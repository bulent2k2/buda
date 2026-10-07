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

After those two, the planner is dominated by the per-candidate scoring
proper (single-thread profile below), and the next quadratic term waiting
at the reticle dial is the rip-up VICTIM RANKING (item 3 of the issue):
3.3 s at NQ=32 for 160 STRICT failures × ~1,000 committed bundles.

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

**B. Fix the scoring workers (the big one).**  Three shapes, cheapest first:
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

**C. Halve the refine pass.**  Each revisit runs TWO full `plan_bundle`
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

**E. Rip-up ranking from a band → occupant index.**  `charge_log_` already
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
