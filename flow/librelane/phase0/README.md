# Phase 0 of the LibreLane hierarchical-flow study

The runnable half of [docs/internal/librelane_hier_flow.md](../../../docs/internal/librelane_hier_flow.md):
a two-macro toy and the scripts that MEASURE what OpenROAD's routers do with
BUDA-style corridor handoffs.  The exact recipes, in order, are in that
document's §8; this is the file map.

| Path | What it is |
|---|---|
| `reg32/` | the block: 32-bit register + logic, 66 signal pins; `config.json` hardens it with LibreLane's own pin placer, `config_pins.json` with a **pin DEF template** (`gen_pins_def.py` writes `pins.def`) -- the block-side handoff BUDA will emit in phase 1 |
| `two_reg32/` | the top: `u0 -> u1` chained by one 32-bit bus `mid[31:0]`, both instances hardened `reg32` macros (paths point at `reg32/runs/phase0/final/`) |
| `measure/run_or.sh` | runs an OpenROAD script inside the LibreLane container on a run directory, mirroring `librelane --dockerized`'s mounts |
| `measure/guide_ref.tcl` → `extract_bus_guides.py` → `guide_test.tcl` → `check_inside.py` | **measurement A**: `set_nets_to_route` + `read_guides` -- does detailed routing seat the bus inside guides BUDA would write?  (`--dy` shifts the channel a whole gcell for the sharp form) |
| `measure/mark_fixed.py` → `fixed_test.tcl` → `compare_bus_wires.py` | **measurement B**: does a `+ FIXED` pre-routed bus survive `global_route` + `detailed_route` untouched? |

Authored against LibreLane 3.0.11 and first RUN on 2026-09-05 (macOS, Docker
Desktop 4.12, sky130A): both measurements pass, with the numbers in the
document's §8.  Every script says what a pass looks like and fails loudly on
the shape it did not expect; the shapes the first run found — DEF-escaped
net names, `read_guides` replacing rather than merging, guides in gcell
units, bash 3.2 on macOS, `docker run -t` from a script — are folded into
the scripts and the recipes.  `run_or.sh` takes `LIBRELANE_IMAGE` for a
non-stock image (a Docker engine older than 25 cannot start the stock one).
