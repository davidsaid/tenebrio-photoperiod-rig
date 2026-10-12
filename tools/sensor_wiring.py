"""Draw the SHT45 sensor wiring reference and write it to docs/sensor-wiring.pdf and .png.

The pin assignment must match SENSORS in lights/tenebrio_logger.py and the "Sensors"
section of lights/README.md. Run with a Python that has matplotlib:
    python tools/sensor_wiring.py
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyBboxPatch

OUT = Path(__file__).resolve().parent.parent / "docs" / "sensor-wiring.pdf"
RED, BLACK, BLUE, YELLOW, GREY, LIGHT = "#c62828", "#212121", "#1565c0", "#f9a825", "#757575", "#eeeeee"
WIRE_COLOURS = {"power": RED, "GND": BLACK, "SDA": BLUE, "SCL": YELLOW}

# Physical pin -> (label, role) for every pin the rig uses.
PINS = {
    1: ("RTC 3.3 V", "rtc"), 3: ("RTC SDA", "rtc"), 5: ("RTC SCL", "rtc"), 9: ("RTC GND", "rtc"),
    2: ("5 V — never", "danger"), 4: ("5 V — never", "danger"),
    13: ("T1 power", "power"), 14: ("T1 GND", "GND"), 15: ("T1 SDA", "SDA"), 16: ("T1 SCL", "SCL"),
    31: ("Control power", "power"), 32: ("Control SDA", "SDA"), 33: ("Control SCL", "SCL"), 34: ("Control GND", "GND"),
    37: ("T2 power", "power"), 38: ("T2 SDA", "SDA"), 39: ("T2 GND", "GND"), 40: ("T2 SCL", "SCL"),
}
BLOCKS = [("Treatment 1", 13, 16, "#fff8e1"), ("Control", 31, 34, "#e8f5e9"), ("Treatment 2", 37, 40, "#e3f2fd"),
          ("RTC + room sensor", 1, 10, "#f5f5f5")]
ROWS = [  # sensor, power, GND, SDA, SCL, bus
    ("Treatment 1", "13", "14", "15", "16", "i2c-6 (hardware)"),
    ("Control", "31", "34", "32", "33", "i2c-5 (hardware)"),
    ("Treatment 2", "37", "39", "38", "40", "i2c-7 (software)"),
    ("Room", "—", "—", "—", "—", "i2c-1 (via RTC)"),
]


def pin_xy(pin: int) -> tuple[float, float]:
    row = (pin - 1) // 2
    return (0.0 if pin % 2 else 1.0), -row


def draw_header(ax):
    for name, first, last, fill in BLOCKS:
        top, bottom = (first - 1) // 2, (last - 1) // 2
        ax.add_patch(FancyBboxPatch((-0.38, -bottom - 0.42), 1.76, bottom - top + 0.84,
                                    boxstyle="round,pad=0.02,rounding_size=0.15", fc=fill, ec=GREY, lw=0.8))
        ax.text(-2.75 if name != "RTC + room sensor" else -2.75, -(top + bottom) / 2, name, ha="right", va="center",
                fontsize=11, fontweight="bold", color=BLACK if name != "RTC + room sensor" else GREY)
    for pin in range(1, 41):
        x, y = pin_xy(pin)
        label, role = PINS.get(pin, ("", None))
        colour = WIRE_COLOURS.get(role)
        face = colour or ("white" if role in (None, "empty") else LIGHT)
        ax.add_patch(Circle((x, y), 0.27, fc=face, ec=BLACK, lw=1.0, ls="--" if role == "empty" else "-"))
        ax.text(x, y, str(pin), ha="center", va="center", fontsize=7.5, fontweight="bold",
                color="white" if role in ("power", "GND", "SDA") else BLACK)
        if label:
            side, ha = (-0.45, "right") if pin % 2 else (1.45, "left")
            ax.text(x + side, y, label, ha=ha, va="center", fontsize=8.5,
                    color=RED if role == "danger" else (GREY if role in ("rtc", "empty") else BLACK))
    ax.annotate("", xy=(0.5, -20.2), xytext=(0.5, -19.7), arrowprops=dict(arrowstyle="-|>", color=GREY))
    ax.text(0.5, -20.5, "USB / Ethernet ports this way", ha="center", va="top", fontsize=9, color=GREY)
    ax.text(0.5, 0.75, "Pin 1 (square pad underneath)", ha="center", va="bottom", fontsize=9, color=GREY)
    ax.set_xlim(-4.6, 4.2)
    ax.set_ylim(-21.2, 1.4)
    ax.set_aspect("equal")
    ax.axis("off")


def main() -> None:
    fig = plt.figure(figsize=(8.27, 11.69))  # A4 portrait
    fig.text(0.06, 0.965, "Tenebrio rig · SHT45 sensor wiring", fontsize=17, fontweight="bold", va="top")
    fig.text(0.06, 0.937, "Raspberry Pi 4 header seen from above, USB and Ethernet ports facing you.\n"
             "Odd pins on the left, even pins on the board edge.", fontsize=9, color=GREY, va="top")
    draw_header(fig.add_axes([0.02, 0.30, 0.96, 0.595]))

    ax = fig.add_axes([0.06, 0.17, 0.88, 0.12])
    ax.axis("off")
    header = ["Sensor", "Power (red)", "GND (black)", "SDA (blue)", "SCL (yellow)", "Bus"]
    table = ax.table(cellText=ROWS, colLabels=header, loc="upper center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(9.5)
    table.scale(1, 1.55)
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor("#bdbdbd")
        if row == 0:
            cell.set_text_props(fontweight="bold")
            cell.set_facecolor(LIGHT)
        if row > 0 and col == 0:
            cell.set_text_props(fontweight="bold")

    notes = ("• On each sensor board the red wire goes to VIN, not 3Vo (the Qwiic socket already does this).\n"
             "• Each sensor is powered from a GPIO that the Pi drives high from boot (gpio=27,6,26=op,dh in config.txt).\n"
             "  Until config.txt has that line, those pins are off, so wiring is safe. Never use pins 2 and 4 (5 V).\n"
             "• The room sensor plugs into the RTC board's spare Qwiic connector; it needs no header pins.\n"
             "• Label every cable at both ends: a swapped T1/T2 cable silently swaps the data.\n"
             "• Must match SENSORS in lights/tenebrio_logger.py and lights/README.md, \"Sensors\".")
    fig.text(0.06, 0.155, notes, fontsize=8.5, va="top", linespacing=1.5)
    fig.text(0.94, 0.02, "tenebrio-photoperiod-rig · sensor wiring v2 · 2026-10-11", fontsize=7.5, color=GREY, ha="right")
    fig.savefig(OUT)
    fig.savefig(OUT.with_suffix(".png"), dpi=130)
    print(f"wrote {OUT} and .png")


if __name__ == "__main__":
    main()
