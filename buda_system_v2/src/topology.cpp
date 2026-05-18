#include "topology.h"
#include "conn_topology.h"
#include <cmath>
#include <climits>
#include <set>
#include <string>
#include <iostream>

namespace interconnect {

void Floorplan::add_block(const std::string& name, int x1, int y1, int x2, int y2) {
    blocks_[name] = Rect{x1, y1, x2, y2};
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
void Floorplan::get_hanan_grid(std::vector<int>& x_coords, std::vector<int>& y_coords) const {
    for (const auto& [name, r] : blocks_) {
        x_coords.push_back(r.x1); x_coords.push_back(r.x2);
        y_coords.push_back(r.y1); y_coords.push_back(r.y2);
    }
    std::sort(x_coords.begin(), x_coords.end());
    x_coords.erase(std::unique(x_coords.begin(), x_coords.end()), x_coords.end());
    std::sort(y_coords.begin(), y_coords.end());
    y_coords.erase(std::unique(y_coords.begin(), y_coords.end()), y_coords.end());
}
Segment make_seg(int x1, int y1, int x2, int y2, int layer) {
    Segment s; s.start={x1,y1}; s.end={x2,y2}; s.layer_hint=layer; return s;
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
                Topology hv; hv.type = "L_HV@x" + std::to_string(bx);
                hv.segments.push_back(make_seg(s_orig.face_x(bx), hy, bx, hy, h_layer_));
                if (hy != dy) hv.segments.push_back(make_seg(bx, hy, bx, dy, v_layer_));
                if (hv.segments.size() == 2) results.push_back(hv);
            }
            if (dst.x2 > src.x2) {
                int bx = (src.x2 + dst.x2) / 2;
                Topology hv; hv.type = "L_HV@x" + std::to_string(bx);
                hv.segments.push_back(make_seg(s_orig.face_x(bx), hy, bx, hy, h_layer_));
                if (hy != dy) hv.segments.push_back(make_seg(bx, hy, bx, dy, v_layer_));
                if (hv.segments.size() == 2) results.push_back(hv);
            }
        } else if (use_busterm_) {
            // Option 1: H below dst, V up to dst.y1
            if (s_orig.y1 < d_orig.y1) {
                int hy = std::min(src.y2, d_orig.y1 - m_v);
                if (hy >= src.y1 && hy <= d_orig.y1 - m_v
                        && std::abs(bend_x - s_orig.face_x(bend_x)) >= m_h) {
                    Topology hv; hv.type = "L_HV@x" + std::to_string(bend_x) + "@y" + std::to_string(hy);
                    hv.segments.push_back(make_seg(s_orig.face_x(bend_x), hy, bend_x, hy, h_layer_));
                    hv.segments.push_back(make_seg(bend_x, hy, bend_x, d_orig.y1, v_layer_));
                    results.push_back(hv);
                }
            }
            // Option 2: H above dst, V down to dst.y2
            if (s_orig.y2 > d_orig.y2) {
                int hy = std::max(src.y1, d_orig.y2 + m_v);
                if (hy >= d_orig.y2 + m_v && hy <= src.y2
                        && std::abs(bend_x - s_orig.face_x(bend_x)) >= m_h) {
                    Topology hv; hv.type = "L_HV@x" + std::to_string(bend_x) + "@y" + std::to_string(hy);
                    hv.segments.push_back(make_seg(s_orig.face_x(bend_x), hy, bend_x, hy, h_layer_));
                    hv.segments.push_back(make_seg(bend_x, hy, bend_x, d_orig.y2, v_layer_));
                    results.push_back(hv);
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
                Topology vh; vh.type = "L_VH@x" + std::to_string(vx2);
                int bend_y_fixed = d_orig.face_y(sy);
                if (s_orig.face_y(bend_y_fixed) != bend_y_fixed) vh.segments.push_back(make_seg(vx2, s_orig.face_y(bend_y_fixed), vx2, bend_y_fixed, v_layer_));
                if (vx2 != dx2)   vh.segments.push_back(make_seg(vx2, bend_y_fixed, dx2, bend_y_fixed, h_layer_));
                if (vh.segments.size() == 2) results.push_back(vh);
            }
            if (src.x1 < dst.x1) {
                int vx2 = (src.x1 + dst.x1) / 2;
                int dx2 = d_orig.face_x(vx2);
                Topology vh; vh.type = "L_VH@x" + std::to_string(vx2);
                int bend_y_fixed = d_orig.face_y(sy);
                if (s_orig.face_y(bend_y_fixed) != bend_y_fixed) vh.segments.push_back(make_seg(vx2, s_orig.face_y(bend_y_fixed), vx2, bend_y_fixed, v_layer_));
                if (vx2 != dx2)   vh.segments.push_back(make_seg(vx2, bend_y_fixed, dx2, bend_y_fixed, h_layer_));
                if (vh.segments.size() == 2) results.push_back(vh);
            }
        } else if (use_busterm_) {
            // Option A: H below src, V stub down from src bottom face
            if (d_orig.y1 < s_orig.y1) {
                int bend_y_a = std::min(s_orig.y1 - m_v, dst.y2);
                if (bend_y_a >= dst.y1 && bend_y_a <= s_orig.y1 - m_v
                        && std::abs(d_orig.face_x(vx) - vx) >= m_h) {
                    Topology vh; vh.type = "L_VH@y" + std::to_string(bend_y_a) + "@x" + std::to_string(vx);
                    vh.segments.push_back(make_seg(vx, s_orig.y1,   vx, bend_y_a, v_layer_));
                    vh.segments.push_back(make_seg(vx, bend_y_a, d_orig.face_x(vx), bend_y_a, h_layer_));
                    results.push_back(vh);
                }
            }
            // Option B: H above src, V stub up from src top face
            if (d_orig.y2 > s_orig.y2) {
                int bend_y_b = std::max(s_orig.y2 + m_v, dst.y1);
                if (bend_y_b >= s_orig.y2 + m_v && bend_y_b <= dst.y2
                        && std::abs(d_orig.face_x(vx) - vx) >= m_h) {
                    Topology vh; vh.type = "L_VH@y" + std::to_string(bend_y_b) + "@x" + std::to_string(vx);
                    vh.segments.push_back(make_seg(vx, s_orig.y2,   vx, bend_y_b, v_layer_));
                    vh.segments.push_back(make_seg(vx, bend_y_b, d_orig.face_x(vx), bend_y_b, h_layer_));
                    results.push_back(vh);
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

    // Z_HVH: trunk is vertical at x_cut between the two block centres.
    int min_x = std::min(s.x, d.x), max_x = std::max(s.x, d.x);
    for (int x_cut : x_grid) {
        if (x_cut > min_x && x_cut < max_x) {
            int sx = use_busterm_ ? src.face_x(x_cut) : s.x;
            int dx = use_busterm_ ? dst.face_x(x_cut) : d.x;
            int ty_src = stub_y(use_busterm_, sx != x_cut, src, d.y, s.y);
            int ty_dst = stub_y(use_busterm_, dx != x_cut, dst, s.y, d.y);

            if (ty_src != ty_dst) {
                // Standard Z_HVH
                Topology z; z.type = "Z_HVH@x" + std::to_string(x_cut) + "@y" + std::to_string(ty_src);
                if (sx != x_cut)
                    z.segments.push_back(make_seg(s_orig.face_x(x_cut), ty_src, x_cut, ty_src, h_layer_));
                z.segments.push_back(make_seg(x_cut, ty_src, x_cut, ty_dst, v_layer_));
                if (x_cut != dx)
                    z.segments.push_back(make_seg(x_cut, ty_dst, d_orig.face_x(x_cut), ty_dst, h_layer_));
                if (z.segments.size() == 3) results.push_back(z);
            } else if (use_busterm_ && sx != x_cut && x_cut != dx) {
                // Spread Z_HVH — stubs terminate exactly at x_cut so
                // ConnTopology can detect the shared-endpoint T-junction.
                int sy_hi = src.y2; int sy_lo = src.y1;
                int dy_hi = dst.y2; int dy_lo = dst.y1;

                if (sy_hi != dy_lo) {
                    Topology z; z.type = "Z_HVH@x" + std::to_string(x_cut) + "@y" + std::to_string(sy_hi);
                    z.segments.push_back(make_seg(s_orig.face_x(x_cut), sy_hi, x_cut,  sy_hi, h_layer_));
                    z.segments.push_back(make_seg(x_cut, sy_hi, x_cut, dy_lo, v_layer_));
                    z.segments.push_back(make_seg(x_cut, dy_lo, d_orig.face_x(x_cut),  dy_lo, h_layer_));
                    results.push_back(z);
                }
                if (sy_lo != dy_hi) {
                    Topology z; z.type = "Z_HVH@x" + std::to_string(x_cut) + "@y" + std::to_string(sy_lo);
                    z.segments.push_back(make_seg(s_orig.face_x(x_cut), sy_lo, x_cut,  sy_lo, h_layer_));
                    z.segments.push_back(make_seg(x_cut, sy_lo, x_cut, dy_hi, v_layer_));
                    z.segments.push_back(make_seg(x_cut, dy_hi, d_orig.face_x(x_cut),  dy_hi, h_layer_));
                    results.push_back(z);
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
                // Standard Z_VHV
                Topology z; z.type = "Z_VHV@y" + std::to_string(y_cut) + "@x" + std::to_string(vx_src);
                if (sy != y_cut)
                    z.segments.push_back(make_seg(vx_src, s_orig.face_y(y_cut), vx_src, y_cut, v_layer_));
                z.segments.push_back(make_seg(vx_src, y_cut, vx_dst, y_cut, h_layer_));
                if (y_cut != dy)
                    z.segments.push_back(make_seg(vx_dst, y_cut, vx_dst, d_orig.face_y(y_cut), v_layer_));
                if (z.segments.size() == 3) results.push_back(z);
            } else if (use_busterm_ && sy != y_cut && y_cut != dy) {
                // Spread Z_VHV — stubs terminate exactly at y_cut.
                int vx_hi = src.x2; int vx_lo = src.x1;

                for (int flip = 0; flip < 2; ++flip) {
                    int x1 = flip ? vx_lo : vx_hi;
                    int x2 = flip ? vx_hi : vx_lo;
                    if (x1 == x2) continue;
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

void TopologyGenerator::add_u_shapes(const Busterm& s_bt, const Busterm& d_bt,
                                      const std::vector<int>& x_grid,
                                      const std::vector<int>& y_grid,
                                      std::vector<Topology>& results) {
    const Rect& src = s_bt.bbox; const Rect& dst = d_bt.bbox;
    const Rect& s_orig = s_bt.orig_bbox; const Rect& d_orig = d_bt.orig_bbox;
    Point s = src.center(); Point d = dst.center();
    int min_x = std::min(s.x, d.x), max_x = std::max(s.x, d.x);
    int min_y = std::min(s.y, d.y), max_y = std::max(s.y, d.y);

    // U_HVH: vertical detour trunk left/right of bounding box.
    for (int x_cut : x_grid) {
        if (x_cut < min_x || x_cut > max_x) {
            int sx = use_busterm_ ? src.face_x(x_cut) : s.x;
            int dx = use_busterm_ ? dst.face_x(x_cut) : d.x;
            int ty_src = stub_y(use_busterm_, sx != x_cut, src, d.y, s.y);
            int ty_dst = stub_y(use_busterm_, dx != x_cut, dst, s.y, d.y);
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

    // U_VHV: horizontal detour trunk above/below bounding box.
    for (int y_cut : y_grid) {
        if (y_cut < min_y || y_cut > max_y) {
            int sy = use_busterm_ ? src.face_y(y_cut) : s.y;
            int dy = use_busterm_ ? dst.face_y(y_cut) : d.y;
            int vx_src = stub_x(use_busterm_, sy != y_cut, src, d.x, s.x);
            int vx_dst = stub_x(use_busterm_, dy != y_cut, dst, s.x, d.x);
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

    for (int y_cut : y_grid) {
        if (y_cut >= min_y && y_cut <= max_y) continue;
        int dy      = d_orig.face_y(y_cut);
        int vx_dst  = stub_x(true, dy != y_cut, dst, s.x, d.x);
        int exit_x = (std::abs(src.x1 - d.x) >= std::abs(src.x2 - d.x)) ? src.x1 : src.x2;
        int sy_src = (y_cut < min_y) ? src.y1 : src.y2;
        int x_corner = (exit_x == src.x1) ? src.x1 - margin_x : src.x2 + margin_x;

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

    for (int x_cut : x_grid) {
        if (x_cut >= min_x && x_cut <= max_x) continue;
        int dx      = d_orig.face_x(x_cut);
        int ty_dst  = stub_y(true, dx != x_cut, dst, s.y, d.y);
        int exit_y = (std::abs(src.y1 - d.y) >= std::abs(src.y2 - d.y)) ? src.y1 : src.y2;
        int tx_src = (x_cut < min_x) ? src.x1 : src.x2;
        int y_corner = (exit_y == src.y1) ? src.y1 - margin_y : src.y2 + margin_y;

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

static void annotate_endpoints(Topology& topo,
                                const std::vector<Busterm>& blocks) {
    for (int i = 0; i < (int)topo.segments.size(); ++i) {
        const Segment& seg = topo.segments[i];
        bool horiz = (seg.start.y == seg.end.y);
        for (const Busterm& bt : blocks) {
            const Rect& r = bt.orig_bbox; // connect to physical faces
            auto on_face = [&](const Point& P) -> bool {
                if (horiz)
                    return (P.x == r.x1 || P.x == r.x2)
                           && P.y >= r.y1 && P.y <= r.y2;
                else
                    return (P.y == r.y1 || P.y == r.y2)
                           && P.x >= r.x1 && P.x <= r.x2;
            };
            auto& ep = topo.seg_busterms[i];
            if (!ep.first.has_value()  && on_face(seg.start)) ep.first  = bt;
            if (!ep.second.has_value() && on_face(seg.end))   ep.second = bt;
        }
    }
}

// ---------------------------------------------------------------------------
// Multicast helpers
// ---------------------------------------------------------------------------

void TopologyGenerator::add_trunk_h(const std::vector<Point>& pins,
                                     const std::vector<Busterm>& blocks,
                                     int y_trunk, bool out_of_bbox,
                                     std::vector<Topology>& results)
{
    int n = (int)pins.size();
    std::vector<int> conn_y(n), att_x(n);
    std::vector<bool> has_stub(n);
    for (int i = 0; i < n; ++i) {
        conn_y[i]   = use_busterm_ ? blocks[i].orig_bbox.face_y(y_trunk) : pins[i].y;
        has_stub[i] = (conn_y[i] != y_trunk);
        att_x[i]    = pins[i].x;
    }

    if (use_busterm_) {
        if (!out_of_bbox) {
            int pt_lo = INT_MIN / 2, pt_hi = INT_MAX / 2;
            bool any_pt = false;
            for (int i = 0; i < n; ++i) {
                if (!has_stub[i]) {
                    any_pt = true;
                    const Rect& b = blocks[i].bbox;
                    pt_lo = std::max(pt_lo, b.y1);
                    pt_hi = std::min(pt_hi, b.y2);
                }
            }
            if (any_pt && pt_lo <= pt_hi) {
                int n_above = 0, n_below = 0;
                for (int i = 0; i < n; ++i) {
                    if (has_stub[i]) {
                        if (conn_y[i] > y_trunk) ++n_above;
                        else                      ++n_below;
                    }
                }
                if      (n_above > 0 && n_below == 0) y_trunk = pt_hi;
                else if (n_below > 0 && n_above == 0) y_trunk = pt_lo;
                for (int i = 0; i < n; ++i)
                    conn_y[i] = blocks[i].orig_bbox.face_y(y_trunk);
            }
        }
        {
            int lo = std::min_element(att_x.begin(), att_x.end()) - att_x.begin();
            int hi = std::max_element(att_x.begin(), att_x.end()) - att_x.begin();
            if (!has_stub[lo]) att_x[lo] = blocks[lo].bbox.x2;
            if (!has_stub[hi]) att_x[hi] = blocks[hi].bbox.x1;
        }
        for (int iter = 0; iter < n; ++iter) {
            int lo = std::min_element(att_x.begin(), att_x.end()) - att_x.begin();
            int hi = std::max_element(att_x.begin(), att_x.end()) - att_x.begin();
            bool changed = false;
            if (has_stub[lo]) {
                int target = blocks[lo].bbox.x2;
                if (target > att_x[lo]) { att_x[lo] = target; changed = true; }
            }
            if (has_stub[hi]) {
                int target = blocks[hi].bbox.x1;
                if (target < att_x[hi]) { att_x[hi] = target; changed = true; }
            }
            if (!changed) break;
        }
        for (int iter2 = 0; iter2 < n; ++iter2) {
            int lo = std::min_element(att_x.begin(), att_x.end()) - att_x.begin();
            int hi = std::max_element(att_x.begin(), att_x.end()) - att_x.begin();
            bool changed = false;
            if (!has_stub[lo] && att_x[lo] != blocks[lo].bbox.x2) {
                att_x[lo] = blocks[lo].bbox.x2; changed = true;
            }
            if (!has_stub[hi] && att_x[hi] != blocks[hi].bbox.x1) {
                att_x[hi] = blocks[hi].bbox.x1; changed = true;
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
        if (!has_stub[i]) continue;
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
    std::vector<int> conn_x(n), att_y(n);
    std::vector<bool> has_stub(n);
    for (int i = 0; i < n; ++i) {
        conn_x[i]   = use_busterm_ ? blocks[i].orig_bbox.face_x(x_trunk) : pins[i].x;
        has_stub[i] = (conn_x[i] != x_trunk);
        att_y[i]    = pins[i].y;
    }

    if (use_busterm_) {
        if (!out_of_bbox) {
            int pt_lo = INT_MIN / 2, pt_hi = INT_MAX / 2;
            bool any_pt = false;
            for (int i = 0; i < n; ++i) {
                if (!has_stub[i]) {
                    any_pt = true;
                    const Rect& b = blocks[i].bbox;
                    pt_lo = std::max(pt_lo, b.x1);
                    pt_hi = std::min(pt_hi, b.y2);
                }
            }
            if (any_pt && pt_lo <= pt_hi) {
                int n_right = 0, n_left = 0;
                for (int i = 0; i < n; ++i) {
                    if (has_stub[i]) {
                        if (conn_x[i] > x_trunk) ++n_right;
                        else                      ++n_left;
                    }
                }
                if      (n_right > 0 && n_left == 0) x_trunk = pt_hi;
                else if (n_left  > 0 && n_right == 0) x_trunk = pt_lo;
                for (int i = 0; i < n; ++i)
                    conn_x[i] = blocks[i].orig_bbox.face_x(x_trunk);
            }
        }
        {
            int lo = std::min_element(att_y.begin(), att_y.end()) - att_y.begin();
            int hi = std::max_element(att_y.begin(), att_y.end()) - att_y.begin();
            if (!has_stub[lo]) att_y[lo] = blocks[lo].bbox.y2;
            if (!has_stub[hi]) att_y[hi] = blocks[hi].bbox.y1;
        }
        for (int iter = 0; iter < n; ++iter) {
            int lo = std::min_element(att_y.begin(), att_y.end()) - att_y.begin();
            int hi = std::max_element(att_y.begin(), att_y.end()) - att_y.begin();
            bool changed = false;
            if (has_stub[lo]) {
                int target = blocks[lo].bbox.y2;
                if (target > att_y[lo]) { att_y[lo] = target; changed = true; }
            }
            if (has_stub[hi]) {
                int target = blocks[hi].bbox.y1;
                if (target < att_y[hi]) { att_y[hi] = target; changed = true; }
            }
            if (!changed) break;
        }
        for (int iter2 = 0; iter2 < n; ++iter2) {
            int lo = std::min_element(att_y.begin(), att_y.end()) - att_y.begin();
            int hi = std::max_element(att_y.begin(), att_y.end()) - att_y.begin();
            bool changed = false;
            if (!has_stub[lo] && att_y[lo] != blocks[lo].bbox.y2) {
                att_y[lo] = blocks[lo].bbox.y2; changed = true;
            }
            if (!has_stub[hi] && att_y[hi] != blocks[hi].bbox.y1) {
                att_y[hi] = blocks[hi].bbox.y1; changed = true;
            }
            if (!changed) break;
        }
    }

    int y_lo = INT_MAX, y_hi = INT_MIN;
    for (int i = 0; i < n; ++i) {
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
        if (!has_stub[i]) continue;
        int seg_idx = (int)t.segments.size();
        t.segments.push_back(make_seg(conn_x[i], att_y[i], x_trunk, att_y[i], h_layer_));
        t.seg_busterms[seg_idx].first = blocks[i];
    }
    if (!t.segments.empty()) results.push_back(std::move(t));
}

// ---------------------------------------------------------------------------
// Multi-pin topology generation
// ---------------------------------------------------------------------------

std::vector<Topology> TopologyGenerator::generate_multicast_candidates(
    const std::string& src_name,
    const std::vector<std::string>& dst_names)
{
    std::vector<Topology> results;
    std::vector<Point>   pins;
    std::vector<Busterm> blocks;
    auto mk_bt = [&](const std::string& n) {
        auto cm = floorplan_.get_block_corner_margin(n);
        Rect orig = floorplan_.get_block_bounds(n);
        return Busterm{n, orig.shrink(cm.dx, cm.dy), orig};
    };
    {
        Busterm bt = mk_bt(src_name);
        pins.push_back(bt.bbox.center()); blocks.push_back(bt);
    }
    for (const auto& d : dst_names) {
        Busterm bt = mk_bt(d);
        pins.push_back(bt.bbox.center()); blocks.push_back(bt);
    }

    int x_lo = INT_MAX, x_hi = INT_MIN, y_lo = INT_MAX, y_hi = INT_MIN;
    for (const auto& p : pins) {
        x_lo = std::min(x_lo, p.x); x_hi = std::max(x_hi, p.x);
        y_lo = std::min(y_lo, p.y); y_hi = std::max(y_hi, p.y);
    }

    bool all_same_x = true, all_same_y = true;
    for (const auto& p : pins) {
        if (p.x != pins[0].x) all_same_x = false;
        if (p.y != pins[0].y) all_same_y = false;
    }
    if (all_same_x) {
        Topology t; t.type = "I_V";
        t.segments.push_back(make_seg(pins[0].x, y_lo, pins[0].x, y_hi, v_layer_));
        results.push_back(t);
    }
    if (all_same_y) {
        Topology t; t.type = "I_H";
        t.segments.push_back(make_seg(x_lo, pins[0].y, x_hi, pins[0].y, h_layer_));
        results.push_back(t);
    }
    if (all_same_x || all_same_y) {
        for (auto& t : results) annotate_endpoints(t, blocks);
        return results;
    }

    std::vector<Rect> block_rects;
    block_rects.reserve(blocks.size());
    for (const auto& bt : blocks) block_rects.push_back(bt.orig_bbox);
    std::vector<int> hanan_x, hanan_y;
    bundle_hanan_grid(block_rects, hanan_x, hanan_y);

    std::set<int> y_set, x_set;
    for (int i = 0; i + 1 < (int)hanan_y.size(); ++i) {
        int mid = (hanan_y[i] + hanan_y[i+1]) / 2;
        if (mid > y_lo && mid < y_hi) y_set.insert(mid);
    }
    for (int i = 0; i + 1 < (int)hanan_x.size(); ++i) {
        int mid = (hanan_x[i] + hanan_x[i+1]) / 2;
        if (mid > x_lo && mid < x_hi) x_set.insert(mid);
    }

    for (int y_t : y_set) add_trunk_h(pins, blocks, y_t, false, results);
    for (int x_t : x_set) add_trunk_v(pins, blocks, x_t, false, results);

    if ((int)hanan_y.size() >= 2) {
        int margin_y = std::max(1, (int)(0.1 * (hanan_y.back() - hanan_y[0])));
        for (int i = 0; i + 1 < (int)hanan_y.size(); ++i) {
            int mid = (hanan_y[i] + hanan_y[i+1]) / 2;
            if (mid < y_lo || mid > y_hi)
                add_trunk_h(pins, blocks, mid, true, results);
        }
        add_trunk_h(pins, blocks, hanan_y[0]      - margin_y, true, results);
        add_trunk_h(pins, blocks, hanan_y.back()  + margin_y, true, results);
    }
    if ((int)hanan_x.size() >= 2) {
        int margin_x = std::max(1, (int)(0.1 * (hanan_x.back() - hanan_x[0])));
        for (int i = 0; i + 1 < (int)hanan_x.size(); ++i) {
            int mid = (hanan_x[i] + hanan_x[i+1]) / 2;
            if (mid < x_lo || mid > x_hi)
                add_trunk_v(pins, blocks, mid, true, results);
        }
        add_trunk_v(pins, blocks, hanan_x[0]      - margin_x, true, results);
        add_trunk_v(pins, blocks, hanan_x.back()  + margin_x, true, results);
    }

    for (auto& t : results) annotate_endpoints(t, blocks);
    add_mst_candidates(blocks, results);
    add_multi_trunk_candidates(pins, blocks, results);
    annotate_and_sort(results);
    filter_pinched(results);
    return results;
}

// Find points P1 in r1, P2 in r2 that minimize Manhattan distance.
static void closest_points(const Rect& r1, const Rect& r2, Point& p1, Point& p2) {
    if (r1.x2 < r2.x1) { p1.x = r1.x2; p2.x = r2.x1; }
    else if (r2.x2 < r1.x1) { p1.x = r1.x1; p2.x = r2.x2; }
    else { p1.x = p2.x = (std::max(r1.x1, r2.x1) + std::min(r1.x2, r2.x2)) / 2; }

    if (r1.y2 < r2.y1) { p1.y = r1.y2; p2.y = r2.y1; }
    else if (r2.y2 < r1.y1) { p1.y = r1.y1; p2.y = r2.y2; }
    else { p1.y = p2.y = (std::max(r1.y1, r2.y1) + std::min(r1.y2, r2.y2)) / 2; }
}

void TopologyGenerator::add_mst_candidates(const std::vector<Busterm>& blocks,
                                           std::vector<Topology>& results) {
    if (blocks.size() < 2) return;
    std::vector<std::pair<std::string, Rect>> nodes;
    for (const auto& bt : blocks) nodes.push_back({bt.block_name, bt.bbox});
    auto mst_edges = compute_mst(nodes);

    for (int strategy = 0; strategy < 2; ++strategy) {
        Topology mst;
        mst.type = (strategy == 0) ? "MST_HV" : "MST_VH";
        for (const auto& edge : mst_edges) {
            Point p1, p2;
            closest_points(nodes[edge.u].second, nodes[edge.v].second, p1, p2);
            if (p1.x == p2.x && p1.y == p2.y) continue;
            if (p1.x == p2.x) {
                mst.segments.push_back(make_seg(p1.x, p1.y, p1.x, p2.y, v_layer_));
            } else if (p1.y == p2.y) {
                mst.segments.push_back(make_seg(p1.x, p1.y, p2.x, p1.y, h_layer_));
            } else {
                if (strategy == 0) {
                    mst.segments.push_back(make_seg(p1.x, p1.y, p2.x, p1.y, h_layer_));
                    mst.segments.push_back(make_seg(p2.x, p1.y, p2.x, p2.y, v_layer_));
                } else {
                    mst.segments.push_back(make_seg(p1.x, p1.y, p1.x, p2.y, v_layer_));
                    mst.segments.push_back(make_seg(p1.x, p2.y, p2.x, p2.y, h_layer_));
                }
            }
        }
        annotate_endpoints(mst, blocks);
        results.push_back(std::move(mst));
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

        for (int i = 0; i < (int)blocks.size(); ++i) {
            int yt = (pins[i].y <= y_mid) ? y_t1 : y_t2;
            int face_y = blocks[i].bbox.face_y(yt);
            if (face_y != yt) {
                int si = (int)t.segments.size();
                t.segments.push_back(make_seg(pins[i].x, blocks[i].orig_bbox.face_y(yt), pins[i].x, yt, v_layer_));
                t.seg_busterms[si].first = blocks[i];
            }
        }
        results.push_back(std::move(t));
    }
}

std::vector<Topology> TopologyGenerator::generate_candidates(const std::string& src_name, const std::string& dst_name) {
    std::vector<Topology> candidates;
    auto mk_bt = [&](const std::string& n) {
        auto cm = floorplan_.get_block_corner_margin(n);
        Rect orig = floorplan_.get_block_bounds(n);
        return Busterm{n, orig.shrink(cm.dx, cm.dy), orig};
    };
    Busterm src_bt = mk_bt(src_name);
    Busterm dst_bt = mk_bt(dst_name);
    const Rect& src = src_bt.bbox;
    const Rect& dst = dst_bt.bbox;
    const Rect& s_orig = src_bt.orig_bbox;
    const Rect& d_orig = dst_bt.orig_bbox;

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
    bundle_hanan_grid({s_orig, d_orig}, hanan_x, hanan_y);

    std::vector<int> chan_x, chan_y;
    for (int i = 0; i + 1 < (int)hanan_x.size(); ++i)
        chan_x.push_back((hanan_x[i] + hanan_x[i+1]) / 2);
    for (int i = 0; i + 1 < (int)hanan_y.size(); ++i)
        chan_y.push_back((hanan_y[i] + hanan_y[i+1]) / 2);

    if (hanan_x.size() >= 2) {
        int margin_x = std::max(1, (int)(0.1 * (hanan_x.back() - hanan_x[0])));
        chan_x.insert(chan_x.begin(), hanan_x[0]       - margin_x);
        chan_x.push_back             (hanan_x.back()   + margin_x);
    }
    if (hanan_y.size() >= 2) {
        int margin_y = std::max(1, (int)(0.1 * (hanan_y.back() - hanan_y[0])));
        chan_y.insert(chan_y.begin(), hanan_y[0]       - margin_y);
        chan_y.push_back             (hanan_y.back()   + margin_y);
    }

    add_z_shapes(src_bt, dst_bt, chan_x, chan_y, candidates);
    add_u_shapes(src_bt, dst_bt, chan_x, chan_y, candidates);
    if (allow_double_detour_)
        add_uu_shapes(src_bt, dst_bt, chan_x, chan_y, candidates);
    for (auto& t : candidates) annotate_endpoints(t, {src_bt, dst_bt});
    annotate_and_sort(candidates);
    filter_pinched(candidates);
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

} // namespace interconnect
