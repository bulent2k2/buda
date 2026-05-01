#include "conn_topology.h"
#include <algorithm>
#include <map>
#include <climits>

namespace interconnect {

// ── helpers ───────────────────────────────────────────────────────────────────

static int margin10(int lo, int hi) {
    return std::max(1, (int)(0.1 * (hi - lo)));
}
static bool in_range(int v, int lo, int hi) { return v >= lo && v <= hi; }

// ── ConnTopology::build ───────────────────────────────────────────────────────

void ConnTopology::build(const Topology& topo, const Floorplan& fp) {
    segs_.clear();
    segs_.resize(topo.segments.size());

    for (int i = 0; i < (int)topo.segments.size(); i++) {
        const Segment& s = topo.segments[i];
        ConnSeg& cs = segs_[i];
        cs.horiz    = (s.start.y == s.end.y);
        if (cs.horiz) {
            cs.along_lo = std::min(s.start.x, s.end.x);
            cs.along_hi = std::max(s.start.x, s.end.x);
            cs.perp_pos = s.start.y;
        } else {
            cs.along_lo = std::min(s.start.y, s.end.y);
            cs.along_hi = std::max(s.start.y, s.end.y);
            cs.perp_pos = s.start.x;
        }
        cs.perp_lo = INT_MIN / 2;
        cs.perp_hi = INT_MAX / 2;
    }

    infer_connections(topo, fp);
    compute_slide_ranges(fp);
}

// ── ConnTopology::infer_connections ───────────────────────────────────────────
//
// For every endpoint of every segment we check:
//   (a) Is this point on a block face?  → BUSTERM connection
//   (b) Does it lie on (or share an endpoint with) another segment? → SEG connection
//
// When an endpoint of segment i is an interior point of segment j (T-junction),
// we add a SEG connection to both i (pointing at j) and j (pointing at i).

void ConnTopology::infer_connections(const Topology& topo, const Floorplan& fp) {
    auto blocks = fp.get_all_blocks();
    int n = (int)segs_.size();

    // Helper: add a SegConn to segs_[i] if it isn't already present.
    auto add_conn = [&](int i, SegConn c) {
        for (const auto& x : segs_[i].conns) {
            if (x.kind != c.kind) continue;
            if (c.kind == SegConn::BUSTERM && x.block_name == c.block_name) return;
            if (c.kind == SegConn::SEG     && x.seg_idx   == c.seg_idx)    return;
        }
        segs_[i].conns.push_back(std::move(c));
    };

    for (int i = 0; i < n; i++) {
        const Segment& si = topo.segments[i];
        ConnSeg&       ci = segs_[i];

        for (const Point& P : {si.start, si.end}) {
            bool found = false;

            // (a) busterm connection -------------------------------------------
            for (const auto& [bname, rect] : blocks) {
                bool on_xface = (P.x == rect.x1 || P.x == rect.x2)
                                && in_range(P.y, rect.y1, rect.y2);
                bool on_yface = (P.y == rect.y1 || P.y == rect.y2)
                                && in_range(P.x, rect.x1, rect.x2);

                // H seg connects to an x-face; V seg connects to a y-face.
                if ((ci.horiz && on_xface) || (!ci.horiz && on_yface)) {
                    SegConn c;
                    c.kind       = SegConn::BUSTERM;
                    c.block_name = bname;
                    c.face_coord = ci.horiz ? P.x : P.y;
                    c.seg_idx    = -1;
                    c.at_pos     = ci.horiz ? P.x : P.y;
                    add_conn(i, std::move(c));
                    found = true;
                    break;
                }
            }
            if (found) continue;

            // (b) seg-to-seg connection ----------------------------------------
            for (int j = 0; j < n; j++) {
                if (j == i) continue;
                const ConnSeg& cj = segs_[j];
                if (ci.horiz == cj.horiz) continue; // must be perpendicular

                // Does P lie on segment j?
                bool on_j = ci.horiz
                    ? (P.x == cj.perp_pos && in_range(P.y, cj.along_lo, cj.along_hi))
                    : (P.y == cj.perp_pos && in_range(P.x, cj.along_lo, cj.along_hi));

                if (on_j) {
                    // Connection from i to j: at_pos = position along i
                    {
                        SegConn c;
                        c.kind    = SegConn::SEG;
                        c.seg_idx = j;
                        c.at_pos  = ci.horiz ? P.x : P.y;
                        add_conn(i, std::move(c));
                    }
                    // Reciprocal T-junction from j to i: at_pos = position along j
                    // (only needed when P is interior to j, i.e. not j's own endpoint)
                    {
                        SegConn c;
                        c.kind    = SegConn::SEG;
                        c.seg_idx = i;
                        c.at_pos  = ci.horiz ? P.y : P.x;
                        add_conn(j, std::move(c));
                    }
                    break;
                }
            }
        }
    }
}

// ── ConnTopology::compute_slide_ranges ────────────────────────────────────────
//
// Two-pass constraint propagation from busterms → segments.
//
// Pass 1 — direct busterm constraints:
//   When a segment endpoint lies on a block face, the block's extent in the
//   perpendicular direction constrains the segment's perp_slide.
//
//     H segment on block's x-face  →  perp (y) ∈ [rect.y1+m, rect.y2-m]
//     V segment on block's y-face  →  perp (x) ∈ [rect.x1+m, rect.x2-m]
//
// Pass 2 — indirect via stub connections:
//   When a spine segment S connects to a stub T, and T's far end is anchored
//   to busterm B at face_coord f, then S must be on the outward side of f:
//
//     S is H spine, T is V stub anchored at B.y_face:
//       f == B.y1 (bottom face) → S.y ≤ B.y1   (spine below the block)
//       f == B.y2 (top    face) → S.y ≥ B.y2   (spine above the block)
//     S is V spine, T is H stub anchored at B.x_face:
//       f == B.x1 (left  face) → S.x ≤ B.x1
//       f == B.x2 (right face) → S.x ≥ B.x2

void ConnTopology::compute_slide_ranges(const Floorplan& fp) {
    std::map<std::string, Rect> bmap;
    for (auto& [name, rect] : fp.get_all_blocks()) bmap[name] = rect;

    // ── Pass 1 ──
    for (auto& cs : segs_) {
        for (const auto& conn : cs.conns) {
            if (conn.kind != SegConn::BUSTERM) continue;
            const Rect& rect = bmap.at(conn.block_name);
            if (cs.horiz) {
                int m = margin10(rect.y1, rect.y2);
                cs.perp_lo = std::max(cs.perp_lo, rect.y1 + m);
                cs.perp_hi = std::min(cs.perp_hi, rect.y2 - m);
            } else {
                int m = margin10(rect.x1, rect.x2);
                cs.perp_lo = std::max(cs.perp_lo, rect.x1 + m);
                cs.perp_hi = std::min(cs.perp_hi, rect.x2 - m);
            }
        }
    }

    // ── Pass 2 ──
    // Iterate to convergence (usually 1 pass suffices for a tree).
    bool changed = true;
    while (changed) {
        changed = false;
        for (auto& cs : segs_) {
            for (const auto& conn : cs.conns) {
                if (conn.kind != SegConn::SEG) continue;
                const ConnSeg& stub = segs_[conn.seg_idx];

                // stub must be perpendicular to cs (always true in a valid topology).
                for (const auto& sc : stub.conns) {
                    if (sc.kind != SegConn::BUSTERM) continue;
                    const Rect& rect = bmap.at(sc.block_name);

                    if (cs.horiz && !stub.horiz) {
                        // cs = H spine, stub = V stub anchored at rect's y-face
                        int f = sc.face_coord;  // the busterm y-face stub endpoint
                        int new_lo = cs.perp_lo, new_hi = cs.perp_hi;
                        if (f == rect.y1) new_hi = std::min(new_hi, rect.y1); // spine below B
                        else              new_lo = std::max(new_lo, rect.y2); // spine above B
                        if (new_lo != cs.perp_lo || new_hi != cs.perp_hi) {
                            cs.perp_lo = new_lo; cs.perp_hi = new_hi;
                            changed = true;
                        }
                    } else if (!cs.horiz && stub.horiz) {
                        // cs = V spine, stub = H stub anchored at rect's x-face
                        int f = sc.face_coord;
                        int new_lo = cs.perp_lo, new_hi = cs.perp_hi;
                        if (f == rect.x1) new_hi = std::min(new_hi, rect.x1);
                        else              new_lo = std::max(new_lo, rect.x2);
                        if (new_lo != cs.perp_lo || new_hi != cs.perp_hi) {
                            cs.perp_lo = new_lo; cs.perp_hi = new_hi;
                            changed = true;
                        }
                    }
                }
            }
        }
    }
}

} // namespace interconnect
