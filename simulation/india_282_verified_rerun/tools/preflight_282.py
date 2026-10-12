from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import psutil

from full891_fresh.config import CampaignConfig
from full891_fresh.runner import (
    _event_tasks,
    _normalized_task_contract,
    _worker_plan,
    assert_output_root_compatible,
    build_manifest,
    build_run_contract,
    validate_campaign_contract,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Strict preflight for the India 282-case repair campaign")
    parser.add_argument("--config", default="config/india_282_rerun.json")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args()

    config = CampaignConfig.load(args.config)
    contract = validate_campaign_contract(config)
    cases, scenarios = build_manifest(config, "full")
    run_contract = build_run_contract(config, contract, cases, scenarios, "full")
    output_root = Path(args.output_root).resolve() if args.output_root else config.output_root.resolve()
    output_root.parent.mkdir(parents=True, exist_ok=True)
    disk = shutil.disk_usage(output_root.parent)
    memory = psutil.virtual_memory()

    errors: list[str] = []
    if len(cases) != 282:
        errors.append(f"selected case count is {len(cases)}, expected 282")
    if cases["case_id"].nunique() != 282:
        errors.append("selected case IDs are not unique")
    if set(cases["region"].astype(str)) != {"India"}:
        errors.append("selected cases are not India-only")
    if set(cases["n_satellites"].astype(int)) != {180}:
        errors.append("selected cases are not confined to the audited 180-satellite layer")
    if len(scenarios) != 539:
        errors.append(f"scenario count is {len(scenarios)}, expected 539")

    normalized_tasks, _, _ = _event_tasks(config, "India")
    normalized_contract = _normalized_task_contract(normalized_tasks)
    normalized_task_count = len(normalized_tasks)
    normalized_unique_task_count = int(normalized_tasks["task_id"].astype(str).nunique())
    if normalized_task_count != 773:
        errors.append(f"normalized India task count is {normalized_task_count}, expected 773")
    if normalized_unique_task_count != 773:
        errors.append(
            f"normalized unique India task count is {normalized_unique_task_count}, expected 773"
        )
    if normalized_contract != contract["normalized_regions"]["India"]:
        errors.append("normalized India task fingerprint changed between validation passes")

    try:
        assert_output_root_compatible(output_root, run_contract)
    except Exception as exc:
        errors.append(str(exc))

    protected = {
        (config.root / value).resolve()
        for value in config.data["execution"].get("protected_output_roots", [])
    }
    if output_root in protected or output_root.name == "full891_fresh_complete":
        errors.append(f"output root is protected: {output_root}")

    result = {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "output_root": str(output_root),
        "selected_cases": len(cases),
        "base_workers": int(cases["base_id"].nunique()) if "base_id" in cases else int(
            cases[["n_satellites", "n_planes", "altitude_km", "inclination_deg", "walker_f"]]
            .drop_duplicates()
            .shape[0]
        ),
        "scenarios_per_case": len(scenarios),
        "expected_case_scenario_evaluations": len(cases) * len(scenarios),
        "normalized_india_task_count": normalized_task_count,
        "normalized_unique_india_task_count": normalized_unique_task_count,
        "normalized_task_ids_sha256": normalized_contract["task_ids_sha256"],
        "normalized_tasks_sha256": normalized_contract["normalized_tasks_sha256"],
        "run_contract_sha256": run_contract["run_contract_sha256"],
        "selected_case_ids_sha256": run_contract["selected_case_ids_sha256"],
        "scenario_ids_sha256": run_contract["scenario_ids_sha256"],
        "task_contract": contract,
        "worker_plan": _worker_plan(config),
        "available_ram_gb": round(memory.available / 1024**3, 3),
        "free_disk_gb": round(disk.free / 1024**3, 3),
    }
    print(json.dumps(result, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
