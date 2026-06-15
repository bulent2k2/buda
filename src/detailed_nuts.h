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
#include <limits>
#include <map>
#include <string>
#include <tuple>
#include <vector>
#include "routing_grid.h"

namespace buda {

struct BusSegmentConn {
    int    seg_idx     = -1;
    double at_pos      = 0.0;
    bool   is_endpoint = false;
    bool   lo_end      = false; // true if connection is at the lo-half of this segment
};

struct BusSegment {
    int         bundle_id         = 0;
    int         seg_idx           = 0;
    int         layer             = 0;
    double      span_lo           = 0.0;
    double      span_hi           = 0.0;
    double      interval_lo       = 0.0;
    double      interval_hi       = 0.0;
    int         bit_width         = 1;
    std::string bit_order         = "LO_HI";  // "LO_HI" or "HI_LO"
    bool        timing_critical   = false;
    
    // Explicit connectivity list for bit-wire span adjustment.
    std::vector<BusSegmentConn> connections;

    // Abstract NUTS track_position used as anchor for Option B ordering; NaN = unset (fallback)
    double      abstract_pos      = std::numeric_limits<double>::quiet_NaN();
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
    static bool signals_contiguous(
        double pos_a, double pos_b,
        const std::vector<std::pair<double, TrackSlot>>& all_tracks);
};

} // namespace buda
