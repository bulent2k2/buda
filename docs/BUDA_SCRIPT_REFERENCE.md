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

## Reference pages

The per-command documentation lives in one page per pipeline stage under
[`script_reference/`](script_reference/):

| Page | Stage | Commands |
|---|---|---|
| [Setup](script_reference/setup.md) | setup | `def_layer` · `add_block` · `add_keepout` · `add_net` · `add_bus` · `corner_margin` · `detour_channel` · `set_min_stub_length[_dir\|_layer]` · `set_feedthru` · `set_track_pitch` · `set_unit_check` · `import_lef_tech` |
| [Bundler](script_reference/bundling.md) | 1 | `run_bundler` · `run_hier_bundler` · `dump_hbundles` |
| [Topology generator](script_reference/topologies.md) | 2 | `generate_topologies[_for_bundle]` · `generate_more_topologies` · TopoEdit session (`edit_topology` … `edit_commit`) · `generate_hier_topologies` · `generate_topologies_for_hbundle` · `set_prune_dominated` · `set_dedup_loci` · `set_drop_dangling` · `set_trim_mst_legs` · `set_trim_trunk_stubs` |
| [Planner](script_reference/planner.md) | 3, 4c | `set_planner_param` · `run_planner` (+ `hier`, `post_nuts`) · `select_topology` · `select_topologies` · `unpin_topology` · `dump_pins` |
| [Track assignment (NUTS)](script_reference/nuts.md) | 4, 9 | `run_nuts` · `run_nuts_on_layer` · `run_detailed_nuts` · `set_pair_align_heal` · `set_placed_endpoints` · `ripup_reroute` · `negotiate_congestion` · `refine_selection` |
| [Routing grid](script_reference/routing_grid.md) | 8 | `def_track_pattern` · `add_grid_override` · `report_overhead` |
| [Non-default rules (NDR)](script_reference/ndr.md) | setup | `def_ndr` · `set_ndr` · `dump_ndr` — per-net width / spacing / shielding, with the demand model and the worked vehicles |
| [Verification & visualisation](script_reference/verify_viz.md) | verify / — | `check_design` · `dump_topologies` · `visualize` · `visualize_topologies` · `emit_guides` · `export_def_blockages` · `dump_messages` |

Script control (paths and quoting, `source`, `require_file`, `exit`, comments), the output-files table, the typical
script skeleton, and the BDB command quick reference stay on this page, below —
after the pipeline overview that follows.

New to pinning and hand-editing topologies? Start with
[Customizing Topologies](CUSTOM_TOPOLOGIES_GUIDE.md) — a beginner's how-to for
`select_topology` / group pins / the TopoEdit session, with the best-known
methods, a worked example on `demo/custom_topo.buda` (through `btcl -b` /
`btcl -r`), and the pitfalls (1-based candidates vs 0-based segments,
`edit_commit pin`, persistence across sessions).

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
| Setup | `import_lef_tech` | Build the layer stack + track patterns from a LEF technology file; an explicit `def_layer`/`def_track_pattern` always outranks it |
| Setup | `set_unit_check` | Unit-plausibility guard: stop (default), warn, or ignore when the blocks and the track patterns look like different scales |
| Setup | `add_net`, `add_bus` | Declare nets / buses in the netlist |
| Setup | `def_ndr`, `set_ndr` | Declare a non-default rule (width / spacing / shielding) and attach it to nets by name prefix — before the bundler runs |
| Setup | `dump_ndr` | Print declared rules, their attachment scopes, and each governed bundle's slot demand + layout |
| Setup | `detour_channel` | Set outer-band width for U-shape / UU-shape detour trunks per compass direction |
| 1 | `run_bundler`, `run_hier_bundler` | Group nets into flat or hierarchy-aware buses |
| 1b | `dump_hbundles` | Print a summary of all HBundles (after `run_hier_bundler`) |
| 2 | `generate_topologies`, `generate_hier_topologies` | Enumerate topology candidates for flat or hierarchical bundles |
| 2 | `generate_topologies_for_bundle` | Enumerate topology candidates for a **specific** flat bundle |
| 2 | `generate_topologies_for_hbundle` | Re-run topology generation for a **specific** HBundle by its integer ID |
| 2 setup | `set_prune_dominated` | Opt-in WL-dominance candidate pruning, gated on non-WL routing equivalence (default off) |
| 2 setup | `set_dedup_loci` | Opt-in dedup of nominal-locus candidate variants that share a slide window + connectivity (default off) |
| 2 setup | `set_drop_dangling` | Opt-in handling of candidates with a dangling segment or an unclamped slide window (default off): `clamp` bounds unbounded windows to the design extent, `clamp_drop` also drops truly-dangling candidates, `drop`/`on` drops any such candidate |
| 2 setup | `set_trim_mst_legs` | Opt-in shared-leg trim for MST candidates (default off): cut the duplicated overlap off two legs meeting one block along the same axis — at their starts or (the mirror) at their ends. Opt-in because candidates are WL-sorted, so the cut re-sorts the pool and moves selection far beyond the trimmed bundle; renumbers indices |
| 2 setup | `set_trim_trunk_stubs` | Opt-in redundant-stub suppression on the H trunk path (default off): a stub lying entirely inside a farther block's stub off the same spine point adds no coverage. NOT a new algorithm — it enables the pass `add_trunk` already runs, which only `add_trunk_v` was ever allowed to (the H/V unification gated it off to keep H byte-identical), so every redundant pair in the corpus is a V stub off an H spine. Opt-in because dropping a stub re-sorts the WL-ordered pool; renumbers indices |
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
| 9 setup | `set_placed_endpoints` | Decide a segment's endpoint connections from the PLACED geometry instead of the nominal label, so a junction NUTS contracted a span onto snaps each bit to its own via instead of holding every bit at the shared abstract end (default off; promotes only) |
| 9 setup | `set_pair_align_heal` | Opt-in measured-accept pairwise-overlap alignment at `run_detailed_nuts`: re-solve with same-net stub pairs sharing one track window, keeping it only when opens/overlaps do not rise and detailed WL strictly drops (default off) |
| 3↔4/9 | `ripup_reroute` | Feedback-driven rip-up & re-route: read the **actual** NUTS overlaps / DNUTS opens and re-route contending bundles to clear them |
| 3↔4/9 | `negotiate_congestion` | Measured-congestion negotiation: inject the **actual** overlaps/opens as band demand and re-plan the offending bundles unpinned against the corrected prices (the cheaper first pass; `ripup_reroute` finishes the residual) |
| 3↔4/9 | `refine_selection` | Measured selection WL polish (run after the healers): re-rank selections on the placed result, adopting only moves that keep opens/overlaps parity-or-better and strictly lower realized WL |
| Verify | `check_design` | Audit the design at topo, nuts, or dnuts stages: connectivity opens, layer directions, keepout crossings (alias: `check_connectivity`) |
| — | `dump_topologies` | Text dump of per-bundle candidate topologies (inspection) |
| — | `dump_pins` | The pin inventory: one line per pinned bundle — 1-based candidate, type, forced layers, bottom-up copies marked (what the `btcl` prompt's `pins` verb runs and a `-r` resume prints after `RESUMED`) |
| — | `visualize` | Open interactive NUTS result viewer |
| — | `visualize_topologies` | Open topology explorer |
| — | `source` | Include another `.buda` file |
| Setup | `require_file` | Declare the input files this flow needs, with the remedy — a missing one stops the run at once instead of partway through |
| BDB | `open_bdb`, `import_def_lef`, `import_verilog` | Open / populate the physical design database |
| BDB | `move_comp`, `resize_cell`, `add_comp`, `flip_comp`, `rotate_comp`, `add_cell`, `add_inst`, `add_inst_to_cell`, `add_cell_pin` | Mutate placement and cell/pin definition data in the database |
| BDB | `bdb_net_mode` | Toggle whether netlist is written to BDB database |
| BDB | `add_blocks_from_bdb` | Import floorplan block boundaries at a given hierarchy depth |
| BDB | `derive_busterms`, `refine_busterms` | Extract busterms from hierarchy |
| Hier | `set_bottom_up`, `align_bottom_up` | Mark a cell to be planned/routed ONCE and copied to every instance, and nudge its instances onto a common track phase first |
| Hier | `check_template_tracks` | Bottom-up uniformity gate: verify every instance sees the same tracks before the copies are made (after `run_nuts`, before `run_detailed_nuts`) |
| Hier | `set_cell_layer_cap`, `set_cell_layer_share` | Per-cell layer band, and a fractional per-layer share inside or above it |
| Hier | `set_layer_caps_by_depth`, `reserve_top_layers` | Bulk layer bands: by hierarchy depth, or by reserving the stack's top N layers for the top level |
| Hier | `load_pipeline` | Resume a routing pipeline (bundles, topologies, plan, routing) from the open BDB |

---

## Script control

### Paths, and paths with spaces

A `.buda` line is split on whitespace, so a path containing a space needs a
rule. There are two, and which one applies is decided by the **shape of the
command's argument list** — not by the command's name:

| Argument shape | Commands | How to spell a spaced path |
|---|---|---|
| Exactly one path, nothing after it | `source`, `import_verilog`, `save_bdb`, `def_gds_layer file <p>` | **bare** — the rest of the line *is* the path |
| A path followed by options | `open_bdb`, `import_gds`, `export_gds`, `import_lef_tech`, `emit_guides` (and its `tcl` / `csv` values), `export_def_blockages` | **quoted** |
| Several adjacent paths | `import_def_lef`, `require_file` | **quoted** |

```buda
source my designs/tracks.buda                      # bare: nothing follows it
open_bdb "my designs/ck.bdb.sql" writeback         # quoted: `writeback` must stay an option
import_def_lef "rev 2/top.def" "rev 2/top.lef"     # quoted: nothing else marks the boundary
```

**Why the second and third shapes need the quote.** Consider
`export_gds out.gds bogus_option 1`: no token is a known keyword, so a
rest-of-line rule would read the whole thing as a filename — the
unknown-option error disappears and a typo silently writes a file with a
garbage name. Nothing in that line distinguishes it from a genuinely spaced
path, so the engine refuses to guess and the author resolves it with a
quote. Between two adjacent paths there is not even a keyword to appeal to.

A quote is honoured only where a **token begins**, which is what makes this
identical to a plain whitespace split for every line that does not use one —
an apostrophe inside a word (`a's_block`) is an ordinary character, and an
unterminated quote is too. Every existing flow is therefore untouched:
measured across all checked-in `.buda` files, 222k lines, nothing parses
differently.

A quote also escapes a `#` — see [Comments](#comments).

### `source`

```
source <path>
```

Execute the contents of another `.buda` script file inline, as if its
commands had been typed at the current point. Comments and blank lines in
the included file are skipped.

`source` takes exactly one path and nothing after it, so the whole rest of
the line is the path — a space in it is part of the filename, and no quotes
are needed (`source my designs/tracks.buda`). Quoting works too — and is
needed for the two things the comment rule and the trim take first: a `#` at
a token boundary (`source "my #2/flow.buda"`, else the line is cut and `my`
is the path) and leading or trailing whitespace inside the filename. It matters
most for the one path a user does not choose: `bin/buda "my designs/x.buda"`
reaches the engine as a `source` line, so a checkout under `~/My Designs/`
would otherwise not run at all.

The script path is resolved against the **including script's directory**
(for the outermost `source` — the one the CLI itself issues — that falls
back to the CWD, since there is no enclosing script yet). This is the one
path rule every command shares: `open_bdb`, `save_bdb`, the `import_*` and
`export_*` commands, and `emit_guides` all resolve a relative path against
the enclosing script's directory too, so a `.buda` script is a
location-independent artifact — it reads and writes the same files no matter
where it is run from. Sessions with no script (interactive, the Tcl front
end, the Python API) resolve against the CWD.
Only the outermost script's path is used for sidecar (`.json`) and log
(`.log`) file naming.

**Example:**
```
source ../common/base_layers.buda
source my_floorplan.buda
run_bundler strict
```

### `require_file`

```
require_file <path> [<path> ...] [hint <text ...>]
```

Declare the input files this script needs. Every path is checked; if any is
missing — or is present but is not a regular file, a directory of the same
name being the case that bites — the run **stops immediately** with
`BUDA-1905` (FATAL, exit 1), naming *every* bad path with which of the two
it is, and printing `hint` verbatim. When all are present the command is
silent and the flow continues. A malformed declaration (no path at all)
stops the run too: this command's job is to have checked something, and one
that named nothing checked nothing.

Paths resolve against the **script's own directory**, like `source` and every
`import_*` command, so a required path names the same file the import that
reads it will open. The message prints both the path as you wrote it and the
absolute path it resolved to.

This command takes a **list** of paths, so a spaced one is
[quoted](#paths-and-paths-with-spaces) — between two paths there is nothing
else that can say where one ends:

```buda
require_file "my inputs/top.v" "my inputs/macro.lef" hint run fetch.py first
```

Everything after the `hint` keyword is the remedy text, so several files can
share one hint. It is read as words and re-joined with single spaces (the
`flow/ariane133` hint's double space already came out single), and a quoted
run arrives whole — which is what lets the Tcl front end pass a hint as one
argument (`buda::require_file top.v hint {run fetch.py first}`) without its
quotes reaching the message.

**Why not just let the importer fail?** It does fail, correctly — but its
complaint is about a path it could not open. Where that file *comes from* — a
fetch script, an earlier stage's output, a site-specific location — is the
flow's knowledge, not the engine's, and it is usually the half you need.
Declaring the inputs also moves the failure to the top of the script, so a
run that cannot succeed stops on line one rather than after the setup that
precedes the read.

**Example** — `flow/ariane133`, whose netlist and macro LEF are fetched from
upstream and deliberately not checked in:

```buda
require_file ariane.v fakeram45_256x16.lef hint Fetch them first:  python3 flow/ariane133/fetch.py
open_bdb ariane133.bdb
...
```

```
BUDA-1905: FATAL: 2 required input file(s) not found (ariane133.buda):
    ariane.v   → /repo/flow/ariane133/ariane.v
    fakeram45_256x16.lef   → /repo/flow/ariane133/fakeram45_256x16.lef
  Fetch them first: python3 flow/ariane133/fetch.py
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

A `#` inside a [quoted](#paths-and-paths-with-spaces) run is kept too —
`require_file "inputs/rev #2/top.v"` is a filename, not a comment. Quoting
is the escape for a `#` the same way it is for a space; unquoted, the
comment still wins.

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

Every `<path>` below resolves against the script's own directory, and a
spaced one is spelled per [Paths, and paths with
spaces](#paths-and-paths-with-spaces): bare for `import_verilog` and
`save_bdb`, quoted for `open_bdb` and `import_def_lef`.

### Quick reference

| Command | Description |
|---|---|
| `open_bdb <path> [writeback]` | Open or create a `.bdb`, or materialize a `*.bdb.sql` text fixture to a temp binary. `writeback` (for `.sql` only) dumps changes back to the `.sql` on `save_bdb`/`exit`/end-of-run. Use before any other BDB command. |
| `save_bdb` | Write the working BDB back to its `*.bdb.sql` source now (after `open_bdb … writeback`). |
| `import_def_lef <def> <lef> [no_tracks] [no_blockages] [allow_missing_footprints]` | Import placements from DEF + cell sizes from LEF. Clears all tables. Also imports the DEF's `TRACKS` and its obstruction (macro `OBS`, `LAYER` blockages, `SPECIALNETS` straps) as keepouts — see [BDB_REFERENCE](BDB_REFERENCE.md#obstruction-and-keepouts). |
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

### Hierarchical routing quick reference

These drive the *hierarchy-aware* flow (`run_hier_bundler` →
`generate_hier_topologies` → `run_planner hier`). Each has a full section in
**[docs/BDB_REFERENCE.md](BDB_REFERENCE.md)**.

| Command | Description |
|---|---|
| [`set_bottom_up <cell>\|* [on\|off]`](BDB_REFERENCE.md#set_bottom_up) | Plan and route a cell's own interconnect **once**, then copy it to every instance (copies become keepouts for higher levels). `*` marks every eligible cell. Opt-in: the default hier flow marks nothing. |
| [`align_bottom_up [max_shift <um>] [force]`](BDB_REFERENCE.md#align_bottom_up) | Nudge a marked cell's instances onto a **common track phase**, with minimal total movement, so the copies land on real signal tracks. Run after `def_track_pattern` + `set_bottom_up`, **before** `derive_busterms` / `add_blocks_from_bdb`. |
| [`check_template_tracks [on_mismatch stop\|independent]`](BDB_REFERENCE.md#check_template_tracks) | The uniformity gate: verify every instance sees the same signal tracks before copying. Run after `run_nuts`, before `run_detailed_nuts`. |
| [`set_cell_layer_cap <cell>\|* <cap> [-min <floor>]`](BDB_REFERENCE.md#set_cell_layer_cap) | Restrict a cell's own interconnect to the layer band `[floor..cap]`. |
| [`set_cell_layer_share <cell> <layer> <pct>`](BDB_REFERENCE.md#set_cell_layer_share) | Lease a cell at most `pct`% of a layer's signal tracks — thins a layer inside its band, or grants a bounded slice above the cap. |
| [`set_layer_caps_by_depth <cap1> [<cap2> …] [-min <floor>]`](BDB_REFERENCE.md#set_layer_caps_by_depth) | Bulk bands by how deep a cell's own content goes, deepest first. |
| [`reserve_top_layers <N> [-min <floor>]`](BDB_REFERENCE.md#reserve_top_layers) | The stack-relative twin: reserve the top `N` layers for the top level and cap everything below. Prefer it when the intent is "the top level gets the top N" — an absolute band is only correct for the stack it was written against. |
| [`load_pipeline [expanded]`](BDB_REFERENCE.md#load_pipeline) | Resume from a BDB checkpoint: bundles, candidate topologies, the plan, and as much routing as was persisted. |

**Bottom-up command order** (the part that is easy to get wrong):

```buda
def_track_pattern …          # patterns first — alignment needs the pitches
set_bottom_up my_cell
align_bottom_up              # then phase-align the instances
derive_busterms 1            # only now derive busterms / load blocks
add_blocks_from_bdb 0
…
run_planner hier signal_tracks
run_nuts
check_template_tracks        # gate before copying; default policy STOPS on mismatch
                             # (add `on_mismatch independent` only if you accept
                             #  solving misaligned instances individually)
run_detailed_nuts
```

Worked vehicles: `flow/rnr/mix2_fast_bottomup.buda` (with layer caps:
`…_caps.buda`, with fractional shares: `…_shared.buda`) and
`flow/chip/chip_bottomup.buda` at chip scale.

**Common patterns:**

```buda
# DEF + Verilog merge
open_bdb  <path1>/gcd.bdb  # create new empty db (or open existing one)
import_def_lef  <path2>/gcd.def  <path2>/gcd.lef
import_verilog  <path3>/gcd.v

# Fixup after import
move_comp   u_regfile  10.0  10.0
resize_cell DFFRX1     5.6   4.0

# Build from scratch
open_bdb  ./tiny.bdb
add_comp  u_a  blk  -      0   0  100 100 nonleaf
add_comp  u_b  blk  -    200   0  300 100 nonleaf
add_comp  u_a/x0  cell  u_a   10  10   50  50 leaf
```
