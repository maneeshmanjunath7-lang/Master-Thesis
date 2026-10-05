from __future__ import annotations

import time
from datetime import datetime, timezone

import pandas as pd

from full891_fresh.failures import apply_scenario, prepare_topology_windows
from full891_fresh.routing import prepare_routing_context, simulate_dissemination
from full891_fresh.scenarios import Scenario

from routing_fixture import fixture_inputs


def run(optimized: bool, repetitions: int) -> float:
    # Match the India task count and a 60-satellite architecture while keeping
    # the synthetic contact schedule short enough for a repeatable local test.
    nodes, tasks, opportunities, windows = fixture_inputs(
        node_count=60, task_count=773, cycles=6
    )
    sizes = {"Critical": 1.0, "High": 1.0, "Medium": 1.0, "Low": 1.0}
    scenario = Scenario(
        "benchmark", "benchmark", topology="hybrid", routing_policy="hybrid",
        failure_type="random_satellite_failure", failure_level_percent=20,
    )
    context = prepare_routing_context(nodes, tasks, opportunities, sizes, "GS_Munich") if optimized else None
    prepared = prepare_topology_windows(windows, nodes, [scenario.topology]) if optimized else None
    started = time.perf_counter()
    results = []
    for replicate in range(repetitions):
        failure = apply_scenario(
            scenario, nodes, windows, opportunities, 2400.0, replicate,
            prepared_topology_windows=prepared,
            materialize_opportunities=not optimized,
        )
        results.append(
            simulate_dissemination(
                "CASE", "Fixture", nodes, tasks, opportunities, failure, scenario,
                datetime(2026, 1, 1, tzinfo=timezone.utc), 2400.0,
                sizes, "GS_Munich", False, context,
            )
        )
    elapsed = time.perf_counter() - started
    assert all(isinstance(result.task_metrics, pd.DataFrame) for result in results)
    return elapsed


def main() -> None:
    repetitions = 3
    compatibility_seconds = run(False, repetitions)
    optimized_seconds = run(True, repetitions)
    speedup = compatibility_seconds / optimized_seconds
    print(f"repetitions={repetitions}")
    print(f"compatibility_seconds={compatibility_seconds:.6f}")
    print(f"optimized_seconds={optimized_seconds:.6f}")
    print(f"routing_cache_speedup={speedup:.3f}x")


if __name__ == "__main__":
    main()
