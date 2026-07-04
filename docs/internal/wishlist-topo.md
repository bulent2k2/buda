# Wishlist — Topology generation & connectivity

Deferred follow-ups for topology generation (`src/topology.cpp`) and the
connectivity model (`src/conn_topology.cpp`). Index: [`wishlist.md`](wishlist.md).

## True along-flex trunk DOF (Stage C of the flexible-root re-arch)

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
- Add along-endpoint flex/anchored flags + a coverage floor (+ an `along_pull`) to
  `ConnSeg` (`src/conn_topology.h`) — **DONE in Stage A** as
  `along_flex_lo/hi` + `along_cover_lo/hi` + `along_pull`, computed in a new
  `compute_along_pull()` (the `along_lo`/`along_hi` names were already taken by the
  segment's current extent, and a spine's two endpoints move independently, so a
  single signed pull like `net_pull` could not model them).
- Teach NUTS `do_span_adjustments` / `tighten_pulls` (`src/nuts.cpp`) to contract a
  spine end toward the pull-optimal coordinate even at a mid-junction, never past a
  busterm-face anchor or a pass-through coverage requirement.  **Blocked** — see the
  measurement verdict below: the regressions the flip introduces are upstream of
  NUTS, so this NUTS-only step does not unblock always-on on its own.

**Payoff.** The flexible-root span could then be **always-on** (not just
`double_detour`): trunks would generate tight, gain slide room from the DOF, and
contract to minimal honest wirelength — eliminating the ranking-inflation that
forced the `double_detour` gate, and letting the planner prefer the region-4
pass-through trunk on its merits. Also unlocks always-on generation of the
"region-4" pass-through trunk (e.g. `TRUNK_V@x5772` in
`flow/big_data_test/big2/b4_bus_077.buda`) instead of only under `double_detour`.

### Stage A — SHIPPED (inert ConnSeg data model)

The along-flex DOF is now a first-class field set on `ConnSeg`
(`src/conn_topology.h`): per-endpoint flex/anchored flags (`along_flex_lo/hi`), the
nominal along-coverage floor (`along_cover_lo/hi`) a flex end may contract down to,
and a signed `along_pull` WL hint — all computed by `compute_along_pull()`
(`src/conn_topology.cpp`).  It is deliberately **inert** (no NUTS consumer yet):
the WL corpus (`tools/wl_corpus.py`) is byte-identical to baseline across all 10
representative flows, and the fast tier is green.  This is the foundation for
whichever of the paths below is taken next.

### Measurement verdict (2026-07): the always-on flip is NOT ready, and the NUTS-only DOF is insufficient to make it so

Before building the Stage-B NUTS contraction, we ran the decisive experiment: flip
both generation gates (`topology.cpp:1442/1842`) to **always-on** and measure.  The
result contradicts the premise that the flip is a clean, DOF-fixable win:

- **Zero wirelength benefit.**  The 10-flow WL corpus (`tools/wl_corpus.py`) is
  **byte-identical** to baseline with always-on generation.  The real routed
  designs do not get tighter — the only concrete payoff is *enabling* the region-4
  trunk without the `double_detour` keyword (which already routes cleanly *with*
  it), not better interconnect.
- **3 genuine routing regressions** (fast+mid tier: 15 tests move — 1 pure-gate
  assertion, 11 clean-but-changed selection goldens, **3 real regressions**):
  1. `test_planner4_keepout_overflow_forces_detour` — planner **overflow 0→27 / 0→17**
     and a **new NUTS overlap** (M6, B1×B3): the tighter always-on trunk spans no
     longer fit the planner's reserved bands.
  2. `test_nuts_busterm_face_anchor::test_big2_b4_b24_routes_cleanly` — **48 bits
     unplaced** (was 0) + a new interval violation + 96 connectivity opens.
  3. `test_planner_low_over_cell::test_big2_no_low_layer_over_cell_dumping` —
     **2 LOW-over-cell dumps** (was 0): a bus dumped onto M3 with 0 signal tracks
     (the "Gap A" symptom returns).
- **The wishlist's proposed DOF cannot fix these.**  All 3 regressions are
  **planner-time / detailed-NUTS-time** effects of the tighter *generated* span
  (band overflow, track shortage).  The Stage-B DOF lives in NUTS
  `do_span_adjustments` — a **post-selection, post-planning** span contraction.  It
  cannot undo a planner overflow that already occurred, nor add signal tracks to a
  starved band.  Contracting the placed span at NUTS does not make the *generated*
  tight span fit the planner in the first place.

**The DOF itself is a structural no-op (the deeper finding).**  Using the Stage-A
fields we probed every bundle's *selected* topology across the corpus
(`tools/along_dof_probe.py`) for a flex end whose along-coverage floor
(`along_cover_lo/hi`) sits strictly INSIDE the segment's generated extent — i.e.
genuinely removable "dead wire" (excluding pass-through coverage).  The answer is
**0 across the whole corpus, both gated and under the always-on experiment.**
Reason: generation already lands every spine endpoint exactly on its extreme
stub/coverage — a topology's span never exceeds *its own* coverage floor — so a DOF
that contracts a span *to its own coverage* has nothing to remove in any topology
BUDA emits.  The WL difference the wish imagined (far-face vs. centerline) is a
**stub perp-position** matter already handled by `net_pull`, not a **spine-endpoint
along-extent** one.  So the along-flex DOF as scoped would **save wirelength in zero
unit tests / flows**.

**Re-scoped blocker.**  Making the flexible span always-on safely is therefore not
about a NUTS along-contraction at all — it needs a **planner-aware** flex span: the
congestion planner must reserve the trunk's **minimal/contracted** extent (treating
the endpoints as a range) rather than the wide generated span, so a flex trunk stops
overflowing bands it does not actually need.  That touches `CongestionPlanner`'s band
reservation (`rebuild_cuts_` / demand charging), well beyond the ConnSeg+NUTS scope
the original wish assumed.  Until that exists the `double_detour` gate stays — it is a
correct guard, not an accident.  Stage A's data model, the ConnSeg python bindings,
the WL corpus harness (`tools/wl_corpus.py`) and the dead-wire probe
(`tools/along_dof_probe.py`) are all in place so that larger effort can be measured
from its first commit.
