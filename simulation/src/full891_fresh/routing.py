from __future__ import annotations

import bisect
import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from . import legacy_simulator as legacy
from .failures import FailureOutcome
from .scenarios import Scenario


MILESTONES = (10, 25, 50, 75, 90, 95, 100)


@dataclass
class RoutingResult:
    task_metrics: pd.DataFrame
    case_metrics: dict[str, Any]
    transfers: pd.DataFrame


@dataclass(frozen=True)
class TaskStaticInfo:
    created: float
    deadline: float
    priority: str
    priority_rank: int
    base_size_mbits: float


@dataclass(frozen=True)
class RoutingStaticContext:
    """Failure-independent routing data reused by every scenario in one case."""

    sat_ids: frozenset[str]
    node_type: dict[str, str]
    task_info: dict[str, TaskStaticInfo]
    original_eligible: dict[str, frozenset[str]]
    opportunity_times: dict[tuple[str, str], tuple[float, ...]]


def _safe_quantile(values: list[float], q: float) -> float:
    valid = np.asarray([v for v in values if np.isfinite(v)], dtype=float)
    return float(np.quantile(valid, q)) if valid.size else float("nan")


def _eligible_by_task(
    tasks: pd.DataFrame,
    opportunities: pd.DataFrame,
    sat_ids: set[str],
) -> dict[str, set[str]]:
    deadlines = dict(zip(tasks["task_id"].astype(str), pd.to_numeric(tasks["deadline_time_sec"])))
    result = {task_id: set() for task_id in deadlines}
    if opportunities.empty:
        return result
    columns = opportunities[["task_id", "node_id", "earliest_observation_time_sec"]]
    for task_id_value, node_value, time_value in columns.itertuples(index=False, name=None):
        task_id, node = str(task_id_value), str(node_value)
        if task_id in result and node in sat_ids:
            time_sec = float(time_value)
            if time_sec <= float(deadlines[task_id]):
                result[task_id].add(node)
    return result


def _opportunity_times(opportunities: pd.DataFrame) -> dict[tuple[str, str], tuple[float, ...]]:
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    if opportunities.empty:
        return {}
    columns = opportunities[["task_id", "node_id", "earliest_observation_time_sec"]]
    for task_id, node_id, time_sec in columns.itertuples(index=False, name=None):
        grouped[(str(task_id), str(node_id))].append(float(time_sec))
    return {key: tuple(sorted(values)) for key, values in grouped.items()}


def prepare_routing_context(
    nodes: pd.DataFrame,
    tasks: pd.DataFrame,
    opportunities: pd.DataFrame,
    priority_sizes_mbits: dict[str, float],
    initial_ground_station: str,
) -> RoutingStaticContext:
    """Build once per architecture/CN case instead of once per scenario."""

    sat_ids = frozenset(nodes["node_id"].astype(str))
    node_type = dict(zip(nodes["node_id"].astype(str), nodes["node_type"].astype(str)))
    node_type[initial_ground_station] = "ground_station"
    task_info: dict[str, TaskStaticInfo] = {}
    task_columns = tasks[["task_id", "priority_class", "created_time_sec", "deadline_time_sec"]]
    for task_id_value, priority_value, created_value, deadline_value in task_columns.itertuples(
        index=False, name=None
    ):
        task_id = str(task_id_value)
        priority = str(priority_value)
        task_info[task_id] = TaskStaticInfo(
            created=float(created_value),
            deadline=float(deadline_value),
            priority=priority,
            priority_rank=legacy.PRIORITY_RANK.get(priority, 2),
            base_size_mbits=float(priority_sizes_mbits.get(priority, 1.0)),
        )
    eligible = _eligible_by_task(tasks, opportunities, set(sat_ids))
    return RoutingStaticContext(
        sat_ids=sat_ids,
        node_type=node_type,
        task_info=task_info,
        original_eligible={task_id: frozenset(targets) for task_id, targets in eligible.items()},
        opportunity_times=_opportunity_times(opportunities),
    )


def _milestone_latency(
    sorted_reception_times: list[float],
    target_count: int,
    percent: int,
    created_sec: float,
) -> float:
    if target_count <= 0:
        return float("nan")
    required = int(math.ceil(target_count * percent / 100.0))
    if len(sorted_reception_times) < required:
        return float("nan")
    return (sorted_reception_times[required - 1] - created_sec) / 60.0


def simulate_dissemination(
    case_id: str,
    region: str,
    nodes: pd.DataFrame,
    tasks: pd.DataFrame,
    original_opportunities: pd.DataFrame,
    failure: FailureOutcome,
    scenario: Scenario,
    epoch,
    duration_sec: float,
    priority_sizes_mbits: dict[str, float],
    initial_ground_station: str,
    retain_transfers: bool,
    routing_context: RoutingStaticContext | None = None,
) -> RoutingResult:
    supplied_context = routing_context is not None
    if routing_context is None:
        routing_context = prepare_routing_context(
            nodes, tasks, original_opportunities, priority_sizes_mbits, initial_ground_station
        )
    sat_ids = routing_context.sat_ids
    surviving_sat_ids = sat_ids - failure.failed_nodes
    node_type = routing_context.node_type
    original_eligible = routing_context.original_eligible
    surviving_eligible = {
        task_id: targets - failure.failed_nodes for task_id, targets in original_eligible.items()
    }

    task_info = {
        task_id: {
            "created": static.created,
            "deadline": static.deadline,
            "priority": static.priority,
            "priority_rank": static.priority_rank,
            "size": static.base_size_mbits * scenario.task_size_multiplier,
        }
        for task_id, static in routing_context.task_info.items()
    }

    if scenario.receiver_target == "all_satellites":
        original_targets = {task_id: sat_ids for task_id in task_info}
        surviving_targets = {task_id: surviving_sat_ids for task_id in task_info}
    elif scenario.receiver_target == "all_useful":
        original_targets = original_eligible
        surviving_targets = surviving_eligible
    else:
        raise ValueError(f"Unknown receiver target: {scenario.receiver_target}")

    received: dict[tuple[str, str], tuple[float, int, str]] = {}
    node_received: dict[str, set[str]] = defaultdict(set)
    for task_id, info in task_info.items():
        received[(task_id, initial_ground_station)] = (info["created"], 0, "GROUND_TASK_SERVICE")
        node_received[initial_ground_station].add(task_id)

    observer_copies = defaultdict(int)
    cn_copies = defaultdict(int)
    transfer_rows: list[dict[str, Any]] = []
    transfer_count = 0
    total_data_mbits = 0.0
    max_hops_seen = 0
    node_sent_data = defaultdict(float)
    cn_related_data_mbits = 0.0

    routing_policy = scenario.routing_policy
    if routing_policy not in {"peer", "targeted_central", "hybrid"}:
        raise ValueError(f"Unknown routing policy: {routing_policy}")
    max_cn_copies = scenario.max_cn_copies
    max_observer_copies = scenario.max_observer_copies

    windows = failure.windows
    if not windows.empty and not windows.attrs.get("full891_sorted_windows", False):
        windows = windows.sort_values(["start_time_sec", "end_time_sec", "window_id"])
    if not windows.empty:
        node_as = windows["node_i"].astype(str).to_numpy(copy=False)
        node_bs = windows["node_j"].astype(str).to_numpy(copy=False)
        window_ids = windows["window_id"].astype(str).to_numpy(copy=False)
        starts = pd.to_numeric(windows["start_time_sec"], errors="coerce").to_numpy(copy=False)
        ends = pd.to_numeric(windows["end_time_sec"], errors="coerce").to_numpy(copy=False)
        rate_column = "data_rate_bps" if "data_rate_bps" in windows else "mean_data_rate_bps"
        if rate_column in windows:
            rates = pd.to_numeric(windows[rate_column], errors="coerce").to_numpy(copy=False)
        else:
            rates = np.zeros(len(windows), dtype=float)
        if "capacity_mbits" in windows:
            capacities = pd.to_numeric(windows["capacity_mbits"], errors="coerce").to_numpy(copy=False)
        else:
            capacities = rates * (ends - starts) / 1e6
        sat_gs_values = windows.get("is_sat_gs", pd.Series(False, index=windows.index))
        is_sat_gs_values = sat_gs_values.fillna(False).astype(bool).to_numpy(copy=False)
        window_records = zip(
            window_ids, node_as, node_bs, starts, ends, rates, capacities, is_sat_gs_values
        )
    else:
        window_records = ()
    for window_id, node_a, node_b, window_start, window_end, data_rate_bps, capacity, is_sat_gs in window_records:
        window_start, window_end = float(window_start), float(window_end)
        data_rate_bps, capacity = float(data_rate_bps), float(capacity)
        if data_rate_bps <= 0 or capacity <= 0 or window_end <= window_start:
            continue
        is_sat_gs = bool(is_sat_gs)
        # Direction eligibility is constant for an entire contact window.  Computing
        # it here avoids tens of millions of tiny Python function calls in large
        # campaigns while preserving the existing routing rules exactly.
        if is_sat_gs:
            directions: list[tuple[str, str]] = []
            if node_type.get(node_a, "unknown") == "ground_station" and node_b in surviving_sat_ids:
                directions.append((node_a, node_b))
            if node_type.get(node_b, "unknown") == "ground_station" and node_a in surviving_sat_ids:
                directions.append((node_b, node_a))
        elif node_a not in surviving_sat_ids or node_b not in surviving_sat_ids:
            directions = []
        elif routing_policy == "targeted_central":
            type_a = node_type.get(node_a, "unknown")
            type_b = node_type.get(node_b, "unknown")
            directions = []
            if (
                (type_a == "central_node" and type_b in {"central_node", "observer"})
                or (type_a == "observer" and type_b == "central_node")
            ):
                directions.append((node_a, node_b))
            if (
                (type_b == "central_node" and type_a in {"central_node", "observer"})
                or (type_b == "observer" and type_a == "central_node")
            ):
                directions.append((node_b, node_a))
        else:
            directions = [(node_a, node_b), (node_b, node_a)]
        if not directions:
            continue
        current_time = window_start
        while current_time < window_end and capacity > 1e-12:
            best: tuple[Any, ...] | None = None
            for sender, receiver in directions:
                receiver_type = node_type.get(receiver, "unknown")
                receiver_is_cn = receiver_type == "central_node"
                receiver_is_observer = receiver_type == "observer"
                for task_id in node_received.get(sender, ()):
                    if (task_id, receiver) in received:
                        continue
                    is_target = receiver in surviving_targets[task_id]
                    if routing_policy == "targeted_central" and receiver_is_observer and not is_target:
                        continue
                    if receiver_is_cn and max_cn_copies > 0 and cn_copies[task_id] >= max_cn_copies:
                        continue
                    if (
                        receiver_is_observer
                        and max_observer_copies > 0
                        and observer_copies[task_id] >= max_observer_copies
                    ):
                        continue
                    info = task_info[task_id]
                    sender_time, sender_hops, _ = received[(task_id, sender)]
                    if sender_time > current_time + 1e-9:
                        continue
                    if scenario.max_forwarding_hops >= 0 and sender_hops >= scenario.max_forwarding_hops:
                        continue
                    cutoff = info["deadline"] if scenario.delivery_cutoff == "deadline" else duration_sec
                    if current_time > cutoff + 1e-9:
                        continue
                    size = info["size"]
                    duration = size * 1e6 / data_rate_bps
                    end_time = current_time + duration
                    if end_time > window_end + 1e-9 or end_time > cutoff + 1e-9 or size > capacity + 1e-9:
                        continue
                    target_bonus = 0 if is_target else 1
                    if routing_policy == "hybrid":
                        # Hybrid deliberately prefers the CN backbone but retains peer fallback.
                        coordination_bonus = 0 if receiver_is_cn else 1
                        score = (
                            info["priority_rank"], info["deadline"], coordination_bonus,
                            target_bonus, task_id, receiver,
                        )
                    elif routing_policy == "targeted_central":
                        cn_bonus = 0 if receiver_is_cn else 1
                        score = (info["priority_rank"], info["deadline"], target_bonus, cn_bonus, task_id, receiver)
                    else:
                        # Peer routing gives no priority advantage to a node merely for being central.
                        score = (info["priority_rank"], info["deadline"], target_bonus, 1, task_id, receiver)
                    candidate = (score, task_id, sender, receiver, size, duration, sender_hops + 1)
                    if best is None or candidate[0] < best[0]:
                        best = candidate
            if best is None:
                break
            _, task_id, sender, receiver, size, transfer_duration, hops = best
            start_time, end_time = current_time, current_time + transfer_duration
            received[(task_id, receiver)] = (end_time, hops, sender)
            node_received[receiver].add(task_id)
            if node_type.get(receiver) == "central_node":
                cn_copies[task_id] += 1
            else:
                observer_copies[task_id] += 1
            capacity -= size
            transfer_count += 1
            total_data_mbits += size
            if node_type.get(sender) == "central_node" or node_type.get(receiver) == "central_node":
                cn_related_data_mbits += size
            node_sent_data[sender] += size
            max_hops_seen = max(max_hops_seen, hops)
            if retain_transfers:
                transfer_rows.append({
                    "case_id": case_id, "region": region, "scenario_id": scenario.scenario_id,
                    "window_id": str(window_id), "task_id": task_id,
                    "from_node": sender, "to_node": receiver,
                    "transfer_start_time_sec": start_time, "transfer_end_time_sec": end_time,
                    "transfer_duration_sec": transfer_duration, "transferred_data_mbits": size,
                    "hop_count_after_transfer": hops,
                })
            current_time = end_time

    opp_times = (
        routing_context.opportunity_times
        if supplied_context
        else _opportunity_times(failure.opportunities)
    )

    task_rows: list[dict[str, Any]] = []
    for task_id, info in task_info.items():
        original = original_targets[task_id]
        surviving = surviving_targets[task_id]
        received_surviving = {
            node: received[(task_id, node)][0]
            for node in surviving if (task_id, node) in received
        }
        received_original = {
            node: received[(task_id, node)][0]
            for node in original if (task_id, node) in received
        }
        surviving_times = sorted(received_surviving.values())
        original_times = sorted(received_original.values())
        by_deadline_surviving = [t for t in surviving_times if t <= info["deadline"] + 1e-9]
        by_end_surviving = [t for t in surviving_times if t <= duration_sec + 1e-9]
        by_deadline_original = [t for t in original_times if t <= info["deadline"] + 1e-9]
        by_end_original = [t for t in original_times if t <= duration_sec + 1e-9]

        observation_time = float("nan")
        observing_node = ""
        for node, received_time in received_surviving.items():
            if received_time > info["deadline"]:
                continue
            times = opp_times.get((task_id, node), [])
            index = bisect.bisect_left(times, received_time)
            if index < len(times) and times[index] <= info["deadline"]:
                if not np.isfinite(observation_time) or times[index] < observation_time:
                    observation_time, observing_node = times[index], node

        row: dict[str, Any] = {
            "case_id": case_id, "region": region, "scenario_id": scenario.scenario_id,
            "family": scenario.family, "task_id": task_id, "priority_class": info["priority"],
            "task_size_mbits": info["size"], "created_time_sec": info["created"],
            "deadline_time_sec": info["deadline"], "original_target_count": len(original),
            "surviving_target_count": len(surviving), "failed_target_count": len(original - surviving),
            "reached_original_by_deadline": len(by_deadline_original),
            "reached_original_by_sim_end": len(by_end_original),
            "reached_surviving_by_deadline": len(by_deadline_surviving),
            "reached_surviving_by_sim_end": len(by_end_surviving),
            "original_coverage_deadline_percent": 100.0 * len(by_deadline_original) / len(original) if original else np.nan,
            "original_coverage_sim_end_percent": 100.0 * len(by_end_original) / len(original) if original else np.nan,
            "surviving_coverage_deadline_percent": 100.0 * len(by_deadline_surviving) / len(surviving) if surviving else np.nan,
            "surviving_coverage_sim_end_percent": 100.0 * len(by_end_surviving) / len(surviving) if surviving else np.nan,
            "s100_original_before_deadline": bool(original and len(by_deadline_original) == len(original)),
            "s100_original_by_sim_end": bool(original and len(by_end_original) == len(original)),
            "s100_surviving_before_deadline": bool(surviving and len(by_deadline_surviving) == len(surviving)),
            "s100_surviving_by_sim_end": bool(surviving and len(by_end_surviving) == len(surviving)),
            "observed_before_deadline": bool(np.isfinite(observation_time)),
            "observing_node": observing_node,
            "observation_latency_min": (observation_time - info["created"]) / 60.0 if np.isfinite(observation_time) else np.nan,
        }
        if not original:
            row["failure_class"] = "geometry_failure_no_eligible_receiver"
        elif not by_deadline_surviving:
            row["failure_class"] = "communication_failure_no_target_reached"
        elif not np.isfinite(observation_time):
            row["failure_class"] = "routing_or_timing_failure"
        else:
            row["failure_class"] = "success"
        for percent in MILESTONES:
            row[f"t{percent}_surviving_min"] = _milestone_latency(
                surviving_times, len(surviving), percent, info["created"]
            )
            row[f"t{percent}_original_min"] = _milestone_latency(
                original_times, len(original), percent, info["created"]
            )
        row["t100_surviving_censored"] = not bool(row["s100_surviving_by_sim_end"])
        row["t100_original_censored"] = not bool(row["s100_original_by_sim_end"])
        task_rows.append(row)

    task_metrics = pd.DataFrame(task_rows)
    t100_surviving = pd.to_numeric(task_metrics["t100_surviving_min"], errors="coerce").dropna().tolist()
    t100_original = pd.to_numeric(task_metrics["t100_original_min"], errors="coerce").dropna().tolist()
    sent_loads = [node_sent_data.get(node, 0.0) for node in sorted(surviving_sat_ids)]
    def gini(values: list[float]) -> float:
        array = np.sort(np.asarray(values, dtype=float))
        if not len(array) or float(array.sum()) <= 0:
            return 0.0
        index = np.arange(1, len(array) + 1)
        return float((2.0 * np.sum(index * array) / (len(array) * array.sum())) - (len(array) + 1.0) / len(array))

    available_capacity = float(pd.to_numeric(windows.get("capacity_mbits", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
    available_duration = float(pd.to_numeric(windows.get("duration_sec", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
    case_metrics: dict[str, Any] = {
        "case_id": case_id, "region": region, "scenario_id": scenario.scenario_id,
        "family": scenario.family, "topology": scenario.topology,
        "routing_policy": scenario.routing_policy, "receiver_target": scenario.receiver_target,
        "delivery_cutoff": scenario.delivery_cutoff,
        "max_forwarding_hops": scenario.max_forwarding_hops,
        "max_observer_copies": scenario.max_observer_copies,
        "max_cn_copies": scenario.max_cn_copies,
        "task_size_multiplier": scenario.task_size_multiplier,
        "failure_type": scenario.failure_type,
        "failure_level_percent": scenario.failure_level_percent,
        "replicate": scenario.replicate, "applicability": failure.applicability,
        "n_tasks": len(task_metrics), "n_failed_nodes": len(failure.failed_nodes),
        "failed_nodes": ";".join(sorted(failure.failed_nodes)),
        "removed_window_count": failure.removed_window_count,
        "n_available_windows": len(windows),
        "available_window_duration_sec": available_duration,
        "available_window_capacity_mbits": available_capacity,
        "n_transfer_events": transfer_count, "total_transferred_data_mbits": total_data_mbits,
        "used_capacity_percent": 100.0 * total_data_mbits / available_capacity if available_capacity > 0 else np.nan,
        "cn_related_traffic_share_percent": 100.0 * cn_related_data_mbits / total_data_mbits if total_data_mbits > 0 else 0.0,
        "max_hops_seen": max_hops_seen,
        "mean_original_coverage_deadline_percent": float(task_metrics["original_coverage_deadline_percent"].mean()),
        "mean_original_coverage_sim_end_percent": float(task_metrics["original_coverage_sim_end_percent"].mean()),
        "mean_surviving_coverage_deadline_percent": float(task_metrics["surviving_coverage_deadline_percent"].mean()),
        "mean_surviving_coverage_sim_end_percent": float(task_metrics["surviving_coverage_sim_end_percent"].mean()),
        "s100_original_before_deadline_percent": 100.0 * float(task_metrics["s100_original_before_deadline"].mean()),
        "s100_original_by_sim_end_percent": 100.0 * float(task_metrics["s100_original_by_sim_end"].mean()),
        "s100_surviving_before_deadline_percent": 100.0 * float(task_metrics["s100_surviving_before_deadline"].mean()),
        "s100_surviving_by_sim_end_percent": 100.0 * float(task_metrics["s100_surviving_by_sim_end"].mean()),
        "observation_success_percent": 100.0 * float(task_metrics["observed_before_deadline"].mean()),
        "geometry_failure_count": int((task_metrics["failure_class"] == "geometry_failure_no_eligible_receiver").sum()),
        "communication_failure_count": int((task_metrics["failure_class"] == "communication_failure_no_target_reached").sum()),
        "routing_or_timing_failure_count": int((task_metrics["failure_class"] == "routing_or_timing_failure").sum()),
        "disconnected_task_count_original": int((task_metrics["reached_original_by_sim_end"] == 0).sum()),
        "t100_completed_task_count_surviving": len(t100_surviving),
        "t100_censored_task_count_surviving": int(task_metrics["t100_surviving_censored"].sum()),
        "median_t100_surviving_min": _safe_quantile(t100_surviving, 0.50),
        "p90_t100_surviving_min": _safe_quantile(t100_surviving, 0.90),
        "p95_t100_surviving_min": _safe_quantile(t100_surviving, 0.95),
        "p100_t100_surviving_min": _safe_quantile(t100_surviving, 1.00),
        "t100_completed_task_count_original": len(t100_original),
        "t100_censored_task_count_original": int(task_metrics["t100_original_censored"].sum()),
        "p100_t100_original_min": _safe_quantile(t100_original, 1.00),
        "peak_node_sent_data_mbits": max(node_sent_data.values(), default=0.0),
        "mean_node_sent_data_mbits": float(np.mean(sent_loads)) if sent_loads else 0.0,
        "node_sent_data_gini": gini(sent_loads),
    }
    for priority in ["Critical", "High", "Medium", "Low"]:
        subset = task_metrics.loc[task_metrics["priority_class"] == priority]
        slug = priority.lower()
        case_metrics[f"n_tasks_{slug}"] = len(subset)
        case_metrics[f"{slug}_observation_success_percent"] = (
            100.0 * float(subset["observed_before_deadline"].mean()) if len(subset) else np.nan
        )
        case_metrics[f"{slug}_s100_surviving_before_deadline_percent"] = (
            100.0 * float(subset["s100_surviving_before_deadline"].mean()) if len(subset) else np.nan
        )

    return RoutingResult(task_metrics, case_metrics, pd.DataFrame(transfer_rows))
