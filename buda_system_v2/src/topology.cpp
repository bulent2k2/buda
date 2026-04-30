#include "topology.h"
#include <cmath>
#include <climits>
#include <set>
namespace interconnect {
void Floorplan::add_block(const std::string& name, int x1, int y1, int x2, int y2) {
    blocks_[name] = Rect{x1, y1, x2, y2};
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

// ---------------------------------------------------------------------------
// 2-pin shapes (L / Z / U)
// ---------------------------------------------------------------------------

void TopologyGenerator::add_l_shapes(const Rect& src, const Rect& dst, std::vector<Topology>& results) {
    Point s = src.center(); Point d = dst.center();

    // L_HV: horizontal first, then vertical.
    // Busterm: src connects at its nearest x-face toward d.x;
    //          dst connects at its nearest y-face toward s.y.
    {
        Topology hv; hv.type = "L_HV";
        int sx = use_busterm_ ? src.face_x(d.x) : s.x;
        int dy = use_busterm_ ? dst.face_y(s.y) : d.y;
        if (sx != d.x)                    // H stub from src to bend
            hv.segments.push_back(make_seg(sx, s.y, d.x, s.y, 4));
        if (s.y != dy)                    // V stub from bend to dst
            hv.segments.push_back(make_seg(d.x, s.y, d.x, dy, 5));
        if (!hv.segments.empty()) results.push_back(hv);
    }

    // L_VH: vertical first, then horizontal.
    {
        Topology vh; vh.type = "L_VH";
        int sy = use_busterm_ ? src.face_y(d.y) : s.y;
        int dx = use_busterm_ ? dst.face_x(s.x) : d.x;
        if (s.y != sy || sy != d.y)       // V stub from src to bend
            vh.segments.push_back(make_seg(s.x, sy != s.y ? sy : s.y, s.x, d.y, 5));
        if (s.x != dx)                    // H stub from bend to dst
            vh.segments.push_back(make_seg(s.x, d.y, dx, d.y, 4));
        if (!vh.segments.empty()) results.push_back(vh);
    }
}

void TopologyGenerator::add_z_shapes(const Rect& src, const Rect& dst, const std::vector<int>& x_grid, const std::vector<int>& /*y_grid*/, std::vector<Topology>& results) {
    Point s = src.center(); Point d = dst.center();
    int min_x = std::min(s.x, d.x), max_x = std::max(s.x, d.x);
    for (int x_cut : x_grid) {
        if (x_cut > min_x && x_cut < max_x) {
            int sx = use_busterm_ ? src.face_x(x_cut) : s.x;
            int dx = use_busterm_ ? dst.face_x(x_cut) : d.x;
            Topology z; z.type = "Z_HVH";
            if (sx != x_cut)
                z.segments.push_back(make_seg(sx, s.y, x_cut, s.y, 4));  // H stub src→cut
            z.segments.push_back(make_seg(x_cut, s.y, x_cut, d.y, 5));   // V trunk
            if (x_cut != dx)
                z.segments.push_back(make_seg(x_cut, d.y, dx, d.y, 4));  // H stub cut→dst
            if (!z.segments.empty()) results.push_back(z);
        }
    }
}

void TopologyGenerator::add_u_shapes(const Rect& src, const Rect& dst, const std::vector<int>& x_grid, const std::vector<int>& y_grid, std::vector<Topology>& results) {
    Point s = src.center(); Point d = dst.center();
    int min_x = std::min(s.x, d.x), max_x = std::max(s.x, d.x);
    int min_y = std::min(s.y, d.y), max_y = std::max(s.y, d.y);

    // Vertical U-trunks (detour left/right of bounding box)
    for (int x_cut : x_grid) {
        if (x_cut < min_x || x_cut > max_x) {
            int sx = use_busterm_ ? src.face_x(x_cut) : s.x;
            int dx = use_busterm_ ? dst.face_x(x_cut) : d.x;
            Topology u; u.type = "U_HVH";
            if (sx != x_cut)
                u.segments.push_back(make_seg(sx, s.y, x_cut, s.y, 4));
            u.segments.push_back(make_seg(x_cut, s.y, x_cut, d.y, 5));
            if (x_cut != dx)
                u.segments.push_back(make_seg(x_cut, d.y, dx, d.y, 4));
            if (!u.segments.empty()) results.push_back(u);
        }
    }
    // Horizontal U-trunks (detour above/below bounding box) — use M6 for the trunk
    for (int y_cut : y_grid) {
        if (y_cut < min_y || y_cut > max_y) {
            int sy = use_busterm_ ? src.face_y(y_cut) : s.y;
            int dy = use_busterm_ ? dst.face_y(y_cut) : d.y;
            Topology u; u.type = "U_VHV";
            if (sy != y_cut)
                u.segments.push_back(make_seg(s.x, sy, s.x, y_cut, 5));
            u.segments.push_back(make_seg(s.x, y_cut, d.x, y_cut, 6));   // M6 long-haul
            if (y_cut != dy)
                u.segments.push_back(make_seg(d.x, y_cut, d.x, dy, 5));
            if (!u.segments.empty()) results.push_back(u);
        }
    }
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

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
                                     const std::vector<Rect>& blocks,
                                     int y_trunk, bool out_of_bbox,
                                     std::vector<Topology>& results)
{
    int n = (int)pins.size();

    // conn_y: block face y toward y_trunk (or y_trunk itself if trunk passes through).
    // att_x:  stub x-position, initially block centre; will be slid to shorten spine.
    std::vector<int> conn_y(n), att_x(n);
    std::vector<bool> has_stub(n);
    for (int i = 0; i < n; ++i) {
        conn_y[i]   = use_busterm_ ? blocks[i].face_y(y_trunk) : pins[i].y;
        has_stub[i] = (conn_y[i] != y_trunk);
        att_x[i]    = pins[i].x;
    }

    if (use_busterm_) {
        // For pass-through blocks at the spine extremes, pull the endpoint back to
        // the block face so the spine doesn't poke into the block.
        {
            int lo = std::min_element(att_x.begin(), att_x.end()) - att_x.begin();
            int hi = std::max_element(att_x.begin(), att_x.end()) - att_x.begin();
            if (!has_stub[lo]) att_x[lo] = blocks[lo].x2; // right face (step inward)
            if (!has_stub[hi]) att_x[hi] = blocks[hi].x1; // left face (step inward)
        }

        // Iteratively slide the extreme stubs inward to minimise spine length.
        for (int iter = 0; iter < n; ++iter) {
            int lo = std::min_element(att_x.begin(), att_x.end()) - att_x.begin();
            int hi = std::max_element(att_x.begin(), att_x.end()) - att_x.begin();
            bool changed = false;
            if (has_stub[lo]) {
                int margin = std::max(1, (int)(0.1 * (blocks[lo].x2 - blocks[lo].x1)));
                int target = blocks[lo].x2 - margin;
                if (target > att_x[lo]) { att_x[lo] = target; changed = true; }
            }
            if (has_stub[hi]) {
                int margin = std::max(1, (int)(0.1 * (blocks[hi].x2 - blocks[hi].x1)));
                int target = blocks[hi].x1 + margin;
                if (target < att_x[hi]) { att_x[hi] = target; changed = true; }
            }
            if (!changed) break;
        }
    }

    int x_lo = *std::min_element(att_x.begin(), att_x.end());
    int x_hi = *std::max_element(att_x.begin(), att_x.end());

    Topology t;
    t.type           = out_of_bbox ? "TRUNK_H_OOB" : "TRUNK_H";
    t.trunk_location = y_trunk;
    int spine_layer  = out_of_bbox ? 6 : 4;

    if (x_lo < x_hi)
        t.segments.push_back(make_seg(x_lo, y_trunk, x_hi, y_trunk, spine_layer));

    for (int i = 0; i < n; ++i)
        if (has_stub[i])
            t.segments.push_back(make_seg(att_x[i], conn_y[i], att_x[i], y_trunk, 5));

    if (!t.segments.empty()) results.push_back(std::move(t));
}

// V-trunk at x_trunk: vertical spine + horizontal stubs to nearest block face.
// In busterm mode the stubs slide in y to minimise spine length (symmetric to
// add_trunk_h above).
void TopologyGenerator::add_trunk_v(const std::vector<Point>& pins,
                                     const std::vector<Rect>& blocks,
                                     int x_trunk, bool out_of_bbox,
                                     std::vector<Topology>& results)
{
    int n = (int)pins.size();

    std::vector<int> conn_x(n), att_y(n);
    std::vector<bool> has_stub(n);
    for (int i = 0; i < n; ++i) {
        conn_x[i]   = use_busterm_ ? blocks[i].face_x(x_trunk) : pins[i].x;
        has_stub[i] = (conn_x[i] != x_trunk);
        att_y[i]    = pins[i].y;
    }

    if (use_busterm_) {
        // Pull pass-through block endpoints to the block face.
        {
            int lo = std::min_element(att_y.begin(), att_y.end()) - att_y.begin();
            int hi = std::max_element(att_y.begin(), att_y.end()) - att_y.begin();
            if (!has_stub[lo]) att_y[lo] = blocks[lo].y2; // bottom face (step inward)
            if (!has_stub[hi]) att_y[hi] = blocks[hi].y1; // top face (step inward)
        }

        // Slide extreme stubs inward to shorten spine.
        for (int iter = 0; iter < n; ++iter) {
            int lo = std::min_element(att_y.begin(), att_y.end()) - att_y.begin();
            int hi = std::max_element(att_y.begin(), att_y.end()) - att_y.begin();
            bool changed = false;
            if (has_stub[lo]) {
                int margin = std::max(1, (int)(0.1 * (blocks[lo].y2 - blocks[lo].y1)));
                int target = blocks[lo].y2 - margin;
                if (target > att_y[lo]) { att_y[lo] = target; changed = true; }
            }
            if (has_stub[hi]) {
                int margin = std::max(1, (int)(0.1 * (blocks[hi].y2 - blocks[hi].y1)));
                int target = blocks[hi].y1 + margin;
                if (target < att_y[hi]) { att_y[hi] = target; changed = true; }
            }
            if (!changed) break;
        }
    }

    int y_lo = *std::min_element(att_y.begin(), att_y.end());
    int y_hi = *std::max_element(att_y.begin(), att_y.end());

    Topology t;
    t.type           = out_of_bbox ? "TRUNK_V_OOB" : "TRUNK_V";
    t.trunk_location = x_trunk;

    if (y_lo < y_hi)
        t.segments.push_back(make_seg(x_trunk, y_lo, x_trunk, y_hi, 5));

    for (int i = 0; i < n; ++i)
        if (has_stub[i])
            t.segments.push_back(make_seg(conn_x[i], att_y[i], x_trunk, att_y[i], 4));

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
    std::vector<Point> pins;
    std::vector<Rect>  blocks;
    {
        Rect r = floorplan_.get_block_bounds(src_name);
        pins.push_back(r.center()); blocks.push_back(r);
    }
    for (const auto& d : dst_names) {
        Rect r = floorplan_.get_block_bounds(d);
        pins.push_back(r.center()); blocks.push_back(r);
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
        t.segments.push_back(make_seg(pins[0].x, y_lo, pins[0].x, y_hi, 5));
        results.push_back(t);
    }
    if (all_same_y) {
        Topology t; t.type = "I_H";
        t.segments.push_back(make_seg(x_lo, pins[0].y, x_hi, pins[0].y, 4));
        results.push_back(t);
    }
    if (all_same_x || all_same_y) return results;

    std::vector<int> hanan_x, hanan_y;
    floorplan_.get_hanan_grid(hanan_x, hanan_y);

    // In-bbox trunks: pin-centre coordinates + channel centres (midpoints of
    // adjacent Hanan cell intervals within the pin bounding box).
    std::set<int> y_set, x_set;
    for (const auto& p : pins) { y_set.insert(p.y); x_set.insert(p.x); }

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

    // OOB trunks: Hanan grid lines strictly outside the pin bbox (detour routes).
    for (int y_t : hanan_y)
        if (y_t < y_lo || y_t > y_hi)
            add_trunk_h(pins, blocks, y_t, true, results);
    for (int x_t : hanan_x)
        if (x_t < x_lo || x_t > x_hi)
            add_trunk_v(pins, blocks, x_t, true, results);

    annotate_and_sort(results);
    return results;
}

// ---------------------------------------------------------------------------
// 2-pin candidates
// ---------------------------------------------------------------------------

std::vector<Topology> TopologyGenerator::generate_candidates(const std::string& src_name, const std::string& dst_name) {
    std::vector<Topology> candidates;
    Rect src = floorplan_.get_block_bounds(src_name);
    Rect dst = floorplan_.get_block_bounds(dst_name);
    add_l_shapes(src, dst, candidates);
    std::vector<int> hanan_x, hanan_y;
    floorplan_.get_hanan_grid(hanan_x, hanan_y);
    add_z_shapes(src, dst, hanan_x, hanan_y, candidates);
    add_u_shapes(src, dst, hanan_x, hanan_y, candidates);
    annotate_and_sort(candidates);
    return candidates;
}
}
