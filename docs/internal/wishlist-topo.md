# Wishlist — Topology generation & connectivity

Deferred follow-ups for topology generation (`src/topology.cpp`) and the
connectivity model (`src/conn_topology.cpp`). Index: [`wishlist.md`](wishlist.md).

See also [`mst_edge_realization.md`](mst_edge_realization.md) — trunk-tail
tightening and the per-edge MST L/Z DOF (avoiding the 2ᴺ candidate explosion),
grounded in the current generator code with a measured prototype result.

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

## `multi_trunk` as a default — MEASURED, keep opt-in

**What.** `generate_topologies multi_trunk` (opt-in) emits two-level
`BITRUNK_HVH/VHV` datapath trees. On column/row-aligned datapaths the planner
selects them and QoR improves substantially (col 3×5×6 WL −7.5 %, ov 3→1; col
4×5×8 WL −31.9 %, ov 11→1; row 3×5×6 WL −17.7 %, ov 1→0; see
[`mst_edge_realization.md`](mst_edge_realization.md)). Question: flip it on by
default?

**Measured (default off vs on, over flows that do NOT already opt in).** The
corpus is every flat flow + non-datapath demo + comprehensive_demo, each run with
its `generate_topologies` line as written (no keyword) vs with `multi_trunk`
forced on — so the comparison actually isolates the *default*.
- **QoR-neutral everywhere on the corpus** — identical abstract WL, overlaps and
  unplaced on tc3a_flat, channel_stress, four_blocks(+_3_bundles), dogleg1/2,
  b4_bus_077, and comprehensive_demo. **Zero regressions.**
- **The two datapath demos are excluded from this on/off measurement**: they
  hardcode `generate_topologies multi_trunk` (`flow/datapath_multi_trunk.buda`,
  `flow/datapath_row_vhv.buda`), so they run multi_trunk in *both* configs and
  measure nothing about the default. They are the *win* case (their headers
  document the plain-vs-multi improvement), not a neutral data point — see the
  substantial QoR gains quoted under **What** above.
- **Runtime cost negligible** on tc3a (`generate_topologies` 0.16 s both ways).
- **But zero corpus BENEFIT** (none of these are datapaths) and a real
  **candidate-count cost**: tc3a_flat 2571 → 2797 candidates (+8.8 %), b4 17 → 20,
  four_blocks 60 → 64. That compounds with the BDB candidate-topology persist
  path (see [`wishlist-bdb.md`](wishlist-bdb.md) — persistence, not generation,
  is the large-design bottleneck), so default-on taxes every big hier design for
  benefit only datapaths see.
- The earlier size sweep also found a couple of datapath shapes where multi_trunk
  *loses* (sparse `row 4×5×8` +7.7 % WL; saturated `col 2×6×6` +3.2 % WL) — so
  default-on is not universally safe even on its target class.

**Decision: keep it opt-in.** The benefit is real but confined to datapaths,
where the flag is the right mechanism; default-on would add candidate-count /
persist cost to every design for no corpus gain, with residual loss risk on some
datapath shapes. Revisit only if a datapath becomes a common default workload.

## Incremental re-analysis (topo/conn unification Phase D) — DEFERRED BY MEASUREMENT

**Context.**  The topo/conn unification
([`topo_conn_unification.md`](topo_conn_unification.md), all other phases
implemented) cached the six-pass derived analysis on the `Topology` itself,
validated by content fingerprint.  A mutation therefore costs exactly ONE full
recompute of ONE candidate on its next build — measured at ~10µs for small
topologies — and interactive editing (the TopoEdit ops) performs one mutation
at a time, so the planned dirty-set machinery would optimize a cost that is
already negligible.

**Wish.**  Scoped re-analysis on top of the cache: mutators report a dirty set
of segment indices; the per-neighborhood passes (`derive_conn_segs`,
`derive_net_pull`, `derive_along_flex`) re-run on the dirty closure and the
fixpoint slide passes on the dirty segments' connected component, gated by a
fuzz property test (incremental == full rebuild, field-for-field) and shipped
behind a flag.  Full design: `topo_conn_unification.md` §7.

**Trigger.**  Revisit only if TopoEdit profiling on very large candidates
(hundreds of segments) shows the recompute in an interactive loop; the Phase 0
byte-identity harness (`tools/topo_snapshot.py` goldens +
`test_topo_analysis_golden.py`) is the acceptance gate.

## Unify the 2-pin vs n-pin filter ordering

**Context.**  The two generation paths order their post-emission stages
differently (mapped in [`topo_conn_unification.md`](topo_conn_unification.md)
§1 and called out as a deliberate non-goal in its §12): `generate_2pin` culls
keepout-blocked candidates POST-emission (after sorting, before
`filter_pinched`), while `generate_npin` pre-filters trunk LOCI and has no
post-emission cull at all — MST/BITRUNK segments see keepouts only via the
per-edge `choose_edge_h_first`.  Annotation timing is split the same way
(batch `annotate_endpoints` for trunk/L/Z/U vs self-seeding inside the MST/
hybrid/BITRUNK builders), and `connected_block_names` fills at different
points relative to `filter_pinched` — the ordering accident the PR #194
review traced through the abutment fallback.

**Wish.**  One shared post-emission pipeline (emit → annotate → keepout cull →
pinch → coverage) both paths flow through, so a filter fix or a new gate lands
once instead of per-path.

**Cost/risk.**  This CHANGES ROUTING BYTES (candidates culled at different
stages survive differently), so unlike the unification's phases it needs its
own deliberate before/after review — the corpus diff is mechanical now:
re-baseline `tools/topo_snapshot.py` + `tools/wl_corpus.py` and review the
golden diff bundle by bundle.

## Resolve pre-planner hier slide columns against the cell-local floorplan

**Context.**  `dump_topologies` slide-derived columns — `mslide`
(`_topo_min_slide`, `src/buda_session/reports.py`) and the `wl[lo..hi]`
envelope's upper bound (`_topology_wl_interval`, `src/buda_session/nutsflow.py`)
— build each candidate's `ConnTopology` against `self.fp`, the **absolute**
floorplan.  A **cell-level HBundle template** dumped *before* `run_planner hier`
is still in cell-local coordinates, so its block faces don't resolve against
`self.fp`; ConnTopology leaves every segment's perpendicular slide unbounded
(the ~2e9 sentinel).  PR #215 made that honest at the display layer — the column
prints `free` instead of the raw sentinel, and the doc tells the reader to dump
*after* `run_planner hier` for real slide/envelope numbers — but the underlying
value is still unresolved until the planner expands the template into
per-instance absolute wrappers.

**Wish.**  Build the cell-level template's `ConnTopology` against its
**cell-local floorplan** — the same floorplan `generate_hier_topologies` already
constructs when it generates those candidates — rather than `self.fp`, so a
pre-planner hier dump shows correct finite slides (and a real envelope `hi`)
without needing to plan first.  `mslide` and `wl[lo..hi]` would then be
meaningful the moment candidates exist, matching the flat flow.

**Why deferred / cost.**  Read-only reporting affordance, not a routing
correctness issue — the `free` display + doc note (PR #215) already prevent the
misread, and the numbers are correct post-planner regardless.  The real work is
plumbing the per-template cell-local floorplan (or a way to reconstruct it from
the bundle's `cell_context`) out to the reporting path, which today only holds
the absolute `self.fp`.  `generate_hier_topologies` builds that local floorplan
transiently during generation; making it available at dump time means either
persisting it per cell-level bundle or rebuilding it on demand from the BDB cell
definition.

**Where to start.**  `generate_hier_topologies` (the cell-local case, hier
topology generation) is where the local floorplan is built — capture/rebuild it
keyed by `cell_context`; then have `_topo_min_slide` / `_topology_wl_interval`
pick the cell-local floorplan for a cell-level template and `self.fp` for an
already-absolute bundle.  Gate: the flat flow and post-`run_planner hier`
numbers must be byte-identical (this only *adds* resolution to the pre-planner
hier case), plus a test that a pre-planner hier template dump now shows finite
`mslide` instead of `free` (the inverse of
`test_mslide_unbounded_prints_free_not_sentinel`).


## Corner-margin default `dx=dy=0` — MEASURED, keep 0 (corner-touch gap ✅ resolved)

**Question.**  The global corner margin (`BlockCornerMargin`, `topology.h`)
defaults to `{0,0}` — "no constraint beyond the face extent".  Nothing recorded
*why* 0 is the default (only what it means).  Two experiments settle it.

**Experiment 1 — global `corner_margin dx 1 dy 1` over the `wl_corpus` corpus.**
Baseline = flows as written; experiment = strip any standalone `corner_margin`
and force the global default to `1 1` (per-block `add_block … corner_margin`
overrides kept).

| Flow | abstract WL | detailed WL | overlaps | unplaced |
|---|---|---|---|---|
| tc3a_flat | +0.6% | +0.4% | 0→0 | 0→0 |
| comprehensive_demo | +0.1% | — | 0→0 | — |
| channel_stress | +0.3% | +0.3% | 0→0 | 0→0 |
| four_blocks * | 0.0% | 0.0% | 0→0 | 0→0 |
| four_blocks_3_bundles | +8.8% (430→468) | — | 0→0 | — |
| dogleg1 ** | −0.5% | −0.7% | 0→0 | 0→0 |
| dogleg2 ** | −0.5% | −0.3% | 0→0 | 0→0 |
| double_detour | (flow runs no `run_nuts`) | — | — | — |
| b4_bus_077 | +0.0% (3221→3222) | 0.0% | 0→0 | 0→0 |
| mix (hier) | +1.6% | +2.6% | **3→2** | 0→0 |

\* four_blocks already sets `corner_margin 1 1` — an identical control.
\** dogleg1/2 baseline is `2 2`, so this row is a `2→1` *reduction* (less margin
   → slightly shorter), not a clean `0→1` — not representative of the default.

- **Safe:** no new overlaps and no new unplaced bits anywhere; the hier `mix`
  flow even *improved* by one overlap (3→2) as the 1-unit inset nudged endpoints
  off a contended band.
- **But not free:** a small, consistent WL increase on the genuine `0→1` flows
  (~+0.1 %..+0.6 % flat, +1.6 %/+2.6 % on hier `mix`) — endpoints pulled a unit
  off block corners make stubs/trunks a hair longer.  (`four_blocks_3_bundles`
  +8.8 % is 38 units on a 430-unit toy design — a small-design artifact.)
- **No broad benefit:** the single `mix` overlap relief is the only upside, and
  it came *with* a WL cost.  Everywhere else it is neutral-to-slightly-worse.

**Decision: keep the default `0`.**  It is the permissive identity; a margin is
an opt-in tightening for a *specific* design where corner congestion is real (as
`dogleg1/2` do at `2 2`), not a global default worth a WL tax for no general win.

**Experiment 2 — corner-only touching blocks are a real generation gap at `0`.**
Two blocks that meet at a single corner:

```
source tracks/tracks.buda
add_block u1   0   0 100 100
add_block u2 100 100 200 200      # shares only the point (100,100) with u1
add_bus b[8] u1 u2
run_bundler
generate_topologies               # dx=dy=0 → ZERO candidates (bus unrouted)
# corner_margin dx 1 dy 1         # → 5 candidates, routes cleanly
```

- **`dx=dy=0` → 0 candidates.**  At margin 0 the busterm faces meet only at the
  corner point, so every generated L/U segment is degenerate/pinched and dropped
  by `filter_pinched` / the coverage gate — the bus is left unrouted.
- **`dx=dy=1` → 5 candidates** (`L_HV`, four `U`), routing cleanly (0 overlaps,
  16 bit-wires placed, 0 unplaced).  Shrinking the bboxes (u1→`[1,1,99,99]`,
  u2→`[101,101,199,199]`) opens a 2-unit gap so the faces no longer share a
  point and positive-length segments survive.

So the margin knob is not merely cosmetic: a tiny inset *rescues* a degenerate
placement the default cannot route.

**Follow-up — ✅ RESOLVED.**  Corner-only-touching blocks are now rescued at
generation *independent of the `corner_margin` knob* — the same way a fully shared
edge is (`ABUT_H`/`ABUT_V` + `kAbutmentSpanEpsilon`, PR #197).  The 2-pin fallback
(`generate_candidates`, `topology.cpp`) now detects a single-corner touch and emits
two `CORNER_HV`/`CORNER_VH` candidates by reusing the MST path's `corner_diagonal_L`
(an L routed *around* the shared corner, each leg tapping a real face with slide
room), so the bus routes at the default `corner_margin 0`.  Fully-coincident /
overlapping-with-no-channel blocks correctly stay candidate-free (the zero-candidate
warning fires — the intended flag for a degenerate placement).  Gated `wl_corpus`
byte-identical across all 10 flows (the branch only fires when a bundle would
otherwise have *zero* candidates) + regressions in `test_topo_abutment.py`
(`test_corner_touch_rescued_by_diagonal_L`, `test_corner_touch_bus_routes_to_completion`,
`test_fully_coincident_blocks_produce_no_candidate`).

## Persist the overlap-U perp clamps (`Segment::perp_clamp_lo/hi`) — ✅ RESOLVED

The corner-wrapping overlap U's (`U_OVL_*`/`UU_OVL_*`, PR #224) carry
generation-supplied per-segment perpendicular slide clamps
(`Segment::perp_clamp_lo/hi`) that pin each face-tap arm to its exclusive band and
each detour arm outside the union bbox.  They are **load-bearing for correctness**
(without them NUTS collapses a wrap through the overlapped block); Codex on #224
flagged that a U_OVL persisted to a BDB and resumed via `load_pipeline` **before**
NUTS would reload unclamped and could collapse again.

**Resolved (option 1 — persist):** `perp_clamp_lo/hi` are now `topology_segment`
columns (**BDB v16**), written by all three persist sites (flat + hier + regen) and
restored by `load_pipeline`, mirroring the `edge_id` (v14) round-trip exactly.  They
stay **out of the topology fingerprint** (deterministic from geometry, so an
identical-geometry cache hit already implies an identical clamp — no uid churn), and
pre-v16 rows migrate to the INT_MIN/INT_MAX unclamped sentinels (correct for every
non-U_OVL segment).  Round-trip + resume-before-NUTS regressions live in
`test_bdb_topology_persist.py`
(`test_overlap_u_perp_clamp_persists_and_roundtrips`,
`test_overlap_u_perp_clamp_survives_load_pipeline_resume`).

While here, confirmed `edge_id` persistence (v14) was already complete end-to-end
(schema + all persist sites + `load_pipeline` + `test_topo_resume_analysis.py`); the
stale "NOT YET PERSISTED" note on `topology.h Segment::edge_id` was refreshed.

Option 2 (re-derive on load, no schema change) was considered and set aside: the
persist path is a direct mirror of the existing `edge_id` machinery, so it is the
lower-risk, self-documenting choice.
