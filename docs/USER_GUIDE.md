# BUDA User Guide

Welcome to BUDA! This guide is designed to help you get started with interconnect planning. BUDA automates the grouping of nets into buses (bundling), generates physical routing candidates (topologies), and resolves congestion through planning and track assignment.

---

## 1. The Standard Execution Flow

To successfully run a BUDA script, you should follow this sequence:

1.  **Environment Setup**: Define your metal layers and track patterns.
2.  **Floorplan & Netlist**: Add blocks and the connections between them.
3.  **Bundling**: Group individual nets into manageable bundles.
4.  **Topology Generation**: Enumerate possible routing shapes for each bundle.
5.  **Global Planning**: Choose the best topology for each bundle to minimize congestion.
6.  **Track Assignment (NUTS)**: Solve for the specific track positions of every wire.
7.  **(Optional) Feedback passes**: If NUTS or Detailed NUTS still report overlaps/opens, clear them with the two feedback commands — `negotiate_congestion` (the cheap first pass: reprice the contended bands and re-plan) and then `ripup_reroute` (re-route the stubborn residual). For best results run them after **both** `run_nuts` and `run_detailed_nuts` (see below).

---

## 2. Necessary Conditions (Prerequisites)

BUDA commands depend on each other. If you skip a setup step, the engine may use incorrect defaults or fail.

### Input files: `require_file`
*   **What**: `require_file <path> [<path> ...] [hint <text>]` declares the files your flow needs before it needs them. If any is missing — or is a directory of that name rather than a file — the run stops on that line (`BUDA-1905`, exit 1), naming every bad path and printing your `hint`.
*   **Why?**: An import command fails perfectly well on a file it cannot open — but it can only tell you the path. Where the file *comes from* is something only your flow knows, and that is usually what you need to be told. Declaring inputs at the top also means a run that cannot succeed stops on line one instead of partway through the setup.
*   **When to use it**: any input not checked in beside the script — a fetched or generated netlist, a site LEF, a previous stage's output. Paths resolve against the script's own directory, exactly like `source` and `import_*`. A path with **spaces** goes in quotes (`require_file "my inputs/top.v"`) — see [Paths, and paths with spaces](BUDA_SCRIPT_REFERENCE.md#paths-and-paths-with-spaces) for which commands need the quotes and which do not.
    ```buda
    require_file ariane.v fakeram45_256x16.lef hint Fetch them first:  python3 flow/ariane133/fetch.py
    open_bdb ariane133.bdb
    import_def_lef ../../demo/ariane/ariane.def fakeram45_256x16.lef
    import_verilog ariane.v
    ```

### `generate_topologies`
*   **Prerequisite**: Use `def_layer` first.
*   **Why?**: The generator needs to know which layers are Horizontal (H) and which are Vertical (V). If no layers are defined, it will default to Layer 4 (H) and Layer 5 (V).

### `run_nuts`
*   **Prerequisite**: You must have run `run_planner` (or manually pinned topologies).
*   **Why?**: NUTS assigns tracks to the *selected* topologies. Without a plan, there is nothing to assign.

### `run_detailed_nuts`
*   **Prerequisite 1**: Run `run_nuts` first.
*   **Prerequisite 2**: Use `def_track_pattern` for every used layer.
*   **Why?**: Detailed NUTS performs bit-level placement. It needs to know the exact width and spacing of tracks (the "pattern") to ensure the layout is physically legal.

### `ripup_reroute`
*   **Prerequisite**: Run `run_nuts` (and, for the open-clearing pass, `run_detailed_nuts`) first.
*   **Why?**: This is an *optional* feedback pass. The planner sometimes commits a bundle thinking a band is fine (`overflow=0`) when NUTS or Detailed NUTS later finds it contended — and simply re-running `run_planner` produces the same plan because the planner cannot see the real overlap. `ripup_reroute` reads the **actual** result, re-routes a contending bundle to an alternate topology, re-runs the pipeline, and keeps the move only if it lowers the overlap/open count.
*   **Which stage?** It auto-detects: run it after `run_nuts` to drive down NUTS **overlaps**, or after `run_detailed_nuts` to drive down DetailedNUTS **opens** (unplaced bits). It is a no-op when there is nothing left to fix, and works in both the flat and hierarchical flows.
*   **Best practice — run it in both places.** The two passes clear *different* causes of unplaced bits, so chaining them clears the most:
    ```buda
    run_nuts
    ripup_reroute            # stage a: clear abstract NUTS overlaps
    run_detailed_nuts
    ripup_reroute            # stage b: clear the residual capacity-driven opens
    ```
    Stage a clears the opens that come from abstract track contention (cheaply — fewer, larger wins), giving DetailedNUTS a much better starting point; stage b then re-routes the bits that still don't fit because a band is short of *signal tracks*. On a congested design this two-pass order reaches far fewer opens than stage b alone.
*   **Tip**: Optionally cap the effort with `ripup_reroute <max_iter>` (default 10 iterations, one re-route committed per iteration). On a large design the default may stop while still improving — it says so and you can re-run it or pass a larger `max_iter` to continue.

### `negotiate_congestion`
*   **Prerequisite**: Same as `ripup_reroute` — run `run_nuts` (and, for the open-clearing pass, `run_detailed_nuts`) first.
*   **Why?**: It clears the same overlaps/opens, but it is usually the **cheaper first pass**. Rather than trying a bundle's alternate topologies one at a time (as `ripup_reroute` does), it injects the *actual* failures back into the planner as extra demand on the exact congested bands, then re-plans the offending bundles against those corrected prices — so one planner pass moves many bundles off the contention at once. Stubborn hot-spots get progressively more expensive (PathFinder-style), and each round is kept only if it strictly improves.
*   **Which stage?** Auto-detected exactly like `ripup_reroute` (after `run_nuts` → NUTS overlaps; after `run_detailed_nuts` → DetailedNUTS opens); no-op when there is nothing left to fix.
*   **Best practice — negotiate first, then rip-up.** Negotiation resolves the broad, price-visible contention cheaply; `ripup_reroute` mops up the residual by trying alternate candidates:
    ```buda
    run_nuts
    negotiate_congestion     # reprice the contended bands, replan in one pass
    ripup_reroute            # finish the residual NUTS overlaps
    run_detailed_nuts
    negotiate_congestion     # stage b: reprice the capacity-short bands
    ripup_reroute            # finish the residual DetailedNUTS opens
    ```
*   **Tip**: Cap the effort with `negotiate_congestion <max_iter>` (default 5 rounds).

---

## 3. Sidecar Selections (`.json` files)

When you run BUDA, it automatically looks for a "sidecar" JSON file with the same name as your script (e.g., `quickstart.json` for `quickstart.buda`).

*   **Persistence**: This file stores manual topology selections you've made in the visualizer.
*   **Stability**: If you run `run_planner`, it will prioritize the "pinned" selections found in this file over its own calculated defaults. 
*   **Reproducibility**: You can share your design by providing both the `.buda` script and the `.json` sidecar.

If the engine finds a conflict (e.g., you changed the floorplan and the pinned topology no longer exists), it will print a warning and fall back to the best available candidate.

For iterating on a design *across sessions* — build once with a checkpoint,
then resume at the planner (or just re-inspect the routed result) without
rebuilding — see [Build & Resume Sessions](BUILD_RESUME.md): `btcl -b
<flow>.buda` / `btcl -r <flow>.buda`, with a flat and a hier demo vehicle
(`demo/resume_flat.buda`, `demo/resume_hier.buda`).

---

## 4. Example Script: `quickstart.buda`

Save this as a `.buda` file and run it from the repository root using `buda your_file.buda`.

```python
# ── Step 1: Define Technology ──
# Layer ID, Name, Direction (H/V), Type (TOP/LOW), Capacity per unit
def_layer 4 M4 H TOP 1.0
def_layer 5 M5 V TOP 1.0

# Track patterns are REQUIRED for Detailed NUTS.
# Format: <layer_id> <origin> [<type> <width> <spacing>] ...
def_track_pattern 4 0.0  (SIGNAL 1.0 1.0)x4  POWER 2.0 1.0  GROUND 2.0 1.0  CLK 1.5 1.0
def_track_pattern 5 0.0  (SIGNAL 1.0 1.0)x5  POWER 2.0 1.0  GROUND 2.0 1.0  CLK 1.5 1.0

# ── Step 2: Define Floorplan ──
add_block CPU  100 100 300 300
add_block MEM  700 500 900 700
add_block GPU  400 800 600 1000

# Optional: Add margins to keep wires away from block corners
corner_margin dx 20 dy 20

# ── Step 3: Define Netlist ──
# Single control net
add_net cntr CPU.tx MEM.lx

# 8-bit bus: MEM -> GPU
add_bus mem_to_gpu[8] MEM.tx GPU.rx

# 3-pin bus:
add_bus bus[16]  MEM.io CPU.io,GPU.io

# ── Step 4: Execute Planning ──
run_bundler strict
generate_topologies

# run_planner will load any existing selections from quickstart.json
run_planner 5

run_nuts
negotiate_congestion     # Optional: cheap first pass — reprice contended bands
ripup_reroute            # Optional: finish the residual NUTS overlaps
run_detailed_nuts
negotiate_congestion     # Optional: reprice capacity-short bands
ripup_reroute            # Optional: finish residual DetailedNUTS opens

# ── Step 5: Visualize ──
# Use the GUI to click on bundles and choose different topologies.
# Your choices will be saved to the sidecar JSON file automatically.
visualize
```

---

## 5. Key Concepts for Novices

### BUSTERM Mode
By default, BUDA connects wires to the **faces** of your blocks (Busterm mode). This is more realistic than connecting to the exact center of a block.

### Minimum Stub Length
To ensure your layout isn't too "pinched," you can set a minimum stub length:
`set_min_stub_length 25`
This ensures that every wire segment connecting to a block has a healthy physical length.

### Keepout Zones
You can prohibit routing in specific rectangular regions for one or more layers:
`add_keepout 300 100 500 300 M4`
*   **Planning**: `run_planner` will automatically choose topologies that detour around these zones.
*   **Detailed Routing**: `run_detailed_nuts` will skip any tracks that pass through a keepout zone for the assigned layer.
*   **Visualization**: Keepout zones appear as red hatched rectangles in the visualizer.

### Wide and Shielded Buses (Non-Default Rules)
A clock or sensitive bus often needs more than a default-width wire. Declare a
**non-default rule** (NDR) once and attach it to nets by name prefix, *before*
the bundler runs:

```python
def_ndr clk2x width x2 spacing x2 shield bus net GND
set_ndr clk_ clk2x
```

Every `clk_*` net now routes 2 slots wide, keeps a guard slot of clearance, and
the bus is flanked by shield wires carrying the `GND` label. What matters for a
novice:

*   **Declare before bundling.** Rules attach at `run_bundler` /
    `run_hier_bundler`; a bundle mixing rules is split into rule-uniform parts,
    reported loudly.
*   **Multiplier or absolute.** `width x2` is a *multiplier* — two signal slots
    on every layer, so one rule ports across a stack whose layers have
    different pitches. `width 3` (layout units) or `width 0.2um` is *absolute*
    — one physical width, whose slot cost is resolved per layer. Reach for the
    absolute form when the technology gives you a number in microns; reach for
    the multiplier when you want one rule to mean the same thing everywhere.
    An absolute value rounds UP to the next whole slot, and needs every layer
    it can reach to have a `def_track_pattern` already declared — it is a hard
    error otherwise, naming the multiplier form as the order-independent way
    out.
*   **An emitted shield is labeled metal until you bond it.** It is a real
    routed wire with the shield net's identity and it reserves the track, but by
    default nothing straps it to the power grid — so do not rely on it for
    electrical shielding as-is. Two tokens fix that: `credit`, so an **existing**
    power rail (already grid metal) serves as the shield, or `bond`, so BUDA
    straps each emitted shield to the grid where a matching rail crosses it —
    `bond stride <N>` thins that to every Nth crossing when strapping all of
    them is more vias than the grid wants, always anchoring both ends.
    With `bond` on, `check_design` raises `NDR_BOND` for any shield it could not
    strap — that means no matching rail crosses it, a grid problem rather than a
    routing one.
*   **The planner prices the cost.** Extra width, guards and shields are charged
    as track demand while layers are chosen, so a region that cannot afford an
    NDR bus in *aggregate* shows up as planner overflow at planning time rather
    than only as a failed route at the end. This is aggregate capacity pricing,
    not a proof that a seat exists: a bus can still strand at
    `run_detailed_nuts` if the actual occupancy leaves no unreserved run wide
    enough, which it reports as a warning plus unplaced bits.
*   **Nothing degrades silently.** A **width** rule needing more contiguous
    signal slots than a governed layer's pattern can ever offer is a hard error
    at `run_detailed_nuts`, naming the arithmetic (shield-only and spacing-only
    rules are not width-checked this way — they surface as the stranding above).
    `check_design` adds `NDR_WIDTH`, `NDR_SPACING`, `NDR_SHIELD` (and, with
    `bond`, `NDR_BOND`) checks, and
    `report_wirelength` counts shield metal on its own line.
*   **Inspect it** with `dump_ndr`, which prints each governed bundle's slot
    demand and layout (`SBbGBbGBbGBbS` — shield, bit, guard, …).

Start from a worked vehicle: `flow/ndr_demo.buda` (smallest),
`flow/ndr_shield_flat.buda` (every shield mode), `flow/ndr_shield_hier.buda`
(hierarchical), `flow/ndr_bottom_up.buda` (bottom-up templates), `flow/ndr_bond.buda`
(shield bonding). Full command
reference: [script_reference/ndr.md](script_reference/ndr.md).

### Comparing Topology Candidates by Cost
The topology explorer normally steps through a bundle's candidates in
**increasing wirelength** order. To see them in the order the **planner** cares
about — increasing *cost* — add the `debug` flag:

```python
visualize_topologies bus_033 debug
```

The same view is reachable from the main window: add `debug` to `visualize`,
then open the explorer with the `v` key / "View Topologies" button.

Now `a`/`d` step in increasing planner cost, and the title shows each
candidate's cost with its breakdown (`cost=<total> = seg <…> + wl <…>`) and its
cost-rank. After `run_planner` the cost is the **real** cost the planner would
charge that candidate against the actual congestion from the other bundles, so
you can see *why* it picked the one it did — a shorter candidate that loses to a
slightly longer one is usually paying a congestion penalty. Select a segment
with `j`/`k` and the info box adds that segment's congestion cost. Before
planning there is no congestion yet, so the cost falls back to plain
wirelength. The candidate and family IDs are unchanged — only the stepping order
does — so any pin you make still refers to the same candidate. (Details:
[Script Reference → Verification & visualisation](script_reference/verify_viz.md#visualize_topologies).)

### Hand-Editing a Topology
When none of the generated candidates is quite what you want, you can draw your
own route and drop it into the pool — every edit is checked immediately, so you
cannot silently break connectivity:

```python
edit_topology 1 new        # open an empty working topology for bundle 1
edit_add_trunk V 450       # vertical trunk on the Hanan column x=450, full span
edit_add_stub CPU 0        # stub block CPU to segment 0
edit_add_stub MEM 0
edit_commit pin            # add it as a USER candidate and pin it
run_planner 5              # the planner honors your pin; run_nuts routes it
```

Each command prints a verdict (opens, pinches, disconnected pieces). The same
editing works interactively in the topology explorer — press `e` on a candidate
(see [Key Bindings → TopoEdit mode](KEY_BINDINGS.md#topoedit-mode-expert-hand-editing)).
Hand-committed candidates are protected: they survive regeneration and
re-persist, and your pin follows them by content identity. Full command
documentation: [Script Reference → Topology generator](script_reference/topologies.md).

---

## 6. Advanced: Hierarchical BDB / HBundle Flow

The base flow is flat: you describe blocks directly with `add_block`, connect
them with `add_net` or `add_bus`, then run:

```python
run_bundler strict
generate_topologies
run_planner 5
run_nuts
run_detailed_nuts
```

This is the right starting point when every routing endpoint is already a
top-level floorplan block. It is simple, direct, and easy to debug.

The HBundle flow is hierarchical: you first populate BDB with cells, instances,
and nets, then let BUDA derive routing blocks from hierarchy depth. This lets
the same flow distinguish top-level inter-block routes from repeated local
routes inside cell templates.

### Flat Flow vs. HBundle Flow

| Topic | Base / Flat Flow | BDB / HBundle Flow |
|---|---|---|
| Floorplan source | `add_block` commands | BDB cells and instances, then `add_blocks_from_bdb` |
| Net source | `add_net` / `add_bus` in the flat session | `add_net` / `add_bus` with `bdb_net_mode on`, or imported netlist data |
| Bundling command | `run_bundler strict` | `run_hier_bundler depth N` |
| Topology command | `generate_topologies` | `generate_hier_topologies` |
| Planner command | `run_planner 5` | `run_planner hier 5` |
| Best for | Small flat studies and quick experiments | Designs with hierarchy, repeated cells, or local-vs-global routing tradeoffs |
| Main limitation | Loses hierarchy context | Requires BDB setup before planning |

> **Pin directions:** When `bdb_net_mode on` is active, `add_net`/`add_bus`
> automatically tag the first pin as `OUTPUT` (driver) and the remaining pins as
> `INPUT` (receivers).  When direction is not known — for example after
> `import_verilog`, or for clock nets where there is no designated driver — use
> the trailing `unknown` keyword: `add_net clk u_src.clk u_dst.clk unknown`.
> See [BDB Reference → Pin Directions](BDB_REFERENCE.md#pin-directions) for the
> complete direction model, including how `run_hier_bundler` applies the
> positional fallback for `UNKNOWN`-direction pins.

### Complete HBundle Example

Save this as `hb_quickstart.buda` and run it from the repository root:

```sh
buda hb_quickstart.buda
```

```python
# -- Technology --------------------------------------------------------------
# Layer ID, Name, Direction (H/V), Type (TOP/LOW), Capacity per unit
def_layer 4 M4 H TOP 1.0
def_layer 5 M5 V TOP 1.0

# Track patterns are required by run_detailed_nuts.
def_track_pattern 4 0.0  (SIGNAL 1.0 1.0)x3  POWER 2.0 1.0  (SIGNAL 1.0 1.0)x3  GROUND 2.0 1.0
def_track_pattern 5 0.0  (SIGNAL 1.0 1.0)x3  POWER 2.0 1.0  (SIGNAL 1.0 1.0)x3  GROUND 2.0 1.0

corner_margin dx 5 dy 5
set_min_stub_length 2

# -- BDB: cell templates -----------------------------------------------------
open_bdb :memory:

add_cell src_cell   160 160
add_cell proc_cell  360 160
add_cell snk_cell   160 160
add_cell leaf_cell   80  80

# proc_cell contains two repeated leaf-level routing endpoints.
add_inst_to_cell proc_cell p0 leaf_cell  40 40
add_inst_to_cell proc_cell p1 leaf_cell 200 40

# Top-level placement.
add_inst src_i  src_cell  -   50 50
add_inst proc_i proc_cell -  280 50
add_inst snk_i  snk_cell  -  760 50

# Create busterms for depth 0 and depth 1 so hierarchy endpoints can route.
derive_busterms 1

# Import BDB instances into the BUDA floorplan.
# Depth 0 gives top-level blocks: src_i, proc_i, snk_i.
# Depth 1 gives child blocks such as proc_i/p0 and proc_i/p1.
add_blocks_from_bdb 0
add_blocks_from_bdb 1 skip

# -- BDB netlist -------------------------------------------------------------
# With bdb_net_mode on, buses are written into BDB for run_hier_bundler.
bdb_net_mode on

# Cross-block top-level traffic.
add_bus s2p[8] src_i.out     proc_i/p0.in
add_bus p2s[8] proc_i/p1.out snk_i.in

# Local traffic inside proc_cell. The HBundle flow keeps this as a depth-1
# routing problem instead of flattening it into only top-level traffic.
add_bus p0_p1[8] proc_i/p0.out proc_i/p1.in

# -- HBundle planning pipeline ----------------------------------------------
run_hier_bundler depth 1
dump_hbundles

generate_hier_topologies
run_planner hier 5

run_nuts
check_design nuts

run_detailed_nuts
check_design dnuts

visualize
```

In this example, `s2p` and `p2s` behave like top-level global routes, while
`p0_p1` is recognized as local traffic inside `proc_cell`. The hierarchy-aware
planner can therefore generate and plan candidates in the correct routing
context instead of treating every endpoint as only a flat top-level block.

For larger designs, the usual top-down workflow is:

1. Import or create the hierarchy in BDB.
2. Place or resize major cells enough to define a rough floorplan.
3. Use `derive_busterms` and `add_blocks_from_bdb` for the hierarchy depths you
   want to study.
4. Run `run_hier_bundler`, `generate_hier_topologies`, and `run_planner hier`.
5. Inspect congestion and topology choices, refine the floorplan, and rerun.

### Bottom-Up: Route a Repeated Cell Once

The flow above is **top-down**: every instance of a repeated cell is planned and
routed separately, so a design with 40 copies of one cell solves the same local
problem 40 times — and can solve it 40 slightly different ways.

**Bottom-up** planning inverts that for cells you nominate. The cell's own
interconnect is planned and routed **once** on a reference instance, and that
result is copied verbatim to every other instance. The copies then act as
keepouts for higher levels, which route around them. Two things follow: the
instances are guaranteed identical (a real requirement for repeated blocks), and
the work is done once instead of N times.

It is **opt-in** — the default hier flow marks nothing, so your results do not
change unless you ask for this:

```buda
def_track_pattern 5 …          # patterns FIRST: alignment needs the pitches
set_bottom_up proc_cell        # nominate the cell ('*' marks every eligible one)
align_bottom_up                # nudge its instances onto a common track phase
derive_busterms 1              # only now derive busterms / load blocks
add_blocks_from_bdb 0
…
run_planner hier signal_tracks
run_nuts
check_template_tracks                            # the uniformity gate (stops on mismatch)
run_detailed_nuts
```

Three commands, in this order, and the order matters:

*   **`set_bottom_up <cell>`** nominates the cell. Its instances must be
    congruent — any of the 8 orientations works, and the 90°-rotated family is
    split into its own class solved separately. An instance that matches under no
    orientation is refused loudly rather than copied wrongly.
*   **`align_bottom_up`** nudges the instances onto a shared track phase, with
    minimal total movement (usually the majority does not move at all). Copied
    routing lands on real signal tracks only if the instances agree on where the
    tracks are. Run it **after** `def_track_pattern` and `set_bottom_up`, but
    **before** `derive_busterms` / `add_blocks_from_bdb` — it moves cells, and
    those commands snapshot placement.
*   **`check_template_tracks`** is the gate that proves it worked: it compares
    the signal tracks each instance actually sees. The default `on_mismatch
    stop` refuses to copy and hands you the mismatch report — keep it, because
    it is what makes the identical-instances promise above hold: if alignment
    did not work, you want to know and fix the placement, not route on. Only if
    you *accept* divergent instances (some designs do, when one stray instance
    is not worth re-placing for) switch to `on_mismatch independent`, which
    copies the aligned instances and solves the misaligned ones individually —
    the copies stay uniform, the outliers do not. `run_detailed_nuts` runs the
    check implicitly if you skip it, but then you do not get to choose.

**If a cell should keep its routing off the top-level layers**, cap it:
`set_cell_layer_cap proc_cell M3` restricts the cell's own interconnect to the
band up to M3, leaving the upper layers for the levels above. `reserve_top_layers
2` expresses the common intent — "the top level gets the top two layers" —
without hard-coding layer names against a particular stack.

Full command reference (arguments, edge cases, persistence):
[BDB Reference](BDB_REFERENCE.md#set_bottom_up). Worked vehicles:
`flow/rnr/mix2_fast_bottomup.buda`, its capped and shared variants, and
`flow/chip/chip_bottomup.buda` at chip scale.

## 7. Getting Help
*   Check `docs/BUDA_SCRIPT_REFERENCE.md` for a full list of commands.
*   Use `visualize` at different stages of your script to see what BUDA is doing!
