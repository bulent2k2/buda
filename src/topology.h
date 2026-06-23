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

#pragma once
#include <vector>
#include <string>
#include <algorithm>
#include <map>
#include <set>
#include <optional>
#include "bundler.h"
namespace buda {

// How a multi-rect block handles a trunk that falls in the gap between its rects.
enum class TegMode {
    THRU,  // default: connect only the nearest rect; block's internal routing joins sides
    OVER,  // connect both rects with V stubs + an explicit bridge segment over the block top
};

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
    // True for a dogleg JOG: the short perpendicular segment NUTS inserts to
    // bridge two collinear trunk pieces on different tracks.  Marked so build_
    // nuts_maps excludes it from same-bundle alignment (it must stay on its own
    // column, not snap onto a sibling stub's track).
    bool is_jog = false;
};
// A busterm is a connection point on a block face.  Currently represented by
// the block name and its bounding box; can be refined to a pin location later.
struct Busterm {
    std::string       block_name;
    Rect              bbox;       // possibly margin-inset (union of all rects)
    Rect              orig_bbox;  // always the full physical extent (union)
    // Non-empty for multi-rect blocks: each element is one candidate connection
    // rectangle (unshrunk).  Empty = single-rect block (use orig_bbox as before).
    std::vector<Rect> rects;
    TegMode           teg_mode = TegMode::THRU;
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
    // Populated by generate_candidates so that
    // ConnTopology::infer_connections can identify busterm connections without
    // scanning the entire floorplan.  Key = segment index.
    std::map<int, SegEndpoints> seg_busterms;
    // Over-the-block bridge segments: block_name → bridge Segment.
    // Non-empty only when teg_mode=OVER and trunk falls in the gap between rects.
    std::map<std::string, Segment> bridge_segments;
    // All block names this topology must connect (src + all dsts).
    // Set by generate_candidates; used by connectivity verifier to detect
    // pass-through blocks that have no explicit BUSTERM endpoint connection.
    std::vector<std::string> connected_block_names;
};
// Per-block corner margin: keeps trunk/stub connections away from block corners.
// dx: margin along the horizontal direction (applied to top/bottom faces, extent in X).
// dy: margin along the vertical direction   (applied to left/right  faces, extent in Y).
// A margin of 0 means no constraint beyond the face extent (default).
struct BlockCornerMargin {
    int dx = 0;
    int dy = 0;
};

struct MinStubLength {
    int global = 20;
    std::map<int, int> per_dir;   // 0=HORIZONTAL, 1=VERTICAL
    std::map<int, int> per_layer;
};

// Opt-in feedthru: a block declared routable-through on a given trunk layer is not
// stubbed to and is allowed to be crossed (the block's own router bridges the gap).
// Feedthru is genuinely per-(block, layer): unlike MinStubLength's independent
// per_block/per_layer fallback axes, a true grid is needed so a block can be
// feedthru on one layer but not another.  Rules are resolved most-specific-first:
//   (block, layer) > (block, *) > (*, layer) > (*, *)
// i.e. a block-scoped rule beats a layer-scoped rule.  Each rule stores an explicit
// bool, so a more-specific `off` overrides a broader `on` and vice-versa.
struct FeedthruConfig {
    std::map<std::pair<std::string, int>, bool> per_block_layer;  // (block, layer)
    std::map<std::string, bool>                 per_block;         // (block, *)
    std::map<int, bool>                         per_layer;         // (*, layer)
    bool                                        global = false;    // (*, *)
};

// Per-direction outer margin for U-shape (and UU-shape) detour trunks.
// -1 means "auto" (topology generator uses its internal heuristic).
struct DetourChannelSpec {
    int north = -1;  // margin above the bundle bounding box (larger Y)
    int south = -1;  // margin below the bundle bounding box (smaller Y)
    int east  = -1;  // margin right of the bundle bounding box (larger X)
    int west  = -1;  // margin left  of the bundle bounding box (smaller X)
};

struct KeepoutZone {
    Rect bbox;
    std::set<int> layer_ids;
};

class Floorplan {
public:
    void add_block(const std::string& name, int x1, int y1, int x2, int y2);
    void add_keepout_zone(int x1, int y1, int x2, int y2, const std::vector<int>& layer_ids);
    const std::vector<KeepoutZone>& get_keepout_zones() const { return keepouts_; }
    // Mark a block as a hierarchy container (an envelope enclosing finer blocks)
    // rather than a solid leaf cell.  Containers are transparent to LOW layers:
    // their internal channels carry routing congestion (charged via the child
    // blocks' Hanan cuts), whereas leaf cells block LOW layers entirely.
    void set_container(const std::string& name, bool is_container = true);
    bool is_container(const std::string& name) const;
    // Keepouts seen by LOW (non-TOP) layers: the user-defined zones plus an
    // implicit zone for every solid leaf cell (each cell rect), tagged with the
    // given non-TOP layer ids.  A LOW segment cannot route over a leaf cell —
    // the cell behaves as a keepout for the whole lower stack — while TOP layers
    // (absent from low_layer_ids) cross cells freely.  Shared by the planner's
    // band-capacity model and abstract/detailed NUTS so all three agree.
    std::vector<KeepoutZone> low_layer_keepouts(const std::vector<int>& low_layer_ids) const;
    // Multi-rect block: stores each rect individually; add_block is called
    // internally with the union bounding box for backward compatibility.
    void add_block_rects(const std::string& name, const std::vector<Rect>& rects,
                         TegMode mode = TegMode::THRU);
    // Returns the individual rects for a multi-rect block, or empty for single-rect.
    std::vector<Rect> get_block_rects(const std::string& name) const;
    // TEG mode: controls over-the-block bridge generation for multi-rect blocks.
    void    set_block_teg_mode(const std::string& name, TegMode mode);
    TegMode get_block_teg_mode(const std::string& name) const;
    // Set corner margin for a previously-added block (absolute units).
    void set_block_corner_margin(const std::string& name, int dx, int dy);
    // Set global corner margin applied to all blocks that have no per-block override.
    void set_global_corner_margin(int dx, int dy);

    // Minimum stub length configuration (global, per-direction, per-layer).
    void set_min_stub_length(int val) { min_stub_len_.global = val; }
    void set_min_stub_length_dir(int dir, int val) { min_stub_len_.per_dir[dir] = val; }
    void set_min_stub_length_layer(int layer_id, int val) { min_stub_len_.per_layer[layer_id] = val; }

    int get_min_stub_length(int dir, int layer_id) const {
        if (min_stub_len_.per_layer.count(layer_id)) return min_stub_len_.per_layer.at(layer_id);
        if (min_stub_len_.per_dir.count(dir)) return min_stub_len_.per_dir.at(dir);
        return min_stub_len_.global;
    }

    // Feedthru config (global / per-block / per-layer / per-(block,layer)).
    void set_feedthru(bool v)                                  { feedthru_.global = v; }
    void set_feedthru_block(const std::string& name, bool v)   { feedthru_.per_block[name] = v; }
    void set_feedthru_layer(int layer_id, bool v)              { feedthru_.per_layer[layer_id] = v; }
    void set_feedthru_block_layer(const std::string& name, int layer_id, bool v) {
        feedthru_.per_block_layer[{name, layer_id}] = v;
    }
    // Resolve feedthru for a block on a trunk layer, most-specific-first.
    bool get_feedthru(const std::string& name, int layer_id) const {
        auto it = feedthru_.per_block_layer.find({name, layer_id});
        if (it != feedthru_.per_block_layer.end()) return it->second;
        if (feedthru_.per_block.count(name))     return feedthru_.per_block.at(name);
        if (feedthru_.per_layer.count(layer_id)) return feedthru_.per_layer.at(layer_id);
        return feedthru_.global;
    }

    // Detour channel outer margin.
    // dirs is any combination of N/S/E/W, or the shorthands Y (N+S), X (E+W), A (all).
    // size < 0 resets the specified directions back to auto.
    void set_detour_channel(const std::string& dirs, int size);
    const DetourChannelSpec& get_detour_channel() const { return detour_channel_; }

    Rect get_block_bounds(const std::string& name) const;
    // True iff a block with this exact name has been registered (get_block_bounds
    // silently returns a degenerate {0,0,0,0} for unknown names, so callers that
    // need to validate an endpoint must use this instead).
    bool has_block(const std::string& name) const;
    // Returns per-block margin if set, else global margin, else {0,0}.
    BlockCornerMargin get_block_corner_margin(const std::string& name) const;
    void get_hanan_grid(std::vector<int>& x_coords, std::vector<int>& y_coords) const;
    std::vector<std::pair<std::string, Rect>> get_all_blocks() const {
        std::vector<std::pair<std::string, Rect>> res;
        for(auto const& [key, val] : blocks_) res.push_back({key, val});
        return res;
    }
private:
    std::map<std::string, Rect>          blocks_;
    std::map<std::string, std::vector<Rect>> block_rects_;  // only for multi-rect blocks
    std::set<std::string>                containers_;       // hierarchy envelopes (not leaf cells)
    std::map<std::string, TegMode>       teg_modes_;
    std::map<std::string, BlockCornerMargin> corner_margins_;
    BlockCornerMargin global_corner_margin_{};
    MinStubLength min_stub_len_;
    FeedthruConfig feedthru_;
    DetourChannelSpec detour_channel_;
    std::vector<KeepoutZone> keepouts_;
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
    void set_layer_ids(int h, int v) {
        h_layer_ = h; v_layer_ = v;
        all_h_layers_ = {h}; all_v_layers_ = {v};
    }

    // Inform the generator of ALL available H and V layer IDs so keepout
    // avoidance can distinguish "keepout on one H layer" (planner re-assigns)
    // from "keepout on ALL H layers" (trunk position must be skipped).
    // Must be called after set_layer_ids if multiple H or V layers exist.
    void set_all_h_layers(const std::vector<int>& layers) { all_h_layers_ = layers; }
    void set_all_v_layers(const std::vector<int>& layers) { all_v_layers_ = layers; }

    // Double-detour mode (default false): add UU_VHV / UU_HVH topologies where
    // the src stub is an L-shape that exits a SIDE face of the src block rather
    // than the primary face used by the standard U shape.  Useful when the normal
    // U route is congested.  Only meaningful in busterm mode.
    void set_double_detour(bool v) { allow_double_detour_ = v; }

    // Unified entry point: 1 dst → L/Z/U shapes; N dsts → trunk+branch shapes.
    std::vector<Topology> generate_candidates(
        const std::string& src_name,
        const std::vector<std::string>& dst_names);

private:
    // Internal dispatch targets.
    std::vector<Topology> generate_2pin(
        const std::string& src_name,
        const std::string& dst_name);
    std::vector<Topology> generate_npin(
        const std::string& src_name,
        const std::vector<std::string>& dst_names);
    void filter_pinched(std::vector<Topology>& candidates);
    const Floorplan& floorplan_;
    bool use_busterm_         = true;
    bool allow_double_detour_ = false;
    int  h_layer_             = 4;
    int  v_layer_             = 5;
    std::vector<int> all_h_layers_ = {4};
    std::vector<int> all_v_layers_ = {5};

    void add_l_shapes(const Busterm& src, const Busterm& dst, std::vector<Topology>& results);
    void add_z_shapes(const Busterm& src, const Busterm& dst, const std::vector<int>& x_grid, const std::vector<int>& y_grid, std::vector<Topology>& results);
    void add_u_shapes(const Busterm& src, const Busterm& dst, const std::vector<int>& x_grid, const std::vector<int>& y_grid, std::vector<Topology>& results);
    void add_uu_shapes(const Busterm& src, const Busterm& dst, const std::vector<int>& x_grid, const std::vector<int>& y_grid, std::vector<Topology>& results);
    void add_trunk_h(const std::vector<Point>& pins, const std::vector<Busterm>& blocks,
                     int y_trunk, bool out_of_bbox, std::vector<Topology>& results);
    void add_trunk_v(const std::vector<Point>& pins, const std::vector<Busterm>& blocks,
                     int x_trunk, bool out_of_bbox, std::vector<Topology>& results);
    void add_mst_candidates(const std::vector<Busterm>& blocks, std::vector<Topology>& results);
    void add_trunk_mst_candidates(const std::vector<Busterm>& blocks, std::vector<Topology>& results);
    void add_multi_trunk_candidates(const std::vector<Point>& pins, const std::vector<Busterm>& blocks, std::vector<Topology>& results);
    // Keepout helpers (used by generate_2pin and generate_npin)
    bool segment_blocked_on_all_layers(const Segment& seg) const;
};
}
