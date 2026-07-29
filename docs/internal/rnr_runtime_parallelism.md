# rnr flow runtimes — bottleneck taxonomy & parallelization plan

**Vehicles:** `flow/rnr/mix2_fast_on_aligned_sql.buda` (63.0 s) and
`flow/rnr/mix2_fast_bottomup.buda` (59.2 s), measured 2026-07-29 on
Linux/glibc, 4 cores, `-O3 -march=native` (`main` @ b0b9ad0).  Continues
[`ripup_runtime_analysis.md`](ripup_runtime_analysis.md) (items A/B/
`_open_segments` memo shipped; C investigated → root-caused to the
binding boundary; D/E open) — this note re-measures post-heal flows,
answers "what can we parallelize", and ranks everything found.

## 1. Where the ~60 s goes (per-command)

| command | aligned | bottomup |
|---|--:|--:|
| **ripup_reroute (stage b)** | **38.2 s** | **39.8 s** |
| **ripup_reroute (stage a)** | **13.6 s** | **12.5 s** |
| negotiate (a + b) | 7.1 s | 2.6 s |
| run_planner hier 5 | 1.7 s | 1.6 s |
| dump_topologies | 1.0 s | 1.0 s |
| generate_hier_topologies | 0.7 s | 0.7 s |
| everything else | < 1 s | < 1 s |

The two ripup calls are **~85 %** of both flows.  Everything below is
about them; the only non-healer notes are §5.

## 2. The taxonomy: it is NOT the solver

Stage-b ripup (bottomup): 39.8 s, 461 trials, buckets
`replan 2.6 + nuts 14.0 + dnuts 11.7 + screen 2.2 + snapshot 0.4 +
restore 1.0 = 32 s` (+ ~8 s un-bucketed Python).  But the instrumented
C++ **solve passes** inside those buckets sum to only:

- `nuts` passes ≈ 7.3 s of the 14.0 s bucket,
- `dnuts` passes ≈ 1.3 s of the 11.7 s bucket.

Micro-timing one representative trial statement-by-statement (100
bundles, 1705 candidates, 150 fixed segments):

| per stage-b trial (~68 ms) | ms | what it is |
|---|--:|---|
| `NUTSEngine.run(self.bundles)` | ~28–31 | instrumented passes (~16 ms flow-average; 24.4 ms on the measured baseline solve) + a directly-measured **6.7 ms/call boundary gap** — the pybind list→`vector<BundleWrapper>` conversion, every wrapper + every candidate copied at the call boundary (engine ctor / `add_fixed_segments` / grid setters are ≈ 0.1 ms combined) |
| `make_bus_segments(self.bundles, …)` | 10–11 | the SAME whole-corpus copy again (the item-C dig attributed ~9 ms to marshalling + analysis in a mismatched frame) |
| metric evals (`_rr_disconnected_bits` residual) | ~13 | ~6 ms × ~2.2 evals/trial — the post-memo per-bundle loop + `topo_uid` keying |
| `replan_bundle` | 5.7 | recharge-all + plan-one (another list→vector crossing) |
| DNUTS engine solves (×2) | ~1.3 | **the actual bit placement** |
| screen (amortized), snapshot/restore, glue | remainder | |

**The C++ solvers are ~1–16 ms; the orchestration around them is
~50 ms.**  The single biggest mechanism is the pybind copy of the whole
wrapper list (with all candidate pools) on every `vector<BundleWrapper>`
crossing — ≥ 2 full crossings per stage-b trial (`run`,
`make_bus_segments`), plus `replan_bundle` and each `screen_candidates`
batch.  `run()`'s internal mutable re-copy (`bundle_copy` pass, 1.6 ms)
is separate and additional.

The second mechanism is **trial volume**: 461 stage-b trials, and the
`iter N: sweeping 120–137 deferred move(s)` full-fidelity stall sweeps
(5–6 of them, re-run after every later-tier commit) are the bulk.  The
sweep is the stall certificate — when nothing improves it MUST evaluate
every deferred move, and it re-runs whenever a global/class/release
commit changes the baseline.

## 3. Parallelization opportunities (ranked, 4-core host)

### P1. Batched parallel trial sweep (C++) — the big one
The deferred stall sweep is **embarrassingly parallel**: k independent
`('idx', tidx)` moves evaluated against the SAME committed baseline,
accept = first *in order* that strictly improves (deterministic under
parallel evaluation: evaluate all k, take the lowest-index improver —
identical accept to today's sequential scan when none improves, and the
certificate needs all k anyway).  A C++ `sweep_trials(bundles, moves)`
entry point that (a) crosses the binding ONCE, (b) for each move: clones
the touched state, replans incrementally, runs NUTS (+ the DNUTS bit
placement — `make_bus_segments` is already C++), (c) fans the moves
across a thread pool with the GIL released, and (d) returns per-move
metrics.  Combines the marshalling fix (one crossing for k trials) with
real 4× parallelism on the dominant cost.  The engines are
self-contained (no Python callbacks), so `py::gil_scoped_release` is
safe.  **Effort: high** (a per-move state-clone discipline in C++;
determinism gate: byte-identical accepts vs the sequential sweep on the
corpus).  **Stake: the majority of the ~52 s healer time; realistically
−50–70 % of stage b.**

### P2. Opaque `BundleWrapperVec` — kill the per-call copy everywhere
`py::bind_vector<std::vector<BundleWrapper>>` (opaque) and hold
`session.bundles` as that C++-backed container: every existing
`vector<BundleWrapper>` call (`run`, `optimize_topologies`,
`make_bus_segments`, `screen_candidates`, `replan_bundle*`,
`band_occupants`, …) becomes zero-copy pass-by-reference with no
signature changes C++-side.  **Stake: ~16–18 ms of the ~68 ms stage-b
trial (~25 %; the measured 6.7 ms `run()` boundary gap + ~9 ms
`make_bus_segments` marshalling + shares in `replan_bundle`/screen),
roughly 8–10 s across the two flows** — and it is the enabling step for
P1 (which wants a reference it can fan out from).  **Effort:
medium; risk: aliasing semantics** — Python-side code that relies on
element access returning copies must be audited (the `w.input.candidates
= cands` write-back idiom stays; `self.bundles = list(...)` sites become
container constructions).  Gate: full byte-identity corpus.

### P3. Per-layer threads inside `orientation_fixpoint`
Within one orientation half-iteration, `solve_layer` runs per layer in a
loop (`nuts.cpp:2111`) and layers of one direction are independent
(cross-layer coupling is between the H and V *groups*, which alternate).
Threading the per-group loop parallelizes the biggest instrumented pass
cluster (`context`+`fixpoint` ≈ 4.6 s of stage b).  On THIS design
(~16 ms/solve, 2–4 layers/group) the win is bounded (≤ 2×) — it matters
more for larger designs.  **Effort: medium** (shared `NutsContext`
occupancy must be partitioned per layer — verify no cross-layer writes).
Do after P1/P2; measure on `bigHalf`/`big2` where solves are larger.

### P4. Parallel screening
`screen_candidates` is 31 ms/batch × 29 (0.9–2.2 s/flow); candidates in
a batch are independent single-bundle placements against frozen
occupancy — a natural thread-pool map.  **Effort: low-medium**, small
stake alone, but free once P1's pool exists.

### NOT parallelizable (by design)
- The **hill-climb accept chain** (sequential strict-improvement commits
  — order IS the algorithm); only each stall's *evaluation set* is
  parallel (P1).
- The **planner's greedy commit order** (widest-first, band charging is
  order-dependent) and negotiate's `replan_bundle_ripup` victim ladder.
- Stage a ↔ stage b (data dependency).

## 4. Non-parallel bottlenecks (cheap, do first)

### N1. Metric residual (`_rr_disconnected_bits`) — ~6 s/flow
Still ~6 ms/eval after the uid-memo: the per-eval loop walks EVERY
bundle calling `topo_uid` on its selected candidate to key the memo.
Key by `(bid, id(candidates), selected_index)` or maintain a running
total adjusted only for `_rr_dirty` bundles (the plan already sketched
in ripup_runtime_analysis.md item B, second half).  **Low risk, ~10 %
of stage b.**

### N2. `run()`'s internal mutable copy when doglegs cannot fire
`bundle_copy` ≈ 1.6 ms/solve (0.75 s/flow) pays for the dogleg
fallback's mutable clone on every trial solve, but a re-solve of an
already-adopted, stable selection almost never doglegs.  A
`copy-on-first-dogleg` (solve from `bundles_in`, clone lazily only when
`detect_dogleg_plans` returns work) removes it from the common path.
**Low risk.**

### N3. Trial-volume levers already scoped (item E)
Rank each stall sweep by the screen scores already computed (improvers
surface early; the no-improver certificate cost is unchanged — that is
P1's job), and re-measure `warm_trials` default-on for bottom-up flows
(item D: the warm eval is 4.6–6× cheaper than these 68 ms cold trials,
and the crossover condition "cold ≥ 3× warm" is now clearly met).

### N4. Flow-level trims
`dump_topologies` costs ~1.0 s in BOTH checked-in flows purely for a
text dump mid-flow (drop it or make it opt-in verbose); negotiate
stage-a on the aligned flow spends 3.6 s in 3 full `replan` passes
(inherent); `run_planner hier 5` at 1.7 s and `generate_hier_topologies`
at 0.7 s are one-shot and fine (per-bundle generation is independent and
could thread, but the stake is < 1 s).

## 5. Recommended order

1. **N1 + N2** — bounded Python/C++ tweaks, ~6–7 s combined, byte-identity
   verifiable, no new machinery.
2. **P2 (opaque vector)** — the structural marshalling fix, ~12 s, and
   the foundation P1 builds on.
3. **N3** — screen-ranked sweeps + the bottom-up `warm_trials` re-measure
   (config-level, cheap to try).
4. **P1 (parallel C++ sweep)** — the headline parallel feature; gate on
   byte-identical accepts vs sequential.
5. **P3/P4** — solver/screen threading, measured on the bigger corpus
   where the solve itself dominates.

A realistic composite: N1+N2+P2 ≈ **−35–40 % of the healer time with no
parallelism at all**; P1 on 4 cores takes the remaining sweep-bound
majority down by another ~2–3×.  Endpoints must stay byte-identical
throughout (the corpus diff harness used for #472/#478/#487).

## Implemented — N1 + N2 (2026-07-29)

Both landed together (one bounded C++/binding change each), routes
**byte-identical** on all four gate vehicles (`mix2_fast_on_aligned_sql`,
`mix2_fast_bottomup`, `mix`, `mix2_fast_topdown` — non-timing flow-log
diff empty):

- **N1** — `buda.selected_topo_key(wrapper)`: the selected candidate's
  `topo_uid` + bundle id in ONE zero-copy crossing (a bound
  `BundleWrapper` argument passes by reference).  `_rr_disconnected_bits`'
  per-eval walk keys the memo through it, so the full candidate-POOL copy
  (`w.input.candidates` materializes every Topology per access) and the
  `original_bundle` copy are paid only on a memo miss.  Measured: warm
  metric eval **6 ms → 0.15 ms** (40×; ~13 ms → ~0.3 ms per stage-b
  trial at ~2.2 evals/trial).  The memo keys are equal to the historical
  `(buda.topo_uid(topo), bid)` by construction (same fingerprint, same
  hex format) — guarded by `test_selected_topo_key_matches_topo_uid`.
- **N2** — copy-on-first-dogleg in `NUTSEngine::run`: the mutable
  whole-wrapper clone (every candidate of every bundle) is made only when
  the first solve actually detected cycle plans; the common healer-trial
  re-solve of a stable selection skips it (and the fallback call — whose
  loop is gated on `!out.plans.empty()`, so skipping is
  behavior-identical).  `pass_seconds` keeps the `bundle_copy` key at 0.0
  for schema stability.

**Measured end-to-end** (best-of-N, same box):
`mix2_fast_bottomup` **56.0 → 48.8 s (−13 %)**,
`mix2_fast_on_aligned_sql` **64.9 → 55.6 s (−14 %)**.  The stage-b
un-bucketed time (wall − timing buckets — where the metric evals live)
drops **7.1 → 2.5 s** (bottomup) and **7.5 → 2.1 s** (aligned), and the
`nuts` bucket loses the per-trial clone (aligned stage a: 8.35 → 7.38 s).
Next per the recommended order: **P2** (opaque wrapper vector), then N3.

## Implemented — P2: opaque `BundleWrapperVec` (2026-07-29)

`PYBIND11_MAKE_OPAQUE(std::vector<BundleWrapper>)` (`bind_opaque.h`,
included FIRST in bind_routing.cpp + bind_nuts.cpp — the only TUs binding
functions with the type) + `py::bind_vector` as `buda.BundleWrapperVec`.
Every engine call taking `vector<BundleWrapper>` — `run`,
`optimize_topologies`, `make_bus_segments`, `screen_candidates`,
`replan_bundle[_ripup]`, `replan_candidates`, `candidate_costs`,
`band_occupants`, `recharge_committed`, `extend_grid_for`,
`rerun_layer`, `rerun_bundle_warm` — now takes the container by
REFERENCE with zero copying.  The containment that kept the blast radius
small:

- **Sequence fallbacks everywhere**: each native def is followed by a
  `py::sequence` overload (`wrappers_from_seq`) so plain lists of
  wrappers — hand-built test fixtures, tools, resumed sessions, the
  pre-expansion template re-plan path — keep the historical copy
  semantics unchanged.  Native-first ordering guarantees a vec always
  takes the zero-copy path.
- **Conversion only at the pipeline's creation sites**, where aliasing
  is controlled: the two bundler creation loops, the hier-expansion
  assembly (with the expansion map REMAPPED onto the vec's elements by
  identity, so exp_map instance lists and replica entries mutate the
  same storage the engines see), and the clone-split reassembly
  (`_hier_bundles_orig` then holds element references).  `load_pipeline`
  and the `_hier_bundles_orig`-restore re-plan path deliberately stay
  lists (fallback semantics).
- **Mutation-safety precondition verified**: a full sweep of the planner
  found the ONLY wrapper writes through these entry points are the
  refine pass's probe pin, which restores both fields unconditionally —
  so by-reference passing is behavior-identical (the write-back contract
  remains the returned assignments).  The bind_vector aliasing contract
  (structural mutation invalidates element references) holds because the
  pipeline only ever replaces the container wholesale.
- One latent single-vs-collection type test fixed on the way:
  `TopologyExplorer.__init__`'s `isinstance(wrappers, list)` wrapped the
  vec itself as a single "wrapper"; it now detects the single-wrapper
  case by type and materializes any other iterable.

**Measured**: the `run()` boundary gap drops **6.7 → 2.0 ms/call**, and
end-to-end (same box): `mix2_fast_bottomup` **48.8 → 36.6 s**,
`mix2_fast_on_aligned_sql` **55.6 → 39.0 s** — combined with N1+N2 that
is **−35–40 % from the arc's start** (65/56 s), with all four gate
vehicles byte-identical (same moves, same trials, same endpoints) and
the full fast+mid tiers green (the slow-tier healer end-to-end itself
dropped 153 → 110 s).  Next: N3 (sweep ranking + `warm_trials`
re-measure), then P1 (the parallel sweep, which fans out from exactly
this by-reference container).

## Implemented — P1: batched parallel C++ trial sweep (2026-07-29)

`src/trial_sweep.h/.cpp` + the `buda.parallel_sweep` binding
(`bind_nuts.cpp`, `py::gil_scoped_release` around the whole call) +
`_rr_parallel_deferred_sweep` (ripup.py).  When the screened contender
scan stalls, the deferred stall-certificate moves — the dominant trial
volume (120–137 moves × 5–6 stalled iterations) — are evaluated on a
C++ thread pool instead of one sequential Python-orchestrated trial
each:

- **Per-move private state**: each worker deep-copies the wrapper vec
  (the P2 container, copied ONCE per move C++-side — no binding
  crossings) and clones the planner by value (verified safely copyable:
  const refs, value members, a raw `const RoutingGridStack*`;
  `Topology`'s analysis cache is a per-object shared_ptr with atomic
  refcount and const payload).  Pin the move, `replan_bundle`
  incrementally, NUTS with the bottom-up fixed segments + planner extra
  grids, dogleg adoption on the private copy, and for stage b
  `make_bus_segments` + a DNUTS run that ports the copy-plan path
  (reference solve → oriented sibling copies via `transform_net_segment`
  → rest solve with `add_fixed_bits`).
- **Metric fidelity**: the sweep implements the sequential *fast-trial*
  semantics exactly — stage a replicates `skip_tighten`, stage b runs
  vias-off with the plain-path abort clamped at the committed bar
  (aborts only above the bar, so improving/non-improving verdicts are
  unaffected), and the stage-b primary adds the moved bundle's
  DISCONNECTED term on a caller-side base decomposition (base = total −
  the moved bundle's current contribution; other bundles' contributions
  cannot change with the move, and trial dogleg drift is excluded by the
  dogleg pass's non-severing guarantee, #405).
- **Replay-confirm accept**: outcomes are walked in the sequential visit
  order; the first in-order strict improver is REPLAYED through the
  normal single-move sequential trial — the replay is the accept basis
  and the committed state, so the sweep's numbers only order the pick
  and carry the stall certificate.  A worker that cannot evaluate a move
  (incremental replan unavailable) falls back to the sequential trial at
  its original position; a sweep-vs-replay disagreement is a LOUD
  warning with the replay verdict kept.  Trial counting mirrors the
  sequential path exactly (non-improving swept move = the trial it
  replaces; the winner counts via its replay), so the `done:` lines are
  byte-identical.
- **Gating**: on by default (`_RR_PARALLEL_SWEEP_DEFAULT`), only when
  fast trials are on and warm trials are off (`use_par = use_parallel
  and _rr_fast_trials and not warm` — the sweep implements the
  fast-trial metric, and the warm path must warm-eval each move
  first).  `no_parallel_sweep` opts a run out; `BUDA_SWEEP_THREADS`
  caps the pool (0 = hardware concurrency).  A winner is committed only
  through the replay, so BDB persistence and dogleg-slot handling ride
  the existing sequential machinery untouched.

**Validated**: all four gate vehicles decision-identical par vs seq
(same contender improvements, same moves, same trial counts, same
endpoints; `BUDA_SWEEP_THREADS=1` also identical; zero divergence
warnings).  **Measured** (paired runs, same 4-core box):
`mix2_fast_bottomup` **40.2 → 29.8 s (−26 %)** — the stage-b ripup call
25.9 → 16.8 s, its sequential 465-trial nuts+dnuts cost (11.3 + 6.3 s)
collapsing to 219 trials + 2.9 s of parallel sweep; stage-a ripup 7.6 →
6.3 s.  `mix2_fast_on_aligned_sql` **33.5 → 29.7 s (−11 %)**.  `mix` /
`mix2_fast_topdown` never stall into the sweep and are unchanged.  From
the arc's start (65/56 s) the two rnr flows now sit at **~30 s each
(−47 % / −25 %)**.  Regression cover:
`test/tests/test_ripup_parallel_sweep.py` (sweep fires + deferred
winner accept with all moves force-deferred, par-vs-seq done-line +
selection agreement, the `no_parallel_sweep` token, and a slow-tier
end-to-end decision-line diff on the real mix2 bottom-up vehicle).

## N3 verdicts (2026-07-29)

- **Stall-sweep reordering** (rank the deferred list globally by screen
  score): SKIPPED.  The deferred lists are already screen-score-ordered
  *per contender*, and the sweeps accept the first in-order improver —
  a global reorder changes which improver is found first, i.e. the
  committed trajectory, breaking the byte-identity gate for a
  volume-only win P1 already collects (the sweep pays ~the slowest
  single trial per stall regardless of order).
- **`warm_trials` re-measure** (the item-D crossover claim): measured
  WORSE post-P1 — `mix2_fast_bottomup` 45.0 s, `mix2_fast_on_aligned_sql`
  40.6 s vs the 29.8/29.7 s defaults (endpoints identical).  Warm
  trials gate the parallel sweep off (each move must warm-eval before
  its cold trial, an inherently sequential pre-filter), so the opt-in
  now pays the warm overhead AND forfeits the sweep.  `warm_trials`
  stays default-OFF; the crossover argument is obsolete on stall-sweep-
  bound flows — a future case for it needs per-trial cold cost to
  dominate *outside* the parallel sweep's reach (e.g. the main
  contender scan on a much larger design).

Remaining from the ranked plan: P3 (per-layer solver threads) and P4
(parallel screening), both deferred to a larger-corpus measurement
where the solve itself dominates.

## Implemented — P3: per-layer threads in the orientation sweep (2026-07-29)

`orientation_fixpoint`'s group solve (`solve_group`) now runs the
per-layer `solve_layer` calls of one orientation group on worker
threads.  Safety was established by audit, not assumption: a
`LayerSolver` reads/writes only its own layer's segments — alignment
siblings resolve through the SAME-LAYER map, the junction-anchored
preference reads only PERPENDICULAR partners (the other group, quiescent
during this group's sweep; the partner loop's direction check was moved
ahead of its `placed` read so a same-direction partner's mutable fields
are never touched from a worker), occupancy/keepouts/constraints are per
layer, and neither ctx nor engine state is written — so the parallel
group solve is **result-identical to the sequential loop by
construction** (pinned by `test_nuts_layer_threads.py`, which forces the
threaded path on a small design and asserts placement equality).

Controls: `NUTSEngine::set_layer_threads(n)` (1 = sequential — what
`parallel_sweep`'s workers set, the move fan-out already owns the
cores), `BUDA_NUTS_THREADS` env, auto = hardware concurrency behind a
**size gate**: auto threads only when the group's parallelizable work
remainder — Σ segs² over its non-largest layers, the O(segs²)
placement-occupancy proxy for everything that can overlap the heaviest
layer — is worth a spawn (≈ two 256-seg layers).  An explicit thread
count bypasses the gate.  The gate affects speed only (results identical
either way), so it needs no byte-identity guard.

**Measured — the honest verdict is that the P2-era stake is gone:**

- **Corpus (mix2 vehicles, bigHalf, big2, tc3a): no stake left.**  P1
  moved the dominant trial volume into `parallel_sweep` (whose workers
  solve sequentially — the right grain), leaving residual sequential
  `fixpoint` buckets of 0.03–0.9 s per flow.  Ungated threads actively
  REGRESSED them (bottomup 0.82 → 1.07 s — spawn cost vs ~ms solves);
  with the gate every corpus flow takes the sequential path and is
  byte-identical AND perf-neutral vs pre-P3.
- **At scale the sweep is real but Amdahl-bounded.**  On a synthetic
  3813-segment 6-layer design (1400 buses, heavily congested) the gate
  engages and `run_nuts` drops 199.4 → 194.9 s — the fixpoint bucket's
  parallelizable remainder (13.5 s total, groups of 3) — with
  byte-identical output.  Two structural limits cap the win: (a) the
  planner concentrates load on the lowest TOP layer (measured group
  skew 553/245/180 segs), so the heaviest layer dominates the group and
  parallel wall ≈ max(layer); (b) at scale the solve is dominated by
  passes P3 does not touch — `corner` 121 s + `repair` 50 s vs
  `fixpoint` 13.5 s on that synthetic.
- **Follow-up candidate (new):** the `corner` pass (the
  corner-overlap resolution loop — repeated dirty-layer re-solves +
  `repair_overlaps` per accepted iteration) is the true at-scale
  hotspot.  Its dirty-layer loop can mix directions, so it is NOT
  trivially parallel; any attack on it is an algorithmic item
  (incremental re-solve, bounded repair), not a threading one.

Net: P3 lands as cheap, provably-identical machinery that auto-engages
on heavy balanced groups and stays out of the way everywhere else.  P4
(parallel screening) remains deferred — same reasoning as P3's corpus
verdict: the screen bucket is ~1.2–1.6 s and already amortized by the
P1 sweep's deferral flow.
