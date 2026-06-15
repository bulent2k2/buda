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

#pragma once
#include <vector>
#include <string>
#include <algorithm>
#include "topology.h"
namespace buda {
enum class LayerDir { HORIZONTAL, VERTICAL };
enum class LayerType { TOP, LOW };  // LOW kept for backward compatibility; treated as non-TOP
struct Layer {
    int id;
    std::string name;
    LayerDir dir;
    LayerType type;
    double dilution_factor = 1.0;
    // Channel units consumed per bus bit on this layer
    // (= track pattern unit_pitch / number of SIGNAL slots).  0 = unset:
    // fall back to the density model (base width x dilution_factor).
    double bit_pitch = 0.0;
    // Span preference: span_cost = kSpan * max(0, span_min-span, span-span_max).
    // Defaults give zero span cost for any segment length.
    int    span_min      = 0;
    int    span_max      = 1'000'000'000;
    // Per-layer kSpan override; negative means "use global kSpan".
    double kspan_override = -1.0;
};
class LayerStack {
public:
    void add_layer(int id, const std::string& name, LayerDir dir, LayerType type);
    // Set dilution factor (total_pitch / signal_pitch) for an already-added layer.
    void set_layer_dilution(int id, double factor);
    // Convenience: set dilution from overhead percentage (e.g. 20%).
    void set_layer_overhead(int id, double overhead_percent);
    // Set measured per-bit channel cost (from the layer's track pattern).
    void set_bit_pitch(int id, double pitch);
    // Effective channel footprint of a bus on this layer.  bits > 0 with a
    // measured bit_pitch gives the exact cost (bits x unit_pitch/n_signals);
    // otherwise the legacy density model (base_width x dilution_factor).
    double eff_bus_width(int bits, double base_width, int layer_id) const;
    // Set span preference window for an already-added layer.
    void set_layer_span(int id, int span_min, int span_max);
    // Override the global kSpan coefficient for one layer.
    void set_layer_kspan(int id, double kspan);

    const Layer* get_layer(int id) const;
    double       get_layer_dilution(int id) const;
    bool         has_layer(int id) const;
    bool         is_top(int id) const;

    LayerDir     get_layer_dir(int id) const;
    LayerType    get_layer_type(int id) const;
    // Returns the last-added TOP layer for the given direction (-1 if none).
    int get_top_layer(LayerDir dir) const;
    // Returns IDs of all layers with the given direction, sorted ascending.
    std::vector<int> get_layer_ids_by_dir(LayerDir dir) const;
    // Returns IDs sorted: TOP layers first (ascending), then non-TOP (ascending).
    std::vector<int> get_layer_ids_preferred(LayerDir dir) const;
private:
    std::vector<Layer> layers_;
    int top_horiz_id_ = -1;
    int top_vert_id_ = -1;
};
}