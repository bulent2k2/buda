# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

**BUDA** (Bundled Unified Design Automation) is an **EDA interconnect planning system** for chip design. It bundles nets into buses, generates routing topologies, assigns routes to metal layers, and resolves physical track positions — from abstract bus planning down to individual bit-wire placement respecting power-grid and pre-route blockages.

The core engine is **C++20** exposed to Python via **pybind11**, with a Python CLI and matplotlib visualization layer on top.

### Two ways to drive BUDA

The project is **BDB-centric** (v3 architecture). All hierarchical physical-design data lives in a SQLite-backed **BDB** (Buda Physical Design Database). There are two entry flows:

1. **Flat flow** — declare blocks and nets directly in a `.buda` script (`add_block`, `add_net`), bundle them, generate topologies, plan, NUTS. No hierarchy. This is the original demo flow and what most stage docs below describe.
2. **Hierarchy-aware flow** — open/build a BDB (`open_bdb`, `import_def_lef`, `import_verilog`, or the interactive **Floorplanner**), derive busterms, then run the `hier` variants (`run_hier_bundler`, `generate_hier_topologies`, `run_planner hier`). Templates are solved once per cell type and instantiated at every occurrence.

The **Floorplanner** (`bin/fp`, `bin/bfp`) is a separate interactive GUI tool that edits block placement in a BDB and can launch the hier routing flow directly.

## Useful Docs
- [User Guide](docs/USER_GUIDE.md) — Prerequisites and standard flow for novices.
- [BUDA CLI Reference](docs/BUDA_CLI.md) — `buda` command-line invocation and flags (`--no-viz`, `--tag`, `--log`, `--verbose-conn`, `--ipc-verbose`).
- [BUDA Script Reference](docs/BUDA_SCRIPT_REFERENCE.md) — Detailed command documentation (index + pipeline overview; per-stage command pages live under `docs/script_reference/`).
- [BDB Reference](docs/BDB_REFERENCE.md) — Physical design database: schema, `.buda` commands, Python API.
- [BDB Test-Data Management](docs/internal/bdb_test_data.md) — Diffable `*.bdb.sql` fixtures, the copy-to-temp `bdb_input` test fixture, and the write-back/OA versioning roadmap.
- [Feature-suite coverage plan](docs/internal/feature_coverage_plan.md) — the Gherkin/pytest-bdd spec layer (`test/tests/features/`): the `@landed`/`@future`/`@doc`/`@orphaned` tag vocabulary, the arc→feature coverage map, and the parse+tag guard (`test_feature_files.py`).
- [Floorplanner User Guide](docs/FLOORPLANNER_USER_GUIDE.md) / [Reference](docs/FLOORPLANNER_REFERENCE_GUIDE.md) — Interactive placement GUI and engine API.
- [Hier Bundler](docs/HIER_BUNDLER.md), [Hier Topology](docs/HIER_TOPOLOGY.md), [Hier Planner](docs/HIER_PLANNER.md) — Hierarchy-aware pipeline internals.
- [Cross-Level Bundling](docs/cross_level_bundling.md) and [HBundle Pipeline session notes](docs/session_hbundle_pipeline.md) — How the hier flow was built (Phases A–E).
- [Congestion Planner](docs/congestion_planner.md) — Internal design of the bundle planner: cost model, hard overflow constraint, rip-up & replan.
- [Detailed NUTS](docs/detailed_nuts.md) — Internal design of bit-level track assignment.
- [Routing Grid](docs/routing_grid.md), [Detailed Viz](docs/detailed_viz.md), [Key Bindings](docs/KEY_BINDINGS.md).
- [BDB → Flat Script Converter](docs/BDB2BUDA.md) — `tools/bdb2buda.py`: export a BDB as a flat `.buda` routing script.
- [Flat Script → BDB Cell Converter](docs/BUDA2BDB.md) — `tools/buda2bdb.py`: ingest a flat `.buda` script into a BDB as a cell (reverse of `bdb2buda`; replaces an existing cell and size-syncs its instances).
- [BDB Bus-Width Editor](docs/BDB_EDIT_BUS.md) — `tools/bdb_edit_bus.py`: resize a bus's bit count (prune 64→4, grow 2→16 by cloning a template bit's pins) or delete a bus, editing the netlist directly in a `.bdb`/`.bdb.sql` across every net-referencing table (net/pin/net_props/bundle_net/net_segment/net_via). `--list` enumerates buses; `--dry-run` previews; `--clear-routing` drops stale route tables. Re-run the flow afterward.
- [GDS/OA Interchange Plan](docs/internal/gds_oa_interchange.md) — GDSII import + export (implemented, Phases G0–G4: label-based nets, layer mapping, tested round-trip) and the OA bridge spec.
- [Hierarchical Demo BDB Builder](docs/BUILD_HIER_DEMO.md) — `tools/build_hier_demo.py`: assemble a hierarchical BDB from several flat scripts (each instantiated twice in a top cell) with random cross-instance top-level buses.

## Wrapper scripts (`bin/`)

All the launcher/build wrappers live in **`bin/`** at the repo root: `bb` (build),
`buda` (routing CLI), `fp` / `bfp` (Floorplanner), `viz` (DEF/BDB visualizer),
`u2b` (unit-test → `.buda` visualizer), and `activate` (sourceable env setup).
Each resolves the repo root as its parent dir, so it works from any CWD.

**Add `<repo_root>/bin` to your `PATH`** and you can invoke them bare (`bb`, `buda
flow/x.buda`, `u2b test_foo`); otherwise call them as `bin/bb`, `bin/buda …`, etc.
The examples below use the `bin/…` form so they work without a PATH change.

**macOS `.app` bundles (optional):** `python3 tools/make_macos_apps.py` builds
`bin/Buda.app` and `bin/Floorplanner.app` — thin launchers that run the same
python through a LaunchServices `.app` so the Dock tile shows "Buda" /
"Floorplanner" (name **and** icon) instead of "python3". `bin/fp` and `bin/buda`
auto-launch through their bundle on macOS when it exists (`bin/buda` routes
through a throwaway per-cell bundle so the Dock tile shows the `.buda` basename,
and keeps the terminal via `open --stdout` + `-W`; `BUDA_NO_APP=1` forces the
in-terminal launch). The bundles are git-ignored build products. See
[macOS app bundles](docs/internal/macos_app_bundles.md).

The one-step way (per shell) is to **source** `bin/activate` — it prepends
`bin/` to `PATH` and sets `PYTHONPATH=build:tools` (so `python3 src/buda_cli.py …`
and ad-hoc `import buda` work too). It is idempotent and must be *sourced*, not
executed (a PATH change only affects the sourcing shell):

```bash
source bin/activate            # from the repo root, once per shell
```

Or set `PATH` manually / add it to your shell rc:

```bash
export PATH="$PWD/bin:$PATH"   # from the repo root, once per shell (or add to your rc)
```

## Build

Use the build wrapper script `bin/bb`. By default it performs an **incremental** build:

```bash
bin/bb            # incremental build into build/
bin/bb --clean    # clean rebuild (-c also works)
bin/bb test       # build, then run the FAST test tier (~8s; -t also works)
bin/bb mid        # build, then run FAST + MID tiers (+flow-script integration; -m also works)
bin/bb slow       # build, then run ALL tiers (+SA/GA optimizer storms; -s also works)
bin/bb --help     # describe all options (-h)
```

Tests are split into three cumulative tiers via pytest markers (`mid`, `slow` in
`pytest.ini`); the default run excludes both. Per-test runtimes and the tier
rationale (and the parallel-run setup) live in [docs/internal/test/](docs/internal/test/).

Manual build:

```bash
mkdir -p build && cd build
cmake .. && make -j4
```

All build artifacts remain in `build/`. After a build, `bin/bb` removes any stale `.so` copies from `src/` so they cannot shadow the fresh build. Compiled with `-O3 -march=native -Wall -Wextra` (`/O2` on MSVC).

CMake builds **three** artifacts (see `CMakeLists.txt`):

| Target | Kind | Contents |
|---|---|---|
| `buda_core` | shared lib (`libbuda_core.so`) | BDB + SQLite + busterm + bundler + bundle_refiner. The single compiled copy of the DB-layer types. |
| `buda_db` | Python module | Registers BDB / row types / BustermGen in pybind11's global type registry. Importable standalone. |
| `buda` | Python module | Full routing pipeline. Imports `buda_db` and re-exposes its names (so `buda.BDB == buda_db.BDB`), links `buda_core`. |

Both extension modules link the same `buda_core`, giving pybind11 one `std::type_info` per class — this is what lets `buda.BDB` objects pass into `buda` C++ functions taking `BDB&` without a type-info mismatch/segfault. **When adding a DB-layer type, register it in `buda_db` (via `bind_db.cpp`), not `buda`.**

## Run

Prefer the `bin/` wrapper scripts (they set `PYTHONPATH=build:tools`):

```bash
bin/buda demo/comprehensive_demo.buda   # routing CLI (src/buda_cli.py)
bin/fp  [file.bdb]                       # interactive Floorplanner GUI (tools/bdb_floorplanner.py)
bin/bfp tc1                              # Floorplanner with a built-in demo scenario
bin/bfp flow/some.buda                   # run a .buda flow, then open its BDB in the Floorplanner
bin/u2b test_column_datapath_hvh         # convert a topology unit test to .buda + open the explorer
```

`bin/u2b <test_name>` runs `tools/unit2buda.py` on a fixtureless topology unit
test (see [BDB2BUDA](docs/BUDA2BDB.md) siblings and `tools/unit2buda.py`), writes
the equivalent flat `.buda` to `$TMPDIR`, and opens it in the topology explorer —
a one-shot way to eyeball a test's input floorplan and generated candidates.

Or run the CLI directly:

```bash
PYTHONPATH=build python3 src/buda_cli.py demo/comprehensive_demo.buda
```

Set `export PYTHONPATH=build` once per shell session if invoking Python directly.

`.buda` script command reference:

**BDB / hierarchy setup** (hier flow — see [BDB Reference](docs/BDB_REFERENCE.md)):

| Command | Description |
|---|---|
| `open_bdb <path.bdb> [writeback]` | Open (creating if needed) a BDB for hierarchy-based design data. A `*.bdb.sql` text fixture is materialized to a temp binary (read-only by default); `writeback` dumps changes back to the `.sql` on `save_bdb`/`exit`/end-of-run |
| `save_bdb [<path>]` | No arg: write the working BDB back to its `*.bdb.sql` source now (after `open_bdb … writeback`). With a path: **save-as** — one-shot snapshot of the current state to a new file (`.sql` = diffable text, else binary via the SQLite backup API; not re-flushed at exit), e.g. a placement checkpoint right after `align_bottom_up` |
| `load_pipeline [expanded]` | **Resume/rehydrate** the routing pipeline from the open BDB: bundles + all candidate topologies (`seg_busterms` restored logically from the `topology_seg_busterm` links — never re-derived from geometry — and TEG-over `bridge_segments` from `topology_bridge_segment`), the planner's selection + assigned layers + pre-plan pins, and the abstract-NUTS result — as deep as was persisted — so a fresh session continues where a previous one stopped (checkpoint after `generate_topologies` / `run_planner` / `run_nuts`+`ripup_reroute`, reopen, continue); hier bundles validate in their own frames on load (cell-local templates / cross-level bundles — `entry/exit_busterm_ids` restored), so a pre-planner hier checkpoint incl. hand-committed USER candidates resumes and the continued `run_planner hier` replicates a pinned USER template to every instance. Requires the setup (layers/patterns/blocks) re-declared first; `expanded` = hier post-expansion view (bottom-up TEMPLATE wrappers are restored too, so a pre-`run_nuts` checkpoint re-runs the cell-local solve and keeps uniform copies; a post-`run_nuts` checkpoint keeps the persisted routing). See [BDB Reference](docs/BDB_REFERENCE.md) |
| `import_def_lef <def> <lef>` / `import_verilog <v>` / `import_gds <g> [labels <csv>]` | Ingest a placed design / netlist / GDSII layout into the open BDB (GDS TEXT labels recover nets/pins — a labeled GDS runs the hier flow with zero Verilog; shapes on `def_gds_layer`-mapped routing pairs are excluded from cell footprints) |
| `def_gds_layer <buda_layer_id> <gds_layer> [<dt>]` (also `file <path>`, `labels <csv>`) | GDSII layer mapping: bind a `def_layer` metal to a GDS (layer, datatype) pair — import treats mapped shapes as wires (not macro outlines), export writes metals to their mapped pairs; `labels` registers the default TEXT label layers for `import_gds` |
| `export_gds <file.gds> [outline <l>] [labels <l>\|off] [via_size <um>]` | Export the BDB as deterministic GDSII (reverse of `import_gds`, from the **persisted** tables): cells → structures + SREFs (with instance orientation via `component.orient`), `net_segment` wires (`bus_segment` fallback) on mapped pairs, vias as squares, one net-name TEXT per pin — re-importable with the same map (tested round-trip, incl. rotation/mirror) |
| `set_die <w> <h>` | Set die dimensions in the BDB |
| `add_cell <name> <w> <h>` / `add_cell_pin <cell> <pin> [dir] [px py]` | Define a cell type and its port interface |
| `set_bottom_up <cell>\|* [on\|off]` | Mark a cell template for bottom-up planning (plan/NUTS its local interconnect once, copy to every instance; copies become keepouts for higher levels — see docs/internal/hier_bottom_up_planning.md). Persisted (`cell.bottom_up`, v17); instances must be congruent — any of the 8 orientations, detected geometrically since hierarchical rotate/flip keep tokens `'N'`; direction-preserving instances (`N/S/FN/FS`) copy from the cell's reference directly, while the 90° family (`E/W/FE/FW`) is split at `run_planner hier` into its own **rotation-class clone template** (virtual name `<cell>90`, uniquified; `bundle.cloned_from`, v19) whose candidates are generated from the rotated reference's actual cell-local floorplan — solved once per class, copied within the class; only a no-orientation-matches instance is refused. **`*` = keepout-scope generalization**: mark EVERY eligible cell at once — every cell with congruent placed instances (≥2 = solve-once-**copy**; a **single** instance = solve-once + freeze its cell-local routing as a keepout, nothing to copy). Cells whose ≥2 instances are non-congruent (cannot be frozen-and-copied) are reported and left on the top-down path (fail LOUD, never silent), and `* off` clears every mark. Opt-in — the default hier flow marks nothing, so results are unchanged unless you mark cells; run `align_bottom_up` first so copied instances share a track phase (else stage (c) `check_template_tracks` stops DNUTS with the mismatch report) |
| `align_bottom_up [max_shift <um>] [force]` | Nudge every `set_bottom_up` cell's instances onto a common track phase with **minimal total movement**, per ROTATION-CLASS group (upright and 90°-rotated instances align to their own class references; per axis: coordinate mod the LCM of that direction's layer pitches; the target phase is the circular L1 median of the group's phases, so the majority usually stands still; a MIRRORED instance participates via its effective coordinate about the track layout's symmetry center — real nudge = negated effective shift). Subtree-aware (`translate_comp`), so congruence is preserved; region overrides are absolute-keyed and not fixable by translation. Run after `def_track_pattern` + `set_bottom_up`, BEFORE `derive_busterms`/`add_blocks_from_bdb`; `max_shift` skips any larger nudge with a WARNING. By default a move that introduces a NEW overlap/outside-die issue (`FloorplannerEngine.validate()` pre/post diff) is auto-REVERTED to a fixpoint — the exact-geometry slack cap; `force` keeps such moves and only warns |
| `add_inst <inst> <cell> <parent> <x> <y>` / `add_inst_to_cell <parent_cell> <inst> <child_cell> <x> <y>` | Instantiate a cell into the hierarchy / define a cell's internal structure |
| `add_comp <name> <cell> <parent> <x1> <y1> <x2> <y2>` | Insert a component row with explicit absolute coords |
| `move_comp` / `flip_comp` / `rotate_comp` / `resize_cell` / `set_comp_*` | Mutate placement (move, mirror, rotate 90/180/270, resize) |
| `add_blocks_from_bdb [depth N]` | Load BDB components at a hierarchy depth into the flat Floorplan |
| `bdb_net_mode <on\|off>` | Mirror `add_net`/`add_bus` into the BDB net/pin tables |
| `derive_busterms [max_depth N]` / `refine_busterms` | Populate the busterm table from the component hierarchy (Phase A of hier flow) |

**Routing pipeline** (`.buda` script):

| Command | Stage | Description |
|---|---|---|
| `add_block <name> <x1> <y1> <x2> <y2> [container] [corner_margin ...]` | setup | Place a single-rect floorplan block; `container` marks a hierarchy envelope (transparent to LOW layers; leaf cells block LOW layers as keepouts) rather than a solid leaf cell; optional per-block corner margin (absolute or `pct_h`/`pct_v`). A block name must be UNIQUE — redefining one is a hard error (`Floorplan::add_block` silently overwrites last-wins otherwise) |
| `add_block <name> rect <x1> <y1> <x2> <y2> [rect ...] [teg_mode thru\|over] [corner_margin ...]` | setup | Multi-rect block: topology generator picks the best-fit rect per trunk position; `teg_mode over` generates an explicit bridge segment over the block's notch when the trunk falls in a gap between rects |
| `add_keepout <x1> <y1> <x2> <y2> <layer_list>` | setup | Define a rectangular keep-out zone for specific routing layers |
| `corner_margin dx <n> [dy <n>]` | setup | Set global corner margin for all blocks with no per-block override. Only `dx`/`dy` (absolute); `pct_h`/`pct_v` not valid globally. Single-axis value mirrors to the other axis. |
| `add_net <name> <driver_pin> <receiver_pins_csv> [unknown\|inout]` | setup | Add a net to the netlist; `unknown` = undirected (positional fallback), `inout` = bidirectional (INOUT treated as secondary driver). A net name must be UNIQUE — redefining one is a hard error (`Netlist::add_net` only appends, so a dup would silently double the net) |
| `add_bus <prefix>[<N>] <drv_pin> <rcv_pin_csv>` | setup | Expand a bus into N nets: `prefix[N]` → `prefix_0`…`prefix_{N-1}`; `prefix[lo:hi]` → explicit range. Bit names must be unique — a bus that redefines any already-defined net (another bus/`add_net`, incl. an overlapping range) is a hard error; disjoint ranges (`b[0:1]` then `b[2:3]`) are fine |
| `def_layer <id> <name> <H\|V> [TOP\|LOW] <overhead%> [span_min N] [span_max N] [kSpan K]` | setup | Register a metal layer; `TOP` marks it for trunk preference; optional span limits and per-layer congestion weight override. Both the id AND the name must be UNIQUE — redefining either is a hard error (a dup id is silently ignored / a reused name silently clobbers the name→id map otherwise) |
| `set_min_stub_length <n>` / `set_min_stub_length_dir <H\|V> <n>` / `set_min_stub_length_layer <layer> <n>` | setup | Floor on stub segment length: global, per-direction, or per-layer (avoids tiny unroutable stubs) |
| `set_feedthru <blocks\|*> <layers\|*> [on\|off]` | setup | Mark a block×layer set as opt-in feedthru (routable-through); resolved most-specific-first `(block,layer) > (block,*) > (*,layer) > global`. A fed-through block must be a **bundle busterm the trunk passes through**: the `TRUNK_H`/`TRUNK_V` generator splits the spine at its faces (two BUSTERM landings, bridged by the block's own routing) and `check_topo` accepts the declared relay. Straight/I-shape feedthru + `feedthru_penalty` ranking are later phases. |
| `detour_channel <N\|S\|E\|W\|A> <width>` | setup | Reserve an outer detour band of the given width on one side (or `A` = all four) for U-shape routes. Consumed by BOTH topology generation (U/OOB detour columns) AND the NUTS placement boundary: an explicit channel extends the interval seed so a selected out-of-bbox trunk can be *seated* in the reserved band (issue #58) — bounded segments re-tighten to their slide window, so a design with no explicit channel is byte-identical. A selected OOB trunk with no channel to sit in is reported LOUD (`NUTSResult.unseatable_trunks`) instead of silently stranding its bits at detailed NUTS |
| `run_bundler <STRICT\|CONVERGENT\|BIDIRECTIONAL\|COMBINED> [--dump]` | 1 | Group nets into buses. `--dump` prints one line per created bundle (`b-<id>  nets=<N>  "<reason>"  [first net names]`) — the flat-flow counterpart of `dump_hbundles`. (CONVERGENT = fan-in by shared receiver; BIDIRECTIONAL = direction-agnostic, sorted set of all endpoint instances, so A→B bundles with B→A and cyclic a→b,c/b→c,a/c→b,a; **COMBINED = the join of both** — union-find merges nets connected by a CHAIN of either relation, the only genuinely new point on the strategy lattice STRICT ⊂ {CONVERGENT, BIDIRECTIONAL} ⊂ COMBINED — sound because fan-in trees are direction-agnostic and per-bit tapered; restrict per prefix with `set_bundling`. A CONVERGENT bundle spanning multiple driver *blocks* routes as a **fan-in tree**: generation derives endpoints from ALL the bundle's nets and roots the multicast/MST shapes at the shared sink with every driver as a leaf, so every driver block is physically attached, and the realization is **per-bit tapered** (`Topology::seg_bits`): each segment carries only the bits whose driver→sink path uses it, so planner charging / NUTS widths / DNUTS emission are member-bit-scoped and no net's wire lands on another driver's block — and `check_design`'s net-driver fidelity check (`NET_DRIVER_OPEN`) flags any topology whose block contract drops an endpoint (or any bit with no derivable driver→sink path). BIDIRECTIONAL connects the same blocks so it's sound by construction. See docs/internal/convergent_bundling.md) |
| `set_bundling <prefix>\|* <strict\|no_convergent\|no_bidirectional\|combined>` | 1 setup | Per-net-prefix bundling permission (longest prefix wins; `*` = global default): a merge via a relation happens only when the strategy enables it AND both nets permit it — e.g. `set_bundling clk_ strict` keeps clock nets out of every convergent/bidirectional merge. Applies to BOTH `run_bundler` and `run_hier_bundler` |
| `set_max_bundle_bits <N\|auto\|off> [auto]` | 1 setup | Optional bundle bit bound, applied as a split pass after bundling (any strategy): a bundle over the limit splits into **balanced** parts (600 @ 512 → 300+300, never 512+88) with bits of one bus kept together. `auto` = dynamic per-bundle cap from the **shortest busterm edge**: per endpoint block, the bits incident to it (what the per-bit taper physically lands on its face) must fit `floor(min(w,h)/min_bit_pitch)`; static + auto may combine (max part count wins), and the partitioner enforces every block cap PER PART (a part closes before any cap is exceeded — a balanced target alone can't bound clustered incident bits). Splits are reported LOUD with the binding constraint; `\|SPLIT:k/n` reason suffix. Applies to BOTH `run_bundler` and `run_hier_bundler` — a hier TEMPLATE bundle is split BEFORE per-instance expansion, so the split propagates identically to every occurrence (each part is its own template; the AUTO cap resolves a cell-local leaf to a congruent instance's child footprint, and a fan-in part re-scopes its per-net `net_drivers`/`net_receivers` + reason to the leaves its bits touch) |
| `run_hier_bundler [depth <N>] [STRICT\|CONVERGENT\|BIDIRECTIONAL\|COMBINED]` | 1 | Group nets into hierarchy-aware HBundles using open BDB (BIDIRECTIONAL = direction-agnostic, as in run_bundler: a net + its reverse + cyclic groups bundle together, so one bundle can mix bidirectional pairs and one-way nets; CONVERGENT/COMBINED as in run_bundler, applied per bundling depth to SAME-LEVEL nets — a multi-driver group with differing endpoint block sets becomes a **fan-in bundle** (`FANIN:root\|FROM:leaves` reason, per-net endpoints in `net_drivers`/`net_receivers`) that generation routes as a per-bit tapered fan-in tree in the bundle's frame, while a mixed-direction group over ONE block set keeps the block-to-block BIDIR treatment; cell-local fan-in templates merge with replicas like STRICT templates; `set_bundling` overrides apply; **cross-level** multi-driver groups fan-in too — CONVERGENT/COMBINED group cross-level nets by their shared receiver set into one fan-in bundle rooted at the shared sink with each deep driver as a per-bit tapered leaf (`FANIN` reason persisted, so a resumed session recovers the endpoints even though `net_drivers` isn't persisted), while STRICT/BIDIRECTIONAL keep them separate). Both bundlers **persist** their bundles into the BDB `bundle`/`bundle_net`/`bundle_busterm` tables when a BDB is open (membership keyed by `net_id`, resolved from net name — flat-flow nets are auto-created; see [BDB Reference](docs/BDB_REFERENCE.md)) |
| `dump_hbundles [expanded] [depth N]` | 1 | Print HBundle list (pre-expansion by default; `expanded` = post-`run_planner hier` view; `depth N` = filter by level) |
| `generate_topologies [center_mode] [double_detour] [multi_trunk] [no_hanan_loci] [spine_relays]` | 2 | Generate candidates for all bundles (src/dst auto-derived from netlist). `multi_trunk` (opt-in) adds two-level **BITRUNK_HVH/VHV** trees for high-fan-out datapath nets: a root spine feeds perpendicular branch trunks, each tapping a cluster of aligned blocks (a column/row becomes a multi-tap pass-through trunk), emitted in both orientations. It also adds the legacy single-level **BITRUNK_V** ladder (two V rungs + an H backbone — the row-of-receivers mirror of the always-on **BITRUNK_H**; opt-in because it is a measured QoR net-negative on-by-default). A generation-time **anchoring gate** (`filter_unanchored_bitrunk`) drops a fully-degenerate legacy `BITRUNK_H`/`BITRUNK_V` — one whose endpoint blocks are ALL untapped (covered only by a free-sliding trunk graze that opens every bit at DNUTS, e.g. bigHalf bus_038) — when a clean alternative survives; the two-level trees keep their own `topology_is_clean_tree` gate. Hanan-line trunk loci are DEFAULT-ON (the hanan_loci default flip): n-pin trunk loci are sampled ON the in-bbox Hanan lines (block/keepout edges) as well as at channel midpoints, so a block-edge-aligned trunk can nominal at the geometric WL floor (the b44 +500 overshoot — wishlist-topo "Nominal-WL comparability" piece (a)); `no_hanan_loci` opts a run out (midpoint-only pool — the default pool is ~1.3-1.6x it and the WL-sorted candidate indices `select_topology` pins differ between the settings), and the legacy `hanan_loci` flag is still accepted as a keep-on no-op (pre-flip scripts / v15 knob memos). Every generation path ends in a **coverage gate** (`filter_uncovered`): a candidate leaving a bundle block with no busterm tap and no pass-through (verify `BUSTERM_OPEN` — a silent open) is always dropped, a candidate whose wire graph splits into 2+ electrically separate islands (verify `DISCONNECTED` — the hanan_loci face-coincident-locus family; declared-feedthru candidates exempt, their split-gap islands are bridged by the fed-through block) is always dropped too, and an *undeclared* `FEEDTHRU_RELAY` candidate (legacy multi-rect / rootless trunk+MST fallback) is dropped **when a clean alternative survives** — all with a printed note; if *every* candidate is broken, the list is kept with a WARNING (never strands a bundle). A face/abutment-line trunk (a Hanan-line locus) keeps its stub↔spine junctions (graze taps at the shared face line are cleared so the junctions are derived), and a loci-only candidate whose slide window collapses under the final block contract is dropped with a note (the post-contract pinch gate). Two abutting blocks sharing a full edge are rescued by a shared-edge crossing candidate (`ABUT_H`/`ABUT_V`, `shared_edge_segment`); a zero-candidate bundle (degenerate placement) emits an explicit unrouted-bus warning. **`spine_relays`** (opt-in, default off / byte-identical) replaces the over-the-cell **bracket chain** at a high-degree MST relay hub with a single **collector spine** the incident stubs tap independently (independent NUTS slide) — measured −61% pure-MST connectors on `big.buda`; also the `set_spine_relays()` API + `BUDA_SPINE_RELAYS` env (and the study twin `BUDA_MULTI_TRUNK` for the multi_trunk knob), precedence **token > env > compiled default (off)**; both knobs re-measured 2026-07-30 on the full corpus — keep opt-in (real healer-flow regressions; docs/internal/wishlist-topo.md "Default-flip study"); kept opt-in (net-positive on `viol_bundles` with a couple of raw-metric regressions — see docs/internal/wishlist-topo.md) |
| `generate_topologies_for_bundle <bundle_id\|hint\|id:N\|net:PFX> [center_mode] [double_detour]` | 2 | Generate candidates for a specific bundle; the selector resolves like `select_topology`'s (bare integer = bundle ID, bare non-numeric = net-name hint, `id:`/`net:` force one). Endpoints (src/dst) are derived from the bundle; multiple dst → multicast trunk+branch shapes |
| `generate_hier_topologies [center_mode] [double_detour] [multi_trunk] [no_hanan_loci] [spine_relays]` | 2 | Generate candidates for all HBundles (3-case: cell-local / cross-level / cross-block). `multi_trunk` (opt-in) adds two-level **BITRUNK_HVH/VHV** datapath trees, `no_hanan_loci` opts out of the default-on Hanan-line trunk loci, and `spine_relays` opts into the MST relay-hub collector spine, exactly as in the flat `generate_topologies` (a memoized per-bundle opt-out/opt-in survives bulk regeneration). Re-applies each bundle's persisted generation-knob memo (v15) additively, so a pool accreted with `generate_more_topologies` survives a bulk regeneration. Both `generate_[hier_]topologies` **persist** all candidate topologies into the BDB `topology`/`topology_segment` tables (before `run_planner`) when a BDB is open; see [BDB Reference](docs/BDB_REFERENCE.md) |
| `generate_topologies_for_hbundle <bundle_id> [center_mode] [double_detour] [multi_trunk] [no_hanan_loci] [spine_relays]` | 2 | Re-generate candidates for a single HBundle by ID (`multi_trunk`/`no_hanan_loci`/`spine_relays` as above; an explicit loci polarity / spine opt-in is recorded in the bundle's knob memo so a bulk regen keeps it); useful for debugging zero-candidate bundles |
| `generate_more_topologies <hint> [center_mode] [double_detour] [multi_trunk] [no_hanan_loci] [spine_relays]` | 2 | **Additive** per-bundle generation: run the generator with the given knobs and APPEND the new candidates to the bundle's existing pool, deduplicated by stable content uid (`topo_uid`) — existing indices, the pin, and plan state are untouched, so an expert accretes candidates across knob experiments without losing selections. **Hier-aware**: in a hier-bundled session the hint matches an HBundle id or its first net-name prefix, generation goes through the same 3-case dispatch as `generate_hier_topologies`, a replica match redirects to its template, and a bulk `generate_hier_topologies` re-applies the per-bundle knob memo (v15) so the accreted pool survives regeneration; accretion happens on PRE-expansion templates (after `run_planner hier` the command refuses with the re-run recipe) |
| `set_prune_dominated [on\|off]` | 2 setup | Opt-in WL-dominance candidate pruning (default off — bit-identical without it): at the end of every generation command (not the additive `generate_more_topologies`), drop a candidate whose WL envelope bottom `wl_lo` exceeds another's envelope top `wl_hi` — its BEST realization is longer than the survivor's WORST — but ONLY when the survivor is equivalent in every non-WL respect the planner scores or the escalation ladder exploits (same block contract + feedthru declarations, same segment count/directions/layer hints, survivor slide windows COVERING the dominated one's, survivor spans INSIDE the dominated one's, survivor nominal ≤ dominated nominal); bridge/fan-in-taper/jog/perp-clamped candidates never participate, USER candidates neither prune nor survive-as-dominators (a not-clean edit_commit must not evict a valid generated alternative), and pinned candidates are never dropped (selection remapped by `topo_uid`). Prints a per-candidate note + a `[TopoPrune] pruned N … K refused` summary; runs BEFORE sidecar restore + BDB persistence so indices/persisted rows/pins see the pruned pool (declare before generating; pins must come from an opted-in run). Corpus measured: 0 pruned / 11-3010 pairs refused per flow, endpoints identical — the conservative salvage of the rejected min-WL assignment (opens #13c) |
| `set_dedup_loci [on\|off]` | 2 setup | Opt-in nominal-locus candidate dedup (default off — bit-identical without it): at the end of every generation command (not the additive `generate_more_topologies`), collapse candidates that are the SAME topological choice differing only in a nominal trunk locus WITHIN a shared slide window — identical connectivity, per-segment slide windows, block taps, and net-pull, so the trunk slides with its stubs and every member routes within NUTS realization-noise of each other (e.g. b44's `TRUNK_H@y10830 / y11330 / y11830`, all on trunk slide window `[10830,11830]`, or a `TRUNK_H` vs an MST hybrid that realizes the same skeleton). The best-estimated (lowest-WL) member survives; USER candidates never collapse; the pinned candidate is never dropped (remapped by `topo_uid`). LOSSY (the residual per-member spread is the b44 realization sensitivity, not a real DOF) — prints a per-candidate `[TopoDedup]` note; runs BEFORE sidecar restore + persistence, so indices/pins/persisted rows see the collapsed pool (declare before generating; pins from an opted-in run) |
| `set_drop_dangling [off\|on\|drop\|clamp\|clamp_drop]` | 2 setup | Opt-in handling (default off — bit-identical without it) for candidates with a DANGLING segment (a ConnSeg with a single non-block connection — a wire whose other end connects to nothing) or an UNBOUNDED slide window (the `±2^30` no-clamp sentinel) — the OOB detour / MST-relay geometry that passes the coverage gate but renders as a dangling overshoot (e.g. b44's `TRUNK_*_OOB(+MST)`). Runs at the end of every generation command (not `generate_more_topologies`). **Modes**, least→most aggressive: **`clamp`** bounds every unbounded slide window to the design extent (blocks bbox + candidate spans + margin) via `Segment::perp_clamp_lo/hi` — the candidate's own `Topology::clear_analysis_cache()` is called so the next analyze picks up the bound — and drops NOTHING, so OOB detour routes are kept but can't be flung to infinity (and indices are NOT renumbered, so hard `select_topology` pins survive); **`clamp_drop`** clamps the windows AND drops only the TRULY dangling candidates (single non-block conn); **`drop`** (= `on`) drops any dangling-or-unbounded candidate (the original behavior, most aggressive). Never touches a USER candidate, never drops the pinned one, never strands a bundle. Prints `[TopoDangling]` clamp/drop notes; runs BEFORE sidecar restore + persistence (declare before generating; drop/clamp_drop renumber indices, so their pins come from an opted-in run) |
| `edit_topology <bundle_id> [<cand#>\|new]` + `edit_add_trunk <H\|V> <perp> [<lo> <hi>] [layer <id>]` / `edit_add_stub <block> <seg#>` / `edit_set_span <seg#> <lo> <hi>` / `edit_set_layer <seg#> <layer_id>` / `edit_set_slide <seg#> <lo> <hi>|clear` / `edit_connect <i> <j>` / `edit_disconnect <i> <j> <retract_to>` / `edit_remove_segment <seg#>` / `edit_status` / `edit_commit [pin]` / `edit_abort` | 2 | **TopoEdit session**: open a working copy of a candidate (or an empty topology) — a hier bundle's session resolves and edits in the bundle's OWN frame (cell-local floorplan for a cell-level template, endpoint frame for cross-level; flat/expanded stay on the session floorplan) — apply transactional edits — pick an axis + Hanan line and add a (default full-span) trunk, stub blocks to it, override spans, connect/disconnect perpendicular segments — each printing a verdict (check_topo violations + zero-slide pinch + wire-graph components); `edit_commit` appends the result to the bundle's pool as a `USER` candidate (uid-deduped; `pin` selects it), `edit_abort` discards. With a BDB open, the commit also stores the session's **op-log provenance** as meta (`user_ops:<bundle_id>:<topo_uid>` → base candidate uid + the applied `edit_*` command lines, block/face refs verbatim — the how-it-was-built record beside the geometric rows; GUI commits store it too via the explorer's sink); `load_pipeline` prints a pointer for each restored USER candidate that has one, and `dump_user_ops <bundle_id>` prints the replayable command sequence. Slide overrides ride the existing `plan.seg_slide_lo/hi` NUTS hatch. Coordinate arguments accept **block/face references** — `<block>.<left\|right\|top\|bottom\|cx\|cy>[±N]`, resolved against the edit session's floorplan — and the GUI's `[edit-cmd]` log emits the same form where a coordinate matches one |
| `set_planner_param <name> <value>` | 3 | Set a planner tuning knob; takes effect at the next `run_planner` (knobs may be changed between runs to re-plan). Known params: `kCong` (congestion weight), `kSpan` (span-length weight), `base_cost_non_top` (penalty for non-TOP layers), `kWL` (wirelength weight), `kSegs` (opt-in, default 0 = off: segment-count penalty in wirelength-equivalent units per segment — the kWL term scores `wl + kSegs·n_segments`, demoting many-segment trees whose junction vias scale PER BIT and whose realization DOF the WL estimate omits; b61: the 10-seg TRUNK+MST estimates 9% under the 5-seg tree but realizes only 2% under it with 2.25× the vias — kSegs 500 picks the 5-seg; corpus at 500: big2 unplaced 108→0 / overlaps 4→0 at +1.8% detWL, bigHalf unplaced −64% / detWL −3.1% / vias −29%), `kSegsRel` (the scale-free form: fraction of the design's max-possible HPWL — grid extent W+H — added per segment; **0.02 is the COMPILED DEFAULT** (the measured safe-Pareto point), gated so it engages only when the flow DECLARES `healersAhead` (see below) and without `multi_trunk` trees or `kPeak` steering, exactly where it was measured to only win; env `BUDA_KSEGS_REL` overrides the default for study runs (`0` disables it), and an explicit `set_planner_param kSegs`/`kSegsRel` bypasses the gates entirely; sweep tables + the default-flip gating issues G1–G6 in [docs/internal/ksegs_default_audit.md](docs/internal/ksegs_default_audit.md)), `healersAhead` (0/1 flag, default 0: DECLARE that this flow will heal — `ripup_reroute`/`negotiate_congestion` run later — so the gated proactive `kSegsRel` default engages at `run_planner` and `run_nuts` auto-escalates dead LOW spans; **set it BEFORE `run_planner`**. Issue #444 made this explicit: a healer command later in the flow no longer auto-enables the gate (the old behavior scanned the script text, so the SAME commands routed differently depending on how they were split across `source` files / run interactively). ripup's own re-plan sets it by construction, and an explicit `set_planner_param kSegs`/`kSegsRel` doesn't need it), `kBalance` (TOP-layer load balancing), `kHeight` (short segments prefer the lowest same-direction TOP layer), `kPeak` (opt-in, default 0 = off: routability-aware selection — pay for the worst band's EXISTING fill fraction so candidates steer off nearly-full bands before overflow; the overflow-only `kCong` is blind below capacity. Also carries an absolute-supply floor: a band whose real span-wide signal-track supply — pattern overrides and keepouts included, invisible to the width capacity model — cannot host the bundle's bits is priced as full, in either capacity mode; flat 1.0 in the segment score, proportional `needed/supply` in the intra-segment band choice so all-short bands still rank by how impossible they are), `track_cap_slack` (extra signal tracks/band in `signal_tracks` mode), `refine_passes` (post-commit refinement passes — revisit committed bundles DEEPEST-FIRST against real usage, reservations released, adopting a replan only when leaving the old topology is STRICTLY better by score; the hier level-ordering synthesis — hbundles/01 WL −21%, 02 −32%, 05 opens 47→8 at 2 passes, 10 heals to 0/0, mix heals 1/0→0/0 with 9× faster heal loops. Default: **1 for hier planning**, 0 for flat — big2's plain pipeline regressed under refinement; an explicit set, incl. 0, always wins), `nontop_dead_span_gate` (opt-in, default off: refuse a NON-TOP layer whose stub span has ZERO keepout-clear signal tracks — the pin-access blind spot where the planner's per-cut capacity samples the endpoint-clamped extent — so STRICT escalates to a TOP layer; measured bigHalf no-rr opens 566→135 (−76%). Off by default because span_pool==0 over the conservative abstract span can't tell a genuine cull from a survivor whose final span clears the keepout, so always-on regresses rnr_mix; see opens item 4), `kWLSpread` (opt-in, default −1 = off: realization-risk WL penalty — the session stamps each candidate's slide/span WL envelope `[wl_lo, wl_hi]` onto the topology and the kWL term scores `nominal + kWLSpread×(hi−lo)`, demoting wide-envelope slide-coupled shapes whose NUTS realization lands far above the nominal (the b44 mis-ranking: a 6-seg TRUNK+MST at nominal 3510 realizes 4510/bit, beaten by a 2-seg TRUNK_V at 4010 realizing 3715/bit). Recommended 0.125 (those historic b44 −19.6% / mempool_tile −46.5% numbers predate the NUTS pull-breakpoint clamp; re-measured 2026-07-20 the clamp absorbs b44's win entirely (now +0.0%) while clean healed flows keep modest wins — bigHalf −6.0%, comprehensive −2.8%, mix −2.4%, big2/10_chip −0.5%, all 0/0). Stays OPT-IN by default: the biggest remaining win (mempool_tile −49.7%) is a healerless flow that never reaches a clean endpoint, and mix2's unplaced regresses 42→52 even *with* healers in its flow, so a healers-ahead default gate (kSegsRel-style) wouldn't rescue it — see docs/internal/wishlist-planner.md "Realization-risk WL". Base stays nominal — an envelope-point replacement was measured and rejected), `charge_pull_target` (opt-in, default 0 = off: honest-books mode — charge a pulled segment at its deterministic predicted pull target (breakpoint-clamped window bound) instead of best_band_perp, since NUTS never consumes seg_perp for pulled segments (books-vs-metal: bigHalf 141/185 pulled segments >100 units off their charged band, worst 3378 — a `[NUTS] books-vs-metal` line reports it per run_nuts); the anchor is **occupancy-aware** — used only when its band is overflow-free, else the charge falls to the occupancy-aware best_band_perp, mirroring NUTS's preferred_fit (target the pull, spread to the nearest free track), which removes the over-concentration that steered big2's SELECTION to longer detours (est-WL +6.8%→+1.6%, 2026-07-20) and, by keeping the charge honest, heals bigHalf's L2 opens and the demo-b3 strand at level 1; the same flag makes ripup's band_occupants rank victims by PLACED positions. Measured on: divergence −84% bigHalf, mempool_tile WL −83% with fewer overlaps AND opens, healers absorb the plain-pipeline shuffles to 0/0. Opt-in: the junction-anchored placement preference (pull=0) is not predicted yet and can trip keepout gaps on healer-less flows — comprehensive_demo b3; **level 2** adds the junction prediction: the single-rider anchor clamp (from the same base NUTS uses — nominal when busterm faces are present, band pick otherwise) + a STRICT dead-band gate over junction-extended spans (extension to pulled partners' predicted tracks, refused only on zero-capacity keepout-carved bands) — heals the demo-b3 strand to 0/0, big2 plain opens 268→152, mix 1 ov/0 opens; per-design trade-offs documented in planner.md) |
| `select_topology <sel> <id\|group:N>` / `select_topologies <sels> <ids>` | 3 | Pin one/many bundles to a specific candidate topology (1-based) before planning. `<sel>` is a **numeric bundle ID** (legacy) OR a **net-name hint** (a bare token with any non-digit, e.g. `bus_033`, matches every bundle whose first net starts with it); `select_topologies` also takes comma lists + ranges (`1,5-9,11`) and hints mixed. Disambiguate a numeric-leading bus name with `id:<N>` (force ID) / `net:<prefix>` (force hint, e.g. `net:10` matches a bus `10net`). **`group:<N>` pins a SUPER-CANDIDATE** — the whole nominal-locus FAMILY containing candidate N (the near-identical candidates that differ only in trunk perp within a shared slide window — the `set_dedup_loci` / `dump_topologies --grouped` families): the planner is restricted to that family's members and **refines WHICH member wins** (perp/skeleton), instead of the user hand-picking one nominal. Sets `input.pinned_group` (member indices) and clears the single `topology_pinned`; a later single pin clears the group. **Byte-identical when unused** (empty group = the historical single-pin/full-sweep planner path), and byte-identical to an unpinned plan when the pinned family is the natural winner. Errors name the bus + its candidate count |
| `unpin_topology <hint\|*>` | 3 | Clear a bundle's pin (inverse of `select_topology`): drops `topology_pinned`, any forced `pinned_seg_layers` (from `edit_commit pin` after `edit_set_layer` — the planner honors those for any candidate, so a re-chosen topology would otherwise keep the stale layers) AND any `pinned_group` super-candidate pin, so the next `run_planner` may re-choose, leaving the current selection in place; `*` clears every pin |
| `run_planner <iterations> [signal_tracks]` | 3 | Layer assign + topology select. `signal_tracks` (opt-in) charges band capacity in discrete SIGNAL-track count (× bit pitch) instead of layout width, so a band short of tracks surfaces as planner overflow instead of a silent DNUTS open; needs `def_track_pattern`. See [Signal-Track Capacity plan](docs/internal/planner_signal_track_capacity.md). **Persists** its decision when a BDB is open: `topology.is_selected` + `topology_segment.assigned_layer`, and (hier) expanded per-instance `bundle` rows; see [BDB Reference](docs/BDB_REFERENCE.md) |
| `run_planner hier [<iterations>] [signal_tracks]` | 3 | Hier-aware planner: pins sidecar selections, expands cell-level bundles to per-instance wrappers, then runs congestion planner top-down. `signal_tracks` as above |
| `run_planner post_nuts [V [short long]] [H [short long]]` | 3 | Post-NUTS stub layer reassignment: short/long stubs on V or H layers are moved to cheaper layers |
| `set_track_pitch <pitch>` | 3 setup | Declare inter-bus track pitch before `run_planner` so its pitch-aware band reservations match the `run_nuts` that packs tracks; `run_nuts` with no arg reuses it |
| `run_nuts [pitch]` | 4 | Abstract track placement (defaults to the last `set_track_pitch`/`run_nuts` value; warns if it differs from the pitch `run_planner` reserved for). **Persists** placed bus segments + symbolic bus-vias into the BDB `bus_segment`/`bus_via` tables (FK to `bundle`) plus a `route_snapshot` content-hash fingerprint of the routed output when a BDB is open; see [BDB Reference](docs/BDB_REFERENCE.md) |
| `set_dead_span_escalate <on\|off>` | 4 setup | Force post-NUTS **dead-span escalation** at `run_nuts` (explicit opt-in): a LOW-layer segment whose ACTUAL placed geometry (span + Hanan interval) offers ZERO keepout-clear signal tracks — the exact DetailedNUTS admission test (`count_signal_tracks_in_span` span-clear, then the `count_signal_tracks_in` midpoint fallback) — is a guaranteed DNUTS open; it is moved to the cheapest same-direction TOP layer and NUTS re-solves, iterating until no dead LOW segment remains. This is the FINAL-GEOMETRY form of `nontop_dead_span_gate` (the plan-time gate can't tell a cull from a survivor; the placed-geometry test fires on zero survivors). **Runs AUTOMATICALLY at `run_nuts` when the flow declares `healersAhead`** (`set_planner_param healersAhead 1`, the same explicit gate the kSegsRel default uses — issue #444 replaced the old `_healers_in_flow` script-text scan) — before the healers, the measured-better timing (mix 1/16→0/0, bigHalf 190→94), with the stage-b `_heal_dead_spans` fold still running too (the two compose; mix2 unchanged). This flag forces it even without a healer ahead; `_dead_span_auto_at_run_nuts=False` disables the auto path. See docs/internal/wishlist-planner.md "dead-span discriminator" |
| `run_nuts_on_layer <layer-name>` | 4 | Re-solve one layer with NUTS without disturbing other layers; **re-persists** the re-solved routing (bus + detailed rows) when a BDB is open |
| `dump_topologies [<hint>] [--problems] [--conn] [--grouped]` | — | Text dump of per-bundle candidate topologies (type, wirelength, segments, pass-through, `min_slide`, selected/pinned); `--problems` filters to bundles with duplicate/pinched/single/pass-through candidates and prints an aggregate summary; `--conn` adds a per-segment connectivity detail for the selected candidate (what each seg connects to — busterms + other segs, the bundle busterms it passes through (`passthru:`; unrelated blocks it flies over — normal OTC routing — under `otc-over:`, flagged `low-cross:` instead when the effective layer is non-TOP (leaf footprints are LOW-layer keepouts); interior overlap only, never single-point abutments), its slide range, and net-pull preference). **`--grouped`** collapses nominal-locus FAMILIES (super-candidates) to one representative row each (the lowest-WL member), annotated `family:+K@lo..hi` (K other variants spanning trunk perp lo..hi) and the header `cands=N → M families` — the reduced set to inspect before a `select_topology <b> group:<rep>` group-pin. Read-only inspection (never drops candidates — display only). |
| `visualize_topologies [<hint>] [debug]` | — | Open topology explorer for the matching bundle (`-all [hints…]` for multiple). `debug` orders candidate stepping (a/d) by **increasing planner cost** instead of wirelength — the REAL charged cost post-`run_planner` (true congestion from the other committed bundles, via `CongestionPlanner::candidate_costs`, read-only), the intrinsic wirelength pre-plan — keeping the candidate/group IDs and the `topo i/n` display unchanged (only the traversal order changes), and shows each candidate's total cost + components (`seg` congestion/span/layer terms + `wl`) with its cost-rank in the title, plus the selected segment's per-term congestion cost in the j/k info panel |
| `visualize [debug]` | — | Open interactive matplotlib window; `debug` opens the topology explorer it spawns (`v` / "View Topologies") in the debug cost view (increasing-cost candidate stepping + cost display), same as `visualize_topologies … debug` |
| `source <file>` | — | Execute another `.buda` script inline |
| `def_track_pattern <layer_id> <origin> <type> <w> <sp> ...` | 8 setup | Define repeating track pattern. A layer's global pattern may be defined ONCE — a duplicate is a hard error (it silently overwrote last-wins otherwise); use `add_grid_override` for a region-scoped pattern. Each `<type>` must be a canonical slot type `POWER\|GROUND\|CLOCK\|SHIELD\|SIGNAL\|CUSTOM` (case-insensitive; aliases `_`→`SIGNAL` (terse shorthand), `GND`→`GROUND`, `CLK`→`CLOCK`, `VDD`→`POWER`, `VSS`→`GROUND`) — only `SIGNAL` is routable, and an unknown type is a hard error (it silently became a non-signal rail before, losing a mistyped `SIGNAL`'s tracks). Same validation for `add_grid_override` |
| `add_grid_override <layer_id> <x1> <y1> <x2> <y2> <origin> ...` | 8 setup | Region-scoped pattern override |
| `run_detailed_nuts [lo_hi\|hi_lo]` | 9 | Snap bit-wires to concrete tracks; emits per-bit `net_vias` (the symbolic bus-vias fanned out per bit, drawn under `[Vias/Conns]` in detailed viz). **Persists** bit-wires + per-bit vias into the BDB `net_segment`/`net_via` tables (FK to `bundle`, net identity via `net_id`) and rewrites `route_snapshot` (stage `detailed_nuts`) when a BDB is open; see [BDB Reference](docs/BDB_REFERENCE.md) |
| `ripup_reroute [max_iter] [use_edge_candidates] [no_global] [no_class_moves] [no_release_moves] [fast_trials\|no_fast_trials] [screen\|no_screen] [warm_trials\|no_warm_trials] [converge_guard\|no_converge_guard] [no_parallel_sweep]` | 3↔4/9 | Feedback-driven rip-up & re-route: greedy hill-climb that reads the **actual** NUTS overlaps (run after `run_nuts`) or DetailedNUTS opens (run after `run_detailed_nuts`), re-pins a contending bundle to an alternate topology, re-runs the pipeline, and keeps moves that reduce the metric — clears congestion the planner's band model under-predicts (`overflow=0`). Per contended bundle it always tries its **index alternates** (farness-from-contention first; under measured contention the top-8 farness-ranked candidates from BEYOND the 8-cheapest-estimate window are appended after the legacy pool, so a higher-estimate class — OOB trunk, BITRUNK tree — is promotable, committing only on a STRICTLY better measured metric (cheap-first order + strict `<`: ties keep the cheaper move); big2 stage-a residual 1→0), and — only when `use_edge_candidates` is passed (**off by default**) — also, when the selected candidate is an MST type, **per-edge L/Z flips** of that candidate's contended edges (`flip_mst_edge` in place, undone by the involution on rejection), committing whichever wins. The flip source is opt-in because on the current corpus it is *tried* on real contended MST edges (e.g. big2 bundle 24) but the index alternates always win the commit — so it changes no routes and is left off unless you want to explore edge flips. Applies to all MST candidate types incl. `TRUNK+MST` hybrids (`_rr_flip_edges` matches `"MST" in type`; hybrid legs are `edge_id`-tagged); see [MST edge realization](docs/internal/mst_edge_realization.md). When the contender scan stalls above zero, a bounded **global-occupant pass** (default on; `no_global` disables) ranks the committed bundles holding each remaining contention site's bands (`CongestionPlanner::band_occupants` — the victim ranking of `replan_bundle_ripup`, read-only) and trials each occupant's alternates ranked against THE SITE's location — a NON-contended bundle holding the contended bands can be the global fix no contender-derived move reaches (big2 b61: its STRICT-rejected window-infeasible `TRUNK_H+MST` candidate is reachable because a pinned trial's ladder ends in BEST_EFFORT), committed only on a strictly better measured metric. Each trial replans **incrementally** (`CongestionPlanner::replan_bundle`: recharge every other wrapper's committed assignment, plan the one moved bundle) and skips the command handlers' persist/log work, so a trial costs ~one NUTS solve instead of a full-design planner pass (~10-40× faster ripup); the winning index move **commits by restoring its forward snapshot** (no extra pipeline re-run; planner cuts explicitly recharged so the viz overlay stays truthful) and rejected trials restore only the wrappers they dirtied. A per-run timing summary (`replan/nuts/dnuts/snapshot/restore` seconds + counts, plus a per-pass `solve passes:` breakdown) rides the final `done:` line; `BUDA_RR_TRACE=1` adds a per-trial line. **Fast trials** (default on; `no_fast_trials` opts out): trials skip metric-neutral passes — stage a the WL-only `tighten_pulls` (overlap-non-increasing, so the trial metric is an upper bound and accepts stay sound), stage b the per-bit via emission (pure output, metric identical) — while COMMITS always re-run the full pipeline, so committed routes are full-pipeline states and every commit strictly improves the true metric; the first-improving choice among moves can differ from a full-trial run (corpus: mix/slowdown_rnr/big2 byte-identical, bigHalf same 0/0 endpoint at 32s vs 88s). **Fixed-context screen** (default on; `no_screen` opts out): before full-trialing a contender's alternates, each candidate is placed ALONE against every other bundle's baseline placement frozen as fixed occupancy (`add_fixed_segments_except`, doglegs+tighten skipped, ~ms per candidate) and only the top-2 screened moves are full-trialed — the screened score is an ORDERING, never a metric (accepts stay on the true full metric, unlike the reverted two-tier trials), and screened-out moves are DEFERRED to the iteration's stall sweep (full-fidelity, before the global pass), so the stop certificate is still a full sweep. Corpus: same clean endpoints, bigHalf rr flow 40.8s→16.5s (stage-b trials 123→11), big2 ripup 0.91→0.43s. **Warm trials** (opt-in, default OFF; `warm_trials` enables): pre-filter each move with the warm-start single-bundle re-solve (`NUTSEngine::rerun_bundle_warm` — seed the baseline, place only the moved bundle against it frozen, then run the safety passes on the unfrozen union; cost tracks the move's blast radius) — only warm-improving moves pay a cold trial, warm-rejected moves are cold-swept at the stall point so the stop certificate stays a full COLD sweep and accepts stay on the true cold metric. Fidelity measured (`BUDA_RR_WARM_STUDY=1` harness): 91-100% accept agreement, 4.6-6× cheaper per solve on bigHalf — but with the screen already cutting trial volume the pre-filter is cost-neutral on the corpus, hence default off; opt in when per-trial cold cost dominates (crossover ≈ cold ≥3× the ~41-70ms warm eval). Stage b's metric is lexicographic `(DNUTS opens, NUTS overlaps)` and the loop keeps grinding collateral overlap creep after the opens hit 0. No-op when already clean. Works in both flat flow and **hier flow** (after `run_planner hier`, self.bundles is the expanded per-instance list, so a re-route re-pins one instance in place). **Bottom-up template CLASS moves** (default on; `no_class_moves` disables): when even the global pass stalls and the residual contention sits on `hier.locked` bottom-up template instances (which every other pass must skip — their routing is a uniform fixed copy), the class pass re-pins the cell TEMPLATE to an alternate candidate, re-runs the cell-local solve for its layers (other templates' pins honored), propagates the pin to every instance of the rotation class, and measures a NO-replan pipeline re-run (every other wrapper keeps its committed assignment; the moved class's routing is the recomputed fixed copies) — one move re-routes ALL instances, committed only on a strictly better measured metric, user-pinned templates inviolable, BDB persistence deferred to the accept path. A no-op on non-bottom-up flows (byte-identical). Measured on mix2_fast_bottomup + healers: stage-a residual 2→1 overlaps, final DNUTS opens 16 (2 locked bundles)→8 (1); see docs/internal/bottomup_healer_templates.md. **Measured-infeasibility uniformity break** (stage b, default on but ALSO gated on the `check_template_tracks on_mismatch independent` policy — the user's declared willingness to solve instances individually; `no_release_moves` disables): when even the class pass stalls and a `hier.locked` instance still holds measured DNUTS opens — its plan-time track pools MATCH the reference, so the conflict is dynamic neighbors/occupancy at THAT instance, invisible to any static pool comparison — the RELEASE pass unlocks exactly that instance (fixed copy withdrawn, pin kept, forced per-segment layers cleared — the `unpin_topology` hazard: the planner applies them to ANY candidate, so a repin would carry the old candidate's H/V layers onto a different-direction shape), re-solves it individually, tries its candidate alternates when the free re-solve alone does not improve, and commits only on a strictly better metric — the aligned siblings keep the uniform copy, and the commit is LOUD (`RELEASE COMMIT` names the instance). Measured: bundle 166's stuck 8-open residual heals to 0 with a clean detailed `check_design` (release + topo 1→2), matching the top-down twin's endpoint (opens #14 (a)). **Dead-span escalation folded in** (default on, stage b only): the same `_heal_dead_spans` preconditioning as negotiate — dead LOW segments escalated to TOP before the hill-climb, which then heals the fallout (see the negotiate row / wishlist-planner "dead-span discriminator"). **Parallel stall sweep** (rnr runtime P1, default on; `no_parallel_sweep` opts out, gated on fast trials on + warm trials off): the deferred stall-certificate moves — the dominant trial volume — are evaluated on a C++ thread pool (`buda.parallel_sweep`, GIL released; per-move private wrapper/planner copies, incremental replan + NUTS(+DNUTS incl. the bottom-up copy-plan path), metrics implementing the sequential fast-trial semantics exactly), outcomes walked in the sequential visit order with the first in-order improver REPLAYED through the normal sequential trial (the replay is the accept basis and the committed state; unevaluable moves fall back to sequential trials; a sweep-vs-replay disagreement is a LOUD warning, replay verdict kept) — decision-identical by construction (validated byte-identical incl. trial counts on the rnr vehicles; `BUDA_SWEEP_THREADS` caps the pool, 0 = hardware concurrency). Measured: mix2_fast_bottomup 40.2→29.8s (stage-b ripup 25.9→16.8s), mix2_fast_on_aligned_sql 33.5→29.7s; no-stall flows unchanged. **Convergence guard** (default on; `no_converge_guard` disables, `converge_guard` forces): stop early on an over-capacity design that can't converge — fires only when ≥6 committed iterations have run, the primary metric is still ≥100 (far above any converging flow's mid-ripup residual ≤48), AND <3% of the window-start metric cleared over the last 6 iters; provably can't fire on a flow that converges in <6 iters or drops below the floor. Measured: tc3a 56.8s→16.3s (+481 opens on a hopeless 7000+-open flow); bigHalf/mix2 unaffected. See docs/internal/healer_effectiveness_2026-07.md |
| `negotiate_congestion [max_iter] [class_moves]` | 3↔4 | Measured-congestion negotiation (run after `run_nuts`, before/with `ripup_reroute`): each ACTUAL NUTS overlap rectangle is injected as extra demand on the exact planner bands where it happened (`inject_band_demand`), with PathFinder-style history pressure for repeat offenders, then BOTH bundles of every overlap are re-planned unpinned (`replan_bundle`) — the corrected cost model steers them off the contended bands choosing among ALL candidates in one pass, no per-candidate trial. Iterations accept only strict overlap-count improvement (snapshot/restore otherwise). Typically clears the bulk in seconds; `ripup_reroute` finishes the residual. Stage auto-detected: after `run_detailed_nuts` each DNUTS-open segment's placed window is injected instead (an open marks a band whose real signal-track supply fell short of the width model's promise), metric lexicographic `(opens, overlaps)`. When the target has no overflow-free candidate the replan may also displace the committed blocker holding the contended bands (`replan_bundle_ripup` — the planner ladder's victim rip-up in a single negotiation step; note this victim stage triggers only for a CONTENDED, STRICT-infeasible target and moves victims via unpinned-STRICT replans — ripup's measured-metric, occupant-first, pinned-trial **global-occupant pass** is its complement, not a duplicate). **Dead-span escalation folded in** (default on, stage b only): before the hill-climb, every LOW segment whose FINAL placed geometry has zero keepout-clear signal tracks (a guaranteed DNUTS open no replan can reach — a layer-assignment fault) is escalated to a TOP layer and re-solved, then negotiate heals any collateral overlap (`_heal_dead_spans`; `_heal_dead_spans_in_healers=False` disables; measured bigHalf opens 315→179, slowdown_rnr 42→32, no overlap cost, no-op elsewhere). **`class_moves` (opt-in, default off): bottom-up template price translation** (negotiate v2) — a `hier.locked` affected bundle's TEMPLATE class is negotiated in its cell-local frame: each instance's injected band demand is clipped to the instance bbox, mapped through the inverse orientation transform, summed across instances into the cell-local planner (`extend_grid_for` pre-extends the grid so the injections survive the optimize), the target templates re-plan UNPINNED under that aggregated price field, and the pins propagate to every instance; the iteration accepts/restores under the extended class snapshot with the deferred-BDB contract. Opt-in because it measured endpoint-neutral at best (mix2_fast_bottomup final opens 8→16 at default budgets, equal at `ripup_reroute 30`; the stage-a overlap metric is blind to the DNUTS quality its bigger shuffles trade away, and the stage-b iteration is price-rejected — ripup's class moves already cover the endpoint; table in docs/internal/bottomup_healer_templates.md). See wishlist-ripup item 1 |
| `refine_selection [max_moves] [chase_overlaps]` | 3↔4/9 | Measured selection **WL polish** (selection-basis lever 3, wishlist-planner): the two existing measured loops cannot recover realized wirelength — ripup's metric is overlap/opens-only (stops at parity, never improves WL) and the planner's `refine_passes` re-scores through the cost model whose WL term is the generation-time ESTIMATE, so a candidate that routes shorter than it estimates structurally loses. This end-of-flow pass sweeps every eligible bundle's selection on the MEASURED result: the fixed-context screen orders all alternates (ordering only), the top-2 are full-trialed (fast trials forced off — a tighten-skipped trial's WL would be biased against the move; full trials make winners fwd-restorable via ripup's snapshot+recharge commit path), and the default accept is **componentwise** — opens, overlaps AND interval violations parity-or-better with realized abstract WL (Σ placed span lengths) strictly lower — so the healers' endpoint can never be traded for length (run it AFTER the last `ripup_reroute`; both pre-healer placements were measured to perturb the healers' basins). `chase_overlaps` = plain lexicographic accept (the aggressive pre-healer form, measured mixed). Stage-aware (stage b carries DNUTS opens ahead of overlaps); skips user pins, `hier.locked` bottom-up copies, single-candidate pools; `max_moves` (default 30) bounds commits. Measured end-of-flow (endpoints preserved exactly): mix realized WL −8.4%, aligned −1.7%, bottomup −0.2%; on the healerless topdown flow the componentwise accept doubles as a healer (175 opens/16 ov → 84/2, WL −3.1%). On a STUCK endpoint compose it with a second healer round — `refine_selection` → `negotiate_congestion` → `ripup_reroute` → `refine_selection` — refine's commits change the contention geometry the healers stalled on (negotiate re-plans its targets unpinned, so refine's pins don't block it; healer accepts are strict on their lexicographic (opens, overlaps) metric — opens never rise and a no-accept round restores byte-identically, but an accepted iteration can transiently trade overlaps up for ripup to finish, so the round is lexicographically, NOT componentwise, protective): heals the topdown flow to 0/0 Success (QoR vehicle flow/rnr/mix2_topdown_refine.buda), byte-identical no-op on an already-stalled residual. Opt-in — flows that do not call it are byte-identical |
| `check_design [all]` (alias `check_connectivity`) | verify | Run the design audit at the current stage (topo / NUTS / detailed-NUTS): connectivity opens, layer directions, keepout crossings, unplaced bits. `all` checks every candidate topology; when run pre-planning (no topology selected yet) the topo stage auto-promotes to all-candidates mode. Report-only — it never filters or aborts (generation's coverage gate is what drops uncovered candidates) |
| `check_template_tracks [on_mismatch stop\|independent]` | verify | Bottom-up template planning stage (c) verification (after `run_nuts`, before `run_detailed_nuts`): for every `set_bottom_up` cell — per ROTATION-CLASS group (a 90°-rotated class compares against its own clone-template reference) — compare the span-aware signal-track pools each instance sees for its copied routing (`signal_tracks_in_span` per fixed segment window, normalized by instance origin; a MIRRORED instance's pool is reflected back into the reference frame before comparing) — instances agree iff their offset/reflection phase fits the layer track pitch and no absolute-rect override/keepout cuts their windows differently. Also runs BEFORE any routing (placement-stage mode: whole-instance windows per grid layer, advisory — feeds `align_bottom_up`). Post-routing verdict cached and consumed by `run_detailed_nuts` (which runs the check implicitly if never invoked): ALIGNED cells solve DNUTS once on a reference instance and copy bits+vias to siblings (copies pre-reserved for everyone else); on a mismatch, `stop` (default) refuses DNUTS with the report, `independent` copies the aligned instances and solves the misaligned ones individually. `independent` also declares the willingness ripup's **measured-infeasibility uniformity break** is gated on: a locked instance whose pools MATCH but whose copied routing is measured DNUTS-open anyway (a dynamic neighbors/occupancy conflict no static pool comparison sees) may be released at a stage-b ripup stall and solved individually — see the `ripup_reroute` row's RELEASE pass. The policy persists in BDB meta |
| `report_overhead` | — | Compare `def_layer` overhead% against the actual track-pattern overhead |
| `report_wirelength` (alias `report_wl`) | — | Report routed wirelength per bundle + design total for comparing interconnect quality across changes: **abstract** bus-level WL (one length per placed bus segment — the metric topology decisions move) after `run_nuts`, plus **detailed** bit-level WL (every bit-wire) after `run_detailed_nuts`, each with a per-layer metal breakdown. Full per-bundle table → flow log; total → terminal. |
| `source <file>` / `exit [code]` | — | Execute another `.buda` script inline / stop with an exit code |

Unknown commands are a hard error (the CLI fails fast rather than silently ignoring typos).

## Tests

Run from the repository root — `pytest.ini` configures `testpaths=test/tests`, `pythonpath=build src`, and excludes `slow`-marked tests by default:

```bash
pytest                        # fast tier (excludes 'mid' and 'slow' markers)
pytest -m "not slow"          # fast + mid tiers
pytest -o addopts="" -m slow  # only the slow tier
pytest test/tests/test_nuts.py -v   # single file
bin/bb test                   # build + fast tier; bin/bb mid adds integration, bin/bb slow adds all
```

Feature files in `test/tests/features/` (pytest-bdd). Most stages have a corresponding `.feature` and `test_*.py` file, including BDB (`test_bdb.py`, `bdb_*.feature`), hier flow (`test_hier_*`), floorplanner (`test_floorplanner_*`), connectivity (`test_check_design_hbundle.py`, `test_check_layer_dir.py`), routing grid, and detailed NUTS.

**Checked-in BDB test data** is committed as **diffable SQL text** (`test/tests/data/*.bdb.sql`), never as a binary blob. The `bdb_input` conftest fixture materializes a throwaway binary copy in `tmp_path` so the pipeline never dirties the checked-in fixture; `tools/bdb_serialize.py` round-trips binary↔text and `test/tests/data/build_fixtures.py` regenerates fixtures deterministically. See [BDB Test-Data Management](docs/internal/bdb_test_data.md).

---

## Architecture

### Pipeline Overview

```
        ┌─────────────────────────────────────────────────────────────┐
        │ BDB (SQLite)   components · cells · pins · nets · busterms ·  │
        │                bundles · groups.  Central store for the hier  │
        │                flow; built by import_*, add_*, or Floorplanner│
        └─────────────────────────────────────────────────────────────┘
                 │ derive_busterms / add_blocks_from_bdb
                 ▼
Netlist / Floorplan (.buda script — flat flow — or projected from BDB)
    │
    ▼
[1] Bundler          nets → Bundles      (HierarchicalBundler → HBundles for hier flow)
    │
    ▼
[2] TopologyGen      Bundles → candidate L/Z/U topologies (Hanan grid)
    │                ConnTopology augments each with connectivity + slide ranges + MST
    ▼
[3] Bundle Planner   topology selection + layer assignment (congestion-aware)
    │                each segment now has: layer, routing-dir span, perp interval
    ▼
[4] Abstract NUTS    1.5-D rectangle packing → BusSegment track_position (real coords)
    │                parallelises per layer; power-grid dilution applied approximately
    ▼
[5] Layer Stack      (consulted by stages 3–9 for layer direction/type metadata)
    │
    ▼
[8] Routing Grid     per-layer track patterns (power/signal/clock layout); IMPLEMENTED
    │                global pattern per layer + optional Hanan-region overrides
    ▼
[9] Detailed NUTS    snaps each BusSegment → N NetSegments on concrete signal tracks; IMPLEMENTED
                     respects pre-route blockages; bit ordering; timing-critical mode

[6]  CLI             orchestrates the flow via .buda scripts (src/buda_cli.py)
[7]  Visualizer      interactive matplotlib; click-to-highlight; pre-route toggles
[V]  Verify          check_topo / check_nuts / check_dnuts — connectivity & layer-dir audit
[FP] Floorplanner    interactive placement GUI over FloorplannerEngine + PlacementOptimizer
```

---

## Stage-by-Stage Detail

### Stage 1 — Bundler (`bundler.h/cpp`)

**Responsibility:** Group nets that share driver/receiver topology into `Bundle` / `HBundle` objects.

**Key types:**
- `Net` — name, driver pin (`instance.pin`), list of receiver pins
- `HBundle` — id, list of net names, grouping reason; in the hier flow also carries `level` / `cell_context` / `instances` (the `Bundle` type was renamed `HBundle` and given hierarchy fields)
- `Netlist` — flat container of nets; populated by `add_net` CLI commands
- `Bundler` — flat grouping with a configurable `Strategy`
- `HierarchicalBundler` — hier grouping driven by BDB busterms/pins (`run_hier_bundler`)

**Algorithm (flat):** For each net, generate a string signature from its driver and/or receiver instance names. Nets with the same signature are grouped into one bundle.
- `STRICT` — signature = driver instance + sorted receiver instances; exact match required
- `CONVERGENT` — signature = sorted receiver instances only; shared destination is enough

**Hier:** each net is bundled once at its most specific endpoints (level = common-ancestor depth); cell-level bundles become reusable templates instantiated per occurrence. See [Hier Bundler](docs/HIER_BUNDLER.md) and [Cross-Level Bundling](docs/cross_level_bundling.md).

**Output fed to stage 2:** `vector<BundleWrapper>` (wrapping `Bundle`/`HBundle`)

---

### Stage 2 — Topology Generator (`topology.h/cpp`)

**Responsibility:** For each bundle's source→destination block pair, enumerate candidate routing paths as sequences of axis-aligned segments.

**Key types:**
- `Point` — integer (x, y)
- `Rect` — integer bounding box with `.center()`
- `Segment` — start point, end point, `layer_hint` (integer)
- `Topology` — type string (`"L_HV"`, `"Z_trunk_x"`, `"U_top"`, …), list of segments, estimated wirelength, trunk location
- `Floorplan` — block registry; manages keepout zones; provides `get_hanan_grid()` (sorted unique x and y coordinates of all block edges and keepouts)
- `TopologyGenerator` — generates L, Z, U candidates between two named blocks

**Topology shapes:**
- **L-shape** (2 segments): horizontal then vertical, or vertical then horizontal. The bend point is at one block's center projected onto the other's axis.
- **Z-shape** (3 segments): adds an intermediate trunk segment at a Hanan grid line between the two blocks. Multiple Z candidates are generated — one per intermediate Hanan grid coordinate.
- **U-shape** (3 segments): routes outside the bounding box of the two blocks, used when a direct L or Z path would traverse an obstacle. Trunk is placed beyond the extreme Hanan grid lines.

**MST shapes & feedthrough completion:** Standalone MST candidates (`MST_HV`/`MST_VH`, N≥4 blocks, `add_mst_candidates`) connect each edge's two blocks at their nearest faces. A block with MST degree ≥2 is thus touched by two edge segments at *different* points that, without correction, are joined only *through the block* — a silent feedthrough relay (which understates wirelength and isn't a real wire). `complete_relay_junctions` (`topology.cpp`) post-processes each MST topology: at every relay block it wires the incident segments so they are physically connected (each join is *perpendicular*, the only kind `ConnTopology` infers), routed **over-the-cell (OTC)** when the block is not a feedthru — a benign global wire over the block footprint on the trunk/over-cell layers, *not* a feedthru. Two shapes:
- **Orthogonal stubs (one H, one V) → simple extension.** A relay touched by exactly two stubs of opposite orientation is wired by *extending both stubs* over the cell to meet at the corner (the V stub's column, the H stub's row) inside the footprint. No connector segment is added, and — crucially — neither stub then ends on the block's face, so the block carries **no busterm tap**: it is covered by the crossing wires (`seg_spans_rect` pass-through), and the FEEDTHRU check (which gathers face-endpoint stubs) does not fire. (This is what removes the redundant L at bundle 3 / blk_19.)
- **Parallel stubs (both H / both V, at different rows/columns) → single JOG.** The two stubs are *extended over the cell to a common column (row)* and joined by ONE perpendicular jog (`seg1`/`seg5`/`seg3` for bundle 3 / blk_09). Like the orthogonal case the block then carries **no busterm tap** — it is covered by the crossing wires — and because both stubs now span the block, `tighten_passthrough_ranges` bounds the jog's slide to the **cell extent**, so NUTS keeps the two stubs flexible: if their perpendicular slides overlap, the jog shrinks to zero and they merge into one straight wire through the block.

- **Collinear stubs (both H / both V, at the SAME row/column) → MERGE.** Two stubs entering opposite faces on the same perpendicular coordinate are collinear; a perpendicular connector between them would be zero-length, and ConnTopology cannot wire-join collinear segments. So the two stubs are *merged into ONE straight pass-through wire* (extend one across the block to the other's far endpoint, drop the other), spanning the block face-to-face with no busterm tap. Gated on both far endpoints being pure junctions (not block-face landings) and the block not being a declared feedthru; the erase is deferred until after tap assignment so the landing maps stay valid. Without this the old chaining fallback bridged the pair with a **trivial 2-unit jog** the planner offloaded to a zero-signal-track layer — a guaranteed DetailedNUTS open (big.buda / tc3a_flat bundle 13, 32 bits stranded).

A relay that is *not* one of these clean 2-stub cases (≥3 stubs, a collinear pair whose far end taps another block, or a declared feedthru) falls back to the general chaining, which adds connectors and keeps a single busterm tap; for those, `ConnTopology::pin_relay_tap_connectors` bounds the perpendicular connector at the tap's face endpoint to the **cell footprint** `[face_lo, face_hi]` (an OTC slide window) so the tap's along-reach stays over the cell — a **real** window, explicitly **not** a degenerate zero-slide pin (zero-slide segments are rejected by `filter_pinched`). A final **de-overlap** pass drops a connector only when it is *collinear-contained* within another wire (genuinely redundant), one at a time and only if SEG-connectivity is preserved; it is deliberately NOT generalized to drop any "globally redundant" connector — the MST edges already span a tree, so every connector is globally redundant via the long tree path, and dropping a non-collinear one would re-open the feedthru relay it exists to prevent. This makes the topology self-connected and the wirelength honest. A *feedthru* — a block that connects ≥2 of a bundle's stubs via its own lower-level routing — is now an **opt-in option** (`set_feedthru`): a `TRUNK_H`/`TRUNK_V` spine is split at the faces of any bundle busterm it passes through that has opted in, recorded in `Topology::feedthru_blocks`, and accepted by `check_topo`. **Trunk+MST hybrids (`add_trunk_mst_candidates`) are completed too**: each MST edge *replaces* a child branch block's trunk stub, yielding a cycle-free trunk-rooted tree that `complete_relay_junctions` wires up (single-rect blocks with a stub-owning root); a hybrid that cannot be cleanly completed is dropped rather than emitted, and only the legacy multi-rect / rootless fallback leaves relays flagged as `FEEDTHRU_RELAY`.

**Layer hints:** L-shape horizontal segment gets hint=3 (M3), vertical gets hint=4 (M4). All candidates use the same convention; the bundle planner may override.

**Corner margins:** At Busterm construction time, each block's bounding box is inset by its `BlockCornerMargin{dx, dy}` via `Rect::shrink(dx, dy)`. All shape functions (L/Z/U/UU/trunk) operate on the shrunken bbox directly — no per-function margin threading. The Hanan grid is built from the shrunken bboxes, so stub and trunk positions automatically land within the margin zone. `dy` applies to vertical faces (left/right, constrains Y); `dx` to horizontal faces (top/bottom, constrains X). Guard: if `2*margin >= face_extent`, the shrink is skipped for that axis.

**TEG mode (`teg_mode thru|over`):** Multi-rect blocks carry a `TegMode` flag set via `add_block … teg_mode over|thru`.
- **`thru` (default):** Each trunk connects to the nearest rect only. A split connection (trunk in the gap between rects) is left externally disconnected — the block's internal routing joins the sides. No bridge generated.
- **`over` — disjoint rects (pure TEG):** When the trunk falls in the gap between two rects, both rects get stubs (one to each) and an explicit **bridge segment** is placed along the outer face of the union bounding box (top for H-trunk gap, right for V-trunk gap). Stored in `Topology::bridge_segments[block_name]` (not in `segments`).
- **`over` — rectilinear rects (L-/C-shape):** When the trunk is inside some but not all rects (partial span), a bridge is emitted at the union bbox outer face. `rects_are_rectilinear()` distinguishes these from pure TEG (requires strict x- AND y-overlap between any two rects).
- Bridge is **suppressed** when the trunk lands directly inside a rect or the rects are adjacent (touching edges, no gap).
- The Hanan grid uses **each individual rect's edges** (not just the union bbox), so gap boundaries produce grid lines that trunks snap to naturally.

**Output fed to stage 3:** `vector<Topology>` per bundle, stored in `BundleWrapper::candidates`.

---

### Stage 3 — Bundle Planner / Congestion Planner (`congestion_planner.h/cpp`)

**Responsibility:** Select one topology per bundle and assign layers to its segments, minimizing congestion across all bundles simultaneously.

**Key types:**
- `GlobalCut` — a Hanan grid line segment subdivided into bands, tracking `band_cap` and `band_usage`.
- `BundleWrapper` — wraps a `Bundle` with its topology candidates, selected index, and bus `width`.
- `BundleAssignment` — selected topology index and per-segment layer assignments for a bundle.
- `CongestionPlanner` — congestion-aware global router.

**Algorithm:** Processes widest buses first (greedy heuristic). For each bundle candidate, it evaluates:
1. Congestion cost across cuts (band usage/capacity; band capacity is clamped to the segment's slide-window overlap with the band). Effective bus width per layer = `bits × bit_pitch` when the layer has a track pattern (`LayerStack::eff_bus_width`), else `width × dilution`.
2. Span-mismatch cost (penalizing segment length outside `[span_min, span_max]`).
3. Span-scaled penalty for non-`TOP` layers: `base_cost_non_top · min(1, seg_span/base_span_ref)` — short stubs offload to lower layers cheaply, long trunks stay on TOP.
It selects the candidate topology and layer assignments that minimize total cost, updates band usage, and returns the assignments.

**Hier mode (`run_planner hier`)**: each net is bundled exactly once at its most specific endpoints (level = common-ancestor depth — ancestor-level duplicate projections are not emitted); cell-level HBundle templates are expanded per instance (replicas skipped, each instance wrapper carrying its own donor nets) and planned top-down (`priority = -(level·10000 + n_candidates)`); each cell-local wrapper parks its effective width as a virtual **demand reservation** on TOP-layer bands inside its instance bbox (released at its own turn) so earlier globals leave room; a per-level ladder-stage summary is printed when levels differ.

**Overflow is a hard constraint** (an overflowing band cannot physically host the bus — NUTS would emit a real overlap), enforced by an escalation ladder per bundle:
1. `STRICT` — only candidates that are slide-feasible **and** overflow-free compete on soft costs (congestion/span/wirelength).
2. **Rip-up & replan** — if no candidate is overflow-free, rip up earlier-committed bundles one at a time — ranked by their demand on the failing bundle's contended bands (actual blocker first, zero-overlap victims skipped) — and replan the pair; accepted only if both end up overflow-free.
3. `ALLOW_OVERFLOW` — overflow truly unavoidable: commit the least-cost candidate with a `WARNING`.
4. `BEST_EFFORT` — no candidate even fits its slide windows (e.g. stale sidecar pins): commit anyway with a `WARNING` rather than dropping the bundle.

**After this stage**, each `BundleWrapper::candidates[selected_topology_index]` contains segments where:
- `layer_hint` is the assigned metal layer
- `start`/`end` coordinates define a soft routing-direction span
- The perpendicular coordinate implicitly defines the Hanan grid cell (hard interval for stage 4)

**Output fed to stage 4:** mutated `vector<BundleWrapper>` with `selected_topology_index` and segment layers set.

---

### Stage 4 — Abstract NUTS (`nuts.h/cpp`)

**Responsibility:** Solve the 1.5-D rectangle packing problem (Ekici, Basaran & Keskinocak 2009) — assign a concrete perpendicular `track_position` (real coordinate) to every bus segment, per layer, with no physical overlaps.

**Key types:**
- `TrackSegment` — bundle_id, seg_idx, layer, span_lo/hi (routing direction), interval_lo/hi (hard perpendicular constraint from Hanan cell), width, track_position (output), placed flag
- `NUTSResult` — flat list of `TrackSegment`s + `num_violations` (placed outside interval) + `num_overlaps` (physical collisions after placement)

**Algorithm (per layer):**
1. Extract segments from selected topologies; derive interval constraints from the Hanan grid cell containing each segment's nominal perpendicular coordinate.
2. Build a sweep-line event queue: one START and one END event per segment, sorted by `span_lo`.
3. Sweep: on START, collect occupied intervals from already-placed active segments (same-bundle segments never conflict — per-bit they are the same nets and may share tracks); place via `preferred_fit` at the alignment sibling's position (a placed same-bundle segment off the same perpendicular connector, if it fits), else the junction-anchored preference (a single-junction segment whose placed perpendicular partner's span doesn't cover its pull/centre base moves it to the nearest covered point — junctions from `Topology::seg_conns`; a slide window DISJOINT from a partner's nominal span is surfaced — derived from the final accepted state — as a `NUTSResult::junction_infeasibilities` entry that feeds `ripup_reroute` contenders), else the planner's charged-band centre (`BundleWrapper::seg_perp`, for segments free of busterm/net_pull face semantics), else the pull/centre preference (a pull's target stops at its wirelength BREAKPOINT — `ConnSeg::pull_break`, where the pull's gain saturates — not the slide-window edge, so a connector on a wide interior window cannot overshoot and stretch its coupled trunk; the b44 tug-of-war fix). On END, remove from active set.
4. On placement failure, repack the contended window: all placed interval-overlapping segments are re-placed earliest-deadline-first (`interval_hi` ascending) with `first_fit`; commits only on full success, else falls back to interval centre (overlap recorded if conflicting).
5. After all layers solve and span adjustments follow connected segments' placed positions, a bounded `repair_overlaps` pass re-places victims of any overlap the adjustments materialized (state restored unless the overlap count strictly drops).

**Power-grid interaction:** When a layer has a track pattern (`def_track_pattern`), the abstract bus footprint uses the measured per-bit channel cost: `bits × unit_pitch / n_signal_slots` (`LayerStack::eff_bus_width`), so abstract widths match what detailed NUTS can actually place. Without a pattern, `width` is inflated by the layer's `dilution_factor` (= `unit_pitch / signal_width_sum`) as an approximation.

**Parallelism:** `solve_layer()` is called independently per layer — the per-layer maps have no cross-layer dependencies.

**Output fed to stage 9:** `NUTSResult` with one `TrackSegment` per bus segment.

---

### Stage 5 — Layer Stack (`layering.h/cpp`)

**Responsibility:** Metadata registry for the metal layer stack. Consulted by stages 3, 4, 8, and 9.

**Key types:**
- `Layer` — id, name, `LayerDir` (HORIZONTAL / VERTICAL), `LayerType` (TOP / LOW)
- `LayerStack` — add/query layers; tracks which layer is the top horizontal and top vertical layer

---

### Stage 6 — CLI (`buda_cli.py`, `buda_cmds/`, `buda_session/`)

**Responsibility:** Parse `.buda` script files line-by-line and drive the C++ engine via the pybind11 `buda` module (which re-exposes `buda_db` types).

`BudaSession` holds all live objects: an optional `BDB`, the `Floorplan`, `Netlist`, `LayerStack`, `Bundler` / `HierarchicalBundler`, `bundles` list, `nuts_result`, `routing_grid`, and detailed-NUTS result. Each CLI command maps to one or more method calls on these objects. Unknown commands raise an error.

The CLI is split across three units: `src/buda_cli.py` keeps the session core (init, `run_command`/`do_command` dispatch, flow-log capture + one-line summaries, `main`); `src/buda_cmds/` is the **command registry** — each submodule owns one stage's `cmd_*(session, cmd, args, cmd_line)` handlers and exports a `COMMANDS` dict, assembled by the package into the single registry `do_command` dispatches through (`KNOWN_COMMANDS` is derived from it, and a duplicate registration is a hard import error); `src/buda_session/` holds BudaSession's helper methods as six **mixin classes** (persist, hier, nutsflow, edit, reports, ripup — composed into `BudaSession`, member sets disjoint by construction) plus `util.py` for shared module-level helpers (`_batched`, `_RR_*`).

Adding a new command/stage means: (1) implement the C++ class; (2) expose it via the relevant binding file — `bind_db.cpp` (BDB layer, registered in `buda_db`), `bind_bundler.cpp`, `bind_routing.cpp`, `bind_nuts.cpp` (NUTS / DetailedNUTS / RoutingGrid / ConnTopology / verify), or `bind_optimizer.cpp` (floorplanner); (3) add a `cmd_<name>` handler in the matching `src/buda_cmds/` stage module and register it in that module's `COMMANDS` dict (session-state helpers go in the matching `src/buda_session/` mixin).

---

### Stage 7 — Visualizer (`buda_viz.py` + `viz_*` modules)

**Responsibility:** Interactive matplotlib window. All drawable elements are registered by bundle_id so click-to-highlight works uniformly across all draw methods.

**Module layout** (mirrors the CLI's `buda_cmds`/`buda_session` split): `src/buda_viz.py` is the **façade/assembly** — both classes' `__init__` + `show()`, the mixin-composed `class` statements, and re-exports of every externally-used symbol (external importers only ever touch `buda_viz`). The helpers live in: `src/viz_common.py` (shared layer colors/labels, block/hanan draw helpers, zoom/pan geometry, button styling), `src/viz_window.py` (platform/window-manager glue: raise/focus, Tk geometry resync, macOS Dock icon/app name — monkeypatch `viz_window.<fn>` to intercept, mixins call it qualified), `src/viz_explorer/` (TopologyExplorer mixins: `edit`, `analysis`, `sidecar`, `draw`, `nav`), and `src/viz_main/` (BudaVisualizer mixins: `highlight`, `panels`, `draw_abstract`, `draw_detailed`, `view`). Mixin member sets are disjoint by construction; methods share state via `self`. Adding a viewer method: put it in the matching mixin module.

**Artist registry pattern:** Every `ax.plot()` or `ax.add_patch()` call that represents a routable object is passed to `_register(bundle_id, artist, alpha=..., lw=..., is_band=...)`. This stores the artist's resting style. `_set_highlight(bundle_id)` then dims all other bundles to α=0.1 and brightens the selected bundle to α=1.0 with 2.2× line width. Clicking the same bundle or the background resets.

**Draw methods:**
- `draw_blocks()` — floorplan rectangles (not registered; always full opacity)
- `draw_hanan_grid()` — faint dashed grid lines (not registered)
- `draw_buses()` — topology segments at nominal coordinates (no NUTS)
- `draw_nuts_tracks(nuts_result)` — segments at NUTS-assigned track positions; faint interval-constraint bands behind each segment (registered as `is_band=True`)
- `draw_detailed_tracks(detailed_result)` — individual bit-wire lines at concrete track positions (per-type visibility toggles); see [Detailed Viz](docs/detailed_viz.md)
- `draw_preroutes(routing_grid, layer_stack)` — VDD/GND/CLK/SHIELD pre-route bands from the first-class `PreRoutedSegment` objects (`RoutingGridStack.preroutes`); works in the abstract view too; the `[Preroutes]` button cycles per-type visibility (off → ALL → POWER → GROUND → CLOCK → SHIELD)

---

### Stage 8 — Routing Grid (`routing_grid.h/cpp`) — IMPLEMENTED

**Responsibility:** Define the physical track structure of each metal layer — which track slots are POWER, GROUND, CLOCK, SHIELD, or SIGNAL — and expose this to both abstract NUTS (for dilution) and detailed NUTS (for exact track enumeration).

**Key types:**

`TrackSlot`
- `type`: extensible enum `{ POWER, GROUND, CLOCK, SHIELD, SIGNAL, CUSTOM }`
- `label`: string (`"VDD"`, `"GND"`, `"CLK1"`, user-defined)
- `width`: double (track width in layout units)
- `space_after`: double (gap to the next slot)

`TrackPattern`
- `origin`: double — global anchor from chip origin (0,0); ensures all Hanan channels tile on the same phase
- `slots`: `vector<TrackSlot>` — one repeating unit (e.g. `[VDD(w=2), sig, sig, sig, sig, GND(w=2), sig, sig, sig, sig]`)
- `unit_pitch()` → sum of all `width + space_after` in one unit
- `signal_density()` → sum of signal widths / unit_pitch
- `dilution_factor()` → 1.0 / signal_density (fed to abstract NUTS stage 4)
- `tracks_in_range(lo, hi)` → `vector<(abs_position, TrackSlot)>` — enumerates all track centre positions within a perpendicular interval

`PatternOverride`
- `region`: `Rect` (Hanan-cell-aligned)
- `layer_id`: int
- `pattern`: `TrackPattern` with its own local `origin`
- Power/CLK segments are **broken** at region boundaries (DRC gap accepted)

`RoutingGrid` (per layer)
- `global_pattern`: `TrackPattern`; `overrides`: `vector<PatternOverride>`; plus a list of `keepouts`
- `init(pattern, is_horizontal)` — set the global pattern and routing direction
- `effective_pattern_at(x, y)` → first matching override, else global
- `signal_tracks_in(x, lo, hi)` → only SIGNAL-type slots within the interval

`RoutingGridStack`
- `define_layer(layer_id, pattern, is_horizontal)`
- `add_override(layer_id, x1, y1, x2, y2, pattern)` / `add_keepout(layer_id, x1, y1, x2, y2)`
- `get_layer_grid(layer_id)` → `RoutingGrid&`; `has_layer(layer_id)`

**Python hooks:** `RoutingGridStack`, `TrackPattern`, `TrackSlot` fully exposed to Python so users can build, inspect, and override patterns programmatically without recompiling.

**`.buda` commands:**
```
def_track_pattern <layer_id> <origin> [<type> <width> <space_after>] ...
add_grid_override  <layer_id> <x1> <y1> <x2> <y2> <origin> [<type> <w> <sp>] ...
```

---

### Stage 9 — Detailed NUTS (`detailed_nuts.h/cpp`) — IMPLEMENTED

**Responsibility:** Expand each abstract `BusSegment` (from stage 4) into N concrete `NetSegment`s, one per bit-wire, snapped to exact signal track positions from the `RoutingGridStack`. Pre-route blockages (POWER, GROUND, CLOCK, SHIELD) are hard constraints (they are simply not SIGNAL slots, so bits cannot land on them).

**Key types (as built — plain structs, not a `PlacedSegmentBase` hierarchy yet; see "target state" below):**

`BusSegment` — abstract bus geometry handed to stage 9 (one per selected topology segment)
- `bundle_id`, `seg_idx`, `layer`, `span_lo/hi`, `interval_lo/hi`, `bit_width`
- `bit_order`: string `"LO_HI"` / `"HI_LO"` (default LO_HI)
- `timing_critical`: bool — if true, all bits must land on contiguous signal tracks for uniform RC
- `connections`: `vector<BusSegmentConn>` — explicit connectivity for bit-wire span adjustment
- `abstract_pos`: stage-4 track position used to anchor bit ordering (NaN = fallback)
- `track_lo_bound`/`track_hi_bound`: corner-resolution clamp so a segment stays on the bounded side of a cross-trunk-layer split

`NetSegment` — one bit-wire; output of stage 9
- `bundle_id`, `seg_idx`, `bit_index` (0-based position within bus)
- `track_position` (track centre), `width` (from the `TrackSlot`), `layer`, `span_lo/hi`

`NetVia` — one per-bit layer transition; output of stage 9
- The bundle-level symbolic bus-via fanned out to individual bits: same `(bundle_id, from_seg, to_seg)` key (with `from_seg < to_seg`), one row per `bit_index`
- `from_layer`/`to_layer` (the two bits' layers, always different), `x`/`y` = the per-bit crossing of the two bits' placed tracks
- `bit_index` is the **logical** bit (`bit_order` already applied), so `net_names[bit_index]` resolves the bit's net with no HI_LO re-indexing

`DetailedNUTSResult`
- `net_segments`: `vector<NetSegment>`
- `net_vias`: `vector<NetVia>`
- `num_unplaced`: int

`DetailedNUTSEngine(stack).run(bus_segments)` drives the placement.

**Algorithm:**
1. For each `BusSegment`, call `signal_tracks_in(x, interval_lo, interval_hi)` on the effective `RoutingGrid` to get the available signal track list (power/clock/shield slots are excluded).
2. Take the first `bit_width` signal tracks (LO_HI) or last `bit_width` (HI_LO).
3. If `timing_critical`, verify the selected tracks are contiguous (no power/clock track between them); if not, search for the tightest contiguous window of `bit_width` signal tracks within the interval.
4. Emit one `NetSegment` per track with `track_position` = track centre, `width` = track width from `TrackSlot`.
5. Span-adjust bit-wires so bit i reaches its connected segments' same-bit tracks, then emit one `NetVia` per connected bit pair on **different layers** (deduped per symmetric conn; same-layer touches and unplaced counterparts produce no via).

**`.buda` command:**
```
run_detailed_nuts [lo_hi|hi_lo]
```

**Visualization hook:** `draw_detailed_tracks(detailed_result)` draws individual bit-wire lines at their concrete track positions, with per-type visibility toggles (`[VDD] [GND] [CLK] [SIGNAL]`) as matplotlib `Button` widgets. Per-bit vias are drawn as one scatter `PathCollection` per (bundle, upper layer) — lazy-built with the bit-wires, gated by `[Detailed]` **and** `[Vias/Conns]` — see [Detailed Viz](docs/detailed_viz.md).

---

### Connectivity model & verification (`conn_topology.h/cpp`, `verify.h/cpp`)

These cross-cutting modules sit beside stages 2–9 and guard correctness.

**`ConnTopology`** augments a raw `Topology` with explicit connectivity and slide ranges.
Since the topo/conn unification ([plan + status](docs/internal/topo_conn_unification.md))
the six derivation passes live in `topology_analysis.h/cpp` and their result is
**cached on the Topology** (content-fingerprint-validated — the fingerprint is
also the persisted `topo_uid` candidate identity), so every stage's
`ConnTopology::build` serves the shared cached analysis; `topo_edit.h/cpp`
provides the transactional expert-edit operations (engine for the `edit_*`
CLI commands and the explorer's edit mode):
- Infers connections geometrically — busterm-face membership, shared endpoints, and T-junctions — producing a `ConnSeg` per segment with a `perp_slide` range (`perp_lo`/`perp_hi`) over which the segment can move while every connection stays valid.
- Computes `net_pull` (which way a segment "wants" to slide to shorten connected stubs) used as a NUTS placement preference.
- `trunk_mst(...)` builds a Kruskal MST (`compute_mst` over `manhattan_nearest` distances) connecting a trunk to any blocks not yet directly attached — drives large-fanout / multi-block topologies.

**`verify`** runs design audits at three granularities, surfaced by the `check_design` CLI command (legacy alias: `check_connectivity`):
- `check_topo` — nominal positions from `ConnTopology` (SEG continuity, busterm-face validity, block coverage incl. pass-through blocks, and feedthrough-relay detection).
- `check_nuts` — same checks at NUTS-placed positions, plus **layer-direction** validity (H segment on an H layer, V on a V layer — an unbuildable wire otherwise) and the **keepout audit** (`KEEPOUT_CROSS`: a placed segment whose extent lies on a keepout overlapping its span — the exhausted-window commit `num_keepout_conflicts` counts; zones from the session floorplan via `zone_fp`, so hier bundles' cell-local floorplans can't mask a conflict).
- `check_dnuts` — per-bit checks on `NetSegment` positions after detailed NUTS, plus unplaced-bit detection and the per-bit keepout audit (defense-in-depth: DNUTS's cull removes crossing bits in production).
Violations are typed (`SEG_OPEN`, `BUSTERM_OPEN`, `BUSTERM_FACE`, `UNPLACED`, `LAYER_DIR`, `FEEDTHRU_RELAY`, `KEEPOUT_CROSS`, `NET_DRIVER_OPEN`, `ANTENNA` — a segment attached to the route at FEWER THAN TWO points (at most one busterm tap or seg junction), so everything past that single attachment is a dangling wire terminating in nothing: electrically inert metal the generator should not have emitted (issue #482; structural, reported by check_nuts + check_dnuts, counting DISTINCT attachment positions — busterm taps, seg junctions and pass-through blocks — via the shared `seg_attachment()` predicate the trunk+MST generator also gates on, so a hybrid whose SEED TRUNK is an antenna is dropped at generation (issue #485, docs/internal/seed_trunk_antenna_2026-07.md); also flags the TAP-OVERHANG form (issue #514): a terminal piece past the segment's last junction, lying entirely over the tapped block, that exists only to reach a face the block did not need — the far-face tap IS an attachment so the count rule is blind to it; flagged only when the block stays covered without the piece (a load-bearing tap is kept), declared feedthru blocks exempt. The spine-relay completion no longer produces the shape (J-anchor: the spine stops at its outermost tap, the hub covered by the crossing); generation's own candidate-level knob is `set_drop_dangling`), `DISCONNECTED` — the wire graph splits into 2+ islands (SEG junctions + same-tapped-block continuity): every block tapped, every junction touching, yet the net cannot be electrically complete — the hand-edit escape check_topo/check_nuts/check_dnuts all run, `BIT_SHORT` — two different bits of one bundle, i.e. two different nets, sharing a layer+track over an extended span in dnuts: the same-bundle track-sharing exemption's blind spot once fan-in taper makes per-segment bit subsets possible). `check_design all` audits every candidate topology. **`NET_DRIVER_OPEN`** (Python-side, flat flow) is the net-driver fidelity check: every net endpoint block of a bundle must appear in the topology's `connected_block_names` contract — the check whose absence let a CONVERGENT multi-driver bundle silently route from one driver (see docs/internal/convergent_bundling.md). **`FEEDTHRU_RELAY`** (check_topo only) flags a single-rect block whose connected segments' wires do not actually touch — i.e. the block is silently used as a feedthrough relay. A *feedthru* (a block that connects ≥2 of a bundle's stubs via its own lower-level/intra-block routing) is an **opt-in option** (`set_feedthru`): when a bundle busterm the trunk passes through opts in, the spine is split at its faces (two BUSTERM landings) and `check_topo` skips the `FEEDTHRU_RELAY` for blocks listed in `topo.feedthru_blocks` — but an *undeclared* relay is still flagged, so every topology must otherwise be physically self-connected. A straight trunk *crossing* an unrelated block (not a bundle busterm) is one continuous wire (a pass-through, no BUSTERM conn) and is NOT a feedthru.

---

### Physical Design Database — BDB (`bdb.h/cpp`)

**Responsibility:** SQLite-backed central store for the hierarchy-aware (v3) flow. All other modules read physical-design data through BDB rather than ad-hoc structures. Lives in `buda_core` and is registered in pybind11 by the `buda_db` module.

**Row types** (returned to Python / other modules): `ComponentRow` (hierarchical instance: parent_id, depth, bbox, is_leaf, is_replicated, orient — the rotation/mirror token for GDS round-trip), `NetRow`, `PinRow` (net↔component pin with dir + absolute position), `NetPropsRow` (hpwl, fanout, bus_name, bit_index, bundle_id), `BustermRow` (routing interface: hier_path, depth, bbox, resolution BLOCK/SPATIAL_CLUSTER/PORT, optional multi-rect JSON), `BundleRow`, `GrpRow` (group tree), `CellRow`, `CellPinRow` (cell-type port interface).

**Capabilities:**
- **Ingestion:** `import_def_lef`, `import_verilog` — self-contained parsers (no OpenDB / Cadence / Si2 dependency), detailed below.
- **Hierarchy construction:** `add_cell` / `add_cell_pin` define cell types; `add_inst_to_cell` defines a cell's internal structure; `add_inst` places an instance and eagerly expands all `cell_children` into component rows.
- **Net wiring:** `add_net_pins` derives instance pins from `inst/path.pin` endpoints and inserts interface pins at every ancestor between leaf and common-ancestor (hierarchy propagation). Direction variants: `_undirected` (UNKNOWN, positional fallback) and `_inout` (INOUT = secondary driver).
- **Mutations:** `move_comp`, `set_comp_bbox`, `resize_cell`, `flip_comp`, `rotate_comp` (90/180/270, keeping lower-left fixed; both compose the instance's `orient` token so it stays consistent with the bbox for GDS export).
- **Computed properties:** `compute_hpwl`, `compute_fanout`.
- **Busterms / bundles / groups:** `add_busterm`/`clear_busterms`; group tree mirrors the Python `GroupTree` API.
- **Queries:** `all_components`, `components_at_depth`, `pins_by_comp`, `nets_by_hpwl`, `comps_in_rect`, `common_nets`, etc. Hot read paths use cached prepared statements.

Busterms are derived from the hierarchy by `BustermGen` (`derive_busterms`, Phase A of the hier pipeline). The hierarchy-aware bundler (`HierarchicalBundler`) and topology/planner `hier` variants consume this data. See [BDB Reference](docs/BDB_REFERENCE.md) and [HBundle pipeline notes](docs/session_hbundle_pipeline.md).

#### Design ingestion & interchange

All importers are hand-written, line-by-line state machines in `bdb.cpp` (no external EDA library). The two together populate the same `component` / `cell` / `net` / `pin` tables and are designed to be run **in sequence** — placement from DEF, hierarchy from Verilog. All stored coordinates are **µm**.

**`import_def_lef(def, lef)` — physical placement (`bdb.cpp::import_def_lef`)**
- **LEF first** (`_parse_lef_sizes` + `_parse_lef_pins`): walks `MACRO … END` blocks. `SIZE w BY h` → cell footprint (fills the `cell` table); each `PIN … END` block contributes a port whose offset is the **centroid of its `RECT` shapes** and whose `DIRECTION` becomes the pin dir (missing → `UNKNOWN`). Pins with `USE POWER|GROUND|CLOCK` are skipped (they are pre-routes, not signal terminals). Everything else in the LEF is ignored.
- **DEF second:** a three-state machine (`IDLE → IN_COMPONENTS → IN_NETS`). Reads `UNITS DISTANCE MICRONS` (the integer→µm divisor), `DIEAREA ( 0 0 ) ( x y )` (sets `die_w/die_h`), each `COMPONENTS` entry `- inst cell + PLACED|FIXED ( x y ) orient` (depth-0 leaf component, bbox = DEF origin + LEF `SIZE`, default `0.5×0.5` if the cell is absent from the LEF; the `orient` token is recorded in `component.orient` — DEF↔BDB convention mapped, dims swapped for 90/270 — so a placed design round-trips through GDS export), and each `NETS` entry `- net … ( inst pin ) …` (creates `net` + `net_props` rows and a `pin` row per connection, resolving absolute pin position and direction from the LEF). DEF name escaping (`\[`, `\]`) is stripped so names match Verilog-elaborated paths.
- **Clears** `pin`/`net_props`/`net`/`component`/`cell` first — a fresh load. Components have `depth=0` and no parent until `import_verilog` overlays the hierarchy.

**`import_verilog(v)` — logical hierarchy (`bdb.cpp::import_verilog`)**
- **Phase 1** scans every `module` declaration, recording definition order; the **top module is the last module not instantiated by any other** (no explicit top needed).
- **Phase 2** parses each module body: instance lines (`cell inst ( .port(net), … );`) and port directions (`input/output/inout`). A custom `parse_portmap` handles `\`-escaped names, bit-selects (`net[3:0]` → base name), constants/concatenations/`UNCONNECTED` (skipped), and nested parens; a Verilog keyword set filters out non-instance statements.
- **Elaboration** walks from the top module, expanding instances into hierarchical `component` rows (dotted `parent/child` paths, growing `depth`) and wiring `net`/`pin` rows from the port maps. Instance pins default to `UNKNOWN`, then are overridden per-pin from any matching `cell_pin` direction (`infer_pin_dirs_from_cell_pins`).
- **UPSERT, not replace:** when run after `import_def_lef`, it `INSERT … ON CONFLICT DO UPDATE`s `cell`/`parent_id`/`depth`/`is_leaf` but **preserves `x1..y2`** so DEF placement survives. Components only in the Verilog get `x1=y1=x2=y2=−1` (unplaced); components only in the DEF keep their placement with no parent/depth. This is the canonical "DEF + Verilog merge" flow.

**Interchange formats (roadmap in [docs/internal/gds_oa_interchange.md](docs/internal/gds_oa_interchange.md)):**
- **GDSII import + export** — round-trip a GDSII layout against BDB. **IMPLEMENTED, Phases G0–G4** (`src/gds_io.cpp`): import — structures→cells with recursive footprints, SREF/AREF→component hierarchy with bbox-level transforms, PROPVALUE instance names, TEXT-label net/pin recovery so a labeled GDS runs the hier flow with zero Verilog, and `def_gds_layer` layer mapping so shapes on mapped routing pairs are excluded from cell footprints; `tools/gds_build.py` is the deterministic Python test writer (G0).
  - *Export (`export_gds`, Phase G4):* stream the placed-and-routed result from the persisted BDB tables — cells as structures with outline rects + SREF children, `net_segment` bit-wires (abstract `bus_segment` fallback) as rectangles on their `def_gds_layer`-mapped pairs, vias as squares, one net-name TEXT per pin — deterministic bytes (zeroed timestamps), re-importable with the same map: the import↔export round-trip is tested (`test/tests/test_gds_export.py`) and demoed (`tools/gds_demo.py`).
  - *Import:* read GDSII structures back into BDB component/cell rows — `BOUNDARY`/`BOX` shapes become cell or blockage geometry, `SREF`/`AREF` placements rebuild the component hierarchy, layer→`(layer, datatype)` mapping is inverted. Net connectivity is **optional and file-dependent**: GDS has no standard netlist, but many flows annotate shapes with net names via `TEXT`/label records (on a pin/label layer) or a labeling convention. The importer should support both modes — (a) *connectivity present:* parse the labels to recover `net`/`pin` rows; (b) *geometry only:* import placement/shapes and pair with `import_verilog` for nets, as with DEF today. A flag/auto-detect selects the mode per file.
- **OpenAccess (Si2 OA) import/export** — round-trip designs through an OA design database (`oaDesign`, `oaBlock`, `oaInst`, `oaNet`) so BUDA can sit inside an OA-based flow. Gated on the proprietary OA C++ libraries, so it would live behind an optional CMake feature flag and a separate translation module (e.g. `oa_bridge.cpp`) rather than in `buda_core`. Until then, LEF/DEF + Verilog is the supported interchange path.

Only the OA bridge remains roadmap; when implementing, follow the existing pattern: a standalone parser/writer in its own translation unit, populating or reading the same BDB tables, with coordinates normalized to µm.

---

### Floorplanner (`floorplanner.h/cpp`, `placement_optimizer.h/cpp`, `tools/`)

**Responsibility:** A separate **interactive placement tool** (not part of the routing pipeline) for editing block positions in a BDB and handing off to the hier routing flow.

- **`FloorplannerEngine`** (C++) — die/grid, top-level and child blocks, raw move/resize, align (top/bottom/left/right), grid snapping, `validate()` (overlap / out-of-die / error issues), and `write_bdb(BDB&)` to persist placement. Cross-module `BDB&` passing works because both modules share `buda_core` (see Build).
- **`PlacementOptimizer`** (C++) — simulated annealing (SA) and genetic algorithm (GA) placement with per-block constraints (Fixed / Reshapeable / min W/H) and weighted cost (wirelength / area / overlap). Exposed via `bind_optimizer.cpp`.
- **GUI** (`tools/bdb_floorplanner.py` + `tools/floorplanner_commands.py`) — Tk/matplotlib editor: drag/resize, align/distribute, SA/GA optimize, live HPWL + flylines, validation, and **Run Flow** (writes BDB → generates a hier `.buda` script → runs `buda_cli.py` for immediate routing feedback).
- **Launchers:** `bin/fp [file.bdb]` opens the GUI; `bin/bfp tc1|tc2|<file.bdb>|<script.buda>` adds built-in demo scenarios (`tools/fp_demo.py`) and flow integration.

**Other `tools/`:** DEF/LEF net-clustering visualizers (`def_cluster.py`, `def_viz*.py`, `def_viz_shared.py`), `group_tree.py` (group hierarchy + JSON persistence), `viz_ipc.py` (Unix-socket selection sync between `buda_viz` and `def_viz`), `show_detailed_shorts.py` (report bit-level detailed-NUTS shorts), `scan_fanin.py` (report fan-in merges — shared-receiver / different-driver net groups — across all flows/demos; the CONVERGENT-bundling candidate scan, see `docs/internal/convergent_bundling.md`), `render.py` (headless: pin one bundle's candidate in a `.buda` flow, run planner→NUTS→DetailedNUTS, print `dump_topologies --conn`, and render a topology/NUTS/DNUTS triptych PNG — `tools/render.py <flow.buda> --bundle <id> --topo <id> [--zoom]`), and `qor_corpus.py` (the "did my change help or hurt?" QoR sweep: source a curated corpus of full-pipeline flows and capture each one's final `(overlaps, unplaced, viol_bundles)` — the last is the number of bundles with `check_design` violations, so an overlap-free-but-electrically-broken route still shows up — then `--compare base.json branch.json` diffs a baseline build against a branch build on QoR + runtime, exiting non-zero on any regression; the recipe every topology/planner/NUTS change should run: `git checkout main && bin/bb && tools/qor_corpus.py --out base.json`, then the same on the branch, then `--compare`).

---

## Segment Type Hierarchy (as built — `placed_segment.h`)

```
PlacedSegmentBase          kind, layer, span_lo/hi, track_position, width, placed
├── TrackSegment (BUS)     stage-4 placed bus segment: bundle_id, seg_idx, horiz,
│                          interval, net_pull, pull_target, is_jog, corner bounds
├── NetSegment (NET)       stage-9 placed bit-wire: bundle_id, seg_idx, bit_index
└── PreRoutedSegment       a POWER/GROUND/CLOCK/SHIELD track: label, slot_type,
    (PREROUTE)             track_index — enumerated by RoutingGridStack.preroutes()
```

Realized by Phase G of the NUTS/DNUTS refactor ([plan + as-built resolution](docs/internal/placed_segment_preroutes.md)). `BusSegment` (in `detailed_nuts.h`) deliberately stays OUTSIDE the hierarchy: it is the stage-9 *input descriptor* (intervals, bit_width, bit_order, connections) with no placement of its own — merging it with `TrackSegment` would rename bound pybind fields for zero behavior gain and is deferred to a binding-breaking version. The raw geometry type `Segment` in `topology.h` (start/end points + layer_hint) is a **pre-placement** concept and remains separate. Pre-routes remain non-SIGNAL track slots at solve time (blockage semantics unchanged); `preroutes()` materializes them as objects for viz/reporting/export.

---

## Source File Map

| Area | Files |
|---|---|
| Build / wrappers | `CMakeLists.txt`, `bin/bb` (build), `bin/buda` / `bin/fp` / `bin/bfp` / `bin/viz` / `bin/u2b` (run), `bin/activate` (source: PATH+PYTHONPATH), `pytest.ini` |
| DB layer (`buda_core` → `buda_db`) | `bdb.h/cpp`, `sqlite3.c/h`, `busterm.h/cpp`, `bundler.h/cpp`, `bundle_refiner.h/cpp`, `gds_io.h/cpp`, `bind_db.cpp`, `bindings_db.cpp` |
| Routing pipeline (`buda`) | `topology.h/cpp`, `conn_topology.h/cpp`, `topology_analysis.h/cpp`, `topo_edit.h/cpp`, `layering.h/cpp`, `congestion_planner.h/cpp`, `nuts.h/cpp`, `nuts_geom.h`, `nuts_dogleg.h/cpp`, `placed_segment.h`, `routing_grid.h/cpp`, `detailed_nuts.h/cpp`, `verify.h/cpp`, `floorplanner.h/cpp`, `placement_optimizer.h/cpp` |
| Bindings (`buda`) | `bindings.cpp`, `bind_bundler.cpp`, `bind_routing.cpp`, `bind_nuts.cpp`, `bind_optimizer.cpp` |
| Python | `src/buda_cli.py` (CLI core), `src/buda_cmds/` (command registry, one module per stage), `src/buda_session/` (BudaSession helper mixins + `util.py`), `src/buda_viz.py` (viewer façade/assembly), `src/viz_common.py` + `src/viz_window.py` (shared draw/zoom helpers; window-manager glue), `src/viz_explorer/` (TopologyExplorer mixins), `src/viz_main/` (BudaVisualizer mixins), `src/ui_state.py`, `tools/*.py` (floorplanner GUI + DEF/LEF viz) |
| Demos | `demo/*.buda` — user/designer-facing demo vehicles (comprehensive_demo, quickstart, ariane/mempool/nvdla/ispd19 showcases, …); see `demo/README.md` |
| Flows / tests | `flow/*.buda` — R&D / regression vehicles; shared track fixtures in `flow/tracks/`; `test/tests/*.py`, `test/tests/features/*.feature` |

---

## Dependencies

Full details (required / optional / bundled, versions, install cheat-sheet) in
[docs/build_test_dependencies.md](docs/build_test_dependencies.md). Summary:

- **pybind11** — C++/Python bindings
- **Python 3.13+**
- **matplotlib** + **tkinter** — visualization and floorplanner GUI
- **SQLite** — bundled as `src/sqlite3.c` (amalgamation; no system dependency)
- **pytest** + **pytest-bdd** — testing
- **pytest-xdist** *(optional)* — parallel test runs; `bb -m`/`bb -s` use it automatically when installed (`pip install pytest-xdist`). See [docs/internal/test/parallelism.md](docs/internal/test/parallelism.md).
- **CMake 3.15+**
