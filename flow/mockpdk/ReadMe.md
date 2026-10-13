# `flow/mockpdk/` — a mock PDK

There is no process design kit in this repository, and a hierarchical
floorplan has to be sized against SOMETHING before any block has been
designed.  This directory is that something: a plausible technology, in
the two forms a front end needs, so that a design can go from a netlist to
a first floorplan (`auto_floorplan`, [docs/AUTO_FLOORPLAN.md](../../docs/AUTO_FLOORPLAN.md))
with no file from a foundry in hand.

| file | what it is | read by |
|---|---|---|
| [`stack.buda`](stack.buda) | the metal stack as BUDA declares it: six layers (M2..M7, three TOP), their track patterns with power rails, corner margin, min stub | `source flow/mockpdk/stack.buda` in a `.buda` flow or `buda::source` from Tcl |
| [`mock.tech.lef`](mock.tech.lef) | the same stack as a LEF technology file: direction, pitch, width per routing layer | `import_lef_tech` (all-signal patterns — LEF says nothing about a power grid) |
| [`mock.pdk`](mock.pdk) | the AREA MODEL: what a block costs before it exists — logic by gates per bit of port, memory by bits, a hard macro by size — plus the bit pitch and the face padding | `auto_floorplan pdk flow/mockpdk/mock.pdk` |

The stack is the one every Tcl vehicle declares (`tpu_lib.tcl`,
`soc_lib.tcl`, `flow/tracks/tracks.buda`), so a design floorplanned from the
mock PDK routes on the tracks every recorded table was measured on.
`test_mockpdk.py` pins that the LEF and the `.buda` agree on every layer's
direction and signal pitch — two statements of one stack are two things
that can drift.

## The area model

One fact per line.  The scalars:

```
unit_um        1.0     # layout units per micron
bit_pitch      4.0     # TOP-layer face units per bus bit (M5: 32-unit period / 8 SIGNAL slots)
stdcell_area   0.8     # um^2 per gate equivalent
util           0.65    # standard-cell utilisation inside a logic block
sram_bit_area  0.12    # um^2 per SRAM bit, array only
sram_periph    1.5     # array-to-macro multiplier
pad            24      # face slack on every face-derived dimension
facepad        48      # the floor the light face keeps whatever its bundles ask (2 x pad when unstated)
```

and the leaf rules, first glob to match a cell's name wins (`*` last):

```
leaf bank_*    sram  bits 262144                     # a 32 KB bank
leaf l3bank_*  sram  bits 1048576
leaf mul_*     logic gates_per_bit 160 gates_fixed 400
leaf phy_*     macro w 180 h 120
leaf *         logic gates_per_bit  30 gates_fixed 200
```

* `logic`: area = (gates_fixed + gates_per_bit × B) × stdcell_area / util,
  B the bits on the cell's pins — a datapath cell's logic grows with the
  width of what it processes.
* `sram`: area = bits × sram_bit_area × sram_periph — a memory macro's
  size is a property of the compiler, not of its ports.
* `macro`: a stated size.

Every number is a MOCK, chosen so that a 32-bit datapath leaf comes out
FACE-bound (its routing interface sizes it, as `tpu_lib.tcl` measured)
while a cache comes out AREA-bound (its bits do) — the split a real SoC has
and the reason the area model exists: without it every block is the size
of its pins and a megabyte of L3 is a 200-unit square.  A leaf's faces
still floor every dimension (the bits landing on them at `bit_pitch` plus
`pad`), so the area model can only make a block bigger than its pins need,
never smaller.

Replacing it with a real PDK means: a tech LEF in place of
`mock.tech.lef` (and `def_track_pattern` lines for the power grid the LEF
cannot state), and per-cell areas from synthesis (`stat -json` under a
kept hierarchy, [the LibreLane plan](../../docs/internal/librelane_hier_flow.md))
written as `macro w h` rules or as `logic` rules calibrated on them.
