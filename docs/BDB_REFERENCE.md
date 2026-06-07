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
2. [`.buda` script commands](#2-buda-script-commands)
3. [Python API](#3-python-api)
   - [Row types](#row-types)
   - [Ingestion](#ingestion)
   - [Mutations](#mutations)
   - [Computed properties](#computed-properties)
   - [Queries](#queries)
   - [Group management](#group-management)
   - [Metadata](#metadata)
4. [Typical workflows](#4-typical-workflows)
5. [Notes and caveats](#5-notes-and-caveats)

---

## 1. Schema overview

```
component        id, name, cell, parent_id→component, depth,
                 x1, y1, x2, y2, is_leaf, is_replicated

net              id, name

pin              net_id→net, comp_id→component, pin_name,
                 dir (INPUT|OUTPUT|INOUT), px, py

net_props        net_id→net, hpwl, fanout, driver_comp,
                 bus_name, bit_index, bundle_id

busterm          id (TEXT), comp_id→component, hier_path,
                 depth, x1, y1, x2, y2, resolution, parent_id→busterm

bundle           id (TEXT), depth, strategy, parent_id→bundle, is_replicated
bundle_net       bundle_id→bundle, net_id→net
bundle_busterm   bundle_id→bundle, busterm_id→busterm

grp              id (TEXT), name, color, parent_id→grp
grp_member       grp_id→grp, kind, ref

meta             key (TEXT PK), value  — die_w, die_h, units
```

**coordinates** are in microns (µm). `import_def_lef` converts from DEF
internal units using the `UNITS DISTANCE MICRONS` value from the DEF header.
Unresolved pin positions are stored as `−1`.

`parent_id` in `component` is `NULL` for top-level (depth-0) instances.
Python returns `−1` for a `NULL` parent.

---

## 2. `.buda` script commands

The BDB commands form a self-contained sub-flow inside a `.buda` script.
Call `open_bdb` first; all other BDB commands will print an error and skip
if no database is open.

### `open_bdb`

```
open_bdb <path>
```

Open (or create) a BDB at `<path>`. Subsequent BDB commands operate on this
database. Call once per script; opening a second path replaces the reference
(the first file is not closed automatically — use the Python API if you need
multiple simultaneous databases).

| Argument | Description |
|---|---|
| `path` | File path for the `.bdb`; created if it does not exist. Use `:memory:` for an in-memory scratch database. |

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

### `add_comp`

```
add_comp <name> <cell> <parent|-> <x1> <y1> <x2> <y2> [leaf|nonleaf]
```

Insert a new component row. Use `−` as the parent to create a root (depth-0)
instance.

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
`compute_hpwl()`.

**Example:**
```buda
# Add a new root module
add_comp di  d  -     1900 100 2000 300 nonleaf

# Add a child instance under di
add_comp di/x1i  x1  di   1910 150 1960 200 leaf
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

---

### Mutations

```python
db.move_comp(name: str, x: float, y: float)
```
Move instance `name` to origin `(x, y)`, preserving size.

```python
db.resize_cell(cell: str, w: float, h: float)
```
Set `x2 = x1 + w`, `y2 = y1 + h` for every instance of `cell`.

```python
id: int = db.add_comp(name, cell, parent_name, x1, y1, x2, y2, is_leaf=True)
```
Insert a new component. `parent_name=""` for a root instance. Returns the new
row's `id`.

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
