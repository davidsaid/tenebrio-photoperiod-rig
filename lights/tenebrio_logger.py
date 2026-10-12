"""Record periodic readings to readings.csv and report gaps (power cuts) in the event log.

Each row has the temperature and humidity of every enclosure and the room, read from
SHT45 sensors, plus the Pi's CPU temperature and power flags. Runs as its own service so
that a hung sensor can never delay light switching. Rows are aligned to multiples of the
interval (e.g. :00, :05, :10) and written only while the clock is trusted, so every
timestamp is real. The rows double as a heartbeat: at startup, a gap since the last row is
written to the event log as an outage.
"""

import argparse
import csv
import fcntl
import logging
import os
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from tenebrio_lights import EVENT_COLUMNS, append_csv, clock_source, parse_duration

CPU_TEMP = Path("/sys/class/thermal/thermal_zone0/temp")
# The SHT45 address is fixed in the chip (0x44), so each enclosure sensor has an I2C bus of
# its own: i2c6 and i2c5 in hardware, and a software bus 7 (i2c-gpio). Each sits in a compact
# block of header pins, powered from a GPIO held high from boot. The room sensor sits near
# the Pi, chained off the RTC board on bus 1. See README.md, "Sensors", for the pins.
SENSORS = {"1": 6, "2": 7, "control": 5, "room": 1}  # name -> /dev/i2c-N
SHT45_ADDRESS = 0x44
SHT45_MEASURE_HIGH_PRECISION = 0xFD
I2C_SLAVE = 0x0703
SENSOR_ATTEMPTS = 3
COLUMNS = (["timestamp", "clock"]
           + [f"{kind}_{name}" for name in SENSORS for kind in ("temp_c", "rh")]
           + ["cpu_temp_c", "throttled"])
GAP_TOLERANCE = 1.5  # intervals; a longer gap between rows is reported as an outage

log = logging.getLogger("tenebrio-logger")


def load_interval(config_path: Path) -> timedelta:
    raw = yaml.safe_load(config_path.read_text())
    interval = parse_duration(raw.get("readings_interval", "00:05:00"), "readings_interval")
    if interval <= timedelta(0):
        raise ValueError("readings_interval must be positive")
    return interval


def log_event(events: Path, message: str) -> None:
    append_csv(events, [datetime.now().astimezone().isoformat(timespec="seconds"), "", "", message], EVENT_COLUMNS)


def last_timestamp(path: Path) -> datetime | None:
    """Timestamp of the newest well-formed row; damaged tails (NUL bytes) are skipped."""
    if not path.exists():
        return None
    with path.open("rb") as f:
        f.seek(max(0, path.stat().st_size - 8192))
        lines = f.read().replace(b"\0", b"").decode(errors="ignore").splitlines()
    for line in reversed(lines):
        try:
            return datetime.fromisoformat(line.split(",", 1)[0])
        except ValueError:
            continue
    return None


def crc8(data: bytes) -> int:
    """Sensirion CRC-8: polynomial 0x31, initial value 0xFF."""
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def decode_sht45(raw: bytes) -> tuple[float, float]:
    """(temperature °C, relative humidity %) from a 6-byte measurement; ValueError if corrupt."""
    if len(raw) != 6 or crc8(raw[0:2]) != raw[2] or crc8(raw[3:5]) != raw[5]:
        raise ValueError(f"bad checksum in {raw.hex()}")
    t_ticks, rh_ticks = int.from_bytes(raw[0:2], "big"), int.from_bytes(raw[3:5], "big")
    temperature = -45 + 175 * t_ticks / 65535
    humidity = min(100.0, max(0.0, -6 + 125 * rh_ticks / 65535))
    return temperature, humidity


def read_sht45(bus: int) -> tuple[float, float]:
    fd = os.open(f"/dev/i2c-{bus}", os.O_RDWR)
    try:
        fcntl.ioctl(fd, I2C_SLAVE, SHT45_ADDRESS)
        os.write(fd, bytes([SHT45_MEASURE_HIGH_PRECISION]))
        time.sleep(0.01)  # datasheet: 8.3 ms maximum at high precision
        return decode_sht45(os.read(fd, 6))
    finally:
        os.close(fd)


class Sensors:
    """Reads every SHT45, retrying a failed read; logs each sensor going missing or coming back."""

    def __init__(self, events: Path, read=read_sht45):
        self.events = events
        self.read = read
        self.ok: dict[str, bool | None] = dict.fromkeys(SENSORS)

    def row(self) -> list[str]:
        cells = []
        for name, bus in SENSORS.items():
            reading, error = None, None
            for _ in range(SENSOR_ATTEMPTS):
                try:
                    reading = self.read(bus)
                    break
                except (OSError, ValueError) as e:
                    error = e
                    time.sleep(0.05)
            self.note(name, bus, reading is not None, error)
            cells += [f"{reading[0]:.2f}", f"{reading[1]:.2f}"] if reading else ["", ""]
        return cells

    def note(self, name: str, bus: int, ok: bool, error: Exception | None) -> None:
        if ok == self.ok[name] or (ok and self.ok[name] is None):
            self.ok[name] = ok
            return
        message = (f"sensor {name} (I2C bus {bus}) reading again" if ok
                   else f"sensor {name} (I2C bus {bus}) not reading: {error}")
        log_event(self.events, message)
        log.info(message)
        self.ok[name] = ok


def archive_if_old_format(readings: Path) -> Path | None:
    """Move aside a readings file whose header isn't COLUMNS, so every file has one layout."""
    if not readings.exists():
        return None
    with readings.open(newline="", errors="ignore") as f:
        header = next(csv.reader(f), None)
    if header == COLUMNS:
        return None
    archived = readings.with_name(f"{readings.stem}-until-{datetime.now():%Y%m%d-%H%M}{readings.suffix}")
    readings.rename(archived)
    return archived


def cpu_temp_c() -> str:
    try:
        return f"{int(CPU_TEMP.read_text()) / 1000:.1f}"
    except (OSError, ValueError):
        return ""


def throttled() -> str:
    # Bit 0: under-voltage now; bit 16: under-voltage has occurred since boot.
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=5)
        return out.stdout.strip().removeprefix("throttled=")
    except (OSError, subprocess.SubprocessError):
        return ""


def boot_time(now: datetime) -> datetime:
    uptime = float(Path("/proc/uptime").read_text().split()[0])
    return now - timedelta(seconds=uptime)


def describe_gap(last: datetime, booted: datetime, resumed: datetime) -> str:
    gap = round((resumed - last).total_seconds() / 60)
    if booted > last:
        return (f"outage: last reading {last.isoformat(timespec='seconds')}, system booted "
                f"{booted.isoformat(timespec='seconds')}, readings resumed "
                f"{resumed.isoformat(timespec='seconds')} ({gap} min without readings)")
    return (f"readings gap without reboot: last {last.isoformat(timespec='seconds')}, "
            f"resumed {resumed.isoformat(timespec='seconds')} ({gap} min)")


def next_slot(now: float, interval: float) -> float:
    return (now // interval + 1) * interval


def run(interval: timedelta, readings: Path, events: Path) -> None:
    seconds = interval.total_seconds()
    while clock_source() is None:
        time.sleep(1)

    now = datetime.now().astimezone()
    last = last_timestamp(readings)
    if last is not None and (now - last).total_seconds() > GAP_TOLERANCE * seconds:
        message = describe_gap(last, boot_time(now), now)
        log_event(events, message)
        log.info(message)
    archived = archive_if_old_format(readings)
    if archived:
        log_event(events, f"readings columns changed: previous readings kept in {archived.name}")

    sensors = Sensors(events)
    while True:
        source = clock_source()
        if source:
            stamp = datetime.now().astimezone().isoformat(timespec="seconds")
            cells = sensors.row()
            append_csv(readings, [stamp, source, *cells, cpu_temp_c(), throttled()], COLUMNS)
        time.sleep(max(0.0, next_slot(time.time(), seconds) - time.time()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.yaml"))
    parser.add_argument("--readings", type=Path, default=Path("/var/lib/tenebrio-lights/readings.csv"))
    parser.add_argument("--events", type=Path, default=Path("/var/lib/tenebrio-lights/events.csv"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run(load_interval(args.config), args.readings, args.events)


if __name__ == "__main__":
    main()
