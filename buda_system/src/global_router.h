#pragma once
#include "bundler.h"
#include "topology.h"
#include "layering.h"
namespace interconnect {
struct GlobalCut {
    Point p1, p2;
    LayerDir dir;
    double capacity;
    double current_usage;
};
struct BundleWrapper {
    Bundle original_bundle;
    std::vector<Topology> candidates;
    int selected_topology_index = 0;
    double width = 1.0;
};
class GlobalRouter {
public:
    GlobalRouter(const Floorplan& fp, const LayerStack& layers);
    void set_layer_overhead(int layer_id, double overhead_percent);
    void build_congestion_map();
    void optimize_topologies(std::vector<BundleWrapper>& bundles, int max_iterations);
    double test_get_effective_width(double w, int l) const; // Exposed for test
    void inject_congestion_on_layer(int layer_id, double usage); // Exposed for test
private:
    const Floorplan& floorplan_;
    const LayerStack& layers_;
    std::map<int, double> layer_dilution_factors_;
    std::vector<GlobalCut> cuts_;
    double get_effective_width(double raw_width, int layer_id) const;
    void map_topology_to_cuts(const Topology& topo, double width, bool add_usage);
    double calculate_cost(const Topology& topo, double width) const;
};
}