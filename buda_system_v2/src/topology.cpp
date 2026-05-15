#include "topology.h"
#include "conn_topology.h"
#include <cmath>
#include <climits>
#include <set>
#include <string>
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
    Point s = src.center(); Point d = dst.center();

    // L_HV: horizontal first, then vertical to dst y-face.
    //
    // The Busterm bboxes are already margin-inset, so all face/clamp operations
    // produce positions within the margin zone automatically.
    //
    // Overlap case: when src and dst x-ranges overlap the standard bend_x
    // lands inside src (sx == bend_x → H zero-length).  Route the V through
    // dst's exclusive zone(s) using a single heuristic y-level.
    {
        int sx     = use_busterm_ ? src.face_x(d.x) : s.x;
        int bend_x = use_busterm_ ? dst.face_x(sx)  : d.x;

        if (use_busterm_ && sx == bend_x) {
            // H collapses — route via each of dst's exclusive x-zones.
            int hy = (d.y < src.y1) ? src.y1
                   : (d.y > src.y2) ? src.y2 : s.y;
            int dy = dst.face_y(hy);
            // dst left of src: [dst.x1, src.x1)
            if (dst.x1 < src.x1) {
                int bx = (dst.x1 + src.x1) / 2;
                Topology hv; hv.type = "L_HV";
                hv.segments.push_back(make_seg(src.x1, hy, bx, hy, h_layer_));
                if (hy != dy) hv.segments.push_back(make_seg(bx, hy, bx, dy, v_layer_));
                if (hv.segments.size() == 2) results.push_back(hv);
            }
            // dst right of src: (src.x2, dst.x2]
            if (dst.x2 > src.x2) {
                int bx = (src.x2 + dst.x2) / 2;
                Topology hv; hv.type = "L_HV";
                hv.segments.push_back(make_seg(src.x2, hy, bx, hy, h_layer_));
                if (hy != dy) hv.segments.push_back(make_seg(bx, hy, bx, dy, v_layer_));
                if (hv.segments.size() == 2) results.push_back(hv);
            }
        } else if (use_busterm_) {
            // Two independent options (below dst / above dst).  The bboxes are
            // margin-inset so src.y2 and src.y1 are already the valid face positions.

            // Option 1: H below dst, V up to dst.y1
            if (src.y1 < dst.y1) {
                int hy = (src.y2 < dst.y1) ? src.y2 : dst.y1 - 1;  // dst above or overlapping
                if (hy >= src.y1 && hy < dst.y1) {
                    Topology hv; hv.type = "L_HV";
                    hv.segments.push_back(make_seg(sx,     hy,     bend_x, hy,     h_layer_));
                    hv.segments.push_back(make_seg(bend_x, hy,     bend_x, dst.y1, v_layer_));
                    results.push_back(hv);
                }
            }
            // Option 2: H above dst, V down to dst.y2
            if (src.y2 > dst.y2) {
                int hy = (src.y1 > dst.y2) ? src.y1 : dst.y2 + 1;  // dst below or overlapping
                if (hy > dst.y2 && hy <= src.y2) {
                    Topology hv; hv.type = "L_HV";
                    hv.segments.push_back(make_seg(sx,     hy,     bend_x, hy,     h_layer_));
                    hv.segments.push_back(make_seg(bend_x, hy,     bend_x, dst.y2, v_layer_));
                    results.push_back(hv);
                }
            }
        } else {
            // Non-busterm: single centre-to-centre topo.
            int hy = s.y, dy = d.y;
            Topology hv; hv.type = "L_HV";
            if (sx != bend_x) hv.segments.push_back(make_seg(sx, hy, bend_x, hy, h_layer_));
            if (hy != dy)      hv.segments.push_back(make_seg(bend_x, hy, bend_x, dy, v_layer_));
            if (hv.segments.size() == 2) results.push_back(hv);
        }
    }

    // L_VH: vertical first, then horizontal to dst x-face.
    //
    // In busterm mode the bboxes are margin-inset, so src.x1/x2 are already the
    // valid V-stub x positions and dst.y1/y2 are the valid H connection points.
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
            // src right of dst: (dst.x2, src.x2]
            if (src.x2 > dst.x2) {
                int vx2 = (dst.x2 + src.x2) / 2;
                int dx2 = dst.face_x(vx2);   // = dst.x2
                Topology vh; vh.type = "L_VH";
                if (sy != bend_y) vh.segments.push_back(make_seg(vx2, sy, vx2, bend_y, v_layer_));
                if (vx2 != dx2)   vh.segments.push_back(make_seg(vx2, bend_y, dx2, bend_y, h_layer_));
                if (vh.segments.size() == 2) results.push_back(vh);
            }
            // src left of dst: [src.x1, dst.x1)
            if (src.x1 < dst.x1) {
                int vx2 = (src.x1 + dst.x1) / 2;
                int dx2 = dst.face_x(vx2);   // = dst.x1
                Topology vh; vh.type = "L_VH";
                if (sy != bend_y) vh.segments.push_back(make_seg(vx2, sy, vx2, bend_y, v_layer_));
                if (vx2 != dx2)   vh.segments.push_back(make_seg(vx2, bend_y, dx2, bend_y, h_layer_));
                if (vh.segments.size() == 2) results.push_back(vh);
            }
        } else if (use_busterm_) {
            // Two independent options mirroring L_HV.  dst bbox is margin-inset so
            // dst.y2/y1 are already valid H connection points on dst's x-face.

            // Option A: H below src, V stub down from src bottom face
            if (dst.y1 < src.y1) {
                int bend_y_a = (dst.y2 < src.y1) ? dst.y2 : src.y1 - 1;
                if (bend_y_a >= dst.y1 && bend_y_a < src.y1) {
                    Topology vh; vh.type = "L_VH";
                    vh.segments.push_back(make_seg(vx, src.y1,   vx, bend_y_a, v_layer_));
                    vh.segments.push_back(make_seg(vx, bend_y_a, dx, bend_y_a, h_layer_));
                    results.push_back(vh);
                }
            }
            // Option B: H above src, V stub up from src top face
            if (dst.y2 > src.y2) {
                int bend_y_b = (dst.y1 > src.y2) ? dst.y1 : src.y2 + 1;
                if (bend_y_b > src.y2 && bend_y_b <= dst.y2) {
                    Topology vh; vh.type = "L_VH";
                    vh.segments.push_back(make_seg(vx, src.y2,   vx, bend_y_b, v_layer_));
                    vh.segments.push_back(make_seg(vx, bend_y_b, dx, bend_y_b, h_layer_));
                    results.push_back(vh);
                }
            }
        } else {
            Topology vh; vh.type = "L_VH";
            if (sy != bend_y)
                vh.segments.push_back(make_seg(vx, sy, vx, bend_y, v_layer_));
            if (vx != dx)
                vh.segments.push_back(make_seg(vx, bend_y, dx, bend_y, h_layer_));
            if (vh.segments.size() == 2) results.push_back(vh);
        }
    }
}

// Helper: H-stub y-level for HVH topologies.
// The bbox is already margin-inset, so a plain clamp to [blk.y1, blk.y2] lands
// within the margin zone.  When there is no stub the block face is used directly.
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
    Point s = src.center(); Point d = dst.center();

    // Z_HVH: trunk is vertical at x_cut between the two block centres.
    //
    // Standard case: ty_src != ty_dst — H segments are at different y levels,
    // V segment is non-degenerate.
    //
    // Spread case: when the two BUSTERMs are y-aligned (ty_src == ty_dst), the
    // V segment collapses to zero length.  Instead, spread the two H segments
    // to opposite corners of the block y-range (one near the top face, one near
    // the bottom face) so the V is always visible.
    //
    // To guarantee NUTS places the two H segments on different tracks, each H is
    // extended by ovlp units past the trunk so the two spans strictly overlap:
    //   H1: [sx,        x_cut + ovlp]   (right stub, extends past trunk)
    //   H2: [x_cut - ovlp, dx         ] (left stub,  starts before trunk)
    // ovlp is sized so that the overlap region is wider than the 10% span
    // threshold used by the span-adjustment SET/EXTEND decision in NUTS, which
    // preserves the overlap after track placement.
    // The Busterm bboxes are already margin-inset, so face/clamp operations on
    // src/dst naturally land within the margin zone.
    int min_x = std::min(s.x, d.x), max_x = std::max(s.x, d.x);
    for (int x_cut : x_grid) {
        if (x_cut > min_x && x_cut < max_x) {
            int sx = use_busterm_ ? src.face_x(x_cut) : s.x;
            int dx = use_busterm_ ? dst.face_x(x_cut) : d.x;
            int ty_src = stub_y(use_busterm_, sx != x_cut, src, d.y, s.y);
            int ty_dst = stub_y(use_busterm_, dx != x_cut, dst, s.y, d.y);

            if (ty_src != ty_dst) {
                // Standard Z_HVH — no spread or overlap needed.
                Topology z; z.type = "Z_HVH@x" + std::to_string(x_cut) + "@y" + std::to_string(ty_src);
                if (sx != x_cut)
                    z.segments.push_back(make_seg(sx, ty_src, x_cut, ty_src, h_layer_));
                z.segments.push_back(make_seg(x_cut, ty_src, x_cut, ty_dst, v_layer_));
                if (x_cut != dx)
                    z.segments.push_back(make_seg(x_cut, ty_dst, dx, ty_dst, h_layer_));
                if (z.segments.size() == 3) results.push_back(z);
            } else if (use_busterm_ && sx != x_cut && x_cut != dx) {
                // Spread Z_HVH: both BUSTERMs at same y — force two distinct
                // y-levels so the V segment is non-degenerate.  Generate two
                // mirror variants (top/bottom and bottom/top).
                int sy_hi = src.y2;   // near src top corner (bbox already margin-inset)
                int sy_lo = src.y1;   // near src bottom corner
                int dy_hi = dst.y2;   // near dst top corner
                int dy_lo = dst.y1;   // near dst bottom corner

                // ovlp: extend each H past the trunk so spans strictly overlap.
                // Must be < 11% of the resulting H span so NUTS span-adjustment
                // uses SET (not EXTEND) for precise alignment at trunk stripe edges.
                int h1_len = x_cut - sx;
                int ovlp   = std::max(1, h1_len / 20);  // ~5% of H1 length → SET fires

                // Variant 1: src near top → dst near bottom
                if (sy_hi != dy_lo) {
                    Topology z; z.type = "Z_HVH@x" + std::to_string(x_cut) + "@y" + std::to_string(sy_hi);
                    z.segments.push_back(make_seg(sx,           sy_hi, x_cut + ovlp, sy_hi, h_layer_));
                    z.segments.push_back(make_seg(x_cut,        sy_hi, x_cut,        dy_lo, v_layer_));
                    z.segments.push_back(make_seg(x_cut - ovlp, dy_lo, dx,           dy_lo, h_layer_));
                    results.push_back(z);
                }
                // Variant 2: src near bottom → dst near top
                if (sy_lo != dy_hi) {
                    Topology z; z.type = "Z_HVH@x" + std::to_string(x_cut) + "@y" + std::to_string(sy_lo);
                    z.segments.push_back(make_seg(sx,           sy_lo, x_cut + ovlp, sy_lo, h_layer_));
                    z.segments.push_back(make_seg(x_cut,        sy_lo, x_cut,        dy_hi, v_layer_));
                    z.segments.push_back(make_seg(x_cut - ovlp, dy_hi, dx,           dy_hi, h_layer_));
                    results.push_back(z);
                }
            }
        }
    }

    // Z_VHV: trunk is horizontal at y_cut between the two block centres.
    //
    // Spread case: when BUSTERMs are x-aligned (vx_src == vx_dst), the H segment
    // collapses.  Instead, spread the two V segments to opposite sides of the block
    // x-range so the H is always non-degenerate.  Each V is extended by ovlp units
    // past y_cut so spans overlap → NUTS places them on different tracks.
    int min_y = std::min(s.y, d.y), max_y = std::max(s.y, d.y);
    for (int y_cut : y_grid) {
        if (y_cut > min_y && y_cut < max_y) {
            int sy = use_busterm_ ? src.face_y(y_cut) : s.y;
            int dy = use_busterm_ ? dst.face_y(y_cut) : d.y;
            int vx_src = stub_x(use_busterm_, sy != y_cut, src, d.x, s.x);
            int vx_dst = stub_x(use_busterm_, dy != y_cut, dst, s.x, d.x);

            if (vx_src != vx_dst) {
                // Standard Z_VHV — no spread needed.
                Topology z; z.type = "Z_VHV@y" + std::to_string(y_cut) + "@x" + std::to_string(vx_src);
                if (sy != y_cut)
                    z.segments.push_back(make_seg(vx_src, sy, vx_src, y_cut, v_layer_));
                z.segments.push_back(make_seg(vx_src, y_cut, vx_dst, y_cut, h_layer_));
                if (y_cut != dy)
                    z.segments.push_back(make_seg(vx_dst, y_cut, vx_dst, dy, v_layer_));
                if (z.segments.size() == 3) results.push_back(z);
            } else if (use_busterm_ && sy != y_cut && y_cut != dy) {
                // Spread Z_VHV: both BUSTERMs at same x — force two distinct
                // x-levels so the H segment is non-degenerate.
                int vx_hi = src.x2;   // near src right corner (bbox already margin-inset)
                int vx_lo = src.x1;   // near src left corner

                int v1_len = std::abs(y_cut - sy);
                int ovlp   = std::max(1, v1_len / 20);  // ~5% of V1 length → SET fires

                for (int flip = 0; flip < 2; ++flip) {
                    int x1 = flip ? vx_lo : vx_hi;   // V1 x-level (src side)
                    int x2 = flip ? vx_hi : vx_lo;   // V2 x-level (dst side)
                    if (x1 == x2) continue;
                    Topology z; z.type = "Z_VHV@y" + std::to_string(y_cut) + "@x" + std::to_string(x1);
                    z.segments.push_back(make_seg(x1, sy,           x1, y_cut + ovlp, v_layer_));
                    z.segments.push_back(make_seg(x1, y_cut,        x2, y_cut,        h_layer_));
                    z.segments.push_back(make_seg(x2, y_cut - ovlp, x2, dy,           v_layer_));
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
    Point s = src.center(); Point d = dst.center();
    int min_x = std::min(s.x, d.x), max_x = std::max(s.x, d.x);
    int min_y = std::min(s.y, d.y), max_y = std::max(s.y, d.y);

    // U_HVH: vertical detour trunk left/right of bounding box.
    // Busterm bboxes are already margin-inset; face/clamp naturally lands in margin zone.
    for (int x_cut : x_grid) {
        if (x_cut < min_x || x_cut > max_x) {
            int sx = use_busterm_ ? src.face_x(x_cut) : s.x;
            int dx = use_busterm_ ? dst.face_x(x_cut) : d.x;
            int ty_src = stub_y(use_busterm_, sx != x_cut, src, d.y, s.y);
            int ty_dst = stub_y(use_busterm_, dx != x_cut, dst, s.y, d.y);
            Topology u; u.type = "U_HVH@x" + std::to_string(x_cut);
            if (sx != x_cut)
                u.segments.push_back(make_seg(sx, ty_src, x_cut, ty_src, h_layer_));
            if (ty_src != ty_dst)
                u.segments.push_back(make_seg(x_cut, ty_src, x_cut, ty_dst, v_layer_));
            if (x_cut != dx)
                u.segments.push_back(make_seg(x_cut, ty_dst, dx, ty_dst, h_layer_));
            if (u.segments.size() == 3) results.push_back(u);
        }
    }

    // U_VHV: horizontal detour trunk above/below bounding box — M6 for long-haul trunk.
    for (int y_cut : y_grid) {
        if (y_cut < min_y || y_cut > max_y) {
            int sy = use_busterm_ ? src.face_y(y_cut) : s.y;
            int dy = use_busterm_ ? dst.face_y(y_cut) : d.y;
            int vx_src = stub_x(use_busterm_, sy != y_cut, src, d.x, s.x);
            int vx_dst = stub_x(use_busterm_, dy != y_cut, dst, s.x, d.x);
            Topology u; u.type = "U_VHV@y" + std::to_string(y_cut);
            if (sy != y_cut)
                u.segments.push_back(make_seg(vx_src, sy, vx_src, y_cut, v_layer_));
            if (vx_src != vx_dst)
                u.segments.push_back(make_seg(vx_src, y_cut, vx_dst, y_cut, h_layer_));
            if (y_cut != dy)
                u.segments.push_back(make_seg(vx_dst, y_cut, vx_dst, dy, v_layer_));
            if (u.segments.size() == 3) results.push_back(u);
        }
    }
}

void TopologyGenerator::add_uu_shapes(const Busterm& s_bt, const Busterm& d_bt,
                                       const std::vector<int>& x_grid,
                                       const std::vector<int>& y_grid,
                                       std::vector<Topology>& results) {
    if (!use_busterm_) return;  // only meaningful in busterm mode
    const Rect& src = s_bt.bbox; const Rect& dst = d_bt.bbox;
    Point s = src.center(); Point d = dst.center();
    int min_x = std::min(s.x, d.x), max_x = std::max(s.x, d.x);
    int min_y = std::min(s.y, d.y), max_y = std::max(s.y, d.y);
    // Block-pair bounding box — used for exit margins.
    int bp_x_lo = std::min({src.x1, src.x2, dst.x1, dst.x2});
    int bp_x_hi = std::max({src.x1, src.x2, dst.x1, dst.x2});
    int bp_y_lo = std::min({src.y1, src.y2, dst.y1, dst.y2});
    int bp_y_hi = std::max({src.y1, src.y2, dst.y1, dst.y2});
    int margin_x = std::max(1, (int)(0.1 * (bp_x_hi - bp_x_lo)));
    int margin_y = std::max(1, (int)(0.1 * (bp_y_hi - bp_y_lo)));

    // ── UU_VHV ──────────────────────────────────────────────────────────────
    // Double-detour of U_VHV.  The src stub, normally a single V to the
    // bottom/top face of src, is replaced with an H+V L-shape that exits a
    // SIDE face of src (the face furthest from dst in x).
    // Shape (src → trunk → dst): H · V · H(trunk) · V
    for (int y_cut : y_grid) {
        if (y_cut >= min_y && y_cut <= max_y) continue;  // OOB only

        // Dst attachment (standard, same as U_VHV).
        int dy      = dst.face_y(y_cut);
        int vx_dst  = stub_x(true, dy != y_cut, dst, s.x, d.x);

        // Src: exit the x-face furthest from dst.
        int exit_x = (std::abs(src.x1 - d.x) >= std::abs(src.x2 - d.x))
                     ? src.x1 : src.x2;

        // sy_src: y-level on the src side face.
        // The bbox is already margin-inset so src.y1/y2 are the valid face positions.
        int sy_src = (y_cut < min_y) ? src.y1   // trunk below: src bottom face
                                     : src.y2;  // trunk above: src top face

        // x_corner: where the V leg of the src L-stub is placed.
        // 10% of block-pair bbox further out from src's exit face.
        int x_corner = (exit_x == src.x1) ? src.x1 - margin_x
                                           : src.x2 + margin_x;

        Topology uu; uu.type = "UU_VHV@y" + std::to_string(y_cut);
        if (exit_x != x_corner)
            uu.segments.push_back(make_seg(exit_x, sy_src, x_corner, sy_src, h_layer_)); // H
        if (sy_src != y_cut)
            uu.segments.push_back(make_seg(x_corner, sy_src, x_corner, y_cut, v_layer_)); // V
        if (x_corner != vx_dst)
            uu.segments.push_back(make_seg(x_corner, y_cut, vx_dst, y_cut, h_layer_));   // H trunk
        if (y_cut != dy)
            uu.segments.push_back(make_seg(vx_dst, y_cut, vx_dst, dy, v_layer_));         // V to dst
        if ((int)uu.segments.size() >= 3) results.push_back(uu);
    }

    // ── UU_HVH ──────────────────────────────────────────────────────────────
    // Double-detour of U_HVH.  The src stub, normally a single H to the
    // left/right face of src, is replaced with a V+H L-shape that exits a
    // TOP or BOTTOM face of src (the face furthest from dst in y).
    // Shape (src → trunk → dst): V · H · V(trunk) · H
    for (int x_cut : x_grid) {
        if (x_cut >= min_x && x_cut <= max_x) continue;  // OOB only

        // Dst attachment (standard, same as U_HVH).
        int dx      = dst.face_x(x_cut);
        int ty_dst  = stub_y(true, dx != x_cut, dst, s.y, d.y);

        // Src: exit the y-face furthest from dst.
        int exit_y = (std::abs(src.y1 - d.y) >= std::abs(src.y2 - d.y))
                     ? src.y1 : src.y2;

        // tx_src: x-level on the src exit face.
        // The bbox is already margin-inset so src.x1/x2 are the valid face positions.
        int tx_src = (x_cut < min_x) ? src.x1   // trunk left:  src left face
                                     : src.x2;  // trunk right: src right face

        // y_corner: where the H leg of the src L-stub is placed.
        // 10% of block-pair bbox further out from src's exit face.
        int y_corner = (exit_y == src.y1) ? src.y1 - margin_y
                                           : src.y2 + margin_y;

        Topology uu; uu.type = "UU_HVH@x" + std::to_string(x_cut);
        if (exit_y != y_corner)
            uu.segments.push_back(make_seg(tx_src, exit_y, tx_src, y_corner, v_layer_)); // V
        if (tx_src != x_cut)
            uu.segments.push_back(make_seg(tx_src, y_corner, x_cut, y_corner, h_layer_)); // H
        if (y_corner != ty_dst)
            uu.segments.push_back(make_seg(x_cut, y_corner, x_cut, ty_dst, v_layer_));    // V trunk
        if (x_cut != dx)
            uu.segments.push_back(make_seg(x_cut, ty_dst, dx, ty_dst, h_layer_));          // H to dst
        if ((int)uu.segments.size() >= 3) results.push_back(uu);
    }
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

// Build a Hanan grid from a specific set of blocks only (not the full floorplan).
// Using just the bundle's own blocks avoids trunk candidates at unrelated block
// edges, which would produce redundant or noise topologies.
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
            return a.estimated_wirelength < b.estimated_wirelength;
        });
}

// Populate Topology::seg_busterms for every segment endpoint that lies on the
// face of one of the given blocks.  Checks only the supplied blocks (not the
// whole floorplan) so there is never ambiguity when two blocks share a face
// coordinate — only the block that is structurally relevant is in the list.
static void annotate_endpoints(Topology& topo,
                                const std::vector<Busterm>& blocks) {
    for (int i = 0; i < (int)topo.segments.size(); ++i) {
        const Segment& seg = topo.segments[i];
        bool horiz = (seg.start.y == seg.end.y);
        for (const Busterm& bt : blocks) {
            const Rect& r = bt.bbox;
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

// H-trunk at y_trunk: horizontal spine + vertical stubs to nearest block face.
// In busterm mode the stubs slide in x so the spine is as short as possible:
//   • leftmost stub slides toward +x (block's right face minus 10% margin)
//   • rightmost stub slides toward −x (block's left face plus 10% margin)
//   • pass-through blocks (trunk inside block) generate no stub and the spine
//     endpoint is pulled back to the block face rather than the block centre.
// out_of_bbox=true → spine uses M6 (long-haul detour layer).
void TopologyGenerator::add_trunk_h(const std::vector<Point>& pins,
                                     const std::vector<Busterm>& blocks,
                                     int y_trunk, bool out_of_bbox,
                                     std::vector<Topology>& results)
{
    int n = (int)pins.size();

    // conn_y: block face y toward y_trunk (or y_trunk itself if trunk passes through).
    // att_x:  stub x-position, initially block centre; will be slid to shorten spine.
    std::vector<int> conn_y(n), att_x(n);
    std::vector<bool> has_stub(n);
    for (int i = 0; i < n; ++i) {
        conn_y[i]   = use_busterm_ ? blocks[i].bbox.face_y(y_trunk) : pins[i].y;
        has_stub[i] = (conn_y[i] != y_trunk);
        att_x[i]    = pins[i].x;
    }

    if (use_busterm_) {
        // ── In-bbox pass-through snap ──────────────────────────────────────────
        // When the trunk lands inside one or more pass-through blocks and all
        // stubbed block faces lie on the SAME SIDE, we can minimise stub length
        // by pulling y_trunk to the nearest pass-through boundary on that side
        // (10 % from the block edge, consistent with busterm margin rule).
        // Only applied for in-bbox trunks to avoid pushing OOB trunks back inside.
        if (!out_of_bbox) {
            int pt_lo = INT_MIN / 2, pt_hi = INT_MAX / 2;
            bool any_pt = false;
            for (int i = 0; i < n; ++i) {
                if (!has_stub[i]) {
                    any_pt = true;
                    const Rect& b = blocks[i].bbox;
                    // bbox is already margin-inset; use full extent as valid range.
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
                // Only snap when all stubs are on one side (balanced case is
                // already at the optimal wirelength regardless of trunk y).
                if      (n_above > 0 && n_below == 0) y_trunk = pt_hi; // stubs above → snap up
                else if (n_below > 0 && n_above == 0) y_trunk = pt_lo; // stubs below → snap down
                // Recompute conn_y (stub faces are unchanged, but pass-through
                // conn_y = y_trunk, which we won't use for stub generation).
                for (int i = 0; i < n; ++i)
                    conn_y[i] = blocks[i].bbox.face_y(y_trunk);
                // has_stub stays unchanged — y_trunk is still within each
                // pass-through block's range after clamping by pt_lo/pt_hi.
            }
        }

        // For pass-through blocks at the spine extremes, pull the endpoint back to
        // the block face so the spine doesn't poke into the block.
        {
            int lo = std::min_element(att_x.begin(), att_x.end()) - att_x.begin();
            int hi = std::max_element(att_x.begin(), att_x.end()) - att_x.begin();
            if (!has_stub[lo]) att_x[lo] = blocks[lo].bbox.x2; // right face (step inward)
            if (!has_stub[hi]) att_x[hi] = blocks[hi].bbox.x1; // left face (step inward)
        }

        // Iteratively slide the extreme stubs inward to minimise spine length.
        for (int iter = 0; iter < n; ++iter) {
            int lo = std::min_element(att_x.begin(), att_x.end()) - att_x.begin();
            int hi = std::max_element(att_x.begin(), att_x.end()) - att_x.begin();
            bool changed = false;
            if (has_stub[lo]) {
                // bbox is already margin-inset; slide to face edge directly.
                int target = blocks[lo].bbox.x2;
                if (target > att_x[lo]) { att_x[lo] = target; changed = true; }
            }
            if (has_stub[hi]) {
                int target = blocks[hi].bbox.x1;
                if (target < att_x[hi]) { att_x[hi] = target; changed = true; }
            }
            if (!changed) break;
        }

        // Post-slide pull-to-face: a pass-through block may have been shadowed
        // at the extreme by a stub block that was subsequently slid inward,
        // leaving the pass-through as the new extreme at its centre position.
        // Snap any such block to its face now.
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

    // Spine extent covers ALL blocks: stubs define their own connection points, and
    // pass-through blocks that fall outside the stub range must still be reached by
    // the spine (their att_x was already set to the block face in the pull-to-face
    // step above, so the spine endpoint lands exactly on the block face and
    // ConnTopology registers a BUSTERM connection from the spine endpoint).
    int x_lo = INT_MAX, x_hi = INT_MIN;
    for (int i = 0; i < n; ++i) {
        x_lo = std::min(x_lo, att_x[i]); x_hi = std::max(x_hi, att_x[i]);
    }
    if (x_lo >= x_hi) return; // degenerate (zero-length spine), skip

    Topology t;
    t.type               = std::string(out_of_bbox ? "TRUNK_H_OOB" : "TRUNK_H")
                           + "@y" + std::to_string(y_trunk);
    t.trunk_location     = y_trunk;
    // Blocks at the spine endpoints (att_x == x_lo or x_hi) are BUSTERM
    // connections of the spine, not pass-throughs.  Only interior blocks
    // (strictly between x_lo and x_hi) are true pass-throughs.
    t.pass_through_count = 0;
    for (int i = 0; i < n; ++i)
        if (!has_stub[i] && att_x[i] != x_lo && att_x[i] != x_hi)
            ++t.pass_through_count;
    if (x_lo < x_hi)
        t.segments.push_back(make_seg(x_lo, y_trunk, x_hi, y_trunk, h_layer_));

    // Add stubs and annotate each stub's block-face endpoint directly, bypassing
    // annotate_endpoints geometric scan.  This avoids shared-face ambiguity when
    // two adjacent blocks share an x-coordinate (e.g. u14.x2 == u15.x1): the
    // scan would pick whichever block sorts first, not the one the stub belongs to.
    for (int i = 0; i < n; ++i) {
        if (!has_stub[i]) continue;
        int seg_idx = (int)t.segments.size();
        t.segments.push_back(make_seg(att_x[i], conn_y[i], att_x[i], y_trunk, v_layer_));
        // Stub start = (att_x[i], conn_y[i]) connects to blocks[i]'s y-face.
        t.seg_busterms[seg_idx].first = blocks[i];
    }

    if (!t.segments.empty()) results.push_back(std::move(t));
}

// V-trunk at x_trunk: vertical spine + horizontal stubs to nearest block face.
// In busterm mode the stubs slide in y to minimise spine length (symmetric to
// add_trunk_h above).
void TopologyGenerator::add_trunk_v(const std::vector<Point>& pins,
                                     const std::vector<Busterm>& blocks,
                                     int x_trunk, bool out_of_bbox,
                                     std::vector<Topology>& results)
{
    int n = (int)pins.size();

    std::vector<int> conn_x(n), att_y(n);
    std::vector<bool> has_stub(n);
    for (int i = 0; i < n; ++i) {
        conn_x[i]   = use_busterm_ ? blocks[i].bbox.face_x(x_trunk) : pins[i].x;
        has_stub[i] = (conn_x[i] != x_trunk);
        att_y[i]    = pins[i].y;
    }

    if (use_busterm_) {
        // ── In-bbox pass-through snap ──────────────────────────────────────────
        if (!out_of_bbox) {
            int pt_lo = INT_MIN / 2, pt_hi = INT_MAX / 2;
            bool any_pt = false;
            for (int i = 0; i < n; ++i) {
                if (!has_stub[i]) {
                    any_pt = true;
                    const Rect& b = blocks[i].bbox;
                    // bbox is already margin-inset; use full extent as valid range.
                    pt_lo = std::max(pt_lo, b.x1);
                    pt_hi = std::min(pt_hi, b.x2);
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
                    conn_x[i] = blocks[i].bbox.face_x(x_trunk);
            }
        }

        // Pull pass-through block endpoints to the block face.
        {
            int lo = std::min_element(att_y.begin(), att_y.end()) - att_y.begin();
            int hi = std::max_element(att_y.begin(), att_y.end()) - att_y.begin();
            if (!has_stub[lo]) att_y[lo] = blocks[lo].bbox.y2; // bottom face (step inward)
            if (!has_stub[hi]) att_y[hi] = blocks[hi].bbox.y1; // top face (step inward)
        }

        // Slide extreme stubs inward to shorten spine.
        for (int iter = 0; iter < n; ++iter) {
            int lo = std::min_element(att_y.begin(), att_y.end()) - att_y.begin();
            int hi = std::max_element(att_y.begin(), att_y.end()) - att_y.begin();
            bool changed = false;
            if (has_stub[lo]) {
                // bbox is already margin-inset; slide to face edge directly.
                int target = blocks[lo].bbox.y2;
                if (target > att_y[lo]) { att_y[lo] = target; changed = true; }
            }
            if (has_stub[hi]) {
                int target = blocks[hi].bbox.y1;
                if (target < att_y[hi]) { att_y[hi] = target; changed = true; }
            }
            if (!changed) break;
        }

        // Post-slide pull-to-face (symmetric to add_trunk_h).
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

    // Spine extent covers ALL blocks (symmetric to add_trunk_h above).
    int y_lo = INT_MAX, y_hi = INT_MIN;
    for (int i = 0; i < n; ++i) {
        y_lo = std::min(y_lo, att_y[i]); y_hi = std::max(y_hi, att_y[i]);
    }
    if (y_lo >= y_hi) return; // degenerate (zero-length spine), skip

    Topology t;
    t.type               = std::string(out_of_bbox ? "TRUNK_V_OOB" : "TRUNK_V")
                           + "@x" + std::to_string(x_trunk);
    t.trunk_location     = x_trunk;
    // Same rule as add_trunk_h: blocks at spine endpoints are BUSTERM connections.
    t.pass_through_count = 0;
    for (int i = 0; i < n; ++i)
        if (!has_stub[i] && att_y[i] != y_lo && att_y[i] != y_hi)
            ++t.pass_through_count;

    if (y_lo < y_hi)
        t.segments.push_back(make_seg(x_trunk, y_lo, x_trunk, y_hi, v_layer_));

    // Add stubs and annotate each stub's block-face endpoint directly (same
    // rationale as add_trunk_h: avoids shared-face ambiguity in annotate_endpoints).
    for (int i = 0; i < n; ++i) {
        if (!has_stub[i]) continue;
        int seg_idx = (int)t.segments.size();
        t.segments.push_back(make_seg(conn_x[i], att_y[i], x_trunk, att_y[i], h_layer_));
        // Stub start = (conn_x[i], att_y[i]) connects to blocks[i]'s x-face.
        t.seg_busterms[seg_idx].first = blocks[i];
    }

    if (!t.segments.empty()) results.push_back(std::move(t));
}

// ---------------------------------------------------------------------------
// Multi-pin topology generation (1 driver + N receivers)
// ---------------------------------------------------------------------------

std::vector<Topology> TopologyGenerator::generate_multicast_candidates(
    const std::string& src_name,
    const std::vector<std::string>& dst_names)
{
    std::vector<Topology> results;

    // Collect pin centres and block bounds in parallel order.
    std::vector<Point>   pins;
    std::vector<Busterm> blocks;
    auto mk_bt = [&](const std::string& n) {
        auto cm = floorplan_.get_block_corner_margin(n);
        return Busterm{n, floorplan_.get_block_bounds(n).shrink(cm.dx, cm.dy)};
    };
    {
        Busterm bt = mk_bt(src_name);
        pins.push_back(bt.bbox.center()); blocks.push_back(bt);
    }
    for (const auto& d : dst_names) {
        Busterm bt = mk_bt(d);
        pins.push_back(bt.bbox.center()); blocks.push_back(bt);
    }

    // Bounding box of all pin centres.
    int x_lo = INT_MAX, x_hi = INT_MIN, y_lo = INT_MAX, y_hi = INT_MIN;
    for (const auto& p : pins) {
        x_lo = std::min(x_lo, p.x); x_hi = std::max(x_hi, p.x);
        y_lo = std::min(y_lo, p.y); y_hi = std::max(y_hi, p.y);
    }

    // Degenerate I-shapes: all pins already share a coordinate.
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

    // Hanan grid from bundle blocks only — avoids trunk candidates at unrelated block edges.
    // bundle_hanan_grid needs vector<Rect>; extract from Busterm list.
    std::vector<Rect> block_rects;
    block_rects.reserve(blocks.size());
    for (const auto& bt : blocks) block_rects.push_back(bt.bbox);
    std::vector<int> hanan_x, hanan_y;
    bundle_hanan_grid(block_rects, hanan_x, hanan_y);

    // In-bbox trunks: one candidate per Hanan channel — the midpoint of each
    // consecutive pair of Hanan lines whose midpoint falls strictly within the
    // pin bounding box.
    //
    // Pin-centre coordinates are intentionally NOT added here.  A pin centre
    // always falls inside a Hanan interval, so it produces the same has_stub
    // pattern and conn_y values as the interval's midpoint — the only difference
    // would be a slight shift in the spine coordinate, which adds no structural
    // value and creates near-duplicate candidates in the explorer.
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

    // OOB trunks: channel midpoints of Hanan intervals that fall outside the pin
    // bbox, plus outer-margin positions one step beyond each extreme Hanan line.
    // Never use exact Hanan lines (= block faces) for OOB trunks — placing the
    // trunk on a face collapses one stub to zero length, producing a degenerate
    // pass-through instead of a proper detour stub.  (Same logic as 2-pin U/C.)
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
                if (strategy == 0) { // HV
                    mst.segments.push_back(make_seg(p1.x, p1.y, p2.x, p1.y, h_layer_));
                    mst.segments.push_back(make_seg(p2.x, p1.y, p2.x, p2.y, v_layer_));
                } else { // VH
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

    // Bi-Trunk H: two horizontal trunks connected by a vertical spine
    {
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
                    t.segments.push_back(make_seg(pins[i].x, face_y, pins[i].x, yt, v_layer_));
                    t.seg_busterms[si].first = blocks[i];
                }
            }
            results.push_back(std::move(t));
        }
    }
}

// ---------------------------------------------------------------------------
// 2-pin candidates
// ---------------------------------------------------------------------------

std::vector<Topology> TopologyGenerator::generate_candidates(const std::string& src_name, const std::string& dst_name) {
    std::vector<Topology> candidates;
    auto mk_bt = [&](const std::string& n) {
        auto cm = floorplan_.get_block_corner_margin(n);
        return Busterm{n, floorplan_.get_block_bounds(n).shrink(cm.dx, cm.dy)};
    };
    Busterm src_bt = mk_bt(src_name);
    Busterm dst_bt = mk_bt(dst_name);
    const Rect& src = src_bt.bbox;
    const Rect& dst = dst_bt.bbox;

    // I-shapes: direct single-segment connection when blocks share an overlapping
    // x-range (vertical I) or y-range (horizontal I).  Only meaningful in busterm
    // mode where block extents matter; in centre mode blocks are treated as points.
    if (use_busterm_) {
        // I_V: blocks overlap in x → straight vertical wire through the overlap
        int xo_lo = std::max(src.x1, dst.x1), xo_hi = std::min(src.x2, dst.x2);
        if (xo_lo < xo_hi) {
            int x_mid  = (xo_lo + xo_hi) / 2;
            int src_y  = src.face_y(dst.center().y);
            int dst_y  = dst.face_y(src.center().y);
            if (src_y != dst_y) {
                Topology t; t.type = "I_V";
                t.segments.push_back(make_seg(x_mid, src_y, x_mid, dst_y, v_layer_));
                candidates.push_back(t);
            }
        }
        // I_H: blocks overlap in y → straight horizontal wire through the overlap
        int yo_lo = std::max(src.y1, dst.y1), yo_hi = std::min(src.y2, dst.y2);
        if (yo_lo < yo_hi) {
            int y_mid  = (yo_lo + yo_hi) / 2;
            int src_x  = src.face_x(dst.center().x);
            int dst_x  = dst.face_x(src.center().x);
            if (src_x != dst_x) {
                Topology t; t.type = "I_H";
                t.segments.push_back(make_seg(src_x, y_mid, dst_x, y_mid, h_layer_));
                candidates.push_back(t);
            }
        }
    }

    add_l_shapes(src_bt, dst_bt, candidates);
    std::vector<int> hanan_x, hanan_y;
    bundle_hanan_grid({src, dst}, hanan_x, hanan_y);

    // Z and U/C trunks: use channel midpoints so both stubs are always visible.
    // Hanan lines are block faces; a trunk on a block face collapses one stub.
    // Inner channel midpoints (between consecutive Hanan lines) serve Z trunks.
    // Outer channel midpoints (one step beyond each end, mirroring the outermost
    // real channel width) serve U/C trunks and ensure both stubs exist there too.
    std::vector<int> chan_x, chan_y;
    for (int i = 0; i + 1 < (int)hanan_x.size(); ++i)
        chan_x.push_back((hanan_x[i] + hanan_x[i+1]) / 2);
    for (int i = 0; i + 1 < (int)hanan_y.size(); ++i)
        chan_y.push_back((hanan_y[i] + hanan_y[i+1]) / 2);
    // Outer channel positions for U/C detour trunks: place the trunk 10% of the
    // block-pair bounding box size away from each bbox edge.
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
