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

#include "topology.h"
#include "conn_topology.h"
#include "verify.h"
#include <cmath>
#include <climits>
#include <deque>
#include <stdexcept>
#include <set>
#include <string>
#include <iostream>
#include <numeric>
#include <functional>

namespace buda {

Topology offset_topology(const Topology& t, int dx, int dy,
                         const std::string& name_prefix) {
    auto shift_rect = [dx, dy](const Rect& r) {
        return Rect{r.x1 + dx, r.y1 + dy, r.x2 + dx, r.y2 + dy};
    };
    auto shift_seg = [dx, dy](Segment& s) {
        s.start = Point{s.start.x + dx, s.start.y + dy};
        s.end   = Point{s.end.x   + dx, s.end.y   + dy};
        // A perp clamp is an absolute coordinate on the segment's perp axis
        // (y for H, x for V); shift it with the geometry, leaving sentinels alone.
        const int d = (s.start.y == s.end.y) ? dy : dx;   // H → y-clamp, V → x-clamp
        if (s.perp_clamp_lo != INT_MIN) s.perp_clamp_lo += d;
        if (s.perp_clamp_hi != INT_MAX) s.perp_clamp_hi += d;
    };
    auto shift_busterm = [&](Busterm& b) {
        b.bbox      = shift_rect(b.bbox);
        b.orig_bbox = shift_rect(b.orig_bbox);
        for (auto& r : b.rects) r = shift_rect(r);
    };
    // A cell-local block name (no hierarchy separator) is qualified with the
    // instance path so it resolves against the global (instance-coord)
    // floorplan; an already-absolute name is left alone.
    auto qualify = [&](const std::string& n) {
        return (!name_prefix.empty() && n.find('/') == std::string::npos)
                   ? name_prefix + "/" + n : n;
    };

    Topology out = t;   // copy type/wirelength/trunk_location/pass_through
    for (auto& s : out.segments) shift_seg(s);
    for (auto& [seg_idx, ep] : out.seg_busterms) {
        (void)seg_idx;
        if (ep.first)  { shift_busterm(*ep.first);  ep.first->block_name  = qualify(ep.first->block_name); }
        if (ep.second) { shift_busterm(*ep.second); ep.second->block_name = qualify(ep.second->block_name); }
    }
    if (!name_prefix.empty()) {
        for (auto& n : out.connected_block_names) n = qualify(n);
        for (auto& n : out.feedthru_blocks)       n = qualify(n);
    }
    std::map<std::string, Segment> bridges;
    for (auto& [name, seg] : out.bridge_segments) {
        Segment s = seg; shift_seg(s);
        bridges[qualify(name)] = s;
    }
    out.bridge_segments = std::move(bridges);
    return out;
}

OrientMap orient_map(const std::string& orient) {
    // Token = mirror-about-X-axis first, then CCW rotation (bdb/gds_io
    // convention).  Derived output-normalized forms over a w×h box:
    //   N  (x, y)       S  (w−x, h−y)   FN (x, h−y)   FS (w−x, y)
    //   W  (h−y, x)     E  (y, w−x)     FW (y, x)     FE (h−y, w−x)
    static const std::map<std::string, OrientMap> table = {
        {"N",  {false, false, false}}, {"S",  {false, true,  true }},
        {"FN", {false, false, true }}, {"FS", {false, true,  false}},
        {"W",  {true,  true,  false}}, {"E",  {true,  false, true }},
        {"FW", {true,  false, false}}, {"FE", {true,  true,  true }},
    };
    auto it = table.find(orient);
    return it != table.end() ? it->second : OrientMap{};
}

static Point apply_orient(const OrientMap& m, Point p, int w, int h) {
    int x = p.x, y = p.y;
    if (m.swap) { std::swap(x, y); std::swap(w, h); }
    if (m.rx) x = w - x;
    if (m.ry) y = h - y;
    return Point{x, y};
}

std::string orient_compose(const std::string& outer,
                           const std::string& inner) {
    // Compose the normalized maps (box dims cancel: reflections compose as
    // XORs, routed through the outer swap) and look the result back up.
    const OrientMap a = orient_map(inner), b = orient_map(outer);
    OrientMap c;
    c.swap = a.swap != b.swap;
    // inner's output axes feed outer's input; when outer swaps, inner's
    // rx/ry land on the other output axis.
    const bool irx = b.swap ? a.ry : a.rx;
    const bool iry = b.swap ? a.rx : a.ry;
    c.rx = irx != b.rx;
    c.ry = iry != b.ry;
    static const char* toks[8] = {"N","S","FN","FS","W","E","FW","FE"};
    for (const char* t : toks) {
        const OrientMap m = orient_map(t);
        if (m.swap == c.swap && m.rx == c.rx && m.ry == c.ry) return t;
    }
    return "N";   // unreachable: the 8 maps cover the group
}

std::string orient_inverse(const std::string& orient) {
    static const char* toks[8] = {"N","S","FN","FS","W","E","FW","FE"};
    for (const char* t : toks)
        if (orient_compose(orient, t) == "N") return t;
    return "N";
}

Topology transform_topology(const Topology& t, const std::string& orient,
                            int cell_w, int cell_h, int dx, int dy,
                            const std::string& name_prefix) {
    if (orient == "N" || orient.empty())
        return offset_topology(t, dx, dy, name_prefix);
    const OrientMap m = orient_map(orient);
    auto xf_point = [&](Point p) {
        Point q = apply_orient(m, p, cell_w, cell_h);
        return Point{q.x + dx, q.y + dy};
    };
    auto xf_rect = [&](const Rect& r) {
        const Point a = xf_point(Point{r.x1, r.y1});
        const Point b = xf_point(Point{r.x2, r.y2});
        return Rect{std::min(a.x, b.x), std::min(a.y, b.y),
                    std::max(a.x, b.x), std::max(a.y, b.y)};
    };
    auto xf_seg = [&](Segment& s) {
        // The clamp is an absolute interval on the segment's ORIGINAL perp
        // axis (y for H, x for V); map it through that axis's transform,
        // swapping the endpoints under reflection and keeping the
        // INT_MIN/INT_MAX unbounded sentinels symbolic.
        const bool was_h = (s.start.y == s.end.y);
        // The output box is cell_h×cell_w under a swap.  The original perp
        // axis (y for H, x for V) lands on output y exactly when the
        // transformed segment is still horizontal (was_h != m.swap keeps
        // H→H / V→V under no swap and flips under 90/270).
        const bool out_axis_is_y = (was_h != m.swap);
        const bool axis_reflected = out_axis_is_y ? m.ry : m.rx;
        const int out_w = m.swap ? cell_h : cell_w;
        const int out_h = m.swap ? cell_w : cell_h;
        const int dim = out_axis_is_y ? out_h : out_w;
        const int d = out_axis_is_y ? dy : dx;
        const int lo = s.perp_clamp_lo, hi = s.perp_clamp_hi;
        if (axis_reflected) {
            s.perp_clamp_lo = (hi == INT_MAX) ? INT_MIN : dim - hi + d;
            s.perp_clamp_hi = (lo == INT_MIN) ? INT_MAX : dim - lo + d;
        } else {
            s.perp_clamp_lo = (lo == INT_MIN) ? INT_MIN : lo + d;
            s.perp_clamp_hi = (hi == INT_MAX) ? INT_MAX : hi + d;
        }
        s.start = xf_point(s.start);
        s.end   = xf_point(s.end);
    };
    auto qualify = [&](const std::string& n) {
        return (!name_prefix.empty() && n.find('/') == std::string::npos)
                   ? name_prefix + "/" + n : n;
    };
    auto xf_busterm = [&](Busterm& b) {
        b.bbox      = xf_rect(b.bbox);
        b.orig_bbox = xf_rect(b.orig_bbox);
        for (auto& r : b.rects) r = xf_rect(r);
    };

    Topology out = t;   // copy type/wirelength/trunk_location/pass_through
    for (auto& s : out.segments) xf_seg(s);
    for (auto& [seg_idx, ep] : out.seg_busterms) {
        (void)seg_idx;
        if (ep.first)  { xf_busterm(*ep.first);  ep.first->block_name  = qualify(ep.first->block_name); }
        if (ep.second) { xf_busterm(*ep.second); ep.second->block_name = qualify(ep.second->block_name); }
    }
    if (!name_prefix.empty()) {
        for (auto& n : out.connected_block_names) n = qualify(n);
        for (auto& n : out.feedthru_blocks)       n = qualify(n);
    }
    std::map<std::string, Segment> bridges;
    for (auto& [name, seg] : out.bridge_segments) {
        Segment s = seg; xf_seg(s);
        bridges[qualify(name)] = s;
    }
    out.bridge_segments = std::move(bridges);
    return out;
}

// Forward decl: the geometric endpoint annotator (defined later in this TU).
static void annotate_endpoints(Topology& topo, const std::vector<Busterm>& blocks);

void annotate_topology(Topology& topo, const Floorplan& fp) {
    // Build a Busterm per floorplan block and run the same geometric annotation
    // the generator uses, so a hand-built or BDB-reloaded topology gets the
    // authoritative seg_busterms it needs before ConnTopology::build (which no
    // longer geometrically guesses — see single_source_topo_truth.md).  This is
    // an EXPLICIT one-time annotation, not a hidden per-endpoint fallback.
    std::vector<Busterm> bts;
    for (const auto& [name, orig] : fp.get_all_blocks()) {
        // Mirror the generator's busterm construction (generate_2pin/npin mk_bt):
        // carry the corner-margin-shrunk bbox, the full orig_bbox, the individual
        // rects (so annotate_endpoints checks each rect face, not the union — a
        // multi-rect block must not be tapped through the gap between its rects),
        // and the teg_mode.
        auto cm = fp.get_block_corner_margin(name);
        bts.push_back(Busterm{name, orig.shrink(cm.dx, cm.dy), orig,
                              fp.get_block_rects(name),
                              fp.get_block_teg_mode(name)});
    }
    annotate_endpoints(topo, bts);
    // seg_conns must be derived AFTER seg_busterms: a busterm-tapped endpoint is
    // a block tap, never a wire junction, and the derivation skips it.
    annotate_seg_conns(topo);
}

bool flip_mst_edge(Topology& topo, int edge_id, int h_layer, int v_layer,
                   const Floorplan& fp) {
    if (edge_id < 0) return false;           // -1 = "not an MST-edge leg" sentinel
    // Collect this edge's leg slots.  Only a clean 2-leg diagonal L is flippable;
    // a straight edge (1 leg), a shared-edge realization, or an unknown id has no
    // bend to move, so leave it untouched.
    std::vector<int> legs;
    for (int i = 0; i < (int)topo.segments.size(); ++i)
        if (topo.segments[i].edge_id == edge_id) legs.push_back(i);
    if ((int)legs.size() != 2) return false;

    Segment& a = topo.segments[legs[0]];
    Segment& b = topo.segments[legs[1]];
    auto eq = [](const Point& u, const Point& v) { return u.x == v.x && u.y == v.y; };
    // The two legs meet at a shared bend; the other two endpoints are p1, p2.
    Point bend, p1, p2;
    if      (eq(a.start, b.start)) { bend = a.start; p1 = a.end;   p2 = b.end;   }
    else if (eq(a.start, b.end))   { bend = a.start; p1 = a.end;   p2 = b.start; }
    else if (eq(a.end,   b.start)) { bend = a.end;   p1 = a.start; p2 = b.end;   }
    else if (eq(a.end,   b.end))   { bend = a.end;   p1 = a.start; p2 = b.start; }
    else return false;                       // legs don't share a bend: not a clean L

    // Rectangle (p1,p2) has two corners; the alternate bend is the opposite one.
    Point alt{ p1.x + p2.x - bend.x, p1.y + p2.y - bend.y };
    if (eq(alt, bend)) return false;         // collinear legs: no alternate

    // Reject a flip that would route onto an obstacle: the corner_diagonal_L
    // realization deliberately routed its two legs AROUND a shared block corner,
    // so the opposite bend IS that corner.  More generally, an alternate bend that
    // lands strictly inside a block, or exactly on a block corner, is not a valid
    // routing vertex -- leave such an edge untouched.
    for (const auto& [name, r] : fp.get_all_blocks()) {
        (void)name;
        bool inside = alt.x > r.x1 && alt.x < r.x2 && alt.y > r.y1 && alt.y < r.y2;
        bool corner = (alt.x == r.x1 || alt.x == r.x2) &&
                      (alt.y == r.y1 || alt.y == r.y2);
        if (inside || corner) return false;
    }

    // Rewrite in place: leg a = p1->alt, leg b = alt->p2, layer by direction.  The
    // two slots are preserved, so seg_busterms stays valid; the junction geometry
    // moves, so the caller re-derives seg_conns (annotate_seg_conns).
    auto set_leg = [&](Segment& s, const Point& u, const Point& w) {
        s.start = u; s.end = w;
        s.layer_hint = (u.y == w.y) ? h_layer : v_layer;   // horizontal vs vertical
    };
    set_leg(a, p1, alt);
    set_leg(b, alt, p2);
    return true;
}

void annotate_seg_conns(Topology& topo) {
    // (Re)derive the authoritative seg-to-seg junction annotation from the
    // topology's nominal segment geometry — the SAME zero-tolerance,
    // perpendicular-only predicate ConnTopology::infer_connections used to run
    // on every build, executed ONCE here so all stages read one truth.
    // Orientation/axis bookkeeping mirrors ConnTopology::build exactly
    // (horiz = start.y == end.y; a vertical segment's perp_pos is its x).
    topo.seg_conns.clear();
    const int n = (int)topo.segments.size();
    auto horiz_of = [](const Segment& s) { return s.start.y == s.end.y; };
    for (int i = 0; i < n; i++) {
        const Segment& si = topo.segments[i];
        const bool     hi = horiz_of(si);
        for (int ep = 0; ep < 2; ++ep) {
            // A busterm-tapped endpoint is a block tap — mirror
            // infer_connections' `if (found) continue;` short-circuit.
            auto bt = topo.seg_busterms.find(i);
            if (bt != topo.seg_busterms.end()) {
                const auto& opt = (ep == 0) ? bt->second.first
                                            : bt->second.second;
                if (opt.has_value()) continue;
            }
            const Point& P = (ep == 0) ? si.start : si.end;
            std::vector<int> others;
            for (int j = 0; j < n; j++) {
                if (j == i) continue;
                const Segment& sj = topo.segments[j];
                if (horiz_of(sj) == hi) continue;   // must be perpendicular
                const bool jh = horiz_of(sj);
                const int  perp = jh ? sj.start.y : sj.start.x;
                const int  alo  = jh ? std::min(sj.start.x, sj.end.x)
                                     : std::min(sj.start.y, sj.end.y);
                const int  ahi  = jh ? std::max(sj.start.x, sj.end.x)
                                     : std::max(sj.start.y, sj.end.y);
                const bool on_j = jh
                    ? (P.y == perp && P.x >= alo && P.x <= ahi)
                    : (P.x == perp && P.y >= alo && P.y <= ahi);
                if (on_j) others.push_back(j);      // j ascending → sorted
            }
            if (!others.empty())
                topo.seg_conns[{i, ep}] = std::move(others);
        }
    }
}

void Floorplan::add_block(const std::string& name, int x1, int y1, int x2, int y2) {
    int nx1 = std::min(x1, x2);
    int nx2 = std::max(x1, x2);
    int ny1 = std::min(y1, y2);
    int ny2 = std::max(y1, y2);
    blocks_[name] = Rect{nx1, ny1, nx2, ny2};
    ++rev_;
}
void Floorplan::add_block_rects(const std::string& name, const std::vector<Rect>& rects,
                                 TegMode mode) {
    if (rects.empty())
        throw std::invalid_argument(
            "add_block_rects('" + name + "'): rect list must not be empty");
    std::vector<Rect> norm_rects;
    norm_rects.reserve(rects.size());
    for (const auto& r : rects) {
        norm_rects.push_back({std::min(r.x1, r.x2), std::min(r.y1, r.y2),
                              std::max(r.x1, r.x2), std::max(r.y1, r.y2)});
    }

    Rect u = norm_rects[0];
    for (const auto& r : norm_rects) {
        u.x1 = std::min(u.x1, r.x1); u.y1 = std::min(u.y1, r.y1);
        u.x2 = std::max(u.x2, r.x2); u.y2 = std::max(u.y2, r.y2);
    }
    blocks_[name]      = u;
    block_rects_[name] = norm_rects;
    teg_modes_[name]   = mode;
    ++rev_;
}
void Floorplan::set_block_teg_mode(const std::string& name, TegMode mode) {
    teg_modes_[name] = mode;
    ++rev_;
}
TegMode Floorplan::get_block_teg_mode(const std::string& name) const {
    auto it = teg_modes_.find(name);
    return (it != teg_modes_.end()) ? it->second : TegMode::THRU;
}
std::vector<Rect> Floorplan::get_block_rects(const std::string& name) const {
    auto it = block_rects_.find(name);
    return (it != block_rects_.end()) ? it->second : std::vector<Rect>{};
}
void Floorplan::set_block_corner_margin(const std::string& name, int dx, int dy) {
    corner_margins_[name] = BlockCornerMargin{dx, dy};
    ++rev_;
}
void Floorplan::set_global_corner_margin(int dx, int dy) {
    global_corner_margin_ = BlockCornerMargin{dx, dy};
    ++rev_;
}
BlockCornerMargin Floorplan::get_block_corner_margin(const std::string& name) const {
    auto it = corner_margins_.find(name);
    return (it != corner_margins_.end()) ? it->second : global_corner_margin_;
}
Rect Floorplan::get_block_bounds(const std::string& name) const {
    if (blocks_.count(name)) return blocks_.at(name);
    return Rect{0,0,0,0};
}
bool Floorplan::has_block(const std::string& name) const {
    return blocks_.count(name) > 0;
}
void Floorplan::set_detour_channel(const std::string& dirs, int size) {
    for (char c : dirs) {
        switch (std::toupper(static_cast<unsigned char>(c))) {
            case 'N': detour_channel_.north = size; break;
            case 'S': detour_channel_.south = size; break;
            case 'E': detour_channel_.east  = size; break;
            case 'W': detour_channel_.west  = size; break;
            case 'Y': detour_channel_.north = detour_channel_.south = size; break;
            case 'X': detour_channel_.east  = detour_channel_.west  = size; break;
            case 'A': detour_channel_.north = detour_channel_.south =
                      detour_channel_.east  = detour_channel_.west  = size; break;
            default: break;
        }
    }
    ++rev_;
}
void Floorplan::add_keepout_zone(int x1, int y1, int x2, int y2, const std::vector<int>& layer_ids) {
    KeepoutZone koz;
    koz.bbox = Rect{x1, y1, x2, y2};
    for (int lid : layer_ids) koz.layer_ids.insert(lid);
    keepouts_.push_back(std::move(koz));
    ++rev_;
}
void Floorplan::set_container(const std::string& name, bool is_container) {
    if (is_container) containers_.insert(name);
    else              containers_.erase(name);
    ++rev_;
}
bool Floorplan::is_container(const std::string& name) const {
    return containers_.count(name) > 0;
}
std::vector<KeepoutZone> Floorplan::low_layer_keepouts(const std::vector<int>& low_layer_ids) const {
    std::vector<KeepoutZone> result = keepouts_;   // user-defined zones first
    if (low_layer_ids.empty()) return result;
    std::set<int> low_set(low_layer_ids.begin(), low_layer_ids.end());
    for (const auto& [name, r] : blocks_) {
        if (containers_.count(name)) continue;     // containers are transparent to LOW layers
        // Multi-rect leaf cells block each rect individually (the notch between
        // rects is routable), matching the per-rect Hanan grid.
        auto it = block_rects_.find(name);
        if (it != block_rects_.end()) {
            for (const Rect& ri : it->second) {
                KeepoutZone koz; koz.bbox = ri; koz.layer_ids = low_set;
                result.push_back(std::move(koz));
            }
        } else {
            KeepoutZone koz; koz.bbox = r; koz.layer_ids = low_set;
            result.push_back(std::move(koz));
        }
    }
    return result;
}
void Floorplan::get_hanan_grid(std::vector<int>& x_coords, std::vector<int>& y_coords) const {
    for (const auto& [name, r] : blocks_) {
        auto it = block_rects_.find(name);
        if (it != block_rects_.end()) {
            for (const Rect& ri : it->second) {
                x_coords.push_back(ri.x1); x_coords.push_back(ri.x2);
                y_coords.push_back(ri.y1); y_coords.push_back(ri.y2);
            }
        } else {
            x_coords.push_back(r.x1); x_coords.push_back(r.x2);
            y_coords.push_back(r.y1); y_coords.push_back(r.y2);
        }
    }
    for (const auto& koz : keepouts_) {
        x_coords.push_back(koz.bbox.x1); x_coords.push_back(koz.bbox.x2);
        y_coords.push_back(koz.bbox.y1); y_coords.push_back(koz.bbox.y2);
    }
    std::sort(x_coords.begin(), x_coords.end());
    x_coords.erase(std::unique(x_coords.begin(), x_coords.end()), x_coords.end());
    std::sort(y_coords.begin(), y_coords.end());
    y_coords.erase(std::unique(y_coords.begin(), y_coords.end()), y_coords.end());
}
Segment make_seg(int x1, int y1, int x2, int y2, int layer) {
    Segment s; s.start={x1,y1}; s.end={x2,y2}; s.layer_hint=layer; return s;
}

// ── Keepout helpers ─────────────────────────────────────────────────────────

// Returns true if a horizontal segment at y crossing [x1,x2] overlaps keepout.
static bool h_seg_overlaps_keepout(int x1, int x2, int y, const KeepoutZone& koz) {
    return y  >= koz.bbox.y1 && y  <= koz.bbox.y2 &&
           x1 <= koz.bbox.x2 && x2 >= koz.bbox.x1;
}

// Returns true if a vertical segment at x crossing [y1,y2] overlaps keepout.
static bool v_seg_overlaps_keepout(int x, int y1, int y2, const KeepoutZone& koz) {
    return x  >= koz.bbox.x1 && x  <= koz.bbox.x2 &&
           y1 <= koz.bbox.y2 && y2 >= koz.bbox.y1;
}

// Returns true if the segment is blocked on ALL layers in candidate_layers by
// the given keepout list.  A keepout with empty layer_ids blocks all layers.
static bool all_layers_blocked_by_keepouts(
    const Segment& seg,
    const std::vector<int>& candidate_layers,
    const std::vector<KeepoutZone>& keepouts)
{
    if (candidate_layers.empty() || keepouts.empty()) return false;
    bool is_h = (seg.start.y == seg.end.y);
    int x1 = std::min(seg.start.x, seg.end.x);
    int x2 = std::max(seg.start.x, seg.end.x);
    int y1 = std::min(seg.start.y, seg.end.y);
    int y2 = std::max(seg.start.y, seg.end.y);
    for (int layer : candidate_layers) {
        bool blocked = false;
        for (const auto& koz : keepouts) {
            bool koz_covers = koz.layer_ids.empty() || koz.layer_ids.count(layer);
            if (!koz_covers) continue;
            bool crosses = is_h ? h_seg_overlaps_keepout(x1, x2, y1, koz)
                                : v_seg_overlaps_keepout(x1, y1, y2, koz);
            if (crosses) { blocked = true; break; }
        }
        if (!blocked) return false;  // found a free layer — not all blocked
    }
    return true;
}

// Does the keepout set block `seg` at EVERY perpendicular position of its
// slide window [w_lo, w_hi] on EVERY candidate layer?  The nominal-position
// test above is the w_lo == w_hi special case; a real window lets NUTS slide
// a tap along its block face past a narrow keepout, so a candidate is only
// dead when the coverage EXHAUSTS the window (Codex #234).  Per layer, the
// free positions are the window minus the perp extents of the keepouts whose
// along-range overlaps the segment's span; one surviving free interval on
// any layer keeps the candidate.  (The along span is held at the segment's
// generated extent — a slid tap keeps its span up to junction adjustments,
// so this stays a conservative-but-honest generation-time test.)
static bool all_layers_blocked_across_slide(
    const Segment& seg, int w_lo, int w_hi,
    const std::vector<int>& candidate_layers,
    const std::vector<KeepoutZone>& keepouts)
{
    if (candidate_layers.empty() || keepouts.empty()) return false;
    const bool is_h = (seg.start.y == seg.end.y);
    const int a1 = is_h ? std::min(seg.start.x, seg.end.x)
                        : std::min(seg.start.y, seg.end.y);
    const int a2 = is_h ? std::max(seg.start.x, seg.end.x)
                        : std::max(seg.start.y, seg.end.y);
    if (w_lo > w_hi) std::swap(w_lo, w_hi);
    for (int layer : candidate_layers) {
        std::vector<std::pair<int, int>> free_iv{{w_lo, w_hi}};
        for (const auto& koz : keepouts) {
            if (!(koz.layer_ids.empty() || koz.layer_ids.count(layer))) continue;
            const int k_a1 = is_h ? koz.bbox.x1 : koz.bbox.y1;
            const int k_a2 = is_h ? koz.bbox.x2 : koz.bbox.y2;
            if (a1 > k_a2 || a2 < k_a1) continue;   // no along overlap
            const int k_p1 = is_h ? koz.bbox.y1 : koz.bbox.x1;
            const int k_p2 = is_h ? koz.bbox.y2 : koz.bbox.x2;
            std::vector<std::pair<int, int>> next;
            for (const auto& [lo, hi] : free_iv) {
                if (k_p2 < lo || k_p1 > hi) { next.emplace_back(lo, hi); continue; }
                if (k_p1 > lo) next.emplace_back(lo, k_p1 - 1);
                if (k_p2 < hi) next.emplace_back(k_p2 + 1, hi);
            }
            free_iv = std::move(next);
            if (free_iv.empty()) break;
        }
        if (!free_iv.empty()) return false;  // a free position exists here
    }
    return true;
}

static int clamp(int value, int lo, int hi) {
    return std::max(lo, std::min(hi, value));
}

// ---------------------------------------------------------------------------
// 2-pin shapes (L / Z / U)
// ---------------------------------------------------------------------------

void TopologyGenerator::add_l_shapes(const Busterm& s_bt, const Busterm& d_bt, std::vector<Topology>& results) {
    const Rect& src = s_bt.bbox; const Rect& dst = d_bt.bbox;
    const Rect& s_orig = s_bt.orig_bbox; const Rect& d_orig = d_bt.orig_bbox;
    Point s = src.center(); Point d = dst.center();

    int m_v = floorplan_.get_min_stub_length(1 /*VERTICAL*/, v_layer_);
    int m_h = floorplan_.get_min_stub_length(0 /*HORIZONTAL*/, h_layer_);

    // L_HV: horizontal first, then vertical to dst y-face.
    {
        int sx     = use_busterm_ ? src.face_x(d.x) : s.x;
        int bend_x = use_busterm_ ? dst.face_x(sx)  : d.x;

        if (use_busterm_ && sx == bend_x) {
            // H collapses — route via each of dst's exclusive x-zones.
            int hy = (d.y < src.y1) ? src.y1
                   : (d.y > src.y2) ? src.y2 : s.y;
            int dy = d_orig.face_y(hy);
            if (dst.x1 < src.x1) {
                int bx = (dst.x1 + src.x1) / 2;
                if (std::abs(bx - s_orig.face_x(bx)) >= m_h && std::abs(dy - hy) >= m_v) {
                    Topology hv; hv.type = "L_HV@x" + std::to_string(bx);
                    hv.segments.push_back(make_seg(s_orig.face_x(bx), hy, bx, hy, h_layer_));
                    if (hy != dy) hv.segments.push_back(make_seg(bx, hy, bx, dy, v_layer_));
                    if (hv.segments.size() == 2) results.push_back(hv);
                }
            }
            if (dst.x2 > src.x2) {
                int bx = (src.x2 + dst.x2) / 2;
                if (std::abs(bx - s_orig.face_x(bx)) >= m_h && std::abs(dy - hy) >= m_v) {
                    Topology hv; hv.type = "L_HV@x" + std::to_string(bx);
                    hv.segments.push_back(make_seg(s_orig.face_x(bx), hy, bx, hy, h_layer_));
                    if (hy != dy) hv.segments.push_back(make_seg(bx, hy, bx, dy, v_layer_));
                    if (hv.segments.size() == 2) results.push_back(hv);
                }
            }
        } else if (use_busterm_) {
            // Generate L_HV options for a given bend x.  Skipped silently when the
            // H stub (src orig-face → bx) is shorter than the minimum stub length.
            auto gen_lhv = [&](int bx) {
                if (std::abs(bx - s_orig.face_x(bx)) < m_h) return;
                // Option 1: H below dst, V up to dst.y1
                if (s_orig.y1 < d_orig.y1) {
                    int hy = std::min(src.y2, d_orig.y1 - m_v);
                    if (hy >= src.y1 && hy <= d_orig.y1 - m_v) {
                        Topology hv; hv.type = "L_HV@x" + std::to_string(bx) + "@y" + std::to_string(hy);
                        hv.segments.push_back(make_seg(s_orig.face_x(bx), hy, bx, hy, h_layer_));
                        hv.segments.push_back(make_seg(bx, hy, bx, d_orig.y1, v_layer_));
                        results.push_back(hv);
                    }
                }
                // Option 2: H above dst, V down to dst.y2
                if (s_orig.y2 > d_orig.y2) {
                    int hy = std::max(src.y1, d_orig.y2 + m_v);
                    if (hy >= d_orig.y2 + m_v && hy <= src.y2) {
                        Topology hv; hv.type = "L_HV@x" + std::to_string(bx) + "@y" + std::to_string(hy);
                        hv.segments.push_back(make_seg(s_orig.face_x(bx), hy, bx, hy, h_layer_));
                        hv.segments.push_back(make_seg(bx, hy, bx, d_orig.y2, v_layer_));
                        results.push_back(hv);
                    }
                }
            };
            gen_lhv(bend_x);
            // When the primary bend (dst nearest face) is too close to src's face
            // (e.g. adjacent blocks share an x-edge), also try the nearest x
            // that just clears the min-stub threshold from src's original face.
            // Using the midpoint like the "collapsed H" path would overshoot;
            // here we want the shortest valid stub.
            if (std::abs(bend_x - s_orig.face_x(bend_x)) < m_h) {
                if (dst.x1 < src.x1) {
                    int bx = std::min(s_orig.x1 - m_h, dst.x2);
                    // Bound by the corner-margin-shrunk dst box, not d_orig
                    // (audit C4-03): the original bbox let the vertical stub
                    // tap dst's top/bottom face inside the declared corner
                    // margin — the exact band corner_margin exists to keep
                    // taps out of.
                    if (bx >= dst.x1) gen_lhv(bx);
                }
                if (dst.x2 > src.x2) {
                    int bx = std::max(s_orig.x2 + m_h, dst.x1);
                    if (bx <= dst.x2) gen_lhv(bx);
                }
            }
        } else {
            int hy = s.y, dy = d.y;
            Topology hv; hv.type = "L_HV@x" + std::to_string(bend_x) + "@y" + std::to_string(hy);
            if (sx != bend_x) hv.segments.push_back(make_seg(sx, hy, bend_x, hy, h_layer_));
            if (hy != dy)      hv.segments.push_back(make_seg(bend_x, hy, bend_x, dy, v_layer_));
            if (hv.segments.size() == 2) results.push_back(hv);
        }
    }

    // L_VH: vertical first, then horizontal to dst x-face.
    {
        int sy = use_busterm_ ? src.face_y(d.y) : s.y;
        int vx = s.x;
        int dx = use_busterm_ ? dst.face_x(vx) : d.x;
        int bend_y = use_busterm_ ? dst.face_y(sy) : d.y;

        if (use_busterm_) {
            if      (d.x > src.x2) vx = src.x2;
            else if (d.x < src.x1) vx = src.x1;
            dx = dst.face_x(vx);
        }

        if (use_busterm_ && vx == dx) {
            // H collapses — route via each of src's exclusive x-zones.
            if (src.x2 > dst.x2) {
                int vx2 = (dst.x2 + src.x2) / 2;
                int dx2 = d_orig.face_x(vx2);
                int bend_y_fixed = d_orig.face_y(sy);
                // vx2 must lie within src's x-range; H stub is vx2→dx2
                if (vx2 >= src.x1 && vx2 <= src.x2 &&
                    std::abs(vx2 - dx2) >= m_h && std::abs(bend_y_fixed - sy) >= m_v) {
                    Topology vh; vh.type = "L_VH@x" + std::to_string(vx2);
                    if (s_orig.face_y(bend_y_fixed) != bend_y_fixed) vh.segments.push_back(make_seg(vx2, s_orig.face_y(bend_y_fixed), vx2, bend_y_fixed, v_layer_));
                    if (vx2 != dx2)   vh.segments.push_back(make_seg(vx2, bend_y_fixed, dx2, bend_y_fixed, h_layer_));
                    if (vh.segments.size() == 2) results.push_back(vh);
                }
            }
            if (src.x1 < dst.x1) {
                int vx2 = (src.x1 + dst.x1) / 2;
                int dx2 = d_orig.face_x(vx2);
                int bend_y_fixed = d_orig.face_y(sy);
                // vx2 must lie within src's x-range; H stub is vx2→dx2
                if (vx2 >= src.x1 && vx2 <= src.x2 &&
                    std::abs(vx2 - dx2) >= m_h && std::abs(bend_y_fixed - sy) >= m_v) {
                    Topology vh; vh.type = "L_VH@x" + std::to_string(vx2);
                    if (s_orig.face_y(bend_y_fixed) != bend_y_fixed) vh.segments.push_back(make_seg(vx2, s_orig.face_y(bend_y_fixed), vx2, bend_y_fixed, v_layer_));
                    if (vx2 != dx2)   vh.segments.push_back(make_seg(vx2, bend_y_fixed, dx2, bend_y_fixed, h_layer_));
                    if (vh.segments.size() == 2) results.push_back(vh);
                }
            }
        } else if (use_busterm_) {
            if (std::abs(dx - vx) >= m_h) {
                // Option A: H below src, V stub down from src bottom face
                if (d_orig.y1 < s_orig.y1) {
                    int bend_y_a = std::min(s_orig.y1 - m_v, dst.y2);
                    if (bend_y_a >= dst.y1 && bend_y_a <= s_orig.y1 - m_v) {
                        Topology vh; vh.type = "L_VH@y" + std::to_string(bend_y_a) + "@x" + std::to_string(vx);
                        vh.segments.push_back(make_seg(vx, s_orig.y1,   vx, bend_y_a, v_layer_));
                        vh.segments.push_back(make_seg(vx, bend_y_a, d_orig.face_x(vx), bend_y_a, h_layer_));
                        results.push_back(vh);
                    }
                }
                // Option B: H above src, V stub up from src top face
                if (d_orig.y2 > s_orig.y2) {
                    int bend_y_b = std::max(s_orig.y2 + m_v, dst.y1);
                    if (bend_y_b >= s_orig.y2 + m_v && bend_y_b <= dst.y2) {
                        Topology vh; vh.type = "L_VH@y" + std::to_string(bend_y_b) + "@x" + std::to_string(vx);
                        vh.segments.push_back(make_seg(vx, s_orig.y2,   vx, bend_y_b, v_layer_));
                        vh.segments.push_back(make_seg(vx, bend_y_b, d_orig.face_x(vx), bend_y_b, h_layer_));
                        results.push_back(vh);
                    }
                }
            }
        } else {
            Topology vh; vh.type = "L_VH@y" + std::to_string(bend_y) + "@x" + std::to_string(vx);
            if (sy != bend_y)
                vh.segments.push_back(make_seg(vx, sy, vx, bend_y, v_layer_));
            if (vx != dx)
                vh.segments.push_back(make_seg(vx, bend_y, dx, bend_y, h_layer_));
            if (vh.segments.size() == 2) results.push_back(vh);
        }
    }
}

// True when A and B form a genuine STAGGERED cross: they overlap in 2-D and each
// sticks out on one side per axis (neither nested).  Such a pair has exactly two
// free (empty) union corners on one diagonal — the basis for the overlap L's and
// corner-wrapping U's.  Shared by add_overlap_corner_ls / _us and the U-shape
// dispatch in generate_candidates.
static bool overlap_cross(const Rect& A, const Rect& B) {
    const bool overlap = std::max(A.x1, B.x1) < std::min(A.x2, B.x2)
                      && std::max(A.y1, B.y1) < std::min(A.y2, B.y2);
    const bool x_stagger = (A.x1 < B.x1 && A.x2 < B.x2) || (B.x1 < A.x1 && B.x2 < A.x2);
    const bool y_stagger = (A.y1 < B.y1 && A.y2 < B.y2) || (B.y1 < A.y1 && B.y2 < A.y2);
    return overlap && x_stagger && y_stagger;
}

// Two partially-overlapping blocks form a cross whose union bbox has exactly two
// FREE (empty) outer corners, on one diagonal.  Each free corner admits an L route
// that stays OUTSIDE the shared band: one leg taps a block's exclusive horizontal
// face, the perpendicular leg taps the OTHER block's exclusive vertical face,
// bending in the empty corner.  add_l_shapes (centre-projection) degenerates these
// away — the projected faces land inside the overlap, so the stub collapses and the
// min-stub gate drops it — leaving only I (through the overlap) and U (around the
// bbox).  We emit the two free-corner L's here so the planner also has a route that
// avoids a congested/blocked overlap band.  Requires a genuine STAGGERED cross on
// both axes (each block sticks out on one side per axis; neither nested); each leg
// is placed at the midpoint of its exclusive zone for real slide room, gated on the
// per-direction min-stub length.
void TopologyGenerator::add_overlap_corner_ls(const Busterm& s_bt, const Busterm& d_bt,
                                              std::vector<Topology>& results) {
    if (!use_busterm_) return;
    // Work on the margin-inset bbox (not orig_bbox): every tap must land within
    // the block's corner_margin, and the whole generator routes on the shrunken
    // boxes.  When no margin is set bbox == orig_bbox (unchanged); when a margin
    // separates the two boxes on the routing grid they no longer form a cross, so
    // no L_OVL is emitted and the ordinary L/U shapes handle them.
    const Rect& A = s_bt.bbox;
    const Rect& B = d_bt.bbox;
    if (!overlap_cross(A, B)) return;

    const int m_h = floorplan_.get_min_stub_length(0 /*H*/, h_layer_);
    const int m_v = floorplan_.get_min_stub_length(1 /*V*/, v_layer_);

    // Which block pokes out on each side.
    const Rect& Lx = (A.x1 < B.x1) ? A : B;   // pokes left
    const Rect& Rx = (A.x1 < B.x1) ? B : A;   // pokes right
    const Rect& Bo = (A.y1 < B.y1) ? A : B;   // pokes below (bottom)
    const Rect& To = (A.y1 < B.y1) ? B : A;   // pokes above (top)

    // Emit one corner L: leg1 from face point (afx,afy) to the bend (bx,by), leg2
    // from the bend to the other face point (bfx,bfy).  Each leg is purely H or V by
    // construction; gate on the min-stub for its orientation.
    auto emit = [&](const std::string& tag,
                    int afx, int afy, int bx, int by, int bfx, int bfy) {
        const int l1 = std::abs(bx - afx) + std::abs(by - afy);
        const int l2 = std::abs(bfx - bx) + std::abs(bfy - by);
        const int min1 = (afx == bx) ? m_v : m_h;   // vertical leg vs horizontal
        const int min2 = (bx == bfx) ? m_v : m_h;
        if (l1 < min1 || l2 < min2) return;
        Topology t; t.type = tag;
        t.estimated_wirelength = l1 + l2;   // real length so the planner's WL term ranks the two L's
        t.segments.push_back(make_seg(afx, afy, bx, by, (afx == bx) ? v_layer_ : h_layer_));
        t.segments.push_back(make_seg(bx, by, bfx, bfy, (bx == bfx) ? v_layer_ : h_layer_));
        results.push_back(std::move(t));
    };

    if (&Lx == &Bo) {
        // "/" diagonal (left block is also the bottom block) → free TOP-LEFT + BOTTOM-RIGHT.
        // TL: left/bottom block's TOP face (exclusive left) + top block's LEFT face (exclusive top).
        emit("L_OVL_TL", (Lx.x1 + Rx.x1) / 2, Lx.y2,
                         (Lx.x1 + Rx.x1) / 2, (Bo.y2 + To.y2) / 2,
                         To.x1, (Bo.y2 + To.y2) / 2);
        // BR: bottom block's RIGHT face (exclusive bottom) + right block's BOTTOM face (exclusive right).
        emit("L_OVL_BR", Bo.x2, (Bo.y1 + To.y1) / 2,
                         (Lx.x2 + Rx.x2) / 2, (Bo.y1 + To.y1) / 2,
                         (Lx.x2 + Rx.x2) / 2, Rx.y1);
    } else {
        // "\" diagonal (left block is the top block) → free BOTTOM-LEFT + TOP-RIGHT.
        // BL: left/top block's BOTTOM face (exclusive left) + bottom block's LEFT face (exclusive bottom).
        emit("L_OVL_BL", (Lx.x1 + Rx.x1) / 2, Lx.y1,
                         (Lx.x1 + Rx.x1) / 2, (Bo.y1 + To.y1) / 2,
                         Bo.x1, (Bo.y1 + To.y1) / 2);
        // TR: top block's RIGHT face (exclusive top) + right block's TOP face (exclusive right).
        emit("L_OVL_TR", To.x2, (Bo.y2 + To.y2) / 2,
                         (Lx.x2 + Rx.x2) / 2, (Bo.y2 + To.y2) / 2,
                         (Lx.x2 + Rx.x2) / 2, Rx.y2);
    }
}

// Corner-wrapping U's for a partially-overlapping (staggered-cross) block pair —
// the desirable replacement for the generic pass-through U's, which for overlapping
// endpoints cross a block and double back to tap it (never a useful route).  Each
// U is the dual of a free-corner L (add_overlap_corner_ls): it keeps the L's tap
// on one block's near face but, instead of tapping the other block on the
// overlap-adjacent face, detours one channel PAST it (reusing the same
// beyond-bbox detour columns add_u_shapes uses, so detour_channel is honoured)
// and wraps around its corner to tap its next face.  Emitted for BOTH blocks —
// the wraps around Q (the right block) offer Q's far faces, and their 180°
// mirrors (`*_M`) wrap around P (the left block) to offer P's far faces — so
// across L's + U's each block is offered all four faces and the planner always
// has a route that never enters the shared band, whichever block's near faces
// are congested.  Under double_detour each wrap continues one more channel to
// tap the FAR face (the UU_* variants).  The detour columns/rows come from
// x_grid/y_grid (channel midpoints, incl. the beyond-union bands); each leg is
// min-stub gated.
void TopologyGenerator::add_overlap_corner_us(const Busterm& s_bt, const Busterm& d_bt,
                                              const std::vector<int>& x_grid,
                                              const std::vector<int>& y_grid,
                                              std::vector<Topology>& results) {
    if (!use_busterm_) return;
    // Margin-inset bbox, as add_overlap_corner_ls (keeps taps within corner_margin
    // and consistent with the rest of the shrunk-box generator).
    const Rect& A = s_bt.bbox;
    const Rect& B = d_bt.bbox;
    if (!overlap_cross(A, B)) return;

    const int m_h = floorplan_.get_min_stub_length(0 /*H*/, h_layer_);
    const int m_v = floorplan_.get_min_stub_length(1 /*V*/, v_layer_);
    const int ux1 = std::min(A.x1, B.x1), ux2 = std::max(A.x2, B.x2);
    const int uy1 = std::min(A.y1, B.y1), uy2 = std::max(A.y2, B.y2);

    // Detour lines just beyond the union bbox (the channels add_u_shapes uses).
    const int NONE = INT_MIN;
    auto first_gt = [&](const std::vector<int>& g, int v) {
        for (int x : g) if (x > v) return x;
        return NONE;
    };
    auto last_lt = [&](const std::vector<int>& g, int v) {
        int r = NONE;
        for (int x : g) if (x < v) r = x;
        return r;
    };
    const int xd_r = first_gt(x_grid, ux2);   // detour column right of union
    const int xd_l = last_lt (x_grid, ux1);   // detour column left of union
    const int yd_t = first_gt(y_grid, uy2);   // detour row above union
    const int yd_b = last_lt (y_grid, uy1);   // detour row below union

    // Poly-line topology from consecutive points; leg orientation → layer; each leg
    // min-stub gated; kept only if it is a full 3+ segment path.  `clamps[i]` is the
    // per-segment perpendicular slide clamp (absolute perp-axis coords; sentinels =
    // unclamped): the face-tap arms are pinned to the tapped block's EXCLUSIVE band
    // and every detour arm is pinned OUTSIDE the union bbox.  Without these NUTS
    // slides an arm across a block to shorten wire and collapses the wrap into a
    // pass-through — the arm's bits then land inside the block → opens.
    using Clamp = std::pair<int,int>;
    auto emit = [&](const std::string& tag, std::vector<Clamp> clamps, std::vector<Point> p) {
        for (int v : {p.front().x, p.front().y}) if (v == NONE) return;
        for (const Point& pt : p) if (pt.x == NONE || pt.y == NONE) return;
        Topology t; t.type = tag;
        for (size_t i = 0; i + 1 < p.size(); ++i) {
            const bool horiz = (p[i].y == p[i + 1].y);
            const int len = std::abs(p[i + 1].x - p[i].x) + std::abs(p[i + 1].y - p[i].y);
            if (len < (horiz ? m_h : m_v)) return;
            t.segments.push_back(make_seg(p[i].x, p[i].y, p[i + 1].x, p[i + 1].y,
                                          horiz ? h_layer_ : v_layer_));
        }
        if (t.segments.size() >= 3) {
            for (size_t i = 0; i < t.segments.size() && i < clamps.size(); ++i) {
                t.segments[i].perp_clamp_lo = clamps[i].first;
                t.segments[i].perp_clamp_hi = clamps[i].second;
            }
            results.push_back(std::move(t));
        }
    };
    // Reusable clamp bands: RIGHT/LEFT/TOP/BOT keep a detour arm outside the
    // union; NOCL leaves an arm free (the trailing face-tap arms already
    // approach their block from outside).
    const Clamp RIGHT{ux2, INT_MAX}, LEFT{INT_MIN, ux1},
                TOP{uy2, INT_MAX},   BOT{INT_MIN, uy1}, NOCL{INT_MIN, INT_MAX};

    const Rect& Lx = (A.x1 < B.x1) ? A : B;   // pokes left
    const Rect& Rx = (A.x1 < B.x1) ? B : A;   // pokes right
    const Rect& Bo = (A.y1 < B.y1) ? A : B;   // pokes below
    const Rect& To = (A.y1 < B.y1) ? B : A;   // pokes above
    const bool dd = allow_double_detour_;

    if (&Lx == &Bo) {
        // "/" diagonal: P = lower-left block, Q = upper-right block.  L's tapped
        // P-right/Q-bottom and P-top/Q-left; the U's wrap to Q-right and Q-top.
        const Rect& P = Lx; const Rect& Q = Rx;
        const int p_rt_y = (Bo.y1 + To.y1) / 2;    // P's right face, below Q
        const int p_tp_x = (Lx.x1 + Rx.x1) / 2;    // P's top face, left of Q
        const int q_rt_y = (Q.y1 + P.y2) / 2;      // Q's right face, near bottom-right corner
        const int q_tp_x = (Q.x1 + P.x2) / 2;      // Q's top face, near top-left corner
        const int q_tp_xR = (P.x2 + Q.x2) / 2;     // Q's top face, near top-right (UU far tap)
        const int q_rt_yT = (P.y2 + Q.y2) / 2;     // Q's right face, near top-right (UU far tap)
        // Exclusive bands for the P-tap arm: H arm stays BELOW Q (y∈[P.y1,Q.y1]);
        // V arm stays LEFT of Q (x∈[P.x1,Q.x1]).  Detour arms clamped outside the
        // union (RIGHT/TOP); the trailing Q-tap arm approaches Q from outside → NOCL.
        const Clamp HTAP{P.y1, Q.y1}, VTAP{P.x1, Q.x1};
        emit("U_OVL_HVH", {HTAP, RIGHT, NOCL}, {{P.x2, p_rt_y}, {xd_r, p_rt_y}, {xd_r, q_rt_y}, {Q.x2, q_rt_y}});
        emit("U_OVL_VHV", {VTAP, TOP,   NOCL}, {{p_tp_x, P.y2}, {p_tp_x, yd_t}, {q_tp_x, yd_t}, {q_tp_x, Q.y2}});
        if (dd) {
            emit("UU_OVL_HVHV", {HTAP, RIGHT, TOP,   NOCL}, {{P.x2, p_rt_y}, {xd_r, p_rt_y}, {xd_r, yd_t}, {q_tp_xR, yd_t}, {q_tp_xR, Q.y2}});
            emit("UU_OVL_VHVH", {VTAP, TOP,   RIGHT, NOCL}, {{p_tp_x, P.y2}, {p_tp_x, yd_t}, {xd_r, yd_t}, {xd_r, q_rt_yT}, {Q.x2, q_rt_yT}});
        }
        // 180° mirrors (`*_M`): keep the L's Q-tap leg and wrap around P instead,
        // offering P's OTHER two faces (left/bottom) via the LEFT/BOT detours —
        // without these the left block only ever gets its two near faces.
        const int q_lf_y  = (P.y2 + Q.y2) / 2;     // Q's left face, above P
        const int q_bt_x  = (P.x2 + Q.x2) / 2;     // Q's bottom face, right of P
        const int p_lf_y  = (Q.y1 + P.y2) / 2;     // P's left face, near top-left corner
        const int p_bt_x  = (Q.x1 + P.x2) / 2;     // P's bottom face, near bottom-right corner
        const int p_bt_xL = (P.x1 + Q.x1) / 2;     // P's bottom face, near bottom-left (UU far tap)
        const int p_lf_yB = (P.y1 + Q.y1) / 2;     // P's left face, near bottom-left (UU far tap)
        // Exclusive bands for the Q-tap arm: H arm stays ABOVE P (y∈[P.y2,Q.y2]);
        // V arm stays RIGHT of P (x∈[P.x2,Q.x2]).
        const Clamp QHTAP{P.y2, Q.y2}, QVTAP{P.x2, Q.x2};
        emit("U_OVL_HVH_M", {QHTAP, LEFT, NOCL}, {{Q.x1, q_lf_y}, {xd_l, q_lf_y}, {xd_l, p_lf_y}, {P.x1, p_lf_y}});
        emit("U_OVL_VHV_M", {QVTAP, BOT,  NOCL}, {{q_bt_x, Q.y1}, {q_bt_x, yd_b}, {p_bt_x, yd_b}, {p_bt_x, P.y1}});
        if (dd) {
            emit("UU_OVL_HVHV_M", {QHTAP, LEFT, BOT,  NOCL}, {{Q.x1, q_lf_y}, {xd_l, q_lf_y}, {xd_l, yd_b}, {p_bt_xL, yd_b}, {p_bt_xL, P.y1}});
            emit("UU_OVL_VHVH_M", {QVTAP, BOT,  LEFT, NOCL}, {{q_bt_x, Q.y1}, {q_bt_x, yd_b}, {xd_l, yd_b}, {xd_l, p_lf_yB}, {P.x1, p_lf_yB}});
        }
    } else {
        // "\" diagonal: P = upper-left block, Q = lower-right block.  L's tapped
        // P-bottom/Q-left and P-right/Q-top; the U's wrap to Q-bottom and Q-right.
        const Rect& P = Lx; const Rect& Q = Rx;
        const int p_bt_x = (Lx.x1 + Rx.x1) / 2;    // P's bottom face, left of Q
        const int p_rt_y = (Bo.y2 + To.y2) / 2;    // P's right face, above Q
        const int q_bt_x = (Q.x1 + P.x2) / 2;      // Q's bottom face, near bottom-left corner
        const int q_rt_y = (Q.y2 + P.y1) / 2;      // Q's right face, near top-right corner
        const int q_bt_xR = (P.x2 + Q.x2) / 2;     // Q's bottom face, near bottom-right (UU far tap)
        const int q_rt_yB = (Q.y1 + P.y1) / 2;     // Q's right face, near bottom-right (UU far tap)
        // Exclusive bands for the P-tap arm: H arm stays ABOVE Q (y∈[Q.y2,P.y2]);
        // V arm stays LEFT of Q (x∈[P.x1,Q.x1]).  Detour arms clamped outside the
        // union (RIGHT/BOT); the trailing Q-tap arm approaches Q from outside → NOCL.
        const Clamp HTAP{Q.y2, P.y2}, VTAP{P.x1, Q.x1};
        emit("U_OVL_VHV", {VTAP, BOT,   NOCL}, {{p_bt_x, P.y1}, {p_bt_x, yd_b}, {q_bt_x, yd_b}, {q_bt_x, Q.y1}});
        emit("U_OVL_HVH", {HTAP, RIGHT, NOCL}, {{P.x2, p_rt_y}, {xd_r, p_rt_y}, {xd_r, q_rt_y}, {Q.x2, q_rt_y}});
        if (dd) {
            emit("UU_OVL_VHVH", {VTAP, BOT,   RIGHT, NOCL}, {{p_bt_x, P.y1}, {p_bt_x, yd_b}, {xd_r, yd_b}, {xd_r, q_rt_yB}, {Q.x2, q_rt_yB}});
            emit("UU_OVL_HVHV", {HTAP, RIGHT, BOT,   NOCL}, {{P.x2, p_rt_y}, {xd_r, p_rt_y}, {xd_r, yd_b}, {q_bt_xR, yd_b}, {q_bt_xR, Q.y1}});
        }
        // 180° mirrors (`*_M`): wrap around P (the upper-left block) instead,
        // offering P's OTHER two faces (left/top) via the LEFT/TOP detours.
        const int q_tp_x  = (P.x2 + Q.x2) / 2;     // Q's top face, right of P
        const int q_lf_y  = (Q.y1 + P.y1) / 2;     // Q's left face, below P
        const int p_tp_x  = (Q.x1 + P.x2) / 2;     // P's top face, near top-right corner
        const int p_lf_y  = (Q.y2 + P.y1) / 2;     // P's left face, near bottom-left corner
        const int p_tp_xL = (P.x1 + Q.x1) / 2;     // P's top face, near top-left (UU far tap)
        const int p_lf_yT = (Q.y2 + P.y2) / 2;     // P's left face, near top-left (UU far tap)
        // Exclusive bands for the Q-tap arm: V arm stays RIGHT of P (x∈[P.x2,Q.x2]);
        // H arm stays BELOW P (y∈[Q.y1,P.y1]).
        const Clamp QVTAP{P.x2, Q.x2}, QHTAP{Q.y1, P.y1};
        emit("U_OVL_VHV_M", {QVTAP, TOP,  NOCL}, {{q_tp_x, Q.y2}, {q_tp_x, yd_t}, {p_tp_x, yd_t}, {p_tp_x, P.y2}});
        emit("U_OVL_HVH_M", {QHTAP, LEFT, NOCL}, {{Q.x1, q_lf_y}, {xd_l, q_lf_y}, {xd_l, p_lf_y}, {P.x1, p_lf_y}});
        if (dd) {
            emit("UU_OVL_VHVH_M", {QVTAP, TOP,  LEFT, NOCL}, {{q_tp_x, Q.y2}, {q_tp_x, yd_t}, {xd_l, yd_t}, {xd_l, p_lf_yT}, {P.x1, p_lf_yT}});
            emit("UU_OVL_HVHV_M", {QHTAP, LEFT, TOP,  NOCL}, {{Q.x1, q_lf_y}, {xd_l, q_lf_y}, {xd_l, yd_t}, {p_tp_xL, yd_t}, {p_tp_xL, P.y2}});
        }
    }
}

// Helper: H-stub y-level for HVH topologies.
static int stub_y(bool use_busterm, bool has_stub,
                  const Rect& blk, int toward_y, int fallback_y) {
    if (!use_busterm) return fallback_y;
    return has_stub ? clamp(toward_y, blk.y1, blk.y2) : blk.face_y(toward_y);
}

// Symmetric helper for VHV topologies.
static int stub_x(bool use_busterm, bool has_stub,
                  const Rect& blk, int toward_x, int fallback_x) {
    if (!use_busterm) return fallback_x;
    return has_stub ? clamp(toward_x, blk.x1, blk.x2) : blk.face_x(toward_x);
}

void TopologyGenerator::add_z_shapes(const Busterm& s_bt, const Busterm& d_bt,
                                      const std::vector<int>& x_grid,
                                      const std::vector<int>& y_grid,
                                      std::vector<Topology>& results) {
    const Rect& src = s_bt.bbox; const Rect& dst = d_bt.bbox;
    const Rect& s_orig = s_bt.orig_bbox; const Rect& d_orig = d_bt.orig_bbox;
    Point s = src.center(); Point d = dst.center();

    int m_v = floorplan_.get_min_stub_length(1 /*VERTICAL*/, v_layer_);
    int m_h = floorplan_.get_min_stub_length(0 /*HORIZONTAL*/, h_layer_);

    // Z_HVH: trunk is vertical at x_cut between the two block centres.
    int min_x = std::min(s.x, d.x), max_x = std::max(s.x, d.x);
    for (int x_cut : x_grid) {
        if (x_cut > min_x && x_cut < max_x) {
            int sx = use_busterm_ ? src.face_x(x_cut) : s.x;
            int dx = use_busterm_ ? dst.face_x(x_cut) : d.x;
            int ty_src = stub_y(use_busterm_, sx != x_cut, src, d.y, s.y);
            int ty_dst = stub_y(use_busterm_, dx != x_cut, dst, s.y, d.y);

            if (ty_src != ty_dst) {
                // Enforce horizontal stub lengths.
                if (std::abs(x_cut - s_orig.face_x(x_cut)) >= m_h &&
                    std::abs(x_cut - d_orig.face_x(x_cut)) >= m_h) {
                    Topology z; z.type = "Z_HVH@x" + std::to_string(x_cut) + "@y" + std::to_string(ty_src);
                    if (sx != x_cut)
                        z.segments.push_back(make_seg(s_orig.face_x(x_cut), ty_src, x_cut, ty_src, h_layer_));
                    z.segments.push_back(make_seg(x_cut, ty_src, x_cut, ty_dst, v_layer_));
                    if (x_cut != dx)
                        z.segments.push_back(make_seg(x_cut, ty_dst, d_orig.face_x(x_cut), ty_dst, h_layer_));
                    if (z.segments.size() == 3) results.push_back(z);
                }
            } else if (use_busterm_ && sx != x_cut && x_cut != dx) {
                // Spread Z_HVH
                int sy_hi = src.y2; int sy_lo = src.y1;
                int dy_hi = dst.y2; int dy_lo = dst.y1;

                if (std::abs(x_cut - s_orig.face_x(x_cut)) >= m_h &&
                    std::abs(x_cut - d_orig.face_x(x_cut)) >= m_h) {
                    if (sy_hi != dy_lo) {
                        Topology z; z.type = "Z_HVH@x" + std::to_string(x_cut) + "@y" + std::to_string(sy_hi);
                        z.segments.push_back(make_seg(s_orig.face_x(x_cut), sy_hi, x_cut, sy_hi, h_layer_));
                        z.segments.push_back(make_seg(x_cut, sy_hi, x_cut, dy_lo, v_layer_));
                        z.segments.push_back(make_seg(x_cut, dy_lo, d_orig.face_x(x_cut), dy_lo, h_layer_));
                        results.push_back(z);
                    }
                    if (sy_lo != dy_hi) {
                        Topology z; z.type = "Z_HVH@x" + std::to_string(x_cut) + "@y" + std::to_string(sy_lo);
                        z.segments.push_back(make_seg(s_orig.face_x(x_cut), sy_lo, x_cut, sy_lo, h_layer_));
                        z.segments.push_back(make_seg(x_cut, sy_lo, x_cut, dy_hi, v_layer_));
                        z.segments.push_back(make_seg(x_cut, dy_hi, d_orig.face_x(x_cut), dy_hi, h_layer_));
                        results.push_back(z);
                    }
                }
            }
        }
    }

    // Z_VHV: trunk is horizontal at y_cut between the two block centres.
    int min_y = std::min(s.y, d.y), max_y = std::max(s.y, d.y);
    for (int y_cut : y_grid) {
        if (y_cut > min_y && y_cut < max_y) {
            int sy = use_busterm_ ? src.face_y(y_cut) : s.y;
            int dy = use_busterm_ ? dst.face_y(y_cut) : d.y;
            int vx_src = stub_x(use_busterm_, sy != y_cut, src, d.x, s.x);
            int vx_dst = stub_x(use_busterm_, dy != y_cut, dst, s.x, d.x);

            if (vx_src != vx_dst) {
                // Enforce vertical stub lengths.
                if (std::abs(y_cut - s_orig.face_y(y_cut)) >= m_v &&
                    std::abs(y_cut - d_orig.face_y(y_cut)) >= m_v) {
                    Topology z; z.type = "Z_VHV@y" + std::to_string(y_cut) + "@x" + std::to_string(vx_src);
                    if (sy != y_cut)
                        z.segments.push_back(make_seg(vx_src, s_orig.face_y(y_cut), vx_src, y_cut, v_layer_));
                    z.segments.push_back(make_seg(vx_src, y_cut, vx_dst, y_cut, h_layer_));
                    if (y_cut != dy)
                        z.segments.push_back(make_seg(vx_dst, y_cut, vx_dst, d_orig.face_y(y_cut), v_layer_));
                    if (z.segments.size() == 3) results.push_back(z);
                }
            } else if (use_busterm_ && sy != y_cut && y_cut != dy) {
                // Spread Z_VHV
                int vx_hi = src.x2; int vx_lo = src.x1;

                if (std::abs(y_cut - s_orig.face_y(y_cut)) >= m_v &&
                    std::abs(y_cut - d_orig.face_y(y_cut)) >= m_v) {
                    for (int flip = 0; flip < 2; ++flip) {
                        int x1 = flip ? vx_lo : vx_hi;
                        int x2 = flip ? vx_hi : vx_lo;
                        if (x1 == x2) continue;
                        // Reject: dst stub must land within dst's x-range
                        if (x2 < d_orig.x1 || x2 > d_orig.x2) continue;
                        Topology z; z.type = "Z_VHV@y" + std::to_string(y_cut) + "@x" + std::to_string(x1);
                        z.segments.push_back(make_seg(x1, s_orig.face_y(y_cut), x1, y_cut, v_layer_));
                        z.segments.push_back(make_seg(x1, y_cut, x2, y_cut, h_layer_));
                        z.segments.push_back(make_seg(x2, y_cut, x2, d_orig.face_y(y_cut), v_layer_));
                        results.push_back(z);
                    }
                }
            }
        }
    }
}

void TopologyGenerator::add_u_shapes(const Busterm& s_bt, const Busterm& d_bt,
                                      const std::vector<int>& x_grid,
                                      const std::vector<int>& y_grid,
                                      std::vector<Topology>& results) {
    const Rect& src = s_bt.bbox; const Rect& dst = d_bt.bbox;
    const Rect& s_orig = s_bt.orig_bbox; const Rect& d_orig = d_bt.orig_bbox;
    Point s = src.center(); Point d = dst.center();
    int min_x = std::min(s.x, d.x), max_x = std::max(s.x, d.x);
    int min_y = std::min(s.y, d.y), max_y = std::max(s.y, d.y);

    int m_v = floorplan_.get_min_stub_length(1 /*VERTICAL*/, v_layer_);
    int m_h = floorplan_.get_min_stub_length(0 /*HORIZONTAL*/, h_layer_);

    // U_HVH: vertical detour trunk left/right of bounding box.
    for (int x_cut : x_grid) {
        if (x_cut < min_x || x_cut > max_x) {
            int sx = use_busterm_ ? src.face_x(x_cut) : s.x;
            int dx = use_busterm_ ? dst.face_x(x_cut) : d.x;
            int ty_src = stub_y(use_busterm_, sx != x_cut, src, d.y, s.y);
            int ty_dst = stub_y(use_busterm_, dx != x_cut, dst, s.y, d.y);

            if (std::abs(x_cut - s_orig.face_x(x_cut)) >= m_h &&
                std::abs(x_cut - d_orig.face_x(x_cut)) >= m_h) {
                Topology u; u.type = "U_HVH@x" + std::to_string(x_cut);
                if (sx != x_cut)
                    u.segments.push_back(make_seg(s_orig.face_x(x_cut), ty_src, x_cut, ty_src, h_layer_));
                if (ty_src != ty_dst)
                    u.segments.push_back(make_seg(x_cut, ty_src, x_cut, ty_dst, v_layer_));
                if (x_cut != dx)
                    u.segments.push_back(make_seg(x_cut, ty_dst, d_orig.face_x(x_cut), ty_dst, h_layer_));
                if (u.segments.size() == 3) results.push_back(u);
            }
        }
    }

    // U_VHV: horizontal detour trunk above/below bounding box.
    for (int y_cut : y_grid) {
        if (y_cut < min_y || y_cut > max_y) {
            int sy = use_busterm_ ? src.face_y(y_cut) : s.y;
            int dy = use_busterm_ ? dst.face_y(y_cut) : d.y;
            int vx_src = stub_x(use_busterm_, sy != y_cut, src, d.x, s.x);
            int vx_dst = stub_x(use_busterm_, dy != y_cut, dst, s.x, d.x);

            if (std::abs(y_cut - s_orig.face_y(y_cut)) >= m_v &&
                std::abs(y_cut - d_orig.face_y(y_cut)) >= m_v) {
                Topology u; u.type = "U_VHV@y" + std::to_string(y_cut);
                if (sy != y_cut)
                    u.segments.push_back(make_seg(vx_src, s_orig.face_y(y_cut), vx_src, y_cut, v_layer_));
                if (vx_src != vx_dst)
                    u.segments.push_back(make_seg(vx_src, y_cut, vx_dst, y_cut, h_layer_));
                if (y_cut != dy)
                    u.segments.push_back(make_seg(vx_dst, y_cut, vx_dst, d_orig.face_y(y_cut), v_layer_));
                if (u.segments.size() == 3) results.push_back(u);
            }
        }
    }
}

void TopologyGenerator::add_uu_shapes(const Busterm& s_bt, const Busterm& d_bt,
                                       const std::vector<int>& x_grid,
                                       const std::vector<int>& y_grid,
                                       std::vector<Topology>& results) {
    if (!use_busterm_) return;
    const Rect& src = s_bt.bbox; const Rect& dst = d_bt.bbox;
    const Rect& s_orig = s_bt.orig_bbox; const Rect& d_orig = d_bt.orig_bbox;
    Point s = src.center(); Point d = dst.center();
    int min_x = std::min(s.x, d.x), max_x = std::max(s.x, d.x);
    int min_y = std::min(s.y, d.y), max_y = std::max(s.y, d.y);
    int bp_x_lo = std::min({src.x1, src.x2, dst.x1, dst.x2});
    int bp_x_hi = std::max({src.x1, src.x2, dst.x1, dst.x2});
    int bp_y_lo = std::min({src.y1, src.y2, dst.y1, dst.y2});
    int bp_y_hi = std::max({src.y1, src.y2, dst.y1, dst.y2});
    int margin_x = std::max(1, (int)(0.1 * (bp_x_hi - bp_x_lo)));
    int margin_y = std::max(1, (int)(0.1 * (bp_y_hi - bp_y_lo)));

    int m_v = floorplan_.get_min_stub_length(1 /*VERTICAL*/, v_layer_);
    int m_h = floorplan_.get_min_stub_length(0 /*HORIZONTAL*/, h_layer_);

    for (int y_cut : y_grid) {
        if (y_cut >= min_y && y_cut <= max_y) continue;
        int dy      = d_orig.face_y(y_cut);
        int vx_dst  = stub_x(true, dy != y_cut, dst, s.x, d.x);
        int exit_x = (std::abs(src.x1 - d.x) >= std::abs(src.x2 - d.x)) ? src.x1 : src.x2;
        int sy_src = (y_cut < min_y) ? src.y1 : src.y2;
        int x_corner = (exit_x == src.x1) ? src.x1 - margin_x : src.x2 + margin_x;

        if (std::abs(y_cut - sy_src) >= m_v && std::abs(y_cut - d_orig.face_y(y_cut)) >= m_v &&
            std::abs(x_corner - s_orig.face_x(x_corner)) >= m_h) {
            Topology uu; uu.type = "UU_VHV@y" + std::to_string(y_cut);
            if (exit_x != x_corner)
                uu.segments.push_back(make_seg(s_orig.face_x(x_corner), sy_src, x_corner, sy_src, h_layer_));
            if (sy_src != y_cut)
                uu.segments.push_back(make_seg(x_corner, sy_src, x_corner, y_cut, v_layer_));
            if (x_corner != vx_dst)
                uu.segments.push_back(make_seg(x_corner, y_cut, vx_dst, y_cut, h_layer_));
            if (y_cut != dy)
                uu.segments.push_back(make_seg(vx_dst, y_cut, vx_dst, dy, v_layer_));
            if ((int)uu.segments.size() >= 3) results.push_back(uu);
        }
    }

    for (int x_cut : x_grid) {
        if (x_cut >= min_x && x_cut <= max_x) continue;
        int dx      = d_orig.face_x(x_cut);
        int ty_dst  = stub_y(true, dx != x_cut, dst, s.y, d.y);
        int exit_y = (std::abs(src.y1 - d.y) >= std::abs(src.y2 - d.y)) ? src.y1 : src.y2;
        int tx_src = (x_cut < min_x) ? src.x1 : src.x2;
        int y_corner = (exit_y == src.y1) ? src.y1 - margin_y : src.y2 + margin_y;

        if (std::abs(x_cut - tx_src) >= m_h && std::abs(x_cut - d_orig.face_x(x_cut)) >= m_h &&
            std::abs(y_corner - s_orig.face_y(y_corner)) >= m_v) {
            Topology uu; uu.type = "UU_HVH@x" + std::to_string(x_cut);
            if (exit_y != y_corner)
                uu.segments.push_back(make_seg(tx_src, s_orig.face_y(y_corner), tx_src, y_corner, v_layer_));
            if (tx_src != x_cut)
                uu.segments.push_back(make_seg(tx_src, y_corner, x_cut, y_corner, h_layer_));
            if (y_corner != ty_dst)
                uu.segments.push_back(make_seg(x_cut, y_corner, x_cut, ty_dst, v_layer_));
            if (x_cut != dx)
                uu.segments.push_back(make_seg(x_cut, ty_dst, dx, ty_dst, h_layer_));
            if ((int)uu.segments.size() >= 3) results.push_back(uu);
        }
    }
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

static void bundle_hanan_grid(const std::vector<Rect>& rects,
                               std::vector<int>& xs, std::vector<int>& ys) {
    for (const auto& r : rects) {
        xs.push_back(r.x1); xs.push_back(r.x2);
        ys.push_back(r.y1); ys.push_back(r.y2);
    }
    auto sort_unique = [](std::vector<int>& v) {
        std::sort(v.begin(), v.end());
        v.erase(std::unique(v.begin(), v.end()), v.end());
    };
    sort_unique(xs); sort_unique(ys);
}

static int wirelength(const Topology& t) {
    int wl = 0;
    for (const auto& s : t.segments)
        wl += std::abs(s.end.x - s.start.x) + std::abs(s.end.y - s.start.y);
    // TEG-OVER bridge wires live outside `segments` but are real routed metal;
    // count them so WL is honest (otherwise the planner ranks a bridged
    // candidate as artificially cheap).
    for (const auto& [bname, s] : t.bridge_segments) {
        (void)bname;
        wl += std::abs(s.end.x - s.start.x) + std::abs(s.end.y - s.start.y);
    }
    return wl;
}

static void annotate_and_sort(std::vector<Topology>& v) {
    for (auto& t : v)
        t.estimated_wirelength = wirelength(t);
    // Experiment toggle (BUDA_TOPO_SORT=segs): order candidates by ascending
    // SEGMENT COUNT first, estimated wirelength second — the "simplest shape
    // first" extreme.  Candidate order feeds the display, script/sidecar
    // indices, the planner's tie-break, and ripup's index-window alternates;
    // the planner's SELECTION stays cost-driven either way (see kSegs for
    // the penalty that changes selection).  Default: wirelength order.
    const char* mode = std::getenv("BUDA_TOPO_SORT");
    const bool segs_first = (mode != nullptr && std::string(mode) == "segs");
    // Structural tie-break (wishlist-topo "Nominal-WL comparability", piece b):
    // equal-WL candidates order by ascending SEGMENT COUNT — fewer segments =
    // fewer junctions = tighter realization risk — so a 6-junction MST
    // staircase no longer outranks a 2-seg trunk at the same nominal WL just
    // because ASCII '+' < '@' (the b44 mis-pick: the planner's equal-score
    // tie-break keeps the LOWEST index, which this sort defines).  The type
    // string stays as the final determinism anchor only.  Bridge segments
    // (TEG-over) are real wires with a junction each; count them so a bridged
    // candidate doesn't look structurally simpler than it is.
    auto nsegs = [](const Topology& t) {
        return t.segments.size() + t.bridge_segments.size();
    };
    std::sort(v.begin(), v.end(),
        [&](const Topology& a, const Topology& b) {
            if (segs_first && a.segments.size() != b.segments.size())
                return a.segments.size() < b.segments.size();
            if (a.estimated_wirelength != b.estimated_wirelength)
                return a.estimated_wirelength < b.estimated_wirelength;
            if (nsegs(a) != nsegs(b))
                return nsegs(a) < nsegs(b);
            return a.type < b.type;
        });
}

// ---------------------------------------------------------------------------
// Multi-rect helpers
// ---------------------------------------------------------------------------

// Return all physical rects for a busterm. Empty rects means single-rect
// (use orig_bbox). This returns the individual rects when present.
static std::vector<Rect> bt_all_rects(const Busterm& bt) {
    return bt.rects.empty() ? std::vector<Rect>{bt.orig_bbox} : bt.rects;
}

// True if any two rects have overlapping interiors (strict overlap, not just touching).
// A block whose rects overlap forms a rectilinear polygon; one with disjoint rects
// is a true TEG (terminal equivalence group).
static bool rects_are_rectilinear(const std::vector<Rect>& rects) {
    for (size_t i = 0; i < rects.size(); ++i)
        for (size_t j = i + 1; j < rects.size(); ++j) {
            const Rect& a = rects[i]; const Rect& b = rects[j];
            if (a.x1 < b.x2 && b.x1 < a.x2 && a.y1 < b.y2 && b.y1 < a.y2)
                return true;
        }
    return false;
}

// Best rect for connecting bt to an H trunk at y_trunk: minimises stub length.
// Best rect for connecting bt to a spine at perpendicular coordinate `trunk_locus`
// (the shortest stub): pick the candidate rect whose perp-axis face is nearest the
// spine.  Axis-parameterized — `axis.perp_face` selects face_y for an H spine
// (along_horiz=true, the old best_rect_for_h) and face_x for a V spine (the old
// best_rect_for_v), so the two transposed helpers collapse into one.
static Rect best_rect(const Axis& axis, const Busterm& bt, int trunk_locus) {
    auto rects = bt_all_rects(bt);
    Rect best = rects[0];
    int  best_cost = std::abs(axis.perp_face(best, trunk_locus) - trunk_locus);
    for (size_t k = 1; k < rects.size(); ++k) {
        int cost = std::abs(axis.perp_face(rects[k], trunk_locus) - trunk_locus);
        if (cost < best_cost) { best_cost = cost; best = rects[k]; }
    }
    return best;
}

// Emit the trunk spine along `axis` at perpendicular coordinate `locus`, over the
// along-axis span [lo, hi], split around each feedthru gap (a [gap_lo, gap_hi]
// interval along the spine the trunk skips because the block's own router bridges
// it).  Reproduces the add_trunk_h/v emission exactly: emits nothing when lo>=hi
// (preserving conn_topology's non-inverting invariant) and one segment when there
// are no gaps.  `gaps` is taken by value so the internal sort never touches the
// caller's vector.
static void emit_spine(Topology& t, const Axis& axis, int locus, int lo, int hi,
                       std::vector<std::pair<int,int>> gaps, int layer) {
    if (lo >= hi) return;
    if (gaps.empty()) {
        t.segments.push_back(axis.mkseg(lo, locus, hi, locus, layer));
        return;
    }
    std::sort(gaps.begin(), gaps.end());
    int cur = lo;
    for (const auto& g : gaps) {
        if (cur < g.first)
            t.segments.push_back(axis.mkseg(cur, locus, g.first, locus, layer));
        cur = std::max(cur, g.second);
    }
    if (cur < hi)
        t.segments.push_back(axis.mkseg(cur, locus, hi, locus, layer));
}

static void annotate_endpoints(Topology& topo,
                                const std::vector<Busterm>& blocks) {
    for (int i = 0; i < (int)topo.segments.size(); ++i) {
        const Segment& seg = topo.segments[i];
        bool horiz = (seg.start.y == seg.end.y);
        // Two-pass per-endpoint assignment: PREFER a block the segment ABUTS FROM
        // OUTSIDE (endpoint on the block's face perpendicular to travel AND the
        // body P->other points AWAY from the block's interior); only if nothing
        // abuts does an endpoint fall back to the old first-coincident-face rule
        // (so a terminus that merely lands on a face still taps).  Stops a block
        // the segment passes THROUGH (big2 b25: driver blk_10) from stealing the
        // face-tap of a receiver abutting the same trunk endpoint (blk_01/blk_03).
        // See docs/internal/big2_b25_abutment_tap_dnuts_2026-07.md.
        auto rects_of = [&](const Busterm& bt) -> std::vector<Rect> {
            if (!bt.rects.empty()) return bt.rects;
            return {bt.orig_bbox, bt.bbox};
        };
        auto abuts_rect = [&](const Point& P, const Point& other, const Rect& r) -> bool {
            if (horiz) {
                if (!(P.y >= r.y1 && P.y <= r.y2)) return false;
                if (P.x == r.x1) return other.x < P.x;
                if (P.x == r.x2) return other.x > P.x;
                return false;
            }
            if (!(P.x >= r.x1 && P.x <= r.x2)) return false;
            if (P.y == r.y1) return other.y < P.y;
            if (P.y == r.y2) return other.y > P.y;
            return false;
        };
        auto on_face_rect = [&](const Point& P, const Rect& r) -> bool {
            return horiz
                ? (P.x == r.x1 || P.x == r.x2) && P.y >= r.y1 && P.y <= r.y2
                : (P.y == r.y1 || P.y == r.y2) && P.x >= r.x1 && P.x <= r.x2;
        };
        auto assign = [&](std::optional<Busterm>& slot,
                          const Point& P, const Point& other) {
            if (slot.has_value()) return;
            for (const Busterm& bt : blocks)
                for (const Rect& r : rects_of(bt))
                    if (abuts_rect(P, other, r)) { slot = bt; return; }
            for (const Busterm& bt : blocks)
                for (const Rect& r : rects_of(bt))
                    if (on_face_rect(P, r)) { slot = bt; return; }
        };
        auto& ep = topo.seg_busterms[i];
        assign(ep.first,  seg.start, seg.end);
        assign(ep.second, seg.end,   seg.start);
    }
}

// Remove segment `idx` from a topology, re-keying seg_busterms to the compacted
// indices (entries below idx unchanged, entries above shifted down by one).
void erase_segment(Topology& topo, int idx) {
    topo.segments.erase(topo.segments.begin() + idx);
    std::map<int, SegEndpoints> nb;
    for (auto& [k, v] : topo.seg_busterms) {
        if (k < idx)       nb[k] = v;
        else if (k > idx)  nb[k - 1] = v;
        // k == idx is dropped
    }
    topo.seg_busterms = std::move(nb);
    // Re-key seg_conns the same way: drop records touching idx (as key or as a
    // connected other), shift indices above idx down by one on both sides.
    std::map<std::pair<int,int>, std::vector<int>> nc;
    for (auto& [k, others] : topo.seg_conns) {
        if (k.first == idx) continue;
        const int nk = (k.first > idx) ? k.first - 1 : k.first;
        std::vector<int> no;
        for (int o : others) {
            if (o == idx) continue;
            no.push_back(o > idx ? o - 1 : o);
        }
        if (!no.empty()) nc[{nk, k.second}] = std::move(no);
    }
    topo.seg_conns = std::move(nc);
    // seg_bits is index-keyed too (tapered fan-in membership).
    if (!topo.seg_bits.empty()) {
        std::map<int, std::vector<int>> nsb;
        for (auto& [k, v] : topo.seg_bits) {
            if (k < idx)      nsb[k] = std::move(v);
            else if (k > idx) nsb[k - 1] = std::move(v);
        }
        topo.seg_bits = std::move(nsb);
    }
    // The tap membership is keyed by segment index too, so it shifts in step —
    // otherwise a removed segment desyncs it from seg_bits and taps land on the
    // wrong segment's bits.
    if (!topo.seg_busterm_bits.empty()) {
        std::map<std::pair<int,int>, std::vector<int>> nbb;
        for (auto& [k, v] : topo.seg_busterm_bits) {
            if (k.first < idx)      nbb[{k.first, k.second}] = std::move(v);
            else if (k.first > idx) nbb[{k.first - 1, k.second}] = std::move(v);
        }
        topo.seg_busterm_bits = std::move(nbb);
    }
}

std::vector<int> derive_fanin_seg_bits(
    Topology& topo, const Floorplan& fp,
    const std::vector<std::string>& driver_per_bit,
    const std::vector<std::vector<std::string>>& receivers_per_bit)
{
    const int n_seg  = (int)topo.segments.size();
    const int n_bits = (int)driver_per_bit.size();
    topo.seg_bits.clear();
    topo.seg_busterm_bits.clear();
    std::vector<int> fallback_bits;
    if (n_seg == 0 || n_bits == 0 || (int)receivers_per_bit.size() != n_bits)
        return fallback_bits;

    // Segment adjacency from the authoritative seg_conns junctions.
    std::vector<std::vector<int>> adj(n_seg);
    for (const auto& [key, others] : topo.seg_conns) {
        const int a = key.first;
        if (a < 0 || a >= n_seg) continue;
        for (int b : others)
            if (b >= 0 && b < n_seg) { adj[a].push_back(b); adj[b].push_back(a); }
    }

    // Segments attaching each endpoint block: a BUSTERM tap (seg_busterms),
    // else a pass-through crossing of the block's rects (the same overlap
    // predicate the coverage checks use).  Cached per block.
    std::map<std::string, std::vector<int>> attach;
    auto attach_segs = [&](const std::string& block) -> const std::vector<int>& {
        auto it = attach.find(block);
        if (it != attach.end()) return it->second;
        std::vector<int> segs;
        for (const auto& [si, eps] : topo.seg_busterms) {
            if (si < 0 || si >= n_seg) continue;
            if ((eps.first  && eps.first->block_name  == block) ||
                (eps.second && eps.second->block_name == block))
                segs.push_back(si);
        }
        if (segs.empty()) {
            auto rects = fp.get_block_rects(block);
            if (rects.empty()) rects.push_back(fp.get_block_bounds(block));
            for (int si = 0; si < n_seg; ++si) {
                const Segment& s = topo.segments[si];
                const bool h    = (s.start.y == s.end.y);
                const int  perp = h ? s.start.y : s.start.x;
                const int  lo   = h ? std::min(s.start.x, s.end.x)
                                    : std::min(s.start.y, s.end.y);
                const int  hi   = h ? std::max(s.start.x, s.end.x)
                                    : std::max(s.start.y, s.end.y);
                for (const Rect& r : rects) {
                    const bool hit = h
                        ? (perp >= r.y1 && perp <= r.y2 && lo <= r.x2 && hi >= r.x1)
                        : (perp >= r.x1 && perp <= r.x2 && lo <= r.y2 && hi >= r.y1);
                    if (hit) { segs.push_back(si); break; }
                }
            }
        }
        return attach.emplace(block, std::move(segs)).first->second;
    };

    // Group bits with identical endpoints so the BFS runs once per group
    // (an N-bit sub-bus from one driver is one walk, not N).
    std::map<std::pair<std::string, std::vector<std::string>>,
             std::vector<int>> groups;
    for (int b = 0; b < n_bits; ++b)
        groups[{driver_per_bit[b], receivers_per_bit[b]}].push_back(b);

    std::vector<std::vector<int>> bits_of_seg(n_seg);
    std::map<std::pair<int,int>, std::vector<int>> tap_bits;
    auto mark_all = [&](const std::vector<int>& bits) {
        for (int si = 0; si < n_seg; ++si) {
            for (int b : bits) bits_of_seg[si].push_back(b);
            // Endpoints unresolved for this group: fall back to the
            // historical all-bits behavior for its taps too, so an
            // unwalkable group never LOSES a tap it might need.  Only over
            // the ordinals seg_busterms actually records — an entry for a
            // tap that does not exist is unreadable (the predicate resolves
            // the ordinal through seg_busterms first) and would just make
            // the map assert things about taps the topology does not have.
            auto bt = topo.seg_busterms.find(si);
            if (bt == topo.seg_busterms.end()) continue;
            const bool has[2] = { (bool)bt->second.first, (bool)bt->second.second };
            for (int k = 0; k < 2; ++k) {
                if (!has[k]) continue;
                auto& v = tap_bits[{si, k}];
                v.insert(v.end(), bits.begin(), bits.end());
            }
        }
        fallback_bits.insert(fallback_bits.end(), bits.begin(), bits.end());
    };

    for (const auto& [ep, bits] : groups) {
        const auto& d_segs = attach_segs(ep.first);
        if (d_segs.empty()) { mark_all(bits); continue; }
        // BFS from ALL driver-attaching segments at once (parent forest).
        std::vector<int> parent(n_seg, -2);
        std::deque<int> q;
        for (int si : d_segs)
            if (parent[si] == -2) { parent[si] = -1; q.push_back(si); }
        while (!q.empty()) {
            int u = q.front(); q.pop_front();
            for (int v : adj[u])
                if (parent[v] == -2) { parent[v] = u; q.push_back(v); }
        }
        std::set<int> member(d_segs.begin(), d_segs.end());
        bool ok = true;
        for (const std::string& rblock : ep.second) {
            const auto& r_segs = attach_segs(rblock);
            int hit = -1;
            for (int si : r_segs)
                if (parent[si] != -2) { hit = si; break; }
            if (hit < 0) { ok = false; break; }
            for (int si = hit; si != -1; si = parent[si]) member.insert(si);
        }
        if (!ok) { mark_all(bits); continue; }
        for (int si : member)
            for (int b : bits) bits_of_seg[si].push_back(b);

        // A segment's BUSTERM tap belongs to this group's bits only when the
        // tapped block is one of THEIR endpoints — their driver or one of
        // their receivers.  A trunk that taps a far receiver taps it for the
        // bits going there, not for the bits that branched off earlier; giving
        // every bit the tap is what let those bits keep metal out to it.
        for (int si : member) {
            auto bt = topo.seg_busterms.find(si);
            if (bt == topo.seg_busterms.end()) continue;
            const Busterm* eps[2] = { bt->second.first ? &*bt->second.first : nullptr,
                                      bt->second.second ? &*bt->second.second : nullptr };
            for (int k = 0; k < 2; ++k) {
                if (!eps[k]) continue;
                const std::string& bn = eps[k]->block_name;
                const bool mine = (bn == ep.first) ||
                    (std::find(ep.second.begin(), ep.second.end(), bn) != ep.second.end());
                if (!mine) continue;
                auto& v = tap_bits[{si, k}];
                v.insert(v.end(), bits.begin(), bits.end());
            }
        }
    }

    for (int si = 0; si < n_seg; ++si) {
        auto& v = bits_of_seg[si];
        std::sort(v.begin(), v.end());
        v.erase(std::unique(v.begin(), v.end()), v.end());
        if (!v.empty()) topo.seg_bits[si] = std::move(v);
    }
    for (auto& [key, v] : tap_bits) {
        std::sort(v.begin(), v.end());
        v.erase(std::unique(v.begin(), v.end()), v.end());
        if (!v.empty()) topo.seg_busterm_bits[key] = std::move(v);
    }
    std::sort(fallback_bits.begin(), fallback_bits.end());
    fallback_bits.erase(std::unique(fallback_bits.begin(), fallback_bits.end()),
                        fallback_bits.end());
    return fallback_bits;
}

bool seg_busterm_serves_bit(const Topology& topo, int si,
                            const std::string& block, int bit)
{
    if (topo.seg_busterm_bits.empty()) return true;   // untapered
    auto bt = topo.seg_busterms.find(si);
    if (bt == topo.seg_busterms.end()) return true;
    const Busterm* eps[2] = {
        bt->second.first  ? &*bt->second.first  : nullptr,
        bt->second.second ? &*bt->second.second : nullptr };
    for (int k = 0; k < 2; ++k) {
        if (!eps[k] || eps[k]->block_name != block) continue;
        auto f = topo.seg_busterm_bits.find({si, k});
        if (f == topo.seg_busterm_bits.end() || f->second.empty()) return true;
        return std::binary_search(f->second.begin(), f->second.end(), bit);
    }
    return true;   // no tap ordinal for this block: nothing recorded, so serve all
}

// Per-segment conn COUNT under the connections ConnTopology infers (BUSTERM
// taps + SEG junctions) -- the attachment count the placed stages see, and the
// ANTENNA predicate's input (a segment attached at < 2 points dangles).
// Same mid-generation derivation as conn_seg_components below.
static std::vector<size_t> conn_counts(const Topology& topo,
                                       const Floorplan& fp) {
    Topology t2 = topo;
    annotate_seg_conns(t2);
    ConnTopology ct;
    ct.build(t2, fp);
    std::vector<size_t> out;
    out.reserve(ct.segs().size());
    for (const auto& cs : ct.segs()) out.push_back(cs.conns.size());
    return out;
}

// Number of connected components of `topo` under the SEG (wire-junction)
// connections ConnTopology infers -- the connectivity the downstream stages see.
static int conn_seg_components(const Topology& topo, const Floorplan& fp) {
    // Runs mid-generation, BEFORE the candidate's one-time seg_conns post-pass —
    // derive the junctions on a local copy (ConnTopology no longer infers them).
    Topology t2 = topo;
    annotate_seg_conns(t2);
    ConnTopology ct;
    ct.build(t2, fp);
    const auto& segs = ct.segs();
    int n = (int)segs.size();
    if (n == 0) return 0;
    std::vector<int> uf(n);
    std::iota(uf.begin(), uf.end(), 0);
    auto find = [&uf](int x) { while (uf[x] != x) { uf[x] = uf[uf[x]]; x = uf[x]; } return x; };
    for (int i = 0; i < n; ++i)
        for (const auto& c : segs[i].conns)
            if (c.kind == SegConn::SEG) uf[find(i)] = find(c.seg_idx);
    std::set<int> roots;
    for (int i = 0; i < n; ++i) roots.insert(find(i));
    return (int)roots.size();
}

// True if any connector (index >= n_orig) is collinear-contained within another
// segment -- the overlap that can close a redundant loop at a high-degree relay.
static bool has_collinear_overlap(const Topology& topo, int n_orig) {
    auto covers = [](const Segment& o, const Segment& c) {
        bool o_h = o.start.y == o.end.y, c_h = c.start.y == c.end.y;
        if (o_h != c_h) return false;
        if (c_h) {
            if (o.start.y != c.start.y) return false;
            int olo = std::min(o.start.x, o.end.x), ohi = std::max(o.start.x, o.end.x);
            int clo = std::min(c.start.x, c.end.x), chi = std::max(c.start.x, c.end.x);
            return olo <= clo && chi <= ohi;
        }
        if (o.start.x != c.start.x) return false;
        int olo = std::min(o.start.y, o.end.y), ohi = std::max(o.start.y, o.end.y);
        int clo = std::min(c.start.y, c.end.y), chi = std::max(c.start.y, c.end.y);
        return olo <= clo && chi <= ohi;
    };
    int n = (int)topo.segments.size();
    for (int c = n_orig; c < n; ++c)
        for (int o = 0; o < n; ++o)
            if (o != c && covers(topo.segments[o], topo.segments[c])) return true;
    return false;
}

// ---------------------------------------------------------------------------
// MST feedthrough completion
// ---------------------------------------------------------------------------
//
// MST edges land each edge's L-shape on a block's nearest face independently
// (closest_points per edge), so a block with MST degree >= 2 ends up touched by
// two or more segment endpoints at DIFFERENT points.  Those segments do not
// share a vertex; they are "connected" only because both touch the block, i.e.
// the block is silently used as a feedthrough relay.  That (a) under-counts
// wirelength and (b) relies on intra-block routing the designer never requested.
//
// A *feedthru* -- a block that connects two or more of a bundle's stubs via its
// own lower-level (intra-block) routing -- is an opt-in OPTION that does not
// exist yet.  Until it does, every topology must be physically self-connected.
// This pass adds the missing wire: at each relay block it chains the incident
// landings with perpendicular L/dogleg connectors so they form one physically
// connected component.  Connectors may run along or cross the block footprint --
// that is a benign global wire (like a trunk passing through a block), NOT a
// feedthru.
//
// It then enforces a SINGLE-TAP model: the relay keeps a busterm tap on exactly
// one incident stub (the one with the most slide flexibility) and every other
// landing -- and both endpoints of each appended connector -- is annotated as an
// internal (nullopt) junction.  This is essential: ConnTopology treats a busterm
// annotation as authoritative and skips SEG inference at it, so leaving every
// landing tagged as a busterm would make the connector wire invisible downstream
// (NUTS/detailed-NUTS would still see a through-block feedthrough and could slide
// the pieces apart).  Demoting the extras to nullopt forces real SEG junctions.
//
// A straight trunk crossing a block is one continuous wire and is left
// untouched: its endpoints do not land on the crossed block's faces, so the
// block collects zero incident endpoints here.
// After a TRUNK+MST hybrid is completed, is its seed trunk (the spine at
// trunk_pos) REDUNDANT — i.e. can it be removed while the rest of the topology
// stays a valid route (every block still covered, one electrical island)?
// When the MST edges already connect every endpoint, completion can leave the
// spine as extra wire the tree does not need — a vestigial trunk that renders
// as an abstract-NUTS phantom span DetailedNUTS silently shrinks (bus_005/
// bundle 67 and ~68 more across bigHalf — docs/internal/bus005_dangling_scan).
//
// Removability is the exact test (a spine that is the ONLY coverage/connection
// for some block is NOT removable and is kept — this is what makes it correct
// where a "single-point attachment" or "any overshoot" heuristic was not: it
// keeps a genuinely load-bearing pass-through trunk (Codex P2) AND the partial-
// overshoot trunks the planner actually uses (dropping those regressed mix),
// while dropping only the truly-redundant ones).  The caller drops the whole
// candidate; the plain trunk / L / Z / a clean hybrid still cover the bundle,
// and the coverage gate runs after, so a bundle is never stranded.
//
// The spine is the longest segment matching the trunk orientation at trunk_pos.
// require_dangling_spine: when true, a spine that is merely connectivity-
// redundant is NOT enough — the spine must ALSO be truly dangling (a free
// along-endpoint with a single SEG connection).  This is the OOB discriminator:
// a genuine OOB detour trunk is a real bridge attached at BOTH ends, and though
// the MST may make it connectivity-redundant it can still be the geometry a
// healer uses to escape congestion (slowdown_rnr regresses 0/0→2/8 if such a
// detour is dropped).  Only a vestigial OOB trunk that hangs off the tree at ONE
// point (bus_033 cand 29) is both redundant and dangling — that one we drop.
// The spine: the longest segment matching the trunk orientation at trunk_pos.
// -1 when the completed tree has none (every spine segment was consumed).
static int find_spine_index(const Topology& topo, int trunk_pos, bool is_h) {
    int spine = -1;
    long best_len = -1;
    for (int j = 0; j < (int)topo.segments.size(); ++j) {
        const Segment& sg = topo.segments[j];
        bool h = (sg.start.y == sg.end.y);
        if (is_h ? (!h || sg.start.y != trunk_pos)
                 : (h || sg.start.x != trunk_pos)) continue;
        long len = std::abs(is_h ? sg.end.x - sg.start.x : sg.end.y - sg.start.y);
        if (len > best_len) { best_len = len; spine = j; }
    }
    return spine;
}

// Is the completed hybrid's seed trunk an ANTENNA — attached to the rest of the
// route at fewer than two distinct points (verify's seg_attachment)?  Then the
// candidate carries electrically inert metal by construction and the caller
// drops it: there is no reason to offer the planner a shape whose spine is a
// wire terminating in nothing when the plain trunk and the standalone MST cover
// the bundle anyway (issue #485 question 1).
//
// This is DELIBERATELY independent of seed_trunk_is_redundant below.  An antenna
// spine can still be the only thing holding two COLLINEAR stubs together at its
// single junction — ConnTopology infers a SEG link only for perpendicular pairs,
// so removing it reports DISCONNECTED and the removability test (correctly) says
// "load-bearing, keep".  That reasoning is about the trunk-LESS topology, which
// we never emit; the question here is whether to keep a candidate that is
// antenna-flagged as it stands.  It is not (issue #485's in-bbox family:
// comprehensive_demo b5 cand17, big b4 cand21, mix b18 cand11 — six candidates
// whose spine hangs off one point where two collinear stubs meet).
static bool seed_trunk_is_antenna(const Topology& topo, int trunk_pos, bool is_h,
                                  const Floorplan& fp) {
    const int spine = find_spine_index(topo, trunk_pos, is_h);
    if (spine < 0) return false;
    Topology oc = topo;
    annotate_seg_conns(oc);                      // see the note in the gate below
    ConnTopology oct;
    oct.build(oc, fp);
    if (spine >= (int)oct.segs().size()) return false;
    return seg_attachment(oct.segs()[spine], oc, fp).count() < 2;
}

static bool seed_trunk_is_redundant(const Topology& topo, int trunk_pos, bool is_h,
                                    const Floorplan& fp,
                                    bool require_dangling_spine = false) {
    const int spine = find_spine_index(topo, trunk_pos, is_h);
    if (spine < 0) return false;                 // no spine found — leave it

    // OOB discriminator: a genuine OOB detour trunk is a real bridge whose spine
    // attaches at both ends — connectivity-redundant (the MST gives an alternate
    // path) yet congestion-load-bearing (slowdown_rnr regresses 0/0→2/8 if such a
    // detour is dropped).  A VESTIGIAL OOB trunk (bus_033 cand 29) instead has a
    // spine that hangs off the tree at a single junction — a ConnSeg with ≤1
    // connection and no block face, the set_drop_dangling predicate.  So for OOB
    // we drop only when the SPINE itself dangles.
    if (require_dangling_spine) {
        // The caller's `topo` has had annotate_endpoints + complete_relay_junctions
        // but not the seg_conns derivation ConnTopology needs (otherwise every seg
        // reads 0 conns), so derive JUST that on a copy.
        //
        // It must be annotate_seg_conns, NOT annotate_topology: the latter also
        // re-derives seg_busterms GEOMETRICALLY, re-adding the very face landings
        // complete_relay_junctions deliberately demoted to nullopt under the
        // single-tap model.  The gate then judged a topology that is not the one
        // being pooled — on the corpus the re-annotated spine picked up a phantom
        // BUSTERM conn in ALL 20 OOB cases, reading "not dangling" while the
        // pooled candidate's spine hangs off a single junction.  That is issue
        // #485's OOB family: 20 of its 26 antennas are candidates this gate meant
        // to drop and did not.
        Topology oc = topo;
        annotate_seg_conns(oc);
        ConnTopology oct;
        oct.build(oc, fp);
        // The SPINE itself must dangle: a vestigial OOB trunk (bus_033 cand 29)
        // runs out of the die and hangs off the tree at a SINGLE junction.  A
        // genuine OOB detour (slowdown_rnr) is a real bridge whose spine attaches
        // at BOTH ends — some minor seg may dangle, but the load-bearing spine
        // does not, so we keep it.  "Dangles" is the ANTENNA predicate itself
        // (verify's seg_attachment: distinct busterm/junction POSITIONS plus
        // pass-through blocks), shared rather than re-rolled — the hand-rolled
        // conn-RECORD count this replaces is the same defect the #483 review found
        // in the checker, and a second copy would drift again.
        if (spine >= (int)oct.segs().size()) return false;
        if (seg_attachment(oct.segs()[spine], oc, fp).count() >= 2)
            return false;                        // real detour bridge — keep it
    }

    // Build the trunk-less topology and re-derive its connectivity from scratch
    // (annotate_topology, like a hand-built candidate), then audit it.
    Topology t;
    t.type = topo.type;
    t.trunk_location = topo.trunk_location;
    t.connected_block_names = topo.connected_block_names;
    t.feedthru_blocks = topo.feedthru_blocks;
    for (int j = 0; j < (int)topo.segments.size(); ++j)
        if (j != spine) t.segments.push_back(topo.segments[j]);
    if (t.segments.empty()) return false;        // spine was the only wire — needed

    annotate_topology(t, fp);
    ConnTopology ct;
    ct.build(t, fp);
    // Redundant iff removing the spine leaves NO coverage/connectivity fault: an
    // uncovered block (BUSTERM_OPEN), a split wire graph (unbridged DISCONNECTED),
    // or a newly-exposed feedthrough relay (FEEDTHRU_RELAY) all mean the spine
    // was load-bearing — keep the candidate.
    for (const auto& v : check_topo(ct, t, fp, -1).violations) {
        if (v.kind == ViolationKind::BUSTERM_OPEN) return false;
        if (v.kind == ViolationKind::FEEDTHRU_RELAY) return false;
        if (v.kind == ViolationKind::DISCONNECTED &&
            !disconnected_islands_bridged(ct, t, fp)) return false;
    }
    return true;                                 // valid without the spine → redundant
}

static void complete_relay_junctions(Topology& topo,
                                     const std::vector<Busterm>& blocks,
                                     const Floorplan& fp,
                                     int h_layer, int v_layer,
                                     bool spine_relays = false) {
    // min-stub is intentionally not enforced on completion connectors: a relay
    // MUST be completed (correctness over the min-stub heuristic).  fp is used by
    // the verified de-overlap pass at the end.
    auto on_face = [](const Point& P, bool horiz, const Busterm& bt) -> bool {
        auto check_rect = [&](const Rect& r) -> bool {
            return horiz
                ? (P.x == r.x1 || P.x == r.x2) && P.y >= r.y1 && P.y <= r.y2
                : (P.y == r.y1 || P.y == r.y2) && P.x >= r.x1 && P.x <= r.x2;
        };
        if (bt.rects.empty()) return check_rect(bt.orig_bbox) || check_rect(bt.bbox);
        for (const Rect& ri : bt.rects)
            if (check_rect(ri)) return true;
        return false;
    };

    // Rect-aware "does Pn land on ANY other block's face" — the spine-relay
    // guards use this before relocating a tap / clearing its busterm, so a
    // repositioned endpoint that would sit on another endpoint block's face is
    // left to chaining.  MUST inspect every blocks[bj].rects face (via on_face),
    // not just the union orig_bbox: a multi-rect/TEG block can be tapped on an
    // INTERIOR component face that lies inside its union bbox, which a
    // union-boundary check would miss — wrongly clearing that block's only
    // contact (Codex #461 P2).  Same predicate the landing scan / far_taps_block use.
    auto on_any_other_face = [&](const Point& Pn, int self_bi) -> bool {
        for (int bj = 0; bj < (int)blocks.size(); ++bj) {
            if (bj == self_bi) continue;
            if (on_face(Pn, true, blocks[bj]) || on_face(Pn, false, blocks[bj]))
                return true;
        }
        return false;
    };

    // Gather the distinct landing points on each block's face, tagging each with
    // the orientation of the segment whose endpoint lands there plus the
    // (segment, endpoint) it came from so we can later rewrite its busterm
    // annotation.  Snapshot the segment count first: we append connectors below.
    struct Inc { Point p; bool seg_horiz; int seg_idx; int ep; };
    int n_seg = (int)topo.segments.size();
    std::map<int, std::vector<Inc>> incident;     // block idx -> distinct landing POINTS (chaining)
    std::map<int, std::vector<Inc>> all_land;     // block idx -> EVERY landing endpoint (single-tap)
    auto add_incident = [&](int bi, const Point& P, bool seg_horiz, int seg_idx, int ep) {
        all_land[bi].push_back({P, seg_horiz, seg_idx, ep});
        auto& v = incident[bi];
        for (const Inc& q : v) if (q.p.x == P.x && q.p.y == P.y) return;  // distinct
        v.push_back({P, seg_horiz, seg_idx, ep});
    };
    for (int s = 0; s < n_seg; ++s) {
        const Segment& seg = topo.segments[s];
        bool horiz = (seg.start.y == seg.end.y);
        for (int e = 0; e < 2; ++e) {
            const Point& P = (e == 0) ? seg.start : seg.end;
            for (int bi = 0; bi < (int)blocks.size(); ++bi)
                if (on_face(P, horiz, blocks[bi])) { add_incident(bi, P, horiz, s, e); break; }
        }
    }

    // Connect two incident landings.  CRITICAL: each connector leg must meet its
    // incident segment PERPENDICULARLY (an L-corner / T-junction) -- a collinear
    // end-to-end join is not inferred by ConnTopology.  So the connector leaves
    // each landing in the direction perpendicular to that landing's segment
    // (leave horizontally off a vertical stub, and vice-versa).  This realizes
    // the "dogleg" (same-orientation landings) and "stretch & connect"
    // (orthogonal landings) shapes.
    // Emit a connector leg, skipping any degenerate zero-length segment (which
    // carries no wire and would only confuse downstream SEG-junction inference).
    auto emit = [&](int x1, int y1, int x2, int y2, int layer) {
        if (x1 == x2 && y1 == y2) return;
        topo.segments.push_back(make_seg(x1, y1, x2, y2, layer));
    };
    auto connect = [&](const Inc& a, const Inc& b) {
        if (a.p.x == b.p.x && a.p.y == b.p.y) return;
        bool leaveA_h = !a.seg_horiz;   // perpendicular to a's incident segment
        bool leaveB_h = !b.seg_horiz;
        if (leaveA_h && leaveB_h) {                 // both leave horizontally
            if (a.p.y == b.p.y) {                   // same row: a single H wire
                emit(a.p.x, a.p.y, b.p.x, b.p.y, h_layer);
            } else {                                // Z: H, V, H via an off-column
                int lo = std::min(a.p.x, b.p.x), hi = std::max(a.p.x, b.p.x);
                int mx = (hi - lo >= 2) ? (lo + hi) / 2 : hi + 2;  // not on a/b column
                emit(a.p.x, a.p.y, mx, a.p.y, h_layer);
                emit(mx, a.p.y, mx, b.p.y, v_layer);
                emit(mx, b.p.y, b.p.x, b.p.y, h_layer);
            }
        } else if (!leaveA_h && !leaveB_h) {        // both leave vertically
            if (a.p.x == b.p.x) {                   // same column: a single V wire
                emit(a.p.x, a.p.y, b.p.x, b.p.y, v_layer);
            } else {                                // Z: V, H, V via an off-row
                int lo = std::min(a.p.y, b.p.y), hi = std::max(a.p.y, b.p.y);
                int my = (hi - lo >= 2) ? (lo + hi) / 2 : hi + 2;
                emit(a.p.x, a.p.y, a.p.x, my, v_layer);
                emit(a.p.x, my, b.p.x, my, h_layer);
                emit(b.p.x, my, b.p.x, b.p.y, v_layer);
            }
        } else {                                    // orthogonal landings
            // The natural 2-leg L corner is (b.x,a.y) when A leaves H, else
            // (a.x,b.y).  When the two landings share that perpendicular line the
            // corner coincides with an endpoint: one leg vanishes and the other
            // runs collinear into an incident segment (no inferrable junction).
            // Detour through an off-line so BOTH ends meet perpendicularly (HVHV).
            if (leaveA_h) {                         // A leaves H, B leaves V
                if (a.p.y != b.p.y) {               // clean 2-leg L, corner (b.x,a.y)
                    emit(a.p.x, a.p.y, b.p.x, a.p.y, h_layer);
                    emit(b.p.x, a.p.y, b.p.x, b.p.y, v_layer);
                } else {                            // same row -> detour off-row
                    int lo = std::min(a.p.x, b.p.x), hi = std::max(a.p.x, b.p.x);
                    int xm = (hi - lo >= 2) ? (lo + hi) / 2
                                            : a.p.x + (a.p.x < b.p.x ? 1 : -1);
                    int ym = a.p.y - 2;
                    emit(a.p.x, a.p.y, xm, a.p.y, h_layer);
                    emit(xm, a.p.y, xm, ym, v_layer);
                    emit(xm, ym, b.p.x, ym, h_layer);
                    emit(b.p.x, ym, b.p.x, b.p.y, v_layer);
                }
            } else {                                // A leaves V, B leaves H
                if (a.p.x != b.p.x) {               // clean 2-leg L, corner (a.x,b.y)
                    emit(a.p.x, a.p.y, a.p.x, b.p.y, v_layer);
                    emit(a.p.x, b.p.y, b.p.x, b.p.y, h_layer);
                } else {                            // same column -> detour off-column
                    int lo = std::min(a.p.y, b.p.y), hi = std::max(a.p.y, b.p.y);
                    int ym = (hi - lo >= 2) ? (lo + hi) / 2
                                            : a.p.y + (a.p.y < b.p.y ? 1 : -1);
                    int xm = a.p.x - 2;
                    emit(a.p.x, a.p.y, a.p.x, ym, v_layer);
                    emit(a.p.x, ym, xm, ym, h_layer);
                    emit(xm, ym, xm, b.p.y, v_layer);
                    emit(xm, b.p.y, b.p.x, b.p.y, h_layer);
                }
            }
        }
    };

    // ── 2-stub relay: OTC pass-through extension ─────────────────────────────
    // A relay block touched by exactly two stubs is wired by EXTENDING the stubs
    // over the cell (OTC) so the block is COVERED by the crossing wires
    // (seg_spans_rect pass-through): no busterm tap, and no segment endpoint on
    // its face, so the FEEDTHRU check does not gather it.  Two shapes:
    //   • ORTHOGONAL (one H, one V): extend both to the corner (V's column, H's
    //     row); they meet there, no connector needed.
    //   • PARALLEL (both H or both V): extend both to a common jog line over the
    //     cell and add ONE perpendicular jog joining them.  Because both stubs
    //     now span the block, tighten_passthrough_ranges bounds the jog's slide
    //     to the cell extent, so NUTS keeps the two stubs flexible -- if their
    //     perpendicular slides overlap, the jog shrinks to zero and they merge
    //     into one straight wire through the block.
    std::set<int> otc_handled;
    std::set<int> to_erase;   // stubs merged away by the collinear-relay case below
    for (auto& [bi, pts] : incident) {
        if (pts.size() != 2 || all_land[bi].size() != 2) continue;  // clean 2-stub only
        const Inc& A = pts[0];
        const Inc& B = pts[1];
        // Guard: a landing point that also lies on ANOTHER block's boundary (an
        // adjacent / corner-touching block) may be that block's only pass-through
        // coverage; moving the endpoint inward could leave it with neither a
        // busterm nor a spanning segment.  Skip the OTC extension for such a relay
        // and let the general chaining (which keeps a tap) wire it instead.
        auto on_other_boundary = [&](const Point& P) {
            for (int bj = 0; bj < (int)blocks.size(); ++bj) {
                if (bj == bi) continue;
                const Rect& r = blocks[bj].orig_bbox;
                bool onx = (P.x == r.x1 || P.x == r.x2) && P.y >= r.y1 && P.y <= r.y2;
                bool ony = (P.y == r.y1 || P.y == r.y2) && P.x >= r.x1 && P.x <= r.x2;
                if (onx || ony) return true;
            }
            return false;
        };
        if (on_other_boundary(A.p) || on_other_boundary(B.p)) continue;
        bool handled = true;
        if (A.seg_horiz != B.seg_horiz) {
            // ORTHOGONAL: extend to the corner (V's column, H's row).
            const Inc& Vs = A.seg_horiz ? B : A;
            const Inc& Hs = A.seg_horiz ? A : B;
            int cx = Vs.p.x, cy = Hs.p.y;
            { Segment& s = topo.segments[Vs.seg_idx]; ((Vs.ep == 0) ? s.start : s.end).y = cy; }
            { Segment& s = topo.segments[Hs.seg_idx]; ((Hs.ep == 0) ? s.start : s.end).x = cx; }
        } else if (A.seg_horiz && A.p.y != B.p.y) {
            // PARALLEL H stubs (land on vertical faces): extend to a common
            // column over the cell and join their two rows with a V jog.
            int xj = (A.p.x + B.p.x) / 2;
            { Segment& s = topo.segments[A.seg_idx]; ((A.ep == 0) ? s.start : s.end).x = xj; }
            { Segment& s = topo.segments[B.seg_idx]; ((B.ep == 0) ? s.start : s.end).x = xj; }
            emit(xj, A.p.y, xj, B.p.y, v_layer);
        } else if (!A.seg_horiz && A.p.x != B.p.x) {
            // PARALLEL V stubs (land on horizontal faces): extend to a common
            // row over the cell and join their two columns with an H jog.
            int yj = (A.p.y + B.p.y) / 2;
            { Segment& s = topo.segments[A.seg_idx]; ((A.ep == 0) ? s.start : s.end).y = yj; }
            { Segment& s = topo.segments[B.seg_idx]; ((B.ep == 0) ? s.start : s.end).y = yj; }
            emit(A.p.x, yj, B.p.x, yj, h_layer);
        } else {
            // DEGENERATE COLLINEAR PARALLEL: both stubs share orientation AND
            // perpendicular coordinate -- they enter opposite faces of the block on
            // the SAME row (H stubs) or column (V stubs).  A perpendicular connector
            // between them would be zero-length, so the branches above punt here.
            // The block is spanned by ONE straight pass-through wire: MERGE the two
            // collinear stubs into a single segment (extend A across the block to B's
            // far endpoint, then drop B).  Two collinear segments cannot be
            // wire-joined by ConnTopology, so the old chaining fallback bridged them
            // with a trivial 2-unit jog that the planner offloaded to a zero-track
            // layer -- a guaranteed detailed-NUTS open (big.buda bundle 13).  Guard:
            // only when BOTH far endpoints are pure junctions (not block-face
            // landings), so no other block's coverage depends on the dropped stub;
            // otherwise leave it to the general chaining.
            Segment& sA = topo.segments[A.seg_idx];
            Segment& sB = topo.segments[B.seg_idx];
            // Only B's far endpoint is materialized: the merge extends A's
            // LANDING endpoint across the block to B_far, so A keeps its own far
            // endpoint (A_far) untouched and the merged wire spans [A_far .. B_far].
            // A_far therefore never needs to be read — the asymmetry is by design.
            const Point B_far = (B.ep == 0) ? sB.end : sB.start;
            const bool is_feedthru =
                std::find(topo.feedthru_blocks.begin(), topo.feedthru_blocks.end(),
                          blocks[bi].block_name) != topo.feedthru_blocks.end();
            // Rect-aware far-endpoint face test: mirror the landing collection
            // (on_face), which treats every Busterm::rects face as a real block
            // face.  on_other_boundary sees only orig_bbox, so for a multi-rect /
            // TEG block it would MISS a far endpoint tapping an interior rect --
            // and erasing B would then drop that block's only busterm annotation,
            // opening it.  Check both orientations across all other blocks' rects.
            auto far_taps_block = [&](const Point& P) -> bool {
                for (int bj = 0; bj < (int)blocks.size(); ++bj) {
                    if (bj == bi) continue;
                    if (on_face(P, true, blocks[bj]) || on_face(P, false, blocks[bj]))
                        return true;
                }
                return false;
            };
            if (is_feedthru) {
                // A declared feedthru MUST keep its two BUSTERM landings (the block
                // bridges the split via its own routing, not a straight crossing) --
                // leave this relay to the general chaining below.
                handled = false;
            } else {
                // Extend A's landing endpoint across the block to B's far endpoint so
                // A spans [A_far .. B_far] as one straight wire through the block.
                ((A.ep == 0) ? sA.start : sA.end) = B_far;
                to_erase.insert(B.seg_idx);
                // When a far endpoint taps another block (issue #57: the collinear
                // stubs are the trunk stub + an MST edge, whose far ends land on the
                // driver/next-receiver faces), dropping B would strand the tap B_far
                // carried -- the single-tap pass below assigns it to the about-to-be-
                // erased stub.  This USED TO force the refusal above (relay fell to
                // the general chaining, which offsets a 2-unit connector to make an
                // inferrable perpendicular junction -- the staircase jog).  Instead,
                // REPOINT every landing map entry that referenced B's erased far
                // endpoint onto A's surviving new endpoint (same point), so the tap
                // is assigned to the merged wire.  A_far is untouched (A is kept), so
                // only B's far endpoint needs repointing.  Runs before the tap /
                // chaining / erase passes, which consume the pre-erase indices.
                if (far_taps_block(B_far)) {
                    const int B_far_ep = (B.ep == 0) ? 1 : 0;
                    const int a_seg = A.seg_idx, a_ep = A.ep;
                    const int b_seg = B.seg_idx;
                    const Point tgt = B_far;
                    auto repoint = [&](std::map<int, std::vector<Inc>>& mp) {
                        for (auto& kv : mp)
                            for (Inc& q : kv.second)
                                if (q.seg_idx == b_seg && q.ep == B_far_ep &&
                                    q.p.x == tgt.x && q.p.y == tgt.y) {
                                    q.seg_idx = a_seg; q.ep = a_ep;
                                }
                    };
                    repoint(all_land);
                    repoint(incident);
                }
            }
        }
        if (!handled) continue;
        // Drop the block's busterm on both stubs: it is covered by the crossing
        // wires (a pass-through), not tapped at a face endpoint.
        for (const Inc& q : {A, B}) {
            auto& ep = topo.seg_busterms[q.seg_idx];
            ((q.ep == 0) ? ep.first : ep.second) = std::nullopt;
        }
        otc_handled.insert(bi);
    }

    // ── degree-≥3 STAR→SPINE relay (opt-in) ──────────────────────────────────
    // A high-degree relay whose incident stubs split as ≥2 PARALLEL (majority) +
    // exactly 1 PERPENDICULAR (minority) is wired by one collector SPINE instead
    // of a chain of bracket connectors.  The spine runs along the minority axis;
    // every majority stub T-taps it at its OWN perpendicular coordinate — the
    // parallels are never merged onto one shared track, so each keeps its full
    // independent slide (docs/internal/wishlist-topo.md).
    //
    // Hub coverage is GEOMETRIC (issue #514): the spine stops AT the outermost
    // tap (J-anchor) and the block is covered by the spine's own crossing of
    // the footprint — spine_perp strictly interior, the taps' along-coords on
    // the footprint — the same seg_spans_rect pass-through every
    // block-coverage check accepts, with the nominal perp inside the block
    // (the robust-cover gate's "normal cover" case).  No busterm tap is kept:
    // the earlier follow-up-E strategies (run the spine to the busterm-side
    // face, or extend the outermost stub across the block to its far face)
    // extended wire past the last junction purely to END on a face — a
    // tap-overhang antenna over the very block being tapped, now flagged by
    // detect_antennas.  Conservative: single-rect blocks, the clean
    // ≥2-parallel + 1-perpendicular split, spine line strictly interior; else
    // general chaining.
    std::set<int> spine_handled;
    if (spine_relays) {
        for (auto& [bi, pts] : incident) {
            if (otc_handled.count(bi)) continue;
            if (pts.size() < 3) continue;                       // degree ≥ 3 only
            if (all_land[bi].size() != pts.size()) continue;    // no coincident double-landings
            if (!blocks[bi].rects.empty()) continue;            // single-rect blocks only
            std::vector<const Inc*> P, M;                       // parallel / perpendicular groups
            for (const Inc& q : pts) (q.seg_horiz ? M : P).push_back(&q);

            // ── all-same-orientation (follow-up B) ───────────────────────────────
            // No perpendicular minority to serve as the spine — so ADD one: a new
            // collector segment perpendicular to the stubs, tapped by every stub.
            // This is the case the general chaining handles worst (a 3-segment Z
            // per stub pair).  The block is tapped by the COLLECTOR's own FACE
            // endpoint (a perpendicular busterm landing → bounded slide), so every
            // stub STOPS at the collector line with no perp overshoot past it — the
            // follow-up-E overstretch fix for the all-same case (an earlier scheme
            // spiked the outermost stub across the block to the FAR face, so that
            // one stub visibly overshot the collector).
            if (P.empty() != M.empty()) {                       // exactly one group empty
                const std::vector<const Inc*>& TAPS = P.empty() ? M : P;
                const Rect& bb = blocks[bi].orig_bbox;
                const bool spine_h = M.empty();                 // all-vertical taps → H spine
                const int  p_lo = spine_h ? bb.y1 : bb.x1;
                const int  p_hi = spine_h ? bb.y2 : bb.x2;
                auto along = [&](const Point& p) { return spine_h ? p.x : p.y; };
                auto perp  = [&](const Point& p) { return spine_h ? p.y : p.x; };
                auto mk    = [&](int a, int p)   { return spine_h ? Point{a, p} : Point{p, a}; };
                int t_min = INT_MAX, t_max = INT_MIN;
                for (const Inc* q : TAPS) { int a = along(q->p); t_min = std::min(t_min, a); t_max = std::max(t_max, a); }
                if (t_min == t_max) continue;                   // all collinear → let chaining merge
                // Do all stubs land on the SAME block face?  Then the collector can
                // ride THAT face at the taps' own line: the stubs already tap the
                // face (their MST landing), so no perp reposition and — crucially —
                // no along-overhang is needed to reach a face for coverage.  The
                // collector spans exactly [t_min..t_max]; block coverage is the
                // stubs' own geometric face contacts (a busterm tap kept on the
                // outermost stub).  When the taps straddle faces the collector
                // drops to an interior line instead — but keeps the SAME tight
                // [t_min..t_max] span, so neither branch needs the block's
                // along-extent faces (issue #514 retired that extension).
                const int face_perp = perp(TAPS[0]->p);
                bool common_face = (face_perp == p_lo || face_perp == p_hi);
                for (const Inc* q : TAPS) if (perp(q->p) != face_perp) { common_face = false; break; }
                int spine_perp, c_lo, c_hi;
                const Inc* face_stub = nullptr;                 // stub carrying the busterm tap
                if (common_face) {
                    spine_perp = face_perp;                     // collector rides the shared face
                    c_lo = t_min; c_hi = t_max;                 // tight — no overhang
                    for (const Inc* q : TAPS) if (along(q->p) == t_min) { face_stub = q; break; }
                } else {
                    spine_perp = (p_lo + p_hi) / 2;             // interior collector line
                    if (spine_perp <= p_lo || spine_perp >= p_hi) continue;   // degenerate block
                    // Tight span [t_min..t_max] — no face extension (issue
                    // #514).  The interior collector already covers the block
                    // by geometry (perp strictly interior, taps' alongs on the
                    // footprint), so the former minimal extension to the
                    // nearer face bought nothing and was exactly the
                    // tap-overhang shape detect_antennas now flags.
                    c_lo = t_min;
                    c_hi = t_max;
                }
                auto on_other_boundary = [&](const Point& Pn) { return on_any_other_face(Pn, bi); };
                bool safe = true;
                for (const Inc* q : TAPS)
                    if (on_other_boundary(q->p) || on_other_boundary(mk(along(q->p), spine_perp)))
                        { safe = false; break; }
                if (!safe) continue;                            // fallback to chaining
                // Commit: every stub taps the collector at its OWN along-coord and
                // stops there (independent slide, no overshoot).  Busterm: on the
                // common-face path the outermost stub keeps the tap (its endpoint is
                // on the face); on the interior path it moves to the collector's
                // face landing.  All others demote to SEG junctions.
                for (const Inc* q : TAPS) {
                    Segment& sq = topo.segments[q->seg_idx];
                    ((q->ep == 0) ? sq.start : sq.end) = mk(along(q->p), spine_perp);
                    auto& ep = topo.seg_busterms[q->seg_idx];
                    ((q->ep == 0) ? ep.first : ep.second)
                        = (q == face_stub) ? std::optional<Busterm>{blocks[bi]}
                                           : std::optional<Busterm>{};
                }
                // Add the collector as a NEW segment [c_lo..c_hi] @ spine_perp.
                // Block coverage is GEOMETRIC (verify's face/pass-through test on
                // placed extents), never annotation-driven: on the common-face path
                // the stubs land on the near face (and the outermost keeps an
                // explicit busterm above); on the interior path the collector's
                // strictly-interior line over [t_min..t_max] IS the footprint
                // crossing that covers the block (issue #514 — no face
                // extension anymore).  We deliberately do NOT tag the
                // collector: as an APPENDED segment its busterms are cleared
                // unconditionally below (the same rule that keeps OTC
                // connectors from being re-tagged where they graze a face), so
                // any tag here would be dead — the geometric coverage is what
                // matters, and check_topo/NUTS/DNUTS confirm the interior-path
                // hub stays covered (the straddled-face regression test).
                const Point cs = mk(c_lo, spine_perp), ce = mk(c_hi, spine_perp);
                topo.segments.push_back(make_seg(cs.x, cs.y, ce.x, ce.y, spine_h ? h_layer : v_layer));
                spine_handled.insert(bi);
                continue;
            }

            // ── 2-1 split ─ P = majority orientation (the taps), M = the 1 minority.
            if (P.size() >= 2 && M.size() == 1) { /* P vertical, M horizontal */ }
            else if (M.size() >= 2 && P.size() == 1) { std::swap(P, M); }
            else continue;                                      // 2-2 / other → fallback
            const Inc* Mi = M[0];
            const Rect& bb = blocks[bi].orig_bbox;
            const bool spine_h = Mi->seg_horiz;                 // spine along minority axis
            const int  spine_perp = spine_h ? Mi->p.y : Mi->p.x; // the shared tap line
            const int  p_lo = spine_h ? bb.y1 : bb.x1;          // block's perp extent
            const int  p_hi = spine_h ? bb.y2 : bb.x2;
            // The anchor must STRADDLE the spine line to both cover the block and
            // tap the spine, so the line has to be strictly interior in perp.
            if (spine_perp <= p_lo || spine_perp >= p_hi) continue;
            auto along = [&](const Point& p) { return spine_h ? p.x : p.y; };
            auto mk    = [&](int a, int p)   { return spine_h ? Point{a, p} : Point{p, a}; };
            Segment& sM = topo.segments[Mi->seg_idx];
            const Point M_far = (Mi->ep == 0) ? sM.end : sM.start;
            const int a_lo = spine_h ? bb.x1 : bb.y1;           // block's along extent
            const int a_hi = spine_h ? bb.x2 : bb.y2;
            int t_min = INT_MAX, t_max = INT_MIN;
            for (const Inc* q : P) { int a = along(q->p); t_min = std::min(t_min, a); t_max = std::max(t_max, a); }
            // Busterm-side extreme tap (side away from the neighbour M_far).
            const int bus_along = (along(M_far) >= t_max) ? t_min : t_max;
            // J-anchor (issue #514): stop the spine AT the outermost tap — no
            // face extension, no tap.  The trimmed spine still covers the hub
            // by geometry: spine_perp is strictly interior (gate above) and
            // bus_along lies within the block's along extent (every majority
            // tap is an MST landing ON a block face), so the spine's overlap
            // with the footprint is the same seg_spans_rect pass-through
            // coverage every block-coverage check accepts, with the nominal
            // perp inside the block (the robust-cover gate's own "normal
            // cover" case).  The former M-/P-anchor strategies extended wire
            // past the outermost junction purely to END on a face — a
            // tap-overhang antenna over the very block being tapped (the
            // detect_antennas rule now flags that shape); the extension bought
            // nothing coverage could not already have.
            if (bus_along < a_lo || bus_along > a_hi) continue;  // defensive
            const Point M_new = mk(bus_along, spine_perp);
            // A repositioned endpoint that also lies on ANOTHER block's face
            // could strand that block's coverage — leave such a relay to chaining
            // (guard both the ORIGINAL landing and the new one, per the OTC path;
            // on_any_other_face is rect-aware — see its definition for Codex #461 P2).
            auto on_other_boundary = [&](const Point& Pn) { return on_any_other_face(Pn, bi); };
            bool safe = !on_other_boundary(Mi->p) && !on_other_boundary(M_new);
            for (const Inc* q : P) {
                const Point np = mk(along(q->p), spine_perp);
                if (on_other_boundary(q->p) || on_other_boundary(np)) { safe = false; break; }
            }
            if (!safe) continue;                                // fallback to chaining
            // Commit.  M becomes the spine, trimmed to the outermost tap; every
            // majority stub taps it at its own along-coord (independent slide).
            ((Mi->ep == 0) ? sM.start : sM.end) = M_new;
            for (const Inc* q : P) {
                Segment& sq = topo.segments[q->seg_idx];
                ((q->ep == 0) ? sq.start : sq.end) = mk(along(q->p), spine_perp);
            }
            // Busterm: NONE — the hub is covered by the spine's geometric
            // crossing (like the OTC relay shapes); every landing becomes an
            // internal SEG junction.
            { auto& ep = topo.seg_busterms[Mi->seg_idx];
              ((Mi->ep == 0) ? ep.first : ep.second) = std::nullopt; }
            for (const Inc* q : P) {
                auto& ep = topo.seg_busterms[q->seg_idx];
                ((q->ep == 0) ? ep.first : ep.second) = std::nullopt;
            }
            spine_handled.insert(bi);
        }
    }

    for (auto& [bi, pts] : incident) {
        if (otc_handled.count(bi) || spine_handled.count(bi)) continue; // wired above
        if (pts.size() < 2) continue;        // leaf terminal: nothing to relay
        // Chain the landings (sorted) so all incident segments end up in one
        // wire-connected component through the block's junction.
        std::sort(pts.begin(), pts.end(), [](const Inc& a, const Inc& b) {
            return a.p.x != b.p.x ? a.p.x < b.p.x : a.p.y < b.p.y;
        });
        for (size_t k = 1; k < pts.size(); ++k)
            connect(pts[k - 1], pts[k]);
    }

    // De-overlap is handled after annotations are final (see end of function).

    // Single-tap model: keep the busterm tap on EXACTLY ONE landing per block and
    // demote every other landing to an internal (SEG) junction.  Without this,
    // every landing stays annotated as a busterm on the block, so ConnTopology
    // infers a BUSTERM connection at each and SKIPS the SEG junction between the
    // relay connectors and the stubs -- the wire we just added is invisible
    // downstream and NUTS could slide the pieces apart, silently re-opening the
    // through-block feedthrough.  Demoting the extras to nullopt forces
    // infer_connections to wire them as real SEG junctions.
    //
    // Iterate over ALL landing endpoints, not just distinct points: when two
    // segments share one landing point (e.g. a kept trunk stub and an MST edge
    // both leaving a block's corner in a trunk+MST hybrid) both would otherwise
    // keep the busterm tag -- a double tap on the same block.
    //
    // Pick the tap on the stub with the most slide flexibility: a stub on a
    // vertical (x) face slides in y (flexibility = the block's y-extent); on a
    // horizontal (y) face it slides in x (the x-extent).  The connectors run
    // along the block faces, so tapping a stub (which slides ALONG its face)
    // keeps the single anchor cleanly on the block.
    for (auto& [bi, lands] : all_land) {
        if (otc_handled.count(bi)) continue; // no tap: covered by the OTC crossing
        if (spine_handled.count(bi)) continue; // tap set on the spine's far-face landing
        const Rect& bb = blocks[bi].orig_bbox;
        auto flex = [&](const Inc& q) {
            return q.seg_horiz ? (bb.y2 - bb.y1) : (bb.x2 - bb.x1);
        };
        size_t best = 0;
        for (size_t k = 1; k < lands.size(); ++k)
            if (flex(lands[k]) > flex(lands[best])) best = k;
        for (size_t k = 0; k < lands.size(); ++k) {
            auto& ep   = topo.seg_busterms[lands[k].seg_idx];
            auto& slot = (lands[k].ep == 0) ? ep.first : ep.second;
            if (k == best) slot = blocks[bi];    // the single busterm tap
            else           slot = std::nullopt;  // demote to an internal SEG junction
        }
    }

    // Erase stubs merged away by the degenerate-collinear relay case above.
    // Deferred to here so the incident/all_land maps (keyed by the ORIGINAL segment
    // indices) stayed valid through chaining and tap assignment.  Erase descending
    // so lower indices remain stable; erase_segment reindexes seg_busterms/seg_conns.
    // Decrement n_seg per erased ORIGINAL so the connector boundary below (segments
    // >= n_seg are appended connectors) stays correct after the shift.
    for (auto it = to_erase.rbegin(); it != to_erase.rend(); ++it) {
        erase_segment(topo, *it);
        if (*it < n_seg) --n_seg;
    }

    // Every connector segment appended above is internal wire: annotate both its
    // endpoints as nullopt so infer_connections treats them as SEG junctions (and
    // does not fall back to the geometric busterm search, which would re-tag the
    // connector endpoints that happen to run along a block face).
    for (int s = n_seg; s < (int)topo.segments.size(); ++s) {
        auto& ep = topo.seg_busterms[s];
        ep.first  = std::nullopt;
        ep.second = std::nullopt;
    }

    // De-overlap connectors.  At a high-degree relay the landing-chaining can lay
    // one connector's leg collinear on top of another's, a redundant parallel wire
    // that closes a cycle (the PLUS centre block).  Such a connector is usually
    // droppable -- but NOT always: if it is the only inferable SEG link to a
    // busterm-annotated edge endpoint (ConnTopology suppresses SEG inference at a
    // busterm-tagged endpoint, and does not infer collinear overlaps), dropping it
    // disconnects the topology.  So we remove a collinear-contained connector only
    // after VERIFYING the result stays one connected component, one connector at a
    // time.  Gated on an overlap actually existing (the common case does nothing).
    if (has_collinear_overlap(topo, n_seg)) {
        auto covers = [](const Segment& o, const Segment& c) {
            bool o_h = o.start.y == o.end.y, c_h = c.start.y == c.end.y;
            if (o_h != c_h) return false;
            if (c_h) {
                if (o.start.y != c.start.y) return false;
                int olo = std::min(o.start.x, o.end.x), ohi = std::max(o.start.x, o.end.x);
                int clo = std::min(c.start.x, c.end.x), chi = std::max(c.start.x, c.end.x);
                return olo <= clo && chi <= ohi;
            }
            if (o.start.x != c.start.x) return false;
            int olo = std::min(o.start.y, o.end.y), ohi = std::max(o.start.y, o.end.y);
            int clo = std::min(c.start.y, c.end.y), chi = std::max(c.start.y, c.end.y);
            return olo <= clo && chi <= ohi;
        };
        bool changed = true;
        while (changed) {
            changed = false;
            int n = (int)topo.segments.size();
            for (int c = n_seg; c < n; ++c) {
                bool contained = false;
                for (int o = 0; o < n; ++o)
                    if (o != c && covers(topo.segments[o], topo.segments[c])) { contained = true; break; }
                if (!contained) continue;
                Topology trial = topo;
                erase_segment(trial, c);
                if (conn_seg_components(trial, fp) <= conn_seg_components(topo, fp)) {
                    topo = std::move(trial);   // removal kept connectivity -> commit
                    changed = true;
                    break;
                }
            }
        }
    }

    // Redundant ANTENNA drop (issue #482).  The de-overlap pass above only
    // considers the connectors IT appended (index >= n_seg), but the hybrid
    // construction can lay an ORIGINAL segment redundantly too: an MST edge
    // leg leaving the same block face as that block's trunk stub is collinear
    // with — and contained in — the stub.  The single-tap model then demotes
    // the leg's landing to a nullopt junction, and ConnTopology never infers a
    // junction between COLLINEAR segments, so the leg's near end connects to
    // nothing: a dangling wire (verify's ANTENNA) that no route needs, and one
    // whose free slide window later drags NUTS around.  Drop such a segment
    // when ALL of:
    //   (a) it is attached at fewer than two points (the antenna predicate —
    //       a segment carrying real connectivity is never touched here),
    //   (b) neither endpoint is busterm-annotated, so no block tap is lost,
    //   (c) it is collinear-CONTAINED in another segment, so every block its
    //       geometry covered (pass-through included) stays covered, and
    //   (d) erasing it does not increase the component count.
    // One at a time with re-derivation, like the pass above: erase_segment
    // renumbers, and dropping one antenna can only ever reduce the next one's
    // conn count (never create connectivity), so the loop converges.
    {
        auto contained_in_other = [](const Topology& t, int c) {
            const Segment& cs = t.segments[c];
            const bool c_h = cs.start.y == cs.end.y;
            const int clo = c_h ? std::min(cs.start.x, cs.end.x)
                                : std::min(cs.start.y, cs.end.y);
            const int chi = c_h ? std::max(cs.start.x, cs.end.x)
                                : std::max(cs.start.y, cs.end.y);
            for (int o = 0; o < (int)t.segments.size(); ++o) {
                if (o == c) continue;
                const Segment& os = t.segments[o];
                const bool o_h = os.start.y == os.end.y;
                if (o_h != c_h) continue;
                if (c_h ? (os.start.y != cs.start.y)
                        : (os.start.x != cs.start.x)) continue;
                const int olo = o_h ? std::min(os.start.x, os.end.x)
                                    : std::min(os.start.y, os.end.y);
                const int ohi = o_h ? std::max(os.start.x, os.end.x)
                                    : std::max(os.start.y, os.end.y);
                if (olo <= clo && chi <= ohi) return true;
            }
            return false;
        };
        bool changed = true;
        while (changed) {
            changed = false;
            const std::vector<size_t> counts = conn_counts(topo, fp);
            const int n = (int)topo.segments.size();
            if ((int)counts.size() != n) break;      // defensive: shape drift
            for (int c = 0; c < n; ++c) {
                if (counts[c] >= 2) continue;                        // (a)
                auto bt = topo.seg_busterms.find(c);
                if (bt != topo.seg_busterms.end() &&
                    (bt->second.first || bt->second.second)) continue; // (b)
                if (!contained_in_other(topo, c)) continue;          // (c)
                Topology trial = topo;
                erase_segment(trial, c);
                if (conn_seg_components(trial, fp)
                        > conn_seg_components(topo, fp)) continue;   // (d)
                topo = std::move(trial);
                changed = true;
                break;
            }
        }
    }
}


// ---------------------------------------------------------------------------
// Multicast helpers
// ---------------------------------------------------------------------------

// Find points P1 in r1, P2 in r2 that minimize Manhattan distance.
static void closest_points(const Rect& r1, const Rect& r2, Point& p1, Point& p2) {
    if (r1.x2 < r2.x1) { p1.x = r1.x2; p2.x = r2.x1; }
    else if (r2.x2 < r1.x1) { p1.x = r1.x1; p2.x = r2.x2; }
    else { p1.x = p2.x = (std::max(r1.x1, r2.x1) + std::min(r1.x2, r2.x2)) / 2; }

    if (r1.y2 < r2.y1) { p1.y = r1.y2; p2.y = r2.y1; }
    else if (r2.y2 < r1.y1) { p1.y = r1.y1; p2.y = r2.y2; }
    else { p1.y = p2.y = (std::max(r1.y1, r2.y1) + std::min(r1.y2, r2.y2)) / 2; }
}

// When r1 and r2 ABUT — touch on exactly one axis (a shared edge) with positive
// overlap on the other — closest_points collapses that shared edge to a single
// point (p1 == p2), and the MST edge realizers below drop the resulting
// zero-length "edge", silently disconnecting the abutted block (its bits then go
// unplaced in DetailedNUTS).  Realize the abutment as a real wire lying ON the
// shared boundary and spanning the overlap interval, so it lands on both block
// faces and keeps the MST tree connected.  Returns false when the rects do not
// share an edge (disjoint, corner-only touch, or fully coincident).
static bool shared_edge_segment(const Rect& r1, const Rect& r2,
                                int h_layer, int v_layer, Segment& out) {
    const int ox_lo = std::max(r1.x1, r2.x1), ox_hi = std::min(r1.x2, r2.x2);
    const int oy_lo = std::max(r1.y1, r2.y1), oy_hi = std::min(r1.y2, r2.y2);
    // Shared vertical edge (touch on x): CROSS it with a horizontal wire at the
    // centre of the common y-span; track axis = y, slide = [oy_lo, oy_hi].
    if ((r1.x2 == r2.x1 || r2.x2 == r1.x1) && oy_hi > oy_lo) {
        const int y0 = (oy_lo + oy_hi) / 2;
        out = make_seg(std::min(r1.x1,r2.x1), y0, std::max(r1.x2,r2.x2), y0, h_layer);
        return true;
    }
    // Shared horizontal edge (touch on y): CROSS it with a vertical wire at the
    // centre of the common x-span; track axis = x, slide = [ox_lo, ox_hi].
    if ((r1.y2 == r2.y1 || r2.y2 == r1.y1) && ox_hi > ox_lo) {
        const int x0 = (ox_lo + ox_hi) / 2;
        out = make_seg(x0, std::min(r1.y1,r2.y1), x0, std::max(r1.y2,r2.y2), v_layer);
        return true;
    }
    return false;
}

// Two blocks can be CORNER-DIAGONAL: their facing projections meet at only a
// single point (e.g. blk_09.x2 == blk_39.x1 with no y-overlap).  closest_points
// then returns a straight edge pinned to that single coordinate — zero
// perpendicular slide — and filter_pinched drops any candidate containing it.
// Realize such an edge as an L-shape AROUND the corner so each leg taps a real
// face with room to slide.  There are exactly two L's; the MST_HV / MST_VH
// strategies select between them (H-first vs V-first), so the congestion planner
// picks whichever fits the rest of the topology.  Legs run face-centre to
// face-centre (maximising track room).
static void corner_diagonal_L(const Rect& u, const Rect& v, int strategy,
                              int h_layer, int v_layer, std::vector<Segment>& out) {
    const bool u_left  = (u.x2 <= v.x1);          // u is left of v (else right)
    const bool u_below = (u.y2 <= v.y1);          // u is below v (else above)
    const int ucx = (u.x1 + u.x2) / 2, ucy = (u.y1 + u.y2) / 2;
    const int vcx = (v.x1 + v.x2) / 2, vcy = (v.y1 + v.y2) / 2;
    const int u_hface = u_left  ? u.x2 : u.x1;    // u's vertical face toward v
    const int u_vface = u_below ? u.y2 : u.y1;    // u's horizontal face toward v
    const int v_hface = u_left  ? v.x1 : v.x2;    // v's vertical face toward u
    const int v_vface = u_below ? v.y1 : v.y2;    // v's horizontal face toward u
    if (strategy == 1) {                          // V-then-H: up/down off u, then across to v
        out.push_back(make_seg(ucx, u_vface, ucx, vcy, v_layer));
        out.push_back(make_seg(ucx, vcy, v_hface, vcy, h_layer));
    } else {                                       // H-then-V: across off u, then up/down to v
        out.push_back(make_seg(u_hface, ucy, vcx, ucy, h_layer));
        out.push_back(make_seg(vcx, ucy, vcx, v_vface, v_layer));
    }
}

// Axis-parameterized trunk generator.  add_trunk_v was a strict superset of
// add_trunk_h (it threads a stub_suppressed vector through the span gather,
// touches-block pullback, degenerate-spine block and double_detour that the H
// path lacked); this unifies both by adopting the V structure and gating the
// suppression PASS on `suppress_stubs`.  With suppress_stubs=false (the H
// forwarder) stub_suppressed stays all-false, so every V-only guard goes inert
// and the H output is reproduced byte-for-byte.
//   axis.along_horiz==true  → H spine (runs along x); stub is V (v_layer_).
//   axis.along_horiz==false → V spine (runs along y); stub is H (h_layer_).
void TopologyGenerator::add_trunk(const Axis& axis, bool suppress_stubs,
                                   const std::vector<Point>& pins,
                                   const std::vector<Busterm>& blocks,
                                   int locus, bool out_of_bbox,
                                   std::vector<Topology>& results)
{
    int n = (int)pins.size();
    const int spine_layer = axis.along_horiz ? h_layer_ : v_layer_;
    const int stub_layer  = axis.along_horiz ? v_layer_ : h_layer_;
    std::vector<int>  conn(n), att(n);
    std::vector<bool> has_stub(n);
    std::vector<Rect> best_r(n);   // best rect per block for this trunk locus
    for (int i = 0; i < n; ++i) {
        best_r[i]   = best_rect(axis, blocks[i], locus);
        conn[i]     = use_busterm_ ? axis.perp_face(best_r[i], locus) : axis.perp(pins[i]);
        has_stub[i] = (conn[i] != locus);
        // For multi-rect blocks use the best rect's centre; single-rect uses pin.
        att[i]      = blocks[i].rects.empty() ? axis.along(pins[i]) : axis.along_center(best_r[i]);
    }
    std::vector<bool> stub_suppressed(n, false);

    if (use_busterm_) {
        // Enforce stub length for multicast stubs (stub is perpendicular to spine).
        int m = floorplan_.get_min_stub_length(axis.along_horiz ? 1 /*VERTICAL*/ : 0 /*HORIZONTAL*/,
                                                stub_layer);
        for (int i = 0; i < n; ++i) {
            if (has_stub[i]) {
                if (std::abs(locus - conn[i]) < m) return; // skip this trunk
            }
        }

        if (!out_of_bbox) {
            int pt_lo = INT_MIN / 2, pt_hi = INT_MAX / 2;
            bool any_pt = false;
            bool trunk_inside_direct = false;
            for (int i = 0; i < n; ++i) {
                if (!has_stub[i]) {
                    any_pt = true;
                    if (locus >= axis.perp_lo(best_r[i]) && locus <= axis.perp_hi(best_r[i]))
                        trunk_inside_direct = true;
                    pt_lo = std::max(pt_lo, axis.perp_lo(best_r[i]));
                    pt_hi = std::min(pt_hi, axis.perp_hi(best_r[i]));
                }
            }
            if (any_pt && pt_lo <= pt_hi && !trunk_inside_direct) {
                int n_hi = 0, n_lo = 0;
                for (int i = 0; i < n; ++i) {
                    if (has_stub[i]) {
                        if (conn[i] > locus) ++n_hi;
                        else                 ++n_lo;
                    }
                }
                if      (n_hi > 0 && n_lo == 0) locus = pt_hi;
                else if (n_lo > 0 && n_hi == 0) locus = pt_lo;
                for (int i = 0; i < n; ++i) {
                    best_r[i]  = best_rect(axis, blocks[i], locus);
                    conn[i]    = axis.perp_face(best_r[i], locus);
                    has_stub[i] = (conn[i] != locus);
                }
            }
        }
        // Helper: far/near along-face of best rect (or shrunk union for single-rect w/ margin).
        auto along_hi_of = [&](int i) { return blocks[i].rects.empty() ? axis.along_hi(blocks[i].orig_bbox) : axis.along_hi(best_r[i]); };
        auto along_lo_of = [&](int i) { return blocks[i].rects.empty() ? axis.along_lo(blocks[i].orig_bbox) : axis.along_lo(best_r[i]); };
        auto along_hi_shrunk = [&](int i) { return blocks[i].rects.empty() ? axis.along_hi(blocks[i].bbox) : axis.along_hi(best_r[i]); };
        auto along_lo_shrunk = [&](int i) { return blocks[i].rects.empty() ? axis.along_lo(blocks[i].bbox) : axis.along_lo(best_r[i]); };
        {
            int lo = std::min_element(att.begin(), att.end()) - att.begin();
            int hi = std::max_element(att.begin(), att.end()) - att.begin();
            if (!has_stub[lo]) att[lo] = along_hi_of(lo);
            if (!has_stub[hi]) att[hi] = along_lo_of(hi);
        }
        for (int iter = 0; iter < n; ++iter) {
            int lo = std::min_element(att.begin(), att.end()) - att.begin();
            int hi = std::max_element(att.begin(), att.end()) - att.begin();
            bool changed = false;
            if (has_stub[lo]) {
                int target = along_hi_shrunk(lo);
                if (target > att[lo]) { att[lo] = target; changed = true; }
            }
            if (has_stub[hi]) {
                int target = along_lo_shrunk(hi);
                if (target < att[hi]) { att[hi] = target; changed = true; }
            }
            if (!changed) break;
        }
        for (int iter2 = 0; iter2 < n; ++iter2) {
            int lo = std::min_element(att.begin(), att.end()) - att.begin();
            int hi = std::max_element(att.begin(), att.end()) - att.begin();
            bool changed = false;
            if (!has_stub[lo] && att[lo] != along_hi_of(lo)) {
                att[lo] = along_hi_of(lo); changed = true;
            }
            if (!has_stub[hi] && att[hi] != along_lo_of(hi)) {
                att[hi] = along_lo_of(hi); changed = true;
            }
            if (!changed) break;
        }

        // Suppress stubs made redundant by a longer same-side stub whose att
        // already passes through the shorter stub's block.  V-only pass, gated on
        // suppress_stubs (the H forwarder passes false, keeping stub_suppressed
        // all-false so every downstream guard reduces to the H behaviour).
        //
        // Coverage-safety: a stub i may only be suppressed by a stub j that
        // ACTUALLY SURVIVES (is not itself suppressed) — otherwise i is left with
        // no covering wire (a silent open).  The original loop tested has_stub[j]
        // only, so a chain A←B←C could suppress B believing C covers it while C's
        // surviving wire sits at a different att that misses B (the
        // big2 bus_056 / blk_09 bug).  Decide survivors farthest-first per side:
        // the farthest stub always survives, and a nearer stub is suppressed only
        // when an already-confirmed survivor's att lies within its block's
        // along-extent (the same pass-through coverage verify.cpp checks).
        if (suppress_stubs) {
            std::vector<int> order(n);
            for (int i = 0; i < n; ++i) order[i] = i;
            // Farthest from trunk first; stubs only.
            std::sort(order.begin(), order.end(), [&](int a, int b) {
                return std::abs(conn[a] - locus) > std::abs(conn[b] - locus);
            });
            for (int idx = 0; idx < n; ++idx) {
                int i = order[idx];
                if (!has_stub[i]) continue;
                int di = conn[i] - locus;
                if (di == 0) continue;
                for (int jdx = 0; jdx < idx; ++jdx) {   // only farther-or-equal blocks seen so far
                    int j = order[jdx];
                    if (!has_stub[j] || stub_suppressed[j]) continue;  // j must survive
                    int dj = conn[j] - locus;
                    if (dj == 0) continue;
                    if ((di > 0) != (dj > 0)) continue;          // opposite sides of trunk
                    if (std::abs(dj) <= std::abs(di)) continue;  // j not strictly farther
                    // Surviving stub j's att lies within block i's original along-extent?
                    if (att[j] >= axis.along_lo(blocks[i].orig_bbox) &&
                        att[j] <= axis.along_hi(blocks[i].orig_bbox)) {
                        stub_suppressed[i] = true; break;
                    }
                }
            }
        }
    }

    // Trunk-touches-block, no artificial far-edge tap (issue #84).  A no-stub
    // endpoint block the trunk passes through is touched by the trunk; whether the
    // spine must extend to one of its faces to *tap* it is decided by the conn-seg
    // (stub) span, not by feedthru:
    //   - the STUB span already OVERLAPS the block (straddle) → the trunk touches
    //     it by overlap; pull the face-pushed att back into the stub span so the
    //     spine is NOT extended to manufacture an edge tap (b34_bus_028 blk_00).
    //   - the stub span is OFF the block (one-directional pull) → the overlap test
    //     below is false, so att keeps its near-face value and the spine extends
    //     to that NEAR edge and taps it (pull-up/pull-down).
    // Busterm mode only: with busterm mode off (center_mode) att is the point
    // pin's along-coord, and clamping a pin that lies outside the stub span into
    // it would drop the pin connection (Codex P2 on #88).
    if (use_busterm_) {
        int stub_lo = INT_MAX, stub_hi = INT_MIN;
        for (int i = 0; i < n; ++i)
            if (has_stub[i] && !stub_suppressed[i]) {
                stub_lo = std::min(stub_lo, att[i]);
                stub_hi = std::max(stub_hi, att[i]);
            }
        if (stub_lo <= stub_hi) {
            for (int i = 0; i < n; ++i) {
                if (has_stub[i] || stub_suppressed[i]) continue;   // stubbed/suppressed
                if (!blocks[i].rects.empty()) continue;            // single-rect only (TEG owns multi-rect)
                int b1 = axis.along_lo(blocks[i].orig_bbox), b2 = axis.along_hi(blocks[i].orig_bbox);
                if (b1 <= stub_hi && b2 >= stub_lo)                // stub span overlaps block
                    att[i] = std::clamp(att[i], stub_lo, stub_hi);
            }
        }
    }

    int a_lo = INT_MAX, a_hi = INT_MIN;
    for (int i = 0; i < n; ++i) {
        if (stub_suppressed[i]) continue;
        a_lo = std::min(a_lo, att[i]); a_hi = std::max(a_hi, att[i]);
    }

    // ── Flexible "root" trunk under double_detour ───────────────────────────
    // A trunk is the bundle's root: its endpoints are flexible and span exactly
    // from the lowest busterm it taps to the topmost stub/connection it carries —
    // no more (minimise wirelength) and no less (stay connected).  Opt-in via
    // double_detour.  Each stub sits at its NATURAL centerline (block/rect
    // centre), and the span = [min,max] of those centerlines extended to the NEAR
    // along-face of every pass-through block it does not yet overlap.  Without
    // double_detour the span is left tight (unchanged behaviour).
    if (use_busterm_ && allow_double_detour_) {
        auto ctr = [&](int i) {
            return blocks[i].rects.empty() ? axis.along_center(blocks[i].orig_bbox)
                                           : axis.along_center(best_r[i]);
        };
        // Place every surviving stub at its natural centerline.
        for (int i = 0; i < n; ++i)
            if (has_stub[i] && !stub_suppressed[i]) att[i] = ctr(i);
        // Recentering can move a farther same-side stub off a nearer block it was
        // suppressing; a suppressed block is stubbed — the trunk does NOT pass
        // through it — so if no SURVIVING farther same-side stub still crosses it
        // at the new att, un-suppress it and emit its own centerline stub.
        // Monotonic: only ever re-adds coverage, so it cannot strand a block.
        for (int i = 0; i < n; ++i) {
            if (!stub_suppressed[i]) continue;
            int di = conn[i] - locus;
            bool covered = false;
            for (int j = 0; j < n && !covered; ++j) {
                if (j == i || !has_stub[j] || stub_suppressed[j]) continue;
                int dj = conn[j] - locus;
                if (di == 0 || dj == 0 || (di > 0) != (dj > 0)) continue; // same side
                if (std::abs(dj) <= std::abs(di)) continue;               // strictly farther
                covered = (att[j] >= axis.along_lo(blocks[i].orig_bbox) &&
                           att[j] <= axis.along_hi(blocks[i].orig_bbox));
            }
            if (!covered) { stub_suppressed[i] = false; att[i] = ctr(i); }
        }
        int lo = INT_MAX, hi = INT_MIN;
        for (int i = 0; i < n; ++i) {
            if (stub_suppressed[i] || !has_stub[i]) continue;  // surviving stubs
            lo = std::min(lo, att[i]); hi = std::max(hi, att[i]);
        }
        bool seeded = (lo <= hi);
        for (int i = 0; i < n; ++i) {
            if (stub_suppressed[i] || has_stub[i]) continue;   // pass-through/contained
            int b1 = axis.along_lo(blocks[i].orig_bbox), b2 = axis.along_hi(blocks[i].orig_bbox);
            if (!seeded) { lo = b1; hi = b2; seeded = true; continue; }
            if (b1 > hi) hi = b1;   // block beyond the span: reach its near (lo) face
            if (b2 < lo) lo = b2;   // block below the span: reach its near (hi) face
        }
        if (seeded && lo < hi) { a_lo = lo; a_hi = hi; }
    }
    // Degenerate spine (all real attachments share one along == the junction).  A
    // spine-less topology would leave same-side collinear stubs joined only by
    // nominally overlapping at the junction — ConnTopology infers a SEG link only
    // for *perpendicular* pairs, so two parallel stubs carry no junction constraint
    // and NUTS could place them on different tracks → a silent open (issue #84,
    // Codex P1).  Instead, when a contained endpoint block straddles the junction,
    // SPREAD the stubs to opposite interior sides of it and span the spine between
    // them.  Otherwise drop.
    if (a_lo >= a_hi) {
        const int junction = a_lo;        // == a_hi
        int n_stubs = 0, side = 0;
        bool same_side = true, ok = true, have_B = false;
        int B_lo = INT_MIN, B_hi = INT_MAX;   // intersection of contained blocks' extents
        for (int i = 0; i < n; ++i) {
            if (has_stub[i] && !stub_suppressed[i]) {
                ++n_stubs;
                int s = (conn[i] > locus) ? 1 : (conn[i] < locus ? -1 : 0);
                if (s != 0) { if (side == 0) side = s; else if (s != side) same_side = false; }
                continue;
            }
            if (has_stub[i]) continue;                 // suppressed stub: covered by a survivor
            // no-stub (contained) block: single-rect, must straddle the junction.
            const Rect& bb = blocks[i].orig_bbox;
            if (!blocks[i].rects.empty() || !(axis.along_lo(bb) <= junction && junction <= axis.along_hi(bb))) {
                ok = false; continue;
            }
            have_B = true;
            B_lo = std::max(B_lo, axis.along_lo(bb));
            B_hi = std::min(B_hi, axis.along_hi(bb));
        }
        if (!(n_stubs >= 2 && same_side && ok && have_B &&
              B_lo < junction && junction < B_hi))
            return;
        int new_lo = INT_MAX, new_hi = INT_MIN;
        for (int i = 0; i < n; ++i) {
            if (!(has_stub[i] && !stub_suppressed[i])) continue;
            // Spread to the midpoint of the stub's OWN face ∩ B's interior — never
            // computed from B's faces alone, which (when B is much larger than the
            // stub block) could place the stub off its own face → an off-face
            // BUSTERM (Codex P1).  The intersection naturally lands on the block's
            // side of the junction and stays strictly inside B (no face tap).
            int f1 = blocks[i].rects.empty() ? axis.along_lo(blocks[i].orig_bbox) : axis.along_lo(best_r[i]);
            int f2 = blocks[i].rects.empty() ? axis.along_hi(blocks[i].orig_bbox) : axis.along_hi(best_r[i]);
            int lo = std::max(f1, B_lo), hi = std::min(f2, B_hi);
            if (lo < hi) att[i] = (lo + hi) / 2;     // else keep att (the junction)
            new_lo = std::min(new_lo, att[i]);
            new_hi = std::max(new_hi, att[i]);
        }
        if (new_lo >= new_hi) return;                  // stubs did not spread (no interior room)
        a_lo = new_lo; a_hi = new_hi;                  // spine spans between the spread stubs
    }

    // TEG-over gap stubs are emitted at their rects' CENTRES (a_near/a_far in the
    // emission loop below), not at att[i] — but the spine span [a_lo, a_hi] is
    // computed from att[], which the extreme-attachment pull may have shortened to
    // the block's near along-face.  When such a block is the spine's extreme
    // block, the trunk then stops SHORT of the gap-stub pair and the pair floats
    // off the trunk as a genuinely disconnected island (check_topo DISCONNECTED;
    // the generation coverage gate would drop the candidate, losing the only
    // bridged TEG-over option).  Extend the spine span to reach every gap-stub
    // position, mirroring the emission's best_near/best_far selection exactly.
    for (int i = 0; i < n; ++i) {
        if (stub_suppressed[i] || !has_stub[i]) continue;
        if (blocks[i].teg_mode != TegMode::OVER || blocks[i].rects.size() < 2) continue;
        const auto& rects = blocks[i].rects;
        bool trunk_inside_any = false;
        for (const auto& r : rects)
            if (locus >= axis.perp_lo(r) && locus <= axis.perp_hi(r)) { trunk_inside_any = true; break; }
        if (trunk_inside_any) continue;
        Rect best_near = rects[0]; bool has_near = false;
        Rect best_far  = rects[0]; bool has_far  = false;
        for (const auto& r : rects) {
            if (axis.perp_hi(r) <= locus) {
                if (!has_near || axis.perp_hi(r) > axis.perp_hi(best_near)) { best_near = r; has_near = true; }
            } else if (axis.perp_lo(r) >= locus) {
                if (!has_far || axis.perp_lo(r) < axis.perp_lo(best_far)) { best_far = r; has_far = true; }
            }
        }
        if (!has_near || !has_far) continue;           // falls back to the normal att[i] stub
        for (int a : {axis.along_center(best_near), axis.along_center(best_far)}) {
            a_lo = std::min(a_lo, a);
            a_hi = std::max(a_hi, a);
        }
    }

    Topology t;
    std::string letter = axis.along_horiz ? "H" : "V";
    t.type               = std::string("TRUNK_") + letter + (out_of_bbox ? "_OOB" : "")
                           + (axis.along_horiz ? "@y" : "@x") + std::to_string(locus);
    t.trunk_location     = locus;

    // Opt-in feedthru: a bundle block the trunk passes straight through (a busterm
    // of THIS bundle with no stub) may relay the bus across its interior; split the
    // trunk at the block's along-faces.  Only blocks this topology connects to are
    // eligible.  Single-rect MVP.  Gated on feedthru_active() (default off).
    std::vector<std::pair<int,int>> ft_gaps;   // (lo,hi) along-faces of feedthru blocks
    std::vector<bool> is_feedthru(n, false);
    if (floorplan_.feedthru_active()) {
        for (int i = 0; i < n; ++i) {
            if (has_stub[i] || stub_suppressed[i]) continue;  // not passed through
            if (!floorplan_.get_feedthru(blocks[i].block_name, spine_layer)) continue;
            if (blocks[i].rects.size() > 1) continue;         // MVP: single-rect only
            // A feedthru relay needs the trunk to PASS THROUGH the block (enter one
            // face, exit the other).  A spine fully contained in the block (the
            // bounded interior spine, #84) does not pass through, so it must not be
            // split out — that would delete the only junction (Codex P1).
            if (a_lo >= axis.along_lo(blocks[i].orig_bbox) && a_hi <= axis.along_hi(blocks[i].orig_bbox))
                continue;
            int f1 = std::max(a_lo, axis.along_lo(blocks[i].orig_bbox));
            int f2 = std::min(a_hi, axis.along_hi(blocks[i].orig_bbox));
            if (f1 < f2) {
                ft_gaps.push_back({f1, f2});
                is_feedthru[i] = true;
                t.feedthru_blocks.push_back(blocks[i].block_name);
            }
        }
        std::sort(t.feedthru_blocks.begin(), t.feedthru_blocks.end());
    }

    // Pass-through count excludes feedthru blocks (those are explicit splits).
    t.pass_through_count = 0;
    for (int i = 0; i < n; ++i)
        if (!has_stub[i] && !is_feedthru[i] && att[i] != a_lo && att[i] != a_hi)
            ++t.pass_through_count;

    // Spine along `axis` at perp=locus, split around any feedthru gaps.
    emit_spine(t, axis, locus, a_lo, a_hi, ft_gaps, spine_layer);

    for (int i = 0; i < n; ++i) {
        if (!has_stub[i]) {
            // Direct: trunk is inside the best rect. For OVER mode on a rectilinear
            // block (rects with overlapping interiors, e.g. L-shape), check if ALL
            // rects span the trunk locus. If not, emit a bridge over the outer face
            // so the parts of the block outside the trunk's perp-range are also
            // connected.  Pure TEG blocks (disjoint rects) are exempt.
            if (blocks[i].teg_mode == TegMode::OVER && !blocks[i].rects.empty()
                    && rects_are_rectilinear(blocks[i].rects)) {
                bool all_span = true;
                for (const auto& r : blocks[i].rects)
                    if (locus < axis.perp_lo(r) || locus > axis.perp_hi(r)) { all_span = false; break; }
                if (!all_span) {
                    const Rect& ub = blocks[i].orig_bbox;
                    t.bridge_segments[blocks[i].block_name] =
                        axis.mkseg(axis.along_lo(ub), axis.perp_hi(ub), axis.along_hi(ub), axis.perp_hi(ub), spine_layer);
                }
            }
            continue;
        }

        // Over-the-block: if trunk is in the gap between rects on both sides,
        // emit two stubs (one per side) and a bridge over the outer face.
        if (blocks[i].teg_mode == TegMode::OVER && blocks[i].rects.size() >= 2) {
            const auto& rects = blocks[i].rects;
            bool trunk_inside_any = false;
            for (const auto& r : rects)
                if (locus >= axis.perp_lo(r) && locus <= axis.perp_hi(r)) { trunk_inside_any = true; break; }

            if (!trunk_inside_any) {
                // Partition rects into those perp-below and perp-above the trunk.
                Rect best_near = rects[0]; bool has_near = false;
                Rect best_far  = rects[0]; bool has_far  = false;
                for (const auto& r : rects) {
                    if (axis.perp_hi(r) <= locus) {
                        if (!has_near || axis.perp_hi(r) > axis.perp_hi(best_near)) { best_near = r; has_near = true; }
                    } else if (axis.perp_lo(r) >= locus) {
                        if (!has_far || axis.perp_lo(r) < axis.perp_lo(best_far)) { best_far = r; has_far = true; }
                    }
                }
                if (has_near && has_far) {
                    int a_near = axis.along_center(best_near);
                    int a_far  = axis.along_center(best_far);

                    // Stub from near rect's far face → trunk.
                    emit_tap_segment(t, axis.mkseg(a_near, axis.perp_hi(best_near), a_near, locus, stub_layer), &blocks[i]);
                    // Stub from trunk → far rect's near face.
                    emit_tap_segment(t, axis.mkseg(a_far, locus, a_far, axis.perp_lo(best_far), stub_layer), &blocks[i]);

                    // Bridge segment at union_bbox outer (perp-hi) face.
                    const Rect& ub = blocks[i].orig_bbox;
                    t.bridge_segments[blocks[i].block_name] =
                        axis.mkseg(axis.along_lo(ub), axis.perp_hi(ub), axis.along_hi(ub), axis.perp_hi(ub), spine_layer);
                    continue;
                }
            }
        }

        // Normal single stub — skip if made redundant by a longer stub's pass-through.
        if (stub_suppressed[i]) continue;
        emit_tap_segment(t, axis.mkseg(att[i], conn[i], att[i], locus, stub_layer), &blocks[i]);
    }
    if (!t.segments.empty()) results.push_back(std::move(t));
}

void TopologyGenerator::add_trunk_h(const std::vector<Point>& pins,
                                     const std::vector<Busterm>& blocks,
                                     int y_trunk, bool out_of_bbox,
                                     std::vector<Topology>& results)
{
    add_trunk(Axis{true}, /*suppress_stubs=*/false, pins, blocks, y_trunk, out_of_bbox, results);
}

void TopologyGenerator::add_trunk_v(const std::vector<Point>& pins,
                                     const std::vector<Busterm>& blocks,
                                     int x_trunk, bool out_of_bbox,
                                     std::vector<Topology>& results)
{
    add_trunk(Axis{false}, /*suppress_stubs=*/true, pins, blocks, x_trunk, out_of_bbox, results);
}

bool TopologyGenerator::segment_blocked_on_all_layers(const Segment& seg) const {
    bool is_h = (seg.start.y == seg.end.y);
    const std::vector<int>& layers = is_h ? all_h_layers_ : all_v_layers_;
    return all_layers_blocked_by_keepouts(seg, layers, floorplan_.get_keepout_zones());
}

bool TopologyGenerator::choose_edge_h_first(const Point& p1, const Point& p2,
                                            bool default_h_first) const {
    // Legs of the H-first L: H from p1 across to p2.x, then V up/down to p2.
    // Legs of the V-first L: V from p1 up/down to p2.y, then H across to p2.
    // (Same two lengths either way; only the routing layers/bend differ.)
    auto legs_blocked = [&](bool h_first) {
        Segment a, b;
        if (h_first) {
            a = make_seg(p1.x, p1.y, p2.x, p1.y, h_layer_);
            b = make_seg(p2.x, p1.y, p2.x, p2.y, v_layer_);
        } else {
            a = make_seg(p1.x, p1.y, p1.x, p2.y, v_layer_);
            b = make_seg(p1.x, p2.y, p2.x, p2.y, h_layer_);
        }
        return segment_blocked_on_all_layers(a) || segment_blocked_on_all_layers(b);
    };
    if (!legs_blocked(default_h_first))  return default_h_first;   // default routes: keep it
    if (!legs_blocked(!default_h_first)) return !default_h_first;  // alternate rescues a block
    return default_h_first;                                        // both blocked: keep default
}

// ---------------------------------------------------------------------------
// Multi-pin topology generation
// ---------------------------------------------------------------------------

// hanan_loci face-graze repair (flip blockers 1–2 in
// docs/internal/hanan_loci_flip_audit.md).  A trunk locus sampled ON a block
// face line (the `hanan_loci` knob's extra loci — a midpoint locus is strictly
// inside a channel and cannot ride a face except in degenerate 1-unit
// channels) makes a stub's TRUNK-side endpoint land exactly on the face of a
// face-riding block: annotate_endpoints tags that endpoint as a busterm TAP,
// and a tapped endpoint is never given a SEG junction (annotate_seg_conns'
// tap-wins-over-junction precedence) — so the stub↔spine junction is silently
// swallowed.  The result is either a DISCONNECTED wire graph (an aligned
// column's shared face line taps every stub endpoint: bigHalf shipped 7 such
// auto-selected candidates) or a connected-but-junction-less tree that
// defeats the fan-in taper's driver→sink path derivation.
//
// The tap is a GRAZE, not a landing: the spine itself rides the tapped
// block's face (the same load-bearing inclusive overlap the ABUT candidates
// rely on), so the block keeps its coverage when the graze tap is cleared —
// and clearing it lets annotate_seg_conns record the real junction, restoring
// the constraint NUTS actually needs to hold the stub and spine together.
//
// Cleared only when ALL of:
//   (a) the endpoint lies ON a spine segment (a junction is really there);
//   (b) the trunk locus IS a face coordinate of the tapped block (the tap is
//       the graze, not a genuine face landing into the block).
// A seeded block-side tap can never match (a): has_stub ⇒ conn != locus, so
// the stub's block-side endpoint is off the spine by construction.  Spine
// segments' own endpoint taps (the extreme blocks' structural landings) are
// never touched.
static void restore_face_graze_junctions(Topology& topo) {
    const bool spine_h = topo.type.rfind("TRUNK_H", 0) == 0;
    const bool spine_v = topo.type.rfind("TRUNK_V", 0) == 0;
    if (!spine_h && !spine_v) return;
    const int locus = topo.trunk_location;
    auto horiz_of = [](const Segment& s) { return s.start.y == s.end.y; };
    // Spine segments: along the trunk axis at perp == locus (feedthru splits
    // leave several).
    std::vector<int> spines;
    for (int i = 0; i < (int)topo.segments.size(); ++i) {
        const Segment& s = topo.segments[i];
        if (horiz_of(s) == spine_h &&
            (spine_h ? s.start.y : s.start.x) == locus)
            spines.push_back(i);
    }
    if (spines.empty()) return;
    auto on_spine = [&](const Point& P) {
        for (int j : spines) {
            const Segment& sj = topo.segments[j];
            const int alo = spine_h ? std::min(sj.start.x, sj.end.x)
                                    : std::min(sj.start.y, sj.end.y);
            const int ahi = spine_h ? std::max(sj.start.x, sj.end.x)
                                    : std::max(sj.start.y, sj.end.y);
            const int a   = spine_h ? P.x : P.y;
            if (a >= alo && a <= ahi) return true;
        }
        return false;
    };
    // Does the trunk line ride one of the tapped block's faces?  Mirror
    // annotate_endpoints' rect discipline: individual rects when present,
    // orig/shrunk bbox otherwise.
    auto locus_on_face = [&](const Busterm& bt) {
        auto face = [&](const Rect& r) {
            return spine_h ? (locus == r.y1 || locus == r.y2)
                           : (locus == r.x1 || locus == r.x2);
        };
        if (bt.rects.empty()) return face(bt.orig_bbox) || face(bt.bbox);
        for (const Rect& r : bt.rects)
            if (face(r)) return true;
        return false;
    };
    for (int i = 0; i < (int)topo.segments.size(); ++i) {
        const Segment& s = topo.segments[i];
        if (horiz_of(s) == spine_h) continue;          // spine-direction: keep taps
        auto bt = topo.seg_busterms.find(i);
        if (bt == topo.seg_busterms.end()) continue;
        for (int ep = 0; ep < 2; ++ep) {
            auto& slot = (ep == 0) ? bt->second.first : bt->second.second;
            if (!slot.has_value()) continue;
            const Point& P = (ep == 0) ? s.start : s.end;
            if ((spine_h ? P.y : P.x) != locus) continue;   // not the trunk-side end
            if (!on_spine(P)) continue;                     // (a)
            if (!locus_on_face(*slot)) continue;            // (b)
            slot.reset();                                   // junction wins over graze tap
        }
        if (!bt->second.first.has_value() && !bt->second.second.has_value())
            topo.seg_busterms.erase(bt);
    }
}

std::vector<Topology> TopologyGenerator::generate_npin(
    const std::string& src_name,
    const std::vector<std::string>& dst_names)
{
    std::vector<Topology> results;
    std::vector<Point>   pins;
    std::vector<Busterm> blocks;
    auto mk_bt = [&](const std::string& n) {
        auto cm  = floorplan_.get_block_corner_margin(n);
        Rect orig = floorplan_.get_block_bounds(n);
        Busterm bt{n, orig.shrink(cm.dx, cm.dy), orig,
                   floorplan_.get_block_rects(n),
                   floorplan_.get_block_teg_mode(n)};
        return bt;
    };
    {
        Busterm bt = mk_bt(src_name);
        pins.push_back(bt.bbox.center()); blocks.push_back(bt);
    }
    for (const auto& d : dst_names) {
        Busterm bt = mk_bt(d);
        pins.push_back(bt.bbox.center()); blocks.push_back(bt);
    }

    // Bounding box from all individual rects (not just pin centres) so that
    // Hanan midpoints between separated rects are not filtered out.
    int x_lo = INT_MAX, x_hi = INT_MIN, y_lo = INT_MAX, y_hi = INT_MIN;
    for (const auto& bt : blocks) {
        auto rs = bt.rects.empty() ? std::vector<Rect>{bt.orig_bbox} : bt.rects;
        for (const Rect& r : rs) {
            x_lo = std::min({x_lo, r.x1, r.x2});
            x_hi = std::max({x_hi, r.x1, r.x2});
            y_lo = std::min({y_lo, r.y1, r.y2});
            y_hi = std::max({y_hi, r.y1, r.y2});
        }
    }


    // Hanan grid: include edges of every individual rect (not just union bboxes).
    std::vector<Rect> all_rects_for_hanan;
    for (const auto& bt : blocks) {
        if (bt.rects.empty())
            all_rects_for_hanan.push_back(bt.orig_bbox);
        else
            for (const Rect& r : bt.rects)
                all_rects_for_hanan.push_back(r);
    }
    std::vector<int> hanan_x, hanan_y;
    bundle_hanan_grid(all_rects_for_hanan, hanan_x, hanan_y);

    // Include keepout edges in the local Hanan grid so trunk midpoints naturally
    // fall above/below keepout bands rather than inside them.
    const auto& keepouts = floorplan_.get_keepout_zones();
    if (!keepouts.empty()) {
        for (const auto& koz : keepouts) {
            hanan_x.push_back(koz.bbox.x1); hanan_x.push_back(koz.bbox.x2);
            hanan_y.push_back(koz.bbox.y1); hanan_y.push_back(koz.bbox.y2);
        }
        auto su = [](std::vector<int>& v) {
            std::sort(v.begin(), v.end());
            v.erase(std::unique(v.begin(), v.end()), v.end());
        };
        su(hanan_x); su(hanan_y);
    }

    std::set<int> y_set, x_set;
    for (int i = 0; i + 1 < (int)hanan_y.size(); ++i) {
        int mid = (hanan_y[i] + hanan_y[i+1]) / 2;
        if (mid > y_lo && mid < y_hi) y_set.insert(mid);
    }
    for (int i = 0; i + 1 < (int)hanan_x.size(); ++i) {
        int mid = (hanan_x[i] + hanan_x[i+1]) / 2;
        if (mid > x_lo && mid < x_hi) x_set.insert(mid);
    }
    // OPT-IN (`hanan_loci` generation knob): ALSO sample trunk loci ON the
    // Hanan lines themselves (strictly inside the bundle bbox), not just at
    // channel MIDPOINTS.  A block-edge-aligned locus is where a trunk's nominal
    // wirelength can reach the geometric floor (the b44 mis-ranking: the
    // WL-optimal V-trunk locus x=1200 is io_pad_tl's right edge — a Hanan
    // line — structurally unsampled by midpoints, so the best emitted 2-seg
    // TRUNK_V carried a +500 nominal overshoot).  An edge-aligned locus is
    // safe in add_trunk: the inclusive perp_lo/hi containment test treats a
    // face-riding trunk as inside the block (trunk_inside_direct), so it is not
    // re-snapped, and face-riding coverage is the same load-bearing inclusive
    // overlap the ABUT candidates rely on.  The std::set dedups a Hanan line
    // that coincides with a channel midpoint; distinct-locus duplicates
    // downstream are caught by the content uid (topo_uid).  Opt-in because the
    // extra loci renumber the WL-sorted candidate pool checked-in flows and
    // goldens pin by index.  See docs/internal/wishlist-topo.md "Nominal-WL
    // comparability across shape families", piece (a).
    // loci_only_* record the loci that exist ONLY because of the knob (a Hanan
    // line that coincides with a channel midpoint is a normal midpoint locus).
    // They scope the post-contract pinch gate at the end of this function:
    // both sets are empty at default-off, so the gate cannot touch default
    // pools by construction.
    std::set<int> loci_only_y, loci_only_x;
    if (allow_hanan_loci_) {
        for (int v : hanan_y) if (v > y_lo && v < y_hi && !y_set.count(v)) loci_only_y.insert(v);
        for (int v : hanan_x) if (v > x_lo && v < x_hi && !x_set.count(v)) loci_only_x.insert(v);
        y_set.insert(loci_only_y.begin(), loci_only_y.end());
        x_set.insert(loci_only_x.begin(), loci_only_x.end());
    }

    // Keepout-aware trunk filtering: skip trunk positions where ALL candidate
    // layers for that direction are blocked by keepouts spanning the full x/y
    // extent of the block set.  Partial blocking (some layers free) is left
    // to the planner/NUTS to resolve via layer reassignment.
    for (int y_t : y_set) {
        if (!keepouts.empty()) {
            Segment trunk_probe = make_seg(x_lo, y_t, x_hi, y_t, h_layer_);
            if (all_layers_blocked_by_keepouts(trunk_probe, all_h_layers_, keepouts))
                continue;
        }
        add_trunk_h(pins, blocks, y_t, false, results);
    }
    for (int x_t : x_set) {
        if (!keepouts.empty()) {
            Segment trunk_probe = make_seg(x_t, y_lo, x_t, y_hi, v_layer_);
            if (all_layers_blocked_by_keepouts(trunk_probe, all_v_layers_, keepouts))
                continue;
        }
        add_trunk_v(pins, blocks, x_t, false, results);
    }

    {
        int m_v = floorplan_.get_min_stub_length(1 /*VERTICAL*/,   v_layer_);
        int m_h = floorplan_.get_min_stub_length(0 /*HORIZONTAL*/, h_layer_);
        if ((int)hanan_y.size() >= 2) {
            int margin_y = std::max({m_v, 1, (int)(0.1 * (hanan_y.back() - hanan_y[0]))});
            for (int i = 0; i + 1 < (int)hanan_y.size(); ++i) {
                int mid = (hanan_y[i] + hanan_y[i+1]) / 2;
                if (mid < y_lo || mid > y_hi) {
                    if (!keepouts.empty()) {
                        Segment tp = make_seg(x_lo, mid, x_hi, mid, h_layer_);
                        if (all_layers_blocked_by_keepouts(tp, all_h_layers_, keepouts)) continue;
                    }
                    add_trunk_h(pins, blocks, mid, true, results);
                }
            }
            {
                int y_bot = hanan_y[0] - margin_y;
                if (keepouts.empty() || !all_layers_blocked_by_keepouts(
                        make_seg(x_lo, y_bot, x_hi, y_bot, h_layer_), all_h_layers_, keepouts))
                    add_trunk_h(pins, blocks, y_bot, true, results);
            }
            {
                int y_top = hanan_y.back() + margin_y;
                if (keepouts.empty() || !all_layers_blocked_by_keepouts(
                        make_seg(x_lo, y_top, x_hi, y_top, h_layer_), all_h_layers_, keepouts))
                    add_trunk_h(pins, blocks, y_top, true, results);
            }
        }
        if ((int)hanan_x.size() >= 2) {
            int margin_x = std::max({m_h, 1, (int)(0.1 * (hanan_x.back() - hanan_x[0]))});
            for (int i = 0; i + 1 < (int)hanan_x.size(); ++i) {
                int mid = (hanan_x[i] + hanan_x[i+1]) / 2;
                if (mid < x_lo || mid > x_hi) {
                    if (!keepouts.empty()) {
                        Segment tp = make_seg(mid, y_lo, mid, y_hi, v_layer_);
                        if (all_layers_blocked_by_keepouts(tp, all_v_layers_, keepouts)) continue;
                    }
                    add_trunk_v(pins, blocks, mid, true, results);
                }
            }
            {
                int x_lft = hanan_x[0] - margin_x;
                if (keepouts.empty() || !all_layers_blocked_by_keepouts(
                        make_seg(x_lft, y_lo, x_lft, y_hi, v_layer_), all_v_layers_, keepouts))
                    add_trunk_v(pins, blocks, x_lft, true, results);
            }
            {
                int x_rgt = hanan_x.back() + margin_x;
                if (keepouts.empty() || !all_layers_blocked_by_keepouts(
                        make_seg(x_rgt, y_lo, x_rgt, y_hi, v_layer_), all_v_layers_, keepouts))
                    add_trunk_v(pins, blocks, x_rgt, true, results);
            }
        }
    }

    for (auto& t : results) annotate_endpoints(t, blocks);
    for (auto& t : results) restore_face_graze_junctions(t);
    add_trunk_mst_candidates(blocks, results);
    add_mst_candidates(blocks, results);
    add_multi_trunk_candidates(pins, blocks, results);
    // Shared post-emission pipeline (annotate → sort → keepout cull → pinch →
    // coverage fill).  The keepout cull is NEW on this path: previously only
    // trunk LOCI were keepout-gated pre-emission, and an MST/BITRUNK edge or
    // a trunk STUB through a fully-blocked zone survived to the planner — a
    // silent DNUTS open (the 2-pin path has always culled these post-emission).
    std::vector<std::string> block_names;
    for (const auto& b : blocks) block_names.push_back(b.block_name);
    finalize_candidates(results, block_names);
    // Post-contract pinch gate for hanan_loci-ONLY trunk candidates (flip
    // blocker 3 in docs/internal/hanan_loci_flip_audit.md — the mis-tapped
    // zero-slide face-riders).  filter_pinched runs BEFORE the block contract
    // is stamped (a deliberate legacy-preserving order, see the NOTE in
    // finalize_candidates), so a face/abutment-line spine whose slide window
    // only collapses under the contract's pass-through clamps — e.g. the b34
    // TRUNK_H@y4615 abutment spine, pre-contract [3365,4615], post-contract
    // [4615,4615] — slips through.  Re-check the loci-only candidates (and
    // their +MST hybrids, which inherit the trunk locus) against the FINAL
    // analysis state and drop any that carry a zero-slide segment: an excluded
    // degenerate locus is strictly better than an unplaceable candidate the
    // planner would rank first on wirelength.  Scoped to loci_only_* so the
    // default-off pool is untouched by construction.
    if (!loci_only_y.empty() || !loci_only_x.empty()) {
        // Pass 1: mark (no moves — the common case is zero drops and the list
        // must come back untouched, mirroring filter_uncovered's structure).
        std::vector<char> drop(results.size(), 0);
        int n_drop = 0;
        std::string first_drop;
        for (size_t i = 0; i < results.size(); ++i) {
            const Topology& t = results[i];
            bool loci =
                (t.type.rfind("TRUNK_H", 0) == 0 && loci_only_y.count(t.trunk_location)) ||
                (t.type.rfind("TRUNK_V", 0) == 0 && loci_only_x.count(t.trunk_location));
            if (!loci) continue;
            ConnTopology ct;
            ct.build(t, floorplan_);
            for (const auto& cs : ct.segs())
                if (cs.perp_lo == cs.perp_hi) { drop[i] = 1; break; }
            if (drop[i]) {
                ++n_drop;
                if (first_drop.empty()) first_drop = t.type;
            }
        }
        // Pass 2: rebuild without the dropped candidates.  n_drop == results
        // size is unreachable in practice (the midpoint pool is a superset
        // baseline), but mirror the coverage gate's never-strand policy: keep
        // the flagged list rather than empty the bundle.
        if (n_drop > 0 && n_drop < (int)results.size()) {
            std::vector<Topology> kept;
            kept.reserve(results.size() - n_drop);
            for (size_t i = 0; i < results.size(); ++i)
                if (!drop[i]) kept.push_back(std::move(results[i]));
            notes() << "[TopoGen] dropped " << n_drop << " hanan-loci candidate(s) "
                      << "with a post-contract zero-slide segment (first: "
                      << first_drop << "); " << kept.size() << " remain.\n";
            results = std::move(kept);
        }
    }
    annotate_and_sort(results);   // final WL rank (deferred out of finalize_candidates)
    return results;
}

// Is `topo` one connected component under ConnTopology's SEG junctions?  Unlike
// topology_is_clean_tree this does NOT reject cycles -- a collinear OVERLAP join
// leaves a connected-but-cyclic (redundant) MST that is still routable, whereas a
// collinear BUTT-joint leaves a genuinely disconnected subtree.  We only want to
// drop the latter.
static bool topology_is_connected(const Topology& topo, const Floorplan& fp) {
    // Mid-generation gate: derive seg_conns on a local copy (see conn_seg_components).
    Topology t2 = topo;
    annotate_seg_conns(t2);
    ConnTopology ct;
    ct.build(t2, fp);
    const auto& segs = ct.segs();
    int n = (int)segs.size();
    if (n == 0) return true;
    std::vector<int> uf(n);
    std::iota(uf.begin(), uf.end(), 0);
    std::function<int(int)> find = [&](int x){ return uf[x]==x ? x : uf[x]=find(uf[x]); };
    for (int i = 0; i < n; ++i)
        for (const auto& c : segs[i].conns)
            if (c.kind == SegConn::SEG)
                uf[find(i)] = find(c.seg_idx);
    int root = find(0);
    for (int i = 1; i < n; ++i) if (find(i) != root) return false;
    return true;
}

// Shared MST-edge realizer (see header for the contract).  Extracted from the
// two byte-identical copies that used to live in add_mst_candidates and
// add_trunk_mst_candidates::realize_edges; the only historical divergence — the
// straight-leg min-stub gate — is preserved via `gate_straight`.
bool TopologyGenerator::realize_mst_edge(const Rect& r_u, const Rect& r_v,
                                         bool prefer_h_first, bool gate_straight,
                                         int m_h, int m_v,
                                         std::vector<Segment>& out) const {
    Point p1, p2;
    closest_points(r_u, r_v, p1, p2);
    if (p1.x == p2.x && p1.y == p2.y) {
        // Abutting (or coincident) rects: realize the shared edge as a real wire
        // so the block stays connected, instead of dropping it.
        Segment es;
        if (shared_edge_segment(r_u, r_v, h_layer_, v_layer_, es))
            out.push_back(es);
        return true;
    }
    // Corner-diagonal? A straight edge whose shared projection is a single point
    // is pinned (zero slide); route it around the corner as an L.
    const int cox_lo = std::max(r_u.x1, r_v.x1), cox_hi = std::min(r_u.x2, r_v.x2);
    const int coy_lo = std::max(r_u.y1, r_v.y1), coy_hi = std::min(r_u.y2, r_v.y2);
    if ((p1.x == p2.x && cox_lo == cox_hi) || (p1.y == p2.y && coy_lo == coy_hi)) {
        corner_diagonal_L(r_u, r_v, prefer_h_first ? 0 : 1, h_layer_, v_layer_, out);
        return true;
    }
    if (p1.x == p2.x) {
        if (gate_straight && std::abs(p2.y - p1.y) < m_v) return false;
        out.push_back(make_seg(p1.x, p1.y, p1.x, p2.y, v_layer_));
    } else if (p1.y == p2.y) {
        if (gate_straight && std::abs(p2.x - p1.x) < m_h) return false;
        out.push_back(make_seg(p1.x, p1.y, p2.x, p1.y, h_layer_));
    } else {
        // Diagonal: both L's have the same two leg lengths, so the min-stub gate
        // is orientation-independent; check once, then keep the strategy
        // orientation unless it is keepout-blocked and the alternate is clear.
        if (std::abs(p2.x - p1.x) < m_h || std::abs(p2.y - p1.y) < m_v) return false;
        if (choose_edge_h_first(p1, p2, /*default_h_first=*/prefer_h_first)) {
            out.push_back(make_seg(p1.x, p1.y, p2.x, p1.y, h_layer_));
            out.push_back(make_seg(p2.x, p1.y, p2.x, p2.y, v_layer_));
        } else {
            out.push_back(make_seg(p1.x, p1.y, p1.x, p2.y, v_layer_));
            out.push_back(make_seg(p1.x, p2.y, p2.x, p2.y, h_layer_));
        }
    }
    return true;
}

void TopologyGenerator::add_mst_candidates(const std::vector<Busterm>& blocks,
                                           std::vector<Topology>& results) {
    // MST topologies model daisy-chain connections (each block connects to its
    // nearest neighbour rather than to a shared trunk spine).  For 2 blocks this
    // degenerates to an L-shape already covered by TRUNK candidates.  For 3 blocks
    // the TRUNK+MST hybrid (add_trunk_mst_candidates) already provides MST-like
    // inter-block connectivity on top of the trunk spine.  Standalone MST starts
    // at 4 blocks where the pure tree structure offers something distinct.
    if (blocks.size() < 4) return;

    // Use individual rects for multi-rect blocks so that closest_points finds
    // a point on an actual physical face, not in the union-bbox interior.
    auto block_rects = [&](int i) -> std::vector<Rect> {
        return blocks[i].rects.empty()
               ? std::vector<Rect>{blocks[i].bbox}
               : blocks[i].rects;
    };

    // Closest point pair across all individual rect pairs of two blocks; also
    // reports the chosen rect pair (br_u/br_v) so an abutment can be realized as a
    // shared-edge segment rather than dropped.
    auto closest_block_points = [&](int u, int v, Point& p1, Point& p2,
                                    Rect& br_u, Rect& br_v) {
        int best = INT_MAX;
        for (const Rect& ru : block_rects(u)) {
            for (const Rect& rv : block_rects(v)) {
                int d = manhattan_nearest(ru, rv);
                if (d < best) { best = d; closest_points(ru, rv, p1, p2);
                                br_u = ru; br_v = rv; }
            }
        }
    };

    // Build MST via the shared multi-rect Kruskal (compute_mst): edge weight =
    // min manhattan over all rect pairs.  Byte-identical to the previous inline
    // rect_min_dist Kruskal — same edge enumeration, sort, and union-find — now
    // that compute_mst carries the multi-rect weighting.
    int n = (int)blocks.size();
    std::vector<std::pair<std::string, std::vector<Rect>>> mst_nodes;
    mst_nodes.reserve(n);
    for (int i = 0; i < n; ++i)
        mst_nodes.push_back({blocks[i].block_name, block_rects(i)});
    std::vector<std::pair<int,int>> mst_edges;
    for (const auto& e : compute_mst(mst_nodes))
        mst_edges.push_back({e.u, e.v});

    int m_v = floorplan_.get_min_stub_length(1 /*VERTICAL*/, v_layer_);
    int m_h = floorplan_.get_min_stub_length(0 /*HORIZONTAL*/, h_layer_);

    for (int strategy = 0; strategy < 2; ++strategy) {
        Topology mst;
        mst.type = (strategy == 0) ? "MST_HV" : "MST_VH";
        bool valid = true;
        for (int ei = 0; ei < (int)mst_edges.size(); ++ei) {
            const auto& [eu, ev] = mst_edges[ei];
            const size_t before = mst.segments.size();   // tag this edge's legs
            // closest_block_points selects the closest rect PAIR across the two
            // (possibly multi-rect) blocks; only br_u/br_v are used below (the
            // shared realizer recomputes p1/p2 from them, identically).
            Point p1, p2;
            Rect  br_u, br_v;
            closest_block_points(eu, ev, p1, p2, br_u, br_v);
            // Standalone MST does NOT gate straight legs (gate_straight=false);
            // only its diagonal legs meet the min-stub floor.
            if (!realize_mst_edge(br_u, br_v, /*prefer_h_first=*/strategy == 0,
                                  /*gate_straight=*/false, m_h, m_v, mst.segments)) {
                valid = false; break;
            }
            for (size_t k = before; k < mst.segments.size(); ++k)
                mst.segments[k].edge_id = ei;
        }
        if (valid) {
            // Annotate the raw stubs first, then complete: completion rewrites the
            // relay busterm taps (single tap + SEG junctions) and annotates the
            // connectors it appends, so it must run after the baseline annotation.
            annotate_endpoints(mst, blocks);
            complete_relay_junctions(mst, blocks, floorplan_, h_layer_, v_layer_, allow_spine_relays_);
            if (mst.connected_block_names.empty())
                for (const auto& b : blocks)
                    mst.connected_block_names.push_back(b.block_name);
            // Drop a standalone MST left DISCONNECTED by a collinear butt-joint
            // that ConnTopology can't infer (e.g. a perpendicular abutment
            // crossing meeting a regular edge end-to-end).  The planner cost loop
            // does not check connectivity, so a disconnected MST would otherwise
            // be selectable and route to an open.  Connectivity-only (not
            // clean-tree): a connected-but-cyclic collinear OVERLAP is still
            // routable and kept.
            if (topology_is_connected(mst, floorplan_))
                results.push_back(std::move(mst));
        }
    }
}

// Is `topo` a clean routing TREE under the SEG junctions ConnTopology infers --
// i.e. one connected component AND acyclic?  A completed trunk+MST candidate is
// accepted only when this holds:
//   • connected: a collinear stub/edge end-to-end join (which ConnTopology does
//     not infer) can split the wire into pieces even though it is geometrically
//     continuous;
//   • acyclic: a kept stub that crosses another branch block (a pass-through)
//     leaves a redundant second path to the trunk, which completion would close
//     into a real loop.
// Either defect means the hybrid is not a faithful tree, so it is dropped rather
// than emitted.  The acyclic test mirrors conftest._has_no_cycles (undirected
// cycle detection over unique SEG edges).
static bool topology_is_clean_tree(const Topology& topo, const Floorplan& fp) {
    // Mid-generation gate: derive seg_conns on a local copy (see conn_seg_components).
    Topology t2 = topo;
    annotate_seg_conns(t2);
    ConnTopology ct;
    ct.build(t2, fp);
    const auto& segs = ct.segs();
    int n = (int)segs.size();
    if (n == 0) return true;
    std::vector<int> uf(n);
    std::iota(uf.begin(), uf.end(), 0);
    auto find = [&uf](int x) { while (uf[x] != x) { uf[x] = uf[uf[x]]; x = uf[x]; } return x; };
    std::set<std::pair<int,int>> edges;          // unique undirected SEG edges
    for (int i = 0; i < n; ++i)
        for (const auto& c : segs[i].conns)
            if (c.kind == SegConn::SEG)
                edges.insert({std::min(i, c.seg_idx), std::max(i, c.seg_idx)});
    for (const auto& [a, b] : edges) {
        int ra = find(a), rb = find(b);
        if (ra == rb) return false;              // a SEG edge re-closes a component -> cycle
        uf[ra] = rb;
    }
    int root = find(0);
    for (int i = 1; i < n; ++i) if (find(i) != root) return false;   // disconnected

    // Block coverage (mirrors check_topo): every connected block must have either
    // a BUSTERM tap or a segment spanning it.  SEG-connectivity alone does not
    // guarantee this -- a selectively-completed relay can drop a block's busterm
    // tap (OTC pass-through) while no segment actually spans it, leaving the block
    // silently uncovered.  Reject such a tree at generation so it never becomes a
    // candidate that check_topo would later flag.
    auto spans_rect = [](const ConnSeg& cs, double perp, const Rect& r) {
        if (cs.horiz)
            return perp >= r.y1 && perp <= r.y2
                && cs.along_lo <= r.x2 && cs.along_hi >= r.x1;
        return perp >= r.x1 && perp <= r.x2
            && cs.along_lo <= r.y2 && cs.along_hi >= r.y1;
    };
    std::set<std::string> tapped;
    for (const auto& cs : segs)
        for (const auto& c : cs.conns)
            if (c.kind == SegConn::BUSTERM) tapped.insert(c.block_name);
    for (const auto& bname : topo.connected_block_names) {
        if (tapped.count(bname)) continue;
        auto rects = fp.get_block_rects(bname);
        if (rects.empty()) rects.push_back(fp.get_block_bounds(bname));
        bool covered = false;
        for (const auto& cs : segs) {
            if (covered) break;
            for (const Rect& r : rects)
                if (spans_rect(cs, (double)cs.perp_pos, r)) { covered = true; break; }
        }
        if (!covered) return false;          // uncovered block -> not a clean tree
    }
    return true;
}

// Re-clip a trunk spine after some of its stubs were REPLACED by MST edges and
// dropped (completed-tree hybrid).  The spine span was computed in add_trunk_h/v
// from *all* branch blocks; once an extreme block's stub is dropped (it now
// reaches the trunk through an inter-block edge) the spine no longer needs to
// run that far, yet it was copied verbatim -- leaving a phantom overhang past the
// last real landing (defect: trunk overextension, bundles 14/61).  Recompute the
// spine's along-axis extent from the extreme *kept* landing on the trunk line,
// then extend just enough to still span every pass-through block the trunk
// crosses (those have no busterm tap, so a covering segment is required -- see
// topology_is_clean_tree).  Only the single-spine case is handled; a
// feedthru-split spine is already bounded at block faces and is left untouched.
static void clip_spine_to_landings(Topology& t, int trunk_pos, bool dir_h,
                                   const std::vector<Busterm>& blocks) {
    int spine = -1, n_spine = 0;
    for (int s = 0; s < (int)t.segments.size(); ++s) {
        const Segment& seg = t.segments[s];
        bool is_spine = dir_h ? (seg.start.y == trunk_pos && seg.end.y == trunk_pos)
                              : (seg.start.x == trunk_pos && seg.end.x == trunk_pos);
        if (is_spine) { spine = s; ++n_spine; }
    }
    if (spine < 0 || n_spine != 1) return;   // no spine, or feedthru-split: skip

    auto along    = [&](const Point& p) { return dir_h ? p.x : p.y; };
    auto on_trunk = [&](const Point& p) { return dir_h ? (p.y == trunk_pos)
                                                       : (p.x == trunk_pos); };

    // Required span = every place a kept non-spine segment connects to the trunk
    // line, counting BOTH a stub endpoint that lands on it AND a perpendicular
    // segment that *crosses* it (a T-junction whose endpoints sit off the trunk
    // line -- e.g. an MST edge leg spanning the trunk).  Missing the crossing case
    // would leave the spine dangling out to a dropped block past the last junction.
    int lo = INT_MAX, hi = INT_MIN;
    for (int s = 0; s < (int)t.segments.size(); ++s) {
        if (s == spine) continue;
        const Segment& seg = t.segments[s];
        for (const Point& p : {seg.start, seg.end})
            if (on_trunk(p)) { lo = std::min(lo, along(p)); hi = std::max(hi, along(p)); }
        // Perpendicular crossing: a V seg crossing an H trunk (or vice-versa).
        bool seg_h = (seg.start.y == seg.end.y);
        if (dir_h && !seg_h) {
            int plo = std::min(seg.start.y, seg.end.y), phi = std::max(seg.start.y, seg.end.y);
            if (plo <= trunk_pos && trunk_pos <= phi) {
                lo = std::min(lo, seg.start.x); hi = std::max(hi, seg.start.x);
            }
        } else if (!dir_h && seg_h) {
            int plo = std::min(seg.start.x, seg.end.x), phi = std::max(seg.start.x, seg.end.x);
            if (plo <= trunk_pos && trunk_pos <= phi) {
                lo = std::min(lo, seg.start.y); hi = std::max(hi, seg.start.y);
            }
        }
    }
    if (lo > hi) return;                      // no kept junctions -> leave spine as-is

    // Extend just enough to keep every pass-through block covered.
    for (const auto& b : blocks) {
        bool straddle = dir_h ? (b.orig_bbox.y1 <= trunk_pos && trunk_pos <= b.orig_bbox.y2)
                              : (b.orig_bbox.x1 <= trunk_pos && trunk_pos <= b.orig_bbox.x2);
        if (!straddle) continue;
        int b_lo = dir_h ? b.orig_bbox.x1 : b.orig_bbox.y1;
        int b_hi = dir_h ? b.orig_bbox.x2 : b.orig_bbox.y2;
        if (b_lo > hi)      hi = b_lo;        // block entirely past hi: reach its near face
        else if (b_hi < lo) lo = b_hi;        // block entirely before lo: reach its near face
    }

    // Clip to [lo,hi], never extending past the spine's current extent.
    Segment& sp = t.segments[spine];
    int s0 = along(sp.start), s1 = along(sp.end);
    int cur_lo = std::min(s0, s1), cur_hi = std::max(s0, s1);
    int new_lo = std::max(cur_lo, lo), new_hi = std::min(cur_hi, hi);
    if (new_lo >= new_hi) return;             // degenerate -> leave as-is
    if (new_lo == cur_lo && new_hi == cur_hi) return;   // nothing to clip
    if (dir_h) { sp.start.x = new_lo; sp.end.x = new_hi; }
    else       { sp.start.y = new_lo; sp.end.y = new_hi; }
}

void TopologyGenerator::add_trunk_mst_candidates(
    const std::vector<Busterm>& blocks,
    std::vector<Topology>& results)
{
    // For each TRUNK_H/V topology, identify BRANCH blocks (those with explicit
    // stubs, i.e. not pass-through).  Compute an MST among the branch blocks
    // and add inter-branch edges as extra segments, creating TRUNK+MST hybrids
    // that provide direct block-to-block shortcuts alongside the trunk spine.
    int orig_count = (int)results.size();
    int m_h = floorplan_.get_min_stub_length(0 /*HORIZONTAL*/, h_layer_);
    int m_v = floorplan_.get_min_stub_length(1 /*VERTICAL*/,   v_layer_);

    // <4-block coverage fallback: a non-simple hybrid (multi-rect/TEG branch, or no
    // stub-owning root) whose completion is not a clean tree is normally dropped --
    // but for <4-block bundles add_mst_candidates emits no standalone MST_*, so if
    // EVERY trunk position drops, the bundle is left with no MST-type coverage at
    // all (regressing ad29c1f's 3-pin intent).  Stash the un-completed hybrids here
    // and emit ONE only if no clean MST candidate was produced for the whole bundle,
    // so configs that already have clean coverage don't get an extra relay candidate.
    std::vector<Topology> fallback_pool;

    // Observability for the seed-trunk removability drop (docs/internal/
    // bus005_dangling_scan): a redundant trunk hybrid is dropped silently
    // otherwise, and silent drops are exactly what this line of work set out to
    // make visible.  Counted across all trunk positions; logged once below.
    int n_redundant_dropped = 0;
    // Counted separately from the removability drop above: an ANTENNA spine is a
    // different fault (inert metal in the candidate AS IT STANDS, issue #485) and
    // the two reasons must stay distinguishable in the log — a candidate can be
    // antenna-flagged while its spine is NOT removable (the collinear-stub case).
    int n_antenna_dropped = 0;
    std::string first_redundant;

    for (int ti = 0; ti < orig_count; ++ti) {
        const Topology& trunk_topo = results[ti];
        bool is_h = (trunk_topo.type.find("TRUNK_H") != std::string::npos);
        bool is_v = (trunk_topo.type.find("TRUNK_V") != std::string::npos);
        if (!is_h && !is_v) continue;
        // Don't cascade: skip topologies that are already TRUNK+MST.
        if (trunk_topo.type.find("+MST") != std::string::npos) continue;

        int trunk_pos = trunk_topo.trunk_location;
        // BRANCH blocks: blocks whose orig_bbox does NOT contain trunk_pos.
        // Pass-through (spine) blocks straddle the trunk; they need no stub
        // and are already connected via the trunk passing through their bbox.
        std::vector<int> branch_idx;
        for (int i = 0; i < (int)blocks.size(); ++i) {
            bool is_spine = is_h
                ? (blocks[i].orig_bbox.y1 <= trunk_pos && trunk_pos <= blocks[i].orig_bbox.y2)
                : (blocks[i].orig_bbox.x1 <= trunk_pos && trunk_pos <= blocks[i].orig_bbox.x2);
            if (!is_spine) branch_idx.push_back(i);
        }
        // Need ≥2 branch blocks to compute an inter-branch MST.
        if (branch_idx.size() < 2) continue;

        // Build MST among branch blocks.  Use the block rect nearest to the
        // trunk as the representative rect for each block.  Record each block's
        // distance to the trunk so we can root the MST at the trunk-nearest block.
        std::vector<std::pair<std::string, Rect>> nodes;
        std::vector<int> node_trunk_dist;
        for (int idx : branch_idx) {
            const Busterm& bt = blocks[idx];
            auto rects = bt.rects.empty() ? std::vector<Rect>{bt.orig_bbox} : bt.rects;
            Rect best = rects[0];
            int  best_d = INT_MAX;
            for (const auto& r : rects) {
                Rect tp = is_h ? Rect{r.x1, trunk_pos, r.x2, trunk_pos}
                               : Rect{trunk_pos, r.y1, trunk_pos, r.y2};
                int d = manhattan_nearest(tp, r);
                if (d < best_d) { best_d = d; best = r; }
            }
            nodes.emplace_back(bt.block_name, best);
            node_trunk_dist.push_back(best_d);
        }

        auto mst_edges = compute_mst(nodes);
        if (mst_edges.empty()) continue;

        // ── Trunk-rooted tree: make each MST edge REPLACE a stub ──────────────
        // The copied trunk already stubs to every branch block; the MST edges add
        // a second path between the same blocks, so completing the relays as-is
        // would close a cycle (hence completion was historically skipped here).
        // Instead, root the MST at the branch block nearest the trunk and drop the
        // trunk stub of every OTHER branch block: each non-root block now reaches
        // the trunk solely through its MST parent edge, so the hybrid is a clean
        // trunk-rooted tree that complete_relay_junctions can wire up safely.
        //
        // Scope to single-rect branch blocks: a multi-rect/TEG-OVER block can own
        // two V stubs plus a bridge, and dropping those would dangle the bridge.
        // For those we keep the legacy (un-completed) behaviour, still flagged.
        bool simple = true;
        for (int idx : branch_idx)
            if (!blocks[idx].rects.empty()) { simple = false; break; }

        // The root keeps its trunk stub; every other branch block reaches the
        // trunk through the MST tree rooted at it.  The root must therefore
        // actually OWN a stub: a pass-through block (one the trunk reaches only by
        // another block's stub crossing it) appears in no busterm entry, and
        // "keeping" its nonexistent stub would leave the whole MST cluster
        // detached from the spine.  Root at the trunk-nearest stub-owning block.
        std::set<std::string> stub_owners;
        for (const auto& [sidx, eps] : trunk_topo.seg_busterms) {
            (void)sidx;
            if (eps.first)  stub_owners.insert(eps.first->block_name);
            if (eps.second) stub_owners.insert(eps.second->block_name);
        }
        int root_node = -1;
        for (int k = 0; k < (int)nodes.size(); ++k) {
            if (!stub_owners.count(nodes[k].first)) continue;      // must own a stub
            if (root_node < 0 || node_trunk_dist[k] < node_trunk_dist[root_node])
                root_node = k;
        }
        if (root_node < 0) simple = false;   // no stub-owning branch block to root at

        // ── Selective trunk-rooted tree ──────────────────────────────────────
        // Root the MST at the trunk-nearest stub-owning block and orient it
        // outward.  REPLACE a non-root block's trunk stub with its MST parent
        // edge ONLY when that edge is shorter than the stub (a beneficial
        // shortcut); otherwise keep the straight, shorter stub and do NOT add the
        // edge.  Each used edge drops exactly one stub, so the result stays a
        // cycle-free tree and every block still reaches the trunk (a replaced
        // block via its parent, a kept block via its own stub).  This lets a
        // block stacked just past another reach the trunk through a short
        // inter-block edge instead of a long parallel stub (bundle 8: blk_09
        // reaches the trunk via a short edge to blk_03, not its own long stub).
        std::set<std::string> child_names;        // stubs the MST replaces
        std::vector<bool> used_edge(mst_edges.size(), false);
        if (simple && root_node >= 0) {
            std::vector<std::vector<std::pair<int,int>>> adj(nodes.size());
            for (int e = 0; e < (int)mst_edges.size(); ++e) {
                adj[mst_edges[e].u].push_back({mst_edges[e].v, e});
                adj[mst_edges[e].v].push_back({mst_edges[e].u, e});
            }
            std::vector<int> parent_edge(nodes.size(), -1);
            std::vector<bool> seen(nodes.size(), false);
            std::vector<int> frontier{root_node};
            seen[root_node] = true;
            for (size_t qi = 0; qi < frontier.size(); ++qi) {
                int u = frontier[qi];
                for (const auto& [v, e] : adj[u]) if (!seen[v]) {
                    seen[v] = true; parent_edge[v] = e; frontier.push_back(v);
                }
            }
            for (int k = 0; k < (int)nodes.size(); ++k) {
                if (k == root_node || parent_edge[k] < 0) continue;
                if (mst_edges[parent_edge[k]].dist < node_trunk_dist[k]) {
                    child_names.insert(nodes[k].first);
                    used_edge[parent_edge[k]] = true;
                }
            }
            // <4-block bundles have no standalone MST candidate (add_mst_candidates
            // needs N>=4), so the completed trunk-rooted tree is their ONLY MST-type
            // coverage.  If no edge was individually beneficial, force the full tree
            // (drop every non-root stub, use every tree edge) so a clean TRUNK+MST
            // candidate is still emitted instead of the cyclic legacy hybrid -- which
            // the clean-tree gate now drops.  Preserves ad29c1f's 3-pin coverage.
            if (child_names.empty() && blocks.size() < 4) {
                for (int k = 0; k < (int)nodes.size(); ++k) {
                    if (k == root_node || parent_edge[k] < 0) continue;
                    child_names.insert(nodes[k].first);
                    used_edge[parent_edge[k]] = true;
                }
            }
        }

        std::string mst_type;
        {
            auto at = trunk_topo.type.find('@');
            mst_type = (at != std::string::npos)
                ? trunk_topo.type.substr(0, at) + "+MST" + trunk_topo.type.substr(at)
                : trunk_topo.type + "+MST";
        }

        // Realize a chosen subset of MST edges into segments; returns false if any
        // selected edge is too short to meet the min-stub floor (reject the whole
        // candidate rather than emit an incomplete shortcut).
        auto realize_edges = [&](const std::vector<bool>& take,
                                 std::vector<Segment>& out) -> bool {
            for (int e = 0; e < (int)mst_edges.size(); ++e) {
                if (!take[e]) continue;
                const size_t before = out.size();   // tag this edge's legs below
                const Rect& r_u = nodes[mst_edges[e].u].second;
                const Rect& r_v = nodes[mst_edges[e].v].second;
                // Hybrid orientation follows the trunk (H-trunk -> V-leg first,
                // i.e. prefer_h_first = !is_h) and GATES straight legs: an aligned
                // shortcut shorter than the min-stub floor would be pinned/zero-
                // slide, so it rejects the whole hybrid (gate_straight=true).
                if (!realize_mst_edge(r_u, r_v, /*prefer_h_first=*/!is_h,
                                      /*gate_straight=*/true, m_h, m_v, out))
                    return false;
                for (size_t k = before; k < out.size(); ++k) out[k].edge_id = e;
            }
            return true;
        };

        // Completed-tree form (single-rect blocks, stub-owning root, >=1 beneficial
        // shortcut): drop the replaced stubs, add their shortcut edges, complete
        // the relays, and emit only if it verifies as one clean SEG-connected tree.
        if (simple && root_node >= 0 && !child_names.empty()) {
            std::vector<Segment> edge_segs;
            if (!realize_edges(used_edge, edge_segs) || edge_segs.empty()) continue;
            Topology tree = trunk_topo;
            tree.type = mst_type;
            std::vector<Segment> kept;
            std::map<int, SegEndpoints> kept_bt;
            int ni = 0;
            for (int s = 0; s < (int)trunk_topo.segments.size(); ++s) {
                bool drop = false;
                auto it = trunk_topo.seg_busterms.find(s);
                if (it != trunk_topo.seg_busterms.end())
                    for (const auto& opt : {it->second.first, it->second.second})
                        if (opt && child_names.count(opt->block_name)) { drop = true; break; }
                if (drop) continue;
                kept.push_back(trunk_topo.segments[s]);
                if (it != trunk_topo.seg_busterms.end()) kept_bt[ni] = it->second;
                ++ni;
            }
            tree.segments     = std::move(kept);
            tree.seg_busterms = std::move(kept_bt);
            for (const auto& s : edge_segs) tree.segments.push_back(s);
            // Dropped child stubs may have set the spine's extent: re-clip it to
            // the extreme kept landing so no phantom overhang remains (defect 1).
            clip_spine_to_landings(tree, trunk_pos, is_h, blocks);
            annotate_endpoints(tree, blocks);
            complete_relay_junctions(tree, blocks, floorplan_, h_layer_, v_layer_, allow_spine_relays_);
            // connected_block_names is populated globally only after this pass
            // (generate_candidates), so set it here so the gate's coverage check
            // knows which blocks the tree must cover.
            if (tree.connected_block_names.empty())
                for (const auto& b : blocks)
                    tree.connected_block_names.push_back(b.block_name);
            // Drop a hybrid whose seed trunk is redundant — removing it leaves a
            // valid route (the MST edges already connect everything, so the trunk
            // is vestigial).  Applies to OOB detour trunks too: a genuine detour
            // is NOT removable (removing it disconnects), while a redundant OOB
            // trunk that hangs off the tree at one point IS (bus_033 cand 29).
            // Also drop one whose spine is an ANTENNA as it stands (issue #485).
            if (topology_is_clean_tree(tree, floorplan_)) {
                bool is_oob = (tree.type.find("_OOB") != std::string::npos);
                bool antenna = seed_trunk_is_antenna(tree, trunk_pos, is_h, floorplan_);
                if (antenna ||
                    seed_trunk_is_redundant(tree, trunk_pos, is_h, floorplan_,
                                            /*require_dangling_spine=*/is_oob)) {
                    if (antenna) ++n_antenna_dropped; else ++n_redundant_dropped;
                    if (first_redundant.empty()) first_redundant = tree.type;
                } else {
                    results.push_back(std::move(tree));
                }
            }
            // A simple hybrid that can't be cleanly completed is DROPPED (the base
            // trunk + standalone MST already cover the bundle).
            continue;
        }
        // Simple but no beneficial shortcut.  For >=4 blocks a standalone MST
        // candidate is generated separately (add_mst_candidates), so the plain
        // trunk plus that standalone MST already cover the bundle -- skip the
        // redundant hybrid.  For <4 blocks add_mst_candidates bails (no standalone
        // MST exists), so this hybrid is the bundle's only MST-type coverage:
        // fall through to the legacy hybrid below instead of dropping it.
        if (simple && root_node >= 0 && blocks.size() >= 4) continue;

        // Multi-rect / no stub-owning root: attempt the historical hybrid
        // (full trunk + ALL shortcut edges), but COMPLETE its relays and emit
        // only if it verifies as one clean SEG-connected tree -- no silent
        // feedthrough relay.  Previously this path emitted the un-completed
        // hybrid and relied on check_topo to flag the FEEDTHRU_RELAY after the
        // fact; that left the planner free to select a physically disconnected
        // candidate (with an artificially low wirelength).  An un-completable
        // hybrid is now dropped: the base trunk + standalone MST already cover
        // the bundle.
        std::vector<Segment> all_segs;
        std::vector<bool> all_take(mst_edges.size(), true);
        if (!realize_edges(all_take, all_segs) || all_segs.empty()) continue;
        Topology legacy = trunk_topo;
        legacy.type = mst_type;
        for (const auto& s : all_segs) legacy.segments.push_back(s);
        // Snapshot the un-completed hybrid BEFORE completion mutates it -- it is the
        // <4-block coverage fallback below.
        Topology uncompleted = legacy;
        annotate_endpoints(legacy, blocks);
        complete_relay_junctions(legacy, blocks, floorplan_, h_layer_, v_layer_, allow_spine_relays_);
        if (legacy.connected_block_names.empty())
            for (const auto& b : blocks)
                legacy.connected_block_names.push_back(b.block_name);
        // A single-point seed trunk here is a dangling overshoot: drop the whole
        // candidate (and don't pool it as a fallback either — the plain trunk
        // still covers the bundle).
        bool legacy_is_oob = (legacy.type.find("_OOB") != std::string::npos);
        bool legacy_antenna =
            seed_trunk_is_antenna(legacy, trunk_pos, is_h, floorplan_);
        bool legacy_dangling =
            legacy_antenna ||
            seed_trunk_is_redundant(legacy, trunk_pos, is_h, floorplan_,
                                    /*require_dangling_spine=*/legacy_is_oob);
        if (legacy_dangling &&
            (topology_is_clean_tree(legacy, floorplan_) || blocks.size() < 4)) {
            // would have emitted/pooled but for redundancy
            if (legacy_antenna) ++n_antenna_dropped; else ++n_redundant_dropped;
            if (first_redundant.empty()) first_redundant = legacy.type;
        }
        if (topology_is_clean_tree(legacy, floorplan_) && !legacy_dangling) {
            results.push_back(std::move(legacy));
        } else if (blocks.size() < 4 && !legacy_dangling) {
            // Completion could not yield a clean tree (e.g. a multi-rect/TEG branch
            // whose stubs can't be dropped without dangling its bridge, or no
            // stub-owning root).  Defer it to the post-loop coverage fallback rather
            // than dropping outright: check_topo flags its relay, but its wirelength
            // is honestly over-counted (full trunk + every edge), so even if it is
            // the bundle's only MST option the planner never prefers it to the plain
            // trunk.  For >=4 blocks the base trunk + standalone MST already cover
            // the bundle, so non-simple hybrids are dropped (not pooled).
            annotate_endpoints(uncompleted, blocks);
            for (const auto& b : blocks)
                uncompleted.connected_block_names.push_back(b.block_name);
            fallback_pool.push_back(std::move(uncompleted));
        }
    }

    if (n_redundant_dropped > 0 || n_antenna_dropped > 0) {
        notes() << "[TopoGen] dropped " << (n_redundant_dropped + n_antenna_dropped)
                  << " redundant trunk+MST hybrid(s) (";
        if (n_redundant_dropped > 0) {
            notes() << n_redundant_dropped << " removable seed trunk";
            if (n_antenna_dropped > 0) notes() << ", ";
        }
        if (n_antenna_dropped > 0) notes() << n_antenna_dropped << " antenna seed trunk";
        notes() << "; first: " << first_redundant << ").\n";
    }

    // Emit a single un-completed fallback ONLY if the whole bundle produced no clean
    // MST-type candidate (neither here nor in the completed-tree path above) -- so a
    // <4-block bundle never loses MST coverage, without padding bundles that already
    // have clean hybrids with redundant relay candidates.  Pick the shortest pooled
    // hybrid (least wasted wire) for the coverage role.
    bool any_mst = false;
    for (int i = orig_count; i < (int)results.size(); ++i)
        if (results[i].type.find("MST") != std::string::npos) { any_mst = true; break; }
    if (!any_mst && !fallback_pool.empty()) {
        auto seg_len = [](const Topology& t) {
            int wl = 0;
            for (const auto& s : t.segments)
                wl += std::abs(s.end.x - s.start.x) + std::abs(s.end.y - s.start.y);
            return wl;
        };
        size_t best = 0;
        for (size_t i = 1; i < fallback_pool.size(); ++i)
            if (seg_len(fallback_pool[i]) < seg_len(fallback_pool[best])) best = i;
        results.push_back(std::move(fallback_pool[best]));
    }
}

// Two-level BITRUNK trees for high-fan-out nets over regular datapath-like
// placements.  A root spine (one orientation) feeds perpendicular BRANCH trunks,
// each tapping a cluster of leaf blocks aligned along the root axis; a leaf either
// stubs to its branch (branch runs beside it) or is a multi-tap pass-through (the
// branch runs down through the column and covers it).  Both orientations are
// emitted so the planner can pick the H/T shape that beats a single spine.
// Opt-in (`set_multi_trunk`); self-gated on the clean-tree check.
void TopologyGenerator::add_multi_trunk_candidates(
    const std::vector<Point>& pins,
    const std::vector<Busterm>& blocks,
    std::vector<Topology>& results)
{
    const int n = (int)blocks.size();
    if (n < 4) return;

    // Legacy BITRUNK_H/BITRUNK_V (two parallel rung trunks + a central
    // perpendicular backbone).  Emitted unconditionally; the opt-in flag below
    // only ADDS the two-level trees.  Written ONCE against the Axis abstraction
    // and emitted in BOTH orientations: rungs_horiz => two H rungs + a V
    // backbone (BITRUNK_H, a ROW of receivers — byte-identical to the historical
    // hard-coded H shape); !rungs_horiz => two V rungs + an H backbone
    // (BITRUNK_V, a COLUMN of receivers — the previously-missing mirror, see
    // docs/internal/topology_tree_gen_design.md).  Coordinates read the "along"
    // (rung) axis and "perp" (backbone/stub) axis via Axis.
    auto emit_legacy_bitrunk = [&](bool rungs_horiz) {
        const Axis axis{rungs_horiz};
        const int rung_layer = rungs_horiz ? h_layer_ : v_layer_;
        const int perp_layer = rungs_horiz ? v_layer_ : h_layer_;  // backbone + stubs
        // Stub runs perpendicular to the rung: V (dir 1) for H rungs, H (dir 0)
        // for V rungs.
        const int stub_dir = rungs_horiz ? 1 : 0;
        std::vector<int> perp_coords;
        for (const auto& p : pins) perp_coords.push_back(axis.perp(p));
        std::sort(perp_coords.begin(), perp_coords.end());
        int p_mid = perp_coords[perp_coords.size() / 2];
        int p_t1  = perp_coords[perp_coords.size() / 4];
        int p_t2  = perp_coords[3 * perp_coords.size() / 4];
        if (p_t1 == p_t2) return;
        Topology t;
        t.type = rungs_horiz ? "BITRUNK_H" : "BITRUNK_V";
        int a_min = INT_MAX, a_max = INT_MIN;
        for (const auto& p : pins) { a_min = std::min(a_min, axis.along(p));
                                     a_max = std::max(a_max, axis.along(p)); }
        // A pin-set with no along-extent collapses both rungs to points — a
        // zero-length wire has no conn-segs to pin it, carries no bus, and
        // cannot be placed by NUTS (kAbutmentSpanEpsilon invariant).  Mirror the
        // p_t1==p_t2 guard (audit C4-02); the perpendicular TRUNK shapes cover it.
        if (a_min == a_max) return;
        int a_backbone = (a_min + a_max) / 2;
        t.segments.push_back(axis.mkseg(a_min, p_t1, a_max, p_t1, rung_layer));
        t.segments.push_back(axis.mkseg(a_min, p_t2, a_max, p_t2, rung_layer));
        t.segments.push_back(axis.mkseg(a_backbone, p_t1, a_backbone, p_t2, perp_layer));
        int m_stub = floorplan_.get_min_stub_length(stub_dir, perp_layer);
        for (int i = 0; i < (int)blocks.size(); ++i) {
            int pt    = (axis.perp(pins[i]) <= p_mid) ? p_t1 : p_t2;
            int src_p = axis.perp_face(blocks[i].orig_bbox, pt);
            if (std::abs(pt - src_p) >= m_stub) {
                emit_tap_segment(t, axis.mkseg(axis.along(pins[i]), src_p,
                                               axis.along(pins[i]), pt, perp_layer),
                                 &blocks[i]);
            } else if (pt != src_p) {
                return;   // a stub too short → this legacy BITRUNK is not viable
            }
        }
        // Give the two rung trunks + backbone a seg_busterms entry (the leaf
        // stubs are seeded above) so ConnTopology uses the authoritative path for
        // EVERY segment, never the geometric fallback.  These trunk endpoints tap
        // no block — they are free ends or wire junctions — so their entries stay
        // null/null and the backbone↔rung and stub↔rung joins are inferred as
        // SEG.  We deliberately do NOT call annotate_endpoints here: it would
        // geometrically fill a trunk endpoint that coincidentally grazes a
        // neighbour block face, turning a junction into a spurious busterm (the
        // very corner-feedthru this effort removes).
        for (size_t i = 0; i < t.segments.size(); ++i) (void)t.seg_busterms[i];
        results.push_back(std::move(t));
    };
    emit_legacy_bitrunk(true);    // BITRUNK_H — always-on (grandfathered shape)

    // New two-level BITRUNK_HVH / BITRUNK_VHV trees AND the legacy BITRUNK_V
    // mirror — all opt-in only.  BITRUNK_V is a measured QoR net-negative
    // on-by-default (corpus: unplaced +594, runtime +35% — like BITRUNK_H it is
    // realization-fragile and the planner over-selects it), so it rides the same
    // `multi_trunk` opt-in as the two-level trees rather than the always-on H
    // path.  It fills the row-of-receivers gap only for callers who ask for the
    // extra datapath shapes.
    if (!allow_multi_trunk_) return;   // need enough fan-out for a ≥2-branch tree
    emit_legacy_bitrunk(false);   // BITRUNK_V (row of receivers) — opt-in mirror

    // Split leaf indices into K clusters by a per-leaf key, cutting at the K-1
    // largest gaps in the sorted keys (natural columns/rows of a datapath).
    auto cluster = [&](const std::vector<int>& key, int K) {
        std::vector<int> idx(n);
        std::iota(idx.begin(), idx.end(), 0);
        std::sort(idx.begin(), idx.end(), [&](int a, int b){ return key[a] < key[b]; });
        std::vector<std::pair<int,int>> gaps;   // (gap size, split-after sorted pos)
        for (int j = 0; j + 1 < n; ++j) gaps.push_back({key[idx[j+1]] - key[idx[j]], j});
        std::sort(gaps.begin(), gaps.end(), [](auto& a, auto& b){ return a.first > b.first; });
        std::set<int> splits;
        for (int k = 0; k < K - 1 && k < (int)gaps.size(); ++k)
            if (gaps[k].first > 0) splits.insert(gaps[k].second);
        std::vector<std::vector<int>> out;
        std::vector<int> cur;
        for (int j = 0; j < n; ++j) {
            cur.push_back(idx[j]);
            if (splits.count(j)) { out.push_back(cur); cur.clear(); }
        }
        if (!cur.empty()) out.push_back(cur);
        return out;
    };

    const int m_along_h = floorplan_.get_min_stub_length(0 /*H*/, h_layer_);
    const int m_along_v = floorplan_.get_min_stub_length(1 /*V*/, v_layer_);

    // Emit one BITRUNK candidate.  root_horiz picks the root spine orientation;
    // the branches and leaf stubs are then the opposite orientation.  Coordinate
    // helpers read the "root axis" (RA: x if root_horiz) and "perp axis" (PA).
    auto emit = [&](bool root_horiz, int K) {
        // The root-axis (RA) / perp-axis (PA) coordinate helpers now delegate to
        // the shared Axis abstraction (topology.h) — same orig_bbox arithmetic,
        // one definition.  root_horiz==true ⇒ root spine runs along x.
        const Axis axis{root_horiz};
        auto RA  = [&](const Busterm& b){ return axis.along_center(b.orig_bbox); };
        auto PA  = [&](const Busterm& b){ return axis.perp_center(b.orig_bbox); };
        auto RA1 = [&](const Busterm& b){ return axis.along_lo(b.orig_bbox); };
        auto RA2 = [&](const Busterm& b){ return axis.along_hi(b.orig_bbox); };
        auto PA1 = [&](const Busterm& b){ return axis.perp_lo(b.orig_bbox); };
        auto PA2 = [&](const Busterm& b){ return axis.perp_hi(b.orig_bbox); };
        auto RAface = [&](const Busterm& b, int toward){ return axis.along_face(b.orig_bbox, toward); };
        auto mkseg = [&](int ra1, int pa1, int ra2, int pa2, int layer){
            return axis.mkseg(ra1, pa1, ra2, pa2, layer); };

        std::vector<int> key(n);
        for (int i = 0; i < n; ++i) key[i] = RA(blocks[i]);
        auto clusters = cluster(key, K);
        if ((int)clusters.size() < 2) return;   // a single branch is just a trunk

        // Root spine sits just outside the blocks on the low perp side, clear of
        // every block, so it never accidentally taps one.
        int pa_min = INT_MAX;
        for (const auto& b : blocks) pa_min = std::min(pa_min, PA1(b));
        const int stub_perp = root_horiz ? m_along_v : m_along_h;   // leaf-stub axis min
        const int stub_root = root_horiz ? m_along_h : m_along_v;   // root/branch axis min
        int root_perp = pa_min - std::max(stub_perp, 1);

        const int root_layer   = root_horiz ? h_layer_ : v_layer_;
        const int branch_layer = root_horiz ? v_layer_ : h_layer_;

        Topology t;
        t.type = root_horiz ? "BITRUNK_HVH" : "BITRUNK_VHV";
        std::vector<int> branch_ra;   // root-axis position of each branch

        for (auto& cl : clusters) {
            // Branch position = mean root-axis centre of its leaves.  A leaf whose
            // root-axis range straddles the branch is a pass-through (no stub); the
            // rest get a leaf stub.  Branch perp-span covers every cluster block's
            // full perp extent (so pass-throughs are covered) and reaches the root.
            long sum = 0;
            for (int i : cl) sum += RA(blocks[i]);
            int b_ra = (int)(sum / (int)cl.size());
            int b_pa_lo = root_perp, b_pa_hi = INT_MIN;
            for (int i : cl) { b_pa_lo = std::min(b_pa_lo, PA1(blocks[i]));
                               b_pa_hi = std::max(b_pa_hi, PA2(blocks[i])); }
            if (b_pa_hi <= b_pa_lo) return;
            int branch_idx = (int)t.segments.size();
            t.segments.push_back(mkseg(b_ra, b_pa_lo, b_ra, b_pa_hi, branch_layer));
            branch_ra.push_back(b_ra);

            for (int i : cl) {
                const Busterm& b = blocks[i];
                // Inclusive straddle (audit C4-05): a branch landing exactly on
                // a leaf's along-edge is an edge-riding pass-through covered by
                // the branch span — matching add_trunk's inclusive convention.
                // The strict form computed a zero-length 'stub' there and the
                // < stub_root check silently dropped the WHOLE candidate.
                bool straddle = (b_ra >= RA1(b) && b_ra <= RA2(b));
                if (straddle) continue;   // pass-through: branch covers this block
                int face = RAface(b, b_ra);
                if (std::abs(face - b_ra) < stub_root) return;   // stub too short → drop
                int lp = PA(b);            // leaf connects at its perp centre
                emit_tap_segment(t, mkseg(face, lp, b_ra, lp, root_layer), &b);
                (void)branch_idx;
            }
        }
        // Root spine spans the extreme branch positions at root_perp.
        int r_lo = *std::min_element(branch_ra.begin(), branch_ra.end());
        int r_hi = *std::max_element(branch_ra.begin(), branch_ra.end());
        if (r_hi - r_lo < stub_root) return;
        // Prepend the root spine at index 0, shifting the leaf-stub seg_busterms
        // keys up by one (see prepend_segment in topology.h).
        prepend_segment(t, mkseg(r_lo, root_perp, r_hi, root_perp, root_layer));

        for (const auto& b : blocks) t.connected_block_names.push_back(b.block_name);
        // Give the root spine + branch trunks a seg_busterms entry (leaf stubs are
        // seeded above) so ConnTopology uses the authoritative path for EVERY
        // segment — the clean-tree gate below and all downstream stages read the
        // annotation, never the geometric fallback (docs/internal/
        // single_source_topo_truth.md).  These trunk endpoints tap no block: they
        // are branch↔root / stub↔branch wire junctions or free column ends, so
        // their entries stay null/null (the joins are inferred as SEG).  A
        // pass-through leaf is covered by connected_block_names + span, not an
        // endpoint busterm.  We deliberately do NOT call annotate_endpoints: it
        // would geometrically fill a trunk endpoint grazing a block face, turning
        // a junction into a spurious feedthru busterm.
        for (size_t i = 0; i < t.segments.size(); ++i) (void)t.seg_busterms[i];
        // Only keep a physically self-connected, acyclic, fully-covered tree.
        if (!topology_is_clean_tree(t, floorplan_)) return;
        // Skip a duplicate (different K can yield the same tree).
        auto same_geo = [](const Topology& a, const Topology& b) {
            if (a.type != b.type || a.segments.size() != b.segments.size()) return false;
            for (size_t i = 0; i < a.segments.size(); ++i) {
                const auto& sa = a.segments[i]; const auto& sb = b.segments[i];
                if (sa.start.x != sb.start.x || sa.start.y != sb.start.y ||
                    sa.end.x   != sb.end.x   || sa.end.y   != sb.end.y) return false;
            }
            return true;
        };
        for (const auto& e : results) if (same_geo(e, t)) return;
        results.push_back(std::move(t));
    };

    for (int K = 2; K <= 3; ++K) {
        emit(true,  K);   // BITRUNK_HVH (root H)
        emit(false, K);   // BITRUNK_VHV (root V)
    }
}

void TopologyGenerator::finalize_candidates(std::vector<Topology>& candidates,
                                            const std::vector<std::string>& block_names) {
    // One-time seg-to-seg annotation (topo-truth Phase 4): every candidate's
    // busterm taps are final here (each path's emitters annotate their own),
    // so derive the junction records ONCE; downstream ConnTopology builds
    // (filter_pinched below, planner, NUTS, DNUTS, verify) only read them.
    for (auto& t : candidates) annotate_seg_conns(t);
    // NOTE: the WL sort is intentionally NOT done here.  Each caller runs one
    // annotate_and_sort as its final step — generate_2pin after its abutment/
    // corner rescue (so the rescue candidates are ranked too), generate_npin
    // right after this call.  The culls below are order-independent, so the
    // deferral does not change which candidates survive or their final order.

    // Keepout cull: drop a candidate any of whose segments has its WHOLE
    // perpendicular slide window blocked on all same-direction layers by
    // explicit keepout zones.  Slide-aware (Codex #234): a segment that
    // merely grazes a narrow keepout at its nominal position can slide past
    // it at NUTS time and must survive; only an EXHAUSTED window — no free
    // position on any layer — kills the candidate.  OOB/U segments outside
    // the zones are unaffected.
    const auto& kos = floorplan_.get_keepout_zones();
    if (!kos.empty()) {
        std::vector<Topology> kept;
        kept.reserve(candidates.size());
        for (auto& t : candidates) {
            ConnTopology ct;
            ct.build(t, floorplan_);   // cached analysis; filter_pinched reuses it
            const auto& css = ct.segs();
            bool dead = false;
            for (size_t i = 0; i < t.segments.size(); ++i) {
                const Segment& seg = t.segments[i];
                const bool is_h = (seg.start.y == seg.end.y);
                const std::vector<int>& layers = is_h ? all_h_layers_ : all_v_layers_;
                // Slide window from the shared analysis; fall back to the
                // nominal position (a zero-width window) if unavailable.
                int w_lo, w_hi;
                if (i < css.size()) {
                    w_lo = css[i].perp_lo;
                    w_hi = css[i].perp_hi;
                } else {
                    w_lo = w_hi = is_h ? seg.start.y : seg.start.x;
                }
                if (all_layers_blocked_across_slide(seg, w_lo, w_hi, layers, kos)) {
                    dead = true;
                    break;
                }
            }
            if (!dead) kept.push_back(std::move(t));
        }
        candidates = std::move(kept);
    }

    filter_pinched(candidates);
    // NOTE: the contract stamp deliberately TRAILS filter_pinched.  The
    // analysis' pass-through tightening reads connected_block_names, so the
    // pinch gate historically evaluated a PRE-contract (wider) window than the
    // one downstream stages see — and several checked-in default pools contain
    // candidates whose post-contract window is degenerate (e.g. rnr/mix
    // bundles 33/35: stub slide [1530,1530]) that today's flows route anyway.
    // Moving the stamp above the culls was measured to change those default
    // pools (mix golden), so the stricter POST-contract pinch is applied only
    // to the hanan_loci-only trunk candidates (generate_npin's loci gate
    // below), where the degenerate abutment-line spines actually arise.
    for (auto& t : candidates)
        if (t.connected_block_names.empty())
            t.connected_block_names = block_names;
}

std::vector<Topology> TopologyGenerator::generate_candidates(
    const std::string& src_name,
    const std::vector<std::string>& dst_names)
{
    std::vector<Topology> candidates;
    bool two_pin = false;
    if (dst_names.size() == 1) {
        bool any_multi_rect = !floorplan_.get_block_rects(src_name).empty() ||
                              !floorplan_.get_block_rects(dst_names[0]).empty();
        two_pin = !any_multi_rect;
    }
    candidates = two_pin ? generate_2pin(src_name, dst_names[0])
                         : generate_npin(src_name, dst_names);
    // Uniform coverage gate — one place, so every generation path (2-pin,
    // trunk, MST, BITRUNK) and every caller (flat/hier CLI, direct API) is
    // covered.  Structural per-path guards (topology_is_clean_tree, the
    // add_trunk_v stub-suppression) remain the first line; this is the backstop
    // that keeps an uncovered candidate from ever reaching the planner.
    filter_uncovered(candidates);
    // Realization guard on top of the nominal coverage gate: a BITRUNK whose
    // endpoint block is only grazed by a free-sliding trunk passes check_topo
    // at nominal but opens at NUTS time (BUSTERM_OPEN — bigHalf bus_038).
    filter_unanchored_bitrunk(candidates);
    return candidates;
}

void TopologyGenerator::filter_uncovered(std::vector<Topology>& candidates) const {
    if (candidates.empty()) return;
    // Pass 1: mark. (No moves here — the common case is zero drops and the
    // list must come back untouched.)
    //
    // Three silent-open risks the planner must not be able to pick when a
    // buildable alternative exists:
    //   * BUSTERM_OPEN   — an uncovered block (a silent open).  Always dropped.
    //   * DISCONNECTED   — the wire graph splits into 2+ electrically separate
    //     islands (the hanan_loci face-coincident-locus family; the SAME island
    //     computation check_topo's detect_disconnected runs, so gate and audit
    //     can never diverge).  A missing island means LESS wire and FEWER
    //     opens, so these sort FIRST and optimization CONVERGES onto them —
    //     always dropped, like BUSTERM_OPEN.
    //   * FEEDTHRU_RELAY — the legacy multi-rect / rootless trunk+MST fallback
    //     whose incident wires do not physically touch (a silent feedthru relay
    //     no downstream stage catches).  Dropped too — BUT only when at least
    //     one clean candidate (neither open nor relay) survives, so a bundle
    //     whose ONLY options are relays is never stranded: it stays flagged for
    //     check_connectivity / dump_topologies, exactly as before.
    std::vector<char> is_open(candidates.size(), 0), is_relay(candidates.size(), 0),
                      is_disc(candidates.size(), 0);
    int n_clean = 0;
    std::string first_block, first_type;
    for (size_t i = 0; i < candidates.size(); ++i) {
        ConnTopology ct;
        ct.build(candidates[i], floorplan_);
        bool any_viol = false;
        for (const auto& v : check_topo(ct, candidates[i], floorplan_, -1).violations) {
            any_viol = true;
            if (v.kind == ViolationKind::BUSTERM_OPEN) {
                if (!is_open[i] && first_type.empty()) {
                    first_block = v.block_name;
                    first_type  = candidates[i].type;
                }
                is_open[i] = 1;
            } else if (v.kind == ViolationKind::DISCONNECTED) {
                // Declared-feedthru exemption, scoped to the islands the
                // declared block(s) actually bridge (Codex P2 on #335): a
                // fed-through block's internal routing bridges its split
                // spine (and any stub landing in the split gap), which
                // detect_disconnected does not model — e.g. the TRUNK_H
                // feedthru-through-'mid' candidates the collinear-merge tests
                // pin have always been flagged here and still route.  Exempt
                // ONLY when EVERY island touches a declared feedthru block
                // (disconnected_islands_bridged — the same island union-find
                // detect_disconnected runs); a candidate that ALSO carries an
                // unrelated island touching no declared block is a genuine
                // open and is dropped like any other DISCONNECTED candidate.
                if (!disconnected_islands_bridged(ct, candidates[i], floorplan_))
                    is_disc[i] = 1;
            } else if (v.kind == ViolationKind::FEEDTHRU_RELAY) {
                is_relay[i] = 1;
            }
        }
        // "Clean" = NO violation of any kind — a candidate carrying only
        // SEG_OPEN / BUSTERM_FACE (differently broken) must not count as the
        // buildable alternative that justifies dropping every relay.
        if (!any_viol) ++n_clean;
    }
    // Relays are only droppable when a buildable alternative remains.
    const bool drop_relays = (n_clean > 0);
    std::vector<char> drop(candidates.size(), 0);
    int dropped = 0, dropped_relay = 0, dropped_disc = 0;
    std::string first_disc_type;
    for (size_t i = 0; i < candidates.size(); ++i) {
        if (is_open[i] || is_disc[i] || (is_relay[i] && drop_relays)) {
            drop[i] = 1;
            ++dropped;
            if (is_disc[i]) {
                ++dropped_disc;
                if (first_disc_type.empty()) first_disc_type = candidates[i].type;
            }
            if (is_relay[i] && !is_open[i] && !is_disc[i]) ++dropped_relay;
        }
    }
    if (dropped == 0) return;
    if (dropped == (int)candidates.size()) {
        // Never strand a bundle: keep the (all-broken) list and let the
        // planner's ALLOW_OVERFLOW/BEST_EFFORT ladder commit one with a WARNING;
        // check_connectivity will report the open.  (Reachable only for the
        // all-open / all-disconnected case — relays are dropped solely when a
        // clean one survives.)
        notes() << "[TopoGen] WARNING: all " << candidates.size()
                  << " candidate(s) are broken (";
        if (!first_block.empty())
            notes() << "block '" << first_block << "' unconnected";
        if (dropped_disc > 0)
            notes() << (first_block.empty() ? "" : "; ")
                      << dropped_disc << " with a disconnected wire graph";
        notes() << "); keeping them (check_connectivity will report the "
                     "violations).\n";
        return;
    }
    // Pass 2: rebuild without the dropped candidates.
    std::vector<Topology> kept;
    kept.reserve(candidates.size() - dropped);
    for (size_t i = 0; i < candidates.size(); ++i)
        if (!drop[i]) kept.push_back(std::move(candidates[i]));
    notes() << "[TopoGen] dropped " << dropped << " candidate(s) "
              << "(" << dropped_relay << " feedthru-relay";
    if (dropped_disc > 0)
        notes() << ", " << dropped_disc << " disconnected islands"
                  << " (first: " << first_disc_type << ")";
    if (!first_type.empty())
        notes() << ", first open: " << first_type << " missing block '"
                  << first_block << "'";
    notes() << "); " << kept.size() << " remain.\n";
    candidates = std::move(kept);
}

void TopologyGenerator::filter_unanchored_bitrunk(
    std::vector<Topology>& candidates) const {
    if (candidates.empty()) return;
    std::vector<char> drop(candidates.size(), 0);
    int n_clean = 0, dropped = 0;
    std::string first_type, first_block;
    for (size_t i = 0; i < candidates.size(); ++i) {
        const Topology& t = candidates[i];
        // Scoped to the LEGACY BITRUNK_H / BITRUNK_V shapes (exact match), so
        // every other pool is byte-identical.  The two-level BITRUNK_HVH/VHV
        // trees are deliberately EXCLUDED: their branch trunks cover a column of
        // aligned blocks as a genuine MULTI-TAP pass-through (no per-block stub,
        // by design), so "no busterm tap" is normal for them and their own
        // topology_is_clean_tree gate already validates connectivity/coverage.
        // The legacy ladders have no multi-tap logic — an un-tapped endpoint is
        // always the face-on-trunk edge-graze that opens at DNUTS.
        if (t.type != "BITRUNK_H" && t.type != "BITRUNK_V") { ++n_clean; continue; }
        ConnTopology ct;
        ct.build(candidates[i], floorplan_);
        const auto& segs = ct.segs();
        // An endpoint block is safely connected only if it carries a BUSTERM
        // tap (a perpendicular STUB landing on its face — the same
        // `explicitly_connected` set verify's per-bit dnuts audit exempts).  A
        // block with NO tap is "covered" only by a trunk passing over it, which
        // works solely if the whole bit-band physically fits inside the block —
        // a realization property NUTS decides, invisible here.  The legacy
        // BITRUNK_H leaves face-on-trunk blocks entirely untapped (bigHalf
        // bus_038: all 4 endpoints untapped → every bit opens at dnuts).  Drop
        // such a candidate so the planner falls to an anchored shape.
        std::set<std::string> tapped;
        for (const auto& cs : segs)
            for (const auto& c : cs.conns)
                if (c.kind == SegConn::BUSTERM) tapped.insert(c.block_name);
        // Trigger only on the PROVABLY-broken degenerate case: NO endpoint block
        // is anchored at all (every block is a free-sliding trunk graze, bigHalf
        // bus_038's tapped={}).  A partially-stubbed BITRUNK_H (some blocks off
        // the trunk lines got real stubs) is left alone — its pass-through
        // coverage of the remaining blocks may still fit the bit-band, a
        // realization property this generation-time gate cannot decide, so we
        // conservatively defer to NUTS rather than risk dropping a routable
        // small-bus column datapath.
        bool bad = tapped.empty();
        std::string bad_block =
            bad && !t.connected_block_names.empty() ? t.connected_block_names[0]
                                                    : std::string();
        if (bad) {
            drop[i] = 1;
            ++dropped;
            if (first_type.empty()) { first_type = t.type; first_block = bad_block; }
        } else {
            ++n_clean;
        }
    }
    // Nothing to drop → leave `candidates` untouched (must NOT std::move it into
    // a discarded `kept` first — that would empty every pool with no BITRUNK to
    // drop).  Also never strand a bundle: keep an all-unanchored list.
    if (dropped == 0 || n_clean == 0) return;
    std::vector<Topology> kept;
    for (size_t i = 0; i < candidates.size(); ++i) {
        if (drop[i]) continue;
        kept.push_back(std::move(candidates[i]));
    }
    notes() << "[TopoGen] dropped " << dropped
              << " unanchored BITRUNK candidate(s) (endpoint block has no "
                 "busterm tap → per-bit BUSTERM_OPEN at DNUTS; first: "
              << first_type << " block '" << first_block << "'); "
              << kept.size() << " remain.\n";
    candidates = std::move(kept);
}

std::vector<Topology> TopologyGenerator::generate_2pin(const std::string& src_name, const std::string& dst_name) {
    std::vector<Topology> candidates;
    auto mk_bt = [&](const std::string& n) {
        auto cm = floorplan_.get_block_corner_margin(n);
        Rect orig = floorplan_.get_block_bounds(n);
        return Busterm{n, orig.shrink(cm.dx, cm.dy), orig,
                       floorplan_.get_block_rects(n),
                       floorplan_.get_block_teg_mode(n)};
    };
    Busterm src_bt = mk_bt(src_name);
    Busterm dst_bt = mk_bt(dst_name);
    const Rect& src = src_bt.bbox;
    const Rect& dst = dst_bt.bbox;
    const Rect& s_orig = src_bt.orig_bbox;
    const Rect& d_orig = dst_bt.orig_bbox;

    // Direct I_H/I_V: when bbox y- or x-ranges overlap, a single-segment connection
    // between the facing faces is valid and not covered by L/Z/U shapes (which all
    // require at least one bend).  Only in the single-rect 2-pin path; the npin path
    // uses TRUNK_H/V which subsume this case.
    if (use_busterm_) {
        int xo_lo = std::max(src.x1, dst.x1), xo_hi = std::min(src.x2, dst.x2);
        if (xo_lo < xo_hi) {
            int x_mid  = (xo_lo + xo_hi) / 2;
            int src_y  = s_orig.face_y(dst.center().y);
            int dst_y  = d_orig.face_y(src.center().y);
            if (src_y != dst_y) {
                Topology t; t.type = "I_V";
                t.segments.push_back(make_seg(x_mid, src_y, x_mid, dst_y, v_layer_));
                candidates.push_back(t);
            }
        }
        int yo_lo = std::max(src.y1, dst.y1), yo_hi = std::min(src.y2, dst.y2);
        if (yo_lo < yo_hi) {
            int y_mid  = (yo_lo + yo_hi) / 2;
            int src_x  = s_orig.face_x(dst.center().x);
            int dst_x  = d_orig.face_x(src.center().x);
            if (src_x != dst_x) {
                Topology t; t.type = "I_H";
                t.segments.push_back(make_seg(src_x, y_mid, dst_x, y_mid, h_layer_));
                candidates.push_back(t);
            }
        }
    }

    add_l_shapes(src_bt, dst_bt, candidates);
    // Partially-overlapping endpoint blocks: add the two free-corner L's that route
    // around the overlap (add_l_shapes' centre-projection degenerates these away).
    add_overlap_corner_ls(src_bt, dst_bt, candidates);
    std::vector<int> hanan_x, hanan_y;
    {
        std::vector<Rect> hr;
        for (const Rect& r : bt_all_rects(src_bt)) hr.push_back(r);
        for (const Rect& r : bt_all_rects(dst_bt)) hr.push_back(r);
        bundle_hanan_grid(hr, hanan_x, hanan_y);
    }
    // Mirror generate_npin: add keepout bbox edges so OOB trunk margins
    // extend beyond keepout boundaries rather than landing inside them.
    {
        const auto& kos_2pin = floorplan_.get_keepout_zones();
        if (!kos_2pin.empty()) {
            for (const auto& koz : kos_2pin) {
                hanan_x.push_back(koz.bbox.x1); hanan_x.push_back(koz.bbox.x2);
                hanan_y.push_back(koz.bbox.y1); hanan_y.push_back(koz.bbox.y2);
            }
            auto su = [](std::vector<int>& v) {
                std::sort(v.begin(), v.end());
                v.erase(std::unique(v.begin(), v.end()), v.end());
            };
            su(hanan_x); su(hanan_y);
        }
    }

    std::vector<int> chan_x, chan_y;
    for (int i = 0; i + 1 < (int)hanan_x.size(); ++i)
        chan_x.push_back((hanan_x[i] + hanan_x[i+1]) / 2);
    for (int i = 0; i + 1 < (int)hanan_y.size(); ++i)
        chan_y.push_back((hanan_y[i] + hanan_y[i+1]) / 2);

    {
        int m_h = floorplan_.get_min_stub_length(0 /*HORIZONTAL*/, h_layer_);
        int m_v = floorplan_.get_min_stub_length(1 /*VERTICAL*/,   v_layer_);
        const auto& dc = floorplan_.get_detour_channel();
        if (hanan_x.size() >= 2) {
            int auto_m = std::max({m_h, 1, (int)(0.1 * (hanan_x.back() - hanan_x[0]))});
            int mw = (dc.west  >= 0) ? dc.west  : auto_m;
            int me = (dc.east  >= 0) ? dc.east  : auto_m;
            chan_x.insert(chan_x.begin(), hanan_x[0]       - mw);
            chan_x.push_back             (hanan_x.back()   + me);
        }
        if (hanan_y.size() >= 2) {
            int auto_m = std::max({m_v, 1, (int)(0.1 * (hanan_y.back() - hanan_y[0]))});
            int ms = (dc.south >= 0) ? dc.south : auto_m;
            int mn = (dc.north >= 0) ? dc.north : auto_m;
            chan_y.insert(chan_y.begin(), hanan_y[0]       - ms);
            chan_y.push_back             (hanan_y.back()   + mn);
        }
    }

    add_z_shapes(src_bt, dst_bt, chan_x, chan_y, candidates);
    // Overlapping endpoint blocks: the generic U's cross a block and double back to
    // tap it (a useless route), so replace them with corner-wrapping U_OVL's that
    // detour around B (add_overlap_corner_us emits the UU_OVL's too under
    // double_detour).  Disjoint blocks keep the ordinary U/UU detours.  Test the
    // margin-inset `bbox` (not orig_bbox), matching add_overlap_corner_us's own
    // gate: when corner_margin shrinks the usable boxes so they no longer cross,
    // they route as disjoint blocks and must keep the generic U's — otherwise the
    // U_OVL branch fires but emits nothing (its bbox gate fails) and the bundle
    // loses every U detour.
    if (overlap_cross(src_bt.bbox, dst_bt.bbox)) {
        add_overlap_corner_us(src_bt, dst_bt, chan_x, chan_y, candidates);
    } else {
        add_u_shapes(src_bt, dst_bt, chan_x, chan_y, candidates);
        if (allow_double_detour_)
            add_uu_shapes(src_bt, dst_bt, chan_x, chan_y, candidates);
    }

    for (auto& t : candidates) annotate_endpoints(t, {src_bt, dst_bt});
    // Shared post-emission pipeline (annotate → keepout cull → pinch → coverage
    // fill).  The WL sort is deferred to the END of this function — a single
    // annotate_and_sort after the abutment/corner rescue below — so the rescue's
    // candidates are WL-ranked in the same pass (the culls are order-independent,
    // so deferring the sort leaves the normal path's surviving set and final order
    // unchanged).
    finalize_candidates(candidates, {src_name, dst_name});

    // Abutment fallback: two blocks sharing a FULL edge have coinciding facing
    // faces, so the direct I collapses to zero length and every L/Z/U stub is
    // sub-min-length (and any that survive can be culled by keepouts above),
    // leaving NO candidate and a silently unrouted bus (common for adjacent
    // macros).  When nothing else survives, realize the shared edge as a SHORT
    // wire CROSSING it, centred on the boundary — NOT the full block width.
    // Coverage is by overlap (verify's seg_spans_rect is an overlap test, not a
    // full-span test), so a short centred wire still covers both blocks, and its
    // NUTS slide window still spans the full face overlap (real track room).  The
    // along-length is the min-stub-length setting for the crossing axis — so the
    // bus occupies the minimum channel — but never below kAbutmentSpanEpsilon: a
    // zero-length wire (min-stub 0) has no conn-segs to pin it, carries no bus,
    // and cannot be placed by NUTS.  (An ALONG-edge wire — an earlier attempt — is
    // instead clamped to ZERO slide by the pass-through tighten and strands every
    // bit; see the PR #194 review.)  Evaluated AFTER the keepout cull +
    // filter_pinched so it also rescues abutments whose only other candidates
    // those filters removed; kept only when genuinely routable.  Detected on
    // orig_bbox; corner-touch / coincident share no edge → stays empty (the
    // zero-candidate warning then fires).
    if (use_busterm_) {
        const int ox_lo = std::max(s_orig.x1, d_orig.x1), ox_hi = std::min(s_orig.x2, d_orig.x2);
        const int oy_lo = std::max(s_orig.y1, d_orig.y1), oy_hi = std::min(s_orig.y2, d_orig.y2);
        const bool vshared = (s_orig.x2 == d_orig.x1 || d_orig.x2 == s_orig.x1) && oy_hi > oy_lo;
        const bool hshared = (s_orig.y2 == d_orig.y1 || d_orig.y2 == s_orig.y1) && ox_hi > ox_lo;
        // Annotate a rescue candidate, run the keepout + pinch guards, and keep it
        // only if genuinely routable.  Shared by the edge-abutment and corner-touch
        // paths (a corner L has two legs, so the keepout check is per-segment).
        auto accept_abut = [&](Topology&& t) {
            annotate_endpoints(t, {src_bt, dst_bt});
            annotate_seg_conns(t);
            t.connected_block_names = {src_name, dst_name};
            // estimated_wirelength (and the WL ranking of an asymmetric corner's
            // two strategies) is set by the single annotate_and_sort at the end of
            // generate_2pin, which now runs AFTER this rescue — so the fallback no
            // longer scores itself.
            const auto& kos = floorplan_.get_keepout_zones();
            // Build the ConnTopology once: its per-segment slide window feeds
            // BOTH the pinch test and the SLIDE-AWARE keepout gate (audit
            // C5-03).  The old nominal-only all_layers_blocked_by_keepouts
            // dropped a last-resort ABUT/CORNER candidate whenever a keepout
            // merely crossed its generated centre, even though most of its
            // slide window was clear — the main cull was already upgraded to
            // the slide-aware form (Codex #234); mirror it here.
            ConnTopology ct;
            ct.build(t, floorplan_);
            const auto& css = ct.segs();
            bool pinched = false;
            for (const auto& cs : css)
                if (cs.perp_lo == cs.perp_hi) { pinched = true; break; }
            bool blocked = false;
            if (!pinched && !kos.empty()) {
                for (size_t i = 0; i < t.segments.size(); ++i) {
                    const auto& seg = t.segments[i];
                    const bool sh = (seg.start.y == seg.end.y);
                    const std::vector<int>& layers = sh ? all_h_layers_ : all_v_layers_;
                    int w_lo, w_hi;
                    if (i < css.size()) { w_lo = css[i].perp_lo; w_hi = css[i].perp_hi; }
                    else { w_lo = w_hi = sh ? seg.start.y : seg.start.x; }
                    if (all_layers_blocked_across_slide(seg, w_lo, w_hi, layers, kos)) {
                        blocked = true; break;
                    }
                }
            }
            if (!blocked && !pinched)
                candidates.push_back(std::move(t));
        };

        // The edge-abutment crossing stays a LAST-RESORT realization, emitted
        // only when nothing else survived (see the block comment above).
        Segment es;
        bool ok = false;
        if (candidates.empty() && vshared) {  // shared VERTICAL edge → HORIZONTAL crossing wire (track axis y)
            const int E   = (s_orig.x2 == d_orig.x1) ? s_orig.x2 : d_orig.x2;
            const int y0  = (oy_lo + oy_hi) / 2;
            const int span = std::max(floorplan_.get_min_stub_length(0 /*H*/, h_layer_),
                                      kAbutmentSpanEpsilon);
            const int lo = std::max(std::min(s_orig.x1, d_orig.x1), E - span / 2);
            const int hi = std::min(std::max(s_orig.x2, d_orig.x2), E + (span - span / 2));
            es = make_seg(lo, y0, hi, y0, h_layer_);
            ok = true;
        } else if (candidates.empty() && hshared) {  // shared HORIZONTAL edge → VERTICAL crossing wire (track axis x)
            const int E   = (s_orig.y2 == d_orig.y1) ? s_orig.y2 : d_orig.y2;
            const int x0  = (ox_lo + ox_hi) / 2;
            const int span = std::max(floorplan_.get_min_stub_length(1 /*V*/, v_layer_),
                                      kAbutmentSpanEpsilon);
            const int lo = std::max(std::min(s_orig.y1, d_orig.y1), E - span / 2);
            const int hi = std::min(std::max(s_orig.y2, d_orig.y2), E + (span - span / 2));
            es = make_seg(x0, lo, x0, hi, v_layer_);
            ok = true;
        }
        if (ok) {
            const bool horiz = (es.start.y == es.end.y);
            Topology t;
            t.type = horiz ? ("ABUT_H@y" + std::to_string(es.start.y))
                           : ("ABUT_V@x" + std::to_string(es.start.x));
            t.segments.push_back(es);
            accept_abut(std::move(t));
        }

        // CORNER-DIAGONAL touch: the two blocks meet at exactly ONE point (both
        // facing projections coincide), so closest_points pins a zero-slide edge
        // that filter_pinched drops.  This is the point-contact analogue of edge
        // abutment; realize it the way the MST path already does
        // (corner_diagonal_L): an L routed AROUND the shared corner, each leg
        // tapping a real face with slide room.  Two L's (H-first / V-first) so
        // the planner picks whichever fits.  Unlike the edge-abutment crossing,
        // the corner L's are FIRST-CLASS candidates emitted whenever the corner
        // condition holds — NOT gated on an otherwise-empty list — because an
        // opt-in knob that lets other candidates survive (double_detour's UU
        // wraps clear the two blocks and pass the filters) must never make the
        // cheapest realization vanish.  The face-equality test implies zero
        // overlap on that axis, so fully-coincident / partially OVERLAPPING
        // blocks never satisfy it — with no other candidate they stay empty and
        // the zero-candidate warning fires, which is the intended behaviour.
        // (corner and vshared/hshared are mutually exclusive: an edge share
        // needs positive overlap on the other axis, a corner needs equality.)
        const bool corner = (s_orig.x2 == d_orig.x1 || d_orig.x2 == s_orig.x1)
                         && (s_orig.y2 == d_orig.y1 || d_orig.y2 == s_orig.y1);
        if (corner) {
            for (int strategy = 0; strategy <= 1; ++strategy) {
                std::vector<Segment> ls;
                corner_diagonal_L(s_orig, d_orig, strategy, h_layer_, v_layer_, ls);
                if (ls.empty()) continue;
                Topology t;
                t.type = (strategy == 0) ? "CORNER_HV" : "CORNER_VH";
                for (const auto& g : ls) t.segments.push_back(g);
                accept_abut(std::move(t));
            }
            // Corner U's: the generic add_u_shapes collapses for a corner touch
            // (both stubs aim at the other block's centre and clamp onto the
            // shared corner coordinate, so the detour trunk is zero-length and
            // the 2-segment remnant is discarded) — so the single-detour U
            // family would be missing entirely while double_detour's UU's
            // survive.  Emit one U per detour side (left/right/bottom/top of
            // the union, the same beyond-bbox channels add_u_shapes uses),
            // tapping each block's face MID on that side for maximal slide.
            const int NONE = INT_MIN;
            auto first_gt = [&](const std::vector<int>& g, int v) {
                for (int x : g) if (x > v) return x;
                return NONE;
            };
            auto last_lt = [&](const std::vector<int>& g, int v) {
                int r = NONE;
                for (int x : g) if (x < v) r = x;
                return r;
            };
            const int u_x1 = std::min(s_orig.x1, d_orig.x1), u_x2 = std::max(s_orig.x2, d_orig.x2);
            const int u_y1 = std::min(s_orig.y1, d_orig.y1), u_y2 = std::max(s_orig.y2, d_orig.y2);
            const int m_h = floorplan_.get_min_stub_length(0 /*H*/, h_layer_);
            const int m_v = floorplan_.get_min_stub_length(1 /*V*/, v_layer_);
            const int sy_mid = (s_orig.y1 + s_orig.y2) / 2, dy_mid = (d_orig.y1 + d_orig.y2) / 2;
            const int sx_mid = (s_orig.x1 + s_orig.x2) / 2, dx_mid = (d_orig.x1 + d_orig.x2) / 2;
            // (tag_prefix, detour coord, src face coord, dst face coord).
            struct CU { const char* dir; int det, sf, df; bool horiz_stubs; };
            const CU cus[] = {
                {"x", last_lt (chan_x, u_x1), s_orig.x1, d_orig.x1, true},   // left
                {"x", first_gt(chan_x, u_x2), s_orig.x2, d_orig.x2, true},   // right
                {"y", last_lt (chan_y, u_y1), s_orig.y1, d_orig.y1, false},  // bottom
                {"y", first_gt(chan_y, u_y2), s_orig.y2, d_orig.y2, false},  // top
            };
            for (const CU& c : cus) {
                if (c.det == NONE) continue;
                if (std::abs(c.det - c.sf) < (c.horiz_stubs ? m_h : m_v)) continue;
                if (std::abs(c.det - c.df) < (c.horiz_stubs ? m_h : m_v)) continue;
                Topology t;
                t.type = std::string("CORNER_U_") + (c.horiz_stubs ? "HVH@x" : "VHV@y")
                       + std::to_string(c.det);
                if (c.horiz_stubs) {   // V trunk at x=det, H stubs at each block's y-mid
                    if (std::abs(dy_mid - sy_mid) < m_v) continue;
                    t.segments.push_back(make_seg(c.sf, sy_mid, c.det, sy_mid, h_layer_));
                    t.segments.push_back(make_seg(c.det, sy_mid, c.det, dy_mid, v_layer_));
                    t.segments.push_back(make_seg(c.det, dy_mid, c.df, dy_mid, h_layer_));
                } else {               // H trunk at y=det, V stubs at each block's x-mid
                    if (std::abs(dx_mid - sx_mid) < m_h) continue;
                    t.segments.push_back(make_seg(sx_mid, c.sf, sx_mid, c.det, v_layer_));
                    t.segments.push_back(make_seg(sx_mid, c.det, dx_mid, c.det, h_layer_));
                    t.segments.push_back(make_seg(dx_mid, c.det, dx_mid, c.df, v_layer_));
                }
                accept_abut(std::move(t));
            }
        }
    }
    // Single WL-rank over the final set — normal candidates AND any abutment/corner
    // rescue appended above.  Deferring the one sort to here (rather than before the
    // culls) is what lets the rescue L's be ranked without a second sort or the
    // fallback pre-scoring itself.
    annotate_and_sort(candidates);
    return candidates;
}

void TopologyGenerator::filter_pinched(std::vector<Topology>& candidates) {
    std::vector<Topology> filtered;
    for (auto& cand : candidates) {
        ConnTopology ct;
        ct.build(cand, floorplan_);
        bool pinched = false;
        for (const auto& cs : ct.segs()) {
            // A zero-slide segment is genuinely over-constrained: NUTS has no room
            // to place the bus.  Relay JOG/extension connectors are no longer
            // pinned to a face (they get a real over-the-cell window from
            // pin_relay_tap_connectors), so any zero-slide here is a true pinch.
            if (cs.perp_lo == cs.perp_hi) {
                pinched = true;
                break;
            }
        }
        if (!pinched) {
            filtered.push_back(std::move(cand));
        }
    }
    candidates = std::move(filtered);
}

} // namespace buda
