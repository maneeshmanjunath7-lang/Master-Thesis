# Master thesis final submission package

**Final submission revision: 14 October 2026.** This package contains the furnished TUM-style LaTeX thesis, compact result tables, regenerated figures, validation material, and the code used to integrate the corrected India 180-satellite run with the completed California/India results.

## Final evidence model

- Historical reference: the earlier nominal California--India campaign in Chapter 6.
- Matched Regional Experiment: 594 cases per event window at 60/120 satellites, used for California--India effects and the main conditional shortlist.
- Corrected India 180-Satellite Experiment: 282 verified cases and 151,998 case-scenario rows on the intended 773 tasks. The balanced CN30--CN100 block contains 216 cases and supports Central Node, robustness, priority, Pareto, rank-acceptability, and matched India 120-to-180 analyses.

The earlier 741-task execution is retained only as development provenance. Its index-alignment defect was corrected by resetting the regional index and enforcing task, normalized-task, case-allow-list, and run-contract hashes. All final 180-satellite numerical claims use `data/final_corrected_180/`.

## Build and validation

Compile `main.tex` from this directory with Tectonic. The final compilation produces an A4 thesis with resolved references and bibliography. Machine-readable evidence is stored in:

- `data/final_results/` for the matched 60/120-satellite analysis;
- `data/final_corrected_180/` for the corrected 180-satellite analysis and matched 120-to-180 effects; and
- `data/nominal/` for the historical nominal campaign.

Regenerate the corrected post-processing with `tools/postprocess_corrected_180.py` and the thesis figures with `tools/generate_part2_revision_figures.py`. The multi-gigabyte raw simulation archives remain external to the portable report package.

`AUTHOR_ACTIONS_BEFORE_SUBMISSION.md` records the remaining signature step. The examiner, submission date, and immutable archival release are fixed in the final package. Obsolete internal review ledgers and the superseded exploratory 741-task synthesis are excluded so that they cannot be mistaken for final evidence.
