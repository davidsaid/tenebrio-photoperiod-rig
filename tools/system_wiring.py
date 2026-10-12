"""Draw the whole rig's wiring and write it to docs/system-wiring.pdf and .png.

One page: the 12 V light circuit through the relay HAT, and how each sensor and the clock
reach the Pi. The header pin numbers must match docs/sensor-wiring.pdf and SENSORS in
lights/tenebrio_logger.py. Run with a Python that has matplotlib:
    python tools/system_wiring.py
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

DOCS = Path(__file__).resolve().parent.parent / "docs"
RED, BLACK, BLUE, YELLOW, GREY, AMBER = "#c62828", "#212121", "#1565c0", "#f9a825", "#757575", "#e3a13a"
FILLS = {"pi": "#e8f5e9", "power": "#fff3e0", "box": "#f5f5f5", "sensor": "#e3f2fd", "dark": "#eceff1"}


def box(ax, x, y, w, h, title, fill, sub="", dashed=False):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.2,rounding_size=0.8",
                                fc=fill, ec=GREY, lw=1.0, ls="--" if dashed else "-"))
    ax.text(x + w / 2, y + h - 1.4, title, ha="center", va="top", fontsize=10.5, fontweight="bold")
    if sub:
        ax.text(x + w / 2, y + h - 3.6, sub, ha="center", va="top", fontsize=8.2, color=GREY, linespacing=1.3)


def wire(ax, points, colour, lw=2.2, dashed=False):
    xs, ys = zip(*points)
    ax.plot(xs, ys, color=colour, lw=lw, ls=(0, (4, 2)) if dashed else "-", solid_capstyle="round", zorder=3)


def terminal(ax, x, y, label, side="left"):
    ax.plot([x], [y], "o", ms=6, mfc="white", mec=BLACK, zorder=4)
    ax.text(x + (-0.9 if side == "left" else 0.9), y, label, ha="right" if side == "left" else "left",
            va="center", fontsize=8)


def bundle(ax, points):
    """A four-wire sensor cable, drawn as its blue and yellow signal pair."""
    wire(ax, [(x + 0.3, y + 0.3) for x, y in points], BLUE, lw=1.3)
    wire(ax, [(x - 0.3, y - 0.3) for x, y in points], YELLOW, lw=1.3)


def main() -> None:
    fig = plt.figure(figsize=(11.69, 8.27))  # A4 landscape
    fig.text(0.04, 0.955, "Tenebrio photoperiod rig · system wiring", fontsize=17, fontweight="bold", va="top")
    fig.text(0.04, 0.915, "The Pi switches the 12 V supply of each LED strip with a relay. "
             "Each sensor has its own cable to the Pi. Not to scale.", fontsize=9.5, color=GREY, va="top")
    ax = fig.add_axes([0.02, 0.13, 0.96, 0.76])
    ax.set_xlim(0, 100)
    ax.set_ylim(-2, 68)
    ax.axis("off")

    # Power side
    box(ax, 1, 4, 15, 11, "12 V adapter", FILLS["power"], "12 V DC, 2 A\nfrom the mains")
    box(ax, 20, 5, 9, 9, "DC jack", FILLS["power"], "5.5 × 2.1 mm\nscrew terminals")
    wire(ax, [(16.4, 9.5), (19.6, 9.5)], BLACK, lw=4)
    terminal(ax, 29.5, 11.5, "+", side="right")
    terminal(ax, 29.5, 7.5, "–", side="right")
    box(ax, 33, 23.5, 6, 6, "Wago", FILLS["box"], "+ 12 V")
    box(ax, 33, -1.5, 6, 6, "Wago", FILLS["box"], "– (GND)")
    wire(ax, [(29.5, 11.5), (31, 11.5), (31, 26.5), (32.8, 26.5)], RED)
    wire(ax, [(29.5, 7.5), (31, 7.5), (31, 1.5), (32.8, 1.5)], BLACK)

    # Pi and relay HAT
    box(ax, 42, 50, 24, 15, "Raspberry Pi 4", FILLS["pi"], "40-pin header · I2C bus 1\nsensor buses 5, 6, 7")
    box(ax, 42, 18, 24, 27, "52Pi relay HAT", FILLS["pi"], "sits on the Pi's header\nI2C address 0x10")
    ax.annotate("", xy=(54, 45.4), xytext=(54, 49.6), arrowprops=dict(arrowstyle="<->", color=GREY))
    relays = {"K1": 35.5, "K2": 27.5}
    for name, y in relays.items():
        ax.text(44.2, y, name, fontsize=9.5, fontweight="bold", va="center")
        terminal(ax, 48.5, y - 1.6, "COM", side="right")
        terminal(ax, 59.5, y - 1.6, "\"NC\" ★", side="left")
        ax.plot([48.9, 52.5], [y - 1.6, y + 0.2], color=BLACK, lw=1.4, zorder=4)
        wire(ax, [(39.2, 26.5), (40.5, 26.5), (40.5, y - 1.6), (48.2, y - 1.6)], RED)
    ax.text(54, 20.2, "K3, K4: not used", fontsize=8, color=GREY, ha="center")

    # Enclosures: the strip's pair enters on the left, the sensor cable below it.
    enclosures = [  # name, base y, sensor pins, relay, x of its wires: sensor cable, red, black
        ("Treatment 1", 50, "pins 13–16 · bus 6", "K1", 68, 73),
        ("Treatment 2", 29, "pins 37–40 · bus 7", "K2", 69.5, 74.5),
        ("Control", 8, "pins 31–34 · bus 5", None, 71, None),
    ]
    for i, (name, y, pins, relay, cable_x, red_x) in enumerate(enclosures):
        lit = relay is not None
        box(ax, 79, y - 5, 19, 17, name, FILLS["box"] if lit else FILLS["dark"],
            f"sensor: {pins}" + ("" if lit else "\nno LEDs: always dark"))
        sensor_y = y - 2
        ax.add_patch(FancyBboxPatch((89.6, sensor_y - 1.6), 6.4, 3.2, boxstyle="round,pad=0.1,rounding_size=0.4",
                                    fc=FILLS["sensor"], ec=BLUE, lw=0.9, zorder=5))
        ax.text(92.8, sensor_y, "SHT45", fontsize=7.5, ha="center", va="center", zorder=6)
        bundle(ax, [(66.2, 62 - i * 2.5), (cable_x, 62 - i * 2.5), (cable_x, sensor_y), (89.4, sensor_y)])
        if lit:
            strip_y = y + 2.5
            ax.plot([81, 89], [strip_y, strip_y], color=AMBER, lw=6, solid_capstyle="butt", zorder=4)
            ax.text(85, strip_y + 1.5, "COB LED strip", fontsize=7.5, ha="center")
            relay_y = relays[relay] - 1.6
            wire(ax, [(59.9, relay_y), (red_x, relay_y), (red_x, strip_y + 0.3), (81, strip_y + 0.3)], RED)
            wire(ax, [(81, strip_y - 0.3), (76.5 + i * 1.2, strip_y - 0.3), (76.5 + i * 1.2, 1.5), (39.2, 1.5)], BLACK)

    # Clock and room sensor
    box(ax, 18, 47, 16, 9, "Clock (RTC)", FILLS["sensor"], "PCF8523 · 0x68\npins 1, 3, 5, 9")
    box(ax, 18, 58, 16, 7, "Room sensor", FILLS["sensor"], "SHT45 · 0x44 · Qwiic")
    bundle(ax, [(34.2, 51.5), (41.8, 51.5)])
    ax.text(38, 49.2, "bus 1", fontsize=8, color=BLUE, ha="center")
    wire(ax, [(26, 56.2), (26, 57.8)], BLUE, lw=1.4)
    wire(ax, [(26.7, 56.2), (26.7, 57.8)], YELLOW, lw=1.4)

    # Legend
    legend = [(RED, "+12 V (switched to the strip by the relay)"), (BLACK, "– 12 V return (GND)"),
              (BLUE, "I2C SDA, in a 4-wire sensor cable"), (YELLOW, "I2C SCL, in the same cable")]
    for i, (colour, text) in enumerate(legend):
        ax.plot([1, 5], [44 - i * 3.2] * 2, color=colour, lw=2.4)
        ax.text(6, 44 - i * 3.2, text, fontsize=8.5, va="center")

    notes = ("★ On our relay HAT the screw labels are swapped: the screw printed \"NC\" is the real normally-open contact. "
             "Test yours with a multimeter before\n   you connect the strips. With the Pi off, COM must NOT connect to the screw you use. "
             "Then a Pi that is off or starting keeps every light off.\n"
             "• Each sensor cable carries power, GND, SDA and SCL. The pin blocks are on docs/sensor-wiring.pdf. "
             "Never use the 5 V pins (2 and 4) for a sensor.\n"
             "• Use the same strip type and the same length in every lit enclosure. Cover the sensor's green power LED "
             "with opaque tape, or the dark phase is not dark.")
    fig.text(0.04, 0.115, notes, fontsize=8.5, va="top", linespacing=1.55)
    fig.text(0.96, 0.02, "tenebrio-photoperiod-rig · system wiring · 2026-10-12", fontsize=7.5, color=GREY, ha="right")
    for suffix in ("pdf", "png"):
        fig.savefig(DOCS / f"system-wiring.{suffix}", dpi=160)
    print(f"wrote {DOCS / 'system-wiring.pdf'} and .png")


if __name__ == "__main__":
    main()
