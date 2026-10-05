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
  1345.5, because the tracks at 1401..1417.5 are held by bundle 392's M4 trunk
  (spans overlap on x 213..291), whose own bits had spilled the same way.  The
  3 far bits make 446 seg 2's M5 bits stretch 380 units, onto tracks 392 seg 0
  holds.  The M4 channel is packed with no slack, so one spill cascades.

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
