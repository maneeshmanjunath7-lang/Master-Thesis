# Core result fields

## Case metrics

- `case_id`, `region`, architectural variables: immutable case identity.
- `scenario_id`, `family`, topology/routing/cutoff fields: exact counterfactual identity.
- `failure_type`, `failure_level_percent`, `replicate`, `scenario_seed_hex`: reproducible perturbation.
- `applicability`: marks CN-failure scenarios as not applicable at CN=0 rather than duplicating the baseline.
- `mean_*_coverage_*_percent`: task-averaged receiver coverage.
- `s100_*_percent`: proportion of tasks reaching every target.
- `p100_t100_*_min`: maximum complete-dissemination latency among completed tasks.
- `t100_completed_task_count_*`, `t100_censored_task_count_*`: completion denominator and censoring.
- `observation_success_percent`: observations performed before their task deadlines.
- `geometry_failure_count`, `communication_failure_count`, `routing_or_timing_failure_count`: mutually exclusive unsuccessful-task classes.
- `n_available_windows`, window capacity/duration, transfer count/data and utilisation: opportunity versus actual communication burden.
- `node_sent_data_gini`: load concentration across surviving satellites.

## Task metrics

- Original and surviving target/reached counts.
- Coverage before deadline and by simulation end.
- S100 flags for both denominators.
- T10 through T100 for both denominators.
- Censoring flags and observation outcome.
- Failure class.

## Status values

- `COMPLETED`: scenario executed and metrics written.
- `NOT_APPLICABLE`: scientifically undefined scenario, currently CN failure at CN=0.
- `_FAILED.json`: case stopped with a captured traceback; rerunning resumes from prior partitions.
