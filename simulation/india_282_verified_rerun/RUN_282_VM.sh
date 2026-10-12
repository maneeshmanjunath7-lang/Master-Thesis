#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PYTHON="$ROOT/.venv/bin/python"
FULL891="$ROOT/.venv/bin/full891"
CONFIG="$ROOT/config/india_282_rerun.json"
OUTPUT="$ROOT/outputs/india_282_corrected_773_tasks_vm_contract_v2"
mkdir -p "$ROOT/logs"

export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

on_error() {
  echo "The campaign stopped with an error. Do not merge the output. Inspect logs/05_run_282_vm.log." >&2
}
trap on_error ERR

bash "$ROOT/PREFLIGHT_282_VM.sh"
echo "Starting or safely resuming the contract-locked 282-case run."
"$FULL891" full --config "$CONFIG" --output-root "$OUTPUT" \
  | tee "$ROOT/logs/05_run_282_vm.log"

"$PYTHON" tools/verify_rerun.py --config "$CONFIG" --profile full \
  --output-root "$OUTPUT" --report-name verification_282.json \
  | tee "$ROOT/logs/06_verify_282_vm.log"

trap - ERR
echo "FINAL PASS: all 282 cases contain 539 unique scenarios and the stored task rows use all 773 India task IDs."
