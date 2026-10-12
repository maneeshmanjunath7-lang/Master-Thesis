from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from verify_rerun import verify


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify and zip the corrected India case layer")
    parser.add_argument("--config", default="config/india_282_rerun.json")
    parser.add_argument("--output-root", default="outputs/india_282_corrected_773_tasks_vm_contract_v2")
    parser.add_argument("--archive-base", default="india_282_corrected_773_tasks_vm_contract_v2")
    args = parser.parse_args()

    report = verify(Path(args.config), Path(args.output_root), "full")
    if report["status"] != "PASS":
        print(json.dumps(report, indent=2))
        raise SystemExit("Verification failed; refusing to export an incomplete repair layer")

    output_root = Path(args.output_root).resolve()
    archive_base = Path(args.archive_base).resolve()
    archive_path = shutil.make_archive(str(archive_base), "zip", root_dir=output_root)
    print(json.dumps({"status": "PASS", "archive": archive_path}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
