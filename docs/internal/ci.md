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

### Own the goldens

`BUDA_NUTS_GOLDEN_STRICT=1` turns the host-sensitive xfails into hard failures,
and the golden docstring suggests setting it "on the golden-generation host's
CI". That is deliberately **not** set here, because CI is not currently that
host — enabling it would require regenerating the goldens on the CI image
first, which is a real decision (it changes what the committed goldens mean and
who can reproduce them locally) rather than a workflow tweak.

Consequence, stated so it is not discovered later: `flow/rnr/mix.buda`'s large
golden is currently unenforced in CI. The five stable small flows are always
enforced, and the other three large ones happen to match.

### Run the QoR corpus

`tools/qor_corpus.py` sweeps 36 full flows (~2-4 minutes) but is a *comparison*
tool — a run alone means nothing without a baseline from the merge-base. Making
it a gate needs baseline artifacts cached per commit, so it belongs in a
nightly or label-triggered job, not the per-PR path.

It is worth building. Two regressions in the #518 arc were caught only by a
corpus diff, and one (`mix2_topdown_refine`) was *missed* because the baseline
predated a corpus row added mid-flight — exactly the failure a cached,
automatically-refreshed baseline prevents.

### Check the web ports

`test_web_displaygeom.py` pins the authoritative display geometry
(`viz_common.snap_endpoint_extents`) but never executes the JavaScript
(`src/web/static/index.html`) or Scala
(`web/.../render/DisplayGeom.scala`) ports of it. They are kept in sync by
hand, and issue #554 showed that discipline failing — the fix landed in the
Python renderer and both mirrors stayed broken until review caught it.

Closing that means running the ports under Node and Scala.js in CI. It is the
largest of the follow-ups and independent of everything above.
