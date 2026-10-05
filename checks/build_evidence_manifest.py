from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "checks" / "SHA256SUMS.txt"
INCLUDED = ("results", "figures", "simulation/config", "simulation/input_tasks", "thesis")
EXCLUDED_NAMES = {"SHA256SUMS.txt"}
TEXT_SUFFIXES = {
    ".bib", ".cff", ".cls", ".cmd", ".cpg", ".csv", ".html", ".json",
    ".md", ".prj", ".ps1", ".py", ".sh", ".sty", ".tex", ".toml",
    ".txt", ".xml", ".yaml", ".yml",
}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    if path.suffix.lower() in TEXT_SUFFIXES or path.name == ".keep":
        # Git checks text out with platform-dependent line endings. Hash a
        # canonical LF representation so the manifest verifies on Windows and
        # Linux without weakening byte-level checks for binary evidence.
        data = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        value.update(data)
    else:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                value.update(block)
    return value.hexdigest()


def main() -> None:
    files: list[Path] = []
    for relative in INCLUDED:
        target = ROOT / relative
        if target.is_file():
            files.append(target)
        elif target.exists():
            files.extend(path for path in target.rglob("*") if path.is_file())
    files = sorted(path for path in files if path.name not in EXCLUDED_NAMES)
    lines = [f"{digest(path)}  {path.relative_to(ROOT).as_posix()}" for path in files]
    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(lines)} checksums to {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
