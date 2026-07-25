# `bdb_edit_bus` — BDB Bus-Width Editor

`tools/bdb_edit_bus.py` edits a **bus's bit count** — or deletes a bus — directly
in a **BDB** (Buda Physical Design Database) netlist. It resizes the set of
per-bit nets that make up a bus:

- **PRUNE** a wide bus down (e.g. 64-bit → 4-bit): keep the lowest-index bits,
  drop the rest.
- **GROW** a narrow bus up (e.g. 2-bit → 16-bit): clone a template bit's pin
  structure to add higher-index bits.
- **DELETE** every bit of a bus.

---

## Why This Tool Exists

To explore how bus width affects routability/congestion you often want to make a
quick netlist tweak on an existing design — widen a critical bus, prune an
over-provisioned one, or drop a bus entirely — without re-importing DEF/Verilog
or hand-editing SQL. `bdb_edit_bus` does that in one command, keeping every
net-referencing table consistent.

---

## What a "bus" is in a BDB

A bus is a set of `net` rows sharing a **base name plus a numeric bit suffix**:

```
bus_011_b00, bus_011_b01, ... bus_011_b63      # zero-padded, 'b'-prefixed
s2p_0, s2p_1, s2p_2                            # plain underscore
data[0], data[1], ...                          # bracketed
```

The bit membership lives in the **net name** — `net_props.bus_name` is left unset
even in real BDBs (from `import_def_lef` / `import_verilog`), so the tool
identifies a bus by name pattern, not by a stored column. The **name frame**
(separator, `b` marker, zero-pad width, brackets) is auto-detected from the
existing bits, so bits added by a grow match the design's convention exactly.

---

## Usage

```bash
python3 tools/bdb_edit_bus.py <db> [--list [PREFIX]]
python3 tools/bdb_edit_bus.py <db> --bus <base> --set-bits <N>
python3 tools/bdb_edit_bus.py <db> --bus <base> --delete
```

(Or `tools/bdb_edit_bus.py …` directly — it is executable.)

| Argument / option | Description |
|---|---|
| `<db>` | **Input** BDB: `.bdb` (SQLite binary) or `.bdb.sql` (diffable text; round-tripped via [`bdb_serialize`](internal/bdb_test_data.md)). Both are fully supported for reading |
| `-o`, `--output <path>` | Write the result to a **new** file instead of editing in place — the input is left untouched. The output format follows the extension (`.bdb` binary / `.bdb.sql` text), so this **also converts** between the two (e.g. edit a `.bdb`, emit a `.bdb.sql`). Applies only to an edit, not `--list` |
| `--list [PREFIX]` | Enumerate detected buses (base, bit count, index range, name frame); optional prefix filter. This is the default action when no `--bus` is given |
| `--bus <base>` | The bus base name, e.g. `bus_011` or `s2p` (required for an edit) |
| `--set-bits <N>` | Resize the bus to **N** bits — prunes if fewer than current, grows if more. `--set-bits 0` is equivalent to `--delete` |
| `--delete` | Remove every bit of the bus |
| `--dry-run` | Print the planned change (per-bit added/deleted lines) without writing |
| `--clear-routing` | Also drop the route-derived tables (bundling / topology / NUTS / DNUTS), since a bit-count change invalidates them (see below) |

### Examples

```bash
# What buses are in here, and how wide?
tools/bdb_edit_bus.py design.bdb --list

# Prune a 64-bit bus to its low 4 bits.
tools/bdb_edit_bus.py design.bdb --bus bus_011 --set-bits 4

# Grow a 2-bit bus to 16 bits (bus_011_b00 .. bus_011_b15).
tools/bdb_edit_bus.py design.bdb --bus bus_011 --set-bits 16

# Preview first.
tools/bdb_edit_bus.py design.bdb --bus bus_011 --set-bits 16 --dry-run

# Delete a bus and wipe the now-stale routing in one go.
tools/bdb_edit_bus.py design.bdb --bus bus_011 --delete --clear-routing

# Write to a new file (input untouched) — and convert .bdb -> diffable .bdb.sql.
tools/bdb_edit_bus.py design.bdb --bus bus_011 --set-bits 8 -o widened.bdb.sql
```

Both `.bdb` and `.bdb.sql` work as input and as `-o` output, in any combination
(the tool round-trips through `bdb_serialize`), so `-o` doubles as a format
converter.

---

## What it edits

A bit net is added to / removed from **every** table that references `net(id)`:

| table | role |
|---|---|
| `net` | the bit net itself (`id`, `name`) |
| `pin` | the bit's pins (`comp_id`, `pin_name`, `dir`, `px`, `py`) |
| `net_props` | per-net bus metadata (`bus_name`, `bit_index`, `bundle_id`, …) |
| `bundle_net` | bundle membership + bit order (present only after `run_bundler`) |
| `net_segment` | routed per-bit wires (present only after `run_detailed_nuts`) |
| `net_via` | routed per-bit vias |

**Grow** clones the highest-index existing bit as a template: it copies that
bit's `pin` rows — **renaming** the per-bit *interface* pin (a pin whose name
equals the template net's name, e.g. a hierarchy-boundary port) to the new bit
while keeping shared leaf ports (`out`/`in`) — copies its `net_props` (updating
`bit_index`), and joins the new bit to the **same bundle** so a wider bus flows
through the router. It does **not** fabricate routing for new bits.

**Prune** / **delete** remove the dropped bits from all of the above, including
their routed `net_segment`/`net_via` rows. All work runs in a single
transaction; `--dry-run` rolls it back.

---

## Routing is stale after an edit

Changing a bus's bit count changes the bundling, so any existing topology / NUTS
/ DetailedNUTS result no longer matches the netlist. The tool prints a **WARNING**
and leaves the route tables in place by default (so you can inspect them); pass
`--clear-routing` to drop them and return the BDB to a clean pre-bundle state.
Either way, re-run the flow afterward:

```
open_bdb design.bdb
… (re-declare layers / tracks / blocks as your flow does) …
derive_busterms            # hier flow
run_hier_bundler           # or run_bundler for the flat flow
generate_hier_topologies
run_planner hier
run_nuts
run_detailed_nuts
```

---

## Notes & guarantees

- **Prefix safety.** A base is matched against the *whole* remaining name, so
  `--bus bus_01` does not swallow `bus_011`'s bits, and a `--list` groups
  `bus_011` correctly even though the base itself contains digits.
- **No orphans.** Deletes cascade across all net-referencing tables, so no `pin`
  / `net_props` / `bundle_net` / routing row is left pointing at a removed net.
- **Frame collision.** Grow refuses to overwrite an already-existing net name.
- The edited BDB opens cleanly in the engine (`buda.BDB`) and the resized bus is
  visible to `all_nets` / the bundler.

See also: [BDB Reference](BDB_REFERENCE.md), [BDB Test-Data
Management](internal/bdb_test_data.md), [`bdb2buda`](BDB2BUDA.md) /
[`buda2bdb`](BUDA2BDB.md).
