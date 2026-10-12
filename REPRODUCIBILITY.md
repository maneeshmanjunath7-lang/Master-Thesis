# Reproducibility guide

## Reproduction levels

The release supports three levels of verification.

1. **Inspect the published evidence.** Open `results/Thesis_Result_Tables.xlsx`, the canonical CSV/JSON/Parquet tables, the figure set, and the validation manifests.
2. **Re-run post-processing and figures.** Supply the extracted California, India, and corrected 180-satellite archives to the published scripts.
3. **Re-run the simulations.** Execute the frozen regional campaign or the independent 282-case India rerun on a sufficiently provisioned Ubuntu VM.

## Frozen regional campaign

The campaign definition is `simulation/config/full_campaign.json`. The frozen task source is `simulation/input_tasks/all_sentinel3_tasks_with_priority_score_deduplicated.csv`.

```bash
python -m pip install -r environment/requirements.txt
python -m pip install -e simulation
full891 validate --config simulation/config/full_campaign.json
full891 smoke --config simulation/config/full_campaign.json --output-root outputs/smoke
full891 full --config simulation/config/full_campaign.json
```

The runner is checkpointed. Reusing the same configuration skips only valid completed partitions.

## Corrected India 282-case run

`simulation/india_282_verified_rerun/` is a standalone package for the audited India 180-satellite cases. It enforces 773 unique normalized task identifiers, 539 scenarios per case, and one immutable run-contract hash.

On Ubuntu:

```bash
cd simulation/india_282_verified_rerun
bash SETUP_VM.sh
bash PREFLIGHT_282_VM.sh
bash RUN_CANARY_VM.sh
bash START_282_BACKGROUND_VM.sh
bash STATUS_282_VM.sh
bash VERIFY_282_VM.sh
```

The verifier must report `PASS`, 282 complete cases, zero missing or invalid cases, 539 scenarios per case, and 773 India tasks. Restarting the background command resumes only checkpoints whose case, task, scenario, and run-contract fingerprints match.

## Post-processing

Run the general regional pipeline:

```bash
python postprocessing/complete/postprocess_complete.py \
  --region /path/to/California.zip \
  --region /path/to/India.zip \
  --output postprocessing_output \
  --bootstrap-draws 1000 \
  --weight-samples 10000 \
  --seed 20260910 \
  --collinearity-threshold 0.95
```

Do not use `--skip-task-events` for the final timing analysis; it omits the task-level Kaplan-Meier and restricted-mean timing evidence.

Run the corrected 180-satellite pipeline:

```bash
python postprocessing/corrected_180/postprocess_corrected_180.py \
  --simulation-root /path/to/corrected_180_simulation \
  --report-root /path/to/final_report_source
```

The canonical outputs are published under `results/final_results/` and `results/final_corrected_180/`.

## Evidence boundary

The final matched 60/120-satellite regional block and the corrected India 180-satellite block use the same 773-task India population. This supports the matched 120-to-180 analysis reported in the thesis. The 180-satellite evidence still applies only to the India workload and evaluated architecture grid; it is not used to claim a universal scaling coefficient or an 180-satellite California-India contrast.

The old 741-task run is preserved under `results/historical/` solely to document the index-alignment defect and its correction. It is excluded from the final statistical evidence.

## Integrity checks

Run:

```bash
python checks/verify_release.py
```

The script checks required files, release size limits, forbidden generated paths, possible embedded credentials, and every file recorded in `checks/SHA256SUMS.txt`.
