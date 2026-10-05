# Original source provenance

The package embeds the authoritative physical simulator and task input so the VM run has no runtime dependency on the previous 25 GB result archive.

| Embedded file | Origin inside the authoritative archive | Archive-member SHA-256 | Embedded SHA-256 |
|---|---|---|---|
| `src/full891_fresh/legacy_simulator.py` | `FINAL_FULL891_ACTUAL_EPOCH_45CPU_20260802/walker_standard_arch_hpc_v19_cn_sweep_0_100_actual_epoch_final.py` | `8379e92a82e90dea2f7c1eda0c959fbaf60d6b00d1aab6ec3816c2b751d5c5d0` | `ce6ae6a9f29252f0a5b26313c43073f587e20625dbcb0209d1fa0c8c9419adae` |
| `input_tasks/all_sentinel3_tasks_with_priority_score_deduplicated.csv` | `FINAL_FULL891_ACTUAL_EPOCH_45CPU_20260802/input_tasks/all_sentinel3_tasks_with_priority_score_deduplicated.csv` | `e717a1dde84db0f47efdda09cfca2f437909349b25a139a45a7c1928da80e533` | `46bb35972ba6570a10d9b45a6454942845b813299e25a03cab2e8ebc2d339a1b` |

Authoritative archive SHA-256 recorded during the earlier read-only inventory:

`55f4a7f2140f8838c35b78e33ea2fe5ffd277f6f90f2caad221a3b64fdf6af6e`

The embedded text is content-equivalent after platform line-ending normalisation (CRLF to LF) and one harmless final blank line. The legacy module is preserved as the reference Walker/V19 geometry, link-budget, CN-placement and task-normalisation implementation. New scenario orchestration and routing/metrics are isolated in separate modules.
