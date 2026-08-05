# Wishlist — Abstract & Detailed NUTS

Deferred follow-ups for track assignment (`src/nuts.cpp`,
`src/detailed_nuts.cpp`). Index: [`wishlist.md`](wishlist.md).

## Pull-target breakpoint clamp — ✅ SHIPPED (the b44 tug-of-war fix)

**What (2026-07-17):** `net_pull` is a *direction*; NUTS's pull placement
aimed at the bus-clamped slide-window EDGE (`pull_target`).  On a wide
interior window — a connector crossing the covered block, w=2500 on b44's
`TRUNK_H+MST` — the edge overshoots the point where the pull's wirelength
gain saturates: b44's seg1 (gain ends at 1200 where its trunklet collapses)
was placed at 258.5, its opposite-pulled sibling at 2641.5, and the interior
trunk between them stretched 1250 → 2383 — the entire **+1000/bit**
realization excess behind the b44 mis-ranking arc (each pull "won" its local
leg for ~−640 combined while costing +1643 on the coupled segments).

**As-built:** `derive_net_pull` (topology_analysis.cpp) records each vote's
saturation coordinate and resolves the net pull's slope-crossing breakpoint
onto `ConnSeg::pull_break` — a busterm vote saturates at its `face_coord`, a
floating-spine vote at the NEAREST far segment's slide bound (the exact
overshoot boundary its outside-interval gate already encodes); multi-vote
nets take the ⌈net/2⌉-th breakpoint in travel order (slope crossing).
`build_nuts_maps` (nuts.cpp) clamps the pull preference at the breakpoint
ONLY when it lies within the travel from nominal to the bound (a tightening,
never a new direction); dogleg-pinned slides and dogleg-overridden pulls
keep the bound semantics (their votes describe the pre-split topology), and
the ANCHORED vote case is inert by construction (its face_coord lies beyond
the bound and clamps back — hold-at-bound preserved, tc3a B20).

**Measured (this container):** b44 default selection (the 6-seg MST)
realizes **3627/bit vs 4510 (−19.6%)** — seg1 stops at its 1200 breakpoint,
its sibling aligns onto it, the interior trunk collapses to zero, and the
MST now realizes near its envelope floor, BEATING the 2-seg L (3715) and
matching the kWLSpread knob's re-selection (188,513) via the default
pipeline (188,682).  Corpus: mix / mempool_tile / channel_stress /
comprehensive / hbundles-10 **byte-identical** (the clamp fires only when a
breakpoint sits strictly inside the pull's travel); bigHalf no-rr overlaps
7→3 (opens 288→294, culls 72→138 — the usual plain-pipeline shuffle),
big2 plain overlaps 5→4 with opens 0→48; the standard healers absorb both
completely: big2+negotiate+ripup **0 opens / 4 overlaps** (main+heal 0/5),
bigHalf+healers **0/0**.  Full fast+mid tiers green (1434) with every
golden bit-identical.  The kWLSpread b44 delta collapsed to ~0.1% (its
test now asserts "not worse"); the knob remains the selection-level guard
for realization risk the clamp cannot remove (contention-driven stretch).

**Follow-ons (from the same b44 slide-range analysis; corpus-probed
2026-07-17, detailed below):** (a) opposite-pull connector pairs — a
structural realization-risk signal; (b) FREE-window open-space MST legs;
(c) planner books vs placed metal on pulled segments (planner-side, the
biggest upside — see [`wishlist-planner.md`](wishlist-planner.md) →
*"Charge pulled segments at their predicted pull target"*).

## Opposite-pull ("tug-of-war") connector pairs — a structural risk signal — DETECTOR SHIPPED (a); joint arbitration (b) OPEN

**Status (2026-07-20):** the structural DETECTOR — sketch (a) — is shipped as a
byte-identical diagnostic.  `buda_session.util.find_tug_of_war_pairs(segs)`
reads the already-derived ConnSeg data (each rider's `net_pull` + its junction
`at_pos` along T) and returns every outward (diverging) `(T, lo_rider, hi_rider)`
pair — a `−`-puller sitting strictly BELOW a `+`-puller on the same interior
segment.  It is surfaced in `dump_topologies --problems` (a `TUG(n)` bundle
flag, a per-pair detail line `tug: cand C segT stretched by segL(−)/segH(+)`,
and a `bundles with tug-of-war` summary count) on the SELECTED/displayed
candidate.  Read-only — never changes selection or placement.  Validated
against the canonical b44 repro (`TRUNK_H+MST@y11915` → `(5,1,3)`, separation
1250) with positive/negative/synthetic controls in
`test/tests/test_tug_of_war.py`.  Still OPEN: wiring the count into the
WL tie-break / a kWLSpread-style risk term (that makes it QoR-affecting — a
deliberate follow-on), and sketch (b) NUTS-side joint arbitration.

**What:** two connectors riding the same interior segment T, pulling in
opposite OUTWARD directions (the lower-position rider pulls down, the
higher pulls up).  Each pull locally shortens its own perpendicular leg;
jointly they stretch T between them.  The breakpoint clamp (above) bounds
each pull's overshoot, but the pair remains the structural signature of
realization risk: under contention (a breakpoint position occupied,
placement falling back) or where breakpoints leave a gap, the tug
re-opens.

**Repros (scanner over SELECTED candidates, post-plan):** `b44` — the
canonical: bundle 1 `seg5` tugged by `seg1(−)/seg3(+)`, nominal separation
1250 (pre-clamp realized +1133 of trunk stretch; post-clamp collapses to
0).  `bigHalf` — **7 pairs** on selected candidates alone, e.g. bundle 44
`seg5` by `seg1(−)/seg3(+)` (`TRUNK_H+MST@y5957`, sep 625) and bundle 31's
`MST_VH` with TWO pairs on one segment (`seg8` by `seg3/seg7(−)` vs
`seg1(+)`); `mix` 3 pairs, `mempool_tile` 6 (all on the big `MST_HV/VH`
trees), `big2` 0.

**Fix sketch:** detectable inside `derive_net_pull`'s own data (a segment
whose riders include both a −pull and a +pull at distinct positions) —
zero extra passes.  Uses: (a) a per-candidate count feeding the WL
tie-break or a kWLSpread-style risk estimate (cheaper than the envelope
descent, and STRUCTURAL — it names the mechanism rather than bounding
it); (b) NUTS-side joint arbitration — place the pair to minimize the SUM
(connector legs + T's stretch) instead of sequentially.  Effort: small
for (a); (b) touches placement order → full golden gate.

## FREE (sentinel) slide windows on open-space MST legs — FULLY CLOSED (both facets resolved 2026-07-20)

**Decoupled placement-clamp measurement (2026-07-20, round 2) — the direction
is a REGRESSION, not a win.** Following the round-1 lesson (below), the clamp was
re-implemented in the ONE consumer that still reads the raw sentinel — NUTS's
`build_nuts_maps` `slide_map` — bounding a FREE window to the topology's perp
extent there, leaving `derive_slide_ranges` (and thus the generation pinch gate)
completely untouched.  The decoupling worked exactly as intended: comprehensive_demo
**byte-identical**, b44 unchanged (candidate pools preserved — no pinch-gate churn).
But the clamp itself REGRESSES the congested design: `rnr/mix` `run_nuts` overlaps
15 → 20 and, decisively, the flow that ended **0 violations** now ends with **96
DNUTS violations across 7 bundles** the healers cannot clear (unplaced-bit peak
175 → 302).  The lesson is definitive: **the FREE window's die-wide slide freedom
is load-bearing for NUTS packing** — those open-space legs use the room to slide
clear of contention, and taking it away removes overlap-avoidance headroom exactly
where a congested design needs it most.  So the "de-randomize placement" goal is
misguided for the *placement* consumer; the placement window should stay free.
Reverted (mix back to baseline).

**Envelope facet — resolved as correct-as-is (2026-07-20), NOT a defect to fix.**
The other half of the item read "the WL envelope's `hi` clamps to the floorplan
extent (meaninglessly loose)."  On re-examination it is not loose-by-mistake — it
is CORRECT, and tightening it would be UNSOUND.  The envelope `hi` must be a valid
UPPER bound on where the segment can realize; the placement-clamp measurement above
just proved a FREE leg genuinely retains die-wide (≈fp-extent) slide freedom at
NUTS time, so `_seg_slide_box`'s existing fp-extent clamp is exactly the right
bracket.  Tightening it to the topology bbox would make `hi` SMALLER than the leg's
real reach — an invalid upper bound that would surface bundles as false
"out-of-envelope" (the very failure `_seg_slide_box`'s dogleg-override branch
exists to avoid).  It would also DEFEAT kWLSpread's purpose: a FREE-leg candidate
genuinely IS high-realization-variance, and kWLSpread *should* penalize it — the
"over-tax" is honest risk pricing, not a bug.  So there is nothing sound to change
here.

**Net — the item is fully closed:** the open-space-MST-leg "wildcard" is a FEATURE
(load-bearing for packing), and its loose envelope is the CORRECT bracket for that
genuine freedom.  The realization risk it does carry is surfaced, not clamped: the
tug-of-war detector (above) flags the opposite-pull pairs that make these trees
risky, now reported by BOTH `dump_topologies --problems` and `check_design`
(nuts/dnuts advisory).

**Round-1 measurement (2026-07-20):** the conservative form of the fix sketch below —
a Pass 4 in `derive_slide_ranges` clamping any still-sentinel bound to the
TOPOLOGY's own perp extent (min/max over every segment endpoint on that axis,
which includes an OOB trunk's detour coordinate, so it never excludes a
segment's own nominal perp) — was implemented and corpus-A/B'd, then REVERTED.
It correctly makes the windows finite (b44 `TRUNK_V_OOB+MST@x-246`: 5/9 FREE →
0/9, e.g. seg0 `[-246,2960]`, seg1 `[10250,12000]`), but it is NOT byte-neutral:
the clamped windows feed the **generation-time pinch/coverage gate**
(`filter_pinched` reads each candidate's `min_slide` from these same ConnSeg
windows), so a FREE window that was "infinitely slidable" (never pinched)
becomes a finite — sometimes zero-width — window and the gate now drops
different candidates.  Corpus effect: comprehensive_demo bundle 5 45 → 28
candidates (+1 planner warning); `rnr/mix` 1237 → 946 candidates, detailed WL
850633 → 726880 (−14.5%) BUT unplaced-bit peak 175 → 194 and a transient
`ovl 4` the healers then clear — a genuinely QoR-**ambiguous** mix of wins and
regressions, so per the delicate-zone protocol it was reverted to report-only.

**The real lesson:** the FREE→finite clamp cannot live in the shared
`derive_slide_ranges` analysis, because that one cached result feeds BOTH the
generation-time `min_slide` pinch gate (which must keep seeing today's
unbounded windows to preserve the candidate pool) AND the NUTS-interval / WL-
envelope consumers (which want the finite bound).  A correct fix must
**decouple** the two: bound the window only in the NUTS/envelope consumers
(or carry a second "realization window" field distinct from the generation-gate
window), leaving the pinch/coverage gate on the unbounded sentinel.  That is a
larger change than "a new rule in derive_slide_ranges" — the A/B above is the
evidence for scoping it that way.

**What:** an MST-edge leg that taps no face and crosses no block gets no
constraint from ConnTopology — its window stays the ±5e8 sentinel.
Consequences: the WL envelope's `hi` clamps to the floorplan extent
(meaninglessly loose, so kWLSpread over-taxes the whole candidate), NUTS
interval constraints balloon to near-die-width, and placement becomes a
wildcard driven purely by soft preferences.

**Repro (`b44.buda`, `select_topology 1 20` = `TRUNK_V_OOB+MST@x-246`):**
**5 of 9 segments are FREE**, with NUTS intervals like `[-246,5106]` and
`[9811,12739]` (essentially the die).  Placement scatters: seg0's nominal
perp is **−246** (out of bounds) but lands at **700** (a 946 shift), seg8
drifts 2700→3018.5, four segments collapse to zero span.  On this
uncontended die it realizes 5818 abstract (below the 7206 nominal — the
collapses ate the OOB detour) at 0/0, but the envelope reads
`[3514..51890]` and under ripup's OOB-class promotion these wildcards are
exactly what gets trialed into congested designs.  Second repro: cand 18
(`TRUNK_H_OOB+MST@y9445`, seg0 FREE, envelope hi 17220).

**Fix sketch:** derive a finite window for open-space legs from what they
connect — the junction partners' windows unioned with the bundle bbox
(+ detour band for OOB shapes).  Simultaneously makes the envelope `hi`
honest, shrinks the NUTS interval, and de-randomizes placement.  Effort:
medium — a new rule in `derive_slide_ranges`, full golden + fast/mid
re-verify.

## Non-TOP pin-access stub span-stretched onto its endpoint leaf — NO LIVE REPRO (effectively closed by config; latent NUTS clamp deferred)

**Status (2026-07-16):** the one live repro (flow 10's `x_t*`) was the M5-vs-M7
planner near-tie, and **PR #307** removed it at the source by correcting
`flow/tracks/tracks.buda`'s `M7` to `TOP` (M7 sits above M5/M6 TOP — a genuine
top metal, not a cheap offload target). A **full-corpus sweep**
(all **109** `flow`/`demo` `.buda` scripts that run `run_detailed_nuts` —
`rg -l run_detailed_nuts flow demo -g '*.buda'`, including the doubly-nested
`flow/big_data_test/big2/*.buda`; grepping for the `cull_keepout_crossers`
`"bit(s) removed"` WARNING) now finds
**0 flows with keepout culls** — the survivor span-stretch-onto-keepout event
fires nowhere in the corpus. Combined with the opt-in `nontop_dead_span_gate`
(PR #304) and the keepout-model audit (below), the class has no live repro.
The NUTS-side span-stretch clamp described under **Where to start** is therefore
a **latent** guard, deferred until a design re-creates the near-tie downgrade —
writing it now would touch the delicate `do_span_adjustments` path (big2's
coverage-invariant strand fix) with **no measurable win** and a real
host-sensitivity risk, against the measured-change discipline. If the class
recurs it is still loudly reported (`KEEPOUT_CROSS` + the DNUTS cull WARNING —
never silent), which is the trigger to land the clamp below.

**What (original repro, now config-closed):** A cross-block bus (flow 10's cross-chip `x_t*`, e.g. `x_t4`) routes as
a TOP-H trunk + two vertical stubs dropping into its endpoint leaf cells. The
generator hints a TOP-V layer (M5) for those stubs — TOP tiles leaves freely —
but the planner downgrades them to a non-TOP-V layer (M7) to save the
span-scaled `base_cost_non_top`. On the non-TOP layer the endpoint leaf is a
keepout; NUTS then span-stretches the stub to follow its trunk INTO the leaf
(trunk placed at y≈356, inside the leaf's y[340,470]), so the stub lands on the
keepout → `KEEPOUT_CROSS` → DetailedNUTS culls the pin-access bits (a silent
open, ≈22 bits across the `x_t*` buses). The M5-vs-M7 choice is a planner float
near-tie that flips under `-march=native`, so the residual is **host-sensitive**
(clean on the golden host, opens elsewhere — the companion **PR #281** host-
tolerances the flow's test via a `BUDA_NUTS_GOLDEN_STRICT` gate; until it lands
the flow test's strict assertions still fail on a non-golden host).

**Why deferred / why NOT a planner cost term:** the planner scores *nominal*
per-segment geometry; this crossing is a *post-placement* span-stretch event it
cannot see. Two builds confirmed it — an exclusive leaf-overlap penalty never
fires (the nominal stub only touches the leaf face), and an inclusive one
over-fires: it cannot separate a normal pin-access stub tapping a block face
(legitimately LOW, the `low_seg_obstructed` endpoint-tail trim / Gap A that keeps
the 80 intra-blk local buses on low layers) from the stretched `x_t4` stub, so it
pushes many stubs to TOP (net segments 1194→1282, matching the blunt global
`base_cost_non_top` knob) and trades the 22 keepout opens for ~6 different
packing-gap opens. So the fix locus is NUTS-side, not the planner.

**Where to start:** preferred fix is a **span-stretch clamp** — in NUTS
`do_span_adjustments`, when stretching a *non-TOP* segment, do not extend its
extent onto a leaf keepout on its layer; clamp at the leaf face (the bit places
at the face and vias to the TOP trunk, which crosses the leaf freely). Localized
to the span-adjust path, gated on non-TOP + keepout — but it touches the same
code that closed big2's strand, so it needs the full golden + fast/mid re-verify.
Alternatives (respect the generator's TOP hint for leaf-tapping stubs;
trunk-placement avoiding endpoint leaves) are noted in
[`../future/nuts_packing_gaps.md`](../future/nuts_packing_gaps.md) §4. Until one
lands the residual is bounded and loudly reported (`KEEPOUT_CROSS` +
`placed ON keepout` + the DNUTS cull warning — never silent).

## Abstract-vs-detailed keepout model mismatch — ✅ RESOLVED (audit closed)

**What (history):** the two stages disagreed about what a keepout blocks.
DetailedNUTS filtered tracks against keepouts at ONE along-coordinate — the
span midpoint — so a keepout overlapping the span but missing the midpoint
was invisible and bits routed straight through it with every metric clean
(channel_stress had always emitted 3 such illegal bit-wires); abstract
NUTS's exhausted-window fallback committed the interval centre ON a keepout
with no metric; and `keepout_occupied` silently ignored empty-`layer_ids`
zones that generation treats as blocking every layer.

**Resolution:** `RoutingGrid::signal_tracks_in_span` (span-aware track pool,
preferred; classic midpoint pool as fallback) +
`DetailedNUTSEngine::cull_keepout_crossers` (post-`adjust_bit_spans` cull of
bits whose FINAL span crosses a keepout — zero false positives, counted in
`num_unplaced`/`num_keepout_bits` with a WARNING) +
`NUTSResult::num_keepout_conflicts` (abstract report channel, WARNING) +
empty-`layer_ids` = blocks-all in `keepout_occupied`. The naive alternative
(hard-filter on the abstract span) was measured and rejected: 495 corpus
false positives vs the 3 real crossings. Full write-up:
[`keepout_model_audit.md`](keepout_model_audit.md); tests in
`test/tests/test_keepout_model.py`.

## Rename `check_connectivity` → `check_design` — ✅ RESOLVED

**What (history):** the command's name predated most of what it does. It
began as a pure connectivity audit (SEG/BUSTERM opens); today it also
checks layer-direction validity (`LAYER_DIR` — an unbuildable wire, not an
open) and keepout crossings (`KEEPOUT_CROSS` — an illegal placement, not
an open). "check_connectivity" undersold the audit and misled users into
thinking a Success verdict only covers opens.

**Resolution:** `check_design` is the primary command
(`src/buda_cmds/verify_viz_cmds.py`, handler `cmd_check_design`, session
method `_check_design`); `check_connectivity` stays registered as a legacy
alias mapping to the same handler (regression:
`test/tests/test_check_design_alias.py` asserts byte-identical output).
Rather than the originally planned opportunistic migration, all `flow/`,
`demo/`, `tools/`, and test call sites moved in the same pass, and the
printed strings were touched up with them: headers read "Verifying
{topology|NUTS|Detailed NUTS}-level design..." and the clean verdict is
"Success: no violations found." (`test_check_connectivity_hbundle.py` →
`test_check_design_hbundle.py`). Docs updated (`verify_viz.md`,
`BUDA_SCRIPT_REFERENCE.md`, `BUDA_CLI.md`, `USER_GUIDE.md`, CLAUDE.md);
historical/internal notes keep the old name (accurate via the alias).
Deprecating the alias with a one-line notice remains a far-future option.

## Verify is keepout-blind (`KEEPOUT_CROSS` violation type) — ✅ RESOLVED

**What (history):** `check_nuts` / `check_dnuts` (`src/verify.cpp`) audited
connectivity, layer direction, and unplaced bits, but never tested a placed
wire against keepouts — a segment the engine itself counted as a keepout
conflict got "Success: no opens found".

**Resolution:** `KEEPOUT_CROSS` violation type at both stages — nuts flags a
placed segment whose extent lies on a keepout overlapping its span (the
live exhausted-window commit, `count_keepout_conflicts` semantics per
segment); dnuts flags a bit inside a keepout with the cull's own predicate
(defense-in-depth — the cull prevents it in production). The checks take an
optional `zone_fp` (the floorplan the engine placed against) because a hier
bundle's resolved generation floorplan has no zones and would silently
bless real conflicts. Details:
[`keepout_model_audit.md`](keepout_model_audit.md) class 4; tests in
`test/tests/test_keepout_model.py` + the hbundles/10 flow test.

## Band-level repack for spread-fit overlap clusters — ✅ IMPLEMENTED

**What:** (Baseline updated 2026-07 — see `nuts_band_repack.md` §1: with
big2's new `signal_tracks` + negotiation flow the target is the
PRE-NEGOTIATION residue, 8 overlaps in 3 spread-fit clusters; repro test in
`test_big2_residuals.py`.)  After Gap A part 1 + TOP-layer load balancing,
big2 was down to 9 NUTS track overlaps. All 9 are **spread-fit** (the shared Hanan band has room for both
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

**Where to start:** the detailed, implementable design now lives in
[`nuts_band_repack.md`](nuts_band_repack.md) — cluster discovery over the
residual overlap graph, a `LayerSolver::repack_cluster` entry point over the
existing `try_repack` body (reachable since the PR #205 LayerSolver
extraction), guards, determinism, and the bounds-based gate plan.  In code:
`src/nuts.cpp` `repair_overlaps` + `LayerSolver::try_repack`;
`find_overlaps`/`segs_overlap` (`nuts_geom.h`) for cluster discovery. Verify
on `flow/big_data_test/big2/big2.buda`: the 9 residual overlaps
(M4×1, M6×4, M7×3, M2×1 — all spread-fit) should drop toward 0 with no new DNUTS
opens. NOTE: do NOT try to "balance" this away in the planner — evening the V
load (M5 9117 vs M7 5752) was measured to be counter-productive: it pushes load
toward M7 where the overlaps already sit and regressed DNUTS 60 -> 132 with the
overlap count unchanged. The residual is a packer problem, not a load problem.
See `docs/internal/planner_low_layer_over_cell.md`.

## PlacedSegmentBase + first-class pre-routes (Phase G) — ✅ IMPLEMENTED

**What:** The CLAUDE.md "Segment Type Hierarchy (target state)" unification:
a shared `PlacedSegmentBase{kind, layer, span, track_position, width, placed}`
under `TrackSegment` / `NetSegment` / the NEW `PreRoutedSegment` (label +
track_index), plus `RoutingGridStack::preroutes()` enumerating the non-SIGNAL
track slots as explicit objects and the stage-7 `draw_preroutes` visualizer
layer (per-type cycling toggle, works in the abstract view).  `BusSegment`
deliberately stays the stage-9 input descriptor — merging it with
TrackSegment would break bound names for zero behavior gain.

**Design + as-built resolution:**
[`placed_segment_preroutes.md`](placed_segment_preroutes.md).  Deferred
follow-ups tracked there: BDB pre-route rows / GDS rail export, exact
global-band splitting at pattern-override regions, a per-type button row,
and the binding-breaking Track/BusSegment merge.

## Pre-existing failure: `test_tighten_does_not_trade_pull_for_overlaps` — ✅ RESOLVED (PR #69)

**What:** This `mid`-tier test (`test/tests/test_nuts_pull_repack.py`) asserts the
`tc3a_flat` NUTS solve leaves `<= 2` abstract M7 overlaps, but it produced **3**
(`B59×B74 ×2`, `B56×B79`) — red on `main`.

**Resolution:** Fixed by PR #69 (net_pull only pulls endpoint-setting stubs).
Removing the spurious multicast-trunk pulls — and, with the busterm-tap endpoint
check, the interior-stub pulls — changed `tc3a_flat`'s placement enough that it
now lands at `<= 2` abstract M7 overlaps, so the test is green. The root cause was
exactly the over-applied `net_pull` that drove interior/non-binding stubs to their
slide bounds, congesting the layer; it was never a `tighten_pulls` regression.

## Hard co-placement of joined segments (seg-to-seg junctions) — ✅ RESOLVED

**What:** NUTS kept joined perpendicular segments connected by a *soft*
post-hoc span-extension (`do_span_adjustments`) rather than a placement
constraint, so a CPU-dependent track flip could open a zero-margin corner
(`tc3a_flat` bundle 48) that only DetailedNUTS recovered.

**Resolution (landed in stages):** joins became first-class
(`Topology::seg_conns`, topo-truth Phase 4; persisted at schema v12), the
FP-determinism stopgap + cross-layer coverage invariant made a junction unable
to *open* at NUTS regardless of host, and Part B added junction-aware
placement: a single-junction landing segment *prefers* a track its placed
partner's span already covers (junction-anchored preference in `place_seg`),
and a junction closable only by a large partner stretch is surfaced as a
structured `NUTSResult::junction_infeasibilities` entry consumed by
`ripup_reroute` as a re-pin contender. Design + history in
[`seg_junction_coplacement.md`](seg_junction_coplacement.md); tests in
`test/tests/test_junction_coplacement.py`.

## Override-boundary pattern resolution in span queries — ✅ RESOLVED

**What:** `RoutingGrid::signal_tracks_in_span` (and
`count_signal_tracks_in_span`, deliberately in lockstep) resolved the
track pattern by a single point sample — `effective_pattern_at` at the
along MIDPOINT and the window's `perp_lo`. The override region test is
boundary-inclusive, so an override whose edge exactly touched a Hanan row
claimed the ENTIRE band above that row for the query: a physically
healthy band read as supply-dead (0 tracks where 10 exist), and a window
crossing an override boundary read one pattern for both sides. Surfaced
while building the kPeak supply-floor test — a `y2=120` override turned
the healthy `[120,140]` detour band supply-dead and stranded the detour
route's 8 bits.

**Resolution (2026-07-12):** per the fix sketch — the perp window now
splits at the perp edges of every override whose along range contains the
span midpoint, and each slice resolves its pattern at the SLICE midpoint,
so a boundary-touching override claims only its own side (interior slice
boundaries half-open, window ends closed: a no-override query walks
exactly as before — goldens byte-identical, and no flow/demo uses
`add_grid_override`). Both public views now share ONE private walker
(`for_each_signal_track_in_span`), making the vector/count lockstep
structural rather than promised; DNUTS parity holds by construction (it
consumes `signal_tracks_in_span` directly). The along-midpoint
approximation is unchanged. Tests: the boundary-claim unit test in
`test_routing_grid.py` (the band above reads 10; a crossing window slices
to 1 + 10) and the end-to-end regression in
`test_planner_kpeak_supply.py` (the y2=120 detour now places all 8 bits,
previously a full strand).

## Audit 2026-07: single-layer rerun placement drift vs the full solve — OPEN (observation)

Seen while fixing C1-01 ([audit_2026-07.md](audit_2026-07.md), the
compounding rerun-layer interval shrink): with the intervals now stable,
`run_nuts_on_layer M4` on the 3-bus Z fixture still moves a trunk
(212.03 → 225.00) and lands one M5 overlap the full solve doesn't have —
repeat reruns are a fixpoint, so this is a one-time drift of the
single-layer re-solve's context (junction preferences see other layers
frozen; `resolve_corner_overlaps` is deliberately not run, per the
documented contract). Worth a look if the per-layer ↺ is ever used as a
quality tool rather than a what-if probe.

## Corner/repair at scale — the residual after the runtime arc — OPEN (three items)

Context: the 2026-07 runtime arc (PRs #506/#507/#509;
[rnr_runtime_parallelism.md](rnr_runtime_parallelism.md)) made the
repair loop's bookkeeping fully move-scoped — accept guards on overlap
DELTAS vs the pre-move snapshot, cycle exit on an exact repeated
placement state, settles on the moved set's follower closure.  On the
3813-segment congested synthetic that took `run_nuts` 195 → ~60 s
(`corner` 121 → ~33 s, `repair` 50 → ~20 s).  Gates: #506 and #509 are
byte-identical by construction; #507 is QoR-equivalent (a cycle's
cap-exit parity can change final placements — gated 0 better / 0
worse / 29 unchanged with zero wirelength movement).  What remains
inside `corner`/`repair` is genuine packing computation, and it only
matters at scale — **the two passes' instrumented buckets total
≤ ~0.2 s per corpus flow in the arc's solve-pass profiling** (e.g.
bigHalf ripup `corner` 0.04–0.15 s / `repair` 0.03–0.12 s, tc3a
0.03–0.06 s each, mix2 vehicles similar), so none of these carries
urgency; they are recorded for when a real large design makes them
live.

**(1) Span-indexed occupancy in `repack_members` — mechanical,
identity-preserving.**  The dominant residual is the cluster repack's
per-member obstacle scan: for each member, walk the LAYER's segment
list appending span-overlapping occupancy, then fit over the sorted
intervals (~4 ms per cluster attempt × ~280 attempts/round; the lazy
base-occupancy memo across the two pack modes already halved it —
lazy deliberately, the all-or-nothing placement-time pack aborts at the
first infeasible member and an eager build measured a net LOSS).  A
span-sorted per-layer index (or interval tree) would make each member's
scan O(overlapping) instead of O(layer).  Small constant already;
do only if big designs become routine.

**(2) Marginal-yield stop for improving repack rounds — QoR knob, add
on demand.**  The exact-state cycle guard (#507) kills true cycles, but
the IMPROVING rounds staircase with sharply diminishing returns
(measured per-round overlap clearances 597 → 321 → 61 → 6 on the
mid-size synthetic).  A "break when a round clears < X %" policy would
trade a small endpoint change for time on hopeless designs — the
`ripup_reroute` convergence guard's pattern (tc3a 56.8 → 16.3 s), and
like it, QoR-gated and only worth adding when over-capacity runtime
actually hurts someone.

**(3) Over-capacity classification — the conceptually real one.**  The
synthetic holds 16,386 overlaps NO repair can fix (band demand simply
exceeds signal-track supply), yet the passes still spend their ~50 s
trying: the cluster tier's spread-fit precheck skips provably
over-capacity CLUSTERS, but the per-pair sweep and the corner
constraint machinery churn regardless.  An up-front supply/demand
classification per layer/band — cap repair effort in regions that are
over capacity and report them as the planner's ALLOW_OVERFLOW problem,
which they are — would collapse most of the remaining at-scale cost
while being a no-op on feasible designs.  This is a RESPONSIBILITY
BOUNDARY item, not just a speed one: repair exists to fix the band
model's mispredictions, not infeasibility, and today it cannot tell the
difference.  Sketch: reuse the planner's per-band capacity bookkeeping
(or `count_signal_tracks_in_span` for the detailed-honest form) to tag
over-capacity bands before the repair loop; skip per-pair victims whose
pair sits wholly in a tagged band; report the tagged demand excess on
the `[NUTS]` summary line so the infeasibility is LOUD instead of
silently half-repaired.

## Pairwise-overlap stub alignment — heal SHIPPED opt-in (PR #557); DEFAULT-FLIP measured and REFUSED

**The artifact.**  Two same-net stubs straddling a trunk should align — share
ONE track window, so each bit runs as a single straight wire with no per-bit
trunk jog.  DNUTS's ordered-anchor placer (`place_by_layer`, "Option B")
sorts segments by `abstract_pos` and seats each at its OWN anchor; a later
same-net segment reuses an earlier one's tracks only if they fall inside its
own window.  Alignment is therefore OPPORTUNISTIC, and the processing order
is **not mirror-invariant**: on a mirror-symmetric floorplan the left pair
aligns while the right pair splits, because the mirror flips which stub is
placed first AND puts its self-centred placement at the far edge of the pair
overlap, just out of the partner's reach (the `seat_repro` right column:
u3 `[621..646]` vs l3 `[589..614]`, offset 32).

**What shipped (PR #557).**  `set_pair_align_heal on` — a MEASURED-ACCEPT
pass at `run_detailed_nuts`: re-solve with pair-align (restrict a stub's
track pool to the interval overlap it shares with same-bundle same-width
partners, then proactively adopt a placed partner's exact tracks), and KEEP
it only when unplaced/overlaps do not rise and detailed WL strictly drops.
Default OFF; scoped out of bottom-up (locked) sessions.  Raw study path:
`BUDA_DNUTS_PAIR_ALIGN` (unconditional, no accept).

**Why the accept is load-bearing — the measured record.**  The
UNCONDITIONAL form is corpus NET-NEGATIVE: **0 better / 7 worse / 30
unchanged**.  Restricting stubs to their overlap band concentrates same-net
wires and STARVES signal tracks on congested designs — every worse flow
strands MORE bits (`big.buda` 0/0/0 → 0/8/1; `mix2_fast_on_aligned_sql`
unplaced 16 → 68; four chip flows +8–12%) while corpus WL moves −0.04%.
This is the DUAL of the documented concentration loss
([`interval_pull_model.md`](interval_pull_model.md) "The spreader,
resolved"): spreading manufactures no keepout-clear tracks, concentrating
starves them.  With the accept the same mechanism measures **0 better / 0
worse / 37 unchanged** — it rejects all 7 regressors — with one accepted win
(`tc3a` detailed WL −0.02%) and the `seat_repro` right column aligning
(detailed WL −1.5%).

### The DEFAULT-FLIP question — MEASURED AND REFUSED (2026-08-02)

Both bars were measured.  Neither is met, and the prerequisite that was
supposed to make a flip affordable is **refuted**.  Recorded here so the
question is not re-opened from intuition.

**Bar 1 — a vehicle where the heal accepts on >1 bundle at ≥0.5% WL: NOT
MET.**  Across the 37-flow corpus exactly ONE flow accepts (`tc3a`,
detailed WL **−0.02%**), an order of magnitude under the bar.  The heal's
real market — uncongested designs with misaligned pairs, like the
`seat_repro` (−1.5%) — is barely represented in the corpus, and no corpus
vehicle stands in for it.

**Bar 2 — the rejected-solve cost: SMALL, and smaller than the first
measurement suggested.**  The cost is one extra full DNUTS solve on each
ELIGIBLE flow — 31 of the 37, since the 6 bottom-up vehicles hold
`hier.locked` bundles and the heal returns before solving (Codex #563).
Measured warm, that solve is **0.05–2.1% of the flow's wall time**
(`big` 32ms/1.5s = 2.1%, `big2` 19ms/1.2s = 1.6%, `bigHalf` 26ms/51.7s =
0.05%, `mix` 6.5ms/5.0s = 0.13%).

*Correction to the first pass:* this bar was initially reported as "+3.3%
corpus wall time" from a single heal-on/heal-off corpus run.  That number
is NOISE, not signal — the 6 locked flows are an accidental CONTROL GROUP
(they cannot pay the solve at all) and they moved **+4.2%** while the 31
eligible flows moved +2.5%.  Single-run wall time on this corpus cannot
resolve a per-flow cost this small; the warm microbenchmark above can, and
it is the number to trust.

**The prerequisite is refuted: the "cheap pre-check" cannot work.**  The
idea was to skip the solve when no same-bundle same-width pair has a
non-degenerate interval overlap that is not already sharing tracks.  Built
and measured on 12 flows (`_pair_align_candidates`, since REVERTED — it
was a measured loss, not shipped):

| | flows | pre-check vs one solve |
|---|---|---|
| candidates == 0 (it CAN skip) | 3 of 12 (`b44`, `05_stress_grid`, `10_chip_units`) | 0.56–1.67× — breaks even at best |
| candidates > 0 (skips nothing) | 9 of 12 | **2.5–48× the solve, pure added cost** |

Two independent reasons it fails:

1. **It does not discriminate.**  Real flows are full of same-bundle
   interval-overlapping same-width pairs that are not sharing tracks — 2 to
   24 of them on the flows above — including every flow where the heal is
   ultimately REJECTED.  "Is there an alignable pair" is simply not the
   question; the question is "would aligning them shorten wire", and that is
   what the solve computes.  A predicate over pairs can never answer it.
2. **It is not cheap.**  The check must read per-BIT placement to know
   whether a pair already shares tracks, and a Python pass over that data
   costs multiples of the C++ DNUTS solve it is trying to avoid.

**Verdict: keep it opt-in — on the absence of BENEFIT, not on cost.**  The
cost is real but small (above); what is missing is any reason to pay it.  A
flip would add a solve to 31 flows so that ONE gains 0.02% wirelength, and
the pre-check that would have made even that free is refuted.  The heal
stays exactly what it is — safe by construction (the accept means a flow
that turns it on can never be made worse), and correct to enable per design
when you have the geometry it targets.  If a corpus vehicle ever meets Bar 1,
the cost side is cheap enough that the flip becomes a straightforward yes.

### The #8b follow-on — half 1 SHIPPED: the WL-gain predictor (2026-08-05)

Narrower than the refuted pre-check, and either half suffices; **half 1 is
built**:

1. ✅ **A cheap WL-gain predictor** (NOT a pair predicate) —
   **`DetailedNUTSResult.pair_misalign_wl`**: the C++ solve reports, as a
   byproduct (its own `pass_seconds["pair_misalign"]` bucket — a single
   pass over the placed bits), the total per-bit trunk jog across
   pair-align PARTNER segments (lever A's predicate verbatim: same layer +
   bundle + bit count, overlapping intervals, anchored, not
   timing-critical).  That jog is the wirelength an aligning re-solve
   TARGETS, so `_final_pair_align_heal` skips its re-solve outright when it
   is zero — a no-gain case now costs nothing, which is what the refuted
   Python pre-check could not deliver (it answered "is there a pair", cost
   2.5–48× the solve; this answers "is there wire to win", costs ~nothing
   because the solve computes it in passing).  Two calibration facts,
   measured on the seat repro: the prediction is an OPTIMISTIC bound on the
   targeted gain (the pair aligns mid-overlap, so realized = predicted/2
   there — 128 vs 256), and it bounds only the TARGETED gain: an accept
   arising purely from side effects of the restricted pools would be
   forgone.  Tests: `test_dnuts_pair_align.py` (predictor value, zero-skip
   without a solve, bound vs realized, accept print carries the
   prediction).
2. **A partial/warm DNUTS re-solve** scoped to the affected bundles — the
   stage-9 analogue of `NUTSEngine::rerun_bundle_warm` — remains unbuilt;
   with half 1 shipped it is only needed if the PAYING flows' solve cost
   ever matters (the predictor already zeroes the no-gain flows).

**What this does and does not change.**  The heal (opt-in) is now cheaper:
enabling it on a design without misaligned pairs costs nothing.  The
DEFAULT-FLIP stays refused — Bar 1 (a real corpus benefit) is still not
met, and the predictor does not manufacture benefit; it only removes cost
on the no-gain side.  The flip question is re-opened in the narrow sense
the open stated: its cost side now depends on how many corpus flows carry a
POSITIVE jog (those still pay one solve under a flipped default).

**Predictor census (2026-08-05, 41-flow corpus, heal off — the baseline
jog a flipped default would read):** predictor cost 0.0–5.5 ms/solve (the
5 ms end is the ~40k-bit chip flows) — noise against the solves.  Among
the 31 heal-ELIGIBLE (non-locked) flows, **14 read jog = 0 and would skip
the solve entirely; 17 would pay one**.  `tc3a` — the corpus's only
accepting flow — reads jog = 17391 > 0, so the gate never blocks the one
measured win.  (The 10 LOCKED flows' first-census zeros were an artifact —
the bottom-up merge initially dropped the field, Codex P2 on #594.
Re-measured with the carry fix, ALL TEN read positive jog (205.5 on
`mix2_fast_bottomup_caps_2x` up to 330k on the chip bottom-up vehicles),
so under a flipped default none of them would skip — and the gate's
load-bearing property holds for them too: never falsely zero when there is
wire to win; per-flow numbers in the PR.)  The 17 paying flows are dominated by the
congested vehicles where the unconditional form REGRESSED (big 748k, chip
flows 374–555k of jog the accept would refuse to chase), i.e. large jog on
a congested design is precisely where alignment strands bits and the
accept rejects — which is why the jog is a SKIP gate, not a ranking
signal.  Full per-flow table in PR #594.
