# `flow/tcl/` — flows that COMPUTE their design

A `.buda` script can only *state* a design. These flows **generate** one from
a few numbers, which is the case the [Tcl front end](../../docs/TCL_FRONT_END.md)
exists for.

| vehicle | what it is |
|---|---|
| [`tpu.tcl`](tpu.tcl) + [`tpu_lib.tcl`](tpu_lib.tcl) | a **TPU-shaped systolic array** — N×N PEs in a mesh, fully parameterized |
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
```

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
