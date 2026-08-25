# TEG and multi-rect block support — status appraisal

> 2026-08-22, measured on `main` @ 8a95e98e.  Sources: the Gherkin feature
> suite, the pytest suite, every `flow/`/`demo/` vehicle using the features,
> the docs tree, and the engine sources — plus two dynamic measurements
> (§1.1, §1.2) run on a fresh build of this commit.

**Multi-rect blocks** (`add_block <name> rect x1 y1 x2 y2 [rect ...]`) let a
floorplan block be a set of rectangles instead of one bbox.  **TEG mode**
(`teg_mode thru|over` on the rect form) declares whether those rects are
internally connected: `thru` (default) assumes the block's own routing joins
them, `over` declares they are NOT internally connected, so the generator must
emit an explicit **bridge segment** over the gap/notch whenever a trunk cannot
reach every rect.

The one-line honest summary: **both features are generation-complete and
downstream-thin** — with one amendment a Codex review of PR #820 caught and
§1.3 verifies: for the rectilinear (L-shape) branch the generated bridge is
itself geometrically incomplete, so "generation-complete" holds for the
shapes, not for the bridge's own connectivity.  Multi-rect geometry is threaded deeply through generation,
NUTS anchoring and all three verifiers; TEG-over bridges stop at generation +
persistence and are invisible to the planner, NUTS, DetailedNUTS, every
verifier, `report_wirelength`, the QoR corpus, and every exporter.  A design
that selects a bridged candidate routes **without the bridge** and audits
clean (§1.1) — which makes `teg_mode over`, today, a candidate-pricing
annotation rather than a routing feature.  *(2026-08-22: no longer — the 1(b)
TEG_OPEN audit and the 1(a) emission redesign landed; the trunk generator's
OVER connection metal is ordinary segments now and bridges are legacy-load
only.  See open 1 and §2.2.  For where the whole arc ended, see the Final
state section directly below.)*

## Final state (2026-08-25)

Every open in §4 is now either **struck-resolved** or **resolved-as-scoped**
(the individual entries carry the dates, the measurements and the pinning
tests).  The arc that started from §1.1's silent electrically-open route
ended with: TEG-over connection metal emitted as ordinary segments through
every Direct/gap/one-sided trunk shape (opens 1(a) + the 2026-08-25
residuals (i)/(ii)), the `TEG_OPEN` audit failing whatever placed metal
still misses a rect (1(b)), the `BUDA-1907` thru census and `BUDA-1908`
feedthru warning covering the silent-by-design corners (opens 5/7), the
planner/margin/TopoEdit multi-rect split-brains repaired (opens 3/9/16),
declaration validation + `set_teg_mode` (opens 13/14), and the QoR corpus,
flow tests and demo vehicles pinning it all end to end (open 2,
`demo/teg_hier_hybrid.buda` / `demo/teg_two_spellings.buda`).  Non-OVER and
single-rect designs stayed byte-identical throughout (corpus-guarded).

What remains is deliberate scope, each item verified against the code and
its pinning test on this date:

**Remaining limitations**

1. **MST candidates on OVER multi-rect blocks emit no TEG connection
   metal** (and the TRUNK+MST hybrids' multi-rect branch blocks mostly drop
   at generation as non-simple): an MST edge lands on the closest rect pair
   only, so an OVER block's other rects go unreached — the legacy path,
   scoped by open 1 residual (iii) (a fix is a redesign: there is no shared
   trunk locus to hang per-rect attachment from).  Pinned LOUD end to end:
   `test_teg_open.py::test_mst_on_over_block_fires_teg_open_end_to_end`
   (a selected MST_HV on an OVER receiver fires TEG_OPEN at both placed
   stages naming the unreached rect).
2. **The adjacent-rect Direct corner**: a trunk Direct inside one of two
   ADJACENT rects emits no connection metal — the rects are physically
   contiguous, the feature's suppression rule, transitive over the PHYSICAL
   (`Busterm::orig_rects`) touch graph — while the placed TEG_OPEN contact
   predicate reads per-rect, so such a route, if selected, still reports
   TEG_OPEN.  Documented loud corner (open 1); the suppression side is
   pinned by `test_teg_open.py::
   test_adjacent_chain_is_suppressed_and_separated_rect_still_stubbed` and
   `test_margined_adjacent_chain_keeps_physical_suppression_inset_taps`.
3. ~~**The same-band disjoint sibling** — a DISJOINT rect sharing the trunk's
   perp band (rects side by side along the spine, trunk inside one) gets no
   connection metal.~~  **RESOLVED 2026-08-25 via SPINE-END ANCHORING**, the
   machinery the withdrawn attempts said was needed — and the landing is the
   whole trick: the spine's span is extended to LAND exactly on the
   sibling's facing along-face, which `annotate_endpoints` tags as a real
   BUSTERM landing, so NUTS holds the end via `busterm_faces` `span_cover`
   the way it holds every face landing (the withdrawn BARE extension had no
   landing and was retracted by `do_span_adjustments`; the withdrawn
   over-the-cell anchoring stub tripped the #514 tap-overhang ANTENNA
   rule — this route carries neither, and #514 stays quiet because there is
   no terminal piece at all, just the spine ending on a tapped face).
   Direct trunks on disjoint OVER blocks only; which band rects extend is
   decided by a reached-component test (band rects the natural span
   intersects, expanded transitively over the PHYSICAL touch graph), so the
   ADJACENT corner (item 2) is untouched — an adjacent same-band pair still
   emits nothing and stays TEG_OPEN-loud (control-pinned).  Axis-
   parameterized, so the V-trunk twin (x-band siblings) is covered by the
   same code (test-pinned, not assumed).  Building it MEASURED the #823
   shadow trap the fix was suspected to have died on: with the sibling
   BEYOND the far endpoint block, the spine lands on the SAME block at BOTH
   ends (landing rect's face + sibling face) and `derive_conn_segs`' per-
   BLOCK BUSTERM dedup dropped the second landing — NUTS then retracted the
   sibling end to the far block's face (span [400,650] against a face at
   800, TEG_OPEN at both stages).  The dedup is now per FACE COORD for
   MULTI-RECT blocks only (`add_conn`'s `multirect_bt` flag — two real
   landings on different rects' faces are physically distinct anchors),
   while single-rect blocks keep the per-block shadow rule byte-identically.
   The min-stub floor does not apply (the extension is collinear SPINE
   metal, not a stub — a floor larger than the sibling gap must not reject
   the trunk, test-pinned) and the margin semantic is the established one
   (tappable = inset faces: the spine lands on the sibling's INSET face,
   inside the physical rect, so the per-rect contact audit sees it;
   touching = physical rects in the reached-component).  Measured on the
   repro: `TRUNK_H@y50` now generates ONE spine (100,50)-(600,50) — WL 200
   → 500, the honest price of reaching the sibling — routes 1 TrackSegment
   / 4 bit-wires / 0 unplaced with both placed audits clean where it fired
   TEG_OPEN at both (1 bundle-level at nuts, 4 per-bit at dnuts).  Vehicle:
   `flow/teg_same_band.buda` (EXPECTED CLEAN, guarded by
   `test_flow_scripts.py::test_teg_same_band_flow_routes_clean`); unit pins
   in `test_teg_open.py` — the flipped
   `test_same_band_disjoint_sibling_routes_clean_via_spine_anchoring`, the
   far-side dedup shape, the V twin, a 3-rect same-band chain (farthest
   sibling tapped, middle one crossed as a pass-through), the min-stub-floor
   edge, the margined variant, and the adjacent-pair loud control.
4. **BITRUNK trees (legacy and two-level) are bbox-only on multi-rect
   blocks** — no rect selection, no TEG connection metal
   (resolved-as-documented open 8); an unreached OVER rect fires TEG_OPEN,
   pinned end to end by
   `test_teg_open.py::test_bitrunk_on_over_block_fires_teg_open_end_to_end`.
5. **No import path (DEF/GDS) and no hier declaration produces a multi-rect
   block** — a BDB component is one bbox (opens 6/17;
   `opens_interchange.md` item 16): `derive_busterms` writes empty rects,
   every BDB→Floorplan projection is `add_block(bbox)`, and
   `tools/buda2bdb.py` collapses to the union bbox with a warning naming
   the dropped trailing modifiers, `teg_mode` included (pinned by
   `test_buda2bdb.py::test_multirect_collapse_warning_names_dropped_modifiers`).
   Multi-rect/TEG is script-declared only; the honest hybrid recipe is
   `demo/teg_hier_hybrid.buda`.
6. **The web client renders no legacy-load bridges** — `src/web/serialize.py`
   still serializes a restored candidate's `bridge_segments`, but no web
   renderer draws them (open 10's noted remnant); the matplotlib explorer
   and main viewer DO (`viz_common.draw_legacy_bridges`, pinned by
   `test_viz_legacy_bridge.py`).  Live designs are unaffected — generation
   emits no bridges.
7. **`set_feedthru` on a multi-rect block remains inert in the engine**
   (the feedthru relay is single-rect MVP — `topology.cpp` skips
   `rects.size() > 1`); the declaration now warns instead of dropping the
   intent silently (BUDA-1908, both declaration orders — open 7, pinned by
   `test_set_feedthru_multirect_warning.py`).

## 1. Two measurements that anchor this appraisal

### 1.1 A selected bridge is never built, and no audit notices  (CRITICAL)

> **RESOLVED 2026-08-22** in two landings: the 1(b) TEG_OPEN audit (PR #821)
> made the miss LOUD, and open 1(a) (this branch) removed the miss — the
> §1.1 vehicle's `TRUNK_V@x250` now generates a real connector leg to the
> tall arm instead of a bridge, routes 3 TrackSegments / 12 bit-wires, and
> audits clean.  The measurement below is kept as the pre-change record.

Pin a bridged candidate on the `flow/lShape1.buda` geometry (4-bit bus,
`TRUNK_V@x250`, bridge over `L` at `(400,0)-(400,400)`), then run
`run_planner` → `run_nuts` → `run_detailed_nuts` → `check_design` →
`report_wl`.  Measured on this commit:

- NUTS places **2** TrackSegments (trunk + stub) — no bridge.
- DetailedNUTS emits **8** bit-wires — none on the bridge line.
- `report_wl` total 672 — no bridge metal counted.
- `check_design` (dnuts stage): **"Success: no violations found."**

Under `over` semantics the bridge is *required* metal (the block does not
internally connect its rects), so the routed net is electrically open at the
un-bridged rect group — and nothing says a word.  The audit half is its own
gap: `check_nuts`/`check_dnuts` never read `Topology::bridge_segments`
(`grep bridge src/nuts.cpp src/detailed_nuts.cpp src/congestion_planner.cpp`
returns nothing; `src/verify.cpp`'s only "bridged" notion is the unrelated
declared-feedthru island exemption at `verify.cpp:621`), and
`detect_disconnected` unions all same-block taps as internally continuous
regardless of `teg_mode` (`src/verify.cpp:203-209`) — the exact assumption
`over` exists to revoke.

A second-order effect seen in the same run: NUTS slid the trunk from its
nominal x250 to x392 (inside the base rect), where generation would have
suppressed the bridge — whether a *placed* topology still needs its bridge is
placement-dependent, and nothing re-evaluates it either way.

### 1.2 The L-shape flow's "never triggers" comment is false  (stale doc)

`flow/lShape1.buda:16-19` (and its byte-duplicate
`flow/scenario5_lshaped.buda`) claimed "the OVER logic never triggers" on the
L-shape and that `TRUNK_V@x250` connects Direct with no bridge.  Measured on
this commit: **7 of 18** generated candidates carry bridges, including
`TRUNK_V@x250` itself (bridge `(400,0)-(400,400)`).  The comment predated the
rectilinear partial-span bridge (`src/topology.cpp:3085-3100`); the `@landed`
feature scenario (`busterm_over_the_block.feature:163`) is the correct record.
RESOLVED on this branch (2026-08-22): the comment now states the measured
behavior, and the unreferenced byte-duplicate `scenario5_lshaped.buda` is
removed (only `lShape1.buda` was referenced, by the collinear-stub scan
tools).

### 1.3 The rectilinear bridge does not touch the rect it exists to connect
(raised by the Codex review on PR #820; verified against the code)

> **RESOLVED 2026-08-22 with open 1(a)**: the union-face bridge is no longer
> emitted by either branch — the rectilinear branch emits a perpendicular
> connector leg per un-spanned rect (contacting the rect AND T-junctioning
> the trunk), the disjoint-gap branch emits per-rect stubs joined through the
> trunk (its floating bridge simply dropped).  The analysis below is kept as
> the pre-change record.

The rectilinear branch (`src/topology.cpp:3090-3100`) emits the bridge as one
segment along the **union bbox's perp-hi face** — which only touches the
rects whose extent reaches that face.  On the §1.1 L-shape (tall arm
`(0,0)-(100,400)`, wide base `(0,0)-(400,100)`):

- `TRUNK_V@x250`: bridge `(400,0)-(400,400)` lies on the base's right edge
  and never touches the tall arm (x ≤ 100) — yet the tall arm is exactly the
  rect the trunk missed.
- The `TRUNK_H` family (y125..y325): bridge `(0,400)-(400,400)` lies on the
  tall arm's top edge and never touches the base (y ≤ 100) — the rect the
  trunk missed, mirrored.

In both orientations the un-reached rect touches *nothing* (the branch emits
no stub either — "no extra stubs" is its design), so under `over` semantics
the emitted geometry is already open at generation time.  The disjoint-gap
branch (`:3104-3138`) does not share the defect the same way: there both
rects get stubs to the trunk, so connectivity holds through the trunk and the
union-face bridge is redundant metal rather than a missing link.
Consequence for open 1: carrying bridges downstream (1a) is not sufficient —
the rectilinear branch needs its bridge routed to actually contact each
un-spanned rect (or a connecting leg added) first.  The 1(b) audit would
catch this shape too, which is another reason to land it first.

## 2. Engine support map

### 2.1 Multi-rect blocks

**Implemented** (with the load-bearing sites):

- Data model: per-rect list + margin-inset union bbox + `teg_mode` on
  `Busterm` (`src/topology.h:142-150`); `Floorplan::add_block_rects`
  (`src/topology.h:580-586`, impl `src/topology.cpp:360-393`; empty list is a
  hard error, audit C4-04).
- Generation: trunk best-fit rect per locus (`src/topology.cpp:2722-2793`),
  Hanan grid from every rect's edges (`:512-524`, `:3312-3336`), LOW-layer
  keepouts **per rect** so the notch stays routable (`:448-467`), MST edges
  between closest rect pairs (`:3756-3790`), per-rect endpoint annotation and
  face taps (`:1448-1482`, `:2191-2200`), fan-in `seg_bits` per rect
  (`:1568-1587`).
- NUTS: per-rect pass-through crossings and anchor election
  (`src/nuts.cpp:271-323`, `:940-957`).
- Verify: rect-aware face/coverage/antenna/feedthru checks in all three
  stages (`src/verify.cpp:57-87`, `:286-288`, `:741-767`, `:927-940`,
  `:1183-1230`).
- Persistence: `BustermRow.rects` JSON + `teg_mode` (schema v1/v9,
  `src/bdb.cpp:380-390`; codec `src/busterm.cpp:27-70`), written/restored by
  the seg-busterm persist bridge (`src/bind_routing.cpp:107-192`);
  rects+teg_mode in the topology fingerprint (`src/topology_analysis.cpp:61-63`);
  hier offset/rotate transforms rects (`src/topology.cpp:49`, `:180`).
- Viz: every rect drawn + dashed union box (`src/viz_common.py:210-246`); web
  JSON carries real rects (`src/web/serialize.py:107-124`).  (This bullet
  originally over-claimed the explorer half: `_bundle_hanan_grid` treated
  `get_block_rects`' 4-tuples as Rect objects, so OPENING the explorer on any
  multi-rect bundle crashed with `'tuple' object has no attribute 'x1'` —
  found in the field on `lShape1.buda` 2026-08-23, fixed with a headless
  regression test in `test_explorer_multirect.py`.)

**Partial / inconsistent:**

- ~~`corner_margin` shrinks only the **union** bbox; individual rects are never
  inset (`src/topology.cpp:3296-3300`), so a per-block margin is silently
  inert for the faces multi-rect actually uses.~~  RESOLVED 2026-08-23 — see
  open 9 (`shrink_rects` insets each rect; `orig_rects` keeps the physical
  spelling).
- ~~Planner split-brain: cut **capacity** is carved per rect (via
  `low_layer_keepouts`, `src/congestion_planner.cpp:363-383`) but
  `low_seg_obstructed` tests the **union bbox** (`blocks_cache_` from
  `get_all_blocks()`, `src/congestion_planner.cpp:738-784`) — a LOW segment
  in a routable notch is priced as "crossing the cell" and escalated to TOP.~~
  RESOLVED 2026-08-23 — see open 3 (`leaf_rects_cache_`; the predicate judges
  the same per-rect geometry capacity is carved from).
- Trunk+MST hybrids: a multi-rect branch makes the tree "non-simple", so it
  keeps the legacy un-completed relay shape and is usually dropped as
  `FEEDTHRU_RELAY` (`src/topology.cpp:4077-4082`, `:4680-4685`).  (Still the
  case — the hybrid half of open 1 residual (iii), Final-state item 1.)
- ~~`topo_edit`: `edit_add_stub`/`edit_add_trunk` seed the tap's rects+teg_mode
  (`src/topo_edit.cpp:196-203`) but compute the face and overlap from the
  union bbox (`:165-185`) — a hand-built stub can land in the notch.~~
  RESOLVED 2026-08-25 — superseded by open 16 (`edit_add_stub` goes through
  generation's `best_rect` per-rect selection, with the THRU/OVER mode split).
- NUTS slide windows union the perp extents of all spanned rects, so a
  segment may legally seat over the notch or in a sibling rect — documented
  as deliberate (`src/nuts.cpp:296-302`), but it means "which rect did I
  attach to" is a placement outcome, not a topology property.

**Absent:**

- BITRUNK (legacy and two-level) works entirely on `orig_bbox`
  (`src/topology.cpp:4401`, `:4467-4473`) — no rect selection, no bridges.
  (Resolved-as-documented, open 8 — an unreached OVER rect fires TEG_OPEN,
  test-pinned; Final-state item 4.)
- The 2-pin L/Z/U/I family is **bypassed** for any multi-rect endpoint
  (`src/topology.cpp:4646-4652` forces the n-pin path) — safe, but the 2-pin
  generator itself is not rect-aware if reached directly via bindings.
- `set_feedthru` on a multi-rect block is skipped by the engine
  (`src/topology.cpp` — "MVP: single-rect only"); ~~no warning
  anywhere, the CLI validates only block/layer existence~~ the declaration
  now warns LOUD in both declaration orders (BUDA-1908, open 7; Final-state
  item 7).
- No import path produces a multi-rect block: DEF/LEF components get one
  bbox (rectilinear macros collapse), GDS import likewise; `tools/buda2bdb.py`
  collapses to the union bbox with a warning that now also NAMES the dropped
  trailing modifiers, `teg_mode` included (opens 6/17;
  `opens_interchange.md` item 16; Final-state item 5).
- The hier flow is single-bbox end to end: `BustermGen::derive` writes empty
  `rects` (`src/busterm.cpp:172`), and every BDB→Floorplan projection calls
  `add_block(bbox)` — `add_block_rects` is called from exactly one place in
  `src/`: the CLI setup command.  ~~A resumed or hier session therefore loses
  per-rect geometry unless the setup script re-declares it.~~  The resume
  half was MEASURED FALSE 2026-08-23 — read it as superseded by open 6
  (the recorded setup replays `add_block … rect … teg_mode` verbatim and the
  restored candidates keep busterm rects+teg; `test_teg_resume.py`).

### 2.2 TEG-over bridges — lifecycle table

> **2026-08-22, open 1(a) landed:** generation no longer emits bridges — the
> TEG-over connection metal is ORDINARY segments (per-rect gap stubs joined
> through the trunk; a perpendicular connector leg per un-spanned rect of a
> rectilinear block), so every downstream stage handles it as it handles any
> stub.  The table below therefore describes the **legacy-load path only**: a
> candidate restored from a pre-change checkpoint still carries
> `bridge_segments`, priced/hashed/transformed as recorded, placed by nothing,
> and reported by TEG_OPEN.

| Stage | Status |
|---|---|
| Generation | **no longer emits bridges** (open 1(a), 2026-08-22): the trunk generator's OVER branches emit real per-rect gap stubs / rectilinear connector legs via `emit_tap_segment` (`src/topology.cpp`, `add_trunk`); MST/BITRUNK/2-pin still emit no TEG connection metal |
| Nominal WL + segment-count tie-break | restored bridges still counted (`src/topology.cpp` `wirelength()`/`annotate_and_sort`) so a restored pool keeps its recorded order |
| `topo_uid` fingerprint, hier offset/rotate | hashed + transformed (`src/topology_analysis.cpp:116-119`, `src/topology.cpp:70-75`) — a restored candidate keeps its recorded identity |
| BDB persistence + `load_pipeline` restore | **kept, v11** (`topology_bridge_segment`): generation persists zero rows; pre-change checkpoints restore theirs (`test_bdb_resume_gaps.py`) |
| ConnTopology analysis | ignores restored bridges (they join nothing — which TEG_OPEN reports) |
| Congestion planner / NUTS / DetailedNUTS | consume the NEW connection metal as ordinary segments; restored bridges **never placed** |
| check_topo / check_nuts / check_dnuts | new metal audited like any segment; a restored unrealized bridge → **TEG_OPEN** ("declared bridge is unrealized") |
| `report_wirelength` / QoR metrics | new metal counted (it is placed segments); restored bridges not (never placed) |
| Viz | new metal drawn like any segment; restored bridges: web JSON serializes them (`src/web/serialize.py:225-239`) ~~but no renderer draws them~~ — the matplotlib explorer and main viewer draw them as a dashed "unrealized bridge (legacy checkpoint)" overlay since open 10 (2026-08-23); the WEB client still draws none (Final-state item 6) |
| GDS / DEF / `emit_guides` export | new metal exported (placed rows); restored bridges absent |
| `edit_topology` ops | no bridge create/delete/inspect (nothing creates them any more; `erase_segment` ignores restored ones) |

`thru` mode is silent **by design** and consistently so: only the nearest
rect is connected, `detect_feedthru_relay` skips multi-rect blocks
(`src/verify.cpp:172` — "TEG: internal eq. OK"), and block coverage is
satisfied by any one rect.  That is the correct reading of "internally
connected", but there is no census/report of which rects were left to the
block's internal routing — a debugging blind spot when the assumption is
wrong.

## 3. Test and spec coverage

- **Gherkin:** `busterm_over_the_block.feature` is `@landed` — 9 scenarios,
  8 bound and green (thru/over, gap vs inside, L-shape, pure TEG, adjacency
  suppression, per-block override), ~~1 **xfail**: thru-before-over adjusted-WL
  ranking (`test_busterm_over_the_block.py:81` — "`Topology.adjusted_wl` and
  per-topology `teg_mode` attribute not yet in the C++ API")~~ *(the xfail is
  GONE — open 4 retired the `adjusted_wl` concept and rewrote the scenario as
  the `@landed` same-locus-twin WL assertion; 9/9 passing, zero xfail)*.
  `multi_rect_block.feature` was tagged `@future` with an "all scenarios
  xfail" header — **stale**: its 7 scenarios pass today
  (`docs/internal/test/suite_analysis.md:312` agrees), the xfail in
  `test_multi_rect_block.py:42` was conditional and never fired, and the dead
  fallback `conftest.py:315` ("multi-rect add_block not yet in C++ API")
  survived.  RESOLVED on this branch 2026-08-22: retagged `@landed`, both
  xfail escape hatches removed (a missing candidate now fails loudly).
  Nothing guards that a `@future` file actually xfails, which is
  how the label went stale.  ~~Neither feature appears in
  `feature_coverage_plan.md`'s arc→feature map~~ *(the map carries a TEG /
  multi-rect row since the 2026-08-25 final pass)*; the plan's Phase-2 item
  (hier `teg_mode over` coverage in `datapath_trunk` / `multi_level_trunk`)
  is still unbuilt — the hier-side coverage that exists is
  `test_teg_hier_demos.py` on the #842 vehicles, not a feature file.
- **Units:** multi-rect is broadly exercised (~15 files) — annotation taps
  (`test_offset_topology.py:131`), NUTS-stage face audit
  (`test_check_design_hbundle.py:401`, the ONLY NUTS-stage multi-rect test),
  coverage filter, conn reporting, web serialize, BDB rects round-trip +
  migration, coord validation, crash guard, pass-through census.  TEG is
  pinned at generation + WL + dedup-exclusion + persistence only
  (`test_busterm_over_the_block.py`, `test_topo_keepout_mst.py:575`,
  `test_topo_pool_cleanup.py:233`, `test_bdb_resume_gaps.py:58`,
  `test_seg_busterm_persist.py:137`).  **Zero** planner/NUTS/DNUTS bridge
  tests exist — correctly, since the stages have nothing to test.  ~~One test
  is vacuous: `test_topo_structural_tiebreak.py:60` claims to pin the
  bridge-count half of the sort key on an all-single-rect fixture (always 0).~~
  *(Superseded by open 15 — the assertion is REAL now: a restored-legacy
  bridged twin makes the tie-break term vary.)*  Post-arc the placed stages
  ARE tested — `test_teg_open.py` drives the full pipeline at both placed
  stages, `test_teg_resume.py` pins the resume seam, `test_teg_thru_census.py`
  the BUDA-1907 report, `test_teg_hier_demos.py` the demo vehicles.
  All cited suites pass on this commit (19 + 13 passed, 1 xfailed, measured
  2026-08-22; the xfail is gone since open 4).
- **Flows / QoR:** 7 vehicles (`flow/teg1`, `poly1`, `lShape1`,
  `scenario5_lshaped` (duplicate, deleted 2026-08-22), `tShape1`, `cShape1`,
  `demo/talk2`).  ~~All
  TEG-over vehicles stop at `run_nuts`; the only multi-rect flow reaching
  DetailedNUTS is `demo/talk2.buda` (thru, no bridge) and **no test runs
  it**.  **None of the 7 is in the QoR corpus** (49 flows) — no regression
  gate covers any multi-rect or TEG design, which is why §1.1 can be true
  with a green board.~~  *(Superseded by open 2: `flow/teg_over_audit.buda`
  runs the full pipeline in the QoR corpus and `demo/talk2.buda` is wired
  into `test_flow_scripts.py`; the #842 demo vehicles
  `demo/teg_hier_hybrid.buda` / `demo/teg_two_spellings.buda` are pinned by
  `test_teg_hier_demos.py`.)*
- **Docs:** user-facing `docs/script_reference/setup.md:145-165` (TEG
  section) and `topologies.md:66-78` are good, except ~~`topologies.md:76`
  documents thru-before-over ranking **as fact while it is the one xfail**~~
  *(superseded by open 4 — the ranking follows from real priced wirelength
  and the page says so)*.
  `docs/internal/teg.md` (origin transcript) ~~has a stale viz pointer
  (`_rects_disconnected` — now dead code in `viz_common.py:143`, no callers)
  and ends mid-task~~ *(carries a dated status note since open 12)*.
  ~~`wishlist-bdb.md:271` ("bridge_segments remains
  un-persisted") is superseded by v11 but sits in a kept-for-reference
  section.~~ *(struck with a pointer, open 12)*.  ~~CLAUDE.md's TEG section is
  accurate about generation and silent about the downstream absence.~~
  *(CLAUDE.md now documents the post-emission mechanism, TEG_OPEN and
  `set_teg_mode`; both script_reference pages carry the post-#841 Direct /
  one-sided coverage — final docs pass, 2026-08-25.)*

## 4. Opens, ranked

### Critical

1. **TEG-over bridges are never routed, and their absence is invisible**
   (§1.1).  Two halves, both needed:
   (a) *emission* — ~~carry `bridge_segments` through planner/NUTS/DNUTS as
   real segments~~ **LANDED 2026-08-22**, and NOT by carrying the side-map:
   generation now emits the TEG-over connection metal as **ordinary
   topology segments** through the same `emit_tap_segment` machinery every
   stub uses, so planner/NUTS/DNUTS/audits/`report_wl`/persistence/viz all
   consume it with zero downstream change.  The §1.3 geometry defect is
   fixed in the same stroke — the union-face bridge is not emitted at all:
   the *rectilinear* branch emits one perpendicular **connector leg** per
   un-spanned rect, from the trunk to that rect's nearest perp face at the
   rect's along-centre (tap on the rect face + T-junction on the spine —
   §1.1's `TRUNK_V@x250` now carries an H leg (100,200)–(250,200) instead
   of the floating `(400,0)-(400,400)` bridge); the *disjoint-gap* branch
   emits one stub per rect (near side from its perp-hi face, far side to
   its perp-lo face — the 2-rect case reproduces the old near/far pair
   exactly), the rects joined THROUGH the trunk.  The spine pre-extension
   mirrors the new emission and no longer skips `stub_suppressed` OVER
   blocks (their gap stubs are emitted regardless — the C4-01 pool-loss
   fix, open 11).  Measured on the §1.1 vehicle: pinned `TRUNK_V@x250`
   routes 3 TrackSegments / 12 bit-wires / 0 unplaced, `check_design`
   clean of TEG_OPEN at nuts AND dnuts, abstract WL 500 with the leg's
   100+50 metal counted (`test_teg_open.py::
   test_bridge_reliant_shape_now_routes_real_leg_and_audits_clean` — the
   §1.1 repro flipped from firing to clean).  `Topology::bridge_segments`
   is now **legacy-load only**: empty at generation, the v11 table /
   restore / WL+tie-break pricing / `topo_uid` hash / hier transforms all
   stay so a pre-change checkpoint restores its recorded content — and its
   still-unrealized bridge is reported by TEG_OPEN ("declared bridge is
   unrealized"), never routed silently.  Non-OVER designs are byte-identical
   (every new path is gated on `teg_mode == OVER` + multi-rect; corpus
   0 better / 0 worse, WL +0).  Residuals, all pre-existing and LOUD via
   TEG_OPEN rather than silent:
   ~~(i) a trunk Direct inside ONE disjoint rect emits no metal for the
   other rects (the documented "OVER activates only for gap/partial-span
   trunks" scope)~~ and ~~(ii) a one-sided approach (all rects on the same
   side of the trunk) still falls back to the single best-rect stub~~
   **BOTH RESOLVED 2026-08-25**, in the same `emit_tap_segment` machinery
   and the same FACE→trunk orientation as every 1(a) stub (the #823 hazard
   — a trunk-end busterm seed costs NUTS its face anchor — is not
   reintroduced).  For (i) the Direct branch now covers DISJOINT blocks
   too: each rect outside the landing CONTIGUITY component gets a stub
   from its locus-facing perp face to the trunk at its along-centre
   (min-stub floor enforced exactly as the rectilinear legs enforce it),
   and the component
   (`teg_landing_component`: locus-containing rects expanded transitively
   over `rects_touch`) is what keeps the ADJACENT case suppressed — a rect
   touching the landing rect on a positive-length shared edge is
   physically continuous with it, the feature's adjacency rule, now
   transitive by construction (a 3-rect touching chain emits nothing while
   a separated 4th rect of the same block still gets its stub —
   `test_adjacent_chain_is_suppressed_and_separated_rect_still_stubbed`).
   The suppression's touch graph reads the PHYSICAL rects
   (`Busterm::orig_rects`, the #835 spelling) while band seeds and tap
   coordinates stay on the INSET rects — touching = physical geometry,
   tappable = inset geometry: a corner margin marks faces unusable for
   taps, it does not physically separate the rects, and the first cut read
   the inset rects for BOTH, so a margined abutting pair grew a connector
   between already-contiguous metal (Codex P2 on #841, measured and fixed
   2026-08-25; `rects_touch` is pure abutment — a strict-overlap pair
   margin-classified as a gap keeps its join stub, #835's documented
   honest-consequence semantic, and at margin 0 no strict-overlap pair can
   reach the component so margin-0 output is untouched; pinned by
   `test_margined_adjacent_chain_keeps_physical_suppression_inset_taps`,
   which fails on the pre-fix build).
   For (ii) the gap branch simply dropped its rects-on-BOTH-sides gate, so
   a one-sided approach emits the same per-rect face→trunk stubs joined
   through the trunk.  Measured: the (i) repro — trunk inside the lower
   disjoint rect — now places every segment and audits clean of TEG_OPEN
   at nuts AND dnuts where it fired at both
   (`test_trunk_inside_one_disjoint_rect_now_stubs_the_other_and_audits_clean`
   — the old firing pin, flipped); the (ii) repro likewise
   (`test_one_sided_trunk_now_stubs_every_rect_and_audits_clean`, one stub
   per rect from faces y=100 and y=300 to the trunk); the feature scenario
   "trunk inside one rect" is rewritten to the stub expectation (its
   "OVER activates only in the GAP" comment was residual (i)'s spec).
   Rectilinear Direct blocks and every non-OVER design are byte-identical
   (the rectilinear branch is untouched; both new paths gate on
   `teg_mode == OVER` + ≥2 rects), but OVER pools DO re-sort — every
   one-sided trunk candidate gains its real per-rect stub WL — so
   `flow/teg_over_audit.buda`'s pool went 18 → 17 candidates with
   `TRUNK_V@x250` moving index 10 → 6, and the flow now pins by TYPE
   (`select_topology 1 TRUNK_V@x250`, the #838 selector), retiring the
   hard index; its routed endpoint is unchanged (3 segs / 12 bit-wires /
   both audits Success, `test_teg_over_audit_flow_routes_clean`).  QoR
   corpus (`--vs main` @ 0e24edb8): 0 better / 0 worse / 49 unchanged of
   50, abstract AND detailed WL +0.00% (`ariane133_heal` NOT COMPARABLE —
   its fetched inputs are absent in the measuring environment, the
   harness's documented shape).
   One Direct-branch corner is deliberate and stays LOUD: a trunk Direct
   inside one of two ADJACENT rects emits no connection metal (contiguous
   shape, the feature's rule) while the placed TEG_OPEN contact predicate
   is per-rect, so such a route — if selected — still reports TEG_OPEN
   (pre-existing either way: the shape emitted nothing before too).  The
   OTHER corner — a DISJOINT sibling sharing the trunk's perp band (rects
   side by side along the spine, trunk inside one) — was documented here
   with its measured failed attempts (a perpendicular stub has no gap to
   bridge, a bare spine extension through the sibling is RETRACTED by NUTS
   span adjustment — a spine end with no junction has nothing holding it —
   and an over-the-cell anchoring stub trips the #514 tap-overhang ANTENNA
   rule) until the spine-end anchoring those attempts pointed at was built:
   **RESOLVED 2026-08-25** — the spine lands on the sibling's facing face
   as a real BUSTERM landing NUTS holds, with the #823 per-block BUSTERM
   dedup made per-face for multi-rect blocks so a spine landing on the
   same block at both ends keeps both anchors.  Full record: Final-state
   item 3; vehicle `flow/teg_same_band.buda`; pinned by
   `test_same_band_disjoint_sibling_routes_clean_via_spine_anchoring` and
   the same-band family beside it (the adjacent-pair loud control
   included).
   What REMAINS is (iii), narrowed and verified LOUD:
   **MST candidates (and the TRUNK+MST hybrids' multi-rect branch blocks,
   which mostly drop at generation as non-simple) still emit no TEG
   connection metal** — an MST edge lands on the closest rect pair only,
   so an OVER block's other rects go unreached.  A fix here is a genuine
   redesign, not a branch edit: there is no shared trunk locus, so
   per-rect attachment would have to target an arbitrary tree segment and
   compose with `complete_relay_junctions`' relay wiring, the shared-leg
   trims (`set_trim_mst_legs`), and ripup's per-edge L/Z flips (a flip
   re-routes the very edge a connector would hang from).  Left on the
   legacy path and pinned LOUD end-to-end:
   `test_mst_on_over_block_fires_teg_open_end_to_end` (a selected MST_HV
   on an OVER receiver fires TEG_OPEN at both placed stages naming the
   unreached rect — 1 bundle-level at nuts, 4 per-bit at dnuts) —
   `test_teg_resume.py`'s dirty vehicle now rides the same shape, so the
   resume-armed audit stays pinned too, and BITRUNK's twin pin is open
   8's `test_bitrunk_on_over_block_fires_teg_open_end_to_end`.
   The 1(a) stubs/legs follow the FACE→trunk emission orientation the
   gap-stub retraction fix (#823, see (b)) established — a trunk-end
   busterm seed costs NUTS its face anchor and the stub retracts;
   (b) *audit* — ~~`check_nuts`/`check_dnuts` must fail a selected candidate
   whose bridge has no placed metal~~ **LANDED 2026-08-22** as **`TEG_OPEN`**
   (`detect_teg_open`, `src/verify.cpp`): at both placed stages, every rect
   of an OVER block must be touched by the bundle's placed metal — a
   per-rect, inclusive contact predicate rather than a bridge-presence
   check, so it subsumes "bridge unrealized" AND stays failing under a
   §1.3-shaped emission (a bridge on the union face away from the rect does
   not discharge it), and passes the moment real metal reaches every rect
   however 1(a) chooses to put it there.  Two review refinements (Codex P1s
   on #821, both verified and landed with it): the dnuts audit runs PER BIT
   — each bit is its own net, so bit 0 touching rect A and bit 1 touching
   rect B connects neither, and NDR shield wires (a rail net) are excluded
   from contact — and contact alone is not the verdict: all rects must sit
   in ONE connected component of the group's placed metal (SEG junctions +
   same-rect contact + other blocks' taps), since two islands each touching
   a different rect pass the touch test while `island_roots` unions
   same-BLOCK taps and so cannot see the split.  A bit touching no rect at
   all is exempt (tapered away), with an all-bits-miss fallback so the
   original missing-bridge shape still reports.  THRU stays exempt by
   design;
   `check_topo` deliberately does NOT carry the kind (it feeds generation
   gates, dogleg trials and healer metrics — a reporting audit must not
   shrink candidate pools), which also means `detect_disconnected`'s
   same-block union is left alone: the topo-stage structural graph is
   consumed by behavior, and the placed-stage TEG_OPEN already reports what
   the union hides.  The §1.1 repro now audits
   `Bundle 1: OVER block 'L': rect#0 (0,0)-(100,400) touched by no placed
   metal … declared bridge is unrealized (dnuts)` where it reported Success.
   Building its tests measured a bonus finding: the disjoint-gap stub pair
   did not survive placement in EITHER orientation (NUTS retracted the far
   stub to the trunk — span [150,158] against a face at 300, the
   wishlist-topo "gap-stub pair at rect CENTRES" family).  RESOLVED
   (2026-08-22, branch `claude/teg-gap-stub-fix`): the far gap stub was
   emitted trunk → face while `emit_tap_segment` seeds the busterm on the
   START endpoint, so its tap annotation sat on the TRUNK end —
   `derive_conn_segs`' per-block BUSTERM dedup let it shadow the real
   face-end annotation and NUTS, with no face anchor, retracted the stub.
   Emitting it face → trunk (like every other stub) places both gap stubs to
   their faces: the gap vehicle now audits CLEAN at nuts and dnuts in both
   orientations (connectivity holds through the trunk, per §1.3's last
   paragraph), `test_teg_open.py`'s gap test carries the flipped clean
   expectation its comment promised, and
   `test_teg_gap_stub_annotation.py` pins the root cause directly.  Still
   open here: open 2's corpus vehicle to pin the kind end-to-end in QoR
   (1(a) landed 2026-08-22 — see (a)).

### High

2. ~~**No end-to-end or QoR guard exists for either feature.**  Add at least
   one TEG-over flow that runs `run_detailed_nuts` + `check_design` to the
   corpus (and wire `demo/talk2.buda` into `test_flow_scripts.py`).  Until
   open 1(b) lands this flow will pass vacuously; land them together so the
   flow pins the new violation kind.~~  **LANDED 2026-08-22** (with 1(b)
   already in): `flow/teg_over_audit.buda` — the §1.1 repro as a full
   pipeline flow (pinned bridge-reliant `TRUNK_V@x250`, `select_topology 1
   13`; a hard index, so the pin-fragility guard is
   `test_flow_scripts.py::test_teg_over_audit_flow_pins_teg_open`, which
   asserts the planner line names that candidate AND that `check_design`
   reports TEG_OPEN at both placed stages) — EXPECTED DIRTY, corpus row
   `(0, 0, 1)` at abstract WL 166 / detailed WL 672, added to
   `qor_corpus.py`'s CORPUS so the audit verdict is QoR-guarded; a 1(a)
   emission flips it to 0/0/0 and the diff is the desired loud signal.
   *(That flip happened the same day 1(a) merged into this branch: the
   emission re-sorted the pool — the leg's real WL replaced the priced
   bridge — so the pin moved to `select_topology 1 10` (still
   `TRUNK_V@x250`, now trunk + stub + leg), the guard test became
   `test_teg_over_audit_flow_routes_clean` asserting 3 segs / 12 bit-wires
   / both audits Success / no TEG_OPEN, and the committed qor_table row
   `(0,0,1)` is stale until the nightly refresh re-measures it — the
   designed mechanism, blessed in PR #824's body.)*
   `demo/talk2.buda` (the deepest multi-rect flow, `thru`, previously run
   by no test) is wired into `test_flow_scripts.py`
   (`test_talk2_multirect_thru_full_pipeline`: sidecar pin honored, 3
   segs / 24 bit-wires / clean at all four audits, and no TEG_OPEN — the
   thru-exemption control beside the OVER vehicle).
3. ~~**Planner split-brain on multi-rect**: `low_seg_obstructed` judges the
   union bbox while cut capacity is per-rect
   (`src/congestion_planner.cpp:738-784` vs `:363-383`) — a LOW segment in a
   routable notch is escalated to TOP.  A QoR distortion on exactly the
   designs the feature exists for.~~  **RESOLVED 2026-08-23**: the predicate
   now judges the SAME per-rect geometry capacity is carved from — a per-rect
   twin of the block cache (`leaf_rects_cache_`, built beside `blocks_cache_`
   at cut-rebuild) feeds every sub-question (endpoint pin-access containment,
   wholly-inside-one-cell, mid-span crossing), so the notch between a
   multi-rect block's rects is routable to BOTH halves of the planner.  A
   single-rect block contributes its one rect in the same order, so
   single-rect designs judge byte-identically (fast tier green; spot-run
   comprehensive_demo / rnr/mix / rv/soc pre-vs-post: identical modulo
   timing lines).  Measured vehicle `flow/teg_notch_low.buda` (an A→B hop
   inside the L-block's notch, wholly inside its union bbox, TOP span_min
   making LOW the honest choice): before, both endpoints read "inside the
   cell" per the union and the hop escalated to M5; after, it routes on M3
   with clean nuts+dnuts audits.  Unit + e2e tests in
   `test_planner_notch_low.py` (the predicate is now public and bound to
   Python for exactly this).  The checked-in multi-rect flows did NOT move —
   lShape1/tShape1/cShape1/teg_over_audit declare only TOP layers (the
   predicate short-circuits), and demo/talk2's routes never put a LOW
   segment where the union and the rects disagree (measured: logs identical
   modulo timing).
4. **Candidate ranking prices bridges the router never builds** — ~~nominal
   WL includes bridge length while the realized route omits it~~ the pricing
   half is DISCHARGED by open 1(a) (2026-08-22): the connection metal is
   ordinary segments, so `estimated_wirelength` equals the segment sum and
   priced metal IS built metal (pinned by `test_topo_keepout_mst.py::
   test_trunk_teg_over_metal_is_ordinary_segments_and_wl_honest`; restored
   pre-change candidates keep their recorded bridge pricing).  ~~Still open:
   the documented thru-before-over *adjusted*-WL ranking remains the
   standing xfail~~ **SETTLED 2026-08-23, and by retiring the concept rather
   than building it**: post-emission a separate `adjusted_wl` is not a
   meaningful quantity — the OVER connection metal is ordinary segments
   priced in `estimated_wirelength`, so thru-vs-over ranking is plain WL
   ranking of genuinely different metal, and `teg_mode` is a property of
   the BLOCK, so one pool structurally cannot hold both modes (the phantom
   per-topology `teg_mode` attribute the xfail waited on has no
   post-emission referent).  Measured on the feature's own gap geometry
   (thru pool vs over pool, same trunk locus `TRUNK_H@y200`): thru wl=200 /
   2 segs at rank 12 of 22, over wl=360 / 3 segs (both rects stubbed) at
   rank 18 of 22 — and on the §1.1 L-shape (`TRUNK_V@x250`): thru wl=300 /
   2 segs at rank 5, over wl=500 / 3 segs (the leg is real priced metal) at
   rank 10.  The xfail scenario is REWRITTEN as `@landed` asserting the
   measured SAME-LOCUS-TWIN property (two pools on one geometry; over twin
   strictly higher WL, its extra metal strictly more real segments —
   `test_busterm_over_the_block.py`, 9/9 passing, zero xfail).  The ranks
   above are recorded as observations, deliberately NOT asserted: the two
   pools are different candidate populations (every OVER-affected member
   shifts, not just the twin), so a cross-pool ordinal is confounded — the
   WL-sorted-pool consequence "thru before over, all else equal" follows
   from the WL comparison, which is what the test pins (Codex P2 on
   PR #832, verified).  Also,
   `docs/script_reference/topologies.md`'s TRUNK rows no longer claim a
   `bridge_segments` carry (its TEG paragraph already described the
   post-emission mechanism — real wirelength, no "adjusted" figure).  §3's
   "1 xfail" line and suite_analysis Group 4's mention of this file are
   superseded by this entry.  Item 4 is fully closed.

### Medium

5. ~~**`thru` mode has no census.**  An externally-split TEG block is silent
   by design; add an INFO-level report (message-id catalogue) naming the
   rects left to internal routing, so a wrong `thru` assumption is
   discoverable without reading the topology dump.~~  RESOLVED 2026-08-23:
   **BUDA-1907** (INFO, catalogued) — at `check_design`'s placed stages,
   each audited bundle's thru multi-rect blocks report which rects are
   touched by no placed metal of the bundle, i.e. left to the block's
   internal routing.  Computed INSIDE `detect_teg_open` (`src/verify.cpp`)
   by the SAME `teg_touches` contact scan the OVER verdict uses — one
   predicate, a report-only sink (`ConnResult::thru_census`) instead of a
   violation — unioned over every metal group and reported only when the
   bundle reaches the block at all (a block with no contact anywhere is a
   coverage problem, not a thru-assumption one).  Surfaced by
   `_check_design` with a BUDA-1913/1914-style verdict-keyed memo (bundle,
   block, untouched-rect set — stage in the message, not the key), so the
   dnuts repeat of an unchanged nuts verdict stays quiet while a placement
   that moved a stub off a rect reports again.  NEVER a violation: the
   audit verdict, `by_kind`, `--strict-check` and the QoR triple are all
   unchanged, and INFO is counted by neither diag counter, so existing flow
   logs' counts hold.  Fires on `demo/talk2.buda` (blk2 rect#1 / blk3
   rect#0 — real thru reliance, previously invisible).  Tests:
   `test_teg_thru_census.py` (fires naming the rects; memoized; silent when
   every rect is reached; OVER gets TEG_OPEN, never the census; single-rect
   silent; id in `dump_messages`).
6. ~~**Multi-rect never reaches the hier flow**~~ **RESOLVED-AS-BOUNDED
   2026-08-23, with the suspected resume loss MEASURED AND REFUTED.**  The
   hier half is a boundary by construction, not a defect: a BDB *component*
   holds ONE bbox, so multi-rect cannot be DECLARED as a hier-design input —
   `derive_busterms` writes empty rects (`src/busterm.cpp:172`), every
   BDB→Floorplan projection is `add_block(bbox)`, and `tools/buda2bdb.py`
   collapses to the union bbox; its warning now also NAMES the dropped
   trailing modifiers, `teg_mode` included (it said only "collapsed" while
   silently eating `teg_mode over` — the one modifier whose loss turns an
   electrically-open block into a clean-auditing one; pinned by
   `test_buda2bdb.py::test_multirect_collapse_warning_names_dropped_modifiers`).
   The boundary is documented where users hit it: `docs/BDB_REFERENCE.md`
   (busterm `rects`/`teg_mode` are a routing-time persist artifact — the
   `tb:` rows — not a hier-design input) and `docs/BUDA2BDB.md`.
   The RESUME half of this entry (and §2.1's "a resumed or hier session
   therefore loses per-rect geometry unless the setup script re-declares
   it") was measured 2026-08-23 and is FALSE for the flat resume — nothing
   is lost, with no code change needed, because the machinery already
   re-declares: the `BUDA_RECORD` trace carries
   `add_block L rect … teg_mode over` VERBATIM (measured on
   `flow/teg_over_audit.buda` + an armed checkpoint), a flat stage-resume
   replays that setup wholesale before `load_pipeline`, so the resumed
   floorplan holds both rects + `TegMode.OVER`; restored candidates keep
   busterm rects+teg through the `topology_seg_busterm` bridge; the resumed
   tail reproduces the routed endpoint exactly (same 12 bit-wires /
   detailed WL 1996 / both audits Success); and — the seam that would have
   been silent — `detect_teg_open` reads rects+teg off the session
   FLOORPLAN, and a resumed dirty checkpoint (trunk-Direct-inside-one-rect)
   still FIRES `TEG_OPEN` identically.  Both halves pinned by
   `test_teg_resume.py` (clean endpoint equality + audit-stays-armed).
   Read §2.1's resume sentence as superseded by this entry.  What remains
   genuinely absent is unchanged and lives in items 8/17: no import path
   and no hier declaration produces a multi-rect block.
7. ~~**`set_feedthru` on a multi-rect block is silently ignored**
   (`src/topology.cpp:3056`).  Warn at declaration — the user stated an
   intent the engine drops.~~  RESOLVED 2026-08-23: **BUDA-1908** (WARNING,
   catalogued) at DECLARATION time in the CLI handler
   (`src/buda_cmds/setup_cmds.py`): a `set_feedthru … on` naming a
   multi-rect block — directly or via `*` — warns ONCE per command,
   listing every affected block, while the declaration still takes effect
   for the single-rect blocks it names.  `off` is deliberately not warned:
   a multi-rect block never relays regardless of the flag, so disabling it
   changes nothing AND the outcome matches the intent.  The engine gate
   itself is unchanged (still skips multi-rect).  BOTH declaration orders
   are covered (Codex P2 on #834 caught the reverse): a wildcard/per-layer
   enable declared BEFORE the block exists warns at the multi-rect
   `add_block` instead — the add_block site reads the same most-specific
   `get_feedthru` resolution the engine uses — and the two sites share one
   session-level per-block memo (`_warn_feedthru_multirect`), so the same
   block never warns twice whichever order the flow declares in.  Tests:
   `test_set_feedthru_multirect_warning.py`.
8. ~~**BITRUNK is bbox-only** (`src/topology.cpp:4401`, `:4467-4473`): the
   datapath trees neither select rects nor bridge.  Acceptable as a scoping
   decision, but undocumented.~~  RESOLVED-AS-DOCUMENTED 2026-08-23: the
   scoping stands and is now stated where users read
   (`docs/script_reference/topologies.md` — the BITRUNK table + the
   `multi_trunk` option row — and CLAUDE.md's `generate_topologies` row).
   The claim that makes it acceptable was VERIFIED by experiment rather
   than assumed: a legacy `BITRUNK_H` on an OVER multi-rect design routed
   end to end (4 endpoint blocks, the OVER receiver's second rect beyond
   the rungs' along-span, its union-center stub landing in the gap) fires
   **TEG_OPEN** at both placed stages naming the unreached rect — loud,
   not silent — pinned by `test_teg_open.py::
   test_bitrunk_on_over_block_fires_teg_open_end_to_end`.  On a `thru`
   block the open-5 BUDA-1907 census covers the same blind spot as a
   report.
9. ~~**`corner_margin` is inert for individual rects** (`src/topology.cpp:
   3296-3300`) — silently, on the faces multi-rect routing actually lands
   on.~~  **RESOLVED 2026-08-23**: each rect is now inset exactly as the
   union bbox is, at Busterm construction (`shrink_rects`, topology.h —
   `Rect::shrink`'s per-axis guard applied PER RECT, so a rect too thin for
   the margin keeps that axis at full extent while its siblings still
   inset), at all four construction sites (generate_2pin / generate_npin /
   annotate_topology / topo_edit's edit_add_stub) — so generation, the
   per-bundle Hanan grid, `best_rect` faces, stubs and taps all see the
   inset rects the way a single-rect block sees its inset bbox.
   `derive_slide_ranges`' tap→rect attribution (an EQUALITY match of
   `face_coord` against rect faces) accepts the inset spelling of each face
   too, so the per-rect slide narrowing keeps working — and still matches
   the physical spelling, so restored pre-change candidates narrow as
   before.  Zero margin is the identity: margin-free designs are
   byte-identical (fast tier green; flow/lShape1 and flow/teg_over_audit
   measured unchanged).  The task-brief premise that no checked-in flow
   declares a margin on a multi-rect block was FALSE — `flow/teg1.buda`,
   `flow/poly1.buda` and `demo/talk2.buda` all declare a global
   `corner_margin dx 20 dy 10` over rect-declared blocks, previously inert
   there — so those three MOVE, as the margin doing its declared job:
   teg1/poly1 trunk loci snap to inset rect edges (`TRUNK_H@y100`→`y90`,
   stub intervals shift by the margins; same warning counts, clean as
   before), and talk2's pinned `TRUNK_H@y650` re-prices 1100→1160 WL (taps
   on inset faces), so `demo/talk2.json` was re-recorded to the new
   WL/index (the sidecar's content match had fallen back to a wrong
   index-hint candidate) — its exact-count test passes unchanged (3 segs /
   24 bit-wires / 4× clean).  One documented semantic note: margins now
   participate in the OVER gap/adjacency classification
   (`rects_are_rectilinear` and the gap tests read the inset rects), so a
   margin can turn a thin overlap into a gap — the emitted join metal is
   the honest consequence of declaring those faces unusable.  Tests:
   `test_multirect_corner_margin.py` (inset taps + carried rects, no-margin
   physical faces unchanged, the per-rect per-axis guard).  Review follow-up
   (PR #835 P2, confirmed by repro): `annotate_endpoints`' multi-rect branch
   examined ONLY `Busterm::rects` (now inset) — unlike its single-rect
   branch, which checks BOTH `orig_bbox` and the inset `bbox` — so a
   hand-built (TopoEdit/USER) or restored endpoint landing on the PHYSICAL
   face of a margined multi-rect block lost its tap and the block read
   open.  Fixed by carrying the physical spelling on the Busterm
   (`orig_rects`, the multi-rect twin of `orig_bbox`, populated only when a
   nonzero margin makes it differ; hier offset/transform move it in step)
   and having `rects_of` accept both spellings, exactly mirroring the
   single-rect dual check; zero margin keeps `orig_rects` empty, so
   margin-free annotation is unchanged.  Pinned by the three
   `test_annotate_*` tests in the same file.
10. ~~**Bridges are drawn by nobody** — web JSON serializes them, no client
    renders them, the matplotlib viewer ignores them, and the explorer has
    no bridge affordance; `_rects_disconnected` is dead code while the
    dashed union box draws unconditionally.  A user cannot *see* the wire
    that §1.1 shows is also never built.~~  Mostly EVAPORATED by the 1(a)
    emission (OVER connection metal is ordinary segments, drawn everywhere
    like any stub), and the remnant RESOLVED 2026-08-23: a **legacy-load**
    bridge (`Topology::bridge_segments`, non-empty only on a candidate
    restored from a pre-emission checkpoint — the case whose TEG_OPEN
    message says "declared bridge is unrealized" about a wire nobody drew)
    is now rendered by the matplotlib explorer AND the main viewer (abstract
    and NUTS views, at its recorded nominal coordinates since it is unplaced
    by definition) as a dashed, off-palette, labeled "unrealized bridge
    (legacy checkpoint)" overlay — ONE shared helper,
    `viz_common.draw_legacy_bridges`, main-viewer lines registered so
    click-to-highlight covers them; the label annotations, which the
    reroute cleanup's registry sweep cannot see, are detached by
    `_clear_legacy_bridges` before every redraw (the Codex #484
    endpoint-label idiom; Codex P2 on #834 caught the accumulation); a
    bridge-less topology draws zero extra
    artists, so every live design's viz is unchanged (this supersedes the
    §2.2 table's "restored bridges … no renderer draws them" Viz row for
    the two matplotlib renderers; the web client still draws none).  Tests:
    `test_viz_legacy_bridge.py` (headless Agg: injected `bridge_segments`
    draws + registers, bridge-less draws none).  What remains AS the
    owner's deliberate state, text now precise post-emission:
    `_rects_disconnected` (`src/viz_common.py`) is kept-as-reference dead
    code by its owner's own comment, and the dashed union box draws
    unconditionally — neither is re-wired.  The explorer still has no
    bridge-specific affordance beyond the overlay (nothing creates bridges
    any more, so none is owed).
11. ~~**Known generation pool bug (OPEN, audit C4-01)**: stub-suppressed
    TEG-over blocks skip the spine pre-extension, so gap-stub pairs float
    off-spine and the candidate is dropped~~ RESOLVED 2026-08-22 with open
    1(a): the rewritten pre-pass mirrors the emission exactly and no longer
    skips `stub_suppressed` OVER blocks (see the updated wishlist-topo
    entry).

### Minor

12. **Stale artifacts to fix**: ~~`flow/lShape1.buda` /
    `flow/scenario5_lshaped.buda` "never triggers" comment~~ RESOLVED
    2026-08-22 (comment corrected to the measured behavior, unreferenced
    duplicate deleted); ~~`multi_rect_block.feature` `@future` tag + xfail
    header; `test_multi_rect_block.py` docstring; `conftest.py` dead
    xfail~~ RESOLVED 2026-08-22 (retagged `@landed`, and the conditional
    xfail escape hatches are GONE — a missing expected candidate now fails
    loudly, so a generator regression can no longer hide as xfail;
    `feature_coverage_plan.md`'s mode-2 list updated to match);
    ~~`docs/internal/teg.md:83` viz pointer; `wishlist-bdb.md:271`
    superseded line~~ RESOLVED 2026-08-22 (teg.md carries a dated status
    note — the function moved to `viz_common.py`, is kept-as-reference dead
    code by its owner's own comment, and the promised connectivity gating
    was unwired, so the dashed box draws unconditionally; the wishlist line
    is struck with a pointer to the v11 gap-closed note it predated).  Item
    12 is fully closed.
13. ~~**Validation gaps in `add_block ... rect`**: no overlap/degeneracy check
    on the rect list (only classified later), and a malformed rect with <4
    coords raises a bare `IndexError` (`src/buda_cmds/setup_cmds.py:67-69`)
    instead of the named-argument error the fractional-coord path gets.~~
    RESOLVED 2026-08-25: (a) a truncated rect is a hard error naming the
    missing coordinate and the rect number (`rect 1 is missing <y2>`), the
    same style as the fractional-coord path; (b) a DEGENERATE rect (zero
    width or height — its edges would be block faces and Hanan lines with
    no extent behind them) and a DUPLICATE rect (identical after the
    coordinate-order normalization `add_block_rects` applies — it adds no
    geometry and doubles that rect's stubs/taps) are hard errors at
    declaration naming the rect.  OVERLAP is deliberately NOT refused —
    the "only classified later" this item complained about is the
    classification working as designed: interior overlap between
    non-identical rects is exactly what `rects_are_rectilinear`
    (`src/topology.cpp`) reads to classify an L/C-shape block, and
    edge-adjacent rects drive the OVER adjacency suppression, so
    forbidding either would break the rectilinear branch's own input
    (checked before deciding; pinned positive in the tests).  Tests:
    `test_coord_validation.py` (truncated ×2, zero-width, zero-height,
    duplicate incl. reversed-coordinate spelling, and the two
    stay-legal controls: overlapping L-shape + adjacent rects); docs:
    `docs/script_reference/setup.md` add_block validation paragraph.
14. ~~**`teg_mode` is per-block only** — no global default command exists;
    the feature scenario titled as a global-override test asserts only the
    per-block keyword.  Either add `set_teg_mode` or retitle the scenario.~~
    RESOLVED 2026-08-25, by ADDING `set_teg_mode <thru|over>`: a
    Floorplan-level default (`default_teg_mode_` +
    `set_default_teg_mode`/`default_teg_mode`, bound to Python) that a
    multi-rect block declared WITHOUT an explicit per-block `teg_mode`
    keyword takes; the per-block keyword wins in either direction
    (most-specific-first, the `set_feedthru` convention).  Resolution is
    at DECLARATION time — PROSPECTIVE ONLY, matching add_block's other
    declaration-time resolutions (a retroactive default would silently
    re-mode already-declared blocks): the CLI passes no mode when the
    keyword is absent and `add_block_rects` reads the floorplan's
    CURRENT default, so a flow that never calls the command is
    byte-identical (the default default is THRU).  The mistitled
    scenario (`busterm_over_the_block.feature` "Global teg_mode
    overridden per block", which declared per-block keywords on BOTH
    blocks) now actually exercises the global: `set_teg_mode over` +
    a keyword-less block B (inherits over, stubs both rects) + an
    explicit `teg_mode thru` block C (override wins, one stub).  Tests:
    `test_set_teg_mode.py` (default-off identity, global applies,
    override wins both ways, prospective-only, engine-API contract,
    unknown/missing/extra-argument errors) + the rewritten scenario;
    docs: CLAUDE.md command row + `docs/script_reference/setup.md`
    `set_teg_mode` section (which states the prospective choice).
15. ~~**Vacuous tie-break test** — give `test_topo_structural_tiebreak.py` a
    bridged fixture so `len(bridge_segments)` actually varies.~~  **RESOLVED
    2026-08-25 by making the assertion REAL — and the comparator was
    investigated before choosing that over retiring it.**  Post-1(a) no
    generation path can produce a nonzero bridge count (nothing in
    `topology.cpp` populates `bridge_segments`; only the v11 restore and
    the bindings do), so C++ `annotate_and_sort`'s bridge term is
    dead-for-generation — but its PYTHON TWIN
    (`_resort_pool_preserving_selection`, `src/buda_session/edit.py`)
    carries the same `(wl, segments+bridges, type)` key and runs on every
    pool ACCRETION (`generate_more_topologies`, the knob-memo replays),
    where a RESTORED legacy candidate can sit in the pool being re-sorted;
    §2.2's table promises exactly that ordering ("restored bridges still
    counted … so a restored pool keeps its recorded order").  Retiring the
    term would therefore break the restored-pool contract and desync the
    twins — option (b) rejected.  What landed:
    `test_restored_bridge_counts_in_the_tie_break`
    (`test_topo_structural_tiebreak.py`) — a legacy bridged twin built
    through the bindings (the restore path's own door: `Topology` +
    `bridge_segments` are read-write bound), same recorded WL and segment
    count as its bridge-less twin with a type chosen to sort FIRST on the
    anchor (`AAA_LEGACY`), pushed through the real
    `generate_more_topologies` re-sort and asserted to land AFTER the twin.
    Without the bridge term the key degenerates to `(wl, len(segments),
    type)` and the type anchor would rank the legacy candidate first, so
    the assertion fails the day the term is dropped from EITHER twin — the
    key finally varies on `len(bridge_segments)`.  The `_key` helper's
    stale comment ("bridge segments are real wires") now states the
    legacy-load-only reality.  §3's "one test is vacuous" line is
    superseded by this entry.
16. ~~**`topo_edit` stubs from union bbox** (`src/topo_edit.cpp:165-185`) — a
    hand-built stub can land in the notch; low priority since `edit_status`'s
    verdict would flag most consequences, but the tap-face choice should go
    through `best_rect` like generation does.~~  **RESOLVED 2026-08-25**:
    `edit_add_stub` now picks its tap rect through generation's per-rect
    selection — `best_rect` and `bt_all_rects` are de-static'd out of
    `topology.cpp` and shared via `topology.h` (reused, not copied; a
    rect-list `best_rect` overload carries the one cost rule), and the edit
    session chooses among the rects its FIXED target span can actually
    reach with a real perpendicular stub — a restriction generation never
    needs, since its spine is pre-extended to cover every stub — then
    shortest-stub among those.  The rect view is the Busterm seed's own
    margin-inset `rects` (post-#835), so a `corner_margin`'d multi-rect
    block gets its edit stub on the INSET face exactly like a generated
    tap; a single-rect block contributes exactly ONE candidate (its
    physical bbox via `bt_all_rects`), so single-rect geometry and all
    three failure messages are byte-identical (test-pinned; fast tier
    green, 2600 passed).  Measured on the §1.1 L-shape (H trunk @y500 above
    the block): pre-fix the stub landed at the UNION overlap centre
    (200,400) — the notch, over no physical face — and post-fix at (50,400)
    on the tall arm's real top face with a clean verdict; a trunk span
    reaching only the base now taps the base at y=100 instead of floating
    at the union face y=400; and the pure-TEG gap-only-span shape (union
    overlaps the span, no rect does) REFUSES with the no-overlap message
    instead of emitting a floating gap stub.  The open's `edit_status`
    claim was MEASURED and is TRUE for the notch shape: the pre-fix verdict
    did flag it (`BUSTERM_FACEx1` on the L-shape repro) — loud, but the
    suggested geometry was still wrong and the session offered no way to
    land on a real face short of hand-computing coordinates.  The fix is in
    the C++ engine op, so every door gets it (CLI `edit_add_stub` and the
    explorer GUI's edit mode alike).  Tests: the open-16 block in
    `test_edit_commands.py` (notch → real-face fail-before/pass-after,
    unreachable-rect restriction, gap-only refusal, margin-inset face,
    single-rect byte-identity incl. failure messages).  Read §2.1's
    `topo_edit` bullet as superseded by this entry.  Review follow-up
    (PR #840 P2, verified by repro): the first cut let a rect the target
    CROSSES be discarded while another reachable rect got a stub — under
    THRU that is unnecessary external metal between internally-connected
    terminals (generation's best_rect picks the zero-cost crossed rect and
    emits no stub; the old union path refused with the pass-through
    message).  Fixed with a MODE SPLIT, each side matched to generation as
    measured: THRU gives the pass-through/touch verdict precedence over any
    other reachable rect (refusal restored), while OVER — where the crossed
    rect connects only itself — keeps emitting the stub, which is exactly
    the connector leg the 1(a) rectilinear branch generates for the §1.1
    `TRUNK_V@x250` shape ((100,200)-(250,200), measured identical) and the
    hand fix for the trunk-Direct-inside-ONE-disjoint-rect residual that
    generation leaves to TEG_OPEN.  One test per mode in
    `test_edit_commands.py` (the THRU one fails pre-fix).
17. ~~**No import path produces multi-rect** (DEF rectilinear macros, GDS) —
    a roadmap item rather than a defect; note it in
    `opens_interchange.md` so it is not rediscovered.~~  RESOLVED-AS-NOTED
    2026-08-25: recorded as `opens_interchange.md` **item 16**, with the
    claim re-verified on the importers rather than copied from here — a
    DEF/LEF component's footprint is its LEF `SIZE` bbox and a rectilinear
    macro's finer geometry becomes `OBS` KEEPOUTS (item 12's rects), GDS
    import derives one recursive footprint bbox per structure
    (`gds_io.cpp` `bbox_of`), and `add_block_rects` is called from
    exactly one place in `src/` (the CLI handler; measured by grep,
    matching §2.1) — so the TEG machinery is script-declared only, and
    the entry states why deriving rects from `OBS`/GDS shapes would be an
    item-12-shaped inference and where honest support would start.

## 5. Suggested landing order — CLOSED (2026-08-25)

The order this section proposed is the order that happened: 1(b)'s TEG_OPEN
audit landed first with open 2's corpus vehicle pinning it (PRs #821/#824 —
loud beat silent), the emission-vs-refusal decision went to **emission**
(1(a), PR #823ff), opens 3 and 4 fell out of it as predicted, and the
medium/minor items landed independently (#827/#828, #832-#835, #838-#842,
ending with the 2026-08-25 Direct/one-sided residuals and the demo
vehicles at merge 9a2528bf).  The arc ended with every §4 open struck or
resolved-as-scoped; the surviving scope is the **Remaining limitations**
list in the Final state section at the top of this document.
