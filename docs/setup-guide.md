# Setup guide

This guide installs the software on a new Raspberry Pi. It takes about one hour. You need
a computer (Linux, macOS or Windows with WSL) on the same Wi-Fi network as the Pi.

Do the wiring in [wiring.md](wiring.md) at step 4, when the guide tells you to.

## 1. Prepare the SD card

1. Install [Raspberry Pi Imager](https://www.raspberrypi.com/software/) on your computer.
2. Choose **Raspberry Pi 4** and **Raspberry Pi OS Lite (64-bit)**.
3. In the customisation settings, set:
   - hostname: `tenebrio`
   - username: `pi`
   - your Wi-Fi network and its password
   - your time zone
   - SSH: on, with your public key (or a password)
4. Write the card, put it in the Pi and connect the USB-C power.

After 2–3 minutes, connect from your computer:

    ssh pi@tenebrio.local

## 2. Install the packages

    sudo apt update
    sudo apt install -y python3-yaml python3-smbus2 i2c-tools

The software needs passwordless `sudo` for the user `pi` (the website's shutdown button
and `deploy.sh` use it). Raspberry Pi OS sets this up for the user that Imager creates.
Check it: `sudo -n true` must print nothing.

## 3. Turn on the I2C buses and the sensor power

Open the boot configuration:

    sudo nano /boot/firmware/config.txt

Find the line `dtparam=i2c_arm=on` and remove the `#` in front of it. Then add these lines
at the end of the file, under `[all]`:

    dtoverlay=i2c-rtc,pcf8523
    dtoverlay=i2c6,pins_22_23,baudrate=20000
    dtoverlay=i2c5,pins_12_13,baudrate=20000
    dtoverlay=i2c-gpio,bus=7,i2c_gpio_sda=20,i2c_gpio_scl=21,i2c_gpio_delay_us=20
    gpio=27,6,26=op,dh

| Line | What it does |
|------|--------------|
| `i2c-rtc,pcf8523` | The clock module sets the time at boot |
| `i2c6`, `i2c5` | Two extra I2C buses, for the treatment 1 and control sensors |
| `i2c-gpio,bus=7` | A third extra bus, for the treatment 2 sensor |
| `baudrate=20000`, `delay_us=20` | A slow bus speed, so long cables work |
| `gpio=27,6,26=op,dh` | Switches on the three sensor power pins at boot |

Save the file (Ctrl+O, Enter, Ctrl+X). Set the Wi-Fi country, then shut down:

    sudo raspi-config nonint do_wifi_country ES   # use your country code
    sudo poweroff

## 4. Connect the hardware

Unplug the Pi. Follow [wiring.md](wiring.md), steps 1 to 4 and step 5 up to the relay test.
Then plug the Pi in again.

## 5. Install the rig software

On your computer, download this repository:

    git clone https://github.com/davidsaid/tenebrio-photoperiod-rig.git
    cd tenebrio-photoperiod-rig/lights

Edit `config.yaml` if you want: the experiment name, the start, and each treatment's light
and dark hours. You can also change all of this later on the website. Then install:

    ./deploy.sh pi@tenebrio.local

`deploy.sh` copies the software to `/opt/tenebrio-lights` on the Pi and starts four
services:

| Service | What it does |
|---------|--------------|
| `tenebrio-lights` | Switches the relays from the schedule |
| `tenebrio-logger` | Records temperature and humidity every 5 minutes |
| `tenebrio-web` | Serves the website |
| `tenebrio-network` | Uses your Wi-Fi when it is there, else starts the Pi's own hotspot |

The live settings and all data are in `/var/lib/tenebrio-lights/` on the Pi.

## 6. Set up the Wi-Fi and the hotspot

The lab may have no Wi-Fi. Then the Pi starts its own Wi-Fi network (a hotspot) called
`tenebrio`, and you open the website from your phone.

Do these three steps in this order. The network service is already running, and the order
keeps your SSH connection alive.

First, create your normal Wi-Fi again with the name `home-wifi`. The network service
connects to it; it must not connect by itself:

    sudo nmcli connection add type wifi ifname wlan0 con-name home-wifi ssid 'YOUR-WIFI' \
      wifi-sec.key-mgmt wpa-psk wifi-sec.psk 'YOUR-WIFI-PASSWORD' connection.autoconnect no

Within a minute the service switches the Pi to `home-wifi`. Your SSH session can freeze
for a moment; if it drops, connect again with `ssh pi@tenebrio.local`.

Second, create the hotspot. Choose a password of at least 8 characters and write it down:

    sudo nmcli connection add type wifi ifname wlan0 con-name tenebrio-hotspot ssid tenebrio \
      802-11-wireless.mode ap 802-11-wireless.band bg 802-11-wireless.channel 6 \
      ipv4.method shared wifi-sec.key-mgmt wpa-psk wifi-sec.psk 'CHOOSE-A-PASSWORD' \
      connection.autoconnect yes connection.autoconnect-priority 20

Third, delete the Wi-Fi connection that Imager created, so that it cannot connect by
itself. List the connections:

    nmcli connection show

Delete the Wi-Fi connection that is not `home-wifi` or `tenebrio-hotspot` (its name often
starts with `netplan-` or is `preconfigured`):

    sudo nmcli connection delete 'NAME-FROM-THE-LIST'

## 7. Set the clock

Leave the Pi on your Wi-Fi for at least 15 minutes. It gets the correct time from the
internet and writes it into the clock module. From then on, the clock module keeps the
time when there is no network.

**The lights only follow the schedule when the time is trusted.** At boot without a
network, the time comes from the clock module. If the coin cell is empty, the lights stay
off and the website shows an alert.

## 8. Check

Open `http://tenebrio.local` in a browser on the same Wi-Fi.

- The home page shows the temperature and humidity of every sensor.
- **System** (Sistema) shows the clock as "Internet time" and the power as "OK".
- Do the checks in [wiring.md](wiring.md), step 6.
- Finish the light circuit: [wiring.md](wiring.md), step 5, items 5 to 7.

Then try the lab setup at home. On **Settings** (Configuración), switch on "Use the hotspot
only". After about 30 seconds, join the `tenebrio` Wi-Fi with your phone and open
`http://10.42.0.1`. Switch it off again on the same page.

## 9. Before the real experiment

1. On **Settings**, change the settings password. It starts as `tenebrio`.
2. Archive the test data: **Settings → Archive the experiment**. Give the real experiment
   its name and start time.
3. Check each treatment's light and dark hours on **Settings**.
4. Check that the sensor LEDs are covered and the cables are labelled.

See [using-the-website.md](using-the-website.md) for the daily use.

## Updating the software

Download the new version on your computer and run `./deploy.sh pi@tenebrio.local` again.
Your settings and data stay. The lights do not blink during the update.

To run the tests on your computer: `python3 -m pytest` in `lights/` (needs `pytest` and
`pyyaml`).
