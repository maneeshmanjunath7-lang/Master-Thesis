# Benchmark and equivalence results

Run date: 2026-09-10.

## Scientific equivalence

The deterministic five-scenario fixture was executed against the untouched v2.0 source and v2.1.0 source with `PYTHONHASHSEED=0`. Complete JSON output, including case metrics, task metrics and retained transfer rows, matched byte-for-byte.

```text
original_sha256=360a839312733327b475f249ceeab7c0f011daf5a27056deee8e84c9ba3800cc
optimized_sha256=360a839312733327b475f249ceeab7c0f011daf5a27056deee8e84c9ba3800cc
exact_match=True
```

## India-sized synthetic routing benchmark

The stable version benchmark uses 60 satellites, 773 tasks, six contact cycles, peer routing, and three repetitions. It exercises the dominant dissemination loop but does not include orbital geometry generation, multiprocessing, Parquet writes, or thesis analysis.

```text
v2.0 median routing time: 8.315113 seconds
v2.1 median routing time: 2.753328 seconds
speedup: 3.02x
v2.0 result_sha256: 7037a48188b81ac9e0d07ae09a556ed44531ef325361b88c6c0d9dda0510a3ed
v2.1 result_sha256: 7037a48188b81ac9e0d07ae09a556ed44531ef325361b88c6c0d9dda0510a3ed
```

Case-level routing-context reuse provides additional benefit when many scenarios share one physical case. Its measured gain varies with topology and failure family.

## Reproduce on Ubuntu

```bash
source .venv/bin/activate
export PYTHONPATH=src PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1
python -m unittest discover -s tests -v
python benchmarks/benchmark_version.py
python benchmarks/benchmark_routing.py
```

Interpret these as regression checks and directional performance evidence. The complete India campaign speedup will depend on the contact-window count, CN fraction, failure scenario, storage speed, and number of memory-safe workers.
