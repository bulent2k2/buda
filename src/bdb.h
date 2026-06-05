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

// ── BDB ───────────────────────────────────────────────────────────────────

class BDB {
public:
    explicit BDB(const std::string& db_path);
    ~BDB();

    // ── Ingestion ──────────────────────────────────────────────────────────
    void import_def_lef(const std::string& def_path, const std::string& lef_path);
    void import_verilog(const std::string& v_path);

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
