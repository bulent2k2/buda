# flow/rv — the LEF/DEF entry point at scale

`soc.buda` is the second vehicle for the interface built in phases 1–5 of
[`lefdef_interface_plan.md`](../../docs/internal/lefdef_interface_plan.md).
[`flow/def/`](../def/) is deliberately the **smallest** design that
exercises the path — 4-bit buses, one bus per level, eight leaves. This is
the other end: a dual-core RV32-shaped SoC, five levels deep, 32-bit
datapath, 1230 nets.

```bash
bin/buda flow/rv/soc              # from the repo root, or…
buda ~/work/buda/flow/rv/soc      # …any script path from anywhere (bin/ on PATH)
```

Both read `flow/rv/` and write `flow/rv/out/` — relative paths inside a
script resolve against the *script's* directory, so where you start the run
stops mattering.

## The files

| File | What it carries |
|---|---|
| `soc.lef` | 7 routing layers (M1–M7, real pitches 0.19–0.8 µm), 6 cut layers, 10 `MACRO`s with per-bit pins, power pins and `OBS` |
| `soc.def` | die 1012.0 × 867.2 µm, `TRACKS`, 44 placed `COMPONENTS`, 64 `PINS`, `BLOCKAGES`, `SPECIALNETS`, 1230 `NETS` |
| `soc.v` | the module hierarchy the DEF's instance names refer to, with blackbox stubs for the macros |
| `gen_soc.py` | regenerates all three from one description |

The three inputs are **committed**; the flow never runs the generator. Run
`python3 flow/rv/gen_soc.py` to rewrite them after changing the design.

## One description, elaborated

`flow/def/gen_chip.py` writes its netlist and its DEF from two hand-kept
lists that happen to agree. At 36 nets that is fine. At 1230 it is not, and
the failure it invites is the worst one available: a DEF whose instance or
pin names no longer match the netlist merges into **silently dropped
connections** — every command succeeds and the design is missing wires.

So here the design is authored **once**, as modules with ports, wires and
child instances, and a small elaborator walks it to produce the DEF's
components and nets. The two files cannot disagree because only one of them
is written by hand.

That elaborator is also an independent second opinion on BUDA's own reader.
It applies the same three Verilog rules — a connection is `min(formal,
actual)` bits aligned at the low end, a port's pins carry its *declared*
indices, and the formal's remaining upper bits connect to nothing — and if
the two implementations ever resolve a part-select differently, the DEF's
net count and the merged database's disagree and `import_def_lef` says so
(`imported N of M`). That check runs on every flow invocation.

## The design

```
soc                                                      (top module)
  u_cl                        cluster    depth 0
    u_c0, u_c1                core       depth 1    <- one template, twice
      u_ifu                   ifu        depth 2
        u_pc REG32 / u_inc ADD32 / u_nsel MUX32
        u_iab ALN32 / u_pcb REG32        depth 3    LEAF
      u_idu                   idu        depth 2
        u_dec DEC32 / u_imm IMM32        depth 3    LEAF
      u_exu                   exu        depth 2
        u_bsel MUX32 / u_bru BRU32       depth 3    LEAF
        u_alu                 alu        depth 3
          u_add ADD32 / u_log LOG32 / u_ysel MUX32
                                         depth 4    LEAF
      u_lsu                   lsu        depth 2
        u_agu ADD32 / u_dab ALN32 / u_algn ALN32
        u_st REG32 / u_swe BRU32         depth 3    LEAF
      u_wbsel MUX32                      depth 2    LEAF
      u_rf    RF32                       depth 2    LEAF (CLASS BLOCK)
    u_ixb, u_dxb              xbar       depth 1
      u_amux MUX32 / u_dmux MUX32        depth 2    LEAF
  u_imem, u_dmem  SRAM32                 depth 0    LEAF (CLASS BLOCK)
```

44 leaves at **four different depths** (0, 2, 3 and 4) and 15 containers, so
busterm derivation, the floorplan projection and the bundler all have work
to do at every level rather than one bus per level.

It is a **wiring** vehicle. The structure, port widths and connections are
those of a small core; the arithmetic is not — `u_inc` adds `pc` to itself
where a real fetch unit would add 4. The design is routed, never simulated.
Nothing downstream cares what the bits mean; everything downstream cares how
many there are and where they go.

## What it carries that `flow/def` does not

| | |
|---|---|
| **A 32-bit datapath** | a bundle is 32 nets, not 4 — wide enough that a layer's real track supply is a constraint rather than a rounding error |
| **Non-zero-based part-selects** | `pc[11:2]` onto a 10-bit memory address is how a word-addressed memory is actually wired, and it is the case where port bit *k* is net bit *k+2* |
| **Bit- and part-selects off one bundle** | `ctrl[0]` picks register-vs-immediate, `ctrl[3:1]` is the ALU op, `ctrl[6:4]` the branch condition — how a decoder's output is used |
| **Ports wider than their actual** | a 10-bit address through a 32-bit mux: 10 bits connect and port bits 10–31 must stay unconnected rather than inventing `a[10]` |
| **A template instantiated twice** | `core`, which is what gives the hier pipeline something to solve once |
| **Two hard macros** | `RF32` and `SRAM32`, `CLASS BLOCK` in the LEF — which is what carries them through the DEF+Verilog merge whatever their instance names look like (interchange item 1) |

Measured on the import: **10 bit-selects, 16 part-selects, 200 whole-vector
port connections** across 59 elaborated instances.

## Result

```
127 hbundles (D0: 70, D1: 9, D2: 30, D3: 14, D4: 4)
163 bus segments placed — 0 track overlaps, 0 interval violations
1822 bit-wires — 0 unplaced
check_design dnuts: Success: no violations found
abstract WL 31,234,654   detailed WL 409,819,470   (layout units = DBU)
```

~14 s end to end, of which `refine_selection` is 10 s.

**The healers do real work here, and that is the point of the size.** The
first `check_design dnuts`, before any healer runs, reports **66 violations
across 4 bundles**; `negotiate_congestion` → `ripup_reroute` →
`refine_selection` take it to zero. `flow/def` is clean on the first pass,
so it never exercises that half of the flow.

## The mid-flow warnings are honest

The log ends `0 error line(s), 24 warning line(s)` with a clean endpoint.
They are worth reading rather than tuning away:

* **`Bundle 38 / 74: no overflow-free candidate`** — the 32-bit instruction
  bus fanning from `u_imem` to four decode leaves in two cores, and core 0's
  store-data bus reaching the data crossbar. There is genuinely no
  overflow-free assignment at plan time; the planner says so and commits at
  `ALLOW_OVERFLOW`, and the measured healers then move it.
* **`Layer 6 has insufficient signal tracks (25) for bus width 32`** — M6 is
  0.8 µm pitch, so a 20 µm window holds 25 tracks and cannot host 32 bits.
  This is the supply-doomed-seat shape (issue #536) reported by the exact
  DNUTS admission arithmetic, and it is what the re-seat heal exists for.
* **`insufficient signal tracks (0) … for bus width 1`** in a 0.5 µm
  interval — a die-port pad is 0.5 µm across, narrower than one M6 pitch, so
  no track can land in it. The bit routes on another layer.
* **`98 bit(s) removed — final span crosses a keepout`** — counted unplaced
  rather than kept, then healed. An illegal wire is never kept silently.
* **`BUDA-1603 … BLOCKAGES.PLACEMENT:1`** — a `PLACEMENT` blockage is where
  *cells* may not go; it names no layer, so importing it as a routing
  keepout would forbid routing the DEF left routable. It is recorded in the
  unmodelled census instead.

## Units

`set_import_scale dbu`, so one layout unit is one DEF database unit and the
import is **exact** — a 0.19 µm pitch is not rounded onto an integer grid.
Having declared a scale, the flow also gets the sharp half of the unit guard
(`set_unit_check on`): pitches are checked against real metal, not only
against the scale-free tracks-across ratio.

The documented consequence applies: **script-declared distances are in
layout units too**, so `corner_margin dx 3000` is 3 µm, not 3000 µm.

The `um` suffix (`corner_margin dx 3um`) exists to write that intent down —
and this flow deliberately does **not** use it, which is worth knowing
because the reason is a real boundary between the two features. `dbu`
resolves its factor from the DEF's own `UNITS DISTANCE MICRONS`, so it is
not known until `import_def_lef` runs; `corner_margin` here is declared
*before* the import, and a `um` distance in that window would have to guess.
It does not guess — it refuses, naming the two ways out (move the line after
the import, or declare a numeric scale). Either would work; bare layout
units keep the setup block in one place, above the import that gives them
their size.

## Exports

| Artifact | |
|---|---|
| `out/soc_guides.json` / `.csv` | the corridor manifest — 127 bundles, 160 corridors. The primary artifact. |
| `out/soc_advisory.def` | 48 hard keep-clear blockages + 160 `PLACEMENT + PARTIAL` density caps over the corridors |
| `out/soc.bdb.sql` | the whole database, diffable |

## What building this exposed

**A bundle driven by a die port lost every candidate.**
`import_def_lef` names a boundary component `PIN/<port>` and puts it at
**depth 0** — a name containing a slash that is not a hierarchy separator.
Two places in the hier pipeline counted slashes to get an endpoint's depth,
so they read those components as depth 1 and built the routing frame at a
depth holding *neither* endpoint. The 32 `boot` bundles generated **zero
candidates** — 32 nets with no wire — while `check_design` still reported
"Success: no violations found", because a bundle with no candidates has no
segments to find a violation in. Fixed by asking the component
(`_endpoint_depth` in `src/buda_session/hier.py`); `flow/def` never showed it
because its ports connect within one level.

**A 32-bit die-port bus routed as 32 one-net bundles** — 64 of the 70 D0
bundles a single port bit. Each `PIN/<port>[k]` is its own boundary
component, so the 32 wires from one mux to 32 adjacent pads had 32
different endpoint sets: correct by the rule and wrong about the design.

Measuring it is what showed the fault was narrower and sharper than "a port
bus does not bundle". CONVERGENT **already** merged `boot` (32 pads into one
memory is a fan-in, exactly its case); only `dbg` — the same bus going the
other way — stayed split. The lattice was **asymmetric**: it had a fan-in
relation and no fan-out one. Fixed by adding the mirror, `DIVERGENT`, and
this design is its demo:

```bash
bin/buda flow/rv/soc_divergent    # soc.buda with one token changed
```

|  | `soc.buda` | `soc_divergent.buda` |
|---|---|---|
| bundles | 127 | 78 |
| `dbg` | 32 × 1 net | 1 × 32 nets |
| abstract WL | 31,234,654 | 19,104,008 (−38.8%) |
| detailed WL | 409,819,470 | 430,511,600 (+5.0%) |

Both clean, 0 unplaced. Two scripts rather than one changed script because
both numbers are real: the abstract win is a fan-out tree sharing a trunk
where 32 bundles each reserved their own, the detailed cost is what that
tree pays to reach scattered leaves. Which one a design wants is the
design's call. See
[`opens_interchange.md`](../../docs/internal/opens_interchange.md) item 11.

## Known limits

Everything this vehicle exposed that is still open is owned by
[`docs/internal/opens_interchange.md`](../../docs/internal/opens_interchange.md).

## Guard

`test/tests/test_rv_hier_flow.py` — the width rules read back off the merged
database (fast tier), and the whole flow plus generator reproducibility
(`mid`). Like `flow/def/`, this vehicle is guarded by its tests rather than
by the QoR corpus: what it is for is the *interface*, and a QoR row would
measure the router.
