# Wishlist — Rip-up & re-route

Deferred follow-ups for the feedback-driven `ripup_reroute` pass (Python
hill-climb in `src/buda_cli.py`, driving planner + NUTS + DetailedNUTS). Index:
[`wishlist.md`](wishlist.md).

## `ripup_reroute` v1 follow-ups (deferred from the implementing PR)

The `ripup_reroute [max_iter]` command (Python greedy hill-climb in
`src/buda_cli.py`) shipped as a feedback pass that reads the *actual* NUTS
overlaps (stage a) / DNUTS opens (stage b), re-routes a contending bundle to an
alternate candidate, re-runs the pipeline, and keeps moves that reduce the
metric. Validated on big2 (stage a 9→0, stage b 60→0). The following were
explicitly out of scope for v1.

1. **C++ band-injection rip-up (principled engine version).** Instead of the
   Python loop re-running the whole pipeline per trial, drive the planner's
   existing escalation ladder (STRICT → rip-up → ALLOW_OVERFLOW → BEST_EFFORT,
   `src/congestion_planner.cpp:~914-1022`) directly from the *measured*
   NUTS/DNUTS overlaps — inject the real contention as demand on the failing
   bands so `commit_plan(bw, plan, -1.0)` rips up the actual blocker. Needs a
   public band-injection / overlap-feedback hook on `CongestionPlanner` (none
   exists today; the planner is rebuilt each `run_planner`). *Why deferred:* the
   Python path is validated and additive; the C++ version is a larger
   re-architecture. *Where to start:* `congestion_planner.{h,cpp}` escalation
   ladder + `plan_band_overlap` victim ranking; feed it `nuts_result.overlap_details`.

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

4. **"Only-try-relevant-candidates" speedup.** v1 trials every alternate
   candidate (capped at `_RR_MAX_CANDIDATES_PER_BUNDLE`), each a full pipeline
   re-run — O(candidates × contenders × iters). A filter that only trials
   candidates which move the contended segment off the congested layer/band would
   prune most trials. *Where to start:* `_rr_contenders` / `_rr_trial` in
   `src/buda_cli.py`; use the overlap's `layer`/`perp` to pre-filter candidates.

5. **Tiny synthetic stage-b (DNUTS-open) canned fixture.** Stage b is currently
   covered only by the big2 `@mid` integration test (60→0); a deterministic tiny
   floorplan that forces a DNUTS open (insufficient signal tracks in a shared
   band via `def_track_pattern`) would give a fast-tier unit test. The canned
   fixture proved hard to make deterministic for stage b in v1. *Where to start:*
   `test/tests/test_ripup_reroute.py` `_build_session`; model the track-pattern /
   unplaced setup on `test/tests/test_detailed_nuts.py`.
