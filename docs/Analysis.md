# BUDA Codebase Analysis

Date: 2026-05-26

## Executive Summary

BUDA is a prototype EDA/interconnect planning system. It takes a floorplan and
netlist, groups related nets into buses, generates candidate bus topologies,
chooses topology/layer assignments with a congestion-aware global planner, then
assigns abstract and detailed tracks.

Most core algorithms are implemented in C++20 and exposed to
Python through a `pybind11` module named `buda`. Python provides the
script runner, session orchestration, visualization, sidecar persistence, and
diagnostic logging.

## Repository Layout

Primary directories:

- `src/`: C++ engines, Buda Physical Design Database (BDB), Python CLI, and visualizer.
- `flow/`: `.buda` example/design scripts and sidecar `.json` selections.
- `docs/`: User-facing and design documentation.
- `test/tests`: pytest and pytest-bdd coverage for the C++ module via Python.
- `tools`: Helper tooling, including DEF/LEF clustering and visualization utilities.
- `debug`, `log`, `history`, `fig`: Exploratory scripts, notes, logs, and figures.

## Build And Runtime Model

`CMakeLists.txt` builds a single Python extension module:

```text
buda
```

The extension includes:

- `bindings.cpp`
- `bdb.cpp`
- `sqlite3.c`
- `bundler.cpp`
- `bundle_refiner.cpp`
- `topology.cpp`
- `conn_topology.cpp`
- `layering.cpp`
- `congestion_planner.cpp`
- `nuts.cpp`
- `routing_grid.cpp`
- `detailed_nuts.cpp`
- `busterm.cpp`
- `verify.cpp`

The Python entry point is `src/buda_cli.py`. It imports
`buda`, interprets `.buda` files line by line, and stores mutable flow
state in `BudaSession`.

Typical flow:

```text
def_layer / def_track_pattern
add_block / add_keepout
add_net / add_bus
run_bundler
generate_topologies
run_planner
run_nuts
run_detailed_nuts
visualize
```

## Core Data Flow

1. Technology and floorplan setup

   The script defines metal layers, optional track patterns, blocks, multi-rect
   blocks, corner margins, keepouts, and nets.

2. Bundling

   `Netlist` stores nets. `Bundler` groups nets into `Bundle` objects. A bundle
   contains net names, an ID, a reason string, and terminal count metadata.

3. Topology generation

   `TopologyGenerator` creates candidate Manhattan route shapes for each bundle.
   Two-pin bundles generate L/Z/U/UU-style candidates. Multi-pin bundles can
   generate trunk, multi-trunk, and MST-style candidates. Topologies carry raw
   `Segment` geometry plus endpoint annotations via `seg_busterms`.

4. Connectivity enrichment

   `ConnTopology` turns raw topology segments into connected segments. It
   infers busterm-to-segment and segment-to-segment links, computes slide
   ranges, and computes net-pull hints for later placement.

5. Global planning

   `GlobalRouter` builds congestion cuts from the floorplan Hanan grid and
   evaluates candidate topology/layer assignments. It writes the selected
   topology index, per-segment layer assignments, and representative H/V layers
   back into `BundleWrapper`.

6. Abstract NUTS placement

   `NUTSEngine` extracts selected topology segments into `TrackSegment`
   rectangles. Each segment has a fixed span and a hard perpendicular interval.
   NUTS places each layer independently using sweep-line/first-fit and
   preferred-fit behavior, then reports violations and physical overlap pairs.

7. Detailed NUTS placement

   `RoutingGridStack` defines real signal/power/ground/clock track patterns.
   `DetailedNUTSEngine` expands each abstract bus segment into bit-level
   `NetSegment` placements on concrete signal tracks.

8. Visualization and sidecars

   `buda_viz.py` renders floorplans, topologies, NUTS results, detailed tracks,
   congestion information, keepouts, and interactive topology selection.
   Topology choices persist to sidecar `.json` files next to `.buda` scripts.

## Major Modules

### `bundler.h/.cpp`

Defines:

- `Net`
- `Netlist`
- `Bundle`
- `Bundler`
- `Strategy`

The bundler is intentionally simple. It groups nets by signatures derived from
driver/receiver structure. `STRICT` and `CONVERGENT` strategies are exposed,
and the header includes a `depth_` field for hierarchy-aware behavior.

### `topology.h/.cpp`

Defines geometry and topology generation:

- `Point`, `Rect`, `Segment`
- `Busterm`
- `Topology`
- `Floorplan`
- `TopologyGenerator`

Important capabilities:

- Busterm mode connects to block faces rather than centers.
- Corner margins can be global or per block.
- Multi-rect blocks support terminal-equivalence and rectilinear/notched
  modeling.
- `TegMode::THRU` assumes internal connectivity across rects.
- `TegMode::OVER` emits bridge annotations when explicit over-the-block
  connectivity is needed.
- Keepout zones are stored in the floorplan.
- Minimum stub length can be global, per direction, or per layer.

The topology layer is a central dependency: the planner, NUTS, visualization,
and many tests all consume its segment geometry and annotations.

### `conn_topology.h/.cpp`

This is the bridge between static geometry and routable connectivity. It
augments a `Topology` with `ConnSeg` records containing:

- orientation and layer
- along-span and perpendicular position
- perpendicular slide bounds
- busterm and segment connections
- net-pull direction

This module is important because NUTS depends on slide intervals and pull hints,
while tests use it heavily to assert connector shape and topology correctness.

### `layering.h/.cpp`

Defines the metal stack:

- layer ID and name
- horizontal/vertical direction
- `TOP` versus `LOW` preference
- dilution factor / overhead
- span preference window
- per-layer `kSpan` override

Layering feeds both global routing costs and later physical track assignment.

### `congestion_planner.h/.cpp`

The congestion planner chooses topology and layer assignments. Its public knobs are:

- `kCong`
- `kSpan`
- `base_cost_non_top`

It builds congestion cuts and scores candidate segments by overflow, effective
width, layer preferences, and span mismatch. It returns `BundleAssignment`
objects, which the Python session copies back onto the corresponding
`BundleWrapper`.

### `nuts.h/.cpp`

NUTS is the abstract track assignment engine. It treats each bus segment as a
rectangle:

- fixed routing-direction span
- movable perpendicular center
- hard placement interval
- physical width

It solves each layer independently, reports interval violations, and computes
exact overlap rectangles. It also supports `rerun_layer`, which re-solves a
single layer while preserving the larger result context.

### `routing_grid.h/.cpp`

This module models real repeating track patterns:

- `TrackSlot`: signal, power, ground, clock, shield, custom
- `TrackPattern`: repeating pitch unit with signal density and dilution
- `PatternOverride`: region-specific local pattern
- `RoutingGrid`: per-layer pattern plus keepouts
- `RoutingGridStack`: registry by layer ID

This is the bridge from abstract bus widths to concrete track availability.

### `detailed_nuts.h/.cpp`

Detailed NUTS turns bus-level placement into bit-level placement. A
`BusSegment` has bit width, bit order, layer, span, interval, adjacent segment
metadata, and abstract anchor position. The output is a list of `NetSegment`
placements and a count of unplaced bits.

### `bindings.cpp`

`bindings.cpp` is the API contract between C++ and Python. It exposes nearly
all important structs and engines, including direct mutable fields for many
objects. This makes tests and the CLI straightforward, but it also means Python
can construct states that would be hard to validate from C++ alone.

### `buda_cli.py`

`BudaSession` owns the long-lived state:

- `Floorplan`
- `Netlist`
- `LayerStack`
- `Bundler`
- `GlobalRouter`
- bundles/wrappers
- NUTS and detailed-NUTS results
- layer-name maps
- sidecar path state
- routing grid definitions

The CLI command parser is simple and imperative. It recognizes setup commands,
planning commands, NUTS commands, visualization commands, and `source` for
nested scripts.

Important behavior:

- Sidecar `.json` selections can pin topology choices and per-segment layers.
- `run_planner post_nuts` reassigns short/long stubs after NUTS and reruns NUTS.
- `run_nuts_on_layer` supports targeted layer re-solving.
- NUTS diagnostics are written to `<script>_nuts.log`.
- `--no-viz` disables visualization for batch/test use.

### `buda_viz.py`

Visualization is Matplotlib based. It includes a topology explorer with bundle
and topology navigation, sidecar selection persistence, rerun hooks, keepout
display, layer coloring, and NUTS/detailed-NUTS overlays.

This is not only a viewer: it is part of the interactive planning workflow
because it persists user-selected topology and layer decisions.

### `tools/def_cluster.py`

This helper converts placed DEF/LEF data into BUDA-friendly abstractions. It can
cluster nets by pin centroid, bipartite driver/receiver clusters, grid cells, or
high-fanout extraction. It emits `add_block`/bus-style BUDA content. This is the
main bridge from real design data into the prototype flow.

## Script Language Surface

The `.buda` language is intentionally command-oriented. Important commands:

- `def_layer`
- `def_track_pattern`
- `add_grid_override`
- `add_block`
- `corner_margin`
- `set_min_stub_length`
- `set_min_stub_length_dir`
- `set_min_stub_length_layer`
- `add_keepout`
- `add_net`
- `add_bus`
- `run_bundler`
- `generate_topologies`
- `generate_topologies_for_bundle`
- `set_planner_param`
- `run_planner`
- `run_planner post_nuts`
- `run_nuts`
- `run_nuts_on_layer`
- `run_detailed_nuts`
- `visualize_topologies`
- `visualize`
- `source`

The command language is documented in `docs/BUDA_SCRIPT_REFERENCE.md`.

## Testing Surface

Tests live in `test/tests` and use `pytest` plus `pytest-bdd`. Shared step
fixtures in `conftest.py` construct `interconnect` objects directly.

Notable coverage areas:

- bundling
- topology generation
- unified topology behavior
- connector shape
- corner margins
- minimum stub length
- keepout zones
- routing grid
- detailed NUTS
- NUTS track assignment
- span-aware layer assignment
- global congestion
- feedthrough
- multi-rect blocks
- multi-level trunk
- multicast topology
- hierarchy/depth planning
- busterm over-the-block behavior
- flow script execution

The test suite is integration-heavy in a useful way: many tests exercise the
actual Python-facing C++ module rather than isolated C++ internals.

## Engineering Findings

### Strengths

1. Clear staged architecture

   The main flow is coherent: netlist -> bundles -> topologies -> global
   planning -> abstract tracks -> detailed tracks -> visualization.

2. Productive C++/Python split

   Geometry and placement logic live in C++ while Python provides a fast-moving
   scripting and visualization shell.

3. Strong domain modeling for a prototype

   Concepts such as busterms, corner margins, multi-rect blocks, TEG modes,
   keepouts, layer dilution, span preferences, sidecar selections, and
   detailed track patterns are all represented explicitly.

4. Interactive workflow is built into the design

   Sidecar persistence means the visualizer is not just diagnostic. It is a
   manual override and exploration tool that feeds future runs.

5. Good test direction

   There are tests for many behaviors that are easy to regress: topology shape,
   margins, NUTS placement, detailed tracks, and multi-rect edge cases.

### Risks And Weak Spots

1. `buda_cli.py` is doing too much

   `BudaSession` mixes parsing, flow orchestration, sidecar IO, logging,
   diagnostics, post-NUTS heuristics, detailed-NUTS conversion, and visualizer
   integration. This is workable for a prototype but will become difficult to
   maintain as commands grow.

2. The Python API exposes highly mutable C++ objects

   Many pybind classes use direct `def_readwrite` fields. This helps tests and
   interactive use but weakens invariants. Invalid combinations can be produced
   outside the C++ constructors or engine methods.

3. Some command parsing is ad hoc

   The `.buda` parser is a split-on-whitespace interpreter with per-command
   parsing logic. That keeps the language simple, but robust error reporting,
   quoting, validation, and forward compatibility are limited.

4. Bundle endpoint metadata is split across layers

   The Python session stores `_net_endpoints` separately because the C++ Bundle
   currently carries net names but not canonical source/destination block
   metadata. This is a recurring source of lookup and convention dependency.

5. Codebase has been cleaned up

   The repository has been restructured to merge active development files into a clean root-level layout, eliminating duplicate or legacy directories.

6. Visualization is tightly coupled to runtime state

   `buda_viz.py` directly knows about floorplans, wrappers, sidecars, rerun
   callbacks, NUTS details, and layer behavior. This is pragmatic but makes GUI
   changes risky because they can affect the planning loop.

7. Docs are broad but scattered

   The codebase has good notes, but users must read several files to understand
   the current architecture: `README.md`, `docs/USER_GUIDE.md`,
   `docs/BUDA_SCRIPT_REFERENCE.md`, `src/DESIGN_NOTES.md`, and
   feature-specific docs.

## Suggested Next Improvements

1. Split `BudaSession` into smaller units

   Candidate boundaries:

   - script parser / command dispatcher
   - design state model
   - planning pipeline service
   - sidecar persistence
   - diagnostics/logging
   - visualization adapter

2. Add a first-class C++/Python `BundleEndpoint` model

   Store driver block and receiver blocks with the bundle instead of relying on
   Python-side `_net_endpoints` and net-name conventions.

3. Add validation before each pipeline stage

   Each command such as `generate_topologies`, `run_planner`, `run_nuts`, and
   `run_detailed_nuts` should have a clear preflight check with actionable
   errors.

4. Formalize the `.buda` command grammar

   A small parser layer would make the command surface easier to extend and
   easier to test. It does not need to be a heavy language implementation.

5. Clarify active directories

   The project directories have been flattened. Active source files reside under `src/` and design runs under `flow/`.

6. Keep expanding flow-level regression tests

   The most valuable tests are probably script-level flows that exercise
   realistic designs and assert planner/NUTS invariants: no crashes, bounded
   overlaps, no interval violations, expected sidecar behavior, and detailed
   track placement counts.

## Practical Onboarding Path

For a new developer, the shortest path through the codebase is:

1. Read `docs/USER_GUIDE.md` for the pipeline.
2. Read `docs/BUDA_SCRIPT_REFERENCE.md` for command syntax.
3. Run or inspect `demo/quickstart.buda`.
4. Read `src/buda_cli.py` to understand orchestration.
5. Read the C++ headers in this order:
   `bdb.h`, `bundler.h`, `topology.h`, `conn_topology.h`, `layering.h`,
   `congestion_planner.h`, `nuts.h`, `routing_grid.h`, `detailed_nuts.h`.
6. Use tests in `test/tests` as executable examples of expected behavior.

## Bottom Line

The codebase is a serious prototype with a coherent staged architecture and a
substantial amount of routing-domain behavior already modeled. Its strongest
asset is the clear algorithmic pipeline backed by Python-accessible C++ engines.
The main maintenance risk is orchestration complexity in Python and loose
mutable boundaries between stages. The next large quality step is to preserve
the current experimentation speed while introducing clearer stage interfaces,
preflight validation, and a more explicit design state model.
