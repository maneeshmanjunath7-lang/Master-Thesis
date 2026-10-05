"""Search non-environment Python sources inside a ZIP."""

from __future__ import annotations

import argparse
import zipfile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("zip_path")
    parser.add_argument("pattern")
    parser.add_argument("--context", type=int, default=8)
    args = parser.parse_args()
    with zipfile.ZipFile(args.zip_path) as archive:
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            if not name.endswith(".py") or "/.venv/" in name or "/venv/" in name:
                continue
            text = archive.read(info).decode("utf-8", errors="replace")
            lines = text.splitlines()
            for index, line in enumerate(lines):
                if args.pattern.lower() in line.lower():
                    start = max(0, index - args.context)
                    end = min(len(lines), index + args.context + 1)
                    print(f"\n### {name}:{index + 1}")
                    for line_number in range(start, end):
                        print(f"{line_number + 1:05d}: {lines[line_number]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
