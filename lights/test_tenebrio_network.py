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
             "phone": False, "clock": 0.0, "attempts": []}

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
