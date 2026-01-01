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
    // HV (Layer 3 then 4 assumed for prototype)
    Topology hv; hv.type = "L_HV";
    hv.segments.push_back(make_seg(s.x, s.y, d.x, s.y, 3)); 
    hv.segments.push_back(make_seg(d.x, s.y, d.x, d.y, 4));
    results.push_back(hv);
    // VH
    Topology vh; vh.type = "L_VH";
    vh.segments.push_back(make_seg(s.x, s.y, s.x, d.y, 4));
    vh.segments.push_back(make_seg(s.x, d.y, d.x, d.y, 3));
    results.push_back(vh);
}
void TopologyGenerator::add_z_shapes(const Rect& src, const Rect& dst, const std::vector<int>& x_grid, const std::vector<int>& y_grid, std::vector<Topology>& results) {
    Point s = src.center(); Point d = dst.center();
    int min_x = std::min(s.x, d.x), max_x = std::max(s.x, d.x);
    for (int x_cut : x_grid) {
        if (x_cut > min_x && x_cut < max_x) {
            Topology z; z.type = "Z_HVH"; z.trunk_location = x_cut;
            z.segments.push_back(make_seg(s.x, s.y, x_cut, s.y, 3));
            z.segments.push_back(make_seg(x_cut, s.y, x_cut, d.y, 4));
            z.segments.push_back(make_seg(x_cut, d.y, d.x, d.y, 3));
            results.push_back(z);
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
    return candidates;
}
}