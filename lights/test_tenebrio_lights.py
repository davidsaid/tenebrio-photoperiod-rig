import time
from datetime import datetime, time as clock_time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from tenebrio_lights import Config, ConfigError, Treatment, load_config, parse_duration, rtc_is_trusted

MADRID = ZoneInfo("Europe/Madrid")
START = datetime(2026, 10, 20, 8, 0, tzinfo=MADRID)


def test_parse_duration():
    assert parse_duration("20:00:00", "x") == timedelta(hours=20)
    assert parse_duration("00:00:05", "x") == timedelta(seconds=5)
    assert parse_duration("36:30:00", "x") == timedelta(hours=36, minutes=30)


@pytest.mark.parametrize("bad", [72000, "4h", "04:60:00", "04:00", ""])
def test_parse_duration_rejects(bad):
    with pytest.raises(ConfigError):
        parse_duration(bad, "x")


@pytest.fixture(autouse=True)
def madrid_local_time(monkeypatch):
    # The controller reads the time of day from the Pi's local time zone.
    monkeypatch.setenv("TZ", "Europe/Madrid")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def local(*args):
    return datetime(*args, tzinfo=MADRID)


T1 = Treatment(1, on=clock_time(7, 0), off=clock_time(3, 0))   # 20L:4D, dark 03:00-07:00
T2 = Treatment(2, on=clock_time(9, 0), off=clock_time(13, 0))  # 4L:20D


def test_photoperiod_from_times():
    assert (T1.light, T1.dark) == (timedelta(hours=20), timedelta(hours=4))
    assert (T2.light, T2.dark) == (timedelta(hours=4), timedelta(hours=20))
    assert Treatment(3, clock_time(18, 30), clock_time(18, 0)).light == timedelta(hours=23, minutes=30)


def test_light_between_on_and_off():
    assert not T2.is_light(local(2026, 10, 21, 8, 59, 59), START)
    assert T2.is_light(local(2026, 10, 21, 9, 0), START)
    assert T2.is_light(local(2026, 10, 21, 12, 59, 59), START)
    assert not T2.is_light(local(2026, 10, 21, 13, 0), START)


def test_light_period_across_midnight():
    assert T1.is_light(local(2026, 10, 21, 23, 0), START)
    assert T1.is_light(local(2026, 10, 22, 2, 59, 59), START)
    assert not T1.is_light(local(2026, 10, 22, 3, 0), START)
    assert not T1.is_light(local(2026, 10, 22, 6, 59), START)
    assert T1.is_light(local(2026, 10, 22, 7, 0), START)


def test_dark_before_start_then_follows_schedule_at_once():
    start = local(2026, 10, 20, 15, 0)
    assert not T1.is_light(local(2026, 10, 20, 14, 59), start)
    assert T1.is_light(start, start)


def test_follows_local_clock_across_dst_change():
    # Clocks go back from 03:00 CEST to 02:00 CET on 2026-10-25. The light still goes off at
    # 03:00 and on at 07:00 on the wall clock, so that night's dark period lasts 5 hours.
    assert T1.is_light(datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc), START)   # 02:30 CEST
    assert T1.is_light(datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc), START)   # 02:30 CET
    assert not T1.is_light(datetime(2026, 10, 25, 2, 0, tzinfo=timezone.utc), START)  # 03:00 CET
    assert not T1.is_light(datetime(2026, 10, 25, 5, 59, tzinfo=timezone.utc), START)
    assert T1.is_light(datetime(2026, 10, 25, 6, 0, tzinfo=timezone.utc), START)    # 07:00 CET
    off = T1.next_switch(datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc), START)
    assert off == datetime(2026, 10, 25, 2, 0, tzinfo=timezone.utc)
    assert T1.next_switch(off, START) - off == timedelta(hours=4)


def test_unconfigured_treatments_stay_off():
    cfg = Config(START, {1: T1})
    assert cfg.desired_states(local(2026, 10, 21, 12, 0)) == {1: True, 2: False, 3: False, 4: False}


def write(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return path


def test_load_config(tmp_path):
    cfg = load_config(write(tmp_path, """
start: 2026-10-20 08:00:00
treatments:
  1: {light_on: "09:00", light_off: "13:00"}
  3: {light_on: "21:00", light_off: "05:30"}
"""))
    assert set(cfg.treatments) == {1, 3}
    assert cfg.treatments[1] == Treatment(1, clock_time(9, 0), clock_time(13, 0))
    assert cfg.treatments[3].light == timedelta(hours=8, minutes=30)
    assert cfg.start.utcoffset() is not None


@pytest.mark.parametrize("treatments, message", [
    ('5: {light_on: "07:00", light_off: "03:00"}', "ids are 1-4"),
    ('1: {light_on: "07:00"}', "exactly 'light_on' and 'light_off'"),
    ('1: {light_on: 20:00, light_off: "03:00"}', "in quotes"),
    ('1: {light_on: "24:00", light_off: "03:00"}', "HH:MM"),
    ('1: {light_on: "7:00", light_off: "03:00"}', "HH:MM"),
    ('1: {light_on: "07:00", light_off: "07:00"}', "must differ"),
    ('1: {light: "00:00:30", dark: "00:00:10"}', "not a 24-hour cycle"),
])
def test_load_config_rejects(tmp_path, treatments, message):
    with pytest.raises(ConfigError, match=message):
        load_config(write(tmp_path, f"start: 2026-10-20 08:00:00\ntreatments:\n  {treatments}\n"))


def fake_rtc(tmp_path, hctosys="1", when=datetime(2026, 10, 10, 16, 52, tzinfo=timezone.utc)):
    (tmp_path / "hctosys").write_text(hctosys + "\n")
    (tmp_path / "since_epoch").write_text(f"{int(when.timestamp())}\n")
    return tmp_path, when.timestamp()


def test_rtc_trusted_when_it_set_the_clock_and_agrees(tmp_path):
    rtc, now = fake_rtc(tmp_path)
    assert rtc_is_trusted(now + 30, rtc, uptime=60)


def test_rtc_trusted_after_weeks_of_drift(tmp_path):
    rtc, now = fake_rtc(tmp_path)
    four_weeks = 28 * 86400
    assert not rtc_is_trusted(now + 150, rtc, uptime=60)
    assert rtc_is_trusted(now + 150, rtc, uptime=four_weeks)  # ~5 s/day apart


@pytest.mark.parametrize("case", ["did not set clock", "implausible year", "disagrees", "missing"])
def test_rtc_not_trusted(tmp_path, case):
    if case == "did not set clock":
        rtc, now = fake_rtc(tmp_path, hctosys="0")
    elif case == "implausible year":
        rtc, now = fake_rtc(tmp_path, when=datetime(2089, 1, 10, 1, 3, tzinfo=timezone.utc))
    elif case == "disagrees":
        rtc, now = fake_rtc(tmp_path)
        now += 3600  # systemd replaced the RTC time at boot
    else:
        rtc, now = tmp_path / "absent", 0
    assert not rtc_is_trusted(now, rtc, uptime=60)


def test_next_switch():
    assert T1.next_switch(local(2026, 10, 21, 12, 0), START) == local(2026, 10, 22, 3, 0)
    assert T1.next_switch(local(2026, 10, 22, 3, 0), START) == local(2026, 10, 22, 7, 0)
    assert T2.next_switch(local(2026, 10, 21, 13, 0), START) == local(2026, 10, 22, 9, 0)


def test_next_switch_before_start():
    start = local(2026, 10, 20, 15, 0)
    assert T1.next_switch(local(2026, 10, 19, 12, 0), start) == start         # light at the start
    assert T2.next_switch(local(2026, 10, 19, 12, 0), start) == local(2026, 10, 21, 9, 0)


def test_legacy_durations_become_times_from_the_start(tmp_path):
    cfg = load_config(write(tmp_path, 'start: 2026-10-16 15:16:00\ntreatments:\n'
                                      '  1: {light: "20:00:00", dark: "04:00:00"}\n'))
    assert cfg.treatments[1] == Treatment(1, clock_time(15, 16), clock_time(11, 16))


def test_config_round_trips_through_yaml(tmp_path):
    from tenebrio_lights import config_to_yaml
    cfg = Config(START, {1: T1, 2: Treatment(2, clock_time(23, 59), clock_time(0, 1))})
    path = write(tmp_path, config_to_yaml(cfg, "00:05:00"))
    loaded = load_config(path)
    assert loaded.treatments == cfg.treatments
    assert loaded.start == cfg.start
    assert "readings_interval: \"00:05:00\"" in path.read_text()
    assert loaded.experiment == ""
    named = Config(START, cfg.treatments, "dry-run-1")
    assert load_config(write(tmp_path, config_to_yaml(named, "00:05:00"))).experiment == "dry-run-1"


def test_experiment_name_must_be_a_safe_folder_name(tmp_path):
    with pytest.raises(ConfigError, match="experiment"):
        load_config(write(tmp_path, 'experiment: "../x"\nstart: 2026-10-20 08:00:00\ntreatments: {}\n'))


def test_event_log_starts_a_new_file_with_its_header(tmp_path):
    from tenebrio_lights import EventLog
    log = EventLog(tmp_path / "events.csv")
    (tmp_path / "events.csv").unlink()  # as when the experiment is archived
    log.record("", "", "network: hotspot active")
    lines = (tmp_path / "events.csv").read_text().splitlines()
    assert lines[0] == "timestamp,treatment,state,event" and lines[1].endswith("network: hotspot active")


def test_config_watcher_reloads_and_keeps_previous_on_error(tmp_path):
    import os
    from tenebrio_lights import ConfigWatcher
    path = write(tmp_path, 'start: 2026-10-20 08:00:00\ntreatments:\n  1: {light_on: "07:00", light_off: "03:00"}\n')
    watcher = ConfigWatcher(path)
    assert watcher.changed() == (None, None)

    path.write_text('start: 2026-10-20 08:00:00\ntreatments:\n  2: {light_on: "09:00", light_off: "13:00"}\n')
    os.utime(path, ns=(watcher.mtime + 10**9, watcher.mtime + 10**9))
    config, error = watcher.changed()
    assert error is None and set(config.treatments) == {2}

    path.write_text('start: 2026-10-20 08:00:00\ntreatments:\n  9: {light_on: "09:00", light_off: "13:00"}\n')
    os.utime(path, ns=(watcher.mtime + 10**9, watcher.mtime + 10**9))
    config, error = watcher.changed()
    assert config is None and "ids are 1-4" in error
    assert watcher.changed() == (None, None)  # the same problem is reported once
    assert set(watcher.config.treatments) == {2}
