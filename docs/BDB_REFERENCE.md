# BDB Reference

**BDB** (Buda Physical Design Database) is a SQLite-backed store for the
physical netlist: component placements, net connectivity, pin positions,
busterms, bundles, and groups. Every other BUDA module that needs layout
information reads it exclusively through the `BDB` C++/Python class.

A `.bdb` file is an ordinary SQLite 3 database and can be opened with any
SQLite browser (e.g. [DB Browser for SQLite](https://sqlitebrowser.org/)).

---

## Contents

1. [Schema overview](#1-schema-overview)
   - [Pin Directions](#pin-directions)
2. [`.buda` script commands](#2-buda-script-commands)
3. [Python API](#3-python-api)
   - [Row types](#row-types)
   - [Ingestion](#ingestion)
   - [Cell definitions and hierarchy](#cell-definitions-and-hierarchy)
   - [Mutations](#mutations) — `move_comp`, `resize_cell`, `flip_comp`, `rotate_comp`, `add_comp`
   - [Computed properties](#computed-properties)
   - [Queries](#queries)
   - [Group management](#group-management)
   - [Metadata](#metadata)
4. [Typical workflows](#4-typical-workflows)
5. [Notes and caveats](#5-notes-and-caveats)
6. [Design interchange formats](#6-design-interchange-formats)
   - [Supported today: LEF/DEF + Verilog](#supported-today-lefdef--verilog)
   - [Planned: GDSII import/export](#planned-gdsii-importexport)
   - [Planned: OpenAccess import/export](#planned-openaccess-importexport)

---

## 1. Schema overview

```
component        id, name, cell, parent_id→component, depth,
                 x1, y1, x2, y2, is_leaf, is_replicated, orient

cell             name (PK), width, height

cell_children    parent_cell→cell, inst_name, child_cell→cell, x, y
                 PRIMARY KEY (parent_cell, inst_name)

cell_pin         cell→cell, pin_name, dir (INPUT|OUTPUT|INOUT), px, py
                 PRIMARY KEY (cell, pin_name)

net              id, name

pin              net_id→net, comp_id→component, pin_name,
                 dir (INPUT|OUTPUT|INOUT), px, py

net_props        net_id→net, hpwl, fanout, driver_comp,
                 bus_name, bit_index, bundle_id

busterm          id (TEXT), comp_id→component, hier_path,
                 depth, x1, y1, x2, y2, resolution, parent_id→busterm,
                 rects (JSON multi-rect), teg_mode, orig_x1..orig_y2
                 (routing-time rows id 'tb:<block>' carry the full topology.h
                 Busterm; hier-derived rows id 'bt:<name>')

bundle           id (TEXT), level, strategy, reason, num_terminals,
                 cell_context, instances (JSON), parent_id→bundle,
                 is_replicated, drv_spec_depth, rcv_spec_depth,
                 drv_spec_path, rcv_spec_paths (JSON)
bundle_net       bundle_id→bundle, net_id→net, ord (bit order)
                 PRIMARY KEY (bundle_id, net_id)
bundle_busterm   bundle_id→bundle, busterm_id, role ('entry'|'exit')

topology         bundle_id→bundle, cand_index, type, wirelength,
                 trunk_location, pass_through_count, connected_blocks (JSON),
                 feedthru_blocks (JSON), is_selected, is_pinned
                 PRIMARY KEY (bundle_id, cand_index)
topology_segment bundle_id, cand_index, seg_index, x1,y1,x2,y2,
                 layer_hint, is_jog, assigned_layer (planner's per-seg layer),
                 edge_id (MST-edge identity, v14),
                 perp_clamp_lo/hi (overlap-U perp slide clamp, v16)
                 PK (bundle_id, cand_index, seg_index)
                 FK (bundle_id, cand_index) → topology
topology_seg_busterm
                 bundle_id, cand_index, seg_index, endpoint ('start'|'end'),
                 busterm_id→busterm  — one row per real tap (seg_busterms);
                 a missing (seg,endpoint) is a wire junction
                 PK (bundle_id, cand_index, seg_index, endpoint)
                 FK (bundle_id, cand_index) → topology
topology_seg_conn
                 bundle_id, cand_index, seg_index, endpoint ('start'|'end'),
                 other_seg  — one row per seg-to-seg junction link
                 (seg_conns, v12); a missing (seg,endpoint) is a free
                 end or a busterm tap
                 PK (bundle_id, cand_index, seg_index, endpoint, other_seg)
                 FK (bundle_id, cand_index) → topology
topology_bridge_segment
                 bundle_id, cand_index, block_name, x1,y1,x2,y2,
                 layer_hint, is_jog — one TEG-over bridge per
                 (candidate, multi-rect block) (v11)
                 PK (bundle_id, cand_index, block_name)
                 FK (bundle_id, cand_index) → topology

bus_segment      bundle_id→bundle (FK), seg_idx, layer, is_horiz,
                 x1,y1,x2,y2, track_position, width, placed, is_jog,
                 interval_lo, interval_hi, track_lo_bound, track_hi_bound
                 (v10 solver state for load_pipeline; NULL bound = unbounded)
                 PRIMARY KEY (bundle_id, seg_idx)
bus_via          bundle_id→bundle (FK), from_seg, to_seg, from_layer,
                 to_layer, x, y, bit_width
                 PRIMARY KEY (bundle_id, from_seg, to_seg)
route_snapshot   id (=1, singleton), hash, n_bus_segments, n_bus_vias,
                 stage, n_net_segments, n_net_vias
                 — fingerprint of the routed output

net_segment      bundle_id→bundle (FK), seg_idx, bit_index,
                 net_id→net, layer, is_horiz, x1,y1,x2,y2,
                 track_position, width  — one detailed bit-wire
                 PRIMARY KEY (bundle_id, seg_idx, bit_index)
net_via          bundle_id→bundle (FK), from_seg, to_seg, bit_index,
                 net_id→net, from_layer, to_layer, x, y
                 — one per-bit via (bus_via fanned out per bit)
                 PRIMARY KEY (bundle_id, from_seg, to_seg, bit_index)

grp              id (TEXT), name, color, parent_id→grp
grp_member       grp_id→grp, kind, ref

meta             key (TEXT PK), value  — die_w, die_h, units,
                 schema_version, bdb_tool
```

**Schema version.** The BDB stamps `PRAGMA user_version` (mirrored in
`meta.schema_version`) and migrates forward on open. v1 added versioning +
provenance; v2 added the **bundle-persistence** shape above; v3 re-keyed
`bundle_net` by `net_id`; v4 added the **candidate-topology** tables; v5 added the
**abstract-NUTS bus-routing** tables; v6 added `topology_segment.assigned_layer`
(the planner's per-segment layer); v7 hardened `bus_segment`/`bus_via.bundle_id`
into a **foreign key** to `bundle(id)` and added the **`route_snapshot`**
fingerprint table (the v6→v7 migration rebuilds the bus tables with the FK,
dropping any pre-FK orphan rows); v8 added the **detailed-NUTS** `net_segment`/
`net_via` tables plus the `route_snapshot` `n_net_*` count columns; v9 added
routing-time busterm attributes (`busterm.teg_mode` + `orig_x1..y2`) and the
**`topology_seg_busterm`** join that persists `seg_busterms` logically; v10 added
[`load_pipeline`](#load_pipeline) resume support — the **stage-4 solver state**
columns on `bus_segment` (perpendicular interval + corner-split track bounds),
`bundle_net.ord` (the bundle's bit order), and `topology.is_pinned` (a pre-plan
pin survives a checkpoint); v11 added **`topology_bridge_segment`** (TEG-over
bridges), closing the last un-persisted `Topology` field; v12 added
**`topology_seg_conn`** — the seg-to-seg junction links (`Topology::seg_conns`,
topo-truth Phase 5), so a reload restores BOTH halves of a topology's
connectivity logically; v13 added **`component.orient`** (instance
rotation/mirror as an 8-orientation token `N/S/E/W/FN/FS/FE/FW`, default `'N'`)
so GDS import→export→re-import preserves orientation.
`tools/bdb_serialize.py` preserves the version across the `*.bdb.sql`
round-trip.

**Bundle persistence.** `run_bundler` (flat) and `run_hier_bundler` (hier) write
their Stage-1 bundles into `bundle` / `bundle_net` / `bundle_busterm` whenever a
BDB is open. Membership is keyed by `net_id`; `add_bundle_net(bundle_id, net_name)`
takes a **name** and resolves it, auto-creating a name-only `net` row if absent so
the flat flow (whose nets may not be in the `net` table) persists too.
`bundle_nets(id)` joins back to return names. C++ API: `add_bundle(BundleRow)`,
`add_bundle_net`, `add_bundle_busterm(bundle_id, busterm_id, role)`,
`clear_bundles()`, `all_bundles()`, `bundle_nets(id)`, `bundle_busterms(id)`.

**Bus-routing persistence.** `run_nuts` writes each placed abstract-NUTS segment
into `bus_segment` (the placed rectangle + layer) and one **symbolic bus-via** per
bus-level layer transition into `bus_via` — a via wherever two segments of a
bundle that are connected (per `ConnTopology`, including trunk/stub **T-junctions**)
sit on different layers, one row for all `bit_width` bit-vias. `bundle_id` is a
**hard foreign key** to `bundle(id)`: every bus row joins a persisted bundle. The
hier flow's per-instance wrappers are persisted first (as `is_replicated=1` bundle
rows) so the FK is satisfiable; if `run_nuts` is reached before the planner output
was persisted, `_persist_nuts` persists the parents first. `clear_bundles()` /
`clear_expanded_bundles()` drop the bus rows before their parent bundle rows.
`run_nuts` also writes a **`route_snapshot`** singleton (id=1): a SHA-256 over a
canonical, order-independent serialization of all `bus_segment` + `bus_via` rows,
plus the row counts and stage — so a routing change is one reviewable line in the
`*.bdb.sql` diff, and it is the natural feed for the planned BDB → OA/GDS export.
C++ API: `add_bus_segment(BusSegRow)`, `add_bus_via(BusViaRow)`,
`clear_bus_routing()`, `bus_segments(bundle_id)`, `bus_vias(bundle_id)`,
`set_route_snapshot(hash, n_seg, n_via, stage[, n_net_seg, n_net_via])`,
`route_snapshot()`.

**Detailed-NUTS persistence (schema v8).** `run_detailed_nuts` writes each
bit-wire into `net_segment` (the placed rectangle plus the bit's net identity:
`net_name = net_names[bit_index]` — the **logical** bit, `bit_order` already
applied — resolved to a `net_id` via `_ensure_net`, auto-creating a name-only
`net` row as with `bundle_net`) and each per-bit via into `net_via` — the
symbolic `bus_via` **fanned out per bit**, sharing its
`(bundle_id, from_seg, to_seg)` key with `bit_index` appended, positioned at
the crossing of the two bits' placed tracks. Spans are stored **as-is** (they
may be reversed, `span_lo > span_hi`, after the engine's endpoint snap — same
convention as `bus_segment`; consumers take min/max). `bundle_id` is a hard FK
to `bundle(id)` (same NOT NULL + parent-ensure rules as the bus tables), and
re-solving upstream invalidates downstream: `run_nuts` (clear_bus_routing)
wipes the net rows too, and `clear_bundles()` / `clear_expanded_bundles()`
drop them before their parent bundles. The `route_snapshot` is rewritten with
stage `'detailed_nuts'`, hashing the net rows as well (by net **name**, so the
digest is independent of net-id autoincrement history) and preserving the bus
counts. Reads LEFT JOIN `net` to return `net_name`. C++ API:
`add_net_segment(NetSegRow)`, `add_net_via(NetViaRow)`,
`clear_detailed_routing()`, `net_segments(bundle_id)`, `net_vias(bundle_id)`.

**Topology persistence.** `generate_topologies` (flat) and
`generate_hier_topologies` (hier) write **all** candidate topologies into
`topology` / `topology_segment` whenever a BDB is open — **before** `run_planner`,
so a design's candidates are inspectable/tweakable without paying the planner's
runtime on large designs. `clear_bundles()` also wipes the topology tables (they
FK to `bundle`). C++ API: `add_topology(TopoRow)`,
`add_topology_segment(TopoSegRow)`, `clear_topologies()`, `topologies(bundle_id)`,
`topology_segments(bundle_id, cand_index)`.

Each candidate also persists its **`seg_busterms`** — the authoritative
segment-endpoint→busterm annotation — **logically**, so a reload restores
connectivity without re-deriving it from geometry (the single-source-of-topo-truth
principle; see `docs/internal/single_source_topo_truth.md`). Each real tap becomes
a routing-time busterm row (`tb:<block>`, carrying the full `Busterm` incl.
multi-rect + TEG) plus a `topology_seg_busterm` link; a junction endpoint writes no
row. The buda-module bridge `persist_seg_busterms` / `load_seg_busterms`
(`bind_routing.cpp`) is the single serializer; `load_seg_busterms` rebuilds the
annotation from the link rows + `BDB::busterm(id)` alone (no floorplan). C++ API:
`add_topology_seg_busterm(TopoSegBustermRow)`,
`topology_seg_busterms(bundle_id, cand_index)`, `busterm(id)` (fetches a single
row incl. `tb:` — `all_busterms()` returns hier-derived rows only).

**Planner-output persistence.** `run_planner` records its decision: it marks the
selected candidate (`topology.is_selected`, via `set_topology_selected`) and the
per-segment assigned layers (`topology_segment.assigned_layer`, via
`set_segment_layer`). In the **hier** flow, `run_planner hier` expands cell-level
bundles into per-instance wrappers; those are persisted as `is_replicated=1`
`bundle` rows (`parent_id` = the template bundle) carrying just their selected
topology, so `bus_segment` rows join back to a bundle. `clear_expanded_bundles()`
drops those instance rows (idempotent re-plan). Templates keep their full candidate
set; each instance records its own selection + layers.

**coordinates** are in microns (µm). `import_def_lef` converts from DEF
internal units using the `UNITS DISTANCE MICRONS` value from the DEF header.
Unresolved pin positions are stored as `−1`.

`parent_id` in `component` is `NULL` for top-level (depth-0) instances.
Python returns `−1` for a `NULL` parent.

**`cell` / `cell_children`** store the *structural* definition of a cell type —
its canonical size and which child instances it contains at what relative
positions.  `component` stores the *physical* occurrences (one row per placed
instance).  When `add_inst` places a cell that has `cell_children` rows, the
engine recursively creates all descendant `component` rows automatically
(eager expansion).

---

### Pin Directions

The `dir` column in `pin` and `cell_pin` uses the following string values:

| Direction | Valid in | Meaning |
|-----------|----------|---------|
| `OUTPUT` | `pin`, `cell_pin` | The pin drives the net — signal flows out of this component. |
| `INPUT` | `pin`, `cell_pin` | The pin receives the net — signal flows into this component. |
| `INOUT` | `pin`, `cell_pin` | Bidirectional port; can both drive and receive. Default for `add_cell_pin`. |
| `UNKNOWN` | `pin` only | Direction not known at definition time. Used by `import_verilog`, incomplete LEF, and `add_net … unknown`. |

`cell_pin.dir` (cell-type ports) does not use `UNKNOWN` — it represents the
canonical port definition for a cell type.  `pin.dir` (instance-level pins
created by `add_net`, `add_net_pins`, etc.) uses all four values.

#### How each direction is set

| Source | Direction stored |
|--------|-----------------|
| `add_net <n> <drv> <rcv>` with `bdb_net_mode on` | driver → `OUTPUT`; receivers → `INPUT` |
| `add_net <n> <p1> <p2> unknown` with `bdb_net_mode on` | all pins → `UNKNOWN` |
| `add_net <n> <p1> <p2> inout` with `bdb_net_mode on` | all pins → `INOUT` |
| `add_bus … unknown` with `bdb_net_mode on` | all pins for every expanded net → `UNKNOWN` |
| `add_bus … inout` with `bdb_net_mode on` | all pins for every expanded net → `INOUT` |
| `import_verilog` | all instance-level pins → `UNKNOWN`; then overridden per-pin if a matching `cell_pin` row with a direction exists |
| `import_def_lef` — LEF pin with `DIRECTION OUTPUT/INPUT/INOUT` | stored as-is |
| `import_def_lef` — LEF pin with no `DIRECTION` keyword | → `UNKNOWN` |
| `add_cell_pin` | stored as specified (`INOUT` if omitted) |
| `bdb.add_net_pins(net, drv, rcvs)` | driver → `OUTPUT`; receivers → `INPUT` |
| `bdb.add_net_pins_undirected(net, pins)` | all pins → `UNKNOWN` |
| `bdb.add_net_pins_inout(net, pins)` | all pins → `INOUT` |

#### Hierarchy propagation

When `bdb_net_mode on` is active (or when calling `add_net_pins` / `add_net_pins_undirected` / `add_net_pins_inout` directly), *interface pins* are automatically created at every ancestor component between the leaf pin and the common ancestor of all endpoints. Interface pins inherit the direction of their corresponding leaf pin.

#### Driver priority in `run_hier_bundler`

`run_hier_bundler` selects drivers with the following priority:

| Priority | Direction | Role |
|----------|-----------|------|
| 1 | `OUTPUT` | Preferred driver |
| 2 | `INOUT` | Secondary driver (if no OUTPUT pin exists); otherwise treated as receiver |
| 3 | `INPUT` | Receiver only |
| 4 | `UNKNOWN` | Positional fallback (if neither OUTPUT nor INOUT exist) |

**`INOUT` behaviour:** If no `OUTPUT` pin exists, the first `INOUT` pin (BDB insertion order) becomes the driver; remaining `INOUT` pins become receivers. If an `OUTPUT` pin already drives the net, all `INOUT` pins are receivers. A `[HierBundler]` line is written to `stderr` when INOUT fallback fires.

**`UNKNOWN` behaviour:** Identical fallback rule as INOUT, but only applies when neither `OUTPUT` nor `INOUT` pins are present. A `[HierBundler]` line is written to `stderr` when UNKNOWN fallback fires; `run_hier_bundler` also reports dropped nets on `stdout` after bundling.

> **Pin ordering matters for INOUT and UNKNOWN nets.** The first pin listed in `add_net`/`add_bus` is treated as the driver when the fallback fires. List the driving component's pin first.

---

## 2. `.buda` script commands

The BDB commands form a self-contained sub-flow inside a `.buda` script.
Call `open_bdb` first; all other BDB commands will print an error and skip
if no database is open.

### `open_bdb`

```
open_bdb <path> [writeback]
```

Open (or create) a BDB at `<path>`. Subsequent BDB commands operate on this
database. Call once per script; opening a second path replaces the reference
(the first file is not closed automatically — use the Python API if you need
multiple simultaneous databases).

A **serialized text fixture** (`*.bdb.sql`, produced by `tools/bdb_serialize.py`)
is accepted directly: it is materialized into a throwaway temp binary, so the
pipeline never dirties the checked-in text. By default those changes are
**discarded**. Add the `writeback` keyword to instead dump the working binary back
to the source `.sql` — on [`save_bdb`](#save_bdb), on the next `open_bdb`, and at
`exit` / end of run — an opt-in way to deliberately update a committed fixture.
`writeback` is ignored (with a note) for a plain binary `.bdb`, which is already
opened read-write and persists directly. See
[BDB Test-Data Management](internal/bdb_test_data.md).

| Argument | Description |
|---|---|
| `path` | File path for the `.bdb`; created if it does not exist. A `*.bdb.sql` text fixture is materialized to a temp binary. Use `:memory:` for an in-memory scratch database. |
| `writeback` | (optional) For a `*.bdb.sql` path, write the working binary back to that `.sql` on `save_bdb`/`exit`/end-of-run. Ignored for a binary `.bdb`. |

---

### `save_bdb`

```
save_bdb [<path.bdb | path.bdb.sql>]
```

**No argument:** serialize the working BDB back to the source `*.bdb.sql`
**now** (mid-run). Only meaningful after `open_bdb <file>.sql writeback`;
otherwise it is a no-op with a note. The write also happens automatically on
the next `open_bdb`, on `exit`, and at end of run, so an explicit `save_bdb`
is only needed to checkpoint mid-flow.

**With a path (save-as):** snapshot the CURRENT state to a new file,
independent of any writeback source — e.g. a placement checkpoint right
after `align_bottom_up` that later runs reopen directly (the `set_bottom_up`
flags and aligned coordinates are both in the copy). A `.sql` destination
gets the diffable text form (`tools/bdb_serialize`), anything else a binary
SQLite snapshot (`BDB::save_copy`, the online-backup API — safe while the
session holds the database open, `:memory:` included). An existing
destination is overwritten; the file the live connection holds open is
refused. Unlike the no-arg form, a save-as is a **one-shot copy** — it is
not re-flushed at exit, so a mid-flow snapshot is not overwritten by the
end-of-run state.

```buda
open_bdb mix2.bdb.sql          # fixture stays pristine (no writeback)
set_bottom_up dnuts1
align_bottom_up
save_bdb mix2_aligned.bdb.sql  # aligned placement checkpoint, one shot
```

Python API: `db.save_copy(dest_path)` — binary snapshot of the live
database.

---

### `load_pipeline`

```
load_pipeline [expanded]
```

**Resume / rehydrate**: rebuild the in-memory routing pipeline from the open
BDB's persisted rows, so a fresh session can continue where a previous one
stopped — checkpoint after `generate_topologies`, `run_planner`, or `run_nuts`
(+`ripup_reroute`), reopen the BDB later, `load_pipeline`, and run the next
stage. Restores, as deep as was persisted:

1. **Bundles + all candidate topologies** from `bundle`/`bundle_net` (net names
   in **bit order** via `bundle_net.ord`) + `topology`/`topology_segment`. Each
   reloaded candidate's `seg_busterms` is restored **logically** from the
   `topology_seg_busterm` links (`load_seg_busterms` — the single source of
   topo truth, never re-derived from geometry), so ConnTopology reads the same
   authoritative endpoint taps as a freshly generated one — continuations
   reproduce the single-session results exactly (same rows, same
   `route_snapshot` hash).
2. **The planner's decision** (selected candidate from `topology.is_selected`,
   per-segment layers from `topology_segment.assigned_layer`) plus any
   **pre-plan pin** (`topology.is_pinned` — a resumed `run_planner` honors a
   checkpointed `select_topology`), so `run_nuts` can run directly.
3. **The abstract-NUTS result** from `bus_segment` (incl. the v10 solver-state
   columns: perpendicular interval + corner-split track bounds) so
   `run_detailed_nuts` can run directly.

**Prerequisite:** re-declare the setup first — `def_layer`/`def_track_pattern`
and the blocks (`add_block` for the flat flow, `add_blocks_from_bdb` for hier).
Topology coordinates are absolute, and slide ranges / net_pull are deliberately
*not* persisted — the planner and NUTS recompute them from geometry +
Floorplan. `load_pipeline` fails fast if a persisted topology references a
block missing from the current Floorplan.

`expanded` selects the hier post-expansion view (`is_replicated=1` per-instance
rows + non-template bundles) instead of the pre-expansion templates. An
expanded instance persists only its selected topology (at its template
`cand_index`), so its selection is remapped to the compact in-memory list.

Not restored: `seg_perp` (a NUTS placement *preference* from the planner's
charged bands), planner band state, overlap details. TEG-over bridge segments
**are** restored (`topology_bridge_segment`, v11), so TEG-over multi-rect
designs resume losslessly. `ripup_reroute` and `run_nuts_on_layer` both
**commit** their final routing via the `_checkpoint_routing()` choke point
(planner output + NUTS + detailed rows), so a checkpoint after either resumes
from the re-solved routing, not stale rows. The visualizer's interactive
rerun buttons (↺ / Re-run & Refresh) are deliberately **pure previews** — a
checkpoint changes only on explicit commands, never while exploring.
Tests: `test/tests/test_bdb_resume.py`, `test/tests/test_bdb_resume_gaps.py`.

---

### `import_def_lef`

```
import_def_lef <def_path> <lef_path>
```

Parse a DEF file for component placements and die dimensions, and a LEF file
for cell sizes and pin offsets. **Clears all existing tables** before import.

| Argument | Description |
|---|---|
| `def_path` | Path to the DEF file (VERSION 5.x). Must contain `UNITS DISTANCE MICRONS`, `DIEAREA`, and `COMPONENTS` sections. |
| `lef_path` | Path to the LEF file. `MACRO … SIZE … PIN …` entries are used; everything else is ignored. |

After import: `component` rows have `x1/y1/x2/y2` from the DEF placement
plus the LEF `SIZE`, but `parent_id` and `depth` are `NULL`/0 until
`import_verilog` is run.

---

### `import_verilog`

```
import_verilog <v_path>
```

Parse a Verilog netlist and elaborate the module hierarchy into the `component`,
`net`, and `pin` tables. Clears `net`, `pin`, and `net_props` before import;
**does not clear `component`** so existing placement data from `import_def_lef`
is preserved via UPSERT.

| Argument | Description |
|---|---|
| `v_path` | Path to a structural Verilog file (`.v`). The top module is identified as the last module not instantiated by any other module in the file. |

**What is elaborated:**
- One `component` row per instance path (e.g. `ai/a1i1`).
- Hierarchy fields (`cell`, `parent_id`, `depth`, `is_leaf`) are set from the
  Verilog; coordinates are preserved from an earlier `import_def_lef` if
  present.
- One `net` row per elaborated wire; internal wires are scoped
  (`ai/w1`); wires connected through port bindings keep the caller's net name.
- One `pin` row per port connection per instance.

---

### `import_gds`

```
import_gds <file.gds> [labels <layer_csv>]
def_gds_layer <buda_layer_id> <gds_layer> [<gds_datatype>]
def_gds_layer file <path>
def_gds_layer labels <layer_csv>
```

Import a **GDSII stream** file into the open BDB (Phases G1–G3 of
[`docs/internal/gds_oa_interchange.md`](internal/gds_oa_interchange.md)). A
hand-written binary record reader (`src/gds_io.cpp`, no external EDA library),
following the importer pattern: **fresh load** (clears
`pin`/`net_props`/`net`/`component`/`cell` like `import_def_lef`), all
coordinates normalized to **µm** via the `UNITS` record.

- **Structures → `cell` rows.** Footprint = the *recursive* bbox (own
  `BOUNDARY`/`BOX`/`PATH` geometry ∪ transformed child references), since a GDS
  structure is a macro — the LEF `SIZE` analogue is the full extent. Memoized
  with a cycle guard. `PATH` geometry is stroked: centerline ± `WIDTH`/2, with
  `PATHTYPE` 1/2 end extension.
- **`SREF`/`AREF` → `component` hierarchy.** A top (unreferenced) structure is
  the **die**, not a component: its references elaborate as *unprefixed*
  depth-0 roots — exactly how `import_verilog` elaborates the top module — so
  the geometry-only merge (`import_gds` + `import_verilog`) matches placements
  by name; a single top's extent also sets the die dimensions (the DEF
  `DIEAREA` analogue). Placements expand recursively into absolute-µm
  component rows with dotted paths and growing depth. `AREF` expands its
  cols×rows array. `STRANS` mirror / `ANGLE` (snapped to 0/90/180/270 with a
  warning) / `MAG` apply at bbox level.
- **Instance names.** GDS references are anonymous; a `PROPVALUE` property on
  the reference is used as the instance name when present (arrays and
  duplicates always synthesize), else `<struct>_<ordinal>` deterministically.
- **`TEXT` labels → `net`/`pin` rows (Phase G2).** Each label string is a
  net; its pin lands on the **deepest** component containing the label's
  elaborated position (dir `UNKNOWN` — the hier bundler's positional fallback
  covers direction). Labels flow through the hierarchy transforms like
  geometry, so a label inside a referenced structure repeats per instance
  (the standard GDS flattening semantic). `labels 63,64` restricts which GDS
  layers carry labels (default: every `TEXT`); labels outside every component
  are skipped with a warning. **A labeled GDS runs the hierarchy-aware flow
  with zero Verilog** (`derive_busterms` → `run_hier_bundler` → …); with no
  labels, pair with `import_verilog` as with DEF.
- **Layer mapping (Phase G3).** `def_gds_layer` binds a `def_layer` metal
  layer to a GDS `(layer, datatype)` pair (datatype defaults to 0), stored on
  the `LayerStack`; the map-file form reads `<buda_layer_id> <gds_layer>
  [<gds_datatype>]` lines (`#` comments). On import, `BOUNDARY`/`BOX`/`PATH`
  shapes on mapped pairs are **routing wires, not macro-outline geometry** —
  they are counted (`n_routing_shapes`) but excluded from cell footprints, so
  re-importing a routed GDS keeps outlines clean (the export→import
  round-trip requirement); the same map drives the Phase-G4 exporter's
  metal→GDS direction. `def_gds_layer labels <csv>` registers the default
  `TEXT` label layers so `import_gds` needs no per-call `labels` argument
  (an explicit argument still overrides). Like all `def_layer`-family setup,
  the mapping lives in the session, not the BDB — scripts re-declare it.

Python: `BDB.import_gds(path, label_layers=[], routing_layers=[])` — with
`routing_layers` a list of `(gds_layer, gds_datatype)` pairs — returns a
`GdsImportStats` (`n_structures`, `n_cells`, `n_components`, `n_texts`,
`n_nets`, `n_pins`, `n_labels_skipped`, `n_routing_shapes`, `tops`,
`warnings`). The mapping API on `LayerStack`: `set_gds_mapping(id, gds_layer,
gds_datatype=0)`, `get_gds_layer(id)` / `get_gds_datatype(id)`,
`layer_for_gds(gds_layer, gds_datatype)` (reverse), `gds_mapped_pairs()`.
Tests generate their GDS inputs deterministically with `tools/gds_build.py`
(the Phase-G0 writer, zeroed timestamps): `test/tests/test_gds_import.py`.

---

### `export_gds`

```
export_gds <file.gds> [outline <gds_layer>] [labels <gds_layer>|off] [via_size <um>]
```

The reverse of `import_gds` (Phase G4): stream the open BDB out as a
**deterministic** GDSII file (zeroed timestamps — identical DBs give
identical bytes), reading the **persisted tables**, so it works on a
reopened checkpoint with no live pipeline. Each `cell` row becomes a
structure (outline rectangle on `(outline_layer, 0)`, default 10, plus child
`SREF`s reconstructed from the cell's first component instance, instance
names as `PROPVALUE`); a top structure carries the die extent, root
placements, the routing — `net_segment` bit-wires as rectangles on each
layer's `def_gds_layer`-mapped pair (abstract `bus_segment` fallback;
`net_via`/`bus_via` rows as `via_size` squares, default 1 µm; unmapped
layers default to `(buda_layer, 0)` with a warning) — and one net-name
`TEXT` label per `pin` row (`labels off` disables; default label layer =
first `def_gds_layer labels` entry, else 63).

**Round-trip:** re-importing with the same `def_gds_layer` map recovers the
identical design — components (including instance **orientation**, v13), cell
footprints, die, and (labeled mode) nets/pins — with every routing shape
excluded from footprints. Rotated/mirrored placements re-emit their
`component.orient` token as `STRANS`/`ANGLE` and round-trip exactly (top-level
instances; deeply-nested oriented instances are best-effort). Caveats: pin
directions come back `UNKNOWN` (GDS has no pin-dir concept); non-unit `MAG` is
not represented (stays in the bbox); a genuinely *resized* instance (bbox
matching neither the cell nor its oriented extent) still warns. See
`test/tests/test_gds_export.py` and
[`docs/internal/gds_oa_interchange.md`](internal/gds_oa_interchange.md).

Python: `BDB.export_gds(path, layer_map=[], outline_layer=10,
label_layer=63, write_labels=True, via_size=1.0)` — `layer_map` entries are
`(buda_layer, gds_layer, gds_datatype)` — returns a `GdsExportStats`
(`n_structures`, `n_placements`, `n_wire_shapes`, `n_via_shapes`,
`n_labels`, `stage`, `warnings`).

---

### `bdb_net_mode`

```
bdb_net_mode on|off
```

Toggle whether `add_net` and `add_bus` also write connectivity into the open
BDB.  Off by default.  Requires an open BDB (`open_bdb`).

When **on**, every `add_net` / `add_bus` call writes to the BDB `net` and
`pin` tables in addition to the routing `Netlist`:

- A row is inserted into `net` for the net name.
- A `pin` row is created at the directly-named leaf component (driver
  `OUTPUT`, receivers `INPUT`).
- **Hierarchy propagation**: at each ancestor component strictly between
  the leaf and the common ancestor of all endpoints, an additional `pin` row
  is inserted using the net name as the interface pin name.  This records
  which nets cross each cell boundary.
- For each new pin, the corresponding cell-type port is auto-registered in
  `cell_pin` with direction set and position `−1` (centroid fallback), unless
  an explicit position was already defined by `add_cell_pin`.

---

### `add_cell_pin`

```
add_cell_pin <cell> <pin_name> [INPUT|OUTPUT|INOUT] [<px> <py>]
```

Define (or update) a port on a cell type.  The position `(px, py)` is
relative to the cell's lower-left origin in µm.  Omit coordinates (or use
`−1`) to leave the position unset; the pin's absolute position will then
default to the component's centroid when pins are created by `add_net_pins`.

| Argument | Default | Description |
|---|---|---|
| `cell` | — | Cell type name; must exist in the `cell` table. |
| `pin_name` | — | Port name, e.g. `out`, `in`, `clk`. |
| `INPUT\|OUTPUT\|INOUT` | `INOUT` | Pin direction. |
| `px` | `−1` | X offset from cell origin (µm). |
| `py` | `−1` | Y offset from cell origin (µm). |

---

### `set_die`

```
set_die <w> <h>
```

Explicitly set the die dimensions.  These values are returned by `die_w()` and
`die_h()` and are used by the visualizer and routing stages.

| Argument | Type | Description |
|---|---|---|
| `w` | float | Die width in µm. |
| `h` | float | Die height in µm. |

For designs built with `import_def_lef`, the die size is read from the DEF
`DIEAREA` statement automatically.  For scratch designs (built with `add_cell`
/ `add_inst`) `die_w()` and `die_h()` fall back to the union bounding box of
all placed components when not set explicitly; call `set_die` when you need a
larger canvas (e.g. routing margin around the blocks).

---

### `move_comp`

```
move_comp <name> <x> <y>
```

Move a single instance to a new origin. The cell size (width × height) is
preserved; only `x1`, `y1`, `x2`, `y2` are updated.

| Argument | Type | Description |
|---|---|---|
| `name` | str | Exact instance path, e.g. `ai/a1i1`. |
| `x` | float | New `x1` in µm. |
| `y` | float | New `y1` in µm. |

Throws if the component does not exist. Triggers `compute_hpwl()`.

---

### `resize_cell`

```
resize_cell <cell> <w> <h>
```

Update the bounding box of **every** instance whose `cell` field matches
`<cell>`, setting `x2 = x1 + w` and `y2 = y1 + h`.  The origin (`x1`, `y1`)
of each instance is unchanged.

| Argument | Type | Description |
|---|---|---|
| `cell` | str | Cell type name, e.g. `a1`. |
| `w` | float | New width in µm. |
| `h` | float | New height in µm. |

Silently does nothing if no instances of `cell` exist. Triggers
`compute_hpwl()`.

---

### `add_cell`

```
add_cell <name> <width> <height>
```

Define (or redefine) a cell type with a canonical size.  This is idempotent —
a second call with the same name overwrites the previous size.  The `cell`
table is also populated automatically by `import_def_lef` from LEF `MACRO SIZE`
entries.

| Argument | Type | Description |
|---|---|---|
| `name` | str | Cell type name, e.g. `cpu`. |
| `width` | float | Width in µm. |
| `height` | float | Height in µm. |

---

### `set_bottom_up`

```
set_bottom_up <cell> [on|off]
```

Mark a cell template for **bottom-up planning** (default `on`): the hier flow
plans/NUTSes the cell's local interconnect once and copies the result to every
instance, with the copied routing becoming keepouts for higher-level bundles
(see `docs/internal/hier_bottom_up_planning.md`).  Persisted in the BDB
(`cell.bottom_up`, schema v17), so the flag survives `save_bdb` /
`load_pipeline`.

Turning the flag **on** requires every instance of the cell to be a pure
*translated* copy of the first one — identity orientation `'N'`, equal
outline, identical child placement relative to the instance origin — because
per-instance copies are translation-only.  A non-congruent instance set is
rejected with an error listing the offenders; `run_planner hier` re-checks the
same congruence at expansion time (placement may change after marking) and
hard-errors on violation.  `off` is always accepted.

| Argument | Type | Description |
|---|---|---|
| `cell` | str | Cell type name; must exist in the `cell` table. |
| `on\|off` | keyword | Optional; defaults to `on`. |

Python API: `db.set_cell_bottom_up(cell, on)`, `db.cell_bottom_up(cell)`,
`db.bottom_up_cells()`, and `CellRow.bottom_up` via `db.all_cells()`.

---

### `align_bottom_up`

```
align_bottom_up [max_shift <um>]
```

Nudge every `set_bottom_up` cell's instances onto a common track phase with
**minimal total movement**, so the bottom-up copies land on real signal
tracks in every occurrence.  Per cell and axis, an instance offset is
track-shift-invariant iff it is a multiple of every relevant layer's unit
pitch (V-layer pitches constrain x, H-layer pitches constrain y, combined as
their LCM); the common phase is chosen among the instances' current phases
minimizing the summed circular shift (the L1 circular median lies on a data
point), so the majority of instances usually stand still.

Moves are applied with `translate_comp` (whole subtree — congruence is
preserved).  Requires an open BDB and a routing grid (`def_track_pattern`);
run **before** `derive_busterms` / `add_blocks_from_bdb` (a later run prints
a staleness WARNING).  Region `add_grid_override` patterns are keyed to
absolute rects and cannot be compensated by translation — verify with
`check_template_tracks`, which also runs pre-routing (placement-stage,
whole-window comparison) exactly for this pairing.

| Argument | Type | Description |
|---|---|---|
| `max_shift <um>` | keyword + float | Optional cap: any nudge larger than this is skipped with a WARNING. |

Python API: `db.translate_comp(name, dx, dy)` — translate a component and
its whole subtree (unlike `move_comp`, which repositions only the named
component's bbox).

---

### `add_inst`

```
add_inst <inst_name> <cell_name> <parent|-> <x> <y>
```

Place one occurrence of a defined cell.  Coordinates are **relative to the
parent's lower-left corner**; use `-` as the parent to place at absolute
coordinates.

| Argument | Type | Description |
|---|---|---|
| `inst_name` | str | Full instance path, e.g. `u1` or `u1/s1`. |
| `cell_name` | str | Must exist in the `cell` table. |
| `parent` | str | Instance path of the parent, or `-` for a root instance. |
| `x` | float | X offset from parent origin (or absolute x for root). |
| `y` | float | Y offset from parent origin (or absolute y for root). |

The bounding box is computed as `(abs_x, abs_y) – (abs_x + cell.width,
abs_y + cell.height)`.  The parent is automatically marked `is_leaf=0`.

**Eager expansion:** if `cell_children` rows exist for `cell_name`, all
descendant component rows are created recursively before `add_inst` returns.
All `add_inst_to_cell` calls for a cell must therefore precede the `add_inst`
calls that place it.

Throws if `cell_name` is not in the `cell` table or `parent` is not found.
Triggers `compute_hpwl()`.

---

### `add_inst_to_cell`

```
add_inst_to_cell <parent_cell> <inst_name> <child_cell> <x> <y>
```

Define the structural contents of a cell type: "inside every occurrence of
`parent_cell`, there is an instance named `inst_name` of `child_cell` at
relative offset `(x, y)`."

This writes to `cell_children` only — no `component` rows are created.
Expansion happens when `add_inst` places an occurrence of `parent_cell`.

| Argument | Type | Description |
|---|---|---|
| `parent_cell` | str | Cell type that will contain the child. |
| `inst_name` | str | Instance name within the cell (leaf segment of the path). |
| `child_cell` | str | Cell type of the child instance. |
| `x` | float | X offset from parent cell origin in µm. |
| `y` | float | Y offset from parent cell origin in µm. |

Throws if either cell is not defined. Idempotent — re-issuing the same call
updates the position.

**Example** (equivalent to placing 52 instances explicitly):

```buda
open_bdb :memory:
add_cell blk  300 230
add_cell sub  120  90
add_cell leaf  45  50

# Define blk's contents: 2×2 grid of sub-blocks
add_inst_to_cell  blk  s1  sub   15  15
add_inst_to_cell  blk  s2  sub  155  15
add_inst_to_cell  blk  s3  sub   15 125
add_inst_to_cell  blk  s4  sub  155 125

# Define sub's contents: 2 leaves side by side
add_inst_to_cell  sub  l1  leaf  10  20
add_inst_to_cell  sub  l2  leaf  65  20

# Place 4 top-level occurrences — each expands to 13 component rows
add_inst u1  blk  -   50   50
add_inst u2  blk  -  400   50
add_inst u3  blk  -   50  330
add_inst u4  blk  -  400  330
# Result: 4×(1 blk + 4 sub + 8 leaf) = 52 component rows total
```

---

### `add_blocks_from_bdb`

```
add_blocks_from_bdb <depth> [deepest|skip|error]
```

Walk the component hierarchy and call `add_block` on every component at the
requested depth, making them available to the routing engine.

| Argument | Default | Description |
|---|---|---|
| `depth` | — | Target component depth (0 = root level). |
| `deepest` | ✓ | If a branch ends before `depth`, add that branch's deepest component. |
| `skip` | — | Only add components at exactly `depth`; shallower branches produce nothing. |
| `error` | — | Abort (add no blocks) if any branch is shallower than `depth`. |

Components with `x1 < 0` (unplaced) are always silently skipped.

A log file named `<script_stem>_bdb_blocks.log` is written alongside the
script listing every block name added, with `[deepest-fallback]` annotating
any fallback instances.

---

### `flip_comp`

```
flip_comp <name> <x|y>
```

Mirror a component and all of its descendants in place.

| Argument | Type | Description |
|---|---|---|
| `name` | str | Full instance path of the root component to flip, e.g. `u1`. |
| `x\|y` | str | `x` — mirror left-right about the component's vertical centre; `y` — mirror up-down about the horizontal centre. |

The root component's bounding box is unchanged.  Each descendant `d` is
repositioned so that:
- **flip x**: `new_x1 = root.x1 + root.x2 − d.x2`, `new_x2 = root.x1 + root.x2 − d.x1`
- **flip y**: `new_y1 = root.y1 + root.y2 − d.y2`, `new_y2 = root.y1 + root.y2 − d.y1`

Throws if `name` does not exist.

---

### `rotate_comp`

```
rotate_comp <name> <90|180|270>
```

Rotate a component and all of its descendants counter-clockwise by the given
angle, keeping the component's lower-left corner fixed.

| Argument | Type | Description |
|---|---|---|
| `name` | str | Full instance path of the root component to rotate, e.g. `u1`. |
| `90\|180\|270` | int | Rotation in degrees CCW (only multiples of 90 are supported). |

For 90° and 270° the root's width and height are swapped (the bounding box
becomes `(x1, y1) – (x1+H, y1+W)` where `W` and `H` are the original
width and height).  For 180° the root bbox is unchanged.

Each descendant `d` with relative position `(crx, cry)` and size `(cw, ch)`
within the root `(W×H)` is repositioned as follows:

| Rotation | New position in absolute coords |
|---|---|
| 90° CCW | `x1 = rx1 + (H − cry − ch)`, `y1 = ry1 + crx`, size `ch × cw` |
| 180°    | `x1 = rx1 + (W − crx − cw)`, `y1 = ry1 + (H − cry − ch)`, size `cw × ch` |
| 270° CCW| `x1 = rx1 + cry`, `y1 = ry1 + (W − crx − cw)`, size `ch × cw` |

Throws if `name` does not exist or degrees is not 90, 180, or 270.

---

### `add_comp`

```
add_comp <name> <cell> <parent|-> <x1> <y1> <x2> <y2> [leaf|nonleaf]
```

Insert a new component row using **explicit absolute coordinates**.
Prefer `add_inst` for new work — `add_comp` is retained for backward
compatibility and for cases where the bounding box differs from the cell's
canonical size.  Use `−` as the parent for a root (depth-0) instance.

| Argument | Type | Default | Description |
|---|---|---|---|
| `name` | str | — | Unique instance path, e.g. `di` or `ai/a3i`. |
| `cell` | str | — | Cell type name, e.g. `d`. |
| `parent` | str | — | **Instance path** of the parent (e.g. `u_cpu/core0`), not the cell name. Use `-` for a root instance. |
| `x1 y1` | float | — | Lower-left corner in µm. |
| `x2 y2` | float | — | Upper-right corner in µm. |
| `leaf\|nonleaf` | keyword | `leaf` | `leaf` — stdcell / no children; `nonleaf` — hierarchical. |

`depth` is computed automatically as `parent.depth + 1` (or 0 for root).
Throws if `name` already exists or `parent` is not found. Triggers
`compute_hpwl()`. Does **not** expand `cell_children`.

**Example:**
```buda
# Add a new root module
add_comp di  d  -     1900 100 2000 300 nonleaf

# Add a child instance under di
add_comp di/x1i  x1  di   1910 150 1960 200 leaf
```

---

### `derive_busterms`

```
derive_busterms [<max_depth>]
```

Extract physical port locations (busterms) from the placed component hierarchy and write them to the BDB database.

| Argument | Description |
|---|---|
| `max_depth` | Optional. Maximum traversal depth for hierarchy derivation (defaults to `1`). |

**Example:**
```buda
derive_busterms 1
```

---

## 3. Python API

```python
import buda
db = buda.BDB("my_design.bdb")   # open or create
db = buda.BDB(":memory:")         # in-memory scratch
```

### Row types

All query methods return lists of typed row objects with read-write attributes.

**`ComponentRow`**

| Attribute | Type | Description |
|---|---|---|
| `id` | int | SQLite rowid |
| `name` | str | Full instance path, e.g. `ai/a1i1` |
| `cell` | str | Cell type, e.g. `a1` |
| `parent_id` | int | `id` of parent component; `−1` for root |
| `depth` | int | 0 for top-level instances |
| `x1, y1` | float | Lower-left corner (µm) |
| `x2, y2` | float | Upper-right corner (µm) |
| `is_leaf` | bool | True if no children (stdcell) |
| `is_replicated` | bool | True if part of a replicated group |
| `orient` | str | Instance orientation, one of `N/S/E/W/FN/FS/FE/FW` (default `'N'`); the bbox is the resulting axis-aligned extent (v13) |

**`NetRow`**

| Attribute | Type | Description |
|---|---|---|
| `id` | int | SQLite rowid |
| `name` | str | Qualified net name, e.g. `ab_bus`, `ai/w1` |

**`PinRow`**

| Attribute | Type | Description |
|---|---|---|
| `net_id` | int | References `NetRow.id` |
| `comp_id` | int | References `ComponentRow.id` |
| `pin_name` | str | Port name on the component |
| `dir` | str | `INPUT`, `OUTPUT`, `INOUT`, or `UNKNOWN` |
| `px, py` | float | Absolute pin position (µm); `−1` if unknown |

**`CellRow`**

| Attribute | Type | Description |
|---|---|---|
| `name` | str | Cell type name |
| `width` | float | Canonical width (µm) |
| `height` | float | Canonical height (µm) |

**`CellPinRow`**

| Attribute | Type | Description |
|---|---|---|
| `cell` | str | Cell type name |
| `pin_name` | str | Port name |
| `dir` | str | `INPUT`, `OUTPUT`, or `INOUT` |
| `px` | float | X offset from cell origin (µm); `−1` if unset |
| `py` | float | Y offset from cell origin (µm); `−1` if unset |

**`GrpRow`**

| Attribute | Type | Description |
|---|---|---|
| `id` | str | UUID-style string key |
| `name` | str | Display name |
| `color` | str | Color hint for the visualizer |
| `parent_id` | str | `id` of parent group; `""` for root |

---

### Ingestion

```python
db.import_def_lef(def_path, lef_path)
```
Parse DEF + LEF; clears all tables first. See script command above.

```python
db.import_verilog(v_path)
```
Elaborate Verilog hierarchy; preserves placement coordinates. See script
command above.

```python
gen = buda.BustermGen(db)
gen.derive(max_depth=1)
```
Extract physical port locations (busterms) from the placed component hierarchy and write them to the database.

---

### Cell definitions and hierarchy

```python
db.add_cell(name: str, w: float, h: float)
```
Define or overwrite a cell type.  Idempotent.

```python
rows: list[CellRow] = db.all_cells()
```
Return all defined cell types ordered by name.

```python
id: int = db.add_inst(inst_name, cell_name, parent_name, x, y)
```
Place one occurrence of `cell_name` at `(x, y)` relative to `parent_name`'s
origin (`parent_name=""` for absolute placement).  Eagerly expands
`cell_children` recursively.  Returns the new component row id.

```python
db.add_inst_to_cell(parent_cell, inst_name, child_cell, x, y)
```
Define the structural contents of `parent_cell`: one child occurrence of
`child_cell` at relative offset `(x, y)`.  Writes to `cell_children` only;
no component rows are created until `add_inst` places an occurrence of
`parent_cell`.

```python
db.add_cell_pin(cell, pin_name, dir="INOUT", px=-1.0, py=-1.0)
```
Define or update a port on a cell type.  `px`/`py` are offsets from the
cell's lower-left origin in µm; `−1` means unset (centroid fallback).

```python
rows: list[CellPinRow] = db.all_cell_pins()
```
Return all cell-type port definitions ordered by `(cell, pin_name)`.

```python
net_id: int = db.add_net_pins(net_name, drv, rcvs)
```
Add a net and derive instance-level pins from `"inst/path.pin_name"` endpoint
strings.  Propagates interface pins up the hierarchy to each ancestor strictly
between the leaf and the common ancestor of all endpoints.  `rcvs` is a
`list[str]`.  Idempotent for existing net names.  Driver pin is stored as
`OUTPUT`; receiver pins are stored as `INPUT`.

```python
net_id: int = db.add_net_pins_undirected(net_name, pins)
```
Like `add_net_pins` but stores every pin (leaf and ancestor interface) with
`dir="UNKNOWN"`.  Use when direction is not known at script time — for example
for nets declared with `add_net … unknown`, or for programmatic use after
`import_verilog`.  `pins` is a `list[str]` of `"inst/path.pin_name"` strings;
the first entry becomes the positional driver when `run_hier_bundler` applies
its UNKNOWN fallback rule.

```python
net_id: int = db.add_net_pins_inout(net_name, pins)
```
Like `add_net_pins_undirected` but stores every pin with `dir="INOUT"`.
Use for explicitly bidirectional nets (`add_net … inout`).  `run_hier_bundler`
treats INOUT as a secondary driver: the first entry drives when no `OUTPUT` pin
exists, otherwise all INOUT pins are receivers.

---

### Mutations

```python
db.set_die(w: float, h: float)
```
Explicitly set die dimensions and persist them to the `meta` table.  When
`_die_w` is 0 (unset), `die_w()` / `die_h()` automatically fall back to the
`MAX(x2)` / `MAX(y2)` of all placed components.

```python
db.move_comp(name: str, x: float, y: float)
```
Move instance `name` to origin `(x, y)`, preserving size.

```python
db.resize_cell(cell: str, w: float, h: float)
```
Update the `cell` table and set `x2 = x1 + w`, `y2 = y1 + h` for every
`component` instance of that cell type.

```python
db.flip_comp(name: str, flip_x: bool)
```
Mirror the component subtree rooted at `name`.  `flip_x=True` mirrors
left-right (about the vertical centre); `flip_x=False` mirrors up-down.
The root's bounding box is unchanged; all descendants are repositioned.

```python
db.rotate_comp(name: str, degrees: int)
```
Rotate the component subtree rooted at `name` by `degrees` CCW (90, 180,
or 270).  The root's lower-left corner is fixed.  For 90° and 270° the
root's width and height are swapped.

```python
id: int = db.add_comp(name, cell, parent_name, x1, y1, x2, y2, is_leaf=True)
```
Insert a new component using explicit absolute coordinates.
`parent_name=""` for a root instance. Does not expand `cell_children`.
Returns the new row's `id`.

---

### Computed properties

```python
db.compute_hpwl()    # update net_props.hpwl for every net
db.compute_fanout()  # update net_props.fanout for every net
db.compute_all()     # both of the above
```

These write into `net_props`. Call after any mutation or import that changes
topology. The mutation methods (`move_comp`, `resize_cell`, `add_comp`) call
`compute_hpwl()` automatically.

---

### Queries

```python
rows: list[ComponentRow] = db.all_components()
rows: list[NetRow]       = db.all_nets()
rows: list[PinRow]       = db.all_pins()
rows: list[BustermRow]   = db.all_busterms()
rows: list[BundleRow]    = db.all_bundles()
```

```python
names: list[str] = db.nets_by_hpwl(lo: float, hi: float)
```
Return net names whose HPWL falls in `[lo, hi]` µm, ordered by HPWL descending.
Requires `compute_hpwl()` or `compute_all()` to have been called.

```python
names: list[str] = db.comps_in_rect(xl, yl, xh, yh: float)
```
Return instance names whose bounding box overlaps the query rectangle
`(xl, yl)–(xh, yh)`. Overlap test: `x1 < xh and x2 > xl and y1 < yh and y2 > yl`.

```python
nets: list[str] = db.common_nets(bundle_id1: str, bundle_id2: str)
```
Return net names shared between two bundles. Used by the congestion planner.

---

### Group management

Groups are hierarchical labels applied to components, nets, or busterms for
visualizer colouring and selection.

```python
gid: str = db.new_group(name: str, color: str, parent_id: str = "")
db.add_grp_member(gid, kind, ref)     # kind: "comp"|"net"|"busterm"
db.remove_grp_member(gid, kind, ref)
db.delete_group(gid)
rows: list[GrpRow] = db.all_groups()
```

---

### Metadata

```python
db.units()  → int    # DEF UNITS DISTANCE MICRONS value (e.g. 1000)
db.die_w()  → float  # die width in µm
db.die_h()  → float  # die height in µm
```

```python
db.meta_get(key, def="") → str   # read a meta(key,value) row ('schema_version', 'bdb_tool', …)
db.meta_set(key, value)          # write (upsert) a meta row — design-level key/value store
```

```python
path: str = buda.BDB.db_path(def_path)  # static: replaces .def extension with .bdb
```

---

## 4. Typical workflows

### DEF + Verilog merge (most common)

```buda
open_bdb  flow/lefdef/gcd/gcd.bdb
import_def_lef  flow/lefdef/gcd/gcd.def  flow/lefdef/gcd/gcd.lef
import_verilog  flow/lefdef/gcd/gcd.v
```

This populates placements from DEF and overlays the hierarchy from Verilog.
Components in the DEF that are not present in the Verilog keep their
placement but have no parent/depth set. Components in the Verilog that are
not present in the DEF get `x1=y1=x2=y2=−1`.

### Manual placement from scratch

All coordinates are **absolute µm**. When nesting instances, add the
parent's origin to get the child's absolute position.

```buda
open_bdb  flow/manual/my_design.bdb

# Depth 0 — top-level blocks
add_comp  u_cpu  cpu  -       0    0  500 400 nonleaf
add_comp  u_mem  mem  -     600    0 1100 400 nonleaf

# Depth 1 — mid-level blocks; parent = instance path, not cell name
#   u_cpu origin (0,0): core0 at local (50,50)  → absolute (50,50)
#   u_cpu origin (0,0): core1 at local (250,50) → absolute (250,50)
add_comp  u_cpu/core0  core  u_cpu   50  50 200 200 nonleaf
add_comp  u_cpu/core1  core  u_cpu  250  50 400 200 nonleaf

# Depth 2 — leaf cells; parent = instance path of the enclosing instance
#   core0 origin (50,50): c1 at local (10,10) → absolute (60,60)
#   core0 origin (50,50): c2 at local (80,80) → absolute (130,130)
add_comp  u_cpu/core0/c1  c  u_cpu/core0   60  60 120 120 leaf
add_comp  u_cpu/core0/c2  c  u_cpu/core0  130 130 190 190 leaf
```

### Cell-based hierarchy (most compact)

Define cell sizes and structure once; `add_inst` places top-level occurrences
and the engine automatically expands the full subtree.

```buda
open_bdb :memory:

# 1. Define cell sizes
add_cell top  1000 800
add_cell blk   300 230
add_cell sub   120  90
add_cell leaf   45  50

# 2. Define structure (no component rows yet)
add_inst_to_cell  top  u1  blk   50   50
add_inst_to_cell  top  u2  blk  400   50
add_inst_to_cell  top  u3  blk   50  330
add_inst_to_cell  top  u4  blk  400  330

add_inst_to_cell  blk  s1  sub   15  15
add_inst_to_cell  blk  s2  sub  155  15
add_inst_to_cell  blk  s3  sub   15 125
add_inst_to_cell  blk  s4  sub  155 125

add_inst_to_cell  sub  l1  leaf  10  20
add_inst_to_cell  sub  l2  leaf  65  20

# 3. Place the single top-level occurrence; 1+4+16+32 = 53 rows created
add_inst chip  top  -  0  0

# 4. Feed the leaf cells (depth 3) to the routing engine
add_blocks_from_bdb 3 skip
```

### Post-import fixup

```buda
open_bdb  flow/lefdef/gcd/gcd.bdb
import_def_lef  flow/lefdef/gcd/gcd.def  flow/lefdef/gcd/gcd.lef
import_verilog  flow/lefdef/gcd/gcd.v

# Move one instance that landed outside the die area
move_comp  u_regfile  10  10

# Update all stdcells whose LEF had a stale size
resize_cell  DFFRX1  5.6  4.0
```

### Python snippet

```python
import buda

db = buda.BDB("flow/lefdef/gcd/gcd.bdb")
db.import_def_lef("flow/lefdef/gcd/gcd.def", "flow/lefdef/gcd/gcd.lef")
db.import_verilog("flow/lefdef/gcd/gcd.v")
db.compute_all()

# Find the ten highest-HPWL nets
hot_nets = db.nets_by_hpwl(0, 1e9)[:10]
print("Hot nets:", hot_nets)

# Find everything inside a congested region
crowded = db.comps_in_rect(200, 100, 400, 300)
print("Crowded region:", crowded)

# Nudge one instance
db.move_comp("u_alu/fa_3", 305.0, 120.0)
```

---

## 5. Notes and caveats

**UPSERT semantics during `import_verilog`**  
When called after `import_def_lef`, `import_verilog` does an
`INSERT … ON CONFLICT DO UPDATE` on the `component` table. The UPSERT updates
`cell`, `parent_id`, `depth`, and `is_leaf` but leaves `x1/y1/x2/y2`
untouched, so physical placement from the DEF is preserved.

**`last_insert_rowid` after UPSERT**  
SQLite's `last_insert_rowid()` does not reliably return the updated row's id
when the UPSERT resolves as an UPDATE — it returns the rowid of the last
actual INSERT on the connection, which may be from a prior transaction.
`import_verilog` always does a `SELECT` after each UPSERT to get the
canonical component id (see `bdb.cpp::upsert_comp`).

**`add_comp` vs. `import_verilog`**  
`add_comp` uses a plain `INSERT` (no UPSERT), so it throws on a duplicate
name. Use it to add brand-new instances. If you need to update an existing
component's hierarchy fields, re-run `import_verilog`.

**`add_inst_to_cell` ordering constraint**  
`add_inst_to_cell` only writes to `cell_children`; expansion happens eagerly
when `add_inst` is called.  All `add_inst_to_cell` calls for a cell must
therefore appear in the script *before* the `add_inst` that places an
occurrence of that cell.  Adding structure to a cell after its occurrences
have already been placed has no effect on the existing component rows.

**`add_inst` vs `add_comp`**  
Prefer `add_inst` for new designs — it reads cell dimensions from the `cell`
table and supports eager expansion via `cell_children`.  `add_comp` accepts
explicit absolute coordinates and is retained for backward compatibility and
for cases where the placed bounding box differs from the canonical cell size.
`add_comp` does **not** expand `cell_children`.

**WAL mode**  
The database is opened with `PRAGMA journal_mode=WAL`. This creates
`<path>-wal` and `<path>-shm` sidecar files while the connection is open.
They are merged back into the main file on clean close. Delete them only if
the process was killed mid-write and you want to roll back the last
transaction.

**Coordinate units**  
All coordinates stored in BDB are in **µm** regardless of the DEF
`UNITS DISTANCE MICRONS` value. `import_def_lef` converts on read.
`add_comp` and `move_comp` accept µm directly.

---

## 6. Design interchange formats

BUDA reads industry netlist/layout formats directly into BDB with
**hand-written parsers** in `bdb.cpp` — there is no dependency on OpenDB,
Cadence OpenAccess, or any Si2 library. This keeps the build self-contained
(only pybind11 + bundled SQLite) at the cost of supporting a pragmatic subset
of each format. This section documents what is parsed today and the intended
shape of the planned export/round-trip formats.

| Format | Direction | Status | Entry point |
|---|---|---|---|
| LEF | import | ✅ supported (subset) | `import_def_lef` |
| DEF | import | ✅ supported (subset) | `import_def_lef` |
| Verilog (structural) | import | ✅ supported (subset) | `import_verilog` |
| GDSII | import + export | 🚧 planned | — |
| OpenAccess | import + export | 🚧 planned | — |

### Supported today: LEF/DEF + Verilog

These two importers are meant to be run **in sequence** (`import_def_lef` then
`import_verilog`) to merge physical placement with logical hierarchy. Both are
forgiving line-by-line state machines; unrecognized constructs are skipped
rather than erroring.

**LEF (`_parse_lef_sizes`, `_parse_lef_pins`)** — what is consumed:

| LEF construct | Use in BDB |
|---|---|
| `MACRO <name> … END <name>` | one `cell` row |
| `SIZE <w> BY <h> ;` | cell footprint (`cell.width/height`) |
| `PIN <name> … END <name>` | one `cell_pin` port |
| `DIRECTION INPUT\|OUTPUT\|INOUT ;` | pin direction (absent → `UNKNOWN`) |
| `USE POWER\|GROUND\|CLOCK ;` | **pin skipped** (pre-route, not a signal terminal) |
| `RECT x1 y1 x2 y2 ;` | pin offset = **centroid of all its RECTs** |

Everything else (layers, vias, OBS, antenna, properties, units header) is
ignored. The LEF supplies cell *sizes* and *pin offsets/directions* only.

**DEF (`import_def_lef`)** — a three-state machine
(`IDLE → IN_COMPONENTS → IN_NETS`):

| DEF construct | Use in BDB |
|---|---|
| `UNITS DISTANCE MICRONS <n> ;` | integer→µm divisor (`units()`); everything stored as µm |
| `DIEAREA ( 0 0 ) ( x y ) ;` | `die_w` / `die_h` |
| `- <inst> <cell> + PLACED\|FIXED ( x y ) <orient>` | depth-0 leaf `component`; bbox = DEF origin + LEF `SIZE` (fallback `0.5×0.5` if the cell is missing from the LEF); `<orient>` recorded in `component.orient` (dims swapped for 90/270) |
| `- <net> … ( <inst> <pin> ) …` | `net` + `net_props` row, and one `pin` row per connection with absolute position + direction resolved from the LEF |

DEF name escaping (`\[`, `\]`) is stripped so instance names match the
Verilog-elaborated paths. Component **orientation is recorded** in
`component.orient` (v13): the DEF token maps to BDB's orient convention (DEF's
pure rotations N/W/S/E coincide; the flip tokens permute because DEF mirrors
about the Y axis while BDB mirrors about X — DEF `FN`↔BDB `FS`, `FS`↔`FN`,
`FE`↔`FW`, `FW`↔`FE`) and the placed bbox dims swap for the 90/270
orientations. Bounding boxes are still axis-aligned (the box's extent, not
rotated interior geometry), so the placement round-trips through GDS export.
`import_def_lef` **clears** the `pin`/`net_props`/`net`/`component`/`cell`
tables first: it is a fresh load, and the produced components are all depth-0
with no parent until Verilog overlays the hierarchy.

**Verilog (`import_verilog`)** — structural netlist elaboration:

- **Top detection:** the top module is the last module *not instantiated by
  any other module* in the file — no explicit top argument.
- **Parsing:** instance statements `cell inst ( .port(net), … );` and port
  direction declarations (`input/output/inout`). The custom `parse_portmap`
  handles `\`-escaped identifiers, bit-selects (`d[3:0]` → base `d`),
  constants / concatenations / `UNCONNECTED` (skipped), and nested parentheses.
  A Verilog keyword set filters out behavioral statements.
- **Elaboration:** walks from the top module, creating hierarchical
  `component` rows with dotted `parent/child` paths and increasing `depth`,
  and wiring `net`/`pin` rows from the port maps. Instance pins start as
  `UNKNOWN` and are overridden from any matching `cell_pin` direction.
- **Merge semantics (UPSERT):** when run after `import_def_lef`, it updates
  `cell`/`parent_id`/`depth`/`is_leaf` but **preserves `x1..y2`**, so DEF
  placement survives. Verilog-only components get `x1=y1=x2=y2=−1` (unplaced);
  DEF-only components keep placement with no parent/depth. See §5
  *UPSERT semantics* for the `last_insert_rowid` caveat.

> **Subset caveats.** No support for: DEF `SPECIALNETS`/routing geometry,
> `BLOCKAGES`, `REGIONS`, `GROUPS`; LEF routing/via rules; Verilog `generate`
> blocks, parameter elaboration, `assign` aliasing, or hierarchical port
> bit-blasting beyond simple base-name extraction. These are intentionally
> out of scope for an interconnect-*planning* tool.

### GDSII import/export

**Status: IMPLEMENTED (Phases G0–G4)** — see [`import_gds`](#import_gds),
[`export_gds`](#export_gds), and the phased plan in
[`docs/internal/gds_oa_interchange.md`](internal/gds_oa_interchange.md):
geometry/hierarchy (G1), label-based net recovery (G2), layer mapping with
routing-shape exclusion (G3), and export with a tested import↔export
round-trip (G4). The OA bridge remains spec-only (gated on the Si2 SDK).

Intended capability: round-trip a **GDSII** layout against BDB — export the
placed-and-routed result for sign-off/viewing (KLayout, etc.) and import an
existing layout's geometry back into BDB.

**Export** — sketch of the intended design:

- **Inputs:** BDB `component` bounding boxes (cell outlines / blockages) plus
  the routed wires — abstract NUTS `BusSegment`s or, preferably, detailed-NUTS
  `NetSegment`s (one polygon per bit-wire) keyed by layer.
- **Layer mapping:** a `LayerStack` → GDS `(layer, datatype)` table so each
  metal layer and each pre-route class (POWER/GROUND/CLOCK/SHIELD/SIGNAL from
  the `RoutingGrid`) lands on a distinct GDS purpose.
- **Hierarchy:** either flatten to a single top cell, or emit one GDS
  `structure` per BDB cell type with `SREF`/`AREF` placements mirroring the
  component hierarchy (the latter reuses the template-per-cell-type model the
  hier flow already builds).

**Import** — the inverse, populating the same BDB tables as the other
importers:

- **Shapes:** `BOUNDARY` / `BOX` records become cell or blockage geometry;
  `SREF` / `AREF` placements rebuild the `component` hierarchy (dotted paths,
  depth) the same way Verilog elaboration does.
- **Layer mapping:** the export `(layer, datatype)` table inverted to recover
  the BUDA layer / pre-route class of each shape.
- **Connectivity is optional (file-dependent):** GDS has no standard netlist,
  but a given GDS *may or may not* carry physical connectivity. Both cases are
  in scope:
  - *Connectivity present* — many flows annotate shapes with net names via
    `TEXT` / label records (on a pin or label `(layer, datatype)`) or a known
    labeling convention. When such labels exist, the importer parses them to
    recover `net` / `pin` rows directly from the GDS.
  - *Geometry only* — no labels: import placement and shapes, then pair with
    `import_verilog` for nets, exactly as DEF placement is paired with Verilog
    hierarchy today (see §4 *DEF + Verilog merge*).

  A mode flag (or auto-detect on the presence of label records) chooses per
  file; the two paths converge on the same BDB tables.
- **Units:** GDS stores integers scaled by the `UNITS` record
  (`user-units / database-unit`); convert to **µm** on read, as `import_def_lef`
  does for DEF DBUs.

### Planned: OpenAccess import/export

**Status: not implemented — design intent only.** No OA code exists in the
tree.

Intended capability: round-trip designs through a **Si2 OpenAccess** database
(`oaDesign`/`oaBlock`/`oaInst`/`oaNet`/`oaTerm`) so BUDA can drop into an
OA-based production flow — import placed instances + connectivity into BDB,
run the planning pipeline, and write routed geometry back into the OA design.

Because OpenAccess ships as **proprietary C++ libraries** that cannot be
vendored, the implementation would:

- live in a **separate translation unit** (e.g. `oa_bridge.cpp`) behind an
  **optional CMake feature flag** (`BUDA_ENABLE_OPENACCESS`), so the default
  build keeps zero external EDA dependencies;
- be **dlopen-isolated** from `buda_core` — the OA SDK is found at configure
  time and only the bridge module links against it;
- translate OA objects ↔ the same BDB tables documented in §1, normalizing all
  coordinates to **µm** (OA stores in DBU; convert using the tech `oaDBUPerUU`).

Until OA support lands, the supported interchange path is **LEF/DEF + Verilog
in, with GDS import/export once available**.

For committing BDBs as reviewable test data — and the schema-versioning /
provenance / routing-write-back groundwork that the OA/GDS export will build on —
see [BDB Test-Data Management](internal/bdb_test_data.md). Its `*.bdb.sql` text
dump (`tools/bdb_serialize.py`) is the diffable, version-controllable form of a
BDB.
