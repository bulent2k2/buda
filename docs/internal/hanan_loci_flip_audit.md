# `hanan_loci` default-flip audit — index/test gate

**Status: APPLIED (2026-07-19, branch `claude/hanan-loci-default-flip`).**
The flip has landed on that branch: `TopologyGenerator::allow_hanan_loci_`
(src/topology.h) is now `true`, every generation command accepts the
`no_hanan_loci` opt-out (the legacy `hanan_loci` flag stays accepted as a
keep-on no-op), the pin remaps below were RE-COLLECTED against the gated
pools and applied to the checked-in flows, and the two opt-in spec tests
were inverted into a default-ON spec.  The only remaining step is the
**reference-host golden regen** (`tools/regen_goldens.py --write`, pushed
onto the same branch) — see
[hanan_loci_golden_regen.md](hanan_loci_golden_regen.md).  The sections
below are kept as the audit record; the remap table reflects the FINAL
applied indices.

Context: docs/internal/wishlist-topo.md, "Nominal-WL comparability across
shape families", piece (a).  The `hanan_loci` generation knob (opt-in,
PR #328) also samples n-pin trunk loci ON the in-bbox Hanan lines, not just
at channel midpoints.  Flipping it default-on grows every n-pin pool
~1.3–1.6x and renumbers the 1-based WL-sorted indices that
`select_topology`/`select_topologies` pins in checked-in flows and that
index-sensitive tests assert.

## Method

The default was flipped locally (`allow_hanan_loci_ = true`), the tree
rebuilt, and the fast tier run: **18 failures**.  Each was classified:

- **(a) golden CONTENT change** — new candidates change the snapshot; the
  regen is reference-host-owned (see "Golden regen kit").
- **(b) index-sensitive test** — asserts a candidate index / a pool-order
  artifact that legitimately shifts; **fixed on this branch** to be
  content-based (pin by type, strict-interior specimen filters), verified
  green under BOTH defaults.
- **(c) genuine behavioral catch** — the test flags a real defect or a
  spec that the flip itself must own; NOT papered over, listed under
  "Flip blockers".

Pin audit: every checked-in `flow/**/*.buda` + `demo/**/*.buda` with an
active `select_topology`/`select_topologies` line (31 flows, 112 pin
records) was executed to the generation stage under both defaults
(default-off vs default-on; the default-on side is the knob path — the knob
sets the same member, so the pools are identical to a flipped default).
Each pinned candidate was identified by its stable content uid
(`buda.topo_uid`) in the default-off pool and located in the default-on
pool.  **Every pinned candidate still exists under default-on** (loci only
ADD candidates; 0 pins went MISSING).

## Fast-tier failure classification (default-on, before this branch's fixes)

| # | Test | Class |
|---|---|---|
| 1 | `test_topo_analysis_golden.py::…[flow/four_blocks.buda]` | (a) golden |
| 2 | `test_topo_analysis_golden.py::…[flow/dogleg2.buda]` | (a) golden |
| 3 | `test_nuts_dogleg.py::test_dogleg2_net_pull` | (b) fixed |
| 4 | `test_nuts_dogleg.py::test_dogleg_jog_window_pruned_multicast` | (b) fixed |
| 5 | `test_nuts_dogleg.py::test_dogleg_aligned_stub_no_overstretch` | (b) fixed |
| 6 | `test_nuts_dogleg.py::test_dogleg_state_cleared_on_regenerate` | (b) fixed |
| 7 | `test_nuts_dogleg.py::test_dogleg_reset_on_replan` | (b) fixed |
| 8 | `test_nuts_alignment.py::test_same_trunk_stubs_share_one_band` | (b) fixed |
| 9 | `test_nuts_alignment.py::test_planner3_m6_window_packed_cleanly` | (b) fixed |
| 10 | `test_topo_trunk_tap_edge.py::test_contained_spine_no_tap_v` | (b) fixed |
| 11 | `test_topo_trunk_tap_edge.py::test_contained_spine_no_tap_h` | (b) fixed |
| 12 | `test_planner_charge_pull_target.py::test_level2_junction_prediction_heals_demo_b3` | (b) fixed |
| 13 | `test_topo_hanan_loci.py::test_hanan_loci_is_opt_in_default_pool_unchanged` | (c) flip-owned |
| 14 | `test_topo_hanan_loci.py::test_the_extra_hananline_loci_are_optin` | (c) flip-owned |
| 15 | `test_seg_conn_annotation.py::test_generated_npin_candidates_carry_seg_conns` | (c) generator defect |
| 16 | `test_detailed_nuts_vias.py::test_t_junction_multicast_per_bit` | (c) generator defect |
| 17 | `test_fanin_taper_shorts.py::test_same_side_column_drivers_no_cross_net_short` | (c) generator defect |
| 18 | `test_topo_containment.py::test_no_junctionless_containment_without_feedthru` | (c) generator defect |

(The loci PR measured 16; #13/#14 are the knob's own opt-in spec tests added
by that PR, which by definition fail under a flipped default.)

**Residual default-on fast-tier failures after this branch's (b) fixes: 8**
(items 1, 2, 13–18 above; 1010 passed).  Default-off (the committed state):
fast + mid tiers fully green (1484 passed).

## (b) fixes landed on this branch — content-based, green under both defaults

- `test/tests/test_nuts_dogleg.py` — `_run` now re-pins bundle 3 BY CONTENT
  to the dogleg-forming `TRUNK_H@y225` (map `_DOGLEG_PIN`) and re-runs the
  pinned pipeline when the flow's index pin landed elsewhere; the in-test
  `select_topologies 3 6` in `test_dogleg_state_cleared_on_regenerate` is
  resolved via `_cand_index` (type lookup).  No-op under default-off.
- `test/tests/test_nuts_alignment.py` — the `planner3_session` fixture pins
  all three bundles by type (`_SCENARIO_PINS` = what the planner
  auto-selects from today's default pool), so the M6 alignment/repack
  scenario cannot be swapped out by pool growth.
- `test/tests/test_topo_trunk_tap_edge.py` — `_trunk_inside` picks its
  specimen with a STRICT-interior trunk-locus filter (`lo < loc < hi`); a
  Hanan-line locus riding B's own edge is a face-riding trunk, not the
  contained-spine case these tests spec.  Midpoint loci are always strictly
  interior, so the default pool's specimen is unchanged.
- `test/tests/test_planner_charge_pull_target.py` — `_demo` pins demo
  bundle 3 by content to the strand-forming `TRUNK_V+MST@x285` before the
  first `run_planner`, keeping the b3 keepout-strand repro (and its level-2
  heal) independent of pool numbering.

## Pin remap table — RE-COLLECTED against the gated pools and APPLIED

Final (2026-07-19, the flip commit): the on-side indices were re-collected
against the GATED default-on pools (the pre-gate table's CAUTION applied —
the degenerate-loci gate shrank knob-on pools, so 4 of the 22 pre-gate rows
changed).  **20 of 112 pins remapped; 92 unchanged** (2-pin L/Z/U pools are
unaffected — loci only extend the TRUNK_H/V n-pin family).  Identity was
checked by `topo_uid` per pin (before = post-flip generation with
`no_hanan_loci` == the pre-flip default; after = the flipped default), and
the EDITED flows were re-audited: all 112 pins resolve to the identical
`topo_uid`, 0 pins MISSING from the default pools.

| Flow | Bundle | Pin (off) | -> Pin (on) | Candidate |
|---|---|---|---|---|
| demo/quickstart.buda | 1 | 2 | 7 | TRUNK_V@x350 wl=1200 |
| demo/quickstart.buda | 2 | 3 | 5 | TRUNK_V@x650 wl=1220 |
| demo/quickstart.buda | 4 | 2 | 5 | TRUNK_V@x500 wl=920 |
| flow/big_data_test/b44.buda | 1 | 8 | 15 | TRUNK_V@x700 wl=4010 |
| flow/big_data_test/big2/b24_bus_056.buda | 1 | 8 | 10 | TRUNK_V+MST@x2865 wl=5240 |
| flow/big_data_test/big2/big2_b4_b24.buda | 2 | 8 | 10 | TRUNK_V+MST@x2865 wl=5240 |
| flow/big_data_test/big_3bundles_sel_pure_mst_topo.buda | 1 | 7 | 14 | TRUNK_V+MST@x3520 wl=21424 |
| flow/big_data_test/big_3bundles_sel_pure_mst_topo.buda | 2 | 10 | 20 | TRUNK_V@x5690 wl=7300 |
| flow/big_data_test/big_3bundles_sel_pure_mst_topo.buda | 3 | 7 | 18 | TRUNK_H@y4255 wl=11195 |
| flow/big_data_test/big_3bundles_sel_trunk+mst_topo.buda | 1 | 10 | 19 | TRUNK_V@x8235 wl=21690 |
| flow/big_data_test/big_3bundles_sel_trunk+mst_topo.buda | 2 | 5 | 13 | TRUNK_H+MST@y2340 wl=6970 |
| flow/big_data_test/big_3bundles_sel_trunk+mst_topo.buda | 3 | 6 | 16 | TRUNK_V+MST@x5870 wl=11090 |
| flow/dogleg2-aligned-dogleg-stub.buda | 3 | 5 | 8 | TRUNK_H@y225 wl=729 |
| flow/dogleg2.buda | 3 | 6 | 12 | TRUNK_H@y225 wl=629 |
| flow/nuts_group_pull.buda | 1 | 1 | 3 | TRUNK_H@y8460 wl=4850 |
| flow/planner4.buda | 1 | 3 | 7 | TRUNK_H@y175 wl=655 |
| flow/planner4.buda | 2 | 5 | 10 | TRUNK_H@y440 wl=660 |
| flow/sel_topos.buda | 2 | 4 | 9 | TRUNK_V+MST@x290 wl=655 |
| flow/stub_order_swap.buda | 1 | 1 | 3 | TRUNK_H@y8460 wl=4850 |
| flow/test.buda | 1 | 2 | 5 | TRUNK_V@x450 wl=600 |

Post-gate deltas vs the stale pre-gate table: `b24_bus_056` b1 and
`big2_b4_b24` b2 land at **10** (pre-gate 11 — one gated candidate ahead of
them), and two pre-gate rows became UNCHANGED entirely —
`big2_b4_b24` b1 stays 4 and `b34_bus_028` b1 stays 2 (the gated
`TRUNK_H@y4615` abutment-line candidate was exactly the one that would have
shifted them).

Unchanged-pin flows (spot list): channel_stress (62 pins), dogleg1,
nuts_corner_overlap[_3layer], nuts_corner_touch, nuts_dogleg_cycle,
nuts_relax_range_reg, no_planner_flow, non_default_routing_orientation,
home_view_enh, planner7_noop, planner8_pinned_topos, psi1, ripup1, ripup2,
xlayer_short, sel_topos (b1,b3), quickstart (b3, b5),
big2_b4_b24 (b1), b34_bus_028.
`flow/sel_topos_typo.buda`'s pins are unreachable (the flow exits at its
deliberate `add_bus` typo before generation) — no remap needed.
`demo/quickstart.buda`'s grouped `select_topologies 3-5 2` was split
(`3,5 2 4 5`) because b4 remaps while b3/b5 do not.

## Golden regen kit (reference host only — NEVER regenerated here)

`tools/topo_snapshot.py` goldens whose CONTENT changes under default-on
(new candidates; the comparison is already order-canonical, so pure
renumbering never touches goldens):

- fast tier: `test/tests/data/topo_golden/four_blocks.txt`,
  `test/tests/data/topo_golden/dogleg2.txt`
  (the other four fast flows — four_blocks_3_bundles, dogleg1,
  double_detour, channel_stress — are content-identical: their pools gain
  no surviving loci candidates)
- mid tier (ALL four shift): `demo_comprehensive_demo.txt`,
  `big_data_test_big.txt` (digest), `big_data_test_big2_b4_bus_077.txt`,
  `rnr_mix.txt` (digest) — **UPDATE (flip commit): rnr_mix no longer
  shifts** — the flow is pinned out (`no_hanan_loci`, owner decision), so
  the regen list is 5 topo goldens, not 6

Regen: `PYTHONPATH=build:tools python3 tools/topo_snapshot.py` on the
reference host, in the flip commit, AFTER the generator gate below.

## Flip blockers — genuine defects the flip must resolve first (category c)

**STATUS: RESOLVED (2026-07-18, branch `claude/hanan-loci-degenerate-gate`).**
All three shapes below are fixed — blockers 1–2 at the ROOT
(`restore_face_graze_junctions`, src/topology.cpp: a stub's trunk-side
endpoint that lands on a face-riding block's face line gets its graze tap
cleared so the real stub↔spine junction is derived — the candidate becomes
VALID, junctions restored, fan-in taper derivable), blocker 3 by the
loci-scoped post-contract pinch gate in `generate_npin` (loci-only trunk
candidates re-checked against the FINAL contract analysis; zero-slide →
dropped with a note), plus the defense-in-depth island gate in
`filter_uncovered` (DISCONNECTED candidates dropped, same island computation
as check_topo's detect_disconnected; declared-feedthru candidates exempt —
their split-gap islands are bridged by the fed-through block).  The four
category-(c) tests below pass under a scratch default-on;
`test_topo_hanan_loci_degenerate.py` pins the repro fixtures permanently.
Default-off measured bit-identical (fast+mid 100% green, goldens untouched).
Stressed-corpus re-measurement + updated flip verdict: wishlist-topo.md
piece (a).

The extra loci are exactly the block-face/abutment Hanan lines, so a
degenerate family appears when a sampled locus COINCIDES with the faces the
trunk's stubs land on.  Three observed shapes (all reproduced with the local
default-on build):

1. **DISCONNECTED trees** (caught by
   `test_seg_conn_annotation.py::test_generated_npin_candidates_carry_seg_conns`,
   and end-to-end by `test_detailed_nuts_vias.py::test_t_junction_multicast_per_bit`):
   a trunk locus on an aligned column/row's shared face line (e.g.
   `TRUNK_H@y160` over the `_COLUMN` fixture, `TRUNK_V@x900` in the vias
   test's B/C/D column) makes every stub endpoint a busterm TAP (tap wins
   over junction), so the candidate has EMPTY `seg_conns` and
   `check_topo` flags `DISCONNECTED` (2 islands).  Being at the WL floor it
   sorts FIRST and the planner AUTO-SELECTS it — a verify-flagged
   electrically-incomplete route becomes the shipped route.  This is the
   hard blocker: aligned columns are ubiquitous.
2. **Junction-less but connected trees that defeat the fan-in taper**
   (caught by `test_fanin_taper_shorts.py::test_same_side_column_drivers_no_cross_net_short`):
   `TRUNK_H@y120` rides mb's bottom face; the tree is connected via
   same-tapped-block continuity (check_topo clean) but has no junction
   records, so the per-bit taper cannot derive driver->sink paths and falls
   back to untapered all-segments carriage.
3. **Mis-tapped zero-slide face-riders** (caught by
   `test_topo_containment.py::test_no_junctionless_containment_without_feedthru`):
   the abutment-line `TRUNK_H@y4615` (b34 fixture) is emitted with
   generator-recorded taps `{blk_15, blk_15}` (both endpoints!) where
   geometric annotation derives `{blk_00, blk_15}`; the analyzed slide
   window is degenerate (`perp_lo == perp_hi == 4615`) yet the candidate
   SURVIVED `filter_pinched` — a pinch-gate gap for this family.  Same
   family: `TRUNK_V@x2230` in the `_CONTAINED_*` fixtures (single edge-riding
   segment at the shared face line, WL floor, sorts first).

**Recommended gate (in the loci emission path or `finalize_candidates`,
BEFORE the flip):** drop a loci-emitted candidate that (i) `check_topo`
flags `DISCONNECTED`, or (ii) carries a zero-slide segment after tap
assignment (i.e. make `filter_pinched` see the final taps), or (iii) has a
multi-segment tree with empty `seg_conns` whose endpoints are all taps —
and re-run this audit afterwards (pool indices shift again).  The knob is
opt-in today, so the gate changes nothing for default-off users.

Also flip-owned:

- `test/tests/test_topo_hanan_loci.py` — both tests assert the OPT-IN
  default itself (`test_hanan_loci_is_opt_in_default_pool_unchanged`,
  `test_the_extra_hananline_loci_are_optin`, plus
  `features/hanan_trunk_loci.feature`).  The flip commit inverts them into
  a default-ON spec (and should add a `no_hanan_loci` opt-OUT knob — today
  `_make_topo_gen` only ever sets the knob TRUE, so a flipped default has
  no script-level escape hatch).
- **QoR**: the loci PR measured `rnr/mix` (hier) shifting to
  1 overlap / 32 dnuts-unplaced (from 0/0) under default-on (see
  wishlist-topo).  Re-measure after the gate; the corpus QoR decision is
  part of the flip, not of this prep.
- Mid tier was NOT fully audited under default-on (only its goldens);
  the flip commit must run `bb mid`/`bb slow` under the flipped default.

## Sidecar / BDB resume — order-insensitivity verified for CONTENT growth

- **Sidecar JSON** (`_apply_selections`, src/buda_session/hier.py): resolves
  `topo_uid` first (content fingerprint — a new candidate cannot collide
  with an existing uid; identical content is uid-deduped at accretion), then
  falls back to `(topo_type, topo_wl)` equality, then to a WARNED index
  hint.  Ambiguity check: across all 31 audited flows' pools, under BOTH
  defaults, there are **zero duplicate `(type, wl)` keys** — a trunk type
  string embeds its locus and the loci `std::set` dedups a Hanan line that
  coincides with a channel midpoint, so loci growth introduces no
  first-match ambiguity for uid-less legacy sidecar entries.  Residual
  risk noted: the `(type, wl)` fallback is FIRST-match in pool order; any
  future emitter producing same-type-same-locus-different-content
  candidates would make it silently order-dependent — keep the duplicate
  check in the flip audit re-run.
- **BDB `load_pipeline`**: candidates are restored FROM the persisted rows
  themselves, with `is_selected`/`is_pinned` as per-row flags and a v14
  uid-integrity warning (`topo_uid` recomputed vs persisted) — never an
  index into a regenerated pool — so pre-flip checkpoints resume
  identically under a flipped default.  A post-resume `generate_*` re-syncs
  the persisted pool by uid (USER candidates kept via the
  `pool_uids` match in src/buda_session/persist.py), so content growth
  re-keys rows, not selections.

## Flip-commit checklist

1. ✅ Land the degenerate-loci generator gate (blockers 1–3 above) + its
   tests (PR #335).
2. ✅ Re-run the audit collector under both defaults; refresh the remap
   table (uids are the stable side) — table above, 2026-07-19.
3. ✅ Flip `allow_hanan_loci_ = true` (src/topology.h) and make the knob
   bidirectional (`no_hanan_loci` opt-out on all five generation commands,
   threaded through `_make_topo_gen` / the hier generator / the
   rotation-class clone regen / the v15 knob-memo writer + both replay
   sites — memo encoding documented at `_record_gen_knob_memo`,
   src/buda_cmds/topologies_cmds.py).
4. ✅ Apply the pin remaps (13 flows, 20 pins); invert the two
   `test_topo_hanan_loci.py` specs + `features/hanan_trunk_loci.feature`
   (default-ON spec, opt-out spec, legacy keep-on no-op spec).
5. ⏳ Regenerate the shifted goldens ON THE REFERENCE HOST and push onto
   `claude/hanan-loci-default-flip`: **5** `topo_analysis` goldens
   (`tools/regen_goldens.py --write`; `--verify` on this branch reports
   CONTENT-DIFFERS on exactly four_blocks, dogleg2, comprehensive_demo,
   big digest, b4_bus_077 — rnr_mix reads OK since the flow itself is
   pinned out, see item 6) PLUS the NUTS placement goldens
   (`tools/nuts_snapshot.py` — four_blocks + comprehensive_demo shift in
   the mid tier, big's digest in the slow tier; mix's stays valid via the
   pin-out; found by this branch's mid run, which the pre-flip fast-tier
   audit could not see).
6. ✅ fast+mid gates — residual failures are EXACTLY the golden-content
   set awaiting the reference host: the 6 `topo_analysis` goldens + the 2
   mid-tier `nuts_placement` goldens (four_blocks, comprehensive_demo);
   1485 passed otherwise.  Five index/measurement-sensitive mid-tier
   fixtures were pinned pre-flip via `no_hanan_loci`
   (test_nuts_pull_repack's big fixture, test_planner_nontop_dead_span,
   test_planner_signal_tracks' mix repro, test_datapath_multi_trunk_qor,
   and flow/planner3.buda's contention scenario) — the fast-tier audit
   could not see these, its (b)-fix analogues.  Plus QoR
   re-measurement incl. rnr/mix — **mix REGRESSED under default-on**
   (healed endpoint 0 overlaps / 42 dnuts-unplaced / detWL +0.13% vs 0/0
   with `no_hanan_loci`) and is **PINNED-OUT by owner decision
   (2026-07-19)**: `flow/rnr/mix.buda` generates with `no_hanan_loci`,
   restoring its 0/0 healed endpoint as checked in; the regression
   numbers are kept for the record in wishlist-topo piece (a)'s final
   flip table, and root-causing the interaction is an OPEN follow-on
   there.  `bb slow` still pending on the reference host.
