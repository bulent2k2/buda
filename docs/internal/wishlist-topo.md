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

**The DOF saves 0 WL on any *selected* route — the removable dead wire lives only in
never-selected candidates.**  Using the Stage-A fields we probed every bundle's
topologies across the corpus (`tools/along_dof_probe.py`, `--verbose` for per-hit
detail) for a flex end whose along-coverage floor (`along_cover_lo/hi`) sits strictly
INSIDE the generated extent — i.e. genuinely removable "dead wire" (pass-through
coverage excluded).  Two scopes:
- **Selected topologies: 0 dead wire, every flow, gated and under the always-on
  experiment.**  Generation already lands the *winning* candidate's spine endpoints
  exactly on their extreme stub/coverage, so a NUTS-time DOF that contracts a span to
  its own coverage has nothing to remove on what actually routes.
- **All 4539 candidate topologies: ~790 k units of dead wire — concentrated entirely
  in `TRUNK+MST` / `TRUNK_OOB+MST` hybrids.**  These carry a genuine dangling trunk
  overshoot (e.g. `four_blocks` b2 `TRUNK_H+MST@y125` seg0 spans x=[99,151] but
  connects only at x=99 — 52 units of tail attached to nothing).  It is connected at
  one end (not an open), just wasteful — and that waste inflates the candidate's
  wirelength, which is precisely *why* the planner ranks these hybrids below the tight
  candidates and never selects them.

So the DOF's real leverage is **not** "save WL on the committed route" (there is none
to save) but "de-inflate loose MST-hybrid candidates so they could compete" — a
**ranking / selection** effect (the churn risk), not a wirelength saving on committed
routes.  And a NUTS-time DOF cannot even do that: ranking uses the *generation-time*
WL estimate, before NUTS runs.  A simpler, safer alternative surfaced by the probe is
to **tighten the MST-hybrid trunk endpoints at generation** (drop the dangling
overshoot at emit time) — independent of any DOF — which would make their WL honest
without a NUTS mechanism.  That is a scoped follow-up, noted here, not taken in this PR
(it touches exactly the candidates the always-on experiment showed cause selection
churn, so it needs the same WL-corpus gate).

**Re-scoped blocker.**  Making the flexible span always-on safely is therefore not
about a NUTS along-contraction — it needs either (a) honest generation-time trunk-tail
tightening (above), and/or (b) a **planner-aware** flex span: the congestion planner
reserving the trunk's minimal/contracted extent (endpoints as a range) rather than the
wide generated span, so a flex trunk stops overflowing bands it does not need
(`rebuild_cuts_` / demand charging).  Both are well beyond the ConnSeg+NUTS scope the
original wish assumed.  Until then the `double_detour` gate stays — it is a correct
guard, not an accident.  Stage A's data model, the ConnSeg python bindings, the WL
corpus harness (`tools/wl_corpus.py`) and the dead-wire probe
(`tools/along_dof_probe.py`, selected + all-candidate scopes) are all in place so that
larger effort can be measured from its first commit.
