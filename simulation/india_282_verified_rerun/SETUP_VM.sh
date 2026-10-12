#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
"$PYTHON_BIN" tools/verify_package.py

if [[ ! -x .venv/bin/python ]]; then
  "$PYTHON_BIN" -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-vm-lock.txt
.venv/bin/python -m pip install -e .
.venv/bin/python -m pytest
.venv/bin/python tools/verify_package.py

echo "Setup, tests, and post-install package verification passed. Next run: bash PREFLIGHT_282_VM.sh"
