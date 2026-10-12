# Master Thesis Reproducibility Repository

This repository accompanies the thesis **Effect of Central Nodes on Task Dissemination in Distributed Satellite Systems** by Maneesh Manjunath, Technical University of Munich, submitted on 14 October 2026.

The study evaluates deadline-constrained wildfire follow-up tasks in distributed satellite systems. It connects Sentinel-3-derived task generation, Walker-constellation simulation, temporal routing, observation opportunities, failure replay, and multi-objective post-processing.

## Repository contents

| Directory | Contents |
|---|---|
| `simulation/` | Full891 simulation source plus the independently runnable, contract-verified 282-case India rerun package |
| `postprocessing/` | Regional, cross-region, robustness, timing, Pareto, rank-acceptability, and corrected 180-satellite analysis code |
| `figure_generation/` | Deterministic Python/Matplotlib figure code and published map sources |
| `results/` | Canonical evidence tables, manifests, historical audit material, and the consolidated Excel workbook |
| `figures/` | Final thesis figures and companion tables |
| `thesis/` | Final thesis PDF and complete compilable LaTeX source |
| `checks/` | Release verification and checksum tools |

## Final evidence populations

- The matched regional block contains 594 California and 594 India cases for the validated 60- and 120-satellite population.
- The verified India 180-satellite block contains all 282 audited cases on the corrected 773-task population.
- The matched 120-to-180 analysis uses 216 balanced CN30-CN100 comparisons across 27 architecture clusters.
- A superseded 741-task exploratory run is retained only under `results/historical/` to document the corrected task-index alignment defect. It is excluded from all final findings.

The principal 120-to-180 effects are +1.23 percentage points in observation success, +3.99 points in strict completion, 44.68 minutes lower worst-case dissemination time, and 25,827 Mbit more transferred data per case. These effects describe the evaluated India workload and architecture grid, not a universal fleet-scaling law.

## Quick start

Python 3.11 or 3.12 is recommended.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r environment/requirements.txt
python -m pip install -e simulation
python -m pip install -e postprocessing/library
```

On Windows, activate the environment with `.venv\Scripts\activate`.

Run the release checks:

```bash
python checks/verify_release.py
```

Run the unit tests:

```bash
python -m pytest simulation/tests postprocessing/library/tests postprocessing/california_results_analysis/tests postprocessing/complete/tests
python -m pytest simulation/india_282_verified_rerun/tests
```

## Simulation and post-processing

The original campaign remains under `simulation/`. The corrected 282-case India package is under `simulation/india_282_verified_rerun/`; its `README_VM.md` gives the preflight, canary, background-run, resume, verification, and export commands.

The general post-processing entry point is:

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

The corrected India 180-satellite outputs are processed with:

```bash
python postprocessing/corrected_180/postprocess_corrected_180.py \
  --simulation-root /path/to/corrected_180_simulation \
  --report-root /path/to/final_report_source
```

## Result tables and figures

`results/Thesis_Result_Tables.xlsx` is the reader-oriented workbook. Its summary and analysis sheets point to the canonical CSV sources. The CSV, JSON, and Parquet files remain the authoritative machine-readable evidence.

Final plot files are under `figures/`. Reusable generators are under `figure_generation/`; user-facing figure terminology consistently uses **Central Node**.

## Raw campaign archive

The multi-gigabyte raw VM campaign archive is not committed to GitHub because it contains millions of Parquet partitions and temporary execution records. This release instead contains the frozen inputs, exact software, corrected rerun package, compact evidence tables, validation manifests, report source, and checksums needed to trace the reported claims. Raw archives can be supplied separately for examination.

## Citation

Use the metadata in `CITATION.cff` and cite release `v1.1-thesis-final`.

## License and data sources

Original source code in this repository is released under the MIT License. Third-party packages and source datasets retain their own terms. See `DATA_LICENSE.md` before redistributing source-derived data.
