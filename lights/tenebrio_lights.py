"""Switch each treatment's light from its light/dark cycle in config.yaml.

The state of every light is computed from the clock alone: elapsed time since the
configured start, modulo the treatment's cycle. Nothing is saved between runs, so a
reboot or a crash puts every light back in its scheduled state within a second.
"""

import argparse
import csv
import logging
import os
import re
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

RELAY_HAT_ADDRESS = 0x10  # 52Pi DockerPi 4-channel relay HAT: register n drives relay Kn
RELAY_ON, RELAY_OFF = 0xFF, 0x00
TREATMENT_IDS = (1, 2, 3, 4)
CLOCK_SYNCED_FLAG = Path("/run/systemd/timesync/synchronized")
RTC = Path("/sys/class/rtc/rtc0")  # PCF8523 via dtoverlay=i2c-rtc,pcf8523
# An unset PCF8523 read 2089 on 2026-10-10, so only plausible years are accepted.
RTC_PLAUSIBLE_YEARS = range(2026, 2041)
RTC_AGREEMENT_SECONDS = 60
# Offline, the system clock (Pi oscillator) and the RTC (its own crystal) drift apart a few
# seconds a day, so the allowed disagreement grows with uptime; 100 ppm is ~9 s/day.
RTC_DRIFT_ALLOWANCE = 100e-6
REASSERT_EVERY_SECONDS = 60
EVENT_COLUMNS = ["timestamp", "treatment", "state", "event"]
# Experiment names double as archive folder names, so they are kept to safe characters.
EXPERIMENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")

log = logging.getLogger("tenebrio-lights")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Treatment:
    id: int
    light: timedelta
    dark: timedelta

    def is_light(self, now: datetime, start: datetime) -> bool:
        # Timestamps, not datetime arithmetic: Python compares and subtracts datetimes that
        # share a tzinfo by wall clock, which would shift every cycle at a DST change.
        elapsed = timedelta(seconds=now.timestamp() - start.timestamp())
        if elapsed < timedelta(0) or self.light == timedelta(0):
            return False
        if self.dark == timedelta(0):
            return True
        return elapsed % (self.light + self.dark) < self.light

    def next_switch(self, now: datetime, start: datetime) -> datetime | None:
        """When this light next changes state, or None if it never does."""
        if self.light == timedelta(0) or self.dark == timedelta(0):
            return None if now >= start or self.light == timedelta(0) else start
        elapsed = now.timestamp() - start.timestamp()
        if elapsed < 0:
            return start
        cycle = (self.light + self.dark).total_seconds()
        position = elapsed % cycle
        light = self.light.total_seconds()
        remaining = light - position if position < light else cycle - position
        return datetime.fromtimestamp(now.timestamp() + remaining).astimezone()


@dataclass(frozen=True)
class Config:
    start: datetime
    treatments: dict[int, Treatment]
    experiment: str = ""

    def desired_states(self, now: datetime) -> dict[int, bool]:
        return {tid: tid in self.treatments and self.treatments[tid].is_light(now, self.start)
                for tid in TREATMENT_IDS}


def parse_duration(value, where: str) -> timedelta:
    # Unquoted values like 20:00:00 are read by YAML 1.1 as base-60 integers, and
    # 04:00:00 as a string, so only quoted strings are accepted.
    if not isinstance(value, str):
        raise ConfigError(f"{where}: write durations in quotes as \"HH:MM:SS\", got {value!r}")
    match = re.fullmatch(r"(\d+):([0-5]\d):([0-5]\d)", value.strip())
    if not match:
        raise ConfigError(f"{where}: {value!r} is not HH:MM:SS")
    hours, minutes, seconds = map(int, match.groups())
    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


def format_duration(value: timedelta) -> str:
    seconds = int(value.total_seconds())
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def config_to_yaml(config: Config, readings_interval: str) -> str:
    """Config file text as the web page writes it; load_config reads it back unchanged."""
    lines = [
        "# Light schedule for the Tenebrio photoperiod rig. Written by the monitoring page;",
        "# see lights/README.md for the format. Durations are \"HH:MM:SS\" in quotes, and every",
        "# cycle starts with light at `start`. Treatment ids match relays K1-K4.",
        *([f"experiment: {config.experiment}"] if config.experiment else []),
        f"start: {config.start.strftime('%Y-%m-%d %H:%M:%S')}",
        f'readings_interval: "{readings_interval}"',
        "",
        "treatments:" + ("" if config.treatments else " {}"),
    ]
    for tid, t in sorted(config.treatments.items()):
        lines += [f"  {tid}:", f'    light: "{format_duration(t.light)}"', f'    dark: "{format_duration(t.dark)}"']
    return "\n".join(lines) + "\n"


def load_config(path: Path) -> Config:
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict) or "start" not in raw or "treatments" not in raw:
        raise ConfigError(f"{path}: needs 'start' and 'treatments'")

    start = raw["start"]
    if isinstance(start, str):
        start = datetime.fromisoformat(start)
    if not isinstance(start, datetime):
        raise ConfigError(f"start: expected a date and time like 2026-10-20 08:00:00, got {start!r}")
    start = start.astimezone()  # a time without an offset means the Pi's local time

    treatments = {}
    for tid, entry in (raw["treatments"] or {}).items():
        if tid not in TREATMENT_IDS:
            raise ConfigError(f"treatment {tid!r}: ids are 1-4, one per relay")
        if not isinstance(entry, dict) or set(entry) != {"light", "dark"}:
            raise ConfigError(f"treatment {tid}: needs exactly 'light' and 'dark'")
        treatment = Treatment(tid, parse_duration(entry["light"], f"treatment {tid} light"),
                              parse_duration(entry["dark"], f"treatment {tid} dark"))
        if treatment.light + treatment.dark == timedelta(0):
            raise ConfigError(f"treatment {tid}: light and dark cannot both be zero")
        treatments[tid] = treatment
    experiment = str(raw.get("experiment") or "")
    if experiment and not EXPERIMENT_NAME.fullmatch(experiment):
        raise ConfigError(f"experiment: {experiment!r} must be letters, digits, '.', '_' or '-', up to 64")
    return Config(start, treatments, experiment)


class RelayHat:
    def __init__(self, bus_number: int = 1):
        from smbus2 import SMBus
        self.bus = SMBus(bus_number)

    def set(self, relay: int, on: bool) -> None:
        self.bus.write_byte_data(RELAY_HAT_ADDRESS, relay, RELAY_ON if on else RELAY_OFF)


class EventLog:
    """Append-only CSV of every switch and clock event, for the thesis record."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            self._write(EVENT_COLUMNS)

    def record(self, treatment, state, event: str) -> None:
        self._write([datetime.now().astimezone().isoformat(timespec="seconds"), treatment, state, event])
        log.info("treatment=%s state=%s %s", treatment, state, event)

    def _write(self, row) -> None:
        append_csv(self.path, row, EVENT_COLUMNS)


def append_csv(path: Path, row: list, header: list) -> None:
    """Append one row, writing the header first if the file is new (e.g. just archived).

    fsync per row: on 2026-10-09 a power cut seconds after a deploy turned the rows that
    deploy had just written, still in the page cache, into NUL bytes. A file created by a
    root service (the network service) is handed to the folder's owner, so that the other
    services, which run as pi, can keep appending to it.
    """
    new = not path.exists()
    with path.open("a", newline="") as f:
        writer = csv.writer(f)
        if new and row != header:
            writer.writerow(header)
        writer.writerow(row)
        f.flush()
        os.fsync(f.fileno())
    if new and os.geteuid() == 0:
        owner = path.parent.stat()
        os.chown(path, owner.st_uid, owner.st_gid)


def uptime_seconds() -> float:
    return time.clock_gettime(getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC))


def rtc_is_trusted(now: float, rtc: Path = RTC, uptime: float | None = None) -> bool:
    """The RTC set the system clock at boot, holds a plausible year, and still agrees with it.

    The kernel refuses to read a PCF8523 whose oscillator stopped (dead coin cell), so
    `hctosys` stays 0. Agreement rules out systemd having replaced the RTC time at boot,
    which it does when the RTC is more than 15 years ahead; it allows for the drift
    between the two clocks since boot, so weeks offline don't end in lights held off.
    """
    uptime = uptime_seconds() if uptime is None else uptime
    try:
        set_clock_at_boot = (rtc / "hctosys").read_text().strip() == "1"
        rtc_seconds = int((rtc / "since_epoch").read_text())
    except (OSError, ValueError):
        return False
    year = datetime.fromtimestamp(rtc_seconds, timezone.utc).year
    return (set_clock_at_boot and year in RTC_PLAUSIBLE_YEARS
            and abs(now - rtc_seconds) <= RTC_AGREEMENT_SECONDS + uptime * RTC_DRIFT_ALLOWANCE)


def clock_source() -> str | None:
    """Where trustworthy time comes from: "NTP", "RTC", or None when there is none."""
    if CLOCK_SYNCED_FLAG.exists():
        return "NTP"
    if rtc_is_trusted(time.time()):
        return "RTC"
    return None


class ConfigWatcher:
    """Reloads the config when its file changes; an invalid file keeps the previous config."""

    def __init__(self, path: Path):
        self.path = path
        self.mtime = path.stat().st_mtime_ns
        self.config = load_config(path)
        self.error = None

    def changed(self) -> tuple[Config | None, str | None]:
        """(new config, None) after a valid change; (None, error) once per new problem."""
        try:
            mtime = self.path.stat().st_mtime_ns
            if mtime == self.mtime:
                return None, None
            config = load_config(self.path)
        except (OSError, ValueError, yaml.YAMLError) as error:
            message, is_new = str(error), str(error) != self.error
            self.error = message
            return None, message if is_new else None
        self.mtime, self.config, self.error = mtime, config, None
        return config, None


def run(watcher: ConfigWatcher, relays: RelayHat, events: EventLog, poll_seconds: float = 0.25) -> None:
    config = watcher.config
    applied: dict[int, bool | None] = dict.fromkeys(TREATMENT_IDS)
    last_assert = float("-inf")
    source = "unchecked"

    while True:
        now = datetime.now().astimezone()
        new_config, error = watcher.changed()
        if new_config:
            config = new_config
            events.record("", "", "config reloaded: " + describe(config, now).replace("\n", "; "))
        elif error:
            events.record("", "", f"config NOT reloaded, keeping the previous one: {error}")
        if clock_source() != source:
            source = clock_source()
            events.record("", "", f"clock trusted ({source}): following the schedule" if source
                          else "clock NOT trusted: all lights held off")

        desired = config.desired_states(now) if source else dict.fromkeys(TREATMENT_IDS, False)
        reassert = time.monotonic() - last_assert >= REASSERT_EVERY_SECONDS
        for tid, on in desired.items():
            if on == applied[tid] and not reassert:
                continue
            try:
                relays.set(tid, on)
            except OSError as error:
                log.error("relay %d: I2C write failed: %s", tid, error)
                applied[tid] = None
                continue
            if on != applied[tid]:
                events.record(tid, "light" if on else "dark", "set" if applied[tid] is None else "switched")
            applied[tid] = on
        if reassert:
            last_assert = time.monotonic()
        time.sleep(poll_seconds)


def describe(config: Config, now: datetime) -> str:
    lines = ([f"experiment: {config.experiment}"] if config.experiment else []) + [f"start: {config.start.isoformat()}"]
    for tid in TREATMENT_IDS:
        t = config.treatments.get(tid)
        if t is None:
            lines.append(f"treatment {tid}: not configured, light always off")
            continue
        state = "light" if t.is_light(now, config.start) else "dark"
        lines.append(f"treatment {tid}: light {t.light}, dark {t.dark}, cycle {t.light + t.dark}; now {state}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.yaml"))
    parser.add_argument("--events", type=Path, default=Path("/var/lib/tenebrio-lights/events.csv"))
    parser.add_argument("--check", action="store_true", help="validate the config, print the schedule and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    watcher = ConfigWatcher(args.config)
    print(describe(watcher.config, datetime.now().astimezone()), flush=True)
    if args.check:
        return
    events = EventLog(args.events)
    events.record("", "", f"controller started with {args.config}")
    # A clean stop (deploy, shutdown) leaves this row; a power cut or crash leaves none.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        run(watcher, RelayHat(), events)
    finally:
        events.record("", "", "controller stopped")


if __name__ == "__main__":
    main()
