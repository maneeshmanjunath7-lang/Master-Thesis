#!/usr/bin/env python3
"""Write SHA-256 records for compact evidence, report figures, and the PDF."""

from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / "Master_Thesis_Working_Draft_2026-09-02.pdf"


def main() -> None:
    files = sorted(p for folder in (ROOT / "data", ROOT / "figures") for p in folder.rglob("*") if p.is_file())
    files.append(PDF)
    lines = ["# SHA-256 manifest for the evidence bundled with this report"]
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(ROOT).as_posix()}")
    (ROOT / "evidence_manifest.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(files)} hashes")


if __name__ == "__main__":
    main()
