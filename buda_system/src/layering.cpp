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
int LayerStack::get_top_layer(LayerDir dir) const {
    return (dir == LayerDir::HORIZONTAL) ? top_horiz_id_ : top_vert_id_;
}
}