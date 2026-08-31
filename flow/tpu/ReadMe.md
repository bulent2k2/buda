# `flow/tpu/` — the systolic array through the IMPORT path

```bash
bin/buda flow/tpu/tpu.buda                    # route the imported array
btcl flow/tcl/tpu.tcl 8 -emit flow/tpu        # regenerate the three inputs
```

[`flow/tcl/tpu.tcl`](../tcl/tpu.tcl) builds this same array straight into the
BDB, which exercises the **engine**. This imports it from Verilog + DEF + LEF,
which exercises the **reader** — and it is the only vehicle here that hands
the reader an **array**.

## Why that is worth a vehicle

Every real netlist available is **uniquified**, so no imported design in this
repo has ever had a repeated cell:

| netlist | modules | hier. instances | module types with ≥2 instances |
|---|---|---|---|
| `NV_NVDLA_partition_c.v` | 307 | 306 | **0** |
| `ariane.v` | 127 | 125 | **0** |
| **`tpu.v` (here)** | 6 | **104** | **2** |

`pe_cell` is instantiated 8 times inside `row_cell`, and `row_cell` 8 times at
the top — so the reader, the DEF+Verilog merge and the bundler's cell-local
templating all meet an array for the first time. The import yields **56
`cell:row_cell` template bundles**, 24 cross-level and 72 cross-block.

```
tpu_top                                  depth 0
  row_0 .. row_7      row_cell           one template, EIGHT times
    pe_0 .. pe_7      pe_cell    LEAF    one template, EIGHT times each
  feed_*  wbuf_*  acc_*  pipe_*   LEAF   the array's edges and tail
```

## The inputs are generated, from the same source as the Tcl flow

`tpu.v`, `tpu.def` and `tpu.lef` are written by `tpu_vehicle::emit_*`, from
the **same** parameter set the Tcl flow builds from. `flow/rv` established the
rule — author once, elaborate into both — precisely because a hand-kept
netlist and floorplan drift into silently dropped connections.

The check that this actually holds is that **both paths route to the same
number**: detailed WL **197,376** either way, 152 bundles, 2,944 bit-wires.
A test pins it, so an edit to the geometry that forgets to regenerate is
caught as a WL difference rather than discovered later.

Units: the DEF declares `DATABASE MICRONS 1000` and scales by 1000 while LEF
`SIZE` is in microns, so at the default import scale one layout unit is one
micron and the imported geometry equals the Tcl numbers exactly.

## One expected warning

256 nets are reported as carried by no bundle. That is the **design**, not a
defect: they are `row_0/p_in_*` (the first row has no partial sum arriving
from the north) and `row_7/w_out_*` (the last row passes no weight on). Every
row is the same `row_cell` module, so those ports must be declared even where
the edge instance leaves them unconnected — the Tcl path has no such nets
because it wires pins directly. That is the one place the two representations
differ, and it is a property of expressing an array in Verilog at all.

## Measured

`0 overlaps / 0 unplaced / 0 violations`, abstract WL 9,312, detailed WL
197,376, ~0.9s. In the QoR corpus as `flow/tpu/tpu.buda`.
