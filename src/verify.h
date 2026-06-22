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
#include "conn_topology.h"
#include "topology.h"
#include "layering.h"
#include "nuts.h"
#include "detailed_nuts.h"
#include <string>
#include <vector>

namespace buda {

enum class ViolationKind {
    SEG_OPEN,     // two connected segments fail to touch after placement
    BUSTERM_OPEN, // a required block has no BUSTERM conn and no pass-through segment
    BUSTERM_FACE, // a BUSTERM conn's perp position is outside the block face
    UNPLACED,     // a bit has no concrete track assignment after DetailedNUTS
    LAYER_DIR,    // a segment is assigned to a layer whose routing direction
                  // does not match the segment's orientation (unbuildable wire)
    FEEDTHRU_RELAY, // a (single-rect) block is used as a feedthrough relay: two
                  // segments connect to it (BUSTERM) but are not joined by any
                  // wire path, so they rely on the block's internal routing
};

struct ConnViolation {
    ViolationKind kind;
    int           bundle_id  = -1;
    int           seg_idx    = -1;
    int           seg_idx2   = -1;  // for SEG_OPEN: the other segment
    int           bit_index  = -1;  // -1 for topo/nuts; bit position for dnuts
    std::string   block_name;       // for BUSTERM_OPEN / BUSTERM_FACE
    std::string   message;
};

struct ConnResult {
    std::vector<ConnViolation> violations;
    bool ok() const { return violations.empty(); }
};

// Topology-level check (nominal perp_pos from ConnTopology).
// Checks SEG connection continuity, BUSTERM face validity, and block coverage
// (including pass-through blocks that have no explicit BUSTERM endpoint).
ConnResult check_topo(const ConnTopology& ct, const Topology& topo,
                      const Floorplan& fp, int bundle_id);

// NUTS-level check: same topology structure but positions from TrackSegments.
// Includes block-coverage check for pass-through blocks at placed positions
// and layer-direction validity (H segment on H layer, V on V).
ConnResult check_nuts(const ConnTopology& ct, const NUTSResult& nuts,
                      const Topology& topo, const Floorplan& fp,
                      const LayerStack& layers, int bundle_id);

// Detailed-NUTS-level: per-bit connectivity check using NetSegment positions.
// Includes block-coverage check for pass-through blocks at placed positions
// and layer-direction validity (H segment on H layer, V on V).
ConnResult check_dnuts(const ConnTopology& ct, const DetailedNUTSResult& dnuts,
                       const Topology& topo, const Floorplan& fp,
                       const LayerStack& layers, int bundle_id, int num_bits);

} // namespace buda
