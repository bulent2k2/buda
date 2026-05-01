#pragma once
#include "topology.h"
#include <string>
#include <vector>
#include <climits>

namespace interconnect {

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
    int  along_lo  = 0;
    int  along_hi  = 0;
    int  perp_pos  = 0;
    int  perp_lo   = INT_MIN / 2;
    int  perp_hi   = INT_MAX / 2;
    std::vector<SegConn> conns;
};

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
    const std::vector<ConnSeg>& segs() const { return segs_; }

private:
    std::vector<ConnSeg> segs_;
    void infer_connections(const Topology& topo, const Floorplan& fp);
    void compute_slide_ranges(const Floorplan& fp);
};

} // namespace interconnect
