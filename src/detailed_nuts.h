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
#include "nuts.h"          // BundleWrapper / NUTSResult (make_bus_segments)
#include "placed_segment.h"
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

    // Along-axis coordinates of this segment's BUSTERM block-face taps.  The
    // per-bit span-follow below snaps endpoints to connected SEG bits, which can
    // overwrite a face end (the abstract span already reaches it); these anchors
    // re-extend each bit's span to its block face so the tap survives (mirror of
    // TrackSegment::busterm_faces in the abstract stage).
    std::vector<double> busterm_faces;

    // Abstract NUTS track_position used as anchor for Option B ordering; NaN = unset (fallback)
    double      abstract_pos      = std::numeric_limits<double>::quiet_NaN();

    // Cross-trunk-layer corner resolution: restrict this segment's signal-track
    // choice to [track_lo_bound, track_hi_bound] so it stays on the bounded side
    // of the split (carried from the abstract trunk's bound).  Default = unbounded.
    double      track_lo_bound    = -std::numeric_limits<double>::infinity();
    double      track_hi_bound    =  std::numeric_limits<double>::infinity();
};

// One bit-wire; output of stage 9 (kind NET in the placed-segment hierarchy
// — see placed_segment.h; layer/span/track_position/width live on the base
// with the same names).  Rows are emitted only for bits that actually got a
// track (unplaced bits are counted in num_unplaced, not materialized), so
// `placed` is true on every emitted row.
struct NetSegment : PlacedSegmentBase {
    NetSegment() : PlacedSegmentBase(SegKind::NET) { placed = true; }
    int    bundle_id      = 0;
    int    seg_idx        = 0;
    int    bit_index      = 0;
};

// One per-bit layer transition between two connected segments' bit-wires.
// Fans out from the bundle-level symbolic bus-via: same key
// (bundle_id, from_seg, to_seg), one NetVia per bit_index. from_seg < to_seg
// always (the pair is deduped on (min, max)).
struct NetVia {
    int    bundle_id  = 0;
    int    from_seg   = 0;    // min of the connected seg pair
    int    to_seg     = 0;    // max of the connected seg pair
    int    bit_index  = 0;    // LOGICAL bit (bit_order already applied)
    int    from_layer = 0;    // layer of from_seg's bit-wire
    int    to_layer   = 0;    // layer of to_seg's bit-wire
    double x = 0.0, y = 0.0;  // per-bit crossing (µm)
};

struct DetailedNUTSResult {
    std::vector<NetSegment> net_segments;
    std::vector<NetVia>     net_vias;
    int num_unplaced = 0;
};

class DetailedNUTSEngine {
public:
    explicit DetailedNUTSEngine(const RoutingGridStack& stack);
    DetailedNUTSResult run(const std::vector<BusSegment>& bus_segments) const;

private:
    const RoutingGridStack& stack_;

    // The three stages of run(), in order (each mutates `result` in place):
    // per-layer bit placement in abstract_pos order (Option B), the per-bit
    // span-follow to connected bits' exact tracks (+ BUSTERM face re-extend),
    // and the per-bit via fan-out of the bundle-level symbolic bus-vias.
    void place_by_layer(const std::vector<BusSegment>& bus_segs,
                        DetailedNUTSResult& result) const;
    void adjust_bit_spans(const std::vector<BusSegment>& bus_segs,
                          DetailedNUTSResult& result) const;
    void emit_bit_vias(const std::vector<BusSegment>& bus_segs,
                       DetailedNUTSResult& result) const;

    // Returns index into signal_tracks of the first contiguous window of size
    // bit_width, searching from lo to hi end.  Returns -1 if none found.
    static bool signals_contiguous(
        double pos_a, double pos_b,
        const std::vector<std::pair<double, TrackSlot>>& all_tracks);
};

// Build the stage-9 input from stage 4's output — the former Python handoff
// (buda_session/nutsflow.py::_run_detailed_nuts built BusSegments field by
// field and re-derived connectivity via a fresh ConnTopology), single-sourced
// here so the SEG-connection / lo_end / BUSTERM-face derivation exists once:
// every TrackSegment becomes a BusSegment (track_position -> abstract_pos,
// corner bounds carried), bit_width from the bundle's net count, and the
// per-segment connections/faces from the selected topology's cached analysis
// (ConnTopology::build) — exactly the values the abstract solve placed with.
std::vector<BusSegment> make_bus_segments(
    const std::vector<BundleWrapper>& bundles,
    const NUTSResult& nuts_result,
    const Floorplan& floorplan,
    const std::string& bit_order = "LO_HI");

} // namespace buda
