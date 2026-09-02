# Master Thesis Report

Working thesis report for **Effect of Central Nodes on Task Allocation in Distributed Satellite Systems for Wildfire Detection**.

The report separates two validated evidence layers:

- an earlier nominal campaign with 891 California and 891 India architectures, used for the two-region baseline;
- Fresh Full891 V2 California, with 890 complete 539-scenario cases, used for policy, task-size, failure, Pareto, and objective-weight analysis.

Fresh V2 India and the observation-radius sensitivity are still pending. The report does not infer those results from California.

## Current report

The compiled working draft is `Master_Thesis_Working_Draft_2026-09-02.pdf`. It is suitable for supervisor review but is not the final administrative submission: examiner, official date, declarations, and the remaining Fresh V2 evidence still need confirmation.

## Build

Install [Tectonic](https://tectonic-typesetting.github.io/) and run:

```bash
python checks/check_report.py
tectonic --keep-logs main.tex
```

The repository contains the TUM class/style files needed by the source. Continuous integration runs the checker and builds the PDF on every push and pull request.

## Repository contents

- `chapters/`, `appendices/`: thesis source;
- `figures/nominal/`: earlier validated two-region figures;
- `figures/california_v2/`: Fresh V2 California figures;
- `figures/objective_weights/`: weight-sensitivity figures;
- `data/`: compact CSV/JSON evidence used in the report;
- `checks/check_report.py`: structural, figure, placeholder, size, and secret-pattern checks;
- `tum/` and root TUM style files: portable report template assets.

## Evidence and data boundary

The multi-gigabyte raw Parquet campaign output is intentionally not committed to Git. Compact validated tables and figures are included so the reported values can be audited. The raw simulation release should be archived separately with its manifest, environment, code commit, and SHA-256 checksum.

No service credentials or raw download scripts are included in this report repository.

## Main current result

For Fresh V2 California, the balanced performance/resource method selects:

`T060_P10_H0800_I0986_CN040_F1`

It uses 60 satellites and 24 central nodes and achieves 96.875% observation, 100% strict useful-recipient completion, 100% useful coverage, P100 full-dissemination time of 66.10 minutes, and 888.5 Mbit in the unlimited useful-deadline diagnostic. This is a conditional California recommendation, not a universal optimum.
