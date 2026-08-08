# BUDA Codebase Analysis

Date: 2026-08-08, refreshed same-day after the risk-reduction plan
(Phases A–D + follow-ups) completed (rewrite of the 2026-05-26
original — the codebase has
moved far past that snapshot: the BDB-centric v3 architecture, the
hierarchy-aware pipeline, the measured-feedback healers, GDS interchange,
the interactive Floorplanner, and the chip-scale QoR/runtime tooling all
landed after it.)

## Executive Summary

BUDA is an EDA interconnect planning system for chip design. It bundles
nets into buses, generates candidate routing topologies, selects
topology/layer assignments with a congestion-aware global planner, and
resolves physical track positions down to individual bit-wires respecting
power-grid and pre-route blockages.

The core engine is C++20 exposed to Python via pybind11; Python provides
the `.buda` script CLI, session orchestration, matplotlib visualization,
and persistence into a SQLite-backed physical design database (**BDB**).

Two entry flows share the pipeline:

1. **Flat flow** — declare blocks and nets in a `.buda` script, bundle,
   generate, plan, place (the original demo flow).
2. **Hierarchy-aware flow** — open/build a BDB (`import_def_lef`,
   `import_verilog`, `import_gds`, or the interactive Floorplanner),
   derive busterms, and run the `hier` pipeline variants: templates are
   solved once per cell type and instantiated at every occurrence, with
   opt-in **bottom-up** solve-once-copy planning, per-cell layer caps and
   fractional layer shares, and cross-level bundling.

Beyond the one-pass pipeline there is a measured-feedback **healer loop**
(`negotiate_congestion`, `ripup_reroute`, `refine_selection`) that reads
the *actual* placement failures (NUTS overlaps, DetailedNUTS opens) and
re-plans against them, and a `check_design` audit that types every
violation. Quality is guarded by a 41-flow QoR corpus
(`tools/qor_corpus.py`): `--compare` fails when ANY flow regresses on
overlaps/unplaced/viol_bundles (improvements pass; wirelength and
runtime are reported informationally). Running it base-vs-branch is the
standard bar for any routing-affecting change — and a change that
*claims* to be behavior-neutral is held to the stricter observed
outcome: 0 better / 0 worse / all unchanged with wirelength exactly ±0.

## Repository Layout

- `src/` — C++ engines + pybind bindings; `buda_cli.py`; the CLI command
  registry (`buda_cmds/`), session mixins (`buda_session/`), and the
  visualizer façade + mixins (`buda_viz.py`, `viz_explorer/`, `viz_main/`).
- `bin/` — launcher/build wrappers: `bb` (build), `buda` (routing CLI),
  `fp`/`bfp` (Floorplanner), `viz`, `u2b`, `activate`.
- `flow/` — R&D / regression `.buda` vehicles (`rnr/`, `chip/`,
  `hbundles/`, `big_data_test/`, shared track fixtures in `tracks/`).
- `demo/` — user-facing demo vehicles (quickstart, ariane/mempool/nvdla/
  ispd19 showcases).
- `docs/` — user guides, per-stage script reference
  (`docs/script_reference/`), and `docs/internal/` design/measurement
  notes (the "why is it built this way" record).
- `test/tests/` — pytest + pytest-bdd suites (~240 test files, 52
  `.feature` specs); checked-in BDB fixtures as diffable `*.bdb.sql`.
- `tools/` — Floorplanner GUI, DEF/LEF visualizers, QoR corpus/table,
  BDB converters (`bdb2buda`, `buda2bdb`, `bdb_edit_bus`), forensics.
- `qor/` — the checked-in QoR snapshot (`qor_table.md`) and runtime
  sidecars.

## Build And Runtime Model

CMake builds **three** artifacts (not one):

| Target | Kind | Contents |
|---|---|---|
| `buda_core` | shared library | BDB + SQLite + busterm + bundler + refiner — the single compiled copy of the DB-layer types |
| `buda_db` | Python module | registers BDB / row types / BustermGen in pybind11's global registry |
| `buda` | Python module | full routing pipeline; imports `buda_db` and re-exposes its names |

Both extension modules link the same `buda_core`, giving pybind one
`std::type_info` per class — `buda.BDB` objects pass into `buda` C++
functions taking `BDB&` without type-info mismatches. New DB-layer types
register in `buda_db` (via `bind_db.cpp`), not `buda`.

`bin/bb` performs incremental builds (`-c` clean, `test`/`mid`/`slow`
tiers); compiled `-O3 -march=native -Wall -Wextra` (CI pins `-march` via
`BUDA_ARCH`; MSVC `/O2` — a manual `Windows validation` workflow builds
and tests natively on Windows). GitHub Actions gates every PR with build
+ full suite (~5 min).

**Threading.** `buda --threads N` (`-j`) is one knob for every parallel
stage; the default is half the machine's logical CPUs
(affinity- and cgroup-quota-aware). It sets the `BUDA_THREADS` governor
honored by the auto paths of: the planner's parallel candidate scoring
(`BUDA_PLAN_THREADS`), NUTS per-layer solves (`BUDA_NUTS_THREADS`), the
healers' parallel trial sweeps (`BUDA_SWEEP_THREADS`), and parallel hier
candidate generation (`BUDA_TOPO_THREADS`). Every parallel path is
**decision-identical** (usually print-identical too) to its sequential
twin by construction — the corpus byte-identity gate depends on it.

## Core Data Flow

```
BDB (SQLite): components · cells · pins · nets · busterms · bundles
      │ derive_busterms / add_blocks_from_bdb        (hier flow)
      ▼
[1] Bundler        nets → Bundles / HBundles (STRICT | CONVERGENT |
                   BIDIRECTIONAL | COMBINED; per-prefix permissions;
                   balanced bit-bound splits; fan-in trees)
[2] TopologyGen    candidate L/Z/U/trunk/MST/BITRUNK shapes on the Hanan
                   grid; coverage/antenna/pinch gates; TopoEdit sessions
[3] Planner        congestion-aware topology selection + layer assignment
                   (STRICT ladder → rip-up → ALLOW_OVERFLOW → BEST_EFFORT;
                   parallel candidate scoring; per-cell layer policies)
[4] Abstract NUTS  1.5-D rectangle packing → per-segment track positions
[8] Routing Grid   per-layer track patterns (power/signal layout)
[9] Detailed NUTS  per-bit wires on concrete signal tracks + per-bit vias
[V] verify         check_topo / check_nuts / check_dnuts (typed violations)
 ⟲  Healers        negotiate_congestion · ripup_reroute · refine_selection
                   (measured-feedback re-plan loops between 3↔4/9)
```

Stages persist into the BDB as they run (bundles, candidate topologies,
planner decisions, bus routing, bit routing), so `load_pipeline` can
resume a fresh session from a checkpoint taken after
`generate_topologies`, `run_planner`, or `run_nuts` (+`ripup_reroute`) —
as deep as was persisted, including the hier flow's expanded
per-instance view — with the setup (layers/patterns/blocks) re-declared
first; see `docs/BDB_REFERENCE.md` for the supported resume points.

## Major Modules

### Bundler (`bundler.h/cpp`, `busterm.h/cpp`)

Flat `Bundler` groups nets by driver/receiver signatures; four
strategies form a lattice (STRICT ⊂ {CONVERGENT, BIDIRECTIONAL} ⊂
COMBINED, the last a union-find over both relations). The
`HierarchicalBundler` bundles each net once at its most specific
endpoints using BDB busterms; cell-level bundles become reusable
templates. Multi-driver CONVERGENT groups route as per-bit tapered
**fan-in trees**. `set_max_bundle_bits` splits oversized bundles into
balanced parts (static, per-prefix-scoped, or `auto` from busterm-edge
capacity), applied to templates before expansion.

### Topology generation (`topology.h/cpp`, `topology_analysis.h/cpp`, `topo_edit.h/cpp`)

`TopologyGenerator` emits L/Z/U/UU, trunk+branch, MST, trunk+MST hybrid,
and opt-in two-level BITRUNK shapes; Hanan-line trunk loci are
default-on. Since the topo/conn unification the six connectivity
derivation passes live in `topology_analysis.cpp` and cache on the
`Topology` (content-fingerprint-validated — the fingerprint doubles as
the persisted `topo_uid` candidate identity). Every generation path ends
in gates: coverage (no silent `BUSTERM_OPEN`), disconnected-island,
undeclared-feedthru-relay, BITRUNK anchoring, and opt-in
dominance-pruning / locus-dedup / dangling-drop cleanups. MST relay
completion wires relay hubs physically (extension, jog, merge, or the
opt-in collector spine) so wirelength is honest and no block is silently
used as a feedthrough. `topo_edit.cpp` provides transactional expert
edits (the `edit_*` CLI commands and the explorer's edit mode) with
op-log provenance persisted to the BDB. Per-bundle generation fans out
on a C++ thread pool (`generate_candidates_batch`, GIL released),
print- and decision-identical to the sequential loop.

### Congestion planner (`congestion_planner.h/cpp`)

Greedy widest-first commit over Hanan-grid cuts with band capacities;
overflow is a **hard constraint** enforced by a per-bundle escalation
ladder (STRICT → targeted rip-up-and-replan → ALLOW_OVERFLOW →
BEST_EFFORT, each escalation LOUD). Cost terms are tunable
(`set_planner_param`: kCong/kSpan/kWL/kSegs(Rel)/kPeak/kBalance/
kHeight/…; `band_span_charge` spreads a too-wide bus across the bands it
covers; `charge_pull_target` keeps the books honest against NUTS's pull
placement). Signal-track capacity mode charges discrete track counts.
Per-cell **layer caps/shares** (`set_cell_layer_cap/share`,
`set_layer_caps_by_depth`, `reserve_top_layers`) band-limit a cell's own
interconnect, enforced inside the ladder. Candidate scoring runs on
worker threads with an ordered reduction that replays the serial
compare — decision-identical at any thread count. Hier mode expands
cell-level templates to per-instance wrappers, plans top-down with
demand reservations, and runs a default refinement pass; bottom-up
marked cells solve once per rotation class and copy to instances.

### Abstract NUTS (`nuts.h/cpp`, `nuts_dogleg`, `nuts_geom`, `placed_segment.h`)

Sweep-line 1.5-D packing per layer with preferred-fit placement
(alignment siblings, junction anchoring, pull targets clamped at
wirelength breakpoints), EDF repacking on failure, corner-overlap
resolution with delta-based accept guards, scoped span settling, a
repeat-state convergence guard, and dogleg adoption. Per-layer solves of
one orientation group run on worker threads behind a size gate
(result-identical by audit). The placed-segment type hierarchy
(`PlacedSegmentBase` → `TrackSegment`/`NetSegment`/`PreRoutedSegment`)
unifies stage-4/stage-9 output with materialized pre-routes.

### Routing grid + Detailed NUTS (`routing_grid.h/cpp`, `detailed_nuts.h/cpp`)

`RoutingGridStack` models repeating track patterns (POWER/GROUND/CLOCK/
SHIELD/SIGNAL slots, region overrides, keepouts). `DetailedNUTSEngine`
places each bus segment's bits on concrete signal tracks (span-clear
tracks first), span-adjusts bits to their junction partners, culls
keepout-crossers, and emits per-bit vias. Two measured-accept final
heals run by default: the cull heal (escalate cull-doomed LOW segments)
and the TOP re-seat heal (move supply-doomed TOP seats to a layer that
can host them) — both componentwise-accepted and corpus-guarded.

### Healers (`buda_session/ripup.py` + `trial_sweep.cpp`)

- `negotiate_congestion` injects measured overlap/open locations as
  demand on the exact planner bands and re-plans both parties unpinned
  (PathFinder-style history; opt-in `press` retries).
- `ripup_reroute` is a first-improving hill-climb over contenders'
  candidate alternates with a fixed-context screen, fast trials, an
  incremental replan per trial, global-occupant and bottom-up
  template-class passes, dead-span escalation folds, and a convergence
  guard. Its dominant trial volume — the deferred stall-certificate
  sweep AND the primary screened scan — evaluates on a C++ thread pool
  (`parallel_sweep`, per-move private state, replay-confirmed accepts),
  byte-identical to the sequential path including printed lines and
  trial counts.
- `refine_selection` polishes realized wirelength end-of-flow under a
  componentwise accept.

### BDB (`bdb.h/cpp`, `gds_io.cpp`, persistence mixins)

SQLite-backed store for hierarchy, netlist, busterms, bundles, candidate
topologies (with logical seg-busterm/seg-conn annotations — reloads
never re-derive connectivity from geometry), planner decisions, bus and
bit routing, layer policies, and session meta. Self-contained DEF/LEF
and Verilog parsers (DEF placement + Verilog hierarchy merge), plus
tested GDSII import/export round-trip (label-based net recovery, layer
mapping, deterministic bytes). Checked-in test data is diffable
`*.bdb.sql` text. Persistence is batched into single transactions;
expanded-instance re-persists are selective (fingerprint dirty-tracking)
with busterm-row dedup. The remaining interchange roadmap item is the
OpenAccess bridge (spec'd, gated on the proprietary OA libraries).

### CLI (`buda_cli.py`, `buda_cmds/`, `buda_session/`)

The 2026-05 monolith concern was addressed by splitting three ways:
`buda_cli.py` keeps the session core (dispatch, flow-log capture,
per-command summaries, `--threads` resolution); `buda_cmds/` is the
command registry (one module per stage, duplicate registration a hard
import error, unknown commands fail fast); `buda_session/` holds the
session helpers as six disjoint mixins (persist, hier, nutsflow, edit,
reports, ripup). Adding a command = C++ class + binding + one `cmd_*`
handler registered in its stage module.

### Visualizer (`buda_viz.py`, `viz_explorer/`, `viz_main/`, `viz_common`, `viz_window`)

Mirrors the CLI split: a façade module plus mixin packages for the
topology explorer (edit mode, analysis overlays, sidecar sync, debug
cost view) and the main visualizer (artist-registry highlighting,
abstract/detailed/pre-route layers). The explorer is part of the
planning workflow: commits from its edit mode persist USER candidates
with op-log provenance.

### Floorplanner (`floorplanner.h/cpp`, `placement_optimizer.h/cpp`, `tools/bdb_floorplanner.py`)

A separate interactive placement tool over the BDB: drag/resize/align
editing, SA/GA placement optimization with per-block constraints,
validation, live HPWL/flylines, and Run Flow hand-off into the hier
routing pipeline. Launched via `bin/fp` / `bin/bfp`.

### Tooling (`tools/`)

`qor_corpus.py` (the 41-flow QoR sweep + `--compare` regression gate,
parallel `-j`), `qor_table.py` (the checked-in snapshot + nightly diff
sidecar), `bdb2buda`/`buda2bdb`/`bdb_edit_bus` (BDB ↔ flat-script
converters and netlist surgery), `build_hier_demo.py` (assembles
hierarchical demo BDBs up to true 3-level chip vehicles),
`unit2buda`/`render.py` (test → visual repro), `doomed_seat_forensics`,
DEF/LEF cluster visualizers, and macOS `.app` bundle generation.

## Script Language Surface

The `.buda` command set has grown to 95 commands across setup,
BDB/hierarchy construction, bundling, generation (+ per-bundle,
additive, and edit-session variants), planning (params, pins,
super-candidate group pins), NUTS/DNUTS, healers, layer policies,
verification, reporting, and GDS interchange. It is documented in
`docs/BUDA_SCRIPT_REFERENCE.md` (index + per-stage pages under
`docs/script_reference/`), with the authoritative one-table summary in
the repository `CLAUDE.md`. Unknown commands are a hard error, as are
silently-dangerous redefinitions (duplicate net/block/layer/pattern
names).

## Testing Surface

Three cumulative pytest tiers (markers in `pytest.ini`): **fast**
(~1 870 tests, <30 s — the default), **mid** (+~600 flow-script/BDB
round-trip integration, ~6 min), **slow** (+SA/GA storms and healer
end-to-end agreement runs). `bin/bb test|mid|slow` builds then runs a
tier; pytest-xdist parallelizes when installed. 52 Gherkin `.feature`
specs (pytest-bdd) map arcs to executable scenarios with a tag-vocabulary
guard. Beyond unit/integration tests, the **QoR corpus** is the routing
regression instrument: every topology/planner/NUTS change runs
base-vs-branch and must show no endpoint regression and (for
transparency-classed changes) byte-identical decisions. CI runs build +
full suite on every PR; Windows correctness is validated by a manual
workflow.

## Engineering Findings

### Strengths

1. **Measured-change discipline.** The corpus gate, byte-identity
   validation for parallel paths, and `docs/internal/` measurement notes
   (including rejected experiments with their numbers) make regressions
   rare and reasoning reconstructible. Defaults get flipped only after
   corpus-wide A/B (and several measured-worse features deliberately
   stay opt-in).
2. **Fail-loud philosophy.** Hard errors on typos and redefinitions,
   LOUD warnings at every planner escalation, typed `check_design`
   violations, and report-only audits that never silently filter.
3. **Coherent staged architecture, now with a feedback loop.** The
   pipeline is still the clear spine; the healers close the loop against
   measured reality instead of model estimates, which is where most of
   the QoR wins of the last quarter came from.
4. **Resumable persistence.** The BDB checkpoints every stage deeply
   enough that sessions resume mid-pipeline, including hier templates,
   USER candidates with provenance, and bottom-up copies.
5. **Parallelism with determinism.** Every thread pool (planner scoring,
   NUTS layers, healer sweeps, candidate generation) is
   decision-identical by construction — performance work has not eroded
   reproducibility.
6. **The 2026-05 structural complaints were addressed**: `BudaSession`
   was split into a registry + mixins, the visualizer likewise; bundle
   endpoints are first-class (`net_drivers`/`net_receivers`, fan-in
   contracts checked by `NET_DRIVER_OPEN`); sidecar JSON gave way to BDB
   persistence; directories were flattened (`bin/`, `qor/`, `assets/`).

### Risks And Weak Spots

The five risks this section named in the 2026-08-08 first edition were
addressed by the executed
[risk-reduction plan](internal/risk_reduction_plan.md) (Phases A–D +
follow-ups, all landed):

1. **Mutable pybind surface** → cross-field invariants now validate at
   every stage entry suite-wide (`BUDA_VALIDATE`, `validate_wrappers`);
   `w.pin()`/`w.unpin()` intent methods keep the coupled pin fields
   atomic, with an allowed-writers test pinning the discipline; the
   snapshot-coverage contract makes an unclassified new bound field a
   loud test failure.  Residual: raw writes remain legal at sanctioned
   pin-establishing sites — the guard is checked discipline, not types.
2. **Print-identity oracle** → healer correctness claims ride the
   structured decision trace (`_decision(tag, **kv)`; identity tests
   compare records); `BUDA_DECISION_TRACE` and `qor_corpus --decisions`
   diff runs decision-wise.  One byte-level log canary per area is
   retained deliberately.
3. **`ripup.py` concentration** → carved into seven cohesive modules
   (driver + rr_state / rr_trials / rr_sweeps / negotiate / refine /
   util, 3 430 → ~2 100 lines in the driver), every move gated
   byte-identical.  The seams now carry the contracts (snapshot
   coverage at rr_state, fast-trial semantics at rr_trials).
4. **Scale-sensitive runtime** → `tools/runtime_ab.py` makes the
   two-class (rnr + chip) measurement one command; `qor_corpus
   --compare` rolls runtime up per flow class.  The nightly-corpus
   instrument remains open (below).
5. **Doc staleness** → CI guards: repo-relative links must resolve and
   every registry command must be documented (`test_docs_guards.py`).

What remains true rather than solved:

- The validator/trace/guard net is **checked discipline** — it catches
  the historical bug classes loudly, but the binding still permits raw
  mutation where sanctioned, and a genuinely novel misuse pattern needs
  a new invariant added.
- The C++ engine-entry validator twin was deliberately **not** built
  (no violation class has needed sub-stage granularity); revisit only
  on evidence.
- The healers' correctness story now rests on three layers (trace
  identity, stage-entry invariants, corpus endpoints) — powerful, but
  worth keeping in mind that the corpus remains the only gate that sees
  QoR.

## Fresh Analysis, Lens B — Fit Into An Existing EDA Methodology

*Code-grounded (parsers, exporters, CLI read directly), 2026-08-08.*
The question here is not "is the algorithm good" but "could a CAD group
drop this into a production flow next quarter". The honest summary:
**BUDA today is a self-contained planning environment with its own world
model, not a flow-participating point tool.**

**What already integrates.** Placement/connectivity ingestion is real —
`import_def_lef` reads genuine vendor DEF (the checked-in
`demo/ariane/ariane.def` is an Innovus 21.11 floorplan) including
`UNITS`, `DIEAREA`, `COMPONENTS` with orientation, escaped names, and
`NETS` tuples, and `import_verilog` overlays hierarchy while preserving
DEF placement (`src/bdb.cpp:1297`, `:1663`). GDSII round-trips with
deterministic bytes and label-based net recovery (`src/gds_io.cpp`).
The BDB is versioned (`SCHEMA_VERSION = 22`) with forward migrations
that **refuse to open a newer database** rather than corrupt it
(`src/bdb.cpp:516`). Cross-machine reproducibility is a design property,
not an accident: no RNG in the engine, decision-identical parallelism
under test, and a canonical route fingerprint hashing net *names* rather
than autoincrement ids (`src/buda_session/persist.py:417`).

**The blocking gaps, ranked by how much they block:**

1. **There is no DEF writer** (BLOCKER). The only export path is
   `export_gds`; no `write_def`, no `+ ROUTED` emitter exists anywhere.
   BUDA's routes therefore cannot be handed back to Innovus/ICC2/Fusion
   — GDS rectangles carry no net identity a P&R tool can re-adopt, so
   the output is a picture, not a constraint. The data to emit is
   already in `net_segment`/`net_via`; the writer is ~medium effort, but
   it is only *useful* after gaps 2–3.
2. **No technology data is ingested** (BLOCKER). LEF parsing recognizes
   `MACRO`/`SIZE`/`PIN`/`DIRECTION`/`USE`/`RECT` and ignores everything
   else — `LAYER` rules, `VIA`/`VIARULE`, `OBS`, `SITE`,
   `MANUFACTURINGGRID`, `ORIGIN` (`src/bdb.cpp:1174`, `:1195`). Pin
   geometry is collapsed to a centroid, discarding layer and multi-port
   shape; `USE POWER|GROUND|CLOCK` pins are dropped outright. In DEF,
   `TRACKS`, `GCELLGRID`, `ROW`, `PINS`, `SPECIALNETS`, `VIAS`,
   `BLOCKAGES`, `NONDEFAULTRULES` are all unparsed — the ariane DEF
   *contains* 20 real `TRACKS` statements and `SPECIALNETS`, every one
   discarded. Instead the layer stack is **hand-typed** per flow
   (`def_layer` + `def_track_pattern`; see `flow/chip/chip_tracks.buda`,
   whose comments warn the values must be binary-exact). Reading tech
   LEF is medium effort; *honestly modelling* it is large, because
   `TrackSlot` is `{type,label,width,space_after}` and has no slot for
   min-area, EOL spacing, parallel-run-length tables, or cut rules.
3. **Vias are symbolic points and nothing is DRC-anything** (BLOCKER for
   signoff). `net_via` is `(from,to,x,y)` with no cut count, enclosure,
   or via name; GDS export renders each as a default **1.0 µm square**
   (`src/gds_io.cpp:977`). There is no min-width/spacing/area,
   manufacturing grid, antenna (the `ANTENNA` violation type is a
   *topological* predicate, not the process rule), timing, SDC, Liberty,
   SPEF, or power intent anywhere. Layer assignment is span- and
   congestion-driven, never slack-driven.
4. **Blockages are invisible** (HIGH, and the most surprising omission
   for a routing tool): DEF `BLOCKAGES`, LEF `OBS`, macro `HALO`,
   `PINS`, and `SPECIALNETS` power straps are all unread, so the planner
   cannot see obstacles a real design is full of. Keepouts must be
   re-declared by hand.
5. **The CLI cannot signal design failure** (HIGH — blocks CI/regression
   adoption). Verified empirically: a script with reported violations
   exits **0**, while a malformed script exits 1 with a raw Python
   traceback. BUDA fails loudly on *syntax* and silently on *quality* —
   backwards for a flow harness, which can catch its own typos but
   cannot see 252 unplaced bits. There is also no machine-readable
   per-run report (reports are prose; the only JSON is the opt-in
   decision trace), and no Tcl interface — the industry-standard harness
   language — though the command registry is already a dict and would
   map onto one mechanically.
6. **Importer robustness** (MEDIUM). The DEF reader is line-at-a-time
   `std::regex` with no statement-level tokenizer, no
   `COMPONENTS n`/`NETS n` reconciliation, and silent fallbacks: a cell
   missing from the LEF becomes a **0.5 × 0.5 µm** block
   (`src/bdb.cpp:1408`) and an unmatched instance is skipped
   (`:1461`) — a wrong-LEF run yields a plausible, entirely wrong
   floorplan with no diagnostic. It has never been exercised on a
   full-chip post-place DEF (10⁶–10⁸ lines).
7. **Operational fit** (LOW–MEDIUM, but immediate). Flow logs are
   silently overwritten unless `--log`/`--tag` is passed; error and
   warning counts are computed by **substring-matching the words
   "error"/"warning"** in captured output (`src/buda_cli.py:337`), so a
   net named `error_flag` inflates the count. Block coordinates are
   integer µm by construction (`add_block` hard-errors on a fractional
   value), with float→int rounding happening upstream in the DEF→script
   generator. No packaged wheel; `BUDA_NO_APP=1` should be the batch
   default on macOS.

**The strategic read.** Gaps 1, 2, 4, 5, 6 are *engineering*, not
research — a focused team could land them in a quarter or two, and doing
so moves BUDA from "internal prototype" to "tool a CAD group can pilot
on a real block". Gaps 3 and 5's signoff half are the real cost, and
they mark a fork in the road worth choosing **explicitly**:

- **Advisory planner** (recommended): export DEF *guides, blockages and
  bus corridors* rather than final routes. Needs 1, 2, 4, 5, 6 only;
  sidesteps via/DRC modelling entirely; and matches what the tool is
  genuinely good at — early feasibility, bus corridor planning, layer
  budgeting before detailed routing exists. This is a legitimate,
  valuable product category.
- **Flow-participating router**: needs real vias, a DRC-legality
  checker, and DBU-integer geometry — multi-quarter, and it puts BUDA
  into direct competition with mature detailed routers.

The advisory framing also resolves the QoR-at-scale tension below: a
corridor plan with residual unplaced *bits* is still a useful corridor
plan, whereas a final route with 252 unplaced bits is not a route.

## Current Improvement Directions

Tracked in `docs/internal/wishlist-*.md`, `docs/internal/opens*.md`, and
the per-arc notes; the active headline items (the risk-reduction plan
itself is complete — see above):

1. **Chip-flow runtime** (`docs/internal/chip_flow_parallelism.md`):
   scoped escalation re-solves (P8, ~7–8% of the chip vehicle),
   congestion-map parallel-for (P6a), DNUTS per-layer threads at scale
   (P7), async persistence (P9) — after the landed persistence
   dirty-tracking + parallel generation took the vehicle −31% and the
   B2 copy fan-out fix took the bottom-up twin −16.5%. The B1 victim
   ladder was investigated to a documented dead end: the
   *usage-unchanged* prune is inert once made admissible, leaving
   cheaper per-candidate scoring as the remaining lever.
2. **NDR support** (per-net/bus width/spacing/shield constraints):
   **substantially landed** since the previous edition — `def_ndr` /
   `set_ndr` / `dump_ndr`, a single-sourced group-demand conversion
   shared by planner charging, abstract-NUTS footprint and DNUTS
   admission, the R9 typed audit, R5a rail crediting, v21 BDB rule
   persistence, and R2d hier propagation (`src/ndr.h`,
   `src/buda_cmds/ndr_cmds.py`, four test modules). The documented
   residual is shield-vias for emitted shields.
3. **OpenAccess bridge**: spec'd behind an optional CMake flag; LEF/DEF
   + Verilog + GDS remain the supported interchange.
4. **CI depth**: nightly QoR corpus with golden ownership
   (`docs/internal/opens_ci.md`).
5. **Planner selection-basis levers** and remaining wishlist items
   (dead-span gate default, kWLSpread default) await the measurements
   their notes call for.

## Practical Onboarding Path

1. Read `README.md`, then `docs/USER_GUIDE.md` for the standard flow.
2. Skim the repository `CLAUDE.md` — it is the accurate architecture
   index (build model, stage-by-stage detail, command tables).
3. Run `demo/quickstart.buda` and `bin/bfp tc1`; open a flow in the
   topology explorer (`visualize_topologies`).
4. Read `src/buda_cli.py` + one `buda_cmds/` stage module to see the
   command → engine path.
5. C++ headers in pipeline order: `bdb.h`, `bundler.h`, `topology.h`,
   `topology_analysis.h`, `congestion_planner.h`, `nuts.h`,
   `routing_grid.h`, `detailed_nuts.h`, `verify.h`.
6. For any behavior question, find its `docs/internal/` note — the
   measurement history usually explains the current default.
7. Before changing routing behavior: `tools/qor_corpus.py --out base`
   on main, again on your branch, `--compare` — the standard gate.

## Bottom Line

BUDA has grown from a staged prototype into a measured, resumable,
hierarchy-aware planning system with a real feedback loop and a
regression discipline unusual for a codebase this age: every default is
a recorded measurement, every parallel path proves decision-identity,
and every routing change faces a corpus gate. The structural risks of
the 2026-05 snapshot (monolithic session, ad-hoc endpoints, sidecar
state) were paid down, and the subtler risks this rewrite originally
named — the mutable binding surface, print-identity oracles, the healer
concentration point, scale-blind runtime work, reader-found doc
staleness — were addressed by the executed risk-reduction plan: the
codebase now validates its own invariants at every stage entry, proves
healer identity on structured decision records, carries its healer
logic in cohesive seams, measures runtime per design class by default,
and fails CI on stale docs. What guards it is checked discipline
rather than types — kept honest by the guards themselves. The
near-term high-value work is finishing the chip-scale runtime items,
then NDR — the first feature that will stress the per-net granularity
of a deliberately bus-centric core.
