# A TRUNK+MST leg that overhangs a trunk stub

**Status:** analysis only — nothing changed in the engine.
**Repro:** `flow/big_data_test/bigHalf_sel_bundle_only.buda` (bundle 67 / `bus_005`, candidate 18 `TRUNK_H+MST@y1700`).
**Date:** 2026-08-20.

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

> **Sweep still running at time of writing: 25 of 48 flows complete** (the
> remaining ones are the heavy `chip*` / `rv` / `def` vehicles, and 6 flows are
> skipped because they call `exit`). Numbers below are partial and will move.

| | |
|---|---|
| candidates carrying an uncovered overhang | 129 / 20,744 (0.6%) |
| bundles whose **selected** topology carries one | 4 / 966 (0.4%) |
| nominal dangling metal in selected routes | 3,465 units |

Concentrated rather than spread: `bigHalf` (3 selected bundles, 3,415 units) and
`rnr/mix` (1 bundle, 50 units).

The detector agrees with the engine's own per-bit audit where both can see the
shape. `bigHalf`'s final `check_design` reports **56 per-bit ANTENNA findings /
1,226 units** across exactly the three bundles the detector flags (b7 seg3 — 20
bits, b16 seg3 — 28 bits, b19 seg4 — 8 bits), plus one separate #514 tap-overhang
on b16 seg2. So this is not a `bus_005` curiosity: a corpus flow ships it.

Note also that the two other affected `bigHalf` bundles are **not** `TRUNK+MST`
hybrids (b7's selected candidate is a plain `TRUNK_H@y2120`), so the generation
half of the fix — option (a) below — would not reach them, while option (b)
would.

## Options

Two independent fixes; they address different rows of the table.

**(a) Re-run the trim on the assembled tree** (fixes 1, 2, 3). Call
`trim_shared_leg_overlaps` on `tree.segments` after the stubs and legs are
merged, before `annotate_endpoints`. Cheap and local. It must stay **opt-in on
the existing `set_trim_mst_legs` knob** for the reason already documented for
that knob: the cut re-sorts the WL-ordered pool, so it moves selection well
beyond the trimmed bundle, and it renumbers candidate indices, invalidating
`select_topology` pins taken from a non-opted-in run.

**(b) Re-derive `is_endpoint` against the placed span** (fixes 4, generally).
After `tighten_spans_to_reach`, a conn whose position equals the placed
`span_lo`/`span_hi` is an endpoint whatever the nominal geometry said. This is
the smaller and safer change: it does not touch candidate ranking, and it removes
per-bit dangling metal for *every* shape that produces this pattern, not just
this one. It is also the more honest one — the abstract repair and the per-bit
label currently disagree about the same wire.

Recommendation: (b) first, on its own, and measure; it is a correctness fix with
no selection-space movement. (a) is a QoR change and needs the full corpus
treatment the sibling trim knobs got.
