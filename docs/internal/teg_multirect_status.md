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
only.  See open 1 and §2.2.)*

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
| Viz | new metal drawn like any segment; restored bridges: web JSON serializes them (`src/web/serialize.py:225-239`) but no renderer draws them |
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
   0 better / 0 worse, WL +0).  Residuals, all pre-existing and now LOUD
   via TEG_OPEN rather than silent: a trunk Direct inside ONE disjoint
   rect emits no metal for the other rects (the documented "OVER activates
   only for gap/partial-span trunks" scope, pinned by
   `test_unreached_rect_still_fires_teg_open_end_to_end`); a one-sided
   approach (all rects on the same side of the trunk) still falls back to
   the single best-rect stub; MST/BITRUNK still emit no TEG connection
   metal (multi-rect branch blocks stay on the legacy un-completed path).
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
3. **Planner split-brain on multi-rect**: `low_seg_obstructed` judges the
   union bbox while cut capacity is per-rect
   (`src/congestion_planner.cpp:738-784` vs `:363-383`) — a LOW segment in a
   routable notch is escalated to TOP.  A QoR distortion on exactly the
   designs the feature exists for.
4. **Candidate ranking prices bridges the router never builds** — ~~nominal
   WL includes bridge length while the realized route omits it~~ the pricing
   half is DISCHARGED by open 1(a) (2026-08-22): the connection metal is
   ordinary segments, so `estimated_wirelength` equals the segment sum and
   priced metal IS built metal (pinned by `test_topo_keepout_mst.py::
   test_trunk_teg_over_metal_is_ordinary_segments_and_wl_honest`; restored
   pre-change candidates keep their recorded bridge pricing).  Still open:
   the documented thru-before-over *adjusted*-WL ranking remains the
   standing xfail (`test_busterm_over_the_block.py` — `Topology.adjusted_wl`
   / per-topology `teg_mode` API still absent).

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
6. **Multi-rect never reaches the hier flow**: `derive_busterms` writes
   empty rects (`src/busterm.cpp:172`), every BDB→Floorplan projection is
   `add_block(bbox)`, and `tools/buda2bdb.py` collapses with a warning.
   Decide whether hier multi-rect is in scope; if not, document the boundary
   where users will hit it (resume sessions silently losing rect geometry).
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
9. **`corner_margin` is inert for individual rects** (`src/topology.cpp:
   3296-3300`) — silently, on the faces multi-rect routing actually lands on.
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
