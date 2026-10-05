from __future__ import annotations

import hashlib
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "checks" / "SHA256SUMS.txt"
MAX_FILE_BYTES = 95 * 1024 * 1024
REQUIRED = (
    "README.md",
    "CITATION.cff",
    "LICENSE",
    "REPRODUCIBILITY.md",
    "simulation/pyproject.toml",
    "postprocessing/complete/postprocess_complete.py",
    "figure_generation/generate_thesis_figures.py",
    "results/README.md",
    "results/Thesis_Result_Tables.xlsx",
    "results/final_results/completion_gate.csv",
    "results/option2_exploratory_180/exploratory_180_scope_gate.csv",
    "thesis/Master_Thesis_Maneesh_Manjunath_2026.pdf",
    "thesis/latex/main.tex",
)
FORBIDDEN_COMPONENTS = {".venv", "venv", "__pycache__", "raw_outputs", "outputs", "_runtime_deps"}
TEXT_SUFFIXES = {".py", ".sh", ".ps1", ".json", ".toml", ".yaml", ".yml", ".md", ".txt", ".tex"}
SECRET_ASSIGNMENT = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*['\"][A-Za-z0-9_./+\-=]{16,}['\"]"
)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main() -> int:
    failures: list[str] = []
    checked = 0
    for relative in REQUIRED:
        if not (ROOT / relative).is_file():
            failures.append(f"required file missing: {relative}")

    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        relative = path.relative_to(ROOT).as_posix()
        if path.stat().st_size > MAX_FILE_BYTES:
            failures.append(f"file exceeds 95 MiB release limit: {relative}")
        if any(part in FORBIDDEN_COMPONENTS for part in path.relative_to(ROOT).parts):
            failures.append(f"forbidden generated/raw path: {relative}")
        if path.suffix.lower() in TEXT_SUFFIXES:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if SECRET_ASSIGNMENT.search(text):
                failures.append(f"possible embedded credential: {relative}")

    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        expected, relative = line.split("  ", 1)
        path = ROOT / relative
        checked += 1
        if not path.is_file():
            failures.append(f"missing: {relative}")
        elif digest(path) != expected:
            failures.append(f"changed: {relative}")
    if failures:
        print("Release verification failed:")
        print("\n".join(failures))
        return 1
    print(f"Release verification passed for {checked} checksummed files and {len(REQUIRED)} required files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
