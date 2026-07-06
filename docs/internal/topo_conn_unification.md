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
4. **New: candidate-list golden snapshot — IMPLEMENTED**
   (`tools/topo_snapshot.py` + `test/tests/data/topo_golden/` +
   `test_topo_analysis_golden.py`): a canonical generation-stage dump of every
   corpus bundle's full candidate list — topology (type, WL, segments,
   seg_busterms, seg_conns, bridges) **and** its complete derived analysis
   (all 14 `ConnSeg` fields incl. raw slide sentinels, plus the ordered conns
   list), so items 4 and 5 gate through one file. Large flows (tc3a_flat,
   rnr/mix) commit per-bundle sha256 digests instead of multi-MB text — the
   gate is equally hard, mismatches localize to a bundle, and the reviewable
   diff comes from regenerating the full snapshot on the baseline tree.
   Generation stage only, deliberately: candidate geometry + analysis are pure
   integer arithmetic (machine-stable), while post-NUTS dogleg mutations ride
   on float placements documented to diverge across CPUs.
5. **New: analysis-equivalence property test — IMPLEMENTED** as part of item 4
   (the `ana` lines of the golden are the pinned `ConnSeg` output; any refactor
   that changes one derived value or one ordering fails with a line diff).
   Later phases add the cache-hit == recompute and incremental == full-rebuild
   properties on top of the same serializer.

Two latent nondeterminisms to document, not fix (fixing changes bytes):
`annotate_and_sort` uses `std::sort` keyed `(estimated_wirelength, type)` —
ties are effectively impossible because type strings embed coordinates, but it
is not `stable_sort`; MST edge sort ties (equal `dist`) break by generation
order (topology.cpp:2128).

## 4. Phase A — re-house the analysis as named passes (byte-identical, mechanical) — IMPLEMENTED

> Shipped as `src/topology_analysis.{h,cpp}`; verified byte-identical
> (topo_golden fast+mid, wl_corpus A/B vs the pre-change tree, full fast tier).

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

## 5. Phase B — cache the analysis on the Topology (byte-identical, the performance payoff) — IMPLEMENTED

> Shipped with one deliberate strengthening over the design below: the cache
> validates by **content fingerprint**, not revision discipline. Topology is an
> open struct — its fields are assigned freely in C++ generators (e.g.
> `generate_2pin` assigns `connected_block_names` *after* `filter_pinched` has
> already built an analysis) and via the pybind setters, so a bump protocol
> would silently break on any missed site. `analyze()`
> (topology_analysis.h) re-fingerprints the analysis inputs (FNV-1a over
> segments, seg_busterms, seg_conns, connected_block_names, feedthru, bridges)
> on every call and recomputes on mismatch: a stale cache is structurally
> impossible, no mutator or binding needs changes, and the §11 pybind-bypass
> risk is retired by construction. The Floorplan side kept the rev design
> (uid fresh on copy + rev bumped by every mutator — all its state is behind
> methods, so the discipline is airtight there). The fingerprint is also the
> natural precursor of E1's persisted `topo_uid`. ConnTopology holds an
> immutable shared snapshot (built-before-mutation semantics preserved).
> Gated by `test_topo_analysis_cache.py` + the full Phase 0 harness +
> wl_corpus A/B.

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
  bump rides the same call), and the **pybind setters** for *every `Topology`
  field the analysis reads* — not just `segments`/`seg_busterms`/`seg_conns`
  but also `connected_block_names` (consumed by `tighten_passthrough_ranges`,
  conn_topology.cpp:397/:411) and, defensively, the remaining readwrite fields
  (`bridge_segments`, `feedthru_blocks`) so a future analysis pass reading them
  can never be served a stale cache (bind_routing.cpp:208-219). The rule is
  "any setter bumps", enforced by a unit test that mutates each bound field and
  asserts invalidation. The setter bump closes today's silent-stale-annotation
  hazard: a Python field assignment invalidates the analysis instead of
  silently serving stale slides.
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

## 6. Phase C — one analysis, everywhere (byte-identical, deletion of duplicates) — IMPLEMENTED

> With the Phase B cache validating by content, every consumer's existing
> `ConnTopology().build(...)` call became the shared path with **zero call-site
> changes** — the planner's per-candidate builds, NUTS `build_nuts_maps`, the
> CLI's Python-side DetailedNUTS prep, persist, check, and viz all hit the
> same `TopoAnalysis`. Measured on full flows: computes ≈ 2×candidates (the
> `filter_pinched` build plus one recompute after the deliberate post-filter
> `connected_block_names` assignment — moving that assignment would change
> routing bytes, so it stays), everything else hits — e.g. channel_stress 928
> computes / 814 hits, dogleg1 51/50; a cache-hit build is ~4× cheaper than a
> recompute even on a 9-segment candidate (hit cost = the fingerprint scan).
> The structural invariants (computes ≤ 2×ncand + slack, hits ≥ ncand across a
> full flow) are pinned by `test_flow_level_cross_stage_reuse`.

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

## 7. Phase D — incremental re-analysis (byte-identical by property, flag-gated) — DEFERRED BY MEASUREMENT

> With Phase B in place, a mutation costs exactly ONE full recompute of ONE
> candidate on its next build — measured at ~10µs for small topologies (the
> whole six-pass run), and interactive editing (E3) performs one mutation at a
> time. The dirty-set machinery below would optimize a cost that is already
> negligible, at the price of a fuzz-gated parallel implementation of the
> fixpoint passes. Revisit only if E3 profiling on very large candidates
> (hundreds of segments) shows the recompute in an interactive loop; the
> design below stands ready.

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

### E1 — stable candidate identity (`topo_uid`) — CORE IMPLEMENTED

> Shipped (schema v14): `topology.topo_uid` (hex of the Phase B content
> fingerprint, canonicalized over seg_busterms so an in-memory all-junction
> `(nullopt, nullopt)` entry — semantically identical to a missing one —
> fingerprints like the persisted form) + `topology_segment.edge_id` (the
> documented round-trip gap, closed). `buda.topo_uid(topo)` is bound; all
> three persist sites write the uid; `load_pipeline` restores `edge_id` and
> verifies uid integrity (pre-v14 checkpoints backfill silently, a v14
> mismatch prints a lossy-checkpoint warning). Round-trip gated by
> `test_topo_uid_roundtrip` + the resume test now comparing at full fidelity
> including edge_id (multicast trunk+MST coverage). Fixtures regenerated
> (schema-bump-only diff). E1b is also in: the sidecar carries
> `topo_uid` (explorer writes it; older sidecars without it keep resolving via
> type+WL → index hint), `_apply_selections` and the explorer resolve
> uid-first, and `_reset_plan_for_regen` re-attaches a pin by uid across all
> five regeneration paths (flat + hier) — a user's selection now survives a
> knob-tweaked regeneration (`test_topo_uid_pins.py`).

Today pins die on regeneration because identity is a list index:
`_reset_plan_for_regen` (buda_cli.py:2352) nukes pin/plan state, and sidecars
fall back to a warned `topo_index_hint` (:1063). Introduce a content key — the
type string already embeds coordinates; add a short hash over **all
load-bearing persisted topology state**: `segments` (coords + layer_hint +
is_jog + edge_id), `seg_busterms`, `seg_conns` (the authoritative junction
oracle — two candidates can share geometry/taps but differ in routed
connectivity), `bridge_segments` (real routed metal, counted in wirelength,
topology.cpp:797-803), `feedthru_blocks`, and `connected_block_names` — carried
in the BDB `topology` table and the sidecar. Anything less risks a uid
collision where `generate_more_topologies` dedup drops a distinct candidate or
pins re-attach to the wrong topology. Pins, per-segment layer overrides, and
dogleg bookkeeping re-attach by uid after any regeneration; the index becomes
display-only. While touching that schema, also persist `Segment.edge_id` (the
documented round-trip gap, topology.h:66-71), so a reloaded candidate keeps
per-edge flip identity.

### E2 — additive generation (`generate_more_topologies <hint> [knobs…]`)

Run selected generators — a specific trunk locus, `multi_trunk`,
`double_detour`, a user-suggested Hanan line — and **append** deduped
candidates (dedup by uid) instead of replacing the list. Per-bundle generation
knobs persist in the BDB so the next bulk `generate_topologies` doesn't
silently revert them. Existing pins survive by uid — the expert accretes a
candidate pool across sessions.

### E3 — first-class edit transactions (`TopoEdit`) — ENGINE IMPLEMENTED

> Shipped as `src/topo_edit.{h,cpp}` + bindings, with the operation set
> specified by the user: **pick axis + pick Hanan line + add trunk**
> (`edit_add_trunk(horiz, perp_pos, …)` — default span = the Hanan extent on
> that axis), **override span** (`edit_set_span`; slide override is the
> existing `plan.seg_slide_lo/hi` NUTS hatch — per-plan state, deliberately
> not topology content), **add/remove stub** (`edit_add_stub` seeds the tap
> exactly like the generators — margin-inset bbox, multi-rect, TEG — and
> claims the block; `edit_remove_segment` rides the erase_segment re-key
> discipline), and **connect/disconnect perpendicular segments**
> (`edit_connect` moves the nearest free endpoint to the crossing and extends
> the partner when needed, refusing to move busterm taps; `edit_disconnect`
> retracts the landing endpoint to a given coordinate, so geometry and
> junction records always agree).  Every op returns an `EditVerdict`
> (check_topo violations + zero-slide pinch + **SEG-graph component count** —
> added because check_topo audits taps/faces/touch but not whole-graph
> connectivity, so a span retraction that splits the tree would otherwise
> pass).  A failed op leaves the topology untouched; undo = value snapshot
> (uid-verified).  Gated by `test_topo_edit.py` incl. a from-scratch
> trunk+stubs build reaching `ok()`.  E3b `.buda` commands are IN
> (`edit_topology` opens a transactional working copy — deep-copied, since
> pybind candidate elements alias pool storage — `edit_add_trunk/add_stub/
> set_span/connect/disconnect/remove_segment` apply with printed verdicts,
> `edit_status`, `edit_commit [pin]` appends as a uid-deduped `USER` candidate
> — the E4 entry point — and `edit_abort` discards; gated by
> `test_edit_commands.py` incl. a scripted hand topology routed end-to-end
> through planner+NUTS with zero violations).  REMAINING (E3c): explorer GUI
> wiring (edit mode driving these same ops interactively).

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

## 10. Impact on BDB persistence and resume/rehydrate (`load_pipeline`)

The persistence baseline this plan builds on: `_persist_topologies`
(buda_cli.py:346) is a **wipe-and-rewrite** — `clear_topologies()` then
re-insert keyed by `(bundle_id, cand_index)` with `is_selected`/`is_pinned`
columns; connectivity persists logically (`topology_seg_busterm` v9,
`topology_seg_conn` v12, `topology_bridge_segment` v11); and
`_load_pipeline_from_bdb` (buda_cli.py:660) rehydrates candidates with
`load_seg_busterms` while **slide ranges and net_pull are deliberately
recomputed** ("the planner/NUTS/ConnTopology re-derive slide ranges and
net_pull from geometry + Floorplan — those are recomputed, by design",
:672-675). Not restored: `seg_perp`, planner band state, doglegs.

### Phases A–D: no schema change, three obligations

1. **The analysis cache is transient — never persisted.** It is a derived
   view, excluded from the BDB for the same reason slide ranges are excluded
   today (the one-true-source principle: persisting a derivation invites
   divergence). `route_snapshot` hashes are unaffected because persisted bytes
   don't change.
2. **Reload must participate in the revision discipline.** The load path
   mutates topologies through the pybind setters (`t.segments = segs`,
   `t.connected_block_names = …` — buda_cli.py:744/:756) which bump `rev_`
   after Phase B, but `load_seg_busterms` (bind_routing.cpp:130) mutates from
   C++ — it gets its own bump. A freshly rehydrated topology therefore always
   computes its analysis on first `build()`, exactly like a freshly generated
   one.
3. **The Phase 0 harness gains a resume leg — IMPLEMENTED**
   (`test_topo_resume_analysis.py`): checkpoint, reopen, `load_pipeline`,
   continue. Asserts (a) a **reloaded** candidate's full analysis is
   byte-identical to its in-memory twin (the golden serializer's bytes,
   extending `test_seg_busterm_persist.py`'s check_topo-equivalence to the
   complete `ConnSeg` set; `edge_id` excluded until E1 persists it), and
   (b) **resume determinism**: two independent resumes continuing with the
   same `run_nuts` produce identical `route_snapshot` fingerprints and route
   the same segment/via population as the original session. Note the original
   session's fingerprint may legitimately differ from a resumed one — the
   planner's `seg_perp` placement preference is deliberately not persisted —
   so determinism-of-resume, not original==resumed, is the invariant.
   Phase D's incremental mode must likewise leave persisted rows identical to
   a full rebuild — covered by the fuzz gate plus the `apply_dogleg` →
   re-persist path.

### Phase E: the schema-bearing changes (one version bump, ~v13)

- **E1 uid round-trip.** `topology.topo_uid` column + `topology_segment.edge_id`
  column (closing the documented round-trip gap, topology.h:66-71). The uid
  hash covers exactly the **persisted** load-bearing fields (§8/E1) — a
  deliberate constraint, because it makes the uid *recomputable from a
  checkpoint alone*: pre-v13 BDBs need no migration — `load_pipeline` backfills
  the uid deterministically from rehydrated content, announcing itself like the
  pre-v12 `seg_conns` fallback does (bind_routing.cpp:156). Hash the canonical
  persisted-row encoding (not in-memory iteration state) and add a round-trip
  regression: `uid(generated) == uid(reloaded)`, sibling of
  `test_seg_busterm_persist.py`, including the diffable `*.bdb.sql` round-trip.
  New columns must also flow through `tools/bdb_serialize.py` and the
  `test/tests/data/build_fixtures.py` regeneration per `bdb_test_data.md`.
- **E1 pins.** `is_selected`/`is_pinned` stay as-is (persisted rows always
  describe the current list); uid reattachment happens in memory at
  regeneration time. The sidecar gains a uid field, and `_apply_selections`'
  matching chain becomes uid → type+WL (today's primary) → warned index hint.
- **E2 additive generation — IMPLEMENTED** (`generate_more_topologies
  <hint> [knobs]`): appends knob-produced candidates deduplicated by uid,
  leaving indices/pin/plan untouched; idempotent per knob set; re-persisted
  through the existing path (the in-memory pool is the full truth, so the
  rewrite stays correct). Landing this surfaced a uid refinement: `type` /
  `trunk_location` / `pass_through_count` joined the fingerprint — distinct
  shapes can realize identical segments, and persisted identity must tell
  them apart (for the cache this only makes validation finer-grained).
  REMAINING of E2: uid-keyed upsert persistence + `topology.source` column
  (needed by E4's user candidates) and per-bundle `gen_knobs` persistence.
- **E2 ends wipe-and-rewrite.** Additive generation and user candidates are
  incompatible with `clear_topologies()`: persistence moves to a per-bundle
  **upsert keyed by uid** — regeneration deletes only rows absent from the new
  list *and* whose `source` is `generated`. New `topology.source` column
  (`generated` | `user` | `dogleg`): bulk regeneration can never delete a
  user-authored candidate (E4), and `_adopt_doglegs`' appended split candidate
  — today transient — persists honestly as `dogleg`. Per-bundle generation
  knobs persist in a `bundle.gen_knobs` JSON column so a resumed session's bulk
  `generate_topologies` honors them.
- **E3 edits.** An edit changes content ⇒ a **new uid** (uid is content
  identity, not lineage). The edit transaction re-persists the candidate row +
  annotations through the existing `_persist_topology_annotations` choke point,
  carries the pin to the new uid, and rewrites the sidecar. A `parent_uid`
  lineage column (undo/history across sessions) is a possible later extension,
  deliberately deferred.
- **Hier.** Expanded instances persist only their selected topology at the
  template `cand_index` (load remaps to the compact in-memory index,
  buda_cli.py:728-731) — unchanged. An instance copy's uid differs from its
  template's (offset coordinates), which is correct: uid reattachment operates
  at **template** level, where sidecar pins already live; instance rows keep
  their existing `parent_id` linkage.
- **`load_pipeline` extension checklist**: rehydrate `topo_uid` (or backfill),
  `source`, `gen_knobs`, `edge_id`; everything else is unchanged. The "not
  restored: doglegs" note stays true — doglegs remain recomputed; E1 merely
  lets their bookkeeping re-attach when the same candidate reappears.

## 11. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Python `def_readwrite` mutations bypassing invalidation | All setters bump `rev_` (Phase B); pybind returns copies for field reads, so in-place aliasing from Python isn't possible today (verified in the binding audit) |
| Cache aliasing across `Topology` copies (candidate vectors are copied/moved everywhere) | Cache is a value member: copies carry a valid cache (same content), mutations invalidate per object |
| Fixpoint slide passes resisting scoped re-run | Phase D re-runs them per connected component, not per segment; fuzz gate; flag-gated rollout |
| Hidden ordering dependencies (conn order, sort ties) | Frozen explicitly in Phase 0 items 4–5; the conn-order contract is already documented at conn_topology.cpp:144-146 |
| Log-string diffs breaking flow tests | Strings frozen in Phase 0; any wording change is its own commit with test updates |

## 12. Sequencing and size

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
