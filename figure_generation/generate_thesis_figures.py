"""Command-line entry point for all thesis figures.

The generator reads the canonical table produced by the complete post-processing
pipeline and exports 300 dpi PNG plus vector PDF files.  Full descriptions are
kept in a separate caption guide so the plot area stays clean.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

from analytical_figures import generate_analytical_figures
from explanatory_diagrams import generate_explanatory_diagrams
from thesis_style import FigureWriter, apply_thesis_style


CANONICAL_NAMES = (
    "canonical_completed_rows_all_supplied_regions.parquet",
    "canonical_completed_rows_all_supplied_regions.csv",
    "all_891_postprocessed_master.parquet",
    "all_891_postprocessed_master.csv",
)


def _find_named(root: Path, names: tuple[str, ...]) -> Path | None:
    if root.is_file():
        return root if root.name in names else None
    for name in names:
        direct = root / name
        tables = root / "tables" / name
        if direct.exists():
            return direct
        if tables.exists():
            return tables
    wanted = set(names)
    for current, _, filenames in os.walk(root, onerror=lambda _: None):
        for filename in filenames:
            if filename in wanted:
                return Path(current) / filename
    return None


def _find_all_named(root: Path, name: str) -> list[Path]:
    if root.is_file():
        return []
    found: list[Path] = []
    for current, _, filenames in os.walk(root, onerror=lambda _: None):
        if name in filenames:
            found.append(Path(current) / name)
    return found


def _read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix in {".csv", ".txt"}:
        return pd.read_csv(path, low_memory=False)
    raise ValueError(f"Unsupported table type: {path}")


def _load_canonical(analysis_root: Path) -> tuple[pd.DataFrame, Path]:
    path = _find_named(analysis_root, CANONICAL_NAMES)
    if path is None:
        expected = " or ".join(CANONICAL_NAMES[:2])
        raise FileNotFoundError(
            f"No canonical completed-row table was found below {analysis_root}. "
            f"Run the complete post-processing pipeline first or point --analysis-root to a folder containing {expected}."
        )
    return _read_table(path), path


def _load_tasks(tasks_path: Path | None, analysis_root: Path) -> tuple[pd.DataFrame, Path]:
    if tasks_path is not None:
        if not tasks_path.exists():
            raise FileNotFoundError(f"Task file not found: {tasks_path}")
        return _read_table(tasks_path), tasks_path
    candidates = (
        "all_sentinel3_tasks_with_priority_score_deduplicated.csv",
        "all_sentinel3_tasks.csv",
        "tasks.csv",
    )
    found = _find_named(analysis_root, candidates)
    if found is None:
        raise FileNotFoundError(
            "No task-coordinate CSV was found. Supply it explicitly with --tasks-csv."
        )
    return _read_table(found), found


def _load_priority_summaries(analysis_root: Path) -> pd.DataFrame | None:
    pieces: list[pd.DataFrame] = []
    for path in _find_all_named(analysis_root, "priority_case_metric_summary.csv"):
        table = pd.read_csv(path, low_memory=False)
        if "region" not in table.columns:
            joined = " ".join(path.parts).lower()
            if "california" in joined:
                table["region"] = "California"
            elif "india" in joined:
                table["region"] = "India"
            else:
                continue
        pieces.append(table)
    return pd.concat(pieces, ignore_index=True) if pieces else None


def _load_optional_csv(analysis_root: Path, name: str) -> pd.DataFrame | None:
    path = _find_named(analysis_root, (name,))
    return pd.read_csv(path, low_memory=False) if path is not None else None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the 22 publication-ready figures for the full thesis."
    )
    parser.add_argument(
        "--analysis-root",
        type=Path,
        required=True,
        help="Folder (or file) containing the canonical post-processing table.",
    )
    parser.add_argument(
        "--tasks-csv",
        type=Path,
        default=None,
        help="Task-coordinate CSV. If omitted, the script searches below --analysis-root.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output folder for PNG, PDF, companion tables, and caption guide.",
    )
    parser.add_argument(
        "--only",
        choices=("all", "analysis", "diagrams"),
        default="all",
        help="Generate all figures, only data figures, or only explanatory diagrams.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    apply_thesis_style()
    writer = FigureWriter(args.output)
    canonical_path: Path | None = None
    task_path: Path | None = None
    row_count = 0

    if args.only in {"all", "analysis"}:
        data, canonical_path = _load_canonical(args.analysis_root)
        tasks, task_path = _load_tasks(args.tasks_csv, args.analysis_root)
        row_count = len(data)
        priority_summary = _load_priority_summaries(args.analysis_root)
        task_population_contract = _load_optional_csv(
            args.analysis_root, "case_task_population_contract.csv"
        )
        generate_analytical_figures(
            data,
            tasks,
            writer,
            priority_summary,
            task_population_contract,
        )

    if args.only in {"all", "diagrams"}:
        generate_explanatory_diagrams(writer)

    writer.write_manifest()
    summary = {
        "status": "PASS",
        "figure_count": len(writer.records),
        "analysis_figure_count": sum(r.kind == "analysis" for r in writer.records),
        "diagram_count": sum(r.kind == "diagram" for r in writer.records),
        "canonical_table": str(canonical_path) if canonical_path else None,
        "task_file": str(task_path) if task_path else None,
        "canonical_rows": row_count,
        "output": str(args.output.resolve()),
        "format_rules": {
            "embedded_long_titles": False,
            "raw_identifiers_in_plot": False,
            "raster_export_dpi": 300,
            "vector_pdf": True,
            "captions_external": True,
        },
    }
    (args.output / "RUN_SUMMARY.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise

