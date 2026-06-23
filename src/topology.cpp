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
    (void)fp;  // min-stub is intentionally not enforced on completion connectors:
               // a relay MUST be completed (correctness over the min-stub heuristic).
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

    for (auto& [bi, pts] : incident) {
        if (pts.size() < 2) continue;        // leaf terminal: nothing to relay
        // Chain the landings (sorted) so all incident segments end up in one
        // wire-connected component through the block's junction.
        std::sort(pts.begin(), pts.end(), [](const Inc& a, const Inc& b) {
            return a.p.x != b.p.x ? a.p.x < b.p.x : a.p.y < b.p.y;
        });
        for (size_t k = 1; k < pts.size(); ++k)
            connect(pts[k - 1], pts[k]);
    }

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

    int x_lo = INT_MAX, x_hi = INT_MIN;
    for (int i = 0; i < n; ++i) {
        x_lo = std::min(x_lo, att_x[i]); x_hi = std::max(x_hi, att_x[i]);
    }
    if (x_lo >= x_hi) return;

    Topology t;
    t.type               = std::string(out_of_bbox ? "TRUNK_H_OOB" : "TRUNK_H")
                           + "@y" + std::to_string(y_trunk);
    t.trunk_location     = y_trunk;
    t.pass_through_count = 0;
    for (int i = 0; i < n; ++i)
        if (!has_stub[i] && att_x[i] != x_lo && att_x[i] != x_hi)
            ++t.pass_through_count;
    if (x_lo < x_hi)
        t.segments.push_back(make_seg(x_lo, y_trunk, x_hi, y_trunk, h_layer_));

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
        for (int i = 0; i < n; ++i) {
            if (!has_stub[i]) continue;
            for (int j = 0; j < n; ++j) {
                if (i == j || !has_stub[j]) continue;
                int di = conn_x[i] - x_trunk, dj = conn_x[j] - x_trunk;
                if (di == 0 || dj == 0) continue;
                if ((di > 0) != (dj > 0)) continue;          // opposite sides of trunk
                if (std::abs(dj) <= std::abs(di)) continue;  // j not farther
                // Does stub j's att_y lie within block i's original y-extent?
                if (att_y[j] >= blocks[i].orig_bbox.y1 &&
                    att_y[j] <= blocks[i].orig_bbox.y2) {
                    stub_suppressed[i] = true; break;
                }
            }
        }
    }

    int y_lo = INT_MAX, y_hi = INT_MIN;
    for (int i = 0; i < n; ++i) {
        if (stub_suppressed[i]) continue;
        y_lo = std::min(y_lo, att_y[i]); y_hi = std::max(y_hi, att_y[i]);
    }
    if (y_lo >= y_hi) return;

    Topology t;
    t.type               = std::string(out_of_bbox ? "TRUNK_V_OOB" : "TRUNK_V")
                           + "@x" + std::to_string(x_trunk);
    t.trunk_location     = x_trunk;
    t.pass_through_count = 0;
    for (int i = 0; i < n; ++i)
        if (!has_stub[i] && att_y[i] != y_lo && att_y[i] != y_hi)
            ++t.pass_through_count;

    if (y_lo < y_hi)
        t.segments.push_back(make_seg(x_trunk, y_lo, x_trunk, y_hi, v_layer_));

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

    // Closest point pair across all individual rect pairs of two blocks.
    auto closest_block_points = [&](int u, int v, Point& p1, Point& p2) {
        int best = INT_MAX;
        for (const Rect& ru : block_rects(u)) {
            for (const Rect& rv : block_rects(v)) {
                int d = manhattan_nearest(ru, rv);
                if (d < best) { best = d; closest_points(ru, rv, p1, p2); }
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
            closest_block_points(eu, ev, p1, p2);
            if (p1.x == p2.x && p1.y == p2.y) continue;
            if (p1.x == p2.x) {
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
    return true;
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

        std::set<std::string> child_names;   // branch blocks whose stub the MST replaces
        if (simple)
            for (int k = 0; k < (int)nodes.size(); ++k)
                if (k != root_node) child_names.insert(nodes[k].first);

        // Realize the MST inter-branch edges as segments once; both the legacy and
        // the completed-tree form append the same edge geometry.
        std::vector<Segment> edge_segs;
        bool valid = true;
        for (const auto& edge : mst_edges) {
            const Rect& r_u = nodes[edge.u].second;
            const Rect& r_v = nodes[edge.v].second;
            Point p1, p2;
            closest_points(r_u, r_v, p1, p2);
            if (p1.x == p2.x && p1.y == p2.y) continue;

            if (p1.x == p2.x) {
                if (std::abs(p2.y - p1.y) < m_v) { valid = false; break; }
                edge_segs.push_back(make_seg(p1.x, p1.y, p1.x, p2.y, v_layer_));
            } else if (p1.y == p2.y) {
                if (std::abs(p2.x - p1.x) < m_h) { valid = false; break; }
                edge_segs.push_back(make_seg(p1.x, p1.y, p2.x, p1.y, h_layer_));
            } else {
                // Diagonal L-shape: both legs must meet their minimum length,
                // otherwise the edge would stop short of the branch block and
                // leave a dangling shortcut.  Reject the whole candidate (same
                // as standalone MST) rather than emit an incomplete edge.
                if (std::abs(p2.x - p1.x) < m_h || std::abs(p2.y - p1.y) < m_v) {
                    valid = false; break;
                }
                if (is_h) {
                    // first perpendicular to trunk direction, then along it
                    edge_segs.push_back(make_seg(p1.x, p1.y, p1.x, p2.y, v_layer_));
                    edge_segs.push_back(make_seg(p1.x, p2.y, p2.x, p2.y, h_layer_));
                } else {
                    edge_segs.push_back(make_seg(p1.x, p1.y, p2.x, p1.y, h_layer_));
                    edge_segs.push_back(make_seg(p2.x, p1.y, p2.x, p2.y, v_layer_));
                }
            }
        }
        if (!valid || edge_segs.empty()) continue;   // no usable shortcut edges

        std::string mst_type;
        {
            auto at = trunk_topo.type.find('@');
            mst_type = (at != std::string::npos)
                ? trunk_topo.type.substr(0, at) + "+MST" + trunk_topo.type.substr(at)
                : trunk_topo.type + "+MST";
        }

        // Legacy form: full trunk (every stub) + shortcut edges, annotated only.
        // This is the historical un-completed hybrid; check_topo flags its relays
        // as FEEDTHRU_RELAY.  It is the fallback when the tree form can't be cleanly
        // completed.
        auto build_legacy = [&]() {
            Topology t = trunk_topo;
            t.type = mst_type;
            for (const auto& s : edge_segs) t.segments.push_back(s);
            annotate_endpoints(t, blocks);
            return t;
        };

        // Completed-tree form (single-rect blocks with a stub-owning root): drop
        // each non-root child's trunk stub so the MST edge REPLACES it, yielding a
        // cycle-free trunk-rooted tree that complete_relay_junctions can wire up
        // (single busterm tap per block + SEG junctions).  We accept it only when
        // the result verifies as one SEG-connected component -- a stub that is
        // collinear with an incident MST edge defeats ConnTopology's perpendicular
        // junction inference, and those cases fall back to the legacy form.
        if (simple && !child_names.empty()) {
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
            annotate_endpoints(tree, blocks);
            complete_relay_junctions(tree, blocks, floorplan_, h_layer_, v_layer_);
            if (topology_is_clean_tree(tree, floorplan_))
                results.push_back(std::move(tree));
            // Otherwise a single-rect hybrid that cannot be cleanly completed
            // (a stub collinear with an incident MST edge) is DROPPED rather than
            // emitted as a feedthru/model-disconnected candidate: the base trunk
            // and the (always-completed) standalone MST already cover this bundle,
            // and dropping trims candidate noise.
            continue;
        }
        // Multi-rect / no stub-owning root: completion is out of scope, so emit
        // the historical legacy hybrid (check_topo still flags its relays).
        results.push_back(build_legacy());
    }
}

void TopologyGenerator::add_multi_trunk_candidates(
    const std::vector<Point>& pins,
    const std::vector<Busterm>& blocks,
    std::vector<Topology>& results)
{
    if (blocks.size() < 4) return;
    std::vector<int> y_coords;
    for (const auto& p : pins) y_coords.push_back(p.y);
    std::sort(y_coords.begin(), y_coords.end());
    int y_mid = y_coords[y_coords.size() / 2];
    int y_t1 = y_coords[y_coords.size() / 4];
    int y_t2 = y_coords[3 * y_coords.size() / 4];

    if (y_t1 != y_t2) {
        Topology t;
        t.type = "BITRUNK_H";
        int x_min = INT_MAX, x_max = INT_MIN;
        for (const auto& p : pins) {
            x_min = std::min(x_min, p.x);
            x_max = std::max(x_max, p.x);
        }
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
                // BITRUNK needs all stubs. If one is too short, the whole BITRUNK is bad.
                return; 
            }
        }
        results.push_back(std::move(t));
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
