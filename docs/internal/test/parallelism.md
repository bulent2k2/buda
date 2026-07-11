# Parallel test runs (pytest-xdist)

The `mid` tier grew from ~40s (2026-06) to **~7 min** as the viz / ripup / hier
integration suites filled in (429s / 391 tests on a 4-core Linux box;
~5 min on Apple Silicon). This note records why we parallelize rather than
re-tier, and how it's wired.

## The runtime is a long tail, not breadth

`pytest -m mid --durations=30` shows ~6 tests are ~65% of the wall time:

| Test(s) | Time | % of mid |
|---|---:|---:|
| `test_ripup_reroute` (4 heavy of 25) | ~119s | 28% |
| `test_build_hier_demo` (~12) | ~64s | 15% |
| `test_nuts_placement_golden[flow/rnr/mix.buda]` (1) | 62s | 14% |
| viz tests (`test_viz_collections` / `_preroutes`) | ~40s | 9% |
| everything else (~350 tests) | ~140s | 33% |

Because it's a long tail, a symmetric split of `mid` into three sub-tiers
(mid-fast / mid-mid / mid-slow) was rejected: it yields **five** tiers to
remember and forces a hand-classification of every new test, for a problem that
is really "a handful of tests are big." Two better levers:

1. **Parallelize** — these tests are independent and CPU-bound (this doc).
2. Optionally, later: promote just the heaviest tail to `slow` (one marker), or
   speed up specific offenders (e.g. `nuts_golden[mix.buda]` at 62s for one
   golden; `build_hier_demo` runs the SA optimizer deterministically ~12×).

## Setup: `-n auto --dist loadfile`

`pip install pytest-xdist`, then `bb -m` / `bb -s` auto-parallelize when xdist is
importable (the **fast** tier stays serial — it is ~10s and worker startup
wouldn't pay off). Direct invocation:

```bash
pytest -o addopts="" -m "not slow" -n auto --dist loadfile      # mid, parallel
```

Measured (4-core Linux, after a clean build): **429s → 133s, 391 passed, 0
failures — ~3.2×.** On an 8–10-core Mac the speedup is larger, bounded by the
heaviest single *file* (see below).

### Why `--dist loadfile` (not the default per-test `load`)

`loadfile` keeps every test in a file on **one** worker. Several tests share a
fixture path next to a common `.buda` — e.g. the viz tests write a selection
sidecar `flow/<stem>.json` beside the flow they build, and many use
`dnuts1.buda`. Under the default per-test `load`, two such tests land on
different workers and **race on the same sidecar/log file**. `loadfile`
serializes within a file, so those never cross workers, while different files
still run in parallel — which is where the long-tail files live anyway.

On this 4-core box the default per-test `load` measured the *same* 132.78s (also
0 failures) — with only 4 cores the run is CPU-bound (429s / 4 ≈ 107s + overhead),
so the distribution mode doesn't move wall time, and `loadfile`'s determinism is
free. A single clean per-test run does **not** prove the suite is race-free
(races are timing-dependent), so `loadfile` stays the default.

The trade-off shows up at higher core counts: `loadfile`'s floor is the heaviest
single *file* (`test_ripup_reroute`, ~120s in one file, can't be split), whereas
per-test `load`'s floor is the heaviest single *test* (`nuts_golden[mix.buda]`,
62s). On an 8+-core Mac, per-test `load` could therefore beat `loadfile` — but
only once the viz/flow tests are made sidecar-isolated (unique `tmp_path` per
test) so per-test distribution can't race. That isolation pass is deferred as
future work; until then, `loadfile` is the safe default.

### Gotcha: rebuild first

A parallel run that suddenly shows ~100 failures with `AttributeError: '...'
object has no attribute '...'` is almost always a **stale build**, not an xdist
race — the compiled `buda`/`buda_db` in `build/` lags the source. `bb`
rebuilds before testing; a bare `pytest` does not. Rebuild (`bin/bb`) and re-run
before diagnosing parallelism.

## Controls

| Want | Do |
|---|---|
| Parallel mid/slow (default when xdist present) | `bb -m` / `bb -s` |
| Pin worker count | `BB_JOBS=8 bb -m` |
| Force serial | `BB_JOBS=0 bb -m` (or uninstall xdist) |
| Fast tier | always serial (`bb -t`) |

## Regenerate the numbers

```bash
bin/bb                                                   # rebuild first!
pytest -o addopts="" -m "not slow" --durations=30 -q     # serial, per-test times
time pytest -o addopts="" -m "not slow" -n auto --dist loadfile -q   # parallel
```
