#include "bdb.h"
#include <stdexcept>

namespace buda {

BDB::BDB(const std::string& db_path) {
    if (sqlite3_open(db_path.c_str(), &_db) != SQLITE_OK)
        throw std::runtime_error("BDB: cannot open " + db_path);
    _exec("PRAGMA journal_mode=WAL;");
    _exec("PRAGMA foreign_keys=ON;");
    _create_schema();
}

BDB::~BDB() {
    if (_db) sqlite3_close(_db);
}

void BDB::_exec(const char* sql) {
    char* err = nullptr;
    if (sqlite3_exec(_db, sql, nullptr, nullptr, &err) != SQLITE_OK) {
        std::string msg = err ? err : "unknown error";
        sqlite3_free(err);
        throw std::runtime_error(std::string("BDB SQL error: ") + msg);
    }
}

void BDB::_create_schema() {
    _exec(R"(
        CREATE TABLE IF NOT EXISTS component (
            id           INTEGER PRIMARY KEY,
            name         TEXT UNIQUE NOT NULL,
            cell         TEXT,
            parent_id    INTEGER REFERENCES component(id),
            depth        INTEGER DEFAULT 0,
            x1 REAL, y1 REAL, x2 REAL, y2 REAL,
            is_leaf      INTEGER DEFAULT 1,
            is_replicated INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS net (
            id   INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pin (
            net_id   INTEGER REFERENCES net(id),
            comp_id  INTEGER REFERENCES component(id),
            pin_name TEXT,
            dir      TEXT,
            PRIMARY KEY (net_id, comp_id, pin_name)
        );
        CREATE TABLE IF NOT EXISTS net_props (
            net_id       INTEGER PRIMARY KEY REFERENCES net(id),
            hpwl         REAL,
            fanout       INTEGER,
            driver_comp  TEXT,
            bus_name     TEXT,
            bit_index    INTEGER,
            bundle_id    INTEGER
        );
        CREATE TABLE IF NOT EXISTS busterm (
            id         TEXT PRIMARY KEY,
            comp_id    INTEGER REFERENCES component(id),
            hier_path  TEXT NOT NULL,
            depth      INTEGER,
            x1 REAL, y1 REAL, x2 REAL, y2 REAL,
            resolution TEXT DEFAULT 'BLOCK',
            parent_id  TEXT REFERENCES busterm(id)
        );
        CREATE TABLE IF NOT EXISTS bundle (
            id           TEXT PRIMARY KEY,
            depth        INTEGER DEFAULT 0,
            strategy     TEXT,
            parent_id    TEXT REFERENCES bundle(id),
            is_replicated INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS bundle_net (
            bundle_id TEXT REFERENCES bundle(id),
            net_id    INTEGER REFERENCES net(id),
            PRIMARY KEY (bundle_id, net_id)
        );
        CREATE TABLE IF NOT EXISTS bundle_busterm (
            bundle_id  TEXT REFERENCES bundle(id),
            busterm_id TEXT REFERENCES busterm(id),
            PRIMARY KEY (bundle_id, busterm_id)
        );
        CREATE TABLE IF NOT EXISTS grp (
            id        TEXT PRIMARY KEY,
            name      TEXT NOT NULL,
            color     TEXT,
            parent_id TEXT REFERENCES grp(id)
        );
        CREATE TABLE IF NOT EXISTS grp_member (
            grp_id TEXT REFERENCES grp(id),
            kind   TEXT,
            ref    TEXT,
            PRIMARY KEY (grp_id, kind, ref)
        );
    )");
}

std::string BDB::db_path(const std::string& def_path) {
    auto dot = def_path.rfind('.');
    return (dot == std::string::npos ? def_path : def_path.substr(0, dot)) + ".bdb";
}

// Remaining method stubs — to be implemented
void BDB::import_def_lef(const std::string&, const std::string&) {}
void BDB::import_verilog(const std::string&) {}
void BDB::compute_hpwl()   {}
void BDB::compute_fanout() {}
void BDB::compute_all()    { compute_hpwl(); compute_fanout(); }

std::vector<ComponentRow> BDB::all_components() const { return {}; }
std::vector<NetRow>       BDB::all_nets()        const { return {}; }
std::vector<BustermRow>   BDB::all_busterms()    const { return {}; }
std::vector<BundleRow>    BDB::all_bundles()      const { return {}; }

std::vector<std::string> BDB::nets_by_hpwl(double, double)    const { return {}; }
std::vector<std::string> BDB::comps_in_rect(double,double,double,double) const { return {}; }
std::vector<std::string> BDB::common_nets(const std::string&,
                                           const std::string&) const { return {}; }

std::string BDB::new_group(const std::string&, const std::string&,
                            const std::string&) { return {}; }
void BDB::add_grp_member(const std::string&,const std::string&,
                          const std::string&) {}
void BDB::remove_grp_member(const std::string&,const std::string&,
                              const std::string&) {}
void BDB::delete_group(const std::string&) {}
std::vector<GrpRow> BDB::all_groups() const { return {}; }

}  // namespace buda
