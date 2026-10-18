"""Choose the Pi's Wi-Fi: home Wi-Fi when it works, else the hotspot.

NetworkManager's own autoconnect raced the hotspot against the client network at boot, so
the client network has autoconnect off and this service drives the single radio:

- with no Wi-Fi active, try the client network; if it doesn't connect, start the hotspot
- on the hotspot, retry the client every RETRY_FROM_HOTSPOT, but never while a phone is
  connected (it would lose the monitoring page mid-visit)
- when the client network has been down for CLIENT_GRACE, start the hotspot (NetworkManager's
  own autoconnect on the hotspot usually gets there first, which is a fine safety net)
- while the HOTSPOT_ONLY flag file exists (set on the Configuración page, to try the lab setup
  at home), use only the hotspot

Every 30 s it also checks that the active network works (home Wi-Fi: the router answers a
ping; hotspot: the radio is an access point holding the hotspot address). After a few failed
checks in a row it repairs, one step at a time while the checks keep failing: reconnect,
reload the Wi-Fi driver, and as a last resort restart the Pi, but only while every light is
scheduled dark (a restart turns the lights off for ~30 s; the experimenters' choice,
2026-10-18) and at most once per RESTART_SPACING. The rig is unattended in the lab, so a
stuck radio must not need someone to unplug it.

Every network change, failed check and repair goes to the event log.
"""

import argparse
import logging
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from tenebrio_lights import ConfigError, EventLog, clock_source, load_config

CLIENTS = ("home-wifi",)  # NetworkManager connection names, in order of preference
HOTSPOT = "tenebrio-hotspot"
HOTSPOT_ONLY = Path("/var/lib/tenebrio-lights/hotspot-only")
CHECK_EVERY = 30
CLIENT_GRACE = 120
RETRY_FROM_HOTSPOT = 30 * 60
CONNECT_TIMEOUT = 60
HOTSPOT_ADDRESS = "10.42.0.1"  # NetworkManager's address for a shared (hotspot) connection
FAILURES_BEFORE_REPAIR = 3
RESTART_SPACING = 6 * 3600
LAST_RESTART = Path("/var/lib/tenebrio-lights/network-restart")  # survives the restart it records
CONFIG = Path("/var/lib/tenebrio-lights/config.yaml")

log = logging.getLogger("tenebrio-network")


def nmcli(*args: str, timeout: float = 15) -> subprocess.CompletedProcess:
    return subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)


def active_wifi() -> str | None:
    out = nmcli("-t", "-f", "NAME,TYPE", "connection", "show", "--active").stdout
    for line in out.splitlines():
        name, _, kind = line.rpartition(":")
        if kind == "802-11-wireless":
            return name.replace("\\:", ":")
    return None


def configured(names) -> list[str]:
    existing = set(nmcli("-t", "-f", "NAME", "connection", "show").stdout.split("\n"))
    return [n for n in names if n in existing]


def bring_up(name: str) -> bool:
    try:
        return nmcli("--wait", str(CONNECT_TIMEOUT), "connection", "up", name,
                     timeout=CONNECT_TIMEOUT + 15).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def run(*args: str, timeout: float = 15) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), capture_output=True, text=True, timeout=timeout)


def client_works() -> bool:
    """The router answers a ping over the Wi-Fi."""
    route = run("ip", "-4", "route", "show", "default", "dev", "wlan0").stdout
    gateway = re.search(r"default via (\S+)", route)
    return bool(gateway) and run("ping", "-c", "3", "-W", "2", "-I", "wlan0", gateway[1]).returncode == 0


def hotspot_works() -> bool:
    """The radio is an access point and holds the hotspot address."""
    info = run("iw", "dev", "wlan0", "info").stdout
    address = run("ip", "-4", "-o", "addr", "show", "dev", "wlan0").stdout
    return "type AP" in info and f" {HOTSPOT_ADDRESS}/" in address


def reload_wifi_driver() -> bool:
    """Unload and reload the Pi's Wi-Fi driver: the software version of unplugging the adapter."""
    unload = run("modprobe", "-r", "brcmfmac_cyw", "brcmfmac", timeout=60)
    return unload.returncode == 0 and run("modprobe", "brcmfmac", timeout=60).returncode == 0


def lights_all_dark() -> bool:
    """Every light is scheduled off now, so a restart cannot cut a light period short."""
    if clock_source() is None:
        return True  # the controller holds every light off anyway
    try:
        return not any(load_config(CONFIG).desired_states(datetime.now().astimezone()).values())
    except (OSError, ValueError, ConfigError):
        return False


def restarted_recently() -> bool:
    try:
        return time.time() - float(LAST_RESTART.read_text()) < RESTART_SPACING
    except (OSError, ValueError):
        return False


def restart_pi() -> None:
    LAST_RESTART.write_text(f"{time.time():.0f}\n")
    subprocess.run(["systemctl", "reboot"])


def phone_connected() -> bool:
    """A device on the hotspot answered recently (the page refreshes every 15 s while open)."""
    out = subprocess.run(["ip", "neigh", "show", "dev", "wlan0"], capture_output=True, text=True).stdout
    return any(state in line for line in out.splitlines() for state in ("REACHABLE", "DELAY", "PROBE"))


class Network:
    def __init__(self, events: EventLog):
        self.events = events
        self.client_lost_at: float | None = None
        self.last_client_attempt = float("-inf")
        self.last_state = "unknown"
        self.hotspot_only = False
        self.failures = 0
        self.repairs = 0  # repair steps taken since the network last worked

    def note(self, state: str, message: str) -> None:
        if state != self.last_state:
            self.events.record("", "", f"network: {message}")
            self.last_state = state

    def try_clients(self) -> bool:
        self.last_client_attempt = time.monotonic()
        for name in configured(CLIENTS):
            if bring_up(name):
                self.note(name, f"connected to {name}")
                return True
        return False

    def start_hotspot(self, reason: str) -> None:
        if bring_up(HOTSPOT):
            self.note(HOTSPOT, f"hotspot started ({reason})")
        else:
            self.note("none", f"hotspot failed to start ({reason})")

    def step(self) -> None:
        self.choose()
        self.check()

    def check(self) -> None:
        """Repair the active network one step at a time while it keeps failing its check."""
        current = active_wifi()
        if current is None:
            return  # choose() handles having no network
        works = hotspot_works() if current == HOTSPOT else client_works()
        if works:
            if self.repairs or self.failures >= FAILURES_BEFORE_REPAIR:
                self.events.record("", "", f"network: {current} working again")
            self.failures = self.repairs = 0
            return
        self.failures += 1
        if self.failures < FAILURES_BEFORE_REPAIR:
            return
        what = "hotspot not working" if current == HOTSPOT else "router not answering"
        if self.repairs == 0:
            self.events.record("", "", f"network: {current}: {what}; reconnecting")
            bring_up(current)
            self.failures = 0  # give the repair a few checks to take effect
        elif self.repairs == 1:
            ok = reload_wifi_driver()
            self.events.record("", "", f"network: {current}: still failing; Wi-Fi driver reloaded"
                               if ok else f"network: {current}: still failing; Wi-Fi driver reload FAILED")
            self.failures = 0
        elif restarted_recently():
            if self.repairs == 2:
                self.events.record("", "", f"network: {current}: still failing; not restarting, "
                                           "the Pi already restarted for this within 6 h")
        elif lights_all_dark():
            self.events.record("", "", f"network: {current}: still failing; restarting the Pi "
                                       "while every light is dark")
            restart_pi()
        elif self.repairs == 2:
            self.events.record("", "", f"network: {current}: still failing; will restart the Pi "
                                       "when every light is dark")
        self.repairs += 1

    def choose(self) -> None:
        now = time.monotonic()
        current = active_wifi()
        if HOTSPOT_ONLY.exists():
            self.hotspot_only = True
            if current != HOTSPOT:
                self.start_hotspot("hotspot only, set on the Configuración page")
            else:
                self.note(HOTSPOT, "hotspot active (hotspot only, set on the Configuración page)")
            return
        if self.hotspot_only:
            # Switched back on the Configuración page: retry the client now, even with a phone
            # connected, since that phone is what asked for it.
            self.hotspot_only = False
            if current == HOTSPOT and configured(CLIENTS) and not self.try_clients():
                self.start_hotspot("client networks still unavailable")
            return
        if current in CLIENTS:
            self.client_lost_at = None
            self.note(current, f"connected to {current}")
            return
        if current == HOTSPOT:
            # NetworkManager may raise the hotspot itself (autoconnect is its safety net), so
            # log the state, not only our own switches.
            self.note(HOTSPOT, "hotspot active (no client network connected)")
            if (now - self.last_client_attempt >= RETRY_FROM_HOTSPOT and configured(CLIENTS)
                    and not phone_connected()):
                if not self.try_clients():
                    self.start_hotspot("client networks still unavailable")
            return
        # No Wi-Fi active: at boot, or a client network dropped.
        if self.last_state in CLIENTS:
            self.client_lost_at = self.client_lost_at or now
            if now - self.client_lost_at < CLIENT_GRACE:
                return
        if not self.try_clients():
            self.start_hotspot("no client network available")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--events", type=Path, default=Path("/var/lib/tenebrio-lights/events.csv"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    network = Network(EventLog(args.events))
    while True:
        try:
            network.step()
        except Exception:
            log.exception("network step failed")
        time.sleep(CHECK_EVERY)


if __name__ == "__main__":
    main()
