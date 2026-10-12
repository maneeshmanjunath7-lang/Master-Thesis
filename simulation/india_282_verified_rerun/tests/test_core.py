from __future__ import annotations

import unittest
import tempfile
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from full891_fresh.config import CampaignConfig
from full891_fresh.failures import FailureOutcome, apply_scenario, prepare_topology_windows
from full891_fresh.routing import prepare_routing_context, simulate_dissemination
from full891_fresh.scenarios import Scenario, build_scenarios
from full891_fresh.storage import verified_scenario_ids
from full891_fresh import legacy_simulator as legacy
from full891_fresh.runner import (
    _event_tasks,
    _validated_resume_scenario_ids,
    assert_output_root_compatible,
    build_manifest,
    run_campaign,
    validate_campaign_contract,
)
from tools.verify_rerun import _task_partition_failures
from tools.verify_package import is_excluded as package_path_is_excluded


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
        self.config = CampaignConfig.load(ROOT / "config" / "india_282_rerun.json")

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

    def test_repair_manifest_is_exactly_282_india_cases(self):
        cases, scenarios = build_manifest(self.config, "full")
        self.assertEqual(len(cases), 282)
        self.assertEqual(len(scenarios), 539)
        self.assertEqual(set(cases["region"]), {"India"})
        self.assertEqual(set(cases["n_satellites"]), {180})
        self.assertEqual(cases["case_id"].nunique(), 282)

    def test_frozen_task_and_allowlist_contracts(self):
        contract = validate_campaign_contract(self.config)
        self.assertEqual(contract["task_counts"], {"California": 32, "India": 773})
        self.assertEqual(
            contract["task_input_sha256"],
            "e717a1dde84db0f47efdda09cfca2f437909349b25a139a45a7c1928da80e533",
        )
        self.assertEqual(contract["selected_case_count"], 282)

    def test_regional_filter_resets_index_and_keeps_all_india_tasks(self):
        epoch = datetime(2026, 4, 14, tzinfo=timezone.utc)
        rows = []
        for index, region in enumerate(["California", "California", "India", "India", "India"]):
            rows.append({
                "task_id": f"TASK_{index}",
                "region": region,
                "centroid_lat": 10.0 + index,
                "centroid_lon": 70.0 + index,
                "priority_class": "Low",
                "required_response_time_min": 360,
                "timestamp_utc": legacy.iso_utc(epoch),
            })
        with tempfile.TemporaryDirectory() as temporary:
            task_path = Path(temporary) / "tasks.csv"
            pd.DataFrame(rows).to_csv(task_path, index=False)
            config = SimpleNamespace(
                tasks_path=task_path,
                data={
                    "simulation": {"minimum_duration_hours": 72.0, "end_buffer_hours": 6.0},
                    "execution": {"expected_task_counts": {"India": 3}},
                },
            )
            tasks, _, _ = _event_tasks(config, "India")
        self.assertEqual(len(tasks), 3)
        self.assertEqual(tasks["task_id"].tolist(), ["TASK_2", "TASK_3", "TASK_4"])

    def test_original_campaign_output_name_is_protected(self):
        with tempfile.TemporaryDirectory() as temporary:
            protected = Path(temporary) / "full891_fresh_complete"
            with self.assertRaisesRegex(ValueError, "protected output root"):
                run_campaign(
                    ROOT / "config" / "india_282_rerun.json",
                    profile="full",
                    output_override=protected,
                )

    def test_741_task_case_manifest_is_rejected_before_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            case_dir = Path(temporary) / "T180_P03_H0500_I0600_CN030_F1"
            case_dir.mkdir()
            (case_dir / "case_manifest.json").write_text(
                json.dumps({
                    "n_tasks": 741,
                    "normalized_task_ids_sha256": "old",
                    "normalized_tasks_sha256": "old",
                    "run_contract_sha256": "old",
                    "scenario_ids_sha256": "old",
                    "expected_scenario_count": 539,
                }),
                encoding="utf-8",
            )
            expected = {
                "n_tasks": 773,
                "normalized_task_ids_sha256": "new-ids",
                "normalized_tasks_sha256": "new-tasks",
                "run_contract_sha256": "new-contract",
                "scenario_ids_sha256": "new-scenarios",
                "expected_scenario_count": 539,
            }
            with self.assertRaisesRegex(RuntimeError, "INCOMPATIBLE RESUME DATA"):
                _validated_resume_scenario_ids(
                    case_dir,
                    case_dir.name,
                    expected,
                    {"nominal_original"},
                    True,
                )

    def test_old_raw_hash_only_provenance_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary) / "old_output"
            output_root.mkdir()
            (output_root / "provenance.json").write_text(
                json.dumps({"task_input_sha256": "same-raw-csv-hash"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "INCOMPATIBLE RESUME DATA"):
                assert_output_root_compatible(
                    output_root,
                    {"run_contract_sha256": "new-contract"},
                )


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


class CheckpointIntegrityTests(unittest.TestCase):
    def test_verified_checkpoint_partition(self):
        with tempfile.TemporaryDirectory() as temporary:
            case_dir = Path(temporary)
            pd.DataFrame([
                {"case_id": "CASE", "scenario_id": "S1", "status": "COMPLETED"},
                {"case_id": "CASE", "scenario_id": "S2", "status": "NOT_APPLICABLE"},
            ]).to_parquet(case_dir / "case_metrics_part_0000.parquet", index=False)
            self.assertEqual(verified_scenario_ids(case_dir, "CASE"), {"S1", "S2"})

    def test_duplicate_checkpoint_scenario_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            case_dir = Path(temporary)
            for part in (0, 1):
                pd.DataFrame([{
                    "case_id": "CASE", "scenario_id": "S1", "status": "COMPLETED",
                }]).to_parquet(case_dir / f"case_metrics_part_{part:04d}.parquet", index=False)
            with self.assertRaisesRegex(RuntimeError, "more than one checkpoint"):
                verified_scenario_ids(case_dir, "CASE")

    def test_missing_task_metric_scenario_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            case_dir = Path(temporary)
            task_ids = {f"TASK_{index:04d}" for index in range(773)}
            pd.DataFrame([
                {"case_id": "CASE", "scenario_id": "S1", "task_id": task_id}
                for task_id in sorted(task_ids)
            ]).to_parquet(case_dir / "task_metrics_part_0000.parquet", index=False)
            failures = _task_partition_failures(
                case_dir, "CASE", task_ids, {"S1", "S2"}
            )
            self.assertTrue(any("missing task-metric scenarios" in item for item in failures))

    def test_duplicate_task_metric_scenario_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            case_dir = Path(temporary)
            task_ids = {f"TASK_{index:04d}" for index in range(773)}
            rows = [
                {"case_id": "CASE", "scenario_id": "S1", "task_id": task_id}
                for task_id in sorted(task_ids)
            ]
            pd.DataFrame(rows).to_parquet(
                case_dir / "task_metrics_part_0000.parquet", index=False
            )
            pd.DataFrame(rows).to_parquet(
                case_dir / "task_metrics_part_0001.parquet", index=False
            )
            failures = _task_partition_failures(
                case_dir, "CASE", task_ids, {"S1"}
            )
            self.assertTrue(any("more than one partition" in item for item in failures))


class PackageIntegrityTests(unittest.TestCase):
    def test_generated_editable_install_metadata_is_excluded(self):
        self.assertTrue(
            package_path_is_excluded(
                Path("src/full891_fresh_simulation.egg-info/PKG-INFO")
            )
        )

    def test_generated_background_pid_is_excluded(self):
        self.assertTrue(package_path_is_excluded(Path(".india_282_vm.pid")))

    def test_source_package_is_not_excluded(self):
        self.assertFalse(package_path_is_excluded(Path("src/full891_fresh/runner.py")))

    def test_launcher_stores_pid_under_logs(self):
        launcher = (ROOT / "START_282_BACKGROUND_VM.sh").read_text(encoding="utf-8")
        status = (ROOT / "STATUS_282_VM.sh").read_text(encoding="utf-8")
        self.assertIn('PID_FILE="$ROOT/logs/india_282_vm.pid"', launcher)
        self.assertIn('PID_FILE="$ROOT/logs/india_282_vm.pid"', status)


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
