# BUDA Script Reference

`.buda` scripts are executed line-by-line by the `buda` wrapper which runs `buda_cli.py`. Each line is one
command. Blank lines, lines beginning with `#` and any text after `#` are all ignored.

```
python3 src/buda_cli.py flow/my_design.buda
python3 src/buda_cli.py flow/my_design     # .buda extension inferred
buda flow/my_design     # use wrapper under bin/
```

For the `buda` command line itself — invocation, wrappers, and flags such as
`--no-viz`, `--verbose-conn`, and `--ipc-verbose` — see the
[BUDA CLI Reference](BUDA_CLI.md). This page documents the commands you write
*inside* a script.

---

## Pipeline overview

Commands run in the following order. Later stages depend on earlier ones.

| Stage | Command(s) | Purpose |
|------:|---|---|
| Setup | `def_layer` | Register metal layers |
| Setup | `add_block` | Place floorplan blocks (with optional per-block corner margin) |
| Setup | `corner_margin` | Set global corner margin for all blocks without a per-block override |
| Setup | `set_min_stub_length`, `_dir`, `_layer` | Set minimum stub length globally, per direction, or per layer |
| Setup | `set_feedthru` | Mark a block×layer set as routable-through (opt-in feedthru) |
| Setup | `set_track_pitch` | Declare inter-bus pitch so `run_planner` band reservations match the NUTS solve |
| Setup | `add_net`, `add_bus` | Declare nets / buses in the netlist |
| Setup | `detour_channel` | Set outer-band width for U-shape / UU-shape detour trunks per compass direction |
| 1 | `run_bundler`, `run_hier_bundler` | Group nets into flat or hierarchy-aware buses |
| 1b | `dump_hbundles` | Print a summary of all HBundles (after `run_hier_bundler`) |
| 2 | `generate_topologies`, `generate_hier_topologies` | Enumerate topology candidates for flat or hierarchical bundles |
| 2 | `generate_topologies_for_bundle` | Enumerate topology candidates for a **specific** flat bundle |
| 2 | `generate_topologies_for_hbundle` | Re-run topology generation for a **specific** HBundle by its integer ID |
| 2 setup | `set_prune_dominated` | Opt-in WL-dominance candidate pruning, gated on non-WL routing equivalence (default off) |
| 2 setup | `set_dedup_loci` | Opt-in dedup of nominal-locus candidate variants that share a slide window + connectivity (default off) |
| 2 setup | `set_drop_dangling` | Opt-in handling of candidates with a dangling segment or an unclamped slide window (default off): `clamp` bounds unbounded windows to the design extent, `clamp_drop` also drops truly-dangling candidates, `drop`/`on` drops any such candidate |
| 3 | `set_planner_param` | Tune planner cost coefficients (applied at the next `run_planner`) |
| 3 | `run_planner` | Select topology + assign layers per segment |
| 3b | `select_topology` | Manually pin a bundle's topology candidate (1-based); bundle given by numeric ID or net-name hint (`bus_033`; `id:`/`net:` to disambiguate) |
| 3b | `select_topologies` | Batch pin multiple bundles (IDs, ranges, and/or net-name hints) to specific topologies |
| 3b | `unpin_topology` | Clear a bundle's pin (inverse of `select_topology`); `*` clears all |
| 3 | `run_nuts` | Abstract 1.5-D track placement |
| 4b | `run_nuts_on_layer` | Re-solve one layer after inspection |
| 4c | `run_planner post_nuts` | Reassign stub layers to resolve channel pin conflicts; single NUTS re-run |
| 8 | `def_track_pattern` | Define the repeating POWER/SIGNAL/GROUND track pattern for a layer |
| 8 | `add_grid_override` | Override the track pattern for a specific floorplan region on a layer |
| 8 | `report_overhead` | Compare `def_layer` overhead% against the track pattern; print corrected `def_layer` commands for any mismatch |
| 9 | `run_detailed_nuts` | Snap each bus segment's bits to concrete signal-track positions |
| 9 setup | `set_pair_align_heal` | Opt-in measured-accept pairwise-overlap alignment at `run_detailed_nuts`: re-solve with same-net stub pairs sharing one track window, keeping it only when opens/overlaps do not rise and detailed WL strictly drops (default off) |
| 3↔4/9 | `ripup_reroute` | Feedback-driven rip-up & re-route: read the **actual** NUTS overlaps / DNUTS opens and re-route contending bundles to clear them |
| 3↔4/9 | `negotiate_congestion` | Measured-congestion negotiation: inject the **actual** overlaps/opens as band demand and re-plan the offending bundles unpinned against the corrected prices (the cheaper first pass; `ripup_reroute` finishes the residual) |
| 3↔4/9 | `refine_selection` | Measured selection WL polish (run after the healers): re-rank selections on the placed result, adopting only moves that keep opens/overlaps parity-or-better and strictly lower realized WL |
| Verify | `check_design` | Audit the design at topo, nuts, or dnuts stages: connectivity opens, layer directions, keepout crossings (alias: `check_connectivity`) |
| — | `dump_topologies` | Text dump of per-bundle candidate topologies (inspection) |
| — | `visualize` | Open interactive NUTS result viewer |
| — | `visualize_topologies` | Open topology explorer |
| — | `source` | Include another `.buda` file |
| BDB | `open_bdb`, `import_def_lef`, `import_verilog` | Open / populate the physical design database |
| BDB | `move_comp`, `resize_cell`, `add_comp`, `flip_comp`, `rotate_comp`, `add_cell`, `add_inst`, `add_inst_to_cell`, `add_cell_pin` | Mutate placement and cell/pin definition data in the database |
| BDB | `bdb_net_mode` | Toggle whether netlist is written to BDB database |
| BDB | `add_blocks_from_bdb` | Import floorplan block boundaries at a given hierarchy depth |
| BDB | `derive_busterms` | Extract busterms from hierarchy |

---

## Reference pages

The per-command documentation lives in one page per pipeline stage under
[`script_reference/`](script_reference/):

| Page | Stage | Commands |
|---|---|---|
| [Setup](script_reference/setup.md) | setup | `def_layer` · `add_block` · `add_keepout` · `add_net` · `add_bus` · `corner_margin` · `detour_channel` · `set_min_stub_length[_dir|_layer]` · `set_feedthru` · `set_track_pitch` |
| [Bundler](script_reference/bundling.md) | 1 | `run_bundler` · `run_hier_bundler` · `dump_hbundles` |
| [Topology generator](script_reference/topologies.md) | 2 | `generate_topologies[_for_bundle]` · `generate_more_topologies` · TopoEdit session (`edit_topology` … `edit_commit`) · `generate_hier_topologies` · `generate_topologies_for_hbundle` · `set_prune_dominated` · `set_dedup_loci` · `set_drop_dangling` |
| [Planner](script_reference/planner.md) | 3, 4c | `set_planner_param` · `run_planner` (+ `hier`, `post_nuts`) · `select_topology` · `select_topologies` · `unpin_topology` |
| [Track assignment (NUTS)](script_reference/nuts.md) | 4, 9 | `run_nuts` · `run_nuts_on_layer` · `run_detailed_nuts` · `set_pair_align_heal` · `ripup_reroute` · `negotiate_congestion` · `refine_selection` |
| [Routing grid](script_reference/routing_grid.md) | 8 | `def_track_pattern` · `add_grid_override` · `report_overhead` |
| [Verification & visualisation](script_reference/verify_viz.md) | verify / — | `check_design` · `dump_topologies` · `visualize` · `visualize_topologies` |

Script control (`source`, `exit`, comments), the output-files table, the typical
script skeleton, and the BDB command quick reference stay on this page, below.

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

### `exit`

```
exit [<code>]
```

Stop the run immediately, before the rest of the script (or any including
script) runs — handy for bisecting a flow incrementally. The optional integer
`<code>` becomes the process exit status (default `0`, a clean stop; a
non-integer argument is an error and exits `1`). Any armed BDB writeback is
flushed first, so an `open_bdb … writeback` still persists on the way out.

**Example:**
```
run_planner 5
run_nuts
exit             # stop here to inspect the NUTS result; skip detailed NUTS
run_detailed_nuts
```

---

### Comments

```
# this is a full-line comment
run_bundler # inline comment: everything from `#` to end of line is dropped
def_layer 4 M4 H TOP 0.0 # a trailing note after the command's args
```

Everything from the first **token-starting** `#` (the start of the line, or a
`#` preceded by whitespace) to the end of the line is stripped before the
command is parsed. So a whole line can be a comment, or a command can be
commented partially — `run_bundler # strict` runs `run_bundler`. A `#`
embedded in a token (no preceding whitespace, e.g. inside a path) is **kept**,
so an inline comment cannot silently swallow a real argument.

---

## Output files

| File | Created by | Contents |
|---|---|---|
| `<script>.json` | `visualize_topologies` → Select | Architect-pinned topology selections. Loaded by `run_planner`. |
| `<script>_nuts.log` | `run_nuts` | Per-overlap detail report: segment pairs, span/perp rectangles, area. Re-run sections are appended by `run_nuts_on_layer`. |
| `<script>_flow.log` | Per command | Full detail of every command — planner decisions, NUTS metrics, warnings, and C++ output (routed through `sys.stdout`) — each under a `━━━ <command> ━━━` header with a trailing `[runtime] <command>: <secs>s (…)` line. The terminal shows only a one-line **abstract summary per command** (marker + runtime + a headline) plus a final **Runtime summary** table, so stdout and the log are no longer duplicated. Read the log for post-mortem detail. |

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
generate_topologies_for_bundle sig0
generate_topologies_for_bundle data

# ── Stage 3: global route ────────────────────────────────────
# set_track_pitch ensures the planner reserves the same inter-bus gap
# that run_nuts enforces.  Omit only if the default 1.0 is sufficient.
set_track_pitch 2.0
run_planner 5

# ── Stage 4: abstract track placement ────────────────────────
run_nuts   # reuses set_track_pitch value (2.0) automatically

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
| `open_bdb <path> [writeback]` | Open or create a `.bdb`, or materialize a `*.bdb.sql` text fixture to a temp binary. `writeback` (for `.sql` only) dumps changes back to the `.sql` on `save_bdb`/`exit`/end-of-run. Use before any other BDB command. |
| `save_bdb` | Write the working BDB back to its `*.bdb.sql` source now (after `open_bdb … writeback`). |
| `import_def_lef <def> <lef>` | Import placements from DEF + cell sizes from LEF. Clears all tables. |
| `import_verilog <v>` | Elaborate hierarchy from Verilog; preserves coordinates from a prior `import_def_lef`. |
| `bdb_net_mode on\|off` | Toggle whether nets/buses are written directly to BDB database. |
| `add_blocks_from_bdb <depth> [deepest\|skip\|error]` | Populate the BUDA floorplan from BDB instances at hierarchy depth `N`. Blocks that have at least one loaded BDB descendant are automatically marked as containers (hierarchy envelopes, transparent to LOW layers). |
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
