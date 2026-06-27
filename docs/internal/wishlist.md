# Wishlist / deferred follow-ups

Tracked-but-not-yet-done items. Each entry: what, why deferred, where to start.

## 1. Planner coverage gate (defense-in-depth)

**What:** In `CongestionPlanner::plan_bundle` (`src/congestion_planner.cpp`, the
per-candidate loop ~`:595-645`), demote a candidate to `topo_infeasible` when it
leaves a bundle block with no busterm/pass-through — alongside the existing
overflow gate. Reuse the block-coverage predicate from `verify.cpp`'s
`check_topo` (factor a shared helper if needed). Keep it conservative: the gate
only demotes uncovered candidates, and the existing escalation ladder
(`ALLOW_OVERFLOW` / `BEST_EFFORT`) still commits one with a WARNING if *every*
candidate is uncovered, so it can never strand a bundle.

**Why deferred:** The generation-side fix in PR #65 (coverage-safe stub
suppression in `add_trunk_v`) already eliminates the selected-topology coverage
bug it was meant to backstop. A global selection-behaviour change carries
regression risk in the already-over-congested `big2.buda`, for no current
benefit — so it's pure belt-and-suspenders against a *future* generator emitting
an uncovered candidate.

**Where to start:** `src/congestion_planner.cpp` plan_bundle; `src/verify.cpp`
check_topo coverage logic. Verify with `big2.buda` (no congestion regression) +
the full test suite.

## 2. Pre-existing failure: `test_tighten_does_not_trade_pull_for_overlaps`

**What:** This `mid`-tier test (`test/tests/test_nuts_pull_repack.py`) asserts the
`tc3a_flat` NUTS solve leaves `<= 2` abstract M7 overlaps, but it currently
produces **3** (`B59×B74 ×2`, `B56×B79`). The test is red on `main`.

**Why deferred:** Confirmed unrelated to the PR #65 fixes — it fails identically
on `main` with the fixes stashed (same 3 overlaps). It's a drift in the
`tc3a_flat` design / `tighten_pulls` group-move heuristic that predates this work.
Excluded from the default fast tier, so it doesn't gate `pytest`.

**Where to start:** Decide whether the 3rd overlap is a genuine `tighten_pulls`
regression to fix or an acceptable congestion artifact (then update the guard
threshold with justification). `git bisect` on the overlap count for `tc3a_flat`
would pinpoint when 2 → 3.
