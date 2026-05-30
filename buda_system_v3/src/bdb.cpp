#include "bdb.h"
#include <stdexcept>
#include <fstream>
#include <sstream>
#include <regex>
#include <algorithm>
#include <cmath>
#include <unordered_map>

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
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    )");
    // Load any previously persisted metadata
    sqlite3_stmt* mq;
    sqlite3_prepare_v2(_db, "SELECT key,value FROM meta", -1, &mq, nullptr);
    while (sqlite3_step(mq) == SQLITE_ROW) {
        std::string k = (const char*)sqlite3_column_text(mq, 0);
        std::string v = (const char*)sqlite3_column_text(mq, 1);
        if (k == "units") _units = std::stoi(v);
        else if (k == "die_w") _die_w = std::stod(v);
        else if (k == "die_h") _die_h = std::stod(v);
    }
    sqlite3_finalize(mq);
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
            // SIZE w BY h ;
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
    // Parse LEF first
    auto lef_sizes = _parse_lef_sizes(lef_path);
    auto lef_pins  = _parse_lef_pins(lef_path);

    // Clear existing data
    _exec("DELETE FROM pin; DELETE FROM net_props; DELETE FROM net; DELETE FROM component;");

    std::ifstream f(def_path);
    if (!f) throw std::runtime_error("BDB: cannot open DEF: " + def_path);

    enum class State { IDLE, IN_COMPONENTS, IN_NETS };
    State state = State::IDLE;

    // Prepared statements
    sqlite3_stmt *s_comp=nullptr, *s_net=nullptr, *s_pin=nullptr, *s_np=nullptr;
    sqlite3_prepare_v2(_db,
        "INSERT OR IGNORE INTO component(name,cell,depth,x1,y1,x2,y2,is_leaf)"
        " VALUES(?,?,0,?,?,?,?,1)", -1, &s_comp, nullptr);
    sqlite3_prepare_v2(_db,
        "INSERT OR IGNORE INTO net(name) VALUES(?)", -1, &s_net, nullptr);
    sqlite3_prepare_v2(_db,
        "INSERT OR IGNORE INTO pin(net_id,comp_id,pin_name,dir,px,py)"
        " VALUES(?,?,?,?,?,?)", -1, &s_pin, nullptr);
    sqlite3_prepare_v2(_db,
        "INSERT OR IGNORE INTO net_props(net_id) VALUES(?)", -1, &s_np, nullptr);

    _exec("BEGIN");

    // Caches to avoid repeated lookups
    std::unordered_map<std::string,int> comp_id_cache, net_id_cache;

    auto get_comp_id = [&](const std::string& name) -> int {
        auto it = comp_id_cache.find(name);
        if (it != comp_id_cache.end()) return it->second;
        sqlite3_stmt* q; int id=-1;
        sqlite3_prepare_v2(_db,"SELECT id FROM component WHERE name=?",-1,&q,nullptr);
        sqlite3_bind_text(q,1,name.c_str(),-1,SQLITE_STATIC);
        if (sqlite3_step(q)==SQLITE_ROW) id=sqlite3_column_int(q,0);
        sqlite3_finalize(q);
        comp_id_cache[name]=id; return id;
    };
    auto get_net_id = [&](const std::string& name) -> int {
        auto it = net_id_cache.find(name);
        if (it != net_id_cache.end()) return it->second;
        sqlite3_stmt* q; int id=-1;
        sqlite3_prepare_v2(_db,"SELECT id FROM net WHERE name=?",-1,&q,nullptr);
        sqlite3_bind_text(q,1,name.c_str(),-1,SQLITE_STATIC);
        if (sqlite3_step(q)==SQLITE_ROW) id=sqlite3_column_int(q,0);
        sqlite3_finalize(q);
        net_id_cache[name]=id; return id;
    };

    std::string line, cur_net;
    std::regex comp_re(
        R"(-\s+(\S+)\s+(\S+)\s+\+\s+(?:PLACED|FIXED)\s+\(\s*(\d+)\s+(\d+)\s*\)\s+(\S+))");
    std::regex conn_re(R"(\(\s*(\S+)\s+(\S+)\s*\))");
    std::regex net_hdr_re(R"(^-\s+(\S+))");

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
            auto nums = std::vector<int>();
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
        if (std::regex_search(line, std::regex(R"(^COMPONENTS\s+\d+\s*;)")))
            { state=State::IN_COMPONENTS; continue; }
        if (line.find("END COMPONENTS") != std::string::npos)
            { state=State::IDLE; continue; }
        if (std::regex_search(line, std::regex(R"(^NETS\s+\d+\s*;)")))
            { state=State::IN_NETS; continue; }
        if (line.find("END NETS") != std::string::npos)
            { state=State::IDLE; continue; }

        // ── component line ───────────────────────────────────────────────
        if (state == State::IN_COMPONENTS) {
            std::smatch m;
            if (!std::regex_search(line, m, comp_re)) continue;
            std::string inst=m[1], cell=m[2];
            double x1 = std::stoi(m[3]) / double(_units);
            double y1 = std::stoi(m[4]) / double(_units);
            double w=0.5, h=0.5;
            auto cs = lef_sizes.find(cell);
            if (cs != lef_sizes.end()) { w=cs->second.w; h=cs->second.h; }
            sqlite3_bind_text(s_comp,1,inst.c_str(),-1,SQLITE_TRANSIENT);
            sqlite3_bind_text(s_comp,2,cell.c_str(),-1,SQLITE_TRANSIENT);
            sqlite3_bind_double(s_comp,3,x1);
            sqlite3_bind_double(s_comp,4,y1);
            sqlite3_bind_double(s_comp,5,x1+w);
            sqlite3_bind_double(s_comp,6,y1+h);
            sqlite3_step(s_comp); sqlite3_reset(s_comp);
        }

        // ── nets section ─────────────────────────────────────────────────
        if (state == State::IN_NETS) {
            // New net header: "- net_name"
            if (!line.empty() && line[0]=='-') {
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

                // pin direction + absolute position
                std::string dir="UNKNOWN";
                double px=-1, py=-1;
                // look up cell for this inst
                sqlite3_stmt* qc;
                sqlite3_prepare_v2(_db,"SELECT cell,x1,y1 FROM component WHERE id=?",-1,&qc,nullptr);
                sqlite3_bind_int(qc,1,cid);
                if (sqlite3_step(qc)==SQLITE_ROW) {
                    std::string cell=(const char*)sqlite3_column_text(qc,0);
                    double x1=sqlite3_column_double(qc,1);
                    double y1=sqlite3_column_double(qc,2);
                    auto ci=lef_pins.find(cell);
                    if (ci!=lef_pins.end()) {
                        auto pi=ci->second.find(pin);
                        if (pi!=ci->second.end()) {
                            dir=pi->second.dir;
                            px=x1+pi->second.ox;
                            py=y1+pi->second.oy;
                        }
                    }
                }
                sqlite3_finalize(qc);

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
    sqlite3_stmt* sm;
    sqlite3_prepare_v2(_db,
        "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", -1, &sm, nullptr);
    auto save_meta = [&](const char* k, const std::string& v) {
        sqlite3_bind_text(sm,1,k,-1,SQLITE_STATIC);
        sqlite3_bind_text(sm,2,v.c_str(),-1,SQLITE_TRANSIENT);
        sqlite3_step(sm); sqlite3_reset(sm);
    };
    save_meta("units", std::to_string(_units));
    save_meta("die_w", std::to_string(_die_w));
    save_meta("die_h", std::to_string(_die_h));
    sqlite3_finalize(sm);

    _exec("COMMIT");
    sqlite3_finalize(s_comp); sqlite3_finalize(s_net);
    sqlite3_finalize(s_pin);  sqlite3_finalize(s_np);
}

// ── Computed properties ───────────────────────────────────────────────────────

void BDB::compute_hpwl() {
    // For each net, compute bbox over all known pin positions → HPWL
    const char* sql = R"(
        UPDATE net_props SET hpwl = (
            SELECT (MAX(px)-MIN(px)) + (MAX(py)-MIN(py))
            FROM   pin
            WHERE  pin.net_id = net_props.net_id
              AND  px >= 0 AND py >= 0
        )
    )";
    _exec(sql);
}

void BDB::compute_fanout() {
    const char* sql = R"(
        UPDATE net_props SET fanout = (
            SELECT COUNT(*) FROM pin
            WHERE  pin.net_id = net_props.net_id
              AND  pin.dir = 'INPUT'
        )
    )";
    _exec(sql);
}

void BDB::compute_all() { compute_hpwl(); compute_fanout(); }

// ── Bulk queries ─────────────────────────────────────────────────────────────

std::vector<ComponentRow> BDB::all_components() const {
    std::vector<ComponentRow> rows;
    sqlite3_stmt* q;
    sqlite3_prepare_v2(_db,
        "SELECT id,name,cell,COALESCE(parent_id,-1),depth,x1,y1,x2,y2,is_leaf,is_replicated"
        " FROM component ORDER BY id", -1, &q, nullptr);
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
    sqlite3_finalize(q);
    return rows;
}

std::vector<NetRow> BDB::all_nets() const {
    std::vector<NetRow> rows;
    sqlite3_stmt* q;
    sqlite3_prepare_v2(_db,"SELECT id,name FROM net ORDER BY name",-1,&q,nullptr);
    while (sqlite3_step(q)==SQLITE_ROW)
        rows.push_back({sqlite3_column_int(q,0),
                        (const char*)sqlite3_column_text(q,1)});
    sqlite3_finalize(q);
    return rows;
}

std::vector<PinRow> BDB::all_pins() const {
    std::vector<PinRow> rows;
    sqlite3_stmt* q;
    sqlite3_prepare_v2(_db,
        "SELECT p.net_id, p.comp_id, p.pin_name, p.dir, p.px, p.py FROM pin p",
        -1, &q, nullptr);
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
    sqlite3_finalize(q);
    return rows;
}

std::vector<std::string> BDB::nets_by_hpwl(double lo, double hi) const {
    std::vector<std::string> names;
    sqlite3_stmt* q;
    sqlite3_prepare_v2(_db,
        "SELECT n.name FROM net n JOIN net_props p ON p.net_id=n.id"
        " WHERE p.hpwl >= ? AND p.hpwl <= ? ORDER BY p.hpwl",
        -1, &q, nullptr);
    sqlite3_bind_double(q,1,lo); sqlite3_bind_double(q,2,hi);
    while (sqlite3_step(q)==SQLITE_ROW)
        names.push_back((const char*)sqlite3_column_text(q,0));
    sqlite3_finalize(q);
    return names;
}

std::vector<std::string> BDB::comps_in_rect(double xl, double yl,
                                              double xh, double yh) const {
    std::vector<std::string> names;
    sqlite3_stmt* q;
    sqlite3_prepare_v2(_db,
        "SELECT name FROM component"
        " WHERE x1 < ? AND x2 > ? AND y1 < ? AND y2 > ?"
        " ORDER BY name",
        -1, &q, nullptr);
    sqlite3_bind_double(q,1,xh); sqlite3_bind_double(q,2,xl);
    sqlite3_bind_double(q,3,yh); sqlite3_bind_double(q,4,yl);
    while (sqlite3_step(q)==SQLITE_ROW)
        names.push_back((const char*)sqlite3_column_text(q,0));
    sqlite3_finalize(q);
    return names;
}

std::vector<std::string> BDB::common_nets(const std::string& gid1,
                                           const std::string& gid2) const {
    std::vector<std::string> names;
    sqlite3_stmt* q;
    const char* sql = R"(
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
    )";
    sqlite3_prepare_v2(_db, sql, -1, &q, nullptr);
    sqlite3_bind_text(q,1,gid2.c_str(),-1,SQLITE_STATIC);
    sqlite3_bind_text(q,2,gid1.c_str(),-1,SQLITE_STATIC);
    while (sqlite3_step(q)==SQLITE_ROW)
        names.push_back((const char*)sqlite3_column_text(q,0));
    sqlite3_finalize(q);
    return names;
}

// ── Stub impls ────────────────────────────────────────────────────────────────
void BDB::import_verilog(const std::string&) {}
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
