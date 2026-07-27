# HierTopologyGenerator — Phase D Design

> **As-built updates (2026-07):** this design note predates the CLI split and
> several later phases. Corrections against the current code:
> the implementation lives in `src/buda_cmds/topologies_cmds.py` (command
> handlers) and `src/buda_session/hier.py` (`HierMixin._generate_hier_topo_one`
> + floorplan/reason helpers), not `src/buda_cli.py`; the **cross-level** case
> (`drv_spec_depth >= 0`) builds an endpoint-only custom floorplan rather than
> the depth-D floorplan; floorplan caching is keyed `('cell', parent)` /
> `('depth', d)` (the cross-level path rebuilds per call); §6's priority rule
> **is implemented** (`priority = -(level*10000 + n_candidates)` in
> `planner_cmds.py`, width-DESC tie-break in the C++ sort); all three hier
> generate commands also accept `multi_trunk` (two-level BITRUNK trees),
> `no_hanan_loci` (midpoint-only trunk loci), and `spine_relays` (opt-in MST
> relay-hub collector spine) and
> persist their candidates into the open BDB; generation ends in the coverage
> gate (`filter_uncovered`); and sidecar pins are re-applied on regeneration.
> Bottom-up template planning (a per-cell `set_bottom_up` mode: plan/NUTS/DNUTS
> once per template, copy per instance, copies become blockages) is designed
> and implemented in `docs/internal/hier_bottom_up_planning.md`.

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

## 5. CLI Commands

### `generate_hier_topologies`

```
generate_hier_topologies [center_mode] [double_detour] [multi_trunk] [no_hanan_loci] [spine_relays]
```

See the [BUDA Script reference](script_reference/topologies.md) for the full flag
semantics (`spine_relays` = opt-in MST relay-hub collector spine).

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

**Zero-candidate warning:** If any HBundle ends up with 0 topology candidates, the CLI prints:
```
  WARNING: HierTopo D{level}: bundle {id} ({label}) 0 candidates — bundle will be unrouted!
```
Downstream stages (`run_planner`, `run_nuts`) silently skip bundles with no candidates, so this warning is the only indication that a bundle will not be routed. Common causes: source or destination block not present in the floorplan (check `add_blocks_from_bdb` depth), or extreme span/layer constraints ruling out all candidate shapes.

---

### `generate_topologies_for_hbundle`

```
generate_topologies_for_hbundle <bundle_id> [center_mode] [double_detour]
```

Re-run topology generation for a **single** HBundle identified by its integer ID, without regenerating all other bundles. Useful for debugging when one bundle has zero candidates or when experimenting with flags on a specific bundle.

| Argument | Type | Description |
|---|---|---|
| `bundle_id` | int | Integer bundle ID (as shown by `dump_hbundles`). |
| `center_mode` | keyword | Use block centres as connection points instead of the nearest busterm face. |
| `double_detour` | keyword | Also generate `UU_VHV` / `UU_HVH` high-detour candidates. |

**3-case dispatch:** Uses the same routing-context logic as `generate_hier_topologies`:

| Bundle type | Floorplan used | Src/dst derivation |
|---|---|---|
| Cell-local (`cell_context` set) | Cell-local floorplan (origin at parent (x1,y1)) | Local component names from `entry/exit_busterm_ids` |
| Cross-level (`drv_spec_depth` ≥ 0) | BDB depth-D floorplan | Depth-D absolute component paths |
| Cross-block | BDB depth-D floorplan | Parsed from `reason` string |

**Zero-candidate warning:** Same WARNING line as `generate_hier_topologies` if the bundle ends up with 0 candidates after the call.

**Post-expansion advisory:** If `run_planner hier` has already been called and the specified bundle ID no longer appears in `self.bundles` (because it was expanded into per-instance wrappers), the CLI prints:
```
Note: bundle {id} was expanded by run_planner hier — re-run generate_hier_topologies before planning.
```

**Example:**
```buda
generate_topologies_for_hbundle 4              # re-generate for hb-4
generate_topologies_for_hbundle 4 center_mode  # with centre-mode flag
dump_hbundles depth 1                          # verify cands updated
```

---

## 6. Phase E Integration Note — Constraint-First Ordering

When `CongestionPlanner` processes hier bundles (Phase E), bundles at lower
depth levels may have **very limited routing flexibility** because their
entry/exit faces are constrained by the parent-level routing decision.

**Prioritization rule** (implemented — see as-built note above):

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

| File | Change (locations as-built; see note at top) |
|------|--------|
| `src/buda_cmds/topologies_cmds.py` | `generate_hier_topologies` / `generate_topologies_for_hbundle` command handlers |
| `src/buda_session/hier.py` | `_generate_hier_topo_one` (3-case dispatch), `_build_bdb_floorplan`, `_build_cell_local_floorplan`, `_parse_bundle_reason`, expansion + bottom-up machinery |
| `src/buda_cmds/setup_cmds.py` | stores `_corner_margin` |
| `test/tests/features/hier_topology.feature` | BDD scenarios |
| `test/tests/test_hier_topology.py` | Step defs + standalone tests |
