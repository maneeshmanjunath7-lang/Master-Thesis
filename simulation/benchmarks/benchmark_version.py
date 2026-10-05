from __future__ import annotations

import hashlib
import json
import statistics
import time
from datetime import datetime, timezone

from full891_fresh.failures import apply_scenario
from full891_fresh.routing import simulate_dissemination
from full891_fresh.scenarios import Scenario

from routing_fixture import canonical_frame, fixture_inputs


def main() -> None:
    """Stable benchmark that can run against both v2.0 and v2.1 source trees."""

    nodes, tasks, opportunities, windows = fixture_inputs(
        node_count=60,
        task_count=773,
        cycles=6,
    )
    priority_sizes = {"Critical": 1.0, "High": 1.0, "Medium": 1.0, "Low": 1.0}
    scenario = Scenario(
        "india_synthetic_peer",
        "benchmark",
        topology="peer_only",
        routing_policy="peer",
    )
    elapsed: list[float] = []
    last_result = None
    for _ in range(3):
        failure = apply_scenario(scenario, nodes, windows, opportunities, 2400.0, 101)
        started = time.perf_counter()
        last_result = simulate_dissemination(
            "CASE",
            "IndiaSynthetic",
            nodes,
            tasks,
            opportunities,
            failure,
            scenario,
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            2400.0,
            priority_sizes,
            "GS_Munich",
            False,
        )
        elapsed.append(time.perf_counter() - started)

    assert last_result is not None
    canonical = json.dumps(
        {
            "case_metrics": last_result.case_metrics,
            "task_metrics": canonical_frame(last_result.task_metrics),
        },
        sort_keys=True,
        allow_nan=True,
        separators=(",", ":"),
    ).encode("utf-8")
    print(f"median_seconds={statistics.median(elapsed):.6f}")
    print(f"result_sha256={hashlib.sha256(canonical).hexdigest()}")


if __name__ == "__main__":
    main()
