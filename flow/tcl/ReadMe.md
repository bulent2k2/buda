# `flow/tcl/` — flows that COMPUTE their design

A `.buda` script can only *state* a design. These flows **generate** one from
a few numbers, which is the case the [Tcl front end](../../docs/TCL_FRONT_END.md)
exists for.

| vehicle | what it is |
|---|---|
| [`tpu.tcl`](tpu.tcl) + [`tpu_lib.tcl`](tpu_lib.tcl) | a **TPU-shaped systolic array** — N×N PEs in a mesh, fully parameterized |
| [`soc.tcl`](soc.tcl) + [`soc_lib.tcl`](soc_lib.tcl) | an **SoC-shaped design** — DEEP and DIVERSE: eleven leaf cell types, ragged depth (an ALU four levels down, a UART two), size a dial |
| [`array.tcl`](array.tcl) + [`array_lib.tcl`](array_lib.tcl) | a hierarchical tile array; the front end's original vehicle |
| [`array_save.tcl`](array_save.tcl) / [`array_resume.tcl`](array_resume.tcl) | design ITERATION over one checkpoint — build, pin, reopen, re-plan |
| [`design.tcl`](design.tcl) / [`hdesign.tcl`](hdesign.tcl) | the one-file interactive form: route, then a pin/replan prompt |
| [`corpus/`](corpus/) | the QoR corpus translated to Tcl (`tools/buda2tcl.py`) |

---

# `tpu.tcl` — a systolic array, parameterized

```bash
btcl flow/tcl/tpu.tcl                     # 8x8 = 64 PEs, top-down
btcl flow/tcl/tpu.tcl 16                  # 16x16 = 256 PEs
btcl flow/tcl/tpu.tcl 8 -bottomup         # solve ONE row, copy it to the rest
btcl flow/tcl/tpu.tcl 4 -PW 32 -PIPE 4    # wider psum, deeper tail
btcl flow/tcl/tpu.tcl 32 -dry             # print the size model, build nothing
btcl flow/tcl/tpu.tcl 8 -emit flow/tpu    # write tpu.v/.def/.lef, then stop
```

`-emit` writes the same array as **Verilog + DEF + LEF** for the import path
([`flow/tpu/`](../tpu/ReadMe.md)) — from the same parameters this flow builds
from, so the two cannot drift. Both route to detailed WL **197,376** at N=8,
which is what makes "authored once, elaborated into both" checkable.

## What a PE is

A **PE** is a *processing element* — the single multiply-accumulate cell a
systolic array is tiled from, and the unit `N` counts. In a TPU-style
weight-stationary array each PE holds one weight and, every cycle:

* multiplies the **activation** arriving from its **west** neighbour by that
  weight, and adds the **partial sum** (psum) arriving from its **north**
  neighbour;
* passes the activation on **east** and the new partial sum **south**.

So an N×N grid of them is the matrix-multiply unit, data marches through it
rather than being fetched per operation, and *every wire is to a neighbour* —
which is the property this vehicle exists to put in front of the router. Here
a PE is the `pe_cell` block: `PEW`/`PEH` size it, `PPX`/`PPY` place it, and at
N=32 there are 1024 of them.

The other blocks are the array's edges: `feed_*` (west, activations in),
`wbuf_*` (north, weights in), `acc_*` (south, partial sums out) and the
`pipe_*` tail.

## Why it exists

The corpus had no genuine **mesh**. `flow/chip` is arrayed but assembled from
heterogeneous cells; `flow/ariane133` is real but a CPU core. That mattered
because the whole bottom-up family — `set_bottom_up`, rotation classes,
`align_bottom_up`, `check_template_tracks`, solve-once-copy — keys on **many
congruent instances of one cell**, and nothing here had them in an array.

## Why it is generated rather than imported

The obvious move is to fetch a real ML accelerator netlist; NVDLA is available
through the very channel `flow/ariane133` already uses. **Measured, that does
not work** — a synthesized netlist is *uniquified*, so every replica becomes its
own module and nothing is replicated at all:

| netlist | modules | hier. instances | module types with ≥2 instances |
|---|---|---|---|
| `NV_NVDLA_partition_c.v` | 307 | 306 | **0** |
| `ariane.v` (imported today) | 127 | 125 | **0** |

Raw parameterized RTL is no better: `import_verilog` does not elaborate
`generate`, so a 4-PE array imports as **one** instance with its neighbour
links dropped (`BUDA-1610`) — and says so only in a warning, exit 0.

The honest cost: this exercises the **engine**, not the **reader**. For reader
coverage `flow/ariane133` remains the vehicle, precisely because it is somebody
else's file.

## The shape

```
          wbuf_0  wbuf_1  ...  wbuf_{N-1}      <- north edge: weights in
            |       |            |
 feed_0 -> pe_0_0 -pe_0_1- ... -pe_0_{N-1}     } row_cell instance 0
 feed_1 -> pe_1_0 -pe_1_1- ... -pe_1_{N-1}     } row_cell instance 1
   ...        |       |            |
            acc_0   acc_1  ...  acc_{N-1}      <- south edge: psums out
              |       |            |
            pipe stages (PIPE deep, PW wide)   <- the deep tail
```

Every link is **nearest-neighbour**, which is what makes it systolic and what
makes congruence real rather than nominal. The dataflow splits along exactly
the seam the hier flow cares about:

* **west→east activations** stay inside a row → **cell-local**, `cell:row_cell`
* **north→south psums and weights** cross row instances → **cross-level**

Measured at N=4: 12 cell-local, 12 cross-level, 20 cross-block bundles.

## Parameters

All settable as `-<NAME> <value>`; a bare leading integer is `N`. An unknown
knob is an **error**, because a typo in a sweep that runs for an hour must not
report on a design nobody asked for.

| knob | default | what it is |
|---|---|---|
| `N` | 8 | array dimension — N×N PEs in N rows |
| `PEW` / `PEH` | auto | PE block size |
| `PPX` / `PPY` | auto | PE pitch (placement) |
| `ROWM` / `ROWGAP` | 12 / auto | row-cell margin, gap between rows |
| `EDGEW` / `EDGEH` / `EDGEGAP` | auto / auto / 48 | edge-block size and standoff |
| `X0` / `Y0` | 60 / 120 | array origin |
| `AW` / `PW` / `WW` | 8 / 24 / 8 | activation, psum, weight bus widths |
| `PIPE` / `PIPEGAP` | 2 / 48 | depth of the tail pipeline |
| `BITPITCH` / `PEPAD` / `CHAN` | 4 / 24 / 48 | auto-sizing inputs |
| `YPERIOD` | 306 | the stack's y track period (see below) |

### Sizes are DERIVED, and that was the expensive lesson

A bus has to **land on a face**. A block narrower than its own bus is
unroutable however much channel it is given — measured: `PEW 60` against a
24-bit psum on a 4.0 bit pitch (96 units of face needed) stranded **672 of 832
bits**, and *widening the channel made it worse* (1272), because the channel
was never the binding constraint. So `PEW`/`PEH`/`PPX`/`EDGE*` auto-size from
the bus widths and `BITPITCH`. Override any of them; a face too narrow for its
bus is now reported **at declaration**, where the wrong number is still on
screen.

### `-bottomup` changes the GEOMETRY, not just the flow

Congruent instances must see **identical tracks**, so the row pitch has to be a
whole number of track periods. `align_bottom_up` can only nudge, and with a
compact gap every nudge collided with the next row and was reverted — 7 of 8
instances left misaligned, and `check_template_tracks` then refused DNUTS,
which is the right answer to a design that is not congruent. So `-bottomup`
snaps `ROWGAP` onto `YPERIOD`.

`YPERIOD` is the engine's own y-period for this stack (it prints it on the
`[Align]` line) rather than something recomputed here — it is **not** simply
the LCM of the H pitches (306, where `LCM(18,32)` is 288), and a wrong guess
would be silent. Nothing rests on it being right, though:
`check_template_tracks` measures the real tracks and refuses loudly.

## Measured

Every configuration ends clean — 0 overlaps, 0 unplaced, 0 audit violations.

| N | PEs | bundles | bit-wires | detailed WL | wall |
|---|---|---|---|---|---|
| 4 | 16 | 44 | 832 | 55,680 | 0.35s |
| 8 | 64 | 152 | 2,944 | 197,376 | 0.93s |
| 16 | 256 | 560 | 11,008 | 738,816 | 4.2s |
| 32 | 1024 | 2,144 | 42,496 | 8,641,024 | 29s |

At N=32 the bottom-up path solves **248 reference bits once and copies 7,688 to
961 sibling instances**, with 34,560 solved around them.

**Solve-once-copy costs nothing here.** At *equal geometry* top-down and
bottom-up are byte-identical (N=8: 550,528 both; N=16: 2,174,208 both) — which
is the property worth pinning, since a template copy that quietly routed worse
than the full solve would still end "clean". Comparing the **defaults** instead
shows a ~2.9× gap that is **entirely die size**, not the algorithm: `-bottomup`
snaps the row pitch to 306 against a compact 128. To compare fairly, pass the
same `-ROWGAP` to both.

Pinned by [`test_tcl_tpu_flow.py`](../../test/tests/test_tcl_tpu_flow.py).

# `soc.tcl` — an SoC, deep and diverse

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
`D0: 8, D1: 16, D2: 20, D3: 48`.

## Measured, this container

| NQ | clusters | leaves | bundles | bit-wires | die | wall | endpoint |
|---|---|---|---|---|---|---|---|
| 1 | 2 | 37 | 54 | 1 536 | 2640 × 2016 | 1 s | clean |
| 2 | 4 | 63 | 92 | 2 568 | 5232 × 2016 | 1 s | clean |
| 4 | 8 | 115 | 168 | 4 800 | 5232 × 3200 | 3 s | clean |
| 8 | 16 | 219 | 320 | 9 064 | 7824 × 4384 | 6 s | clean |
| 16 | 32 | 427 | 624 | 17 536 | 10416 × 5568 | 15 s | clean |
| 32 | 64 | 843 | 1232 | 34 480 | 15600 × 7936 | 40 s | clean |
| 64 | 128 | 1675 | 2448 | 68 400 | 20784 × 10304 | 139 s | clean |

Clean at every size measured, top-down, and `-caps`, `-bydepth` and
`-bottomup` are clean too. NQ = 32 used to be **the honest limit** here —
13 bits unplaced after 293 s — and it is neither the limit nor slow any
more: removing three stars (below) took it to clean in 40 s and put NQ = 64
within reach. Every row on this table has now moved twice, once when a
second `heal_if_dirty` round landed and once when the stars went, and both
times because something re-ran it.

## Every endpoint, every bit: the face rule read twice

The face rule — *a leaf's size is derived from the bits that land on its
faces* — has to hold for **every endpoint of a bus**, and it has to be read
on the **sum at each pin**. This vehicle broke both readings in turn, and
the second one was hiding everything else on this page.

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
never found them — and why the guard
(`test_no_cell_is_sized_from_a_knob_no_bus_brings_it`) perturbs each knob
and diffs the sizes rather than parsing the expressions, with **one regime
per knob**, since a term is invisible in any regime where its knob is not
what binds.

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

| GAP | NQ = 8 | NQ = 16 |
|---|---|---|
| 16 | clean, WL 2,160,182 | clean, WL 4,416,873 |
| 48 | clean, WL 2,932,536 | clean, WL 6,022,953 |
| 96 | clean, WL 4,147,638 | clean, WL 8,491,122 |
| 144 | clean, WL 5,362,445 | clean, WL 10,915,654 |

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

| GAP (NQ = 4, DW = 128) | 16 | 32 | 64 | 96 |
|---|---|---|---|---|
| detailed WL | 11,040,362 | 12,129,097 | 12,341,964 | 13,936,468 |
| endpoint | clean | clean | clean | clean |

`-DW` alone: `IW` sizes `dec_cell` and every cell enclosing it, so setting it
too would measure a different design from the one recorded here. So `GAP`
and `M` are plain constants across the whole NQ dial **and** across a 4×
datapath — a stronger claim than the one it replaced, and one reached by
removing faces rather than by tuning a gap.

The last part of the lesson is that these numbers get re-run, and have now
moved **twice**. An earlier sweep read as *non-monotone* (clean at GAP
40/48/80, stranded at the rest) and was presented here as the reason no
derivation could exist; a second healer round changed the answer at every
point; then the star fix turned a whole failing table clean. A recorded
measurement nothing re-runs decays into a claim, so
`test_a_wider_channel_is_not_the_lever_a_wider_bus_needs` runs the cheap end
of both directions on every test run.

## `-bottomup`: the channel it used to need was a face

`-bottomup` used to widen the channel behind the caller's back (`GAP 24
M 24`), and the sweep that justified it read **non-monotone** at NQ = 4 —
16 ✗, 24 ok, 32 ok, 48 ok, **64 ✗**, 96 ok — which was written up here as
evidence that a *fixed* copy turns the channel into a phase lottery.

Both halves were the star faces. With those sized from the bits that land on
them, `-bottomup` needs **no channel at all** through NQ = 4, and where it
does need one the curve is plain monotone. Measured at NQ = 8:

| GAP | 16 | 24 | 32 | 48 | 64 | 96 |
|---|---|---|---|---|---|---|
| result | ✗ 1 ovl / 6 unpl | ✗ 0 / 5 | ✗ 0 / 4 | ok | ok | ok |
| detailed WL | 2,504,030 | 2,475,632 | 2,704,106 | 3,177,507 | 3,717,129 | 4,477,478 |

So the widening is gone: at NQ ≤ 4 it bought nothing and cost wire (NQ = 4:
1,237,977 at the default against 1,329,561 at 24), and at NQ = 8 it was not
enough anyway. Nor is there a number that would have been: NQ = 16 still
strands one bit at 48 and wants **96**. What a fixed copy needs is a
function of the size, which is the caller's to measure — a bottom-up run
above NQ = 4 names the channel itself, `soc.tcl 8 -bottomup -GAP 48 -M 48`.

The atomicity rule it needed is kept as history rather than as code: while
the flag *did* supply the pair it had to supply both or neither, since
`-bottomup -GAP 16` left `M` at 24 and gave 4976 × 1576 where that revision's
default geometry was 4720 × 1440, while `-bottomup -M 16` leaked the other
way — a sweep meant to vary the channel varied two things. Nothing supplies a knob behind the
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
