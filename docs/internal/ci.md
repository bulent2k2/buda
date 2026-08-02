# Continuous integration

`.github/workflows/ci.yml` builds the C++ core and runs the whole pytest suite
on every push to `main` and every pull request.

## Cost

Measured on a 4-core Linux box when this landed:

| stage | time |
|---|---|
| clean build (`bin/bb`) | ~1m 54s |
| full suite, all tiers, `-n 4` | ~3m 16s |
| **total** | **~5m** |

Endpoint: **2077 passed, 25 skipped, 36 xfailed**.

The fast tier alone is ~10-16s, so if the 5-minute gate ever becomes a
bottleneck the cheap split is fast-tier on push and the full suite on PR —
not dropping coverage.

## Dependencies

```
pip install pybind11 pytest pytest-bdd pytest-xdist matplotlib
```

plus CMake >= 3.15, a C++20 compiler and Python dev headers. That is the whole
list, verified by building this repo from nothing in a bare container:

- **SQLite is bundled** (`src/sqlite3.c`) — no system sqlite.
- **No EDA-library dependency** — the DEF/LEF/Verilog/GDS importers are in-tree.
- **Tk is not needed.** The suite runs on matplotlib's Agg backend, so no
  display server (`docs/build_test_dependencies.md`). `MPLBACKEND=Agg` is set
  in the workflow so nothing can lazily select a GUI backend.
- **pytest-xdist is not optional in practice** — it is the ~3x that keeps the
  run at five minutes.

## Why the ISA is pinned

`CMakeLists.txt` defaults to `-march=native`, which is right for a developer
build and wrong for CI: hosted runners are a heterogeneous fleet, so `native`
compiles a different instruction set per run. The placement goldens
(`test_nuts_placement_golden.py`) are precisely what that perturbs — a golden
generated on one runner can fail on the next with no code change.

The workflow sets `BUDA_ARCH=x86-64-v2`, forwarded by `bin/bb` to
`-DBUDA_ARCH`. `-DBUDA_ARCH=none` omits `-march` entirely.

This is the second half of a problem the repo already half-solved:
`-ffp-contract=off` (see the comment in `CMakeLists.txt`) removes FMA
contraction so the double-based congestion/NUTS math rounds identically across
CPUs. Pinning `-march` removes the vectorization half.

**Measured:** under `-march=x86-64-v2` the suite is byte-for-byte as green as
under `native` — 2077 passed either way — and the golden file itself is
8 passed / 1 xfailed. The single xfail is
`test_nuts_placement_matches_golden_large[flow/rnr/mix.buda]`, one of the four
`HOST_SENSITIVE_FLOWS`, which xfail off their generation host by design rather
than failing. Three of the four still match exactly.

## What CI deliberately does NOT do

Three, each a real gap rather than an oversight — the reasoning, evidence and
where-to-start notes are in [`opens_ci.md`](opens_ci.md):

1. **CI does not own the goldens** — `BUDA_NUTS_GOLDEN_STRICT` stays unset, so
   `flow/rnr/mix.buda`'s large golden is currently unenforced in CI.
2. **The QoR corpus does not run** — it is a comparison tool and needs cached
   merge-base baselines, so it belongs in a nightly, not the PR path.
3. **The web ports are not executed** — `test_web_displaygeom.py` pins the
   authoritative geometry but never runs the JS or Scala mirrors of it.
