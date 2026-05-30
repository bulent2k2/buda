#pragma once
#include <map>
#include <stdexcept>
#include <string>
#include <vector>
#include "topology.h"  // Rect

namespace interconnect {

// ---------------------------------------------------------------------------
// TrackSlot — one track position in a repeating pattern unit
// ---------------------------------------------------------------------------
struct TrackSlot {
    std::string type;        // POWER | GROUND | CLOCK | SHIELD | SIGNAL | CUSTOM
    std::string label;       // "VDD", "GND", "CLK1", user-defined
    double      width       = 1.0;
    double      space_after = 0.0;
};

// ---------------------------------------------------------------------------
// TrackPattern — a repeating unit that tiles the full layer extent
// ---------------------------------------------------------------------------
struct TrackPattern {
    double                  origin = 0.0;
    std::vector<TrackSlot>  slots;

    // Sum of (width + space_after) across all slots — the tiling period.
    double unit_pitch() const;

    // Fraction of unit_pitch occupied by SIGNAL slots.
    double signal_density() const;

    // 1 / signal_density — multiply abstract bus width by this to get physical reservation.
    double dilution_factor() const;

    // All (centre_position, slot) pairs whose centre lies in [lo, hi].
    // Tiles the pattern from origin outward to cover the interval.
    std::vector<std::pair<double, TrackSlot>> tracks_in_range(double lo, double hi) const;
};

// ---------------------------------------------------------------------------
// PatternOverride — region-scoped pattern that shadows the global one
// ---------------------------------------------------------------------------
struct PatternOverride {
    Rect         region;    // integer bounding box (Hanan-cell-aligned)
    int          layer_id = -1;
    TrackPattern pattern;   // local pattern with its own origin
};

// ---------------------------------------------------------------------------
// RoutingGrid — per-layer grid with optional region overrides
// ---------------------------------------------------------------------------
class RoutingGrid {
public:
    TrackPattern                global_pattern;
    std::vector<PatternOverride> overrides;
    std::vector<Rect>           keepouts;
    bool                        is_horizontal = true;

    void add_keepout(const Rect& bbox) { keepouts.push_back(bbox); }

    // Returns the first matching override pattern for point (x,y), else global.
    const TrackPattern& effective_pattern_at(double x, double y) const;

    // SIGNAL-only tracks whose centre falls in [lo, hi] at perpendicular coord x.
    std::vector<std::pair<double, TrackSlot>>
    signal_tracks_in(double x, double lo, double hi) const;
};

// ---------------------------------------------------------------------------
// RoutingGridStack — registry of per-layer RoutingGrid objects
// ---------------------------------------------------------------------------
class RoutingGridStack {
public:
    void define_layer(int layer_id, const TrackPattern& pattern, bool is_horizontal);

    void add_override(int layer_id,
                      int x1, int y1, int x2, int y2,
                      const TrackPattern& pattern);

    void add_keepout(int layer_id, int x1, int y1, int x2, int y2) {
        get_layer_grid(layer_id).add_keepout(Rect{x1, y1, x2, y2});
    }

    // Throws std::out_of_range if layer_id is not defined.
    RoutingGrid&       get_layer_grid(int layer_id);
    const RoutingGrid& get_layer_grid(int layer_id) const;

    bool has_layer(int layer_id) const;

private:
    std::map<int, RoutingGrid> layers_;
};

} // namespace interconnect
