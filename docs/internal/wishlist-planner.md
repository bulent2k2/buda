# Wishlist — Congestion planner

Deferred follow-ups for the bundle / congestion planner
(`src/congestion_planner.cpp`, `src/layering.cpp`). Index:
[`wishlist.md`](wishlist.md).

## Planner coverage gate (defense-in-depth)

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

## Model band capacity in signal-track count, not layout width (Gap A part 2) — ✅ IMPLEMENTED

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

## Planner "layer-assignment instability" — ✅ RESOLVED (not a bug)

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
