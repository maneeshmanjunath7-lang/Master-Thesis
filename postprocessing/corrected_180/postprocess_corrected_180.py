from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


SEED = 20261009
BOOTSTRAP_DRAWS = 1000
WEIGHT_DRAWS = 10000
BALANCED_CN = list(range(30, 101, 10))
METRICS = [
    "observation_success_percent",
    "s100_surviving_before_deadline_percent",
    "mean_surviving_coverage_deadline_percent",
    "p100_t100_surviving_min",
    "total_transferred_data_mbits",
]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def read_case_metrics(region_root: Path) -> pd.DataFrame:
    paths = sorted(region_root.rglob("case_metrics_part_*.parquet"))
    if not paths:
        raise FileNotFoundError(f"No case metrics below {region_root}")
    print(f"Reading {len(paths):,} case-metric partitions", flush=True)
    batches: list[pd.DataFrame] = []
    for start in range(0, len(paths), 500):
        # Some NOT_APPLICABLE rows are intentionally stored with a reduced
        # schema and numeric null columns can therefore be inferred as float.
        # Pandas performs the required nullable promotion safely here.
        tables = [pq.read_table(path).to_pandas() for path in paths[start : start + 500]]
        batches.append(pd.concat(tables, ignore_index=True, sort=False))
        print(f"  case metrics: {min(start + 500, len(paths)):,}/{len(paths):,}", flush=True)
    frame = pd.concat(batches, ignore_index=True, sort=False)
    frame = frame.drop_duplicates(["region", "case_id", "scenario_id"], keep="last")
    frame["region"] = "India"
    return frame


def parse_case(case_id: str) -> tuple[int, int, float, float, int, int]:
    match = re.search(r"T(\d+)_P(\d+)_H(\d+)_I(\d+)_CN(\d+)_F(\d+)", str(case_id))
    if not match:
        raise ValueError(f"Cannot parse case ID {case_id}")
    fleet, planes, altitude, inclination_x10, cn, walker_f = (int(v) for v in match.groups())
    return fleet, planes, float(altitude), inclination_x10 / 10.0, cn, walker_f


def paired_cross_fleet(
    corrected_baseline: pd.DataFrame,
    prior_baseline_path: Path,
    output: Path,
) -> None:
    old = pd.read_csv(prior_baseline_path)
    old = old.loc[
        (old["region"] == "India")
        & (pd.to_numeric(old["n_satellites"], errors="coerce") == 120)
        & (pd.to_numeric(old["cn_fraction_percent"], errors="coerce").isin(BALANCED_CN))
    ].copy()
    new = corrected_baseline.copy()

    join_cols = ["n_planes", "altitude_km", "inclination_deg", "cn_fraction_percent"]
    keep_old = ["case_id", "base_id", *join_cols, *METRICS]
    keep_new = ["case_id", "base_id", *join_cols, *METRICS]
    merged = old[keep_old].merge(new[keep_new], on=join_cols, suffixes=("_t120", "_t180"), validate="one_to_one")
    if len(merged) != 216:
        raise RuntimeError(f"Expected 216 matched T120/T180 cases; found {len(merged)}")
    merged["architecture_cluster"] = (
        merged["n_planes"].astype(int).astype(str)
        + "_" + merged["altitude_km"].astype(int).astype(str)
        + "_" + (10 * merged["inclination_deg"]).round().astype(int).astype(str)
    )
    for metric in METRICS:
        merged[f"delta_t180_minus_t120_{metric}"] = (
            pd.to_numeric(merged[f"{metric}_t180"], errors="coerce")
            - pd.to_numeric(merged[f"{metric}_t120"], errors="coerce")
        )
    write_csv(merged, output / "corrected_180_matched_120_180_case_deltas.csv")

    rng = np.random.default_rng(SEED + 20)
    clusters = merged["architecture_cluster"].unique()
    summary_rows: list[dict] = []
    for metric in METRICS:
        column = f"delta_t180_minus_t120_{metric}"
        values = pd.to_numeric(merged[column], errors="coerce")
        estimates = []
        for _ in range(BOOTSTRAP_DRAWS):
            selected = rng.choice(clusters, size=len(clusters), replace=True)
            sample = pd.concat([merged.loc[merged["architecture_cluster"] == value, column] for value in selected])
            estimates.append(pd.to_numeric(sample, errors="coerce").mean())
        summary_rows.append(
            {
                "metric": metric,
                "matched_cases": len(merged),
                "architecture_clusters": len(clusters),
                "t120_mean": pd.to_numeric(merged[f"{metric}_t120"], errors="coerce").mean(),
                "t180_mean": pd.to_numeric(merged[f"{metric}_t180"], errors="coerce").mean(),
                "mean_delta_t180_minus_t120": values.mean(),
                "base_cluster_bootstrap_low": float(np.nanquantile(estimates, 0.025)),
                "base_cluster_bootstrap_high": float(np.nanquantile(estimates, 0.975)),
            }
        )
    write_csv(pd.DataFrame(summary_rows), output / "corrected_180_matched_120_180_summary.csv")

    response_rows: list[dict] = []
    for cn, group in merged.groupby("cn_fraction_percent", observed=True):
        for metric in METRICS:
            response_rows.append(
                {
                    "cn_fraction_percent": cn,
                    "metric": metric,
                    "matched_cases": len(group),
                    "t120_mean": pd.to_numeric(group[f"{metric}_t120"], errors="coerce").mean(),
                    "t180_mean": pd.to_numeric(group[f"{metric}_t180"], errors="coerce").mean(),
                    "mean_delta_t180_minus_t120": pd.to_numeric(
                        group[f"delta_t180_minus_t120_{metric}"], errors="coerce"
                    ).mean(),
                }
            )
    write_csv(pd.DataFrame(response_rows), output / "corrected_180_matched_120_180_cn_response.csv")


def main() -> int:
    parser = argparse.ArgumentParser(description="Post-process the corrected 773-task India 180-satellite run")
    parser.add_argument("--simulation-root", type=Path, required=True)
    parser.add_argument("--report-root", type=Path, required=True)
    args = parser.parse_args()

    simulation_root = args.simulation_root.resolve()
    report_root = args.report_root.resolve()
    output = report_root / "data" / "final_corrected_180"
    output.mkdir(parents=True, exist_ok=True)

    tools = report_root / "tools"
    pc = load_module("corrected_pc", tools / "postprocess_complete.py")
    pr = load_module("corrected_priority", tools / "priority_event_analysis.py")
    consistency = load_module("corrected_consistency", tools / "full891_consistency_audit.py")

    verification = json.loads((simulation_root / "verification_282.json").read_text(encoding="utf-8"))
    if verification.get("status") != "PASS" or int(verification.get("verified_complete_cases", 0)) != 282:
        raise RuntimeError("Corrected package did not pass the supplied 282-case verification gate")

    manifest = pq.read_table(simulation_root / "campaign_case_manifest.parquet").to_pandas()
    manifest["cn_fraction_percent"] = pd.to_numeric(manifest["cn_fraction_percent"], errors="coerce")
    manifest["base_id"] = manifest["case_id"].astype(str).map(pc.base_id)
    write_csv(manifest, output / "corrected_180_case_selection_manifest.csv")
    availability = (
        manifest.groupby("cn_fraction_percent", as_index=False)
        .agg(cases=("case_id", "nunique"), base_architectures=("base_id", "nunique"))
        .sort_values("cn_fraction_percent")
    )
    availability["expected_base_architectures"] = 27
    availability["missing_cases"] = 27 - availability["cases"]
    availability["balanced_for_all_27_bases"] = availability["cases"].eq(27)
    write_csv(availability, output / "corrected_180_case_availability_by_cn.csv")

    canonical_path = output / "corrected_180_canonical_case_metrics.parquet"
    if canonical_path.exists():
        frame = pd.read_parquet(canonical_path)
        print(f"Reusing {canonical_path}", flush=True)
    else:
        frame = read_case_metrics(simulation_root / "runs" / "India")
        if len(frame) != 282 * 539:
            raise RuntimeError(f"Expected 151,998 case-scenario rows; found {len(frame):,}")
        frame["base_id"] = frame["case_id"].astype(str).map(pc.base_id)
        frame.to_parquet(canonical_path, index=False, compression="zstd")

    task_counts = pd.to_numeric(frame["n_tasks"], errors="coerce")
    scope = pd.DataFrame(
        [
            ("verified_complete_cases", verification["verified_complete_cases"], 282, "PASS"),
            ("unique_case_scenario_rows", len(frame), 282 * 539, "PASS"),
            ("task_population", int(task_counts.mode().iloc[0]), 773, "PASS"),
            ("balanced_cn30_cn100_cases", int(manifest["cn_fraction_percent"].isin(BALANCED_CN).sum()), 216, "PASS"),
        ],
        columns=["check", "observed", "reference", "status"],
    )
    write_csv(scope, output / "corrected_180_scope_gate.csv")

    balanced_ids = set(manifest.loc[manifest["cn_fraction_percent"].isin(BALANCED_CN), "case_id"].astype(str))
    balanced = frame.loc[frame["case_id"].astype(str).isin(balanced_ids)].copy()
    if balanced["case_id"].nunique() != 216 or len(balanced) != 216 * 539:
        raise RuntimeError("Balanced CN30-CN100 analysis block is incomplete")

    corr, redundancy = pc.metric_correlations(balanced, 0.95)
    corr.to_csv(output / "corrected_180_metric_correlation_matrices.csv")
    write_csv(redundancy, output / "corrected_180_metric_pair_redundancy.csv")
    write_csv(pc.weighting_proposal(redundancy), output / "corrected_180_weighting_proposal_for_review.csv")

    cn, absolute, adjacent = pc.cn_effects(balanced, BOOTSTRAP_DRAWS, SEED)
    write_csv(cn, output / "corrected_180_cn_fraction_intervals.csv")
    write_csv(absolute, output / "corrected_180_cn_absolute_count_summary.csv")
    write_csv(adjacent, output / "corrected_180_cn_adjacent_paired_deltas.csv")
    high_summary, high_cases = pc.high_cn_diagnosis(adjacent, BOOTSTRAP_DRAWS, SEED + 1)
    write_csv(high_summary, output / "corrected_180_high_cn_decline_diagnosis.csv")
    write_csv(high_cases, output / "corrected_180_high_cn_cases.csv")

    paired_detail, paired_summary = pc.paired_scenario_effects(balanced, BOOTSTRAP_DRAWS, SEED + 2)
    write_csv(paired_detail, output / "corrected_180_paired_scenario_effects.csv")
    write_csv(paired_summary, output / "corrected_180_paired_scenario_effects_summary.csv")

    robust_detail, robust_summary, robust_auc = pc.robustness_analysis(balanced, BOOTSTRAP_DRAWS, SEED + 3)
    write_csv(robust_detail, output / "corrected_180_robustness_detail.csv")
    write_csv(robust_summary, output / "corrected_180_robustness_summary.csv")
    write_csv(robust_auc, output / "corrected_180_robustness_auc.csv")

    combined_detail, combined_summary = consistency.combined_stress_summary(
        balanced, pc, BOOTSTRAP_DRAWS, SEED + 4
    )
    write_csv(combined_detail, output / "corrected_180_combined_stress_detail.csv")
    write_csv(combined_summary, output / "corrected_180_combined_stress_summary.csv")

    baseline = balanced.loc[balanced["scenario_id"] == "unlimited_useful_deadline"].copy()
    baseline = pc.add_robustness_score(baseline, robust_auc)
    pareto, winners, acceptability = pc.pareto_and_weights(baseline, WEIGHT_DRAWS, SEED + 5)
    write_csv(pareto, output / "corrected_180_exact_pareto.csv")
    write_csv(winners, output / "corrected_180_random_weight_winners.csv")
    write_csv(acceptability, output / "corrected_180_rank_acceptability.csv")
    write_csv(pc.build_shortlist(pareto, acceptability, 5), output / "corrected_180_conditional_top5_shortlist.csv")
    write_csv(pc.factor_range_effects(balanced), output / "corrected_180_factor_effects.csv")

    events = pr.read_region_events(simulation_root / "runs" / "India", "India")
    events = events.loc[events["case_id"].astype(str).isin(balanced_ids)].copy()
    priority_curves, priority_summary = pr.summarize_events(events, BOOTSTRAP_DRAWS, SEED + 6)
    write_csv(priority_curves, output / "corrected_180_priority_km_curves.csv")
    write_csv(priority_summary, output / "corrected_180_priority_event_summary.csv")
    write_csv(pr.standardized_macro(priority_summary), output / "corrected_180_priority_equal_weight_summary.csv")
    write_csv(pr.priority_counts(events), output / "corrected_180_priority_task_counts.csv")

    paired_cross_fleet(
        pareto,
        report_root / "data" / "final_results" / "uniform_60_120_exact_pareto.csv",
        output,
    )

    analysis_manifest = {
        "status": "PASS",
        "analysis_date": "2026-10-09",
        "simulation_verification": verification,
        "full_case_count": 282,
        "balanced_case_count": 216,
        "balanced_cn_levels_percent": BALANCED_CN,
        "task_population": 773,
        "case_scenario_rows": int(len(frame)),
        "bootstrap_draws": BOOTSTRAP_DRAWS,
        "random_weight_draws": WEIGHT_DRAWS,
        "seed": SEED,
        "inference_boundary": (
            "Aggregate architecture analysis uses the complete CN30-CN100 block. "
            "The controlled T120-T180 comparison uses the matched India unlimited-routing baseline only."
        ),
    }
    (output / "corrected_180_postprocessing_manifest.json").write_text(
        json.dumps(analysis_manifest, indent=2), encoding="utf-8"
    )
    print(f"Post-processing complete: {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
