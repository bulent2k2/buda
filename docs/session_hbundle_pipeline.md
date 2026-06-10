# Session Notes: Hierarchical Bundle Pipeline (Phases A–E)

This document records the design and implementation of the full HBundle pipeline — the work that made BUDA hierarchy-aware end-to-end. It covers the plan, what was built, the key decisions made along the way, and the test vehicles created to verify each phase.

---

## Starting Point and Motivation

Before this work, BUDA had a **flat routing model**: blocks live in one floorplan, nets connect named blocks, bundles are groups of topologically similar nets. The `BDB` (Buda Physical Design Database) had been added to store hierarchical design data (cells, instances, nets), but the routing engine still received a flattened view through `add_blocks_from_bdb`.

The goal was to make BUDA **hierarchy-aware end-to-end**:

1. Represent a design at multiple levels of hierarchy (chip → block → leaf)
2. Bundle nets at each level with the correct scope — cross-chip nets at the chip level, intra-block nets at the block level
3. Solve routing templates once per **cell type** and instantiate them at every occurrence
4. Generate topologies that use the correct floorplan for each hierarchy level
5. Plan and NUTS-place them in the right top-down order

The work was structured into five phases (A–E), each building on the previous.

---

## Architecture Overview

```
BDB (component/net/pin hierarchy)
       │
       ▼
[Phase A] derive_busterms        → BustermRow per component in busterm table
       │
       ▼
[Phase B] HBundle data model     → Bundle renamed; level/cell_context/instances fields added
       │
       ▼
[Phase C] run_hier_bundler       → HBundle per (depth, driver→receiver group); cell templates merged
       │
       ▼
[Phase D] generate_hier_topologies → candidates per bundle using correct per-level floorplan
       │
       ▼
[Phase E] run_planner hier       → cell-level expansion to per-instance; top-down priority ordering
       │
       ▼
       run_nuts / check_connectivity  (unchanged C++ engine)
```

---

## Phase A — BustermGen (`derive_busterms`)

**Commit**: `feat: rename Bundle→HBundle with hierarchy fields; implement BustermGen`

### What was built

`BustermGen::derive(int max_depth)` walks every component in the BDB up to `max_depth` and writes one `BustermRow` per component into the BDB `busterm` table. A busterm represents a component's routing interface — its bounding box and position in the hierarchy.

```cpp
for each ComponentRow comp where comp.depth <= max_depth and comp.x1 >= 0:
    HierBusterm hbt;
    hbt.id        = "bt:" + comp.name;
    hbt.hier_path = comp.name;
    hbt.depth     = comp.depth;
    hbt.x1..y2    = comp bbox;
    hbt.resolution = BLOCK (hierarchical) or SPATIAL_CLUSTER (leaf);
    hbt.parent_id  = "bt:" + parent.name  (if parent exists);
    _db.add_busterm(row);
```

### BDB additions

New BDB methods added to support BustermGen and later phases:

| Method | Purpose |
|--------|---------|
| `add_busterm(row)` | Insert or replace one busterm row |
| `clear_busterms()` | Delete all busterms before re-deriving |
| `components_at_depth(d)` | Return all components at exactly depth d |
| `pins_by_comp(comp_id)` | Return all pins belonging to one component |

### Name collision fix

The pre-existing `busterm.h` had a `Busterm` struct (used by the routing topology layer). The new hierarchy-level busterm was named `HierBusterm` to avoid the collision. The routing-level `Busterm` was kept unchanged.

### CLI command

```
derive_busterms [max_depth]
```

Default `max_depth = 1`. Clears and repopulates the busterm table from scratch.

Output: `derive_busterms: N busterms written (depth 0..K).`

---

## Phase B — HBundle Data Model

**Also in commit**: `feat: rename Bundle→HBundle with hierarchy fields; implement BustermGen`

### What changed

`Bundle` was renamed to `HBundle` throughout — C++ structs, Python bindings, CLI output, and tests. Six hierarchy fields were added:

```cpp
struct HBundle {
    // (existing fields)
    int id;
    std::vector<std::string> net_names;
    std::string reason;
    int num_terminals = 0;

    // NEW hierarchy fields
    int level = 0;                          // 0 = top-level, ≥1 = sub-level
    std::string cell_context;               // "" for cross-block; cell type name for intra-cell
    std::vector<std::string> instances;     // instance paths this HBundle covers
    int parent_id = -1;                     // -1 for top-level; id of parent HBundle
    std::vector<int> child_ids;
    std::vector<std::string> entry_busterm_ids;  // "bt:<name>" of driver-side busterm
    std::vector<std::string> exit_busterm_ids;   // "bt:<name>" of receiver-side busterms
};
```

### Design decision: full rename

The decision was to do a **full rename** (not a subclass or alias) so that the hierarchical concept is first-class everywhere. All existing tests were updated in the same commit. The flat `Bundler` was kept as-is for non-BDB flows.

### Build fixes in the same commit

Several pre-existing include errors that only surfaced when compiling on Linux were fixed: missing `<functional>` in `conn_topology.cpp`, `<cmath>` in `nuts.cpp`, `<iterator>` in `bdb.cpp`. `CMakeLists.txt` was updated to auto-detect the pybind11 cmake directory via `python3` so the build works without a manual `CMAKE_PREFIX_PATH`.

---

## Phase C — HierarchicalBundler

**Commit**: `feat: implement HierarchicalBundler (Phase C)`

### What was built

A new `HierarchicalBundler` class reads component/net/pin data from BDB and produces `vector<HBundle>` with correct depth, cell context, and multiple-occurrence merging. See `docs/HIER_BUNDLER.md` for the full algorithm.

### Key algorithm sketch

```
1. Index BDB: comp_by_id, net_name, pins_by_net

2. For depth D = 0 .. max_depth:
   a. Find nets with both driver and ≥1 receiver at exactly depth D
      (via _endpoints_at_depth)
   b. Group nets by STRICT sig: "DRV:<drv_name>|REC:<sorted rcv names>"
   c. Create one HBundle per group, set level=D
   d. Set cell_context if all endpoints share the same parent component
   e. Set parent_id linkage for cross-block depth-D→depth-0 pairs

3. Multiple-occurrence merging:
   Group cell-level HBundles by (cell_context, cell-local reason).
   For groups of size ≥ 2: keep first as template, accumulate instances,
   mark replicas with parent_id pointing at the template.
```

### How `add_net_pins` propagation interacts

`BDB::add_net_pins` inserts ancestor interface pins at every level strictly between the leaf endpoint and the common ancestor of all endpoints. This means:
- Cross-block nets (e.g. `src_i → proc_i`) appear at **both D=0 and D=1**
- Intra-block nets (e.g. `proc_i/pa_i → proc_i/pb_i`) appear **only at D=1**

The bundler exploits this naturally — it sees each net at the right depth(s) without extra logic.

### CLI command

```
run_hier_bundler [depth <N>]
```

Default `max_depth = 1`. Requires an open BDB with nets loaded.

Output: `HierBundler: N hbundles (D0: a, D1: b, …)`

### Test vehicle

`flow/hbundles/01_pipeline_hier.buda` — single source→proc→sink chain with two intra-proc buses. Verifies 6 HBundles (D0:2, D1:4) and cell_context detection.

---

## Phase D — `generate_hier_topologies`

**Commit**: `feat: implement generate_hier_topologies (Phase D)`

### What was built

The `generate_hier_topologies` CLI command generates topology candidates for all HBundles. See `docs/HIER_TOPOLOGY.md` for details.

### Three cases

| Bundle type | Floorplan | Block names |
|-------------|-----------|-------------|
| Any-depth cross-block | BDB depth-D floorplan | full component paths (e.g. `proc_i/pa_i`) |
| Intra-cell (cell_context set) | Cell-local floorplan | local names only (e.g. `pa_i`) |
| Cross-level (Phase F) | Custom mixed floorplan | actual endpoint paths at their depths |

**Cross-block** floorplans are built from `BDB.all_components()` filtered to exactly depth D, with `add_block(c.name, c.x1, c.y1, c.x2, c.y2)`. The global corner margin is applied.

**Cell-local** floorplans are built from children of `b.instances[0]`, with coordinates translated to cell-local origin (subtract parent's x1/y1). No corner margin (the entry/exit busterm positions anchor the route to the correct faces).

Template sharing: a cell-level HBundle with `instances = [proc_i1, proc_i2]` generates candidates **once** (cell-local); all instances share the same candidate set. Per-instance absolute transforms happen in Phase E.

### Helper methods added to `BudaSession`

| Method | Purpose |
|--------|---------|
| `_build_bdb_floorplan(depth)` | BDB depth-D floorplan |
| `_build_cell_local_floorplan(parent_name)` | cell-local floorplan for parent's children |
| `_parse_bundle_reason(reason)` | parse `"DRV:x|REC:a,b,"` → `(x, [a,b])` |
| `_make_topo_gen(fp, center, detour)` | create TopologyGenerator with layer IDs |

### CLI command

```
generate_hier_topologies [center_mode] [double_detour]
```

Output example:
```
HierTopo D0: bundle 1 (src_i→proc_i) 7 candidates
HierTopo D1: bundle 4 (pa_i→pb_i) 5 candidates  [cell:proc_cell]
```

---

## Phase E — `run_planner hier`

**Commit**: `feat: implement run_planner hier (Phase E)`

### What was built

`run_planner hier [N]` expands cell-level bundles to per-instance absolute-coord wrappers, assigns priorities, and runs the existing `CongestionPlanner`. See `docs/HIER_PLANNER.md` for details.

### Priority encoding

```python
w.priority = -(b.level * 10_000 + len(w.candidates))
```

Higher priority (less-negative) is processed first. This encodes:
- **Depth-0 before depth-1** (10,000 factor separates levels)
- **Fewer candidates first** within a level (most-constrained gets first pick)

### Instance expansion

Cell-level HBundles have candidates in cell-local coordinates. Before planning, each is expanded into one `BundleWrapper` per instance with candidates offset to absolute coordinates:

```python
for inst_name in b.instances:
    parent = comps[inst_name]
    dx, dy = parent.x1, parent.y1
    new_w.candidates = [offset_topology(t, dx, dy) for t in template.candidates]
```

`offset_topology` creates a new `Topology` with every segment's start/end Point shifted by (dx, dy). The expanded wrappers replace `self.bundles` so `run_nuts` / `visualize` / `check_connectivity` operate on absolute-coord wrappers — no changes needed to those stages.

### `CongestionPlanner` change

A `priority` field was added to `BundleWrapper`. The planner's internal sort was changed from `width DESC` only to `(priority DESC, width DESC)` — this is the only planner change needed.

### `_clone_hbundle_with_id` helper

Added to `BudaSession` to create a shallow HBundle copy with a new id (needed for the expansion step, which assigns synthetic ids to each per-instance wrapper so `BundleAssignment` lookups are unambiguous).

---

## Test Vehicles

### `flow/hbundles/01_pipeline_hier.buda`
Single source → proc → sink. Depth-1 hierarchy, 2 intra-proc buses. Verifies the full Phase C–E pipeline with 6 HBundles and cell context detection.

### `flow/hbundles/02_two_procs.buda`
Two proc instances: same cell type, same internal buses. Verifies multiple-occurrence merging (template + 1 replica → instances = [proc_i1, proc_i2]) and correct per-instance expansion in Phase E.

### `flow/hbundles/03_priority_ordering.buda`
Designs with bundles of varying candidate counts. Verifies constraint-first ordering: 1-candidate bundles are routed before 7-candidate bundles at the same depth.

### `flow/hbundles/04_deep_hierarchy.buda`
Three-level hierarchy (chip → blk → leaf, depth 0–2). Tests `run_hier_bundler depth 2`, generating HBundles at all three levels including cell-level templates at both D1 and D2.

### `flow/hbundles/05_stress_grid.buda` — `07_wide_fan_stress.buda`
3×2 block grid stress tests, adding progressively wider multicast fans (3-pin through 12-pin buses). See `docs/cross_level_bundling.md` for details on 07.

### `flow/hbundles/08_cross_level.buda`
Cross-level buses where driver and receiver are at different hierarchy depths. See `docs/cross_level_bundling.md` for the full description of this test and the bundler fix it required.

---

## Key Design Decisions

### 1. Full rename: Bundle → HBundle
Not a subclass. All flat-flow code uses `HBundle` with the hierarchy fields defaulting to zero/empty. This avoids a two-type system and makes the hierarchy fields available everywhere without casting.

### 2. Feedthrough default: merge into top-level bundle
When a net passes through a block without terminating inside it, the block is treated as topologically transparent. The top-level bundle routes through. No intra-block sub-bundle is created unless the user explicitly models it. This matches what `add_net_pins` naturally produces: propagated ancestor pins, not explicit feedthrough pins.

### 3. Multiple-occurrence conflict fallback: per-instance unique routing
If a cell-level template geometry conflicts with a specific instance's placement (e.g. an instance is rotated relative to the others), that instance gets an independent routing solution. Not yet triggered by any test vehicle, but the expansion step in Phase E handles it: each instance's candidates are independently valid.

### 4. Cell-local topology without corner margins
Cell-local floorplans omit the global corner margin because the entry/exit busterm positions (derived from the parent-level routing) already anchor the route to the correct face regions. Adding a margin would incorrectly shrink the intra-cell routing space.

### 5. NUTS and check_connectivity needed no changes
After Phase E expansion, `self.bundles` contains `BundleWrapper`s with absolute-coord candidates and selected topology indices — exactly the same form as flat-flow wrappers. All downstream stages (NUTS, connectivity check, visualizer) work unchanged.

---

## Test Suite

New feature files and test modules created for each phase:

| File | Phase | Scenarios |
|------|-------|-----------|
| `test/tests/features/hier_bundler.feature` | C | HBundle depth, cell_context, merge |
| `test/tests/features/hier_topology.feature` | D | Cell-local fp, cross-block fp, template sharing |
| `test/tests/features/hier_planner.feature` | E | Priority ordering, instance expansion |
| `test/tests/features/hier_testcase.feature` | C–E | End-to-end pipeline integration |
| `test/tests/test_hier_bundler.py` | C | Step defs + standalone tests |
| `test/tests/test_hier_topology.py` | D | Step defs + standalone tests |
| `test/tests/test_hier_planner.py` | E | Step defs + standalone tests |
| `test/tests/test_hier_testcase.py` | C–E | Full pipeline flow tests |

All 264 tests pass after the complete Phase A–E implementation.

---

## Files Modified (Phase A–E Summary)

| File | Changes |
|------|---------|
| `src/bundler.h` | `Bundle` → `HBundle`; added 6 hierarchy fields; added `HierarchicalBundler` class |
| `src/bundler.cpp` | Implemented `HierarchicalBundler::run()` |
| `src/busterm.h` | Added `HierBusterm` struct; `BustermGen` prototype update |
| `src/busterm.cpp` | Implemented `BustermGen::derive()` |
| `src/bdb.h` | Added `add_busterm`, `clear_busterms`, `components_at_depth`, `pins_by_comp` |
| `src/bdb.cpp` | Implemented the four new BDB methods |
| `src/bindings.cpp` | Exposed `HBundle` (all fields), `HierBusterm`, `BustermGen`, `HierarchicalBundler`; added `priority` to `BundleWrapper` |
| `src/congestion_planner.h` | Added `priority` field to `BundleWrapper` |
| `src/congestion_planner.cpp` | Sort by `(priority DESC, width DESC)` |
| `src/buda_cli.py` | Added `derive_busterms`, `run_hier_bundler`, `generate_hier_topologies`, `run_planner hier`; helpers `_build_bdb_floorplan`, `_build_cell_local_floorplan`, `_parse_bundle_reason`, `_make_topo_gen`, `_expand_hier_bundles`, `_offset_topology`, `_clone_hbundle_with_id` |
| `CMakeLists.txt` | Auto-detect pybind11 cmake dir via python3 |
| `flow/hbundles/01–08_*.buda` | Test vehicles for each phase |
| `test/tests/features/hier_*.feature` | BDD scenarios |
| `test/tests/test_hier_*.py` | Step definitions and standalone tests |
