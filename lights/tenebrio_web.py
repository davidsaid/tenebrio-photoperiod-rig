"""Monitoring site for the rig, served on the Pi's hotspot (http://10.42.0.1).

Three main pages: Inicio (the experiment: each enclosure's temperature and humidity against its
light schedule, CSV downloads), Sistema (clock, Pi health, network, event log, archived
experiments) and Configuración (the experiment's name and schedule, archiving it, the
hotspot-only switch, the settings password and shutting the Pi down). A fourth page,
Archivo, shows one archived experiment with the same charts as Inicio. Every change needs the settings password, so that visitors to the lab can
look but not touch. Saving the schedule validates with the controller's own load_config,
replaces config.yaml atomically, and records the change in the event log; the controller
picks the new file up within a second.
"""

import argparse
import csv
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import subprocess
import tempfile
import threading
import time
import zipfile
from io import BytesIO
from urllib.parse import parse_qs, urlsplit
from http.cookies import SimpleCookie
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from tenebrio_lights import (EXPERIMENT_NAME, TREATMENT_IDS, Config, ConfigError, EventLog, Treatment,
                             clock_source, config_to_yaml, format_duration, load_config, parse_duration)
from tenebrio_logger import SENSORS
from tenebrio_network import HOTSPOT_ONLY

WEB = Path(__file__).with_name("web")
FILES = {  # URL -> (file in web/, content type)
    "/": ("index.html", "text/html; charset=utf-8"),
    "/sistema": ("sistema.html", "text/html; charset=utf-8"),
    "/configuracion": ("configuracion.html", "text/html; charset=utf-8"),
    "/archivo": ("archivo.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/i18n.js": ("i18n.js", "text/javascript; charset=utf-8"),
}
RECENT_EVENTS = 300
PASSWORD_FILE = Path("/var/lib/tenebrio-lights/settings-password")
DEFAULT_PASSWORD = "tenebrio"  # until it is changed on the Configuración page (David, 2026-10-11)
PBKDF2_ITERATIONS = 200_000
SESSION_COOKIE = "tenebrio_session"
SESSION_SECONDS = 12 * 3600
FAILED_LOGIN_DELAY = 1.0
PROTECTED = ("/api/config", "/api/hotspot-only", "/api/password", "/api/shutdown", "/api/archive")
ARCHIVE_DIR = "archive"  # next to config.yaml: archive/<experiment name>/
SERIES = [f"{kind}_{name}" for name in SENSORS for kind in ("temp_c", "rh")] + ["cpu_temp_c"]


def read_table(path: Path) -> list[dict[str, str]]:
    """CSV rows as dicts keyed by the file's own header, damaged lines dropped."""
    if not path.exists():
        return []
    text = path.read_bytes().decode(errors="replace")
    lines = [line for line in text.splitlines() if line and "\0" not in line]
    return list(csv.DictReader(lines))


def read_rows(path: Path) -> list[list[str]]:
    """CSV rows without the header; lines damaged by power cuts (NUL bytes) are dropped."""
    if not path.exists():
        return []
    text = path.read_bytes().decode(errors="replace")
    lines = [line for line in text.splitlines()[1:] if line and "\0" not in line]
    return list(csv.reader(lines))


def event_kind(row: list[str]) -> str:
    message = row[3] if len(row) > 3 else ""
    if row[1:3] and row[1] and row[2]:
        return "switch"
    for prefix, kind in (("outage", "outage"), ("readings gap", "outage"), ("config", "config"),
                         ("clock", "clock"), ("controller", "controller"), ("sensor", "sensor"),
                         ("network", "network"), ("experiment", "experiment")):
        if message.startswith(prefix):
            return kind
    return "other"


def status(config_path: Path, events_path: Path, readings_path: Path) -> dict:
    now = datetime.now().astimezone()
    config = load_config(config_path)
    treatments = []
    for tid in TREATMENT_IDS:
        t = config.treatments.get(tid)
        if t is None:
            continue
        upcoming = t.next_switch(now, config.start)
        treatments.append({
            "id": tid,
            "light": format_duration(t.light),
            "dark": format_duration(t.dark),
            "is_light": t.is_light(now, config.start),
            "next_switch": upcoming.isoformat(timespec="seconds") if upcoming else None,
            "cycle_position": cycle_position(t, now, config.start),
        })

    events = [{"time": r[0], "treatment": r[1], "state": r[2], "message": r[3] if len(r) > 3 else "",
               "kind": event_kind(r)} for r in read_rows(events_path) if r]
    notable = [e for e in events if e["message"] != "set"]
    readings = read_table(readings_path)

    return {
        "now": now.isoformat(timespec="seconds"),
        "experiment": config.experiment,
        "clock": clock_source(),
        "start": config.start.isoformat(timespec="seconds"),
        "started": now >= config.start,
        "readings_interval": raw_readings_interval(config_path),
        "treatments": treatments,
        "sensors": list(SENSORS),
        "latest_reading": readings[-1] if readings else None,
        "outages": [e for e in events if e["kind"] == "outage"][::-1],
        "recent_events": notable[-RECENT_EVENTS:][::-1],
        "network": {"wifi": active_wifi(), "hotspot_only": HOTSPOT_ONLY.exists()},
        "files": {name: file_info(path) for name, path in (("events", events_path), ("readings", readings_path))},
    }


def active_wifi() -> str | None:
    try:
        out = subprocess.run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        name, _, kind = line.rpartition(":")
        if kind == "802-11-wireless":
            return name
    return None


def number(value: str | None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def series(events_path: Path, readings_path: Path) -> dict:
    """Every reading as columns of numbers (null where a sensor gave none), the times each
    treatment's light was actually on, from the controller's own event log, and the events
    themselves, to draw over the charts."""
    readings = [r for r in read_table(readings_path) if r.get("timestamp")]
    times = []
    for r in readings:
        try:
            times.append(datetime.fromisoformat(r["timestamp"]).timestamp())
        except ValueError:
            times.append(None)
    keep = [i for i, t in enumerate(times) if t is not None]
    return {
        "t": [times[i] for i in keep],
        "series": {name: [number(readings[i].get(name)) for i in keep] for name in SERIES},
        "light": light_periods(read_rows(events_path)),
        "events": chart_events(read_rows(events_path)),
        "now": datetime.now().astimezone().timestamp(),
    }


def chart_events(rows: list[list[str]]) -> list[dict]:
    """Events with epoch times. A "set" row (the controller starting) counts as a light
    switch only if it changes the light from what it was, a power cut having turned it off."""
    out, lights = [], {}
    for row in rows:
        if len(row) < 4:
            continue
        try:
            when = datetime.fromisoformat(row[0]).timestamp()
        except ValueError:
            continue
        kind, treatment, state = event_kind(row), row[1], row[2]
        if kind == "switch":
            if lights.get(treatment, "dark") == state:
                continue
            lights[treatment] = state
        elif kind == "outage" and row[3].startswith("outage:"):
            lights = {}
        out.append({"t": when, "kind": kind, "treatment": treatment, "state": state, "message": row[3]})
    return out


def light_periods(rows: list[list[str]]) -> dict[str, list[list[float | None]]]:
    """[on, off] epoch pairs per treatment; off is None while the light is still on.

    A power cut turns every light off without an event, so the outage row, written after the
    reboot, closes any open period at its "last reading" time.
    """
    periods: dict[str, list[list[float | None]]] = {}
    for row in rows:
        if len(row) < 4:
            continue
        try:
            when = datetime.fromisoformat(row[0]).timestamp()
        except ValueError:
            continue
        treatment, state, message = row[1], row[2], row[3]
        if treatment and state in ("light", "dark"):
            mine = periods.setdefault(treatment, [])
            is_open = bool(mine) and mine[-1][1] is None
            if state == "light" and not is_open:
                mine.append([when, None])
            elif state == "dark" and is_open:
                mine[-1][1] = when
        elif message.startswith("outage: last reading "):
            try:
                cut = datetime.fromisoformat(message.split()[3].rstrip(",")).timestamp()
            except (IndexError, ValueError):
                continue
            for mine in periods.values():
                if mine and mine[-1][1] is None:
                    mine[-1][1] = max(cut, mine[-1][0])
    return periods


def set_hotspot_only(on: bool, events_path: Path) -> None:
    if on == HOTSPOT_ONLY.exists():
        return
    if on:
        HOTSPOT_ONLY.touch()
    else:
        HOTSPOT_ONLY.unlink()
    EventLog(events_path).record("", "", f"hotspot only switched {'on' if on else 'off'} from the Configuración page")


def cycle_position(t: Treatment, now: datetime, start: datetime) -> float | None:
    """Fraction of the way through the current cycle (0 = lights on), or None if not running."""
    elapsed = now.timestamp() - start.timestamp()
    cycle = (t.light + t.dark).total_seconds()
    if elapsed < 0 or cycle == 0:
        return None
    return (elapsed % cycle) / cycle


def file_info(path: Path) -> dict:
    if not path.exists():
        return {"rows": 0, "bytes": 0}
    return {"rows": len(read_rows(path)), "bytes": path.stat().st_size}


def raw_readings_interval(config_path: Path) -> str:
    raw = yaml.safe_load(config_path.read_text()) or {}
    return raw.get("readings_interval", "00:05:00")


def config_from_form(form: dict) -> Config:
    """Validate the page's submission exactly as the controller validates config.yaml."""
    try:
        start = datetime.fromisoformat(str(form["start"])).astimezone()
    except (KeyError, ValueError):
        raise ConfigError("Fecha de inicio no válida")
    treatments = {}
    for entry in form.get("treatments", []):
        tid = int(entry["id"])
        if tid not in TREATMENT_IDS:
            raise ConfigError(f"Tratamiento {tid}: los números van del 1 al 4")
        for name, value in (("luz", entry["light"]), ("oscuridad", entry["dark"])):
            if not isinstance(value, str) or not re.fullmatch(r"\d+:[0-5]\d:[0-5]\d", value):
                raise ConfigError(f"Tratamiento {tid}: la duración de {name} debe ser HH:MM:SS, por ejemplo 04:00:00")
        light = parse_duration(entry["light"], f"treatment {tid} light")
        dark = parse_duration(entry["dark"], f"treatment {tid} dark")
        if light + dark == timedelta(0):
            raise ConfigError(f"Tratamiento {tid}: luz y oscuridad no pueden ser ambas cero")
        treatments[tid] = Treatment(tid, light, dark)
    return Config(start, treatments, check_experiment_name(form.get("experiment", "")))


def check_experiment_name(name) -> str:
    name = str(name or "").strip()
    if not EXPERIMENT_NAME.fullmatch(name):
        raise ConfigError("experiment_name")
    return name


def summarize(config: Config) -> str:
    parts = ([f"experiment {config.experiment}"] if config.experiment else []) + [f"start {config.start.strftime('%Y-%m-%d %H:%M:%S')}"]
    parts += [f"treatment {tid} light {format_duration(t.light)} dark {format_duration(t.dark)}"
              for tid, t in sorted(config.treatments.items())]
    return ", ".join(parts) + ("" if config.treatments else ", no treatments")


def save_config(config: Config, config_path: Path, events_path: Path) -> None:
    old = load_config(config_path)
    text = config_to_yaml(config, raw_readings_interval(config_path))
    with tempfile.NamedTemporaryFile("w", dir=config_path.parent, delete=False, suffix=".tmp") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    load_config(Path(f.name))  # the exact bytes the controller will read must pass its validation
    os.replace(f.name, config_path)
    fsync_dir(config_path.parent)
    EventLog(events_path).record("", "", f"config changed from the monitoring page: "
                                         f"{summarize(old)} -> {summarize(config)}")


def fsync_dir(path: Path) -> None:
    directory = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def archive_root(config_path: Path) -> Path:
    return config_path.parent / ARCHIVE_DIR


def archive_experiment(archive_as: str, new_name: str, new_start: datetime,
                       config_path: Path, events_path: Path, readings_path: Path) -> Path:
    """Close the running experiment and start the next one with the same photoperiods.

    The readings, the events (ending with an "archived" row), any older readings files and a
    copy of the config move to archive/<archive_as>/ and become read-only. The services
    open their files for every row, so their next rows start new files. The new experiment
    keeps the photoperiods; its lights stay off until `new_start`.
    """
    archive_as, new_name = check_experiment_name(archive_as), check_experiment_name(new_name)
    if archive_as == new_name:
        raise ConfigError("same_name")
    folder = archive_root(config_path) / archive_as
    if folder.exists():
        raise ConfigError("archive_exists")
    old = load_config(config_path)
    new = Config(new_start, old.treatments, new_name)
    folder.mkdir(parents=True)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    EventLog(events_path).record("", "", f"experiment archived as {ARCHIVE_DIR}/{archive_as}; next experiment {new_name}")
    snapshot = config_path.read_text()
    if old.experiment != archive_as:
        snapshot = snapshot.replace(f"experiment: {old.experiment}\n", "") if old.experiment else snapshot
        snapshot = f"experiment: {archive_as}\n" + snapshot
    (folder / "config.yaml").write_text(snapshot + f"archived: {now}\n")
    moved = [p for p in (readings_path, events_path) if p.exists()]
    moved += sorted(readings_path.parent.glob(f"{readings_path.stem}-until-*{readings_path.suffix}"))
    for path in moved:
        os.replace(path, folder / path.name)
    EventLog(events_path)  # header first, so the next row from any service lands in a proper file
    for path in folder.iterdir():
        path.chmod(0o444)
    folder.chmod(0o555)
    fsync_dir(folder)
    fsync_dir(config_path.parent)
    save_config(new, config_path, events_path)
    EventLog(events_path).record("", "", f"experiment {new_name} started; previous experiment archived as "
                                         f"{ARCHIVE_DIR}/{archive_as}")
    return folder


def archived_experiments(config_path: Path) -> list[dict]:
    root = archive_root(config_path)
    out = []
    for folder in sorted(root.iterdir()) if root.exists() else []:
        info = archived_info(folder)
        if info:
            out.append(info)
    return sorted(out, key=lambda e: e.get("archived") or "", reverse=True)


def archived_info(folder: Path) -> dict | None:
    try:
        raw = yaml.safe_load((folder / "config.yaml").read_text()) or {}
        config = load_config(folder / "config.yaml")
    except (OSError, ValueError, yaml.YAMLError):
        return None
    readings = read_table(folder / "readings.csv")
    events = [{"time": r[0], "treatment": r[1], "state": r[2], "message": r[3], "kind": event_kind(r)}
              for r in read_rows(folder / "events.csv") if len(r) > 3]
    archived = raw.get("archived")
    return {
        "name": folder.name,
        "start": config.start.isoformat(timespec="seconds"),
        "archived": archived.isoformat(timespec="seconds") if isinstance(archived, datetime) else archived,
        "first_reading": readings[0]["timestamp"] if readings else None,
        "last_reading": readings[-1]["timestamp"] if readings else None,
        "readings_interval": raw.get("readings_interval", "00:05:00"),
        "treatments": [{"id": tid, "light": format_duration(t.light), "dark": format_duration(t.dark)}
                       for tid, t in sorted(config.treatments.items())],
        "sensors": list(SENSORS),
        "outages": [e for e in events if e["kind"] == "outage"][::-1],
        "files": {name: file_info(folder / f"{name}.csv") for name in ("events", "readings")},
    }


def archive_folder(config_path: Path, name: str) -> Path | None:
    """The archive folder for a name from a URL, or None; never a path outside the archive."""
    if not EXPERIMENT_NAME.fullmatch(name):
        return None
    folder = archive_root(config_path) / name
    return folder if folder.is_dir() else None


def archive_zip(config_path: Path, events_path: Path, readings_path: Path) -> bytes:
    """Every archived experiment, plus the running one under running/<name>/."""
    buffer = BytesIO()
    running = f"running/{load_config(config_path).experiment or 'unnamed'}"
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        root = archive_root(config_path)
        for path in sorted(root.rglob("*")) if root.exists() else []:
            if path.is_file():
                z.writestr(f"archive/{path.relative_to(root)}", clean(path))
        for path in [config_path, events_path, readings_path,
                     *sorted(readings_path.parent.glob(f"{readings_path.stem}-until-*{readings_path.suffix}"))]:
            if path.exists():
                z.writestr(f"{running}/{path.name}", clean(path))
    return buffer.getvalue()


def clean(path: Path) -> bytes:
    """File contents without the NUL bytes a power cut can leave."""
    return path.read_bytes().replace(b"\0", b"")


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def check_password(password: str) -> bool:
    if not PASSWORD_FILE.exists():
        return hmac.compare_digest(password.encode(), DEFAULT_PASSWORD.encode())
    try:
        _, iterations, salt, digest = PASSWORD_FILE.read_text().strip().split("$")
    except ValueError:
        return False
    attempt = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
    return hmac.compare_digest(attempt.hex(), digest)


def set_password(password: str, events_path: Path) -> None:
    if len(password) < 4:
        raise ValueError("password_too_short")
    tmp = PASSWORD_FILE.with_suffix(".tmp")
    tmp.write_text(hash_password(password) + "\n")
    os.replace(tmp, PASSWORD_FILE)
    EventLog(events_path).record("", "", "settings password changed from the Configuración page")


class Sessions:
    """Logged-in browsers, by random cookie token; kept in memory, so a restart logs everyone out."""

    def __init__(self):
        self.expiry: dict[str, float] = {}
        self.lock = threading.Lock()

    def create(self) -> str:
        token = secrets.token_urlsafe(24)
        with self.lock:
            now = time.monotonic()
            self.expiry = {t: e for t, e in self.expiry.items() if e > now}
            self.expiry[token] = now + SESSION_SECONDS
        return token

    def valid(self, token: str | None) -> bool:
        with self.lock:
            return bool(token) and self.expiry.get(token, 0) > time.monotonic()

    def end(self, token: str | None) -> None:
        with self.lock:
            self.expiry.pop(token, None)


def shut_down(events_path: Path) -> None:
    """Power off a second after answering, so the page gets its reply first."""
    EventLog(events_path).record("", "", "shutdown requested from the Configuración page")

    def later():
        time.sleep(1)
        subprocess.run(["sudo", "-n", "systemctl", "poweroff"], check=False)

    threading.Thread(target=later, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    config_path: Path
    events_path: Path
    readings_path: Path
    sessions = Sessions()

    def session_token(self) -> str | None:
        morsel = SimpleCookie(self.headers.get("Cookie", "")).get(SESSION_COOKIE)
        return morsel.value if morsel else None

    def authenticated(self) -> bool:
        return self.sessions.valid(self.session_token())

    def current_status(self) -> dict:
        data = status(self.config_path, self.events_path, self.readings_path)
        data["authenticated"] = self.authenticated()
        return data

    def do_GET(self):
        url = urlsplit(self.path)
        path, query = url.path, parse_qs(url.query)
        archived = archive_folder(self.config_path, query.get("e", [""])[0])
        if path in FILES:
            name, content_type = FILES[path]
            self.send(HTTPStatus.OK, (WEB / name).read_bytes(), content_type)
        elif path == "/api/status":
            self.send_json(HTTPStatus.OK, self.current_status())
        elif path == "/api/series":
            self.send_json(HTTPStatus.OK, series(self.events_path, self.readings_path))
        elif path == "/api/archive":
            self.send_json(HTTPStatus.OK, archived_experiments(self.config_path))
        elif path == "/api/archive/experiment" and archived:
            self.send_json(HTTPStatus.OK, archived_info(archived))
        elif path == "/api/archive/series" and archived:
            self.send_json(HTTPStatus.OK, series(archived / "events.csv", archived / "readings.csv"))
        elif path in ("/descargar/eventos.csv", "/descargar/lecturas.csv"):
            folder = archived or self.events_path.parent
            experiment = archived.name if archived else load_config(self.config_path).experiment
            file = folder / ("events.csv" if "eventos" in path else "readings.csv")
            body = clean(file) if file.exists() else b""
            name = "-".join(filter(None, ["tenebrio", experiment, "eventos" if "eventos" in path else "lecturas",
                                          f"{datetime.now():%Y-%m-%d}"])) + ".csv"
            self.send(HTTPStatus.OK, body, "text/csv; charset=utf-8",
                      {"Content-Disposition": f'attachment; filename="{name}"'})
        elif path == "/descargar/archivo.zip":
            body = archive_zip(self.config_path, self.events_path, self.readings_path)
            self.send(HTTPStatus.OK, body, "application/zip",
                      {"Content-Disposition": f'attachment; filename="tenebrio-archivo-{datetime.now():%Y-%m-%d}.zip"'})
        else:
            self.send(HTTPStatus.NOT_FOUND, b"No encontrado", "text/plain; charset=utf-8")

    def do_POST(self):
        if self.path not in PROTECTED + ("/api/login", "/api/logout"):
            return self.send(HTTPStatus.NOT_FOUND, b"No encontrado", "text/plain; charset=utf-8")
        try:
            form = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path == "/api/login":
                return self.login(str(form.get("password", "")))
            if self.path == "/api/logout":
                self.sessions.end(self.session_token())
                return self.send_json(HTTPStatus.OK, self.current_status())
            if not self.authenticated():
                return self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "login_required"})
            if self.path == "/api/hotspot-only":
                set_hotspot_only(bool(form["on"]), self.events_path)
            elif self.path == "/api/password":
                if not check_password(str(form.get("current", ""))):
                    time.sleep(FAILED_LOGIN_DELAY)
                    return self.send_json(HTTPStatus.FORBIDDEN, {"error": "wrong_password"})
                set_password(str(form["new"]), self.events_path)
            elif self.path == "/api/shutdown":
                shut_down(self.events_path)
            elif self.path == "/api/archive":
                try:
                    start = datetime.fromisoformat(str(form["start"])).astimezone()
                except (KeyError, ValueError):
                    raise ConfigError("start")
                archive_experiment(str(form.get("archive_as", "")), str(form.get("new_name", "")), start,
                                   self.config_path, self.events_path, self.readings_path)
            else:
                save_config(config_from_form(form), self.config_path, self.events_path)
        except (ConfigError, ValueError, KeyError, TypeError) as error:
            return self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        except OSError as error:
            return self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(error)})
        self.send_json(HTTPStatus.OK, self.current_status())

    def login(self, password: str):
        if not check_password(password):
            time.sleep(FAILED_LOGIN_DELAY)  # slows guessing; the hotspot is the only way in
            return self.send_json(HTTPStatus.FORBIDDEN, {"error": "wrong_password"})
        token = self.sessions.create()
        data = status(self.config_path, self.events_path, self.readings_path)
        data["authenticated"] = True
        cookie = f"{SESSION_COOKIE}={token}; Path=/; Max-Age={SESSION_SECONDS}; HttpOnly; SameSite=Strict"
        self.send_json(HTTPStatus.OK, data, {"Set-Cookie": cookie})

    def send_json(self, code, data, headers: dict | None = None):
        self.send(code, json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode(),
                  "application/json; charset=utf-8", headers)

    def send(self, code, body: bytes, content_type: str, headers: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class DualStackServer(ThreadingHTTPServer):
    """Accepts IPv4 (phones on the hotspot) and IPv6 (the link-local Ethernet cable to the Mac)."""

    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--events", type=Path, default=Path("/var/lib/tenebrio-lights/events.csv"))
    parser.add_argument("--readings", type=Path, default=Path("/var/lib/tenebrio-lights/readings.csv"))
    parser.add_argument("--port", type=int, default=80)
    args = parser.parse_args()
    Handler.config_path, Handler.events_path, Handler.readings_path = args.config, args.events, args.readings
    DualStackServer(("::", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
