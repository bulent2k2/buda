# Parallel candidate scoring in `plan_bundle` (chip-flow P1)

**Status: LANDED** (2026-08-04).  `run_planner`'s candidate scoring runs on a
worker-thread pool (default: hardware concurrency, capped at 8), with results
**decision-identical** to the sequential sweep — verified byte-identical
against pre-change `main` on selections, per-segment layers, perp bands, and
exact NUTS track positions, plus a full-corpus `qor_corpus --compare`.

## Why the planner, and why this loop

`chip_topdown` (the longest corpus flow) profiles as: `run_planner hier`
**91–105s (67%)**, `run_detailed_nuts` 26s (19%), `generate_hier_topologies`
11s (8%).  Within the planner, ~all samples land in `plan_bundle`'s candidate
scoring (`score_segment` → `cong_cost_segment` → `for_each_band_w` →
`usable_band_cap` → `count_signal_tracks_in`).

The greedy **commit order is inherently sequential** (each bundle sees prior
commits' congestion), but the **per-candidate evaluation inside one
`plan_bundle` call is not**: the exact-value rollback between candidates means
every candidate is scored from the *same base cut state*.  That makes each
evaluation a pure function of (base state, bundle, candidate, params) — the
textbook parallel map.

## Design

1. **Candidate scoring overlay** (replaces the `cand_undo_` log).  During
   scoring, `apply_segment` writes a candidate's own charges into a per-thread
   overlay instead of `cuts_`; the five band-usage read sites route through
   `cand_usage_(ci, b)` (overlay hit, else base).  `cuts_` is **read-only for
   the whole scoring pass**, which is the race-freedom argument.  Bit-identity:
   the overlay's first touch of a band seeds the committed value and later
   charges `+=` — the same arithmetic the in-place charge performed, so every
   read returns the identical double.

   The overlay is an **epoch-stamped flat array** over all bands (flattened by
   `band_base_`): a read is one branch + one array access, per-candidate
   rollback is `++epoch` (O(1)).  A hash map was measured ~15% slower serially.
   Each thread's overlay is `thread_local` and **persistent across
   `plan_bundle` calls** (a per-call zero-fill of the multi-MB array measurably
   regressed chip-scale planning); a `cuts_gen_` generation stamp (bumped by
   `rebuild_cuts_`) re-zeros it whenever it last served a different grid, and
   planner *clones* (trial sweeps) share it safely — their `band_base_` is
   identical and values are seeded per touch from the current planner's `cuts_`.

2. **`score_candidate_`** — the historical loop body, verbatim, packaged to a
   `CandScore` (score, overflow, seg_layers/perp, debug seg costs, contended
   bands, infeasible/share-refused flags).  Threads claim candidates via an
   atomic counter; each candidate is evaluated by exactly one thread (so the
   per-`Topology` analysis cache is never touched concurrently — every other
   structure read during scoring is built eagerly in `rebuild_cuts_`:
   `cut_index_`, `blocks_cache_`, `sig_ntrk`).

3. **Ordered reduction** — walks `cand_indices` in order replaying the exact
   sequential compare (`< best-1e-6`, near-tie → lower index) plus the
   debug-view recording and the infeasible/collective-budget skips.  Winner
   and every tie-break identical to the serial sweep *by construction*; no
   FP-reassociation anywhere (per-candidate arithmetic is untouched, and the
   reduction compares the same doubles in the same order).

4. **Thread policy** — mirrors NUTS `layer_threads` (rnr P3):
   `set_plan_threads(n)` > `BUDA_PLAN_THREADS` env > hardware concurrency,
   clamped to `[1, min(ncand, 8)]`; pools `< 8` candidates stay sequential
   (spawn cost, perf-only gate).  **No nested pools**: `trial_sweep`'s worker
   clones pin `set_plan_threads(1)` (the move fan-out owns the cores), and
   `qor_corpus`'s sweep workers `setdefault(BUDA_PLAN_THREADS, "1")`
   (flow-level parallelism already saturates); a `-j 1` sweep keeps planner
   parallelism and gets the single-flow speedup.

## Measured (4-core container, chip_topdown)

At landing (vs pre-change main, both byte-identical):

| | main | branch `BUDA_PLAN_THREADS=1` | branch auto (4T) |
|---|--:|--:|--:|
| `run_planner hier 5` | 103.3s | 105.7s (+2.3%, noise-band) | **61.6s (1.68×)** |
| flow total | 148.7s | 150.7s | **105.7s (−29%)** |
| ov / unpl / WLs / all hashes | — | identical | identical |

Thread-count sweep (post-landing main, one batch — the flow had meanwhile
improved on main, so absolute numbers differ from the table above; every
row's selection/layer/perp/track hashes identical):

| `BUDA_PLAN_THREADS` | planner | speedup | flow total |
|--:|--:|--:|--:|
| 1 | 70.2s | 1.00× | 127.7s |
| 2 | 51.4s | 1.37× | 107.2s |
| 4 | 44.9s | **1.56×** | 99.9s |
| 8 | 45.7s | 1.54× | 102.0s |
| 16 | 48.8s | 1.44× | 108.5s |

The host has 4 physical cores (no SMT), so 8/16 measure OVERSUBSCRIPTION,
not scaling: 8 threads is flat vs 4 (no cliff — the auto cap of 8 is safe on
narrower hosts than it assumes) and 16 mildly degrades (~8% — scheduling
overhead plus a per-thread overlay footprint), which is why AUTO stays
capped while an explicit request is honored as given (the NUTS
layer_threads convention) for experiments on wider hosts.  True 8/16-CORE
scaling needs a wider machine; the 1→2→4 curve is sublinear because the hot
leaves (`usable_band_cap` / `count_signal_tracks_in`) are memory-bandwidth-
bound and the main thread waits on stragglers at the join.

## What was deliberately NOT parallelized

- The **commit order** (greedy schedule) — semantic.
- `commit_plan` / reservations / `rebuild_cuts_` — cheap, serial.
- Cross-`plan_bundle` pipelining — would reorder commits.

## Verification

- `test_planner_parallel_scoring.py`: decision-identity at 1/2/4/8 threads on
  a 36-candidate pool (above the gate), env re-read per run.
- Fast tier green with threads active (placement goldens lock exact coords).
- Full-corpus `qor_corpus --compare` main-vs-branch: 0 better / 0 worse, WL
  totals identical (see the PR).
