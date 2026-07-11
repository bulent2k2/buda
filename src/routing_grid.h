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
#include <map>
#include <stdexcept>
#include <string>
#include <vector>
#include "placed_segment.h"  // PreRoutedSegment
#include "topology.h"  // Rect

namespace buda {

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
    void add_keepout(const Rect& bbox) { keepouts_.push_back(bbox); }

    // Initialise the global pattern and routing direction (called by RoutingGridStack).
    void init(const TrackPattern& p, bool is_horiz) {
        global_pattern_ = p;
        is_horizontal_  = is_horiz;
    }

    // Append a region override (called by RoutingGridStack).
    void add_pattern_override(PatternOverride ov) {
        overrides_.push_back(std::move(ov));
    }

    // The layer-wide pattern (ignores region overrides).  Exposed for the
    // placement-phase math in align_bottom_up: an instance offset is
    // track-shift-invariant iff it is a multiple of this pattern's
    // unit_pitch — overrides are absolute-rect-keyed and cannot be fixed by
    // translation, so they are the check's job, not the aligner's.
    const TrackPattern& global_pattern() const { return global_pattern_; }
    bool has_overrides() const { return !overrides_.empty(); }

    // Returns the first matching override pattern for point (x,y), else global.
    const TrackPattern& effective_pattern_at(double x, double y) const;

    // SIGNAL-only tracks whose centre falls in [lo, hi] at perpendicular coord x.
    std::vector<std::pair<double, TrackSlot>>
    signal_tracks_in(double x, double lo, double hi) const;

    // COUNT of the same SIGNAL tracks, computed without materializing any vector
    // (hot path: the congestion planner's signal-track band capacity calls this
    // once per candidate segment per band per iteration and only needs the count).
    // Identical result to signal_tracks_in(...).size(), no heap allocation.
    int count_signal_tracks_in(double x, double lo, double hi) const;

    // Span-aware sibling of signal_tracks_in: SIGNAL tracks whose centre falls
    // in [perp_lo, perp_hi], with a track blocked when a keepout's PERP extent
    // covers the track centre AND its ALONG extent overlaps [along_lo,
    // along_hi] — the whole span the wire will occupy, not one sample point.
    // signal_tracks_in(x, …) is the along_lo == along_hi == x special case; a
    // keepout that overlaps the span but misses the sample point is invisible
    // to it, which let DetailedNUTS route bits straight through a keepout
    // (keepout-model audit).  The pattern is still resolved at the along
    // MIDPOINT (effective_pattern_at's point sampling — the documented
    // override approximation, unchanged here).
    std::vector<std::pair<double, TrackSlot>>
    signal_tracks_in_span(double along_lo, double along_hi,
                          double perp_lo, double perp_hi) const;

    // COUNT of the same span-aware SIGNAL tracks, without materializing a
    // vector (hot-path sibling, like count_signal_tracks_in for the point
    // version): the congestion planner's kPeak supply floor calls this once
    // per candidate segment per band choice.  Identical result to
    // signal_tracks_in_span(...).size().
    int count_signal_tracks_in_span(double along_lo, double along_hi,
                                    double perp_lo, double perp_hi) const;

    // The layer's NON-SIGNAL track slots (POWER / GROUND / CLOCK / SHIELD /
    // CUSTOM) materialized as first-class PreRoutedSegments — Phase G of
    // docs/internal/placed_segment_preroutes.md.  Global-pattern slots whose
    // centre lies in [perp_lo, perp_hi] span the [along_lo, along_hi] window
    // MINUS every override shadow whose perp range contains the slot centre
    // (a shadowed slot splits into the unshadowed pieces — inside an override
    // region the effective pattern is the override's, matching what
    // effective_pattern_at/signal_tracks_in sample); each PatternOverride's
    // local pattern is enumerated within (region ∩ perp window) with the span
    // clipped to (region ∩ along window) — bands break at region boundaries.
    // SIGNAL slots (include_signal) additionally break at KEEPOUTS, matching
    // signal_tracks_in's filter — a rail is never drawn where a bit cannot
    // land; non-SIGNAL slots ignore keepouts (a pre-route is a physical rail).
    // track_index is a running index per enumeration; `layer` is stamped by
    // RoutingGridStack::preroutes.
    //
    // include_signal additionally emits the SIGNAL slots (slot_type "SIGNAL")
    // — those are NOT pre-routes, but the same enumeration is what a track
    // -rail display needs (the visualizer's [Tracks] view), and keeping one
    // walker avoids a second global+override tiling implementation.
    std::vector<PreRoutedSegment> preroutes_in(
        double perp_lo, double perp_hi,
        double along_lo, double along_hi,
        bool include_signal = false) const;

    bool is_horizontal() const { return is_horizontal_; }

    // Raw keepout rects (read-only) — DetailedNUTS's post-placement crossing
    // cull re-checks final junction-adjusted bit spans against them.
    const std::vector<Rect>& keepouts() const { return keepouts_; }

private:
    TrackPattern                 global_pattern_;
    std::vector<PatternOverride> overrides_;
    std::vector<Rect>            keepouts_;
    bool                         is_horizontal_ = true;
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

    // Pre-routes of one layer within the given windows (see
    // RoutingGrid::preroutes_in — include_signal adds the SIGNAL slots for
    // track-rail display), with each segment's `layer` stamped.
    std::vector<PreRoutedSegment> preroutes(
        int layer_id, double perp_lo, double perp_hi,
        double along_lo, double along_hi,
        bool include_signal = false) const;

    bool has_layer(int layer_id) const;

private:
    std::map<int, RoutingGrid> layers_;
};

} // namespace buda
