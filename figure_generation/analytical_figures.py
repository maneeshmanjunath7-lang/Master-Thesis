"""Data-driven figures used in the full thesis report."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Polygon
import numpy as np
import pandas as pd
import shapefile

from thesis_style import (
    FigureWriter,
    SERIES_COLORS,
    TUM_BLUE,
    TUM_DARK_BLUE,
    TUM_GRAY,
    TUM_GREEN,
    TUM_LIGHT_BLUE,
    TUM_LIGHT_GRAY,
    TUM_ORANGE,
    TUM_PURPLE,
    TUM_RED,
    clean_axis,
    panel_label,
)


METRIC_LABELS = {
    "observation_success_percent": "Observation success (%)",
    "s100_surviving_before_deadline_percent": "Tasks completed by deadline (%)",
    "mean_surviving_coverage_deadline_percent": "Mean deadline coverage (%)",
    "p100_t100_surviving_min": "Time to full dissemination (min)",
    "total_transferred_data_mbits": "Transferred data (Mbit)",
    "used_capacity_percent": "Used link capacity (%)",
    "cn_related_traffic_share_percent": "Central Node traffic share (%)",
    "max_hops_seen": "Maximum hop count",
    "node_sent_data_gini": "Node-load Gini coefficient",
}

REGION_COLORS = {"California": TUM_BLUE, "India": TUM_ORANGE}
REGION_MARKERS = {"California": "o", "India": "s"}
MAP_DATA_ROOT = Path(__file__).resolve().parent / "map_data"


def _first_col(frame: pd.DataFrame, choices: Iterable[str]) -> str | None:
    lookup = {str(c).strip().lower(): c for c in frame.columns}
    for choice in choices:
        if choice.lower() in lookup:
            return str(lookup[choice.lower()])
    return None


def _numeric(frame: pd.DataFrame, columns: Iterable[str]) -> None:
    for column in columns:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")


def _regions(frame: pd.DataFrame) -> list[str]:
    found = [str(x) for x in frame.get("region", pd.Series(dtype=str)).dropna().unique()]
    ordered = [x for x in ("California", "India") if x in found]
    return ordered + sorted(x for x in found if x not in ordered)


def _scenario(frame: pd.DataFrame, names: Iterable[str]) -> pd.DataFrame:
    names_lower = {name.lower() for name in names}
    return frame[frame["scenario_id"].astype(str).str.lower().isin(names_lower)].copy()


def _human_policy(value: str) -> str:
    text = str(value).lower()
    if "central" in text:
        return "Central Node routing"
    if "peer" in text:
        return "Peer routing"
    if "hybrid" in text:
        return "Hybrid routing"
    return "Nominal routing"


def _human_failure(value: str) -> str:
    return {
        "random_satellite_failure": "Random satellite loss",
        "random_cn_failure": "Random Central Node loss",
        "targeted_cn_failure": "Targeted Central Node loss",
        "link_window_outage": "Link-window outage",
        "capacity_degradation": "Capacity degradation",
        "ground_station_outage": "Ground-station outage",
        "combined_stress": "Combined stress",
    }.get(str(value), str(value).replace("_", " ").title())


def _quantile_summary(
    frame: pd.DataFrame, group: list[str], metric: str
) -> pd.DataFrame:
    return (
        frame.dropna(subset=[metric])
        .groupby(group, dropna=False)[metric]
        .agg(mean="mean", low=lambda x: x.quantile(0.25), high=lambda x: x.quantile(0.75), n="count")
        .reset_index()
    )


def _pareto_mask(values: np.ndarray, maximize: list[bool]) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    good = np.isfinite(values).all(axis=1)
    transformed = values.copy()
    for index, is_max in enumerate(maximize):
        if not is_max:
            transformed[:, index] *= -1.0
    result = np.zeros(len(values), dtype=bool)
    for i in np.flatnonzero(good):
        other = transformed[good]
        point = transformed[i]
        dominated = np.any(np.all(other >= point, axis=1) & np.any(other > point, axis=1))
        result[i] = not dominated
    return result


def _draw_boundary(
    ax: plt.Axes,
    shp_path: Path,
    field: str,
    value: str,
) -> tuple[float, float, float, float]:
    """Draw one published administrative boundary and return its bounding box."""
    reader = shapefile.Reader(str(shp_path))
    fields = [item[0] for item in reader.fields[1:]]
    if field not in fields:
        raise ValueError(f"Boundary field {field!r} is missing from {shp_path.name}.")
    field_index = fields.index(field)
    matches = [
        item
        for item in reader.iterShapeRecords()
        if str(item.record[field_index]).strip().lower() == value.lower()
    ]
    if not matches:
        raise ValueError(f"Boundary value {value!r} is missing from {shp_path.name}.")

    boxes: list[tuple[float, float, float, float]] = []
    for item in matches:
        shape = item.shape
        boxes.append(tuple(float(x) for x in shape.bbox))
        ends = list(shape.parts[1:]) + [len(shape.points)]
        for start, end in zip(shape.parts, ends):
            coordinates = np.asarray(shape.points[start:end], dtype=float)
            if len(coordinates) < 3:
                continue
            ax.add_patch(
                Polygon(
                    coordinates,
                    closed=True,
                    facecolor=TUM_LIGHT_GRAY,
                    edgecolor=TUM_GRAY,
                    linewidth=0.8,
                    zorder=0,
                )
            )
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def plot_task_map(tasks: pd.DataFrame, writer: FigureWriter) -> None:
    lon = _first_col(tasks, ["longitude", "lon", "task_longitude", "centroid_lon", "x"])
    lat = _first_col(tasks, ["latitude", "lat", "task_latitude", "centroid_lat", "y"])
    region = _first_col(tasks, ["region", "country", "study_region", "area"])
    priority = _first_col(tasks, ["priority", "priority_class", "priority_score", "class"])
    if not lon or not lat:
        raise ValueError("The task file needs latitude and longitude columns.")
    data = tasks.copy()
    _numeric(data, [lon, lat])
    data = data.dropna(subset=[lon, lat])
    if region is None:
        data["_region"] = np.where(data[lon] < 0, "California", "India")
        region = "_region"
    else:
        data[region] = data[region].astype(str).str.strip().str.title()
    if priority is None:
        data["_priority"] = "All tasks"
        priority = "_priority"

    areas = [x for x in ("California", "India") if x in data[region].unique()]
    if not areas:
        areas = list(data[region].dropna().unique())[:2]
    fig, axes = plt.subplots(1, len(areas), figsize=(10.0, 4.2), constrained_layout=True)
    axes = np.atleast_1d(axes)
    priority_values = list(data[priority].dropna().unique())
    for ax, area in zip(axes, areas):
        subset = data[data[region] == area]
        if area == "California":
            bounds = _draw_boundary(
                ax,
                MAP_DATA_ROOT / "us_states" / "cb_2025_us_state_500k.shp",
                "NAME",
                "California",
            )
        elif area == "India":
            bounds = _draw_boundary(
                ax,
                MAP_DATA_ROOT / "natural_earth" / "ne_50m_admin_0_countries.shp",
                "ADMIN",
                "India",
            )
        else:
            bounds = (
                float(subset[lon].min()),
                float(subset[lat].min()),
                float(subset[lon].max()),
                float(subset[lat].max()),
            )
        for index, value in enumerate(priority_values):
            points = subset[subset[priority] == value]
            if points.empty:
                continue
            label = str(value).replace("_", " ").title()
            ax.scatter(
                points[lon],
                points[lat],
                s=34,
                alpha=0.72,
                color=SERIES_COLORS[index % len(SERIES_COLORS)],
                marker=["o", "s", "^", "D"][index % 4],
                linewidth=0.35,
                edgecolor="white",
                label=label,
                zorder=2,
            )
        x_pad = max(0.4, (bounds[2] - bounds[0]) * 0.04)
        y_pad = max(0.4, (bounds[3] - bounds[1]) * 0.04)
        ax.set_xlim(bounds[0] - x_pad, bounds[2] + x_pad)
        ax.set_ylim(bounds[1] - y_pad, bounds[3] + y_pad)
        ax.set_xlabel("Longitude (degrees)")
        ax.set_ylabel("Latitude (degrees)")
        panel_label(ax, str(area))
        clean_axis(ax, grid="both")
        ax.set_aspect("equal", adjustable="datalim")
    handles, labels = axes[0].get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels, loc="outside lower center", ncol=min(4, len(labels)))
    writer.save(
        fig,
        number=1,
        stem="task_locations",
        kind="analysis",
        caption=(
            "Spatial distribution of the wildfire-observation tasks used for the California and India "
            "experiments. Marker shape and colour indicate task-priority class. The California outline "
            "is from the 2025 US Census cartographic boundary file; the India outline is from the Natural "
            "Earth 1:50 million admin-0 dataset."
        ),
        source="Task input file; US Census Bureau 2025 cartographic boundaries; Natural Earth 5.1.1",
        sample=f"{len(data):,} tasks",
    )


def plot_campaign_coverage(data: pd.DataFrame, writer: FigureWriter) -> None:
    complete = data.copy()
    if "case_id" in complete.columns:
        case_counts = complete.groupby("region")["case_id"].nunique()
    else:
        case_counts = complete.groupby("region").size()
    scenario_counts = complete.groupby("region")["scenario_id"].nunique()
    regions = _regions(complete)
    x = np.arange(len(regions))
    fig, axes = plt.subplots(1, 2, figsize=(9.0, 3.8), constrained_layout=True)
    values = [case_counts.get(region, 0) for region in regions]
    axes[0].bar(x, values, color=[REGION_COLORS.get(r, TUM_BLUE) for r in regions], width=0.62)
    axes[0].set_ylabel("Completed base architectures")
    axes[0].set_xticks(x, regions)
    for pos, value in zip(x, values):
        axes[0].text(pos, value, f"{int(value):,}", ha="center", va="bottom", fontsize=10.5)
    values = [scenario_counts.get(region, 0) for region in regions]
    axes[1].bar(x, values, color=[REGION_COLORS.get(r, TUM_BLUE) for r in regions], width=0.62)
    axes[1].set_ylabel("Evaluated scenario types")
    axes[1].set_xticks(x, regions)
    for pos, value in zip(x, values):
        axes[1].text(pos, value, f"{int(value):,}", ha="center", va="bottom", fontsize=10.5)
    panel_label(axes[0], "Architecture coverage")
    panel_label(axes[1], "Scenario coverage")
    for ax in axes:
        clean_axis(ax)
        ax.margins(y=0.15)
    writer.save(
        fig,
        number=2,
        stem="campaign_coverage",
        kind="analysis",
        caption=(
            "Coverage of the post-processed campaign by study region. The left panel counts distinct "
            "base architectures and the right panel counts distinct evaluated scenario types after the "
            "analysis quality gate."
        ),
        source="Canonical completed-row table",
        sample=f"{len(data):,} completed scenario rows",
    )


def plot_regional_cn_response(data: pd.DataFrame, writer: FigureWriter) -> None:
    nominal = _scenario(data, ["nominal_original", "nominal"])
    if nominal.empty:
        nominal = data[data.get("family", "").astype(str).str.lower().eq("nominal")].copy()
    metrics = [
        "observation_success_percent",
        "s100_surviving_before_deadline_percent",
        "mean_surviving_coverage_deadline_percent",
        "total_transferred_data_mbits",
    ]
    metrics = [m for m in metrics if m in nominal.columns]
    fig, axes = plt.subplots(2, 2, figsize=(10.2, 7.0), constrained_layout=True)
    for ax, metric in zip(axes.flat, metrics):
        summary = _quantile_summary(nominal, ["region", "cn_fraction_percent"], metric)
        for region in _regions(summary):
            part = summary[summary["region"] == region].sort_values("cn_fraction_percent")
            ax.plot(
                part["cn_fraction_percent"],
                part["mean"],
                marker=REGION_MARKERS.get(region, "o"),
                color=REGION_COLORS.get(region, TUM_GRAY),
                label=region,
            )
            ax.fill_between(
                part["cn_fraction_percent"].to_numpy(float),
                part["low"].to_numpy(float),
                part["high"].to_numpy(float),
                color=REGION_COLORS.get(region, TUM_GRAY),
                alpha=0.12,
                linewidth=0,
            )
        ax.set_xlabel("Central Node fraction (%)")
        ax.set_ylabel(METRIC_LABELS.get(metric, metric))
        clean_axis(ax)
    for ax in axes.flat[len(metrics) :]:
        ax.set_visible(False)
    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="outside lower center", ncol=len(labels))
    writer.save(
        fig,
        number=3,
        stem="regional_cn_response",
        kind="analysis",
        caption=(
            "Nominal performance as a function of the Central Node fraction for California and India. "
            "Lines show the architecture-level mean and shaded bands show the interquartile range."
        ),
        source="Canonical completed-row table, nominal scenario",
        sample=f"{len(nominal):,} architecture rows",
    )


def plot_cn_regression(data: pd.DataFrame, writer: FigureWriter) -> None:
    nominal = _scenario(data, ["nominal_original", "nominal"])
    metric = "observation_success_percent"
    summary = _quantile_summary(nominal, ["region", "cn_fraction_percent"], metric)
    regions = _regions(summary)
    fig, axes = plt.subplots(1, len(regions), figsize=(9.6, 3.9), constrained_layout=True)
    axes = np.atleast_1d(axes)
    model_rows: list[dict[str, object]] = []
    for ax, region in zip(axes, regions):
        part = summary[summary["region"] == region].sort_values("cn_fraction_percent")
        x = part["cn_fraction_percent"].to_numpy(float)
        y = part["mean"].to_numpy(float)
        ax.scatter(x, y, s=42, color=REGION_COLORS.get(region, TUM_BLUE), label="Observed mean")
        candidates: list[tuple[str, int, float, np.ndarray]] = []
        for name, degree in (("Linear", 1), ("Quadratic", 2), ("Cubic", 3)):
            if len(x) <= degree:
                continue
            coeff = np.polyfit(x, y, degree)
            fitted = np.polyval(coeff, x)
            residual = float(np.sum((y - fitted) ** 2))
            k = degree + 1
            aic = len(y) * np.log(max(residual / len(y), 1e-12)) + 2 * k
            candidates.append((name, degree, aic, coeff))
        best = min(candidates, key=lambda item: item[2])
        grid = np.linspace(np.nanmin(x), np.nanmax(x), 200)
        ax.plot(grid, np.polyval(best[3], grid), color=TUM_GRAY, linestyle="--", label="Selected fit")
        ax.set_xlabel("Central Node fraction (%)")
        ax.set_ylabel("Observation success (%)")
        panel_label(ax, region)
        clean_axis(ax)
        model_rows.extend(
            {"region": region, "model": name, "degree": degree, "AIC": aic, "selected": name == best[0]}
            for name, degree, aic, _ in candidates
        )
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="outside lower center", ncol=2)
    pd.DataFrame(model_rows).to_csv(writer.table_dir / "cn_model_comparison.csv", index=False)
    writer.save(
        fig,
        number=4,
        stem="cn_model_fit",
        kind="analysis",
        caption=(
            "Observed nominal task-success response to the Central Node fraction and the polynomial "
            "model selected by the Akaike information criterion in each region. Numerical model "
            "comparisons are provided in the companion table."
        ),
        source="Canonical completed-row table, nominal scenario",
        sample=f"{len(nominal):,} architecture rows",
        notes="No regression equation or software-generated title is embedded in the plot.",
    )


def plot_paired_regions(data: pd.DataFrame, writer: FigureWriter) -> None:
    subset = _scenario(data, ["unlimited_useful_deadline", "nominal_original"])
    metric = "observation_success_percent"
    if "case_id" not in subset.columns:
        raise ValueError("The paired-region figure requires case_id.")
    pair = subset.pivot_table(index="case_id", columns="region", values=metric, aggfunc="mean")
    pair = pair.dropna(subset=["California", "India"])
    fig, ax = plt.subplots(figsize=(5.6, 5.0), constrained_layout=True)
    ax.scatter(pair["California"], pair["India"], s=31, color=TUM_BLUE, alpha=0.62, edgecolor="none")
    limits = [
        float(np.nanmin(pair[["California", "India"]].to_numpy())) - 1,
        float(np.nanmax(pair[["California", "India"]].to_numpy())) + 1,
    ]
    ax.plot(limits, limits, color=TUM_GRAY, linestyle="--", linewidth=1.4, label="Equal regional result")
    ax.set_xlim(limits)
    ax.set_ylim(limits)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("California observation success (%)")
    ax.set_ylabel("India observation success (%)")
    clean_axis(ax, grid="both")
    ax.legend(loc="lower right")
    writer.save(
        fig,
        number=5,
        stem="paired_region_performance",
        kind="analysis",
        caption=(
            "Paired comparison of matched architectures across California and India. Each marker is one "
            "shared architecture; the diagonal denotes equal task-observation success in both regions."
        ),
        source="Canonical completed-row table, matched case identifiers",
        sample=f"{len(pair):,} matched architectures",
    )


def plot_two_region_pareto(data: pd.DataFrame, writer: FigureWriter) -> None:
    subset = _scenario(data, ["unlimited_useful_deadline"])
    if subset.empty:
        subset = _scenario(data, ["nominal_original"])
    metric = "s100_surviving_before_deadline_percent"
    resource = "total_transferred_data_mbits"
    fig, ax = plt.subplots(figsize=(7.3, 4.8), constrained_layout=True)
    pareto_rows: list[pd.DataFrame] = []
    for region in _regions(subset):
        part = subset[subset["region"] == region].dropna(subset=[metric, resource]).copy()
        part["pareto"] = _pareto_mask(part[[metric, resource]].to_numpy(), [True, False])
        pareto_rows.append(part)
        ax.scatter(
            part[resource],
            part[metric],
            s=24,
            alpha=0.28,
            color=REGION_COLORS.get(region, TUM_GRAY),
            marker=REGION_MARKERS.get(region, "o"),
        )
        front = part[part["pareto"]].sort_values(resource)
        ax.plot(
            front[resource],
            front[metric],
            color=REGION_COLORS.get(region, TUM_GRAY),
            marker=REGION_MARKERS.get(region, "o"),
            label=region,
        )
    if pareto_rows:
        pd.concat(pareto_rows, ignore_index=True).to_csv(
            writer.table_dir / "two_region_pareto_membership.csv", index=False
        )
    ax.set_xlabel("Transferred data (Mbit)")
    ax.set_ylabel("Tasks completed by deadline (%)")
    clean_axis(ax, grid="both")
    ax.legend(loc="best")
    writer.save(
        fig,
        number=6,
        stem="two_region_pareto_landscape",
        kind="analysis",
        caption=(
            "Performance--resource trade space for both study regions. Faint markers show all evaluated "
            "architectures and connected markers show the non-dominated frontier when deadline completion "
            "is maximised and transferred data is minimised."
        ),
        source="Canonical completed-row table, unlimited-useful-deadline scenario",
        sample=f"{len(subset):,} architecture rows",
    )


def plot_cn_performance(
    data: pd.DataFrame,
    writer: FigureWriter,
    task_population_contract: pd.DataFrame | None = None,
) -> None:
    subset = _scenario(data, ["unlimited_useful_deadline"])
    if subset.empty:
        subset = _scenario(data, ["nominal_original"])
    metrics = [
        "s100_surviving_before_deadline_percent",
        "mean_surviving_coverage_deadline_percent",
        "total_transferred_data_mbits",
        "node_sent_data_gini",
    ]
    metrics = [m for m in metrics if m in subset.columns]
    fig, axes = plt.subplots(2, 2, figsize=(10.4, 7.1), constrained_layout=True)
    combinations = subset[["region", "n_satellites"]].drop_duplicates().sort_values(["region", "n_satellites"])
    size_styles = {
        60: ("-", "o"),
        120: ("--", "s"),
        180: (":", "^"),
    }
    legend_handles: list[Line2D] = []
    for ax, metric in zip(axes.flat, metrics):
        summary = _quantile_summary(subset, ["region", "n_satellites", "cn_fraction_percent"], metric)
        for _, row in combinations.reset_index(drop=True).iterrows():
            region = str(row["region"])
            satellites = int(row["n_satellites"])
            part = summary[(summary["region"] == region) & (summary["n_satellites"] == satellites)]
            if part.empty:
                continue
            part = part.sort_values("cn_fraction_percent")
            style, marker = size_styles.get(satellites, ("-.", "D"))
            color = REGION_COLORS.get(region, TUM_GRAY)
            ax.plot(
                part["cn_fraction_percent"],
                part["mean"],
                color=color,
                linestyle=style,
                marker=marker,
                markersize=3.5,
                markevery=max(1, len(part) // 6),
            )
            if ax is axes.flat[0]:
                legend_handles.append(
                    Line2D(
                        [0],
                        [0],
                        color=color,
                        linestyle=style,
                        marker=marker,
                        markersize=4,
                        label=f"{region}, {satellites} satellites",
                    )
                )
        ax.set_xlabel("Central Node fraction (%)")
        ax.set_ylabel(METRIC_LABELS.get(metric, metric))
        clean_axis(ax)
    for ax in axes.flat[len(metrics) :]:
        ax.set_visible(False)
    fig.legend(handles=legend_handles, loc="outside lower center", ncol=3)
    india_180 = subset[(subset["region"] == "India") & (pd.to_numeric(subset["n_satellites"], errors="coerce") == 180)]
    note = "No India 180-satellite rows were present in the supplied analysis table."
    if not india_180.empty:
        note = (
            "The India 180-satellite layer is included in the main comparison. Check the task-population "
            "contract and state any mismatch explicitly in the caption and limitations text."
        )
        if task_population_contract is not None and not task_population_contract.empty:
            contract = task_population_contract.copy()
            contract["n_satellites"] = pd.to_numeric(contract.get("n_satellites"), errors="coerce")
            relevant = contract[(contract["region"].astype(str) == "India") & (contract["n_satellites"] == 180)]
            if not relevant.empty:
                intended = pd.to_numeric(relevant.get("intended_task_count"), errors="coerce").dropna()
                actual_min = pd.to_numeric(relevant.get("minimum_task_count"), errors="coerce").dropna()
                compliant = relevant.get("all_rows_use_intended_task_count", pd.Series(False, index=relevant.index)).astype(str).str.lower().eq("true")
                intended_value = int(intended.mode().iloc[0]) if not intended.empty else 0
                actual_value = int(actual_min.mode().iloc[0]) if not actual_min.empty else 0
                note = (
                    f"India 180-satellite task-population audit: {len(relevant)} architecture cases; "
                    f"{int(compliant.sum())} use the intended {intended_value}-task population throughout, "
                    f"while {int((~compliant).sum())} contain a mismatch (most commonly {actual_value} tasks). "
                    "The layer remains visible but must be qualified in the report."
                )
    writer.save(
        fig,
        number=7,
        stem="central_node_performance",
        kind="analysis",
        caption=(
            "System response to the Central Node fraction, separated by region and constellation size. "
            "The panels report deadline completion, mean deadline coverage, transferred data, and the "
            "inequality of transmitted load across nodes."
        ),
        source="Canonical completed-row table, unlimited-useful-deadline scenario",
        sample=f"{len(subset):,} architecture rows",
        notes=note,
    )


def plot_factor_effects(data: pd.DataFrame, writer: FigureWriter) -> None:
    subset = _scenario(data, ["unlimited_useful_deadline"])
    if subset.empty:
        subset = _scenario(data, ["nominal_original"])
    metric = "s100_surviving_before_deadline_percent"
    factors = [
        ("n_satellites", "Satellites"),
        ("n_planes", "Orbital planes"),
        ("altitude_km", "Altitude (km)"),
        ("inclination_deg", "Inclination (degrees)"),
        ("cn_fraction_percent", "Central Node fraction (%)"),
    ]
    factors = [(column, label) for column, label in factors if column in subset.columns]
    fig, axes = plt.subplots(2, 3, figsize=(11.0, 6.8), constrained_layout=True)
    for ax, (column, label) in zip(axes.flat, factors):
        summary = _quantile_summary(subset, ["region", column], metric)
        for region in _regions(summary):
            part = summary[summary["region"] == region].sort_values(column)
            ax.plot(
                part[column],
                part["mean"],
                color=REGION_COLORS.get(region, TUM_GRAY),
                marker=REGION_MARKERS.get(region, "o"),
                label=region,
            )
            ax.fill_between(
                part[column].to_numpy(float),
                part["low"].to_numpy(float),
                part["high"].to_numpy(float),
                color=REGION_COLORS.get(region, TUM_GRAY),
                alpha=0.10,
                linewidth=0,
            )
        ax.set_xlabel(label)
        ax.set_ylabel("Tasks completed by deadline (%)")
        clean_axis(ax)
    for ax in axes.flat[len(factors) :]:
        ax.set_visible(False)
    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="outside lower center", ncol=len(labels))
    writer.save(
        fig,
        number=8,
        stem="architecture_main_effects",
        kind="analysis",
        caption=(
            "Marginal architecture-factor effects on deadline completion. Each point is the mean over the "
            "other evaluated factors and the shaded range is the interquartile interval; the figure is "
            "descriptive rather than a causal decomposition."
        ),
        source="Canonical completed-row table, unlimited-useful-deadline scenario",
        sample=f"{len(subset):,} architecture rows",
    )


def plot_spearman_heatmap(data: pd.DataFrame, writer: FigureWriter) -> None:
    subset = _scenario(data, ["unlimited_useful_deadline"])
    if subset.empty:
        subset = _scenario(data, ["nominal_original"])
    columns = [
        "cn_fraction_percent",
        "observation_success_percent",
        "s100_surviving_before_deadline_percent",
        "mean_surviving_coverage_deadline_percent",
        "p100_t100_surviving_min",
        "total_transferred_data_mbits",
        "used_capacity_percent",
        "node_sent_data_gini",
    ]
    columns = [c for c in columns if c in subset.columns]
    labels = {
        "cn_fraction_percent": "Central Node fraction",
        "observation_success_percent": "Observation success",
        "s100_surviving_before_deadline_percent": "Deadline completion",
        "mean_surviving_coverage_deadline_percent": "Deadline coverage",
        "p100_t100_surviving_min": "Completion time",
        "total_transferred_data_mbits": "Transferred data",
        "used_capacity_percent": "Capacity use",
        "node_sent_data_gini": "Load inequality",
    }
    corr = subset[columns].corr(method="spearman")
    corr.to_csv(writer.table_dir / "spearman_correlation_matrix.csv")
    fig, ax = plt.subplots(figsize=(7.2, 6.2), constrained_layout=True)
    image = ax.imshow(corr.to_numpy(), vmin=-1, vmax=1, cmap="RdBu_r")
    tick_labels = [labels[c] for c in columns]
    ax.set_xticks(np.arange(len(columns)), tick_labels, rotation=42, ha="right")
    ax.set_yticks(np.arange(len(columns)), tick_labels)
    for i in range(len(columns)):
        for j in range(len(columns)):
            value = corr.iat[i, j]
            if np.isfinite(value):
                colour = "white" if abs(value) >= 0.58 else TUM_GRAY
                ax.text(j, i, f"{value:.2f}", ha="center", va="center", fontsize=9.2, color=colour)
    colorbar = fig.colorbar(image, ax=ax, fraction=0.046, pad=0.03)
    colorbar.set_label("Spearman rank correlation")
    ax.tick_params(length=0)
    writer.save(
        fig,
        number=9,
        stem="spearman_metric_relationships",
        kind="analysis",
        caption=(
            "Spearman rank correlations among the principal architecture and outcome variables. Positive "
            "values indicate that both variables tend to increase together; negative values indicate an "
            "opposing monotonic relation. Correlation describes association and is not interpreted as causation."
        ),
        source="Canonical completed-row table, unlimited-useful-deadline scenario",
        sample=f"{len(subset):,} architecture rows",
    )


def plot_policy_ablation(data: pd.DataFrame, writer: FigureWriter) -> None:
    policy = data[data["scenario_id"].astype(str).str.lower().str.contains("policy_")].copy()
    policy["Policy"] = policy["scenario_id"].map(_human_policy)
    metrics = [
        "observation_success_percent",
        "s100_surviving_before_deadline_percent",
        "total_transferred_data_mbits",
    ]
    metrics = [m for m in metrics if m in policy.columns]
    order = ["Central Node routing", "Peer routing", "Hybrid routing"]
    fig, axes = plt.subplots(1, len(metrics), figsize=(11.0, 3.9), constrained_layout=True)
    axes = np.atleast_1d(axes)
    for ax, metric in zip(axes, metrics):
        summary = policy.groupby(["Policy", "region"])[metric].mean().reset_index()
        x = np.arange(len(order))
        regions = _regions(summary)
        width = 0.34
        for index, region in enumerate(regions):
            values = [
                summary[(summary["Policy"] == name) & (summary["region"] == region)][metric].mean()
                for name in order
            ]
            offset = (index - (len(regions) - 1) / 2) * width
            ax.bar(
                x + offset,
                values,
                width=width,
                color=REGION_COLORS.get(region, TUM_GRAY),
                label=region,
            )
        ax.set_xticks(x, [name.replace(" routing", "") for name in order], rotation=24, ha="right")
        ax.set_ylabel(METRIC_LABELS.get(metric, metric))
        clean_axis(ax)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="outside lower center", ncol=len(labels))
    writer.save(
        fig,
        number=10,
        stem="routing_policy_ablation",
        kind="analysis",
        caption=(
            "Routing-policy ablation across the Central Node, peer-only, and hybrid strategies. Bars report "
            "regional means for observation success, deadline completion, and transferred data."
        ),
        source="Canonical completed-row table, routing-policy scenarios",
        sample=f"{len(policy):,} scenario rows",
    )


def plot_task_size_sensitivity(data: pd.DataFrame, writer: FigureWriter) -> None:
    family = data["family"].astype(str) if "family" in data.columns else pd.Series("", index=data.index)
    scenario = data["scenario_id"].astype(str)
    relevant = family.str.contains("task_size", case=False, na=False) | scenario.str.contains(
        "task_size", case=False, na=False
    )
    task_size = data[relevant].copy()
    multiplier = pd.to_numeric(task_size.get("task_size_multiplier", np.nan), errors="coerce")
    if isinstance(multiplier, pd.Series):
        task_size["task_size_multiplier"] = multiplier
    if "task_size_multiplier" not in task_size.columns or task_size["task_size_multiplier"].isna().all():
        extracted = task_size["scenario_id"].astype(str).str.extract(
            r"([0-9]+(?:\.[0-9]+)?)x", expand=False
        )
        task_size["task_size_multiplier"] = pd.to_numeric(extracted, errors="coerce")
    if task_size.empty:
        pattern = data["scenario_id"].astype(str).str.extract(r"([0-9]+(?:\.[0-9]+)?)x", expand=False)
        task_size = data[pattern.notna()].copy()
        task_size["task_size_multiplier"] = pd.to_numeric(pattern[pattern.notna()], errors="coerce")
    metrics = [
        "observation_success_percent",
        "s100_surviving_before_deadline_percent",
        "total_transferred_data_mbits",
    ]
    metrics = [m for m in metrics if m in task_size.columns]
    fig, axes = plt.subplots(1, len(metrics), figsize=(11.0, 3.8), constrained_layout=True)
    axes = np.atleast_1d(axes)
    for ax, metric in zip(axes, metrics):
        summary = _quantile_summary(task_size, ["region", "task_size_multiplier"], metric)
        for region in _regions(summary):
            part = summary[summary["region"] == region].sort_values("task_size_multiplier")
            ax.plot(
                part["task_size_multiplier"],
                part["mean"],
                marker=REGION_MARKERS.get(region, "o"),
                color=REGION_COLORS.get(region, TUM_GRAY),
                label=region,
            )
        ax.set_xlabel("Task-data multiplier")
        ax.set_ylabel(METRIC_LABELS.get(metric, metric))
        clean_axis(ax)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="outside lower center", ncol=len(labels))
    writer.save(
        fig,
        number=11,
        stem="task_size_sensitivity",
        kind="analysis",
        caption=(
            "Sensitivity of performance and network load to the task-data volume. The horizontal axis is "
            "the task-size multiplier relative to the nominal task definition."
        ),
        source="Canonical completed-row table, task-size scenarios",
        sample=f"{len(task_size):,} scenario rows",
    )


def _priority_long(data: pd.DataFrame) -> pd.DataFrame:
    candidates = {
        "Critical": ["critical_observation_success_percent", "priority_critical_success_percent"],
        "High": ["high_observation_success_percent", "priority_high_success_percent"],
        "Normal": ["normal_observation_success_percent", "priority_normal_success_percent"],
        "Low": ["low_observation_success_percent", "priority_low_success_percent"],
    }
    pieces: list[pd.DataFrame] = []
    for label, names in candidates.items():
        column = next((name for name in names if name in data.columns), None)
        if column:
            piece = data[["region", column]].copy()
            piece["priority"] = label
            piece["value"] = pd.to_numeric(piece[column], errors="coerce")
            pieces.append(piece[["region", "priority", "value"]])
    if pieces:
        return pd.concat(pieces, ignore_index=True)
    # If detailed priority fields are unavailable, use task priority score as a defensible proxy only
    # when it is already present in the canonical analysis.
    priority_column = _first_col(data, ["priority_class", "priority", "task_priority"])
    if priority_column and "observation_success_percent" in data.columns:
        return data[["region", priority_column, "observation_success_percent"]].rename(
            columns={priority_column: "priority", "observation_success_percent": "value"}
        )
    return pd.DataFrame(columns=["region", "priority", "value"])


def plot_priority_outcomes(
    data: pd.DataFrame, writer: FigureWriter, priority_summary: pd.DataFrame | None = None
) -> None:
    nominal = _scenario(data, ["unlimited_useful_deadline", "nominal_original"])
    if priority_summary is not None and not priority_summary.empty:
        summary = priority_summary.copy()
        if "scenario_id" in summary.columns:
            preferred = summary[summary["scenario_id"].astype(str).eq("unlimited_useful_deadline")]
            if preferred.empty:
                preferred = summary[summary["scenario_id"].astype(str).eq("nominal_original")]
            if not preferred.empty:
                summary = preferred
        priority_col = _first_col(summary, ["priority_class", "priority"])
        metrics = [
            ("observation_success_percent_mean", "Observation success (%)"),
            ("s100_before_deadline_percent_mean", "Tasks completed by deadline (%)"),
        ]
        metrics = [(column, label) for column, label in metrics if column in summary.columns]
        if priority_col and "region" in summary.columns and metrics:
            priorities = [
                value
                for value in ("Critical", "High", "Medium", "Normal", "Low")
                if value in summary[priority_col].astype(str).unique()
            ]
            priorities += sorted(
                set(summary[priority_col].dropna().astype(str).unique()) - set(priorities)
            )
            fig, axes = plt.subplots(1, len(metrics), figsize=(9.4, 3.8), constrained_layout=True)
            axes = np.atleast_1d(axes)
            regions = _regions(summary)
            x = np.arange(len(priorities))
            width = 0.34
            for ax, (column, label) in zip(axes, metrics):
                for index, region in enumerate(regions):
                    part = summary[summary["region"] == region].set_index(priority_col)
                    values = [pd.to_numeric(part[column].get(priority, np.nan), errors="coerce") for priority in priorities]
                    offset = (index - (len(regions) - 1) / 2) * width
                    ax.bar(x + offset, values, width, color=REGION_COLORS.get(region, TUM_GRAY), label=region)
                ax.set_xticks(x, priorities, rotation=20, ha="right")
                ax.set_ylabel(label)
                clean_axis(ax)
            handles, labels = axes[0].get_legend_handles_labels()
            fig.legend(handles, labels, loc="outside lower center", ncol=len(labels))
            counts = (
                pd.to_numeric(summary["case_count"], errors="coerce").fillna(0)
                if "case_count" in summary.columns
                else pd.Series(np.ones(len(summary)), index=summary.index)
            )
            sample = f"{int(counts.sum()):,} case-priority summaries"
            summary.to_csv(writer.table_dir / "priority_outcome_summary.csv", index=False)
            missing_pairs: list[str] = []
            for region in regions:
                for priority in priorities:
                    row = summary[
                        (summary["region"] == region)
                        & (summary[priority_col].astype(str) == priority)
                    ]
                    if row.empty or row[[column for column, _ in metrics]].isna().to_numpy().all():
                        missing_pairs.append(f"{region} {priority}")
            note = (
                "No bar is drawn where the supplied task population contains no usable observations: "
                + ", ".join(missing_pairs)
                + "."
                if missing_pairs
                else "All displayed region-priority combinations contain usable observations."
            )
            writer.save(
                fig,
                number=12,
                stem="priority_specific_outcomes",
                kind="analysis",
                caption=(
                    "Observation success and deadline completion by task-priority class. The comparison tests "
                    "whether the routing and scheduling logic preserves the intended service differentiation "
                    "across regions."
                ),
                source="Regional priority case-metric summaries",
                sample=sample,
                notes=note,
            )
            return
    long = _priority_long(nominal)
    if long.empty:
        # Deliberately draw an honest availability panel instead of inventing a priority breakdown.
        fig, ax = plt.subplots(figsize=(7.2, 2.7), constrained_layout=True)
        ax.axis("off")
        ax.text(
            0.5,
            0.5,
            "Priority-stratified task outcomes were not present\nin the supplied canonical table.",
            ha="center",
            va="center",
            fontsize=13,
            color=TUM_GRAY,
        )
        sample = "No priority-stratified outcome rows"
    else:
        summary = long.groupby(["region", "priority"])["value"].agg(["mean", "median", "count"]).reset_index()
        priorities = list(summary["priority"].dropna().astype(str).unique())
        fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.8), constrained_layout=True)
        x = np.arange(len(priorities))
        regions = _regions(summary)
        width = 0.34
        for index, region in enumerate(regions):
            part = summary[summary["region"] == region].set_index("priority")
            means = [part["mean"].get(p, np.nan) for p in priorities]
            medians = [part["median"].get(p, np.nan) for p in priorities]
            offset = (index - (len(regions) - 1) / 2) * width
            axes[0].bar(x + offset, means, width, color=REGION_COLORS.get(region, TUM_GRAY), label=region)
            axes[1].plot(x, medians, marker=REGION_MARKERS.get(region, "o"), color=REGION_COLORS.get(region, TUM_GRAY), label=region)
        for ax in axes:
            ax.set_xticks(x, priorities, rotation=20, ha="right")
            ax.set_ylabel("Observation success (%)")
            clean_axis(ax)
        panel_label(axes[0], "Mean")
        panel_label(axes[1], "Median")
        handles, labels = axes[0].get_legend_handles_labels()
        fig.legend(handles, labels, loc="outside lower center", ncol=len(labels))
        sample = f"{len(long):,} priority-stratified observations"
        summary.to_csv(writer.table_dir / "priority_outcome_summary.csv", index=False)
    writer.save(
        fig,
        number=12,
        stem="priority_specific_outcomes",
        kind="analysis",
        caption=(
            "Task-observation outcomes by priority class. The comparison tests whether the routing and "
            "scheduling logic preserves the intended service differentiation across regions."
        ),
        source="Canonical completed-row table, priority-stratified fields",
        sample=sample,
        notes="If the input lacks priority-stratified fields, the figure explicitly reports that absence.",
    )


def plot_robustness(data: pd.DataFrame, writer: FigureWriter) -> None:
    families = {
        "random_satellite_failure",
        "random_cn_failure",
        "targeted_cn_failure",
        "link_window_outage",
        "capacity_degradation",
        "ground_station_outage",
        "combined_stress",
    }
    robust = data[data.get("family", "").astype(str).isin(families)].copy()
    level = "failure_level_percent"
    if level not in robust.columns:
        level = _first_col(robust, ["stress_level_percent", "severity_percent", "failure_percent"]) or "failure_level_percent"
    if level not in robust.columns:
        robust[level] = np.nan
    metric = "s100_surviving_before_deadline_percent"
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.5), constrained_layout=True)
    for ax, region in zip(axes, ("California", "India")):
        region_data = robust[robust["region"] == region]
        summary = region_data.groupby(["family", level])[metric].mean().reset_index()
        for idx, family in enumerate(sorted(summary["family"].unique())):
            part = summary[summary["family"] == family].sort_values(level)
            ax.plot(
                part[level],
                part[metric],
                marker=["o", "s", "^", "D", "v", "P", "X"][idx % 7],
                color=SERIES_COLORS[idx % len(SERIES_COLORS)],
                label=_human_failure(family),
            )
        ax.set_xlabel("Failure or stress severity (%)")
        ax.set_ylabel("Tasks completed by deadline (%)")
        panel_label(ax, region)
        clean_axis(ax)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="outside lower center", ncol=3)
    writer.save(
        fig,
        number=13,
        stem="robustness_response",
        kind="analysis",
        caption=(
            "Retention of deadline performance under component failures, link/capacity disturbances, and "
            "combined stress. Each line is the regional mean at the indicated severity."
        ),
        source="Canonical completed-row table, robustness scenarios",
        sample=f"{len(robust):,} robustness rows",
    )


def plot_unlimited_pareto(data: pd.DataFrame, writer: FigureWriter) -> None:
    subset = _scenario(data, ["unlimited_useful_deadline"])
    metric = "s100_surviving_before_deadline_percent"
    resource = "total_transferred_data_mbits"
    fig, axes = plt.subplots(1, 2, figsize=(10.1, 4.2), constrained_layout=True)
    pareto: list[pd.DataFrame] = []
    for ax, region in zip(axes, ("California", "India")):
        part = subset[subset["region"] == region].dropna(subset=[metric, resource]).copy()
        part["pareto"] = _pareto_mask(part[[metric, resource]].to_numpy(), [True, False])
        pareto.append(part)
        sizes = 20 + 0.18 * pd.to_numeric(part.get("n_satellites", 60), errors="coerce").fillna(60)
        ax.scatter(part[resource], part[metric], s=sizes, color=TUM_LIGHT_BLUE, alpha=0.35, edgecolor="none")
        front = part[part["pareto"]].sort_values(resource)
        ax.scatter(front[resource], front[metric], s=52, color=TUM_ORANGE, edgecolor="white", linewidth=0.6, label="Non-dominated")
        ax.plot(front[resource], front[metric], color=TUM_ORANGE, linewidth=1.2)
        ax.set_xlabel("Transferred data (Mbit)")
        ax.set_ylabel("Tasks completed by deadline (%)")
        panel_label(ax, region)
        clean_axis(ax, grid="both")
    if pareto:
        pd.concat(pareto, ignore_index=True).to_csv(writer.table_dir / "unlimited_pareto_membership.csv", index=False)
    axes[0].legend(loc="best")
    writer.save(
        fig,
        number=14,
        stem="unlimited_policy_pareto",
        kind="analysis",
        caption=(
            "Unlimited-policy trade space within each study region. Marker size represents constellation "
            "size; highlighted points are non-dominated with respect to deadline completion and total "
            "transferred data."
        ),
        source="Canonical completed-row table, unlimited-useful-deadline scenario",
        sample=f"{len(subset):,} architecture rows",
    )


def _normalise(series: pd.Series, benefit: bool = True) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    span = values.max() - values.min()
    if not np.isfinite(span) or span == 0:
        score = pd.Series(np.full(len(values), 0.5), index=values.index)
    else:
        score = (values - values.min()) / span
    return score if benefit else 1.0 - score


def plot_weight_sweep(data: pd.DataFrame, writer: FigureWriter) -> None:
    subset = _scenario(data, ["unlimited_useful_deadline"])
    if subset.empty:
        subset = _scenario(data, ["nominal_original"])
    subset = subset.dropna(subset=["s100_surviving_before_deadline_percent", "total_transferred_data_mbits"]).copy()
    subset["performance_score"] = _normalise(subset["s100_surviving_before_deadline_percent"], True)
    subset["efficiency_score"] = _normalise(subset["total_transferred_data_mbits"], False)
    rows: list[dict[str, object]] = []
    for region in _regions(subset):
        part = subset[subset["region"] == region]
        for weight in np.linspace(0, 1, 101):
            score = weight * part["performance_score"] + (1 - weight) * part["efficiency_score"]
            winner = part.loc[score.idxmax()]
            rows.append(
                {
                    "region": region,
                    "performance_weight": weight,
                    "n_satellites": winner.get("n_satellites", np.nan),
                    "cn_fraction_percent": winner.get("cn_fraction_percent", np.nan),
                    "case_id": winner.get("case_id", ""),
                }
            )
    sweep = pd.DataFrame(rows)
    sweep.to_csv(writer.table_dir / "objective_weight_sweep.csv", index=False)
    fig, axes = plt.subplots(1, 2, figsize=(10.0, 4.0), constrained_layout=True)
    for region in _regions(sweep):
        part = sweep[sweep["region"] == region]
        axes[0].step(part["performance_weight"], part["n_satellites"], where="post", color=REGION_COLORS.get(region, TUM_GRAY), label=region)
        axes[1].step(part["performance_weight"], part["cn_fraction_percent"], where="post", color=REGION_COLORS.get(region, TUM_GRAY), label=region)
    axes[0].set_ylabel("Selected constellation size")
    axes[1].set_ylabel("Selected Central Node fraction (%)")
    constellation_sizes = sorted(pd.to_numeric(subset.get("n_satellites"), errors="coerce").dropna().unique())
    if constellation_sizes:
        axes[0].set_yticks(constellation_sizes)
        padding = max(10.0, 0.08 * (max(constellation_sizes) - min(constellation_sizes) or max(constellation_sizes)))
        axes[0].set_ylim(min(constellation_sizes) - padding, max(constellation_sizes) + padding)
    for ax in axes:
        ax.set_xlabel("Weight assigned to performance")
        clean_axis(ax)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="outside lower center", ncol=len(labels))
    writer.save(
        fig,
        number=15,
        stem="objective_weight_sweep",
        kind="analysis",
        caption=(
            "Architecture selected as the objective weight shifts from transfer efficiency to deadline "
            "performance. The step changes reveal decision thresholds rather than implying a unique global optimum."
        ),
        source="Derived from the canonical completed-row table",
        sample=f"{len(subset):,} candidate architecture rows; 101 weights per region",
        notes="Exact winning case identifiers are kept in the companion table, not printed in the figure.",
    )


def plot_winner_frequency(data: pd.DataFrame, writer: FigureWriter) -> None:
    subset = _scenario(data, ["unlimited_useful_deadline"])
    if subset.empty:
        subset = _scenario(data, ["nominal_original"])
    subset = subset.dropna(subset=["s100_surviving_before_deadline_percent", "total_transferred_data_mbits"]).copy()
    rng = np.random.default_rng(20261004)
    winner_rows: list[dict[str, object]] = []
    for region in _regions(subset):
        part = subset[subset["region"] == region].copy()
        part["performance_score"] = _normalise(part["s100_surviving_before_deadline_percent"], True)
        part["efficiency_score"] = _normalise(part["total_transferred_data_mbits"], False)
        part["balance_score"] = _normalise(part.get("node_sent_data_gini", pd.Series(0.5, index=part.index)), False)
        count: dict[int, int] = {}
        for weights in rng.dirichlet([1, 1, 1], size=4000):
            score = (
                weights[0] * part["performance_score"]
                + weights[1] * part["efficiency_score"]
                + weights[2] * part["balance_score"]
            )
            index = int(score.idxmax())
            count[index] = count.get(index, 0) + 1
        ranked = sorted(count.items(), key=lambda item: item[1], reverse=True)[:6]
        for rank, (index, wins) in enumerate(ranked, start=1):
            row = part.loc[index]
            winner_rows.append(
                {
                    "region": region,
                    "rank": rank,
                    "plot_label": f"{region[0]}{rank}",
                    "wins": wins,
                    "frequency_percent": 100 * wins / 4000,
                    "case_id": row.get("case_id", ""),
                    "n_satellites": row.get("n_satellites", np.nan),
                    "n_planes": row.get("n_planes", np.nan),
                    "altitude_km": row.get("altitude_km", np.nan),
                    "inclination_deg": row.get("inclination_deg", np.nan),
                    "cn_fraction_percent": row.get("cn_fraction_percent", np.nan),
                }
            )
    winners = pd.DataFrame(winner_rows)
    winners.to_csv(writer.table_dir / "winner_frequency_architecture_key.csv", index=False)
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 4.4), constrained_layout=True)
    for ax, region in zip(axes, ("California", "India")):
        part = winners[winners["region"] == region].sort_values("frequency_percent")
        ax.barh(part["plot_label"], part["frequency_percent"], color=REGION_COLORS.get(region, TUM_BLUE))
        ax.set_xlabel("Selection frequency (%)")
        ax.set_ylabel("Candidate code")
        panel_label(ax, region)
        clean_axis(ax, grid="x")
        for y, value in enumerate(part["frequency_percent"]):
            ax.text(value, y, f" {value:.1f}", va="center", fontsize=10)
    writer.save(
        fig,
        number=16,
        stem="monte_carlo_winner_frequency",
        kind="analysis",
        caption=(
            "Frequency with which the leading architectures are selected under 4,000 random combinations "
            "of performance, transfer-efficiency, and load-balance weights per region. Candidate codes are "
            "mapped to full architecture definitions in the companion table."
        ),
        source="Monte Carlo objective-weight analysis derived from the canonical completed-row table",
        sample="4,000 random weight vectors per region",
        notes="Short candidate codes prevent case identifiers and long architecture names from cluttering the plot.",
    )


def generate_analytical_figures(
    data: pd.DataFrame,
    tasks: pd.DataFrame,
    writer: FigureWriter,
    priority_summary: pd.DataFrame | None = None,
    task_population_contract: pd.DataFrame | None = None,
) -> None:
    """Generate the complete set of 16 analytical report figures."""

    required = {"region", "scenario_id", "cn_fraction_percent"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"Canonical table is missing required columns: {sorted(missing)}")
    _numeric(
        data,
        [
            "n_satellites",
            "n_planes",
            "altitude_km",
            "inclination_deg",
            "cn_fraction_percent",
            "n_central_nodes",
            *METRIC_LABELS.keys(),
            "task_size_multiplier",
            "failure_level_percent",
        ],
    )
    data["region"] = data["region"].astype(str).str.strip().str.title()
    functions = [
        lambda: plot_task_map(tasks, writer),
        lambda: plot_campaign_coverage(data, writer),
        lambda: plot_regional_cn_response(data, writer),
        lambda: plot_cn_regression(data, writer),
        lambda: plot_paired_regions(data, writer),
        lambda: plot_two_region_pareto(data, writer),
        lambda: plot_cn_performance(data, writer, task_population_contract),
        lambda: plot_factor_effects(data, writer),
        lambda: plot_spearman_heatmap(data, writer),
        lambda: plot_policy_ablation(data, writer),
        lambda: plot_task_size_sensitivity(data, writer),
        lambda: plot_priority_outcomes(data, writer, priority_summary),
        lambda: plot_robustness(data, writer),
        lambda: plot_unlimited_pareto(data, writer),
        lambda: plot_weight_sweep(data, writer),
        lambda: plot_winner_frequency(data, writer),
    ]
    for function in functions:
        function()

