"""Check that the relays hold the state the schedule calls for; restart the Pi if not.

Every CHECK_EVERY it reads each relay back from the HAT and compares it with the schedule,
exactly as the controller computes it (all off while the clock is not trusted). A mismatch
that lasts MISMATCH_FOR is logged as an error and the Pi restarts (the experimenters' choice,
2026-10-18): a frozen controller or a HAT that reset would otherwise hold a wrong light for
hours in an unattended lab. MISMATCH_FOR is longer than the controller's 60 s re-assert, so
a fault the controller corrects by itself, and the instant of a scheduled switch, never
cause a restart. At most one restart per RESTART_SPACING: if a restart did not help, the
error keeps being logged instead.

It reads relays only; it cannot see whether the LEDs light (the 12 V side is invisible to
the Pi).
"""

import argparse
import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path

from tenebrio_lights import (RELAY_HAT_ADDRESS, RELAY_OFF, RELAY_ON, TREATMENT_IDS, ConfigWatcher, EventLog,
                             clock_source)

CHECK_EVERY = 15
MISMATCH_FOR = 90
RESTART_SPACING = 6 * 3600
LAST_RESTART = Path("/var/lib/tenebrio-lights/light-check-restart")  # survives the restart it records

log = logging.getLogger("tenebrio-validator")


def read_relays(bus) -> dict[int, bool | None]:
    """Each relay's state as the HAT reports it; None if it reads neither on nor off."""
    values = {tid: bus.read_byte_data(RELAY_HAT_ADDRESS, tid) for tid in TREATMENT_IDS}
    return {tid: {RELAY_ON: True, RELAY_OFF: False}.get(v) for tid, v in values.items()}


def describe(states: dict[int, bool | None]) -> str:
    names = {True: "light", False: "dark", None: "unknown"}
    return ", ".join(f"{tid} {names[on]}" for tid, on in states.items())


def restarted_recently() -> bool:
    try:
        return time.time() - float(LAST_RESTART.read_text()) < RESTART_SPACING
    except (OSError, ValueError):
        return False


def restart_pi() -> None:
    LAST_RESTART.write_text(f"{time.time():.0f}\n")
    subprocess.run(["systemctl", "reboot"])


class Validator:
    def __init__(self, watcher: ConfigWatcher, bus, events: EventLog):
        self.watcher, self.bus, self.events = watcher, bus, events
        self.mismatch_since: float | None = None
        self.reported = False

    def expected(self) -> dict[int, bool]:
        self.watcher.changed()  # an invalid file keeps the previous config, as in the controller
        if clock_source() is None:
            return dict.fromkeys(TREATMENT_IDS, False)
        return self.watcher.config.desired_states(datetime.now().astimezone())

    def step(self) -> None:
        expected = self.expected()
        try:
            actual = read_relays(self.bus)
            problem = None if actual == expected else (f"schedule says {describe(expected)}; "
                                                       f"relays read {describe(actual)}")
        except OSError as error:
            problem = f"relay HAT not answering ({error})"
        now = time.monotonic()
        if problem is None:
            if self.reported:
                self.events.record("", "", "controller check: relays match the schedule again")
            self.mismatch_since, self.reported = None, False
            return
        self.mismatch_since = self.mismatch_since or now
        if now - self.mismatch_since < MISMATCH_FOR or self.reported:
            return
        self.reported = True
        if restarted_recently():
            self.events.record("", "", f"controller check FAILED: {problem}; not restarting, the Pi "
                                       "already restarted for this within 6 h")
            return
        self.events.record("", "", f"controller check FAILED: {problem}; restarting the Pi")
        restart_pi()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=Path("/var/lib/tenebrio-lights/config.yaml"))
    parser.add_argument("--events", type=Path, default=Path("/var/lib/tenebrio-lights/events.csv"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from smbus2 import SMBus
    validator = Validator(ConfigWatcher(args.config), SMBus(1), EventLog(args.events))
    while True:
        try:
            validator.step()
        except Exception:
            log.exception("check failed")
        time.sleep(CHECK_EVERY)


if __name__ == "__main__":
    main()
