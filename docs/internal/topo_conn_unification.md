# Unifying `topology.h/cpp` and `conn_topology.h/cpp`

A phased re-architecture plan: fold the two modules into one data model with a
cached, invalidation-tracked analysis layer — **byte-identical at every phase**,
with the consumer API (planner, NUTS, DetailedNUTS, verify, wirelength report,
Python bindings) frozen — and, on top of that foundation, **incremental
topology generation with an expert user in the loop**.

Grounded in a full call-site audit of both modules (consumer map + generation
seams), summarized in §1–§2. Line numbers are from the tree at the time of
writing; treat them as anchors, not gospel.

## 1. Diagnosis — what the two modules actually are today

| | `Topology` (topology.h/cpp) | `ConnTopology` (conn_topology.h/cpp) |
|---|---|---|
| Role | **Authoritative model**: segment geometry + logical connectivity (`seg_busterms`, `seg_conns`) + block/bridge/feedthru metadata | **Derived analysis**: per-segment slide windows (`perp_lo/hi`), pass-through tightening, relay-tap pinning, `net_pull`, along-flex DOF |
| Truth | Yes — generated once, persisted logically (BDB schema v9/v12; see `single_source_topo_truth.md` Phases 1–5) | None — `build()` is a pure function of `(Topology, Floorplan)`; `infer_connections` reads the two maps and never re-derives |
| Cost | — | Rebuilt with **no caching** at every consumption point (see below) |

Every `ConnSeg` field is either (a) a re-encoding of `Segment` geometry
(`horiz`, `layer_id`, `along_lo/hi`, `perp_pos`), (b) a read of the two
authoritative `Topology` maps (`conns`), or (c) a derived constraint computed
from those plus the floorplan (`perp_lo/hi`, `net_pull`, `along_flex_*`,
`along_cover_*`, `along_pull`). There is no independent truth in ConnTopology.

**Rebuild frequency** (B = bundles, C ≈ candidates/bundle):

- Generation: ≥2C builds/bundle (`filter_pinched` at topology.cpp:3100 +
  `filter_uncovered` at :2919), plus the three mid-generation gates
  (`conn_seg_components` :939, `topology_is_connected` :2057,
  `topology_is_clean_tree` :2237) each re-deriving `seg_conns` on a local copy,
  plus 2 builds per connector-drop trial in `complete_relay_junctions`'
  de-overlap pass (:1355).
- Planner: `plan_bundle` builds once per candidate (congestion_planner.cpp:661)
  × every escalation rung × every ripup/negotiate trial (`replan_bundle`,
  `replan_bundle_ripup`).
- NUTS: `build_nuts_maps` once per bundle (nuts.cpp:87), re-run per dogleg trial
  and per `rerun_layer`.
- CLI: `_run_detailed_nuts` re-does the identical per-bundle builds **in
  Python** (buda_cli.py:2704) right after NUTS did them in C++;
  `_persist_bundle_vias` (:541), `dump_topologies` (`_topo_min_slide` :2388,
  per candidate), `check_connectivity` (:5293, per candidate in `all` mode).
- Viz: per redraw of the explorer (`_build_conn_topo` buda_viz.py:891) and per
  bundle in `draw_buses`/`draw_nuts_tracks` (:3360/:3407).

The `single_source_topo_truth.md` migration already moved *connectivity* to
generate-once / consume-everywhere / persist-logically. What is still split
across the module boundary is the *derived analysis* — which is why the two
modules feel like one system in two files: `ConnTopology` is `Topology`'s
analysis pass, living in a separate class that every stage re-runs from
scratch.

**Unification thesis:** don't merge two data models — there is only one. Turn
`ConnTopology` into a **cached, invalidation-tracked analysis of `Topology`**,
expose each internal pass as a re-runnable unit, and keep the existing class as
a thin façade so the external API is untouched.

## 2. The frozen API surface (verified by call-site audit)

Must not change, byte-for-byte:

- `ConnTopology::build(const Topology&, const Floorplan&)`, `segs()`,
  `trunk_mst()`; `ConnSeg` (all 14 fields), `SegConn`, `MSTEdge`; free
  `manhattan_nearest`, `seg_bbox`, `compute_mst` — bound 1:1 in
  `bind_nuts.cpp:40-83`, ~360 references across `test/tests/`.
- `check_topo` / `check_nuts` / `check_dnuts` signatures (`verify.h:58/64/71`)
  — all take `const ConnTopology&` first; verify reads only `horiz`,
  `perp_pos`, `along_lo/hi`, `conns` (never `perp_lo/hi` / `net_pull` /
  along-flex).
- **De-facto contracts**:
  - `segs()[i]` ↔ `topo.segments[i]` 1:1 — every consumer indexes this way.
  - The unbounded-slide sentinel magnitude (`INT_MIN/2` / `INT_MAX/2`) leaks
    into hard-coded constants: `_SLIDE_SENTINEL = 1e8` (buda_cli.py:2396),
    `kSentinel = 5e8` (nuts.cpp:146), `kSentinel = INT_MAX/2`
    (congestion_planner.cpp:664), `_UNCONSTRAINED` (buda_viz.py:672).
  - Conn iteration order: `infer_connections` deliberately reproduces the
    retired geometric scan's conn ordering (conn_topology.cpp:144-146) because
    NUTS `rev_conn_map`/`align_map` tie-breaks depend on it.
  - The planner/NUTS override escape hatches — `plan.seg_net_pull`
    (INT_MIN = use computed), `plan.seg_slide_lo/hi` (NaN = use computed),
    `seg_perp` (congestion_planner.h:99-107, :233) — the NUTS dogleg path
    pins these to correct what a fresh derivation would get wrong.
- Already insulated by construction: **DetailedNUTS** consumes pre-digested
  `BusSegment.connections`/`busterm_faces` built by the CLI (buda_cli.py:2726-
  2742) and never touches ConnTopology; **report_wirelength** sums placed
  `TrackSegment`/`NetSegment` spans only (`_wirelength_by_bundle`
  buda_cli.py:1660).

## 3. Phase 0 — byte-identity harness (build this first)

Every later phase ships only when this gate is green against the pre-phase
baseline:

1. **wl_corpus** — `tools/wl_corpus.py` output byte-identical (the project's
   existing gate for generation changes).
2. **Flow logs** — `test_flow_scripts.py` diffs stderr + flow-log text; freeze
   the `[TopoGen]` / warning strings (any wording change is its own commit).
3. **route_snapshot hashes** — the per-stage content hashes persisted to the
   BDB (`_persist_route_snapshot`, buda_cli.py:491) for the corpus flows.
4. **New: candidate-list golden snapshot** — a `dump_topologies`-based dump
   (type strings, WL, segment coords, `min_slide`, `--conn` detail) for every
   bundle of every corpus flow, committed as a golden file. This is the surface
   the refactor touches most directly; the existing gates only sample it.
5. **New: analysis-equivalence property test** — for every corpus candidate,
   assert the new-path `ConnSeg` vector is field-for-field identical to a
   pinned copy of the old `build()` (kept in the test tree during the
   migration, deleted in Phase F).

Two latent nondeterminisms to document, not fix (fixing changes bytes):
`annotate_and_sort` uses `std::sort` keyed `(estimated_wirelength, type)` —
ties are effectively impossible because type strings embed coordinates, but it
is not `stable_sort`; MST edge sort ties (equal `dist`) break by generation
order (topology.cpp:2128).

## 4. Phase A — re-house the analysis as named passes (byte-identical, mechanical)

Move the six passes out of `ConnTopology`'s private methods into free functions
in a new `topology_analysis.{h,cpp}` (topology.cpp is already ~3.1k lines;
don't grow it):

```cpp
// each takes (const Topology&, const Floorplan&, std::vector<ConnSeg>&)
derive_conn_segs(...);     // geometry re-encode + infer_connections
derive_slide_ranges(...);  // compute_slide_ranges
tighten_passthrough(...);  // tighten_passthrough_ranges
pin_relay_taps(...);       // pin_relay_tap_connectors
derive_net_pull(...);      // compute_net_pull
derive_along_flex(...);    // compute_along_pull
```

`ConnTopology::build` becomes a 6-line driver calling them in the exact current
order (conn_topology.cpp:54-59). `conn_topology.h` keeps the class + structs
(API frozen); `conn_topology.cpp` shrinks to the façade.

*Acceptance: harness green; no caller changes anywhere.*

## 5. Phase B — cache the analysis on the Topology (byte-identical, the performance payoff)

Add to `Topology` a copyable analysis cache plus a revision counter:

```cpp
struct TopoAnalysisCache {
    std::shared_ptr<const std::vector<ConnSeg>> segs;  // shared so segs() stays const&
    uint64_t topo_rev = 0;   // revision it was computed at
    uint64_t fp_rev   = 0;   // floorplan revision
    bool     valid    = false;
};
mutable TopoAnalysisCache analysis_cache_;
uint64_t rev_ = 0;           // bumped by every mutator
```

- **Revision discipline** — every mutation entry point identified in the audit
  bumps `rev_`: `emit_tap_segment`, `prepend_segment`, `erase_segment`,
  `annotate_endpoints`, `annotate_seg_conns`, `annotate_topology`,
  `complete_relay_junctions`, `flip_mst_edge`, `offset_topology` (fresh copy ⇒
  fresh rev), NUTS `apply_dogleg` (nuts.cpp:2139 already re-annotates — the
  bump rides the same call), and the **pybind setters** for
  `segments`/`seg_busterms`/`seg_conns` (bind_routing.cpp:208-219). The setter
  bump closes today's silent-stale-annotation hazard: a Python field assignment
  invalidates the analysis instead of silently serving stale slides.
- `Floorplan` gets the same one-line `rev_` bump in its mutators (blocks,
  keepouts, margins, min-stub, feedthru, detour channel).
- `ConnTopology::build` first checks the cache: hit ⇒ share `segs`; miss ⇒ run
  the Phase A passes and store. Same algorithm, same inputs — byte-identical by
  construction, proven by the Phase 0 property test (cache-hit == recompute).
- Cache is a **value member**: `Topology` copies carry a valid cache (correct —
  same content), mutations invalidate per object. No global registry, no keying
  problem across the candidate vectors that are copied/moved everywhere.

*Payoff (from the frequency audit): `filter_pinched` + `filter_uncovered` + the
mid-generation gates collapse to one build per candidate; planner escalation
rungs and every ripup/negotiate trial stop rebuilding all B bundles (only the
mutated one recomputes); NUTS dogleg trials and the Python DetailedNUTS prep
become cache hits. Roughly a 3-6× reduction in analysis work per pipeline pass
and per ripup trial.*

## 6. Phase C — one analysis, everywhere (byte-identical, deletion of duplicates)

With the cache in place, retire the structural duplications — no consumer
signature changes:

- `conn_seg_components` / `topology_is_connected` / `topology_is_clean_tree`
  stop hand-re-deriving `seg_conns` on local copies mid-generation; they
  annotate + build through the same cached path.
- The CLI's `_run_detailed_nuts` keeps calling `ConnTopology().build(...)`
  (bound API unchanged) — now a cache hit against the build NUTS just did.
- `dump_topologies`, `_persist_bundle_vias`, `check_connectivity`, and both viz
  paths likewise become hits.

*Acceptance: harness green; a perf smoke (time `ripup_reroute` on big2) shows
the expected drop; no Python-visible behavior change.*

## 7. Phase D — incremental re-analysis (byte-identical by property, flag-gated)

Localized mutations shouldn't invalidate the whole analysis:

- Mutators report a **dirty set** of segment indices (dogleg: split trunk + jog
  + touched stubs; edge flip: the two legs; a segment drag: that segment + its
  `seg_conns` partners).
- `derive_conn_segs`, `derive_net_pull`, `derive_along_flex` are
  per-segment-neighborhood and re-run only on the dirty closure. The slide
  passes (`derive_slide_ranges` + `tighten_passthrough` + `pin_relay_taps`)
  contain a fixpoint loop; re-run them on the dirty segments' **connected
  component** (cheap — components are already derivable from `seg_conns`).
- **Correctness gate**: a fuzz property test — random mutation sequences on
  corpus candidates; the incremental result must equal the full rebuild
  field-for-field. Incremental mode ships behind a flag until the fuzzer has
  soaked; the default flip is its own commit with the full harness.

## 8. Phase E — incremental generation with the expert in the loop

The feature layer the re-arch exists to unlock. Four building blocks, in
dependency order; each is usable on its own.

### E1 — stable candidate identity (`topo_uid`)

Today pins die on regeneration because identity is a list index:
`_reset_plan_for_regen` (buda_cli.py:2352) nukes pin/plan state, and sidecars
fall back to a warned `topo_index_hint` (:1063). Introduce a content key — the
type string already embeds coordinates; add a short hash over
`(segments, seg_busterms)` — carried in the BDB `topology` table and the
sidecar. Pins, per-segment layer overrides, and dogleg bookkeeping re-attach by
uid after any regeneration; the index becomes display-only. While touching that
schema, also persist `Segment.edge_id` (the documented round-trip gap,
topology.h:66-71), so a reloaded candidate keeps per-edge flip identity.

### E2 — additive generation (`generate_more_topologies <hint> [knobs…]`)

Run selected generators — a specific trunk locus, `multi_trunk`,
`double_detour`, a user-suggested Hanan line — and **append** deduped
candidates (dedup by uid) instead of replacing the list. Per-bundle generation
knobs persist in the BDB so the next bulk `generate_topologies` doesn't
silently revert them. Existing pins survive by uid — the expert accretes a
candidate pool across sessions.

### E3 — first-class edit transactions (`TopoEdit`)

A small C++ API (bound to Python, driven from the topology explorer) wrapping
each supported edit — move a segment within its slide window, move a trunk
locus, `flip_mst_edge` (exists), add/remove/extend a stub, insert a jog,
re-layer (exists as the sidecar override) — as:

    mutate → structural-annotation maintenance (the erase_segment /
    prepend_segment discipline) → scoped re-analysis (Phase D) → immediate
    check_topo + pinch + coverage verdict

Every edit is validity-checked live and undoable (the `flip_mst_edge`
involution, relied on by ripup undo at buda_cli.py:3172, is the template).
This turns today's "an expert *can* hand-edit `topo.segments` from Python but
silently corrupts annotations" into a supported loop.

### E4 — user-authored candidates

`add_candidate <bundle>` promotes a hand-built/edited topology into the pool
through `annotate_topology` + the standard gates, flagged `user` (exempt from
`filter_uncovered`'s drop but never from its warning), pinned by uid. The
explorer's existing pin / layer-cycle / "Re-run & Refresh" loop
(`_rerun_all`, buda_cli.py:2240) then gives plan→NUTS→DNUTS feedback per edit —
with Phases B–D making that loop seconds, not a full-design recompute.

## 9. Phase F — documentation + cleanup

Update `CLAUDE.md` (Stage 2 + connectivity sections) and
`BUDA_SCRIPT_REFERENCE.md`; extend `single_source_topo_truth.md` with
"Phase 6: derived analysis unified"; delete the migration-era pinned copy of
the old `build()` from the test tree.

## 10. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Python `def_readwrite` mutations bypassing invalidation | All setters bump `rev_` (Phase B); pybind returns copies for field reads, so in-place aliasing from Python isn't possible today (verified in the binding audit) |
| Cache aliasing across `Topology` copies (candidate vectors are copied/moved everywhere) | Cache is a value member: copies carry a valid cache (same content), mutations invalidate per object |
| Fixpoint slide passes resisting scoped re-run | Phase D re-runs them per connected component, not per segment; fuzz gate; flag-gated rollout |
| Hidden ordering dependencies (conn order, sort ties) | Frozen explicitly in Phase 0 items 4–5; the conn-order contract is already documented at conn_topology.cpp:144-146 |
| Log-string diffs breaking flow tests | Strings frozen in Phase 0; any wording change is its own commit with test updates |

## 11. Sequencing and size

Phases 0→A→B→C are strictly ordered, each independently shippable and
byte-identical (small/medium each). D is medium and flag-gated. E splits into
four increments (E1 uid → E2 additive gen → E3 edits → E4 user candidates),
each usable on its own — **E1 alone already fixes the biggest expert-loop
pain** (pins dying on regeneration). Nothing in A–D changes a single consumer
signature; E is purely additive API.

**Deliberate non-goal:** unifying the 2-pin vs n-pin filter *ordering* (the
keepout cull runs post-emission in `generate_2pin` but pre-emission on trunk
loci in `generate_npin`, with no post-emission cull for MST/BITRUNK shapes —
a known fragility). That is a real cleanup but it changes routing bytes, so it
stays out of this plan and gets its own before/after corpus review.
