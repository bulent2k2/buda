/*
 * Copyright 2026 Ben Bulent Basaran
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#pragma once
#include "congestion_planner.h"
#include "placed_segment.h"
#include <array>
#include <limits>
#include <map>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace buda {

// One bus segment after track assignment (kind BUS in the placed-segment
// hierarchy — see placed_segment.h; layer/span/track_position/width/placed
// live on the base with the same names, so every consumer and binding is
// unchanged).
// span_lo/span_hi  = extent along the routing direction (x for H, y for V)
// interval_lo/hi   = hard constraint on perpendicular position from the Hanan cell
// track_position   = assigned perpendicular position (y for H, x for V) — output
struct TrackSegment : PlacedSegmentBase {
    TrackSegment() : PlacedSegmentBase(SegKind::BUS) {
        // Assigned output; NaN = unplaced (the base defaults to 0.0 — the
        // pre-route convention — so the abstract stage overrides it here).
        track_position = std::numeric_limits<double>::quiet_NaN();
    }
    int  bundle_id  = -1;
    int  seg_idx    = -1;
    bool horiz      = false;
    double interval_lo = 0, interval_hi = 0; // perpendicular placement range (hard)
    int    net_pull = 0;                  // from ConnSeg: >0 prefer hi, <0 prefer lo
    // Resolved pull target: the coordinate this segment wants to reach (the
    // clamped pull_bound — the slide-window bound, or the nominal position when
    // the bound was the unbounded sentinel).  NaN for net_pull==0.  Set after
    // build_nuts_maps; consumed by tighten_pulls and exposed for tests / viz.
    double pull_target = std::numeric_limits<double>::quiet_NaN();
    bool   is_jog   = false;              // dogleg jog (slide window pruned to the trunk's stub extent)
    // Cross-trunk-layer corner resolution: the committed fixed track bound for
    // this trunk (one side of a split).  Carried into detailed NUTS so its bits
    // snap to the bounded side on real signal tracks.  Default = unbounded.
    double track_lo_bound = -std::numeric_limits<double>::infinity();
    double track_hi_bound =  std::numeric_limits<double>::infinity();
    // Along-axis coordinates of this segment's BUSTERM block-face taps (x for an
    // H segment, y for a V segment).  rev_conn_map carries only SEG connectivity,
    // so do_span_adjustments uses these as extend-only anchors to guarantee a
    // face-tapped segment's along-span always reaches its block face after slides.
    std::vector<double> busterm_faces;
};

// Exact geometry of one overlap pair, after placement.
struct OverlapDetail {
    int    layer;
    int    bid_a,  seg_a;    // first  segment: bundle_id, seg_idx
    int    bid_b,  seg_b;    // second segment: bundle_id, seg_idx
    double span_lo, span_hi; // overlap rectangle — routing direction
    double perp_lo, perp_hi; // overlap rectangle — perpendicular direction
};

// One junction edge the solver could not honor by placement (Part B): the
// landing segment's entire feasible centre range (slide window) lies OUTSIDE
// the spanning partner's NOMINAL span, so the junction closes only by
// stretching the partner beyond what the topology intended.  Derived from the
// final accepted state (never from tentative solve passes).  Structured like
// OverlapDetail so ripup_reroute can re-pin the offending bundle to an
// alternate topology.
struct JunctionInfeasibility { int bundle_id; int seg_a; int seg_b; };

// An out-of-bbox trunk (>=2 SEG conns, no busterm of its own) whose nominal
// perpendicular coordinate falls OUTSIDE the routable design boundary (the
// Hanan grid extent, extended by any explicit detour_channel) — so its
// placement window collapses and NUTS cannot seat it there without violating
// its interval, stranding its bits at detailed NUTS.  Report-only, but loud:
// the selection is unroutable as pinned (widen detour_channel or re-select).
// nom = the trunk's nominal perp; bound = the boundary edge it overshot.
struct UnseatableTrunk { int bundle_id; int seg_idx; bool horiz;
                         double nom; double bound; };

struct NUTSResult {
    std::vector<TrackSegment>  segments;
    std::vector<OverlapDetail> overlap_details;     // one entry per overlapping pair
    std::vector<JunctionInfeasibility> junction_infeasibilities;
    // OOB trunks whose nominal perp falls outside the routable design boundary
    // (Hanan extent + explicit detour_channel): unseatable as pinned (#58).
    std::vector<UnseatableTrunk> unseatable_trunks;
    int num_violations = 0;   // segments placed outside their interval
    int num_overlaps   = 0;   // pairs of segments that physically overlap after placement
    // Placed segments whose physical extent [pos - w/2, pos + w/2] lands on a
    // keepout (user zones + implicit LOW-layer leaf footprints) overlapping
    // their span.  Placement AVOIDS keepout-occupied intervals, but when a
    // window is exhausted the interval-centre fallback still commits —
    // previously with NO metric, so a bus through a keepout reported a fully
    // clean NUTS (keepout-model audit).  Counted per segment; report-only
    // (placement behaviour unchanged).
    int num_keepout_conflicts = 0;
    std::map<int, int> overlaps_per_layer;  // layer_id -> overlap pair count
    // Per-pass seconds of the solve(s) that produced this result (the RR
    // round-3 profiling layer: WHERE inside a trial's full solve the time
    // goes).  Keys: extract / context / fixpoint / dogleg_detect / repair /
    // corner / tighten / metrics.  Buckets accumulate across EVERY solve of
    // the run — the dogleg fallback's trial re-solves included (n_solves
    // counts them) — so the sum tracks the run's total solve wall.  Pure
    // observation: never read by any placement decision.
    std::map<std::string, double> pass_seconds;
    int n_solves = 0;
    // Overlap count right after the orientation fixpoint, BEFORE the
    // repair/corner/tighten passes (round-3 early-abort study: how often do
    // the reduction passes rescue a trial that looks doomed post-fixpoint?).
    // Last solve's value when the dogleg fallback re-solved.  Observation
    // only; counted over the same segment set the fixpoint placed (bottom-up
    // fixed segments are appended later, so on bottom-up designs the basis
    // differs from num_overlaps by the fixed-segment overlaps).
    int overlaps_post_fixpoint = 0;
    // Topologies the dogleg pass mutated (bundle_id -> new selected Topology and
    // its seg_layers).  The CLI adopts these into its bundles before rebuilding
    // ConnTopology for detailed NUTS, so the split bundle's stubs get the correct
    // (post-split) connectivity instead of routing with stale, corrupted spans.
    std::map<int, Topology>            dogleg_topologies;
    std::map<int, std::vector<int>>    dogleg_seg_layers;
    std::map<int, std::vector<int>>    dogleg_seg_net_pull;
    std::map<int, std::vector<int>>    dogleg_seg_perp;
    std::map<int, std::vector<double>> dogleg_seg_slide_lo;
    std::map<int, std::vector<double>> dogleg_seg_slide_hi;
};

// Non-Uniform Track Sharing engine.
//
// Solves the 1.5-D rectangle packing problem (Ekici, Basaran, Keskinocak 2009)
// for each metal layer independently.
//
// Each bus segment is a rectangle whose routing-direction extent is fixed and
// whose perpendicular position must lie within a Hanan-grid-derived interval.
// A sweep-line / first-fit algorithm assigns track positions so that no two
// segments on the same layer with overlapping spans also overlap in the
// perpendicular direction.
// Translate a placed TrackSegment by (dx, dy) and re-key it to another
// bundle — the per-instance copy step of bottom-up template planning (the
// TrackSegment analogue of offset_topology).  The routing-direction fields
// shift by the along-axis delta and the perpendicular fields (position,
// interval, pull target, corner bounds) by the cross-axis delta.
TrackSegment offset_track_segment(const TrackSegment& ts, int dx, int dy,
                                  int new_bundle_id);

// Orientation-aware sibling: map a placed segment from a source frame
// (cell box of cell_w×cell_h at (src_x, src_y)) through `orient` into a
// destination frame at (dst_x, dst_y).  Supports the direction-preserving
// set N/S/FN/FS only — 90/270 swap H↔V and thus the layer, which needs the
// (deferred) layer-pairing policy; throws on those.  Reflections swap the
// interval/bound endpoints (±inf corner-bound sentinels ride IEEE
// arithmetic) and flip the net_pull sign on a reflected perpendicular.
TrackSegment transform_track_segment(const TrackSegment& ts,
                                     const std::string& orient,
                                     int cell_w, int cell_h,
                                     int src_x, int src_y,
                                     int dst_x, int dst_y,
                                     int new_bundle_id);

// Records how segment S's span must follow segment T's track position
// (S connects to T at S's lo or hi end, or mid-span).
struct SpanAdjConn { int src_bid, src_si; bool lo_end; bool is_endpoint; };

// (bundle_id, seg_idx) → keys of same-bundle segments connected to the same
// perpendicular segment.  Siblings on the same layer prefer the same track
// position: per-bit they are the same nets and may share tracks (mirrors the
// DetailedNUTS same-bundle reservation exemption), collapsing e.g. a
// multicast trunk's two opposite stubs onto one band.
using AlignMap = std::map<std::pair<int,int>, std::vector<std::pair<int,int>>>;

class LayerSolver;   // nuts.cpp: one layer's placement pass (befriended below)

// Constraints for one layer's phase-0 corner-overlap resolution, fed back into
// solve_layer.  Two kinds, both built lazily from detected corner overlaps:
//   preds  — key → same-layer segments that must sit BELOW it (relative
//            ordering; same-trunk-layer pairs, bottom-edge packed).
//   bounds — key → fixed track-coordinate bounds [lo, hi] for cross-trunk-layer
//            pairs (a trunk nudged to one side of a split coordinate); placed by
//            preferred_fit toward the nearer bound.  Default {-inf, +inf}.
struct LayerConstraints {
    std::map<std::pair<int,int>, std::set<std::pair<int,int>>> preds;
    std::map<std::pair<int,int>, std::pair<double,double>>     bounds;
    bool empty() const { return preds.empty() && bounds.empty(); }
};

// Everything the placement + repair passes derive from the selected topologies
// before solving, built ONCE per solve by build_context() (which also applies
// the interval prep to the segments): the former eight build_nuts_maps
// out-params plus the (bundle_id, seg_idx) -> TrackSegment* lookup.  All maps
// are keyed by (bundle_id, seg_idx).  Owned per solve — run()'s dogleg trials
// and rerun_layer() each build their own against their own segment vector
// (ts_ptr_map points into it).
struct NutsContext {
    std::map<std::pair<int,int>, double>                     pull_map;
    std::map<std::pair<int,int>, std::pair<double,double>>   slide_map;
    std::set<std::pair<int,int>>                             trunk_set;
    std::set<std::pair<int,int>>                             busterm_set;
    std::map<std::pair<int,int>, std::vector<SpanAdjConn>>   rev_conn_map;
    std::map<std::pair<int,int>, int>                        net_pull_map;
    AlignMap                                                 align_map;
    std::map<std::pair<int,int>, std::vector<double>>        busterm_face_map;
    std::map<std::pair<int,int>, TrackSegment*>              ts_ptr_map;
};

class NUTSEngine {
public:
    explicit NUTSEngine(const Floorplan& fp, const LayerStack& ls);

    // Minimum gap between adjacent placed buses (default 1.0 layout unit).
    void set_track_pitch(double pitch);

    // Fast-trial mode (RR round 3): skip the final tighten_pulls pass.  The
    // pass is WL-only and provably overlap-NON-INCREASING (its per-move guard
    // reverts any move where find_overlaps grows), so a solve without it
    // reports an overlap count that is an UPPER BOUND on the full solve's —
    // an RR trial accepted on the skipped metric is sound (the true state is
    // at least as good), a rejection may rarely be spurious.  A COMMIT must
    // therefore re-run the full pipeline (the session enforces this).
    // Default off: run() is byte-identical unless explicitly enabled.
    void set_skip_tighten(bool v) { skip_tighten_ = v; }

    // Screen mode (RR round 3, fixed-context single-bundle screen): skip the
    // dogleg fallback in run().  Dogleg surgery mutates the selected Topology
    // and its plan arrays and exports adoption maps — a screen's result is
    // discarded (it only ORDERS candidates), so it must stay read-only, and
    // the fallback's trial re-solves would multiply the screen's cost.
    // Default off: run() is byte-identical unless explicitly enabled.
    void set_skip_doglegs(bool v) { skip_doglegs_ = v; }

    // Supply additional Hanan grid coordinates (e.g. segment endpoints outside
    // the floorplan bounding box) that the NUTSEngine should use when deriving
    // perpendicular intervals.  Must be called before run() / rerun_layer().
    void set_extra_grid_points(std::vector<int> xs, std::vector<int> ys);

    // Bottom-up template planning (stage b): register already-placed segments
    // (per-instance translated copies of a cell-local NUTS solve) as FIXED.
    // Their bundles are skipped by extraction (never re-solved), every solver
    // pass sees each fixed segment's physical extent as an engine-internal
    // keepout zone on its layer (identical occupancy to another placed bundle,
    // but immovable), and the segments are appended verbatim to the result so
    // downstream stages (persist, viz, detailed NUTS) consume them normally.
    // Must be called before run() / rerun_layer().
    void add_fixed_segments(const std::vector<TrackSegment>& segs);

    // add_fixed_segments() convenience for the RR fixed-context screen: fix
    // every PLACED segment of `baseline` except `exclude_bid`'s in one call
    // (a per-segment Python round-trip would dominate the screen's ~ms
    // budget).  Unplaced segments are skipped — a NaN track_position has no
    // physical extent to freeze.  On a bottom-up design the baseline already
    // carries the bottom-up fixed copies (run() appends them), so the caller
    // must NOT also inject them separately.
    void add_fixed_segments_except(const NUTSResult& baseline,
                                   int exclude_bid);

    // Run NUTS on the bundles that have already been processed by CongestionPlanner.
    // Each BundleWrapper must have candidates filled and selected_topology_index set.
    NUTSResult run(const std::vector<BundleWrapper>& bundles);

    // Re-solve a single layer, keeping all other layers' placements intact.
    // Resets the target layer's segments to fresh topology state, re-runs the
    // sweep-line solver, applies span adjustments to connected segments, and
    // recomputes metrics for the whole result.
    NUTSResult rerun_layer(const NUTSResult& prev,
                           const std::vector<BundleWrapper>& bundles,
                           int layer_id) const;

    // Batched fixed-context screen (RR round 5): score every candidate in
    // `tidxs` for target_bid in ONE call on an engine already configured as
    // a screen (pitch, extra grids, skip flags, add_fixed_segments_except).
    // Works on a LOCAL COPY of the wrapper list — one Python->C++
    // conversion per contender instead of one per replan, no session-state
    // mutation, no restore — pinning each candidate, replanning its layers
    // (`planner.replan_bundle`), and running the single-bundle placement;
    // per candidate only (tidx, overlaps, violations) crosses back, not a
    // ~full-design segment vector.  Because run() sees the WHOLE list, its
    // empty-grid fallback derives from the current selections including
    // the pinned candidate — exact full-trial grid parity with no
    // caller-side merging.  clear_dogleg_overrides mirrors _rr_trial's
    // hazard guard (a dogleg-adopted target's per-segment overrides index
    // its split topology, not the screened candidate).  nullopt when the
    // incremental replan is unavailable for any candidate — the caller
    // falls back to the unscreened order.
    std::optional<std::vector<std::array<int, 3>>> screen_candidates(
        const std::vector<BundleWrapper>& bundles,
        int target_bid,
        const std::vector<int>& tidxs,
        CongestionPlanner& planner,
        bool clear_dogleg_overrides);

    // Warm-start single-bundle re-solve (RR round 4 study): re-extract and
    // place ONLY target_bid's segments against `prev` frozen as occupancy
    // (the #293 screen), then UNFREEZE and run the safety passes —
    // settle_spans / repair_overlaps / resolve_corner_overlaps / tighten
    // (skipped in fast-trial mode) — over the real union so neighbours
    // adjust, and compute exact metrics on the warm state.  No orientation
    // fixpoint (the baseline seed replaces it) and no dogleg fallback (a
    // warm state must not export topology surgery).  The warm metric is
    // EXACT for the warm placement but the placement differs from a cold
    // run()'s — consumers must treat it as a predictor of the cold metric
    // and re-verify with run() before committing anything.
    NUTSResult rerun_bundle_warm(const NUTSResult& prev,
                                 const std::vector<BundleWrapper>& bundles,
                                 int target_bid) const;

private:
    friend class LayerSolver;   // placement pass: uses first_fit/preferred_fit/track_pitch_
    const Floorplan& floorplan_;
    const LayerStack& layers_;
    double track_pitch_ = 1.0;
    bool skip_tighten_ = false;            // fast-trial mode (see setter)
    bool skip_doglegs_ = false;            // screen mode (see setter)
    std::vector<int> extra_x_, extra_y_;   // additional grid points from CongestionPlanner

    // User keepouts plus implicit solid-leaf-cell keepouts on every non-TOP
    // layer (Gap 2): a LOW segment may not route over a leaf cell, so the cell
    // behaves as a keepout for the whole lower stack.  TOP segments are filtered
    // out by KeepoutZone::layer_ids.  Mirrors the planner's band-capacity model.
    std::vector<KeepoutZone> low_keepouts() const;

    // low_keepouts() plus the fixed segments' derived zones — what the solver
    // passes (LayerSolver, repair_overlaps, tighten_pulls) must avoid.  The
    // fixed zones stay OUT of low_keepouts() itself so the report-only
    // count_keepout_conflicts never flags a fixed segment against its own
    // footprint (it IS still checked against real user zones — a uniform copy
    // landing on a keepout that exists in only one instance must be loud).
    std::vector<KeepoutZone> solver_keepouts() const;

    // Fixed (bottom-up copy) segments: appended to every result, never
    // re-solved; their bundles are excluded from extraction.
    std::vector<TrackSegment> fixed_segments_;
    std::set<int>             fixed_bundle_ids_;
    std::vector<KeepoutZone>  fixed_zones_;

    // Build a flat list of TrackSegments from all selected topologies.
    std::vector<TrackSegment> extract_segments(
        const std::vector<BundleWrapper>& bundles,
        const std::vector<int>& x_grid,
        const std::vector<int>& y_grid) const;

    // Solve placement for one layer (modifies TrackSegment::track_position in place).
    // pull_map: (bundle_id, seg_idx) -> preferred perpendicular centre; absent entries
    // fall back to first-fit (lowest valid) behaviour.
    // align_map: same-bundle sibling preference (see AlignMap).
    // constraints: optional phase-0 corner-overlap constraints (relative
    // ordering and/or fixed track bounds).  Constrained segments are placed
    // first, before the normal anchor/sweep phases.  Empty = no change.
    // jn_map/jn_segs: the junction edges (rev_conn_map) + global segment lookup,
    // used for the junction-anchored preference (Part B) — empty maps disable it.
    void solve_layer(std::vector<TrackSegment*>& segs,
                     NutsContext& ctx,
                     const LayerConstraints& constraints = {}) const;

    // Alternating orientation-group fixpoint: solve a whole orientation group
    // (all H or all V) at once, propagate spans to the perpendicular group,
    // solve that, propagate back, and iterate to a fixpoint.  Each group thus
    // packs against the OTHER group's already-stretched spans — proactive
    // ordering rather than reactive repair.  Leads with the orientation of the
    // lowest TOP layer.  Keeps the best-by-overlap-count state; stops on no
    // strict overlap drop or a repeated placement state (a genuine cyclic
    // vertical constraint).  Replaces the naive per-layer solve loop; the
    // existing repair_overlaps / resolve_corner_overlaps run after it as a
    // safety net.
    void orientation_fixpoint(
        std::vector<TrackSegment>& segments,
        std::map<int, std::vector<TrackSegment*>>& by_layer,
        NutsContext& ctx,
        const std::map<int, LayerConstraints>& seed_cons = {}) const;

    // Post-span-adjustment overlap repair: the final cross-layer span
    // adjustments can extend spans of already-packed layers, materialising
    // overlaps after packing.  Re-places victims of overlapping pairs within
    // their intervals against current adjusted spans (bounded iterations;
    // restores the original state unless the overlap count strictly drops).
    // seed_cons: the ACTIVE per-layer corner constraints when invoked from
    // resolve_corner_overlaps (its by_layer_cons is not yet persisted onto
    // the segments' track bounds at that point) — the cluster repack must
    // treat constrained phase-0 trunks as fixed obstacles, exactly like the
    // placement-time try_repack does.  Null elsewhere.
    void repair_overlaps(std::vector<TrackSegment>& segments,
                         NutsContext& ctx,
                         const std::map<int, LayerConstraints>* seed_cons
                             = nullptr) const;

    // Corner-overlap resolution (vertical-constraint style): two stubs that
    // collide on a layer can't be separated by moving either (they're
    // perp-locked) — only by adjusting the trunks they hang from.  Same trunk
    // layer → order the trunks (anchored-end rule, bottom-edge pack).  Different
    // trunk layers → nudge each trunk within its own layer to opposite sides of
    // a split.  Re-solve the affected trunk layer(s) under the accumulated
    // constraints; keep the result only while the total overlap count strictly
    // drops and no new interval violation appears (stop-&-reverse).
    void resolve_corner_overlaps(std::vector<TrackSegment>& segments,
                                 NutsContext& ctx) const;

    // Final greedy wirelength-tightening pass.  The sweep/repack place segments
    // by LOCAL decisions made as the layer fills, so a segment parked away from
    // its pull is never revisited even after later moves free space next to its
    // pull bound.  Here, once the layout has settled, each pulled segment is slid
    // as close to its pull bound as the FINAL occupancy allows (preferred_fit),
    // its follower spans re-adjusted, and the move kept only when it strictly
    // shortens total wirelength without adding an overlap or interval violation
    // (per-move stop-&-revert).  Biggest gaps first; iterates to a fixpoint.
    // only_layer >= 0 restricts which segments may be SLID to that layer (used by
    // rerun_layer to keep its single-layer contract); the overlap / wirelength
    // guards and follower-span adjustments stay global either way.  Default -1
    // tightens every layer.
    void tighten_pulls(std::vector<TrackSegment>& segments,
                       NutsContext& ctx,
                       int only_layer = -1) const;

    // First-fit: lowest valid placement position within [lo, hi].
    // Returns NaN if the interval is infeasible.
    double first_fit(double lo, double hi, double width,
                     const std::vector<std::pair<double,double>>& occupied) const;

    // Preferred-fit: valid placement position closest to 'preferred' within [lo, hi].
    // Returns NaN if the interval is infeasible.
    double preferred_fit(double lo, double hi, double width,
                         const std::vector<std::pair<double,double>>& occupied,
                         double preferred) const;
};

} // namespace buda
