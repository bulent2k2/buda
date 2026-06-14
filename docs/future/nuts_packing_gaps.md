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
CLI marks a BDB ancestor only when one of its descendants is actually loaded into
the floorplan, and accepts an `add_block … container` keyword). A **solid leaf
cell** (non-container) is a keepout for every LOW (non-TOP) layer:
`Floorplan::low_layer_keepouts(low_ids)` emits an implicit `KeepoutZone` per leaf
cell. The planner's band capacity (`rebuild_cuts_`) and abstract NUTS
(`solve_layer`/`repair_overlaps`) consume it directly; the CLI installs the same
leaf zones into the `RoutingGridStack` non-TOP layers before `run_detailed_nuts`,
so detailed routing avoids signal tracks over cells too. A LOW segment therefore
cannot route over a cell, while TOP layers (absent from `low_ids`) cross cells
freely.

A **container** is transparent: it is excluded from the `for_each_band` endpoint
clamp, so a segment inside it keeps its full extent and is charged across the
child-edge cuts it crosses — intra-container channel congestion now rises
monotonically with the number of LOW segments packed through it.

---

## 3. "Corner overlaps" from span stretching — PARTLY RESOLVED

### Problem

The planner scores a topology at its planned coordinates. NUTS places one layer,
then stretches the connected segments on the other layer to meet their placed
trunks (`do_span_adjustments`). Two stretched segments that were disjoint at plan
time can then collide — a **corner overlap**. When the colliding segments are
perp-locked (e.g. pinned to a block face), `repair_overlaps` cannot help: moving a
victim sideways is infeasible. The only cure is to reorder the *trunks* they hang
from — the **vertical constraint** of two-sided channel routing.

```
A single VERTICAL M5 track column (one x, shared by stubs a and b).
y runs upward; the horizontal H6 trunk a hangs from is shown by its y-level (┄┄).

        PLANNED  (planner's books)            AFTER NUTS  (trunk placed higher)
  y                                       y
 356                                     356  ┄┄┄  H trunk PLACED @356
      ┄┄┄  trunk planned @270                 █       (H congestion slid it up)
 340  ▒▒▒  b (anchored y340, grows down) 340   █▒▒ ◄ a now runs through 300–340
 320  ▒▒▒                                320   █▒▒   → CORNER OVERLAP with b
 300  ▒▒▒                                300   █▒▒     (same track, overlapping y)
      ···  free gap: a charged only            █
 280  ···  up to y=270                   280   █   ◄ a stretched to follow its trunk
 270  ┄┄┄  trunk planned @270            270   █      into bands b already occupies
 260  ███  a (anchored y230, grows up)   260   █
 240  ███   ends safely below b          240   █   a.span_hi: 270 → 356
 230  ███                                230   █
```

Worked example: `flow/nuts_corner_overlap.buda` — two buses pinned to Z-topos
whose first V-stubs share the x≈90 column; the lower-anchored stub's trunk must
take the lower track or the stubs cross.

### Implemented: lazy vertical-constraint resolution (`resolve_corner_overlaps`)

After `repair_overlaps`, a bounded pass runs only when spans were stretched:
1. Detect corner overlaps — `find_overlaps` pairs (now an O(n log n) per-layer
   sweep) with ≥1 member in the **stretched** set that `do_span_adjustments`
   reports.
2. Derive an ordering edge from each pair's **anchored (non-stretched) ends**: the
   segment anchored lower ⇒ its trunk must take the lower track.
3. Re-solve the trunk layer under the accumulated edges — `solve_layer` phase 0
   places constrained trunks in topological order, **bottom-edge packed**
   (`first_fit` from just above each predecessor) so successors have room.
4. **Stop & reverse**: keep the re-solve only while the total overlap count
   strictly drops; otherwise restore and stop. Bounded iterations; short-circuits
   when nothing stretched.

This clears the `nuts_corner_overlap.buda` overlap (and its 4 detailed-NUTS
unplaced bits → 0). The heuristic edge derivation is safe because the monotone
guard reverts any edge that doesn't help.

### Still open

The guard means genuinely **cyclic** vertical constraints (net A above B at one
column, B above A at another — NP-hard, needing a *dogleg* that splits a trunk
across tracks) are left as-is. Flow 10's ~4 residual abstract overlaps and the
bundle-47 reservation conflict are not resolved by this pass; a future dogleg /
joint-placement extension would be needed. **Effort:** medium→large.
