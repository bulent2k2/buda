# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

**BUDA** (Bundled Unified Design Automation) is an **EDA interconnect planning system** for chip design. It bundles nets into buses, generates routing topologies, assigns routes to metal layers, and resolves physical track positions — from abstract bus planning down to individual bit-wire placement respecting power-grid and pre-route blockages.

The core engine is **C++20** exposed to Python via **pybind11**, with a Python CLI and matplotlib visualization layer on top.

### Two ways to drive BUDA

The project is **BDB-centric** (v3 architecture). All hierarchical physical-design data lives in a SQLite-backed **BDB** (Buda Physical Design Database). There are two entry flows:

1. **Flat flow** — declare blocks and nets directly in a `.buda` script (`add_block`, `add_net`), bundle them, generate topologies, plan, NUTS. No hierarchy. This is the original demo flow and what most stage docs below describe.
2. **Hierarchy-aware flow** — open/build a BDB (`open_bdb`, `import_def_lef`, `import_verilog`, or the interactive **Floorplanner**), derive busterms, then run the `hier` variants (`run_hier_bundler`, `generate_hier_topologies`, `run_planner hier`). Templates are solved once per cell type and instantiated at every occurrence.

The **Floorplanner** (`./fp`, `./bfp`) is a separate interactive GUI tool that edits block placement in a BDB and can launch the hier routing flow directly.

## Useful Docs
- [User Guide](docs/USER_GUIDE.md) — Prerequisites and standard flow for novices.
- [BUDA Script Reference](docs/BUDA_SCRIPT_REFERENCE.md) — Detailed command documentation.
- [BDB Reference](docs/BDB_REFERENCE.md) — Physical design database: schema, `.buda` commands, Python API.
- [Floorplanner User Guide](docs/FLOORPLANNER_USER_GUIDE.md) / [Reference](docs/FLOORPLANNER_REFERENCE_GUIDE.md) — Interactive placement GUI and engine API.
- [Hier Bundler](docs/HIER_BUNDLER.md), [Hier Topology](docs/HIER_TOPOLOGY.md), [Hier Planner](docs/HIER_PLANNER.md) — Hierarchy-aware pipeline internals.
- [Cross-Level Bundling](docs/cross_level_bundling.md) and [HBundle Pipeline session notes](docs/session_hbundle_pipeline.md) — How the hier flow was built (Phases A–E).
- [Congestion Planner](docs/congestion_planner.md) — Internal design of the bundle planner: cost model, hard overflow constraint, rip-up & replan.
- [Detailed NUTS](docs/detailed_nuts.md) — Internal design of bit-level track assignment.
- [Routing Grid](docs/routing_grid.md), [Detailed Viz](docs/detailed_viz.md), [Key Bindings](docs/KEY_BINDINGS.md).
- [BDB → Flat Script Converter](docs/BDB2BUDA.md) — `tools/bdb2buda.py`: export a BDB as a flat `.buda` routing script.
- [Flat Script → BDB Cell Converter](docs/BUDA2BDB.md) — `tools/buda2bdb.py`: ingest a flat `.buda` script into a BDB as a cell (reverse of `bdb2buda`; replaces an existing cell and size-syncs its instances).
- [Hierarchical Demo BDB Builder](docs/BUILD_HIER_DEMO.md) — `tools/build_hier_demo.py`: assemble a hierarchical BDB from several flat scripts (each instantiated twice in a top cell) with random cross-instance top-level buses.

## Build

Use the build wrapper script `bb` at the repository root. By default it performs an **incremental** build:

```bash
./bb            # incremental build into build/
./bb --clean    # clean rebuild (-c also works)
./bb test       # build, then run the FAST test tier (~8s; -t also works)
./bb mid        # build, then run FAST + MID tiers (+flow-script integration; -m also works)
./bb slow       # build, then run ALL tiers (+SA/GA optimizer storms; -s also works)
./bb --help     # describe all options (-h)
```

Tests are split into three cumulative tiers via pytest markers (`mid`, `slow` in
`pytest.ini`); the default run excludes both. Per-test runtimes and the tier
rationale live in [docs/internal/test_runtime_analysis.md](docs/internal/test_runtime_analysis.md).

Manual build:

```bash
mkdir -p build && cd build
cmake .. && make -j4
```

All build artifacts remain in `build/`. After a build, `bb` removes any stale `.so` copies from `src/` so they cannot shadow the fresh build. Compiled with `-O3 -march=native -Wall -Wextra` (`/O2` on MSVC).

CMake builds **three** artifacts (see `CMakeLists.txt`):

| Target | Kind | Contents |
|---|---|---|
| `buda_core` | shared lib (`libbuda_core.so`) | BDB + SQLite + busterm + bundler + bundle_refiner. The single compiled copy of the DB-layer types. |
| `buda_db` | Python module | Registers BDB / row types / BustermGen in pybind11's global type registry. Importable standalone. |
| `buda` | Python module | Full routing pipeline. Imports `buda_db` and re-exposes its names (so `buda.BDB == buda_db.BDB`), links `buda_core`. |

Both extension modules link the same `buda_core`, giving pybind11 one `std::type_info` per class — this is what lets `buda.BDB` objects pass into `buda` C++ functions taking `BDB&` without a type-info mismatch/segfault. **When adding a DB-layer type, register it in `buda_db` (via `bind_db.cpp`), not `buda`.**

## Run

Prefer the wrapper scripts at the repo root (they set `PYTHONPATH=build:tools`):

```bash
./buda flow/comprehensive_demo.buda     # routing CLI (src/buda_cli.py)
./fp  [file.bdb]                         # interactive Floorplanner GUI (tools/bdb_floorplanner.py)
./bfp tc1                                # Floorplanner with a built-in demo scenario
./bfp flow/some.buda                     # run a .buda flow, then open its BDB in the Floorplanner
```

Or run the CLI directly:

```bash
PYTHONPATH=build python3 src/buda_cli.py flow/comprehensive_demo.buda
```

Set `export PYTHONPATH=build` once per shell session if invoking Python directly.

`.buda` script command reference:

**BDB / hierarchy setup** (hier flow — see [BDB Reference](docs/BDB_REFERENCE.md)):

| Command | Description |
|---|---|
| `open_bdb <path.bdb>` | Open (creating if needed) a BDB for hierarchy-based design data |
| `import_def_lef <def> <lef>` / `import_verilog <v>` | Ingest a placed design / netlist into the open BDB |
| `set_die <w> <h>` | Set die dimensions in the BDB |
| `add_cell <name> <w> <h>` / `add_cell_pin <cell> <pin> [dir] [px py]` | Define a cell type and its port interface |
| `add_inst <inst> <cell> <parent> <x> <y>` / `add_inst_to_cell <parent_cell> <inst> <child_cell> <x> <y>` | Instantiate a cell into the hierarchy / define a cell's internal structure |
| `add_comp <name> <cell> <parent> <x1> <y1> <x2> <y2>` | Insert a component row with explicit absolute coords |
| `move_comp` / `flip_comp` / `rotate_comp` / `resize_cell` / `set_comp_*` | Mutate placement (move, mirror, rotate 90/180/270, resize) |
| `add_blocks_from_bdb [depth N]` | Load BDB components at a hierarchy depth into the flat Floorplan |
| `bdb_net_mode <on\|off>` | Mirror `add_net`/`add_bus` into the BDB net/pin tables |
| `derive_busterms [max_depth N]` / `refine_busterms` | Populate the busterm table from the component hierarchy (Phase A of hier flow) |

**Routing pipeline** (`.buda` script):

| Command | Stage | Description |
|---|---|---|
| `add_block <name> <x1> <y1> <x2> <y2> [container] [corner_margin ...]` | setup | Place a single-rect floorplan block; `container` marks a hierarchy envelope (transparent to LOW layers; leaf cells block LOW layers as keepouts) rather than a solid leaf cell; optional per-block corner margin (absolute or `pct_h`/`pct_v`) |
| `add_block <name> rect <x1> <y1> <x2> <y2> [rect ...] [teg_mode thru\|over] [corner_margin ...]` | setup | Multi-rect block: topology generator picks the best-fit rect per trunk position; `teg_mode over` generates an explicit bridge segment over the block's notch when the trunk falls in a gap between rects |
| `add_keepout <x1> <y1> <x2> <y2> <layer_list>` | setup | Define a rectangular keep-out zone for specific routing layers |
| `corner_margin dx <n> [dy <n>]` | setup | Set global corner margin for all blocks with no per-block override. Only `dx`/`dy` (absolute); `pct_h`/`pct_v` not valid globally. Single-axis value mirrors to the other axis. |
| `add_net <name> <driver_pin> <receiver_pins_csv> [unknown\|inout]` | setup | Add a net to the netlist; `unknown` = undirected (positional fallback), `inout` = bidirectional (INOUT treated as secondary driver) |
| `add_bus <prefix>[<N>] <drv_pin> <rcv_pin_csv>` | setup | Expand a bus into N nets: `prefix[N]` → `prefix_0`…`prefix_{N-1}`; `prefix[lo:hi]` → explicit range |
| `def_layer <id> <name> <H\|V> [TOP\|LOW] <overhead%> [span_min N] [span_max N] [kSpan K]` | setup | Register a metal layer; `TOP` marks it for trunk preference; optional span limits and per-layer congestion weight override |
| `set_min_stub_length <n>` / `set_min_stub_length_dir <H\|V> <n>` / `set_min_stub_length_layer <layer> <n>` | setup | Floor on stub segment length: global, per-direction, or per-layer (avoids tiny unroutable stubs) |
| `set_feedthru <blocks\|*> <layers\|*> [on\|off]` | setup | Mark a block×layer set as opt-in feedthru (routable-through); resolved most-specific-first `(block,layer) > (block,*) > (*,layer) > global`. A fed-through block must be a **bundle busterm the trunk passes through**: the `TRUNK_H`/`TRUNK_V` generator splits the spine at its faces (two BUSTERM landings, bridged by the block's own routing) and `check_topo` accepts the declared relay. Straight/I-shape feedthru + `feedthru_penalty` ranking are later phases. |
| `detour_channel <N\|S\|E\|W\|A> <width>` | setup | Reserve an outer detour band of the given width on one side (or `A` = all four) for U-shape routes |
| `run_bundler <STRICT\|CONVERGENT\|BIDIRECTIONAL>` | 1 | Group nets into buses (CONVERGENT = fan-in by shared receiver; BIDIRECTIONAL = direction-agnostic, sorted set of all endpoint instances, so A→B bundles with B→A and cyclic a→b,c/b→c,a/c→b,a. CONVERGENT can span multiple driver *blocks* → warn; BIDIRECTIONAL connects the same blocks so it's sound. See docs/internal/convergent_bundling.md) |
| `run_hier_bundler [depth <N>] [STRICT\|BIDIRECTIONAL]` | 1 | Group nets into hierarchy-aware HBundles using open BDB (BIDIRECTIONAL = direction-agnostic, as in run_bundler: a net + its reverse + cyclic groups bundle together, so one bundle can mix bidirectional pairs and one-way nets) |
| `dump_hbundles [expanded] [depth N]` | 1 | Print HBundle list (pre-expansion by default; `expanded` = post-`run_planner hier` view; `depth N` = filter by level) |
| `generate_topologies [center_mode] [double_detour]` | 2 | Generate candidates for all bundles (src/dst auto-derived from netlist) |
| `generate_topologies_for_bundle <hint> <src> <dst...> [center_mode] [double_detour]` | 2 | Generate candidates for a specific bundle; multiple dst → multicast trunk+branch shapes |
| `generate_hier_topologies [center_mode] [double_detour]` | 2 | Generate candidates for all HBundles (3-case: cell-local / cross-level / cross-block) |
| `generate_topologies_for_hbundle <bundle_id> [center_mode] [double_detour]` | 2 | Re-generate candidates for a single HBundle by ID; useful for debugging zero-candidate bundles |
| `set_planner_param <name> <value>` | 3 | Set a planner tuning knob; takes effect at the next `run_planner` (knobs may be changed between runs to re-plan). Known params: `kCong` (congestion weight), `kSpan` (span-length weight), `base_cost_non_top` (penalty for non-TOP layers), `kWL` (wirelength weight), `kBalance` (TOP-layer load balancing), `track_cap_slack` (extra signal tracks/band in `signal_tracks` mode) |
| `select_topology <hint> <id>` / `select_topologies <hint> <ids>` | 3 | Pin one/many bundles to a specific candidate topology (1-based; ranges like `1,5-9,11`) before planning |
| `run_planner <iterations> [signal_tracks]` | 3 | Layer assign + topology select. `signal_tracks` (opt-in) charges band capacity in discrete SIGNAL-track count (× bit pitch) instead of layout width, so a band short of tracks surfaces as planner overflow instead of a silent DNUTS open; needs `def_track_pattern`. See [Signal-Track Capacity plan](docs/internal/planner_signal_track_capacity.md) |
| `run_planner hier [<iterations>] [signal_tracks]` | 3 | Hier-aware planner: pins sidecar selections, expands cell-level bundles to per-instance wrappers, then runs congestion planner top-down. `signal_tracks` as above |
| `run_planner post_nuts [V [short long]] [H [short long]]` | 3 | Post-NUTS stub layer reassignment: short/long stubs on V or H layers are moved to cheaper layers |
| `set_track_pitch <pitch>` | 3 setup | Declare inter-bus track pitch before `run_planner` so its pitch-aware band reservations match the `run_nuts` that packs tracks; `run_nuts` with no arg reuses it |
| `run_nuts [pitch]` | 4 | Abstract track placement (defaults to the last `set_track_pitch`/`run_nuts` value; warns if it differs from the pitch `run_planner` reserved for) |
| `run_nuts_on_layer <layer-name>` | 4 | Re-solve one layer with NUTS without disturbing other layers |
| `dump_topologies [<hint>] [--problems] [--conn]` | — | Text dump of per-bundle candidate topologies (type, wirelength, segments, pass-through, `min_slide`, selected/pinned); `--problems` filters to bundles with duplicate/pinched/single/pass-through candidates and prints an aggregate summary; `--conn` adds a per-segment connectivity detail for the selected candidate (what each seg connects to — busterms + other segs, the busterms it passes through, its slide range, and net-pull preference). Read-only inspection. |
| `visualize_topologies [<hint>]` | — | Open topology explorer for the matching bundle (`-all [hints…]` for multiple) |
| `visualize` | — | Open interactive matplotlib window |
| `source <file>` | — | Execute another `.buda` script inline |
| `def_track_pattern <layer_id> <origin> <type> <w> <sp> ...` | 8 setup | Define repeating track pattern |
| `add_grid_override <layer_id> <x1> <y1> <x2> <y2> <origin> ...` | 8 setup | Region-scoped pattern override |
| `run_detailed_nuts [lo_hi\|hi_lo]` | 9 | Snap bit-wires to concrete tracks |
| `ripup_reroute [max_iter]` | 3↔4/9 | Feedback-driven rip-up & re-route: greedy hill-climb that reads the **actual** NUTS overlaps (run after `run_nuts`) or DetailedNUTS opens (run after `run_detailed_nuts`), re-pins a contending bundle to an alternate topology, re-runs planner→NUTS(→DNUTS), and keeps moves that reduce the metric — clears congestion the planner's band model under-predicts (`overflow=0`). No-op when already clean. Works in both flat flow and **hier flow** (after `run_planner hier`, self.bundles is the expanded per-instance list, so a re-route re-pins one instance and re-plans the expanded wrappers in place) |
| `check_connectivity [all]` | verify | Run connectivity verification at the current stage (topo / NUTS / detailed-NUTS). `all` checks every candidate topology; auto-run before planning |
| `report_overhead` | — | Compare `def_layer` overhead% against the actual track-pattern overhead |
| `source <file>` / `exit [code]` | — | Execute another `.buda` script inline / stop with an exit code |

Unknown commands are a hard error (the CLI fails fast rather than silently ignoring typos).

## Tests

Run from the repository root — `pytest.ini` configures `testpaths=test/tests`, `pythonpath=build src`, and excludes `slow`-marked tests by default:

```bash
pytest                        # fast tier (excludes 'mid' and 'slow' markers)
pytest -m "not slow"          # fast + mid tiers
pytest -o addopts="" -m slow  # only the slow tier
pytest test/tests/test_nuts.py -v   # single file
./bb test                     # build + fast tier; ./bb mid adds integration, ./bb slow adds all
```

Feature files in `test/tests/features/` (pytest-bdd). Most stages have a corresponding `.feature` and `test_*.py` file, including BDB (`test_bdb.py`, `bdb_*.feature`), hier flow (`test_hier_*`), floorplanner (`test_floorplanner_*`), connectivity (`test_check_connectivity_hbundle.py`, `test_check_layer_dir.py`), routing grid, and detailed NUTS.

---

## Architecture

### Pipeline Overview

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

---

## Stage-by-Stage Detail

### Stage 1 — Bundler (`bundler.h/cpp`)

**Responsibility:** Group nets that share driver/receiver topology into `Bundle` / `HBundle` objects.

**Key types:**
- `Net` — name, driver pin (`instance.pin`), list of receiver pins
- `HBundle` — id, list of net names, grouping reason; in the hier flow also carries `level` / `cell_context` / `instances` (the `Bundle` type was renamed `HBundle` and given hierarchy fields)
- `Netlist` — flat container of nets; populated by `add_net` CLI commands
- `Bundler` — flat grouping with a configurable `Strategy`
- `HierarchicalBundler` — hier grouping driven by BDB busterms/pins (`run_hier_bundler`)

**Algorithm (flat):** For each net, generate a string signature from its driver and/or receiver instance names. Nets with the same signature are grouped into one bundle.
- `STRICT` — signature = driver instance + sorted receiver instances; exact match required
- `CONVERGENT` — signature = sorted receiver instances only; shared destination is enough

**Hier:** each net is bundled once at its most specific endpoints (level = common-ancestor depth); cell-level bundles become reusable templates instantiated per occurrence. See [Hier Bundler](docs/HIER_BUNDLER.md) and [Cross-Level Bundling](docs/cross_level_bundling.md).

**Output fed to stage 2:** `vector<BundleWrapper>` (wrapping `Bundle`/`HBundle`)

---

### Stage 2 — Topology Generator (`topology.h/cpp`)

**Responsibility:** For each bundle's source→destination block pair, enumerate candidate routing paths as sequences of axis-aligned segments.

**Key types:**
- `Point` — integer (x, y)
- `Rect` — integer bounding box with `.center()`
- `Segment` — start point, end point, `layer_hint` (integer)
- `Topology` — type string (`"L_HV"`, `"Z_trunk_x"`, `"U_top"`, …), list of segments, estimated wirelength, trunk location
- `Floorplan` — block registry; manages keepout zones; provides `get_hanan_grid()` (sorted unique x and y coordinates of all block edges and keepouts)
- `TopologyGenerator` — generates L, Z, U candidates between two named blocks

**Topology shapes:**
- **L-shape** (2 segments): horizontal then vertical, or vertical then horizontal. The bend point is at one block's center projected onto the other's axis.
- **Z-shape** (3 segments): adds an intermediate trunk segment at a Hanan grid line between the two blocks. Multiple Z candidates are generated — one per intermediate Hanan grid coordinate.
- **U-shape** (3 segments): routes outside the bounding box of the two blocks, used when a direct L or Z path would traverse an obstacle. Trunk is placed beyond the extreme Hanan grid lines.

**MST shapes & feedthrough completion:** Standalone MST candidates (`MST_HV`/`MST_VH`, N≥4 blocks, `add_mst_candidates`) connect each edge's two blocks at their nearest faces. A block with MST degree ≥2 is thus touched by two edge segments at *different* points that, without correction, are joined only *through the block* — a silent feedthrough relay (which understates wirelength and isn't a real wire). `complete_relay_junctions` (`topology.cpp`) post-processes each MST topology: at every relay block it wires the incident segments so they are physically connected (each join is *perpendicular*, the only kind `ConnTopology` infers), routed **over-the-cell (OTC)** when the block is not a feedthru — a benign global wire over the block footprint on the trunk/over-cell layers, *not* a feedthru. Two shapes:
- **Orthogonal stubs (one H, one V) → simple extension.** A relay touched by exactly two stubs of opposite orientation is wired by *extending both stubs* over the cell to meet at the corner (the V stub's column, the H stub's row) inside the footprint. No connector segment is added, and — crucially — neither stub then ends on the block's face, so the block carries **no busterm tap**: it is covered by the crossing wires (`seg_spans_rect` pass-through), and the FEEDTHRU check (which gathers face-endpoint stubs) does not fire. (This is what removes the redundant L at bundle 3 / blk_19.)
- **Parallel stubs (both H / both V, at different rows/columns) → single JOG.** The two stubs are *extended over the cell to a common column (row)* and joined by ONE perpendicular jog (`seg1`/`seg5`/`seg3` for bundle 3 / blk_09). Like the orthogonal case the block then carries **no busterm tap** — it is covered by the crossing wires — and because both stubs now span the block, `tighten_passthrough_ranges` bounds the jog's slide to the **cell extent**, so NUTS keeps the two stubs flexible: if their perpendicular slides overlap, the jog shrinks to zero and they merge into one straight wire through the block.

A relay that is *not* one of these clean 2-stub cases (≥3 stubs, or a degenerate same-row/column pair) falls back to the general chaining, which adds connectors and keeps a single busterm tap; for those, `ConnTopology::pin_relay_tap_connectors` bounds the perpendicular connector at the tap's face endpoint to the **cell footprint** `[face_lo, face_hi]` (an OTC slide window) so the tap's along-reach stays over the cell — a **real** window, explicitly **not** a degenerate zero-slide pin (zero-slide segments are rejected by `filter_pinched`). A final **de-overlap** pass drops a connector only when it is *collinear-contained* within another wire (genuinely redundant), one at a time and only if SEG-connectivity is preserved; it is deliberately NOT generalized to drop any "globally redundant" connector — the MST edges already span a tree, so every connector is globally redundant via the long tree path, and dropping a non-collinear one would re-open the feedthru relay it exists to prevent. This makes the topology self-connected and the wirelength honest. A *feedthru* — a block that connects ≥2 of a bundle's stubs via its own lower-level routing — is now an **opt-in option** (`set_feedthru`): a `TRUNK_H`/`TRUNK_V` spine is split at the faces of any bundle busterm it passes through that has opted in, recorded in `Topology::feedthru_blocks`, and accepted by `check_topo`. **Trunk+MST hybrids (`add_trunk_mst_candidates`) are completed too**: each MST edge *replaces* a child branch block's trunk stub, yielding a cycle-free trunk-rooted tree that `complete_relay_junctions` wires up (single-rect blocks with a stub-owning root); a hybrid that cannot be cleanly completed is dropped rather than emitted, and only the legacy multi-rect / rootless fallback leaves relays flagged as `FEEDTHRU_RELAY`.

**Layer hints:** L-shape horizontal segment gets hint=3 (M3), vertical gets hint=4 (M4). All candidates use the same convention; the bundle planner may override.

**Corner margins:** At Busterm construction time, each block's bounding box is inset by its `BlockCornerMargin{dx, dy}` via `Rect::shrink(dx, dy)`. All shape functions (L/Z/U/UU/trunk) operate on the shrunken bbox directly — no per-function margin threading. The Hanan grid is built from the shrunken bboxes, so stub and trunk positions automatically land within the margin zone. `dy` applies to vertical faces (left/right, constrains Y); `dx` to horizontal faces (top/bottom, constrains X). Guard: if `2*margin >= face_extent`, the shrink is skipped for that axis.

**TEG mode (`teg_mode thru|over`):** Multi-rect blocks carry a `TegMode` flag set via `add_block … teg_mode over|thru`.
- **`thru` (default):** Each trunk connects to the nearest rect only. A split connection (trunk in the gap between rects) is left externally disconnected — the block's internal routing joins the sides. No bridge generated.
- **`over` — disjoint rects (pure TEG):** When the trunk falls in the gap between two rects, both rects get stubs (one to each) and an explicit **bridge segment** is placed along the outer face of the union bounding box (top for H-trunk gap, right for V-trunk gap). Stored in `Topology::bridge_segments[block_name]` (not in `segments`).
- **`over` — rectilinear rects (L-/C-shape):** When the trunk is inside some but not all rects (partial span), a bridge is emitted at the union bbox outer face. `rects_are_rectilinear()` distinguishes these from pure TEG (requires strict x- AND y-overlap between any two rects).
- Bridge is **suppressed** when the trunk lands directly inside a rect or the rects are adjacent (touching edges, no gap).
- The Hanan grid uses **each individual rect's edges** (not just the union bbox), so gap boundaries produce grid lines that trunks snap to naturally.

**Output fed to stage 3:** `vector<Topology>` per bundle, stored in `BundleWrapper::candidates`.

---

### Stage 3 — Bundle Planner / Congestion Planner (`congestion_planner.h/cpp`)

**Responsibility:** Select one topology per bundle and assign layers to its segments, minimizing congestion across all bundles simultaneously.

**Key types:**
- `GlobalCut` — a Hanan grid line segment subdivided into bands, tracking `band_cap` and `band_usage`.
- `BundleWrapper` — wraps a `Bundle` with its topology candidates, selected index, and bus `width`.
- `BundleAssignment` — selected topology index and per-segment layer assignments for a bundle.
- `CongestionPlanner` — congestion-aware global router.

**Algorithm:** Processes widest buses first (greedy heuristic). For each bundle candidate, it evaluates:
1. Congestion cost across cuts (band usage/capacity; band capacity is clamped to the segment's slide-window overlap with the band). Effective bus width per layer = `bits × bit_pitch` when the layer has a track pattern (`LayerStack::eff_bus_width`), else `width × dilution`.
2. Span-mismatch cost (penalizing segment length outside `[span_min, span_max]`).
3. Span-scaled penalty for non-`TOP` layers: `base_cost_non_top · min(1, seg_span/base_span_ref)` — short stubs offload to lower layers cheaply, long trunks stay on TOP.
It selects the candidate topology and layer assignments that minimize total cost, updates band usage, and returns the assignments.

**Hier mode (`run_planner hier`)**: each net is bundled exactly once at its most specific endpoints (level = common-ancestor depth — ancestor-level duplicate projections are not emitted); cell-level HBundle templates are expanded per instance (replicas skipped, each instance wrapper carrying its own donor nets) and planned top-down (`priority = -(level·10000 + n_candidates)`); each cell-local wrapper parks its effective width as a virtual **demand reservation** on TOP-layer bands inside its instance bbox (released at its own turn) so earlier globals leave room; a per-level ladder-stage summary is printed when levels differ.

**Overflow is a hard constraint** (an overflowing band cannot physically host the bus — NUTS would emit a real overlap), enforced by an escalation ladder per bundle:
1. `STRICT` — only candidates that are slide-feasible **and** overflow-free compete on soft costs (congestion/span/wirelength).
2. **Rip-up & replan** — if no candidate is overflow-free, rip up earlier-committed bundles one at a time — ranked by their demand on the failing bundle's contended bands (actual blocker first, zero-overlap victims skipped) — and replan the pair; accepted only if both end up overflow-free.
3. `ALLOW_OVERFLOW` — overflow truly unavoidable: commit the least-cost candidate with a `WARNING`.
4. `BEST_EFFORT` — no candidate even fits its slide windows (e.g. stale sidecar pins): commit anyway with a `WARNING` rather than dropping the bundle.

**After this stage**, each `BundleWrapper::candidates[selected_topology_index]` contains segments where:
- `layer_hint` is the assigned metal layer
- `start`/`end` coordinates define a soft routing-direction span
- The perpendicular coordinate implicitly defines the Hanan grid cell (hard interval for stage 4)

**Output fed to stage 4:** mutated `vector<BundleWrapper>` with `selected_topology_index` and segment layers set.

---

### Stage 4 — Abstract NUTS (`nuts.h/cpp`)

**Responsibility:** Solve the 1.5-D rectangle packing problem (Ekici, Basaran & Keskinocak 2009) — assign a concrete perpendicular `track_position` (real coordinate) to every bus segment, per layer, with no physical overlaps.

**Key types:**
- `TrackSegment` — bundle_id, seg_idx, layer, span_lo/hi (routing direction), interval_lo/hi (hard perpendicular constraint from Hanan cell), width, track_position (output), placed flag
- `NUTSResult` — flat list of `TrackSegment`s + `num_violations` (placed outside interval) + `num_overlaps` (physical collisions after placement)

**Algorithm (per layer):**
1. Extract segments from selected topologies; derive interval constraints from the Hanan grid cell containing each segment's nominal perpendicular coordinate.
2. Build a sweep-line event queue: one START and one END event per segment, sorted by `span_lo`.
3. Sweep: on START, collect occupied intervals from already-placed active segments (same-bundle segments never conflict — per-bit they are the same nets and may share tracks); place via `preferred_fit` at the alignment sibling's position (a placed same-bundle segment off the same perpendicular connector, if it fits), else the planner's charged-band centre (`BundleWrapper::seg_perp`, for segments free of busterm/net_pull face semantics), else the pull/centre preference. On END, remove from active set.
4. On placement failure, repack the contended window: all placed interval-overlapping segments are re-placed earliest-deadline-first (`interval_hi` ascending) with `first_fit`; commits only on full success, else falls back to interval centre (overlap recorded if conflicting).
5. After all layers solve and span adjustments follow connected segments' placed positions, a bounded `repair_overlaps` pass re-places victims of any overlap the adjustments materialized (state restored unless the overlap count strictly drops).

**Power-grid interaction:** When a layer has a track pattern (`def_track_pattern`), the abstract bus footprint uses the measured per-bit channel cost: `bits × unit_pitch / n_signal_slots` (`LayerStack::eff_bus_width`), so abstract widths match what detailed NUTS can actually place. Without a pattern, `width` is inflated by the layer's `dilution_factor` (= `unit_pitch / signal_width_sum`) as an approximation.

**Parallelism:** `solve_layer()` is called independently per layer — the per-layer maps have no cross-layer dependencies.

**Output fed to stage 9:** `NUTSResult` with one `TrackSegment` per bus segment.

---

### Stage 5 — Layer Stack (`layering.h/cpp`)

**Responsibility:** Metadata registry for the metal layer stack. Consulted by stages 3, 4, 8, and 9.

**Key types:**
- `Layer` — id, name, `LayerDir` (HORIZONTAL / VERTICAL), `LayerType` (TOP / LOW)
- `LayerStack` — add/query layers; tracks which layer is the top horizontal and top vertical layer

---

### Stage 6 — CLI (`buda_cli.py`)

**Responsibility:** Parse `.buda` script files line-by-line and drive the C++ engine via the pybind11 `buda` module (which re-exposes `buda_db` types).

`BudaSession` holds all live objects: an optional `BDB`, the `Floorplan`, `Netlist`, `LayerStack`, `Bundler` / `HierarchicalBundler`, `bundles` list, `nuts_result`, `routing_grid`, and detailed-NUTS result. Each CLI command maps to one or more method calls on these objects. Unknown commands raise an error.

Adding a new command/stage means: (1) implement the C++ class; (2) expose it via the relevant binding file — `bind_db.cpp` (BDB layer, registered in `buda_db`), `bind_bundler.cpp`, `bind_routing.cpp`, `bind_nuts.cpp` (NUTS / DetailedNUTS / RoutingGrid / ConnTopology / verify), or `bind_optimizer.cpp` (floorplanner); (3) add an `elif cmd == "..."` branch in `BudaSession.do_command()`.

---

### Stage 7 — Visualizer (`buda_viz.py`)

**Responsibility:** Interactive matplotlib window. All drawable elements are registered by bundle_id so click-to-highlight works uniformly across all draw methods.

**Artist registry pattern:** Every `ax.plot()` or `ax.add_patch()` call that represents a routable object is passed to `_register(bundle_id, artist, alpha=..., lw=..., is_band=...)`. This stores the artist's resting style. `_set_highlight(bundle_id)` then dims all other bundles to α=0.1 and brightens the selected bundle to α=1.0 with 2.2× line width. Clicking the same bundle or the background resets.

**Draw methods:**
- `draw_blocks()` — floorplan rectangles (not registered; always full opacity)
- `draw_hanan_grid()` — faint dashed grid lines (not registered)
- `draw_buses()` — topology segments at nominal coordinates (no NUTS)
- `draw_nuts_tracks(nuts_result)` — segments at NUTS-assigned track positions; faint interval-constraint bands behind each segment (registered as `is_band=True`)
- `draw_detailed_tracks(detailed_result)` — individual bit-wire lines at concrete track positions (per-type visibility toggles); see [Detailed Viz](docs/detailed_viz.md)
- `draw_preroutes(...)` *(planned)* — VDD/GND/CLK/SHIELD bands; per-type visibility toggle

---

### Stage 8 — Routing Grid (`routing_grid.h/cpp`) — IMPLEMENTED

**Responsibility:** Define the physical track structure of each metal layer — which track slots are POWER, GROUND, CLOCK, SHIELD, or SIGNAL — and expose this to both abstract NUTS (for dilution) and detailed NUTS (for exact track enumeration).

**Key types:**

`TrackSlot`
- `type`: extensible enum `{ POWER, GROUND, CLOCK, SHIELD, SIGNAL, CUSTOM }`
- `label`: string (`"VDD"`, `"GND"`, `"CLK1"`, user-defined)
- `width`: double (track width in layout units)
- `space_after`: double (gap to the next slot)

`TrackPattern`
- `origin`: double — global anchor from chip origin (0,0); ensures all Hanan channels tile on the same phase
- `slots`: `vector<TrackSlot>` — one repeating unit (e.g. `[VDD(w=2), sig, sig, sig, sig, GND(w=2), sig, sig, sig, sig]`)
- `unit_pitch()` → sum of all `width + space_after` in one unit
- `signal_density()` → sum of signal widths / unit_pitch
- `dilution_factor()` → 1.0 / signal_density (fed to abstract NUTS stage 4)
- `tracks_in_range(lo, hi)` → `vector<(abs_position, TrackSlot)>` — enumerates all track centre positions within a perpendicular interval

`PatternOverride`
- `region`: `Rect` (Hanan-cell-aligned)
- `layer_id`: int
- `pattern`: `TrackPattern` with its own local `origin`
- Power/CLK segments are **broken** at region boundaries (DRC gap accepted)

`RoutingGrid` (per layer)
- `global_pattern`: `TrackPattern`; `overrides`: `vector<PatternOverride>`; plus a list of `keepouts`
- `init(pattern, is_horizontal)` — set the global pattern and routing direction
- `effective_pattern_at(x, y)` → first matching override, else global
- `signal_tracks_in(x, lo, hi)` → only SIGNAL-type slots within the interval

`RoutingGridStack`
- `define_layer(layer_id, pattern, is_horizontal)`
- `add_override(layer_id, x1, y1, x2, y2, pattern)` / `add_keepout(layer_id, x1, y1, x2, y2)`
- `get_layer_grid(layer_id)` → `RoutingGrid&`; `has_layer(layer_id)`

**Python hooks:** `RoutingGridStack`, `TrackPattern`, `TrackSlot` fully exposed to Python so users can build, inspect, and override patterns programmatically without recompiling.

**`.buda` commands:**
```
def_track_pattern <layer_id> <origin> [<type> <width> <space_after>] ...
add_grid_override  <layer_id> <x1> <y1> <x2> <y2> <origin> [<type> <w> <sp>] ...
```

---

### Stage 9 — Detailed NUTS (`detailed_nuts.h/cpp`) — IMPLEMENTED

**Responsibility:** Expand each abstract `BusSegment` (from stage 4) into N concrete `NetSegment`s, one per bit-wire, snapped to exact signal track positions from the `RoutingGridStack`. Pre-route blockages (POWER, GROUND, CLOCK, SHIELD) are hard constraints (they are simply not SIGNAL slots, so bits cannot land on them).

**Key types (as built — plain structs, not a `PlacedSegmentBase` hierarchy yet; see "target state" below):**

`BusSegment` — abstract bus geometry handed to stage 9 (one per selected topology segment)
- `bundle_id`, `seg_idx`, `layer`, `span_lo/hi`, `interval_lo/hi`, `bit_width`
- `bit_order`: string `"LO_HI"` / `"HI_LO"` (default LO_HI)
- `timing_critical`: bool — if true, all bits must land on contiguous signal tracks for uniform RC
- `connections`: `vector<BusSegmentConn>` — explicit connectivity for bit-wire span adjustment
- `abstract_pos`: stage-4 track position used to anchor bit ordering (NaN = fallback)
- `track_lo_bound`/`track_hi_bound`: corner-resolution clamp so a segment stays on the bounded side of a cross-trunk-layer split

`NetSegment` — one bit-wire; output of stage 9
- `bundle_id`, `seg_idx`, `bit_index` (0-based position within bus)
- `track_position` (track centre), `width` (from the `TrackSlot`), `layer`, `span_lo/hi`

`DetailedNUTSResult`
- `net_segments`: `vector<NetSegment>`
- `num_unplaced`: int

`DetailedNUTSEngine(stack).run(bus_segments)` drives the placement.

**Algorithm:**
1. For each `BusSegment`, call `signal_tracks_in(x, interval_lo, interval_hi)` on the effective `RoutingGrid` to get the available signal track list (power/clock/shield slots are excluded).
2. Take the first `bit_width` signal tracks (LO_HI) or last `bit_width` (HI_LO).
3. If `timing_critical`, verify the selected tracks are contiguous (no power/clock track between them); if not, search for the tightest contiguous window of `bit_width` signal tracks within the interval.
4. Emit one `NetSegment` per track with `track_position` = track centre, `width` = track width from `TrackSlot`.

**`.buda` command:**
```
run_detailed_nuts [lo_hi|hi_lo]
```

**Visualization hook:** `draw_detailed_tracks(detailed_result)` draws individual bit-wire lines at their concrete track positions, with per-type visibility toggles (`[VDD] [GND] [CLK] [SIGNAL]`) as matplotlib `Button` widgets.

---

### Connectivity model & verification (`conn_topology.h/cpp`, `verify.h/cpp`)

These cross-cutting modules sit beside stages 2–9 and guard correctness.

**`ConnTopology`** augments a raw `Topology` with explicit connectivity and slide ranges:
- Infers connections geometrically — busterm-face membership, shared endpoints, and T-junctions — producing a `ConnSeg` per segment with a `perp_slide` range (`perp_lo`/`perp_hi`) over which the segment can move while every connection stays valid.
- Computes `net_pull` (which way a segment "wants" to slide to shorten connected stubs) used as a NUTS placement preference.
- `trunk_mst(...)` builds a Kruskal MST (`compute_mst` over `manhattan_nearest` distances) connecting a trunk to any blocks not yet directly attached — drives large-fanout / multi-block topologies.

**`verify`** runs connectivity audits at three granularities, surfaced by the `check_connectivity` CLI command:
- `check_topo` — nominal positions from `ConnTopology` (SEG continuity, busterm-face validity, block coverage incl. pass-through blocks, and feedthrough-relay detection).
- `check_nuts` — same checks at NUTS-placed positions, plus **layer-direction** validity (H segment on an H layer, V on a V layer — an unbuildable wire otherwise).
- `check_dnuts` — per-bit checks on `NetSegment` positions after detailed NUTS, plus unplaced-bit detection.
Violations are typed (`SEG_OPEN`, `BUSTERM_OPEN`, `BUSTERM_FACE`, `UNPLACED`, `LAYER_DIR`, `FEEDTHRU_RELAY`). `check_connectivity all` audits every candidate topology. **`FEEDTHRU_RELAY`** (check_topo only) flags a single-rect block whose connected segments' wires do not actually touch — i.e. the block is silently used as a feedthrough relay. A *feedthru* (a block that connects ≥2 of a bundle's stubs via its own lower-level/intra-block routing) is an **opt-in option** (`set_feedthru`): when a bundle busterm the trunk passes through opts in, the spine is split at its faces (two BUSTERM landings) and `check_topo` skips the `FEEDTHRU_RELAY` for blocks listed in `topo.feedthru_blocks` — but an *undeclared* relay is still flagged, so every topology must otherwise be physically self-connected. A straight trunk *crossing* an unrelated block (not a bundle busterm) is one continuous wire (a pass-through, no BUSTERM conn) and is NOT a feedthru.

---

### Physical Design Database — BDB (`bdb.h/cpp`)

**Responsibility:** SQLite-backed central store for the hierarchy-aware (v3) flow. All other modules read physical-design data through BDB rather than ad-hoc structures. Lives in `buda_core` and is registered in pybind11 by the `buda_db` module.

**Row types** (returned to Python / other modules): `ComponentRow` (hierarchical instance: parent_id, depth, bbox, is_leaf, is_replicated), `NetRow`, `PinRow` (net↔component pin with dir + absolute position), `NetPropsRow` (hpwl, fanout, bus_name, bit_index, bundle_id), `BustermRow` (routing interface: hier_path, depth, bbox, resolution BLOCK/SPATIAL_CLUSTER/PORT, optional multi-rect JSON), `BundleRow`, `GrpRow` (group tree), `CellRow`, `CellPinRow` (cell-type port interface).

**Capabilities:**
- **Ingestion:** `import_def_lef`, `import_verilog` — self-contained parsers (no OpenDB / Cadence / Si2 dependency), detailed below.
- **Hierarchy construction:** `add_cell` / `add_cell_pin` define cell types; `add_inst_to_cell` defines a cell's internal structure; `add_inst` places an instance and eagerly expands all `cell_children` into component rows.
- **Net wiring:** `add_net_pins` derives instance pins from `inst/path.pin` endpoints and inserts interface pins at every ancestor between leaf and common-ancestor (hierarchy propagation). Direction variants: `_undirected` (UNKNOWN, positional fallback) and `_inout` (INOUT = secondary driver).
- **Mutations:** `move_comp`, `set_comp_bbox`, `resize_cell`, `flip_comp`, `rotate_comp` (90/180/270, keeping lower-left fixed).
- **Computed properties:** `compute_hpwl`, `compute_fanout`.
- **Busterms / bundles / groups:** `add_busterm`/`clear_busterms`; group tree mirrors the Python `GroupTree` API.
- **Queries:** `all_components`, `components_at_depth`, `pins_by_comp`, `nets_by_hpwl`, `comps_in_rect`, `common_nets`, etc. Hot read paths use cached prepared statements.

Busterms are derived from the hierarchy by `BustermGen` (`derive_busterms`, Phase A of the hier pipeline). The hierarchy-aware bundler (`HierarchicalBundler`) and topology/planner `hier` variants consume this data. See [BDB Reference](docs/BDB_REFERENCE.md) and [HBundle pipeline notes](docs/session_hbundle_pipeline.md).

#### Design ingestion & interchange

All importers are hand-written, line-by-line state machines in `bdb.cpp` (no external EDA library). The two together populate the same `component` / `cell` / `net` / `pin` tables and are designed to be run **in sequence** — placement from DEF, hierarchy from Verilog. All stored coordinates are **µm**.

**`import_def_lef(def, lef)` — physical placement (`bdb.cpp::import_def_lef`)**
- **LEF first** (`_parse_lef_sizes` + `_parse_lef_pins`): walks `MACRO … END` blocks. `SIZE w BY h` → cell footprint (fills the `cell` table); each `PIN … END` block contributes a port whose offset is the **centroid of its `RECT` shapes** and whose `DIRECTION` becomes the pin dir (missing → `UNKNOWN`). Pins with `USE POWER|GROUND|CLOCK` are skipped (they are pre-routes, not signal terminals). Everything else in the LEF is ignored.
- **DEF second:** a three-state machine (`IDLE → IN_COMPONENTS → IN_NETS`). Reads `UNITS DISTANCE MICRONS` (the integer→µm divisor), `DIEAREA ( 0 0 ) ( x y )` (sets `die_w/die_h`), each `COMPONENTS` entry `- inst cell + PLACED|FIXED ( x y ) orient` (depth-0 leaf component, bbox = DEF origin + LEF `SIZE`, default `0.5×0.5` if the cell is absent from the LEF), and each `NETS` entry `- net … ( inst pin ) …` (creates `net` + `net_props` rows and a `pin` row per connection, resolving absolute pin position and direction from the LEF). DEF name escaping (`\[`, `\]`) is stripped so names match Verilog-elaborated paths.
- **Clears** `pin`/`net_props`/`net`/`component`/`cell` first — a fresh load. Components have `depth=0` and no parent until `import_verilog` overlays the hierarchy.

**`import_verilog(v)` — logical hierarchy (`bdb.cpp::import_verilog`)**
- **Phase 1** scans every `module` declaration, recording definition order; the **top module is the last module not instantiated by any other** (no explicit top needed).
- **Phase 2** parses each module body: instance lines (`cell inst ( .port(net), … );`) and port directions (`input/output/inout`). A custom `parse_portmap` handles `\`-escaped names, bit-selects (`net[3:0]` → base name), constants/concatenations/`UNCONNECTED` (skipped), and nested parens; a Verilog keyword set filters out non-instance statements.
- **Elaboration** walks from the top module, expanding instances into hierarchical `component` rows (dotted `parent/child` paths, growing `depth`) and wiring `net`/`pin` rows from the port maps. Instance pins default to `UNKNOWN`, then are overridden per-pin from any matching `cell_pin` direction (`infer_pin_dirs_from_cell_pins`).
- **UPSERT, not replace:** when run after `import_def_lef`, it `INSERT … ON CONFLICT DO UPDATE`s `cell`/`parent_id`/`depth`/`is_leaf` but **preserves `x1..y2`** so DEF placement survives. Components only in the Verilog get `x1=y1=x2=y2=−1` (unplaced); components only in the DEF keep their placement with no parent/depth. This is the canonical "DEF + Verilog merge" flow.

**Planned interchange formats (not yet implemented — roadmap):**
- **GDSII import + export** — round-trip a GDSII layout against BDB.
  - *Export:* flatten/stream the placed-and-routed result (`component` bboxes + NUTS/detailed-NUTS `NetSegment` wires per layer) to a GDSII layout for sign-off/visualization in standard layout viewers. The layer→GDS datatype mapping would extend `LayerStack`.
  - *Import:* read GDSII structures back into BDB component/cell rows — `BOUNDARY`/`BOX` shapes become cell or blockage geometry, `SREF`/`AREF` placements rebuild the component hierarchy, layer→`(layer, datatype)` mapping is inverted. Net connectivity is **optional and file-dependent**: GDS has no standard netlist, but many flows annotate shapes with net names via `TEXT`/label records (on a pin/label layer) or a labeling convention. The importer should support both modes — (a) *connectivity present:* parse the labels to recover `net`/`pin` rows; (b) *geometry only:* import placement/shapes and pair with `import_verilog` for nets, as with DEF today. A flag/auto-detect selects the mode per file.
- **OpenAccess (Si2 OA) import/export** — round-trip designs through an OA design database (`oaDesign`, `oaBlock`, `oaInst`, `oaNet`) so BUDA can sit inside an OA-based flow. Gated on the proprietary OA C++ libraries, so it would live behind an optional CMake feature flag and a separate translation module (e.g. `oa_bridge.cpp`) rather than in `buda_core`. Until then, LEF/DEF + Verilog is the supported interchange path.

These planned formats are tracked here for design intent only — there is **no GDS/OA code in the tree today** (`grep -ri gds\|openaccess src/` returns nothing). When implementing, follow the existing pattern: a standalone parser/writer in its own translation unit, populating or reading the same BDB tables, with coordinates normalized to µm.

---

### Floorplanner (`floorplanner.h/cpp`, `placement_optimizer.h/cpp`, `tools/`)

**Responsibility:** A separate **interactive placement tool** (not part of the routing pipeline) for editing block positions in a BDB and handing off to the hier routing flow.

- **`FloorplannerEngine`** (C++) — die/grid, top-level and child blocks, raw move/resize, align (top/bottom/left/right), grid snapping, `validate()` (overlap / out-of-die / error issues), and `write_bdb(BDB&)` to persist placement. Cross-module `BDB&` passing works because both modules share `buda_core` (see Build).
- **`PlacementOptimizer`** (C++) — simulated annealing (SA) and genetic algorithm (GA) placement with per-block constraints (Fixed / Reshapeable / min W/H) and weighted cost (wirelength / area / overlap). Exposed via `bind_optimizer.cpp`.
- **GUI** (`tools/bdb_floorplanner.py` + `tools/floorplanner_commands.py`) — Tk/matplotlib editor: drag/resize, align/distribute, SA/GA optimize, live HPWL + flylines, validation, and **Run Flow** (writes BDB → generates a hier `.buda` script → runs `buda_cli.py` for immediate routing feedback).
- **Launchers:** `./fp [file.bdb]` opens the GUI; `./bfp tc1|tc2|<file.bdb>|<script.buda>` adds built-in demo scenarios (`tools/fp_demo.py`) and flow integration.

**Other `tools/`:** DEF/LEF net-clustering visualizers (`def_cluster.py`, `def_viz*.py`, `def_viz_shared.py`), `group_tree.py` (group hierarchy + JSON persistence), `viz_ipc.py` (Unix-socket selection sync between `buda_viz` and `def_viz`), `show_detailed_shorts.py` (report bit-level detailed-NUTS shorts), and `render.py` (headless: pin one bundle's candidate in a `.buda` flow, run planner→NUTS→DetailedNUTS, print `dump_topologies --conn`, and render a topology/NUTS/DNUTS triptych PNG — `tools/render.py <flow.buda> --bundle <id> --topo <id> [--zoom]`).

---

## Segment Type Hierarchy (target state)

```
PlacedSegmentBase          kind, layer, span, track_position, width, placed
├── BusSegment             bundle_id, seg_idx, bit_width, interval, bit_order, timing_critical
├── NetSegment             bundle_id, seg_idx, bit_index, net_name, track_index
└── PreRoutedSegment       label, track_index
```

This is the **intended unification**, not the current shape: as built, stage 4 emits `TrackSegment` (in `nuts.h`) and stage 9 uses standalone `BusSegment` / `NetSegment` structs (in `detailed_nuts.h`) — there is no shared `PlacedSegmentBase` base class and `PreRoutedSegment` is not yet a type (pre-routes are modelled implicitly as non-SIGNAL track slots). The raw geometry type `Segment` in `topology.h` (start/end points + layer_hint) is a **pre-placement** concept and remains separate.

---

## Source File Map

| Area | Files |
|---|---|
| Build / wrappers | `CMakeLists.txt`, `bb` (build), `buda` / `fp` / `bfp` (run), `pytest.ini` |
| DB layer (`buda_core` → `buda_db`) | `bdb.h/cpp`, `sqlite3.c/h`, `busterm.h/cpp`, `bundler.h/cpp`, `bundle_refiner.h/cpp`, `bind_db.cpp`, `bindings_db.cpp` |
| Routing pipeline (`buda`) | `topology.h/cpp`, `conn_topology.h/cpp`, `layering.h/cpp`, `congestion_planner.h/cpp`, `nuts.h/cpp`, `routing_grid.h/cpp`, `detailed_nuts.h/cpp`, `verify.h/cpp`, `floorplanner.h/cpp`, `placement_optimizer.h/cpp` |
| Bindings (`buda`) | `bindings.cpp`, `bind_bundler.cpp`, `bind_routing.cpp`, `bind_nuts.cpp`, `bind_optimizer.cpp` |
| Python | `src/buda_cli.py` (CLI), `src/buda_viz.py` (visualizer), `src/ui_state.py`, `tools/*.py` (floorplanner GUI + DEF/LEF viz) |
| Flows / tests | `flow/*.buda`, `test/tests/*.py`, `test/tests/features/*.feature` |

---

## Dependencies

- **pybind11** — C++/Python bindings
- **Python 3.13+**
- **matplotlib** + **tkinter** — visualization and floorplanner GUI
- **SQLite** — bundled as `src/sqlite3.c` (amalgamation; no system dependency)
- **pytest** + **pytest-bdd** — testing
- **CMake 3.15+**
