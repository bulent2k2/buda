# flow/ariane133 — a real 45nm design through the hier pipeline

The third LEF/DEF/Verilog vehicle, and the first that is **somebody else's
design**. `flow/def/` and `flow/rv/` are authored here, which is what makes
them good at finding faults of *structure* — and blind to everything that
only appears in a file a human did not write.

```bash
python3 flow/ariane133/fetch.py          # ~12 MB, checksum-pinned
bin/buda flow/ariane133/ariane133.buda
```

Or as **one command**, which fetches (or verifies) and then routes:

```bash
bin/btcl flow/ariane133/ariane133.tcl         # inputs + the healer flow
bin/btcl flow/ariane133/ariane133.tcl base    # inputs + the plain flow
```

The two-step recipe above is the one to know; the Tcl driver exists because
this vehicle is the only one in the repo whose inputs are not in the repo, so
running step two alone is a mistake a fresh clone makes exactly once. It is a
driver and not a second copy of the flow — it hands the engine the **same**
`.buda` file — and it ends by asking the finished session for its numbers
(`buda::query`), so the pair is gateable: non-zero exit on a dirty endpoint or
a stage that never ran. `-nofetch` verifies without downloading.

Both `.buda` flows now **declare their inputs** on the first line:

```
require_file ariane.v fakeram45_256x16.lef hint Fetch them first: …
```

so running one without the fetch stops immediately with the remedy
(`BUDA-1905`) instead of partway through the setup. The importer's own
complaint is about a path it could not open; where that file comes from is
this flow's knowledge, and that is the half worth printing.

| | |
|---|---|
| design | ariane133 — a RISC-V core with 133 SRAM macros, 45nm |
| netlist | gate-level, **127 modules, 5 hierarchy levels** |
| nets / bundles | 5576 nets → **111 hbundles** (D0 50, D1 5, D2 25, D3 31) |
| runtime | **~13.5 s** end to end, obstruction model included |
| endpoint | 121 segments, **0 track overlaps, 0 interval violations**; 77 connectivity violations in 25 bundles — see *What is not clean* |

## Where the files come from

The DEF is **already in this repo**: `demo/ariane/ariane.def`. That is the
point rather than a convenience.

`opens_interchange.md` item 9 closed by establishing that `demo/ariane`'s
DEF and LEF are from two different technologies — a TILOS MacroPlacement
**NanGate45** benchmark paired with an **ASAP7** SRAM that arrived
separately ("got it later"). The conclusion was that the LEF was never the
LEF for that DEF. This vehicle is the other half: the file that *is* its LEF
was never missing from the world, only from here.

`fetch.py` gets it, from the same benchmark suite that produced the DEF:

| | upstream | |
|---|---|---|
| `ariane.v` | `Flows/NanGate45/ariane133/netlist/ariane.v` | 12 MB, 127 modules |
| `fakeram45_256x16.lef` | `Enablements/NanGate45/lef/` | the SRAM, 57.57 × 133.0 µm |

[TILOS-AI-Institute/MacroPlacement](https://github.com/TILOS-AI-Institute/MacroPlacement),
BSD 3-Clause (Regents of the University of California).

Three independent checks say these belong to our DEF: it was emitted by the
same Innovus build with the same `defOut -floorplan` command five days
apart; `ariane.v` instantiates `fakeram45_256x16` exactly **133** times
against the DEF's **133** components, with matching escaped instance names;
and the import reports `133 of 133`, `495 of 495`, `missing_cells: []`, with
every macro at **57.57 × 133.0 µm** — against the **0.5 × 0.5 µm** speck the
wrong LEF produced.

**Nothing is vendored, and every file is digest-pinned.** That is a direct
response to how item 9 arose: two files from different technologies sat
beside each other for years with nothing recording where either came from,
and a ReadMe between them describing a third design again. If upstream
moves, `fetch.py` fails with a digest mismatch rather than quietly handing
the flow a different design.

## Two things this vehicle established

**You do not need the NanGate45 standard-cell LIBRARY — but you do need its
TECHNOLOGY.** The library is unnecessary: the DEF is a floorplan DEF and
instantiates nothing but the SRAM, and the netlist's **76,731 standard cells
are skipped as library cells**.

The tech LEF is a different matter, and the claim that used to stand here —
that ten `def_layer` lines and the SRAM's LEF "reproduce the full-library
import exactly" — was **true of the placement and false of the grid**. A DEF
`TRACKS` statement gives positions and says nothing about how wide a wire is,
so every routing layer modelled ONE FULL-PITCH signal slot: a wire occupying
its whole track with no space beside it, and a "minimum wire" of 280 DBU
where this technology's metal1 is 140. Nothing about the route depended on
it — one signal track per pitch either way, so capacity is identical
(measured on current main: 121 segments, 0 overlaps, 77 violations in 25 bundles, identical either way) — but
every question about the WIDTH of a wire was degenerate, which is why no NDR
rule could mean anything on this design.

So `fetch.py` now also fetches `NangateOpenCellLibrary.tech.lef`, and
`import_lef_tech` takes its PITCH and WIDTH while the ten `def_layer` lines
keep owning each layer's identity. It carries the same proprietary
boilerplate as ever — *"provided pursuant to a License Agreement containing
restrictions on its use… valuable trade secrets… does not indicate actual or
intended publication"* — so it is fetched and **never vendored**, which is
the treatment the netlist and the SRAM LEF already get for milder reasons.

What that buys, measured here: a physically-motivated
`def_ndr em3 width 0.21um metal` (3× metal1's minimum wire) now resolves to
**2 slots/bit on metal1–metal6 and 1 on metal7–metal10** — the upper metal's
own 0.4 µm wire already exceeds the declared width, and BUDA-1914 says so
rather than leaving it implied.

**This vehicle found `opens_interchange.md` item 12, which has since
landed.** One fakeram macro carries **99 `OBS` rects**, so 133 of them
import **13,034 keepouts**; every keepout edge is a Hanan line and the grid
is a product, so it went from 2,479 cells to **2,508,972** and
`run_planner hier` did not finish in **50 minutes**.

`ariane133.buda` now declares **`set_keepout_loci outside`**: a keepout
lying inside a block still blocks but adds no grid line. Grid **6,327
cells**, and the flow runs **with** its obstruction model in ~19 s, so
`no_blockages` is gone.

The knob is opt-in and worth understanding before copying it: an interior
locus *is* reachable — a trunk may cross a block over-the-cell — so this
removes candidate positions along with the grid. It is the right trade for a
design whose LEF draws obstruction in 99 rects per macro, and the wrong one
for a design with a handful, where it measurably cost `flow/rv` a better
trunk. See item 12.

## What is not clean, and why that is the input's shape

Two separate things, worth not conflating.

**The unplaced containers** — `check_design` reports violations of one kind,
*block referenced in topologies but not in floorplan*.

A DEF is flat — `COMPONENTS` lists leaf instances only — so every level
between the die and the macros arrives from the Verilog with no geometry.
`derive_container_bboxes` gives 28 of them a bbox from their placed macro
children, but **16 pure-logic containers have no placed descendant at all**
— `ex_stage_i` and its whole subtree (`alu_i`, `branch_unit_i`, `i_mult`,
`i_div`, `i_multiplier`, `lsu_i`, `i_mmu`, `i_store_unit`), plus
`csr_regfile_i`, `id_stage_i`, `issue_stage_i`, `i_frontend`,
`i_perf_counters` and two under `i_nbdcache/i_miss_handler` — because a
*floorplan* DEF places no standard cells. Bundles that reach those blocks
have nothing to land on.

This is the honest ceiling of a floorplan DEF, not a routing defect.
Removing it needs a **fully placed** DEF, which upstream generates rather
than ships — so it costs an OpenROAD or Innovus run, not a download.  The
same is true of a power grid, and that run IS written down:
[openroad_pdn_recipe.md](../../docs/internal/openroad_pdn_recipe.md) grids
this design's floorplan with pdngen using the two LEFs `fetch.py` already
pins.

**The congestion is gone, and chasing it found a bug** (`opens_interchange.md`
item 13). When item 12 made the obstruction model affordable, this flow
reported **195 track overlaps**, which looked like the honest cost of routing
a real macro design. It was not. Each of the 133 macros carries a DEF
`+ HALO 10000`, and we were importing that **placement** halo as a routing
keepout with no layer — so it blocked every routing layer, across 5 µm around
every macro. Dropping it (a halo is placement information; `ROUTEHALO` is the
routing construct) takes this flow to **0 overlaps**, with all 13,034 `OBS`
keepouts still enforced.

Worth keeping as a caution: the first response was to treat it as QoR.
Promoting metal5–metal10 to `TOP` and running `negotiate_congestion` +
`ripup_reroute` got 195 → 23 overlaps in 84 s and read like progress. The
root-cause fix reaches 0 in 13.5 s with the original layer policy and no
healers, so none of that tuning survives. The signal that should have been
read first was in the advisory all along — supply-doomed seats reporting
**zero** signal tracks in their windows, on layers carrying 4,848 tracks.
Zero is not a congestion number.

## Relation to the other vehicles

| | nets | levels | authored here? |
|---|---|---|---|
| [`flow/def/`](../def/) | 36 | 3 | yes — smallest thing that exercises the path |
| [`flow/rv/`](../rv/) | 1230 | 5 | yes — a design large enough for quantity to bite |
| **`flow/ariane133/`** | **5576** | **5** | **no — a real design, in a real technology** |

The first two are the argument for having both a small and a large vehicle.
This one is the argument for having one you did not write: item 12 was
invisible to both, because a hand-written LEF has a handful of `OBS` rects —
a human typed them.
