#pragma once
#include "bundler.h"
#include "topology.h"
#include "layering.h"
namespace interconnect {

// One Hanan-grid cut subdivided into perpendicular bands.
// V-cut (dir=VERTICAL):   x fixed, bands along Y grid → counts H-segments crossing it.
// H-cut (dir=HORIZONTAL): y fixed, bands along X grid → counts V-segments crossing it.
struct GlobalCut {
    Point    p1, p2;           // endpoints of the cut line (for visualisation)
    int      cut_coord = 0;    // x_mid (V-cut) or y_mid (H-cut)
    LayerDir dir;
    int      layer_id = 0;
    std::vector<double> band_cap;    // capacity per perpendicular Hanan band
    std::vector<double> band_usage;  // accumulated demand per band
};

struct BundleWrapper {
    Bundle original_bundle;
    std::vector<Topology> candidates;
    int selected_topology_index = 0;
    bool topology_pinned = false;
    double width = 1.0;
    // Per-segment layer assignments set by CongestionPlanner (primary).
    // Index matches topo.segments of the selected topology.
    std::vector<int> seg_layers;
    // Manual layer overrides per segment.  Values are layer IDs, or -1
    // for no override (let the planner decide).
    std::vector<int> pinned_seg_layers;
    // Legacy per-direction overrides (set by post_nuts; secondary to seg_layers).
    int assigned_v_layer = -1;
    int assigned_h_layer = -1;
};

struct BundleAssignment {
    int bundle_id;
    int topo_index;
    int v_layer_id;              // representative V layer (logging)
    int h_layer_id;              // representative H layer (logging)
    std::vector<int> seg_layers; // per-segment assignments (same order as topo.segments)
};

class CongestionPlanner {
public:
    CongestionPlanner(const Floorplan& fp, const LayerStack& layers);
    // Tune global planner knobs.  Recognised names:
    //   "kCong"            — overflow cost coefficient: cost = kCong*(overflow/cap) (default 1.0)
    //   "kSpan"            — span-mismatch cost per layout-unit (default 0.001)
    //   "base_cost_non_top"— flat penalty for non-TOP layers (default 0.5)
    void set_planner_param(const std::string& name, double value);
    void build_congestion_map();
    std::vector<BundleAssignment> optimize_topologies(
            std::vector<BundleWrapper>& bundles, int max_iterations);
    const std::vector<GlobalCut>& get_cuts() const { return cuts_; }
    const std::vector<int>& get_x_grid() const { return x_grid_; }
    const std::vector<int>& get_y_grid() const { return y_grid_; }

private:
    void _rebuild_cuts();
    // Overflow congestion cost: kCong * max(0, (usage+eff-cap)/cap).  Zero below capacity.
    double cong_cost_segment(const Segment& seg, int layer_id, double eff_width) const;
    // Raw overflow for logging (usage+eff - cap, clamped to 0).
    double score_segment(const Segment& seg, int layer_id, double eff_width) const;
    void   apply_segment(const Segment& seg, int layer_id, double eff_width);
    // Span-mismatch cost: kSpan(layer) * max(0, span_min-span, span-span_max).
    double span_cost_for(double seg_span, int layer_id) const;

    int    find_band(bool is_vcut, int perp_pos) const;

    const Floorplan&  floorplan_;
    const LayerStack& layers_;
    std::vector<GlobalCut> cuts_;
    std::vector<int> x_grid_, y_grid_;

    // Tunable cost coefficients.
    double kCong_             = 1.0;
    double kSpan_             = 0.001;
    double base_cost_non_top_ = 0.5;
};

} // namespace interconnect
