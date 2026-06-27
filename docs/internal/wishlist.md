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

## 3. Planner layer-assignment instability (input-sensitive tie-break)

**What:** `run_planner`'s per-segment layer assignment for a bundle can differ
between two runs whose `.buda` input is **byte-identical up to and including
`run_planner`**. Observed with `flow/big_data_test/big2_b4_b24.buda` bundle 2
(pinned `TRUNK_V@x5485`): the prefix through `run_planner` assigns `[V→M5 H→M4
H→M4]`, while the full file (same prefix + trailing `run_nuts` / checks) assigns
`[V→M7 H→M6 H→M6]` — same widths, same selected candidate, different metal. It is
deterministic per file but sensitive to content that executes *after*
`run_planner`, which points at an allocation-order / `unordered_*` iteration /
floating-point-tie dependence in the layer cost comparison rather than true
randomness.

**Why it matters:** when several same-direction layers tie on cost, the chosen
metal (and therefore congestion distribution and `dump_topologies --conn`'s
reported layer) is not stable across otherwise-equivalent flows. Found while
addressing the PR #66 review; the dump change itself is correct (it faithfully
reports whatever `plan.seg_layers` holds).

**Where to start:** `src/congestion_planner.cpp` `plan_bundle` layer-selection
loop (the `best_s`/`best_lid` comparison ~`:604`). Audit the tie-break: make it a
total order on a stable key (layer id, then deterministic cost) so equal-cost
layers resolve identically regardless of map iteration / heap layout. Repro by
diffing the `[Planner] Bundle … → topo …` line between the bare prefix
(`sed -n '1,/^run_planner$/p'`) and the full file.
