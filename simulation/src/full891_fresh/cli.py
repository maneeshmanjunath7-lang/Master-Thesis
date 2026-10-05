from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .analysis import run_analysis
from .config import CampaignConfig
from .runner import build_manifest, run_campaign
from .scenarios import build_scenarios


def _validate(config: CampaignConfig) -> dict:
    tasks = pd.read_csv(config.tasks_path)
    counts = tasks.groupby("region").size().to_dict()
    cases, scenarios = build_manifest(config, "full")
    expected_counts = {"California": 32, "India": 773}
    if counts != expected_counts:
        raise ValueError(f"Frozen task counts require {expected_counts}; got {counts}")
    return {
        "configuration_fingerprint": config.fingerprint,
        "base_architectures": len(list(config.iter_base_architectures())),
        "cases_per_region": config.case_count_per_region,
        "regions": config.data["regions"],
        "task_counts": counts,
        "campaign_case_count": len(cases),
        "scenario_count_per_case": len(scenarios),
        "case_scenario_evaluations": len(cases) * len(scenarios),
        "output_budget_gb": config.data["storage"]["maximum_output_gb"],
        "free_space_reserve_gb": config.data["storage"]["minimum_free_space_gb"],
    }


def _estimate(config: CampaignConfig) -> dict:
    tasks = pd.read_csv(config.tasks_path).groupby("region").size().to_dict()
    scenarios = build_scenarios(config.data)
    main = [s for s in scenarios if s.retain_task_metrics]
    retained_robust = [s for s in scenarios if not s.retain_task_metrics and (
        s.family in {"targeted_cn_failure", "capacity_degradation"}
        or s.replicate < int(config.data["storage"]["retain_robustness_task_seed_count"])
    )]
    cases_per_region = config.case_count_per_region
    main_rows = sum(tasks.values()) * cases_per_region * len(main)
    robust_rows = sum(tasks.values()) * cases_per_region * len(retained_robust)
    return {
        "scenario_count_per_case": len(scenarios),
        "main_scenarios_with_task_rows": len(main),
        "sampled_robustness_scenarios_with_task_rows": len(retained_robust),
        "estimated_retained_task_rows": main_rows + robust_rows,
        "case_metric_rows": len(scenarios) * cases_per_region * len(config.data["regions"]),
        "note": "Every scenario is executed; only detailed task rows for repeated robustness seeds are sampled.",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fresh end-to-end Full891 thesis campaign")
    parser.add_argument("command", choices=["validate", "manifest", "estimate", "smoke", "full", "analyze", "status"])
    parser.add_argument("--config", default="config/full_campaign.json")
    parser.add_argument("--output-root", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = CampaignConfig.load(args.config)
    if args.command == "validate":
        result = _validate(config)
    elif args.command == "estimate":
        result = _estimate(config)
    elif args.command == "manifest":
        cases, scenarios = build_manifest(config, "full")
        result = {"cases": len(cases), "scenarios_per_case": len(scenarios), "evaluations": len(cases) * len(scenarios)}
    elif args.command in {"smoke", "full"}:
        result = run_campaign(args.config, profile=args.command, output_override=args.output_root)
    elif args.command == "status":
        output_root = Path(args.output_root).resolve() if args.output_root else config.output_root.resolve()
        successes = list((output_root / "runs").rglob("_SUCCESS.json")) if (output_root / "runs").exists() else []
        all_failures = list((output_root / "runs").rglob("_FAILED.json")) if (output_root / "runs").exists() else []
        failures = [path for path in all_failures if not (path.parent / "_SUCCESS.json").exists()]
        case_parts = list((output_root / "runs").rglob("case_metrics_part_*.parquet")) if (output_root / "runs").exists() else []
        result = {
            "output_root": str(output_root), "completed_cases": len(successes),
            "failed_cases": len(failures), "checkpoint_partitions": len(case_parts),
            "expected_cases": config.case_count_per_region * len(config.data["regions"]),
            "campaign_report_exists": (output_root / "campaign_run_report.json").exists(),
        }
    else:
        result = run_analysis(args.config, output_override=args.output_root)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
