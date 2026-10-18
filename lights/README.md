# Light controller and readings logger

Switches each treatment's LED strip through the relay HAT at the times of day set in
`config.yaml`. It runs on the Pi as the systemd service `tenebrio-lights`.
A separate service, `tenebrio-logger`, records periodic readings and reports outages,
and `tenebrio-web` serves a monitoring site; see "Readings" and "Monitoring site" below.

## Configure

The live schedule on the Pi is `/var/lib/tenebrio-lights/config.yaml`. Change it from the
monitoring page, or from this folder with `./deploy.sh --push-config`. `config.yaml` here
is the template that seeds it. Each treatment (1–4, matching relays K1–K4) has a
`light_on` and a `light_off` time of day, written as quoted `"HH:MM"` (24-hour clock).
A `light_off` earlier than `light_on` means the light period crosses midnight, so
`"07:00"` to `"03:00"` gives 20 h of light and 4 h of dark. The two times must differ;
a treatment left out keeps its light off. Before the shared `start`, all lights are off;
from `start` on, each light follows its times at once. The keys are not `on`/`off`,
because YAML 1.1 reads those as true and false. `experiment` names the running experiment (letters, digits, `.`, `_`, `-`); it
becomes the folder name when the experiment is archived (see "Experiments and the
archive").

The controller reloads the file within a second of it changing; no restart is needed. A
file that fails validation is rejected, the previous schedule stays in force, and both
outcomes go in the event log.

Check a config without touching the Pi:

    python3 tenebrio_lights.py --check

## Deploy

    ./deploy.sh                 # code only; the live schedule is left alone
    ./deploy.sh --push-config   # also replace the live schedule with config.yaml
    ./deploy.sh pi@tenebrio.local   # another host than the `tenebrio` SSH alias

This copies the folder to `/opt/tenebrio-lights` on the Pi (`ssh tenebrio`) and restarts
the five services, and installs the NetworkManager setting that turns Wi-Fi power saving
off. It prints any difference between the live schedule and `config.yaml`,
so that a change made on the phone is noticed before it is overwritten. Restarting does
not blink the lights.

## Behaviour that matters for the experiment

- **The state comes from the clock alone.** The controller saves nothing between runs, so
  after a crash, a reboot or a power cut every light returns to its scheduled state
  within a second.
- **No switching on an untrusted clock.** The clock is trusted when systemd-timesyncd has
  synchronized it over the network (`/run/systemd/timesync/synchronized`), or when the
  PCF8523 RTC set it at boot (`/sys/class/rtc/rtc0/hctosys` is 1), holds a year from
  2026 to 2040, and still agrees with the system clock: within 60 s plus 100 ppm of the
  uptime, since offline the two clocks drift apart a few seconds a day. Otherwise every light
  is held off and the event log says so. Once the network is back, the kernel copies NTP
  time into the RTC (`CONFIG_RTC_SYSTOHC`), so the RTC stays correct.
- **Local wall-clock time.** The times are read on the Pi's local clock, so dark periods
  stay in the local night (the experimenters' choice, 2026-10-17). On the night daylight
  saving time ends (2026-10-25, 03:00 becomes 02:00), a dark period that spans 02:00–03:00
  lasts an hour longer; in spring, it is an hour shorter.
- **Configs from before 2026-10-17** set `light` and `dark` durations from `start`. The
  controller still reads one with a 24-hour cycle, as `light_on` at the start's time of
  day; the page writes it back in the new form on the next save. Archived configs are
  shown as they were written.
- Each relay is re-asserted every 60 s, in case the HAT ever resets.
- Switching can lag the scheduled instant by up to 0.25 s (the poll interval).

## Event log

On the Pi, `/var/lib/tenebrio-lights/events.csv` gets one row per switch and per clock
event: `timestamp,treatment,state,event`. Each row is forced onto the SD card (fsync) as
it is written, so a power cut loses at most the row being written. A service that finds
the file missing (just archived) starts a new one with its header; one created by the
network service, which runs as root, is handed to `pi`.

## Experiments and the archive

Each experiment has a name, set on Configuración (David, 2026-10-12: dry runs must not mix
with the real experiment). Archiving, also on Configuración, closes the running
experiment: `readings.csv`, `events.csv` (ending with an `experiment archived` row), any
older `readings-until-*.csv` and a copy of `config.yaml` with an `archived:` time move to
`/var/lib/tenebrio-lights/archive/<name>/`, and the folder and its files become
read-only. The services open their files for every row, so their next rows start new
files. The next experiment keeps the photoperiods and gets its own name and start; its
lights stay off until then. Sistema lists the archived experiments; each opens on the
Archivo page (`/archivo?e=<name>`) with the same charts as Inicio, and its own CSV
downloads. Sistema also offers the whole archive as one ZIP, with the running experiment
under `running/<name>/`.

## Readings

`tenebrio_logger.py` writes one row every `readings_interval` (in `config.yaml`, default
`"00:05:00"`) to `/var/lib/tenebrio-lights/readings.csv`, aligned to multiples of the
interval:

    timestamp,clock,temp_c_1,rh_1,temp_c_2,rh_2,temp_c_control,rh_control,temp_c_room,rh_room,cpu_temp_c,throttled

- `clock`: whether the time came from `NTP` or the `RTC`. No row is written while the
  clock is untrusted, so every timestamp is real.
- `temp_c_*`, `rh_*`: each SHT45's temperature (°C) and relative humidity (%), for
  treatments 1 and 2, the control enclosure and the room. A sensor that doesn't answer, or
  whose reading fails its checksum three times, leaves its two cells empty; the event log
  records when it stops and when it reads again.
- `cpu_temp_c`: the processor's own sensor. It runs 15–25 °C above the room, so it shows
  the Pi's health, not the enclosure climate.
- `throttled`: `vcgencmd get_throttled`. Bit 0 (0x1) means under-voltage now; bit 16
  (0x10000) means under-voltage has occurred since boot, a sign of a weak supply.

When the columns change, the logger moves the old file aside as
`readings-until-YYYYMMDD-HHMM.csv` and logs it, so each file has a single layout.
It runs as its own service so that a hung sensor can never delay light switching.

## Sensors

The SHT45's I2C address (0x44) is fixed in the chip, so each enclosure sensor has an I2C bus
of its own, which also keeps a fault on a long cable away from the relay and RTC bus (bus 1,
pins 1, 3, 5 and 9, unchanged). The room sensor needs no long cable, so it plugs into the
RTC board's spare Qwiic connector and shares bus 1 (David, 2026-10-11). `SENSORS` in
`tenebrio_logger.py` maps names to buses.

Each enclosure sensor's cable ends in its own 2×2 block of header pins: power, ground, SDA
and SCL. The Pi has only two 3.3 V pins and the RTC uses one, so each is powered from a GPIO
driven high from boot (`gpio=...=op,dh` in `config.txt`); an SHT45 board draws a few mA,
well within a GPIO's drive. In the cables, red is power, black ground, blue SDA and yellow
SCL (Qwiic colours). On each sensor board use VIN, not 3Vo.

| Sensor | Bus | Pins | Power (red) | GND (black) | SDA (blue) | SCL (yellow) |
|---|---|---|---|---|---|---|
| Treatment 1 | `i2c-6`, hardware (`dtoverlay=i2c6,pins_22_23`) | 13–16 | pin 13 (GPIO 27) | pin 14 | pin 15 (GPIO 22) | pin 16 (GPIO 23) |
| Control | `i2c-5`, hardware (`dtoverlay=i2c5,pins_12_13`) | 31–34 | pin 31 (GPIO 6) | pin 34 | pin 32 (GPIO 12) | pin 33 (GPIO 13) |
| Treatment 2 | `i2c-7`, software (`dtoverlay=i2c-gpio,bus=7,...`) | 37–40 | pin 37 (GPIO 26) | pin 39 | pin 38 (GPIO 20) | pin 40 (GPIO 21) |
| Room | `i2c-1`, chained off the RTC board | — | | | | |

The buses run slowly because the cables are about 1 m of chained Dupont jumpers; a reading
every 5 minutes needs almost no bandwidth.

## Outages

The readings double as a heartbeat. When the logger starts and its last row is older than
1.5 intervals, it adds a row to the event log, for example
`outage: last reading 18:50, system booted 19:30, readings resumed 19:31 (41 min without
readings)`. The power-off time is then known to within one interval, and the return time
almost exactly. The controller logs `controller stopped` on a clean stop (deploy,
shutdown), so a `controller started` with no `controller stopped` before it marks a power
cut or crash.

The Pi cannot see the 12 V side: if only the LED adapter loses power, the strips go dark
while the log says light. Light sensors in the enclosures would close that gap.

## Monitoring site

`tenebrio_web.py` serves the site on port 80: `http://10.42.0.1` from a phone on the
`tenebrio` hotspot. It uses system fonts and draws its charts itself, since the hotspot has
no internet. Files are in `web/`: four pages sharing `app.css`, `app.js` and `i18n.js`.

- **Inicio** (`/`): a summary (day of the experiment, outages since the start, each
  enclosure's light, temperature and humidity now), then one card per treatment and the
  control with temperature and humidity charts, and the CSV downloads. The charts shade
  the times the light was actually off, from the controller's event log rather than the
  schedule, and draw the room as a dashed reference line. All cards share one y scale, so
  they can be compared by eye. A slider sets the window, from 48 h by default up to the
  whole experiment; tapping a chart shows the values at that moment. Hardware appears only
  as alerts: untrusted clock, under-voltage, no recent readings, a silent sensor, or the
  latest power cut since the start, if it was in the last three days. Below the slider,
  "Eventos en las gráficas" draws logged events as vertical lines: light switches by
  default (on in amber, off dashed in blue), and any other kind present in the log (power
  cuts, settings, clock, light control, sensors, network, experiment), chosen per phone.
  A treatment's card shows its own switches; the control card shows every treatment's,
  each labelled with its number. Tapping near a line names the event.
- **Sistema** (`/sistema`), read-only: clock source, CPU temperature, power flags, network,
  archived experiments with the ZIP download, outages and the event log.
- **Archivo** (`/archivo?e=<name>`), read-only: one archived experiment, its settings and
  its charts.
- **Configuración** (`/configuracion`), behind the settings password: the experiment's
  name and schedule (start and each treatment's on and off times, with the photoperiod they give), archiving the experiment, the "solo punto de acceso" switch (see Network), changing
  the password, and shutting the Pi down. A schedule save is checked twice, in the page and
  on the server with the controller's own `load_config`; the file is replaced atomically,
  and an event-log row records the old and new schedule.

**Password.** The settings password keeps visitors to the lab from changing anything by
accident (David, 2026-10-11). It starts as `tenebrio` and is changed on the Configuración
page; the new one is stored as a salted PBKDF2 hash in
`/var/lib/tenebrio-lights/settings-password`. Deleting that file restores `tenebrio`.
Logging in sets an HttpOnly cookie for 12 hours; sessions live in memory, so restarting
`tenebrio-web` logs everyone out. A wrong password costs a 1 s delay. The site is plain
HTTP on the hotspot, whose Wi-Fi password remains the outer protection.

**Shutting down.** There is no button on the Pi, so Configuración has one. It logs
`shutdown requested from the Configuración page` and runs `sudo -n systemctl poweroff`
(the `pi` user has passwordless sudo). The relay HAT keeps its relays as they were until
the Pi is unplugged, since the 5 V rail stays powered after a halt.

**Language.** Spanish or English, chosen per phone with the ES/EN flags at the end of the
menu and remembered in that browser (on phones the "Tenebrio molitor" name is hidden to
make room); a new phone starts in its system language (Spanish if
Spanish, otherwise English). Every visible string is in `web/i18n.js`. The CSV files keep
their English column names.

**Appearance.** Light or dark follows the phone unless changed under Apariencia on
Configuración, which needs no password and is remembered per browser
(`tenebrio-theme`). The password boxes have an eye button to show what was typed.

APIs: `GET /api/status`, `GET /api/series` (every reading as columns, each treatment's
light-on periods, and the events to draw), `GET /api/archive` (the archived experiments),
`GET /api/archive/experiment?e=<name>` and `/api/archive/series?e=<name>`, `POST
/api/login`, `/api/logout`, and, with the login cookie, `/api/config`, `/api/archive`
(`archive_as`, `new_name`, `start`), `/api/hotspot-only`, `/api/password`,
`/api/shutdown`. Downloads: `/descargar/lecturas.csv` and `/descargar/eventos.csv` (add
`?e=<name>` for an archived experiment) and `/descargar/archivo.zip`.

## Network

`tenebrio_network.py` (service `tenebrio-network`, runs as root) chooses the Wi-Fi on
the single built-in radio. It uses home Wi-Fi (`home-wifi`) when it connects, and
otherwise the `tenebrio` hotspot. From the hotspot it retries home Wi-Fi every 30 minutes,
never while a phone is connected. Every change is logged as `network: ...`.

It also checks the active network every 30 s: on home Wi-Fi the router must answer a ping;
on the hotspot the radio must be an access point holding 10.42.0.1. After 3 failed checks
in a row it repairs, one step at a time while the checks keep failing: reconnect, reload
the Wi-Fi driver (`brcmfmac`), and as a last resort restart the Pi, but only while every
light is scheduled dark, and at most once per 6 h
(`/var/lib/tenebrio-lights/network-restart` holds the time of the last one). Each step is
logged. Wi-Fi power saving is off (`tenebrio-wifi-powersave.conf`, installed by
`deploy.sh` in `/etc/NetworkManager/conf.d/`): on 2026-10-17 the Pi stayed associated
with home Wi-Fi overnight but stopped answering, so the website was unreachable and the
hotspot fallback never started.

## Light check

`tenebrio_validator.py` (service `tenebrio-validator`, runs as root) reads each relay back
from the HAT every 15 s and compares it with the schedule, computed as the controller does
(all off while the clock is not trusted). A mismatch, or a HAT that does not answer, that
lasts 90 s is logged as `controller check FAILED: ...` and the Pi restarts (2026-10-18:
a frozen controller would otherwise hold a wrong light for hours in the unattended lab).
90 s is longer than the controller's 60 s re-assert, so faults it corrects by itself and
the instant of a switch never cause a restart. At most one restart per 6 h
(`/var/lib/tenebrio-lights/light-check-restart`); after that the error is logged once and
the Pi is left running. It reads the relays only: it cannot see whether the LEDs light.
Tested on the Pi on 2026-10-18 by stopping the controller and switching on unused relay 3:
the error was logged after 97 s and the Pi restarted with every relay correct.

The kernel's hardware watchdog (`bcm2835-wdt`, fed by systemd every minute,
`RuntimeWatchdogSec=1min`) restarts the Pi if the whole system freezes.

The "solo punto de acceso" switch on the Configuración page creates `/var/lib/tenebrio-lights/hotspot-only`.
While that file exists the Pi uses only the hotspot, across reboots, so the lab setup can
be tried at home; clearing the switch retries home Wi-Fi at once. The page is at
`http://10.42.0.1` on the hotspot and `http://tenebrio.local` on home Wi-Fi.

In the lab there is no network, so the clock runs on the RTC alone (see Behaviour).

## Test

    python3 -m pytest   # needs pytest and pyyaml; covers the controller, logger and web server
