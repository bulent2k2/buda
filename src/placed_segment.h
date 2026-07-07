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
#include <string>

// The placed-segment type hierarchy (CLAUDE.md "Segment Type Hierarchy",
// realized by docs/internal/placed_segment_preroutes.md — Phase G of the
// NUTS/DNUTS refactor):
//
//   PlacedSegmentBase        kind, layer, span, track_position, width, placed
//   ├── TrackSegment (BUS)   the stage-4 placed bus segment      (nuts.h)
//   ├── NetSegment   (NET)   the stage-9 placed bit-wire         (detailed_nuts.h)
//   └── PreRoutedSegment     a POWER/GROUND/CLOCK/SHIELD track   (below)
//
// BusSegment (detailed_nuts.h) deliberately stays OUTSIDE the hierarchy: it
// is the stage-9 *input descriptor* (intervals, bit_width, bit_order,
// connections) with no placement of its own — the placed abstract bus
// segment is TrackSegment.  Folding the two would rename bound pybind
// fields (track_position <-> abstract_pos) for zero behavior gain; revisit
// only with a deliberate binding-breaking version.

namespace buda {

enum class SegKind { BUS, NET, PREROUTE };

// What every placed, physical wire/track shares: which layer it is on, its
// routing-direction extent (span_lo/span_hi — nominal endpoint identity, may
// be stored reversed after placement; see nuts_geom.h), its perpendicular
// position and width, and whether it has actually been placed.
struct PlacedSegmentBase {
    SegKind kind;
    int     layer   = 0;
    double  span_lo = 0, span_hi = 0;    // routing-direction extent
    double  track_position = 0.0;        // perpendicular position
    double  width   = 1.0;
    bool    placed  = false;
    explicit PlacedSegmentBase(SegKind k) : kind(k) {}
};

// One pre-routed track: a non-SIGNAL TrackSlot occurrence (POWER / GROUND /
// CLOCK / SHIELD / CUSTOM) materialized as an explicit object, enumerated
// from the routing grid by RoutingGridStack::preroutes().  Pre-routes exist
// before signal routing and are placed by definition.
struct PreRoutedSegment : PlacedSegmentBase {
    PreRoutedSegment() : PlacedSegmentBase(SegKind::PREROUTE) { placed = true; }
    std::string label;           // TrackSlot label ("VDD", "CLK1", ...), else its type
    std::string slot_type;       // the TrackSlot type ("POWER", "GROUND", ...)
    int         track_index = -1;  // index within its layer's enumeration
                                   // (a viz/report identity, not a global id)
};

} // namespace buda
