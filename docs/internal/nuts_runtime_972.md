# `run_nuts` on bigsoc: profile and options (#972, part 2)

Measured 2026-10-11 on the same 4-CPU container as the planner profile
([planner_runtime_972.md](planner_runtime_972.md)), on `main` after #973,
with the recorded `flow/tcl/bigsoc.tcl` traces (`-NC 4 -N 8 -NL3 4 -NMC 4
-NIO 16 -abstract -noheal`) replayed through `bin/buda --threads 1`.  The
planner profile ended with `run_nuts` at 514 s on the NQ=62 reticle dial
once the planner was down to 85 s; this is that stage.

## Headline

`run_nuts` the COMMAND is three things, and the C++ solve is the smallest
of them until the reticle dial:

| dial | segments | `run_nuts` | first engine solve | dead-span escalation (re-solves) | BDB persist | rest |
|---|---|---|---|---|---|---|
| NQ=8 (4 thr)  | 1,251 | 2.3 s  | 0.21 s | 1.10 s (3) | 0.88 s | 0.1 s |
| NQ=16 (1 thr) | 2,240 | 8.8 s  | 1.3 s  | 3.6 s (2)  | 3.7 s  | 0.2 s |
| NQ=32 (1 thr) | 4,594 | 77.1 s | 30.7 s | 19.1 s (2) | 20.9 s | 6.4 s (the selective planner re-persist after the escalation) |
| NQ=62 (1 thr) | 7,637 | 400 s  | 79.8 s | 251 s (3)  | 66.7 s | 2.3 s |

1. **The dead-span escalation re-solves the WHOLE design once per
   iteration** (`_escalate_dead_low_segments`, up to 5 iterations: move
   every dead LOW segment to a TOP layer, re-run NUTS, look again).  At
   NQ=62 that is three more full solves, 251 s of the 400, for 354 moved
   segments, and each re-solve ends with MORE overlaps than the solve
   before it (650 → 1027 → 1035 → 1036): the escalation trades guaranteed
   DNUTS opens for overlaps by design, which the healers are meant to
   repair, and `-abstract` has none.  The per-iteration counts (new on the
   `[NutsProf]` line) say where the waste is: at NQ=62 the three
   iterations move 330, then 23, then 1 segment, and every one of them
   pays a full ~65 s solve; at NQ=32 it is 1,308 then 14.
2. **The persist is quadratic in Python.**  `_persist_bundle_vias(w)`
   rebuilt its per-bundle segment map by scanning EVERY placed segment of
   the result, once per bundle, through pybind accessors — O(bundles ×
   segments) object conversions.  Fixed below: 3.7 → 0.24 s at NQ=16.
3. **Inside one engine solve the cost is the overlap repair, not the
   sweep.**  Single-thread sample at NQ=32: `resolve_corner_overlaps`
   30.3 s of the first solve's 37.7 s, whose iterations re-solve whole
   dirty layers and re-run `repair_overlaps`' cluster repack; the
   placement sweep itself (`solve_layer`) is 9 %.  The hottest self frame
   of the whole stage was the `std::set<const TrackSegment*>` lookup in
   `repack_members`' occupancy build (13 %), beside a per-attempt rebuild
   of the solver's keepout list (one zone per leaf, a `std::set` each)
   for every cluster-repack attempt.

## Method

* `[NutsProf]` (new, `BUDA_REPLAN_PROF=1`, the planner's gate): the
  `run_nuts` handler times its phases (engine run, dogleg adoption, the
  escalation with its re-solve count and per-iteration moves, the
  selective planner re-persist, the NUTS log, the persist) and prints the
  engine's own `pass_seconds` of the FIRST solve.  The engine stamped
  those on every result already; nothing on this path printed them.
* Sampling as in the planner profile: `py-spy record --native` on the
  `-DBUDA_PROFILE=ON` build, frames resolved against `nm`, the run
  single-threaded so the sample is the stage's whole wall.
* Decision identity: the per-layer NUTS log (`log/<trace>_nuts.log`,
  every overlap pair by bundle) diffed before/after, plus the
  `[NUTS] … Track overlaps … Interval violations` lines.

## Where the time goes (single-thread samples)

NQ=16 (10.8 s under the sampler), share of `run_nuts`'s samples:

| share | where |
|---|---|
| 49 % | `_escalate_dead_low_segments`: 36 % its two full re-solves, ~10 % the per-segment scan (`count_signal_tracks_in_span`, `w.input.candidates` copied per segment) |
| 42 % | `_persist_nuts`: 31 % in `_persist_bundle_vias`' per-bundle scan of every segment (pybind casts, `std::vector` copies), 4.6 % sqlite |
| 40 % | `NUTSEngine::run` (first solve + the re-solves): `repair_overlaps` 26 % (all of it `repack_overlap_clusters` → `repack_members` 20 %), `resolve_corner_overlaps` 17 %, `solve_layer` 7 % |

NQ=32 (94 s under the sampler):

| share | where |
|---|---|
| 61 % | `NUTSEngine::run`: `repair_overlaps` 48 % (`repack_members` 34 %), `resolve_corner_overlaps` 40 % (its inner repairs included), `solve_layer` 9 %, `keepout_occupied` 9 % |
| 26 % | the escalation (21 % its re-solves) |
| 25 % | `_persist_nuts` (24 % in `_persist_bundle_vias`) |
| 8.5 % | `_persist_planner_output(selective)` after the escalation (`_persist_topology_annotations` 6.5 %) |

First-solve pass seconds at NQ=32: corner 30.3, repair 5.3, fixpoint 1.3,
tighten 0.6 (of 37.7).  The LAST solve of the same run (after the dead
segments moved to TOP): corner 3.7, fixpoint 2.2, repair 1.4 — the first
solve pays for the dead LOW seats the escalation then removes.

## What was prototyped (branch `ccr-d45ce568-rrqgeo`), all decision-identical

* **Persist in one pass** (`persist.py`): the per-bundle segment map is
  built once for every bundle; `w.input.candidates` (a COPY of every
  candidate Topology per access) is read once per bundle.
* **Escalation scan memoized** (`nutsflow.py`): the pool size, the
  selected candidate's member bits and the admission needs are pure per
  (bundle, segment, layer) inside one call and were re-read — with the
  candidate-pool copy — per segment per iteration.
* **Solver** (`nuts.cpp`): the cluster repack's per-attempt `LayerSolver`
  takes the keepout list from `repair_overlaps` instead of rebuilding it
  (`low_layer_keepouts` per attempt), walks only the zones that can apply
  to its layer (same zones, same order), and the two member-set lookups
  are sorted vectors with binary search instead of `std::set`.

| dial | `run_nuts` before → after | first solve | escalation | persist | NUTS log |
|---|---|---|---|---|---|
| NQ=16 | 8.8 → 4.0 s | 1.3 → 0.89 s | 3.6 → 2.65 s | 3.7 → 0.24 s | identical |
| NQ=32 | 77.1 → 48.8 s | 30.7 → 25.5 s (corner 20.2) | 19.1 → 16.3 s (re-solves 12.7; iterations moved 1,308 then 14) | 20.9 → 0.48 s | identical |
| NQ=62 | 400 → 292 s | 79.8 → 69.0 s (repair 29.3, corner 25.4, fixpoint 12.0) | 251 → 220 s (three re-solves, 201 s, for 330, 23 and 1 moved segments) | 66.7 → 0.9 s | engine lines identical (full log: see below) |

## Options, ranked

**A. Land the three prototypes** (above): persist O(segments), escalation
scan O(segments), solver attempt setup O(layer zones).  Byte-identical by
construction and measured so on the NUTS logs.

**B. Stop re-solving the whole design per escalation iteration.**  The
moved set strictly grows and iteration k only needs a placement to judge
the NEXT dead set.  Shapes, cheapest first: (B1) do not re-solve for a
handful of late moves — at NQ=62 iterations 2 and 3 move 23 and 1
segment and cost 130 s of the 292, at NQ=32 iteration 2 moves 14 for
6 s; a floor on the moved count (or on its share of the previous
iteration's) that ends the loop with the moves applied to `seg_layers`
and ONE final re-solve; (B2)
re-solve only the layers a moved segment left or joined (`rerun_layer`
exists), accepting that spans propagate; (B3) a cheaper solve (C).  B2/B3
change decisions and need the corpus; B1 is a policy question for the
flow (`-abstract` pays three solves to measure a dial, a healing flow
pays them before healers that re-solve anyway).

**C. The corner pass and the cluster repack are the engine.**  Each
corner iteration re-solves whole dirty layers from scratch (O(n²) per
layer) and runs a full `repair_overlaps`; each cluster-repack attempt
takes a snapshot of EVERY segment, builds each member's occupancy by
scanning every segment on the layer, and restores on rejection.  Levers:
(C1) a per-layer interval index for the occupancy build (hits re-emitted
in the layer's own order so `preferred_fit`'s tie-breaks are untouched);
(C2) snapshots scoped to the layer plus the members' junction partners;
(C3) count attempts vs accepts per solve (324 accepted at NQ=62; the
attempts are not logged) before deciding whether a cheaper attempt or
fewer attempts is the lever.  Byte-identity is by construction for C1/C2
and must be measured on the corpus.

**D. The selective planner re-persist after the escalation** (6–8 s at
NQ=32): `_persist_topology_annotations` rewrites every candidate's
busterm and junction links for each moved bundle; the escalation changes
only `seg_layers`, so a layer-only re-persist is the shape.

**E. Four threads.**  The per-layer NUTS pool parallelises the group
solves (Amdahl-bound by the heaviest layer) but not the corner pass's
dirty-layer re-solves nor the cluster repacks nor any of the Python; the
single-thread numbers here are what `-j 1` pays, and `-j 4` changes only
the `fixpoint` bucket.

## Not verified

* The QoR corpus (`tools/qor_corpus.py --vs main`) was not run on the
  prototypes; the NUTS-log diff at NQ=16/32/62 and the fast tier are what
  this session checked.
