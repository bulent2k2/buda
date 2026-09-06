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
| Block pins from the plan, written | `FP_DEF_TEMPLATE` — the same file, from BUDA | **`emit_pin_def <file.def> <block-or-cell> [unrouted <edge> [<layer>]] [depth <um>] [grid <dbu>] [lef <file>]`** (`buda_session/pin_def.py`): after `run_detailed_nuts`, one pin per net bit where its bit-wire meets the block face, on the bit-wire's layer (a track by construction), rectangle SYMMETRIC about the PLACED point, PLAIN names (odb reads an escaped `d\[16\]` back as `d[16\]` and matches nothing; `escaped_names` opts in), UNITS from `lu_per_um`; a cell in a hier session is a TEMPLATE (every instance must agree in cell-local coordinates, `N` only for now); nets on no bus are spread on one edge's tracks; a pin is on the BLOCK's track grid, so an instance origin off the pin layer's track period is REFUSED with the residue and the clearing shift (`snap` moves each pin to the nearest block-frame track and reports the largest, BUDA-1713); verifier `tools/pin_def_verify.py` (absolute rectangles, never origins). Vehicle: `phase0/two_reg32/pins.buda` (§8 step 3b) |
| Bus corridors | `read_guides` after `set_nets_to_route` | **EXISTS**: `emit_guides <file.guide>` writes the OpenROAD guide file — gcells from the DEF's `GCELLGRID`, the floor-at-both-ends junction rule, DEF-escaped names, pin-access strips on the `terminal` layers (§8 step 5b; the phase-0 lessons of step 5 are the rules it is built from) |
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

**Measured — arm F, this machine** (2026-09-05, recipe 7 as written: Intel
Mac, Docker Desktop 4.89 at 8 CPUs / 8 GB, LibreLane 3.0.11, sky130A,
`FP_CORE_UTIL 40`, 20 ns clock; rows in `flow/librelane/tier1a/results.jsonl`,
one run each, an otherwise idle box):

| N | wall | synth | fp+place | CTS | route | signoff | std cells (comb / flops) | die (mm²) | util | setup WS (ns) | WL (mm) | DRC |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2 | 8.8 min | 25 s | 87 s | 79 s | 166 s | 172 s | 4,410 (2,660 / 274) | 0.087 | 46.7 % | +4.65 | 64 | 0 |
| 4 | 20.2 min | 34 s | 118 s | 77 s | 482 s | 500 s | 14,997 (9,153 / 860) | 0.280 | 46.5 % | +1.81 | 227 | 0 |
| 8 | 75.7 min | 123 s | 338 s | 252 s | 1,754 s | 2,074 s | 56,471 (34,375 / 3,000) | 1.032 | 46.3 % | **−0.55** | 935 | 0 |

The synthesis table above is confirmed by the runs (2,660 / 9,153 / 34,375
combinational cells against its 2,660 / 9,105 / 34,194; the flop counts
grow by the hold buffers), and its cell area maps onto LibreLane's std-cell
area by ~1.7× (taps and timing-repair buffers).  The factors per doubling
are NOT constant: wall 2.3× then **3.7×**, route 2.9× then 3.6×, signoff
2.9× then 4.2×, wire 3.5× then 4.1× — the flat run turns superlinear
between N = 4 and N = 8, and signoff (RCX, STA over nine corners, KLayout
DRC, LVS) is the largest stage at N = 8, not routing.  Read forward, N = 16
is a 5–6 h flat run on this box (~200 k std cells, ~4 mm²).  And N = 8 is
the first size that MISSES timing at 20 ns (−0.55 ns WS, −1.44 ns TNS,
every DRC clean) — the flat arm's own timing wall, before any hierarchy is
involved, which is the baseline the H arms' timing column will be read
against (§2.4).  Every run left ~3 GB of run directory at N = 8 (1 GB at 4);
`runs/` is git-ignored.

Smoke vehicles needing no authoring: `manual_macro_placement_test`,
`salsa20`.

### 7.2 Three arms

| Arm | Synthesis | Blocks | Placement | Pins | Buses |
|---|---|---|---|---|---|
| **F** flat | `flatten` | none | GPL | `IOPlacement` | GRT |
| **H** hierarchical, no BUDA | the leaf modules the RTL declares, each hardened from its own module text; the top `flatten`ed over the hardened netlists as black boxes | one per leaf cell type (`pe_cell`, `feed_cell`, `wbuf_cell`, `acc_cell`), die = the emitter's LEF `SIZE` | `ManualMacroPlacement` at the emitter's DEF locations (the whole placement translated once to fit the die), PDN pitch/offset derived from the array pitch | per-block `IOPlacement` | GRT |
| **H+B** hierarchical with BUDA | `keep` → harden | per cell, BUDA-sized | BUDA `PlacementOptimizer` | BUDA `FP_DEF_TEMPLATE` | BUDA guides (A) |

H isolates what hierarchy alone costs; H+B minus H is BUDA's contribution.
Arm H is what `flow/librelane/tier1a/harm.sh N` writes (§8 steps 7a–7d),
and it differs from the first draft's row in two ways that are the point:
the partition is not a `SYNTH_KEEP_HIERARCHY_MIN_COST` threshold but the
four leaf modules of `tpu_rtl.v`, cut out of the file one per block run
(`row_cell` stays soft), and the block placement is not a "simple grid" but
the emitter's own DEF — the same placement the H+B arm starts from — so
the two hierarchical arms differ only in what BUDA adds (sizes, pins,
corridors), never in where the macros sit.  The one thing H decides that
the DEF does not say is the top's PDN phase (§8 step 4's lesson).  pdngen
never SHORTS a strap to a macro's power pin — it CUTS the strap
(`Shape::cut` spares a same-net obstruction only when the strap CONTAINS the
pin across its width, which a 2 µm block pin in a 1.6 µm top strap never
is) — so a same-layer meeting is a clip, and what FEEDS a macro is the
cross-layer crossing the macro grid vias (`add_pdn_connect {met4 met5}`): a
top met4 strap over the block's met5 pin.  `harm.sh` picks `PDN_VPITCH` as
a divisor of the PE column pitch and `PDN_VOFFSET` so that every macro of a
cell sees the straps at one phase, clear of the met4 pins its block config
PREDICTS, crossing its met5 pins, and with a VPWR+VGND pair in every
standard-cell row fragment the halos leave; `PDN_HOFFSET` keeps the met5
straps off every macro's met5 pins, ordered by distance from the macro
boxes so they land mid-channel.  `pdn_phase.py` verifies the prediction on
the hardened LEFs before the top runs.

The step-7c run added the rule that governs OBSTRUCTION, which is the other
thing that removes a strap.  The `lef` view a top reads
(`final/lef/<cell>.lef`) is MAGIC's — OpenROAD's `-bloat_occupied_layers`
abstract LEF is the separate `<cell>.openroad.lef`, which nothing here
reads — so the OBS is the block's ACTUAL metal, and whose metal it is
decides everything.  `pe_cell` routes on met4 (`RT_MAX_LAYER met4`) and its
LEF carries a met4 OBS, but that OBS *is* the block's own PDN, the same
rectangles as its power pins, so the phase search cleared it by clearing
them.  The toy's met4 OBS in §9's step 5b was SIGNAL routing pushed onto
met4 by a pin layout, sitting wherever the router put it — and that made
pdngen drop straps and fail IR-drop signoff.  So the rule is not "cap the
block below the top's PDN layers": what must hold is that the block's use
of those layers is PREDICTABLE and cleared by the phase search.  Capping at
`RT_MAX_LAYER met3` guarantees it (+0.9 % block wire on the toy); leaving
met4 to the block works only while the block's met4 is its own grid.
`pdn_phase.py` classifies every OBS rectangle as the block's own power
metal or as foreign, and reports the foreign case before the top runs,
which is the check nothing in the flow had.

### 7.3 Metrics — all from LibreLane's own `metrics.json`

Per arm and per N: wall-clock (total and per stage; H arms report block
hardening as wall time with parallelism AND as CPU-sum),
`design__die__area`, `design__instance__utilization`,
`timing__setup__ws`/`tns` (with the §2.4 caveat), `power__total` and its
breakdown, `route__wirelength`, `route__drc_errors` + signoff DRC, plus
BUDA's end-of-run triple for H+B.  Output: one table per N and a crossover
plot — runtime and each PPA metric versus N, three lines each.

An H arm's wirelength is the top's PLUS every block's, counted once per
placed instance (a cell hardened once and placed eight times has eight
times the wire on silicon), and the block-internal part is its own column
rather than folded into the total.  That is where the block-side handoff
is paid for: on the phase-0 block the pin template that straightens the
top-level bus costs the block **+60 % of its own wire** (`reg32`, 3937 →
6303 µm, LibreLane's placer vs d-west/q-east, measured 2026-09-05 — §8
steps 2–3), and H+B minus H on the arm total alone would net that against
the bus it buys without saying which side moved.  `tier1a/runtimes.py
<top_run> --block <block_run>[:<instances>] …` writes the row that way.

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

**3b. The same handoff, with BUDA writing the template.**  Steps 2 and 4
first: the template comes from the top's PLAN, so the top must exist as
placed and the block as hardened.  Then, from the BUDA checkout:

```bash
cd ~/src/buda/flow/librelane/phase0/two_reg32
python3 prep_pins.py                       # two_reg32_fp.def + reg32_macro.lef + sky130_tech.lef, from the runs
cd ~/src/buda && bin/buda --no-viz flow/librelane/phase0/two_reg32/pins.buda
cd flow/librelane/phase0/reg32
librelane --dockerized --run-tag phase0_buda_pins config_buda_pins.json
python3 ~/src/buda/tools/pin_def_verify.py ../two_reg32/reg32_pins.def runs/phase0_buda_pins/final/def/reg32.def
```

Pass, in order: `prep_pins: ok` (2 reg32 instances, 32 `mid` nets, TRACKS
and `GCELLGRID` kept — the same staged DEF `buda_route.buda` reads; the DEF
is the final one reduced to its floorplan — the std cells have no LEF here
and the port nets would give u0's `d` and u1's `d` two different answers,
which is what `emit_pin_def` refuses on a cell; the reason each is dropped
is in the script's docstring); the flow's
`check_design dnuts` clean and its last line `[PinDEF] reg32_pins.def: 66
pin(s) for reg32 — 64 from the plan (detailed; E 32, W 32), 2 spread on
edge S on met2`, which is the hand template's layout — `d` west, `q` east,
`clk`/`rst` south, every pin on a track — with the y of each `d[i]`/`q[i]`
now the y the top-level bus was ROUTED at, so the bus between the two
macros is straight by construction (`u0.q[i]` and `u1.d[i]` are the same
bit-wire, and the template has one local y for both); the hardening run
completing with `Odb.ApplyDEFTemplate` relocating 66 pins; and the
verifier's `PASS: 66 of 66 template pin(s) appear in the final DEF with an
identical absolute rectangle`.  A `MISMATCH:` names the pin and both
rectangles; `REFUSED:` means the final DEF had no `PINS` section, which is
not a pass.  If `Odb.ApplyDEFTemplate` refuses a NAME rather than a
position, it is the DEF spelling, and the first real run SETTLED it: the
template writes the PLAIN `d[0]` under `BUSBITCHARS "[]"`, as the hand
template did.  The escaped spelling — what OpenROAD WRITES, and what the
routed DEF's nets are called — does not survive being read back: odb takes
`d\[16\]` as the name `d[16\]` (it consumes the leading escape and keeps
the trailing one), so `Odb.ApplyDEFTemplate` reported all 66 pins "not found
in design layout" and exited 2.  `escaped_names` writes the other spelling
for the day a tool wants it; nothing here does.

**THE BLOCK'S TRACKS ARE NOT THE TOP'S, and the flow moves the macros for
it.**  A planned pin sits where the top's bit-wire meets the face, on one
of the TOP's tracks; the block is hardened in its own run with tracks at
`OFFSET + k*PITCH` from ITS OWN origin.  The two grids coincide only when
the instance origin is a whole number of track periods on each pin layer,
and LibreLane's (10, 20) µm is not: 0.34 µm past a met2 period in x and
0.28 past a met3 one in y, which lands `d[0]` 0.40 µm off the block's met3
track and `clk` 0.12 off met2 (measured on this toy).  `emit_pin_def`
refuses that outright, naming each instance's residue and the smallest
clearing shift, so `pins.buda` moves the macros onto the period first
(`move_comp u0 9200 20400` / `u1 160080 20400` — multiples of 0.46 in x and
0.68 in y that keep both clear of the 30 µm PDN straps, the step-4 phase
requirement), and step 4's `config.json` takes the same two locations for
the re-run that hardens against this template.  `snap` is the fallback when
a placement cannot move: each pin goes to the nearest block-frame track and
BUDA-1713 reports the largest shift — metal the top's router then has to
jog to reach, which is a cost, not a fix.

**A BUS'S BIT PITCH MUST LEAVE THE ROUTER ITS ADJUSTMENT MARGIN.**  The
first end-to-end hardening against a BUDA template put the 32 bits on 32
CONSECUTIVE met3 tracks — the densest plan there is, and the one the global
router cannot work with: `GRT_LAYER_ADJUSTMENTS` holds a fraction of every
layer's tracks in reserve, and a face with none left ends the run in
`GRT-0116` overflow (measured: **69** overflow, at 5789 µm).  The fix
belongs in the PLAN, not the writer — the writer puts a pin where the bit
was routed, so the pitch is the router's own question — and it is one line
in `pins.buda`:

```
def_track_pattern 3 190 SIGNAL 300 380 CUSTOM 300 380
```

one SIGNAL slot then one the router keeps, so the bus takes every SECOND
met3 track.  `buda_route.buda` needs the same declaration or its guide rows
sit between the pins.  **The origin is a slot START, not a track centre**:
`def_track_pattern` anchors the first slot's low edge and the track is its
centre, so a centre at c needs `origin = c - width/2` — exactly what the DEF
importer computes for itself (`_apply_def_tracks`).  sky130's met3 tracks are
at 340 + 680k DBU, so **190** puts the signal centres on 340 + 1360k (every
second PDK track) while **340** puts them on 490 + 1360k — 150 DBU off every
one of them, half the wire width.  The measured ladder below was taken with
340; the vehicle now declares 190 and the number wants re-measuring.

**The numbers to record** are reg32's own `route__wirelength`
(`runs/<tag>/final/metrics.json`) across the three ways to place its pins,
measured 2026-09-06 on the phase-0 toy:

| pins placed by | reg32 `route__wirelength` | vs the free placer | outcome |
|---|---|---|---|
| LibreLane's own `IOPlacement` | 3937 µm | — | the block alone, no bus to serve |
| the hand template (`gen_pins_def.py`) | 6358 µm | +62 % | clean (§7.3's +60 % is the same run at 6303 µm, before `RT_MAX_LAYER met3`) |
| BUDA, every met3 track | 5789 µm | +47 % | **refused**: GRT-0116, 69 overflow |
| BUDA, every SECOND met3 track | **4966 µm** | **+26 %** | clean but for 4 residual overflow at the die margin |

So the planned template costs the block **+26 %** where the hand one costs
+60-62 %, and that is the §7.3 trade measured rather than assumed: BUDA lands
`d` and `q` on the y the top's bus was ROUTED at, which is a better row for
the block's own logic than the convention the hand template followed.  The
verifier reports `PASS: 66 of 66` on the hardened result, and at the top the
§5b containment falls from **30.4 % to 0.4 %**.  The 4 residual overflow are
at the die margin and are the next thing to chase — the off-track 340 origin
above is a candidate cause, since a pin 150 DBU off the grid is one the
router must jog to reach.

`config_buda_pins.json` differs from `config_pins.json` only in where
`FP_DEF_TEMPLATE` points, so the two hardenings are comparable.  **Both
carry `RT_MAX_LAYER met3`**, and that is not tidiness — a block allowed to
route on the top's PDN layers hands the top a met4 `OBS`, pdngen drops every
strap crossing it, and every macro power pin comes out unconnected (measured
at step 5b; §9 carries the rule).

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
python3 check_inside.py out/guided.def out/bus.guide --slack 1.0 --max-outside-pct 5
```

Pass: `guide_test.tcl` prints `A: 32 bus net(s), 101 other net(s)`,
`nobus.guide` has no `mid[*]` entry, `merged.guide` has all 133, detailed
routing ends at `Number of violations = 0`, and `check_inside.py` exits 0
with a `PASS:` line.  Measured 2026-09-05: 280 segments, 6108 µm of bus
wire, **98.1 % inside its own layer's boxes at 1 µm slack** (1.2 % on
another layer inside the corridor's xy footprint, 0.7 % outside it; at a
whole gcell of slack, 6.9 µm, 0.2 % outside).  The exits are gcell-edge
overshoots and pin-access legs, never a run leaving the corridor.  The
threshold is what makes the exit code the verdict: without
`--max-outside-pct` any miss exits 1, which is the right rule for a
synthetic DEF and the wrong one for a routed design, where the first run
exited 1 on the result this paragraph calls a pass — a script no harness
could gate on.  5 % is chosen ABOVE both measured numbers (1.9 % and 3.7 %
below) and well under the 21.2 % of a corridor the wire did not follow, so
it separates following from not following; an unrouted bit fails at any
threshold.

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
python3 check_inside.py out/guided.def out/bus.guide --slack 1.0 --max-outside-pct 5
```

Pass: still exit 0.  Measured: 390 segments, 6350 µm, **96.3 % inside the
SHIFTED corridor** (2.4 % layer change, 1.3 % outside; 0.2 % beyond one
gcell) — while checked against the AS-ROUTED guides the same wire is 21.2 %
outside (run the check against `out/all.guide`'s bus entries to see the
same script FAIL on it).  It went where the guide said, not where the router would have
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

**5b. Measurement A with BUDA's guides** — phase 1's first closed loop.
Step 5 proved the router follows a guide; the guides were the router's own.
This step routes the bus in BUDA and hands the router BUDA's guide file.
The block is the TEMPLATE-hardened one (step 3): with LibreLane's own pin
placement 0 of 32 bits have `u0.q` on the east face AND `u1.d` on the
west, so no corridor between the blocks' facing edges can reach them —
measured first (53 % of the bus wire outside BUDA's corridor, `q[0]` on
u0's WEST face), which is the H+B premise stated the other way round: the
pin template is what makes BUDA's corridor reachable at all.

```bash
cd flow/librelane/phase0/two_reg32
librelane --dockerized --run-tag phase0_pins config_pins.json               # the top against the TEMPLATE block
mkdir -p out
cp runs/phase0_pins/*-odb-manualmacroplacement/two_reg32.def out/placed.def  # u0/u1 FIXED, std cells unplaced
ln -sf $PDK_ROOT/sky130A/libs.ref/sky130_fd_sc_hd/techlef/sky130_fd_sc_hd__nom.tlef out/tech.tlef
ln -sf $PWD/../reg32/runs/phase0_pins/final/lef/reg32.lef out/block.lef      # the block BUDA routes between
../../../../bin/buda buda_route.buda --no-viz                                 # -> out/buda_bus.guide
cd ../measure
ODB=$(ls ../two_reg32/runs/phase0_pins/*-openroad-cts/two_reg32.odb)
./run_or.sh ../two_reg32/runs/phase0_pins guide_ref.tcl ODB=$ODB OUT=$PWD/out   # the ROUTER's corridor on this run
python3 extract_bus_guides.py out/all.guide out/all_bus.guide                # …kept as the control
python3 extract_bus_guides.py ../two_reg32/out/buda_bus.guide out/bus.guide  # the bus entries, as BUDA wrote them
./run_or.sh ../two_reg32/runs/phase0_pins guide_test.tcl ODB=$ODB OUT=$PWD/out
python3 check_inside.py out/guided.def out/bus.guide --slack 1.0 --max-corridor-outside-pct 10             # the verdict
python3 check_inside.py out/guided.def out/all_bus.guide --slack 1.0 --max-corridor-outside-pct 10         # control: the ROUTER's corridor
python3 check_inside.py out/guided.def out/bus.guide --slack 1.0 --max-outside-pct 5                       # strict: fails until the pin writer exists
```

Pass: `buda_route.buda` ends with `check_design dnuts` clean and
`emit_guides` reporting every via in a gcell its net holds on both layers;
`guide_test.tcl` reaches `Number of violations = 0`; the first
`check_inside.py` exits 0 — the wire stayed inside BUDA's corridor by the
CORRIDOR measure (`--max-corridor-outside-pct`: outside the guide's
footprint on every layer; a one-point path outside it, a via with no
wire, fails at any threshold) — and the control against the router's own
corridor FAILS, which is the evidence that the router followed BUDA
rather than agreeing with it.  The third line is the STRICT measure of
step 5 (`--max-outside-pct`: outside the net's own-layer boxes), which a
single-layer corridor plan cannot meet while the router changes layers
inside it to reach pins whose rows the guide did not set.

**Today neither verdict passes, for one known reason, and the recipe says
so rather than lowering the bar** (next paragraph): the corridor verdict
fails on 15 vias outside the corridor — every one of them at a jog
between a bit's BUDA row and its pin row — with 7.7 % of the wire length
outside, and the strict verdict fails at 30.4 %.  What the run DOES
establish is the comparison: against the router's own corridor the same
wire is 58.1 % outside.  The pass above is the criterion the pin-DEF
writer (§9) has to meet; the three numbers to record are step 5's
(segments, µm, % outside) for both guide files.

**Measured 2026-09-06.**  BUDA: 1 bundle, 32 bits on one met3 segment, 0
unplaced, 2,240 µm; 32 guide entries, per-bit rows, terminal strips on 21.
Detailed routing under them: 0 violations.  The bus, 3,105 µm: **7.7 %
outside BUDA's corridor, 22.7 % on another layer inside it** — against the
router's own corridor the same wire is **58.1 % outside**, so the router
went where BUDA said (92 % inside by length) and not where it would have
gone.  The corridor verdict still FAILS — on 15 lone vias outside the
footprint, 11 below and 4 above their bit's BUDA row, all within x = 77–157
µm, i.e. at the channel ends where the jogs are — and the strict one at
30.4 %; the failing share is
81 % vertical wire on met2/met4: jogs between the row BUDA gave a
bit and the row its pins sit on.  BUDA packed the 32 bits into y 48–76 µm
of the channel; the template put the pins at y 28–71 µm (every second
track from track 12, by hand), and only 11 of 32 bits have their pin
inside their BUDA row — the worst is 31 µm off.  That is the one direction
this loop does not close yet: the pins were not written from BUDA's route,
so BUDA's rows and the pins disagree and the router pays the difference.
Phase 1's pin-DEF writer (§5) is exactly that direction — pins placed on
the rows BUDA's bits land on — after which the jogs, and the "another
layer inside the corridor" share, should vanish; until then the threshold
measures the hand template, not the guide writer.

Three things bit on the way, all now in the files.  (1) `import_lef_tech`
refused sky130's tech LEF: its `PROPERTYDEFINITIONS` block holds `LAYER
LEF58_TYPE STRING ;`, which the reader took for a LAYER block, and `END
PROPERTYDEFINITIONS` then read as a mismatched END — the first real tech
LEF the command met (fixed in `lef_io.cpp`, pinned).  (2) A DEF written
before global routing carries no `GCELLGRID` (OpenROAD defines the grid at
`global_route`; the first DEF here with one is `39-openroad-globalrouting`'s),
so `buda_route.buda` passes `gcell 6.9` — the router's `GCELLGRID STEP` —
rather than reading it off `placed.def`.  (3) **The template-hardened block
failed the top's PDN check with EVERY macro pin unconnected** (`PSM-0069`),
where the own-placer block at the same placement had passed: the +60 %
internal wire of the d-west/q-east layout put some of the block's routing
on met4, its LEF then carried a met4 `OBS`, and pdngen drops any core met4
strap that would cross an obstruction — so no strap ever reached the
macros' met5 pins.  The block is hardened with `RT_MAX_LAYER met3` now
(both reg32 configs): met4/met5 are the top's PDN and routing layers and
not the block's to use — the same idea as BUDA's `reserve_top_layers`,
and a rule for phase 1's block-config writer (§9).  It cost the block
+0.9 % wire (6,303 → 6,358 µm), nothing else, and every template pin
stayed put.

What `buda_route.buda` does and why is written in the file; the guide
writer's rules are §8 step 5's four lessons, each now enforced in
`emit_guides` and pinned by `test/tests/test_emit_guide_file.py` with the
phase-0 measure scripts as the reader.

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
python3 ../runtimes.py runs/flat --set N=4 --set arm=F        # per-stage seconds + area/timing/power/WL/DRC
python3 ../runtimes.py runs/flat --set N=4 --set arm=F --json >> ../results.jsonl   # one row per run, saying which
```

Pass: `Flow complete`; `runtimes.py` prints the stage split and the metrics
row.  The numbers to keep per N: the stage seconds, `design__die__area`,
`timing__setup__ws`, `power__total`, `route__wirelength`, DRC count.  Done
for N = 2, 4, 8 on 2026-09-05 (8.8 / 20.2 / 75.7 min, all DRC-clean; the
table and the reading are in §7.1) — run the three SEQUENTIALLY on an idle
box, since the stage seconds are the point and a second run on the same
cores is a confound.  `N=16` last — read forward from the three, it is a
5–6 h run here.

**7a. Tier 1a — arm H, the blocks.**  `harm.sh N` reads the set step 7
emitted and writes `n<N>/h/`: one hardening directory per leaf cell (its
module cut out of `tpu_rtl.v`, a fixed die of exactly its `tpu.lef` SIZE,
pins by LibreLane's placer, the `reg32` block settings), `top/`, the
predicted LEFs, and a README with these commands filled in for that N.
The four cells are independent, so they harden in parallel; the wall time
of the batch and the cpu-sum are BOTH recorded (§7.3).

```bash
cd ~/src/buda && flow/librelane/tier1a/harm.sh 4         # prints the PDN plan and the utilization estimate
cd flow/librelane/tier1a/n4/h
python3 ../../pdn_phase.py top/config.json predicted_lef/*.lef     # dry run, no tools: must PASS
date +%s > blocks.start
for c in pe_cell feed_cell wbuf_cell acc_cell; do
  (cd $c && librelane --dockerized --run-tag h config.json > h.log 2>&1) &
done; wait; date +%s > blocks.end
```

Pass: `Flow complete` in each `<cell>/h.log` and `<cell>/runs/h/final/{gds,lef,nl,spef/nom}`
present — the paths `top/config.json` names.

**The PEPAD is 100**, settled by the first real run (2026-09-06, N = 4,
LibreLane 3.0.11 / sky130A).  At the emitter's default the PE die is its
bus-face size (152 × 56 µm, 12 rows) and `OpenROAD.GlobalPlacement` refuses
with `GPL-0301 Utilization 152.234 % exceeds 100%`: the RTL's PE synthesizes
to **5,964 µm² of standard cells (624 cells)**, which is §7.1's ~1.7×
Yosys-to-LibreLane ratio applied to its share of the synthesis table — the
ratio `harm.py`'s estimate now applies, having read ~40 % low without it.
Two bars apply in turn, `GPL-0301` at 100 % and then
`PL_TARGET_DENSITY_PCT 50` via `GPL-0302`: PEPAD 56 clears the first and not
the second (~68 %), 88 lands on the line (49.8 %), and **100** (228 × 132 µm,
23,605 µm² of core, ~25–30 %) is the honest margin.  So:

```bash
flow/librelane/tier1a/gen.sh 4 -PEPAD 100 && flow/librelane/tier1a/harm.sh 4
```

which moves the top die to 1476 × 1644 µm and re-derives the PDN.  Arm F is
unaffected (it sizes itself from the RTL).  Measured at PEPAD 100: all four
cells `Flow complete`, **batch wall 440 s** (`pe_cell` the long pole, the
other three ~4.5 min), DRC and KLayout 0 each, route wirelength pe 15,576 /
feed 1,259 / wbuf 1,259 / acc 6,643.  `harm.sh` prints its own estimate per
cell and names the PEPAD to regenerate with when either bar is at risk.

**7b. Arm H — the PDN-phase check, before the top.**

```bash
python3 ../../pdn_phase.py top/config.json */runs/h/final/lef/*.lef
```

Pass: `PASS: 36 instances, ... 0 clips, every instance connected on VPWR
and VGND`, exit 0.  This is step 4's lesson as a step, and the reading of
it that survived contact with pdngen's source.  pdngen never SHORTS a
strap to a macro's pin — it CUTS the strap.  Where a strap comes within
spacing of any power pin of a macro, `Shape::cut` removes it over that
macro (the same-net exception spares it only when the strap CONTAINS the
pin across its width, which a 2 µm block pin in a 1.6 µm strap never is),
which is what the toy measured.  So a same-layer meeting is a CLIP, never a
connection, and what feeds a macro is the cross-layer crossing the macro
grid vias (`add_pdn_connect {met4 met5}`): a top met4 strap over the
block's met5 pin.  `harm.sh` chose `PDN_VOFFSET` so every macro of a cell
sees the straps at one phase, clear of its predicted met4 pins, crossing
its met5 pins, and with a VPWR+VGND pair in every standard-cell row
fragment the halos leave (the toy's sliver failure); `PDN_HOFFSET` keeps
the met5 straps off every macro's met5 pins, mid-channel.  This run
replaces the predicted pins and obstructions with the real ones.

A `CLIP`, `OBSTRUCTED` or `UNCONNECTED` line names the instance, the pin
rectangle, the strap and the smallest x- or y-shift that clears it, and
the equivalent `PDN_VOFFSET`/`PDN_HOFFSET` for the whole placement: change
the offsets in `top/config.json`, rerun the check, and only then the top.
`OBSTRUCTED` is the one this check exists for beyond the phase: an OBS
rectangle on a PDN layer that is NOT the block's own power metal, which no
phase search can have cleared — §7.2 has the rule and `RT_MAX_LAYER` is the
lever.  Obstruction that IS the block's own grid is counted and named as
such, since the pin clearance already governs that same metal.

**7c. Arm H — the top.**

```bash
(cd top && librelane --dockerized --run-tag h config.json)
```

Pass: `Odb.ManualMacroPlacement` prints `Successfully placed 36 instances`
— the instance-name rule (`row_0/pe_0` in the DEF, `row_0.pe_0` in the
flattened netlist) fails HERE, with exit 1, if it is wrong for this
LibreLane — then `Flow complete`, `All shapes on net VPWR are connected`
(and VGND).  The placement is the DEF's translated by (226, 70) µm at
PEPAD 100: the emitter puts `feed_*` at x = −140, outside its own DIEAREA,
and the shift is the smallest that brings every halo inside the die
(`top/placement.json` has both coordinates of every instance).

Measured 2026-09-06 at N = 4, PEPAD 100: **2,890 s**, `Successfully placed
36 instances`, both PDN nets connected, DRC / KLayout / LVS / antenna 0,
15,167 standard cells beside the 36 macros on a 1,476 × 1,644 µm die.  The
instance-name rule, the sky130A PDN defaults, the strap-centre semantics
and the row-fragment rule all held on the real tool.

**7d. Arm H — the row.**

```bash
python3 ../../runtimes.py top/runs/h --set N=4 --set arm=H --blocks-from top/config.json
python3 ../../runtimes.py top/runs/h --set N=4 --set arm=H --blocks-from top/config.json --json >> ../../results.jsonl
```

`--set` stamps the row with its coordinates (step 7's rule); `--blocks-from` reads each block's run directory and instance count off the
top's own `MACROS` entry, so the H row (§7.3: wall AND cpu-sum for the
blocks, wire per PLACED instance, the block-internal wire its own column)
cannot disagree with the config the top was built from.

**The first F-vs-H pair, N = 4** (2026-09-06).  `gen.sh -PEPAD` changes only
the emitted DEF/LEF — `tpu_rtl.v` and the flat `config.json` are
byte-identical at PEPAD 24 and 100 — so **arm F is PEPAD-independent** and
step 7's row stands without a re-run:

| | F (flat) | H (hier, no BUDA) | H/F |
|---|---|---|---|
| wall | 1,211 s | **3,296 s** (blocks 423 parallel + top 2,873) | 2.72× |
| CPU | 1,211 s | 4,327 s | 3.57× |
| die | 0.280 mm² | **2.427 mm²** | 8.66× |
| wire | 227 mm | 582 mm (top 243 + blocks 339) | 2.56× |
| setup WS (worst corner) | +1.81 ns | +0.89 ns | — |
| DRC / LVS / antenna | 0 | 0 | — |

Two readings, and the second is the one that matters for §3.  **The blocks
cost 423 s of wall and are CONSTANT in N** — four cell types whether the
array is 2 × 2 or 32 × 32 — which is §4's solve-once premise visible in a
measurement for the first time; the top is the entire growth term, and at
N = 4 alone it is 2.4× the whole flat run.  And **the top is slow because
its die is 8.66× F's, which is a SIZING artifact rather than a cost of
hierarchy**: the emitter sizes each cell to its bus faces and PEPAD 100 pads
it to ~30 % utilization, while F derives its die from cell area at 46 %.
Block sizing is one of the four decisions §3 assigns to BUDA, so this arm
measures *hierarchy with geometric block sizing*, and the 8.66× is the
headroom H+B has to recover — not what hierarchy costs.  Timing now has a
number behind §2.4's caveat: +0.89 ns against F's +1.81 at the slow corner,
every block boundary budgeted by one `IO_DELAY_CONSTRAINT`.

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
`salsa20` with `check_design` clean and LibreLane signoff clean.  Two
requirements phase 0 measured go into the writers rather than the recipe:

* **A macro is placed at a PDN PHASE its power pins clear** (§8 step 4: at
  x = 20 `u0`'s VGND pin sat under the top's VPWR strap, pdngen clipped the
  strap, and signoff — nothing earlier — refused the design; at x = 10 ≡
  160 mod 30 both macros connect).  BUDA already has the shape of this
  machinery: `align_bottom_up` nudges congruent instances onto a common
  TRACK phase (coordinate mod the LCM of the layer pitches), and a PDN grid
  is one more period — `PDN_VPITCH`/`PDN_HPITCH` with `PDN_VOFFSET`/
  `PDN_HOFFSET` from the top's config, tested against the macro's own
  pin rectangles from its LEF rather than against the cell bbox.  Either
  the placer snaps each macro to a clearing phase, or the placement writer
  derives `PDN_*OFFSET` from where the macros landed; the toy's fix was the
  former by hand.  The check belongs in the writer, not in signoff.
* **The pin verifier compares RECTANGLES, not `PLACED` origins** (§8 step
  3: OpenROAD re-centres every pin's origin; 66/66 rects identical, 0/66
  origins).
* **A block is hardened with its routing capped BELOW the top's PDN
  layers** (`RT_MAX_LAYER met3` on sky130, where the top's straps are met4
  and met5; §8 step 5b): a block that routes on met4 hands the top a LEF
  with a met4 `OBS`, pdngen drops every core strap that would cross it,
  and the macro's power pins go unconnected — found only at signoff, and
  only on the block whose pin layout had cost it enough wire to reach
  met4.  BUDA's `reserve_top_layers` is the same rule from the other side;
  the block-config writer emits it.
* **The pin-DEF writer places each block pin on the row BUDA's bit lands
  on** (§8 step 5b): with pins from a hand template, only 11 of 32 bits had
  their pin inside their BUDA row (31 µm off at worst), and the router paid
  the difference in vertical jogs — 22.7 % of the bus wire — which the
  strict containment verdict counts against the guide.  The corridor is
  followed (92 % by length); the rows have to agree with the pins, and the
  writer is where they are made to.

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
   for concrete runtime numbers; Chisel is acceptable.  ~~Open: the tier-2
   config size~~ — the 1a flat numbers (§7.1) say what this box affords:
   **~55 k std cells / 1 mm² is a 76-minute flat run, and the next doubling
   is 5–6 h.**  So tier 2's `ChipTop` should be sized to the N = 8 point
   (a Rocket + a Gemmini mesh at 4 or 8, ~50–100 k cells) if its F arm is
   to be run at all on a laptop — a bigger config still has an H+B arm but
   no flat baseline to compare against, which is the one thing the
   crossover needs.  Confirm against tier 1b's numbers when they exist.
4. **The PDN-phase placement rule** (§9, phase 1): snap-to-phase in the
   placer, or derive the offsets from the placement?  Snapping keeps the
   top's PDN config authoritative (what a real flow has) and costs each
   macro up to half a pitch of movement; deriving the offsets moves the
   whole grid for one macro's sake and cannot serve two macros at
   incompatible phases.  Snapping is the proposal; the two_reg32 toy at a
   deliberately wrong phase is the test either way.
5. **The 5 % pass threshold of measurement A** (§8 step 5) is a number
   read off two runs of one toy.  It should be re-read on the first real
   vehicle (tier 1a, N=4): if the gcell-edge and pin-access share does not
   scale with the design, 5 % stays; if it does, the threshold is the wrong
   shape and the check should exclude the terminal gcell at each pin.
