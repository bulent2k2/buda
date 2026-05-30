#include "routing_grid.h"
#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace interconnect {

// ---------------------------------------------------------------------------
// TrackPattern
// ---------------------------------------------------------------------------

double TrackPattern::unit_pitch() const {
    double total = 0.0;
    for (const auto& s : slots)
        total += s.width + s.space_after;
    return total;
}

double TrackPattern::signal_density() const {
    double up = unit_pitch();
    if (up <= 0.0) return 0.0;
    double sig = 0.0;
    for (const auto& s : slots)
        if (s.type == "SIGNAL") sig += s.width;
    return sig / up;
}

double TrackPattern::dilution_factor() const {
    double sd = signal_density();
    return (sd > 0.0) ? 1.0 / sd : 1.0;
}

std::vector<std::pair<double, TrackSlot>>
TrackPattern::tracks_in_range(double lo, double hi) const {
    double up = unit_pitch();
    if (up <= 0.0 || slots.empty() || lo > hi) return {};

    // Calculate the first unit index n such that origin + n*up could contain centres >= lo.
    // std::floor handles negative offsets correctly.
    int n_start = static_cast<int>(std::floor((lo - origin) / up)) - 1;

    std::vector<std::pair<double, TrackSlot>> result;
    for (int n = n_start; ; ++n) {
        double unit_start = origin + static_cast<double>(n) * up;
        if (unit_start > hi) break;

        double pos = unit_start;
        for (const auto& slot : slots) {
            double centre = pos + slot.width / 2.0;
            if (centre >= lo && centre <= hi)
                result.push_back({centre, slot});
            pos += slot.width + slot.space_after;
        }
    }
    return result;
}

// ---------------------------------------------------------------------------
// RoutingGrid
// ---------------------------------------------------------------------------

const TrackPattern& RoutingGrid::effective_pattern_at(double x, double y) const {
    for (const auto& ov : overrides) {
        if (x >= static_cast<double>(ov.region.x1) &&
            x <= static_cast<double>(ov.region.x2) &&
            y >= static_cast<double>(ov.region.y1) &&
            y <= static_cast<double>(ov.region.y2))
            return ov.pattern;
    }
    return global_pattern;
}

std::vector<std::pair<double, TrackSlot>>
RoutingGrid::signal_tracks_in(double x, double lo, double hi) const {
    const TrackPattern& pat = effective_pattern_at(x, lo);
    auto all = pat.tracks_in_range(lo, hi);
    std::vector<std::pair<double, TrackSlot>> result;
    result.reserve(all.size());
    for (auto& p : all) {
        if (p.second.type == "SIGNAL") {
            bool blocked = false;
            for (const auto& koz : keepouts) {
                // p.first is the fixed coordinate of the track (Y if horizontal, X if vertical).
                // x is the coordinate along the track span (X if horizontal, Y if vertical).
                double px = is_horizontal ? x : p.first;
                double py = is_horizontal ? p.first : x;
                if (px >= koz.x1 && px <= koz.x2 &&
                    py >= koz.y1 && py <= koz.y2) {
                    blocked = true;
                    break;
                }
            }
            if (!blocked) result.push_back(std::move(p));
        }
    }
    return result;
}

// ---------------------------------------------------------------------------
// RoutingGridStack
// ---------------------------------------------------------------------------

void RoutingGridStack::define_layer(int layer_id, const TrackPattern& pattern, bool is_horizontal) {
    auto& g = layers_[layer_id];
    g.global_pattern = pattern;
    g.is_horizontal  = is_horizontal;
}

void RoutingGridStack::add_override(int layer_id,
                                    int x1, int y1, int x2, int y2,
                                    const TrackPattern& pattern) {
    PatternOverride ov;
    ov.region   = Rect{x1, y1, x2, y2};
    ov.layer_id = layer_id;
    ov.pattern  = pattern;
    layers_[layer_id].overrides.push_back(std::move(ov));
}

RoutingGrid& RoutingGridStack::get_layer_grid(int layer_id) {
    auto it = layers_.find(layer_id);
    if (it == layers_.end())
        throw std::out_of_range("RoutingGridStack: layer " +
                                std::to_string(layer_id) + " not defined");
    return it->second;
}

const RoutingGrid& RoutingGridStack::get_layer_grid(int layer_id) const {
    auto it = layers_.find(layer_id);
    if (it == layers_.end())
        throw std::out_of_range("RoutingGridStack: layer " +
                                std::to_string(layer_id) + " not defined");
    return it->second;
}

bool RoutingGridStack::has_layer(int layer_id) const {
    return layers_.count(layer_id) > 0;
}

} // namespace interconnect
