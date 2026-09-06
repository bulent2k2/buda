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
| `pdn_phase.py <top config.json> [<cell>.lef ...]` | the check to run AFTER hardening and BEFORE the top: reads the hardened macros' VPWR/VGND pin rectangles and the top's PDN config, reports every pin under a strap of the other net and every macro no strap feeds, and the smallest shift (or the equivalent PDN_VOFFSET/PDN_HOFFSET) that clears it.  §8 step 4's PSM-0069 lesson, made a step instead of a signoff surprise |
| `runtimes.py <run> [--block <run>[:<n>] ...] [--blocks-from <top config.json>] [--json]` | the row for the table: per-stage seconds and the §7.3 metrics; an H arm's row carries its blocks (wall = the longest, cpu = the sum, wire per PLACED instance), derived from the top's MACROS entry with `--blocks-from` |

## Arm F (flat) at N

```bash
cd ~/src/buda
for N in 2 4 8; do flow/librelane/tier1a/gen.sh $N; done
cd flow/librelane/tier1a/n4 && librelane --dockerized --run-tag flat config.json
python3 ../runtimes.py runs/flat --json >> ../results.jsonl
```

## Arm H (hierarchical, no BUDA) at N

```bash
cd ~/src/buda
flow/librelane/tier1a/harm.sh 4              # after gen.sh 4; writes n4/h/ and prints the plan
cd flow/librelane/tier1a/n4/h && cat README.md
```

Then the generated README's four steps, in order: **dry-run** `pdn_phase.py`
on the predicted LEFs (no tools), **harden** the four cells in parallel
(`librelane --dockerized --run-tag h config.json` in each, timing the batch for
the wall figure), **check** `pdn_phase.py` on the hardened LEFs, run the
**top**, and take the **row** with `runtimes.py top/runs/h --blocks-from
top/config.json --json >> ../../results.jsonl`.

What `harm.sh` decided, and why (the full statement is `harm.py`'s docstring):

* **Instance names.**  The DEF writes `row_0/pe_0`; the top is synthesized
  flattened, and Yosys's separator is a dot, so the macro instance is
  `row_0.pe_0`.  `Odb.ManualMacroPlacement` exits 1 on a declared instance the
  netlist lacks, so a wrong rule fails there, not at signoff.
* **The die-fit shift.**  The emitter places `feed_*` at x = -140, outside the
  DEF's own die.  Every instance is translated by one (dx, dy) = (max(0, halo -
  min x), max(0, halo - min y)) -- (150, 0) for the default set -- and the die
  is the DEF's; `top/placement.json` holds both coordinates of every instance.
  A DEF that already fits is not moved.
* **The PDN phase.**  `PDN_VPITCH` is the PE column pitch over the smallest k
  (PPX/2 = 100 with the defaults) whose offset clears every cell's PREDICTED
  met4 pins, crosses every cell's met5 pins (that crossing feeds the macro) and
  puts a VPWR+VGND pair in every standard-cell row fragment the halos leave;
  `PDN_HPITCH` is the row pitch (128) with the offset farthest from every
  macro's predicted met5 pins.  The prediction is the block config `harm.sh`
  writes (straps at core + 5 + 30k, width 2), which is what `pdn_phase.py` on
  the hardened LEFs verifies.
* **Utilization.**  The emitter sizes a PE for the bus faces BUDA routes to
  (152 x 56 um = 12 standard-cell rows), and the RTL's PE synthesizes to
  roughly 3.9k um^2 (§7.1's totals), about 85 % of that core -- above the
  block's `PL_TARGET_DENSITY_PCT` 50, so expect `OpenROAD.GlobalPlacement` to
  refuse.  `harm.sh` prints the estimate and the remedy: regenerate the whole
  set with `gen.sh N -PEPAD 56` (DEF, LEF and the H arm scale together; arm F
  is unaffected, it sizes itself from the RTL).  The first real run should
  settle which PEPAD the benchmark uses at every N.

`test/tests/test_librelane_tiers.py` pins all of this at N = 2 and 4 --
every location, the die sizes, the name rule, the removed bodies, the pitch
rule, the predicted-pin dry run -- plus `pdn_phase.py` on the phase-0 toy's
own geometry (u0 at x = 20 collides at 33.22-35.22 under the 34.72-36.32
strap, 0.8 um clears it, x = 10 passes) and `runtimes.py --blocks-from`.
