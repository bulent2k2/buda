# Feature-suite refresh — plan & coverage map

Bring the Gherkin/pytest-bdd `.feature` suite (`test/tests/features/`) back in
sync with the shipped system, and keep it that way. This page is the plan of
record and the living **arc → feature** coverage map; update it whenever a
feature file is added or an arc lands.

## Why this exists

The 40 `.feature` files (290 scenarios, ~4.7k lines) are an excellent,
richly-diagrammed snapshot of BUDA **as of roughly the topology-generation /
multicast / hier-planner era**. Since then every major arc's real test coverage
moved into **plain pytest**, and the Gherkin narrative never caught up. The
clearest tell: `ripup_reroute.feature` has **3** scenarios while
`test_ripup_reroute.py` has **~59** test functions — the entire RR speedup arc
(global-occupant pass, fast trials, fixed-context screen, `negotiate_congestion`)
is invisible in the spec layer.

### The five staleness modes found

1. **Frozen snapshots** — real coverage grew in pytest; the spec stalled
   (`ripup_reroute`).
2. **Design-ahead specs reality diverged from** — written against an imagined
   API that never shipped, now permanently `xfail`: `feedthru.feature`
   (`pass_through_count` API; shipped as `set_feedthru` / `topo.feedthru_blocks`),
   `multi_rect_block.feature` (`add_block_rects`), `hierarchy_depth_planning.feature`
   ("API design, not yet implemented").
3. **Orphaned / disabled** — never bound to a step file, so never run:
   `bundler_hierarchy`, `large_fanout_mst`, `layer_assignment`, and
   `topology_generation` (its `scenarios(...)` line is commented out).
4. **Doc-only mirrors** — the `.feature` is documentation but the tests were
   hand-written in parallel, not pytest-bdd-bound: `routing_grid`,
   `detailed_track_assignment`, `nuts_track_assignment`, `corner_margin`,
   `span_aware_layer_assignment`, `global_congestion`.
5. **Stale internals** — e.g. `topology_flexibility.feature`'s comment math
   (`slide=100`) contradicts its own asserted values (`slide=80`);
   `bundler_logic.feature` carries chat-style "NEW Scenario (Your Request)"
   comments.

There is also **no dedicated `check_design`/verify feature**, and there were **no
Gherkin `@tags`** — so a reader could not tell landed from aspirational.

## Philosophy — the narrative spec layer

`.feature` files are BUDA's **behavioral specification & narrative-documentation
layer**: the human-readable "what the system does and why," with the ASCII
diagrams. They are executable where the binding is cheap and documentation where
the real coverage already lives in pytest. Every scenario carries a tag saying
which it is.

### Tag vocabulary (see `test/tests/features/README.md`)

- `@landed` — matches shipped behavior. Bound to pytest-bdd and green, OR a
  narrative spec whose executable coverage lives in a named `test_*.py`.
- `@future` — spec ahead of code. Not bound (or `xfail` with a reason). Carries
  a `# see docs/…` pointer. This is the **honest** version of the accidental
  xfails found above.
- `@doc` — narrative mirror by design; the executable tests live in a named
  hand-written `test_*.py`, not pytest-bdd.
- `@mid` / `@slow` — mirror the `pytestmark` tier of the bound step file, when
  one exists, so the feature signals its cost.

A feature file with no step-file binding is inert under pytest (no auto-discovery
guard exists), so narrative `@future`/`@doc` files are safe to add.

## Phases

### Phase 0 — truth-in-labeling hygiene (no new coverage)
Stop the suite from actively misleading. Annotate, don't rewrite bound files
(rewriting scenario names would break `scenarios()` bindings):
- Add `test/tests/features/README.md` (this vocabulary + policy).
- Tag the three design-ahead files `@future` with a one-line pointer to the doc
  and a header note that the shipped API differs (leaves the existing `xfail`
  mechanics intact).
- Resolve the four orphans: annotate with `@doc`/`@future`/`@orphaned` + a header
  note explaining status and where the real coverage is (or that it is
  deprecated). No new step files in Phase 0.
- Fix the stale internals (`topology_flexibility` comment math, `bundler_logic`
  scratchpad comments).

### Phase 1 — spec the major landed arcs with zero coverage
New narrative feature files (tagged `@landed`/`@doc`, header points at the
executable `test_*.py`):
- `bottom_up_planning.feature` — `set_bottom_up <cell>|*`, `align_bottom_up`,
  `check_template_tracks`, rotation-class clone templates.
- `ripup_reroute.feature` (rewrite/expand) — stage-a overlaps + stage-b DNUTS
  opens, global-occupant pass (`no_global`), fast trials, fixed-context screen
  (`screen`/`no_screen`), `negotiate_congestion`.
- `bundling_strategies.feature` — CONVERGENT/BIDIRECTIONAL/COMBINED lattice,
  `set_bundling`, `set_max_bundle_bits` (static+auto), fan-in taper,
  `NET_DRIVER_OPEN`.
- `topo_edit.feature` — the `edit_*` transactional family, `P` pin-span,
  two-step trunk placement.
- `check_design.feature` — the verify spine, one scenario per violation type.
- `gds_interchange.feature` + `pipeline_resume.feature` — `import_gds` /
  `export_gds` / `def_gds_layer` round-trip; `open_bdb writeback` / `save_bdb` /
  `load_pipeline` checkpoint-resume.
- `planner_knobs.feature` — `signal_tracks`, `kPeak`, `refine_passes`.

### Phase 2 — refresh partially-stale existing files
`datapath_trunk` / `multi_level_trunk`: add hier `multi_trunk` and `teg_mode over`
rectilinear coverage; tighten the qualitative asserts the files themselves flag as
"documented follow-up."

### Phase 3 — honest `@future` roadmap specs
Encode the documented opens & future directions as pending, clearly-marked
scenarios so the spec leads the work. One `future_directions.feature` grouping:
OA bridge (opens #7), along-flex Stage C (#6), cross-level fan-in grouping & hier
`set_max_bundle_bits` (#8), Gap-4 non-TOP stub clamp (#4), straight/I-shape
feedthru + `feedthru_penalty`, multi-victim rip-up + planner-stage negotiated
congestion, and the `bigHalf` rr-flip decision (#10). Each carries a `# see docs/…`
pointer.

## Arc → feature coverage map

Updated as files land. `L` = `@landed`, `F` = `@future`, `D` = `@doc`.

| Arc / capability | Feature file | Tag | Executable coverage |
|---|---|---|---|
| Bottom-up hier planning | `bottom_up_planning.feature` | L | `test_hier_*`, bottom-up tests |
| Ripup speedup arc | `ripup_reroute.feature` | L | `test_ripup_reroute.py` |
| Bundling strategies / fan-in | `bundling_strategies.feature` | L | `test_bundler.py`, `test_hier_bundler.py` |
| TopoEdit | `topo_edit.feature` | L | `test_topo_explorer_edit_mode.py` |
| Verify / check_design | `check_design.feature` | L | `test_check_*`, `test_connectivity*` |
| GDS interchange | `gds_interchange.feature` | L | `test_gds_export.py` |
| Pipeline resume / save_bdb | `pipeline_resume.feature` | L | `test_bdb_*persist*`, resume tests |
| Planner knobs | `planner_knobs.feature` | L | `test_planner_*` |
| Roadmap / opens | `future_directions.feature` | F | — (pending) |

## Maintenance rule

When an arc lands, add or update its feature file **in the same PR** and refresh
this map. When a `@future` scenario's code ships, flip it to `@landed` and bind or
name its coverage. `opens.md` links here so the coverage view stays discoverable.
