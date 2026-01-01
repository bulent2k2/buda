#include "global_router.h"
#include <limits>
#include <cmath>
namespace interconnect {
GlobalRouter::GlobalRouter(const Floorplan& fp, const LayerStack& ls) : floorplan_(fp), layers_(ls) {}
void GlobalRouter::set_layer_overhead(int layer_id, double overhead_percent) {
    if (overhead_percent >= 100.0) overhead_percent = 99.0;
    layer_dilution_factors_[layer_id] = 100.0 / (100.0 - overhead_percent);
}
double GlobalRouter::get_effective_width(double raw_width, int layer_id) const {
    return raw_width * (layer_dilution_factors_.count(layer_id) ? layer_dilution_factors_.at(layer_id) : 1.0);
}
double GlobalRouter::test_get_effective_width(double w, int l) const { return get_effective_width(w,l); }
void GlobalRouter::build_congestion_map() {
    // Prototype: Empty map, assume infinite capacity for visual stress test
    cuts_.clear(); 
}
void GlobalRouter::inject_congestion_on_layer(int layer_id, double usage) {
    // Prototype mock
}
void GlobalRouter::map_topology_to_cuts(const Topology& topo, double width, bool add_usage) {
    // Prototype mock
}
double GlobalRouter::calculate_cost(const Topology& topo, double width) const {
    // Simple wirelength for now
    double len = 0;
    for(auto& seg : topo.segments) {
        len += std::abs(seg.start.x - seg.end.x) + std::abs(seg.start.y - seg.end.y);
    }
    return len;
}
void GlobalRouter::optimize_topologies(std::vector<BundleWrapper>& bundles, int max_iterations) {
    // For prototype, just pick index 0 or random Z-shape
    // Let's bias towards Z-shapes for visual demo if available
    for(auto& bw : bundles) {
        bw.selected_topology_index = 0;
        for(size_t i=0; i<bw.candidates.size(); ++i) {
            if(bw.candidates[i].type == "Z_HVH") bw.selected_topology_index = i;
        }
    }
}
}