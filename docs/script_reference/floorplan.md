# BUDA Script Reference — Floorplan commands

[← Index](../BUDA_SCRIPT_REFERENCE.md)

The commands that make GEOMETRY where the input stated none.  Before
`auto_floorplan` a design reached the hierarchical flow by one of two
routes — a floorplan drawn in Tcl from the bus widths, or a DEF whose leaf
placement `derive_container_bboxes` grew containers around — and a Verilog
netlist alone, which `import_verilog` reads into a cell tree with every
component unplaced and every cell 0 × 0, had nothing to route.  The full
account, with the measurements, is [AUTO_FLOORPLAN.md](../AUTO_FLOORPLAN.md).

## `auto_floorplan`

```
auto_floorplan [pdk <file>] [gap <n>] [margin <n>] [top_margin <n>]
               [place auto|slice|grid|sa] [wl <f>] [snap <px> <py>]
               [grow <cell>=<f>,...] [cols <cell>=<n>,...]
               [seed <n>] [keep <n>] [pad <n>] [bitpitch <f>] [aspect <f>]
               [util <f>] [sa_iter <n>] [fixed <cell>,...]
```

Size and place the whole cell tree of the open BDB — every leaf, every
container, every instance, the die — from the netlist's own pins and a PDK
area model.  Needs an open BDB holding components (after `import_verilog`,
or `add_cell` / `add_inst`); a routing grid is read when declared (the bit
pitch of the coarsest TOP layer sizes the faces) and the mock default is
used otherwise.

| option | default | what it is |
|---|---|---|
| `pdk <file>` | none | the area model ([flow/mockpdk/mock.pdk](../../flow/mockpdk/mock.pdk)); without it the face rule alone sizes every leaf, said in the report |
| `gap <n>` / `margin <n>` / `top_margin <n>` | 16 / 16 / = margin | the channel between siblings, the margin inside a container, the die's margin |
| `place` | `auto` | `auto` = `slice` (a slicing beam for ≤ 8 children, connectivity clustering into groups of ≤ 8 beyond); `grid` = the `soc_lib.tcl` grid with the best column count; `sa` = the Floorplanner's C++ annealer from the grid's start, legalized and aligned, kept only when it scores lower |
| `wl <f>` | 0.5 | the weight of the nets' wirelength against area in the packing cost (units: bits × a block side); the TPU netlist measures better at 0.25, the SoC vehicle at 0.5 — docs/AUTO_FLOORPLAN.md §3 has both |
| `snap <px> <py>` | 1 1 | every origin on a multiple of the track period per axis, so every instance of a cell sees the same track phase (sizes, gaps, margins rounded up; children low-aligned) |
| `grow <cell>=<f>,...` | none | scale a leaf's size, or a container's gap and margin — the next round's hand-down of what the route measured |
| `cols <cell>=<n>,...` | none | force a container's grid column count (`<top>` for the die) |
| `seed <n>` / `sa_iter <n>` | 1 / 2000 × n | the annealer's seed and iterations |
| `keep <n>` | 5 | arrangements each subset keeps in the slicing beam (plus its two extreme shapes) |
| `pad <n>` / `bitpitch <f>` | the PDK's (24 / 4.0) | the face rule's slack and per-bit pitch |
| `aspect <f>` | 2.0 | a container's aspect cap — a preference, taken within 15 % of the best cost, yielding to what its faces ask |
| `util <f>` | the PDK's | the standard-cell utilisation the logic area is sized at |
| `fixed <cell>,...` | none | cells whose geometry the BDB ALREADY holds — the size from the `cell` table (`resize_cell`), the children's offsets from the `cell_children` template rows (`add_inst_to_cell`) — kept untouched and stamped at every instance like any other template.  A placement the caller computed by a rule of its own: a systolic array is not a job for a placer, so `bigsoc.tcl` writes the NPU by `tpu_lib.tcl`'s own array rule and fixes it.  Refused when a fixed cell has no size or a child no offset |

Prints a table — per cell its level, instance count, children, size, how
it was sized (`face` / `area` / `macro` for a leaf with the face bits and
the area side; the packer and its utilisation and wirelength for a
container), the die with its utilisation, and the placement audit
(`FloorplannerEngine.validate()`: overlaps, outside-die).  Refuses a cell
whose instances disagree on their children (a cell is placed once and
stamped at every occurrence), an unreadable PDK, a cell containing itself.
Byte-identical on a rerun with the same options.

What it reads from the netlist, per cell: the bits of each pin grouped by
the far endpoint at the cell's level and the direction (the bundle loads,
for the faces); the nets among a reference instance's children, each pin
resolved to where the child that carries it sits (for the packing); the
bits a child exchanges through the container's ports (the boundary pull).

Vehicles: `flow/tpu/tpu.v` read without its DEF
(`test_auto_floorplan.py`), [`flow/tcl/bigsoc.tcl`](../../flow/tcl/bigsoc.tcl).
