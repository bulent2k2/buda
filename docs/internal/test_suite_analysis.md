# BUDA Test Suite Analysis

Structure, coverage, and xfail/skip inventory for the BUDA pytest suite.
Companion to [test_runtime_analysis.md](test_runtime_analysis.md), which
covers per-test runtimes and the three-tier marker scheme.

> Last updated: 2026-06-23

---

## Overview

| Metric | Count |
|---|---|
| Test files | 56 |
| Feature files (pytest-bdd) | 38 |
| Total test functions / scenarios | ~521 |
| xfail groups (whole-file or per-scenario) | 5 |
| Scenarios blocked by missing C++ features | ~35 |
| Tests currently passing (fast tier, `bb -t`) | ~440 |
| Tests in mid tier (`bb -m`) | 40 |
| Tests in slow tier (`bb -s`) | 2 |

The suite mixes two styles:

- **Unit/integration tests** — plain `pytest` functions that import `buda` or
  `buda_db` and call Python/C++ APIs directly.
- **BDD scenarios** — `pytest-bdd` `@scenario` / `scenarios()` functions backed
  by `.feature` files in `test/tests/features/`. Steps are shared across
  feature files via `conftest.py` and per-file `@given/@when/@then` hooks.

---

## Tier Structure

Three cumulative tiers defined in `pytest.ini` (`addopts = -m "not slow and not mid"`):

| Tier | Marker | What | Tests | Run with |
|---|---|---|---:|---|
| **fast** | *(none)* | Unit + component tests | ~440 | `bb -t` / `pytest` |
| **mid** | `mid` | Integration tests (subprocesses + full visualizer builds) | 40 | `bb -m` |
| **slow** | `slow` | SA/GA placement-optimizer convergence storms | 2 | `bb -s` |

The **mid** tier (`pytestmark = pytest.mark.mid`) covers:

| File | Tests | Why mid |
|---|---:|---|
| `test_flow_scripts.py` | 20 | Each `subprocess.run`s `buda_cli.py` on a `.buda` file (~1s/test) |
| `test_viz_collections.py` | 12 | Builds a real `BudaVisualizer` over a full flow; Agg backend init (~0.5–3s/test) |
| `test_topo_explorer_terminals.py` | 5 | Builds a `TopologyExplorer` over a routed flow (~0.3s/test) |
| `test_topo_explorer_focus_on_cycle.py` | 1 | Same — full Explorer build (~0.5s) |
| **total** | **40** | |

The two **slow** tests are `test_optimize_demo_tc1_overlap_storm` and
`test_optimize_demo_tc2_fixed_io` in `test_floorplanner_commands.py`,
each decorated with `@pytest.mark.slow`.

---

## Coverage Map

### BDB Layer (`buda_db`)

| File | Scenarios | Covers |
|---|---:|---|
| `test_bdb.py` | 75 | Import DEF/LEF, Verilog, combined; component mutations; cell/inst; BDB net/pin tables; flip/rotate |
| `test_bundle_wrapper_api.py` | 16 | BundleWrapper sub-struct API (.input, .plan, .hier, CongestionPlanner output) |
| `test_busterm_rects.py` | 12 | BustermRow multi-rect JSON encoding; PORT/BLOCK/SPATIAL_CLUSTER resolution |
| `test_routing_grid.py` | 21 | RoutingGridStack, TrackPattern, signal slot enumeration, per-region overrides |
| `test_bundler.py` | 3 | STRICT vs CONVERGENT bundling strategies |
| `test_generate_needs_bundler.py` | 3 | TopologyGenerator bundler-precondition check |
| `test_hier_bundler.py` | ~33 | Hierarchical bundler: cell-local, cross-level, cross-block HBundles; Verilog-dirs variant |
| `test_hier_bundler_verilog_dirs.py` | 3 | Module discovery from Verilog directory imports |
| `test_floorplanner_commands.py` | 17 | FloorplannerEngine Python API; SA/GA optimizer; fp_demo TC1/TC2/TC3; BDB write roundtrip |
| `test_floorplanner_contract.py` | ~16 | Floorplanner engine API (passing); GUI scenarios (xfail — see below) |

### Topology Generator (Stage 2)

| File | Scenarios | Covers |
|---|---:|---|
| `test_topology.py` | 3 | Topology struct smoke tests |
| `test_trivial_topo.py` | 3 | Single-segment degenerate case |
| `test_connector_shape.py` | 12 | Stub shape selection (Direct/L/Z) based on trunk slide range |
| `test_corner_margin.py` | 12 | Block corner margin dx/dy; perp slide range shrinking |
| `test_keepout_zone.py` | 4 | Keepout zone placement and Hanan grid injection |
| `test_topo_keepout_mst.py` | 21 | Keepout-edge injection into MST trunk generation |
| `test_multi_rect_block.py` | 7 | TEG multi-rect block topologies (thru/over modes) |
| `test_busterm_over_the_block.py` | 9 | TEG bridge segments; over-the-block stub connectivity |
| `test_multicast_topology.py` | 8 | One-to-many (multicast) trunk+branch topologies |
| `test_unified_topology.py` | 14 | Unified topology generation across hierarchy levels |
| `test_hier_topology.py` | ~10 | Hier topology candidate generation per cell type |
| `test_hier_testcase.py` | ~12 | Hier netlist + floorplan structure setup |
| `test_conn_topology_missing_block.py` | 1 | ConnTopology graceful handling of missing-block annotations |
| `test_trunk_coverage.py` | 1 | All leaf blocks covered by trunk |

### Bundle Planner (Stage 3)

| File | Scenarios | Covers |
|---|---:|---|
| `test_global_congestion.py` | 5 | CongestionPlanner map and optimization |
| `test_planner_symmetric_congestion.py` | 1 | Load balancing across symmetric candidates |
| `test_planner_narrow_cell_cut.py` | 1 | Narrow-cell cutline edge case |
| `test_planner_pinned_congestion_visible.py` | 1 | Pinned topology congestion communication |
| `test_pinned_overflow_message.py` | 1 | User-facing error message for pinned overflow |
| `test_span_layer_assignment.py` | 10 | Span-aware layer assignment logic |
| `test_layer_orientation_honored.py` | 5 | Layer H/V direction compliance |
| `test_sidecar_script_topo_conflict.py` | 2 | Script-pinned topology conflict handling |
| `test_hier_planner.py` | ~11 | Hierarchical global planner: cell-local, cross-level passes |
| `test_hierarchy_depth_planning.py` | 8 | Depth-aware busterm rollup (xfail — see below) |
| `test_check_layer_dir.py` | 5 | LAYER_DIR violation detection |
| `test_check_design_hbundle.py` | 16 | check_topo / check_nuts / check_dnuts on HBundle flows |
| `test_reversed_span_connectivity.py` | 1 | Span direction independence |

### Abstract NUTS (Stage 4)

| File | Scenarios | Covers |
|---|---:|---|
| `test_nuts.py` | 8 | Track segment placement; overlap handling |
| `test_nuts_alignment.py` | 3 | Alignment precision |
| `test_nuts_dogleg.py` | 12 | Multi-segment connectivity; segment indexing |
| `test_passthrough_slide.py` | 2 | Pass-through block trunk geometry |
| `test_no_tiny_stubs.py` | 4 | Minimum stub length enforcement |
| `test_min_stub_lengths_exhaustive.py` | 2 | Exhaustive stub-length bounds checking |

### Detailed NUTS (Stage 9)

| File | Scenarios | Covers |
|---|---:|---|
| `test_detailed_nuts.py` | 16 | Bit-level track placement (TDD against planned API) |
| `test_detailed_nuts_xlayer.py` | 2 | Cross-layer bridging |

### ConnTopology / Pull fields

| File | Scenarios | Covers |
|---|---:|---|
| `test_net_pull.py` | 6 | Pull field flow integration (lo_pull/hi_pull usage) |
| `test_pull_preference.py` | 10 | lo_pull/hi_pull/pull_balance; ~CEN candidate; adjusted_wl ranking |
| `test_topology_flexibility.py` | 7 | adjusted_wl, discount_factor, zero-slide candidate ranking |

### Feedthru / Multi-level Trunk

| File | Scenarios | Covers |
|---|---:|---|
| `test_feedthru.py` | 6 | Feedthru relay trunk splitting; V-stub suppression (xfail — see below) |
| `test_multi_level_trunk.py` | 6 | BITRUNK / TRITRUNK; feedthru relay; root-trunk detection |

### CLI / Visualization / Other

| File | Tests | Tier | Covers |
|---|---:|---|---|
| `test_flow_scripts.py` | 20 | mid | End-to-end `.buda` script execution; includes regression for issue #16 (pinless bus sentinel bug) |
| `test_unknown_command.py` | 8 | fast | CLI error handling for unknown/malformed commands |
| `test_viz_collections.py` | 12 | mid | `BudaVisualizer` rendering: collections, lazy detail build, rail toggle, solo mode, pan |
| `test_topo_explorer_focus_on_cycle.py` | 1 | mid | Topology explorer: pin focus survives cycle |
| `test_topo_explorer_pin_badge.py` | 2 | fast | Topology explorer: pin badge display (script-pin vs planner-pin) |
| `test_topo_explorer_single_pin.py` | 1 | fast | Topology explorer: single-pin topology pinning |
| `test_topo_explorer_terminals.py` | 5 | mid | Topology explorer: terminal display, key bindings ('d', '-', '+', 'u' removed), arrow pan |

---

## xfail Inventory

All xfail markers use `strict=False` (unexpected passes are surfaced as XPASS
warnings but do not fail the run).  There are five distinct groups, each
representing a cohesive block of missing C++ API.

---

### Group 1 — Floorplanner GUI test harness (9 scenarios)

**File:** `test_floorplanner_contract.py`  
**Feature:** `features/floorplanner_gui_basic.feature`  
**Reason:** `requires headless display driver; GUI is implemented but no automated display-based test harness exists yet`

The Tk/matplotlib GUI (`tools/bdb_floorplanner.py`) is fully implemented.
The `given_gui_session` step calls `pytest.fail(...)` unconditionally because
no headless display driver or robot-framework harness exists to drive it.
The nine scenarios test: canvas creation, block drag, resize handles, edge
alignment, horizontal distribution, child-block editing, rotation with
descendant propagation, validation, and HBundle-script export.

The `floorplanner_engine_api.feature` scenarios in the same file pass — they
drive the C++ `FloorplannerEngine` directly without touching the GUI layer.

**Unblocking:** Add a headless display driver (e.g. `Xvfb` + `pyautogui`, or a
`tk` event injection harness) and wire it into `given_gui_session`.

---

### Group 2 — Feedthru relay (6 scenarios)

**File:** `test_feedthru.py`  
**Feature:** `features/feedthru.feature`  
**Reason:** `feedthru relay not yet implemented in C++`

Feedthru blocks act as relay junctions: a trunk is split at the feedthru face,
the feedthru block passes signal through its interior, and the far-side stub
is suppressed so only one physical segment exits each face.  None of the
required C++ primitives (`pass_through_count`, trunk-split at feedthru, V-stub
suppression) are implemented.  One scenario also calls
`pytest.xfail("Per-block pass_through_count not in C++ API")` inside a step.

**Related xfails in `test_multi_level_trunk.py`:** the BITRUNK/TRITRUNK
scenarios partially overlap — two scenarios are decorated with
`@pytest.mark.xfail` (feedthru V-stub suppression; root-trunk identification)
and ~12 step implementations call `pytest.xfail()` for the same missing
primitives.

---

### Group 3 — Hierarchy depth / rollup (8 scenarios)

**File:** `test_hierarchy_depth_planning.py`  
**Feature:** `features/hierarchy_depth_planning.feature`  
**Reason:** `hierarchy depth / rollup not yet implemented in C++ (depth_ is dead code)`

These scenarios test the automatic rollup of busterms to higher hierarchy
levels when a child cell is too shallow to form independent bundles.  The
`depth_` field in the C++ `HBundle` struct is a placeholder — it is set but
never acted on.  All eight scenarios are blocked.

---

### Group 4 — `adjusted_wl` / `discount_factor` candidate ranking (7 + 4 scenarios)

**Files:** `test_topology_flexibility.py` (7 scenarios, 4 xfail);
           `test_pull_preference.py` (10 scenarios, 2 with `_XFAIL_RANK`);
           `test_busterm_over_the_block.py` (9 scenarios, 1 xfail)

**Reason:** `adjusted_wl not yet in C++ API` / `adjusted_wl / pull_balance ranking not yet in C++ API` / `Topology.adjusted_wl and per-topology teg_mode attribute not yet in C++ API`

The `Topology` struct lacks an `adjusted_wl` field (wirelength discounted by
a per-scenario factor for topologies that benefit from block pass-through).
Without it, the planner cannot rank candidates using the three-key sort
`(adjusted_wl, min_slide, pull_balance)`.  Affected scenarios test:
topology candidate ranking by discounted wirelength, `discount_factor`
persistence, the `zero-slide` candidate preference, and TEG-mode persistence
on a `Topology` object.

Some step implementations call `pytest.xfail()` directly when the
`adjusted_wl` attribute is absent at runtime.  Two `pytest.skip()` calls guard
against "no zero-slide candidate found" — these are not missing features but
rather precondition failures when the expected candidate is absent.

---

### Group 5 — `lo_pull` / `hi_pull` / `~CEN` topology (6 scenarios)

**File:** `test_pull_preference.py` (10 scenarios total, 6 xfail)  
**Reasons (three distinct markers):**

| Marker | Scenarios | Reason |
|---|---:|---|
| `_XFAIL_PULL` | 2 | `lo_pull / hi_pull / pull_balance API fields not yet populated in C++` |
| `_XFAIL_CEN` | 2 | `~CEN candidate not yet generated by TopologyGenerator` |
| `_XFAIL_RANK` | 2 | `adjusted_wl / pull_balance ranking not yet in C++ API` |

`lo_pull` and `hi_pull` are the signed displacements from a segment's
geometric centroid toward the driver and receiver block faces respectively.
`pull_balance` is their ratio.  These are not currently computed or stored on
`ConnSeg` objects in C++.

The `~CEN` (near-centroid) topology variant routes the trunk through the
centroid of the driver/receiver block pair rather than the Hanan grid; the
`TopologyGenerator` does not yet emit it.

The four passing scenarios in this file (`_L_HV topology`, `Balanced TRUNK_H`,
`Skewed TRUNK_H`, `Leaf stub ConnSegs`) use only geometry that is already
computed (slide ranges, perp intervals, segment counts) — these pass cleanly
and had their xfail markers removed when they were reclassified to XPASS.

---

## Skip Inventory

There are no whole-test `@pytest.mark.skip` decorators in the suite.  Two
`pytest.skip()` calls appear inside BDD step implementations (not at the
test-function level), both in `test_topology_flexibility.py`:

```
pytest.skip("no zero-slide candidate found — precondition unmet")
```

These guard scenarios that require a specific candidate topology to be present;
when the topology generator does not emit that candidate the scenario is
skipped rather than failed.  They are precondition guards, not missing-feature
markers, and will naturally pass once the full candidate set is generated.

---

## Feature File Inventory

38 `.feature` files with 290 total scenarios in `test/tests/features/`:

| Feature file | Scenarios | Status |
|---|---:|---|
| bdb_import.feature | 17 | ✓ all pass |
| bdb_combined.feature | 7 | ✓ all pass |
| bdb_mutations.feature | 7 | ✓ all pass |
| bdb_cell_inst.feature | 11 | ✓ all pass |
| bdb_add_blocks.feature | 7 | ✓ all pass |
| bdb_inst_to_cell.feature | 6 | ✓ all pass |
| bdb_flip_rotate.feature | 7 | ✓ all pass |
| bdb_net_pins.feature | 11 | ✓ all pass |
| bundler_logic.feature | 3 | ✓ all pass |
| bundler_hierarchy.feature | 2 | ✓ all pass |
| hier_bundler.feature | 9 | ✓ all pass |
| hier_bundler_verilog_dirs.feature | 3 | ✓ all pass |
| connector_shape.feature | 12 | ✓ all pass |
| corner_margin.feature | 11 | ✓ all pass |
| keepout_zone.feature | 2 | ✓ all pass |
| multi_rect_block.feature | 7 | ✓ all pass |
| busterm_over_the_block.feature | 9 | 1 xfail (adjusted_wl) |
| multicast_topology.feature | 8 | ✓ all pass |
| topology_generation.feature | 3 | ✓ all pass |
| unified_topology.feature | 14 | ✓ all pass |
| hier_testcase.feature | 8 | ✓ all pass |
| hier_topology.feature | 5 | ✓ all pass |
| hier_planner.feature | 5 | ✓ all pass |
| layer_assignment.feature | 2 | ✓ all pass |
| span_aware_layer_assignment.feature | 8 | ✓ all pass |
| global_congestion.feature | 5 | ✓ all pass |
| large_fanout_mst.feature | 3 | ✓ all pass |
| topo_keepout_mst (no .feature — unit tests) | 21 | ✓ all pass |
| nuts_track_assignment.feature | 8 | ✓ all pass |
| detailed_track_assignment.feature | 12 | ✓ all pass |
| routing_grid.feature | 13 | ✓ all pass |
| no_tiny_stubs.feature | 3 | ✓ all pass |
| pull_preference.feature | 10 | 6 xfail (groups 4+5) |
| topology_flexibility.feature | 7 | 4 xfail (group 4) |
| multi_level_trunk.feature | 6 | partial xfail/skip in steps |
| feedthru.feature | 6 | all xfail (group 2) |
| hierarchy_depth_planning.feature | 8 | all xfail (group 3) |
| floorplanner_gui_basic.feature | 9 | all xfail (group 1) |
| floorplanner_engine_api.feature | 7 | ✓ all pass |

---

## Changelog

### 2026-06-23

**New tests (3):**

- `test_flow_scripts.py::test_pinless_buses_stay_separate` — regression for
  issue #16: `extract_instance` was mapping every pinless (no-dot) endpoint to
  the sentinel `"top"`, causing `a left right` and `b up down` to share a
  STRICT bundler signature and collapse into one bundle. Fix: bare token is now
  the block name. Test drives `flow/no_pin_suffix.buda` and asserts two bundles
  are created with the correct driver→receiver pairs.

- `test_viz_collections.py::test_s_key_toggles_solo` — verifies the new `'s'`
  key (BudaVisualizer solo mode) toggling; both on→off and off→on.

- `test_topo_explorer_terminals.py::test_explorer_d_is_next_topo_not_layer_down`
  — regression for the `'d'` key double-binding in TopologyExplorer (`'d'` was
  wired to both *next topology* and *layer-down*, so one keypress did both). Now
  only next-topology; `'-'`/`'_'` remain layer-down. Also verifies `'u'` is no
  longer a layer key (alias removed).

**Tier moves (3 files → mid):**

- `test_viz_collections.py` (12 tests) — each builds a full `BudaVisualizer`
  over `dnuts1.buda`; ~0.5–3s per test.
- `test_topo_explorer_terminals.py` (5 tests) — each builds a `TopologyExplorer`
  over a routed flow; ~0.3s per test.
- `test_topo_explorer_focus_on_cycle.py` (1 test) — same reason, ~0.5s.

**Net effect:** fast tier (`bb -t`) dropped from ~30s to ~15s; mid tier grew
from 19 to 40 tests.

---

## Missing Features Summary

The xfail groups map directly to five C++ features that have been specified
(feature files + BDD scenarios written) but not yet implemented:

| Feature | Tracking xfail group | Scenarios blocked |
|---|---|---:|
| Headless GUI test harness | Group 1 | 9 |
| Feedthru relay trunk splitting + V-stub suppression | Group 2 | ~15 |
| Hierarchy depth rollup (`depth_` field activation) | Group 3 | 8 |
| `adjusted_wl` / `discount_factor` on `Topology` struct | Group 4 | ~7 |
| `lo_pull` / `hi_pull` / `pull_balance` on `ConnSeg`; `~CEN` topology variant | Group 5 | 6 |

All five are non-trivial C++ changes.  Groups 4 and 5 are related — both are
about enriching candidate-ranking metadata — and could be tackled together.
Group 2 (feedthru) is the largest in terms of cascading step failures across
two test files (`test_feedthru.py` and `test_multi_level_trunk.py`).
