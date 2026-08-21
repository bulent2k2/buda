# A TRUNK+MST leg that overhangs a trunk stub

**Status:** option (b) LANDED behind `set_placed_endpoints` (default off, measured 3 better / 0 worse). Options (a) and the defect-1 residue are still open.
**Repro:** `flow/big_data_test/bigHalf_sel_bundle_only.buda` (bundle 67 / `bus_005`,
candidate 18 `TRUNK_H+MST@y1700`), with `bigHalf_sel_bundle_trimmed.buda` as its
control — same design, same topology, `seg2` cut back to its last attachment:

```bash
bin/buda --no-viz flow/big_data_test/bigHalf_sel_bundle_only.buda      # 23 violations
bin/buda --no-viz flow/big_data_test/bigHalf_sel_bundle_trimmed.buda   # Success

# and WHY, per segment and per bit:
python3 tools/show_stale_endpoints.py \
    flow/big_data_test/bigHalf_sel_bundle_only.buda bus_005
```

`tools/show_stale_endpoints.py` prints nominal span beside placed span for every
segment and flags each junction whose label disagrees with the geometry; the
violation counts it quotes are `check_dnuts`'s own, so it cannot report a
different number from the flow.

**Date:** 2026-08-20; option (b) built and measured 2026-08-21.

## The short version

The user's hypothesis was right, and the interesting part is *where* it is right.

The pinned candidate carries 1070 units of metal that connects to nothing. The
abstract route is nevertheless clean, because **NUTS repairs it**:
`tighten_spans_to_reach` contracts a span back to its outermost junction, so the
placed segment is exactly as long as it should be and abstract WL is identical
with or without the overhang (7932 either way).

That repair is what hides the defect, and it arrives too late to undo three
decisions taken on the un-repaired span — plus it leaves a fourth defect behind,
because it moves the geometry without refreshing the label DetailedNUTS reads.

| # | Where | Harm | Measured |
|---|---|---|---|
| 1 | generation | candidate WL estimate inflated by exactly the overhang, so the candidate is mis-ranked | 8930 vs a true 7860 |
| 2 | planner | congestion charged over the phantom span | seg2 charge 7050 → 2820 (2.5×), 103 → 85 bands |
| 3 | planner | span-scaled non-TOP penalty promotes the segment to a TOP layer it does not need | M7 vs M5 |
| 4 | DetailedNUTS | junction stays labelled *mid-span*, so no bit snaps to its own via | 23 of 48 bits, 654 units |

Only #4 is reported by any audit, and only at the very last `check_design`.

## The geometry

`dump_topologies bus_005 --conn`, candidate 18:

```
seg0  H M4  along[700,1050]  perp=1700    busterms: blk_34@face=700
                                          segs:     seg1@1050(end), seg2@1050(end)
seg1  V M5  along[1700,2770] perp=1050    busterms: blk_19@face=2770
                                          segs:     seg0@1700(end)
seg2  V M7  along[500,2770]  perp=1050    busterms: (none)
                                          segs:     seg0@1700(mid), seg3@500(end)
seg3  H M6  along[1050,6290] perp=500     busterms: io_pad_br@face=6290
                                          segs:     seg2@1050(end)
```

`seg2`'s only attachments are at y=1700 (`seg0`) and y=500 (`seg3`). It spans
y[500,**2770**]. The stretch y[1700,2770] therefore:

- attaches to nothing at its far end — no busterm, no junction;
- is **exactly coincident in plan with `seg1`** (both at x=1050, both spanning
  y[1700,2770]) — `seg1` on M5, `seg2`'s overhang on M7. Duplicate metal, one
  layer up;
- ends on `blk_19`'s face without tapping it: `seg1` owns that tap.

## Why it exists

`add_trunk_mst_candidates` builds the hybrid as *trunk spine + surviving trunk
stubs + realized MST legs*. The MST edge here is (`blk_19` → `io_pad_br`), and
`realize_mst_edge` routes each edge **from the block face**, not from the point
at which the tree already reaches that block. `blk_19` is already reached by its
trunk stub `seg1`, so the leg's first stretch retraces it.

This is precisely the shape `trim_shared_leg_overlaps` exists to remove — the
comment above it at `src/topology.cpp:3624` describes this exact harm, including
the mid-span-conn consequence, verbatim. It cannot reach this instance because of
*when* it runs:

```cpp
// src/topology.cpp — inside realize_edges()
if (allow_mst_leg_trim_) trim_shared_leg_overlaps(out, m_h, m_v);   // `out` = MST legs ONLY
...
// later, in the caller:
tree.segments = std::move(kept);                          // spine + surviving trunk stubs
for (const auto& s : edge_segs) tree.segments.push_back(s);   // MST legs joined here
```

The trim's input is the MST legs alone, before the trunk stubs are merged in. So:

- **MST leg vs MST leg** → covered by `set_trim_mst_legs`;
- **trunk stub vs trunk stub** → covered by `set_trim_trunk_stubs`;
- **MST leg vs trunk stub** (this case) → covered by neither.

Confirmed empirically: with either knob on, candidate 18 is still WL 8930 with 4
segments.

## Why no audit catches it before DNUTS

- `check_design topo all` → Success. The `ANTENNA` rule counts *distinct
  attachment positions* and `seg2` has two; #514's tap-overhang rule wants the
  piece to lie over a block the segment itself **taps**, and `seg2` taps nothing.
- `check_design nuts` → Success. By then `tighten_spans_to_reach` has contracted
  the placed span to [428,1754]; there is no overhang left to see.
- `check_design` (dnuts) → **23 violations**, per-bit, the only place it surfaces.

`scan_dangling.py`'s categories do not cover it either: it is neither a
single-connection stub (A) nor an unbounded slide window (B/C).

## Defect 4 in detail — the stale endpoint label

`is_endpoint` is derived from **nominal** coordinates, once, at analysis time:

```cpp
// src/topology_analysis.cpp
c.is_endpoint = (at_i == ci.along_lo || at_i == ci.along_hi);
```

At nominal, `seg2` spans y[500,2770] and the `seg0` junction sits at y=1700 —
interior, so `is_endpoint = false`.

`DetailedNUTSEngine` then reads that label:

```cpp
// src/detailed_nuts.cpp
// Endpoint connections snap their end of the wire to the connected bit's exact
// position ... Mid-span connections never gate the ends; they only require the
// span to keep covering the connected bit's position.
if (conn.is_endpoint) { ... }
...
// Ends with no endpoint conn (e.g. a BUSTERM face) keep the abstract span.
```

But NUTS has meanwhile contracted `seg2`'s placed span to **[428,1754]**, and
`seg0`'s placed track is **1754** — the junction is now exactly the segment's hi
end. The geometry says "endpoint"; the label still says "mid". So every bit keeps
the shared abstract end 1754 instead of snapping to its own partner bit's track:

```
Bundle 67: Seg 2 bit 0 ... spans [360.5,1754] but this bit only reaches [360.5,1702.5] — 0 + 51.5 dangling
Bundle 67: Seg 2 bit 1 ... spans [362.5,1754] but this bit only reaches [362.5,1704]   — 0 + 50   dangling
...
```

23 bits, 5.0–51.5 units each, 654 total. (The other 25 bits have partners *above*
1754; for them the "keep covering" rule extends the span exactly far enough, so
they are clean. Roughly half the bus by construction.)

This defect is **independent of the generation defect**: it fires whenever NUTS
contracts a span onto a junction that was interior at nominal, whatever produced
the overhang.

## Method

Controlled A/B via `edit_topology`, same candidate, only `seg2`'s span changed:

```
edit_topology 67 18
edit_set_span 2 500 1700     # -> "violations: none; components=1"
edit_commit pin
```

Four arms, all with `run_planner signal_tracks` → `run_nuts` → `run_detailed_nuts`:

| arm | seg2 span | layers | seg2 charge | detailed WL | `check_design` dnuts |
|---|---|---|---|---|---|
| A  | [500,2770] | planner-chosen (**M7**) | 7050 / 103 bands | 381306 | 23 violations |
| B  | [500,1700] | planner-chosen (**M5**) | — | 380664 | Success |
| A' | [500,2770] | forced M4/M5/M7/M6 | 7050 / 103 bands | 381306 | 23 violations |
| D  | [500,1700] | forced M4/M5/M7/M6 | 2820 / 85 bands | 380664 | Success |

A' vs D isolates the span (identical layers, identical abstract placement:
seg0 [700,3112], seg2 track 3112, seg3 [3112,6290] in both). A vs B isolates the
layer choice — the *only* input that changed is `seg2`'s nominal length, and the
planner moved it off a TOP layer.

The committed trimmed candidate reports **WL 7860**, which is the pool minimum —
candidates 1–6 are all 7860. So a trimmed candidate 18 would move from 18th to
tied-first in a WL-sorted pool of 33.

The same detector run over the whole bundle finds the overhang is systematic
across the family, each inflated by exactly its own dangling length:

```
b67 cand 18 TRUNK_H+MST@y1700  seg2 trail=1070  wl=8930 (true 7860)
b67 cand 15 TRUNK_H+MST@y1875  seg2 trail= 895  wl=8755 (true 7860)
b67 cand 14 TRUNK_H+MST@y2050  seg2 trail= 720  wl=8580 (true 7860)
```

## How common is it

Rare, but it does reach committed routes. A scan for the shape — a segment with
**≥2 attachments** (so not `scan_dangling.py`'s category A), a **bounded** slide
window (not B/C), and nominal metal past its outermost attachment that does not
cover a bundle block — over the QoR corpus:

All 48 flows, 150,755 candidates:

| | |
|---|---|
| candidates carrying an uncovered overhang | **1,383 / 150,755 (0.92%)** |
| bundles whose **selected** topology carries one | **5 / 6,261 (0.08%)** |
| nominal dangling metal in selected routes | **4,181 units** |

Present at candidate level almost everywhere — every `chip*` vehicle carries
145–173 of them — but it rarely wins selection. Which is itself suggestive:
defect 1 over-prices exactly these candidates, so part of the reason the selected
rate is two orders of magnitude below the candidate rate may be that the antenna
prices them out of contention. That is a hypothesis, not a measurement.

The five committed instances:

| flow | bundle | selected candidate | overhang |
|---|---|---|---|
| `big_data_test/bigHalf` | b7  | `TRUNK_H@y2120`     | seg3, 2000 |
| `big_data_test/bigHalf` | b19 | `TRUNK_H+MST@y3995` | seg4, 1400 |
| `big_data_test/bigHalf` | b16 | `TRUNK_H@y4730`     | seg3, 15 |
| `rnr/mix2_fast_on_aligned_sql` | b61 | `BITRUNK_H`   | seg0, 716 |
| `rnr/mix` | b80 | `TRUNK_V+MST@x1450` | seg2, 50 |

**Nominal overhang is necessary for defects 1–3 but not sufficient for defect 4.**
Cross-checking each against the engine's own per-bit audit:

- `bigHalf` → **56 per-bit ANTENNA findings / 1,226 units** on exactly
  **b7 seg3 (20 bits), b16 seg3 (28), b19 seg4 (8)** — segment-for-segment
  agreement with the scan, plus a separate #514 tap-overhang on b16 seg2.
- `rnr/mix` → **3 findings on b80 seg2** — again an exact match.
- `rnr/mix2_fast_on_aligned_sql` → **zero**. The nominal overhang is real (716
  units, and it is still charged and still ranked on), but its junction does not
  end up mid-span after placement, so the bits snap normally.

So four of the five committed instances carry the per-bit consequence and one
does not. Two of the five are `TRUNK+MST` hybrids; the rest are plain `TRUNK_H`
and a `BITRUNK_H`, which matters for the choice of fix below.

## Options

Two independent fixes; they address different rows of the table.

**(a) Re-run the trim on the assembled tree** (fixes 1, 2, 3 — for the hybrid
path only). Call `trim_shared_leg_overlaps` on `tree.segments` after the stubs
and legs are merged, before `annotate_endpoints`. Cheap and local. It must stay
**opt-in on the existing `set_trim_mst_legs` knob** for the reason already
documented for that knob: the cut re-sorts the WL-ordered pool, so it moves
selection well beyond the trimmed bundle, and it renumbers candidate indices,
invalidating `select_topology` pins taken from a non-opted-in run.

Note its reach is narrower than the problem: only 2 of the 5 committed instances
are `TRUNK+MST`. The plain-`TRUNK_H` and `BITRUNK_H` cases come from other
generators and would need their own equivalent — which is the same lesson
`set_trim_mst_legs` and `set_trim_trunk_stubs` already teach, now a third time.

**(b) Re-derive `is_endpoint` against the placed span** (fixes 4, generally).
After `tighten_spans_to_reach`, a conn whose position equals the placed
`span_lo`/`span_hi` is an endpoint whatever the nominal geometry said. This is
the smaller and safer change: it does not touch candidate ranking, and it removes
per-bit dangling metal for *every* shape that produces this pattern, not just
this one. It is also the more honest one — the abstract repair and the per-bit
label currently disagree about the same wire.

Recommendation: **(b) first, on its own, and measure.** It is a correctness fix
with no selection-space movement, it is generator-agnostic (it would cover all
four of the committed instances that carry per-bit dangling metal, whatever shape
produced them), and it removes the disagreement between the abstract repair and
the per-bit label rather than papering over it. (a) is a QoR change, reaches only
the hybrid subset, and needs the full corpus treatment the sibling trim knobs
got.

Worth stating plainly: **neither fixes defect 1 in general.** The WL estimate is
computed on nominal geometry, and NUTS's contraction — which is what makes the
estimate wrong — happens long after selection. Any candidate whose realized
length differs from its nominal one is mis-ranked by the same mechanism; that is
the territory `kWLSpread` already occupies, and the overhang is a case where the
gap is not a realization risk but a certainty.

---

## As built (2026-08-21): `set_placed_endpoints`

Option (b) landed, default **off**, as a `.buda` token
(`set_placed_endpoints [on|off]`) with `BUDA_DNUTS_PLACED_ENDPOINTS=1` as the
corpus-sweep seed. The change is 20 lines inside `make_bus_segments`, the one
stage-4 → stage-9 handoff.

**Only the stage-9 descriptor moves.** `BusSegmentConn::is_endpoint` is rebuilt
on every call; `ConnSeg::is_endpoint` — cached on the `Topology`, shared with
generation, the planner and the topo-stage audit — stays nominal. The struct
already mixed the two: `bs.span_lo/span_hi` are copied from the placed
`TrackSegment` twenty lines above the loop that copied the nominal label.

A **module flag**, not a `make_bus_segments` parameter: that function is called
from the CLI, from `trial_sweep`, and from every healer re-solve, so a
parameter would let one caller drift and make a trial's verdict disagree with
the commit's.

### Promote only — the half that was measured and rejected

The first prototype was symmetric: promote mid→endpoint *and* demote
endpoint→mid when placement moved the span end past a nominal endpoint. That
was added for tidiness, not from evidence, and it is wrong. Clearing
`has_ep_*` makes that end eligible for the tapered retraction to `pres_*`
(`detailed_nuts.cpp`), which cuts the wire short of a partner it still has to
meet — the later `cover` pass does not save it.

```
# symmetric rule, big_3bundles_sel_pure_mst_topo:
Bundle 1: Seg 9<->10: 12 bit(s) — segment disconnected
Bundle 1: Seg 8: 12 bit(s) — dangling metal past its own attachments
```

A real open, strictly worse than the dangling metal it removes. Corpus-wide:
**1 better, 14 worse**, detailed WL **up** 23,233. Only the promotion is
justified by the diagnosis — a junction sitting on the placed end *is* that
end, and snapping there can only shorten a bit back to a via it already owns.

### Measured

| | overlaps/unplaced/viol_bundles | abstract WL | detailed WL |
|---|---|---|---|
| knob off vs `main` | 0 better, 0 worse, 48 unchanged | +0 | +0 |
| knob off vs on | **3 better, 0 worse, 45 unchanged** | +0 | **−1,114** |

`bigHalf` 0/0/3 → 0/0/1, `rnr/mix` 0/0/1 → 0/0/0,
`chip/chip_stack_bottomup` 83/221/21 → 83/221/20. Exactly three flows move and
every other flow is identical on all five numbers; abstract WL is unchanged by
construction, which is itself a check that the fix did not leak upstream.

`bigHalf` goes 3 → 1 rather than 3 → 0 because one of its three bundles also
carries the separate #514 tap-overhang on `b16 seg2`, which this does not
claim to touch.

Runtime is **not** quoted: `qor_corpus.py` runs flows in parallel, so its
per-flow seconds are contended. A runtime claim needs `tools/runtime_ab.py`.

### Toward the flip

The case for default-on is that this is a correctness fix measuring
3-better/0-worse with no selection-space movement. Before flipping:

- `tools/runtime_ab.py` on the chip vehicles — the parallel sweep hinted at a
  gain but cannot support the claim.
- A hier/bottom-up check specifically: a locked template's bits are COPIED to
  siblings, so a label change on the reference must not desynchronise copies.
  `chip_stack_bottomup` improving is encouraging, not proof.
- Decide whether `viol_bundles` improving on three flows is worth changing
  every existing flow's detailed output at all — the corpus gate says yes, but
  the flip is a methodology call, as with the `metal` default study.

Regression tests: `test/tests/test_placed_endpoints.py` (10 tests) — the
defect reproduces with the knob off, clears with it on, the junction really is
the placed end, the label flips only with the knob, a nominal endpoint is
**never** demoted (with the premise asserted, so the test cannot pass
vacuously), the flow the symmetric rule broke gains no violation kind, and the
command's default/parse behaviour.
