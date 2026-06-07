#include "conn_topology.h"
#include <algorithm>
#include <map>
#include <numeric>
#include <set>
#include <climits>

namespace buda {

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
        cs.layer_id = s.layer_hint;
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
    tighten_passthrough_ranges(topo, fp);
    compute_net_pull();
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
                    int at_i = ci.horiz ? P.x : P.y;
                    {
                        SegConn c;
                        c.kind    = SegConn::SEG;
                        c.seg_idx = j;
                        c.at_pos  = at_i;
                        c.is_endpoint = (at_i == ci.along_lo || at_i == ci.along_hi);
                        add_conn(i, std::move(c));
                    }
                    // Reciprocal T-junction from j to i: at_pos = position along j
                    int at_j = ci.horiz ? P.y : P.x;
                    {
                        SegConn c;
                        c.kind    = SegConn::SEG;
                        c.seg_idx = i;
                        c.at_pos  = at_j;
                        c.is_endpoint = (at_j == cj.along_lo || at_j == cj.along_hi);
                        add_conn(j, std::move(c));
                    }
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

            Rect face_rect = bmap.at(conn.block_name);
            {
                const auto rects = fp.get_block_rects(conn.block_name);
                if (!rects.empty()) {
                    if (cs.horiz) {
                        int u_y1 = INT_MAX, u_y2 = INT_MIN;
                        for (const Rect& r : rects) {
                            if ((r.x1 == conn.face_coord || r.x2 == conn.face_coord)
                                    && cs.perp_pos >= r.y1 && cs.perp_pos <= r.y2) {
                                u_y1 = std::min(u_y1, r.y1);
                                u_y2 = std::max(u_y2, r.y2);
                            }
                        }
                        if (u_y1 <= u_y2)
                            face_rect = Rect(face_rect.x1, u_y1, face_rect.x2, u_y2);
                    } else {
                        int u_x1 = INT_MAX, u_x2 = INT_MIN;
                        for (const Rect& r : rects) {
                            if ((r.y1 == conn.face_coord || r.y2 == conn.face_coord)
                                    && cs.perp_pos >= r.x1 && cs.perp_pos <= r.x2) {
                                u_x1 = std::min(u_x1, r.x1);
                                u_x2 = std::max(u_x2, r.x2);
                            }
                        }
                        if (u_x1 <= u_x2)
                            face_rect = Rect(u_x1, face_rect.y1, u_x2, face_rect.y2);
                    }
                }
            }

            BlockCornerMargin cm = fp.get_block_corner_margin(conn.block_name);
            if (cs.horiz) {
                int m  = cm.dy;
                int lo = face_rect.y1 + m;
                int hi = face_rect.y2 - m;
                if (lo > hi || cs.perp_pos < lo || cs.perp_pos > hi) {
                    lo = face_rect.y1; hi = face_rect.y2;
                }
                cs.perp_lo = std::max(cs.perp_lo, lo);
                cs.perp_hi = std::min(cs.perp_hi, hi);
            } else {
                int m  = cm.dx;
                int lo = face_rect.x1 + m;
                int hi = face_rect.x2 - m;
                if (lo > hi || cs.perp_pos < lo || cs.perp_pos > hi) {
                    lo = face_rect.x1; hi = face_rect.x2;
                }
                cs.perp_lo = std::max(cs.perp_lo, lo);
                cs.perp_hi = std::min(cs.perp_hi, hi);
            }
        }
    }

    // ── Pass 2 ──
    bool changed = true;
    while (changed) {
        changed = false;
        for (auto& cs : segs_) {
            for (const auto& conn : cs.conns) {
                if (conn.kind != SegConn::SEG) continue;
                const ConnSeg& stub = segs_[conn.seg_idx];

                for (const auto& sc : stub.conns) {
                    if (sc.kind != SegConn::BUSTERM) continue;

                    if (cs.horiz && !stub.horiz) {
                        // cs = H spine, stub = V stub anchored at rect's y-face
                        int f = sc.face_coord;
                        int new_lo = cs.perp_lo, new_hi = cs.perp_hi;

                        // Only enforce push-out if this is an actual relay stub (length > 0).
                        // Direct connections (perp_pos == f) are exempt from Pass 2 push-out.
                        if (std::abs(cs.perp_pos - f) > 0) {
                            int m = fp.get_min_stub_length(1 /*VERTICAL*/, stub.layer_id);
                            if (cs.perp_pos > f) new_lo = std::max(new_lo, f + m);
                            else                  new_hi = std::min(new_hi, f - m);
                        }

                        if (new_lo != cs.perp_lo || new_hi != cs.perp_hi) {
                            cs.perp_lo = new_lo; cs.perp_hi = new_hi;
                            changed = true;
                        }
                    } else if (!cs.horiz && stub.horiz) {
                        // cs = V spine, stub = H stub anchored at rect's x-face
                        int f = sc.face_coord;
                        int new_lo = cs.perp_lo, new_hi = cs.perp_hi;

                        if (std::abs(cs.perp_pos - f) > 0) {
                            int m = fp.get_min_stub_length(0 /*HORIZONTAL*/, stub.layer_id);
                            if (cs.perp_pos > f) new_lo = std::max(new_lo, f + m);
                            else                  new_hi = std::min(new_hi, f - m);
                        }

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

// ── ConnTopology::tighten_passthrough_ranges ──────────────────────────────────
//
// For every segment that spans a connected block without an explicit BUSTERM
// endpoint on that segment, tighten [perp_lo, perp_hi] so that NUTS cannot
// slide the segment out of that block's perpendicular face extent.
//
// "Spans" means: at the segment's nominal perp_pos, the along-range overlaps
// at least one rect of the block.  The constraint applied is the union perp
// span of all such matching rects (with corner margin), intersected into the
// already-computed [perp_lo, perp_hi].

void ConnTopology::tighten_passthrough_ranges(const Topology& topo,
                                               const Floorplan& fp)
{
    for (auto& cs : segs_) {
        // Collect blocks already anchored to this segment by a BUSTERM conn.
        std::set<std::string> anchored;
        for (const auto& conn : cs.conns)
            if (conn.kind == SegConn::BUSTERM)
                anchored.insert(conn.block_name);

        for (const auto& bname : topo.connected_block_names) {
            if (anchored.count(bname)) continue;

            auto rects = fp.get_block_rects(bname);
            if (rects.empty()) rects.push_back(fp.get_block_bounds(bname));

            // Union perp span and along span of every rect this segment actually spans.
            int span_lo = INT_MAX, span_hi = INT_MIN;
            int along_lo_B = INT_MAX, along_hi_B = INT_MIN;
            for (const Rect& r : rects) {
                bool through = cs.horiz
                    ? (cs.perp_pos >= r.y1 && cs.perp_pos <= r.y2
                       && cs.along_lo <= r.x2 && cs.along_hi >= r.x1)
                    : (cs.perp_pos >= r.x1 && cs.perp_pos <= r.x2
                       && cs.along_lo <= r.y2 && cs.along_hi >= r.y1);
                if (!through) continue;
                if (cs.horiz) {
                    span_lo = std::min(span_lo, r.y1); span_hi = std::max(span_hi, r.y2);
                    along_lo_B = std::min(along_lo_B, r.x1); along_hi_B = std::max(along_hi_B, r.x2);
                } else {
                    span_lo = std::min(span_lo, r.x1); span_hi = std::max(span_hi, r.x2);
                    along_lo_B = std::min(along_lo_B, r.y1); along_hi_B = std::max(along_hi_B, r.y2);
                }
            }
            if (span_lo > span_hi) continue; // segment doesn't span this block

            BlockCornerMargin cm = fp.get_block_corner_margin(bname);
            int margin = cs.horiz ? cm.dy : cm.dx;
            int lo = span_lo + margin;
            int hi = span_hi - margin;
            if (lo > hi) { lo = span_lo; hi = span_hi; } // margin too large: skip
            cs.perp_lo = std::max(cs.perp_lo, lo);
            cs.perp_hi = std::min(cs.perp_hi, hi);

            // When cs passes through B via a suppressed stub, the spine segment T
            // connected at cs's endpoint must stay on the far side of B so that
            // do_span_adjustments cannot retract cs.span_hi (or span_lo) below B's
            // near face, which would break the pass-through at placed positions.
            for (const auto& conn : cs.conns) {
                if (conn.kind != SegConn::SEG || !conn.is_endpoint) continue;
                ConnSeg& T = segs_[conn.seg_idx];
                if (conn.at_pos == cs.along_hi) {
                    // T is at hi end; after span adj, span_hi = T.track_position.
                    // Need T.track_position >= along_lo_B.
                    T.perp_lo = std::max(T.perp_lo, along_lo_B);
                } else if (conn.at_pos == cs.along_lo) {
                    // T is at lo end; after span adj, span_lo = T.track_position.
                    // Need T.track_position <= along_hi_B.
                    T.perp_hi = std::min(T.perp_hi, along_hi_B);
                }
            }
        }
    }
}

int manhattan_nearest(const Rect& a, const Rect& b) {
    int dx = std::max(0, std::max(a.x1 - b.x2, b.x1 - a.x2));
    int dy = std::max(0, std::max(a.y1 - b.y2, b.y1 - a.y2));
    return dx + dy;
}

Rect seg_bbox(const ConnSeg& cs) {
    if (cs.horiz)
        return Rect{ cs.along_lo, cs.perp_pos, cs.along_hi, cs.perp_pos };
    else
        return Rect{ cs.perp_pos, cs.along_lo, cs.perp_pos, cs.along_hi };
}

std::vector<MSTEdge> compute_mst(
    const std::vector<std::pair<std::string, Rect>>& nodes)
{
    int n = (int)nodes.size();
    if (n <= 1) return {};
    struct RawEdge { int u, v, dist; };
    std::vector<RawEdge> edges;
    edges.reserve(n * (n - 1) / 2);
    for (int i = 0; i < n; i++)
        for (int j = i + 1; j < n; j++)
            edges.push_back({i, j, manhattan_nearest(nodes[i].second,
                                                     nodes[j].second)});

    std::sort(edges.begin(), edges.end(),
              [](const RawEdge& a, const RawEdge& b){ return a.dist < b.dist; });

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

std::vector<MSTEdge> ConnTopology::trunk_mst(int trunk_idx,
                                              const Floorplan& fp) const
{
    const ConnSeg& trunk = segs_[trunk_idx];
    std::set<std::string> connected;
    for (const auto& c : trunk.conns)
        if (c.kind == SegConn::BUSTERM) connected.insert(c.block_name);

    std::vector<std::pair<std::string, Rect>> nodes;
    nodes.emplace_back("trunk", seg_bbox(trunk));
    for (const auto& [name, rect] : fp.get_all_blocks())
        if (!connected.count(name))
            nodes.emplace_back(name, rect);

    return compute_mst(nodes);
}

void ConnTopology::compute_net_pull() {
    for (auto& cs : segs_) {
        int pos = 0, neg = 0;
        for (const auto& conn : cs.conns) {
            if (conn.kind != SegConn::SEG) continue;
            const ConnSeg& nb = segs_[conn.seg_idx];
            for (const auto& sc : nb.conns) {
                if (sc.kind != SegConn::BUSTERM) continue;
                if      (sc.face_coord > cs.perp_pos) ++pos;
                else if (sc.face_coord < cs.perp_pos) ++neg;
            }
        }
        for (const auto& conn : cs.conns) {
            if (conn.kind != SegConn::SEG) continue;
            const ConnSeg& nb = segs_[conn.seg_idx];
            bool nb_has_bt = false;
            for (const auto& sc : nb.conns)
                if (sc.kind == SegConn::BUSTERM) { nb_has_bt = true; break; }
            if (nb_has_bt) continue;
            int lo = INT_MAX, hi = INT_MIN;
            for (const auto& sc : nb.conns) {
                if (sc.kind != SegConn::SEG) continue;
                int p = segs_[sc.seg_idx].perp_pos;
                lo = std::min(lo, p);
                hi = std::max(hi, p);
            }
            if (lo < hi) {
                if      (cs.perp_pos == lo) ++pos;
                else if (cs.perp_pos == hi) ++neg;
            }
        }
        cs.net_pull = pos - neg;
    }
}

} // namespace buda
