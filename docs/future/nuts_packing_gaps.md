# Planner→NUTS Packing Gaps

`flow/hbundles/10_chip_units_blocks_leaf.buda` (4-level hierarchy, 176 buses /
968 bits) ends with all 176 bundles planned STRICT (overflow-free books). The
residual abstract-NUTS track overlaps and unplaced detailed-NUTS bits each trace
to one of four systematic gaps at the planner/NUTS interface. The flow's test
(`test_10_four_level_scale_one_bundle_per_bus`) ratchets the residual counts so
regressions are caught.

Gaps 1 and 2 are now **resolved** (flow 10: abstract-NUTS overlaps dropped from
10 → 4, detailed-NUTS placements rose from 1112 → 1224). Gap 3 is largely
resolved too: span-stretch corner overlaps, the cross-trunk-layer case, and now
genuinely **cyclic** vertical constraints (via a dogleg) are handled; the
remaining open items are noted at the end of §3. Gap 4 (a non-TOP pin-access stub
span-stretched onto its endpoint leaf) is **open** — diagnosed, with the reason a
planner-side cost term cannot fix it and the NUTS-side options in §4.

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

### Detailed-NUTS spacing for the cross-layer case — RESOLVED

Abstract NUTS uses only `g = 1` to set each trunk's side of the split; the real
separation comes from the per-layer signal-track grids.  When two trunk layers
have an *aligned* signal track, detailed NUTS used to snap both trunks to the same
coordinate, so the two M5 stubs met end-to-end — a bit-level short detailed NUTS
didn't report (it only counts unplaced bits).

Fix: `resolve_corner_overlaps` records the committed cross-layer split bound on the
trunk `TrackSegment`s (`track_lo_bound`/`track_hi_bound`); the CLI carries them onto
the `BusSegment`s; detailed NUTS filters each trunk's `signal_tracks` to its bounded
side before snapping.  The trunks land on disjoint real tracks → the stubs no longer
meet.  If a side genuinely lacks `bit_width` tracks the bits are reported **unplaced**
(an honest spacing failure) rather than silently shorted.  Verified by
`test_detailed_nuts_xlayer.py` (no same-track/overlapping-span stub pair on
`nuts_corner_touch`, 0 unplaced).

### Cyclic vertical constraints (dogleg) — RESOLVED

A genuinely **cyclic** vertical constraint (trunk A must sit above B at one column
but below it at another — generally a directed cycle A→B→C→…→A) admits no single
track ordering, so the corner pass gives up at its `!new_edge` guard. The cure is
a **dogleg**: split one trunk on the cycle across two tracks joined by a
perpendicular jog, so its two pieces become *independent* trunks that straddle
their neighbours, breaking the cycle.

Implemented (`nuts.cpp`):
- **Detection** (`detect_dogleg_plans`) builds the same-layer vertical-constraint
  graph from co-located stub pairs — co-location by the stubs' **Hanan interval**
  (the column they're constrained to), so it is placement-independent and catches
  two wide multi-bit stubs that share a narrow column even when NUTS shifted them
  to different tracks. A directed cycle is found by DFS (handles 2-cycles and
  longer); one split plan is emitted per trunk on the cycle, carrying the full
  cycle's ordering edges.
- **Resolution** runs after the corner pass on a small residual (heavy congestion
  is the planner's job). It tries splitting each trunk and keeps the cheapest:
  the trunk's slide window must host two sub-trunks, then the **longest span** wins
  (more room for the jog), tie-broken on a shorter jog. `apply_dogleg` mutates the
  selected `Topology` (split trunk + extend its stubs + jog, marked `is_jog` so it
  is exempt from sibling alignment), and the **full cycle order** is seeded as
  same-layer `LayerConstraints` (the split trunk redirected to its covering piece),
  since the corner pass can't impose a 3+-trunk chain.
- **Detailed NUTS adoption**: the dogleg-mutated topologies are exported
  (`NUTSResult::dogleg_topologies`) and adopted by the CLI before it rebuilds
  `ConnTopology`, so the split bundle's stubs route with correct (post-split)
  connectivity instead of corrupted spans that would short on shared tracks.

Verified by `test_nuts_dogleg.py`: `flow/nuts_dogleg_cycle.buda` (2-cycle) and
`flow/dogleg1.buda` (3-cycle) each reach 0 abstract overlaps, 0 unplaced detailed
bits, and 0 bit-level shorts.

### Still open

- The **alternating orientation-group fixpoint** (solve all H, propagate spans,
  solve all V, iterate) is scaffolded but only adopts a sweep when it strictly
  reduces overlaps without straying from planner-reserved bands; a sweep is judged
  on raw overlaps, which is unsound because the repair/corner passes run after it.
  A sound version must compare POST-cleanup overlaps. **Effort:** medium.
- Cycles whose stubs are **cross-layer** (the two trunks on different layers) are
  handled by the existing `g=1` split + carry-bound, not the dogleg; a dogleg for
  the cross-layer cyclic case is not implemented. Flow 10's last residual overlap
  is not a clean same-layer cycle the detector catches (it remains at 1).

---

## 4. Non-TOP pin-access stub span-stretched into its endpoint leaf — OPEN

### Problem

On some hosts flow 10 leaves **DetailedNUTS opens** that trace to a cross-block
bus's vertical stub landing ON its own endpoint leaf cell. Concretely, bus
`x_t4` (left/u4/bt/hi → right/u4/bt/lo, a cross-chip bus) selects `U_VHV`: an M6
(TOP-H) trunk with two vertical stubs dropping into the endpoint leaves
`left/u4/bt/hi` = (1490,340)-(1600,470) and `right/u4/bt/lo` = (3250,340)-(3360,470).
The generator hints **M5 (TOP-V)** for those stubs — TOP layers tile leaf cells
freely — but the planner **downgrades them to M7 (non-TOP-V)** to save the
span-scaled `base_cost_non_top` (a short stub is cheap on a low layer). On M7 the
endpoint leaf is a keepout. NUTS then span-stretches the stub to follow the trunk
(placed at y≈356, INSIDE the leaf's y[340,470]); the M7 stub's extent lands on the
leaf, `verify` flags `KEEPOUT_CROSS`, and DetailedNUTS culls the pin-access bits —
a silent open (≈22 bits across the `x_t*` buses).

### Why it is host-sensitive

The M5-vs-M7 layer choice for these stubs is a near-tie in the planner's float
score; `-march=native` codegen tips it differently per CPU. On the golden host
the stubs stay on M5 (clean); on other x86-64 hosts they drop to M7 (opens). This
is why `test_10_four_level_scale_one_bundle_per_bus` needs a host-tolerant gate
(`BUDA_NUTS_GOLDEN_STRICT`, PR #281) — the exact residual is environmental.

### Why a planner-side cost term does NOT fix it cleanly

The planner scores each segment's **nominal** geometry; the defect is a
**post-placement** event (NUTS span-stretch pulling the stub into the leaf), which
the planner cannot see. Two attempts confirmed this:
- A penalty that fires only when the nominal stub extent OVERLAPS a leaf never
  triggers — the nominal stub merely *touches* the leaf face (e.g. seg (1595,470)
  on the leaf's y=470 edge), so the crossing is invisible pre-NUTS. Opens stay 22.
- A penalty on any stub that *touches* a leaf (inclusive bounds) over-fires: it
  cannot distinguish a normal pin-access stub tapping a block face (fine on a LOW
  layer — this is the `low_seg_obstructed` endpoint-tail trim, Gap A, that keeps
  the 80 intra-blk local buses on low layers) from the `x_t4` stub NUTS later
  stretches in. It pushes many stubs to TOP (net segments 1194→1282, matching the
  blunt global `base_cost_non_top` knob) and trades the 22 keepout opens for ~6
  different packing-gap opens — worse than the clean baseline and broadly
  golden-churning.

So this is NOT a planner cost-model gap; the real fix locus is NUTS-side.

### Fix options (not implemented)

1. **Span-stretch clamp (preferred).** When NUTS span-adjusts a *non-TOP*
   segment, do not stretch its extent onto a leaf keepout on its layer — clamp at
   the leaf face. The bit places at the face and vias to the trunk (which is on a
   TOP layer and crosses the leaf freely). Localized to the span-adjust path,
   gated on non-TOP + keepout. **Effort:** medium; touches the same span-adjust
   code that closed big2's strand, so needs the full golden + fast/mid re-verify.
2. **Respect the generator's TOP hint.** Stop the planner downgrading a
   generator-hinted-TOP stub that lands a pin inside a leaf to a non-TOP layer.
   Cleaner intent, but the "lands a pin inside a leaf" test hits the same
   nominal-geometry over-firing as above and must be narrowed by connectivity.
3. **Trunk placement.** Keep NUTS from placing a trunk over an endpoint leaf when
   a non-TOP stub connects to it — the deepest change, most regression risk.

Until one lands, the residual is bounded and reported (`KEEPOUT_CROSS` +
`placed ON keepout` + the DNUTS cull warning — never silent) and the flow's test
tolerates it off the golden host.
