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

Realize an abutment as a **real wire lying on the shared boundary**, spanning the
overlap interval, instead of dropping it. New helper next to `closest_points`:

```cpp
static bool shared_edge_segment(const Rect& r1, const Rect& r2,
                                int h_layer, int v_layer, Segment& out);
```

- Shared **vertical** edge (rects touch on x, positive y-overlap) → a V segment on
  the shared column `[oy_lo, oy_hi]` on the V layer.
- Shared **horizontal** edge (touch on y, positive x-overlap) → an H segment on the
  shared row `[ox_lo, ox_hi]` on the H layer.
- Returns false for disjoint, corner-only (zero overlap on the touching axis), or
  fully coincident rects — those keep the original `continue`.

Both realizers call it in the `p1 == p2` branch and push the returned segment
(`add_mst_candidates` also widened the `closest_block_points` lambda to report the
chosen rect pair so the helper has the actual rects). The segment lands on both
block faces, so `annotate_endpoints` / `ConnTopology` infer a busterm tap on each
and the tree stays connected.

**Min-stub:** the abutment join is emitted even when the overlap interval is below
the layer's min-stub floor. Connectivity wins over the stub-length heuristic at a
true abutment — a slightly short connector on the shared edge is correct; an open
is not.

The change is surgical: non-abutting MST edges never enter the `p1 == p2` branch,
so every design without edge-sharing blocks is byte-for-byte unchanged (verified:
`tc3a_flat_x10` has zero abutting pairs and its routing is identical pre/post fix).

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
