# Planner congestion fidelity — Gap A (LOW layers over cells)

Branch: `claude/planner-congestion-fidelity`. This note records the diagnosis of
why the bundle planner commits every bundle `overflow=0` yet DetailedNUTS (DNUTS)
leaves bits unplaced on `flow/big_data_test/big2/big2.buda`, the `detour_channel`
experiment that ruled out an escape-space fix, and the **Gap A part 1** fix that
landed here (LOW-over-cell blocking). Gap A part 2 and Gap C remain open.

## Symptom

On big2 the planner reports all bundles routed with no overflow, but DNUTS
reports **484 unplaced bits**. The 484 split cleanly into two failure modes:

| Failure | Layers | Bits | Cause |
|---|---|---|---|
| `insufficient signal tracks (0)` | M2/M3 (LOW) | 228 | LOW segment routed over a leaf cell — zero signal tracks there |
| `reservation conflict` (`N unreserved, need M`) | M6/M7 (TOP) | 256 | TOP band over-subscribed at the contended interval |

Both are the planner believing capacity exists where DNUTS finds none.

## The `detour_channel` experiment (no-code, ruled out)

Hypothesis: the design lacks escape space; reserving an outer detour band would
let bundles route around the hot interior channels. Result on big2:

| Condition | Unplaced |
|---|---|
| baseline | 484 |
| `detour_channel A 400` alone | 484 (**inert**) |
| `double_detour` alone | 776 (**worse**) |
| `detour_channel A {200,400,600}` + `double_detour` | 776 / 720 / 896 |

- `detour_channel` is **inert** without `double_detour`: no candidate topology
  routes into the reserved band, so it changes zero bits.
- `double_detour` is **net-negative**: it reshuffles planner selections wholesale
  (the OOB detour shapes get over-selected because the planner mis-costs them) and
  routes worse at DNUTS.

Conclusion: the bottleneck is not perimeter space, it is **planner congestion-model
fidelity**. Reserving space the model can't cost correctly doesn't help.

## Gap A part 1 — LOW layers over cells (FIXED)

### Root cause

A LOW (non-TOP) layer cannot route over a solid leaf cell — those track slots are
keepout. The planner already zeroes LOW band capacity over cells
(`low_layer_keepouts` → `band_available_length`). The leak was in
`CongestionPlanner::for_each_band`'s **endpoint-face clamp**.

That clamp exists to drop the in-cell *pin-access tail* of a block-attached stub
(a stub from a cell face into an open channel routes only in the channel; the
in-cell portion is pin access on another layer). But it iterated **all** blocks,
mutating the along-extent `[lo,hi]` as it went, with a `lo_in && hi_in → lo = hi;
break` "fully inside" short-circuit. For a segment that (a) lay wholly inside a
cell or (b) crossed a cell mid-span, the accumulation collapsed the *entire*
along-extent to nothing, so `for_each_band` charged **zero cuts** — the segment
looked completely **free** on LOW. When its TOP layers overflowed, the STRICT gate
then escaped to LOW, and DNUTS reported the bits unplaced.

Verified by instrumentation: for the failing big2 segments (e.g. bundle 24 seg 2
crossing `blk_14`, bundle 34 seg 1 wholly inside `blk_32`) the LOW layer scored
`cong=0, ov=0` at every perpendicular band, and `for_each_band` charged no cuts.

### Fix

`CongestionPlanner::low_seg_obstructed(seg, layer_id, perp)` returns true when a
non-TOP segment's routed extent — after excluding the pin-access tails at its two
endpoint cells — still lies over a leaf cell (mid-span crossing, or wholly
inside). `score_segment` and `cong_cost_segment` short-circuit to a hard overflow
(9999) for such a segment, so the STRICT gate treats LOW as blocked and the bus
routes over-the-cell on a TOP layer instead, accepting honest overflow rather than
a silent open. TOP layers always return false (they tile cells freely).

### Result on big2

| Metric | Pre-fix | Post-fix |
|---|---|---|
| Unplaced bits | 484 | **272** |
| `insufficient signal tracks (0)` (LOW-over-cell) | 7 warnings / 228 bits | **0** |
| M2 (LOW) NUTS track overlaps | 3 | **0** |
| Topo-stage violations | 0 | 0 |

Regression test: `test/tests/test_planner_low_over_cell.py` (mid tier) runs
`flow/big_data_test/big2/big2_noviz.buda` and asserts zero LOW-over-cell dumps.

## TOP-layer load balancing (NUTS overlaps)

After Gap A part 1 the residual failures were on the TOP layers: 41 NUTS track
overlaps (40 of them on M6/M7) and 272 DNUTS unplaced. Investigation showed:

- **Not capacity:** 35 of 41 overlaps fit within their assigned band (shared
  interval ≥ sum of the two bus widths) — the planner hands NUTS packable bands.
- **Not the `seg_perp` hint:** a prototype spreading the hint had no effect (NUTS
  overrides it with `net_pull` / repack).
- **Not the repair guard:** only 1 of 41 was the unrepairable both-pulled case,
  but relaxing `repair_overlaps` to accept plateau moves changed nothing — the
  victims' intervals are too full for a single-victim move to find a gap.
- **Root cause — layer load imbalance.** The planner breaks equal-cost ties
  toward the highest metal, and on a TOP layer with no span window the
  span/base costs are 0, so *every* H bus piled onto M6 and *every* V bus onto
  M7: M6 carried 4.7× M4's load, M7 4.8× M5's. Nearly all overlaps landed on the
  two overloaded layers.

**Fix:** a load-balancing tie-breaker (`kBalance_`, default 0.01). The per-layer
committed load is summed at each bundle's turn, and a cost term `kBalance ·
(layer_load / max same-direction layer load)` biases the choice toward the
less-loaded of the equal-cost same-direction TOP layers. LOW layers don't
compete (they already carry the base penalty). Because the load reflects only
committed bundles, it grows across the greedy schedule and steers later bundles
onto the layers earlier ones left empty.

**Result on big2:** NUTS overlaps 41 → 9, DNUTS unplaced 272 → 60, 0
LOW-over-cell, no topo violations. Loads even out (M4 5436 / M6 5832; M5 9117 /
M7 5752, vs the old 2178 / 10164 and 3609 / 17296). The balance also relieves
much of the Gap A part 2 TOP over-subscription as a side effect. The kBalance
plateau holds to ~0.015; above that, over-balancing pushes buses onto LOW layers
and unplaced climbs. Regression: `test_top_layer_load_balancing` in
`test/tests/test_span_layer_assignment.py`.

## Remaining (open)

- **Gap A part 2 — TOP capacity in averaged width.** The residual 272 are M6/M7
  `reservation conflict`s. The planner models a band's capacity as available
  *layout width* (`band_available_length`), but DNUTS places on discrete *signal
  tracks*; at a contended interval the per-track count is what binds. Until band
  capacity is modelled in signal-track count, these over-subscriptions commit
  `overflow=0` and never trigger the rip-up/replan ladder.
- **Gap C — per-band charge vs NUTS placement.** The planner charges each segment
  to the cheapest band in its slide window (`best_band_perp`), but NUTS pulls the
  segment to its `net_pull` extreme. A prototype that charged at the `net_pull`
  band backfired (484 → 2520) **because the STRICT gate's escape valve was the LOW
  layers** — tightening TOP just dumped more onto unusable M2/M3. Gap A part 1
  removes that escape valve, which is the precondition for Gap C to help. Gap A
  part 2 should precede Gap C as well.
