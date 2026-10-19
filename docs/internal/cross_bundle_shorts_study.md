# Cross-bundle shorts: what the DetailedNUTS levers buy

*2026-10-05.  Follow-up to #948 (the audit) and #962 (the counting and the
first two levers).  Published as the artifact "Cross-Bundle Short Study" (see
[artifacts.md](../artifacts.md)); this file is its twin in the tree.*

## Question

Can DetailedNUTS stop creating **cross-bundle shorts** (two wires of different
nets from different bundles sharing metal on one layer) instead of leaving them
for `check_design`'s `BIT_SHORT` and the judge to report?

## Mechanism

Placement reserves a bus's tracks against other bundles over its **abstract**
span.  The span-follow (`adjust_bit_spans`) then moves each bit's end to its
junction partner's same-bit track, which can lie past that span, on a track
another bundle holds.  The two abstract spans never met, so neither reserved
against the other.  On the `soc.tcl 8 -LAYOUT compact -PAD 10 -GAP 4 -M 4
-noheal` checkpoint 18 of 20 shorts have **both** wires stretched past their
reserved spans, 2 have one.

## Levers (all opt-in, `BUDA_DNUTS_SHORT_GUARD`)

| lever | what it does |
|---|---|
| `reach` | reserve against the span a segment will REACH; per bit since this study (each track's own reach, verified after the pick against other bundles' per-track reach) |
| `cull` | after the span-follow, remove the stretched side of each short, counted unplaced |
| `reseat` | (built, **removed**) drop the culprit's track from its pool and re-solve |

## Results

Corpus, 57 flows, QoR triple (overlaps/unplaced/viol_bundles), one build,
better / worse flows: `reach` 7 / 3, `cull` 3 / 4, `reach,cull` 6 / 4.
Wirelength moves < 0.01 %.  Regressions under `reach`: `bigHalf` 0/0/0 ->
0/52/1, `mix2_fast_bottomup_shared` 0/0/2 -> 4/60/7.

| repro | baseline | `reach` | `cull` |
|---|---|---|---|
| `soc.tcl 8 compact -noheal`, judge SHORT / unplaced | 20 / 1131 | 20 / 1131 (per-bit); 0 / 1355 (envelope form) | 0 / 1149 |
| `converge.tcl soc 4 -arms blind -maxreserve 0 -informed 0` | 4 shorts, 85 unplaced | 4, 85 | 0, 89 |
| mix2 top-down recipe (`test_refine_selection`) | clean | clean | clean |
| mix2 release recipe (`test_ripup_release_moves`) | clean | clean | clean |

* Where shorts cannot be avoided the cause is physical: the competing bundle
  holds the free tracks inside the stretched bit's reach.  Removing the shorts
  means dropping bits (about 18 under `cull`; seven whole 32-bit M5 segments
  under the old envelope `reach`).
* `reseat` diverged: 20 -> 27 -> 45 shorts, because moving one track shifts
  the whole window onto tracks the neighbours also reach.
* The converge repro's shorts have one wire stretched and one inside.
  `reach` sees them (20 of the segment's 32 picked tracks conflict) but the
  window holds exactly 32 tracks for 32 bits, so there is no re-pick.  The 20
  is conservative: the stretched wire's partner is on a layer not placed yet,
  so the bound is its whole abstract footprint, while the true stretch hits 4
  bits.  (Corrected: this file first said the partner lands outside its
  footprint; it lands inside, the footprint is just wide.)
* The two mix2 recipes carried 3 and 2 shorts when #948 landed.  They judge
  clean now, with or without a lever (the healers' score reads shorts since
  #962), so both tests' `known_cross_shorts` bounds are 0.

## Healers

`ripup_reroute`, `negotiate_congestion` and `refine_selection` count shorts as
opens by default (`BUDA_HEAL_SHORTS`).  The full `soc.tcl 8 compact PAD 10 GAP
4` flow, whose first round has 1151 violations, ends 0 overlaps / 0 unplaced /
0 audit violations.  The overlaps-only blind spot no longer applies.

## Decision

No lever becomes a default.  A real fix is at the abstract level: two buses
whose stretched extents will meet should not be seated on the same track.

## Footprint measurement (2026-10-05)

For each short, the stretched wire's partner bit against the partner's
abstract footprint (`bus_segment` position +/- width / 2), from the
checkpoints' tables:

| repro | shorts | stretched wire's partner bit |
|---|---|---|
| `soc.tcl 8 compact -noheal` | 20 | outside the footprint in all 20 (18 also have the other wire stretched, partner inside) |
| `converge.tcl soc 4` blind round | 4 | inside the footprint in all 4 (footprint 218 wide, stretch 105) |

So keeping bits inside the abstract footprint would address the soc repro and
not the converge one.

## Footprint lever, tried and dropped (2026-10-05)

Proposal 1 was a `foot` lever: seat a segment's bits inside its own abstract
footprint (`abstract_pos` +/- `abstract_width` / 2) whenever that footprint
holds enough free tracks, else the whole pool.  On `soc.tcl 8 compact -noheal`
it moved 5 of the 451 bits that sit outside their footprint and left the
judge's 20 shorts and 1131 unplaced bits unchanged; it was removed.

Why the footprint is not enough, from a trace of the pool (not a guess):

* A width-model shortfall is not the cause: counting signal tracks inside each
  placed segment's footprint from the pattern, 390 of 391 segments hold at
  least their bits.
* Bundle 434 seg 1 (32 bits, M4, footprint y 768..840) is seated ON a keepout,
  `(80,770)-(218,908)`: abstract NUTS had said so ("placed ON a keepout, window
  exhausted") and DetailedNUTS keeps its bits off the keepout, so 30 of them
  land at 910..975.  (An earlier version of this section blamed five sibling
  bundles at the same abstract position; their spans are disjoint, 213..355,
  1061..1203, ..., and they hold nothing of each other's.)
* The shorts in this checkpoint are of another kind.  Bundle 446 seg 1 (M4)
  gets 28 of its 32 bits inside the footprint and 3 at y = 1063.5, 1203 and
  1345.5.  Its footprint (y 1410..1482) overlaps bundle 392's M4 footprint
  (y 1346..1418) by 8 units over the same x span in the ABSTRACT plan, which is
  one of the 84 abstract overlaps of this unhealed round; the tracks DNUTS then
  finds taken are the ones that overlap makes contested.  The 3 far bits make
  446 seg 2's M5 bits stretch 380 units, onto tracks 392 seg 0 holds.  (An
  earlier draft called this a cascade of spills in a packed channel; the
  measurement below says the root is the abstract overlap itself.)

## Abstract-level experiment: inter-bus gap (2026-10-05)

`set_track_pitch` is the existing slack between buses at the abstract stage
(default 1.0).  `soc.tcl 8 -LAYOUT compact -PAD 10 -GAP 4 -M 4` with it set
before `run_planner` (a throwaway copy of `soc.tcl`, not checked in), first
round, `-noheal`:

| pitch | abstract overlaps | unplaced | judge SHORT | detailed WL |
|---|---|---|---|---|
| default | 84 | 1131 | 20 | 1,212,134 |
| auto | 19 | 329 | 48 | 1,631,392 |
| 4.5 | 15 | 352 | 8 | 1,538,397 |
| 9 | 11 | 208 | 0 | 1,590,261 |

Full flow with healing (judge CLEAN in every row, `soc.tcl`'s own verdict in
brackets): default 1,692,110 detailed WL [clean]; 4.5: 1,597,600 (-5.6 %) and
9: 1,589,216 (-6.1 %) [each ends on 1 abstract overlap, FAILED by `soc.tcl`'s
rule, 0 audit violations]; 18: 1,836,338 (+8.5 %) [9 overlaps].  Abstract WL
78,488 / 75,572 / 75,093 / 84,228.

So slack at the abstract stage removes the shorts of the first round (at 9)
and 90 % of its unplaced bits, and the healed route is shorter, but the effect
is not monotone (`auto` has more shorts than the default, 18 costs wire), the
flows end on a residual overlap that the default flow does not, and it is one
design at one size.  Not a recommendation to change a default.

### Pitch sweep over three designs (2026-10-06)

Full flow, `set_track_pitch` set before `run_planner` via a throwaway copy of
`soc.tcl`.  The judge reads CLEAN on every row.  Times are NOT comparable with
the default run: three flows shared four cores, and only the default of A was
timed alone (25 s).

| design | pitch | first round (ovl / unplaced) | final (`soc.tcl`) | detailed WL | time |
|---|---|---|---|---|---|
| A: `soc 8 compact PAD 10 GAP 4 M 4` | default | 84 / 1131 | clean | 1,692,110 | 25 s alone |
| | 3 | 19 / 329 | 1 overlap, FAILED | 1,602,582 | 222 s |
| | 6 | 15 / 352 | 1 overlap, FAILED | 1,597,844 | 301 s |
| | 9 | 11 / 208 | 1 overlap, FAILED | 1,589,216 | 354 s |
| | 12 | 29 / 1224 | 12 overlaps, FAILED | 1,777,593 | 225 s |
| B: `soc 4 compact PAD 10 GAP 4 M 4` | default | 58 / 640 | 2 overlaps, FAILED | 738,742 | 57 s |
| | 6 | 2 / 144 | 3 overlaps, FAILED | 739,516 | 46 s |
| | 9 | 6 / 162 | 3 overlaps, FAILED | 731,657 | 49 s |
| C: `soc 8` (defaults) | default | 2 / 40 | clean | 1,968,672 | 22 s |
| | 9 | 1 / 32 | clean | 1,950,146 | 19 s |

Reading: a gap of 3 to 9 cuts the first round's unplaced bits by 60 to 80 %
on A and B and a little on C, and the detailed WL by 5.6 % (A), 1 % (B,
at 9) and 1 % (C).  It does not make the flow end cleaner by `soc.tcl`'s rule
(A ends on 1 overlap where the default ends clean; B fails with or without it),
and 12 is worse than no gap on A.  Healing time on A is not measured fairly.
No change to a default is supported.

### Pitch sweep re-run serially, with healing time and first-round shorts (2026-10-06)

Same flows, one at a time (the parallel timings above are void).  First-round
shorts are the shorted bits `check_design` lists in the first audit, before any
heal; the judge reads CLEAN on the final route of every row.

| design | pitch | first round: ovl / unplaced / shorted bits | final | detailed WL | time |
|---|---|---|---|---|---|
| A `soc 8 compact` | default | 84 / 1131 / 20 | clean | 1,692,110 | 24 s |
| | 3 | 19 / 329 / 48 | 1 overlap | 1,602,582 | 136 s |
| | 6 | 15 / 352 / 8 | 1 overlap | 1,597,844 | 188 s |
| | 9 | 11 / 208 / 0 | 1 overlap | 1,589,216 | 221 s |
| B `soc 4 compact` | default | 58 / 640 / 9 | 2 overlaps | 738,742 | 35 s |
| | 6 | 2 / 144 / 18 | 3 overlaps | 739,516 | 30 s |
| | 9 | 6 / 162 / 23 | 3 overlaps | 731,657 | 31 s |
| C `soc 8` | default | 2 / 40 / 0 | clean | 1,968,672 | 14 s |
| | 9 | 1 / 32 / 0 | clean | 1,950,146 | 15 s |

A gap does not remove the shorts: on B it raises the first round's shorted bits
from 9 to 18 and 23, and on A the count is not monotone (48, 8, 0).  On A it
makes the healing 6 to 9 times slower for a 5 to 6 % shorter route, and ends
on a residual overlap; on B and C the time is unchanged.  The first round's
overlaps and unplaced bits drop everywhere a gap is set; the shorts do not
follow them.  So the gap is not a short fix.

### `run_planner hier 5 signal_tracks` (2026-10-06)

`soc.tcl` plans with `run_planner hier 5` (band capacity in layout width).  With
`signal_tracks` (capacity in discrete signal tracks) on the same three
designs, run serially, default pitch; baselines are the serial rows above:

| design | planner | first round: ovl / unplaced / shorted bits | final | detailed WL | time |
|---|---|---|---|---|---|
| A `soc 8 compact` | default | 84 / 1131 / 20 | clean | 1,692,110 | 24 s |
| | `signal_tracks` | 42 / 1455 / 54 | 1 overlap | 1,550,523 | 172 s |
| B `soc 4 compact` | default | 58 / 640 / 9 | 2 overlaps | 738,742 | 35 s |
| | `signal_tracks` | 11 / 338 / 27 | clean | 865,115 | 12 s |
| C `soc 8` | default | 2 / 40 / 0 | clean | 1,968,672 | 14 s |
| | `signal_tracks` | 3 / 48 / 0 | clean | 1,979,198 | 18 s |

The judge reads CLEAN on every final route.  The effect points in different
directions on each design: wire -8.4 % with a 7x slower heal on A, +17 % wire
with a faster, clean heal on B, nothing on C; first-round shorts rise on A
(20 to 54) and B (9 to 27).  Not a fix for the shorts, and not a default.

## What the first-round shorts are made of (2026-10-06)

Each short pair, classified from the checkpoint tables (`bus_segment`,
`bus_via`, `net_segment`, the layer stack and the leaf footprints, read with
the judge's own readers): which wire is stretched past its abstract span, which
partner bit it followed, and why that partner bit sits outside the partner's
footprint.  Pairs counted with real metal overlap (track distance below the
sum of half widths), which reproduces the judge's and `check_design`'s counts.

| checkpoint (first round, healers off) | short pairs | partner bit displaced because |
|---|---|---|
| `soc 8 compact` | 20 | all 20: the partner segment's abstract footprint overlaps another bundle's footprint in the plan |
| `soc 4 compact` | 9 | all 9: the same |
| `soc 8` defaults | 0 | |
| `soc 8 compact`, pitch auto | 48 | all 48: the partner is seated over a leaf-cell keepout |
| `soc 8 compact`, pitch 4.5 | 8 | all 8: the same |
| `converge soc 4` blind round | 4 | none: the partner bit is INSIDE a wide footprint |

So in the default first round every short traces to an overlap that abstract
NUTS already counts, which is why the healers, which read that count, end the
default flows without them.  With a gap set, the abstract overlaps shrink and
the shorts that remain are keepout-seated partners instead.  The converge
shorts are neither.  Limits: the soc family only, five checkpoints, one
heuristic for "overlaps another footprint" (any other bundle's rectangle on the
same layer with positive along and across overlap), and the 'why' of the
converge case is the stretch inside a wide footprint, not a displaced bit.
Corpus flows were not classified: the corpus run does not keep checkpoints.  The classifier is `tools/experiment/short_causes.py`.

## The converge shorts, and a lever for them: junction reach (2026-10-07)

Cause.  `converge soc 4`'s four shorts come from a stub that meets a wide
perpendicular partner.  Bundle 1 seg 0 (M6) meets seg 1 (M7, 32 bits spread
over 218 units, x 851..1069).  Abstract NUTS ends the stub's span at the
partner's CENTRE (`do_span_adjustments` and `tighten_spans_to_reach` in
`src/nuts.cpp` both use the partner's `track_position`), but each stub bit must
reach ITS partner bit, and those lie across the whole partner width: the low
bits run 105 units past the span's end and over bundle 176's wires.  The
abstract plan therefore under-states a junction's metal by half the partner's
width, wherever the partner is wide.

Lever.  `BUDA_NUTS_JUNCTION_REACH=1` ends the span at the partner's far edge in
both places (off by default; unset is byte-identical).

| measurement | default | junction reach |
|---|---|---|
| `converge soc 4` blind round: judge SHORT / unplaced / abstract overlaps | 4 / 85 / 5 | 0 / 117 / 7 |
| A `soc 8 compact`, first round shorted bits | 20 | 2 |
| B `soc 4 compact`, first round shorted bits | 9 | 2 |
| C `soc 8`, first round shorted bits | 0 | 0 |
| A / B / C final result (`soc.tcl`) | clean / 2 overlaps / clean | clean / 2 overlaps / clean |
| A / B / C detailed WL | 1,692,110 / 738,742 / 1,968,672 | 1,706,394 / 739,028 / 1,968,672 |
| A / B / C time | 24 / 35 / 14 s | 18 / 28 / 11 s |

Judge CLEAN on every final route.  Corpus (57 flows, QoR triple): 6 better,
5 worse, 45 unchanged; abstract WL +6.7 % (the spans are longer by
construction), detailed WL +0.01 %, sweep time -9 %.  Worse: both bottom-up
mix2 rows (`mix2_fast_bottomup_caps` 2/0/0 -> 11/72/11, `..._shared` 0/0/2 ->
4/80/10) and three chip bottom-up rows by a few overlaps or bits.  So it removes
the shorts it targets where the first round is the whole story, and costs
bits on bottom-up designs, where a template's copied span is longer.  Not a
default; the bottom-up regression wants its own look before it could be one.
Fast tier: 3271 passed.

### Why junction reach loses bits on `mix2_fast_bottomup_shared` (2026-10-13)

The lever is better than the default before the healers and worse after them.
First DetailedNUTS solve: 558 unplaced bits at the default, 398 with the lever
(376 after its first healer round).  End of the flow: 0 unplaced at the default
(2 shorted bits), 80 with the lever, in two groups: bundles 149..154 (2 segs x
4 bits) and 171..174 (seg 1, 8 bits each).

What differs is the route the healers settled on.  Bundle 171 ends as two
segments at the default (`L_HV`: M4 seg 0, M5 seg 1) and as three with the
lever (M5, M4, M5), whose M4 middle segment sits in a 15-unit perpendicular
window, `[1340, 1355]`, that holds 6 to 7 signal tracks of the leased M4 (the
flow's 75 % share) for 8 bits; DetailedNUTS reports "insufficient signal tracks
(7) for bus width 8" at four such windows (`[420,435]`, `[880,895]`,
`[1340,1355]`, `[1700,1715]`) that the default run never meets, and those four
windows are bundles 171 to 174.  Bundles 149 to 154 are the same story on M2/M3.
So the loss is a different healer trajectory (ripup re-pins to other candidates
when the abstract spans are longer) landing on a seat too narrow for its bus,
not a flaw in what the span says.  Not tried: constraining the healers so they
cannot choose a candidate whose seat window is narrower than its bus.  Method:
the flow's `.buda` run in a `BudaSession` with its `visualize`/`exit` lines
dropped, abstract segments of the affected bundles printed with and without
`BUDA_NUTS_JUNCTION_REACH=1`.

#### Healer seat scoring does not rescue it (2026-10-13)

`set_heal_seats` (`BUDA_HEAL_SEATS=1`) already scores supply-doomed seats, which
is the rule proposed above for the 15-unit windows, so it was tried instead of
new code.  `mix2_fast_bottomup_shared`, final `check_design` violations (and
detailed WL):

| junction reach | heal seats | violations | detailed WL |
|---|---|---|---|
| off | off | 2 | 890,954 |
| off | on | 2 | 943,484 |
| on | off | 80 | 965,116 |
| on | on | 320 | 808,764 (route incomplete) |

Seat scoring costs 6 % wire with the lever off and leaves 2 violations, and with
the lever on it makes the endpoint worse (320, 34 groups, 22 bundles), not
better.  The hypothesis that a healer that avoids too-narrow seats recovers the
lost bits is not supported by the existing mechanism; a purpose-built filter
was not written.

#### The static width gate on bottom-up sessions (2026-10-13)

The ripup trial gate `_rr_width_infeasible` (#523) already refuses a candidate
whose segment cannot hold its bits in its slide window, and is scoped out of
bottom-up sessions (an earlier measurement found it perturbed class moves).
`BUDA_RR_WIDTH_GATE_LOCKED=1` (study knob, off by default) lets it run there.

`mix2_fast_bottomup_shared` through `bin/buda`, final `check_design` violations
(detailed WL): lever off, gate off 2 (890,954); lever off, gate on 0
(905,451); lever on, gate off 80 (965,116); lever on, gate on 28 (943,902).

Corpus (57 flows, QoR triple):

| setting | better | worse | notes |
|---|---|---|---|
| gate only | 0 | 2 | `mix2_fast_bottomup_shared` 0/0/2 -> 5/0/0 (no opens, 5 abstract overlaps), `mix2_fast_on_aligned_sql` 2/16/1 -> 2/28/2; WL unchanged |
| gate + junction reach | 7 | 5 | the lever's own 6/5, plus `mix2_fast_on_aligned_sql` -> 1/0/0 |
| junction reach alone | 6 | 5 | |

With the gate the lever's bottom-up losses shrink (`mix2_fast_bottomup_caps`
11/72/11 -> 5/48/6, `mix2_fast_bottomup_shared` 4/80/10 -> 7/22/3) and do not
disappear; the gate on its own makes two flows worse.  Sweep times (1047 s base,
949 s lever, 1604 s gate, 1377 s gate + lever) come from different days and
runs and are not comparable.  So neither is a default.

### Junction reach, top-down designs only (2026-10-13)

The bottom-up losses above are one kind of flow (a template solved once and
copied, with goals of its own), so the lever got a mode that leaves them alone:
`BUDA_NUTS_JUNCTION_REACH=topdown` applies it unless the design carries
bottom-up marks.  The session tells the engine at each `run_nuts` and at each
healer re-run (`set_junction_reach_allowed`, from `bdb.bottom_up_cells()`).

`mix2_fast_bottomup_shared` with `topdown` is identical to the lever off (2
violations, 890,954 WL); a top-down flow behaves as with the lever on.

Corpus (57 flows) against the default: 5 better, 0 worse, 51 unchanged.
Better: `tc3a` 0/0/2 -> 0/0/0, `mix2_fast_topdown` 1/0/0 -> 0/0/0,
`chip_stack_topdown` 22/263/21 -> 21/235/19, `chip_topdown` 5/134/11 ->
5/130/11, `chip3_topdown` 6/268/35 -> 6/266/35.  Detailed WL +0.01 %, abstract
WL +4.85 % (longer spans by construction).  Against the lever on everywhere
(6 better, 5 worse) the difference is the bottom-up rows, which return to the
default.  The soc first-round shorts quoted above (A 20 to 2, B 9 to 2) were
measured on top-down flows.  Fast tier: 3271 passed.  Not made a default: that
call, and whether the abstract-WL growth is acceptable, is for the owner.

### Review of #991 (2026-10-13)

A review of the PR at `cc7a3ad` found the default path byte-identical to
`main` (full corpus, 13 CLI flows, a hash of 8 in-process routes) and the lever
numbers reproducible, and raised points inside the levers.  What each came to:

- **`topdown` does not reach the converge repro.**  `converge.tcl soc 4 -arms
  blind -judge` is a `-bottomup` session (`converge::mark *` marks 18 cells), so
  `topdown` decides per DESIGN and leaves the lever off for every bundle there,
  the top-level ones that carry the shorts included.  The review's table: unset
  4 judge SHORTs / 85 unplaced, `=1` 0 / 117, `topdown` 4 / 85 (identical to
  unset).  The motivating shorts therefore stay under `topdown`, and converge is
  not in the 57-flow corpus, so "5 better, 0 worse" says nothing about them.  I
  have not re-run that table myself.
- **Junction reach left per-bit dangling metal, and the judge cannot see it.**
  The span end moved to the partner's far edge (`track ± width/2`), but
  DetailedNUTS promotes a junction to a segment end only when the partner's
  track equals the end exactly, so the bits kept a shared end past their vias:
  on the mix2 top-down recipe with the lever on, bundle 35 seg 5 had 3 ANTENNA
  bits (reproduced; unset has none).  `independent_audit.py` has no
  dangling-metal check, so "Judge CLEAN" never covered this class.  Fixed:
  DetailedNUTS now also promotes an end that sits at the partner's far edge when
  the lever is active, and the recipe checks clean under both modes.  Re-swept:
  `topdown` 5 better / 0 worse / 51 unchanged with the same five rows, abstract
  WL +4.85 %, detailed WL +0.01 %; `reach` 7 / 3.  An ANTENNA count belongs in
  any A/B before the lever is made a default.
- **The per-bit `reach` lever's exact half**, three corrections: an NDR segment
  compared by its abstract span against earlier NDR buses and bottom-up copies
  (they carry no per-track reach), now by its own reach; the tracks the probe
  rejected were dropped by the same-bundle hazard repairs, now honoured by all
  three; and the loop's exits misreported, now the pick with the fewest
  offending tracks stands (it used to drift after the fourth round) and the
  probe is quiet, so a failed re-pick no longer prints the stranding warning for
  a bundle that was placed.  The probe also skips assignments whose interval
  cannot meet the segment's and computes the bit reaches once.  Not fixed: NDR
  segments still get no post-pick exact check, and a bottom-up copy is compared
  by its envelope.
- **Not tested before:** a two-bit reach test now exists
  (`test_reach_places_a_two_bit_bus_clear_of_a_stretching_partner`).  Nothing
  tests the NDR comparison, the `topdown` session sync or the width gate.

### Made the default for top-down designs (2026-10-13)

The owner decided that `BUDA_NUTS_JUNCTION_REACH=topdown` becomes the default
and that the abstract-WL growth is acceptable.  Unset (or empty) now means
`topdown`; `0`/`off` is the pre-lever spans; `1` is every design, as before.
The corpus numbers are the ones above (the `topdown` sweep against the old
default on the same build: 5 better / 0 worse / 51 unchanged, abstract WL
+4.85 %, detailed WL +0.01 %), so no further sweep was needed; the QoR
snapshot `qor/qor_table.md` is refreshed by the nightly's own PR.

What the flip cost, found by running the fast tier with it on:
- **Seven fixtures pinned the pre-lever spans** and failed: four exact-mechanism
  tests (`nuts_corner_touch`'s cross-layer corner, the admit-then-cull shape,
  b44's pull breakpoint, the dogleg's jog) and one wirelength pin
  (`b3_bus_023` abstract 7765 -> 7770).  The mechanism tests now run with the
  lever off through the `no_junction_reach` fixture (the session reads the
  variable on every command, so it works in-process); the wirelength pin moved.
- **`flow/nuts_corner_touch.buda` is a real, if small, regression.**  A
  healerless micro-fixture outside the corpus, it ended 0 overlaps / 0
  unplaced and now ends 1 overlap / 4 unplaced: the far-edge spans change the
  cross-layer corner the fixture is built on.  The corpus (5/0/51) cannot see
  it, and I did not chase it.  The flow now declares `set_junction_reach off`
  on its first lines (the script-level switch added to #994), so it keeps the
  behaviour it was built for whatever the environment says, and its three
  tests no longer need the `no_junction_reach` fixture.
- **Nothing here changes the converge repro**: its blind round is bottom-up,
  so the four motivating shorts stay (see the review section above).

### How the junction-reach switch works, in detail (2026-10-15)

**Who decides.**  `BudaSession._sync_junction_reach` runs before every
command and hands the engine one verdict (`buda.set_junction_reach_allowed`;
`buda.junction_reach_active()` reads it back).  Order of precedence: a typed
`set_junction_reach on|off|topdown` first, then `BUDA_NUTS_JUNCTION_REACH`,
then the default.  `topdown` (the default) is on unless the BDB carries
`set_bottom_up` marks; `on` / `1` forces every design; `off` / `0` forces none;
`env` drops the typed setting.  The engine holds no mode of its own (-1 = no
session behind it, so the process environment decides), which is why a driver
can flip it in-process and why the first caller no longer freezes it.

**Where it acts: abstract NUTS and DetailedNUTS, never the planner.**  Four
places read the verdict, none of them in `congestion_planner.cpp`:
- `do_span_adjustments` (`src/nuts.cpp`): a stub that follows a perpendicular
  partner no longer ends at the partner's track *centre* but at its far edge,
  `track -+ width/2`, on the end that faces away (`sc.lo_end` picks the sign).
- the same pass's cover step: the partner's two edges are covered, not just
  its centre, so every bit of the partner is reachable.
- `tighten_spans_to_reach`: the reach window is widened by half the partner's
  width on both sides (`jh`).
- `make_bus_segments` (`src/detailed_nuts.cpp`): a placed stub end that lies
  on the partner's far edge, not only at its centre, is promoted to an
  endpoint junction, so bits snap to their own vias there instead of keeping a
  shared abstract end past it (which left dangling metal).
The planner picks topologies and layers and charges bands from the *nominal*
spans, so it never prices the extra half-width.  The cost shows up only in
NUTS output: abstract wirelength +4.85 % on the corpus, detailed +0.01 %.
Whether a planner that priced the longer spans would choose differently is
not measured.

**The two failure modes point in opposite directions.**
- *Short spans* (the old behaviour) fail at DetailedNUTS: a bit snapped to its
  partner's track past the stub's end lands on another bundle's metal, a
  cross-bundle short (`DetailedNUTSResult.num_cross_shorts`).
- *Long spans* (the new default) are what overlaps the corner in
  `flow/nuts_corner_touch.buda`, which therefore declares
  `set_junction_reach off` itself.

**Consequence for an adaptive setting.**  "Start short, switch to long if
corner overlaps appear" would switch the wrong way.  The trigger that matches
the evidence is: run short, and if DetailedNUTS leaves cross-bundle shorts,
redo with long spans.  Two costs make it more than a flag: the span is decided
in abstract NUTS, so the redo is `run_nuts` + `run_detailed_nuts`, not
DetailedNUTS alone; and the switch can be whole-design (simple, but lengthens
bundles that were fine) or per bundle (needs a per-segment flag read by both
NUTS and DetailedNUTS, and pays wire only where a short was actually found).
Both would be accepted only when the redo has fewer shorts and no more
overlaps or unplaced bits, the same rule the heals use.  An experiment over
the corpus (every flow under short and under long spans, with the final
shorts, the bundles they sit on and per-bundle wirelength) is the way to size
the per-bundle saving before building it; its results go in the next section.

### Adaptive junction reach, measured (2026-10-15)

`tools/experiment/adaptive_reach.py` runs every corpus flow twice, short
spans (`set_junction_reach off`) and long spans (`on`, every design, bottom-up
included), recording the final overlaps / unplaced / violating bundles, the
cross-bundle shorts and the bundles they sit on, and per-bundle abstract
wirelength; `adaptive_reach_analyze.py` scores policies from that file.  One
sweep, 56 flows (`ariane133_heal` needs its fetched inputs and was not run),
healers on as each flow ships.  The two whole-design adaptive rules take the
long run only when it is componentwise no worse on (overlaps, unplaced,
viol_bundles) and strictly better on one.

| policy | overlaps | unplaced | viol | clean flows | abstract WL (sum) | shorts left |
|---|---|---|---|---|---|---|
| always short | 289 | 1393 | 135 | 43 | 36,649,322 | 15 |
| always long | 304 | 1536 | 152 | 46 | 38,287,457 | 5 |
| shipped default (long, except bottom-up marks) | 287 | 1359 | 131 | 45 | 38,144,904 | 11 |
| adaptive, trigger = short run left shorts | 288 | 1400 | 133 | 44 | 36,695,871 | 5 |
| adaptive, trigger = short run is dirty | 286 | 1359 | 131 | 46 | 36,724,714 | 11 |

- **Trigger on dirty, not on shorts.**  Only 5 flows leave shorts under short
  spans, and the shorts-only rule misses the flows that long spans help
  through overlaps (`mix2_fast_topdown` 1 -> 0 overlaps, `chip_stack_topdown`
  263 -> 235 unplaced): 1400 unplaced against 1359.
- **Dirty-triggered adaptive matches the shipped default on quality** (same
  unplaced and violating bundles, one fewer overlap, one more clean flow:
  `mix2_fast_bottomup` goes clean under long spans, which the default does not
  give a bottom-up design) **at 3.7 % less abstract wire** (36.72 M against
  38.14 M).  Nearly all of that wire is one flow: `rv/soc_conv_div`, clean under
  short spans, grows 1.36 M under long (8 %); the next three are 30 k each.
  Read it as "a clean design keeps its short wire", which is the point of the
  rule, not as a broad saving.
- **The accept rule is doing real work.**  Long spans are much worse on
  `mix2_fast_bottomup_shared` ((0,0,2) -> (4,80,10)) and
  `mix2_fast_bottomup_caps` ((2,0,0) -> (11,72,11)); blind "always long on a
  dirty design" would take those.  `chip_stack_bottomup` and
  `chip_bottomup` trade unplaced bits against shorts and are refused too.
- **Per bundle looks promising, unproven.**  In every flow whose short run
  leaves shorts, they sit on 2 bundles out of 80-660, and lengthening only
  those costs 53-188 abstract units against 1,408-29,260 for lengthening all
  (`tc3a` 53 against 1,408; `chip_topdown` 188 against 23,529;
  `mix2_fast_bottomup_shared` is the exception, 2,644 of 12,143, and long
  spans are worse there anyway).  This is the wire cost on the bundles
  concerned in the all-long run; it does not show that lengthening only them
  clears the shorts, nor how the packing reacts to a design with mixed spans.
- **Caveats.**  One run per cell and greedy healers, so single-unit differences
  on the chip flows are inside basin noise; the policies are scored from the
  two uniform runs, not from a built adaptive pass, so a real redo (which
  re-runs NUTS and DetailedNUTS and re-heals) can land elsewhere.

## Per-bundle adaptive junction reach (2026-10-16)

`set_junction_reach adaptive` (top-down designs only). Every bundle starts on
the SHORT spans. After `run_detailed_nuts`, if the result carries cross-bundle
shorts, `_final_reach_heal` (`nutsflow.py`) puts the bundles on those shorts
into a per-bundle set (`buda.set_junction_reach_bundles`, read by the four
reach sites in `nuts.cpp` and the promotion in `detailed_nuts.cpp` through
`junction_reach_on(bundle_id)`), re-runs NUTS + DetailedNUTS, and keeps the
trial only if opens strictly drop and overlaps do not rise; otherwise it
restores. The set is cleared by a fresh `run_nuts` and by any change of mode.
Bottom-up (`hier.locked`) sessions are scoped out, because long spans measured
much worse there; adaptive is then the short spans.

Corpus (56 flows, the same sweep as above; `tools/experiment/adaptive_reach.py
OUT.json adaptive`), against the short spans:

| flow | short (ovl/unpl/viol, abstract WL, shorts) | adaptive | long everywhere |
|---|---|---|---|
| big_data_test/tc3a | 0/0/2, 78019, 1 | 0/0/0, 78072 (+53), 0 | 0/0/0, 79430 (+1411) |
| chip/chip_topdown | 5/134/11, 2534991, 4 | 5/134/9, 2535520 (+529), 0 | 5/130/11, 2558512 (+23521), 1 |
| chip/chip_stack_topdown | 22/263/21, 2281361, 0 | 22/263/21, 2281637 (+276) | 21/235/19, +20244 |

Every other flow is byte-identical to the short spans, including the three
bottom-up flows that carry shorts (scoped out). So the per-bundle form clears
the shorts the whole-design form clears (tc3a, chip_topdown) at 2-5% of its
abstract wirelength cost. `chip_stack_topdown` reads no final shorts under the
short spans, yet its wire moved: a healer's intermediate DetailedNUTS carried
shorts there and the heal was accepted mid-flow, ending at the same final
QoR with slightly more wire. `ariane133_heal` needs fetched inputs and is
excluded.

## Flipping the default to `adaptive`? A/B on main (2026-10-19)

Today's default (`topdown`: far-edge spans on every top-down design, short
spans on bottom-up ones) against `set_junction_reach adaptive`, the same 56-flow
corpus on `f22fd507` (`tools/experiment/adaptive_reach.py OUT.json
topdown,adaptive`, 4 workers, so seconds are indicative only).

Totals over the flows that ran (ovl / unplaced / violating bundles, abstract WL,
detailed WL, seconds): topdown 287 / 1359 / 131, 38.14M, 821.69M, 1242 s;
adaptive 289 / 1393 / 131, 36.65M (-3.9 %), 821.52M (-0.02 %), 1338 s (+8 %).

- 37 flows differ, 33 of them only in abstract WL (shorter by 0.5-9 %, QoR triple
  identical, detailed WL equal or within a fraction of a percent).
- Better: `chip_topdown` (shorts 1 -> 0, violating bundles 11 -> 9, but 130 -> 134
  unplaced), `tc3a` (identical QoR, shorts already 0 under the default).
- Worse: `chip_stack_topdown` 21/235/19 -> 22/263/21; `chip3_topdown` 266 -> 268
  unplaced; `rnr/mix2_fast_topdown` 0/0/0 -> 1 overlap (a clean flow goes dirty) and
  6 s -> 52 s in the sweep (4.5 s -> 17 s alone: the heal's trial is rejected at a
  mid-flow DetailedNUTS, opens 112 -> 112, and the healers then run from the short
  spans' basin).  `bigHalf` 54 -> 85 s, `rnr/mix2` 23 -> 32 s.

Reading: adaptive buys abstract wire and no detailed wire, loses unplaced bits on
the chip flows and one clean verdict, and costs heal trials.  Not flipped.
