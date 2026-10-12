#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
"$ROOT/.venv/bin/python" tools/verify_rerun.py \
  --config config/india_282_rerun.json \
  --profile full \
  --output-root outputs/india_282_corrected_773_tasks_vm_contract_v2 \
  --report-name verification_282.json
