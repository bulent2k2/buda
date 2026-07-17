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
#include "topology.h"
#include <memory>
#include <string>
#include <utility>
#include <vector>
#include <climits>

namespace buda {

// ── Connection ────────────────────────────────────────────────────────────────
//
// A connection from one endpoint (or T-junction point) on a segment to either
// a busterm face or another segment.
//
// at_pos: coordinate ALONG this segment where the connection occurs.
//   H segment → at_pos is an x value  (the column where the other thing attaches)
//   V segment → at_pos is a y value   (the row where the other thing attaches)
//
struct SegConn {
    enum Kind { BUSTERM, SEG } kind;

    // BUSTERM: the segment endpoint lies on this block face.
    std::string block_name;   // block name in the Floorplan
    int         face_coord;   // the face coordinate  (x for x-face, y for y-face)

    // SEG: the endpoint (or T-junction) meets another segment.
    int seg_idx;              // index into ConnTopology::segs()
    int at_pos;               // position along THIS segment where the junction is
    bool is_endpoint = false; // true if at_pos is at either end of THIS segment
};

// ── ConnSeg ───────────────────────────────────────────────────────────────────
//
// A segment extended with explicit connectivity and computed slide range.
//
//  along_span [along_lo, along_hi]   extent in the segment's primary direction
//    H → x-range     V → y-range
//
//  perp_pos                          current (nominal) perpendicular position
//    H → y            V → x
//
//  perp_slide [perp_lo, perp_hi]     range perp_pos can take while all
//    connections remain valid.  Anchored by direct busterm constraints (pass 1)
//    and tightened by stub-propagated busterm constraints (pass 2).
//
struct ConnSeg {
    bool horiz     = false;
    int  layer_id  = -1;
    int  along_lo  = 0;
    int  along_hi  = 0;
    int  perp_pos  = 0;
    int  perp_lo   = INT_MIN / 2;
    int  perp_hi   = INT_MAX / 2;
    // net_pull > 0: more connected-stub anchors lie above perp_pos (slide up/right
    //              reduces total connected wirelength).
    // net_pull < 0: more anchors lie below → prefer sliding down/left.
    // net_pull == 0: balanced or no stub connections → no preferred direction.
    int  net_pull  = 0;
    // Saturation coordinate of the net_pull: the perpendicular position where
    // the pull's wirelength gain stops (the slope-crossing breakpoint over the
    // votes derive_net_pull counted — a busterm vote saturates at its
    // face_coord, a floating-spine vote at the far segment's near slide
    // bound).  Sliding PAST it re-lengthens what the pull was shortening —
    // the b44 tug-of-war: NUTS's legacy pull placement aims at the slide-
    // window EDGE, and on a wide interior window (a connector crossing the
    // covered block, w=2500) that overshoots the breakpoint by ~940 and
    // stretches the coupled trunk between the connectors.  INT_MIN sentinel =
    // no derivable breakpoint (net_pull == 0, or votes without a coordinate);
    // consumers fall back to the window bound.
    int  pull_break = INT_MIN;

    // ── Along-flex DOF (Stage C of the flexible-root re-arch) ─────────────────
    // The perp slide above moves the whole segment rigidly.  A trunk spine has a
    // SECOND degree of freedom the perp slide cannot express: its two along
    // ENDPOINTS move INDEPENDENTLY — the spine length is a range, not a fixed
    // generated coordinate.  An end is "flex" (contractible toward the interior)
    // when NO busterm tap anchors the segment at that endpoint; the end is then
    // defined purely by the extreme junction/coverage it must reach, which NUTS
    // resolves at placement time.  [along_cover_lo, along_cover_hi] is the
    // NOMINAL along-coverage floor (min/max over junction at_pos + busterm
    // face_coord); the spine may contract a flex end down to that floor but never
    // past it.  Pass-through-block along coverage is protected separately by
    // tighten_passthrough_ranges' perp constraints on the endpoint stub, so it is
    // deliberately NOT folded into the cover floor here.
    // along_pull is a signed WL-gradient hint: >0 → contracting the hi end shortens
    // wire; <0 → the lo end; magnitude = number of ends with slack.  Computed by
    // compute_along_pull(); consumed by NUTS do_span_adjustments (Stage B).
    bool along_flex_lo  = false;   // lo endpoint may contract up (toward along_hi)
    bool along_flex_hi  = false;   // hi endpoint may contract down (toward along_lo)
    int  along_cover_lo = 0;       // smallest along-coord the segment must reach
    int  along_cover_hi = 0;       // largest along-coord it must reach
    int  along_pull     = 0;

    std::vector<SegConn> conns;
};

// ── Manhattan nearest-point distance ─────────────────────────────────────────
//
// Minimum Manhattan distance between any point in rect a and any point in
// rect b.  Treats each rect as a closed axis-aligned rectangle.  For a
// segment pass its degenerate bounding box (x1==x2 for V, y1==y2 for H).
//
int manhattan_nearest(const Rect& a, const Rect& b);

// Return the bounding-box Rect of a ConnSeg (degenerate in the perp direction).
Rect seg_bbox(const ConnSeg& cs);

// ── MST types ─────────────────────────────────────────────────────────────────

struct MSTEdge {
    int         u, v;      // indices into the nodes vector (0-based)
    int         dist;      // Manhattan nearest-point distance
    std::string u_name;
    std::string v_name;
};

// Kruskal's MST over a set of named rectangular nodes.
// Distances = manhattan_nearest between bounding boxes.
// Returns exactly (nodes.size()-1) edges, sorted ascending by dist.
std::vector<MSTEdge> compute_mst(
    const std::vector<std::pair<std::string, Rect>>& nodes);

// ── ConnTopology ──────────────────────────────────────────────────────────────
//
// Augments a raw Topology with:
//   • explicit connectivity (busterm faces ↔ segments, segment ↔ segment)
//   • computed perp_slide ranges for every segment
//
// build() infers connections geometrically (shared endpoints / T-junctions /
// busterm face membership) then propagates slide constraints bottom-up from
// the busterms, which act as the fixed anchors of the routing tree.
//
class ConnTopology {
public:
    void build(const Topology& topo, const Floorplan& fp);
    const std::vector<ConnSeg>& segs() const;

    // For the trunk segment at segs()[trunk_idx], gather all blocks in fp that
    // are NOT yet directly connected (no BUSTERM conn on that segment), then
    // return the MST over {trunk} ∪ {unconnected blocks}.
    // Node 0 in the result is the trunk; nodes 1..n are the unconnected blocks
    // in the order returned by fp.get_all_blocks() minus already-connected ones.
    std::vector<MSTEdge> trunk_mst(int trunk_idx, const Floorplan& fp) const;

private:
    // The derivation lives in topology_analysis.h (six named passes; Phase A)
    // and is CACHED on the Topology itself, validated by content fingerprint
    // (Phase B) — build() is a thin wrapper over analyze().  This class is the
    // frozen consumer facade; it holds an immutable shared snapshot, so a ct
    // built before a topology mutation keeps serving the state it was built
    // from, exactly as the old owned-vector did.
    std::shared_ptr<const struct TopoAnalysis> a_;
};

} // namespace buda
