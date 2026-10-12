from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import psutil

from . import __version__, legacy_simulator as legacy
from .config import BaseArchitecture, CampaignConfig
from .failures import apply_scenario, prepare_topology_windows
from .routing import prepare_routing_context, simulate_dissemination
from .scenarios import Scenario, build_scenarios, scenario_manifest
from .storage import (
    DiskBudget,
    sha256_file,
    verified_scenario_ids,
    write_json_atomic,
    write_parquet_atomic,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ordered_value_digest(values: list[str]) -> str:
    return _sha256_text("\n".join(values) + "\n")


def _normalized_task_contract(tasks: pd.DataFrame) -> dict[str, Any]:
    columns = [
        "task_id", "latitude", "longitude", "priority_class", "task_size_mbits",
        "region", "required_response_time_min", "timestamp_utc", "deadline_time_utc",
        "created_time_sec", "deadline_time_sec",
    ]
    missing = [column for column in columns if column not in tasks.columns]
    if missing:
        raise ValueError(f"Normalized task table is missing required columns: {missing}")
    canonical = tasks[columns].copy()
    canonical["task_id"] = canonical["task_id"].astype(str)
    canonical = canonical.sort_values("task_id", kind="stable").reset_index(drop=True)
    payload = canonical.to_csv(index=False, lineterminator="\n", float_format="%.12g")
    ordered_ids = canonical["task_id"].tolist()
    return {
        "task_count": int(len(canonical)),
        "unique_task_count": int(canonical["task_id"].nunique()),
        "task_ids_sha256": _ordered_value_digest(ordered_ids),
        "normalized_tasks_sha256": _sha256_text(payload),
        "priority_counts": {
            str(key): int(value)
            for key, value in canonical["priority_class"].value_counts().sort_index().items()
        },
    }


def _event_tasks(config: CampaignConfig, region: str) -> tuple[pd.DataFrame, datetime, float]:
    raw = pd.read_csv(config.tasks_path)
    if "region" not in raw:
        raise ValueError("Task input requires a region column")
    # Reset the index after regional filtering.  Without this, pandas aligns
    # the filtered 32..804 India index against the normalizer's 0..772 index,
    # silently dropping the final 32 India tasks as apparent NaN coordinates.
    selected = (
        raw.loc[raw["region"].astype(str).str.casefold() == region.casefold()]
        .copy()
        .reset_index(drop=True)
    )
    if selected.empty:
        raise ValueError(f"No tasks found for region {region}")
    if "task_id" not in selected.columns:
        raise ValueError("Frozen task input requires a task_id column")
    selected_ids = selected["task_id"].astype(str).tolist()
    if any(not value.strip() for value in selected_ids):
        raise ValueError(f"Frozen {region} task input contains an empty task_id")
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError(f"Frozen {region} task input contains duplicate task IDs")
    timestamp = pd.to_datetime(selected["timestamp_utc"], utc=True, errors="raise")
    epoch = timestamp.min().to_pydatetime()
    tasks = legacy.normalize_tasks(selected, epoch=epoch, task_time_mode="original")
    expected_count = config.data.get("execution", {}).get("expected_task_counts", {}).get(region)
    if expected_count is not None and len(tasks) != int(expected_count):
        raise ValueError(
            f"Normalized task count for {region} is {len(tasks)}, expected {int(expected_count)}. "
            "Refusing to run with an inconsistent task population."
        )
    normalized_ids = tasks["task_id"].astype(str).tolist()
    missing_ids = sorted(set(selected_ids) - set(normalized_ids))
    extra_ids = sorted(set(normalized_ids) - set(selected_ids))
    if len(normalized_ids) != len(set(normalized_ids)) or missing_ids or extra_ids:
        raise ValueError(
            f"Normalized {region} task identity mismatch. Missing IDs: {missing_ids[:10]}; "
            f"unexpected IDs: {extra_ids[:10]}. Refusing to start the campaign."
        )
    if len(normalized_ids) != len(selected_ids):
        raise ValueError(
            f"Normalized {region} task row count {len(normalized_ids)} does not match the "
            f"filtered input row count {len(selected_ids)}. Refusing to start the campaign."
        )
    if not np.isfinite(tasks[["latitude", "longitude"]].to_numpy(dtype=float)).all():
        raise ValueError(f"Normalized {region} tasks contain invalid coordinates")
    normalized_regions = set(tasks["region"].astype(str).str.casefold())
    if normalized_regions != {region.casefold()}:
        raise ValueError(
            f"Normalized region mismatch for {region}: found {sorted(normalized_regions)}"
        )
    simulation = config.data["simulation"]
    latest_deadline = float(tasks["deadline_time_sec"].max())
    duration_hours = max(
        float(simulation["minimum_duration_hours"]),
        (latest_deadline + float(simulation["end_buffer_hours"]) * 3600.0) / 3600.0,
    )
    legacy.validate_task_horizon(tasks, duration_hours, "original", allow_truncated_task_horizon=False)
    return tasks, epoch, duration_hours


def _case_id(base: BaseArchitecture, cn_fraction: float) -> str:
    return base.base_id.replace(f"_F{base.walker_f}", f"_CN{int(round(cn_fraction)):03d}_F{base.walker_f}")


def _resolve_contract_path(config: CampaignConfig, raw_path: str | Path) -> Path:
    path = Path(raw_path)
    return path.resolve() if path.is_absolute() else (config.root / path).resolve()


def _load_case_allowlist(config: CampaignConfig) -> tuple[list[str] | None, Path | None, str | None]:
    """Load and strictly validate the optional case-selection contract."""
    execution = config.data["execution"]
    raw_path = execution.get("case_allowlist_file")
    if not raw_path:
        return None, None, None

    path = _resolve_contract_path(config, str(raw_path))
    if not path.is_file():
        raise FileNotFoundError(f"Case allow-list not found: {path}")
    digest = sha256_file(path)
    expected_digest = str(execution.get("case_allowlist_sha256", "")).strip().lower()
    if expected_digest and digest.lower() != expected_digest:
        raise ValueError(
            f"Case allow-list SHA-256 mismatch: expected {expected_digest}, got {digest}"
        )

    frame = pd.read_csv(path)
    if "case_id" not in frame.columns:
        raise ValueError(f"Case allow-list requires a case_id column: {path}")
    case_ids = frame["case_id"].astype(str).str.strip().tolist()
    if any(not value for value in case_ids):
        raise ValueError("Case allow-list contains an empty case_id")
    if len(case_ids) != len(set(case_ids)):
        duplicates = sorted(pd.Series(case_ids)[pd.Series(case_ids).duplicated()].unique())
        raise ValueError(f"Case allow-list contains duplicate case IDs: {duplicates[:10]}")

    valid_case_ids = {
        _case_id(base, fraction)
        for base in config.iter_base_architectures()
        for fraction in config.cn_fractions
    }
    unknown = sorted(set(case_ids) - valid_case_ids)
    if unknown:
        raise ValueError(f"Case allow-list contains IDs outside the frozen Full891 grid: {unknown[:10]}")

    expected_count = int(execution.get("expected_selected_case_count", len(case_ids)))
    if len(case_ids) != expected_count:
        raise ValueError(
            f"Case allow-list count mismatch: expected {expected_count}, got {len(case_ids)}"
        )
    return case_ids, path, digest


def validate_campaign_contract(config: CampaignConfig) -> dict[str, Any]:
    """Validate the frozen task file and optional case allow-list before any output is written."""
    execution = config.data["execution"]
    task_path = config.tasks_path.resolve()
    if not task_path.is_file():
        raise FileNotFoundError(f"Task input not found: {task_path}")
    task_digest = sha256_file(task_path)
    expected_task_digest = str(execution.get("task_input_sha256", "")).strip().lower()
    if expected_task_digest and task_digest.lower() != expected_task_digest:
        raise ValueError(
            f"Task input SHA-256 mismatch: expected {expected_task_digest}, got {task_digest}"
        )

    raw_tasks = pd.read_csv(task_path)
    if "region" not in raw_tasks.columns:
        raise ValueError("Task input requires a region column")
    task_counts = {
        str(key): int(value)
        for key, value in raw_tasks.groupby(raw_tasks["region"].astype(str)).size().to_dict().items()
    }
    expected_counts = {
        str(key): int(value)
        for key, value in execution.get("expected_task_counts", {}).items()
    }
    if expected_counts and task_counts != expected_counts:
        raise ValueError(f"Frozen task counts require {expected_counts}; got {task_counts}")
    if "task_id" not in raw_tasks.columns:
        raise ValueError("Task input requires a task_id column")
    raw_ids = raw_tasks["task_id"].astype(str)
    if raw_ids.duplicated().any():
        duplicates = sorted(raw_ids[raw_ids.duplicated()].unique())
        raise ValueError(f"Frozen task input contains duplicate task IDs: {duplicates[:10]}")
    missing_regions = sorted(set(map(str, config.data["regions"])) - set(task_counts))
    if missing_regions:
        raise ValueError(f"Task input is missing configured regions: {missing_regions}")

    case_ids, allowlist_path, allowlist_digest = _load_case_allowlist(config)
    if case_ids is not None and list(map(str, config.data["regions"])) != ["India"]:
        raise ValueError("The India repair allow-list may only run with regions=[\"India\"]")
    normalized_regions: dict[str, dict[str, Any]] = {}
    for region in map(str, config.data["regions"]):
        normalized_tasks, _, _ = _event_tasks(config, region)
        normalized_regions[region] = _normalized_task_contract(normalized_tasks)
    return {
        "task_input": str(task_path),
        "task_input_sha256": task_digest,
        "task_counts": task_counts,
        "case_allowlist": str(allowlist_path) if allowlist_path else None,
        "case_allowlist_sha256": allowlist_digest,
        "selected_case_count": len(case_ids) if case_ids is not None else None,
        "normalized_regions": normalized_regions,
    }


def build_run_contract(
    config: CampaignConfig,
    input_contract: dict[str, Any],
    cases: pd.DataFrame,
    scenarios: pd.DataFrame,
    profile: str,
) -> dict[str, Any]:
    """Build the immutable scientific/checkpoint contract for one output root."""
    storage = config.data["storage"]
    storage_contract = {
        key: value
        for key, value in storage.items()
        if key not in {"maximum_output_gb", "minimum_free_space_gb", "maximum_scratch_gb"}
    }
    payload = {
        "contract_version": 2,
        "profile": profile,
        "task_input_sha256": input_contract["task_input_sha256"],
        "case_allowlist_sha256": input_contract["case_allowlist_sha256"],
        "normalized_regions": input_contract["normalized_regions"],
        "selected_case_count": int(len(cases)),
        "selected_case_ids_sha256": _ordered_value_digest(
            sorted(cases["case_id"].astype(str).tolist())
        ),
        "scenario_count": int(len(scenarios)),
        "scenario_ids_sha256": _ordered_value_digest(
            scenarios["scenario_id"].astype(str).tolist()
        ),
        "scientific_configuration": {
            key: config.data[key]
            for key in (
                "random_seed", "regions", "architecture_grid", "simulation",
                "communication", "dissemination", "task_size_sensitivity", "robustness",
            )
        },
        "storage_contract": storage_contract,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return {**payload, "run_contract_sha256": _sha256_text(encoded)}


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def assert_output_root_compatible(output_root: Path, run_contract: dict[str, Any]) -> None:
    """Reject old 741-task output and every other incompatible resume root."""
    if not output_root.exists():
        return
    entries = list(output_root.iterdir())
    if not entries:
        return
    provenance_path = output_root / "provenance.json"
    if not provenance_path.is_file():
        raise RuntimeError(
            f"INCOMPATIBLE RESUME DATA: {output_root} is not empty but has no provenance.json. "
            "Use the new output directory supplied by this package; do not copy old run data into it."
        )
    previous = _load_json(provenance_path)
    previous_digest = previous.get("run_contract_sha256")
    expected_digest = run_contract["run_contract_sha256"]
    if previous_digest != expected_digest:
        raise RuntimeError(
            "INCOMPATIBLE RESUME DATA: the existing output was not created under the exact "
            f"773-task run contract (found {previous_digest!r}, expected {expected_digest}). "
            "The old 741-task checkpoints cannot be resumed. Keep them for audit and use the "
            "fresh output directory supplied by this package."
        )


def _validated_resume_scenario_ids(
    case_dir: Path,
    case_id: str,
    expected_case_contract: dict[str, Any],
    expected_scenario_ids: set[str],
    resume: bool,
) -> set[str]:
    metric_parts = sorted(case_dir.glob("case_metrics_part_*.parquet"))
    manifest_path = case_dir / "case_manifest.json"
    if not resume and (metric_parts or manifest_path.exists()):
        raise RuntimeError(
            f"Resume is disabled but checkpoint data already exists for {case_id}: {case_dir}"
        )
    if not metric_parts:
        if manifest_path.exists():
            manifest = _load_json(manifest_path)
            for key, expected in expected_case_contract.items():
                if manifest.get(key) != expected:
                    raise RuntimeError(
                        f"INCOMPATIBLE RESUME DATA for {case_id}: {key} is "
                        f"{manifest.get(key)!r}, expected {expected!r}"
                    )
        return set()
    if not manifest_path.is_file():
        raise RuntimeError(
            f"INCOMPATIBLE RESUME DATA for {case_id}: checkpoint partitions exist without "
            "case_manifest.json"
        )
    manifest = _load_json(manifest_path)
    for key, expected in expected_case_contract.items():
        if manifest.get(key) != expected:
            raise RuntimeError(
                f"INCOMPATIBLE RESUME DATA for {case_id}: {key} is "
                f"{manifest.get(key)!r}, expected {expected!r}. Old 741-task partitions "
                "must not be reused."
            )
    completed = verified_scenario_ids(case_dir, case_id)
    unexpected = sorted(completed - expected_scenario_ids)
    if unexpected:
        raise RuntimeError(
            f"INCOMPATIBLE RESUME DATA for {case_id}: unexpected scenarios {unexpected[:10]}"
        )
    return completed


def _scenario_subset(scenarios: list[Scenario], profile: str) -> list[Scenario]:
    if profile == "full":
        return scenarios
    if profile == "smoke":
        wanted = {
            "nominal_original", "unlimited_useful_deadline", "unlimited_useful_sim_end",
            "policy_peer", "policy_hybrid", "task_size_x2p0",
            "random_satellite_failure_p10_r00", "targeted_cn_failure_p10",
            "link_window_outage_p10_r00", "capacity_degradation_p10",
            "ground_station_outage_p10_r00",
        }
        return [scenario for scenario in scenarios if scenario.scenario_id in wanted]
    raise ValueError(f"Unknown execution profile: {profile}")


def _retain_robust_task_metrics(scenario: Scenario, config: CampaignConfig) -> bool:
    if scenario.retain_task_metrics:
        return True
    keep = int(config.data["storage"].get("retain_robustness_task_seed_count", 0))
    if scenario.family in {"targeted_cn_failure", "capacity_degradation"}:
        return keep > 0
    return scenario.replicate < keep


def _retain_transfers(scenario: Scenario, base: BaseArchitecture, cn_fraction: float, config: CampaignConfig) -> bool:
    storage = config.data["storage"]
    return (
        scenario.retain_transfers
        and base.n_satellites in [int(x) for x in storage["retain_transfer_n_satellites"]]
        and int(round(cn_fraction)) in [int(x) for x in storage["retain_transfer_cn_fractions"]]
    )


def _not_applicable_metrics(
    case_id: str, region: str, scenario: Scenario, base: BaseArchitecture, cn_fraction: float
) -> dict[str, Any]:
    return {
        "case_id": case_id, "region": region, "scenario_id": scenario.scenario_id,
        "family": scenario.family, "status": "NOT_APPLICABLE",
        "applicability": "not_applicable_no_central_nodes",
        "n_satellites": base.n_satellites, "n_planes": base.n_planes,
        "altitude_km": base.altitude_km, "inclination_deg": base.inclination_deg,
        "cn_fraction_percent": cn_fraction, "n_central_nodes": 0,
        "failure_type": scenario.failure_type, "failure_level_percent": scenario.failure_level_percent,
        "replicate": scenario.replicate,
    }


def _run_base_worker(payload: dict[str, Any]) -> dict[str, Any]:
    config = CampaignConfig.load(payload["config_path"])
    region = str(payload["region"])
    base = BaseArchitecture(**payload["base"])
    profile = str(payload["profile"])
    cn_fractions = [float(x) for x in payload["cn_fractions"]]
    output_root = Path(payload["output_root"])
    compression = str(config.data["storage"]["compression"])
    scenarios = _scenario_subset(build_scenarios(config.data), profile)
    expected_scenario_ids = {scenario.scenario_id for scenario in scenarios}
    region_task_contract = dict(payload["normalized_region_contract"])
    expected_case_contract = {
        "n_tasks": int(region_task_contract["task_count"]),
        "normalized_task_ids_sha256": region_task_contract["task_ids_sha256"],
        "normalized_tasks_sha256": region_task_contract["normalized_tasks_sha256"],
        "run_contract_sha256": payload["run_contract_sha256"],
        "scenario_ids_sha256": payload["scenario_ids_sha256"],
        "expected_scenario_count": len(scenarios),
    }
    simulation = config.data["simulation"]
    communication = config.data["communication"]
    priority_sizes = {k: float(v) for k, v in config.data["dissemination"]["priority_task_size_mbits"].items()}
    worker_dir = output_root / "runs" / region / base.base_id
    worker_dir.mkdir(parents=True, exist_ok=True)
    log_path = worker_dir / "worker.log"
    completed_cases = 0
    failed_cases = 0
    budget = DiskBudget(
        output_root,
        float(config.data["storage"]["maximum_output_gb"]),
        float(config.data["storage"]["minimum_free_space_gb"]),
    )

    try:
        with log_path.open("a", encoding="utf-8") as log, redirect_stdout(log), redirect_stderr(log):
            print(f"\n[{utc_now()}] START {region} {base.base_id} profile={profile}", flush=True)
            resume = bool(config.data["execution"].get("resume", True))
            pending_by_case: dict[str, list[Scenario]] = {}
            for cn_fraction in cn_fractions:
                case_id = _case_id(base, cn_fraction)
                case_dir = worker_dir / case_id
                case_dir.mkdir(parents=True, exist_ok=True)
                completed_ids = _validated_resume_scenario_ids(
                    case_dir,
                    case_id,
                    expected_case_contract,
                    expected_scenario_ids,
                    resume,
                )
                pending = [scenario for scenario in scenarios if scenario.scenario_id not in completed_ids]
                if pending:
                    pending_by_case[case_id] = pending
                    continue
                print(f"[{utc_now()}] SKIP completed {case_id}", flush=True)
                write_json_atomic({
                    "case_id": case_id, "status": "COMPLETED", "finished_at_utc": utc_now(),
                    "scenario_count": len(scenarios), "resumed_from_complete_partitions": True,
                    "run_contract_sha256": payload["run_contract_sha256"],
                }, case_dir / "_SUCCESS.json")
                completed_cases += 1

            if not pending_by_case:
                print(
                    f"[{utc_now()}] FAST-SKIP complete base {region} {base.base_id}; "
                    "no geometry regenerated",
                    flush=True,
                )
                return {
                    "region": region, "base_id": base.base_id, "status": "COMPLETED",
                    "completed_cases": completed_cases, "failed_cases": failed_cases,
                    "fast_resume_skip": True,
                }

            tasks, epoch, duration_hours = _event_tasks(config, region)
            actual_task_contract = _normalized_task_contract(tasks)
            if actual_task_contract != region_task_contract:
                raise RuntimeError(
                    f"Worker task contract changed for {region}: expected {region_task_contract}, "
                    f"got {actual_task_contract}"
                )
            duration_sec = duration_hours * 3600.0
            geometry_started = time.perf_counter()
            geometry_config = legacy.ArchitectureConfig(
                n_satellites=base.n_satellites, n_planes=base.n_planes,
                altitude_km=base.altitude_km, inclination_deg=base.inclination_deg,
                cn_fraction_percent=0.0, walker_f=base.walker_f, raan0_deg=base.raan0_deg,
            )
            _, geometry_nodes, _, geometry_validation = legacy.generate_walker_constellation(geometry_config)
            if not bool(geometry_validation["passed"].all()):
                raise RuntimeError(f"Base geometry validation failed for {base.base_id}")
            time_seconds = np.arange(0.0, duration_sec + 0.1, int(simulation["time_step_sec"]))
            positions, sub_lat, sub_lon = legacy.precompute_satellite_positions(geometry_nodes, time_seconds)
            velocities = legacy.precompute_satellite_velocities(geometry_nodes, time_seconds)
            opportunities = legacy.generate_observation_opportunities(
                geometry_config, geometry_nodes, tasks, epoch, duration_hours,
                int(simulation["time_step_sec"]), float(simulation["observation_radius_km"]),
                subpoint_lat=sub_lat, subpoint_lon=sub_lon, time_seconds=time_seconds,
            )
            print(
                f"[{utc_now()}] BASE-PREP {region} {base.base_id} "
                f"tasks={len(tasks)} opportunities={len(opportunities)} "
                f"elapsed_sec={time.perf_counter() - geometry_started:.3f}",
                flush=True,
            )

            for cn_fraction in cn_fractions:
                case_id = _case_id(base, cn_fraction)
                pending = pending_by_case.get(case_id)
                if pending is None:
                    continue
                case_dir = worker_dir / case_id
                case_started = time.perf_counter()
                arch_config = legacy.ArchitectureConfig(
                    n_satellites=base.n_satellites, n_planes=base.n_planes,
                    altitude_km=base.altitude_km, inclination_deg=base.inclination_deg,
                    cn_fraction_percent=cn_fraction, walker_f=base.walker_f, raan0_deg=base.raan0_deg,
                )
                manifest, nodes, cn_distribution, validation = legacy.generate_walker_constellation(arch_config)
                if not bool(validation["passed"].all()):
                    raise RuntimeError(f"Architecture validation failed for {case_id}")
                ground_stations = legacy.default_ground_stations(str(simulation["ground_station_set"]))
                windows = legacy.generate_communication_windows(
                    arch_config, nodes, ground_stations, epoch, duration_hours,
                    int(simulation["time_step_sec"]), float(communication["sat_sat_max_range_km"]),
                    float(communication["sat_sat_data_rate_bps"]), float(communication["sat_gs_data_rate_bps"]),
                    "all", positions_eci=positions, time_seconds=time_seconds,
                    comm_band=str(communication["comm_band"]),
                    central_power_boost_factor=float(communication["central_power_boost_factor"]),
                    min_window_data_mbits=float(communication["min_window_data_mbits"]),
                    capacity_utilization_limit=float(communication["capacity_utilization_limit"]),
                    velocities_eci=velocities,
                )
                initial_ground_station = ground_stations[0].node_id
                routing_context = prepare_routing_context(
                    nodes, tasks, opportunities, priority_sizes, initial_ground_station
                )
                topology_windows = prepare_topology_windows(
                    windows, nodes, (scenario.topology for scenario in pending)
                )
                write_parquet_atomic(manifest, case_dir / "architecture_manifest.parquet", compression)
                write_parquet_atomic(nodes, case_dir / "constellation_nodes.parquet", compression)
                write_parquet_atomic(cn_distribution, case_dir / "central_node_distribution.parquet", compression)
                write_parquet_atomic(validation, case_dir / "architecture_validation.parquet", compression)
                write_json_atomic({
                    "case_id": case_id, "region": region, "base": asdict(base),
                    "cn_fraction_percent": cn_fraction, "n_central_nodes": arch_config.n_central_nodes,
                    "epoch_utc": legacy.iso_utc(epoch), "duration_hours": duration_hours,
                    "n_tasks": len(tasks), "n_windows_generated": len(windows),
                    "n_observation_opportunities": len(opportunities),
                    "configuration_fingerprint": config.fingerprint,
                    "task_input_sha256": payload.get("task_input_sha256"),
                    "expected_region_task_count": payload.get("expected_region_task_count"),
                    "case_allowlist_sha256": payload.get("case_allowlist_sha256"),
                    "normalized_task_ids_sha256": region_task_contract["task_ids_sha256"],
                    "normalized_tasks_sha256": region_task_contract["normalized_tasks_sha256"],
                    "run_contract_sha256": payload["run_contract_sha256"],
                    "scenario_ids_sha256": payload["scenario_ids_sha256"],
                    "expected_scenario_count": len(scenarios),
                }, case_dir / "case_manifest.json")

                metric_batch: list[dict[str, Any]] = []
                task_batch: list[pd.DataFrame] = []
                transfer_batch: list[pd.DataFrame] = []
                part_number = len(list(case_dir.glob("case_metrics_part_*.parquet")))

                def flush() -> None:
                    nonlocal metric_batch, task_batch, transfer_batch, part_number
                    if not metric_batch:
                        return
                    suffix = f"{part_number:04d}"
                    if task_batch:
                        write_parquet_atomic(pd.concat(task_batch, ignore_index=True), case_dir / f"task_metrics_part_{suffix}.parquet", compression)
                    if transfer_batch:
                        write_parquet_atomic(pd.concat(transfer_batch, ignore_index=True), case_dir / f"transfers_part_{suffix}.parquet", compression)
                    # The case-metric partition is the checkpoint marker and is written last.
                    write_parquet_atomic(pd.DataFrame(metric_batch), case_dir / f"case_metrics_part_{suffix}.parquet", compression)
                    metric_batch, task_batch, transfer_batch = [], [], []
                    part_number += 1

                try:
                    for scenario in pending:
                        if cn_fraction == 0 and scenario.failure_type in {"random_cn_failure", "targeted_cn_failure"}:
                            metric_batch.append(_not_applicable_metrics(case_id, region, scenario, base, cn_fraction))
                        else:
                            seed = scenario.deterministic_seed(int(config.data["random_seed"]), case_id)
                            failure = apply_scenario(
                                scenario, nodes, windows, opportunities, duration_sec, seed,
                                prepared_topology_windows=topology_windows,
                                materialize_opportunities=False,
                            )
                            retain_transfers = _retain_transfers(scenario, base, cn_fraction, config)
                            result = simulate_dissemination(
                                case_id, region, nodes, tasks, opportunities, failure, scenario,
                                epoch, duration_sec, priority_sizes, initial_ground_station, retain_transfers,
                                routing_context,
                            )
                            result.case_metrics.update({
                                "status": "COMPLETED", "n_satellites": base.n_satellites,
                                "n_planes": base.n_planes, "altitude_km": base.altitude_km,
                                "inclination_deg": base.inclination_deg,
                                "cn_fraction_percent": cn_fraction,
                                "n_central_nodes": arch_config.n_central_nodes,
                                "scenario_seed_hex": f"{seed:016x}",
                            })
                            metric_batch.append(result.case_metrics)
                            if _retain_robust_task_metrics(scenario, config):
                                task_batch.append(result.task_metrics)
                            if retain_transfers and not result.transfers.empty:
                                transfer_batch.append(result.transfers)
                        if len(metric_batch) >= 20:
                            flush()
                    flush()
                    write_json_atomic({
                        "case_id": case_id, "status": "COMPLETED", "finished_at_utc": utc_now(),
                        "scenario_count": len(scenarios),
                        "run_contract_sha256": payload["run_contract_sha256"],
                    }, case_dir / "_SUCCESS.json")
                    print(
                        f"[{utc_now()}] COMPLETE {case_id} pending_scenarios={len(pending)} "
                        f"elapsed_sec={time.perf_counter() - case_started:.3f}",
                        flush=True,
                    )
                    completed_cases += 1
                    budget.check()
                except Exception as exc:
                    flush()
                    failed_cases += 1
                    write_json_atomic({
                        "case_id": case_id, "status": "FAILED", "failed_at_utc": utc_now(),
                        "error": str(exc), "traceback": traceback.format_exc(),
                    }, case_dir / "_FAILED.json")
                    print(traceback.format_exc(), flush=True)
                    if not config.data["execution"].get("retry_failed", True):
                        raise
                finally:
                    del windows, nodes, manifest, cn_distribution, validation
            print(f"[{utc_now()}] END {region} {base.base_id}", flush=True)
        return {"region": region, "base_id": base.base_id, "status": "COMPLETED", "completed_cases": completed_cases, "failed_cases": failed_cases}
    except Exception as exc:
        return {"region": region, "base_id": base.base_id, "status": "FAILED", "error": str(exc), "traceback": traceback.format_exc(), "completed_cases": completed_cases, "failed_cases": failed_cases + 1}


def _worker_plan(config: CampaignConfig) -> dict[str, Any]:
    requested = int(config.data["execution"]["max_parallel_base_architectures"])
    per_worker_gb = float(config.data["execution"]["memory_limit_per_worker_gb"])
    reserve_gb = float(config.data["execution"].get("memory_reserve_gb", 4.0))
    available_gb = psutil.virtual_memory().available / 1024 ** 3
    usable_gb = max(0.0, available_gb - reserve_gb)
    memory_workers = max(1, int(usable_gb // per_worker_gb))
    cpu_count = os.cpu_count() or 1
    selected = max(1, min(requested, cpu_count, memory_workers))
    return {
        "requested_workers": requested,
        "selected_workers": selected,
        "logical_cpu_count": cpu_count,
        "available_ram_gb": round(available_gb, 3),
        "memory_reserve_gb": reserve_gb,
        "usable_ram_gb": round(usable_gb, 3),
        "estimated_ram_per_worker_gb": per_worker_gb,
        "memory_limited_workers": memory_workers,
    }


def _effective_workers(config: CampaignConfig) -> int:
    return int(_worker_plan(config)["selected_workers"])


def build_manifest(config: CampaignConfig, profile: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    bases = list(config.iter_base_architectures())
    if profile == "smoke":
        bases = bases[: int(config.data["execution"]["smoke_base_architectures"])]
        fractions = [float(x) for x in config.data["execution"]["smoke_cn_fractions"]]
    else:
        fractions = config.cn_fractions
    allowlist, _, _ = _load_case_allowlist(config)
    allowed = set(allowlist) if allowlist is not None else None
    rows = []
    for region in config.data["regions"]:
        for base in bases:
            for fraction in fractions:
                case_id = _case_id(base, fraction)
                if allowed is not None and case_id not in allowed:
                    continue
                rows.append({"region": region, **asdict(base), "cn_fraction_percent": fraction, "case_id": case_id})
    scenarios = _scenario_subset(build_scenarios(config.data), profile)
    return pd.DataFrame(rows), pd.DataFrame(scenario_manifest(scenarios))


def _require_canary_gate(config: CampaignConfig, input_contract: dict[str, Any]) -> None:
    raw_path = config.data["execution"].get("required_canary_report")
    if not raw_path:
        return
    path = _resolve_contract_path(config, str(raw_path))
    if not path.is_file():
        raise RuntimeError(
            f"CANARY REQUIRED: {path} does not exist. Run bash RUN_CANARY_VM.sh and "
            "obtain status PASS before starting the 282-case campaign."
        )
    report = _load_json(path)
    expected_region = input_contract["normalized_regions"]["India"]
    smoke_cases, smoke_scenarios = build_manifest(config, "smoke")
    smoke_contract = build_run_contract(
        config, input_contract, smoke_cases, smoke_scenarios, "smoke"
    )
    required = {
        "status": "PASS",
        "task_input_sha256": input_contract["task_input_sha256"],
        "normalized_task_ids_sha256": expected_region["task_ids_sha256"],
        "normalized_tasks_sha256": expected_region["normalized_tasks_sha256"],
        "verified_complete_cases": 1,
        "scenario_count_per_case": len(smoke_scenarios),
        "run_contract_sha256": smoke_contract["run_contract_sha256"],
    }
    mismatches = {
        key: {"found": report.get(key), "expected": expected}
        for key, expected in required.items()
        if report.get(key) != expected
    }
    if mismatches:
        raise RuntimeError(
            f"CANARY FAILED CONTRACT CHECK: {json.dumps(mismatches, sort_keys=True)}"
        )


def run_campaign(config_path: str | Path, profile: str = "full", output_override: str | Path | None = None) -> dict[str, Any]:
    config = CampaignConfig.load(config_path)
    contract = validate_campaign_contract(config)
    cases, scenarios = build_manifest(config, profile)
    if cases.empty:
        raise RuntimeError("The case-selection contract produced no runnable cases")
    run_contract = build_run_contract(config, contract, cases, scenarios, profile)
    output_root = Path(output_override).resolve() if output_override else config.output_root.resolve()
    protected_roots = [
        _resolve_contract_path(config, value)
        for value in config.data["execution"].get("protected_output_roots", [])
    ]
    if output_root in protected_roots or output_root.name == "full891_fresh_complete":
        raise ValueError(f"Refusing to write the repair campaign into protected output root: {output_root}")
    assert_output_root_compatible(output_root, run_contract)
    if profile == "full":
        _require_canary_gate(config, contract)
    output_root.mkdir(parents=True, exist_ok=True)
    storage = config.data["storage"]
    budget = DiskBudget(output_root, float(storage["maximum_output_gb"]), float(storage["minimum_free_space_gb"]))
    disk_status = budget.check()
    worker_plan = _worker_plan(config)
    write_parquet_atomic(cases, output_root / "campaign_case_manifest.parquet", str(storage["compression"]))
    write_parquet_atomic(scenarios, output_root / "scenario_manifest.parquet", str(storage["compression"]))
    event_rows = []
    for region in config.data["regions"]:
        event_tasks, event_epoch, event_duration = _event_tasks(config, str(region))
        write_parquet_atomic(
            event_tasks,
            output_root / "inputs" / f"tasks_{str(region).lower()}.parquet",
            str(storage["compression"]),
        )
        event_rows.append({
            "region": region, "epoch_utc": legacy.iso_utc(event_epoch),
            "duration_hours": event_duration, "task_count": len(event_tasks),
            "latest_deadline_time_sec": float(event_tasks["deadline_time_sec"].max()),
        })
    write_parquet_atomic(pd.DataFrame(event_rows), output_root / "event_manifest.parquet", str(storage["compression"]))
    write_json_atomic({
        "created_at_utc": utc_now(), "package_version": __version__,
        "configuration_fingerprint": config.fingerprint,
        "configuration_path": str(config.path), "task_input": str(config.tasks_path),
        "task_input_sha256": contract["task_input_sha256"],
        "task_counts": contract["task_counts"],
        "case_allowlist": contract["case_allowlist"],
        "case_allowlist_sha256": contract["case_allowlist_sha256"],
        "normalized_regions": contract["normalized_regions"],
        "run_contract_sha256": run_contract["run_contract_sha256"],
        "run_contract": run_contract,
        "selected_case_count": len(cases), "profile": profile,
        "case_count": len(cases), "scenario_count_per_case": len(scenarios),
        "expected_case_scenario_evaluations": len(cases) * len(scenarios),
        "python": sys.version, "platform": platform.platform(), "initial_disk_status": disk_status,
        "initial_worker_plan": worker_plan,
    }, output_root / "provenance.json")

    group_columns = [
        "region", "n_satellites", "n_planes", "altitude_km",
        "inclination_deg", "walker_f", "raan0_deg",
    ]
    payloads: list[dict[str, Any]] = []
    for key, selected in cases.groupby(group_columns, sort=False):
        region, n_satellites, n_planes, altitude_km, inclination_deg, walker_f, raan0_deg = key
        base = BaseArchitecture(
            int(n_satellites), int(n_planes), float(altitude_km),
            float(inclination_deg), int(walker_f), float(raan0_deg),
        )
        payloads.append({
            "config_path": str(config.path), "region": str(region), "base": asdict(base),
            "profile": profile,
            "cn_fractions": sorted(selected["cn_fraction_percent"].astype(float).tolist()),
            "output_root": str(output_root),
            "task_input_sha256": contract["task_input_sha256"],
            "expected_region_task_count": int(contract["task_counts"][str(region)]),
            "case_allowlist_sha256": contract["case_allowlist_sha256"],
            "normalized_region_contract": contract["normalized_regions"][str(region)],
            "run_contract_sha256": run_contract["run_contract_sha256"],
            "scenario_ids_sha256": run_contract["scenario_ids_sha256"],
        })
    workers = int(worker_plan["selected_workers"])
    print(f"WORKER-PLAN {json.dumps(worker_plan, sort_keys=True)}", flush=True)
    status_rows: list[dict[str, Any]] = []
    if workers == 1:
        for payload in payloads:
            status_rows.append(_run_base_worker(payload))
            budget.check()
    else:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_run_base_worker, payload): payload for payload in payloads}
            for future in as_completed(futures):
                result = future.result()
                status_rows.append(result)
                print(
                    f"[{len(status_rows)}/{len(payloads)}] {result.get('status')} "
                    f"{result.get('region')} {result.get('base_id')}",
                    flush=True,
                )
                budget.check()
                write_parquet_atomic(pd.DataFrame(status_rows), output_root / "base_worker_status.parquet", str(storage["compression"]))
    status = pd.DataFrame(status_rows)
    write_parquet_atomic(status, output_root / "base_worker_status.parquet", str(storage["compression"]))
    report = {
        "finished_at_utc": utc_now(), "profile": profile, "workers": workers,
        "base_worker_count": len(payloads),
        "completed_base_workers": int((status["status"] == "COMPLETED").sum()) if len(status) else 0,
        "failed_base_workers": int((status["status"] == "FAILED").sum()) if len(status) else 0,
        "final_disk_status": budget.check(),
    }
    write_json_atomic(report, output_root / "campaign_run_report.json")
    return report
