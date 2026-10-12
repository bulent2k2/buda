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
  `run_nuts` handler times its phases (the setup before the solve — engine
  construction, fan-in derivation, bottom-up injection, the span snapshot;
  0.1 s at NQ=16 — then the engine run, dogleg adoption, the escalation
  with its re-solve count and per-iteration moves, the selective planner
  re-persist, the NUTS log, the persist) and prints the
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
  candidate Topology per access) is read once per bundle.  The same
  per-bundle copy was found and fixed in parallel by #976 (the planner
  write-up's "Abstract NUTS: the time is persistence" section, with its
  own measurement: NQ=16 16.4 → 11.0 s, NQ=32 95.0 → 68.7 s in-process),
  which landed on `main` while this branch was open; the merged code is
  #976's map (one read of `nuts_result.segments`, grouped by bundle) plus
  this branch's single read of the candidate pool.  The table below was
  measured with this branch's form of the map, which does the same work.
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
| NQ=62 | 400 → 292 s | 79.8 → 69.0 s (repair 29.3, corner 25.4, fixpoint 12.0) | 251 → 220 s (three re-solves, 201 s, for 330, 23 and 1 moved segments) | 66.7 → 0.9 s | identical (7,297 lines) |

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

## Landed: B1 as a knob, and what it measures (2026-10-11)

`set_dead_span_escalate floor <F>` (`BUDA_DEAD_SPAN_FLOOR` for a sweep;
default 0, byte-identical): a LATE escalation iteration (the second on)
that finds fewer than `ceil(F × segments)` dead LOW segments is NOT applied — the loop ends on
the placement it already has, the segments are reported and left on their
layers, and that iteration's full re-solve is not paid.  Deferred
application (the scan collects its moves and writes `seg_layers` only once
the iteration clears the floor) is what keeps a dropped iteration's
`seg_layers` and `nuts_result` agreeing.  The run_nuts entry only — the
explicit flag and the `healersAhead` auto path are the same loop — never
the healers' own folds.

Measured on the four dials at `F = 0.01`, one thread, the same build and
session as the pair beside it (`[NutsProf]`; the audit line is
`check_design nuts`, where a dead LOW segment the floor left shows up as a
supply-doomed seat):

| dial | `run_nuts` off → floor | iterations (moved) → (moved, dropped) | doomed seats off → floor | audit violations | abstract WL |
|---|---|---|---|---|---|
| NQ=8  | 1.75 → 1.20 s (−31 %) | [41, 2, 1] → [41] + [2] | 2 → 4 (64 → 112 bits) | 28 → 31 | +0.08 % |
| NQ=16 | 5.26 → 3.98 s (−24 %) | [78, 5] → [78] + [5] | 3 → 8 (96 → 240) | 55 → 60 | +0.05 % |
| NQ=32 | 64.6 → 55.3 s (−14 %) | [1308, 14] → [1308] + [14] | 265 → 279 (8,480 → 8,904) | 84 → 99 | +1.8 % |
| NQ=62 | 366.6 → 188.1 s (−49 %) | [330, 23, 1] → [330] + [23] | 28 → 51 (896 → 1,392) | 119 → 142 | −0.5 % |

(This box ran ~7 % slower than the part-2 table's: NQ=62 off is 366.6 s
here against 343 s there on the same code; the pairs are what count.)

What it buys is exactly what the estimate said — the late re-solves: one
at NQ=8/16/32, two at NQ=62 (178 s of 367).  What it costs is exactly the
segments dropped, and the audit prices them: every one is a seat with
fewer clear tracks than its bits, so a flow that goes on to DetailedNUTS
without healers strands those bits (NQ=62: 23 seats, 496 bits, 0.3 % of
the segments).  The NQ=32 row also shows the final placement is a
DIFFERENT one (iteration 1's re-solve rather than iteration 2's): +1.8 %
abstract WL and 15 more audit violations for 14 dropped segments.

**Two things the measurement corrects in the estimate.**  First, a flow
that heals does not get the saving for free: the stage-b fold
(`_heal_dead_spans`) escalates the dropped segments with the unfloored
loop, so it pays a NUTS re-solve plus a DNUTS there — the floor DEFERS
that re-solve to the healers rather than removing it, and only rides for
free when the fold had other dead spans to escalate in the same pass.
The floor's honest market is the healerless flow — the `-abstract -noheal`
dial measurement, a healerless flow to DNUTS that accepts the strands —
and the corpus run below says what it does to the healed endpoints.
Second, the saving is bounded by the solve count: at NQ=62 four solves
are 336 of the 367 s and the floor removes two; the solve itself (repair
38.9 s, corner 29.0 s, fixpoint 15.5 s of the 86 s first solve) is where
the remaining time is, which is option C.

**The corpus, twice.**  Swept with `BUDA_DEAD_SPAN_FLOOR=0.01` against
the same build with the variable unset (`qor_corpus.py --out` both sides,
`--compare`; 57 flows, `ariane133_heal` NOT COMPARABLE for its fetched
input).  The FIRST form of the knob floored every iteration and read
**0 better / 1 worse / 55 unchanged**: `rnr/mix2` went from clean to
4 overlaps / 32 unplaced / 2 violating bundles in a third of the time.
mix2 has 238 segments, so the floor was 3, and its FIRST iteration found
one dead segment — dropped, the whole escalation skipped, and the stage-a
healers climbed from a different placement (overlaps 7 → 2 → 1 became
5 → 2 → 0, DNUTS then 235 → 111 unplaced) into a basin the stage-b fold
and ripup ended at 32 (ovl 4).  That is the measured reason the entry
exists — escalating BEFORE the stage-a healers beats the stage-b fold
alone — undone by the floor on a design small enough that one move is the
whole escalation.  The dial vehicle could not show it: its first
iteration is 41–1,308 moves, far above the floor at every size.  So the
knob prices only the LATE iterations (the second on; the cascades a
re-solve creates, which is what the estimate was about), and the dials'
numbers above are unchanged by that (NQ=16 re-measured byte-identical).
The SECOND run, under that rule, is **identical on every metric across
all 57 flows, abstract and detailed WL +0** — no corpus flow has a late
iteration under the floor: the corpus designs run zero or one entry
iteration (`bigHalf` 253 segments and `mix2_fast_topdown` 262: zero,
their dead spans arise at stage b; `mix2`: one), and the cascades the
floor is for appear on the dials from NQ=8 up.  Harmless where it does
not fire, honest where it does, and still a QoR trade, so it ships
opt-in; `-abstract -noheal` dial sweeps and a healerless flow that
accepts the audit's price are its market.

**B2 probed and not built.**  The per-layer re-solve the engine already
has (`rerun_layer`, the `run_nuts_on_layer` command) was timed on the
NQ=32 result as the shape B2 would use for a late iteration — the two TOP
layers the moved segments join: M6 3.1 s + M7 2.2 s against 7.1 s for the
full re-solve it would replace (the escalation's re-solves are cheaper
than the 31 s first solve, whose corner pass runs on the pre-escalation
layer assignment).  A quarter saved per late iteration, and the per-layer
solve is a different placement — on an already-solved design it moved 169
of M6's 923 segments and the audit read 90 violations / 270 doomed seats
against 84 / 265 — so it would trade the floor's stated price for an
unstated one.  Left as measured.

## Landed: C3 (attempt accounting) and C1 (span index for the repack) (2026-10-12)

**C3 — count attempts before choosing a lever.**  `buda.repack_stats()`
(process-wide atomics, observation only; `BUDA_REPLAN_PROF=1` prints a
`[RepackProf]` line beside `[NutsProf]`, covering every solve of the
command, re-solves included) counts `repack_overlap_clusters`' attempts and
times its phases.  One thread, the three dials:

| dial | repack of `run_nuts` | narrow attempts (noop / rejected / accepted) | wide attempts (rejected / accepted) | members per wide attempt | time in the pack |
|---|---|---|---|---|---|
| NQ=16 | 1.96 of 5.8 s | 675 (399 / 185 / 91) | 584 (574 / 10) | 123 | 1.22 s wide, 0.05 narrow |
| NQ=32 | 34.1 of 51.7 s | 4,144 (3,391 / 607 / 146) | 3,998 (3,950 / 48) | 187 | 26.8 s wide, 0.4 narrow |
| NQ=62 | 238.9 of 336.3 s | 4,778 (1,710 / 2,853 / 215) | 4,563 (4,454 / 109) | 449 | ~215 s wide |

The repack is **the** cost of `run_nuts` at scale (71 % at NQ=62), the
snapshot, restore, settle and guard terms are small (snapshot 0.4 s,
restore 0.2 s, guard 15 s at NQ=62), solver construction is 2 s, and the time
is the WIDE attempt's pack: 97–99 % of wide attempts are rejected by the
guard, each carrying the cluster plus every same-layer neighbour in its
window (123–449 members).  Narrow attempts are cheap and mostly no-ops (they
fail the `members < 2` test once constrained trunks are dropped).  So the
lever is the wide attempt's per-member cost (C1), or a prediction that a
wide attempt will be rejected (which changes decisions and needs the
corpus — not built).

**C1 — an interval index for the occupancy build.**  Each member's base
occupancy was found by testing every segment on the layer (`occupancy_from`:
placed, other bundle, closed-span overlap), so a wide attempt cost
members × layer (449 × ~2,200 at NQ=62, 4.5G tests in all).  The tests only
need the segments whose span touches the member's, a handful.  With eight
or more members, `repack_members` now builds a `SpanIndex` — a balanced
implicit tree over the layer's placed segments sorted by span start, each
node holding the maximum span end below it — once per call, and asks it
for the touching segments in ascending layer order, then applies the same
`occupancy_from` to them.  A repack's non-members do not move during the
pack, so one build is exact; fewer than eight members keep the scan (the
build would not repay).  `BUDA_NO_SPAN_INDEX=1` keeps the scan everywhere,
so a before/after is one binary.

| dial | `run_nuts` scan → index | repack | wide pack | NUTS log |
|---|---|---|---|---|
| NQ=16 | 5.07 → 4.57 s (−10 %) | 1.74 → 1.28 s | 1.22 → 0.75 s | identical |
| NQ=32 | 50.6 → 36.8 s (−27 %) | 33.6 → 19.8 s | 26.8 → 13.0 s | identical |
| NQ=62 | 336.3 → 228.0 s (−32 %) | 238.9 → 136.0 s | ~215 → 108.1 s | identical (vs the unmodified build) |

`test_nuts_span_index.py` compares the index with `occupancy_from`'s own
predicate over 300 randomized layers (touching and identical ends, nested
spans, reversed ends, unplaced segments, a NaN end), and the QoR corpus
swept on one binary with the scan (`BUDA_NO_SPAN_INDEX=1`) and with the
index reads **identical on every metric across the 56 comparable flows**
(`ariane133_heal` not comparable: fetched input), abstract and detailed WL
+0.  Its runtime moved −0.9 % (1306 → 1294 s): the corpus designs seldom
reach a repack of eight members, so the index barely engages there, and the
gain is the large-design one in the table above.

**What is left.**  The wide pack is still 108 s at NQ=62 (~24 ms per
attempt, 449 members): what remains is per-member work that scales with the
occupancy list rather than with the layer — the copy of the base list, the
loop over already-repacked members (members²/2 span tests), the sort and
`preferred_fit` over a list of hundreds.  Two shapes, neither built: an
incremental occupancy list for the repacked members (keep it sorted, insert
instead of copy and re-sort), which is decision-identical; and skipping the
wide attempt where the narrow attempt's failure already proves it cannot
help, which is not and needs the corpus.

## Not verified

* The QoR corpus (`tools/qor_corpus.py --vs main`) was not run on the
  prototypes; the per-layer NUTS-log diff at NQ=16, 32 and 62 (the NQ=62
  pair measured on the pre-change and the prototype build in the same
  session, 343 s against 292 s) and the fast tier (3307 passed) are what
  this session checked.
