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

Clean at every size measured, top-down, and `-caps`, `-bydepth` and
`-bottomup` are clean too. NQ = 32 used to be **the honest limit** here —
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

NQ = 16 is where a fixed copy still needs one, and there the curve is
**genuinely non-monotone** — measured on the honestly sized design this time,
not as an artefact:

| GAP (NQ = 16) | 16 | 24 | 32 | 48 | 96 |
|---|---|---|---|---|---|
| result | ✗ 3 ovl | ok | ✗ 8 unpl | ok | ok |
| detailed WL | 4,100,702 | 4,603,772 | 4,892,848 | 5,834,639 | 8,354,767 |

That irregularity was reported once and **withdrawn** when its cause turned
out to be the stars. It is back on different evidence: with the faces honest
and the coefficients gone, a fixed copy at NQ = 16 still lands each instance
on whatever track phase the channel gives it, and 32 is *worse* than 24.
Which is the whole argument against a built-in number — the flag cannot pick
one, because the answer is not monotone in the knob it would set. A
bottom-up run at NQ ≥ 16 names the channel itself and measures it:
`soc.tcl 16 -bottomup -GAP 48 -M 48`.

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
