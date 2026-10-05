# Result evidence

This directory contains the compact, machine-readable evidence used in the final thesis. The multi-gigabyte raw case archive is intentionally excluded from Git.

## Final evidence policy

- `final_results/` is the confirmatory California–India layer. Regional comparisons and the main architecture shortlist use the matched 60/120-satellite population: 594 cases per region, with 32 California tasks and 773 India tasks.
- `option2_exploratory_180/` is the official exploratory India 180-satellite layer. It contains 282 computationally complete cases on one internally consistent 741-task population. Architecture-level aggregate inference uses the balanced 216-case CN30–CN100 block.
- The two populations are not pooled to estimate a pure 120-to-180 fleet-size effect or an 180-satellite California–India contrast.
- `california_v2/`, `nominal/`, and `objective_weights/` retain supporting and historical analyses that are identified explicitly in the thesis.

## Reader workbook

`Thesis_Result_Tables.xlsx` formats the principal completion, regional-effect, Central Node, robustness, timing, Pareto, acceptability, and shortlist tables. Every sheet states its canonical source CSV. The CSV/JSON/Parquet files remain the authoritative evidence.

## Main findings represented here

- On matched 60/120-satellite architectures, India has 4.66 percentage points higher observation fulfilment than California, but 2.98 points lower strict useful completion, 5.95 points lower useful coverage, and approximately 175 minutes later worst-case complete dissemination.
- Central Node participation is policy- and workload-dependent rather than monotonic. Higher participation can improve the unlimited diagnostic while communication traffic continues to increase.
- Packet growth and ground-station outage are major performance and robustness drivers.
- Exact Pareto filtering and rank-acceptability analysis support a conditional portfolio, not one universal architecture.
- The exploratory 180-satellite layer reproduces the performance–traffic tension under its declared 741-task workload and is kept separate from confirmatory regional inference.

See `../REPRODUCIBILITY.md` for commands, expected inputs, tests, and integrity checks.
