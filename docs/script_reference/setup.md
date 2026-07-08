# BUDA Script Reference — Setup commands

Technology, floorplan, netlist, and routing-policy declarations that precede the pipeline stages: `def_layer`, `add_block`, `add_keepout`, `add_net`, `add_bus`, `corner_margin`, `detour_channel`, `set_min_stub_length[_dir|_layer]`, `set_feedthru`, `set_track_pitch`.

Part of the [BUDA Script Reference](../BUDA_SCRIPT_REFERENCE.md) — see its pipeline overview for where these commands run in the flow.

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
add_block <name> <x1> <y1> <x2> <y2> [container] [corner_margin dx <n> [dy <n>]]
add_block <name> <x1> <y1> <x2> <y2> [container] [corner_margin pct_h <p> [pct_v <p>]]
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
| `container` | keyword | Optional. Mark this block as a **hierarchy envelope** — transparent to LOW layers. A container block's interior is not blocked for low-metal routing; congestion inside the envelope is charged via finer descendant block cuts. Used for hierarchical blocks whose children are separately imported as blocks. |

A per-block `corner_margin` overrides the global `corner_margin` command for
that block. The margin is baked into the block's `Busterm.bbox` at construction;
topology generation and the Hanan grid use the shrunken bounding box directly.

#### Adjacent, touching, and overlapping blocks

Topology generation handles blocks that are *not* cleanly separated as follows:

- **Shared full edge (abutment).** Two blocks sharing a face have coinciding
  facing edges, so the ordinary L/Z/U candidates collapse. A dedicated fallback
  realises the shared edge as a short crossing wire (`ABUT_H`/`ABUT_V`), so the
  bus routes — no action needed.
- **Single-corner touch.** Two blocks meeting at exactly one corner are rescued
  the same way: the fallback routes an L *around* the shared corner
  (`CORNER_HV`/`CORNER_VH`). This works at the default `corner_margin 0` — no
  margin is required.
- **Fully coincident or overlapping blocks.** Two blocks occupying the same
  rectangle (or an overlap with no routable channel left) produce **no
  candidate**, and `generate_topologies` emits a **zero-candidate warning** for
  that bus. This is intended: a bus between blocks that occupy the same space has
  no meaningful geometry, so the warning flags a placement problem rather than
  inventing a route.
- **Partial (staggered) overlap.** Two blocks that overlap but each stick out on
  both axes route as usual — a straight `I_H`/`I_V` through the shared band, `U`
  detours around the union, **and** two `L_OVL_*` L's that bend around the union's
  two free outer corners (each leg taps an exclusive, non-overlapping face), giving
  the planner a route that avoids the shared band. A block *nested* inside the
  other has no exclusive faces, so no corner L is generated.

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

# Hierarchy envelope: a top-level block whose children are imported as sub-blocks.
# LOW layers can route through the envelope; congestion is charged via child cuts.
add_block u_proc  0  0  400  400  container
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
- **Topology generation**: `generate_topologies` / `generate_topologies_for_bundle`
  incorporate keepout zones at two levels:
  - *Hanan grid*: keepout bounding-box edges are added to the local Hanan grid so
    Z/TRUNK midpoints snap to positions outside keepout bands rather than landing
    in the middle of a blocked region.
  - *Trunk filtering*: a TRUNK_H or TRUNK_V position is suppressed only when
    **all** routable layers in that direction are blocked at that position. If at
    least one same-direction layer is free the position is kept (the planner
    reassigns to the free layer).
  - *2-pin filtering*: L-shape, Z-shape, and U-shape candidates whose horizontal
    or vertical segment is fully blocked on **all** layers in the segment's
    direction are suppressed.
- **Planning**: `run_planner` subtracts the prohibited area from its congestion
  model, forcing it to choose topologies that detour around the zone.
- **Detailed Routing**: `run_detailed_nuts` automatically skips any signal
  tracks that pass through a keep-out zone for the segment's assigned layer.
  Additionally, solid (non-container) leaf-cell blocks automatically generate
  keepouts on all LOW-layer routing grids so signal tracks cannot route over
  cell interiors on non-TOP layers.
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
add_net <name> <pin1> <pin2>[,<pin3>…] inout
```

Add a single net to the netlist.

| Argument | Type | Description |
|---|---|---|
| `name` | str | Net name. Used as a bundle hint key — the first net name in a bundle identifies the bundle in sidecar files and `generate_topologies_for_bundle`. |
| `driver_pin` | str | Driver pin in `instance.port` form, e.g. `u_cpu.tx`. When `unknown` or `inout` is appended this is the first (positional) pin. |
| `receiver_pins` | str | Comma-separated list of receiver pins (no spaces), e.g. `u_mem.rx` or `u_a.rx,u_b.rx`. |
| `unknown` | keyword | Optional. Marks the net as having no known pin direction. All listed pins are stored in BDB with `dir="UNKNOWN"`. `run_hier_bundler` uses positional order as a last-resort fallback (after OUTPUT and INOUT checks). |
| `inout` | keyword | Optional. Marks the net as explicitly bidirectional. All listed pins are stored in BDB with `dir="INOUT"`. `run_hier_bundler` treats the first INOUT pin as a secondary driver (used when no OUTPUT pin exists); remaining INOUT pins are receivers. |

**Directed form** — use when you know which end drives:
```
add_net data_0  u_cpu.dout  u_mem.din
add_net req     u_cpu.req   u_arb.req0,u_arb.req1
```

**Undirected form** — use when direction is completely unknown at script time:
```
add_net clk  u_clkbuf.clk  u_cpu.clk,u_mem.clk  unknown
```

**Bidirectional form** — use for explicitly bidirectional ports (e.g. I²C SDA):
```
add_net sda  u_master.sda  u_slave.sda  inout
```

When `bdb_net_mode` is **off** both direction keywords have no effect on the
flat bundler — direction is always inferred positionally. When `bdb_net_mode`
is **on**, `unknown` uses `add_net_pins_undirected` and `inout` uses
`add_net_pins_inout`, storing the respective direction in the BDB pin table for
`run_hier_bundler`.

---

### `add_bus`

```
add_bus <prefix>[<N>]        <driver_pin> <receiver_pins>
add_bus <prefix>[<lo>:<hi>]  <driver_pin> <receiver_pins>
add_bus <prefix>[<N>]        <pin1> <pin2> unknown
add_bus <prefix>[<N>]        <pin1> <pin2> inout
```

Convenience macro that expands to a sequence of `add_net` calls.

| Form | Expands to |
|---|---|
| `bu[4]` | `bu_0`, `bu_1`, `bu_2`, `bu_3` |
| `bu[2:5]` | `bu_2`, `bu_3`, `bu_4`, `bu_5` |

The driver and receiver pins are the same for every expanded net — this
describes a parallel bus where every bit shares the same source and
destination blocks.

The trailing `unknown` and `inout` keywords work identically to their `add_net`
counterparts: all pins for every expanded net are stored in BDB with the
corresponding direction.

**Example:**
```
add_bus data[8]  u_cpu.dout  u_mem.din          # expands to data_0 … data_7
add_bus addr[16] u_cpu.addr  u_mem.addr         # expands to addr_0 … addr_15
add_bus clk[4]   u_clk.out   u_tile.clk  unknown  # undirected clock bus
add_bus sda[4]   u_m.sda     u_s.sda     inout    # bidirectional I²C bus
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

**Default:** `dx 0 dy 0` (no corner margin is applied by default).

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

**Default:** `auto` (represented by size `-1`) for all directions — i.e. if you
never call `detour_channel`, the detour band is **auto-sized per axis**, not a
fixed distance. For each side the generator ventures out

```
auto_margin = max( min_stub_length(dir), 1, 0.1 × bundle_span_on_that_axis )
```

beyond the bundle's bounding box — so **≈ 10% of the bundle's span** on that axis,
with a floor of the layer's min-stub length (and ≥ 1 unit). East/West use the
**X-span**, North/South use the **Y-span**. A wider bundle therefore detours
proportionally farther out; `min_stub_length` defaults to 20 layout units, so the
band is at least that wide. (`bundle_span` is the extent between the bundle's
extreme Hanan grid lines — essentially the source+destination bounding box.)
Setting any direction to a non-negative `size` overrides the auto value for that
direction only.

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

The topology generator places U-shape trunks at the auto distance above —
`max(min_stub_length(dir), 1, 0.1 × bundle_span_on_that_axis)` — beyond the
bundle bounding box (default `min_stub_length` = 20 layout units; E/W keyed off
the X-span, N/S off the Y-span). This creates an **outer band** in the congestion
model whose capacity equals that distance.

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
| `len` | int | Minimum length in layout units. Default `20`. |

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
| `len` | int | Minimum length in layout units. Default: unset (falls back to global value). |

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
| `len` | int | Minimum length in layout units. Default: unset (falls back to direction-specific value, then to global value). |

**Example:**
```buda
set_min_stub_length_layer M3 10
```

**Min stub length vs. the abutment epsilon.** The three `set_min_stub_length*`
commands are a *design* knob — a floor on stub segment length, resolved
most-specific-first (`layer > dir > global`), and legitimately settable to `0`
("no stub floor here"). Two full-edge-abutting blocks are rescued by a single
short wire *crossing* the shared edge (an `ABUT_H`/`ABUT_V` candidate); its
length is minimized to the applicable min-stub-length so the bus takes the
smallest channel. But that wire must never be zero-length — a zero-length segment
carries no bit-wires and cannot be placed by NUTS — so it is floored at a fixed
router constant, `kAbutmentSpanEpsilon` (2 layout units, `src/topology.h`):
effective length = `max(min_stub_length, epsilon)`. The epsilon is a
**non-degeneracy invariant, not a DRC parameter**, so it is intentionally *not*
scriptable: when the min stub floor is non-zero it already dominates, and when it
is `0` the epsilon is only the smallest length that still straddles the edge and
covers both blocks. There is no design case for the two differing beyond this, so
there is deliberately no `set_abutment_epsilon` command.

---

### `set_feedthru`

```
set_feedthru <blocks> <layers> [on|off]
```

Mark a set of blocks as **feedthru-capable** (routable-through) on a set of trunk
layers. A feedthru-enabled block must be a **busterm of the bundle that the trunk
passes straight through** (a destination/relay it would otherwise stub to but whose
body the spine crosses). When such a block opts in, the `TRUNK_H`/`TRUNK_V` spine is
*split* at the block's two crossed faces — landing a BUSTERM connection on each face,
so the block "connects ≥2 of the bundle's stubs" — and the block's own lower-level
router bridges the gap. `check_topo` accepts this declared relay (it is recorded in
`Topology::feedthru_blocks`); an *undeclared* relay on the same geometry is still
flagged. An *unrelated* block the trunk merely crosses (not a bundle busterm) is a
pass-through, never a feedthru. Straight/I-shape feedthru and a `feedthru_penalty`
ranking knob are later phases.

Feedthru is genuinely **per-(block, layer)** — a block may be routable-through on one
trunk layer but not another — so the command sets a full block×layer grid rather than
two independent axes.

| Argument | Type | Description |
|---|---|---|
| `blocks` | str | Comma-separated block names (`A,B,C`), or `*` / `all` for every block. One token — no spaces. |
| `layers` | str | Comma-separated layer names or ids (`M4,M5` or `4,5`), or `*` / `all` for every layer. One token — no spaces. |
| `on\|off` | flag | Optional. Enable (`on`/`true`/`1`) or disable (`off`/`false`/`0`). Default `on`. |

**Resolution** (most-specific rule wins, so a block-scoped rule beats a layer-scoped
one): `(block, layer) > (block, *) > (*, layer) > (*, *)`. Each call stores explicit
on/off values, so a narrower `off` carves an exception out of a broader `on`.

**Examples:**
```buda
set_feedthru FT *           # block FT, all layers, on
set_feedthru * M4,M5        # all blocks, layers M4 and M5
set_feedthru A,B M4         # blocks A and B, on M4 only
set_feedthru A M5 off       # carve out one (block, layer) pair
set_feedthru * * on         # global default on
```

---

### `set_track_pitch`

```
set_track_pitch <pitch>
```

Declare the inter-bus gap that `run_planner` should reserve in its band
congestion books, and that `run_nuts` will enforce between adjacent bus
segments. Calling this **before** `run_planner` keeps the two stages
consistent (Gap 1): the planner's band-capacity check adds `pitch` to the
effective bus width so it never books a band so full that NUTS cannot fit the
required inter-bus gap beside the bus.

| Argument | Type | Description |
|---|---|---|
| `pitch` | float | Minimum perpendicular gap between the upper edge of one bus and the lower edge of the next, in layout units. Default `1.0`. |

**Behaviour:**
- After this command, subsequent `run_planner` calls reserve an extra `pitch`
  units of capacity per bus in every band they charge.
- `run_nuts` with no explicit argument reuses the stored pitch rather than
  resetting to `1.0`.
- If `run_nuts` is called with an explicit pitch that differs from the stored
  value, a warning is printed advising you to re-run `run_planner` (or call
  `set_track_pitch` before planning) so both stages agree.

**Example:**
```buda
set_track_pitch 2.0    # 2-unit gap between buses
run_planner 5          # plans with 2-unit pitch baked in
run_nuts               # reuses 2.0 automatically — no need to pass it again
```

---
