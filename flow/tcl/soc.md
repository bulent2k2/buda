# `soc.tcl` — an SoC, deep and diverse

*Split out of [`ReadMe.md`](ReadMe.md), which this chapter had grown to
four fifths of. Every path below is relative to `flow/tcl/`, unchanged.*

```bash
btcl flow/tcl/soc.tcl                       # NQ=2 quadrants
btcl flow/tcl/soc.tcl 4                     # THE DIAL
btcl flow/tcl/soc.tcl 2 -NC 4 -NBANK 4      # wider clusters, bigger L1
btcl flow/tcl/soc.tcl 4 -bottomup           # solve one cluster, copy it
btcl flow/tcl/soc.tcl 2 -bydepth "M3 M4 M5" # a cap per intrinsic level
btcl flow/tcl/soc.tcl 8 -dry                # print the size, build nothing
```

## What the corpus was missing, and it is not size

`tpu.tcl` is a **mesh**: one cell tiled N × N, so every leaf is the same cell
at the same depth. `flow/chip` is heterogeneous but **uniform in depth** —
every leaf sits at one level. `flow/ariane133` is somebody else's real
design, and a synthesized netlist is uniquified, so nothing repeats. Between
them two shapes a real SoC has went unexercised:

* **Many different cell types, each repeating a different number of times.**
  A mesh tiles one cell, so every count is the same count. Here eleven leaf
  types spanning an order of magnitude, and the two extremes are separate
  code paths — `set_bottom_up *` copies a template to many instances and
  *freezes* a single-instance cell as a keepout with nothing to copy.
  Measured, `btcl flow/tcl/soc.tcl 2 -census`:

  ```
  sram_cell 20   tag_cell 9   fifo_cell 8
  alu_cell 4   dec_cell 4   io_cell 4   mul_cell 4   regf_cell 4
  xbar_cell 4                          <- one per router
  bridge_cell 1   memctl_cell 1        <- the singletons
  ```

  **Eleven types with two singletons**, which is narrower than what this
  section claimed first — *"most appearing once"*, with `xbar_cell` named as
  one of them when it has an instance per router (Codex P2, #930).
  `leaf_census` derives from the same argument `_fill` builds the instances
  from, so the counts are a measurement and a test pins them.
* **Ragged depth.** A leaf sits 2, 3 or 4 levels down depending on which
  subsystem it is in.

```
soc
|- quad_<q>  x NQ                                    level 4
|   `- cl_<c>  x NC                                  level 3
|       |- core -> dec, alu, mul, regf               level 2 -> 1
|       |- l1i, l1d -> tag, sram_<b> x NBANK         level 2 -> 1
|       `- rtr -> fifo, xbar, fifo                   level 2 -> 1
|- l2  -> tag, memctl, sram_<b> x NBANK2             level 3 -> 1
`- io  -> bridge, p_<k> x NIO                        level 3 -> 1
```

## What the ragged depth buys, measured

`set_layer_caps_by_depth` caps a cell by how deep its **own** content goes, so
one declaration gives **four different bands** here:

```
$ btcl flow/tcl/soc.tcl 2 -bydepth "M3 M4 M5"
[LayerCaps] level 1 -> band [lowest..M3]: 11 cell(s): alu_cell, bridge_cell, …
[LayerCaps] level 2 -> band [lowest..M4]:  5 cell(s): core_cell, io_blk_cell, l1_cell, l2_cell, rtr_cell
[LayerCaps] level 3 -> band [lowest..M5]:  1 cell(s): cluster_cell
[LayerCaps] level 4+ unrestricted:         1 cell(s): quad_cell
```

On a uniform-depth vehicle every cell is one level and this collapses to the
`reserve_top_layers` case. The bundles land at four depths too — at NQ = 2,
`D0: 9, D1: 18, D2: 20, D3: 48`.

## Measured, this container

| NQ | clusters | leaves | bundles | bit-wires | die | wall | endpoint |
|---|---|---|---|---|---|---|---|
| 1 | 2 | 37 | 57 | 1 664 | 2128 × 2016 | 1 s | clean |
| 2 | 4 | 63 | 95 | 2 656 | 4208 × 2016 | 1 s | clean |
| 4 | 8 | 115 | 171 | 5 024 | 4208 × 3200 | 2 s | clean |
| 8 | 16 | 219 | 323 | 9 328 | 6288 × 4384 | 5 s | clean |
| 16 | 32 | 427 | 627 | 18 080 | 8368 × 5568 | 14 s | clean |
| 32 | 64 | 843 | 1235 | 35 096 | 12528 × 7936 | 36 s | clean |
| 64 | 128 | 1675 | 2451 | 69 592 | 16688 × 10304 | 120 s | clean |

Clean at every size measured — **top-down**, which is what this table is.
`-caps` and `-bydepth` are clean where they were measured (NQ = 2, what the
tests run). **`-bottomup` is not clean at every size and must not be read
off this table** (Codex P2, #930): at the default channel it is clean at
NQ = 1/2/4/8 and comes back **dirty at NQ = 16, NQ = 32 and NQ = 64** (8 bits
of one bundle, the same one, the seat analysed at the end of this page). The
whole advertised dial has now been run bottom-up; a named channel is measured
to rescue 16 and 32; for 64 it is **not measured** — the attempt ran 90
minutes without finishing (see the bottom-up section). The bottom-up section below is the one to read for it, and
it carries its own per-gap tables; extending an all-sizes claim to the flag
contradicted them two hundred lines later in the same document. NQ = 32 used to be **the honest limit** here —
13 bits unplaced after 293 s — and it is neither the limit nor slow any
more: removing three stars took it to clean, and removing the phantom
coefficients (below) then took ~16 % off the wire and a fifth off the die at
every row. **Every number on this table has now moved four times** — when a
second `heal_if_dirty` round landed, when the stars went, when the
coefficients went, and when the two dead instances below were wired — and
each time because something re-ran it rather than because anyone re-read it.

The last of those moved the **bundle** and **bit-wire** columns (+3 buses at
every size: the L2 tag lookup both ways and the bridge's injection at the
chain start) and left every **die** unchanged — including at `-DW 128` and
`-CW 128`, measured — which is the point of it: the geometry was always paid
for, only the workload was missing.

## The floorplan: `band` (measured above) and `-LAYOUT compact`

Every table on this page was measured on the **historical** top level, now
named `-LAYOUT band` and still the default: the NQ quadrants in a
ceil(sqrt(NQ))-column grid, the l2 + io pair in a band above it,
left-aligned.  Its cost is visible in any picture of it — at NQ = 32 the grid
is 6 × 6 for 32 quadrants, so FOUR slots in the upper right are empty, and the
band covers a third of the width — and it is measured as the top-level block
area over the die area, 0.784 at NQ = 32.

**`-LAYOUT compact`** chooses the top level for that number: every column
count 1..NQ is a candidate, the pair either FILLS the hole a short last row
leaves (when it fits there) or sits in a band above, CENTRED, and the
candidate with the highest utilization wins, its aspect ratio held to
[1/2, 2] so a strip cannot win on area alone.  Chosen rather than asserted,
because whether the hole or the band is the better home for the pair depends
on how big the pair is against a quadrant, and the widths decide that.  What
it picks on the dial, and what routing it gets — top-down, every row clean:

| NQ | layout | grid | die | utilization | abstract WL | detailed WL | Δ det WL | wall |
|---|---|---|---|---|---|---|---|---|
| 2 | band | 2 × 1 + band | 4208 × 2016 | 0.668 | 25 200 | 525 144 | | 1.2 s |
| 2 | compact | 1 × 2 + band | 2096 × 3136 | 0.862 | 24 918 | 497 073 | −5.3 % | 1.3 s |
| 4 | band | 2 × 2 + band | 4208 × 3200 | 0.779 | 45 251 | 1 007 765 | | 2.2 s |
| 4 | compact | 2 × 2 + band | 4176 × 3136 | 0.801 | 44 137 | 963 620 | −4.4 % | 3.3 s |
| 8 | band | 3 × 3, 1 hole + band | 6288 × 4384 | 0.730 | 89 063 | 1 968 672 | | 3.9 s |
| 8 | compact | 3 × 3, pair in the hole | 6256 × 3568 | 0.902 | 94 652 | 2 000 920 | +1.6 % | 4.8 s |
| 16 | band | 4 × 4 + band | 8368 × 5568 | 0.846 | 174 599 | 3 935 746 | | 11.1 s |
| 16 | compact | 3 × 6, pair in the 2-slot hole | 6256 × 7120 | 0.885 | 180 213 | 3 986 186 | +1.3 % | 9.6 s |
| 32 | band | 6 × 6, 4 holes + band | 12528 × 7936 | 0.784 | 328 264 | 7 461 648 | | 25.8 s |
| 32 | compact | 4 × 8 + centred band | 8336 × 10240 | 0.914 | 333 298 | 7 570 868 | +1.5 % | 22.3 s |

Read it for what it is.  The die shrinks at every size (14 % less area at
NQ = 32, 24 % at NQ = 8) and the route stays clean at every size, which is
the claim the variant makes.  The wire is NOT monotone with it: the two small
sizes route shorter on the compact die and the three larger ones 1.3–1.6 %
longer — a denser die puts the global buses through less empty channel and
past more blocks, and which of the two wins is a property of the size, not
of the layout.  Stated in both directions rather than as "compact is
cheaper".  A layout is a FLOW input on this vehicle, not a routing change:
`-dry` prints the decision (`layout compact grid 4x8 holes 0 util 0.914`)
before anything is built, and the default is pinned byte for byte by
`test_tcl_soc_flow.py` so that this page's other tables keep meaning what
they say.

**Bottom-up is NOT the same story**, and the table says so rather than
letting the top-down one stand for both (the mistake this page already made
once, Codex P2 on #930).  `-bottomup`, default channel, both layouts:

| NQ | layout | die | endpoint | detailed WL | what stranded |
|---|---|---|---|---|---|
| 8 | band | 6288 × 4384 | **clean** | 2 175 864 | — |
| 8 | compact | 6256 × 3568 | ✗ 4 ovl / 0 unpl | (2 143 167) | nothing — overlaps only, the fixed-copy residue |
| 16 | band | 8368 × 5568 | ✗ 3 ovl / 8 unpl | (4 319 399) | `hb-2 seg 0`, 8 bits of `pc_0` — the seat analysed below |
| 16 | compact | 6256 × 7120 | ✗ 4 ovl / 29 unpl | (4 217 356) | five bundles, 5–7 bits each (114, 379, 417, 493, 607); NO doomed-seat advisory |
| 32 | band | 12528 × 7936 | ✗ 3 ovl / 8 unpl | (8 265 153) | the same `pc_0` seat |
| 32 | compact | 8336 × 10240 | ✗ 1 ovl / 32 unpl | (8 329 899) | one 32-bit segment (`bundle 1 seg 2`); NO doomed-seat advisory |

So the compact die is DIRTIER bottom-up at every size measured — dirty at
NQ = 8 where the band is clean, and stranding 29 / 32 bits at 16 / 32
against the band's 8 — and it strands DIFFERENT things: the band's
recurring fault is the one supply-doomed `pc_0` seat, while the compact
runs report no doomed seat at all and lose bits in bundles the band never
touches.  A parenthesised wirelength excludes the stranded bits and is not
comparable to a complete route.  The named channel, the page's measured
workaround for the fixed copy, does what it does on the band at NQ = 8:
`compact -bottomup -GAP 24 -M 24` is **clean** at 2 388 211 (+9.8 % over
the band's clean default) and `-GAP 32 -M 32` clean at 2 704 701; NQ = 16
and 32 under a named channel are not measured.

What this licenses is narrow.  Top-down, `compact` is the better die at
every size and the route is clean everywhere.  Bottom-up, the fixed copy
lands on a tighter die and the residue is larger; whether that is the hole
the pair sits in (NQ = 8 and 16), the centred band (NQ = 32) or simply the
gap the compact grid leaves between quadrants is NOT established, and the
five-bundle / one-bundle strandings are named so the census can be read
against them rather than guessed at.  The default stays `band` for that
reason and for the tables above it.

## Compaction at NQ = 8, compact layout (measured 2026-09-25)

The briefing's 16-core SoC (`soc.tcl 8 -LAYOUT compact`, 219 leaves, 323
buses, 8,272 nets) swept over the two levers the generator exposes: `PAD`,
added to every leaf's face-derived size, and `GAP` = `M`, the channel and
the margin.  `BITPITCH` stays at the stack's 4.0: lowering it would size
faces narrower than the buses that land on them, which is the face rule's
own failure rather than compaction.  Top-down, the vehicle's own healing
(`heal_if_dirty`, two rounds); one run per point.

| PAD | GAP = M | die | area vs default | first check | end | detailed WL | runtime (s) |
|---|---|---|---|---|---|---|---|
| 24 | 16 | 6256 x 3568 | 1.000 | 80 unplaced | clean | 2,000,920 | 4.4 |
| 24 | 8 | 5720 x 3272 | 0.838 | 16 unplaced | clean | 1,785,596 | 3.7 |
| 24 | 4 | 5452 x 3124 | 0.763 | 32 unplaced | clean | 1,632,362 | 5.7 |
| 12 | 16 | 5968 x 3424 | 0.915 | 1,344 unplaced | clean | 2,173,109 | 5.1 |
| 12 | 8 | 5432 x 3128 | 0.761 | 1,464 unplaced | clean | 1,926,514 | 6.2 |
| 12 | 4 | 5164 x 2980 | 0.689 | 1,136 unplaced | clean | 1,717,497 | 11.5 |
| 10 | 4 | 5116 x 2956 | **0.678** | 1,131 unplaced | clean | 1,728,474 | 12.9 |
| 8 | 4 | 5068 x 2932 | 0.666 | 1,238 unplaced | 168u, 1o, 8b | (1,414,393) | 192.8 |
| 6 | 4 | 5020 x 2908 | 0.654 | 1,455 unplaced | 232u, 3o, 14b | (1,398,283) | 256.5 |
| 4 | 16 | 5776 x 3328 | 0.861 | 1,240 unplaced | 8u, 1b | (1,856,805) | 251.7 |
| 4 | 8 | 5240 x 3032 | 0.712 | 1,717 unplaced | 200u, 3o, 13b | (1,611,430) | 195.2 |
| 4 | 4 | 4972 x 2884 | 0.642 | 1,360 unplaced | 200u, 2o, 11b | (1,392,344) | 260.3 |
| 0 | 16 | 5680 x 3280 | 0.835 | 1,224 unplaced | 40u, 7o, 16b | (1,907,641) | 178.7 |
| 0 | 8 | 5144 x 2984 | 0.688 | 1,443 unplaced | 264u, 3o, 15b | (1,585,188) | 96.5 |
| 0 | 4 | 4876 x 2836 | 0.619 | 1,200 unplaced | 488u, 16b | (1,368,267) | 134.2 |

In an end column, `u` = unplaced bits after detailed routing, `o` =
overlaps and `b` = dirty bundles: the distinct bundles holding an
unplaced bit, an audit violation or an overlap -- the final
`check_design`'s flagged bundles joined with both bundles of every
overlapping pair, read from each run's saved route (the runs re-made
from the tree with `BUDA_BDB_MEMORY_TO`, each reproducing its recorded
die and end state).  Here and in every table below.

A failing point's wire is in parentheses: a stranded bit lays none, so it
reads as a saving and is not comparable with a complete route
(`report_wirelength` says so itself).  Runtime is the whole `btcl` run,
wall clock, one run per point with the default two worker threads on a
four-core container, re-measured for this column (every end state
reproduced): what it prices is the HEALING -- a clean first check routes
in 4-6 s, a thousand-odd stranded bits heal clean in 11-13 s, and every
point that does not heal spends three to four minutes failing to.

* **The channel is free to take.**  At the default padding, 16 -> 4 is
  0.76x the area and 18 % less wire, clean, and the FIRST check gets
  cleaner (80 -> 32 unplaced) — this chapter's channel lesson again: a
  wider channel buys no routing.
* **Padding is the binding lever, and its floor is 10.**  At `PAD 10 GAP
  4` the die is 0.68x the default and the wire 13.6 % shorter, clean; at 8
  and below no channel heals.  Every point at 12 or less is dirty at the
  first check by a thousand-odd bits, so what makes these points clean is
  the healing, not the floorplan — the first check at 24 is two orders of
  magnitude cleaner.
* **Area and wire part company below 24.**  `PAD 24 GAP 4` has the least
  wire of any clean point (1,632,362); `PAD 10 GAP 4` has the smallest die
  at 5.9 % more wire.  Which one is "compact" is a choice the table
  prices rather than makes.

Not measured: bottom-up, a second NQ, repeat runs, and the judge
(`tools/independent_audit.py`) on the endpoints; the sweep's logs are not
kept.  Every point regenerates as `btcl flow/tcl/soc.tcl 8 -LAYOUT
compact -PAD <p> -GAP <g> -M <g>`.

### Round 2: squeezing the cluster, and stretching the leaves (2026-09-25)

The grid packer (`_pack_geom`) puts a container's children in a
ceil(sqrt(n))-column grid sized by its largest members, and at `PAD 10 GAP
4` that leaves a cluster 55 % leaf — while the sixteen clusters are 87 %
of the die.  (It is sixteen cores, one per cluster, not thirty-two: the
32x leaf is `fifo_cell`, two per router.)  `PACK slice` (opt-in; the grid
stays the default and every table above is byte-identical) packs every
container as the best SLICING floorplan of its children — Stockmeyer
shape curves, a Pareto curve per container carried up to the die, which
`_top_geom` picks — and `ASPECT` lets each leaf take a non-square shape of
the same area, leaves up to ASPECT:1, containers held to 2:1.  `CGAP`
splits the channel: `CGAP` between siblings in a cluster or quadrant,
`GAP` inside the leaf-level containers (core, caches, router, io).

The geometry is cheap (milliseconds to seconds, `-dry`), a route is not
(10 s clean, minutes healing), so the search BISECTED the routed axes with
a pass/fail oracle — **clean, in at most 60 s** (5x the grid's 12.9 s) —
and then CHECKED THE NEIGHBOURS of every candidate, one unit away on each
axis, because a single probe near the frontier turned out not to be a
measurement (below).  Top-down, one run per point, the vehicle's own
healing, two worker threads; `first` is the first `check_design dnuts`.

| packing | PAD | GAP | CGAP | die area | first | end | detailed WL | s |
|---|---|---|---|---|---|---|---|---|
| grid | 10 | 4 | – | 15,122,896 | 1,131u | clean | 1,728,474 | 12.9 |
| grid | 10 | 5 | – | 15,512,719 | 1,328u | clean | 1,920,872 | 10.6 |
| grid | 11 | 4 | – | 15,255,520 | 1,076u | clean | 1,774,705 | 10.8 |
| grid | 10 | 3 | – | 14,738,031 | 1,207u | 64u, 2o, 4b | – | 108.2 |
| slice | 24 | 4 | = | 12,834,160 | 344u | 10u, 2b | – | 423 |
| slice | 24 | 4 | 16 | 13,302,592 | 599u | 1u, 1b | – | 289.9 |
| slice | 24 | 8 | = | 14,301,376 | 492u | clean | 2,179,216 | 482.7 |
| slice | 24 | 16 | = | 17,473,792 | 91u | clean | 2,109,010 | 9.0 |
| slice | 24 | 16 | 4 | 16,936,240 | 202u | clean | 2,124,635 | 12.0 |
| slice | 24 | 12 | 4 | 15,505,776 | 184u | clean | 2,016,222 | 12.7 |
| slice | 24 | 11 | 4 | 15,158,020 | 218u | clean | 2,061,633 | 17.5 |
| slice | 24 | 13 | 4 | 15,857,476 | 219u | clean | 2,082,140 | 17.7 |
| slice | 23 | 12 | 4 | 15,372,000 | 221u | clean | 2,130,871 | 46.1 |
| slice | 24 | 8 | 4 | 14,138,416 | 501u | 24u, 2o, 4b | – | 258.3 |
| slice | 20 | 12 | 4 | 14,974,128 | 265u | clean | 2,046,353 | 18.6 |
| slice | 21 | 12 | 4 | 15,106,176 | 265u | 2o, 3b | – | 202.8 |
| slice | 20 | 13 | 4 | 15,319,780 | 262u | timeout | – | 300 |
| slice | 20 | 11 | 4 | 14,632,420 | 515u | clean | 2,241,934 | 73.0 |
| slice | 19 | 12 | 4 | 14,842,656 | 207u | clean | 1,895,379 | 232.9 |
| slice | 18 | 12 | 4 | 14,711,760 | 501u | clean | 2,090,949 | 280.6 |
| slice | 20 | 16 | 4 | 16,380,400 | 226u | 15 viol, 1b | – | 188.1 |
| slice | 16 | 16 | 4 | 15,833,776 | 706u | 288u, 4o, 13b | – | 171.7 |
| slice | 10 | 16 | 4 | 15,031,120 | 1,876u | timeout | – | 300 |

(`=` = CGAP equal to GAP; a timeout is the 300 s cap; failing points'
wire omitted.)  And the leaf aspect, all at `PAD 24 GAP 12 CGAP 4`
(15,505,776 square, clean in 12.7 s):

| leaf stretch allowed | die area | first | end |
|---|---|---|---|
| any, to 4:1 (containers too) | 13,076,560 | 2,594u | timeout |
| any, to 3:1 (containers too) | 13,132,332 | 2,254u | 899u, 39o, >= 29b (the overlaps' bundles not recorded) |
| short face >= bits x pitch, containers 2:1 | 15,163,200 | 606u | timeout |
| short face >= bits x pitch + 10 (`FACEPAD`, kept) | 15,505,776 (= square) | 184u | clean |

* **The whitespace is not waste; it is the routing.**  The slicing packer
  takes a cluster from 55 % to ~85 % leaf and the die to 0.74x at the same
  knobs — and none of it routes: at `PAD 24 GAP 4` the grid is clean in
  5.7 s and the slice heals to 10 unplaced in seven minutes.  The grid's
  "holes" sit beside `xbar`, the SRAMs and the core, where the local
  buses need LOW-layer room, and taking them out means buying the room
  back as channel or padding.  The robust slice frontier is `PAD 24, GAP
  11-13, CGAP 4` at 15.2-15.9M — no smaller than the grid's `PAD 10 GAP 4`
  (15.1M) and carrying ~20 % more wire.  Both clean frontiers hold the same
  6.97M of raw face demand (bits x pitch, squared, over the 219 leaves) on
  a ~15.1M die: 46 %.  Where the slack goes — holes, padding, channel —
  barely matters; how much there is, does.
* **The channel is not free inside a slice packing.**  It is in the grid
  (round 1), because the holes already carry the local routing.  With
  them gone, slack INSIDE the leaf-level containers is what the router
  wants and slack between clusters buys nothing: CGAP 16/32 over GAP 4 made
  the first check worse, GAP 12 over CGAP 4 made it clean.
* **Stretching a leaf spends its face, so at the padding floor there is
  nothing to spend.**  Every leaf is already the square its own bits size;
  a stretch at constant area narrows a face below the bits the router
  may land there (it picks the nearest face, not the widest), and
  unbounded stretches stranded 2,254-2,594 bits.  Even a 1.25:1 stretch of
  the 32-bit leaves — leaving 8 units of slack on the short face —
  stranded 606.  With the short face held to `bits x pitch + FACEPAD` (10,
  the padding floor measured above) aspect changes NOTHING until PAD 32,
  and at PAD 48 it saves 8 % of a die (17.4M) that is still larger than
  square leaves at PAD 10-24.  Aspect freedom at constant area does not
  pay on this vehicle, and the reason is the face rule, not the search.
* **A single probe near the frontier is not a measurement.**  `PAD 20 GAP
  12` came back clean in 18.6 s at 14.97M — 1 % under the grid — and every
  neighbour disagreed: PAD 21 ends on 2 overlaps, GAP 13 times out, GAP 11
  and PAD 19 heal clean in 73 s and 233 s.  The grid's neighbourhood
  (PAD 10-11, GAP 4-5) is clean in 10.6-12.9 s every time.  Healing time
  near the frontier is heavy-tailed in the geometry, so a bisection needs
  a neighbourhood check at the point it lands on, and the sweet spot is
  the one whose neighbours agree: **grid, `PAD 10-11 GAP 4`**, 0.68x the
  default die in ~12 s.

Knobs: `-PACK slice`, `-ASPECT <1..4>` (leaves; needs slice), `-CGAP <n>`,
`-FACEPAD <n>` (default 10), `-KEEP <n>` (curve points kept per container,
default 12, at least 3; 24 finds a 1.8 % smaller die at 5x the geometry
time, though every curve is thinned so a larger KEEP is not guaranteed a
smaller die).  PACK slice packs at most 10 children per container (it
tries every split, 3^n) and refuses more.  Under `-FIX` the two leaves
shared across parents keep their square, so a plan index names one
geometry whatever `-ASPECT` allows.  A
point regenerates as `btcl flow/tcl/soc.tcl 8 -LAYOUT compact -PACK slice
-PAD <p> -GAP <g> -M <g> -CGAP <c>`; `test_a_slice_packing_is_a_legal_floorplan_and_never_worse_than_the_grid`
holds the geometry.

### Round 3: each cell routed alone, and what it predicts (2026-09-25)

Round 2's slice layouts carried ~20 % more wire than the grid, which
suggested a packer that weighs connectivity as well as area.  Before
building one, each container was routed ALONE, to measure what its
children say to each other and to the world, what that asks of each
child's edges, and whether a plan's local cost predicts its chip cost.

**The instrument.**  [`soc_local.tcl`](soc_local.tcl) builds a design
whose top is ONE instance `u` of a cell: its internal buses are the ones
`build_buses` gives a representative occurrence (`quad_1/cl_0` for the
cluster -- mid NoC chain, with an io pad), and every external bus ends on
a port block in a ring round the cell, on the side where its far end sits
in the chip the same knobs build.  It routes with `soc.tcl`'s own hier
flow into a file BDB, and [`tools/cell_face_demand.py`](../../tools/cell_face_demand.py)
reads the stored tables (the judge's rule: no engine import) for bits
between children, bits per child FACE against the face's capacity at the
bit pitch, and local wire.  Plans come from `soc_vehicle::enum_plans`
(every ordered slicing arrangement within a slack of the smallest,
deduplicated) and are built through the vehicle's `-FIX {<cell> k}` knob,
so plan k is one geometry locally and in the chip (tested).

**Connections** (netlist facts, not route-dependent):

| cell | internal | external |
|---|---|---|
| core | alu-regf 64, alu-dec 32, mul-regf 32 | dec 48 and regf 48, all to the caches |
| cluster | core-l1i 48, core-l1d 48, l1d-rtr 32 | all 104 bits on `rtr` (NoC W 48, N 56 incl. an 8-bit pad) |
| rtr | **none** | every bus |

**Edge demand** found the one leaf the face rule under-sizes.  The rule
sizes a leaf from its heaviest single PIN, which assumes every pin gets a
face of its own; `regf_cell` has FIVE bus endpoints (4 x 32 + 16) on four
faces, so the best spread still puts 48 bits on one face -- 192 + PAD
where the rule gives 128 + PAD -- and the local core run put 64 bits on
its south face (capacity 34 at `PAD 10`).  Every family stranding in the
chip at `PAD 10 GAP 4` ends there: `m`, `x` and `dd` are 854 of its 1,131
first-check bits.  No other leaf is short this way.  Sizing `regf` for
four faces costs no area in the grid layout (the core still fits its
slot) and roughly halves the first check below the floor -- but it moves
no end state the right way:

| PAD (GAP 4, grid) | first check | first, regf four-face | end | end, regf four-face |
|---|---|---|---|---|
| 12 | 1,136 | 1,197 | clean, 11.5 s | 32u, 1o, 2b |
| 10 | 1,131 | 1,285 | clean, 12.9 s | clean, 86 s |
| 8 | 1,238 | 771 | 168u, 1o, 8b | 304u, 11b |
| 4 | 1,360 | 616 | 200u, 2o, 11b | 360u, 12b |
| 0 | 1,200 | 704 | 488u, 16b | 552u, 18b |

(Two runs at a time, so the times are loaded; one run per point.)

**Plans, and whether the local run predicts the chip.**  At `PAD 10 GAP
4` the cluster has 165 slicing plans within 50 % of its smallest, all
routed locally in under two minutes; within one shape they differ widely
(716 x 972: 64 to 176 bits unplaced after healing, the grid's own
arrangement 112).  Nineteen were then routed in the full chip, healing
off, and the chip's first check ranged 965 to 2,281 bits -- more than
the +-10 % that neighbouring geometries differ by, so the plan matters.
Nothing measured at cluster scope ranks it (Spearman against the chip's
first check, 19 plans):

| predictor | rho |
|---|---|
| local first check | 0.24 (0.75 on the first nine -- chance) |
| local end state after healing | worse; the best-healing plan is the chip's worst |
| internal bits x distance between children | -0.22 |
| external bits x distance to the NoC sides | 0.01 |
| cluster area | -0.15 |

Two measured reasons.  The context changes what strands: the same
cluster at the same plan strands `id`, `x`, `l1dd` and its port buses
alone and `m` and `dd` in the chip.  It is not the track phase: with
every layer's tracks shifted onto the chip's phase (`-at chip`; the die
the same size), the local run still strands `id` (43 bits), `x` (19) and
`l1dd` (26), one bit of `m` and none of `dd`, against 46, 20 and 25 at
the ring corner; the phase moves mainly the port buses (`nl` 64 to 96,
`nr` 16 to 0; 185 bits against 173 in all).  Two earlier cuts of
`-at chip` moved the seat instead: the first clamped it to the ring in y,
keeping only the x phase, and read the two as bit for bit identical
(withdrawn); the second moved it a whole track period past the ring,
which here added 306 to the die's height (184 bits) but for a reference
near the left edge would have made the die ~15x wider than the chip.
The ring width does not matter (36 to 400).  The predictor
table above was measured at the ring corner.  And fixing a
cluster's shape re-lays out every quad and the top, so a plan's chip
result mixes its own cost with a different global floorplan -- though
among the seven plans that share one shape and one die the chip still
spans 1,084 to 2,281.

* **Not built: a packer driven by these proxies.**  None predicts, so a
  packer optimizing one would optimize noise.
* **What would decide it** is the chip itself, sampled: the first check
  is stable to +-10 % under a one-unit perturbation and the healed end
  state is not, so a plan search has to score candidates on the chip's
  first check (about 25 s each, healing off), several samples per
  candidate, and heal only the survivors.
* **`regf` is the finding to keep**: a real under-sizing the face rule
  cannot see, found only by measuring edges.  It is now the opt-in knob
  `-FACES 2|4` (round 4), not the default, because on present evidence it
  makes the first check better and the endpoint no better.

Regenerate: `btcl flow/tcl/soc_local.tcl cluster_cell -plan <k|grid|current>
-slack 0.5 -bdb out.bdb -PAD 10 -GAP 4 -M 4 -PACK slice`, then
`tools/cell_face_demand.py out.bdb`; the chip at a plan is `soc.tcl 8
-LAYOUT compact -PACK slice -FIXSLACK 0.5 -FIX {cluster_cell <k>} -noheal`.

### Round 4: `regf` resized, and plans scored on the chip (2026-09-25)

**`-FACES 2|4`** sizes a leaf for ALL its pins rather than its heaviest
one.  Each leaf's pins -- read off the same `build_buses` the design is
wired by, with the engine calls caught, never from a hand table -- are
spread over its faces heaviest-first onto the lightest, a pin staying one
place on one face.  `FACES 4` makes every face hold the heaviest face that
spread leaves; `FACES 2` widens only the N/S pair and lets E/W keep their
per-pin size.  At the defaults the derivation grows exactly one leaf,
`regf_cell`, from 138 to 202 square at PAD 10 (FACES 2: 202 x 138); in the
grid layout that is free (the core still fits its slot, die unchanged),
in the slice layout FACES 4 widens the die 7 % and FACES 2 is free.
Default 0, byte-identical.

**The search** (`tools/soc_plan_search.py`, `FACES 4`, `PACK slice`, `PAD
10 GAP 4`): all 204 cluster plans within 50 % of the smallest, each routed
through the whole chip with healing off (about 4.5 s each, four at a
time); the twelve best re-sampled at PAD 11 and at GAP = M = 5 with the
same ARRANGEMENT (matched on its slicing tree, children named by type --
`H(V(rtr_cell,core_cell),V(l1_cell,l1_cell))` -- since a plan's index
moves when sizes do); the four best by mean healed in full beside the two
packers' own choices.

The first check falls with area, and at every area plans differ a lot:

| die area | plans | best first-check score | median |
|---|---|---|---|
| 12,017,872 | 4 | 1,354 | 1,447 |
| 13,829,608 | 12 | 832 | 1,693 |
| 13,987,792 | 12 | 744 | 2,051 |
| 14,498,848 | 4 | 656 | 784 |
| 15,407,392 | 12 | 544 | 672 |
| 17,496,352 | 40 | 560 | 1,085 |

(score = unplaced + 16 x overlaps, 192 of 204 parsed.)  Re-sampled, the
best screened plans held: plan 60 scored 544 at all three samples, plan
112 544/568/576, plans 164 and 186 564/568/664 and 560/560/684 -- the
spread is within a quarter of the score, which is the +-10 % the first
check was measured to move by.  (This read differently at first: the
harness matched a plan across perturbations by pairwise child order,
which maps 204 plans onto 156 keys, so 112, 164 and 186 were re-sampled
as OTHER plans and appeared to jump to 2,008-4,130.  Codex P1 on #961;
the harness now matches each plan's slicing tree by child type, unique by
construction, and the numbers here are the re-run.)  Then the heal:

| candidate | die area | first check | end | detailed WL | s |
|---|---|---|---|---|---|
| plan 60 | 15,407,392 | 512u/2o | 480u, 15b | – | 135.2 |
| plan 112 | 16,852,000 | 512u/2o | 480u, 8o, 23b | – | 77.7 |
| plan 61 | 15,407,392 | 558u/4o | 320u, 10b | – | 127.2 |
| plan 66 (first run) | 15,407,392 | 512u/5o | 416u, 13b | – | 190.8 |
| plan 129 | 16,852,000 | 560u/3o | clean | 1,976,268 | 10.9 |
| slice packer's own choice | 12,017,872 | 1,745u/53o | 128u, 2o, 9b | – | 208.0 |
| grid packer, FACES 4 | 15,122,896 | 1,285u/45o | clean | 1,486,953 | 84.6 |
| grid packer, FACES 0 (round 1) | 15,122,896 | 1,131u/84o | clean | 1,728,474 | 12.9 |

* **The first check does not predict the heal.**  The four best first
  checks of the search end 320-480 bits short; the grid, with more than
  twice their first-check failures, heals clean.  Round 3 found that a
  cell routed alone does not predict the chip; this is the chip not
  predicting itself one step later.  A search scored on the first check
  optimizes the wrong thing, so an honest score is the HEALED result, at
  up to five minutes a plan.
* **No plan beat the grid.**  The one fast clean plan is 11 % larger.
  The sweet spot stays the grid packer at `PAD 10-11 GAP 4`, `FACES 0`:
  0.68x the default die, clean in about 12 s.
* **`FACES 4` on the grid** heals clean with 14 % less wire (1,486,953
  against 1,728,474) but takes 85-88 s (two runs) where the per-pin rule takes 13 --
  shorter wire, longer heal.

Regenerate: `tools/soc_plan_search.py --knobs "-PAD 10 -GAP 4 -M 4 -FACES
4" --out <dir>` (every run cached in `<dir>/runs.json` under its command line and the code it ran on, so a re-run of the same code resumes and one after a checkout or rebuild measures again; a run that dies without a verdict is reported as an error and not cached).

## Every endpoint, every bit, every instance: the face rule read three ways

The face rule — *a leaf's size is derived from the bits that land on its
faces* — has to hold for **every endpoint of a bus**, on the **sum at each
pin**, and for **every instance** rather than every cell type. This vehicle
broke all three readings in turn; the second was hiding everything else on
this page, and the third was invisible to every guard written for the first
two.

**Per bus.** `sram_cell` drives `l1id_*[IW]` out of each `l1i` bank and
`alu_cell` receives `i_[IW]`, both sized from `DW`; `xbar_cell` was `2*DW` on
both axes while `nr_[AW]` joins two routers directly and `pc_[CW]` arrives
from an io pad (Codex P2 × 2, #930). Every face became a `max` over the buses
landing on it. (This cited `id_[IW]` until the bank wiring landed — `id_` now
leaves `l1i/tag.d_out`, so it names no SRAM at all, and a stale dependency is
what a future face change would follow. Codex P2 again.)

**Per pin.** A pin is *one place*, so what has to fit there is every bit that
lands on it, not the widest bus taken alone. `NBANK`/`NBANK2` were advertised
as dials while only `bank_0` was ever wired — at the defaults 11 of the 20
`sram_cell` instances carried **no net**, so raising either knob added filler
geometry and left the routed workload alone (Codex P2, #930) — and wiring
them put each `NBANK` bank on one `tag.d_in`, each `NBANK2` bank on one
`mc.d_in` and every peripheral of a cluster on one `xbar.p_in`: the same
**star** the memory controller taught (next section), in three more places,
with each of those buses passing the per-bus check. `check_bus_faces`
accumulates per **endpoint path** now — and refuses outright when it cannot
resolve a path, since a guard that checks nothing must not pass — so
`NBANK`/`NBANK2`/`NIO` grow the cells they feed.

**Per instance**, which is a different question and the one the guards kept
answering by accident. Every check on this page reduces the design to cell
**types** before comparing — a census by type, a declared size by type — and
an unwired *occurrence* of a type that is wired elsewhere is invisible to all
of them, however exact they are about the type. Two were:

* **`l2/tag`** — an L2 tag array instantiated and named by no bus, in the
  census and in the die with nothing on it, while the four live L1 tags
  answered for `tag_cell` (Codex P2, #930). Now wired the way a controller
  really works: the address out to the tag, the tag's answer back, on the
  controller's own pin (`mc.t_in`, not `mc.d_in`, which already aggregates
  every bank — the per-pin rule above). Both faces already held it, so this
  wired a dead instance **without moving a single size**, which is the honest
  reading: the geometry was always paid for, only the workload was missing.
* **`quad_0/cl_0/rtr/fi_in`** — found by the guard written for the first one,
  in the same run. `nl_` wires hop *i* to hop *i+1*, so the chain's first hop
  has nothing upstream of it — one dead `fifo_cell` at *every* size, that
  being a derivation rather than an extrapolation from the two sizes measured:
  a chain has exactly one first hop whatever `NQ`/`NC` say. A peripheral
  bridge is a NoC master, so it injects at the chain head — one bus, and the
  physical answer. `bridge_cell` therefore takes a `DW` pin beside its
  `NIO*CW` star, so its face is `max` of the two; that does not move the
  default (both are 32 bits) and at `-DW 128` it grows 152 → 536 units with
  the **die unchanged**, the io block not being what binds there.

  The cheaper-looking fix was to close the chain into a **ring** (`mr` back to
  `chain[0]`), and it is wrong for a reason worth keeping: that leaves every
  `fi_in.in` with exactly one bus, which would make `fifo_cell`'s `2*DW`
  phantom — and that coefficient is this vehicle's one *measured-honest*
  multiplicity. A fix that wires a dead instance by deleting the aggregation
  another face is sized from just trades one fault for the other.

So `soc_vehicle::leaf_paths` enumerates the leaf **instance paths** (and
`leaf_census` is now derived from it, one walk), and the face test requires
each path to appear as some bus's endpoint *before* anything is reduced by
type. Mutation-tested both ways: delete either bus and it names the instance,
in every regime.

What it cost was never the sizes. It was a **causal claim**, made twice.
First `-AW 128` was reported as 128 bits unplaced on a *supply-doomed seat*
and called "the channel from the other side". Then `-DW` and `-CW` were
written up as a keepout cull and a dead span "deliberately not diagnosed
further here". Every one of them was a face:

| knob at 128 | first cut | per-bus `max` | per-pin **sum** |
|---|---|---|---|
| `-IW` | 512 unplaced | **clean** | **clean** |
| `-AW` | 128 unplaced | **clean** | **clean** |
| `-DW` | 65 unplaced | 65 unplaced | **clean** |
| `-CW` | 741 unplaced | 166 unplaced | **clean** |

(NQ = 1, each knob alone.) A symptom the tool reports is not a cause: the
advisory named the seat every time and never once the reason for it — and
the two readings that sounded most like physics, a keepout cull and a dead
span, were the two that survived longest.

**The mirror**, which cost a fourth reading of the same rule: a knob may
appear in a cell's size **only if** some bus of that width lands on that
cell. Three terms broke it — `dec_cell`'s `2*CW`, `tagpin`'s `CW`,
`bridge_cell`'s `DW` — each a **phantom dependency**, a knob sizing a cell
no bus of that width ever touches. Only the first was live, and it mattered:
`-CW 128` grew the whole core/cluster stack for nothing (die 4576 × 5600
against 4320 × 4704), so that experiment was measuring unrelated whitespace
and could credit a clean route to the wrong geometry. The other two were
dominated at the defaults and bind at `NBANK`/`NIO` = 1. Removing all three
leaves the defaults **unchanged**, which is precisely why reading the table
never found them.

`bridge_cell`'s `DW` has since come **back**, and honestly: it was a phantom
because no `DW` bus landed on the bridge, and the bridge's injection into the
chain start (above) is one. The form was never wrong — the bus was missing.
That is worth keeping as the shape of the mistake: a term with nothing behind
it and a term whose bus the design forgot to declare look identical in the
expression, and only the endpoints tell them apart.

The guard for all of this is
`test_every_leaf_face_is_exactly_the_bits_that_land_on_it`, which **replaced**
a perturb-and-diff version: that one asked whether a knob *appears*, so it
could not see a wrong COEFFICIENT at all, and needed one regime per knob to
defend. The equality form needs neither.

## The lesson it paid for: a face is derived, a channel is not

A leaf's size **is** derived from the bits that land on its faces — that is
`tpu.tcl`'s lesson and it holds. The arc that says a CHANNEL is different is
worth keeping, because every step of it looked right:

The first cut derived every face and left gaps flat, and `check_design`
reported a supply-doomed seat — *19 signal tracks in a 32-bit bus's placed
window*. A seat's window is a Hanan band, i.e. the gap between two blocks, so
the reading was that a gap must host a whole bus (~136 units at the M6
per-bit channel). Deriving it that way **does** clear the symptom.

It is still the wrong fix, and only the measurement says so. The real cause
was a **star on the memory controller**: the first netlist wired every
cluster straight to `l2/mc`, which at NQ = 4 gave 72 bits unplaced with four
bundles committing on planner overflow and *no* supply-doomed seat — real
congestion, not sizing. A memory controller is arbitrated, not wired to eight
masters in parallel, and its face is sized for one bus. With the NoC chain in
its place a wider channel is **pure cost**:

| GAP = M | NQ = 8 | NQ = 16 |
|---|---|---|
| 16 | clean, WL 1,968,672 | clean, WL 3,935,746 |
| 48 | clean, WL 2,826,579 | clean, WL 5,553,364 |
| 96 | clean, WL 4,024,854 | clean, WL 8,034,294 |
| 144 | clean, WL 5,290,204 | clean, WL 10,561,015 |

Every row routes, so the wider channel buys **nothing** and costs wire
monotonically — which is `tpu.tcl`'s lesson in the direction it recorded it:
*the channel was never the binding constraint*, so widening it only inflates
the die and makes every wire longer.

Where the design used to **fail**, a channel was not the lever either — and
it is not what fixed it. Swept at DW = 128 (a 4× datapath), NQ = 4, this
table read 65 bits unplaced at GAP 16 and never reached zero at any width
tried (46 at 96; NQ = 1 ran the same way, 65 down to 31 at GAP 160), and it
concluded the bits were **culled for crossing a keepout** on one cross-level
NoC leg. The advice was right and the *cause* was wrong: three stars were
still in the netlist, and with those faces sized from what lands on them
`-DW 128` is clean at every gap, the channel still pure cost:

| GAP = M (NQ = 4, DW = 128) | 16 | 32 | 64 | 96 |
|---|---|---|---|---|
| detailed WL | 9,253,086 | 9,865,420 | 11,400,415 | 12,698,056 |
| endpoint | clean | clean | clean | clean |

`-DW` alone: `IW` sizes `dec_cell` and every cell enclosing it, so setting it
too would measure a different design from the one recorded here. So `GAP`
and `M` are plain constants across the whole NQ dial **and** across a 4×
datapath — a stronger claim than the one it replaced, and one reached by
removing faces rather than by tuning a gap.

The last part of the lesson is that these numbers get re-run, and have now
moved **three times**. An earlier sweep read as *non-monotone* (clean at GAP
40/48/80, stranded at the rest) and was presented here as the reason no
derivation could exist; a second healer round changed the answer at every
point; the star fix turned a whole failing table clean; and the phantom
coefficients then took ~25 % off every number in it. A recorded
measurement nothing re-runs decays into a claim, so
`test_a_wider_channel_is_not_the_lever_a_wider_bus_needs` runs the cheap end
of both directions on every test run.

## `-bottomup`: every sizing fix pushed the channel further out

`-bottomup` used to widen the channel behind the caller's back (`GAP 24
M 24`) to clear two overlaps at NQ = 4, and the sweep that justified it read
**non-monotone** — 16 ✗, 24 ok, 32 ok, 48 ok, **64 ✗**, 96 ok — written up
here as evidence that a *fixed* copy turns the channel into a phase lottery.

Every sizing fault since has pushed that need further out, which is the
pattern worth recording. The **star faces** took it from NQ = 4 to NQ = 8.
The **phantom coefficients** — `2*DW` on four cells with nothing behind it —
took it from NQ = 8 to NQ = 16. At the default channel the flag is now clean
through NQ = 8, and at NQ = 8 *every* gap is clean:

| GAP = M (NQ = 8, `-bottomup`) | 16 | 24 | 32 | 48 | 64 | 96 |
|---|---|---|---|---|---|---|
| result | ok | ok | ok | ok | ok | ok |
| detailed WL | 2,175,864 | 2,401,550 | 2,595,391 | 3,031,186 | 3,571,607 | 4,329,443 |

Wiring the two **dead instances** (above) did *not* push it further — NQ = 8
stays clean and NQ = 16 stays dirty at the default channel — which is worth
recording as the negative result it is: three sizing faults moved this
threshold and this one did not, because it added workload without changing a
single face.

NQ = 16 is where a fixed copy at the default channel first comes back
**dirty** — the observation, not a claim about what it needs, which is argued
at the end of this page and is not a channel — and there the gap sweep is
**genuinely non-monotone**, measured on the honestly sized design this time
rather than as an artefact:

| GAP = M (NQ = 16, `-bottomup`) | 16 | 24 | 32 | 48 | 96 |
|---|---|---|---|---|---|
| result | ✗ 3 ovl / 8 unpl | ok | ✗ 8 unpl | ✗ 1 ovl / 8 unpl | ok |
| detailed WL | 4,319,399 | 4,648,190 | 5,058,836 | 5,856,977 | 8,621,243 |

Re-measuring this one **changed** a row rather than confirming it: GAP 48 was
clean last revision and fails now, so the working region has shrunk to **two
of the five gaps swept**. That is a real effect of the three added buses, not
a re-reading — and it makes the case against a built-in number stronger while
making the practical advice narrower, which is worth stating in that order.
At this size a bottom-up run has to sweep, not guess.

That irregularity was reported once and **withdrawn** when its cause turned
out to be the stars. It is back on different evidence: with the faces honest
and the coefficients gone, **both 32 and 48** are *worse* than 24 at NQ = 16.

What that does and does not license is worth being exact about, because the
first write-up got it wrong (Codex P2, #930). Non-monotonicity rules out
**"increase until clean"** as a search — a caller stepping up from a clean
24 lands on a dirty 32 — so a sweep has to be a sweep and not a ramp. It does
**not** show that no fixed default exists, and the table above refutes that
reading directly: **GAP = M = 96 is clean at every size measured** — NQ = 2,
4, 8, 16, and NQ = 32 (measured since this paragraph was written, so the range
is wider than it claimed). A conservative built-in value is available.

The argument against building it in is **cost**, which is this page's own
lesson pointed at the flag:

| NQ | cheapest clean gap | detailed WL | at GAP 96 | ratio |
|---|---|---|---|---|
| 2 | 16 (the default) | 591,230 | 1,231,210 | **2.08×** |
| 4 | 16 (the default) | 1,088,063 | 2,210,154 | **2.03×** |
| 8 | 16 (the default) | 2,175,864 | 4,329,443 | **1.99×** |
| 16 | 24 | 4,648,190 | 8,621,243 | **1.85×** |
| 32 | 24 | 8,938,821 | 16,671,389 | **1.87×** |

The NQ = 32 row was missing while the sentence below claimed the conclusion
held at every measured size (Codex P2, #930) — the fifth measurement added a
few paragraphs later and not carried up into the table it belongs in, which
is the same summary-versus-detail gap as the top-down/bottom-up line, and in
the same document.

A default of 96 would roughly **double the wire across the five sizes in that
table** (NQ = 2 … 32) — including the three already clean at the default 16
and needing no channel at all — to rescue the **two** that are not. **NQ = 64
is excluded from this argument, not covered by it** (Codex P2, #930): it is
dirty at the default and *no* named-channel run there has finished, so
neither a clean gap nor a cost is known for it, and presenting an unfinished
sweep as part of a whole-dial cost claim is exactly the overreach the rest of
this page keeps retracting. Over the five it does cover, this is precisely
the trade the *"a wider channel buys no routing and costs wire
monotonically"* measurement above refuses, so the flag declines to make it on
the caller's behalf. A bottom-up run **at NQ = 16 or NQ = 32** names the
channel itself and measures it: `soc.tcl 16 -bottomup -GAP 24 -M 24` (or
`32 -bottomup -GAP 24 -M 24`).

### What the bottom-up failure actually is

Chasing the threshold found the cause, and it is not a channel — the fifth
time on this vehicle that a channel reading turned out to be a seat or a
face. **Every** failing bottom-up run strands the same eight bits of the same
bundle:

```
hb-2   D0  cross-level  "DRV:io/p_0|REC:quad_0/cl_0/rtr/xbar"  nets=8
```

That is `pc_0` — the first io pad into cluster 0's crossbar, 8 bits of `CW`.
Measured, and the invariant is the point:

| NQ | GAP | the seat the tool reports | endpoint |
|---|---|---|---|
| 16 | 16 | `bundle 2 seg 0` M7 (TOP), 7 tracks < 8 bits | ✗ 3 ovl / 8 unpl |
| 16 | 24 | `bundle 2 seg 0` M4 (LOW), 0 tracks < 8 bits | **clean** |
| 16 | 32 | `bundle 2 seg 0` M7 (TOP), 7 tracks < 8 bits | ✗ 0 ovl / 8 unpl |
| 16 | 48 | `bundle 2 seg 0` (LOW) | ✗ 1 ovl / 8 unpl |
| 32 | 16 | `bundle 2 seg 0` M7 (TOP), 7 tracks < 8 bits | ✗ 3 ovl / 8 unpl |
| 32 | 24 | `bundle 2 seg 0` M4 (LOW), 0 tracks < 8 bits | **clean** |
| 64 | 16 | `bundle 2 seg 0` M7 (TOP), 7 tracks < 8 bits | ✗ 7 ovl / 8 unpl |
| 32 | 16 **+ `set_max_bundle_bits 4 for pc_`** | **none reported** — 0 advisory lines against 4 without it | ✗ 3 ovl / **0 unpl** |

The same bundle, the same segment, the same eight bits, across a **4× span**
(NQ = 16 → 64) and at every gap — **including the clean ones**.

That "4×" is now measured rather than asserted, and the history is the point.
The paragraph originally claimed *"two sizes 4× apart"* while the table held
only NQ = 16 and 32, which are **2×** apart — plain arithmetic, wrong from the
day it was written, surviving my own audit of this very section plus four
review rounds, and wrong in the direction that *flattered* the claim by
overstating its scale range. Codex caught it (P2, #930) and offered two
remedies: say 2×, or measure something fourfold away. The first landed at
once; then **NQ = 64 bottom-up** was run and reports the same `bundle 2
seg 0`, M7, 7 tracks < 8 bits. So the span is genuinely 4× — 32 → 128
clusters, 427 → 1675 leaves — and the claim ends up *stronger* than the one
that was wrong, rather than merely narrower.

**What repeats is the segment, not the seat** (Codex P2, #930). Read the rows:
the assigned layer moves M7/TOP → M4/LOW → M7/TOP, and changing `GAP` moves
the placed span and slide window too — and a *seat* is defined by exactly
those things, since `_report_doomed_seats` derives it from the segment's
**assigned layer** and its span × slide window
(`src/buda_session/nutsflow.py`). So two rows naming different layers are
different **seats**. What the table establishes is that the same *logical*
bundle/segment — `hb-2` seg 0, the eight bits of `pc_0` — is repeatedly
supply-doomed however the gap is set; what varies is both the seat it lands on
*and* whether the **healers clear it**. Calling the seat invariant overstated
the evidence, and it did so one paragraph above admitting the healing
mechanism is unknown — the same fault as the withdrawn phase-lottery
mechanism, committed in the sentence written to replace it.

And the tool has been saying the category in plain text at every one of those
runs:

```
Advisory: 1 supply-doomed seat(s) — static width-infeasibility,
          not reservation conflicts.
```

So this is the **#536 supply-doomed seat** class, not congestion. A gap sweep
works on it only indirectly, by changing the geometry the healers then have to
repair — which is exactly why the curve is non-monotone and why no threshold
predicted it. *Why* healing succeeds at 24 and fails at 16/32/48 is **not**
established here, and after withdrawing one asserted mechanism this round I am
not about to supply another.

#### The repeat is not size-independent either

Two further measurements bound it, and the second is the one that matters:

| run | doomed seat? |
|---|---|
| NQ = 2 / 4 / 8, `-bottomup` | **none reported at all** |
| NQ = 16, **plain** (top-down) | clean, **none reported at all** |
| NQ = 16, `-bottomup` | the seat, at every gap swept |

The same design, at the same size, with the same `pc_0` and the same `io_cell`
face, is **clean top-down**. So the seat is a product of the **bottom-up fixed
copy at scale**, not of a declared width — which is why *"a wider `io_cell`
face"* has been dropped from the remedies below. It was an untested guess, and
the top-down run is evidence against it.

Trying to make the seat appear at NQ = 1 — to give the test a live mutation —
failed three times, and the failures are independent evidence for the same
reading:

| attempt | result |
|---|---|
| `io_cell` face ÷ 4 | the flow **refuses at declaration** — a too-narrow face never reaches the router |
| `-GAP 4`, `-GAP 8` | clean, no seat: a starved channel does not produce one either |
| `-CW 64` (4×) | clean, no seat |

At this size the seat is reachable by **neither width nor channel**, so the
`not in stdout` assertion in `test_bottom_up_routes_the_diverse_hierarchy_clean`
is labelled in the test as a **canary** rather than as a guard with a
demonstrated mutation. The negative result is worth more than the assertion.

#### The seat lever, measured rather than asserted

The consequence for the advice is that "a fixed copy needs a channel" was
always naming the symptom. The lever a 7-tracks-for-8-bits seat wants is the
**seat** — and that is now measured, one knob at a time, **at two sizes**:

| remedy (NQ = 16 `-bottomup`) | ovl | unpl | viol | detailed WL |
|---|---|---|---|---|
| none (default GAP 16) | 3 | 8 | 8 | (4,319,399) |
| `set_max_bundle_bits 4 for pc_` | 3 | **0** | **0** | 4,284,321 |
| `-GAP 24 -M 24` | **0** | 0 | 0 | 4,648,190 |
| both | 0 | 0 | 0 | 4,762,874 |

| remedy (NQ = 32 `-bottomup`) | ovl | unpl | viol | detailed WL |
|---|---|---|---|---|
| none (default GAP 16) | 3 | 8 | 8 | (8,265,153) |
| `set_max_bundle_bits 4 for pc_` | 3 | **0** | **0** | 8,293,531 |
| `-GAP 24 -M 24` | **0** | 0 | 0 | 8,938,821 |
| `-GAP 96 -M 96` | 0 | 0 | 0 | 16,671,389 |

The NQ = 32 rows exist because a reviewer pointed out that the necessity of a
channel there had been inferred from the channel alone (Codex P2, #930). They
are the answer: a **non-channel** remedy clears the stranding at that size too.

**A parenthesised WL excludes stranded bits** and is *not* comparable to a
complete route — `report_wirelength` prints that caveat on the line above the
number, and an earlier revision of this very paragraph read those two rows
against each other anyway (*"at slightly less wire than doing nothing"*). The
NQ = 32 pair points the other way (+0.34 %), which is what makes it plain that
the comparison is not one. The **row counts** are what carry the finding.

That **decomposes the failure into two independent faults, at both sizes**. The
seat lever removes *exactly* the seat — the eight stranded bits and the eight
audit violations, with no doomed seat reported at all (four advisory lines at
NQ = 32 without it, **zero** with) — and leaves the three overlaps untouched,
because those are the fixed-copy residue and a different problem. The channel
clears **both**, which is precisely why it looked like the cause for five
revisions: it is the remedy for the overlaps and it re-seats the doomed segment
incidentally. A non-channel remedy fixing the stranding at two sizes is what
*retires* "the channel is needed" as a claim, rather than merely narrowing it.

**The practical advice does not change**, and saying so plainly matters more
than the diagnosis being new: for a *clean* endpoint the channel alone is still
the cheapest point, and stacking both is redundant and costs 2.5 % more wire
than the channel by itself. What the seat lever buys is a correct account of
which fault is which — and a remedy for the stranding alone, for a methodology
that can live with three overlaps (its wire lands within half a percent of the
default's at either size, and the default's figure is not a complete route). The channel
guidance stays, labelled a workaround rather than a cause.

**Both sides of that trade, priced.** The argument above counts only wire,
which is half a comparison — so here is what the dirty endpoint costs at the
first size measured to come back dirty (NQ = 32 is the second; see above). At
NQ = 16 the default gap strands
**8 bits of 19,424 (0.041 %)** with 3 overlaps; `-GAP 24 -M 24` clears it for
**+7.6 %** wire, and the built-in candidate 96 would cost **+100 %**. So the
case against a default is not merely that 96 is expensive — it is that the
caller's own remedy at the affected size costs *about a thirteenth of what the
default would* (+7.6 % against +100 %), which is exactly why naming it beats
building it in. Phrased as a fraction rather than as "thirteen times cheaper":
the same reversed idiom the NQ = 32 ratio was caught in below, and fixing one
while leaving the other is the partial-fix pattern this PR keeps paying for. A
methodology that would rather pay 2× everywhere than strand 0.04 % of one
size's bits can still do so; it just has to say so, and it now has both
numbers to decide on.

**NQ = 32 is measured now** (Codex P2, #930 — and the shape of that finding is
the recurring one: the seat table above had already *recorded* an NQ = 32
bottom-up run while this paragraph still said NQ = 32 had never been run
bottom-up. My own data refuting my own sentence, a hundred lines apart in one
document, for the second time in this PR.) Measured, `-bottomup` at NQ = 32:

| GAP = M (NQ = 32, `-bottomup`) | 16 | 24 | 96 |
|---|---|---|---|
| result | ✗ 3 ovl / 8 unpl | **clean** | **clean** |
| detailed WL | — | 8,938,821 | 16,671,389 |

So NQ = 32 behaves like NQ = 16: dirty at the default channel, and the
caller's own remedy `-GAP 24 -M 24` routes it for **46 % less wire** than the
conservative 96, which costs **1.87× as much** (8,938,821 against 16,671,389).
Stated in both directions because the first version said *"1.87× less wire"*
(Codex P2, #930) — an invalid construction: a ratio > 1 says how much MORE the
expensive option costs, and "N× less" has no arithmetic meaning. The two
numbers it was derived from were printed in the table directly above it. It
also extends the cost argument against a built-in 96 to a fifth size.

**What it does *not* show is that NQ = 32 NEEDS a channel** — and this
paragraph said it did (Codex P2, #930). Three gaps establish that the default
is dirty and that 24 and 96 are clean; *necessity* would require a
**non-channel** remedy to fail here, and none had been tried at this size.
Inferring a cause from one successful intervention is the error this page
retracts on every other paragraph — committed, this time, in the sentence
written to replace the previous retraction. It is **measured** instead
([below](#the-seat-lever-measured-rather-than-asserted)): the seat lever
clears the stranding at NQ = 32 exactly as it does at NQ = 16, so a channel is
**not** necessary here either.

**NQ = 64 is measured at the default gap and *not* at a named one**, and the
attempt is recorded rather than dropped. At the default it is dirty — 7 ovl /
8 unpl, detailed WL 16,654,709 over 2451 bundles / 76,096 bit-wires, and
**the same segment landing on an M7 seat** — `bundle 2 seg 0`, 7 tracks < 8
bits. Not "the same seat" (Codex P2, #930): a seat is its layer *plus its
span × slide window*, and the windows were never compared across two layouts
of different size, so what the run establishes is the segment, the layer and
the 7 < 8 deficit. That distinction was corrected two commits earlier and
**reintroduced here**, in the very commit that measured NQ = 64 — a
retraction undone by its own follow-up. `64 -bottomup -GAP 24 -M 24` then ran **90 minutes
without finishing** and was killed by the harness timeout (not hung: 2 h 17 m
of CPU at 183 % on that bundle count). Its last reported state was its
*second* healer round at **2 overlaps / 2 unplaced** — six of the eight bits
placed, against 7/8 at the default — so the channel is doing something at
this size, and that is the whole of what the evidence supports. Whether it
reaches clean is **unknown**, and "nearly clean" is not clean: the NQ = 16
curve is non-monotone, so 16 and 32 agreeing at 24 is not even a safe guess
for 64.

The operational rule needs no threshold either way: **if a bottom-up run comes
back dirty, sweep the channel** — and at this size, budget hours rather than
minutes for it.

Worth noting for the seat analysis below: the clean NQ = 32 / GAP 24 run
**still reports the doomed seat** and heals past it, exactly as NQ = 16 /
GAP 24 does. That is independent support for the reading that the *segment*
repeats while the healers' success varies.

The **mechanism** is not established and the earlier claim that a fixed copy
"lands each instance on whatever track phase the channel gives it" was an
assertion, not a measurement — it is a hypothesis consistent with four points
on one design, and nothing here separates it from the alternatives.

That example is the point restated at its own expense: it read `-GAP 48
-M 48` until this revision, because 48 was clean when the line was written.
Three added buses made it fail, so a sentence telling the reader to measure
was itself recommending a configuration that does not route. The recipe is
**sweep**, and a named gap in it is an illustration with a shelf life.

The atomicity rule it needed is kept as history rather than as code: while
the flag *did* supply the pair it had to supply both or neither, since
`-bottomup -GAP 16` left `M` at 24 and gave 4976 × 1576 where that revision's
default geometry was 4720 × 1440 (both dies since re-measured away by the
sizing fixes), while `-bottomup -M 16` leaked the other way — a sweep meant
to vary the channel varied two things. Nothing supplies a knob behind the
caller's back now.

`align_bottom_up` then reports that nested marked parents place children at
incompatible phases, and that is measured rather than tuned away: the track
period is **per axis**, and on this stack the H layers (pitches 18/18/34)
give LCM **306** — what `tpu.tcl` snaps its row pitch to — while the V layers
(18/32/41) give LCM **11808**, larger than most cells here. A mesh tiles on
one axis and can be phase-aligned; a diverse 2-D packing generally cannot. So
the flow declares `check_template_tracks on_mismatch independent`, which is
the honest measurement of what the bottom-up family can do on a design that
is not an array.
