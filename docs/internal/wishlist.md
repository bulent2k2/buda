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

## 2. Pre-existing failure: `test_tighten_does_not_trade_pull_for_overlaps` — ✅ RESOLVED (PR #69)

**What:** This `mid`-tier test (`test/tests/test_nuts_pull_repack.py`) asserts the
`tc3a_flat` NUTS solve leaves `<= 2` abstract M7 overlaps, but it produced **3**
(`B59×B74 ×2`, `B56×B79`) — red on `main`.

**Resolution:** Fixed by PR #69 (net_pull only pulls endpoint-setting stubs).
Removing the spurious multicast-trunk pulls — and, with the busterm-tap endpoint
check, the interior-stub pulls — changed `tc3a_flat`'s placement enough that it
now lands at `<= 2` abstract M7 overlaps, so the test is green. The root cause was
exactly the over-applied `net_pull` that drove interior/non-binding stubs to their
slide bounds, congesting the layer; it was never a `tighten_pulls` regression.

## 3. Planner "layer-assignment instability" — ✅ RESOLVED (not a bug)

**Claim (investigated):** `run_planner`'s per-segment layer assignment seemed to
differ between two runs with input "byte-identical up to `run_planner`" —
`flow/big_data_test/big2_b4_b24.buda` bundle 2 assigned `[V→M5 H→M4 H→M4]` from a
`sed`-extracted prefix file but `[V→M7 H→M6 H→M6]` from the full file.

**Finding: the planner is fully deterministic — there is no instability.** A
sweep of `congestion_planner.cpp/.h` found no `unordered_*` / pointer-keyed /
hash-ordered containers in the hot path: layer ids come sorted
(`layering.cpp:89`), cuts/bands iterate by index, and the layer-cost compare
(`congestion_planner.cpp`) iterates highest-id-first so ties break toward higher
metal. The full file gives `M7/M6` on every run; the prefix file gives `M5/M4` on
every run — each deterministic.

The difference was a **reproduction artifact**: the `sed` prefix file was written
to the scratchpad, so its relative `source ../tracks4top.buda` (resolved against
the *script's* directory) did not exist. `source` on a missing file used to print
an error and continue, so the run proceeded with **no `def_layer`s**, and
`run_planner` silently defaulted to M4(H)/M5(V) — the `M5/M4` assignment. Placing
the same prefix file *in* `flow/big_data_test/` (where the source resolves) gives
`[V→M7 H→M6 H→M6]`, identical to the full file. Confirmed.

**Hardening shipped** so this class of confusion can't recur: `source` on a
missing file is now a hard error (exit 1, like an unknown command), and
`run_planner` prints a one-shot `[Planner] WARNING` when no H/V layers are defined
before falling back to M4/M5.

---

## Follow-up: true along-flex trunk DOF (Stage C of the flexible-root re-arch)

**Context.** The coverage-driven flexible trunk span (PR on `claude/topo-gen-b4`)
makes a trunk's endpoints span exactly from the lowest busterm it taps to the
topmost stub centerline — minimal, no dead wire — but only **under
`double_detour`**, and the minimisation is computed at GENERATION (stub centerline
+ near-face coverage of pass-through blocks).  A stub's slide still comes only from
its busterm face intersected with the *generated* spine extent; NUTS
`do_span_adjustments` contracts/extends a spine end **only where the extreme
connection sits at the endpoint** (it SETs there) — a stub at a mid/T-junction is
extend-only, and there is no along-direction pull.  So the generated span is the
binding one (we place stubs at centerlines specifically so the extreme stub keeps
a positive slide window), and the behaviour is gated off by default to avoid
disturbing candidate rankings (always-on far-face traversal inflated V-trunk WL
and flipped planner selections).

**Wish.** A first-class **along-flex DOF** so a trunk spine's endpoints are a
*range* resolved by pull, not a fixed generated coordinate:
- Add `along_lo`/`along_hi` *bounds* (+ an `along_pull`) to `ConnSeg`
  (`src/conn_topology.h`), computed in `compute_net_pull` (today perp-only,
  `src/conn_topology.cpp`).
- Teach NUTS `do_span_adjustments` / `tighten_pulls` (`src/nuts.cpp`) to contract a
  spine end toward the pull-optimal coordinate even at a mid-junction, never past a
  busterm-face anchor or a pass-through coverage requirement.

**Payoff.** The flexible-root span could then be **always-on** (not just
`double_detour`): trunks would generate tight, gain slide room from the DOF, and
contract to minimal honest wirelength — eliminating the ranking-inflation that
forced the `double_detour` gate, and letting the planner prefer the region-4
pass-through trunk on its merits. Also unlocks always-on generation of the
"region-4" pass-through trunk (e.g. `TRUNK_V@x5772` in
`flow/big_data_test/big2/b4_bus_077.buda`) instead of only under `double_detour`.
