#!/usr/bin/env python3
"""Fail-fast checks for the public thesis-report package."""

from __future__ import annotations

import re
import hashlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 20 * 1024 * 1024

REQUIRED = [
    "main.tex",
    "settings.tex",
    "bibliography.bib",
    "chapters/04_methodology.tex",
    "chapters/05_experimental_design.tex",
    "chapters/06_nominal_results.tex",
    "chapters/07_sensitivity.tex",
    "chapters/08_discussion.tex",
    "chapters/09_conclusion.tex",
    "appendices/c_validation.tex",
    "data/california_v2/integrity_summary.json",
    "evidence_manifest.sha256",
]

TEXT_SUFFIXES = {".tex", ".bib", ".md", ".txt", ".py", ".yml", ".yaml", ".json"}
PLACEHOLDER_PATTERNS = [
    re.compile(r"\\todo\s*\{"),
    re.compile(r"Result pending\.", re.IGNORECASE),
    re.compile(r"INSERT[_ -]?ME", re.IGNORECASE),
    re.compile(r"TBD(?![A-Za-z])"),
]
SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]\s*['\"][^'\"]{8,}['\"]"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
]
GRAPHIC_RE = re.compile(r"\\includegraphics(?:\[[^]]*\])?\{([^}]+)\}")
CITE_RE = re.compile(r"\\cite\w*\{([^}]+)\}")
BIB_RE = re.compile(r"@\w+\s*\{\s*([^,\s]+)")


def tracked_files() -> list[Path]:
    skipped = {".git", ".venv", "__pycache__"}
    return [p for p in ROOT.rglob("*") if p.is_file() and not skipped.intersection(p.parts)]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def main() -> int:
    errors: list[str] = []
    for rel in REQUIRED:
        if not (ROOT / rel).is_file():
            errors.append(f"missing required file: {rel}")

    files = tracked_files()
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        if path.stat().st_size > MAX_FILE_BYTES:
            errors.append(f"unexpected file larger than 20 MiB: {rel}")
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = read_text(path)
        if rel == "main.tex" or rel.startswith("chapters/") or rel.startswith("appendices/"):
            for pattern in PLACEHOLDER_PATTERNS:
                if pattern.search(text):
                    errors.append(f"placeholder pattern in {rel}: {pattern.pattern}")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                errors.append(f"possible secret assignment in {rel}: {pattern.pattern}")

    tex_files = [p for p in files if p.suffix.lower() == ".tex"]
    tex = "\n".join(read_text(p) for p in tex_files)
    graphic_dirs = [
        ROOT / "figures",
        ROOT / "figures/nominal",
        ROOT / "figures/california_v2",
        ROOT / "figures/objective_weights",
    ]
    for graphic in GRAPHIC_RE.findall(tex):
        requested = Path(graphic)
        candidates: list[Path] = []
        if requested.suffix:
            candidates.extend(d / requested for d in graphic_dirs)
        else:
            for ext in (".pdf", ".png", ".jpg", ".jpeg"):
                candidates.extend(d / (graphic + ext) for d in graphic_dirs)
        if not any(p.is_file() for p in candidates):
            errors.append(f"missing figure referenced by LaTeX: {graphic}")

    bib_path = ROOT / "bibliography.bib"
    if bib_path.is_file():
        bib_keys = set(BIB_RE.findall(read_text(bib_path)))
        cited = {key.strip() for group in CITE_RE.findall(tex) for key in group.split(",")}
        for key in sorted(cited - bib_keys):
            errors.append(f"citation key absent from bibliography: {key}")

    log_path = ROOT / "main.log"
    if log_path.is_file():
        log = read_text(log_path)
        for marker in ("undefined references", "Citation `", "There were undefined references"):
            if marker.lower() in log.lower():
                errors.append(f"LaTeX log contains: {marker}")

    manifest = ROOT / "evidence_manifest.sha256"
    if manifest.is_file():
        for line_no, line in enumerate(read_text(manifest).splitlines(), start=1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            try:
                expected, rel = line.split("  ", 1)
            except ValueError:
                errors.append(f"invalid evidence manifest line {line_no}")
                continue
            target = ROOT / rel
            if not target.is_file():
                errors.append(f"manifest target missing: {rel}")
                continue
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual.lower() != expected.lower():
                errors.append(f"manifest hash mismatch: {rel}")

    if errors:
        print("THESIS CHECK FAILED")
        for error in sorted(set(errors)):
            print(f"- {error}")
        return 1

    print(f"THESIS CHECK PASSED ({len(files)} files inspected)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
