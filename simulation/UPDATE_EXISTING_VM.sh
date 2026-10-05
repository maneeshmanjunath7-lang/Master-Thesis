#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ $# -ne 1 ]]; then
  echo "Usage: $0 /absolute/path/to/existing/thesis_full_simulation_v2" >&2
  exit 2
fi

TARGET_DIR="$(cd "$1" && pwd)"
if [[ "$SOURCE_DIR" == "$TARGET_DIR" ]]; then
  echo "Source and target are the same directory; no update is needed." >&2
  exit 2
fi
if [[ ! -f "$TARGET_DIR/pyproject.toml" || ! -d "$TARGET_DIR/src/full891_fresh" ]]; then
  echo "Target does not look like a Full891 project: $TARGET_DIR" >&2
  exit 2
fi
if pgrep -af "full891[[:space:]]+full" >/dev/null 2>&1; then
  echo "A Full891 full campaign still appears to be running. Stop it before updating." >&2
  exit 3
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$TARGET_DIR/update_backups/$STAMP"
mkdir -p "$BACKUP_DIR"
cp -a "$TARGET_DIR/src" "$BACKUP_DIR/src"
cp -a "$TARGET_DIR/config" "$BACKUP_DIR/config"
for name in pyproject.toml RUN_FULL_VM.sh STATUS_VM.sh setup_vm.sh; do
  if [[ -e "$TARGET_DIR/$name" ]]; then
    cp -a "$TARGET_DIR/$name" "$BACKUP_DIR/$name"
  fi
done

cp -a "$SOURCE_DIR/src/." "$TARGET_DIR/src/"
cp -a "$SOURCE_DIR/config/." "$TARGET_DIR/config/"
mkdir -p "$TARGET_DIR/benchmarks" "$TARGET_DIR/tests"
cp -a "$SOURCE_DIR/benchmarks/." "$TARGET_DIR/benchmarks/"
cp -a "$SOURCE_DIR/tests/." "$TARGET_DIR/tests/"
cp -a "$SOURCE_DIR/pyproject.toml" "$SOURCE_DIR/requirements-vm.txt" "$TARGET_DIR/"
cp -a "$SOURCE_DIR/RUN_FULL_VM.sh" "$SOURCE_DIR/STATUS_VM.sh" "$SOURCE_DIR/setup_vm.sh" "$TARGET_DIR/"
cp -a "$SOURCE_DIR/README.md" "$SOURCE_DIR/CHANGELOG.md" "$SOURCE_DIR/BENCHMARK_RESULTS.md" "$SOURCE_DIR/UPGRADE_AND_RESUME.md" "$TARGET_DIR/"
chmod +x "$TARGET_DIR/RUN_FULL_VM.sh" "$TARGET_DIR/STATUS_VM.sh" "$TARGET_DIR/setup_vm.sh"

if [[ -x "$TARGET_DIR/.venv/bin/python" ]]; then
  "$TARGET_DIR/.venv/bin/python" -m pip install -e "$TARGET_DIR"
else
  echo "No existing virtual environment was found. Run $TARGET_DIR/setup_vm.sh before resuming."
fi

echo "Updated $TARGET_DIR to Full891 v2.1.0"
echo "Backup: $BACKUP_DIR"
echo "Existing outputs and logs were preserved. Follow UPGRADE_AND_RESUME.md to validate and resume."
