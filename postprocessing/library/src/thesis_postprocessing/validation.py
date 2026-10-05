"""Discovery, inventory, and scientific gate validation."""

from __future__ import annotations

import itertools
import json
import os
import platform
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import pandas as pd

from .archive import CampaignArchive, event_label, parse_case_id


CORE_ROLES = {
    "case_status.json",
    "failure_classification.csv",
    "performance_summary.csv",
    "task_loading_report.json",
    "task_observation_results.csv",
    "task_reception_times.csv",
    "transfer_events.csv.gz",
    "wildfire_tasks_used.csv",
}

CASE_MANIFEST_ROLES = {
    "architecture_manifest.csv",
    "architecture_validation_report.csv",
    "central_node_distribution.csv",
    "constellation_nodes.csv",
    "ground_stations.csv",
    "run_config.json",
}


@dataclass
class ValidationResult:
    cases: pd.DataFrame
    checks: pd.DataFrame
    event_roots: dict[str, str]
    gate_a_pass: bool
    gate_b_pass: bool
    task_hashes: pd.DataFrame


def expected_case_ids() -> set[str]:
    values = itertools.product(
        [60, 120, 180],
        [3, 5, 10],
        [500, 800, 1000],
        [60.0, 80.0, 98.6],
        range(0, 101, 10),
    )
    return {
        f"T{n_sat:03d}_P{n_planes:02d}_H{altitude:04d}_I{int(round(inclination * 10)):04d}_CN{cn:03d}_F1"
        for n_sat, n_planes, altitude, inclination, cn in values
    }


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def write_inventory(archive: CampaignArchive, output_root: Path) -> pd.DataFrame:
    out = output_root / "00_inventory"
    out.mkdir(parents=True, exist_ok=True)
    event_roots = sorted(archive.event_roots, key=len, reverse=True)
    rows = []
    folder_counts: Counter[str] = Counter()
    for info in archive.infos:
        name = info.filename.replace("\\", "/")
        matched_event = next((root for root in event_roots if name.startswith(root + "/")), "")
        relative = name[len(matched_event) + 1 :] if matched_event else name
        parts = PurePosixPath(relative).parts
        case_id = parts[0] if parts and parts[0].startswith("T") else ""
        role = PurePosixPath(name).name
        selected = role in CORE_ROLES or role in {
            "combined_performance_summary.csv",
            "combined_architecture_manifest.csv",
            "combined_central_node_distribution.csv",
            "architecture_sweep_manifest.csv",
            "architecture_sweep_status.csv",
            "event_run_manifest.csv",
            "final_thesis_pipeline_manifest.json",
        }
        rows.append(
            {
                "member_path": name,
                "event": event_label(matched_event) if matched_event else "",
                "case_id": case_id,
                "role": role,
                "extension": "".join(PurePosixPath(role).suffixes).lower(),
                "archive_bytes": info.file_size,
                "zip_compressed_bytes": info.compress_size,
                "crc32": f"{info.CRC:08x}",
                "selected_by_core_profile": selected,
            }
        )
        parent_parts = PurePosixPath(name).parent.parts
        for depth in range(1, min(len(parent_parts), 7) + 1):
            folder_counts["/".join(parent_parts[:depth])] += 1
    inventory = pd.DataFrame(rows)
    _write_csv(inventory, out / "file_inventory.csv")

    tree_lines = ["# Folder tree (file counts; capped at depth 7)"]
    for folder, count in sorted(folder_counts.items()):
        depth = folder.count("/")
        tree_lines.append(f"{'  ' * depth}{PurePosixPath(folder).name}/ [{count} files below]")
    (out / "folder_tree.txt").write_text("\n".join(tree_lines), encoding="utf-8")

    roles = (
        inventory[inventory["event"] != ""]
        .groupby(["event", "role"], dropna=False)
        .agg(files=("member_path", "size"), archive_bytes=("archive_bytes", "sum"), selected=("selected_by_core_profile", "max"))
        .reset_index()
    )
    _write_csv(roles, out / "discovered_file_roles.csv")

    archive_size = archive.path.stat().st_size
    report = [
        "# Access report",
        "",
        f"- Generated UTC: `{datetime.now(timezone.utc).isoformat()}`",
        f"- Source ZIP: `{archive.path}`",
        "- Source mode: `read-only`",
        f"- ZIP readable: `true`",
        f"- ZIP files: `{len(archive.infos):,}`",
        f"- ZIP size on disk: `{archive_size:,}` bytes",
        f"- ZIP member bytes: `{sum(info.file_size for info in archive.infos):,}`",
        f"- Event roots: `{len(archive.event_roots)}`",
        f"- Output root writable: `{os.access(output_root, os.W_OK)}`",
        "- Giant communication-window and observation-opportunity members remain external in the authoritative ZIP under the core profile.",
    ]
    (out / "access_report.md").write_text("\n".join(report), encoding="utf-8")
    return inventory


def validate_campaign(archive: CampaignArchive, output_root: Path) -> ValidationResult:
    out = output_root / "01_validation"
    out.mkdir(parents=True, exist_ok=True)
    expected = expected_case_ids()
    checks: list[dict[str, object]] = []
    missing_rows: list[dict[str, str]] = []
    duplicate_rows: list[dict[str, str]] = []
    failed_rows: list[dict[str, str]] = []
    inconsistency_rows: list[dict[str, object]] = []
    task_hash_rows: list[dict[str, object]] = []
    case_frames: list[pd.DataFrame] = []
    roots: dict[str, str] = {}

    def check(region: str, check_id: str, passed: bool, severity: str, observed: object, expected_value: object, details: str = "") -> None:
        checks.append(
            {
                "region": region,
                "check_id": check_id,
                "passed": bool(passed),
                "severity": severity,
                "observed": observed,
                "expected": expected_value,
                "details": details,
            }
        )

    for event_root in archive.event_roots:
        region = event_label(event_root)
        roots[region] = event_root
        actual_cases = set(archive.event_cases(event_root))
        missing = sorted(expected - actual_cases)
        extra = sorted(actual_cases - expected)
        for case_id in missing:
            missing_rows.append({"region": region, "case_id": case_id, "reason": "missing_case_directory"})
        for case_id in extra:
            missing_rows.append({"region": region, "case_id": case_id, "reason": "unexpected_case_directory"})
        check(region, "grid_exact_891", not missing and not extra and len(actual_cases) == 891, "ERROR", len(actual_cases), 891, f"missing={len(missing)} extra={len(extra)}")

        tables = {
            name: archive.read_csv(archive.event_member(event_root, name))
            for name in [
                "combined_performance_summary.csv",
                "combined_architecture_manifest.csv",
                "architecture_sweep_manifest.csv",
                "architecture_sweep_status.csv",
            ]
        }
        status = tables["architecture_sweep_status.csv"].copy()
        status["status"] = status["status"].astype(str)
        skipped = set(status.loc[status["status"] == "SKIPPED_COMPLETED", "case_id"].astype(str))
        for name, frame in tables.items():
            duplicate_ids = frame.loc[frame["case_id"].duplicated(keep=False), "case_id"].astype(str).unique()
            for case_id in duplicate_ids:
                duplicate_rows.append({"region": region, "source": name, "case_id": case_id})
            ids = set(frame["case_id"].astype(str))
            if name == "combined_architecture_manifest.csv":
                mapping_pass = ids == (expected - skipped) and not len(duplicate_ids)
                mapping_details = f"duplicates={len(duplicate_ids)}; omitted_reused_cases={len(skipped)}"
                mapping_expected: object = 891 - len(skipped)
            else:
                mapping_pass = ids == expected and not len(duplicate_ids)
                mapping_details = f"duplicates={len(duplicate_ids)}"
                mapping_expected = 891
            check(region, f"{name}_case_mapping", mapping_pass, "ERROR", len(ids), mapping_expected, mapping_details)

        invalid_status = status[~status["status"].isin(["COMPLETED", "SKIPPED_COMPLETED"])]
        for row in invalid_status.to_dict("records"):
            failed_rows.append({"region": region, "case_id": str(row["case_id"]), "status": str(row["status"]), "message": str(row.get("message", ""))})
        check(region, "campaign_status_complete_or_reused", invalid_status.empty, "ERROR", len(invalid_status), 0)

        role_missing: dict[str, set[str]] = defaultdict(set)
        for case_id in actual_cases:
            for role in CORE_ROLES | CASE_MANIFEST_ROLES:
                if archive.case_member(event_root, case_id, role) not in archive.info_by_name:
                    role_missing[role].add(case_id)
        for role in CORE_ROLES:
            missing_role = role_missing.get(role, set())
            check(region, f"core_role_{role}", not missing_role, "ERROR", len(missing_role), 0)
            for case_id in sorted(missing_role):
                missing_rows.append({"region": region, "case_id": case_id, "reason": f"missing:{role}"})

        missing_manifests = set().union(*(role_missing.get(role, set()) for role in CASE_MANIFEST_ROLES))
        check(
            region,
            "reused_cases_match_missing_per_case_manifests",
            skipped == missing_manifests,
            "ERROR",
            len(skipped),
            len(missing_manifests),
            "Combined manifests are authoritative for reused cases only when the sets match.",
        )

        case_status_counts: Counter[str] = Counter()
        status_case_id_mismatch = 0
        for case_id in sorted(actual_cases):
            payload = archive.read_json(archive.case_member(event_root, case_id, "case_status.json"))
            case_status_counts[str(payload.get("status", "MISSING"))] += 1
            status_case_id_mismatch += int(str(payload.get("case_id")) != case_id)
        check(region, "case_status_ids_match", status_case_id_mismatch == 0, "ERROR", status_case_id_mismatch, 0)
        check(region, "case_status_completed", set(case_status_counts).issubset({"COMPLETED"}), "ERROR", dict(case_status_counts), {"COMPLETED": 891})

        task_hash_counts: Counter[str] = Counter()
        task_row_counts: Counter[int] = Counter()
        for case_id, member in archive.iter_case_members(event_root, "wildfire_tasks_used.csv"):
            digest = archive.sha256(member)
            rows = archive.read_csv(member, usecols=["task_id"]).shape[0]
            task_hash_counts[digest] += 1
            task_row_counts[rows] += 1
            task_hash_rows.append({"region": region, "case_id": case_id, "sha256": digest, "task_rows": rows})
        check(region, "identical_task_source_per_case", len(task_hash_counts) == 1, "ERROR", len(task_hash_counts), 1)
        check(region, "identical_task_count_per_case", len(task_row_counts) == 1, "ERROR", dict(task_row_counts), "one row count")

        sweep = tables["architecture_sweep_manifest.csv"].copy()
        invariant_columns = [
            "duration_hours",
            "time_step_sec",
            "sat_sat_data_rate_bps",
            "sat_gs_data_rate_bps",
            "ground_station_set",
            "routing_policy",
            "max_forwarding_hops",
            "max_observer_copies_per_task",
            "max_cn_copies_per_task",
            "task_size_mode",
            "default_task_size_mbits",
            "max_tasks",
            "require_min_tasks",
        ]
        for column in invariant_columns:
            unique = sweep[column].dropna().astype(str).unique().tolist()
            passed = len(unique) == 1
            check(region, f"config_invariant_{column}", passed, "ERROR", len(unique), 1, json.dumps(unique[:10]))
            if not passed:
                inconsistency_rows.append({"region": region, "field": column, "unique_values": json.dumps(unique)})

        perf = tables["combined_performance_summary.csv"].copy()
        arch = tables["combined_architecture_manifest.csv"].copy()
        parsed = pd.DataFrame([parse_case_id(case_id) for case_id in sorted(actual_cases)]).rename(
            columns={
                "n_satellites": "n_satellites_id",
                "n_planes": "n_planes_id",
                "altitude_km": "altitude_km_id",
                "inclination_deg": "inclination_deg_id",
                "cn_fraction_percent": "cn_fraction_percent_id",
            }
        )
        arch_fields = ["n_satellites", "n_planes", "altitude_km", "inclination_deg", "cn_fraction_percent"]
        arch_subset = arch[["case_id", "architecture_id", *arch_fields]].rename(
            columns={"architecture_id": "architecture_id_manifest", **{field: f"{field}_manifest" for field in arch_fields}}
        )
        perf_subset = perf[["case_id", "architecture_id", "n_central_nodes"]]
        sweep_subset = sweep[["case_id", *arch_fields]].rename(
            columns={field: f"{field}_sweep" for field in arch_fields}
        )
        comparison = (
            parsed.merge(perf_subset, on="case_id", how="left", validate="one_to_one")
            .merge(sweep_subset, on="case_id", how="left", validate="one_to_one")
            .merge(arch_subset, on="case_id", how="left", validate="one_to_one")
        )
        mismatch_count = 0
        for field in arch_fields:
            left = pd.to_numeric(comparison[f"{field}_id"], errors="coerce")
            manifest = pd.to_numeric(comparison[f"{field}_manifest"], errors="coerce")
            sweep_values = pd.to_numeric(comparison[f"{field}_sweep"], errors="coerce")
            manifest_mismatches = comparison.loc[manifest.notna() & ((left - manifest).abs() > 1e-9), "case_id"]
            sweep_mismatches = comparison.loc[(left - sweep_values).abs() > 1e-9, "case_id"]
            mismatches = pd.concat([manifest_mismatches, sweep_mismatches]).drop_duplicates()
            mismatch_count += len(mismatches)
            for case_id in mismatches.tolist():
                inconsistency_rows.append({"region": region, "field": field, "case_id": case_id, "unique_values": "case_id_vs_manifest"})
        check(region, "case_id_matches_architecture_manifest", mismatch_count == 0, "ERROR", mismatch_count, 0)
        architecture_id_mismatches = comparison.loc[
            comparison["architecture_id_manifest"].notna()
            & (comparison["architecture_id_manifest"].astype(str) != comparison["architecture_id"].astype(str)),
            "case_id",
        ]
        check(region, "architecture_id_summary_matches_manifest", architecture_id_mismatches.empty, "ERROR", len(architecture_id_mismatches), 0)

        cases = comparison.copy()
        cases["region"] = region
        cases["event_root"] = event_root
        cases = cases.merge(status[["case_id", "status", "runtime_sec", "message"]], on="case_id", how="left")
        cases = cases.merge(perf, on=["case_id", "architecture_id"], how="left", suffixes=("", "_summary"))
        cases["reused_case"] = cases["status"].eq("SKIPPED_COMPLETED")
        cases["base_architecture_id"] = cases["case_id"].str.replace(r"_CN\d{3}_F1$", "", regex=True)
        case_frames.append(cases)

    checks_frame = pd.DataFrame(checks)
    cases_frame = pd.concat(case_frames, ignore_index=True)
    task_hashes = pd.DataFrame(task_hash_rows)
    _write_csv(checks_frame, out / "campaign_validation.csv")
    _write_csv(pd.DataFrame(missing_rows, columns=["region", "case_id", "reason"]), out / "missing_cases.csv")
    _write_csv(pd.DataFrame(duplicate_rows, columns=["region", "source", "case_id"]), out / "duplicate_cases.csv")
    _write_csv(pd.DataFrame(failed_rows, columns=["region", "case_id", "status", "message"]), out / "failed_or_incomplete_cases.csv")
    _write_csv(pd.DataFrame(inconsistency_rows), out / "configuration_inconsistencies.csv")
    _write_csv(task_hashes, out / "task_source_hashes.csv")

    gate_a = archive.path.is_file() and os.access(output_root, os.W_OK)
    gate_b = gate_a and bool(checks_frame.loc[checks_frame["severity"] == "ERROR", "passed"].all())
    exclusions = cases_frame.loc[~cases_frame["case_id"].isin(expected), ["region", "case_id"]].copy()
    exclusions["reason"] = "outside_expected_grid"
    exclusions["decision"] = "exclude"
    _write_csv(exclusions, out / "exclusions.csv")
    authoritative = cases_frame[["region", "case_id", "status", "reused_case"]].copy()
    authoritative["authoritative"] = gate_b
    _write_csv(authoritative, out / "authoritative_case_set.csv")

    failures = checks_frame[~checks_frame["passed"]]
    report_lines = [
        "# Campaign validation report",
        "",
        f"- Gate A (access and separation): `{'PASS' if gate_a else 'FAIL'}`",
        f"- Gate B (authoritative case set): `{'PASS' if gate_b else 'FAIL'}`",
        f"- Regions: `{', '.join(sorted(roots))}`",
        f"- Cases discovered: `{len(cases_frame):,}`",
        f"- Error checks failed: `{len(failures[failures['severity'] == 'ERROR'])}`",
        f"- Warning checks failed: `{len(failures[failures['severity'] == 'WARNING'])}`",
        "",
        "No ranking, regression, cost tradeoff, or Pareto result is authorized unless Gate B is PASS.",
        "The 41 reused California cases are accepted only through the combined manifests and exact status/missing-manifest set match.",
    ]
    (out / "validation_report.md").write_text("\n".join(report_lines), encoding="utf-8")
    return ValidationResult(cases_frame, checks_frame, roots, gate_a, gate_b, task_hashes)


def software_versions() -> dict[str, object]:
    import matplotlib
    import numpy
    import pyarrow

    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "pandas": pd.__version__,
        "numpy": numpy.__version__,
        "matplotlib": matplotlib.__version__,
        "pyarrow": pyarrow.__version__,
    }
