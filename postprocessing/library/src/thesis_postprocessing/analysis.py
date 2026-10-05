"""Gate-controlled CN, regional, cost, Pareto, and rank-stability analyses."""

from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from .canonical import CanonicalResult
from .validation import ValidationResult


SEED = 20260811
RANK_DRAWS = 10_000
METRICS = [
    "observation_success_percent",
    "full_dissemination_success_percent_s100",
    "mean_receiver_fraction_at_deadline_percent",
    "p90_first_reception_latency_min",
    "total_transferred_data_mbits",
]


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def _minmax(series: pd.Series, invert: bool = False) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").astype(float)
    finite = values[np.isfinite(values)]
    if finite.empty:
        scaled = pd.Series(0.0, index=series.index)
    else:
        filled = values.fillna(finite.max() if invert else finite.min())
        low, high = float(filled.min()), float(filled.max())
        scaled = pd.Series(1.0, index=series.index) if math.isclose(low, high) else (filled - low) / (high - low)
    return 1.0 - scaled if invert else scaled


def _metadata(path: Path, title: str, data_files: list[str], definitions: dict[str, object]) -> None:
    path.with_suffix(".metadata.json").write_text(
        json.dumps(
            {
                "title": title,
                "data_files": data_files,
                "definitions": definitions,
                "limitations": [
                    "Associations across the discrete design grid are not causal effects.",
                    "Latency summaries are conditional on successful completion; censoring is reported separately.",
                    "CN=0 under central_only means no central relay, not an unrestricted peer-to-peer baseline.",
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _save(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(path, dpi=220, bbox_inches="tight")
    plt.close()


def _exact_pareto(frame: pd.DataFrame, maximize: list[str], minimize: list[str]) -> pd.Series:
    work = frame[maximize + minimize].copy()
    for column in maximize:
        values = pd.to_numeric(work[column], errors="coerce")
        work[column] = values.fillna(-np.inf)
    for column in minimize:
        values = pd.to_numeric(work[column], errors="coerce")
        work[column] = -values.fillna(np.inf)
    matrix = work.to_numpy(dtype=float)
    efficient = np.ones(len(matrix), dtype=bool)
    for index, point in enumerate(matrix):
        if not efficient[index]:
            continue
        dominates_point = np.all(matrix >= point, axis=1) & np.any(matrix > point, axis=1)
        dominates_point[index] = False
        if dominates_point.any():
            efficient[index] = False
    return pd.Series(efficient, index=frame.index)


def _classify_trend(group: pd.DataFrame, metric: str) -> tuple[str, float, float, float | None]:
    ordered = group.sort_values("cn_fraction_percent")
    x = ordered["cn_fraction_percent"].to_numpy(dtype=float)
    y = pd.to_numeric(ordered[metric], errors="coerce").to_numpy(dtype=float)
    valid = np.isfinite(y)
    rho = float(spearmanr(x[valid], y[valid]).statistic) if valid.sum() >= 3 and len(np.unique(y[valid])) > 1 else math.nan
    deltas = np.diff(y)
    tolerance = 1e-9
    if np.all(deltas >= -tolerance):
        label = "monotonic_nondecreasing"
    elif np.all(deltas <= tolerance):
        label = "monotonic_nonincreasing"
    elif 0 < int(np.nanargmax(y)) < len(y) - 1:
        label = "interior_peak_or_mixed"
    else:
        label = "mixed"
    saturation: float | None = None
    for index in range(len(deltas)):
        if np.all(np.abs(deltas[index:]) < 1.0):
            saturation = float(x[index])
            break
    return label, rho, float(y[-1] - y[0]), saturation


def _rank_sensitivity(region_frame: pd.DataFrame, draws: int = RANK_DRAWS) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(SEED)
    component_names = ["observation", "s100", "receiver_fraction", "timeliness", "resource_efficiency"]
    components = pd.DataFrame(
        {
            "observation": _minmax(region_frame["observation_success_percent"]),
            "s100": _minmax(region_frame["full_dissemination_success_percent_s100"]),
            "receiver_fraction": _minmax(region_frame["mean_receiver_fraction_at_deadline_percent"]),
            "timeliness": _minmax(region_frame["p90_first_reception_latency_min"], invert=True),
            "resource_efficiency": _minmax(region_frame["resource_burden_proxy"], invert=True),
        }
    )
    weights = rng.dirichlet(np.ones(len(component_names)), size=draws)
    values = components.to_numpy(dtype=float)
    ranks = np.empty((len(region_frame), draws), dtype=np.uint16)
    winners = np.empty(draws, dtype=np.int32)
    batch_size = 250
    for start in range(0, draws, batch_size):
        end = min(draws, start + batch_size)
        scores = values @ weights[start:end].T
        order = np.argsort(-scores, axis=0, kind="stable")
        batch_ranks = np.empty_like(order, dtype=np.uint16)
        columns = np.arange(end - start)
        batch_ranks[order, columns] = np.arange(1, len(region_frame) + 1, dtype=np.uint16)[:, None]
        ranks[:, start:end] = batch_ranks
        winners[start:end] = order[0, :]

    rows = []
    for index, (_, case) in enumerate(region_frame.reset_index(drop=True).iterrows()):
        case_ranks = ranks[index].astype(float)
        rows.append(
            {
                "region": case["region"],
                "case_id": case["case_id"],
                "winner_frequency": float((winners == index).mean()),
                "top_10_frequency": float((case_ranks <= 10).mean()),
                "mean_rank": float(case_ranks.mean()),
                "median_rank": float(np.median(case_ranks)),
                "p05_rank": float(np.quantile(case_ranks, 0.05)),
                "p95_rank": float(np.quantile(case_ranks, 0.95)),
                "min_rank": int(case_ranks.min()),
                "max_rank": int(case_ranks.max()),
            }
        )
    weight_frame = pd.DataFrame(weights, columns=[f"weight_{name}" for name in component_names])
    weight_frame.insert(0, "draw_id", np.arange(draws))
    weight_frame["winner_case_id"] = region_frame.reset_index(drop=True).iloc[winners]["case_id"].to_numpy()
    return pd.DataFrame(rows), weight_frame


def run_supported_analyses(
    validation: ValidationResult,
    canonical: CanonicalResult,
    output_root: Path,
) -> pd.DataFrame:
    if not (validation.gate_b_pass and canonical.gate_c_pass):
        raise RuntimeError("Gate B and Gate C must pass before analytical phases")

    case_fields = validation.cases[
        [
            "region",
            "case_id",
            "base_architecture_id",
            "n_satellites_id",
            "n_planes_id",
            "altitude_km_id",
            "inclination_deg_id",
            "cn_fraction_percent_id",
            "n_central_nodes",
            "reused_case",
        ]
    ].rename(
        columns={
            "n_satellites_id": "n_satellites",
            "n_planes_id": "n_planes",
            "altitude_km_id": "altitude_km",
            "inclination_deg_id": "inclination_deg",
            "cn_fraction_percent_id": "cn_fraction_percent",
        }
    )
    frame = case_fields.merge(canonical.case_metrics, on=["region", "case_id"], how="inner", validate="one_to_one")
    frame = frame.merge(canonical.network_metrics, on=["region", "case_id"], how="left", validate="one_to_one")
    for column in ["n_satellites", "n_planes", "altitude_km", "inclination_deg", "cn_fraction_percent", "n_central_nodes"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["transferred_data_per_task_mbits"] = frame["total_transferred_data_mbits"] / frame["n_tasks"]

    nominal_dir = output_root / "03_nominal_cn_analysis"
    raw_columns = [
        "region",
        "case_id",
        "base_architecture_id",
        "n_satellites",
        "n_planes",
        "altitude_km",
        "inclination_deg",
        "cn_fraction_percent",
        "n_central_nodes",
        *METRICS,
        "mean_full_dissemination_latency_min",
        "p90_observation_latency_min",
        "first_reception_censored_tasks",
        "full_dissemination_censored_tasks",
    ]
    raw_sweeps = frame[raw_columns].sort_values(["region", "base_architecture_id", "cn_fraction_percent"])
    _write_csv(raw_sweeps, nominal_dir / "architecture_cn_sweeps.csv")
    marginal_rows = []
    trend_rows = []
    for (region, base_id), group in raw_sweeps.groupby(["region", "base_architecture_id"]):
        ordered = group.sort_values("cn_fraction_percent").copy()
        for metric in METRICS[:4]:
            ordered[f"delta_{metric}"] = ordered[metric].diff()
        ordered["added_central_nodes"] = ordered["n_central_nodes"].diff()
        ordered["observation_gain_per_added_cn"] = ordered["delta_observation_success_percent"] / ordered["added_central_nodes"].replace(0, np.nan)
        ordered["s100_gain_per_added_cn"] = ordered["delta_full_dissemination_success_percent_s100"] / ordered["added_central_nodes"].replace(0, np.nan)
        marginal_rows.append(ordered)
        for metric in ["observation_success_percent", "full_dissemination_success_percent_s100", "p90_first_reception_latency_min"]:
            label, rho, endpoint_change, saturation = _classify_trend(ordered, metric)
            trend_rows.append(
                {
                    "region": region,
                    "base_architecture_id": base_id,
                    "metric": metric,
                    "trend_class": label,
                    "spearman_rho": rho,
                    "cn100_minus_cn0": endpoint_change,
                    "candidate_saturation_cn_fraction_percent": saturation,
                }
            )
    marginal = pd.concat(marginal_rows, ignore_index=True)
    trends = pd.DataFrame(trend_rows)
    _write_csv(marginal, nominal_dir / "cn_marginal_effects.csv")
    _write_csv(trends, nominal_dir / "cn_trend_classification.csv")

    cn_aggregate = (
        frame.groupby(["region", "cn_fraction_percent"])
        .agg(
            architectures=("case_id", "size"),
            observation_mean=("observation_success_percent", "mean"),
            observation_q25=("observation_success_percent", lambda s: s.quantile(0.25)),
            observation_q75=("observation_success_percent", lambda s: s.quantile(0.75)),
            s100_mean=("full_dissemination_success_percent_s100", "mean"),
            receiver_fraction_mean=("mean_receiver_fraction_at_deadline_percent", "mean"),
            p90_first_reception_mean=("p90_first_reception_latency_min", "mean"),
        )
        .reset_index()
    )
    _write_csv(cn_aggregate, nominal_dir / "cn_aggregate_by_region.csv")

    architecture_dir = output_root / "04_architecture_effects"
    factor_rows = []
    for region, region_frame in frame.groupby("region"):
        for factor in ["n_satellites", "n_planes", "altitude_km", "inclination_deg", "cn_fraction_percent"]:
            for level, group in region_frame.groupby(factor):
                row = {"region": region, "factor": factor, "level": level, "cases": len(group)}
                for metric in METRICS[:4]:
                    row[f"mean_{metric}"] = group[metric].mean()
                    row[f"median_{metric}"] = group[metric].median()
                factor_rows.append(row)
    factor_effects = pd.DataFrame(factor_rows)
    _write_csv(factor_effects, architecture_dir / "factor_level_effects.csv")
    correlation_rows = []
    for region, region_frame in frame.groupby("region"):
        for factor in ["n_satellites", "n_planes", "altitude_km", "inclination_deg", "cn_fraction_percent"]:
            for metric in METRICS:
                subset = region_frame[[factor, metric]].dropna()
                rho = spearmanr(subset[factor], subset[metric]).statistic if len(subset) >= 3 else math.nan
                correlation_rows.append({"region": region, "factor": factor, "metric": metric, "spearman_rho": rho, "n": len(subset)})
    correlations = pd.DataFrame(correlation_rows)
    _write_csv(correlations, architecture_dir / "exploratory_spearman_associations.csv")
    (architecture_dir / "interpretation_guardrails.md").write_text(
        "# Architecture-effects guardrails\n\nThese are controlled-grid associations, not causal estimates. Factor levels are discrete and interactions are expected. CN=0 is a no-central-relay condition under `central_only`; it is not a peer-to-peer baseline.\n",
        encoding="utf-8",
    )

    regional_dir = output_root / "05_regional_comparison"
    paired = frame[frame["region"] == "California"].merge(
        frame[frame["region"] == "India"], on="case_id", suffixes=("_california", "_india"), validate="one_to_one"
    )
    for metric in METRICS[:4] + ["transferred_data_per_task_mbits", "transfer_events_per_task"]:
        paired[f"delta_{metric}_india_minus_california"] = paired[f"{metric}_india"] - paired[f"{metric}_california"]
    paired_columns = ["case_id"] + [column for column in paired if column.startswith("delta_")]
    _write_csv(paired[paired_columns], regional_dir / "paired_case_differences.csv")
    rank_agreement_rows = []
    for metric in METRICS[:4]:
        subset = paired[[f"{metric}_california", f"{metric}_india"]].dropna()
        rank_agreement_rows.append(
            {
                "metric": metric,
                "spearman_rank_agreement": spearmanr(subset.iloc[:, 0], subset.iloc[:, 1]).statistic,
                "paired_cases": len(subset),
            }
        )
    rank_agreement = pd.DataFrame(rank_agreement_rows)
    _write_csv(rank_agreement, regional_dir / "regional_rank_agreement.csv")
    regional_summary = (
        frame.groupby("region")
        .agg(
            cases=("case_id", "size"),
            tasks_per_case=("n_tasks", "mean"),
            mean_observation_success_percent=("observation_success_percent", "mean"),
            mean_s100_percent=("full_dissemination_success_percent_s100", "mean"),
            mean_receiver_fraction_percent=("mean_receiver_fraction_at_deadline_percent", "mean"),
            mean_transfer_data_per_task_mbits=("transferred_data_per_task_mbits", "mean"),
            mean_transfer_events_per_task=("transfer_events_per_task", "mean"),
        )
        .reset_index()
    )
    _write_csv(regional_summary, regional_dir / "regional_normalized_summary.csv")

    cost_dir = output_root / "06_cost_and_pareto"
    cost_frames = []
    for region, region_frame in frame.groupby("region", sort=True):
        work = region_frame.copy()
        work["resource_satellite_component"] = _minmax(work["n_satellites"])
        work["resource_central_node_component"] = _minmax(work["n_central_nodes"])
        work["communication_data_component"] = _minmax(work["transferred_data_per_task_mbits"])
        work["communication_event_component"] = _minmax(work["transfer_events_per_task"])
        work["resource_burden_proxy"] = (
            0.40 * work["resource_satellite_component"]
            + 0.20 * work["resource_central_node_component"]
            + 0.20 * work["communication_data_component"]
            + 0.20 * work["communication_event_component"]
        )
        work["pareto_optimal"] = _exact_pareto(
            work,
            maximize=["observation_success_percent", "full_dissemination_success_percent_s100", "mean_receiver_fraction_at_deadline_percent"],
            minimize=["p90_first_reception_latency_min", "resource_burden_proxy"],
        )
        cost_frames.append(work)
    frame = pd.concat(cost_frames, ignore_index=True)
    cost_columns = [
        "region",
        "case_id",
        "n_satellites",
        "n_central_nodes",
        "transferred_data_per_task_mbits",
        "transfer_events_per_task",
        "resource_satellite_component",
        "resource_central_node_component",
        "communication_data_component",
        "communication_event_component",
        "resource_burden_proxy",
        "pareto_optimal",
        "observation_success_percent",
        "full_dissemination_success_percent_s100",
        "mean_receiver_fraction_at_deadline_percent",
        "p90_first_reception_latency_min",
    ]
    _write_csv(frame[cost_columns], cost_dir / "resource_cost_vectors_and_exact_pareto.csv")
    (cost_dir / "cost_definition.md").write_text(
        "\n".join(
            [
                "# Cost definition",
                "",
                "No monetary cost is claimed. The reported vector contains architecture size, central-node count, transferred data per task, and transfer events per task.",
                "The displayed scalar resource-burden proxy uses declared weights 0.40/0.20/0.20/0.20 after within-region min-max scaling.",
                "The Pareto set is computed exactly over observation success, S100, receiver fraction, conditional P90 latency, and the declared resource proxy. No genetic algorithm is used.",
            ]
        ),
        encoding="utf-8",
    )

    sensitivity_dir = output_root / "07_rank_sensitivity"
    stability_frames = []
    for region, region_frame in frame.groupby("region", sort=True):
        stability, weights = _rank_sensitivity(region_frame.reset_index(drop=True), RANK_DRAWS)
        stability_frames.append(stability)
        _write_csv(weights, sensitivity_dir / f"weight_draws_and_winners_{region.lower()}.csv")
    stability_frame = pd.concat(stability_frames, ignore_index=True)
    _write_csv(stability_frame, sensitivity_dir / "rank_stability.csv")
    (sensitivity_dir / "method.md").write_text(
        "\n".join(
            [
                "# Rank-sensitivity method",
                "",
                f"- Draws: `{RANK_DRAWS}` Dirichlet(1,1,1,1,1) weight vectors",
                f"- Random seed: `{SEED}`",
                "- Benefit components: observation success, S100, receiver fraction, inverse conditional P90 first-reception latency, inverse declared resource burden.",
                "- Results are reported as rank intervals and inclusion frequencies; no universal optimum is asserted.",
            ]
        ),
        encoding="utf-8",
    )

    robustness_dir = output_root / "08_robustness"
    robustness_dir.mkdir(parents=True, exist_ok=True)
    (robustness_dir / "STATUS.md").write_text(
        "# Robustness status\n\nNot executed: the archive contains one nominal realization per case and no validated offline failure-replay engine. Observed failure classes remain nominal outcome diagnostics, not Monte Carlo robustness evidence. See `13_additional_runs_plan`.\n",
        encoding="utf-8",
    )
    task_size_dir = output_root / "09_task_size_sensitivity"
    task_size_dir.mkdir(parents=True, exist_ok=True)
    (task_size_dir / "STATUS.md").write_text(
        "# Task-size sensitivity status\n\nNot claimed as a controlled sweep. Task size varies by priority in the nominal campaign and is confounded with priority/deadline. A dedicated replay or targeted V19 rerun is required.\n",
        encoding="utf-8",
    )

    _build_figures(frame, cn_aggregate, rank_agreement, stability_frame, output_root)
    _build_tables(frame, regional_summary, rank_agreement, stability_frame, output_root)
    _write_interpretation_notes(frame, output_root)
    _write_additional_runs_plan(output_root)
    return frame


def _build_figures(
    frame: pd.DataFrame,
    cn_aggregate: pd.DataFrame,
    rank_agreement: pd.DataFrame,
    stability: pd.DataFrame,
    output_root: Path,
) -> None:
    out = output_root / "10_thesis_figures"
    out.mkdir(parents=True, exist_ok=True)
    colors = {"California": "#d95f02", "India": "#1b9e77"}

    fig, axes = plt.subplots(2, 2, figsize=(11, 7.5), sharex=True)
    definitions = [
        ("observation_mean", "Observation success [%]"),
        ("s100_mean", "S100 full dissemination [%]"),
        ("receiver_fraction_mean", "Receiver fraction at deadline [%]"),
        ("p90_first_reception_mean", "Conditional P90 first reception [min]"),
    ]
    for ax, (metric, label) in zip(axes.ravel(), definitions):
        for region, group in cn_aggregate.groupby("region"):
            ax.plot(group["cn_fraction_percent"], group[metric], marker="o", label=region, color=colors.get(region))
        ax.set_ylabel(label)
        ax.set_xlabel("Central-node fraction [%]")
    axes[0, 0].legend()
    _save(out / "01_cn_sweep_regional_summary.png")
    _write_csv(cn_aggregate, out / "01_cn_sweep_regional_summary.data.csv")
    _metadata(out / "01_cn_sweep_regional_summary.png", "CN sweep regional summary", ["01_cn_sweep_regional_summary.data.csv"], {"aggregation": "mean over 81 architecture families at each CN fraction"})

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.8))
    for ax, region in zip(axes, ["California", "India"]):
        group = frame[frame["region"] == region]
        pareto = group[group["pareto_optimal"]]
        ax.scatter(group["resource_burden_proxy"], group["observation_success_percent"], s=10, alpha=0.25, color=colors[region])
        ax.scatter(pareto["resource_burden_proxy"], pareto["observation_success_percent"], s=30, facecolors="none", edgecolors="black", label="Exact multi-objective Pareto set")
        ax.set_title(region)
        ax.set_xlabel("Declared resource-burden proxy [0–1]")
        ax.set_ylabel("Observation success [%]")
        ax.legend(fontsize=8)
    _save(out / "02_exact_pareto_landscape.png")
    pareto_data = frame[["region", "case_id", "resource_burden_proxy", "observation_success_percent", "full_dissemination_success_percent_s100", "p90_first_reception_latency_min", "pareto_optimal"]]
    _write_csv(pareto_data, out / "02_exact_pareto_landscape.data.csv")
    _metadata(out / "02_exact_pareto_landscape.png", "Exact Pareto landscape", ["02_exact_pareto_landscape.data.csv"], {"pareto_objectives": "See 06_cost_and_pareto/cost_definition.md"})

    top = stability.sort_values(["region", "winner_frequency", "top_10_frequency"], ascending=[True, False, False]).groupby("region").head(10)
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.2))
    for ax, region in zip(axes, ["California", "India"]):
        group = top[top["region"] == region].sort_values("winner_frequency")
        ax.barh(group["case_id"], 100 * group["winner_frequency"], color=colors[region])
        ax.set_title(region)
        ax.set_xlabel("Winner frequency across weights [%]")
        ax.tick_params(axis="y", labelsize=7)
    _save(out / "03_rank_weight_sensitivity.png")
    _write_csv(top, out / "03_rank_weight_sensitivity.data.csv")
    _metadata(out / "03_rank_weight_sensitivity.png", "Rank stability under 10,000 weight draws", ["03_rank_weight_sensitivity.data.csv"], {"seed": SEED, "draws": RANK_DRAWS})

    paired = frame[frame["region"] == "California"][["case_id", "observation_success_percent"]].merge(
        frame[frame["region"] == "India"][["case_id", "observation_success_percent"]], on="case_id", suffixes=("_california", "_india")
    )
    plt.figure(figsize=(6.4, 6.1))
    plt.scatter(paired["observation_success_percent_california"], paired["observation_success_percent_india"], s=12, alpha=0.35, color="#4c78a8")
    limits = [0, max(paired.filter(like="observation_success").max()) * 1.03]
    plt.plot(limits, limits, linestyle="--", color="black", linewidth=1)
    plt.xlim(limits)
    plt.ylim(limits)
    plt.xlabel("California observation success [%]")
    plt.ylabel("India observation success [%]")
    plt.title("Same architecture under different regional task loads")
    _save(out / "04_regional_paired_observation.png")
    _write_csv(paired, out / "04_regional_paired_observation.data.csv")
    _metadata(out / "04_regional_paired_observation.png", "Paired regional observation success", ["04_regional_paired_observation.data.csv"], {"pairing": "same case_id; task loads and event geometries differ"})


def _build_tables(frame: pd.DataFrame, regional: pd.DataFrame, agreement: pd.DataFrame, stability: pd.DataFrame, output_root: Path) -> None:
    out = output_root / "11_thesis_tables"
    out.mkdir(parents=True, exist_ok=True)
    _write_csv(regional, out / "table_regional_normalized_summary.csv")
    _write_csv(agreement, out / "table_regional_rank_agreement.csv")
    pareto_summary = frame.groupby("region").agg(cases=("case_id", "size"), pareto_cases=("pareto_optimal", "sum")).reset_index()
    _write_csv(pareto_summary, out / "table_exact_pareto_summary.csv")
    stable = stability.sort_values(["region", "winner_frequency", "top_10_frequency"], ascending=[True, False, False]).groupby("region").head(15)
    _write_csv(stable, out / "table_rank_stability_top15.csv")


def _write_interpretation_notes(frame: pd.DataFrame, output_root: Path) -> None:
    out = output_root / "12_interpretation_notes"
    out.mkdir(parents=True, exist_ok=True)
    notes = [
        "# Interpretation notes",
        "",
        "## Claims supported by the current archive",
        "",
        "- Comparisons across the complete 891-case grid within each event/region.",
        "- Architecture-specific 11-point central-node sweeps.",
        "- Conditional latency plus explicit completion/censoring rates.",
        "- Transfer-derived S100, receiver-fraction, communication burden, and node-load concentration.",
        "- Exact Pareto membership under the declared non-monetary resource vector.",
        "- Rank stability under 10,000 declared random weight vectors.",
        "",
        "## Claims not supported",
        "",
        "- CN=0 is not a peer-to-peer baseline because satellite-to-satellite mode is `central_only`.",
        "- No universal best architecture is claimed.",
        "- No causal interpretation is assigned to correlations or factor-level associations.",
        "- No monetary cost is inferred.",
        "- Nominal failure classes are not failure-injection robustness evidence.",
        "- Regional differences combine geometry, task density, priority mix, deadlines, and load; they are not a pure geographic effect.",
        "- The simulated mission chain begins with post-injection dissemination and follow-up observation; end-to-end onboard/fire detection is outside scope unless separately simulated.",
        "",
        f"Authoritative analytical rows: `{len(frame):,}` (two regions × 891 cases).",
    ]
    (out / "interpretation_notes.md").write_text("\n".join(notes), encoding="utf-8")


def _write_additional_runs_plan(output_root: Path) -> None:
    out = output_root / "13_additional_runs_plan"
    out.mkdir(parents=True, exist_ok=True)
    plan = [
        "# Targeted additional-runs plan",
        "",
        "No rerun is needed to complete the nominal dual-region 891-case analysis. The following are required only for broader thesis claims:",
        "",
        "1. **Policy ablation (highest priority):** run matched peer-enabled, central-only, and hybrid routing conditions for a predeclared architecture subset. This is required before calling CN=0 a decentralized/P2P baseline or making a general policy claim.",
        "2. **Failure robustness:** first implement and verify deterministic offline replay against nominal outputs. If valid, evaluate 0/5/10/20/30% failures using the same 30 seeded realizations per case. Otherwise run targeted V19 simulations on a Pareto-stratified subset.",
        "3. **Task-size sensitivity:** use a controlled size multiplier with task identities, priorities, deadlines, geometry, and seeds held fixed. The current priority-dependent sizes are confounded and cannot serve as a task-size sweep.",
        "4. **Cross-region mechanism study:** if a pure regional claim is needed, construct matched task-load/deadline scenarios rather than comparing the present 32-task and 773-task events directly.",
        "",
        "Selection rule for targeted cases: stratify by region, constellation size, CN fraction (0/30/60/100%), exact Pareto membership, and rank-stability tier; freeze the list before running.",
    ]
    (out / "additional_runs_plan.md").write_text("\n".join(plan), encoding="utf-8")
