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
   - [GDSII import/export](#gdsii-importexport)
   - [Planned: OpenAccess import/export](#planned-openaccess-importexport)

---

## 1. Schema overview

```
component        id, name, cell, parent_id→component, depth,
                 x1, y1, x2, y2, is_leaf, is_port (v23), is_replicated,
                 orient (v13)

cell             name (PK), width, height,
                 cls (LEF MACRO CLASS, v24), bottom_up (v17),
                 layer_cap, layer_floor (band, -1 = unset, v20)

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
                 drv_spec_path, rcv_spec_paths (JSON),
                 gen_knobs (generation-knob memo, v15),
                 is_expanded, bu_locked (v18), cloned_from (v19),
                 ndr_rule (governing rule stamped at persist, v21)
bundle_net       bundle_id→bundle, net_id→net, ord (bit order),
                 drv_path, rcv_paths (JSON) — per-bit fan-in/fan-out
                 endpoints, what makes the taper derivable on resume (v27)
                 PRIMARY KEY (bundle_id, net_id)
bundle_busterm   bundle_id→bundle, busterm_id, role ('entry'|'exit')

topology         bundle_id→bundle, cand_index, type, wirelength,
                 trunk_location, pass_through_count, connected_blocks (JSON),
                 feedthru_blocks (JSON), is_selected, is_pinned (v10),
                 topo_uid (stable content identity, v14),
                 source ('generated'|'user'|'dogleg', v15)
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

cell_layer_share cell→cell, layer_id, share (fraction of the layer's
                 signal tracks, v20)
                 PRIMARY KEY (cell, layer_id)

ndr_rule         name (PK), width_x, spacing_x (multipliers), shield_mode,
                 shield_per_n, shield_net, layers (CSV; '' = every layer),
                 credit (R5a, v22), bond (R6; 0 = off, N = stride, v25),
                 width_abs, spacing_abs (R1 absolute, 0 = undeclared, v26),
                 per_layer, metal (v28)
                 — the declared rules (v21), RAW values only: the slot
                 quantization is a function of the CURRENT grid, so it is
                 re-derived on open and never stored
ndr_scope        prefix (PK), rule→ndr_rule  — '*' is the global default
                 scope; longest prefix wins (v21)

track_pattern    layer_id (PK), origin, is_horiz, bounded, bound_lo,
                 bound_hi, source ('script'|'lef'|'def'), slots (JSON)
                 — one layer's global pattern as DECLARED, not a read-back
                 of the built grid (v29)
grid_override    layer_id, x1, y1, x2, y2, origin, slots (JSON)
                 PRIMARY KEY (layer_id, x1, y1, x2, y2)
keepout          x1, y1, x2, y2, layers (CSV), inside_block, net
                 — the ZONE with its layer set, stored whole rather than
                 one row per (zone, layer) (v29)
                 PRIMARY KEY (x1, y1, x2, y2, layers)

grp              id (TEXT), name, color, parent_id→grp
grp_member       grp_id→grp, kind, ref

meta             key (TEXT PK), value  — die_w, die_h, units, lu_per_um,
                 schema_version, bdb_tool, verilog_top, layer_cap_default,
                 layer_caps_by_depth, bu_mismatch_policy,
                 user_ops:<bundle_id>:<topo_uid>
```

**30 tables, 232 columns** at schema v29.  A `bundle.ndr_rule` note for
anyone reading the DDL: that column is added by the v21 *migration* and is
absent from `BUNDLE_DDL`, so it exists in every database (a fresh one
migrates from 0) but cannot be found by reading the `CREATE TABLE` text
alone.

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
so GDS import→export→re-import preserves orientation; v14 added
`topology.topo_uid` + `topology_segment.edge_id` (stable candidate/MST-edge
identity); v15 added `topology.source` + `bundle.gen_knobs`; v16 added
`topology_segment.perp_clamp_lo/hi`; v17 added **`cell.bottom_up`** (bottom-up
template planning flag); v18 added `bundle.is_expanded` + `bundle.bu_locked`
(planner-expanded / bottom-up-locked row provenance); v19 added
**`bundle.cloned_from`** (rotation-class clone template provenance — see
[`set_bottom_up`](#set_bottom_up)); v20 added the **per-cell layer policy** —
`cell.layer_cap` / `cell.layer_floor` (the band `[floor..cap]`, `-1` = unset)
plus the `cell_layer_share (cell_id, layer_id, share)` table and the
`layer_cap_default` meta key for the `*` default — see
[`set_cell_layer_cap`](#set_cell_layer_cap); v21 added **NDR rule
persistence** — the `ndr_rule` table (declared rules, raw multiplier values:
name, width_x, spacing_x, shield_mode, shield_per_n, shield_net, layers CSV),
the `ndr_scope` table (prefix → rule attachments; `*` = the global default),
and `bundle.ndr_rule` (the governing rule stamped per persisted bundle).
`def_ndr`/`set_ndr` write through when a BDB is open; `open_bdb` restores
rules + scopes (session-typed entries win, a previous BDB's restored entries
drop), and `load_pipeline` VOIDs (LOUD, re-plan required) any restored plan
whose governing rule changed — by name OR content — since the checkpoint
(see docs/internal/ndr_architecture.md §4/§7).  v22 added
**`ndr_rule.credit`** (the R5a end-shield rail-crediting opt-in — pricing
basis, so it joins the rule row and, when set, the `|c1` fingerprint
suffix; pre-v22 rules migrate to 0, correct since they never credited).
v23 added **`component.is_port`** — a DEF `PINS` die port kept as a
zero-area boundary component.  Reading `PINS` does not by itself make a
port an endpoint (endpoint derivation is keyed by component), so each
placed port becomes a component; the flag is what keeps that fiction
VISIBLE to the database and to the audits instead of passing as a real
instance.  Pre-v23 designs have no ports, so the 0 default is correct.
v24 added **`cell.cls`** — the LEF `MACRO … CLASS` token.  It is the only
authoritative answer to "is this a hard macro or a standard cell", and
`import_verilog` needs it to decide which instances of undefined modules
belong in the routing hierarchy — which is what carries a hard macro
through a DEF + Verilog merge however its instance name is spelled.  Pre-v24
designs never recorded it, so `''` (not stated) is correct.
v25 added **`ndr_rule.bond`** (the R6 shield-bonding opt-in).  Unlike
`credit`, bonding is OUTPUT-only — it emits extra `net_via` straps and
moves neither demand nor placement — so it is deliberately NOT part of
the `bundle.ndr_rule` pricing fingerprint: toggling it must not VOID a
restored plan.  A strap reuses `net_via` with a NEGATIVE `to_seg` (the
strap ordinal; a real segment index is `>= 0`, so the
`(bundle_id, from_seg, to_seg, bit_index)` primary key stays unique) —
its far end is a power-grid rail, not a routed segment.  Pre-v25 rules
migrate to 0, correct since they never bonded.
v26 added **`ndr_rule.width_abs` / `spacing_abs`** (R1 absolute width and
spacing, in layout units; 0 = not declared).  They are persisted because an
absolute declaration leaves the MULTIPLIER at 1.0 — without them a reopened
design restores the rule as DEFAULT width, usually inactive, silently losing
the constraint the design was routed under.  The derived QUANTIZATION (slots
per bit, guards per gap) is deliberately NOT stored: it is a function of the
CURRENT grid, so the same rule against a different stack is a different slot
count and a stored one would be charged against geometry it never measured.
`open_bdb` re-derives it, and says so loudly when the governed layers carry
no track pattern in the new session.  Pre-v26 rules migrate to 0, correct
since they were multiplier-only.
v27 added **`bundle_net.drv_path` / `rcv_paths`** — the per-bit endpoints of a
fan-in / fan-out bundle (`HBundle::net_drivers[ord]` /
`net_receivers[ord]`), on the row that already carries the membership so the
bit alignment cannot drift from it.  They are what makes the per-bit taper
(`Topology::seg_bits`) derivable on resume; without them a restored fan-in
came back UNTAPERED — every segment carrying every bit — and routed wider
than the design that was checkpointed while reporting itself clean (see
[`load_pipeline`](#load_pipeline)).  Empty for every other bundle, and empty
for a pre-v27 checkpoint, which resumes exactly as it did and says so
(BUDA-1904).
v28 added **`ndr_rule.per_layer` / `metal`** — per-layer declared values
(`''` = no entries, so a pre-v28 rule restores as the layer-independent
rule it was) and the width-anchoring reading (`metal` anchors a declared
absolute width to the metal rather than to the per-signal-slot channel).
The `metal` flip was measured and REFUSED as a default, so it is per-rule
and DECLARATION-ONLY: a restored rule keeps the reading it was persisted
with (see docs/internal/ndr_metal_default_study.md).
v29 added the **routing grid** — the `track_pattern` table (one row per
layer: origin, direction, bounds, the slot list as JSON, and `source`), the
`grid_override` table (region-scoped patterns, keyed by layer + region) and
the `keepout` table (the zone, its layer set as a CSV, `inside_block` and
the SPECIALNET `net`).  These were the last physical-design facts with no
table: pure session state, rebuilt by whoever declared them.  When that
"whoever" is `import_def_lef`, a hier stage-resume loses them, because it
HOLDS the import — a replayed `add_inst` is a duplicate-instance error.
`run_detailed_nuts requires a routing grid` was the visible symptom and the
only loud moment; the quiet half is worse, since `run_nuts` and the healers
run on before it, so a `plan` resume re-solves against the `def_layer`
overhead figure instead of the pattern's own pitch and against no
obstruction at all (measured on `flow/ariane133`: 20 keepout-seated
segments in the build, 18 in the resume).

What is stored is the **declaration**, not a read-back of the built grid, so
a restore replays `define_layer` / `add_override` / `add_keepout` verbatim.
`track_pattern.source` (`script` | `lef` | `def`) is the persisted form of
the session's provenance memo, and it is what makes precedence survive the
round trip — "an explicit `def_track_pattern` outranks imported data in
either order" is a rule about WHO declared a value, which a pattern alone
cannot say.  A keepout is stored as the ZONE, layer set included, rather
than one row per (zone, layer): `set_keepout_loci` reasons per zone, and
splitting it would lose the object the rule is about.  Rows are keyed by
geometry, so re-running a flow against the same BDB upserts instead of
accumulating, and a burst is written in one transaction — a DEF import
declares thousands at once (5,472 on `demo/ariane`, ~0.01 s).

`open_bdb` restores all three beside the layer-policy and NDR restores, and
re-derives the layer facts the pattern feeds (dilution, bit pitch, NDR
geometry) — without those the width model falls back to the overhead figure,
which is the silent half of the divergence.  A flow that opens a checkpoint
and then re-declares its own patterns is NOT a duplicate declaration: that
error is about a flow contradicting itself, not about one re-declaring what
its checkpoint handed back.  A pre-v29 checkpoint carries no grid rows and
cannot be fixed retroactively, so `load_pipeline` says so (BUDA-1503) when
it restores a routed design into a session with no grid.

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
`*.bdb.sql` diff, and it is the natural feed for BDB → GDS export (and the
planned OA export).
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

**coordinates** are in *layout units*, and one layout unit is whatever the
design's **import scale** says it is — microns by default. `import_def_lef`
converts DEF internal units using the `UNITS DISTANCE MICRONS` value from the
DEF header, then applies the scale; LEF numbers (already µm) get the scale
alone. The factor is recorded as meta `lu_per_um` and restored on open, so a
reopened design knows what its own numbers mean. Unresolved pin positions are
stored as `−1`.

At the default scale of `1.0` a coordinate is a micron and the engine's
integer grid quantizes to 1 µm — ~2000 DBU on an advanced node, roughly 20-25
track pitches. Setting `set_import_scale dbu` makes one layout unit one DEF
database unit, so the import is exact and nothing is quantized away. See
[the coordinate contract](internal/engine_units.md).

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
| `path` | File path for the `.bdb`; created if it does not exist. A `*.bdb.sql` text fixture is materialized to a temp binary. Use `:memory:` for an in-memory scratch database. A path containing **spaces** is quoted (`open_bdb "my designs/ck.bdb.sql" writeback`, or `open_bdb "my designs/ck.bdb"` on its own) — see [Paths, and paths with spaces](BUDA_SCRIPT_REFERENCE.md#paths-and-paths-with-spaces). |
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
expanded instance persists its selected topology **plus any per-instance
USER candidates** (a TopoEdit commit on one instance without `pin` lands as
an extra `source='user'` row — both alternative hand shapes survive the
resume, and un-edited instances still persist exactly one row), each at its
template `cand_index`, so the selection is remapped to the compact
in-memory list.
For **bottom-up** designs the pre-expansion TEMPLATE wrappers are restored
alongside (from the template bundle rows referenced as parents by the
expanded rows, validated against their own cell-local floorplans, with the
persisted local-solve selection as a full pin): a checkpoint taken
**before `run_nuts`** then re-runs the cell-local solve on resume and keeps
uniform per-instance copies, while a **post-`run_nuts`** checkpoint keeps
sourcing the fixed copies from the persisted routing (exact; the preference
ends at the next re-plan). Re-running `run_planner hier` on a resumed
post-expansion session remains unsupported.

Not restored: `seg_perp` (a NUTS placement *preference* from the planner's
charged bands), planner band state, overlap details.

**The per-bit fan-in taper IS restored** (v27). `Topology::seg_bits` is
derived at *generation* and by no load path, so a restored CONVERGENT /
DIVERGENT fan-in used to come back with an empty map — and an empty map means
*every segment carries every bit*, the untapered tree. The resumed design was
still clean; it was simply **wider than the one that was saved**, and nothing
said so. Measured on `flow/tcl/array_save.tcl` + `array_resume.tcl` (2 x 2, a
checkpoint and a straight continuation of it): every plain bundle round-tripped
bit-for-bit while the two fan-in bundles grew — b1 16 → 18 bit-wires, b10 32 →
48, total 88 → 106 — with both endpoints reporting 0 overlaps / 0 unplaced / 0
violations. Everything downstream moves with the taper: planner band charging
(`congestion_planner.cpp` prices `seg_bits` when non-empty), abstract NUTS
widths, DNUTS emission.

What closes it is `bundle_net.drv_path` / `rcv_paths` (v27): the per-bit
endpoints `HBundle::net_drivers[i]` / `net_receivers[i]`, on the same row the
membership uses so the bit alignment is the same `ord`. They are **stored, not
re-derived at load**, because the driver/receiver roles they encode come out of
a subtle pass in the bundler (deepest OUTPUT, path-maximal receivers,
INOUT/UNKNOWN fallbacks, extra-driver attachment) that a second implementation
in the loader would drift from. `_restore_wrapper` then re-derives `seg_bits`
on every restored candidate (`_retaper_fanin`), asking the frame which spelling
of the endpoint names it recognises — as stored, or by leaf for a cell-local
template — rather than re-deciding which hier case this is. Same continuation,
same `route_snapshot` hash: 9644 WL / 88 bit-wires on both sides of the round
trip, where the untapered resume gave 13895 / 106.

A checkpoint written **before v27** has no endpoints to restore and resumes
untapered exactly as it did, which is reported (**BUDA-1904**) rather than left
to be noticed as a mysteriously wider design; re-run the bundler and
re-checkpoint to store them. If neither spelling of a stored endpoint matches
the frame, the taper is left underived and reported the same way — guessing a
mapping could drop a bit's wire.

TEG-over bridge segments
**are** restored (`topology_bridge_segment`, v11), so TEG-over multi-rect
designs resume losslessly. `ripup_reroute` and `run_nuts_on_layer` both
**commit** their final routing via the `_checkpoint_routing()` choke point
(planner output + NUTS + detailed rows), so a checkpoint after either resumes
from the re-solved routing, not stale rows. The visualizer's interactive
rerun buttons (↺ / Re-run & Refresh) are deliberately **pure previews** — a
checkpoint changes only on explicit commands, never while exploring.
Tests: `test/tests/test_bdb_resume.py`, `test/tests/test_bdb_resume_gaps.py`.

---

### `set_import_scale`

```
set_import_scale micron|dbu|<layout units per micron>
```

Declare what a layout unit means for this design, **before** `import_def_lef`.
With no argument, print the current scale.

| Value | Meaning |
|---|---|
| `micron` | **Default.** 1 layout unit = 1 µm. Historic behaviour, bit-identical. |
| `dbu` | 1 layout unit = 1 DEF database unit — an **exact** import with no quantization. Resolved from the DEF's own `UNITS DISTANCE MICRONS` at import time, so the script never has to know (or mis-state) the technology's DBU count. |
| *number* | An explicit factor, for a scale neither of the above expresses. |

The scale is applied at import and **only** at import: everything downstream
works in the chosen unit because there is nothing left to convert. It is
persisted as meta `lu_per_um` — as a *number*, even when selected as `dbu`, so
a later import into the same BDB cannot silently restate the stored
coordinates in a different unit.

Note what the scale does **not** touch: distances declared in the `.buda`
script (`corner_margin`, `set_min_stub_length*`, `detour_channel`,
`def_track_pattern` widths, …) are already in layout units and are taken as
written. Scale the import and you must scale those too — the
[unit-plausibility guard](script_reference/setup.md#set_unit_check) stops the
run when they disagree.

---

### `import_def_lef`

```
import_def_lef <def_path> <lef_path> [no_tracks] [no_blockages] [allow_missing_footprints]
```

Parse a DEF file for component placements and die dimensions, and a LEF file
for cell sizes and pin offsets. **Clears all existing tables** before import.

| Argument | Description |
|---|---|
| `def_path` | Path to the DEF file (VERSION 5.x). Must contain `UNITS DISTANCE MICRONS`, `DIEAREA`, and `COMPONENTS` sections. |
| `lef_path` | Path to the LEF file. `MACRO … SIZE … PIN …` entries are used; everything else is ignored. |
| `no_tracks` | Do not turn the DEF's `TRACKS` statements into track patterns. |
| `no_blockages` | Do not import obstruction — see [Obstruction and keepouts](#obstruction-and-keepouts) below. |
| `allow_missing_footprints` | Proceed when a component's cell has no LEF `MACRO`. Without it that is a hard error: the silent 0.5×0.5 µm fallback it replaced turned a wrong-LEF run into a plausible and entirely wrong design. |

The three option tokens are order-free and validated — a misspelling is a
flow-stopping error rather than a silently ignored word.

Both paths resolve against the script's own directory. Either may contain
**spaces** when quoted — `import_def_lef "rev 2/top.def" "rev 2/top.lef"` —
which is the only way to state where one ends and the next begins; see
[Paths, and paths with spaces](BUDA_SCRIPT_REFERENCE.md#paths-and-paths-with-spaces).

After import: `component` rows have `x1/y1/x2/y2` from the DEF placement
plus the LEF `SIZE`, but `parent_id` and `depth` are `NULL`/0 until
`import_verilog` is run.

#### Obstruction and keepouts

A router that cannot see a blockage plans through it, so the import turns the
DEF's obstruction into the same keepouts `add_keepout` declares, on both
consumers: the Floorplan (which feeds the planner) and the RoutingGrid (which
feeds DetailedNUTS).

**What becomes a keepout**

| DEF construct | one keepout per | carries |
|---|---|---|
| macro `OBS` (from the component's LEF `MACRO`, for a **placed** instance — see below) | rect | `inside_block` — whether the rect lies within that instance's placed extent. **Measured, not assumed**: LEF does not require an `OBS` rect to sit inside `SIZE`, and one that pokes out is exactly the one whose edge is a useful Hanan locus. |
| `BLOCKAGES` … `LAYER <layer>` | rect | `inside_block` — measured, as above |
| `SPECIALNETS` routed metal (power straps) | polyline segment | `inside_block` — measured, as above — and the strap's **net**. The net is what lets the NDR rail predicates tell a `VDD`/`GND` rail from anonymous obstruction; a LEF states a wire's width and never says which tracks the power grid takes, so on an imported design the rails are in `SPECIALNETS` and nowhere else. |

`inside_block` is **measured for all three**, against the placed extents of
the design's own components. It was once hardcoded `false` for everything but
`OBS`, which made `set_keepout_loci outside` answer its own question from the
flag rather than from the geometry — so a PDN's macro-local straps, drawn
*over* the macros by pdngen's `-macro` grids, had the loci of none of them
suppressed. Measuring it is not optional cleanliness: a macro grid is declared
with a halo, so those straps are built to extend slightly past the macro they
belong to, and the one that pokes out is the one whose edge is a useful locus
— the same reason `OBS` has never been allowed to assume it.

**What "inside a block" means here**, precisely: inside a **placed
component** of the design. That is measured at import time, and which
components a flow later projects into the `Floorplan` is not known then —
`add_blocks_from_bdb <depth> [deepest|skip|error]` runs afterwards and may
load only some depths. So on a flow that projects a subset, the flag is
answered against a superset of the blocks. It does not make a suppressed
locus unsound: `set_keepout_loci outside` removes reachable candidate
positions **by design** (a trunk may cross a block over-the-cell), which is
why it is opt-in and not a free win — the block's own edges were never a
promised replacement. Worth knowing when reading the flag's name.

One component kind is absent from the test on the other side: a **die-port
boundary component** (`PINS`, `is_port`) is synthesized *after* the
`COMPONENTS` stream, so it is not among the extents containment is measured
against. On the DEFs here that changes nothing — a port is pin-sized and no
strap or blockage fits inside one — but that is an observation about these
designs, **not a guarantee**: a port's bbox is the union of all its `RECT`
shapes, so a DEF declaring a large pad-like pin can enclose a narrow strap or
blockage, which then keeps its loci under `set_keepout_loci outside`. The
error is one-directional — a keepout is never wrongly *suppressed*, only
never suppressed — so it costs grid rather than candidate positions, and it
fails in the safe direction. Recorded rather than left to be rediscovered,
since nothing in the code says so.

**What does not** — each counted in the unmodelled census rather than applied,
so "not imported" is reported rather than silent:

| construct | census key | why not |
|---|---|---|
| component `+ HALO` | `COMPONENTS.HALO` | A halo keeps other **cells** away — placement information, carrying no layer. Mapping it onto every routing layer forbids routing the DEF left routable. `+ ROUTEHALO` is the routing construct, and that is the one BUDA ignores; the two were once the wrong way round (recorded, with the measured 195 → 0 track overlaps on `flow/ariane133`, in [opens_interchange.md](internal/opens_interchange.md) item 13). |
| `BLOCKAGES … + PLACEMENT` | `BLOCKAGES.PLACEMENT` | Says where **cells** may go, and carries no layer. |
| `BLOCKAGES … + PARTIAL <density>` | `BLOCKAGES.PARTIAL` | A density **cap**, not a prohibition. |
| macro `OBS` of an **`UNPLACED`** instance | `COMPONENTS.OBS_UNPLACED` | The obstruction is nowhere, so nothing can be emitted. Counted in **rects** — the quantity lost — because one `fakeram45_256x16` carries 99 of them and an instance count would understate it by two orders of magnitude. |

The census is reported as `BUDA-1603` (`[DEF] unmodelled construct(s): …`).

**What is reported.** `[DEF] keepouts added:` gives a count per provenance,
and names the nets when straps were read — for example
`OBS:13034, BLOCKAGES:12  (net rects: VDD:3400, VSS:3400)`. Both figures count
**rectangles**, not nets or straps: a polyline strap of N points becomes N−1
rectangles.

**Four ways obstruction does not arrive**, and none of them is silent.

*Before a keepout is ever formed* — these apply to macro `OBS`, so the table
above is what happens for a **placed** instance whose cell is in the LEF:

- The instance is **`UNPLACED`**. Its `OBS` is nowhere, so it is skipped —
  and censused as `COMPONENTS.OBS_UNPLACED`, for the same reason the halo
  beside it is counted for every halo, placed or not. This was silent until
  2026-08-19: a floorplan DEF that leaves instances unplaced blocked less
  metal than its macros describe, with nothing in the report saying so.
- The cell has **no LEF `MACRO`**, so there is no `OBS` to read. Reachable
  only under `allow_missing_footprints`, since a missing footprint is
  otherwise a hard error. The waiver names the cells (`BUDA-1606`) and now
  states this second cost explicitly: their instances block no metal at all.
  The `SIZE` is the half everyone expects to lose; the obstruction is the
  half that bites later, since a router planning over metal it cannot see
  reads as a clean result.

*When the keepout is installed* — these are reported:

- A keepout naming a **layer this session has not declared** is skipped;
  nothing can be placed there to block.
- A zone whose integer bbox **has no area after quantization** — obstruction
  thinner than one layout unit, ordinary for sub-micron rails imported at
  micron scale — is dropped, and said, with the nets it belonged to
  (`BUDA-1615`). A strap that vanished there is the one hole in `net`'s
  invariant, so it is never silent.

**Related.** Imported keepouts are persisted and restored like any other —
see the `keepout` table under [Schema overview](#1-schema-overview) (v29). How much of the Hanan
grid they contribute is a separate, opt-in decision made **before** the
import, with
[`set_keepout_loci`](script_reference/topologies.md#set_keepout_loci):
blocking behaviour is identical in every mode, and one `fakeram45_256x16`
carries 99 `OBS` rects, so a 133-macro design imports over 13,000 keepouts and
the grid pays for every edge.

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
- The die's own ports: the boundary components `import_def_lef` synthesized
  from DEF `PINS` keep their pin rows, saved before the clear and restored
  after elaboration. A top-level port is not an instance, so elaboration
  alone would leave them disconnected.

**What is NOT elaborated: library cells.**

An instance of a module the netlist does not define is a library cell.
Keeping them all would turn a million gates into a million component rows, so
they are filtered — and the rule is, in order:

1. **The LEF calls the cell a hard macro** — a `MACRO … CLASS` other than
   `CORE` (an absent CLASS reads as CORE, LEF's own default) → keep. The
   technology states this outright, so it is not a guess, and it is
   independent of how the instance is named — which is what carries
   `fakeram45_256x16 u_mem (…)` through a DEF+Verilog merge. The class is
   persisted on the `cell` row (`cell.cls`, schema v24) by `import_def_lef`.
2. Otherwise, for an import with **no LEF** to ask: an escaped instance name
   *and* a lowercase letter in the cell name (`fakeram45_*` yes, `DFFR_X1`
   no — Genus escapes both).
3. Otherwise skipped.

Skips are **counted and their cell kinds named** (`BUDA-1608`), because rule 2
is still a heuristic and an instance that silently never existed is
indistinguishable from a design that never had one.

> The placement is **not** the discriminator, though it looks like one on a
> macro-only DEF: a DEF for a gate-level design lists every standard cell in
> `COMPONENTS`, so "the placement already has an instance by that name" is
> true of every buffer and flop and the filter admits the whole netlist —
> exactly the explosion it exists to prevent. Pinned by
> `test_a_gate_level_merge_keeps_the_macro_and_drops_the_standard_cells`.

Returns a `VerilogImportStats` (`top_module`, `elaborated`,
`skipped_library_cells`, `skipped_kinds`, `skipped_cells` — the last capped at
eight, so `skipped_kinds` is what says whether the list is complete — plus the
vector-connection counts below).

**Vector connections.**

A port map's **bit-select keeps its selector**: `.a0(w[0])` is net `w[0]`, so
a 4-bit bus arrives as four nets. It used to resolve to the base name `w`,
which collapsed the bus to one net *and shorted its bits together* — two pins
the netlist keeps apart came back joined. The DEF side has always named bits
individually, so per-bit nets are also what makes the merge line up.

The identifier is resolved through the hierarchy context and the selector
re-applied to the **result**, which is what carries a bit-select across a
boundary: with `.p(w)` in the parent, the child's own `p[0]` lands on `w[0]`.

| Shape | Handling | Counted as |
|---|---|---|
| `.a(w[0])` | exact — one net per bit | `bit_selects` |
| `.a(w[3:0])` | one pin per bit the **formal port** can take | `part_selects` |
| `.a(w)` | a pin per bit of the port, on the matching bit of the actual | `vector_ports` |
| `{a,b}`, `w[i]` | unresolved — the connection is an open (`BUDA-1610`) | `unresolved_conns` |

**Part-select width.** Verilog width-adapts a port connection, so how much of
a part-select lands is the formal's **declared** width: `.s(w[3:0])` on a
scalar `input s` connects bit 0 alone, and on `input [1:0] s` connects bits 0
and 1. Bits are taken LSB-first, the end Verilog aligns. A formal whose width
is unknown — an undefined module declares no ports — connects **bit 0 only**
(true for every width ≥ 1) and reports the rest (`BUDA-1612`, counted as
`unsized_part_selects`) rather than assuming a width.

A part-select whose low bit is not 0, on a module elaboration descends into,
is reported (`BUDA-1611`): the child numbers its port bits from 0, so port bit
*k* is net bit *k+lo*.

Indices may carry whitespace — `w[ 0 ]`, `w[3 : 0]` are literal and resolve
exactly. `unresolved_conns` is counted **per elaborated instance**, like the
other two, so a module instantiated a hundred times reports a hundred opens
and one never instantiated reports none.

An **escaped** identifier keeps its brackets as part of the NAME — `\w[0]` is
a net called `w[0]`, not bit 0 of `w`, and `\w[1][0]` (a 2-D array element) is
a name whose "index" no select parser can read.

`net_props.bus_name` / `bit_index` are filled in from the **stored** net name
by `derive_bus_bit`, shared with `import_def_lef` so a DEF net and the Verilog
net it merges with cannot be classified differently.

**A vector port is N pins.**  `input [3:0] a` becomes `a[0]`..`a[3]`, in
`cell_pin` and on every instance, so a whole-vector connection wires bit to
bit instead of putting one pin on N nets.  Pins carry the port's **declared**
indices, so `input [7:4] a` is `a[4]`..`a[7]` — numbering them from 0 would
put them where no LEF/DEF pin of that macro is.

The width of a whole-signal *actual* comes from its declaration — `.a(w)` says
nothing about how wide `w` is — so `wire [3:0] w;` is read too, declared range
included.  An undeclared signal is an implicit wire, 1 bit.

Verilog aligns the two ends at their **low** bits and adapts the width, so a
connection is `min(formal, actual)` bits.  The formal's remaining upper bits
are **unconnected**, and are recorded as such: a child referencing one finds
"no connection" rather than a name derived from the actual's base, which would
invent `s[3]` for a scalar `s`.

Port bits are carried through the hierarchy by a per-bit context, so an offset
slice (`.p(w[7:4])`) maps exactly rather than approximately.

> Why the filter matters beyond tidiness: a dropped instance in a merge does
> **not** remove the DEF's row — it leaves it orphaned at depth 0. Its
> container then has no children, cannot be sized by
> `derive_container_bboxes`, gets no busterm, and the routing interface loses
> a whole level. See [`internal/opens_interchange.md`](internal/opens_interchange.md) item 1.

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
set_bottom_up <cell>|* [on|off]
```

Mark a cell template for **bottom-up planning** (default `on`): the hier flow
plans/NUTSes the cell's local interconnect once and copies the result to every
instance, with the copied routing becoming keepouts for higher-level bundles
(see `docs/internal/hier_bottom_up_planning.md`).  Persisted in the BDB
(`cell.bottom_up`, schema v17), so the flag survives `save_bdb` /
`load_pipeline`.

**`*` — keepout-scope generalization.** `set_bottom_up *` marks **every
eligible** cell in one call, where *eligible* = a cell with congruent placed
instances.  A cell with ≥2 instances is the solve-once-**copy** unit
(plan/NUTS once, copy to siblings); a **single-instance** cell has nothing
to copy but its cell-local routing is still solved once and **frozen as a
keepout** for the levels above (the same fixed-segment path, copy-to-one).
It is a pure convenience over marking each cell by name — it reuses the
entire marked-cell path (template solve, fixed-segment blockage,
`check_template_tracks`, DNUTS copy) with no new mechanism.  Cells whose
**≥2 instances are non-congruent** (cannot be frozen-and-copied) are
**reported and left on the top-down path** (fail LOUD, never silently
marked); a single instance is trivially congruent, so single-instance cells
are never skipped.  `* off` clears the flag on every currently-marked cell.
The generalization is **opt-in**: the default hier
flow marks nothing, so existing designs are unchanged unless you mark cells.
Because every marked cell is subject to the track-phase alignment contract,
run `align_bottom_up` (after a `def_track_pattern`) before `derive_busterms`
so copied instances share a phase — otherwise stage (c)
`check_template_tracks` stops DNUTS with the mismatch report, exactly as it
does for an explicit mark.

Turning the flag **on** requires every instance of the cell to be a rigid
transform of the first one — any of the 8 orientations.  The orientation is
detected **geometrically** by matching the full subtree shape under all 8
candidates (hierarchical `rotate_comp`/`flip_comp` rewrite descendant bboxes
and keep every orient token `'N'`, so tokens alone cannot be trusted; when a
self-symmetric child layout matches several orientations, identity is
preferred if it matches, else the candidate whose track phases fit best).
Only an instance matching NO orientation is rejected (genuinely
non-congruent, offenders listed).

The copies are handled per **rotation class**:

- **Direction-preserving instances** (`N`, `S` = 180°, `FN`/`FS` = axis
  mirrors) copy from the cell's reference directly — topologies, NUTS
  segments, and DNUTS bits/vias are orientation-transformed end-to-end.
- **The 90° family** (`E/W/FE/FW`) is split at `run_planner hier` into its
  own **rotation-class clone template**: a virtual template named
  `<cell>90` (uniquified `_1`, `_2`, … against real cell names and other
  bundle contexts) whose candidates are generated from the rotated
  reference instance's *actual* cell-local floorplan and planned with real
  per-direction layer costs — so no H↔V "layer pairing" mapping is ever
  applied to an existing solve.  Within the class every instance is a
  direction-preserving transform of the class reference, and the usual
  solve-once-copy-many machinery applies unchanged.  The clone is a
  routing-template identity only: it persists as a `bundle` row
  (`cell_context = '<cell>90'`, provenance in `bundle.cloned_from`,
  schema v19) and **never** appears in the `cell`/`component`/`pin`
  tables, so GDS/DEF/Verilog interchange is unaffected;
  `load_pipeline` restores the clone registry from `cloned_from`.

`run_planner hier` re-checks congruence at expansion time (placement may
change after marking) and hard-errors on violation.  `off` is always
accepted.

| Argument | Type | Description |
|---|---|---|
| `cell` | str | Cell type name; must exist in the `cell` table. |
| `on\|off` | keyword | Optional; defaults to `on`. |

Python API: `db.set_cell_bottom_up(cell, on)`, `db.cell_bottom_up(cell)`,
`db.bottom_up_cells()`, and `CellRow.bottom_up` via `db.all_cells()`.

---

### `align_bottom_up`

```
align_bottom_up [max_shift <um>] [force]
```

Nudge every `set_bottom_up` cell's instances onto a common track phase with
**minimal total movement**, so the bottom-up copies land on real signal
tracks in every occurrence.  Instances are grouped per **rotation class**
(upright and 90°-rotated occurrences align to their own class references —
the same grouping the planner-time clone templates use, under the same
`<cell>90` group name).  Per group and axis, an instance offset is
track-shift-invariant iff it is a multiple of every relevant layer's unit
pitch (V-layer pitches constrain x, H-layer pitches constrain y, combined as
their LCM); the common phase is chosen among the instances' current phases
minimizing the summed circular shift (the L1 circular median lies on a data
point), so the majority of instances usually stand still.

**Mirrored instances** (`S`/`FN`/`FS`, detected geometrically) participate
via their *effective* coordinate `e = K − extent − c` on each reflected
axis, where `K ≡ 2σ_l (mod pitch_l)` for every layer of the direction
(CRT-combined) and σ_l is the reflection-symmetry center of that layer's
signal-track set; the real nudge is the negated effective shift.  A
direction whose track layout has no such symmetry (or whose layers'
congruences are inconsistent) cannot host an aligned mirrored window at
all — such instances are left unmoved on that axis with a WARNING and do
not vote for the phase.

Moves are applied with `translate_comp` (whole subtree — congruence is
preserved).  Requires an open BDB and a routing grid (`def_track_pattern`);
run **before** `derive_busterms` / `add_blocks_from_bdb` (a later run prints
a staleness WARNING).  Region `add_grid_override` patterns are keyed to
absolute rects and cannot be compensated by translation — verify with
`check_template_tracks`, which also runs pre-routing (placement-stage,
whole-window comparison) exactly for this pairing.

After the moves, `FloorplannerEngine.validate()` audits the placement.
**By default any move that introduced a NEW overlap / outside-die issue is
auto-reverted** — the exact-geometry realization of a slack-aware cap: the
applied end state itself is the test, so large-but-legal nudges pass while a
nudge into a neighbor or off the die is undone (iterating to a fixpoint,
since one revert can newly collide with a still-moved sibling).  A reverted
instance leaves its cell possibly misaligned — `check_template_tracks`
reports it.  Pre-existing issues are summarized, never blamed on the
alignment.

| Argument | Type | Description |
|---|---|---|
| `max_shift <um>` | keyword + float | Optional cap: any nudge larger than this is skipped with a WARNING. |
| `force` | keyword | Keep moves that introduce NEW validate issues (WARNING only, no auto-revert). |

Python API: `db.translate_comp(name, dx, dy)` — translate a component and
its whole subtree (unlike `move_comp`, which repositions only the named
component's bbox).

---

### `check_template_tracks`

```
check_template_tracks [on_mismatch stop|independent]
```

The bottom-up **uniformity gate**: verify that every instance of a
`set_bottom_up` cell actually sees the same signal tracks for the routing it
is about to be handed a copy of.  A template is solved once and copied
verbatim, so that claim has to be checked rather than assumed — an instance
sitting on a different track phase, or with a region override or keepout
cutting its windows differently, cannot host the copy.

Run it **after `run_nuts`, before `run_detailed_nuts`**.  `run_detailed_nuts`
runs it implicitly if you never call it, but calling it explicitly lets you
choose the mismatch policy and see the report in flow order.

| Argument | Type | Default | Description |
|---|---|---|---|
| `on_mismatch stop` | keyword | default | Refuse DNUTS with the mismatch report — the design is not ready to copy |
| `on_mismatch independent` | keyword | — | Copy the ALIGNED instances and solve the misaligned ones individually.  This also declares the willingness `ripup_reroute`'s release pass is gated on (see its `no_release_moves` flag) |

**What it compares.** Per rotation class, the span-aware signal-track pool each
instance sees for each fixed segment window, normalized by instance origin (a
mirrored instance's pool is reflected back into the reference frame first).
Instances agree iff their offset/reflection phase fits the layer track pitch
and nothing cuts their windows differently.  Comparing raw *pools* is stronger
than comparing demand: identical pools mean identical seating for any rule,
including [non-default rules](NDR_REQUIREMENTS.md).

The verdict is cached and consumed by `run_detailed_nuts`; the policy persists
in BDB meta.  It also runs **before** any routing (placement-stage mode:
whole-instance windows per grid layer, advisory) — that early report is what
tells you to run [`align_bottom_up`](#align_bottom_up).

```
[TemplateTracks] cell 'sub_cell': ALIGNED — 4 instance(s) see identical signal tracks (ref u1, 3 window(s) compared)
```

---

### `set_cell_layer_cap`

```
set_cell_layer_cap <cell>|* <cap_layer> [-min <floor_layer>]
set_cell_layer_cap * off
```

**Per-cell layer policy**: the cell's OWN interconnect (its cell-local
bundles) may use layers in the band `[floor..cap]` and **nothing outside
it**.  This is the classic hierarchical BKM — leaves stay on the low metal,
the levels above them add the mid layers, the top level keeps the top
layers to itself — declared per cell type instead of hoped for.  Layers are
named or numbered (`M3` or `3`); `-min` sets the band's floor (default: no
lower bound); `*` sets the default for cells with no policy of their own;
`* off` clears **everything** (bands and shares), restoring byte-identical
behavior to never having declared a policy.

The band is resolved onto bundle wrappers by the **owning-frame rule**: a
cell-local template and its expanded per-instance wrappers (and a
rotation-class clone, via its base cell) take that cell's band; a
cross-level bundle takes the common ancestor's.  Enforcement lives in the
planner core's layer enumeration, so the STRICT ladder, rip-up trials,
`negotiate_congestion`, `ripup_reroute` and `run_planner post_nuts` all
comply.  Within an all-LOW band the highest layer per direction is promoted
to **effective-TOP** for cost purposes only (so a capped cell's trunks are
not taxed as if they were stubs); layer *physics* — leaf footprints as LOW
keepouts, TOP flying over — is unchanged.

Validation is LOUD at declaration: an unknown layer or cell, a floor above
the cap, or a band containing no H or no V routing layer are hard errors.
Bands are **persisted** (`cell.layer_cap`/`layer_floor`, schema v20; the
`*` default in `meta.layer_cap_default`), so `open_bdb` restores them and
`load_pipeline` re-resolves the masks — voiding, LOUDLY, any restored plan
that a since-tightened cap outlaws.  `align_bottom_up` and
`check_template_tracks` band-scope a capped cell's track-phase criterion to
its band's pitches.

One documented exception: an explicit `select_topology`/`edit_commit pin`
with forced segment layers overrides the mask.  `check_design` surfaces
every such pinned above-cap segment (and would report unpinned out-of-band
metal LOUD, which the mask makes impossible).

```
set_cell_layer_cap leaf_cell M3          # leaves on M2/M3
set_cell_layer_cap mid_cell  M5 -min M4  # a DISJOINT band, M4..M5 only
```

Python API: `db.set_cell_layer_band(cell, floor, cap)`,
`db.cell_layer_band(cell)`, `db.layer_capped_cells()`, and
`CellRow.layer_cap` / `.layer_floor` via `db.all_cells()`.

---

### `set_layer_caps_by_depth`

```
set_layer_caps_by_depth <cap1> [<cap2> ...] [-min <floor_layer>]
set_layer_caps_by_depth off
```

Declare the whole policy in one line: cap every cell by **how deep its own
content goes**, counting DEEPEST-FIRST.  `<cap1>` caps the deepest cells,
`<cap2>` the level above them, and so on; levels past the argument list are
**unrestricted**, so a short list fails in the right direction (the top
keeps every layer).  Bands are cumulative — level *i* gets
`[floor..cap_i]`, keeping the cheap low layers and *adding* what its
argument grants.

A cell's **level is intrinsic** — a property of its own subtree, not of
where it happens to be instantiated, so a cell used at several hierarchy
depths still has one well-defined level:

| Cell | Level |
|---|---|
| childless, not a container | 1 |
| childless **container** (declared to acquire children later) | 2 — one level of reserved headroom |
| anything else | 1 + max over its child cells' levels |

Container-ness comes from the design: a component marked non-leaf in the
BDB (`import_verilog` marks every *defined* module non-leaf, childless or
not) or a floorplan container block.  The child graph unions the
`cell_children` cell-type edges with the elaborated component tree, so
designs built by `add_inst_to_cell` and designs elaborated by
`import_verilog` level identically.

An explicit [`set_cell_layer_cap`](#set_cell_layer_cap) **always outranks**
this default, in either declaration order; `set_layer_caps_by_depth off`
clears only the by-depth entries and leaves explicit ones alone.  Each
level's band is validated exactly as a hand-declared one (unknown layer,
floor above a cap, and a band with no H or no V layer are hard errors); a
cap list that *decreases* is noted, not refused.  Re-running with a
**shorter** list frees the levels it no longer names — their bands are
cleared from the session and the BDB, not merely reported as unrestricted.

Every assigned band is persisted like a hand-declared one, and which cells
this command owns is persisted alongside them (`meta.layer_caps_by_depth`)
— a band alone cannot say whether it was declared explicitly or in bulk,
and after a reload that difference is what keeps an explicit cap from being
overwritten and lets `off` find the bands to clear.  The level assignment
is reported:

```
set_layer_caps_by_depth M3 M5
  [LayerCaps] level 1 -> band [lowest..M3]: 144 cell(s): big2__blk_00, …
  [LayerCaps] level 2 -> band [lowest..M5]: 2 cell(s): big2, mix2
  [LayerCaps] level 3+ unrestricted: 1 cell(s): top
```

Python API: `db.cell_child_edges()` returns the `(parent_cell, child_cell)`
edges the levels are computed from.

---

### `reserve_top_layers`

```
reserve_top_layers <N> [-min <floor_layer>]
reserve_top_layers off
```

The **stack-relative** way to say the same thing: reserve the top `N`
layers of the declared stack for the top level, and cap every cell below it
just under them.  Prefer this over
[`set_layer_caps_by_depth`](#set_layer_caps_by_depth) whenever the intent is
"the top level gets the top *N*" — which is the usual BKM.

The difference matters because a band naming absolute layers is only correct
for the stack it was written against.  `set_layer_caps_by_depth M3 M5`
reserved the top **pair** on a 6-layer stack; the identical line on a
10-layer stack reserves the top **six**, starving the cells it governs, and
nothing reports it — an over-tight band is legal, merely wasteful.
`reserve_top_layers 2` states the intent, so it re-derives the right cap on
either stack.

Everything else matches the by-depth command: cell levels are intrinsic and
bottom-anchored, an explicit `set_cell_layer_cap` always outranks it, bands
persist, and the two share one bulk-declaration provenance memo — so
`reserve_top_layers off` and `set_layer_caps_by_depth off` are the same
operation, and re-running either replaces whatever the other declared
(including freeing a cell that is no longer governed).

Validation is LOUD: `N` below 1, an `N` that leaves no layer for the cells
below the top, an unknown floor, a floor above the derived cap, and a
reservation leaving the cells no H or no V layer are all hard errors.  A
design with no level above the deepest one is reported as a no-op rather
than capping every cell against an absent top.

```
reserve_top_layers 2
  [LayerCaps] reserving the top 2 layer(s) (M10, M11) for level 3:
              146 cell(s) below it capped to [lowest..M9]: big2, mix2, …
```

---

### `set_cell_layer_share`

```
set_cell_layer_share <cell> <layer> <pct>
```

**Fractional layer share**: the cell's own interconnect may use at most
`pct`% of `<layer>`'s signal tracks.  A share overrides the band in either
direction — thin a layer *inside* the band, or lease a bounded slice
*above* the cap.  The second is the wiring-limited escape valve: a cell
whose band is genuinely too tight can be given, say, 75% of the next layer
up instead of the whole thing.  `pct 100` removes the share (explicit full
use).

The share is realized per tier.  Cell-local solves see a **derived thinned
view** — the first `floor(share × n_signal)` SIGNAL slots of each period
are kept (same origin and pitch, so track phase and `align_bottom_up` are
untouched) plus a layer-stack clone whose bit pitch prices the leased slice
honestly.  Globally-planned bundles get `share × capacity` plus a **scalar
collective budget**: one counter per (cell-instance, layer) enforced inside
the planner's layer enumeration, so a cell's several bundles cannot
together exceed the lease and the choice steers to an in-band alternative
rather than escalating past it.

The share is a **budget on the cell, never a reservation against the
parent**: a child using 12% of its 30% slice leaves the other 88% to the
levels above, which see the full grid minus the child's actual placed
routing.

Validation is LOUD: an unknown layer or cell, a layer with no
`def_track_pattern` (the thinning needs the period), and a share that
floors to **zero** kept slots are hard errors — the last names the minimum
meaningful share for that pattern.  Shares are persisted
(`cell_layer_share`, schema v20) and cleared by `set_cell_layer_cap * off`.

```
set_cell_layer_cap   dnuts1 M3        # band stops at M3 …
set_cell_layer_share dnuts1 M4 75     # … but lease 75% of M4 above it
set_cell_layer_share dnuts1 M5 75
```

Python API: `db.set_cell_layer_share(cell, layer_id, share)`,
`db.cell_layer_shares(cell)`, `db.layer_share_cells()`.

Design notes and the measured study: `docs/internal/hier_layer_caps.md`.

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

An **unplaced** component is skipped — it has no extent to route to. That is
why a DEF+Verilog design needs `derive_container_bboxes` first; see below.

---

### `derive_container_bboxes`

```
derive_container_bboxes [margin <n>]
```

Give every **unplaced container** the bounding box of its placed
descendants, grown by `margin` on each side. Prints how many it placed, and
reports (BUDA-1607) any container with nothing placed underneath it.

This is the step a **DEF + Verilog merge** needs and neither file can
supply. A DEF is flat — `COMPONENTS` lists leaf instances only — so a
hierarchical instance has no row anywhere, and `import_verilog`, which knows
the tree but no geometry, leaves it unplaced. `derive_busterms` skips
unplaced components, so without this the routing interface comes out with a
**hole** in it: the ports and the leaves get busterms, the levels between
them do not, and the leaf busterms have no parent to belong to. Every
command succeeds while that happens, which is what makes it worth a
command of its own.

Deliberately **explicit** rather than folded into `import_verilog`: it
invents geometry the input never stated, so it belongs in the script. It
never moves a component that already has a position, and it resolves
deepest-first so a container of containers is built from children that were
themselves just resolved.

| Argument | Description |
|---|---|
| `margin` | Optional. Layout units added on each side of the derived box (default `0`). |

**Example** — the full merge (see `flow/def/chip.buda`):
```buda
import_def_lef chip.def chip.lef     # placement, flat
import_verilog chip.v                # hierarchy, no geometry
derive_container_bboxes margin 1500  # give the containers an extent
derive_busterms 2
```

---

### `refine_busterms`

```
refine_busterms
```

Re-derive the busterm table using the **same `max_depth` as the last
`derive_busterms` call**, clearing and rewriting it in place. Takes no
arguments — the depth is remembered from the earlier call, so this is a
refresh, not a re-parameterisation; to change the depth, call
`derive_busterms <max_depth>` again.

Run it after mutating placement (`move_comp`, `flip_comp`, `rotate_comp`,
`resize_cell`, `align_bottom_up`, …) so the routing interface matches the
components' new positions. Prints the number of busterms written.

`derive_busterms` must have run first — otherwise the command reports
`Error: run derive_busterms first` and does nothing.

**Example:**
```buda
derive_busterms 2
move_comp chip/i_dnuts1  1200 800
refine_busterms            # busterms re-derived at depth 2
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
| GDSII | import + export | ✅ supported (Phases G0–G4) | `import_gds` / `export_gds` |
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

Until OA support lands, the supported interchange paths are **LEF/DEF +
Verilog in** and **GDSII import/export** (round-trip); OA is the only
remaining planned format.

For committing BDBs as reviewable test data — and the schema-versioning /
provenance / routing-write-back groundwork that the OA/GDS export will build on —
see [BDB Test-Data Management](internal/bdb_test_data.md). Its `*.bdb.sql` text
dump (`tools/bdb_serialize.py`) is the diffable, version-controllable form of a
BDB.
