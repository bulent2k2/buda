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
#include <cmath>
#include <climits>
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

void Floorplan::add_block(const std::string& name, int x1, int y1, int x2, int y2) {
    int nx1 = std::min(x1, x2);
    int nx2 = std::max(x1, x2);
    int ny1 = std::min(y1, y2);
    int ny2 = std::max(y1, y2);
    blocks_[name] = Rect{nx1, ny1, nx2, ny2};
}
void Floorplan::add_block_rects(const std::string& name, const std::vector<Rect>& rects,
                                 TegMode mode) {
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
}
void Floorplan::set_block_teg_mode(const std::string& name, TegMode mode) {
    teg_modes_[name] = mode;
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
}
void Floorplan::set_global_corner_margin(int dx, int dy) {
    global_corner_margin_ = BlockCornerMargin{dx, dy};
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
}
void Floorplan::add_keepout_zone(int x1, int y1, int x2, int y2, const std::vector<int>& layer_ids) {
    KeepoutZone koz;
    koz.bbox = Rect{x1, y1, x2, y2};
    for (int lid : layer_ids) koz.layer_ids.insert(lid);
    keepouts_.push_back(std::move(koz));
}
void Floorplan::set_container(const std::string& name, bool is_container) {
    if (is_container) containers_.insert(name);
    else              containers_.erase(name);
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
                    if (bx >= d_orig.x1) gen_lhv(bx);
                }
                if (dst.x2 > src.x2) {
                    int bx = std::max(s_orig.x2 + m_h, dst.x1);
                    if (bx <= d_orig.x2) gen_lhv(bx);
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
    std::sort(v.begin(), v.end(),
        [](const Topology& a, const Topology& b) {
            if (a.estimated_wirelength != b.estimated_wirelength)
                return a.estimated_wirelength < b.estimated_wirelength;
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
static Rect best_rect_for_h(const Busterm& bt, int y_trunk) {
    auto rects = bt_all_rects(bt);
    Rect best = rects[0];
    int  best_cost = std::abs(best.face_y(y_trunk) - y_trunk);
    for (size_t k = 1; k < rects.size(); ++k) {
        int cost = std::abs(rects[k].face_y(y_trunk) - y_trunk);
        if (cost < best_cost) { best_cost = cost; best = rects[k]; }
    }
    return best;
}

// Best rect for connecting bt to a V trunk at x_trunk.
static Rect best_rect_for_v(const Busterm& bt, int x_trunk) {
    auto rects = bt_all_rects(bt);
    Rect best = rects[0];
    int  best_cost = std::abs(best.face_x(x_trunk) - x_trunk);
    for (size_t k = 1; k < rects.size(); ++k) {
        int cost = std::abs(rects[k].face_x(x_trunk) - x_trunk);
        if (cost < best_cost) { best_cost = cost; best = rects[k]; }
    }
    return best;
}

static void annotate_endpoints(Topology& topo,
                                const std::vector<Busterm>& blocks) {
    for (int i = 0; i < (int)topo.segments.size(); ++i) {
        const Segment& seg = topo.segments[i];
        bool horiz = (seg.start.y == seg.end.y);
        for (const Busterm& bt : blocks) {
            // For multi-rect blocks check each individual rect so that a stubbed
            // block whose union-bbox y-range contains the trunk y is NOT falsely
            // annotated as a Direct connection.
            auto on_face = [&](const Point& P) -> bool {
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
            auto& ep = topo.seg_busterms[i];
            if (!ep.first.has_value()  && on_face(seg.start)) ep.first  = bt;
            if (!ep.second.has_value() && on_face(seg.end))   ep.second = bt;
        }
    }
}

// Remove segment `idx` from a topology, re-keying seg_busterms to the compacted
// indices (entries below idx unchanged, entries above shifted down by one).
static void erase_segment(Topology& topo, int idx) {
    topo.segments.erase(topo.segments.begin() + idx);
    std::map<int, SegEndpoints> nb;
    for (auto& [k, v] : topo.seg_busterms) {
        if (k < idx)       nb[k] = v;
        else if (k > idx)  nb[k - 1] = v;
        // k == idx is dropped
    }
    topo.seg_busterms = std::move(nb);
}

// Number of connected components of `topo` under the SEG (wire-junction)
// connections ConnTopology infers -- the connectivity the downstream stages see.
static int conn_seg_components(const Topology& topo, const Floorplan& fp) {
    ConnTopology ct;
    ct.build(topo, fp);
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
static void complete_relay_junctions(Topology& topo,
                                     const std::vector<Busterm>& blocks,
                                     const Floorplan& fp,
                                     int h_layer, int v_layer) {
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
            handled = false;   // degenerate same-perp parallel: leave to chaining
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

    for (auto& [bi, pts] : incident) {
        if (otc_handled.count(bi)) continue; // wired by the OTC extension above
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

void TopologyGenerator::add_trunk_h(const std::vector<Point>& pins,
                                     const std::vector<Busterm>& blocks,
                                     int y_trunk, bool out_of_bbox,
                                     std::vector<Topology>& results)
{
    int n = (int)pins.size();
    std::vector<int>  conn_y(n), att_x(n);
    std::vector<bool> has_stub(n);
    std::vector<Rect> best_r(n);   // best rect per block for this trunk y
    for (int i = 0; i < n; ++i) {
        best_r[i]   = best_rect_for_h(blocks[i], y_trunk);
        conn_y[i]   = use_busterm_ ? best_r[i].face_y(y_trunk) : pins[i].y;
        has_stub[i] = (conn_y[i] != y_trunk);
        // For multi-rect blocks use the best rect's centre; single-rect uses pin.
        att_x[i]    = blocks[i].rects.empty() ? pins[i].x : best_r[i].center().x;
    }

    if (use_busterm_) {
        // Enforce vertical stub length for multicast stubs.
        int m_v = floorplan_.get_min_stub_length(1 /*VERTICAL*/, v_layer_);
        for (int i = 0; i < n; ++i) {
            if (has_stub[i]) {
                if (std::abs(y_trunk - conn_y[i]) < m_v) return; // skip this trunk
            }
        }

        if (!out_of_bbox) {
            int pt_lo = INT_MIN / 2, pt_hi = INT_MAX / 2;
            bool any_pt = false;
            bool trunk_inside_direct = false;
            for (int i = 0; i < n; ++i) {
                if (!has_stub[i]) {
                    any_pt = true;
                    if (y_trunk >= best_r[i].y1 && y_trunk <= best_r[i].y2)
                        trunk_inside_direct = true;
                    pt_lo = std::max(pt_lo, best_r[i].y1);
                    pt_hi = std::min(pt_hi, best_r[i].y2);
                }
            }
            if (any_pt && pt_lo <= pt_hi && !trunk_inside_direct) {
                int n_above = 0, n_below = 0;
                for (int i = 0; i < n; ++i) {
                    if (has_stub[i]) {
                        if (conn_y[i] > y_trunk) ++n_above;
                        else                      ++n_below;
                    }
                }
                if      (n_above > 0 && n_below == 0) y_trunk = pt_hi;
                else if (n_below > 0 && n_above == 0) y_trunk = pt_lo;
                for (int i = 0; i < n; ++i) {
                    best_r[i]  = best_rect_for_h(blocks[i], y_trunk);
                    conn_y[i]  = best_r[i].face_y(y_trunk);
                    has_stub[i] = (conn_y[i] != y_trunk);
                }
            }
        }
        // Helper: x-face of best rect (or shrunk union for single-rect w/ margin).
        auto x2_of = [&](int i) { return blocks[i].rects.empty() ? blocks[i].orig_bbox.x2 : best_r[i].x2; };
        auto x1_of = [&](int i) { return blocks[i].rects.empty() ? blocks[i].orig_bbox.x1 : best_r[i].x1; };
        auto x2_shrunk = [&](int i) { return blocks[i].rects.empty() ? blocks[i].bbox.x2 : best_r[i].x2; };
        auto x1_shrunk = [&](int i) { return blocks[i].rects.empty() ? blocks[i].bbox.x1 : best_r[i].x1; };
        {
            int lo = std::min_element(att_x.begin(), att_x.end()) - att_x.begin();
            int hi = std::max_element(att_x.begin(), att_x.end()) - att_x.begin();
            if (!has_stub[lo]) att_x[lo] = x2_of(lo);
            if (!has_stub[hi]) att_x[hi] = x1_of(hi);
        }
        for (int iter = 0; iter < n; ++iter) {
            int lo = std::min_element(att_x.begin(), att_x.end()) - att_x.begin();
            int hi = std::max_element(att_x.begin(), att_x.end()) - att_x.begin();
            bool changed = false;
            if (has_stub[lo]) {
                int target = x2_shrunk(lo);
                if (target > att_x[lo]) { att_x[lo] = target; changed = true; }
            }
            if (has_stub[hi]) {
                int target = x1_shrunk(hi);
                if (target < att_x[hi]) { att_x[hi] = target; changed = true; }
            }
            if (!changed) break;
        }
        for (int iter2 = 0; iter2 < n; ++iter2) {
            int lo = std::min_element(att_x.begin(), att_x.end()) - att_x.begin();
            int hi = std::max_element(att_x.begin(), att_x.end()) - att_x.begin();
            bool changed = false;
            if (!has_stub[lo] && att_x[lo] != x2_of(lo)) {
                att_x[lo] = x2_of(lo); changed = true;
            }
            if (!has_stub[hi] && att_x[hi] != x1_of(hi)) {
                att_x[hi] = x1_of(hi); changed = true;
            }
            if (!changed) break;
        }
    }

    // Trunk-touches-block, no artificial far-edge tap (mirror of add_trunk_v; #84).
    // A no-stub block whose extent the STUB span already overlaps is touched by
    // the trunk by overlap; pull its face-pushed att_x back into the stub span so
    // the spine is not extended to manufacture an edge tap.  A block the stub span
    // is OFF (one-directional pull) keeps its near-face att_x and is tapped there.
    if (use_busterm_) {
        int stub_lo = INT_MAX, stub_hi = INT_MIN;
        for (int i = 0; i < n; ++i)
            if (has_stub[i]) {
                stub_lo = std::min(stub_lo, att_x[i]);
                stub_hi = std::max(stub_hi, att_x[i]);
            }
        if (stub_lo <= stub_hi) {
            for (int i = 0; i < n; ++i) {
                if (has_stub[i]) continue;
                if (!blocks[i].rects.empty()) continue;        // single-rect only
                int b1 = blocks[i].orig_bbox.x1, b2 = blocks[i].orig_bbox.x2;
                if (b1 <= stub_hi && b2 >= stub_lo)            // stub span overlaps block
                    att_x[i] = std::clamp(att_x[i], stub_lo, stub_hi);
            }
        }
    }

    int x_lo = INT_MAX, x_hi = INT_MIN;
    for (int i = 0; i < n; ++i) {
        x_lo = std::min(x_lo, att_x[i]); x_hi = std::max(x_hi, att_x[i]);
    }

    // ── Flexible "root" trunk under double_detour (mirror of add_trunk_v) ────
    // Minimal connecting span: each stub sits at its NATURAL centerline (block/
    // rect centre, not the wire-min face), and the spine spans from there out to
    // the NEAR x-face of every pass-through block it does not yet overlap — no
    // beyond-bbox margin.  Gives purely-pass-through trunks a valid span, keeps
    // extreme-endpoint stubs slideable, and stays tight; left unchanged without
    // double_detour.  See the add_trunk_v counterpart for the rationale.
    if (use_busterm_ && allow_double_detour_) {
        auto ctr_x = [&](int i) {
            return blocks[i].rects.empty() ? blocks[i].orig_bbox.center().x
                                           : best_r[i].center().x;
        };
        int lo = INT_MAX, hi = INT_MIN;
        for (int i = 0; i < n; ++i) {
            if (!has_stub[i]) continue;                        // stubs only
            att_x[i] = ctr_x(i);                               // place stub at its centerline
            lo = std::min(lo, att_x[i]); hi = std::max(hi, att_x[i]);
        }
        bool seeded = (lo <= hi);
        for (int i = 0; i < n; ++i) {
            if (has_stub[i]) continue;                         // pass-through/contained
            int b1 = blocks[i].orig_bbox.x1, b2 = blocks[i].orig_bbox.x2;
            if (!seeded) { lo = b1; hi = b2; seeded = true; continue; }
            if (b1 > hi) hi = b1;   // block right of the span: reach its near (left)  face
            if (b2 < lo) lo = b2;   // block left  of the span: reach its near (right) face
        }
        if (seeded && lo < hi) { x_lo = lo; x_hi = hi; }
    }
    // Degenerate spine (all real attachments share one x == the junction).  Mirror
    // of add_trunk_v (#84 refine): rather than a junction-less spine-less collapse
    // (parallel stubs separate, a silent open — Codex P1) or a block-centre anchor
    // (which dangles the far spine endpoint), SPREAD the stubs to opposite interior
    // sides of the junction — each toward its own block's side, to the midpoint
    // between the junction and the contained block's near face — and span the spine
    // between them, so both spine endpoints connect to a stub and the trunk pulls
    // inward.  Only when a contained block straddles the junction; else drop.
    if (x_lo >= x_hi) {
        const int junction = x_lo;        // == x_hi
        int n_stubs = 0, side = 0;
        bool same_side = true, ok = true, have_B = false;
        int B_x1 = INT_MIN, B_x2 = INT_MAX;   // intersection of contained blocks' extents
        for (int i = 0; i < n; ++i) {
            if (has_stub[i]) {
                ++n_stubs;
                int s = (conn_y[i] > y_trunk) ? 1 : (conn_y[i] < y_trunk ? -1 : 0);
                if (s != 0) { if (side == 0) side = s; else if (s != side) same_side = false; }
                continue;
            }
            const Rect& bb = blocks[i].orig_bbox;
            if (!blocks[i].rects.empty() || !(bb.x1 <= junction && junction <= bb.x2)) {
                ok = false; continue;
            }
            have_B = true;
            B_x1 = std::max(B_x1, bb.x1);
            B_x2 = std::min(B_x2, bb.x2);
        }
        if (!(n_stubs >= 2 && same_side && ok && have_B &&
              B_x1 < junction && junction < B_x2))
            return;
        int new_lo = INT_MAX, new_hi = INT_MIN;
        for (int i = 0; i < n; ++i) {
            if (!has_stub[i]) continue;
            // Spread to the midpoint of the stub's OWN face ∩ B's interior (mirror
            // of add_trunk_v) — keeps the stub on its own face even when B is much
            // larger than the stub block (Codex P1), and strictly inside B.
            int fx1 = blocks[i].rects.empty() ? blocks[i].orig_bbox.x1 : best_r[i].x1;
            int fx2 = blocks[i].rects.empty() ? blocks[i].orig_bbox.x2 : best_r[i].x2;
            int lo = std::max(fx1, B_x1), hi = std::min(fx2, B_x2);
            if (lo < hi) att_x[i] = (lo + hi) / 2;     // else keep att_x (the junction)
            new_lo = std::min(new_lo, att_x[i]);
            new_hi = std::max(new_hi, att_x[i]);
        }
        if (new_lo >= new_hi) return;                  // stubs did not spread (no interior room)
        x_lo = new_lo; x_hi = new_hi;                  // spine spans between the spread stubs
    }

    Topology t;
    t.type               = std::string(out_of_bbox ? "TRUNK_H_OOB" : "TRUNK_H")
                           + "@y" + std::to_string(y_trunk);
    t.trunk_location     = y_trunk;

    // Opt-in feedthru: a bundle block the trunk passes straight through (a
    // busterm of THIS bundle that gets no stub) may relay the bus across its
    // interior via its own lower-level routing -- the trunk is split at the
    // block's two faces and the block's internal route bridges the gap.  Only
    // blocks this topology actually connects to are eligible: an unrelated
    // block the trunk merely crosses is a pass-through, never a feedthru.
    // Because the block is a busterm, ConnTopology infers a BUSTERM conn at
    // each face, so the two half-spines are anchored to the block's faces (they
    // cannot slide off it or onto separate tracks) and the lower-level bridge
    // keeps two valid landings -- no separate downstream slide-clamp needed.
    // Gated on feedthru_active() (default off); single-rect blocks only (MVP).
    std::vector<std::pair<int,int>> ft_gaps;   // (x1,x2) faces of feedthru blocks
    std::vector<bool> is_feedthru(n, false);
    if (floorplan_.feedthru_active()) {
        for (int i = 0; i < n; ++i) {
            if (has_stub[i]) continue;                 // trunk doesn't pass through it
            if (!floorplan_.get_feedthru(blocks[i].block_name, h_layer_)) continue;
            if (blocks[i].rects.size() > 1) continue;  // MVP: single-rect only
            // A feedthru relay needs the trunk to PASS THROUGH the block (enter one
            // face, exit the other).  A spine fully contained in the block (the
            // bounded interior spine, #84) does not pass through, so it must not be
            // split out — that would delete the only junction (Codex P1).
            if (x_lo >= blocks[i].orig_bbox.x1 && x_hi <= blocks[i].orig_bbox.x2)
                continue;
            int fx1 = std::max(x_lo, blocks[i].orig_bbox.x1);
            int fx2 = std::min(x_hi, blocks[i].orig_bbox.x2);
            if (fx1 < fx2) {
                ft_gaps.push_back({fx1, fx2});
                is_feedthru[i] = true;
                t.feedthru_blocks.push_back(blocks[i].block_name);
            }
        }
        std::sort(t.feedthru_blocks.begin(), t.feedthru_blocks.end());
    }

    // Pass-through count excludes feedthru blocks (those are explicit splits).
    t.pass_through_count = 0;
    for (int i = 0; i < n; ++i)
        if (!has_stub[i] && !is_feedthru[i] && att_x[i] != x_lo && att_x[i] != x_hi)
            ++t.pass_through_count;

    if (x_lo < x_hi) {
        if (ft_gaps.empty()) {
            t.segments.push_back(make_seg(x_lo, y_trunk, x_hi, y_trunk, h_layer_));
        } else {
            // Split the spine, skipping each feedthru block's x-extent.
            std::sort(ft_gaps.begin(), ft_gaps.end());
            int cur = x_lo;
            for (const auto& g : ft_gaps) {
                if (cur < g.first)
                    t.segments.push_back(make_seg(cur, y_trunk, g.first, y_trunk, h_layer_));
                cur = std::max(cur, g.second);
            }
            if (cur < x_hi)
                t.segments.push_back(make_seg(cur, y_trunk, x_hi, y_trunk, h_layer_));
        }
    }

    for (int i = 0; i < n; ++i) {
        if (!has_stub[i]) {
            // Direct: trunk is inside the best rect. For OVER mode on a rectilinear
            // block (rects with overlapping interiors, e.g. L-shape), check if ALL
            // rects span y_trunk. If not, emit a bridge over the top so the parts
            // of the block that are outside the trunk's y-range are also connected.
            // Pure TEG blocks (disjoint rects) are exempt — for them, "trunk inside
            // one rect" is normal direct-connection behaviour with no bridge.
            if (blocks[i].teg_mode == TegMode::OVER && !blocks[i].rects.empty()
                    && rects_are_rectilinear(blocks[i].rects)) {
                bool all_span = true;
                for (const auto& r : blocks[i].rects)
                    if (y_trunk < r.y1 || y_trunk > r.y2) { all_span = false; break; }
                if (!all_span) {
                    const Rect& ub = blocks[i].orig_bbox;
                    t.bridge_segments[blocks[i].block_name] =
                        make_seg(ub.x1, ub.y2, ub.x2, ub.y2, h_layer_);
                }
            }
            continue;
        }

        // Over-the-block: if trunk is in the gap between rects on both sides,
        // emit two V stubs (one per side) and a horizontal bridge over the block top.
        if (blocks[i].teg_mode == TegMode::OVER && blocks[i].rects.size() >= 2) {
            const auto& rects = blocks[i].rects;
            bool trunk_inside_any = false;
            for (const auto& r : rects)
                if (y_trunk >= r.y1 && y_trunk <= r.y2) { trunk_inside_any = true; break; }

            if (!trunk_inside_any) {
                // Partition rects into those fully below and fully above trunk.
                Rect best_below = rects[0]; bool has_below = false;
                Rect best_above = rects[0]; bool has_above = false;
                for (const auto& r : rects) {
                    if (r.y2 <= y_trunk) {
                        if (!has_below || r.y2 > best_below.y2) { best_below = r; has_below = true; }
                    } else if (r.y1 >= y_trunk) {
                        if (!has_above || r.y1 < best_above.y1) { best_above = r; has_above = true; }
                    }
                }
                if (has_below && has_above) {
                    int cx_below = best_below.center().x;
                    int cx_above = best_above.center().x;

                    // V stub down: trunk → top face of lower rect
                    int idx = (int)t.segments.size();
                    t.segments.push_back(make_seg(cx_below, best_below.y2, cx_below, y_trunk, v_layer_));
                    t.seg_busterms[idx].first = blocks[i];

                    // V stub up: trunk → bottom face of upper rect
                    idx = (int)t.segments.size();
                    t.segments.push_back(make_seg(cx_above, y_trunk, cx_above, best_above.y1, v_layer_));
                    t.seg_busterms[idx].first = blocks[i];

                    // Bridge H segment at union_bbox.y2 (over the block top)
                    const Rect& ub = blocks[i].orig_bbox;
                    t.bridge_segments[blocks[i].block_name] =
                        make_seg(ub.x1, ub.y2, ub.x2, ub.y2, h_layer_);
                    continue;
                }
            }
        }

        // Normal single stub
        int seg_idx = (int)t.segments.size();
        t.segments.push_back(make_seg(att_x[i], conn_y[i], att_x[i], y_trunk, v_layer_));
        t.seg_busterms[seg_idx].first = blocks[i];
    }
    if (!t.segments.empty()) results.push_back(std::move(t));
}

void TopologyGenerator::add_trunk_v(const std::vector<Point>& pins,
                                     const std::vector<Busterm>& blocks,
                                     int x_trunk, bool out_of_bbox,
                                     std::vector<Topology>& results)
{
    int n = (int)pins.size();
    std::vector<int>  conn_x(n), att_y(n);
    std::vector<bool> has_stub(n);
    std::vector<Rect> best_r(n);   // best rect per block for this trunk x
    for (int i = 0; i < n; ++i) {
        best_r[i]   = best_rect_for_v(blocks[i], x_trunk);
        conn_x[i]   = use_busterm_ ? best_r[i].face_x(x_trunk) : pins[i].x;
        has_stub[i] = (conn_x[i] != x_trunk);
        // For multi-rect blocks use the best rect's centre; single-rect uses pin.
        att_y[i]    = blocks[i].rects.empty() ? pins[i].y : best_r[i].center().y;
    }
    std::vector<bool> stub_suppressed(n, false);

    if (use_busterm_) {
        // Enforce horizontal stub length for multicast stubs.
        int m_h = floorplan_.get_min_stub_length(0 /*HORIZONTAL*/, h_layer_);
        for (int i = 0; i < n; ++i) {
            if (has_stub[i]) {
                if (std::abs(x_trunk - conn_x[i]) < m_h) return; // skip this trunk
            }
        }

        if (!out_of_bbox) {
            int pt_lo = INT_MIN / 2, pt_hi = INT_MAX / 2;
            bool any_pt = false;
            bool trunk_inside_direct = false;
            for (int i = 0; i < n; ++i) {
                if (!has_stub[i]) {
                    any_pt = true;
                    if (x_trunk >= best_r[i].x1 && x_trunk <= best_r[i].x2)
                        trunk_inside_direct = true;
                    pt_lo = std::max(pt_lo, best_r[i].x1);
                    pt_hi = std::min(pt_hi, best_r[i].x2);
                }
            }
            if (any_pt && pt_lo <= pt_hi && !trunk_inside_direct) {
                int n_right = 0, n_left = 0;
                for (int i = 0; i < n; ++i) {
                    if (has_stub[i]) {
                        if (conn_x[i] > x_trunk) ++n_right;
                        else                      ++n_left;
                    }
                }
                if      (n_right > 0 && n_left == 0) x_trunk = pt_hi;
                else if (n_left  > 0 && n_right == 0) x_trunk = pt_lo;
                for (int i = 0; i < n; ++i) {
                    best_r[i]  = best_rect_for_v(blocks[i], x_trunk);
                    conn_x[i]  = best_r[i].face_x(x_trunk);
                    has_stub[i] = (conn_x[i] != x_trunk);
                }
            }
        }
        // Helper: y-face of best rect (or shrunk union for single-rect w/ margin).
        auto y2_of = [&](int i) { return blocks[i].rects.empty() ? blocks[i].orig_bbox.y2 : best_r[i].y2; };
        auto y1_of = [&](int i) { return blocks[i].rects.empty() ? blocks[i].orig_bbox.y1 : best_r[i].y1; };
        auto y2_shrunk = [&](int i) { return blocks[i].rects.empty() ? blocks[i].bbox.y2 : best_r[i].y2; };
        auto y1_shrunk = [&](int i) { return blocks[i].rects.empty() ? blocks[i].bbox.y1 : best_r[i].y1; };
        {
            int lo = std::min_element(att_y.begin(), att_y.end()) - att_y.begin();
            int hi = std::max_element(att_y.begin(), att_y.end()) - att_y.begin();
            if (!has_stub[lo]) att_y[lo] = y2_of(lo);
            if (!has_stub[hi]) att_y[hi] = y1_of(hi);
        }
        for (int iter = 0; iter < n; ++iter) {
            int lo = std::min_element(att_y.begin(), att_y.end()) - att_y.begin();
            int hi = std::max_element(att_y.begin(), att_y.end()) - att_y.begin();
            bool changed = false;
            if (has_stub[lo]) {
                int target = y2_shrunk(lo);
                if (target > att_y[lo]) { att_y[lo] = target; changed = true; }
            }
            if (has_stub[hi]) {
                int target = y1_shrunk(hi);
                if (target < att_y[hi]) { att_y[hi] = target; changed = true; }
            }
            if (!changed) break;
        }
        for (int iter2 = 0; iter2 < n; ++iter2) {
            int lo = std::min_element(att_y.begin(), att_y.end()) - att_y.begin();
            int hi = std::max_element(att_y.begin(), att_y.end()) - att_y.begin();
            bool changed = false;
            if (!has_stub[lo] && att_y[lo] != y2_of(lo)) {
                att_y[lo] = y2_of(lo); changed = true;
            }
            if (!has_stub[hi] && att_y[hi] != y1_of(hi)) {
                att_y[hi] = y1_of(hi); changed = true;
            }
            if (!changed) break;
        }

        // Suppress stubs made redundant by a longer same-side stub whose att_y
        // already passes through the shorter stub's block.
        //
        // Coverage-safety: a stub i may only be suppressed by a stub j that
        // ACTUALLY SURVIVES (is not itself suppressed) — otherwise i is left with
        // no covering wire (a silent open).  The original loop tested has_stub[j]
        // only, so a chain A←B←C could suppress B believing C covers it while C's
        // surviving wire sits at a different att_y that misses B (the
        // big2 bus_056 / blk_09 bug).  Decide survivors farthest-first per side:
        // the farthest stub always survives, and a nearer stub is suppressed only
        // when an already-confirmed survivor's att_y lies within its block's
        // y-extent (the same pass-through coverage verify.cpp checks).
        {
            std::vector<int> order(n);
            for (int i = 0; i < n; ++i) order[i] = i;
            // Farthest from trunk first; stubs only.
            std::sort(order.begin(), order.end(), [&](int a, int b) {
                return std::abs(conn_x[a] - x_trunk) > std::abs(conn_x[b] - x_trunk);
            });
            for (int idx = 0; idx < n; ++idx) {
                int i = order[idx];
                if (!has_stub[i]) continue;
                int di = conn_x[i] - x_trunk;
                if (di == 0) continue;
                for (int jdx = 0; jdx < idx; ++jdx) {   // only farther-or-equal blocks seen so far
                    int j = order[jdx];
                    if (!has_stub[j] || stub_suppressed[j]) continue;  // j must survive
                    int dj = conn_x[j] - x_trunk;
                    if (dj == 0) continue;
                    if ((di > 0) != (dj > 0)) continue;          // opposite sides of trunk
                    if (std::abs(dj) <= std::abs(di)) continue;  // j not strictly farther
                    // Surviving stub j's att_y lies within block i's original y-extent?
                    if (att_y[j] >= blocks[i].orig_bbox.y1 &&
                        att_y[j] <= blocks[i].orig_bbox.y2) {
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
    //     it by overlap; pull the face-pushed att_y back into the stub span so the
    //     spine is NOT extended to manufacture an edge tap (b34_bus_028 blk_00).
    //   - the stub span is OFF the block (one-directional pull) → the overlap test
    //     below is false, so att_y keeps its near-face value and the spine extends
    //     to that NEAR edge and taps it (pull-up/pull-down).
    // The degenerate sub-case (stubs collapse to one y, so the pulled-back block
    // would leave the spine zero-length) is given a bounded interior spine further
    // below — never a face tap and never a junction-less collapse.
    //
    // Busterm mode only (mirror of add_trunk_h): with busterm mode off (center_mode)
    // att_y is the point pin's y, and clamping a pin that lies outside the stub span
    // into it would drop the pin connection (Codex P2 on #88).
    if (use_busterm_) {
        int stub_lo = INT_MAX, stub_hi = INT_MIN;
        for (int i = 0; i < n; ++i)
            if (has_stub[i] && !stub_suppressed[i]) {
                stub_lo = std::min(stub_lo, att_y[i]);
                stub_hi = std::max(stub_hi, att_y[i]);
            }
        if (stub_lo <= stub_hi) {
            for (int i = 0; i < n; ++i) {
                if (has_stub[i] || stub_suppressed[i]) continue;   // stubbed/suppressed
                if (!blocks[i].rects.empty()) continue;            // single-rect only (TEG owns multi-rect)
                int b1 = blocks[i].orig_bbox.y1, b2 = blocks[i].orig_bbox.y2;
                if (b1 <= stub_hi && b2 >= stub_lo)                // stub span overlaps block
                    att_y[i] = std::clamp(att_y[i], stub_lo, stub_hi);
            }
        }
    }

    int y_lo = INT_MAX, y_hi = INT_MIN;
    for (int i = 0; i < n; ++i) {
        if (stub_suppressed[i]) continue;
        y_lo = std::min(y_lo, att_y[i]); y_hi = std::max(y_hi, att_y[i]);
    }

    // ── Flexible "root" trunk under double_detour ───────────────────────────
    // A trunk is the bundle's root: its endpoints are flexible and span exactly
    // from the lowest busterm it taps to the topmost stub/connection it carries —
    // no more (minimise wirelength) and no less (stay connected).  Opt-in via
    // double_detour.  Two pieces:
    //   1. Each stub sits at its NATURAL centerline (block/rect centre), not the
    //      wire-min face the earlier refinement may have pulled an extreme stub
    //      to.  A stub pinned to the block's near face that also bounds the spine
    //      end has zero slide (the blk_02 driver stub at x5772 → filter_pinched
    //      dropped the candidate); placing it at the centre keeps the spine end
    //      strictly inside the block's face extent so the stub stays slideable.
    //   2. The span = [min,max] of those stub centerlines, then extended to the
    //      NEAR face of every pass-through block it does not yet overlap (so it
    //      still crosses every receiver it connects by containment) — e.g. down
    //      to blk_29's upper edge, up to the driver-stub centerline.  No
    //      beyond-bbox margin: the generator is honest and tight, and NUTS
    //      endpoint-following resolves the rest.
    // Without double_detour the span is left tight (unchanged behaviour), so
    // normal flows and their candidate rankings are not disturbed.
    if (use_busterm_ && allow_double_detour_) {
        auto ctr_y = [&](int i) {
            return blocks[i].rects.empty() ? blocks[i].orig_bbox.center().y
                                           : best_r[i].center().y;
        };
        // Place every surviving stub at its natural centerline.
        for (int i = 0; i < n; ++i)
            if (has_stub[i] && !stub_suppressed[i]) att_y[i] = ctr_y(i);
        // Recentering can move a farther same-side stub off a nearer block it was
        // suppressing (that block was suppressed precisely because the farther
        // stub's pre-recenter att_y crossed its y-extent).  A suppressed block is
        // stubbed — the trunk does NOT pass through it — so if no SURVIVING farther
        // same-side stub still crosses it at the new att_y, un-suppress it and emit
        // its own centerline stub.  Monotonic: only ever re-adds coverage, so it
        // cannot strand a block (the Codex P1 on this PR).
        for (int i = 0; i < n; ++i) {
            if (!stub_suppressed[i]) continue;
            int di = conn_x[i] - x_trunk;
            bool covered = false;
            for (int j = 0; j < n && !covered; ++j) {
                if (j == i || !has_stub[j] || stub_suppressed[j]) continue;
                int dj = conn_x[j] - x_trunk;
                if (di == 0 || dj == 0 || (di > 0) != (dj > 0)) continue; // same side
                if (std::abs(dj) <= std::abs(di)) continue;               // strictly farther
                covered = (att_y[j] >= blocks[i].orig_bbox.y1 &&
                           att_y[j] <= blocks[i].orig_bbox.y2);
            }
            if (!covered) { stub_suppressed[i] = false; att_y[i] = ctr_y(i); }
        }
        int lo = INT_MAX, hi = INT_MIN;
        for (int i = 0; i < n; ++i) {
            if (stub_suppressed[i] || !has_stub[i]) continue;  // surviving stubs
            lo = std::min(lo, att_y[i]); hi = std::max(hi, att_y[i]);
        }
        bool seeded = (lo <= hi);
        for (int i = 0; i < n; ++i) {
            if (stub_suppressed[i] || has_stub[i]) continue;   // pass-through/contained
            int b1 = blocks[i].orig_bbox.y1, b2 = blocks[i].orig_bbox.y2;
            if (!seeded) { lo = b1; hi = b2; seeded = true; continue; }
            if (b1 > hi) hi = b1;   // block above the span: reach its near (bottom) face
            if (b2 < lo) lo = b2;   // block below the span: reach its near (top)    face
        }
        if (seeded && lo < hi) { y_lo = lo; y_hi = hi; }
    }
    // Degenerate spine (all real attachments share one y == the junction).  A
    // spine-less topology would leave the same-side collinear stubs joined only by
    // nominally overlapping at the junction — ConnTopology infers a SEG link only
    // for *perpendicular* pairs, so two parallel stubs carry no junction constraint
    // and NUTS could place them on different tracks → a silent open (issue #84,
    // Codex P1).  Instead, when a contained endpoint block straddles the junction,
    // SPREAD the stubs to opposite interior sides of it and span the spine between
    // them: each stub moves toward its own block's side of the junction, to the
    // midpoint between the junction and the contained block's near face on that
    // side — strictly interior (no face tap).  seg0 then runs from the low stub to
    // the high stub, so BOTH its endpoints connect to a stub (no dead wire — the
    // earlier block-centre anchor left the far endpoint dangling), and the trunk's
    // endpoints pull inward (the opposing straddle pulls, low=+1 / high=-1).  The
    // block is connected by containment (the spine lies inside it).  Otherwise drop.
    if (y_lo >= y_hi) {
        const int junction = y_lo;        // == y_hi
        int n_stubs = 0, side = 0;
        bool same_side = true, ok = true, have_B = false;
        int B_y1 = INT_MIN, B_y2 = INT_MAX;   // intersection of contained blocks' extents
        for (int i = 0; i < n; ++i) {
            if (has_stub[i] && !stub_suppressed[i]) {
                ++n_stubs;
                int s = (conn_x[i] > x_trunk) ? 1 : (conn_x[i] < x_trunk ? -1 : 0);
                if (s != 0) { if (side == 0) side = s; else if (s != side) same_side = false; }
                continue;
            }
            if (has_stub[i]) continue;                 // suppressed stub: covered by a survivor
            // no-stub (contained) block: single-rect, must straddle the junction.
            const Rect& bb = blocks[i].orig_bbox;
            if (!blocks[i].rects.empty() || !(bb.y1 <= junction && junction <= bb.y2)) {
                ok = false; continue;
            }
            have_B = true;
            B_y1 = std::max(B_y1, bb.y1);
            B_y2 = std::min(B_y2, bb.y2);
        }
        if (!(n_stubs >= 2 && same_side && ok && have_B &&
              B_y1 < junction && junction < B_y2))
            return;
        int new_lo = INT_MAX, new_hi = INT_MIN;
        for (int i = 0; i < n; ++i) {
            if (!(has_stub[i] && !stub_suppressed[i])) continue;
            // Spread to the midpoint of the stub's OWN face ∩ B's interior — never
            // computed from B's faces alone, which (when B is much larger than the
            // stub block) could place the stub off its own face → an off-face
            // BUSTERM (Codex P1).  The intersection naturally lands on the block's
            // side of the junction and stays strictly inside B (no face tap).
            int fy1 = blocks[i].rects.empty() ? blocks[i].orig_bbox.y1 : best_r[i].y1;
            int fy2 = blocks[i].rects.empty() ? blocks[i].orig_bbox.y2 : best_r[i].y2;
            int lo = std::max(fy1, B_y1), hi = std::min(fy2, B_y2);
            if (lo < hi) att_y[i] = (lo + hi) / 2;     // else keep att_y (the junction)
            new_lo = std::min(new_lo, att_y[i]);
            new_hi = std::max(new_hi, att_y[i]);
        }
        if (new_lo >= new_hi) return;                  // stubs did not spread (no interior room)
        y_lo = new_lo; y_hi = new_hi;                  // spine spans between the spread stubs
        // falls through: the spine block emits the V segment [y_lo,y_hi]; the stub
        // loop wires blk_15/blk_32; the contained block is covered by the spine.
    }

    Topology t;
    t.type               = std::string(out_of_bbox ? "TRUNK_V_OOB" : "TRUNK_V")
                           + "@x" + std::to_string(x_trunk);
    t.trunk_location     = x_trunk;

    // Opt-in feedthru (mirror of add_trunk_h): a bundle block the trunk passes
    // straight through (a busterm of THIS bundle with no stub) may relay the bus
    // across its interior; split the trunk at the block's y-faces.  Only blocks
    // this topology connects to are eligible.  Single-rect MVP.
    std::vector<std::pair<int,int>> ft_gaps;   // (y1,y2) faces of feedthru blocks
    std::vector<bool> is_feedthru(n, false);
    if (floorplan_.feedthru_active()) {
        for (int i = 0; i < n; ++i) {
            if (has_stub[i] || stub_suppressed[i]) continue;  // not passed through
            if (!floorplan_.get_feedthru(blocks[i].block_name, v_layer_)) continue;
            if (blocks[i].rects.size() > 1) continue;         // MVP: single-rect only
            // A feedthru relay needs the trunk to PASS THROUGH the block (enter one
            // face, exit the other).  A spine fully contained in the block (the
            // bounded interior spine, #84) does not pass through, so it must not be
            // split out — that would delete the only junction (Codex P1).
            if (y_lo >= blocks[i].orig_bbox.y1 && y_hi <= blocks[i].orig_bbox.y2)
                continue;
            int fy1 = std::max(y_lo, blocks[i].orig_bbox.y1);
            int fy2 = std::min(y_hi, blocks[i].orig_bbox.y2);
            if (fy1 < fy2) {
                ft_gaps.push_back({fy1, fy2});
                is_feedthru[i] = true;
                t.feedthru_blocks.push_back(blocks[i].block_name);
            }
        }
        std::sort(t.feedthru_blocks.begin(), t.feedthru_blocks.end());
    }

    // Pass-through count excludes feedthru blocks (those are explicit splits).
    t.pass_through_count = 0;
    for (int i = 0; i < n; ++i)
        if (!has_stub[i] && !is_feedthru[i] && att_y[i] != y_lo && att_y[i] != y_hi)
            ++t.pass_through_count;

    if (y_lo < y_hi) {
        if (ft_gaps.empty()) {
            t.segments.push_back(make_seg(x_trunk, y_lo, x_trunk, y_hi, v_layer_));
        } else {
            std::sort(ft_gaps.begin(), ft_gaps.end());
            int cur = y_lo;
            for (const auto& g : ft_gaps) {
                if (cur < g.first)
                    t.segments.push_back(make_seg(x_trunk, cur, x_trunk, g.first, v_layer_));
                cur = std::max(cur, g.second);
            }
            if (cur < y_hi)
                t.segments.push_back(make_seg(x_trunk, cur, x_trunk, y_hi, v_layer_));
        }
    }

    for (int i = 0; i < n; ++i) {
        if (!has_stub[i]) {
            // Direct: trunk is inside the best rect. For OVER mode on a rectilinear
            // block (rects with overlapping interiors, e.g. L-shape), check if ALL
            // rects span x_trunk. If not, emit a bridge over the top.
            // Pure TEG blocks (disjoint rects) are exempt.
            if (blocks[i].teg_mode == TegMode::OVER && !blocks[i].rects.empty()
                    && rects_are_rectilinear(blocks[i].rects)) {
                bool all_span = true;
                for (const auto& r : blocks[i].rects)
                    if (x_trunk < r.x1 || x_trunk > r.x2) { all_span = false; break; }
                if (!all_span) {
                    const Rect& ub = blocks[i].orig_bbox;
                    t.bridge_segments[blocks[i].block_name] =
                        make_seg(ub.x2, ub.y1, ub.x2, ub.y2, v_layer_);
                }
            }
            continue;
        }

        // Over-the-block for V trunk: trunk in horizontal gap between rects
        if (blocks[i].teg_mode == TegMode::OVER && blocks[i].rects.size() >= 2) {
            const auto& rects = blocks[i].rects;
            bool trunk_inside_any = false;
            for (const auto& r : rects)
                if (x_trunk >= r.x1 && x_trunk <= r.x2) { trunk_inside_any = true; break; }

            if (!trunk_inside_any) {
                Rect best_left = rects[0]; bool has_left = false;
                Rect best_right = rects[0]; bool has_right = false;
                for (const auto& r : rects) {
                    if (r.x2 <= x_trunk) {
                        if (!has_left || r.x2 > best_left.x2) { best_left = r; has_left = true; }
                    } else if (r.x1 >= x_trunk) {
                        if (!has_right || r.x1 < best_right.x1) { best_right = r; has_right = true; }
                    }
                }
                if (has_left && has_right) {
                    int cy_left  = best_left.center().y;
                    int cy_right = best_right.center().y;

                    // H stub left: trunk → right face of left rect
                    int idx = (int)t.segments.size();
                    t.segments.push_back(make_seg(best_left.x2, cy_left, x_trunk, cy_left, h_layer_));
                    t.seg_busterms[idx].first = blocks[i];

                    // H stub right: trunk → left face of right rect
                    idx = (int)t.segments.size();
                    t.segments.push_back(make_seg(x_trunk, cy_right, best_right.x1, cy_right, h_layer_));
                    t.seg_busterms[idx].first = blocks[i];

                    // Bridge V segment at union_bbox.x2 (right outer face)
                    const Rect& ub = blocks[i].orig_bbox;
                    t.bridge_segments[blocks[i].block_name] =
                        make_seg(ub.x2, ub.y1, ub.x2, ub.y2, v_layer_);
                    continue;
                }
            }
        }

        // Normal single stub — skip if made redundant by a longer stub's pass-through
        if (stub_suppressed[i]) continue;
        int seg_idx = (int)t.segments.size();
        t.segments.push_back(make_seg(conn_x[i], att_y[i], x_trunk, att_y[i], h_layer_));
        t.seg_busterms[seg_idx].first = blocks[i];
    }
    if (!t.segments.empty()) results.push_back(std::move(t));
}

bool TopologyGenerator::segment_blocked_on_all_layers(const Segment& seg) const {
    bool is_h = (seg.start.y == seg.end.y);
    const std::vector<int>& layers = is_h ? all_h_layers_ : all_v_layers_;
    return all_layers_blocked_by_keepouts(seg, layers, floorplan_.get_keepout_zones());
}

// ---------------------------------------------------------------------------
// Multi-pin topology generation
// ---------------------------------------------------------------------------

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

    bool has_multi_rect = false;
    for (const auto& bt : blocks)
        if (!bt.rects.empty()) { has_multi_rect = true; break; }

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
    add_trunk_mst_candidates(blocks, results);
    add_mst_candidates(blocks, results);
    add_multi_trunk_candidates(pins, blocks, results);
    annotate_and_sort(results);
    filter_pinched(results);
    for (auto& t : results)
        if (t.connected_block_names.empty())
            for (const auto& b : blocks)
                t.connected_block_names.push_back(b.block_name);
    return results;
}

// Is `topo` one connected component under ConnTopology's SEG junctions?  Unlike
// topology_is_clean_tree this does NOT reject cycles -- a collinear OVERLAP join
// leaves a connected-but-cyclic (redundant) MST that is still routable, whereas a
// collinear BUTT-joint leaves a genuinely disconnected subtree.  We only want to
// drop the latter.
static bool topology_is_connected(const Topology& topo, const Floorplan& fp) {
    ConnTopology ct;
    ct.build(topo, fp);
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

    // MST edge weights: minimum manhattan distance across all rect pairs.
    auto rect_min_dist = [&](int u, int v) -> int {
        int d = INT_MAX;
        for (const Rect& ru : block_rects(u))
            for (const Rect& rv : block_rects(v))
                d = std::min(d, manhattan_nearest(ru, rv));
        return d;
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

    // Build MST on closest-rect distances.
    int n = (int)blocks.size();
    struct RawEdge { int u, v, dist; };
    std::vector<RawEdge> all_edges;
    all_edges.reserve(n * (n - 1) / 2);
    for (int i = 0; i < n; ++i)
        for (int j = i + 1; j < n; ++j)
            all_edges.push_back({i, j, rect_min_dist(i, j)});
    std::sort(all_edges.begin(), all_edges.end(),
              [](const RawEdge& a, const RawEdge& b){ return a.dist < b.dist; });
    std::vector<int> par(n); std::iota(par.begin(), par.end(), 0);
    std::function<int(int)> find = [&](int x) {
        return par[x] == x ? x : par[x] = find(par[x]);
    };
    std::vector<std::pair<int,int>> mst_edges;
    mst_edges.reserve(n - 1);
    for (const auto& e : all_edges) {
        int pu = find(e.u), pv = find(e.v);
        if (pu == pv) continue;
        par[pu] = pv;
        mst_edges.push_back({e.u, e.v});
        if ((int)mst_edges.size() == n - 1) break;
    }

    int m_v = floorplan_.get_min_stub_length(1 /*VERTICAL*/, v_layer_);
    int m_h = floorplan_.get_min_stub_length(0 /*HORIZONTAL*/, h_layer_);

    for (int strategy = 0; strategy < 2; ++strategy) {
        Topology mst;
        mst.type = (strategy == 0) ? "MST_HV" : "MST_VH";
        bool valid = true;
        for (const auto& [eu, ev] : mst_edges) {
            Point p1, p2;
            Rect  br_u, br_v;
            closest_block_points(eu, ev, p1, p2, br_u, br_v);
            if (p1.x == p2.x && p1.y == p2.y) {
                // Abutting (or coincident) rects: realize the shared edge as a
                // real wire so the block stays connected, instead of dropping it.
                Segment es;
                if (shared_edge_segment(br_u, br_v, h_layer_, v_layer_, es))
                    mst.segments.push_back(es);
                continue;
            }
            // Corner-diagonal? A straight edge whose shared projection is a single
            // point is pinned (zero slide); route it around the corner as an L.
            const int cox_lo = std::max(br_u.x1, br_v.x1), cox_hi = std::min(br_u.x2, br_v.x2);
            const int coy_lo = std::max(br_u.y1, br_v.y1), coy_hi = std::min(br_u.y2, br_v.y2);
            const bool corner = (p1.x == p2.x && cox_lo == cox_hi)
                             || (p1.y == p2.y && coy_lo == coy_hi);
            if (corner) {
                std::vector<Segment> ls;
                corner_diagonal_L(br_u, br_v, strategy, h_layer_, v_layer_, ls);
                for (const auto& s : ls) mst.segments.push_back(s);
            } else if (p1.x == p2.x) {
                mst.segments.push_back(make_seg(p1.x, p1.y, p1.x, p2.y, v_layer_));
            } else if (p1.y == p2.y) {
                mst.segments.push_back(make_seg(p1.x, p1.y, p2.x, p1.y, h_layer_));
            } else {
                if (strategy == 0) {
                    // H then V
                    if (std::abs(p2.x - p1.x) < m_h || std::abs(p2.y - p1.y) < m_v) {
                        valid = false; break;
                    }
                    mst.segments.push_back(make_seg(p1.x, p1.y, p2.x, p1.y, h_layer_));
                    mst.segments.push_back(make_seg(p2.x, p1.y, p2.x, p2.y, v_layer_));
                } else {
                    // V then H
                    if (std::abs(p2.y - p1.y) < m_v || std::abs(p2.x - p1.x) < m_h) {
                        valid = false; break;
                    }
                    mst.segments.push_back(make_seg(p1.x, p1.y, p1.x, p2.y, v_layer_));
                    mst.segments.push_back(make_seg(p1.x, p2.y, p2.x, p2.y, h_layer_));
                }
            }
        }
        if (valid) {
            // Annotate the raw stubs first, then complete: completion rewrites the
            // relay busterm taps (single tap + SEG junctions) and annotates the
            // connectors it appends, so it must run after the baseline annotation.
            annotate_endpoints(mst, blocks);
            complete_relay_junctions(mst, blocks, floorplan_, h_layer_, v_layer_);
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
    ConnTopology ct;
    ct.build(topo, fp);
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
                const Rect& r_u = nodes[mst_edges[e].u].second;
                const Rect& r_v = nodes[mst_edges[e].v].second;
                Point p1, p2;
                closest_points(r_u, r_v, p1, p2);
                if (p1.x == p2.x && p1.y == p2.y) {
                    // Abutting rects: realize the shared edge instead of dropping
                    // it (a dropped edge would disconnect the replaced child).
                    Segment es;
                    if (shared_edge_segment(r_u, r_v, h_layer_, v_layer_, es))
                        out.push_back(es);
                    continue;
                }
                // Corner-diagonal shortcut: faces meet at a single point, so a
                // straight edge would be pinned (zero slide) and filter_pinched
                // would drop the hybrid.  Route it around the corner as an L
                // (same fix as the standalone MST path).  <4-block hybrids have no
                // standalone-MST fallback, so this is the only MST coverage.
                const int cox_lo = std::max(r_u.x1, r_v.x1), cox_hi = std::min(r_u.x2, r_v.x2);
                const int coy_lo = std::max(r_u.y1, r_v.y1), coy_hi = std::min(r_u.y2, r_v.y2);
                if ((p1.x == p2.x && cox_lo == cox_hi) || (p1.y == p2.y && coy_lo == coy_hi)) {
                    corner_diagonal_L(r_u, r_v, is_h ? 1 : 0, h_layer_, v_layer_, out);
                    continue;
                }
                if (p1.x == p2.x) {
                    if (std::abs(p2.y - p1.y) < m_v) return false;
                    out.push_back(make_seg(p1.x, p1.y, p1.x, p2.y, v_layer_));
                } else if (p1.y == p2.y) {
                    if (std::abs(p2.x - p1.x) < m_h) return false;
                    out.push_back(make_seg(p1.x, p1.y, p2.x, p1.y, h_layer_));
                } else {
                    // Diagonal L-shape: both legs must meet their minimum length.
                    if (std::abs(p2.x - p1.x) < m_h || std::abs(p2.y - p1.y) < m_v)
                        return false;
                    if (is_h) {
                        out.push_back(make_seg(p1.x, p1.y, p1.x, p2.y, v_layer_));
                        out.push_back(make_seg(p1.x, p2.y, p2.x, p2.y, h_layer_));
                    } else {
                        out.push_back(make_seg(p1.x, p1.y, p2.x, p1.y, h_layer_));
                        out.push_back(make_seg(p2.x, p1.y, p2.x, p2.y, v_layer_));
                    }
                }
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
            complete_relay_junctions(tree, blocks, floorplan_, h_layer_, v_layer_);
            // connected_block_names is populated globally only after this pass
            // (generate_candidates), so set it here so the gate's coverage check
            // knows which blocks the tree must cover.
            if (tree.connected_block_names.empty())
                for (const auto& b : blocks)
                    tree.connected_block_names.push_back(b.block_name);
            if (topology_is_clean_tree(tree, floorplan_))
                results.push_back(std::move(tree));
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
        complete_relay_junctions(legacy, blocks, floorplan_, h_layer_, v_layer_);
        if (legacy.connected_block_names.empty())
            for (const auto& b : blocks)
                legacy.connected_block_names.push_back(b.block_name);
        if (topology_is_clean_tree(legacy, floorplan_)) {
            results.push_back(std::move(legacy));
        } else if (blocks.size() < 4) {
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

    // Legacy BITRUNK_H (two parallel H trunks + a central V backbone).  Emitted
    // unconditionally, exactly as before this change, so the DEFAULT candidate set
    // and planner choices are preserved — the opt-in flag below only ADDS the new
    // two-level trees.
    [&]() {
        std::vector<int> y_coords;
        for (const auto& p : pins) y_coords.push_back(p.y);
        std::sort(y_coords.begin(), y_coords.end());
        int y_mid = y_coords[y_coords.size() / 2];
        int y_t1  = y_coords[y_coords.size() / 4];
        int y_t2  = y_coords[3 * y_coords.size() / 4];
        if (y_t1 == y_t2) return;
        Topology t;
        t.type = "BITRUNK_H";
        int x_min = INT_MAX, x_max = INT_MIN;
        for (const auto& p : pins) { x_min = std::min(x_min, p.x); x_max = std::max(x_max, p.x); }
        int x_backbone = (x_min + x_max) / 2;
        t.segments.push_back(make_seg(x_min, y_t1, x_max, y_t1, h_layer_));
        t.segments.push_back(make_seg(x_min, y_t2, x_max, y_t2, h_layer_));
        t.segments.push_back(make_seg(x_backbone, y_t1, x_backbone, y_t2, v_layer_));
        int m_v = floorplan_.get_min_stub_length(1 /*VERTICAL*/, v_layer_);
        for (int i = 0; i < (int)blocks.size(); ++i) {
            int yt = (pins[i].y <= y_mid) ? y_t1 : y_t2;
            int src_y = blocks[i].orig_bbox.face_y(yt);
            if (std::abs(yt - src_y) >= m_v) {
                int si = (int)t.segments.size();
                t.segments.push_back(make_seg(pins[i].x, src_y, pins[i].x, yt, v_layer_));
                t.seg_busterms[si].first = blocks[i];
            } else if (yt != src_y) {
                return;   // a stub too short → legacy BITRUNK_H is not viable
            }
        }
        // Give the two H trunks + V backbone a seg_busterms entry (the leaf
        // stubs are seeded above) so ConnTopology uses the authoritative path for
        // EVERY segment, never the geometric fallback.  These trunk endpoints tap
        // no block — they are free ends or wire junctions — so their entries stay
        // null/null and the backbone↔trunk and stub↔trunk joins are inferred as
        // SEG.  We deliberately do NOT call annotate_endpoints here: it would
        // geometrically fill a trunk endpoint that coincidentally grazes a
        // neighbour block face, turning a junction into a spurious busterm (the
        // very corner-feedthru this effort removes).
        for (size_t i = 0; i < t.segments.size(); ++i) (void)t.seg_busterms[i];
        results.push_back(std::move(t));
    }();

    // New two-level BITRUNK_HVH / BITRUNK_VHV trees — opt-in only.
    if (!allow_multi_trunk_) return;   // need enough fan-out for a ≥2-branch tree

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
        auto RA  = [&](const Busterm& b){ return root_horiz ? (b.orig_bbox.x1 + b.orig_bbox.x2) / 2
                                                            : (b.orig_bbox.y1 + b.orig_bbox.y2) / 2; };
        auto PA  = [&](const Busterm& b){ return root_horiz ? (b.orig_bbox.y1 + b.orig_bbox.y2) / 2
                                                            : (b.orig_bbox.x1 + b.orig_bbox.x2) / 2; };
        auto RA1 = [&](const Busterm& b){ return root_horiz ? b.orig_bbox.x1 : b.orig_bbox.y1; };
        auto RA2 = [&](const Busterm& b){ return root_horiz ? b.orig_bbox.x2 : b.orig_bbox.y2; };
        auto PA1 = [&](const Busterm& b){ return root_horiz ? b.orig_bbox.y1 : b.orig_bbox.x1; };
        auto PA2 = [&](const Busterm& b){ return root_horiz ? b.orig_bbox.y2 : b.orig_bbox.x2; };
        auto RAface = [&](const Busterm& b, int toward){
            return root_horiz ? b.orig_bbox.face_x(toward) : b.orig_bbox.face_y(toward); };
        // Build a Point from (root-axis coord, perp-axis coord).
        auto mkpt = [&](int ra, int pa){ return root_horiz ? Point{ra, pa} : Point{pa, ra}; };
        auto mkseg = [&](int ra1, int pa1, int ra2, int pa2, int layer){
            Point a = mkpt(ra1, pa1), c = mkpt(ra2, pa2);
            return make_seg(a.x, a.y, c.x, c.y, layer); };

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
                bool straddle = (b_ra > RA1(b) && b_ra < RA2(b));
                if (straddle) continue;   // pass-through: branch covers this block
                int face = RAface(b, b_ra);
                if (std::abs(face - b_ra) < stub_root) return;   // stub too short → drop
                int lp = PA(b);            // leaf connects at its perp centre
                int si = (int)t.segments.size();
                t.segments.push_back(mkseg(face, lp, b_ra, lp, root_layer));
                t.seg_busterms[si].first = b;   // busterm at the leaf-face endpoint
                (void)branch_idx;
            }
        }
        // Root spine spans the extreme branch positions at root_perp.
        int r_lo = *std::min_element(branch_ra.begin(), branch_ra.end());
        int r_hi = *std::max_element(branch_ra.begin(), branch_ra.end());
        if (r_hi - r_lo < stub_root) return;
        t.segments.insert(t.segments.begin(),
                          mkseg(r_lo, root_perp, r_hi, root_perp, root_layer));
        // seg_busterms indices shifted by the inserted root at index 0.
        std::map<int, SegEndpoints> shifted;
        for (auto& [k, v] : t.seg_busterms) shifted[k + 1] = v;
        t.seg_busterms = std::move(shifted);

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

std::vector<Topology> TopologyGenerator::generate_candidates(
    const std::string& src_name,
    const std::vector<std::string>& dst_names)
{
    if (dst_names.size() == 1) {
        bool any_multi_rect = !floorplan_.get_block_rects(src_name).empty() ||
                              !floorplan_.get_block_rects(dst_names[0]).empty();
        if (!any_multi_rect)
            return generate_2pin(src_name, dst_names[0]);
    }
    return generate_npin(src_name, dst_names);
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
    add_u_shapes(src_bt, dst_bt, chan_x, chan_y, candidates);
    if (allow_double_detour_)
        add_uu_shapes(src_bt, dst_bt, chan_x, chan_y, candidates);
    for (auto& t : candidates) annotate_endpoints(t, {src_bt, dst_bt});
    annotate_and_sort(candidates);

    // Keepout filtering for 2-pin: remove topologies whose trunk segment is
    // blocked on all available layers.  OOB/U-shape segments outside the
    // keepout are not affected; only fully-blocked in-bbox segments are culled.
    {
        const auto& kos = floorplan_.get_keepout_zones();
        if (!kos.empty()) {
            candidates.erase(
                std::remove_if(candidates.begin(), candidates.end(),
                    [&](const Topology& t) {
                        for (const auto& seg : t.segments) {
                            bool is_h = (seg.start.y == seg.end.y);
                            const std::vector<int>& layers =
                                is_h ? all_h_layers_ : all_v_layers_;
                            if (all_layers_blocked_by_keepouts(seg, layers, kos))
                                return true;
                        }
                        return false;
                    }),
                candidates.end());
        }
    }

    filter_pinched(candidates);
    for (auto& t : candidates)
        if (t.connected_block_names.empty())
            t.connected_block_names = {src_name, dst_name};
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
