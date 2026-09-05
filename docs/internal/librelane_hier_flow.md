# BUDA in a LibreLane hierarchical RTL-to-GDS flow — the plan

**Status: PLAN with a runnable phase 0.**  Written 2026-09-05 against
LibreLane 3.0.11 (`librelane/librelane` @ `33ab648`) and its CI vehicles
(`librelane/librelane-ci-designs`).  Every LibreLane fact below was read from
that source, with the file named; every claim about OpenROAD's routers that
no document makes is something phase 0 MEASURES.  The phase-0 files are under
[`flow/librelane/phase0/`](../../flow/librelane/phase0/README.md); they were
authored here and run for the first time on your machine.

## 1. What we are doing, in one paragraph

Keep the hierarchy the RTL already declares.  One synthesis with hierarchy
kept gives every module's area; BUDA takes the blocks and the top-level
netlist and decides what a flat flow decides blindly or not at all — where
each block goes, how big it is, where its pins sit on which face, and where
the buses between blocks run; each distinct block is hardened ONCE under
those constraints, in parallel; the top integrates them with the buses
routed inside BUDA's corridors.  Then a three-arm benchmark on a design that
scales measures what hierarchy costs, what BUDA gives back, and where the
crossover sits.

## 2. Decisions (the pushbacks, adopted)

These were framing changes in the first draft; they are now the plan.

1. **The hypothesis is that BUDA shrinks the hierarchical penalty and moves
   the crossover — not that hierarchy wins PPA.**  A hierarchical flow
   structurally pays area (10 µm macro halos by default, channels, pin-access
   rows, no cross-boundary logic sharing) and often timing (no re-buffering
   across a hardened boundary) in exchange for runtime, parallelism, reuse
   and predictability.  On every design in LibreLane's CI set a flat run will
   win PPA with or without BUDA; that is the trade, not a verdict.  So the
   benchmark has **three arms** (§7.2) and a vehicle that **scales** (§7.1),
   and its success criterion is written down before the runs (§7.4).
2. **One kept-hierarchy synthesis, not per-module "fast" synthesis.**
   Synthesis is not the expensive stage; P&R is.  `SYNTH_HIERARCHY_MODE
   keep` (or `SYNTH_KEEP_HIERARCHY_MIN_COST`, LibreLane's own notion of
   "large enough") gives every module's area in one `stat -json`, the
   threshold IS the partition, and it is the same netlist the flat arm uses —
   one fewer variable in the comparison.  `SYNTH_ELABORATE_ONLY` maps
   nothing and so reports no area.
3. **Corridors enter as route guides first; FIXED wires only after phase 0
   measures them.**  `grt` documents `set_nets_to_route` (route only a
   subset) and `read_guides`; BUDA's `emit_guides` already computes exactly a
   guide's content.  What neither router's README says is what happens to a
   net that already carries FIXED wiring — so that is measurement B, not a
   design assumption.  Obstructions cannot reserve a corridor for particular
   nets and are not a pre-route mechanism.
4. **Per-pin timing budgets are phase 3, and the first benchmark's timing
   column carries that caveat.**  LibreLane budgets every block I/O with one
   number (`IO_DELAY_CONSTRAINT`), and its own macro guide warns that this is
   where hierarchical timing goes wrong.  Until BUDA derives per-pin budgets
   from planned wire length, the boundary paths' slack measures the SDC
   default, not BUDA.

Answers recorded since the first draft: **LibreLane runs on your macOS box
under Docker** (§8 is written for exactly that); **PDK defaults to sky130A**
(LibreLane's default; say so if gf180mcu); the **vehicle** is
`flow/tcl/tpu.tcl` made synthesizable with real PE RTL (§7.1), unless you
prefer an existing design.

## 3. What LibreLane has today

The `Classic` flow (`librelane/flows/classic.py`) is a fixed step sequence:
lint → `Yosys.Synthesis` → `OpenROAD.Floorplan` → `Odb.ManualMacroPlacement`
→ PDN → `Odb.AddRoutingObstructions` → `OpenROAD.IOPlacement` /
`Odb.CustomIOPlacement` / `Odb.ApplyDEFTemplate` → global placement → CTS →
`OpenROAD.GlobalRouting` → `OpenROAD.DetailedRouting` → RCX → STA →
stream-out → DRC/LVS.  OpenROAD links the netlist flat (`link_design`
without `-hier`, `scripts/openroad/common/io.tcl:208`).

The pieces a hierarchical flow needs are all present; the DECISIONS are
manual or blind:

| Concern | LibreLane mechanism | Decision comes from |
|---|---|---|
| Which modules stay hierarchical | `SYNTH_HIERARCHY_MODE flatten\|deferred_flatten\|keep`, `SYNTH_KEEP_HIERARCHY_MIN_COST` (gate threshold), `SYNTH_KEEP_HIERARCHY_MODULES/INSTANCES` (`steps/pyosys.py:482-509`) | user / threshold |
| Block area | Yosys `stat -json` → `<step>/reports/stat.json`, per module under `keep` (`scripts/pyosys/synthesize.py:346`) | synthesis |
| Hardened block as macro | `MACROS`: `gds`/`lef`/`nl`/`spef`/`lib` + `instances{name:{location,orientation}}` (`config/variable.py:107`, and the *Using Macros* guide, `usage/using_macros.md` in LibreLane's own docs) | user |
| Macro placement | `Odb.ManualMacroPlacement` (fixed; `steps/odb.py:404`). **No automatic macro placer in Classic.** | user, by hand |
| Macro halo | `FP_MACRO_HORIZONTAL/VERTICAL_HALO`, default 10 µm | default |
| Block pin placement | `OpenROAD.IOPlacement` (auto); `Odb.CustomIOPlacement` (`IO_PIN_ORDER_CFG`: ordered names/regexes per side); `Odb.ApplyDEFTemplate` (`FP_DEF_TEMPLATE`: copies die area and **exact non-power pin locations**, matched by name, `strict`/`permissive`; `odb.py:254`, `odbpy/defutil.py:relocate_pins`) | per block, blind to the top |
| Block die size | `FP_SIZING absolute` + `DIE_AREA` | user |
| Keep-outs | `FP_OBSTRUCTIONS` (hard placement), `PL_SOFT_OBSTRUCTIONS` (soft), `ROUTING_OBSTRUCTIONS` (layer rects → `dbObstruction`, removed after DRT) | user |
| Global routing | `global_route` + `write_guides` (`scripts/openroad/common/grt.tcl`); OpenROAD `grt` also has **`set_nets_to_route`** and **`read_guides`** | tool |
| Detailed routing | `detailed_route -droute_end_iter 64 -or_seed 42` (`scripts/openroad/drt.tcl`) | tool |
| I/O timing budgets | `base.sdc:19-45`: `set_input_delay`/`set_output_delay` = `CLOCK_PERIOD × IO_DELAY_CONSTRAINT / 100`, **uniform** | one number |
| Hierarchical STA | macro `nl`+`spef` (preferred) or `lib`, else black-box | — |
| Metrics | `design__instance__area`, `design__die__area`, `design__instance__utilization`, `timing__setup__ws/tns`, `timing__hold__ws`, `power__total/internal/switching/leakage`, `route__wirelength`, `route__drc_errors`, `magic/klayout__drc_error__count`, per-step `runtime` | — |

The CI vehicles are regression designs, not benchmarks (largest: `aes_core`
~5.4k lines, `picorv32a` ~3k in one module).  Several have the shape BUDA's
bottom-up path exists for — repeated instances of one module: `PPU`
(`Sprite` ×8), `salsa20` (`salsa20_qr` ×4), `manual_macro_placement_test`
(`spm` ×2).  That last one is the stock macro-flow test, and its two macros
share no net — each talks only to top ports — which is why phase 0 has its
own two-block toy.

## 4. The flow

```
                 top.v (+ submodule RTL)
                        │
   [S0] Yosys.Synthesis, SYNTH_HIERARCHY_MODE=keep          one run; per-module area
        (or SYNTH_KEEP_HIERARCHY_MIN_COST=N = the partition)  from reports/stat.json
                        │
   [B1] BUDA: import top netlist + block areas               import_verilog + generated LEF
        size blocks (area/util AND face capacity)            the tpu_lib rule, generalized
        place blocks (PlacementOptimizer SA/GA)              FloorplannerEngine, headless
        run_hier_bundler → generate_hier_topologies →
        run_planner hier → run_nuts → run_detailed_nuts      the existing pipeline
                        │
        per block:   DEF template (die area + PINS at exact positions)  → FP_DEF_TEMPLATE
                     DIE_AREA                                            → FP_SIZING absolute
                     [phase 3] per-pin input/output delays               → PNR_SDC_FILE
        for the top: MACROS instances {location, orientation}
                     route guides for the bus nets  (emit_guides, OpenROAD format)
                     PL_SOFT_OBSTRUCTIONS under corridors (export_def_blockages density)
                        │
   [S1] per distinct block, IN PARALLEL: LibreLane Classic     one hardening per CELL
        → gds / lef / nl / spef
                        │
   [S2] top: LibreLane Classic with MACROS,
        set_nets_to_route <all but bus nets> ; read_guides buda.guide
        → hierarchical STA with nl + spef
```

S1 hardens each distinct CELL once: a module instantiated eight times is
hardened once and placed eight times, so hardening is O(cells) — the same
solve-once-copy premise as BUDA's bottom-up planning (`set_bottom_up`,
`align_bottom_up`, `check_template_tracks`), and the two compound.

## 5. Interfaces: what BUDA emits, and whether it exists

| BUDA output | LibreLane input | Exists? |
|---|---|---|
| Block placement | `MACROS.<cell>.instances.<inst>.location/orientation` | trivial writer, NEW |
| Block size | `DIE_AREA` + `FP_SIZING absolute` | rule in `flow/tcl/tpu_lib.tcl` (face-capacity aware); a command, NEW |
| Block pins at exact positions | `FP_DEF_TEMPLATE` — the shape `phase0/reg32/gen_pins_def.py` writes by hand | NEW writer: per net bit landing on a block, face + coordinate + layer from the DNUTS `net_segment` endpoint at the busterm, transformed to block-local through the instance orientation (`orient_rect.py`) |
| Bus corridors | `read_guides` after `set_nets_to_route` | `emit_guides` has the content (per-bit tapered); the OpenROAD guide format (`phase0/measure/guide_io.py`) is NEW |
| Placement keep-out under corridors | `PL_SOFT_OBSTRUCTIONS` | `export_def_blockages density`; the tuple list is NEW |
| Bus wiring as FIXED pre-routes | DEF `NETS … + FIXED` | NEW, gated on measurement B |
| Per-pin timing budgets | `set_input_delay`/`set_output_delay` in `PNR_SDC_FILE` | NEW capability, phase 3 |
| Headless block placement | — | `PlacementOptimizer.run_sa/run_ga` bound; `floorplanner_commands.optimize_placement`; no `.buda` command yet |
| sky130 stack | — | `import_lef_tech` |

The block-side rows are file formats LibreLane already consumes: no tool
risk.  The top-side corridor handoff is where the risk is, and it is ranked:

| # | Mechanism | BUDA writes | Tool risk | Fidelity |
|---|---|---|---|---|
| A | **Guides** | guide file for bus nets; `set_nets_to_route` for the rest | LOW — both documented; **measurement A** checks `read_guides` ADDS and `detailed_route` seats inside | router picks tracks within BUDA's corridor |
| B | **FIXED wires** | DEF `+ FIXED` wiring for bus nets | **measurement B** — undocumented in `grt`/`drt` | BUDA's exact bit-wires |
| C | Obstructions | `ROUTING_OBSTRUCTIONS` / `PL_SOFT_OBSTRUCTIONS` | none | keep-out only |

## 6. Phase 0 — what it establishes

Phase 0 costs a few hours of a laptop and settles, with files rather than
opinions:

1. LibreLane runs, flat and with macros, on this machine (recipes 0–1).
2. A LibreLane-hardened block accepts a pin DEF template of the shape BUDA
   will write, and lands its pins where the template says (recipes 2–3).
3. A top with two hardened macros and a bus between them routes (recipe 4).
4. **Measurement A**: with the bus withheld from `global_route`
   (`set_nets_to_route`) and its guides supplied by `read_guides`, does
   `detailed_route` seat the bus inside those guides — including a corridor
   deliberately SHIFTED from where the router would have put it — while the
   other nets' guides survive the merge?  (recipe 5)
5. **Measurement B**: does a bus carrying `+ FIXED` wiring come out of
   `global_route` + `detailed_route` byte-identical?  (recipe 6)

A passes ⇒ mechanism A is the phase-1 handoff.  B passes ⇒ FIXED pre-routes
are available for phase 3.  Either failing is a result, not a blocker: the
scripts print what the router did instead.

## 7. The benchmark

### 7.1 Vehicle

A design whose scale is a parameter: a **systolic array with real MAC PEs**.
`flow/tcl/tpu.tcl` already generates the physical shell (Verilog + DEF + LEF
at any N, buses derived from the datapath widths) and is a QoR-corpus row;
it lacks synthesizable PE RTL (a multiply-accumulate with the
weight/psum/activation pipeline — small).  N = 2, 4, 8, (16), each run three
ways.  64 PEs is ONE hardening.  Smoke vehicles needing no authoring:
`manual_macro_placement_test`, `salsa20`.

### 7.2 Three arms

| Arm | Synthesis | Blocks | Placement | Pins | Buses |
|---|---|---|---|---|---|
| **F** flat | `flatten` | none | GPL | `IOPlacement` | GRT |
| **H** hierarchical, no BUDA | `keep` → harden | per cell | `ManualMacroPlacement`, simple grid | per-block `IOPlacement` | GRT |
| **H+B** hierarchical with BUDA | `keep` → harden | per cell, BUDA-sized | BUDA `PlacementOptimizer` | BUDA `FP_DEF_TEMPLATE` | BUDA guides (A) |

H isolates what hierarchy alone costs; H+B minus H is BUDA's contribution.

### 7.3 Metrics — all from LibreLane's own `metrics.json`

Per arm and per N: wall-clock (total and per stage; H arms report block
hardening as wall time with parallelism AND as CPU-sum),
`design__die__area`, `design__instance__utilization`,
`timing__setup__ws`/`tns` (with the §2.4 caveat), `power__total` and its
breakdown, `route__wirelength`, `route__drc_errors` + signoff DRC, plus
BUDA's end-of-run triple for H+B.  Output: one table per N and a crossover
plot — runtime and each PPA metric versus N, three lines each.

### 7.4 Success criterion — proposed, to confirm before any benchmark run

* **H+B ≥ H on every PPA metric at every N.**  BUDA must never make
  hierarchy worse; this is the floor.
* **At the largest N where F completes, H+B is within 10 % of F on die area
  and within 0.5 ns of F's worst setup slack, at ≤ ½ of F's wall time.**
* **The crossover exists inside the sweep**: some N at which H+B beats F on
  wall time by ≥ 2× while meeting the bullet above.

A number chosen after seeing the data proves nothing; these are placeholders
to be argued about NOW, not after.

## 8. Recipes — macOS + Docker, in order

Everything below runs on macOS 15+ (Apple Silicon or Intel) with Docker
Desktop.  `--dockerized` makes LibreLane run inside
`ghcr.io/librelane/librelane:3.0.11` with your home directory, the PDK root
(`~/.ciel`) and the current directory mounted at the same paths, and prints
the exact `docker run` it uses; `phase0/measure/run_or.sh` mirrors that
command with `openroad` as the entrypoint.  The BUDA checkout is assumed at
`~/src/buda` (any path under your home works).

**0. Install, once.**

```bash
brew install make python python-tk
brew install --cask docker              # open Docker.app once; Settings › Resources: ≥ 4 CPUs, ≥ 8 GB
python3 -m pip install --upgrade "librelane==3.0.11"
python3 -m librelane --dockerized --smoke-test    # pulls the image + sky130A into ~/.ciel; ~10 min first time
```

Pass: the smoke test ends in `Flow complete`.

**1. The stock macro flow, as delivered** — learn the contract on LibreLane's
own two-macro test before touching ours.

```bash
git clone --depth 1 https://github.com/librelane/librelane-ci-designs.git ~/librelane-ci-designs
cd ~/librelane-ci-designs/manual_macro_placement_test
librelane --dockerized config.json
```

Pass: `runs/RUN_*/final/` exists, `Flow complete`.  Worth a look:
`runs/RUN_*/resolved.json` is every variable the run resolved — it is where
you read this PDK's `RT_MIN_LAYER`/`RT_MAX_LAYER`, `IO_PIN_H_LAYER`/
`IO_PIN_V_LAYER` and `TECH_LEFS`.

**2. Harden the block, LibreLane placing its pins.**

```bash
cd ~/src/buda/flow/librelane/phase0/reg32
librelane --dockerized --run-tag phase0 config.json
ls runs/phase0/final/gds runs/phase0/final/lef runs/phase0/final/nl runs/phase0/final/spef/*
```

Pass: `reg32.gds`, `reg32.lef`, `reg32.nl.v`, and `nom_/min_/max_`
SPEFs — the paths `two_reg32/config.json` names.

**3. Harden it again with a pin DEF template** — the block-side handoff.

```bash
python3 gen_pins_def.py > pins.def          # d[*] west, q[*] east, clk/rst south, on tracks
librelane --dockerized --run-tag phase0_pins config_pins.json
grep -A2 '^- d\[0\]' runs/phase0_pins/final/def/reg32.def
grep -A2 '^- d\[0\]' pins.def
```

Pass: the run completes (`Odb.ApplyDEFTemplate` reports the die bbox and
relocates 66 pins), and `d[0]`'s `+ PLACED ( 0 <y> )` in the final DEF is the
template's.  If the template is refused for an off-track or off-grid pin, the
generator's `--h-pitch/--h-offset/--v-pitch/--v-offset` take the PDK's real
values from the tech LEF; if `resolved.json` shows different pin layers, pass
`--h-layer/--v-layer`.

**4. The top with two hardened macros and a bus between them.**

```bash
cd ../two_reg32
librelane --dockerized --run-tag phase0 config.json
grep -c 'mid\[' runs/phase0/final/def/two_reg32.def
```

Pass: `Flow complete`, 32 `mid[*]` nets routed between `u0` and `u1`.  If
the PDN step cannot find the macros' power pins, add
`"PDN_MACRO_CONNECTIONS": ["u[01] VPWR VGND VPWR VGND"]` (the format is
`<instance_rx> <vdd_net> <gnd_net> <vdd_pin> <gnd_pin>`; the pin names are in
`../reg32/runs/phase0/final/lef/reg32.lef`).

**5. Measurement A — guides.**

```bash
cd ../measure && mkdir -p out
ODB=$(ls ../two_reg32/runs/phase0/*-openroad-cts/two_reg32.odb)
./run_or.sh ../two_reg32/runs/phase0 guide_ref.tcl  ODB=$ODB OUT=$PWD/out      # reference: route all, keep guides
python3 extract_bus_guides.py out/all.guide out/bus.guide                       # the bus's guides, as routed
./run_or.sh ../two_reg32/runs/phase0 guide_test.tcl ODB=$ODB OUT=$PWD/out      # withhold bus, read_guides, drt
python3 check_inside.py out/guided.def out/bus.guide
```

Pass: `nobus.guide` has no `mid[*]` entry, `merged.guide` has them and the
other nets' entries unchanged (`diff <(grep -v … ) …` if you want it exact),
`check_inside.py` exits 0 (`… 0 outside their guides`).  Then the sharper
form — a corridor the router did NOT choose:

```bash
python3 extract_bus_guides.py out/all.guide out/bus.guide --dy 3     # shift 3 µm inside the channel
./run_or.sh ../two_reg32/runs/phase0 guide_test.tcl ODB=$ODB OUT=$PWD/out
python3 check_inside.py out/guided.def out/bus.guide
```

Pass: still 0 outside.  That is the result that makes mechanism A the
phase-1 handoff.

**6. Measurement B — FIXED wires.**

```bash
python3 mark_fixed.py out/guided.def out/fixed.def                    # bus: + ROUTED → + FIXED
MACRO_LEF=$(cd ../reg32 && pwd)/runs/phase0/final/lef/reg32.lef
./run_or.sh ../two_reg32/runs/phase0 fixed_test.tcl DEF=$PWD/out/fixed.def MACRO_LEF=$MACRO_LEF OUT=$PWD/out
python3 compare_bus_wires.py out/fixed.def out/fixed_after.def
```

Pass: `32 bus net(s): 32 unchanged, 0 changed`.  If `global_route` objects
to the OTHER nets already carrying wiring, re-make the input with
`mark_fixed.py --strip-others` (the bus stays FIXED, everything else is
re-routed from scratch) and rerun.  A `CHANGED` result is the finding that
keeps FIXED pre-routes out of phase 1.

Keep `out/` — its files are the evidence the write-up cites.

## 9. Phases 1–3

**Phase 1 — plumbing.**  The NEW rows of §5: placement / size / pin-DEF /
guide writers, an `optimize_placement` command (or a Python driver), sky130
via `import_lef_tech`; one orchestrator (`tools/librelane_hier.py`: S0 →
BUDA → S1 in parallel → S2); proven end to end on the phase-0 toy and
`salsa20` with `check_design` clean and LibreLane signoff clean.

**Phase 2 — the benchmark.**  PE RTL for the systolic vehicle; the three arms
across N; the §7.3 table and crossover plot; a write-up that states the §2.1
trade as measured.

**Phase 3 — what the data says next.**  Per-pin timing budgets (§2.4) if the
timing column is the story; FIXED pre-routes (B) if measurement B passed and
guides left QoR on the table; `set_bottom_up` on the repeated cell so BUDA's
interface planning is solve-once too.

## 10. What BUDA gains regardless

The block-side writers turn BUDA from a planner whose output is a report
into one whose output is CONSTRAINTS a mainstream open flow consumes
unchanged — the advisory writers finished.  And a real synthesized design
through `import_verilog` is a reader vehicle of a kind the tree does not
have: every netlist here is either authored or uniquified.

## 11. Still open

1. The **success criterion** in §7.4 — confirm or replace the numbers.
2. **sky130A** unless told otherwise.
3. **Vehicle**: PE RTL for `tpu.tcl` (recommended), or an existing design?
