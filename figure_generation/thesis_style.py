"""Shared publication style and export checks for the thesis figures."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import matplotlib as mpl
import matplotlib.pyplot as plt


TUM_BLUE = "#0065BD"
TUM_DARK_BLUE = "#005293"
TUM_LIGHT_BLUE = "#64A0C8"
TUM_ORANGE = "#E37222"
TUM_GREEN = "#A2AD00"
TUM_RED = "#C4071B"
TUM_PURPLE = "#69085A"
TUM_GRAY = "#4D4D4D"
TUM_LIGHT_GRAY = "#DAD7CB"

SERIES_COLORS = [
    TUM_BLUE,
    TUM_ORANGE,
    TUM_GREEN,
    TUM_PURPLE,
    TUM_LIGHT_BLUE,
    TUM_RED,
    TUM_DARK_BLUE,
]


def apply_thesis_style() -> None:
    """Apply a clean TUM-compatible Matplotlib style with readable type sizes."""

    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [
                "Arial",
                "TeX Gyre Heros",
                "Liberation Sans",
                "DejaVu Sans",
            ],
            "font.size": 11.5,
            "axes.labelsize": 12.5,
            "axes.titlesize": 12.5,
            "axes.titleweight": "semibold",
            "xtick.labelsize": 10.5,
            "ytick.labelsize": 10.5,
            "legend.fontsize": 10.0,
            "figure.dpi": 120,
            "savefig.dpi": 300,
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.04,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.edgecolor": TUM_GRAY,
            "axes.linewidth": 0.8,
            "axes.axisbelow": True,
            "axes.grid": False,
            "grid.color": TUM_LIGHT_GRAY,
            "grid.alpha": 0.55,
            "grid.linewidth": 0.65,
            "lines.linewidth": 2.0,
            "lines.markersize": 5.5,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "legend.frameon": False,
        }
    )


def clean_axis(ax: mpl.axes.Axes, *, grid: str | None = "y") -> None:
    if grid:
        ax.grid(True, axis=grid)
    ax.tick_params(direction="out", length=3.5, width=0.8, color=TUM_GRAY)


def panel_label(ax: mpl.axes.Axes, text: str) -> None:
    """Add only a concise panel label; full explanations belong in captions."""

    ax.text(
        0.0,
        1.025,
        text,
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=12.5,
        fontweight="semibold",
        color=TUM_GRAY,
    )


_RAW_NAME_PATTERN = re.compile(
    r"(?:case[_ -]?id|scenario[_ -]?id|nominal_original|unlimited_useful_deadline|"
    r"policy_(?:central|peer|hybrid)|[A-Za-z]:\\|/home/|\.parquet|\.csv)",
    flags=re.IGNORECASE,
)


def _visible_text(fig: mpl.figure.Figure) -> Iterable[str]:
    for item in fig.findobj(match=lambda obj: hasattr(obj, "get_text")):
        try:
            value = item.get_text()
        except Exception:
            continue
        if value:
            yield str(value)


def validate_figure_text(fig: mpl.figure.Figure) -> None:
    """Reject internal identifiers, paths, filenames, and long embedded titles."""

    if getattr(fig, "_suptitle", None) is not None:
        text = fig._suptitle.get_text().strip()
        if text:
            raise ValueError("Do not embed a figure title; keep the full description in LaTeX.")
    for ax in fig.axes:
        title = ax.get_title().strip()
        if len(title) > 32:
            raise ValueError(f"Panel heading is too long: {title!r}")
    for text in _visible_text(fig):
        if _RAW_NAME_PATTERN.search(text):
            raise ValueError(f"Internal name/path leaked into figure text: {text!r}")


@dataclass
class FigureRecord:
    number: int
    stem: str
    kind: str
    caption: str
    source: str
    sample: str
    notes: str = ""


@dataclass
class FigureWriter:
    output_root: Path
    records: list[FigureRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.output_root = Path(self.output_root)
        self.png_dir = self.output_root / "png"
        self.pdf_dir = self.output_root / "pdf"
        self.table_dir = self.output_root / "companion_tables"
        for directory in (self.png_dir, self.pdf_dir, self.table_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        fig: mpl.figure.Figure,
        *,
        number: int,
        stem: str,
        kind: str,
        caption: str,
        source: str,
        sample: str,
        notes: str = "",
    ) -> None:
        validate_figure_text(fig)
        fig.savefig(self.png_dir / f"{number:02d}_{stem}.png", dpi=300)
        fig.savefig(self.pdf_dir / f"{number:02d}_{stem}.pdf")
        plt.close(fig)
        self.records.append(
            FigureRecord(number, stem, kind, caption, source, sample, notes)
        )

    def write_manifest(self) -> None:
        manifest = self.output_root / "FIGURE_MANIFEST.csv"
        with manifest.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "number",
                    "stem",
                    "kind",
                    "png",
                    "pdf",
                    "caption",
                    "source",
                    "sample",
                    "notes",
                ],
            )
            writer.writeheader()
            for row in sorted(self.records, key=lambda r: r.number):
                writer.writerow(
                    {
                        "number": row.number,
                        "stem": row.stem,
                        "kind": row.kind,
                        "png": f"png/{row.number:02d}_{row.stem}.png",
                        "pdf": f"pdf/{row.number:02d}_{row.stem}.pdf",
                        "caption": row.caption,
                        "source": row.source,
                        "sample": row.sample,
                        "notes": row.notes,
                    }
                )

        guide = self.output_root / "CAPTION_GUIDE.md"
        lines = [
            "# Thesis figure caption guide",
            "",
            "The figures intentionally contain no long titles, filenames, case IDs, or raw variable names. "
            "Use the following descriptions as LaTeX captions and adjust chapter references as needed.",
            "",
        ]
        for row in sorted(self.records, key=lambda r: r.number):
            lines.extend(
                [
                    f"## Figure {row.number}: {row.stem.replace('_', ' ').title()}",
                    "",
                    row.caption,
                    "",
                    f"- Source: {row.source}",
                    f"- Sample: {row.sample}",
                    f"- Notes: {row.notes or 'None.'}",
                    "",
                ]
            )
        guide.write_text("\n".join(lines), encoding="utf-8")

