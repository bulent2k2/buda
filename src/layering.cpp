/*
 * Copyright 2026 Ben Bulent Basaran
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include "layering.h"
namespace buda {
void LayerStack::add_layer(int id, const std::string& name, LayerDir dir, LayerType type) {
    layers_.push_back({id, name, dir, type, 1.0});
    if (type == LayerType::TOP) {
        if (dir == LayerDir::HORIZONTAL) top_horiz_id_ = id;
        else top_vert_id_ = id;
    }
}
// Drop a layer so a later declaration can REPLACE it.  Needed only by the
// LEF tech import's precedence rule (Phase 2b): an explicit `def_layer`
// outranks imported technology data in EITHER declaration order, and
// add_layer appends — a duplicate id would leave both rows in the vector,
// with lookups silently taking the first (i.e. the imported one).
//
// The TOP bookkeeping is re-derived rather than patched: it is a pair of
// cached ids, and clearing one without rescanning would leave the stack with
// no top layer in that direction even though another TOP layer exists.
bool LayerStack::remove_layer(int id) {
    const size_t before = layers_.size();
    layers_.erase(std::remove_if(layers_.begin(), layers_.end(),
                                 [id](const Layer& l) { return l.id == id; }),
                  layers_.end());
    if (layers_.size() == before) return false;
    top_horiz_id_ = top_vert_id_ = -1;
    for (const auto& l : layers_) {
        if (l.type != LayerType::TOP) continue;
        if (l.dir == LayerDir::HORIZONTAL) top_horiz_id_ = l.id;
        else top_vert_id_ = l.id;
    }
    return true;
}

void LayerStack::set_layer_dilution(int id, double factor) {
    for (auto& l : layers_) if (l.id == id) { l.dilution_factor = factor; return; }
}
void LayerStack::set_layer_overhead(int id, double overhead_percent) {
    if (overhead_percent >= 100.0) return;
    set_layer_dilution(id, 100.0 / (100.0 - overhead_percent));
}
void LayerStack::set_layer_span(int id, int span_min, int span_max) {
    for (auto& l : layers_) if (l.id == id) { l.span_min = span_min; l.span_max = span_max; return; }
}
void LayerStack::set_layer_kspan(int id, double kspan) {
    for (auto& l : layers_) if (l.id == id) { l.kspan_override = kspan; return; }
}
void LayerStack::set_bit_pitch(int id, double pitch) {
    for (auto& l : layers_) if (l.id == id) { l.bit_pitch = pitch; return; }
}
void LayerStack::set_gds_mapping(int id, int gds_layer, int gds_datatype) {
    for (auto& l : layers_)
        if (l.id == id) { l.gds_layer = gds_layer; l.gds_datatype = gds_datatype; return; }
}
int LayerStack::get_gds_layer(int id) const {
    const Layer* l = get_layer(id);
    return l ? l->gds_layer : -1;
}
int LayerStack::get_gds_datatype(int id) const {
    const Layer* l = get_layer(id);
    return l ? l->gds_datatype : 0;
}
int LayerStack::layer_for_gds(int gds_layer, int gds_datatype) const {
    for (const auto& l : layers_)
        if (l.gds_layer == gds_layer && l.gds_datatype == gds_datatype &&
            l.gds_layer >= 0)
            return l.id;
    return -1;
}
std::vector<std::pair<int,int>> LayerStack::gds_mapped_pairs() const {
    std::vector<std::pair<int,int>> out;
    for (const auto& l : layers_)
        if (l.gds_layer >= 0) out.emplace_back(l.gds_layer, l.gds_datatype);
    return out;
}
double LayerStack::eff_bus_width(int bits, double base_width, int layer_id) const {
    const Layer* l = get_layer(layer_id);
    if (l && l->bit_pitch > 0.0 && bits > 0)
        return bits * l->bit_pitch;
    return base_width * (l ? l->dilution_factor : 1.0);
}
const Layer* LayerStack::get_layer(int id) const {
    for (const auto& l : layers_) if (l.id == id) return &l;
    return nullptr;
}
double LayerStack::bit_pitch(int id) const {
    const Layer* l = get_layer(id);
    return l ? l->bit_pitch : 0.0;
}
double LayerStack::get_layer_dilution(int id) const {
    const Layer* l = get_layer(id);
    return l ? l->dilution_factor : 1.0;
}
bool LayerStack::has_layer(int id) const {
    return get_layer(id) != nullptr;
}
bool LayerStack::is_top(int id) const {
    const Layer* l = get_layer(id);
    return l && l->type == LayerType::TOP;
}
bool LayerStack::is_above_top(int id) const {
    const Layer* l = get_layer(id);
    if (!l || l->type == LayerType::TOP) return false;
    int top = get_top_layer(l->dir);
    return top >= 0 && id > top;
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
    std::vector<int> ids = get_layer_ids_by_dir(dir);
    // Stable-sort: TOP layers first (ascending), then non-TOP (ascending).
    std::stable_sort(ids.begin(), ids.end(), [&](int a, int b) {
        bool a_top = is_top(a);
        bool b_top = is_top(b);
        if (a_top != b_top) return a_top;
        return false; // preserve original ascending order within each group
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