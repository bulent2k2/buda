# Measurement hazards: when the comparison quietly degenerates

**Guards:** `tools/measure_guard.py`, wired into `tools/qor_corpus.py` and
`tools/qor_table.py`.
**Date:** 2026-08-22.

## The one shape

Every failure recorded here has the same shape, and it is why they get believed:

> The comparison degenerated, and the tool reported success.

No exception, no traceback, no missing output — just a number that is wrong in a
way that reads as a finding. Three of the four report **"no difference"**, which
is exactly what a null result looks like. A fourth reports a large diff and
invites you to attribute all of it to your branch.

None of them are caught by the test suite, by CI, or by reading the diff. They
are caught by noticing that a number is implausible *for the change you made* —
which requires already suspecting it.

All four were hit in a single piece of work (the `set_placed_endpoints`
analysis, #818 and #822), which is the reason for this page.

## The four

### 1. `--vs` resolves the LOCAL ref

`qor_corpus.py --vs main` measures your **local** `main`. A topic-branch
workflow never checks `main` out, so it drifts. Measured: 44 commits stale, and
the sweep re-measured the old base while both sides succeeded and reported
`0 better, 0 worse`.

Two tells, neither obvious: the WL totals were the *old* base's figures
verbatim, and the run said `48 comparable flows` of 49 — one flow did not exist
on that baseline.

**Guarded already**, and the advisory is what caught it. Fix: `git fetch origin
main:main`, or pass `--vs origin/main`.

### 2. A regenerated snapshot carries other people's work

`qor/qor_table.md` records the commit it was generated at. Regenerate it on a
branch whose base has moved and the diff is *their movement plus yours*, with
nothing in it saying which is which.

Measured: 27 of 49 rows changed and the header went `39 clean → 37 clean`,
which reads as the branch causing regressions. **Three** rows were the branch's.

**Now guarded** — `describe_drift` names the span at regeneration time. The
attribution technique is the general one: regenerate once with your change
disabled, and diff the two runs against each other rather than against the
checked-in file.

### 3. A rebase without a rebuild

This one had **no guard at all** and cost the most. A rebase pulls in other
people's C++; nothing rebuilds; the sweep then measures a binary that does not
match its source.

Measured: two unrelated tests failed and looked like a real regression on
`main` — `test_exec_error_names_the_failing_substatement` was testing a
`bdb.cpp` change the binary did not contain. `main`'s CI being green was the
only tell. Everything run after that rebase — a snapshot regeneration, an
isolation sweep, a full suite — was against the stale binary.

**Now guarded** — `check_build_fresh` refuses when the newest compiled source
is newer than the newest built extension. `--allow-stale-build` overrides.

Three things it deliberately does *not* do:

- **It never refuses because it found no build.** "I found no build" has two
  causes that look identical from here: there really is none (the flows then
  fail loudly on their own, a better error than this one), or the layout is one
  `_BUILD_DIRS`/`_EXT_SUFFIXES` does not know about — the guard author's bug.
  Making the second fatal turns that bug into a CI outage on a platform the
  author cannot run: the first cut globbed `build/*.so` only, so on MSVC
  (`build/Release/*.pyd`) and Cygwin (`*.dll`) it found nothing after a
  *successful* build and every test calling `qor_corpus.sweep` would have
  exited 2 (#829 P1). **A guard must degrade to silence when it cannot tell,
  and refuse only when it has actually measured a problem.**
- **Python edits never trip it.** The Python layer is imported from source, so
  a `.py` change is live. Only `.cpp/.h/.c` and `CMakeLists.txt` count.
- **It judges by the NEWEST extension, not the oldest.** `buda` and `buda_db`
  are separate targets over different sources, so an incremental build relinks
  only what needed it. Judging by the oldest made every ordinary incremental
  build look stale (measured: `buda_db.so` reported 97.8 min behind a
  `detailed_nuts.cpp` it does not depend on, straight after a successful
  `bin/bb`). **A guard that cries wolf is one people learn to override**, which
  would leave the real hazard unguarded — so the false-positive rate matters
  more than the theoretical partial-build case.

### 4. An A/B whose two sides are the same

After `set_placed_endpoints` flipped to default-on, the study instructions still
named `BUDA_DNUTS_PLACED_ENDPOINTS=1`. Following them means running `=1` versus
*unset* — **two ON runs** — which reports no difference, and "no difference"
reads as *the flip changes nothing* rather than as a mis-run experiment.

**Now guarded** — `warn_if_identical` advises whenever `--compare`'s two sides
are indistinguishable on every metric across every flow. It is an *advisory*,
not an error: that is the correct outcome for a change that is byte-identical
by design (and this repo lands those deliberately). The point is only that it
must never pass unremarked, because *"my change does nothing"* and *"my
experiment did nothing"* are the same output.

This one is the catch-all. Whatever made the comparison degenerate — the same
commit twice, a no-op override, one build serving both sides — arrives here.

### The guard's own advice must be true

`check_build_fresh` tells the user to pass `--allow-stale-build`. That is only
useful if every CLI reaching the gate accepts it — and `tools/qor_nopin.py`
also calls `qc.sweep` while registering no such flag, so its users were told to
pass an option it would reject (#829 P2). The flag is registered from one place
(`add_build_flag`) and every sweeping CLI calls it; a new one that forgets will
tell its users to do something impossible, so that is the thing to check when
adding a harness.

## Using the guards

```bash
# refuses if the engine predates its C++ sources
tools/qor_corpus.py --out mine.json
tools/qor_corpus.py --allow-stale-build --out mine.json   # override

# advises if both sides are indistinguishable
tools/qor_corpus.py --compare base.json mine.json

# advises that the diff spans commits that are not yours
tools/qor_table.py --out qor/qor_table.md --json qor/qor_table_rows.json
```

`--compare`, `--check`, `--clean-baselines` and `--diff` read JSON and never
load the engine, so they are exempt from the freshness gate. `--vs` builds both
sides itself and is self-consistent by construction.

## What is still unguarded

- **A baseline your tree does not contain.** `--vs` advises on it; nothing
  stops it. A branch forked before commits that *are* in the baseline gets
  their effects reported as its own.
- **Comparing the wrong two files.** Nothing checks that `base.json` came from
  the base.
- **Parallel-sweep timings.** `qor_corpus.py` runs flows concurrently, so its
  `sec` column is contended — measured swinging −8.2% → +9.2% across two runs
  of a change that touches no placement. The tool labels it *informational*;
  a runtime claim needs `tools/runtime_ab.py`. Not a guard, a known limit.
