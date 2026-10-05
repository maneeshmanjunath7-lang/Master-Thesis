# Changelog

## 2.1.0 - 2026-09-10

This is a performance and resumability update. It does not change the frozen architecture grid, scenario definitions, task inputs, routing priorities, failure model, metrics, or output column contract.

### Runtime changes

- Precomputes task eligibility and observation-opportunity lookup data once per architecture/CN case instead of once per scenario.
- Filters and orders physical contact windows once per topology and reuses them across scenarios.
- Precomputes satellite velocities once per base architecture and reuses them across all 11 central-node fractions.
- Replaces the routing hot loop's repeated receiver-eligibility function calls with equivalent per-window direction plans and direct lookups.
- Uses tuple/array iteration in high-volume Pandas loops and sorts milestone reception times once per task.
- Prevents nested NumPy/BLAS thread pools in `RUN_FULL_VM.sh` so architecture workers do not compete for the same CPU cores.

### Resume and VM safety

- Checks every case checkpoint before regenerating base geometry. A fully completed base now logs `FAST-SKIP` and returns immediately.
- Adds `BASE-PREP`, per-case elapsed time, and `WORKER-PLAN` log records.
- Selects workers from CPU count and available physical RAM after a configurable reserve; swap space is not treated as fast RAM.
- The packaged VM defaults are six requested workers, 2 GB estimated RAM per worker, and a 4 GB system reserve.

### Verification

- Nine unit/equivalence tests pass.
- The five-scenario v2.0/v2.1 fixture output is byte-for-byte identical.
- An India-sized synthetic peer-routing fixture has the same result SHA-256 in both versions and is 3.02x faster in median routing time on the packaging machine.

## 2.0.0

Original frozen Full891 fresh campaign package.
