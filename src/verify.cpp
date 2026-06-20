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

#include "verify.h"
#include <algorithm>
#include <map>
#include <set>
#include <sstream>

namespace buda {

// ── helpers ───────────────────────────────────────────────────────────────────

// True when `pos` falls within the block face extent in the perpendicular axis.
//   H segment (horiz=true):  pos = y, checked against rect's [y1, y2].
//   V segment (horiz=false): pos = x, checked against rect's [x1, x2].
// Returns true if any individual rect of the block covers pos (multi-rect aware).
static bool perp_in_block_face(double pos, bool horiz,
                                const std::string& block_name,
                                const Floorplan& fp)
{
    auto rects = fp.get_block_rects(block_name);
    if (rects.empty()) {
        Rect bb = fp.get_block_bounds(block_name);
        return horiz ? (pos >= bb.y1 && pos <= bb.y2)
                     : (pos >= bb.x1 && pos <= bb.x2);
    }
    for (const Rect& r : rects) {
        bool ok = horiz ? (pos >= r.y1 && pos <= r.y2)
                        : (pos >= r.x1 && pos <= r.x2);
        if (ok) return true;
    }
    return false;
}

// True when segment `cs` at perpendicular position `perp` passes through rect `r`.
// "Passes through" means: perp is inside r's perp-face range, AND the along span
// overlaps r's along range.
static bool seg_spans_rect(const ConnSeg& cs, double perp, const Rect& r) {
    if (cs.horiz)
        return perp >= r.y1 && perp <= r.y2
            && cs.along_lo <= r.x2 && cs.along_hi >= r.x1;
    else
        return perp >= r.x1 && perp <= r.x2
            && cs.along_lo <= r.y2 && cs.along_hi >= r.y1;
}

// ── check_topo ────────────────────────────────────────────────────────────────

ConnResult check_topo(const ConnTopology& ct, const Topology& topo,
                      const Floorplan& fp, int bundle_id)
{
    ConnResult result;
    const auto& segs = ct.segs();
    int n = (int)segs.size();

    // 1. SEG connection continuity (each pair checked once, i < j).
    for (int i = 0; i < n; ++i) {
        const ConnSeg& cs = segs[i];
        for (const auto& conn : cs.conns) {
            if (conn.kind != SegConn::SEG) continue;
            int j = conn.seg_idx;
            if (j <= i) continue;
            const ConnSeg& other = segs[j];
            bool i_reach_j = (cs.perp_pos >= other.along_lo && cs.perp_pos <= other.along_hi);
            bool j_reach_i = (other.perp_pos >= cs.along_lo && other.perp_pos <= cs.along_hi);
            if (!i_reach_j || !j_reach_i) {
                ConnViolation v;
                v.kind = ViolationKind::SEG_OPEN;
                v.bundle_id = bundle_id; v.seg_idx = i; v.seg_idx2 = j;
                std::ostringstream msg;
                msg << "Seg " << i << " and Seg " << j << " disconnected (topo)";
                if (!i_reach_j)
                    msg << "; seg" << i << ".perp=" << cs.perp_pos
                        << " not in seg" << j << ".along=["
                        << other.along_lo << "," << other.along_hi << "]";
                if (!j_reach_i)
                    msg << "; seg" << j << ".perp=" << other.perp_pos
                        << " not in seg" << i << ".along=["
                        << cs.along_lo << "," << cs.along_hi << "]";
                v.message = msg.str();
                result.violations.push_back(std::move(v));
            }
        }
    }

    // 2. BUSTERM face validity: face_coord must be at a segment endpoint, and
    //    perp_pos must lie within the block's face extent.
    for (int i = 0; i < n; ++i) {
        const ConnSeg& cs = segs[i];
        for (const auto& conn : cs.conns) {
            if (conn.kind != SegConn::BUSTERM) continue;
            bool at_endpoint = (conn.face_coord == cs.along_lo ||
                                conn.face_coord == cs.along_hi);
            bool face_ok = perp_in_block_face((double)cs.perp_pos, cs.horiz,
                                              conn.block_name, fp);
            if (!at_endpoint || !face_ok) {
                ConnViolation v;
                v.kind = ViolationKind::BUSTERM_FACE;
                v.bundle_id = bundle_id; v.seg_idx = i;
                v.block_name = conn.block_name;
                std::ostringstream msg;
                msg << "Seg " << i << " BUSTERM to '" << conn.block_name << "'";
                if (!at_endpoint)
                    msg << "; face_coord=" << conn.face_coord
                        << " not at endpoint ["
                        << cs.along_lo << "," << cs.along_hi << "]";
                if (!face_ok)
                    msg << "; perp_pos=" << cs.perp_pos << " outside block face";
                v.message = msg.str();
                result.violations.push_back(std::move(v));
            }
        }
    }

    // 3. Block coverage: every block in connected_block_names must have either
    //    an explicit BUSTERM connection or a segment that passes through it.
    std::set<std::string> explicitly_connected;
    for (const auto& cs : segs)
        for (const auto& conn : cs.conns)
            if (conn.kind == SegConn::BUSTERM)
                explicitly_connected.insert(conn.block_name);

    for (const auto& bname : topo.connected_block_names) {
        if (explicitly_connected.count(bname)) continue;
        auto rects = fp.get_block_rects(bname);
        if (rects.empty()) rects.push_back(fp.get_block_bounds(bname));
        bool covered = false;
        for (const auto& cs : segs) {
            if (covered) break;
            for (const Rect& r : rects)
                if (seg_spans_rect(cs, (double)cs.perp_pos, r)) { covered = true; break; }
        }
        if (!covered) {
            ConnViolation v;
            v.kind = ViolationKind::BUSTERM_OPEN;
            v.bundle_id = bundle_id; v.seg_idx = -1;
            v.block_name = bname;
            v.message = "Block '" + bname
                + "' has no BUSTERM connection and no pass-through segment";
            result.violations.push_back(std::move(v));
        }
    }

    return result;
}

// ── check_nuts ────────────────────────────────────────────────────────────────

ConnResult check_nuts(const ConnTopology& ct, const NUTSResult& nuts,
                      const Topology& topo, const Floorplan& fp,
                      const LayerStack& layers, int bundle_id)
{
    ConnResult result;
    const auto& segs = ct.segs();
    int n = (int)segs.size();

    std::map<int, const TrackSegment*> ts_map;
    for (const auto& ts : nuts.segments)
        if (ts.bundle_id == bundle_id)
            ts_map[ts.seg_idx] = &ts;

    // 0. Layer-direction validity: a segment on a layer whose routing
    //    direction doesn't match its orientation is unbuildable, and its
    //    track_position came from the wrong axis — the coordinate-based
    //    checks below can then pass coincidentally.
    for (const auto& [si, tsp] : ts_map) {
        const TrackSegment& ts = *tsp;
        bool dir_ok = layers.has_layer(ts.layer) &&
            (layers.get_layer_dir(ts.layer) == LayerDir::HORIZONTAL) == ts.horiz;
        if (!dir_ok) {
            ConnViolation v;
            v.kind = ViolationKind::LAYER_DIR;
            v.bundle_id = bundle_id; v.seg_idx = si;
            std::ostringstream msg;
            msg << "Seg " << si << " runs " << (ts.horiz ? "H" : "V")
                << " but is on layer M" << ts.layer << " ("
                << (!layers.has_layer(ts.layer) ? "undefined"
                    : layers.get_layer_dir(ts.layer) == LayerDir::HORIZONTAL ? "H" : "V")
                << ") — unbuildable (nuts)";
            v.message = msg.str();
            result.violations.push_back(std::move(v));
        }
    }

    // 1. SEG connection continuity.
    for (int i = 0; i < n; ++i) {
        auto it_i = ts_map.find(i);
        if (it_i == ts_map.end() || !it_i->second->placed) continue;
        const TrackSegment& ts_i = *it_i->second;
        const ConnSeg& cs = segs[i];
        for (const auto& conn : cs.conns) {
            if (conn.kind != SegConn::SEG) continue;
            int j = conn.seg_idx;
            if (j <= i) continue;
            auto it_j = ts_map.find(j);
            if (it_j == ts_map.end() || !it_j->second->placed) continue;
            const TrackSegment& ts_j = *it_j->second;
            // span_lo/span_hi encode nominal endpoint identity, so they may be
            // stored with lo > hi after placement swaps the two ends.  Reach is
            // a geometric (order-independent) test, so compare against the
            // ordered [min,max] extent.
            const double i_lo = std::min(ts_i.span_lo, ts_i.span_hi);
            const double i_hi = std::max(ts_i.span_lo, ts_i.span_hi);
            const double j_lo = std::min(ts_j.span_lo, ts_j.span_hi);
            const double j_hi = std::max(ts_j.span_lo, ts_j.span_hi);
            bool i_reach_j = (i_lo <= ts_j.track_position &&
                               ts_j.track_position <= i_hi);
            bool j_reach_i = (j_lo <= ts_i.track_position &&
                               ts_i.track_position <= j_hi);
            if (!i_reach_j || !j_reach_i) {
                ConnViolation v;
                v.kind = ViolationKind::SEG_OPEN;
                v.bundle_id = bundle_id; v.seg_idx = i; v.seg_idx2 = j;
                std::ostringstream msg;
                msg << "Seg " << i << " and Seg " << j << " disconnected (nuts)";
                if (!i_reach_j)
                    msg << "; track_pos[" << j << "]=" << ts_j.track_position
                        << " not in span[" << i << "]=["
                        << i_lo << "," << i_hi << "]";
                if (!j_reach_i)
                    msg << "; track_pos[" << i << "]=" << ts_i.track_position
                        << " not in span[" << j << "]=["
                        << j_lo << "," << j_hi << "]";
                v.message = msg.str();
                result.violations.push_back(std::move(v));
            }
        }
    }

    // 2. BUSTERM face check at placed track_position.
    for (int i = 0; i < n; ++i) {
        auto it = ts_map.find(i);
        if (it == ts_map.end() || !it->second->placed) continue;
        const TrackSegment& ts = *it->second;
        const ConnSeg& cs = segs[i];
        for (const auto& conn : cs.conns) {
            if (conn.kind != SegConn::BUSTERM) continue;
            if (!perp_in_block_face(ts.track_position, cs.horiz, conn.block_name, fp)) {
                ConnViolation v;
                v.kind = ViolationKind::BUSTERM_FACE;
                v.bundle_id = bundle_id; v.seg_idx = i;
                v.block_name = conn.block_name;
                std::ostringstream msg;
                msg << "Seg " << i << " BUSTERM to '" << conn.block_name
                    << "'; track_pos=" << ts.track_position << " outside block face (nuts)";
                v.message = msg.str();
                result.violations.push_back(std::move(v));
            }
        }
    }

    // 3. Block coverage: pass-through blocks must still be spanned at placed positions.
    std::set<std::string> explicitly_connected;
    for (const auto& cs : segs)
        for (const auto& conn : cs.conns)
            if (conn.kind == SegConn::BUSTERM)
                explicitly_connected.insert(conn.block_name);

    for (const auto& bname : topo.connected_block_names) {
        if (explicitly_connected.count(bname)) continue;
        auto rects = fp.get_block_rects(bname);
        if (rects.empty()) rects.push_back(fp.get_block_bounds(bname));
        bool covered = false;
        for (const auto& ts : nuts.segments) {
            if (ts.bundle_id != bundle_id || !ts.placed || covered) continue;
            if (ts.seg_idx < 0 || ts.seg_idx >= n) continue;
            const ConnSeg& cs = segs[ts.seg_idx];
            for (const Rect& r : rects) {
                bool through = cs.horiz
                    ? (ts.track_position >= r.y1 && ts.track_position <= r.y2
                       && ts.span_lo <= r.x2 && ts.span_hi >= r.x1)
                    : (ts.track_position >= r.x1 && ts.track_position <= r.x2
                       && ts.span_lo <= r.y2 && ts.span_hi >= r.y1);
                if (through) { covered = true; break; }
            }
        }
        if (!covered) {
            ConnViolation v;
            v.kind = ViolationKind::BUSTERM_OPEN;
            v.bundle_id = bundle_id; v.seg_idx = -1;
            v.block_name = bname;
            v.message = "Block '" + bname
                + "' has no pass-through segment at placed track positions (nuts)";
            result.violations.push_back(std::move(v));
        }
    }

    return result;
}

// ── check_dnuts ───────────────────────────────────────────────────────────────

ConnResult check_dnuts(const ConnTopology& ct, const DetailedNUTSResult& dnuts,
                       const Topology& topo, const Floorplan& fp,
                       const LayerStack& layers, int bundle_id, int num_bits)
{
    ConnResult result;
    const auto& segs = ct.segs();
    int n = (int)segs.size();

    std::map<std::pair<int,int>, const NetSegment*> ns_map;
    for (const auto& ns : dnuts.net_segments)
        if (ns.bundle_id == bundle_id)
            ns_map[{ns.seg_idx, ns.bit_index}] = &ns;

    // 0. Layer-direction validity (see check_nuts).  All bits of a segment
    //    share its layer, so report once per (seg, layer).
    std::set<std::pair<int,int>> dir_reported;
    for (const auto& [key, nsp] : ns_map) {
        int si = key.first;
        if (si < 0 || si >= n) continue;
        const NetSegment& ns = *nsp;
        bool seg_horiz = segs[si].horiz;
        bool dir_ok = layers.has_layer(ns.layer) &&
            (layers.get_layer_dir(ns.layer) == LayerDir::HORIZONTAL) == seg_horiz;
        if (!dir_ok && dir_reported.insert({si, ns.layer}).second) {
            ConnViolation v;
            v.kind = ViolationKind::LAYER_DIR;
            v.bundle_id = bundle_id; v.seg_idx = si;
            std::ostringstream msg;
            msg << "Seg " << si << " runs " << (seg_horiz ? "H" : "V")
                << " but its bit wires are on layer M" << ns.layer << " ("
                << (!layers.has_layer(ns.layer) ? "undefined"
                    : layers.get_layer_dir(ns.layer) == LayerDir::HORIZONTAL ? "H" : "V")
                << ") — unbuildable (dnuts)";
            v.message = msg.str();
            result.violations.push_back(std::move(v));
        }
    }

    // 1. BUSTERM face check at placed per-bit track_position.
    for (int i = 0; i < n; ++i) {
        const ConnSeg& cs = segs[i];
        for (const auto& conn : cs.conns) {
            if (conn.kind != SegConn::BUSTERM) continue;
            for (int bit = 0; bit < num_bits; ++bit) {
                auto it = ns_map.find({i, bit});
                if (it == ns_map.end()) continue;
                if (!perp_in_block_face(it->second->track_position, cs.horiz,
                                        conn.block_name, fp)) {
                    ConnViolation v;
                    v.kind = ViolationKind::BUSTERM_FACE;
                    v.bundle_id = bundle_id; v.seg_idx = i; v.bit_index = bit;
                    v.block_name = conn.block_name;
                    std::ostringstream msg;
                    msg << "Seg " << i << " Bit " << bit << " BUSTERM to '"
                        << conn.block_name << "'; track_pos="
                        << it->second->track_position << " outside block face (dnuts)";
                    v.message = msg.str();
                    result.violations.push_back(std::move(v));
                }
            }
        }
    }

    // 2. SEG connection continuity per bit.
    for (int i = 0; i < n; ++i) {
        const ConnSeg& cs = segs[i];
        for (const auto& conn : cs.conns) {
            if (conn.kind != SegConn::SEG) continue;
            int j = conn.seg_idx;
            if (j <= i) continue;
            for (int bit = 0; bit < num_bits; ++bit) {
                auto it_i = ns_map.find({i, bit});
                auto it_j = ns_map.find({j, bit});
                if (it_i == ns_map.end() || it_j == ns_map.end()) continue;
                const NetSegment& ns_i = *it_i->second;
                const NetSegment& ns_j = *it_j->second;
                // span_lo/span_hi keep nominal endpoint identity (may be lo>hi);
                // reach is order-independent, so use the ordered [min,max] extent.
                const double i_lo = std::min(ns_i.span_lo, ns_i.span_hi);
                const double i_hi = std::max(ns_i.span_lo, ns_i.span_hi);
                const double j_lo = std::min(ns_j.span_lo, ns_j.span_hi);
                const double j_hi = std::max(ns_j.span_lo, ns_j.span_hi);
                bool i_reach_j = (i_lo <= ns_j.track_position &&
                                   ns_j.track_position <= i_hi);
                bool j_reach_i = (j_lo <= ns_i.track_position &&
                                   ns_i.track_position <= j_hi);
                if (!i_reach_j || !j_reach_i) {
                    ConnViolation v;
                    v.kind = ViolationKind::SEG_OPEN;
                    v.bundle_id = bundle_id; v.seg_idx = i; v.seg_idx2 = j;
                    v.bit_index = bit;
                    std::ostringstream msg;
                    msg << "Seg " << i << " and Seg " << j
                        << " Bit " << bit << " disconnected (dnuts)";
                    if (!i_reach_j)
                        msg << "; track_pos[" << j << "]=" << ns_j.track_position
                            << " not in span[" << i << "]=["
                            << i_lo << "," << i_hi << "]";
                    if (!j_reach_i)
                        msg << "; track_pos[" << i << "]=" << ns_i.track_position
                            << " not in span[" << j << "]=["
                            << j_lo << "," << j_hi << "]";
                    v.message = msg.str();
                    result.violations.push_back(std::move(v));
                }
            }
        }
    }

    // 3. Unplaced bits: every (seg_idx, bit) in [0,n) × [0,num_bits) must have
    //    a NetSegment; absence means DetailedNUTS could not find a valid track.
    for (int i = 0; i < n; ++i) {
        for (int bit = 0; bit < num_bits; ++bit) {
            if (ns_map.count({i, bit})) continue;
            ConnViolation v;
            v.kind = ViolationKind::UNPLACED;
            v.bundle_id = bundle_id; v.seg_idx = i; v.bit_index = bit;
            v.message = "Seg " + std::to_string(i) + " Bit " + std::to_string(bit)
                + " has no placed track (unplaced in DetailedNUTS)";
            result.violations.push_back(std::move(v));
        }
    }

    // 4. Pass-through block coverage per bit.
    std::set<std::string> explicitly_connected;
    for (const auto& cs : segs)
        for (const auto& conn : cs.conns)
            if (conn.kind == SegConn::BUSTERM)
                explicitly_connected.insert(conn.block_name);

    for (const auto& bname : topo.connected_block_names) {
        if (explicitly_connected.count(bname)) continue;
        auto rects = fp.get_block_rects(bname);
        if (rects.empty()) rects.push_back(fp.get_block_bounds(bname));
        for (int bit = 0; bit < num_bits; ++bit) {
            bool covered = false;
            for (int i = 0; i < n && !covered; ++i) {
                auto it = ns_map.find({i, bit});
                if (it == ns_map.end()) continue;
                const NetSegment& ns = *it->second;
                const ConnSeg& cs = segs[i];
                for (const Rect& r : rects) {
                    bool through = cs.horiz
                        ? (ns.track_position >= r.y1 && ns.track_position <= r.y2
                           && ns.span_lo <= r.x2 && ns.span_hi >= r.x1)
                        : (ns.track_position >= r.x1 && ns.track_position <= r.x2
                           && ns.span_lo <= r.y2 && ns.span_hi >= r.y1);
                    if (through) { covered = true; break; }
                }
            }
            if (!covered) {
                ConnViolation v;
                v.kind = ViolationKind::BUSTERM_OPEN;
                v.bundle_id = bundle_id; v.seg_idx = -1; v.bit_index = bit;
                v.block_name = bname;
                v.message = "Block '" + bname + "' Bit " + std::to_string(bit)
                    + " has no pass-through segment at placed track positions (dnuts)";
                result.violations.push_back(std::move(v));
            }
        }
    }

    return result;
}

} // namespace buda
