from __future__ import annotations

import argparse
import gc
import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyarrow.parquet as pq


SCENARIOS = {"nominal_original", "unlimited_useful_deadline"}
EVENTS = (
    ("follow_up_observation", "observed_before_deadline", "observation_latency_min"),
    ("full_dissemination_S100", "s100_surviving_before_deadline", "t100_surviving_min"),
)
PRIORITY_HORIZONS = {"Critical": 30.0, "High": 60.0, "Medium": 180.0, "Low": 360.0}
PRIORITY_ORDER = ["Critical", "High", "Medium", "Low"]


def base_id(case_id: str) -> str:
    return re.sub(r"_CN\d{3}(?=_F\d+$)", "", str(case_id))


def cn_fraction(case_id: str) -> float:
    match = re.search(r"_CN(\d{3})(?=_F\d+$)", str(case_id))
    if not match:
        raise ValueError(f"Cannot parse CN fraction from {case_id}")
    return float(int(match.group(1)))


def km_curve(durations: np.ndarray, events: np.ndarray, horizon: float) -> tuple[pd.DataFrame, float, float]:
    valid = np.isfinite(durations) & (durations >= 0.0)
    durations = np.minimum(durations[valid], horizon)
    events = events[valid].astype(int)
    event_times = sorted(np.unique(durations[events == 1]).tolist())
    survival = 1.0
    previous = 0.0
    rmst = 0.0
    rows = [{"time_min": 0.0, "survival": 1.0, "event_probability": 0.0, "at_risk": len(durations), "events": 0, "censored": 0}]
    for time in event_times:
        rmst += survival * (time - previous)
        at_risk = int(np.sum(durations >= time - 1e-12))
        observed = int(np.sum((np.abs(durations - time) <= 1e-12) & (events == 1)))
        censored = int(np.sum((np.abs(durations - time) <= 1e-12) & (events == 0)))
        if at_risk:
            survival *= 1.0 - observed / at_risk
        rows.append(
            {
                "time_min": time,
                "survival": survival,
                "event_probability": 1.0 - survival,
                "at_risk": at_risk,
                "events": observed,
                "censored": censored,
            }
        )
        previous = time
    rmst += survival * max(0.0, horizon - previous)
    return pd.DataFrame(rows), float(1.0 - survival), float(rmst)


def bootstrap_mean(values: np.ndarray, draws: int, rng: np.random.Generator) -> tuple[float, float, float]:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return np.nan, np.nan, np.nan
    estimates = np.empty(draws, dtype=float)
    for index in range(draws):
        estimates[index] = rng.choice(values, size=len(values), replace=True).mean()
    return float(values.mean()), float(np.quantile(estimates, 0.025)), float(np.quantile(estimates, 0.975))


def read_region_events(region_root: Path, region_name: str) -> pd.DataFrame:
    paths = sorted(region_root.rglob("task_metrics_part_*.parquet"))
    print(f"Reading {len(paths):,} task-metric partitions for {region_name}", flush=True)
    wanted = {
        "case_id",
        "region",
        "scenario_id",
        "task_id",
        "priority_class",
        "created_time_sec",
        "deadline_time_sec",
        "observed_before_deadline",
        "observation_latency_min",
        "s100_surviving_before_deadline",
        "t100_surviving_min",
    }
    pieces: list[pd.DataFrame] = []
    for index, path in enumerate(paths, start=1):
        available = set(pq.read_schema(path).names)
        use = sorted(wanted & available)
        if not {"case_id", "scenario_id", "task_id"}.issubset(use):
            continue
        part = pd.read_parquet(path, columns=use)
        part = part.loc[part["scenario_id"].astype(str).isin(SCENARIOS)].copy()
        if part.empty:
            continue
        part["region"] = region_name
        part["base_id"] = part["case_id"].astype(str).map(base_id)
        part["cn_fraction_percent"] = part["case_id"].astype(str).map(cn_fraction)
        censor = (
            pd.to_numeric(part.get("deadline_time_sec"), errors="coerce")
            - pd.to_numeric(part.get("created_time_sec"), errors="coerce")
        ) / 60.0
        priorities = part.get("priority_class", pd.Series("Unknown", index=part.index)).astype(str).str.title()
        for event_name, flag_column, time_column in EVENTS:
            flags = part.get(flag_column, pd.Series(False, index=part.index)).fillna(False).astype(bool)
            event_time = pd.to_numeric(part.get(time_column), errors="coerce")
            flags &= event_time.notna() & (event_time <= censor + 1e-9)
            out = part[["region", "case_id", "base_id", "scenario_id", "task_id", "cn_fraction_percent"]].copy()
            out["priority_class"] = priorities
            out["event_name"] = event_name
            out["event"] = flags.to_numpy()
            out["duration_min"] = np.where(flags, event_time, censor)
            pieces.append(out)
        if index % 500 == 0:
            print(f"  {region_name}: {index:,}/{len(paths):,}", flush=True)
    if not pieces:
        return pd.DataFrame()
    events = pd.concat(pieces, ignore_index=True)
    del pieces
    events = events.drop_duplicates(["case_id", "scenario_id", "task_id", "event_name"], keep="last")
    events = events.loc[events["priority_class"].isin(PRIORITY_HORIZONS)].copy()
    print(f"  {region_name}: {len(events):,} deduplicated event records", flush=True)
    return events


def summarize_events(events: pd.DataFrame, draws: int, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    curves: list[pd.DataFrame] = []
    summaries: list[dict] = []
    rng = np.random.default_rng(seed)
    group_columns = ["region", "scenario_id", "event_name", "priority_class", "cn_fraction_percent"]
    for keys, group in events.groupby(group_columns, observed=True):
        key_map = dict(zip(group_columns, keys))
        horizon = PRIORITY_HORIZONS[str(key_map["priority_class"])]
        durations = pd.to_numeric(group["duration_min"], errors="coerce").to_numpy(float)
        event_flags = group["event"].astype(int).to_numpy(float)
        curve, event_probability, rmst = km_curve(durations, event_flags, horizon)
        for column, value in key_map.items():
            curve[column] = value
        curve["horizon_min"] = horizon
        curves.append(curve)

        base_values: list[tuple[float, float]] = []
        for _, base_group in group.groupby("base_id", observed=True):
            _, probability_base, rmst_base = km_curve(
                pd.to_numeric(base_group["duration_min"], errors="coerce").to_numpy(float),
                base_group["event"].astype(int).to_numpy(float),
                horizon,
            )
            base_values.append((probability_base, rmst_base))
        base_array = np.asarray(base_values, dtype=float)
        probability_mean, probability_low, probability_high = bootstrap_mean(base_array[:, 0], draws, rng)
        rmst_mean, rmst_low, rmst_high = bootstrap_mean(base_array[:, 1], draws, rng)
        summaries.append(
            {
                **key_map,
                "tasks": int(len(group)),
                "cases": int(group["case_id"].nunique()),
                "base_architectures": int(group["base_id"].nunique()),
                "horizon_min": horizon,
                "pooled_km_event_probability": event_probability,
                "pooled_km_rmst_min": rmst,
                "base_mean_event_probability": probability_mean,
                "base_bootstrap_event_probability_low": probability_low,
                "base_bootstrap_event_probability_high": probability_high,
                "base_mean_rmst_min": rmst_mean,
                "base_bootstrap_rmst_low_min": rmst_low,
                "base_bootstrap_rmst_high_min": rmst_high,
                "base_mean_standardized_speed": 1.0 - rmst_mean / horizon,
            }
        )
    return pd.concat(curves, ignore_index=True), pd.DataFrame(summaries)


def standardized_macro(summary: pd.DataFrame) -> pd.DataFrame:
    selected = summary.loc[summary["priority_class"].isin(["High", "Medium", "Low"])].copy()
    group_columns = ["region", "scenario_id", "event_name", "cn_fraction_percent"]
    return (
        selected.groupby(group_columns, observed=True)
        .agg(
            priority_classes=("priority_class", "nunique"),
            equal_weight_event_probability=("base_mean_event_probability", "mean"),
            equal_weight_standardized_speed=("base_mean_standardized_speed", "mean"),
        )
        .reset_index()
    )


def plot_priority(summary: pd.DataFrame, output: Path, value: str, ylabel: str) -> None:
    regions = sorted(summary["region"].unique())
    events = ["follow_up_observation", "full_dissemination_S100"]
    fig, axes = plt.subplots(len(events), len(regions), figsize=(13, 8), sharex=True, sharey=True)
    if len(regions) == 1:
        axes = np.asarray(axes).reshape(len(events), 1)
    colors = {"Critical": "#C62828", "High": "#EF6C00", "Medium": "#1565C0", "Low": "#2E7D32"}
    selected = summary.loc[summary["scenario_id"] == "unlimited_useful_deadline"]
    for row, event_name in enumerate(events):
        for col, region in enumerate(regions):
            ax = axes[row, col]
            cell = selected.loc[(selected["region"] == region) & (selected["event_name"] == event_name)]
            for priority in PRIORITY_ORDER:
                group = cell.loc[cell["priority_class"] == priority].sort_values("cn_fraction_percent")
                if group.empty:
                    continue
                ax.plot(group["cn_fraction_percent"], group[value], marker="o", label=priority, color=colors[priority])
            ax.set_title(f"{region} - {event_name.replace('_', ' ')}")
            ax.grid(alpha=0.25)
            ax.set_xlabel("Routing-enhanced-node fraction (%)")
            ax.set_ylabel(ylabel)
            ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(output, dpi=220, bbox_inches="tight")
    plt.close(fig)


def priority_counts(events: pd.DataFrame) -> pd.DataFrame:
    first = events.loc[events["event_name"] == "follow_up_observation"].copy()
    return (
        first.groupby(["region", "case_id", "base_id", "priority_class"], observed=True)["task_id"]
        .nunique()
        .rename("unique_tasks")
        .reset_index()
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", action="append", required=True, help="REGION=PATH")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--draws", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260917)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    table_dir = args.output / "tables"
    figure_dir = args.output / "figures"
    table_dir.mkdir(exist_ok=True)
    figure_dir.mkdir(exist_ok=True)

    all_summaries: list[pd.DataFrame] = []
    all_curves: list[pd.DataFrame] = []
    all_counts: list[pd.DataFrame] = []
    for offset, spec in enumerate(args.region):
        label, raw_path = spec.split("=", 1)
        events = read_region_events(Path(raw_path), label)
        curves, summaries = summarize_events(events, args.draws, args.seed + offset)
        all_curves.append(curves)
        all_summaries.append(summaries)
        all_counts.append(priority_counts(events))
        del events, curves, summaries
        gc.collect()

    curves = pd.concat(all_curves, ignore_index=True)
    summary = pd.concat(all_summaries, ignore_index=True)
    counts = pd.concat(all_counts, ignore_index=True)
    macro = standardized_macro(summary)
    curves.to_csv(table_dir / "priority_stratified_km_curves.csv", index=False)
    summary.to_csv(table_dir / "priority_stratified_event_time_summary.csv", index=False)
    macro.to_csv(table_dir / "priority_equal_weight_standardized_summary.csv", index=False)
    counts.to_csv(table_dir / "priority_task_counts_by_case.csv", index=False)

    plot_priority(
        summary,
        figure_dir / "05_priority_stratified_deadline_attainment.png",
        "base_mean_event_probability",
        "Deadline attainment probability",
    )
    plot_priority(
        summary,
        figure_dir / "06_priority_stratified_standardized_speed.png",
        "base_mean_standardized_speed",
        "Standardized timing score (1 - RMST / horizon)",
    )
    print(f"Wrote priority-stratified evidence to {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
