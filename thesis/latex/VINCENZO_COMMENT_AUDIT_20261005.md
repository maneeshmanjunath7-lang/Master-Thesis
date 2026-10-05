# Vincenzo Comment Resolution Audit

Source reviewed: `Master_Thesis Part 1_261001_155052_Commented (1).pdf`

Re-audit date: 5 October 2026

All 51 embedded PDF annotations were extracted directly from the commented PDF and checked against the complete revised thesis source. The earlier ledger overstated comment 30: the previous task-location figure showed coordinates but no geographic boundary. This audit corrects that omission and records the full-document consistency changes.

| No. | PDF page | Supervisor comment | Resolution in revised Part 1 |
|---:|---:|---|---|
| 1 | 1 | Simplify the subtitle. | Replaced it with: “Wildfire Follow-Up as a Case Study for Architecture Trade-Space Analysis.” |
| 2 | 2 | Remember to sign. | Kept the official TUM declaration page with the author name and submission date. The author must still add a handwritten or approved digital signature before submission; the report does not insert a false signature. |
| 3 | 3 | Remove the evidence-scope paragraph/page. | Removed the stand-alone evidence-scope front-matter page. The necessary provenance boundary now appears briefly in Sections 1.6 and 4.8. |
| 4 | 5 | Call them central nodes and define the routing capability once. | Uses “central node” throughout. The first definition states that it is a preferred relay with broader link eligibility and an enhanced communication budget. |
| 5 | 5 | Define Fresh V2 in simple terms before using it. | Removed “Fresh V2” from the opening explanation. The abstract describes the experiment directly. |
| 6 | 5 | Introduce the method before abstract results; clarify regional inputs. | Added a compact method paragraph before results and states that California and India use the same architecture grid but different frozen workloads (32 and 773 tasks). |
| 7 | 5 | Too much abstract detail. | Reduced the abstract to the problem, method, three main quantitative findings, the main conclusion, and one sentence on the 180-satellite provenance boundary. |
| 8 | 15 | Avoid connector-heavy wording. | Rewrote the introduction with shorter declarative paragraphs and explicit event order. |
| 9 | 15 | Introduce central nodes and their characteristics first. | Moved the plain-language central-node definition into Section 1.1 before the research problem. |
| 10 | 15 | Wording is too complex. | Replaced the previous long treatment description with purpose-first prose and a five-stage mission-chain figure. |
| 11 | 15 | Add Messina and Golkar AIAA 2025 reference. | Added and cited AIAA 2025-0588 in Sections 1.1 and 2.4 and the reference list. |
| 12 | 16 | Missing reference. | Added supporting citations for DSS operation, decentralized allocation, temporal networking, and active-fire products. |
| 13 | 16 | Reduce five research questions to no more than three. | Replaced five questions with three result-focused questions on central-node/routing effects, sensitivity, and architecture trade-offs. |
| 14 | 17 | Where do the hypotheses come from? | Removed the unsupported five-hypothesis list. |
| 15 | 17 | Support hypotheses with references and detail. | Replaced it with three literature-motivated expectations linked to temporal routing and trade-space behaviour. |
| 16 | 18 | Move “Meaning of a Central Node” earlier. | Integrated the definition into Section 1.1 and the implementation boundary into Section 2.4. |
| 17 | 18 | Scope table is repetitive. | Removed the scope table and replaced it with a concise boundary list in Section 1.6. |
| 18 | 19 | Aims, objectives, goals, contributions, and scope repeat. | Reduced the structure to one aim, four objectives, three questions, one boundary section, and three contributions. |
| 19 | 21 | Define i and j in the time-varying connectivity notation. | Section 2.3 states that contact e links nodes i and j and defines start, end, and rate. |
| 20 | 21 | Expand the time-varying connectivity explanation. | Added a plain-language distinction between contact opportunities and used transfers, plus capacity, order, release, and deadline conditions. |
| 21 | 22 | Add more central-node or comparable relay literature. | Connected federated collaboration, network analysis, relay roles, DTN, and decentralized allocation; added Messina and Golkar explicitly. |
| 22 | 22 | Literature subsections feel detached. | Reorganized Chapter 2 as one narrative: DSS coordination -> mission event chain -> temporal networking -> central nodes -> fire workload -> research gap. |
| 23 | 22 | Explain dataset merging more clearly. | States that FIRMS and Sentinel-3 are not merged row by row; FIRMS screens and checks, while Sentinel-3 alone generates tasks. |
| 24 | 22 | “From Fire Pixels to Communication Tasks” is methodology. | Moved the complete processing sequence to Chapter 3 and retained only the scientific context in Chapter 2. |
| 25 | 23 | TAT-C and supporting tools belong in methodology. | Moved TAT-C and orbital implementation into Section 4.3. |
| 26 | 24 | Strengthen the research gap. | Added a precise gap: connect wildfire requests, finite temporal contacts, reception-before-observation, central-node fraction, and burden in one controlled experiment. |
| 27 | 25 | Move the wildfire dataset discussion earlier. | Chapter 2 now introduces the two source products and their roles before Chapter 3 presents the implementation. |
| 28 | 25 | Specify vague meaning. | Replaced broad regional claims with “compact event case” and “dense workload case” and explicitly rejects a pure geographic interpretation. |
| 29 | 25 | Assume the reader knows nothing. | Added plain definitions of FRP, DBSCAN, task reception, observation opportunity, central node, and temporal contact. |
| 30 | 26 | Add maps and enlarge labels. | Corrected in this revision. Figure 3.1 now overlays all 805 task coordinates on published California and India administrative outlines. California uses the 2025 US Census cartographic boundary; India uses Natural Earth 5.1.1. Labels and legends are exported directly from Matplotlib at report scale. |
| 31 | 27 | Ensure DBSCAN is understood. | Added an explanation of `eps`, `min_samples`, clusters, noise points, and why DBSCAN is suitable. |
| 32 | 27 | Clarify when threshold/clustering are applied and their effect. | States the exact order: event/region/quality/10 MW filtering, then DBSCAN, then task-level de-duplication; explains that settings change network load. |
| 33 | 28 | Use consistent score notation. | Standardized `S_FRP`, `S_conf`, `S_cluster`, and `S_task`. |
| 34 | 28 | Justify packet sizes. | States that packet sizes are engineering request-message assumptions, not images; explains why the multiplier study is needed. |
| 35 | 29 | Recreate Figure 3.3 with proper tools/formatting. | Removed the screenshot-style figure. The population is presented as a LaTeX table, while Figure 3.1 and the remaining schematics are deterministic Python/Matplotlib exports rather than screenshots or generated artwork. |
| 36 | 30 | Add a reference. | Added active-fire product citations and retained source-product provenance in the method. |
| 37 | 30 | Explain what the Madre Fire check proves. | States that it supports one event’s temporal/geographic plausibility but does not validate every task, priority calibration, or classification accuracy. |
| 38 | 31 | Restate the methodology objective. | Opens Chapter 4 with the exact operational question the simulation answers. |
| 39 | 31 | Caption Figure 4.1 more meaningfully. | New caption identifies the execution order and explains why routing/reception precede observation. |
| 40 | 31 | Reference the task table from the previous chapter. | Section 4.2 explicitly identifies Chapter 3’s frozen CSV and regional task counts as simulation input. |
| 41 | 31 | Add architecture-parameter table. | Added a compact table listing all five architecture factors and their levels. |
| 42 | 32 | Explain the 1.5 link-budget boost. | Describes how the distance factor is applied per central endpoint and how it enters eligibility and rate calculations. |
| 43 | 32 | Remove unnecessary zeros after decimal. | Uses 800 km, 98.6 deg, 700 km, 10 deg, and similar meaningful precision. |
| 44 | 32 | Add a Walker-constellation visualization. | Added a restrained Walker schematic with standard satellites shown as open blue circles and Central Nodes as orange squares. |
| 45 | 33 | State the resulting communication distance. | Reports the implemented UHF sensitivity ceilings: approximately 2306 km ordinary-ordinary, 3460 km with one central endpoint, and 5190 km with two, with geometry caveats. |
| 46 | 33 | Add topology examples. | Added a temporal store-carry-forward example with a labelled Central Node and retained the plain definitions of central-only, peer-only, and hybrid policies. |
| 47 | 33 | Clarify unlimited diagnostic and greedy/causal routing. | States exactly which limits are removed, which definitions remain, and that the algorithm is not a global flow optimizer. |
| 48 | 33 | Scenario transformations are unclear. | Replaced the dense subsection with a rectilinear scenario flowchart and a family/transformation/question table. Every transformed case passes through one common comparison gate and is paired with its baseline. |
| 49 | 34 | Add an example/flowchart for metrics. | Added the temporal routing example plus the end-to-end simulation/post-processing workflow, and explains how reception, later access, deadline, burden, robustness, Pareto, and rank acceptability differ. |
| 50 | 34 | Methodology requires major simplification. | Rebuilt Chapter 4 into nine purpose-led sections. Core science stays in the chapter; worker memory, compression, and resume mechanics are moved to software documentation. |
| 51 | 34 | Merge small subsections and add visuals/examples. | Consolidated the method and added four methodology figures plus architecture, scenario, and metric tables. |

## Editorial policy used

- Results and numerical definitions were not changed merely to improve prose.
- The 180-satellite task-population issue was not hidden; it was moved to the validation section and stated in proportion to its scientific effect.
- The revised wording uses “central node” as the main term and reserves “routing package” for the coupled implementation treatment.
- Claims that would require unexecuted ablation, placement, observation-radius, or operational-calibration studies remain explicitly conditional.

## Full-document consistency fixes added in this audit

- The report title, metadata, captions, result headings, discussion, conclusion, and source files now use **Central Node** as the user-facing term.
- The discussion and conclusion now answer the same three research questions stated in the revised introduction; the former RQ4/RQ5 structure has been removed.
- The five unsupported hypothesis labels were replaced with an assessment of the three literature-motivated expected relationships.
- The stand-alone evidence-scope front-matter chapter was removed from the compiled report. Provenance limits remain in the methodology, results, discussion, and validation appendix.
- The corrected Part 1 was merged into the complete thesis while retaining the existing results, appendices, references, and TUM SPS formatting.
