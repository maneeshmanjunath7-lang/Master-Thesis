#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PID_FILE="$ROOT/logs/india_282_vm.pid"
CONSOLE_LOG="$ROOT/logs/india_282_vm_console.log"
mkdir -p "$ROOT/logs"

if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE")"
  if kill -0 "$OLD_PID" 2>/dev/null; then
    echo "The campaign is already running with PID $OLD_PID."
    exit 0
  fi
fi

nohup bash "$ROOT/RUN_282_VM.sh" >>"$CONSOLE_LOG" 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"
sleep 2
if ! kill -0 "$PID" 2>/dev/null; then
  echo "The process exited during startup. Inspect $CONSOLE_LOG" >&2
  exit 3
fi

echo "Started the verified 282-case campaign in the background with PID $PID."
echo "Monitor it with: bash STATUS_282_VM.sh"
