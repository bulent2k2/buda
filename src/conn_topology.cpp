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

#include "conn_topology.h"
#include <algorithm>
#include <cassert>
#include <functional>
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
    pin_relay_tap_connectors(fp);
    compute_net_pull();

    for (const auto& cs : segs_) {
        assert(cs.along_lo <= cs.along_hi);
        assert(cs.perp_lo  <= cs.perp_hi);
    }
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

            // Block may not exist in this floorplan (e.g. a cell-local busterm
            // name checked against a different floorplan); skip its constraint
            // rather than throwing — mirrors the graceful misses in the other
            // Floorplan accessors (get_block_rects/bounds/corner_margin).
            auto bm_it = bmap.find(conn.block_name);
            if (bm_it == bmap.end()) continue;
            Rect face_rect = bm_it->second;
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
                        // The spine must stay on the OUTWARD side of the stub's face f,
                        // ideally with the full min-stub clearance m.  When the full
                        // f±m clearance would empty the slide window against a pass-1
                        // busterm bound (e.g. the spine sits on a block edge), relax to
                        // the face f itself — a shorter stub but still the correct side —
                        // and drop the constraint only if even the face would invert
                        // (connectivity wins over the stub-length floor).
                        if (std::abs(cs.perp_pos - f) > 0) {
                            int m = fp.get_min_stub_length(1 /*VERTICAL*/, stub.layer_id);
                            if (cs.perp_pos > f) {
                                if      (f + m <= new_hi) new_lo = std::max(new_lo, f + m);
                                else if (f     <= new_hi) new_lo = std::max(new_lo, f);
                            } else {
                                if      (f - m >= new_lo) new_hi = std::min(new_hi, f - m);
                                else if (f     >= new_lo) new_hi = std::min(new_hi, f);
                            }
                        }

                        if (new_lo <= new_hi
                            && (new_lo != cs.perp_lo || new_hi != cs.perp_hi)) {
                            cs.perp_lo = new_lo; cs.perp_hi = new_hi;
                            changed = true;
                        }
                    } else if (!cs.horiz && stub.horiz) {
                        // cs = V spine, stub = H stub anchored at rect's x-face
                        int f = sc.face_coord;
                        int new_lo = cs.perp_lo, new_hi = cs.perp_hi;

                        // See the H-spine branch: keep the outward-side constraint,
                        // relaxing the full min-stub clearance to the face when it
                        // would otherwise empty the busterm-bounded slide window.
                        if (std::abs(cs.perp_pos - f) > 0) {
                            int m = fp.get_min_stub_length(0 /*HORIZONTAL*/, stub.layer_id);
                            if (cs.perp_pos > f) {
                                if      (f + m <= new_hi) new_lo = std::max(new_lo, f + m);
                                else if (f     <= new_hi) new_lo = std::max(new_lo, f);
                            } else {
                                if      (f - m >= new_lo) new_hi = std::min(new_hi, f - m);
                                else if (f     >= new_lo) new_hi = std::min(new_hi, f);
                            }
                        }

                        if (new_lo <= new_hi
                            && (new_lo != cs.perp_lo || new_hi != cs.perp_hi)) {
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
    // Blocks with a BUSTERM conn on ANY segment get their connectivity from
    // that stub.  A segment that merely grazes such a block at its nominal
    // perp_pos is not the connection to it and must stay free to slide away
    // (e.g. a U-detour's source stub crossing the dst block's perp extent).
    // Only blocks connected purely by pass-through (suppressed stubs) need
    // the tightening below.
    std::set<std::string> explicitly_connected;
    for (const auto& s : segs_)
        for (const auto& conn : s.conns)
            if (conn.kind == SegConn::BUSTERM)
                explicitly_connected.insert(conn.block_name);

    for (auto& cs : segs_) {
        for (const auto& bname : topo.connected_block_names) {
            if (explicitly_connected.count(bname)) continue;

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

// ── ConnTopology::pin_relay_tap_connectors ────────────────────────────────────
//
// A relay block keeps exactly ONE busterm tap (complete_relay_junctions' single-
// tap model); its other landings are demoted to SEG junctions and the landings
// are chained by a JOG / extension connector.  A BUSTERM conn clamps only the tap
// segment's PERPENDICULAR slide — but the tap's ALONG reach to face_coord is set
// by the connector attached at that SAME endpoint: after NUTS span adjustment the
// tap's span end follows that connector's placed perp position.  If the connector
// is free to slide arbitrarily, NUTS drags the whole staircase off the block and
// the tap no longer reaches it — a silent open at NUTS / dNUTS.
//
// The relay block is NOT a feedthru of this bundle, so the JOG / extension is
// routed OVER THE CELL (OTC).  Bound each such connector's perpendicular slide to
// the block's footprint extent: it may slide anywhere over the cell but never off
// it.  As long as the connector stays within [face_lo, face_hi], the tap's span
// end (which follows it) stays over the cell and still touches the block, so the
// along-reach is preserved — WITHOUT a degenerate zero-slide pin (which would
// leave NUTS no room to place a positive-width bus).  Intersect with the
// connector's existing window so we only tighten, never widen past a real
// constraint.
void ConnTopology::pin_relay_tap_connectors(const Floorplan& fp) {
    std::map<std::string, Rect> bmap;
    for (auto& [name, rect] : fp.get_all_blocks()) bmap[name] = rect;

    int n = (int)segs_.size();
    for (int i = 0; i < n; ++i) {
        ConnSeg& cs = segs_[i];
        for (const auto& bc : cs.conns) {
            if (bc.kind != SegConn::BUSTERM) continue;
            int f = bc.face_coord;
            // The busterm sits at one of cs's along endpoints (see check_topo).
            if (f != cs.along_lo && f != cs.along_hi) continue;
            auto bm_it = bmap.find(bc.block_name);
            if (bm_it == bmap.end()) continue;            // block not in this floorplan
            const Rect& bb = bm_it->second;
            // OTC window = the cell footprint in the connector's perp direction.
            for (const auto& sc : cs.conns) {
                if (sc.kind != SegConn::SEG || !sc.is_endpoint) continue;
                if (sc.at_pos != f) continue;
                ConnSeg& T = segs_[sc.seg_idx];
                if (T.horiz == cs.horiz) continue;        // need a bend, not collinear
                int lo = T.horiz ? bb.y1 : bb.x1;
                int hi = T.horiz ? bb.y2 : bb.x2;
                int nlo = std::max(T.perp_lo, lo);
                int nhi = std::min(T.perp_hi, hi);
                if (nlo > nhi) continue;                  // empty: don't violate invariant
                if (T.perp_pos < nlo || T.perp_pos > nhi) continue;  // nominal outside window
                T.perp_lo = nlo;
                T.perp_hi = nhi;
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
    for (int ci = 0; ci < (int)segs_.size(); ++ci) {
        ConnSeg& cs = segs_[ci];
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
            // nb is a floating spine (no busterm of its own): the pull comes
            // from the far segments reachable through it.  Compare against each
            // far segment's anchored slide interval [perp_lo, perp_hi], not its
            // nominal position — a far stub can slide anywhere within its block
            // face, so only a perp_pos OUTSIDE that interval produces a pull.
            // NUTS turns any nonzero pull into "slide to my own range extreme";
            // emitting a pull while already inside the far interval makes the
            // segment overshoot past its target (see flow/pull2.buda).
            //
            // This spine contributes a SINGLE unit of pull, not one per far
            // segment: sliding cs only shortens the spine if cs is an extreme
            // endpoint of it (all far segments on one side).  Shortening the
            // spine is worth one unit regardless of how many segments hang off
            // the other end — so a multi-fanout (multicast) spine must not
            // multiply the pull.  If far segments lie on both sides, cs is
            // interior and exerts no pull.
            int far_hi = 0, far_lo = 0;
            for (const auto& sc : nb.conns) {
                if (sc.kind != SegConn::SEG || sc.seg_idx == ci) continue;
                const ConnSeg& far = segs_[sc.seg_idx];
                if      (cs.perp_pos < far.perp_lo) ++far_hi;   // far is above/right of cs
                else if (cs.perp_pos > far.perp_hi) ++far_lo;   // far is below/left  of cs
            }
            if      (far_hi > 0 && far_lo == 0) ++pos;   // cs is the low endpoint → pull toward hi
            else if (far_lo > 0 && far_hi == 0) ++neg;   // cs is the high endpoint → pull toward lo
        }
        cs.net_pull = pos - neg;
    }
}

} // namespace buda
