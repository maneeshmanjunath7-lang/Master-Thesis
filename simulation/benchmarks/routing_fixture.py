from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from full891_fresh.failures import apply_scenario
from full891_fresh.routing import simulate_dissemination
from full891_fresh.scenarios import Scenario


def fixture_inputs(
    node_count: int = 12,
    task_count: int = 40,
    cycles: int = 18,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    nodes = pd.DataFrame(
        [
            {
                "node_id": f"S{index:02d}",
                "node_type": "central_node" if index < 3 else "observer",
                "is_central_node": index < 3,
            }
            for index in range(node_count)
        ]
    )
    tasks = pd.DataFrame(
        [
            {
                "task_id": f"T{index:03d}",
                "priority_class": ["Critical", "High", "Medium", "Low"][index % 4],
                "created_time_sec": float(index * 7),
                "deadline_time_sec": float(1800 + index * 7),
            }
            for index in range(task_count)
        ]
    )
    opportunities = pd.DataFrame(
        [
            {
                "task_id": task_id,
                "node_id": node_id,
                "earliest_observation_time_sec": 900.0 + node_index * 10.0,
            }
            for task_id in tasks["task_id"]
            for node_index, node_id in enumerate(nodes["node_id"])
        ]
    )
    windows: list[dict[str, object]] = []
    window_id = 0
    for cycle in range(cycles):
        start = float(cycle * 120)
        for node_index, node_id in enumerate(nodes["node_id"]):
            windows.append(
                {
                    "window_id": f"W{window_id:05d}",
                    "node_i": "GS_Munich",
                    "node_j": node_id,
                    "start_time_sec": start,
                    "end_time_sec": start + 45.0,
                    "duration_sec": 45.0,
                    "data_rate_bps": 1_000_000.0,
                    "capacity_mbits": 45.0,
                    "is_sat_gs": True,
                }
            )
            window_id += 1
            peer_id = nodes.iloc[(node_index + 1) % len(nodes)]["node_id"]
            windows.append(
                {
                    "window_id": f"W{window_id:05d}",
                    "node_i": node_id,
                    "node_j": peer_id,
                    "start_time_sec": start + 50.0,
                    "end_time_sec": start + 110.0,
                    "duration_sec": 60.0,
                    "data_rate_bps": 1_000_000.0,
                    "capacity_mbits": 60.0,
                    "is_sat_gs": False,
                }
            )
            window_id += 1
    return nodes, tasks, opportunities, pd.DataFrame(windows)


def canonical_frame(frame: pd.DataFrame) -> list[dict[str, object]]:
    return json.loads(frame.to_json(orient="records", double_precision=12))


def main() -> None:
    nodes, tasks, opportunities, windows = fixture_inputs()
    priority_sizes = {"Critical": 1.0, "High": 1.0, "Medium": 1.0, "Low": 1.0}
    scenarios = [
        Scenario("nominal", "test", topology="central_only", routing_policy="targeted_central"),
        Scenario("peer", "test", topology="peer_only", routing_policy="peer"),
        Scenario(
            "sat_failure", "test", topology="hybrid", routing_policy="hybrid",
            failure_type="random_satellite_failure", failure_level_percent=20,
        ),
        Scenario(
            "capacity", "test", topology="hybrid", routing_policy="hybrid",
            failure_type="capacity_degradation", failure_level_percent=20,
            capacity_factor=0.8,
        ),
        Scenario(
            "link_outage", "test", topology="hybrid", routing_policy="hybrid",
            failure_type="link_window_outage", failure_level_percent=10,
        ),
    ]
    output: dict[str, object] = {}
    for scenario in scenarios:
        failure = apply_scenario(scenario, nodes, windows, opportunities, 2400.0, 101)
        result = simulate_dissemination(
            "CASE", "Fixture", nodes, tasks, opportunities, failure, scenario,
            datetime(2026, 1, 1, tzinfo=timezone.utc), 2400.0,
            priority_sizes, "GS_Munich", True,
        )
        output[scenario.scenario_id] = {
            "case_metrics": result.case_metrics,
            "task_metrics": canonical_frame(result.task_metrics),
            "transfers": canonical_frame(result.transfers),
        }
    print(json.dumps(output, sort_keys=True, allow_nan=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
