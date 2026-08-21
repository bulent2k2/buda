# BUDA Script Reference — Stages 4 & 9 — Track assignment (NUTS)

Abstract bus-level track placement and bit-level detailed placement, plus the feedback passes: `run_nuts`, `run_nuts_on_layer`, `run_detailed_nuts`, `ripup_reroute`, `negotiate_congestion`, `refine_selection`.

Part of the [BUDA Script Reference](../BUDA_SCRIPT_REFERENCE.md) — see its pipeline overview for where these commands run in the flow.

---

## Stage 4 — Abstract NUTS

### `run_nuts`

```
run_nuts [<track_pitch>]
```

Runs the Non-Uniform Track Sharing (NUTS) 1.5-D rectangle packing solver.
Assigns a concrete perpendicular `track_position` to every bus segment on
every layer, guaranteeing no physical overlaps (within capacity).

The algorithm sweeps segments by span start, placing each new segment at the
lowest feasible position within its Hanan-grid-cell interval constraint using
a first-fit strategy. Each layer is solved independently and in parallel.

| Argument | Type | Default | Description |
|---|---|---|---|
| `track_pitch` | float | stored pitch | Minimum gap between the upper edge of one segment and the lower edge of the next, in layout units. When omitted, reuses the pitch previously set by `set_track_pitch` (or `1.0` if never set). |

**Output:** Prints segment count, interval violations, and track overlap counts
per layer. Writes a detailed overlap report to `<script>_nuts.log`.

**Pitch consistency warning:** If an explicit `track_pitch` argument is given
that differs from the pitch `run_planner` was called with, a warning is printed
reminding you to call `set_track_pitch` before `run_planner` (or re-run
`run_planner`) so the planner's band reservations and NUTS's packing gap agree.

**Notes:**
- Must be called after `run_planner` (or after `generate_topologies_for_bundle`
  if skipping the planner).
- The track pitch used here is remembered and reused by `run_nuts_on_layer`.
- An *interval violation* means a segment could not fit within its Hanan-cell
  interval; it is placed at the interval centre as a best-effort fallback and
  counted.
- A *track overlap* means two segments on the same layer have overlapping
  spans and overlapping perpendicular extents — a physical short. The overlap
  report details each collision.

**Example:**
```
run_nuts 2.0
```

---

### `run_nuts_on_layer`

```
run_nuts_on_layer <layer_name>
```

Re-solve NUTS for a single named layer, leaving all other layers untouched.
Useful for iterative refinement after inspecting the overlap log for a
specific layer.

| Argument | Type | Description |
|---|---|---|
| `layer_name` | str | Layer name as declared in `def_layer`, e.g. `M3` or `M5`. |

**Requires:** `run_nuts` must have been called first; `run_nuts_on_layer`
updates the existing `NUTSResult` in place.

**Output:** Prints per-layer violation and overlap counts for the re-solved
layer. Appends a timestamped section to the existing `<script>_nuts.log`.

**Example:**
```
run_nuts 2.0
run_nuts_on_layer M3     # re-solve only M3 after reviewing the log
run_nuts_on_layer M5     # then re-solve M5 if needed
```

---


## Stage 9 — Detailed NUTS

Stage 9 snaps each abstract bus segment (from Stage 4) to concrete signal-track positions (from Stage 8). See [docs/detailed_nuts.md](../detailed_nuts.md) for the full design.

### `run_detailed_nuts`

```
run_detailed_nuts [lo_hi|hi_lo]
```

For each bus segment in the NUTS result, calls `signal_tracks_in()` on its layer's routing grid and assigns one signal track per bit-wire.

| Argument | Type | Default | Description |
|---|---|---|---|
| `lo_hi` / `hi_lo` | keyword | `lo_hi` | Bit ordering: `lo_hi` assigns bit 0 to the lowest available signal track, increasing upward; `hi_lo` assigns bit 0 to the highest track, decreasing. |

**Algorithm:**

1. Convert each `TrackSegment` from `run_nuts` to a `BusSegment` (layer, span, interval, bit_width from rounded abstract width).
2. For each `BusSegment`, enumerate signal tracks inside `[interval_lo, interval_hi]` using the layer's `RoutingGrid`.
3. Select tracks according to `bit_order`:
   - `lo_hi`: take the first `bit_width` signal tracks.
   - `hi_lo`: take the last `bit_width` signal tracks.
4. If a bus is marked `timing_critical` (not yet settable from `.buda`; API available in Python): find the first contiguous window of `bit_width` signal tracks — a window where no POWER/GROUND/CLOCK track centre lies between any adjacent pair.
5. If fewer signal tracks are available than `bit_width`, or no valid contiguous window exists, the entire bus is **unplaced** (all-or-nothing; no partial placement).

**Output:** Prints the total number of net segments placed and the number of bits unplaced.

**Leaf-cell keepouts (Gap 2):** Before solving, `run_detailed_nuts` automatically
installs keepout zones on every non-TOP layer's routing grid for each solid
(non-container) leaf-cell block. This prevents signal tracks from routing over
cell interiors on LOW layers — matching the constraint the planner and abstract
NUTS already enforce. Blocks marked `container` (hierarchy envelopes) are
excluded from this automatic keepout. The keepouts are installed once per routing
grid object and are transparent to repeated `run_detailed_nuts` calls.

**Requires:** `run_nuts` must have been called first. At least one `def_track_pattern` must cover the layers used by the NUTS result.

**Example:**
```buda
run_nuts 2.0
def_track_pattern 4 0.0  POWER 2.0 1.0  SIGNAL 1.0 1.0  SIGNAL 1.0 1.0  GROUND 2.0 1.0  SIGNAL 1.0 1.0  SIGNAL 1.0 1.0
run_detailed_nuts
```

With HI_LO ordering:
```buda
run_detailed_nuts hi_lo
```

---

### `set_pair_align_heal`

```
set_pair_align_heal [on|off]
```

Opt-in **measured-accept pairwise-overlap alignment**, applied at the end of
every `run_detailed_nuts`. Default **off** — a flow that never calls it is
bit-identical. With no argument, prints the current state.

**The artifact it targets.** Two same-net stubs straddling a trunk should
*align*: share one track window, so each bit runs as a single straight wire
with no per-bit trunk jog. Stage 9's ordered-anchor placer sorts segments by
`abstract_pos` and seats each at its **own** anchor, so a later same-net
segment can reuse an earlier one's tracks only if they fall inside its own
window. Alignment is therefore opportunistic, and the processing order is not
mirror-invariant: on a mirror-symmetric floorplan one stub pair aligns while
its mirror splits.

**What the heal does.** Re-solves stage 9 with pair-align on — restrict a
stub's track pool to the interval overlap it shares with same-bundle
same-width partners, then proactively adopt a placed partner's exact tracks —
and **keeps** that result only when unplaced and overlaps do **not** rise and
detailed wirelength strictly drops. Otherwise the baseline is restored. On
accept it prints `[DetailedNUTS] PAIR-ALIGN: …` with the WL before/after.

**The accept is what makes it safe.** The *unconditional* form of the same
mechanism is corpus **net-negative** (0 better / 7 worse): restricting stubs
to their overlap band concentrates same-net wires and starves signal tracks on
congested designs, stranding more bits. Behind the accept the same mechanism
measures **0 better / 0 worse / 37 unchanged** — it rejects every regressor —
so enabling it cannot make a design worse. Its wins are on *uncongested*
designs with misaligned pairs.

| Argument | Type | Default | Description |
|---|---|---|---|
| `on` / `off` | keyword | *(prints state)* | Enable/disable the heal for subsequent `run_detailed_nuts` calls. |

**Scope:** applies to flat and hier flows alike, **including bottom-up
(`hier.locked`) sessions** — unlike the cull/reseat heals, which are scoped
out of those because they mutate layer assignment and re-run the abstract
solve. This heal re-runs stage 9 only, so `nuts_result` and `plan.seg_layers`
are untouched; and the bottom-up path aligns the **reference** instance, whose
bits are then copied to the siblings, so template uniformity is preserved by
construction.

**Requires:** set it **before** `run_detailed_nuts` — the heal reads the flag
when that command completes.

**Healers ahead → the heal stands down.** Because it hooks
`run_detailed_nuts`, on a healing flow it would fire at a *mid-flow*
invocation and `negotiate_congestion` / `ripup_reroute` would then re-solve
stage 9 from their own trials, overwriting whatever it aligned — the solve
would be pure cost (measured on `mix2_fast_bottomup`: accepted at a
68-unplaced mid-state, endpoint byte-identical to not running it). So the
heal skips itself while healers are **declared ahead and have not run yet**
— `set_planner_param healersAhead 1`, the same explicit declaration that
gates the `kSegsRel` default and the `run_nuts` dead-span auto (issue #444;
never a script scan). Once a healer has run, or on a flow that never
declares one, this DNUTS result is the endpoint and the heal runs normally.

A flow that heals but does **not** declare `healersAhead` therefore still
pays the mid-flow solve — declaring it is the documented contract, and the
same declaration is what the other two gated behaviors key off.

**Zero-cost on no-gain designs (the #8b WL-gain predictor):** the DNUTS
solve reports, in passing, the total per-bit trunk jog across the segment
pairs the alignment could unify (`DetailedNUTSResult.pair_misalign_wl` —
the wirelength the re-solve targets). When it is **zero** the heal skips
its re-solve entirely, so enabling the heal on a design with nothing to
align costs nothing. The prediction is an optimistic bound on the targeted
gain (the pair may align mid-overlap and realize about half of it — the
accept still measures the real thing); the accept line reports both
(`predicted jog N`).

**Example:**
```buda
run_planner
run_nuts
set_pair_align_heal on
run_detailed_nuts
check_design
```

**Study path:** `BUDA_DNUTS_PAIR_ALIGN=1` in the environment forces the
alignment **unconditionally**, with no accept gate — the measured
net-negative configuration. Use it for experiments only; use the command for
real flows.

---

### `set_placed_endpoints`

```
set_placed_endpoints [on|off]
```

Decide a segment's **endpoint** connections from the **placed** geometry rather
than the nominal label. Default **off** — a flow that never calls it is
byte-identical. With no argument, prints the current state.

**The defect it fixes.** `is_endpoint` is derived once, at generation, from
nominal coordinates (`at_pos == along_lo || along_hi`). NUTS then moves the
ends — `tighten_spans_to_reach` contracts a span back to its outermost junction
— and nothing re-derives the label. DetailedNUTS reads it as its **only** gate
on per-bit snapping: an endpoint conn pulls each bit's end onto its own partner
bit's track, a mid-span conn merely asks the span to keep covering. A junction
that was interior at nominal and *is* the placed end therefore leaves every bit
stretched to one shared abstract end — dangling metal past each bit's own via,
reported only by the last `check_design` and by nothing before it.

**What changes.** Only the value written into the stage-9 descriptor
(`BusSegmentConn`), which is rebuilt on every `make_bus_segments` call. The
nominal analysis (`ConnSeg`) is cached on the `Topology` and shared with
generation, the planner and the topo-stage audit — it stays nominal.

**Promotes only** (mid → endpoint). The symmetric rule — also demoting a
nominal endpoint that placement moved the span end past — was implemented and
measured, and is **wrong**: clearing the endpoint flag makes that end eligible
for the tapered retraction, cutting a wire short of a partner it still has to
meet. It produced real `SEG_OPEN`s and measured 14 flows worse against 1, with
detailed wirelength up.

**Measured** (48-flow QoR corpus, off vs on): **3 better, 0 worse, 45
unchanged**; abstract WL unchanged (+0 — the change is stage-9 only), detailed
WL −1,114. `bigHalf` 0/0/3 → 0/0/1, `rnr/mix` 0/0/1 → 0/0/0,
`chip/chip_stack_bottomup` 83/221/21 → 83/221/20.

Study override `BUDA_DNUTS_PLACED_ENDPOINTS=1` seeds the default so a corpus
can be A/B'd without editing every flow; an explicit token always wins.

Repro and analysis: [hybrid_leg_overhang.md](../internal/hybrid_leg_overhang.md).

---

### `set_dead_span_escalate`

```
set_dead_span_escalate [on|off]
```

Force post-NUTS **dead-span escalation** at every `run_nuts`. Default **off**
— a flow that never calls it is bit-identical. With no argument, prints the
current state.

**The artifact it targets.** A LOW-layer segment whose *final placed geometry*
— its seat, span × slide window — offers **fewer keepout-clear signal tracks
than the segment's member bits** is a guaranteed DetailedNUTS open: admission
is all-or-nothing, so a partial-supply shortfall strands **every** bit of that
segment. The pass moves such a segment to the cheapest same-direction TOP
layer and re-solves NUTS, iterating until no dead LOW segment remains.

This is the **final-geometry** form of the planner's `nontop_dead_span_gate`.
The plan-time gate samples the conservative *abstract* span, so a zero pool
there cannot distinguish a genuine cull from a survivor whose final span
clears the keepout — which is why that gate stays off by default. The placed
geometry has no such ambiguity: it fires only on real zero-survivor seats.

**You usually do not need this command.** The escalation already runs
automatically at `run_nuts` whenever the flow declares
`set_planner_param healersAhead 1` — the same explicit declaration that gates
the proactive `kSegsRel` default (issue #444). Running it *before* the healers
measured better than the stage-b fold alone (`mix` 1/16 → 0/0, `bigHalf` 190 →
94 opens), and the two compose. Use `set_dead_span_escalate on` to force the
pass in a flow that has **no** healer ahead of it; set
`_dead_span_auto_at_run_nuts = False` on the session to suppress the automatic
path.

See `docs/internal/wishlist-planner.md` → "dead-span discriminator" for the
measurements behind the default.

---

### `ripup_reroute`

```
ripup_reroute [max_iter] [use_edge_candidates] [no_global] [no_class_moves]
              [no_release_moves]
              [fast_trials|no_fast_trials] [screen|no_screen]
              [warm_trials|no_warm_trials] [converge_guard|no_converge_guard]
              [no_parallel_sweep]
```

Feedback-driven rip-up & re-route. The congestion planner's band-capacity model is
layout-width based, so it can report `overflow=0` for a band that NUTS (or
DetailedNUTS) later finds contended — and re-running `run_planner` re-derives the
same plan. `ripup_reroute` closes that loop: it reads the **actual** overlaps/opens,
greedily re-routes a contending bundle to an alternate topology candidate, re-runs
the pipeline, and keeps only moves that reduce the metric.

| Argument | Type | Default | Description |
|---|---|---|---|
| `max_iter` | int | `10` | Maximum number of outer hill-climb iterations (each commits at most one re-route). |
| `use_edge_candidates` | flag | off | Also try the per-edge MST L/Z **flip** move-source (below) on contended MST candidates. Off by default. |
| `no_global` | flag | off | Disable the **global-occupant pass** (below), which otherwise runs when the contender scan stalls above zero. |
| `no_class_moves` | flag | off | Disable the **bottom-up template class-move pass** (below): when the residual contention sits on `hier.locked` bottom-up template instances, re-pin the cell TEMPLATE and re-route the whole rotation class in one measured move. A no-op on non-bottom-up flows either way. |
| `no_release_moves` | flag | off | Disable the **measured-infeasibility uniformity break** (below), the stall chain's true last tier (stage b only, and ALSO gated on `check_template_tracks on_mismatch independent`): release a locked instance whose copied routing is measured DNUTS-open and solve it individually. A no-op without the policy / without locked opens either way. |
| `no_fast_trials` / `fast_trials` | flag | fast on | Disable / force **fast trials** (below): trials skip metric-neutral solve passes; commits always re-run the full pipeline. |
| `no_screen` / `screen` | flag | screen on | Disable / force the **fixed-context screen** (below): rank each contender's alternates by a ~ms frozen-context placement and full-trial only the top few, deferring the rest to the iteration's stall sweep. |
| `warm_trials` / `no_warm_trials` | flag | warm OFF | Enable / force-off **warm trials** (below): pre-filter each move with the warm-start single-bundle re-solve, cold-trialing only warm-improving moves; warm-rejected moves are cold-swept at the stall point (the stop certificate stays a full cold sweep). Off by default — corpus-measured cost-neutral once the screen has cut trial volume, and measured WORSE post-parallel-sweep on the rnr vehicles (the sequential warm pre-filter gates the parallel sweep off). |
| `no_parallel_sweep` | flag | sweep on | Disable the **parallel stall sweep** (below): evaluate the deferred stall-certificate moves sequentially instead of on C++ worker threads. Decision-identical either way (the parallel path replays its winner through the sequential trial); the token exists for triage and single-core determinism studies. `BUDA_SWEEP_THREADS` caps the pool size (0 = hardware concurrency). All tokens are order-independent — `ripup_reroute 20 no_global` and `ripup_reroute no_global 20` are equivalent. |

**Two stages, auto-detected from pipeline state:**

| Run it after… | Stage | Metric driven down |
|---|---|---|
| `run_nuts` | a | NUTS abstract track overlaps (`num_overlaps`) |
| `run_detailed_nuts` | b | DetailedNUTS opens / unplaced bits (`num_unplaced`) |

**Algorithm (greedy hill-climb, first-improving-contender):** each iteration
snapshots per-bundle state, then scans the contending bundles in priority order
(the open bundles in stage b, then the bundles on either side of each NUTS
overlap) and **commits the first contender whose best alternate candidate lowers
the metric** — re-pinning it and re-running planner→NUTS(→DNUTS) silently. It
stops when the metric reaches 0, no contender improves, or `max_iter` is hit. It
is a **no-op** when the metric is already 0 (prints `metric already 0 — nothing
to do`).

> Each trial is a full pipeline re-run, so committing the *first* improving
> contender (rather than the single best move over *all* contenders) keeps an
> iteration cheap on large designs. A flushed per-contender heartbeat makes
> progress visible. On a big design the default `max_iter` may be reached while
> the metric is still falling — the command says so and suggests re-running or
> raising `max_iter`.

Output logs each contender tried and each committed move, e.g.:

```
[ripup_reroute] stage b (DNUTS opens): start metric=236, max_iter=10, 25 contenders
[ripup_reroute] iter 1: contender 1/25 bundle 102 improves 236->174 (topo 4->6)
[ripup_reroute] iter 1: COMMIT bundle 102 topo 4->6, metric 236->174
...
[ripup_reroute] reached max_iter=10 while still improving — re-run ripup_reroute or raise max_iter (e.g. `ripup_reroute 50`) to continue.
[ripup_reroute] done: metric 236->46 after 10 move(s), 96 trial(s).
```

**Flat and hier flow:** Works after both `run_planner` and `run_planner hier`. In
hier flow `self.bundles` is the expanded per-instance list, so a re-route re-pins a
single **instance** wrapper and re-plans the expanded set in place — the right
granularity for relieving a local congestion hot-spot without disturbing the cell's
other instances.

**Move sources (what a contender is allowed to try):**
- **Index alternates (always on).** Re-pin the bundle to one of its *other*
  candidate topologies, tried relevance-first (candidates farthest from the
  measured contention first). The base pool is the 8 cheapest-estimate
  candidates (they are WL-sorted); under measured contention the top-8
  farness-ranked candidates from *beyond* that window are appended after it,
  so a higher-estimate candidate class (an OOB detour trunk, a two-level
  BITRUNK tree — always past index 8 on fan-out bundles) becomes reachable.
  A promotion commits only on a *strictly* better measured (opens, overlaps)
  metric: the cheap candidates are tried first and the commit comparison is
  strict, so at an equal metric the cheaper move always wins the tie.
- **Per-edge MST L/Z flip (`use_edge_candidates`, off by default).** When the
  bundle's *selected* candidate is an MST type (incl. `TRUNK+MST` hybrids, whose
  legs are edge-tagged), also flip the L/Z bend of that candidate's **contended**
  edges in place — a targeted local change that keeps the same candidate index.
  Only contended edges are tried, so the cost stays ~linear in the number of
  overflows rather than exploding to `2ᴺ`. This source is **opt-in** because on
  the current corpus a flip is only ever *tried* on real contended MST edges — an
  index alternate always wins the commit — so leaving it off changes no routes.
  Enable it only when you want to explore edge flips. See
  [MST edge realization](../internal/mst_edge_realization.md).

**Fast trials (round 3, default on; `no_fast_trials` opts out, `fast_trials`
forces on).** Trials skip metric-neutral passes: stage a skips the WL-only
`tighten_pulls` (overlap-NON-increasing by its per-move guard, so the trial
metric is an UPPER BOUND — an accept implies the true state improves at least
as much; only rejections can rarely be spurious), stage b skips per-bit via
emission (pure output; the metric is identical).  COMMITS always re-run the
full pipeline (fast trials take no forward snapshots), so every committed
route is a full-pipeline state and every commit strictly improves the TRUE
metric.  The choice among improving moves can differ from a full-trial run
(first-improving order): measured on the rr corpus — mix / slowdown_rnr /
big2 byte-identical; bigHalf reaches the same 0/0 endpoint by a different
trajectory at 32s vs 88s (123 vs 454 stage-b trials; per-solve savings are
the stage-b DNUTS −13% and the stage-a tighten skip — the rest of that gap
is the trajectory).

**Fixed-context screen (round 3, default on; `no_screen` opts out, `screen`
forces on).** Even with fast trials, every trial re-solves the WHOLE design's
abstract NUTS to evaluate re-pinning ONE bundle.  The screen inverts that:
before full-trialing a contender's alternates, each candidate is placed
ALONE against every other bundle's baseline placement frozen as fixed
occupancy (the bottom-up `add_fixed_segments` machinery; doglegs and tighten
skipped — the result is discarded), which costs ~milliseconds, and only the
best-screened few (`_RR_SCREEN_TOP_N`, default 2) are full-trialed.  A whole
contender screens in ONE C++ call (`NUTSEngine::screen_candidates` +
`CongestionPlanner::replan_candidates`, round 5): the wrapper list crosses
the Python/C++ boundary once, the planner's committed-usage recharge runs
once (candidates plan uncommitted against identical others-only usage —
provably what a `replan_bundle` sequence saw), screen-mode `run()` skips
its dogleg-only mutable deep copy of every wrapper, and only
`(tidx, overlaps, violations)` triples come back — scores byte-identical
to the per-candidate path at ~3 ms instead of ~8-10 ms per candidate
(bigHalf screen bucket 1.58 s → 0.65 s, big2 ripup 0.29 → 0.22 s, same
trajectories everywhere).  The
screened `(overlaps, violations)` is an **ordering, never a metric**: accept
decisions always run on the true full-trial metric — which is exactly what
separates this from the measured-worse-and-reverted layer-scoped two-tier
trials (whose cheap metric decided accepts and mis-ranked).  Screened-out
moves are **deferred, not dropped**: an iteration whose screened scan finds
no improvement sweeps the deferred moves at full fidelity before the global
pass, so the loop still stops only when a FULL sweep proves no improving
move — a bad screen can reorder which improving move is found first (a
trajectory effect, like fast trials) but never weaken the stall certificate
or commit a wrong move.  Measured on the rr corpus (screen vs `no_screen`,
same clean endpoints everywhere): bigHalf rr-enabled flow 40.8s → 16.5s
(stage-b full trials 123 → 11 + 75 screens at ~10 ms; stage-b ripup
28.9s → 4.9s), big2 ripup 0.91s → 0.43s (17 → 3 trials), mix /
slowdown_rnr stage-b ~2.5s → ~1.5s.  When the incremental replan is
unavailable for a candidate the contender falls back to the unscreened
order; the global-occupant pass is never screened (its per-stall budget
already bounds it).

**Parallel stall sweep (rnr runtime P1, default on; `no_parallel_sweep`
opts out).** The deferred stall-certificate sweep is the dominant trial
volume on healer-heavy flows (120+ moves per stalled iteration), and its
moves are independent evaluations against the SAME committed baseline —
embarrassingly parallel.  With fast trials on and warm trials off, the
sweep runs on a C++ thread pool (`buda.parallel_sweep`, GIL released):
each worker deep-copies the wrapper container and planner privately, pins
its move, replans incrementally, and re-runs NUTS (stage b: + bit
placement, incl. the bottom-up DNUTS copy-plan path) with metrics that
implement the sequential fast-trial semantics exactly.  Outcomes are
walked in the sequential visit order and the first in-order strict
improver is **replayed through the normal sequential trial** — the replay
is the accept basis and the committed state, so the sweep's numbers only
order the pick and carry the stall certificate; a move the workers cannot
evaluate falls back to a sequential trial at its position, and a
sweep-vs-replay disagreement is a LOUD warning with the replay verdict
kept.  Decision-identical to the sequential sweep by construction
(validated byte-identical on the rnr vehicles, incl. trial counts).
Measured (4 cores): `mix2_fast_bottomup` 40.2 → 29.8 s end-to-end (the
stage-b ripup 25.9 → 16.8 s), `mix2_fast_on_aligned_sql` 33.5 → 29.7 s;
flows that never stall are unchanged.  `BUDA_SWEEP_THREADS` caps the pool
(0 = hardware concurrency).

**Parallel primary screened scan (rnr runtime P1b, default on with the
same gate).** The primary contender scan — each contender's top-screened
kept moves, the trials the first-improving loop runs one at a time — is
evaluated on the same pool in **lazy visit-order chunks**: a chunk's
contenders are move-listed and screened only when the chunk is reached,
so an early improver costs what the sequential loop costs, while a
grinding stalled scan gets the full pool win.  A contender with a
sweep-improving (or unevaluable) move replays its ENTIRE kept list
through the sequential best-of-list trial, so the committed move, the
printed heartbeat/improver lines, and the trial counts are all identical
to the sequential scan by construction.  Additional gate over P1: idx
moves only (no `use_edge_candidates`) and a **>1-thread pool** — on a
1-thread pool the chunked evaluation is strictly worse than committing
at the first improving trial (measured 2.5× on `rnr/mix`), so
single-thread pools (e.g. qor sweep workers) keep the sequential scan;
the deferred stall sweep stays engaged at any width.  Measured
(`BUDA_THREADS=4`): `mix2_fast_bottomup_caps` stage-b ripup
25.6 → 19.2 s (−25 % on top of P1; fully sequential 47.9 s), decision
lines byte-identical, corpus 0/0/41 with WL ±0.00%.

**Warm trials (round 4, default OFF; `warm_trials` opts in).** A cold trial
re-solves the whole design's abstract NUTS from scratch; the warm-start
re-solve (`NUTSEngine::rerun_bundle_warm`) instead seeds the baseline
placement, places only the moved bundle against it frozen (the screen's
machinery), then unfreezes and runs the real safety passes over the union —
so its cost tracks the move's blast radius, not the design.  Its metric is
exact *for the warm state* and a measured PREDICTOR of the cold metric
(Phase-0 study, `BUDA_RR_WARM_STUDY=1` harness: 91-100% accept agreement,
4.6-6× cheaper per solve on bigHalf; full record in `wishlist-healer.md`).
With `warm_trials` on, each move is warm-evaluated first: only
warm-improving moves pay the cold trial (accepts stay on the true cold
metric — a warm false-accept costs one cold trial), and warm-rejected moves
are **cold-swept at the iteration's stall point** before any stop or
global-pass verdict, so the stop certificate remains a full cold sweep and
a warm false-reject costs time, never the endpoint.  **Off by default** by
the same measurement discipline that turned the screen on: with the screen
already cutting cold-trial volume to near-minimum and fast trials cutting
their cost, the pre-filter measured cost-neutral to slightly negative on
the corpus (bigHalf 12.3 vs 12.7s; mix wash; big2 +0.09s).  Opt in when
per-trial cold cost dominates — the study's crossover is roughly a cold
trial ≥3× the warm eval (~41-70 ms).

**Global-occupant pass (default on; `no_global` disables).** When the contender
scan stalls above zero — every contender's every move tried, none improved —
one bounded pass widens the search to the **band occupants**: for each
remaining contention site (overlap rectangle / open segment window), the
committed bundles holding the site's planner bands are ranked by their demand
on exactly those bands (`CongestionPlanner::band_occupants`, the same victim
ranking `negotiate_congestion`'s `replan_bundle_ripup` uses, exposed
read-only), and each occupant's index alternates are trialed ranked against
*the site's* location (the occupant itself is non-contended, so its own
contention-derived ordering would be empty). A **non-contended** bundle
holding the contended bands can be the global fix that no contender-derived
move reaches — the big2 b61 class, where the winning candidate is even
window-infeasible (STRICT-rejected at plan time; reachable because a pinned
trial's replan ladder ends in BEST_EFFORT). Strict-improvement accept, top-3
occupants per site (ranked AFTER excluding the already-scanned contenders,
so the site's own overlap parties cannot crowd the slots), 6 moves per
occupant with beyond-window promotions guaranteed representation, ≤36
trials per stall; a `GLOBAL` progress line marks the pass. Like the
contender scan, the pass may override a `topology_pinned` occupant's pin
(only a `hier.locked` template copy is inviolable). Flows that never stall
above zero are byte-identical with the pass on (the whole corpus today).

**Bottom-up template class-move pass (default on; `no_class_moves`
disables).** The stall chain's last tier, after the global pass. A
`hier.locked` wrapper is a bottom-up (`set_bottom_up`) template instance:
its routing is a uniform fixed copy of the cell template's local solve, so
every pass above must skip it — and a design whose residual contention sits
ON locked bundles is stuck (the `mix2_fast_bottomup` plateau). The class
pass moves the **template** instead: for each locked-contender class (up to
8 alternates each, ranked by the contended instance's contention sites —
expansion preserves candidate order, so instance-ranked indices are valid
on the template pool), it force-pins the alternate on the template, re-runs
the **cell-local solve** for its layers (the pin is kept; the other
templates of the cell keep their pins and layers), propagates the pin to
every instance of the rotation class, and measures a **no-replan** pipeline
re-run — every other wrapper keeps its committed assignment, and the moved
class's routing is the fixed copies NUTS recomputes from the re-pinned
template. One move re-routes ALL instances of the class; strict-improvement
accept; a template the user pinned before the local solve is never moved;
BDB persistence is deferred to the accept path (rejected trials leave no
rows). `CLASS` progress lines mark the pass; non-bottom-up flows are
byte-identical (the pass is a structural no-op). Measured on
`mix2_fast_bottomup` + healers: stage-a residual 2→1 overlaps, final DNUTS
opens 16 (2 locked bundles)→8 (1); see
`docs/internal/bottomup_healer_templates.md`.

**Measured-infeasibility uniformity break (stage b; default on, gated on
`check_template_tracks on_mismatch independent`; `no_release_moves`
disables).** The stall chain's true last tier, after the class pass. A
locked instance whose plan-time track pools MATCH its reference can still
strand bits at DNUTS: the conflict is dynamic — neighbors and occupancy at
THAT instance — invisible to any static pool comparison (opens #14:
`mix2_fast_bottomup` bundle 166, where the uniform copy works at 3 of 4
instances and the 4th's surroundings close its window, exhausting every
class-level move). The RELEASE pass breaks uniformity for exactly the
measured-infeasible instance: unlock it (its fixed copy is withdrawn and
NUTS solves it individually; the pin is kept, and the FORCED per-segment
layers are cleared — the planner applies `pinned_seg_layers` to any
candidate, so a repin would otherwise carry the old candidate's H/V layers
onto a different-direction shape, an unbuildable route the (opens,
overlaps) metric cannot see), re-solve with every other wrapper's committed
assignment untouched, and — when the free re-solve alone does not strictly
improve — try the freed wrapper's farness-ranked candidate alternates
before restoring. Strict-improvement accept; the aligned siblings keep the
uniform copy; the released instance's `bu_locked` is persisted so a resume
restores it unlocked; every commit is LOUD (`RELEASE COMMIT` names the
instance and cell). The `independent` policy is the opt-in: it already
declares the user accepts per-instance solving for environmental
mismatches — this extends it to measured infeasibility. Measured: bundle
166's stuck 8-open residual heals to **0 opens with a clean detailed
`check_design`** (release + topo 1→2), matching the top-down twin's
endpoint.

**Notes:**
- It is an explicit congestion-fix pass, so it may re-route any contended bundle —
  including one pinned earlier by `select_topology` (its pin is replaced).
- The base flow is unchanged unless an improving move is found; the command is
  opt-in and additive.
- A stage-b `hi_lo` bit-order selection is preserved across the re-route.
- For a cheaper first pass that clears the bulk of the contention before rip-up
  tries per-candidate alternates, run [`negotiate_congestion`](#negotiate_congestion)
  first — the two compose (negotiate the broad contention, rip-up the residual).

**Requires:** `run_planner` (or `run_planner hier`) and at least `run_nuts` to have
run first.

**Recommended usage — run it in both places.** The two stages clear *different*
causes of unplaced bits, so chaining them clears the most:

```buda
run_planner 5            # or: run_planner hier 5
run_nuts
ripup_reroute            # stage a: drive NUTS overlaps toward 0
run_detailed_nuts
ripup_reroute            # stage b: drive the residual DNUTS opens toward 0
```

Stage a removes the opens that stem from abstract track **contention** (when two
buses overlap on a band, DetailedNUTS cannot fit all their bits) — these are the
cheap, high-yield wins, and clearing them gives DetailedNUTS a much better
starting point. The opens that survive stage a (with `overlaps=0`) are **not**
contention-driven; they are signal-track **capacity** shortfalls — a band whose
abstract width fit but whose discrete SIGNAL-track count is short of the bit count
— which only stage b can re-route around. Measured on
`flow/rnr/mix.buda` (a hier design): baseline 21 overlaps / 236 opens → stage a
**21→0 overlaps, 236→150 opens** → stage b **150→30 opens**, versus 236→46 for
stage b alone.

> The residual opens that neither pass can clear are the pure capacity-mismatch
> cases. Predicting them inside the planner (charging band capacity in
> signal-track count rather than layout width) is a planned follow-on — see
> *Gap A part 2* in `docs/internal/wishlist-planner.md`.

---

### `negotiate_congestion`

```
negotiate_congestion [max_iter] [class_moves|no_class_moves]
                     [press|no_press]
```

Measured-congestion negotiation — a faster, complementary alternative to
`ripup_reroute`'s guess-and-test. Instead of trying a contending bundle's
alternate topology candidates one at a time (each a full NUTS solve),
`negotiate_congestion` feeds the **actual** failures back into the planner as
extra demand on the exact bands where they happened, then **re-plans the
offending bundles _unpinned_** against those corrected prices — so the cost
model itself steers them off the contended bands, choosing among **all** their
candidates in a single planner pass with no per-candidate trial. It typically
clears the bulk of the contention in seconds; `ripup_reroute` is then the
finisher for whatever negotiation leaves.

| Argument | Type | Default | Description |
|---|---|---|---|
| `max_iter` | int | `5` | Maximum number of negotiation rounds. Each round injects the current failures and re-plans; it is kept only if it strictly improves the metric, otherwise it is rolled back and (by default) the loop stops. |
| `press` | flag | off | **Opt-in pressure-escalation retry** (2026-08-07). By default a rejected round ends the run — the first-failure stop. With `press`, a rejected round restores and **retries under the grown PathFinder history pressure** (the history counters persist, so the same rectangles re-inject at grown amounts — the escalation the default stop computes and then throws away), until `max_iter` runs out or a failed retry reproduces the **same** metric as the previous failure (the deterministic replans are insensitive to the grown amounts — certified waste, stop: `identical outcome under escalated pressure`). Opt-in because it measured **0 better / 1 worse** on the corpus as a default: the caps/mix stage-b stalls are price-insensitive (the retry repeats byte-identically), and on `mix2_fast_on_aligned_sql` the pressed retries DO crack the stall on negotiate's own metric (stage b `150 (ovl 12)` → `130 (ovl 6)`, abstract WL −6.5%) but the improved hand-off shifts `ripup_reroute`'s greedy basin and the flow **endpoint** lands worse (2/16/1 → 4/28/2) — the healer-composition hazard. Worth trying by hand on a stuck endpoint (compose with a second healer round, like `refine_selection`); `no_press` states the default explicitly. |
| `class_moves` | flag | off | **Opt-in bottom-up template price translation** (negotiate v2). Normally a `hier.locked` bottom-up template instance is never a negotiation target (its routing is a uniform copy of the cell template's local solve). With `class_moves`, a locked affected bundle's TEMPLATE class is negotiated instead: the iteration's injected band demand is clipped to each instance's bbox, mapped through the inverse orientation transform into the cell frame, and summed across instances into the cell-local planner; the target templates then re-plan **unpinned** under that aggregated price field and the result propagates to every instance of the class (accept/rollback covers the template state; user-pinned templates are never touched; BDB persistence is deferred to the accept). Opt-in because it measured endpoint-neutral at best on the corpus: the stage-a overlap metric is blind to the DNUTS quality its bigger multi-class shuffles trade away (mix2_fast_bottomup final opens 8→16 at default ripup budgets, equal at `ripup_reroute 30`), and the stage-b priced iteration is rejected by its own accept guard — `ripup_reroute`'s class moves already cover the endpoint. Measurement table in `docs/internal/bottomup_healer_templates.md`. `no_class_moves` states the default explicitly. |

**Two stages, auto-detected from pipeline state** (same as `ripup_reroute`):

| Run it after… | Stage | Metric driven down |
|---|---|---|
| `run_nuts` | a | NUTS abstract track overlaps (`num_overlaps`) |
| `run_detailed_nuts` | b | DetailedNUTS opens / unplaced bits — lexicographic `(opens, overlaps)`, so clearing opens can't silently trade away abstract packing |

**Algorithm (PathFinder-style negotiation):** each round

1. injects each measured failure as band demand on its exact `(layer, span, perp)`
   rectangle — a NUTS overlap in stage a, or an open segment's placed window
   (scaled by the missing-bit fraction) in stage b — with **history pressure**
   that grows each time the same rectangle re-appears, so a stubborn hot-spot
   becomes progressively more expensive;
2. re-plans the affected bundles **unpinned** (both bundles of every overlap in
   stage a; the open bundles in stage b), widest-first, against the corrected
   prices — and may even displace a committed bundle blocking the contended bands
   (the planner's own rip-up ladder);
3. accepts the round only on **strict metric improvement** (snapshot/restore
   otherwise), so it is a safe hill-climb. A rejected round ends the run by
   default; with the opt-in `press` flag the loop instead retries with the
   grown history pressure until `max_iter` is exhausted or a failed retry
   lands on the **same metric as the previous failure** (see the `press` row
   above for the measured trade-off).

It is a **no-op** when the metric is already 0 (`metric already 0 — nothing to
do`). Injected demand is always cleared at the end, so it never leaks into later
commands.

**Stage-a scope advisory:** the stage-a metric is NUTS overlaps **only**, so a
clean stage-a exit can sit right above a dirty `check_design` — keepout-seated
segments (exhausted-window commits) and supply-doomed seats are invisible to
the overlap metric and only surface as DNUTS opens at stage b. Both stage-a
healers (`negotiate_congestion` and `ripup_reroute`) print an advisory at exit
whenever such seats exist:

```
[negotiate] advisory: stage-a metric is overlaps-only — 29 keepout-seated
segment(s), 2 supply-doomed seat(s) remain and will surface as DNUTS opens at
stage b (details: check_design).
```

Output logs the start metric and each round:

```
[negotiate] stage a (NUTS overlaps): start metric=10, max_iter=5
[negotiate] iter 1: 10 contention site(s) -> replanned 13 bundle(s), metric 10->5
[negotiate] iter 2: no improvement (metric 5->5) — restored, stop.
[negotiate] done: metric 10->5 after 1 accepted iteration(s).
```

With `press`, the iter-2 line becomes `— restored, pressure escalates.` and the
loop keeps retrying until the budget runs out or a failed retry repeats the
previous failure's metric (`— restored; identical outcome under escalated
pressure, stop.`).

**Requires:** `run_planner` and at least `run_nuts` to have run first.

**How it differs from `ripup_reroute`:** negotiation changes the planner's
**cost model** and lets one replanning pass move *many* bundles at once, whereas
`ripup_reroute` re-pins **one** bundle to an alternate **topology** per committed
move. They compose — negotiate the broad, price-visible contention, then rip-up
the stubborn residual:

```buda
run_nuts
negotiate_congestion     # cheap: reprice the contended bands, replan in one pass
ripup_reroute            # finish the residual NUTS overlaps
run_detailed_nuts
negotiate_congestion     # stage b: reprice the capacity-short bands
ripup_reroute            # finish the residual DetailedNUTS opens
```

See [wishlist-healer.md](../internal/wishlist-healer.md) (item 1) for the design
rationale and the measured negotiate-then-ripup results.

---

### `refine_selection`

```
refine_selection [max_moves] [chase_overlaps] [no_parallel_sweep]
```

Measured selection **WL polish** — selection-basis lever 3 (wishlist-planner
"Selection basis"). The two existing measured loops cannot recover realized
wirelength: `ripup_reroute`'s metric is overlap/opens-only (it stops at parity
and never improves WL), and the planner's `refine_passes` re-scores through
the cost model whose WL term is the generation-time **estimate** — so a
candidate that *routes* shorter than it estimates structurally loses (the
BITRUNK/spine realization gap the 2026-07-30 default-flip study measured).
`refine_selection` closes that gap: it sweeps every eligible bundle's
selection on the **measured** result — screening all alternate candidates
against the other bundles' frozen placement (the fixed-context screen,
ordering only), full-trialing the best two, and adopting a move only when the
endpoint cannot get worse:

- **Default accept (WL polish):** opens, overlaps and NUTS interval
  violations must be
  **parity-or-better componentwise** AND realized abstract WL (the placed
  spans' total length) strictly lower — so the healers' endpoint can never be
  traded for length. Run it at the **end of the flow**, after the last
  `ripup_reroute`: both pre-healer placements were measured to perturb the
  healers' basins (even overlap-parity selection changes shifted their
  trajectories).
- **`chase_overlaps`:** plain lexicographic accept (`(overlaps, violations,
  WL)` stage a; `(opens, overlaps, violations, WL)` stage b) — the aggressive
  pre-healer form, measured
  mixed on the vehicles (it perturbs downstream healing; kept as the
  documented escape hatch for healerless experiments).

| Argument | Type | Default | Description |
|---|---|---|---|
| `max_moves` | int | `30` | Commit budget. Sweeps repeat until a full sweep commits nothing or the budget is spent. |
| `chase_overlaps` | flag | off | Plain lexicographic accept instead of the componentwise WL-polish guard (see above). |
| `no_parallel_sweep` | flag | off | Keep the sequential trial loop. By default the sweep's full trials run on the C++ parallel pool (the ripup P1 machinery in **full-trial** form — tighten on in both stages, DNUTS abort off, outcomes carrying opens/overlaps/violations/realized-WL): bundles whose sweep outcomes the accept guard would not take book their trials at pool width, and a bundle with a would-accept move **replays** its whole kept list through the sequential trial — the replay is the accept basis and the committed state, so committed moves, printed lines and trial counts match the sequential sweep exactly (a sweep-vs-replay disagreement is LOUD and the replay verdict wins). 1-thread pools (`BUDA_SWEEP_THREADS=1`) keep the sequential loop. |

Stage-aware like the healers (stage a after `run_nuts`, stage b after
`run_detailed_nuts` — the stage-b metric carries the DNUTS opens ahead of the
abstract overlaps). Trials run **full** (fast trials forced off for the pass:
`tighten_pulls` is WL-only, so a tighten-skipped trial's WL would be biased
against the move), which also makes every winning trial forward-restorable —
commits reuse ripup's snapshot-restore + recharge path. Skips user-pinned
bundles (inviolable), `hier.locked` bottom-up copies, and single-candidate
pools. Deterministic; **opt-in** — flows that do not call it are
byte-identical.

Measured on the rnr vehicles (end-of-flow placement, default accept —
endpoints preserved *exactly*, by construction):

| Vehicle | Endpoint (opens/ovl) | Realized WL | Cost |
|---|---|---|---|
| mix | 0/1 → 0/1 | 64893 → 59424 (**−8.4%**) | 44.8s (28 moves, 605 trials) |
| aligned | 30/0 → 30/0 | 75369 → 74058 (−1.7%) | 5.2s (8 moves) |
| bottomup | 0/1 → 0/1 | 73155 → 72991 (−0.2%) | 4.6s (5 moves) |
| topdown (healerless) | 175/16 → **84/2** | 67558 → 65480 (−3.1%) | 42.9s (25 moves) |

On a healerless flow the componentwise accept also lets opens/overlap
improvements through, so the pass doubles as a healer (the topdown row).

**Requires:** `run_planner` and at least `run_nuts` to have run first.

```buda
run_detailed_nuts
negotiate_congestion
ripup_reroute            # heal to the endpoint first
refine_selection         # then recover the realized-WL gap — endpoint frozen
check_design
```

**Composing with the healers on a stuck endpoint.** When residual
opens/overlaps survive the flow, refine's commits change the contention
geometry the healers stalled on — so a second healer round *after* refine can
crack what the first could not:

```buda
refine_selection         # componentwise pre-shrink (also heals on a healerless flow)
negotiate_congestion     # reprice the (now different) residual contention
ripup_reroute            # finish it
refine_selection         # claw back the WL the healers spent
check_design
```

The composition is safe **on the healers' own terms** — but note the terms:
negotiate/ripup accept only on strict improvement of their **lexicographic**
`(opens, overlaps)` metric (snapshot/restore otherwise), so opens never
increase and a round that finds nothing restores byte-identically (the
aligned case below). It is *not* componentwise-safe the way refine's own
accept is: an accepted negotiate iteration may trade overlaps **up** while
opens come down (84/2 → 32/6 in the measurement below), so if the following
`ripup_reroute` stalls before clearing them, the endpoint is lexicographically
better but mixed versus the post-refine state — the final `check_design` is
the arbiter, and re-running `ripup_reroute` with a larger `max_iter` is the
finisher. Refine's committed pins don't block the round — the healers build
their target sets from the failure sites and re-plan each target *unpinned*.
The other cost: when the healers do move things they spend WL (their metrics
don't track it), which is why the trailing second `refine_selection` belongs
in the recipe. Measured (2026-07-30):

- **topdown** (the plain healerless mix2 pipeline — the checked-in QoR
  vehicle `flow/rnr/mix2_topdown_refine.buda`): 175 opens/16 ovl →
  refine 84/2 (WL 67558→65480) → negotiate 32/6 → ripup **0/0**
  (WL 69894) → refine 0/0 at WL **69621**, final `check_design`
  **Success** — a flow that had never reached a clean endpoint, healed at
  ~+6% WL vs its broken state. (That trajectory is the vehicle's UNDECLARED
  configuration. On 2026-08-04 the flow took the `healersAhead` win — same
  0/0/0 endpoint at −5.2% detailed WL, healers finishing far sooner; comment
  the declaration out to reproduce the numbers above. See
  [ksegs_default_audit.md](../internal/ksegs_default_audit.md).)
- **aligned** (a 30-open residual that already resisted the full healer
  chain): the post-refine negotiate found nothing (its one trial measured
  worse and was restored), ripup and the second refine committed nothing —
  a byte-identical safe no-op costing ~15–20s of trials.

So: on a clean endpoint a second healer round no-ops immediately; on a
residual, a round that accepts nothing is a cheap byte-identical no-op, a
round that accepts strictly improves opens (with a possible transient
overlap trade for ripup to finish), and the best case is a full heal.

---

## Wirelength reporting

### `report_wirelength` (alias `report_wl`)

```
report_wirelength
```

Report routed wirelength per bundle + a design total, so a change to topology
generation / planning / NUTS can be compared for interconnect quality. Prints the
**abstract** bus-level WL (one length per placed bus segment — the metric topology
decisions move) once `run_nuts` has run, and adds the **detailed** bit-level WL
(every bit-wire) once `run_detailed_nuts` has run, each with a per-layer metal
breakdown. The full per-bundle table is captured to the flow log; the terminal
shows the totals.

**NDR shield metal** is reported on its own line, separately from signal WL:
shields emitted by a [non-default rule](ndr.md) are real metal the design pays
for, but they are not signal wirelength — keeping them apart is what lets a
governed design's quality metrics stay comparable with an ungoverned one.

**DOF envelope `[lo..hi]`.** Each bundle's routed WL is shown against the interval
its selected topology's *degrees of freedom* permit — computed from the segments'
slide ranges and min/max span (`ConnTopology`): a segment joined to a trunk at a
junction can slide over that trunk's `[perp_lo, perp_hi]`, and a flexible trunk
end contracts toward its coverage floor. Per bundle the table shows:

| Column | Meaning |
|---|---|
| `lo` | **Tightest** routing the DOF allow — the total span *minimized jointly* over the slide box by convex coordinate descent (respects the coupling that one trunk cannot sit at both ends of its slide at once) |
| `hi` | Loose **outer** upper bound (each segment independently stretched to its max span) |
| `WL` | Routed **topology-segment** wire (the portion the envelope brackets) |
| `jog` | NUTS-inserted dogleg-jog wire (extra metal *outside* the topology; excluded from `WL`) |
| `fill` | Where `WL` sits in `[lo..hi]` — 0 % at `lo`, so **lower = tighter**, i.e. more of the topology's slide freedom was spent shortening the route |

`lo` is the minimum for the topology as routed: when a dogleg was adopted the
envelope uses the plan's per-segment slide overrides (the windows NUTS placed
within) and excludes the jog's own span, so a doglegged bundle stays inside. A
rare residual multi-trunk slide-model gap can still put the routed WL just outside
the envelope (flagged `*`) — it is a routing metric, not a hard proof. `hi` stays
a loose outer bound.
The detailed section brackets the bit-level total against the **bit-scaled**
envelope (`[lo..hi] × bit count`); per-bit jogs/vias add wire beyond a flat
scale, so the detailed WL can ride high in — or slightly above — that scaled
envelope.

The same `[lo..hi]` envelope is shown per **candidate** in
[`dump_topologies`](verify_viz.md) (the `wl[lo..hi]` column), so candidates can be
compared by their routing freedom *before* planning — a wide envelope means the
candidate gives NUTS a lot of room to shorten.

Every total carries its **unplaced** count on the same line: WL sums only placed
wire, so a lower number that comes from dropped segments/bits is flagged rather
than silently rewarded.

---
