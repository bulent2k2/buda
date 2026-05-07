#include "layering.h"
namespace interconnect {
void LayerStack::add_layer(int id, const std::string& name, LayerDir dir, LayerType type) {
    layers_.push_back({id, name, dir, type});
    if (type == LayerType::TOP) {
        if (dir == LayerDir::HORIZONTAL) top_horiz_id_ = id;
        else top_vert_id_ = id;
    }
}
LayerDir LayerStack::get_layer_dir(int id) const {
    for(auto& l : layers_) if(l.id == id) return l.dir;
    return LayerDir::HORIZONTAL;
}
LayerType LayerStack::get_layer_type(int id) const {
    for(auto& l : layers_) if(l.id == id) return l.type;
    return LayerType::LOW;
}
std::vector<int> LayerStack::get_layer_ids_preferred(LayerDir dir) const {
    int top_id = get_top_layer(dir);
    std::vector<int> ids = get_layer_ids_by_dir(dir);
    // Stable-sort: TOP layer first, LOW layers keep ascending order after it.
    std::stable_sort(ids.begin(), ids.end(), [&](int a, int b) {
        bool a_top = (a == top_id);
        bool b_top = (b == top_id);
        if (a_top != b_top) return a_top;
        return false; // preserve original ascending order for LOW layers
    });
    return ids;
}
int LayerStack::get_top_layer(LayerDir dir) const {
    return (dir == LayerDir::HORIZONTAL) ? top_horiz_id_ : top_vert_id_;
}
std::vector<int> LayerStack::get_layer_ids_by_dir(LayerDir dir) const {
    std::vector<int> ids;
    for (const auto& l : layers_)
        if (l.dir == dir) ids.push_back(l.id);
    std::sort(ids.begin(), ids.end());
    return ids;
}
}