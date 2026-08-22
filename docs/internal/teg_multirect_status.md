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
downstream-thin.**  Multi-rect geometry is threaded deeply through generation,
NUTS anchoring and all three verifiers; TEG-over bridges stop at generation +
persistence and are invisible to the planner, NUTS, DetailedNUTS, every
verifier, `report_wirelength`, the QoR corpus, and every exporter.  A design
that selects a bridged candidate routes **without the bridge** and audits
clean (§1.1) — which makes `teg_mode over`, today, a candidate-pricing
annotation rather than a routing feature.

## 1. Two measurements that anchor this appraisal

### 1.1 A selected bridge is never built, and no audit notices  (CRITICAL)

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
  JSON carries real rects (`src/web/serialize.py:107-124`).

**Partial / inconsistent:**

- `corner_margin` shrinks only the **union** bbox; individual rects are never
  inset (`src/topology.cpp:3296-3300`), so a per-block margin is silently
  inert for the faces multi-rect actually uses.
- Planner split-brain: cut **capacity** is carved per rect (via
  `low_layer_keepouts`, `src/congestion_planner.cpp:363-383`) but
  `low_seg_obstructed` tests the **union bbox** (`blocks_cache_` from
  `get_all_blocks()`, `src/congestion_planner.cpp:738-784`) — a LOW segment
  in a routable notch is priced as "crossing the cell" and escalated to TOP.
- Trunk+MST hybrids: a multi-rect branch makes the tree "non-simple", so it
  keeps the legacy un-completed relay shape and is usually dropped as
  `FEEDTHRU_RELAY` (`src/topology.cpp:4077-4082`, `:4680-4685`).
- `topo_edit`: `edit_add_stub`/`edit_add_trunk` seed the tap's rects+teg_mode
  (`src/topo_edit.cpp:196-203`) but compute the face and overlap from the
  union bbox (`:165-185`) — a hand-built stub can land in the notch.
- NUTS slide windows union the perp extents of all spanned rects, so a
  segment may legally seat over the notch or in a sibling rect — documented
  as deliberate (`src/nuts.cpp:296-302`), but it means "which rect did I
  attach to" is a placement outcome, not a topology property.

**Absent:**

- BITRUNK (legacy and two-level) works entirely on `orig_bbox`
  (`src/topology.cpp:4401`, `:4467-4473`) — no rect selection, no bridges.
- The 2-pin L/Z/U/I family is **bypassed** for any multi-rect endpoint
  (`src/topology.cpp:4646-4652` forces the n-pin path) — safe, but the 2-pin
  generator itself is not rect-aware if reached directly via bindings.
- `set_feedthru` on a multi-rect block is **silently skipped**
  (`src/topology.cpp:3049`, `:3056` — "MVP: single-rect only"); no warning
  anywhere, the CLI validates only block/layer existence.
- No import path produces a multi-rect block: DEF/LEF components get one
  bbox (rectilinear macros collapse), GDS import likewise; `tools/buda2bdb.py`
  collapses to the union bbox with a warning and drops `teg_mode`.
- The hier flow is single-bbox end to end: `BustermGen::derive` writes empty
  `rects` (`src/busterm.cpp:172`), and every BDB→Floorplan projection calls
  `add_block(bbox)` — `add_block_rects` is called from exactly one place in
  `src/`: the CLI setup command.  A resumed or hier session therefore loses
  per-rect geometry unless the setup script re-declares it.

### 2.2 TEG-over bridges — lifecycle table

| Stage | Status |
|---|---|
| Generation (trunk H/V: gap-stub pair + bridge; rectilinear partial-span bridge; suppression when trunk lands in a rect / rects adjacent) | **implemented** (`src/topology.cpp:3007-3138`) — but ONLY the trunk generator emits bridges; MST/BITRUNK/2-pin never do |
| Nominal WL + segment-count tie-break | counted (`src/topology.cpp:1325-1332`, `:1352-1357`) |
| `topo_uid` fingerprint, hier offset/rotate | hashed + transformed (`src/topology_analysis.cpp:116-119`, `src/topology.cpp:70-75`) |
| BDB persistence + `load_pipeline` restore | **implemented, v11** (`topology_bridge_segment`, `src/bdb.cpp:236-249`, `src/buda_session/persist.py:569-594`, `:917-968`) |
| ConnTopology analysis | ignored |
| Congestion planner (layer, congestion charge) | **absent** |
| NUTS (track position) | **absent** |
| DetailedNUTS (per-bit wires, vias) | **absent** |
| check_topo / check_nuts / check_dnuts | **absent** |
| `report_wirelength` / QoR metrics | **absent** (sums placed segments only) |
| Viz | web JSON serializes bridges (`src/web/serialize.py:225-239`) but **no renderer draws them**, matplotlib included |
| GDS / DEF / `emit_guides` export | **absent** |
| `edit_topology` ops | **absent** (no bridge create/delete/inspect; `erase_segment` ignores them) |

So the bridge's full life is: emitted, priced into the candidate sort,
persisted, restored — and then dropped on the floor at `run_planner`.
Asymmetrically, the WL pricing *demotes* bridged candidates for metal the
router will never build.

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
  suppression, per-block override), 1 **xfail**: thru-before-over adjusted-WL
  ranking (`test_busterm_over_the_block.py:81` — "`Topology.adjusted_wl` and
  per-topology `teg_mode` attribute not yet in the C++ API").
  `multi_rect_block.feature` was tagged `@future` with an "all scenarios
  xfail" header — **stale**: its 7 scenarios pass today
  (`docs/internal/test/suite_analysis.md:312` agrees), the xfail in
  `test_multi_rect_block.py:42` was conditional and never fired, and the dead
  fallback `conftest.py:315` ("multi-rect add_block not yet in C++ API")
  survived.  RESOLVED on this branch 2026-08-22: retagged `@landed`, both
  xfail escape hatches removed (a missing candidate now fails loudly).
  Nothing guards that a `@future` file actually xfails, which is
  how the label went stale.  Neither feature appears in
  `feature_coverage_plan.md`'s arc→feature map; the plan's Phase-2 item
  (hier `teg_mode over` coverage, `:101`) is unbuilt.
- **Units:** multi-rect is broadly exercised (~15 files) — annotation taps
  (`test_offset_topology.py:131`), NUTS-stage face audit
  (`test_check_design_hbundle.py:401`, the ONLY NUTS-stage multi-rect test),
  coverage filter, conn reporting, web serialize, BDB rects round-trip +
  migration, coord validation, crash guard, pass-through census.  TEG is
  pinned at generation + WL + dedup-exclusion + persistence only
  (`test_busterm_over_the_block.py`, `test_topo_keepout_mst.py:575`,
  `test_topo_pool_cleanup.py:233`, `test_bdb_resume_gaps.py:58`,
  `test_seg_busterm_persist.py:137`).  **Zero** planner/NUTS/DNUTS bridge
  tests exist — correctly, since the stages have nothing to test.  One test
  is vacuous: `test_topo_structural_tiebreak.py:60` claims to pin the
  bridge-count half of the sort key on an all-single-rect fixture (always 0).
  All cited suites pass on this commit (19 + 13 passed, 1 xfailed, measured).
- **Flows / QoR:** 7 vehicles (`flow/teg1`, `poly1`, `lShape1`,
  `scenario5_lshaped` (duplicate, deleted 2026-08-22), `tShape1`, `cShape1`,
  `demo/talk2`).  All
  TEG-over vehicles stop at `run_nuts`; the only multi-rect flow reaching
  DetailedNUTS is `demo/talk2.buda` (thru, no bridge) and **no test runs
  it**.  **None of the 7 is in the QoR corpus** (49 flows) — no regression
  gate covers any multi-rect or TEG design, which is why §1.1 can be true
  with a green board.
- **Docs:** user-facing `docs/script_reference/setup.md:145-165` (TEG
  section) and `topologies.md:66-78` are good, except `topologies.md:76`
  documents thru-before-over ranking **as fact while it is the one xfail**.
  `docs/internal/teg.md` (origin transcript) has a stale viz pointer
  (`_rects_disconnected` — now dead code in `viz_common.py:143`, no callers)
  and ends mid-task.  `wishlist-bdb.md:271` ("bridge_segments remains
  un-persisted") is superseded by v11 but sits in a kept-for-reference
  section.  CLAUDE.md's TEG section is accurate about generation and silent
  about the downstream absence.

## 4. Opens, ranked

### Critical

1. **TEG-over bridges are never routed, and their absence is invisible**
   (§1.1).  Two halves, both needed:
   (a) *emission* — carry `bridge_segments` through planner/NUTS/DNUTS as
   real segments (layer assignment, congestion charge, track position,
   per-bit wires + vias), or explicitly refuse `over` designs at
   `run_planner` until they are;
   (b) *audit* — `check_nuts`/`check_dnuts` must fail a selected candidate
   whose bridge has no placed metal, and `detect_disconnected` must stop
   assuming same-block continuity for `teg_mode over` blocks
   (`src/verify.cpp:203-209`).  (b) alone converts the silent hole into a
   loud one and is much cheaper — the right first landing.

### High

2. **No end-to-end or QoR guard exists for either feature.**  Add at least
   one TEG-over flow that runs `run_detailed_nuts` + `check_design` to the
   corpus (and wire `demo/talk2.buda` into `test_flow_scripts.py`).  Until
   open 1(b) lands this flow will pass vacuously; land them together so the
   flow pins the new violation kind.
3. **Planner split-brain on multi-rect**: `low_seg_obstructed` judges the
   union bbox while cut capacity is per-rect
   (`src/congestion_planner.cpp:738-784` vs `:363-383`) — a LOW segment in a
   routable notch is escalated to TOP.  A QoR distortion on exactly the
   designs the feature exists for.
4. **Candidate ranking prices bridges the router never builds** — nominal WL
   includes bridge length (`src/topology.cpp:1325-1332`) while the realized
   route omits it, so bridged candidates are demoted against a cost that is
   currently fictional; and the documented thru-before-over *adjusted*-WL
   ranking is the standing xfail (`topologies.md:76` vs
   `test_busterm_over_the_block.py:81`).  Resolve in whichever direction
   open 1 goes: either the metal becomes real (pricing is then correct — add
   the missing `adjusted_wl`/`teg_mode` API and un-xfail) or the doc claim
   must be retracted.

### Medium

5. **`thru` mode has no census.**  An externally-split TEG block is silent
   by design; add an INFO-level report (message-id catalogue) naming the
   rects left to internal routing, so a wrong `thru` assumption is
   discoverable without reading the topology dump.
6. **Multi-rect never reaches the hier flow**: `derive_busterms` writes
   empty rects (`src/busterm.cpp:172`), every BDB→Floorplan projection is
   `add_block(bbox)`, and `tools/buda2bdb.py` collapses with a warning.
   Decide whether hier multi-rect is in scope; if not, document the boundary
   where users will hit it (resume sessions silently losing rect geometry).
7. **`set_feedthru` on a multi-rect block is silently ignored**
   (`src/topology.cpp:3056`).  Warn at declaration — the user stated an
   intent the engine drops.
8. **BITRUNK is bbox-only** (`src/topology.cpp:4401`, `:4467-4473`): the
   datapath trees neither select rects nor bridge.  Acceptable as a scoping
   decision, but undocumented.
9. **`corner_margin` is inert for individual rects** (`src/topology.cpp:
   3296-3300`) — silently, on the faces multi-rect routing actually lands on.
10. **Bridges are drawn by nobody** — web JSON serializes them, no client
    renders them, the matplotlib viewer ignores them, and the explorer has
    no bridge affordance; `_rects_disconnected` is dead code while the
    dashed union box draws unconditionally.  A user cannot *see* the wire
    that §1.1 shows is also never built.
11. **Known generation pool bug (OPEN, audit C4-01)**: stub-suppressed
    TEG-over blocks skip the spine pre-extension, so gap-stub pairs float
    off-spine and the candidate is dropped — pool loss on TEG flows
    (`docs/internal/wishlist-topo.md:1314-1325`; also queued in
    `work_menu_2026-08-06.md`).

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
13. **Validation gaps in `add_block ... rect`**: no overlap/degeneracy check
    on the rect list (only classified later), and a malformed rect with <4
    coords raises a bare `IndexError` (`src/buda_cmds/setup_cmds.py:67-69`)
    instead of the named-argument error the fractional-coord path gets.
14. **`teg_mode` is per-block only** — no global default command exists;
    the feature scenario titled as a global-override test asserts only the
    per-block keyword.  Either add `set_teg_mode` or retitle the scenario.
15. **Vacuous tie-break test** — give `test_topo_structural_tiebreak.py` a
    bridged fixture so `len(bridge_segments)` actually varies.
16. **`topo_edit` stubs from union bbox** (`src/topo_edit.cpp:165-185`) — a
    hand-built stub can land in the notch; low priority since `edit_status`'s
    verdict would flag most consequences, but the tap-face choice should go
    through `best_rect` like generation does.
17. **No import path produces multi-rect** (DEF rectilinear macros, GDS) —
    a roadmap item rather than a defect; note it in
    `opens_interchange.md` so it is not rediscovered.

## 5. Suggested landing order

1(b) audit first (loud beats silent), with the QoR/corpus vehicle of open 2
pinning it; then decide 1(a) emission vs an explicit `over`-unsupported
refusal; 3 and 4 fall out of that decision; the medium/minor items are
independent and mostly one-liners.
