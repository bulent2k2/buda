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
| Setup | `set_min_stub_length`, `_dir`, `_layer` | Set minimum stub length globally, per direction, or per layer |
| Setup | `add_net`, `add_bus` | Declare nets / buses in the netlist |
| Setup | `detour_channel` | Set outer-band width for U-shape / UU-shape detour trunks per compass direction |
| 1 | `run_bundler`, `run_hier_bundler` | Group nets into flat or hierarchy-aware buses |
| 1b | `dump_hbundles` | Print a summary of all HBundles (after `run_hier_bundler`) |
| 2 | `generate_topologies`, `generate_hier_topologies` | Enumerate topology candidates for flat or hierarchical bundles |
| 2 | `generate_topologies_for_bundle` | Enumerate topology candidates for a **specific** flat bundle |
| 2 | `generate_topologies_for_hbundle` | Re-run topology generation for a **specific** HBundle by its integer ID |
| 3 | `set_planner_param` | Tune planner cost coefficients (applied at the next `run_planner`) |
| 3 | `run_planner` | Select topology + assign layers per segment |
| 3b | `select_topology` | Manually pin a specific topology candidate for a bundle by its 1-based ID |
| 4 | `run_nuts` | Abstract 1.5-D track placement |
| 4b | `run_nuts_on_layer` | Re-solve one layer after inspection |
| 4c | `run_planner post_nuts` | Reassign stub layers to resolve channel pin conflicts; single NUTS re-run |
| 8 | `def_track_pattern` | Define the repeating POWER/SIGNAL/GROUND track pattern for a layer |
| 8 | `add_grid_override` | Override the track pattern for a specific floorplan region on a layer |
| 8 | `report_overhead` | Compare `def_layer` overhead% against the track pattern; print corrected `def_layer` commands for any mismatch |
| 9 | `run_detailed_nuts` | Snap each bus segment's bits to concrete signal-track positions |
| Verify | `check_connectivity` | Verify connectivity at topo, nuts, or dnuts stages and detect opens |
| — | `visualize` | Open interactive NUTS result viewer |
| — | `visualize_topologies` | Open topology explorer |
| — | `source` | Include another `.buda` file |
| BDB | `open_bdb`, `import_def_lef`, `import_verilog` | Open / populate the physical design database |
| BDB | `move_comp`, `resize_cell`, `add_comp`, `flip_comp`, `rotate_comp`, `add_cell`, `add_inst`, `add_inst_to_cell`, `add_cell_pin` | Mutate placement and cell/pin definition data in the database |
| BDB | `bdb_net_mode` | Toggle whether netlist is written to BDB database |
| BDB | `add_blocks_from_bdb` | Import floorplan block boundaries at a given hierarchy depth |
| BDB | `derive_busterms` | Extract busterms from hierarchy |

---

## Setup commands

### `def_layer`

```
def_layer <id> <name> <dir> [TOP|LOW] <overhead%> [span_min N] [span_max N] [kSpan K]
```

Register a metal routing layer.

| Argument | Type | Description |
|---|---|---|
| `id` | int | Unique numeric layer ID (used everywhere else to identify the layer) |
| `name` | str | Human-readable name, e.g. `M4` |
| `dir` | `H` or `V` | Routing direction: horizontal or vertical |
| `TOP` / `LOW` | keyword | Optional. `TOP` — preferred (highest) layer in this direction; `LOW` — secondary. Omitting defaults to `LOW`. |
| `overhead%` | float | Fraction of each channel consumed by power/clock tracks, 0–99. Scales effective bus width by `100/(100 − overhead%)`. Use `0.0` for no dilation. |
| `span_min N` | keyword+int | Optional. Segments shorter than `N` layout units on this layer incur a span-mismatch penalty (guides short stubs to lower layers). |
| `span_max N` | keyword+int | Optional. Segments longer than `N` layout units on this layer incur a span-mismatch penalty (guides long wires to higher layers). |
| `kSpan K` | keyword+float | Optional. Per-layer override for the span-mismatch cost coefficient (overrides the global `kSpan` from `set_planner_param`). |

**Notes:**
- At least one H layer and one V layer must be defined before `run_planner`.
- Multiple V layers (e.g. M3, M5, M7) are all considered by the global router; the planner assigns each segment to the layer that minimises its score.
- The layer name is used by `run_nuts_on_layer`.

**Example:**
```
def_layer 3 M3 V TOP 0.0  span_max 150              # short stubs only
def_layer 4 M4 H TOP 0.0
def_layer 5 M5 V TOP 0.0  span_min 100 span_max 400 # mid-range spans
def_layer 6 M6 H TOP 20.0                           # 20% power overhead
def_layer 7 M7 V TOP 0.0  span_min 300              # long trunks only
```

---

### `add_block`

```
add_block <name> <x1> <y1> <x2> <y2> [corner_margin dx <n> [dy <n>]]
add_block <name> <x1> <y1> <x2> <y2> [corner_margin pct_h <p> [pct_v <p>]]
add_block <name> rect <x1> <y1> <x2> <y2> [rect ...] [teg_mode thru|over] [corner_margin ...]
```

Place a block in the floorplan. Blocks define the Hanan grid used by topology
generation and the congestion model.

The first two forms place a single rectangular block. The third form places a
**multi-rect block**: each `rect` token introduces one candidate connection
rectangle. The topology generator picks whichever rect minimises stub length
for each trunk position. The Hanan grid includes **all individual rect edges**
(not just the union bounding box), so gap and notch boundaries produce Hanan
lines that trunks can snap to. The union bounding box of all rects is used as
the block's overall footprint and as the reference dimension for `pct_h`/`pct_v`
margin calculations.

| Argument | Type | Description |
|---|---|---|
| `name` | str | Instance name, e.g. `u_cpu`. Referred to in `add_net` pin names and `generate_topologies_for_bundle`. |
| `x1 y1` | int | Lower-left corner (layout units) |
| `x2 y2` | int | Upper-right corner (layout units) |
| `rect x1 y1 x2 y2` | keyword | Multi-rect form: one candidate connection rectangle. Repeat for each rect. |
| `teg_mode thru\|over` | keyword | Optional; multi-rect form only. Controls how topology generation handles trunks that fall in the gap between rects. Default: `thru`. See **TEG mode** below. |
| `corner_margin dx N` | keyword | Optional. Shrink the routing face by `N` units in X (top/bottom faces). If `dy` is omitted, the same value applies to Y as well. |
| `corner_margin dy N` | keyword | Optional. Shrink the routing face by `N` units in Y (left/right faces). |
| `corner_margin pct_h P` | keyword | Shrink X faces by `P`% of block width. If `pct_v` is omitted, same percentage applies to height. |
| `corner_margin pct_v P` | keyword | Shrink Y faces by `P`% of block height. |

A per-block `corner_margin` overrides the global `corner_margin` command for
that block. The margin is baked into the block's `Busterm.bbox` at construction;
topology generation and the Hanan grid use the shrunken bounding box directly.

#### TEG mode

A multi-rect block models one of two physical situations:

**Disjoint rects (pure TEG — Terminal Equivalence Group):** the rects are
spatially separated, e.g. a block with ports on both left and right sides
represented as two narrow rectangles. Each rect is an independent candidate
connection point; the block can be reached from either side on any given trunk.

**Overlapping rects (rectilinear block):** the rects share area (e.g. an
L-shaped block defined as a tall arm + a wide base). All rects form one
connected polygon. A trunk that only crosses part of the polygon must route
over the block's outer boundary to reach the far portion.

`teg_mode` controls what happens when a horizontal trunk falls in the vertical
gap between rects (or a vertical trunk falls in the horizontal gap):

| Mode | Behaviour |
|---|---|
| `thru` (default) | The topology connects only the **nearest** rect (lowest stub length). The block's internal routing is assumed to join any disconnected portions. No bridge segment is generated. |
| `over` — disjoint rects | When the trunk falls in the gap between two rects, **both** rects are connected with stubs (one up, one down) and an explicit **bridge segment** is generated along the outer face of the union bounding box. The bridge physically joins the two sides over the notch. |
| `over` — rectilinear rects | When the trunk is inside some rects but not all (partial span), a bridge segment is generated along the outer face of the union bounding box to annotate that an explicit over-the-block wire is needed. Stubs are only generated for rects that the trunk does not directly hit. |

The bridge segment is stored in the topology's `bridge_segments` map (keyed by
block name) rather than in the main segment list, so callers can distinguish
routing wires from bridge annotations.

Over-the-block mode **does not** generate a bridge when:
- The trunk lands inside a rect (direct connection; no gap crossing).
- The two rects are adjacent (touching edges, no gap).

**Examples:**
```
add_block u_cpu   0    0  100  100
add_block u_mem 200    0  300  100  corner_margin dx 5
add_block u_io  400    0  500  100  corner_margin pct_h 10 pct_v 15

# L-shaped block: tall arm + wide base (rectilinear; teg_mode over emits bridge)
add_block u_l  rect  0  0  100  400  rect  0  0  400  100  teg_mode over

# Block with disjoint left and right ports (pure TEG; thru = default)
add_block u_dp rect  200  0  300  100  rect  400  0  500  100  teg_mode thru

# Notched block where trunk may fall in gap — explicit over-the-block bridge
add_block u_notch  rect  200  0  280  100  rect  220  300  300  400  teg_mode over

# Same notched block, relying on internal routing to join the two sides (default)
add_block u_notch  rect  200  0  280  100  rect  220  300  300  400
```

---

### `add_keepout`

```
add_keepout <x1> <y1> <x2> <y2> <layer1> <layer2> ...
```

Define a keep-out zone where specific routing layers are prohibited. Both the
global planner and the detailed router respect these zones.

| Argument | Type | Description |
|---|---|---|
| `x1 y1` | int | Lower-left corner of the prohibited region (layout units) |
| `x2 y2` | int | Upper-right corner of the prohibited region (layout units) |
| `layerN` | int or str | Layer ID or name (e.g. `4` or `M4`) to block in this zone. |

**Effect:**
- **Planning**: `run_planner` subtracts the prohibited area from its congestion
  model, forcing it to choose topologies that detour around the zone.
- **Detailed Routing**: `run_detailed_nuts` automatically skips any signal
  tracks that pass through a keep-out zone for the segment's assigned layer.
- **Visualization**: Keep-out zones appear as red hatched rectangles.

**Example:**
```
# Block M4 routing in the middle of the chip
add_keepout 300 300 500 500 M4

# Block both M4 and M5 over a sensitive analog block
add_keepout 100 100 250 250 M4 M5
```

---

### `add_net`

```
add_net <name> <driver_pin> <receiver_pins>
add_net <name> <pin1> <pin2>[,<pin3>…] unknown
```

Add a single net to the netlist.

| Argument | Type | Description |
|---|---|---|
| `name` | str | Net name. Used as a bundle hint key — the first net name in a bundle identifies the bundle in sidecar files and `generate_topologies_for_bundle`. |
| `driver_pin` | str | Driver pin in `instance.port` form, e.g. `u_cpu.tx`. When `unknown` is appended this is the first (positional) pin. |
| `receiver_pins` | str | Comma-separated list of receiver pins (no spaces), e.g. `u_mem.rx` or `u_a.rx,u_b.rx`. |
| `unknown` | keyword | Optional. Marks the net as having no known pin direction. All listed pins are stored in BDB with `dir="UNKNOWN"`. The flat bundler still uses positional order (first = driver, rest = receivers); `run_hier_bundler` applies the same positional fallback from BDB. |

**Directed form** — use when you know which end drives:
```
add_net data_0  u_cpu.dout  u_mem.din
add_net req     u_cpu.req   u_arb.req0,u_arb.req1
```

**Undirected form** — use for clock, reset, or any bidirectional signal where driver direction is not known at script time:
```
add_net clk  u_clkbuf.clk  u_cpu.clk,u_mem.clk  unknown
```

When `bdb_net_mode` is **off** the `unknown` keyword has no effect on the flat
bundler — direction is always inferred positionally. When `bdb_net_mode` is
**on** the keyword causes `add_net_pins_undirected` to be used instead of
`add_net_pins`, storing `"UNKNOWN"` in the BDB pin table so `run_hier_bundler`
can handle the net with its positional-fallback logic.

---

### `add_bus`

```
add_bus <prefix>[<N>]        <driver_pin> <receiver_pins>
add_bus <prefix>[<lo>:<hi>]  <driver_pin> <receiver_pins>
add_bus <prefix>[<N>]        <pin1> <pin2> unknown
```

Convenience macro that expands to a sequence of `add_net` calls.

| Form | Expands to |
|---|---|
| `bu[4]` | `bu_0`, `bu_1`, `bu_2`, `bu_3` |
| `bu[2:5]` | `bu_2`, `bu_3`, `bu_4`, `bu_5` |

The driver and receiver pins are the same for every expanded net — this
describes a parallel bus where every bit shares the same source and
destination blocks.

The trailing `unknown` keyword works identically to `add_net unknown`: all pins
for every expanded net are stored in BDB with `dir="UNKNOWN"`.

**Example:**
```
add_bus data[8]  u_cpu.dout  u_mem.din       # expands to data_0 … data_7
add_bus addr[16] u_cpu.addr  u_mem.addr      # expands to addr_0 … addr_15
add_bus clk[4]   u_clk.out   u_tile.clk  unknown   # undirected clock bus
```

---

### `corner_margin`

```
corner_margin dx <n> [dy <n>]
```

Set a global corner margin applied to all blocks that have no per-block
`corner_margin` override. Percentage variants (`pct_h` / `pct_v`) are not
supported globally because the margin in layout units depends on individual
block dimensions — use `dx` / `dy` instead.

| Argument | Type | Description |
|---|---|---|
| `dx N` | keyword+int | Shrink top/bottom (X-direction) faces by `N` layout units. If `dy` is omitted, same value applies to Y. |
| `dy N` | keyword+int | Shrink left/right (Y-direction) faces by `N` layout units. |

The margin constrains where segment endpoints can land on a block face.
`dy` limits the Y range on left/right (vertical) faces; `dx` limits the X
range on top/bottom (horizontal) faces. A guard prevents the margin from
inverting the face if the block is smaller than `2 × margin`.

**Example:**
```
corner_margin dx 8          # 8-unit margin on all faces for all blocks
corner_margin dx 5 dy 10    # 5-unit X margin, 10-unit Y margin
```

---

### `detour_channel`

```
detour_channel <dir> <size> [<dir> <size> …]
```

Set the **outer-band width** for U-shape (and UU-shape) detour trunks in the
specified compass direction(s). Must be called before `generate_topologies` or
`generate_topologies_for_bundle`.

| Argument | Type | Description |
|---|---|---|
| `dir` | str | Direction shorthand — see table below |
| `size` | int | Outer band width in layout units. Negative value resets the direction to auto. |

**Direction shorthands:**

| `dir` | Directions set |
|---|---|
| `N` | North — above the bundle bounding box (larger Y) |
| `S` | South — below the bundle bounding box (smaller Y) |
| `E` | East — right of the bundle bounding box (larger X) |
| `W` | West — left of the bundle bounding box (smaller X) |
| `Y` | Both N and S |
| `X` | Both E and W |
| `A` | All four directions |

Multiple `dir size` pairs may appear in one command.

**Background — why this matters:**

The topology generator places U-shape trunks at a distance of
`max(min_stub_length, 0.1 × block_span)` beyond the bundle bounding box
(default `min_stub_length` = 20 layout units). This creates an **outer band**
in the congestion model whose capacity equals that distance.

If the outer band is narrower than a bus's effective width
(`bus_width × dilution_factor`), every U-topology candidate will overflow that
band more severely than the direct I-shape overflows the primary channel. The
planner then correctly — but unhelpfully — prefers the direct topology, leaving
congestion unresolved and DetailedNUTS unable to place all bits.

Setting a larger outer margin gives detour topologies a band wide enough to
carry the bus, allowing the planner to route one bus through the main channel
and the other through the detour at zero overflow.

**Sizing rules of thumb:**

| Goal | Minimum `size` |
|---|---|
| Abstract NUTS passes (no overflow) | `≥ max_bus_width × dilution_factor` |
| DetailedNUTS places all bits | `≥ one full layer track-pattern unit pitch` (printed as `unit_pitch` by `[RoutingGrid]` at startup) |

In practice, setting `size` to the full primary channel width (the Y or X span
of the busiest block pair) satisfies both conditions for typical bus widths.

**Notes:**
- Calling `detour_channel` multiple times is additive: later calls override only
  the specified directions; unspecified directions keep their previous value.
- Negative `size` resets the named directions back to the auto heuristic.
- The command has no effect on Z-shapes, L-shapes, I-shapes, or multicast
  TRUNK topologies — only on U-shape and UU-shape outer trunks.

**Examples:**
```buda
# Set both north and south outer bands to 100 units
detour_channel Y 100

# Asymmetric: wide detour above, narrow below
detour_channel N 120  S 40

# Wide detour in all directions
detour_channel A 100

# East/West detour for vertical routing channels
detour_channel X 80

# Reset south to auto
detour_channel S -1
```

---

### `set_min_stub_length`

```
set_min_stub_length <len>
```

Set the global minimum stub length for routing to and from block boundary pins.

| Argument | Type | Description |
|---|---|---|
| `len` | int | Minimum length in layout units. |

**Example:**
```buda
set_min_stub_length 20
```

---

### `set_min_stub_length_dir`

```
set_min_stub_length_dir <dir> <len>
```

Set the minimum stub length for a specific direction: horizontal (`H` or `HORIZONTAL`) or vertical (`V` or `VERTICAL`).

| Argument | Type | Description |
|---|---|---|
| `dir` | str | Direction: `H` (or `HORIZONTAL`), or `V` (or `VERTICAL`). |
| `len` | int | Minimum length in layout units. |

**Example:**
```buda
set_min_stub_length_dir H 15
```

---

### `set_min_stub_length_layer`

```
set_min_stub_length_layer <layer_name> <len>
```

Set the minimum stub length for a specific metal layer.

| Argument | Type | Description |
|---|---|---|
| `layer_name` | str | Metal layer name (e.g. `M3`). |
| `len` | int | Minimum length in layout units. |

**Example:**
```buda
set_min_stub_length_layer M3 10
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

### `run_hier_bundler`

```
run_hier_bundler [depth <N>]
```

Group nets into hierarchy-aware bundles (HBundles) by querying the component hierarchy in the open BDB. Must be called after `open_bdb` and populating the database.

| Argument | Type | Description |
|---|---|---|
| `depth N` | keyword+int | Optional. Maximum hierarchy depth to traverse and group (defaults to `1`). |

HBundles group signals that cross cell boundaries at different levels of the physical hierarchy, allowing the planner and routing engines to distinguish local intra-cell routing from top-level inter-cell interconnect.

Each net is bundled **exactly once**, at its most specific endpoint projection within `depth N`; the bundle's level is the depth of the endpoints' common ancestor (its routing context). Identical cell-local buses across instances of the same cell merge into one template bundle carrying all instance paths (expanded back per instance by `run_planner hier`).

**Example:**
```buda
run_hier_bundler depth 1
```

---

### `dump_hbundles`

```
dump_hbundles [expanded] [depth N]
```

Print a one-line summary for every HBundle. Must be called after `run_hier_bundler`.

| Argument | Type | Description |
|---|---|---|
| `expanded` | keyword | Show the post-expansion per-instance wrappers in `self.bundles` (state after `run_planner hier`). Without this flag the pre-expansion snapshot captured at `run_hier_bundler` time is shown. |
| `depth N` | keyword+int | Filter output to bundles at hierarchy level N. |

**Output format per line:**

```
hb-{id}  D{level}  {kind}  "{short_reason}"  nets={n}  cands={c}  [{instances}]
```

| Field | Description |
|---|---|
| `hb-{id}` | Bundle integer ID |
| `D{level}` | Hierarchy depth of this bundle |
| `{kind}` | `cell:{cell_context}` if cell_context is set; `cross-level` if drv_spec_depth ≥ 0; else `cross-block` |
| `{short_reason}` | Abbreviated reason string (driver/receiver signature) |
| `nets={n}` | Number of nets in the bundle |
| `cands={c}` | Number of topology candidates (0 = topology not yet generated) |
| `[{instances}]` | Instance list — shown only for cell-level bundles |

By default, output reflects the **pre-expansion snapshot** (`_hier_bundles_orig`) captured at `run_hier_bundler` time — the canonical bundle IDs as the user sees them. After `run_planner hier`, adding `expanded` shows the runtime per-instance wrappers instead.

**Example output (pipeline test vehicle):**
```
hb-1  D0  cross-block  "DRV:src_i|REC:proc_i,"  nets=8  cands=7
hb-2  D0  cross-block  "DRV:proc_i|REC:snk_i,"  nets=8  cands=7
hb-3  D1  cross-block  "DRV:src_i/buf_i|REC:proc_i/pa_i,"  nets=8  cands=5
hb-4  D1  cell:proc_cell  "DRV:pa_i|REC:pb_i,"  nets=8  cands=4  [proc_i]
hb-5  D1  cell:proc_cell  "DRV:pb_i|REC:pc_i,"  nets=8  cands=4  [proc_i]
hb-6  D1  cross-block  "DRV:proc_i/pc_i|REC:snk_i,"  nets=8  cands=5
```

**Example:**
```buda
run_hier_bundler depth 1
dump_hbundles                 # show all 6 bundles
dump_hbundles depth 1         # show only depth-1 bundles
dump_hbundles expanded        # show expanded per-instance view (after run_planner hier)
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

| Type string | Segments | Description |
|---|---|---|
| `I_H` / `I_V` | 1 | Straight H or V wire; generated when margin-inset bboxes overlap on one axis. Always shortest; tried first. |
| `L_HV@x{bend}@y{hy}` | 2 | H then V; two corner variants (top/bottom face exit). |
| `L_VH@y{bend}@x{vx}` | 2 | V then H; two corner variants (left/right face exit). |
| `Z_HVH@x{cut}@y{ty}` | 2–3 | H stub · V trunk · H stub; one candidate per Hanan channel midpoint strictly between the two blocks. |
| `Z_VHV@y{cut}@x{vx}` | 2–3 | V stub · H trunk · V stub; symmetric. |
| `U_HVH@x{cut}` | 2–3 | H stub · V trunk (outside block bbox in X) · H stub. |
| `U_VHV@y{cut}` | 2–3 | V stub · H trunk (outside block bbox in Y) · V stub. |
| `UU_VHV@y{cut}` | 3–4 | H+V L-exit from src side face, then H trunk OOB. Requires `double_detour`. |
| `UU_HVH@x{cut}` | 3–4 | V+H L-exit from src side face, then V trunk OOB. Requires `double_detour`. |

**Candidate shapes generated (multicast):**

| Type string | Description |
|---|---|
| `TRUNK_H@y{trunk}` | H spine + V stubs to each receiver. Optimised with pass-through snapping and extreme-stub slide. For receivers with `teg_mode over`, may carry a `bridge_segments` entry (see below). |
| `TRUNK_V@x{trunk}` | V spine + H stubs. Symmetric. Same bridge logic applies. |
| `TRUNK_H_OOB@y{trunk}` | H spine outside the pin bounding box + V stubs (detour equivalent of U-shape). |
| `TRUNK_V_OOB@x{trunk}` | V spine outside the pin bounding box + H stubs. |
| `MST_HV` | Prim MST on block bboxes, L-bends H-first. Lower total wirelength for scattered pins. |
| `MST_VH` | Prim MST, L-bends V-first. |
| `BITRUNK_H` | Two parallel H spines at 25th/75th percentile Y + vertical backbone. Generated for 4+ receivers. |

**Bridge segments (`teg_mode over`):**

When a receiver block uses `teg_mode over` and the trunk falls in the gap
between its rects, the topology carries a **bridge segment** for that block.
The bridge is a short wire segment placed along the outer face of the block's
union bounding box (top face for an H-trunk gap; right face for a V-trunk gap).
It is stored in `topology.bridge_segments[block_name]` — separate from the main
`topology.segments` list — so the planner and visualizer can distinguish routing
wires from bridge annotations. Bridge topologies have higher adjusted wirelength
than their `thru` counterparts (the bridge adds explicit wire) and are therefore
ranked after `thru` candidates when all else is equal.

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
- Candidate shapes generated are identical to `generate_topologies_for_bundle` (I, L, Z, U, UU, multicast TRUNK/MST/BITRUNK variants).
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

### `generate_hier_topologies`

```
generate_hier_topologies [center_mode] [double_detour]
```

Generate routing topology candidates for all HBundles generated by `run_hier_bundler`. Must be called after `run_hier_bundler` and before `run_planner`.

The topology generator automatically determines the routing context for each HBundle:
1. **Intra-cell / Same-level local routing**: generates candidates relative to the cell's local floorplan and boundaries.
2. **Cross-level routing**: generates candidates spanning between different depths in the hierarchy using absolute global coordinates.

**Optional flags** (same as `generate_topologies_for_bundle`):

| Flag | Effect |
|---|---|
| `center_mode` | Use block centres as connection points instead of the nearest busterm face. |
| `double_detour` | Also generate `UU_VHV` / `UU_HVH` high-detour candidates for very congested situations. |

**Zero-candidate warning:** If any HBundle ends up with 0 topology candidates, the CLI prints:
```
  WARNING: HierTopo D{level}: bundle {id} ({label}) 0 candidates — bundle will be unrouted!
```
Downstream stages (`run_planner`, `run_nuts`) silently skip bundles with no candidates, so this warning is the only indication that a bundle will not be routed. Common causes: source or destination block not present in the floorplan, or extreme span/layer constraints ruling out all shapes.

**Example:**
```buda
run_hier_bundler depth 1
generate_hier_topologies
run_planner hier 5
```

---

### `generate_topologies_for_hbundle`

```
generate_topologies_for_hbundle <bundle_id> [center_mode] [double_detour]
```

Re-run topology generation for a single HBundle identified by its integer ID. Uses the same 3-case dispatch as `generate_hier_topologies` (cell-local / cross-level / cross-block). Useful for debugging when a specific bundle has zero candidates or when experimenting with flags without re-running all bundles.

| Argument | Type | Description |
|---|---|---|
| `bundle_id` | int | Integer bundle ID (as shown by `dump_hbundles`). |
| `center_mode` | keyword | Use block centres as connection points instead of the nearest busterm face. |
| `double_detour` | keyword | Also generate `UU_VHV` / `UU_HVH` high-detour candidates. |

**Requirements:** open BDB, `run_hier_bundler` already called.

**Zero-candidate warning:** Same WARNING line as `generate_hier_topologies` if the bundle ends up with 0 candidates.

**Post-expansion advisory:** If `run_planner hier` has already been called and the specified bundle ID no longer appears in `self.bundles` (because it was expanded into per-instance wrappers), the CLI prints:
```
Note: bundle {id} was expanded by run_planner hier — re-run generate_hier_topologies before planning.
```

**Example:**
```buda
generate_topologies_for_hbundle 4              # re-generate candidates for hb-4
generate_topologies_for_hbundle 4 center_mode  # with centre-mode flag
```

---

## Stage 3 — Global router / planner

### `set_planner_param`

```
set_planner_param <name> <value>
```

Tune a global planner cost coefficient. Takes effect at the next `run_planner`:
each run seeds a fresh planner from all values set so far, so knobs may be
adjusted between runs to re-plan with different weights.

| Parameter | Default | Description |
|---|---|---|
| `kCong` | `1.0` | Congestion cost coefficient. Multiplies the overflow ratio `max(0, usage+eff_width−cap)/cap` for each channel band (zero when the segment fits). Note: overflow is gated as a hard constraint first (see `run_planner`); `kCong` only arbitrates among candidates when overflow is genuinely unavoidable, or prices residual soft pressure. |
| `kSpan` | `0.001` | Span-mismatch cost coefficient. Multiplies the excess span outside a layer's `[span_min, span_max]` window. Guides long segments to higher metal and short stubs to lower metal. |
| `base_cost_non_top` | `0.5` | Penalty per segment for using a non-`TOP` layer, scaled by segment span (see `base_span_ref`). Keeps the default preference on top layers without hard-blocking lower ones. |
| `base_span_ref` | 25% of the larger Hanan grid extent | Span at which a segment pays the full `base_cost_non_top`; shorter segments pay proportionally less (`× span/base_span_ref`). Short stubs therefore drop to lower layers when TOP bands saturate instead of detouring on TOP — preserving TOP capacity for long trunks. |
| `kWL` | `0.001` | Wirelength cost per layout unit, added to the topology score. Steers equal-congestion choices toward shorter topologies, so a detour wins only when it avoids real congestion. |

**Example:**
```
set_planner_param kCong 2.0          # stronger congestion avoidance
set_planner_param kSpan 0.005        # stronger span preference
set_planner_param base_cost_non_top 0.1
set_planner_param kWL 0.01           # stronger preference for short routes
```

---

### `run_planner`

```
run_planner [<iterations>]
```

Runs the global congestion-aware router. Bundles are processed widest-first
(fattest-first greedy). For each bundle:

1. Builds a Hanan-grid congestion map (one cut per channel per layer).
2. Scores every topology candidate — for each segment independently selects
   the best layer from the direction-appropriate set (H layers for H segments,
   V layers for V segments).  Segment score = `kCong·overflow/cap + kSpan·excess
   + base_cost_non_top·min(1, seg_span/base_span_ref)`, where
   `overflow = max(0, usage+eff_width−cap)` (zero when the segment fits) and the
   non-TOP penalty scales with segment span so short stubs offload to lower
   layers cheaply while long trunks stay on TOP (see `set_planner_param
   base_span_ref`).
   The congestion charge goes to the cheapest Hanan band the segment's slide
   interval can host the bus in (slide-aware lookup), not just the band at the
   interval centre.  Band capacity is clamped to the slide window's overlap
   with the band: demand confined to a sub-band window (slide bounds are
   usually not Hanan lines) is not priced against the whole band.
   The effective bus width per layer uses the measured per-bit channel cost
   when a track pattern is defined (`bits × unit_pitch/n_signals`, see
   `def_track_pattern`); otherwise the density model (`width × dilution`).
   Topology score = maximum segment score (weakest-link) `+ kWL·wirelength`.
3. Selects the topology with the lowest score. Ties broken by candidate index
   (shortest wirelength first, since candidates are sorted).
4. Commits the winning topology's per-segment layer choices to the running
   congestion state so subsequent bundles see the correct congestion.
5. Applies any architect-pinned selections from the `.json` sidecar file
   (see `visualize_topologies`): for a pinned bundle, only that one topology
   is scored (layer assignment is still computed).

**Overflow is a hard constraint** — an overflowing band cannot physically host
the bus, so NUTS would emit a real overlap. Each bundle walks an escalation
ladder (see [congestion_planner.md](congestion_planner.md) for the full design):

1. `STRICT` — only candidates that fit their slide windows **and** are
   overflow-free compete on the soft costs above.
2. **Rip-up & replan** — if no candidate is overflow-free, earlier-committed
   bundles are ripped up one at a time, ranked by the demand they hold on the
   failing bundle's contended bands (the actual blocker first; zero-overlap
   victims skipped), and the pair is replanned; accepted only if both end up
   overflow-free.
3. `ALLOW_OVERFLOW` — overflow truly unavoidable: the least-cost candidate is
   committed with a `WARNING`.
4. `BEST_EFFORT` — no candidate even fits its slide windows (e.g. stale sidecar
   pins): committed anyway with a `WARNING` rather than dropping the bundle.

| Argument | Type | Default | Description |
|---|---|---|---|
| `iterations` | int | 5 | Reserved for planned PathFinder-style negotiated-congestion iterations (see [future/planner_ripup_extensions.md](future/planner_ripup_extensions.md)); currently unused beyond the first pass. |

**Output:** Prints per-bundle selection: topology type, assigned per-segment
layers, ` [pinned]`/` [replanned]` tags, and the raw overflow in layout units
(0 unless a fallback mode committed). Rip-ups print
`[Planner] Rip-up: replanned bundle <P> to free capacity for bundle <B>:`
followed by the victim's new selection line; fallback modes print
`[Planner] WARNING: Bundle <id>: no overflow-free candidate (even after
rip-up); …` or `…: no candidate fits its slide windows …` respectively.

**Side effects:**
- Creates a `GlobalRouter` object accessible to `visualize` for congestion
  overlay drawing.
- Reads and applies `<script>.json` sidecar if it exists.

**Example:**
```
run_planner 5
```

---

### `run_planner hier`

```
run_planner hier [<iterations>]
```

Hierarchy-aware variant of `run_planner` for the HBundle pipeline. Requires an
open BDB, `run_hier_bundler`, and `generate_hier_topologies`. Steps:

1. Applies architect-pinned sidecar selections to the pre-expansion bundles.
2. **Expands** each cell-level HBundle into one wrapper per cell instance, with
   candidates offset to absolute coordinates by the instance origin.
3. Assigns each wrapper `priority = -(level·10000 + n_candidates)` and runs the
   congestion planner sorted by `(priority DESC, width DESC)` — depth-0 globals
   first, then within each level the least-flexible bundles first.

Two mechanisms manage the local-vs-global competition this ordering creates
(see [HIER_PLANNER.md](HIER_PLANNER.md) §7 and
[congestion_planner.md](congestion_planner.md)):

- **Cell-interior demand reservation.** Each expanded cell-local wrapper parks
  its effective bus width as virtual usage on the TOP-layer bands inside its
  instance bbox before planning starts; the reservation is released right
  before the bundle's own turn. Earlier globals avoid a cell-interior band
  only when it cannot hold both bundles — a "leave room" constraint, not a
  keep-out.
- **Per-level summary.** When bundles span multiple depth levels, the planner
  prints a per-level report after planning:

  ```
  [Planner] Level summary:
    D0: 1 bundles  strict:1  layers{M6:1}
    D1: 1 bundles  strict:1  layers{M5:1 M6:1}
  ```

  Stage counts other than `strict:` (`ripup:` / `overflow:` / `best_effort:`)
  flag levels losing the competition or under-capacity regions.

The span-scaled non-TOP penalty (`base_span_ref`) complements both: short
cell-local stubs drop to lower layers when TOP saturates instead of detouring
on TOP, preserving TOP capacity for long global trunks.

**Side effects:** Replaces the session bundle list with the expanded
per-instance wrappers (see `dump_hbundles expanded`); subsequent `run_nuts` /
`check_connectivity` / `visualize` operate on the expanded set.

**Example:**
```
run_hier_bundler depth 1
generate_hier_topologies
run_planner hier 5
```

Demonstrated end-to-end by `flow/hbundles/08_cross_level.buda` and
`flow/hbundles/09_local_global_compete.buda`.

---

### `select_topology`

```
select_topology <bundle_id> <topo_id>
```

Manually pin a specific topology candidate for a given bundle by its numeric bundle ID and topology candidate ID (1-based index). This manually overrides the planner's selection.

If the planner has already run, layer assignment is automatically re-run with
the pin in place (logged with a `[pinned]` marker), so per-segment layers
always describe the pinned topology's segment list. Pins set before
`run_planner` are honored when it runs.

| Argument | Type | Description |
|---|---|---|
| `bundle_id` | int | Numeric ID of the bundle (e.g. `2`). |
| `topo_id` | int | 1-based ID of the topology candidate to pin (e.g. `2` for the second topology). |

**Example:**
```buda
# Pin topology candidate 2 for bundle 2
select_topology 2 2
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

## Stage 8 — Routing grid

Stage 8 defines the physical track structure of each metal layer. Commands in this stage can appear anywhere in the script — they only take effect when `run_detailed_nuts` (Stage 9) is called. See [docs/routing_grid.md](routing_grid.md) for the full design.

### `def_track_pattern`

```
def_track_pattern <layer_id> <origin> <type1> <w1> <sp1> [<type2> <w2> <sp2> …]
```

Define the repeating track pattern for a layer. The pattern tiles from `origin` outward across the full layer extent.

| Argument | Type | Description |
|---|---|---|
| `layer_id` | int | Layer ID as registered with `def_layer` |
| `origin` | float | Anchor position of the first unit in layout units (use `0.0` to align with chip origin) |
| `type` | string | Track type: `POWER`, `GROUND`, `CLOCK`, `SHIELD`, `SIGNAL`, or `CUSTOM` |
| `w` | float | Track width in layout units |
| `sp` | float | Space after this track (gap to the next slot), in layout units |

Each `<type> <w> <sp>` triple defines one slot in the repeating unit. Slots are listed in order from low to high perpendicular position. The unit pitch is the sum of all `(w + sp)` values.

**Notes:**
- Each layer may have at most one global pattern. Calling `def_track_pattern` a second time on the same layer replaces the existing pattern.
- The `origin` should be consistent across layers and with the power-grid intent — mismatched origins produce phase drift across layers.
- At least one `SIGNAL` slot is required for `run_detailed_nuts` to place any bit-wires.
- Calling `run_detailed_nuts` without a defined pattern for a bus segment's layer causes that bus to be counted as unplaced.
- Defining a pattern also syncs the layer's **dilution factor** and **per-bit
  channel cost** (`unit_pitch / n_signal_slots`) into the layer stack. The
  per-bit cost supersedes `def_layer`'s overhead% for planner and NUTS
  effective bus widths: a 16-bit bus on a 34-pitch/8-signal layer is priced
  at 16 × 4.25 = 68 units, matching what detailed NUTS can actually place.

**Example:**
```buda
# 4 signal tracks per 14-unit period, surrounded by POWER/GROUND rails
def_track_pattern 4 0.0  POWER 2.0 1.0  SIGNAL 1.0 1.0  SIGNAL 1.0 1.0  GROUND 2.0 1.0  SIGNAL 1.0 1.0  SIGNAL 1.0 1.0

# M3 (vertical): denser signal pitch, CLOCK rail every 8 tracks
def_track_pattern 3 0.0  CLOCK 1.0 0.5  SIGNAL 0.5 0.5  SIGNAL 0.5 0.5  SIGNAL 0.5 0.5  SIGNAL 0.5 0.5
```

---

### `add_grid_override`

```
add_grid_override <layer_id> <x1> <y1> <x2> <y2> <origin> <type1> <w1> <sp1> [<type2> <w2> <sp2> …]
```

Override the track pattern for a rectangular region on a layer. Useful when a floorplan region (e.g. an SRAM macro) uses a different power-grid pitch than the rest of the chip.

| Argument | Type | Description |
|---|---|---|
| `layer_id` | int | Layer to override |
| `x1 y1 x2 y2` | int | Bounding box of the override region (Hanan-grid-aligned) |
| `origin` | float | Anchor for the local pattern within this region |
| `type w sp …` | — | Same slot format as `def_track_pattern` |

First-match wins: if a point falls in multiple override regions, the first one defined takes precedence.

**Notes:**
- Power and clock tracks are broken at region boundaries — a DRC gap at the seam is accepted.
- The global pattern (from `def_track_pattern`) still applies outside the override region.
- Override regions and the global pattern are independent objects; each has its own `origin`.

**Example:**
```buda
# Dense SRAM macro at (200,0)-(400,500) uses half-pitch POWER/SIGNAL layout
add_grid_override 4  200 0 400 500  0.0  POWER 1.5 0.5  SIGNAL 0.5 0.5  SIGNAL 0.5 0.5  GROUND 1.5 0.5  SIGNAL 0.5 0.5  SIGNAL 0.5 0.5
```

---

### `report_overhead`

```
report_overhead
```

Compares the overhead percentage set in each `def_layer` command against the overhead implied by the layer's `def_track_pattern`. Call this after defining track patterns to verify that the two values are consistent — a mismatch means abstract NUTS (which uses the `def_layer` dilution) and detailed NUTS (which uses the actual track geometry) will disagree on how much space a bus occupies.

For each layer the command prints:

| Column | Meaning |
|---|---|
| `def_layer%` | Overhead set in `def_layer` (or `(not set)` if omitted / zero) |
| `actual%` | Overhead derived from the track pattern: `(1 − signal_density) × 100` |
| `dilution` | `unit_pitch / signal_width_sum` from the track pattern |
| `status` | `OK` if the two values agree within 0.05%, otherwise `MISMATCH` |

For any layer with a mismatch, the command also prints a corrected `def_layer` line ready to paste back into the script.

**Example output** for a layer with 33.33% set in `def_layer` but a track pattern whose actual overhead is 55.56%:
```
[report_overhead] Layer overhead analysis:
  Layer       def_layer%   actual%  dilution  status
  ---------- ----------- --------- ---------  ------
  M4              33.33%    55.56%    2.2500  MISMATCH (diff=22.23%)
    -> suggested: def_layer 4 M4 H TOP 55.56
  M5              33.33%    55.56%    2.2500  MISMATCH (diff=22.23%)
    -> suggested: def_layer 5 M5 V TOP 55.56
```

**Notes:**
- `report_overhead` is read-only — it does not change any layer settings.
- Only the global pattern per layer is checked; per-region overrides (`add_grid_override`) are not compared.
- Layers with no track pattern defined show `(no pattern)` in the `actual%` column.

---

## Stage 9 — Detailed NUTS

Stage 9 snaps each abstract bus segment (from Stage 4) to concrete signal-track positions (from Stage 8). See [docs/detailed_nuts.md](detailed_nuts.md) for the full design.

### `run_detailed_nuts`

```
run_detailed_nuts [lo_hi|hi_lo]
```

For each bus segment in the NUTS result, calls `signal_tracks_in()` on its layer's routing grid and assigns one signal track per bit-wire.

| Argument | Type | Default | Description |
|---|---|---|---|
| `lo_hi` / `hi_lo` | keyword | `lo_hi` | Bit ordering: `lo_hi` assigns bit 0 to the lowest available signal track, increasing upward; `hi_lo` assigns bit 0 to the highest track, decreasing. |

**Algorithm:**

1. Convert each `TrackSegment` from `run_nuts` to a `BusSegment` (layer, span, interval, bit_width from rounded abstract width).
2. For each `BusSegment`, enumerate signal tracks inside `[interval_lo, interval_hi]` using the layer's `RoutingGrid`.
3. Select tracks according to `bit_order`:
   - `lo_hi`: take the first `bit_width` signal tracks.
   - `hi_lo`: take the last `bit_width` signal tracks.
4. If a bus is marked `timing_critical` (not yet settable from `.buda`; API available in Python): find the first contiguous window of `bit_width` signal tracks — a window where no POWER/GROUND/CLOCK track centre lies between any adjacent pair.
5. If fewer signal tracks are available than `bit_width`, or no valid contiguous window exists, the entire bus is **unplaced** (all-or-nothing; no partial placement).

**Output:** Prints the total number of net segments placed and the number of bits unplaced.

**Requires:** `run_nuts` must have been called first. At least one `def_track_pattern` must cover the layers used by the NUTS result.

**Example:**
```buda
run_nuts 2.0
def_track_pattern 4 0.0  POWER 2.0 1.0  SIGNAL 1.0 1.0  SIGNAL 1.0 1.0  GROUND 2.0 1.0  SIGNAL 1.0 1.0  SIGNAL 1.0 1.0
run_detailed_nuts
```

With HI_LO ordering:
```buda
run_detailed_nuts hi_lo
```

---

## Verification commands

### `check_connectivity`

```
check_connectivity [stage] [all]
```

Verify signal/bus electrical connectivity and report any open connections, missing stubs, or track routing violations. The `nuts` and `dnuts` stages also
flag layer-direction violations: a segment (or its bit wires) assigned to a
layer whose routing direction does not match the segment's orientation is
reported as unbuildable.

| Argument | Type | Default | Description |
|---|---|---|---|
| `stage` | str | `dnuts` | Routing stage to verify: `topo` (topology candidates), `nuts` (abstract track sharing), or `dnuts` (detailed bit placement). |
| `all` | keyword | — | Checks all candidate topologies instead of just the selected one. Only applicable for the `topo` stage. Automatically enabled if no topology is selected yet. |

**Hierarchical design — missing-block warning:** When `check_connectivity` is called after `run_planner hier`, it additionally checks that every `connected_block_name` referenced in the selected topologies exists in the current floorplan. If any are missing:
```
  Warning: N block(s) referenced in topologies but not in floorplan: name1, name2, ...
  Hint: call 'add_blocks_from_bdb N skip' for all required depths.
```
This catches the common error of calling only `add_blocks_from_bdb 0` when depth-1 cell-level bundles also need `add_blocks_from_bdb 1 skip` (because they reference absolute paths like `proc_i/pa_i`). The check is only active when `run_planner hier` has been used (detected by `_hier_expansion_map` being non-empty).

**Example:**
```buda
# Check detailed NUTS placement for opens (typically at the end of script)
check_connectivity dnuts
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
| `▶  Re-run & Refresh` button | Re-run the planner (respecting all pinned topologies) + NUTS, then refresh the main layout window. Equivalent to pressing `r`. |
| `r` key | Same as `▶  Re-run & Refresh` |

The `▶  Re-run & Refresh` button appears only when the session was started
with `run_nuts` already completed (i.e. `visualize` was called after NUTS).

**Persistence:** Selected topologies are saved to `<script>.json` alongside
the `.buda` file. The next `run_planner` will load and honour these pins,
overriding the congestion-based choice for pinned bundles.

**Window title:** `<first_net_name> (Bundle N)` — identifies which bundle is
being explored.

**Hierarchical flow deduplication:** After `run_planner hier`, `self.bundles` holds one wrapper per cell instance. Without deduplication, `visualize_topologies -all` would open the same cell-level bundle template once per instance (e.g. two windows for `pa→pb` if there are two proc instances). Instead, cell-level bundles are deduplicated by `(cell_context, reason)`. The first instance is shown with a title annotation: `(N instances — showing first)`. This avoids redundant exploration windows while still accurately representing the template topology.

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
| `<script>_flow.log` | Session start | Mirror of all Python `print()` output (planner decisions, NUTS metrics, warnings). C++ output is not captured. Written from the first command; useful for post-mortem review. |

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

# ── Detour channel (optional) ───────────────────────────────
# Set the outer-band width for U-shape detour trunks.
# Without this, the default margin (~20 units) may be too narrow for wide
# buses, causing the planner to prefer congested direct routes over detours.
# Rule of thumb: use the primary-channel span or ≥ the layer unit_pitch.
# corner_margin dx 8          # example: constrain stubs away from block corners
# detour_channel Y 100        # 100-unit north+south outer band
# detour_channel A 100        # 100-unit band in all four directions

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

# ── Stage 8: routing grid (track pattern definitions) ─────────
# Define the repeating POWER/SIGNAL/GROUND pattern for each layer.
# slot format: <TYPE> <width> <space_after>  (one unit = one repeating period)
def_track_pattern 4 0.0  POWER 2.0 1.0  SIGNAL 1.0 1.0  SIGNAL 1.0 1.0  GROUND 2.0 1.0  SIGNAL 1.0 1.0  SIGNAL 1.0 1.0
def_track_pattern 3 0.0  POWER 2.0 1.0  SIGNAL 1.0 1.0  SIGNAL 1.0 1.0  GROUND 2.0 1.0  SIGNAL 1.0 1.0  SIGNAL 1.0 1.0

# ── Stage 9: snap bit-wires to concrete signal tracks ─────────
run_detailed_nuts        # lo_hi ordering (default)
# run_detailed_nuts hi_lo  # reverse bit ordering

# ── Visualise ────────────────────────────────────────────────
visualize
```

---

## BDB — Physical Design Database

BDB commands operate on a persistent SQLite store for component placements,
nets, pins, and hierarchy. They can appear anywhere in a script but are
independent of the BUDA routing pipeline (stages 1–9).

Full reference: **[docs/BDB_REFERENCE.md](BDB_REFERENCE.md)**

### Quick reference

| Command | Description |
|---|---|
| `open_bdb <path>` | Open or create a `.bdb` file. Use before any other BDB command. |
| `import_def_lef <def> <lef>` | Import placements from DEF + cell sizes from LEF. Clears all tables. |
| `import_verilog <v>` | Elaborate hierarchy from Verilog; preserves coordinates from a prior `import_def_lef`. |
| `bdb_net_mode on\|off` | Toggle whether nets/buses are written directly to BDB database. |
| `add_blocks_from_bdb <depth> [deepest\|skip\|error]` | Populate the BUDA floorplan from BDB instances at hierarchy depth `N`. |
| `set_die <w> <h>` | Set die size (bounding box) dimensions in the database. |
| `add_cell <name> <width> <height>` | Define a cell template and its size in BDB. |
| `add_inst <inst> <cell> <parent\|-> <x> <y>` | Place a new instance at coordinates relative to parent or root (`-`). |
| `add_inst_to_cell <parent_cell> <inst> <child_cell> <x> <y>` | Place a sub-instance inside a parent cell template. |
| `add_cell_pin <cell> <pin> [INPUT\|OUTPUT\|INOUT] [<px> <py>]` | Add a pin with optional offset coordinates to a cell definition. |
| `move_comp <name> <x> <y>` | Shift instance `name` to new origin `(x, y)`; preserves cell size. |
| `resize_cell <cell> <w> <h>` | Set `x2=x1+w`, `y2=y1+h` for every instance of cell type `cell`. |
| `flip_comp <name> x\|y` | Mirror component `name` horizontally or vertically. |
| `rotate_comp <name> 90\|180\|270` | Rotate component `name` by specified degrees. |
| `add_comp <name> <cell> <parent\|-> <x1> <y1> <x2> <y2> [leaf\|nonleaf]` | Insert a new component. Use `−` as parent for a root instance. |
| `derive_busterms [max_depth]` | Extract physical port locations from the hierarchy and write to BDB. |

**Common patterns:**

```buda
# DEF + Verilog merge
open_bdb  flow/lefdef/gcd/gcd.bdb
import_def_lef  flow/lefdef/gcd/gcd.def  flow/lefdef/gcd/gcd.lef
import_verilog  flow/lefdef/gcd/gcd.v

# Fixup after import
move_comp   u_regfile  10.0  10.0
resize_cell DFFRX1     5.6   4.0

# Build from scratch
open_bdb  flow/manual/tiny.bdb
add_comp  u_a  blk  -      0   0  100 100 nonleaf
add_comp  u_b  blk  -    200   0  300 100 nonleaf
add_comp  u_a/x0  cell  u_a   10  10   50  50 leaf
```
