from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


COMPARISONS = [
    ("policy_central", "policy_peer"),
    ("policy_central", "policy_hybrid"),
    ("task_size_x1p0", "task_size_x0p5"),
    ("task_size_x1p0", "task_size_x2p0"),
    ("task_size_x1p0", "task_size_x4p0"),
    ("nominal_original", "unlimited_useful_deadline"),
]


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def pair_contract(frame: pd.DataFrame, expected: dict[str, int]) -> pd.DataFrame:
    rows: list[dict] = []
    for region, region_group in frame.groupby("region"):
        for reference, alternative in COMPARISONS:
            selected = region_group.loc[region_group["scenario_id"].isin([reference, alternative])]
            pivot = selected.pivot_table(index="case_id", columns="scenario_id", values="n_tasks", aggfunc="last")
            both = pivot.dropna(subset=[reference, alternative]) if {reference, alternative}.issubset(pivot.columns) else pd.DataFrame()
            if both.empty:
                rows.append(
                    {
                        "region": region,
                        "reference_scenario": reference,
                        "alternative_scenario": alternative,
                        "paired_cases": 0,
                        "same_task_count_cases": 0,
                        "intended_task_count_cases": 0,
                        "mismatched_task_count_cases": 0,
                    }
                )
                continue
            same = both[reference].eq(both[alternative])
            intended = same & both[reference].eq(expected[region])
            rows.append(
                {
                    "region": region,
                    "reference_scenario": reference,
                    "alternative_scenario": alternative,
                    "paired_cases": int(len(both)),
                    "same_task_count_cases": int(same.sum()),
                    "intended_task_count_cases": int(intended.sum()),
                    "mismatched_task_count_cases": int((~same).sum()),
                }
            )
    return pd.DataFrame(rows)


def combined_stress_summary(frame: pd.DataFrame, complete_module, draws: int, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    baseline = frame.loc[frame["scenario_id"] == "unlimited_useful_deadline"].set_index(["region", "case_id"])
    combined = frame.loc[frame["family"] == "combined_stress"].copy()
    rows: list[dict] = []
    for (region, case_id, size, level), group in combined.groupby(
        ["region", "case_id", "task_size_multiplier", "failure_level_percent"]
    ):
        if (region, case_id) not in baseline.index:
            continue
        base = baseline.loc[(region, case_id)]
        if isinstance(base, pd.DataFrame):
            base = base.iloc[-1]
        row = {
            "region": region,
            "case_id": case_id,
            "base_id": complete_module.base_id(case_id),
            "task_size_multiplier": float(size),
            "failure_level_percent": float(level),
            "replicates": int(group["replicate"].nunique()),
        }
        for metric in ["observation_success_percent", "s100_surviving_before_deadline_percent"]:
            stressed = float(pd.to_numeric(group[metric], errors="coerce").mean())
            nominal = float(base[metric])
            row[f"baseline_{metric}"] = nominal
            row[f"stressed_{metric}"] = stressed
            row[f"delta_{metric}"] = stressed - nominal
            row[f"retention_{metric}"] = stressed / nominal if nominal > 1e-12 else np.nan
        rows.append(row)
    detail = pd.DataFrame(rows)
    rng = np.random.default_rng(seed)
    summary_rows: list[dict] = []
    for (region, size, level), group in detail.groupby(["region", "task_size_multiplier", "failure_level_percent"]):
        for metric in ["observation_success_percent", "s100_surviving_before_deadline_percent"]:
            for quantity in ["stressed", "delta", "retention"]:
                column = f"{quantity}_{metric}"
                mean, low, high = complete_module.cluster_bootstrap_mean(group, column, draws, rng)
                summary_rows.append(
                    {
                        "region": region,
                        "task_size_multiplier": size,
                        "failure_level_percent": level,
                        "metric": metric,
                        "quantity": quantity,
                        "cases": int(group["case_id"].nunique()),
                        "base_architectures": int(group["base_id"].nunique()),
                        "mean": mean,
                        "bootstrap_low": low,
                        "bootstrap_high": high,
                        "p05": float(pd.to_numeric(group[column], errors="coerce").quantile(0.05)),
                    }
                )
    return detail, pd.DataFrame(summary_rows)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis-output", type=Path, required=True)
    parser.add_argument("--intended-task-csv", type=Path, required=True)
    parser.add_argument("--postprocessor", type=Path, required=True)
    parser.add_argument("--draws", type=int, default=1000)
    parser.add_argument("--weight-samples", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20260917)
    args = parser.parse_args()

    sys.path.insert(0, str(args.postprocessor.parent))
    import postprocess_complete as complete

    tables = args.analysis_output / "tables"
    canonical_path = tables / "canonical_completed_rows_all_supplied_regions.parquet"
    frame = pd.read_parquet(canonical_path)
    frame["n_tasks"] = pd.to_numeric(frame["n_tasks"], errors="coerce")

    intended_tasks = pd.read_csv(args.intended_task_csv)
    expected = intended_tasks.groupby("region")["task_id"].nunique().astype(int).to_dict()

    family_counts = (
        frame.groupby(["region", "family", "n_tasks"], dropna=False)
        .agg(rows=("scenario_id", "size"), cases=("case_id", "nunique"), scenarios=("scenario_id", "nunique"))
        .reset_index()
    )
    write_csv(family_counts, tables / "task_population_by_family.csv")

    case_contract = (
        frame.groupby(["region", "case_id", "base_id", "n_satellites", "n_planes", "altitude_km", "inclination_deg", "cn_fraction_percent"])
        .agg(
            completed_rows=("scenario_id", "size"),
            distinct_task_counts=("n_tasks", "nunique"),
            minimum_task_count=("n_tasks", "min"),
            maximum_task_count=("n_tasks", "max"),
        )
        .reset_index()
    )
    case_contract["intended_task_count"] = case_contract["region"].map(expected)
    case_contract["all_rows_use_intended_task_count"] = (
        case_contract["distinct_task_counts"].eq(1)
        & case_contract["minimum_task_count"].eq(case_contract["intended_task_count"])
    )
    write_csv(case_contract, tables / "case_task_population_contract.csv")

    pair_audit = pair_contract(frame, expected)
    write_csv(pair_audit, tables / "scenario_pair_task_population_contract.csv")

    india_audit = pd.read_csv(args.analysis_output / "regional_India" / "tables" / "archive_case_audit.csv")
    invalid_india = india_audit.loc[pd.to_numeric(india_audit["task_count"], errors="coerce") != expected["India"]].copy()
    invalid_india["rerun_reason"] = (
        "Case manifest uses 741 tasks; intended frozen thesis population is 773 India tasks (805 total with California)."
    )
    write_csv(invalid_india, tables / "india_cases_requiring_773_task_rerun.csv")

    primary = frame.loc[frame["scenario_id"].isin(["nominal_original", "unlimited_useful_deadline"])].copy()
    primary_expected = primary.loc[
        primary.apply(lambda row: row["n_tasks"] == expected[str(row["region"])], axis=1)
    ]
    india_primary = primary_expected.loc[primary_expected["region"] == "India"]
    complete_india_bases = (
        india_primary.groupby("base_id")["case_id"].nunique().loc[lambda values: values == 11].index.astype(str)
    )
    # The fully balanced intended-task population consists of the 60- and 120-satellite bases.
    safe_bases = sorted(
        set(complete_india_bases)
        & set(india_primary.loc[india_primary["n_satellites"].isin([60, 120]), "base_id"].astype(str))
    )
    safe_cases = sorted(
        india_primary.loc[india_primary["base_id"].astype(str).isin(safe_bases), "case_id"].astype(str).unique()
    )
    safe_case_table = (
        primary.loc[primary["case_id"].astype(str).isin(safe_cases), [
            "case_id", "base_id", "n_satellites", "n_planes", "altitude_km", "inclination_deg", "cn_fraction_percent"
        ]]
        .drop_duplicates("case_id")
        .sort_values("case_id")
    )
    write_csv(safe_case_table, tables / "uniform_60_120_safe_case_ids.csv")

    uniform = frame.loc[frame["case_id"].astype(str).isin(safe_cases)].copy()
    uniform = uniform.loc[
        uniform.apply(lambda row: row["n_tasks"] == expected[str(row["region"])], axis=1)
    ]

    correlation_matrix, redundancy = complete.metric_correlations(uniform, 0.95)
    correlation_matrix.to_csv(tables / "uniform_60_120_performance_metric_correlation_matrices.csv")
    write_csv(redundancy, tables / "uniform_60_120_metric_pair_redundancy.csv")
    write_csv(
        complete.weighting_proposal(redundancy),
        tables / "uniform_60_120_weighting_proposal_for_review.csv",
    )

    cn_summary, cn_absolute, cn_deltas = complete.cn_effects(uniform, args.draws, args.seed)
    write_csv(cn_summary, tables / "uniform_60_120_cn_fraction_intervals.csv")
    write_csv(cn_absolute, tables / "uniform_60_120_cn_absolute_count_summary.csv")
    write_csv(cn_deltas, tables / "uniform_60_120_cn_adjacent_paired_deltas.csv")
    high_cn, high_cn_detail = complete.high_cn_diagnosis(cn_deltas, args.draws, args.seed + 1)
    write_csv(high_cn, tables / "uniform_60_120_high_cn_decline_diagnosis.csv")
    write_csv(high_cn_detail, tables / "uniform_60_120_high_cn_cases.csv")

    pair_detail, pair_summary = complete.paired_scenario_effects(uniform, args.draws, args.seed + 2)
    write_csv(pair_detail, tables / "uniform_60_120_paired_scenario_effects.csv")
    write_csv(pair_summary, tables / "uniform_60_120_paired_scenario_effects_summary.csv")

    robust_detail, robust_summary, robust_auc = complete.robustness_analysis(uniform, args.draws, args.seed + 3)
    write_csv(robust_detail, tables / "uniform_60_120_robustness_detail.csv")
    write_csv(robust_summary, tables / "uniform_60_120_robustness_summary.csv")
    write_csv(robust_auc, tables / "uniform_60_120_robustness_auc.csv")

    baseline = uniform.loc[uniform["scenario_id"] == "unlimited_useful_deadline"].copy()
    baseline = complete.add_robustness_score(baseline, robust_auc)
    pareto, winners, acceptability = complete.pareto_and_weights(
        baseline, args.weight_samples, args.seed + 4
    )
    write_csv(pareto, tables / "uniform_60_120_exact_pareto.csv")
    write_csv(winners, tables / "uniform_60_120_random_weight_winners.csv")
    write_csv(acceptability, tables / "uniform_60_120_rank_acceptability.csv")
    shortlist = complete.build_shortlist(pareto, acceptability, 5)
    write_csv(shortlist, tables / "uniform_60_120_conditional_top5_shortlist.csv")

    cross_detail, cross_summary, rank_agreement = complete.cross_region_analysis(
        baseline, args.draws, args.seed + 5
    )
    cross_detail["transferred_data_mbits_per_task"] = (
        pd.to_numeric(cross_detail.get("total_transferred_data_mbits_India"), errors="coerce")
        / expected["India"]
        - pd.to_numeric(cross_detail.get("total_transferred_data_mbits_California"), errors="coerce")
        / expected["California"]
    ) if "total_transferred_data_mbits_India" in cross_detail else np.nan
    write_csv(cross_detail, tables / "uniform_60_120_matched_region_case_deltas.csv")
    write_csv(cross_summary, tables / "uniform_60_120_matched_region_delta_summary.csv")
    write_csv(rank_agreement, tables / "uniform_60_120_regional_rank_agreement.csv")

    factor_effects = complete.factor_range_effects(uniform)
    write_csv(factor_effects, tables / "uniform_60_120_factor_effects.csv")

    combined_detail, combined_summary = combined_stress_summary(uniform, complete, args.draws, args.seed + 6)
    write_csv(combined_detail, tables / "uniform_60_120_combined_stress_detail.csv")
    write_csv(combined_summary, tables / "uniform_60_120_combined_stress_summary.csv")

    summary = {
        "intended_task_counts": expected,
        "india_case_manifests_using_741_tasks": int(len(invalid_india)),
        "cases_with_multiple_task_counts_across_scenarios": int((case_contract["distinct_task_counts"] > 1).sum()),
        "safe_uniform_base_architectures": len(safe_bases),
        "safe_uniform_cases_per_region": len(safe_cases),
        "safe_satellite_levels": [60, 120],
        "full_grid_scientific_status": "BLOCKED_BY_INDIA_TASK_POPULATION_DRIFT",
        "uniform_subset_status": "VALID_SENSITIVITY_SUBSET",
        "required_rerun_cases": int(len(invalid_india)),
    }
    (args.analysis_output / "data_contract_audit_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
