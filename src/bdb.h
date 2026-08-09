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

#include <climits>
#include <limits>
#include <string>
#include <vector>
#include <optional>
#include <unordered_map>
#include "sqlite3.h"

namespace buda {

// Forward declaration only: the LEF reader is an implementation detail of the
// importer, and bdb.h is included nearly everywhere (lef_io.h is not).
struct LefLibrary;
struct DefTracks;
struct DefBlockage;

// What `import_def_lef` hands back: the counts a caller must be able to
// reconcile, plus the PHYSICAL data that belongs to the session rather than
// the database (tracks, blockages, halos).  Deliberately not the whole
// DefDesign — components and nets went into the tables, and a real DEF's
// copy of them is the part that would not fit in memory twice.
struct VerilogImportStats {
    std::string top_module;
    int elaborated = 0;             // component rows written
    // Instances of a module the netlist does not define, dropped as library
    // cells.  Reported rather than silent: the filter is a heuristic, and a
    // dropped instance is a hole in the hierarchy that every later stage
    // treats as absence rather than as an omission.
    int skipped_library_cells = 0;   // instances
    int skipped_kinds = 0;           // DISTINCT cell types among them
    // The first few distinct cell types, capped.  `skipped_kinds` is the
    // true total, so a caller can tell a complete list from a truncated one
    // — the list alone cannot say, since its entries are already unique.
    std::vector<std::string> skipped_cells;
};

struct DefImportStats {
    int declared_components = -1, imported_components = 0;
    int declared_nets = -1, imported_nets = 0;
    int declared_pins = -1, imported_pins = 0;
    int placed_components = 0;
    int port_components = 0;          // synthesized boundary comps (Phase 3d)
    std::vector<std::string> missing_cells;   // in DEF, absent from LEF
    std::vector<std::string> warnings;
    std::string unmodelled;           // census, as in the LEF reader
    // Physical data for the session to apply; coordinates already converted
    // to LAYOUT UNITS by the importer (docs/internal/engine_units.md).
    struct Track { std::string dir; double start, step; int count;
                   std::vector<std::string> layers; };
    struct Keepout { std::string layer; double x1, y1, x2, y2; std::string why; };
    std::vector<Track>   tracks;
    std::vector<Keepout> keepouts;
};

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
    // Instance orientation relative to its parent, one of the 8 orthogonal
    // LEF/DEF/OA tokens (N/S/E/W/FN/FS/FE/FW). The bbox above is the resulting
    // axis-aligned extent (so downstream stays bbox-only); orient is the extra
    // fact a faithful GDS SREF re-emit needs. Default 'N' (identity). (v12)
    std::string orient = "N";
    // A synthesized boundary component standing in for a DEF top-level PORT
    // (v23, Phase 3d).  Downstream stages treat it as a component because
    // that is what makes a die-edge net routable at all; this flag is what
    // stops the fiction from passing as a real instance.
    bool        is_port = false;
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

// One persisted seg_conns junction link (topology_seg_conn row, v12): endpoint
// `endpoint` of segment `seg_index` lands on segment `other_seg`.  One endpoint
// can join several segments (3+ meeting at a point), hence other_seg in the PK.
struct TopoSegConnRow {
    std::string bundle_id;
    int         cand_index = 0;
    int         seg_index  = 0;
    std::string endpoint;    // 'start' | 'end'
    int         other_seg = 0;
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
    // v18: written by the planner's expansion persist (synthetic per-instance
    // wrapper row), as opposed to a bundler-persisted replica — the two are
    // otherwise shape-identical, and load_pipeline's expanded view must keep
    // exactly the expanded rows.
    bool        is_expanded = false;
    // Bottom-up template copy (v18): this expanded per-instance row carries a
    // uniform copy of its template's local solve — load_pipeline restores it
    // as a locked (pinned, never-moved) wrapper.
    bool        bu_locked = false;
    // v19: rotation-class clone provenance. Non-empty = this row is a CLONE
    // template synthesized for a marked cell's 90°-rotated instance class
    // (cell_context = the virtual clone name, e.g. "alu90"); the value is
    // the ORIGINAL template's bundle id. The clone is a routing-template
    // identity only — it never appears in cell/component/pin tables, so
    // GDS/DEF/Verilog interchange is unaffected. Bottom-up gates resolve
    // the clone's marked-ness through this link.
    std::string cloned_from;
    // v21: the NDR rule governing this bundle when it was persisted ("" =
    // default rule).  load_pipeline compares it against the FRESH prefix
    // resolution: a mismatch means the rules changed since the checkpoint,
    // so the restored plan was priced under a different demand and is
    // VOIDED (LOUD, re-plan required).
    std::string ndr_rule;
};

// One declared NDR rule (v21 ndr_rule table).  Raw multiplier values as
// declared by def_ndr — quantization to slots happens at resolution time
// (buda_cmds/ndr_cmds.py), so the stored rule survives a policy change in
// the quantizer.  layers is a CSV of layer ids ("" = any).
struct NdrRuleRow {
    std::string name;
    double      width_x   = 1.0;
    double      spacing_x = 1.0;
    int         shield_mode  = 0;
    int         shield_per_n = 0;
    std::string shield_net   = "GND";
    std::string layers;
    // v22: R5a crediting opted in (an END shield may be satisfied by an
    // adjacent pattern rail electrically identical to shield_net).
    int         credit = 0;
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
    bool        is_pinned = false;  // pre-plan select_topology pin (v10; load_pipeline
                                    // restores it so a resumed run_planner honors it)
    std::string topo_uid;           // stable content identity (v14, hex fingerprint over
                                    // all load-bearing persisted state; Phase E1 of
                                    // topo_conn_unification.md) — recomputable from a
                                    // checkpoint alone, so pre-v14 rows backfill on load
    std::string source = "generated"; // 'generated' | 'user' | 'dogleg' (v15, Phase E4):
                                    // bulk regeneration may only delete 'generated' rows
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
    int         edge_id = -1;       // MST-edge identity (v14; closes the documented
                                    // round-trip gap in topology.h Segment::edge_id)
    // Overlap-U per-segment perpendicular slide clamp (v16; topology.h
    // Segment::perp_clamp_lo/hi).  INT_MIN/INT_MAX = unclamped (every non-U_OVL
    // segment); persisted so a resumed U_OVL candidate reloads clamped.
    int         perp_clamp_lo = INT_MIN;
    int         perp_clamp_hi = INT_MAX;
};

// One TEG-over bridge segment of a candidate topology (Topology::bridge_segments:
// block_name -> Segment placed along the outer face of the block's union bbox
// when the trunk falls in a gap between its rects). Persisted so a load_pipeline
// resume restores TEG-over designs losslessly (v11).
struct TopoBridgeRow {
    std::string id;                 // bundle_id (FK)
    int         cand_index = 0;
    std::string block_name;         // the multi-rect block the bridge spans
    int         x1 = 0, y1 = 0, x2 = 0, y2 = 0;
    int         layer_hint = 0;
    bool        is_jog = false;
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
    // Stage-4 solver state (v9) so a session can rehydrate its NUTSResult from
    // the BDB (load_pipeline) and continue into detailed NUTS: the hard
    // perpendicular placement interval, and the cross-trunk-layer corner-split
    // track bounds (stored NULL when unbounded; +/-inf here).
    double      interval_lo = 0, interval_hi = 0;
    double      track_lo_bound = -std::numeric_limits<double>::infinity();
    double      track_hi_bound =  std::numeric_limits<double>::infinity();
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
    // Bottom-up template planning flag (v17): when set, the hier flow plans /
    // NUTSes this cell's local interconnect once and copies the result to
    // every instance (see docs/internal/hier_bottom_up_planning.md).
    bool        bottom_up = false;
    // Per-cell layer band [layer_floor..layer_cap] (v20): the cell's own
    // interconnect defaults to full use of layers inside the band and none
    // outside (docs/internal/hier_layer_caps.md). -1 = unset (cap: uncapped;
    // floor: no lower bound).
    int         layer_cap   = -1;
    int         layer_floor = -1;
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
    // v9 = routing-time busterm attrs (teg_mode/orig_*) + topology_seg_busterm join;
    // v10 = load_pipeline resume support: bus_segment stage-4 solver state
    //       (interval + corner-split track bounds), bundle_net.ord (bit order),
    //       topology.is_pinned (pre-plan pin survives a checkpoint);
    // v11 = topology_bridge_segment (TEG-over bridges), closing the last
    //       un-persisted Topology field so TEG-over designs resume losslessly.
    // v12 = topology_seg_conn (seg-to-seg junction links persisted logically);
    // v13 = component.orient (instance rotation/mirror as an 8-orientation
    //       token) so GDS import->export->re-import preserves orientation.
    // v14 = topology.topo_uid (stable candidate identity) + topology_segment.edge_id
    //       (MST-edge identity round-trip);
    // v15 = topology.source (candidate provenance) + bundle.gen_knobs (per-bundle
    //       generation-knob memo);
    // v16 = topology_segment.perp_clamp_lo/hi (overlap-U per-segment perp slide
    //       clamp) so a resumed U_OVL candidate reloads clamped.
    // v17 = cell.bottom_up (bottom-up template planning flag: the cell's local
    //       interconnect is planned/NUTSed once and copied to every instance).
    // v18 = bundle.is_expanded (planner-expanded per-instance row, distinct
    //       from a bundler replica) + bundle.bu_locked (the row is a uniform
    //       bottom-up copy of its template's local solve — restored as a
    //       locked wrapper by load_pipeline).
    // v19 = bundle.cloned_from (rotation-class clone template provenance:
    //       the 90°-instance class of a marked cell gets its own template,
    //       named e.g. "alu90", linked to the original template's id).
    // v20 = cell.layer_cap/layer_floor (per-cell layer band, the binary
    //       policy of docs/internal/hier_layer_caps.md; '*' default lives in
    //       meta.layer_cap_default) + the cell_layer_share table (fractional
    //       per-layer shares — schema landed with the band, consumed by
    //       Phase 3).
    // v21 = NDR rule persistence (docs/internal/ndr_architecture.md §4):
    //       the ndr_rule table (declared rules, raw multiplier values so a
    //       re-quantization policy change re-derives slots on load) + the
    //       ndr_scope table (prefix → rule attachments; '*' = the global
    //       default scope) + bundle.ndr_rule (the governing rule stamped on
    //       each persisted bundle, so load_pipeline can detect a
    //       since-changed resolution and VOID the restored plan).
    // v22 = ndr_rule.credit (R5a end-shield crediting opt-in — part of the
    //       rule's pricing basis, so it rides the same table).
    static constexpr int SCHEMA_VERSION = 24;

    explicit BDB(const std::string& db_path);
    ~BDB();

    // Snapshot the CURRENT database state into a new binary file at
    // dest_path (SQLite online-backup API: safe while this BDB is open,
    // works for file-backed and :memory: databases alike; an existing
    // destination is overwritten).  The save-as primitive under
    // `save_bdb <path>`.
    void save_copy(const std::string& dest_path) const;

    // ── Schema version & metadata ──────────────────────────────────────────
    // The schema version stored in this DB (PRAGMA user_version). Equals
    // SCHEMA_VERSION after open, since the constructor migrates forward.
    int schema_version() const;
    // Read a meta(key,value) row, or `def` if absent. Provenance keys include
    // 'schema_version' and 'bdb_tool'.
    std::string meta_get(const std::string& key,
                         const std::string& def = "") const;
    // Write (upsert) a meta(key,value) row. Public sibling of the internal
    // _set_meta so tools/session code can persist design-level flags.
    void meta_set(const std::string& key, const std::string& value);

    // ── Ingestion ──────────────────────────────────────────────────────────
    DefImportStats import_def_lef(const std::string& def_path,
                                  const std::string& lef_path);
    VerilogImportStats import_verilog(const std::string& v_path);
    // Wipe the design tables (pin/net_props/net/component/cell) for a fresh
    // load — what import_def_lef does internally; public for import_gds.
    void clear_design();
    // Attach a label-recovered pin: ensure a net row for `net_name` (creating
    // a name-only row if absent) and insert a pin on `comp_id` at (px, py)
    // with dir UNKNOWN. Used by import_gds Phase G2 (TEXT-label net recovery);
    // duplicates (same net/comp/pin_name) are ignored.
    void add_label_pin(const std::string& net_name, int comp_id,
                       const std::string& pin_name, double px, double py);

    // ── Cell definitions ───────────────────────────────────────────────────
    void add_cell(const std::string& name, double w, double h);
    std::vector<CellRow> all_cells() const;
    // Bottom-up template planning flag (v17). set_cell_bottom_up throws if the
    // cell is not defined; cell_bottom_up returns false for an unknown cell.
    void set_cell_bottom_up(const std::string& cell, bool on);
    bool cell_bottom_up(const std::string& cell) const;
    // Names of all cells with bottom_up set, sorted (empty when none).
    std::vector<std::string> bottom_up_cells() const;
    // Per-cell layer band (v20). set_cell_layer_band throws if the cell is
    // not defined; (-1,-1) clears. cell_layer_band returns {floor, cap}
    // ({-1,-1} for unknown/unset cells); layer_capped_cells lists cells with
    // a cap set, sorted.
    void set_cell_layer_band(const std::string& cell, int floor, int cap);
    std::pair<int,int> cell_layer_band(const std::string& cell) const;
    std::vector<std::string> layer_capped_cells() const;
    // Fractional layer shares (v20 cell_layer_share table, Phase 3).
    // set_cell_layer_share upserts (share <= 0 deletes the row); throws if
    // the cell is not defined. cell_layer_shares returns the cell's
    // (layer_id, share) rows sorted by layer; layer_share_cells lists cells
    // holding any share row, sorted.
    void set_cell_layer_share(const std::string& cell, int layer_id, double share);
    std::vector<std::pair<int,double>> cell_layer_shares(const std::string& cell) const;
    std::vector<std::string> layer_share_cells() const;

    // ── NDR rule persistence (v21, docs/internal/ndr_architecture.md §4) ──
    // set_ndr_rule upserts a declared rule; ndr_rules returns every rule
    // sorted by name.  set_ndr_scope upserts a prefix→rule attachment
    // (throws if the rule is not declared — the FK made LOUD);
    // delete_ndr_scope removes one (missing = no-op); ndr_scopes returns
    // every (prefix, rule) sorted by prefix.  clear_ndr drops all scopes
    // and rules (the session-level 'forget everything' path).
    void set_ndr_rule(const NdrRuleRow& r);
    std::vector<NdrRuleRow> ndr_rules() const;
    void set_ndr_scope(const std::string& prefix, const std::string& rule);
    void delete_ndr_scope(const std::string& prefix);
    std::vector<std::pair<std::string,std::string>> ndr_scopes() const;
    void clear_ndr();
    // The cell-type child graph as (parent_cell, child_cell) edges, sorted and
    // de-duplicated (one edge per distinct pair however many instances).  The
    // structural source for intrinsic cell LEVELS (set_layer_caps_by_depth,
    // docs/internal/hier_layer_caps.md §13 Phase 5 Q1); a design elaborated
    // straight into component rows (import_verilog) has no cell_children rows,
    // so callers union this with the component tree.
    std::vector<std::pair<std::string,std::string>> cell_child_edges() const;

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
    // Translate a component AND its whole subtree by (dx, dy) — unlike
    // move_comp, which repositions only the named component's bbox and would
    // break instance congruence for a hierarchical block.  Used by
    // align_bottom_up's placement nudges.
    void translate_comp(const std::string& name, double dx, double dy);
    // Set a single instance bounding box exactly.
    void set_comp_is_leaf(const std::string& name, bool is_leaf);
    void set_comp_bbox(const std::string& name,
                       double x1, double y1, double x2, double y2);
    // Give every UNPLACED container the bounding box of its placed
    // descendants, grown by `margin` on each side.  Returns how many were
    // placed; `unresolved` (if given) collects the containers left unplaced
    // because nothing under them has a position.
    //
    // This is what a DEF + Verilog merge needs and cannot get from either
    // file.  A DEF is FLAT — `COMPONENTS` lists leaf instances only — so a
    // hierarchical instance has no row anywhere and `import_verilog`, which
    // knows the tree but no geometry, writes it unplaced.  BustermGen then
    // skips it (`comp.x1 < 0`), and every busterm collapses to depth 0: the
    // hierarchy exists in the database while the ROUTING interface is flat.
    //
    // Deliberately an explicit call rather than a step inside
    // `import_verilog`: it INVENTS geometry the files never stated, which is
    // exactly the kind of thing that should appear in the script.  It never
    // touches a component that already has a position.
    int derive_container_bboxes(double margin = 0.0,
                                std::vector<std::string>* unresolved = nullptr);
    // Update the cell definition and every instance's x2/y2 to x1+w, y1+h.
    void resize_cell(const std::string& cell, double w, double h);
    void set_comp_cell(const std::string& comp_name, const std::string& new_cell);
    // Insert a new component row using explicit absolute coordinates.
    // parent_name="" for a root instance.  Throws if name already exists.
    int  add_comp(const std::string& name, const std::string& cell,
                  const std::string& parent_name,
                  double x1, double y1, double x2, double y2,
                  bool is_leaf = true, const std::string& orient = "N");
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
    // keep_user=true (v15): user-sourced topology rows — and the bundle
    // rows they FK to — survive the wipe (the re-add upserts those bundle
    // rows in place).  Routing rows are always cleared (invalidated).
    void clear_bundles(bool keep_user = false);                   // wipe bundle + topology tables
    std::vector<std::string> bundle_nets(const std::string& bundle_id) const;
    // (busterm_id, role) pairs for a bundle.
    std::vector<std::pair<std::string, std::string>>
        bundle_busterms(const std::string& bundle_id) const;

    // ── Write batching ─────────────────────────────────────────────────────
    // Wrap a burst of row inserts in ONE transaction so the WAL is fsync'd once
    // instead of once per statement (autocommit).  Nestable via a depth counter:
    // only the outermost begin/commit touches the DB, so composing persist
    // helpers (each guarding its own body) is safe.  rollback_batch collapses
    // the whole stack — call it on error to discard a partial write.
    // The depth counter is advanced only AFTER the BEGIN/COMMIT succeeds, so a
    // throwing outer statement leaves depth consistent with SQLite's real state
    // (failed BEGIN → depth 0; failed COMMIT → depth 1, txn still open) and the
    // caller can always recover via rollback_batch().
    void begin_batch();
    void commit_batch();
    void rollback_batch();

    // ── Candidate topology persistence (Stage-2 output) ────────────────────
    void add_topology(const TopoRow& tr);           // INSERT OR REPLACE
    void add_topology_segment(const TopoSegRow& sr);
    // keep_user=true (Phase E4): rows with source='user' — and the busterm/
    // segment/link/bridge rows backing them — survive the wipe, so a bulk
    // re-persist can never delete a hand-committed candidate from an earlier
    // session.  Callers then renumber_topology() any kept row whose cand_index
    // would collide with the fresh 0..n-1 block.
    void clear_topologies(bool keep_user = false);
    // Move one candidate's rows (topology + segments + busterm links +
    // seg-conn links + bridges) to a new cand_index (v15 orphan renumbering).
    void renumber_topology(const std::string& bundle_id, int old_ci, int new_ci);
    // Delete one candidate's rows across all five topology tables (v15; used
    // when a kept user row is about to be rewritten from the in-memory pool
    // at a different index — the pool write recreates it with fresh rows).
    void delete_topology(const std::string& bundle_id, int ci);
    // Per-bundle generation-knob memo (v15): the additive-generation knob set
    // last used for this bundle, honored by a resumed bulk generation.
    void set_bundle_gen_knobs(const std::string& bundle_id, const std::string& knobs);
    std::string bundle_gen_knobs(const std::string& bundle_id) const;                        // wipe topology + segments
    std::vector<TopoRow> topologies(const std::string& bundle_id) const;
    std::vector<TopoSegRow> topology_segments(const std::string& bundle_id,
                                              int cand_index) const;
    // Persist / read one seg_busterms endpoint→busterm link.  The routing bridge
    // (persist_seg_busterms in the buda module) inserts the referenced 'tb:<name>'
    // busterm row before calling add; only real taps get a row (junction = absent).
    void add_topology_seg_busterm(const TopoSegBustermRow& r);
    // Persist / read one seg_conns junction link (v12); the routing bridge
    // (persist_seg_conns in the buda module) writes one row per real junction —
    // a missing (seg, endpoint) is a free end or a busterm tap.
    void add_topology_seg_conn(const TopoSegConnRow& r);
    std::vector<TopoSegConnRow> topology_seg_conns(
        const std::string& bundle_id, int cand_index) const;
    std::vector<TopoSegBustermRow> topology_seg_busterms(
        const std::string& bundle_id, int cand_index) const;
    // TEG-over bridge segments (Topology::bridge_segments), one row per
    // (candidate, block); wiped with the topologies by clear_topologies().
    void add_topology_bridge(const TopoBridgeRow& r);   // INSERT OR REPLACE
    std::vector<TopoBridgeRow> topology_bridges(
        const std::string& bundle_id, int cand_index) const;
    // All candidates' bridges for one bundle in a single query (the reload path
    // buckets by cand_index — avoids one empty SELECT per candidate on designs
    // without TEG-over blocks, the common case).
    std::vector<TopoBridgeRow> all_topology_bridges(
        const std::string& bundle_id) const;
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
    // The per-bundle twin: drop ONE expanded bundle's rows (bundle + nets +
    // busterms + topologies + annotations + routing) so a selective
    // re-persist can rewrite only the bundles whose plan changed
    // (chip_flow_parallelism.md C1).  Invalidates the route_snapshot like
    // the bulk clear — every selective site rewrites routing right after.
    void clear_expanded_bundle(const std::string& bundle_id);

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

    // ── Import scale (layout units per micron) ─────────────────────────────
    // The ONE place a design decides what a layout unit is.  BUDA's engine is
    // unit-agnostic (see docs/internal/engine_units.md), so this factor does
    // not change any algorithm — it changes what the stored numbers COUNT.
    //
    //   1.0 (default)  1 layout unit = 1 µm.  Historic behaviour, bit-identical.
    //   <UNITS>        1 layout unit = 1 DEF database unit — the import is then
    //                  EXACT, with no quantization at all.  Selected by
    //                  set_import_scale_from_def_units(), which reads the DEF's
    //                  own `UNITS DISTANCE MICRONS` so the caller does not have
    //                  to know it in advance.
    //
    // Applied at import ONLY.  Everything downstream — the ~59 int(round())
    // BDB→Floorplan conversions in the Python layer included — then works in
    // the chosen unit by construction, because there is nothing left to
    // convert.  Persisted as meta 'lu_per_um' and restored on open, so a
    // reopened design keeps the scale its coordinates were written in.
    void   set_import_scale(double lu_per_um);
    void   set_import_scale_from_def_units();   // 1 layout unit = 1 DEF DBU
    double import_scale() const;                // layout units per micron
    double die_w() const;   // explicit die_w, or union-bbox of all comps if unset
    double die_h() const;   // explicit die_h, or union-bbox of all comps if unset
    void   set_die(double w, double h);

    // ── Static helpers ─────────────────────────────────────────────────────
    static std::string db_path(const std::string& def_path);   // .def → .bdb

private:
    sqlite3* _db = nullptr;
    int    _units = 1000;
    double _die_w = 0.0, _die_h = 0.0;
    double _lu_per_um = 1.0;          // layout units per micron (import scale)
    bool   _lu_from_def_units = false; // resolve _lu_per_um from the DEF's UNITS

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
    int  _batch_depth = 0;   // begin/commit_batch nesting depth (0 = autocommit)
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
    // Insert the net_props row a net needs to appear in compute_hpwl/fanout and
    // nets_by_hpwl (idempotent) — the DEF/Verilog/label importers all do this.
    void _ensure_net_props(int net_id);
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
    struct LefCell { double w, h; std::string cls; };
    struct LefPin  { double ox, oy; std::string dir; };  // offset from cell origin
    using LefCells = std::unordered_map<std::string, LefCell>;
    using LefPins  = std::unordered_map<std::string,
                         std::unordered_map<std::string, LefPin>>;
    // Projections of a parsed LEF onto what the BDB stores (Phase 2a; the
    // parsing itself lives in lef_io.cpp).
    static LefCells _lef_cells(const LefLibrary& lib);
    static LefPins  _lef_pins (const LefLibrary& lib);
    static std::string _lef_unmodelled_census(const LefLibrary& lib);
};

}  // namespace buda
