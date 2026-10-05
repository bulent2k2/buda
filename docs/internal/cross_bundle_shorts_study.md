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
