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

### `find_overlaps`: parallel vs end-to-end touch

Abstract segments are bit-bundles, not single wires, so the two ways two
distinct-net segments can touch differ (`segs_overlap`):
- **End-to-end** (collinear, same track, spans meet) — the bit ends butt up → a
  DRC. The span axis test is **closed** (touch counts).
- **Parallel** (side-by-side, track edges meet, spans overlap) — the bundles just
  sit edge to edge; intra-bundle spacing covers it → not a DRC. The perp axis test
  stays **strict**.

So `find_overlaps` flags spans-overlap-or-touch AND tracks-strictly-overlap. This
surfaces end-to-end touches the old strict test hid — including those from
**aligned trunks** on two layers (both trunks land at the same coordinate, so the
stubs meet end-to-end with no span stretching at all).

### Implemented: corner-overlap resolution (`resolve_corner_overlaps`)

After `repair_overlaps`, a bounded pass runs while overlaps exist:
1. Detect — `find_overlaps` pairs (O(n log n) per-layer sweep) where both
   segments are stubs hanging from **distinct trunks** (geometric criterion; no
   longer gated on a "stretched" set, since aligned trunks cause touches with no
   stretching).
2. Anchored-end rule picks the low/high trunk (stub anchored lower → lower trunk).
3. Constrain and re-solve the affected trunk layer(s):
   - **Same trunk layer** → relative ordering edge; phase 0 bottom-edge packs the
     trunks in topological order.
   - **Different trunk layers** → the trunks can't be track-ordered against each
     other, so each is nudged within its own layer to opposite sides of a split
     `S` (abstract gap `g = 1`): `lo_trunk ≤ S−g/2`, `hi_trunk ≥ S+g/2`, placed by
     `preferred_fit` toward `S`. `S = clamp(midpoint of current trunk positions,
     feasible window)`; an empty window (intervals can't separate them) is skipped.
4. **Stop & reverse**: keep a re-solve only while the total overlap count strictly
   drops AND no new interval violation appears; else restore and stop.

Clears `nuts_corner_overlap.buda` (same-layer), `nuts_corner_overlap_3layer.buda`
(3-layer, same trunk layer), and `nuts_corner_touch.buda` (cross trunk layer).
Flow 10 improved 4 → 1 abstract overlaps and 8 → 0 unplaced detailed bits.

### Still open

- **Detailed-NUTS spacing** for the cross-layer case: abstract uses `g = 1` only
  to set the side; the real separation should come from snapping each trunk to the
  nearest signal track on its own layer (per-layer track patterns). Follow-up.
- Genuinely **cyclic** vertical constraints (A above B at one column, B above A at
  another — NP-hard, needs a *dogleg* splitting a trunk across tracks) are left as
  is by the guard; flow 10's last residual overlap is this class. **Effort:**
  medium→large.
