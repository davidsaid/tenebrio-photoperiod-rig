# Using the website

Open the website from a phone or a computer:

- on the Pi's hotspot (Wi-Fi `tenebrio`): `http://10.42.0.1`
- on the same Wi-Fi as the Pi: `http://tenebrio.local`

The website is in Spanish or English. Tap **ES** or **EN** at the top. Each phone
remembers its choice.

## Home (Inicio)

- **Summary:** the experiment's name and day, the number of power cuts since the start,
  and the temperature and humidity of each enclosure and the room now.
- **One card per enclosure:** is the light on, when it switches next, and charts of
  temperature and humidity. The shaded bands are the times the light was really off. The
  dashed line is the room.
- **Slider:** how much time the charts show, from the last 6 hours to the whole
  experiment.
- **Events on the charts:** vertical lines for logged events. Light switches are on by
  default: amber is "light on", dashed blue is "light off". Tick other kinds (power cuts,
  settings changes, clock, sensors, network) to see them too. A treatment's card shows its
  own light switches. The control card shows the switches of every treatment, with the
  treatment number on each line.
- **Tap a chart** to see the values at that moment, and the events near it.
- **Download data:** the readings and the events of the running experiment, as CSV files.

## System (Sistema)

Read-only. The Pi's clock, processor temperature, power and network. Below that:

- **Archived experiments:** tap one to see its settings and charts. The ZIP button
  downloads every experiment, archived and running, in one file.
- **Power cuts** and the full **event log**.

## Settings (Configuración)

Settings need the settings password. It starts as `tenebrio`. Change it in the section
"Settings password".

- **Light schedule:** the experiment name, the start, and the light and dark hours of
  each treatment. Every cycle starts with light at the start time. Before the start, all
  lights are off. Tap "Review changes", check the list, then "Save".
- **Archive the experiment:** ends the running experiment and starts a new one. The old
  data moves to a read-only folder and stays on the System page. The new experiment keeps
  the same light and dark hours. Its lights are off until its start time. Use this to
  separate practice runs from the real experiment.
- **Network:** "Use the hotspot only" makes the Pi use only its own Wi-Fi, also after a
  restart. Use it to try the lab setup at home.
- **Shut down the Pi:** always shut down before you unplug the Pi. Wait until the
  sensor LEDs go off, then unplug the USB-C cable.
- **Appearance:** light or dark colours, for this phone only. No password needed.

## The files

Readings (`readings.csv`), one row every 5 minutes:

    timestamp,clock,temp_c_1,rh_1,temp_c_2,rh_2,temp_c_control,rh_control,temp_c_room,rh_room,cpu_temp_c,throttled

- `temp_c_*` is the temperature in °C and `rh_*` the relative humidity in %, for
  treatment 1, treatment 2, the control and the room. An empty cell means the sensor did
  not answer.
- `clock` says where the time came from: `NTP` (internet) or `RTC` (clock module).
- `cpu_temp_c` and `throttled` describe the Pi's own health.

Events (`events.csv`), one row per event:

    timestamp,treatment,state,event

A light switch has a treatment number and the state `light` or `dark`. Other events, like
power cuts and settings changes, have only a text in `event`.
