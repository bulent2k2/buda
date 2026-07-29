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
    BIT_SHORT,    // two DIFFERENT bits of one bundle (= two different nets)
                  // share a layer + track with overlapping/touching spans — a
                  // physical short.  The abstract same-bundle track-sharing
                  // exemption assumed "per-bit they are the same nets"; the
                  // tapered fan-in (Topology::seg_bits) makes that
                  // conditional, so dnuts audits it (predicate lifted from
                  // tools/show_detailed_shorts.py)
    KEEPOUT_CROSS, // a placed wire lies ON a keepout that overlaps its span —
                  // nuts: the bus segment's physical extent [pos ± w/2]
                  // strictly overlaps the zone (the exhausted-window fallback
                  // commit, same semantics as NUTSResult::num_keepout_conflicts);
                  // dnuts: a bit's track centre is inside the zone (same
                  // predicate as DetailedNUTS's cull_keepout_crossers —
                  // defense-in-depth: the cull prevents this in production)
    ANTENNA,      // a segment is attached to the rest of the wire graph at
                  // FEWER THAN TWO points — it has at most one conn (BUSTERM
                  // tap or SEG junction), so everything beyond that single
                  // attachment is a dangling wire that terminates in nothing.
                  // Electrically inert metal (an "antenna"): it adds
                  // capacitance and can violate antenna rules, and it means
                  // the generator emitted a segment no route needs — the
                  // canonical case is an MST edge leg laid collinear on top
                  // of a trunk stub out of the same block face, whose
                  // demoted (nullopt) landing then has no inferable junction
                  // (issue #482).  Structural, so one detector serves the
                  // placed stages; generation's own knob for candidate-level
                  // dangles is `set_drop_dangling`
    DISCONNECTED, // the topology's wire graph splits into 2+ islands: SEG
                  // junctions + same-tapped-block continuity (a through-block
                  // joint is either a declared feedthru or flagged separately
                  // as FEEDTHRU_RELAY) leave some segments unreachable from
                  // the rest — the net cannot be electrically complete even
                  // though every block is tapped and every junction touches
                  // (e.g. a TopoEdit session that removed the only bridging
                  // stub and committed with comps=2)
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

// Declared-feedthru scoping for generation's DISCONNECTED gate
// (TopologyGenerator::filter_uncovered).  True iff the wire graph splits into
// 2+ islands under detect_disconnected's island model (SEG junctions +
// same-block tap continuity — the SAME union-find, shared implementation) and
// EVERY island touches one of topo.feedthru_blocks (a BUSTERM conn to it, or
// inclusive geometric overlap with its rects — a stub landing in the split
// gap): each island then reaches the declared block, whose internal routing
// is the declared bridge.  An island touching NO declared feedthru block is a
// genuine open the exemption must not cover (Codex P2 on #335).  False when
// no feedthru is declared or the graph is not split.
bool disconnected_islands_bridged(const ConnTopology& ct, const Topology& topo,
                                  const Floorplan& fp);

// NUTS-level check: same topology structure but positions from TrackSegments.
// Includes block-coverage check for pass-through blocks at placed positions,
// layer-direction validity (H segment on H layer, V on V), and the
// KEEPOUT_CROSS audit.
//
// zone_fp: the floorplan whose keepout zones the NUTS engine actually placed
// against — in the hier flow `fp` may be a bundle's cell-local generation
// floorplan (right coordinate/name space for the busterm-face checks) whose
// zone list is EMPTY, while placements are global; testing keepouts against
// it would silently pass a segment the engine itself counted as a conflict.
// nullptr = use fp (the flat flow, where they are the same object).
ConnResult check_nuts(const ConnTopology& ct, const NUTSResult& nuts,
                      const Topology& topo, const Floorplan& fp,
                      const LayerStack& layers, int bundle_id,
                      const Floorplan* zone_fp = nullptr);

// Detailed-NUTS-level: per-bit connectivity check using NetSegment positions.
// Includes block-coverage check for pass-through blocks at placed positions,
// layer-direction validity (H segment on H layer, V on V), and the
// KEEPOUT_CROSS audit (zone_fp as in check_nuts).
ConnResult check_dnuts(const ConnTopology& ct, const DetailedNUTSResult& dnuts,
                       const Topology& topo, const Floorplan& fp,
                       const LayerStack& layers, int bundle_id, int num_bits,
                       const Floorplan* zone_fp = nullptr);

} // namespace buda
