import pytest

import tenebrio_network as net


class FakeEvents:
    def __init__(self):
        self.rows = []

    def record(self, treatment, state, message):
        self.rows.append(message)


@pytest.fixture
def world(monkeypatch, tmp_path):
    """A fake radio: which connection is up, which exist, which can connect."""
    state = {"active": None, "existing": {"home-wifi", net.HOTSPOT}, "reachable": set(),
             "phone": False, "clock": 0.0, "attempts": [], "works": True, "dark": False,
             "reloads": 0, "restarts": 0}

    def bring_up(name):
        state["attempts"].append(name)
        if name == net.HOTSPOT or name in state["reachable"]:
            state["active"] = name
            return True
        return False

    monkeypatch.setattr(net, "active_wifi", lambda: state["active"])
    monkeypatch.setattr(net, "configured", lambda names: [n for n in names if n in state["existing"]])
    monkeypatch.setattr(net, "bring_up", bring_up)
    monkeypatch.setattr(net, "phone_connected", lambda: state["phone"])
    monkeypatch.setattr(net, "HOTSPOT_ONLY", tmp_path / "hotspot-only")
    monkeypatch.setattr(net.time, "monotonic", lambda: state["clock"])
    monkeypatch.setattr(net, "client_works", lambda: state["works"])
    monkeypatch.setattr(net, "hotspot_works", lambda: state["works"])
    monkeypatch.setattr(net, "lights_all_dark", lambda: state["dark"])
    monkeypatch.setattr(net, "LAST_RESTART", tmp_path / "network-restart")

    def reload_wifi_driver():
        state["reloads"] += 1
        return True

    def restart_pi():
        state["restarts"] += 1
        net.LAST_RESTART.write_text(f"{net.time.time():.0f}\n")

    monkeypatch.setattr(net, "reload_wifi_driver", reload_wifi_driver)
    monkeypatch.setattr(net, "restart_pi", restart_pi)
    return state


def test_boot_prefers_a_client_network(world):
    world["reachable"] = {"home-wifi"}
    network = net.Network(FakeEvents())
    network.step()
    assert world["active"] == "home-wifi"
    assert world["attempts"] == ["home-wifi"]
    assert network.events.rows == ["network: connected to home-wifi"]


def test_boot_without_clients_starts_hotspot(world):
    network = net.Network(FakeEvents())
    network.step()
    assert world["active"] == net.HOTSPOT
    assert network.events.rows == ["network: hotspot started (no client network available)"]


def test_lost_client_gets_a_grace_period_then_hotspot(world):
    world["reachable"] = {"home-wifi"}
    network = net.Network(FakeEvents())
    network.step()
    world["active"], world["reachable"] = None, set()  # home Wi-Fi drops and stays gone
    world["clock"] = 60
    network.step()
    assert world["active"] is None  # still within CLIENT_GRACE
    world["clock"] = 60 + net.CLIENT_GRACE
    network.step()
    assert world["active"] == net.HOTSPOT


def test_hotspot_retries_clients_only_when_no_phone_is_connected(world):
    network = net.Network(FakeEvents())
    network.step()  # hotspot at boot
    world["reachable"] = {"home-wifi"}
    world["phone"] = True
    world["clock"] = net.RETRY_FROM_HOTSPOT + 1
    network.step()
    assert world["active"] == net.HOTSPOT  # a phone is using the page: stay
    world["phone"] = False
    network.step()
    assert world["active"] == "home-wifi"
    assert network.events.rows[-1] == "network: connected to home-wifi"


def test_failed_retry_returns_to_hotspot(world):
    network = net.Network(FakeEvents())
    network.step()
    world["clock"] = net.RETRY_FROM_HOTSPOT + 1
    world["active"] = net.HOTSPOT
    network.step()
    assert world["active"] == net.HOTSPOT
    assert world["attempts"][-2:] == ["home-wifi", net.HOTSPOT]


def test_hotspot_raised_by_networkmanager_is_logged(world):
    world["reachable"] = {"home-wifi"}
    network = net.Network(FakeEvents())
    network.step()
    world["active"], world["reachable"] = net.HOTSPOT, set()  # NM's autoconnect took over
    world["clock"] = 30
    network.step()
    assert network.events.rows[-1] == "network: hotspot active (no client network connected)"


def test_hotspot_only_flag_overrides_home_wifi_and_clearing_it_retries_at_once(world):
    world["reachable"] = {"home-wifi"}
    network = net.Network(FakeEvents())
    network.step()
    net.HOTSPOT_ONLY.touch()
    world["clock"] = 30
    network.step()
    assert world["active"] == net.HOTSPOT
    assert network.events.rows[-1] == "network: hotspot started (hotspot only, set on the Configuración page)"
    world["clock"] = 60
    network.step()
    assert world["attempts"].count("home-wifi") == 1  # no client retries while the flag is set
    net.HOTSPOT_ONLY.unlink()
    world["phone"] = True  # the phone that cleared the flag is still on the hotspot
    world["clock"] = 90
    network.step()
    assert world["active"] == "home-wifi"


def connected(world):
    world["reachable"] = {"home-wifi"}
    network = net.Network(FakeEvents())
    network.step()
    network.events.rows.clear()
    return network


def checks(network, n):
    for _ in range(n):
        network.step()


def test_working_network_logs_nothing(world):
    network = connected(world)
    checks(network, 10)
    assert network.events.rows == []


def test_a_short_glitch_is_not_repaired(world):
    network = connected(world)
    world["works"] = False
    checks(network, net.FAILURES_BEFORE_REPAIR - 1)
    world["works"] = True
    checks(network, 1)
    assert network.events.rows == [] and world["attempts"] == ["home-wifi"]


def test_reconnect_fixes_it(world):
    network = connected(world)
    world["works"] = False
    checks(network, net.FAILURES_BEFORE_REPAIR)
    assert world["attempts"] == ["home-wifi", "home-wifi"]
    world["works"] = True
    checks(network, 1)
    assert network.events.rows == ["network: home-wifi: router not answering; reconnecting",
                                   "network: home-wifi working again"]
    assert world["reloads"] == 0


def test_repairs_escalate_and_restart_waits_for_dark(world):
    network = connected(world)
    world["works"] = False
    checks(network, 2 * net.FAILURES_BEFORE_REPAIR)
    assert world["reloads"] == 1 and world["restarts"] == 0
    checks(network, net.FAILURES_BEFORE_REPAIR + 5)  # lights on: wait, logged once
    assert world["restarts"] == 0
    assert network.events.rows[-1] == ("network: home-wifi: still failing; will restart the Pi "
                                       "when every light is dark")
    world["dark"] = True
    checks(network, 1)
    assert world["restarts"] == 1
    assert network.events.rows[-1] == "network: home-wifi: still failing; restarting the Pi while every light is dark"


def test_no_second_restart_within_spacing(world):
    net.LAST_RESTART.write_text(f"{net.time.time():.0f}\n")
    world["dark"] = True
    network = connected(world)
    world["works"] = False
    checks(network, 3 * net.FAILURES_BEFORE_REPAIR + 5)
    assert world["restarts"] == 0
    assert network.events.rows[-1].endswith("not restarting, the Pi already restarted for this within 6 h")


def test_hotspot_is_checked_too(world):
    network = net.Network(FakeEvents())
    network.step()  # no client network: hotspot
    network.events.rows.clear()
    world["works"] = False
    checks(network, net.FAILURES_BEFORE_REPAIR)
    assert network.events.rows == ["network: tenebrio-hotspot: hotspot not working; reconnecting"]


def test_lights_all_dark_follows_the_schedule(monkeypatch, tmp_path):
    from datetime import datetime as real_datetime
    config = tmp_path / "config.yaml"
    config.write_text('start: 2026-10-01 00:00:00\ntreatments:\n'
                      '  1: {light_on: "07:00", light_off: "03:00"}\n  2: {light_on: "09:00", light_off: "13:00"}\n')
    monkeypatch.setattr(net, "CONFIG", config)
    monkeypatch.setattr(net, "clock_source", lambda: "RTC")

    class At(real_datetime):
        moment = None

        @classmethod
        def now(cls, tz=None):
            return cls.moment

    monkeypatch.setattr(net, "datetime", At)
    At.moment = real_datetime(2026, 10, 20, 4, 0).astimezone()   # both dark
    assert net.lights_all_dark()
    At.moment = real_datetime(2026, 10, 20, 10, 0).astimezone()  # both lit
    assert not net.lights_all_dark()
    monkeypatch.setattr(net, "clock_source", lambda: None)       # lights held off
    assert net.lights_all_dark()
    monkeypatch.setattr(net, "clock_source", lambda: "RTC")
    config.write_text("not: a config\n")                         # unreadable: never restart
    assert not net.lights_all_dark()
