#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PYTHON="$ROOT/.venv/bin/python"
FULL891="$ROOT/.venv/bin/full891"
CONFIG="$ROOT/config/india_282_rerun.json"
OUTPUT="$ROOT/outputs/canary_india_773_vm_contract_v2"
mkdir -p "$ROOT/logs"

export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

bash "$ROOT/PREFLIGHT_282_VM.sh"
echo "Running the mandatory one-case canary. The 282-case run cannot start until this passes."
"$FULL891" smoke --config "$CONFIG" --output-root "$OUTPUT" \
  | tee "$ROOT/logs/03_canary_run.log"
"$PYTHON" tools/verify_rerun.py --config "$CONFIG" --profile smoke \
  --output-root "$OUTPUT" --report-name canary_verification.json \
  | tee "$ROOT/logs/04_canary_verify.log"

echo "Canary PASS. It is now safe to start the full 282-case run."
