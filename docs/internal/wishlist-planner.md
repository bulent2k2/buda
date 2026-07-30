# Wishlist — Congestion planner

Deferred follow-ups for the bundle / congestion planner
(`src/congestion_planner.cpp`, `src/layering.cpp`). Index:
[`wishlist.md`](wishlist.md).

## NON-TOP dead-span stub opens: planner-side gate SHIPPED (opt-in) + the discriminator DELIVERED via post-NUTS escalation (default on); residual is a distinct CAPACITY item

**What:** the planner's per-cut capacity (`score_segment` →
`usable_band_cap`) samples a non-TOP stub's endpoint-CLAMPED along-extent
(`for_each_band` treats the in-cell tail as pin access routed on another
layer), so a pin-access stub whose in-cell span sits over a leaf keepout
on a LOW layer passes the width/track check yet is assigned to a band with
too few — often **zero** — signal tracks across the FULL span DetailedNUTS
places from. Result: a guaranteed DNUTS open. Diagnosed on
`flow/big_data_test/bigHalf.buda` (no rr): **10 stub segments**, every one
assigned to M2/M3 with `count_signal_tracks_in_span == 0` while a
same-direction TOP layer (M4/M5/M6/M7) had 100–380 tracks right there. The
same class is flow 10's "M7 is non-TOP creates some opens" note (M7's
above-TOP mis-label, the sibling wishlist item below, is one *source* of a
dead LOW band).

**Shipped (opt-in, this PR):** `set_planner_param nontop_dead_span_gate 1`
— refuse a NON-TOP layer whose abstract span has **0** keepout-clear
signal tracks in the chosen band (the exact `count_signal_tracks_in_span`
pool DNUTS reads, via the factored `span_signal_supply` helper), so STRICT
escalates to a TOP layer that can host the bits. Measured **bigHalf no-rr
DNUTS unplaced 566 → 135 (−76%)**, no new overlaps. Default OFF, so the
whole corpus is bit-identical.

**Why opt-in — the hard part that's still OPEN.** `span_pool == 0` over the
CONSERVATIVE abstract span does NOT distinguish a genuine cull from a
survivor: the abstract span is an overestimate of the final
junction-adjusted bit spans, so bigHalf's stubs (whose bits cannot retract
clear of the keepout → cull → open) and `rnr_mix`'s stubs (whose final
spans DO clear the keepout → place) BOTH read `span_pool == 0` at plan
time. An always-on gate therefore helps bigHalf but regresses rnr_mix's
healed endpoint (0 → 16) by over-escalating survivors onto TOP (also
measured: the midpoint-fallback variant, which mirrors DNUTS admission,
un-fires the useful cases because the planner's wide slide window sees
tracks the narrow final interval won't — bigHalf back to 566). **The
always-on discriminator** is a post-placement-aware predictor: does the
keepout cover the WHOLE routed extent (→ bits can't retract clear → gate)
or only part (→ they can → leave on LOW)? That needs either a
final-span estimate at plan time or a NUTS-side signal, and ties into
opens item 4 (the `do_span_adjustments` span-stretch clamp — the NUTS-side
half of the same bug). **Effort:** medium; the payoff is turning the
opt-in gate always-on cleanly.

**Second flow now blocked on this same discriminator (2026-07-19): the
`rnr/mix` hanan_loci regression.**  Root-causing the mix–loci flip
regression (wishlist-topo "mix–loci interaction ROOT-CAUSED") landed
exactly here: mix's 42 stranded bits (b61 seg1/seg6, b90 seg0) are
segments the STRICT ladder escalates onto LOW layers (M2/M3) whose full
span is keepout-dead, because the richer loci pool crowds the TOP bands.
`nontop_dead_span_gate 1` is the strongest lever (mix loci 42 → 18) but
over-conservative in the same way (+5 ov, and it regresses mix's baseline
0/0 → 0/48).  So the always-on discriminator now has **two** corpus
beneficiaries — bigHalf (−76% no-rr opens) and un-pinning mix from
`no_hanan_loci` — raising its priority.  Measured non-fixes for mix:
`kWLSpread 0.125` regresses it (42 → 48), `kPeak 0.1` only partially helps
(42 → 32).

**The discriminator SIGNAL found — it is FINAL-GEOMETRY, not plan-time
(2026-07-19, measured on `flow/rnr/mix.buda`).**  Two prototypes:
- *Plan-time span discrimination is INSUFFICIENT.*  Gating on the CLAMPED
  routed-extent (corridor between the endpoint cell faces) supply instead
  of the raw span — the natural "does the keepout cover the whole
  corridor" test — STILL regresses mix baseline 0/0 → 0/48: mix's
  SURVIVORS have dead corridors too (`routed_extent` supply == 0), because
  the true final span is shorter still than the corridor.  The root
  circularity: DNUTS admits on the NARROW final interval (span-clear OR
  midpoint pool), while any plan-time gate only has the WIDE slide window
  — so no plan-time span test separates a cull from a survivor.  (The
  earlier midpoint-fallback variant fails the mirror way — over-un-fires.)
- *The FINAL-GEOMETRY (post-NUTS) test is CLEAN.*  Running the exact DNUTS
  admission test — `count_signal_tracks_in_span` (span-clear) OR
  `count_signal_tracks_in` (midpoint) — on each LOW segment's ACTUAL
  placed span + band, after abstract NUTS, fires on **ZERO** LOW segments
  in mix baseline (no survivor false-positives), because the final
  interval IS what DNUTS uses.  This is the concrete path to the always-on
  gate: a **post-NUTS dead-span escalation** — move a genuinely dead LOW
  segment to a TOP layer with supply and re-solve (the `run_planner
  post_nuts` insertion class, `nutsflow.py::_run_post_nuts_planner`),
  driven off placed geometry.  `RoutingGrid::count_signal_tracks_in{,_span}`
  are Python-bound; the C++ `span_signal_supply` computes the same on
  placed geometry.

**BUILT — opt-in `set_dead_span_escalate on` (2026-07-19).**
`nutsflow.py::_escalate_dead_low_segments`, wired into `cmd_run_nuts` behind
the session flag (default OFF = every checked-in flow bit-identical).  After
each `run_nuts`, every LOW segment whose ACTUAL placed geometry (`span_lo/hi`
+ `interval_lo/hi`) offers zero keepout-clear signal tracks — the exact
DNUTS admission test: `count_signal_tracks_in_span` (span-clear) then the
`count_signal_tracks_in` midpoint fallback — is moved to the cheapest
same-direction TOP layer (`seg_layers[si]`) and NUTS re-solves; iterate until
no dead LOW segment remains (a segment pinned to TOP never returns to LOW, so
the LOW set strictly shrinks — termination guaranteed).  Corpus A/B (flag on
vs off, `ov`/`unpl`):

| flow | baseline | escalate | Δ |
|---|---|---|---|
| bigHalf | 5 / 315 | **3 / 171** | opens −144, ov −2 |
| mix (loci on) | 0 / 42 | **0 / 16** | opens −26, ov 0 |
| mix (baseline) | 0 / 0 | 0 / 0 | no-op (clean signal) |
| b44, big2, channel_stress, comprehensive_demo, hbundles/10, b4_bus_077 | — | — | bit-identical |
| mempool_tile | 61 / 2976 | 90 / 2913 | opens −63 but **ov +29** |

So it is OPT-IN, not default: the two clean beneficiaries are bigHalf and
un-pinning mix from `no_hanan_loci`; the pathological `mempool_tile` stress
demo (already 2976 opens) trades opens for overlaps when it escalates onto its
crowded TOP bands — the one regression, and why default-off matters.  mix's
full un-pinning still needs the LOW-supply capacity fix too (~2/3; see
wishlist-topo "mix–loci").  Tested: `test/tests/test_dead_span_escalate.py`
(keepout-dead LOW stub strands 8 bits off; escalation moves it to TOP and
places; a live LOW stub is left in place — no false positives; default off).

**FOLDED INTO THE HEALERS — default ON (2026-07-19).**
`ripup.py::_heal_dead_spans`, called at the top of both stage-b healers
(`_ripup_reroute` / `_negotiate_congestion`) before the hill-climb: a dead
LOW segment is a guaranteed open no candidate re-pin can reach (a
layer-assignment fault, not a topology-selection one), so escalate it to TOP
ONCE up front and let the healer's own loop absorb any collateral overlap —
the same "escalate, then heal the fallout" contract the planner escalations
use.  Crucially the fold is **structurally safe as a default**: only
stage-b (DNUTS-open) healer runs are touched, escalation strictly reduces
opens (a dead LOW segment strands 100% of its bits), and the manual opt-in's
one regressor — `mempool_tile` — runs NO healer, so it is out of scope by
construction.  Corpus A/B over every healer-running flow (fold off = main, on
= default):

| flow | off (main) | on (fold) | esc | Δ |
|---|---|---|---|---|
| bigHalf | 5 / 315 | **5 / 179** | 2 | opens −136, ov 0 |
| slowdown_rnr | 0 / 42 | **0 / 32** | 3 | opens −10, ov 0 |
| mix, b61, mix2, mix2_fast, big2, datapath×2, synth×3 | — | unchanged | 0–2 | no-op |

Two clean wins, zero regressions, no overlap cost anywhere.  `mix2`/`mix2_fast`
still open (73 / 256) with esc 0 — their opens are the LOW-supply-contention
class, NOT keepout-dead, so the fold correctly leaves them for the companion
LOW-supply capacity fix (see wishlist-topo "mix–loci").  Off-switch:
`_heal_dead_spans_in_healers = False` (study/bisect).  Tested:
`test/tests/test_dead_span_heal_fold.py`.  The manual `set_dead_span_escalate`
(run_nuts-level) stays for healer-less flows.

**TIMING REFINEMENT — escalate at `run_nuts`, before the healers (2026-07-19).**
The stage-b fold arrives AFTER the stage-a healers have already committed
around the un-escalated layout, so the escalated bundle lands in a
pre-arranged crowd.  Running the SAME escalation at `run_nuts` — before any
healer — lets the whole negotiate/ripup cascade adapt around the escalated
layers.  `cmd_run_nuts` now escalates automatically when a healer is ahead
(the `_healers_in_flow` scan the kSegsRel default uses; the manual
`set_dead_span_escalate` still forces it), and the stage-b fold STILL runs
too — the two compose (a segment already moved to TOP is not re-found).
Measured-best is BOTH: run_nuts-timing fixes flows the late fold alone
leaves open, while the fold recovers the one flow the early pass alone
regresses.  Corpus A/B (fresh build, real config — kSegsRel + healersAhead
active; OLD = fold only, NEW = run_nuts + fold):

| flow | off (fold only) | on (run_nuts + fold) | Δ |
|---|---|---|---|
| mix (as-checked-in) | 1 / 16 | **0 / 0** | opens −16, ov −1 |
| bigHalf | 1 / 190 | **1 / 94** | opens −96 |
| mix-loci, slowdown_rnr, big2, b61 | 0 / 0 | 0 / 0 | already clean |
| mix2 | 2 / 42 | 2 / 42 | fold recovers (no regression) |
| mix2_fast | 33 / 256 | 33 / 256 | unchanged |

Two clean wins, zero regressions.  Rollback/study knob
`_dead_span_auto_at_run_nuts = False` forces the stage-b-only timing.  This
is also what closed the mix–loci follow-on (wishlist-topo): with kSegsRel on,
mix-loci was already 0/0, and the 42/32/16 figures were a scriptless-harness
artifact (no `script_path` → `_healers_in_flow` False → kSegsRel silently
off).

**AT ITS CLEAN LIMIT — the plan-time always-on gate is SUPERSEDED, and the
supply-short extension is measured & REJECTED (2026-07-19).**  The literal
open item — flip the plan-time `nontop_dead_span_gate` always-on — is a dead
end: no plan-time span test separates a cull from a survivor (documented
above), and the post-NUTS escalation now delivers the same goal reactively
off placed geometry.  So the discriminator IS the escalation, and it clears
every *keepout-dead* (pool == 0) LOW segment.  What remains open is a DIFFERENT
class.  Classifying bigHalf's residual **94 opens** (default, real config) on
final geometry: **48 are LOW supply-short** (`max(span,mid) pool < nbits` —
some tracks, just too few) and **46 are LOW pool ≥ nbits** (abstract band OK,
real per-track OCCUPANCY short — contention).  Neither is keepout-dead.
Extending the escalation trigger from `pool == 0` to `pool < nbits`
(supply-short), even with a capacity-aware max-pool TOP target AND a
`target pool ≥ nbits` gate, **regresses bigHalf 94 → 270 opens** (+2 ov):
`count_signal_tracks_in{,_span}` sees keepout-clear tracks but NOT occupancy,
so escalating ~48 supply-short segments floods TOP's already-used bands and
cascades into far more opens than it fixes.  A *dead* segment strands 100% of
its bits regardless (2–3 per flow, TOP absorbs them — a layer-assignment
fault); a *supply-short* segment already places SOME bits on LOW, and forcing
48 of them onto a crowded TOP just relocates the shortage.  Conclusion: the
residual is a genuine **TOP-capacity** shortage, not a dead-span fault — it
needs occupancy-aware planning / better topology selection to LOWER the LOW
demand (fewer bits forced onto starved bands), not more escalation.  Tracked
as its own item; the dead-span escalation is CLOSED at its clean limit.

## A metal *above* the TOP band is still a top metal — config-smell WARNING shipped; auto-override measured & rejected

**What:** `LayerType` is a binary flag `{ TOP, LOW }` set explicitly per
layer in `def_layer`; nothing checks a layer's **position** in the stack.
A metal declared *above* the highest TOP layer — e.g. `tracks.buda` had
`M5 (V) TOP`, `M6 (H) TOP`, then `M7 (V)` with no `TOP` — is physically a
high, precious top-level metal, yet the planner's `base_cost_non_top`
penalty (`congestion_planner.cpp`, the `base` term) read it as a cheap
non-TOP offload target and steered short stubs onto it. That is the
modeling half of the NON-TOP/LOW stub-open bug (opens item 4): the
"M7 is non-TOP creates some opens" note.

**Shipped:** `LayerStack::is_above_top(id)` (non-TOP AND id above the
highest TOP-layer id in its direction) + an always-on config-smell
**WARNING** at `build_congestion_map` naming the offending layers and
pointing at the fix (mark them `TOP` in `def_layer`).  Zero routing
change — pure diagnostic, goldens bit-identical.  **`tracks.buda`'s M7
corrected to `TOP`** (the warning's own advice): measured a WIN across the
hbundles suite — 05 opens 32→**0**, 06 2/34→**0/20**, 07 overlap→**0**, 10
unchanged.  Test: `test/tests/test_above_top_layer_warning.py`.

**Measured & rejected — an automatic "treat above-TOP as TOP" override.**
Two shapes tried: (a) my first cut denied the discount but only
half-wired the TOP steering (kBalance can PULL load onto the empty high
metal) — regressed `channel_stress` 0/3 → 12/21; (b) the honest config
fix (mark the layer TOP, full machinery) is a *win* on hbundles but
*regresses* the same `channel_stress` 0/3 → **2/5**.  Root cause: a dense
stress flow legitimately uses the high metal as an **overflow-relief
valve**, and the offload discount is exactly the relief it leans on.  So
"a layer above TOP is always TOP" is NOT a universally correct auto-rule —
it is a per-design call.  `channel_stress`'s fixture is therefore left
non-TOP deliberately (the warning informs; the design choice stands).

**Still open (lower priority):** a position-derived layer model (or a
third `ABOVE_TOP` category) so TOP-ness is *derived* from the stack rather
than hand-labelled.  Given the relief-valve tension it would need a
per-layer opt-out, so the diagnostic-plus-config-fix that shipped is the
pragmatic model; the derived-position refactor is a nicety, not a
correctness gap.

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

## Charge pulled segments at their predicted pull target (books vs metal) — PHASES 0+1 SHIPPED (opt-in level 1); junction prediction SHIPPED (opt-in level 2); default-flip still gated

**What (2026-07-17, from the b44 slide-range analysis):** the planner
charges each segment's congestion demand at a chosen band
(`BundleAssignment.seg_perp`), but NUTS's placement preference chain lets
pull/face semantics OUTRANK the charged band for pulled segments — so the
capacity books say one place and the metal lands in another: reserved
bands sit empty while used bands were never charged.  Under contention
this is a systematic source of the overlaps the planner "didn't predict"
(the class negotiate/ripup grind on).

**Repros (pulled segments with a charged band; divergence =
|placed track − seg_perp|):** `b44` — **4 of 4** pulled segments diverge
>100 (e.g. seg5 charged at band 11330, placed 10888.5, Δ442; pre-clamp
seg3 was charged 1450, placed 2641.5, Δ1191).  `bigHalf` — **141 of 185**
pulled+charged segments diverge >100 units, worst **Δ3378** (bundle 45
seg11: charged 3565, placed 186.5).  `big2_noviz` — **123 of 148**, worst
Δ2264.  This is the MAJORITY of pulled segments on the congested corpus,
not a corner case.

**Shipped (2026-07-17, opt-in `set_planner_param charge_pull_target 1`):**
Phase 0 — the `[NUTS] books-vs-metal` diagnostic line after every
`run_nuts` (always-on report, no behavior).  Phase 1 — with the knob on,
`plan_bundle` anchors a pulled segment's charge/scored bands at the
deterministic predicted target (window bound tightened by an in-travel
`ConnSeg::pull_break`, bus-width clamped), and the session passes PLACED
positions to `band_occupants` so ripup's victim ranking follows the metal
(contention fallback can move metal off even an honest prediction — the
plan-based ranking saw only b1 of the (1,3) overlap on the occupant
fixture).  Measured: divergence bigHalf 141→22 (−84%), big2 123→53, b44
4→1; endpoints mempool_tile WL −83% with overlaps 61→27 and opens
2971→1696, bigHalf WL −6.1% overlaps 3→2, mix/channel_stress ~neutral;
healers absorb the plain-pipeline shuffles (big2+heal 0/0, bigHalf+heal
0/0).  Tests: `test_planner_charge_pull_target.py`; knob-off is
bit-identical (full fast+mid green).  **Why opt-in — the deep-dive
finding on the kPeak/alignment/ripup test failures + comprehensive_demo:**
the placement preference chain has THREE members that outrank the charged
band (pull, face, junction-anchor) and the prediction covers only the
pull.  On `comprehensive_demo` the knob's WL-better reshuffle (−3.3%)
lands b3's MST leg on the keepout via the JUNCTION-ANCHORED preference
(`pull=0`) — 1 bit strands on a healer-less demo.  Also exposed: the two
kPeak hybrid-floor tests asserted anchors on a pulled fixture segment
whose metal never moved (books-only steering, all 8 bits stranded old AND
new — fixtures to refit with an unpulled segment when this flips default).

**Follow-on (a) shipped (2026-07-17) as LEVEL 2** (`charge_pull_target 2`):
the demo-b3 probe overturned the first diagnosis — b3's charge and metal
AGREE (both y=500); the failure is a SPAN books-vs-metal: seg3's nominal
span is a 35-unit stub clear of the keepout, but its pulled trunk partner's
predicted track (450) stretches it 200 units across the M4 keepout (the
junction-driven span stretch NUTS's do_span_adjustments realizes).  Level 2
adds: **(a1)** the single-rider anchor clamp on the charged band (the NUTS
anchor rule mirrored on nominal rider extents — starting from THE SAME
BASE NUTS uses: the NOMINAL coordinate when the segment has busterm faces,
since `pull_map` stays nominal and `seg_perp` is never consumed there, the
band pick otherwise; clamping every single-rider segment from the band
pick was a mis-mirror caught in review — fixing it took mix from 2 ov/21
opens to 1/0 and bigHalf 3/560 to 2/360) and **(a2)** a STRICT
DEAD-BAND gate over junction-extended spans (`span_hits_dead_band`) — each
span extended to every pulled partner's deterministic predicted track,
refused ONLY when the extension crosses a zero-capacity (keepout-carved)
band.  Two stronger (a2) forms measured and REJECTED: charging the full
extension corpus-wide (mix healed 0→2 ov, big2 WL +13%) and gating on
any-overflow (mix 2 ov / 21 opens) — physical impossibility gates, load
pressure never does (the dead-span-gate over-conservatism lesson, twice).
Measured at level 2: **comprehensive_demo heals to 0/0**, big2 plain opens
268→152 (5 ov, the (a1) clamp), b44/mempool/hbundles-10 unchanged;
**mix 1 ov / 0 opens** and bigHalf 2/360 vs level 1's 0/26 and 2/392 —
level 2 improves level 1's opens on both.  Isolation note: level 1's own
mix endpoint is 0 ov / 26 opens (vs 0/0 off-knob) — an occupant-overlay
reshuffle property discovered during (a)'s isolation runs, previously
unmeasured.  Tests: `test_level2_junction_prediction_heals_demo_b3` +
the level-1 suite.

**Re-measured on latest main (2026-07-20, after the dead-span-into-healers
fold + convergence guard + kSegsRel default landed), each flow AS CHECKED IN
with its own healers, levels 0/1/2 (detailed WL after `run_detailed_nuts`):**

| flow | L0 ov/un/WL | L1 | L2 |
|---|---|---|---|
| mix | 0/0 / 850633 | 0/0 / 782204 (**−8.0%**) | 0/0 / 777816 (**−8.6%**) |
| comprehensive_demo | 0/0 / 40888 | 0/0 / 39656 (−3.0%) | 0/0 / 39656 (−3.0%) |
| 10_chip_units | 0/0 / 364252 | 0/0 / 357004 (−2.0%) | 0/0 / 357004 (−2.0%) |
| bigHalf | 0/0 / 15473957 | 0/0 / 15159910 (−2.0%) | **0/30** / 16142958 (+4.3%) |
| big2 | 0/0 / 11601416 | 0/0 / 12395918 (**+6.8%**) | 0/0 / 12360178 (**+6.6%**) |
| mix2 | 2/42 / 839868 | 1/**144** / 821476 | 6/**150** / 785748 |

**Gate (i) has MOVED, not cleared.**  mix — the *historical* blocker — now
heals cleanly at BOTH levels with a large WL win (−8%): the
dead-span-escalation-into-healers fold + kSegsRel default that landed since
2026-07-17 absorb the honest-books reshuffle its heal budget used to choke
on (same mechanism that dissolved mix's kSegsRel objection).  But the
honest-books mode is still not a corpus-wide win: **big2's WL regresses ~7%
at both levels** (endpoint stays 0/0 — the routes just get 7% longer, a real
quality regression), **mix2's unplaced blows up 42→144 (L1) / 150 (L2)** on
an already-stressed flow, and **level 2 regresses bigHalf to 0/30 opens**
(level 1 keeps it clean).  Level 1 is the closer candidate — 4 flows improve
clean (mix −8%, comprehensive −3%, 10_chip −2%, bigHalf −2%), only big2 (WL)
and mix2 (unplaced) block — but neither level clears the corpus, so the flip
stays gated with the blocker relocated from mix to big2/mix2.

**Level-1 charge fix — occupancy-aware anchor (2026-07-20, SHIPPED opt-in).**
The big2 blocker above was diagnosed as a SELECTION shift: honest-books level 1
flipped 26/80 big2 bundles to longer OOB/trunk candidates (est-WL +8%, endpoint
still 0/0 — NUTS placed the short routes fine).  Root cause: `band_perp`
returned the clamped `pull_anchor` UNCONDITIONALLY, so every pulled segment
booked demand at its window bound, piling phantom demand onto a few bands;
later bundles saw those bands full and escalated to detours — but NUTS's
`preferred_fit` TARGETS the pull and SPREADS to the nearest free track, so the
concentration never materialized.  Fix: charge at the anchor only when that
band is overflow-free, else fall to the occupancy-aware `best_band_perp`
(exactly NUTS's spread).  Where the congestion is REAL (anchor and every nearby
band overflow) the fallback still overflows and STRICT escalates, so the honest
concentration is preserved.  Measured (fix vs pre-fix, each level as checked
in):

| flow | L0 | L1 fix (was) | L2 fix (was) |
|---|---|---|---|
| big2 | 0/0/11601416 | 0/0/**11791161** (12395918; +6.8%→**+1.6%**) | 0/0/11836484 (12360178; +6.6%→+2.0%) |
| bigHalf | 0/0/15473957 | 0/0/**14156043** (15159910; −2%→−8.5%) | 0/0/**15617200** (**0/30**→**0/0**) |
| mix2 | 2/42/839868 | 5/**31**/854844 (unplaced 144→**31**, below L0) | 11/180/780635 (150→180) |
| mempool_tile | 61/2976/532381 | 16/1870/**256366** (−52%, win kept) | 24/1984/254976 (−52%) |
| mix | 0/0/850633 | 0/0/828270 (782204; −8%→−2.6%) | 0/0/777801 (−8.6%, unchanged) |
| comprehensive / 10_chip | 0/0 | 0/0 (−3%/−2% erased) | 0/0 (erased) |

Net: the fix removes big2's WL blocker (+6.8%→+1.6%), clears bigHalf L2's 0/30
opens, turns mix2's L1 unplaced regression (144) into an improvement (31, below
L0), and preserves the mempool/mix-L2 wins.  The trade is small WL-only losses
(≤3%, all still 0/0) on comprehensive/10_chip and half of mix's L1 win — the
flip side of removing the over-concentration (where it helped by luck it no
longer does).  By keeping the charge honest it ALSO heals the comprehensive_demo
b3 keepout strand at **level 1** (previously a level-2 dead-band-gate win) and
makes the plan-based `band_occupants` ranking honest enough that the placed
overlay's strict-superset behavior now shows only on contention-fallback flows.
Knob-off bit-identical.  Gate (i) is now big2 +1.6% (a defensible honest-books
cost) — much closer to a defensible default, though the flip still needs
reference-host golden regeneration (iii) and the alignment-sibling predictor
(ii).

**Default-flip: still gated.**  The remaining discriminators: (i) the
honest-books mode is not a corpus-wide win — big2 WL +7% and mix2 unplaced
42→144 at level 1, plus bigHalf 0→30 opens at level 2 (2026-07-20; mix's
historical objection has dissolved but the blocker relocated to big2/mix2,
table above); (ii) the alignment-sibling placement remains unpredicted
(b44's seg3 residual — a LEVEL-3 static predictor was PROTOTYPED AND
REJECTED, detailed below); (iii) goldens must be regenerated on the
reference host.  The kPeak hybrid-floor fixture refit (their pulled segment
asserts books-only anchors) also waits on the flip.

**The alignment-sibling prediction — PROTOTYPED & REJECTED (2026-07-19,
static heuristic insufficient; it is a genuine placement fixed-point).**
NUTS's START-time placement chain (`nuts.cpp` solve loop, ~L1600–1656)
picks a segment's target track in strict priority: (1) cross-layer
split-side bound → (2) **alignment sibling** → (3) junction anchor →
(4) charged pull target → (5) interval centre.  Level 1 taught the planner
to charge at member (4)'s predicted target; level 2 predicted member (3).
Member (2) — the alignment sibling — sits ABOVE both and is still charged
wrong.  An *alignment sibling* is a same-bundle segment sharing a
perpendicular connector (Pass 3, `nuts.cpp:210` — `rev_conn_map[T]` lists
the segments whose span follows trunk `T`; any two sharing `T` are
siblings, e.g. a multicast trunk's stubs on opposite sides).  When one is
already placed and its track fits the current segment's centre range, the
current segment lands EXACTLY on the sibling's track
(`nuts.cpp:1605–1613`), collapsing the split onto one shared track — free,
since same-bundle bits never conflict, and a win for DNUTS bit-sharing; it
exists to break the wirelength-neutral trunk deadlock (moving one sibling
alone leaves the others pinning the junction).

*Why it is the HARDEST member to predict.*  Levels 1–2 are functions of
STATIC topology geometry — the pull breakpoint is a slope crossing; the
junction anchor clamps into a partner's nominal span; both knowable at
plan time.  The alignment target is `sibling.track_position` — a RUNTIME
output of the sweep, wherever that sibling landed, which depends on sweep
order and the whole occupancy state.  It is not a geometric constant but a
FIXED POINT of the placement itself.

*The concrete residual (b44 seg3, the outlier pinned in
`test_planner_charge_pull_target.py`; reproduced on the pinned
`TRUNK_H+MST@y11915` staircase at level 2).*  seg1 (V, pull −1) and seg3
(V, pull +1) are siblings sharing the H connector seg5 (pull −1).  The
planner charges seg3 at its own upward pull target (2642), but NUTS
collapses seg1/seg3 onto ONE track at 1200 (seg5 → zero): seg1 lands at
its charge (div 0), seg3 lands on seg1's track, off its 2642 charge
(**div 1442**).  3 of 4 pulled charges are exact; seg3 is the residual.

*The prototype (`charge_pull_target 3`, graded above level 2).*  Build
alignment groups from `conn_segs` (same-orientation segs sharing a
perpendicular SEG-conn partner, union-find).  For each group, charge every
pulled member at the predicted collapse track — the extreme member anchor
in the group's NET-PULL direction (members + the SHARED connector's pull:
the b44 seg5 tie-breaker, only connectors touching ≥2 members vote),
clamped into the group's common slide window.  **On b44 this WORKS:** net
= seg1(−1)+seg3(+1)+seg5(−1) = −1 → collapse low → both charge 1200, seg3
div 1442 → **0**, all 4 pulled charges exact.

*Why REJECTED — the static rule cannot tell a real collapse from an
independent placement.*  Two same-orientation segments sharing a connector
are only POTENTIAL siblings; whether they actually merge onto one track is
the sweep fixed-point, invisible at plan time.  Measured (level 3 vs 2,
each flow AS CHECKED IN incl. its healers):
  - **Blanket group override:** endpoint REGRESSIONS — big2 dnuts-unplaced
    84 → 184 and overlaps 2 → 4, bigHalf worst books-divergence 662 →
    4185; mix happened to improve (ov 1 → 0, WL −0.6%) but by luck, not
    prediction quality.
  - **Tightened to the clean case** (exactly two pulled siblings, opposite
    pull signs — b44's shape): endpoint-NEUTRAL corpus-wide, but STILL
    false-positives — `demo/comprehensive_demo` bundle 3 seg7 (H, pull +1)
    charges AND places at its own pull target 748 at level 2 (div 0, no
    collapse), yet the level-3 detector groups it with a non-merging
    sibling and charges it at a phantom 652 → a **new** 96-unit divergence
    where there was none.  A books-honesty feature that dishonest-ifies
    some books is self-defeating, and it does NOT reduce the big2/bigHalf
    residuals (56 → 57, 22 → 22) — it only fixes b44 by the net-pull
    direction coincidentally matching.

*Conclusion.*  The collapse track is a true fixed-point of the sweep; no
static plan-time heuristic distinguishes a merging sibling pair from an
independent one.  A reliable prediction needs a NUTS-side pre-solve signal
— an actual alignment-group resolution pass run at plan time (place the
group in isolation, read the collapse) — which is a larger build than the
level-1/2 geometric predictions and out of proportion to the payoff
(endpoint-neutral on today's corpus; only b44's pinned staircase moves).
Prototype reverted; the `band_occupants` PLACED-position overlay (Phase 1)
already lets ripup attribute a mispredicted aligned segment correctly, so
the downstream damage is contained even with the charge left approximate.
Gate (ii) for the default flip therefore stands, downgraded from
"unpredicted" to "predictable only via a NUTS-side pass, deferred".

### The NUTS-side alignment pre-solve — DESIGN + measured-negligible decision (2026-07-20)

The reliable predictor the conclusion above calls for is an **isolated
single-bundle alignment resolution** run at plan time.  Concretely:

1. After a bundle's layers are assigned (so the per-layer sweep is defined),
   build its alignment groups exactly as NUTS Pass 3 does (`rev_conn_map[T]`
   over the bundle's own segments: any two segments following the same
   perpendicular connector `T`, jogs excluded, are siblings — union-find over
   `conn_segs`).
2. Run the bundle's segments through an **isolated placement** — the real NUTS
   sweep on just this bundle, no other occupancy (the machinery already exists:
   `NUTSEngine::rerun_bundle_warm` / the screen-mode single-bundle placement
   place one bundle against a frozen/empty context).  Same-bundle bits never
   conflict, so the collapse is deterministic: the first-swept sibling lands at
   its pull target and the rest collapse onto it iff its track falls in their
   centre range — the range check the static level-3 heuristic could not do
   because it lacked the sweep.
3. Read each aligned segment's placed track and charge it there (member (2)),
   above the level-1/2 pull/junction anchors.

This is faithful (it runs the actual sweep, so it distinguishes a real collapse
from an independent placement) and is the correct way to close member (2).

**Why the build is DEFERRED — the payoff is measured endpoint-neutral.**  After
the occupancy-aware anchor shipped (#364), a corpus sizing pass
(`charge_pull_target 2`, each flow as checked in) counted pulled segments whose
final placed track diverges >100 units from their charged band:

| flow | pulled | div>100 | worst | flow | pulled | div>100 | worst |
|---|---|---|---|---|---|---|---|
| b44 | 2 | **0** | 0 | 10_chip | 49 | 8 | 574 |
| mix | 182 | 21 | 626 | big2 | 159 | 52 | 2498 |
| mix2 | 167 | 15 | 1010 | bigHalf | 171 | 67 | 3115 |
| comprehensive | 16 | 0 | 56 | | | | |

The clean signal is **b44's full flow: 0 residual** — the alignment-sibling
mis-charge only appears in the *pinned staircase* fixture
(`test_planner_charge_pull_target.py`), never when b44 auto-selects.  The large
corpus counts (big2 52, bigHalf 67) are NOT the alignment class: this diagnostic
measures divergence AFTER the full flow, so it is dominated by (a) the healers
legitimately re-pinning routes, (b) the occupancy-aware anchor charging at
`best_band_perp` while NUTS spreads to a slightly different free track, and
(c) contention-fallback (BEST_EFFORT) moving metal — none of which a static
alignment pre-solve addresses, and (a)/(c) are already handled downstream by the
`band_occupants` PLACED overlay.  So an isolated-solve pre-solve would fix only
b44's staircase residual — **endpoint-neutral on the corpus**, confirming the
2026-07-19 finding post-#364.

**Decision.**  Gate (ii) is reframed: the alignment member is *predictable* (the
pre-solve above is the design), but predicting it moves **no corpus metric**, so
the build is DEFERRED under the same measured-change discipline that keeps
kWLSpread / charge_pull_target opt-in — shipping a large NUTS↔planner pre-solve
for zero measured movement is exactly the complexity the discipline refuses.
The design is recorded here and ready to build if a future corpus case makes the
alignment residual matter (or when the reference host takes up the
charge_pull_target default flip and wants member (2) closed for completeness).
The `band_occupants` PLACED overlay already contains the downstream damage in the
meantime.  Tracked as a **big open** on the planner/NUTS subsystem —
[`opens.md`](opens.md) → *Big / blocked / conditional* item 8 — with the build
trigger (a bigger test case where the residual moves a corpus metric).

**Cheap in-planner path re-confirmed insufficient (2026-07-20).**  Before
deferring, the tractable version was built and measured: resolve the collapse
*in the planner* as a bounded rule (leader = first-swept sibling by `along_lo`,
followers collapse onto its pull target, occupancy-aware in `band_perp`).  On
b44 it **regressed** the residual — seg1's charge went 1200 → 2642 (div 0 →
1442) — because the collapse track is NOT the first-swept sibling's target: seg3
has the lower `span_lo` (swept first) yet both land at seg1's 1200, since the
outcome is the whole group's tug-of-war net-pull (seg1− , seg3+ , shared trunk
seg5− → net low → 1200), a fixed-point of the sweep.  Any plan-time leader/
net-pull rule is the same static heuristic already rejected 2026-07-19, and it
fails the same way.  This empirically re-confirms that ONLY NUTS's actual sweep
(the isolated single-bundle solve above) captures the collapse — the bounded
shortcut is not a viable substitute, so the faithful two-pass is the only build
that works, and it stays deferred on the endpoint-neutral payoff.

## Realization-risk WL: rank on the envelope, not just the nominal — `kWLSpread` SHIPPED (opt-in)

**The b44 mis-ranking (repro `flow/big_data_test/b44.buda`, 2026-07-16):**
the planner's `kWL` term scores the candidate's NOMINAL segment-sum, but the
routed WL is a realization inside the candidate's slide/span DOF envelope
`[wl_lo, wl_hi]` (the interval dump_topologies/report_wl already compute).
b44's 52-bit multicast: the nominal ranking picks a 6-seg `TRUNK_H+MST`
(nominal 3510, envelope [3510..12160]) whose greedy NUTS placement stretches
the trunk between its independently-slid stubs to 4510/bit, over a 2-seg
`TRUNK_V` (nominal 4010, [3510..5010]) that realizes 3715/bit — detailed WL
234546 vs 193376 (+21%), in a single-bundle zero-contention flow. Corpus fill
study (290 bundles / 6 flows): routed WL sits at **fill mean 14.7%, median 9%,
p90 32%** of the envelope — realizations concentrate near the bottom, spread
is the risk signal.

**As-built:** `Topology::wl_lo/wl_hi` (derived annotation like `seg_bits`:
never persisted, excluded from topo_uid), stamped by the session
(`_annotate_wl_envelopes`, the dump_topologies envelope math + per-bundle
frame resolver) at `run_planner`/`run_planner hier` when
`set_planner_param kWLSpread <a>` is set; the planner's WL term becomes
`nominal + kWLSpread × (wl_hi − wl_lo)`. **Base stays the nominal** — the
envelope-point REPLACEMENT `wl_lo + fill×spread` was measured and REJECTED
(it erases genuine nominal differences and reshuffles near-ties corpus-wide:
big2 +27% WL / opens 0→252 at every fill tested). Measured at `0.125`:
b44 −19.6% detailed WL (beats even the flow's hand-pin), **mempool_tile
−46.5% WL with overlaps 61→27 AND opens 2971→2038**, mix −0.1%, hbundles/10
−0.5%; plain no-healer pipelines can surface a selection-shuffle opens delta
(big2 0→60) that the standard healers absorb completely (big2 + negotiate +
ripup: **0 opens / 1 overlap** vs baseline 0/5, +2.1% WL; bigHalf + healers
**0/0**). `0.25` is too aggressive (mix healed endpoint 0→16). Tests:
`test/tests/test_planner_wl_spread.py`; docs: `docs/script_reference/planner.md`.

**Root causes (deep-dive):** the nominal-vs-realization gap is a
generation-policy comparability problem — the MST hybrid's nominal is a
zero-overshoot monotone staircase (always AT its envelope bottom), plain
trunks sample loci only at Hanan-channel midpoints (the WL-optimal
edge-aligned locus is never emitted, b44's +500), and WL ties USED TO break
alphabetically (`(wl, type)`, ASCII `'+' < '@'`) then by lowest index — the
structural `(wl, nsegs, type)` tie-break shipped 2026-07-17 (piece b) — see
[`wishlist-topo.md`](wishlist-topo.md) → *"Nominal-WL comparability across
shape families"* for the remaining generation-side follow-ons (Hanan-line
loci, dominance pruning) and why the score-term route shipped first.

**Update (2026-07-17, post pull-breakpoint clamp):** the NUTS pull-target
breakpoint clamp ([`wishlist-nuts.md`](wishlist-nuts.md) → *"Pull-target
breakpoint clamp"*) removed b44's +1000/bit realization overshoot at the
source, so the DEFAULT pipeline now realizes the MST at 3627/bit and the
knob's b44 delta collapsed to ~0.1% (its test asserts "not worse").  The
knob remains the selection-level guard for realization risk the clamp
cannot remove (contention-driven stretch; wide envelopes still price real
risk on congested designs — the mempool_tile/bigHalf numbers below predate
the clamp and should be re-measured before a default-flip decision).

**Re-measured post-clamp (2026-07-20), `kWLSpread 0.125` vs `0`, real config
(script gates on, healers as each flow declares them, detailed WL after
`run_detailed_nuts`):**

| flow | healers | base ov/un/detWL | 0.125 ov/un/detWL | dWL |
|---|---|---|---|---|
| b44 | — | 0/0 / 193376 | 0/0 / 193376 | **+0.0%** |
| mempool_tile | none | 61/2976 / 532381 | 27/2230 / 267990 | −49.7% |
| mix | yes | 0/0 / 850633 | 0/0 / 830452 | −2.4% |
| mix2 | yes | 2/**42** / 839868 | 2/**52** / 825726 | −1.7% |
| bigHalf | yes | 0/0 / 15473957 | 0/0 / 14541544 | **−6.0%** |
| big2 | yes | 0/0 / 11601416 | 0/0 / 11543464 | −0.5% |
| 10_chip_units | yes | 0/0 / 364252 | 0/0 / 362418 | −0.5% |
| comprehensive_demo | yes | 0/0 / 40888 | 0/0 / 39726 | −2.8% |
| b4_bus_077 | — | 0/0 / 191669 | 0/0 / 191669 | +0.0% |

The clamp confirms the 2026-07-17 prediction: **b44's flagship −19.6% is gone
(now +0.0%)** — the clamp already captures at the source what the knob used to
buy on b44. What survives is (i) modest but genuine WL wins on *clean, healed*
flows (bigHalf −6.0%, comprehensive −2.8%, mix −2.4%, big2/10_chip −0.5%, all
0/0 preserved), and (ii) the big mempool_tile number, **but that is a
healerless flow that never reaches a clean endpoint** (ends 27 ov / 2230 un) —
not a basis for a default. And mix2's unplaced *regresses* 42→52 **with
healers already in its flow**.

**Default-flip verdict (2026-07-20): stays OPT-IN.** The mix2 regression is
the deciding data against criterion (a) below: the kSegsRel-style "gate the
default on healers-ahead" trick works only because healers absorb the
selection shuffle — but mix2 *has* healers and still regresses, so a
healers-ahead gate would not protect it. With the flagship b44 win absorbed by
the clamp and the largest remaining win on a broken flow, the clean-flow gains
(≤6%) don't justify risking a stress-flow opens regression by default. Path
(b) — a spread term that prices only the *trunk-stretch* (junction-coupled)
component rather than the whole envelope — remains the open route to a safe
default; it is a real implementation, not measured yet.

**Default-flip criteria (stays opt-in for now):** same bar as `kPeak` — the
opens shuffle on plain pipelines means a blanket default needs either (a) the
healers in the default flow path (refuted for kWLSpread above — mix2 regresses
*with* healers), or (b) a spread term that prices only the
*trunk-stretch* component (junction-coupled spread) rather than the whole
envelope. Bottom-up template planning (`_plan_bottom_up_templates`) IS
annotated (Codex #312): the local solve's planner is seeded from
`_planner_params`, and the templates' envelopes are stamped against the
cell-local floorplan the solve plans in, so the spread term applies before
the pin that expansion locks in.

## Selection basis: rank on measured routability, not the generation-time WL estimate — LEVERS 1+2+3 SHIPPED; `kPeak` default DECIDED (stays opt-in)

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
pool. The ripup loop keeps the best measured metric over a contender's
move list with a STRICT `<`, so the cheap-first order is load-bearing: an
extra displaces a cheap fix only by a STRICTLY better (opens, overlaps)
metric — at an equal metric the earlier (cheap) move always wins the tie,
so routes change only where a promotion strictly improves the measured
metric (goldens byte-identical; mix.buda verified identical to main).
Farness-first over the WHOLE pool was measured and rejected: it put the
far expensive candidate BEFORE the cheap same-effect one, handing it the
tie (mix.buda bundle 85: idx 26 over idx 5, +2% abstract WL at an equal
metric). Measured on big2
(`tc3b_flat_x5`): stage-a residual overlaps after ripup 1→0 (bundle 27
promoted to index 11, unreachable before), abstract WL −0.18%, detailed WL
+0.04%, stage-b endpoint unchanged (0/0). `negotiate_congestion` needs no
companion change — its `replan_bundle` re-plans UNPINNED, scoring ALL
candidates through the cost model, so every class was already reachable
there (and lever 1's `kPeak` is the knob that biases that model toward
routability). Tests: `test/tests/test_ripup_class_rerank.py` (pool
composition: legacy-first, beyond-cap extras, farness ranking, self-trial
exclusion).

**Lever 3 as-built (2026-07-30): `refine_selection` — the measured WL
polish.** Levers 1+2 left a residual gap neither measured loop could
close: ripup's metric is overlap/opens-only (it stops at parity and
never improves WL), and the planner's `refine_passes` re-scores through
the cost model whose WL term is the ESTIMATE — so a candidate that
routes shorter than it estimates still structurally loses (the
2026-07-30 default-flip study pinned this as the `multi_trunk` /
`spine_relays` flip blocker). The new opt-in `refine_selection
[max_moves] [chase_overlaps]` command (`_refine_selection`,
`src/buda_session/ripup.py`) sweeps every eligible bundle's selection on
the MEASURED result, reusing ripup's machinery end to end: the
fixed-context screen orders all alternates (ordering only, never a
metric), the top-2 are full-trialed (fast trials forced off — a
tighten-skipped trial's WL would be biased against the move, and full
trials make winners forward-restorable), commits ride the fwd-snapshot +
`recharge_committed` path. The accept metric is realized abstract WL
(Σ placed span lengths — the post-settle geometry the estimate only
approximates). **Placement matters — it runs at the END of the flow,
after the healers**: both pre-healer placements were implemented and
measured first, and BOTH perturbed the healers' basins (lexicographic
(ovl, WL): aligned 30/0 → 16 opens/6 ovl; even the overlap-parity
WL-polish accept: bottomup 0 → 16 opens, mix 1 ovl → 4 opens — a
selection change at parity still shifts the healers' trajectories).
End-of-flow, the default accept is componentwise — opens AND overlaps
parity-or-better with WL strictly lower — so an endpoint regression is
impossible by construction (`chase_overlaps` keeps the aggressive
lexicographic form for healerless experiments). Measured (rnr vehicles,
endpoints preserved exactly): mix realized WL 64893→59424 (**−8.4%**,
44.8s), aligned −1.7% (5.2s), bottomup −0.2% (4.6s); on the healerless
topdown flow the componentwise accept doubles as a healer: 175 opens/16
ovl → **84/2** with WL −3.1%. Opt-in — flows that do not call it are
byte-identical. Tests: `test/tests/test_refine_selection.py` (+
`features/refine_selection.feature`); docs:
`docs/script_reference/nuts.md`.

**`kPeak` default decision (2026-07-11): stays opt-in (default 0).
Confirmed after the supply-aware follow-on shipped — the reopener premise
was falsified by measurement.** The full experimental record (per-testcase
corpus tables, value sweeps, healed endpoints, and the three
implemented-and-rejected variants) lives in
[kpeak_measurements.md](kpeak_measurements.md).
- The big2 "double-steer" hypothesis was debugged and REJECTED first —
  big2_noviz runs the PLAIN pipeline (no negotiate/ripup at all). At every
  tested value (0.05/0.1/0.2) exactly two wide trunk segments (bundle 23
  seg 1, 60 bits; bundle 25 seg 0, 56 bits) strand completely (~104–116
  opens vs 0 baseline), identically in `signal_tracks` mode.
- The "absolute-supply blindness" diagnosis was then ALSO falsified: the
  stranded trunks' NUTS windows hold **153 / 93** real signal tracks for
  their 60 / 56 bits — supply is ample, and the DNUTS failure path is the
  `reserved`-tracks exhaustion, not "insufficient signal tracks". Bundle
  23 isn't even party to any NUTS overlap: interval-overlapping
  *competitors* placed earlier reserve most of the shared window's
  tracks, and the all-or-nothing check strands the widest late-processed
  segment. The real mechanism is the **pre-charge horizon**: a wide
  bundle plans early against a nearly-empty map, so NO per-bundle term —
  relative or absolute — evaluated at its plan time can price the
  arrivals that come after it. Intrinsically a feedback problem;
  `negotiate_congestion` + `ripup_reroute` are the fix (they heal big2 to
  0/0, the baseline endpoint).
- The healed endpoint is itself flow-dependent: `mix` + kPeak 0.1 heals
  to 0 overlaps / **16** opens vs the baseline's 1 / **0** (same with and
  without the supply floor, with mix's configured negotiate/ripup
  budget) — so even "kPeak + the loops" is a per-design option to
  validate with `check_design`, not a blanket recommendation.

**Supply-aware `peak_util` — SHIPPED as a kPeak improvement (2026-07-11),
on its own merits, not as the default-reopener** (that premise died
above). When kPeak > 0 and the layer has a `def_track_pattern`, the
band's span-wide SIGNAL supply (`count_signal_tracks_in_span`, the same
override/keepout-aware pool DetailedNUTS places from; along-extent uses
the shared `routed_extent` endpoint-block clamp; window = Hanan band ∩
slide window) is checked against the bundle's bit count and util is
clamped to ≥ 1 on a shortfall — an empty-because-unroutable band never
ranks better than a full one. The planner now receives the routing grid
in WIDTH mode too (read only behind kPeak/track-mode gates; defaults
bit-identical, goldens verified). This catches the class the width model
structurally cannot see: a region override's supply (eff_bus_width uses
the layer's GLOBAL pattern). Measured (vs pre-floor kPeak): mix pre-heal
opens @0.1 128→**86** (−55% vs baseline 190; healed endpoint unchanged
16), channel_stress heals from 0.05 (was 0.1), tc3a clean at ALL values
(the 0.2 regression is gone), big2 unchanged (its mechanism is the
horizon, above). Tests: `test/tests/test_planner_kpeak_supply.py` (blind
default strands 8 bits through an override-starved corridor; floor
detours it clean; floor silent when supply suffices), plus the
count/vector lockstep guard in `test_routing_grid.py`.

The two follow-on refinements from the PR #257 review are now settled
(2026-07-12):
- **Proportional supply clamp — implemented, measured, REJECTED.** The
  flat `1.0` indeed goes flat under total scarcity: in a synthetic
  all-bands-floored scenario, `tracks_needed/supply` flips the selection
  from the worst band (8-for-3) to the least-bad one (8-for-7 at planner
  band granularity) and every bit places, where flat strands all 8. But
  the corpus paid for it: the region above 1.0 leaks into the
  topology/layer competition against overflow-priced alternatives —
  exactly the caution recorded when this was proposed — and `mix`
  regressed decisively at kPeak 0.1 (overlaps 20→**40**, opens 86→107);
  hbundles/05/06/07 and channel_stress unchanged; big2 untouched by
  construction (its floor never fires). Reverted to flat; the decision
  and numbers live at the clamp site in `peak_util_segment`.
- **The middle ground — flat-score / proportional-band-choice hybrid —
  explored and ADOPTED (2026-07-12).** The floor's shape now follows the
  caller's comparison scope (`peak_util_segment(..., proportional_floor)`):
  the segment SCORE keeps the flat 1.0 (it compares across
  topologies/layers — the leak that killed the global clamp is
  structurally excluded), while `best_band_perp`'s intra-segment band
  choice prices a shortfall as needed/supply, so among bands that ALL
  fall short the charge (and NUTS's `seg_perp` anchor) steers to the
  least-impossible one instead of the flat tie leaving the anchor at
  the window centre — even when the centre sits in the WORST band.
  Corpus-neutral at kPeak 0.1 (mix 20/86, channel_stress 0/0,
  hbundles/05 13, 06 2/2, 07 0/0 — all identical to flat): floored
  multi-band slide windows don't arise on the current corpus, and a
  single-bundle strand can never be fixed by band choice anyway (the
  NUTS interval spans the whole window, and DNUTS admission is
  interval-total) — the anchor placement matters only through
  multi-bundle packing. Adopted despite the neutral corpus because the
  tie-break flaw is real, the mechanism is pinned by test
  (`test_hybrid_floor_steers_anchor_to_least_bad_band`: anchor 210 → 270
  into the 4-track band), and the risk is bounded by construction.
- **Override-boundary pattern resolution — FIXED** (see wishlist-nuts):
  the span walkers now resolve the pattern per perp slice, so a
  boundary-touching override no longer claims the band above it. The
  original analysis follows.

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

**The lever (original analysis — since shipped as levers 1–3; the
realized-WL side is lever 3's `refine_selection`, as-built above).** Bias
selection by *routed* quality, not just estimated
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
