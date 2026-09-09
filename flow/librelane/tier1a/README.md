# Tier 1a of the LibreLane study: the systolic array at N, arms F, H and H+B

The runnable half of [docs/internal/librelane_hier_flow.md](../../../docs/internal/librelane_hier_flow.md)
§7.1 (the vehicle), §7.2 (the arms) and §8 (the recipes, steps 7 and 7a-7f).
Everything here runs on the macOS + Docker setup of §8; the tree has no EDA
tools, so each script says what a pass looks like and fails loudly on the
shape it did not expect.

| Path | What it is |
|---|---|
| `gen.sh N` | emits the array at N (`btcl flow/tcl/tpu.tcl N -emit`) into `n<N>/`: `tpu_rtl.v` (synthesizable), `tpu.v` (BUDA's shell), `tpu.def` + `tpu.lef` (the placement), and `config.json` for **arm F** (flat, relative sizing).  Extra arguments go to `tpu.tcl` (`gen.sh 4 -PEPAD 100` is what the first real run needed: the RTL's PE is 5,964 um² of cells and global placement refused the default die at 152 % utilization -- see the utilization note below and the doc's step 7a) |
| `harm.sh N` | from `n<N>/` writes `n<N>/h/`, **arm H** (hierarchical, no BUDA): a block-hardening directory per leaf cell type, `top/` with the macros placed where the DEF placed them and the PDN derived from the array pitch, `predicted_lef/` for a dry run, and a `README.md` with the exact commands in order.  The logic and every rule is `harm.py` |
| `pdn_phase.py <top config.json> [<cell>.lef ...]` | the check to run AFTER hardening and BEFORE the top: reads the hardened macros' VPWR/VGND pin rectangles **and their `OBS` blocks** and the top's PDN config and PREDICTS what pdngen builds, step by step as OpenROAD's `src/pdn` does it -- the straps, every CUT a pin or OBS makes in them (spacing across, halo along), the vias the macro grid's `add_pdn_connect` makes at same-net cross-layer overlaps, and the TRIM that removes every fragment with fewer vias than the layer needs -- then partitions what is left with `pdn_connect.py`'s own `net_components` and reports every TRIM (information), every STRANDED terminal (a pin none of whose rectangles is on its net's grid), every FLOATING fragment (survives trim off the grid: the "unconnected shapes" `PSM-0069` counts), and the smallest VERIFIED whole-placement shift (or the equivalent PDN_VOFFSET/PDN_HOFFSET) that clears it.  **ADVISORY** -- a prediction from the LEFs and the config, not the verdict, which is OpenROAD's own PSM at the end of the top run; it does not gate `harm.sh`.  Its older version counted every same-layer meeting as a defect, and acting on that FAIL by hand once turned a working plan into `PSM-0069` (§11 item 8; #895) |
| `pdn_connect.py <pdn.def> [<cell>.lef ...]` | the check to run AFTER the top's PDN stage: reads the DEF **pdngen wrote** and reports, per macro power pin, whether a via landed on it and -- where none did -- whether the pin had a same-net crossing on the other `add_pdn_connect` layer at all.  The verdict is per TERMINAL (a LEF `PIN` is one node, so a via on any of its access rectangles feeds it) with the per-rectangle detail printed beside it.  Nothing is modelled: every cut, halo and obstruction subtraction has already happened by the time that metal is in the file.  `--self-cross <lef>...` asks the LEF-only half (does a power pin cross its OWN net on the other connect layer -- a per-cell property that connects or floats a net on every phase, which no offset search can fix).  It also partitions each power net's DEF metal into ELECTRICAL components (same-layer touch, joined across layers by the vias) and names every fragment off the main grid and the terminals it strands -- the `PSM-0069` failure mode a per-pin rollup cannot see.  The post-mortem twin of `pdn_phase.py`; `PSM-0040`/`PSM-0069` stay the verdict  Three things it reads that decide what "connected" means (#900): the DEF's `VIAS` section, so a via joins only the layers it connects (a via4 over a met1 rail does not join the rail; a via the DEF does not define is joined on every layer, counted); the DEF's `PINS` section, PSM's SOURCES -- the main component is the one a top pin touches, not the largest, because PSM asks reachability from the supply; and `--explain <instance|cell|terminal|*>`, which prints the chain of rects and joins by which a macro terminal reaches a source.  The terminal VERDICT is that reachability: `connected` when a chain from one of its vias reaches a top pin, `unsourced` when its via joins metal the supply never enters -- the macro's OWN pin on the other layer included, which pdngen vias too (`getInstancePins`) and which is exactly the N=8 failing plan: every pe_cell VGND rect carried such a via, the old rollup said connected, and PSM counted all 512 of them unconnected (measured on the artefacts: 0 unsourced on the working plan, 64 per net on the failing one) |
| `drc_locate.py <drc.klayout.lyrdb> <top.def> [<cell>.lef ...]` | the reader for a top run's KLayout DRC report (#896): maps every marker from TOP coordinates to the instance holding it and the CELL-LOCAL spot (placement and orientation inverted), groups equal spots -- five `m2.2` markers at `acc_cell` local (69.4, 0.0) are ONE defect -- and, with the macro LEFs, says per offending edge whether it lies on a pin/OBS rectangle the LEF claims, in a HOLE of the abstract (inside the box on no claimed shape: the router reads the spot as free, so it is the top's wire or macro metal the LEF omits, the nearest claimed shape named), or outside the box.  A claimed edge against a hole edge is the abstraction NOTCH the N=8 markers turned out to be -- the router overhanging a wire into a corner Magic's LEF leaves uncovered beside a pin, against the macro's real metal -- whose fix is in the abstract, not the placement.  A reader, not a check: it says which experiment to run first, never whether the violation is real |
| `notch_obs.py <cell.gds> <magic.lef> <out.lef> [--layer met2] [--gds-layer L/DT]` | #896's third fix, the one the two measured ones point at (§11 item 13): obstruct the metal the abstract OMITS and nothing else.  Reads the cell's real metal on the layer from its GDS (BOUNDARY rectangles, flattened through SREF/AREF), the LEF's claim on it (every pin's rects plus the OBS), and writes the per-rectangle DIFFERENCE into the OBS as `LAYER met2` RECTs -- on the #896 shape exactly the notch beside `in[22]`, with the pin's own metal, the metal under the blanket and the subcells' shapes left alone, so every place the top legitimately routes met2 stays free (the met2 BLANKET closed the DRC and cost 6,233 Magic overlaps for that reason).  Refuses a non-rectangle shape rather than boxing it.  Whether the patched abstract closes the marker without costing pin access is a top run with it in place of Magic's LEF |
| `notch.sh N [--layers met2]` | runs the pair above per leaf cell, between hardening and the top: `rectify_gds.py` (in the LibreLane image's KLayout -- `pya` is KLayout's own module, so `run_or.sh`, which runs `openroad`, cannot carry it) then `notch_obs.py`, writing `<cell>.notch.lef` beside Magic's.  **Not optional and not skippable**: `harm.py` points the top's `MACROS.<cell>.lef` at that file, so a top run without this step stops at once on a LEF that is not there -- which is the point, since the fix was three hand steps per cell per run and a skipped one brings the marker back with nothing saying so (#907).  A cell whose GDS the decomposition cannot handle is REFUSED, loudly and with no `.notch.lef` left standing; the generated README's step 2 has the hand recipe and the carry-it-unpatched escape |
| `runtimes.py <run> [--set KEY=VALUE ...] [--block <run>[:<n>] ...] [--blocks-from <top config.json>] [--json]` | the row for the table: per-stage seconds and the §7.3 metrics; an H arm's row carries its blocks (wall = the longest, cpu = the sum, wire per PLACED instance), derived from the top's MACROS entry with `--blocks-from` |
| `apply_sizes.py <sizes dir> [--n N] [--baseline <n dir>] [--optimize-aspect] [--args] [--json]` | the FIRST of arm **H+B**'s three contributions: turns `emit_block_size`'s fragments (from `size.buda`) into the `gen.sh` knobs that re-emit the array at BUDA's block sizes, and predicts the die with the emitter's own arithmetic. `--optimize-aspect` reshapes the PE to minimise the ARRAY's die rather than the cell's — same area, same face floors. See the study's §8 step 7e |
| `pins.sh N` | the SECOND: generates and runs `n<N>/pins.buda`, which routes the array and writes one `FP_DEF_TEMPLATE` per leaf CELL TYPE into `n<N>/pins/` -- a template, because a cell is hardened once and placed N² times.  It reads `tpu_rtl.v` (not `tpu.v`) because `FP_DEF_TEMPLATE` is ALL OR NOTHING: declaring it makes LibreLane skip `OpenROAD.IOPlacement`, so a port the template omits is placed by nobody.  Three costs it REPORTS per cell -- pins snapped onto the block's own track grid (BUDA-1713), disputed pins taken from a reference instance (BUDA-1714, for a cell whose instances have different neighbours), and pins moved off a track another net's pin already held (BUDA-1715).  Study §8 step 7f |
| `harm.sh N --pins pins` | writes arm **H+B**'s blocks instead of arm H's: `FP_DEF_TEMPLATE` per block config plus `RT_MAX_LAYER met3` (a template costs internal wire, extra wire reaches met4, and a block with a met4 `OBS` made pdngen drop the straps that fed it -- §8 step 5b).  Without `--pins` the output is byte-identical to arm H's |
| `guides.sh N` | the THIRD: generates and runs `n<N>/h/top/guides.buda`, which routes the top's buses against the placement LibreLane ACTUALLY used (the run's own manual-macro-placement DEF, since `harm.py` shifts every macro) and writes the corridors in the file `read_guides` reads |
| `guide_route.tcl` | puts those corridors into the ODB, through phase 0's `measure/run_or.sh`: global-route everything BUT the guided nets, merge in BUDA's entries (FILTERING the router's own for those nets -- `write_guides` emits the guides the ODB already holds), `read_guides`, `write_db`.  It stops there ON PURPOSE: LibreLane's own `OpenROAD.DetailedRouting` is resumed on the result, so every routing metric and the whole signoff tail stay LibreLane's |

## Arm F (flat) at N

```bash
cd ~/src/buda
for N in 2 4 8; do flow/librelane/tier1a/gen.sh $N; done
cd flow/librelane/tier1a/n4 && librelane --dockerized --run-tag flat config.json
python3 ../runtimes.py runs/flat --set N=4 --set arm=F --json >> ../results.jsonl
```

## Arm H (hierarchical, no BUDA) at N

```bash
cd ~/src/buda
flow/librelane/tier1a/gen.sh 4 -PEPAD 100     # the PEPAD the first real run settled on
flow/librelane/tier1a/harm.sh 4              # writes n4/h/ and prints the plan
cd flow/librelane/tier1a/n4/h && cat README.md
```

Then the generated README's five steps, in order: **dry-run** `pdn_phase.py`
on the predicted LEFs (no tools, and ADVISORY -- see its row above), **harden**
the four cells in parallel
(`librelane --dockerized --run-tag h config.json` in each, timing the batch for
the wall figure), **`notch.sh N`** to patch each abstract (not optional -- the top's
`MACROS` name the patched LEFs), read `pdn_phase.py` on the hardened LEFs (advisory too), run the
**top** -- whose `PSM-0040`/`PSM-0069` check is the PDN verdict, with
`pdn_connect.py` to localise a failure -- and take the **row** with `runtimes.py top/runs/h --set N=4 --set arm=H --blocks-from
top/config.json --json >> ../../results.jsonl` (`--set` stamps the row with
its coordinates, as the flat arm's row is stamped).

## Arm H+B (hierarchical, with BUDA) at N

All three contributions: BUDA's block sizes, its pins, its corridors.

```bash
cd ~/src/buda
bin/buda --no-viz flow/librelane/tier1a/size.buda        # if out/ is empty
cd flow/librelane/tier1a
python3 apply_sizes.py out --n 2 --baseline n2 --optimize-aspect   # read it first
./gen.sh 2 $(python3 apply_sizes.py out --n 2 --optimize-aspect --args)
./pins.sh 2                       # -> n2/pins/<cell>.def, one per leaf cell TYPE
./harm.sh 2 --pins pins           # -> n2/h, with FP_DEF_TEMPLATE in every block
cd n2/h && cat README.md          # steps 1, 2, 4a, 4b, 4c, 5
```

The generated README differs from arm H's in two places.  Step 1 adds
`tools/pin_def_verify.py` per cell -- every TEMPLATE pin must be in the
hardened DEF at the same ABSOLUTE rectangle (never the same `PLACED` origin:
OpenROAD re-centres every one it writes).  And step 4, the top, runs in
three parts, because LibreLane 3.0.11 has no step that READS a guide file:

```bash
../../notch.sh 2                  # the patched abstracts the top's MACROS name
(cd top && librelane --dockerized --run-tag hb \
    --to OpenROAD.DetailedRouting --skip OpenROAD.DetailedRouting config.json)
../../guides.sh 2
ODB=$(ls -t top/runs/hb/*/*.odb | head -1)
../../../phase0/measure/run_or.sh top/runs/hb ../../guide_route.tcl \
    ODB=$ODB GUIDE=$PWD/top/out/buda_bus.guide OUT=$PWD/top/out
(cd top && librelane --dockerized --last-run --from OpenROAD.DetailedRouting \
    -e odb="$PWD/out/guided.odb" config.json)
```

The cut is at DETAILED routing rather than right after global routing
because `RepairDesignPostGRT` and `ResizerTimingPostGRT` each re-run
`grt.tcl`, and BUDA's guides have to be the last word.  Measured at N = 2
(§8 step 7f): 168/168 template pins verified, `All shapes on net VPWR are
connected` and VGND, route DRC 0, KLayout DRC 1.

When the top's PDN check fails (`[PSM-0069] Check connectivity failed`), read
what pdngen actually did rather than re-deriving what it should have done:

```bash
cd flow/librelane/tier1a/n4/h/top
python3 ../../pdn_connect.py runs/h/*-pdn/*.def ../*/runs/h/final/lef/*.notch.lef --json pdn.json
python3 ../../pdn_connect.py --self-cross ../*/runs/h/final/lef/*.notch.lef
```

The first names every power pin with no via, says whether it had anything to
reach, and whether a via it has REACHES a source (the top's own pins) -- an
`unsourced` terminal is one whose via joins metal the supply never enters,
PSM-0038's shape; the second asks, from the LEFs alone, whether each power
pin crosses its OWN net on the other connect layer.  Read the second for
less than it first claimed: `InstanceGrid::getInstancePins` puts a macro's
pins in the set `Grid::getIntersections` searches, so such a crossing is one
pdngen MAY via -- but the N=8 `PDN_HOFFSET 109.3` run offered it 512 pe_cell
VGND pin-on-pin crossings over the floor and it made none (#900), so a `yes`
is not a connection and a `SPLIT:` line is a cell to look at, not a verdict.
The verdict before a top run is `pdn_phase.py`; after it, the first command
here, and PSM.

What `harm.sh` decided, and why (the full statement is `harm.py`'s docstring):

* **Instance names.**  The DEF writes `row_0/pe_0`; the top is synthesized
  flattened, and Yosys's separator is a dot, so the macro instance is
  `row_0.pe_0`.  `Odb.ManualMacroPlacement` exits 1 on a declared instance the
  netlist lacks, so a wrong rule fails there, not at signoff.
* **The die-fit shift.**  The emitter places `feed_*` at x = -140, outside the
  DEF's own die.  Every instance is translated by one (dx, dy) = (max(0, die x0
  + halo - min x), max(0, die y0 + halo - min y)) -- (150, 0) for the default
  set, measured from the DIE's own origin, so a DEF whose DIEAREA does not
  start at (0, 0) shifts by more, not less -- and the die is the DEF's;
  `top/placement.json` holds both coordinates of every instance.  A DEF that
  already fits is not moved.  What must fit is the macro BODY; a halo poking
  past the die edge only means no other cell fits beside it there.
* **The PDN phase.**  pdngen never SHORTS a strap to a macro's power pin, it
  CUTS the strap, so a same-layer meeting is a clip and what FEEDS a macro is
  the cross-layer crossing the macro grid vias (`add_pdn_connect {met4 met5}`):
  a top met4 strap over the block's met5 pin.  `PDN_VPITCH` is the PE column
  pitch over the smallest k (PPX/2 = 100 with the defaults) whose offset clears
  every cell's PREDICTED met4 pins, crosses every cell's met5 pins, and puts a
  VPWR+VGND pair in every standard-cell row fragment the halos leave;
  `PDN_HPITCH` is the row pitch with the offset whose met5 straps clear every
  macro's predicted met5 pins -- and since that clearance is `inf` for every
  offset whose straps miss the macros entirely, the tie is broken by distance
  from the macro BOXES, so the straps land mid-channel.  The prediction is the
  block config `harm.sh` writes (straps at core + 5 + 30k, width 2), which is
  what `pdn_phase.py` on the hardened LEFs verifies.
* **Whose metal the obstruction is.**  `final/lef/<cell>.lef` is MAGIC's LEF
  (OpenROAD's `-bloat_occupied_layers` one is the separate
  `<cell>.openroad.lef`, which nothing here reads), so its OBS is the block's
  actual metal -- and on the PDN layers that is the block's own grid, the same
  rectangles as its power pins, which the phase search clears by clearing the
  pins.  FOREIGN metal on a PDN layer is the dangerous kind: signal routing
  pushed up there by a pin layout, wherever the router put it, which no phase
  search cleared and which pdngen's cut then removes -- the phase-0 toy's
  IR-drop failure.  `pdn_phase.py` classifies every OBS rect and reports that
  case, naming `RT_MAX_LAYER` as the lever.
* **Utilization: the PEPAD is 100.**  The emitter sizes a PE for the bus faces
  BUDA routes to (152 x 56 um = 12 standard-cell rows) and the RTL's PE
  measures **5,964 um^2 / 624 cells** (the first real run at N = 4), so at the
  default `OpenROAD.GlobalPlacement` refuses with `GPL-0301 Utilization
  152.234 %`.  Two bars apply in turn -- GPL-0301 at 100 %, then
  `PL_TARGET_DENSITY_PCT` 50 via GPL-0302 -- and PEPAD 56 clears only the
  first (~68 %), 88 lands on the line (49.8 %), 100 gives 228 x 132 um at
  ~25-30 %.  So the benchmark uses `gen.sh N -PEPAD 100`; DEF, LEF and the H
  arm scale together, and arm F is unaffected (it sizes itself from the RTL).
  `harm.sh` prints the estimate per cell -- measured where a run has measured
  it, else §7.1's Yosys total times the ~1.7x LibreLane ratio that section
  records -- and names the PEPAD to regenerate with.

`test/tests/test_librelane_tiers.py` pins all of this at N = 2 and 4 --
every location, the die sizes, the name rule, the removed bodies, the pitch
rule, the met5 crossing, the predicted-pin dry run, the shift measured from
a moved die origin -- plus `pdn_phase.py` on the phase-0 toy's own geometry
(u0 at x = 20 puts its VGND pin at 33.22-35.22 under the 34.72-36.32 strap,
pdngen cuts that strap and u0's VPWR is left unfed, 0.8 um clears it, x = 10
passes), a block whose OBS is its own power metal against one carrying
foreign metal on a PDN layer, pdngen's strap loop at the far core edge, and
`runtimes.py --blocks-from`.
