#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ ! -x .venv/bin/full891 ]]; then
  echo "Run ./setup_vm.sh first." >&2
  exit 2
fi

mkdir -p logs outputs

# Each architecture already runs in its own process. Prevent NumPy/BLAS from
# creating nested thread pools that compete for the same VM cores and RAM.
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-1}"

echo "1/5 Validating frozen campaign inputs"
.venv/bin/full891 validate --config config/full_campaign.json | tee logs/01_validate.log

echo "2/5 Writing/confirming workload estimate"
.venv/bin/full891 estimate --config config/full_campaign.json | tee logs/02_estimate.log

echo "3/5 Running the four-case end-to-end smoke campaign"
.venv/bin/full891 smoke \
  --config config/full_campaign.json \
  --output-root outputs/smoke_full891_fresh | tee logs/03_smoke.log

echo "4/5 Starting/resuming the complete fresh campaign"
.venv/bin/full891 full --config config/full_campaign.json | tee logs/04_full.log

echo "5/5 Building canonical tables, models, figures and supervisor update"
.venv/bin/full891 analyze --config config/full_campaign.json | tee logs/05_analysis.log

echo "Complete. See outputs/full891_fresh_complete/analysis/"
