# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

**BUDA** (Bundled Unified Design Automation) is an **EDA interconnect planning system** for chip design. It bundles nets into buses, generates routing topologies (L/Z/U-shapes on a Hanan grid), and assigns routes to metal layers based on congestion analysis.

The core engine is **C++20** exposed to Python via **pybind11**, with a Python CLI and matplotlib visualization layer on top.

## Build

```bash
# Build v2 (current)
cd buda_system_v2/
mkdir -p build && cd build
cmake .. && make -j4

# Copy the compiled shared library so Python can import it
cd ..
cp build/interconnect.cpython-313-darwin.so src/
```

CMake builds a single shared library module (`interconnect`) with `-O3 -march=native -Wall -Wextra`.

## Run

```bash
cd buda_system_v2/src/
python3 buda_cli.py comprehensive_demo.buda
python3 buda_cli.py design_demo.buda
```

`.buda` script syntax:
```
def_layer <id> <name> <H|V> <TOP|LOW> <overhead_percent>
add_block <name> <x1> <y1> <x2> <y2>
add_net <name> <driver_pin> <receiver_pins_csv>
run_bundler <STRICT|CONVERGENT>
generate_topologies_for_bundle <hint> <src_block> <dst_block>
run_planner <iterations>
run_nuts [track_pitch]      # optional; enables NUTS track assignment
visualize                   # draws NUTS positions when run_nuts was called
```

## Tests

Tests live in `test/tests/` and use **pytest-bdd** with Gherkin feature files in `test/tests/features/`.

```bash
# Run all tests
cd test/tests/
pytest

# Run a single test file
pytest test_bundler.py
pytest test_topology.py
pytest test_global_congestion.py
```

Feature files: `bundler_hierarchy.feature`, `bundler_logic.feature`, `topology_generation.feature`, `global_congestion.feature`, `layer_assignment.feature`, `detailed_track_assignment.feature`.

## Architecture

### Pipeline (in order)

1. **Bundler** (`bundler.h/cpp`) — groups nets by shared driver/receiver connectivity into `Bundle` objects.
   - `STRICT` strategy: same driver AND receivers → one bundle
   - `CONVERGENT` strategy: shared receivers only → one bundle

2. **Topology Generator** (`topology.h/cpp`) — for each bundle, generates candidate routing paths using the Hanan grid (block boundaries):
   - L-shape: 2 segments (horizontal + vertical)
   - Z-shape: 3 segments via intermediate Hanan grid point
   - U-shape: 3 segments that detour outside the bounding box

3. **Bundle Planner / Global Router** (`global_router.h/cpp`) — builds "cuts" from floorplan grid lines, tracks capacity/usage per layer cut, applies a dilution factor for layer overhead, and selects the best topology per bundle. After this step every segment has a layer, a soft routing-direction span, and a hard perpendicular interval from the Hanan grid cell it occupies.
   - Dilution formula: `effective_width = raw_width × (100 / (100 − overhead_percent))`

4. **NUTS – Non-Uniform Track Sharing** (`nuts.h/cpp`) — solves the 1.5-D rectangle packing problem (Ekici, Basaran & Keskinocak 2009) per layer. Each bus segment is treated as a rectangle: its routing-direction span is fixed; its perpendicular position must lie within a hard Hanan-grid-cell interval. A sweep-line / first-fit algorithm assigns concrete track positions with no physical overlaps.
   - Key types: `TrackSegment` (input + output per segment), `NUTSResult` (flat list of placed segments + violation/overlap counts).
   - Runs independently per layer (parallelisable).
   - Best-effort placement when an interval is too narrow; violation is counted in `NUTSResult::num_violations`.

5. **Layer Stack** (`layering.h/cpp`) — metadata for the metal layer stack (direction H/V, type TOP/LOW).

5. **CLI** (`buda_cli.py`) — parses `.buda` scripts and drives the pipeline.

6. **Visualization** (`buda_viz.py`) — matplotlib rendering of blocks, Hanan grid, routed buses with non-uniform widths, driver terminals (cyan squares), receiver terminals (magenta circles). Layer colors: M3 horizontal = blue, M4 vertical = red.

### Two System Versions

- `buda_system/` — v1 (older, kept for reference)
- `buda_system_v2/` — v2 (current; adds U-shapes, non-uniform widths in visualization)

`setup_buda.py` at the repo root is a generator script that can regenerate the project skeleton from templates.

## Dependencies

- **pybind11** — C++/Python bindings
- **Python 3.13+**
- **matplotlib** — visualization
- **pytest** + **pytest-bdd** — testing
- **CMake 3.15+**
