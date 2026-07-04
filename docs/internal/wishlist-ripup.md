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

5. **Tiny synthetic stage-b (DNUTS-open) canned fixture. — ✅ RESOLVED.**
   `_build_dnuts_open_session` (`test/tests/test_ripup_reroute.py`): an
   all-POWER `add_grid_override` corridor kills M4's signal tracks exactly
   under the pinned `L_HV` trunk's Hanan window, so DetailedNUTS
   deterministically opens all 8 bits (pattern arithmetic, no packing ties —
   CPU-invariant) while the alternate `L_VH` trunk runs through healthy
   pattern.  Fast-tier tests cover the fixture itself, stage-b ripup clearing
   it (one trial), and stage-b negotiation clearing it by pure cost.
