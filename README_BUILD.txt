MANEESH MANJUNATH — MASTER'S THESIS WORKING DRAFT
Updated: 2 September 2026

Build and check from the package root:

  python checks/check_report.py
  tectonic --keep-logs main.tex

Evidence included:
- Earlier nominal baseline: 891 California + 891 India cases; 17,820 metric recomputations passed at 1e-6.
- Fresh V2 California: 891 identifiers, 890 complete cases, 539 scenarios/case, validated robustness and task-size replay.
- Fresh V2 objective analysis: exact Pareto filtering and 10,000 randomized weight draws (seed 20260825).

Evidence still pending:
- Fresh V2 India validation and paired regional analysis.
- Observation-radius sensitivity.
- Separate Fresh V2 first-reception extraction, if retained in the final RQ wording.

The Git repository contains compact CSV/JSON evidence and all report figures. It intentionally excludes the multi-gigabyte raw Parquet archives and credential-bearing acquisition scripts.
