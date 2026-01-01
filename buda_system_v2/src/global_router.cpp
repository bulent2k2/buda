#include "global_router.h"
#include <iostream>
namespace interconnect {
GlobalRouter::GlobalRouter(const Floorplan& fp, const LayerStack& ls) : floorplan_(fp), layers_(ls) {}
void GlobalRouter::set_layer_overhead(int layer_id, double overhead_percent) {}
void GlobalRouter::build_congestion_map() {}
void GlobalRouter::optimize_topologies(std::vector<BundleWrapper>& bundles, int max_iterations) {
    // MOCK SELECTION LOGIC FOR DEMO PURPOSES
    // In a real system, this uses congestion costs.
    // Here, we select based on bundle name hints to force L, Z, and U visualization.
    for(auto& bw : bundles) {
        bw.selected_topology_index = 0; // Default to first (usually L)
        if (bw.original_bundle.get_net_names().empty()) continue;
        std::string first_net = bw.original_bundle.get_net_names()[0];

        // Bundle 2 -> Force Z-shape
        if (first_net.find("b2_") != std::string::npos) {
            for(size_t i=0; i<bw.candidates.size(); ++i) {
                if(bw.candidates[i].type.find("Z_") == 0) { bw.selected_topology_index = i; break; }
            }
        } 
        // Bundle 3 -> Force U-shape (Detour)
        else if (first_net.find("b3_") != std::string::npos) {
            for(size_t i=0; i<bw.candidates.size(); ++i) {
                if(bw.candidates[i].type.find("U_") == 0) { bw.selected_topology_index = i; break; }
            }
        }
        // Bundle 1 stays L-shape
    }
}
}