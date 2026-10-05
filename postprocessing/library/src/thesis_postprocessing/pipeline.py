"""Pipeline orchestration and provenance."""

from __future__ import annotations

import json
import shutil
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import __version__
from .analysis import run_supported_analyses
from .archive import CampaignArchive
from .canonical import build_canonical_tables
from .validation import software_versions, validate_campaign, write_inventory


OUTPUT_DIRS = [
    "00_inventory",
    "01_validation",
    "02_canonical_tables",
    "03_nominal_cn_analysis",
    "04_architecture_effects",
    "05_regional_comparison",
    "06_cost_and_pareto",
    "07_rank_sensitivity",
    "08_robustness",
    "09_task_size_sensitivity",
    "10_thesis_figures",
    "11_thesis_tables",
    "12_interpretation_notes",
    "13_additional_runs_plan",
    "provenance",
]


def _log(log_path: Path, message: str) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    line = f"[{timestamp}] {message}"
    print(line, flush=True)
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")


def _write_run_summary(
    output_root: Path,
    zip_path: Path,
    profile: str,
    gate_a: bool,
    gate_b: bool,
    gate_c: bool | None,
    cases: int,
    analytical_rows: int,
) -> None:
    status = "COMPLETE" if gate_a and gate_b and (gate_c is True or profile == "validate") else "STOPPED_AT_GATE"
    lines = [
        "# Full891 thesis post-processing run",
        "",
        f"- Status: `{status}`",
        f"- Source ZIP (read-only): `{zip_path}`",
        f"- Profile: `{profile}`",
        f"- Gate A: `{'PASS' if gate_a else 'FAIL'}`",
        f"- Gate B: `{'PASS' if gate_b else 'FAIL'}`",
        f"- Gate C: `{'PASS' if gate_c else ('FAIL' if gate_c is False else 'NOT RUN')}`",
        f"- Cases inventoried: `{cases:,}`",
        f"- Analytical case rows: `{analytical_rows:,}`",
        "",
        "Read the gate reports before using any ranking/Pareto output. The raw ZIP was not modified.",
    ]
    (output_root / "RUN_SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")


def run_pipeline(
    zip_path: Path,
    output_root: Path,
    profile: str = "core",
    compute_archive_hash: bool = True,
) -> int:
    output_root = output_root.expanduser().resolve()
    zip_path = zip_path.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    for folder in OUTPUT_DIRS:
        (output_root / folder).mkdir(parents=True, exist_ok=True)
    log_path = output_root / "provenance" / "pipeline.log"
    stale_traceback = output_root / "provenance" / "traceback.txt"
    if stale_traceback.exists():
        stale_traceback.unlink()
    config_source = Path(__file__).resolve().parents[2] / "config" / "analysis.json"
    shutil.copy2(config_source, output_root / "provenance" / "analysis_config.json")
    (output_root / "provenance" / "software_versions.json").write_text(
        json.dumps(software_versions(), indent=2), encoding="utf-8"
    )
    (output_root / "provenance" / "invocation.json").write_text(
        json.dumps(
            {
                "generated_utc": datetime.now(timezone.utc).isoformat(),
                "package_version": __version__,
                "zip_path": str(zip_path),
                "output_root": str(output_root),
                "profile": profile,
                "compute_archive_hash": compute_archive_hash,
                "argv": sys.argv,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    gate_a = gate_b = False
    gate_c: bool | None = None
    case_count = analytical_rows = 0
    try:
        _log(log_path, f"Opening authoritative ZIP read-only: {zip_path}")
        with CampaignArchive(zip_path) as archive:
            _log(log_path, "Building file inventory and access report")
            inventory = write_inventory(archive, output_root)
            scientific_roles = {
                "case_status.json",
                "failure_classification.csv",
                "performance_summary.csv",
                "task_loading_report.json",
                "task_observation_results.csv",
                "task_reception_times.csv",
                "transfer_events.csv.gz",
                "wildfire_tasks_used.csv",
                "adjacency_window_count.csv",
                "adjacency_window_duration_sec.csv",
                "adjacency_window_capacity_mbits.csv",
                "combined_performance_summary.csv",
                "combined_architecture_manifest.csv",
                "architecture_sweep_manifest.csv",
                "architecture_sweep_status.csv",
            }
            consumed = inventory[inventory["role"].isin(scientific_roles)][
                ["member_path", "event", "case_id", "role", "archive_bytes", "crc32"]
            ]
            consumed.to_csv(output_root / "provenance" / "consumed_files.csv", index=False)
            if compute_archive_hash:
                _log(log_path, "Computing whole-ZIP SHA-256 provenance hash")
                archive_hash = archive.archive_sha256()
            else:
                archive_hash = "SKIPPED_BY_REQUEST"
            (output_root / "provenance" / "source_archive.json").write_text(
                json.dumps(
                    {
                        "path": str(zip_path),
                        "size_bytes": zip_path.stat().st_size,
                        "sha256": archive_hash,
                        "read_only_contract": True,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            _log(log_path, "Running Gate A/B grid, status, mapping, role, task, and configuration checks")
            validation = validate_campaign(archive, output_root)
            gate_a, gate_b = validation.gate_a_pass, validation.gate_b_pass
            case_count = len(validation.cases)
            if not gate_b:
                _log(log_path, "Gate B failed; stopping before metrics and rankings")
                _write_run_summary(output_root, zip_path, profile, gate_a, gate_b, gate_c, case_count, 0)
                return 2
            if profile == "validate":
                _log(log_path, "Validation profile complete at Gate B")
                _write_run_summary(output_root, zip_path, profile, gate_a, gate_b, gate_c, case_count, 0)
                return 0

            _log(log_path, "Building canonical task outcomes, transfer-derived S100, and network summaries")
            canonical = build_canonical_tables(archive, validation, output_root)
            gate_c = canonical.gate_c_pass
            if not gate_c:
                _log(log_path, "Gate C failed; stopping before rankings, cost, and Pareto analysis")
                _write_run_summary(output_root, zip_path, profile, gate_a, gate_b, gate_c, case_count, 0)
                return 3

            _log(log_path, "Running supported CN, architecture, regional, Pareto, and rank-stability analyses")
            analytical = run_supported_analyses(validation, canonical, output_root)
            analytical_rows = len(analytical)
            analytical.to_parquet(output_root / "02_canonical_tables" / "analysis_case_table.parquet", index=False)

        _log(log_path, "Pipeline completed successfully; authoritative ZIP remained unchanged")
        _write_run_summary(output_root, zip_path, profile, gate_a, gate_b, gate_c, case_count, analytical_rows)
        return 0
    except Exception as exc:
        _log(log_path, f"ERROR: {type(exc).__name__}: {exc}")
        (output_root / "provenance" / "traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
        _write_run_summary(output_root, zip_path, profile, gate_a, gate_b, gate_c, case_count, analytical_rows)
        raise
