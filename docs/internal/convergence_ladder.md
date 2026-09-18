# BUDA Convergence Ladder — experiment plan

*Draft, 2026-09-13; the three open questions were decided the same day — see [Decisions](#decisions-2026-09-13).  The rendered version with pictures is the
artifact [BUDA Convergence Ladder](https://claude.ai/code/artifact/834de29e-3848-487a-b864-eda0a88c8cea);
this is the same text kept in the tree.  The pictures are not checked in —
`tools/render_design.py` regenerates them from the flows named below, and the
numbers under each are read from the same session as the picture.*

Experiments that show, abstractly but convincingly, that a hierarchical design
converges better with BUDA's bus plan handed down to the blocks — for the pins
on block boundaries *and* for the pre-routes the blocks commit on signal
tracks — without depending on a PDN, clock synthesis or a detailed router we
cannot run in the container.

## What "convergence" means here

The hierarchical failure mode is not routing.  It is **information that
crosses the block boundary too late**.  Blocks are implemented against a guess
about the top — which side the pins go on, how much of each layer the top will
need over the block, how many feedthrough tracks to leave — and the top
integrates against the block's abstract, a LEF `OBS` blob that says "something
is here" and nothing about which tracks.  The mismatch surfaces as re-spins.

So the quantities to measure are about the loop, not the route:

- **Rounds to clean.**  A round is: block implementation frozen → top
  integrates → verdict → constraints revised → block re-spun.
- **Effort per round.**  Instances re-routed; with solve-once-copy that is one
  template, without it every instance.
- **Reservation efficiency.**  Tracks the block reserved for the top against
  tracks the top used.  Under-reserve and you re-spin; over-reserve and the
  block pays for nothing.
- **Final QoR.**  Detailed wirelength, TOP-layer metal, unplaced bits — at the
  converged endpoint, not mid-loop.

None of that needs a PDN, a clock tree or a DRC deck.  It needs a controllable
*policy* for what the block is told, a vehicle whose size is a dial, and a
judge that is not BUDA.

### Two facts checked before designing, both load-bearing

**BUDA has no fixed pin today.**  `src/busterm.cpp:82–89`: a busterm's bbox is
always the whole component bbox; `PORT` vs `SPATIAL_CLUSTER` is a label, and
`add_cell_pin`'s `px py` is consumed by nothing in the routing frame.  Every
bus lands wherever BUDA likes on the face.  The conventional arm — "the block
team assigned the pins" — cannot be expressed yet.

**The top plan's per-instance, per-layer demand was not queryable** (at the
time of the draft).  `buda::query` answered overlaps, unplaced and counts;
`reserve_top_layers` and `set_cell_layer_share` take a number a human guessed.
Handing a budget *down* is what "BUDA in the loop" means, and that primitive
did not exist.  **Built (item 3 below, 2026-09-15):** `report_layer_demand`
and `buda::query demand` read, off the routed result, the signal tracks the
rest of the design places over each instance on each layer against the
tracks it has — the number item 4 hands down as the complement share.

Both are small to build, and both *are* the thesis, so building them is the
experiment rather than scope creep.

## The vehicles, as they route today

Both are generated, so size is a dial, and both already route clean top-down
at the sizes shown.  Regenerate the pictures with:

```
tools/render_design.py flow/soc_mid.buda --out /tmp/soc --title "soc.tcl 32"
tools/tcl2buda.py flow/tcl/tpu.tcl 16 -o /tmp/tpu_16.buda
tools/render_design.py /tmp/tpu_16.buda --out /tmp/tpu --title "tpu.tcl 16"
```

| vehicle | die | leaves / components / levels | bundles | bit-wires | detailed WL | endpoint |
|---|---|---|---|---|---|---|
| `soc.tcl 32` (`flow/soc_mid.buda`) | 12528 × 7936 | 843 / 1197 / 4 | 1235 | 35 096 | 7 461 648 | 0 ovl / 0 unpl, two audits clean |
| `tpu.tcl 16` (256 PEs) | 3496 × 2648 | 336 / 352 / 2 | 560 | 11 008 | 738 816 | 0 ovl / 0 unpl, two audits clean, 2.9 s |

`soc.tcl 32` is the deep, diverse one: thirty-two quads of two clusters, each
cluster a core, two L1s and a router, plus an L2 and an IO subsystem at
different depths — eleven leaf cell types repeating anywhere from once to
twenty times, a leaf sitting two, three or four levels down depending on where
it lives.  In its NUTS picture the cross-quad backbones on M6/M7 and the
per-cluster M4/M5 fabric are the two tiers the experiments pull apart.

`tpu.tcl 16` is the mesh: one `pe_cell` tiled inside sixteen `row_cell`s,
weight buffers below, accumulators above, feeders on the left.  Every instance
is identical, which is exactly what the bottom-up family keys on and what makes
the ladder easiest to read here.

## The spine: an information ladder

One vehicle, one design, five rungs of *what the block is told before it is
implemented*.  Each rung maps onto a knob that exists or is one primitive
away.  Rungs 0–2 are the pins-on-boundaries half; rungs 3–4 are the
pre-routes-on-signal-tracks half.  Rungs 3 and 4 are the ones a conventional
flow structurally cannot reach at implementation time, so that is where the
gap should open — and the ladder shows *which* information closes it, which is
a stronger claim than "BUDA is better".

**In-house, the ladder is measured on the pre-routes axis only — rung 0 → 3 →
4.**  Its conventional arm needs no competitor's tool: "blind bottom-up plus a
LEF abstract" *is* the industry's information flow, not something we imitated.
The pins axis (rungs 1–2) has no credible in-house baseline (Decisions, Q2) and
is evaluated with a partner instead.

| rung | the block knows | conventional analogue | BUDA knob |
|---|---|---|---|
| 0 | nothing | blind bottom-up; LEF `OBS` abstract handed up | `set_bottom_up`, no caps; the top sees whole-layer keepouts over the footprint |
| 1 | pin **sides** | a port-direction convention (inputs left, outputs right) | *partner evaluation* (see Decisions): no in-house baseline is credible |
| 2 | pin **positions** | a top-level net-based global router + track assignment, or a team by hand | *partner evaluation*; the fixed-pin primitive lets BUDA consume their pins |
| 3 | + a layer **budget** | the "don't use M6/M7 in blocks" memo | `reserve_top_layers` / `set_cell_layer_cap` — exists |
| 4 | + exact **track reservations** derived from the top's own plan | does not exist conventionally | derived `set_cell_layer_share` per cell + `hier.locked` copies as track-level keepouts — *derivation to build* |

## Candidate experiments

Ordered by how much they need built, not by how much they prove.

### E4 — Diagnosed vs blind iteration *(data exists)*

**Hypothesis.**  A loop whose failure is *named* closes in one step; a loop
that only sees a count has to sweep, and on a non-monotone response a sweep is
the only valid search.

**Arms.**  Already measured on `soc.tcl 16 -bottomup`.  Blind: the gap sweep
16 ✗ · 24 ok · 32 ✗ · 48 ✗ · 96 ok — five runs, and "increase until clean"
would have stepped from a clean 24 onto a dirty 32.  Diagnosed: the
doomed-seat census named `hb-2 seg 0` (8 bits of `pc_0`) and
`set_max_bundle_bits 4 for pc_` cleared the stranding in one run, at NQ = 16
and again at NQ = 32.

**Metric.**  Runs to a clean endpoint; whether the remedy touched the cause
(the seat) or the symptom (the channel).

**Judge.**  The existing `check_design` verdicts — acceptable here because both
arms are judged by the same audit and the claim is about the *loop*, not the
route.

**Status.**  Nothing to build.  **Written up: [convergence_e4.md](convergence_e4.md)** — a
re-reading of [`flow/tcl/soc.md`](../../flow/tcl/soc.md).

### E2 — Abstract precision: blob vs tracks *(nothing new)*

**Hypothesis.**  The top loses routability in proportion to how coarsely it
sees the block's committed metal.  Same frozen block routing, three views of
it.

**Arms.**  (a) whole-footprint `OBS` per used layer — what a LEF abstract says;
(b) the per-layer bounding box of the actual routing; (c) exact per-track
keepouts — the `hier.locked` copies BUDA already produces.  BUDA can generate
(a) and (b) from (c) itself, so all three arms are one flow with a switch.

**Metric.**  Top-level unplaced, overlaps and detailed WL against the number of
layers the block was allowed, on the NQ / N dial.

**Judge.**  Independent geometric audit (below).

**Status.**  Pure geometry, no policy loop — the cleanest single plot in the
set.  **Run and written up: [convergence_e2.md](convergence_e2.md)** — four
arms (none / blob / bbox / exact keepouts) at NQ = 2, 4, 8, healerless and
healed: precision orders the outcome monotonically at NQ = 2, 4, 8, SWAPS bbox/exact at NQ = 16 healerless (195 against 87 stranded) and swaps back healed (12 against 18) — the plain plan is worse under finer keepouts, the healed one better, the cause left to the census rather than guessed; the blob
reserves ~60× the layer-area the block's routing occupies and strands an
order of magnitude more bits than the bbox, and the tables are judged by
`check_design` until the independent audit (item 2 below) exists.

### E1 — Blind bottom-up vs derived budget

**Hypothesis.**  Handing a block the *complement* of the top's measured demand
converges in one informed round; handing it nothing converges in R blind
rounds, with R growing with instance count.

**Arms.**  Conventional: block routes freely, is frozen, top routes against
it; on failure the policy removes the block's highest layer and re-spins every
instance.  BUDA: plan top-down first, read per-instance per-layer demand, hand
each cell the complement as a share, solve the template once, copy, route the
top.

**Metric.**  Rounds, effort (instances re-routed), top-level unplaced,
reservation efficiency (reserved ÷ used).

**Judge.**  Independent geometric audit.

**Strawman defence.**  The conventional policy's own parameter (how much to
give up per round) is swept to its best before comparing.  Half the numbers
exist: the chip vehicle's §12 study (a cap doubles top-level M6/M7 metal) and
the mix2 50 %-lease boundary.

**Honest risk.**  BUDA's own bottom-up is **dirty at NQ ≥ 16** at the default
channel — the `pc_0` seat.  "One round" is therefore a measurement, not a
given; if BUDA needs a second round it is a diagnosed one (E4), and the
write-up says so.

**Needs.**  ~~The demand query~~ (built: `buda::query demand`, item 3); ~~`derive_cell_layer_shares`~~ (built, item 4); ~~the loop driver~~ (built: `flow/tcl/converge.tcl`, item 5).

**Status.**  **Run and written up: [convergence_e1.md](convergence_e1.md)**
(2026-09-16) — and the claim is **refuted for the primitive as built**, with
the reason measured.  The blind policy (`reserve_top_layers`, its step swept
to its best) is clean in **two rounds** at NQ = 2, 4 and 16 with healers
off, reserving **4.5–8×** the tracks the top used, and never clean at NQ = 8
(its only knob overshoots: `reserve 3` strands 1,444 bits of the blocks'
own buses).  The derived share reaches a clean endpoint in **no round** at
any size and does not improve on the round it was derived from, because a
`set_cell_layer_share` is UNIFORM — the last slots of every period, over
the whole instance — while the top's demand is POSITIONAL, and the block's
own 32-bit buses already fill their seats to 89–100 % on the layers the top
wants.  The derivation now floors the share by the cell's own need (the
first run without it stranded 410 bits at NQ = 2 against the blind round's
8) and says, per cell and layer, where a share cannot express the
complement.  On the mesh control, where the top's demand IS uniform, the
derived share reserves 1.12–1.19× what the top uses at no cost to the
route.  With the vehicle's own healing the healers do the work: the blind round 1 is clean with no reservation at all at NQ = 2, 4 and 8, and at NQ = 16 the top-down-derived round and the blind `reserve 1` round clear the E4 seat in the same session count (8.5× against 9.8× reservation, +1.3 % wire), while the blind-round-derived arm stays dirty on it.  What rung 4 wants is a positional reservation —
E5's corridor — which moves ahead in the build order, and E1 is re-run
against it.

### E3 — Pin assignment at scale *(deferred to a partner evaluation)*

**Decision (Q2).**  Flyline-HPWL is easy to beat, and the real conventional
method — a top-level net-based global router with track assignment, or a
large team doing it by hand — is not something we can build in-house without
building it badly.  Any baseline of ours is a strawman by construction, so
this experiment is NOT run in-house.  It is re-scoped as: route against a
partner's own pin assignment (alpha/beta or in-house evaluation) and compare
what BUDA chooses, which needs no baseline of ours.  What follows is the
original design, kept for that evaluation.

**Hypothesis.**  Pins chosen for wirelength pile onto one face and exceed that
face's track supply on the *sum* at a pin; pins chosen with the bus plan land
where the tracks are.  The face rule `soc.tcl` paid for three times, read as
an experiment.

**Arms.**  Rung 1 (sides) and rung 2 (flyline positions) against BUDA-chosen
taps, on the SoC dial.

**Metric.**  Stranded bits from supply-doomed seats; rounds where the
conventional remedy is "move the pins / widen the face".

**Judge.**  Independent geometric audit; the doomed-seat arithmetic (bits vs
tracks in a window) is ~30 lines and is re-implemented in the auditor rather
than trusted from the engine.

**Strawman defence.**  The flyline policy gets a spread parameter (how far pins
may be spaced along the face) and is swept to its best first.

**Needs.**  The fixed-pin primitive — now for a different reason: it is what
lets BUDA *consume* an externally assigned pin placement at all, the
interoperability prerequisite for the partner evaluation, not a strawman arm.

### E5 — Feedthrough reservation

**Hypothesis.**  A block that reserves feedthrough tracks by guess either
under-reserves (re-spin) or over-reserves (pays); a block handed the top's
exact crossing demand reserves exactly.

**Arms.**  Conventional: F uniformly spaced feedthrough tracks per layer, F
swept.  BUDA: top plan → which bundles cross which block on which layer with
how many bits → `set_feedthru` plus positioned reservations
(`add_grid_override` can stand in for a corridor).

**Metric.**  Reservation efficiency and rounds.

**Judge.**  Independent geometric audit.

**Needs.**  ~~A positioned corridor~~ (built: `set_cell_layer_reserve`, item
6, with its `uniform F` form as the conventional arm).

**Status.**  **Run and written up: [convergence_e5.md](convergence_e5.md)**
(2026-09-17).  The guessed reservation (F evenly spaced tracks on every
TOP layer of every marked cell, F swept over its whole legal range — 4, 8;
16 exceeds the smallest cell's supply) is **worse than reserving nothing at
every size**: 368–2,907 stranded bits at F = 4 against the blind round's
8–336, because four tracks in every 35-track seat push the cores' 32-bit
bus onto LOW layers that cannot host it.  The derived positional
reservation reaches a **clean endpoint with healers off at NQ = 2, 8 and
16** (one, one and two informed rounds) where E1's share reached none and
the blind band needed two rounds and never cleaned NQ = 8, reserving
3.2–8.1× the top's used tracks (the union over a template's instances —
exact, 1.00×, on the mesh control).  With the vehicle's own healing the derived arm is clean at every
size in one informed round (in one of its two arms) and its NQ = 16 route
is 11–14 % less wire than the blind band's — the first size at which the
informed arm beats the blind one on the route itself — while the guess is
healed clean at NQ = 2–8 at 10–30× the blind round's healing time and
never at NQ = 16.  Running it also closed an enforcement gap the audit
found (an instance solved in the global DNUTS run under `on_mismatch
independent` saw no reservation; it now carries the tracks as blocked
tracks).

The write-up first read the top as landing on the reserved tracks only
**6–11 %** of the time and blamed the loop's missing fixpoint on that.
**That number was a mis-measurement** — the scratch script divided the
hits by the instance's *supply* rather than by the top's own tracks over
it — and is corrected in the write-up: read right, the unsteered top lands
on a derived reservation **0.65–0.97** of the time on the SoC (the
reservation is the union over a template's instances and covers half the
supply) and **0.00** on the mesh, where the top's seats shift by a track
phase between rounds.  **Build item 6b, the top-side half, was built
against the wrong number and measured** (2026-09-17, `set_reserve_steer`,
off by default): a bus crossing a governed instance is seated on the
reserved tracks by NUTS and lands its bits there first in DetailedNUTS,
gated on the corridor being able to host it.  On the mesh it takes the hit
rate from 0.00 to 1.00 at byte-identical wire; on the SoC it lifts
0.65–0.97 to 0.70–1.00 and makes the informed rounds *dirtier* at NQ ≥ 4
(E5's clean healerless NQ = 8 top-down round goes to 80 unplaced, the
NQ = 16 top-down round from 170 to 390 at +11 % wire), because every
crossing bus is pulled into the union corridor and the packing pays for
tracks the top would have hit anyway — so it ships as a lever, not a
default.  What the informed loop lacks is **plan stability**: on the
recorded NQ = 2 rounds, 4 of the 13 top-level bundles keep their topology
between the blind round and the informed one and none keeps its seat, so
the corridor a derivation names is the previous top's and the next top
plans elsewhere.  A track preference cannot supply that; a pin can, and
**build item 6c, the top's plan handed down, is BUILT and measured**
(2026-09-17, `derive_top_plan` → `pin_plan`, `converge.tcl -handdown`):
the measurement round's globally planned selections, layers and seats
come down with the reservation and the informed round routes the blocks
under the SAME top.  Every pin applies and every seat is honoured, and the
loop has a **fixpoint** — the budget AND the plan a round derives
reproduce the ones it ran under within one to three informed rounds on the
SoC and in the first on the mesh (where the reservation is then exactly the top's use, 1.00×)
— where E5's free re-plan reproduced nothing.  Healers off, the fixpoint
is exactly as clean as the top handed down (the measurement round's own
overlaps ride along); handed a HEALED blind round, the bottom-up informed
round is clean **without healers** in one round at NQ = 2, 4 and 8 (E5
never cleaned NQ = 4, healed or not) and 8 bits short at NQ = 16, healed
clean there.  The top-down source is weaker (2–35 overlaps at round 1,
healed clean at NQ ≥ 4) and at NQ = 2 stays at E5's dirty fixpoint — the
union reservation covering the core's own 32-bit seat — which is the
reservation's limit, not the plan's.  **6d, the derivation yielding the
block its seat, is BUILT and measured** (2026-09-18, `derive_cell_layer_reserves
yield`, `converge.tcl -yield`): where the corridor leaves a block's seat
no run of consecutive free tracks its bus needs (the run, not the count —
an abstract seat is one rectangle, and even one corridor track left in
the core's 36-track window sent its local planner to a dead LOW layer),
the block keeps its current seat and the top takes the loss, a nested
template's inherited corridor included (the core has no line of its own).
The NQ = 2 top-down round strands NOTHING in one round where 6c held the
dirty fixpoint at 360 bits, at the price of one overlap no healer clears;
SIX of the eight healed arms are clean, the exceptions both top-down —
NQ = 2 at one overlap and NQ = 16 at two.  It costs 8 bits at NQ = 2
bottom-up and an overlap or two at NQ = 4/8, and gains 8 at NQ = 16
top-down healerless, so it is a lever, not a default.  (Six, not seven:
the healed table was re-measured after a second review finding and NQ = 16
top-down moved from clean to two overlaps — the only one of its nine rows
that moved.)

## The judge must not be BUDA

If DetailedNUTS routes both arms and `check_design` scores them, a skeptic
says the referee wears our jersey.  The proposal is
`tools/independent_audit.py`: ~150 lines over the persisted BDB tables —
`net_segment`, `net_via`, keepouts, track patterns — that checks from geometry
alone, importing no engine:

- every bit lies on a `SIGNAL` slot of its layer's pattern;
- no two nets share a track over an overlapping span;
- no bit lies over a keepout on its layer;
- every net is electrically connected from driver to every receiver through
  its vias.

It pays forward: the second-PDK work will need an audit that is not the
router, and the same file serves.

## What is deliberately not claimed

Timing closure.  DRC-clean at a real detailed router's rule deck.  Anything
against a named commercial tool.  The claim is narrower and defensible: *when
the bus plan is handed down instead of guessed, a hierarchical design
converges in one informed round instead of R blind ones, the block's
reservations are exact instead of padded, and the gap grows with instance
count.*  Every table on the size dial; every baseline tuned to its best first;
every "one round" measured rather than assumed.

## What gets built, in order

1. **E4 write-up, E2 run** — nothing new, one clean plot each.  **Done**: [convergence_e4.md](convergence_e4.md), [convergence_e2.md](convergence_e2.md).
2. **`tools/independent_audit.py`** — the judge, before any A/B table is
   written (Q3: it judges every table; one OpenROAD `read_guides` witness on
   one row only if the audience needs it).
3. **Per-instance per-layer demand query** — read off the top-down plan;
   exposed through `buda::query` so a Tcl driver can branch on it.  **Done**
   (2026-09-15): [`report_layer_demand`](../script_reference/nuts.md#layer-demand-reporting)
   / `buda::query demand ?inst? ?layer?` — `{inst cell layer bits used supply
   pct}` per placed instance and patterned layer, `used` the signal tracks
   under the UNION of the foreign metal (a share thins uniformly, so a track
   the top takes anywhere over the instance is one the cell must leave),
   `supply` the count the collective lease is sized from, ownership by frame
   instance (own = the instance or its subtree).  Reads DNUTS bit tracks when
   they exist, the abstract placement before (`bits` or `bits+1` per crossing
   bus, by phase — conservative).  -1 before `run_nuts`.
4. **`derive_cell_layer_shares`** — the complement of that demand, per cell,
   as `set_cell_layer_share` lines.  This is rung 4.  **Done** (2026-09-16):
   [`derive_cell_layer_shares [apply] [file <path>] [cells ...]`](../script_reference/nuts.md#derive_cell_layer_shares-apply-file-path-cells-ab)
   — `100 − worst pct` over the cell's instances, floored; scope = named
   cells, else the `set_bottom_up` marks, else every cell owning a
   cell-local bundle; a zero-slot complement is skipped and said.  It
   REPORTS the budget-vs-reservation gap instead of assuming it away: a
   share thins the cell's pattern to its first `floor(s × n_signal)`
   slots while the top's demand sits on specific tracks, and `collide` is
   the top's tracks inside the kept slots (6 of 8 on the two-instance
   test design).  Whether that gap strands bits at scale is E1's
   measurement, which is why the count is on every line.
5. **`flow/tcl/converge.tcl`** — the loop driver: runs the conventional policy
   as a scripted round, counts, and runs the BUDA one-pass, on soc first
   (Q1) with tpu as the control, across the dial.  **Done** (2026-09-16):
   three arms as loops (blind `reserve_top_layers` rounds; the top-down
   derived budget; the blind-round-derived budget — the diagnosed loop),
   every round an ordinary vehicle session through five shared hooks in
   `converge_lib.tcl` (`-reserve`/`-shares`/`-derive`/`-noheal`/`-report`),
   reservation efficiency read off the demand rows.
6. ~~E1~~ **run** ([convergence_e1.md](convergence_e1.md): the share
   primitive refuted, the reason measured) → ~~the positional reservation~~
   **built** (2026-09-17): [`set_cell_layer_reserve`](../BDB_REFERENCE.md#set_cell_layer_reserve)
   — per cell and layer the cell-local tracks the cell's own routing leaves
   free, enforced as keepouts on the cell-local template solve and the
   reference DNUTS view (the parent keeps the full grid) — with
   [`derive_cell_layer_reserves`](../script_reference/nuts.md#derive_cell_layer_reserves-apply-file-path-cells-ab)
   handing the top's placed tracks down as those lines, `buda::query
   reserves`, the `LAYER_RESERVE` audit, and `converge.tcl -primitive
   reserve` driving E1 with it; a nested template INHERITS an ancestor's
   corridor (projected into its own frame, unioned over its occurrences),
   which the SoC forced: the top's M5 tracks over a cluster sit where the
   nested core's 32-bit bus seats, and a core solved without them lost all
   32 bits at DNUTS.  On the two-instance design the block's bus moves off
   the eight reserved tracks and the top uses all eight; on the SoC at
   NQ=2 healerless every reservation is honoured and the design is DIRTY
   (360 unplaced: the cores' regf→alu bus, off its reserved M5 seat onto
   LOW layers that cannot host it) — the first measurement, not the
   verdict → ~~E1 re-run against it → E5~~ **E5 run** (2026-09-17,
   [convergence_e5.md](convergence_e5.md); its `td`/`bu` rows ARE E1
   re-run against the positional primitive): clean healerless at three of
   four sizes where the share cleaned none, exact on the mesh; the
   unsteered top uses the reserved tracks 0.65–0.97 of the time on the SoC
   and 0.00 on the mesh (the write-up's first reading, 6–11 %, was a
   mis-measurement, corrected in place).
   ~~**6b — the top-side half of the reservation**~~ **BUILT and measured**
   (2026-09-17, `set_reserve_steer`, off by default): NUTS seats a
   crossing bus on a governed instance's reserved tracks and DetailedNUTS
   lands its bits there first, where the corridor can host the bus.  The
   mesh: 0.00 → 1.00 at no cost.  The SoC: 0.65–0.97 → 0.70–1.00 with the
   informed rounds dirtier at NQ ≥ 4 — the hit rate was never what the
   loop lacked.  A lever, not a default; the planner's own term (a
   negative cost on reserved bands) was not built, since a preference
   cannot restore a plan the templates' new charges have moved.
   ~~**6c — the top's plan handed down with the reservation**~~ **BUILT
   and measured** (2026-09-17, `derive_top_plan` → `pin_plan`,
   `converge.tcl -handdown`; [convergence_e5.md](convergence_e5.md), "The
   plan handed down"): the derivation round's globally planned
   selections (by candidate uid and type spec), forced layers and seats
   (width-wide slide windows — a seat pin to NUTS, with the bit stage
   keeping the segment's natural window) come down with the reservation.
   Every pin applies, every seat is honoured, and the loop has a fixpoint
   at every size on both vehicles (exact and 1.00× on the mesh) where the
   free re-plan never did; healers off the fixpoint is as clean as the
   top handed down, and from a healed blind round the informed round is
   clean healerless in one round at NQ ≤ 8 (8 bits short at NQ = 16).
   Found on the way and fixed: a healer move off a pinned shape carried
   its forced layers (LAYER_DIR behind a clean metric), and a top-down
   measurement round must be ALIGNED (a seat is geometry; the mesh's rows
   move by a phase).  What remained was the reservation's own limit (the
   `td` arm at NQ = 2: a union covering the core's own seat, E5's dirty
   fixpoint) — a derivation-policy question, not a new primitive, and
   ~~**6d — the derivation yields the block its seat**~~ **BUILT and
   measured** (2026-09-18, `derive_cell_layer_reserves yield`,
   `converge.tcl -yield`; [convergence_e5.md](convergence_e5.md), "The
   derivation yields the block its seat"): where the corridor leaves a
   block's seat no run of consecutive free tracks its bus needs — the
   test is the run and not the count, since an abstract seat is one
   rectangle and even one corridor track left in the core's window sent
   its local planner to a dead LOW layer — the block keeps its current
   seat and the top takes the loss, nested templates' INHERITED corridors
   included (the core has no line of its own; its 360 bits were the
   cluster's corridor).  The NQ = 2 top-down informed round strands
   NOTHING in one round where 6c held the dirty fixpoint at 0/360/360
   healerless and 0/128 healed, at the price of one overlap that no
   healer clears — so the stranding goes, "clean" does not — and seven
   of the eight healed arms are clean.  Elsewhere the trade is small and
   runs both ways: 8 bits gained at NQ = 16 top-down, parity at NQ = 16
   bottom-up, 8 bits lost at NQ = 2 bottom-up and an overlap or two at
   NQ = 4/8.  So it ships as a lever, not a default.  (These are the
   SECOND measurement: the first ran a yield that tested each seat
   against half of what fragments it, and its headline cost — 264
   stranded bits at NQ = 16 bottom-up — was that defect, not the policy,
   which measures 0 overlaps and the no-yield baseline's 40 bits.)  What
   would take both is a driver policy that yields AND unpins exactly the
   top buses whose seats were yielded.
7. **Fixed-pin primitive** — a busterm restricted to a face, then to a window
   on a face — as the interoperability piece for the partner evaluation (E3),
   last, since nothing in-house depends on it.

## Decisions (2026-09-13)

The three questions the draft ended on, answered by the owner:

- **Q1 — which vehicle leads: `soc.tcl`**, as leaned; `tpu.tcl` is the control
  where the effect should be purest.
- **Q2 — the rung-2 baseline: there is no credible one to build in-house.**
  Flyline-HPWL is easy to beat, and so is a top-level net-based global router
  plus track assignment; most design houses do this by hand with a large
  team.  A baseline we write is a strawman by construction.  So the pins half
  of the claim is not argued from in-house experiments at all: it goes to an
  alpha/beta partner or an in-house evaluation on a real flow, where BUDA
  routes against *their* pin assignment.  In-house, the ladder is measured on
  the pre-routes axis (rung 0 → 3 → 4), whose conventional arm is the
  industry's own information flow rather than an imitation of anyone's tool.
  The fixed-pin primitive survives with a different purpose — consuming a
  partner's pins — and moves to the end of the build order.
- **Q3 — the judge: the independent Python geometric audit for every table**,
  as leaned, with one external OpenROAD `read_guides` witness on one row only
  if the audience needs it.
