# flow/def — the LEF/DEF entry point, end to end

`chip.buda` is the vehicle for the interface built in phases 1–5 of
[`lefdef_interface_plan.md`](../../docs/internal/lefdef_interface_plan.md):
read a technology and a placed hierarchical design **off disk**, run the
hierarchy-aware routing pipeline on them, and write the advisory artifacts
back out.

```bash
bin/buda flow/def/chip            # from the repo root
```

Every other hier vehicle (`flow/hbundles/*.buda`) *declares* its design in
the script — `add_cell`, `add_inst_to_cell`, `add_bus`. This one declares
nothing. What the script contains is the flow.

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

**Relative paths do not all mean the same thing.** `import_*`,
`emit_guides`, `export_*` resolve against the CWD; `save_bdb` and `source`
resolve against the *script's* directory. Same-looking paths, different
roots.

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
