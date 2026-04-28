#pragma once
#include "global_router.h"
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
    double span_lo  = 0, span_hi   = 0;   // routing-direction extent
    double interval_lo = 0, interval_hi = 0; // perpendicular placement range (hard)
    double width    = 1.0;                 // bus width in perpendicular direction
    double track_position = -1.0;         // assigned output
    bool   placed   = false;
};

struct NUTSResult {
    std::vector<TrackSegment> segments;
    int num_violations = 0;   // segments placed outside their interval
    int num_overlaps   = 0;   // pairs of segments that physically overlap after placement
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
    explicit NUTSEngine(const Floorplan& fp);

    // Minimum gap between adjacent placed buses (default 1.0 layout unit).
    void set_track_pitch(double pitch);

    // Run NUTS on the bundles that have already been processed by GlobalRouter.
    // Each BundleWrapper must have candidates filled and selected_topology_index set.
    NUTSResult run(const std::vector<BundleWrapper>& bundles);

private:
    const Floorplan& floorplan_;
    double track_pitch_ = 1.0;

    // Build a flat list of TrackSegments from all selected topologies.
    std::vector<TrackSegment> extract_segments(
        const std::vector<BundleWrapper>& bundles,
        const std::vector<int>& x_grid,
        const std::vector<int>& y_grid) const;

    // Solve placement for one layer (modifies TrackSegment::track_position in place).
    void solve_layer(std::vector<TrackSegment*>& segs) const;

    // First-fit within [lo, hi] given a sorted list of already-occupied intervals.
    // Returns the lowest valid placement position, or -1.0 if the interval is infeasible.
    double first_fit(double lo, double hi, double width,
                     const std::vector<std::pair<double,double>>& occupied) const;
};

} // namespace interconnect
