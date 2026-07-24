# `ripup_reroute` runtime analysis — `flow/rnr/mix2_fast_bottomup.buda`

A profiling pass on the two `ripup_reroute` calls in the bottom-up hier flow
`flow/rnr/mix2_fast_bottomup.buda` (stage a after `run_nuts`, stage b after
`run_detailed_nuts`), with prioritized speed-up opportunities.

## Method

`ripup_reroute` was enabled at both points (the checked-in flow comments them
out) and run with the built-in timing instrumentation (`BUDA_RR_TRACE=1`, the
`timing:` / `solve passes:` lines) plus a `cProfile` pass for function-level
attribution. Measured on Linux/glibc (faster than the reporter's macOS, but the
proportions and the a:b ratio carry over).

## Measured breakdown

```
stage a (NUTS overlaps):  5.3 s, metric 21->3, 10 moves, 109 full trials
  timing: replan 0.69/119   nuts 2.18/119   screen 1.55/595   snapshot 0.05   restore 0.09
  nuts passes: context 0.61  fixpoint 0.20  bundle_copy 0.17  corner 0.05  junctions 0.02 …

stage b (DNUTS opens):   22.4 s, metric 104(ovl3)->20, 4 moves, 253 full trials
  timing: replan 1.65/257   nuts 5.76/257   dnuts 6.11/257   screen 1.41/428  snapshot 0.03  restore 0.12
  nuts passes: context 1.33  bundle_copy 0.47  fixpoint 0.36  junctions 0.27  corner 0.17 …
  dnuts passes: place 0.35  bit_spans 0.21  keepout_cull 0.03  vias 0.01   (== 0.6 s total)
```

Two anomalies drive the investigation:

1. **The `dnuts` bucket (6.1 s) is 10× its instrumented C++ solve** (`dnuts
   passes` sum to only ~0.6 s). ~5.5 s is Python/marshalling overhead *around*
   the engine calls in `_run_detailed_nuts`.
2. **~7 s of stage b is outside every `timing:` bucket** — it is the metric
   evaluation (`_rr_stage_metric`'s lambda), which runs per trial but is not
   charged to replan/nuts/dnuts.

`cProfile` (short 3-iter stage-b run, 9 trials) attributes the per-trial DNUTS
call (`_run_detailed_nuts`, 31 ms/call) as:

| sub-cost | ms/trial | note |
|---|--:|---|
| `_bottom_up_dnuts_plan` (`hier.py:2855`) | **12.8** | recomputed every trial; placement-invariant |
| `buda.make_bus_segments` (C++) | **8.7** | rebuilds **all** BusSegments from all bundles every trial |
| eng1+eng2 `DetailedNUTSEngine.run` | ~2–3 | the actual bit placement |
| merge / list concat / keepouts | remainder | Python glue |

and the metric side:

| function | cumtime (3-iter) | note |
|---|--:|---|
| `_rr_disconnected_bits` (`ripup.py:72`) | **0.33 s** (0.14 s self, 27 calls) | full O(all-bundles × ConnTopology) scan **every metric eval**, ~0 in the common case |

(The one-time costs — the initial `run_planner hier 5` `optimize_topologies`
1.1 s, and the end-of-run BDB persistence `_persist_topologies` 0.33 s /
`persist_seg_busterms` 0.24 s — are **not** in the ripup loop: trials skip
persistence via `_rr_in_trial`. They are out of scope here.)

## Opportunities (prioritized)

### A. Cache `_bottom_up_dnuts_plan` across ripup trials — ~3.3 s in stage b
The plan `(ref_ids, copy_specs, skip_ids)` depends only on the placement + the
`check_template_tracks` verdict, **both invariant during a ripup run** (trials
move topologies, never geometry). Today it is recomputed on every
`_run_detailed_nuts` — 12.8 ms/trial × 257 ≈ **3.3 s**. It rebuilds `comps =
{c.name: c for c in self.bdb.all_components()}` (a DB hit) and calls
`bottom_up_congruence_index` ~21×/trial (193 calls over 9 trials in the profile).
Cache it on the session exactly like `_bottom_up_fixed_segments` already caches
`_bu_fixed_cache`, invalidated by `_plan_bottom_up_templates` (the re-plan reset).
**Low risk, high payoff, self-contained.** The natural first fix.

### B. Make `_rr_disconnected_bits` incremental — several seconds in stage b
It is the stage-b metric's primary term (`num_unplaced + _rr_disconnected_bits()`)
and runs on **every** metric evaluation (~3×/trial). Each call rebuilds a
`ConnTopology` + `check_topo` + `disconnected_islands_bridged` for **every bundle
in the design**, returning 0 in the common case (generation already drops
disconnected candidates). Only the trial's `_rr_dirty` bundles (the moved bundle
+ dogleg-dirty slots) can change their DISCONNECTED status, so:
- keep a per-bundle disconnected-bit count + a running total, computed once at
  ripup entry; per eval, recompute only the `_rr_dirty` bundles' contributions
  and adjust the total (mirrors the existing `_rr_dirty` scoped-restore pattern);
- and/or short-circuit: a bundle whose selected candidate's cached analysis has
  no DISCONNECTED violation contributes 0 without the union-find.

This removes a full-corpus topology scan from the hot metric path. **Medium
effort** (must thread the dirty set into the metric closure), **high payoff** —
it is the bulk of stage b's ~7 s of un-bucketed time.

### C. Incrementalize / cache `make_bus_segments` — ~2.2 s in stage b
`make_bus_segments(self.bundles, nuts_result, fp, bit_order)` re-derives BusSegment
rows for **all** bundles each trial (8.7 ms × 257 ≈ 2.2 s), though only the moved
bundle's segments changed. Options, increasing effort: (a) cache the returned
list and patch only the `_rr_dirty` bundles' rows between trials; (b) add a C++
`make_bus_segments_for(bundle_ids)` incremental entry point. **Medium/high
effort** (touches the C++ boundary), medium payoff.

### D. Avoid the per-trial NUTS `context` rebuild — 0.6 s (a) / 1.3 s (b)
Every incremental trial re-solves NUTS for the whole design; `context` (building
the sweep occupancy over all placed segments) is the largest NUTS sub-pass. The
warm-start single-bundle re-solve (`rerun_bundle_warm`, `warm_trials`) already
targets exactly this but is **opt-in / off by default** (measured cost-neutral
once the screen cuts trial volume — see the `ripup_reroute` docs). Worth
re-measuring a **default-on warm pre-filter specifically for bottom-up flows**,
where the fixed-context (all the locked template copies) is large and identical
across a stall-sweep's trials. **Higher effort/risk** (fidelity gating), so after
A–C.

### E. Cut trial *volume* in the stall sweep — complements A–D
Stage b's cost is trial-count-bound: `iter 4 … sweeping 135 deferred move(s)` +
`iter 5 … 127` are full cold trials the screen deferred. A–C cut per-trial cost;
this cuts the count: rank the deferred sweep by the **screen scores already
computed** and early-exit on the first improver (the sweep only needs *one*),
or warm-filter the sweep before the cold pass. **Medium effort**, and it
multiplies with A–C.

## Recommended order

1. **A** (cache the DNUTS plan) — biggest clean win, isolated, ~3 s.
2. **B** (incremental disconnected-bits) — removes the hidden full-corpus metric
   scan, several seconds.
3. **C** (incremental `make_bus_segments`) — ~2 s, needs a small C++ addition.
4. **E** then **D** — volume + warm-start, once per-trial cost is down.

A + B + C are all "recompute-once vs recompute-per-trial" fixes with no QoR
impact (identical results, fewer redundant computations) and should be
byte-identical-verifiable against the current corpus (`mix2`, `bigHalf`, `mix`,
`big2`) via the `done: metric …` endpoints. They target an estimated ~8 s of the
~22 s stage b (and ~0.6 s of stage a), i.e. a plausible ~35–40 % stage-b
reduction before touching trial volume (E) or the solver (D).

## Implemented — A + B

Both landed in this change (pure Python, no engine/QoR change):

- **A** — `_bottom_up_dnuts_plan` is now a cached wrapper over
  `_bottom_up_dnuts_plan_compute` (`hier.py`), invalidated wherever
  `_bu_fixed_cache` / `_template_track_verdict` are (re-plan, `align_bottom_up`,
  fresh `check_template_tracks`). Cuts the per-trial DNUTS overhead.
- **B** — `_rr_disconnected_bits` memoizes the per-candidate DISCONNECTED verdict
  by `(topo_uid, bid)` in a run-scoped `_rr_disc_memo` (init in `_rr_t_init`,
  cleared at run exit), so a metric eval pays the ConnTopology + union-find only
  for the moved bundle's new candidate instead of rescanning every bundle.

**Measured** on `flow/rnr/mix2_fast_bottomup` (same build, A+B vs baseline),
metric endpoints **byte-identical** (a: 21→3 / 10 moves / 109 trials; b:
104(ovl3)→20 / 4 moves / 253 trials):

| | baseline | A+B | Δ |
|---|--:|--:|--:|
| stage a | 5.11 s | 5.05 s | — (no DNUTS / disconnected metric) |
| **stage b** | **21.57 s** | **15.82 s** | **−27 %** |
| stage-b `dnuts` bucket | 6.36 s | 4.85 s | −1.5 s (A) |
| stage-b metric-eval (un-bucketed) | — | — | ≈ −4.2 s (B) |

Fast tier green (1189 passed).

## Follow-up — `_open_segments` memo, and why C was deferred

**Re-measured on post-A/B `main`** (113-trial stage-b run), the remaining
ripup-loop hotspots are `buda.screen_candidates` (~1.4 s, C++), the
`_rr_disconnected_bits` residual (~1.3 s, the per-bundle loop + `topo_uid`
keying), and `make_bus_segments` (~1.0 s, item C).

**Shipped (safe, isolated):** `_open_segments` is now memoized by the identity
of `self.detailed_result` (`ripup.py`). The contender scan calls it several times
per iteration on the same result (`_rr_contenders` / `_rr_contention_centres` /
`_rr_open_bundles` / the stage-b edge walk); a solve always *replaces*
`detailed_result` with a new object and selections change in lockstep, so object
identity is a sound, self-validating key with no commit/restore coupling.
Byte-identical (a: 21→3, b: 104→20); modest (~0.4 s / ~2–3 % of stage b — the
real flow re-calls it less than the micro-profile suggested).

**C investigated in depth, then abandoned — it is a refactor, not a cache.** A
dedicated dig into where `make_bus_segments`' ~12–16 ms/call actually goes
(instrumented `loop1` vs `loop2`, plus a `fast/slow/null/fpmiss` counter on a
cached-analysis-reuse attempt) found the cost is NOT a re-derivation a cache can
skip:

- **`loop2` (build the `BusSegment` rows) ≈ 0.06 ms** — negligible; caching the
  *output* saves nothing.
- **`loop1` (per-bundle connectivity analysis) ≈ 7 ms** — but NOT a redundant
  re-fingerprint of a valid cache. In this hier flow every selected candidate's
  `analysis_cache_` was populated in a **cell-local frame**, so its `fp_uid`/
  `fp_rev` mismatch the session floorplan (`fpmiss=100/100`) and `analyze()`
  **recomputes** the six passes every call. Reusing `analysis_cache_` (the
  attempted fast path) is a no-op here — and would also break the deliberate
  "analyze re-fingerprints every call so a stale cache is structurally
  impossible" invariant (`topology_analysis.h`).
- **≈ 9 ms is pybind argument marshalling** — `buda.make_bus_segments(self.bundles,
  …)` deep-**copies** the whole `std::vector<BundleWrapper>` (every candidate of
  every bundle, not just the selected one) on each call. Confirmed: an in-function
  cache never persists (each call gets fresh copies), which is *why* the reuse
  attempt showed `fpmiss` every call.

So the real C win requires two deeper changes, neither a bounded/verifiable
cache: (1) stop deep-copying all bundles across the binding (a signature/holder
refactor, e.g. pass only the selected candidates + net counts + `nuts_result`, or
a non-copying wrapper), and (2) make hier analyses reuse a session-frame cache
(or hand the NUTS-computed `ConnSeg` straight to DNUTS). Combined with the
caching-hostile commit/restore loop (a "skip contenders whose contention is
unchanged" cache was tried at `ripup.py` ~L1625 and **reverted** — bigHalf
stranded 52 opens), **C is out of scope as an incremental optimization**. The
A/B + `_open_segments` wins stand; **E** (implemented below) and **D** remain the
lower-risk follow-ups.

## Implemented — E (stall-sweep trial-volume cut)

`_rr_scan_moves` gained a `first_improving` flag, passed `True` at the two stall
sweeps only (the deferred-move sweep and the warm-rescued cold sweep). The main
contender scan is unchanged — it still full-scans each contender for its
BEST-metric move — but a stall sweep now stops a contender's scan on its FIRST
strictly-improving move instead of trialing the rest of its (already
screen-sorted, best-first) list for an optimum the caller discards anyway: both
sweeps commit the first improving CONTENDER, so within that contender only one
improving move is ever used.

The **stall certificate is preserved**: the early exit fires only when an
improver exists, so a sweep that finds nothing still trials its whole list —
the "no improving re-route ⇒ stop / go global" verdict rests on the same full
trial set as before. The change is **trajectory-affecting** (which improver
commits can differ), not byte-identical; it is a no-op whenever no stall sweep
fires that iteration.

**Measured** (same build, E vs current `main`):

| flow | stall sweep fires? | endpoint (a / b) | trials (a / b) |
|---|---|---|---|
| `mix2_fast_bottomup` | yes | 21→3 / 104(ovl3)→20 (identical) | 109→**104** / 253→**241** |
| `bigHalf` | no (inert) | 4→0 / 92→0 (identical) | 3 / 5 (unchanged) |
| `mix` | no (inert) | 7→0 (identical) | 19 (unchanged) |

Where the sweep fires (`mix2`), trial volume drops with a byte-identical metric
endpoint; where it does not (`bigHalf`, `mix` reach improvers in the screened
scan), E is provably inert. The saving scales with how often the screened scan
stalls into the deferred sweep — modest on this corpus, larger on flows that
lean on the sweep. Fast tier green (1189 passed).

## D measured out — the warm pre-filter's margin is gone

A default-on warm pre-filter (item **D**) was measured on the **post-A/B/
`_open_segments`/E** build with the warm-study probe (`BUDA_RR_WARM_STUDY=1`,
which samples the warm single-bundle re-solve alongside every cold trial and
reports fidelity + per-solve cost) on `mix2_fast_bottomup`:

| stage | trials | accept agreement | false-reject | warm ms | cold ms | cold/warm |
|---|--:|--:|--:|--:|--:|--:|
| a | 104 | 84/104 (81%) | 0 | 23.8 | 26.6 | **1.1×** |
| b | 241 | 241/241 (100%) | 0 | 45.3 | 65.6 | **1.45×** |

**Fidelity is not the problem** — stage b is a perfect 241/241 accept agreement
with **zero false-rejects** in both stages, so D would be *safe*: a warm-rejected
move is still cold-swept at the completeness certificate, and warm never wrongly
discards a real improver.

**The cost margin is the problem.** D's premise is "cold trials are expensive, so
pre-filter them cheaply." The warm pre-filter only pays off past the documented
crossover of **cold ≥ 3× the warm eval**. On the current build cold is only
**1.1–1.45×** the warm eval — nowhere near 3×. Historically the warm eval was
4.6–6× cheaper on bigHalf; that gap is exactly what the A/B (DNUTS-plan cache −3.3 s,
disconnected-bits memo −4.2 s) and `_open_segments` wins closed. By cutting the
per-trial *cold* cost, those merges lowered the very cost D was meant to avoid,
and E then trimmed the sweep *volume* D was meant to filter — each prior win
narrowed D's remaining margin.

The arithmetic on the dominant regime: stage b is mostly stall-sweep trials
(~135 + ~127 deferred moves), and the certificate cold-sweeps every
warm-rejected move in a stalling iteration *anyway* — so warm's ~45 ms/eval on
those is unrecovered overhead. Expected wall-clock on `mix2_fast_bottomup`, D's
own target flow: **≈ 0 %, plausibly net-negative.**

**Disposition:** like C, **D is measured out, not shipped.** The one regime where
it could still win is a flow whose cold trial is genuinely expensive (a huge
locked fixed-context making the NUTS `context` rebuild hundreds of ms, so
cold ≥ 3× warm). If such a flow appears, re-run this same probe on it and gate a
default-on warm pre-filter to that flow class only if `cold/warm ≥ 3×` holds
there. On today's corpus it does not.

This closes the `ripup_reroute` speed-up arc: **A/B** (−27 % stage b) was the
prize, **`_open_segments`** and **E** were clean marginal trims, and **C** and
**D** are both correct-in-principle but not worth the complexity at current
scale.
