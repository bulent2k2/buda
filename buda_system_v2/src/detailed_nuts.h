#pragma once
#include <string>
#include <vector>
#include "routing_grid.h"

namespace interconnect {

struct BusSegment {
    int         bundle_id       = 0;
    int         seg_idx         = 0;
    int         layer           = 0;
    double      span_lo         = 0.0;
    double      span_hi         = 0.0;
    double      interval_lo     = 0.0;
    double      interval_hi     = 0.0;
    int         bit_width       = 1;
    std::string bit_order       = "LO_HI";  // "LO_HI" or "HI_LO"
    bool        timing_critical = false;
};

struct NetSegment {
    int    bundle_id      = 0;
    int    seg_idx        = 0;
    int    bit_index      = 0;
    double track_position = 0.0;
    double width          = 1.0;
    int    layer          = 0;
    double span_lo        = 0.0;
    double span_hi        = 0.0;
};

struct DetailedNUTSResult {
    std::vector<NetSegment> net_segments;
    int num_unplaced = 0;
};

class DetailedNUTSEngine {
public:
    explicit DetailedNUTSEngine(const RoutingGridStack& stack);
    DetailedNUTSResult run(const std::vector<BusSegment>& bus_segments) const;

private:
    const RoutingGridStack& stack_;

    // Returns index into signal_tracks of the first contiguous window of size
    // bit_width, searching from lo to hi end.  Returns -1 if none found.
    static int find_contiguous_window_lo(
        const std::vector<std::pair<double, TrackSlot>>& signal_tracks,
        const std::vector<std::pair<double, TrackSlot>>& all_tracks,
        int bit_width);

    // Same but scans from high end; returns the lowest index of the window.
    static int find_contiguous_window_hi(
        const std::vector<std::pair<double, TrackSlot>>& signal_tracks,
        const std::vector<std::pair<double, TrackSlot>>& all_tracks,
        int bit_width);

    static bool signals_contiguous(
        double pos_a, double pos_b,
        const std::vector<std::pair<double, TrackSlot>>& all_tracks);
};

} // namespace interconnect
