import pytest

import tenebrio_validator as val
from tenebrio_lights import RELAY_OFF, RELAY_ON, ConfigWatcher


class FakeEvents:
    def __init__(self):
        self.rows = []

    def record(self, treatment, state, message):
        self.rows.append(message)


class FakeHat:
    def __init__(self):
        self.registers = {1: RELAY_OFF, 2: RELAY_OFF, 3: RELAY_OFF, 4: RELAY_OFF}
        self.broken = False

    def read_byte_data(self, address, register):
        if self.broken:
            raise OSError(121, "Remote I/O error")
        return self.registers[register]


@pytest.fixture
def rig(monkeypatch, tmp_path):
    """Treatment 1 lit all day but one minute, so 'now' is almost surely in its light period."""
    config = tmp_path / "config.yaml"
    config.write_text('start: 2026-10-01 00:00:00\ntreatments:\n  1: {light_on: "00:00", light_off: "23:59"}\n')
    state = {"clock": 0.0, "restarts": 0}
    monkeypatch.setattr(val, "clock_source", lambda: "NTP")
    monkeypatch.setattr(val.time, "monotonic", lambda: state["clock"])
    monkeypatch.setattr(val, "LAST_RESTART", tmp_path / "light-check-restart")

    def restart_pi():
        state["restarts"] += 1
        val.LAST_RESTART.write_text(f"{val.time.time():.0f}\n")

    monkeypatch.setattr(val, "restart_pi", restart_pi)
    hat = FakeHat()
    hat.registers[1] = RELAY_ON
    validator = val.Validator(ConfigWatcher(config), hat, FakeEvents())
    if not validator.expected()[1]:
        pytest.skip("ran during the one dark minute")
    return validator, hat, state


def run_for(validator, state, seconds):
    end = state["clock"] + seconds
    while state["clock"] <= end:
        validator.step()
        state["clock"] += val.CHECK_EVERY


def test_matching_relays_log_nothing(rig):
    validator, hat, state = rig
    run_for(validator, state, 600)
    assert validator.events.rows == [] and state["restarts"] == 0


def test_short_mismatch_is_left_to_the_controller(rig):
    validator, hat, state = rig
    hat.registers[1] = RELAY_OFF  # e.g. the HAT reset; the controller re-asserts within 60 s
    run_for(validator, state, 60)
    hat.registers[1] = RELAY_ON
    run_for(validator, state, 60)
    assert validator.events.rows == [] and state["restarts"] == 0


def test_lasting_mismatch_logs_an_error_and_restarts(rig):
    validator, hat, state = rig
    hat.registers[1] = RELAY_OFF  # e.g. the controller froze
    run_for(validator, state, val.MISMATCH_FOR + val.CHECK_EVERY)
    assert state["restarts"] == 1
    assert validator.events.rows == ["controller check FAILED: schedule says 1 light, 2 dark, 3 dark, 4 dark; "
                                     "relays read 1 dark, 2 dark, 3 dark, 4 dark; restarting the Pi"]


def test_no_second_restart_within_spacing(rig):
    validator, hat, state = rig
    val.LAST_RESTART.write_text(f"{val.time.time():.0f}\n")
    hat.registers[2] = RELAY_ON  # a light on that should be off
    run_for(validator, state, 600)
    assert state["restarts"] == 0
    assert len(validator.events.rows) == 1 and validator.events.rows[0].endswith("within 6 h")
    hat.registers[2] = RELAY_OFF
    run_for(validator, state, 15)
    assert validator.events.rows[-1] == "controller check: relays match the schedule again"


def test_hat_not_answering_counts_as_wrong(rig):
    validator, hat, state = rig
    hat.broken = True
    run_for(validator, state, val.MISMATCH_FOR + val.CHECK_EVERY)
    assert state["restarts"] == 1 and "relay HAT not answering" in validator.events.rows[0]


def test_untrusted_clock_means_all_off(rig, monkeypatch):
    validator, hat, state = rig
    monkeypatch.setattr(val, "clock_source", lambda: None)
    hat.registers[1] = RELAY_OFF  # the controller holds every light off
    run_for(validator, state, 600)
    assert validator.events.rows == [] and state["restarts"] == 0
