#pragma once
#include "global_router.h"
#include <map>
#include <string>
#include <utility>
#include <vector>

namespace interconnect {

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
    double track_position = -1.0;         // assigned output
    bool   placed   = false;
    int    net_pull = 0;                  // from ConnSeg: >0 prefer hi, <0 prefer lo
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
class NUTSEngine {
public:
    explicit NUTSEngine(const Floorplan& fp, const LayerStack& ls);

    // Minimum gap between adjacent placed buses (default 1.0 layout unit).
    void set_track_pitch(double pitch);

    // Supply additional Hanan grid coordinates (e.g. segment endpoints outside
    // the floorplan bounding box) that the NUTSEngine should use when deriving
    // perpendicular intervals.  Must be called before run() / rerun_layer().
    void set_extra_grid_points(std::vector<int> xs, std::vector<int> ys);

    // Run NUTS on the bundles that have already been processed by GlobalRouter.
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
    std::vector<int> extra_x_, extra_y_;   // additional grid points from GlobalRouter

    // Build a flat list of TrackSegments from all selected topologies.
    std::vector<TrackSegment> extract_segments(
        const std::vector<BundleWrapper>& bundles,
        const std::vector<int>& x_grid,
        const std::vector<int>& y_grid) const;

    // Solve placement for one layer (modifies TrackSegment::track_position in place).
    // pull_map: (bundle_id, seg_idx) -> preferred perpendicular centre; absent entries
    // fall back to first-fit (lowest valid) behaviour.
    void solve_layer(std::vector<TrackSegment*>& segs,
                     const std::map<std::pair<int,int>, double>& pull_map) const;

    // First-fit: lowest valid placement position within [lo, hi].
    // Returns -1.0 if the interval is infeasible.
    double first_fit(double lo, double hi, double width,
                     const std::vector<std::pair<double,double>>& occupied) const;

    // Preferred-fit: valid placement position closest to 'preferred' within [lo, hi].
    // Returns -1.0 if the interval is infeasible.
    double preferred_fit(double lo, double hi, double width,
                         const std::vector<std::pair<double,double>>& occupied,
                         double preferred) const;
};

} // namespace interconnect
