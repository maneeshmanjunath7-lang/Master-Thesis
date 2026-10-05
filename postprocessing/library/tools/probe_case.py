"""Probe a representative case without writing outputs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from thesis_postprocessing.archive import CampaignArchive, event_label  # noqa: E402
from thesis_postprocessing.canonical import _task_outcomes_for_case  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("zip_path")
    parser.add_argument("--case-id", default="T060_P03_H0500_I0600_CN000_F1")
    args = parser.parse_args()
    with CampaignArchive(args.zip_path) as archive:
        for root in archive.event_roots:
            outcome, metrics, network, comparisons = _task_outcomes_for_case(
                archive, root, event_label(root), args.case_id
            )
            print(
                event_label(root),
                f"tasks={len(outcome)}",
                f"S100={metrics['full_dissemination_success_percent_s100']:.6f}",
                f"observation={metrics['observation_success_percent']:.6f}",
                f"transfer_mbits={network['transferred_data_mbits']:.6f}",
                f"max_summary_delta={max(row['absolute_difference'] for row in comparisons):.12g}",
                f"receiver_overflow={(outcome['destinations_reached_before_deadline'] > outcome['useful_recipients_total']).sum()}",
                f"denominator_mismatch={(outcome['derived_useful_recipients_total'] != outcome['useful_recipients_total']).sum()}",
                f"reached_mismatch={(outcome['derived_useful_recipients_reached_all_time'] != outcome['useful_recipients_reached']).sum()}",
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
