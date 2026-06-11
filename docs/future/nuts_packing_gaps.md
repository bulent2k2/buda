# Future Enhancements: Planner→NUTS Packing Gaps

`flow/hbundles/10_chip_units_blocks_leaf.buda` (4-level hierarchy, 176 buses /
968 bits) ends with all 176 bundles planned STRICT (overflow-free books) yet
~10 abstract-NUTS track overlaps and a handful of unplaced detailed-NUTS bits
(~0.8%). Each residual traces to one of three systematic gaps at the
planner/NUTS interface, recorded here in priority order. The flow's test
(`test_10_four_level_scale_one_bundle_per_bus`) ratchets the residual counts
so regressions are caught while these remain open.

Related context: the planner already exports its chosen band per segment
(`BundleAssignment.seg_perp` → `BundleWrapper.seg_perp`), and NUTS uses it as
the preferred placement for segments free of face semantics — that closed the
"trunks pile into one band while charged bands sit empty" failure mode. The
gaps below are what's left.

---

## 1. Band accounting ignores inter-bus pitch

### Problem

The planner's band bookkeeping sums effective bus widths (`eff_bus_width`)
against band capacity. NUTS additionally separates distinct buses by
`track_pitch_`. A band the planner loads to near its capacity with k buses
needs `(k−1) × pitch` more room than the books show; placement fails, the
window repack fails for the same arithmetic reason, and the segment falls
back to the interval centre — a recorded overlap.

Observed in flow 10: four buses (two 34-wide cross-chip trunks + two 17-wide
blk-local hops) booked into the 120-wide bb-leaf band; sum 102 ≤ 120, but not
packable with pitch.

### Proposed enhancement

Charge each segment `eff + pitch_margin` in `apply_segment`/`score_segment`
(e.g. `pitch_margin = track_pitch` for every bus after the first in a band —
approximated by always adding it and granting each band one free margin:
`cap_eff = cap + pitch`). Keep the books symmetric in `collect_overflow_bands`
and `best_band_perp`. Validate: flow 10 M6 leaf-band overlaps disappear;
planner3/4/5, ripup1/2, 08, 09 stay green.

**Effort:** small-medium — the subtlety is not double-penalizing single-bus
bands (planner3's window-capacity expectations are sensitive).

---

## 2. Non-TOP face clamping treats ancestor blocks as containment

### Problem

`for_each_band` clamps a non-TOP segment's cut range to its endpoint-block
faces; a segment **fully inside** a block is treated as internally routed
(zero cuts → zero congestion). With hierarchical block import
(`add_blocks_from_bdb 0` … `3 skip`), the depth-0 chip blocks contain
everything, so any segment within one chip rides non-TOP layers for free —
in flow 10 several cross-chip trunks dropped to M4 "inside" the chip union
even though leaf/blk/unit blocks obstruct most of that area.

### Proposed enhancement

Clamp only against blocks that contain the segment's **endpoints exclusively**
(true endpoint blocks): skip blocks in `blocks_cache_` that strictly contain
other blocks (ancestors), or — cleaner — let the CLI mark imported ancestor
blocks (`Floorplan::add_block(..., is_container=true)`) and have both the
band-capacity exclusion and the face clamp ignore containers. Validate with a
flow where a low-layer trunk must price the leaf-level obstructions it
crosses.

**Effort:** medium — touches the floorplan API and both capacity paths.

---

## 3. Span adjustments stretch segments into uncharged bands

### Problem

The planner scores a topology at its planned coordinates. NUTS places
segments, then stretches connected spans to meet placed positions
(`do_span_adjustments`). A detour stub planned as y∈[230,270] can end as
y∈[220,356] after its trunk slides — now crossing cuts/bands the planner
never charged, and colliding with buses that were disjoint at planning time.
`repair_overlaps` fixes what it can (per-move monotone guard), but a face
window genuinely over-filled post-stretch has no repair.

### Proposed enhancement

Options, roughly in order of increasing fidelity:
- **Bounded slide:** limit a follower's stretch to the Hanan band(s) its
  planner charge covered; let the *trunk* absorb the remaining gap (trunks
  usually have wide windows).
- **Re-charge & verify:** after span adjustment, re-run the band books on
  final geometry; bands now over cap trigger a targeted NUTS re-place of the
  newest contributor (planner stays untouched).
- **Joint placement:** place connected segments as a group (trunk + stubs)
  so spans never change after the fact — the principled fix, and the largest.

Validate: flow 10 M5 stub overlaps (`B43×B47`-class) disappear; 05–07 stress
flows improve or hold.

**Effort:** medium (bounded slide) to large (joint placement).
