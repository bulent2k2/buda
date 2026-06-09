# HierTopologyGenerator — Phase D Design

## 1. Goal

Generate routing topology candidates for each HBundle output from
`HierarchicalBundler.run()`.  Three distinct cases:

| Bundle type | Floorplan used | Src/dst names |
|-------------|---------------|---------------|
| Depth-0 cross-block | BDB depth-0 floorplan | depth-0 component paths (e.g. "src_i") |
| Depth-D cross-block (D ≥ 1) | BDB depth-D floorplan | depth-D component paths (e.g. "src_i/buf_i") |
| Depth-D intra-cell (D ≥ 1) | **Cell-local floorplan** | local component names (e.g. "pa_i") |

The intra-cell case is the new contribution of Phase D.  Templates are
generated **once per (cell_context, cell-local-signature)** — all instances
in `HBundle.instances` share the same candidate set (in cell-local coords).
Per-instance placement transforms are applied in Phase E.

---

## 2. Floorplan Construction

### 2a. BDB depth-D floorplan

```
For each ComponentRow c where c.depth == D and c.x1 >= 0:
  fp.add_block(c.name, c.x1, c.y1, c.x2, c.y2)
```

Block names are the full component paths (e.g. "proc_i/pa_i"), matching
the `reason` strings produced by `HierarchicalBundler`.

The **global corner margin** from the main floorplan (`session._corner_margin`)
is applied to this floorplan.

### 2b. Cell-local floorplan

```
parent = BDB component named b.instances[0]   (e.g. proc_i)
For each child c of parent (c.parent_id == parent.id):
  local_name = last path segment of c.name    (e.g. "pa_i")
  fp.add_block(local_name,
               c.x1 - parent.x1, c.y1 - parent.y1,
               c.x2 - parent.x1, c.y2 - parent.y1)
```

Cell-local coordinates: origin at parent's (x1, y1); all child coords
translated by (-parent.x1, -parent.y1).

No corner margin is applied to cell-local floorplans (entry/exit busterm
constraints already anchor the route to the right faces).

---

## 3. Source / Destination Derivation

### Cross-block bundles

Parse `b.reason`:
```
"DRV:src_i/buf_i|REC:proc_i/pa_i,"  →  src="src_i/buf_i", dsts=["proc_i/pa_i"]
```

These names match block names in the BDB depth-D floorplan.

### Cell-level bundles

```
src_local = b.entry_busterm_ids[0]
            .removeprefix("bt:")
            .split("/")[-1]          # e.g. "pa_i"

dst_locals = [bt.removeprefix("bt:").split("/")[-1]
              for bt in b.exit_busterm_ids]   # e.g. ["pb_i"]
```

These names match block names in the cell-local floorplan.

---

## 4. Topology Generation

For each bundle:
1. Build the appropriate floorplan (cached per (depth, cell_context, instance) key)
2. Create `TopologyGenerator(fp)`; set layer IDs from `LayerStack` if available
3. Call `generate_candidates(src, dsts)` → store in `BundleWrapper.candidates`

Template sharing: for an HBundle with `instances = [proc_i1, proc_i2]`, the
same cell-local candidates serve all instances.  No per-instance regeneration.

---

## 5. CLI Command

```
generate_hier_topologies [center_mode] [double_detour]
```

Requires: `open_bdb` and `run_hier_bundler` called first.

Processes all bundles in `session.bundles` depth by depth (0 … max).
Prints a summary line per bundle.

Output example:
```
HierTopo D0: bundle 1 (src_i→proc_i) 7 candidates
HierTopo D0: bundle 2 (proc_i→snk_i) 7 candidates
HierTopo D1: bundle 3 (src_i/buf_i→proc_i/pa_i) 7 candidates   [cross-block]
HierTopo D1: bundle 4 (pa_i→pb_i) 5 candidates                 [cell:proc_cell]
HierTopo D1: bundle 5 (pb_i→pc_i) 5 candidates                 [cell:proc_cell]
HierTopo D1: bundle 6 (proc_i/pc_i→snk_i/rcv_i) 7 candidates  [cross-block]
```

---

## 6. Phase E Integration Note — Constraint-First Ordering

When `CongestionPlanner` processes hier bundles (Phase E), bundles at lower
depth levels may have **very limited routing flexibility** because their
entry/exit faces are constrained by the parent-level routing decision.

**Prioritization rule** (to be implemented in Phase E):

> Within each depth level, sort bundles ascending by **number of topology
> candidates** before the planner loop — bundles with fewer options get
> first pick of the congestion map.

```
priority_key = (depth ASC, n_candidates ASC, width DESC)
```

- `depth ASC`: top-down; depth-0 is solved before depth-1 (constraints flow down)
- `n_candidates ASC`: within a level, most-constrained first ("no waffle room" first)
- `width DESC`: tie-break; wider buses still claim their space before narrow ones

A bundle with 1 candidate has zero flexibility — it must be placed wherever
that single topology lands.  A bundle with 7 candidates can adapt around
congestion.  Routing the 1-candidate bundle first ensures it gets its only
valid path; the 7-candidate bundle will find an alternative if that path
is blocked.

---

## 7. Files Modified

| File | Change |
|------|--------|
| `src/buda_cli.py` | Add `generate_hier_topologies`; helper methods `_build_bdb_floorplan`, `_build_cell_local_floorplan`, `_parse_bundle_reason`; store `_corner_margin` |
| `test/tests/features/hier_topology.feature` | BDD scenarios |
| `test/tests/test_hier_topology.py` | Step defs + standalone tests |
