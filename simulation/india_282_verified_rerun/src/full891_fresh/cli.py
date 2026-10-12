from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .config import CampaignConfig
from .runner import build_manifest, build_run_contract, run_campaign, validate_campaign_contract
from .scenarios import build_scenarios


def _validate(config: CampaignConfig) -> dict:
    contract = validate_campaign_contract(config)
    cases, scenarios = build_manifest(config, "full")
    run_contract = build_run_contract(config, contract, cases, scenarios, "full")
    return {
        "configuration_fingerprint": config.fingerprint,
        "base_architectures": len(list(config.iter_base_architectures())),
        "frozen_grid_cases_per_region": config.case_count_per_region,
        "selected_cases": len(cases),
        "regions": config.data["regions"],
        "task_counts": contract["task_counts"],
        "task_input_sha256": contract["task_input_sha256"],
        "case_allowlist": contract["case_allowlist"],
        "case_allowlist_sha256": contract["case_allowlist_sha256"],
        "normalized_regions": contract["normalized_regions"],
        "run_contract_sha256": run_contract["run_contract_sha256"],
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
    cases, _ = build_manifest(config, "full")
    cases_by_region = cases.groupby("region").size().to_dict()
    selected_task_case_pairs = sum(
        int(tasks[region]) * int(case_count)
        for region, case_count in cases_by_region.items()
    )
    main_rows = selected_task_case_pairs * len(main)
    robust_rows = selected_task_case_pairs * len(retained_robust)
    return {
        "selected_cases": len(cases),
        "cases_by_region": cases_by_region,
        "scenario_count_per_case": len(scenarios),
        "main_scenarios_with_task_rows": len(main),
        "sampled_robustness_scenarios_with_task_rows": len(retained_robust),
        "estimated_retained_task_rows": main_rows + robust_rows,
        "case_metric_rows": len(scenarios) * len(cases),
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
        contract = validate_campaign_contract(config)
        cases, scenarios = build_manifest(config, "full")
        run_contract = build_run_contract(config, contract, cases, scenarios, "full")
        successes = list((output_root / "runs").rglob("_SUCCESS.json")) if (output_root / "runs").exists() else []
        compatible_successes = []
        incompatible_successes = []
        for success_path in successes:
            manifest_path = success_path.parent / "case_manifest.json"
            try:
                success = json.loads(success_path.read_text(encoding="utf-8"))
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                valid = (
                    success.get("run_contract_sha256") == run_contract["run_contract_sha256"]
                    and manifest.get("run_contract_sha256") == run_contract["run_contract_sha256"]
                    and int(manifest.get("n_tasks", -1)) == 773
                    and manifest.get("normalized_task_ids_sha256")
                    == contract["normalized_regions"]["India"]["task_ids_sha256"]
                )
            except Exception:
                valid = False
            (compatible_successes if valid else incompatible_successes).append(success_path)
        all_failures = list((output_root / "runs").rglob("_FAILED.json")) if (output_root / "runs").exists() else []
        failures = [path for path in all_failures if not (path.parent / "_SUCCESS.json").exists()]
        case_parts = list((output_root / "runs").rglob("case_metrics_part_*.parquet")) if (output_root / "runs").exists() else []
        result = {
            "output_root": str(output_root), "completed_cases": len(compatible_successes),
            "incompatible_success_markers": len(incompatible_successes),
            "failed_cases": len(failures), "checkpoint_partitions": len(case_parts),
            "expected_cases": len(cases),
            "scenarios_per_case": len(scenarios),
            "remaining_cases": max(0, len(cases) - len(compatible_successes)),
            "run_contract_sha256": run_contract["run_contract_sha256"],
            "campaign_report_exists": (output_root / "campaign_run_report.json").exists(),
        }
    else:
        if config.data["execution"].get("case_allowlist_file"):
            raise RuntimeError(
                "Standalone analysis is disabled for a partial repair package. "
                "Verify the repair output, overlay it into a copy of the complete campaign, "
                "then run the complete post-processor."
            )
        from .analysis import run_analysis

        result = run_analysis(args.config, output_override=args.output_root)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
