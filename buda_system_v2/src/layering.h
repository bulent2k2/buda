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
    LayerDir  get_layer_dir(int id) const;
    LayerType get_layer_type(int id) const;
    int get_top_layer(LayerDir dir) const;
    // Returns IDs of all layers with the given direction, sorted ascending.
    std::vector<int> get_layer_ids_by_dir(LayerDir dir) const;
    // Returns IDs sorted: TOP layer first, then LOW layers ascending by ID.
    std::vector<int> get_layer_ids_preferred(LayerDir dir) const;
private:
    std::vector<Layer> layers_;
    int top_horiz_id_ = -1;
    int top_vert_id_ = -1;
};
}