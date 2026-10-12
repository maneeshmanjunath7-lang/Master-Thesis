"""Generate the Part 2 revision figures from the frozen thesis tables.

The figures intentionally keep titles, identifiers, and interpretation outside the
plot area.  Every output is written as both a 300 dpi PNG and a vector PDF.
"""

from __future__ import annotations

from pathlib import Path
import re

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np
import pandas as pd
from scipy.optimize import curve_fit


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CORRECTED_180 = DATA / "final_corrected_180"
OUT = ROOT / "figures" / "part2_revision"
TABLES = OUT / "tables"

BLUE = "#0065BD"
DARK_BLUE = "#003359"
LIGHT_BLUE = "#64A0C8"
ORANGE = "#E37222"
GREEN = "#A2AD00"
RED = "#C4071B"
PURPLE = "#69085A"
GRAY = "#555555"
LIGHT_GRAY = "#DAD7CB"


def style() -> None:
    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "DejaVu Sans"],
            "font.size": 10.5,
            "axes.labelsize": 11.5,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
            "legend.fontsize": 9.2,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.color": "#D9D9D9",
            "grid.linewidth": 0.7,
            "grid.alpha": 0.8,
            "lines.linewidth": 2.0,
            "lines.markersize": 5.5,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "savefig.facecolor": "white",
        }
    )


def save(fig: plt.Figure, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    TABLES.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / f"{name}.png", dpi=300, bbox_inches="tight")
    fig.savefig(OUT / f"{name}.pdf", bbox_inches="tight")
    plt.close(fig)


def panel(ax: plt.Axes, label: str) -> None:
    ax.text(0.01, 0.98, label, transform=ax.transAxes, ha="left", va="top", fontweight="bold")


def plot_analysis_workflow() -> None:
    fig, ax = plt.subplots(figsize=(11.8, 5.2))
    ax.set_xlim(0, 11.8)
    ax.set_ylim(0, 4.8)
    ax.axis("off")

    box_width = 3.05
    box_height = 1.05
    x_left, x_mid, x_right = 0.35, 4.38, 8.41
    y_top, y_bottom = 2.72, 0.86
    boxes = [
        (x_left, y_top, "1  Input audit", "Hashes, task counts,\npriority counts", BLUE),
        (x_mid, y_top, "2  Integrity gate", "Cases, scenarios, status,\nand duplicate checks", BLUE),
        (x_right, y_top, "3  Metric pass", "Mission, timing, traffic,\nand robustness metrics", DARK_BLUE),
        (x_right, y_bottom, "4  Paired analysis", "Matched cases and\nwithin-case effects", DARK_BLUE),
        (x_mid, y_bottom, "5  Decision pass", "Redundancy, Pareto, and\nweight sensitivity", DARK_BLUE),
        (x_left, y_bottom, "6  Claim freeze", "Evidence boundary\nand traceability", GREEN),
    ]

    for x, y, title, body, color in boxes:
        box = FancyBboxPatch(
            (x, y),
            box_width,
            box_height,
            boxstyle="round,pad=0.035,rounding_size=0.045",
            linewidth=1.6,
            edgecolor=color,
            facecolor="#F7F9FB",
        )
        ax.add_patch(box)
        ax.text(
            x + 0.18,
            y + box_height - 0.22,
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

    def arrow(start: tuple[float, float], end: tuple[float, float]) -> None:
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

    mid_y_top = y_top + box_height / 2
    mid_y_bottom = y_bottom + box_height / 2
    arrow((x_left + box_width + 0.14, mid_y_top), (x_mid - 0.14, mid_y_top))
    arrow((x_mid + box_width + 0.14, mid_y_top), (x_right - 0.14, mid_y_top))
    arrow(
        (x_right + box_width / 2, y_top - 0.12),
        (x_right + box_width / 2, y_bottom + box_height + 0.12),
    )
    arrow((x_right - 0.14, mid_y_bottom), (x_mid + box_width + 0.14, mid_y_bottom))
    arrow((x_mid - 0.14, mid_y_bottom), (x_left + box_width + 0.14, mid_y_bottom))

    ax.text(
        0.35,
        4.42,
        "Scientific analysis sequence",
        fontsize=15.5,
        color=DARK_BLUE,
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
    save(fig, "01_analysis_workflow")


def nominal_data() -> pd.DataFrame:
    return pd.read_csv(DATA / "nominal" / "01_cn_sweep_regional_summary.data.csv")


def plot_nominal_cn_response() -> None:
    data = nominal_data()
    specs = [
        ("observation_mean", "Observation fulfilment (%)"),
        ("s100_mean", "Complete dissemination (%)"),
        ("receiver_fraction_mean", "Useful-recipient coverage (%)"),
        ("p90_first_reception_mean", "P90 first reception (min)"),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(10.4, 7.2), constrained_layout=True)
    for ax, (column, ylabel) in zip(axes.flat, specs):
        for region, color, marker, linestyle in (
            ("California", BLUE, "o", "-"), ("India", ORANGE, "s", "--")
        ):
            part = data[data.region == region].sort_values("cn_fraction_percent")
            ax.plot(part.cn_fraction_percent, part[column], color=color, marker=marker,
                    linestyle=linestyle, label=region)
        ax.set_xlabel("Central Node fraction (%)")
        ax.set_ylabel(ylabel)
        ax.set_xticks(np.arange(0, 101, 20))
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="outside lower center", ncol=2, frameon=False)
    save(fig, "02_nominal_cn_response")


def _exp_offset(x: np.ndarray, a: float, b: float, c: float) -> np.ndarray:
    return a * np.exp(-b * x) + c


def _fit_selected(x: np.ndarray, y: np.ndarray, model: str) -> tuple[np.ndarray, np.ndarray]:
    grid = np.linspace(float(x.min()), float(x.max()), 300)
    if model == "Quadratic":
        coeff = np.polyfit(x, y, 2)
        return grid, np.polyval(coeff, grid)
    try:
        params, _ = curve_fit(_exp_offset, x, y, p0=[float(y[0] - y[-1]), 0.04, float(y[-1])], maxfev=30000)
        return grid, _exp_offset(grid, *params)
    except Exception:
        coeff = np.polyfit(x, y, 2)
        return grid, np.polyval(coeff, grid)


def plot_nominal_model_fits() -> None:
    data = nominal_data()
    specs = [
        ("observation_mean", "Observation fulfilment (%)", {"California": "Exponential + offset", "India": "Quadratic"}),
        ("receiver_fraction_mean", "Useful-recipient coverage (%)", {"California": "Quadratic", "India": "Quadratic"}),
        ("p90_first_reception_mean", "P90 first reception (min)", {"California": "Exponential + offset", "India": "Exponential + offset"}),
    ]
    fig, axes = plt.subplots(3, 2, figsize=(10.0, 9.2), constrained_layout=True)
    for row, (column, ylabel, models) in enumerate(specs):
        for col, (region, color) in enumerate((("California", BLUE), ("India", ORANGE))):
            ax = axes[row, col]
            part = data[data.region == region].sort_values("cn_fraction_percent")
            x = part.cn_fraction_percent.to_numpy(float)
            y = part[column].to_numpy(float)
            grid, fitted = _fit_selected(x, y, models[region])
            ax.scatter(x, y, s=34, color=color, zorder=3, label="Observed marginal mean")
            ax.plot(grid, fitted, color=GRAY, linestyle="--", label=models[region])
            ax.set_xlabel("Central Node fraction (%)")
            ax.set_ylabel(ylabel)
            ax.set_xticks(np.arange(0, 101, 20))
            panel(ax, region)
            if row == 0 and col == 0:
                ax.legend(frameon=False, loc="lower center")
            elif row == 0 and col == 1:
                ax.legend(frameon=False, loc="lower center")
    save(fig, "03_nominal_cn_model_fits")


def plot_nominal_paired() -> None:
    data = pd.read_csv(DATA / "nominal" / "04_regional_paired_observation.data.csv")
    x = data.observation_success_percent_california
    y = data.observation_success_percent_india
    fig, ax = plt.subplots(figsize=(6.2, 5.3), constrained_layout=True)
    ax.scatter(x, y, s=24, color=BLUE, alpha=0.42, edgecolor="none")
    ax.plot([0, 100], [0, 100], color=GRAY, linestyle="--", label="Equal result")
    ax.set_xlim(-2, 102)
    ax.set_ylim(-2, 102)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("California observation fulfilment (%)")
    ax.set_ylabel("India observation fulfilment (%)")
    ax.legend(frameon=False, loc="lower right")
    save(fig, "04_nominal_paired_observation")


def _parse_case(case_id: str) -> tuple[int, int, int, int, int]:
    values = re.search(r"T(\d+)_P(\d+)_H(\d+)_I(\d+)_CN(\d+)", str(case_id))
    if not values:
        return (0, 0, 0, 0, 0)
    return tuple(int(x) for x in values.groups())


def plot_nominal_pareto() -> None:
    data = pd.read_csv(DATA / "nominal" / "02_exact_pareto_landscape.data.csv")
    parsed = data.case_id.apply(_parse_case)
    data[["satellites", "planes", "altitude", "inclination_x10", "cn_percent"]] = pd.DataFrame(parsed.tolist(), index=data.index)
    fig, axes = plt.subplots(1, 2, figsize=(10.6, 4.5), constrained_layout=True)
    marker_map = {60: "o", 120: "s", 180: "^"}
    for ax, region in zip(axes, ("California", "India")):
        part = data[data.region == region]
        for fleet, marker in marker_map.items():
            fleet_data = part[part.satellites == fleet]
            ax.scatter(fleet_data.resource_burden_proxy, fleet_data.observation_success_percent,
                       c=fleet_data.cn_percent, cmap="viridis", vmin=0, vmax=100,
                       marker=marker, s=25, alpha=0.40, edgecolor="none")
        front = part[part.pareto_optimal.astype(str).str.lower().isin(["true", "1"])]
        ax.scatter(front.resource_burden_proxy, front.observation_success_percent,
                   facecolor="none", edgecolor="black", linewidth=0.8, s=53,
                   label="Exact multi-objective Pareto member")
        ax.set_xlabel("Resource-burden proxy (0--1)")
        ax.set_ylabel("Observation fulfilment (%)")
        panel(ax, region)
    fleet_handles = [Line2D([0], [0], marker=m, color="none", markerfacecolor=LIGHT_BLUE,
                            markersize=7, label=f"{fleet} satellites") for fleet, m in marker_map.items()]
    axes[0].legend(handles=fleet_handles, frameon=False, loc="lower right")
    norm = mpl.colors.Normalize(vmin=0, vmax=100)
    sm = mpl.cm.ScalarMappable(norm=norm, cmap="viridis")
    cbar = fig.colorbar(sm, ax=axes, location="right", shrink=0.88, pad=0.02)
    cbar.set_label("Central Node fraction (%)")
    save(fig, "05_nominal_pareto_landscape")

    front = data[data.pareto_optimal.astype(str).str.lower().isin(["true", "1"])].copy()
    composition = (
        front.groupby(["region", "satellites"], as_index=False)
        .agg(pareto_cases=("case_id", "count"), median_cn_percent=("cn_percent", "median"),
             min_cn_percent=("cn_percent", "min"), max_cn_percent=("cn_percent", "max"),
             median_observation=("observation_success_percent", "median"),
             median_resource=("resource_burden_proxy", "median"))
    )
    composition.to_csv(TABLES / "nominal_pareto_front_composition.csv", index=False)


def plot_confirmatory_robustness() -> None:
    data = pd.read_csv(DATA / "final_results" / "uniform_60_120_robustness_summary.csv")
    data = data[(data.metric == "s100_surviving_before_deadline_percent") & (data.quantity == "retention")].copy()
    labels = {
        "capacity_degradation": "Capacity degradation",
        "ground_station_outage": "Ground-station outage",
        "link_window_outage": "Link-window outage",
        "random_cn_failure": "Random Central Node loss",
        "random_satellite_failure": "Random satellite loss",
        "targeted_cn_failure": "Targeted Central Node loss",
    }
    colors = [BLUE, ORANGE, GREEN, RED, PURPLE, GRAY]
    markers = ["o", "s", "^", "D", "v", "P"]
    linestyles = ["-", "--", "-.", ":", (0, (5, 2)), (0, (3, 1, 1, 1))]
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.6), constrained_layout=True, sharey=True)
    for ax, region in zip(axes, ("California", "India")):
        part = data[data.region == region]
        for idx, family in enumerate(labels):
            line = part[part.family == family].sort_values("failure_level_percent")
            if line.empty:
                continue
            ax.plot(line.failure_level_percent, 100 * line["mean"], color=colors[idx], marker=markers[idx],
                    linestyle=linestyles[idx], label=labels[family])
        ax.axhline(100, color="black", linewidth=1.0)
        ax.set_xlabel("Failure or degradation level (%)")
        panel(ax, region)
    axes[0].set_ylabel("Strict-completion retention (%)")
    handles, legend_labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, legend_labels, loc="outside lower center", ncol=3, frameon=False)
    save(fig, "06_confirmatory_robustness")


def plot_exploratory_cn() -> None:
    data = pd.read_csv(CORRECTED_180 / "corrected_180_cn_fraction_intervals.csv")
    data = data[data.cn_fraction_percent >= 30].copy()
    scenarios = [("nominal_original", DARK_BLUE, "o", "Bounded routing"),
                 ("unlimited_useful_deadline", ORANGE, "s", "Unlimited routing")]
    specs = [
        ("observation_success_percent", "Observation fulfilment (%)"),
        ("s100_surviving_before_deadline_percent", "Complete dissemination (%)"),
        ("p100_t100_surviving_min", "Worst-case complete dissemination (min)"),
        ("total_transferred_data_mbits", "Transferred data (Mbit/case)"),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(10.4, 7.4), constrained_layout=True)
    for ax, (metric, ylabel) in zip(axes.flat, specs):
        for scenario, color, marker, label in scenarios:
            line = data[data.scenario_id == scenario].sort_values("cn_fraction_percent")
            if line.empty:
                continue
            mean_col = f"{metric}_mean"
            low_col = f"{metric}_bootstrap_low"
            high_col = f"{metric}_bootstrap_high"
            ax.plot(line.cn_fraction_percent, line[mean_col], color=color, marker=marker, label=label)
            if {low_col, high_col}.issubset(line.columns):
                ax.fill_between(line.cn_fraction_percent, line[low_col], line[high_col],
                                color=color, alpha=0.10, linewidth=0)
        ax.set_xlabel("Central Node fraction (%)")
        ax.set_ylabel(ylabel)
        ax.set_xticks(np.arange(30, 101, 10))
    handles, labels_out = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels_out, loc="outside lower center", ncol=2, frameon=False)
    save(fig, "07_exploratory_180_cn_response")


def plot_exploratory_scenario_effects() -> None:
    data = pd.read_csv(CORRECTED_180 / "corrected_180_paired_scenario_effects_summary.csv")
    wanted = {
        "policy_peer": "Peer routing",
        "task_size_x0p5": "Packet size 0.5x",
        "task_size_x2p0": "Packet size 2x",
        "task_size_x4p0": "Packet size 4x",
        "unlimited_useful_deadline": "Unlimited routing",
    }
    data = data[data.alternative_scenario.isin(wanted)].copy()
    data["label"] = data.alternative_scenario.map(wanted)
    metrics = [("observation_success_percent", "Observation change (percentage points)"),
               ("s100_surviving_before_deadline_percent", "Strict-completion change (percentage points)"),
               ("total_transferred_data_mbits", "Transferred-data change (Mbit/case)")]
    fig, axes = plt.subplots(1, 3, figsize=(11.3, 4.3), constrained_layout=True)
    order = list(wanted.values())
    colors = [BLUE, LIGHT_BLUE, ORANGE, RED, GREEN]
    for ax, (metric, xlabel) in zip(axes, metrics):
        part = data[data.metric == metric].set_index("label").reindex(order).dropna(subset=["mean_delta_alternative_minus_reference"])
        values = part.mean_delta_alternative_minus_reference
        y = np.arange(len(part))
        ax.barh(y, values, color=colors[: len(part)])
        ax.axvline(0, color="black", linewidth=0.9)
        ax.set_yticks(y, part.index if ax is axes[0] else [""] * len(part))
        ax.set_xlabel(xlabel)
        ax.invert_yaxis()
    save(fig, "08_exploratory_180_scenario_effects")


def plot_exploratory_robustness() -> None:
    data = pd.read_csv(CORRECTED_180 / "corrected_180_robustness_summary.csv")
    data = data[(data.metric == "s100_surviving_before_deadline_percent") & (data.quantity == "retention")].copy()
    labels = {
        "capacity_degradation": "Capacity degradation",
        "ground_station_outage": "Ground-station outage",
        "link_window_outage": "Link-window outage",
        "random_cn_failure": "Random Central Node loss",
        "random_satellite_failure": "Random satellite loss",
        "targeted_cn_failure": "Targeted Central Node loss",
    }
    colors = [BLUE, ORANGE, GREEN, RED, PURPLE, GRAY]
    markers = ["o", "s", "^", "D", "v", "P"]
    linestyles = ["-", "--", "-.", ":", (0, (5, 2)), (0, (3, 1, 1, 1))]
    fig, ax = plt.subplots(figsize=(8.3, 4.8), constrained_layout=True)
    for idx, family in enumerate(labels):
        line = data[data.family == family].sort_values("failure_level_percent")
        if line.empty:
            continue
        ax.plot(line.failure_level_percent, 100 * line["mean"], color=colors[idx], marker=markers[idx],
                linestyle=linestyles[idx], label=labels[family])
    ax.axhline(100, color="black", linewidth=1.0)
    ax.set_xlabel("Failure or degradation level (%)")
    ax.set_ylabel("Strict-completion retention (%)")
    ax.legend(frameon=False, ncol=2, loc="lower left")
    save(fig, "09_exploratory_180_robustness")


def plot_exploratory_combined() -> None:
    data = pd.read_csv(CORRECTED_180 / "corrected_180_combined_stress_summary.csv")
    data = data[(data.metric == "s100_surviving_before_deadline_percent") & (data.quantity == "retention")].copy()
    packet_col = "task_size_multiplier"
    failure_col = "failure_level_percent"
    fig, ax = plt.subplots(figsize=(7.0, 4.5), constrained_layout=True)
    for multiplier, color, marker in ((2.0, BLUE, "o"), (4.0, ORANGE, "s")):
        line = data[data[packet_col] == multiplier].sort_values(failure_col)
        ax.plot(line[failure_col], 100 * line["mean"], color=color, marker=marker,
                label=f"Packet size {multiplier:.0f}x")
    ax.set_xlabel("Random satellite loss (%)")
    ax.set_ylabel("Strict-completion retention (%)")
    ax.set_xticks(sorted(data[failure_col].dropna().unique()))
    ax.legend(frameon=False)
    save(fig, "10_exploratory_180_combined_stress")


def plot_matched_cn_response() -> None:
    data = pd.read_csv(DATA / "final_results" / "uniform_60_120_cn_fraction_intervals.csv")
    specs = [
        ("observation_success_percent", "Observation fulfilment (%)"),
        ("s100_surviving_before_deadline_percent", "Complete dissemination (%)"),
        ("total_transferred_data_mbits", "Transferred data (Mbit/case)"),
    ]
    styles = {
        ("California", "nominal_original"): (BLUE, "o", "--", "California, bounded"),
        ("California", "unlimited_useful_deadline"): (BLUE, "o", "-", "California, unlimited"),
        ("India", "nominal_original"): (ORANGE, "s", "--", "India, bounded"),
        ("India", "unlimited_useful_deadline"): (ORANGE, "s", "-", "India, unlimited"),
    }
    fig, axes = plt.subplots(1, 3, figsize=(11.2, 4.2), constrained_layout=True)
    for ax, (metric, ylabel) in zip(axes, specs):
        for key, (color, marker, line_style, label) in styles.items():
            region, scenario = key
            line = data[(data.region == region) & (data.scenario_id == scenario)].sort_values("cn_fraction_percent")
            mean_col = f"{metric}_mean"
            low_col = f"{metric}_bootstrap_low"
            high_col = f"{metric}_bootstrap_high"
            ax.plot(line.cn_fraction_percent, line[mean_col], color=color, marker=marker,
                    linestyle=line_style, label=label)
            ax.fill_between(line.cn_fraction_percent, line[low_col], line[high_col],
                            color=color, alpha=0.08, linewidth=0)
        ax.set_xlabel("Central Node fraction (%)")
        ax.set_ylabel(ylabel)
        ax.set_xticks(np.arange(0, 101, 20))
    handles, labels_out = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels_out, loc="outside lower center", ncol=2, frameon=False)
    save(fig, "11_matched_cn_response")


def plot_exploratory_pareto() -> None:
    data = pd.read_csv(CORRECTED_180 / "corrected_180_exact_pareto.csv")
    data = data[data.cn_fraction_percent >= 30].copy()
    front = data[data.is_exact_pareto.astype(bool)]
    fig, axes = plt.subplots(1, 2, figsize=(10.8, 4.6), constrained_layout=True)
    specs = [
        ("total_transferred_data_mbits", "Transferred data (Mbit/case)"),
        ("p100_t100_surviving_min", "Worst-case complete dissemination (min)"),
    ]
    for ax, (xcol, xlabel) in zip(axes, specs):
        ax.scatter(data[xcol], data.s100_surviving_before_deadline_percent,
                   c=data.cn_fraction_percent, cmap="viridis", vmin=30, vmax=100,
                   s=26, alpha=0.28, edgecolor="none")
        ax.scatter(front[xcol], front.s100_surviving_before_deadline_percent,
                   c=front.cn_fraction_percent, cmap="viridis", vmin=30, vmax=100,
                   s=58, edgecolor="black", linewidth=0.7, label="Exact Pareto member")
        ax.set_xlabel(xlabel)
        ax.set_ylabel("Complete dissemination (%)")
    axes[0].legend(frameon=False, loc="lower right")
    norm = mpl.colors.Normalize(vmin=30, vmax=100)
    sm = mpl.cm.ScalarMappable(norm=norm, cmap="viridis")
    cbar = fig.colorbar(sm, ax=axes, location="right", shrink=0.88, pad=0.02)
    cbar.set_label("Central Node fraction (%)")
    save(fig, "12_exploratory_180_pareto")


def plot_exploratory_priority() -> None:
    data = pd.read_csv(CORRECTED_180 / "corrected_180_priority_event_summary.csv")
    data = data[(data.scenario_id == "unlimited_useful_deadline") & (data.cn_fraction_percent >= 30)].copy()
    event_labels = {"follow_up_observation": "Follow-up observation", "full_dissemination_S100": "Complete dissemination"}
    colors = {"High": ORANGE, "Medium": BLUE, "Low": GREEN}
    markers = {"High": "o", "Medium": "s", "Low": "^"}
    fig, axes = plt.subplots(2, 2, figsize=(10.4, 7.3), constrained_layout=True)
    for row, event in enumerate(event_labels):
        subset = data[data.event_name == event]
        for priority in ("High", "Medium", "Low"):
            line = subset[subset.priority_class == priority].sort_values("cn_fraction_percent")
            axes[row, 0].plot(line.cn_fraction_percent, line.base_mean_event_probability,
                              color=colors[priority], marker=markers[priority], label=priority)
            axes[row, 1].plot(line.cn_fraction_percent, line.base_mean_standardized_speed,
                              color=colors[priority], marker=markers[priority], label=priority)
        axes[row, 0].set_ylabel(f"{event_labels[event]}\nprobability by deadline")
        axes[row, 1].set_ylabel(f"{event_labels[event]}\nstandardized speed")
        for col in range(2):
            axes[row, col].set_xlabel("Central Node fraction (%)")
            axes[row, col].set_xticks(np.arange(30, 101, 10))
    handles, labels_out = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels_out, loc="outside lower center", ncol=3, frameon=False)
    save(fig, "13_exploratory_180_priority")


def plot_matched_120_180_response() -> None:
    data = pd.read_csv(CORRECTED_180 / "corrected_180_matched_120_180_cn_response.csv")
    specs = [
        ("observation_success_percent", "Observation fulfilment (%)"),
        ("s100_surviving_before_deadline_percent", "Complete dissemination (%)"),
        ("p100_t100_surviving_min", "Worst-case complete dissemination (min)"),
        ("total_transferred_data_mbits", "Transferred data (Mbit/case)"),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(10.4, 7.4), constrained_layout=True)
    for ax, (metric, ylabel) in zip(axes.flat, specs):
        line = data[data.metric == metric].sort_values("cn_fraction_percent")
        ax.plot(line.cn_fraction_percent, line.t120_mean, color=BLUE, marker="o", label="120 satellites")
        ax.plot(line.cn_fraction_percent, line.t180_mean, color=ORANGE, marker="s", label="180 satellites")
        ax.set_xlabel("Central Node fraction (%)")
        ax.set_ylabel(ylabel)
        ax.set_xticks(np.arange(30, 101, 10))
    handles, labels_out = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels_out, loc="outside lower center", ncol=2, frameon=False)
    save(fig, "14_matched_india_120_180_response")


def main() -> None:
    style()
    plot_analysis_workflow()
    plot_nominal_cn_response()
    plot_nominal_model_fits()
    plot_nominal_paired()
    plot_nominal_pareto()
    plot_confirmatory_robustness()
    plot_exploratory_cn()
    plot_exploratory_scenario_effects()
    plot_exploratory_robustness()
    plot_exploratory_combined()
    plot_matched_cn_response()
    plot_exploratory_pareto()
    plot_exploratory_priority()
    plot_matched_120_180_response()
    print(f"Generated Part 2 revision figures in {OUT}")


if __name__ == "__main__":
    main()
