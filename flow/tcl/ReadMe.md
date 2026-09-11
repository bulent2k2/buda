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

* **Many different cell types, most appearing once.** A mesh measures
  solve-once-copy; it says nothing about a planner meeting a new floorplan at
  every block. Here eleven leaf cell types, of which `memctl_cell`,
  `bridge_cell`, `xbar_cell` and `tag_cell` appear once per subsystem while
  `sram_cell` and `cluster_cell` repeat — both in one design, which is what a
  real SoC is.
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
`D0: 8, D1: 8, D2: 20, D3: 16`.

## Measured, this container

| NQ | clusters | leaves | bundles | die | wall | endpoint |
|---|---|---|---|---|---|---|
| 1 | 2 | 37 | 30 | 2384 × 1440 | 0.5 s | clean |
| 2 | 4 | 63 | 52 | 4720 × 1440 | 1.1 s | clean |
| 4 | 8 | 115 | 96 | 4720 × 2496 | 2.0 s | clean |
| 8 | 16 | 219 | 184 | 7056 × 3552 | 5.8 s | clean |
| 16 | 32 | 427 | 360 | 9392 × 4608 | 23.8 s | clean |
| 32 | 64 | 843 | 712 | 14064 × 6720 | 61 s | **24 overlaps, 96 unplaced** |

Clean to NQ = 16 top-down, and `-bottomup`, `-caps` and `-bydepth` are clean
at every size measured. NQ = 32 is the honest limit.

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
its place a full-width channel is **strictly worse**:

| GAP | NQ = 8 | NQ = 16 |
|---|---|---|
| 16 | clean, WL 2,076,658 | clean, WL 4,326,186 |
| 48 | clean, WL 2,799,211 | clean, WL 5,806,145 |
| 96 | clean, WL 3,897,596 | **32 unplaced** |
| 144 | clean, WL 4,960,422 | **32 unplaced** |

— which is `tpu.tcl`'s lesson in the direction it recorded it: *widening the
channel made it worse, the channel never having been the binding
constraint.* The die it inflates makes every wire longer while the congestion
sits elsewhere.

The obvious repair is a **fraction** of a bus rather than a whole one, and
that is where it stops being a rule at all. Swept at DW = 128, NQ = 4:

| GAP | 16 | 24 | 32 | 40 | 48 | 56 | 64 | 80 | 96 |
|---|---|---|---|---|---|---|---|---|---|
| result | ✗ | ✗ | ✗ | ok | ok | ✗ | ✗ | ok | ✗ |

Non-monotone, so there is no width a derivation could target: a gap shifts
every block, and with it which blocks land on which track phase, so the
channel knob **perturbs** the route rather than feeding it. A vehicle that
derived this would be asserting a law its own numbers deny. `GAP` and `M` are
therefore plain constants that work across the whole NQ dial at the default
bus widths, and a design moving `DW` far from the default sweeps `-GAP`
rather than trusting an arithmetic.

## `-bottomup` changes the geometry, and exercises the `independent` path

Like `tpu.tcl`'s, this `-bottomup` is not only a flow change. The copied
cell-local routing is a fixed copy at every instance, so what it cannot clear
is an **overlap** rather than an open, and at the top-down channel the design
leaves two standing after both healer rounds. Measured at NQ = 4 the cheapest
channel that clears it is 24 (+7.8 % WL), and the flag supplies `GAP` and `M`
**atomically** — naming *either* suppresses the whole pair, because a caller
who names one is doing the geometry by hand. Filling each half in
independently made the flag's own contribution partial: `-bottomup -GAP 16`
left `M` at 24 and gave a 4976 × 1576 die where the default geometry is
4720 × 1440, so a sweep meant to vary the channel alone varied two things
(and `-bottomup -M 16` leaked the other way, 4840 × 1472).

`align_bottom_up` then reports that nested marked parents place children at
incompatible phases, and that is measured rather than tuned away: the track
period is **per axis**, and on this stack the H layers (pitches 18/18/34)
give LCM **306** — what `tpu.tcl` snaps its row pitch to — while the V layers
(18/32/41) give LCM **11808**, larger than most cells here. A mesh tiles on
one axis and can be phase-aligned; a diverse 2-D packing generally cannot. So
the flow declares `check_template_tracks on_mismatch independent`, which is
the honest measurement of what the bottom-up family can do on a design that
is not an array.
