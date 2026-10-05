from __future__ import annotations

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
from .storage import DiskBudget, existing_scenario_ids, sha256_file, write_json_atomic, write_parquet_atomic


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _event_tasks(config: CampaignConfig, region: str) -> tuple[pd.DataFrame, datetime, float]:
    raw = pd.read_csv(config.tasks_path)
    if "region" not in raw:
        raise ValueError("Task input requires a region column")
    selected = raw.loc[raw["region"].astype(str).str.casefold() == region.casefold()].copy()
    if selected.empty:
        raise ValueError(f"No tasks found for region {region}")
    timestamp = pd.to_datetime(selected["timestamp_utc"], utc=True, errors="raise")
    epoch = timestamp.min().to_pydatetime()
    tasks = legacy.normalize_tasks(selected, epoch=epoch, task_time_mode="original")
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
                completed_ids = existing_scenario_ids(case_dir) if resume else set()
                pending = [scenario for scenario in scenarios if scenario.scenario_id not in completed_ids]
                if pending:
                    pending_by_case[case_id] = pending
                    continue
                print(f"[{utc_now()}] SKIP completed {case_id}", flush=True)
                write_json_atomic({
                    "case_id": case_id, "status": "COMPLETED", "finished_at_utc": utc_now(),
                    "scenario_count": len(scenarios), "resumed_from_complete_partitions": True,
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
    rows = []
    for region in config.data["regions"]:
        for base in bases:
            for fraction in fractions:
                rows.append({"region": region, **asdict(base), "cn_fraction_percent": fraction, "case_id": _case_id(base, fraction)})
    scenarios = _scenario_subset(build_scenarios(config.data), profile)
    return pd.DataFrame(rows), pd.DataFrame(scenario_manifest(scenarios))


def run_campaign(config_path: str | Path, profile: str = "full", output_override: str | Path | None = None) -> dict[str, Any]:
    config = CampaignConfig.load(config_path)
    output_root = Path(output_override).resolve() if output_override else config.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    storage = config.data["storage"]
    budget = DiskBudget(output_root, float(storage["maximum_output_gb"]), float(storage["minimum_free_space_gb"]))
    disk_status = budget.check()
    worker_plan = _worker_plan(config)
    cases, scenarios = build_manifest(config, profile)
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
        "task_input_sha256": sha256_file(config.tasks_path), "profile": profile,
        "case_count": len(cases), "scenario_count_per_case": len(scenarios),
        "expected_case_scenario_evaluations": len(cases) * len(scenarios),
        "python": sys.version, "platform": platform.platform(), "initial_disk_status": disk_status,
        "initial_worker_plan": worker_plan,
    }, output_root / "provenance.json")

    if profile == "smoke":
        bases = list(config.iter_base_architectures())[: int(config.data["execution"]["smoke_base_architectures"])]
        fractions = [float(x) for x in config.data["execution"]["smoke_cn_fractions"]]
    else:
        bases = list(config.iter_base_architectures())
        fractions = config.cn_fractions
    payloads = [
        {
            "config_path": str(config.path), "region": region, "base": asdict(base),
            "profile": profile, "cn_fractions": fractions, "output_root": str(output_root),
        }
        for region in config.data["regions"] for base in bases
    ]
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
