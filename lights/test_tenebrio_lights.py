from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from tenebrio_lights import Config, ConfigError, Treatment, load_config, parse_duration, rtc_is_trusted

MADRID = ZoneInfo("Europe/Madrid")
START = datetime(2026, 10, 20, 8, 0, tzinfo=MADRID)


def at(**delta):
    return START + timedelta(**delta)


def test_parse_duration():
    assert parse_duration("20:00:00", "x") == timedelta(hours=20)
    assert parse_duration("00:00:05", "x") == timedelta(seconds=5)
    assert parse_duration("36:30:00", "x") == timedelta(hours=36, minutes=30)


@pytest.mark.parametrize("bad", [72000, "4h", "04:60:00", "04:00", ""])
def test_parse_duration_rejects(bad):
    with pytest.raises(ConfigError):
        parse_duration(bad, "x")


def test_light_first_then_dark_then_repeat():
    t = Treatment(1, light=timedelta(hours=4), dark=timedelta(hours=20))
    assert t.is_light(at(seconds=0), START)
    assert t.is_light(at(hours=3, minutes=59, seconds=59), START)
    assert not t.is_light(at(hours=4), START)
    assert not t.is_light(at(hours=23, minutes=59), START)
    assert t.is_light(at(hours=24), START)
    assert t.is_light(at(days=10, hours=1), START)


def test_dark_before_start():
    t = Treatment(1, light=timedelta(hours=1), dark=timedelta(hours=1))
    assert not t.is_light(at(seconds=-1), START)


def test_always_dark_and_always_light():
    assert not Treatment(1, timedelta(0), timedelta(hours=1)).is_light(at(minutes=5), START)
    assert Treatment(1, timedelta(hours=1), timedelta(0)).is_light(at(days=3), START)


def test_cycle_counts_elapsed_time_across_dst_change():
    # Clocks go back from 03:00 CEST to 02:00 CET on 2026-10-25: a 24-hour cycle
    # starting 08:00 CEST switches on at 07:00 CET afterwards.
    t = Treatment(1, light=timedelta(hours=4), dark=timedelta(hours=20))
    assert t.is_light(datetime(2026, 10, 26, 7, 0, tzinfo=MADRID), START)
    assert not t.is_light(datetime(2026, 10, 26, 11, 0, tzinfo=MADRID), START)
    assert t.is_light(datetime(2026, 10, 26, 6, 0, tzinfo=timezone.utc), START)


def test_unconfigured_treatments_stay_off():
    cfg = Config(START, {1: Treatment(1, timedelta(hours=1), timedelta(0))})
    assert cfg.desired_states(at(minutes=1)) == {1: True, 2: False, 3: False, 4: False}


def write(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return path


def test_load_config(tmp_path):
    cfg = load_config(write(tmp_path, """
start: 2026-10-20 08:00:00
treatments:
  1: {light: "04:00:00", dark: "20:00:00"}
  3: {light: "00:00:05", dark: "00:00:05"}
"""))
    assert set(cfg.treatments) == {1, 3}
    assert cfg.treatments[1].light == timedelta(hours=4)
    assert cfg.start.utcoffset() is not None


@pytest.mark.parametrize("treatments, message", [
    ('5: {light: "01:00:00", dark: "01:00:00"}', "ids are 1-4"),
    ('1: {light: "01:00:00"}', "exactly 'light' and 'dark'"),
    ('1: {light: 20:00:00, dark: "04:00:00"}', "in quotes"),
    ('1: {light: "00:00:00", dark: "00:00:00"}', "both be zero"),
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


def test_next_switch_within_cycle():
    t = Treatment(1, light=timedelta(hours=20), dark=timedelta(hours=4))
    assert t.next_switch(at(hours=1), START) == at(hours=20)
    assert t.next_switch(at(hours=21), START) == at(hours=24)


def test_next_switch_before_start_and_constant():
    t = Treatment(1, light=timedelta(hours=4), dark=timedelta(hours=20))
    assert t.next_switch(at(hours=-2), START) == START
    assert Treatment(1, timedelta(0), timedelta(hours=1)).next_switch(at(hours=1), START) is None
    always_light = Treatment(1, timedelta(hours=1), timedelta(0))
    assert always_light.next_switch(at(hours=-1), START) == START
    assert always_light.next_switch(at(hours=1), START) is None


def test_config_round_trips_through_yaml(tmp_path):
    from tenebrio_lights import config_to_yaml
    cfg = Config(START, {1: Treatment(1, timedelta(hours=20), timedelta(hours=4)),
                         2: Treatment(2, timedelta(hours=36, seconds=5), timedelta(0))})
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
    path = write(tmp_path, 'start: 2026-10-20 08:00:00\ntreatments:\n  1: {light: "01:00:00", dark: "01:00:00"}\n')
    watcher = ConfigWatcher(path)
    assert watcher.changed() == (None, None)

    path.write_text('start: 2026-10-20 08:00:00\ntreatments:\n  2: {light: "02:00:00", dark: "01:00:00"}\n')
    os.utime(path, ns=(watcher.mtime + 10**9, watcher.mtime + 10**9))
    config, error = watcher.changed()
    assert error is None and set(config.treatments) == {2}

    path.write_text('start: 2026-10-20 08:00:00\ntreatments:\n  9: {light: "02:00:00", dark: "01:00:00"}\n')
    os.utime(path, ns=(watcher.mtime + 10**9, watcher.mtime + 10**9))
    config, error = watcher.changed()
    assert config is None and "ids are 1-4" in error
    assert watcher.changed() == (None, None)  # the same problem is reported once
    assert set(watcher.config.treatments) == {2}
