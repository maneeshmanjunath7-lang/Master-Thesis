# Thesis post-processing: dual-region full891 campaign

This package implements the frozen 11 August 2026 post-processing contract for the authoritative dual-region result ZIP. It reads the archive directly and never extracts into or writes to the raw campaign.

## Safety and scientific gates

The pipeline stops automatically if:

1. Gate A cannot confirm readable input and a separate writable output;
2. Gate B cannot freeze the exact 891-case grid per region with reliable mappings, core roles, statuses, and invariant configurations;
3. Gate C cannot reproduce simulator summary metrics or satisfy event-order/denominator checks.

Ranking, resource tradeoff, and exact Pareto analysis only run after all three gates pass.

## Run

```powershell
python run_pipeline.py `
  "C:\Users\manee\MasterThesis_DSS\VM run final\FINAL_FULL891_ACTUAL_EPOCH_45CPU_20260802 (1).zip" `
  --output-root "C:\Users\manee\OneDrive\Documents\Playground\thesis_postprocessing_output_FULL891_20260811"
```

Use `--profile validate` to stop after inventory and Gate B. Use `--skip-archive-hash` only when a whole-ZIP provenance hash is not required.

## Core-profile storage decision

The ZIP contains roughly 16.9 GB of compressed communication-window files and 8.0 GB of compressed observation-opportunity files. With less than 10 GB free on the drive, duplicating those tables would be unsafe. The core profile therefore:

- materializes canonical cases, tasks, task outcomes, case metrics, and network metrics as Parquet;
- consumes transfer events to reconstruct deadline-aware receiver fraction and S100;
- consumes adjacency matrices for available-vs-used communication summaries;
- writes a row-level source index pointing to every detailed raw ZIP member;
- leaves the giant raw detailed tables authoritative and read-only inside the ZIP.

## Interpretation constraints

- Conditional latency is never reported without completion/censoring information.
- `CN=0` under `central_only` is a no-central-relay condition, not an unrestricted peer-to-peer baseline.
- The resource vector is non-monetary and its weights are declared.
- Pareto membership is exact for the declared objectives; there is no universal optimum.
- Failure robustness and task-size sensitivity are not claimed without controlled replay/reruns.
- The mission-chain claim is limited to post-injection dissemination and follow-up observation.

