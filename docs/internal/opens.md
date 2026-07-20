# Open items — the cross-subsystem priority view

What remains to focus on, ranked by value/effort. This page is a **snapshot
index** (last verified against `main`: **2026-07-15**, post PR #298) —
the details, evidence, and where-to-start notes live in the per-subsystem
wishlist files ([`wishlist.md`](wishlist.md) is their index). When an item
lands, mark it ✅ in its wishlist file, move it to a (possibly new) section below in this document, titled *Resolved (by \<date\>)*, re-verify the whole list against `main` when picking the next piece of work (parallel sessions land
items this page doesn't see).

> **Feature-suite coverage:** the Gherkin narrative spec layer
> (`test/tests/features/`) is mapped to these arcs & opens in
> [`feature_coverage_plan.md`](feature_coverage_plan.md). The open items below
> are also captured as `@future` scenarios in `features/future_directions.feature`
> — flip those to `@landed` as they ship.

> **2026-07 whole-codebase audit:** 33 findings fixed + 1 refuted + 60
> report-only leads (unverified), with the confirmed-fix table, the C7-04
> pybind view-aliasing deep dive, and sanitizer-build environment notes in
> [`audit_2026-07.md`](audit_2026-07.md).

## Substantial features (bounded, clear plans)

13. **Nominal-WL comparability across shape families (b44 follow-ons)** —
   [`wishlist-topo.md`](wishlist-topo.md) → *"Nominal-WL comparability
   across shape families"*. The generation-side residue of the b44 arc
   (PR #312 shipped the planner-side opt-in `kWLSpread`): the MST hybrid's
   nominal sits AT its envelope bottom by construction while plain trunks
   sample loci only at Hanan-channel midpoints (the WL-optimal edge-aligned
   locus is never emitted, b44's +500), and WL ties USED TO break
   alphabetically (`(wl, type)`, ASCII `'+' < '@'`) then by lowest index.
   Three bounded
   pieces: ~~**(a)** also sample trunk loci ON Hanan lines (~2× pool growth,
   measure)~~ — **SHIPPED 2026-07-17** as the opt-in `hanan_loci` generation
   knob (b44 emits `TRUNK_V@x1200` at the 3510 floor; measured growth
   ~1.3–1.6×, not 2×), then **FLIPPED DEFAULT-ON 2026-07-19** (branch
   `claude/hanan-loci-default-flip`, after the degenerate-loci gate): the
   knob is now the `no_hanan_loci` opt-out, the 20-pin remap is applied
   (uid-verified), and the 5 content-shifted goldens await the
   reference-host regen; the rnr/mix healed endpoint REGRESSED default-on
   (0/0 → 0 ov / 42 unplaced) and the flow is PINNED-OUT with
   `no_hanan_loci` by owner decision — numbers kept, and the mix–loci
   root-cause is an OPEN follow-on in wishlist-topo piece (a);
   ~~**(b)** a structural `(wl, nsegs, type)` tie-break~~ —
   ✅ shipped 2026-07-17 (`annotate_and_sort` + the Python pool-merge
   resort; the pin index audit rode the ship: 112 checked-in
   `select_topology` pins uid-verified, 109 unchanged / 3 remapped; the
   `topo_analysis` golden comparison made order-canonical so text goldens
   stay byte-owned by the reference host, digest goldens recomputed under
   the order-independent canonical hash with a baseline-equality proof —
   audit table + measurements in wishlist-topo.md); 
   ~~**(c)** dominance
   pruning~~ — ✅ SHIPPED as the opt-in `set_prune_dominated` (default off,
   bit-identical): drops a candidate whose `wl_lo` exceeds another's
   `wl_hi` ONLY when the survivor passes the non-WL routing-equivalence
   gate (same contract/feedthru, same seg count/directions/layer hints,
   survivor windows covering + spans inside the dominated one's — the
   Codex #313 condition). Corpus-measured: 0 pruned / 11-3010 dominated
   pairs refused per flow with identical endpoints — every dominated pair
   differs in shape family or corridor, so this is safety infrastructure
   for future pool-growing generation changes, not a QoR lever today;
   details + gate definition in `wishlist-topo.md` piece (c). Also parked
   there: the `kWLSpread` default-flip criteria
   ([`wishlist-planner.md`](wishlist-planner.md) →
   *"Realization-risk WL"*).

10. **The `bigHalf.buda` rr flip — ✅ DONE (2026-07-20).**  Both
   `ripup_reroute 30` lines are now enabled in the checked-in flow (the
   `30` = the host-tolerant max_iter the endpoint test guards; ripup stops
   early at 0/0 so it costs nothing on a fast host), which reaches the clean
   **0 overlaps / 0 opens** endpoint (was ~1/94 with negotiate only).  Decision backed by a healer-effectiveness sweep of
   the 15 nested flows that have DNUTS opens without RR: `ripup_reroute`
   drives 6 of them (bigHalf, big2_noviz, tc3b_flat, hbundles/06 & 07,
   big_3bundles_pure_mst) to a clean 0/0 that `negotiate_congestion` alone
   does not reach — negotiate is the cheap first pass (≈0.3–2s, halves the
   opens) and ripup is the finisher (2–5× slower, closes them).  The
   endpoint stays CI-guarded by `test_bighalf_rr_reaches_clean_endpoint`
   (now re-runs the checked-in flow with a max_iter-30 host-tolerance
   inject, robust whether the lines are enabled or reverted).

8. **Bundler follow-on corners (hier)** —
   [`wishlist-bundler.md`](wishlist-bundler.md) → *"Remaining corners"*.
   Two bounded pieces left after the bundler subsystem went
   feature-complete across flat and hier (PRs #268/#273/#276):
   **cross-level fan-in grouping** (cross-level nets keep
   STRICT/BIDIRECTIONAL grouping today because their single
   `drv_spec_path` metadata cannot describe a multi-driver group — needs
   a per-net endpoint record like the same-level `net_drivers` /
   `net_receivers`), and **hier `set_max_bundle_bits`** (the balanced
   split pass is flat-only; hier splits must propagate through the
   template↔replica linkage so every instance splits identically). Both
   fail LOUD/conservative today, never silent.

## Big / blocked / conditional

*(bottom-up conditionals, added 2026-07-10 — these only fire on specific
designs and fail LOUD, never silent:)*

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

## Resolved (by 2026-07-16)

- **BDB topology tables as the USER-topo persistence home (hier flows)** ✅
  — **DONE (2026-07-16)**, closing item 12.  Flat was already whole (v15
  `source='user'`); the hier gaps were frame resolution, not persistence:
  TopoEdit (`edit_topology`) and the `load_pipeline` loader now resolve each
  bundle's OWN floorplan (the same `_floorplan_for_hbundle` cases
  check_design uses; the loader also restores the never-read-back
  `entry/exit_busterm_ids` that gate the cell-local case), so a cell-local
  template edits/validates in its own frame, a PRE-planner hier checkpoint
  with a hand-committed USER candidate resumes (the missing-block gate used
  to reject `pa_i`/`pb_i`), and the resumed `run_planner hier` replicates
  the pinned USER template to every instance.  A POST-expansion
  `edit_commit` now persists through `_persist_planner_output` (the
  pre-expansion `_persist_topologies` used to rewrite instance wrappers as
  normal rows, clobbering the `is_expanded` checkpoint in place).  Tests:
  `test_bdb_user_topo.py` (flat lock-in + 3 hier round trips).  Follow-ons
  (op-log provenance in BDB meta, GUI hier frames, per-instance pools) in
  [`wishlist-topoedit.md`](wishlist-topoedit.md).

- **Non-TOP pin-access stub span-stretched onto its endpoint leaf** ✅
  (as NO LIVE REPRO — effectively closed by config) — **DONE (2026-07-16)**.
  The one live repro was flow 10's cross-chip `x_t*` stubs, where the planner
  downgraded a generator-hinted-TOP pin-access stub to a non-TOP layer whose
  endpoint leaf is a keepout and NUTS span-stretched the stub onto it → the
  pin-access bits were culled (a silent open). The **planner half shipped**
  as the opt-in `set_planner_param nontop_dead_span_gate` (PR #304 — refuses a
  non-TOP layer whose span has **0** keepout-clear tracks and escalates to TOP;
  `bigHalf` no-rr DNUTS unplaced **566 → 135, −76%**). The repro itself was then
  removed at the **source** by **PR #307**: the downgrade was the M5-vs-M7
  planner near-tie, and M7 sits *above* M5/M6 TOP — a genuine top metal, so
  correcting `flow/tracks/tracks.buda`'s `M7` to `TOP` eliminated the offload.
  A **full-corpus sweep** (all **109** `flow`/`demo` `.buda` scripts running
  `run_detailed_nuts` — `rg -l run_detailed_nuts flow demo -g '*.buda'`,
  including the doubly-nested `flow/big_data_test/big2/*.buda`; grepping the
  `cull_keepout_crossers` `"bit(s) removed"` WARNING) finds **0 flows with
  keepout culls** — the survivor
  span-stretch-onto-keepout event fires nowhere. The NUTS-side span-stretch
  clamp in `do_span_adjustments` (don't stretch a non-TOP segment onto a leaf
  keepout — clamp at the face) is kept as a **latent, deferred** guard in
  [`wishlist-nuts.md`](wishlist-nuts.md): with no live repro it would touch the
  delicate span-adjust path (big2's coverage-invariant strand fix) with **no
  measurable win** and real host-sensitivity risk, against the measured-change
  discipline. If the class ever recurs it is still loudly reported
  (`KEEPOUT_CROSS` + the DNUTS cull WARNING — never silent), which is the
  trigger to land the clamp.

- **A metal above the TOP band is still a top metal (layer-model gap)** ✅
  — **DONE (2026-07-16)**. `LayerType` is position-blind, so a metal
  declared above the highest TOP layer but not marked `TOP` (e.g. `M7`
  over `M5/M6 TOP`) read to the planner as a **cheap** non-TOP stub-
  offload target. Shipped `LayerStack::is_above_top` + an always-on
  config-smell **WARNING** at `build_congestion_map` (pure diagnostic,
  goldens bit-identical) naming the mis-labelled layers and pointing at
  the fix. Corrected `tracks.buda`'s M7 to `TOP` (the warning's advice) —
  measured a WIN on the hbundles suite (05 opens 32→0, 06 2/34→0/20, 07
  overlap→0). An **automatic** "treat above-TOP as TOP" override was
  measured and **rejected**: a dense stress flow (`channel_stress`)
  legitimately uses the high metal as an overflow-relief valve and
  regresses when M7 is forced TOP (0/3 → 2/5), so it stays non-TOP by
  design — "above TOP ⇒ TOP" is a per-design call, not an auto-rule. The
  diagnostic + per-design config fix is the model; a position-derived
  layer type is a deferred nicety (see wishlist-planner). Tests:
  `test/tests/test_above_top_layer_warning.py`.

## Resolved (by 2026-07-15)

- **RR fixed-context single-bundle trial screen** ✅ — **DONE (2026-07-15,
  PRs #293 + #298)**, closing item 9 exactly in the screen-then-confirm
  shape the round-2 revert pointed to.  **#293 (round 3):** per contender,
  each idx alternate is placed ALONE against every other bundle's baseline
  placement frozen as fixed occupancy (the bottom-up `add_fixed_segments`
  machinery + `set_skip_doglegs`; ~ms per candidate) and only the top-2
  screened moves are full-trialed — the screened score is an ORDERING,
  never a metric (accepts stay on the true full metric), and screened-out
  moves are DEFERRED to the iteration's stall sweep, so the stop
  certificate remains a full sweep.  Default on; `no_screen` opts out.
  bigHalf rr flow 40.8s → 16.5s (stage-b full trials 123 → 11), big2
  ripup 0.91 → 0.43s, endpoints identical everywhere.  **#298 (round
  5):** the screen batched — `NUTSEngine::screen_candidates` (one
  wrapper-list crossing per contender, C++-side copy, triples back) +
  `CongestionPlanner::replan_candidates` (recharge once, plan candidates
  uncommitted — provably the `replan_bundle`-sequence state) + the real
  win, found by instrumenting `run()`'s un-bucketed tail: skip run()'s
  dogleg-only mutable deep copy of every wrapper in screen mode.  8.9 →
  3.25 ms/screen, scores byte-identical (an exact-equivalence refactor).
  bigHalf ~12.4s flow wall.

- **RR warm-start single-bundle re-solve** ✅ (as a measured OPT-IN) —
  **DONE (2026-07-15, PR #296)**: `NUTSEngine::rerun_bundle_warm` seeds
  the baseline, places only the moved bundle against it frozen, then runs
  the real safety passes on the unfrozen union — cost tracks the move's
  blast radius.  Phase-0 fidelity study (`BUDA_RR_WARM_STUDY=1` harness,
  kept): 91–100% accept agreement, 4.6–6× cheaper per solve on bigHalf,
  errors skewing to verify-absorbable false-accepts.  Phase 1 wired
  soundly (warm pre-filter; only warm-improving moves pay a cold trial;
  warm-rejected moves cold-swept at the stall point — the certificate
  stays a full COLD sweep) but the production A/B was a WASH behind the
  #293 screen (bigHalf 12.29 vs 12.73s; big2 +0.09s), so
  `_RR_WARM_TRIALS_DEFAULT = False`: default routes byte-identical,
  `warm_trials` opts in.  The flip bar (post-screen cold trials ≥3× the
  ~41–70ms warm eval — designs several times bigHalf's size) is recorded
  in [`wishlist-ripup.md`](wishlist-ripup.md), which now carries the
  complete round 1–5 history including all measured negative results.

## Resolved (by 2026-07-14)

- **Global-overlap re-route of NON-contended bundles** ✅ — **DONE
  (2026-07-14)**, as ripup's **global-occupant pass** (the b61 class:
  big2 bundle 61, not itself contended, holds the bands whose re-route
  runs the design at 8 overlaps instead of 10 — via a window-infeasible
  STRICT-rejected candidate no contended-only scan could reach).  Runs
  ONLY when the normal contender scan stalls above zero (clean flows
  structurally byte-identical — corpus verified): per remaining
  contention site, `CongestionPlanner::band_occupants` (one new
  read-only binding composing `inject_band_demand`'s rect→bands mapping
  with `replan_bundle_ripup`'s `plan_band_overlap` victim ranking) ranks
  the committed band holders, and each occupant's alternates are trialed
  ranked against THE SITE's location, strict-improvement accept, bounded
  (3 occupants/site, 6 moves/occupant, ≤36 trials/stall).  Default on;
  `no_global` opts out.  Shipped as the middle of a five-PR RR arc
  (#286–#288 + rounds 3 in #290/#291): timing instrumentation down to
  per-pass solve profiles, commit-by-forward-restore + scoped restore,
  the #286-retrospective hardening (reject-restore pool re-grow,
  trial-persist gate, commit cut parity, the latent
  reference-internal element-dangling fix via `Topology.__copy__`),
  fast trials (metric-neutral pass skipping) and the sound stage-b
  place-abort — bigHalf's clean 0/0 endpoint 49s → **32s** with corpus
  endpoints identical.  Two measured-and-reverted negative results
  (stall skip-cache; layer-scoped two-tier trials, 3.5× worse) and the
  abort study (repair+corner are the PRIMARY overlap reducers — no
  post-fixpoint abort, no pass-skipping them) were the recorded
  constraints item 9 was then closed under — see *Resolved (by
  2026-07-15)* above.  Details: [`wishlist-ripup.md`](wishlist-ripup.md).

- **Hier bundler: CONVERGENT + COMBINED + fan-in bundles** ✅ — **DONE
  (2026-07-14, PR #276)**, closing the fan-in item's hier follow-on: the
  bundler subsystem is now feature-complete across flat and hier flows.
  `run_hier_bundler` accepts all four strategies, applied PER BUNDLING
  DEPTH to same-level nets (each net bundles once at its most specific
  level — the depth-aware semantics ARE the scope decision: fan-ins
  split across subtrees/depths stay separate routing problems). A
  multi-driver group with differing endpoint block sets becomes a
  `FANIN:root|FROM:leaves` bundle routed as a per-bit tapered fan-in
  tree in its frame (cell-local templates merge with replicas like
  STRICT; the taper is re-derived per instance at expansion from donor
  metadata, so replica net order can't misalign it); a mixed-direction
  same-set group keeps the block-to-block BIDIR treatment, INCLUDING its
  historical single-entry emission. `set_bundling` overrides apply to
  both bundlers; cross-level pairs merge under BIDIRECTIONAL *and*
  COMBINED (never-finer lattice guarantee); reasons are never a
  driverless `REC:…`. Review hardening (two blocking seam bugs: pure
  CONVERGENT stranding single-driver cross-block buses, pure BIDIR
  emission divergence on return pairs) landed with seam-targeted
  regression tests. Remaining corners → open item 8 above. Details:
  [`convergent_bundling.md`](convergent_bundling.md) HIER section.

- **COMBINED bundling + per-prefix overrides + bundle bit bound** ✅ —
  **DONE (2026-07-12, PR #273)**, flat flow. The strategies form a
  lattice — STRICT (finest) ⊂ {CONVERGENT, BIDIRECTIONAL} ⊂ COMBINED —
  and `run_bundler COMBINED` is the join: union-find merges nets
  connected by a CHAIN of either relation (sound because fan-in trees
  are direction-agnostic and per-bit tapered). `set_bundling <prefix>|*
  <mode>` gates each relation per net prefix (longest prefix wins, both
  nets must permit — e.g. keep clock nets out of every merge).
  `set_max_bundle_bits <N|auto|off>` bounds bundle size as a balanced,
  bus-preserving split pass; `auto` derives a per-endpoint-block cap
  from the shortest busterm edge vs the bits the taper lands on that
  block, enforced PER PART (Codex #273). QoR: congestion_demo's
  cpu+gpu→display merge, abstract WL −29% at zero overlaps.

- **Fan-in per-bit taper + BIT_SHORT / taper-aware audits** ✅ —
  **DONE (2026-07-12, PRs #268 follow-up + #271)**. The CONVERGENT
  fan-in realization is per-bit TAPERED (`Topology::seg_bits` /
  `BusSegment.bit_list`): planner charging, NUTS widths, and DNUTS
  emission are all member-bit-scoped, so no net's wire lands on another
  driver's block. New audits: `BIT_SHORT` in `check_dnuts` (two
  different bits of one bundle sharing a layer+track over an extended
  span — the same-bundle track-sharing exemption's blind spot once
  per-segment bit subsets exist) and the taper-aware `UNPLACED` audit
  (expects only the bits a segment actually carries, PR #271).

- **BIT_SHORT heal: same-bundle bit-track consistency in DNUTS** ✅ —
  **DONE (2026-07-17)**. The audit above found real shorts on the
  corpus: `big.buda` 44 (3 same-layer sibling pairs off one trunk,
  windows interleaved by a few slots — the per-bit junction stagger
  then overlaps different-bit spans), `mix.buda` 90.  Root cause: the
  exemption is only sound BIT-FOR-BIT, but `place_by_layer` chose each
  same-bundle segment's window independently (abstract sibling
  alignment is best-effort and congestion bumps it; even equal anchors
  can snap to offset track sets when a competitor reserves one slot).
  Fix in `place_by_layer`, natural-first: keep the historical selection
  whenever it shares no track with a span-interacting sibling carrying
  a different bit (byte-identical — the dogleg split trunks and every
  clean flow untouched); on a real conflict, ALIGN (adopt the sibling's
  exact per-bit track list — bit k joins bit k as one net) else
  DISJOINT (repick with the sibling's tracks reserved), else honest
  unplaced (never a silent short, same policy as the corner bound).
  Hazard scope: closed span test dilated by the sibling's track extent
  (the bit-span adjustment reaches past the abstract span end by up to
  the junction partner's extent).  Measured: big 44 → 0, mix 90 → 0,
  BIT_SHORT zero corpus-wide; wl_corpus identical everywhere except
  mix (+0.5% abstract WL — its rnr healing re-steers once the shorts
  are gone; still 0 overlaps / 0 unplaced); fast+mid tiers green with
  no golden churn.  Tests: `test_dnuts_same_bundle_shorts.py` (all
  three branches + exemption-scope preservation).

## Resolved (by 2026-07-12)

- **Selection basis: rank on measured routability** ✅ — **SETTLED
  (2026-07-10..12)**, all levers shipped and every open question
  decided; see [`wishlist-planner.md`](wishlist-planner.md) →
  *"Selection basis … LEVERS 1+2 SHIPPED"* for the full record. Lever 1
  = opt-in `set_planner_param kPeak` (peak existing-band-utilization in
  segment scoring + the slide-window band choice) with the supply-aware
  `peak_util` floor (region-override supply the width model can't see:
  mix pre-heal opens −55%, tc3a's 0.2 regression gone) and the
  flat-score / proportional-band-choice hybrid adopted; the default-on
  question is **decided: stays opt-in** — big2's stranding is the
  pre-charge horizon (a feedback problem no plan-time term can price;
  negotiate+ripup heal it to 0/0), and the proportional score clamp was
  implemented, measured, and rejected (mix overlaps 20→40). Lever 2 =
  ripup's beyond-window farness-ranked candidate promotion
  (`_rr_candidate_order`, goldens byte-identical, big2 stage-a residual
  1→0).

- **`refine_passes` default-on decision** ✅ — **DECIDED & SHIPPED
  (2026-07-12): hier-only default-on at 1 pass.** All gating
  measurements from
  [`refine_passes_default.md`](refine_passes_default.md) ran the same
  day: demo corpus (11 flows) exact no-ops; flat corpus no-ops except
  `big2_noviz` (0 → 60 opens, the pre-charge-horizon class — kills the
  both-paths default, flat stays 0); hier corpus all wins or no-ops,
  with `mix` healing 1/0 → **0/0** and its heal loops converging 9×
  faster (ripup trials inheriting refinement measured as part of that —
  fidelity won over gating). `run_planner hier` (and every other hier
  planner site) now defaults `refine_passes` to 1 unless the user set
  it (0 included); the one churned golden (`rnr_mix`) re-baselined
  deliberately, recording overlaps 1 → 0. Full decision record in the
  study doc.

- **Hier level ordering: deep-first + symmetric global reservation** ✅ —
  **DONE (2026-07-12)**, shipped as the two-pass synthesis: opt-in
  planner refinement passes (`set_planner_param refine_passes <n>`,
  default 0 = bit-identical). Pass 1 stays top-down (nothing plans
  blind — the deep-first failure mode is structurally excluded); each
  refinement pass revisits committed bundles DEEPEST-FIRST against real
  usage (reservations released) with the strictly-better-than-keeping
  accept rule (adopt an unrestricted STRICT replan only when it beats
  the best plan keeping the old topology, both scored on the same
  state — adopting any replan was measured and rejected: hbundles/10
  went 7→78 opens on 23 score-equal lateral moves). Measured: every
  deep-first win captured or exceeded (01 WL −21%, 02 −32%, 05 opens
  47→8 at 2 passes), 10 heals 1 ovl/7 opens → **0/0** instead of
  regressing, everything else (incl. mix2_fast) a byte-identical no-op.
  Details + table in
  [`../congestion_planner.md`](../congestion_planner.md) → *"Level
  ordering"*; tests in `test/tests/test_planner_refine.py`.

## Resolved (by 2026-07-11)

*(moved down from the active sections above as they landed; details and
evidence in the per-subsystem wishlist files as cited.)*

- **CONVERGENT fan-in topology** ✅ — **DONE (2026-07-11)**. A multi-driver
  CONVERGENT bundle now routes as a **fan-in tree** rooted at the shared
  sink with every driver block as a leaf: `_bundle_endpoints` derives
  generation endpoints from ALL of a bundle's nets (single-driver bundles
  byte-identical to the historical first-net derivation), and the existing
  direction-agnostic multicast trunk+branch / MST shapes serve the fan-in
  with the arrows reversed. The missing **net-driver fidelity check**
  landed as `NET_DRIVER_OPEN` in `check_design` (every net endpoint block
  must be in the topology's `connected_block_names` contract, plus a
  per-bit path check for fan-in bundles). The realization is per-bit
  TAPERED (`Topology::seg_bits`, derived at plan/NUTS time): each segment
  carries only the bits whose driver→sink path uses it — planner charge,
  NUTS width, and DNUTS emission all member-bit-scoped, so no net's wire
  lands on another driver's block (Codex #268 P1). Warning
  downgraded to a note; pipeline collapse tests inverted into acceptance
  tests + a mixed-driver case; QoR: `demo/ariane136_l2`'s 1024-bit
  12-driver rdata merge generates the fan-in tree and checks clean.
  The taper hardening, the flat COMBINED/overrides/bit-bound layer, and
  the hier-bundler modes (the scope decision) all landed afterwards —
  see *Resolved (by 2026-07-14)* above.
- **Keepout scope generalization** ✅ — **DONE (2026-07-11)**, landed as
  the opt-in `set_bottom_up *`: mark EVERY eligible cell at once, where
  eligible = a cell with congruent placed instances (≥2 = solve-once-COPY;
  a SINGLE instance = solve-once + freeze its cell-local routing as a
  keepout for the levels above, nothing to copy — verified: the lone
  locked instance's segment blocks a crossing depth-0 bus, DNUTS routes
  the rest around it). Reuses 100% of the marked-cell path (template
  solve, fixed-segment blockage, `check_template_tracks`, DNUTS copy) with
  no new blockage code; only a cell whose ≥2 instances are non-congruent
  is reported and left top-down (fail LOUD). The default flow still marks
  nothing, so Q4's zero-regression guarantee holds unless the user opts
  in.
  `_eligible_bottom_up_cells` (enumeration) + `_set_bottom_up_all`
  (command). Measured: `flow/rnr/mix2_fast` (patterns + align, 4 cells
  hand-marked) → `*` marks 25 cells but routes BYTE-IDENTICAL (the extra
  21 have no freezable local interconnect — a safe superset);
  `flow/hbundles/10` (no patterns/align) → `*` marks 4 cells, shifts NUTS
  212→207 segments / 1→28 overlaps and stops DNUTS LOUD on the
  track-phase mismatch (the reminder that `*` needs `align_bottom_up` +
  a track pattern, like any explicit mark). See the §10 Q4 as-built note
  in [`hier_bottom_up_planning.md`](hier_bottom_up_planning.md).
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
