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
