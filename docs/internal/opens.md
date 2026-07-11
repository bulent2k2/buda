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
- **Floorplanner UI toggle for `cell.bottom_up`** ✅ — implemented per the
  design in [`cell_settings_ui.md`](cell_settings_ui.md): an extensible,
  schema-driven per-cell settings dialog (mirrors Optimize) with
  `cell.bottom_up` as the first `CellSetting` descriptor, plus a
  Selection-panel quick checkbox. The congruence check is shared verbatim
  with the CLI (`buda_session.hier.bottom_up_congruence_issues`, the
  orientation-aware definition); enabling is congruence-gated, clearing is
  unconditional. Command layer + tests:
  `tools/floorplanner_commands.py` /
  `test/tests/test_floorplanner_cell_settings.py`.
- **Hier topology unit tests drive a local reimplementation** ✅ —
  `test_hier_topology.py` now drives the REAL dispatch
  (`generate_hier_topologies` via a BudaSession) in every test and BDD
  scenario, the in-file reimplementation (and the cell-local floorplan
  replica) is deleted, and the cross-level case (c) branch gains its
  first coverage: three new tests pin the `drv_spec_depth` bundling, the
  `[cross-level D0→D1]` dispatch tag, absolute endpoint-spanning
  coordinates, and no-regression on sibling bundles (15 tests total).

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
   [`wishlist-planner.md`](wishlist-planner.md) → *"Selection basis …
   LEVERS 1+2 SHIPPED"*. Lever 1 landed as the opt-in `set_planner_param
   kPeak` (peak existing-band-utilization term, in both segment scoring
   and the slide-window band choice; measured at 0.1: channel_stress
   keepout opens healed at zero overlap cost, rnr/mix DNUTS −33%, tc3a
   clean — but big2's negotiate/ripup-healed flow prefers the knob off,
   so **off by default**). Lever 2 landed in `_rr_candidate_order`:
   under measured contention ripup's trial pool appends the top-8
   farness-ranked candidates from beyond the 8-cheapest-estimate window,
   so a higher-estimate class (OOB trunk, BITRUNK tree) is promotable —
   committing only on a strictly better measured metric (cheap-first
   order + strict `<`: ties keep the cheaper move) — goldens
   byte-identical, big2 stage-a residual overlaps 1→0. The kPeak default-on question is
   **decided: stays opt-in** — big2's plain pipeline strands two wide
   trunks on supply-poor bands at every tested value (`peak_util` is
   blind to absolute signal-track supply; both capacity modes), though
   negotiate+ripup fully heal it. Reopener: a supply-aware `peak_util`
   (see wishlist-planner).
### Bottom-up template planning follow-ons (added 2026-07-10)

- ✅ **Orientation-aware instance copying** — **DONE (2026-07-10, mirrors +
  180 scope)**. Bottom-up copies now support the direction-preserving
  orientations (`N/S/FN/FS`) end-to-end: geometric per-instance orientation
  detection (full-subtree shape match — hierarchical `rotate_comp`/
  `flip_comp` keep every token `'N'`, so tokens alone are untrustworthy;
  ambiguous self-symmetric layouts are disambiguated by a track-phase
  score, identity preferred when it matches), `transform_topology` /
  `transform_track_segment` / `transform_net_segment` / `transform_net_via`
  (C++, shared `OrientMap` algebra with `orient_compose`/`orient_inverse`),
  mirror-normalized `check_template_tracks` pools (both routed and
  placement-stage modes), and `align_bottom_up` mirrored-phase math
  (effective coordinate about the pattern's symmetry center σ, per-direction
  CRT-combined `K ≡ 2σ_l mod pitch_l`). The plain top-down hier expansion
  now applies the correct GEOMETRIC transform for ALL 8 orientations
  (fixing its silent translation-only mis-transform; 90° instances get
  their pinned per-segment layers dropped with a warning since H↔V swaps).
  **90°/270° instances of marked cells: ✅ DONE (2026-07-10)** via
  **rotation-class clone templates** — instead of an H↔V layer-pairing map,
  the 90° family of a marked cell is split at `run_planner hier` into its
  own clone template (virtual name `<cell>90`, uniquified; persisted with
  `bundle.cloned_from`, v19) whose candidates are generated from the
  rotated reference's actual cell-local floorplan and planned with real
  per-direction layer costs; within the class every instance is a
  direction-preserving transform of the class reference, so the existing
  copy machinery applies unchanged.  The clone never touches
  cell/component/pin tables (interchange unaffected);
  check_template_tracks / align_bottom_up group per class.
- ✅ **`generate_more_topologies` is not hier-aware** — **DONE
  (2026-07-11)**. The additive command now detects a hier-bundled session
  and accretes through the same 3-case dispatch as
  `generate_hier_topologies` (`_generate_hier_topo_one(additive=True)` →
  shared `_merge_more_candidates`: topo_uid dedup + WL re-sort with
  selection/dogleg remap). Hints match an HBundle id or first net-name
  prefix; a replica match redirects to its template; the v15 knob memo now
  replays on bulk `generate_hier_topologies` too (`_apply_hier_gen_knobs`),
  so accreted HBundle pools survive regeneration. Post-expansion accretion
  is refused with the re-run recipe (pools live on expanded wrappers then).
  `generate_topologies_for_hbundle` stays replace-only by design — parity
  with the flat `generate_topologies_for_bundle`.

## Big / blocked / conditional

*(bottom-up conditionals, added 2026-07-10 — these only fire on specific
designs and fail LOUD, never silent:)*

- ✅ **Dogleg-split templates are not copied** — **DONE (2026-07-11)**.
  When the cell-local solve needs a dogleg, `_adopt_bottom_up_doglegs`
  now adopts the split exactly like the flat flow's `_adopt_doglegs`: the
  template gets the split candidate (appended / slot-overwritten,
  `_bu_dogleg_slot` bookkeeping reset on every re-plan) fully pinned with
  the split's layers + placement overrides, and every LOCKED instance
  gets the orientation-TRANSFORMED split candidate + full pin (instance
  slots ride the shared `_dogleg_slot`, cleaned by the planner-time
  `_reset_doglegs`). The fixed copies already carried the split geometry;
  the adoption makes every wrapper's selected candidate agree with them
  segment-for-segment (the DNUTS-handoff invariant). Getting the LOCAL
  solve to even reproduce a flat dogleg exposed three frame-fidelity gaps,
  all fixed: the local planner's `seg_perp` band centres were not copied
  to the template plan, the local NUTS never saw the local planner's
  candidate-extended Hanan grid, and the cell-local floorplan dropped the
  session's declared corner margin AND min-stub lengths
  (`_apply_fp_session_settings`; min-stub deliberately NOT retrofitted
  onto the depth-projection / cross-level frames — that measurably
  regressed tuned hier flows and belongs to a golden review).
- ✅ **Resume from a pre-`run_nuts` checkpoint loses NUTS-copy
  uniformity** — **DONE (2026-07-11)**. `load_pipeline expanded` now also
  rebuilds the pre-expansion TEMPLATE wrappers
  (`_restore_bottom_up_templates`, sharing the extracted
  `_restore_wrapper` loader body): the canonical parents of the expanded
  rows, validated against their OWN cell-local floorplans (their block
  names are cell-local), with the v18-persisted local-solve selection
  restored as the full pin stage (b) requires. A pre-`run_nuts` resume
  then re-runs the cell-local solve and keeps uniform per-instance
  copies; a post-`run_nuts` resume still prefers the persisted routing
  (exact — `_bu_fixed_from_resume`, cleared by a re-plan), and a
  template without a usable persisted selection falls back per-instance
  LOUD as before. Note: re-running `run_planner hier` on a resumed
  (post-expansion) session remains unsupported (double expansion —
  pre-existing).
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
