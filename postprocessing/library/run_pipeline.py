from __future__ import annotations

import argparse
import sys
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PACKAGE_ROOT / "src"))

from thesis_postprocessing.pipeline import run_pipeline  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run contract-driven full891 thesis post-processing.")
    parser.add_argument("zip_path", help="Authoritative full891 ZIP (kept read-only).")
    parser.add_argument("--output-root", required=True, help="Separate output directory.")
    parser.add_argument(
        "--profile",
        choices=["core", "validate"],
        default="core",
        help="core builds task/case metrics and supported analyses; validate stops at the gates.",
    )
    parser.add_argument("--skip-archive-hash", action="store_true", help="Skip the slow whole-ZIP SHA-256 pass.")
    args = parser.parse_args()
    return run_pipeline(
        zip_path=Path(args.zip_path),
        output_root=Path(args.output_root),
        profile=args.profile,
        compute_archive_hash=not args.skip_archive_hash,
    )


if __name__ == "__main__":
    raise SystemExit(main())
