# Continuous integration

`.github/workflows/ci.yml` builds the C++ core and runs the whole pytest suite
on every push to `main` and every pull request.

## Cost

Measured on a 4-core Linux box when this landed:

| stage | time |
|---|---|
| clean build (`bin/bb`) | ~1m 54s |
| full suite, all tiers, `-n 4` | ~3m 08s |
| **total** | **~5m** |

Endpoint: **2105 passed, 3 skipped, 36 xfailed**.  First run on a hosted
runner: **5m 21s**, green.

The fast tier alone is ~10-16s, so if the 5-minute gate ever becomes a
bottleneck the cheap split is fast-tier on push and the full suite on PR —
not dropping coverage.

## Dependencies

```
pip install pybind11 pytest pytest-bdd pytest-xdist matplotlib
pip install -r src/web/requirements.txt
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
- **`src/web/requirements.txt` is not optional either** (fastapi/uvicorn/httpx).
  Without it pytest skips `test_web_ws.py` wholesale and the `_client()`
  helpers in `test_web_server` / `test_web_checkpoint` / `test_web_edit` skip
  too, so the HTTP, WebSocket, checkpoint and topology-edit surfaces go unrun
  while the gate still reports success.  Measured: **2077 passed / 25 skipped**
  without it, **2105 passed / 3 skipped** with it — 28 tests.

That last one is why the workflow has a `No module skipped for a missing
import` step.  pytest skips a whole module when its import fails, so a missing
dependency *removes* tests silently rather than failing; an import-skip is a CI
configuration error and is treated as one.  The 3 remaining skips are
legitimate (one macOS-only backend test, two fixture-dependent) and carry a
different reason.

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

**Measured:** under `-march=x86-64-v2` the suite is as green as under `native`.

## CI is the golden reference host

The workflow sets `BUDA_NUTS_GOLDEN_STRICT=1`. Without it the four
`HOST_SENSITIVE_FLOWS` xfail on *any* mismatch — and `pytest.xfail()` is
imperative, so it swallows a genuine regression exactly as readily as an
environmental one. All four large goldens were therefore **decorative** in CI:
a refactor that really moved wires would have reported xfail and stayed green.

Strict is only defensible because this environment is controlled (pinned ISA,
fixed runner image). Developer machines are heterogeneous and keep the lenient
xfail by leaving the variable unset.

`flow/rnr/mix.buda`'s golden was regenerated for this. Its diff is
QoR-neutral — identical `248 segments overlaps=0 violations=0` and
`3132 netsegs 1862 vias unplaced=0`, with one 16-bit bus segment reassigned
M3→M5 (abstract 10→9 / 96→97, detailed 184→168 / 1176→1192). The other eight
goldens were byte-identical when regenerated and were left untouched.

**The flag is scoped to the placement goldens.** It used to be wider than its
name: `test_flow_scripts.py::test_10_four_level_scale_one_bundle_per_bus` read
the same variable to switch from tolerant bounds to a hand-calibrated QoR
ratchet (`segs == 209`, `unplaced == 0`, no keepout culls). Unlike the
placement goldens that ratchet is **not** regenerable by any tool — the numbers
are hardcoded, calibrated on the original generation host under
`-march=native`, which is not CI's ISA.

Coupling them meant "enforce the NUTS goldens" silently also meant "enforce
this unrelated ratchet", and the flow's own documented-as-acceptable
host-sensitive alternative would fail the whole run. Measured on CI: it yields
**206 segments**, not 209 — the same as an ordinary developer container — so
the coupling was not a theoretical risk. The ratchet now has its own
`BUDA_FLOW_QOR_STRICT`, which CI deliberately does **not** set.

## Why the runner image is pinned

`runs-on: ubuntu-24.04`, not `ubuntu-latest`. Strict golden mode compares
SHA-256 placement digests, so a label migration that swaps the compiler or
system libraries would fail every PR with no repository change — the ISA pin
does not protect against that. `ubuntu-latest` resolved to 24.04 (GCC 13.3.0)
when the goldens were baselined, so the pin is behaviour-preserving today.
Bump it deliberately, together with a golden rebaseline
(`tools/nuts_snapshot.py`) in the same commit.

## Nightly QoR corpus

`.github/workflows/nightly-qor.yml` sweeps the 37-flow QoR corpus at 03:17 UTC
(and on `workflow_dispatch`) and diffs it against the **previous successful
nightly**, so a routing-quality regression on `main` surfaces within a day
instead of waiting for someone to remember `tools/qor_corpus.py`.

It is deliberately not part of the PR gate: the corpus is a *comparison* tool —
a single run says nothing without a baseline — and it costs a full sweep on top
of a build.

**Measured on the hosted runner** (first real run, 2026-08-02): build 1m52s +
corpus sweep 6m34s (`swept 37 flows in 393.9s (jobs=4)`) = **8m49s**. The
"~4m20s sweep" figure this page carried before that run was measured in a
developer container; the hosted runner is ~50% slower for the same work. Adding
the snapshot-table refresh below takes the job to roughly **16 min**.

**The baseline is only promoted on a clean run.** That is the design decision
worth knowing: promoting a regressed sweep would make the regression the new
normal — reported once, then absent from every later diff. Leaving the baseline
at the last good result means a regression keeps being reported until it is
actually fixed. First run (or after a cache eviction) establishes a baseline and
passes; the tool itself raises an uncaught `FileNotFoundError` on a missing
baseline, so the workflow handles that case rather than letting it crash.

**An errored sweep is rejected before either.** `cmd_run` records a flow that
raises as `{"flow": ..., "err": ...}` and still exits 0, and `cmd_compare`
prints a flow that is *new* to the corpus as `(new)` without counting it in
`n_worse`. So a broken flow — on the first run, or a freshly added corpus row —
would otherwise sail through as clean, get promoted, and be read as "unchanged"
by every later nightly. The workflow fails on any `err` row instead.

The run JSON and the diff upload as an artifact on **every** run, including
failures — that is when they are most useful.

### The snapshot table is refreshed by the same job

`qor_table.md` is a *different* artifact from everything above, and the two are
easy to confuse:

| | `tools/qor_corpus.py` | `tools/qor_table.py` |
|---|---|---|
| purpose | A/B **compare** two builds, gate on regressions | single-build **snapshot** |
| output | `qor-current.json` + a diff + an exit code | `qor_table.md` (+ `qor_table_rows.json`) |
| in the nightly | the regression gate | the table refresh |

Nothing refreshed the table, so it drifted **46 commits stale** before anyone
noticed — while `docs/` pages cite its numbers as evidence. The nightly now
regenerates it and, when the numbers actually move, opens a PR on the fixed
branch `nightly/qor-table`.

**A PR rather than a commit to `main`,** because this file is *cited*: a change
to it is a claim about the design, not a build artifact, and a scheduled job
rewriting a cited document unattended makes the stale-but-authoritative problem
worse rather than better. The branch is fixed and force-pushed, so at most one
such PR is ever open. The job's `permissions:` are scoped to exactly that —
`contents: write` + `pull-requests: write`, which is deliberately *not* enough
to write to `main`.

**The change gate ignores `sec`.** That column is the run's own wall-clock, so
the rendered table differs on *every* run even when the QoR is identical; a
plain `git diff --quiet` would open a PR nightly forever and bury the signal.
The predicate is `qor_table.semantic_diff` (exposed as `--diff`), deliberately
in Python rather than inline YAML so it is unit-tested — `test_qor_sweep.py`
covers both directions (timing-only churn is *not* a change; a real metric move
*is*). The `--json` sidecar it compares is sorted by flow, so reordering the
`CORPUS` list does not read as a whole-file change.

**This costs a second full sweep** (~6.5 min). The two tools' `run_flow`s
measure the same pipeline and could share one sweep with a key rename
(`ovl/unpl/viol` vs `overlaps/unplaced/viol_bundles`), but that would re-point
the regression *gate* at the other tool's row-builder and invalidate the cached
baseline's key schema — a bad trade for six minutes of unattended runner time.
Recorded in [`opens_ci.md`](opens_ci.md).

The refresh runs on a clean, established **or regressed** result — a regressed
`main` is exactly when the snapshot most needs to tell the truth — but not when
the sweep errored, since that table would be garbage.

### Caveats on reading the nightly's output

- **Per-flow `sec` is not timing-faithful.** The sweep runs parallel (jobs =
  CPU count), so those are wall-clock under contention. The compare prints them
  as informational and nothing gates on them; use `-j 1` for real timings.
- **Some corpus flows sit on knife-edge endpoints.** `rnr/mix2`'s clean result
  depends on an exact 4-iteration rip-up trace, documented in the flow itself,
  so occasional legitimate churn is expected. A red nightly is a report to
  triage, not proof of a bug — it blocks nothing.

## What CI deliberately does NOT do

One real gap remains — reasoning and where-to-start notes in
[`opens_ci.md`](opens_ci.md):

1. **The web ports are not executed** — `test_web_displaygeom.py` pins the
   authoritative geometry but never runs the JS or Scala mirrors of it.

Plus one narrower piece: the nightly compares `main` against itself over time,
so it catches a regression a day *after* it lands. A `run-qor` PR label that
diffs a branch against its merge-base would catch it before — see `opens_ci.md`.
