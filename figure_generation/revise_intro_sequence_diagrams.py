from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Rectangle


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "figures" / "part1_revision"

BLUE = "#0065BD"
ORANGE = "#E37222"
DARK = "#404040"
LIGHT = "#EAF2F8"
RULE = "#D8D5CC"


def setup(width: float, height: float):
    fig, ax = plt.subplots(figsize=(width, height))
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    return fig, ax


def box(ax, x, y, w, h, label, highlighted=False, fontsize=11.6):
    ax.add_patch(
        Rectangle(
            (x, y),
            w,
            h,
            facecolor=LIGHT if highlighted else "white",
            edgecolor=ORANGE if highlighted else BLUE,
            linewidth=1.6,
        )
    )
    ax.text(
        x + w / 2,
        y + h / 2,
        label,
        ha="center",
        va="center",
        fontsize=fontsize,
        color=DARK,
        fontweight="semibold" if highlighted else "normal",
        linespacing=1.15,
    )


def arrow(ax, start, end):
    ax.add_patch(
        FancyArrowPatch(
            start,
            end,
            arrowstyle="-|>",
            mutation_scale=15,
            linewidth=1.4,
            color=DARK,
        )
    )


def mission_chain():
    fig, ax = setup(12.0, 3.8)
    labels = [
        "Wildfire\nobservation task",
        "Satellite\nacquisition",
        "Inter-satellite\ndissemination",
        "Ground-station\ndelivery",
        "Operational\nuse",
    ]
    xs = [0.015, 0.220, 0.425, 0.630, 0.835]
    w, y, h = 0.150, 0.48, 0.27

    for index, (x, label) in enumerate(zip(xs, labels), start=1):
        box(ax, x, y, w, h, label, highlighted=index == 3, fontsize=11.9)
        ax.text(
            x + w / 2,
            0.84,
            str(index),
            ha="center",
            va="center",
            fontsize=12.0,
            color="white",
            bbox={"boxstyle": "square,pad=0.25", "facecolor": BLUE, "edgecolor": BLUE},
        )
        if index < len(labels):
            arrow(ax, (x + w + 0.012, y + h / 2), (xs[index] - 0.012, y + h / 2))

    ax.text(
        xs[2] + w / 2,
        0.375,
        "Central Nodes support forwarding decisions",
        ha="center",
        va="center",
        fontsize=11.4,
        color=ORANGE,
    )
    ax.plot([0.04, 0.96], [0.18, 0.18], color=RULE, linewidth=1.1)
    ax.text(
        0.5,
        0.09,
        "Time-varying contacts, link capacity, and task deadlines constrain the complete chain",
        ha="center",
        va="center",
        fontsize=11.2,
        color=DARK,
    )
    fig.savefig(OUT / "mission_chain.png", dpi=300, facecolor="white")
    plt.close(fig)


def method_sequence():
    fig, ax = setup(12.0, 5.4)
    ax.text(0.02, 0.73, "Simulation", ha="left", va="center", fontsize=12.2, color=DARK, fontweight="semibold")
    ax.text(0.02, 0.27, "Post-processing", ha="left", va="center", fontsize=12.2, color=DARK, fontweight="semibold")
    ax.plot([0.02, 0.98], [0.50, 0.50], color=RULE, linewidth=1.1)

    xs = [0.15, 0.365, 0.580, 0.795]
    w, h = 0.155, 0.20
    top = ["Architecture\ngrid", "Scenario\ndefinition", "Discrete-event\nsimulation", "Checkpointed\noutputs"]
    bottom = ["Completion and\nintegrity gate", "Canonical\nanalysis table", "Paired effects and\nrobustness", "Pareto and\nsensitivity results"]

    for row_y, labels in ((0.63, top), (0.17, bottom)):
        for index, (x, label) in enumerate(zip(xs, labels)):
            highlight = (row_y > 0.5 and index in (0, 3)) or (row_y < 0.5 and index in (0, 1))
            box(ax, x, row_y, w, h, label, highlighted=(row_y < 0.5 and index == 0), fontsize=11.5)
            if highlight and not (row_y < 0.5 and index == 0):
                ax.add_patch(Rectangle((x, row_y), w, h, facecolor=LIGHT, edgecolor=BLUE, linewidth=1.6))
                ax.text(x + w / 2, row_y + h / 2, label, ha="center", va="center", fontsize=11.5, color=DARK, linespacing=1.15)
            if index < len(labels) - 1:
                arrow(ax, (x + w + 0.012, row_y + h / 2), (xs[index + 1] - 0.012, row_y + h / 2))

    return_x = xs[-1] + w / 2
    gate_x = xs[0] + w / 2
    ax.plot([return_x, return_x, gate_x], [0.63, 0.535, 0.535], color=DARK, linewidth=1.25)
    arrow(ax, (gate_x, 0.535), (gate_x, 0.385))
    ax.text(0.56, 0.555, "Only verified outputs proceed", ha="center", va="bottom", fontsize=11.0, color=DARK)

    fig.savefig(OUT / "method_flow.png", dpi=300, facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    mission_chain()
    method_sequence()
