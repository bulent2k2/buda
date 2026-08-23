# `buda2bdb` — Buda Script to BDB Cell Translator

`tools/buda2bdb.py` ingests a flat `.buda` script into a **BDB** (Buda Physical
Design Database) as a **cell**.  It is the reverse of
[`bdb2buda`](BDB2BUDA.md): the script's `add_block`s become the cell's internal
blocks, its `add_net`/`add_bus` become the cell's local nets, and `set_die`
(or, when absent, the bounding box of all blocks) becomes the cell size.

---

## Why This Tool Exists

`bdb2buda` lets you take a design in a BDB and run the flat routing flow on it.
`buda2bdb` closes the loop: a hand-authored or generated flat `.buda` script can
be folded back into a BDB as a reusable **cell** in the hierarchy-aware flow —
solved once per cell type and instantiated at every occurrence.  Together the two
tools give a round-trip between the flat and hier worlds, which also makes
`bdb2buda` a convenient correctness oracle (see [Round-trip](#round-trip)).

---

## Usage

```bash
python3 tools/buda2bdb.py <cellname.buda> <bdbfile.bdb|bdbfile.bdb.sql> [-cell <name>]
```

| Argument / option | Default | Description |
|---|---|---|
| `<cellname.buda>` | *(required)* | Input flat `.buda` script |
| `<bdbfile>` | *(required)* | Target BDB; created if it does not exist. May be a binary `.bdb` or a diffable `.bdb.sql` (or `.sql`) text fixture |
| `-cell <name>` | filename stem | Cell name to create (e.g. `cpu.buda` → `cpu`) |

A **`.bdb.sql`** target is edited via a throwaway temp binary and serialized
back to the text form (reusing `tools/bdb_serialize`); an existing `.sql` is
materialized first, so adding/replacing a cell preserves the cells already in
the fixture. See [BDB Test-Data Management](internal/bdb_test_data.md).

### Examples

```bash
# Create cell "two" in a fresh BDB from a flow script
python3 tools/buda2bdb.py flow/two.buda /tmp/two.bdb

# Add/replace cell "core" in an existing BDB, overriding the name
python3 tools/buda2bdb.py core_flat.buda soc.bdb -cell core

# Write the result straight to a diffable text fixture
python3 tools/buda2bdb.py core_flat.buda cells.bdb.sql -cell core
```

---

## What Gets Read

| Command | Effect |
|---|---|
| `set_die <w> <h>` | Sets the cell size; blocks are kept in their script frame |
| `add_block <name> <x1> <y1> <x2> <y2>` | One internal block (a child instance of the cell) |
| `add_net <name> <drv> <rcv_csv> [unknown\|inout]` | One cell-local net |
| `add_bus <prefix>[N] <drv> <rcv_csv> [unknown\|inout]` | Expands to nets `prefix_0 … prefix_{N-1}` (range form `[lo:hi]` supported) |
| `source <file>` | Followed when the file is found (relative to the script) |

All other commands (`def_layer`, `def_track_pattern`, `run_*`, `visualize`,
`corner_margin`, …) are **ignored with a warning** — only placement and
connectivity are translated.  Multi-rect `add_block <name> rect …` is collapsed
to the union bounding box — a BDB component holds ONE bbox, so the collapse is
by construction (the hier/BDB multi-rect boundary,
`docs/internal/teg_multirect_status.md` open 6) — and the warning names both
the collapse and every dropped trailing modifier, `teg_mode` included (an
OVER declaration lost silently would turn an electrically-open block into a
clean-auditing one); `container` / `corner_margin` are dropped the same way.

---

## Cell Size and Coordinates

- **With `set_die`:** the cell is `<w> × <h>` and blocks keep their script
  coordinates (origin `(0,0)`).
- **Without `set_die`:** the cell is the bounding box of all blocks, and blocks
  are shifted so the cell's lower-left corner is the origin — matching how
  `bdb2buda -cell` emits child coordinates relative to the parent.

Coordinates are stored **1:1 as micron values** (script integer → same µm).  For
an exact round-trip, read back with `bdb2buda -scale 1`.

---

## How the Cell Is Stored

To store nets, the cell is **instantiated once** — pins attach to component rows,
never to a bare cell template.  `buda2bdb` therefore creates:

1. The cell row (`add_cell`) sized to the die.
2. A synthetic leaf cell `<cell>__<block>` and a `cell_children` row per block
   (`add_inst_to_cell`), with coordinates local to the cell origin.
3. A **representative instance** named `<cell>` (`add_inst`), which expands the
   children to components `<cell>/<block>`.
4. The cell-local nets (`add_net_pins` / `_undirected` / `_inout`) on those
   child components, e.g. driver endpoint `"<cell>/<block>.<port>"`.

`bdb2buda -cell <cell>` finds this instance (by name and cell type), reads the
children and their pins, and reproduces the script.

---

## Replace and Instance Sync

- **Replace:** if `<cell>` already exists, its representative-instance subtree,
  `cell_children` rows, stale port declarations, now-orphaned nets, and unused
  synthetic child cells are deleted first (via a scoped SQLite pass), then the
  cell is rebuilt. Names are matched literally (not via SQL `LIKE`/`GLOB`), so a
  cell named `cpu_0` never disturbs an unrelated `cpuA0`.
- **Instance sync:** after rebuilding, `resize_cell` updates **every** component
  of that cell type — including other instances placed elsewhere in the BDB — to
  the new size, keeping each instance's lower-left origin fixed.

> **Net model / scope.** The canonical netlist lives on the **representative
> instance** `<cell>` (pins attach to component rows, not to a cell template).
> Other instances of the cell are placements that get **size-synced** only —
> their internal child bodies are left in place (never left with dangling cell
> references). If you need other instances' internals rebuilt from the new
> definition, re-instantiate them after import.

---

## Round-trip

`bdb2buda` is the oracle:

```bash
python3 tools/buda2bdb.py chip.buda design.bdb -cell chip
python3 tools/bdb2buda.py design.bdb -cell chip -scale 1
#   → set_die / add_block / add_net / add_bus matching chip.buda
```

Notes on fidelity:
- Use `-scale 1`; `buda2bdb` stores coordinates unscaled.
- A script with **no** `set_die` is bbox-shifted on the first pass; the emitted
  script (which now carries an explicit `set_die`) is a fixpoint thereafter.
- `bdb2buda` re-collapses consecutive `prefix_0…prefix_{N-1}` nets into
  `add_bus`, and labels a port-less pin as `.p`.

---

## Limitations

| Limitation | Notes |
|---|---|
| No external ports | Cell `cell_pin` ports are not inferred; all nets are internal |
| No routing tech | `def_layer` / `def_track_pattern` and run/verify commands are ignored |
| Multi-rect blocks | Collapsed to the union bbox (by construction — one bbox per BDB component); the warning names the dropped modifiers, `teg_mode` included |
| Global net names | `net.name` is unique BDB-wide; the replace step clears the cell's old nets to avoid collisions |
