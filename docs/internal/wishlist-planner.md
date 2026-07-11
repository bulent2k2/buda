# Wishlist — Congestion planner

Deferred follow-ups for the bundle / congestion planner
(`src/congestion_planner.cpp`, `src/layering.cpp`). Index:
[`wishlist.md`](wishlist.md).

## LOW-layer abutment crossings are guaranteed DNUTS opens (big2's 72 open bits) — ✅ RESOLVED

**What (history):** big2's only remaining DNUTS opens (72 bits, 2026-07
baseline with `run_planner signal_tracks` + negotiation) were two
single-segment **abutment-crossing** bundles (`bus_042`/32b, `bus_002`/40b):
a ~20-unit V stub crossing a shared block edge, which the planner assigned
to **LOW layer M3**.  On a LOW layer the two leaf blocks' footprints are
keepouts, and an abutment crossing's slide window lies *entirely inside
those footprints* by construction — so DetailedNUTS finds ZERO unblocked
signal tracks and strands every bit, while abstract NUTS reports the
placement clean (overlaps=0, violations=0).  `negotiate_congestion` after
DNUTS does not recover it (the band genuinely has no supply on any LOW
layer).

**Resolution (in `low_seg_obstructed`, the Gap A predicate):** the root
cause was a blind spot in the existing obstruction test.  It trims the two
endpoint pin-access tails back to their cell faces and treated an *empty
open interior* (`tlo >= thi`) as "tails meet in a gap: nothing left to
route" → routable.  For an abutment crossing the tails meet because the two
cells **share an edge** — no gap, no open-channel point anywhere on the
span.  The fix: an empty interior between two DISTINCT endpoint cells
(their faces abut or overlap at this perp) is flagged obstructed, so
`score_segment` returns the hard 9999 and STRICT layer selection routes the
crossing over-the-cell on TOP.  A single endpoint tail meeting the far end,
or tails meeting across a real gap, stay routable exactly as before.
Measured: big2 full flow 0 overlaps / **0 DNUTS opens** (was 0/72), ~1 s
total; both golden corpora byte-identical (no other corpus flow has an
abutment LOW assignment).

**Tests:** `test/tests/test_big2_residuals.py::
test_low_layer_abutment_stub_planner_avoids_low` (planner-side guarantee:
TOP layer chosen, all 72 bits place) and `…_dnuts_open_repro` (the
DNUTS-side mechanism, kept alive by pinning M3 manually).

**Follow-up — ✅ RESOLVED:** the abstract/detailed keepout-model mismatch
noted here was audited and closed (span-aware DNUTS track pools + final-span
crossing cull + abstract `num_keepout_conflicts` report channel + empty-
`layer_ids` unification). See
[`keepout_model_audit.md`](keepout_model_audit.md) and
[`wishlist-nuts.md`](wishlist-nuts.md). The planner's own band sampling at
cut coordinates remains a point-sample approximation (audit class 5) — a
cost misestimate now surfaced downstream as honest DNUTS opens rather than
silent illegal wires; full span-aware band accounting is deferred.

## Planner coverage gate (defense-in-depth) — ✅ RESOLVED (superseded by the generation-time gate)

**Resolution:** implemented at **topology generation** instead of in the planner,
keeping `run_planner` focused on capacity/congestion.
`TopologyGenerator::filter_uncovered` (`src/topology.cpp`) runs at the tail of
`generate_candidates` — one uniform gate over every generation path (2-pin,
trunk, MST, BITRUNK) and every caller (flat/hier CLI, direct API). Per
candidate it runs verify's `check_topo` and drops two silent-open risks the
planner cannot detect: `BUSTERM_OPEN` (a `connected_block_names` block with no
busterm tap and no pass-through) is **always** dropped; `FEEDTHRU_RELAY` (the
legacy multi-rect / rootless trunk+MST fallback whose incident wires do not
physically touch — a silent feedthru no downstream stage catches, PR #194) is
dropped **only when a clean candidate — neither open nor relay — survives**.
Drops are printed, never silent. A **never-strand** fallback keeps the whole
list (with a WARNING) when *every* candidate is uncovered, or when a bundle's
only options are relays, so the planner's `ALLOW_OVERFLOW`/`BEST_EFFORT` ladder
still commits one (relay-only bundles stay flagged for `check_connectivity`).
Tests: `test/tests/test_topo_coverage_filter.py`; regression: full fast + mid
tiers, wl_corpus byte-identical (relays are not selected in the corpus, so the
drop changes no route).

**Residual gaps (tracked elsewhere, not planner concerns):** a block missing
from `topo.connected_block_names` is invisible to any coverage check — that is
the CONVERGENT list-fidelity gap in
[`wishlist-bundler.md`](wishlist-bundler.md); post-NUTS slide drift remains
`check_nuts`'s job.

The original proposal (kept for the record): demote uncovered candidates to
`topo_infeasible` inside `CongestionPlanner::plan_bundle`, deferred because the
PR #65 generation-side fix had already removed the live bug and a global
selection-behaviour change carried `big2.buda` regression risk. The
generation-time gate delivers the same backstop without touching planner
selection semantics.

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

## Selection basis: rank on measured routability, not the generation-time WL estimate — LEVERS 1+2 SHIPPED; `kPeak` default DECIDED (stays opt-in)

**Lever 1 as-built (2026-07-10):** `set_planner_param kPeak <w>` adds a
peak-EXISTING-band-utilization term to the per-segment soft cost
(`CongestionPlanner::peak_util_segment`): the max `usage/cap` over the
bands a segment would use, **pre-charge** — post-charge utilization was
measured and rejected (on an uncongested design it degenerates into an
intrinsic "narrow channel" penalty that biases against exactly the column
channels the BITRUNK trees use; datapath WL regressed across the sweep).
The same term joins the slide-window band choice (`best_band_perp`) — the
Codex #252 review caught that pricing a band the legacy nearest-band
tie-break had already chosen steers nothing within the window; with the
fix the charge itself (and NUTS's `seg_perp`) moves to the emptier band.
Default `0` skips the term entirely (existing flows bit-identical; goldens
guard it). Measured at `kPeak 0.1` on the congested corpus:
**channel_stress heals its 3 keepout-open bits at zero overlap cost**,
**rnr/mix DNUTS unplaced 190→128 (−33%)**, tc3a_flat stays clean
(0.05–0.1). **Why not default-on:** big2 (signal_tracks + heavy
negotiate/ripup healing) prefers the knob off — 5 NUTS overlaps / 0 opens
baseline vs 6 / ~110 at every tested value — the term and the feedback
loops double-steer untuned. Tests: `test/tests/test_planner_kpeak.py`
(loaded-corridor steering repro + off-is-off + param recognition); docs:
`docs/script_reference/planner.md`.

**Lever 2 as-built (2026-07-11):** the QoR-measured re-rank lives in
`ripup_reroute`'s trial-pool builder (`_rr_candidate_order`,
`src/buda_session/ripup.py`). Candidates are WL-sorted
(`annotate_and_sort`), and the pool used to be `range(min(n, 8))` — the 8
cheapest estimates only, so on a fan-out trunk bundle (35–45 candidates,
OOB trunks / BITRUNK trees at indices 8–40) no measured contention could
ever promote a higher-estimate class: the trial that would test it was
structurally unreachable (verified on `flow/datapath_multi_trunk.buda`:
every bundle's two-level trees sit beyond the first-8 window). Now, when
the contender has measured contention sites, the top-8 farness-ranked
candidates from BEYOND the first-8 window are APPENDED after the legacy
pool — whenever a cheap alternate improves, the first-improving scan
commits the same move as before (routes unchanged; goldens byte-identical),
and the expensive classes are trialed exactly when every cheap alternate
fails, acceptance still gated by the measured (opens, overlaps) metric.
Farness-first over the WHOLE pool was measured and rejected: it commits a
far expensive candidate before a cheap same-effect one (mix.buda bundle 85:
idx 26 over idx 5, +2% abstract WL at an equal metric). Measured on big2
(`tc3b_flat_x5`): stage-a residual overlaps after ripup 1→0 (bundle 27
promoted to index 11, unreachable before), abstract WL −0.18%, detailed WL
+0.04%, stage-b endpoint unchanged (0/0). `negotiate_congestion` needs no
companion change — its `replan_bundle` re-plans UNPINNED, scoring ALL
candidates through the cost model, so every class was already reachable
there (and lever 1's `kPeak` is the knob that biases that model toward
routability). Tests: `test/tests/test_ripup_class_rerank.py` (pool
composition: legacy-first, beyond-cap extras, farness ranking, self-trial
exclusion).

**`kPeak` default decision (2026-07-11): stays opt-in (default 0).**
The big2 "double-steer" hypothesis was debugged and REJECTED — big2_noviz
runs the PLAIN pipeline (no negotiate/ripup at all), so the regression is
pure planner steering meeting DNUTS supply. Measured on top of lever 2:
- The damage is concentrated and structural: at every tested value
  (0.05/0.1/0.2) exactly two wide trunk segments (bundle 23 seg 1, 60
  bits; bundle 25 seg 0, 56 bits) are steered onto M4 windows with
  near-zero real signal-track supply and strand completely (0 bits
  placed, ~104–116 opens vs 0 baseline). `peak_util` prices *relative*
  existing load (`usage/cap`), so an almost-supply-free band at
  utilization 0 looks maximally attractive — the term is blind to
  *absolute* supply.
- NOT a width-model artifact: `run_planner signal_tracks` + kPeak 0.1
  shows the identical 6 overlaps / 116 opens.
- The feedback loops fully heal it: big2 + kPeak 0.1 +
  `negotiate_congestion` + `ripup_reroute` reaches 0 overlaps / 0 opens —
  the same endpoint as the baseline healed run. So the knob is safe (and
  useful) in flows that run the loops; a *default* must not depend on
  them.
- The best value is flow-dependent and non-monotonic: mix optimum is 0.1
  (190→128 unplaced, −33%; 0.2 backslides to 169), channel_stress heals
  at 0.1, tc3a clean at 0.05–0.1.
**Reopener (the follow-on lever):** a supply-aware `peak_util` — treat a
band whose absolute signal-track supply within the segment's span cannot
host the bundle's bit count as fully utilized (util ≥ 1) instead of
attractive-empty. That removes the big2 failure mode at its root and
would justify re-running this decision. The original analysis follows.

**What.** The planner selects one candidate per bundle by a cost model
(`kCong·congestion + kSpan·span + base_cost_non_top + kWL·wirelength`, see
`set_planner_param`) whose wirelength term is the candidate's **generation-time
`estimated_wirelength`** — computed before NUTS runs. So a candidate whose honest
estimate is *longer* but whose **routed** QoR (track usage, overlaps, DNUTS opens)
is *better* structurally loses. The clearest instance is the two-level datapath
tree (`BITRUNK_HVH/VHV`, opt-in `multi_trunk`): on a two-column fan-out it costs
~two branch trunks + a root spine, so its estimate legitimately exceeds a single
`TRUNK+MST`, and the planner ranks it below and won't pick it even where the tree
would relieve the column congestion that a trunk+MST piles onto one band.

**Measured (this session, BITRUNK margin/slide investigation — no code kept).**
The suspected levers were *not* the cause: corner margins are inert on the corpus
(no `corner_margin` is set), the branch/leaf slide window already exists
(`min_slide≈80`), and a rendered DNUTS-unplaced case traced to **signal-track
supply**, not candidate flexibility. The selection loss is **structural in the
cost model**, not a margin/slide bug — which is exactly why `multi_trunk` remains
a correct narrow opt-in (see [`wishlist-topo.md`](wishlist-topo.md) →
"`multi_trunk` as a default — keep opt-in") rather than a default.

**The lever (deferred).** Bias selection by *routed* quality, not just estimated
WL, for the candidate classes that route better than they estimate:
- a **congestion-/track-aware selection term** — e.g. weight a candidate by the
  peak band demand it induces (the planner already knows per-band load), so a tree
  that spreads a column across branch trunks scores better than a spine that
  saturates one band; or
- a **QoR-measured re-rank** — the `ripup_reroute` / `negotiate_congestion`
  machinery already re-plans against *actual* NUTS overlaps and DNUTS opens; let it
  promote a datapath tree when the committed trunk+MST leaves residual column
  contention (it used to only re-pin the 8 cheapest index alternates, never
  up-ranking a higher-estimate class — SHIPPED as lever 2, see as-built above).

**Related, tracked in [`wishlist-topo.md`](wishlist-topo.md):** honest
generation-time **trunk-tail tightening** of `TRUNK+MST` hybrids (their dangling
overshoot inflates the estimate and is a *second* reason the estimate misranks
them), and a **planner-aware flex span** (reserve the contracted trunk extent, not
the wide generated span). Both attack the same "estimate ≠ routed cost" gap from
the generation/NUTS side; this item is the planner-selection side.

**Why deferred / gate.** Any change here flips planner selections, so it ships
only behind the `tools/wl_corpus.py` diff (neutral-or-better on the 10-flow
corpus) **plus** the datapath-QoR flows (`flow/datapath_multi_trunk.buda`,
`flow/datapath_row_vhv.buda`) — a selection bias that helps datapaths must not
regress the neutral corpus. Start at the layer/topology cost compare in
`CongestionPlanner::plan_bundle` (`src/congestion_planner.cpp`) and the
`estimated_wirelength` term feeding it.
