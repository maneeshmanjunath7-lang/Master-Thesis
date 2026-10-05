from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))

from postprocess_complete import (  # noqa: E402
    EXPECTED_CASES_PER_REGION,
    RegionBundle,
    completion_gate,
    cross_region_analysis,
    followup_satellite_plan,
    high_cn_diagnosis,
    km_curve,
    metric_correlations,
    pareto_and_weights,
)


class CompletePostprocessingTests(unittest.TestCase):
    def test_exact_completion_gate(self) -> None:
        audit = pd.DataFrame(
            {
                "case_id": [f"case_{index}" for index in range(EXPECTED_CASES_PER_REGION)],
                "unique_scenario_rows": 539,
                "cn_fraction_percent": np.tile(np.arange(0, 101, 10), 81),
                "success_marker": True,
            }
        )
        integrity = {
            "case_directories": 891,
            "base_architecture_directories": 81,
            "success_markers": 891,
            "failed_markers": 0,
            "failed_checks": 0,
            "unique_scenario_rows_all_cases": 480249,
            "failed_status_rows_success_cases": 0,
            "conflicting_duplicate_keys": 0,
            "balanced_base_architectures": 81,
            "balanced_complete_cases": 891,
        }
        bundle = RegionBundle("California", Path("x"), Path("x"), Path("x"), pd.DataFrame(), audit, integrity)
        table, passed = completion_gate(bundle)
        self.assertTrue(passed)
        self.assertTrue(table["passed"].all())

    def test_metric_redundancy_flags_duplicate_resource_metrics(self) -> None:
        frame = pd.DataFrame(
            {
                "scenario_id": "unlimited_useful_deadline",
                "total_transferred_data_mbits": [1, 2, 3, 4],
                "n_transfer_events": [10, 20, 30, 40],
                "observation_success_percent": [80, 60, 90, 70],
            }
        )
        _, pairs = metric_correlations(frame, 0.95)
        row = pairs.loc[
            pairs.apply(
                lambda value: {value["metric_a"], value["metric_b"]}
                == {"total_transferred_data_mbits", "n_transfer_events"},
                axis=1,
            )
        ].iloc[0]
        self.assertTrue(bool(row["severe_redundancy_0p98"]))

    def test_high_cn_decline_is_paired_and_detected(self) -> None:
        deltas = pd.DataFrame(
            {
                "region": ["California", "California"],
                "scenario_id": ["nominal_original", "nominal_original"],
                "base_id": ["A", "B"],
                "case_id_from": ["A80", "B80"],
                "case_id_to": ["A90", "B90"],
                "cn_from_percent": [80, 80],
                "cn_to_percent": [90, 90],
                "delta_observation_success_percent": [-2.0, 1.0],
                "delta_n_transfer_events": [20, 10],
            }
        )
        summary, details = high_cn_diagnosis(deltas, draws=50, seed=7)
        self.assertAlmostEqual(float(summary.iloc[0]["share_with_observation_drop_percent"]), 50.0)
        self.assertEqual(len(details), 2)

    def test_km_and_rmst_include_censored_task(self) -> None:
        _, probability, rmst = km_curve(np.array([1.0, 2.0]), np.array([1.0, 0.0]), 2.0)
        self.assertAlmostEqual(probability, 0.5)
        self.assertAlmostEqual(rmst, 1.5)

    def test_followup_counts_respect_plane_divisibility(self) -> None:
        shortlist = pd.DataFrame(
            [
                {
                    "case_id": "T180_P03_H0500_I0600_CN050_F1",
                    "n_planes": 3,
                    "altitude_km": 500,
                    "inclination_deg": 60,
                    "cn_fraction_percent": 50,
                    "walker_f": 1,
                }
            ]
        )
        plan = followup_satellite_plan(shortlist)
        self.assertEqual(plan["n_satellites_to_simulate"].tolist(), [501, 999])

    def test_cross_region_deltas_and_weight_acceptability(self) -> None:
        rows = []
        for region, shift in [("California", 0.0), ("India", -3.0)]:
            for index in range(3):
                rows.append(
                    {
                        "region": region,
                        "case_id": f"T0{60 + 60 * index}_P03_H0500_I0600_CN050_F1",
                        "base_id": f"B{index}",
                        "scenario_id": "unlimited_useful_deadline",
                        "n_satellites": 60 + 60 * index,
                        "n_planes": 3,
                        "altitude_km": 500,
                        "inclination_deg": 60,
                        "cn_fraction_percent": 50,
                        "n_central_nodes": 30 + 30 * index,
                        "observation_success_percent": 90 - index + shift,
                        "s100_surviving_before_deadline_percent": 85 - index + shift,
                        "mean_surviving_coverage_deadline_percent": 92 - index + shift,
                        "p100_t100_surviving_min": 20 + index,
                        "total_transferred_data_mbits": 100 + 10 * index,
                        "robustness_auc_retention": 0.9 - 0.05 * index,
                    }
                )
        frame = pd.DataFrame(rows)
        details, summary, ranks = cross_region_analysis(frame, draws=50, seed=8)
        self.assertEqual(len(details), 3)
        obs = summary.loc[summary["metric"] == "observation_success_percent"].iloc[0]
        self.assertAlmostEqual(float(obs["mean_delta_b_minus_a"]), -3.0)
        self.assertAlmostEqual(float(ranks.iloc[0]["spearman_rank_agreement"]), 1.0)
        pareto, winners, acceptability = pareto_and_weights(frame, samples=100, seed=9)
        self.assertFalse(pareto.empty)
        self.assertEqual(len(winners), 200)
        self.assertEqual(len(acceptability), 6)


if __name__ == "__main__":
    unittest.main()
