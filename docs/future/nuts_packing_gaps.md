# Planner→NUTS Packing Gaps

`flow/hbundles/10_chip_units_blocks_leaf.buda` (4-level hierarchy, 176 buses /
968 bits) ends with all 176 bundles planned STRICT (overflow-free books). The
residual abstract-NUTS track overlaps and unplaced detailed-NUTS bits each trace
to one of three systematic gaps at the planner/NUTS interface. The flow's test
(`test_10_four_level_scale_one_bundle_per_bus`) ratchets the residual counts so
regressions are caught.

Gaps 1 and 2 are now **resolved** (flow 10: abstract-NUTS overlaps dropped from
10 → 4, detailed-NUTS placements rose from 1112 → 1224). Gap 3 remains open and
is documented below with the chosen approach for when it is picked up.

Related context: the planner exports its chosen band per segment
(`BundleAssignment.seg_perp` → `BundleWrapper.seg_perp`), and NUTS uses it as the
preferred placement for segments free of face semantics — that closed the
"trunks pile into one band while charged bands sit empty" failure mode.

---

## 1. Band accounting ignores inter-bus pitch — RESOLVED

### Problem

The planner's band bookkeeping summed effective bus widths (`eff_bus_width`)
against band capacity. NUTS additionally separates distinct buses by
`track_pitch_`. A band the planner loaded to near its capacity with k buses
needs `(k−1) × pitch` more room than the books showed; placement failed, the
window repack failed for the same arithmetic reason, and the segment fell back to
the interval centre — a recorded overlap.

### Fix (implemented)

`CongestionPlanner` now carries `track_pitch_` (mirroring `NUTSEngine`, set by the
CLI from the same value handed to `run_nuts`). Each segment is charged
`eff_width + track_pitch_` (in `score_segment`, `cong_cost_segment`,
`collect_overflow_bands`, `apply_segment`, `apply_reservation`,
`plan_band_overlap`) and each band granted one free margin in
`usable_band_cap` (`cap + track_pitch_`). So k buses reserve the `(k−1)×pitch` of
separation NUTS enforces, while a single-bus band is unaffected. A physically
blocked band (cap 0 — e.g. a leaf-cell keepout, see Gap 2) stays a hard block:
`usable_band_cap` returns 0 there rather than `pitch`.

Covered by `test_inter_bus_pitch_reserved`; the dilution/slide/overflow-threshold
unit tests set `set_track_pitch(0.0)` to isolate their subject from the margin.

---

## 2. Non-TOP face clamping treated ancestor blocks as containment — RESOLVED

### Problem

`for_each_band` clamped a non-TOP segment's cut range to its endpoint-block faces;
a segment **fully inside** a block was treated as internally routed (zero cuts →
zero congestion). With hierarchical block import the depth-0 chip blocks contain
everything, so any segment within one chip rode non-TOP layers for free — even
though leaf/blk/unit blocks obstruct most of that area. Symmetrically, the band-
capacity model carved out the *whole* chip footprint, zeroing its internal
channels too.

### Fix (implemented)

Blocks now carry an explicit `is_container` flag (`Floorplan::set_container`; the
CLI marks BDB ancestors — any component with children — and accepts an
`add_block … container` keyword). A **solid leaf cell** (non-container) is a
keepout for every LOW (non-TOP) layer: `Floorplan::low_layer_keepouts(low_ids)`
emits an implicit `KeepoutZone` per leaf cell, consumed by the planner's band
capacity (`rebuild_cuts_`), abstract NUTS (`solve_layer`/`repair_overlaps`), and
detailed NUTS through one shared path. A LOW segment therefore cannot route over
a cell, while TOP layers (absent from `low_ids`) cross cells freely.

A **container** is transparent: it is excluded from the `for_each_band` endpoint
clamp, so a segment inside it keeps its full extent and is charged across the
child-edge cuts it crosses — intra-container channel congestion now rises
monotonically with the number of LOW segments packed through it.

---

## 3. Span adjustments stretch segments into uncharged bands — OPEN

### Problem

The planner scores a topology at its planned coordinates. NUTS places segments,
then stretches connected spans to meet placed positions (`do_span_adjustments`).
A detour stub planned as `y∈[230,270]` can end as `y∈[220,356]` after its trunk
slides — now crossing cuts/bands the planner never charged, and colliding with
buses that were disjoint at planning time. `repair_overlaps` fixes what it can
(per-move monotone guard), but a face window genuinely over-filled post-stretch
has no repair.

```
  Planned (planner books these bands):        After NUTS places + stretches:

  M4 trunk planned @ y=270 ───────┐           M4 trunk PLACED @ y=356 ──────────┐  (slid up:
                                  │                                             │   M4 congestion)
                                  │                                             │
  stub a  ▓▓ y∈[230,270]          │           stub a  ▓▓ y∈[230,356] ◄─ stretched to
          ▓▓                      │                   ▓▓                follow the trunk
          ▓▓                      │                   ▓▓
   bus b  ░░ y∈[300,340]  (disjoint           bus b  ░░ y∈[300,340]   ◄─ now OVERLAPS a
          ░░  from a at plan time)                   ░░                  in the y∈[300,340]
                                                                         band the planner
                                                                         never charged a for
```

The flow `flow/future/nuts_span_stretch_gap3.buda` sets up the detour + crossing
geometry and exercises the span-adjustment path; at 2-bus scale `repair_overlaps`
still rescues it. The unrepairable form appears at scale in
`10_chip_units_blocks_leaf.buda` (the 4 residual abstract overlaps and the
bundle-47 reservation conflict).

### Chosen approach: re-charge & verify

After `do_span_adjustments`, re-run the band books on the **final** geometry; any
band now over capacity triggers a targeted NUTS re-placement of the newest
contributor. The planner itself stays untouched.

**Efficiency constraint (required):** this must not slow NUTS in the common case.
Re-charge only the segments whose span actually changed during
`do_span_adjustments` (track the dirty set there) and the bands those segments
now touch — never the whole design — and short-circuit entirely when no span was
adjusted. The verify pass is then proportional to the (usually small) number of
stretched segments, not to the segment count.

**Alternatives considered:**
- *Bounded slide* — cap a follower's stretch to the Hanan band(s) its planner
  charge covered and let the trunk absorb the gap. Cheaper, but loses legitimate
  long stretches.
- *Joint placement* — place connected segments (trunk + stubs) as a group so
  spans never change post-placement. The principled fix, and the largest rewrite
  of `solve_layer`.

Validate: flow 10 M5 stub overlaps (`B43×B47`-class) disappear and the residual
overlap ratchet drops below 4; planner3/4/5, ripup1/2, 05–09 stay green.

**Effort:** medium.
