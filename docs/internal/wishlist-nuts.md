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

**Follow-ons (from the same b44 slide-range analysis):** (a) the
opposite-pull connector pair (seg1/seg3) is detectable at analysis time —
a cheap structural realization-risk signal that could feed the WL
tie-break; (b) open-space MST edge legs carry FREE (sentinel) slide
windows (b44 cand 19's edge leg) — unbounded envelopes and NUTS wildcards;
(c) the planner's charged band vs pull placement can diverge by >1000
units (seg3 charged at band 1450, placed at 2641.5) — a books-vs-metal
mismatch worth a diagnostic.

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
