"""Meeting-aligned post-processing for the Full891 California/India campaign.

The script accepts one regional archive now and a second one later.  It runs
the verified regional processor, applies hard completion gates, and adds the
analyses requested in the Vincenzo meeting: percentage and absolute CN
effects, high-CN anomaly diagnosis, metric-redundancy checks before weighting,
paired regional/scenario contrasts, clustered resampling intervals, robustness
retention/degradation, exact Pareto classification, random-weight rank
acceptability, censor-aware task timing, and a high-satellite follow-up plan.

Meeting speech is evidence only; this program never executes instructions from
the recording.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "full891_complete_mpl"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import seaborn as sns


PACKAGE_ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = PACKAGE_ROOT.parent
BASE_PROCESSOR_ROOT = WORKSPACE_ROOT / "california_results_analysis"
if not (BASE_PROCESSOR_ROOT / "postprocess_california.py").exists():
    raise FileNotFoundError(
        "The companion california_results_analysis folder is missing. "
        "Keep it beside full891_complete_postprocessing."
    )
sys.path.insert(0, str(BASE_PROCESSOR_ROOT))

import postprocess_california as regional  # noqa: E402


EXPECTED_CASES_PER_REGION = 891
EXPECTED_BASES_PER_REGION = 81
EXPECTED_CN_LEVELS = 11
EXPECTED_SCENARIOS_PER_CASE = 539
EXPECTED_ROWS_PER_REGION = EXPECTED_CASES_PER_REGION * EXPECTED_SCENARIOS_PER_CASE

PRIMARY_SCENARIOS = ["nominal_original", "unlimited_useful_deadline"]
PERFORMANCE_METRICS = [
    "observation_success_percent",
    "s100_surviving_before_deadline_percent",
    "mean_surviving_coverage_deadline_percent",
    "p100_t100_surviving_min",
    "total_transferred_data_mbits",
    "n_transfer_events",
    "used_capacity_percent",
    "cn_related_traffic_share_percent",
    "max_hops_seen",
    "peak_node_sent_data_mbits",
    "node_sent_data_gini",
]
CORE_EFFECT_METRICS = [
    "observation_success_percent",
    "s100_surviving_before_deadline_percent",
    "mean_surviving_coverage_deadline_percent",
    "p100_t100_surviving_min",
    "total_transferred_data_mbits",
]
ROBUST_FAMILIES = [
    "random_satellite_failure",
    "random_cn_failure",
    "targeted_cn_failure",
    "link_window_outage",
    "capacity_degradation",
    "ground_station_outage",
]
ARCH_FACTORS = [
    "n_satellites",
    "n_planes",
    "altitude_km",
    "inclination_deg",
    "cn_fraction_percent",
    "n_central_nodes",
]
COLORS = {
    "California": "#2E75B6",
    "India": "#E07A3F",
    "nominal_original": "#17365D",
    "unlimited_useful_deadline": "#E07A3F",
}


@dataclass
class RegionBundle:
    label: str
    raw_input: Path
    output: Path
    extracted_root: Path
    canonical: pd.DataFrame
    audit: pd.DataFrame
    integrity: dict


def log(message: str) -> None:
    print(f"[complete] {message}", flush=True)


def safe_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_") or "region"


def base_id(case_id: str) -> str:
    return regional.base_id(str(case_id))


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def write_json(value: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def bootstrap_mean(values: Iterable[float], draws: int, rng: np.random.Generator) -> tuple[float, float, float]:
    array = pd.to_numeric(pd.Series(list(values)), errors="coerce").dropna().to_numpy(float)
    if not len(array):
        return math.nan, math.nan, math.nan
    if len(array) == 1 or draws <= 1:
        value = float(array.mean())
        return value, value, value
    samples = rng.choice(array, size=(draws, len(array)), replace=True).mean(axis=1)
    return float(array.mean()), float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))


def normalise(series: pd.Series, invert: bool = False) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    lo, hi = values.min(), values.max()
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        result = pd.Series(0.5, index=series.index, dtype=float)
    else:
        result = (values - lo) / (hi - lo)
    result = result.fillna(0.0)
    return 1.0 - result if invert else result


def pareto_mask(values: np.ndarray, maximise: Sequence[bool]) -> np.ndarray:
    oriented = values.astype(float).copy()
    for index, direction in enumerate(maximise):
        if not direction:
            oriented[:, index] *= -1.0
    keep = np.ones(len(oriented), dtype=bool)
    for index, candidate in enumerate(oriented):
        if not keep[index]:
            continue
        dominates = np.all(oriented >= candidate, axis=1) & np.any(oriented > candidate, axis=1)
        dominates[index] = False
        if dominates.any():
            keep[index] = False
    return keep


def process_region(
    raw_input: Path,
    output_root: Path,
    work_root: Path,
    skip_task_metrics: bool,
) -> RegionBundle:
    provisional = output_root / f"regional_{safe_slug(raw_input.stem)}"
    result = regional.run(raw_input, provisional, work_root / safe_slug(raw_input.stem), skip_task_metrics)
    canonical = pd.read_parquet(provisional / "tables" / "canonical_case_metrics_all_cases.parquet")
    labels = sorted(canonical.get("region", pd.Series(dtype=str)).dropna().astype(str).unique())
    label = labels[0] if len(labels) == 1 else ("+".join(labels) if labels else raw_input.stem)
    desired = output_root / f"regional_{safe_slug(label)}"
    if desired != provisional and not desired.exists():
        provisional.rename(desired)
        provisional = desired
    audit = pd.read_csv(provisional / "tables" / "archive_case_audit.csv")
    integrity = json.loads((provisional / "tables" / "integrity_summary.json").read_text(encoding="utf-8"))
    canonical["base_id"] = canonical["case_id"].astype(str).map(base_id)
    return RegionBundle(
        label=label,
        raw_input=raw_input.resolve(),
        output=provisional.resolve(),
        extracted_root=Path(result.get("regional_root", result["california_root"])),
        canonical=canonical,
        audit=audit,
        integrity=integrity,
    )


def completion_gate(bundle: RegionBundle) -> tuple[pd.DataFrame, bool]:
    audit = bundle.audit
    integrity = bundle.integrity
    checks = [
        ("case_directories", int(integrity.get("case_directories", -1)), EXPECTED_CASES_PER_REGION),
        ("base_architectures", int(integrity.get("base_architecture_directories", -1)), EXPECTED_BASES_PER_REGION),
        ("success_markers", int(integrity.get("success_markers", -1)), EXPECTED_CASES_PER_REGION),
        ("failed_markers", int(integrity.get("failed_markers", -1)), 0),
        ("validation_failures", int(integrity.get("failed_checks", -1)), 0),
        ("unique_scenario_rows", int(integrity.get("unique_scenario_rows_all_cases", -1)), EXPECTED_ROWS_PER_REGION),
        ("failed_status_rows", int(integrity.get("failed_status_rows_success_cases", -1)), 0),
        ("conflicting_duplicate_keys", int(integrity.get("conflicting_duplicate_keys", -1)), 0),
        ("balanced_base_architectures", int(integrity.get("balanced_base_architectures", -1)), EXPECTED_BASES_PER_REGION),
        ("balanced_cases", int(integrity.get("balanced_complete_cases", -1)), EXPECTED_CASES_PER_REGION),
        (
            "cases_with_exactly_539_unique_scenarios",
            int((pd.to_numeric(audit["unique_scenario_rows"], errors="coerce") == EXPECTED_SCENARIOS_PER_CASE).sum()),
            EXPECTED_CASES_PER_REGION,
        ),
        (
            "cn_levels_present",
            int(pd.to_numeric(audit["cn_fraction_percent"], errors="coerce").nunique()),
            EXPECTED_CN_LEVELS,
        ),
    ]
    rows = [
        {
            "region": bundle.label,
            "check": name,
            "observed": observed,
            "required": required,
            "passed": observed == required,
        }
        for name, observed, required in checks
    ]
    table = pd.DataFrame(rows)
    return table, bool(table["passed"].all())


def scientific_rows(bundle: RegionBundle) -> pd.DataFrame:
    complete = set(bundle.audit.loc[bundle.audit["success_marker"].astype(bool), "case_id"].astype(str))
    frame = bundle.canonical.loc[bundle.canonical["case_id"].astype(str).isin(complete)].copy()
    return frame.loc[frame["status"].astype(str) == "COMPLETED"].copy()


def metric_correlations(frame: pd.DataFrame, threshold: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    selected = frame.loc[frame["scenario_id"] == "unlimited_useful_deadline"].copy()
    metrics = [column for column in PERFORMANCE_METRICS if column in selected]
    numeric = selected[metrics].apply(pd.to_numeric, errors="coerce")
    pearson = numeric.corr(method="pearson")
    spearman = numeric.corr(method="spearman")
    rows = []
    for left_index, left in enumerate(metrics):
        for right in metrics[left_index + 1 :]:
            rho = spearman.loc[left, right]
            r = pearson.loc[left, right]
            rows.append(
                {
                    "metric_a": left,
                    "metric_b": right,
                    "pearson_r": r,
                    "spearman_rho": rho,
                    "max_absolute_correlation": np.nanmax(np.abs([r, rho])),
                    "possible_double_counting": bool(np.nanmax(np.abs([r, rho])) >= threshold),
                    "severe_redundancy_0p98": bool(np.nanmax(np.abs([r, rho])) >= 0.98),
                }
            )
    matrix = pd.concat({"pearson": pearson, "spearman": spearman}, names=["method", "metric"])
    return matrix, pd.DataFrame(rows).sort_values("max_absolute_correlation", ascending=False)


def cn_effects(frame: pd.DataFrame, draws: int, seed: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    selected = frame.loc[frame["scenario_id"].isin(PRIMARY_SCENARIOS)].copy()
    rng = np.random.default_rng(seed)
    summary_rows: list[dict] = []
    for (region_name, scenario, cn), group in selected.groupby(["region", "scenario_id", "cn_fraction_percent"]):
        row = {
            "region": region_name,
            "scenario_id": scenario,
            "cn_fraction_percent": cn,
            "n_base_architectures": int(group["base_id"].nunique()),
            "n_central_nodes_median": float(pd.to_numeric(group["n_central_nodes"], errors="coerce").median()),
        }
        for metric in CORE_EFFECT_METRICS:
            if metric not in group:
                continue
            values = pd.to_numeric(group[metric], errors="coerce").dropna()
            mean, lower, upper = bootstrap_mean(values, draws, rng)
            row[f"{metric}_mean"] = mean
            row[f"{metric}_median"] = float(values.median()) if len(values) else math.nan
            row[f"{metric}_bootstrap_low"] = lower
            row[f"{metric}_bootstrap_high"] = upper
        summary_rows.append(row)

    absolute_rows: list[dict] = []
    for (region_name, scenario, n_sat, n_cn), group in selected.groupby(
        ["region", "scenario_id", "n_satellites", "n_central_nodes"]
    ):
        row = {
            "region": region_name,
            "scenario_id": scenario,
            "n_satellites": n_sat,
            "n_central_nodes": n_cn,
            "cn_fraction_percent_median": float(pd.to_numeric(group["cn_fraction_percent"], errors="coerce").median()),
            "n_architecture_rows": int(len(group)),
        }
        for metric in CORE_EFFECT_METRICS:
            values = pd.to_numeric(group.get(metric), errors="coerce").dropna()
            row[f"{metric}_mean"] = float(values.mean()) if len(values) else math.nan
            row[f"{metric}_median"] = float(values.median()) if len(values) else math.nan
        absolute_rows.append(row)

    delta_rows: list[dict] = []
    keys = ["region", "scenario_id", "base_id"]
    for key, group in selected.sort_values("cn_fraction_percent").groupby(keys):
        group = group.drop_duplicates("cn_fraction_percent", keep="last").sort_values("cn_fraction_percent")
        for (_, before), (_, after) in zip(group.iloc[:-1].iterrows(), group.iloc[1:].iterrows()):
            row = dict(zip(keys, key))
            row.update(
                {
                    "case_id_from": before["case_id"],
                    "case_id_to": after["case_id"],
                    "cn_from_percent": float(before["cn_fraction_percent"]),
                    "cn_to_percent": float(after["cn_fraction_percent"]),
                    "n_central_nodes_from": float(before["n_central_nodes"]),
                    "n_central_nodes_to": float(after["n_central_nodes"]),
                }
            )
            for metric in PERFORMANCE_METRICS:
                if metric in group:
                    row[f"delta_{metric}"] = pd.to_numeric(pd.Series([after[metric]]), errors="coerce").iloc[0] - pd.to_numeric(
                        pd.Series([before[metric]]), errors="coerce"
                    ).iloc[0]
            delta_rows.append(row)
    return pd.DataFrame(summary_rows), pd.DataFrame(absolute_rows), pd.DataFrame(delta_rows)


def high_cn_diagnosis(deltas: pd.DataFrame, draws: int, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    high = deltas.loc[(deltas["scenario_id"] == "nominal_original") & (deltas["cn_from_percent"] >= 70)].copy()
    outcome = "delta_observation_success_percent"
    high["observation_drop"] = pd.to_numeric(high[outcome], errors="coerce") < 0
    explanatory = [
        "delta_total_transferred_data_mbits",
        "delta_n_transfer_events",
        "delta_used_capacity_percent",
        "delta_cn_related_traffic_share_percent",
        "delta_max_hops_seen",
        "delta_peak_node_sent_data_mbits",
        "delta_node_sent_data_gini",
    ]
    explanatory = [column for column in explanatory if column in high]
    rows: list[dict] = []
    rng = np.random.default_rng(seed)
    for (region_name, transition), group in high.assign(
        transition=high["cn_from_percent"].astype(int).astype(str) + "→" + high["cn_to_percent"].astype(int).astype(str)
    ).groupby(["region", "transition"]):
        mean, lower, upper = bootstrap_mean(group[outcome], draws, rng)
        row = {
            "region": region_name,
            "transition_percent": transition,
            "base_architectures": int(group["base_id"].nunique()),
            "share_with_observation_drop_percent": 100.0 * float(group["observation_drop"].mean()),
            "mean_observation_change_pp": mean,
            "bootstrap_low_pp": lower,
            "bootstrap_high_pp": upper,
        }
        for column in explanatory:
            paired = group[[outcome, column]].apply(pd.to_numeric, errors="coerce").dropna()
            row[f"spearman_observation_vs_{column.removeprefix('delta_')}"] = (
                float(paired.corr(method="spearman").iloc[0, 1]) if len(paired) >= 3 else math.nan
            )
        rows.append(row)
    detail_columns = [
        "region",
        "base_id",
        "case_id_from",
        "case_id_to",
        "cn_from_percent",
        "cn_to_percent",
        outcome,
        "observation_drop",
        *explanatory,
    ]
    details = high[[column for column in detail_columns if column in high]].sort_values(outcome)
    return pd.DataFrame(rows), details


def paired_scenario_effects(frame: pd.DataFrame, draws: int, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    comparisons = [
        ("policy_central", "policy_peer"),
        ("policy_central", "policy_hybrid"),
        ("task_size_x1p0", "task_size_x0p5"),
        ("task_size_x1p0", "task_size_x2p0"),
        ("task_size_x1p0", "task_size_x4p0"),
        ("nominal_original", "unlimited_useful_deadline"),
    ]
    detail_rows: list[pd.DataFrame] = []
    summary_rows: list[dict] = []
    rng = np.random.default_rng(seed)
    indexed = frame.set_index(["region", "case_id", "scenario_id"])
    for reference, alternative in comparisons:
        try:
            left = indexed.xs(reference, level="scenario_id")
            right = indexed.xs(alternative, level="scenario_id")
        except KeyError:
            continue
        common = left.index.intersection(right.index)
        if not len(common):
            continue
        details = pd.DataFrame(index=common).reset_index()
        details["reference_scenario"] = reference
        details["alternative_scenario"] = alternative
        details["base_id"] = details["case_id"].map(base_id)
        for metric in CORE_EFFECT_METRICS:
            if metric not in left or metric not in right:
                continue
            details[f"delta_{metric}"] = (
                pd.to_numeric(right.loc[common, metric], errors="coerce").to_numpy()
                - pd.to_numeric(left.loc[common, metric], errors="coerce").to_numpy()
            )
        detail_rows.append(details)
        for region_name, group in details.groupby("region"):
            for metric in CORE_EFFECT_METRICS:
                column = f"delta_{metric}"
                if column not in group:
                    continue
                mean, lower, upper = bootstrap_mean(group[column], draws, rng)
                summary_rows.append(
                    {
                        "region": region_name,
                        "reference_scenario": reference,
                        "alternative_scenario": alternative,
                        "metric": metric,
                        "paired_cases": int(group["case_id"].nunique()),
                        "mean_delta_alternative_minus_reference": mean,
                        "median_delta": float(pd.to_numeric(group[column], errors="coerce").median()),
                        "bootstrap_low": lower,
                        "bootstrap_high": upper,
                    }
                )
    return (pd.concat(detail_rows, ignore_index=True) if detail_rows else pd.DataFrame()), pd.DataFrame(summary_rows)


def robustness_analysis(frame: pd.DataFrame, draws: int, seed: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    baseline = frame.loc[frame["scenario_id"] == "unlimited_useful_deadline"].set_index(["region", "case_id"])
    robust = frame.loc[frame["family"].isin(ROBUST_FAMILIES)].copy()
    details: list[dict] = []
    for (region_name, case, family_name, level), group in robust.groupby(
        ["region", "case_id", "family", "failure_level_percent"], dropna=False
    ):
        if (region_name, case) not in baseline.index:
            continue
        base = baseline.loc[(region_name, case)]
        if isinstance(base, pd.DataFrame):
            base = base.iloc[-1]
        row = {
            "region": region_name,
            "case_id": case,
            "base_id": base_id(case),
            "family": family_name,
            "failure_level_percent": float(level),
            "replicates": int(group["replicate"].nunique()) if "replicate" in group else int(len(group)),
        }
        for metric in ["observation_success_percent", "s100_surviving_before_deadline_percent"]:
            stressed = float(pd.to_numeric(group[metric], errors="coerce").mean())
            original = float(pd.to_numeric(pd.Series([base[metric]]), errors="coerce").iloc[0])
            row[f"baseline_{metric}"] = original
            row[f"stressed_{metric}"] = stressed
            row[f"delta_{metric}"] = stressed - original
            row[f"retention_{metric}"] = stressed / original if original > 1e-12 else math.nan
        details.append(row)
    detail = pd.DataFrame(details)
    if detail.empty:
        return detail, pd.DataFrame(), pd.DataFrame()

    auc_rows: list[dict] = []
    for (region_name, case, family_name), group in detail.groupby(["region", "case_id", "family"]):
        row = {"region": region_name, "case_id": case, "base_id": base_id(case), "family": family_name}
        for metric in ["observation_success_percent", "s100_surviving_before_deadline_percent"]:
            y = pd.to_numeric(group[f"retention_{metric}"], errors="coerce")
            x = pd.to_numeric(group["failure_level_percent"], errors="coerce")
            valid = x.notna() & y.notna()
            xv = np.r_[0.0, x.loc[valid].to_numpy(float)]
            yv = np.r_[1.0, y.loc[valid].to_numpy(float)]
            order = np.argsort(xv)
            row[f"normalised_auc_retention_{metric}"] = (
                float(np.trapezoid(yv[order], xv[order]) / xv[order][-1]) if len(xv) > 1 and xv[order][-1] > 0 else math.nan
            )
        auc_rows.append(row)
    auc = pd.DataFrame(auc_rows)

    rng = np.random.default_rng(seed)
    summary_rows: list[dict] = []
    for (region_name, family_name, level), group in detail.groupby(["region", "family", "failure_level_percent"]):
        for metric in ["observation_success_percent", "s100_surviving_before_deadline_percent"]:
            for value_kind in ["stressed", "delta", "retention"]:
                column = f"{value_kind}_{metric}"
                mean, lower, upper = bootstrap_mean(group[column], draws, rng)
                summary_rows.append(
                    {
                        "region": region_name,
                        "family": family_name,
                        "failure_level_percent": level,
                        "metric": metric,
                        "quantity": value_kind,
                        "base_architectures": int(group["base_id"].nunique()),
                        "mean": mean,
                        "bootstrap_low": lower,
                        "bootstrap_high": upper,
                        "p05": float(pd.to_numeric(group[column], errors="coerce").quantile(0.05)),
                    }
                )
    return detail, pd.DataFrame(summary_rows), auc


def add_robustness_score(baseline: pd.DataFrame, auc: pd.DataFrame) -> pd.DataFrame:
    if auc.empty:
        result = baseline.copy()
        result["robustness_auc_retention"] = math.nan
        return result
    score = (
        auc.groupby(["region", "case_id"])["normalised_auc_retention_s100_surviving_before_deadline_percent"]
        .mean()
        .rename("robustness_auc_retention")
        .reset_index()
    )
    return baseline.merge(score, on=["region", "case_id"], how="left")


def pareto_and_weights(frame: pd.DataFrame, samples: int, seed: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    objectives = [
        ("observation_success_percent", True),
        ("s100_surviving_before_deadline_percent", True),
        ("p100_t100_surviving_min", False),
        ("n_satellites", False),
        ("n_central_nodes", False),
        ("total_transferred_data_mbits", False),
    ]
    if frame["robustness_auc_retention"].notna().any():
        objectives.append(("robustness_auc_retention", True))
    rng = np.random.default_rng(seed)
    pareto_parts: list[pd.DataFrame] = []
    winners: list[pd.DataFrame] = []
    summaries: list[pd.DataFrame] = []
    for region_name, group in frame.groupby("region"):
        group = group.drop_duplicates("case_id", keep="last").reset_index(drop=True).copy()
        usable = group.dropna(subset=[name for name, _ in objectives]).copy().reset_index(drop=True)
        if usable.empty:
            continue
        matrix = usable[[name for name, _ in objectives]].to_numpy(float)
        usable["is_exact_pareto"] = pareto_mask(matrix, [direction for _, direction in objectives])

        components = pd.DataFrame(index=usable.index)
        components["mission_success"] = normalise(usable["s100_surviving_before_deadline_percent"])
        components["observation_success"] = normalise(usable["observation_success_percent"])
        components["timeliness"] = normalise(usable["p100_t100_surviving_min"], invert=True)
        components["resource_efficiency"] = pd.concat(
            [
                normalise(usable["n_satellites"], invert=True),
                normalise(usable["n_central_nodes"], invert=True),
                normalise(usable["total_transferred_data_mbits"], invert=True),
            ],
            axis=1,
        ).mean(axis=1)
        components["robustness"] = (
            normalise(usable["robustness_auc_retention"])
            if usable["robustness_auc_retention"].notna().any()
            else pd.Series(0.5, index=usable.index)
        )
        component_names = list(components)
        weights = rng.dirichlet(np.ones(len(component_names)), size=samples)
        scores = weights @ components.to_numpy(float).T
        winner_index = np.argmax(scores, axis=1)
        top_k = min(5, len(usable))
        top_indices = np.argpartition(-scores, kth=top_k - 1, axis=1)[:, :top_k]
        win_counts = np.bincount(winner_index, minlength=len(usable))
        top_counts = np.bincount(top_indices.ravel(), minlength=len(usable))
        usable["rank1_acceptability_percent"] = 100.0 * win_counts / samples
        usable["top5_acceptability_percent"] = 100.0 * top_counts / samples
        equal = components.mean(axis=1)
        usable["equal_family_score"] = equal
        usable["equal_family_rank"] = equal.rank(method="min", ascending=False).astype(int)
        for name in component_names:
            usable[f"component_{name}"] = components[name]
        pareto_parts.append(usable)

        winner_rows = pd.DataFrame(weights, columns=[f"weight_{name}" for name in component_names])
        winner_rows.insert(0, "region", region_name)
        winner_rows["winner_case_id"] = usable.iloc[winner_index]["case_id"].to_numpy()
        winners.append(winner_rows)
        summaries.append(
            usable[
                [
                    "region",
                    "case_id",
                    "base_id",
                    "is_exact_pareto",
                    "rank1_acceptability_percent",
                    "top5_acceptability_percent",
                    "equal_family_score",
                    "equal_family_rank",
                    *[f"component_{name}" for name in component_names],
                ]
            ].sort_values(["rank1_acceptability_percent", "top5_acceptability_percent"], ascending=False)
        )
    return (
        pd.concat(pareto_parts, ignore_index=True) if pareto_parts else pd.DataFrame(),
        pd.concat(winners, ignore_index=True) if winners else pd.DataFrame(),
        pd.concat(summaries, ignore_index=True) if summaries else pd.DataFrame(),
    )


def cross_region_analysis(frame: pd.DataFrame, draws: int, seed: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    regions = sorted(frame["region"].dropna().astype(str).unique())
    if len(regions) != 2:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    left_name, right_name = regions
    selected = frame.loc[frame["scenario_id"] == "unlimited_useful_deadline"].copy()
    left = selected.loc[selected["region"] == left_name].set_index("case_id")
    right = selected.loc[selected["region"] == right_name].set_index("case_id")
    common = left.index.intersection(right.index)
    details = pd.DataFrame({"case_id": common})
    details["base_id"] = details["case_id"].map(base_id)
    summary_rows: list[dict] = []
    rank_rows: list[dict] = []
    rng = np.random.default_rng(seed)
    for metric in CORE_EFFECT_METRICS + ["robustness_auc_retention"]:
        if metric not in left or metric not in right:
            continue
        a = pd.to_numeric(left.loc[common, metric], errors="coerce").to_numpy()
        b = pd.to_numeric(right.loc[common, metric], errors="coerce").to_numpy()
        column = f"delta_{safe_slug(right_name)}_minus_{safe_slug(left_name)}_{metric}"
        details[column] = b - a
        mean, lower, upper = bootstrap_mean(details[column], draws, rng)
        summary_rows.append(
            {
                "region_a": left_name,
                "region_b": right_name,
                "metric": metric,
                "paired_cases": int(np.isfinite(b - a).sum()),
                "mean_delta_b_minus_a": mean,
                "median_delta_b_minus_a": float(pd.Series(b - a).median()),
                "bootstrap_low": lower,
                "bootstrap_high": upper,
            }
        )
        valid = np.isfinite(a) & np.isfinite(b)
        rank_rows.append(
            {
                "metric": metric,
                "region_a": left_name,
                "region_b": right_name,
                "matched_cases": int(valid.sum()),
                "spearman_rank_agreement": float(pd.Series(a[valid]).corr(pd.Series(b[valid]), method="spearman"))
                if valid.sum() >= 3
                else math.nan,
            }
        )
    return details, pd.DataFrame(summary_rows), pd.DataFrame(rank_rows)


def km_curve(durations: np.ndarray, events: np.ndarray, horizon: float) -> tuple[pd.DataFrame, float, float]:
    valid = np.isfinite(durations) & (durations >= 0) & np.isfinite(events)
    durations = durations[valid].astype(float)
    events = events[valid].astype(bool)
    if not len(durations):
        return pd.DataFrame(), math.nan, math.nan
    horizon = float(min(horizon, durations.max()))
    survival = 1.0
    previous = 0.0
    rmst = 0.0
    rows = [{"time_min": 0.0, "survival": 1.0, "event_probability": 0.0, "at_risk": len(durations)}]
    for time in np.sort(np.unique(durations[durations <= horizon])):
        rmst += survival * (time - previous)
        at_risk = int((durations >= time).sum())
        deaths = int(((durations == time) & events).sum())
        censored = int(((durations == time) & ~events).sum())
        if at_risk and deaths:
            survival *= 1.0 - deaths / at_risk
        rows.append(
            {
                "time_min": float(time),
                "survival": survival,
                "event_probability": 1.0 - survival,
                "at_risk": at_risk,
                "events": deaths,
                "censored": censored,
            }
        )
        previous = float(time)
    rmst += survival * max(0.0, horizon - previous)
    return pd.DataFrame(rows), float(1.0 - survival), float(rmst)


def task_event_analysis(
    bundles: Sequence[RegionBundle],
    draws: int,
    seed: int,
    horizon_override: float | None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    event_rows: list[pd.DataFrame] = []
    wanted = set(PRIMARY_SCENARIOS)
    columns = [
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
    ]
    for bundle in bundles:
        complete = set(bundle.audit.loc[bundle.audit["success_marker"].astype(bool), "case_id"].astype(str))
        paths = sorted(bundle.extracted_root.rglob("task_metrics_part_*.parquet"))
        log(f"Reading retained task events for {bundle.label}: {len(paths):,} partitions")
        for index, path in enumerate(paths, start=1):
            available = set(pq.read_schema(path).names)
            use = [column for column in columns if column in available]
            part = pd.read_parquet(path, columns=use)
            if "scenario_id" not in part or "case_id" not in part:
                continue
            part = part.loc[part["scenario_id"].astype(str).isin(wanted) & part["case_id"].astype(str).isin(complete)].copy()
            if part.empty:
                continue
            if "region" not in part:
                part["region"] = bundle.label
            part["base_id"] = part["case_id"].astype(str).map(base_id)
            part["cn_fraction_percent"] = part["case_id"].astype(str).map(
                lambda value: regional.parse_case_id(value)["cn_fraction_percent"]
            )
            censor = (
                pd.to_numeric(part.get("deadline_time_sec"), errors="coerce")
                - pd.to_numeric(part.get("created_time_sec"), errors="coerce")
            ) / 60.0
            for event_name, flag_column, time_column in [
                ("follow_up_observation", "observed_before_deadline", "observation_latency_min"),
                ("full_dissemination_S100", "s100_surviving_before_deadline", "t100_surviving_min"),
            ]:
                flags = part.get(flag_column, pd.Series(False, index=part.index)).fillna(False).astype(bool)
                event_time = pd.to_numeric(part.get(time_column), errors="coerce")
                flags &= event_time.notna() & (event_time <= censor + 1e-9)
                out = part[["region", "case_id", "base_id", "scenario_id", "task_id", "priority_class", "cn_fraction_percent"]].copy()
                out["event_name"] = event_name
                out["event"] = flags
                out["duration_min"] = np.where(flags, event_time, censor)
                event_rows.append(out)
            if index % 250 == 0:
                log(f"Read {index:,}/{len(paths):,} task partitions for {bundle.label}")
    events = pd.concat(event_rows, ignore_index=True) if event_rows else pd.DataFrame()
    if events.empty:
        return pd.DataFrame(), pd.DataFrame()
    events = events.drop_duplicates(["region", "case_id", "scenario_id", "task_id", "event_name"], keep="last")
    curves: list[pd.DataFrame] = []
    summaries: list[dict] = []
    rng = np.random.default_rng(seed)
    group_columns = ["region", "scenario_id", "event_name", "cn_fraction_percent"]
    for keys, group in events.groupby(group_columns):
        natural_horizon = float(pd.to_numeric(group["duration_min"], errors="coerce").max())
        horizon = float(horizon_override) if horizon_override is not None else natural_horizon
        curve, event_probability, rmst = km_curve(
            pd.to_numeric(group["duration_min"], errors="coerce").to_numpy(float),
            group["event"].astype(int).to_numpy(float),
            horizon,
        )
        for column, value in zip(group_columns, keys):
            curve[column] = value
        curve["horizon_min"] = horizon
        curves.append(curve)

        base_values = []
        for _, base_group in group.groupby("base_id"):
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
                **dict(zip(group_columns, keys)),
                "tasks": int(len(group)),
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
            }
        )
    return pd.concat(curves, ignore_index=True), pd.DataFrame(summaries)


def factor_range_effects(frame: pd.DataFrame) -> pd.DataFrame:
    selected = frame.loc[frame["scenario_id"] == "unlimited_useful_deadline"]
    rows: list[dict] = []
    for region_name, region_group in selected.groupby("region"):
        for factor in ARCH_FACTORS:
            if factor not in region_group:
                continue
            levels = pd.to_numeric(region_group[factor], errors="coerce")
            for metric in CORE_EFFECT_METRICS:
                if metric not in region_group:
                    continue
                table = pd.DataFrame({"factor": levels, "metric": pd.to_numeric(region_group[metric], errors="coerce")}).dropna()
                means = table.groupby("factor")["metric"].mean()
                rows.append(
                    {
                        "region": region_name,
                        "factor": factor,
                        "metric": metric,
                        "evaluated_min": float(means.index.min()),
                        "evaluated_max": float(means.index.max()),
                        "evaluated_range": float(means.index.max() - means.index.min()),
                        "number_of_levels": int(len(means)),
                        "max_minus_min_group_mean": float(means.max() - means.min()),
                        "spearman_over_evaluated_grid": float(table.corr(method="spearman").iloc[0, 1]),
                        "interpretation": "Effect is conditional on the evaluated factor range; do not convert this correlation directly into a decision weight.",
                    }
                )
    return pd.DataFrame(rows)


def build_shortlist(pareto: pd.DataFrame, acceptability: pd.DataFrame, count: int = 5) -> pd.DataFrame:
    if pareto.empty or acceptability.empty:
        return pd.DataFrame()
    merged = pareto.merge(
        acceptability[["region", "case_id", "rank1_acceptability_percent", "top5_acceptability_percent"]],
        on=["region", "case_id"],
        how="left",
        suffixes=("", "_summary"),
    )
    merged = merged.loc[merged["is_exact_pareto"]].copy()
    regions = sorted(merged["region"].unique())
    if len(regions) == 2:
        common = set(merged.loc[merged["region"] == regions[0], "case_id"]) & set(
            merged.loc[merged["region"] == regions[1], "case_id"]
        )
        pool = merged.loc[merged["case_id"].isin(common)].copy()
        if pool.empty:
            pool = merged
        summary = pool.groupby("case_id", as_index=False).agg(
            regions_present=("region", "nunique"),
            worst_region_rank1_acceptability_percent=("rank1_acceptability_percent", "min"),
            mean_top5_acceptability_percent=("top5_acceptability_percent", "mean"),
        )
        chosen_ids = summary.sort_values(
            ["regions_present", "worst_region_rank1_acceptability_percent", "mean_top5_acceptability_percent"],
            ascending=False,
        ).head(count)["case_id"]
        return merged.loc[merged["case_id"].isin(chosen_ids)].sort_values(["case_id", "region"])
    return merged.sort_values(["rank1_acceptability_percent", "top5_acceptability_percent"], ascending=False).head(count)


def followup_satellite_plan(shortlist: pd.DataFrame) -> pd.DataFrame:
    if shortlist.empty:
        return pd.DataFrame()
    unique = shortlist.drop_duplicates("case_id", keep="first")
    rows = []
    for _, row in unique.iterrows():
        planes = int(row["n_planes"])
        for target in [500, 1000]:
            adjusted = max(planes, int(round(target / planes)) * planes)
            rows.append(
                {
                    "source_case_id": row["case_id"],
                    "requested_satellite_scale": target,
                    "n_satellites_to_simulate": adjusted,
                    "adjustment_reason": "Satellite count must be divisible by plane count" if adjusted != target else "No adjustment required",
                    "n_planes": planes,
                    "altitude_km": row["altitude_km"],
                    "inclination_deg": row["inclination_deg"],
                    "cn_fraction_percent": row["cn_fraction_percent"],
                    "walker_f": row.get("walker_f", 1),
                    "run_scope": "Targeted follow-up only; smoke-test one case before the full pair of regions",
                }
            )
    return pd.DataFrame(rows)


def plot_cn(summary: pd.DataFrame, output: Path) -> None:
    if summary.empty:
        return
    sns.set_theme(style="whitegrid", context="talk")
    metrics = [
        ("observation_success_percent", "Observation success (%)"),
        ("s100_surviving_before_deadline_percent", "S100 before deadline (%)"),
        ("total_transferred_data_mbits", "Transferred data (Mbit/case)"),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.8))
    for ax, (metric, label) in zip(axes, metrics):
        for (region_name, scenario), group in summary.groupby(["region", "scenario_id"]):
            group = group.sort_values("cn_fraction_percent")
            style = "-" if scenario == "unlimited_useful_deadline" else "--"
            color = COLORS.get(region_name, "#6B7280")
            ax.plot(group["cn_fraction_percent"], group[f"{metric}_mean"], style, marker="o", color=color, label=f"{region_name} · {scenario.replace('_', ' ')}")
            ax.fill_between(
                group["cn_fraction_percent"],
                group[f"{metric}_bootstrap_low"],
                group[f"{metric}_bootstrap_high"],
                color=color,
                alpha=0.10,
            )
        ax.set_xlabel("Central-node fraction (%)")
        ax.set_ylabel(label)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=min(4, len(labels)), frameon=False, bbox_to_anchor=(0.5, -0.08))
    fig.suptitle("Balanced central-node response with base-architecture resampling intervals")
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_metric_correlation(matrix: pd.DataFrame, output: Path) -> None:
    if matrix.empty:
        return
    spearman = matrix.xs("spearman", level="method")
    fig, ax = plt.subplots(figsize=(10, 8))
    sns.heatmap(spearman, cmap="vlag", center=0, vmin=-1, vmax=1, annot=True, fmt=".2f", ax=ax)
    ax.set_title("Performance-metric redundancy audit (Spearman rank correlation)")
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_robustness(summary: pd.DataFrame, output: Path) -> None:
    selected = summary.loc[
        (summary["metric"] == "s100_surviving_before_deadline_percent") & (summary["quantity"] == "retention")
    ]
    if selected.empty:
        return
    fig, ax = plt.subplots(figsize=(11, 6))
    for (region_name, family_name), group in selected.groupby(["region", "family"]):
        group = group.sort_values("failure_level_percent")
        ax.plot(group["failure_level_percent"], 100 * group["mean"], marker="o", label=f"{region_name} · {family_name.replace('_', ' ')}")
        ax.fill_between(group["failure_level_percent"], 100 * group["bootstrap_low"], 100 * group["bootstrap_high"], alpha=0.10)
    ax.axhline(100, color="black", linewidth=1)
    ax.set(xlabel="Failure/degradation level (%)", ylabel="S100 retention (% of matched no-failure baseline)", title="Mission-level robustness degradation")
    ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left", frameon=False, fontsize=8)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_event_summary(summary: pd.DataFrame, output: Path) -> None:
    if summary.empty:
        return
    fig, axes = plt.subplots(2, 2, figsize=(13, 9), sharex=True)
    for row_index, event_name in enumerate(["follow_up_observation", "full_dissemination_S100"]):
        selected = summary.loc[summary["event_name"] == event_name]
        for (region_name, scenario), group in selected.groupby(["region", "scenario_id"]):
            group = group.sort_values("cn_fraction_percent")
            label = f"{region_name} · {scenario.replace('_', ' ')}"
            color = COLORS.get(region_name, "#6B7280")
            style = "-" if scenario == "unlimited_useful_deadline" else "--"
            axes[row_index, 0].plot(group["cn_fraction_percent"], 100 * group["base_mean_event_probability"], style, marker="o", color=color, label=label)
            axes[row_index, 1].plot(group["cn_fraction_percent"], group["base_mean_rmst_min"], style, marker="o", color=color, label=label)
        axes[row_index, 0].set_ylabel(f"{event_name.replace('_', ' ').title()}\nprobability by horizon (%)")
        axes[row_index, 1].set_ylabel("Restricted mean time (min)")
    for ax in axes[-1]:
        ax.set_xlabel("Central-node fraction (%)")
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=min(4, len(labels)), frameon=False, bbox_to_anchor=(0.5, -0.03))
    fig.suptitle("Censor-aware dissemination and observation timing")
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def weighting_proposal(redundancy: pd.DataFrame) -> pd.DataFrame:
    flagged = set()
    if not redundancy.empty:
        for _, row in redundancy.loc[redundancy["possible_double_counting"]].iterrows():
            flagged.update([row["metric_a"], row["metric_b"]])
    rows = [
        ("Mission success", "s100_surviving_before_deadline_percent", "maximise", 0.25, "Primary useful dissemination endpoint"),
        ("Observation", "observation_success_percent", "maximise", 0.20, "Follow-up sensing after reception; operationally distinct from dissemination"),
        ("Timeliness", "p100_t100_surviving_min / censor-aware RMST", "minimise", 0.20, "Time endpoint reported with event probability to avoid censoring bias"),
        ("Resource efficiency", "composite: satellites, absolute CN count, transferred Mbit", "maximise efficiency", 0.20, "One family prevents several correlated resource columns receiving full weights separately"),
        ("Robustness", "mean failure-family S100 retention AUC", "maximise", 0.15, "Rewards graceful degradation, not only nominal performance"),
    ]
    return pd.DataFrame(
        [
            {
                "objective_family": family,
                "representative_measure": measure,
                "direction": direction,
                "illustrative_weight_for_supervisor_review": weight,
                "justification": justification,
                "data_redundancy_flag": any(name in str(measure) for name in flagged),
                "approval_status": "PROPOSAL — Vincenzo to review; random-weight acceptability remains the primary sensitivity evidence",
            }
            for family, measure, direction, weight, justification in rows
        ]
    )


def compile_report(
    bundles: Sequence[RegionBundle],
    gate: pd.DataFrame,
    campaign_ready: bool,
    redundancy: pd.DataFrame,
    high_cn: pd.DataFrame,
    shortlist: pd.DataFrame,
    event_summary: pd.DataFrame,
) -> str:
    regions = ", ".join(bundle.label for bundle in bundles)
    severe = redundancy.loc[redundancy.get("severe_redundancy_0p98", False)] if not redundancy.empty else pd.DataFrame()
    lines = [
        "# Full891 thesis post-processing report",
        "",
        f"Regions supplied: **{regions}**.",
        f"Final two-region evidence gate: **{'PASS' if campaign_ready else 'NOT YET PASSED'}**.",
        "",
        "## Interpretation status",
        "",
    ]
    if campaign_ready:
        lines.append("Both regional archives passed the exact 891-case / 539-scenario gate; combined tables may be used for final thesis claims after review.")
    else:
        lines.append("Outputs are preliminary or regional. Do not present them as the final California–India campaign until both regions pass every gate.")
    lines.extend(
        [
            "",
            "## Meeting-aligned conclusions to inspect",
            "",
            "- The CN analysis reports both fraction (%) and absolute central-node count.",
            "- High-CN declines are paired within the same base architecture and related to transfer count/data, used capacity, CN traffic share, hops, peak load, and load inequality. Association is diagnostic, not proof of congestion by itself.",
            "- Performance metrics are correlated with one another before any weighted score is interpreted. Highly redundant metrics are kept visible but are not given independent full family weights.",
            "- Exact Pareto membership is primary. Weighting is a sensitivity analysis, not a declaration of one universal optimum.",
            "- The high-satellite plan changes only shortlisted designs and adjusts satellite counts to remain divisible by plane count.",
            "",
            "## Completion gate",
            "",
            gate.to_markdown(index=False),
            "",
            "## Metric redundancy",
            "",
            f"Severely correlated metric pairs (absolute Pearson or Spearman ≥ 0.98): **{len(severe)}**.",
            "See `tables/metric_pair_redundancy.csv` before approving weights.",
            "",
            "## High-CN behavior",
            "",
        ]
    )
    if high_cn.empty:
        lines.append("No high-CN diagnostic was available.")
    else:
        for _, row in high_cn.iterrows():
            lines.append(
                f"- {row['region']} {row['transition_percent']}%: mean observation change "
                f"{row['mean_observation_change_pp']:.3f} percentage points; "
                f"{row['share_with_observation_drop_percent']:.1f}% of matched base architectures declined."
            )
    lines.extend(["", "## Censor-aware timing", ""])
    if event_summary.empty:
        lines.append("Retained task partitions were skipped or unavailable, so Kaplan–Meier/RMST outputs were not created.")
    else:
        lines.append("Task-level observation and S100 times are reported with censored failures included. See `tables/event_time_summary.csv` and the event-time figure.")
    lines.extend(["", "## Conditional shortlist", ""])
    if shortlist.empty:
        lines.append("No shortlist could be produced from the supplied rows.")
    else:
        lines.append("The shortlist is conditional on the declared objectives and sampled preferences; it is not a universal optimum.")
        lines.append("")
        lines.append(shortlist[[column for column in ["region", "case_id", "rank1_acceptability_percent", "top5_acceptability_percent", "is_exact_pareto"] if column in shortlist]].to_markdown(index=False))
    lines.extend(
        [
            "",
            "## Required next scientific checks",
            "",
            "1. Inspect the worst high-CN cases at trace level or rerun one representative case with transfer/window retention; the paired correlation alone cannot prove routing congestion.",
            "2. Send the weighting proposal and metric-redundancy table to Vincenzo for approval before naming a preferred design.",
            "3. Once India is complete, rerun this same command with both archives and replace preliminary figures rather than merging outputs manually.",
            "4. Smoke-test each 500/1000-satellite follow-up candidate before committing the VM to the targeted extension.",
            "5. Keep California/India conclusions case-study-specific; task counts and regional conditions differ strongly.",
        ]
    )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", action="append", required=True, help="Regional ZIP or extracted regional run folder; provide once now and twice after India completes")
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument("--work-dir", default=str(Path(tempfile.gettempdir()) / "full891_complete_work"))
    parser.add_argument("--bootstrap-draws", type=int, default=1000)
    parser.add_argument("--weight-samples", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20260910)
    parser.add_argument("--collinearity-threshold", type=float, default=0.95)
    parser.add_argument("--rmst-horizon-min", type=float)
    parser.add_argument("--skip-task-events", action="store_true", help="Skip slower task-partition Kaplan–Meier/RMST analysis")
    parser.add_argument("--skip-regional-task-summary", action="store_true", help="Skip the older retained task summary inside each regional audit")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output = Path(args.output).expanduser().resolve()
    work = Path(args.work_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    tables = output / "tables"
    figures = output / "figures"
    tables.mkdir(exist_ok=True)
    figures.mkdir(exist_ok=True)

    bundles = [
        process_region(Path(path).expanduser(), output, work, args.skip_regional_task_summary)
        for path in args.region
    ]
    labels = [bundle.label for bundle in bundles]
    if len(labels) != len(set(labels)):
        raise ValueError(f"Each --region must contain a distinct region; found {labels}")

    gate_parts, gate_results = [], []
    scientific_parts = []
    for bundle in bundles:
        gate, passed = completion_gate(bundle)
        gate_parts.append(gate)
        gate_results.append(passed)
        scientific_parts.append(scientific_rows(bundle))
    gate = pd.concat(gate_parts, ignore_index=True)
    scientific = pd.concat(scientific_parts, ignore_index=True)
    campaign_ready = len(bundles) == 2 and all(gate_results)
    write_csv(gate, tables / "completion_gate.csv")
    scientific.to_parquet(tables / "canonical_completed_rows_all_supplied_regions.parquet", index=False, compression="zstd")

    correlation_matrix, redundancy = metric_correlations(scientific, args.collinearity_threshold)
    correlation_matrix.to_csv(tables / "performance_metric_correlation_matrices.csv")
    write_csv(redundancy, tables / "metric_pair_redundancy.csv")
    proposal = weighting_proposal(redundancy)
    write_csv(proposal, tables / "weighting_proposal_for_vincenzo_review.csv")

    cn_summary, cn_absolute, cn_deltas = cn_effects(scientific, args.bootstrap_draws, args.seed)
    write_csv(cn_summary, tables / "cn_fraction_base_clustered_intervals.csv")
    write_csv(cn_absolute, tables / "cn_absolute_count_summary.csv")
    write_csv(cn_deltas, tables / "cn_adjacent_paired_deltas.csv")
    high_cn_summary, high_cn_details = high_cn_diagnosis(cn_deltas, args.bootstrap_draws, args.seed + 1)
    write_csv(high_cn_summary, tables / "high_cn_decline_diagnosis.csv")
    write_csv(high_cn_details, tables / "high_cn_cases_for_trace_investigation.csv")

    paired_details, paired_summary = paired_scenario_effects(scientific, args.bootstrap_draws, args.seed + 2)
    write_csv(paired_details, tables / "paired_policy_tasksize_nominal_effects.csv")
    write_csv(paired_summary, tables / "paired_policy_tasksize_nominal_effects_summary.csv")

    robust_detail, robust_summary, robust_auc = robustness_analysis(scientific, args.bootstrap_draws, args.seed + 3)
    write_csv(robust_detail, tables / "robustness_paired_case_detail.csv")
    write_csv(robust_summary, tables / "robustness_clustered_summary.csv")
    write_csv(robust_auc, tables / "robustness_retention_auc_by_case.csv")

    baseline = scientific.loc[scientific["scenario_id"] == "unlimited_useful_deadline"].copy()
    baseline = add_robustness_score(baseline, robust_auc)
    pareto, weight_winners, acceptability = pareto_and_weights(baseline, args.weight_samples, args.seed + 4)
    write_csv(pareto, tables / "exact_pareto_with_objective_components.csv")
    write_csv(weight_winners, tables / "random_weight_winners.csv")
    write_csv(acceptability, tables / "rank_acceptability_summary.csv")

    cross_detail, cross_summary, rank_agreement = cross_region_analysis(baseline, args.bootstrap_draws, args.seed + 5)
    write_csv(cross_detail, tables / "matched_region_case_deltas.csv")
    write_csv(cross_summary, tables / "matched_region_delta_summary.csv")
    write_csv(rank_agreement, tables / "regional_rank_agreement.csv")

    factor_effects = factor_range_effects(scientific)
    write_csv(factor_effects, tables / "factor_effects_with_evaluated_ranges.csv")

    shortlist = build_shortlist(pareto, acceptability, 5)
    followup = followup_satellite_plan(shortlist)
    write_csv(shortlist, tables / "conditional_top5_shortlist.csv")
    write_csv(followup, tables / "targeted_500_1000_satellite_followup_plan.csv")

    if args.skip_task_events:
        event_curves, event_summary = pd.DataFrame(), pd.DataFrame()
    else:
        event_curves, event_summary = task_event_analysis(
            bundles, args.bootstrap_draws, args.seed + 6, args.rmst_horizon_min
        )
    write_csv(event_curves, tables / "kaplan_meier_event_curves.csv")
    write_csv(event_summary, tables / "event_time_summary.csv")

    plot_cn(cn_summary, figures / "01_cn_fraction_response_clustered_intervals.png")
    plot_metric_correlation(correlation_matrix, figures / "02_performance_metric_redundancy.png")
    plot_robustness(robust_summary, figures / "03_robustness_retention.png")
    plot_event_summary(event_summary, figures / "04_censor_aware_event_times.png")

    report = compile_report(bundles, gate, campaign_ready, redundancy, high_cn_summary, shortlist, event_summary)
    (output / "THESIS_POSTPROCESSING_REPORT.md").write_text(report, encoding="utf-8")
    provenance = {
        "status": "FINAL_READY" if campaign_ready else "PRELIMINARY_OR_REGIONAL",
        "regions": labels,
        "campaign_gate_passed": campaign_ready,
        "expected": {
            "cases_per_region": EXPECTED_CASES_PER_REGION,
            "base_architectures_per_region": EXPECTED_BASES_PER_REGION,
            "cn_levels": EXPECTED_CN_LEVELS,
            "scenarios_per_case": EXPECTED_SCENARIOS_PER_CASE,
            "rows_per_region": EXPECTED_ROWS_PER_REGION,
            "rows_two_regions": 2 * EXPECTED_ROWS_PER_REGION,
        },
        "input_files": [
            {
                "path": str(bundle.raw_input),
                "sha256": sha256_file(bundle.raw_input) if bundle.raw_input.is_file() else None,
            }
            for bundle in bundles
        ],
        "python": sys.version,
        "platform": platform.platform(),
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ["numpy", "pandas", "pyarrow", "matplotlib", "seaborn", "scipy"]
        },
        "analysis_parameters": vars(args),
        "method_notes": [
            "Resampling intervals use base architectures as the resampling unit.",
            "They are stability intervals over the enumerated design grid, not population confidence intervals.",
            "Kaplan-Meier/RMST uses the task deadline as the censoring limit for the declared before-deadline endpoints.",
            "Exact Pareto membership is independent of the illustrative weights.",
        ],
    }
    write_json(provenance, output / "reproducibility_manifest.json")
    log(f"Done. Final campaign gate: {'PASS' if campaign_ready else 'NOT YET PASSED'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
