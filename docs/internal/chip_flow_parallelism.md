# chip-flow runtimes — where chip_stack_topdown's time goes & what can parallelize

**Vehicle:** `flow/chip/chip_stack_topdown.buda` (660 hbundles / 16 427
candidates / 560 planner-expanded instances, 11 750 nets), measured
2026-08-07 on Linux/glibc, 4 cores, `-O3 -march=native`, `BUDA_THREADS=4`
(`main` @ 96f6029).  The chip-scale sequel to
[`rnr_runtime_parallelism.md`](rnr_runtime_parallelism.md) — that arc
parallelized the healers (P1/P1b/P2/P3); this flow is healer-light
(one `negotiate_congestion`, no ripup) and the profile is completely
different: **the biggest single mechanism is BDB persistence, not any
solver.**

## 1. Per-command (this box; the user's -j 8 macOS numbers in parens)

| command | 4-core Linux | (user's box) |
|---|--:|--:|
| run_planner hier 5 signal_tracks | **45.9 s** | (53.4 s) |
| negotiate_congestion | **22.2 s** | (24.7 s) |
| run_nuts | 12.8 s | (21.5 s) |
| generate_hier_topologies | 10.7 s | (36.9 s) |
| run_detailed_nuts | 9.5 s | (13.4 s) |
| everything else | ~2 s | |
| **total** | **103 s** | |

## 2. Attribution (cProfile, 94.7 s profiled ≈ the 103 s wall)

The five stages decompose into FOUR mechanisms:

| mechanism | seconds | share |
|---|--:|--:|
| **BDB persistence** (SQLite + per-topology annotation writes) | **~37 s** | **~38 %** |
| `CongestionPlanner::optimize_topologies` (C++) | 34.4 s | ~35 % |
| abstract-NUTS re-solves (~1.1 s × ~12 across the flow) | ~13 s | ~13 % |
| candidate generation (`generate_candidates`, 660 × 10 ms) | 6.6 s | ~7 % |

Per stage:

| stage | total | breakdown |
|---|--:|---|
| run_planner hier | 46.9 | `optimize_topologies` 34.4 + persist 8.5 + `build_congestion_map` 3.4 |
| negotiate | 22.9 | **persist (checkpoint) 11.4** + `replan_bundle_ripup` 5.6 s/94 + NUTS solves 4.3 + DNUTS 0.5 |
| run_nuts | 13.7 | **persist 10.6** (≈ 9 re-persist of planner output after dead-span escalation + 1.7 bus rows) + escalation 1.4 + solve ~1.1 |
| run_detailed_nuts | 10.7 | cull heal 8.3 (≈ 7 escalation rounds × 1.1 s **abstract** re-solves) + persist 1.8 + DNUTS solve ~0.5 |
| generate_hier_topologies | 11.7 | `generate_candidates` 6.6 + `_persist_topologies` 4.6 |

### 2a. The persistence hot spot, precisely

`_persist_planner_output` runs **three times** (after `run_planner`; again
at `run_nuts` because dead-span escalation mutated `seg_layers`; again in
negotiate's `_checkpoint_routing`), and each run **clears and rewrites all
560 expanded-instance bundles from scratch** (`clear_expanded_bundles()`
+ `_add_expanded_bundle` per instance).  Inside that,
`buda.persist_seg_busterms` on the expanded instances costs **25.8 s —
15 ms per topology × 560 instances × 3 rewrites** — while the SAME
function on the 16 427 generation-time candidates costs 1.6 s total
(0.1 ms each).  The generation path passes the `seen` busterm-dedup set
and has warm analysis caches; the planner path passes `seen=None`
(a "persists few candidates" assumption that chip scale broke) and its
expanded instances are freshly translated copies, so each persist pays
the wide JSON busterm-row INSERT per endpoint and/or the cold
topology-analysis derivation.

Raw SQLite inserts are NOT the problem: 90 668 `add_topology_segment`
calls cost 1.0 s (11 µs each), and the batched `commit_batch` total is
0.4 s.

### 2b. The abstract-NUTS solve at chip scale

One solve ≈ 1.1 s, and its pass profile (negotiate's 4 solves):
`tighten 2.71  fixpoint 0.70  corner 0.38  repair 0.14` — at THIS scale
the dominant pass is **`tighten_pulls`** (the WL slide polish,
~0.7 s/solve), not the corner/repair cluster that dominated the
over-congested synthetic (rnr doc §"corner/repair").  P3's per-layer
threading covers only the fixpoint bucket (~0.18 s/solve).  The flow
runs ~12 full solves: 1 `run_nuts` + ~7 dead-span/cull-heal escalation
rounds + 4 negotiate iterations — the escalation loops re-run the FULL
solve per round to re-check a handful of segments.

### 2c. The planner's parallel fraction (measured)

`run_planner hier 5 signal_tracks`: **67.0 s @ BUDA_PLAN_THREADS=1 →
45.8 s @ 4** (1.46×).  By Amdahl, ~28 s of the 1-thread time is the
already-parallel candidate scoring and **~39 s is sequential residual**:
persistence ~9, `build_congestion_map` 3.4, and ~26 s of sequential
planner core — the greedy commit chain, the STRICT-ladder rip-up
replans, per-bundle apply/recharge, demand reservations, and the hier
default `refine_passes 1` (all order-dependent by design; the scoring
inside each is what parallelizes).

## 3. Ranked opportunities

### C1. Kill the redundant expanded-instance persistence — ~20–25 s, no parallelism needed
Three independent levers, composable:
- **Dirty-tracking**: re-persists 2 and 3 rewrite 560 instances when a
  handful changed (run_nuts's escalation touched ~6 segments; negotiate
  accepted 3 iterations).  Re-persist only wrappers whose
  selection/seg_layers changed since the last persist — the same
  `_rr_dirty` discipline ripup already keeps.  Removes ~2 of the 3
  passes (~17 s) outright.
- **Busterm-row dedup in the planner path**: thread the existing
  `seen_busterms` set through `_persist_planner_output` →
  `_add_expanded_bundle` (measured below).
- **Root-cause split**: instrument `persist_seg_busterms` to separate
  cold-analysis time from the wide-INSERT time; whichever dominates,
  the fix is mechanical (translate/carry the template's cached analysis
  to expanded copies, or write busterm rows through a prepared batch).

Measured prototype (busterm dedup alone, this branch):
`run_planner hier` 45.8 → **39.7 s** (−6.1 s on the FIRST of the three
persist passes — confirming the wide INSERT, not the analysis, is the
bulk of the 15 ms; dirty-tracking on passes 2/3 stacks on top).

### P5. Parallel candidate generation — up to ~5 s here, ~27 s on the user's box
`generate_candidates` is 660 independent 10 ms calls (per-bundle
floorplans; cases (a)/(b) share cached read-only floorplans).  The
`parallel_sweep` pattern applies directly: a C++ batch entry point
taking [(floorplan, src, dsts, knobs)], fanning bundles across the pool
with the GIL released, returning candidate pools in input order
(deterministic — generation is per-bundle pure).  Cautions: `Floorplan`
lazily caches its Hanan grid (pre-warm per floorplan before the fan-out
or give workers private copies); per-bundle stdout must be buffered
per-task and replayed in order (the P1b print-transparency discipline).
Generation-time persistence (4.6 s) stays serial but can overlap the
NEXT stage (see P9).  Note the stake is machine-dependent: this stage
is 36.9 s on the user's box vs 10.7 here — the fan-out matters most
exactly where the user runs.

### P6. Planner: scale what's parallel, then attack the residual
The scoring already scales (67→45.8 s at 4 threads; the user's 8-core
box should push further — `BUDA_PLAN_THREADS=8`).  The ~26 s sequential
core is order-dependent by design (greedy commit + ladder), but two
pieces are not:
- `build_congestion_map` (3.4 s): a pure map over cuts — a trivial
  parallel-for.
- The STRICT-ladder rip-up replans and `refine_passes` re-plans are
  sequential accept chains, but each step's candidate scoring is
  already the parallel part; no further structural win without
  changing the algorithm.  NOT recommended.

### P7. DNUTS per-layer threads (the P3 twin) — small here, bigger at scale
`place_by_layer` solves each layer independently (own grid, own
assignment list; cross-layer coupling only in the later
`bit_spans`/`vias` passes).  A per-layer fan-out mirroring P3
(size-gated, results merged in layer order — deterministic by
construction) parallelizes the `place` bucket.  At this flow's scale
the DNUTS solve is only ~0.1 s/call, so this is a low-priority
follow-up measured on bigger detailed designs; it also composes with
the healers (each `parallel_sweep` worker already owns its core, so
workers keep layer-threads=1 exactly like P3).

### P8. Stop paying the full NUTS solve per escalation round — ~6–8 s
The dead-span/cull-heal escalation loops re-run the ENTIRE abstract
solve (1.1 s, tighten-dominated) once per round (~8 rounds across
run_nuts + run_detailed_nuts + negotiate's dead-span fold) to re-check
the few segments they escalated.  Two composable fixes:
- Batch: escalate ALL dead segments found in a round already happens;
  the loop mostly converges in 1–2 rounds per call site — the cost is
  the number of CALL SITES × full solve.  A scoped re-solve
  (`rerun_layer` on the touched layers only, like
  `run_nuts_on_layer`) turns each round into a fraction of a solve.
- `tighten_pulls` (0.7 s/solve — the dominant pass at this scale) is a
  WL polish irrelevant to the escalation's dead-span predicate; an
  escalation-internal re-solve could run tighten-skipped (the healers'
  fast-trial precedent) with one full solve at the end.

### P9. Overlap persistence with compute (parallelism in the large)
Even after C1, checkpoints serialize behind SQLite.  The BDB writes are
append/replace batches derived from snapshot-able state; a writer
thread (single SQLite owner, work handed over as row batches) would let
`run_nuts`/`negotiate` proceed while the previous stage's checkpoint
drains.  Effort: medium-high (the BDB connection must stay
single-threaded; the handoff must copy the row data); do it only if
C1's dirty-tracking leaves persistence visible in the profile.

### NOT parallelizable (by design)
- The planner's greedy widest-first commit chain and its ladder
  (order IS the cost model), negotiate's accept/restore iterations,
  and `replan_bundle_ripup`'s victim ladder (5.6 s here) — each
  replan must see the previous replans' recharges.
- The escalation loops' round structure (each round's dead-span set
  depends on the previous round's solve).

## Implemented — C1: selective expanded-instance persistence (2026-08-07)

Two composed levers, landed together:

- **Busterm-row dedup in the planner path** (the measured prototype):
  `_persist_planner_output` threads a per-pass `seen_busterms` set
  through `_add_expanded_bundle` → `_persist_topology_annotations`, the
  same contract the generation-time persist already used — the wide
  JSON busterm row writes once per geometry-fingerprinted id, link rows
  per candidate as before.  DB endstate identical by construction
  (rows are content-identical per id).
- **Dirty-tracking (`selective=True`)** at the RE-persist sites (the
  run_nuts escalation re-persist, `_checkpoint_routing`; the
  run_planner command itself stays a full rewrite — a fresh plan is a
  semantic reset): each expanded wrapper gets a cheap fingerprint
  (selected `topo_uid` via the zero-copy `selected_topo_key`, the
  assigned `seg_layers`, the lock flag, and the instance-local USER
  extra uids — every row-shaping input), memoized per persist
  (`_persisted_plan_fp`).  A selective pass rewrites ONLY changed
  bundles through the new per-bundle `BDB::clear_expanded_bundle(id)`
  (the parameterized twin of the bulk clear, same child-before-parent
  order); an id-set mismatch falls back to the full path.  The memo is
  invalidated everywhere expanded rows can change under it (`open_bdb`,
  `load_pipeline`, `_persist_bundles`' clear-and-rewrite).

Guards: `test_bdb_planner_persist.py` — a no-change selective persist
rewrites ZERO bundles and the serialized SQL dump is line-identical; a
changed-bundle selective persist matches a forced full rewrite of the
same state exactly; an id-set mismatch takes the full path.  Corpus:
0/0/41, WL ±0.  Fast 1805 + mid 598 green.

## Implemented — P5: parallel hier candidate generation (2026-08-07)

`buda.generate_candidates_batch(generators, srcs, dsts, n_threads)`
(bind_routing.cpp): fan a batch of PRE-CONFIGURED per-bundle
`TopologyGenerator`s (1:1 task:generator; shared floorplans are
read-only — audited: no mutable state, no const_cast, `get_hanan_grid`
builds fresh) across a thread pool with the GIL released.
Print-transparency machinery: `TopologyGenerator::set_note_stream`
redirects the 15 "[TopoGen]" note sites (all generator members) into a
per-task buffer, returned per task and written by the caller exactly
where the sequential loop printed them.

`cmd_generate_hier_topologies` splits the per-bundle work into
`_prep_hier_topo_gen` (the 3-case endpoint/floorplan/knob resolution —
skip warnings buffered and replayed in visit order) → the batch call →
`_install_hier_topo_result` (pool install + fan-in taper + the
HierTopo report line) sequentially in bundle order, with the knob-memo
replay sequential as before.  Gate: >1-thread pool
(`BUDA_TOPO_THREADS` explicit > `BUDA_THREADS` governor > hardware)
AND ≥8 bundles; the sequential loop is otherwise unchanged, and every
other generation caller (per-hbundle, additive, rotation-clone) stays
sequential through the same shared pieces.

Validated: the chip vehicle's generation flow-log is byte-identical
seq vs batch (1145 content lines, all [TopoGen] notes in position;
only timing digits differ); `test_hier_topo_batch.py` pins pool/print
identity on the hier_mixed fixture with the gate lowered.  Measured:
`generate_hier_topologies` **11.0 → 6.4 s** at 4 threads (the C++
generation 6.6 → ~1.7 s; the remainder is the still-serial
`_persist_topologies`).

### Composite (C1 + P5, same box, `BUDA_THREADS=4`)

`chip_stack_topdown` **103.1 → 70.7 s (−31 %)** with a byte-identical
endpoint: generate 10.7 → 6.2, planner 45.9 → 35.6, run_nuts 12.8 →
4.2, dnuts 9.5 → 9.6, negotiate 22.2 → 13.1.  Corpus guard: **0
better / 0 worse / 41 unchanged**, abstract + detailed WL exactly ±0;
every chip flow −9 to −28 s (corpus total −8.6 %).  What remains on
top: P8 (the dnuts cull-heal's full re-solves — now the biggest
non-planner residual), P6a, and the planner's sequential core.

## Refresh — where things stand (2026-08-08, main @ post-#621)

Re-measured after the C1+P5 landing, the risk-reduction arc (A–D, all
byte-identical-gated), and the NDR phase-1 + v21-persistence merges.

**Headline: the flow is FLAT since C1+P5 — and single runs on this box
lie.**  A first single run read 94.1 s (vs the documented 70.7) and a
first n=2 `runtime_ab` vs the #610 merge read +4.2 s "all in
negotiate"; a second n=2 A/B vs the #616 merge, twenty minutes later,
showed main at 72.2 s with negotiate back at 13.5 s.  Reconciliation:
this VM's run-to-run variance is ±2–4 s per stage (negotiate observed
13.5–17.6 s across same-binary runs), so:

- **No code-level regression** #610 → main within noise; the risk-arc
  and NDR merges are runtime-neutral on this vehicle (the planner core
  profiled 36.4 s vs 34.4 s — flat under profiler inflation).
- **Instrument rule**: n=2 itself PRODUCED the false +4.2 s (~6 %)
  delta above, so its trustworthy resolution on this flow is only
  ~≥10 %; use `-n 3`+ for anything smaller (and re-run before acting on
  any delta near the boundary), never a single run.

Current attribution (planner-stage profile + clean runs, ~72 s flow):

| block | now | note |
|---|--:|---|
| `optimize_topologies` | ~36 s (~50 %) | the dominant block; scoring already parallel — remaining levers are more cores (8-core: raise `BUDA_PLAN_THREADS`) or algorithmic |
| negotiate | ~13.5–17.6 s | replans (~5.6 s, sequential by design) + solves; the highest-variance stage |
| dnuts (incl. cull-heal re-solves) | ~10 s | P8's stake intact: ~5–8 s ≈ 7–11 % of the ~72 s flow |
| generation (parallel) + its persist | ~8 s | the C++ fan-out holds at ~1.8 s; `_persist_topologies` (~4.6 s wall) is now the biggest persistence item left → P9's case |
| `build_congestion_map` | 3.3 s | P6a unchanged |
| run_nuts | ~4–5 s | solve ~1 s + escalation + bus persist |

Ranked next steps, re-based: **P8** (scoped escalation re-solves) and
**P6a** (congestion-map parallel-for) survive as the decision-safe
short list (~11–15 % combined); **P9** (async persistence) now targets the
generation-time persist specifically; the planner core's ~50 % share is
the standing algorithmic frontier (its parallel fraction measured
1.46× at 4 threads — an 8-core box should push further before any
structural work).  The corpus rollups + `runtime_ab` (Phase A tooling)
are the standing instruments — both were load-bearing in reaching
today's verdict.

## The bottom-up twin — chip_stack_bottomup (2026-08-08)

Same die, same technology, bottom-up templates (`set_bottom_up big2/
mix2`) — and a COMPLETELY different profile.  Clean run at
`BUDA_THREADS=4`: **129.0 s**, of which:

| command | s | share |
|---|--:|--:|
| **negotiate_congestion** | **79.8** | **62 %** |
| run_planner hier 5 signal_tracks | 29.8 | 23 % |
| generate_hier_topologies | 7.8 | 6 % |
| run_detailed_nuts | 6.6 | 5 % |
| run_nuts | 2.3 | 2 % |

Negotiate's own timing line splits its 79.8 s as `replan 59.9s/4,
dnuts 15.8s/4, nuts 0.7s/4` — and the cProfile attribution names both
mechanisms precisely:

- **B1 — `replan_bundle_ripup` at 569 ms/call** (105 calls = 59.8 s,
  46 % of the flow) vs ~60 ms/call on the topdown twin (94 calls =
  5.6 s).  The 10× per-call cost is specific to the bottom-up state:
  most wrappers are `hier.locked`, so each affected free bundle's
  ripup-capable replan (recharge-all + plan-one + the victim ladder)
  grinds against band-holders it cannot move.  Hypotheses to test
  C++-side, in order: (a) the victim ladder re-scanning locked
  occupants per call — skip locked victims BEFORE ranking, or
  precompute the movable-occupant set once per negotiate iteration;
  (b) the demand-reservation re-park inside each recharge-all.  Either
  way this is negotiate's whole story on bottom-up designs, and it is
  sequential by design — the fix is per-call cost, not parallelism.
- **B2 — the DNUTS copy fan-out is quadratic Python** (20.8 s SELF
  time in `_run_detailed_nuts` over 5 calls ≈ 4.2 s/call, 16 % of the
  flow — while the DNUTS engine passes total ~0.2 s).  The copy loop
  iterates `for spec in copy_specs: for ns in r1.net_segments: if
  ns.bundle_id == ref_bid` — O(specs × all reference bit-wires) with a
  pybind attribute access per probe.  Fix shape: group
  `r1.net_segments`/`net_vias` by bundle_id in ONE pass, then fan out
  per spec from the group (O(N + copies)); or move the whole transform
  fan-out into C++ beside `transform_net_segment`.  Decision-safe (the
  copies are identical, only found faster); stake ~15 s here and on
  every bottom-up stage-b healer trial that re-runs DNUTS.

The planner's 29.8 s covers THREE `optimize_topologies` calls (23.4 s
total — the two cell-local template solves + the global pass), each
using the parallel scoring.  Generation/persist behave as on the
topdown twin.

**Bottom-up ranked order: B2 first** (mechanical, decision-safe,
~12 % of this flow and it compounds into healer trials), **then B1**
(the single biggest item anywhere in the chip corpus at 46 % of this
flow, but it needs the C++ investigation above), then the shared
P8/P6a items.

## 4. Recommended order

1. **C1 dirty-tracking + dedup** — the ~20 s that is pure waste; no
   decision surface at all (persistence only), gated by byte-identical
   BDB dumps + the corpus.
2. **P5 generation fan-out** — the biggest true parallel win on the
   user's machine (36.9 s stage); the parallel_sweep machinery is
   proven.
3. **P8 scoped escalation re-solves** — bounded, measurable, no
   determinism risk (same predicate, same escalations, cheaper
   re-check).
4. **P6a congestion-map parallel-for** + **P7 DNUTS layer threads** —
   small, safe, ride existing patterns.
5. **P9 async persistence** — only if the profile still shows it.
