#include "topology.h"
#include <cmath>
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