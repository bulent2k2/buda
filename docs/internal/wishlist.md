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

## Gap A part 2: model band capacity in signal-track count, not layout width — ✅ IMPLEMENTED

**Shipped** as the opt-in `run_planner [hier] N signal_tracks` keyword. The planner
is handed the `RoutingGridStack` (`set_routing_grid` / `set_capacity_mode`) and, on
patterned layers, `usable_band_cap` counts the discrete SIGNAL tracks in the band
(clamped to the slide window, honouring grid keepouts) × the layer's bit pitch — so
`nbits·bit_pitch ≤ ntrk·bit_pitch` reduces to the exact integer test `nbits ≤ ntrk`.
A band whose width fit but whose track count is short now reports overflow at plan
time and engages the STRICT rip-up/replan ladder. Opt-in (default WIDTH path is
byte-identical); `set_planner_param track_cap_slack` adds quantization slack.
Validated on `flow/rnr/mix.buda`: width plan 236 DNUTS opens → `signal_tracks` plan
**162** with no ripup. Design + rationale: `docs/internal/planner_signal_track_capacity.md`;
reference: `docs/BUDA_SCRIPT_REFERENCE.md` (`run_planner` → Signal-track band capacity).
The original analysis follows.

**What:** The bundle planner models a Hanan-band's capacity as available *layout
width* (`band_available_length` in `src/congestion_planner.cpp` — geometric
distance minus keepouts), but DetailedNUTS places bits on discrete *signal
tracks* drawn from the layer's `TrackPattern` (power/ground/clock slots are not
SIGNAL, so the usable count is a fraction of the width). At a contended interval
the binding constraint is the per-track signal count, not the width. So the
planner can commit a bundle `overflow=0` while DNUTS finds the interval short of
signal tracks → a silent open. Switch the planner's band capacity (and
`eff_bus_width` charge) to **signal-track-count units** at the segment's actual
interval, so over-subscription surfaces as `overflow` at planning time and
engages the existing STRICT rip-up/replan ladder instead of failing at DNUTS.

**Evidence (big2, after Gap A part 1):** 272 residual unplaced bits, all TOP
`reservation conflict`. Of the 7 failing bundles, 3 (bundles 10, 14, 37 — the
small 8/36/8-bit needs) have **no NUTS overlap at all** — NUTS's abstract-width
footprint fit, but the discrete signal-track count fell short. That is the pure
units-mismatch signature this item addresses. The other 4 (23, 25, 27, 45) also
overlap at NUTS, i.e. genuine over-capacity the width model under-prices.

**Why deferred:** The user asked to first resolve the **NUTS-stage overlaps** by
improving the planner (the 41 TOP-layer overlaps that DNUTS opens partly
correlate with). Capacity-unit conversion is the second TOP-layer lever and
should follow, since it changes the planner's overflow accounting globally and
wants the NUTS-overlap work settled first to read its effect cleanly.

**Where to start:** `band_available_length` / `usable_band_cap` /
`LayerStack::eff_bus_width` (`src/congestion_planner.cpp`, `src/layering.cpp`),
and how `RoutingGrid`/`TrackPattern` signal density (`signal_density`,
`dilution_factor`) is consulted. Verify on `flow/big_data_test/big2/big2.buda`:
the 3 NUTS-clean DNUTS opens (bundles 10/14/37) should become planner `overflow`
warnings (then rip-up/replan), and total unplaced should drop. See
`docs/internal/planner_low_layer_over_cell.md` for the full Gap A/C breakdown.

**Plan written:** the detailed implementation plan lives in
`docs/internal/planner_signal_track_capacity.md` — shipped as an opt-in
`run_planner [hier] N signal_tracks` keyword (the planner is handed the
`RoutingGridStack` and charges band capacity in signal-track count / demand in
bit count on patterned layers). New evidence from `flow/rnr/mix.buda`: stage-a
ripup drives 21→0 overlaps but leaves **150 DNUTS opens at `overflow==0`** — the
exact capacity-mismatch class this item targets.

## NUTS band-level repack for spread-fit overlap clusters

**What:** After Gap A part 1 + TOP-layer load balancing, big2 is down to 9 NUTS
track overlaps. All 9 are **spread-fit** (the shared Hanan band has room for both
buses — sum of widths <= interval), i.e. pure placement clustering, not
over-capacity. They survive because `NUTSEngine::repair_overlaps`
(`src/nuts.cpp`) only relocates ONE victim per overlap into a gap its own
interval still has free; when a cluster of 3+ buses share a band (e.g. on big2 M7
bundle B79 collides with B65, B26 AND B45) no single-victim move separates them,
and a plateau-move relaxation of the strict-improvement guard was tried and did
nothing (the victims' intervals are already full given the others' positions).

The fix is a **band-level multi-segment repack**: gather the maximal set of
mutually-overlapping segments sharing a (layer, span-overlap, interval-overlap)
cluster and re-distribute all of them at once across the union of their slide
windows (they provably fit — spread-fit), instead of nudging one at a time. The
existing dense `try_repack` (`src/nuts.cpp:~1306`) already packs a member set to
low edges during initial placement; reuse/lift it into `repair_overlaps` as the
cluster resolver.

**Why deferred:** Single-victim repair + the planner load-balancing already took
big2 from 43 -> 9 overlaps; the residual needs a genuinely different (cluster)
algorithm. Out of scope for the current planner-fidelity branch, which is
planner-only.

**Where to start:** `src/nuts.cpp` `repair_overlaps` (~:529) and the dense
`try_repack` lambda (~:1306); `find_overlaps`/`segs_overlap` for cluster
discovery. Verify on `flow/big_data_test/big2/big2.buda`: the 9 residual overlaps
(M4×1, M6×4, M7×3, M2×1 — all spread-fit) should drop toward 0 with no new DNUTS
opens. NOTE: do NOT try to "balance" this away in the planner — evening the V
load (M5 9117 vs M7 5752) was measured to be counter-productive: it pushes load
toward M7 where the overlaps already sit and regressed DNUTS 60 -> 132 with the
overlap count unchanged. The residual is a packer problem, not a load problem.
See `docs/internal/planner_low_layer_over_cell.md`.

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
   bands NUTS/DNUTS later find contended. Already tracked above as **"Gap A part
   2: model band capacity in signal-track count, not layout width"**; resolving
   it would let the planner predict the overflow up front and engage its *own*
   ladder, reducing how often `ripup_reroute` is needed. Cross-referenced here as
   the principled follow-on.

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

## Multi-source (fan-in) topology support to make CONVERGENT bundling sound

**What:** The bundler's `CONVERGENT` strategy groups nets by shared receiver only,
so a bundle can span several drivers (a many-to-one fan-in). Topology generation
models a bundle by a single `src→dst` pair, so such a bundle routes from ONE
arbitrary driver and the other drivers are silently left unrouted — physically
wrong. Give topology generation a **multi-source / fan-in tree** shape (several
source busterms merging toward the shared sink, e.g. an MST/Steiner trunk each
driver joins), and add the missing **net-driver fidelity check** to
`check_connectivity` (today it validates a topology's internal self-consistency,
not that every original net driver is actually attached — which is why the gap
slipped through). Then `run_bundler CONVERGENT` becomes genuinely useful for
real fan-in patterns (multiple masters → one slave, write data → memory) instead
of a foot-gun.

**Why deferred:** No faithful physical representation exists yet; `CONVERGENT`
only matches routing when it degenerates to `STRICT`. Shipped for now: the CLI
honours the `STRICT|CONVERGENT` argument again (was silently ignored) and prints
a warning when `CONVERGENT` is selected, rather than misrouting silently.

**Where to start:** `src/topology.cpp` (single `src→dst` derivation per bundle;
reuse the `trunk_mst` / `compute_mst` machinery in `src/conn_topology.cpp`),
`src/verify.cpp` `check_topo` (add the driver-attachment check), and
`src/bundler.cpp` (`CONVERGENT` signature). Full investigation, evidence, and
verdict: [`convergent_bundling.md`](convergent_bundling.md). Pipeline test that
locks in the current behaviour: `test/tests/test_bundler_convergent_pipeline.py`.
