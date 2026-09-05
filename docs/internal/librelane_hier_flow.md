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

**Phase 0 ran on 2026-09-05 (macOS, Docker Desktop 4.12, LibreLane 3.0.11,
sky130A) and all five held** — the numbers are under each recipe in §8.
A: the router seats the bus inside a supplied corridor to within a gcell
(98.1 % of wire inside the as-routed corridor at 1 µm slack, 96.3 % inside
one shifted a gcell away that the router would not have chosen).  B: a
FIXED bus survives both routers byte-identical while the other nets route
around it.  What it cost to get there is the useful part: nine "bites",
every one a fact about the tool rather than a bug in the plan, and each is
now in the script or the recipe that needed it — the macro-vs-PDN phase
requirement of step 4 being the one that reaches into phase 1's design.

## 7. The benchmark

### 7.1 Vehicles — a ladder, not one design

No single open-source RTL has CPU + GPU + TPU + I/O integrated (surveyed
2026-09-05: Chipyard is the integrated CPU + accelerator + I/O platform;
Vortex is the open GPU, standalone; the ETH SystemVerilog family — Cheshire,
Ara, Snitch/Occamy — is the alternative without Chisel).  For the
CROSSOVER measurement, controllability beats realism: the size must be a
dial or the benchmark yields one point instead of a curve.  So:

| Tier | Vehicle | Why | Where |
|---|---|---|---|
| **1a** | the systolic array, `flow/tcl/tpu.tcl` at N | size is a dial; the cheapest iteration | `flow/librelane/tier1a/` |
| **1b** | a **Gemmini** mesh (Chipyard) at N = 4, 8, 16 | the same shape from somebody else's real RTL: `Mesh` → `Tile` → `PE`, repeated modules | `flow/librelane/tier1b/` |
| **2** | Chipyard **Rocket + Gemmini + peripherals** (`ChipTop`) | CPU + TPU + I/O on one chip, with SRAM macros | tier-1b recipe, `DESIGN_NAME: ChipTop` |
| **3**, optional | **Vortex**, multi-core | the only open GPU; repeated cores; standalone | — |

**Tier 1a is synthesizable now.**  `tpu.tcl -emit` writes `tpu_rtl.v` beside
`tpu.v`: the same modules, instances and widths, with a streaming-MAC
datapath inside every PE (`p_out <= p_in + a_in*w_in`, activation east,
weight south, all registered), registered feeders and weight buffers, and
accumulating output stages; `tpu.v` stays the byte-identical shell BUDA
plans against (the clock net alone would change every corpus count).
Synthesized here with Yosys against sky130_fd_sc_hd (`tt_025C_1v80`, the
liberty OpenROAD-flow-scripts ships) — **cell area, not P&R**: it sizes the
die and says which N a laptop flow can afford, and it is what a LibreLane
run will reproduce to within its own ABC script:

| N | PEs | cells | flops | cell area (µm²) | core at 45 % util (mm²) | synth (Yosys-WASM, 1 thread) |
|---|---|---|---|---|---|---|
| 2 | 4 | 2,660 | 274 | 21,534 | 0.05 | 3 s |
| 4 | 16 | 9,105 | 844 | 73,269 | 0.16 | 7 s |
| 8 | 64 | 34,194 | 2,968 | 273,116 | 0.61 | 26 s |
| 16 | 256 | 133,332 | 11,056 | 1,067,325 | 2.4 | 106 s |

Area grows 3.6–3.9× per doubling (the array is quadratic; the edges are
linear), and 64 PEs is ONE hardening in the H arms.  For scale: LibreLane's
own Sky130 tutorial pushes `TinyRocketConfig` "to minimize tool runtime", so
N = 8 is a comfortable laptop design and N = 16 is the size at which a flat
sky130 route becomes an hours-long run — exactly the range a crossover would
have to sit in.

Smoke vehicles needing no authoring: `manual_macro_placement_test`,
`salsa20`.

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
command with `openroad` as the entrypoint, reading the layer names and LEF
paths from the run's `resolved.json` (`read_resolved.py`, which matches the
default corner against LibreLane's wildcard-keyed `TECH_LEFS` the way its
own steps do).  The BUDA checkout is assumed at
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

Pass: `reg32.gds`, `reg32.lef`, `reg32.nl.v`, and `nom/min/max`
SPEFs — the paths `two_reg32/config.json` names.

**3. Harden it again with a pin DEF template** — the block-side handoff.

```bash
python3 gen_pins_def.py > pins.def          # d[*] west, q[*] east, clk/rst south, on tracks
librelane --dockerized --run-tag phase0_pins config_pins.json
grep -A3 -E '^\s*- d\[0\] ' runs/phase0_pins/final/def/reg32.def
grep -A3 -E '^\s*- d\[0\] ' pins.def
```

Pass: the run completes (`Odb.ApplyDEFTemplate` relocates 66 pins, warning
only that VPWR/VGND are power pins it ignores), and `d[0]`'s ABSOLUTE
rectangle — `LAYER` offsets added to the `PLACED` point — is the template's.
Compare rectangles, not origins: OpenROAD writes every pin back with its
origin at the rect's CENTRE, so the template's `LAYER met3 ( 0 -150 ) ( 2000
150 ) + PLACED ( 0 8500 )` comes out as `LAYER met3 ( -1000 -150 ) ( 1000
150 ) + PLACED ( 1000 8500 )` — the same 2 x 0.3 um of metal, and a check
on the `PLACED` point alone reports all 66 pins moved (measured 2026-09-05:
66 of 66 rectangles identical, 0 of 66 origins).  Phase 1's pin writer and
any verifier of it must read the geometry the same way.  If the template is
refused for an off-track or off-grid pin, the generator's `--h-pitch/--h-offset/--v-pitch/--v-offset` take the PDK's real
values from the tech LEF; if `resolved.json` shows different pin layers, pass
`--h-layer/--v-layer`.

**4. The top with two hardened macros and a bus between them.**

```bash
cd ../two_reg32
librelane --dockerized --run-tag phase0 config.json
grep -c 'mid\[' runs/phase0/final/def/two_reg32.def
```

Pass: `Flow complete`, `All shapes on net VPWR are connected` (and VGND),
32 `mid[*]` nets routed between `u0` and `u1` (measured 2026-09-05: 100 std
cells, DRC/LVS/antenna 0, IR drop worst 0.12 mV).  The first draft of this
toy did NOT pass, and the way it failed is a phase-1 requirement, so it is
kept here.  `OpenROAD.IRDropReport` stopped the run with `[PSM-0069] Check
connectivity failed on VPWR` — after routing, which was already clean — and
the unconnected shapes were `u0`'s OWN power pins while `u1`'s were fine,
same cell, same y.  `PDN_MACRO_CONNECTIONS` (the guess this paragraph used
to make) is not it: both macro grids were inserted.  The cause is the
macro's x-PHASE against the top's strap grid.  The core met4 straps sit at
x = core origin + `PDN_VOFFSET` + k·`PDN_VPITCH` (5.52 + 30k); with `u0` at
x = 20 its VGND met4 pin (cell-local 13.22–15.22) lands at 33.22–35.22, under
the VPWR strap at 34.72–36.32, and pdngen CLIPS the strap to above the
macro rather than short the two nets — so the strap never reaches the met5
pins it was to feed, and the one surviving strap over `u0` (95.52) misses
the macro's met5 pin, which ends at 94.06, by 0.66 µm.  `u1` at x = 160 has
the same pins at 169.52/173.22, clear of the 185.52 strap.  A second
failure mode hid behind it: a die that leaves a sliver of standard-cell
rows outside a macro halo (9 sites at 250.24–254.38 on the first 260-wide
die) whose rails no strap crosses.  Both are geometry.  The toy now places
`u0` at x = 10 (10 ≡ 160 mod 30, so both macros see the straps in the same
phase) on a 250-wide die (each halo reaches its die edge, so every standard
cell sits in the channel).  For phase 1 this means BUDA's block placer must
know the top's PDN grid — pitch, offset and the macro's own pin pattern —
and either place each macro at a phase its pins clear, or derive
`PDN_VOFFSET` from the placement; an x that is legal for placement can still
be one no PDN connects, and nothing before signoff says so.

**5. Measurement A — guides.**

```bash
cd ../measure && mkdir -p out
ODB=$(ls ../two_reg32/runs/phase0/*-openroad-cts/two_reg32.odb)
./run_or.sh ../two_reg32/runs/phase0 guide_ref.tcl  ODB=$ODB OUT=$PWD/out      # reference: route all, keep guides
python3 extract_bus_guides.py out/all.guide out/bus.guide                       # the bus's guides, as routed
./run_or.sh ../two_reg32/runs/phase0 guide_test.tcl ODB=$ODB OUT=$PWD/out      # withhold bus, read_guides, drt
python3 check_inside.py out/guided.def out/bus.guide --slack 1.0
```

Pass: `guide_test.tcl` prints `A: 32 bus net(s), 101 other net(s)`,
`nobus.guide` has no `mid[*]` entry, `merged.guide` has all 133, detailed
routing ends at `Number of violations = 0`, and `check_inside.py` reports
the bus wire inside its guides.  Measured 2026-09-05: 280 segments, 6108 µm
of bus wire, **98.1 % inside its own layer's boxes at 1 µm slack** (1.2 %
on another layer inside the corridor's xy footprint, 0.7 % outside it; at a
whole gcell of slack, 6.9 µm, 0.2 % outside).  The exits are gcell-edge
overshoots and pin-access legs, never a run leaving the corridor.

Three things the first attempt got wrong, each now baked into the scripts:
the database spells the bus nets DEF-escaped (`mid\[0\]`, backslashes
included), `write_guides` spells them the same way, and `set_nets_to_route`
matches either that or the plain `mid[0]` — but NOT the doubled backslashes
a Tcl list gives such a string, and a call that matches nothing routes
EVERYTHING silently, so `guide_test.tcl` finds and passes the bus by its
plain name; `read_guides` REPLACES the guide set rather than adding to it
(after it, `write_guides` held only the bus and DRT routed only the bus), so
the merge is done in the file; and a guide is a set of GCELLS — 6.9 µm here,
the `GCELLGRID` DRT prints — so every box the scripts write is gcell-aligned
(`[ERROR DRT-0229] genGuides_split split_indices is empty` on anything
else).  Then the sharper form — a corridor the router did NOT choose:

```bash
python3 extract_bus_guides.py out/all.guide out/bus.guide --dy 6.9   # shift the channel one gcell
./run_or.sh ../two_reg32/runs/phase0 guide_test.tcl ODB=$ODB OUT=$PWD/out
python3 check_inside.py out/guided.def out/bus.guide --slack 1.0
```

Pass: still inside.  Measured: 390 segments, 6350 µm, **96.3 % inside the
SHIFTED corridor** (2.4 % layer change, 1.3 % outside; 0.2 % beyond one
gcell) — while checked against the AS-ROUTED guides the same wire is 21.2 %
outside.  It went where the guide said, not where the router would have
gone.  That is the result that makes mechanism A the phase-1 handoff.  Only
the CHANNEL moves — the part of each box between the macros (`--channel 90
160` µm by default, snapped inward to the gcell grid: 96.6..158.7) is CLIPPED
out and shifted, the metal over the macros stays, since the pins do not
move; each cut is bridged by a RISER on the vertical layer next to the cut
box's layer, two gcell columns wide, because adjacent-layer guides connect
only where they SHARE a gcell (a riser that merely abutted its pieces gave
`DRT-0218 Guide is not connected to design`).  `--dy` must be a whole
number of gcells — a box moved by less still overlaps the gcell it came
from, so the router may stay and the check cannot tell following from
agreeing — and `extract_bus_guides.py` refuses anything else.
`check_inside.py` checks every SEGMENT along its length on its own layer,
says which kind of miss each is (another layer inside the corridor, or
outside it), weighs both by wire length, and counts a bus bit with no
wiring as a failure, never a pass.

**6. Measurement B — FIXED wires.**

```bash
python3 mark_fixed.py out/guided.def out/fixed.def --strip-others         # bus: + ROUTED → + FIXED; others: unrouted
MACRO_LEF=$(cd ../reg32 && pwd)/runs/phase0/final/lef/reg32.lef
./run_or.sh ../two_reg32/runs/phase0 fixed_test.tcl DEF=$PWD/out/fixed.def MACRO_LEF=$MACRO_LEF OUT=$PWD/out
python3 compare_bus_wires.py out/fixed.def out/fixed_after.def
```

Pass: `Routed nets: 101` from global routing (the 32 FIXED nets skipped),
`Number of violations = 0` from detailed routing, and `32 bus net(s): 32
unchanged, 0 changed`.  Measured 2026-09-05: exactly that — the routers
route the other 101 nets around a FIXED bus and leave its wiring
byte-identical.  `--strip-others` is not optional in practice: with the
other nets still carrying `+ ROUTED` wiring, `global_route` routed 0 nets
and `detailed_route` re-derived guides from the existing wires, so the
"unchanged" verdict would have measured a session that re-routed nothing.
`mark_fixed.py` refuses a bus bit that had no wiring to mark, so an
unrouted bit cannot come out of the comparison as "unchanged".  A `CHANGED`
result is the finding that keeps FIXED pre-routes out of phase 1.

Keep `out/` — its files are the evidence the write-up cites.

**7. Tier 1a — the array, flat, at N.**  One directory per N; the DEF and
LEF the H arms will use are written beside the RTL.

```bash
cd ~/src/buda
for N in 2 4 8; do flow/librelane/tier1a/gen.sh $N; done        # + 16 when the small ones are in
cd flow/librelane/tier1a/n4 && librelane --dockerized --run-tag flat config.json
python3 ../runtimes.py runs/flat                # per-stage seconds + area/timing/power/WL/DRC
python3 ../runtimes.py runs/flat --json >> ../results.jsonl     # one row per run, for the table
```

Pass: `Flow complete`; `runtimes.py` prints the stage split and the metrics
row.  The numbers to keep per N: the stage seconds, `design__die__area`,
`timing__setup__ws`, `power__total`, `route__wirelength`, DRC count.  `N=16`
last — it is the run that tells you the laptop's ceiling.

**8. Tier 1b — a Gemmini mesh at N = 4, 8, 16.**  Chipyard needs Linux; on
the Mac that is a Linux container with the BUDA checkout mounted.  The full
recipe, with the two places it is guessing, is
[`flow/librelane/tier1b/README.md`](../../flow/librelane/tier1b/README.md):
drop `BudaGemminiConfigs.scala` into Chipyard, `make verilog` per N, then
LibreLane on the generated `Mesh` with `mesh_config.json`, and the same
`runtimes.py` on each run so the 1a and 1b rows land in one table.

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
3. ~~Vehicle~~ — decided: the ladder in §7.1, tiers 1a and 1b first, both
   for concrete runtime numbers; Chisel is acceptable.  Open: the tier-2
   config size, once the 1b numbers say what the laptop affords.
