# Full891 California laptop post-processing

This package runs natively on Windows 11; WSL is not required. It accepts either a `California.zip` archive or an extracted `California/` result directory from the Full891 v2 simulation. Raw results are treated as read-only and generated files are written to a separate output directory.

The default workflow includes retained task-level metrics. Use case-only mode only for a quick preliminary pass.

## Before transferring the California data

On the Ubuntu VM, measure the completed region first:

```bash
cd /home/maneeshmanjunath/Downloads/Maneesh/thesis_full_simulation_v2
du -sh outputs/full891_fresh_complete/runs/California
```

Create the archive after California has completed:

```bash
cd outputs/full891_fresh_complete/runs
zip -r California_full891_results.zip California
sha256sum California_full891_results.zip
```

Transfer `California_full891_results.zip` to the laptop. Keep enough space for the ZIP, its uncompressed contents, analysis outputs, and a 3 GB working reserve. The analyzer checks this before extraction and stops safely if space is insufficient.

## One-time Windows setup

Open PowerShell in this package directory:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\SETUP_WINDOWS.ps1
```

This creates an isolated `.venv-post` environment and installs the required numerical, Parquet, statistical, and plotting libraries. It does not modify the simulation environment.

## Complete California analysis

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\RUN_POSTPROCESSING_WINDOWS.ps1 `
  -InputPath "C:\Data\California_full891_results.zip" `
  -OutputPath "C:\Users\manee\Full891_Postprocessing\California_complete"
```

Alternatively, drag the ZIP or extracted `California` folder onto `RUN_POSTPROCESSING_WINDOWS.cmd`. This uses a timestamped output directory under `C:\Users\manee\Full891_Postprocessing`.

The Windows runner deletes only its own temporary extraction directory after a successful run. Add `-KeepExtracted` to retain it, or `-CaseOnly` for a fast pass that skips retained task metrics.

## Scientific outputs

The output contains:

- campaign completeness, duplicate, failure, scenario-count, configuration-fingerprint, and architecture-validation audits;
- canonical de-duplicated case metrics;
- balanced central-node-fraction response tables;
- nominal-versus-unlimited paired comparisons;
- architecture-factor means and unadjusted eta-squared effects;
- routing-policy and task-size sensitivity summaries;
- paired robustness deltas by failure family, severity, and CN fraction;
- priority-stratified success, latency, completion, and failure summaries;
- retained transfer-event summaries;
- exact Pareto membership, illustrative equal-weight ranking, thresholds, and top-30 tables;
- Pearson/Spearman architecture-performance correlations;
- nine publication-ready figures.

Start with:

1. `postprocessing_run_summary.json`
2. `tables/integrity_summary.json`
3. `tables/selected_scenario_summary.csv`
4. `tables/balanced_cn_fraction_summary.csv`
5. `tables/paired_nominal_vs_unlimited_summary.csv`
6. `tables/robustness_summary.csv`
7. `tables/unlimited_exact_pareto_cases.csv`
8. `figures/`

## Interpretation boundaries

- Scientific aggregates use only cases with `_SUCCESS.json`.
- Resume duplicates are removed by `(region, case_id, scenario_id)` with deterministic keep-last logic.
- CN curves use only base architectures with all 11 CN fractions.
- Robustness scenarios are paired with `unlimited_useful_deadline`, matching their routing defaults.
- S100 requires every surviving useful target to receive the complete task before its deadline.
- Conditional latency must be interpreted beside completion/censoring percentages.
- Exact Pareto membership is preference-free; the equal-weight score is only illustrative.
- Resource quantities are not monetary cost without traceable cost coefficients.
- California is a case study. Matched cross-region conclusions must wait for India.

## Direct Python command and verification

```powershell
.\.venv-post\Scripts\python.exe .\postprocess_california.py `
  --input "C:\Data\California_full891_results.zip" `
  --output "C:\Users\manee\Full891_Postprocessing\California_complete"

.\.venv-post\Scripts\python.exe -m unittest discover -s tests -v
```
