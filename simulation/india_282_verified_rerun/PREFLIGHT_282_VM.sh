#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PYTHON="$ROOT/.venv/bin/python"
FULL891="$ROOT/.venv/bin/full891"
CONFIG="$ROOT/config/india_282_rerun.json"
OUTPUT="$ROOT/outputs/india_282_corrected_773_tasks_vm_contract_v2"
mkdir -p "$ROOT/logs"

if [[ ! -x "$PYTHON" || ! -x "$FULL891" ]]; then
  echo "Environment missing. Run: bash SETUP_VM.sh" >&2
  exit 2
fi

"$PYTHON" tools/verify_package.py | tee "$ROOT/logs/00_package_verification.log"
"$FULL891" validate --config "$CONFIG" | tee "$ROOT/logs/01_validate_282_vm.log"
"$PYTHON" tools/preflight_282.py --config "$CONFIG" --output-root "$OUTPUT" \
  | tee "$ROOT/logs/02_preflight_282_vm.log"

echo "Preflight passed: 282 cases, 539 scenarios per case, and exactly 773 unique India tasks."
