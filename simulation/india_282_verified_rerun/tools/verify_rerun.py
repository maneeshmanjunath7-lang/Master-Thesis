from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from full891_fresh.config import CampaignConfig
from full891_fresh.runner import (
    _event_tasks,
    _normalized_task_contract,
    build_manifest,
    build_run_contract,
    validate_campaign_contract,
)
from full891_fresh.storage import verified_scenario_ids, write_json_atomic


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _task_partition_failures(
    case_dir: Path,
    case_id: str,
    expected_task_ids: set[str],
    expected_scenario_ids: set[str],
) -> list[str]:
    failures: list[str] = []
    seen_scenarios: set[str] = set()
    for path in sorted(case_dir.glob("task_metrics_part_*.parquet")):
        try:
            frame = pd.read_parquet(path, columns=["case_id", "scenario_id", "task_id"])
        except Exception as exc:
            failures.append(f"unreadable task partition {path.name}: {exc}")
            continue
        if frame.empty:
            failures.append(f"empty task partition {path.name}")
            continue
        if set(frame["case_id"].astype(str)) != {case_id}:
            failures.append(f"wrong case ID in task partition {path.name}")
            continue
        for scenario_id_raw, group in frame.groupby("scenario_id", sort=False):
            scenario_id = str(scenario_id_raw)
            if scenario_id not in expected_scenario_ids:
                failures.append(f"{path.name}: unexpected task-metric scenario {scenario_id}")
                continue
            if scenario_id in seen_scenarios:
                failures.append(
                    f"{path.name}: task-metric scenario {scenario_id} appears in more than one partition"
                )
                continue
            seen_scenarios.add(scenario_id)
            task_ids = group["task_id"].astype(str)
            expected_count = len(expected_task_ids)
            if len(task_ids) != expected_count or task_ids.nunique() != expected_count:
                failures.append(
                    f"{path.name}/{scenario_id}: expected {expected_count} unique task rows, "
                    f"found {len(task_ids)} rows and {task_ids.nunique()} unique IDs"
                )
                continue
            actual = set(task_ids)
            if actual != expected_task_ids:
                missing = sorted(expected_task_ids - actual)
                extra = sorted(actual - expected_task_ids)
                failures.append(
                    f"{path.name}/{scenario_id}: task identity mismatch; "
                    f"missing={missing[:5]}, extra={extra[:5]}"
                )
    missing_scenarios = sorted(expected_scenario_ids - seen_scenarios)
    if missing_scenarios:
        failures.append(
            f"missing task-metric scenarios: {missing_scenarios[:10]}"
            + (f" (+{len(missing_scenarios) - 10} more)" if len(missing_scenarios) > 10 else "")
        )
    return failures


def _expected_task_metric_scenarios(
    scenarios: pd.DataFrame,
    cn_fraction_percent: float,
    retained_seed_count: int,
) -> set[str]:
    retained: set[str] = set()
    for row in scenarios.to_dict(orient="records"):
        failure_type = str(row.get("failure_type", ""))
        if cn_fraction_percent == 0 and failure_type in {
            "random_cn_failure", "targeted_cn_failure",
        }:
            continue
        family = str(row.get("family", ""))
        replicate = int(row.get("replicate", 0))
        keep = bool(row.get("retain_task_metrics", False))
        if family in {"targeted_cn_failure", "capacity_degradation"}:
            keep = keep or retained_seed_count > 0
        else:
            keep = keep or replicate < retained_seed_count
        if keep:
            retained.add(str(row["scenario_id"]))
    return retained


def verify(
    config_path: Path,
    output_root_override: Path | None = None,
    profile: str = "full",
) -> dict:
    config = CampaignConfig.load(config_path)
    contract = validate_campaign_contract(config)
    cases, scenarios = build_manifest(config, profile)
    run_contract = build_run_contract(config, contract, cases, scenarios, profile)
    expected_ids = set(cases["case_id"].astype(str))
    expected_scenarios = set(scenarios["scenario_id"].astype(str))
    output_root = output_root_override.resolve() if output_root_override else config.output_root.resolve()
    runs_root = output_root / "runs" / "India"

    failures: list[dict[str, str]] = []
    provenance_path = output_root / "provenance.json"
    if not provenance_path.is_file():
        failures.append({"case_id": "<campaign>", "reason": "missing provenance.json"})
    else:
        provenance = load_json(provenance_path)
        if provenance.get("run_contract_sha256") != run_contract["run_contract_sha256"]:
            failures.append({
                "case_id": "<campaign>",
                "reason": "campaign run-contract mismatch",
            })

    source_tasks, _, _ = _event_tasks(config, "India")
    expected_task_ids = set(source_tasks["task_id"].astype(str))
    expected_task_contract = contract["normalized_regions"]["India"]
    input_path = output_root / "inputs" / "tasks_india.parquet"
    if not input_path.is_file():
        failures.append({"case_id": "<campaign>", "reason": "missing normalized India input parquet"})
    else:
        try:
            input_tasks = pd.read_parquet(input_path)
            input_task_ids = set(input_tasks["task_id"].astype(str))
            input_task_contract = _normalized_task_contract(input_tasks)
            if len(input_tasks) != 773 or len(input_task_ids) != 773:
                failures.append({
                    "case_id": "<campaign>",
                    "reason": (
                        f"normalized India input has {len(input_tasks)} rows and "
                        f"{len(input_task_ids)} unique task IDs; expected 773/773"
                    ),
                })
            if input_task_ids != expected_task_ids or input_task_contract != expected_task_contract:
                failures.append({
                    "case_id": "<campaign>",
                    "reason": "normalized India input identity/fingerprint mismatch",
                })
        except Exception as exc:
            failures.append({"case_id": "<campaign>", "reason": f"invalid input parquet: {exc}"})

    discovered_dirs = {
        path.name: path
        for path in runs_root.glob("*/*")
        if path.is_dir()
    } if runs_root.exists() else {}
    discovered_ids = set(discovered_dirs)

    complete_ids: list[str] = []
    retained_seed_count = int(
        config.data["storage"].get("retain_robustness_task_seed_count", 0)
    )
    for case_id in sorted(expected_ids):
        case_dir = discovered_dirs.get(case_id)
        if case_dir is None:
            failures.append({"case_id": case_id, "reason": "missing case directory"})
            continue
        success_path = case_dir / "_SUCCESS.json"
        manifest_path = case_dir / "case_manifest.json"
        if not success_path.is_file():
            failures.append({"case_id": case_id, "reason": "missing _SUCCESS.json"})
            continue
        if not manifest_path.is_file():
            failures.append({"case_id": case_id, "reason": "missing case_manifest.json"})
            continue

        success = load_json(success_path)
        manifest = load_json(manifest_path)
        checks = {
            "success status": success.get("status") == "COMPLETED",
            "success scenario count": int(success.get("scenario_count", -1)) == len(expected_scenarios),
            "success run contract": success.get("run_contract_sha256") == run_contract["run_contract_sha256"],
            "case task count": int(manifest.get("n_tasks", -1)) == 773,
            "case input hash": manifest.get("task_input_sha256") == contract["task_input_sha256"],
            "case allow-list hash": manifest.get("case_allowlist_sha256") == contract["case_allowlist_sha256"],
            "case task-ID hash": manifest.get("normalized_task_ids_sha256") == expected_task_contract["task_ids_sha256"],
            "case normalized-task hash": manifest.get("normalized_tasks_sha256") == expected_task_contract["normalized_tasks_sha256"],
            "case run contract": manifest.get("run_contract_sha256") == run_contract["run_contract_sha256"],
            "case scenario-plan hash": manifest.get("scenario_ids_sha256") == run_contract["scenario_ids_sha256"],
            "case expected scenario count": int(manifest.get("expected_scenario_count", -1)) == len(expected_scenarios),
        }
        failed_checks = [name for name, passed in checks.items() if not passed]
        if failed_checks:
            failures.append({"case_id": case_id, "reason": ", ".join(failed_checks)})
            continue
        try:
            actual_scenarios = verified_scenario_ids(case_dir, case_id)
        except Exception as exc:
            failures.append({"case_id": case_id, "reason": str(exc)})
            continue
        if actual_scenarios != expected_scenarios:
            failures.append({
                "case_id": case_id,
                "reason": (
                    f"scenario partition mismatch: expected {len(expected_scenarios)}, "
                    f"found {len(actual_scenarios)}"
                ),
            })
            continue
        expected_task_scenarios = _expected_task_metric_scenarios(
            scenarios,
            float(cases.loc[cases["case_id"].astype(str).eq(case_id), "cn_fraction_percent"].iloc[0]),
            retained_seed_count,
        )
        task_failures = _task_partition_failures(
            case_dir,
            case_id,
            expected_task_ids,
            expected_task_scenarios,
        )
        if task_failures:
            failures.append({"case_id": case_id, "reason": "; ".join(task_failures[:5])})
            continue
        complete_ids.append(case_id)

    unexpected = sorted(discovered_ids - expected_ids)
    report = {
        "status": "PASS" if len(complete_ids) == len(expected_ids) and not failures and not unexpected else "FAIL",
        "profile": profile,
        "output_root": str(output_root),
        "expected_cases": len(expected_ids),
        "verified_complete_cases": len(complete_ids),
        "missing_or_invalid_cases": len(failures),
        "unexpected_case_directories": unexpected,
        "scenario_count_per_case": len(expected_scenarios),
        "task_count_india": 773,
        "task_input_sha256": contract["task_input_sha256"],
        "normalized_task_ids_sha256": expected_task_contract["task_ids_sha256"],
        "normalized_tasks_sha256": expected_task_contract["normalized_tasks_sha256"],
        "case_allowlist_sha256": contract["case_allowlist_sha256"],
        "run_contract_sha256": run_contract["run_contract_sha256"],
        "failures": failures,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify every output of the India repair campaign")
    parser.add_argument("--config", default="config/india_282_rerun.json")
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--profile", choices=["full", "smoke"], default="full")
    parser.add_argument("--report-name", default=None)
    args = parser.parse_args()
    output_override = Path(args.output_root) if args.output_root else None
    report = verify(Path(args.config), output_override, args.profile)
    output_root = Path(report["output_root"])
    report_name = args.report_name or ("verification_282.json" if args.profile == "full" else "canary_verification.json")
    write_json_atomic(report, output_root / report_name)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
