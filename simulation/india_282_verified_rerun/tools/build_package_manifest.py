from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {
    ".venv", ".pytest_cache", "__pycache__", "outputs", "logs",
    "test_temp", "preflight_probe_output",
}


def is_excluded(relative: Path) -> bool:
    return any(
        part in EXCLUDED_PARTS
        or part.endswith(".egg-info")
        or part == ".india_282_vm.pid"
        for part in relative.parts
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    files = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.name == "PACKAGE_MANIFEST.json":
            continue
        relative = path.relative_to(ROOT)
        if is_excluded(relative):
            continue
        files.append({
            "path": relative.as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        })

    manifest = {
        "package": ROOT.name,
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "purpose": "Ubuntu VM rerun of the 282 audited India cases with a contract-locked 773-task population",
        "software_version": "2.2.2-india282-vm-contract-fix",
        "fix": "Reset the regional index, require exact 773-task identity, reject incompatible 741-task checkpoints, require a verified canary, and separate generated installation/PID metadata from immutable package files.",
        "source_campaign_archive_sha256": "b54fabc0278830913ade88aa8fa188dc89de65b2ddaf141370e9396a0905b45f",
        "task_input_sha256": "e717a1dde84db0f47efdda09cfca2f437909349b25a139a45a7c1928da80e533",
        "case_allowlist_sha256": "053f74ab92b5e382b1cd9547a5cc3454532770cb583a2a9566904d892f6902ab",
        "expected_cases": 282,
        "expected_scenarios_per_case": 539,
        "files": files,
    }
    target = ROOT / "PACKAGE_MANIFEST.json"
    target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
