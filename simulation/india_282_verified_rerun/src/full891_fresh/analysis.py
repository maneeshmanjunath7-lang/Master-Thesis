from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import KFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import PolynomialFeatures, StandardScaler

from .config import CampaignConfig
from .scenarios import build_scenarios
from .storage import write_json_atomic, write_parquet_atomic


ARCH_FEATURES = [
    "n_satellites", "n_planes", "altitude_km", "inclination_deg",
    "cn_fraction_percent", "n_central_nodes",
]
PERFORMANCE_TARGETS = [
    "observation_success_percent",
    "s100_surviving_before_deadline_percent",
    "mean_surviving_coverage_deadline_percent",
    "p100_t100_surviving_min",
    "total_transferred_data_mbits",
    "n_transfer_events",
    "n_available_windows",
    "available_window_capacity_mbits",
    "peak_node_sent_data_mbits",
    "node_sent_data_gini",
]


def _read_parts(paths: list[Path], columns: list[str] | None = None) -> pd.DataFrame:
    if not paths:
        return pd.DataFrame()
    tables = []
    for start in range(0, len(paths), 1000):
        dataset = ds.dataset([str(p) for p in paths[start:start + 1000]], format="parquet")
        tables.append(dataset.to_table(columns=columns))
    return pa.concat_tables(tables, promote_options="default").to_pandas()


def collect_case_metrics(output_root: Path) -> pd.DataFrame:
    paths = sorted((output_root / "runs").rglob("case_metrics_part_*.parquet"))
    frame = _read_parts(paths)
    if frame.empty:
        raise RuntimeError(f"No case metric partitions found under {output_root / 'runs'}")
    frame = frame.drop_duplicates(["region", "case_id", "scenario_id"], keep="last")
    return frame.reset_index(drop=True)


def _numeric(frame: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    out = frame.loc[:, list(columns)].copy()
    for column in out:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    return out


def correlation_tables(completed: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    selected = completed.loc[completed["scenario_id"] == "nominal_original"]
    columns = [c for c in ARCH_FEATURES + PERFORMANCE_TARGETS if c in selected]
    numeric = _numeric(selected, columns)
    return numeric.corr(method="spearman"), numeric.corr(method="pearson")


def _cv_scores(
    model, x: np.ndarray, y: np.ndarray, folds: int, seed: int, log_target: bool = False
) -> dict[str, float]:
    folds = min(folds, len(y))
    if folds < 2:
        return {"cv_r2": np.nan, "cv_rmse": np.nan, "cv_mae": np.nan}
    splitter = KFold(n_splits=folds, shuffle=True, random_state=seed)
    r2, rmse, mae = [], [], []
    for train, test in splitter.split(x):
        train_target = np.log(y[train]) if log_target else y[train]
        model.fit(x[train], train_target)
        prediction = model.predict(x[test])
        if log_target:
            prediction = np.exp(prediction)
        r2.append(r2_score(y[test], prediction))
        rmse.append(math.sqrt(mean_squared_error(y[test], prediction)))
        mae.append(mean_absolute_error(y[test], prediction))
    return {"cv_r2": float(np.mean(r2)), "cv_rmse": float(np.mean(rmse)), "cv_mae": float(np.mean(mae))}


def regression_comparison(completed: pd.DataFrame, config: CampaignConfig) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    selected = completed.loc[completed["scenario_id"].isin([
        "nominal_original", "unlimited_useful_deadline"
    ])].copy()
    x_columns = [c for c in ARCH_FEATURES if c in selected]
    x_all = _numeric(selected, x_columns)
    seed = int(config.data["random_seed"])
    folds = int(config.data["analysis"]["cross_validation_folds"])
    for (region, scenario_id), region_frame in selected.groupby(["region", "scenario_id"]):
        indices = region_frame.index
        x_region = x_all.loc[indices]
        for target in PERFORMANCE_TARGETS:
            if target not in region_frame:
                continue
            y_series = pd.to_numeric(region_frame[target], errors="coerce")
            mask = x_region.notna().all(axis=1) & y_series.notna()
            x = x_region.loc[mask].to_numpy(dtype=float)
            y = y_series.loc[mask].to_numpy(dtype=float)
            if len(y) < 20:
                continue
            models: list[tuple[str, Any, np.ndarray, bool]] = [
                ("linear", make_pipeline(StandardScaler(), LinearRegression()), np.ones(len(y), dtype=bool), False),
            ]
            for degree in config.data["analysis"]["polynomial_degrees"]:
                models.append((
                    f"polynomial_degree_{int(degree)}",
                    make_pipeline(PolynomialFeatures(int(degree), include_bias=False), StandardScaler(), LinearRegression()),
                    np.ones(len(y), dtype=bool), False,
                ))
            positive = y > 0
            if int(positive.sum()) >= 20:
                models.append(("exponential_log_linear", make_pipeline(StandardScaler(), LinearRegression()), positive, True))
            for name, model, model_mask, log_target in models:
                x_model, y_model = x[model_mask], y[model_mask]
                scores = _cv_scores(model, x_model, y_model, folds, seed, log_target=log_target)
                rows.append({
                    "region": region, "scenario_id": scenario_id, "target": target, "model": name,
                    "n_samples": len(y_model), **scores,
                    "selection_note": "Compare held-out RMSE/MAE; do not select by training fit alone.",
                })
    result = pd.DataFrame(rows)
    if not result.empty:
        result["best_cv_rmse_within_region_scenario_target"] = result.groupby(
            ["region", "scenario_id", "target"]
        )["cv_rmse"].transform("min") == result["cv_rmse"]
    return result


def resource_cost_vectors(completed: pd.DataFrame, config: CampaignConfig) -> pd.DataFrame:
    columns = [
        "region", "case_id", "scenario_id", "n_satellites", "n_planes",
        "n_central_nodes", "cn_fraction_percent", "n_available_windows",
        "available_window_duration_sec", "available_window_capacity_mbits",
        "n_transfer_events", "total_transferred_data_mbits", "peak_node_sent_data_mbits",
        "node_sent_data_gini",
    ]
    columns = [column for column in columns if column in completed]
    frame = completed.loc[completed["scenario_id"].isin([
        "nominal_original", "unlimited_useful_deadline", "policy_peer", "policy_hybrid"
    ]), columns].copy()
    coefficients = config.data["analysis"].get("monetary_cost_coefficients")
    if coefficients:
        required = {"satellite", "plane", "central_node", "transferred_mbit"}
        missing = required - set(coefficients)
        if missing:
            raise ValueError(f"Monetary cost coefficients are missing: {sorted(missing)}")
        frame["monetary_cost"] = (
            pd.to_numeric(frame["n_satellites"]) * float(coefficients["satellite"])
            + pd.to_numeric(frame["n_planes"]) * float(coefficients["plane"])
            + pd.to_numeric(frame["n_central_nodes"]) * float(coefficients["central_node"])
            + pd.to_numeric(frame["total_transferred_data_mbits"]) * float(coefficients["transferred_mbit"])
        )
        frame["monetary_cost_note"] = "User-supplied coefficients; inspect provenance config before interpretation."
    else:
        frame["monetary_cost_note"] = "Not calculated: no traceable monetary coefficients supplied."
    return frame


def _pareto_mask(values: np.ndarray, maximize: list[bool]) -> np.ndarray:
    transformed = values.copy().astype(float)
    for column, is_max in enumerate(maximize):
        if not is_max:
            transformed[:, column] *= -1.0
    keep = np.ones(len(transformed), dtype=bool)
    for i, point in enumerate(transformed):
        if not keep[i]:
            continue
        dominated = np.all(transformed >= point, axis=1) & np.any(transformed > point, axis=1)
        if dominated.any():
            keep[i] = False
    return keep


def pareto_table(completed: pd.DataFrame) -> pd.DataFrame:
    selected = completed.loc[completed["scenario_id"] == "unlimited_useful_deadline"].copy()
    objectives = [
        "observation_success_percent", "s100_surviving_before_deadline_percent",
        "p100_t100_surviving_min", "n_satellites", "n_central_nodes",
        "total_transferred_data_mbits",
    ]
    output = []
    for region, frame in selected.groupby("region"):
        numeric = _numeric(frame, objectives)
        valid = numeric.notna().all(axis=1)
        subset = frame.loc[valid].copy()
        if subset.empty:
            continue
        subset["is_exact_pareto"] = _pareto_mask(
            numeric.loc[valid].to_numpy(), [True, True, False, False, False, False]
        )
        output.append(subset)
    return pd.concat(output, ignore_index=True) if output else pd.DataFrame()


def rank_weight_sensitivity(completed: pd.DataFrame, config: CampaignConfig) -> pd.DataFrame:
    selected = completed.loc[completed["scenario_id"] == "unlimited_useful_deadline"].copy()
    objectives = [
        "observation_success_percent", "s100_surviving_before_deadline_percent",
        "mean_surviving_coverage_deadline_percent", "p100_t100_surviving_min",
        "n_satellites", "n_central_nodes", "total_transferred_data_mbits",
    ]
    rng = np.random.default_rng(int(config.data["random_seed"]))
    samples = int(config.data["analysis"]["weight_samples"])
    outputs = []
    for region, frame in selected.groupby("region"):
        numeric = _numeric(frame, objectives)
        valid = numeric.notna().all(axis=1)
        frame, numeric = frame.loc[valid].copy(), numeric.loc[valid]
        if frame.empty:
            continue
        scaled = (numeric - numeric.min()) / (numeric.max() - numeric.min()).replace(0, 1)
        for cost in ["p100_t100_surviving_min", "n_satellites", "n_central_nodes", "total_transferred_data_mbits"]:
            scaled[cost] = 1.0 - scaled[cost]
        weights = rng.dirichlet(np.ones(len(objectives)), size=samples)
        scores = scaled.to_numpy() @ weights.T
        winners = np.argmax(scores, axis=0)
        counts = np.bincount(winners, minlength=len(frame))
        ranks = np.argsort(np.argsort(-scores, axis=0), axis=0) + 1
        top10 = (ranks <= 10).mean(axis=1)
        out = frame[["region", "case_id", "cn_fraction_percent"] + [c for c in objectives if c in frame]].copy()
        out["winner_percent"] = 100.0 * counts / samples
        out["top10_percent"] = 100.0 * top10
        outputs.append(out.sort_values("winner_percent", ascending=False))
    return pd.concat(outputs, ignore_index=True) if outputs else pd.DataFrame()


def robustness_summary(completed: pd.DataFrame) -> pd.DataFrame:
    robust = completed.loc[completed["family"].isin([
        "random_satellite_failure", "random_cn_failure", "targeted_cn_failure",
        "link_window_outage", "capacity_degradation", "ground_station_outage", "combined_stress",
    ])].copy()
    metrics = [
        "observation_success_percent", "s100_original_before_deadline_percent",
        "s100_surviving_before_deadline_percent", "mean_original_coverage_deadline_percent",
        "mean_surviving_coverage_deadline_percent", "p100_t100_surviving_min",
    ]
    metrics = [m for m in metrics if m in robust]
    if robust.empty:
        return pd.DataFrame()
    grouped = robust.groupby(["region", "family", "failure_level_percent"], dropna=False)
    rows = []
    for key, frame in grouped:
        row = dict(zip(["region", "family", "failure_level_percent"], key))
        row["scenario_evaluations"] = len(frame)
        row["architectures"] = frame["case_id"].nunique()
        for metric in metrics:
            values = pd.to_numeric(frame[metric], errors="coerce")
            row[f"{metric}_mean"] = float(values.mean())
            row[f"{metric}_std"] = float(values.std())
            row[f"{metric}_p05"] = float(values.quantile(0.05))
            row[f"{metric}_p95"] = float(values.quantile(0.95))
        rows.append(row)
    return pd.DataFrame(rows)


def task_size_summary(completed: pd.DataFrame) -> pd.DataFrame:
    frame = completed.loc[completed["family"] == "task_size"].copy()
    if frame.empty:
        return frame
    metrics = [
        "observation_success_percent", "s100_surviving_before_deadline_percent",
        "mean_surviving_coverage_deadline_percent", "p100_t100_surviving_min",
        "total_transferred_data_mbits",
    ]
    return frame.groupby(["region", "task_size_multiplier"], as_index=False)[metrics].mean(numeric_only=True)


def policy_summary(completed: pd.DataFrame) -> pd.DataFrame:
    frame = completed.loc[completed["family"] == "policy_ablation"].copy()
    if frame.empty:
        return frame
    metrics = [
        "observation_success_percent", "s100_surviving_before_deadline_percent",
        "mean_surviving_coverage_deadline_percent", "p100_t100_surviving_min",
        "total_transferred_data_mbits",
    ]
    return frame.groupby(["region", "topology", "routing_policy"], as_index=False)[metrics].mean(numeric_only=True)


def regional_rank_agreement(completed: pd.DataFrame) -> pd.DataFrame:
    frame = completed.loc[completed["scenario_id"] == "unlimited_useful_deadline"].copy()
    regions = sorted(frame["region"].dropna().unique())
    if len(regions) != 2:
        return pd.DataFrame()
    left, right = regions
    rows = []
    for metric in PERFORMANCE_TARGETS:
        if metric not in frame:
            continue
        pivot = frame.pivot_table(index="case_id", columns="region", values=metric, aggfunc="first")
        paired = pivot[[left, right]].dropna()
        rows.append({
            "metric": metric, "region_a": left, "region_b": right,
            "paired_architectures": len(paired),
            "spearman_rank_correlation": float(paired[left].corr(paired[right], method="spearman")) if len(paired) else np.nan,
            "pearson_correlation": float(paired[left].corr(paired[right], method="pearson")) if len(paired) else np.nan,
        })
    return pd.DataFrame(rows)


def _plot_robustness(summary: pd.DataFrame, path: Path) -> None:
    if summary.empty:
        return
    fig, axes = plt.subplots(1, 2, figsize=(13, 5), constrained_layout=True)
    for (region, family), frame in summary.groupby(["region", "family"]):
        label = f"{region} – {family.replace('_', ' ')}"
        axes[0].plot(frame["failure_level_percent"], frame["observation_success_percent_mean"], marker="o", label=label)
        axes[1].plot(frame["failure_level_percent"], frame["mean_original_coverage_deadline_percent_mean"], marker="o", label=label)
    axes[0].set(title="Observation success under failures", xlabel="Failure level (%)", ylabel="Success (%)")
    axes[1].set(title="Original-mission coverage under failures", xlabel="Failure level (%)", ylabel="Coverage (%)")
    axes[0].grid(alpha=.25); axes[1].grid(alpha=.25)
    axes[1].legend(fontsize=7, bbox_to_anchor=(1.02, 1), loc="upper left")
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _plot_task_size(summary: pd.DataFrame, path: Path) -> None:
    if summary.empty:
        return
    fig, ax = plt.subplots(figsize=(8, 5), constrained_layout=True)
    for region, frame in summary.groupby("region"):
        ax.plot(frame["task_size_multiplier"], frame["observation_success_percent"], marker="o", label=region)
    ax.set(xlabel="Task-size multiplier", ylabel="Observation success (%)", title="Task-size sensitivity")
    ax.grid(alpha=.25); ax.legend()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def run_analysis(config_path: str | Path, output_override: str | Path | None = None) -> dict[str, Any]:
    config = CampaignConfig.load(config_path)
    output_root = Path(output_override).resolve() if output_override else config.output_root.resolve()
    analysis_root = output_root / "analysis"
    figures = analysis_root / "figures"
    tables = analysis_root / "tables"
    figures.mkdir(parents=True, exist_ok=True)
    tables.mkdir(parents=True, exist_ok=True)
    case_metrics = collect_case_metrics(output_root)
    write_parquet_atomic(case_metrics, analysis_root / "canonical_case_metrics.parquet")
    completed = case_metrics.loc[case_metrics["status"] == "COMPLETED"].copy()

    spearman, pearson = correlation_tables(completed)
    spearman.to_csv(tables / "correlation_spearman.csv")
    pearson.to_csv(tables / "correlation_pearson.csv")
    regression = regression_comparison(completed, config)
    regression.to_csv(tables / "regression_model_comparison.csv", index=False)
    pareto = pareto_table(completed)
    write_parquet_atomic(pareto, tables / "exact_pareto.parquet")
    ranks = rank_weight_sensitivity(completed, config)
    ranks.to_csv(tables / "rank_weight_sensitivity.csv", index=False)
    robust = robustness_summary(completed)
    robust.to_csv(tables / "robustness_summary.csv", index=False)
    task_size = task_size_summary(completed)
    task_size.to_csv(tables / "task_size_summary.csv", index=False)
    policy = policy_summary(completed)
    policy.to_csv(tables / "policy_ablation_summary.csv", index=False)
    regional = regional_rank_agreement(completed)
    regional.to_csv(tables / "regional_rank_agreement.csv", index=False)
    resources = resource_cost_vectors(completed, config)
    write_parquet_atomic(resources, tables / "resource_cost_vectors.parquet")
    _plot_robustness(robust, figures / "robustness.png")
    _plot_task_size(task_size, figures / "task_size_sensitivity.png")

    case_manifest_path = output_root / "campaign_case_manifest.parquet"
    scenario_manifest_path = output_root / "scenario_manifest.parquet"
    expected_cases = len(pd.read_parquet(case_manifest_path)) if case_manifest_path.exists() else config.case_count_per_region * len(config.data["regions"])
    expected_scenarios = len(pd.read_parquet(scenario_manifest_path)) if scenario_manifest_path.exists() else len(build_scenarios(config.data))
    expected_rows = expected_cases * expected_scenarios
    report = {
        "case_metric_rows": len(case_metrics), "completed_rows": len(completed),
        "not_applicable_rows": int((case_metrics["status"] == "NOT_APPLICABLE").sum()),
        "failed_rows": int((case_metrics["status"] == "FAILED").sum()),
        "architectures_present": int(case_metrics[["region", "case_id"]].drop_duplicates().shape[0]),
        "architectures_expected": expected_cases,
        "scenario_rows_expected": expected_rows,
        "scenario_rows_present": len(case_metrics),
        "campaign_complete": len(case_metrics) == expected_rows and int((case_metrics["status"] == "FAILED").sum()) == 0,
        "regression_models_compared": len(regression),
        "pareto_cases": int(pareto.get("is_exact_pareto", pd.Series(dtype=bool)).sum()) if not pareto.empty else 0,
    }
    write_json_atomic(report, analysis_root / "analysis_report.json")
    (analysis_root / "VINCENZO_UPDATE.md").write_text(
        "# Full891 fresh campaign update\n\n"
        f"Validated architecture-region combinations: {report['architectures_present']} / {expected_cases}.\n\n"
        "The campaign includes fresh orbital/contact generation, nominal and unlimited dissemination, "
        "central/peer/hybrid policy ablation, task-size sensitivity, failure robustness, T100/P100 with "
        "censoring, regression comparison, exact Pareto analysis, and weight sensitivity.\n\n"
        "See `analysis_report.json` and the files under `tables/` for the numerical results. Monetary cost "
        "is intentionally omitted unless traceable cost coefficients are supplied.\n",
        encoding="utf-8",
    )
    return report
