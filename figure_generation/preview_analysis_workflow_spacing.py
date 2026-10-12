from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "figures" / "part2_revision" / "01_analysis_workflow_spacing_preview.png"

BLUE = "#0065BD"
DARK_BLUE = "#003359"
GREEN = "#A2AD00"
GRAY = "#4D4D4D"
LIGHT_FILL = "#F7F9FB"


mpl.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "DejaVu Sans"],
        "font.size": 11,
        "figure.facecolor": "white",
        "savefig.facecolor": "white",
    }
)


def add_box(ax, x, y, width, height, title, body, color):
    patch = FancyBboxPatch(
        (x, y),
        width,
        height,
        boxstyle="round,pad=0.035,rounding_size=0.045",
        linewidth=1.6,
        edgecolor=color,
        facecolor=LIGHT_FILL,
    )
    ax.add_patch(patch)
    ax.text(
        x + 0.18,
        y + height - 0.22,
        title,
        color=color,
        fontsize=12.2,
        fontweight="bold",
        ha="left",
        va="top",
    )
    ax.text(
        x + 0.18,
        y + 0.20,
        body,
        color=GRAY,
        fontsize=10.1,
        ha="left",
        va="bottom",
        linespacing=1.12,
    )


def add_arrow(ax, start, end):
    ax.add_patch(
        FancyArrowPatch(
            start,
            end,
            arrowstyle="-|>",
            mutation_scale=15,
            linewidth=1.6,
            color=GRAY,
            shrinkA=2,
            shrinkB=2,
        )
    )


fig, ax = plt.subplots(figsize=(11.8, 5.2))
ax.set_xlim(0, 11.8)
ax.set_ylim(0, 4.8)
ax.axis("off")

box_width = 3.05
box_height = 1.05
x_left, x_mid, x_right = 0.35, 4.38, 8.41
y_top, y_bottom = 2.72, 0.86

steps = [
    (x_left, y_top, "1  Input audit", "Hashes, task counts,\npriority counts", BLUE),
    (x_mid, y_top, "2  Integrity gate", "Cases, scenarios, status,\nand duplicate checks", BLUE),
    (x_right, y_top, "3  Metric pass", "Mission, timing, traffic,\nand robustness metrics", DARK_BLUE),
    (x_right, y_bottom, "4  Paired analysis", "Matched cases and\nwithin-case effects", DARK_BLUE),
    (x_mid, y_bottom, "5  Decision pass", "Redundancy, Pareto, and\nweight sensitivity", DARK_BLUE),
    (x_left, y_bottom, "6  Claim freeze", "Evidence boundary\nand traceability", GREEN),
]

for x, y, title, body, color in steps:
    add_box(ax, x, y, box_width, box_height, title, body, color)

mid_y_top = y_top + box_height / 2
mid_y_bottom = y_bottom + box_height / 2
add_arrow(ax, (x_left + box_width + 0.14, mid_y_top), (x_mid - 0.14, mid_y_top))
add_arrow(ax, (x_mid + box_width + 0.14, mid_y_top), (x_right - 0.14, mid_y_top))
add_arrow(
    ax,
    (x_right + box_width / 2, y_top - 0.12),
    (x_right + box_width / 2, y_bottom + box_height + 0.12),
)
add_arrow(ax, (x_right - 0.14, mid_y_bottom), (x_mid + box_width + 0.14, mid_y_bottom))
add_arrow(ax, (x_mid - 0.14, mid_y_bottom), (x_left + box_width + 0.14, mid_y_bottom))

ax.text(
    0.35,
    4.42,
    "Scientific analysis sequence",
    color=DARK_BLUE,
    fontsize=15.5,
    fontweight="bold",
    ha="left",
    va="top",
)
ax.text(
    5.9,
    0.24,
    "A failed gate restricts the evidence population or withholds the claim; scoring does not repair invalid input.",
    color=GRAY,
    fontsize=10.2,
    ha="center",
    va="bottom",
)

fig.savefig(OUT, dpi=220, bbox_inches="tight", pad_inches=0.16)
plt.close(fig)
print(OUT)
