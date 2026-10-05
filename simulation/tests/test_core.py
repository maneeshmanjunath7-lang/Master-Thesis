from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from full891_fresh.config import CampaignConfig
from full891_fresh.failures import FailureOutcome, apply_scenario, prepare_topology_windows
from full891_fresh.routing import prepare_routing_context, simulate_dissemination
from full891_fresh.scenarios import Scenario, build_scenarios
from full891_fresh import legacy_simulator as legacy


ROOT = Path(__file__).resolve().parents[1]


def synthetic_inputs():
    nodes = pd.DataFrame([
        {"node_id": "A", "node_type": "observer", "is_central_node": False},
        {"node_id": "B", "node_type": "observer", "is_central_node": False},
    ])
    tasks = pd.DataFrame([{
        "task_id": "T1", "priority_class": "Medium", "created_time_sec": 0.0,
        "deadline_time_sec": 10.0,
    }])
    opportunities = pd.DataFrame([
        {"task_id": "T1", "node_id": "A", "earliest_observation_time_sec": 5.0},
        {"task_id": "T1", "node_id": "B", "earliest_observation_time_sec": 5.0},
    ])
    windows = pd.DataFrame([
        {
            "window_id": "W_GS_A", "node_i": "GS_Munich", "node_j": "A",
            "start_time_sec": 0.0, "end_time_sec": 5.0, "duration_sec": 5.0,
            "data_rate_bps": 1_000_000.0, "capacity_mbits": 5.0,
            "is_sat_gs": True, "link_type": "sat_gs",
        },
        {
            "window_id": "W_A_B", "node_i": "A", "node_j": "B",
            "start_time_sec": 20.0, "end_time_sec": 25.0, "duration_sec": 5.0,
            "data_rate_bps": 1_000_000.0, "capacity_mbits": 5.0,
            "is_sat_gs": False, "link_type": "sat_sat",
        },
    ])
    return nodes, tasks, opportunities, windows


class GridAndScenarioTests(unittest.TestCase):
    def setUp(self):
        self.config = CampaignConfig.load(ROOT / "config" / "full_campaign.json")

    def test_frozen_grid(self):
        self.assertEqual(len(list(self.config.iter_base_architectures())), 81)
        self.assertEqual(self.config.case_count_per_region, 891)

    def test_complete_scenario_plan(self):
        scenarios = build_scenarios(self.config.data)
        self.assertEqual(len(scenarios), 539)
        ids = {scenario.scenario_id for scenario in scenarios}
        self.assertIn("nominal_original", ids)
        self.assertIn("unlimited_useful_sim_end", ids)
        self.assertIn("policy_peer", ids)
        self.assertIn("task_size_x4p0", ids)
        self.assertIn("random_satellite_failure_p30_r29", ids)


class RoutingTests(unittest.TestCase):
    def test_t100_after_deadline_is_retained_and_censored_at_deadline(self):
        nodes, tasks, opportunities, windows = synthetic_inputs()
        scenario = Scenario(
            "test", "test", topology="hybrid", routing_policy="peer",
            receiver_target="all_useful", delivery_cutoff="simulation_end",
        )
        failure = FailureOutcome(windows, opportunities, set(), 0)
        result = simulate_dissemination(
            "CASE", "Test", nodes, tasks, opportunities, failure, scenario,
            datetime(2026, 1, 1, tzinfo=timezone.utc), 30.0,
            {"Medium": 1.0}, "GS_Munich", True,
        )
        task = result.task_metrics.iloc[0]
        self.assertFalse(bool(task["s100_surviving_before_deadline"]))
        self.assertTrue(bool(task["s100_surviving_by_sim_end"]))
        self.assertAlmostEqual(float(task["t100_surviving_min"]), 21.0 / 60.0)
        self.assertAlmostEqual(result.case_metrics["p100_t100_surviving_min"], 21.0 / 60.0)
        self.assertEqual(len(result.transfers), 2)

    def test_original_and_surviving_denominators_differ_after_failure(self):
        nodes, tasks, opportunities, windows = synthetic_inputs()
        surviving_windows = windows.loc[~windows["node_i"].eq("B") & ~windows["node_j"].eq("B")]
        surviving_opps = opportunities.loc[opportunities["node_id"] != "B"]
        failure = FailureOutcome(surviving_windows, surviving_opps, {"B"}, 1)
        scenario = Scenario("failure", "test", topology="hybrid", routing_policy="peer")
        result = simulate_dissemination(
            "CASE", "Test", nodes, tasks, opportunities, failure, scenario,
            datetime(2026, 1, 1, tzinfo=timezone.utc), 30.0,
            {"Medium": 1.0}, "GS_Munich", False,
        )
        task = result.task_metrics.iloc[0]
        self.assertEqual(task["original_target_count"], 2)
        self.assertEqual(task["surviving_target_count"], 1)
        self.assertEqual(task["original_coverage_deadline_percent"], 50.0)
        self.assertEqual(task["surviving_coverage_deadline_percent"], 100.0)

    def test_deadline_cutoff_prevents_late_transfer(self):
        nodes, tasks, opportunities, windows = synthetic_inputs()
        scenario = Scenario(
            "deadline", "test", topology="hybrid", routing_policy="peer",
            delivery_cutoff="deadline",
        )
        failure = FailureOutcome(windows, opportunities, set(), 0)
        result = simulate_dissemination(
            "CASE", "Test", nodes, tasks, opportunities, failure, scenario,
            datetime(2026, 1, 1, tzinfo=timezone.utc), 30.0,
            {"Medium": 1.0}, "GS_Munich", False,
        )
        self.assertFalse(bool(result.task_metrics.iloc[0]["s100_surviving_by_sim_end"]))

    def test_cached_runner_path_matches_compatibility_path(self):
        nodes, tasks, opportunities, windows = synthetic_inputs()
        scenario = Scenario(
            "failure", "test", topology="hybrid", routing_policy="peer",
            failure_type="random_satellite_failure", failure_level_percent=25,
        )
        baseline_failure = apply_scenario(scenario, nodes, windows, opportunities, 30.0, 7)
        baseline = simulate_dissemination(
            "CASE", "Test", nodes, tasks, opportunities, baseline_failure, scenario,
            datetime(2026, 1, 1, tzinfo=timezone.utc), 30.0,
            {"Medium": 1.0}, "GS_Munich", True,
        )

        context = prepare_routing_context(
            nodes, tasks, opportunities, {"Medium": 1.0}, "GS_Munich"
        )
        prepared = prepare_topology_windows(windows, nodes, [scenario.topology])
        cached_failure = apply_scenario(
            scenario, nodes, windows, opportunities, 30.0, 7,
            prepared_topology_windows=prepared,
            materialize_opportunities=False,
        )
        cached = simulate_dissemination(
            "CASE", "Test", nodes, tasks, opportunities, cached_failure, scenario,
            datetime(2026, 1, 1, tzinfo=timezone.utc), 30.0,
            {"Medium": 1.0}, "GS_Munich", True, context,
        )

        pd.testing.assert_frame_equal(baseline.task_metrics, cached.task_metrics)
        pd.testing.assert_frame_equal(baseline.transfers, cached.transfers)
        pd.testing.assert_series_equal(
            pd.Series(baseline.case_metrics).sort_index(),
            pd.Series(cached.case_metrics).sort_index(),
        )


class FailureTests(unittest.TestCase):
    def test_capacity_degradation_scales_rate_and_capacity(self):
        nodes, _, opportunities, windows = synthetic_inputs()
        scenario = Scenario(
            "cap", "capacity_degradation", topology="hybrid",
            failure_type="capacity_degradation", failure_level_percent=20,
            capacity_factor=0.8,
        )
        outcome = apply_scenario(scenario, nodes, windows, opportunities, 30.0, 1)
        self.assertTrue(np.allclose(outcome.windows["data_rate_bps"], 800_000.0))
        self.assertTrue(np.allclose(outcome.windows["capacity_mbits"], 4.0))

    def test_central_only_removes_observer_peer_window(self):
        nodes, _, opportunities, windows = synthetic_inputs()
        scenario = Scenario("central", "test", topology="central_only")
        outcome = apply_scenario(scenario, nodes, windows, opportunities, 30.0, 1)
        self.assertEqual(outcome.windows["window_id"].tolist(), ["W_GS_A"])


class PhysicalCoreInterfaceTests(unittest.TestCase):
    def test_fresh_geometry_contact_and_observation_interfaces(self):
        epoch = datetime(2026, 1, 1, tzinfo=timezone.utc)
        config = legacy.ArchitectureConfig(
            n_satellites=6, n_planes=1, altitude_km=500,
            inclination_deg=60, cn_fraction_percent=50,
        )
        _, nodes, _, validation = legacy.generate_walker_constellation(config)
        self.assertTrue(bool(validation["passed"].all()))
        time_seconds = np.array([0.0, 60.0, 120.0])
        positions, lat, lon = legacy.precompute_satellite_positions(nodes, time_seconds)
        windows = legacy.generate_communication_windows(
            config, nodes, legacy.default_ground_stations("munich_only"), epoch,
            duration_hours=120.0 / 3600.0, time_step_sec=60,
            sat_sat_max_range_km=0.0, sat_sat_data_rate_bps=250_000.0,
            sat_gs_data_rate_bps=100_000.0, sat_sat_mode="all",
            positions_eci=positions, time_seconds=time_seconds,
        )
        tasks = pd.DataFrame([{
            "task_id": "MICRO", "latitude": 0.0, "longitude": 0.0,
            "timestamp_utc": legacy.iso_utc(epoch),
            "deadline_time_utc": legacy.iso_utc(epoch.replace(minute=2)),
            "created_time_sec": 0.0, "deadline_time_sec": 120.0,
            "priority_class": "Medium",
        }])
        opportunities = legacy.generate_observation_opportunities(
            config, nodes, tasks, epoch, 120.0 / 3600.0, 60, 700.0,
            subpoint_lat=lat, subpoint_lon=lon, time_seconds=time_seconds,
        )
        self.assertIsInstance(windows, pd.DataFrame)
        self.assertIsInstance(opportunities, pd.DataFrame)


if __name__ == "__main__":
    unittest.main()
