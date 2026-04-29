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
void TopologyGenerator::add_l_shapes(const Rect& src, const Rect& dst, std::vector<Topology>& results) {
    Point s = src.center(); Point d = dst.center();
    Topology hv; hv.type = "L_HV";
    hv.segments.push_back(make_seg(s.x, s.y, d.x, s.y, 4));  // M4 horizontal
    hv.segments.push_back(make_seg(d.x, s.y, d.x, d.y, 5));  // M5 vertical
    results.push_back(hv);
    Topology vh; vh.type = "L_VH";
    vh.segments.push_back(make_seg(s.x, s.y, s.x, d.y, 5));  // M5 vertical
    vh.segments.push_back(make_seg(s.x, d.y, d.x, d.y, 4));  // M4 horizontal
    results.push_back(vh);
}
void TopologyGenerator::add_z_shapes(const Rect& src, const Rect& dst, const std::vector<int>& x_grid, const std::vector<int>& y_grid, std::vector<Topology>& results) {
    Point s = src.center(); Point d = dst.center();
    int min_x = std::min(s.x, d.x), max_x = std::max(s.x, d.x);
    for (int x_cut : x_grid) {
        if (x_cut > min_x && x_cut < max_x) {
            Topology z; z.type = "Z_HVH";
            z.segments.push_back(make_seg(s.x, s.y, x_cut, s.y, 4));  // M4 horizontal stub
            z.segments.push_back(make_seg(x_cut, s.y, x_cut, d.y, 5));// M5 vertical trunk
            z.segments.push_back(make_seg(x_cut, d.y, d.x, d.y, 4));  // M4 horizontal stub
            results.push_back(z);
        }
    }
}
// NEW: U-Shape logic (detours outside bounding box)
void TopologyGenerator::add_u_shapes(const Rect& src, const Rect& dst, const std::vector<int>& x_grid, const std::vector<int>& y_grid, std::vector<Topology>& results) {
    Point s = src.center(); Point d = dst.center();
    int min_x = std::min(s.x, d.x), max_x = std::max(s.x, d.x);
    int min_y = std::min(s.y, d.y), max_y = std::max(s.y, d.y);

    // Vertical U-trunks (detour left/right of bounding box)
    for (int x_cut : x_grid) {
        if (x_cut < min_x || x_cut > max_x) {
            Topology u; u.type = "U_HVH";
            u.segments.push_back(make_seg(s.x, s.y, x_cut, s.y, 4));  // M4 horizontal stub
            u.segments.push_back(make_seg(x_cut, s.y, x_cut, d.y, 5));// M5 vertical trunk
            u.segments.push_back(make_seg(x_cut, d.y, d.x, d.y, 4));  // M4 horizontal stub
            results.push_back(u);
        }
    }
    // Horizontal U-trunks (detour above/below bounding box) — use M6 for the trunk
    for (int y_cut : y_grid) {
        if (y_cut < min_y || y_cut > max_y) {
            Topology u; u.type = "U_VHV";
            u.segments.push_back(make_seg(s.x, s.y, s.x, y_cut, 5));  // M5 vertical stub
            u.segments.push_back(make_seg(s.x, y_cut, d.x, y_cut, 6));// M6 horizontal trunk (long-haul)
            u.segments.push_back(make_seg(d.x, y_cut, d.x, d.y, 5));  // M5 vertical stub
            results.push_back(u);
        }
    }
}
// ---------------------------------------------------------------------------
// Multicast helpers
// ---------------------------------------------------------------------------

// H-trunk at y_trunk: one horizontal spine + vertical stubs from each pin.
// out_of_bbox=true → spine uses M6 (long-haul detour layer).
void TopologyGenerator::add_trunk_h(const std::vector<Point>& pins, int y_trunk,
                                     bool out_of_bbox, std::vector<Topology>& results)
{
    int x_lo = INT_MAX, x_hi = INT_MIN;
    for (const auto& p : pins) { x_lo = std::min(x_lo, p.x); x_hi = std::max(x_hi, p.x); }

    Topology t;
    t.type         = out_of_bbox ? "TRUNK_H_OOB" : "TRUNK_H";
    t.trunk_location = y_trunk;
    int spine_layer = out_of_bbox ? 6 : 4;

    if (x_lo < x_hi)
        t.segments.push_back(make_seg(x_lo, y_trunk, x_hi, y_trunk, spine_layer));

    for (const auto& p : pins)
        if (p.y != y_trunk)
            t.segments.push_back(make_seg(p.x, p.y, p.x, y_trunk, 5)); // M5 stub

    if (!t.segments.empty()) results.push_back(std::move(t));
}

// V-trunk at x_trunk: one vertical spine + horizontal stubs from each pin.
void TopologyGenerator::add_trunk_v(const std::vector<Point>& pins, int x_trunk,
                                     bool out_of_bbox, std::vector<Topology>& results)
{
    int y_lo = INT_MAX, y_hi = INT_MIN;
    for (const auto& p : pins) { y_lo = std::min(y_lo, p.y); y_hi = std::max(y_hi, p.y); }

    Topology t;
    t.type         = out_of_bbox ? "TRUNK_V_OOB" : "TRUNK_V";
    t.trunk_location = x_trunk;

    if (y_lo < y_hi)
        t.segments.push_back(make_seg(x_trunk, y_lo, x_trunk, y_hi, 5)); // M5 spine

    for (const auto& p : pins)
        if (p.x != x_trunk)
            t.segments.push_back(make_seg(p.x, p.y, x_trunk, p.y, 4)); // M4 stub

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

    // Collect all pin centres.
    std::vector<Point> pins;
    pins.push_back(floorplan_.get_block_bounds(src_name).center());
    for (const auto& d : dst_names)
        pins.push_back(floorplan_.get_block_bounds(d).center());

    // Bounding box of all pins.
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
    if (all_same_x || all_same_y) return results; // degenerate — no need for trunk variants

    // Candidate trunk positions: unique pin-centre coords + Hanan grid lines.
    std::vector<int> hanan_x, hanan_y;
    floorplan_.get_hanan_grid(hanan_x, hanan_y);

    std::set<int> y_set(hanan_y.begin(), hanan_y.end());
    for (const auto& p : pins) y_set.insert(p.y); // pin centres as additional candidates

    std::set<int> x_set(hanan_x.begin(), hanan_x.end());
    for (const auto& p : pins) x_set.insert(p.x);

    // H-trunk candidates (spine inside or outside pin bbox).
    for (int y_t : y_set)
        add_trunk_h(pins, y_t, (y_t < y_lo || y_t > y_hi), results);

    // V-trunk candidates.
    for (int x_t : x_set)
        add_trunk_v(pins, x_t, (x_t < x_lo || x_t > x_hi), results);

    return results;
}

// ---------------------------------------------------------------------------
// 2-pin candidates (unchanged)
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
    return candidates;
}
}