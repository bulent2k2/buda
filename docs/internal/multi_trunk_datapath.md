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

### Legacy single-level ladders (`BITRUNK_H`, `BITRUNK_V`)

Beside the two-level trees, `add_multi_trunk_candidates` also emits the **legacy
single-level ladder** — two parallel rung trunks + one perpendicular backbone,
each block stubbing to its nearer rung:

- **`BITRUNK_H`** (two H rungs + a V backbone) is **always on** (grandfathered):
  the default candidate set is unchanged.
- **`BITRUNK_V`** (two V rungs + an H backbone — the previously-missing mirror,
  see `topology_tree_gen_design.md`) is **opt-in under `multi_trunk`**, alongside
  the two-level trees. It fills the row-of-receivers case `BITRUNK_H` cannot, but
  is a **QoR net-negative on-by-default** (corpus: unplaced +594, runtime +35% —
  like `BITRUNK_H` it is realization-fragile and the planner over-selects it), so
  it does not join the always-on path.

Both ladders are written once against the `Axis` abstraction and emitted in both
orientations (`emit_legacy_bitrunk(rungs_horiz)`); the H path is byte-identical
to the historical hard-coded shape.

### Anchoring gate (`filter_unanchored_bitrunk`)

A legacy ladder covers a block either by a real perpendicular **stub** (an
anchored busterm tap) or — where a block face lands on a rung — by a **free
sliding graze** with no tap. A *fully-degenerate* ladder (NO endpoint block
tapped at all) is covered only by grazes: it passes `check_topo` at nominal but a
NUTS-time slide moves every rung off its blocks, opening every bit at
DetailedNUTS (bigHalf **bus_038**: a `0/0` route that is electrically open at all
four endpoints). The generation gate (run at the uniform coverage gate, after
`filter_uncovered`) drops such a candidate **when a clean alternative survives**,
so the planner falls to an anchored shape. Scoped to the exact legacy types
(`BITRUNK_H`/`BITRUNK_V`); the two-level trees keep their own
`topology_is_clean_tree` gate (their column pass-throughs are legitimate
multi-taps). Corpus: bus_038 + wide_fan_stress go `0/0/1 → 0/0/0`; totals
net-neutral (overlaps +1, unplaced +30, viol_bundles +0) — it converts the
silent-open failure mode into clean routes.

> The gate triggers only on the *provably-broken* fully-degenerate case. The
> general discriminator — does the whole bit-band fit inside the endpoint block?
> — is a NUTS-time realization property a generation-time geometric gate cannot
> decide, so a partially-stubbed ladder is deferred to NUTS rather than risk
> dropping a routable small-bus column.

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

- **`test/tests/features/datapath_trunk.feature` + `test_datapath_trunk.py`**
  (pytest-bdd): column datapath → HVH, row datapath → VHV, multi-tap pass-through
  column, and the opt-in guard (no BITRUNK_HVH/VHV without the flag). Each asserts
  a root-orientation trunk with ≥2 perpendicular branches, full connectivity, and
  no cycles.
- **`test/tests/test_multi_trunk_units.py`** — fixtureless `def test_*()` unit
  tests (no pytest fixtures) that build a `Floorplan`/`TopologyGenerator` and call
  `generate_candidates` directly, so **`tools/unit2buda.py`** can convert each into
  a runnable `.buda` script for visual inspection (see below). Cases: column HVH,
  row VHV, multi-tap pass-through, 3-column comb, opt-in guard, plus two
  `@pytest.mark.mid` end-to-end flows (pin a BITRUNK_HVH → planner → NUTS routes
  with 0 overlaps; default candidate set is unchanged under the flag).
- **`test/tests/features/multi_level_trunk.feature` + `test_multi_level_trunk.py`**
  — the multi-block scenarios now generate with `multi_trunk` and assert the real
  two-level structure strictly (two-level tree, connects-all, acyclic, root feeding
  ≥2 perpendicular branches). The feedthru-block-as-relay trunk split stays xfail
  (unimplemented); source-anchored root and true depth-3 are documented follow-ups,
  so those scenarios assert the achievable two-level shape.

### Visual inspection with `unit2buda`

`tools/unit2buda.py` records `set_multi_trunk` and emits `generate_topologies
multi_trunk`, so a fixtureless unit test round-trips to a `.buda` that reproduces
the same trees:

```
tools/unit2buda.py test_column_datapath_hvh -o /tmp/col.buda
./buda /tmp/col.buda            # opens the topology explorer on the HVH tree
```

## v1 scope & follow-ups

- **In:** H / comb shapes (K≥2 clusters) both orientations; multi-tap
  pass-through branch trunks; opt-in flat `generate_topologies`.
- **Done since v1:** `multi_level_trunk.feature`'s multi-block scenarios flipped
  from MST-fallback/xfail to strict two-level-BITRUNK asserts; `unit2buda`
  multi_trunk support + fixtureless unit tests + mid-tier e2e flow.
- **Follow-ups:** thread `multi_trunk` into `generate_topologies_for_bundle` and
  `generate_hier_topologies`; a source-anchored root (root passes near the driver)
  — would let `multi_level_trunk` assert root-adjacent-to-src strictly; a true
  depth-3 tree for `2×2×2` layouts; the feedthru-block-as-relay trunk split (the
  one remaining xfail in `multi_level_trunk.feature`); ranking tie-break by slide
  flexibility (`topology_flexibility.feature`).
- **T-shape** (single perpendicular bar of receivers from a driver) is already
  covered by the existing single `TRUNK_H`/`TRUNK_V` (spine + driver stub); the
  new value is the ≥2-branch H/comb trees.
