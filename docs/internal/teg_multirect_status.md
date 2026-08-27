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
feedthru warning covering the silent-by-design corners (opens 5/7 — and,
since 2026-08-27, the multi-rect feedthru RELAY itself, honoured under
`thru` and refused under `over`), the
planner/margin/TopoEdit multi-rect split-brains repaired (opens 3/9/16),
declaration validation + `set_teg_mode` (opens 13/14), and the QoR corpus,
flow tests and demo vehicles pinning it all end to end (open 2,
`demo/teg_hier_hybrid.buda` / `demo/teg_two_spellings.buda`).  Non-OVER and
single-rect designs stayed byte-identical throughout (corpus-guarded).

What remains is deliberate scope, each item verified against the code and
its pinning test on this date:

**Remaining limitations**

0. **The ANTENNA audit reads NOMINAL geometry, at both stages** (Codex P2 on
   #855, verified and scoped out of it).  `detect_antennas` takes
   `(segs, topo, fp, bundle_id, stage, result)` and **no placement argument at
   all** — `segs` is `ct.segs()`, the nominal `ConnTopology`, at the NUTS call
   site and the DNUTS one alike.  So every sibling it consults is nominal: a
   segment that went UNPLACED (or, at DNUTS, has no wire for the affected bit)
   still counts as metal holding a block or a rect.  The consequence Codex
   named is real — if a tap-overhang piece is the only PLACED wire touching an
   OVER rect while an unplaced nominal sibling intersects it, the per-rect
   guard's `still` reads true, the piece is not judged load-bearing, and the
   block-level test may report ANTENNA on a wire whose removal would open the
   rect.
   NOT introduced by the limitation-2 companion fix, and not fixable inside
   it: the PRE-EXISTING block-level test (`covered_without_piece`, issue #482 /
   Codex #517) has the identical property — it reads `segs[j]` and each
   sibling's `conns` with no placement test anywhere — and the new per-rect
   branch mirrors it deliberately, so making only the new branch
   placement-aware would leave one function reading two different worlds three
   lines apart.  The honest fix is to give the audit placement (a
   `NUTSResult` / `DetailedNUTSResult` and, at DNUTS, the bit group) and apply
   it to BOTH tests, which changes pre-existing verdicts and therefore needs
   its own corpus measurement.
   Severity is bounded by the shape: the scenario requires an unplaced
   segment, which is already an `UNPLACED` violation, and `TEG_OPEN` is
   computed from PLACED metal independently (`detect_teg_open` reads
   `TegMetal` groups, not `segs`), so the open is reported whatever ANTENNA
   says.  The defect is therefore a spurious EXTRA report on a route already
   reported dirty — never a silent pass.

1. ~~**MST candidates on OVER multi-rect blocks emit no TEG connection
   metal**: an MST edge lands on the closest rect pair only, so an OVER
   block's other rects go unreached — the legacy path, scoped by open 1
   residual (iii) (a fix is a redesign: there is no shared trunk locus to
   hang per-rect attachment from).~~  **RESOLVED 2026-08-25** — see open 1
   residual (iii) for the design and measurements: `add_mst_teg_attachments`
   runs on the FINISHED tree (after `complete_relay_junctions`, the ordering
   that keeps the relay machinery off the new face landing) and attaches
   every rect the tree leaves unreached — the audit's own inclusive contact
   on the PHYSICAL rects, expanded over `rects_touch` contiguity like
   `teg_landing_component` — with a perpendicular T-stub onto an overlapping
   tree segment, else a two-leg L onto the nearest segment's span midpoint
   (face→outward per #823, min-stub floors, `edge_id -1`).  The old firing
   pin is FLIPPED (`test_teg_open.py::
   test_mst_on_over_block_now_attaches_every_rect_and_audits_clean`), the
   spanning-edge control is pinned beside it.  (The corner that remained
   loud here — an ADJACENT rect suppressed by contiguity while a selected MST
   on such a block still reported TEG_OPEN — was limitation 2's, and is
   RESOLVED 2026-08-27 with it: `reached_now` reads contact alone now, so the
   adjacent rect gets its attachment,
   `test_mst_on_adjacent_rect_over_block_now_attaches_and_audits_clean`.)  The TRUNK+MST
   hybrids need no attachment pass: their multi-rect branch blocks flunk
   `simple` onto the legacy hybrid path, which keeps the FULL seed trunk —
   per-rect OVER connection metal included — under the clean-tree gate.
   End-to-end vehicle `flow/teg_mst_over.buda` (QoR corpus row, EXPECTED
   CLEAN).
2. ~~**The adjacent-rect Direct corner**: a trunk Direct inside one of two
   ADJACENT rects emits no connection metal — the rects are physically
   contiguous, the feature's suppression rule, transitive over the PHYSICAL
   (`Busterm::orig_rects`) touch graph — while the placed TEG_OPEN contact
   predicate reads per-rect, so such a route, if selected, still reports
   TEG_OPEN.~~  **RESOLVED 2026-08-27 — direction (b), the generator moved to
   the audit, and the crux was decided by the feature's own code.**

   The question this corner posed: `teg_mode over` declares the rects not
   internally connected, yet two rects sharing a positive-length edge are a
   contiguous FOOTPRINT — does the shared edge imply shared metal (teach the
   AUDIT to expand its contact over the contiguity graph, direction (a)), or
   is the declaration authoritative across an abutment (make the GENERATOR
   emit the metal, direction (b))?

   **A footprint is not a wire, and the code had already said so.**  A block
   rect is a PLACEMENT REGION; the metal is what the router puts down.  Two
   macros placed edge to edge are one contiguous footprint and entirely
   separate metal — which is exactly the design a user spells `over`, since a
   block whose interior DOES join its rects is what `thru` is for (and `thru`
   is the default).  The decisive evidence is internal: `rects_are_rectilinear`
   classifies by strict interior OVERLAP, and for such a block — rects sharing
   AREA, unambiguously one polygon, a STRONGER contiguity than abutment — the
   OVER Direct branch emits a connector leg to every un-spanned rect.  That
   emission is the landed §1.1/§1.3 fix, the CRITICAL one this whole arc
   started from.  Suppressing on the WEAKER contiguity while emitting on the
   stronger is not a policy, it is a leftover: the adjacency rule was stated in
   BRIDGE terms ("no gap between them, so no bridge is needed" —
   `busterm_over_the_block.feature`, and true of a bridge), and open 1(a)
   replaced bridges with each rect's OWN attachment without re-deriving it.
   The audit — written AFTER that redesign, straight off the declaration —
   reads contact per rect and was right; the generator held the stale half.
   Direction (a) was therefore rejected: it is the cheaper change and the one
   the repo's single-sourcing instinct reaches for, but it would have taught
   the audit to certify a route whose second rect no metal reaches, which is
   §1.1's silent electrical open re-introduced in a narrower shape.

   What landed, in generation: `rects_touch` and `teg_landing_component` are
   DELETED, and with them the generator's private notion of "reached" at all
   three sites that had one — the Direct branch's landing component, the
   spine-end anchoring pass's reached-component (its `connected` lambda, both
   the abutment and the strict-overlap term), and `add_mst_teg_attachments`'
   `reached_now` closure.  `rects_are_rectilinear` went with them, and its death
   confirms the argument: the Direct branch consulted it ONLY to pick which
   suppression to apply (a connector leg for a rectilinear block's cross-band
   rect, a stub for a disjoint block's rect outside the landing component) —
   both emitting the same geometry through `emit_tap_segment` — so with nothing
   suppressed the two branches are one rule and the classifier had nothing left
   to decide.  All three sites now read CONTACT and nothing else, through
   the ONE predicate the audit reads (`axis_touches_rect` / `seg_touches_rect`,
   hoisted into `topology.h`; `verify.cpp`'s `teg_touches` is a one-line
   forward to it, and the third open-coded copy in `derive_fanin_seg_bits`'
   `attach_segs` folded in too).  Measured on the two forms of the corner —
   CROSS-BAND (stacked rects, H trunk Direct inside the lower): TEG_OPEN 1 at
   nuts / 4 at dnuts → both clean, generation emitting a V stub from the upper
   rect's face y=100 to the trunk; SAME-BAND (side-by-side rects): the spine
   anchors to the sibling's face (x400→x300, was no extension) and the route is
   clean.  The MST form likewise (rect#1 gets an H T-stub from x=600).

   One companion fix was NOT optional and is the same conflation one audit
   over: the new cross-band stub overhangs its junction entirely over the block,
   and the #514 TAP-OVERHANG ANTENNA rule asks whether "the block stays
   covered without it" — a BLOCK-level question `teg_mode over` revokes, so it
   called the one wire holding rect#1 an antenna (measured: `ANTENNA` 1 at both
   stages the moment the metal appeared).  For an OVER multi-rect block the
   redundancy test is now judged per RECT, through the same contact predicate:
   a piece whose removal would leave any rect it touches untouched is
   load-bearing.  Non-OVER and single-rect blocks are untouched.

   QoR corpus (`--vs main` @ ba590394): **0 better / 0 worse / 50 unchanged**
   of 51, abstract AND detailed WL **+0.00%** (`ariane133_heal` NOT
   COMPARABLE — its fetched inputs are absent in the baseline worktree, the
   harness's documented shape).  Byte-identical is the honest reading AND the
   gap: **no checked-in design uses an abutting OVER pair**, so the corpus was
   structurally blind to the change — the coverage is the unit pins below plus
   a direct baseline-vs-branch run of every OVER vehicle there IS
   (`lShape1`, `cShape1`, `tShape1`, `teg_same_band`, `teg_two_spellings`,
   `teg_hier_hybrid`, all identical: same segment counts, same detailed WL).
   The anchoring pass's strict-OVERLAP term went with the abutment one — same
   fallacy, and leaving it would have left a second generator-only notion of
   "reached" — and those runs are what says the removal costs nothing here: a
   rectilinear block's in-band rects are span-touched, so the pass stays the
   no-op the overlap term used to guarantee.  New corpus row
   `flow/teg_adjacent.buda` (0/0/0) closes the blind spot going forward.
   Pins: `test_teg_open.py::
   test_adjacent_chain_gets_a_stub_per_rect_the_trunk_misses` (was
   `..._is_suppressed_and_separated_rect_still_stubbed`, asserting `== [500]`;
   now `== [100, 200, 500]`),
   `test_margined_adjacent_chain_taps_inset_faces` (`== [510]` →
   `== [110, 210, 510]` — the #835 tap semantic survives, its "touching =
   physical geometry" twin is moot with no touch graph left),
   `test_same_band_adjacent_pair_anchors_the_spine_and_audits_clean`,
   `test_cross_band_adjacent_pair_stubs_the_second_rect_and_audits_clean` (new
   — also the ANTENNA companion's pin), and
   `test_mst_on_adjacent_rect_over_block_now_attaches_and_audits_clean`.
   Controls held: a genuinely DISJOINT unreached rect still fires
   (`test_bitrunk_on_over_block_fires_teg_open_end_to_end`, the same-band
   no-legal-attachment MST shape, the island verdict, the legacy-bridge
   restore), a SPANNED rect still gets no redundant stub
   (`test_mst_edge_spanning_the_far_rect_gets_no_redundant_stub`), and THRU
   blocks stay exempt.  The two dirty vehicles that had been MOVED onto this
   shape in #846 — `test_teg_resume.py`'s resume-armed audit and
   `test_teg_thru_census.py`'s OVER twin — were re-homed again, onto the
   BITRUNK shape (limitation 4) — and on 2026-08-27 a THIRD time, onto the
   `TRUNK_*+MST` hybrid (limitation 8), when BITRUNK went clean too — so both
   still measure a LOUD audit rather than a vacuous one.  End-to-end vehicle: `flow/teg_adjacent.buda` (EXPECTED
   CLEAN, `test_flow_scripts.py::test_teg_adjacent_flow_routes_clean` + the
   QoR corpus row), which guards the ANTENNA companion too — the stub it
   emits overhangs entirely over the block, so a BLOCK-level tap-overhang
   verdict moves the row LOUDLY.
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
   Direct trunks on OVER multi-rect blocks; which band rects extend is
   decided PER RECT by a reached-component test — band rects the natural
   span intersects, expanded transitively over PHYSICAL connectivity
   (abutment OR strict interior overlap) — so the ADJACENT corner (item 2)
   was untouched — an adjacent same-band pair still emitted nothing and
   stayed TEG_OPEN-loud (control-pinned).  *(2026-08-27, item 2: that
   reached-component is GONE — contact alone, the audit's own predicate — so
   an adjacent same-band pair now anchors the spine like any other sibling
   and audits clean.)*  Per-rect rather than gated on the
   block-level `rects_are_rectilinear` classification, which the first cut
   was and Codex P2 on #845 caught, measured real: a MIXED rect set (an
   overlapping pair PLUS a disjoint same-band rect) classified rectilinear,
   skipped the pass wholesale, and the rectilinear leg branch has no
   same-band metal either — spine [500,700] against a sibling at 0..100,
   TEG_OPEN 1/4.  Counting a strict overlap as connected is what keeps a
   FULLY-connected rectilinear block a provable no-op (every rect joins the
   reached component) while the mixed set's separated sibling extends;
   pinned by `test_mixed_rect_set_same_band_sibling_anchors_despite_overlap`
   (fails pre-fix) with the cross-band leg interaction control beside it
   (`test_mixed_rect_set_cross_band_rect_keeps_the_rectilinear_leg` — one
   leg, no double emission).  Axis-
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
   edge, the margined variant, and the adjacent pair (a LOUD control until
   item 2 landed 2026-08-27; now the anchoring twin).
4. ~~**BITRUNK trees (legacy and two-level) are bbox-only on multi-rect
   blocks** — no rect selection, no TEG connection metal
   (resolved-as-documented open 8); an unreached OVER rect fires TEG_OPEN.~~
   **RESOLVED 2026-08-27.**  Both halves cost something real and they cost it
   in opposite directions, which is why they landed together:
   * **SELECTION.** The tap was the UNION bbox face at the PIN's along
     coordinate, which on a multi-rect block can be the GAP between rects — a
     tap on no metal.  Measured: the generation coverage gate then dropped the
     whole candidate (`[TopoGen] dropped 1 candidate(s) (0 feedthru-relay,
     first open: BITRUNK_H missing block 'r2')`), so a multi-rect design
     silently lost the datapath shape it asked for.  It now picks the tap rect
     with `best_rect` and lands at that rect's along-centre, exactly as
     `add_trunk` does — for THRU blocks too, since a tap that misses is a miss
     whatever the block declares.
   * **CONNECTION METAL.** Where the candidate did survive, an OVER rect the
     tree missed was electrically open (LOUD via TEG_OPEN, which is what made
     the scoping acceptable).  Each OVER rect now takes the trunk generator's
     own two rules: one sharing the spine's perp band is reached by GROWING
     the spine over its along-centre (a CROSSING — interior contact, needing
     no busterm seed, unlike `add_trunk`'s Direct spine-end anchoring which
     lands on a FACE and relies on `annotate_endpoints`; BITRUNK deliberately
     never calls it, so it has no face anchor to fall back on), and every
     other rect gets ONE perpendicular stub from its locus-facing face,
     FACE → spine per the #823 rule.
   ONE rule serves both families (`plan_teg_attachments`, `src/topology.cpp`):
   a two-level tree's leaf stub runs along the ROOT axis at the rect's PERP
   centre off a branch, which is the legacy rung's stub shape read through
   `Axis{!root_horiz}` — so the transposition is what makes it one rule and
   not two.  Rects are planned FARTHEST-face-first and one a planned stub
   already TOUCHES is skipped (`seg_touches_rect`, the TEG_OPEN audit's own
   predicate), so two rects sharing an along-centre on the same side yield ONE
   stub rather than the collinear containing pair `set_trim_trunk_stubs`
   exists to remove.
   **Reusing `add_mst_teg_attachments` was tried and REJECTED, measured**: on
   the pinned vehicle it emits an H stub `(900,50)→(400,50)` lying COLLINEAR
   ON the rung `(50,50)-(750,50)` over x∈[400,750] — 350 units of duplicate
   metal.  The pass has no notion that a spine already runs at the target's
   perp; its same-band case is the documented "no legal attachment → stays
   LOUD", so the legal-but-worse alternative wins instead.  Teaching it to
   EXTEND a target would change MST behaviour on a deliberately conservative
   path for no MST-side evidence, so the pass is untouched and the
   spine-shaped family got the spine-shaped rule.
   **It needed a NUTS half.**  `tighten_spans_to_reach` elects exactly ONE
   pass-through anchor per (bundle, BLOCK) — anchoring every crossing keeps
   phantom span for nothing (b44) — which rests on a block's rects being
   interchangeable covers, precisely what `teg_mode over` REVOKES.  So the
   generated rung reached x=950 and the PLACED span came back clipped to
   x=600 (rect#0's far face, the one elected crossing), TEG_OPEN firing on
   placed geometry while the candidate was correct.
   `PassthruCrossing::own_anchor` (set only for OVER multi-rect blocks) keys
   the election on the RECT there; every other design elects per block byte
   for byte.  Same per-rect reading the #514 ANTENNA rule already takes.
   ALWAYS-ON, and the scope is a property of the code rather than a
   measurement: every branch is reachable only through `Busterm::rects`, so a
   single-rect design generates the historical geometry exactly
   (`bt_all_rects` hands `best_rect` the single `{orig_bbox}` and
   `rects.empty()` keeps the pin's along-coordinate) — test-pinned on exact
   coordinates.  It DOES re-sort the WL-ordered pool on a multi-rect design
   (the rung grew), renumbering candidate indices there, which is the property
   that makes `set_trim_mst_legs`/`set_trim_trunk_stubs` opt-in; this one
   repairs electrically broken metal and a dropped candidate, so it lands
   always-on like limitations 1-3 did, and multi-rect flows pin by TYPE.
   Corpus 0 better / 0 worse / 51 unchanged, abstract AND detailed WL +0 —
   and that is a BLIND SPOT, not a clean bill: BITRUNK needs >= 4 endpoint
   blocks in one bundle and no corpus flow has that WITH a multi-rect
   endpoint.  All 13 checked-in multi-rect vehicles were therefore run
   baseline-vs-branch directly and are identical.  New corpus row
   `flow/teg_bitrunk.buda` (0/0/0) closes it going forward.
   Pins: `test_teg_open.py::
   test_bitrunk_on_over_block_routes_clean_via_per_rect_metal` (was
   `..._fires_teg_open_end_to_end`, asserting `TEG_OPEN >= 1` + the rect name
   at both placed stages), the off-band stub, the BITRUNK_V mirror, the
   two-level per-rect contact, the collinear-dedup, and the NUTS-half anchor
   test — every one of which FAILS pre-fix.  Controls held: a THRU multi-rect
   block still gets ONE stub and no invented metal, a single-rect design is
   byte-identical on exact coordinates, and a genuinely unreached rect still
   fires (limitation 8 below, which is where the two dirty vehicles moved).
5. ~~**No import path (DEF/GDS) and no hier declaration produces a
   multi-rect block**~~ — the HIER/BDB half is **RESOLVED 2026-08-27**
   (schema **v30**, `set_cell_rects`); the IMPORT half is **REFUSED, with
   the evidence**, and stays recorded as `opens_interchange.md` item 16.

   **What was true**: a BDB *cell* was one `width x height` box, so a
   multi-rect footprint had nowhere to live — `derive_busterms` wrote empty
   rects, every BDB→Floorplan projection was `add_block(bbox)`, and
   `tools/buda2bdb.py` collapsed to the union bbox.  The whole TEG machinery
   (`teg_mode`, the OVER connection metal, `TEG_OPEN`, the BUDA-1907 thru
   census) was therefore reachable only from a flat `.buda` script, and
   `demo/teg_hier_hybrid.buda` is the workaround that boundary forced:
   hierarchy in the BDB, macros hand-declared beside it, routed by the FLAT
   bundler.

   **What landed** (direction (b) of the three the task offered — (a) plus
   round-trip fidelity, without an import path):

   * **`set_cell_rects <cell> rect … [teg_mode thru|over]`** (and
     `… off`), stored as v30 `cell_rect` rows + `cell.teg_mode`.  The
     footprint hangs off the **CELL**, not the component, because that is
     what it is a property OF — LEF's `SIZE` is a MACRO property — and the
     consequences are the reason: one declaration governs every instance,
     the rects are CELL-LOCAL so a `move_comp` can never stale them, and a
     transformed instance gets them transformed with it.  A component-level
     table would have needed every mutation to rewrite N rect rows and would
     have let two instances of one cell disagree about their own footprint.
   * **The union must be exactly the cell box** (hard error naming both).
     Placement, HPWL, overlap checking and `validate()` all read the
     component bbox, which comes from the cell size; a union that disagreed
     would route against one shape and place against another — the
     split-brain open 3 fixed on the planner side.
   * **ONE projection rule** (`_fp_add_comp` in `src/buda_session/hier.py`)
     serving all five BDB→Floorplan sites — `add_blocks_from_bdb`, the
     depth frame, the cell-local template frame, and the two cross-level
     frames — so they cannot disagree about a block's shape.  Its transform
     is `src/orient_rect.py`, standalone and dependency-free for the
     `slot_groups.py` reason: `tools/bdb2buda.py` needs the same rule and
     deliberately imports only `buda_db`.
   * **`derive_busterms` stamps the rects** onto the `bt:` rows it writes.
     `BustermRow.rects` has carried an optional multi-rect JSON since v1 and
     `BustermGen` wrote it empty because a cell had no rects to write; it is
     stamped from the CLI handler rather than inside `BustermGen::derive`
     because the per-instance rects need the ORIENTATION transform, which
     lives in `topology.cpp` (the `buda` module) while `busterm.cpp` is in
     `buda_core` and cannot link against it.  Nothing consumes those rows'
     geometry today (the bundler uses busterm IDs, and generation builds its
     `Busterm`s from the Floorplan), so this is completeness, not the
     load-bearing path.
   * **A footprint that goes STALE is refused at the projection**
     (**BUDA-1919**): the union-equals-the-cell-box invariant is enforced at
     declaration, but a later `resize_cell` rewrites every instance's bbox
     and keeps the rects (so does a hand-edited `.bdb.sql`).
     `add_block_rects` derives the block's bbox FROM the rects, so
     projecting a stale footprint would hand the routing frame a different
     shape from the one placement reads.  The check sits in the ONE
     projection rule — where every frame passes — rather than in N mutation
     sites, and it falls back to the single bbox, which is the honest
     projection of what the BDB now says.
   * **The RESUME seam needs no code and is pinned anyway.**  A hier
     stage-resume HOLDS the construction commands (a replayed `add_inst` is
     a duplicate-instance error), so a re-declared footprint was never an
     option — which is precisely the argument for STORING it: the rects come
     back with the checkpoint and the projection reads them fresh.  Measured
     (`test_the_footprint_survives_a_hier_stage_resume`): a second session
     declaring only the stack + `add_blocks_from_bdb` + `load_pipeline`
     rebuilds the same multi-rect frame and reproduces the routed endpoint
     exactly.  Compare open 6's flat resume, which works for the opposite
     reason — there the recorded `add_block … rect … teg_mode over` replays
     verbatim.
   * **Round trip**: `tools/buda2bdb.py` writes a multi-rect `add_block`'s
     rects + `teg_mode` onto the synthetic child cell, and
     `tools/bdb2buda.py` exports them back as `add_block … rect … teg_mode
     over`.  The collapse warning is GONE and its test is flipped
     (`test_buda2bdb.py::
     test_multirect_geometry_and_teg_mode_survive_the_conversion`, with the
     old assertion kept in a comment) beside a new round-trip pin.

   **The one limitation the fix has, reported rather than silent
   (BUDA-1918)**: `rotate_comp`/`flip_comp` on a CONTAINER rewrite descendant
   **bboxes** and deliberately leave their orientation tokens untouched
   (`BDB::rotate_comp` composes the token only for a childless subtree —
   composing it for a hierarchical block would make GDS export
   double-transform).  For a single-bbox instance the bbox rewrite IS the
   transform; a multi-rect footprint is geometry the bbox does not carry, so
   it stays upright while the instance turns, and nothing in the row records
   that a transform happened.  The transform therefore WARNS, naming every
   affected descendant; transforming the leaf itself works (its token is
   composed, and the projection reads it — test-pinned in both directions).

   **The IMPORT half is refused on measurement, not left undone.**  Nothing
   in the current import inputs *states* a multi-rect block: DEF `COMPONENTS`
   carry no outline (the footprint is the LEF `SIZE`, one `w BY h` by
   grammar), LEF expresses a non-rectangular macro only implicitly through
   `OBS`/pin geometry, and a GDS structure's outline is whatever its shapes
   union to, with no marker for "these rects are the connection interface".
   Deriving rects from any of those is an INFERENCE with item 12's failure
   mode — the reading that turned 133 macros' `OBS` into 13,034 keepouts and
   a 1012× grid.  So the source of truth is a DECLARATION, which is exactly
   what `set_cell_rects` is; `opens_interchange.md` item 16 keeps the
   derivation question open with the same "opt-in and measured on
   `flow/ariane133`" precondition it always had.

   **QoR corpus** (`--vs main` @ 9955d9a1): **0 better / 0 worse / 51
   unchanged** of 53, abstract AND detailed WL **+0.00%**, the new row
   measuring 0/0/0 (`ariane133_heal` NOT COMPARABLE — its fetched inputs are
   absent in the baseline worktree, the harness's documented shape).  Read
   "all unchanged" as the DESIGNED answer rather than a blind spot: every
   new path is gated on the design declaring a footprint (`multirect_cells()`
   empty is one `SELECT DISTINCT` and out), and the FLAT multi-rect path —
   whose rect parser was refactored into the shared
   `parse_rect_list`/`validate_rect_list` — is genuinely covered by the
   corpus rows that already exercise it (`teg_over_audit`, `teg_mst_over`,
   `teg_adjacent`, plus `talk2`/`lShape1` through `test_flow_scripts.py`).
   What the corpus could NOT have covered is the new behaviour itself, which
   is why the new row exists.

   **Vehicle**: `flow/teg_hier_cell.buda` (QoR corpus row, EXPECTED CLEAN) —
   two `unit` instances, each holding an OVER L-shaped macro and a THRU
   two-slab macro, routed by `run_hier_bundler` → `generate_hier_topologies`
   → `run_planner hier` → NUTS → DNUTS.  Measured against the same design on
   `main` (where the footprint simply cannot be expressed): 32 → 64
   candidates, 5 → 8 bus segments, 20 → 32 bit-wires, detailed WL 3462 →
   8033, both audits clean either way, and the BUDA-1907 census appears —
   naming `u0/ioc_i` rect#0 `(400,160)-(520,310)`, which is the per-rect
   contact predicate (`teg_touches`, the same one `TEG_OPEN` reads with the
   OVER gate) running in a HIER frame for the first time.  Unit pins in
   `test_bdb_multirect_cell.py`; the migration pin is
   `test_bdb_schema.py::test_pre_v30_db_gains_the_multirect_footprint_tables_on_open`.
6. **The web client renders no legacy-load bridges** — `src/web/serialize.py`
   still serializes a restored candidate's `bridge_segments`, but no web
   renderer draws them (open 10's noted remnant); the matplotlib explorer
   and main viewer DO (`viz_common.draw_legacy_bridges`, pinned by
   `test_viz_legacy_bridge.py`).  Live designs are unaffected — generation
   emits no bridges.
7. ~~**`set_feedthru` on a multi-rect block remains inert in the engine**
   (the feedthru relay is single-rect MVP — `topology.cpp` skips
   `rects.size() > 1`); the declaration now warns instead of dropping the
   intent silently (BUDA-1908, both declaration orders — open 7, pinned by
   `test_set_feedthru_multirect_warning.py`).~~  **RESOLVED 2026-08-27** —
   and by TEG MODE, not by a size gate, because `teg_mode` and
   `set_feedthru` are declarations about the SAME thing (the block's own
   internal routing) and the MVP gate had collapsed two opposite answers
   into one refusal:
   * **THRU** declares the rects internally connected, which is exactly the
     trust a relay asks for — the tool ALREADY spends it everywhere else,
     since a `thru` gap trunk taps the nearest rect and leaves the sides
     "to the block's internal routing".  So the relay is HONOURED, and the
     spine splits at the **along-hull of the rects the spine's band
     crosses**.  Not the union bbox: a rect in ANOTHER band is not under
     the trunk, so deleting spine across its extent would claim a relay
     over empty space and leave both pieces ending in mid-air instead of
     on a face (`test_thru_multirect_relay_stops_at_the_rects_under_the_trunk`
     measures exactly that).  The hull DOES span the physical gaps
     BETWEEN the crossed rects, which is the cross-gap continuity `thru`
     asserts.  Band membership and the hull are read through
     `bt_all_rects` — the same geometry `best_rect`/`has_stub` decided the
     pass-through on, so the pass cannot disagree with itself about which
     rects the trunk meets.
   * **OVER** declares the rects NOT internally connected, which
     contradicts the relay claim, so it is REFUSED — the trunk stays whole
     (the ordinary pass-through) and `over`'s own per-rect connection metal
     is emitted as usual.  Refusing beats implementing: under `over` the
     per-rect legs T-junction the spine, so a split can leave a leg
     hanging in a removed gap — and `disconnected_islands_bridged` EXEMPTS
     an island touching a declared feedthru block, so the declaration
     would launder the very open `over` exists to expose.  A WARNING
     rather than a hard error, because the reachable spelling is a
     wildcard (`set_feedthru * *`) over a design that merely happens to
     contain an OVER block, and the declaration is still correct for every
     other block it names.
   **BUDA-1908 is retargeted, not retired** — same identity ("this
   feedthru declaration will not take effect on the named multi-rect
   block"), sharper text and a narrower trigger; it no longer fires for a
   THRU block, which is what the resolution buys.  The audits needed
   nothing: `disconnected_islands_bridged` already read a feedthru block
   per rect, the `FEEDTHRU_RELAY` and #514 tap-overhang exemptions are
   by NAME, and `TEG_OPEN` is OVER-gated, so the refusal keeps the two
   features from meeting at all.  Tests:
   `test_feedthru_multirect.py` (geometry both ways, the off-band honesty
   guard, single-rect + undeclared controls, an end-to-end audit, and the
   HIER composition — a `set_cell_rects` THRU macro relaying in a
   cell-local template frame, where the block is known by its LOCAL name)
   and the retargeted `test_set_feedthru_multirect_warning.py`; vehicle
   `flow/feedthru_multirect.buda` (QoR corpus row — `set_feedthru`
   appeared in NO corpus flow before it, so the spine-splitting branch,
   which DELETES metal, was swept by nothing).
8. **A `TRUNK_*+MST` hybrid drops the seed trunk's TEG connection metal**
   (OPENED 2026-08-27 by the limitation-4 work, which needed a still-dirty
   shape and found this one).  `add_trunk_mst_candidates` copies the seed
   trunk and then re-derives the spine from the SURVIVING branch blocks,
   which loses both forms of the seed's OVER metal.  Measured on
   `flow/teg_bitrunk.buda`'s geometry:
   * the seed `TRUNK_H@y100` spans x 100..900 (spine-end anchored onto
     rect#1's facing face) while `TRUNK_H+MST@y100` spans x 100..500;
   * `TRUNK_V@x600` carries the per-rect stub `(900,50)-(600,50)` for
     rect#1 and `TRUNK_V+MST@x600` simply does not.
   Both route to TEG_OPEN — 1 bundle-level at nuts, 4 per-bit at dnuts — so
   it is LOUD, not silent, which is the same guarantee that made BITRUNK's
   bbox-only scoping acceptable.  CLAUDE.md's stage-2 TEG section claimed
   "`TRUNK_*+MST` hybrids inherit the seed trunk's per-rect metal"; that is
   false for these two shapes and is corrected there.  The hybrid path
   already declines to rewire a multi-rect branch block (`simple = false`,
   "dropping them for an MST edge would detach the rects the stubs exist to
   connect"), so the fix is presumably to re-run the same per-rect rules
   against the hybrid's OWN final spine rather than to inherit the seed's —
   deliberately NOT attempted here, since it is a different generator with
   its own clean-tree and stub-replacement invariants.  Pinned by
   `test_teg_open.py::test_trunk_mst_hybrid_on_over_block_still_fires_teg_open`,
   and it is where the two dirty vehicles (`test_teg_resume.py`'s
   resume-armed audit, `test_teg_thru_census.py`'s OVER twin) now sit — each
   has been re-homed every time a shape was resolved out from under it, and
   both still measure a LOUD audit rather than a vacuous one.

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
  case, and since 2026-08-25 recorded as the SUFFICIENT answer rather than a
  gap: the legacy hybrid keeps the full seed trunk incl. its per-rect OVER
  connection metal, so residual (iii)'s attachment pass covers standalone
  MST only — see Final-state item 1.)
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

- ~~BITRUNK (legacy and two-level) works entirely on `orig_bbox` — no rect
  selection, no bridges.~~  RESOLVED 2026-08-27 (Final-state item 4): both
  families pick their tap rect with `best_rect` and emit the trunk
  generator's own OVER connection metal through one shared, axis-transposed
  rule (`plan_teg_attachments`), with the NUTS pass-through anchor election
  keyed per RECT for OVER blocks (`PassthruCrossing::own_anchor`).
  Single-rect designs are unchanged by construction.
- The 2-pin L/Z/U/I family is **bypassed** for any multi-rect endpoint
  (`src/topology.cpp:4646-4652` forces the n-pin path) — safe, but the 2-pin
  generator itself is not rect-aware if reached directly via bindings.
- ~~`set_feedthru` on a multi-rect block is skipped by the engine
  (`src/topology.cpp` — "MVP: single-rect only"); no warning
  anywhere, the CLI validates only block/layer existence~~ RESOLVED
  2026-08-27: the relay is HONOURED on a `teg_mode thru` multi-rect block
  (split at the along-hull of the rects the spine's band crosses) and
  REFUSED on an `over` one, which declares its rects not internally
  connected — reported in both declaration orders (BUDA-1908, retargeted;
  Final-state item 7).
- No IMPORT path produces a multi-rect block: DEF/LEF components get one
  bbox (rectilinear macros collapse), GDS import likewise.  REFUSED rather
  than undone — none of those files STATES a multi-rect block, so deriving
  one is an inference with item 12's failure mode (`opens_interchange.md`
  item 16; Final-state item 5).  `tools/buda2bdb.py` no longer collapses:
  since v30 it writes the rects + `teg_mode` onto the synthetic child cell
  and `tools/bdb2buda.py` exports them back.
- ~~The hier flow is single-bbox end to end: `BustermGen::derive` writes empty
  `rects` (`src/busterm.cpp:172`), and every BDB→Floorplan projection calls
  `add_block(bbox)` — `add_block_rects` is called from exactly one place in
  `src/`: the CLI setup command.~~  RESOLVED 2026-08-27 (schema v30,
  `set_cell_rects`): the five projections go through ONE rule
  (`_fp_add_comp`) that calls `add_block_rects` whenever the component's cell
  declares a footprint, and `derive_busterms` stamps the rects + `teg_mode`
  onto its rows — see Final-state item 5.  ~~A resumed or hier session therefore loses
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
| Generation | **no longer emits bridges** (open 1(a), 2026-08-22): the trunk generator's OVER branches emit real per-rect gap stubs / rectilinear connector legs via `emit_tap_segment` (`src/topology.cpp`, `add_trunk`); MST candidates carry attachment stubs since 2026-08-25 (`add_mst_teg_attachments`, residual (iii)); BITRUNK does since 2026-08-27 (`plan_teg_attachments`, Final-state item 4).  Still absent: the `TRUNK_*+MST` hybrid, which re-derives its spine and drops the seed's metal (Final-state item 8), and the 2-pin family, which any multi-rect endpoint bypasses anyway |
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
  8 bound and green (thru/over, gap vs inside, L-shape, pure TEG, adjacent
  rects — a "no bridge is needed" scenario until 2026-08-27, now the
  connection-metal one, Final-state item 2 — per-block override), ~~1 **xfail**: thru-before-over adjusted-WL
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
   ~~and the component
   (`teg_landing_component`: locus-containing rects expanded transitively
   over `rects_touch`) is what keeps the ADJACENT case suppressed — a rect
   touching the landing rect on a positive-length shared edge is
   physically continuous with it, the feature's adjacency rule, now
   transitive by construction (a 3-rect touching chain emits nothing while
   a separated 4th rect of the same block still gets its stub).
   The suppression's touch graph reads the PHYSICAL rects
   (`Busterm::orig_rects`, the #835 spelling) while band seeds and tap
   coordinates stay on the INSET rects — touching = physical geometry,
   tappable = inset geometry: a corner margin marks faces unusable for
   taps, it does not physically separate the rects, and the first cut read
   the inset rects for BOTH, so a margined abutting pair grew a connector
   between already-contiguous metal (Codex P2 on #841, measured and fixed
   2026-08-25).~~
   **The whole suppression is WITHDRAWN 2026-08-27** (Final-state item 2,
   which carries the argument and the measurements): abutment describes the
   FOOTPRINT and `over` declares the block's ROUTING, so the landing
   COMPONENT is now just the landing rect and every rect the trunk does not
   cross gets its stub — `teg_landing_component` and `rects_touch` are
   deleted, and "reached" is the audit's own contact predicate at every OVER
   site.  What SURVIVES of the #841 P2 fix is the half that was never about
   suppression: tap coordinates are still the INSET faces (the margined
   chain taps y=110/210/510, `test_margined_adjacent_chain_taps_inset_faces`).
   Its other half — "touching = physical geometry" — existed only to keep a
   margin from re-classifying an abutting pair as needing a connector, and
   with no touch graph left there is nothing to read in either spelling.
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
   ~~One Direct-branch corner is deliberate and stays LOUD: a trunk Direct
   inside one of two ADJACENT rects emits no connection metal (contiguous
   shape, the feature's rule) while the placed TEG_OPEN contact predicate
   is per-rect, so such a route — if selected — still reports TEG_OPEN
   (pre-existing either way: the shape emitted nothing before too).~~
   **RESOLVED 2026-08-27** — "deliberate" was inherited rather than derived:
   the rule was stated in BRIDGE terms and outlived the metal it described,
   and the generator's own rectilinear branch already emitted a leg across
   the STRONGER contiguity.  Full argument, the rejected direction (teaching
   the audit to expand its contact instead) and the measurements: Final-state
   item 2.  The
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
   the same-band family beside it (the adjacent pair among them — its loud
   control became the anchoring twin
   `test_same_band_adjacent_pair_anchors_the_spine_and_audits_clean` when
   item 2 landed 2026-08-27).
   ~~What REMAINS is (iii), narrowed and verified LOUD:
   **MST candidates (and the TRUNK+MST hybrids' multi-rect branch blocks,
   which mostly drop at generation as non-simple) still emit no TEG
   connection metal** — an MST edge lands on the closest rect pair only,
   so an OVER block's other rects go unreached.  A fix here is a genuine
   redesign, not a branch edit: there is no shared trunk locus, so
   per-rect attachment would have to target an arbitrary tree segment and
   compose with `complete_relay_junctions`' relay wiring, the shared-leg
   trims (`set_trim_mst_legs`), and ripup's per-edge L/Z flips (a flip
   re-routes the very edge a connector would hang from).~~
   **(iii) RESOLVED 2026-08-25** (`add_mst_teg_attachments`,
   `src/topology.cpp`), and the redesign's composition questions were
   answered one by one rather than dodged:
   the pass runs on the FINISHED tree — AFTER `complete_relay_junctions` —
   because running before it would hand the relay machinery a second face
   landing on the OVER block and its 2-stub OTC extension would rewire
   metal that is already connected through the tree.  "Unreached" is the
   AUDIT's own reading (inclusive contact, the generation spelling of
   verify's `teg_touches`, judged on the PHYSICAL rects the audit reads),
   ~~expanded transitively over `rects_touch` exactly like the trunk path's
   `teg_landing_component`~~ — so a rect an edge SPANS is reached
   (pass-through contact IS attachment: the r4 control adds no metal), and
   ~~an ADJACENT rect is suppressed (limitation 2's corner, which a selected
   candidate still reports per-rect)~~ **— the closure is GONE 2026-08-27
   with limitation 2 (contact alone now, `seg_touches_rect`, which IS
   verify's predicate rather than a spelling of it), so an adjacent rect
   nothing touches gets its attachment like any other:
   `test_mst_on_adjacent_rect_over_block_now_attaches_and_audits_clean`.**
   The attachment is
   ordinary segments through `emit_tap_segment`, FACE → outward (#823): a
   perpendicular T-stub from the rect's locus-facing face at its
   along-centre onto an along-overlapping tree segment, else a two-leg L
   turning onto the nearest segment's span MIDPOINT (a strict interior
   landing, so the junction never coincides with a tapped endpoint);
   cheapest total length wins, min-stub floors enforced, and a rect with NO
   legal attachment (every target shares its perp band — the same-band
   shape whose bare extension NUTS retracts) stays unreached and LOUD.
   Measured on the firing repro (4 blocks, disjoint OVER receiver): MST_HV
   10 → 11 segments — one H stub (900,100)→(500,100) tapping rect#1's face
   onto the r1→r2 edge's V leg — TEG_OPEN at both stages → both audits
   Success, 44/44 bit-wires placed.  The composition verdicts:
   `set_trim_mst_legs` runs BEFORE the pass (on the raw edge legs), so the
   A/B (`BUDA_MST_LEG_TRIM=1`) keeps the stub and the clean endpoint,
   measured on both orientations; ripup's per-edge flips cannot mistake a
   stub for an MST leg (`edge_id -1`) and `flip_mst_edge` now refuses to
   flip a leg carrying a foreign endpoint T-junctioned on its INTERIOR —
   the whole-leg extension of its bend-anchor guard, since a flip moves the
   leg wholesale and nothing downstream repairs a stranded junction
   (`test_topo_keepout_mst.py::
   test_flip_mst_edge_refuses_when_a_stub_t_junctions_a_leg_interior`,
   fail-before verified; a junction at the edge's surviving far endpoints
   p1/p2 does not block the flip); the TRUNK+MST hybrids need no pass of
   their own — a multi-rect branch block flunks `simple` onto the legacy
   hybrid path, which keeps the FULL seed trunk incl. its per-rect OVER
   connection metal (and mostly drops at the clean-tree gate as before);
   fan-in taper is untouched here (the vehicle's STRICT bundle does not
   taper, and an attachment stub taps its block like any stub, so
   `derive_fanin_seg_bits` walks it as ordinary tree metal).  OVER MST
   pools re-sort (the stub is real priced WL) — the same caveat as every
   1(a) landing; non-OVER and non-multi-rect designs are byte-identical
   (the pass gates on `teg_mode == OVER` + ≥2 rects and adds nothing
   elsewhere; corpus-guarded).  The old firing pin is FLIPPED
   (`test_mst_on_over_block_now_attaches_every_rect_and_audits_clean`, with
   the spanning-edge control beside it), the two dirty vehicles that SAT on
   the MST shape because it was dirty — `test_teg_resume.py`'s
   resume-armed audit and `test_teg_thru_census.py`'s OVER twin — moved
   onto the adjacent-rect Direct shape (limitation 2, then still loud — and
   moved AGAIN on 2026-08-27 when that shape went clean too, onto BITRUNK's
   — and a THIRD time the same day, onto the `TRUNK_*+MST` hybrid, when
   BITRUNK went clean with it (limitations 4 and 8); a resume test whose
   vehicle audits clean proves nothing about the audit staying armed), and
   BITRUNK's twin pin was open 8's
   `test_bitrunk_on_over_block_fires_teg_open_end_to_end`, now flipped to
   `..._routes_clean_via_per_rect_metal`.  End-to-end
   vehicle `flow/teg_mst_over.buda` (fix + control in one design, both
   audits Success, QoR corpus row EXPECTED CLEAN).  QoR corpus
   (`--vs main` @ 6e3ba29d): **0 better / 0 worse / 49 unchanged** of 51,
   abstract AND detailed WL **+0.00%**, the new row measuring 0/0/0
   (`ariane133_heal` NOT COMPARABLE — its fetched inputs are absent in the
   baseline worktree, the harness's documented shape).
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
   2026-08-23, with the suspected resume loss MEASURED AND REFUTED — then
   the boundary itself REMOVED 2026-08-27 (schema v30, `set_cell_rects`;
   Final-state item 5).  Read the "boundary by construction" below as the
   record of what was true until a CELL could carry a footprint: it was
   argued from the COMPONENT holding one bbox, and the answer was that the
   footprint belongs on the cell type in the first place.**  The
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
   Read §2.1's resume sentence as superseded by this entry.  What remained
   genuinely absent lived in items 8/17 — and the HIER-declaration half of
   that is resolved (Final-state item 5); the IMPORT half is refused with
   its evidence and stays in `opens_interchange.md` item 16.
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
   `test_set_feedthru_multirect_warning.py`.  *(2026-08-27: the engine gate
   is gone for THRU blocks — the relay is honoured there — so the warning
   is retargeted to `teg_mode over`, which genuinely contradicts it.  See
   Final-state item 7.)*
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
   report.  *(2026-08-27: the scoping itself is GONE — see Final-state
   item 4.  Both halves are fixed, the pin is flipped to
   `..._routes_clean_via_per_rect_metal`, and the same experiment that made
   the scoping acceptable is now the vehicle `flow/teg_bitrunk.buda`.  What
   the experiment MISSED, because it only ever pinned one candidate: on a
   different multi-rect geometry the union-bbox tap fell in the GAP and the
   coverage gate dropped the whole BITRUNK candidate, so the cost was not
   only a loud open but a silently missing datapath shape.)*
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
    edge-adjacent rects are an ordinary way to draw a rectilinear footprint
    (they drove the OVER adjacency suppression until it was withdrawn
    2026-08-27 — Final-state item 2), so
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
