#include "bdb.h"
#include <stdexcept>
#include <fstream>
#include <sstream>
#include <regex>
#include <algorithm>
#include <cmath>
#include <unordered_map>
#include <unordered_set>
#include <functional>

namespace buda {

namespace {
struct Stmt {
    sqlite3_stmt* p = nullptr;
    Stmt() = default;
    Stmt(sqlite3* db, const char* sql) { sqlite3_prepare_v2(db, sql, -1, &p, nullptr); }
    ~Stmt() { sqlite3_finalize(p); }
    Stmt(const Stmt&) = delete;
    Stmt& operator=(const Stmt&) = delete;
    operator sqlite3_stmt*() const { return p; }
};
} // namespace

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
            px       REAL,    -- absolute pin x in um (-1 if unknown)
            py       REAL,    -- absolute pin y in um (-1 if unknown)
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
        CREATE TABLE IF NOT EXISTS cell (
            name   TEXT PRIMARY KEY,
            width  REAL NOT NULL,
            height REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    )");
    Stmt mq(_db, "SELECT key,value FROM meta");
    while (sqlite3_step(mq) == SQLITE_ROW) {
        std::string k = (const char*)sqlite3_column_text(mq, 0);
        std::string v = (const char*)sqlite3_column_text(mq, 1);
        if (k == "units") _units = std::stoi(v);
        else if (k == "die_w") _die_w = std::stod(v);
        else if (k == "die_h") _die_h = std::stod(v);
    }
}

std::string BDB::db_path(const std::string& def_path) {
    auto dot = def_path.rfind('.');
    return (dot == std::string::npos ? def_path : def_path.substr(0, dot)) + ".bdb";
}

int    BDB::units() const { return _units; }
double BDB::die_w() const { return _die_w; }
double BDB::die_h() const { return _die_h; }

// ── LEF parsers ───────────────────────────────────────────────────────────────
// Both parsers use a line-by-line state machine — std::regex multiline flag
// only affects ^ / $ anchors, NOT '.', so whole-file regex cannot span lines.

// Strip DEF escaping (\[ and \]) so names match Verilog-elaborated paths
static std::string normalize_def_name(const std::string& s) {
    std::string out;
    out.reserve(s.size());
    for (size_t i = 0; i < s.size(); ++i) {
        if (s[i] == '\\' && i+1 < s.size() && (s[i+1]=='[' || s[i+1]==']'))
            continue;
        out += s[i];
    }
    return out;
}

static std::vector<std::string> split_ws(const std::string& s) {
    std::istringstream ss(s);
    return {std::istream_iterator<std::string>(ss), {}};
}

BDB::LefCells BDB::_parse_lef_sizes(const std::string& lef_path) {
    LefCells sizes;
    std::ifstream f(lef_path);
    if (!f) return sizes;

    std::string line, cur_cell;
    while (std::getline(f, line)) {
        auto tok = split_ws(line);
        if (tok.empty()) continue;
        if (tok[0] == "MACRO" && tok.size() >= 2)
            { cur_cell = tok[1]; continue; }
        if (tok[0] == "END" && tok.size() >= 2 && tok[1] == cur_cell)
            { cur_cell.clear(); continue; }
        if (!cur_cell.empty() && tok[0] == "SIZE" && tok.size() >= 4) {
            try { sizes[cur_cell] = {std::stod(tok[1]), std::stod(tok[3])}; }
            catch (...) {}
        }
    }
    return sizes;
}

BDB::LefPins BDB::_parse_lef_pins(const std::string& lef_path) {
    LefPins result;
    std::ifstream f(lef_path);
    if (!f) return result;

    std::string line, cur_cell, cur_pin, cur_dir, cur_use;
    std::vector<double> xs, ys;
    bool in_pin = false;

    auto flush_pin = [&]() {
        if (cur_cell.empty() || cur_pin.empty()) return;
        if (cur_use == "POWER" || cur_use == "GROUND" || cur_use == "CLOCK") {
            xs.clear(); ys.clear(); cur_pin.clear(); cur_dir.clear(); cur_use.clear();
            in_pin = false; return;
        }
        if (!xs.empty()) {
            double ox=0, oy=0;
            for (auto x:xs) ox+=x; for (auto y:ys) oy+=y;
            ox/=xs.size(); oy/=ys.size();
            std::string dir = cur_dir.empty() ? "UNKNOWN" : cur_dir;
            if (dir == "INOUT") dir = "OUTPUT";
            result[cur_cell][cur_pin] = {ox, oy, dir};
        }
        xs.clear(); ys.clear(); cur_pin.clear(); cur_dir.clear(); cur_use.clear();
        in_pin = false;
    };

    while (std::getline(f, line)) {
        auto tok = split_ws(line);
        if (tok.empty()) continue;

        if (tok[0] == "MACRO" && tok.size() >= 2)
            { cur_cell = tok[1]; in_pin = false; continue; }
        if (tok[0] == "END" && tok.size() >= 2 && tok[1] == cur_cell)
            { flush_pin(); cur_cell.clear(); continue; }

        if (cur_cell.empty()) continue;

        if (tok[0] == "PIN" && tok.size() >= 2) {
            flush_pin();
            cur_pin = tok[1]; in_pin = true; continue;
        }
        if (tok[0] == "END" && tok.size() >= 2 && tok[1] == cur_pin)
            { flush_pin(); continue; }

        if (!in_pin) continue;

        if (tok[0] == "DIRECTION" && tok.size() >= 2)
            cur_dir = tok[1];
        else if (tok[0] == "USE" && tok.size() >= 2)
            cur_use = tok[1];
        else if (tok[0] == "RECT" && tok.size() >= 5) {
            try {
                double x1=std::stod(tok[1]), y1=std::stod(tok[2]);
                double x2=std::stod(tok[3]), y2=std::stod(tok[4]);
                xs.push_back((x1+x2)/2); ys.push_back((y1+y2)/2);
            } catch (...) {}
        }
    }
    flush_pin();
    return result;
}

// ── DEF importer ─────────────────────────────────────────────────────────────

void BDB::import_def_lef(const std::string& def_path, const std::string& lef_path) {
    auto lef_sizes = _parse_lef_sizes(lef_path);
    auto lef_pins  = _parse_lef_pins(lef_path);

    _exec("DELETE FROM pin; DELETE FROM net_props; DELETE FROM net; "
          "DELETE FROM component; DELETE FROM cell;");

    std::ifstream f(def_path);
    if (!f) throw std::runtime_error("BDB: cannot open DEF: " + def_path);

    enum class State { IDLE, IN_COMPONENTS, IN_NETS };
    State state = State::IDLE;

    Stmt s_comp(_db,
        "INSERT OR IGNORE INTO component(name,cell,depth,x1,y1,x2,y2,is_leaf)"
        " VALUES(?,?,0,?,?,?,?,1)");
    Stmt s_net (_db, "INSERT OR IGNORE INTO net(name) VALUES(?)");
    Stmt s_pin (_db,
        "INSERT OR IGNORE INTO pin(net_id,comp_id,pin_name,dir,px,py)"
        " VALUES(?,?,?,?,?,?)");
    Stmt s_np  (_db, "INSERT OR IGNORE INTO net_props(net_id) VALUES(?)");

    // Persistent lookup stmts — reused across all calls to the lambdas below
    Stmt s_find_comp(_db, "SELECT id FROM component WHERE name=?");
    Stmt s_find_net (_db, "SELECT id FROM net WHERE name=?");
    Stmt s_find_cell(_db, "SELECT cell,x1,y1 FROM component WHERE id=?");

    // Populate cell table from LEF sizes
    {
        Stmt sc(_db, "INSERT OR REPLACE INTO cell(name,width,height) VALUES(?,?,?)");
        for (auto& [cname, sz] : lef_sizes) {
            sqlite3_bind_text  (sc, 1, cname.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_double(sc, 2, sz.w);
            sqlite3_bind_double(sc, 3, sz.h);
            sqlite3_step(sc); sqlite3_reset(sc);
        }
    }

    _exec("BEGIN");

    std::unordered_map<std::string,int> comp_id_cache, net_id_cache;

    auto get_comp_id = [&](const std::string& name) -> int {
        auto it = comp_id_cache.find(name);
        if (it != comp_id_cache.end()) return it->second;
        int id = -1;
        sqlite3_bind_text(s_find_comp, 1, name.c_str(), -1, SQLITE_TRANSIENT);
        if (sqlite3_step(s_find_comp) == SQLITE_ROW) id = sqlite3_column_int(s_find_comp, 0);
        sqlite3_reset(s_find_comp);
        comp_id_cache[name] = id;
        return id;
    };
    auto get_net_id = [&](const std::string& name) -> int {
        auto it = net_id_cache.find(name);
        if (it != net_id_cache.end()) return it->second;
        int id = -1;
        sqlite3_bind_text(s_find_net, 1, name.c_str(), -1, SQLITE_TRANSIENT);
        if (sqlite3_step(s_find_net) == SQLITE_ROW) id = sqlite3_column_int(s_find_net, 0);
        sqlite3_reset(s_find_net);
        net_id_cache[name] = id;
        return id;
    };

    std::string line, cur_net;
    const std::regex comp_re(
        R"(-\s+(\S+)\s+(\S+)\s+\+\s+(?:PLACED|FIXED)\s+\(\s*(\d+)\s+(\d+)\s*\)\s+(\S+))");
    const std::regex conn_re(R"(\(\s*(\S+)\s+(\S+)\s*\))");
    const std::regex net_hdr_re(R"(^\s*-\s+(\S+))");
    const std::regex re_sec_comp(R"(^COMPONENTS\s+\d+\s*;)");
    const std::regex re_sec_nets(R"(^NETS\s+\d+\s*;)");

    while (std::getline(f, line)) {
        // ── section transitions ──────────────────────────────────────────
        if (line.find("UNITS DISTANCE MICRONS") != std::string::npos) {
            std::istringstream ss(line);
            std::string tok;
            while (ss >> tok) if (std::isdigit(tok[0])) { _units=std::stoi(tok); break; }
            continue;
        }
        if (line.find("DIEAREA") != std::string::npos) {
            // DIEAREA ( 0 0 ) ( x y ) ;
            std::vector<int> nums;
            std::istringstream ss(line);
            std::string tok;
            while (ss >> tok)
                if (!tok.empty() && (std::isdigit(tok[0]) || tok[0]=='-'))
                    nums.push_back(std::stoi(tok));
            if (nums.size() >= 4) {
                _die_w = nums[2] / double(_units);
                _die_h = nums[3] / double(_units);
            }
            continue;
        }
        if (std::regex_search(line, re_sec_comp))
            { state=State::IN_COMPONENTS; continue; }
        if (line.find("END COMPONENTS") != std::string::npos)
            { state=State::IDLE; continue; }
        if (std::regex_search(line, re_sec_nets))
            { state=State::IN_NETS; continue; }
        if (line.find("END NETS") != std::string::npos)
            { state=State::IDLE; continue; }

        // ── component line ───────────────────────────────────────────────
        if (state == State::IN_COMPONENTS) {
            std::smatch m;
            if (!std::regex_search(line, m, comp_re)) continue;
            std::string inst=normalize_def_name(m[1]), cell=m[2];
            double x1 = std::stoi(m[3]) / double(_units);
            double y1 = std::stoi(m[4]) / double(_units);
            double w=0.5, h=0.5;
            auto cs = lef_sizes.find(cell);
            if (cs != lef_sizes.end()) { w=cs->second.w; h=cs->second.h; }
            sqlite3_bind_text  (s_comp,1,inst.c_str(),-1,SQLITE_TRANSIENT);
            sqlite3_bind_text  (s_comp,2,cell.c_str(),-1,SQLITE_TRANSIENT);
            sqlite3_bind_double(s_comp,3,x1);
            sqlite3_bind_double(s_comp,4,y1);
            sqlite3_bind_double(s_comp,5,x1+w);
            sqlite3_bind_double(s_comp,6,y1+h);
            sqlite3_step(s_comp); sqlite3_reset(s_comp);
        }

        // ── nets section ─────────────────────────────────────────────────
        if (state == State::IN_NETS) {
            // New net header: "- net_name" or "  - net_name" (leading whitespace allowed)
            auto first = line.find_first_not_of(" \t");
            if (first != std::string::npos && line[first] == '-') {
                std::smatch m;
                if (std::regex_search(line, m, net_hdr_re)) {
                    cur_net = m[1];
                    if (cur_net == "*") { cur_net=""; continue; }
                    sqlite3_bind_text(s_net,1,cur_net.c_str(),-1,SQLITE_TRANSIENT);
                    sqlite3_step(s_net); sqlite3_reset(s_net);
                    int nid = get_net_id(cur_net);
                    if (nid > 0) {
                        sqlite3_bind_int(s_np,1,nid);
                        sqlite3_step(s_np); sqlite3_reset(s_np);
                    }
                }
            }
            // Connection tokens: ( inst pin )
            if (cur_net.empty()) continue;
            int net_id = get_net_id(cur_net);
            if (net_id < 0) continue;
            auto cb = std::sregex_iterator(line.begin(), line.end(), conn_re);
            for (auto it=cb; it!=std::sregex_iterator(); ++it) {
                std::string inst=(*it)[1], pin=(*it)[2];
                if (inst=="PIN") continue;
                int cid = get_comp_id(inst);
                if (cid < 0) continue;

                std::string dir="UNKNOWN";
                double px=-1, py=-1;
                sqlite3_bind_int(s_find_cell, 1, cid);
                if (sqlite3_step(s_find_cell) == SQLITE_ROW) {
                    std::string cell = (const char*)sqlite3_column_text(s_find_cell, 0);
                    double x1 = sqlite3_column_double(s_find_cell, 1);
                    double y1 = sqlite3_column_double(s_find_cell, 2);
                    auto ci = lef_pins.find(cell);
                    if (ci != lef_pins.end()) {
                        auto pi = ci->second.find(pin);
                        if (pi != ci->second.end()) {
                            dir = pi->second.dir;
                            px  = x1 + pi->second.ox;
                            py  = y1 + pi->second.oy;
                        }
                    }
                }
                sqlite3_reset(s_find_cell);

                sqlite3_bind_int   (s_pin,1,net_id);
                sqlite3_bind_int   (s_pin,2,cid);
                sqlite3_bind_text  (s_pin,3,pin.c_str(),-1,SQLITE_TRANSIENT);
                sqlite3_bind_text  (s_pin,4,dir.c_str(),-1,SQLITE_TRANSIENT);
                sqlite3_bind_double(s_pin,5,px);
                sqlite3_bind_double(s_pin,6,py);
                sqlite3_step(s_pin); sqlite3_reset(s_pin);
            }
        }
    }

    // Persist die metadata so direct .bdb opens work without re-parsing
    Stmt sm(_db, "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)");
    auto save_meta = [&](const char* k, const std::string& v) {
        sqlite3_bind_text(sm,1,k,-1,SQLITE_STATIC);
        sqlite3_bind_text(sm,2,v.c_str(),-1,SQLITE_TRANSIENT);
        sqlite3_step(sm); sqlite3_reset(sm);
    };
    save_meta("units", std::to_string(_units));
    save_meta("die_w", std::to_string(_die_w));
    save_meta("die_h", std::to_string(_die_h));

    _exec("COMMIT");
}

// ── Computed properties ───────────────────────────────────────────────────────

void BDB::compute_hpwl() {
    _exec(R"(
        UPDATE net_props SET hpwl = (
            SELECT (MAX(px)-MIN(px)) + (MAX(py)-MIN(py))
            FROM   pin
            WHERE  pin.net_id = net_props.net_id
              AND  px >= 0 AND py >= 0
        )
    )");
}

void BDB::compute_fanout() {
    _exec(R"(
        UPDATE net_props SET fanout = (
            SELECT COUNT(*) FROM pin
            WHERE  pin.net_id = net_props.net_id
              AND  pin.dir = 'INPUT'
        )
    )");
}

void BDB::compute_all() { compute_hpwl(); compute_fanout(); }

// ── Bulk queries ─────────────────────────────────────────────────────────────

std::vector<ComponentRow> BDB::all_components() const {
    std::vector<ComponentRow> rows;
    Stmt q(_db,
        "SELECT id,name,cell,COALESCE(parent_id,-1),depth,x1,y1,x2,y2,is_leaf,is_replicated"
        " FROM component ORDER BY id");
    while (sqlite3_step(q)==SQLITE_ROW) {
        ComponentRow r;
        r.id           = sqlite3_column_int(q,0);
        r.name         = (const char*)sqlite3_column_text(q,1);
        r.cell         = (const char*)sqlite3_column_text(q,2);
        r.parent_id    = sqlite3_column_int(q,3);
        r.depth        = sqlite3_column_int(q,4);
        r.x1           = sqlite3_column_double(q,5);
        r.y1           = sqlite3_column_double(q,6);
        r.x2           = sqlite3_column_double(q,7);
        r.y2           = sqlite3_column_double(q,8);
        r.is_leaf      = sqlite3_column_int(q,9);
        r.is_replicated= sqlite3_column_int(q,10);
        rows.push_back(r);
    }
    return rows;
}

std::vector<NetRow> BDB::all_nets() const {
    std::vector<NetRow> rows;
    Stmt q(_db, "SELECT id,name FROM net ORDER BY name");
    while (sqlite3_step(q)==SQLITE_ROW)
        rows.push_back({sqlite3_column_int(q,0),
                        (const char*)sqlite3_column_text(q,1)});
    return rows;
}

std::vector<PinRow> BDB::all_pins() const {
    std::vector<PinRow> rows;
    Stmt q(_db,
        "SELECT p.net_id, p.comp_id, p.pin_name, p.dir, p.px, p.py FROM pin p");
    while (sqlite3_step(q)==SQLITE_ROW) {
        PinRow r;
        r.net_id   = sqlite3_column_int(q,0);
        r.comp_id  = sqlite3_column_int(q,1);
        r.pin_name = (const char*)sqlite3_column_text(q,2);
        r.dir      = (const char*)sqlite3_column_text(q,3);
        r.px       = sqlite3_column_double(q,4);
        r.py       = sqlite3_column_double(q,5);
        rows.push_back(r);
    }
    return rows;
}

std::vector<std::string> BDB::nets_by_hpwl(double lo, double hi) const {
    std::vector<std::string> names;
    Stmt q(_db,
        "SELECT n.name FROM net n JOIN net_props p ON p.net_id=n.id"
        " WHERE p.hpwl >= ? AND p.hpwl <= ? ORDER BY p.hpwl");
    sqlite3_bind_double(q,1,lo); sqlite3_bind_double(q,2,hi);
    while (sqlite3_step(q)==SQLITE_ROW)
        names.push_back((const char*)sqlite3_column_text(q,0));
    return names;
}

std::vector<std::string> BDB::comps_in_rect(double xl, double yl,
                                              double xh, double yh) const {
    std::vector<std::string> names;
    Stmt q(_db,
        "SELECT name FROM component"
        " WHERE x1 < ? AND x2 > ? AND y1 < ? AND y2 > ?"
        " ORDER BY name");
    sqlite3_bind_double(q,1,xh); sqlite3_bind_double(q,2,xl);
    sqlite3_bind_double(q,3,yh); sqlite3_bind_double(q,4,yl);
    while (sqlite3_step(q)==SQLITE_ROW)
        names.push_back((const char*)sqlite3_column_text(q,0));
    return names;
}

std::vector<std::string> BDB::common_nets(const std::string& gid1,
                                           const std::string& gid2) const {
    std::vector<std::string> names;
    Stmt q(_db, R"(
        SELECT DISTINCT n.name
        FROM   grp_member gm1
        JOIN   pin p1   ON gm1.kind='inst'
                       AND p1.comp_id=(SELECT id FROM component WHERE name=gm1.ref)
        JOIN   net n    ON n.id=p1.net_id
        JOIN   pin p2   ON p2.net_id=n.id
        JOIN   component c2 ON c2.id=p2.comp_id
        JOIN   grp_member gm2 ON gm2.kind='inst' AND gm2.ref=c2.name
                              AND gm2.grp_id=?
        WHERE  gm1.grp_id=?
        ORDER  BY n.name
    )");
    sqlite3_bind_text(q,1,gid2.c_str(),-1,SQLITE_STATIC);
    sqlite3_bind_text(q,2,gid1.c_str(),-1,SQLITE_STATIC);
    while (sqlite3_step(q)==SQLITE_ROW)
        names.push_back((const char*)sqlite3_column_text(q,0));
    return names;
}

// ── Verilog importer ──────────────────────────────────────────────────────────
//
// Parses a gate-level Verilog netlist and populates component/net/pin tables.
// Designed for Cadence Genus synthesis output (ariane, nvdla, mempool_tile, etc.).
//
// Strategy: record only "interesting" instances — those whose cell type is itself
// a defined module (hierarchical), or whose instance name uses Verilog escaped
// identifier syntax (\name<sp>), which Genus uses for macro instances like
// \macro_mem[0].i_ram.  Standard cells (INV_X1 g94) are silently dropped.
//
// Net scoping: each net gets a fully-qualified name using the elaborated
// instance path as a prefix (e.g. "i_cache_subsystem/i_icache/clk_i").
// Port connections propagate the parent-scope net name down to child modules so
// that the same physical wire has one canonical name across all hierarchy levels.
//
// Merge behaviour: does NOT clear the component table, so DEF-loaded placement
// coordinates survive.  Clears net/pin tables first (safe to call after
// import_def_lef).

void BDB::import_verilog(const std::string& v_path) {

    // ── Phase 1: collect defined module names (one fast pass) ────────────────
    // Also keep definition order: the top module is typically defined last.
    std::unordered_set<std::string> defined_mods;
    std::vector<std::string>        defined_order;
    {
        std::ifstream f(v_path);
        if (!f) throw std::runtime_error("BDB: cannot open: " + v_path);
        std::string line;
        while (std::getline(f, line)) {
            auto tok = split_ws(line);
            if (tok.size() >= 2 && tok[0] == "module") {
                std::string nm = tok[1];
                auto p = nm.find('(');
                if (p != std::string::npos) nm.resize(p);
                if (!nm.empty() && !defined_mods.count(nm)) {
                    defined_mods.insert(nm);
                    defined_order.push_back(nm);
                }
            }
        }
    }

    // ── local helpers ─────────────────────────────────────────────────────────

    // Extract simple net name from a port-map value expression.
    // Skips constants, concatenations, UNCONNECTED stubs; strips bit selects.
    auto clean_net = [](const std::string& expr) -> std::string {
        std::string e = expr;
        while (!e.empty() && std::isspace((unsigned char)e.front())) e.erase(e.begin());
        while (!e.empty() && std::isspace((unsigned char)e.back()))  e.pop_back();
        if (e.empty() || std::isdigit((unsigned char)e[0]) || e[0] == '{') return "";
        if (e[0] == '\\') e.erase(e.begin());   // strip verilog escape prefix
        auto br = e.find('[');
        if (br != std::string::npos) e.resize(br);
        while (!e.empty() && std::isspace((unsigned char)e.back())) e.pop_back();
        if (e.size() >= 11 && e.substr(0,11) == "UNCONNECTED") return "";
        return e;
    };

    // Parse ".port (net), ..." text → vector of (port, net) pairs
    auto parse_portmap = [&](const std::string& text)
        -> std::vector<std::pair<std::string,std::string>>
    {
        std::vector<std::pair<std::string,std::string>> result;
        size_t i = 0, sz = text.size();
        while (i < sz) {
            if (text[i] != '.') { ++i; continue; }
            ++i;
            // port name (plain or \escaped)
            std::string port;
            if (i < sz && text[i] == '\\') {
                ++i;
                size_t j = i;
                while (j < sz && text[j] != '(' && !std::isspace((unsigned char)text[j])) ++j;
                port = text.substr(i, j - i);
                i = j;
            } else {
                size_t j = i;
                while (j < sz && text[j] != '(' && text[j] != ',' &&
                       !std::isspace((unsigned char)text[j])) ++j;
                port = text.substr(i, j - i);
                i = j;
            }
            while (i < sz && text[i] != '(') ++i;
            if (i >= sz) break;
            ++i;
            // find matching ')'
            int depth = 1; size_t k = i;
            while (k < sz && depth > 0) {
                if (text[k] == '(') ++depth;
                else if (text[k] == ')') --depth;
                ++k;
            }
            std::string net = clean_net(text.substr(i, k - i - 1));
            if (!port.empty() && !net.empty())
                result.emplace_back(port, net);
            i = k;
        }
        return result;
    };

    // ── Phase 2: parse module bodies ─────────────────────────────────────────

    struct VInst {
        std::string cell, name;   // cell type, instance name (both unescaped)
        std::vector<std::pair<std::string,std::string>> portmap;
    };
    struct VMod { std::vector<VInst> insts; };
    std::unordered_map<std::string, VMod> mod_lib;

    static const std::unordered_set<std::string> kws = {
        "input","output","inout","wire","reg","assign","parameter","localparam",
        "always","initial","begin","end","if","else","case","casez","casex",
        "default","task","function","generate","endgenerate","for","while",
        "repeat","posedge","negedge","integer","real","time","event",
        "supply0","supply1","tri","genvar","defparam","specify","endspecify",
        "table","endtable","primitive","endprimitive","fork","join"
    };

    {
        std::ifstream f(v_path);
        std::string line, cur_mod, accum;
        bool in_header = false, in_body = false, accumulating = false;

        auto rtrim = [](std::string s) {
            while (!s.empty() && std::isspace((unsigned char)s.back())) s.pop_back();
            return s;
        };

        // Process a complete accumulated instance text
        auto finish_inst = [&](const std::string& text) {
            size_t i = 0, sz = text.size();
            while (i < sz && std::isspace((unsigned char)text[i])) ++i;
            // cell type
            std::string cell;
            bool esc_cell = (i < sz && text[i] == '\\');
            if (esc_cell) {
                ++i;
                size_t j = i;
                while (j < sz && !std::isspace((unsigned char)text[j])) ++j;
                cell = text.substr(i, j - i); i = j;
            } else {
                size_t j = i;
                while (j < sz && !std::isspace((unsigned char)text[j]) && text[j] != '(') ++j;
                cell = text.substr(i, j - i); i = j;
            }
            while (i < sz && std::isspace((unsigned char)text[i])) ++i;
            // instance name
            std::string inst_nm;
            bool esc_inst = (i < sz && text[i] == '\\');
            if (esc_inst) {
                ++i;
                size_t j = i;
                while (j < sz && !std::isspace((unsigned char)text[j])) ++j;
                inst_nm = text.substr(i, j - i); i = j;
            } else {
                size_t j = i;
                while (j < sz && !std::isspace((unsigned char)text[j]) && text[j] != '(') ++j;
                inst_nm = text.substr(i, j - i); i = j;
            }
            if (cell.empty() || inst_nm.empty()) return;
            bool is_defined = defined_mods.count(cell);
            // Skip standard cells: cell not a defined module AND inst name unescaped
            if (!is_defined && !esc_inst) return;
            // Skip uppercase standard cells even with escaped instance names
            // (e.g. "DFFR_X1 \arb_sel_q_reg[0]" in Genus output).
            // Real macros (fakeram45_*, etc.) contain lowercase letters.
            if (!is_defined && esc_inst) {
                bool has_lower = false;
                for (char c : cell) if (std::islower((unsigned char)c)) { has_lower=true; break; }
                if (!has_lower) return;
            }
            while (i < sz && text[i] != '(') ++i;
            VInst vi{ cell, inst_nm, {} };
            if (i < sz) vi.portmap = parse_portmap(text.substr(i));
            mod_lib[cur_mod].insts.push_back(std::move(vi));
        };

        while (std::getline(f, line)) {
            // strip // comments
            auto ci = line.find("//");
            if (ci != std::string::npos) line.resize(ci);

            if (!in_body && !in_header) {
                auto tok = split_ws(line);
                if (tok.size() >= 2 && tok[0] == "module") {
                    std::string nm = tok[1];
                    auto p = nm.find('('); if (p != std::string::npos) nm.resize(p);
                    cur_mod = nm;
                    mod_lib.emplace(cur_mod, VMod{});
                    if (line.find(';') != std::string::npos) in_body = true;
                    else in_header = true;
                }
                continue;
            }
            if (in_header) {
                if (line.find(';') != std::string::npos) { in_header = false; in_body = true; }
                continue;
            }

            // ── in module body ────────────────────────────────────────────────
            if (line.find("endmodule") != std::string::npos) {
                if (accumulating) { finish_inst(accum); accum.clear(); accumulating = false; }
                in_body = false; cur_mod.clear(); continue;
            }
            if (accumulating) {
                accum += ' '; accum += line;
                auto trimmed = rtrim(line);
                if (!trimmed.empty() && trimmed.back() == ';') {
                    // ends with ); → complete instance; ends with just ; → discard
                    if (trimmed.size() >= 2 && trimmed[trimmed.size()-2] == ')')
                        finish_inst(accum);
                    accum.clear(); accumulating = false;
                }
                continue;
            }
            // Check if line could start an instance declaration
            auto tok = split_ws(line);
            if (tok.empty() || kws.count(tok[0])) continue;
            accum = line;
            auto trimmed = rtrim(line);
            if (!trimmed.empty() && trimmed.back() == ';') {
                if (trimmed.size() >= 2 && trimmed[trimmed.size()-2] == ')')
                    finish_inst(accum);
                accum.clear();
            } else {
                accumulating = true;
            }
        }
        if (accumulating && !accum.empty()) { finish_inst(accum); accum.clear(); }
    }

    // ── Phase 3: find top module ──────────────────────────────────────────────
    // The top module is the one never instantiated by any other module.
    // There may be multiple (unused utility modules); pick the LAST one in the
    // file — by convention Genus places the top module at the end of the netlist.
    std::string top_mod;
    {
        std::unordered_set<std::string> instantiated;
        for (auto& [mn, mod] : mod_lib)
            for (auto& vi : mod.insts)
                instantiated.insert(vi.cell);
        for (auto it = defined_order.rbegin(); it != defined_order.rend(); ++it)
            if (!instantiated.count(*it)) { top_mod = *it; break; }
    }
    if (top_mod.empty()) return;

    // ── Phase 4: elaborate hierarchy → BDB ───────────────────────────────────
    // Preserve component placement from any prior import_def_lef call.
    // Clear only net/pin tables.
    _exec("DELETE FROM pin; DELETE FROM net_props; DELETE FROM net;");

    // UPSERT: keep existing x1/y1/x2/y2 (from DEF), update hierarchy fields
    Stmt s_comp(_db, R"(
        INSERT INTO component(name,cell,parent_id,depth,x1,y1,x2,y2,is_leaf)
        VALUES(?,?,?,?,-1,-1,-1,-1,?)
        ON CONFLICT(name) DO UPDATE SET
          cell=excluded.cell, parent_id=excluded.parent_id,
          depth=excluded.depth, is_leaf=excluded.is_leaf
    )");
    Stmt s_net (_db, "INSERT OR IGNORE INTO net(name) VALUES(?)");
    Stmt s_pin (_db,
        "INSERT OR IGNORE INTO pin(net_id,comp_id,pin_name,dir,px,py)"
        " VALUES(?,?,?,?,?,?)");
    Stmt s_np  (_db, "INSERT OR IGNORE INTO net_props(net_id) VALUES(?)");
    Stmt s_find_by_name(_db, "SELECT id FROM component WHERE name=?");

    _exec("BEGIN");

    std::unordered_map<std::string,int> comp_ids, net_ids;

    auto upsert_comp = [&](const std::string& path, const std::string& cell,
                           int par_id, int depth, bool is_leaf) -> int {
        sqlite3_bind_text(s_comp,1,path.c_str(),-1,SQLITE_TRANSIENT);
        sqlite3_bind_text(s_comp,2,cell.c_str(),-1,SQLITE_TRANSIENT);
        if (par_id >= 0) sqlite3_bind_int(s_comp,3,par_id);
        else             sqlite3_bind_null(s_comp,3);
        sqlite3_bind_int(s_comp,4,depth);
        sqlite3_bind_int(s_comp,5,is_leaf ? 1 : 0);
        sqlite3_step(s_comp); sqlite3_reset(s_comp);
        // Always SELECT — last_insert_rowid is unreliable after UPSERT DO UPDATE:
        // it returns the last INSERT rowid from any prior transaction, not the
        // updated row's rowid.
        sqlite3_bind_text(s_find_by_name, 1, path.c_str(), -1, SQLITE_TRANSIENT);
        int id = -1;
        if (sqlite3_step(s_find_by_name) == SQLITE_ROW)
            id = sqlite3_column_int(s_find_by_name, 0);
        sqlite3_reset(s_find_by_name);
        comp_ids[path] = id;
        return id;
    };

    auto get_net = [&](const std::string& name) -> int {
        auto it = net_ids.find(name);
        if (it != net_ids.end()) return it->second;
        sqlite3_bind_text(s_net,1,name.c_str(),-1,SQLITE_TRANSIENT);
        sqlite3_step(s_net);
        int id = (int)sqlite3_last_insert_rowid(_db);
        sqlite3_reset(s_net);
        sqlite3_bind_int(s_np,1,id); sqlite3_step(s_np); sqlite3_reset(s_np);
        net_ids[name] = id;
        return id;
    };

    // Recursive elaboration.
    // ctx maps (local wire name → fully-qualified root-scope net name)
    // so port connections propagate the parent net name into child scopes.
    using Ctx = std::unordered_map<std::string,std::string>;

    std::function<void(const std::string&, const std::string&, int, int, const Ctx&)>
    elaborate = [&](const std::string& mod_nm, const std::string& path,
                    int par_id, int depth, const Ctx& ctx)
    {
        auto mit = mod_lib.find(mod_nm);
        if (mit == mod_lib.end()) return;

        for (auto& vi : mit->second.insts) {
            bool is_leaf = !defined_mods.count(vi.cell);
            // Instance path uses unescaped name (matches DEF convention)
            std::string inst_path = path.empty() ? vi.name : path + "/" + vi.name;

            int cid = upsert_comp(inst_path, vi.cell, par_id, depth, is_leaf);

            // Build child context and create pin records
            Ctx child_ctx;
            for (auto& [port, local_net] : vi.portmap) {
                // Resolve local_net through current ctx (port connections from parent)
                auto it = ctx.find(local_net);
                std::string qnet = (it != ctx.end())
                    ? it->second
                    : (path.empty() ? local_net : path + "/" + local_net);

                child_ctx[port] = qnet;

                int nid = get_net(qnet);
                if (nid <= 0 || cid <= 0) continue;
                sqlite3_bind_int   (s_pin,1,nid);
                sqlite3_bind_int   (s_pin,2,cid);
                sqlite3_bind_text  (s_pin,3,port.c_str(),-1,SQLITE_TRANSIENT);
                sqlite3_bind_text  (s_pin,4,"UNKNOWN",-1,SQLITE_STATIC);
                sqlite3_bind_double(s_pin,5,-1.0);
                sqlite3_bind_double(s_pin,6,-1.0);
                sqlite3_step(s_pin); sqlite3_reset(s_pin);
            }

            if (!is_leaf)
                elaborate(vi.cell, inst_path, cid, depth + 1, child_ctx);
        }
    };

    elaborate(top_mod, "", -1, 0, {});
    _exec("COMMIT");
}

// ── Mutations ─────────────────────────────────────────────────────────────────

void BDB::move_comp(const std::string& name, double x, double y) {
    Stmt q(_db, "SELECT x1, y1, x2, y2 FROM component WHERE name=?");
    sqlite3_bind_text(q, 1, name.c_str(), -1, SQLITE_TRANSIENT);
    if (sqlite3_step(q) != SQLITE_ROW)
        throw std::runtime_error("move_comp: not found: " + name);
    double w = sqlite3_column_double(q, 2) - sqlite3_column_double(q, 0);
    double h = sqlite3_column_double(q, 3) - sqlite3_column_double(q, 1);
    Stmt u(_db, "UPDATE component SET x1=?, y1=?, x2=?, y2=? WHERE name=?");
    sqlite3_bind_double(u, 1, x);
    sqlite3_bind_double(u, 2, y);
    sqlite3_bind_double(u, 3, x + w);
    sqlite3_bind_double(u, 4, y + h);
    sqlite3_bind_text  (u, 5, name.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_step(u);
    compute_hpwl();
}

void BDB::resize_cell(const std::string& cell, double w, double h) {
    // Keep the cell definition in sync
    Stmt uc(_db, "INSERT OR REPLACE INTO cell(name,width,height) VALUES(?,?,?)");
    sqlite3_bind_text  (uc, 1, cell.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_double(uc, 2, w);
    sqlite3_bind_double(uc, 3, h);
    sqlite3_step(uc);
    // Update every instance's bounding box
    Stmt u(_db, "UPDATE component SET x2=x1+?, y2=y1+? WHERE cell=?");
    sqlite3_bind_double(u, 1, w);
    sqlite3_bind_double(u, 2, h);
    sqlite3_bind_text  (u, 3, cell.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_step(u);
    compute_hpwl();
}

int BDB::add_comp(const std::string& name, const std::string& cell,
                  const std::string& parent_name,
                  double x1, double y1, double x2, double y2, bool is_leaf) {
    int par_id = -1, depth = 0;
    if (!parent_name.empty()) {
        Stmt qp(_db, "SELECT id, depth FROM component WHERE name=?");
        sqlite3_bind_text(qp, 1, parent_name.c_str(), -1, SQLITE_TRANSIENT);
        if (sqlite3_step(qp) != SQLITE_ROW)
            throw std::runtime_error("add_comp: parent not found: " + parent_name);
        par_id = sqlite3_column_int(qp, 0);
        depth  = sqlite3_column_int(qp, 1) + 1;
    }
    Stmt ins(_db, R"(
        INSERT INTO component(name,cell,parent_id,depth,x1,y1,x2,y2,is_leaf)
        VALUES(?,?,?,?,?,?,?,?,?)
    )");
    sqlite3_bind_text  (ins, 1, name.c_str(),   -1, SQLITE_TRANSIENT);
    sqlite3_bind_text  (ins, 2, cell.c_str(),   -1, SQLITE_TRANSIENT);
    if (par_id >= 0) sqlite3_bind_int(ins, 3, par_id);
    else             sqlite3_bind_null(ins, 3);
    sqlite3_bind_int   (ins, 4, depth);
    sqlite3_bind_double(ins, 5, x1);
    sqlite3_bind_double(ins, 6, y1);
    sqlite3_bind_double(ins, 7, x2);
    sqlite3_bind_double(ins, 8, y2);
    sqlite3_bind_int   (ins, 9, is_leaf ? 1 : 0);
    if (sqlite3_step(ins) != SQLITE_DONE)
        throw std::runtime_error("add_comp: insert failed (name exists?): " + name);
    int id = (int)sqlite3_last_insert_rowid(_db);
    compute_hpwl();
    return id;
}

void BDB::add_cell(const std::string& name, double w, double h) {
    Stmt ins(_db, "INSERT OR REPLACE INTO cell(name,width,height) VALUES(?,?,?)");
    sqlite3_bind_text  (ins, 1, name.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_double(ins, 2, w);
    sqlite3_bind_double(ins, 3, h);
    if (sqlite3_step(ins) != SQLITE_DONE)
        throw std::runtime_error("add_cell: failed for: " + name);
}

std::vector<CellRow> BDB::all_cells() const {
    Stmt q(_db, "SELECT name, width, height FROM cell ORDER BY name");
    std::vector<CellRow> result;
    while (sqlite3_step(q) == SQLITE_ROW)
        result.push_back({ (const char*)sqlite3_column_text(q,0),
                           sqlite3_column_double(q,1),
                           sqlite3_column_double(q,2) });
    return result;
}

int BDB::add_inst(const std::string& inst_name, const std::string& cell_name,
                  const std::string& parent_name, double x, double y) {
    // Look up cell size
    Stmt qc(_db, "SELECT width, height FROM cell WHERE name=?");
    sqlite3_bind_text(qc, 1, cell_name.c_str(), -1, SQLITE_TRANSIENT);
    if (sqlite3_step(qc) != SQLITE_ROW)
        throw std::runtime_error("add_inst: cell not defined: " + cell_name);
    double w = sqlite3_column_double(qc, 0);
    double h = sqlite3_column_double(qc, 1);

    // Resolve parent — coordinates are relative to parent's x1,y1
    int par_id = -1, depth = 0;
    double abs_x = x, abs_y = y;
    if (!parent_name.empty()) {
        Stmt qp(_db, "SELECT id, depth, x1, y1 FROM component WHERE name=?");
        sqlite3_bind_text(qp, 1, parent_name.c_str(), -1, SQLITE_TRANSIENT);
        if (sqlite3_step(qp) != SQLITE_ROW)
            throw std::runtime_error("add_inst: parent not found: " + parent_name);
        par_id = sqlite3_column_int   (qp, 0);
        depth  = sqlite3_column_int   (qp, 1) + 1;
        abs_x  = sqlite3_column_double(qp, 2) + x;
        abs_y  = sqlite3_column_double(qp, 3) + y;
        // Mark parent as non-leaf now that it has a child
        Stmt ul(_db, "UPDATE component SET is_leaf=0 WHERE id=?");
        sqlite3_bind_int(ul, 1, par_id);
        sqlite3_step(ul);
    }

    Stmt ins(_db, R"(
        INSERT INTO component(name,cell,parent_id,depth,x1,y1,x2,y2,is_leaf)
        VALUES(?,?,?,?,?,?,?,?,1)
    )");
    sqlite3_bind_text  (ins, 1, inst_name.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text  (ins, 2, cell_name.c_str(), -1, SQLITE_TRANSIENT);
    if (par_id >= 0) sqlite3_bind_int(ins, 3, par_id);
    else             sqlite3_bind_null(ins, 3);
    sqlite3_bind_int   (ins, 4, depth);
    sqlite3_bind_double(ins, 5, abs_x);
    sqlite3_bind_double(ins, 6, abs_y);
    sqlite3_bind_double(ins, 7, abs_x + w);
    sqlite3_bind_double(ins, 8, abs_y + h);
    if (sqlite3_step(ins) != SQLITE_DONE)
        throw std::runtime_error("add_inst: insert failed (name exists?): " + inst_name);
    int id = (int)sqlite3_last_insert_rowid(_db);
    compute_hpwl();
    return id;
}

std::vector<BustermRow>  BDB::all_busterms() const { return {}; }
std::vector<BundleRow>   BDB::all_bundles()   const { return {}; }

std::string BDB::new_group(const std::string&, const std::string&,
                            const std::string&) { return {}; }
void BDB::add_grp_member(const std::string&,const std::string&,
                          const std::string&) {}
void BDB::remove_grp_member(const std::string&,const std::string&,
                              const std::string&) {}
void BDB::delete_group(const std::string&) {}
std::vector<GrpRow> BDB::all_groups() const { return {}; }

}  // namespace buda
