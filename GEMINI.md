# BUDA: Interconnect Planning System

BUDA (Bundled Unified Design Automation) is an open-source EDA interconnect planning system for chip design. It groups nets into buses, generates routing topologies, plans layer assignments, and resolves detailed track positions—ranging from abstract physical design planning to bit-level track assignment.

This file provides comprehensive architectural guidance, algorithmic requirements, design patterns, and engineering standards for developers working on the BUDA codebase.

---

## 1. Architectural Overview & Flows

The project is structured around a centralized SQLite-backed database called **BDB** (Buda Physical Design Database). There are two primary execution flows:

```
        ┌─────────────────────────────────────────────────────────────┐
        │ BDB (SQLite)   components · cells · pins · nets · busterms ·  │
        │                bundles · groups.  Central store for the hier  │
        │                flow; built by import_*, add_*, or Floorplanner│
        └─────────────────────────────────────────────────────────────┘
                  │ derive_busterms / add_blocks_from_bdb
                  ▼
Netlist / Floorplan (.buda script — flat flow — or projected from BDB)
    │
    ▼
[1] Bundler          nets → Bundles      (HierarchicalBundler → HBundles for hier flow)
    │
    ▼
[2] TopologyGen      Bundles → candidate L/Z/U topologies (Hanan grid)
    │                ConnTopology augments each with connectivity + slide ranges + MST
    ▼
[3] Bundle Planner   topology selection + layer assignment (congestion-aware)
    │                each segment now has: layer, routing-dir span, perp interval
    ▼
[4] Abstract NUTS    1.5-D rectangle packing → BusSegment track_position (real coords)
    │                parallelises per layer; power-grid dilution applied approximately
    ▼
[5] Layer Stack      (consulted by stages 3–9 for layer direction/type metadata)
    │
    ▼
[8] Routing Grid     per-layer track patterns (power/signal/clock layout); IMPLEMENTED
    │                global pattern per layer + optional Hanan-region overrides
    ▼
[9] Detailed NUTS    snaps each BusSegment → N NetSegments on concrete signal tracks; IMPLEMENTED
                     respects pre-route blockages; bit ordering; timing-critical mode
 
[6]  CLI             orchestrates the flow via .buda scripts (src/buda_cli.py)
[7]  Visualizer      interactive matplotlib; click-to-highlight; pre-route toggles
[V]  Verify          check_topo / check_nuts / check_dnuts — connectivity & layer-dir audit
[FP] Floorplanner    interactive placement GUI over FloorplannerEngine + PlacementOptimizer
```

### The Two Routing Flows
1. **Flat Flow:** Declare blocks and nets directly in a `.buda` script using `add_block` and `add_net`. Run flat bundlers and topology generators. Ideal for simple, flat designs.
2. **Hierarchy-Aware Flow:** Manage hierarchical designs via BDB (`open_bdb`, `import_def_lef`, `import_verilog`, `import_gds`, or the interactive **Floorplanner** GUI). Derives busterms using `derive_busterms`, runs `run_hier_bundler` to group nets into `HBundle` templates, generates topologies cell-by-cell using `generate_hier_topologies`, and plans top-down via `run_planner hier`.

---

## 2. Stage-by-Stage Reference

### Stage 1: Bundler (`bundler.cpp/.h`, `bundle_refiner.cpp/.h`)
*   **Responsibility:** Group nets sharing driver/receiver blocks.
*   **Strategies:**
    *   `STRICT`: Groups nets sharing *both* driver and receiver instances exactly.
    *   `BIDIRECTIONAL` (Recommended for mixed-direction buses): Direction-agnostic grouping based on the sorted set of *all* endpoint instances. Connects all endpoint blocks and is topologically sound.
    *   `CONVERGENT`: Grouped by receiver instances only. **Unsound** for multi-driver buses in the current pipeline (issues warning) because the topology generator only routes from a single representative driver.

### Stage 2: Topology Generator (`topology.cpp/.h`, `conn_topology.cpp/.h`)
*   **Responsibility:** Enumerate candidate routing paths (L-shapes, Z-shapes, U-shapes) on a block-boundary Hanan grid.
*   **Key Algorithms:**
    *   **Trunk+MST Hybrid Completion:** For high fan-out nets, MST edges connect branch blocks. Emitted topologies must be cycle-free trees. The MST is rooted at the branch block closest to the trunk, and child stubs are removed to avoid loops.
    *   **Relay Completion:** Relays (blocks touched by $\geq 2$ segments) are completed by `complete_relay_junctions`. Orthogonal stubs are extended to meet at cell corners; parallel stubs are joined by a single perpendicular jog.
    *   **TEG Modes:** Multi-rect blocks support `thru` (block internal routing resolves gap) and `over` (generates explicit bridge segment `Topology::bridge_segments` across outer faces when trunks land in gaps).
    *   **Corner Margins:** Shrinks bboxes by `dx`/`dy` before generating Hanan lines.

### Stage 3: Congestion Planner (`congestion_planner.cpp/.h`)
*   **Responsibility:** Select one candidate topology per bundle and assign layers to segments.
*   **Parameters:** Tuned via `set_planner_param` (e.g., `kCong`, `kSpan`, `kWL`, `base_cost_non_top`).
*   **Escalation Ladder for Overflow:**
    1.  `STRICT`: Only evaluates overflow-free candidates.
    2.  **Rip-up & Replan**: If STRICT fails, rips up blocker bundles (ranked by demand on overflow cuts) one-by-one and replans.
    3.  `ALLOW_OVERFLOW`: Commits candidate with minimal total overflow cost.
    4.  `BEST_EFFORT`: Ignores slide/repack bounds if no candidate fits.
*   **Hierarchical Mode (`run_planner hier`):** Plans top-down. Cell-level bundles are expanded to instance wrappers. Local wrappers make virtual **demand reservations** on TOP-layer bands to reserve space before global paths are planned.

### Stage 4: Abstract NUTS (`nuts.cpp/.h`)
*   **Responsibility:** Solve 1.5-D rectangle packing per layer using a sweep-line algorithm to assign concrete track positions.
*   **Dogleg Generation:** Detects vertical constraint cycles. Breaks cycles by splitting a trunk into two sub-trunks on different tracks, connected by a perpendicular jog.
*   **Corner Overlap Resolution:** Detects end-to-end touches or overlap conflicts on stretched stubs. Orders trunks to prevent overlap or applies cross-layer splits with track bounds (`track_lo_bound`/`track_hi_bound`).

### Stage 8: Routing Grid Stack (`routing_grid.cpp/.h`)
*   **Responsibility:** Define physical track slot structures (POWER, GROUND, CLOCK, SHIELD, SIGNAL).
*   **Grid Patterns:** Programmed via `def_track_pattern` and overridden locally by `add_grid_override`. Exposes `signal_density()` and `dilution_factor()` to stages 3 & 4.

### Stage 9: Detailed NUTS (`detailed_nuts.cpp/.h`)
*   **Responsibility:** Snap abstract `BusSegment` rows into concrete, bit-level `NetSegment` lines on signal-only tracks.
*   **Vias:** Emits individual `NetVia` rows for bit-level layer crossings. Respects track bounds and timing-critical contiguity requirements.

---

## 3. SQLite Database Layer (BDB) & Design Interchange

The physical layout database lives in `src/bdb.cpp` and SQLite.

### BDB Ingestion & Exchange
*   **`import_def_lef(def, lef)`:** Parses LEF macro sizes/pin locations and DEF placement/netlists. Normalizes all dimensions to microns ($\mu m$).
*   **`import_verilog(v)`:** Elaborates hierarchical module definitions, instantiating `component` structures and propagating nets. UPSERT logic preserves existing DEF placement coords.
*   **GDSII Ingestion & Export (`src/gds_io.cpp`):**
    *   `import_gds`: Resolves cell geometries, hierarchical placements (SREFs, AREFs), and nets via TEXT label layers mapping. Routing shapes on layers mapped with `def_gds_layer` are excluded from cell footprints.
    *   `export_gds`: Exports design outline structures, wires (`net_segment`), and vias (`net_via`) deterministically (zero-timestamp headers) to match binary output across test environments.
*   **OpenAccess (OA) Bridge:** Exposes `import_oa` and `export_oa` signatures in `src/oa_bridge.cpp` (spec-only; gated behind `BUDA_WITH_OA` CMake flag).

### Serialization & Resume
*   **SQL Text Fixtures:** BDB data is checked into git as diffable text dumps (`test/tests/data/*.bdb.sql`).
*   **State Persistence:** Bundles, topologies, selected planner indices, NUTS bus-segments, and detailed net-segments are persisted in BDB tables.
*   **Resume Flow:** `load_pipeline [expanded]` restores the live session's routing state from database rows bit-identically, bypassing expensive candidate generation.

---

## 4. Development & Coding Rules

### C++ / Python Bindings Policy
1.  **Separate DB Bindings:** DB-layer structures (e.g., BDB, Row types, BustermGen) MUST be registered in the standalone `buda_db` Python module via `src/bind_db.cpp` to ensure single-instance `std::type_info` matching and prevent type mismatch segfaults.
2.  **Pipeline Bindings:** Expose routing stages and solvers inside the `buda` Python module via `src/bindings.cpp` and files like `src/bind_nuts.cpp` or `src/bind_routing.cpp`.
3.  **No CD Commands:** Do not propose or run `cd` in terminal commands. Use absolute paths or correct working directory parameters.

### Verification & Testing
*   **Fast Tier (default):** Runs unit tests excluding markers `mid` and `slow`. Runtime is $\sim 8s$.
*   **Mid Tier (`pytest -m "not slow"`):** Fast tests + flow-script integration tests.
*   **Slow Tier (`pytest -o addopts="" -m slow`):** Full suite, including GA/SA optimizer runs.
*   **BDB Fixture Verification:** Tests must not modify original checked-in `.bdb.sql` files. Use the `bdb_input` fixture to work on a temporary copy.
*   **Connectivity Verification:** Run `check_connectivity` at stage completions to catch `SEG_OPEN`, `BUSTERM_OPEN`, `BUSTERM_FACE`, `UNPLACED`, `LAYER_DIR`, or `FEEDTHRU_RELAY` violations.

---

## 5. Future Roadmap

- [ ] **Steiner Tree Heuristic:** Enhance topology generation with Minimum Steiner Tree (MST) support for high fan-out nets.
- [ ] **Multi-Trunk Enhancements:** Support depth-3 trees and source-anchored roots in multi-trunk generators.
- [ ] **OpenAccess Integration:** Implement and verify the `oa_bridge.cpp` bridge using physical Si2 OA SDK libraries.
- [ ] **Negotiated Congestion:** Build PathFinder-style congestion iterations in the global planner to re-price cuts and eliminate routing conflicts globally.
- [ ] **Multi-Victim Rip-up:** Expand `ripup_reroute` to support k-victim replanning sweeps.
- [ ] **GUI Visual Overlays:** Expose congestion heatmaps and routing previews directly in the Floorplanner canvas.
