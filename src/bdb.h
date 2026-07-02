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
// bdb.h — Buda Physical Design Database
// SQLite-backed store for components, nets, pins, busterms, bundles, and groups.
// All other v3 modules access physical design data exclusively through BDB.

#include <string>
#include <vector>
#include <optional>
#include <unordered_map>
#include "sqlite3.h"

namespace buda {

// ── Row types returned to Python / other modules ──────────────────────────

struct ComponentRow {
    int         id;
    std::string name;
    std::string cell;
    int         parent_id;   // -1 if root
    int         depth;
    double      x1, y1, x2, y2;
    bool        is_leaf;      // true = STDCELL, false = hierarchical block
    bool        is_replicated;
};

struct NetRow {
    int         id;
    std::string name;
};

struct PinRow {
    int         net_id;
    int         comp_id;
    std::string pin_name;
    std::string dir;          // INPUT | OUTPUT | INOUT | UNKNOWN
    double      px, py;       // absolute pin position in µm (-1 if unknown)
};

struct NetPropsRow {
    int         net_id;
    double      hpwl;
    int         fanout;
    std::string driver_comp;
    std::string bus_name;
    int         bit_index;
    int         bundle_id;
};

struct BustermRow {
    std::string id;
    int         comp_id;
    std::string hier_path;
    int         depth;
    double      x1, y1, x2, y2;
    std::string resolution;   // BLOCK | SPATIAL_CLUSTER | PORT
    std::string parent_id;
    // Optional multi-rect geometry (e.g. TEG blocks).
    // JSON-encoded: [[x1,y1,x2,y2],...].  Empty string = single-rect (x1..y2 only).
    std::string rects;
    // Routing-time busterm attributes (topology.h Busterm).  teg_mode is the
    // TEG-gap handling ('THRU'|'OVER'); orig_* is the full physical extent while
    // x1..y2 hold the (possibly margin-inset) tap bbox.  Defaults keep hier-derived
    // rows (which don't set these) well-formed.
    std::string teg_mode = "THRU";
    double       orig_x1 = 0, orig_y1 = 0, orig_x2 = 0, orig_y2 = 0;
};

// One persisted seg_busterms endpoint→busterm link (topology_seg_busterm row).
struct TopoSegBustermRow {
    std::string bundle_id;
    int         cand_index = 0;
    int         seg_index  = 0;
    std::string endpoint;    // 'start' | 'end'
    std::string busterm_id;
};

struct BundleRow {
    std::string id;
    int         level = 0;             // hierarchy level (0 = top / flat)
    std::string strategy;             // STRICT | CONVERGENT | BIDIRECTIONAL
    std::string reason;               // grouping signature
    int         num_terminals = 0;
    std::string cell_context;         // "" for top-level; cell type otherwise
    std::string instances;            // JSON array of instance paths
    std::string parent_id;            // "" for top-level
    bool        is_replicated = false;
    int         drv_spec_depth = -1;  // cross-level driver depth (-1 = same-level)
    int         rcv_spec_depth = -1;
    std::string drv_spec_path;
    std::string rcv_spec_paths;       // JSON array
};

struct GrpRow {
    std::string id;
    std::string name;
    std::string color;
    std::string parent_id;
};

// One candidate topology of a bundle (Stage-2 output). Keyed by (bundle_id,
// cand_index); segments live in topology_segment.
struct TopoRow {
    std::string id;                 // bundle_id (FK to bundle)
    int         cand_index = 0;     // index in the bundle's candidate list
    std::string type;               // "L_HV", "Z_trunk_x", "U_top", …
    int         wirelength = 0;     // estimated wirelength
    int         trunk_location = 0;
    int         pass_through_count = 0;
    std::string connected_blocks;   // JSON array of block names
    std::string feedthru_blocks;    // JSON array of opt-in feedthru blocks
    bool        is_selected = false;// pinned/selected candidate (post-plan; pre-plan pin)
};

// One segment of a candidate topology.
struct TopoSegRow {
    std::string id;                 // bundle_id (FK)
    int         cand_index = 0;
    int         seg_index = 0;
    int         x1 = 0, y1 = 0, x2 = 0, y2 = 0;
    int         layer_hint = 0;     // generation-time hint
    bool        is_jog = false;
    int         assigned_layer = -1;// planner's per-segment layer (-1 = unassigned)
};

// One placed abstract-NUTS bus segment (Stage-4 output). `id` is a *soft* link to
// a bundle: the flat flow uses the original bundle id, but the hier flow's
// run_planner expands bundles into per-instance wrappers with synthetic ids, so
// there is no hard FK. Geometry is the placed rectangle (real coords, µm).
struct BusSegRow {
    std::string id;                 // bundle id (FK -> bundle.id)
    int         seg_idx = 0;
    int         layer = 0;
    bool        is_horiz = false;
    double      x1 = 0, y1 = 0, x2 = 0, y2 = 0;
    double      track_position = 0;
    double      width = 0;
    bool        placed = false;
    bool        is_jog = false;
};

// One symbolic bus-via: a bus-level layer transition between two connected
// segments (bit_width bit-vias represented as one row).
struct BusViaRow {
    std::string id;                 // bundle id (FK -> bundle.id)
    int         from_seg = 0, to_seg = 0;
    int         from_layer = 0, to_layer = 0;
    double      x = 0, y = 0;       // junction position (µm)
    int         bit_width = 0;
};

// One detailed-NUTS bit-wire (NetSegment) as persisted: placed rectangle plus
// the bit's net identity. net_name is the WRITE input (resolved to net_id via
// _ensure_net, auto-creating a name-only net row); reads LEFT JOIN net to fill
// both fields (net_id = -1 / net_name = "" when unresolved).
struct NetSegRow {
    std::string id;                 // bundle id (FK -> bundle.id)
    int         seg_idx = 0;
    int         bit_index = 0;      // LOGICAL bit (bit_order already applied)
    int         net_id = -1;
    std::string net_name;
    int         layer = 0;
    bool        is_horiz = false;
    double      x1 = 0, y1 = 0, x2 = 0, y2 = 0;
    double      track_position = 0;
    double      width = 0;
};

// One per-bit via: the symbolic bus_via row fanned out per bit. Shares the
// (bundle_id, from_seg, to_seg) key with its parent bus_via; bit_index + the
// bit's net identity complete the row. from_seg < to_seg always.
struct NetViaRow {
    std::string id;                 // bundle id (FK -> bundle.id)
    int         from_seg = 0, to_seg = 0;
    int         bit_index = 0;
    int         net_id = -1;
    std::string net_name;
    int         from_layer = 0, to_layer = 0;
    double      x = 0, y = 0;       // per-bit crossing (µm)
};

// Singleton fingerprint of the routed output (bus_segment + bus_via, plus
// net_segment + net_via once detailed NUTS persists). One row (id=1); its
// content hash turns a routing change into one reviewable *.bdb.sql diff line
// and is the natural feed for the planned BDB -> OA/GDS export.
struct RouteSnapshotRow {
    std::string hash;
    int         n_bus_segments = 0;
    int         n_bus_vias = 0;
    std::string stage;              // "abstract_nuts" or "detailed_nuts"
    int         n_net_segments = 0;
    int         n_net_vias = 0;
};

struct CellRow {
    std::string name;
    double      width, height;
};

struct CellPinRow {
    std::string cell;
    std::string pin_name;
    std::string dir;        // INPUT | OUTPUT | INOUT
    double      px, py;     // offset from cell origin (-1 = unset)
};

// ── BDB ───────────────────────────────────────────────────────────────────

class BDB {
public:
    // Current BDB schema version, stamped into PRAGMA user_version. Bump this
    // and add a step to _migrate() when the schema changes; opening an older DB
    // then migrates it forward. v1 = versioned schema + provenance meta;
    // v2 = bundle-persistence tables (bundle / bundle_net / bundle_busterm);
    // v3 = bundle_net re-keyed by net_id (was net_name);
    // v4 = candidate-topology tables (topology / topology_segment);
    // v5 = abstract-NUTS bus routing tables (bus_segment / bus_via);
    // v6 = topology_segment.assigned_layer (planner's per-segment layer);
    // v7 = bus_segment/bus_via FK to bundle + route_snapshot fingerprint table;
    // v8 = detailed-NUTS net_segment/net_via tables + route_snapshot n_net_* counts;
    // v9 = routing-time busterm attrs (teg_mode/orig_*) + topology_seg_busterm join.
    static constexpr int SCHEMA_VERSION = 9;

    explicit BDB(const std::string& db_path);
    ~BDB();

    // ── Schema version & metadata ──────────────────────────────────────────
    // The schema version stored in this DB (PRAGMA user_version). Equals
    // SCHEMA_VERSION after open, since the constructor migrates forward.
    int schema_version() const;
    // Read a meta(key,value) row, or `def` if absent. Provenance keys include
    // 'schema_version' and 'bdb_tool'.
    std::string meta_get(const std::string& key,
                         const std::string& def = "") const;

    // ── Ingestion ──────────────────────────────────────────────────────────
    void import_def_lef(const std::string& def_path, const std::string& lef_path);
    void import_verilog(const std::string& v_path);

    // ── Cell definitions ───────────────────────────────────────────────────
    void add_cell(const std::string& name, double w, double h);
    std::vector<CellRow> all_cells() const;

    // ── Cell-level pins (port interface) ──────────────────────────────────
    // Define or update a port on a cell type.  px/py are offsets from the
    // cell's lower-left origin (-1 means position unset / use centroid).
    void add_cell_pin(const std::string& cell, const std::string& pin_name,
                      const std::string& dir = "INOUT",
                      double px = -1.0, double py = -1.0);
    std::vector<CellPinRow> all_cell_pins() const;
    // Resolve UNKNOWN instance pin directions from cell_pin declarations.
    // Returns the number of pin rows updated.
    int infer_pin_dirs_from_cell_pins();

    // Add a net and derive instance-level pins from "inst/path.pin_name"
    // endpoint notation.  Also inserts interface pins at each ancestor
    // component strictly between the leaf and the common ancestor of all
    // endpoints (hierarchy propagation).  Returns the net id.
    int add_net_pins(const std::string& net_name,
                     const std::string& drv,
                     const std::vector<std::string>& rcvs);

    // Like add_net_pins but stores every pin with dir="UNKNOWN".
    // Use when direction is not known at script time (e.g. undirected nets
    // declared with 'add_net … unknown', or after import_verilog).
    // HierarchicalBundler will use positional ordering as a fallback.
    int add_net_pins_undirected(const std::string& net_name,
                                const std::vector<std::string>& pins);

    // Like add_net_pins but stores every pin with dir="INOUT".
    // Use for explicitly bidirectional nets declared with 'add_net … inout'.
    // HierarchicalBundler treats INOUT as a secondary driver (priority below
    // OUTPUT); the first INOUT pin becomes the driver when no OUTPUT exists,
    // and remaining INOUT pins become receivers.
    int add_net_pins_inout(const std::string& net_name,
                           const std::vector<std::string>& pins);

    // ── Mutations ──────────────────────────────────────────────────────────
    // Move a single instance to new origin (x,y); size is preserved.
    void move_comp(const std::string& name, double x, double y);
    // Set a single instance bounding box exactly.
    void set_comp_is_leaf(const std::string& name, bool is_leaf);
    void set_comp_bbox(const std::string& name,
                       double x1, double y1, double x2, double y2);
    // Update the cell definition and every instance's x2/y2 to x1+w, y1+h.
    void resize_cell(const std::string& cell, double w, double h);
    void set_comp_cell(const std::string& comp_name, const std::string& new_cell);
    // Insert a new component row using explicit absolute coordinates.
    // parent_name="" for a root instance.  Throws if name already exists.
    int  add_comp(const std::string& name, const std::string& cell,
                  const std::string& parent_name,
                  double x1, double y1, double x2, double y2,
                  bool is_leaf = true);
    // Place a named instance of a defined cell at (x,y) relative to the
    // parent's origin (absolute when parent_name="").  Cell size comes from
    // the cell table; parent is automatically marked non-leaf.
    // If cell_children rows exist for cell_name they are eagerly expanded:
    // all descendant component rows are created recursively.
    // Returns the new component row id.  Throws if cell or parent not found.
    int  add_inst(const std::string& inst_name, const std::string& cell_name,
                  const std::string& parent_name, double x, double y);

    // Define the structural contents of a cell: "inside parent_cell, there is
    // an instance named inst_name of child_cell at relative position (x,y)."
    // Does not create component rows; expansion happens when add_inst places
    // an occurrence of parent_cell.  Throws if either cell is not defined.
    void add_inst_to_cell(const std::string& parent_cell,
                          const std::string& inst_name,
                          const std::string& child_cell,
                          double x, double y);

    // Mirror all descendants left-right (flip_x=true) or up-down (flip_x=false)
    // about the component's own centre.  The root bounding box is unchanged
    // (rectangular bbox is symmetric); only child absolute coords are updated.
    void flip_comp(const std::string& name, bool flip_x);

    // Rotate the component and all descendants CCW by 90, 180, or 270 degrees,
    // keeping the lower-left corner fixed.  For 90/270 the root bbox width and
    // height are swapped; for 180 the bbox is unchanged.
    void rotate_comp(const std::string& name, int degrees);

    // ── Computed properties ────────────────────────────────────────────────
    void compute_hpwl();
    void compute_fanout();
    void compute_all();

    // ── Busterm management ────────────────────────────────────────────────
    // Insert or replace one busterm row. Idempotent on the same id.
    void add_busterm(const BustermRow& bt);
    // Delete all busterm rows (used before re-deriving from scratch).
    void clear_busterms();

    // ── Queries ────────────────────────────────────────────────────────────
    std::vector<ComponentRow> all_components() const;
    // Components at exactly the given hierarchy depth.
    std::vector<ComponentRow> components_at_depth(int depth) const;
    // Pins that belong to the given component id.
    std::vector<PinRow>       pins_by_comp(int comp_id) const;
    std::vector<NetRow>       all_nets()        const;
    std::vector<PinRow>       all_pins()        const;
    std::vector<BustermRow>   all_busterms()    const;   // hier-derived only
    // Fetch a single busterm by id (incl. routing-time 'tb:<name>' rows that
    // all_busterms filters out); used by the topology-reload bridge.
    std::optional<BustermRow> busterm(const std::string& id) const;
    std::vector<BundleRow>    all_bundles()      const;

    // ── Bundle persistence (Stage 1 output; both flat and hier flows) ──────
    void add_bundle(const BundleRow& br);   // INSERT OR REPLACE into bundle
    void add_bundle_net(const std::string& bundle_id, const std::string& net_name);
    void add_bundle_busterm(const std::string& bundle_id,
                            const std::string& busterm_id,
                            const std::string& role = "");
    void clear_bundles();                   // wipe bundle + topology tables
    std::vector<std::string> bundle_nets(const std::string& bundle_id) const;
    // (busterm_id, role) pairs for a bundle.
    std::vector<std::pair<std::string, std::string>>
        bundle_busterms(const std::string& bundle_id) const;

    // ── Candidate topology persistence (Stage-2 output) ────────────────────
    void add_topology(const TopoRow& tr);           // INSERT OR REPLACE
    void add_topology_segment(const TopoSegRow& sr);
    void clear_topologies();                        // wipe topology + segments
    std::vector<TopoRow> topologies(const std::string& bundle_id) const;
    std::vector<TopoSegRow> topology_segments(const std::string& bundle_id,
                                              int cand_index) const;
    // Persist / read one seg_busterms endpoint→busterm link.  The routing bridge
    // (persist_seg_busterms in the buda module) inserts the referenced 'tb:<name>'
    // busterm row before calling add; only real taps get a row (junction = absent).
    void add_topology_seg_busterm(const TopoSegBustermRow& r);
    std::vector<TopoSegBustermRow> topology_seg_busterms(
        const std::string& bundle_id, int cand_index) const;
    // Mark one candidate as the selected topology for a bundle (planner choice or
    // pin): sets is_selected=1 for cand_index, 0 for the bundle's other rows.
    void set_topology_selected(const std::string& bundle_id, int cand_index);
    // Set the planner's assigned layer on one topology segment.
    void set_segment_layer(const std::string& bundle_id, int cand_index,
                           int seg_index, int layer);
    // Reset assigned_layer to -1 for ALL of a bundle's segments (drops stale
    // planner output when a re-plan picks a different candidate).
    void reset_assigned_layers(const std::string& bundle_id);
    // Remove the expanded per-instance bundle rows (is_replicated=1) and the
    // topologies keyed to them (idempotency for re-running run_planner hier).
    void clear_expanded_bundles();

    // ── Abstract-NUTS bus routing persistence (Stage-4 output) ─────────────
    void add_bus_segment(const BusSegRow& r);       // INSERT OR REPLACE
    void add_bus_via(const BusViaRow& r);           // INSERT OR REPLACE
    void clear_bus_routing();                       // wipe bus_segment + bus_via
    std::vector<BusSegRow> bus_segments(const std::string& bundle_id) const;
    std::vector<BusViaRow> bus_vias(const std::string& bundle_id) const;

    // ── Detailed-NUTS per-bit routing persistence ──────────────────────────
    void add_net_segment(const NetSegRow& r);       // INSERT OR REPLACE
    void add_net_via(const NetViaRow& r);           // INSERT OR REPLACE
    // Wipe net_segment + net_via (+ the route_snapshot they were hashed into);
    // bus rows stay. The caller rewrites the snapshot after re-persisting.
    void clear_detailed_routing();
    std::vector<NetSegRow> net_segments(const std::string& bundle_id) const;
    std::vector<NetViaRow> net_vias(const std::string& bundle_id) const;

    // Route fingerprint (singleton, id=1).
    void set_route_snapshot(const std::string& hash, int n_bus_segments,
                            int n_bus_vias, const std::string& stage,
                            int n_net_segments = 0, int n_net_vias = 0);
    RouteSnapshotRow route_snapshot() const;

    std::vector<std::string>  nets_by_hpwl(double lo, double hi)              const;
    std::vector<std::string>  comps_in_rect(double xl, double yl,
                                             double xh, double yh)            const;
    std::vector<std::string>  common_nets(const std::string& bundle_id1,
                                          const std::string& bundle_id2)      const;

    // ── Group management (mirrors GroupTree Python API) ────────────────────
    std::string new_group(const std::string& name, const std::string& color,
                          const std::string& parent_id = "");
    void        add_grp_member(const std::string& gid, const std::string& kind,
                                const std::string& ref);
    void        remove_grp_member(const std::string& gid, const std::string& kind,
                                   const std::string& ref);
    void        delete_group(const std::string& gid);
    std::vector<GrpRow> all_groups() const;

    // ── Metadata ───────────────────────────────────────────────────────────
    int    units() const;
    double die_w() const;   // explicit die_w, or union-bbox of all comps if unset
    double die_h() const;   // explicit die_h, or union-bbox of all comps if unset
    void   set_die(double w, double h);

    // ── Static helpers ─────────────────────────────────────────────────────
    static std::string db_path(const std::string& def_path);   // .def → .bdb

private:
    sqlite3* _db = nullptr;
    int    _units = 1000;
    double _die_w = 0.0, _die_h = 0.0;

    // Cached prepared statements for hot read paths.
    // Lazily prepared on first use; reset (not finalized) between calls.
    // Finalized in the destructor.
    mutable sqlite3_stmt* _q_all_components     = nullptr;
    mutable sqlite3_stmt* _q_components_at_depth= nullptr;
    mutable sqlite3_stmt* _q_all_nets           = nullptr;
    mutable sqlite3_stmt* _q_all_pins           = nullptr;
    mutable sqlite3_stmt* _q_pins_by_comp       = nullptr;
    mutable sqlite3_stmt* _q_all_busterms       = nullptr;

    void _exec(const char* sql);
    void _create_schema();
    // Bring the DB from its stored PRAGMA user_version up to SCHEMA_VERSION,
    // applying each version step in order, then stamp the new version. Steps
    // must be idempotent (a serialized *.bdb.sql round-trip resets user_version
    // to 0, so migrations re-run on the next open).
    void _migrate();
    // Seed/refresh provenance rows in the meta table (idempotent).
    void _seed_provenance();
    // Return the id of net `name`, creating a name-only row if absent (used by
    // add_bundle_net so flat-flow nets can be keyed by net_id).
    int  _ensure_net(const std::string& name);
    // Upsert a meta(key,value) row.
    void _set_meta(const std::string& key, const std::string& value);
    // Insert a pin for net_id at the component named inst_path, auto-register
    // the cell-type port (INSERT OR IGNORE in cell_pin).
    void _add_pin_by_path(int net_id, const std::string& inst_path,
                          const std::string& pin_name, const std::string& dir);
    // Recursively create component rows for all cell_children of cell_name,
    // rooted at parent_comp_id / parent_comp_name at absolute (abs_x, abs_y).
    // child_depth is the depth to assign to the immediate children.
    // Uses INSERT OR IGNORE — safe to call on already-expanded subtrees.
    void _expand_cell_children(int parent_comp_id,
                                const std::string& parent_comp_name,
                                const std::string& cell_name,
                                double abs_x, double abs_y,
                                int child_depth);
    // parsers
    struct LefCell { double w, h; };
    struct LefPin  { double ox, oy; std::string dir; };  // offset from cell origin
    using LefCells = std::unordered_map<std::string, LefCell>;
    using LefPins  = std::unordered_map<std::string,
                         std::unordered_map<std::string, LefPin>>;
    static LefCells _parse_lef_sizes(const std::string& lef_path);
    static LefPins  _parse_lef_pins (const std::string& lef_path);
};

}  // namespace buda
