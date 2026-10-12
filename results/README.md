# Result evidence

This directory contains the compact, machine-readable evidence used in the final thesis. The multi-gigabyte raw case archive is intentionally excluded from Git.

## Final evidence policy

- `final_results/` contains the matched California-India evidence for the validated 60- and 120-satellite population: 594 cases per region, with 32 California tasks and 773 India tasks.
- `final_corrected_180/` contains the verified India 180-satellite evidence: 282 complete cases, 773 tasks, and a balanced 216-case CN30-CN100 comparison block spanning 27 architecture clusters.
- The common 773-task India population supports matched 120-to-180 comparisons. The thesis still limits those conclusions to the evaluated India workload and architecture grid.
- `historical/option2_exploratory_180/` preserves the superseded 741-task run only as correction provenance. It is excluded from final figures, tables, and claims.
- `california_v2/`, `nominal/`, and `objective_weights/` retain supporting and historical analyses identified explicitly in the thesis.

## Reader workbook

`Thesis_Result_Tables.xlsx` formats the principal completion, regional-effect, Central Node, robustness, timing, Pareto, acceptability, shortlist, and 120-to-180 tables. Every analysis sheet states its canonical source CSV. The CSV/JSON/Parquet files remain the authoritative evidence.

## Main findings represented here

- On matched 60/120-satellite architectures, India has 4.66 percentage points higher observation fulfilment than California, but 2.98 points lower strict useful completion, 5.95 points lower useful coverage, and approximately 175 minutes later worst-case complete dissemination.
- In the matched India 120-to-180 comparison, the 180-satellite cases have 1.23 points higher observation success, 3.99 points higher strict completion, 44.68 minutes lower worst-case dissemination time, and 25,827 Mbit more transferred data per case.
- Central Node participation is policy- and workload-dependent rather than monotonic. Higher participation can improve mission outcomes while communication traffic and differentiated-node count continue to increase.
- Ground-station outage is the largest tested individual vulnerability; combined stresses expose losses that nominal analysis misses.
- Exact Pareto filtering and rank-acceptability analysis support a conditional portfolio, not one universal architecture.

See `../REPRODUCIBILITY.md` for commands, expected inputs, tests, and integrity checks.
