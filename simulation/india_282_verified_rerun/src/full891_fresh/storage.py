from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pandas as pd


GIB = 1024 ** 3


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_atomic(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    os.replace(temporary, path)


def write_parquet_atomic(frame: pd.DataFrame, path: Path, compression: str = "zstd") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    frame.to_parquet(temporary, index=False, compression=compression)
    os.replace(temporary, path)


def existing_scenario_ids(case_directory: Path) -> set[str]:
    ids: set[str] = set()
    if not case_directory.exists():
        return ids
    for path in sorted(case_directory.glob("case_metrics_part_*.parquet")):
        try:
            frame = pd.read_parquet(path, columns=["scenario_id"])
            ids.update(frame["scenario_id"].astype(str))
        except Exception:
            continue
    return ids


def verified_scenario_ids(case_directory: Path, expected_case_id: str) -> set[str]:
    """Read checkpoint partitions without silently accepting damage or duplicates.

    A case-metric partition is the transaction marker for a completed batch.  Any
    unreadable partition, duplicate scenario, wrong case identifier, or unfinished
    status makes resume unsafe and therefore aborts the run.
    """
    ids: set[str] = set()
    allowed_statuses = {"COMPLETED", "NOT_APPLICABLE"}
    for path in sorted(case_directory.glob("case_metrics_part_*.parquet")):
        try:
            frame = pd.read_parquet(path, columns=["case_id", "scenario_id", "status"])
        except Exception as exc:
            raise RuntimeError(f"Unreadable checkpoint partition {path}: {exc}") from exc
        if frame.empty:
            raise RuntimeError(f"Empty checkpoint partition: {path}")
        case_ids = set(frame["case_id"].astype(str))
        if case_ids != {expected_case_id}:
            raise RuntimeError(
                f"Checkpoint {path} contains case IDs {sorted(case_ids)}, expected {expected_case_id}"
            )
        statuses = set(frame["status"].astype(str))
        invalid_statuses = sorted(statuses - allowed_statuses)
        if invalid_statuses:
            raise RuntimeError(
                f"Checkpoint {path} contains unfinished statuses: {invalid_statuses}"
            )
        part_ids = frame["scenario_id"].astype(str).tolist()
        if len(part_ids) != len(set(part_ids)):
            raise RuntimeError(f"Checkpoint {path} contains duplicate scenario IDs")
        overlap = sorted(ids.intersection(part_ids))
        if overlap:
            raise RuntimeError(
                f"Scenario IDs occur in more than one checkpoint for {expected_case_id}: {overlap[:10]}"
            )
        ids.update(part_ids)
    return ids


class DiskBudget:
    def __init__(self, output_root: Path, maximum_output_gb: float, minimum_free_gb: float):
        self.output_root = output_root
        self.maximum_output_bytes = int(maximum_output_gb * GIB)
        self.minimum_free_bytes = int(minimum_free_gb * GIB)

    def output_bytes(self) -> int:
        if not self.output_root.exists():
            return 0
        return sum(path.stat().st_size for path in self.output_root.rglob("*") if path.is_file())

    def check(self) -> dict[str, float]:
        self.output_root.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(self.output_root)
        output_size = self.output_bytes()
        status = {
            "output_gb": output_size / GIB,
            "free_gb": usage.free / GIB,
            "total_gb": usage.total / GIB,
        }
        if output_size >= self.maximum_output_bytes:
            raise RuntimeError(
                f"Output safety limit reached: {status['output_gb']:.2f} GB >= "
                f"{self.maximum_output_bytes/GIB:.2f} GB"
            )
        if usage.free <= self.minimum_free_bytes:
            raise RuntimeError(
                f"Free-space safety reserve reached: {status['free_gb']:.2f} GB <= "
                f"{self.minimum_free_bytes/GIB:.2f} GB"
            )
        return status

