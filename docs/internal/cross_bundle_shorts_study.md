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
