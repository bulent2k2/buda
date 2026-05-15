#pragma once
#include <vector>
#include <string>
#include <algorithm>
#include <map>
#include <optional>
#include "bundler.h"
namespace interconnect {
struct Point { int x, y; };
struct Rect {
    int x1, y1, x2, y2;
    Point center() const { return { (x1+x2)/2, (y1+y2)/2 }; }
    // Nearest block face in the direction of 'toward'. Returns 'toward' itself
    // if the value falls inside the block (= trunk passes through the block).
    int face_x(int toward) const { return toward > x2 ? x2 : toward < x1 ? x1 : toward; }
    int face_y(int toward) const { return toward > y2 ? y2 : toward < y1 ? y1 : toward; }
    // Return a rect inset by (dx, dy) on each side.
    // Guard: if the margin would invert an axis, keep that axis at full extent.
    Rect shrink(int dx, int dy) const {
        int nx1 = x1+dx, nx2 = x2-dx, ny1 = y1+dy, ny2 = y2-dy;
        if (nx1 > nx2) { nx1 = x1; nx2 = x2; }
        if (ny1 > ny2) { ny1 = y1; ny2 = y2; }
        return Rect{nx1, ny1, nx2, ny2};
    }
};
struct Segment {
    Point start, end;
    int layer_hint = 0;
};
// A busterm is a connection point on a block face.  Currently represented by
// the block name and its bounding box; can be refined to a pin location later.
struct Busterm {
    std::string block_name;
    Rect        bbox;       // possibly margin-inset
    Rect        orig_bbox;  // always the full physical extent
};
// Per-segment busterm annotation: .first = busterm at segment start endpoint,
// .second = busterm at segment end endpoint.  nullopt means the endpoint is an
// internal junction (connects to another segment, not a block face).
using SegEndpoints = std::pair<std::optional<Busterm>, std::optional<Busterm>>;
struct Topology {
    std::string type;
    std::vector<Segment> segments;
    int estimated_wirelength  = 0;
    int trunk_location        = 0;
    int pass_through_count    = 0;  // blocks whose bbox contains the trunk (no stub generated)
    // Populated by generate_candidates / generate_multicast_candidates so that
    // ConnTopology::infer_connections can identify busterm connections without
    // scanning the entire floorplan.  Key = segment index.
    std::map<int, SegEndpoints> seg_busterms;
};
// Per-block corner margin: keeps trunk/stub connections away from block corners.
// dx: margin along the horizontal direction (applied to top/bottom faces, extent in X).
// dy: margin along the vertical direction   (applied to left/right  faces, extent in Y).
// A margin of 0 means no constraint beyond the face extent (default).
struct BlockCornerMargin {
    int dx = 0;
    int dy = 0;
};

class Floorplan {
public:
    void add_block(const std::string& name, int x1, int y1, int x2, int y2);
    // Set corner margin for a previously-added block (absolute units).
    void set_block_corner_margin(const std::string& name, int dx, int dy);
    // Set global corner margin applied to all blocks that have no per-block override.
    void set_global_corner_margin(int dx, int dy);
    Rect get_block_bounds(const std::string& name) const;
    // Returns per-block margin if set, else global margin, else {0,0}.
    BlockCornerMargin get_block_corner_margin(const std::string& name) const;
    void get_hanan_grid(std::vector<int>& x_coords, std::vector<int>& y_coords) const;
    std::vector<std::pair<std::string, Rect>> get_all_blocks() const {
        std::vector<std::pair<std::string, Rect>> res;
        for(auto const& [key, val] : blocks_) res.push_back({key, val});
        return res;
    }
private:
    std::map<std::string, Rect> blocks_;
    std::map<std::string, BlockCornerMargin> corner_margins_;
    BlockCornerMargin global_corner_margin_{};
};
class TopologyGenerator {
public:
    explicit TopologyGenerator(const Floorplan& fp) : floorplan_(fp) {}

    // Busterm mode (default true): route segments terminate at the nearest
    // block face rather than at the block centre.  Set to false to restore
    // the original centre-to-centre behaviour.
    void set_busterm_mode(bool v) { use_busterm_ = v; }

    // Override the default H and V layer IDs used for segment layer_hint.
    // Defaults: h=4 (M4), v=5 (M5).  Call this after determining which layers
    // are actually defined in the layer stack so no undefined layer is emitted.
    void set_layer_ids(int h, int v) { h_layer_ = h; v_layer_ = v; }

    // Double-detour mode (default false): add UU_VHV / UU_HVH topologies where
    // the src stub is an L-shape that exits a SIDE face of the src block rather
    // than the primary face used by the standard U shape.  Useful when the normal
    // U route is congested.  Only meaningful in busterm mode.
    void set_double_detour(bool v) { allow_double_detour_ = v; }

    // 2-pin: L / Z / U shapes
    std::vector<Topology> generate_candidates(
        const std::string& src_name,
        const std::string& dst_name);

    // Multi-pin: trunk + branch shapes (1 driver, N receivers)
    std::vector<Topology> generate_multicast_candidates(
        const std::string& src_name,
        const std::vector<std::string>& dst_names);

private:
    void filter_pinched(std::vector<Topology>& candidates);
    const Floorplan& floorplan_;
    bool use_busterm_         = true;
    bool allow_double_detour_ = false;
    int  h_layer_             = 4;
    int  v_layer_             = 5;

    void add_l_shapes(const Busterm& src, const Busterm& dst, std::vector<Topology>& results);
    void add_z_shapes(const Busterm& src, const Busterm& dst, const std::vector<int>& x_grid, const std::vector<int>& y_grid, std::vector<Topology>& results);
    void add_u_shapes(const Busterm& src, const Busterm& dst, const std::vector<int>& x_grid, const std::vector<int>& y_grid, std::vector<Topology>& results);
    void add_uu_shapes(const Busterm& src, const Busterm& dst, const std::vector<int>& x_grid, const std::vector<int>& y_grid, std::vector<Topology>& results);
    void add_trunk_h(const std::vector<Point>& pins, const std::vector<Busterm>& blocks,
                     int y_trunk, bool out_of_bbox, std::vector<Topology>& results);
    void add_trunk_v(const std::vector<Point>& pins, const std::vector<Busterm>& blocks,
                     int x_trunk, bool out_of_bbox, std::vector<Topology>& results);
    void add_mst_candidates(const std::vector<Busterm>& blocks, std::vector<Topology>& results);
    void add_multi_trunk_candidates(const std::vector<Point>& pins, const std::vector<Busterm>& blocks, std::vector<Topology>& results);
};
}
