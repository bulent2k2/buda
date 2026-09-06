# Tier 1a of the LibreLane study: the systolic array at N, arms F and H

The runnable half of [docs/internal/librelane_hier_flow.md](../../../docs/internal/librelane_hier_flow.md)
§7.1 (the vehicle), §7.2 (the arms) and §8 (the recipes, steps 7 and 7a-7d).
Everything here runs on the macOS + Docker setup of §8; the tree has no EDA
tools, so each script says what a pass looks like and fails loudly on the
shape it did not expect.

| Path | What it is |
|---|---|
| `gen.sh N` | emits the array at N (`btcl flow/tcl/tpu.tcl N -emit`) into `n<N>/`: `tpu_rtl.v` (synthesizable), `tpu.v` (BUDA's shell), `tpu.def` + `tpu.lef` (the placement), and `config.json` for **arm F** (flat, relative sizing).  Extra arguments go to `tpu.tcl` (`gen.sh 4 -PEPAD 56` makes every cell 32 um larger each way -- see the utilization note below) |
| `harm.sh N` | from `n<N>/` writes `n<N>/h/`, **arm H** (hierarchical, no BUDA): a block-hardening directory per leaf cell type, `top/` with the macros placed where the DEF placed them and the PDN derived from the array pitch, `predicted_lef/` for a dry run, and a `README.md` with the exact commands in order.  The logic and every rule is `harm.py` |
| `pdn_phase.py <top config.json> [<cell>.lef ...]` | the check to run AFTER hardening and BEFORE the top: reads the hardened macros' VPWR/VGND pin rectangles **and their `OBS` blocks** and the top's PDN config, reports every strap a macro's pin CUTS, every layer a macro obstructs outright, every macro no surviving strap feeds, and the smallest shift (or the equivalent PDN_VOFFSET/PDN_HOFFSET) that clears it.  §8 step 4's PSM-0069 lesson, made a step instead of a signoff surprise |
| `runtimes.py <run> [--set KEY=VALUE ...] [--block <run>[:<n>] ...] [--blocks-from <top config.json>] [--json]` | the row for the table: per-stage seconds and the §7.3 metrics; an H arm's row carries its blocks (wall = the longest, cpu = the sum, wire per PLACED instance), derived from the top's MACROS entry with `--blocks-from` |

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

Then the generated README's four steps, in order: **dry-run** `pdn_phase.py`
on the predicted LEFs (no tools), **harden** the four cells in parallel
(`librelane --dockerized --run-tag h config.json` in each, timing the batch for
the wall figure), **check** `pdn_phase.py` on the hardened LEFs, run the
**top**, and take the **row** with `runtimes.py top/runs/h --set N=4 --set arm=H --blocks-from
top/config.json --json >> ../../results.jsonl` (`--set` stamps the row with
its coordinates, as the flat arm's row is stamped).

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
* **What feeds a macro, and therefore the PDN.**  A hardened block's abstract
  LEF carries a whole-block cover obstruction on every layer it drew anything
  on (`write_abstract_lef -bloat_occupied_layers`, default on), and pdngen
  bloats that by the macro halo and subtracts it from the top's straps.  So
  the top's met4 straps are CUT over every macro whatever their phase, and a
  block hardened multilayer would obstruct met5 too and could not be fed at
  all.  Hence: the blocks are hardened `PDN_MULTILAYER` **false** (LibreLane's
  own setting for a macro meant for integration), `PDN_HPITCH`/`PDN_HOFFSET`
  are chosen so a met5 strap of each net crosses every macro's met4 pins of
  that net with room for a via -- that crossing, vias by the macro grid's
  `add_pdn_connect {met4 met5}`, is the macro's whole supply, so it is a GATE
  -- and `PDN_VPITCH`/`PDN_VOFFSET` (the PE column pitch over the smallest k,
  PPX/2 = 100 with the defaults, every macro of a cell at one phase) put a
  full VPWR+VGND pair in every standard-cell row fragment the halos leave,
  which is what met4 still does here.  The prediction is the block config
  `harm.sh` writes (met4 straps at core + 5 + 30k width 2, and the OBS the
  bloating will add), which is what `pdn_phase.py` on the hardened LEFs
  verifies.
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
passes), a macro that obstructs both PDN layers, pdngen's strap loop at the
far core edge, and `runtimes.py --blocks-from`.
