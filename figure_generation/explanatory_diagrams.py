"""Restrained engineering schematics for the thesis method chapters.

The diagrams deliberately use standard rectangular blocks, straight connectors,
one primary colour, and one Central Node accent.  They are intended to read like
technical report figures rather than presentation graphics.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, FancyArrowPatch, Rectangle
import numpy as np

from thesis_style import (
    FigureWriter,
    TUM_BLUE,
    TUM_DARK_BLUE,
    TUM_GRAY,
    TUM_LIGHT_BLUE,
    TUM_LIGHT_GRAY,
    TUM_ORANGE,
)


PALE_BLUE = "#EEF4F8"
PALE_GRAY = "#F4F4F2"


def _canvas(width: float = 10.0, height: float = 3.2):
    fig, ax = plt.subplots(figsize=(width, height), constrained_layout=True)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    return fig, ax


def _box(
    ax,
    xy,
    width,
    height,
    text,
    *,
    facecolor="white",
    edgecolor=TUM_BLUE,
    text_color=TUM_GRAY,
    fontsize=10.8,
    linewidth=1.25,
    weight="normal",
):
    patch = Rectangle(
        xy,
        width,
        height,
        linewidth=linewidth,
        edgecolor=edgecolor,
        facecolor=facecolor,
    )
    ax.add_patch(patch)
    ax.text(
        xy[0] + width / 2,
        xy[1] + height / 2,
        text,
        ha="center",
        va="center",
        color=text_color,
        fontsize=fontsize,
        fontweight=weight,
        linespacing=1.15,
    )
    return patch


def _arrow(ax, start, end, *, color=TUM_GRAY, linewidth=1.25, style="-|>"):
    arrow = FancyArrowPatch(
        start,
        end,
        arrowstyle=style,
        mutation_scale=12,
        linewidth=linewidth,
        color=color,
        connectionstyle="arc3,rad=0",
    )
    ax.add_patch(arrow)
    return arrow


def _stage_number(ax, x: float, y: float, number: int) -> None:
    ax.text(
        x,
        y,
        str(number),
        ha="center",
        va="center",
        fontsize=10.0,
        color="white",
        bbox={"boxstyle": "square,pad=0.25", "facecolor": TUM_BLUE, "edgecolor": TUM_BLUE},
    )


def plot_mission_chain(writer: FigureWriter) -> None:
    fig, ax = _canvas(10.8, 2.75)
    labels = [
        "Wildfire\nobservation task",
        "Satellite\nacquisition",
        "Inter-satellite\ndissemination",
        "Ground-station\ndelivery",
        "Operational\nuse",
    ]
    positions = np.linspace(0.035, 0.805, len(labels))
    width = 0.15
    for index, (x, label) in enumerate(zip(positions, labels), start=1):
        is_network = index == 3
        _box(
            ax,
            (x, 0.42),
            width,
            0.25,
            label,
            facecolor=PALE_BLUE if is_network else "white",
            edgecolor=TUM_ORANGE if is_network else TUM_BLUE,
            weight="semibold" if is_network else "normal",
        )
        _stage_number(ax, x + width / 2, 0.76, index)
        if index < len(labels):
            _arrow(ax, (x + width, 0.545), (positions[index] - 0.006, 0.545))
    ax.text(
        positions[2] + width / 2,
        0.31,
        "Central Nodes support forwarding decisions",
        ha="center",
        va="center",
        color=TUM_ORANGE,
        fontsize=10.4,
    )
    ax.plot([0.06, 0.94], [0.15, 0.15], color=TUM_LIGHT_GRAY, linewidth=1.0)
    ax.text(
        0.50,
        0.09,
        "Time-varying contacts, link capacity and task deadlines constrain the complete chain",
        ha="center",
        color=TUM_GRAY,
        fontsize=10.2,
    )
    writer.save(
        fig,
        number=17,
        stem="mission_chain",
        kind="diagram",
        caption=(
            "End-to-end mission chain considered in the thesis, from wildfire-task creation to operational "
            "delivery. The research contribution focuses on the inter-satellite dissemination stage and "
            "the effect of assigning Central Node functionality to a subset of satellites."
        ),
        source="Conceptual synthesis of the thesis system model",
        sample="Not applicable",
        notes="Engineering block diagram; stages are functional and not drawn to a physical scale.",
    )


def plot_task_workflow(writer: FigureWriter) -> None:
    fig, ax = _canvas(10.8, 3.8)
    labels = [
        "Sentinel-3\nfire detections",
        "Spatial and temporal\nclustering",
        "Priority and deadline\nassignment",
        "Visibility-window\nmatching",
        "Simulation-ready\ntask record",
    ]
    positions = np.linspace(0.035, 0.805, len(labels))
    width = 0.15
    for index, (x, label) in enumerate(zip(positions, labels)):
        _box(ax, (x, 0.57), width, 0.23, label, facecolor=PALE_BLUE if index in (0, 4) else "white")
        if index < len(labels) - 1:
            _arrow(ax, (x + width, 0.685), (positions[index + 1] - 0.006, 0.685))

    fields = [
        (0.22, "Location\nTimestamp"),
        (0.41, "Priority class\nResponse deadline"),
        (0.60, "Task data volume\nObservation value"),
    ]
    ax.text(0.035, 0.30, "Derived fields", ha="left", va="center", fontsize=10.5, color=TUM_GRAY, fontweight="semibold")
    for x, label in fields:
        _box(
            ax,
            (x, 0.18),
            0.16,
            0.18,
            label,
            facecolor=PALE_GRAY,
            edgecolor=TUM_GRAY,
            fontsize=10.0,
            linewidth=0.9,
        )
        _arrow(ax, (x + 0.08, 0.36), (x + 0.08, 0.55), color=TUM_GRAY, linewidth=1.0)
    writer.save(
        fig,
        number=18,
        stem="task_generation_workflow",
        kind="diagram",
        caption=(
            "Processing sequence used to transform Sentinel-3 fire detections into simulation tasks. "
            "Clustering determines the event representation; priority, deadline, data volume and visibility "
            "define how each task enters the dissemination simulation."
        ),
        source="Implemented task-generation and simulation-input workflow",
        sample="Not applicable",
    )


def plot_analysis_workflow(writer: FigureWriter) -> None:
    fig, ax = _canvas(11.0, 4.2)
    ax.text(0.025, 0.72, "Simulation", ha="left", va="center", fontsize=10.8, color=TUM_GRAY, fontweight="semibold")
    ax.text(0.025, 0.28, "Post-processing", ha="left", va="center", fontsize=10.8, color=TUM_GRAY, fontweight="semibold")
    ax.plot([0.02, 0.98], [0.50, 0.50], color=TUM_LIGHT_GRAY, linewidth=1.0)

    top = [
        (0.15, "Architecture\ngrid"),
        (0.35, "Scenario\ndefinition"),
        (0.55, "Discrete-event\nsimulation"),
        (0.75, "Checkpointed\noutputs"),
    ]
    for index, (x, text) in enumerate(top):
        _box(ax, (x, 0.63), 0.14, 0.19, text, facecolor=PALE_BLUE if index in (0, 3) else "white")
        if index < len(top) - 1:
            _arrow(ax, (x + 0.14, 0.725), (top[index + 1][0] - 0.006, 0.725))

    bottom = [
        (0.15, "Completion and\nintegrity gate"),
        (0.35, "Canonical\nanalysis table"),
        (0.55, "Paired effects and\nrobustness"),
        (0.75, "Pareto and\nsensitivity results"),
    ]
    for index, (x, text) in enumerate(bottom):
        _box(
            ax,
            (x, 0.17),
            0.14,
            0.19,
            text,
            facecolor=PALE_BLUE if index in (0, 1) else "white",
            edgecolor=TUM_ORANGE if index == 0 else TUM_BLUE,
        )
        if index < len(bottom) - 1:
            _arrow(ax, (x + 0.14, 0.265), (bottom[index + 1][0] - 0.006, 0.265))
    ax.plot([0.82, 0.82, 0.22], [0.63, 0.535, 0.535], color=TUM_GRAY, linewidth=1.1)
    _arrow(ax, (0.22, 0.535), (0.22, 0.37), color=TUM_GRAY, linewidth=1.1)
    ax.text(0.52, 0.555, "Only verified outputs proceed", ha="center", color=TUM_GRAY, fontsize=9.8)
    writer.save(
        fig,
        number=19,
        stem="simulation_postprocessing_workflow",
        kind="diagram",
        caption=(
            "Reproducible simulation and post-processing workflow. Checkpointed outputs enter the analysis "
            "only after completion and integrity checks, after which all statistics are derived from one "
            "canonical table."
        ),
        source="Implemented campaign and post-processing workflow",
        sample="Not applicable",
    )


def plot_walker_constellation(writer: FigureWriter) -> None:
    fig, ax = _canvas(8.5, 5.8)
    ax.set_aspect("equal", adjustable="box")
    earth = Circle((0.50, 0.49), 0.105, facecolor=PALE_BLUE, edgecolor=TUM_DARK_BLUE, linewidth=1.2)
    ax.add_patch(earth)
    ax.text(0.50, 0.49, "Earth", ha="center", va="center", color=TUM_DARK_BLUE, fontsize=10.8)

    central_indices = {2, 8, 14, 20}
    satellite_index = 0
    for plane in range(4):
        angle = np.deg2rad(plane * 42 - 64)
        phase_offset = plane * np.pi / 12
        t = np.linspace(0, 2 * np.pi, 400)
        x = 0.50 + 0.37 * np.cos(t) * np.cos(angle) - 0.16 * np.sin(t) * np.sin(angle)
        y = 0.49 + 0.37 * np.cos(t) * np.sin(angle) + 0.16 * np.sin(t) * np.cos(angle)
        ax.plot(x, y, color=TUM_LIGHT_GRAY, linewidth=0.9, zorder=0)
        for phase in np.linspace(0, 2 * np.pi, 6, endpoint=False) + phase_offset:
            sx = 0.50 + 0.37 * np.cos(phase) * np.cos(angle) - 0.16 * np.sin(phase) * np.sin(angle)
            sy = 0.49 + 0.37 * np.cos(phase) * np.sin(angle) + 0.16 * np.sin(phase) * np.cos(angle)
            is_central = satellite_index in central_indices
            ax.scatter(
                sx,
                sy,
                s=72 if is_central else 38,
                marker="s" if is_central else "o",
                facecolor=TUM_ORANGE if is_central else "white",
                edgecolor=TUM_ORANGE if is_central else TUM_BLUE,
                linewidth=1.2,
                zorder=2,
            )
            satellite_index += 1
    legend = [
        Line2D([0], [0], marker="o", linestyle="", markerfacecolor="white", markeredgecolor=TUM_BLUE, label="Standard satellite", markersize=7),
        Line2D([0], [0], marker="s", linestyle="", markerfacecolor=TUM_ORANGE, markeredgecolor=TUM_ORANGE, label="Central Node", markersize=7),
    ]
    fig.legend(handles=legend, loc="outside lower center", ncol=2)
    writer.save(
        fig,
        number=20,
        stem="walker_constellation_central_nodes",
        kind="diagram",
        caption=(
            "Schematic Walker-type constellation in which a defined subset of satellites acts as Central "
            "Nodes. The figure distinguishes node roles; the number of orbital planes and satellite spacing "
            "are illustrative and are not drawn to scale."
        ),
        source="Conceptual representation of the simulated architecture",
        sample="Not applicable",
    )


def plot_temporal_routing(writer: FigureWriter) -> None:
    fig, ax = _canvas(10.4, 4.8)
    nodes = ["Ground station", "Satellite A", "Central Node", "Satellite B"]
    ys = np.linspace(0.82, 0.22, len(nodes))
    for label, y in zip(nodes, ys):
        label_color = TUM_ORANGE if label == "Central Node" else TUM_GRAY
        label_weight = "semibold" if label == "Central Node" else "normal"
        ax.text(0.025, y, label, ha="left", va="center", fontsize=11.0, color=label_color, fontweight=label_weight)
        ax.plot([0.20, 0.96], [y, y], color=TUM_LIGHT_GRAY, linewidth=0.85)

    events = [
        (0.27, 0, 1, "Upload", TUM_BLUE),
        (0.44, 1, 2, "Relay", TUM_BLUE),
        (0.69, 2, 3, "Relay", TUM_ORANGE),
        (0.87, 3, 0, "Delivery", TUM_BLUE),
    ]
    for x, source, target, label, color in events:
        _arrow(ax, (x, ys[source]), (x + 0.075, ys[target]), color=color, linewidth=1.5)
        text_y = (ys[source] + ys[target]) / 2 + (0.035 if source < target else -0.04)
        text_x = x - 0.012 if label == "Delivery" else x + 0.037
        ax.text(text_x, text_y, label, ha="center", va="center", color=color, fontsize=9.8)

    ax.plot([0.535, 0.64], [ys[2], ys[2]], color=TUM_ORANGE, linewidth=3.0, solid_capstyle="butt")
    ax.text(0.587, ys[2] + 0.055, "Store", ha="center", color=TUM_ORANGE, fontsize=9.8)
    ax.text(0.58, 0.07, "Time", ha="center", color=TUM_GRAY)
    _arrow(ax, (0.21, 0.105), (0.95, 0.105), color=TUM_GRAY, linewidth=1.1)
    writer.save(
        fig,
        number=21,
        stem="temporal_routing_example",
        kind="diagram",
        caption=(
            "Store-carry-forward dissemination over time-varying contacts. The task is uploaded to a "
            "satellite, relayed to a Central Node, stored until a useful contact becomes available, and "
            "then forwarded for ground delivery."
        ),
        source="Conceptual representation of temporal routing in the simulator",
        sample="Not applicable",
    )


def plot_scenario_transformations(writer: FigureWriter) -> None:
    fig, ax = _canvas(10.8, 5.4)
    _box(ax, (0.025, 0.41), 0.16, 0.17, "Base architecture", facecolor=PALE_BLUE, weight="semibold")
    _arrow(ax, (0.185, 0.495), (0.245, 0.495))
    ax.plot([0.25, 0.25], [0.13, 0.87], color=TUM_GRAY, linewidth=1.0)

    families = [
        "Routing-policy variants",
        "Task-volume variants",
        "Satellite and Central Node failures",
        "Link-window and capacity stress",
        "Ground-station outage",
        "Combined-stress scenarios",
    ]
    ys = np.linspace(0.80, 0.18, len(families))
    for index, (label, y) in enumerate(zip(families, ys)):
        face = PALE_BLUE if index < 2 else "white"
        edge = TUM_BLUE if index < 2 else TUM_GRAY
        _arrow(ax, (0.25, y), (0.335, y), color=TUM_GRAY, linewidth=1.0)
        _box(ax, (0.34, y - 0.045), 0.31, 0.09, label, facecolor=face, edgecolor=edge, fontsize=10.2, linewidth=1.0)
        _arrow(ax, (0.65, y), (0.745, y), color=TUM_GRAY, linewidth=1.0)
    ax.plot([0.75, 0.75], [0.13, 0.87], color=TUM_GRAY, linewidth=1.0)
    _arrow(ax, (0.75, 0.495), (0.81, 0.495))
    _box(ax, (0.815, 0.41), 0.16, 0.17, "Paired with\nbaseline", facecolor=PALE_BLUE, weight="semibold")

    ax.text(0.25, 0.93, "Scenario generation", ha="center", color=TUM_GRAY, fontsize=10.2)
    ax.text(0.75, 0.93, "Common comparison gate", ha="center", color=TUM_GRAY, fontsize=10.2)
    writer.save(
        fig,
        number=22,
        stem="scenario_failure_transformations",
        kind="diagram",
        caption=(
            "Scenario-generation structure applied to each base architecture. Routing, task-volume and "
            "failure/stress variants retain the same base definition and are paired with their baseline "
            "case before effects are calculated."
        ),
        source="Implemented scenario-generation design",
        sample="Not applicable",
        notes="Straight connectors indicate data lineage; no causal ordering is implied among scenario families.",
    )


def generate_explanatory_diagrams(writer: FigureWriter) -> None:
    for function in (
        plot_mission_chain,
        plot_task_workflow,
        plot_analysis_workflow,
        plot_walker_constellation,
        plot_temporal_routing,
        plot_scenario_transformations,
    ):
        function(writer)

