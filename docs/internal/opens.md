# Open items — the cross-subsystem priority view

What remains to focus on, ranked by value/effort. This page is a **snapshot
index** (last verified against `main`: **2026-07-09**, post PR #226/#227) —
the details, evidence, and where-to-start notes live in the per-subsystem
wishlist files ([`wishlist.md`](wishlist.md) is their index). When an item
lands, mark it ✅ here *and* in its wishlist file; re-verify the whole list
against `main` when picking the next piece of work (parallel sessions land
items this page doesn't see).

## Quick wins (small, low risk)

*(bottom-up items added 2026-07-10, post PR #238 + its follow-ons; details
in [`hier_bottom_up_planning.md`](hier_bottom_up_planning.md) §11 unless
noted.)*

- **`align_bottom_up` slack-aware default cap + auto-revert** ✅ — resolved
  as an EXACT-GEOMETRY cap rather than a derived scalar (a scalar slack
  bound would also block large-but-legal nudges, e.g. mix2's 90 µm snaps):
  by default any move whose post-apply `validate()` diff shows a NEW
  overlap/outside-die issue is auto-reverted to a fixpoint (a revert can
  newly collide with a still-moved sibling); `force` keeps such moves with
  the old WARNING-only behavior. `max_shift` remains the explicit user cap.
- **Floorplanner UI toggle for `cell.bottom_up`** — the flag is persisted
  (v17) and the engine/BDB sides are done; the GUI neither displays nor
  edits it. Design proposed in
  [`cell_settings_ui.md`](cell_settings_ui.md): an extensible, schema-driven
  per-cell settings dialog (mirrors Optimize) with `cell.bottom_up` as the
  first descriptor, reusing the CLI's congruence check.
- **Hier topology unit tests drive a local reimplementation** —
  `test_hier_topology.py` re-implements generation in-file instead of
  calling `HierMixin._generate_hier_topo_one`, so the cross-level (case c)
  branch of the real dispatch has no coverage. Port the tests to the real
  entry point.

## Substantial features (bounded, clear plans)

2. **CONVERGENT fan-in topology** —
   [`wishlist-bundler.md`](wishlist-bundler.md) → *"Multi-source (fan-in)
   topology support"*. The last whole-subsystem gap: a CONVERGENT bundle
   spanning several driver blocks routes from ONE arbitrary driver (the CLI
   warns rather than misroutes). Needs a multi-source fan-in tree shape
   (reuse `trunk_mst`/`compute_mst`) plus a net-driver fidelity check in
   `check_topo`. Full investigation: `convergent_bundling.md`. **Highest
   user-facing value** of the open items.
3. **Selection basis: rank on measured routability** —
   [`wishlist-planner.md`](wishlist-planner.md) → *"Selection basis: rank on
   measured routability, not the generation-time WL estimate"*. The
   WL-estimate ranking structurally under-selects `BITRUNK` datapath trees
   that route better than they estimate. Levers sketched: a peak-band-demand
   selection term, or letting negotiate/ripup up-rank across candidate
   classes. The natural continuation of the 2026-07 planner work (signal
   tracks, abutment Gap A, `kHeight`); real golden churn to review.
### Bottom-up template planning follow-ons (added 2026-07-10)

- **Orientation-aware instance copying** — the Q1 decision deferred it:
  instances of a bottom-up cell must be identity-orientation today
  (guard-and-refuse; the top-down flow only warns). Supporting rotated /
  mirrored instances means rotate/mirror-aware `offset_topology` plus
  transformed NUTS/DNUTS copies — and would also fix the pre-existing
  silent mis-transform in the plain top-down hier expansion. Biggest of
  the bottom-up deferrals; the BDB side (`component.orient`,
  `rotate_comp`/`flip_comp` composition) is already in place.
- **`generate_more_topologies` is not hier-aware** — the additive
  per-bundle command matches by net-name against the flat floorplan and
  never enters the 3-case hier dispatch; `generate_topologies_for_hbundle`
  is replace-only. An expert cannot accrete candidates for an HBundle.

## Big / blocked / conditional

*(bottom-up conditionals, added 2026-07-10 — these only fire on specific
designs and fail LOUD, never silent:)*

- **Dogleg-split templates are not copied** — when a cell's LOCAL NUTS
  solve needs a dogleg, that cell falls back to per-instance global NUTS
  with a WARNING. Copying a split means adopting the dogleg topology on
  the template AND offsetting it per instance (candidates, plan arrays,
  slide overrides). See
  [`hier_bottom_up_planning.md`](hier_bottom_up_planning.md) §4.3 as-built.
- **Resume from a pre-`run_nuts` checkpoint loses NUTS-copy uniformity** —
  `load_pipeline expanded` restores locked wrappers, but with no persisted
  routing and no template wrappers the local solve cannot be reconstructed
  (loud WARNING, per-instance fallback). Fix = rebuild template wrappers
  from the persisted template bundle rows (candidates + the v18-persisted
  template selection are all there).
- **Keepout scope generalization** — deferred by the Q4 decision: only
  *marked* cells' copied routing blocks higher levels; making EVERY hier
  cell's planned+NUTSed local routing a blockage is a possible future
  knob (changes results of every existing hier design — needs golden
  review).

5. **Global-overlap re-route of NON-contended bundles** —
   [`wishlist-ripup.md`](wishlist-ripup.md) → *"Global-overlap re-route of
   NON-contended bundles"*. The measured b61-class global win (10 → 8
   overlaps from re-routing a bundle that is not itself contended).
   Explicitly bigger/riskier — enlarges the ripup search, can churn; only
   worth it if the corpus shows several such cases.
6. **True along-flex trunk DOF (Stage C)** —
   [`wishlist-topo.md`](wishlist-topo.md) → *"True along-flex trunk DOF"*.
   Stage A (ConnSeg `along_flex`/`along_pull`) landed; the always-on flip is
   **blocked** by regressions upstream of NUTS (far-face traversal inflates
   V-trunk WL and flips planner selections) — that investigation gates any
   NUTS-side work.
7. **OA bridge (import/export)** — [`wishlist-bdb.md`](wishlist-bdb.md) →
   *"Persist the routing pipeline into the BDB"* (the export consumer) and
   `gds_oa_interchange.md`. Everything BDB-side is ✅ (persist stages 1–5,
   resume, GDS round-trip incl. rotation/mirror); the OA half is **gated on
   the proprietary Si2 OA C++ libraries** — waits on external access, then
   follows the documented pattern (own translation unit behind a CMake flag).

## Recently resolved (verified on main, 2026-07-09)

- **Rename `check_connectivity` → `check_design`** ✅ — the command audits
  connectivity, layer directions, and keepout crossings, so the old name
  undersold it. `check_design` is the primary name; `check_connectivity`
  stays registered as a legacy alias (identical handler — alias regression
  test `test_check_design_alias.py`). All `flow/`, `demo/`, `tools/`, and
  test call sites migrated; the check headers now read "Verifying …-level
  design" and the clean verdict "Success: no violations found." (layer-dir
  and keepout violations were never *opens*). See
  [`wishlist-nuts.md`](wishlist-nuts.md).

- **Verify keepout-blindness (`KEEPOUT_CROSS`)** ✅ — `check_nuts` flags a
  placed segment lying ON a keepout that overlaps its span (the live
  exhausted-window commit — it used to say "Success"; hbundles/10's 2
  commits now reported), `check_dnuts` flags a crossing bit with the cull's
  own predicate (defense-in-depth). Both take `zone_fp` (the floorplan the
  engine placed against) so hier bundles' zone-less resolved floorplans
  can't mask real conflicts. See
  [`keepout_model_audit.md`](keepout_model_audit.md) class 4 and
  [`wishlist-nuts.md`](wishlist-nuts.md).

- **Abstract-vs-detailed keepout model audit** ✅ — the two stages now agree
  on what a keepout blocks: span-aware DNUTS track pools
  (`signal_tracks_in_span`, preferred with midpoint fallback), a
  post-adjustment crossing cull (`num_keepout_bits`, zero false positives —
  the naive span-hard filter would have stranded 495 corpus bits vs the 3
  real crossings it exposes in channel_stress), an abstract
  `num_keepout_conflicts` report channel, and empty-`layer_ids` = blocks-all
  unification. Full write-up:
  [`keepout_model_audit.md`](keepout_model_audit.md); the `KEEPOUT_CROSS`
  spin-off is also resolved (above).

- **2-pin / n-pin filter-ordering unification** ✅ — one shared
  post-emission pipeline (`finalize_candidates`: annotate → sort → keepout
  cull → pinch → coverage fill).  Both golden corpora byte-identical; the
  n-pin path gains the keepout cull, closing a silent-open class (a dead
  stub/MST edge routed 0/0 at NUTS then stranded every bit at DNUTS, and
  was reachable via stage-a ripup).  See [`wishlist-topo.md`](wishlist-topo.md)
  → *"Unify the 2-pin vs n-pin filter ordering … ✅ IMPLEMENTED"*.

- **Pre-planner hier slide columns** ✅ — `dump_topologies` now resolves each
  hier bundle's generation-time floorplan (`_make_topo_fp_resolver`, sharing
  `check_connectivity`'s `_floorplan_for_hbundle`), so a cell-level template
  shows real finite `mslide`/`wl[lo..hi]`/`--conn` slides before
  `run_planner hier`, matching the flat flow (the PR #215 `free` display
  remains for genuinely unresolvable slides). See
  [`wishlist-topo.md`](wishlist-topo.md) → *"Resolve pre-planner hier slide
  columns … ✅ RESOLVED"*.

- **Per-edge MST flip move-source** ✅ — already resolved as an **opt-in
  toggle** rather than a removal: `ripup_reroute [max_iter]
  [use_edge_candidates]` keeps the measured-redundant flip source off by
  default (zero trial cost, routes unaffected) while preserving it for
  exploration. See [`wishlist-ripup.md`](wishlist-ripup.md) → *"Per-edge MST
  flip move-source … ✅ RESOLVED (opt-in toggle)"*.
- **Corner-touch generation gap** ✅ — rescued at generation independent of
  `corner_margin` via `CORNER_HV`/`CORNER_VH` diagonal L's (reusing the MST
  path's `corner_diagonal_L`); fully-coincident blocks correctly stay
  candidate-free. See [`wishlist-topo.md`](wishlist-topo.md) corner-margin
  item, Experiment 2 follow-up.
- **Partially-overlapping blocks** ✅ — free-corner `L_OVL` candidates
  (PR #221) and corner-wrapping `U_OVL_*`/`UU_OVL_*` with load-bearing
  per-segment perp clamps (PR #224), clamps persisted to BDB v16 and
  restored by `load_pipeline`. See [`wishlist-topo.md`](wishlist-topo.md)
  *"Persist the overlap-U perp clamps"* (✅).
- **LOW-layer abutment crossings** ✅ (PR #225) — Gap A predicate flags an
  empty open interior between two distinct abutting endpoint cells; big2
  full flow 0 overlaps / 0 DNUTS opens (was 0/72), ~1 s.
- **`kHeight` short-stub layer steering** ✅ (PR #226) — short segments
  prefer the lowest same-direction TOP layer; rnr/mix WL −5.4 %, overlaps
  3 → 1; `set_planner_param kHeight 0` restores the legacy tie-break.
