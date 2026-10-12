import os
import zipfile
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest

import tenebrio_web
from tenebrio_lights import ConfigError, load_config
from tenebrio_web import config_from_form, event_kind, light_periods, read_rows, save_config, series, set_hotspot_only


def test_config_from_form():
    config = config_from_form({"start": "2026-10-20T08:00:00", "experiment": "dry-run-1",
                               "treatments": [{"id": 1, "light": "20:00:00", "dark": "04:00:00"}]})
    assert config.treatments[1].dark == timedelta(hours=4)
    assert config.start.utcoffset() is not None


@pytest.mark.parametrize("form, message", [
    ({"start": "nope", "treatments": []}, "Fecha de inicio"),
    ({"start": "2026-10-20T08:00:00", "treatments": [{"id": 5, "light": "01:00:00", "dark": "01:00:00"}]}, "del 1 al 4"),
    ({"start": "2026-10-20T08:00:00", "treatments": [{"id": 1, "light": "20:00", "dark": "04:00:00"}]}, "HH:MM:SS"),
    ({"start": "2026-10-20T08:00:00", "treatments": [{"id": 1, "light": "00:00:00", "dark": "00:00:00"}]}, "ambas cero"),
])
def test_config_from_form_rejects(form, message):
    with pytest.raises(ConfigError, match=message):
        config_from_form(form)


def test_read_rows_drops_damaged_lines(tmp_path):
    path = tmp_path / "events.csv"
    path.write_bytes(b"timestamp,treatment,state,event\n2026-10-10T19:00:00+02:00,1,light,switched\n"
                     + b"\0" * 40 + b"\n2026-10-10T19:05:00+02:00,,,controller stopped\n")
    assert [r[3] for r in read_rows(path)] == ["switched", "controller stopped"]


def test_event_kind():
    assert event_kind(["t", "1", "light", "switched"]) == "switch"
    assert event_kind(["t", "", "", "outage: last reading ..."]) == "outage"
    assert event_kind(["t", "", "", "config changed from the monitoring page: ..."]) == "config"


def test_save_config_writes_valid_file_and_logs_change(tmp_path):
    config_path, events_path = tmp_path / "config.yaml", tmp_path / "events.csv"
    config_path.write_text('start: 2026-10-20 08:00:00\nreadings_interval: "00:01:00"\n'
                           'treatments:\n  1: {light: "20:00:00", dark: "04:00:00"}\n')
    new = config_from_form({"start": "2026-10-21T08:00:00", "experiment": "dry-run-1",
                            "treatments": [{"id": 2, "light": "04:00:00", "dark": "20:00:00"}]})
    save_config(new, config_path, events_path)
    assert set(load_config(config_path).treatments) == {2}
    assert 'readings_interval: "00:01:00"' in config_path.read_text()
    assert not list(tmp_path.glob("*.tmp"))
    assert "treatment 1 light 20:00:00 dark 04:00:00 -> experiment dry-run-1, start 2026-10-21 08:00:00, treatment 2" in events_path.read_text()


def stamp(hour, minute=0):
    return datetime(2026, 10, 20, hour, minute, tzinfo=timezone(timedelta(hours=2)))


def test_light_periods_follow_events_and_outages_close_them():
    rows = [
        [stamp(8).isoformat(), "1", "light", "set"],
        [stamp(8).isoformat(), "2", "dark", "set"],
        [stamp(9).isoformat(), "2", "light", "switched"],
        [stamp(10).isoformat(), "1", "dark", "switched"],
        [stamp(11, 30).isoformat(), "", "", f"outage: last reading {stamp(11).isoformat()}, system booted ..."],
        [stamp(11, 30).isoformat(), "2", "light", "set"],
    ]
    periods = light_periods(rows)
    assert periods["1"] == [[stamp(8).timestamp(), stamp(10).timestamp()]]
    assert periods["2"] == [[stamp(9).timestamp(), stamp(11).timestamp()], [stamp(11, 30).timestamp(), None]]


def test_series_reads_columns_by_header_and_blanks_as_null(tmp_path):
    readings = tmp_path / "readings.csv"
    readings.write_text("timestamp,clock,temp_c_1,rh_1,temp_c_2,rh_2,temp_c_control,rh_control,"
                        "temp_c_room,rh_room,cpu_temp_c,throttled\n"
                        f"{stamp(8).isoformat()},RTC,25.10,60.00,,,24.90,58.20,23.00,55.00,45.3,0x0\n")
    data = series(tmp_path / "events.csv", readings)
    assert data["t"] == [stamp(8).timestamp()]
    assert data["series"]["temp_c_1"] == [25.1]
    assert data["series"]["temp_c_2"] == [None]
    assert data["light"] == {}


def test_hotspot_only_switch_logs_only_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(tenebrio_web, "HOTSPOT_ONLY", tmp_path / "hotspot-only")
    events = tmp_path / "events.csv"
    set_hotspot_only(True, events)
    set_hotspot_only(True, events)
    assert (tmp_path / "hotspot-only").exists()
    set_hotspot_only(False, events)
    assert not (tmp_path / "hotspot-only").exists()
    assert [r[3] for r in read_rows(events)] == ["hotspot only switched on from the Configuración page",
                                                 "hotspot only switched off from the Configuración page"]


def test_password_defaults_to_tenebrio_and_can_be_changed(tmp_path, monkeypatch):
    monkeypatch.setattr(tenebrio_web, "PASSWORD_FILE", tmp_path / "settings-password")
    assert tenebrio_web.check_password("tenebrio")
    assert not tenebrio_web.check_password("Tenebrio")
    tenebrio_web.set_password("larvas2026", tmp_path / "events.csv")
    assert tenebrio_web.check_password("larvas2026")
    assert not tenebrio_web.check_password("tenebrio")
    assert "larvas2026" not in (tmp_path / "settings-password").read_text()
    with pytest.raises(ValueError):
        tenebrio_web.set_password("abc", tmp_path / "events.csv")


def test_sessions_expire_and_end():
    sessions = tenebrio_web.Sessions()
    token = sessions.create()
    assert sessions.valid(token)
    assert not sessions.valid("forged") and not sessions.valid(None)
    sessions.end(token)
    assert not sessions.valid(token)


CONFIG = ('experiment: bench-tests\nstart: 2026-10-11 15:30:00\nreadings_interval: "00:05:00"\n'
          'treatments:\n  1: {light: "20:00:00", dark: "04:00:00"}\n')


def test_config_form_needs_a_safe_experiment_name():
    form = {"start": "2026-10-20T08:00:00", "treatments": [], "experiment": "dry-run-1"}
    assert config_from_form(form).experiment == "dry-run-1"
    for bad in ("", "../etc", "two words", "x" * 65):
        with pytest.raises(ConfigError, match="experiment_name"):
            config_from_form({**form, "experiment": bad})


def test_archive_moves_files_read_only_and_starts_next_experiment(tmp_path):
    config_path, events, readings = tmp_path / "config.yaml", tmp_path / "events.csv", tmp_path / "readings.csv"
    config_path.write_text(CONFIG)
    events.write_text("timestamp,treatment,state,event\n2026-10-11T15:30:00+02:00,1,light,switched\n")
    readings.write_text("timestamp,clock\n2026-10-11T15:35:00+02:00,NTP\n")
    (tmp_path / "readings-until-20261011-1513.csv").write_text("old\n")
    start = datetime(2026, 10, 12, 18, 0).astimezone()

    folder = tenebrio_web.archive_experiment("bench-tests", "dry-run-1", start, config_path, events, readings)

    assert folder == tmp_path / "archive" / "bench-tests"
    assert sorted(p.name for p in folder.iterdir()) == ["config.yaml", "events.csv", "readings-until-20261011-1513.csv", "readings.csv"]
    assert all(not os.access(p, os.W_OK) for p in [folder, *folder.iterdir()])
    assert read_rows(folder / "events.csv")[-1][3] == "experiment archived as archive/bench-tests; next experiment dry-run-1"
    archived = load_config(folder / "config.yaml")
    assert archived.experiment == "bench-tests" and archived.start.hour == 15
    new = load_config(config_path)
    assert new.experiment == "dry-run-1" and new.start == start and set(new.treatments) == {1}
    assert not readings.exists()
    assert events.read_text().startswith("timestamp,treatment,state,event\n")
    assert read_rows(events)[-1][3].startswith("experiment dry-run-1 started")

    info = tenebrio_web.archived_experiments(config_path)
    assert [e["name"] for e in info] == ["bench-tests"]
    assert info[0]["treatments"] == [{"id": 1, "light": "20:00:00", "dark": "04:00:00"}]
    assert info[0]["files"]["readings"]["rows"] == 1

    with pytest.raises(ConfigError, match="archive_exists"):
        tenebrio_web.archive_experiment("bench-tests", "dry-run-2", start, config_path, events, readings)
    with pytest.raises(ConfigError, match="same_name"):
        tenebrio_web.archive_experiment("dry-run-1", "dry-run-1", start, config_path, events, readings)
    for path in [folder, *folder.iterdir()]:  # let pytest clean up
        path.chmod(0o755)


def test_archive_zip_holds_archive_and_running_experiment(tmp_path):
    config_path, events, readings = tmp_path / "config.yaml", tmp_path / "events.csv", tmp_path / "readings.csv"
    config_path.write_text(CONFIG.replace("bench-tests", "dry-run-1"))
    events.write_bytes(b"timestamp,treatment,state,event\n\0\0\n")
    (tmp_path / "archive" / "bench-tests").mkdir(parents=True)
    (tmp_path / "archive" / "bench-tests" / "readings.csv").write_text("timestamp\n")
    with zipfile.ZipFile(BytesIO(tenebrio_web.archive_zip(config_path, events, readings))) as z:
        assert sorted(z.namelist()) == ["archive/bench-tests/readings.csv", "running/dry-run-1/config.yaml",
                                        "running/dry-run-1/events.csv"]
        assert b"\0" not in z.read("running/dry-run-1/events.csv")


def test_archive_folder_rejects_paths_outside_the_archive(tmp_path):
    (tmp_path / "archive" / "bench-tests").mkdir(parents=True)
    config_path = tmp_path / "config.yaml"
    assert tenebrio_web.archive_folder(config_path, "bench-tests") == tmp_path / "archive" / "bench-tests"
    assert tenebrio_web.archive_folder(config_path, "..") is None
    assert tenebrio_web.archive_folder(config_path, "../archive") is None
    assert tenebrio_web.archive_folder(config_path, "missing") is None


def test_chart_events_skip_set_rows_that_change_nothing():
    rows = [
        [stamp(8).isoformat(), "1", "light", "set"],
        [stamp(9).isoformat(), "", "", "controller stopped"],
        [stamp(9, 1).isoformat(), "1", "light", "set"],
        [stamp(10).isoformat(), "1", "dark", "switched"],
        [stamp(11).isoformat(), "", "", "outage: last reading ..."],
        [stamp(11).isoformat(), "1", "dark", "set"],
    ]
    events = tenebrio_web.chart_events(rows)
    assert [(e["kind"], e["state"]) for e in events] == [("switch", "light"), ("controller", ""), ("switch", "dark"), ("outage", "")]
    assert events[0]["t"] == stamp(8).timestamp()
