# Post-processing

`complete/postprocess_complete.py` is the final entry point used for the thesis analysis. It calls the validated regional reader in `california_results_analysis/` and adds cross-region comparison, population gates, task-level timing, robustness, Pareto, metric-redundancy, and preference-sensitivity analyses.

`library/` contains the reusable ZIP-native post-processing package and inspection utilities.

Run the complete pipeline from the repository root:

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

The output tables used in the thesis are published in `results/`.
