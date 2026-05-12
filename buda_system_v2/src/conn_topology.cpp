#include "conn_topology.h"
#include <algorithm>
#include <map>
#include <numeric>
#include <set>
#include <climits>

namespace interconnect {

// ── helpers ───────────────────────────────────────────────────────────────────

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

        for (int ep = 0; ep < 2; ++ep) {
            const Point& P = (ep == 0) ? si.start : si.end;
            bool found = false;

            // (a) busterm from pre-computed annotation -------------------------
            // topology.cpp's annotate_endpoints populates topo.seg_busterms so
            // we know exactly which block each terminal endpoint belongs to —
            // no geometric search needed, no shared-face ambiguity possible.
            // When the annotation entry exists but says "no busterm" (nullopt),
            // the endpoint is a bend/SEG junction — skip the geometric fallback
            // so a coincidentally-touching block face is not misidentified as a
            // busterm connection (e.g. L-shape bend at a block corner).
            bool annotation_present = false;
            {
                auto it = topo.seg_busterms.find(i);
                if (it != topo.seg_busterms.end()) {
                    annotation_present = true;
                    const auto& opt = (ep == 0) ? it->second.first
                                                : it->second.second;
                    if (opt.has_value()) {
                        SegConn c;
                        c.kind       = SegConn::BUSTERM;
                        c.block_name = opt->block_name;
                        c.face_coord = ci.horiz ? P.x : P.y;
                        c.seg_idx    = -1;
                        c.at_pos     = ci.horiz ? P.x : P.y;
                        add_conn(i, std::move(c));
                        found = true;
                    }
                }
            }

            // (b) geometric fallback — for unannotated topologies (e.g. those
            // built directly in tests without going through generate_candidates).
            // Skipped when the annotation entry is present (even if nullopt):
            // the annotation is authoritative and the bend/junction is a SEG conn.
            if (!found && !annotation_present) {
                for (const auto& [bname, rect] : blocks) {
                    bool on_xface = (P.x == rect.x1 || P.x == rect.x2)
                                    && in_range(P.y, rect.y1, rect.y2);
                    bool on_yface = (P.y == rect.y1 || P.y == rect.y2)
                                    && in_range(P.x, rect.x1, rect.x2);
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
            }
            if (found) continue;

            // (c) seg-to-seg connection ----------------------------------------
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

    // ── Pass 0 — pass-through block constraints ──────────────────────────────
    //
    // A segment "passes through" a block when its along-span overlaps the block's
    // extent in the along direction AND its perp_pos is inside the block's perp
    // extent.  The segment's perp coordinate must stay within the block's perp
    // range to maintain that connection — constrain perp_lo/hi accordingly.
    //
    // Only applied to blocks that have NO direct BUSTERM connection anywhere in
    // the topology.  A block with its own dedicated stub (BUSTERM on some other
    // segment) is already correctly connected; segments that merely cross over its
    // footprint are routing wires at a different layer and carry no connectivity
    // obligation — constraining them by that block's extent would be wrong.
    //
    // Detour trunks (U/C shapes) whose perp_pos is outside the union bounding box
    // of all busterm-connected blocks are routing in the detour region and must not
    // be constrained by blocks they happen to pass near in that region.
    //
    // No margin is applied here: the trunk can sit at any y (or x) within the
    // block as long as the along-span overlaps.
    {
        std::set<std::string> busterm_blocks;
        for (const auto& cs : segs_)
            for (const auto& conn : cs.conns)
                if (conn.kind == SegConn::BUSTERM)
                    busterm_blocks.insert(conn.block_name);

        // Union bbox of all busterm-connected blocks.
        int bt_x_lo = INT_MAX, bt_x_hi = INT_MIN;
        int bt_y_lo = INT_MAX, bt_y_hi = INT_MIN;
        for (const auto& bn : busterm_blocks) {
            const Rect& r = bmap.at(bn);
            bt_x_lo = std::min(bt_x_lo, r.x1); bt_x_hi = std::max(bt_x_hi, r.x2);
            bt_y_lo = std::min(bt_y_lo, r.y1); bt_y_hi = std::max(bt_y_hi, r.y2);
        }
        bool have_bt = (bt_x_hi >= bt_x_lo);

        for (auto& cs : segs_) {
            // Skip detour trunks: perp_pos outside the busterm union bbox.
            if (have_bt) {
                if ( cs.horiz && (cs.perp_pos < bt_y_lo || cs.perp_pos > bt_y_hi)) continue;
                if (!cs.horiz && (cs.perp_pos < bt_x_lo || cs.perp_pos > bt_x_hi)) continue;
            }
            for (const auto& [bname, rect] : bmap) {
                if (busterm_blocks.count(bname)) continue; // has its own stub
                if (cs.horiz) {
                    bool overlaps = (cs.along_lo < rect.x2 && cs.along_hi > rect.x1);
                    // Strictly interior: a segment at exactly y=y1 or y=y2 is touching
                    // the block face, not passing through the interior — no constraint.
                    bool inside   = (cs.perp_pos > rect.y1 && cs.perp_pos < rect.y2);
                    if (overlaps && inside) {
                        cs.perp_lo = std::max(cs.perp_lo, rect.y1);
                        cs.perp_hi = std::min(cs.perp_hi, rect.y2);
                    }
                } else {
                    bool overlaps = (cs.along_lo < rect.y2 && cs.along_hi > rect.y1);
                    bool inside   = (cs.perp_pos > rect.x1 && cs.perp_pos < rect.x2);
                    if (overlaps && inside) {
                        cs.perp_lo = std::max(cs.perp_lo, rect.x1);
                        cs.perp_hi = std::min(cs.perp_hi, rect.x2);
                    }
                }
            }
        }
    }

    // ── Pass 1 ──
    // Constrain to the block face extent, optionally shrunk by the block's corner
    // margin (set via Floorplan::set_block_corner_margin).
    //
    //   H segment on left/right face (face runs in Y) → margin = dy
    //     slide becomes [rect.y1+dy, rect.y2-dy]
    //   V segment on top/bottom face (face runs in X) → margin = dx
    //     slide becomes [rect.x1+dx, rect.x2-dx]
    //
    // Guard: if the margin would invert the interval (block smaller than 2×margin),
    // fall back to the full face extent for that axis.
    for (auto& cs : segs_) {
        for (const auto& conn : cs.conns) {
            if (conn.kind != SegConn::BUSTERM) continue;
            const Rect& rect = bmap.at(conn.block_name);
            BlockCornerMargin cm = fp.get_block_corner_margin(conn.block_name);
            if (cs.horiz) {
                int m  = cm.dy;
                int lo = rect.y1 + m;
                int hi = rect.y2 - m;
                // Guard 1: block too short for both margins.
                // Guard 2: nominal perp_pos is outside the margin range — this
                //   happens when the topology generator places the endpoint at the
                //   face boundary (e.g. y=rect.y1 for a below-to-above L).
                //   Applying the margin would exclude the nominal position and
                //   cause NUTS interval inversions; fall back to full face extent.
                if (lo > hi || cs.perp_pos < lo || cs.perp_pos > hi) {
                    lo = rect.y1; hi = rect.y2;
                }
                cs.perp_lo = std::max(cs.perp_lo, lo);
                cs.perp_hi = std::min(cs.perp_hi, hi);
            } else {
                int m  = cm.dx;
                int lo = rect.x1 + m;
                int hi = rect.x2 - m;
                if (lo > hi || cs.perp_pos < lo || cs.perp_pos > hi) {
                    lo = rect.x1; hi = rect.x2;
                }
                cs.perp_lo = std::max(cs.perp_lo, lo);
                cs.perp_hi = std::min(cs.perp_hi, hi);
            }
        }
    }

    // ── Pass 2 ──
    // Iterate to convergence (usually 1 pass suffices for a tree).
    // Only segments with ≥2 SEG connections are genuine spines; only they need
    // indirect busterm constraints propagated from their stubs.  Terminal stubs
    // (1 SEG connection + 1 BUSTERM) are fully constrained by Pass 1 already.
    // Applying Pass 2 from a stub's perspective would treat the spine's annotated
    // endpoint BUSTERMs as if they were its own stub's far-end anchor, producing
    // inverted intervals for multicast TRUNK_H/V topologies.
    bool changed = true;
    while (changed) {
        changed = false;
        for (auto& cs : segs_) {
            {
                int n_seg = 0;
                for (const auto& c : cs.conns)
                    if (c.kind == SegConn::SEG) ++n_seg;
                if (n_seg < 2) continue;  // not a spine; skip
            }
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

// ── manhattan_nearest ─────────────────────────────────────────────────────────
//
// Minimum Manhattan distance between the closest points of two rectangles.
// The gap in each axis is max(0, lo_of_right - hi_of_left) after sorting.
// Overlapping or touching rects → gap 0, distance = 0.

int manhattan_nearest(const Rect& a, const Rect& b) {
    int dx = std::max(0, std::max(a.x1 - b.x2, b.x1 - a.x2));
    int dy = std::max(0, std::max(a.y1 - b.y2, b.y1 - a.y2));
    return dx + dy;
}

// ── seg_bbox ──────────────────────────────────────────────────────────────────

Rect seg_bbox(const ConnSeg& cs) {
    if (cs.horiz)
        return Rect{ cs.along_lo, cs.perp_pos, cs.along_hi, cs.perp_pos };
    else
        return Rect{ cs.perp_pos, cs.along_lo, cs.perp_pos, cs.along_hi };
}

// ── compute_mst (Kruskal's) ───────────────────────────────────────────────────
//
// Build every pairwise edge, sort by Manhattan distance, then greedily add
// edges that join two previously disconnected components (union-find).

std::vector<MSTEdge> compute_mst(
    const std::vector<std::pair<std::string, Rect>>& nodes)
{
    int n = (int)nodes.size();
    if (n <= 1) return {};

    // Enumerate all O(N²) edges.
    struct RawEdge { int u, v, dist; };
    std::vector<RawEdge> edges;
    edges.reserve(n * (n - 1) / 2);
    for (int i = 0; i < n; i++)
        for (int j = i + 1; j < n; j++)
            edges.push_back({i, j, manhattan_nearest(nodes[i].second,
                                                     nodes[j].second)});

    std::sort(edges.begin(), edges.end(),
              [](const RawEdge& a, const RawEdge& b){ return a.dist < b.dist; });

    // Union-find: path-compressed.
    std::vector<int> parent(n);
    std::iota(parent.begin(), parent.end(), 0);
    std::function<int(int)> find = [&](int x) {
        return parent[x] == x ? x : parent[x] = find(parent[x]);
    };

    std::vector<MSTEdge> mst;
    mst.reserve(n - 1);
    for (const auto& e : edges) {
        int pu = find(e.u), pv = find(e.v);
        if (pu == pv) continue;
        parent[pu] = pv;
        mst.push_back({e.u, e.v, e.dist,
                       nodes[e.u].first, nodes[e.v].first});
        if ((int)mst.size() == n - 1) break;
    }
    return mst;
}

// ── ConnTopology::trunk_mst ───────────────────────────────────────────────────
//
// Collect all blocks in fp whose name does NOT appear in any BUSTERM conn of
// segs_[trunk_idx].  Build a node list: node 0 = trunk (degenerate bbox),
// nodes 1..k = unconnected blocks.  Return their MST.

std::vector<MSTEdge> ConnTopology::trunk_mst(int trunk_idx,
                                              const Floorplan& fp) const
{
    const ConnSeg& trunk = segs_[trunk_idx];

    // Collect already-connected block names.
    std::set<std::string> connected;
    for (const auto& c : trunk.conns)
        if (c.kind == SegConn::BUSTERM) connected.insert(c.block_name);

    // Build node list: trunk first, then unconnected blocks.
    std::vector<std::pair<std::string, Rect>> nodes;
    nodes.emplace_back("trunk", seg_bbox(trunk));
    for (const auto& [name, rect] : fp.get_all_blocks())
        if (!connected.count(name))
            nodes.emplace_back(name, rect);

    return compute_mst(nodes);
}

} // namespace interconnect
