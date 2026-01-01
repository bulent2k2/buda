#pragma once
#include <vector>
#include <string>
#include <algorithm>
#include "topology.h"
namespace interconnect {
enum class LayerDir { HORIZONTAL, VERTICAL };
enum class LayerType { TOP, LOW };
struct Layer {
    int id;
    std::string name;
    LayerDir dir;
    LayerType type;
};
class LayerStack {
public:
    void add_layer(int id, const std::string& name, LayerDir dir, LayerType type);
    LayerDir get_layer_dir(int id) const; // Helper needed for router
    int get_top_layer(LayerDir dir) const;
private:
    std::vector<Layer> layers_;
    int top_horiz_id_ = -1;
    int top_vert_id_ = -1;
};
}