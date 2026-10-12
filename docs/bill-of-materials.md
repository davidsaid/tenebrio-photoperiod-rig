# Bill of materials

These are the parts for one rig: two lit enclosures, one dark control enclosure and a
room sensor. Prices are what we paid on Amazon Spain in October 2026. They are a guide
only. Any part with the same specification works.

## Electronics

| # | Part | Qty | What it does | Our example | Price |
|---|------|-----|--------------|-------------|-------|
| 1 | Raspberry Pi 4 Model B, 4 GB | 1 | Runs the light control, the logger and the website | Starter kit with the official 15 W USB-C supply, official case and 64 GB microSD card ([B0D1N3V2FF](https://www.amazon.es/dp/B0D1N3V2FF)) | €147.24 (kit) |
| 2 | 52Pi DockerPi 4-channel relay HAT (EP-0099) | 1 | Switches the 12 V supply of each LED strip. It sits on the Pi's header and uses I2C address 0x10 | [B07Q2P9D7K](https://www.amazon.es/dp/B07Q2P9D7K) | €15.24 |
| 3 | Adafruit PCF8523 real-time clock, STEMMA QT | 1 | Keeps the time when there is no network. The schedule depends on it | [B09G49X41M](https://www.amazon.es/dp/B09G49X41M) | €16.11 |
| 4 | CR1220 coin cell | 1 | Battery for the clock | Pack of 10 ([B07PB12MKK](https://www.amazon.es/dp/B07PB12MKK)) | €6.99 |
| 5 | SHT45 temperature and humidity module, with STEMMA QT / Qwiic connectors | 4 | One in each enclosure and one for the room. Address 0x44, fixed | Aideepen SHT45 ([B0H1WW65ZP](https://www.amazon.es/dp/B0H1WW65ZP)) | not recorded |
| 6 | Qwiic / STEMMA QT cable kit with JST-SH to female Dupont cables | 1 | Connects each sensor to the Pi's header, and the room sensor to the clock | elechawk, 12 cables ([B08HQ1VSVL](https://www.amazon.es/dp/B08HQ1VSVL)) | €10.99 |
| 7 | Female-to-female Dupont jumper wires | ~16 | Make the sensor cables long enough to reach the enclosures | Any | — |

## Light circuit (12 V)

| # | Part | Qty | What it does | Our example | Price |
|---|------|-----|--------------|-------------|-------|
| 8 | 12 V COB LED strip, 4000 K, CRI 90, 8 mm wide | 1 reel (5 m) | The light in each lit enclosure | [B0BMTQZ1BV](https://www.amazon.es/dp/B0BMTQZ1BV) | €16.24 |
| 9 | 8 mm 2-pin COB strip connectors with leads | 1 pack | Connect a wire to a cut piece of strip without soldering | Pack of 20 ([B0FRZD1FYF](https://www.amazon.es/dp/B0FRZD1FYF)) | €8.69 |
| 10 | 12 V DC power adapter, 2 A, 5.5 × 2.1 mm plug | 1 | Powers the LED strips | [B0DKTGDW2L](https://www.amazon.es/dp/B0DKTGDW2L) | €9.50 |
| 11 | DC jack with screw terminals, 5.5 × 2.1 mm | 1 pack | Connects the adapter plug to the wires | 4 pairs ([B0CH4HBFHB](https://www.amazon.es/dp/B0CH4HBFHB)) | €6.49 |
| 12 | Two-core cable, 2 × 0.5 mm², red and black | 10 m | Carries 12 V from the relays to each strip | [B0FJSJ7TQM](https://www.amazon.es/dp/B0FJSJ7TQM) | €6.91 |
| 13 | Wago 221-413 lever connectors | 1 pack | Join the + wires to the relays and the – wires to the adapter | Pack of 10 ([B01AKF3I58](https://www.amazon.es/dp/B01AKF3I58)) | €7.99 |

## Enclosures

You also need one opaque enclosure per treatment and one for the control. Use the same
model for all of them. Cut the same length of LED strip for every lit enclosure, and write
the length down: it sets the light intensity and the heat.

## Tools

- Multimeter with a continuity test. You need it to check the relay contacts (see
  [wiring.md](wiring.md)).
- Wire stripper and a small flat screwdriver.
- Scissors for the LED strip. Cut only at the marked cut points.
- Opaque tape, to cover the green power LED on each sensor.
- Labels or a marker for both ends of every cable.
- A computer with an SD card reader, to install the Pi's software.

## Notes

- **The relay HAT keeps the lights off when the Pi is off,** provided you use the right
  screw on each relay. On our board the screw labels were wrong. Check yours before you
  connect the strips ([wiring.md](wiring.md), step 5).
- **The sensors have a fixed address,** so each one needs its own I2C bus. The Pi 4 has
  enough free pins for three enclosure sensors. The room sensor shares the clock's bus.
- **The official Pi power supply matters.** A weak USB-C supply causes under-voltage. The
  website shows a warning when that happens.
