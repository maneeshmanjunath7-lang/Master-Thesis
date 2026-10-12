#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
CONFIG="$ROOT/config/india_282_rerun.json"
OUTPUT="$ROOT/outputs/india_282_corrected_773_tasks_vm_contract_v2"
PID_FILE="$ROOT/logs/india_282_vm.pid"

if [[ -x "$ROOT/.venv/bin/full891" ]]; then
  "$ROOT/.venv/bin/full891" status --config "$CONFIG" --output-root "$OUTPUT"
fi

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE")"
  if kill -0 "$PID" 2>/dev/null; then
    echo "background_process: RUNNING (PID $PID)"
  else
    echo "background_process: NOT RUNNING (last PID $PID)"
  fi
else
  echo "background_process: no PID file"
fi

echo "memory:"
free -h
echo "disk:"
df -h "$ROOT"
if [[ -f "$ROOT/logs/india_282_vm_console.log" ]]; then
  echo "latest_log_lines:"
  tail -n 20 "$ROOT/logs/india_282_vm_console.log"
fi
