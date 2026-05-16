# BUDA Script Reference

`.buda` scripts are executed line-by-line by `buda_cli.py`. Each line is one
command. Blank lines and lines beginning with `#` are ignored.

```
python3 src/buda_cli.py flow/my_design.buda
python3 src/buda_cli.py flow/my_design      # .buda extension inferred
```

---

## Pipeline overview

Commands run in the following order. Later stages depend on earlier ones.

| Stage | Command(s) | Purpose |
|------:|---|---|
| Setup | `def_layer` | Register metal layers |
| Setup | `add_block` | Place floorplan blocks (with optional per-block corner margin) |
| Setup | `corner_margin` | Set global corner margin for all blocks without a per-block override |
| Setup | `add_net`, `add_bus` | Declare nets / buses in the netlist |
| 1 | `run_bundler` | Group nets into buses |
| 2 | `generate_topologies` | Enumerate topology candidates for **all** bundles (src/dst auto-derived) |
| 2 | `generate_topologies_for_bundle` | Enumerate topology candidates for a **specific** bundle |
| 3 | `set_planner_param` | Tune planner cost coefficients (must be called before `run_planner`) |
| 3 | `run_planner` | Select topology + assign layers per segment |
| 4 | `run_nuts` | Abstract 1.5-D track placement |
| 4b | `run_nuts_on_layer` | Re-solve one layer after inspection |
| 4c | `run_planner post_nuts` | Reassign stub layers to resolve channel pin conflicts; single NUTS re-run |
| — | `visualize` | Open interactive NUTS result viewer |
| — | `visualize_topologies` | Open topology explorer |
| — | `source` | Include another `.buda` file |

---

## Setup commands

### `def_layer`

```
def_layer <id> <name> <dir> <type> <overhead%>
```

Register a metal routing layer.

| Argument | Type | Description |
|---|---|---|
| `id` | int | Unique numeric layer ID (used everywhere else to identify the layer) |
| `name` | str | Human-readable name, e.g. `M4` |
| `dir` | `H` or `V` | Routing direction: horizontal or vertical |
| `type` | `TOP` or `LOW` | `TOP` — preferred (highest) layer in this direction; `LOW` — secondary |
| `overhead%` | float | Fraction of each channel consumed by power/clock tracks, 0–99. Scales effective bus width by `100/(100 − overhead%)`. Use `0.0` for no dilation. |

**Notes:**
- At least one H layer and one V layer must be defined before `run_planner`.
- Multiple V layers (e.g. M3, M5, M7) are used by the global router in
  ascending ID order — the lowest-numbered V layer is filled first before
  spilling to the next.
- The layer name is used by `run_nuts_on_layer`.

**Example:**
```
def_layer 3 M3 V TOP 0.0
def_layer 4 M4 H TOP 0.0
def_layer 5 M5 V TOP 0.0
def_layer 6 M6 H TOP 20.0   # 20% of M6 channels used by power grid
def_layer 7 M7 V TOP 0.0
```

---

### `add_block`

```
add_block <name> <x1> <y1> <x2> <y2>
```

Place a rectangular block in the floorplan. Blocks define the Hanan grid
used by topology generation and the congestion model.

| Argument | Type | Description |
|---|---|---|
| `name` | str | Instance name, e.g. `u_cpu`. Referred to in `add_net` pin names and `generate_topologies_for_bundle`. |
| `x1 y1` | int | Lower-left corner (layout units) |
| `x2 y2` | int | Upper-right corner (layout units) |

**Example:**
```
add_block u_cpu   0    0  100  100
add_block u_mem 200    0  300  100
```

---

### `add_net`

```
add_net <name> <driver_pin> <receiver_pins>
```

Add a single net to the netlist.

| Argument | Type | Description |
|---|---|---|
| `name` | str | Net name. Used as a bundle hint key — the first net name in a bundle identifies the bundle in sidecar files and `generate_topologies_for_bundle`. |
| `driver_pin` | str | Driver pin in `instance.port` form, e.g. `u_cpu.tx` |
| `receiver_pins` | str | Comma-separated list of receiver pins (no spaces), e.g. `u_mem.rx` or `u_a.rx,u_b.rx` |

**Example:**
```
add_net data_0  u_cpu.dout  u_mem.din
add_net req     u_cpu.req   u_arb.req0,u_arb.req1
```

---

### `add_bus`

```
add_bus <prefix>[<N>]        <driver_pin> <receiver_pins>
add_bus <prefix>[<lo>:<hi>]  <driver_pin> <receiver_pins>
```

Convenience macro that expands to a sequence of `add_net` calls.

| Form | Expands to |
|---|---|
| `bu[4]` | `bu_0`, `bu_1`, `bu_2`, `bu_3` |
| `bu[2:5]` | `bu_2`, `bu_3`, `bu_4`, `bu_5` |

The driver and receiver pins are the same for every expanded net — this
describes a parallel bus where every bit shares the same source and
destination blocks.

**Example:**
```
add_bus data[8]  u_cpu.dout  u_mem.din       # expands to data_0 … data_7
add_bus addr[16] u_cpu.addr  u_mem.addr      # expands to addr_0 … addr_15
```

---

## Stage 1 — Bundler

### `run_bundler`

```
run_bundler strict
run_bundler convergent
```

Group all nets in the netlist into `Bundle` objects. Must be called after
all `add_net` / `add_bus` commands and before `generate_topologies_for_bundle`.

| Strategy | Grouping rule |
|---|---|
| `strict` | Driver instance **and** sorted receiver instances must match exactly. |
| `convergent` | Only sorted receiver instances must match; different drivers allowed. |

Bundle width is computed automatically as `1.5 × (number of nets)` layout
units. The bundler prints the number of bundles created.

**Sidecar:** topology selections saved from a previous `visualize_topologies`
session are loaded later by `run_planner` and applied on top of the planner's
choices (architect overrides).

**Example:**
```
run_bundler strict
```

---

## Stage 2 — Topology generator

### `generate_topologies_for_bundle`

```
generate_topologies_for_bundle <hint> <src> <dst> [flags]
generate_topologies_for_bundle <hint> <src> <dst1> <dst2> … [flags]
```

Generate routing topology candidates for the bundle whose first net name
starts with `<hint>`.

**Positional arguments:**

| Argument | Description |
|---|---|
| `hint` | Prefix of the first net name in the target bundle, e.g. `t0_b3`. |
| `src` | Source block name (must match an `add_block` name). |
| `dst` / `dst1 …` | One or more destination block names. Single destination → 2-pin L/Z/U candidates. Multiple destinations → multicast trunk-and-branch candidates. |

**Optional flags** (append anywhere after the block names):

| Flag | Effect |
|---|---|
| `center_mode` | Use block centres as connection points instead of the nearest busterm face. |
| `double_detour` | Also generate `UU_VHV` / `UU_HVH` high-detour candidates for very congested situations. |

**Candidate shapes generated (2-pin):**

| Shape | Segments | Description |
|---|---|---|
| `L_HV` / `L_VH` | 2 | Right-angle bend; horizontal-first or vertical-first. |
| `Z_trunk_x` / `Z_trunk_y` | 3 | Z-route with intermediate trunk at each Hanan grid line between the two blocks. |
| `U_top` / `U_bot` / `U_left` / `U_right` | 3 | U-route outside the block bounding box. |
| `UU_VHV` / `UU_HVH` | 4 | Double-detour variant (requires `double_detour` flag). |
| `I_H` / `I_V` | 1 | Straight horizontal or vertical route when blocks are axis-aligned. |

**Multicast shapes:** `TRUNK_H`, `TRUNK_V`, `TRUNK_Z` with per-receiver
stub branches.

**Notes:**
- Each call targets exactly one bundle. For N bundles, call N times.
- If no bundle matches the hint, a warning is printed and no error is raised.
- Candidates are stored on the bundle and consumed by `run_planner`.

**Example:**
```
generate_topologies_for_bundle t0_b3  u_t0  u_b3
generate_topologies_for_bundle t0_b3  u_t0  u_b3  center_mode
generate_topologies_for_bundle bus_rsp  u_resp  u_a  u_b  u_c   # multicast
```

---

### `generate_topologies`

```
generate_topologies [center_mode] [double_detour]
```

Generate routing topology candidates for **all** bundles produced by `run_bundler`.
Source and destination block names are derived automatically from the netlist
(registered at `add_net` / `add_bus` time — no manual `hint`, `src`, or `dst` needed).

**Optional flags** (same as `generate_topologies_for_bundle`):

| Flag | Effect |
|---|---|
| `center_mode` | Use block centres as connection points instead of the nearest busterm face. |
| `double_detour` | Also generate `UU_VHV` / `UU_HVH` high-detour candidates for very congested situations. |

**Notes:**
- Replaces N individual `generate_topologies_for_bundle` calls with one line.
- Must be called after `run_bundler` and before `run_planner`.
- Candidate shapes generated are identical to `generate_topologies_for_bundle` (L, Z, U, I, multicast TRUNK variants).
- Bundles with no registered endpoint info emit a warning and are skipped.

**Example:**
```
run_bundler strict
generate_topologies
run_planner 5
```

With flags:
```
run_bundler strict
generate_topologies  double_detour
run_planner 5
```

---

## Stage 3 — Global router / planner

### `run_planner`

```
run_planner [<iterations>]
```

Runs the global congestion-aware router. For each bundle:
1. Builds a Hanan-grid congestion map (one cut per channel per layer).
2. Scores every `(topology candidate, V-layer)` pair against current cut
   utilisation.
3. Selects the combination with the lowest peak overflow, preferring
   lower-numbered V layers when scores are equal (M3 fills before M5, M5
   before M7).
4. Updates `BundleWrapper.selected_topology_index` and
   `BundleWrapper.assigned_v_layer` for the winning choice.
5. Applies any architect-pinned selections from the `.json` sidecar file
   (see `visualize_topologies`), which override the planner's choices for
   pinned bundles.

Bundles are processed widest-first (fattest-first greedy).

| Argument | Type | Default | Description |
|---|---|---|---|
| `iterations` | int | 5 | Reserved for future iterative refinement; currently unused beyond the first pass. |

**Output:** Prints `[Planner] Bundle N (W units wide) → <type>  V-layer=MX  overflow=Y`
for each bundle, followed by a NUTS run summary.

**Side effects:**
- Creates a `GlobalRouter` object accessible to `visualize` for congestion
  overlay drawing.
- Reads and applies `<script>.json` sidecar if it exists.

**Example:**
```
run_planner 5
```

---

---

## Stage 4c — Post-NUTS stub layer reassignment

### `run_planner post_nuts`

```
run_planner post_nuts [V [<short_v> [<long_v>]]] [H [<short_h> [<long_h>]]]
```

Runs a second planner pass **after** `run_nuts` that resolves **channel pin
conflicts** — local stub-on-stub overlaps at block faces that the global
planner cannot predict before concrete track positions are known.

Both V and H directions can be reassigned in a **single invocation**; only one
NUTS re-run is performed regardless of how many directions are specified.

#### The channel pin conflict problem

The global planner (Stage 3) assigns every bundle to a single vertical and
horizontal layer and selects a topology, but it cannot see how many stubs from
adjacent blocks will compete for the same narrow perpendicular interval on the
same layer. When many blocks line up along a channel wall, their stubs pack
into the same Hanan-cell column on M5, exceeding its capacity and causing NUTS
violations.

#### Resolution strategy

For each requested direction, stubs are redistributed across all available
layers for that direction using stub span length as a proxy for routing
distance:

| Stub span (routing-direction extent) | Target layer |
|---|---|
| `< short_thresh` | Lowest-numbered layer (e.g. M3) — short stubs close to the block face stay on the nearest metal |
| `> long_thresh`  | Highest-numbered layer (e.g. M7) — long stubs crossing the full channel use the highest available metal |
| Between thresholds | Unchanged — stays on the planner-assigned layer (e.g. M5) |

After all reassignments, a single full NUTS re-run makes all layers consistent
with the new assignments.

#### Syntax

| Token | Description |
|---|---|
| `V` | Enable V-stub reassignment. Up to two numeric thresholds may follow. |
| `H` | Enable H-stub reassignment. Up to two numeric thresholds may follow. |
| `<short>` | Stubs shorter than this move to the lowest layer. |
| `<long>` | Stubs longer than this move to the highest layer. |

**Default thresholds** (used when a letter is given without explicit values):

| Direction | short | long |
|---|---|---|
| V | 80.0 | 200.0 |
| H | 150.0 | 400.0 |

**Bare `run_planner post_nuts`** (no direction letter) → V with defaults (80 / 200). Backward compatible with the previous two-argument form.

#### Notes

- Requires `run_nuts` to have been called first.
- Bundles are classified by the **longest** segment span within the bundle for
  each direction, so all stubs in a bundle move together to the same new layer.
- A single NUTS re-run is performed after all direction reassignments; any
  previous `run_nuts_on_layer` overrides are superseded.
- Thresholds are in layout units. Inspect the NUTS log or use `visualize` to
  estimate typical stub lengths for your floorplan.

#### Examples

```buda
# V only — backward-compatible forms
run_planner post_nuts               # V defaults (80 / 200)
run_planner post_nuts V             # same
run_planner post_nuts V 100 280     # custom V thresholds

# H only
run_planner post_nuts H             # H defaults (150 / 400)
run_planner post_nuts H 120 350     # custom H thresholds

# Both directions in one pass (single NUTS re-run)
run_planner post_nuts V 80 200 H 150 400
run_planner post_nuts V H           # both with defaults
```

#### Typical script pattern (congested channel)

```buda
run_planner 5
run_nuts 2.0
run_planner post_nuts V 100 280 H 150 400   # redistribute stubs to M3/M5/M7
visualize
```

---

## Stage 4 — Abstract NUTS

### `run_nuts`

```
run_nuts [<track_pitch>]
```

Runs the Non-Uniform Track Sharing (NUTS) 1.5-D rectangle packing solver.
Assigns a concrete perpendicular `track_position` to every bus segment on
every layer, guaranteeing no physical overlaps (within capacity).

The algorithm sweeps segments by span start, placing each new segment at the
lowest feasible position within its Hanan-grid-cell interval constraint using
a first-fit strategy. Each layer is solved independently and in parallel.

| Argument | Type | Default | Description |
|---|---|---|---|
| `track_pitch` | float | 1.0 | Minimum gap between the upper edge of one segment and the lower edge of the next, in layout units. |

**Output:** Prints segment count, interval violations, and track overlap counts
per layer. Writes a detailed overlap report to `<script>_nuts.log`.

**Notes:**
- Must be called after `run_planner` (or after `generate_topologies_for_bundle`
  if skipping the planner).
- The track pitch used here is remembered and reused by `run_nuts_on_layer`.
- An *interval violation* means a segment could not fit within its Hanan-cell
  interval; it is placed at the interval centre as a best-effort fallback and
  counted.
- A *track overlap* means two segments on the same layer have overlapping
  spans and overlapping perpendicular extents — a physical short. The overlap
  report details each collision.

**Example:**
```
run_nuts 2.0
```

---

### `run_nuts_on_layer`

```
run_nuts_on_layer <layer_name>
```

Re-solve NUTS for a single named layer, leaving all other layers untouched.
Useful for iterative refinement after inspecting the overlap log for a
specific layer.

| Argument | Type | Description |
|---|---|---|
| `layer_name` | str | Layer name as declared in `def_layer`, e.g. `M3` or `M5`. |

**Requires:** `run_nuts` must have been called first; `run_nuts_on_layer`
updates the existing `NUTSResult` in place.

**Output:** Prints per-layer violation and overlap counts for the re-solved
layer. Appends a timestamped section to the existing `<script>_nuts.log`.

**Example:**
```
run_nuts 2.0
run_nuts_on_layer M3     # re-solve only M3 after reviewing the log
run_nuts_on_layer M5     # then re-solve M5 if needed
```

---

## Visualisation commands

### `visualize`

```
visualize
```

Opens the interactive NUTS result viewer (matplotlib window). No arguments.

**What is shown:**
- Floorplan blocks (grey rectangles, always visible).
- Hanan grid (faint dashed lines).
- If `run_nuts` has been called: bus segments at their NUTS-assigned
  `track_position`s, coloured by layer, with faint interval-constraint bands.
- If `run_planner` has been called: congestion-map cut utilisation overlay.
- If `run_nuts` has *not* been called: topology segments at their nominal
  (pre-NUTS) coordinates.

**Interactive controls:**

| Action | Effect |
|---|---|
| Click a segment or terminal | Highlight that bundle; dim all others |
| Click the same bundle again, or click background | Clear highlight |
| Layer checkboxes (right panel) | Toggle per-layer visibility |
| ☑ All Layers button | Toggle all layers on/off |
| Bundle list (right panel) — left click | Highlight bundle |
| Bundle list — right click (on label) | Toggle bundle visibility |
| ☑ All Bundles button | Toggle all bundles on/off |
| Bundle list scroll ▲ / ▼ | Scroll bundle list |
| Solo button | Isolate highlighted bundle; hide all others |
| Next / Prev buttons | Walk through bundles sequentially |

**Sidecar:** topology selections saved from `visualize_topologies` are
preserved in `<script>.json` and loaded by the next `run_planner` invocation.

---

### `visualize_topologies`

```
visualize_topologies <hint>
visualize_topologies -all [<hint1> <hint2> …]
```

Opens the topology explorer for one or more bundles. Allows stepping through
all generated topology candidates and pinning a selection for the planner.

| Form | Behaviour |
|---|---|
| `visualize_topologies <hint>` | Open explorer for the first bundle whose first net name starts with `<hint>`. |
| `visualize_topologies -all` | Open explorers for every bundle (one window per bundle, opened sequentially). |
| `visualize_topologies -all <hint1> <hint2> …` | Open explorers for all bundles matching any of the given hints. |

**Explorer controls:**

| Action | Effect |
|---|---|
| `<` / `>` buttons | Step through topology candidates |
| `Select` button | Pin this topology; saves to `<script>.json` sidecar |
| `Deselect` button | Remove the pin for this bundle |

**Persistence:** Selected topologies are saved to `<script>.json` alongside
the `.buda` file. The next `run_planner` will load and honour these pins,
overriding the congestion-based choice for pinned bundles.

**Window title:** `<first_net_name> (Bundle N)` — identifies which bundle is
being explored.

**Example:**
```
visualize_topologies t0_b3          # explore one bundle
visualize_topologies -all           # explore every bundle
visualize_topologies -all t0_ t1_   # explore all bundles starting with t0_ or t1_
```

---

## Script control

### `source`

```
source <path>
```

Execute the contents of another `.buda` script file inline, as if its
commands had been typed at the current point. Comments and blank lines in
the included file are skipped.

The script path is resolved relative to the current working directory.
Only the outermost script's path is used for sidecar (`.json`) and log
(`.log`) file naming.

**Example:**
```
source ../common/base_layers.buda
source my_floorplan.buda
run_bundler strict
```

---

### Comments

```
# this is a comment
```

Lines beginning with `#` (after optional leading whitespace) are ignored.
Inline comments (after a command on the same line) are **not** supported —
the `#` must be the first non-whitespace character.

---

## Output files

| File | Created by | Contents |
|---|---|---|
| `<script>.json` | `visualize_topologies` → Select | Architect-pinned topology selections. Loaded by `run_planner`. |
| `<script>_nuts.log` | `run_nuts` | Per-overlap detail report: segment pairs, span/perp rectangles, area. Re-run sections are appended by `run_nuts_on_layer`. |

---

## Typical script skeleton

```buda
# ── Layer stack ────────────────────────────────────────────
def_layer 3 M3 V TOP 0.0
def_layer 4 M4 H TOP 0.0
def_layer 5 M5 V TOP 0.0
def_layer 6 M6 H TOP 0.0
def_layer 7 M7 V TOP 0.0

# ── Floorplan ───────────────────────────────────────────────
add_block u_a   0    0  100  100
add_block u_b 200    0  300  100
add_block u_c 200  200  300  300

# ── Netlist ─────────────────────────────────────────────────
add_net  sig0   u_a.tx  u_b.rx
add_bus  data[8] u_a.dout  u_b.din

# ── Stage 1: bundle ─────────────────────────────────────────
run_bundler strict

# ── Stage 2: topologies ─────────────────────────────────────
generate_topologies_for_bundle sig0  u_a  u_b
generate_topologies_for_bundle data  u_a  u_b

# ── Stage 3: global route ────────────────────────────────────
run_planner 5

# ── Stage 4: abstract track placement ────────────────────────
run_nuts 2.0

# ── Stage 4c (optional): redistribute stubs across V and/or H layers ──
# Use when many blocks line up along a channel and stubs overlap.
# run_planner post_nuts V 80 200           # V only
# run_planner post_nuts H 150 400          # H only
# run_planner post_nuts V 80 200 H 150 400 # both in one NUTS re-run

# ── Optional: re-solve a single congested layer ───────────────
# run_nuts_on_layer M3

# ── Visualise ────────────────────────────────────────────────
visualize
```
