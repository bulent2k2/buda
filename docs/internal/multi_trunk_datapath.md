# Multi-trunk BITRUNK trees for datapath fan-out (`generate_topologies multi_trunk`)

Status: **v1 implemented (opt-in).** For high-fan-out nets over regular
datapath-like placements (rows/columns of aligned blocks) the naturally optimal
route is a **two-level trunk tree**, not one long single spine. `multi_trunk`
generates these as `BITRUNK_HVH` / `BITRUNK_VHV` candidates.

## Shape

- **Root spine** — one trunk (H for HVH, V for VHV), placed just outside the
  blocks on the low perp side so it never accidentally taps a block.
- **Branch trunks** — perpendicular to the root, one per leaf cluster, each
  T-joining the root (or sharing its endpoint).
- **Leaf connection** — a leaf either **stubs** to its branch (branch runs beside
  it) or is a **multi-tap pass-through** (the branch runs down through a column of
  collinear blocks and covers each — no per-block stub). This is what makes a
  datapath column route as a single trunk tapping every block.

`BITRUNK_HVH` = root H → V branches (receiver **columns**); `BITRUNK_VHV` = root V
→ H branches (receiver **rows**). Both orientations are always emitted; the
planner ranks by honest wirelength (`annotate_and_sort`).

## How it works (`src/topology.cpp::add_multi_trunk_candidates`)

1. Gated on the opt-in `set_multi_trunk` flag and `n >= 4` leaves.
2. **Cluster** leaves along the root axis (center coord), cutting at the `K-1`
   largest gaps — the natural columns/rows of a datapath. Tries `K = 2, 3`.
3. Per cluster, place a branch at the **mean center** of its leaves' root-axis
   coords. A leaf whose root-axis range straddles the branch is a pass-through;
   the rest get a stub. The branch's perp-span covers every cluster block's full
   extent (pass-through coverage) and reaches the root.
4. Emit only if the tree is a **clean tree** (`topology_is_clean_tree`:
   connected, acyclic, every block covered by a busterm or pass-through span);
   dedup identical trees across `K`.

Connectivity/slide-ranges are inferred by the existing `ConnTopology` (branch
T-junctions on the root, leaf T-junctions on branches). No new `Topology` fields.

## Wiring

- `TopologyGenerator::set_multi_trunk(bool)` (`src/topology.h`), bound in
  `src/bind_routing.cpp`, threaded from `buda_cli._make_topo_gen` when the
  `multi_trunk` keyword is present on `generate_topologies`.

## Tests

`test/tests/features/datapath_trunk.feature` + `test_datapath_trunk.py`: column
datapath → HVH, row datapath → VHV, multi-tap pass-through column, and the opt-in
guard (no BITRUNK without the flag). Each asserts a root-orientation trunk with
≥2 perpendicular branches, full connectivity, and no cycles.

## v1 scope & follow-ups

- **In:** H / comb shapes (K≥2 clusters) both orientations; multi-tap
  pass-through branch trunks; opt-in flat `generate_topologies`.
- **Follow-ups:** thread `multi_trunk` into `generate_topologies_for_bundle` and
  `generate_hier_topologies`; strengthen `multi_level_trunk.feature` (flip its
  MST-fallback/xfail asserts to strict BITRUNK once root-adjacent-to-src and
  depth-3 are guaranteed); a source-anchored root (root passes near the driver);
  ranking tie-break by slide flexibility (`topology_flexibility.feature`).
- **T-shape** (single perpendicular bar of receivers from a driver) is already
  covered by the existing single `TRUNK_H`/`TRUNK_V` (spine + driver stub); the
  new value is the ≥2-branch H/comb trees.
