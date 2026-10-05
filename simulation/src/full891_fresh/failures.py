from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from .scenarios import Scenario


@dataclass
class FailureOutcome:
    windows: pd.DataFrame
    opportunities: pd.DataFrame
    failed_nodes: set[str]
    removed_window_count: int
    ground_outage_start_sec: float | None = None
    ground_outage_end_sec: float | None = None
    applicability: str = "applicable"


def _positive_count(population: int, percent: float) -> int:
    if population <= 0 or percent <= 0:
        return 0
    return min(population, max(1, int(math.ceil(population * percent / 100.0))))


def _choose(values: list[str], count: int, rng: np.random.Generator) -> set[str]:
    if count <= 0:
        return set()
    indices = rng.choice(len(values), size=count, replace=False)
    return {values[int(index)] for index in np.atleast_1d(indices)}


def _targeted_central_nodes(nodes: pd.DataFrame, windows: pd.DataFrame, count: int) -> set[str]:
    central = set(nodes.loc[nodes["is_central_node"].astype(bool), "node_id"].astype(str))
    if count <= 0 or not central:
        return set()
    score = {node: 0.0 for node in central}
    degree = {node: set() for node in central}
    capacity_column = "capacity_mbits" if "capacity_mbits" in windows else "window_capacity_mbits"
    if capacity_column in windows:
        selected = windows[["node_i", "node_j", capacity_column]]
        records = selected.itertuples(index=False, name=None)
    else:
        records = ((a, b, 0.0) for a, b in windows[["node_i", "node_j"]].itertuples(index=False, name=None))
    for a_value, b_value, capacity_value in records:
        a, b = str(a_value), str(b_value)
        capacity = float(capacity_value or 0.0)
        if a in central:
            score[a] += capacity
            degree[a].add(b)
        if b in central:
            score[b] += capacity
            degree[b].add(a)
    ranked = sorted(central, key=lambda n: (-score[n], -len(degree[n]), n))
    return set(ranked[:count])


def _filter_topology(windows: pd.DataFrame, nodes: pd.DataFrame, topology: str) -> pd.DataFrame:
    topology = topology.lower()
    if topology not in {"central_only", "peer_only", "hybrid"}:
        raise ValueError(f"Unknown topology: {topology}")
    if windows.empty or topology in {"peer_only", "hybrid"}:
        return windows
    central = set(nodes.loc[nodes["is_central_node"].astype(bool), "node_id"].astype(str))
    sat_gs = windows.get("is_sat_gs", pd.Series(False, index=windows.index)).astype(bool)
    allowed = sat_gs | windows["node_i"].astype(str).isin(central) | windows["node_j"].astype(str).isin(central)
    return windows.loc[allowed].copy()


def prepare_topology_windows(
    windows: pd.DataFrame,
    nodes: pd.DataFrame,
    topologies: Iterable[str],
) -> dict[str, pd.DataFrame]:
    """Filter and sort each topology once for reuse across scenario failures."""

    if windows.empty:
        ordered = windows.copy()
    else:
        ordered = windows.sort_values(
            ["start_time_sec", "end_time_sec", "window_id"]
        ).reset_index(drop=True)
    ordered.attrs["full891_sorted_windows"] = True
    prepared: dict[str, pd.DataFrame] = {}
    for topology in sorted(set(str(value).lower() for value in topologies)):
        filtered = _filter_topology(ordered, nodes, topology)
        if filtered is not ordered:
            filtered = filtered.reset_index(drop=True)
        filtered.attrs["full891_sorted_windows"] = True
        prepared[topology] = filtered
    return prepared


def _apply_ground_outage(
    windows: pd.DataFrame,
    duration_sec: float,
    percent: float,
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, float, float, int]:
    outage_duration = max(0.0, duration_sec * percent / 100.0)
    if outage_duration <= 0:
        return windows.copy(), 0.0, 0.0, 0
    start = float(rng.uniform(0.0, max(0.0, duration_sec - outage_duration)))
    end = start + outage_duration
    rows: list[dict[str, Any]] = []
    removed = 0
    for record in windows.to_dict(orient="records"):
        if not bool(record.get("is_sat_gs", False)):
            rows.append(record)
            continue
        a, b = float(record["start_time_sec"]), float(record["end_time_sec"])
        if b <= start or a >= end:
            rows.append(record)
            continue
        removed += 1
        original_duration = max(b - a, 1e-12)
        original_capacity = float(record.get("capacity_mbits", 0.0))
        segments = [(a, min(b, start), "pre"), (max(a, end), b, "post")]
        for seg_start, seg_end, suffix in segments:
            if seg_end <= seg_start:
                continue
            rec = dict(record)
            rec["start_time_sec"] = seg_start
            rec["end_time_sec"] = seg_end
            rec["duration_sec"] = seg_end - seg_start
            rec["capacity_mbits"] = original_capacity * (seg_end - seg_start) / original_duration
            if "window_capacity_mbits" in rec:
                rec["window_capacity_mbits"] = rec["capacity_mbits"]
            rec["window_id"] = f"{record['window_id']}_{suffix}"
            rows.append(rec)
    return pd.DataFrame(rows, columns=windows.columns), start, end, removed


def apply_scenario(
    scenario: Scenario,
    nodes: pd.DataFrame,
    windows: pd.DataFrame,
    opportunities: pd.DataFrame,
    duration_sec: float,
    seed: int,
    prepared_topology_windows: Mapping[str, pd.DataFrame] | None = None,
    materialize_opportunities: bool = True,
) -> FailureOutcome:
    rng = np.random.default_rng(seed)
    using_prepared = prepared_topology_windows is not None
    if prepared_topology_windows is not None:
        try:
            working = prepared_topology_windows[scenario.topology.lower()]
        except KeyError as exc:
            raise KeyError(f"Topology {scenario.topology!r} was not prepared") from exc
    else:
        # Preserve the public function's historical copy semantics for direct callers.
        working = _filter_topology(windows, nodes, scenario.topology).copy()
    original_count = len(working)
    sat_ids = sorted(nodes["node_id"].astype(str).tolist())
    cn_ids = sorted(nodes.loc[nodes["is_central_node"].astype(bool), "node_id"].astype(str).tolist())
    failed: set[str] = set()
    applicability = "applicable"

    if scenario.failure_type == "random_satellite_failure":
        failed = _choose(sat_ids, _positive_count(len(sat_ids), scenario.failure_level_percent), rng)
    elif scenario.failure_type == "random_cn_failure":
        if not cn_ids:
            applicability = "not_applicable_no_central_nodes"
        else:
            failed = _choose(cn_ids, _positive_count(len(cn_ids), scenario.failure_level_percent), rng)
    elif scenario.failure_type == "targeted_cn_failure":
        if not cn_ids:
            applicability = "not_applicable_no_central_nodes"
        else:
            failed = _targeted_central_nodes(
                nodes, working, _positive_count(len(cn_ids), scenario.failure_level_percent)
            )
    elif scenario.failure_type == "link_window_outage":
        count = _positive_count(len(working), scenario.failure_level_percent)
        if count:
            keep = np.ones(len(working), dtype=bool)
            removed_indices = np.atleast_1d(rng.choice(len(working), size=count, replace=False)).astype(int)
            keep[removed_indices] = False
            working = working.iloc[keep].copy()
    elif scenario.failure_type == "capacity_degradation":
        working = working.copy()
        factor = float(scenario.capacity_factor)
        for column in ["capacity_mbits", "window_capacity_mbits", "total_data_mbits"]:
            if column in working:
                working[column] = pd.to_numeric(working[column], errors="coerce").fillna(0.0) * factor
        for column in ["data_rate_bps", "mean_data_rate_bps"]:
            if column in working:
                working[column] = pd.to_numeric(working[column], errors="coerce").fillna(0.0) * factor
    elif scenario.failure_type not in {"none", "ground_station_outage"}:
        raise ValueError(f"Unknown failure type: {scenario.failure_type}")

    ground_start = ground_end = None
    ground_removed = 0
    if scenario.failure_type == "ground_station_outage":
        working, ground_start, ground_end, ground_removed = _apply_ground_outage(
            working, duration_sec, scenario.failure_level_percent, rng
        )

    output_opportunities = opportunities
    if failed:
        keep = ~working["node_i"].astype(str).isin(failed) & ~working["node_j"].astype(str).isin(failed)
        working = working.loc[keep].copy()
        if materialize_opportunities:
            if opportunities.empty or "node_id" not in opportunities:
                output_opportunities = opportunities.copy()
            else:
                output_opportunities = opportunities.loc[
                    ~opportunities["node_id"].astype(str).isin(failed)
                ].copy()
    elif materialize_opportunities:
        output_opportunities = opportunities.copy()

    if not working.empty and (
        not using_prepared or scenario.failure_type == "ground_station_outage"
    ):
        working = working.sort_values(["start_time_sec", "end_time_sec", "window_id"]).reset_index(drop=True)
    working.attrs["full891_sorted_windows"] = True
    return FailureOutcome(
        windows=working,
        opportunities=output_opportunities,
        failed_nodes=failed,
        removed_window_count=max(0, original_count - len(working)) + ground_removed,
        ground_outage_start_sec=ground_start,
        ground_outage_end_sec=ground_end,
        applicability=applicability,
    )
