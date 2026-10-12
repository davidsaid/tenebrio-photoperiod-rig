from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import tenebrio_logger
from tenebrio_logger import (COLUMNS, Sensors, archive_if_old_format, crc8, decode_sht45, describe_gap,
                             last_timestamp, load_interval, next_slot)

MADRID = ZoneInfo("Europe/Madrid")


def test_next_slot_aligns_to_interval():
    assert next_slot(1000.0, 300) == 1200.0
    assert next_slot(1200.0, 300) == 1500.0


def test_last_timestamp_skips_damaged_tail(tmp_path):
    path = tmp_path / "readings.csv"
    path.write_bytes(b"timestamp,clock,cpu_temp_c,throttled\n"
                     b"2026-10-10T18:50:00+02:00,NTP,42.1,0x0\n"
                     b"2026-10-10T18:55:00+02:00,NTP,42.3,0x0\n" + b"\0" * 300)
    assert last_timestamp(path) == datetime(2026, 10, 10, 18, 55, tzinfo=MADRID)


def test_last_timestamp_none_without_rows(tmp_path):
    assert last_timestamp(tmp_path / "missing.csv") is None
    (tmp_path / "header.csv").write_text("timestamp,clock,cpu_temp_c,throttled\n")
    assert last_timestamp(tmp_path / "header.csv") is None


def test_describe_gap_with_reboot():
    last = datetime(2026, 10, 10, 18, 50, tzinfo=MADRID)
    message = describe_gap(last, last + timedelta(minutes=40), last + timedelta(minutes=41))
    assert message.startswith("outage: last reading 2026-10-10T18:50:00+02:00, system booted 2026-10-10T19:30:00")
    assert "(41 min without readings)" in message


def test_describe_gap_without_reboot():
    last = datetime(2026, 10, 10, 18, 50, tzinfo=MADRID)
    message = describe_gap(last, last - timedelta(hours=1), last + timedelta(minutes=20))
    assert message.startswith("readings gap without reboot")


def test_load_interval(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text('start: 2026-10-20 08:00:00\nreadings_interval: "00:05:00"\ntreatments: {}\n')
    assert load_interval(path) == timedelta(minutes=5)
    path.write_text('start: 2026-10-20 08:00:00\ntreatments: {}\n')
    assert load_interval(path) == timedelta(minutes=5)
    path.write_text('readings_interval: "00:00:00"\n')
    with pytest.raises(ValueError):
        load_interval(path)


def test_crc8_matches_the_sensirion_datasheet_example():
    assert crc8(bytes([0xBE, 0xEF])) == 0x92


def measurement(t_ticks: int, rh_ticks: int) -> bytes:
    t, rh = t_ticks.to_bytes(2, "big"), rh_ticks.to_bytes(2, "big")
    return t + bytes([crc8(t)]) + rh + bytes([crc8(rh)])


def test_decode_sht45():
    temperature, humidity = decode_sht45(measurement(0x6666, 0x8000))
    assert temperature == pytest.approx(25.0, abs=0.01)
    assert humidity == pytest.approx(56.5, abs=0.01)
    assert decode_sht45(measurement(0, 0))[1] == 0.0  # formula gives -6 %RH; clamped


def test_decode_sht45_rejects_corruption():
    raw = bytearray(measurement(0x6666, 0x8000))
    raw[1] ^= 0x01
    with pytest.raises(ValueError, match="checksum"):
        decode_sht45(bytes(raw))


def test_sensors_log_only_when_a_sensor_goes_missing_or_comes_back(tmp_path, monkeypatch):
    monkeypatch.setattr(tenebrio_logger.time, "sleep", lambda s: None)
    broken = {7}  # treatment 2

    def read(bus):
        if bus in broken:
            raise OSError(121, "Remote I/O error")
        return 25.0, 56.5

    events = tmp_path / "events.csv"
    sensors = Sensors(events, read=read)
    assert sensors.row() == ["25.00", "56.50", "", "", "25.00", "56.50", "25.00", "56.50"]
    sensors.row()
    broken.clear()
    sensors.row()
    header, *lines = events.read_text().splitlines()
    assert header == "timestamp,treatment,state,event"
    assert len(lines) == 2
    assert "sensor 2 (I2C bus 7) not reading: [Errno 121] Remote I/O error" in lines[0]
    assert "sensor 2 (I2C bus 7) reading again" in lines[1]


def test_old_readings_file_is_archived_once(tmp_path):
    readings = tmp_path / "readings.csv"
    readings.write_text("timestamp,clock,cpu_temp_c,throttled\n2026-10-10T18:50:00+02:00,NTP,42.1,0x0\n")
    archived = archive_if_old_format(readings)
    assert archived.name.startswith("readings-until-") and not readings.exists()
    readings.write_text(",".join(COLUMNS) + "\n")
    assert archive_if_old_format(readings) is None
