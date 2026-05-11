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
    // Per-segment layer assignments set by GlobalRouter (primary).
    // Index matches topo.segments of the selected topology.
    std::vector<int> seg_layers;
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

class GlobalRouter {
public:
    GlobalRouter(const Floorplan& fp, const LayerStack& layers);
    void set_layer_overhead(int layer_id, double overhead_percent);
    void build_congestion_map();
    std::vector<BundleAssignment> optimize_topologies(
            std::vector<BundleWrapper>& bundles, int max_iterations);
    const std::vector<GlobalCut>& get_cuts() const { return cuts_; }

private:
    // 2D per-segment scoring/application.
    double score_segment(const Segment& seg, int layer_id, double eff_width) const;
    void   apply_segment(const Segment& seg, int layer_id, double eff_width);

    // Band lookup: for a V-cut (is_vcut=true) look up in y_grid_, else x_grid_.
    int  find_band(bool is_vcut, int perp_pos) const;
    double get_dilution(int layer_id) const;
    double segment_affinity(double span_norm, int layer_id,
                            int top_layer,
                            const std::vector<int>& alt_layers) const;

    const Floorplan&  floorplan_;
    const LayerStack& layers_;
    std::map<int, double> layer_dilution_factors_;
    std::vector<GlobalCut> cuts_;
    std::vector<int> x_grid_, y_grid_;  // Hanan grids, populated by build_congestion_map
};

} // namespace interconnect
