# NUTS / DetailedNUTS Refactor Plan

Status: **IMPLEMENTED** (Phases 0, A-F; G deferred as planned).  Every phase
landed as one commit on the PR branch, gated byte-identical by the Phase 0
placement goldens (plus fast+mid tiers).
Scope: `src/nuts.{h,cpp}` (stage 4, 2,801 lines), `src/detailed_nuts.{h,cpp}`
(stage 9, 527 lines), their pybind surface (`src/bind_nuts.cpp`), and the
Python handoff (`src/buda_session/nutsflow.py::_run_detailed_nuts`).
Companion to [`topo_conn_unification.md`](topo_conn_unification.md) — same
ground rules: **byte-identical results**, consumer APIs constant, phased and
gated, every phase independently revertible.

---

## 1. What the review found

### 1.1 Module shape today

`nuts.cpp` is one 2,532-line translation unit containing five distinguishable
subsystems with no internal boundaries:

| Subsystem | Pieces | ~Lines |
|---|---|---|
| Map building & interval prep | `build_nuts_maps` (8 out-params), `apply_interval_constraints`, `relax_boundary_intervals`, `set_pull_targets` | 350 |
| Geometry & metrics | `sp_lo/sp_hi`, `segs_overlap`, `find_overlaps`, `count_violations`, `compute_metrics`, `keepout_occupied`, `do_span_adjustments` | 350 |
| Placement core | `first_fit`, `preferred_fit`, `solve_layer` (410 lines, 4 nested lambdas: `place_seg`, `try_repack`, `place_phase0`, `pack`) | 500 |
| Repair ladder | `repair_overlaps`, `resolve_corner_overlaps`, `tighten_pulls`, `orientation_fixpoint` | 700 |
| Dogleg subsystem | `CycleEdge`/`DoglegPlan`/`DoglegResult`, `detect_dogleg_plans`, `apply_dogleg`, plus the trial loop inside `run()` | 500 |
| Drivers | `run()` (with a `solve` lambda duplicating the prep ceremony), `rerun_layer()`, `derive_junction_infeasibilities` | 250 |

`detailed_nuts.cpp` is one 360-line `run()` doing three phases inline:
per-layer bit placement, per-bit span-follow, per-bit via emission.

### 1.2 Duplication inventory (verified by a full consumer sweep)

**(a) The 8-parallel-map ceremony.** `build_nuts_maps` fills eight
`std::map<std::pair<int,int>, …>` out-params keyed by `(bundle_id, seg_idx)`
(pull, slide, trunk-set, busterm-set, rev-conn, net-pull, align,
busterm-face). Every pass takes 4–6 of them; `run()`'s `solve` lambda and
`rerun_layer()` repeat the same ~25-line build + `apply_interval_constraints`
+ `relax_boundary_intervals` + `set_pull_targets` + `ts_ptr_map` ceremony
verbatim (`nuts.cpp:2234-2257` vs `2484-2502`).

**(b) Snapshot/restore ×4.** `struct Snap {pos, lo, hi[, placed]}` plus
take/restore lambdas is re-implemented in `repair_overlaps`,
`tighten_pulls`, `resolve_corner_overlaps` (3-field variant) and
`orientation_fixpoint` (4-field variant).

**(c) Occupancy building ×4.** "Same layer, other bundle, closed span
overlap → `track ± width/2`, plus LOW keepouts, then sort" appears in
`solve_layer::build_occupied`, `try_repack::pack`'s inner loop,
`repair_overlaps`' victim loop, and `tighten_pulls::build_occ`.

**(d) The guarded-move idiom ×4.** *Snapshot → mutate tracks →
`do_span_adjustments` → recompute metric → restore unless strictly better*
is the universal accept/reject shape of `repair_overlaps` (per move),
`tighten_pulls` (per move/group), `resolve_corner_overlaps` (per iteration),
`orientation_fixpoint` (per sweep) — each with its own metric, each
hand-rolled.

**(e) Ordered-span normalization everywhere.** `span_lo/span_hi` keep
*nominal endpoint identity* and may be stored reversed; every geometric
consumer re-derives `min/max` — `sp_lo/sp_hi` inside `nuts.cpp`, four local
`min/max` sites in `verify.cpp`, `nutsflow.py:395-406`, `ripup.py:586`. The
`cover` (extend-only, endpoint-identity-preserving) lambda is duplicated
between `do_span_adjustments` (`nuts.cpp:378`) and the DNUTS bit-span pass
(`detailed_nuts.cpp:339`), including the busterm-face re-extension that
follows it in both.

**(f) TrackSegment ≈ BusSegment near-twins.** Shared fields: `bundle_id,
seg_idx, layer, span_lo/hi, interval_lo/hi, busterm_faces, track_lo_bound/
track_hi_bound` (the last two carry identical cross-trunk-corner semantics
with cross-referencing comments); `track_position → abstract_pos` renamed in
flight. `SpanAdjConn{src_bid,src_si,lo_end,is_endpoint}` and
`BusSegmentConn{seg_idx,at_pos,is_endpoint,lo_end}` are the same junction
record twice. This is the drift CLAUDE.md's "Segment Type Hierarchy (target
state)" section already flags.

**(g) The stage-4→9 handoff re-derives connectivity in Python.**
`nutsflow.py::_run_detailed_nuts` (715-782) rebuilds `ConnTopology` per
bundle and re-derives per-segment SEG connections — including the
`lo_end = (at_pos <= mid)` midpoint rule — and re-collects BUSTERM
`face_coord`s, all of which `build_nuts_maps` already computed in C++ during
the abstract solve (and stored on `TrackSegment::busterm_faces`, which is
*not even bound to Python*, so Python recomputes what C++ already carries).
Two implementations of the same derivation, in two languages, that must not
drift — the exact failure class the topo/conn unification eliminated for
stage 2/3.

**(h) Dogleg subsystem is mis-homed.** ~500 lines inside `nuts.cpp` mutate
*stage-2/3 state* — the selected `Topology`'s segments, `plan.seg_layers`,
`seg_net_pull`, `seg_slide_*`, `seg_perp`, plus `annotate_seg_conns` — i.e.
topology surgery living inside the track packer, coupled back to the CLI via
six `dogleg_*` side-channel maps on `NUTSResult` and `EditMixin::_adopt_doglegs`.

### 1.3 What is externally load-bearing (must not move)

A full sweep of C++, Python, tools and tests established the frozen surface:

- **C++ consumers:** only `verify.cpp` (reads `NUTSResult::segments`,
  `DetailedNUTSResult::net_segments` field-wise). `congestion_planner.h` is a
  *dependency* of `nuts.h`, not a consumer. BDB row structs are independent.
- **pybind (`bind_nuts.cpp`):** every bound name on `TrackSegment` (16
  fields), `OverlapDetail`, `JunctionInfeasibility`, `NUTSResult` (12 fields
  incl. the six `dogleg_*` maps), `NUTSEngine` (4 methods), `BusSegmentConn`
  (+ opaque `BusSegmentConnList`), `BusSegment` (15), `NetSegment` (8),
  `NetVia` (8), `DetailedNUTSResult` (3), `DetailedNUTSEngine.run`. Python
  reach: `nuts_result` in 27 test files, `detailed_result` in 15, plus
  `persist.py` (BDB checkpoint fields), `ripup.py`, `buda_viz.py`,
  `tools/render.py`, `tools/show_detailed_shorts.py`.
- **Unbound (free to restructure):** `TrackSegment::net_pull` and
  `pull_target` are bound (keep); `TrackSegment::busterm_faces` is **not**
  bound; `SpanAdjConn`, `AlignMap`, `LayerConstraints`, all file-statics.
- **Persistence:** `persist.py` `_persist_nuts` / `_persist_bundle_vias` /
  `_persist_detailed_nuts` read only bound fields; `_load_pipeline_from_bdb`
  reconstructs `NUTSResult` via the bound ctor + `readwrite` fields. Nothing
  extra to keep alive, but nothing bound may be dropped or renamed.

---

## 2. Target architecture

```
src/nuts_geom.h            span/overlap/occupancy primitives (header-only):
                           sp_lo/sp_hi, span_cover, segs_overlap,
                           find_overlaps, count_violations, compute_metrics,
                           keepout_occupied, occupancy_from,
                           PlacementSnapshot (take/restore, ±placed flag)
src/nuts.{h,cpp}           NutsContext (struct in nuts.h; build_context() +
                           settle_spans() in nuts.cpp), LayerSolver
                           (solve_layer phases as methods), repair ladder,
                           orientation fixpoint, run()/rerun_layer() drivers
                           — packing only
src/nuts_dogleg.{h,cpp}    cycle detection + topology split + trial loop,
                           behind run_dogleg_fallback() called from run()
src/detailed_nuts.{h,cpp}  DetailedNUTSEngine: place_by_layer /
                           adjust_bit_spans / emit_bit_vias methods +
                           make_bus_segments() bridge (single-sourced handoff)
```

Public headers `nuts.h` / `detailed_nuts.h` keep every existing type and
field so `verify.cpp`, `bind_nuts.cpp` and all Python continue to compile
and behave unchanged. New internal headers are implementation detail.

The **`PlacedSegmentBase` unification** from CLAUDE.md's target-state section
is *deliberately deferred* (Phase G): pybind base-class registration is
possible without renaming any attribute, but the payoff (a shared `kind` +
`PreRoutedSegment`) only materializes when pre-routes become explicit
objects rather than non-SIGNAL slots. Introducing the base now would be
churn without a consumer. The geometry core (Phase A) captures the sharable
behavior (ordered spans, cover) without the type merge.

---

## 3. Phases

Ordering follows risk: pure motion first, structural change later. Every
phase ends with the full gate set (§4) green.

### Phase 0 — NUTS placement goldens (new gate) — ✅ DONE
*(as landed: diffable TEXT goldens in `test/tests/data/nuts_golden/*.txt` —
not `.json` — with per-(stage, layer) sha256 digests for the two large flows;
tiers are **mid** (small corpus) + **slow** (tc3a_flat, rnr/mix), since
full-pipeline runs are integration tests post-#202.  The corpus is the
wl_corpus list: the hier flow is `rnr/mix` and big2 is represented by the
single-bus `b4_bus_077` extraction — deliberate risk reduction, full big2's
FP-sensitive whole-design outcome stays bounds-tested, not golden-pinned.
The three FP/ISA-sensitive flows warn-only (xfail) off the golden-generation
host unless `BUDA_NUTS_GOLDEN_STRICT` is set.)*
`tools/wl_corpus.py` compares WL totals, overlap counts and unplaced bits —
strong but not airtight (two placement swaps can preserve all three). Add
`tools/nuts_snapshot.py` (sibling of `topo_snapshot.py`): run the corpus
flows, hash the sorted `(bundle_id, seg_idx, layer, track_position, span_lo,
span_hi, placed)` tuples of `nuts_result.segments` and the sorted
`(bundle_id, seg_idx, bit_index, layer, track_position, span_lo, span_hi)`
tuples of `detailed_result.net_segments` (+ `net_vias`), and check
per-flow digests into `test/tests/data/nuts_golden_*.json` with a fast+mid
golden test. This is the byte-identity oracle for every later phase.

### Phase A — Geometry & metrics core (`nuts_geom.h`) — ✅ DONE
*(as landed: the `sp_lo`/`sp_hi` names were kept — they are established in
comments/docs — rather than renamed to `ordered_lo/hi`.)*
Pure motion, header-only where possible:
- `sp_lo/sp_hi` → `ordered_lo/ordered_hi` inline helpers (old names kept as
  aliases inside `nuts.cpp` to keep the diff reviewable).
- The `cover` lambda → `span_cover(double& span_lo, double& span_hi, double c)`
  preserving the ordered/endpoint-identity contract; adopted at both existing
  sites (`do_span_adjustments`, DNUTS bit-span pass) with bodies unchanged.
- `segs_overlap`, `find_overlaps`, `count_violations`, `compute_metrics`,
  `keepout_occupied` move from file-static to the shared header/TU, same
  bodies. `verify.cpp` may adopt `ordered_lo/hi` (cosmetic, optional).

### Phase B — `NutsContext` (kill the map ceremony) — ✅ DONE
*(as landed: `NutsContext` lives in `nuts.h` + `build_context()` in
`nuts.cpp` — no separate `nuts_context.{h,cpp}` TU was needed, the struct is
declaration-only and the builder is one static function.)*
`struct NutsContext` owning the eight maps, `ts_ptr_map`, `by_layer`;
`NutsContext::build(bundles, floorplan, segments, only_layer)` wraps
`build_nuts_maps` + `apply_interval_constraints` + `relax_boundary_intervals`
+ `set_pull_targets` + busterm-face stamping exactly as `run()` does today
(with `rerun_layer()`'s `only_layer` variants). Pass signatures collapse to
`(segments, ctx)`; `run()`'s `solve` lambda and `rerun_layer()` share one
prep path. All private — zero API impact.

### Phase C — Snapshot / occupancy / guarded-move helpers — ✅ DONE
*(as landed: `PlacementSnapshot` + per-pair `occupancy_from()` in
`nuts_geom.h`, plus `settle_spans()` in `nuts.cpp`; a full `guarded_move()`
wrapper was skipped — with snapshot/settle factored out, each pass's
three-line guard with its own metric is clearer than a callback.)*
- `PlacementSnapshot` (3- and 4-field variants) replaces the four hand-rolled
  Snap structs.
- `build_occupancy(seg, segments|layer_view, kozs)` replaces the four
  occupancy loops (parameterized only by the member-exclusion set
  `try_repack` needs).
- A `guarded_move(ctx, mutate, metric_better)` primitive expressing
  *snapshot → mutate → span-adjust → accept/revert*; adopted per pass **with
  each pass's exact existing metric expression inline** — the guards stay
  semantically untouched, only the scaffolding unifies.

### Phase D — `solve_layer` → `LayerSolver` — ✅ DONE
A private `LayerSolver` struct holds what the nested lambdas close over
(`layer_map`, keepouts, constraints, the maps via `NutsContext`); `place_seg`,
`try_repack`, `pack`, `place_phase0` and phases 0/1/2 become methods with
verbatim bodies. 410 lines → ~5 × 60-line units. Also the enabler for the
wishlist item "band-level repack for spread-fit overlap clusters"
([`wishlist-nuts.md`](wishlist-nuts.md)): it explicitly wants `try_repack`
lifted into `repair_overlaps`, which requires exactly this extraction.

### Phase E — Dogleg extraction (`nuts_dogleg.{h,cpp}`) — ✅ DONE
Move `CycleEdge`, `DoglegPlan`, `DoglegResult`, `detect_dogleg_plans`,
`apply_dogleg`, and lift `run()`'s trial loop into
`run_dogleg_fallback(bundles, out, solve_fn, track_pitch)` where `solve_fn`
is the existing `solve` closure. `nuts.cpp` keeps a single call site; the
six `dogleg_*` maps on `NUTSResult` and `_adopt_doglegs` are untouched.
Topology surgery gets its own reviewable home next to the analysis
annotators it already calls (`annotate_seg_conns`).

### Phase F — DNUTS decomposition + single-sourced handoff — ✅ DONE
1. Split `DetailedNUTSEngine::run` into `place_by_layer`,
   `adjust_bit_spans`, `emit_bit_vias` private methods (verbatim motion).
2. Add C++ `make_bus_segments(const std::vector<BundleWrapper>&,
   const NUTSResult&, const std::map<int,int>& bits_per_bundle,
   const std::string& bit_order)` reproducing `nutsflow.py:736-772`
   *exactly*, but sourcing connections/faces from the same cached topo
   analysis the abstract stage used and copying
   `TrackSegment::busterm_faces` directly (no Python re-derivation, no
   second `lo_end` midpoint rule). Bind it; `_run_detailed_nuts` shrinks to
   building `bits_per_bundle` + two engine calls. The Python-side
   `BusSegment` construction path stays bound and working (tests use it).
   Gate: detailed goldens byte-identical.

### Phase G — `PlacedSegmentBase` (DEFERRED, unchanged)
Introduce the CLAUDE.md target base struct + `PreRoutedSegment` only when
pre-routes become first-class (draw_preroutes / explicit pre-route rows).
Prereqs are done by then: geometry helpers shared (A), handoff single-sourced
(F). Revisit alongside that feature; wishlisted, not scheduled.

---

## 4. Gates (every phase)

1. `bin/bb test` / `bin/bb mid` — fast + mid tiers green.
2. `tools/nuts_snapshot.py` goldens (Phase 0) — **byte-identical placements**
   (abstract + detailed) on the corpus (as landed): the four_blocks pair,
   `dogleg1`/`dogleg2`, `channel_stress`, `demo/comprehensive_demo`,
   `big2/b4_bus_077`, `tc3a_flat`, and `rnr/mix` (the hier + ripup flow).
3. `tools/wl_corpus.py` A/B vs pre-phase HEAD on the same build — identical.
4. Flow-log diff on `flow/rnr/mix.buda` (message text, counts, ordering —
   the `[NUTS]`/`[DetailedNUTS]` prints move with their code, verbatim).
5. `route_snapshot` BDB hashes unchanged for the persisting flows.
6. FP-determinism guards untouched (`kFpTieTol` in `preferred_fit`, the
   quantized sort keys in `solve_layer`) — they move, they do not change.

## 5. Constraints & non-goals

- **No behavioral improvements ride along.** Band-level repack, corner-min
  enhancements, DNUTS Option-C ideas stay in their wishlists; this refactor
  only makes them cheaper to build.
- **No binding renames/removals**; additions only (`make_bus_segments`).
- **`NUTSResult`/`DetailedNUTSResult` stay copyable value types** (ripup's
  snapshot/restore and `rerun_layer(prev, …)` depend on it).
- **Prints are part of the contract** (flow-log goldens, `_extract_headline`
  markers like "segments placed" / "bits unplaced") — moved verbatim.
- CMake: new TUs join `buda` target (routing pipeline), not `buda_core` —
  no DB-layer registration involved.

## 6. Shape after (as landed)

| File | Before | After |
|---|---|---|
| `nuts.cpp` | 2,532 | 1,930 (context/prep, LayerSolver, repair ladder, drivers) |
| `nuts_geom.h` | — | 233 (header-only) |
| `nuts_dogleg.{h,cpp}` | — | 87 + 451 |
| `detailed_nuts.cpp` | 415 | 511 (3 methods + `make_bus_segments`) |
| `detailed_nuts.h` | 112 | 139 |
| `nutsflow.py::_run_detailed_nuts` | 68 | 25 |
| goldens gate | — | `tools/nuts_snapshot.py` + `test_nuts_placement_golden.py` + 9 goldens |
