# Thesis figure caption guide

The figures intentionally contain no long titles, filenames, case IDs, or raw variable names. Use the following descriptions as LaTeX captions and adjust chapter references as needed.

## Figure 1: Task Locations

Spatial distribution of the wildfire-observation tasks used for the California and India experiments. Marker shape and colour indicate task-priority class. The California outline is from the 2025 US Census cartographic boundary file; the India outline is from the Natural Earth 1:50 million admin-0 dataset.

- Source: Task input file; US Census Bureau 2025 cartographic boundaries; Natural Earth 5.1.1
- Sample: 805 tasks
- Notes: None.

## Figure 2: Campaign Coverage

Coverage of the post-processed campaign by study region. The left panel counts distinct base architectures and the right panel counts distinct evaluated scenario types after the analysis quality gate.

- Source: Canonical completed-row table
- Sample: 940,410 completed scenario rows
- Notes: None.

## Figure 3: Regional Cn Response

Nominal performance as a function of the Central Node fraction for California and India. Lines show the architecture-level mean and shaded bands show the interquartile range.

- Source: Canonical completed-row table, nominal scenario
- Sample: 1,782 architecture rows
- Notes: None.

## Figure 4: Cn Model Fit

Observed nominal task-success response to the Central Node fraction and the polynomial model selected by the Akaike information criterion in each region. Numerical model comparisons are provided in the companion table.

- Source: Canonical completed-row table, nominal scenario
- Sample: 1,782 architecture rows
- Notes: No regression equation or software-generated title is embedded in the plot.

## Figure 5: Paired Region Performance

Paired comparison of matched architectures across California and India. Each marker is one shared architecture; the diagonal denotes equal task-observation success in both regions.

- Source: Canonical completed-row table, matched case identifiers
- Sample: 891 matched architectures
- Notes: None.

## Figure 6: Two Region Pareto Landscape

Performance--resource trade space for both study regions. Faint markers show all evaluated architectures and connected markers show the non-dominated frontier when deadline completion is maximised and transferred data is minimised.

- Source: Canonical completed-row table, unlimited-useful-deadline scenario
- Sample: 1,782 architecture rows
- Notes: None.

## Figure 7: Central Node Performance

System response to the Central Node fraction, separated by region and constellation size. The panels report deadline completion, mean deadline coverage, transferred data, and the inequality of transmitted load across nodes.

- Source: Canonical completed-row table, unlimited-useful-deadline scenario
- Sample: 1,782 architecture rows
- Notes: India 180-satellite task-population audit: 297 architecture cases; 15 use the intended 773-task population throughout, while 282 contain a mismatch (most commonly 741 tasks). The layer remains visible but must be qualified in the report.

## Figure 8: Architecture Main Effects

Marginal architecture-factor effects on deadline completion. Each point is the mean over the other evaluated factors and the shaded range is the interquartile interval; the figure is descriptive rather than a causal decomposition.

- Source: Canonical completed-row table, unlimited-useful-deadline scenario
- Sample: 1,782 architecture rows
- Notes: None.

## Figure 9: Spearman Metric Relationships

Spearman rank correlations among the principal architecture and outcome variables. Positive values indicate that both variables tend to increase together; negative values indicate an opposing monotonic relation. Correlation describes association and is not interpreted as causation.

- Source: Canonical completed-row table, unlimited-useful-deadline scenario
- Sample: 1,782 architecture rows
- Notes: None.

## Figure 10: Routing Policy Ablation

Routing-policy ablation across the Central Node, peer-only, and hybrid strategies. Bars report regional means for observation success, deadline completion, and transferred data.

- Source: Canonical completed-row table, routing-policy scenarios
- Sample: 5,346 scenario rows
- Notes: None.

## Figure 11: Task Size Sensitivity

Sensitivity of performance and network load to the task-data volume. The horizontal axis is the task-size multiplier relative to the nominal task definition.

- Source: Canonical completed-row table, task-size scenarios
- Sample: 7,128 scenario rows
- Notes: None.

## Figure 12: Priority Specific Outcomes

Observation success and deadline completion by task-priority class. The comparison tests whether the routing and scheduling logic preserves the intended service differentiation across regions.

- Source: Regional priority case-metric summaries
- Sample: 7,128 case-priority summaries
- Notes: No bar is drawn where the supplied task population contains no usable observations: India Critical.

## Figure 13: Robustness Response

Retention of deadline performance under component failures, link/capacity disturbances, and combined stress. Each line is the regional mean at the indicated severity.

- Source: Canonical completed-row table, robustness scenarios
- Sample: 920,808 robustness rows
- Notes: None.

## Figure 14: Unlimited Policy Pareto

Unlimited-policy trade space within each study region. Marker size represents constellation size; highlighted points are non-dominated with respect to deadline completion and total transferred data.

- Source: Canonical completed-row table, unlimited-useful-deadline scenario
- Sample: 1,782 architecture rows
- Notes: None.

## Figure 15: Objective Weight Sweep

Architecture selected as the objective weight shifts from transfer efficiency to deadline performance. The step changes reveal decision thresholds rather than implying a unique global optimum.

- Source: Derived from the canonical completed-row table
- Sample: 1,782 candidate architecture rows; 101 weights per region
- Notes: Exact winning case identifiers are kept in the companion table, not printed in the figure.

## Figure 16: Monte Carlo Winner Frequency

Frequency with which the leading architectures are selected under 4,000 random combinations of performance, transfer-efficiency, and load-balance weights per region. Candidate codes are mapped to full architecture definitions in the companion table.

- Source: Monte Carlo objective-weight analysis derived from the canonical completed-row table
- Sample: 4,000 random weight vectors per region
- Notes: Short candidate codes prevent case identifiers and long architecture names from cluttering the plot.

## Figure 17: Mission Chain

End-to-end mission chain considered in the thesis, from wildfire-task creation to operational delivery. The research contribution focuses on the inter-satellite dissemination stage and the effect of assigning Central Node functionality to a subset of satellites.

- Source: Conceptual synthesis of the thesis system model
- Sample: Not applicable
- Notes: Engineering block diagram; stages are functional and not drawn to a physical scale.

## Figure 18: Task Generation Workflow

Processing sequence used to transform Sentinel-3 fire detections into simulation tasks. Clustering determines the event representation; priority, deadline, data volume and visibility define how each task enters the dissemination simulation.

- Source: Implemented task-generation and simulation-input workflow
- Sample: Not applicable
- Notes: None.

## Figure 19: Simulation Postprocessing Workflow

Reproducible simulation and post-processing workflow. Checkpointed outputs enter the analysis only after completion and integrity checks, after which all statistics are derived from one canonical table.

- Source: Implemented campaign and post-processing workflow
- Sample: Not applicable
- Notes: None.

## Figure 20: Walker Constellation Central Nodes

Schematic Walker-type constellation in which a defined subset of satellites acts as Central Nodes. The figure distinguishes node roles; the number of orbital planes and satellite spacing are illustrative and are not drawn to scale.

- Source: Conceptual representation of the simulated architecture
- Sample: Not applicable
- Notes: None.

## Figure 21: Temporal Routing Example

Store-carry-forward dissemination over time-varying contacts. The task is uploaded to a satellite, relayed to a Central Node, stored until a useful contact becomes available, and then forwarded for ground delivery.

- Source: Conceptual representation of temporal routing in the simulator
- Sample: Not applicable
- Notes: None.

## Figure 22: Scenario Failure Transformations

Scenario-generation structure applied to each base architecture. Routing, task-volume and failure/stress variants retain the same base definition and are paired with their baseline case before effects are calculated.

- Source: Implemented scenario-generation design
- Sample: Not applicable
- Notes: Straight connectors indicate data lineage; no causal ordering is implied among scenario families.
