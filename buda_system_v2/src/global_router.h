#pragma once
#include "bundler.h"
#include "topology.h"
#include "layering.h"
namespace interconnect {
struct GlobalCut {
    Point p1, p2;
    LayerDir dir;
    int layer_id = 0;
    double capacity;
    double current_usage;
};
struct BundleWrapper {
    Bundle original_bundle;
    std::vector<Topology> candidates;
    int selected_topology_index = 0;
    bool topology_pinned = false;  // if true, optimizer keeps selected_topology_index and only assigns layers
    double width = 1.0;
    int assigned_v_layer = -1;  // -1 = use segment layer_hint; set by GlobalRouter
    int assigned_h_layer = -1;  // -1 = use segment layer_hint; set by GlobalRouter
};
struct BundleAssignment {
    int bundle_id;
    int topo_index;
    int v_layer_id;
    int h_layer_id;
};
class GlobalRouter {
public:
    GlobalRouter(const Floorplan& fp, const LayerStack& layers);
    void set_layer_overhead(int layer_id, double overhead_percent);
    void build_congestion_map();
    std::vector<BundleAssignment> optimize_topologies(std::vector<BundleWrapper>& bundles, int max_iterations);
    const std::vector<GlobalCut>& get_cuts() const { return cuts_; }
private:
    double score_topology(const Topology& topo,
                          int v_layer_id, int h_layer_id,
                          double eff_v_width, double eff_h_width) const;
    void   apply_topology(const Topology& topo,
                          int v_layer_id, int h_layer_id,
                          double eff_v_width, double eff_h_width);
    const Floorplan& floorplan_;
    const LayerStack& layers_;
    std::map<int, double> layer_dilution_factors_;
    std::vector<GlobalCut> cuts_;
};
}