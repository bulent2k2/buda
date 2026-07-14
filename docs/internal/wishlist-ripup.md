# Wishlist — Rip-up & re-route

Deferred follow-ups for the feedback-driven `ripup_reroute` pass (Python
hill-climb in `src/buda_cli.py`, driving planner + NUTS + DetailedNUTS). Index:
[`wishlist.md`](wishlist.md).

## Incremental trial replan + no-persist reruns — ✅ IMPLEMENTED

**Symptom.** On `flow/rnr/slowdown_rnr.buda` (the hier repro), stage-a ripup
took 131s and stage-b 619s; profiling put **~90% in
`CongestionPlanner::optimize_topologies`** — every trial re-planned the ENTIRE
expanded design (~185 wrappers, ~0.88s/call) to evaluate re-pinning ONE bundle,
while the NUTS solve being measured was ~0.08s.  A further ~6-8% was per-trial
waste from driving the full `run_nuts` command handler (BDB persist +
route-snapshot hash + nuts-log write + diagnostics, discarded for nearly every
trial and re-done by `_checkpoint_routing` at the end anyway).

**Fix (three pieces).**
1. **`CongestionPlanner::replan_bundle(bundles, target_bid)`**
   (`congestion_planner.{h,cpp}`, bound in `bind_routing.cpp`): rebuild band
   usage by *charging* every other wrapper's committed assignment (a synthetic
   `PlanResult` from its `seg_layers`/`seg_perp` — no candidate scoring), then
   run the escalation ladder minus rip-up (a trial may not move others; the
   Python hill-climb IS the outer rip-up) for the target alone and return its
   assignment.  Requires the prior full `optimize_topologies` on the same
   planner instance (grid/cuts/span_ref); `nullopt` → caller falls back to the
   legacy full replan.  Adopted doglegs stay adopted (their split candidates +
   exported per-segment pins are exactly the committed state to charge);
   `_rr_trial` clears only the *target's* stale dogleg overrides.
2. **`_run_nuts_internal`** — the solver core minus persist/log/diagnostics;
   `_rr_rerun` uses it for every trial/commit, and `_rr_snapshot`/`_rr_restore`
   now capture/restore the full per-wrapper plan state (assignment arrays +
   dogleg overrides), making rejected trials EXACTLY reversible — which
   retired the post-loop `dirty` full rebuild (with incremental commits that
   rebuild was actively harmful: full-replanning the committed selections
   assigns different layers and can destroy the improvement — measured
   opens 30 → 122 on the hier repro before it was removed).
3. **Lexicographic stage-b metric `(DNUTS opens, NUTS overlaps)`** + the loop
   stops only at the ABSOLUTE zero: opens-clearing moves can no longer trade
   abstract packing away silently (big2's `<=9` overlap guard tripped at 12
   before this), and after the opens hit 0 the same hill-climb grinds back any
   collateral overlap creep its own moves introduced.

**Measured (cloud).** slowdown_rnr.buda: stage-a 131s → 4.3s (30×, same
22→2); stage-b 619s → 45s (14×) with a BETTER endpoint — 92→16 opens, 0
overlaps, vs the old 60→36.  mix.buda: stage-a ripup 190s → 4.5s (42×), and
the previously-commented-out "slow improvement (3)" stage-b ripup is now
enabled in the flow (44s, final dnuts 16 violations vs 66 before).  big2
goldens hold (opens →0, overlaps ≤9).  Full fast+mid tier green.

**Still deferred:** the C++ band-injection ladder below (item 1) remains the
principled end-state; `replan_bundle` is a step toward it (the charge-others/
plan-one machinery it needs now exists).

## RR efficiency round 2 (bigHalf) — ✅ instrumentation + structural wins

**Vehicle.** `flow/big_data_test/bigHalf.buda` (tc3a, 44 blocks / 2840 nets,
half-size die — see `ReadMe_bigHalf.md`): with both `ripup_reroute` lines
enabled the clean 0/0 endpoint cost ~49s vs ~5.7s for the checked-in
negotiate-only config (same host; the ReadMe's ~3s/50.9s rows are the
author's host).

**Instrumentation (Phase 0).** Per-run timing accumulator in
`src/buda_session/ripup.py` (`_rr_t_*`): seconds + call counts for
replan / nuts / dnuts / snapshot / restore, charged inside `_rr_rerun`,
`_rr_snapshot`, `_rr_restore` (getattr-guarded — free outside a run), an
always-on one-line summary after each run's `done:` print, and a per-trial
line behind `BUDA_RR_TRACE=1`.  The breakdown confirmed the post-replan_bundle
residual is the per-trial FULL solves: bigHalf stage a `nuts 10.6s/34`
(~0.31s each, 92% of the stage); stage b `nuts 20.8s/122 + dnuts 7.1s/122`
(85%), 116 rejected trials for 6 commits.

**Structural wins (Phase 1).**
- **Commit-by-forward-restore:** the winning index move's trial state is
  snapshotted BEFORE the per-trial restore, and the commit *restores forward*
  to it instead of re-running the pipeline — one full rerun saved per
  committed iteration.  Guarded by `_rr_fwd_ok`: a trial that appended a
  dogleg candidate (or moved a dogleg slot) still commits via the legacy
  re-run, because `_rr_restore` can trim a candidate pool but never re-grow
  it; flip moves keep the legacy path too (their in-place geometry is not
  snapshot-covered).  After the forward restore the planner's cut usage is
  explicitly recharged from the restored committed assignments
  (`CongestionPlanner::recharge_committed`, the public no-scoring recharge):
  replanning consumers recharge anyway, but DIRECT cut readers — the
  visualizer's congestion overlay reads `get_cuts()` — must see the
  committed route, not the last rejected trial's recharge (Codex #286).
  Exactness pinned by A/B tests (forward vs legacy commit: identical
  wrapper state + metric + per-band cut usage, stages a and b).
- **Scoped per-trial restore:** `_rr_restore(snap, only=dirty)` rewrites only
  the wrappers a trial dirtied — the incremental replan mutates just its
  target, plus any dogleg-adopted bundles (`_rr_rerun` collects
  `{target} ∪ dogleg-slot keys`; full-replan fallbacks set dirty=None ⇒ full
  restore; NUTSEngine::run takes `const&`, and pybind's list→vector
  conversion copy is what isolates the session wrappers — C++ never mutates
  them).  bigHalf stage-b restore: 1.50s → 0.17s.
- **Tried and REVERTED — stall skip-cache:** skipping a contender whose own
  contention signature was unchanged since its last all-moves-failed sweep
  looked conservative but is WRONG: trial outcomes depend on global state,
  not the contender's own sites.  Measured on bigHalf stage b: commits to
  five other bundles freed the capacity bundle 77's alternates needed while
  77's own signature never moved — the skip stranded 52 opens (and cost MORE
  trials, 569 vs 116, from the diverged trajectory).  Re-sweep cost must be
  attacked by making trials cheaper, not fewer.
- **Measured negligible — engine reuse:** NUTSEngine construction + pitch +
  grid injection is ~0.004ms vs ~176ms per bigHalf solve (<0.01%); a cached
  engine across trials is not worth the lifetime bookkeeping.

**Retrospective follow-ups (#286 post-merge review) — ✅ ALL ADDRESSED.**
(1) A rejected full-replan-fallback trial's `_reset_doglegs` shrank a pool
the trim-only restore couldn't re-grow (phantom out-of-range selection):
`_rr_restore` now re-inserts the snapshot's dl_cand content at its slot
before trimming, and `_persist_planner_output` is gated by the new
`_rr_in_trial` flag so a trial's `run_planner` fallback can never leave
rejected planner rows for a `load_pipeline` resume.  Chasing the re-grow
test exposed a LATENT hazard in the original dl_cand capture: a bound
vector member's element access returns a REFERENCE into its storage
(def_readwrite getter = reference_internal), which dangles across
size-changing reassignments — the direct capture survived only because
size-preserving overwrites reuse the storage.  `Topology.__copy__` /
`__deepcopy__` are now bound and the snapshot takes a real copy.
(2) Cut-state parity: BOTH commit paths now `recharge_committed` (the
legacy re-run's cuts predated `_adopt_doglegs` on in-place dogleg
re-solves).  (3) The A/B exactness state is content-level (per-candidate
geometry fingerprints + the dogleg bookkeeping dicts) and a big2 @mid A/B
covers the MST/dogleg-capable branches.  Notes: mid-trial exceptions now
restore via `_rr_guarded_move` / the extracted `_negotiate_iteration`
guard, and the by-value wording below is corrected (const& + pybind's
conversion copy).  The #287 micro-nit (`+ len(tried)` in the occupant
request) is also taken.

**Measured (same host, fresh build).** bigHalf rr-enabled: 49s → **46s**,
identical 9-commit trajectory and 0/0 endpoint; slowdown_rnr 11.1s → 9.4s,
mix 10.4s → 9.4s, big2_noviz unchanged — all with identical endpoints,
moves, and trial counts.  The remaining ~27s of bigHalf stage b is the
116 full NUTS+DNUTS trial solves.

**Tried and REVERTED — layer-scoped two-tier trials.**  The obvious
incremental attack (a cheap trial tier: `NUTSEngine::rerun_bundle` swaps
the one moved bundle's segments and re-solves only the layers its old+new
segments touch — no orientation fixpoint / dogleg fallback / corner pass —
ranking the moves, with the winner re-verified by the exact full pipeline
before acceptance and a full-fidelity re-sweep on a cheap-tier stall) was
implemented and measured **3.5× WORSE** on bigHalf (46s → 160s, trials
116 → 1136).  Two structural reasons, worth recording so the next attempt
skips them: (1) on a flat few-layer design, LAYERS are far too coarse a
partition — an L-move touches one H + one V layer = half the stack, so
the "cheap" solve cost ~54ms vs ~170ms full (barely 3×), and stage b
still pays the full DNUTS (~57ms) per cheap trial; (2) the skipped
passes make the cheap metric noisy enough to mis-rank (false negatives
force the full re-sweep, false positives waste verifies), and the
diverged trajectories more than ate the per-trial saving.  **The right
follow-on shape** is a FIXED-CONTEXT single-bundle placement: hold every
other bundle's placement from the baseline, place only the target's new
segments into the existing occupancy (the `LayerSolver::repack_members`
machinery already packs a member set against non-member obstacles — it
needs a public "place these members, everyone else fixed" entry), metric
= baseline-minus-target overlaps + the new segments' overlaps.  That is
O(target segs) per trial (~1ms-class), leaving stage b's floor at the
~57ms DNUTS — which then needs the fixed-bits incremental DNUTS
(`add_fixed_bits`, the bottom-up ref/rest split) to matter.  Both are a
separate, larger round; the measured numbers above are the bar it must
beat.

## `ripup_reroute` v1 follow-ups (deferred from the implementing PR)

The `ripup_reroute [max_iter]` command (Python greedy hill-climb in
`src/buda_cli.py`) shipped as a feedback pass that reads the *actual* NUTS
overlaps (stage a) / DNUTS opens (stage b), re-routes a contending bundle to an
alternate candidate, re-runs the pipeline, and keeps moves that reduce the
metric. Validated on big2 (stage a 9→0, stage b 60→0). The following were
explicitly out of scope for v1.

1. **C++ band-injection rip-up (principled engine version). — ✅ v1 SHIPPED
   (`negotiate_congestion`, stage-a).** The feedback hook exists:
   `CongestionPlanner::inject_band_demand(layer, span, perp, amount)` maps a
   measured overlap rectangle onto the exact (cut, band) pairs via
   `for_each_band` and charges it as extra demand (records re-applied by every
   `replan_bundle` after its usage recharge; `clear_injected_demand()` resets).
   The `negotiate_congestion [max_iter]` command (run after `run_nuts`) drives
   the loop: inject each measured `nuts_result.overlap_details` rectangle with
   PathFinder-style history pressure (the same rectangle prices up each
   iteration it survives), re-plan BOTH bundles of every overlap UNPINNED via
   `replan_bundle` — the corrected cost model chooses among ALL candidates in
   one pass, no per-candidate NUTS trial — re-run NUTS, and accept only
   strictly-improving iterations (snapshot/restore otherwise).
   `ripup_reroute` remains the finisher for the residual.  Measured: big2
   9→2 overlaps in 0.25s (then ripup 2→0 in 0.4s / 9 trials); the mix hier
   repro 22→8 in 2.7s (then ripup 8→0 in 1.9s / 24 trials — the hill-climb
   alone plateaued at 2), lifting the flows' final DNUTS state to 16
   violations with a fully clean NUTS.  Tests:
   `test_ripup_reroute.py::test_negotiate_*` (canned overlap re-routes by
   cost with changed selections; big2 negotiate+ripup → 0; no-op when clean).
   **v2 SHIPPED too:** (a) *DNUTS-open injection* — `negotiate_congestion`
   auto-detects stage b (after `run_detailed_nuts`): each open segment's whole
   placed window is injected, scaled by the missing-bit fraction (an open marks
   a band whose REAL signal-track supply fell short of what the track-blind
   width model promised), metric = lexicographic (opens, overlaps).  Validated
   deterministically on the item-5 canned fixture (8→0 opens in ONE cost-driven
   iteration — the injection teaches the width model about the dead band) and
   on the hier repro (stage-b 88→6 before the ripup finisher).
   (b) *Ladder victim rip-up in the replan* — `replan_bundle_ripup`: when the
   target has no overflow-free candidate under committed+injected demand, rip
   up the committed bundle holding the most demand on the contended bands
   (`plan_band_overlap` ranking), replan the pair, accept only if both end up
   overflow-free — the `optimize_topologies` stage-2 dance in a single
   negotiation step.  negotiate applies both returned assignments; ripup
   TRIALS keep plain `replan_bundle` (a trial must not move others).
   Deepened the hier repro's negotiation: stage-a 22→5 (was 22→8), stage-b
   88→6 (was 64→14); big2 with stage-a negotiation is fully clean end-to-end
   (0 opens / 0 overlaps).

2. **Planner capacity-model fix (count signal tracks).** The deeper root cause —
   the planner's band model is layout-width based and reports `overflow=0` for
   bands NUTS/DNUTS later find contended. Tracked in
   [`wishlist-planner.md`](wishlist-planner.md) as **"Model band capacity in
   signal-track count, not layout width (Gap A part 2)"** (✅ implemented as the
   opt-in `signal_tracks` mode); resolving it lets the planner predict the
   overflow up front and engage its *own* ladder, reducing how often
   `ripup_reroute` is needed. Cross-referenced here as the principled follow-on.

3. **Hier-mode support (`run_planner hier`). — ✅ RESOLVED.** Implemented: after
   `run_planner hier`, `self.bundles` is already the expanded per-instance list
   (unique IDs, absolute coords) that NUTS/DNUTS and overlap/open detection key
   off, so `_rr_snapshot`/`_rr_restore`/`_rr_contenders`/`_rr_wrapper` needed no
   change. The only hier-specific piece is `_rr_replan_hier` (`src/buda_cli.py`),
   which re-optimizes the expanded wrappers in place — no re-expansion — preserving
   their `.hier.priority`/reservation fields (planner-read-only); `_rr_rerun`
   branches to it on a `_planner_is_hier` flag set by the `run_planner hier` /
   flat branches. A re-route naturally operates at **instance** granularity (it
   re-pins one expanded wrapper), which is exactly the right level for local
   congestion relief. Validated on `flow/hbundles/06_multipin_stress.buda`
   (stage b 8→0, stage a 2→1) and `01_pipeline_hier.buda` (clean no-op).

4. **"Only-try-relevant-candidates" speedup. — ✅ RESOLVED (as relevance
   ORDERING).** `_rr_candidate_order` scans a contender's alternates with the
   candidates whose same-orientation segments sit FARTHEST from the bundle's
   measured contention sites (overlap rects / open windows) first — the
   likeliest fixes, so the first-improving scan usually stops after 1-2 trials
   (hier stage-b finisher: 6 moves → 3 to the same 0-open endpoint).
   Deliberately a pure REORDERING rather than the filter originally sketched:
   same candidate set and cap, so no reachable fix can be pruned away — and
   the residual cost is the terminal no-improvement sweep, which a filter
   could cut only by risking exactly that.  With negotiation now clearing the
   bulk before ripup runs, the remaining sweep is small.
   *Extended (2026-07-11, wishlist-planner "Selection basis" lever 2):* under
   measured contention the pool is no longer only the first-8 window — the
   top-8 farness-ranked candidates from BEYOND it are appended after the
   legacy pool, so a higher-estimate class (OOB trunk, BITRUNK tree — always
   past index 8 in the WL-sorted list) becomes promotable exactly when every
   cheap alternate fails.  Legacy-first ordering keeps routes unchanged
   whenever a cheap move improves (goldens byte-identical); big2 stage-a
   residual overlaps 1→0.  See `test/tests/test_ripup_class_rerank.py`.

5. **Tiny synthetic stage-b (DNUTS-open) canned fixture. — ✅ RESOLVED.**
   `_build_dnuts_open_session` (`test/tests/test_ripup_reroute.py`): an
   all-POWER `add_grid_override` corridor kills M4's signal tracks exactly
   under the pinned `L_HV` trunk's Hanan window, so DetailedNUTS
   deterministically opens all 8 bits (pattern arithmetic, no packing ties —
   CPU-invariant) while the alternate `L_VH` trunk runs through healthy
   pattern.  Fast-tier tests cover the fixture itself, stage-b ripup clearing
   it (one trial), and stage-b negotiation clearing it by pure cost.

## Per-edge MST flip move-source (measured redundant) — ✅ RESOLVED (opt-in toggle)

**What.** Step 4b added a per-edge L/Z flip as a ripup move source alongside the
index alternates (`_rr_flip_edges` / `_rr_apply_move('flip', …)`). Measured
([`mst_edge_realization.md`](mst_edge_realization.md), step-4b section): the flip
is **correct but redundant** — across the corpus and constructed scenarios no
flip ever clears an overlap, because (a) the datapath winners `BITRUNK_HVH/VHV`
are not flippable (no `edge_id`, multi-tap legs) and (b) a flip only moves an
edge's bend to the opposite corner of its own bounding box — a strictly weaker
move than an index alternate, which swaps the whole topology.

**Resolution: kept behind an opt-in toggle rather than removed.**
`ripup_reroute [max_iter] [use_edge_candidates]` — the flip source is **off by
default** (the default scan builds index-alternate moves only, so no trial is
spent on flips and default routes are unaffected) and enabled by passing
`use_edge_candidates` when an expert wants to explore edge flips.  Gate:
`src/buda_cmds/nuts_cmds.py` (flag parse) → `_ripup_reroute(…,
use_edge_candidates=False)` → the `('flip', …)` move construction in
`src/buda_session/ripup.py`.  `flip_mst_edge` + `Segment::edge_id` (persisted,
BDB v14) stay first-class primitives for a possible future *stronger* per-edge
move (a Z/dogleg that genuinely changes the footprint).  The original "remove
the branch outright" proposal is superseded: the toggle preserves the
exploration path at zero default cost.

## Global-overlap re-route of NON-contended bundles — ✅ IMPLEMENTED (the global-occupant pass)

**What (historical).** ripup only re-routed bundles that appear in an
overlap; negotiate only re-plans the bundles of measured overlaps. But a
*non-contended* bundle can hold bands whose re-route lowers the **total**
overlap count. Measured example
([`mst_edge_realization.md`](mst_edge_realization.md), #178): big2 bundle 61
is not itself contended, yet pinning it to its (window-infeasible,
STRICT-rejected) `TRUNK_H+MST` candidate routes the whole design at **8
overlaps instead of 10** — a global win the greedy planner + contended-only
ripup could not see.

**As built.** `_rr_global_pass` (`src/buda_session/ripup.py`), hooked at the
stall point of `_ripup_reroute` — it runs ONLY when the normal
first-improving contender scan finds nothing and the metric is nonzero, so
every flow that reaches absolute zero (the whole corpus today) is
structurally byte-identical.  Per remaining contention site (stage a:
overlap rects; stage b: open segments' placed windows — negotiate's site
set), the committed bundles holding the site's bands are ranked by
`CongestionPlanner::band_occupants` — the rect→bands mapping of
`inject_band_demand` composed with the `plan_band_overlap` victim ranking of
`replan_bundle_ripup`, exposed as ONE read-only binding — and each
occupant's index alternates are trialed ranked against *the site's* location
(`_rr_candidate_order(sites=...)`: the occupant is non-contended, so its own
contention-derived ordering would be empty and the beyond-window promotion —
the reach to b61's window-infeasible candidate, commitable because a pinned
trial's ladder ends in BEST_EFFORT — would be lost).  Strict-improvement
accept, first improving occupant wins; budgets `_RR_GLOBAL_TOP_K=3` /
`_RR_GLOBAL_MOVES_PER_OCC=6` / `_RR_GLOBAL_MAX_TRIALS=36` per stall.
**Default on**, `no_global` keyword opts out (the churn risk the sketch
worried about is contained by the stall-only trigger + strict accept +
bounded budget; corpus verified byte-identical).

**Differentiation from negotiate's `replan_bundle_ripup` victim stage**
(kept explicit because they look similar): the victim stage triggers only
for a CONTENDED, STRICT-infeasible target; victims replan via
unpinned-STRICT (window-infeasible candidates unreachable by construction);
accept is planner-model pair-feasibility.  The global pass is
measured-metric-driven, occupant-first, pinned-trial — the complement.

**Tests** (`test_ripup_reroute.py`): `band_occupants` ranking unit
(holders positive, locked excluded, top_k), direct-drive pass mechanics
(finds the improving occupant move, restores baseline, forward snapshot
commits), stall-hook wiring + `no_global` gating, end-to-end clear on the
canned occupant fixture, and `sites=None` byte-identity for
`_rr_candidate_order`.  A fast-tier fixture where the pass is the ONLY
reachable fix turned out not to exist at small scale — the
junction-infeasibility contender source already reaches small designs'
occupants — which is itself a finding: the pass's unique value is on large
designs where junction signals are absent (the b61 class).

## RR efficiency round 3 — Phase 0: per-pass solve profile (2026-07-14)

Round 2 pinned the cost to the per-trial full solves (85–92% of RR
runtime); this round's Phase 0 instruments WHERE inside a solve the time
goes.  `NUTSResult.pass_seconds` / `DetailedNUTSResult.pass_seconds`
(observation only, buckets accumulate across the dogleg fallback's trial
re-solves — `n_solves` counts them) feed per-pass totals into the RR/
negotiate timing summary as a `solve passes:` line.

First measurement (bigHalf, both rr lines enabled, this host):

    stage b: nuts 55.4s/454 solves —
      fixpoint 14.2  corner 12.4  tighten 10.8  repair 8.7
      (context/dogleg_detect/extract/metrics ≈ 0.7 combined)
    dnuts 18.0s/454 — place 7.5  vias 2.9  bit_spans 1.7  cull 0.2

What the numbers say about the round-3 candidates:

- **tighten_pulls is ~19% of the trial solve** and is metric-neutral for
  stage a by construction (its guard rejects any move that adds an
  overlap or violation, and it never removes one — WL only).  Skipping it
  during trials is the cheapest structural win — but a forward-restore
  commit would then commit an untightened layout, so tighten-skipped
  commits must route through the legacy re-run (one full solve per
  commit; still net-positive at ~20 rejected trials per commit).
- **emit_bit_vias (2.9s) is pure output**: the stage-b metric
  (num_unplaced) is computed by place + cull; vias are never read by a
  trial.  Same commit caveat as tighten.  bit_spans must stay (the cull
  reads adjusted spans).
- **The safety net (corner 12.4 + repair 8.7) outweighs the core
  fixpoint (14.2)** — early-abort budgets (stop a trial's solve once the
  overlap count provably reaches the current metric) would truncate
  exactly these tail passes on the ~95% of trials that are rejections.
- context/extract are negligible: the fixed-context single-bundle screen
  (`LayerSolver::repack_members`) remains the headline multiplier; these
  numbers say its cheap-trial cost would be dominated by ONE layer's
  placement, i.e. milliseconds.
