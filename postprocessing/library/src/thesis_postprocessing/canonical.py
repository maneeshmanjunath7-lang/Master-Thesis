"""Canonical task/case tables and Gate C metric reconstruction."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from pandas.errors import EmptyDataError

from .archive import CampaignArchive
from .validation import ValidationResult


@dataclass
class CanonicalResult:
    tasks: pd.DataFrame
    case_metrics: pd.DataFrame
    network_metrics: pd.DataFrame
    metric_comparisons: pd.DataFrame
    qc_checks: pd.DataFrame
    gate_c_pass: bool


def _bool_series(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.lower().map({"true": True, "false": False}).fillna(False).astype(bool)


def _numeric(frame: pd.DataFrame, columns: list[str]) -> None:
    for column in columns:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")


def _percentile(series: pd.Series, quantile: float) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna().to_numpy(dtype=float)
    return float(np.quantile(values, quantile)) if len(values) else math.nan


def _gini(values: np.ndarray) -> float:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values) & (values >= 0)]
    if not len(values) or math.isclose(float(values.sum()), 0.0):
        return 0.0
    values = np.sort(values)
    n = len(values)
    return float((2 * np.dot(np.arange(1, n + 1), values) / (n * values.sum())) - (n + 1) / n)


def _load_transfer_events(archive: CampaignArchive, member: str) -> pd.DataFrame:
    columns = [
        "task_id",
        "from_node",
        "to_node",
        "from_node_type",
        "to_node_type",
        "transfer_start_time_sec",
        "transfer_end_time_sec",
        "transfer_duration_sec",
        "transferred_data_mbits",
        "hop_count_after_transfer",
        "window_id",
        "link_type",
    ]
    try:
        frame = archive.read_csv(member, usecols=columns, low_memory=False)
    except EmptyDataError:
        return pd.DataFrame(columns=columns)
    _numeric(
        frame,
        [
            "transfer_start_time_sec",
            "transfer_end_time_sec",
            "transfer_duration_sec",
            "transferred_data_mbits",
            "hop_count_after_transfer",
        ],
    )
    return frame


def _load_useful_recipient_sets(archive: CampaignArchive, member: str) -> dict[str, set[str]]:
    """Match the simulator's useful_satellites_by_task definition exactly."""
    opportunities = archive.read_csv(
        member,
        usecols=["task_id", "node_id", "earliest_observation_time_sec", "deadline_time_sec"],
        low_memory=False,
    )
    _numeric(opportunities, ["earliest_observation_time_sec", "deadline_time_sec"])
    valid = opportunities[
        opportunities["earliest_observation_time_sec"].notna()
        & opportunities["deadline_time_sec"].notna()
        & (opportunities["earliest_observation_time_sec"] <= opportunities["deadline_time_sec"] + 1e-9)
    ]
    return {
        str(task_id): set(group["node_id"].dropna().astype(str))
        for task_id, group in valid.groupby("task_id", sort=False)
    }


def _matrix_upper_sum(archive: CampaignArchive, member: str) -> float:
    frame = archive.read_csv(member, index_col=0)
    matrix = frame.apply(pd.to_numeric, errors="coerce").fillna(0.0).to_numpy(dtype=float)
    if matrix.ndim != 2:
        return math.nan
    n = min(matrix.shape)
    return float(np.triu(matrix[:n, :n], k=1).sum())


def _task_outcomes_for_case(
    archive: CampaignArchive,
    event_root: str,
    region: str,
    case_id: str,
    useful_recipient_sets: dict[str, set[str]] | None = None,
) -> tuple[pd.DataFrame, dict[str, float | int | str], dict[str, float | int | str], list[dict[str, object]]]:
    member = lambda role: archive.case_member(event_root, case_id, role)
    tasks = archive.read_csv(member("wildfire_tasks_used.csv"), low_memory=False)
    reception = archive.read_csv(member("task_reception_times.csv"), low_memory=False)
    observation = archive.read_csv(member("task_observation_results.csv"), low_memory=False)
    transfers = _load_transfer_events(archive, member("transfer_events.csv.gz"))
    summary = archive.read_csv(member("performance_summary.csv")).iloc[0]
    if useful_recipient_sets is None:
        useful_recipient_sets = _load_useful_recipient_sets(archive, member("observation_opportunities.csv.gz"))

    task_columns = [
        "task_id",
        "latitude",
        "longitude",
        "priority_class",
        "task_size_mbits",
        "required_response_time_min",
        "timestamp_utc",
        "deadline_time_utc",
        "created_time_sec",
        "deadline_time_sec",
    ]
    base = tasks[task_columns].copy()
    reception_keep = [
        "task_id",
        "satellites_reached",
        "central_nodes_reached",
        "observers_reached",
        "useful_observers_total",
        "useful_observers_reached",
        "useful_recipients_total",
        "useful_recipients_reached",
        "first_satellite_reception_node",
        "first_satellite_reception_time_sec",
        "first_reception_latency_min",
        "first_reception_hop_count",
        "max_hops",
        "received_by_satellite_before_deadline",
        "received_by_useful_observer_before_deadline",
        "received_by_useful_recipient_before_deadline",
    ]
    observation_keep = [
        "task_id",
        "observed_before_deadline",
        "observing_satellite_id",
        "observing_satellite_type",
        "observation_time_sec",
        "reception_to_observation_min",
        "observation_hop_count",
        "min_distance_km",
        "failure_class",
    ]
    outcome = base.merge(reception[reception_keep], on="task_id", how="left", validate="one_to_one")
    outcome = outcome.merge(observation[observation_keep], on="task_id", how="left", validate="one_to_one")
    for column in [
        "received_by_satellite_before_deadline",
        "received_by_useful_observer_before_deadline",
        "received_by_useful_recipient_before_deadline",
        "observed_before_deadline",
    ]:
        outcome[column] = _bool_series(outcome[column])
    numeric_columns = [
        "latitude",
        "longitude",
        "task_size_mbits",
        "required_response_time_min",
        "created_time_sec",
        "deadline_time_sec",
        "satellites_reached",
        "central_nodes_reached",
        "observers_reached",
        "useful_observers_total",
        "useful_observers_reached",
        "useful_recipients_total",
        "useful_recipients_reached",
        "first_satellite_reception_time_sec",
        "first_reception_latency_min",
        "first_reception_hop_count",
        "max_hops",
        "observation_time_sec",
        "reception_to_observation_min",
        "observation_hop_count",
        "min_distance_km",
    ]
    _numeric(outcome, numeric_columns)

    outcome["destinations_reached_before_deadline"] = 0.0
    outcome["last_unique_reception_time_sec"] = np.nan
    useful_reached_all_time = pd.Series(dtype=float)
    if not transfers.empty:
        deadlines = outcome.set_index("task_id")["deadline_time_sec"]
        satellite_transfers = transfers[
            ~transfers["to_node_type"].astype(str).str.lower().isin(["ground_station", "ground", "gs"])
        ].copy()
        satellite_transfers["deadline_time_sec"] = satellite_transfers["task_id"].map(deadlines)
        useful_mask = [
            str(to_node) in useful_recipient_sets.get(str(task_id), set())
            for task_id, to_node in zip(satellite_transfers["task_id"], satellite_transfers["to_node"])
        ]
        useful_transfers = satellite_transfers.loc[useful_mask].copy()
        useful_first_arrivals_all = (
            useful_transfers.sort_values("transfer_end_time_sec")
            .drop_duplicates(["task_id", "to_node"], keep="first")
        )
        useful_reached_all_time = useful_first_arrivals_all.groupby("task_id")["to_node"].nunique()
        before = useful_transfers[
            useful_transfers["transfer_end_time_sec"].notna()
            & useful_transfers["deadline_time_sec"].notna()
            & (useful_transfers["transfer_end_time_sec"] <= useful_transfers["deadline_time_sec"] + 1e-9)
        ].copy()
        first_arrivals = (
            before.sort_values("transfer_end_time_sec")
            .drop_duplicates(["task_id", "to_node"], keep="first")
        )
        destination_counts = first_arrivals.groupby("task_id")["to_node"].nunique()
        last_arrival = first_arrivals.groupby("task_id")["transfer_end_time_sec"].max()
        outcome["destinations_reached_before_deadline"] = outcome["task_id"].map(destination_counts).fillna(0).astype(float)
        outcome["last_unique_reception_time_sec"] = outcome["task_id"].map(last_arrival)

    outcome["derived_useful_recipients_total"] = outcome["task_id"].map(
        {task_id: len(nodes) for task_id, nodes in useful_recipient_sets.items()}
    ).fillna(0).astype(float)
    outcome["derived_useful_recipients_reached_all_time"] = outcome["task_id"].map(useful_reached_all_time).fillna(0).astype(float)

    eligible = outcome["useful_recipients_total"]
    reached = outcome["destinations_reached_before_deadline"]
    outcome["receiver_fraction_at_deadline_percent"] = np.where(
        eligible > 0,
        100.0 * np.minimum(reached, eligible) / eligible,
        np.nan,
    )
    outcome["full_dissemination_success"] = np.where(eligible > 0, reached >= eligible, False).astype(bool)
    outcome["full_dissemination_latency_min"] = np.where(
        outcome["full_dissemination_success"],
        (outcome["last_unique_reception_time_sec"] - outcome["created_time_sec"]) / 60.0,
        np.nan,
    )
    outcome["observation_latency_min"] = np.where(
        outcome["observed_before_deadline"],
        (outcome["observation_time_sec"] - outcome["created_time_sec"]) / 60.0,
        np.nan,
    )
    outcome["combined_success"] = (
        outcome["received_by_useful_recipient_before_deadline"] & outcome["observed_before_deadline"]
    )
    outcome["first_reception_censored"] = outcome["first_reception_latency_min"].isna()
    outcome["full_dissemination_censored"] = ~outcome["full_dissemination_success"]
    outcome.insert(0, "region", region)
    outcome.insert(1, "case_id", case_id)
    outcome.insert(2, "architecture_id", str(summary["architecture_id"]))

    n_tasks = len(outcome)
    pct = lambda values: 100.0 * float(pd.Series(values).fillna(False).astype(bool).mean()) if n_tasks else math.nan
    case_metrics: dict[str, float | int | str] = {
        "region": region,
        "case_id": case_id,
        "architecture_id": str(summary["architecture_id"]),
        "n_tasks": n_tasks,
        "first_reception_success_percent": pct(outcome["received_by_satellite_before_deadline"]),
        "useful_recipient_success_percent": pct(outcome["received_by_useful_recipient_before_deadline"]),
        "full_dissemination_success_percent_s100": pct(outcome["full_dissemination_success"]),
        "mean_receiver_fraction_at_deadline_percent": float(outcome["receiver_fraction_at_deadline_percent"].mean()),
        "observation_success_percent": pct(outcome["observed_before_deadline"]),
        "combined_success_percent": pct(outcome["combined_success"]),
        "mean_first_reception_latency_min": float(outcome["first_reception_latency_min"].mean()),
        "p50_first_reception_latency_min": _percentile(outcome["first_reception_latency_min"], 0.50),
        "p90_first_reception_latency_min": _percentile(outcome["first_reception_latency_min"], 0.90),
        "p95_first_reception_latency_min": _percentile(outcome["first_reception_latency_min"], 0.95),
        "mean_full_dissemination_latency_min": float(outcome["full_dissemination_latency_min"].mean()),
        "p90_full_dissemination_latency_min": _percentile(outcome["full_dissemination_latency_min"], 0.90),
        "p95_full_dissemination_latency_min": _percentile(outcome["full_dissemination_latency_min"], 0.95),
        "mean_observation_latency_min": float(outcome["observation_latency_min"].mean()),
        "p90_observation_latency_min": _percentile(outcome["observation_latency_min"], 0.90),
        "p95_observation_latency_min": _percentile(outcome["observation_latency_min"], 0.95),
        "first_reception_censored_tasks": int(outcome["first_reception_censored"].sum()),
        "full_dissemination_censored_tasks": int(outcome["full_dissemination_censored"].sum()),
        "geometry_failures": int(outcome["failure_class"].astype(str).str.contains("geometry", case=False, na=False).sum()),
        "communication_failures": int(outcome["failure_class"].astype(str).str.contains("communication", case=False, na=False).sum()),
        "routing_failures": int(outcome["failure_class"].astype(str).str.contains("routing", case=False, na=False).sum()),
        "n_transfer_events": int(len(transfers)),
        "total_transferred_data_mbits": float(transfers["transferred_data_mbits"].sum()) if not transfers.empty else 0.0,
    }

    available_count = _matrix_upper_sum(archive, member("adjacency_window_count.csv"))
    available_duration = _matrix_upper_sum(archive, member("adjacency_window_duration_sec.csv"))
    available_capacity = _matrix_upper_sum(archive, member("adjacency_window_capacity_mbits.csv"))
    node_load = pd.Series(dtype=float)
    central_load = 0.0
    if not transfers.empty:
        sent = transfers.groupby("from_node")["transferred_data_mbits"].sum()
        received = transfers.groupby("to_node")["transferred_data_mbits"].sum()
        node_load = sent.add(received, fill_value=0.0)
        node_types = pd.concat(
            [
                transfers[["from_node", "from_node_type"]].rename(columns={"from_node": "node", "from_node_type": "type"}),
                transfers[["to_node", "to_node_type"]].rename(columns={"to_node": "node", "to_node_type": "type"}),
            ],
            ignore_index=True,
        ).drop_duplicates("node").set_index("node")["type"]
        central_nodes = node_types[node_types.astype(str).str.contains("central", case=False, na=False)].index
        central_load = float(node_load.reindex(central_nodes).fillna(0.0).sum())
    loads = node_load.to_numpy(dtype=float)
    total_node_load = float(loads.sum()) if len(loads) else 0.0
    shares = loads / total_node_load if total_node_load > 0 else np.array([], dtype=float)
    top_count = max(1, math.ceil(len(loads) * 0.10)) if len(loads) else 0
    network_metrics: dict[str, float | int | str] = {
        "region": region,
        "case_id": case_id,
        "available_window_count_upper_triangle": available_count,
        "available_window_duration_sec_upper_triangle": available_duration,
        "available_window_capacity_mbits_upper_triangle": available_capacity,
        "used_window_count": int(transfers["window_id"].nunique()) if not transfers.empty else 0,
        "transfer_event_count": int(len(transfers)),
        "transfer_duration_sec": float(transfers["transfer_duration_sec"].sum()) if not transfers.empty else 0.0,
        "transferred_data_mbits": float(transfers["transferred_data_mbits"].sum()) if not transfers.empty else 0.0,
        "capacity_utilization_percent": 100.0 * float(transfers["transferred_data_mbits"].sum()) / available_capacity if available_capacity > 0 else math.nan,
        "max_node_load_mbits": float(loads.max()) if len(loads) else 0.0,
        "top_10_percent_node_load_share": float(np.sort(shares)[-top_count:].sum()) if top_count else 0.0,
        "node_load_gini": _gini(loads),
        "node_load_hhi": float(np.square(shares).sum()) if len(shares) else 0.0,
        "central_node_load_share": central_load / total_node_load if total_node_load > 0 else 0.0,
        "transfer_data_per_task_mbits": float(transfers["transferred_data_mbits"].sum()) / n_tasks if n_tasks else math.nan,
        "transfer_events_per_task": len(transfers) / n_tasks if n_tasks else math.nan,
        "task_to_available_capacity_ratio": float(outcome["task_size_mbits"].sum()) / available_capacity if available_capacity > 0 else math.nan,
    }

    comparison_metrics = {
        "n_tasks": float(n_tasks),
        "tasks_reached_before_deadline_percent": case_metrics["first_reception_success_percent"],
        "dissemination_success_percent": case_metrics["useful_recipient_success_percent"],
        "observation_success_percent": case_metrics["observation_success_percent"],
        "mean_first_reception_latency_min": case_metrics["mean_first_reception_latency_min"],
        "p50_first_reception_latency_min": case_metrics["p50_first_reception_latency_min"],
        "p90_first_reception_latency_min": case_metrics["p90_first_reception_latency_min"],
        "p95_first_reception_latency_min": case_metrics["p95_first_reception_latency_min"],
        "n_transfer_events": case_metrics["n_transfer_events"],
        "total_transferred_data_mbits": case_metrics["total_transferred_data_mbits"],
    }
    comparisons = []
    for metric, recomputed in comparison_metrics.items():
        reported = pd.to_numeric(pd.Series([summary.get(metric)]), errors="coerce").iloc[0]
        difference = float(recomputed) - float(reported) if pd.notna(reported) and pd.notna(recomputed) else math.nan
        comparisons.append(
            {
                "region": region,
                "case_id": case_id,
                "metric": metric,
                "reported": reported,
                "recomputed": recomputed,
                "absolute_difference": abs(difference) if pd.notna(difference) else math.nan,
            }
        )
    return outcome, case_metrics, network_metrics, comparisons


def build_canonical_tables(
    archive: CampaignArchive,
    validation: ValidationResult,
    output_root: Path,
    tolerance: float = 1e-6,
) -> CanonicalResult:
    canonical_dir = output_root / "02_canonical_tables"
    validation_dir = output_root / "01_validation"
    canonical_dir.mkdir(parents=True, exist_ok=True)
    validation.cases.to_parquet(canonical_dir / "cases.parquet", index=False)

    task_catalogs = []
    source_index_rows = []
    for region, event_root in validation.event_roots.items():
        first_case = sorted(validation.cases.loc[validation.cases["region"] == region, "case_id"])[0]
        tasks = archive.read_csv(archive.case_member(event_root, first_case, "wildfire_tasks_used.csv"), low_memory=False)
        tasks["region"] = region
        tasks = tasks[["region", *[column for column in tasks.columns if column != "region"]]]
        task_catalogs.append(tasks)
        for case_id in sorted(validation.cases.loc[validation.cases["region"] == region, "case_id"]):
            for role in ["communication_windows.csv.gz", "observation_opportunities.csv.gz", "transfer_events.csv.gz"]:
                member = archive.case_member(event_root, case_id, role)
                info = archive.info_by_name[member]
                source_index_rows.append(
                    {
                        "region": region,
                        "case_id": case_id,
                        "role": role,
                        "zip_member": member,
                        "archive_member_bytes": info.file_size,
                        "zip_compressed_bytes": info.compress_size,
                        "materialized_in_core_profile": role == "transfer_events.csv.gz",
                    }
                )
    tasks_frame = pd.concat(task_catalogs, ignore_index=True)
    tasks_frame.to_parquet(canonical_dir / "tasks.parquet", index=False)
    source_index = pd.DataFrame(source_index_rows)
    source_index.to_parquet(canonical_dir / "external_detailed_table_index.parquet", index=False)

    outcome_path = canonical_dir / "task_outcomes.parquet"
    writer: pq.ParquetWriter | None = None
    case_rows: list[dict[str, object]] = []
    network_rows: list[dict[str, object]] = []
    comparison_rows: list[dict[str, object]] = []
    qc_rows: list[dict[str, object]] = []
    processed = 0
    total = len(validation.cases)
    cached_base_id = ""
    cached_useful_sets: dict[str, set[str]] = {}
    try:
        for region, event_root in validation.event_roots.items():
            case_ids = sorted(validation.cases.loc[validation.cases["region"] == region, "case_id"])
            for case_id in case_ids:
                base_id = case_id.rsplit("_CN", 1)[0]
                if base_id != cached_base_id:
                    representative_case = f"{base_id}_CN000_F1"
                    cached_useful_sets = _load_useful_recipient_sets(
                        archive,
                        archive.case_member(event_root, representative_case, "observation_opportunities.csv.gz"),
                    )
                    cached_base_id = base_id
                outcome, case_metrics, network_metrics, comparisons = _task_outcomes_for_case(
                    archive, event_root, region, case_id, cached_useful_sets
                )
                table = pa.Table.from_pandas(outcome, preserve_index=False)
                if writer is None:
                    writer = pq.ParquetWriter(outcome_path, table.schema, compression="zstd")
                elif table.schema != writer.schema:
                    table = table.cast(writer.schema, safe=False)
                writer.write_table(table)
                case_rows.append(case_metrics)
                network_rows.append(network_metrics)
                comparison_rows.extend(comparisons)

                invalid_order = (
                    (outcome["first_satellite_reception_time_sec"].notna())
                    & (outcome["first_satellite_reception_time_sec"] + 1e-9 < outcome["created_time_sec"])
                ).sum()
                invalid_observation_order = (
                    outcome["observed_before_deadline"]
                    & (outcome["observation_time_sec"] + 1e-9 < outcome["first_satellite_reception_time_sec"])
                ).sum()
                invalid_success_deadline = (
                    outcome["observed_before_deadline"]
                    & (outcome["observation_time_sec"] > outcome["deadline_time_sec"] + 1e-9)
                ).sum()
                receiver_overflow = (
                    outcome["destinations_reached_before_deadline"] > outcome["useful_recipients_total"] + 1e-9
                ).sum()
                denominator_mismatch = (
                    outcome["derived_useful_recipients_total"] != outcome["useful_recipients_total"]
                ).sum()
                reached_mismatch = (
                    outcome["derived_useful_recipients_reached_all_time"] != outcome["useful_recipients_reached"]
                ).sum()
                for check_id, count, severity in [
                    ("first_reception_not_before_creation", int(invalid_order), "ERROR"),
                    ("observation_not_before_first_reception", int(invalid_observation_order), "ERROR"),
                    ("successful_observation_within_deadline", int(invalid_success_deadline), "ERROR"),
                    ("derived_receiver_count_not_above_eligible_total", int(receiver_overflow), "ERROR"),
                    ("useful_recipient_denominator_matches_simulator", int(denominator_mismatch), "ERROR"),
                    ("useful_recipient_reached_count_matches_simulator", int(reached_mismatch), "ERROR"),
                ]:
                    qc_rows.append(
                        {
                            "region": region,
                            "case_id": case_id,
                            "check_id": check_id,
                            "violations": count,
                            "severity": severity,
                            "passed": count == 0,
                        }
                    )
                processed += 1
                if processed % 100 == 0 or processed == total:
                    print(f"[canonical] processed {processed}/{total} cases", flush=True)
    finally:
        if writer is not None:
            writer.close()

    case_metrics_frame = pd.DataFrame(case_rows)
    network_frame = pd.DataFrame(network_rows)
    comparisons_frame = pd.DataFrame(comparison_rows)
    qc_frame = pd.DataFrame(qc_rows)
    case_metrics_frame.to_parquet(canonical_dir / "case_metrics.parquet", index=False)
    network_frame.to_parquet(canonical_dir / "network_case_metrics.parquet", index=False)
    comparisons_frame.to_csv(validation_dir / "metric_recomputation_comparison.csv", index=False)
    qc_frame.to_csv(validation_dir / "qc_invariants.csv", index=False)

    comparison_pass = comparisons_frame["absolute_difference"].fillna(0.0).le(tolerance)
    comparison_summary = (
        comparisons_frame.assign(passed=comparison_pass)
        .groupby("metric")
        .agg(cases=("case_id", "size"), failures=("passed", lambda values: int((~values).sum())), max_abs_difference=("absolute_difference", "max"))
        .reset_index()
    )
    comparison_summary.to_csv(validation_dir / "metric_recomputation_summary.csv", index=False)
    gate_c = bool(comparison_pass.all() and qc_frame["passed"].all())
    (validation_dir / "gate_c_report.md").write_text(
        "\n".join(
            [
                "# Gate C — metric reconstruction and invariants",
                "",
                f"- Gate C: `{'PASS' if gate_c else 'FAIL'}`",
                f"- Reported/recomputed comparisons: `{len(comparisons_frame):,}`",
                f"- Comparison failures at tolerance {tolerance:g}: `{int((~comparison_pass).sum())}`",
                f"- Invariant violations: `{int(qc_frame['violations'].sum())}`",
                "- Latency summaries are conditional on completion; censored counts are retained separately.",
                "- S100 is reconstructed from unique satellite destinations reached by each task before its deadline and the simulator's useful-recipients denominator.",
            ]
        ),
        encoding="utf-8",
    )

    dictionary_rows = [
        ("cases.parquet", "case_id", "string", "Stable case identifier; one row per region/case."),
        ("tasks.parquet", "task_id", "string", "Stable event task identifier; unique within region."),
        ("task_outcomes.parquet", "first_reception_latency_min", "minutes", "Conditional latency; null when censored."),
        ("task_outcomes.parquet", "full_dissemination_success", "boolean", "S100: all useful recipients reached by deadline."),
        ("task_outcomes.parquet", "receiver_fraction_at_deadline_percent", "percent", "Unique useful recipients reached by deadline divided by eligible total."),
        ("task_outcomes.parquet", "observation_latency_min", "minutes", "Observation time minus task creation time; conditional on success."),
        ("case_metrics.parquet", "full_dissemination_success_percent_s100", "percent", "Share of tasks achieving S100."),
        ("network_case_metrics.parquet", "available_window_count_upper_triangle", "count", "Undirected available-window count from adjacency upper triangle."),
        ("network_case_metrics.parquet", "capacity_utilization_percent", "percent", "Transferred data divided by upper-triangle available capacity."),
        ("external_detailed_table_index.parquet", "zip_member", "path", "Read-only pointer to detailed raw table retained in ZIP."),
    ]
    pd.DataFrame(dictionary_rows, columns=["table", "field", "unit_or_type", "definition"]).to_csv(
        canonical_dir / "data_dictionary.csv", index=False
    )
    (canonical_dir / "README.md").write_text(
        "\n".join(
            [
                "# Canonical tables",
                "",
                "The core profile materializes cases, tasks, task outcomes, case metrics, and case-level network metrics.",
                "The very large communication-window and observation-opportunity tables are not duplicated because the drive does not have enough free space; `external_detailed_table_index.parquet` points to every authoritative ZIP member.",
                "Transfer events are consumed to reconstruct deadline-aware receiver counts and S100, but their raw rows remain in the ZIP.",
            ]
        ),
        encoding="utf-8",
    )
    return CanonicalResult(tasks_frame, case_metrics_frame, network_frame, comparisons_frame, qc_frame, gate_c)
