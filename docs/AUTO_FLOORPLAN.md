# From a netlist and a PDK to a first hierarchical floorplan

**Status: BUILT and measured (2026-10-07).**  The command is
`auto_floorplan`; the vehicles are the TPU's own Verilog read without its
DEF, and [`flow/tcl/bigsoc.tcl`](../flow/tcl/bigsoc.tcl), a large SoC
authored as a netlist.  Every number below is from a run recorded in this
PR; the first floorplan is NOT clean on either vehicle, and that is stated
where it is measured rather than smoothed over.

## 1. The gap this fills

BUDA's hierarchical flow needs a floorplan: cells with sizes, instances
with positions, a die.  Every vehicle in the tree had one, and had it by
one of two routes:

* **drawn in Tcl** from the bus widths (`tpu_lib.tcl`, `soc_lib.tcl`,
  `array_lib.tcl`) — the engine path, a floorplan the vehicle's author
  computed;
* **imported from a DEF** (`flow/ariane133`, `flow/tpu/tpu.buda`,
  `flow/rv`), with `derive_container_bboxes` sizing the containers a flat
  DEF does not list from the leaves it placed.

A chip team starts with neither.  It starts with a **Verilog netlist and a
PDK** — `import_verilog` reads the first into the BDB and gives the cell
tree, every instance and every per-bit pin, and NO geometry: every
component unplaced, every cell 0 × 0.  With nothing to place against,
`derive_busterms` skips everything and there is nothing to route.  The
interactive Floorplanner (`bin/fp`) could seed placeholder blocks for a
human to drag; nothing made the first floorplan automatically, and the
C++ `PlacementOptimizer` the Floorplanner carries had never been run in a
top-down / bottom-up flow.

`auto_floorplan` is that step.  It reads the open BDB's netlist and a PDK
area model and writes a complete floorplan — every leaf sized, every
container packed, every instance stamped, the die set — so the hier flow
runs on it as on a hand-drawn one, and the flow's own audits then say what
the first floorplan is worth.

## 2. The flow, in one page

```
  design.v  +  flow/mockpdk/{stack.buda, mock.pdk}
      │
  source flow/mockpdk/stack.buda          the metal stack (or import_lef_tech)
  open_bdb design.bdb
  import_verilog design.v                 cells, instances, pins; no geometry
  auto_floorplan pdk flow/mockpdk/mock.pdk    <-- THE FIRST FLOORPLAN
      │
  derive_busterms 4 · add_blocks_from_bdb 0..3 · run_hier_bundler depth 4
  generate_hier_topologies · run_planner hier 5 · run_nuts · run_detailed_nuts
  check_design dnuts · (healers) · report_wirelength
      │
  report_layer_demand / buda::query demand    what the route asked of each block
      └──────────► auto_floorplan ... grow <cell>=<f> gap <g>   the next round
```

From Tcl the same commands are `buda::...`; `flow/tcl/bigsoc.tcl` is the
whole loop as a vehicle (`-iterate K`).

## 3. What `auto_floorplan` does

Bottom-up over the cell tree the netlist declares (leaves first, a cell's
level = 1 + the max of its children's, the same intrinsic level
`set_layer_caps_by_depth` uses):

**A leaf is sized from two floors, the larger winning per axis.**

* *The face rule.*  A bus has to land on a face, so each face must host
  the bits of the bundles that will land on it at the stack's bit pitch,
  plus a pad — `tpu_lib.tcl`'s lesson, where a PE narrower than its own
  psum stranded 672 of 832 bits and widening the channel made it worse.
  Read off a netlist, "bundle" means: the cell's pins grouped by the
  sibling they reach (or the world outside the parent) and the direction
  they go in, which is what the bundler lands on one face.  The groups are
  spread over four faces heaviest-first; the two heaviest go on one
  OPPOSITE pair, so the block has a *heavy* side and a *light* side — for
  a PE that reproduces `tpu_lib.tcl`'s PEW/PEH rule exactly (32 bits of
  psum + weight → 152, 8 bits of activation → 56).  The light face must
  host its biggest single bundle, not the sum of what the spread put
  there (measured: the sum sized a row of PEs 312 tall for eight 24-bit
  outputs that physically leave one face together).
* *The area model* (`flow/mockpdk/mock.pdk`): logic by gates per bit of
  port, memory by bits, a hard macro by size.  An area-bound leaf is as
  square as its faces allow.  Without a PDK the face rule alone sizes
  every leaf, and the report says so.

**A container's children are placed, and its size follows.**  One packing
per cell TYPE, so every instance is congruent by construction — what the
bottom-up family (solve once, copy) needs.  The packing minimises one
cost: the container's area over its children's area, plus `wl` × the
half-perimeter wirelength of the nets among the children in units of bits
× a block side (default 0.5).  The weight is a vehicle's choice, measured
both ways: on the TPU's own netlist `wl 0.25` keeps the row a line where
0.5 lets the activation chain's wirelength tip it into a column, so §4
measures the TPU at 0.25 — and on the SoC vehicle at NQ = 2 the default
0.5 is the better one (healed endpoint 0 / 38 against 0 / 78 at 0.25,
first audit 209 against 338 unplaced; bottom-up 16 / 430 against
33 / 708), with 0.25 reaching `-iterate 3`'s clean round HEALERLESS at
round 3 where 0.5 needs the healers (both end clean).  Three things are
in that cost that a plain packer does not see:

* *the container's own faces*: the bits crossing its boundary floor its
  sides the way a leaf's do, judged on the padded shape, so a row of PEs
  carrying 256 bits across one face comes out as the 1616 × 120 line that
  face asks for rather than a square padded to 57 % utilisation — and the
  2 : 1 aspect cap is a preference, taken only when it costs at most 15 %
  more;
* *where a bus leaves a block*: a container's ports are resolved to the
  child that carries them, recursively, so the level above places against
  the point a bus actually leaves rather than the block's centre (on the
  TPU the eight psum outputs of a row leave at eight columns; centred they
  pulled every accumulator into one clump);
* *the boundary pull*: a child carrying the container's ports belongs at
  an edge, charged its bits times its distance to the nearest edge of the
  packing it is in (the L3 controller, carrying four 32-bit links out of
  its slice, was otherwise packed between the bank rows and every link
  crossed a bank).

The packers, in `src/hier_floorplan.py` (pure Python, testable without a
build):

| children | method | what it is |
|---|---|---|
| ≤ 8 | `slice_pack` | a beam over every slicing of the set (Stockmeyer's shape-curve composition), each subset keeping its 5 best arrangements plus the two extreme shapes, so a line can survive to the top where the floors decide |
| > 8 | `cluster_pack` | agglomerative clustering by bits-between-per-member into groups of ≤ 8, each sliced, recursively; blocks that talk to nothing pair up by area |
| `place sa` | `compact_pack` over the annealer | the Floorplanner's own `PlacementOptimizer` from the grid's start, its positions legalized by constraint-graph compaction and stacked same-cell instances aligned, kept only when it scores lower than the grid |
| `place grid` / `cols` | `best_grid` | the `soc_lib.tcl` grid, the column count chosen on the same cost |

**The annealer, measured.**  `run_sa` starts from a random placement, and
with raw wirelength deltas in the thousands against `t_init` 1.0 it was a
greedy descent: rows shifted sideways, accumulators scattered.  Scaling
the terms to order one and cooling over the whole run makes it anneal; it
is still the weaker choice on these vehicles (the TPU's top level: 2.14
against the clustered slicing's 1.75 on the shared cost, 845 against 781
violations at the first audit), which is why clustering is the default and
the annealer a lever.  The `FloorplannerEngine` audits every result
(`validate()`: overlaps, outside-die) and the report says what it found.

**Then it writes.**  Cell sizes, the `cell_children` template rows (so the
Floorplanner GUI and `add_inst` read the same offsets), every instance's
box in one batched write (`BDB::set_comp_bboxes` — one HPWL recompute
instead of one per instance; a box is the cell's own w × h at the
instance's origin, so every stamped instance is UPRIGHT and a rotated
token a DEF import left on the row is reset to `N` with the box, the
count said — the geometry written is an `N` instance's, and a token
describing a box the write replaced would be a lie), childless components
marked leaf (a ports-only module is a block, which is what
`import_def_lef` + `import_verilog` conclude for the same module), the
die.

Options: `pdk <file>` `util <f>` `gap <n>` `margin <n>` `top_margin <n>`
`place auto|slice|grid|sa` `seed <n>` `keep <n>` `wl <f>` `snap <px> <py>`
`bitpitch <f>` `pad <n>` `aspect <f>` `grow <cell>=<f>,...`
`cols <cell>=<n>,...` `sa_iter <n>` `fixed <cell>,...`.  **`fixed`** keeps
a cell exactly as the BDB holds it (size from `resize_cell`, children from
`add_inst_to_cell`) and stamps it like any template: a systolic array is
not a job for a placer, and `bigsoc.tcl` writes its NPU by `tpu_lib.tcl`'s
own array rule — PEs at a pitch along a row, rows stacked, feeders west,
weight buffers north, accumulators south on their columns, the tail below,
a DMA under the tail — and fixes `pe_cell`, `row_cell` and `npu_cell`
while the engine places everything around the array (`-npu auto` lets the
engine place it too, for the comparison).  `snap` puts every origin on a track
period (sizes, gaps and margins rounded up to it) so every instance of a
cell sees the same track phase wherever its parent puts it — the
`-bottomup` row-pitch snap of `tpu_lib.tcl`, as a rule.  `grow` scales a
leaf's size or a container's channel, which is how a later round hands
down what the route measured.

## 4. Measured

The TPU's own netlist (`flow/tpu/tpu.v`, 112 instances, no DEF), the mock
PDK, `wl 0.25`:

| step | result |
|---|---|
| sizes | `pe_cell` 184 × 88 (face; `tpu_lib.tcl` draws 152 × 56 — the netlist cannot tell that pe_0's activation arrives from a different face than its psum), `row_cell` 1616 × 120 (slice, 67 %), die 2056 × 1104 at 76 % |
| `auto_floorplan` | 3.7 s |
| first audit | 781 violations in 32 bundles (0 supply-doomed seats) |
| one healer round | 36 violations in 6 bundles |

The SoC at NQ = 2 (126 leaves at five levels of cells, 407 buses, 9,064
bits; `bigsoc.tcl 2`, the NPU by the array rule).  Re-measured 2026-10-07
after the vehicle's wiring was repaired: every cache had named its tag's
`b_out` on one net PER BANK, and the emitter connected a pin to the last
net that named it, so only the last bank was wired and the other banks'
nets were dangling (Codex on #973; `hnet::net` refuses the shape now).
The tables before that read 447 buses, first audit 9 / 209, healed
0 / 38, `-iterate 3` clean, bottom-up 1 / 113 — a design with a quarter
of its cache buses missing, kept here only as what the repair moved:

| arm | floorplan | first audit (ovl / unplaced) | after the vehicle's two healer rounds |
|---|---|---|---|
| one round | die 4508 × 2434, 82 % | 16 / 528 | **0 ovl / 6 unplaced** (69 s) |
| `-iterate 3` (gap 16 → 24 → 36, starved cells × 1.25 per round) | die 4508 × 2434 → 4940 × 2754 → 4104 × 4168, 88 % | 11 / 302 (round 3) | 0 ovl / 24 unplaced (59 s for the three rounds) |
| `-bottomup` (`set_bottom_up *`, `align_bottom_up`, `on_mismatch independent`) | the one-round floorplan | 8 / 607 | 12 ovl / 115 unplaced (114 s) |

Read honestly: the loop's levers are blunt — the healerless first audits
of the three rounds (528, 441, 302) move by a fifth per round while the
demand rule grows ten cell types by the third (every small block the
NPU's and the routers' buses cross) — and the endpoint it reaches
(0 / 24) is WORSE than the one-round arm's (0 / 6), so on the wired
design a wider channel is not what the healers were short of; on the
under-wired design the same loop ended clean where one round ended
0 / 38, which read as a channel lesson and was a measurement of the
missing buses.  Before the L0 and with the engine placing
the array too (`-npu auto`, the first cut), the same arms ended 8 / 124
and 0 / 19 — a different design, so not a comparison, recorded because
that is the number the first commit carries.

The large dial, `bigsoc.tcl 4 -NC 4 -N 8 -NL3 4 -NMC 4 -NIO 8`, was
measured on the first cut (no L0, the engine placing the array: 370
leaves, 1,407 buses, 512 bundles): die 6064 × 6804 at 63 %, first audit
143 ovl / 1343 unplaced, 26 / 390 after the two healer rounds, 4 m 31 s
wall on four cores — about 1.3 % of the bits stranded at the end at
either size, the residual a class and not a size.  The reticle dial of
the current design is in §4b.

Runtime at NQ = 2: 40–52 s wall for the whole chain, the floorplan 3–7 s
of it.  The smallest dial (34 leaves) runs in 7 s.

The bottom-up arm says something about the STACK rather than the
floorplan: with V pitches 18 / 32 / 41 the x track period is 11,808 units,
so a diverse 2-D packing cannot be phase-aligned by translation — every
nested template (a core inside a cluster, a PE row inside the NPU) is
reported "sits off the parent's chosen phase, not fixable by translation",
which is what `soc.tcl` recorded for the same stack — and the misaligned
instances are solved individually, which is the `independent` policy's
meaning.  `auto_floorplan snap <px> <py>` is the rule that would align
them, at that period's cost in die.

## 4b. The reticle-limit chip

`flow/mockpdk/mock.pdk` states the mock lithography's reticle
(`reticle_w 26000`, `reticle_h 33000` µm — the industry's 26 × 33 mm
field), and `bigsoc.tcl -reticle` sizes the dial to it: one probe
floorplan at the given NQ, then NQ scaled so the die fills `-fill` (0.85)
of the reticle's area, re-floorplanned and measured against it.  The
design it runs is the deep one — soc / quad / cluster / core / l0 / bank,
five levels of cells above the standard cells — with an L0 inside every
core, L1i/L1d per cluster, an L2 per quadrant, L3 slices at the top, and
the NPU by the array rule.  `-abstract` stops at abstract NUTS (bus
segments on tracks, the NUTS-stage audit, no bit placed): the fast screen
of a floorplan, and the picture the overlap highlight is for.

`tools/render_design.py` draws the abstract stage with every OVERLAPPING
pair's two segments in black and their overlap rectangle filled yellow —
the engine's own `overlap_details`, the pairs `num_overlaps` counts; two
colours no layer uses, since the first cut drew them red and M5 is red —
so the dirt of an abstract route is where the eye goes first.

**Measured (2026-10-07).**  The probe at NQ = 4 gives a 5740 × 8220 die
(0.055 of the reticle), so the dial goes to **NQ = 62** — 5,868 instances,
4,238 leaves, 15,069 buses, 375,496 bits, five levels — and
`auto_floorplan` places it in 4.5 s: 72 top-level blocks by `cluster(9)`,
die **22000 × 22276** at 0.786 utilization, placement audit clean.  That is
**0.57 of the reticle's area, not the 0.85 asked for**: the probe scales
NQ by area assuming the die grows linearly with the quadrant count, and the
packing at 72 top blocks leaves more channel than at 14 (`cluster(8)` at
NQ = 4, 0.673), so the fill lands short and a second probe at the result
would be the fix (not built: one probe is the mechanism, and the miss is
the measurement of it).  The picture is
[`reticle_fp.png`](internal/img/bigsoc_reticle_fp.png).

**The route** (re-measured 2026-10-07 after #974, `btcl -j 1` — one
scoring thread — on 4 CPUs; the first measurement, 77 min wall of which
`run_planner hier` 65, is issue #972's): `run_hier_bundler depth 5`
(5,804 hbundles — D0 155, D1 633, D2 1544, D3 2976, D4 496) and
`generate_hier_topologies` (40,045 candidates) take about 70 s together,
**`run_planner hier` 82 s** (121 rip-ups, 176 bundles committed with
overflow, a refine pass moving 180 — the same decisions line for line,
which is what #974 measured and this run confirms), and abstract NUTS
places the 7,637 bus segments to **1,036 overlapping pairs, 119 audit
violations and 147 seat faults** at 2,834,330 units of abstract wire,
unchanged; the whole `-abstract` run is **16.2 min wall** (972 s, run
twice: 973.7 s with a rebuild sharing the box for its first two minutes,
972.3 s alone), the balance abstract NUTS, its audit and the checkpoint
writes (`-save`), with `run_nuts` the longest stage now — 514 s in
#974's single-thread replay of the same trace
([planner_runtime_972.md](internal/planner_runtime_972.md)).  The dirt is
the top level's: 108 of the 155 D0 bundles committed with overflow (max
761 units over a band's capacity) against 4 of 2,976 at D3 — the
cluster(9) packing of 72 top blocks leaves channels the L3 ring, the
memory and the NPU links cannot share, which is what the picture shows
([`bigsoc_reticle_nuts.png`](internal/img/bigsoc_reticle_nuts.png): the
yellow is where two buses hold one track).  No healer ran (`-abstract`);
with the planner at 82 s the healers are the next thing to try at this
size, each round paying NUTS's eight minutes.  The planner time was two
faults, not one (#974, `docs/internal/planner_runtime_972.md`): every
`plan_bundle` call re-summed every band of every cut (22 million on this
grid, 2015 × 1848 Hanan lines over six layers) — FIXED, the total is kept
beside the bands — and every call re-allocates and zeroes each scoring
worker's whole-grid overlay, which is OPEN and is why one scoring thread
beats four at every size here: NQ = 8 / 16 / 62 plan in 4.3 s / 8.7 s /
82 s at `-j 1` against 16.4 s / 135 s / unfinished at `-j 4` (the shipped
code before #974: 77 s / 270 s / 3,900 s), for 944 / 1,664 / 5,804
bundles, every audit identical across all of it.  The smaller dials of the
same design for comparison, at `-j 1`: NQ = 16 (1,636 instances, 1,202
leaves, 3,971 buses; die 11752 × 11740 at 0.726) reads 194 overlaps, 35
audit violations, 143 seat faults at the abstract audit in 72 s wall
(194 s at `-j 4`); NQ = 8 (900 instances, 2,283 buses; die 11236 × 7120 at
0.670) 112 / 24 / 82 in 25 s (36 s).  The NQ = 16 abstract route with its overlaps highlighted is
[`bigsoc_nq16_nuts.png`](internal/img/bigsoc_nq16_nuts.png),
its floorplan [`bigsoc_nq16_fp.png`](internal/img/bigsoc_nq16_fp.png).

## 5. What it is not, and what comes next

* **Not a placer that reads the outside of a block.**  The boundary pull
  says *at an edge*; which edge, the level above decides, and the
  bottom-up pass never knows.  The top-down half of the loop is the route
  itself, read back (`report_layer_demand`, the audits) and handed down as
  `grow` / `gap` — `bigsoc.tcl -iterate` — and that lever is coarse, as §4
  measures.  What the loop wants next is the derivation the convergence
  ladder already built for layers, pointed at geometry: the top's seats
  over a block saying which FACE and how much, as a per-cell face budget
  the next floorplan sizes to.
* **Not clean.**  A first floorplan from a netlist strands bits the hand-
  drawn vehicles do not; the healers recover most, not all.  The remaining
  class on both vehicles is a 24–32-bit bus landing on a small edge block
  (an accumulator, a feeder, a peripheral) whose window is a few tracks
  short — the #536 supply-doomed-seat class, which the face rule sizes for
  but the packing then places where the top's wires cross it.
* **Alignment for bottom-up** is `snap`, or `align_bottom_up` after the
  fact; the vehicle's `-bottomup` uses the latter.
* **The PDK is a mock.**  `flow/mockpdk/ReadMe.md` says what a real one
  replaces.
