from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {
    ".venv", ".pytest_cache", "__pycache__", "outputs", "logs",
    "test_temp", "preflight_probe_output",
}


def is_excluded(relative: Path) -> bool:
    """Return True only for runtime/build artifacts that are safe to regenerate."""
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
    parser = argparse.ArgumentParser(description="Verify the immutable VM package files")
    parser.parse_args()
    manifest_path = ROOT / "PACKAGE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {entry["path"]: entry for entry in manifest["files"]}
    errors: list[str] = []
    for relative, entry in expected.items():
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"missing: {relative}")
            continue
        actual = sha256(path)
        if actual != entry["sha256"]:
            errors.append(f"hash mismatch: {relative}")
    actual_files = set()
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.name == "PACKAGE_MANIFEST.json":
            continue
        relative = path.relative_to(ROOT)
        if is_excluded(relative):
            continue
        if path.name == ".gitkeep":
            continue
        actual_files.add(relative.as_posix())
    unexpected = sorted(actual_files - set(expected))
    if unexpected:
        errors.extend(f"unexpected immutable file: {path}" for path in unexpected)
    result = {
        "status": "PASS" if not errors else "FAIL",
        "package": manifest.get("package"),
        "software_version": manifest.get("software_version"),
        "verified_files": len(expected),
        "errors": errors,
    }
    print(json.dumps(result, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
