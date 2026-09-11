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
| Block size | `DIE_AREA` + `FP_SIZING absolute` | **EXISTS**: `emit_block_size <file.json> <block-or-cell> [area <um2>] [util <pct>] [aspect <w/h>] [margin <um>] [metrics <file.json>] [inst <name>]` (`buda_session/block_size.py`) — the larger of the FACE demand (`eff_bus_width` of the bits the routed plan lands on each face, W/E constraining height and N/S width) and the AREA demand (`area / util`, shaped by `aspect`), per axis, reporting WHICH BINDS. Vehicle: `tier1a/size.buda` (§8 step 3c) |
| Block pins at exact positions | `FP_DEF_TEMPLATE` — the shape `phase0/reg32/gen_pins_def.py` writes by hand | NEW writer: per net bit landing on a block, face + coordinate + layer from the DNUTS `net_segment` endpoint at the busterm, transformed to block-local through the instance orientation (`orient_rect.py`) |
| Block pins from the plan, written | `FP_DEF_TEMPLATE` — the same file, from BUDA | **`emit_pin_def <file.def> <block-or-cell> [unrouted <edge> [<layer>]] [depth <um>] [grid <dbu>] [lef <file>] [snap] [on_mismatch refuse|reference]`** (`buda_session/pin_def.py`): after `run_detailed_nuts`, one pin per net bit where its bit-wire meets the block face, on the bit-wire's layer (a track by construction), rectangle SYMMETRIC about the PLACED point, PLAIN names (odb reads an escaped `d\[16\]` back as `d[16\]` and matches nothing; `escaped_names` opts in), UNITS from `lu_per_um`; a cell in a hier session is a TEMPLATE (every instance must agree in cell-local coordinates, `N` only for now); nets on no bus are spread on one edge's tracks; a pin is on the BLOCK's track grid, so an instance origin off the pin layer's track period is REFUSED with the residue and the clearing shift (`snap` moves each pin to the nearest block-frame track and reports the largest, BUDA-1713); verifier `tools/pin_def_verify.py` (absolute rectangles, never origins); two pins never share one rectangle — a template MERGES what each instance routes, so one net's pin can land on another's metal where no single instance would (BUDA-1715, 8 of a PE's 32 south-face pins on the tier-1a array), and the later one moves to the nearest free block-frame track; `on_mismatch reference` is for a cell whose instances CANNOT agree because their neighbours differ (the array's last PE row hands its psum to an accumulator, every other row to the PE above), taking the disputed pin from the position the most instances share and counting the jog every other one is left with (BUDA-1714). **The template must be COMPLETE**: `FP_DEF_TEMPLATE` makes LibreLane skip `OpenROAD.IOPlacement` entirely, so a port the template omits is placed by nobody and `GPL-0326` refuses the run — which is why the tier-1a flow reads the SYNTHESIZABLE netlist (`clk`/`rst` and all) rather than the emitter's structural view. Vehicles: `phase0/two_reg32/pins.buda` (§8 step 3b), `tier1a/pins.sh` (§8 step 7f) |
| Bus corridors | `read_guides` after `set_nets_to_route` | **EXISTS**: `emit_guides <file.guide>` writes the OpenROAD guide file — gcells from the DEF's `GCELLGRID`, the floor-at-both-ends junction rule, DEF-escaped names, pin-access strips on the `terminal` layers (§8 step 5b; the phase-0 lessons of step 5 are the rules it is built from).  **LibreLane 3.0.11 has no step that READS one** (`grt` only writes), so an arm hands them over by cutting the flow: `--to OpenROAD.DetailedRouting --skip OpenROAD.DetailedRouting`, `read_guides` into the ODB, then `--last-run --from OpenROAD.DetailedRouting -e odb=<ours>` — LibreLane's own detailed route and signoff, on BUDA's corridors (§8 step 7f, `tier1a/guide_route.tcl`).  Two traps: the cut has to be BELOW every step that re-routes (`ResizerTimingPostGRT` re-runs `grt.tcl`), and `write_guides` emits the guides the ODB already holds, so the merge must FILTER the router's entry for each guided net rather than concatenate |
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

Arm H+B is what `tier1a/pins.sh N` + `harm.sh N --pins pins` + `guides.sh N`
build (§8 step 7f); it EXISTS at N = 2.  The placement is still the
emitter's DEF rather than `PlacementOptimizer`'s, which is deliberate —
the two hierarchical arms must differ only in what BUDA adds, and a
different placement would confound sizes, pins and corridors with it.

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

**What pdngen's connection mechanism actually is** (OpenROAD `src/pdn/src`,
read 2026-09-07 — until then the rule above was inferred from behaviour, and
a run whose macros floated had been chased on the inference).  It is
verified now, and it is narrower than the prose above implies:

* `Grid::getIntersections` (`grid.cpp:573`) emits a via wherever a same-net
  shape on an `add_pdn_connect` pair's LOWER layer OVERLAPS a same-net shape
  on its UPPER layer.  That is the whole rule.  It is CROSS-LAYER, so "a
  strap of the same net over the pin on the pin's own layer" is not part of
  it — a macro pin with no strap above it connects perfectly well, which is
  what a model-free reading of a failing run's PDN DEF had shown and nothing
  could explain.
* `InstanceGrid::getInstancePins` (`:1609`) injects the macro's OWN pins as
  fixed shapes on their own layers and `InstanceGrid::getIntersections`
  (`:1654`) merges them into the search set.  **So a macro's met4 pin IS
  the partner that vias its met5 pin, with no strap anywhere** — and that
  is now counted rather than argued (#905, 2026-09-09): on the N=8
  `PDN_HOFFSET 109.3` DEF the via the source predicts character for
  character, `via5_6_2000_2000_1_1_1600_1600`, is placed **2,664 times**
  (1,520 VPWR, 1,144 VGND), with **zero** `PDN-0110`/`PDN-0195` — the only
  two voices a declined pair has.

  That those are PIN-ON-PIN rests on the via-name FAMILY, not on reading
  one number.  The DEF draws four `via5_6_*` names, and the trailing
  `1600_1600` is CONSTANT across all four — so it is not the shape widths,
  which is the misreading available to anyone who notices that 1.6 is also
  the strap width.  The LEADING pair varies over {1600, 2000}, and the only
  nonzero strap widths in `SPECIALNETS` are met4 1600 and met5 1600, so a
  leading 1600 says THAT SIDE is a strap and `2000_2000` says neither is.
  All four combinations appear, in the proportions the geometry predicts:
  pin×pin 2,664, strap×strap 705, **pin×strap 256, strap×pin 248** —
  naming the met4 side first, the order `via5_6` reads in (met4 is layer
  5, met5 layer 6).  Those two were printed the other way round when
  this paragraph landed: the leading pair is the via's x/y EXTENT rather
  than its met4/met5 sides, so a met4 PIN crossing a met5 STRAP is
  `2000_1600` (2.0 from the pin in x, 1.6 from the strap in y) and reads
  naturally as "strap second".  It changes nothing about `2000_2000`.

  **And none of it needs to be inferred from a name.**  Asking the DEF
  which layer carries a strap beneath each placement answers the
  question directly, and that is the measurement worth keeping:

  | via | met4 strap | met5 strap | count |
  |---|---|---|---|
  | `2000_2000` | **no** | **no** | **2,664** |
  | `2000_1600` | no | yes | 256 |
  | `1600_2000` | yes | no | 248 |
  | `1600_1600` | yes | yes | 705 |

  Every one of the 2,664 sits where neither connect layer has a strap.
  The via-name family predicts that and the geometry confirms it, which
  is the right order to trust them in.

  Locating each via inside its instance (`COMPONENTS` origins + each cell's
  `SIZE`, bucketing every `SPECIALNETS` placement by containing instance)
  then meets the refuted claim on its OWN subject, which the aggregate does
  not: the claim was 512 **pe_cell VGND** crossings and NONE of them made.

  | cell | VGND | VPWR |
  |---|---|---|
  | **pe_cell** | **1,024** | 1,280 |
  | acc_cell | 72 | 144 |
  | feed_cell | 24 | 48 |
  | wbuf_cell | 24 | 48 |
  | **outside any macro** | **0** | **0** |

  pe_cell VGND alone carries **1,024** — 16 per instance across all 64 —
  where the old reading said zero.  Two things fall out.  **All 2,664 sit
  inside a macro bbox and none in the channels**, which is what pin-derived
  vias look like and is evidence independent of the width argument.  And
  **1,024 = 2 × 512**: the old "512 crossings" was itself short by half (16
  per instance, not 8), so the broken instrument was wrong in more than one
  digit — which is the whole lesson below, arriving twice.

  This paragraph said the opposite for a day, on a reading of the same DEF
  that reported all 512 `partner-no-via`, and the correction is worth more
  than the fact: **the source reading was right and the measurement that
  overturned it was the broken instrument.**  A count settled it, in the
  source's favour, against a measurement — so "measured otherwise" is not
  automatically the end of an argument; the instrument is evidence too.
  **Which half misreported is now found, and it was neither reader.**  The
  suspects were cleared in turn — `read_vias` reads 6,781 of 6,781 via
  placements across three of OpenROAD's own pdngen goldens, and the
  SPECIALNETS placement map finds all four vias inside the very rect the
  tool called empty, on net `VGND`, so the net-name mismatch that was the
  standing hypothesis is ruled out.  The defect is in `report()`: its
  detail block lists **every** finding of a floating terminal, not only
  findings that lack something, and the branch describing them fell
  through to `"…but no via"` without ever testing `f["via"]`.

  On the 109.3 DEF every one of the **1,448** findings is `connected`
  **with a via** — there is no `partner-no-via` anywhere in the data.  The
  512 lines were 8 rects × 64 `pe_cell` instances whose TERMINAL is
  `unsourced`, each correctly joined and each described as unjoined.  So
  the artefact that overturned a correct source reading was one `elif` in
  a reporter, and the JSON beside it was right the whole time.

  The terminals really are `unsourced` — the vias join the macro's own
  pins and nothing feeds that island — which is why the substantive
  verdict never moved; only the sentence explaining it was false.  Pinned
  now by a test on the TEXT, the thing no fixture asserted, since every
  fixture carrying this shape checked the JSON.

  What the via does NOT do is source anything: it joins the macro's pins
  to each other, and the island is fed only if a surviving strap fragment
  touches it.  So `--self-cross`'s `yes` is a cell property, never a
  connection, and the model does not depend on it either way — it strands
  the island for want of a source, which is why direction B fails
  correctly.
* `Grid::makeVias` (`:827`) pulls into the macro's search area every shape
  from every OTHER grid, so the macro grid connects using the CORE grid's
  straps.  LibreLane's macro grid draws no metal of its own: `pdn_cfg.tcl`
  is `define_pdn_grid -macro -default -name macro -starts_with POWER -halo
  ...` plus exactly one `add_pdn_connect -grid macro -layers
  "$PDN_VERTICAL_LAYER $PDN_HORIZONTAL_LAYER"`, and no `add_pdn_stripe`.
  (Worth recording because if it HAD drawn its own straps, the phase search
  would be aiming at metal that does not exist.)

The same-layer CUT (`Shape::cut`, `shape.cpp:223`, sparing a same-net
obstruction only when the strap contains it across its width) is real and
unchanged — it is what REMOVES a strap, not what connects one.  The two
relations are independent, and only the cross-layer one answers "is this pin
fed".

Two readings a first cut got wrong, both caught in review and both worth
stating because they bite in opposite directions.  A DEF SPECIAL wire is
extended by half its width at the ENDS as well as across the run (what
`src/bdb.cpp`'s `special_wires` -> keepouts pass already applies on all four
sides), so a strap whose centreline stops beside a macro pin still reaches
it -- stopping at the centreline endpoints loses real metal and under-reports
connectivity.  And the count that decides a verdict is per TERMINAL: a LEF
`PIN` is one node whose several `RECT`s are alternative access shapes
connected inside the macro, so a via on any one feeds it, and pdngen viaing
one access shape and not another is the normal case rather than a floating
pin.

It also answers the question a per-pin rollup structurally cannot, and the
N=8 run is why it has to: there, the FAILING DEF and the passing one both
audited "208 terminals connected, 0 floating", because the failure was a
strap fragment isolated from the grid while every terminal kept its via.
`net_components()` partitions each net's own DEF metal into electrical
components -- same-layer rectangles that touch are one piece, and a via joins
every shape of that net covering its point -- calls the largest by area the
grid, and reports each remaining fragment with the terminals it strands.  A
fragment carrying terminals fails the run; one carrying none is floating stub
metal, named and not fatal.  Two rules earn their place: TOUCHING counts,
because pdngen writes a strap as segments that meet end to end and a
strict-overlap rule would report a whole grid as rubble; and two fragments
landing on one macro's pin are NOT joined, because a hard macro's internal
PDN really does connect them but PSM cannot traverse an abstract LEF and does
not credit it -- agreeing with the verdict matters more here than being
physically complete, so that bridge is reported rather than applied.
`PSM-0040`/`PSM-0069` and the `*-grid-errors.rpt` stay the verdict; this
localises a failure to pins or away from them.

`pdn_connect.py` is the post-mortem twin of `pdn_phase.py`: it reads the DEF
pdngen WROTE and reports, per macro power pin, whether a via landed on it and
— where none did — whether the pin had a same-net crossing to connect to at
all.  Nothing in it is modelled: every cut, halo and obstruction subtraction
has already happened by the time that metal is in the file, so it needs no
theory of any of them.  Its four verdicts are `connected` (a via, ground
truth), `no-partner` (nothing to reach — a geometry story), `partner-no-via`
(the crossing is there and the via is not — take it to the pdngen log, not to
more geometry), and `via-no-partner`, which the rule above makes impossible
and which therefore means the READER is wrong; it is printed first and fails
the run on its own, so a wrong reading cannot be mistaken for a clean design.

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
is paid for: on the phase-0 block a pin template that straightens the
top-level bus costs the block **+49 % of its own wire** when BUDA writes it
(`reg32`, 3937 → 5879 µm) and **+61 %** by hand (→ 6358 µm) — §8 steps 2, 3
and 3b, re-measured 2026-09-06 on the vehicle as it stands; the +26 % once
recorded here came from a template `emit_pin_def` now refuses, and step 3b
says why.  H+B minus H on the arm total alone would net that against the
bus it buys without saying which side moved.  `tier1a/runtimes.py
<top_run> --block <block_run>[:<instances>] …` writes the row that way.

### 7.4 Success criterion — decided 2026-09-08, after the N = 8 point

The first draft (kept below, struck) was written before any run.  Two of
its clauses turned out to describe a target the flow cannot reach by
construction rather than by execution, and the N = 8 measurements (§8 step
7f, measured at N = 8, PR #901) are what showed it; the third clause is the question the
study exists to answer and stays.

* **The floor: H+B ≥ H on the ARM TOTALS and on signoff, at every N.**
  The complete list: arm wall AND arm CPU-sum (§7.3 records both; the
  wall figure carries the ±25 % noise of §8 step 7d and the CPU-sum does
  not, so gating wall alone gates the noisier one), arm wire (top +
  blocks), die, worst setup slack, worst hold slack, power, and every
  signoff count the flow PRODUCES: route DRC, LVS, antenna, KLayout DRC,
  PSM's connectivity verdict, and **Magic's illegal-overlap count**
  (`magic__illegal_overlap__count`) — the last is not optional, because
  the met2-blanket variant of §11 item 13 passed every other count and
  improved the one it was aimed at (KLayout DRC 2 → 0) while breaking
  extraction with 6,233 `obsm2`/`metal2` overlaps: `Checker.IllegalOverlap`
  is what failed that run, and a floor without it would have called the
  run an improvement.  `magic__drc_error__count` is NOT on the list
  because these configs set `RUN_MAGIC_DRC` false and the flow reports it
  as `None`; a count the flow does not produce cannot be gated on.  Not
  "every metric": BUDA's pins and corridors move wire from the top into
  the blocks BY DESIGN, and that trade is the point — at N = 8 the top's
  wire fell 59.9 % while the blocks' rose 40.8 %, for an arm total 2.8 %
  under H+size and 15 % under H.  A floor that forbids the block half
  forbids the mechanism.  **That trade is now measured at three points**
  (N = 4 added 2026-09-10, `hb4/`), against H+size so the block sizes are
  held equal and only BUDA's pins and corridors vary:

  | N | blocks | top | arm | top's share of arm wire |
  |---|---|---|---|---|
  | 2 | +37.5 % | −45.4 % | **+0.59 %** | 44.5 % |
  | 4 | +38.5 % | −54.8 % | **−2.23 %** | 43.7 % |
  | 8 | +40.8 % | −59.9 % | **−2.84 %** | 43.3 % |

  Monotone, and it crosses between N = 2 and N = 4.  The reason is not the
  one it looks like: the top's SHARE of arm wire is essentially constant
  (~44 % at every N), so the crossover is not a shifting mix.  `arm =
  s·top + (1−s)·blocks` at the baseline's `s` is an identity, exact at all
  three N, so the question is only which of the two ratios moves.

  **The block half is not "nearly flat" — it is a computable mix climbing
  to a ceiling.**  Per-cell block wire is fixed (below), so the block ratio
  is that mix weighted by instance counts (N², N, N, 3N), and `pe_cell` is
  the only N² term, so it converges to `pe_cell`'s own **19264/13401 =
  +43.75 %**.  The model reproduces the measurement exactly where the
  per-cell values hold, and its two free extrapolations follow:

  | N | 2 | 4 | 8 | 16 | 32 |
  |---|---|---|---|---|---|
  | predicted | +35.18 % | **+38.50 %** | **+40.80 %** | +42.17 % | +42.93 % |
  | measured | +37.50 % | +38.50 % | +40.80 % | — | — |

  So the honest form of "monotone" is a race against a rising bar.  The
  top saving needed to break even is `(1−s)/s × blocks`, which RISES with N:

  | N | break-even top saving | measured | margin |
  |---|---|---|---|
  | 2 | ≥ 46.7 % | 45.4 % | **−1.3 pts** |
  | 4 | ≥ 49.7 % | 54.8 % | +5.1 |
  | 8 | ≥ 53.4 % | 59.9 % | +6.6 |

  The N = 2 row misses by **1.3 points** — that is what "has not yet paid"
  means, precisely.  And the margin grows but DECELERATES (+5.1 → +6.6
  against a bar rising 3–4 points per doubling), so the trade improves only
  for as long as the corridor saving outruns a ceiling-bounded block cost.
  The block half is now predictable in closed form; the top half is not.

  **What the top half saves is the upper-layer detour, not length
  everywhere.**  The three top DEFs decompose by layer (a wire-run walk of
  each `final/def`, which recovers 93–95 % of OpenROAD's own
  `route__wirelength` because it counts runs and neither vias nor patch
  rects — so read the shares, not the absolutes):

  | N | met1+met2 | met3+met4+met5 |
  |---|---|---|
  | 2 | **+5.5 %** | **−82.4 %** |
  | 4 | −11.7 % | −87.5 % |
  | 8 | −17.4 % | −89.5 % |

  At N = 2 the local wire GROWS by 5.5 % and the arm still comes within
  1.3 points of breaking even; what falls, at every N, is the metal above
  met2 — met4 alone goes 23,063 → 2,227 µm at N = 2, 73,832 → 3,478 at
  N = 4 and 272,018 → 9,051 at N = 8.  That names the mechanism rather
  than assuming it: BUDA's pins land INSIDE the block where the corridor
  arrives (measured on the hardened LEFs — 82 of `pe_cell`'s 84 pins and
  50 of `acc_cell`'s 52 move, H+size's sitting on the block boundary at
  x = 0.3 / y = 0.14 µm where `IOPlacement` put them), the §11 item 13
  notch opens the OBS over them, and the top reaches them over-the-cell on
  met2 instead of climbing to met4 to get around a block face.

  The saving is therefore BOUNDED by how much detour there is to remove,
  which is the same shape the break-even table shows from the other side —
  it improves with N and decelerates — and it predicts where the top half
  stops improving: at N = 8 met3+ is already down to **15.1 %** of the
  top's wire (42,907 of 284,831 µm) against H+size's **58.3 %**, so at most
  that 15 % remains to be taken.  The top half is still not a closed form,
  but it is no longer unexplained.

  **The trade is on the clock, and it is visible at all three points.**
  H+B's worst setup slack is UNDER H+size's by 0.117 ns at N = 2 (0.604 vs
  0.721), 0.144 at N = 4 (0.298 vs 0.442) and 0.106 at N = 8 (0.371 vs
  0.476) — all positive, no violations, but consistently worse while the
  wire is consistently shorter.  Shorter is not faster here because the
  wire moved DOWN: met2 is thin and resistive where met4 is thick, so
  trading 272 mm of met4 for a shorter met2 path raises resistance on the
  paths that take it.  Hold does not pay it (H+B is +0.112 ns with 0
  violations at all three N; H+size's own N = 8 leg carries −0.220 ns and
  60 hold violations).

  Two controls make the three-point comparison attributable to pins and
  corridors alone: the macros are at BYTE-IDENTICAL positions and
  orientations in both arms at every N (14/14, 36/36, 104/104 checked in
  the final DEFs), and the arms differ in the pin positions above.  Nothing
  else about the floorplan moves.

  **Per-cell block wire is N-independent for N ≥ 4, not for all N.**  The
  H+B templates and the hardened block LEFs (notch patches included) are
  BYTE-IDENTICAL between N = 4 and N = 8, so block hardening is per
  size-set rather than per point and any cross-N difference is a top-level
  effect.  But `pe_cell` breaks it at N = 2 — **19,754** against 19,264 at
  both larger N, the other three cells and every H+size cell being fixed at
  all three.  So the mechanism as first stated ("one template per cell TYPE
  from a reference instance, so the plan cannot depend on instance count")
  over-reaches: a template MERGES what each instance routes (BUDA-1715),
  and a 2×2 mesh has no interior PE, so the merge has genuinely different
  inputs.  That is also exactly why the mix model above misses N = 2 by
  2.3 points and lands on the other two.  The supportable claim is
  **converged by N = 4**, which is still what makes the hardening reusable.

  One more thing the three-point table should not leave silent: the N = 2
  H+B row carries `klayout__drc_error__count: 1` where the other two carry
  0 — it is the pre-notch run, the N = 8 practice having been to supersede
  such a run with a clean twin (`hb` → `hbnt`).  That pair moved the arm by
  11 units in 1.68 M (0.0007 %), so this is a footnote rather than grounds
  for a re-run — but it is the row the crossover is anchored on.  The comparison is STRICT, and it is strict on
  MEASURED grounds rather than for want of a measurement: **run-to-run
  timing noise is ZERO** (measured 2026-09-08 — an independent repeat of
  the N = 8 H+B arm, all three legs, against the run it repeats:
  **282 metrics present in both, 0 differ**, with no key in one and not
  the other — `timing__setup__ws` bit-equal at 0.367518,
  `route__wirelength` at 300,704, and the signoff counts down to the
  baseline's own 2 KLayout DRC errors).  The layout is reproducible, which
  is what the pinned detailed-router seed (`-or_seed 42`, §4) predicted,
  so there is no noise for a tolerance to absorb and every loss counts by
  arithmetic rather than by policy.  One caveat stands: it is ONE repeat
  of ONE config — bit-identity is far stronger evidence than closeness (it
  says the flow is deterministic, not merely quiet), but it is one pair.
  **N = 8 status:** holds on wall (4,797 s vs H's 6,208), CPU-sum (5,402
  vs 6,996 s), arm wire, die (3.935 vs 6.347 mm²), hold (H's −1.075 ns
  fixed to +0.112), power, PSM, Magic overlaps (0 vs 0) and — since
  `notch_obs.py` — **KLayout DRC (0 vs 0)**; fails on ONE: setup
  **0.0185 ns** under H (+0.3705 vs +0.3890 ns), which the noise
  measurement now confirms is real.
* **The die penalty is reported, not gated.**  ~~Within 10 % of F on die
  area~~ was a target for a flat flow, not a hard-macro one: F packs at
  46.3 % utilisation with nothing between the cells, while H+B pays a
  channel per block face, a pin-driven block padding (PEPAD 100 for pe_cell
  at 50 % block utilisation, feed/wbuf at 17 %) and a die that is the
  array's envelope — 3.935 mm² against F's 1.032, 3.81×, at N = 8.  The
  arithmetic, from the emitter's own die formula on the N = 8 set
  (PR #903 review), is what makes this a measurement rather than a
  judgement: of the 1648 × 2388 µm die, the block footprint is 1.459 mm²
  (37.1 %) and everything else — channels, margins, the tail — is
  2.476 mm² (62.9 %).  Pulling both named levers as hard as they go:

  | lever | die | vs current | vs F |
  |---|---|---|---|
  | channel 48 → 20 µm | 3.142 mm² | 0.80× | 3.04× |
  | PE at ~60 % utilisation (100 × 100) | 2.831 mm² | 0.72× | 2.74× |
  | **both** | **2.166 mm²** | **0.55×** | **2.10×** |

  The ceiling on tuning is a 45 % reduction landing at 2.10× F, against a
  clause that wanted 1.1× — and the channel, pure overhead rather than
  placer headroom, is the larger lever of the two.  So the number was
  never going to be met by tuning and would have been dropped after the
  data either way; the honest thing is to drop it now and keep the cost
  visible: every table states H+B's die against F's, and the floor above
  requires H+B's die ≤ H's (3.935 vs 6.347: holds).
* **The crossover, the study's question, unchanged:** some N inside the
  sweep at which H+B beats F on wall time by **≥ 2×**, with H+B's worst
  setup slack within 0.5 ns of F's and signoff clean.  **Status:** not
  observed.  H's wall against F went 2.72× at N = 4 to 1.37× at N = 8
  (solve-once, §8 step 7d); H+B is 1.06× F at N = 8 (4,797 vs 4,541 s),
  setup +0.368 ns against F's −0.550.  N = 16 is the first N at which the
  trend can show H+B under F, and F at N = 16 is the hours-long reference
  that bounds the ratio.

The first draft, for the record:

* ~~**H+B ≥ H on every PPA metric at every N.**~~
* ~~**At the largest N where F completes, H+B is within 10 % of F on die
  area and within 0.5 ns of F's worst setup slack, at ≤ ½ of F's wall
  time.**~~
* ~~**The crossover exists inside the sweep**: some N at which H+B beats F
  on wall time by ≥ 2× while meeting the bullet above.~~

Its own caution stands and is why the change is written down with the
numbers that forced it rather than made silently: a number chosen after
seeing the data proves nothing, so a clause dropped after seeing the data
has to say what it was measuring and why that was the wrong thing.

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
| the hand template (`gen_pins_def.py`) | 6358 µm | +61 % | clean (6303 µm before `RT_MAX_LAYER met3`, the same run) |
| BUDA, every met3 track | 5789 µm | +47 % | **refused**: GRT-0116, 69 overflow |
| BUDA, every SECOND met3 track | 4966 µm | +26 % | **not reproducible** — see below |
| **BUDA, the vehicle as it stands** | **5879 µm** | **+49 %** | clean; 17 overflow at the top, DRC/LVS 0 |

**The 4966 µm / +26 % row is history, not a result the vehicle produces.**
It was measured before `move_comp` was added, i.e. with the pins 0.40 µm off
the block's own met3 track — the configuration `emit_pin_def` now REFUSES
(verified 2026-09-06: delete the two `move_comp` lines and it refuses,
correctly, naming the residue).  Re-measured on the vehicle as it stands the
number is **5879 µm, +49 %**: still better than the hand template's +61 %,
by 12 points rather than 35.

**And the reason is that the bus is no longer on met3.**  With the macros on
the track period the planner puts it on **met1**, at the met1 pitch (340
DBU), and the template's 64 `d`/`q` pins come out on met1 — not the
`IO_PIN_H_LAYER` the hand template used.  The origin is not the trigger
(340 and 190 both give met1 once the macros move); the 0.8 µm move is, which
says the choice was marginal.  The mechanism is visible in the declared
patterns: halving met3 to give the router its adjustment margin left met3 at
1360 DBU per bit against met1's 340, so **met3 became 4× more expensive per
bit than the layer below it**, and nothing in the flow REQUIRES the pin
layer — `TOP` is a preference the cost function can outvote.  Both layers
can host the bus (met1 235 bits per 80 µm face, met3 78), so this is cost,
not capacity.

The off-grid pins were also NOT the cause of the 4 residual overflow: with
the corrected, on-grid template the top's global routing reports **17**
(met2 7, met3 5, met4 4, met5 1) and still signs off clean, DRC and LVS 0,
because detailed routing resolves them.

**What this asks of phase 1** (§9): the writer must CONSTRAIN the bus to the
intended pin layer rather than leave the layer to the planner's cost — a
template whose layer can flip on a sub-micron placement change is not a
handoff anyone can build on.  Until it does, the §7.3 block-internal column
is +49 %, and the layer the template lands on has to be read off the run
rather than assumed.

`config_buda_pins.json` differs from `config_pins.json` only in where
`FP_DEF_TEMPLATE` points, so the two hardenings are comparable.  **Both
carry `RT_MAX_LAYER met3`**, and that is not tidiness — a block allowed to
route on the top's PDN layers hands the top a met4 `OBS`, pdngen drops every
strap crossing it, and every macro power pin comes out unconnected (measured
at step 5b; §9 carries the rule).

**3c. How big should the block have been?**  Steps 3/3b place the pins;
this sizes the die they sit on, which is the OTHER half of the block-side
handoff and the one §8 step 7d's 8.66x hangs on.  It needs no tools — the
routed plan supplies the faces and a previous hardening run the area:

```bash
cd ~/src/buda && bin/buda --no-viz flow/librelane/tier1a/size.buda
python3 -c "import json;d=json.load(open('flow/librelane/tier1a/out/pe_cell.json'));print(d['DIE_AREA'],d['derivation']['binds'])"
```

Pass: four `[BlockSize]` lines, each naming the die, which demand binds on
each axis, and how the emitted cell compares.  **Measured 2026-09-06** on
the checked-in N = 8 array (`PEPAD 24`, every cell 152 x 56) with each
cell's `design__instance__area` from step 7a:

| cell | rule says | binds (w, h) | faces | emitted / rule |
|---|---|---|---|---|
| `pe_cell` | 144.5 x 58.7 | area, area | N/S 32b on M5 need 128; E/W 16b on M4 need 52 | **1.00x** |
| `acc_cell` | 96.0 x 51.1 | **face**, area | N/S 24b on M5 need 96 | 1.74x |
| `feed_cell` | 44.2 x 44.2 | area, area | E 8b on M4 needs 18 | **4.35x** |
| `wbuf_cell` | 44.2 x 44.2 | area, area | N 8b on M5 needs 32 | **4.35x** |

The reading is sharper than "everything is too big".  At `PEPAD 24` the PE
is *already right* — its area demand at 46 % and its face demand agree to
1 % — so the emitter's face-aware rule is sound FOR THE CELL IT WAS WRITTEN
FOR.  What is 4.35x oversized is every EDGE cell, because `EDGEW`/`EDGEH`
default to `PEW`/`PEH`: a `feed_cell` carrying 8 bits is given a die sized
for a PE's 32.  And `acc_cell` is the one cell whose WIDTH is face-bound,
so no area target can shrink it — the 24-bit psum on M5 is the floor.

That also says where the step-7d 8.66x actually comes from.  It is not the
PE (1.00x at PEPAD 24) but the run's `PEPAD 100`, which pads every cell to
228 x 132: 3.55x the PE's own demand, applied to all four types.  So the
headroom is real and it is a SIZING knob, not a property of hierarchy —
which is what arm H+B is for.

**Feeding it back** is the next step and deliberately not this one:
`harm.py` takes its `DIE_AREA` from `PEPAD`, and taking it from these
fragments instead changes the emitted DEF (block sizes move the pitches,
which move the die), so it wants its own before/after H row rather than
riding in with the writer.

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
cd ~/src/buda/flow/librelane/phase0/measure && mkdir -p out
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

Run it against the BUDA-pinned block (step 3b) — `TAG=phase0_buda_pins`
below.  `TAG=phase0_pins`, the HAND template, is the same recipe and is
what the "neither verdict passes" paragraph reports; the two runs are the
before and after of the pin writer.

```bash
cd flow/librelane/phase0/two_reg32
TAG=phase0_buda_pins                                                        # or phase0_pins for the hand template
librelane --dockerized --run-tag $TAG config_buda_pins.json                 # the top against THAT block
mkdir -p out
cp runs/$TAG/*-odb-manualmacroplacement/two_reg32.def out/placed.def         # u0/u1 FIXED, std cells unplaced
ln -sf $PDK_ROOT/sky130A/libs.ref/sky130_fd_sc_hd/techlef/sky130_fd_sc_hd__nom.tlef out/tech.tlef
ln -sf $PWD/../reg32/runs/$TAG/final/lef/reg32.lef out/block.lef             # the block BUDA routes between
../../../../bin/buda buda_route.buda --no-viz                                 # -> out/buda_bus.guide
cd ../measure
ODB=$(ls ../two_reg32/runs/$TAG/*-openroad-cts/two_reg32.odb)
./run_or.sh ../two_reg32/runs/$TAG guide_ref.tcl ODB=$ODB OUT=$PWD/out          # the ROUTER's corridor on this run
python3 extract_bus_guides.py out/all.guide out/all_bus.guide                # …kept as the control
python3 extract_bus_guides.py ../two_reg32/out/buda_bus.guide out/bus.guide  # the bus entries, as BUDA wrote them
./run_or.sh ../two_reg32/runs/$TAG guide_test.tcl ODB=$ODB OUT=$PWD/out
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

**With BUDA's pins both verdicts pass** (`TAG=phase0_buda_pins`, measured
2026-09-06): the bus is 2294 µm in 168 segments, **0.0 % outside BUDA's
corridor and 0.4 % on another layer inside it**, so the strict measure
passes at 0.4 % against 5 %.  The control passes too, and that is the
expected shape rather than a failed test: with the pins on the plan's rows
the plan IS the router's natural route, so the two corridors coincide —
the sharp form of step 5 (a corridor shifted a whole gcell) stays the test
of FOLLOWING.  The top for that run needs `GRT_ALLOW_CONGESTION` (step 3b:
4 residual overflow units at the die's east margin, which detailed routing
clears), and the measure scripts' `global_route` carries
`-allow_congestion` for the same reason — refusing there would end the
measurement before it starts.

**With the HAND template's pins neither verdict passes, for one known
reason, and the recipe says so rather than lowering the bar** (next
paragraph): the corridor verdict
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
python3 ../../pdn_phase.py top/config.json predicted_lef/*.lef     # dry run, no tools: ADVISORY (§11 item 8)
date +%s > blocks.start
for c in pe_cell feed_cell wbuf_cell acc_cell; do
  (cd $c && librelane --dockerized --run-tag h config.json > h.log 2>&1) &
done; wait; date +%s > blocks.end
../../notch.sh 4                                     # the patched abstracts the top's MACROS name
```

Pass: `Flow complete` in each `<cell>/h.log` and `<cell>/runs/h/final/{gds,lef,nl,spef/nom}`
present — the paths `top/config.json` names.

`notch.sh` is outside the timed batch on purpose (it is seconds, and the
wall figure §7.3 reports is the hardening) but it is NOT optional: it closes
the abstraction notch of §11 item 13 per cell, and `top/config.json` names
the `<cell>.notch.lef` it writes, so the top run stops on a missing LEF
without it.  Pass, per cell: `area ... (IDENTICAL)` from the decomposition
and the piece count `notch_obs.py` claimed (~1.2 µm² per cell at N = 8; zero
pieces is also a pass and means Magic's abstract already covered the layer).

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
python3 ../../pdn_phase.py top/config.json */runs/h/final/lef/*.notch.lef
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

**N = 8, and the H/F gap is closing** (2026-09-06).  Arm H at the next
size, same recipe.  This is NOT §7.4's crossover — that one is about
**H+B**, which does not exist yet — see the reading below the table:

| | F N=4 | H N=4 | F N=8 | H N=8 |
|---|---|---|---|---|
| wall | 1,211 s | 3,296 s | 4,541 s | **6,208 s** |
| CPU | 1,211 s | 4,327 s | 4,541 s | 6,996 s |
| blocks (wall, parallel) | — | 423 s | — | **326 s** |
| top alone | 1,211 s | 2,873 s | 4,541 s | 5,882 s |
| die | 0.280 mm² | 2.427 mm² | 1.032 mm² | 6.347 mm² |
| wire | 227 mm | 582 mm | 935 mm | 1,980 mm |
| setup WS | +1.81 ns | +0.89 ns | **−0.55 ns** | +0.39 ns |
| hold WS | +0.103 ns | +0.11 ns | +0.091 ns | **−1.075 ns** |
| DRC / LVS / antenna | 0 | 0 | 0 | 0 |

**H/F on wall time goes 2.72× → 1.37×** — measured, at two points.  The
per-doubling growth behind it is F **3.75×** against H **1.88×**, but each
of those is a SINGLE interval (two points, no third to check the shape) and
the wall figures carry ±25 % noise (below), so they are a trend, not a law.

**What this is not.**  §7.4's crossover asks for an N at which **H+B** beats
F on wall time by **≥ 2×** while holding die area within 10 % of F's and
setup slack within 0.5 ns (as §7.4 read when this was written; the die
clause is since struck and the penalty reported instead).  Nothing here
bears on it:

* the arm measured is **H**, without BUDA — H+B does not exist yet, and
  §7.2 defines H as the control that isolates what hierarchy alone costs;
* H is **slower than F at both measured N** (2.72× and 1.37×), so no
  crossover has been observed on either side of anything;
* extending the single-interval rates to N = 16 gives F ≈ 4.7 h against
  H ≈ 3.2 h — a 1.47× advantage, still short of the 2× the criterion asks,
  and an extrapolation rather than a measurement;
* and H's die is **6.15× F's** at N = 8, nowhere near the 10 % the same
  criterion requires, for the sizing reason in reading 2 below.

So the honest statement is the trend: H's wall-time disadvantage shrinks as
N grows, which is what the solve-once premise predicts, and N = 16 is the
first run that could show H reaching parity with F.  Whether §7.4's
crossover exists is a question about H+B and stays open.

Where H's slower growth comes from is measured, not inferred: the blocks
cost **326 s against 423 s** — the same work, so the difference is noise
(below) — and H's TOP grows 2.05× where F's whole run grows 3.75×, because
the PE logic is inside the macros and the top places 35 k standard cells
against F's 56 k.

**The block artifacts at N = 8 are BYTE-IDENTICAL to N = 4** — every LEF,
every `route__wirelength`, every cell count, every DRC number.  So the
solve-once premise holds in the strongest available form: not merely
constant in N, but the same output, which means a production flow would
CACHE them and pay **zero** for blocks at every N after the first.  It also
bounds the noise on these wall figures: identical work took 423 s and 326 s
on the same machine, so read ±25 % into every runtime here.  The premise is
scoped, though — it holds because this vehicle's CELL-TYPE SET is fixed as
N grows (36 → 104 instances of the same four cells).  A design whose block
CONTENT grows with N would not behave this way.

**Both arms fail timing at N = 8, in different places**, which is the
sharpest thing the pair says: F misses SETUP (−0.55 ns, §7.1) while H makes
setup (+0.39 ns) and misses **HOLD** by 1.075 ns at the three slow corners
— LibreLane raises it as a deferred error and the run ends non-zero with
routing and DRC otherwise clean.  Hold at a hardened boundary is §2.4's
caveat arriving: every block I/O is budgeted by one `IO_DELAY_CONSTRAINT`,
and no arm can fix that from the top.  Per-pin budgets (phase 3) are what
this asks for.

**Two caveats on the runtimes.**  The N = 8 top needed the container's
memory raised from 8.2 to 20 GB: nine parallel STA corners at ~800 MiB each
overran it, and Magic's abstract-LEF write peaked at 6 GiB, was killed
mid-write, and left a 0-byte LEF that `Odb.CheckDesignAntennaProperties`
turned into an unhandled `StopIteration` rather than "the design LEF is
empty".  So N = 4's H top ran under 8.2 GB and N = 8's under 20; the
comparison slightly favours N = 8.  And 6.35 mm² is the largest design
here — the memory ceiling, not the algorithm, is what a bigger N will hit
first.

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

**7e. Arm H+size — the block sizes come from BUDA** (one of H+B's three
contributions; the pins and the corridors are not in this arm — see the
measured section below).  Steps 3c and 7a-7d
leave one number on the table: arm H's die is 8.66x arm F's at N = 4 and
6.15x at N = 8, and §8 step 3c measured that as a SIZING artifact — the
emitter pads every cell with one `PEPAD` while `emit_block_size` sizes each
by the larger of its face and area demands.  This is the arm that collects
it.

Feeding the sizes back is not a re-hardening: the emitter derives the PE
pitch from `PEW` (`PPX = PEW + CHAN`), the row from the pitch and the die
from the row, so a block that shrinks moves every instance and the die with
it.  The array is RE-EMITTED at BUDA's sizes:

```bash
cd ~/src/buda && bin/buda --no-viz flow/librelane/tier1a/size.buda
cd flow/librelane/tier1a
python3 apply_sizes.py out --n 8 --baseline n8 --optimize-aspect   # read it before running it
./gen.sh 8 $(python3 apply_sizes.py out --n 8 --optimize-aspect --args)
./harm.sh 8
```

`harm.sh` only WRITES `n8/h`; the hardening is steps 7a-7c, run from
`n8/h` exactly as the generated `README.md` there spells them out (the four
blocks in parallel, `pdn_phase.py`, then the top).  Only then:

```bash
cd n8/h
python3 ../../runtimes.py top/runs/h --set N=8 --set arm=H+B \
        --blocks-from top/config.json --json >> ../../results.jsonl
```

`apply_sizes.py` turns the fragments into the emitter's knobs and predicts
the die with the emitter's own arithmetic — transcribed, and pinned by a
test that reproduces BOTH measured arm-H dies (2.427 mm² at N = 4 and
6.347 at N = 8) from the parameters alone, so a drifted transcription
cannot promise a saving nobody can collect.

**MEASURED at N = 8** (2026-09-07, macOS / LibreLane 3.0.11 / sky130A, Docker
at 20 GB).  It hardens, and the prediction below was exact — 3.935 mm² to
the digit.

**This arm is `H+size`, NOT H+B.**  §7.2 defines H+B as BUDA's sizes AND its
`FP_DEF_TEMPLATE` pins AND its corridors; step 7e applies only
`emit_block_size` and then runs `harm.sh`, which keeps per-block
`IOPlacement` pins and ordinary global routing, and the row carries none of
BUDA's own end-of-run triple (§7.3).  So this measures **hierarchy plus
BUDA sizing** — one of the three contributions — and the row is stamped
`arm=H+size` so it cannot be read as the H+B point.  H+B needs the pin
writer (§8 step 3b, working on the toy) and the guide handoff (§8 step 5b,
working on the toy) carried into the tier-1a vehicle:

| N = 8 | F | H | **H+size** |
|---|---|---|---|
| wall | 4,541 s | 6,208 s | **5,023 s** |
| CPU | 4,541 s | 6,996 s | 5,855 s |
| blocks (wall, parallel) | — | 326 s | 408 s |
| top alone | 4,541 s | 5,882 s | 4,615 s |
| die | 1.032 mm² | 6.347 mm² | **3.935 mm²** |
| utilisation | 46.3 % | 50.8 % | 38.7 % |
| arm wire | 935 mm | 1,980 mm | 1,732 mm |
| setup WS | −0.55 ns | +0.39 ns | **+0.46 ns** |
| hold WS | +0.091 ns | −1.075 ns | **−0.238 ns** |
| route DRC / LVS / antenna | 0 | 0 | 0 |
| KLayout DRC | 0 | 0 | **5** |

**H+size improves four metrics and regresses two.**  Better: die 0.62×,
wall 0.81×, arm wire 0.87×, hold slack −1.075 → −0.238 ns.  Worse: **KLayout
DRC 0 → 5**, and block wall time 326 → 408 s (that one is byte-identical
work and inside the ±25 % noise §8 step 7d measured, so read it as noise,
not as a cost).  The DRC regression is not noise and it **breaks §7.4's
floor** — "H+B ≥ H on every PPA metric at every N" — so on the criterion as
written this arm fails, on 5 `m2.2` spacing violations, and that has to be
fixed rather than argued away.

So §8 step 3c's reading was right — arm H's die was a SIZING artifact and
BUDA collects most of it — and the `--optimize-aspect` search's own
arithmetic is trustworthy: it predicted the die exactly.

Against F the arm is still 3.81× the die and 1.11× the wall, so hierarchy
has not overtaken flat here; what changed is that the gap is now the one
hierarchy actually costs rather than one the emitter's padding added.

**Two residuals, both deferred errors** (the flow runs to completion and
exits non-zero at the gate): **5 KLayout DRC violations**, all `m2.2`
(met2 minimum spacing) in a repeating pattern — two x positions (763.4,
939.4) against three y (1944, 2052, 2160), edge gaps of 0.02–0.07 µm, so
systematic geometry rather than noise — and **hold at −0.238 ns**, better
than H's −1.075 but not passing.  Route DRC, LVS and antenna are clean.

**The PDN needs `harm.py`'s own offsets, and a hand-"fix" broke it.**  On the
first attempt at this arm the top failed `PSM-0069` with 512 unconnected
VGND shapes, and the cause was an offset introduced BY HAND after
`pdn_phase.py` reported the tool's own plan as failing (144 clips) and
blessed the replacement with "0 clips, every instance connected".  Re-run
unmodified, the same design reports `All shapes on net VPWR are connected`
and VGND, and `pdn_connect.py` audits 208 terminals connected, 0 floating.
The clips `pdn_phase.py` counts are what pdngen resolves by CUTTING a strap
(`Shape::cut`), which is only fatal when it isolates a fragment: the hand
offset cut VGND's met5 into 85 pieces (spans 32.8/108.6/152.8 µm against
VPWR's 21 intact at 1636.7) and stranded some.  So its clip verdict is as
unreliable as the connectivity verdict §7.2 records, and `harm.sh`'s
"the PDN plan fails pdn_phase.py" warning is — on this evidence — a false
alarm on a plan that works.  (Since resolved: `pdn_phase.py` now models
pdngen's cut/via/TRIM and reports a meeting as a TRIM, not a clip — §11
item 10.)

**The verdict is OpenROAD's own PSM check, not `pdn_connect.py`.**  That
script rolls up macro power TERMINALS, and the failure above is a strap
fragment isolated from the grid while every terminal still has its via —
which it structurally cannot see: on the FAILING DEF it reported the same
"208 terminals connected, 0 floating" it reports on the passing one.  It is
the right tool for asking whether a pin has access, and the wrong one for
asking whether the network is whole.  Read `PSM-0040`/`PSM-0069` and the
`*-grid-errors.rpt` for pass/fail; use `pdn_connect.py` to localise a
failure to pins or away from them.

**The prediction, now confirmed** (`PEPAD 100` is what the measured H run
used).  The die column below is no longer a prediction — the run above
emitted 3.935 mm² exactly — and the rows are kept as the record of what the
tool promised before anyone ran it:

| PE size | source | die | vs arm H | vs arm F (1.032 mm²) |
|---|---|---|---|---|
| 228 x 132 | `PEPAD 100` (arm H, measured) | **6.347 mm²** | — | 6.15x |
| 128 x 150 | the rule, `--optimize-aspect` | **3.935 mm²** | **0.62x** | 3.81x |

The saving is real and it is smaller than the geometry alone suggests,
because a block answers to a THIRD demand neither `emit_block_size` nor the
emitter knows about: **the placer measures utilization against the CORE,
not the die.**  The core is the die minus LibreLane's margins, rounded down
to whole standard-cell rows, and on a small block the two differ
enormously — a 128 x 67 PE is 8576 µm² of die and 5090 of core, so it is
117 % utilised and `harm.py` predicts `GPL-0301` on it (Codex #890 caught
exactly this in the first cut of the tool, which had claimed 2.802 mm²).
`apply_sizes.py` therefore searches subject to `harm.py`'s own bar rather
than to nominal area, and it grows the edge knob for the same reason —
`acc_cell` at the rule's 96 x 52 is refused too.

What `--optimize-aspect` is still for: the rule shapes a block by its own
faces, which is right for one block and not for an array, where `PPX` is
spent once per COLUMN so a wide PE costs N times its width and only once
its height.  The 128 the search lands on is exactly the PE's north/south
face demand, i.e. the face floor binds the width while the placer's bar
binds the height.

The honest caveats.  These are PREDICTIONS from the emitter's geometry plus
`harm.py`'s placement bar, not a run: they say nothing about whether the
blocks still meet timing at a different aspect, whether the top still
routes at 0.62x the die, or what the hold violation §8 step 7d found does
when the boundaries move.  And the
edge cells are coarsened on the way out — the emitter has ONE `EDGEW`/
`EDGEH`, so `feed_cell` and `wbuf_cell` (44.2 x 44.2 by the rule) are
emitted at `acc_cell`'s 96 x 52.  Making them independent is an emitter
change; `apply_sizes.py` names every cell it oversizes for that reason.

**7f. Arm H+B — the pins and the corridors come from BUDA too.**  Step 7e
spends one of H+B's three contributions (`emit_block_size`); this spends
the other two, so a row can be stamped `arm=H+B` rather than `H+size`:
each block is hardened with an `FP_DEF_TEMPLATE` BUDA wrote from its own
plan (§8 step 3b's writer, on the array), and the top's buses are routed
inside BUDA's corridors (§8 step 5b's guide file, mechanism A).  Two new
scripts and one option:

```bash
cd flow/librelane/tier1a
bin/buda --no-viz size.buda                                   # if out/ is empty
./gen.sh 2 $(python3 apply_sizes.py out --n 2 --optimize-aspect --args)
./pins.sh 2                      # -> n2/pins/<cell>.def, one per leaf CELL TYPE
./harm.sh 2 --pins pins          # each block config gets FP_DEF_TEMPLATE
cd n2/h && cat README.md         # steps 1, 3a, 3b, 3c, 4 -- the arm's own recipe
```

`pins.sh N` generates and runs `n<N>/pins.buda`; `guides.sh N` (step 3b of
the generated README) generates and runs `n<N>/h/top/guides.buda` and
`guide_route.tcl` puts its corridors into the ODB.  Both flows are
GENERATED for the reason `gen.sh` generates a config: a `.buda` script has
no variables and resolves every relative path against its own directory,
so an N-agnostic file could not name `n<N>/tpu.def`.  The rules behind each
live in the writer.

**The top runs in three parts, and that is the mechanism, not a
workaround.**  LibreLane 3.0.11 has no step that reads a guide file — `grt`
only ever WRITES one — so the corridor handoff is: run the top with
`--to OpenROAD.DetailedRouting --skip OpenROAD.DetailedRouting`, put BUDA's
guides into the resulting ODB, then resume `--last-run --from
OpenROAD.DetailedRouting -e odb=<ours>`.  What must NOT happen is finishing
the route ourselves, which is what phase 0's measurement did: every routing
metric, the DRC count and the whole signoff tail after it are LibreLane's,
and an arm whose numbers came from a hand-run router would not be
comparable with F or H.  So `guide_route.tcl` stops at `write_db`.  The
guides go in AFTER every step that might re-route (`RepairDesignPostGRT`
and `ResizerTimingPostGRT` each re-run `grt.tcl`), which is why the cut is
at detailed routing and not right after global routing.

**`FP_DEF_TEMPLATE` is ALL OR NOTHING, and that decides which netlist BUDA
reads.**  Declaring it makes LibreLane skip `OpenROAD.IOPlacement`
entirely (`openroad.py:1357`, "I/O pins were loaded from ..."), so a pin
the template omits is placed by nobody and global placement refuses the
run: `GPL-0326 clk toplevel port is not placed`, on all four cells.
`FP_TEMPLATE_MATCH_MODE permissive` does not rescue it — it only downgrades
the mismatch from an error to a warning; the pin still ends up unplaced.
So the template has to cover every port of the module the BLOCK RUN
synthesizes, which is `tpu_rtl.v` and not the emitter's structural
`tpu.v`: the structural view declares only the bus ports, while the
synthesizable twin also has `clk` and `rst`.  `pins.buda` therefore reads
`tpu_rtl.v` — the same file LibreLane does — and `emit_pin_def` spreads the
ports no bus reaches on one edge, which is exactly what a clock and a reset
need.  Permissive mode is still set, because the two pin sets are not
identical in the other direction either (the top's die ports reach a block
on nets BUDA has no corridor for); what `strict` was protecting is checked
afterwards and more sharply by `tools/pin_def_verify.py`, which requires
every TEMPLATE pin to be in the hardened DEF at the same absolute
rectangle.  **168 of 168 verified** across the four cells at N = 2.

Reading `tpu_rtl.v` immediately found a reader defect that no netlist in
the tree could show: **`input wire [7:0] a` was read as ONE BIT.**  The
declared width comes from the LEADING range of the direction clause (it has
to: a later bracket belongs to an escaped name), and the net type and sign
sit between the direction and the range — so with `wire` in the way the
range never matched, `pe_cell`'s 8-bit `a_in` arrived as a scalar, and the
first template built from that netlist had 8 pins where the design has 80.
Every netlist here writes the bare `input [7:0] a` form, which is why it
survived this long.  Fixed in `bdb.cpp` (strip the type and sign first) and
pinned on all four spellings.

**Three costs the block-side handoff carries on an array that it did not on
the toy**, each reported per cell by `pins.sh` so they can be read against
the block wirelength:

* **The pins are SNAPPED onto the block's own track grid** (BUDA-1713,
  largest shift 0.4 µm at N = 2).  The honest fix is a placement on the
  track period — the rule `align_bottom_up` implements and the one the toy
  used — but on an ARRAY that means rounding `PPX` up to a multiple of the
  met2 period (0.92 µm) and `RPY` to one of met3's (1.36), and those are
  paid once per COLUMN and once per ROW, i.e. N times each: ~9 % of the die
  at N = 8.  A snap costs at most half a period of jog at the face instead.
  The toy's free fix is not free here, and the arithmetic is why.
* **A cell whose instances have different NEIGHBOURS cannot have one
  template agree with every instance's plan** (BUDA-1714, the new
  `emit_pin_def … on_mismatch reference`).  In this array the last row of
  PEs hands its psum to an accumulator while every other row hands it to
  the PE above, so `row_0/pe_0` and `row_1/pe_0` route `p_out` out of
  different bundles to different places — measured, `p_out[0]` at cell-local
  x 61870 against 65550 — and no re-plan makes them congruent, because it
  is the design and not the plan.  The disputed pin is taken from the
  position the most instances share and every instance left with a jog is
  counted; that is the same trade `set_bottom_up` makes for a cell's
  internal routing, except that here the siblings are not congruent and the
  top's router pays for the copy.
* **Two nets never share one pin's metal** (BUDA-1715).  A template MERGES
  what each instance routes, so `p_in` can come from one instance and
  `w_in` from another and — the instances being congruent — land at the same
  local coordinate on the same face: 8 of `pe_cell`'s 32 south-face pins
  collided at N = 2 on the first attempt, which is two nets on one
  rectangle, i.e. a short.  `emit_pin_def` now moves the later pin (in name
  order) to the nearest free block-frame track and says so.  The toy could
  not show this either — one bus per face.

**`write_guides` emits the guides the ODB already has, so the merge must
FILTER.**  Phase 0's recipe concatenates the router's guide file with
BUDA's and reads the result as one set (`read_guides` REPLACES rather than
adds).  That works from a post-CTS ODB, where no net has a guide yet.  Here
the ODB comes from after LibreLane's OWN global route, so `write_guides`
wrote all 256 bus nets too and a straight concatenation named every one of
them TWICE — leaving which corridor the router obeys up to `read_guides`.
`guide_route.tcl` drops the router's entry for any net BUDA supplies, as it
copies.  Which nets are withheld is read off BUDA's guide file rather than
from a net-name prefix: a systolic array has one prefix per link, and a
prefix list would be one more thing to keep in step with the emitter.


**MEASURED at N = 2** (2026-09-07, macOS / LibreLane 3.0.11 / sky130A), with a
CONTROL — the same emitted array, the same block sizes, the same placement,
`harm.py` with no `--pins` and no corridor handoff — so the delta is exactly
the two contributions this step adds and nothing else:

| N = 2 | F | H+size | **H+B** | H+B vs H+size |
|---|---|---|---|---|
| top wall | 528 s | 900 s | 973 s | +8.1 % |
| blocks (wall, parallel) | — | 263 s | 329 s | +25 % |
| die | 0.087 mm² | 0.625 mm² | 0.625 mm² | — (same DIE_AREA by construction) |
| **top wire** | 64,268 µm | 67,857 µm | **37,043 µm** | **−45.4 %** |
| **block wire** (per placed instance) | — | 84,592 µm | **116,310 µm** | **+37.5 %** |
| **arm wire** | — | 152,449 µm | 153,353 µm | **+0.6 %** |
| setup WS | +4.655 ns | +0.721 ns | +0.603 ns | −0.118 ns |
| hold WS | +0.106 ns | +0.113 ns | +0.112 ns | −0.001 ns |
| route DRC / LVS / antenna | 0 | 0 | 0 | — |
| KLayout DRC | 0 | 0 | **1** | +1 |

**The corridors do what they are for, and the blocks pay for it almost
exactly.**  BUDA's pins and guides cut the TOP's routed wire by 45 % — the
metric the whole handoff targets, on a design where the router had every
alternative available and 0.73 % congestion — and the pin templates cost the
blocks 37.5 % of their own wire, which is the same effect §8 step 3b measured
on the toy (+49 % there).  Counted the way §7.3 requires (top plus every
block, once per PLACED instance) the arm total moves **+0.6 %**: a wash.  That
is the number §7.3 predicted would be uninformative on its own — "H+B minus H
on the arm total alone would net that against the bus it buys without saying
which side moved" — and it is now measured rather than argued: the block side
is not a rounding error, it is the entire saving.

Whether that stays true at larger N is NOT settled by this run and should not
be guessed: both halves scale with N² (top corridors grow with the array, and
block wire is counted per placed instance), so the ratio need not improve, and
the block-side cost is one a better pin plan could reduce while the top-side
saving is already close to the geometric floor.  The next thing to measure is
this same pair at N = 8, where H+size is already on the table (§8 step 7e).

**On §7.4's floor this arm still fails, and for the third time on the same
metric class**: KLayout DRC 0 → 1, one `m2.2` (met2 minimum spacing) edge pair
at (433.44, 26.97) in `tpu_top`, gap 0.04 µm.  Step 7e's five were all inside
`acc_cell`; this one is at the TOP, so it is the router's own metal rather than
a cell's.  Setup slack also gives up 0.118 ns of a +0.72 ns margin.  Neither is
large and neither is noise, and "H+B ≥ H on every PPA metric" admits no
allowance for either — so the criterion is what has to be argued about (§11
item 1), not the measurement.


**MEASURED at N = 8** (2026-09-07), with the H+size row of step 7e as the
CONTROL — same emitted set (byte-identical `tpu.def`/`tpu.lef`/`tpu_rtl.v`),
same block sizes, same placement, so the delta is the pins and the corridors
and nothing else:

| N = 8 | F | H | H+size | **H+B** | H+B vs H+size |
|---|---|---|---|---|---|
| arm wall | 4,541 s | 6,208 s | 5,023 s | **4,797 s** | −4.5 % |
| top alone | 4,541 s | 5,882 s | 4,615 s | 4,496 s | −2.6 % |
| blocks (wall, parallel) | — | 326 s | 408 s | 301 s | −26 % (noise: ±25 %) |
| die | 1.032 mm² | 6.347 | 3.935 | 3.935 | — |
| utilisation | 46.3 % | 50.8 % | 38.7 % | 38.7 % | — |
| **top wire** | 934,831 µm | 803,897 | 749,932 | **300,704** | **−59.9 %** |
| **block wire** | — | 1,176,440 | 981,616 | **1,382,072** | **+40.8 %** |
| **arm wire** | — | 1,980,337 | 1,731,548 | **1,682,776** | **−2.8 %** |
| setup WS | −0.550 ns | +0.389 | +0.464 | +0.368 | −0.096 ns |
| **hold WS** | +0.091 ns | **−1.075** | −0.238 | **+0.112** | +0.350 ns |
| route DRC / LVS / antenna | 0 | 0 | 0 | 0 | — |
| KLayout DRC | 0 | 0 | 5 | **2** | −3 |

PSM clean on both nets, 168/168 template pins verified in the hardened
blocks, 2,944 guided nets all present in the design, `check_design dnuts`
clean before the handoff.

**The N = 2 question is settled, and favourably.**  At N = 2 the corridors
bought −45.4 % of the top's wire for +37.5 % on the blocks and the arm total
came out **+0.6 %: a wash**, and this doc said plainly that whether the trade
improves with N was not settled and should not be guessed, since both halves
scale with N².  At N = 8 the top saving GROWS to **−59.9 %** while the block
cost holds at +40.8 %, and the arm total turns into a **−2.8 % net win**.  So
the corridor saving scales better than the pin cost does — measured at two
points, one doubling apart, rather than argued.

**H+B against arm H**: better on wall (−23 %), die (0.62×), top wire (−63 %),
arm wire (−15 %), power (−3.6 %) and — the one that matters most — **hold
slack, which H FAILS at −1.075 ns and H+B passes at +0.112 ns** (§11 item 6).
Worse on block wire (+17 %), setup slack (−0.021 ns of a +0.39 ns margin) and
**KLayout DRC 0 → 2**.

**Both remaining markers were one `m2.2`** at `wbuf_cell` local (91.4, 14.9),
in `wbuf_1` and `wbuf_5` — the SAME abstraction notch as the single N = 2
marker and the same class as step 7e's five in `acc_cell`.  So every DRC
violation this arm ever produced traced to one hole in one cell's LEF, not to
the routing, and `notch_obs.py` closed it (§11 item 13): **KLayout DRC 2 → 0
with Magic overlaps still 0, at +11 µm of top wire** — `Flow complete`, no
deferred errors, the first fully clean signoff this arm has produced.

**So §7.4's floor is unmet at N = 8 on ONE metric: setup slack**, 0.0185 ns
under H (+0.3705 against +0.3890) on the notch-fixed arm.  That it is a real
regression rather than measurement scatter is now measured rather than
assumed — an independent repeat of the whole arm reproduced it BIT-IDENTICALLY
(282 metrics present in both, 0 differ, with no key in one and not the other;
`timing__setup__ws` equal to the digit at 0.367518, `route__wirelength` at
300,704, and the signoff counts down to the baseline's own 2 KLayout DRC
errors), so run-to-run timing noise is zero and there is no tolerance for the gap to hide in.  What that leaves is
a tuning question rather than a verdict on the mechanism: this arm has never
been timing-driven — no `PNR_SDC_FILE`, and §5's per-pin timing budgets are
phase 3 and unbuilt — so 0.0185 ns of a +0.39 ns margin is the first thing a
timing-aware run would be expected to move.

**7g. LOOK at a run** (`flow/librelane/snapshots.py`).  Every measurement in
this section is a number, and the study had no way to SEE a run: LibreLane
renders exactly ONE image, of the FINAL layout, while the eighteen
intermediate DEFs a top run writes — floorplan, macro placement, PDN, global
and detailed placement, CTS, global and detailed routing — sit on disk and
nothing ever draws them.  A wrong floorplan is then invisible until a metric
twelve steps later is wrong.

```bash
python3 flow/librelane/snapshots.py <run>/runs/<tag> --list      # what is there
python3 flow/librelane/snapshots.py <run>/runs/<tag> --bdb       # curated set + BDBs
```

The recipes for all three things worth looking at — these stages, BUDA's own
bundles and bit-wires through `btcl -b`, and the pins on both sides of the
template handoff — are collected in
[`flow/librelane/VISUAL_CHECKS.md`](../../flow/librelane/VISUAL_CHECKS.md),
with the sweep over every run on disk and
[`contact_sheet.py`](../../flow/librelane/contact_sheet.py), which puts all
of them on ONE page so the same stage can be compared across N, across arms
and across the variants of one arm (48 runs / 116 images on the current
tree).  Its own fenced blocks are walked by the pasteability guard the way
this file's are.

**Nothing is re-run** — every artefact comes from files the run already
wrote, so it works on runs finished weeks ago and costs seconds a stage.  The
renderer is KLayout's own `render.py`, the one the flow's `KLayout.Render`
step uses, which takes a DEF as readily as a GDS; the tech file, layer
properties, layer map and cell LEFs all come from the run's own
`resolved.json`, so a stage renders in exactly the colours the final render
uses.  (The script lives at a Nix store path inside the image, so it is
located by asking the interpreter, not by hard-coding a path that works until
the next release.)

**A BDB is written only where a BDB is the right container, and the boundary
is measured rather than assumed.**  `import_def_lef` reads COMPONENTS, NETS
and pin connectivity, so a placement stage lands complete — on the N = 8 top,
296 components / 4 cells / 3,586 nets / 6,928 pins at macro placement, rising
to 39,465 / 7 / 3,861 by CTS as tap cells and the clock tree appear, which is
itself worth watching.  But it does NOT read routed geometry:
`net_segment`, `bus_segment` and `net_via` come back **0/0/0** on a routed
DEF, because those tables are BUDA's OWN routing output.  A BDB of a routed
stage would therefore look like an unrouted design and quietly mislead, so
the tool refuses to write one and says why.

The footprints in it are the REAL ones, on the standard cells as well as the
macros (#908).  The first cut concatenated the hardened macro LEFs alone and
passed `allow_missing_footprints`, so at CTS all 39,465 components landed at
the importer's 0.5 × 0.5 µm fallback: a BDB that opens in `bin/fp` with
specks where the cells are, and an HPWL over speck centres.  The PDK's
standard-cell library is already in the run's own `CELL_LEFS`, so naming it
costs nothing — measured on the N = 8 top at CTS, **39,141 of 39,141**
components imported with **0** at the fallback size (tap cells at their real
0.46 × 2.72 µm site) — and with it named the flag can go, which makes the
importer's refusal a guard again rather than a setting that hides a missing
input.  The `def_layer` table comes from the run's own `TECH_LEFS` the same
way, parsed for `TYPE ROUTING` layers with a `DIRECTION`, rather than from a
hard-coded sky130 stack; a run with no readable technology LEF is told which
stack it fell back to and what its `PDK` says.  For somebody else's routing the
DEF is the artefact: `bin/viz <run>/NN-step/<design>.def` opens it, and
`bin/fp <stage>.bdb` opens a placement stage where a macro can be dragged and
the HPWL and flylines move with it.

What the first run of it showed on the N = 8 H+B top, at a glance and with no
measurement: the 8 × 8 PE array, the `feed_cell` column down the west edge,
the `wbuf` row above and the `acc`/pipe rows below — and the wide empty
margins that §7.4's die penalty is made of.

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
* ~~**The pin-DEF writer constrains the bus to the intended pin layer.**~~
  RESOLVED, and in the PLAN rather than the writer, which is where a layer
  is decided.  Measured 2026-09-06 (§8 step 3b): once the macros sit on the
  track period, the planner moves the bus from met3 to met1 — 4× cheaper per
  bit under the declared patterns — and the template's pins follow it off
  the layer the block-side handoff is supposed to use.  A 0.8 µm placement
  change was enough to flip it, and the block's own wire went +26 % → +49 %.
  `TOP` is a PREFERENCE the cost function outvotes, and neither existing
  layer constraint reaches one bus (`set_cell_layer_cap` governs everything
  a cell routes; an NDR `layers` restriction needs a rule that also
  constrains width, spacing or shielding, which `def_ndr` enforces).  So
  **`set_bus_layers <prefix>|* <layers>`** is the knob — longest prefix
  wins, intersected with whatever else governs the bundle, applied at
  bundling for flat and re-applied after every hier policy resolution — and
  **`emit_pin_def … expect_layer <csv>`** is the check that it held,
  refusing to write a template whose pins left the layer.  `pins.buda`
  declares both; §8 step 3b's number wants re-measuring with them.
* **A pin DEF is ALL OR NOTHING** (§8 step 7f, measured 2026-09-07):
  declaring `FP_DEF_TEMPLATE` makes LibreLane skip `OpenROAD.IOPlacement`
  entirely, so a port the template omits is placed by nobody and
  `GPL-0326` refuses the run — and `FP_TEMPLATE_MATCH_MODE permissive`
  does not rescue it, it only downgrades the report.  So the pin writer
  must be handed the port set of the module the BLOCK RUN synthesizes, not
  the one the plan happens to route: on the tier-1a array that meant
  reading the synthesizable netlist (`clk`, `rst` and all) rather than the
  emitter's structural view, and letting `emit_pin_def` spread the ports no
  bus reaches.  A block-config writer that emits a template must therefore
  also know the block's full interface.
* **A template pin owns its metal, and a template MERGES instances**
  (§8 step 7f): each instance contributes the pins it routes, so two nets
  can land on one rectangle where no single instance would — 8 of a PE's 32
  south-face pins, from two independently planned bundles.  The writer
  refuses to emit that (BUDA-1715); a placement writer that later moves a
  macro has to re-derive rather than transform.
* **On an ARRAY the track-phase fix is not free** (§8 step 7f): the rule
  above about placing a macro at a clearing phase has a twin for TRACKS,
  and the toy's answer (move the instance onto the period) costs a whole
  period per COLUMN and per ROW on an array — ~9 % of the die at N = 8 —
  because the pitch is what has to move.  `emit_pin_def snap` pays at most
  half a period of jog at the face instead, which is why the array uses it
  and the toy did not need to.
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

1. ~~The **success criterion** in §7.4 — confirm or replace the numbers.~~
   Decided 2026-09-08 (§7.4): the floor is H+B ≥ H on the ARM TOTALS and
   on signoff (per-metric was wrong for a trade that moves wire into the
   blocks by design); the die-within-10 %-of-F clause is struck as a
   flat-flow target a hard-macro flow cannot reach by tuning (3.81× at
   N = 8, tens of percent available), the penalty reported instead; the
   ≥ 2× wall-time crossover with setup within 0.5 ns of F stays as the
   study's question.  Both of the floor's N = 8 failures are now
   RESOLVED, one by a fix and one by a measurement, leaving ONE:

   * the KLayout DRC notch (#896) is **CLOSED** — `notch_obs.py` claims
     the 1.2 µm² per cell the abstract omits and takes DRC 2 → 0 with
     Magic overlaps still 0 and +11 µm of top wire (§11 item 13);
   * the 0.021 ns setup loss **counts, measured** — an independent repeat
     of the whole arm is bit-identical to the run it repeats (273 shared
     metrics, 0 differ), so run-to-run timing noise is zero and there is
     no tolerance to absorb the gap.  It is now 0.0185 ns against the
     notch-fixed arm (+0.3705 vs H's +0.3890).

   So H+B at N = 8 passes every gate on the floor except setup slack, by
   0.0185 ns of a +0.39 ns margin.  What is open is what to DO about that:
   a real regression this small is a tuning question (the arm has never
   been timing-driven — no `PNR_SDC_FILE`, §5's phase-3 per-pin budgets
   unbuilt), not evidence the mechanism costs timing.  Also open: the die
   arithmetic's two levers (channel first, then padding) as the
   reported-not-gated cost.
2. **sky130A** unless told otherwise.
3. ~~Vehicle~~ — decided: the ladder in §7.1, tiers 1a and 1b first, both
   for concrete runtime numbers; Chisel is acceptable.  **H+size exists at N = 8** (§8 step 7e: die 3.935 mm², wall 5,023 s —
   four metrics better than H, KLayout DRC 0 → 5 worse, still 3.81× F's die
   and 1.11× its wall).  It is NOT the H+B point: it carries BUDA's sizes
   but not its pins or corridors, and its DRC regression breaks §7.4's own
   floor, so the criterion is unmet twice over.  **H's wall-time gap
   to F is closing as N grows** (§8 step 7d: H/F 2.72× at N = 4, 1.37× at
   N = 8), which is what solve-once predicts.  That is NOT §7.4's crossover
   — that one is about H+B beating F by ≥ 2× (within 10 % die area as it
   read then; the die clause is since struck, item 1), and the arm
   measured is H, slower than F at both points and at 6.15× its die.
   N = 16 would show whether H reaches parity, and is the run the 20 GB
   container was raised for.  **H+B now exists at N = 2** (§8 step 7f: all
   three contributions — sizes, `FP_DEF_TEMPLATE` pins, corridors — with
   168/168 template pins verified in the hardened blocks, PSM clean on both
   nets, route DRC 0 and 1 KLayout `m2.2`), and against its own CONTROL
   (H+size on the same array) BUDA's pins and corridors cut the TOP's wire
   **−45.4 %** for **+37.5 %** on the blocks, i.e. **+0.6 %** on the arm
   total — a wash, which is the number §7.3 said would be uninformative
   alone and is now measured.  So §7.4 can be argued about against a real
   arm; what is missing is that pair at the N where F is slow, since N = 2
   is far below any crossover.  ~~Open: the tier-2
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
5. **The container needs ~20 GB for N = 8 and above** (§8 step 7d): nine
   parallel STA corners and Magic's abstract-LEF write each want several
   GB, and at 8.2 GB both are killed — Magic silently, leaving a 0-byte LEF
   that a later step reports as an unhandled `StopIteration`.  Raised here;
   a machine running the benchmark needs it set before N = 16.
6. **H fails HOLD at N = 8** (−1.075 ns, three slow corners) while F fails
   SETUP (−0.55 ns).  Both are the clock being too fast for the PE's
   single-cycle MAC at this corner set, but H's is at the hardened
   boundaries, which is §2.4's uniform `IO_DELAY_CONSTRAINT` arriving as a
   number.  Decide before the benchmark: relax the sweep clock so both arms
   are clean at every N (the F rows at 20 ns would want re-running), or
   keep 20 ns and report a sweep in which both arms miss timing at N = 8 for
   different reasons.  Per-pin budgets (phase 3) address H's half only.
7. **Arm H+size's two residuals at N = 8** (§8 step 7e): 5 KLayout `m2.2`
   met2-spacing violations in a repeating pattern, and hold at −0.238 ns.
   Both are deferred errors on an otherwise clean run (route DRC, LVS,
   antenna 0), and the hold half is the same clock question as item 6.
   The DRC half is #896 (all five at `acc_cell` local (69.4, 0.0), next to
   its own met4 VGND pin); `drc_locate.py` on the run's `.lyrdb` + top DEF
   + `acc_cell.lef` says per edge whether the offending metal is on a
   shape the LEF claims, in a hole of the abstract, or outside the box.
   Run on the artefacts (PR #900) it is an abstraction NOTCH, not any of
   the issue's three experiments: Magic's LEF leaves the corner where pin
   `in[22]`'s rect ends and the met2 OBS begins uncovered (x 69.37–69.65,
   y 0–0.56), the macro's real met2 sits in it, the router reads the notch
   as free and overhangs the top's wire into it — 0.130 µm against m2.2's
   0.140; the same notch beside `wbuf_cell`'s `rst` gives the N=2 H+B run
   its one marker (0.040 µm).  The block's own DRC is 0 because the partner
   shape is the top's wire.

   **The DRC half is CLOSED** (2026-09-10, run tag `hsnt`; §11 item 13):
   `notch_obs.py` takes it 5 → 0 with illegal overlaps still 0 and top wire
   +0.067 %.  Both candidates this item used to name were then MEASURED and
   are the wrong answers — the `-bloat_occupied_layers` abstract adds
   met4/met5 OBS the PDN cannot cross (125,800 power-grid violations, never
   routed), and growing the met2 OBS to a blanket closes the DRC and costs
   **6,233 illegal overlaps**; what works is claiming only the metal the
   abstract omits.  **The hold half stays open** at −0.220 ns (60
   violations, improved from −0.238 / 67 but still a deferred error), and
   it is item 6's clock question, not an abstract question.
9. **Two bundles for one cell-local link** (found on the way to §8 step
   7f, not chased): at N = 2 the row's activation chain comes out as TWO
   hbundles — `hb-11 D1 cell:row_cell "DRV:row_0/pe_0|REC:row_0/pe_1"
   nets=8 [row_0, row_1]` and `hb-14 D1 cell:row_cell
   "DRV:row_1/pe_0|REC:row_1/pe_1" nets=8 [row_1]`.  Together they carry
   each row's 8 nets exactly once, so nothing is double-routed, but the
   first one's instance list claims both rows while its nets are row_0's,
   and the two route to different cell-local positions — which is what
   `emit_pin_def`'s reference merge then has to paper over (a 46 µm jog on
   `a_in[0]`).  If a replica that should have merged did not, the template
   would agree with every instance and that jog would be zero.  Worth a
   look at `HierarchicalBundler`'s replica merge before reading the
   BUDA-1714 counts as the cost of hierarchy.

10. ~~**`pdn_phase.py`'s verdicts cannot be acted on**~~ REWRITTEN (#895):
   the old model counted every same-layer meeting as a defect (the "144
   clips" FAIL on the plan that works, §8 step 7e) and called a via-less
   strap fragment stranded; following it turned a working H+B design into
   a `PSM-0069` failure.  It now predicts pdngen's own steps, read from
   OpenROAD's `src/pdn` — cut (`Shape::cut`, spacing across and halo along
   the layer's wire axis), via (`Grid::getIntersections`, the macro's own
   pins injected by `getInstancePins`), TRIM (`PdnGen::trimShapes`: a
   fragment with fewer vias than `Shape::isRemovable` requires is removed —
   2, or 1 on a pin layer, and `PDN_ENABLE_PINS` makes both connect layers
   pin layers; `PDN_SKIPTRIM` skips it and harm.py sets that for BLOCKS
   only) — and partitions what survives with `pdn_connect.py`'s OWN
   `net_components`, so prediction and post-mortem share one definition of
   "connected".  It reports TRIMs as information, STRANDED terminals (no
   rectangle of the pin on its net's grid — a LEF PIN is one terminal,
   which is why the phase-0 toy passes PSM with two VGND rectangles off the
   grid) and FLOATING fragments (survive trim off the grid: the "N
   unconnected shapes" PSM counts), and offers only a VERIFIED shift.  On
   the phase-0 toy: x=10 PASS and x=20 FAIL (u0's VPWR stranded), both as
   measured.  Still ADVISORY — a prediction from the LEFs and the config —
   and the verdict stays OpenROAD's `PSM-0040`/`PSM-0069`.  **Validated
   both ways on the N=8 artefacts** (PR #900, three run reports):

   | on the N=8 artefacts | `pdn_phase.py` (prediction) | `pdn_connect.py` (written DEF) | PSM |
   |---|---|---|---|
   | harm.py's own plan | PASS, 104/104 terminals on the grid | 208 connected, 0 floating | `PSM-0040` both nets |
   | `PDN_HOFFSET 109.3` | FAIL: 64 stranded in 64 instance-nets | 144 connected, 64 floating (64 unsourced) | `PSM-0069`, 512 shapes |

   The arithmetic closes on both sides: each pe_cell's VGND strands on a
   component of its own 8 pin rects, × 64 = the 512 shapes PSM counts, and
   the old code FAILed the working plan (144 "clips").  Getting there took
   two reader fixes the artefacts exposed: the DEF `PINS` reader wanted
   `;` on its own line (OpenROAD writes it on the `PLACED` line; 0 of 324
   pins read, sources silently off), and the terminal verdict was "a via
   landed" where PSM's is "a chain reaches a source" — on the failing plan
   every pe_cell VGND rect has a same-net 2.0 × 2.0 µm crossing with the
   macro's own met5 pin and pdngen DID via it — 1,024 of them in that cell
   (§7.2; the "made no via there" this sentence carried until 2026-09-11
   was the #905 misreading, and the via joins the macro's own two pins,
   which feeds nothing).  What that cell has none of is a via onto a
   STRAP: the met5 VGND strap is cut across every `pe_cell`, so nothing
   the supply enters through reaches those rects: `unsourced`.  "One
   blob" and "the supply reaches it" are different questions, and only
   the second is PSM's.  The prediction's remedy on that plan was
   `dy=+1.6` (`PDN_HOFFSET=107.7`) and that was FALSIFIED — the corrected
   cut rule asks +1.605 (107.695), which `check_grid.tcl` measured PASS
   on both nets with 107.7 and 109.3 both measured FAIL as controls
   (item 12).
13. **Both candidate fixes for the #896 notch FAIL, each differently**
   (measured 2026-09-07 on the N = 8 H+B arm, one top run each).  §11 item 7
   named two: read OpenROAD's bloated abstract at the top, or grow the met2
   OBS to cover the macro's real metal.  Neither survives contact.

   * **`<cell>.openroad.lef` (the whole abstract) destroys the PDN.**  It
     does close the notch — a single `4.67,0 – 91.47,60` rect covers it —
     but it adds blanket OBS on **met4 and met5**, where Magic's LEF has
     NONE, and those are the top's PDN layers.  pdngen drops every strap
     that crosses an obstruction, so the macros lose their supply:
     **125,800 power grid violations**, the flow stopping at
     `Checker.PowerGridViolations` before routing mattered.  That is §7.2's
     own rule ("foreign metal on a PDN layer is the dangerous kind")
     reproduced by the proposed fix.  It also needs producing first —
     `OpenROAD.WriteViews` is not in the Classic flow, so the file never
     exists (`write_abstract.tcl` makes it from a block's final ODB).
   * **The layer-scoped form (met2's OBS only, `patch_obs.py`) fixes the
     DRC and breaks extraction.**  It clears the PDN gate (`PSM-0040` both
     nets) and takes **KLayout DRC 2 → 0** at **+0.15 % top wire**
     (300,704 → 301,145 µm) with timing and die unmoved — and then Magic
     reports **6,233 illegal overlaps against the baseline's 0**, every one
     of them `Illegal overlap between obsm2 and metal2 (types do not
     connect)`.
   
   The second failure is the informative one: a blanket met2 OBS
   CONTRADICTS what this arm does, because the top routes met2 — that is
   where `emit_pin_def` puts the N/S bus pins, so the router must reach met2
   on the macro's faces and cross met2 over it.  Declaring the whole layer
   obstructed and then routing on it is exactly the overlap Magic counts.

   So the fix cannot be "obstruct the layer"; it has to be **"obstruct the
   metal the abstract omits, and nothing else"** — the boolean difference
   between the macro's real met2 GDS and Magic's met2 OBS, added as rects.
   That is the surgical version of the same idea, it leaves every place the
   top legitimately routes met2 free, and it is the next thing to try.
   `notch_obs.py <cell.gds> <magic.lef> <out.lef>` computes it — the
   cell's met2 rectangles from its GDS, flattened through SREF/AREF, minus
   every pin and OBS rect the LEF claims, appended to the OBS as RECTs;
   tested on a synthetic cell carrying exactly the `in[22]` notch, where it
   claims that one rectangle and nothing else, and it refuses a shape that
   is not a rectangle rather than boxing it.

   **MEASURED 2026-09-08, and it is the fix.**  On the N = 8 H+B arm:

   | | KLayout DRC | Magic overlaps | PDN | top wire |
   |---|---|---|---|---|
   | Magic's LEF (baseline) | 2 | 0 | clean | 300,704 µm |
   | whole abstract | — | — | **125,800 violations** | never routed |
   | met2 blanket | 0 | **6,233** | clean | 301,145 µm |
   | **`notch_obs.py`** | **0** | **0** | clean | **300,715 µm** |

   Eleven microns across a 300 mm route — +0.004 %, against the blanket's
   +0.15 % — with die and hold unmoved and setup a shade BETTER (+0.3705
   vs +0.3675 ns).  `Flow complete`, no deferred errors: the first fully
   clean signoff this arm has produced, and #896 closes on it.

   Its precondition did not hold on the real cells, which is worth
   recording because the refusal was RIGHT and still blocked every one.
   The tool needs the macro's metal as rectangles and will not box a
   polygon — but Magic streams routed metal as rectilinear BOUNDARYs (a
   wire with a jog is one 12- or 14-corner polygon), **982 of them on
   `pe_cell` alone**, so all four cells were refused.  `rectify_gds.py`
   decomposes them into rectangles first (a trapezoid decomposition of an
   axis-aligned polygon IS rectangles over the same area) and compares the
   region area before and after, refusing to write if they differ —
   identical on all four cells, after which `notch_obs.py` runs unmodified.
   The claim it then makes is 1.2 µm² per cell where the blanket claimed
   5,208, and the notch goes from 98 % uncovered to 0 %.

   **It is now a STEP rather than a hand recipe** (#907).  The measurement
   above cost three hand operations per cell per run — rectify, notch, edit
   `MACROS.<cell>.lef` — and a step someone skips brings the marker back
   with nothing saying so, because the block's own DRC is 0: the partner
   shape is the top's wire.  `flow/librelane/tier1a/notch.sh N` runs both
   tools per leaf cell and writes `<cell>.notch.lef` beside Magic's, and
   `harm.py` points the generated top config's `MACROS.<cell>.lef` at that
   file — so the step cannot be skipped at all: a top run without it stops
   at once on a LEF that is not there.  A cell whose GDS the decomposition
   cannot handle is refused LOUDLY, with no stale `.notch.lef` left standing
   from a previous run and the hand recipe in the generated README's step 2.

   That the automation reproduces the measurement is measured, not assumed:
   re-run over the N = 8 artefacts it produced all four `.notch.lef` files
   **byte-identical** to the hand-run ones the table above was measured on,
   in 17 s for the set.

   **That claim was about the `.lef`, and the `.gds` did not hold it until
   #918.**  Re-confirmed at N = 4 on 2026-09-10 — three cells' `.notch.lef`
   came back byte-identical to the ones the H+B top actually consumed — but
   the `<cell>.rect.gds` written beside them did not reproduce: two
   derivations of one source gave identical byte SIZE and different SHA256
   (`pe_cell.rect.gds`, 1,836,104 bytes both times, 248 differing bytes).

   The cause was NOT GDSII.  Every differing byte sat in a `BGNLIB` or
   `BGNSTR` modification/access time field, because `rectify_gds.py` ended
   in a bare `ly.write(out)` and KLayout stamps the current time into those
   records by default — while `src/gds_io.cpp` has always zeroed exactly
   those fields (`zero12()`), so the repository already had the convention
   and this one script was not following it.  The first draft of this note
   said the file "cannot be" reproducible, which was wrong in the way that
   matters: it blamed the format for a property of one call (Codex, PR
   #918).  It now writes with `gds2_write_timestamps = False`, and BOTH
   artefacts reproduce — measured over two runs on all four cells,
   `.rect.gds` and `.notch.lef` byte-identical each time, with the fix
   changing nothing but the headers (868 bytes differ from a pre-fix file,
   **all** of them in those two record types, none outside, and the
   `.notch.lef` derived from it identical to the abstract the H+B top
   consumed).

   One residual, and it is the reason to keep the distinction in mind: a
   `.rect.gds` written BEFORE this change carries real timestamps, so it
   will not match a fresh one however deterministic both runs are.  Compare
   an old artefact by geometry, not by digest.

   **It was also the H+size arm's only DRC** (measured 2026-09-10, run tag
   `hsnt`).  That arm was the one dirty cell left in §7.3's table at 5
   KLayout markers, and `drc_locate.py` reads them as ONE defect repeated
   per instance — `acc_cell` local ~(69.4, 0.1), both edges in an abstract
   hole, nearest claimed shape pin `in[22]` at 0.010 µm — character for
   character the #896 notch.  Re-running the top against the patched
   abstracts (the blocks were already hardened, so `notch.sh` plus the top
   alone):

   | | baseline `h` | notched `hsnt` |
   |---|---|---|
   | KLayout DRC | **5** | **0** |
   | route wirelength | 749,932 | 750,432 (+0.067 %) |
   | route DRC | 0 | 0 |
   | hold WS / violations | −0.2380 ns / 67 | **−0.2200 ns / 60** |
   | setup WS | +0.4637 | +0.4762 |
   | die area, instances | 3,935,420 / 528,542 | 3,935,420 / 528,536 |

   So the fix carries across arms: +0.067 % of top wire here against
   +0.004 % on H+B, and timing moves the right way on both hold measures
   rather than being traded away.

   Two things this run does NOT say, both worth stating because the flow
   still exits non-zero.  The run ends on a DEFERRED ERROR for **hold
   violations** in two corners — and so did the baseline (`top/h.log`
   line 14544, the same checker on the same corners), so that is
   pre-existing and slightly IMPROVED, not a cost of the patch; what left
   the deferred set is the DRC.  And `RUN_MAGIC_DRC` is `False` here — as
   it is on every top run in this tree — so there is no `magic__drc_error__count`
   for either row.  That is NOT the overlap number §7.4's floor gates on:
   the overlap check rides Magic's stream-out rather than its DRC deck and
   reports `magic__illegal_overlap__count`, which IS present on both runs
   and is **0 → 0**.  Which is the check that mattered here, since the
   rejected met2 BLANKET closed the same DRC and cost 6,233 illegal
   overlaps doing it: the surgical patch closes it and adds none.

12. **`pdn_phase.py` detects, but its REMEDY is wrong** (measured
   2026-09-07 on the N = 8 artefacts).  The model now fails the
   `PDN_HOFFSET 109.3` plan correctly — 64 stranded terminals, matching
   PSM's 512 shapes — and offers a verified-looking fix with it: *"shifting
   EVERY macro by dy=+1.600 (`PDN_HOFFSET=107.7`) leaves nothing predicted
   to fail"*.  It does not.  Built and checked, the 107.7 plan gives
   `PSM-0069` on VGND with **512 unconnected shapes whose coordinate list
   is byte-identical to 109.3's** — the remedy moved nothing that mattered,
   though it did apply (the met5 straps moved exactly 1.6 µm, 120180 →
   118580 DBU).  `pdn_phase.py` predicts `PASS` for it.  The detection
   half was validated first and the remedy half only later — the
   *historical* remedy, `dy=+1.600` (`PDN_HOFFSET=107.7`), was measured
   WRONG, and the 1.605 one that replaced it measured right (the table
   below).  A remedy that looks verified is worse than none: §11 item 10's
   whole lesson was that acting on this tool by hand broke a working
   design.  So the FAIL line says so itself ("is PREDICTED to leave
   nothing failing.  A HYPOTHESIS, not a fix … judge it with
   check_grid.tcl"), and the generated README calls it the shift the MODEL
   predicts.  That wording stays even though the search is now checked at
   one point: one measured offset on one design does not make the shift
   SEARCH verified, only this answer from it.  Test an offset it names
   with `check_grid.tcl`, which costs minutes:

   ```bash
   cd n<N>/h/top
   librelane --dockerized --run-tag pdnX --to OpenROAD.GeneratePDN config_X.json
   # Resolve the ODB first: a glob inside an `ODB=...` word is not expanded
   # (bash passes `*.odb` through literally; zsh errors "no matches found").
   ODB=$(ls runs/pdnX/*-openroad-generatepdn/*.odb | head -1)
   ../../../../phase0/measure/run_or.sh runs/pdnX ../../../check_grid.tcl ODB="$PWD/$ODB"
   ```

   That check is itself validated against a known failure (the 109.3 run's
   own ODB: 512 shapes, `PSM-0069` on VGND, VPWR clean — the signoff
   verdict, from step 21 instead of step 56).

   **Why it passed 107.7** — read out of `src/pdn/src` rather than run
   (#904, 2026-09-09), four places the model differed from `Shape::cut`
   and `Shape::writeToDb`, all fixed: (1) the rtree query that finds a
   pin's obstruction is a CLOSED box intersection, so a strap whose edge
   sits exactly two spacings from the pin is cut — the model spared the
   touch, and its shift candidates ARE the boundaries, so it returned
   exactly the shifts pdngen rejects (107.7 is 109.3 less one met5
   spacing); (2) the length lost is the pin plus the halo plus TWO
   spacings (the obstruction rect grown by the strap's own spacing), not
   one; (3) the same-net spare is unreachable for a macro under
   `define_pdn_grid -macro`, whose `GridObsShape` copy of the pin carries
   no net; (4) with `PDN_ENABLE_PINS` every surviving fragment on a
   connect layer is written as a pin shape — a PSM source — so a
   component is fed iff it holds one surviving fragment, a fragment off
   the largest component is reported and not failed, and `PDN_SKIPTRIM`
   still loses a via-less fragment at the write (PDN-0200).  On the
   phase-0 toy the remedy moved from -1.1 to -1.105 (the grid step a
   closed cut needs) and the y-remedy from -5.0 to -5.005.

   **The corrected remedy is MEASURED (2026-09-11), and it passes.**  Both
   failing neighbours were put through the same check first, because a
   check that passes everything proves nothing:

   | `PDN_HOFFSET` | `pdn_phase.py` predicts | `check_grid.tcl` / PSM measures |
   |---|---|---|
   | 109.3 | FAIL — 64 stranded in 64 instance-nets | **FAIL** VGND `PSM-0069`, 512 `PSM-0038` shapes; VPWR PASS |
   | 107.7 | FAIL — asks a further **dy=+0.005** | **FAIL** VGND `PSM-0069`, 512 shapes; VPWR PASS |
   | **107.695** | PASS — 104/104 both nets | **PASS both nets**, `PSM-0040`, 0 failing |

   Three for three, the 0.005 µm distinction included, which is the whole
   of #904.  Run `runs/pdn107695`, config `config_107695.json`, both under
   `hb/n8/h/top/`.

   The cause is now readable off the ARTEFACTS rather than only out of
   `src/pdn/src`, and it is item (1) above with a number on it: the met5
   VGND strap is CUT across every `pe_cell`, surviving only in the
   32.76 µm channels between them, so `pe_cell` VGND carries **1,024
   pin-on-pin vias and ZERO vias onto a strap** while VPWR carries 384
   onto straps.  The cut is a spacing TOUCH — the strap band ends at
   cell-local 103.28 and the nearest different-net met5 pin (VPWR) starts
   at 104.88, a gap of **exactly 1.60 µm** against met5 min spacing 1.6.
   That is why the shift is 1.605 rather than 1.6 and why 107.7 was 0.005
   short.  The failure is also NET-ASYMMETRIC — **VPWR is clean at every
   one of the three offsets** — so it is visible only to a check that
   reports PER NET.  `check_grid.tcl` does exactly that (it runs
   `check_power_grid` per net and fails if either does), which is why it
   catches this; what would hide it is a check reading a single pooled
   number, a total shape or via count across both nets, where VPWR's
   health dilutes VGND's 512.

   One cost, since the remedy is not free: the ~107.7 region emits **7
   `PDN-0110`** ("no via inserted between met4 and met5"), all on VPWR,
   where 109.3 emits 0 — **the same seven**, not merely the same
   count: identical coordinates and net in both runs (seven x positions,
   all at y = 894.78, all VPWR), so the 0.005 correction demonstrably
   neither introduced nor moved them.  How WIDE the offset band that
   carries them is, is unmeasured — two points cannot say — and PSM passes
   VPWR regardless.  `PDN-0195` is 0 at all three.

   The
   pin-on-pin question (#905) resolved the same way, and then the grep
   was actually run: nothing in the source declines the pair —
   `InstanceGrid::getIntersections` injects the pins, the generator builds
   a 2.0 × 2.0 crossing with one cut, and nothing removes it — and the DEF
   carries **2,664** placements of exactly that via with zero
   `PDN-0110`/`PDN-0195`.  So the "none of 512" reading was the wrong one
   and the README's earlier "every rect carried one" was right.  Either way that via
   sources nothing: it joins the macro's pins to each other, and the
   island is fed only by a surviving strap fragment.

11. **The 5 % pass threshold of measurement A** (§8 step 5) is a number
   read off two runs of one toy.  It should be re-read on the first real
   vehicle (tier 1a, N=4): if the gcell-edge and pin-access share does not
   scale with the design, 5 % stays; if it does, the threshold is the wrong
   shape and the check should exclude the terminal gcell at each pin.
