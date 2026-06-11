# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

**BUDA** (Bundled Unified Design Automation) is an **EDA interconnect planning system** for chip design. It bundles nets into buses, generates routing topologies, assigns routes to metal layers, and resolves physical track positions — from abstract bus planning down to individual bit-wire placement respecting power-grid and pre-route blockages.

The core engine is **C++20** exposed to Python via **pybind11**, with a Python CLI and matplotlib visualization layer on top.

## Useful Docs
- [User Guide](docs/USER_GUIDE.md) — Prerequisites and standard flow for novices.
- [BUDA Script Reference](docs/BUDA_SCRIPT_REFERENCE.md) — Detailed command documentation.
- [BDB Reference](docs/BDB_REFERENCE.md) — Physical design database: schema, `.buda` commands, Python API.
- [Congestion Planner](docs/congestion_planner.md) — Internal design of the bundle planner: cost model, hard overflow constraint, rip-up & replan.
- [Detailed NUTS](docs/detailed_nuts.md) — Internal design of bit-level track assignment.

## Build

Use the provided build wrapper script `bb` at the repository root to perform a clean build:

```bash
./bb
```

To build and run all tests:

```bash
./bb test
```

Manual build:

```bash
mkdir -p build && cd build
cmake .. && make -j4
cp build/buda.cpython-*.so src/
```

CMake builds a single shared library module (`buda`) with `-O3 -march=native -Wall -Wextra`.

## Run

```bash
cd src/
python3 buda_cli.py ../flow/comprehensive_demo.buda
```

`.buda` script command reference:

| Command | Stage | Description |
|---|---|---|
| `add_block <name> <x1> <y1> <x2> <y2> [corner_margin ...]` | setup | Place a single-rect floorplan block; optional per-block corner margin (absolute or `pct_h`/`pct_v`) |
| `add_block <name> rect <x1> <y1> <x2> <y2> [rect ...] [teg_mode thru\|over] [corner_margin ...]` | setup | Multi-rect block: topology generator picks the best-fit rect per trunk position; `teg_mode over` generates an explicit bridge segment over the block's notch when the trunk falls in a gap between rects |
| `add_keepout <x1> <y1> <x2> <y2> <layer_list>` | setup | Define a rectangular keep-out zone for specific routing layers |
| `corner_margin dx <n> [dy <n>]` | setup | Set global corner margin for all blocks with no per-block override. Only `dx`/`dy` (absolute); `pct_h`/`pct_v` not valid globally. Single-axis value mirrors to the other axis. |
| `add_net <name> <driver_pin> <receiver_pins_csv>` | setup | Add a net to the netlist |
| `add_bus <prefix>[<N>] <drv_pin> <rcv_pin_csv>` | setup | Expand a bus into N nets: `prefix[N]` → `prefix_0`…`prefix_{N-1}`; `prefix[lo:hi]` → explicit range |
| `def_layer <id> <name> <H\|V> [TOP\|LOW] <overhead%> [span_min N] [span_max N] [kSpan K]` | setup | Register a metal layer; `TOP` marks it for trunk preference; optional span limits and per-layer congestion weight override |
| `run_bundler <STRICT\|CONVERGENT>` | 1 | Group nets into buses |
| `run_hier_bundler [depth <N>]` | 1 | Group nets into hierarchy-aware HBundles using open BDB |
| `dump_hbundles [expanded] [depth N]` | 1 | Print HBundle list (pre-expansion by default; `expanded` = post-`run_planner hier` view; `depth N` = filter by level) |
| `generate_topologies [center_mode] [double_detour]` | 2 | Generate candidates for all bundles (src/dst auto-derived from netlist) |
| `generate_topologies_for_bundle <hint> <src> <dst...> [center_mode] [double_detour]` | 2 | Generate candidates for a specific bundle; multiple dst → multicast trunk+branch shapes |
| `generate_hier_topologies [center_mode] [double_detour]` | 2 | Generate candidates for all HBundles (3-case: cell-local / cross-level / cross-block) |
| `generate_topologies_for_hbundle <bundle_id> [center_mode] [double_detour]` | 2 | Re-generate candidates for a single HBundle by ID; useful for debugging zero-candidate bundles |
| `set_planner_param <name> <value>` | 3 | Set a planner tuning knob; takes effect at the next `run_planner` (knobs may be changed between runs to re-plan). Known params: `kCong` (congestion weight), `kSpan` (span-length weight), `base_cost_non_top` (penalty for non-TOP layers), `kWL` (wirelength weight) |
| `run_planner <iterations>` | 3 | Layer assign + topology select |
| `run_planner hier [<iterations>]` | 3 | Hier-aware planner: pins sidecar selections, expands cell-level bundles to per-instance wrappers, then runs congestion planner top-down |
| `run_planner post_nuts [V [short long]] [H [short long]]` | 3 | Post-NUTS stub layer reassignment: short/long stubs on V or H layers are moved to cheaper layers |
| `run_nuts [pitch]` | 4 | Abstract track placement |
| `run_nuts_on_layer <layer-name>` | 4 | Re-solve one layer with NUTS without disturbing other layers |
| `visualize_topologies [<hint>]` | — | Open topology explorer for the matching bundle (`-all [hints…]` for multiple) |
| `visualize` | — | Open interactive matplotlib window |
| `source <file>` | — | Execute another `.buda` script inline |
| `def_track_pattern <layer_id> <origin> <type> <w> <sp> ...` | 8 setup | Define repeating track pattern |
| `add_grid_override <layer_id> <x1> <y1> <x2> <y2> <origin> ...` | 8 setup | Region-scoped pattern override |
| `run_detailed_nuts [lo_hi\|hi_lo]` | 9 | Snap bit-wires to concrete tracks |

## Tests

```bash
cd test/tests/
pytest                        # all tests
pytest test_nuts.py -v        # single file
```

Feature files in `test/tests/features/`. Each stage has a corresponding `.feature` and `test_*.py` file.

---

## Architecture

### Pipeline Overview

```
Netlist (.buda script)
    │
    ▼
[1] Bundler          nets → Bundles
    │
    ▼
[2] TopologyGen      Bundles → candidate L/Z/U topologies (Hanan grid)
    │
    ▼
[3] Bundle Planner   topology selection + layer assignment (congestion-aware)
    │                each segment now has: layer, routing-dir span, perp interval
    ▼
[4] Abstract NUTS    1.5-D rectangle packing → BusSegment track_position (real coords)
    │                parallelises per layer; power-grid dilution applied approximately
    ▼
[5] Layer Stack      (consulted by stages 3–9 for layer direction/type metadata)

[6] CLI              orchestrates stages 1–9 via .buda scripts
[7] Visualizer       interactive matplotlib; click-to-highlight; pre-route toggles

    ── planned ──────────────────────────────────────────────────────────────
[8] Routing Grid     defines per-layer track patterns (power/signal/clock layout)
    │                global pattern per layer + optional Hanan-region overrides
    ▼
[9] Detailed NUTS    snaps each BusSegment → N NetSegments on concrete signal tracks
                     respects pre-route blockages; bit ordering; timing-critical mode
```

---

## Stage-by-Stage Detail

### Stage 1 — Bundler (`bundler.h/cpp`)

**Responsibility:** Group nets that share driver/receiver topology into `Bundle` objects.

**Key types:**
- `Net` — name, driver pin (`instance.pin`), list of receiver pins
- `Bundle` — id, list of net names, grouping reason string
- `Netlist` — flat container of nets; populated by `add_net` CLI commands
- `Bundler` — runs grouping with a configurable `Strategy`

**Algorithm:** For each net, generate a string signature from its driver and/or receiver instance names. Nets with the same signature are grouped into one bundle.
- `STRICT` — signature = driver instance + sorted receiver instances; exact match required
- `CONVERGENT` — signature = sorted receiver instances only; shared destination is enough

**Output fed to stage 2:** `vector<Bundle>`

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

**Responsibility:** Parse `.buda` script files line-by-line and drive the C++ engine via the pybind11 `interconnect` module.

`BudaSession` holds all live objects: `Floorplan`, `Netlist`, `LayerStack`, `Bundler`, `bundles` list, `nuts_result`, and (planned) `routing_grid`. Each CLI command maps to one or more method calls on these objects.

Adding a new stage means: (1) implement the C++ class, (2) expose it in `bindings.cpp`, (3) add a `elif cmd == "..."` branch in `BudaSession.do_command()`.

---

### Stage 7 — Visualizer (`buda_viz.py`)

**Responsibility:** Interactive matplotlib window. All drawable elements are registered by bundle_id so click-to-highlight works uniformly across all draw methods.

**Artist registry pattern:** Every `ax.plot()` or `ax.add_patch()` call that represents a routable object is passed to `_register(bundle_id, artist, alpha=..., lw=..., is_band=...)`. This stores the artist's resting style. `_set_highlight(bundle_id)` then dims all other bundles to α=0.1 and brightens the selected bundle to α=1.0 with 2.2× line width. Clicking the same bundle or the background resets.

**Draw methods:**
- `draw_blocks()` — floorplan rectangles (not registered; always full opacity)
- `draw_hanan_grid()` — faint dashed grid lines (not registered)
- `draw_buses()` — topology segments at nominal coordinates (no NUTS)
- `draw_nuts_tracks(nuts_result)` — segments at NUTS-assigned track positions; faint interval-constraint bands behind each segment (registered as `is_band=True`)
- `draw_preroutes(...)` *(planned)* — VDD/GND/CLK/SHIELD bands; per-type visibility toggle
- `draw_detailed_tracks(detailed_result)` *(planned)* — individual bit-wire lines at concrete track positions

---

### Stage 8 — Routing Grid (`routing_grid.h/cpp`) *(planned)*

**Responsibility:** Define the physical track structure of each metal layer — which track slots are POWER, GROUND, CLOCK, SHIELD, or SIGNAL — and expose this to both abstract NUTS (for dilution) and detailed NUTS (for exact track enumeration).

**Key types (designed, not yet implemented):**

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
- `global_pattern`: `TrackPattern`
- `overrides`: `vector<PatternOverride>`
- `effective_pattern_at(x, y)` → first matching override, else global
- `signal_tracks_in(x, lo, hi)` → only SIGNAL-type slots within the interval

`RoutingGridStack`
- `define_layer(layer_id, pattern)`
- `add_override(layer_id, region, pattern)`
- `get_layer_grid(layer_id)` → `RoutingGrid&`

**Python hooks:** `RoutingGridStack`, `TrackPattern`, `TrackSlot` fully exposed to Python so users can build, inspect, and override patterns programmatically without recompiling.

**`.buda` commands:**
```
def_track_pattern <layer_id> <origin> [<type> <width> <space_after>] ...
add_grid_override  <layer_id> <x1> <y1> <x2> <y2> <origin> [<type> <w> <sp>] ...
```

---

### Stage 9 — Detailed NUTS (`detailed_nuts.h/cpp`) *(planned)*

**Responsibility:** Expand each abstract `BusSegment` (from stage 4) into N concrete `NetSegment`s, one per bit-wire, snapped to exact signal track positions from the `RoutingGridStack`. Pre-route blockages (POWER, GROUND, CLOCK, SHIELD) are hard constraints.

**Key types (designed, not yet implemented):**

`PlacedSegmentBase` (abstract base)
- `layer`, `span_lo`, `span_hi`, `track_position`, `width`
- `kind`: `{ BUS, NET, POWER, GROUND, CLOCK, SHIELD, CUSTOM }`
- `placed`: bool

`BusSegment : PlacedSegmentBase` — replaces `TrackSegment` from stage 4
- `bundle_id`, `seg_idx`, `bit_width` (number of signal tracks needed)
- `interval_lo`, `interval_hi`
- `bit_order`: `{ LO_HI, HI_LO }` (default LO_HI)
- `timing_critical`: bool — if true, all bits must be on contiguous signal tracks with equal spacing for uniform RC

`NetSegment : PlacedSegmentBase` — one bit-wire; output of stage 9
- `bundle_id`, `seg_idx`, `bit_index` (0-based position within bus)
- `net_name`: string
- `track_index`: int (index into `signal_tracks_in()` result)

`PreRoutedSegment : PlacedSegmentBase` — fixed blockage; input to stage 9
- `label`: string (`"VDD"`, `"CLK1"`, …)
- `track_index`: int

`DetailedNUTSResult`
- `net_segments`: `vector<NetSegment>`
- `prerouted_segments`: `vector<PreRoutedSegment>`
- `num_unplaced`: int

**Algorithm:**
1. For each `BusSegment` in `NUTSResult`, call `signal_tracks_in(x, interval_lo, interval_hi)` on the effective `RoutingGrid` to get the available signal track list.
2. Take the first `bit_width` signal tracks (LO_HI) or last `bit_width` (HI_LO).
3. If `timing_critical`, verify all selected tracks are contiguous (no power/clock tracks between them); if not, search for the tightest contiguous window of `bit_width` signal tracks within the interval.
4. Emit one `NetSegment` per track with `track_position` = track centre, `width` = track width from `TrackSlot`.

**`.buda` command:**
```
run_detailed_nuts [lo_hi|hi_lo]
```

**Visualization hook:** `draw_detailed_tracks(detailed_result)` draws individual bit-wire lines at their concrete track positions, with per-type visibility toggles (`[VDD] [GND] [CLK] [SIGNAL]`) as matplotlib `Button` widgets.

---

## Segment Type Hierarchy (target state)

```
PlacedSegmentBase          kind, layer, span, track_position, width, placed
├── BusSegment             bundle_id, seg_idx, bit_width, interval, bit_order, timing_critical
├── NetSegment             bundle_id, seg_idx, bit_index, net_name, track_index
└── PreRoutedSegment       label, track_index
```

The raw geometry type `Segment` in `topology.h` (start/end points + layer_hint) is a **pre-placement** concept and remains separate from this hierarchy. `PlacedSegmentBase` and its subtypes are **post-placement** and live in `nuts.h` / `detailed_nuts.h`.

---

---

## Dependencies

- **pybind11** — C++/Python bindings
- **Python 3.13+**
- **matplotlib** — visualization
- **pytest** + **pytest-bdd** — testing
- **CMake 3.15+**
