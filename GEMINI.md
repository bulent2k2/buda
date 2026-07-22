# BUDA: Interconnect Planning System

BUDA (Bundled Unified Design Automation) is an open-source EDA interconnect planning system for chip design. It groups nets into buses, generates routing topologies, plans layer assignments, and resolves detailed track positions—ranging from abstract physical design planning down to bit-level track assignment respecting power-grid patterns and pre-route blockages.

This file provides comprehensive architectural guidance, algorithmic requirements, design patterns, and engineering standards for developers working on the BUDA codebase.

---

## 1. Architectural Overview & Flows

The project is structured around a centralized SQLite-backed database called **BDB** (Buda Physical Design Database). There are two primary execution flows:

```
        ┌─────────────────────────────────────────────────────────────┐
        │ BDB (SQLite)   components · cells · pins · nets · busterms · │
        │                bundles · groups. Central store for the hier │
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
[5] Healers          negotiate_congestion (in-place model correction via PathFinder history)
    │                ripup_reroute (measured search on (DNUTS opens, NUTS overlaps))
    ▼
[8] Routing Grid     per-layer track patterns (power/signal/clock layout)
    │                global pattern per layer + optional Hanan-region overrides
    ▼
[9] Detailed NUTS    snaps each BusSegment → N NetSegments on concrete signal tracks
                     respects pre-route blockages; bit ordering; timing-critical mode
 
[CLI]  buda          orchestrates the flow via .buda scripts (bin/buda, src/buda_cli.py)
[VIZ]  Visualizers   matplotlib (bin/viz, src/buda_viz.py) + TopoExplorer (bin/u2b, tools/unit2buda.py)
[WEB]  Web Server    Scala.js + Python Web client with WebSocket progress (src/web/, web/)
[V]    Verify        check_topo / check_nuts / check_dnuts / check_connectivity / check_design
[FP]   Floorplanner  interactive placement GUI (bin/fp, bin/bfp, tools/bdb_floorplanner.py)
```

### The Two Routing Flows
1. **Flat Flow:** Declare blocks and nets directly in a `.buda` script using `add_block` and `add_net`. Run flat bundlers and topology generators. Ideal for simple, flat designs.
2. **Hierarchy-Aware Flow:** Manage hierarchical designs via BDB (`open_bdb`, `import_def_lef`, `import_verilog`, `import_gds`, or the interactive **Floorplanner** GUI). Derives busterms using `derive_busterms`, runs `run_hier_bundler` to group nets into `HBundle` templates, generates topologies cell-by-cell using `generate_hier_topologies`, and plans top-down via `run_planner hier`. Supports **bottom-up template planning** (`set_bottom_up`, `align_bottom_up`), where cell interconnect is planned once per rotation class (`<cell>90`) and frozen as keepouts for top-level planning.

---

## 2. Stage-by-Stage Reference

### Stage 1: Bundler (`bundler.cpp/.h`, `bundle_refiner.cpp/.h`)
*   **Responsibility:** Group nets sharing driver/receiver blocks.
*   **Strategies:**
    *   `STRICT`: Groups nets sharing *both* driver and receiver instances exactly.
    *   `BIDIRECTIONAL` (Recommended for mixed-direction buses): Direction-agnostic grouping based on the sorted set of *all* endpoint instances. Connects all endpoint blocks and is topologically sound.
    *   `CONVERGENT`: Grouped by receiver instances only. **Unsound** for multi-driver buses in the current pipeline (issues warning) because the topology generator only routes from a single representative driver.
*   **Hierarchical Bundling (`run_hier_bundler`):** Groups nets into cell-local `HBundle` templates that replicate across all cell instances.

### Stage 2: Topology Generator (`topology.cpp/.h`, `conn_topology.cpp/.h`, `topology_analysis.cpp/.h`)
*   **Responsibility:** Enumerate candidate routing paths (L-shapes, Z-shapes, U-shapes) on a block-boundary Hanan grid.
*   **Key Algorithms:**
    *   **Trunk+MST Hybrid Completion:** For high fan-out nets, MST edges connect branch blocks. Emitted topologies must be cycle-free trees. The MST is rooted at the branch block closest to the trunk, and child stubs are removed to avoid loops.
    *   **Relay Completion:** Relays (blocks touched by $\geq 2$ segments) are completed by `complete_relay_junctions`. Orthogonal stubs are extended to meet at cell corners; parallel/collinear stubs are merged with post-convergence span re-tightening.
    *   **TEG Modes:** Multi-rect blocks support `thru` (block internal routing resolves gap) and `over` (generates explicit bridge segment `Topology::bridge_segments` across outer faces when trunks land in gaps).
    *   **Corner Margins:** Shrinks bboxes by `dx`/`dy` before generating Hanan lines.
    *   **Identity & Caching:** Candidates carry a content-fingerprint `topo_uid` used for pinning, selection, snapshot/restore, and duplicate elimination.

### Stage 3: Congestion Planner (`congestion_planner.cpp/.h`)
*   **Responsibility:** Select one candidate topology per bundle and assign layers to segments.
*   **Parameters:** Tuned via `set_planner_param` (e.g., `kCong`, `kSpan`, `kWL`, `kWLSpread`, `kSegsRel`, `base_cost_non_top`).
*   **Planner-Healer Lookahead Coupling:** `kSegsRel=0.02` (compact-topology cost penalty) auto-engages when downstream healers (`negotiate_congestion` / `ripup_reroute`) are present in the flow script, steering the planner toward wide compact trunks that avoid discrete-track shortages.
*   **Escalation Ladder for Overflow:**
    1.  `STRICT`: Only evaluates candidates that are slide-feasible and overflow-free.
    2.  **Rip-up & Replan**: If STRICT fails, rips up blocker bundles (ranked by demand on overflow cuts) one-by-one and replans.
    3.  `ALLOW_OVERFLOW`: Commits candidate with minimal total overflow cost.
    4.  `BEST_EFFORT`: Ignores slide/repack bounds if no candidate fits.
*   **Hierarchical & Bottom-Up Modes (`run_planner hier`):** Cell-level bundles are expanded to instance wrappers. Local wrappers make virtual **demand reservations** on TOP-layer bands. Under `set_bottom_up`, cell templates are solved once per rotation class (`<cell>90`) and copied to congruent instances.

### Stage 4: Abstract NUTS (`nuts.cpp/.h`, `nuts_dogleg.cpp/.h`)
*   **Responsibility:** Solve 1.5-D rectangle packing per layer using a sweep-line algorithm to assign concrete track positions.
*   **Dogleg Generation:** Detects vertical constraint cycles. Breaks cycles by splitting a trunk into two sub-trunks on different tracks, connected by a perpendicular jog. Rejects splits that disconnect the bundle.
*   **Corner Overlap & Pull Clamping:** Detects end-to-end touches or overlap conflicts on stretched stubs. Applies `pull_break` clamps on wide windows to prevent single connectors from overshooting and dragging coupled trunks.
*   **Dead-Span Escalation:** Automatically moves dead LOW-layer segments (with zero keepout-clear signal tracks) to TOP layers before solving (`_heal_dead_spans`).

### Stage 5: Healers (`ripup.py`, `buda_session/ripup.py`)
*   **Responsibility:** Perform closed-loop model correction and measured search to eliminate NUTS overlaps and Detailed NUTS opens.
*   **`negotiate_congestion`:** PathFinder-style in-place model correction. Lays synthetic demand (`inject_band_demand`) on contended bands with accumulating history to break oscillations, then replans offender pairs simultaneously (~0.1–4s workhorse).
*   **`ripup_reroute`:** Measured search on lexicographic metric `(DNUTS opens, NUTS overlaps)`. Reaches back into topo-gen's full candidate pool (+8 candidates beyond planner's cheap-8 window), uses fixed-context screening and fast-trial evaluations, and targets bundles with NUTS overlaps, Detailed NUTS opens, or `junction_infeasibilities`.

### Stage 8: Routing Grid Stack (`routing_grid.cpp/.h`)
*   **Responsibility:** Define physical track slot structures (POWER, GROUND, CLOCK, SHIELD, SIGNAL).
*   **Grid Patterns:** Programmed via `def_track_pattern` and overridden locally by `add_grid_override`. Exposes `signal_density()` and `dilution_factor()` to stages 3 & 4.
*   **Track-Phase Alignment:** `align_bottom_up` nudges `set_bottom_up` instances into track-phase alignment across rotation classes using circular L1 median phase targets.

### Stage 9: Detailed NUTS (`detailed_nuts.cpp/.h`)
*   **Responsibility:** Snap abstract `BusSegment` rows into concrete, bit-level `NetSegment` lines on signal-only tracks.
*   **Vias & Contiguity:** Emits individual `NetVia` rows for bit-level layer crossings. Respects track bounds, pre-route blockages, bit ordering, and timing-critical contiguity requirements. Counts severed-bus bits as opens during verification and ripup.

---

## 3. SQLite Database Layer (BDB), Interchange & Serialization

The physical layout database lives in `src/bdb.cpp` and SQLite.

### BDB Ingestion & Exchange
*   **`import_def_lef(def, lef)`:** Parses LEF macro sizes/pin locations and DEF placement/netlists. Normalizes all dimensions to microns ($\mu m$).
*   **`import_verilog(v)`:** Elaborates hierarchical module definitions, instantiating `component` structures and propagating nets. UPSERT logic preserves existing DEF placement coords.
*   **GDSII Ingestion & Export (`src/gds_io.cpp`):**
    *   `import_gds`: Resolves cell geometries, hierarchical placements (SREFs, AREFs), and nets via TEXT label layer mapping. Routing shapes on layers mapped with `def_gds_layer` are excluded from cell footprints.
    *   `export_gds`: Exports design outline structures, wires (`net_segment`), and vias (`net_via`) deterministically (zero-timestamp headers) to match binary output across test environments.
*   **OpenAccess (OA) Bridge:** Exposes `import_oa` and `export_oa` signatures in `src/oa_bridge.cpp` (spec-only; gated behind `BUDA_WITH_OA` CMake flag).

### Serialization & Resume
*   **SQL Text Fixtures:** BDB data is checked into git as diffable text dumps (`test/tests/data/*.bdb.sql`).
*   **State Persistence & Resume:** Bundles, topologies, selected planner indices, NUTS bus-segments, and detailed net-segments are persisted in BDB tables.
*   **Rehydration Flow:** `load_pipeline [expanded]` restores the live session's routing state from database rows bit-identically (restoring `seg_busterms` from `topology_seg_busterm` links and bridge segments from `topology_bridge_segment`), bypassing candidate regeneration and allowing multi-stage pipeline checkpoints.

---

## 4. Development & Tooling Standards

### C++ / Python Bindings Policy
1.  **Separate DB Bindings:** DB-layer structures (e.g., BDB, Row types, BustermGen) MUST be registered in the standalone `buda_db` Python module via `src/bind_db.cpp` to ensure single-instance `std::type_info` matching and prevent type mismatch segfaults.
2.  **Pipeline Bindings:** Expose routing stages and solvers inside the `buda` Python module via `src/bindings.cpp` and specialized binding files (`src/bind_nuts.cpp`, `src/bind_routing.cpp`, `src/bind_optimizer.cpp`). Both `buda_db` and `buda` link `libbuda_core.so`.
3.  **No CD Commands:** Do not propose or run `cd` in terminal commands. Use absolute paths or correct working directory parameters.

### Execution & Launcher Wrappers (`bin/`)
*   Always use or recommend the repo-root `bin/` wrappers:
    *   `source bin/activate`: Prepend `bin/` to `PATH` and set `PYTHONPATH=build:tools`.
    *   `bin/bb`: Wrapper for CMake/Ninja build (`--clean`, `test`, `mid`, `slow`).
    *   `bin/buda`: CLI routing runner (`src/buda_cli.py`).
    *   `bin/fp` / `bin/bfp`: Floorplanner GUI launchers (`tools/bdb_floorplanner.py`).
    *   `bin/u2b`: Topology unit-test converter & visualizer (`tools/unit2buda.py`).
    *   `bin/viz`: DEF/BDB visualizer launcher.
*   **macOS `.app` Bundles:** `tools/make_macos_apps.py` builds `bin/Buda.app` and `bin/Floorplanner.app` for native macOS Dock icons.

### Verification & Testing Tiers
*   **Fast Tier (`bin/bb test` / `pytest` default):** Core unit and stage tests. Runtime is $\sim 8s$.
*   **Mid Tier (`bin/bb mid` / `pytest -m "not slow"`):** Fast tests + flow-script integration tests.
*   **Slow Tier (`bin/bb slow` / `pytest -o addopts="" -m slow`):** Full suite, including GA/SA optimizer storms.
*   **BDB Fixture Verification:** Tests must not modify original checked-in `.bdb.sql` files. Use the `bdb_input` fixture to work on a temporary copy.
*   **Feature Coverage:** Gherkin specs in `test/tests/features/` follow `@landed`, `@future`, `@doc`, and `@orphaned` tag vocabulary guarded by `test_feature_files.py`.
*   **Connectivity Verification:** Run `check_connectivity` and `check_design` at stage completions to catch `SEG_OPEN`, `BUSTERM_OPEN`, `BUSTERM_FACE`, `UNPLACED`, `LAYER_DIR`, or `FEEDTHRU_RELAY` violations.

---

## 5. Out-of-Date Documents & Known Discrepancies

When working with documentation in this repository, note the following outdated files:

*   **`README.md`**: Contains an outdated source tree diagram (lists non-existent top-level `tests/` and `extern/pybind11/` directories; missing `tools/`, `web/`, `bin/`, and modern C++ engine modules in `src/`). Refer to [CLAUDE.md](CLAUDE.md) or this file for current structure.
*   **`README_build`**: Obsolete build guide referencing a non-existent `setup_buda.py` script and `buda_system/` folder. Use `bin/bb` or standard CMake in `build/`.
*   **`README_viz`**: Obsolete visualizer guide referencing a non-existent `buda_system_v2` directory. Use `bin/buda` / `bin/viz` / `bin/u2b`.
*   **`tools/ReadMe_tools.md`**: Outdated path references (`~/src/buda`, `buda_system_v2/`, relative `src/buda_cli.py` paths) predating the `bin/` wrappers and repository layout consolidation.
*   **`docs/internal/ReadMe.md`**: Scratch artifact stub file.

---

## 6. Future Roadmap

- [ ] **Steiner Tree Heuristic:** Enhance topology generation with Minimum Steiner Tree (MST) support for high fan-out nets.
- [ ] **Multi-Trunk Enhancements:** Support depth-3 trees and source-anchored roots in multi-trunk generators.
- [ ] **OpenAccess Integration:** Complete verification of `oa_bridge.cpp` using physical Si2 OA SDK libraries.
- [ ] **Pre-Charge Forecast Horizon:** Build a two-pass demand forecasting layer between topology generation and the global planner to price incoming arrivals before greedy assignment.
- [ ] **Healer Metric Disconnection Coverage:** Incorporate `DISCONNECTED` bit counts directly into stage-b healer lexicographic metrics `(opens, disconnected, overlaps)`.

