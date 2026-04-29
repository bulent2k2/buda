#pragma once
#include <vector>
#include <string>
#include <algorithm>
#include <map>
#include "bundler.h"
namespace interconnect {
struct Point { int x, y; };
struct Rect { 
    int x1, y1, x2, y2; 
    Point center() const { return { (x1+x2)/2, (y1+y2)/2 }; }
};
struct Segment {
    Point start, end;
    int layer_hint = 0; 
};
struct Topology {
    std::string type; 
    std::vector<Segment> segments;
    int estimated_wirelength = 0;
    int trunk_location = 0;
};
class Floorplan {
public:
    void add_block(const std::string& name, int x1, int y1, int x2, int y2);
    Rect get_block_bounds(const std::string& name) const;
    void get_hanan_grid(std::vector<int>& x_coords, std::vector<int>& y_coords) const;
    std::vector<std::pair<std::string, Rect>> get_all_blocks() const {
        std::vector<std::pair<std::string, Rect>> res;
        for(auto const& [key, val] : blocks_) res.push_back({key, val});
        return res;
    }
private:
    std::map<std::string, Rect> blocks_;
};
class TopologyGenerator {
public:
    TopologyGenerator(const Floorplan& fp) : floorplan_(fp) {}

    // 2-pin: L / Z / U shapes
    std::vector<Topology> generate_candidates(
        const std::string& src_name,
        const std::string& dst_name);

    // Multi-pin: trunk + branch shapes (1 driver, N receivers)
    std::vector<Topology> generate_multicast_candidates(
        const std::string& src_name,
        const std::vector<std::string>& dst_names);

private:
    const Floorplan& floorplan_;
    void add_l_shapes(const Rect& src, const Rect& dst, std::vector<Topology>& results);
    void add_z_shapes(const Rect& src, const Rect& dst, const std::vector<int>& x_grid, const std::vector<int>& y_grid, std::vector<Topology>& results);
    void add_u_shapes(const Rect& src, const Rect& dst, const std::vector<int>& x_grid, const std::vector<int>& y_grid, std::vector<Topology>& results);
    void add_trunk_h(const std::vector<Point>& pins, int y_trunk, bool out_of_bbox, std::vector<Topology>& results);
    void add_trunk_v(const std::vector<Point>& pins, int x_trunk, bool out_of_bbox, std::vector<Topology>& results);
};
}