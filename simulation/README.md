# Full891 Fresh End-to-End Simulation v2.1 (performance update)

This is the standalone VM package for the complete thesis campaign. It does **not** read or replay the previous 25 GB result ZIP. It starts from the frozen Sentinel-3 wildfire task input and the original validated Walker/V19 physical model, generates geometry and communication contacts, performs dissemination and observation, runs every configured sensitivity/failure scenario, and builds thesis analyses.

Version 2.1 keeps the v2.0 campaign definition and output schema unchanged while reducing repeated work in the routing and geometry paths. A deterministic India-sized synthetic benchmark produced the same result hash and reduced median routing time from 8.315 s to 2.753 s (3.02x). This is a routing-kernel benchmark, not a guarantee that the complete campaign will be exactly 3.02x faster; geometry, storage and scenario mix also contribute to wall time.

If v2.0 is already running on the VM, do not replace its source files mid-run. See `UPGRADE_AND_RESUME.md` for the checkpoint-safe update procedure.

## Frozen scientific scope

- 81 Walker base architectures.
- 11 central-node fractions (0% to 100% in 10% steps).
- 891 architecture cases per region.
- California (32 tasks) and India (773 tasks): 1,782 architecture-region cases.
- 539 controlled scenarios per case: 960,498 scenario evaluations.
- Original nominal control; unlimited hops/copies; deadline and simulation-end delivery.
- Central-only, peer-only and hybrid policy ablation.
- 0.5x, 1x, 2x and 4x task-size sensitivity.
- Random satellite/CN failures, targeted CN failures, link-window outages, capacity degradation and ground-station outages at 5/10/20/30%.
- Thirty deterministic repetitions for random robustness families.
- Selected combined task-size/failure stress tests.
- T10/T25/T50/T75/T90/T95/T100, completion probability, censoring and P100(maximum completed-task T100).
- Both surviving-network and original-mission failure denominators.
- Correlation, linear/exponential/polynomial regression comparison, exact Pareto fronts, weight sensitivity and regional rank agreement.

The architecture count remains 891 per region. Routing policies, sizes and failures are scenario dimensions, not additional architecture definitions.

## 50 GB storage design

The simulator processes one base architecture at a time. For each CN fraction it generates all physical contacts in memory, executes the scenario matrix, writes Zstandard-compressed Parquet partitions, and releases the windows before moving on.

It retains:

- one case-level result for every scenario/seed;
- full task-level results for nominal, unlimited, policy and size scenarios;
- task-level results for configured representative robustness seeds;
- selected transfer traces for 60-satellite cases and CN fractions 0/20/50/80/100;
- all configuration, validation, status and provenance records.

It deliberately does not persist every physical window or every packet event from all 960,498 evaluations. Those intermediates would exceed 50 GB and are not required for the requested metrics. The default output cap is 40 GB with an 8 GB free-space reserve.

## VM installation

Ubuntu with Python 3.11 or 3.12 is recommended.

```bash
cd thesis_full_simulation_v2
chmod +x setup_vm.sh RUN_FULL_VM.sh STATUS_VM.sh
./setup_vm.sh
```

The setup finishes by validating the exact grid and task counts.

## Run everything

```bash
./RUN_FULL_VM.sh
```

The script performs validation, a real end-to-end smoke run, the complete run, and final analysis. It stops if the smoke run fails. The full run is resumable; running the same command again skips checkpointed scenarios.

For an interrupted campaign that has already passed validation and smoke testing, resume only the full stage:

```bash
source .venv/bin/activate
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
full891 full --config config/full_campaign.json
```

Monitor from another terminal:

```bash
./STATUS_VM.sh
tail -f logs/04_full.log
```

Direct commands are also available:

```bash
.venv/bin/full891 validate --config config/full_campaign.json
.venv/bin/full891 estimate --config config/full_campaign.json
.venv/bin/full891 smoke --config config/full_campaign.json --output-root outputs/smoke_full891_fresh
.venv/bin/full891 full --config config/full_campaign.json
.venv/bin/full891 status --config config/full_campaign.json
.venv/bin/full891 analyze --config config/full_campaign.json
```

## Output contract

```text
outputs/full891_fresh_complete/
├── campaign_case_manifest.parquet
├── scenario_manifest.parquet
├── event_manifest.parquet
├── inputs/tasks_<region>.parquet
├── provenance.json
├── base_worker_status.parquet
├── campaign_run_report.json
├── runs/<region>/<base>/<case>/
│   ├── case_manifest.json
│   ├── architecture_manifest.parquet
│   ├── constellation_nodes.parquet
│   ├── central_node_distribution.parquet
│   ├── architecture_validation.parquet
│   ├── case_metrics_part_*.parquet
│   ├── task_metrics_part_*.parquet
│   ├── transfers_part_*.parquet
│   └── _SUCCESS.json or _FAILED.json
└── analysis/
    ├── canonical_case_metrics.parquet
    ├── analysis_report.json
    ├── VINCENZO_UPDATE.md
    ├── tables/
    └── figures/
```

The case-metric partition is written last for each checkpoint batch. A crash therefore cannot mark a scenario complete before its retained task/transfer records are safely written.

## Metric definitions

- `T100`: elapsed minutes from task creation until every target receiver has the complete task.
- `P100(T100)`: the maximum T100 among tasks that reached 100%.
- `t100_*_censored`: true when a task never reached 100% by simulation end.
- `S100 before deadline`: percentage of tasks that reached every target before their deadline.
- `surviving coverage`: denominator excludes failed satellites.
- `original coverage`: denominator retains the pre-failure target population.
- `all_useful`: satellites with a valid observation opportunity before the task deadline.
- `all_satellites`: every satellite in the configured denominator.

Observation remains deadline-constrained even when dissemination is allowed to continue until simulation end.

## Scientific boundaries

- Peer-only uses the same physical spacecraft/link model but removes central-routing privilege and permits any feasible satellite pair to relay.
- Hybrid permits feasible peer links and central relays.
- Monetary cost is not invented. The default Pareto analysis uses explicit resource quantities. Add traceable coefficients under `analysis.monetary_cost_coefficients` before claiming monetary cost.
- A full campaign is computationally large. Effective workers are capped by CPU count and available RAM, even if 45 workers are requested.
- The v2.1 VM default requests six architecture workers, budgets 2 GB per worker and reserves 4 GB for Ubuntu and interactive applications. At startup, the `WORKER-PLAN` log record shows the actual selected worker count.

## Tests

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

See `CHANGELOG.md`, `BENCHMARK_RESULTS.md`, and `UPGRADE_AND_RESUME.md` for the exact update, verification evidence, and VM deployment steps.
