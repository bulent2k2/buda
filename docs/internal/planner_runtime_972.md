# Issue #972 — `run_planner hier` on bigsoc: profile and options

Measured 2026-10-07 on a 4-CPU / 15 GB container, branch
`claude/hierarchical-floorplan-buda-sbfy7o` (the vehicle's branch) plus the
instrumentation and two prototypes described below.  `tclsh` 8.6.14,
Python 3.13, GCC `-O3 -march=native`; the profiled runs use the
`-DBUDA_PROFILE=ON` build (same codegen, symbols kept, frame pointers).

## Headline

| dial | wrappers | bands | planner as shipped (4 scoring threads) | + incremental `layer_load` | + ONE scoring thread |
|---|---|---|---|---|---|
| NQ=8  | 944   | 3.74 M | 47.2 s (40.1 s on the profiled build) | 17.8 s | **6.2 s** |
| NQ=16 | 1,664 | 5.68 M | 247.6 s | 136.5 s | **9.3 s** |
| NQ=32 | 3,104 | 16.0 M | not run (hours) | not run | **32.3 s** |
| NQ=62 | 5,804 | 22.3 M | > 25 min, unfinished (issue) | not run | **84.5 s** |

Every planner decision line (`[Planner] Bundle … -> topo …`, rip-ups,
refine lines, the level summary) and the `run_nuts` metrics are identical
between the shipped code and every faster configuration at NQ=8 and NQ=16
(1,033 and 1,836 lines compared).

Two things, not one, make the planner quadratic on this design, and the
second is not in the issue:

1. **`plan_bundle` re-sums every band of every cut** for the kBalance
   tie-breaker (the issue's item 1).  2.0–2.1 ns per band, so 7.7 ms per
   call at NQ=8 and 12 ms at NQ=16; 53 % and 27 % of the planner's wall.
   Maintaining the per-cut total in `GlobalCut::add_usage` makes it
   O(cuts) (0.06 ms per call) and is decision-identical (measured).
2. **The parallel candidate scoring is a net LOSS at this scale.**  Each
   `plan_bundle` call spawns fresh `std::thread` workers, and each worker's
   scoring overlay is a `static thread_local` sized to the WHOLE band set
   (`thread_overlay_`: 8 B value + 4 B stamp per band), so a new thread
   re-allocates and zeroes it on every call — 45 MB per worker per call at
   NQ=8, 68 MB at NQ=16, 266 MB at NQ=62 — and the main thread waits on
   the join.  The comment on `thread_overlay_` ("zero setup per plan_bundle
   call") is true only of the main thread, whose thread_local persists.
   With one scoring thread the SAME binary plans NQ=8 in 6.2 s instead of
   17.8 s and NQ=16 in 9.3 s instead of 136.5 s.  This is also why the
   issue saw the process at 17–25 % CPU: the workers are memory-bound
   zeroing, not scoring.  `BUDA_PLAN_THREADS` cannot override it from the
   usual launchers because `--threads N` / `-j N` / `BUDA_THREADS_REQUEST`
   set `plan_threads_` explicitly, which outranks the env; `bin/btcl -j 1`
   or `bin/buda --threads 1` is the immediate lever.

(Both are landed: fix 1 by #974, the worker overlay by B1 below, 2026-10-11,
together with the rip-up victim index E and the merged refine probe C.)

After those two, the planner is dominated by the per-candidate scoring
proper (single-thread profile below), and the next quadratic term waiting
at the reticle dial is the rip-up VICTIM RANKING (item 3 of the issue):
3.3 s at NQ=32 for 160 STRICT failures × ~1,000 committed bundles.

## Landed 2026-10-11: E (victim index) and C (merged refine probe)

Both are in `src/congestion_planner.cpp`, decision-identical by construction
and measured (every `[Planner]` line and the whole flow log but the runtime
numbers identical at NQ=8, 16 and 32 against a build of the merge base
`b1eeceba`; `test_planner_rank_index.py` pins it on `flow/soc_small.buda`,
where both paths run — 3 rip-ups, 33 refined bundles — and fails under a
mutation of either: dropping one indexed candidate, or shifting the merged
`keep` score by one).  Single scoring thread (`-j 1`), the shipped flat
recordings replayed through `bin/buda`; `[PlanProf]` seconds.

| dial | planner total | rank (fails, plans scored) | refine (plans) | commit |
|---|---|---|---|---|
| NQ=8  base | 4.38 | 0.169 (49, 17,088) | 1.27 (1,888) | 0.051 |
| NQ=8  E+C  | 3.72 | 0.027 (49, 300) | 1.12 (944) | 0.067 |
| NQ=16 base | 11.57 | 0.888 (107, 68,661) | 3.21 (3,328) | 0.115 |
| NQ=16 E+C  | 9.62 | 0.094 (107, 560) | 3.06 (1,664) | 0.136 |
| NQ=32 base | 35.07 | 5.14 (247, 258,283) | 7.79 (6,208) | 0.271 |
| NQ=32 E+C  | 29.63 | 0.617 (247, 1,817) | 7.53 (3,104) | 0.371 |

**E does what the estimate said.**  The ranking used to score every
committed plan on every STRICT failure (258,283 `plan_band_overlap` calls
at NQ=32); the band → bundle index (`band_index_`, the inverse of
`charge_log_`, maintained in `commit_plan` in lockstep with the log) names
the 7 or so plans per failure that hold a charge on a contended band
(1,817 in all; 300 and 560 at NQ=8 and 16), and only those are scored —
6.3x, 9.4x and 8.3x on the phase at NQ=8, 16 and 32,
with the growth as fails × committed gone.  The VALUE each candidate is
sorted by is still `plan_band_overlap`, so the sort sees the same pairs: a
plan's bands at commit time are the bands a re-derivation sees at rank
time under the geometric spread modes (`band_span_charge` 1 and 2), which
is the condition `band_index_exact_()` states; the greedy modes (3–5,
which read live occupancy) and the legacy mode 0 (no log) keep the full
scan, as does `BUDA_RANK_SCAN=1` for a study.  Its cost is the index
maintenance in `commit_plan`: the commit phase +0.02–0.10 s per dial, a
small fraction of what the rank phase returns.  Extrapolated to the NQ=71
reticle dial's 23.1 s rank phase: 2–3 s.

**C was over-estimated, and the measurement is the correction.**  The
estimate read "two full `plan_bundle` sweeps per revisit" and expected
half the refine time; the pinned `keep` probe is a sweep over ONE
candidate, so what the merge removes is one call's fixed cost and one
candidate's scoring per revisit, not half the work: refine −12 % at NQ=8,
−5 % at NQ=16, −3 % at NQ=32 (plans halved in every row, 1,888 → 944 and
so on).  Its worth at the reticle dial is a second or two, not the 17 s
estimated.  Kept because it is free and exact: `plan_bundle` fills an
optional `probe_out` with the result a call pinned to `probe_topo` would
return, read off the same reduction (`score_candidate_` never reads the
pin flag, and every candidate is scored from the same read-only base
state); a GROUP pin or a `best_topo` outside the pool keeps the two calls,
and `BUDA_REFINE_TWO_CALLS=1` keeps them for a study.

The whole-planner numbers (−15 %, −17 %, −16 %) carry the usual caveat of
this box: the STRICT phase, whose work did not change, read −13 % to
−15 % between the two builds too, so the phase deltas above are the
measurement and the totals are indicative.  With E and C in, the ranked
list below stands with D, F and B (the worker overlay) as the remaining
planner levers; abstract NUTS is the bigger term at NQ ≥ 32 (88 s against
the planner's 39 s wall at NQ=32 here).

## Landed 2026-10-11: B1 (persistent scoring workers)

`plan_bundle` spawned `n-1` fresh `std::thread`s per call and each one's
scoring overlay is a `thread_local` sized to every band of the grid, so a
worker re-created it — 45 MB at NQ=8, 68 MB at NQ=16 — on EVERY call
(finding 2 above; the 4-thread planner was 2–15x slower than the 1-thread
one).  The workers now come from a process-wide persistent pool
(`ScoringPool` in `congestion_planner.cpp`: detached threads, one closure
per worker per call, the caller runs the same closure and waits for the
jobs it dispatched), so a worker's overlay is sized once per grid
generation exactly as the main thread's always was.  Process-wide rather
than planner-owned because planners are copied (the trial sweep makes one
per worker) and a copy must not carry threads; the sweep's copies plan
single-threaded, so the pool is never entered twice at once by one flow.
Decision-identical by the existing argument (the thread count never
changes a decision; `test_planner_parallel_scoring.py`), and
`test_planner_scoring_pool.py` reads the new `plan_overlay_inits()` probe:
a 4-thread multi-bundle plan adds at most one overlay (re)size per thread,
where per-call spawning added three per call.  The cost is memory that now
PERSISTS instead of being paid and freed per call: up to `n-1` overlays of
12 B per band alongside the main thread's (0.8 GB at NQ=62 with 4
threads), which is what B2 — an O(touched) reset — would shrink.

| dial | 1 thread (E+C) | 4 threads before B1 (post-#974) | 4 threads with B1 | scoring block, 4 thr before → after |
|---|---|---|---|---|
| NQ=8  | 3.72 | 13.80 | **2.59** | 12.4 → 1.58 |
| NQ=16 | 9.62 | 136.9 | **6.17** | 133.0 → 3.87 |

Four threads now beat one (1.44x and 1.56x on the whole planner, 1.8x and
2.0x on the scoring block, on a 4-CPU box whose main thread also carries
the serial phases), and the shipped default — `btcl`/`buda` with no `-j`
run half the machine's threads — no longer needs `-j 1` as a workaround.
Decisions identical to the 1-thread run at both dials.

## Landed 2026-10-11: B2 (sparse scoring overlay)

The overlay B1 made persistent was a flat array over EVERY band of the grid
(a value and a stamp each, 12 B per band per scoring thread): 64 MB per
thread at NQ=16, 268 MB at the NQ=62 reticle dial, times the thread count,
resident for the process.  It is now an epoch stamp per CUT plus, per cut,
the short list of (band, value) the candidate being scored has written —
sized by the cuts (thousands) and by what a candidate touches, not by the
grid.  A read is one array access on the cut's stamp and, only on a cut
this candidate wrote, a scan of its few entries; the rollback is still
`++epoch`.  Values are seeded from the committed base on first touch and
accumulated with the same `+=`, so every read returns the bit-identical
double (the earlier note that a hash map read ~15 % slower stands: the
stamp stays dense over the cuts, and there is no hashing).  `band_base_`,
the flattened band index the array needed, is gone.

| dial, threads | planner (dense overlay) | planner (sparse) | peak RSS after `run_planner`, dense → sparse |
|---|---|---|---|
| NQ=8, 1  | 3.72 s | 3.71 s | — |
| NQ=8, 4  | 2.59 s | 2.46 s | — |
| NQ=16, 1 | 9.62 s | 9.60 s | — |
| NQ=16, 4 | 6.17 s | 5.71 s | 579 → 343 MB (in-process, the whole session) |

Decisions identical to the merge-base run at both dials and both thread
counts (every `[Planner]` line); the per-read scan costs nothing
measurable, and the 236 MB the NQ=16 session gives back is the four
overlays (4 × 64 MB) minus the per-cut lists.  At the reticle dial the
same arithmetic returns about 1 GB at four threads.

The per-cut list's first form was one (band, value) pair per entry,
rescanned on every insertion.  Codex (P2 on #979) named the path that
makes it quadratic: under the default spread an oversized bus charges a
contiguous ascending range of B bands per crossed cut, so B insertions
cost O(B²) comparisons where the flat array cost O(B) — on a fine Hanan
grid (the `ariane133`-class imports) B can be large.  The list is now
ascending RUNS of bands (`CandOverlay::CutVals`: `{b0, n, off}` into a
value vector), so a write that continues the cut's last run is one
append, a write inside a run one index, and a read a scan over the few
runs — O(1) amortised per band written; the greedy spread modes
(nearest-first order) simply open more runs.  Measured three ways on a
synthetic fine-grid flow (112 small staggered blocks, forty 96- and
128-bit buses crossing them, 40 bundles / 1,265 candidates; planner
≈ 1.1–1.5 s): dense, pair list and runs within the run-to-run noise of
each other and decision-identical — the quadratic term does not show on
that vehicle, so the restructuring is an argument from the shape of the
spread, not a measured win, and costs nothing where it does not bite.
Re-timed on the recorded dials BACK TO BACK against the pair-list build
(the container had moved to a slower host between the two sessions — the
pair list itself read 4.7–4.9 s at NQ=8 where the table above has 3.71 —
so only the alternating pair is comparable): NQ=8, 1 thread, pair list
4.74 / 4.87 s against runs 4.55 / 4.61 s; NQ=16, 4 threads, 7.82 against
7.88 s — within noise, with the scoring block 3 % faster at NQ=8 and 2.5 %
at NQ=16.  Peak RSS at NQ=16 with 4 threads still 343 MB; decisions
identical at both dials and both thread counts.

## Landed 2026-10-11: the layer-only re-persist

The dead-span escalation's selective re-persist rewrote every expanded
instance whose fingerprint changed (above): bundle row, net and busterm
links, the selected topology with its segments, the seg-busterm
annotations and the pinned-layer meta — to change `assigned_layer` on a
few `topology_segment` rows.  `_persist_planner_output` now recognises a
fingerprint that differs in the assigned layers ALONE
(`_fp_differs_in_layers_only`), drops the instance's routing rows the way
the rewrite does (`BDB::clear_bundle_routing`: bus/net segments and vias
plus the route_snapshot, which every selective site rewrites right after)
and updates the selected candidate's segment layers in place.  Any other
difference — selection, pins, lock, NDR stamp, USER extras — still takes
the full rewrite.  `test_persist_layer_only.py` runs `flow/soc_small.buda`
through `run_nuts` both ways (`BUDA_PERSIST_FULL=1` is the control), dumps
each BDB as text and compares every table's rows as a sorted multiset:
identical, with the layer-only path having served the escalation's
instances in the default arm.  `run_nuts` NQ=16 11.9 → 9.6 s (same
build, `BUDA_PERSIST_FULL=1` against the default), NQ=32 68.7 → 64.2 s
(in-process, one thread; the engine's own seconds unchanged).  Modest,
because what dominates the command from here is the escalation's full
re-solves — PR #977's measurement, where its option B (a floor on late
moves) is the lever.

## Abstract NUTS: the time is persistence, not placement (2026-10-11)

`run_nuts` is the next command at NQ ≥ 32 (88 s against the planner's
39 s wall at NQ=32), and its engine is not where the time goes.  The
engine's own per-pass timers (`NUTSResult.pass_seconds`, every stage of
`NUTSEngine::run` charged) total **1.07 s of the command's 16.4 s at
NQ=16 and 9.6 s of 95.0 s at NQ=32** (in-process, one thread, no CLI log
capture) — corner 45–50 %, fixpoint 17–28 %, repair 17 %, tighten 9–10 %
of that engine time.  A py-spy `--native` sample of the command on the
symbols-kept build (`cmake -B build-prof -DBUDA_PROFILE=ON`) puts the
rest in the SESSION's persistence of the result into the BDB, inside the
`_batched` transaction wrapper (53 % of the command's samples at NQ=16):

| where | share of `run_nuts` samples, NQ=16 |
|---|---|
| `_persist_nuts` → `_persist_bundle_vias` | 35 % (33 %) |
| `_persist_planner_output` → `_add_expanded_bundle` → `BDB::add_busterm` | 18 % (14 %, 12 %) |
| `_escalate_dead_low_segments` (the healersAhead re-solve, legitimate) | 17 % |
| `_persist_topology_annotations` (`persist_seg_busterms`) | 12 % |
| SQLite `sqlite3_step` / `sqlite3_prepare_v2` | 13 % / 5 % |
| `malloc` / `operator new` | 19 % / 17 % |

Two mechanisms, one fixed:

1. **A by-value vector read per bundle** — `_persist_bundle_vias` read
   `nuts_result.segments` (a by-value pybind field: every read copies the
   whole vector into a Python list of `TrackSegment` copies) once PER
   BUNDLE to pick out that bundle's segments: bundles × segments copies,
   the `malloc`/`operator new` share above.  FIXED: `_persist_nuts` copies
   the result once, groups it by bundle in one pass and hands each bundle
   its map (same rows, same order).  `run_nuts` NQ=16 16.4 → 11.0 s (−33 %),
   NQ=32 95.0 → 68.7 s (−28 %), the engine's 1.05 / 9.5 s unchanged — so
   59 s of the NQ=32 command are still outside the engine, in the
   remaining persistence below.
2. **A statement prepared per row** — the write paths (`add_bus_via`,
   `add_busterm`, `add_bundle_net`, `add_bundle_busterm`, …) build a
   `Stmt` per call, so every row pays `sqlite3_prepare_v2`; only the hot
   READ paths cache theirs (`_q_*`).  Open: cache the write statements the
   same way (reset, never finalize), which is what the 5 % prepare share
   and part of the step share are.  The `_persist_route_snapshot` digest
   also re-reads every bundle's rows through four queries per bundle.

Re-sampled after fix 1 (NQ=16, shares of the command's samples):
`_escalate_dead_low_segments` 25 % (the re-solve), `_persist_planner_output`
23 % → `_add_expanded_bundle` 18 % → `BDB::add_busterm` 15 % and
`_persist_topology_annotations` 16 %, `sqlite3_step` 18 %, prepare 2.6 %,
`_persist_nuts` 3 %.  That planner re-persist is the escalation's own
(`selective=True`, Codex #351) and it is doing honest work: the
escalation moved **638** segments here, so hundreds of expanded instances'
fingerprints changed and each one is rewritten whole — bundle row, net
and busterm links, the selected topology with its segments, the
seg-busterm annotations — when all that changed is
`topology_segment.assigned_layer` on a few rows.  The lever that remains
is a LAYER-ONLY re-persist for an expanded instance whose selection,
pins and candidate set are unchanged (update the assigned layers in
place, fingerprint split into a shape part and a layer part), which has
to stay identical to the full rewrite row for row — BUILT, see the
section above; the
per-row statement preparation is 2.6 % and not worth a cache on its own.
The dogleg-adoption and dead-span-escalation re-solves stay; what the
remaining persistence costs at the reticle dial is the next measurement
(`run_nuts` was 514 s there with the planner at 111 s).

## Method

* `BUDA_REPLAN_PROF=1` already timed `plan_bundle`'s `layer_load` and
  scoring blocks but only printed from `replan_bundle_ripup`.  The
  prototype adds phase timers to `optimize_topologies` (reservation
  park/release, first STRICT plan, victim ranking, rip-up ladder,
  fallbacks, commit, refine) and prints one `[PlanProf]` line at the end
  (to stderr, i.e. into the flow log under the CLI), plus the
  `analysis_cache_counters()` for the planner alone.
* Sampling: `py-spy record --native` on the engine process, frames resolved
  against `nm` of the profiling build.  py-spy samples only the main thread
  and drops idle samples, so with 4 scoring threads its shares describe the
  main thread's ACTIVE time (which over-represents the serial parts); the
  single-thread run (`--threads 1`) is the faithful scoring breakdown.
* Runs: the Tcl flow recorded once per dial with `BUDA_RECORD`, then replayed
  through `bin/buda <trace> --no-viz --threads N --report-json` so every
  configuration plans the identical design; decisions compared by diffing
  the planner's log lines.

## Phase timers (planner wall, seconds)

| run | total | reserve | STRICT | rank | ladder (plans / rip-ups) | fallback | refine (plans) | `layer_load` | scoring |
|---|---|---|---|---|---|---|---|---|---|
| NQ=8 shipped, 4 thr  | 47.2  | 0.27 | 16.8 | 0.25 (50 fails, 16.8 k scored) | 6.9 (418 / 25) | 0.45 | 22.3 (1,888) | 25.1 | 20.2 |
| NQ=8 fix 1, 4 thr    | 17.8  | 0.26 | 7.3  | 0.23 | 3.0 | 0.21 | 6.6 | 0.20 | 16.2 |
| NQ=8 fix 1, 1 thr    | 6.2   | –    | 2.5  | –    | –   | –    | 1.4 | –    | 4.7 |
| NQ=16 shipped, 4 thr | 247.6 | 0.88 | 103.0 | 0.92 (83 fails, 49.7 k scored) | 24.3 (431 / 50) | 1.96 | 115.8 (3,328) | 65.9 | 175.4 |
| NQ=16 fix 1, 4 thr   | 136.5 | 0.53 | 59.3 | 0.70 | 15.5 | 1.18 | 58.9 | 0.39 | 133.0 |
| NQ=16 fix 1, 1 thr   | 9.3   | 0.45 | 3.8  | 0.62 | 1.2  | 0.36 | 2.7 | 0.19 | 7.0 |
| NQ=32 fix 1, 1 thr   | 32.3  | 1.44 | 10.9 | 3.33 (160 fails, 160.7 k scored) | 7.6 (1,249 / 56) | 1.65 | 6.7 (6,208) | 0.64 | 23.1 |
| NQ=62 fix 1, 1 thr   | 84.5  | 2.92 | 26.4 | 13.4 (297 fails, 553 k scored) | 17.5 (2,839 / 121) | 3.34 | 19.5 (11,608) | 1.55 | 58.2 |

Per call / per candidate:

| run | `layer_load` per `plan_bundle` call | scoring per candidate |
|---|---|---|
| NQ=8 shipped  | 7.68 ms (2.05 ns/band) | 1.20 ms |
| NQ=16 shipped | 12.1 ms (2.13 ns/band) | 6.28 ms |
| NQ=8 fix 1, 1 thr  | 0.06 ms | 0.28 ms |
| NQ=16 fix 1, 1 thr | 0.04 ms | 0.25 ms |
| NQ=32 fix 1, 1 thr | 0.06 ms | 0.41 ms |
| NQ=62 fix 1, 1 thr | 0.08 ms | 0.53 ms |

Single-threaded, scoring per candidate is flat-to-mild across an 8x design
(0.28 → 0.25 → 0.41 → 0.53 ms); four-threaded it grows with the band count
(1.2 → 6.3 ms), which is the overlay re-initialisation, not the work.

## Where scoring's time goes (single-thread sample, NQ=8, 597 samples in the planner)

Inclusive shares of the planner's samples:

| share | frame | note |
|---|---|---|
| 76 % | `score_candidate_` | |
| 60 % | `for_each_band_w` → `for_each_cut_` | the band-charge geometry, through a `std::function` per call |
| 46 % | `cong_cost_segment` | |
| 43 % | `best_band_perp` | the band-choice loop: one `cong_cost_segment` per band in the slide window, per segment, per layer |
| 21 % | `_Rb_tree` (std::set / std::map) | the `contended` `std::set<pair<int,int>>`, `cut_index_` map lookups, `layer_load` / `cand_share_use` maps |
| 16 % | `usable_band_cap` | |
| 15 % | `low_seg_obstructed` | a linear scan of EVERY leaf rect (674 here, 4,238 at NQ=62), twice, per LOW-layer segment evaluation |
| 12 % | `malloc` / `operator new` | `std::function` captures, per-candidate vectors |
| 7 %  | `routed_extent` | the same per-leaf scan, once per LOW-layer `for_each_cut_` |
| 3 %  | `analyze` (ConnTopology) | the cache HITS: planner-only counters read computes = candidates (6,352 = 6,352) |

So the shape is `candidates × segments × layers × (bands in the slide
window) × (cuts the segment crosses)`, with a per-leaf linear scan inside
the LOW-layer branch.  Nothing in it is O(grid) per call any more; it
grows with the bundle's own geometry and, through the leaf scan, with the
design's leaf count.

What did NOT pay: the per-candidate `analyze()` builds a private
`std::map<std::string, Rect>` of every floorplan block twice (in
`derive_slide_ranges` and `pin_relay_taps`); the 4-thread main-thread
samples put 24 % on those two lines at NQ=16, but replacing them with
in-place `has_block`/`get_block_bounds` lookups measured 133.0 s vs
134.5 s — the samples were showing the main thread's share while the wall
was the workers' zeroing.  The change is byte-identical by construction
and harmless; it is in the prototype but is not a lever.

## Findings against the issue's list

1. `layer_load` full scan — confirmed and the largest single serial cost;
   fixed in the prototype, 2.2x / 1.75x on its own.
2. `apply_reservation` walking every cut — real but small: 0.26–1.44 s
   per run (park + release), ~0.4 % of the shipped planner.  Worth indexing
   only after the rest.
3. Rip-up ranking over every committed bundle — small at NQ=8/16
   (0.25 / 0.9 s) but the fastest-growing phase once the others are gone:
   3.3 s at NQ=32 and 13.4 s at NQ=62 (16 % of that planner), with
   `scored` (committed plans ranked) 16.8 k → 49.7 k → 161 k → 553 k and
   `fails` 50 → 83 → 160 → 297.  The ladder's own trials (`plans`) grow
   the same way: 17.5 s at NQ=62.
4. `[PlanProf]` printed at the end of `optimize_topologies` — done in the
   prototype.
5. `optimize_topologies(bundles, max_iterations)` ignores its argument —
   confirmed (`int /*max_iterations*/`), unchanged.
6. NOT in the issue: the scoring thread pool (finding 2 above), which is
   worth more than everything else combined on this vehicle.

## Options, ranked by measured or expected gain

**A. Land the incremental `layer_load` (prototype, measured).**
`GlobalCut` keeps `total_usage_` beside `band_usage_`, updated in
`add_usage` / `reset_usage` / `init_bands` (every usage write goes through
them); `plan_bundle` sums per cut instead of per band.  Gain: 2.2x at NQ=8,
1.75x at NQ=16, growing with the grid.  Risk: the per-layer sum is now
accumulated in chronological rather than band order, so it can differ from
the full scan in the last ulp; it feeds `kBalance * load / max_load`, which
is compared under a 1e-6 epsilon.  Measured identical here; the gate for
landing is `tools/qor_corpus.py --vs main` (not run in this session).
`BUDA_LAYERLOAD_SCAN=1` keeps the old scan as a study control.

**B. Fix the scoring workers (the big one) — B1 and B2 LANDED 2026-10-11,
see above (NQ=16 at 4 threads 136.9 → 5.71 s; peak RSS −236 MB).**  Three shapes, cheapest first:
  * B1 — stop paying per call: make the worker overlay persistent (a
    planner-owned pool of `n_threads` overlays handed to the workers, or a
    persistent thread pool), so the O(bands) allocation happens once per
    grid generation as it already does on the main thread.  Expected: the
    4-thread run at least matches the 1-thread run and the parallel speed-up
    returns (3.3x of 4 on the scoring block).
  * B2 — make the overlay reset O(touched) rather than O(bands): record the
    slots a candidate wrote and clear only those (the epoch stamp already
    makes `clear()` O(1); it is the INITIAL `assign` that costs).  Needed
    anyway for NQ=62, where even a one-time 266 MB × 4 per grid is fine but
    a per-call one is not.
  * B3 — until B1/B2 land, gate the auto thread policy on the band count as
    well as the candidate count (today: `ncand >= 8` only), or default hier
    planning to one scoring thread; and document `-j 1` / `--threads 1` as
    the lever (it is not `BUDA_PLAN_THREADS`, which the launchers' explicit
    request outranks).
  Decision-identical by construction (the thread count never changes a
  decision; corpus-verified already).

**C. Halve the refine pass — LANDED 2026-10-11, see above; the "two FULL
sweeps" premise was wrong (the pinned probe scores one candidate), so it
bought 3–12 % of refine, not half.**  Each revisit runs TWO `plan_bundle`
sweeps against the same ripped-up state — a `keep` probe pinned to the old
candidate and the unrestricted `np` sweep — and the old candidate's score
inside the `np` sweep IS the `keep` score (same state, same candidate,
per-candidate scoring is independent and deterministic; `costs_out` already
returns every candidate's score).  Reading `keep` off the `np` sweep halves
refine (22 → 11 s at NQ=8 shipped; 1.4 → 0.7 s single-threaded) and is
decision-identical by construction.  Refine is 40–47 % of the shipped
planner, so this is ~1.3x overall.

**D. Index the leaf rects.**  `routed_extent` and `low_seg_obstructed` scan
every leaf rect for every LOW-layer evaluation (22 % of scoring at 674
leaves; 4,238 at NQ=62).  A per-axis interval index over leaf rects (sorted
by perp-start with a prefix max of perp-end, like `prune_unreachable_partner_windows`'s keepout index) answers "leaves whose perp range
contains this perp" in O(log n + hits).  Same answers, same order if the
hits are visited in cache order.  Expected ~15–20 % of scoring.

**E. Rip-up ranking from a band → occupant index — LANDED 2026-10-11, see
above (8.3x on the phase at NQ=32).**  `charge_log_` already
records, per (bundle, seg), every (cut, band, amount) charged; an inverted
index (cut, band) → bundles lets the ranking sum `plan_band_overlap` over
only the bundles that load a contended band instead of every committed
plan.  Same ranking values (the sum over contended bands of each bundle's
charge is exactly what `plan_band_overlap` recomputes).  Bounds the one
phase still growing as fails × committed (3.3 s at NQ=32).

**F. Cheaper band geometry.**  `for_each_band_w`/`for_each_cut_` take a
`std::function` per call and `collect_overflow_bands` fills a
`std::set<pair<int,int>>`; templating the visitor and keeping `contended`
as a sorted vector removes most of the 12 % malloc and 21 % tree time.
Mechanical, byte-identical; ~20–25 % of scoring.

**G. `best_band_perp`'s band loop** is the structural term (43 %): one full
`cong_cost_segment` per usable band in the slide window.  The B1 note in
the source says a binary-search restriction measured flat on
`chip_stack_bottomup` because windows span the grid; here too.  A real win
needs an incremental evaluation (the cost of band b+1 shares all but one
band's usage with b) and is NOT decision-trivial — defer until A–F land and
re-profile.

**H. Housekeeping:** keep the `[PlanProf]` line; honour or drop
`max_iterations` (the `5` in `run_planner hier 5` is read as a budget and
is not one); `apply_reservation` by `cut_index_` range (item 2) when
convenient.

## What "done" would look like, measured here

* NQ=62 reticle dial: the issue's unfinished (> 25 min) planner runs in
  84.5 s with A + one scoring thread, no code beyond A.  The whole
  `-abstract` run then takes 13.7 min on this box, so the stages AFTER the
  planner, which the issue's run never reached (`run_nuts`, `check_design
  nuts` on 5,804 bundles / 22 M bands), are the next thing to profile.
  The recorded trace replayed through `bin/buda --threads 1
  --report-json` (so NUTS's per-layer pool is ONE thread here too; the
  planner's 111 s against the Tcl run's 84.5 s is the CLI's flow-log
  capture of 6,105 `[Planner]` lines):

  | command | seconds |
  |---|---|
  | `auto_floorplan` | 19.4 |
  | `run_hier_bundler depth 5` | 5.4 |
  | `generate_hier_topologies` | 42.4 |
  | `run_planner hier 5` | 111.0 |
  | `run_nuts` | **513.9** |
  | `check_design nuts` | 32.2 |
  | `report_wirelength` | 9.4 |
  | whole run | 738.3 |

  So once the planner is fixed, abstract NUTS is the reticle dial's
  bottleneck (1,036 overlaps / 28 interval violations at the audit), and it
  deserves the same per-phase timer treatment before anything is
  concluded about it — this run also serialised its per-layer pool.
* NQ=8 / NQ=16 re-measured: 6.2 s / 9.3 s against 47.2 s / 247.6 s.
* QoR corpus `--vs main` 0 better / 0 worse, abstract and detailed WL +0,
  for A (and C when built); B is covered by the existing thread-count
  identity guarantee.

## Not verified in this session

* The QoR corpus sweep (`tools/qor_corpus.py --vs main`) was not run: on
  4 CPUs it is an hour-plus of both sides, and the question here was the
  profile.  Planner test files pass on the prototype
  (`test_global_congestion.py`, `test_hier_planner.py`,
  `test_planner_band_span_charge.py`, `test_bdb_planner_persist.py`:
  21 passed).
* The side observation (53,568 nets in no bundle; no template expansion)
  was not investigated; it changes the bundle count, not the per-bundle
  cost.

## Reproduce

```bash
apt-get install tcl; pip install pybind11 matplotlib pytest pytest-bdd
bin/bb                                   # or cmake -B build-prof -DBUDA_PROFILE=ON for symbols
# record a dial once, then replay it under any thread count:
BUDA_RECORD=nq8.buda BUDA_REPLAN_PROF=1 bin/btcl flow/tcl/bigsoc.tcl 8 -NC 4 -N 8 -NL3 4 -NMC 4 -NIO 16 -abstract -noheal
tclsh flow/tcl/bigsoc.tcl 8 -NC 4 -N 8 -NL3 4 -NMC 4 -NIO 16 -emit bigsoc8.v   # the trace's import_verilog points at a temp file
sed -i 's#^import_verilog .*#import_verilog bigsoc8.v#' nq8.buda
BUDA_REPLAN_PROF=1 bin/buda nq8.buda --no-viz --threads 4 --report-json r4.json   # [PlanProf] lands in log/nq8_flow.log
BUDA_REPLAN_PROF=1 bin/buda nq8.buda --no-viz --threads 1 --report-json r1.json
BUDA_LAYERLOAD_SCAN=1 bin/buda nq8.buda --no-viz --threads 1                      # the shipped layer_load scan, same binary
```
