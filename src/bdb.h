#pragma once
// bdb.h — Buda Physical Design Database
// SQLite-backed store for components, nets, pins, busterms, bundles, and groups.
// All other v3 modules access physical design data exclusively through BDB.

#include <string>
#include <vector>
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
    std::string dir;          // INPUT OUTPUT INOUT
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
};

struct BundleRow {
    std::string id;
    int         depth;
    std::string strategy;
    std::string parent_id;
    bool        is_replicated;
};

struct GrpRow {
    std::string id;
    std::string name;
    std::string color;
    std::string parent_id;
};

struct CellRow {
    std::string name;
    double      width, height;
};

// ── BDB ───────────────────────────────────────────────────────────────────

class BDB {
public:
    explicit BDB(const std::string& db_path);
    ~BDB();

    // ── Ingestion ──────────────────────────────────────────────────────────
    void import_def_lef(const std::string& def_path, const std::string& lef_path);
    void import_verilog(const std::string& v_path);

    // ── Cell definitions ───────────────────────────────────────────────────
    // Upsert a cell definition (name, width, height).
    void add_cell(const std::string& name, double w, double h);
    std::vector<CellRow> all_cells() const;

    // ── Mutations ──────────────────────────────────────────────────────────
    // Move a single instance to new origin (x,y); size is preserved.
    void move_comp(const std::string& name, double x, double y);
    // Update the cell definition and every instance's x2/y2 to x1+w, y1+h.
    void resize_cell(const std::string& cell, double w, double h);
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

    // ── Computed properties ────────────────────────────────────────────────
    void compute_hpwl();
    void compute_fanout();
    void compute_all();

    // ── Queries ────────────────────────────────────────────────────────────
    std::vector<ComponentRow> all_components() const;
    std::vector<NetRow>       all_nets()        const;
    std::vector<PinRow>       all_pins()        const;
    std::vector<BustermRow>   all_busterms()    const;
    std::vector<BundleRow>    all_bundles()      const;

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
    double die_w() const;
    double die_h() const;

    // ── Static helpers ─────────────────────────────────────────────────────
    static std::string db_path(const std::string& def_path);   // .def → .bdb

private:
    sqlite3* _db = nullptr;
    int    _units = 1000;
    double _die_w = 0.0, _die_h = 0.0;

    void _exec(const char* sql);
    void _create_schema();
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
