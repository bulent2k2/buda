# `bdb2buda` — BDB to Flat Script Converter

`tools/bdb2buda.py` converts a BDB (Buda Physical Design Database) into a
flat `.buda` routing script.  The output contains `set_die`, `add_block`,
`add_net`, and `add_bus` commands that feed directly into the BUDA flat
routing flow (`bin/buda out.buda`).

---

## Why This Tool Exists

The **hier flow** stores all design data in a BDB (hierarchy, placement,
netlist).  The **flat flow** works from hand-authored `.buda` scripts.
`bdb2buda` bridges the gap: it lets you take a design already in a BDB —
whether built with the Floorplanner, imported from DEF/LEF, or generated
by `fp_demo.py` — and run the flat routing pipeline on it directly.

Typical uses:

- Quickly test the routing flow on a Floorplanner demo scenario
  (`bin/bfp tc1` → optimize → export → route).
- Hand-off a placed design from the hier flow to the flat flow for
  debugging a specific routing problem.
- Generate a baseline `.buda` script that you can then annotate with
  layer definitions, corner margins, or topology pins.

---

## Usage

```bash
python3 tools/bdb2buda.py <file.bdb|file.bdb.sql> [options] [-o <output.buda>]
```

Output goes to **stdout** unless `-o` is given.

The input may be a binary **`.bdb`** or a diffable **`.bdb.sql`** (or `.sql`)
text fixture — the checked-in form under `test/tests/data/`. A `.sql` is
materialized to a throwaway temp binary first (read-only; the source `.sql`
is never modified), so both produce identical output. See
[BDB Test-Data Management](internal/bdb_test_data.md).

### Options

| Option | Default | Description |
|---|---|---|
| `-cell <name>` | *(none)* | Export children of the named component or cell type instead of the top-level blocks (see [Cell mode](#cell-mode)) |
| `-scale <N>` | `10` | Multiply every coordinate by N and round to the nearest integer (see [Coordinate scaling](#coordinate-scaling)) |
| `-o <path>` | stdout | Write the script to a file instead of printing it |

### Quick examples

```bash
# Top-level blocks from a Floorplanner BDB, default scale ×10
python3 tools/bdb2buda.py design.bdb -o design.buda

# Children of cell type "chip_top", no scaling
python3 tools/bdb2buda.py design.bdb -cell chip_top -scale 1 -o chip.buda

# Print to stdout and inspect
python3 tools/bdb2buda.py /tmp/bfp_tc1.bdb | head -20

# Straight from a checked-in diffable text fixture (no binary needed)
python3 tools/bdb2buda.py test/tests/data/hier_mixed.bdb.sql -o hier_mixed.buda
```

---

## What Gets Written

### `set_die`

```
set_die <width> <height>
```

Die dimensions are taken from the BDB (`die_w` / `die_h`).  When no die
was set in the BDB, the bounding box of the selected components is used as
a fallback.  In `-cell` mode the die is the parent component's own
bounding box.

### `add_block`

```
add_block <name> <x1> <y1> <x2> <y2>
```

One line per selected component, sorted by name.  In top-level mode the
block name is the component's full name (which for flat BDBs has no `/`).
In `-cell` mode the *leaf* part of the name is used and coordinates are
relative to the parent's lower-left corner.

**A component whose CELL declares a multi-rect footprint** (`set_cell_rects`,
schema v30) is emitted in the rect form instead:

```
add_block <name> rect <x1> <y1> <x2> <y2> [rect ...] [teg_mode over]
```

The cell-local rects are transformed by the instance's orientation and
translated to its placement (the same rule the hier BDB→Floorplan projections
use — `src/orient_rect.py`, shared rather than re-derived), so what comes out
is the geometry the routing frame sees.  Before v30 a BDB cell was one
`width x height` box, so a TEG macro round-tripped as its union bbox and lost
`teg_mode` with it.

### `add_net` and `add_bus`

Nets with at least two endpoints inside the selected component set are
emitted.  Nets that touch only one selected component (boundary /
interface pins) are silently skipped.

**Driver selection:**
- Any pin with direction `OUTPUT` or `DRIVER` becomes the driver.
- If no pin has a declared driver direction (e.g. nets written via
  `add_net_pins_undirected`), the first pin in storage order is used as
  driver and the rest become receivers.

**Bus collapsing:**  
Sequential nets that share the same prefix, driver, and receiver set are
collapsed into a single `add_bus` command:

| Condition | Result |
|---|---|
| Net names match `<prefix>_0`, `<prefix>_1`, … `<prefix>_{N-1}` (no leading zeros) | `add_bus <prefix>[N] <drv> <rcv_csv>` |
| Any condition above fails (zero-padded indices, non-consecutive, mixed connectivity) | Individual `add_net` per net |

---

## Coordinate Scaling

The flat routing flow's `add_block` parser reads coordinates as integers
(`int(...)`).  BDB coordinates are stored as floating-point µm values, so
fractional coordinates (common in DEF/LEF imports) would be truncated.

The `-scale <N>` flag (default **10**) multiplies every coordinate by N
and rounds to the nearest integer:

| Source coordinate | `-scale 10` (default) | `-scale 1` |
|---|---|---|
| `12.5 µm` | `125` | `12` |
| `100.0 µm` | `1000` | `100` |
| `3000.0 µm` | `30000` | `3000` |

With the default ×10, a BDB whose coordinates are in µm is converted to a
script whose coordinates are in tenths of a µm — a common unit in EDA
flows.  If your BDB already contains integer coordinates (e.g. from the
Floorplanner with grid=10), `-scale 1` avoids unnecessary magnification.

---

## Cell Mode

Without `-cell`, the converter exports the **depth-0 components** of the
BDB — the top-level instances, the same ones the Floorplanner shows at its
root level.

With `-cell <name>`, the converter:

1. Finds the first component whose **instance name** or **cell type** matches
   `<name>`.
2. Exports that component's **direct children** as blocks.
3. Uses the parent component's bounding box as the die.
4. Makes all coordinates **relative to the parent's lower-left corner**.

Example — extract the internals of `u_core`:

```bash
python3 tools/bdb2buda.py soc.bdb -cell u_core -o core_flat.buda
```

---

## Integration with the BUDA Flow

The generated script contains only placement and connectivity — it has no
layer definitions, track patterns, or corner margins.  A typical next step
after conversion:

```bash
# 1. Convert
python3 tools/bdb2buda.py design.bdb -o design.buda

# 2. Prepend technology setup (layer defs, track patterns, etc.)
#    or source an existing tech file from within design.buda:
#       source flow/my_tech.buda

# 3. Route
bin/buda design.buda
```

Alternatively, pipe the output into a wrapper script:

```bash
python3 tools/bdb2buda.py /tmp/bfp_tc1.bdb | cat flow/tech_header.buda - > /tmp/tc1_routable.buda
bin/buda /tmp/tc1_routable.buda
```

---

## End-to-End Example

Start from a Floorplanner demo scenario and route it:

```bash
# 1. Generate demo BDB and open in Floorplanner
bin/bfp tc1

# 2. (In the GUI) run SA optimizer, spread blocks, close window

# 3. Convert the optimized BDB to a flat script
python3 tools/bdb2buda.py /tmp/bfp_tc1.bdb -scale 1 -o /tmp/tc1.buda

# 4. Inspect the output
head -10 /tmp/tc1.buda
#   set_die 3000 2400
#   
#   add_block blk_00 47 320 307 390
#   add_block blk_01 120 10 180 130
#   ...
#   add_net bus_000 blk_38.p blk_10.p
#   ...

# 5. Add layer definitions and route
cat flow/my_tech.buda /tmp/tc1.buda | bin/buda /dev/stdin
```

---

## Limitations

| Limitation | Notes |
|---|---|
| No layer or track data emitted | The BDB has no routing technology; add `def_layer` / `def_track_pattern` manually or via `source` |
| Single-pin boundary nets skipped | Nets connecting a selected block to a block outside the selected set are dropped |
| Zero-padded bus names not collapsed | Nets named `bus_000_b00`…`bus_000_b59` (from `fp_demo.py`) emit as individual `add_net` lines; only unpadded `prefix_0`…`prefix_{N-1}` names collapse to `add_bus` |
| First matching instance used for `-cell` | If multiple instances share the same cell type, the first one found in storage order is used |
