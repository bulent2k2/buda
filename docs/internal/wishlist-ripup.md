# Wishlist — Rip-up & re-route

Deferred follow-ups for the feedback-driven `ripup_reroute` pass (Python
hill-climb in `src/buda_session/ripup.py`, driving planner + NUTS +
DetailedNUTS). Index: [`wishlist.md`](wishlist.md).

## Status (2026-07-15, post PR #298) — the RR speedup arc is CLOSED OUT

Rounds 1–5 are all on `main`; this file is their decision record, newest
section last.  Headline: bigHalf's rr-enabled clean 0/0 endpoint went
**~49s → ~12.4s flow wall** (~8s of rr+negotiate on top of the ~4.5s
no-rr config, same host),
with every round gated on identical/clean corpus endpoints:

- **R1** incremental trial replan + no-persist reruns (30–42× on hier).
- **R2** timing instrumentation, commit-by-forward-restore, scoped
  restore (+ two recorded reverts: stall skip-cache, layer-scoped
  two-tier trials).
- **R3** fast trials + the sound stage-b place-abort + the abort study
  (#289–#291), then the **fixed-context screen** (#293, default on).
- **R4** warm-start single-bundle re-solve (#296): fidelity proven
  (91–100% agreement, 4.6–6×/solve), production wash behind the screen —
  shipped **opt-in** (`warm_trials`), default off.
- **R5** batched screen (#298): 8.9 → 3.25 ms/screen, byte-identical —
  the cost was run()'s dogleg-only wrapper deep copy, not the plumbing.

**What would reopen work here (all trigger-gated, bars recorded below):**
the warm-default flip (post-screen cold trials ≥3× the ~41–70ms warm
eval), the stage-b opens-proxy (only if a corpus shows the abstract
screen mis-ordering), fixed-bits incremental DNUTS (only if stage b's
~30–60ms per-trial DNUTS floor ever dominates), and the C++
band-injection ladder end-state (item 1 below; `negotiate_congestion`
already covers the practical need).  Nothing is blocked; nothing fails
silent.

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


## RR efficiency round 3 — fast trials (metric-neutral pass skipping, 2026-07-14)

Shipped as the round's first lever (default on; `no_fast_trials` opts out):
stage-a trials skip `tighten_pulls` (overlap-non-increasing by its per-move
guard → the trial metric is an UPPER BOUND: accepts sound, rejections rarely
spurious), stage-b trials skip via emission (pure output, metric identical).
Commits always re-run the full pipeline (fast trials take no forward
snapshots), so `m_true <= m_skip < cur` guarantees every commit strictly
improves the TRUE metric and the committed route is a full-pipeline state.

Measured (this host, A/B on vs `no_fast_trials`):

    mix / slowdown_rnr / big2:  byte-identical trajectories and endpoints
    bigHalf (rr enabled):       32.0s vs 87.9s, same 0/0 endpoint
      - direct per-solve savings: stage-b dnuts 34.6ms vs 40.0ms (-13%,
        the vias skip); stage-a solves skip tighten (~19% of a solve)
      - the REST of the gap is a trajectory effect: fast trials' spurious
        rejections changed which improving move was found first (23 vs 16
        stage-a trials), and the divergent path needed 123 vs 454 stage-b
        trials to the same clean endpoint.  The effect can swing either
        way per design; the contract (strict true-metric improvement per
        commit) holds on every path.

Follow-on levers, per the Phase 0 profile: early-abort budgets in the trial
solve (the corner+repair tail outweighs the fixpoint — biggest effect on the
~95% rejected trials), then the fixed-context single-bundle screen
(`LayerSolver::repack_members`) as the headline multiplier.

## RR round 3 — early-abort study + the sound stage-b place-abort (2026-07-14)

The abort lever was DESIGNED as two halves and measured before building
(bigHalf, `no_fast_trials`, per-trial `overlaps_post_fixpoint` vs final):

- **Stage-a post-fixpoint abort: REJECTED — and the study reframes the
  Phase-0 profile.**  Every one of the 16 stage-a trials sat far above the
  metric post-fixpoint (~23-27 overlaps vs cur 2-3), and repair+corner then
  took them to ~2: the "safety net" passes are the PRIMARY overlap
  reducers, not a tail.  5/16 trials — including every actual accept
  (3->2, 2->0) — would have been spuriously rejected by a post-fixpoint
  abort.  Consequences: no abort there, and no future pass-skipping of
  repair/corner either; the per-trial NUTS solve can only be attacked by
  the fixed-context screen.
- **Stage-b place-abort: SHIPPED (sound, zero spuriousness).**  287/373
  rejected trials were certain rejections on opens alone (median excess 76
  over cur), and `num_unplaced` is non-decreasing through place and cull —
  so `DetailedNUTSEngine::run(…, abort_unplaced=cur_opens)` stops placing
  the moment the running count exceeds the committed metric's opens
  (result.aborted, post-place passes skipped).  Armed only for fast
  trials; commits re-run full with the abort disarmed.  Trajectory
  byte-identical by construction (aborted trials were already rejections);
  bigHalf confirms identical moves/trials/endpoint, dnuts place 2.59->2.02s
  + bit_spans 0.49->0.33s on the fast config (modest there because #290
  already cut the trial count; scales with trial count elsewhere).

Remaining round-3 lever: the fixed-context single-bundle screen — now
confirmed as the ONLY way at the per-trial NUTS solve, since its passes are
all load-bearing.  (Shipped below.)

## RR round 3 — fixed-context single-bundle screen — ✅ SHIPPED (2026-07-15)

The round's final lever, in the screen-then-confirm shape the round-2
two-tier revert pointed to.  **Mechanism:** the bottom-up fixed-segment
machinery, not a new solver entry — `NUTSEngine::add_fixed_segments_except
(baseline, exclude_bid)` freezes every other bundle's placed TrackSegments
as immovable occupancy (one C++ call; the baseline already carries any
bottom-up copies), `set_skip_doglegs` keeps the screen read-only (no
topology surgery on a discarded result), and `run([target_wrapper])` places
ONLY the target's segments against the frozen context (`build_context` over
a single wrapper; same-bundle metric exemption means the target cannot
conflict with itself).  Screen score = the result's `(num_overlaps,
num_violations)`: the fixed-fixed component is a CONSTANT within one
contender's scan, so the score is a valid ORDERING — never a metric.

**Wiring (`_rr_screen_scores` / `_rr_screen_prune`, `src/buda_session/
ripup.py`):** per contender, pin each idx alternate + `replan_bundle` for
its layers + screen (~10 ms measured, replan included), keep the
`_RR_SCREEN_TOP_N = 2` best-screened for full trials, DEFER the rest.  A
stalled iteration sweeps the deferred moves at full fidelity before the
global pass (`_rr_scan_moves`, the extracted inner trial loop, is shared),
so the loop's stop certificate is still a FULL sweep — the two-tier
attempt's failure modes are structurally absent: accepts run on the true
full metric (soundness), and the screen can only postpone a move within an
iteration, never prune it (completeness).  Grid parity on hier sessions:
`_rr_screen_grid` reproduces run()'s fallback-grid derivation (union of ALL
bundles' selected-topology coords when the flat fp + planner grids are
empty) — a single-wrapper run would otherwise derive intervals from the
target alone.  Screening falls back to the unscreened order when
`replan_bundle`'s preconditions fail; the global-occupant pass is never
screened (already budget-bounded).  Timing rides the summary as a
`screen s/N` bucket.

**Measured (this host, screen vs `no_screen`, endpoints identical — all
flows reach their clean 0/0):**

    bigHalf (rr 30):  flow 40.8s -> 16.5s.  Stage b is the story:
      full trials 123 -> 11 (nuts 21.1s/131 -> 2.6s/17, dnuts 5.3 -> 0.9)
      + 75 screens at 0.74s total; stage-b ripup 28.9s -> 4.9s.  Stage a
      modest (5.8s/25 -> 3.7s/27 solves + 1.4s/131 screens; 26 vs 23
      trials — deferred sweeps fired).  Entry metric of stage b differs
      (184 vs 257 opens): a trajectory effect of stage a committing a
      different improving move first, exactly the #290 class.
    big2:            ripup 0.91s -> 0.43s (17 -> 3 trials), same 2->0.
    mix:             stage a 7 -> 3 trials; stage b 2.47s -> 1.49s
                     (46(3) -> 0/0 both, 3 vs 4 moves).
    slowdown_rnr:    same design/endpoints; stage b 2.45s -> 1.64s.

**Default ON** (the measurement-decides-the-default pattern); `no_screen` /
`screen` tokens override per run.  Tests: frozen-context verbatim +
reproducibility + no-surgery-export (engine), screen-vs-no_screen endpoint
A/B (both stages), an ADVERSARIAL screen (defers everything) that must
still reach the clean endpoint through the stall sweep, prune ordering /
tie discipline, target-state restore exactness, and the hier grid-fallback
parity.

**Deliberately NOT taken:** a screened-score threshold against the current
metric (skip "hopeless" full trials outright) — that converts the screen
from an ordering into a decision-maker, reintroducing the two-tier
attempt's spurious-rejection mode for a saving the deferred-sweep design
already captures.  Stage b's remaining floor is the per-trial full DNUTS
(#291's place-abort already trims certain rejections); the fixed-bits
incremental DNUTS (`add_fixed_bits`, the bottom-up ref/rest split) remains
the next candidate if that floor ever matters.  One more noted lever
(review #293): stage-b screens rank by ABSTRACT (overlaps, violations)
only — a candidate's DNUTS-open potential is invisible, so the stage-b
ranking is a proxy (protected by deferral; 11 full trials still found all
6 bigHalf stage-b commits).  If a future corpus shows stage-b screens
mis-ordering badly, a cheap opens-proxy (signal-track supply vs need per
placed window) is the shape to add.

## RR round 4 — Phase 0: warm-start single-bundle re-solve fidelity study (2026-07-15)

After #293 the remaining RR cost is SEMANTIC: full cold solves for trials,
certificates, and negotiate (the #291 abort study showed every pass inside
them is load-bearing, so nothing may be skipped).  The round-4 hypothesis:
replace the from-scratch orientation fixpoint with a WARM SEED — place only
the moved bundle against the baseline frozen (#293's screen), then unfreeze
and run the real safety passes (settle_spans / repair_overlaps /
resolve_corner_overlaps / tighten) on the union so neighbours adjust — and
the passes' work scales with the move's blast radius instead of the design.

**Prototype:** `NUTSEngine::rerun_bundle_warm(prev, bundles, target_bid)`
(const; scratch engine copy carries the frozen zones; doglegs skipped — a
warm state must never export topology surgery; `build_context(prep=false)`
because the trunk-margin interval shrink is NOT idempotent and both sides'
segments are already prepped).  The warm metric is EXACT for the warm
placement but the placement differs from a cold run()'s — it is a
PREDICTOR of the cold metric, never a substitute.

**Study harness** (`BUDA_RR_WARM_STUDY=1`, `_rr_warm_study_sample/report`):
every cold ripup trial ALSO runs the warm re-solve from the same baseline
(cold still drives — trajectories byte-identical with the study off) and
records metric exactness, accept-decision agreement vs the committed
metric, false-accepts (warm improves, cold doesn't — cheap: a
verify-on-accept cold run rejects them), and FALSE-REJECTS (warm misses a
cold improvement — the two-tier killer).  Run with `no_fast_trials` so the
cold reference is the exact full metric.

**Measured (this host, screen on, no_fast_trials):**

    mix / slowdown_rnr:  a 3+3 trials, b 8+8 — accept agreement 22/22,
                         0 FA, 0 FR; warm ~2x cheaper (14-19ms vs 28-38ms)
    big2:                a 3 trials — 2/3 agree, 1 FA, 0 FR (25 vs 31ms)
    bigHalf (rr 30):     a 7 trials — 4/7 agree, 3 FA, 0 FR;
                           warm 29.4ms vs cold 175.3ms (6x)
                         b 360 trials — 328/360 agree (91%), 28 FA,
                           4 FR (1.1%); warm 66.6ms vs cold 304.0ms
                           (4.6x; the warm 66ms includes a FULL DNUTS —
                           the production wiring arms the #291 abort)

**Decision: GO for Phase 1.**  The error structure matches the design:
warm is systematically OPTIMISTIC (it can find packings the cold fixpoint
doesn't), so errors skew to false-accepts, which verify-on-accept absorbs
at one cold trial each; false-rejects are 4/392 overall and are made
endpoint-harmless by a COLD certificate sweep at the stall point — the
same deferral architecture #293 proved (the loop still stops only after a
full COLD sweep finds nothing).  The multiplier grows with design size,
confirming the blast-radius hypothesis.  Phase-1 shape: warm as a
PRE-FILTER inside the move scan (screen orders -> warm evaluates ->
only warm-improving moves run the existing cold trial + commit path),
stall triggers the cold re-sweep before the global pass; knob with a
measurement-decided default.  Fidelity caveats recorded: study trials
sampled post-cold-adoption wrapper state (doglegs; rare), and bottom-up
sessions are out of the study's corpus.

## RR round 4 — Phase 1: warm trials wired; default OFF (honest wash, 2026-07-15)

The wiring shipped exactly in the Phase-0 shape (`_rr_warm_eval` +
`warm_rej` collection in `_rr_scan_moves`, the warm-stall certificate
sweep in `_ripup_reroute` before the global pass, `warm_trials` /
`no_warm_trials` tokens, a `warm s/N` timing bucket) — soundness and
completeness are structural: accepts run on the true cold metric, and the
stop certificate remains a full cold sweep whatever the predictor does
(pinned by the adversarial reject-all test).

**But the production A/B came back a WASH, and the default is OFF:**

    bigHalf (rr 30):  12.29s warm vs 12.73s no_warm — cold trials
                      26->9 (a) and 11->8 (b), but 26+11 warm evals at
                      ~41-70ms replaced them ~1:1 in cost (stage b
                      slightly NEGATIVE: 2.94s vs 2.78s of solves).
    mix:              wash (0.41+1.19 vs 0.39+1.13).
    big2:             +0.09s with warm on (0.38 vs 0.29).

**Why Phase 0's 4.6-6x didn't materialize:** the study measured per-SOLVE
cost against `no_fast_trials` cold solves (175-304ms) on a 360-trial
distribution.  Production runs sit BEHIND the #293 screen (trial volume
already near-minimum — 11 stage-b cold trials) and fast trials (cold
~111ms) — so there is little left for the pre-filter to eat, every
warm-accepted move pays warm+cold, and a ~41-70ms warm eval is only ~2.7x
cheaper than what it replaces.  The screen and the warm filter target the
SAME waste (rejected cold trials); the screen gets there first at ~10ms.

**Kept:** `rerun_bundle_warm` (the engine entry has standalone value and
exact-for-warm-state metrics), the study harness (`BUDA_RR_WARM_STUDY=1`),
and the opt-in wiring (zero default-route risk; every existing flow is
byte-identical with the default off).  **The bar for flipping the
default:** a corpus where post-screen cold-trial cost dominates — roughly
a cold trial >=3x the warm eval, i.e. designs several times bigHalf's
size, or stall-sweep-heavy flows.  Measure with the study harness first;
the wiring is already there.

## RR round 5 — batched screen (2026-07-15): the cost was the COPY, not the plumbing

Post-#296 the screen's ~8-10ms/candidate was the largest trimmable RR
bucket.  Three hypotheses were implemented and MEASURED in sequence —
recording the chain because the first two, though "obviously" right, were
each only a sliver:

1. *pybind conversion dominates* (one wrapper-list conversion per
   `replan_bundle` call + a ~600-segment result copy per screen) →
   `NUTSEngine::screen_candidates` batches a whole contender into one C++
   call returning (tidx, overlaps, violations) triples.  Measured: ~8ms/
   screen UNCHANGED.  (The conversion is real — ~5.6ms per full-list
   crossing — but amortizing it over ~7 candidates only shaves ~0.8ms
   each.)
2. *replan_bundle's O(all-bundles) committed-usage recharge dominates* →
   `CongestionPlanner::replan_candidates`: recharge ONCE, plan each pinned
   candidate UNCOMMITTED — provably the same others-only usage every
   separate replan_bundle call saw (each recharged away its predecessor's
   commit); parity pinned by test.  Measured: still ~7.7ms/screen (the
   ladder is only ~0.6-1.5ms).
3. *Instrument the un-bucketed tail* (new pass_seconds buckets: grid /
   bundle_copy / junctions / keepout_audit / report — observability that
   outlives this round): the wall was in run()'s FIRST LINE —
   `std::vector<BundleWrapper> bundles = bundles_in;`, the mutable deep
   copy (every candidate topology of every wrapper) that exists solely for
   dogleg surgery... which screen mode SKIPS.  ~2.5ms C++-side per screen
   run, plus matching allocation/teardown.  Fix: skip_doglegs_ solves
   straight from the caller's list (solve takes const&; the fallback is
   the only mutator).

**Measured (this host):** screen 8.9 → 3.25ms/candidate (2.7x; scores
byte-identical, `rows equal: True`); bigHalf rr-enabled screen bucket
1.58s → 0.65s (131+75 screens), big2 ripup 0.29 → 0.22s, mix stage-b
1.16 → 1.03s — identical trajectories and endpoints everywhere (the
batched path is an exact-equivalence refactor, not a heuristic change).
The residual ~3ms = ladder ~1.5 + target placement + metrics over the
frozen context; further trimming would attack run()'s per-call context
rebuild — not worth it while the screen bucket sits at ~5% of the rr
stages.  Warm evals (`rerun_bundle_warm`, opt-in) inherit the copy skip
for free (their scratch engine is skip_doglegs).
