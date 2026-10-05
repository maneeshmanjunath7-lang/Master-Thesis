# Master Thesis Reproducibility Repository

This repository accompanies the thesis **Effect of Central Nodes on Task Dissemination in Distributed Satellite Systems** by Maneesh Manjunath, Technical University of Munich, 2026.

The study evaluates deadline-constrained wildfire follow-up tasks in distributed satellite systems. It connects Sentinel-3-derived task generation, Walker-constellation simulation, temporal routing, task reception, later observation opportunities, failure replay, and multi-objective post-processing.

## Repository contents

| Directory | Contents |
|---|---|
| `simulation/` | Full891 simulation source, frozen campaign configuration, task input, tests, and VM run scripts |
| `postprocessing/` | Complete regional and cross-region analysis, task-level processing, validation, Pareto, robustness, timing, and rank-acceptability code |
| `figure_generation/` | Deterministic Python/Matplotlib code for the 22 thesis figures and published map sources |
| `results/` | Compact machine-readable evidence tables, manifests, and the consolidated Excel workbook |
| `figures/` | Final PNG and PDF thesis figures, figure manifest, captions, and companion tables |
| `thesis/` | Final thesis PDF and complete LaTeX source |
| `checks/` | Release verification and checksum tools |

## Evidence populations

The repository preserves the evidence boundary used in the thesis.

- The confirmatory layer contains matched California and India results for the validated 60- and 120-satellite population.
- The exploratory layer contains 282 completed India 180-satellite cases evaluated on an internally consistent 741-task population.
- The 180-satellite layer is reported separately because its task population is not interchangeable with the 773-task confirmatory India input.
- No cross-population fleet-size coefficient is claimed.

See `results/final_results/case_task_population_contract.csv`, `results/final_results/data_contract_audit_summary.json`, and Appendix C of the thesis for the validation record and root-cause explanation.

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

Run the unit tests:

```bash
python -m pytest simulation/tests postprocessing/library/tests postprocessing/california_results_analysis/tests postprocessing/complete/tests
```

Validate the Full891 campaign definition:

```bash
full891 validate --config simulation/config/full_campaign.json
```

The complete campaign is computationally expensive. Read `simulation/README.md` and `REPRODUCIBILITY.md` before starting it.

## Post-processing

The complete post-processor accepts extracted regional result folders or regional ZIP archives:

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

The final compact tables used in the report are already provided under `results/`.

## Figures

Recreate the report figures from the canonical post-processing table:

```bash
python figure_generation/generate_thesis_figures.py \
  --analysis-root /path/to/postprocessing_output \
  --tasks-csv simulation/input_tasks/all_sentinel3_tasks_with_priority_score_deduplicated.csv \
  --output regenerated_figures
```

The California outline is from the US Census Bureau 2025 cartographic boundary files. The India outline is from Natural Earth 1:50 million cultural vectors. Source and version details are in `figure_generation/map_data/SOURCES.md`.

## Result tables

`results/Thesis_Result_Tables.xlsx` is the reader-oriented workbook. The CSV, JSON, and Parquet files remain the authoritative machine-readable evidence. The workbook does not replace them.

## Raw campaign archive

The multi-gigabyte raw VM campaign archive is not committed to GitHub. It contains millions of Parquet partitions and temporary execution records. The repository instead includes the frozen inputs, exact source code, configuration, compact evidence tables, validation manifests, and checksums needed to trace every reported claim. Raw archives can be supplied separately for examination.

## Citation

Use the citation metadata in `CITATION.cff` and cite release `v1.0-thesis`.

## License and data sources

Original source code in this repository is released under the MIT License. Third-party packages and source datasets retain their own terms. See `DATA_LICENSE.md` before redistributing source-derived data.
