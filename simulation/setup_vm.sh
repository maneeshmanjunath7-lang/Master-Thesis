#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -r requirements-vm.txt
.venv/bin/python -m pip install -e .

echo "Environment ready: $ROOT_DIR/.venv"
.venv/bin/full891 validate --config config/full_campaign.json

