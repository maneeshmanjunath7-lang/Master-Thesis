# Reproducibility guide

## Reproduction levels

The repository supports three levels of verification.

1. **Inspect the published evidence.** Open `results/Thesis_Result_Tables.xlsx`, the compact CSV/Parquet tables, the figure manifest, and the validation records.
2. **Re-run post-processing and figures.** Supply extracted California and India archives to the complete post-processor, then regenerate the figures.
3. **Re-run the full simulation.** Execute the frozen 891-case campaign for each region on a sufficiently provisioned Ubuntu VM.

## Frozen simulation

The campaign definition is `simulation/config/full_campaign.json`. The frozen task source is `simulation/input_tasks/all_sentinel3_tasks_with_priority_score_deduplicated.csv`.

Install and validate:

```bash
python -m pip install -r environment/requirements.txt
python -m pip install -e simulation
full891 validate --config simulation/config/full_campaign.json
```

Run a smoke case before the full campaign:

```bash
full891 smoke --config simulation/config/full_campaign.json --output-root outputs/smoke
```

Start or resume the full campaign:

```bash
full891 full --config simulation/config/full_campaign.json
```

The runner is checkpointed. Reusing the same configuration skips valid completed partitions. Input hashes and task-population checks must pass before scientific comparisons are accepted.

## Complete post-processing

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

Do not use `--skip-task-events` for the final timing analysis. It omits the task-level Kaplan-Meier and restricted-mean timing evidence.

## Evidence boundary

The confirmatory 60/120-satellite comparison and the exploratory India 180-satellite layer use different task populations. This is recorded rather than silently normalized away. The exploratory layer is valid for internally matched comparisons among its 282 cases but is not merged into a clean 60-to-120-to-180 scaling coefficient.

## Checksums

Run:

```bash
python checks/verify_release.py
```

The script checks the files recorded in `checks/SHA256SUMS.txt` and rejects missing or changed evidence files.
