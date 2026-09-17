# E5 — Feedthrough reservation: guessed against derived

*Experiment E5 of the [Convergence Ladder](convergence_ladder.md), run and
written up 2026-09-17.  Driver: [`flow/tcl/converge.tcl`](../../flow/tcl/converge.tcl)
(`-primitive reserve -arms uniform,td,bu`) over the vehicle hooks in
[`converge_lib.tcl`](../../flow/tcl/converge_lib.tcl); construction guarded by
`test/tests/test_converge_driver.py` (mid tier, NQ = 2) and
`test/tests/test_cell_layer_reserve.py`.  No picture: the finding is about
rounds, reservations and where the top's metal lands, not about a route.*

## The claim being tested

A block that reserves feedthrough tracks **by guess** either under-reserves
(re-spin) or over-reserves (pays); a block handed the top's **exact crossing
demand** reserves exactly.  The metric is rounds to a clean endpoint, the
reservation's size against the tracks the top actually used, and — new for
this experiment — the reservation's **hit rate**: of the top's tracks over a
governed instance, how many landed on the tracks the block reserved.

## The result, in one paragraph

**Half the claim holds and half does not, and the half that fails is the
half the ladder wrote as obvious.**  The guessed reservation never reaches a
clean endpoint at any size in its whole physical range (F = 4 and 8 per TOP
layer per cell; F = 16 exceeds the smallest cell's 14 tracks and is
refused), and it is worse than reserving nothing at every size — 368 to
2,907 stranded bits at F = 4 against the blind round's 8 to 336 — because
four evenly spaced tracks on every TOP layer of every cell fall inside the
cores' 32-bit seats and push those buses onto LOW layers that cannot host
them.  The derived positional reservation (`derive_cell_layer_reserves` →
`set_cell_layer_reserve`, the primitive E1 asked for) reaches a **clean
endpoint with healers off at three of four sizes** — NQ = 2 in one informed
round, NQ = 8 in one, NQ = 16 in two — where E1's share reached none, and
where the blind band policy needed two rounds and never converged at
NQ = 8.  It reserves **3.2–8.1×** the tracks the top used, the same order
as the blind policy's 4.5–8×, because a template is solved once and the
reservation is the *union* over its instances.  But the second half is
refuted by a number nobody had asked for: the top lands on the reserved
tracks **6–11 % of the time**.  Nothing in the flow steers the top onto the
tracks the block left free, so the reservation does not work as a corridor
the top uses; it works by *displacing* the block's own buses off the region
the top wants, and it is exactly that displacement which strands the cores'
32-bit bus wherever the derived tracks cover its seat (NQ = 2 top-down, 360
bits; every second informed round, 757 / 1,444 / 2,864).  The informed loop
therefore has **no fixpoint** — each round re-derives from a top that moved
— and the ladder's rung 4 needs the top-side half of the primitive: a
reservation the top's planner *prefers*.

## Construction

**Vehicle.**  `soc.tcl` at NQ = 2, 4, 8, 16 (4 / 8 / 16 / 32 clusters; 63 /
127 / 255 / 511 leaf instances; 95 / 183 / 359 / 711 buses), the ladder's
lead vehicle; `tpu.tcl` at N = 8, 16 as the control.  Every session is one
run of the vehicle through the E1 hooks plus one new one (`-uniform F`);
the report each session leaves now carries, per governed (instance, layer),
the tracks reserved, how many of them the top's placed metal uses, and how
many carry the cell's own metal.

**The three arms**, each a loop until clean or out of moves:

| arm | round 1 | round k | what it is |
|---|---|---|---|
| **uniform** | every `set_bottom_up` cell reserves **F evenly spaced tracks on every TOP layer** (`set_cell_layer_reserve * TOP uniform F`, F = 4), solves once, copies; the top routes against it | F doubles (8, 16, …) until clean, past `-fmax`, or past the smallest cell's supply — the engine refuses an F a cell cannot host, and the driver records that as the sweep's end | the conventional feedthrough reservation: a guess with no plan behind it, said through the SAME primitive so one enforcement and one audit read both arms |
| **td** | plan the whole design **top-down** once; read the tracks the top placed over every instance; write every cell the UNION over its instances, in the cell's frame | solve every template once under the lines, copy, route the top; re-derive from the result | rung 4 with the positional primitive |
| **bu** | the blind round 1 **is** the measurement (E1's `blind` round: templates solved once, copied, frozen; the top routes against them) | the informed round: templates under the lines derived from round 1, then re-derive | the diagnosed loop, positional |

**What a positional reservation says** (E1's "what a share can and cannot
say", for the primitive that replaced it).  `set_cell_layer_reserve C L
p1,p2,…` names cell-local track centres on layer `L` that `C`'s own
interconnect leaves free: each becomes a keepout on the cell-local floorplan
(the local planner's capacity and the local NUTS seats avoid it) and on the
grid clone the reference DNUTS solve runs on; a nested template inherits its
ancestors' corridors projected into its own frame.  The parent keeps the
full grid — a reservation is room *for* the top, never a keepout against
it.  The derivation is the union of the top's placed tracks over every
occurrence of the cell (a template is solved once), which the derivation
prices as `used/inst`, and it reports `seat_hit` — reserved tracks inside
the cell's own worst seat — *without* acting on it: E1's floor kept a share
off the block's own seat; the positional derivation deliberately does not,
because the primitive exists so that the block moves its bus and whether
the local solve finds the room is the measurement.

**The `uniform` form.**  `uniform F` reserves the real signal tracks at the
(k + ½)/F points of the cell's extent (centred, so `uniform 1` is the
middle track), read over the reference occurrence — F tracks a bit can sit
on, not a spacing.  `*` names the marked cells, `TOP` the stack's TOP
layers (M5, M6, M7 on the SoC), and every (cell, layer) pair is computed
before any is stored, so `io_cell`'s 14 M5 tracks bound the whole sweep at
F = 8.

**Reservation efficiency** as in E1: *reserved* = tracks the policy takes
from the block over every governed instance, *used* = tracks the top placed
over that instance, inside or outside the reservation; a governed instance
is one the cell's reservation covers (a 90°-rotated occurrence is not
governed and has no row).  **Hit rate**, new here: the top's tracks that
fell *inside* the reservation over *used*, read off the extended report
(`governed INST CELL LAYER N USED OWN`; the healerless rows below were
re-run under the extended report for this number alone, verdicts
identical).

**What running it fixed.**  The audit reported the cluster's own metal on a
reserved track at the SoC's *misaligned* clusters (three of the eight under a
mirrored quad at NQ = 2): an instance solved in the global DNUTS run under
`check_template_tracks on_mismatch independent` never saw the reservation's
keepouts, which live on the reference solve's grid clone.  Such an instance
now carries the reserved tracks, folded into its frame, as its bundles'
blocked tracks (`BundleHierMeta.blocked_tracks` → `BusSegment.blocked_tracks`,
so the C++ trial sweep reads the same list), and every table here is from
the fixed engine (the healerless numbers moved by nothing but a few units
of wirelength in two rounds — the bits took the neighbouring track).  The
healed rounds then found the second door of the same gap: `ripup_reroute`'s
RELEASE pass withdraws an instance from the uniform copy and solves it
individually — the released cores' own metal sat on their reserved tracks
(2 of 4 at every core, NQ = 2 and 4) — so releasing stamps the same list,
through the one helper both doors call, and a rejected release restores it
with the lock.  The
abstract-stage audit also read a bus seated flush against a reserved track
as touching it (6 of 48 on `io_blk_cell`, 0 once the bits were placed); it
now reports that reading as the estimate it is and reserves VIOLATED for
placed bits.

**Judge.**  `check_design`, the same audit on every arm — the ladder's
independent geometric audit (build item 2) still does not exist, as the
E1 and E2 write-ups also say.  A parenthesised wirelength excludes stranded
bits and is not comparable to a complete route.

## Results — healers off (the plain pipeline's verdict)

`ovl/unpl/viol` = NUTS overlaps / DetailedNUTS unplaced bits / audit
violations.  `classes` = template classes solved that round (every session
marks the same 18 cells).  `reserved ÷ used` is read off the round's own
governed rows.  The blind round 1 is the `bu` arm's measurement and is
shown once.

| size | arm | round | policy | final ovl/unpl/viol | detailed WL | reserved | used | reserved ÷ used | hit rate | s |
|---|---|---|---|---|---|---|---|---|---|---|
| 2 | blind | 1 | reserve 0 | 1/8/8 | (584,581) | — | — | — | — | 1.4 |
| 2 | uniform | 1 | uniform 4 | 2/368/368 | (557,357) | 1,044 | 2,245 | 0.47 | — | 1.8 |
| 2 | uniform | 2 | uniform 8 | 6/136/136 | (560,705) | 2,088 | 2,221 | 0.94 | — | 1.8 |
| 2 | td | 0 | top-down | 0/0/0 | 525,144 | — | — | — | — | 1.4 |
| 2 | td | 1 | lines r0 | 0/360/360 | (538,483) | 1,324 | 805 | 1.64 | — | 1.9 |
| 2 | td | 2 | lines r1 | 1/376/376 | (566,769) | 2,144 | 1,019 | 2.10 | — | 2.0 |
| 2 | bu | 1 | lines r0 | **0/0/0** | 536,005 | 7,234 | 2,269 | 3.19 | 0.09 | 2.3 |
| 4 | blind | 1 | reserve 0 | 5/117/117 | (980,908) | — | — | — | — | 2.5 |
| 4 | uniform | 1 | uniform 4 | 2/741/741 | (1,064,732) | 1,932 | 5,012 | 0.39 | — | 3.4 |
| 4 | uniform | 2 | uniform 8 | 14/269/269 | (1,039,315) | 3,864 | 5,247 | 0.74 | — | 3.4 |
| 4 | td | 0 | top-down | 1/16/16 | (993,477) | — | — | — | — | 2.8 |
| 4 | td | 1 | lines r0 | 1/24/24 | (982,122) | 7,352 | 1,959 | 3.75 | — | 4.0 |
| 4 | td | 2 | lines r1 | 0/37/37 | (957,112) | 7,888 | 1,809 | 4.36 | — | 4.0 |
| 4 | bu | 1 | lines r0 | 0/45/45 | (942,054) | 22,475 | 4,738 | 4.74 | 0.10 | 4.5 |
| 4 | bu | 2 | lines r1 | 0/757/757 | (962,888) | 21,668 | 4,793 | 4.52 | — | 4.5 |
| 8 | blind | 1 | reserve 0 | 6/93/93 | (2,012,407) | — | — | — | — | 5.4 |
| 8 | uniform | 1 | uniform 4 | 1/1454/1454 | (2,118,394) | 3,708 | 8,967 | 0.41 | — | 7.3 |
| 8 | uniform | 2 | uniform 8 | 30/560/560 | (2,025,227) | 7,416 | 9,061 | 0.82 | — | 7.1 |
| 8 | td | 0 | top-down | 2/40/40 | (1,871,476) | — | — | — | — | 5.3 |
| 8 | td | 1 | lines r0 | **0/0/0** | 1,982,521 | 14,528 | 3,411 | 4.26 | 0.06 | 8.2 |
| 8 | bu | 1 | lines r0 | 0/18/18 | (2,147,336) | 53,501 | 9,430 | 5.67 | — | 9.7 |
| 8 | bu | 2 | lines r1 | 0/1444/1444 | (1,937,231) | 66,230 | 9,580 | 6.91 | — | 10.4 |
| 16 | blind | 1 | reserve 0 | 11/336/336 | (4,274,800) | — | — | — | — | 12.2 |
| 16 | uniform | 1 | uniform 4 | 1/2907/2907 | (4,373,880) | 7,260 | 16,676 | 0.44 | — | 16.9 |
| 16 | uniform | 2 | uniform 8 | 60/1102/1102 | (4,160,682) | 14,520 | 17,587 | 0.83 | — | 16.5 |
| 16 | td | 0 | top-down | 3/32/32 | (3,677,304) | — | — | — | — | 14.2 |
| 16 | td | 1 | lines r0 | 7/170/170 | (4,323,498) | 45,448 | 8,259 | 5.50 | — | 20.9 |
| 16 | td | 2 | lines r1 | 0/2864/2864 | (3,857,628) | 63,400 | 7,032 | 9.02 | — | 20.4 |
| 16 | bu | 1 | lines r0 | 1/8/8 | (3,839,107) | 145,191 | 17,826 | 8.14 | — | 23.0 |
| 16 | bu | 2 | lines r1 | **0/0/0** | 3,905,996 | 111,767 | 18,136 | 6.16 | 0.11 | 21.7 |

The uniform sweep ended at F = 16 at every size: `io_cell M5: uniform 16
asks more tracks than the cell has (14 signal tracks over its extent)`.

| size | arm | rounds | classes solved | endpoint | E1 for comparison (share / blind band at its best) |
|---|---|---|---|---|---|
| 2 | uniform | 2 | 36 | dirty, 6/136/136 | — |
| 2 | td | 1 + 2 | 36 | dirty, 1/376/376 | share dirty 2/8/8 |
| 2 | bu | 1 + 1 | 36 | **clean** | share dirty 1/8/8; blind clean in 2 rounds at 8.15× |
| 4 | uniform | 2 | 36 | dirty, 14/269/269 | — |
| 4 | td | 1 + 2 | 36 | dirty, 0/37/37 | share dirty 4/45/45 |
| 4 | bu | 1 + 2 | 54 | dirty, 0/757/757 (best round 0/45/45) | share dirty 5/117/117; blind clean in 2 rounds at 5.13× |
| 8 | uniform | 2 | 36 | dirty, 30/560/560 | — |
| 8 | td | 1 + 1 | 18 | **clean** | share dirty 13/96/96; blind never clean |
| 8 | bu | 1 + 2 | 54 | dirty, 0/1444/1444 (best round 0/18/18) | share dirty 11/109/109 |
| 16 | uniform | 2 | 36 | dirty, 60/1102/1102 | — |
| 16 | td | 1 + 2 | 36 | dirty, 0/2864/2864 (best round 7/170/170) | share dirty 21/203/203 |
| 16 | bu | 1 + 2 | 54 | **clean** | share dirty 12/368/368; blind clean in 2 rounds at 4.49× |

## Results — healers on (`soc_lib`'s heal-if-dirty in every round)

HEALED_PLACEHOLDER

## The control — `tpu.tcl` (healers off)

The mesh, where the top's demand is uniform by construction:

| size | arm | round | policy | final ovl/unpl/viol | detailed WL | reserved | used | reserved ÷ used |
|---|---|---|---|---|---|---|---|---|
| 8 | blind | 1 | reserve 0 | 0/0/0 | 550,528 | — | — | — |
| 8 | uniform | 1 | uniform 4 | 0/0/0 | 550,528 | 96 | 2,112 | 0.05 |
| 8 | td | 0 | top-down | 0/0/0 | 197,376 | — | — | — |
| 8 | td | 1 | lines r0 | 0/0/0 | 550,528 | 2,344 | 2,048 | 1.14 |
| 8 | bu | 1 | lines r0 | 0/0/0 | 550,528 | 2,112 | 2,112 | 1.00 |
| 16 | blind | 1 | reserve 0 | 0/0/0 | 2,174,208 | — | — | — |
| 16 | uniform | 1 | uniform 4 | 0/0/0 | 2,174,208 | 192 | 8,320 | 0.02 |
| 16 | td | 0 | top-down | 0/0/0 | 738,816 | — | — | — |
| 16 | td | 1 | lines r0 | 0/0/0 | 2,174,208 | 8,784 | 8,192 | 1.07 |
| 16 | bu | 1 | lines r0 | 0/0/0 | 2,174,208 | 8,320 | 8,320 | 1.00 |

Every arm is clean in its first round, as in E1, so the control has no
rounds to count; what it measures is the reservation, and here the
positional derivation is **exact**: the blind-round-derived lines reserve
1.00× what the top uses at both sizes (the union over a mesh's congruent
rows IS each row's demand), against the share's 1.12–1.19×, and the route
under them is byte-identical in wirelength.  The uniform guess reserves
2–5 % of the demand and changes nothing either — on a design that needed
no reservation, a guess that misses costs nothing, which is the one case
where a guess is free.

## What the tables say

1. **The guessed reservation is worse than none, at every size and every
   F it can take.**  F = 4 strands 368 / 741 / 1,454 / 2,907 bits against
   the blind round's 8 / 117 / 93 / 336; F = 8 strands 136 / 269 / 560 /
   1,102; F = 16 is refused by the smallest cell.  What strands is the
   cores' 32-bit `regf → alu` bus at every core (48 units wide, a
   `U_HVH` pinned onto M2/M3 or M4/M3 — LOW layers whose windows cannot
   host 32 bits, "closed by partner stretch"): four evenly spaced tracks on
   every TOP layer of a 35-track seat land inside it, the local solve
   moves the bus off, and the only place left is a layer that cannot take
   it.  A guess does not merely over- or under-reserve — on a block whose
   seats are full it *damages the block* before it helps the top, which
   the hypothesis had no row for.

2. **The derived positional reservation converges where the share never
   did, and in fewer rounds than the blind band.**  Clean with healers off
   at NQ = 2 (bu, one informed round), NQ = 8 (td, one) and NQ = 16 (bu,
   two), at 3.2× / 4.3× / 6.2× the top's used tracks; E1's blind band was
   clean in two rounds at 8.2× / never / 4.5×, and the share never.  At
   NQ = 4 no arm reaches clean (best 0/24/24, td round 1) where the blind
   band did.  The reservation's size is the same order as the band's
   because both are unions: a band takes a whole layer, a positional line
   takes every track any instance's top ever used.

3. **The top does not use the reservation.**  Hit rate 0.09 / 0.10 / 0.06
   / 0.11 on the four re-read rounds: nine of ten tracks the top places
   over a governed instance are NOT the tracks the block reserved for it.
   The audit's per-cell ranges say the same — `cluster_cell M5: 118
   reserved, the top uses 0..40 of them per instance`.  The primitive is
   one-sided: the block keeps tracks free, and the top's planner and NUTS
   have no reason to prefer them, so after the block moves its buses the
   top re-plans against the new occupancy and seats elsewhere.  What the
   reservation buys is that the block's own buses have LEFT the region the
   top wants — which is why a clean round is clean — not that the top is
   routed through named tracks.

4. **So the informed loop has no fixpoint.**  Each derivation reads the
   top of the round before, the top moved, and the union moves with it:
   the second informed round strands 757 (NQ = 4), 1,444 (NQ = 8) and 2,864
   (NQ = 16, td) bits — every one a core's 32-bit bus under lines that now
   cover its seat — and at NQ = 16 the same second round goes clean (bu),
   which is the same mechanism with the other sign.  E1's share loop was
   self-consistent after one round and wrong; the positional loop is right
   at three sizes and not self-consistent at any.  The derivation reports
   `seat_hit` for exactly these lines and does not act on it; E1's floor
   was the share's answer to the same collision.

5. **The union is the price of solve-once-copy, and it is most of the
   reservation.**  At NQ = 16 the blind-derived lines reserve 8.1× what
   the top uses because 32 clusters' tops are unioned into one cluster
   template; the per-instance figure the derivation prints (`used/inst`)
   is the honest per-block cost and the union is what a template solved
   once must pay.  Where instances are congruent AND the top is uniform
   (the mesh control) the union collapses to the demand and the
   reservation is exact at 1.00×.

## What the tables do not say

- **That a positional reservation is the wrong primitive.**  It is the
  right shape — it converges where the uniform share could not, and it
  is exact on the control — and it is half a primitive: the top-side half,
  a reservation the top's planner *prefers* over a governed instance (a
  negative cost on reserved tracks, or a hard restriction of the crossing
  bundles to them), is what turns a 6–11 % hit rate into a corridor and
  gives the informed loop a fixpoint.  That is the build item this
  experiment writes.
- **That the derivation should floor by the block's own seat.**  E1's
  floor did that for the share and left the top nothing on the layers it
  wanted; here the collision is reported (`seat_hit`) and the local solve
  is given the chance to move the bus, which it takes when a layer can
  host it and cannot when the only free layers are LOW.  A derivation
  that ALSO steered the top would make the collision rarer rather than
  forbidding it.
- **Anything judged by an independent audit** (build item 2 is still not
  built; every row is `check_design`'s, the same audit on every arm).
- **That the uniform arm was run at its best.**  Its parameter was swept
  over its whole legal range (F = 4, 8; 16 refused) and it was worse than
  nothing everywhere, but a guess with a different SHAPE — F tracks on the
  top pair only, or a band of adjacent tracks rather than evenly spaced
  ones — was not tried, and "evenly spaced on every TOP layer" is the
  ladder's own description of the conventional arm, not a survey of
  practice.
- **Why NQ = 4 is the size no arm cleans.**  Its best derived round leaves
  24 bits (td round 1: one core's bus on a `Z_HVH` over M4/M5/M6 and one
  `U_VHV`, plus 8 of `pc_`) and the blind band cleared it at `reserve 2`;
  the write-up records the observation and does not guess at the
  mechanism.

## What it contributes to the ladder

E5 was the experiment that would price a guessed feedthrough reservation
against a derived one.  It does that — the guess is not just wasteful but
harmful on a block whose seats are full, and the derived reservation is
exact on a mesh and converges on the SoC where the share could not — and
it measures the thing the ladder's rung 4 had assumed: that a reserved
track is a track the top uses.  It is not, at 6–11 %, until the top is
told.  The build order gains the top-side primitive (a preference for
reserved tracks in the hier planner and NUTS over governed instances) as
item 6b, ahead of the fixed-pin work; E1's re-run against the positional
primitive is this table's `td`/`bu` rows.

## Provenance

- `flow/tcl/converge.tcl soc 2 4 8 16 -primitive reserve -arms
  uniform,td,bu -out run` (healers off); `… -heal -out runh` (healed);
  `flow/tcl/converge.tcl tpu 8 16 -primitive reserve -arms uniform,td,bu
  -out runt` (the control).  Each writes
  `e5_<vehicle>_<heal>_step1_reserve.md` and one `.log`/`.rep` pair per
  session; the hit rates are from `soc.tcl <N> -bottomup -noheal -shares
  run/<arm>_shares_r<k>.buda -primitive reserve -report …` re-runs of the
  four clean-or-best rounds under the extended report.
- Engine at the merge of #936 plus this change (the `uniform` form, the
  driver's `uniform` arm, the blocked-track enforcement on globally solved
  instances, the extended report).  `BUDA_THREADS_REQUEST` at the launcher
  default (half the machine's logical CPUs).
