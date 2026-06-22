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
#include <limits>
#include <map>
#include <string>
#include <utility>
#include <vector>

namespace buda {

// One bus segment after track assignment.
// span_lo/span_hi  = extent along the routing direction (x for H, y for V)
// interval_lo/hi   = hard constraint on perpendicular position from the Hanan cell
// track_position   = assigned perpendicular position (y for H, x for V) — output
struct TrackSegment {
    int  bundle_id  = -1;
    int  seg_idx    = -1;
    int  layer      = 0;
    bool horiz      = false;
    double span_lo  = 0, span_hi   = 0;   // routing-direction extent
    double interval_lo = 0, interval_hi = 0; // perpendicular placement range (hard)
    double width    = 1.0;                 // bus width in perpendicular direction
    double track_position = std::numeric_limits<double>::quiet_NaN(); // assigned output; NaN = unplaced
    bool   placed   = false;
    int    net_pull = 0;                  // from ConnSeg: >0 prefer hi, <0 prefer lo
    bool   is_jog   = false;              // dogleg jog (slide window pruned to the trunk's stub extent)
    // Cross-trunk-layer corner resolution: the committed fixed track bound for
    // this trunk (one side of a split).  Carried into detailed NUTS so its bits
    // snap to the bounded side on real signal tracks.  Default = unbounded.
    double track_lo_bound = -std::numeric_limits<double>::infinity();
    double track_hi_bound =  std::numeric_limits<double>::infinity();
};

// Exact geometry of one overlap pair, after placement.
struct OverlapDetail {
    int    layer;
    int    bid_a,  seg_a;    // first  segment: bundle_id, seg_idx
    int    bid_b,  seg_b;    // second segment: bundle_id, seg_idx
    double span_lo, span_hi; // overlap rectangle — routing direction
    double perp_lo, perp_hi; // overlap rectangle — perpendicular direction
};

struct NUTSResult {
    std::vector<TrackSegment>  segments;
    std::vector<OverlapDetail> overlap_details;     // one entry per overlapping pair
    int num_violations = 0;   // segments placed outside their interval
    int num_overlaps   = 0;   // pairs of segments that physically overlap after placement
    std::map<int, int> overlaps_per_layer;  // layer_id -> overlap pair count
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
// Records how segment S's span must follow segment T's track position
// (S connects to T at S's lo or hi end, or mid-span).
struct SpanAdjConn { int src_bid, src_si; bool lo_end; bool is_endpoint; };

// (bundle_id, seg_idx) → keys of same-bundle segments connected to the same
// perpendicular segment.  Siblings on the same layer prefer the same track
// position: per-bit they are the same nets and may share tracks (mirrors the
// DetailedNUTS same-bundle reservation exemption), collapsing e.g. a
// multicast trunk's two opposite stubs onto one band.
using AlignMap = std::map<std::pair<int,int>, std::vector<std::pair<int,int>>>;

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

class NUTSEngine {
public:
    explicit NUTSEngine(const Floorplan& fp, const LayerStack& ls);

    // Minimum gap between adjacent placed buses (default 1.0 layout unit).
    void set_track_pitch(double pitch);

    // Supply additional Hanan grid coordinates (e.g. segment endpoints outside
    // the floorplan bounding box) that the NUTSEngine should use when deriving
    // perpendicular intervals.  Must be called before run() / rerun_layer().
    void set_extra_grid_points(std::vector<int> xs, std::vector<int> ys);

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

private:
    const Floorplan& floorplan_;
    const LayerStack& layers_;
    double track_pitch_ = 1.0;
    std::vector<int> extra_x_, extra_y_;   // additional grid points from CongestionPlanner

    // User keepouts plus implicit solid-leaf-cell keepouts on every non-TOP
    // layer (Gap 2): a LOW segment may not route over a leaf cell, so the cell
    // behaves as a keepout for the whole lower stack.  TOP segments are filtered
    // out by KeepoutZone::layer_ids.  Mirrors the planner's band-capacity model.
    std::vector<KeepoutZone> low_keepouts() const;

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
    void solve_layer(std::vector<TrackSegment*>& segs,
                     const std::map<std::pair<int,int>, double>& pull_map,
                     const AlignMap& align_map,
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
        const std::map<std::pair<int,int>, double>& pull_map,
        const AlignMap& align_map,
        const std::map<std::pair<int,int>, std::vector<SpanAdjConn>>& rev_conn_map,
        std::map<std::pair<int,int>, TrackSegment*>& ts_ptr_map,
        const std::map<int, LayerConstraints>& seed_cons = {}) const;

    // Post-span-adjustment overlap repair: the final cross-layer span
    // adjustments can extend spans of already-packed layers, materialising
    // overlaps after packing.  Re-places victims of overlapping pairs within
    // their intervals against current adjusted spans (bounded iterations;
    // restores the original state unless the overlap count strictly drops).
    void repair_overlaps(
        std::vector<TrackSegment>& segments,
        const std::map<std::pair<int,int>, double>&                pull_map,
        const std::map<std::pair<int,int>, int>&                   net_pull_map,
        const AlignMap&                                            align_map,
        const std::map<std::pair<int,int>, std::vector<SpanAdjConn>>& rev_conn_map,
        std::map<std::pair<int,int>, TrackSegment*>&               ts_ptr_map) const;

    // Corner-overlap resolution (vertical-constraint style): two stubs that
    // collide on a layer can't be separated by moving either (they're
    // perp-locked) — only by adjusting the trunks they hang from.  Same trunk
    // layer → order the trunks (anchored-end rule, bottom-edge pack).  Different
    // trunk layers → nudge each trunk within its own layer to opposite sides of
    // a split.  Re-solve the affected trunk layer(s) under the accumulated
    // constraints; keep the result only while the total overlap count strictly
    // drops and no new interval violation appears (stop-&-reverse).
    void resolve_corner_overlaps(
        std::vector<TrackSegment>& segments,
        const std::map<std::pair<int,int>, double>&                pull_map,
        const std::map<std::pair<int,int>, int>&                   net_pull_map,
        const AlignMap&                                            align_map,
        const std::map<std::pair<int,int>, std::vector<SpanAdjConn>>& rev_conn_map,
        std::map<std::pair<int,int>, TrackSegment*>&               ts_ptr_map) const;

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
    void tighten_pulls(
        std::vector<TrackSegment>& segments,
        const std::map<std::pair<int,int>, int>&                   net_pull_map,
        const std::map<std::pair<int,int>, std::vector<SpanAdjConn>>& rev_conn_map,
        std::map<std::pair<int,int>, TrackSegment*>&               ts_ptr_map,
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
