from __future__ import annotations

"""Post-process one Full891 V2 regional result archive.

The script accepts either a regional ZIP or an already extracted regional
directory.  It performs integrity checks, removes resume-created duplicate
scenario rows using the same key as the simulator, creates balanced scientific
summaries, and writes reusable CSV/Parquet tables plus publication-ready PNGs.

Example
-------
python postprocess_california.py \
  --input "C:/path/to/California.zip" \
  --output "C:/path/to/california_postprocessing"
"""

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from typing import Iterable, Sequence

os.environ.setdefault(
    "MPLCONFIGDIR",
    str(Path(tempfile.gettempdir()) / "full891_california_matplotlib"),
)

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
import seaborn as sns


CASE_RE = re.compile(
    r"^T(?P<n_satellites>\d{3,4})_P(?P<n_planes>\d{2})_H(?P<altitude_km>\d{4})_"
    r"I(?P<inclination_code>\d{4})_CN(?P<cn_fraction_percent>\d{3})_F(?P<walker_f>\d+)$"
)

SELECTED_SCENARIOS = [
    "nominal_original",
    "unlimited_useful_deadline",
    "unlimited_useful_sim_end",
    "policy_central",
    "policy_peer",
    "policy_hybrid",
]

ROBUST_FAMILIES = [
    "random_satellite_failure",
    "random_cn_failure",
    "targeted_cn_failure",
    "link_window_outage",
    "capacity_degradation",
    "ground_station_outage",
    "combined_stress",
]

PERFORMANCE_METRICS = [
    "observation_success_percent",
    "s100_surviving_before_deadline_percent",
    "mean_surviving_coverage_deadline_percent",
    "p100_t100_surviving_min",
    "total_transferred_data_mbits",
    "n_transfer_events",
    "used_capacity_percent",
    "peak_node_sent_data_mbits",
    "node_sent_data_gini",
]

BENEFIT_METRICS = [
    "observation_success_percent",
    "s100_surviving_before_deadline_percent",
    "mean_surviving_coverage_deadline_percent",
]

ARCHITECTURE_FACTORS = [
    "n_satellites",
    "n_planes",
    "altitude_km",
    "inclination_deg",
    "cn_fraction_percent",
]

COLORS = {
    "navy": "#17365D",
    "blue": "#2E75B6",
    "teal": "#1B998B",
    "orange": "#E07A3F",
    "red": "#C94C4C",
    "gold": "#D6A84B",
    "gray": "#6B7280",
    "light": "#EAF1F8",
}


def _print(message: str) -> None:
    print(f"[california] {message}", flush=True)


def _safe_extract(zip_path: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        bad = [
            info.filename
            for info in archive.infolist()
            if Path(info.filename).is_absolute() or ".." in Path(info.filename).parts
        ]
        if bad:
            raise ValueError(f"Unsafe ZIP members detected: {bad[:5]}")
        corrupt = archive.testzip()
        if corrupt:
            raise ValueError(f"CRC failure in ZIP member: {corrupt}")
        uncompressed_bytes = sum(info.file_size for info in archive.infolist() if not info.is_dir())
        free_bytes = shutil.disk_usage(destination).free
        reserve_bytes = 3 * 1024 ** 3
        if free_bytes < uncompressed_bytes + reserve_bytes:
            raise OSError(
                "Insufficient free disk space for verified extraction: "
                f"archive expands to {uncompressed_bytes / 1024 ** 3:.2f} GB, "
                f"free space is {free_bytes / 1024 ** 3:.2f} GB, and a 3 GB "
                "working reserve is required."
            )
        archive.extractall(destination)
    return destination


def resolve_california_root(input_path: Path, work_dir: Path) -> Path:
    if input_path.is_file() and input_path.suffix.lower() == ".zip":
        source = input_path.resolve()
        stat = source.stat()
        signature = f"{source}|{stat.st_size}|{stat.st_mtime_ns}"
        suffix = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:12]
        extracted = work_dir / f"{input_path.stem}_{suffix}"
        marker = extracted / ".extract_complete"
        if not marker.exists():
            _print(f"Extracting verified archive to {extracted}")
            _safe_extract(input_path, extracted)
            marker.write_text(signature + "\n", encoding="utf-8")
        candidates = [extracted / "California", extracted]
    elif input_path.is_dir():
        candidates = [input_path / "California", input_path]
    else:
        raise FileNotFoundError(input_path)

    for candidate in candidates:
        if candidate.exists() and any(CASE_RE.match(path.name) for path in candidate.rglob("*")):
            return candidate
    raise FileNotFoundError(f"Could not locate California case directories under {input_path}")


def case_directories(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_dir() and CASE_RE.match(path.name))


def parse_case_id(case_id: str) -> dict[str, float | int]:
    match = CASE_RE.match(case_id)
    if not match:
        raise ValueError(f"Unrecognized case id: {case_id}")
    values = {key: int(value) for key, value in match.groupdict().items()}
    return {
        "n_satellites": values["n_satellites"],
        "n_planes": values["n_planes"],
        "altitude_km": float(values["altitude_km"]),
        "inclination_deg": values["inclination_code"] / 10.0,
        "cn_fraction_percent": float(values["cn_fraction_percent"]),
        "walker_f": values["walker_f"],
    }


def base_id(case_id: str) -> str:
    return re.sub(r"_CN\d{3}(?=_F\d+$)", "", case_id)


def read_parquet_parts(
    paths: Sequence[Path], columns: Sequence[str] | None = None, chunk_size: int = 750
) -> pd.DataFrame:
    """Read many small Parquet files with schema promotion.

    Chunk order follows sorted file order, allowing deterministic keep-last
    deduplication after resumed runs appended new checkpoint partitions.
    """

    if not paths:
        return pd.DataFrame()
    tables: list[pa.Table] = []
    ordered = sorted(paths)
    # Some CN-failure checkpoint parts contain only NOT_APPLICABLE fields.  A
    # dataset created from such a file first would otherwise hide the full
    # metric columns in later fragments.  Select the widest schema once and
    # enforce it for every chunk so absent fields are promoted to nulls.
    schema_candidates = [
        pq.read_schema(path) for path in ordered[: min(len(ordered), 1000)]
    ]
    unified_schema = max(schema_candidates, key=len)
    for start in range(0, len(ordered), chunk_size):
        chunk = ordered[start : start + chunk_size]
        table = ds.dataset(
            [str(path) for path in chunk], format="parquet", schema=unified_schema
        ).to_table(columns=list(columns) if columns else None)
        tables.append(table)
        _print(f"Read {min(start + chunk_size, len(ordered)):,}/{len(ordered):,} Parquet parts")
    return pa.concat_tables(tables, promote_options="default").to_pandas()


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def conflicting_duplicate_keys(raw_metrics: pd.DataFrame) -> pd.DataFrame:
    """Identify resume-created duplicate keys whose scientific payload differs."""

    keys = ["region", "case_id", "scenario_id"]
    if raw_metrics.empty or not set(keys).issubset(raw_metrics.columns):
        return pd.DataFrame(columns=keys + ["duplicate_versions"])
    duplicates = raw_metrics.loc[raw_metrics.duplicated(keys, keep=False)].copy()
    if duplicates.empty:
        return pd.DataFrame(columns=keys + ["duplicate_versions"])
    payload_columns = [column for column in duplicates.columns if column not in keys + ["_read_order"]]
    payload = duplicates[payload_columns].copy()
    # Hashing treats identical NaN placements consistently and avoids converting
    # hundreds of thousands of result rows to large Python strings.
    duplicates["_payload_hash"] = pd.util.hash_pandas_object(payload, index=False).to_numpy()
    counts = duplicates.groupby(keys, dropna=False)["_payload_hash"].nunique().rename("duplicate_versions")
    return counts.loc[counts > 1].reset_index()


def audit_archive(root: Path, cases: Sequence[Path], raw_metrics: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    raw_counts = raw_metrics.groupby("case_id", sort=False).size()
    canonical = raw_metrics.drop_duplicates(["region", "case_id", "scenario_id"], keep="last")
    unique_counts = canonical.groupby("case_id", sort=False).size()
    rows: list[dict] = []
    for case in cases:
        case_id = case.name
        manifest = read_json(case / "case_manifest.json")
        raw_count = int(raw_counts.get(case_id, 0))
        unique_count = int(unique_counts.get(case_id, 0))
        row = {
            "case_id": case_id,
            "base_id": base_id(case_id),
            **parse_case_id(case_id),
            "success_marker": (case / "_SUCCESS.json").exists(),
            "failed_marker": (case / "_FAILED.json").exists(),
            "case_metric_parts": len(list(case.glob("case_metrics_part_*.parquet"))),
            "task_metric_parts": len(list(case.glob("task_metrics_part_*.parquet"))),
            "transfer_parts": len(list(case.glob("transfers_part_*.parquet"))),
            "raw_scenario_rows": raw_count,
            "unique_scenario_rows": unique_count,
            "duplicate_scenario_rows": raw_count - unique_count,
            "manifest_present": bool(manifest),
            "task_count": manifest.get("n_tasks"),
            "duration_hours": manifest.get("duration_hours"),
            "epoch_utc": manifest.get("epoch_utc"),
            "n_windows_generated": manifest.get("n_windows_generated"),
            "n_observation_opportunities": manifest.get("n_observation_opportunities"),
            "configuration_fingerprint": manifest.get("configuration_fingerprint"),
            "architecture_manifest_present": (case / "architecture_manifest.parquet").exists(),
            "architecture_validation_present": (case / "architecture_validation.parquet").exists(),
            "central_node_distribution_present": (case / "central_node_distribution.parquet").exists(),
            "constellation_nodes_present": (case / "constellation_nodes.parquet").exists(),
        }
        rows.append(row)

    audit = pd.DataFrame(rows).sort_values("case_id").reset_index(drop=True)
    complete_counts = audit.loc[audit["success_marker"], "unique_scenario_rows"]
    expected_scenarios = int(complete_counts.mode().iloc[0]) if len(complete_counts) else 0
    complete_case_ids = set(audit.loc[audit["success_marker"], "case_id"])
    complete_canonical = canonical.loc[canonical["case_id"].isin(complete_case_ids)]

    cn_per_base = audit.loc[audit["success_marker"]].groupby("base_id")["cn_fraction_percent"].nunique()
    expected_cn_levels = int(audit["cn_fraction_percent"].nunique())
    balanced_bases = set(cn_per_base.loc[cn_per_base == expected_cn_levels].index)

    summary = {
        "base_architecture_directories": int(audit["base_id"].nunique()),
        "case_directories": int(len(audit)),
        "success_markers": int(audit["success_marker"].sum()),
        "missing_success_markers": int((~audit["success_marker"]).sum()),
        "failed_markers": int(audit["failed_marker"].sum()),
        "expected_scenarios_per_case": expected_scenarios,
        "expected_full_scenario_rows": int(len(audit) * expected_scenarios),
        "raw_scenario_rows": int(len(raw_metrics)),
        "unique_scenario_rows_all_cases": int(len(canonical)),
        "duplicate_scenario_rows_removed": int(len(raw_metrics) - len(canonical)),
        "unique_scenario_rows_success_cases": int(len(complete_canonical)),
        "completed_status_rows_success_cases": int((complete_canonical["status"] == "COMPLETED").sum()),
        "not_applicable_rows_success_cases": int((complete_canonical["status"] == "NOT_APPLICABLE").sum()),
        "failed_status_rows_success_cases": int((complete_canonical["status"] == "FAILED").sum()),
        "balanced_base_architectures": int(len(balanced_bases)),
        "balanced_complete_cases": int(audit["base_id"].isin(balanced_bases).sum()),
        "configuration_fingerprints": {
            str(key): int(value)
            for key, value in audit["configuration_fingerprint"].fillna("MISSING").value_counts().items()
        },
        "task_counts": {
            str(key): int(value) for key, value in audit["task_count"].value_counts(dropna=False).items()
        },
    }
    audit["expected_scenario_rows"] = expected_scenarios
    audit["missing_scenario_rows"] = expected_scenarios - audit["unique_scenario_rows"]
    audit["balanced_base"] = audit["base_id"].isin(balanced_bases)
    return audit, summary


def validation_audit(cases: Sequence[Path]) -> tuple[pd.DataFrame, dict]:
    paths = [case / "architecture_validation.parquet" for case in cases]
    paths = [path for path in paths if path.exists()]
    frame = read_parquet_parts(paths, chunk_size=500)
    summary = {
        "validation_files": len(paths),
        "validation_checks": int(len(frame)),
        "passed_checks": int(frame.get("passed", pd.Series(dtype=bool)).fillna(False).sum()),
        "failed_checks": int((~frame.get("passed", pd.Series(dtype=bool)).fillna(False)).sum()),
    }
    return frame, summary


def descriptive_summary(frame: pd.DataFrame, group_columns: list[str], metrics: Iterable[str]) -> pd.DataFrame:
    rows: list[dict] = []
    for keys, group in frame.groupby(group_columns, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = dict(zip(group_columns, keys))
        row["case_count"] = int(group["case_id"].nunique()) if "case_id" in group else len(group)
        row["scenario_evaluations"] = int(len(group))
        for metric in metrics:
            if metric not in group:
                continue
            values = pd.to_numeric(group[metric], errors="coerce").dropna()
            row[f"{metric}_n"] = int(len(values))
            row[f"{metric}_mean"] = float(values.mean()) if len(values) else np.nan
            row[f"{metric}_median"] = float(values.median()) if len(values) else np.nan
            row[f"{metric}_std"] = float(values.std()) if len(values) > 1 else np.nan
            row[f"{metric}_p05"] = float(values.quantile(0.05)) if len(values) else np.nan
            row[f"{metric}_p95"] = float(values.quantile(0.95)) if len(values) else np.nan
            row[f"{metric}_min"] = float(values.min()) if len(values) else np.nan
            row[f"{metric}_max"] = float(values.max()) if len(values) else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def paired_scenario_comparison(frame: pd.DataFrame) -> pd.DataFrame:
    nominal = frame.loc[frame["scenario_id"] == "nominal_original"].set_index("case_id")
    unlimited = frame.loc[frame["scenario_id"] == "unlimited_useful_deadline"].set_index("case_id")
    shared = nominal.index.intersection(unlimited.index)
    rows = []
    for case in shared:
        row = {
            "case_id": case,
            "base_id": base_id(case),
            **parse_case_id(case),
        }
        for metric in PERFORMANCE_METRICS:
            if metric in nominal and metric in unlimited:
                left = pd.to_numeric(pd.Series([nominal.at[case, metric]]), errors="coerce").iloc[0]
                right = pd.to_numeric(pd.Series([unlimited.at[case, metric]]), errors="coerce").iloc[0]
                row[f"nominal_{metric}"] = left
                row[f"unlimited_{metric}"] = right
                row[f"delta_unlimited_minus_nominal_{metric}"] = right - left
        rows.append(row)
    return pd.DataFrame(rows)


def priority_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for scenario_id in ["nominal_original", "unlimited_useful_deadline"]:
        selected = frame.loc[frame["scenario_id"] == scenario_id]
        for priority in ["Critical", "High", "Medium", "Low"]:
            slug = priority.lower()
            n_column = f"n_tasks_{slug}"
            obs_column = f"{slug}_observation_success_percent"
            s100_column = f"{slug}_s100_surviving_before_deadline_percent"
            rows.append(
                {
                    "scenario_id": scenario_id,
                    "priority_class": priority,
                    "case_count": int(selected["case_id"].nunique()),
                    "tasks_per_case": float(pd.to_numeric(selected[n_column], errors="coerce").median()),
                    "observation_success_percent_mean": float(pd.to_numeric(selected[obs_column], errors="coerce").mean()),
                    "observation_success_percent_p05": float(pd.to_numeric(selected[obs_column], errors="coerce").quantile(0.05)),
                    "observation_success_percent_p95": float(pd.to_numeric(selected[obs_column], errors="coerce").quantile(0.95)),
                    "s100_before_deadline_percent_mean": float(pd.to_numeric(selected[s100_column], errors="coerce").mean()),
                    "s100_before_deadline_percent_p05": float(pd.to_numeric(selected[s100_column], errors="coerce").quantile(0.05)),
                    "s100_before_deadline_percent_p95": float(pd.to_numeric(selected[s100_column], errors="coerce").quantile(0.95)),
                }
            )
    return pd.DataFrame(rows)


def robustness_tables(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    robust = frame.loc[frame["family"].isin(ROBUST_FAMILIES)].copy()
    baseline_columns = ["case_id"] + [metric for metric in PERFORMANCE_METRICS if metric in frame]
    baseline = frame.loc[frame["scenario_id"] == "unlimited_useful_deadline", baseline_columns].copy()
    baseline = baseline.rename(columns={metric: f"baseline_{metric}" for metric in PERFORMANCE_METRICS if metric in baseline})
    robust = robust.merge(baseline, on="case_id", how="left", validate="many_to_one")
    for metric in PERFORMANCE_METRICS:
        if metric in robust and f"baseline_{metric}" in robust:
            robust[f"delta_{metric}"] = pd.to_numeric(robust[metric], errors="coerce") - pd.to_numeric(
                robust[f"baseline_{metric}"], errors="coerce"
            )
    numeric_metrics = [metric for metric in PERFORMANCE_METRICS if metric in robust]
    numeric_metrics += [f"delta_{metric}" for metric in PERFORMANCE_METRICS if f"delta_{metric}" in robust]
    group_columns = ["family", "failure_level_percent", "task_size_multiplier"]
    summary = descriptive_summary(
        robust.loc[robust["status"] == "COMPLETED"], group_columns, numeric_metrics
    )
    return robust, summary


def architecture_effects(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    group_rows = []
    eta_rows = []
    for scenario_id in ["nominal_original", "unlimited_useful_deadline"]:
        selected = frame.loc[frame["scenario_id"] == scenario_id]
        for factor in ARCHITECTURE_FACTORS:
            for metric in BENEFIT_METRICS:
                sample = selected[[factor, metric]].copy()
                sample[metric] = pd.to_numeric(sample[metric], errors="coerce")
                sample = sample.dropna()
                if sample.empty:
                    continue
                grand = float(sample[metric].mean())
                total_ss = float(((sample[metric] - grand) ** 2).sum())
                between_ss = 0.0
                for level, group in sample.groupby(factor):
                    mean = float(group[metric].mean())
                    between_ss += len(group) * (mean - grand) ** 2
                    group_rows.append(
                        {
                            "scenario_id": scenario_id,
                            "factor": factor,
                            "level": level,
                            "metric": metric,
                            "n": len(group),
                            "mean": mean,
                            "std": float(group[metric].std()),
                            "p05": float(group[metric].quantile(0.05)),
                            "p95": float(group[metric].quantile(0.95)),
                        }
                    )
                eta_rows.append(
                    {
                        "scenario_id": scenario_id,
                        "factor": factor,
                        "metric": metric,
                        "eta_squared_unadjusted": between_ss / total_ss if total_ss > 0 else np.nan,
                        "interpretation": "Unadjusted one-factor share of variance in the balanced factorial subset.",
                    }
                )
    return pd.DataFrame(group_rows), pd.DataFrame(eta_rows)


def exact_pareto(frame: pd.DataFrame) -> pd.DataFrame:
    selected = frame.loc[frame["scenario_id"] == "unlimited_useful_deadline"].copy()
    objectives = BENEFIT_METRICS + [
        "n_satellites",
        "n_central_nodes",
        "total_transferred_data_mbits",
    ]
    numeric = selected[objectives].apply(pd.to_numeric, errors="coerce")
    valid = numeric.notna().all(axis=1)
    selected = selected.loc[valid].copy().reset_index(drop=True)
    values = numeric.loc[valid].to_numpy(dtype=float)
    transformed = values.copy()
    transformed[:, 3:] *= -1.0
    keep = np.ones(len(transformed), dtype=bool)
    for i, point in enumerate(transformed):
        dominated = np.all(transformed >= point, axis=1) & np.any(transformed > point, axis=1)
        dominated[i] = False
        if dominated.any():
            keep[i] = False
    selected["is_exact_pareto"] = keep

    scaled = numeric.loc[valid].reset_index(drop=True).copy()
    for column in scaled:
        minimum, maximum = scaled[column].min(), scaled[column].max()
        scaled[column] = (scaled[column] - minimum) / (maximum - minimum) if maximum > minimum else 0.5
    for column in ["n_satellites", "n_central_nodes", "total_transferred_data_mbits"]:
        scaled[column] = 1.0 - scaled[column]
    selected["illustrative_equal_weight_score"] = scaled.mean(axis=1)
    selected["illustrative_score_rank"] = selected["illustrative_equal_weight_score"].rank(
        ascending=False, method="min"
    ).astype(int)
    return selected.sort_values(["is_exact_pareto", "illustrative_equal_weight_score"], ascending=[False, False])


def task_level_summary(root: Path, complete_case_ids: set[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    paths = [path for path in root.rglob("task_metrics_part_*.parquet") if path.parent.name in complete_case_ids]
    columns = [
        "case_id",
        "scenario_id",
        "family",
        "task_id",
        "priority_class",
        "task_size_mbits",
        "observed_before_deadline",
        "observation_latency_min",
        "failure_class",
        "s100_surviving_before_deadline",
        "surviving_coverage_deadline_percent",
        "t100_surviving_min",
        "t100_surviving_censored",
    ]
    tasks = read_parquet_parts(paths, columns=columns)
    tasks = tasks.drop_duplicates(["case_id", "scenario_id", "task_id"], keep="last")
    selected = tasks.loc[tasks["scenario_id"].isin(SELECTED_SCENARIOS + [
        "task_size_x0p5", "task_size_x1p0", "task_size_x2p0", "task_size_x4p0"
    ])].copy()
    rows = []
    for (scenario_id, priority), group in selected.groupby(["scenario_id", "priority_class"]):
        observed = group["observed_before_deadline"].fillna(False).astype(bool)
        s100 = group["s100_surviving_before_deadline"].fillna(False).astype(bool)
        latency = pd.to_numeric(group.loc[observed, "observation_latency_min"], errors="coerce").dropna()
        t100 = pd.to_numeric(group.loc[~group["t100_surviving_censored"].fillna(True), "t100_surviving_min"], errors="coerce").dropna()
        rows.append(
            {
                "scenario_id": scenario_id,
                "priority_class": priority,
                "case_count": int(group["case_id"].nunique()),
                "task_evaluations": int(len(group)),
                "observation_success_percent": 100.0 * float(observed.mean()),
                "s100_before_deadline_percent": 100.0 * float(s100.mean()),
                "mean_surviving_coverage_deadline_percent": float(pd.to_numeric(group["surviving_coverage_deadline_percent"], errors="coerce").mean()),
                "conditional_observation_latency_mean_min": float(latency.mean()) if len(latency) else np.nan,
                "conditional_observation_latency_p90_min": float(latency.quantile(0.90)) if len(latency) else np.nan,
                "t100_completion_percent": 100.0 * float((~group["t100_surviving_censored"].fillna(True)).mean()),
                "conditional_t100_p90_min": float(t100.quantile(0.90)) if len(t100) else np.nan,
            }
        )
    failures = (
        selected.loc[selected["scenario_id"].isin(["nominal_original", "unlimited_useful_deadline"])]
        .groupby(["scenario_id", "priority_class", "failure_class"], dropna=False)
        .size()
        .rename("task_evaluations")
        .reset_index()
    )
    failures["percent_within_scenario_priority"] = 100.0 * failures["task_evaluations"] / failures.groupby(
        ["scenario_id", "priority_class"]
    )["task_evaluations"].transform("sum")
    return pd.DataFrame(rows), failures


def transfer_summary(root: Path, complete_case_ids: set[str]) -> pd.DataFrame:
    paths = [path for path in root.rglob("transfers_part_*.parquet") if path.parent.name in complete_case_ids]
    if not paths:
        return pd.DataFrame()
    transfers = read_parquet_parts(paths)
    transfers = transfers.drop_duplicates(
        ["case_id", "scenario_id", "window_id", "task_id", "from_node", "to_node", "transfer_start_time_sec"],
        keep="last",
    )
    transfers["cn_fraction_percent"] = transfers["case_id"].map(
        lambda value: parse_case_id(str(value))["cn_fraction_percent"]
    )
    transfers["from_ground_station"] = transfers["from_node"].astype(str).str.startswith("GS_")
    rows = []
    for (scenario_id, cn), group in transfers.groupby(["scenario_id", "cn_fraction_percent"]):
        rows.append(
            {
                "scenario_id": scenario_id,
                "cn_fraction_percent": cn,
                "sampled_cases": int(group["case_id"].nunique()),
                "transfer_events": int(len(group)),
                "transferred_data_mbits": float(pd.to_numeric(group["transferred_data_mbits"], errors="coerce").sum()),
                "mean_transfer_duration_sec": float(pd.to_numeric(group["transfer_duration_sec"], errors="coerce").mean()),
                "p95_transfer_duration_sec": float(pd.to_numeric(group["transfer_duration_sec"], errors="coerce").quantile(0.95)),
                "mean_hop_count_after_transfer": float(pd.to_numeric(group["hop_count_after_transfer"], errors="coerce").mean()),
                "ground_station_origin_share_percent": 100.0 * float(group["from_ground_station"].mean()),
            }
        )
    return pd.DataFrame(rows)


def write_coverage_and_threshold_tables(
    canonical: pd.DataFrame,
    audit: pd.DataFrame,
    tables: Path,
) -> None:
    complete_case_ids = set(audit.loc[audit["success_marker"], "case_id"])
    success_rows = canonical.loc[canonical["case_id"].isin(complete_case_ids)].copy()
    family_coverage = (
        success_rows.groupby(["family", "status"], dropna=False)
        .agg(
            scenario_ids=("scenario_id", "nunique"),
            cases=("case_id", "nunique"),
            case_scenario_rows=("scenario_id", "size"),
        )
        .reset_index()
        .sort_values(["family", "status"])
    )
    family_coverage.to_csv(tables / "scenario_family_coverage.csv", index=False)

    full_case = audit.loc[
        audit["success_marker"] & (audit["unique_scenario_rows"] == audit["expected_scenario_rows"]),
        "case_id",
    ].iloc[0]
    scenario_reference = (
        canonical.loc[canonical["case_id"] == full_case, ["scenario_id", "family"]]
        .drop_duplicates("scenario_id")
        .set_index("scenario_id")["family"]
        .to_dict()
    )
    expected_ids = set(scenario_reference)
    missing_rows = []
    for case_id in audit.loc[audit["missing_scenario_rows"] > 0, "case_id"]:
        present = set(canonical.loc[canonical["case_id"] == case_id, "scenario_id"].astype(str))
        for scenario_id in sorted(expected_ids - present):
            missing_rows.append(
                {
                    "case_id": case_id,
                    "scenario_id": scenario_id,
                    "family": scenario_reference.get(scenario_id, "unknown"),
                }
            )
    pd.DataFrame(missing_rows).to_csv(tables / "missing_case_scenarios.csv", index=False)

    completed = success_rows.loc[success_rows["status"] == "COMPLETED"]
    threshold_rows = []
    for scenario_id in ["nominal_original", "unlimited_useful_deadline"]:
        group = completed.loc[completed["scenario_id"] == scenario_id]
        threshold_rows.extend(
            [
                {"scenario_id": scenario_id, "criterion": "observation_success_percent >= 80", "count": int((group["observation_success_percent"] >= 80).sum())},
                {"scenario_id": scenario_id, "criterion": "observation_success_percent >= 90", "count": int((group["observation_success_percent"] >= 90).sum())},
                {"scenario_id": scenario_id, "criterion": "s100_before_deadline_percent == 100", "count": int((group["s100_surviving_before_deadline_percent"] == 100).sum())},
                {"scenario_id": scenario_id, "criterion": "mean_coverage_deadline_percent >= 95", "count": int((group["mean_surviving_coverage_deadline_percent"] >= 95).sum())},
                {
                    "scenario_id": scenario_id,
                    "criterion": "observation == 100 and S100 == 100 and mean coverage == 100",
                    "count": int(
                        (
                            (group["observation_success_percent"] == 100)
                            & (group["s100_surviving_before_deadline_percent"] == 100)
                            & (group["mean_surviving_coverage_deadline_percent"] == 100)
                        ).sum()
                    ),
                },
            ]
        )
    pd.DataFrame(threshold_rows).to_csv(tables / "performance_threshold_counts.csv", index=False)

    unlimited = completed.loc[completed["scenario_id"] == "unlimited_useful_deadline"].copy()
    perfect = unlimited.loc[
        (unlimited["observation_success_percent"] == 100)
        & (unlimited["s100_surviving_before_deadline_percent"] == 100)
        & (unlimited["mean_surviving_coverage_deadline_percent"] == 100)
    ].sort_values(
        ["n_satellites", "n_central_nodes", "total_transferred_data_mbits", "p100_t100_surviving_min"]
    )
    perfect.to_csv(tables / "unlimited_perfect_performance_cases.csv", index=False)


def configure_plot_style() -> None:
    sns.set_theme(style="whitegrid", context="talk")
    plt.rcParams.update(
        {
            "figure.dpi": 120,
            "savefig.dpi": 220,
            "font.family": "DejaVu Sans",
            "axes.titleweight": "bold",
            "axes.titlesize": 13,
            "axes.labelsize": 11,
            "legend.fontsize": 9,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
        }
    )


def save_figure(fig: plt.Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_completeness(summary: dict, path: Path, region_label: str) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    axes[0].bar(
        ["Successful", "Incomplete", "Failed markers"],
        [summary["success_markers"], summary["missing_success_markers"], summary["failed_markers"]],
        color=[COLORS["teal"], COLORS["orange"], COLORS["red"]],
    )
    axes[0].set_title("Case-level archive status")
    axes[0].set_ylabel("Cases")
    for patch in axes[0].patches:
        axes[0].text(patch.get_x() + patch.get_width() / 2, patch.get_height() + 8, f"{int(patch.get_height())}", ha="center")
    expected = summary["expected_full_scenario_rows"]
    unique = summary["unique_scenario_rows_all_cases"]
    axes[1].barh(["Expected", "Present (unique)"], [expected, unique], color=[COLORS["light"], COLORS["blue"]])
    axes[1].set_title("Scenario-row coverage")
    axes[1].set_xlabel("Unique case-scenario rows")
    axes[1].set_xlim(0, expected * 1.08)
    for patch in axes[1].patches:
        axes[1].text(patch.get_width() + expected * 0.01, patch.get_y() + patch.get_height() / 2, f"{int(patch.get_width()):,}", va="center")
    fig.suptitle(f"{region_label} Full891 V2 archive completeness", fontsize=15, fontweight="bold")
    fig.tight_layout()
    save_figure(fig, path)


def plot_cn_performance(frame: pd.DataFrame, path: Path, region_label: str) -> None:
    selected = frame.loc[frame["scenario_id"].isin(["nominal_original", "unlimited_useful_deadline"])]
    labels = {"nominal_original": "Nominal constrained", "unlimited_useful_deadline": "Unlimited reference"}
    metrics = [
        ("observation_success_percent", "Observation success (%)"),
        ("s100_surviving_before_deadline_percent", "Strict S100 before deadline (%)"),
        ("mean_surviving_coverage_deadline_percent", "Mean useful-recipient coverage (%)"),
        ("total_transferred_data_mbits", "Transferred data (Mbit/case)"),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(12.5, 8), sharex=True)
    colors = {"nominal_original": COLORS["navy"], "unlimited_useful_deadline": COLORS["orange"]}
    for ax, (metric, ylabel) in zip(axes.flat, metrics):
        for scenario_id, group in selected.groupby("scenario_id"):
            stats = group.groupby("cn_fraction_percent")[metric].agg(["mean", "std", "count"]).reset_index()
            ci = 1.96 * stats["std"] / np.sqrt(stats["count"])
            ax.plot(stats["cn_fraction_percent"], stats["mean"], marker="o", label=labels[scenario_id], color=colors[scenario_id])
            ax.fill_between(stats["cn_fraction_percent"], stats["mean"] - ci, stats["mean"] + ci, alpha=0.15, color=colors[scenario_id])
        ax.set_ylabel(ylabel)
        ax.set_xlabel("Central-node fraction (%)")
        ax.grid(alpha=0.25)
    axes[0, 0].legend(frameon=False)
    n_bases = frame["case_id"].map(base_id).nunique() if not frame.empty else 0
    fig.suptitle(
        f"{region_label}: balanced central-node sensitivity ({n_bases} complete base architectures)",
        fontsize=15,
        fontweight="bold",
    )
    fig.tight_layout()
    save_figure(fig, path)


def plot_factor_effects(group_means: pd.DataFrame, path: Path) -> None:
    selected = group_means.loc[
        (group_means["scenario_id"] == "unlimited_useful_deadline")
        & (group_means["metric"] == "s100_surviving_before_deadline_percent")
    ]
    fig, axes = plt.subplots(2, 3, figsize=(13, 7.5))
    for ax, factor in zip(axes.flat, ARCHITECTURE_FACTORS):
        group = selected.loc[selected["factor"] == factor].sort_values("level")
        ax.plot(group["level"].astype(float), group["mean"], marker="o", color=COLORS["blue"], linewidth=2)
        ax.fill_between(group["level"].astype(float), group["p05"], group["p95"], color=COLORS["blue"], alpha=0.13)
        factor_title = factor.replace("_", " ").title().replace("Cn ", "CN ")
        ax.set_title(factor_title)
        ax.set_ylabel("Mean S100 (%)")
        ax.grid(alpha=0.25)
    axes.flat[-1].axis("off")
    fig.suptitle("Unadjusted architecture main effects on unlimited S100", fontsize=15, fontweight="bold")
    fig.tight_layout()
    save_figure(fig, path)


def plot_policy(frame: pd.DataFrame, path: Path, region_label: str) -> None:
    selected = frame.loc[frame["family"] == "policy_ablation"].copy()
    selected["policy"] = selected["scenario_id"].str.replace("policy_", "", regex=False).str.title()
    metrics = [
        ("observation_success_percent", "Observation success (%)"),
        ("s100_surviving_before_deadline_percent", "S100 before deadline (%)"),
        ("mean_surviving_coverage_deadline_percent", "Mean coverage (%)"),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.3))
    for ax, (metric, ylabel) in zip(axes, metrics):
        order = ["Central", "Peer", "Hybrid"]
        sns.barplot(data=selected, x="policy", y=metric, order=order, ax=ax, color=COLORS["blue"], errorbar=("ci", 95))
        ax.set_xlabel("")
        ax.set_ylabel(ylabel)
        ax.tick_params(axis="x", rotation=20)
    fig.suptitle(f"{region_label}: routing-policy ablation", fontsize=15, fontweight="bold")
    fig.tight_layout()
    save_figure(fig, path)


def plot_task_size(frame: pd.DataFrame, path: Path) -> None:
    selected = frame.loc[frame["family"] == "task_size"].copy()
    metrics = [
        ("observation_success_percent", "Observation success (%)"),
        ("s100_surviving_before_deadline_percent", "S100 before deadline (%)"),
        ("mean_surviving_coverage_deadline_percent", "Mean coverage (%)"),
        ("total_transferred_data_mbits", "Transferred data (Mbit/case)"),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(11.5, 7.8))
    for ax, (metric, ylabel) in zip(axes.flat, metrics):
        stats = selected.groupby("task_size_multiplier")[metric].agg(["mean", "std", "count"]).reset_index()
        ci = 1.96 * stats["std"] / np.sqrt(stats["count"])
        ax.plot(stats["task_size_multiplier"], stats["mean"], marker="o", color=COLORS["orange"], linewidth=2)
        ax.fill_between(stats["task_size_multiplier"], stats["mean"] - ci, stats["mean"] + ci, color=COLORS["orange"], alpha=0.15)
        ax.set_xscale("log", base=2)
        ax.set_xticks([0.5, 1, 2, 4], ["0.5x", "1x", "2x", "4x"])
        ax.set_ylabel(ylabel)
        ax.grid(alpha=0.25)
    fig.suptitle("Task-size sensitivity under unlimited routing", fontsize=15, fontweight="bold")
    fig.tight_layout()
    save_figure(fig, path)


def plot_robustness(summary: pd.DataFrame, path: Path) -> None:
    selected = summary.loc[summary["family"] != "combined_stress"].copy()
    metric = "delta_s100_surviving_before_deadline_percent_mean"
    fig, ax = plt.subplots(figsize=(11.5, 6.2))
    for family, group in selected.groupby("family"):
        group = group.sort_values("failure_level_percent")
        ax.plot(
            group["failure_level_percent"],
            group[metric],
            marker="o",
            linewidth=2,
            label=family.replace("_", " ").title().replace("Cn ", "CN "),
        )
    ax.axhline(0, color="black", linewidth=1)
    ax.set_xlabel("Failure/degradation level (%)")
    ax.set_ylabel("Mean change in S100 vs unlimited baseline (percentage points)")
    ax.set_title("Robustness response relative to the matched no-failure case")
    ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left", frameon=False)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    save_figure(fig, path)


def plot_priority(summary: pd.DataFrame, path: Path, region_label: str) -> None:
    melted = summary.melt(
        id_vars=["scenario_id", "priority_class"],
        value_vars=["observation_success_percent_mean", "s100_before_deadline_percent_mean"],
        var_name="metric",
        value_name="percent",
    )
    melted["scenario"] = melted["scenario_id"].map(
        {"nominal_original": "Nominal constrained", "unlimited_useful_deadline": "Unlimited reference"}
    )
    melted["metric"] = melted["metric"].map(
        {
            "observation_success_percent_mean": "Observation success",
            "s100_before_deadline_percent_mean": "Strict S100",
        }
    )
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), sharey=True)
    for ax, (scenario, group) in zip(axes, melted.groupby("scenario", sort=False)):
        sns.barplot(
            data=group,
            x="priority_class",
            y="percent",
            hue="metric",
            order=["Critical", "High", "Medium", "Low"],
            palette=[COLORS["blue"], COLORS["orange"]],
            ax=ax,
        )
        ax.set_title(scenario)
        ax.set_xlabel("")
        ax.set_ylabel("Mean success (%)")
        ax.legend(frameon=False)
    fig.suptitle(f"{region_label}: priority-class outcomes", fontsize=14, fontweight="bold")
    fig.tight_layout()
    save_figure(fig, path)


def plot_correlation(frame: pd.DataFrame, table_path: Path, figure_path: Path) -> None:
    selected = frame.loc[frame["scenario_id"] == "unlimited_useful_deadline"]
    columns = ARCHITECTURE_FACTORS + [
        "n_central_nodes",
        "observation_success_percent",
        "s100_surviving_before_deadline_percent",
        "mean_surviving_coverage_deadline_percent",
        "total_transferred_data_mbits",
        "n_transfer_events",
        "node_sent_data_gini",
    ]
    numeric = selected[columns].apply(pd.to_numeric, errors="coerce")
    correlation = numeric.corr(method="spearman")
    correlation.to_csv(table_path)
    shown = correlation.loc[ARCHITECTURE_FACTORS + ["n_central_nodes"], columns[6:]]
    fig, ax = plt.subplots(figsize=(9.2, 5.3))
    sns.heatmap(shown, annot=True, fmt=".2f", cmap="vlag", center=0, vmin=-1, vmax=1, ax=ax, cbar_kws={"label": "Spearman rho"})
    ax.set_title("Architecture-performance rank correlations (unlimited reference)")
    ax.set_xlabel("")
    ax.set_ylabel("")
    fig.tight_layout()
    save_figure(fig, figure_path)


def plot_pareto(pareto: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 6.2))
    normal = pareto.loc[~pareto["is_exact_pareto"]]
    frontier = pareto.loc[pareto["is_exact_pareto"]]
    scatter = ax.scatter(
        normal["total_transferred_data_mbits"],
        normal["s100_surviving_before_deadline_percent"],
        s=20 + pd.to_numeric(normal["n_satellites"]) * 0.22,
        c=normal["cn_fraction_percent"],
        cmap="viridis",
        alpha=0.35,
        edgecolors="none",
    )
    ax.scatter(
        frontier["total_transferred_data_mbits"],
        frontier["s100_surviving_before_deadline_percent"],
        s=35 + pd.to_numeric(frontier["n_satellites"]) * 0.25,
        c=frontier["cn_fraction_percent"],
        cmap="viridis",
        edgecolors=COLORS["red"],
        linewidths=1.3,
        label="Exact six-objective Pareto cases",
    )
    cbar = fig.colorbar(scatter, ax=ax)
    cbar.set_label("Central-node fraction (%)")
    ax.set_xlabel("Transferred data (Mbit/case)")
    ax.set_ylabel("Strict S100 before deadline (%)")
    ax.set_title("Performance-resource trade space (marker size = satellites)")
    ax.legend(frameon=False)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    save_figure(fig, path)


def write_json(data: dict, path: Path) -> None:
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def run(input_path: Path, output: Path, work_dir: Path, skip_task_metrics: bool = False) -> dict:
    input_path = input_path.resolve()
    output = output.resolve()
    work_dir = work_dir.resolve()
    if input_path.is_dir() and (output == input_path or input_path in output.parents):
        raise ValueError("The output directory must not be inside the raw California input directory")
    output.mkdir(parents=True, exist_ok=True)
    tables = output / "tables"
    figures = output / "figures"
    tables.mkdir(exist_ok=True)
    figures.mkdir(exist_ok=True)
    configure_plot_style()

    root = resolve_california_root(input_path, work_dir)
    cases = case_directories(root)
    if not cases:
        raise RuntimeError(f"No case directories found under {root}")
    _print(f"Found {len(cases):,} case directories")

    metric_paths = [path for case in cases for path in case.glob("case_metrics_part_*.parquet")]
    raw_metrics = read_parquet_parts(metric_paths)
    raw_metrics["_read_order"] = np.arange(len(raw_metrics), dtype=np.int64)
    duplicate_conflicts = conflicting_duplicate_keys(raw_metrics)
    audit, integrity = audit_archive(root, cases, raw_metrics)
    validation, validation_summary = validation_audit(cases)
    integrity.update(validation_summary)
    integrity["conflicting_duplicate_keys"] = int(len(duplicate_conflicts))

    canonical = (
        raw_metrics.sort_values("_read_order")
        .drop_duplicates(["region", "case_id", "scenario_id"], keep="last")
        .drop(columns="_read_order")
        .reset_index(drop=True)
    )
    complete_case_ids = set(audit.loc[audit["success_marker"], "case_id"])
    scientific = canonical.loc[canonical["case_id"].isin(complete_case_ids)].copy()
    scientific = scientific.loc[scientific["status"] == "COMPLETED"].copy()
    balanced_bases = set(audit.loc[audit["balanced_base"], "base_id"])
    balanced = scientific.loc[scientific["case_id"].map(base_id).isin(balanced_bases)].copy()

    audit.to_csv(tables / "archive_case_audit.csv", index=False)
    duplicate_conflicts.to_csv(tables / "conflicting_duplicate_keys.csv", index=False)
    validation.loc[~validation["passed"].fillna(False)].to_csv(tables / "failed_architecture_validation_checks.csv", index=False)
    canonical.to_parquet(tables / "canonical_case_metrics_all_cases.parquet", index=False, compression="zstd")
    scientific.to_parquet(tables / "canonical_case_metrics_success_cases.parquet", index=False, compression="zstd")
    write_json(integrity, tables / "integrity_summary.json")
    write_coverage_and_threshold_tables(canonical, audit, tables)

    selected_summary = descriptive_summary(
        scientific.loc[scientific["scenario_id"].isin(SELECTED_SCENARIOS)],
        ["scenario_id"],
        PERFORMANCE_METRICS,
    )
    selected_summary.to_csv(tables / "selected_scenario_summary.csv", index=False)

    cn_summary = descriptive_summary(
        balanced.loc[balanced["scenario_id"].isin(["nominal_original", "unlimited_useful_deadline"])],
        ["scenario_id", "cn_fraction_percent"],
        PERFORMANCE_METRICS,
    )
    cn_summary.to_csv(tables / "balanced_cn_fraction_summary.csv", index=False)

    paired = paired_scenario_comparison(scientific)
    paired.to_csv(tables / "paired_nominal_vs_unlimited.csv", index=False)
    paired_summary = descriptive_summary(
        paired.assign(scenario_id="unlimited_minus_nominal"),
        ["scenario_id"],
        [column for column in paired if column.startswith("delta_unlimited_minus_nominal_")],
    )
    paired_summary.to_csv(tables / "paired_nominal_vs_unlimited_summary.csv", index=False)

    priorities = priority_summary(scientific)
    priorities.to_csv(tables / "priority_case_metric_summary.csv", index=False)

    policy = descriptive_summary(
        scientific.loc[scientific["family"] == "policy_ablation"],
        ["scenario_id", "topology", "routing_policy"],
        PERFORMANCE_METRICS,
    )
    policy.to_csv(tables / "policy_ablation_summary.csv", index=False)

    task_size = descriptive_summary(
        scientific.loc[scientific["family"] == "task_size"],
        ["task_size_multiplier"],
        PERFORMANCE_METRICS,
    )
    task_size.to_csv(tables / "task_size_summary.csv", index=False)

    robust_rows, robustness = robustness_tables(scientific)
    robustness.to_csv(tables / "robustness_summary.csv", index=False)
    robustness_by_cn = descriptive_summary(
        robust_rows.loc[robust_rows["status"] == "COMPLETED"],
        ["family", "failure_level_percent", "task_size_multiplier", "cn_fraction_percent"],
        [metric for metric in robust_rows if metric.startswith("delta_")],
    )
    robustness_by_cn.to_csv(tables / "robustness_by_cn_fraction.csv", index=False)

    factor_means, factor_eta = architecture_effects(balanced)
    factor_means.to_csv(tables / "architecture_factor_group_means.csv", index=False)
    factor_eta.to_csv(tables / "architecture_factor_eta_squared.csv", index=False)

    pareto = exact_pareto(scientific)
    pareto.to_csv(tables / "unlimited_pareto_and_equal_weight_ranking.csv", index=False)
    pareto.loc[pareto["is_exact_pareto"]].to_csv(tables / "unlimited_exact_pareto_cases.csv", index=False)
    for scenario_id in ["nominal_original", "unlimited_useful_deadline"]:
        top = scientific.loc[scientific["scenario_id"] == scenario_id].sort_values(
            ["s100_surviving_before_deadline_percent", "observation_success_percent", "mean_surviving_coverage_deadline_percent", "n_satellites", "n_central_nodes"],
            ascending=[False, False, False, True, True],
        )
        top.head(30).to_csv(tables / f"top_30_{scenario_id}.csv", index=False)

    transfer = transfer_summary(root, complete_case_ids)
    transfer.to_csv(tables / "retained_transfer_event_summary.csv", index=False)

    if not skip_task_metrics:
        task_summary, task_failures = task_level_summary(root, complete_case_ids)
        task_summary.to_csv(tables / "retained_task_level_summary.csv", index=False)
        task_failures.to_csv(tables / "nominal_unlimited_task_failure_classes.csv", index=False)
    else:
        task_summary = pd.DataFrame()

    region_values = sorted(scientific.get("region", pd.Series(dtype=str)).dropna().astype(str).unique())
    region_label = ", ".join(region_values) if region_values else input_path.stem
    plot_completeness(integrity, figures / "01_archive_completeness.png", region_label)
    plot_cn_performance(balanced, figures / "02_cn_fraction_performance.png", region_label)
    plot_factor_effects(factor_means, figures / "03_architecture_main_effects.png")
    plot_policy(scientific, figures / "04_policy_ablation.png", region_label)
    plot_task_size(scientific, figures / "05_task_size_sensitivity.png")
    plot_robustness(robustness, figures / "06_robustness_delta_s100.png")
    plot_priority(priorities, figures / "07_priority_outcomes.png", region_label)
    plot_correlation(
        balanced,
        tables / "unlimited_spearman_correlation.csv",
        figures / "08_architecture_performance_correlation.png",
    )
    plot_pareto(pareto, figures / "09_unlimited_pareto_trade_space.png")

    result = {
        "input": str(input_path.resolve()),
        "regional_root": str(root.resolve()),
        "california_root": str(root.resolve()),  # backward-compatible key
        "region": region_label,
        "output": str(output.resolve()),
        **integrity,
        "scientific_completed_rows": int(len(scientific)),
        "balanced_scientific_rows": int(len(balanced)),
        "pareto_cases": int(pareto["is_exact_pareto"].sum()),
        "task_level_summary_created": not task_summary.empty,
        "figures_created": len(list(figures.glob("*.png"))),
        "tables_created": len(list(tables.glob("*"))),
    }
    write_json(result, output / "postprocessing_run_summary.json")
    _print(json.dumps(result, indent=2))
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Regional ZIP or extracted Full891 regional directory")
    parser.add_argument("--output", required=True, help="Directory for tables and figures")
    parser.add_argument(
        "--work-dir",
        default=str(Path(tempfile.gettempdir()) / "full891_california_postprocess_work"),
        help="Temporary extraction directory used only when --input is a ZIP",
    )
    parser.add_argument(
        "--skip-task-metrics",
        action="store_true",
        help="Skip the slower retained task-partition analysis",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    run(
        Path(args.input).expanduser(),
        Path(args.output).expanduser(),
        Path(args.work_dir).expanduser(),
        skip_task_metrics=args.skip_task_metrics,
    )


if __name__ == "__main__":
    main()
