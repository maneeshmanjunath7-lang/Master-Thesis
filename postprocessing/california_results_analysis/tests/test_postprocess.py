from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
import json
from pathlib import Path

import pandas as pd

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from postprocess_california import (
    _safe_extract,
    conflicting_duplicate_keys,
    exact_pareto,
    parse_case_id,
    run,
)


class PostprocessingTests(unittest.TestCase):
    def test_case_id_parser(self) -> None:
        parsed = parse_case_id("T060_P03_H0500_I0986_CN030_F1")
        self.assertEqual(parsed["n_satellites"], 60)
        self.assertEqual(parsed["n_planes"], 3)
        self.assertEqual(parsed["altitude_km"], 500.0)
        self.assertEqual(parsed["inclination_deg"], 98.6)
        self.assertEqual(parsed["cn_fraction_percent"], 30.0)
        scaled = parse_case_id("T1000_P10_H0500_I0986_CN030_F1")
        self.assertEqual(scaled["n_satellites"], 1000)

    def test_pareto_keeps_only_nondominated_rows(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "case_id": "A",
                    "scenario_id": "unlimited_useful_deadline",
                    "observation_success_percent": 100.0,
                    "s100_surviving_before_deadline_percent": 100.0,
                    "mean_surviving_coverage_deadline_percent": 100.0,
                    "n_satellites": 60,
                    "n_central_nodes": 6,
                    "total_transferred_data_mbits": 10.0,
                },
                {
                    "case_id": "B",
                    "scenario_id": "unlimited_useful_deadline",
                    "observation_success_percent": 90.0,
                    "s100_surviving_before_deadline_percent": 90.0,
                    "mean_surviving_coverage_deadline_percent": 90.0,
                    "n_satellites": 120,
                    "n_central_nodes": 12,
                    "total_transferred_data_mbits": 20.0,
                },
            ]
        )
        result = exact_pareto(frame).set_index("case_id")
        self.assertTrue(bool(result.at["A", "is_exact_pareto"]))
        self.assertFalse(bool(result.at["B", "is_exact_pareto"]))

    def test_zip_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("../outside.txt", "unsafe")
            with self.assertRaises(ValueError):
                _safe_extract(archive, root / "extract")

    def test_conflicting_duplicate_payload_is_reported(self) -> None:
        frame = pd.DataFrame(
            [
                {"region": "California", "case_id": "A", "scenario_id": "nominal", "score": 1.0},
                {"region": "California", "case_id": "A", "scenario_id": "nominal", "score": 2.0},
                {"region": "California", "case_id": "B", "scenario_id": "nominal", "score": 3.0},
                {"region": "California", "case_id": "B", "scenario_id": "nominal", "score": 3.0},
            ]
        )
        result = conflicting_duplicate_keys(frame)
        self.assertEqual(result["case_id"].tolist(), ["A"])

    def test_end_to_end_fixture_creates_all_figures(self) -> None:
        scenarios = [
            ("nominal_original", "baseline", 1.0),
            ("unlimited_useful_deadline", "baseline", 1.0),
            ("unlimited_useful_sim_end", "baseline", 1.0),
            ("policy_central", "policy_ablation", 1.0),
            ("policy_peer", "policy_ablation", 1.0),
            ("policy_hybrid", "policy_ablation", 1.0),
            ("task_size_x0p5", "task_size", 0.5),
            ("task_size_x1p0", "task_size", 1.0),
            ("task_size_x2p0", "task_size", 2.0),
            ("task_size_x4p0", "task_size", 4.0),
            ("random_satellite_failure_10_seed00", "random_satellite_failure", 1.0),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            region_root = temporary_root / "raw" / "California"
            for cn_fraction in range(0, 101, 10):
                case_id = f"T060_P03_H0500_I0986_CN{cn_fraction:03d}_F1"
                case = region_root / "T060_P03_H0500_I0986_F1" / case_id
                case.mkdir(parents=True)
                rows = []
                task_rows = []
                for scenario_id, family, multiplier in scenarios:
                    performance = min(100.0, 70.0 + 0.2 * cn_fraction)
                    if family == "random_satellite_failure":
                        performance -= 5.0
                    rows.append(
                        {
                            "case_id": case_id,
                            "region": "California",
                            "scenario_id": scenario_id,
                            "family": family,
                            "status": "COMPLETED",
                            "topology": "hybrid",
                            "routing_policy": "hybrid",
                            "receiver_target": "all_useful",
                            "delivery_cutoff": "deadline",
                            "failure_type": family if family == "random_satellite_failure" else "none",
                            "failure_level_percent": 10.0 if family == "random_satellite_failure" else 0.0,
                            "task_size_multiplier": multiplier,
                            "replicate": 0,
                            "n_satellites": 60,
                            "n_planes": 3,
                            "altitude_km": 500.0,
                            "inclination_deg": 98.6,
                            "cn_fraction_percent": float(cn_fraction),
                            "n_central_nodes": round(60 * cn_fraction / 100),
                            "observation_success_percent": performance,
                            "s100_surviving_before_deadline_percent": performance - 5.0,
                            "mean_surviving_coverage_deadline_percent": performance - 2.0,
                            "p100_t100_surviving_min": 40.0 - 0.1 * cn_fraction,
                            "total_transferred_data_mbits": 100.0 + cn_fraction,
                            "n_transfer_events": 50 + cn_fraction,
                            "used_capacity_percent": 20.0,
                            "peak_node_sent_data_mbits": 10.0,
                            "node_sent_data_gini": 0.2,
                            **{
                                f"n_tasks_{priority}": count
                                for priority, count in {
                                    "critical": 1,
                                    "high": 6,
                                    "medium": 16,
                                    "low": 9,
                                }.items()
                            },
                            **{
                                f"{priority}_{metric}": performance
                                for priority in ("critical", "high", "medium", "low")
                                for metric in (
                                    "observation_success_percent",
                                    "s100_surviving_before_deadline_percent",
                                )
                            },
                        }
                    )
                    task_rows.append(
                        {
                            "case_id": case_id,
                            "scenario_id": scenario_id,
                            "family": family,
                            "task_id": "TASK001",
                            "priority_class": "Critical",
                            "task_size_mbits": multiplier,
                            "observed_before_deadline": True,
                            "observation_latency_min": 5.0,
                            "failure_class": "success",
                            "s100_surviving_before_deadline": True,
                            "surviving_coverage_deadline_percent": 100.0,
                            "t100_surviving_min": 10.0,
                            "t100_surviving_censored": False,
                        }
                    )
                pd.DataFrame(rows).to_parquet(case / "case_metrics_part_0000.parquet", index=False)
                pd.DataFrame(task_rows).to_parquet(case / "task_metrics_part_0000.parquet", index=False)
                pd.DataFrame([{"check": "fixture", "passed": True}]).to_parquet(
                    case / "architecture_validation.parquet", index=False
                )
                (case / "case_manifest.json").write_text(
                    json.dumps(
                        {
                            "n_tasks": 32,
                            "duration_hours": 24,
                            "epoch_utc": "2026-01-01T00:00:00Z",
                            "n_windows_generated": 100,
                            "n_observation_opportunities": 32,
                            "configuration_fingerprint": "fixture",
                        }
                    ),
                    encoding="utf-8",
                )
                (case / "_SUCCESS.json").write_text('{"status":"COMPLETED"}', encoding="utf-8")

            output = temporary_root / "analysis"
            summary = run(temporary_root / "raw", output, temporary_root / "work")
            self.assertEqual(summary["figures_created"], 9)
            self.assertTrue(summary["task_level_summary_created"])
            self.assertTrue((output / "tables" / "unlimited_exact_pareto_cases.csv").exists())
            self.assertTrue((output / "postprocessing_run_summary.json").exists())


if __name__ == "__main__":
    unittest.main()
