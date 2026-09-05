# BUDA in a LibreLane hierarchical RTL-to-GDS flow — proposal

**Status: PROPOSAL, nothing built.**  Written 2026-09-05 against LibreLane
3.0.11 (`librelane/librelane` @ `33ab648`) and its CI vehicles
(`librelane/librelane-ci-designs`).  Every LibreLane fact below was read from
that source, with the file named; every claim about OpenROAD's routers that
could not be read from a document is marked as something phase 0 must
MEASURE.

## 1. The idea, restated

A default RTL-to-GDS run is FLAT: synthesis flattens the Verilog hierarchy and
place-and-route sees one sea of cells.  The proposal is to keep the hierarchy
the RTL already declares:

1. synthesize each large-enough module separately, just far enough to know
   its area;
2. hand BUDA the resulting blocks and the top-level netlist, and let it decide
   what a flat flow decides blindly or not at all — where each block goes,
   how big it is, **where its pins sit on which face**, and where the buses
   between blocks run;
3. harden each block under those constraints (in parallel), then integrate;
4. benchmark against the flat run.

The idea is sound, and it maps onto machinery LibreLane already has.  What
follows is (a) what that machinery is, (b) where BUDA fits and what it must
emit, (c) four places where the framing should change, and (d) a benchmark
that can find the answer instead of confirming one.

## 2. What LibreLane has today

The `Classic` flow (`librelane/flows/classic.py`) is a fixed step sequence:
Verilator lint → `Yosys.Synthesis` → `OpenROAD.Floorplan` →
`Odb.ManualMacroPlacement` → PDN → `Odb.AddRoutingObstructions` →
`OpenROAD.IOPlacement` / `Odb.CustomIOPlacement` / `Odb.ApplyDEFTemplate` →
global placement → CTS → `OpenROAD.GlobalRouting` → `OpenROAD.DetailedRouting`
→ RCX → STA → stream-out → DRC/LVS.  OpenROAD links the netlist flat
(`link_design` without `-hier`, `scripts/openroad/common/io.tcl:208`).

The pieces a hierarchical flow needs are all present, but the DECISIONS are
manual or blind:

| Concern | LibreLane mechanism | Where the decision comes from today |
|---|---|---|
| Which modules stay hierarchical in synthesis | `SYNTH_HIERARCHY_MODE flatten\|deferred_flatten\|keep`, `SYNTH_KEEP_HIERARCHY_MIN_COST` (gate-count threshold), `SYNTH_KEEP_HIERARCHY_MODULES/INSTANCES` (`steps/pyosys.py:482-509`) | user, or a gate-count threshold |
| Block area estimate | `design__instance__area` from Yosys `stat` after synthesis (`pyosys.py:587`) | synthesis |
| Hardened block as a macro | `MACROS` config: `gds`/`lef`/`nl`/`spef`/`lib` + `instances{name: {location, orientation}}` (`config/variable.py:107`, `docs/source/usage/using_macros.md`) | user |
| Macro placement | `Odb.ManualMacroPlacement` writes `name x y orient` and fixes them (`steps/odb.py:404`).  **No automatic macro placer in Classic.** | user, by hand |
| Macro halo | `FP_MACRO_HORIZONTAL/VERTICAL_HALO`, default 10 µm | default |
| Block pin placement | `OpenROAD.IOPlacement` (auto), `Odb.CustomIOPlacement` (`FP_PIN_ORDER_CFG`: ordered pin names/regexes per side, `min_distance`), `Odb.ApplyDEFTemplate` (`FP_DEF_TEMPLATE`: copies die/core area and **exact non-power pin locations** from a DEF; `strict`/`permissive`) (`odb.py:254,653`; `openroad.py:1286`) | per block, with no knowledge of the top level |
| Block die size | `FP_SIZING absolute` + `DIE_AREA`, or `relative` + `FP_CORE_UTIL` | user |
| Keep-out areas | `FP_OBSTRUCTIONS` (hard placement), `PL_SOFT_OBSTRUCTIONS` (soft placement, `openroad.py:1125-1135`), `ROUTING_OBSTRUCTIONS` (layer rects → `dbObstruction`, removed after DRT; `odb.py:560`) | user |
| Global routing | `global_route` (FastRoute) with `set_macro_extension`, writes `after_grt.guide` (`scripts/openroad/common/grt.tcl`).  OpenROAD's `grt` also has **`set_nets_to_route`** (route only a listed subset) and **`read_guides`** | tool |
| Detailed routing | `detailed_route` (TritonRoute) with `-or_seed 42` (`scripts/openroad/drt.tcl`); no guide file passed explicitly, guides live in the DB | tool |
| I/O timing budgets | `base.sdc:19-45`: `set_input_delay` and `set_output_delay` = `CLOCK_PERIOD × IO_DELAY_CONSTRAINT / 100` **for every pin uniformly** | one number for the whole block |
| Hierarchical STA | macro `nl`+`spef` (preferred) or `lib`, else black-box; the guide's own warning is "errors at the boundary" (`using_macros.md` §STA) | — |
| Metrics | `design__instance__area`, `design__die__area`, `design__instance__utilization`, `timing__setup__ws/tns`, `timing__hold__ws`, `power__total/internal/switching/leakage`, `route__wirelength`, `route__drc_errors`, `magic/klayout__drc_error__count`, per-step `runtime` (`steps/step.py:289`) | — |

So the hierarchical flow EXISTS in LibreLane as "harden macros, then integrate",
and its three unautomated decisions — macro placement, block pin placement,
and where the buses run — are exactly BUDA's job.  Block SIZING is a fourth:
`flow/tcl/tpu.tcl` learned that a block narrower than its own bus strands the
bus however much channel it gets (672 of 832 bits, and widening the channel
made it worse), so the size must come from the buses as well as the area.

Two more facts shape the plan.  The CI vehicles are regression designs, not
benchmarks: the largest are `aes_core` (~5.4k lines), `picorv32a` (~3k, one
big module), `usb_cdc_core`, `salsa20`; none is large enough for a flat run
to hurt.  And several carry the shape BUDA's bottom-up path exists for —
**repeated instances of one module**: `PPU` (`Sprite` ×8), `salsa20`
(`salsa20_qr` ×4), `manual_macro_placement_test` (`spm` ×2, the macro-flow
smoke test).

## 3. The proposed flow

```
                 top.v (+ submodule RTL)
                        │
   [S0] Yosys.Synthesis, SYNTH_HIERARCHY_MODE=keep         one run, hierarchy kept
        (or SYNTH_KEEP_HIERARCHY_MIN_COST=N)                → per-module area from `stat`
                        │
   [B1] BUDA: import top netlist + block areas             import_verilog + generated LEF
        size blocks (area/util AND face capacity)          (the tpu_lib rule, generalized)
        place blocks (PlacementOptimizer SA/GA)            FloorplannerEngine, headless
        run_hier_bundler → generate_hier_topologies →
        run_planner hier → run_nuts → run_detailed_nuts    the existing pipeline
                        │
        emits, per block:   DEF template (die area + PINS at exact positions)
                            DIE_AREA
                            [phase 2] per-pin input/output delay budgets
        emits, for the top: MACROS instances {location, orientation}
                            OpenROAD guides for the bus nets  (emit_guides, new format)
                            PL_SOFT_OBSTRUCTIONS under corridors (export_def_blockages density)
                        │
   [S1] per block, IN PARALLEL: LibreLane Classic with
        FP_SIZING=absolute, DIE_AREA, FP_DEF_TEMPLATE=<block>.def
        → gds/lef/nl/spef                                  one hardening per CELL, not per instance
                        │
   [S2] top: LibreLane Classic with MACROS (+ FP_PDN_MACRO_HOOKS),
        set_nets_to_route <all but bus nets> ; read_guides buda.guide
        → hierarchical STA with nl+spef
```

S0 is one synthesis run, not one per module: `keep` (or the gate-count
threshold, which is LibreLane's own notion of "large enough") gives every
module's area in a single `stat`, and the threshold IS the partition.  This is
cheaper than per-module runs and cannot disagree with itself.

S1 hardens each distinct CELL once.  A module instantiated eight times is
hardened once and placed eight times — hardening time is O(cells), not
O(instances), which is where a hierarchical flow's runtime win actually
comes from, and it is the same solve-once-copy premise as BUDA's bottom-up
planning (`set_bottom_up`, `align_bottom_up`, `check_template_tracks`).  The
two compound: BUDA plans the cell's interface once, LibreLane hardens it
once.

## 4. Interfaces: what BUDA emits, and whether it exists

| BUDA output | LibreLane input | Exists in BUDA? |
|---|---|---|
| Block placement | `MACROS.<cell>.instances.<inst>.location/orientation` (JSON) or `MACRO_PLACEMENT_CFG` | trivial writer, NEW |
| Block size | `DIE_AREA` with `FP_SIZING absolute` | sizing rule exists in `tpu_lib.tcl` (face-capacity aware); needs generalizing into a command, NEW |
| Block pins at exact positions | `FP_DEF_TEMPLATE` (DEF with DIEAREA + PINS + placed rects) | NEW writer: for each net bit landing on a block, face + coordinate + layer from the DNUTS `net_segment` endpoint at the busterm; transform to block-local coordinates through the instance orientation (`orient_rect.py` has the rule) |
| Bus corridors as route guides | `read_guides` (net name + layer rects) after `set_nets_to_route` on the rest | `emit_guides` writes the same content as JSON/CSV/`create_route_guide` Tcl; NEW: OpenROAD guide format |
| Placement keep-out under corridors | `PL_SOFT_OBSTRUCTIONS` | `export_def_blockages density` writes it as DEF `PLACEMENT + PARTIAL`; NEW: the tuple list LibreLane takes |
| Bus wiring as pre-routes | DEF `NETS ... + FIXED` wiring for the bus nets | NEW, and the only item with real risk (§6) |
| Per-pin timing budgets | `set_input_delay`/`set_output_delay` per pin in `PNR_SDC_FILE` | NEW capability (§5.4) |
| Block placement engine, headless | — | `PlacementOptimizer.run_sa/run_ga` bound to Python; `floorplanner_commands.optimize_placement` exists; no `.buda` command yet |
| sky130 layer stack | — | `import_lef_tech` reads the tech LEF |

Everything in the "trivial/exists" rows is file formats LibreLane already
consumes, so the block-side integration (size, placement, pins) carries no
tool risk at all.  The top-side integration (corridors) has three mechanisms,
ranked in §6.

## 5. Where I would change the framing

### 5.1 "Increase all the PPA metrics" is not the hypothesis to test

A hierarchical flow structurally PAYS for what it gains.  It pays area (macro
halos — 10 µm default per side, channels between blocks, pin-access rows,
no cross-boundary logic sharing) and often timing (no re-buffering across a
hardened boundary, boundary paths budgeted by a fixed number).  It gains
runtime (blocks harden in parallel, each P&R is small), memory, reuse (a
cell hardened once), predictability, and the ability to ECO one block.

On every design in LibreLane's CI set, a flat run will beat a hierarchical one
on area and timing, with or without BUDA.  That is not a finding against
BUDA; it is the shape of the trade.  The claim BUDA can make is narrower and
testable: **BUDA shrinks the hierarchical penalty** — better pin placement
and planned buses mean shorter top-level wires, fewer detours, less congestion
at the block edges — **and so moves the crossover** (the design size above
which hierarchical wins) to the left.  The benchmark should be built to find
that crossover, which means it needs a vehicle that SCALES (§7), and it needs
three arms, not two (§7.2), so that "hierarchy" and "BUDA" are measured
separately.

### 5.2 "Fast-mode synthesis" is the wrong knob

Synthesis is not the expensive stage; P&R is.  A normal-quality synthesis
with hierarchy kept (`SYNTH_HIERARCHY_MODE keep`) costs about what a flat
one does and gives every module's area in one `stat`.  `SYNTH_ELABORATE_ONLY`
does no mapping and so reports no area; a degraded synthesis gives a degraded
area, and the block sizes are what everything downstream is built on.  One
kept-hierarchy synthesis is both faster than per-module runs and the same
netlist the flat arm uses, which removes a variable from the comparison.

### 5.3 "Preroutes" should mean guides first, wires second

BUDA's DNUTS output is bus wiring at track resolution, and emitting it as
DEF FIXED wiring is the highest-fidelity handoff.  It is also the one
mechanism whose tool behaviour is undocumented: neither `grt`'s nor `drt`'s
README says what happens to a net that already carries FIXED wires.  I
believe TritonRoute treats FIXED shapes as immovable, and I am not going to
build on a belief.

The mechanism the routers DO document is guides: `grt` has
`set_nets_to_route` ("only the nets defined in this command are routed") and
`read_guides`, so the top-level run can global-route everything except the
bus nets, read BUDA's guides for the bus nets, and let detailed routing seat
every net inside its guide.  BUDA already computes exactly the guide content
(`emit_guides`: per bundle, per segment, layer + rectangle + the nets that
traverse it — per-bit tapered, so a fan-in branch names only its bits); the
new piece is the OpenROAD guide file format.  Guides leave the exact track
choice to TritonRoute, which is a strength here (it knows the PDK's DRC), and
they compose with the existing `PL_SOFT_OBSTRUCTIONS` writer to keep cells
from being placed under a corridor.

FIXED wires stay on the list as phase 3, after phase 0 has measured what the
routers do with them.  Obstructions (`ROUTING_OBSTRUCTIONS`) cannot reserve a
corridor for particular nets — they block everything — so they are not a
pre-route mechanism at all; they are for keep-outs.

### 5.4 Without per-pin timing budgets, the "performance" column measures the SDC default

Every block's I/O is budgeted by one number, `IO_DELAY_CONSTRAINT`, and the
macro guide warns that this is where hierarchical timing goes wrong.  In a
hierarchical arm, the boundary paths' slack is therefore set by that default,
not by anything BUDA did.  BUDA has no timing model, but it knows each bus's
planned length and layer, which is enough for a wire-delay estimate (Elmore on
the PDK's per-layer RC; LibreLane's `OpenROAD.DumpRCValues` prints the values
it uses) and hence a per-pin `set_input_delay`/`set_output_delay` that is
derived from geometry instead of a percentage.  This is real new capability
and I would keep it OUT of the first benchmark — but the first benchmark's
timing column must then be read with that caveat stated, or we will attribute
the SDC default to BUDA in one direction or the other.

## 6. The corridor handoff, ranked

| # | Mechanism | What BUDA writes | Tool risk | Fidelity |
|---|---|---|---|---|
| A | **Guides** | OpenROAD guide file for bus nets; `set_nets_to_route` for the rest | LOW: both commands documented; phase 0 verifies `read_guides` ADDS to the DB's guides rather than replacing them | router picks tracks inside BUDA's corridor |
| B | **FIXED wires** | DEF `NETS` with `+ FIXED` wiring + `VIAS` for the bus nets | UNVERIFIED: FIXED preservation undocumented in `grt`/`drt`; needs a via-name mapping from the tech LEF (BUDA's `net_via` carries x/y/layers, not a via name) | BUDA's exact bit-wires |
| C | **Obstructions** | `ROUTING_OBSTRUCTIONS` / `PL_SOFT_OBSTRUCTIONS` | none | keep-out only; cannot say "this net here" |

Phase 0 runs A and B on a two-block toy and reports what each router
actually does.  The proposal commits to A; B is adopted only if measured.

## 7. The benchmark

### 7.1 Vehicle

None of the CI designs is big enough to show a crossover, so the benchmark
needs a design that scales, and the honest option is one whose scale is a
parameter: a **systolic array with real MAC PEs**.  `flow/tcl/tpu.tcl` already
generates the physical shell (Verilog + DEF + LEF at any N, the buses derived
from the datapath widths) and is a QoR-corpus row; what it lacks is
synthesizable PE RTL (a multiply-accumulate with the weight/psum/activation
pipeline — small).  With that, one parameter sweeps N = 2, 4, 8, (16), and
each N is run three ways.  This vehicle also has the repeated-cell shape that
makes S1 O(cells): 64 PEs are ONE hardening.

For a smoke test that needs no authoring: `manual_macro_placement_test`
(`spm` ×2, already a macro-flow design) and `salsa20` (`salsa20_qr` ×4).

### 7.2 Three arms, not two

| Arm | Synthesis | Blocks | Placement | Pins | Buses |
|---|---|---|---|---|---|
| **F** flat | `flatten` | none | GPL | `IOPlacement` | GRT |
| **H** hierarchical, no BUDA | `keep` → harden | per cell | `ManualMacroPlacement` from a simple grid / hand layout | per-block `IOPlacement` (no top-level knowledge) | GRT |
| **H+B** hierarchical with BUDA | `keep` → harden | per cell, BUDA-sized | BUDA `PlacementOptimizer` | BUDA `FP_DEF_TEMPLATE` per block | BUDA guides (A) |

H isolates what hierarchy alone costs; H+B minus H is BUDA's contribution.
Without H, a good H+B result cannot be attributed and a bad one cannot be
diagnosed.

### 7.3 Metrics, all from LibreLane's own `metrics.json`

Per arm and per N: wall-clock (total, and per-stage — for H arms the
block-hardening stage is reported both as wall time with parallelism and as
CPU-sum), `design__die__area`, `design__instance__utilization`,
`timing__setup__ws`/`tns` (with the §5.4 caveat), `power__total` (+ the
breakdown), `route__wirelength`, `route__drc_errors` + signoff DRC counts,
plus BUDA's own end-of-run triple for H+B.  The report is one table per N and
a crossover plot: runtime and each PPA metric versus N, three lines each.

### 7.4 What "worth it" means should be fixed BEFORE the runs

E.g.: at the largest N that flat completes, H+B within X% of flat on area
and timing at ≤ 1/Y the wall time; or H+B beats H on every PPA metric at
every N.  A number chosen after seeing the data proves nothing.  This is the
first of the questions in §10.

## 8. Phases

**Phase 0 — feasibility (days).**  Get LibreLane running: this container has
no nix and no docker daemon (client only), so the x86_64 AppImage devshell is
the only in-container path and is unverified; a Linux box with nix or docker
is the safe route.  Run `spm` flat and `manual_macro_placement_test` (the
macro contract) as delivered.  Then the two measurements everything depends
on, each on a hand-written two-block toy:
  (i) `read_guides` after `set_nets_to_route`: does DRT route the guided nets
      inside the guides, and are the DB's existing guides kept?
  (ii) DEF `+ FIXED` bus wires: does `grt` skip them and does `drt` keep them?
Plus one no-risk check: a hand-written pin DEF through `FP_DEF_TEMPLATE`.

**Phase 1 — plumbing.**  The NEW rows of §4 (placement/size/pin/guide
writers, an `optimize_placement` command or Python driver, sky130 stack via
`import_lef_tech`) and one orchestrator (`tools/librelane_hier.py`: S0 →
BUDA → S1 in parallel → S2), proven on the smoke vehicles end to end with
`check_design` clean and LibreLane's signoff clean.

**Phase 2 — the benchmark.**  PE RTL for the systolic vehicle; the three
arms across N; the §7.3 table and crossover plot; a write-up that states the
§5.1 trade as measured rather than as hoped.

**Phase 3 — what the data says next.**  Per-pin timing budgets (§5.4) if the
timing column is the story; FIXED-wire pre-routes (B) if phase 0 measured
them workable and guides left QoR on the table; bottom-up marks
(`set_bottom_up`) on the repeated cell so BUDA's interface planning is also
solve-once.

## 9. What BUDA gains regardless of the benchmark's answer

The block-side writers (size, placement, pins) turn BUDA from a planner whose
output is a report into one whose output is CONSTRAINTS a mainstream open
flow consumes unchanged — the "advisory writers" (`emit_guides`,
`export_def_blockages`) finished.  And a real synthesized design through
`import_verilog` is a reader vehicle of a kind the tree does not have: every
netlist here is either authored or uniquified.

## 10. Open questions (asked, not assumed)

1. **Success criterion** (§7.4) — what result would make this worth pursuing
   past the benchmark?
2. **PDK** — sky130A (LibreLane's default; 5 metals, met1 H / met2 V /
   met3 H / met4 V / met5 H, macros usually routed to met4) or gf180mcu?
   It sets BUDA's layer stack and the legal pin layers.
3. **Where LibreLane runs** — a Linux box with nix or docker, or should
   phase 0 first try the AppImage in this container?
4. **Vehicle** — author real PE RTL to make `tpu.tcl` synthesizable, or build
   the scaling vehicle from an existing design (e.g. N × `picorv32`)?
5. **Pre-route semantics** — guides first (§5.3), FIXED wires after phase 0
   measures them: agreed?
6. **Timing budgets** — out of the first benchmark with the caveat stated
   (§5.4), or in scope from the start?
