# Full891 post-processing implementation handoff

## Completed run

- Authoritative input (read-only): `C:\Users\manee\MasterThesis_DSS\VM run final\FINAL_FULL891_ACTUAL_EPOCH_45CPU_20260802 (1).zip`
- Input SHA-256: `55f4a7f2140f8838c35b78e33ea2fe5ffd277f6f90f2caad221a3b64fdf6af6e`
- Generated output: `C:\Users\manee\.codex\visualizations\2026\08\10\019fed1b-2720-7952-a185-973399e027f1\thesis_postprocessing_output`
- Output files: 69
- Output size: approximately 90.6 MB
- Gate A: PASS
- Gate B: PASS
- Gate C: PASS
- Authoritative cases: 1,782 (891 California + 891 India)
- Canonical task outcomes: 717,255
- Simulator-summary reconciliations: 17,820, with zero failures at tolerance `1e-6`
- QC invariant violations: 0

## Scientific scope

Implemented analyses include architecture-specific 11-point CN sweeps, marginal CN effects, controlled-grid associations, paired regional comparison, non-monetary resource vectors, exact Pareto membership, and 10,000 seeded rank-weight scenarios.

S100 is reconstructed using the simulator's exact useful-recipient definition: satellites with a future observation opportunity before the task deadline. The useful set is read once per orbit family and reused across its 11 CN fractions.

Robustness failure injection, controlled task-size sensitivity, and peer/central/hybrid policy ablation are not claimed from the nominal archive. Their targeted plan is in `13_additional_runs_plan/additional_runs_plan.md`.

## Storage decision

The very large communication-window and observation-opportunity members remain authoritative inside the ZIP. The core profile writes an external-member index and materializes the task/case/network tables needed by the supported analyses. This avoids duplicating more data than the drive's free space permits.

## Re-run

Use the Python environment documented in `README.md`, then run:

```powershell
python -B run_pipeline.py `
  "C:\Users\manee\MasterThesis_DSS\VM run final\FINAL_FULL891_ACTUAL_EPOCH_45CPU_20260802 (1).zip" `
  --output-root "<separate-writable-output-folder>" `
  --profile core
```

Do not point `--output-root` inside the raw campaign or ZIP location.
