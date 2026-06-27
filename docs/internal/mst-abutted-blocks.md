# MST topologies miss abutted blocks

**Status:** fixed. Root cause and fix recorded here; repro
`flow/big_data_test/big2_b1_bus_007.buda` (issue "mst topos miss abutted blocks").

## Symptom

A bundle whose blocks **abut** (share an edge) routes with disconnected blocks:
`check_connectivity topo` reports `Block 'blk_NN' has no BUSTERM connection and no
pass-through segment`, and DetailedNUTS leaves those blocks' bits unconnected
(`Block 'blk_NN': 28 bit(s) — no pass-through/busterm connection`).

Repro (`big2_b1_bus_007.buda`): driver `blk_00`, receivers `blk_06, blk_33,
blk_17, blk_05, blk_21` (28-bit bus). Three pairs share an edge:

| pair | shared edge | overlap (the real edge extent) |
|---|---|---|
| blk_00 / blk_05 | y = 3780 (blk_00 bottom = blk_05 top) | x ∈ [1250, 2230] |
| blk_00 / blk_06 | x = 1250 (blk_00 left = blk_06 right) | y ∈ [4630, 4775] |
| blk_21 / blk_33 | y = 3645 (blk_21 top = blk_33 bottom) | x ∈ [3500, 4220] |

Pre-fix, the selected `MST_HV` left **blk_05, blk_06, blk_21** (each the abutted
partner) disconnected.

## MST generation code map (`src/topology.cpp`)

The flat topology generator builds MST-flavoured candidates in two places, both
fed by the same geometry helpers:

- **`add_mst_candidates`** (≈ line 1769) — standalone `MST_HV` / `MST_VH` for
  bundles with **N ≥ 4** blocks. Builds a Kruskal MST over closest-rect Manhattan
  distances (`rect_min_dist` → `manhattan_nearest`), then realizes each MST edge
  into segments, annotates, and runs `complete_relay_junctions`.
- **`add_trunk_mst_candidates`** (≈ line 2016) — `TRUNK_*+MST` hybrids: a trunk
  spine plus MST shortcut edges that replace a child's trunk stub when the edge is
  shorter. Edge realization lives in the local `realize_edges` lambda (≈ line 2189).
- **`closest_points(r1, r2, p1, p2)`** (≈ line 1112) — the shared geometry helper:
  the closest point pair between two rects, used to turn an MST edge (a block pair)
  into wire segments.
- **`complete_relay_junctions`** / **`topology_is_clean_tree`** — post-process a
  realized candidate: wire up relay junctions and (hybrids) gate on the result
  being one acyclic SEG-connected tree.

`ConnTopology` (`conn_topology.cpp`) then infers connectivity geometrically;
`verify.cpp` audits it (`check_topo`/`check_nuts`/`check_dnuts`).

## Root cause

For two rects that **abut** — touch on exactly one axis (`r1.x2 == r2.x1`) with
positive overlap on the other — `closest_points` takes the midpoint `else` branch
on **both** axes (the `<` comparisons are false on the touching axis, and the
rects overlap on the other), returning **`p1 == p2`**: a single point on the
shared edge. (Example: blk_00 / blk_06 → both points `(1250, 4702)`.) The real
shared edge is a *segment* spanning the overlap interval, but it is collapsed to a
point.

Both edge realizers then **drop** that edge:

```cpp
// add_mst_candidates and add_trunk_mst_candidates::realize_edges, pre-fix:
closest_points(r_u, r_v, p1, p2);
if (p1.x == p2.x && p1.y == p2.y) continue;   // abutment edge silently skipped
```

Abutting blocks have Manhattan distance 0, so their edges sort **first** in
Kruskal and are always chosen for the MST. Dropping such an edge removes a tree
edge, splitting the abutted block (or its subtree) into a disconnected component.
There is no fallback, so the orphaned block reaches neither a busterm tap nor a
pass-through wire → unplaced bits.

This is the routed-output face of the "touching blocks" degenerate class noted in
[topo-norm-phase2-deferred.md](topo-norm-phase2-deferred.md): zero-length /
sub-min-stub MST edges. Unlike deferred defects 2 & 5 (which only affect
*non-selected* candidates), this one disconnects a **selected** topology, so it
must be fixed rather than deferred.

## Fix

Realize an abutment as a real wire that **crosses** the shared edge, oriented
**perpendicular** to it, instead of dropping it. New helper next to
`closest_points`:

```cpp
static bool shared_edge_segment(const Rect& r1, const Rect& r2,
                                int h_layer, int v_layer, Segment& out);
```

- Shared **vertical** edge (rects touch on x, positive y-overlap) → a **horizontal**
  wire crossing it at the centre of the common y-span, spanning far-face to far-face.
- Shared **horizontal** edge (touch on y, positive x-overlap) → a **vertical** wire
  crossing it at the centre of the common x-span.
- Returns false for disjoint, corner-only (zero overlap on the touching axis), or
  fully coincident rects — those keep the original `continue`.

**Why perpendicular, not along the edge.** A wire's bits spread along its
perpendicular (track) axis, and its slide is bounded by the intersection of the
faces it connects. A wire laid *along* the shared edge has its track axis pointing
*across* the edge → pinned to the boundary line → **zero perpendicular slide**, and
`filter_pinched` then drops the whole candidate (a zero-width channel can't host a
multi-bit bus). A wire that *crosses* the edge has its track axis running *along*
the edge, so its slide equals the common span of the two faces (e.g. the x-overlap
for a horizontal abutment) and the bus's bits fan out across that span. Spanning
far-face to far-face puts both endpoints on real block faces (clean busterm taps).

Both realizers call it in the `p1 == p2` branch (`add_mst_candidates` also widened
the `closest_block_points` lambda to report the chosen rect pair). This also
**resolves the collinear internal-abutted-node case** (a straight A–B–C chain):
the perpendicular crossings of the two edges incident to the internal node overlap
and touch, so there is no pair of collinear stubs for `ConnTopology` to fail on —
no `FEEDTHRU_RELAY`. (That was the Codex P1 / topo-norm defect-2 family on the
along-the-edge version.)

**Min-stub:** a true abutment between blocks narrower than the min-stub floor
yields an inherently short crossing; the join is emitted anyway (connectivity wins
over the stub-length heuristic — min-stub is intentionally not enforced on
abutment/completion connectors). `test_min_stub_length_exhaustive` exempts stubs
within an abutting pair's footprint accordingly.

The change is surgical: non-abutting MST edges never enter the `p1 == p2` branch,
so every design without edge-sharing blocks is byte-for-byte unchanged (verified:
`tc3a_flat_x10` has zero abutting pairs and its routing is identical pre/post fix).

## Collinear butt-joint guard (Codex P1)

The perpendicular crossing introduces one new collinear case: when the crossing
meets a **regular** (non-abutment) MST edge **end-to-end** collinearly — e.g. A
abutted above B with B–C a regular V edge below at the same x — the two segments
share an endpoint but `ConnTopology::infer_connections` skips collinear pairs
(`conn_topology.cpp:156`), so it records no SEG join. NUTS would then place the two
independently and the subtree could detach. This is the deferred **defect 2**
(collinear-join) reachable through abutment; the old along-the-edge segment was
perpendicular to a collinear regular edge, so it didn't arise.

Two guards close the gap until collinear-join inference lands (proper fix):
- **`detect_feedthru_relay`** (`verify.cpp`) — its geometric `touch` now treats a
  pure collinear butt-joint (segments meeting only at a point) as *disconnected*
  (collinear OVERLAP still counts), so `check_topo` reports the open it previously
  missed.
- **`topology_is_connected` gate** (`topology.cpp`) — `add_mst_candidates` now
  drops a standalone MST that is not one connected SEG component (the planner's
  cost loop does not check connectivity, so a disconnected MST was otherwise
  selectable). Connectivity-only, so a connected-but-cyclic collinear *overlap*
  (the A–B–C chain) is kept.

`test_abutment_collinear_butt_joint_connects` is a strict xfail capturing the
underlying limitation; it XPASSes once the topology can be realized connected.

## Corner-diagonal edges (fixed — separate branch)

Distinct from abutment: two blocks can be **corner-diagonal** — their facing edges
overlap in only a single point (e.g. `big2_b3`'s blk_09 / blk_39 meet only at
x=4870, no span overlap). `closest_points` returns a straight edge pinned to that
single coordinate (zero slide), and `filter_pinched` dropped any candidate
containing it — so `big2_b3` had **zero** standalone `MST_HV`/`MST_VH`.

`corner_diagonal_L` realizes such an edge as an **L-shape around the corner** so
each leg taps a real face with slide. It is wired into **both** edge realizers —
`add_mst_candidates` (standalone MST) and `add_trunk_mst_candidates::realize_edges`
(the trunk+MST hybrid) — because a `<4`-block bundle has no standalone-MST fallback,
so a corner-diagonal branch shortcut would otherwise lose all TRUNK+MST coverage.
There are exactly two L's, and the MST_HV / MST_VH strategies (or the hybrid's
trunk orientation) select between them (H-first vs V-first), so both are generated
and the congestion planner picks per the rest of the topology.

**Which L, and why.** For blk_09 (lower-left) / blk_39 (upper-right):
- **L1** (V off blk_09's TOP, then H to blk_39's LEFT): taps TOP (x-extent 1370) +
  LEFT (y-extent 1060) → bottleneck slide **1060**.
- **L2** (H off blk_09's RIGHT, then V to blk_39's BOTTOM): taps RIGHT (y-extent
  530) + BOTTOM (x-extent 1230) → bottleneck slide **530**.

L1 is preferable: it taps the **longer faces**, so its tighter leg has ~2× the
track room (1060 vs 530) for the multi-bit bus; empirically L2 also trips the
deferred outlier-slide (defect 5) on its short-face leg. Legs run face-centre to
face-centre to maximise that room. `big2_b3` now generates 2 clean MSTs, 0 unplaced.

## Verification

- `./buda --no-viz flow/big_data_test/big2_b1_bus_007.buda`:
  `check_connectivity topo all` / `nuts` / `dnuts` all "Success: no opens found";
  `[DetailedNUTS] 140 net segments placed, 0 bits unplaced`. The standalone
  `MST_HV` / `MST_VH` candidates pass `check_connectivity topo all` (they failed
  pre-fix with the three disconnected blocks).
- `test/tests/test_topo_mst_abutted.py` — unit test (abutted geometry → MST
  connects all blocks) plus the end-to-end repro flow (0 unplaced).
- Whole-design effect: `big2.buda` (the full `tc3b_flat_x5`) drops from **60 to
  20** unplaced-bit report lines — the fix resolves abutment disconnections across
  the design, not only bundle 1. The remaining 20 are separate issues (congestion
  track-exhaustion and other repros such as `big2_b3_bus_023.buda`).
- Fast tier green; the only `-m "not slow"` failure
  (`test_tighten_does_not_trade_pull_for_overlaps`, abstract overlaps 3 > 2 on the
  big design) is **pre-existing** — it fails identically on `d3b5cb4` without this
  change, introduced by the test-data compaction in `35e1d413`, and is unrelated to
  MST generation.
