# flow/def — the LEF/DEF entry point, end to end

`chip.buda` is the vehicle for the interface built in phases 1–5 of
[`lefdef_interface_plan.md`](../../docs/internal/lefdef_interface_plan.md):
read a technology and a placed hierarchical design **off disk**, run the
hierarchy-aware routing pipeline on them, and write the advisory artifacts
back out.

```bash
bin/buda flow/def/chip            # from the repo root, or…
buda ~/work/buda/flow/def/chip    # …any script path from anywhere (bin/ on PATH)
```

Either way the flow reads `flow/def/` and writes `flow/def/out/`: every
relative path **inside the script** resolves against the script's own
directory, so where you start the run stops mattering once the script is
found. (Locating the script itself is the shell's job — that argument is
still relative to your CWD, like any command-line path.)

Every other hier vehicle (`flow/hbundles/*.buda`) *declares* its design in
the script — `add_cell`, `add_inst_to_cell`, `add_bus`. This one declares
nothing. What the script contains is the flow.

It is deliberately the **smallest** design that does so. Its sibling
[`flow/rv/`](../rv/) is the other end of the scale — a dual-core RV32-shaped
SoC, 1230 nets, five levels, a 32-bit datapath — and exists because a small
vehicle finds the faults that are about *structure* while a large one finds
the faults that only appear once a quantity stops being one.

## The files

| File | What it carries |
|---|---|
| `chip.lef` | 6 routing layers (direction / pitch / width), 5 cut layers, one `LEAF` macro with pins, power pins and an `OBS` |
| `chip.def` | die, `TRACKS`, `GCELLGRID`, 8 placed leaf `COMPONENTS`, 8 `PINS`, `BLOCKAGES`, `SPECIALNETS`, 36 `NETS` |
| `chip.v` | the module hierarchy the DEF's instance names refer to |
| `gen_chip.py` | regenerates all three from one description |

The three inputs are **committed**; the flow never runs the generator. Run
`python3 flow/def/gen_chip.py` to rewrite them after changing the design —
they are generated from one source precisely so a hand-edited 36-net DEF
cannot drift out of agreement with its netlist, which fails in the most
confusing way available (silently dropped connections).

## Why there is a Verilog file

Because **a DEF is flat.** `COMPONENTS` lists leaf instances and nothing
else, which is why `import_def_lef` writes every row at `depth=0`. The
hierarchy lives in the instance *names* — `u_q0/bl/lo` — and is recovered by
elaborating the netlist over the placement, keeping every DEF coordinate and
adding the parent/depth the DEF cannot carry. That is the "DEF + Verilog
merge" the [BDB reference](../../docs/BDB_REFERENCE.md) describes.

So "a hierarchical DEF" is really a DEF plus the netlist that gives its
names meaning. Neither file states where a hierarchical *instance* is,
because neither has a row for one — see `derive_container_bboxes` below.

## The design

```
chip                                    (top module)
  u_q0, u_q1        quad     depth 0
    bl, br          blk      depth 1
      lo, hi        LEAF     depth 2    <- the only DEF COMPONENTS
```

One 4-bit bus per level, so every stage of the hier pipeline has something
of its own to do:

| Bus | Level | From → to |
|---|---|---|
| D2 | inside every `blk` (4 instances) | `lo` → `hi` |
| D1 | inside every `quad` (2 instances) | `bl/hi` → `br/lo` |
| D0 | top | `u_q0/br/hi` → `u_q1/bl/lo` |
| ports | top | `din*` → `u_q0/bl/lo`, `u_q1/br/hi` → `dout*` |

Result: 15 bundles (D0 9, D1 2, D2 4), 18 placed bus segments, 48 bit-wires,
**0 overlaps, 0 unplaced bits, `check_design` clean at both stages.**

## Units

The flow declares `set_import_scale dbu`, so one layout unit is one DEF
database unit and the import is **exact** — no rounding of a 0.2 µm pitch
onto an integer grid. Having declared a scale, it also gets the sharp half
of the unit guard: pitches are checked against real metal (0.005–500 µm),
not only against the scale-free tracks-across ratio.

The documented consequence applies: **script-declared distances are in
layout units too**, so `corner_margin dx 2000` is 2 µm, not 2000 µm.

## Two things worth knowing before you copy this flow

**`derive_container_bboxes` is not optional here.** Without it, busterm
derivation skips every unplaced component and all 22 busterms collapse to
depth 0 — the hierarchy sits in the database while the routing interface is
flat, and nothing says so. It gives each container the extent of its placed
children, and it is a separate command rather than a step inside
`import_verilog` because it *invents* geometry the input never stated.

**Relative paths all resolve against the script's directory.** Imports,
exports, `open_bdb`, `save_bdb` and `source` share one rule. (It used to be
two — imports/exports against the CWD — and this script ran only from the
repo root; see `docs/internal/opens_interchange.md` item 4, resolved.)

## Exports

| Artifact | |
|---|---|
| `out/chip_guides.json` / `.csv` | the corridor manifest — per bundle, its nets, layer and reserved rectangle. The primary artifact. |
| `out/chip_guides.tcl` | a worked `create_route_guide` script |
| `out/chip_advisory.def` | real keepouts as hard blockages + `PLACEMENT + PARTIAL` density caps over the corridors. Re-reads cleanly (33/33 blockages, nothing unmodelled). |
| `out/chip.gds` | the placed geometry |
| `out/chip.bdb.sql` | the whole database, diffable |

**GDS round-trip limits**, stated so nobody rediscovers them: the synthetic
`__PORT__` boundary cells and the container cells the netlist merge created
carry no size, so their structures come back empty and the 8 port labels
land on nothing. The routing geometry round-trips; the scaffolding the merge
invented does not. Note also that the metal map starts at GDS layer **11**:
`export_gds` writes cell outlines on layer 10 by default, and a re-import
reads every shape on a mapped routing pair as a wire, so mapping a metal
onto 10 hands the outlines back as routing.

## Known limits

The two notes above, and everything else this vehicle exposed that is still
open, are owned by
[`docs/internal/opens_interchange.md`](../../docs/internal/opens_interchange.md).

Two of its items were found by building this design and are now **fixed**,
which is the argument for having the vehicle:

* **Item 1** — a hard macro instance dropped from the hierarchy because its
  name was not backslash-escaped. The LEF's `MACRO … CLASS` now decides, so
  the instance name is not consulted; whatever the reader does skip is
  counted and its cell kinds named (`BUDA-1608`).
* **Item 2, both halves** — a vector port map collapsing to one net, and a
  vector PORT being one pin. This design was written in scalars throughout
  *because* of them. It is a vector design now, end to end: LEF pins
  `A[0]`..`A[3]` (the LEF already declared `BUSBITCHARS "[]"`), DEF nets and
  die `PINS` named `din[0]`, and a netlist that says
  `input [3:0] A;` / `LEAF lo (.A(I), .Z(w));` like a netlist does.

  It routes **identically** to both earlier spellings — 15 bundles, 273,800
  abstract and 870,800 detailed WL, 60 bit-wires, `check_design` clean. That
  is the point: the design never changed, only how it is written. Read by the
  pre-fix reader the same netlist routes **18 bit-wires instead of 60**, with
  every `check_design` still reporting success.
