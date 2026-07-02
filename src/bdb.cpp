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

#include "bdb.h"
#include <stdexcept>
#include <fstream>
#include <iterator>
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
    if (!_db) return;
    sqlite3_finalize(_q_all_components);
    sqlite3_finalize(_q_components_at_depth);
    sqlite3_finalize(_q_all_nets);
    sqlite3_finalize(_q_all_pins);
    sqlite3_finalize(_q_pins_by_comp);
    sqlite3_finalize(_q_all_busterms);
    sqlite3_close(_db);
}

void BDB::_exec(const char* sql) {
    char* err = nullptr;
    if (sqlite3_exec(_db, sql, nullptr, nullptr, &err) != SQLITE_OK) {
        std::string msg = err ? err : "unknown error";
        sqlite3_free(err);
        throw std::runtime_error(std::string("BDB SQL error: ") + msg);
    }
}

// Bundle tables (schema v2). Membership is keyed by net *name* so it is flow-
// agnostic: the flat flow's nets may not have rows in the BDB `net` table, and
// Membership is keyed by net_id (FK to net): consistent with net_props.bundle_id
// and normalized. The flat flow's nets need not pre-exist in the net table —
// add_bundle_net auto-creates a name-only net row (_ensure_net) so both flows key
// by id. Kept in one place so _create_schema (fresh DB) and _migrate (rebuild)
// never drift.
static const char* BUNDLE_DDL = R"(
    CREATE TABLE IF NOT EXISTS bundle (
        id             TEXT PRIMARY KEY,
        level          INTEGER DEFAULT 0,   -- hierarchy level (0 = top / flat)
        strategy       TEXT,                -- STRICT | CONVERGENT | BIDIRECTIONAL
        reason         TEXT,                -- grouping signature
        num_terminals  INTEGER DEFAULT 0,
        cell_context   TEXT,                -- "" for top-level; cell type otherwise
        instances      TEXT,                -- JSON array of instance paths
        parent_id      TEXT REFERENCES bundle(id),  -- "" / NULL for top-level
        is_replicated  INTEGER DEFAULT 0,
        drv_spec_depth INTEGER DEFAULT -1,  -- cross-level driver depth (-1 = same-level)
        rcv_spec_depth INTEGER DEFAULT -1,
        drv_spec_path  TEXT,
        rcv_spec_paths TEXT                 -- JSON array
    );
    CREATE TABLE IF NOT EXISTS bundle_net (
        bundle_id TEXT REFERENCES bundle(id),
        net_id    INTEGER REFERENCES net(id),
        ord       INTEGER DEFAULT -1,  -- bit order within the bundle (v10)
        PRIMARY KEY (bundle_id, net_id)
    );
    CREATE TABLE IF NOT EXISTS bundle_busterm (
        bundle_id  TEXT REFERENCES bundle(id),
        busterm_id TEXT,
        role       TEXT DEFAULT '',   -- 'entry' | 'exit' (hier flow)
        PRIMARY KEY (bundle_id, busterm_id, role)
    );
)";

// Candidate-topology tables (schema v4). Keyed by (bundle_id, cand_index) — a
// composite key with no autoincrement, so the *.bdb.sql dump stays deterministic.
static const char* TOPOLOGY_DDL = R"(
    CREATE TABLE IF NOT EXISTS topology (
        bundle_id          TEXT REFERENCES bundle(id),
        cand_index         INTEGER,
        type               TEXT,
        wirelength         INTEGER DEFAULT 0,
        trunk_location     INTEGER DEFAULT 0,
        pass_through_count INTEGER DEFAULT 0,
        connected_blocks   TEXT,    -- JSON array of block names
        feedthru_blocks    TEXT,    -- JSON array
        is_selected        INTEGER DEFAULT 0,
        is_pinned          INTEGER DEFAULT 0,  -- pre-plan select_topology pin (v10)
        PRIMARY KEY (bundle_id, cand_index)
    );
    CREATE TABLE IF NOT EXISTS topology_segment (
        bundle_id  TEXT,
        cand_index INTEGER,
        seg_index  INTEGER,
        x1 INTEGER, y1 INTEGER, x2 INTEGER, y2 INTEGER,
        layer_hint INTEGER DEFAULT 0,
        is_jog     INTEGER DEFAULT 0,
        assigned_layer INTEGER DEFAULT -1,  -- planner's per-segment layer (-1 = unassigned)
        PRIMARY KEY (bundle_id, cand_index, seg_index),
        FOREIGN KEY (bundle_id, cand_index)
            REFERENCES topology(bundle_id, cand_index)
    );
    -- Per-segment-endpoint busterm annotation (topology.h seg_busterms): the ONE
    -- authoritative record of which segment endpoint taps which busterm.  A row is
    -- present only for a real tap; a missing (seg,endpoint) is a wire junction
    -- (nullopt).  busterm_id references a routing-time busterm row (id 'tb:<name>').
    -- This is what lets a reloaded topology restore its connectivity LOGICALLY,
    -- without re-deriving it from geometry (see single_source_topo_truth.md Ph.3).
    CREATE TABLE IF NOT EXISTS topology_seg_busterm (
        bundle_id  TEXT,
        cand_index INTEGER,
        seg_index  INTEGER,
        endpoint   TEXT,              -- 'start' | 'end'
        busterm_id TEXT REFERENCES busterm(id),
        PRIMARY KEY (bundle_id, cand_index, seg_index, endpoint),
        FOREIGN KEY (bundle_id, cand_index)
            REFERENCES topology(bundle_id, cand_index)
    );
    -- TEG-over bridge segments (Topology::bridge_segments, v11): one row per
    -- (candidate, multi-rect block) whose trunk falls in a gap between rects.
    -- Persisted so load_pipeline restores TEG-over designs losslessly.
    CREATE TABLE IF NOT EXISTS topology_bridge_segment (
        bundle_id  TEXT,
        cand_index INTEGER,
        block_name TEXT,
        x1 INTEGER, y1 INTEGER, x2 INTEGER, y2 INTEGER,
        layer_hint INTEGER DEFAULT 0,
        is_jog     INTEGER DEFAULT 0,
        PRIMARY KEY (bundle_id, cand_index, block_name),
        FOREIGN KEY (bundle_id, cand_index)
            REFERENCES topology(bundle_id, cand_index)
    );
)";

// Abstract-NUTS bus routing tables (schema v5). bundle_id is a *soft* link (no FK):
// the hier flow's expanded per-instance ids need not exist in the bundle table.
// Composite keys (no autoincrement) → deterministic *.bdb.sql dumps.
static const char* NUTS_DDL = R"(
    CREATE TABLE IF NOT EXISTS bus_segment (
        -- NOT NULL: SQLite treats a NULL child key as satisfying the FK, and PK
        -- columns are not implicitly NOT NULL in a rowid table, so without this a
        -- hand-edited dump could insert an orphan (bundle_id IS NULL) row.
        bundle_id      TEXT NOT NULL REFERENCES bundle(id),
        seg_idx        INTEGER,
        layer          INTEGER,
        is_horiz       INTEGER DEFAULT 0,
        x1 REAL, y1 REAL, x2 REAL, y2 REAL,
        track_position REAL,
        width          REAL,
        placed         INTEGER DEFAULT 0,
        is_jog         INTEGER DEFAULT 0,
        -- Stage-4 solver state (v9) for load_pipeline resume: the hard
        -- perpendicular interval and the corner-split track bounds (NULL =
        -- unbounded; infinities are not valid SQL literals in a dump).
        interval_lo    REAL,
        interval_hi    REAL,
        track_lo_bound REAL,
        track_hi_bound REAL,
        PRIMARY KEY (bundle_id, seg_idx)
    );
    CREATE TABLE IF NOT EXISTS bus_via (
        bundle_id  TEXT NOT NULL REFERENCES bundle(id),   -- NOT NULL: see bus_segment
        from_seg   INTEGER,
        to_seg     INTEGER,
        from_layer INTEGER,
        to_layer   INTEGER,
        x REAL, y REAL,
        bit_width  INTEGER,
        PRIMARY KEY (bundle_id, from_seg, to_seg)
    );
    -- Singleton fingerprint of the routed output (bus_segment + bus_via, plus
    -- net_segment + net_via after detailed NUTS), so a routing change is one
    -- reviewable line in the *.bdb.sql diff.
    CREATE TABLE IF NOT EXISTS route_snapshot (
        id             INTEGER PRIMARY KEY,   -- always 1 (current routing)
        hash           TEXT,
        n_bus_segments INTEGER DEFAULT 0,
        n_bus_vias     INTEGER DEFAULT 0,
        stage          TEXT,                  -- 'abstract_nuts' / 'detailed_nuts'
        n_net_segments INTEGER DEFAULT 0,
        n_net_vias     INTEGER DEFAULT 0
    );
)";

// Detailed-NUTS per-bit routing tables (schema v8). One net_segment row per
// bit-wire, one net_via row per per-bit layer transition (the symbolic bus_via
// fanned out per bit — same (bundle_id, from_seg, to_seg) key + bit_index).
// net_id joins the net table (resolved from the bit's net name via _ensure_net).
static const char* NET_DDL = R"(
    CREATE TABLE IF NOT EXISTS net_segment (
        -- NOT NULL: see bus_segment (a NULL child key would satisfy the FK).
        bundle_id      TEXT NOT NULL REFERENCES bundle(id),
        seg_idx        INTEGER,
        bit_index      INTEGER,
        net_id         INTEGER REFERENCES net(id),
        layer          INTEGER,
        is_horiz       INTEGER DEFAULT 0,
        x1 REAL, y1 REAL, x2 REAL, y2 REAL,
        track_position REAL,
        width          REAL,
        PRIMARY KEY (bundle_id, seg_idx, bit_index)
    );
    CREATE TABLE IF NOT EXISTS net_via (
        bundle_id  TEXT NOT NULL REFERENCES bundle(id),
        from_seg   INTEGER,
        to_seg     INTEGER,
        bit_index  INTEGER,
        net_id     INTEGER REFERENCES net(id),
        from_layer INTEGER,
        to_layer   INTEGER,
        x REAL, y REAL,
        PRIMARY KEY (bundle_id, from_seg, to_seg, bit_index)
    );
)";

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
            parent_id  TEXT REFERENCES busterm(id),
            rects      TEXT DEFAULT NULL,
            -- Routing-time busterm attributes (topology.h Busterm): the TEG-gap
            -- handling mode and the full (un-margin-shrunk) physical extent.  x1..y2
            -- above hold the possibly margin-inset bbox; these hold the original.
            teg_mode   TEXT DEFAULT 'THRU',
            orig_x1 REAL DEFAULT 0, orig_y1 REAL DEFAULT 0,
            orig_x2 REAL DEFAULT 0, orig_y2 REAL DEFAULT 0
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
        CREATE TABLE IF NOT EXISTS cell_children (
            parent_cell TEXT NOT NULL REFERENCES cell(name),
            inst_name   TEXT NOT NULL,
            child_cell  TEXT NOT NULL REFERENCES cell(name),
            x           REAL NOT NULL DEFAULT 0,
            y           REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (parent_cell, inst_name)
        );
        CREATE TABLE IF NOT EXISTS cell_pin (
            cell      TEXT NOT NULL REFERENCES cell(name),
            pin_name  TEXT NOT NULL,
            dir       TEXT NOT NULL DEFAULT 'INOUT',
            px        REAL NOT NULL DEFAULT -1,
            py        REAL NOT NULL DEFAULT -1,
            PRIMARY KEY (cell, pin_name)
        );
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    )");
    _exec(BUNDLE_DDL);     // bundle tables for a fresh DB
    _exec(TOPOLOGY_DDL);   // candidate-topology tables for a fresh DB
    _exec(NUTS_DDL);       // abstract-NUTS bus routing tables for a fresh DB
    _exec(NET_DDL);        // detailed-NUTS per-bit routing tables for a fresh DB
    _migrate();
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

// ── Schema version, migration & provenance ───────────────────────────────────

void BDB::_set_meta(const std::string& key, const std::string& value) {
    Stmt s(_db, "INSERT INTO meta(key,value) VALUES(?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value");
    sqlite3_bind_text(s, 1, key.c_str(),   -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(s, 2, value.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_step(s);
}

std::string BDB::meta_get(const std::string& key, const std::string& def) const {
    Stmt q(_db, "SELECT value FROM meta WHERE key=?");
    sqlite3_bind_text(q, 1, key.c_str(), -1, SQLITE_TRANSIENT);
    if (sqlite3_step(q) == SQLITE_ROW)
        return reinterpret_cast<const char*>(sqlite3_column_text(q, 0));
    return def;
}

int BDB::schema_version() const {
    Stmt q(_db, "PRAGMA user_version");
    return (sqlite3_step(q) == SQLITE_ROW) ? sqlite3_column_int(q, 0) : 0;
}

void BDB::_seed_provenance() {
    // schema_version mirrors PRAGMA user_version so it is visible in the diffable
    // *.bdb.sql dump (iterdump captures meta rows but not the pragma).
    _set_meta("schema_version", std::to_string(SCHEMA_VERSION));
    // Tool identifier, written once (not overwritten if already present).
    _exec("INSERT OR IGNORE INTO meta(key,value) VALUES('bdb_tool','buda-bdb')");
    // NOTE: wall-clock created/modified timestamps are intentionally NOT stored
    // here — they would make every fixture regeneration a noisy diff and defeat
    // build_fixtures.py --check. Add them later behind dump normalization.
}

void BDB::_migrate() {
    int v = schema_version();
    if (v > SCHEMA_VERSION)
        throw std::runtime_error(
            "BDB: schema version " + std::to_string(v) +
            " is newer than this build supports (max " +
            std::to_string(SCHEMA_VERSION) + "); upgrade BUDA to open this database.");
    if (v < 1) {
        // v0 (pre-versioning) -> v1: absorb the legacy busterm.rects column add
        // (idempotent).
        sqlite3_exec(_db,
            "ALTER TABLE busterm ADD COLUMN rects TEXT DEFAULT NULL",
            nullptr, nullptr, nullptr);  // Ignored if column already exists.
    }
    if (v < 2) {
        // v1 -> v2: bundle-persistence schema. The bundle tables have never had a
        // write path, so they are always empty — rebuild them to the new shape
        // (busterm role, extra hier columns) rather than a long ALTER chain.
        _exec("DROP TABLE IF EXISTS bundle_busterm;"
              "DROP TABLE IF EXISTS bundle_net;"
              "DROP TABLE IF EXISTS bundle;");
        _exec(BUNDLE_DDL);
    }
    if (v < 3) {
        // v2 -> v3: re-key bundle_net by net_id (was net_name). Preserve any
        // persisted memberships: stage the old (bundle_id, net_name) rows, rebuild
        // the table in the net_id shape, then re-insert (resolving/creating net ids
        // via add_bundle_net -> _ensure_net, which also covers flat-flow nets).
        // A fresh DB reached this step with the net_id shape already (the v<2 step
        // uses the current BUNDLE_DDL), so guard on the column actually present.
        bool has_net_name = false;
        { Stmt q(_db, "PRAGMA table_info(bundle_net)");
          while (sqlite3_step(q) == SQLITE_ROW) {
              if (std::string(reinterpret_cast<const char*>(
                      sqlite3_column_text(q, 1))) == "net_name") has_net_name = true;
          } }
        if (has_net_name) {
            std::vector<std::pair<std::string, std::string>> staged;
            { Stmt q(_db, "SELECT bundle_id, net_name FROM bundle_net");
              while (sqlite3_step(q) == SQLITE_ROW)
                  staged.emplace_back(
                      reinterpret_cast<const char*>(sqlite3_column_text(q, 0)),
                      reinterpret_cast<const char*>(sqlite3_column_text(q, 1))); }
            _exec("DROP TABLE bundle_net;");
            _exec(BUNDLE_DDL);   // recreate bundle_net in the net_id shape
            for (const auto& [bid, name] : staged)
                add_bundle_net(bid, name);
        }
    }
    if (v < 4) {
        // v3 -> v4: add the candidate-topology tables (brand new, no data to move).
        _exec(TOPOLOGY_DDL);
    }
    if (v < 5) {
        // v4 -> v5: add the abstract-NUTS bus routing tables (brand new).
        _exec(NUTS_DDL);
    }
    if (v < 6) {
        // v5 -> v6: planner's per-segment assigned layer (idempotent add).
        sqlite3_exec(_db,
            "ALTER TABLE topology_segment ADD COLUMN assigned_layer INTEGER DEFAULT -1",
            nullptr, nullptr, nullptr);  // ignored if column already exists
    }
    if (v < 7) {
        // v6 -> v7: harden bus_segment/bus_via with a real FK to bundle(id) and add
        // the route_snapshot fingerprint table. SQLite cannot add a FK via ALTER, so
        // rebuild both bus tables: rename aside, recreate via NUTS_DDL (now carrying
        // REFERENCES bundle(id)), copy back only rows whose bundle_id still resolves
        // to a bundle row (dropping any pre-FK orphans), then drop the temps. FK
        // enforcement is toggled off for the rebuild so the copy itself can't trip.
        sqlite3_exec(_db, "PRAGMA foreign_keys = OFF;", nullptr, nullptr, nullptr);
        _exec("ALTER TABLE bus_segment RENAME TO bus_segment_v6;"
              "ALTER TABLE bus_via RENAME TO bus_via_v6;");
        _exec(NUTS_DDL);   // recreates bus_segment/bus_via (with FK) + route_snapshot
        // Columns are named explicitly: NUTS_DDL creates bus_segment in its
        // CURRENT (v9) shape with extra columns a v6 table doesn't have, so a
        // bare `SELECT *` would be a column-count mismatch.
        _exec("INSERT INTO bus_segment(bundle_id,seg_idx,layer,is_horiz,"
              "x1,y1,x2,y2,track_position,width,placed,is_jog)"
              " SELECT bundle_id,seg_idx,layer,is_horiz,"
              "x1,y1,x2,y2,track_position,width,placed,is_jog"
              " FROM bus_segment_v6"
              " WHERE bundle_id IN (SELECT id FROM bundle);"
              "INSERT INTO bus_via(bundle_id,from_seg,to_seg,from_layer,"
              "to_layer,x,y,bit_width)"
              " SELECT bundle_id,from_seg,to_seg,from_layer,to_layer,x,y,bit_width"
              " FROM bus_via_v6"
              " WHERE bundle_id IN (SELECT id FROM bundle);"
              "DROP TABLE bus_segment_v6;"
              "DROP TABLE bus_via_v6;");
        sqlite3_exec(_db, "PRAGMA foreign_keys = ON;", nullptr, nullptr, nullptr);
    }
    if (v < 8) {
        // v7 -> v8: detailed-NUTS per-bit routing tables (brand new, v4/v5
        // pattern) + route_snapshot detailed-row counts. A v6-or-older DB got
        // the new route_snapshot columns from NUTS_DDL in the v<7 step, so the
        // ALTERs are error-ignored when the columns already exist (v6 pattern).
        _exec(NET_DDL);
        sqlite3_exec(_db,
            "ALTER TABLE route_snapshot ADD COLUMN n_net_segments INTEGER DEFAULT 0",
            nullptr, nullptr, nullptr);  // ignored if column already exists
        sqlite3_exec(_db,
            "ALTER TABLE route_snapshot ADD COLUMN n_net_vias INTEGER DEFAULT 0",
            nullptr, nullptr, nullptr);
    }
    if (v < 9) {
        // v8 -> v9: routing-time busterm attributes on the busterm table
        // (teg_mode + un-shrunk orig bbox) and the topology_seg_busterm join that
        // persists seg_busterms logically.  ALTER ADD COLUMN is per-column and
        // errors if the column already exists, so ignore each result (idempotent);
        // the new table is created via TOPOLOGY_DDL (all IF NOT EXISTS).
        for (const char* col : {
                "ALTER TABLE busterm ADD COLUMN teg_mode TEXT DEFAULT 'THRU'",
                "ALTER TABLE busterm ADD COLUMN orig_x1 REAL DEFAULT 0",
                "ALTER TABLE busterm ADD COLUMN orig_y1 REAL DEFAULT 0",
                "ALTER TABLE busterm ADD COLUMN orig_x2 REAL DEFAULT 0",
                "ALTER TABLE busterm ADD COLUMN orig_y2 REAL DEFAULT 0" })
            sqlite3_exec(_db, col, nullptr, nullptr, nullptr);
        _exec(TOPOLOGY_DDL);   // adds topology_seg_busterm (topology/_segment no-op)
    }
    if (v < 10) {
        // v9 -> v10: load_pipeline resume support (idempotent adds; a fresh or
        // v6-or-older DB gets the bus_segment columns from NUTS_DDL directly).
        // - bus_segment stage-4 solver state (interval + corner-split bounds);
        // - bundle_net.ord: bit order of the bundle's nets (bundle_nets() was
        //   name-ordered, which breaks bit_index -> net mapping at >= 10 bits);
        // - topology.is_pinned: a pre-plan select_topology pin survives a
        //   checkpoint, so a resumed run_planner still honors it.
        for (const char* col : {
                "ALTER TABLE bus_segment ADD COLUMN interval_lo REAL",
                "ALTER TABLE bus_segment ADD COLUMN interval_hi REAL",
                "ALTER TABLE bus_segment ADD COLUMN track_lo_bound REAL",
                "ALTER TABLE bus_segment ADD COLUMN track_hi_bound REAL",
                "ALTER TABLE bundle_net ADD COLUMN ord INTEGER DEFAULT -1",
                "ALTER TABLE topology ADD COLUMN is_pinned INTEGER DEFAULT 0" })
            sqlite3_exec(_db, col, nullptr, nullptr, nullptr);
    }
    if (v < 11) {
        // v10 -> v11: TEG-over bridge segments (brand new table, created via
        // TOPOLOGY_DDL — all IF NOT EXISTS, so this is a no-op for the rest).
        _exec(TOPOLOGY_DDL);
    }
    if (v < SCHEMA_VERSION) {
        // Refresh provenance (incl. the meta.schema_version mirror) on EVERY
        // forward migration, not just the initial v0 seed — otherwise an upgraded
        // DB would keep a stale meta.schema_version while PRAGMA user_version moved.
        _seed_provenance();
        _exec(("PRAGMA user_version = " + std::to_string(SCHEMA_VERSION) + ";").c_str());
    }
}

// ── Cell-level pins ──────────────────────────────────────────────────────────

void BDB::add_cell_pin(const std::string& cell, const std::string& pin_name,
                       const std::string& dir, double px, double py) {
    Stmt s(_db,
        "INSERT INTO cell_pin(cell,pin_name,dir,px,py) VALUES(?,?,?,?,?)"
        " ON CONFLICT(cell,pin_name) DO UPDATE SET"
        "   dir=excluded.dir, px=excluded.px, py=excluded.py");
    sqlite3_bind_text  (s, 1, cell.c_str(),     -1, SQLITE_TRANSIENT);
    sqlite3_bind_text  (s, 2, pin_name.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text  (s, 3, dir.c_str(),      -1, SQLITE_TRANSIENT);
    sqlite3_bind_double(s, 4, px);
    sqlite3_bind_double(s, 5, py);
    sqlite3_step(s);
}

std::vector<CellPinRow> BDB::all_cell_pins() const {
    Stmt q(_db, "SELECT cell, pin_name, dir, px, py FROM cell_pin ORDER BY cell, pin_name");
    std::vector<CellPinRow> out;
    while (sqlite3_step(q) == SQLITE_ROW) {
        CellPinRow r;
        r.cell     = (const char*)sqlite3_column_text(q, 0);
        r.pin_name = (const char*)sqlite3_column_text(q, 1);
        r.dir      = (const char*)sqlite3_column_text(q, 2);
        r.px       = sqlite3_column_double(q, 3);
        r.py       = sqlite3_column_double(q, 4);
        out.push_back(r);
    }
    return out;
}

int BDB::infer_pin_dirs_from_cell_pins() {
    Stmt u(_db, R"(
        UPDATE pin
        SET dir = (
            SELECT cp.dir
            FROM component c
            JOIN cell_pin cp
              ON cp.cell = c.cell AND cp.pin_name = pin.pin_name
            WHERE c.id = pin.comp_id
              AND cp.dir IN ('INPUT','OUTPUT','INOUT')
        )
        WHERE dir = 'UNKNOWN'
          AND EXISTS (
            SELECT 1
            FROM component c
            JOIN cell_pin cp
              ON cp.cell = c.cell AND cp.pin_name = pin.pin_name
            WHERE c.id = pin.comp_id
              AND cp.dir IN ('INPUT','OUTPUT','INOUT')
          )
    )");
    if (sqlite3_step(u) != SQLITE_DONE)
        throw std::runtime_error("infer_pin_dirs_from_cell_pins: update failed");
    return sqlite3_changes(_db);
}

// Insert a pin for net_id at the component named inst_path, with pin_name and dir.
// Absolute pin position is derived from cell_pin relative coords when available,
// otherwise defaults to the component's centroid.
// Also auto-registers the port on the cell type (INSERT OR IGNORE in cell_pin).
void BDB::_add_pin_by_path(int net_id, const std::string& inst_path,
                            const std::string& pin_name, const std::string& dir) {
    Stmt q(_db, "SELECT id, cell, x1, y1, x2, y2 FROM component WHERE name=?");
    sqlite3_bind_text(q, 1, inst_path.c_str(), -1, SQLITE_TRANSIENT);
    if (sqlite3_step(q) != SQLITE_ROW) return;  // component not in DB; skip silently
    int comp_id = sqlite3_column_int(q, 0);
    std::string cell_name = (const char*)sqlite3_column_text(q, 1);
    double x1 = sqlite3_column_double(q, 2), y1 = sqlite3_column_double(q, 3);
    double x2 = sqlite3_column_double(q, 4), y2 = sqlite3_column_double(q, 5);

    // Derive absolute pin position from cell_pin offset when available.
    double px = (x1 + x2) / 2.0, py = (y1 + y2) / 2.0;
    if (!cell_name.empty()) {
        Stmt cp(_db, "SELECT px, py FROM cell_pin WHERE cell=? AND pin_name=?");
        sqlite3_bind_text(cp, 1, cell_name.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(cp, 2, pin_name.c_str(),  -1, SQLITE_TRANSIENT);
        if (sqlite3_step(cp) == SQLITE_ROW) {
            double rx = sqlite3_column_double(cp, 0);
            double ry = sqlite3_column_double(cp, 1);
            if (rx >= 0) px = x1 + rx;
            if (ry >= 0) py = y1 + ry;
        }
    }

    // Insert instance-level pin (idempotent).
    Stmt ins(_db,
        "INSERT OR IGNORE INTO pin(net_id,comp_id,pin_name,dir,px,py) VALUES(?,?,?,?,?,?)");
    sqlite3_bind_int   (ins, 1, net_id);
    sqlite3_bind_int   (ins, 2, comp_id);
    sqlite3_bind_text  (ins, 3, pin_name.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text  (ins, 4, dir.c_str(),      -1, SQLITE_TRANSIENT);
    sqlite3_bind_double(ins, 5, px);
    sqlite3_bind_double(ins, 6, py);
    sqlite3_step(ins);

    // Auto-register the port on the cell type without overwriting user-defined entries.
    if (!cell_name.empty()) {
        Stmt cp(_db,
            "INSERT OR IGNORE INTO cell_pin(cell,pin_name,dir,px,py) VALUES(?,?,?,-1,-1)");
        sqlite3_bind_text(cp, 1, cell_name.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(cp, 2, pin_name.c_str(),  -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(cp, 3, dir.c_str(),       -1, SQLITE_TRANSIENT);
        sqlite3_step(cp);
    }
}

int BDB::add_net_pins(const std::string& net_name,
                      const std::string& drv,
                      const std::vector<std::string>& rcvs) {
    // Insert net (idempotent).
    Stmt ins_net(_db, "INSERT OR IGNORE INTO net(name) VALUES(?)");
    sqlite3_bind_text(ins_net, 1, net_name.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_step(ins_net);

    Stmt q_net(_db, "SELECT id FROM net WHERE name=?");
    sqlite3_bind_text(q_net, 1, net_name.c_str(), -1, SQLITE_TRANSIENT);
    if (sqlite3_step(q_net) != SQLITE_ROW)
        throw std::runtime_error("add_net_pins: failed to insert net " + net_name);
    int net_id = sqlite3_column_int(q_net, 0);

    // Parse "inst/path.pin_name" into (path, pin_name, dir).
    struct Ep { std::string path, pin, dir; };
    auto parse = [](const std::string& s, const std::string& d) -> Ep {
        auto dot = s.rfind('.');
        if (dot == std::string::npos) return {s, s, d};
        return {s.substr(0, dot), s.substr(dot + 1), d};
    };

    std::vector<Ep> eps;
    eps.push_back(parse(drv, "OUTPUT"));
    for (const auto& r : rcvs) eps.push_back(parse(r, "INPUT"));

    // Split a path into '/' segments.
    auto split = [](const std::string& p) {
        std::vector<std::string> v;
        std::string seg;
        for (char c : p) {
            if (c == '/') { if (!seg.empty()) { v.push_back(seg); seg.clear(); } }
            else seg += c;
        }
        if (!seg.empty()) v.push_back(seg);
        return v;
    };

    std::vector<std::vector<std::string>> all_segs;
    for (const auto& ep : eps) all_segs.push_back(split(ep.path));

    // Longest common prefix of all endpoint paths.
    size_t common = all_segs[0].size();
    for (size_t i = 1; i < all_segs.size(); ++i) {
        size_t k = 0;
        while (k < common && k < all_segs[i].size() &&
               all_segs[0][k] == all_segs[i][k]) ++k;
        common = k;
    }

    // Add leaf pin + interface pins at each ancestor strictly above the leaf
    // and strictly below the common ancestor.
    for (size_t i = 0; i < eps.size(); ++i) {
        const auto& ep = eps[i];
        const auto& seg = all_segs[i];

        // Leaf: explicit pin_name with declared direction.
        _add_pin_by_path(net_id, ep.path, ep.pin, ep.dir);

        // Intermediate ancestors: use net_name as the interface pin_name.
        // Direction: OUTPUT if on the driver side, INPUT if on a receiver side.
        for (size_t d = seg.size() - 1; d > common; --d) {
            std::string anc;
            for (size_t k = 0; k < d; ++k) {
                if (k > 0) anc += '/';
                anc += seg[k];
            }
            _add_pin_by_path(net_id, anc, net_name, ep.dir);
        }
    }

    return net_id;
}

int BDB::add_net_pins_undirected(const std::string& net_name,
                                  const std::vector<std::string>& pins) {
    Stmt ins_net(_db, "INSERT OR IGNORE INTO net(name) VALUES(?)");
    sqlite3_bind_text(ins_net, 1, net_name.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_step(ins_net);

    Stmt q_net(_db, "SELECT id FROM net WHERE name=?");
    sqlite3_bind_text(q_net, 1, net_name.c_str(), -1, SQLITE_TRANSIENT);
    if (sqlite3_step(q_net) != SQLITE_ROW)
        throw std::runtime_error("add_net_pins_undirected: failed to insert net " + net_name);
    int net_id = sqlite3_column_int(q_net, 0);

    auto split = [](const std::string& p) {
        std::vector<std::string> v;
        std::string seg;
        for (char c : p) {
            if (c == '/') { if (!seg.empty()) { v.push_back(seg); seg.clear(); } }
            else seg += c;
        }
        if (!seg.empty()) v.push_back(seg);
        return v;
    };

    struct Ep { std::string path, pin; };
    auto parse = [](const std::string& s) -> Ep {
        auto dot = s.rfind('.');
        if (dot == std::string::npos) return {s, s};
        return {s.substr(0, dot), s.substr(dot + 1)};
    };

    std::vector<Ep> eps;
    std::vector<std::vector<std::string>> all_segs;
    for (const auto& p : pins) {
        eps.push_back(parse(p));
        all_segs.push_back(split(eps.back().path));
    }

    // Longest common prefix of all endpoint paths.
    size_t common = all_segs.empty() ? 0 : all_segs[0].size();
    for (size_t i = 1; i < all_segs.size(); ++i) {
        size_t k = 0;
        while (k < common && k < all_segs[i].size() &&
               all_segs[0][k] == all_segs[i][k]) ++k;
        common = k;
    }

    for (size_t i = 0; i < eps.size(); ++i) {
        const auto& ep  = eps[i];
        const auto& seg = all_segs[i];
        _add_pin_by_path(net_id, ep.path, ep.pin, "UNKNOWN");
        for (size_t d = seg.size() - 1; d > common; --d) {
            std::string anc;
            for (size_t k = 0; k < d; ++k) {
                if (k > 0) anc += '/';
                anc += seg[k];
            }
            _add_pin_by_path(net_id, anc, net_name, "UNKNOWN");
        }
    }

    return net_id;
}

int BDB::add_net_pins_inout(const std::string& net_name,
                             const std::vector<std::string>& pins) {
    Stmt ins_net(_db, "INSERT OR IGNORE INTO net(name) VALUES(?)");
    sqlite3_bind_text(ins_net, 1, net_name.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_step(ins_net);

    Stmt q_net(_db, "SELECT id FROM net WHERE name=?");
    sqlite3_bind_text(q_net, 1, net_name.c_str(), -1, SQLITE_TRANSIENT);
    if (sqlite3_step(q_net) != SQLITE_ROW)
        throw std::runtime_error("add_net_pins_inout: failed to insert net " + net_name);
    int net_id = sqlite3_column_int(q_net, 0);

    auto split = [](const std::string& p) {
        std::vector<std::string> v;
        std::string seg;
        for (char c : p) {
            if (c == '/') { if (!seg.empty()) { v.push_back(seg); seg.clear(); } }
            else seg += c;
        }
        if (!seg.empty()) v.push_back(seg);
        return v;
    };

    struct Ep { std::string path, pin; };
    auto parse = [](const std::string& s) -> Ep {
        auto dot = s.rfind('.');
        if (dot == std::string::npos) return {s, s};
        return {s.substr(0, dot), s.substr(dot + 1)};
    };

    std::vector<Ep> eps;
    std::vector<std::vector<std::string>> all_segs;
    for (const auto& p : pins) {
        eps.push_back(parse(p));
        all_segs.push_back(split(eps.back().path));
    }

    size_t common = all_segs.empty() ? 0 : all_segs[0].size();
    for (size_t i = 1; i < all_segs.size(); ++i) {
        size_t k = 0;
        while (k < common && k < all_segs[i].size() &&
               all_segs[0][k] == all_segs[i][k]) ++k;
        common = k;
    }

    for (size_t i = 0; i < eps.size(); ++i) {
        const auto& ep  = eps[i];
        const auto& seg = all_segs[i];
        _add_pin_by_path(net_id, ep.path, ep.pin, "INOUT");
        for (size_t d = seg.size() - 1; d > common; --d) {
            std::string anc;
            for (size_t k = 0; k < d; ++k) {
                if (k > 0) anc += '/';
                anc += seg[k];
            }
            _add_pin_by_path(net_id, anc, net_name, "INOUT");
        }
    }

    return net_id;
}

int BDB::units() const { return _units; }

void BDB::set_die(double w, double h) {
    _die_w = w; _die_h = h;
    auto upsert = [&](const char* key, double val) {
        std::string sql = "INSERT INTO meta(key,value) VALUES('" + std::string(key) +
                          "','" + std::to_string(val) +
                          "') ON CONFLICT(key) DO UPDATE SET value=excluded.value";
        _exec(sql.c_str());
    };
    upsert("die_w", w);
    upsert("die_h", h);
}

static double _union_bbox_dim(sqlite3* db, bool want_w) {
    const char* sql = want_w
        ? "SELECT MAX(x2) FROM component WHERE x1 >= 0"
        : "SELECT MAX(y2) FROM component WHERE y1 >= 0";
    sqlite3_stmt* s = nullptr;
    if (sqlite3_prepare_v2(db, sql, -1, &s, nullptr) != SQLITE_OK) return 0.0;
    double v = 0.0;
    if (sqlite3_step(s) == SQLITE_ROW && sqlite3_column_type(s, 0) != SQLITE_NULL)
        v = sqlite3_column_double(s, 0);
    sqlite3_finalize(s);
    return v;
}

double BDB::die_w() const {
    if (_die_w > 0.0) return _die_w;
    return _union_bbox_dim(_db, true);
}
double BDB::die_h() const {
    if (_die_h > 0.0) return _die_h;
    return _union_bbox_dim(_db, false);
}

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
              AND  pin.dir IN ('INPUT', 'INOUT')
        )
    )");
}

void BDB::compute_all() { compute_hpwl(); compute_fanout(); }

// ── Bulk queries ─────────────────────────────────────────────────────────────

std::vector<ComponentRow> BDB::all_components() const {
    if (!_q_all_components)
        sqlite3_prepare_v2(_db,
            "SELECT id,name,cell,COALESCE(parent_id,-1),depth,x1,y1,x2,y2,is_leaf,is_replicated"
            " FROM component ORDER BY id",
            -1, &_q_all_components, nullptr);
    sqlite3_reset(_q_all_components);
    std::vector<ComponentRow> rows;
    while (sqlite3_step(_q_all_components) == SQLITE_ROW) {
        auto q = _q_all_components;
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
    if (!_q_all_nets)
        sqlite3_prepare_v2(_db, "SELECT id,name FROM net ORDER BY name",
                           -1, &_q_all_nets, nullptr);
    sqlite3_reset(_q_all_nets);
    std::vector<NetRow> rows;
    while (sqlite3_step(_q_all_nets) == SQLITE_ROW)
        rows.push_back({sqlite3_column_int(_q_all_nets, 0),
                        (const char*)sqlite3_column_text(_q_all_nets, 1)});
    return rows;
}

std::vector<PinRow> BDB::all_pins() const {
    if (!_q_all_pins)
        sqlite3_prepare_v2(_db,
            "SELECT p.net_id, p.comp_id, p.pin_name, p.dir, p.px, p.py FROM pin p",
            -1, &_q_all_pins, nullptr);
    sqlite3_reset(_q_all_pins);
    std::vector<PinRow> rows;
    while (sqlite3_step(_q_all_pins) == SQLITE_ROW) {
        auto q = _q_all_pins;
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
    struct VMod {
        std::vector<VInst> insts;
        std::unordered_map<std::string, std::string> port_dirs;
    };
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

        auto clean_port_name = [](std::string s) {
            s = std::regex_replace(s, std::regex(R"(\[[^\]]+\])"), " ");
            s = std::regex_replace(s, std::regex(R"([,;()])"), " ");
            auto tok = split_ws(s);
            if (tok.empty()) return std::string();
            std::string name = tok.back();
            if (name == "wire" || name == "reg" || name == "logic" ||
                name == "signed" || name == "unsigned")
                return std::string();
            if (!name.empty() && name[0] == '\\') name.erase(name.begin());
            return name;
        };

        auto parse_port_dirs = [&](const std::string& text) {
            if (cur_mod.empty()) return;
            static const std::regex decl_re(R"(\b(input|output|inout)\b([^;)]*))");
            auto begin = std::sregex_iterator(text.begin(), text.end(), decl_re);
            auto end = std::sregex_iterator();
            for (auto it = begin; it != end; ++it) {
                std::string dir = (*it)[1].str();
                std::transform(dir.begin(), dir.end(), dir.begin(),
                               [](unsigned char c){ return std::toupper(c); });
                std::string rest = (*it)[2].str();
                rest = std::regex_replace(rest, std::regex(R"(\[[^\]]+\])"), " ");
                rest = std::regex_replace(rest, std::regex(R"(\b(wire|reg|logic|signed|unsigned)\b)"), " ");
                std::stringstream ss(rest);
                std::string item;
                while (std::getline(ss, item, ',')) {
                    std::string name = clean_port_name(item);
                    if (!name.empty())
                        mod_lib[cur_mod].port_dirs[name] = dir;
                }
            }
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
                    parse_port_dirs(line);
                    if (line.find(';') != std::string::npos) in_body = true;
                    else in_header = true;
                }
                continue;
            }
            if (in_header) {
                parse_port_dirs(line);
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
            if (tok.empty()) continue;
            if (tok[0] == "input" || tok[0] == "output" || tok[0] == "inout") {
                parse_port_dirs(line);
                continue;
            }
            if (kws.count(tok[0])) continue;
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
    Stmt s_cell(_db,
        "INSERT OR IGNORE INTO cell(name,width,height) VALUES(?,0,0)");
    Stmt s_cell_pin(_db, R"(
        INSERT INTO cell_pin(cell,pin_name,dir,px,py)
        VALUES(?,?,?,-1,-1)
        ON CONFLICT(cell,pin_name) DO UPDATE SET dir=excluded.dir
    )");

    _exec("BEGIN");

    for (const auto& [cell_name, mod] : mod_lib) {
        sqlite3_bind_text(s_cell, 1, cell_name.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_step(s_cell); sqlite3_reset(s_cell);
        for (const auto& [pin_name, dir] : mod.port_dirs) {
            sqlite3_bind_text(s_cell_pin, 1, cell_name.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(s_cell_pin, 2, pin_name.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(s_cell_pin, 3, dir.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_step(s_cell_pin); sqlite3_reset(s_cell_pin);
        }
    }

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

void BDB::set_comp_is_leaf(const std::string& name, bool is_leaf) {
    Stmt u(_db, "UPDATE component SET is_leaf=? WHERE name=?");
    sqlite3_bind_int (u, 1, is_leaf ? 1 : 0);
    sqlite3_bind_text(u, 2, name.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_step(u);
}

void BDB::set_comp_bbox(const std::string& name,
                        double x1, double y1, double x2, double y2) {
    Stmt q(_db, "SELECT id FROM component WHERE name=?");
    sqlite3_bind_text(q, 1, name.c_str(), -1, SQLITE_TRANSIENT);
    if (sqlite3_step(q) != SQLITE_ROW)
        throw std::runtime_error("set_comp_bbox: not found: " + name);
    Stmt u(_db, "UPDATE component SET x1=?, y1=?, x2=?, y2=? WHERE name=?");
    sqlite3_bind_double(u, 1, x1);
    sqlite3_bind_double(u, 2, y1);
    sqlite3_bind_double(u, 3, x2);
    sqlite3_bind_double(u, 4, y2);
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

void BDB::set_comp_cell(const std::string& comp_name, const std::string& new_cell) {
    Stmt u(_db, "UPDATE component SET cell=? WHERE name=?");
    sqlite3_bind_text(u, 1, new_cell.c_str(),  -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(u, 2, comp_name.c_str(), -1, SQLITE_TRANSIENT);
    if (sqlite3_step(u) != SQLITE_DONE)
        throw std::runtime_error("set_comp_cell: update failed: " + comp_name);
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
    _expand_cell_children(id, inst_name, cell_name, abs_x, abs_y, depth + 1);
    compute_hpwl();
    return id;
}

void BDB::add_inst_to_cell(const std::string& parent_cell,
                            const std::string& inst_name,
                            const std::string& child_cell,
                            double x, double y) {
    Stmt qp(_db, "SELECT 1 FROM cell WHERE name=?");
    sqlite3_bind_text(qp, 1, parent_cell.c_str(), -1, SQLITE_TRANSIENT);
    if (sqlite3_step(qp) != SQLITE_ROW)
        throw std::runtime_error("add_inst_to_cell: parent cell not defined: " + parent_cell);

    Stmt qc(_db, "SELECT 1 FROM cell WHERE name=?");
    sqlite3_bind_text(qc, 1, child_cell.c_str(), -1, SQLITE_TRANSIENT);
    if (sqlite3_step(qc) != SQLITE_ROW)
        throw std::runtime_error("add_inst_to_cell: child cell not defined: " + child_cell);

    Stmt ins(_db, R"(
        INSERT OR REPLACE INTO cell_children(parent_cell,inst_name,child_cell,x,y)
        VALUES(?,?,?,?,?)
    )");
    sqlite3_bind_text  (ins, 1, parent_cell.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text  (ins, 2, inst_name.c_str(),   -1, SQLITE_TRANSIENT);
    sqlite3_bind_text  (ins, 3, child_cell.c_str(),  -1, SQLITE_TRANSIENT);
    sqlite3_bind_double(ins, 4, x);
    sqlite3_bind_double(ins, 5, y);
    sqlite3_step(ins);
}

void BDB::_expand_cell_children(int parent_comp_id,
                                  const std::string& parent_comp_name,
                                  const std::string& cell_name,
                                  double abs_x, double abs_y,
                                  int child_depth) {
    // Collect cell_children rows (avoid nested prepared statements sharing the db handle)
    struct ChildDef { std::string inst_name, child_cell; double x, y; };
    std::vector<ChildDef> children;
    {
        Stmt q(_db, "SELECT inst_name,child_cell,x,y FROM cell_children WHERE parent_cell=?");
        sqlite3_bind_text(q, 1, cell_name.c_str(), -1, SQLITE_TRANSIENT);
        while (sqlite3_step(q) == SQLITE_ROW) {
            children.push_back({
                (const char*)sqlite3_column_text(q, 0),
                (const char*)sqlite3_column_text(q, 1),
                sqlite3_column_double(q, 2),
                sqlite3_column_double(q, 3)
            });
        }
    }
    if (children.empty()) return;

    // Parent has children → mark non-leaf
    {
        Stmt ul(_db, "UPDATE component SET is_leaf=0 WHERE id=?");
        sqlite3_bind_int(ul, 1, parent_comp_id);
        sqlite3_step(ul);
    }

    for (const auto& ch : children) {
        // Look up child cell size
        double cw = 0, ch_h = 0;
        {
            Stmt qsz(_db, "SELECT width,height FROM cell WHERE name=?");
            sqlite3_bind_text(qsz, 1, ch.child_cell.c_str(), -1, SQLITE_TRANSIENT);
            if (sqlite3_step(qsz) != SQLITE_ROW) continue; // unknown cell — skip
            cw   = sqlite3_column_double(qsz, 0);
            ch_h = sqlite3_column_double(qsz, 1);
        }

        double cx = abs_x + ch.x;
        double cy = abs_y + ch.y;
        std::string child_path = parent_comp_name + "/" + ch.inst_name;

        {
            Stmt ins(_db, R"(
                INSERT OR IGNORE INTO component
                    (name,cell,parent_id,depth,x1,y1,x2,y2,is_leaf)
                VALUES(?,?,?,?,?,?,?,?,1)
            )");
            sqlite3_bind_text  (ins, 1, child_path.c_str(),   -1, SQLITE_TRANSIENT);
            sqlite3_bind_text  (ins, 2, ch.child_cell.c_str(),-1, SQLITE_TRANSIENT);
            sqlite3_bind_int   (ins, 3, parent_comp_id);
            sqlite3_bind_int   (ins, 4, child_depth);
            sqlite3_bind_double(ins, 5, cx);
            sqlite3_bind_double(ins, 6, cy);
            sqlite3_bind_double(ins, 7, cx + cw);
            sqlite3_bind_double(ins, 8, cy + ch_h);
            sqlite3_step(ins);
        }

        // Retrieve id (may have been inserted just now or already existed)
        int child_id = -1;
        {
            Stmt qid(_db, "SELECT id FROM component WHERE name=?");
            sqlite3_bind_text(qid, 1, child_path.c_str(), -1, SQLITE_TRANSIENT);
            if (sqlite3_step(qid) == SQLITE_ROW)
                child_id = sqlite3_column_int(qid, 0);
        }

        if (child_id >= 0)
            _expand_cell_children(child_id, child_path, ch.child_cell,
                                   cx, cy, child_depth + 1);
    }
}

// ── flip_comp / rotate_comp ────────────────────────────────────────────────

// Internal: get root bbox, then collect all descendants via recursive CTE.
// Returns root in rows[0], descendants in rows[1..].
namespace {
struct BBoxRow { int id; double x1,y1,x2,y2; };

void gather_subtree(sqlite3* db, const std::string& name,
                    std::vector<BBoxRow>& rows) {
    // Root
    {
        Stmt q(db, "SELECT id,x1,y1,x2,y2 FROM component WHERE name=?");
        sqlite3_bind_text(q, 1, name.c_str(), -1, SQLITE_TRANSIENT);
        if (sqlite3_step(q) != SQLITE_ROW)
            throw std::runtime_error("component not found: " + name);
        rows.push_back({sqlite3_column_int(q,0),
                        sqlite3_column_double(q,1), sqlite3_column_double(q,2),
                        sqlite3_column_double(q,3), sqlite3_column_double(q,4)});
    }
    // Descendants (exclude root; start CTE from root's children)
    {
        Stmt q(db, R"(
            WITH RECURSIVE sub(id,x1,y1,x2,y2) AS (
                SELECT id,x1,y1,x2,y2 FROM component WHERE parent_id=?
                UNION ALL
                SELECT c.id,c.x1,c.y1,c.x2,c.y2
                FROM component c JOIN sub ON c.parent_id=sub.id
            )
            SELECT id,x1,y1,x2,y2 FROM sub
        )");
        sqlite3_bind_int(q, 1, rows[0].id);
        while (sqlite3_step(q) == SQLITE_ROW)
            rows.push_back({sqlite3_column_int(q,0),
                            sqlite3_column_double(q,1), sqlite3_column_double(q,2),
                            sqlite3_column_double(q,3), sqlite3_column_double(q,4)});
    }
}
} // anonymous namespace

void BDB::flip_comp(const std::string& name, bool flip_x) {
    std::vector<BBoxRow> rows;
    gather_subtree(_db, name, rows);

    const auto& root = rows[0];
    Stmt upd(_db, "UPDATE component SET x1=?,y1=?,x2=?,y2=? WHERE id=?");

    // Root bbox is unchanged for flip (rectangular → symmetric about centre).
    // Transform each descendant's absolute coords.
    for (size_t i = 1; i < rows.size(); ++i) {
        const auto& d = rows[i];
        double nx1=d.x1, ny1=d.y1, nx2=d.x2, ny2=d.y2;
        if (flip_x) {
            // Mirror about vertical centre: x' = root.x1 + root.x2 − x
            nx1 = root.x1 + root.x2 - d.x2;
            nx2 = root.x1 + root.x2 - d.x1;
        } else {
            // Mirror about horizontal centre: y' = root.y1 + root.y2 − y
            ny1 = root.y1 + root.y2 - d.y2;
            ny2 = root.y1 + root.y2 - d.y1;
        }
        sqlite3_reset(upd);
        sqlite3_bind_double(upd,1,nx1); sqlite3_bind_double(upd,2,ny1);
        sqlite3_bind_double(upd,3,nx2); sqlite3_bind_double(upd,4,ny2);
        sqlite3_bind_int   (upd,5,d.id);
        sqlite3_step(upd);
    }
    compute_hpwl();
}

void BDB::rotate_comp(const std::string& name, int degrees) {
    if (degrees != 90 && degrees != 180 && degrees != 270)
        throw std::runtime_error("rotate_comp: degrees must be 90, 180, or 270");

    std::vector<BBoxRow> rows;
    gather_subtree(_db, name, rows);

    const auto& root = rows[0];
    double rx1=root.x1, ry1=root.y1;
    double W=root.x2-rx1, H=root.y2-ry1;

    Stmt upd(_db, "UPDATE component SET x1=?,y1=?,x2=?,y2=? WHERE id=?");
    auto do_upd = [&](int id, double nx1,double ny1,double nx2,double ny2) {
        sqlite3_reset(upd);
        sqlite3_bind_double(upd,1,nx1); sqlite3_bind_double(upd,2,ny1);
        sqlite3_bind_double(upd,3,nx2); sqlite3_bind_double(upd,4,ny2);
        sqlite3_bind_int   (upd,5,id);
        sqlite3_step(upd);
    };

    // Root: swap width/height for 90/270, unchanged for 180
    if (degrees == 90 || degrees == 270)
        do_upd(root.id, rx1, ry1, rx1+H, ry1+W);

    // Descendants: transform relative to original root lower-left
    for (size_t i = 1; i < rows.size(); ++i) {
        const auto& d = rows[i];
        double crx = d.x1-rx1, cry = d.y1-ry1;   // relative lower-left
        double cw  = d.x2-d.x1, ch  = d.y2-d.y1; // child size

        double nx1, ny1, nx2, ny2;
        if (degrees == 90) {
            // CCW 90°: new_rel = (H−cry−ch, crx); child size swaps to ch×cw
            nx1 = rx1 + (H - cry - ch);  ny1 = ry1 + crx;
            nx2 = nx1 + ch;               ny2 = ny1 + cw;
        } else if (degrees == 270) {
            // CW 90°: new_rel = (cry, W−crx−cw); child size swaps to ch×cw
            nx1 = rx1 + cry;              ny1 = ry1 + (W - crx - cw);
            nx2 = nx1 + ch;               ny2 = ny1 + cw;
        } else {
            // 180°: reflect both axes
            nx1 = rx1 + (W - crx - cw);  ny1 = ry1 + (H - cry - ch);
            nx2 = nx1 + cw;               ny2 = ny1 + ch;
        }
        do_upd(d.id, nx1, ny1, nx2, ny2);
    }
    compute_hpwl();
}

void BDB::add_busterm(const BustermRow& bt) {
    Stmt s(_db,
        "INSERT OR REPLACE INTO busterm(id,comp_id,hier_path,depth,x1,y1,x2,y2,"
        "resolution,parent_id,rects,teg_mode,orig_x1,orig_y1,orig_x2,orig_y2)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)");
    sqlite3_bind_text  (s, 1, bt.id.c_str(),         -1, SQLITE_TRANSIENT);
    // comp_id is a FK to component(id); routing-time busterms have no component,
    // so bind NULL (which satisfies the FK) rather than an invalid -1.
    if (bt.comp_id < 0)
        sqlite3_bind_null(s, 2);
    else
        sqlite3_bind_int(s, 2, bt.comp_id);
    sqlite3_bind_text  (s, 3, bt.hier_path.c_str(),  -1, SQLITE_TRANSIENT);
    sqlite3_bind_int   (s, 4, bt.depth);
    sqlite3_bind_double(s, 5, bt.x1);
    sqlite3_bind_double(s, 6, bt.y1);
    sqlite3_bind_double(s, 7, bt.x2);
    sqlite3_bind_double(s, 8, bt.y2);
    sqlite3_bind_text  (s, 9, bt.resolution.c_str(), -1, SQLITE_TRANSIENT);
    if (bt.parent_id.empty())
        sqlite3_bind_null(s, 10);
    else
        sqlite3_bind_text(s, 10, bt.parent_id.c_str(), -1, SQLITE_TRANSIENT);
    if (bt.rects.empty())
        sqlite3_bind_null(s, 11);
    else
        sqlite3_bind_text(s, 11, bt.rects.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text  (s, 12, bt.teg_mode.c_str(),  -1, SQLITE_TRANSIENT);
    sqlite3_bind_double(s, 13, bt.orig_x1);
    sqlite3_bind_double(s, 14, bt.orig_y1);
    sqlite3_bind_double(s, 15, bt.orig_x2);
    sqlite3_bind_double(s, 16, bt.orig_y2);
    sqlite3_step(s);
}

void BDB::clear_busterms() {
    _exec("DELETE FROM busterm");
}

std::vector<ComponentRow> BDB::components_at_depth(int depth) const {
    if (!_q_components_at_depth)
        sqlite3_prepare_v2(_db,
            "SELECT id,name,cell,COALESCE(parent_id,-1),depth,x1,y1,x2,y2,is_leaf,is_replicated"
            " FROM component WHERE depth=? ORDER BY id",
            -1, &_q_components_at_depth, nullptr);
    sqlite3_reset(_q_components_at_depth);
    sqlite3_bind_int(_q_components_at_depth, 1, depth);
    std::vector<ComponentRow> rows;
    while (sqlite3_step(_q_components_at_depth) == SQLITE_ROW) {
        auto q = _q_components_at_depth;
        ComponentRow r;
        r.id           = sqlite3_column_int(q, 0);
        r.name         = (const char*)sqlite3_column_text(q, 1);
        r.cell         = (const char*)sqlite3_column_text(q, 2);
        r.parent_id    = sqlite3_column_int(q, 3);
        r.depth        = sqlite3_column_int(q, 4);
        r.x1           = sqlite3_column_double(q, 5);
        r.y1           = sqlite3_column_double(q, 6);
        r.x2           = sqlite3_column_double(q, 7);
        r.y2           = sqlite3_column_double(q, 8);
        r.is_leaf      = sqlite3_column_int(q, 9);
        r.is_replicated= sqlite3_column_int(q, 10);
        rows.push_back(r);
    }
    return rows;
}

std::vector<PinRow> BDB::pins_by_comp(int comp_id) const {
    if (!_q_pins_by_comp)
        sqlite3_prepare_v2(_db,
            "SELECT net_id,comp_id,pin_name,dir,px,py FROM pin WHERE comp_id=?",
            -1, &_q_pins_by_comp, nullptr);
    sqlite3_reset(_q_pins_by_comp);
    sqlite3_bind_int(_q_pins_by_comp, 1, comp_id);
    std::vector<PinRow> rows;
    while (sqlite3_step(_q_pins_by_comp) == SQLITE_ROW) {
        auto q = _q_pins_by_comp;
        PinRow r;
        r.net_id   = sqlite3_column_int(q, 0);
        r.comp_id  = sqlite3_column_int(q, 1);
        r.pin_name = (const char*)sqlite3_column_text(q, 2);
        r.dir      = (const char*)sqlite3_column_text(q, 3);
        r.px       = sqlite3_column_double(q, 4);
        r.py       = sqlite3_column_double(q, 5);
        rows.push_back(r);
    }
    return rows;
}

// Column list shared by all_busterms() and busterm(): keep the two readers in
// sync so a routing-time row (teg_mode/orig_*) round-trips through either path.
static const char* BUSTERM_COLS =
    "id,comp_id,hier_path,depth,x1,y1,x2,y2,resolution,"
    "COALESCE(parent_id,''),COALESCE(rects,''),"
    "COALESCE(teg_mode,'THRU'),"
    "COALESCE(orig_x1,0),COALESCE(orig_y1,0),"
    "COALESCE(orig_x2,0),COALESCE(orig_y2,0)";

static BustermRow read_busterm_row(sqlite3_stmt* q) {
    BustermRow r;
    r.id         = (const char*)sqlite3_column_text(q, 0);
    r.comp_id    = (sqlite3_column_type(q, 1) == SQLITE_NULL)
                       ? -1 : sqlite3_column_int(q, 1);
    r.hier_path  = (const char*)sqlite3_column_text(q, 2);
    r.depth      = sqlite3_column_int(q, 3);
    r.x1         = sqlite3_column_double(q, 4);
    r.y1         = sqlite3_column_double(q, 5);
    r.x2         = sqlite3_column_double(q, 6);
    r.y2         = sqlite3_column_double(q, 7);
    r.resolution = (const char*)sqlite3_column_text(q, 8);
    r.parent_id  = (const char*)sqlite3_column_text(q, 9);
    r.rects      = (const char*)sqlite3_column_text(q, 10);
    r.teg_mode   = (const char*)sqlite3_column_text(q, 11);
    r.orig_x1    = sqlite3_column_double(q, 12);
    r.orig_y1    = sqlite3_column_double(q, 13);
    r.orig_x2    = sqlite3_column_double(q, 14);
    r.orig_y2    = sqlite3_column_double(q, 15);
    return r;
}

// The hierarchy-derived busterms only.  Routing-time busterms (id 'tb:<name>',
// written by the topology-persist bridge) share this table but describe a
// different entity (a segment tap, not a hierarchy interface); excluding them
// here keeps the hier bundler / BustermGen::all / CLI listing unpolluted.  The
// loader reaches routing rows by id via busterm() instead.
std::vector<BustermRow> BDB::all_busterms() const {
    if (!_q_all_busterms)
        sqlite3_prepare_v2(_db,
            (std::string("SELECT ") + BUSTERM_COLS +
             " FROM busterm WHERE id NOT LIKE 'tb:%' ORDER BY depth,id").c_str(),
            -1, &_q_all_busterms, nullptr);
    sqlite3_reset(_q_all_busterms);
    std::vector<BustermRow> rows;
    while (sqlite3_step(_q_all_busterms) == SQLITE_ROW)
        rows.push_back(read_busterm_row(_q_all_busterms));
    return rows;
}

std::optional<BustermRow> BDB::busterm(const std::string& id) const {
    Stmt q(_db, (std::string("SELECT ") + BUSTERM_COLS +
                 " FROM busterm WHERE id=?").c_str());
    sqlite3_bind_text(q, 1, id.c_str(), -1, SQLITE_TRANSIENT);
    if (sqlite3_step(q) == SQLITE_ROW)
        return read_busterm_row(q);
    return std::nullopt;
}

// ── Bundle persistence ───────────────────────────────────────────────────────

void BDB::add_bundle(const BundleRow& br) {
    Stmt s(_db,
        "INSERT INTO bundle(id,level,strategy,reason,num_terminals,cell_context,"
        "instances,parent_id,is_replicated,drv_spec_depth,rcv_spec_depth,"
        "drv_spec_path,rcv_spec_paths) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(id) DO UPDATE SET level=excluded.level,"
        " strategy=excluded.strategy, reason=excluded.reason,"
        " num_terminals=excluded.num_terminals, cell_context=excluded.cell_context,"
        " instances=excluded.instances, parent_id=excluded.parent_id,"
        " is_replicated=excluded.is_replicated, drv_spec_depth=excluded.drv_spec_depth,"
        " rcv_spec_depth=excluded.rcv_spec_depth, drv_spec_path=excluded.drv_spec_path,"
        " rcv_spec_paths=excluded.rcv_spec_paths");
    sqlite3_bind_text  (s, 1, br.id.c_str(),           -1, SQLITE_TRANSIENT);
    sqlite3_bind_int   (s, 2, br.level);
    sqlite3_bind_text  (s, 3, br.strategy.c_str(),     -1, SQLITE_TRANSIENT);
    sqlite3_bind_text  (s, 4, br.reason.c_str(),       -1, SQLITE_TRANSIENT);
    sqlite3_bind_int   (s, 5, br.num_terminals);
    sqlite3_bind_text  (s, 6, br.cell_context.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text  (s, 7, br.instances.c_str(),    -1, SQLITE_TRANSIENT);
    if (br.parent_id.empty()) sqlite3_bind_null(s, 8);
    else sqlite3_bind_text(s, 8, br.parent_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int   (s, 9, br.is_replicated ? 1 : 0);
    sqlite3_bind_int   (s, 10, br.drv_spec_depth);
    sqlite3_bind_int   (s, 11, br.rcv_spec_depth);
    sqlite3_bind_text  (s, 12, br.drv_spec_path.c_str(),   -1, SQLITE_TRANSIENT);
    sqlite3_bind_text  (s, 13, br.rcv_spec_paths.c_str(),  -1, SQLITE_TRANSIENT);
    sqlite3_step(s);
}

int BDB::_ensure_net(const std::string& name) {
    { Stmt q(_db, "SELECT id FROM net WHERE name=?");
      sqlite3_bind_text(q, 1, name.c_str(), -1, SQLITE_TRANSIENT);
      if (sqlite3_step(q) == SQLITE_ROW) return sqlite3_column_int(q, 0); }
    // Not present (e.g. a flat-flow net): create a name-only row so bundle_net can
    // FK to it. Pins/props are absent by design — the flat flow has no BDB pins.
    Stmt ins(_db, "INSERT INTO net(name) VALUES(?)");
    sqlite3_bind_text(ins, 1, name.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_step(ins);
    return static_cast<int>(sqlite3_last_insert_rowid(_db));
}

void BDB::add_bundle_net(const std::string& bundle_id, const std::string& net_name) {
    int net_id = _ensure_net(net_name);
    // ord = insertion sequence per bundle: bundle_nets() returns names in this
    // order, preserving the bundle's BIT order (name-sorting breaks the
    // bit_index -> net mapping at >= 10 bits: d_10 < d_2 lexicographically).
    Stmt s(_db,
        "INSERT OR IGNORE INTO bundle_net(bundle_id,net_id,ord)"
        " VALUES(?,?,(SELECT COUNT(*) FROM bundle_net WHERE bundle_id=?1))");
    sqlite3_bind_text(s, 1, bundle_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int (s, 2, net_id);
    sqlite3_step(s);
}

void BDB::add_bundle_busterm(const std::string& bundle_id,
                             const std::string& busterm_id,
                             const std::string& role) {
    Stmt s(_db, "INSERT OR IGNORE INTO bundle_busterm(bundle_id,busterm_id,role)"
                " VALUES(?,?,?)");
    sqlite3_bind_text(s, 1, bundle_id.c_str(),  -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(s, 2, busterm_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(s, 3, role.c_str(),       -1, SQLITE_TRANSIENT);
    sqlite3_step(s);
}

void BDB::clear_bundles() {
    // Topology and bus/net routing all FK to bundle, so clear them first
    // (children before parents) or the FK constraint would reject the bundle
    // delete. The route_snapshot fingerprint describes the routed rows, so it
    // is invalidated too.
    _exec("DELETE FROM route_snapshot;"
          "DELETE FROM net_via; DELETE FROM net_segment;"
          "DELETE FROM bus_via; DELETE FROM bus_segment;"
          "DELETE FROM topology_seg_busterm;"
          "DELETE FROM topology_bridge_segment;"
          "DELETE FROM topology_segment; DELETE FROM topology;"
          "DELETE FROM busterm WHERE id LIKE 'tb:%';"
          "DELETE FROM bundle_busterm; DELETE FROM bundle_net; DELETE FROM bundle;");
}

std::vector<BundleRow> BDB::all_bundles() const {
    Stmt q(_db,
        "SELECT id,level,strategy,reason,num_terminals,cell_context,instances,"
        "parent_id,is_replicated,drv_spec_depth,rcv_spec_depth,drv_spec_path,"
        "rcv_spec_paths FROM bundle ORDER BY CAST(id AS INTEGER), id");
    auto txt = [](sqlite3_stmt* st, int c) -> std::string {
        const unsigned char* p = sqlite3_column_text(st, c);
        return p ? reinterpret_cast<const char*>(p) : std::string();
    };
    std::vector<BundleRow> rows;
    while (sqlite3_step(q) == SQLITE_ROW) {
        BundleRow b;
        b.id            = txt(q, 0);
        b.level         = sqlite3_column_int(q, 1);
        b.strategy      = txt(q, 2);
        b.reason        = txt(q, 3);
        b.num_terminals = sqlite3_column_int(q, 4);
        b.cell_context  = txt(q, 5);
        b.instances     = txt(q, 6);
        b.parent_id     = txt(q, 7);
        b.is_replicated = sqlite3_column_int(q, 8) != 0;
        b.drv_spec_depth = sqlite3_column_int(q, 9);
        b.rcv_spec_depth = sqlite3_column_int(q, 10);
        b.drv_spec_path  = txt(q, 11);
        b.rcv_spec_paths = txt(q, 12);
        rows.push_back(std::move(b));
    }
    return rows;
}

std::vector<std::string> BDB::bundle_nets(const std::string& bundle_id) const {
    // Membership is stored by net_id; join back to net so callers still see names.
    // Bit order first (ord, v10); legacy rows (ord=-1/NULL) fall back to name.
    Stmt q(_db, "SELECT n.name FROM bundle_net b JOIN net n ON b.net_id=n.id"
                " WHERE b.bundle_id=?"
                " ORDER BY (b.ord IS NULL OR b.ord < 0), b.ord, n.name");
    sqlite3_bind_text(q, 1, bundle_id.c_str(), -1, SQLITE_TRANSIENT);
    std::vector<std::string> out;
    while (sqlite3_step(q) == SQLITE_ROW)
        out.emplace_back(reinterpret_cast<const char*>(sqlite3_column_text(q, 0)));
    return out;
}

std::vector<std::pair<std::string, std::string>>
BDB::bundle_busterms(const std::string& bundle_id) const {
    Stmt q(_db, "SELECT busterm_id,role FROM bundle_busterm WHERE bundle_id=?"
                " ORDER BY role, busterm_id");
    sqlite3_bind_text(q, 1, bundle_id.c_str(), -1, SQLITE_TRANSIENT);
    std::vector<std::pair<std::string, std::string>> out;
    while (sqlite3_step(q) == SQLITE_ROW) {
        const unsigned char* r = sqlite3_column_text(q, 1);
        out.emplace_back(reinterpret_cast<const char*>(sqlite3_column_text(q, 0)),
                         r ? reinterpret_cast<const char*>(r) : std::string());
    }
    return out;
}

// ── Candidate topology persistence ───────────────────────────────────────────

void BDB::add_topology(const TopoRow& tr) {
    Stmt s(_db,
        "INSERT INTO topology(bundle_id,cand_index,type,wirelength,trunk_location,"
        "pass_through_count,connected_blocks,feedthru_blocks,is_selected,is_pinned)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(bundle_id,cand_index) DO UPDATE SET type=excluded.type,"
        " wirelength=excluded.wirelength, trunk_location=excluded.trunk_location,"
        " pass_through_count=excluded.pass_through_count,"
        " connected_blocks=excluded.connected_blocks,"
        " feedthru_blocks=excluded.feedthru_blocks, is_selected=excluded.is_selected,"
        " is_pinned=excluded.is_pinned");
    sqlite3_bind_text  (s, 1, tr.id.c_str(),               -1, SQLITE_TRANSIENT);
    sqlite3_bind_int   (s, 2, tr.cand_index);
    sqlite3_bind_text  (s, 3, tr.type.c_str(),             -1, SQLITE_TRANSIENT);
    sqlite3_bind_int   (s, 4, tr.wirelength);
    sqlite3_bind_int   (s, 5, tr.trunk_location);
    sqlite3_bind_int   (s, 6, tr.pass_through_count);
    sqlite3_bind_text  (s, 7, tr.connected_blocks.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text  (s, 8, tr.feedthru_blocks.c_str(),  -1, SQLITE_TRANSIENT);
    sqlite3_bind_int   (s, 9, tr.is_selected ? 1 : 0);
    sqlite3_bind_int   (s, 10, tr.is_pinned ? 1 : 0);
    sqlite3_step(s);
}

void BDB::add_topology_segment(const TopoSegRow& sr) {
    Stmt s(_db,
        "INSERT OR REPLACE INTO topology_segment(bundle_id,cand_index,seg_index,"
        "x1,y1,x2,y2,layer_hint,is_jog,assigned_layer) VALUES(?,?,?,?,?,?,?,?,?,?)");
    sqlite3_bind_text  (s, 1, sr.id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int   (s, 2, sr.cand_index);
    sqlite3_bind_int   (s, 3, sr.seg_index);
    sqlite3_bind_int   (s, 4, sr.x1);
    sqlite3_bind_int   (s, 5, sr.y1);
    sqlite3_bind_int   (s, 6, sr.x2);
    sqlite3_bind_int   (s, 7, sr.y2);
    sqlite3_bind_int   (s, 8, sr.layer_hint);
    sqlite3_bind_int   (s, 9, sr.is_jog ? 1 : 0);
    sqlite3_bind_int   (s, 10, sr.assigned_layer);
    sqlite3_step(s);
}

void BDB::clear_topologies() {
    // Children before parents (FK): the seg-busterm links reference both topology
    // and busterm.  The routing-time 'tb:' busterm rows exist only to back those
    // links, so drop them too (hier-derived 'bt:' rows are untouched); re-persist
    // recreates them idempotently, keeping the *.bdb.sql dump deterministic.
    _exec("DELETE FROM topology_seg_busterm;"
          "DELETE FROM topology_bridge_segment;"
          "DELETE FROM topology_segment; DELETE FROM topology;"
          "DELETE FROM busterm WHERE id LIKE 'tb:%';");
}

std::vector<TopoRow> BDB::topologies(const std::string& bundle_id) const {
    Stmt q(_db,
        "SELECT bundle_id,cand_index,type,wirelength,trunk_location,"
        "pass_through_count,connected_blocks,feedthru_blocks,is_selected,is_pinned"
        " FROM topology WHERE bundle_id=? ORDER BY cand_index");
    sqlite3_bind_text(q, 1, bundle_id.c_str(), -1, SQLITE_TRANSIENT);
    auto txt = [](sqlite3_stmt* st, int c) -> std::string {
        const unsigned char* p = sqlite3_column_text(st, c);
        return p ? reinterpret_cast<const char*>(p) : std::string();
    };
    std::vector<TopoRow> out;
    while (sqlite3_step(q) == SQLITE_ROW) {
        TopoRow t;
        t.id                 = txt(q, 0);
        t.cand_index         = sqlite3_column_int(q, 1);
        t.type               = txt(q, 2);
        t.wirelength         = sqlite3_column_int(q, 3);
        t.trunk_location     = sqlite3_column_int(q, 4);
        t.pass_through_count = sqlite3_column_int(q, 5);
        t.connected_blocks   = txt(q, 6);
        t.feedthru_blocks    = txt(q, 7);
        t.is_selected        = sqlite3_column_int(q, 8) != 0;
        t.is_pinned          = sqlite3_column_int(q, 9) != 0;
        out.push_back(std::move(t));
    }
    return out;
}

std::vector<TopoSegRow> BDB::topology_segments(const std::string& bundle_id,
                                               int cand_index) const {
    Stmt q(_db,
        "SELECT bundle_id,cand_index,seg_index,x1,y1,x2,y2,layer_hint,is_jog,"
        "assigned_layer FROM topology_segment WHERE bundle_id=? AND cand_index=?"
        " ORDER BY seg_index");
    sqlite3_bind_text(q, 1, bundle_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int (q, 2, cand_index);
    std::vector<TopoSegRow> out;
    while (sqlite3_step(q) == SQLITE_ROW) {
        TopoSegRow s;
        s.id         = reinterpret_cast<const char*>(sqlite3_column_text(q, 0));
        s.cand_index = sqlite3_column_int(q, 1);
        s.seg_index  = sqlite3_column_int(q, 2);
        s.x1 = sqlite3_column_int(q, 3);
        s.y1 = sqlite3_column_int(q, 4);
        s.x2 = sqlite3_column_int(q, 5);
        s.y2 = sqlite3_column_int(q, 6);
        s.layer_hint = sqlite3_column_int(q, 7);
        s.is_jog     = sqlite3_column_int(q, 8) != 0;
        s.assigned_layer = sqlite3_column_int(q, 9);
        out.push_back(std::move(s));
    }
    return out;
}

void BDB::add_topology_seg_busterm(const TopoSegBustermRow& r) {
    Stmt s(_db,
        "INSERT OR REPLACE INTO topology_seg_busterm"
        "(bundle_id,cand_index,seg_index,endpoint,busterm_id) VALUES(?,?,?,?,?)");
    sqlite3_bind_text(s, 1, r.bundle_id.c_str(),  -1, SQLITE_TRANSIENT);
    sqlite3_bind_int (s, 2, r.cand_index);
    sqlite3_bind_int (s, 3, r.seg_index);
    sqlite3_bind_text(s, 4, r.endpoint.c_str(),   -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(s, 5, r.busterm_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_step(s);
}

std::vector<TopoSegBustermRow> BDB::topology_seg_busterms(
    const std::string& bundle_id, int cand_index) const {
    Stmt q(_db,
        "SELECT bundle_id,cand_index,seg_index,endpoint,busterm_id"
        " FROM topology_seg_busterm WHERE bundle_id=? AND cand_index=?"
        " ORDER BY seg_index,endpoint");
    sqlite3_bind_text(q, 1, bundle_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int (q, 2, cand_index);
    std::vector<TopoSegBustermRow> out;
    while (sqlite3_step(q) == SQLITE_ROW) {
        TopoSegBustermRow r;
        r.bundle_id  = reinterpret_cast<const char*>(sqlite3_column_text(q, 0));
        r.cand_index = sqlite3_column_int(q, 1);
        r.seg_index  = sqlite3_column_int(q, 2);
        r.endpoint   = reinterpret_cast<const char*>(sqlite3_column_text(q, 3));
        r.busterm_id = reinterpret_cast<const char*>(sqlite3_column_text(q, 4));
        out.push_back(std::move(r));
    }
    return out;
}

void BDB::add_topology_bridge(const TopoBridgeRow& r) {
    Stmt s(_db,
        "INSERT OR REPLACE INTO topology_bridge_segment(bundle_id,cand_index,"
        "block_name,x1,y1,x2,y2,layer_hint,is_jog) VALUES(?,?,?,?,?,?,?,?,?)");
    sqlite3_bind_text(s, 1, r.id.c_str(),         -1, SQLITE_TRANSIENT);
    sqlite3_bind_int (s, 2, r.cand_index);
    sqlite3_bind_text(s, 3, r.block_name.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int (s, 4, r.x1);
    sqlite3_bind_int (s, 5, r.y1);
    sqlite3_bind_int (s, 6, r.x2);
    sqlite3_bind_int (s, 7, r.y2);
    sqlite3_bind_int (s, 8, r.layer_hint);
    sqlite3_bind_int (s, 9, r.is_jog ? 1 : 0);
    sqlite3_step(s);
}

static TopoBridgeRow _bridge_row_from(sqlite3_stmt* q) {
    TopoBridgeRow r;
    r.id         = reinterpret_cast<const char*>(sqlite3_column_text(q, 0));
    r.cand_index = sqlite3_column_int(q, 1);
    r.block_name = reinterpret_cast<const char*>(sqlite3_column_text(q, 2));
    r.x1 = sqlite3_column_int(q, 3);
    r.y1 = sqlite3_column_int(q, 4);
    r.x2 = sqlite3_column_int(q, 5);
    r.y2 = sqlite3_column_int(q, 6);
    r.layer_hint = sqlite3_column_int(q, 7);
    r.is_jog     = sqlite3_column_int(q, 8) != 0;
    return r;
}

std::vector<TopoBridgeRow> BDB::topology_bridges(const std::string& bundle_id,
                                                 int cand_index) const {
    Stmt q(_db,
        "SELECT bundle_id,cand_index,block_name,x1,y1,x2,y2,layer_hint,is_jog"
        " FROM topology_bridge_segment WHERE bundle_id=? AND cand_index=?"
        " ORDER BY block_name");
    sqlite3_bind_text(q, 1, bundle_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int (q, 2, cand_index);
    std::vector<TopoBridgeRow> out;
    while (sqlite3_step(q) == SQLITE_ROW)
        out.push_back(_bridge_row_from(q));
    return out;
}

std::vector<TopoBridgeRow> BDB::all_topology_bridges(
        const std::string& bundle_id) const {
    Stmt q(_db,
        "SELECT bundle_id,cand_index,block_name,x1,y1,x2,y2,layer_hint,is_jog"
        " FROM topology_bridge_segment WHERE bundle_id=?"
        " ORDER BY cand_index, block_name");
    sqlite3_bind_text(q, 1, bundle_id.c_str(), -1, SQLITE_TRANSIENT);
    std::vector<TopoBridgeRow> out;
    while (sqlite3_step(q) == SQLITE_ROW)
        out.push_back(_bridge_row_from(q));
    return out;
}

void BDB::set_topology_selected(const std::string& bundle_id, int cand_index) {
    Stmt s(_db, "UPDATE topology SET is_selected=(cand_index=?) WHERE bundle_id=?");
    sqlite3_bind_int (s, 1, cand_index);
    sqlite3_bind_text(s, 2, bundle_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_step(s);
}

void BDB::set_segment_layer(const std::string& bundle_id, int cand_index,
                            int seg_index, int layer) {
    Stmt s(_db, "UPDATE topology_segment SET assigned_layer=?"
                " WHERE bundle_id=? AND cand_index=? AND seg_index=?");
    sqlite3_bind_int (s, 1, layer);
    sqlite3_bind_text(s, 2, bundle_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int (s, 3, cand_index);
    sqlite3_bind_int (s, 4, seg_index);
    sqlite3_step(s);
}

void BDB::reset_assigned_layers(const std::string& bundle_id) {
    Stmt s(_db, "UPDATE topology_segment SET assigned_layer=-1 WHERE bundle_id=?");
    sqlite3_bind_text(s, 1, bundle_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_step(s);
}

void BDB::clear_expanded_bundles() {
    // Expanded per-instance bundles are marked is_replicated=1. Drop them and any
    // rows keyed to them (net/bus routing, topologies, memberships) — children
    // before parents so the routing-table FKs to bundle(id) are never violated.
    // This removes some routed rows, so the route_snapshot is invalidated too.
    _exec(
        "DELETE FROM route_snapshot;"
        "DELETE FROM net_via WHERE bundle_id IN"
        " (SELECT id FROM bundle WHERE is_replicated=1);"
        "DELETE FROM net_segment WHERE bundle_id IN"
        " (SELECT id FROM bundle WHERE is_replicated=1);"
        "DELETE FROM bus_via WHERE bundle_id IN"
        " (SELECT id FROM bundle WHERE is_replicated=1);"
        "DELETE FROM bus_segment WHERE bundle_id IN"
        " (SELECT id FROM bundle WHERE is_replicated=1);"
        "DELETE FROM topology_seg_busterm WHERE bundle_id IN"
        " (SELECT id FROM bundle WHERE is_replicated=1);"
        "DELETE FROM topology_bridge_segment WHERE bundle_id IN"
        " (SELECT id FROM bundle WHERE is_replicated=1);"
        "DELETE FROM topology_segment WHERE bundle_id IN"
        " (SELECT id FROM bundle WHERE is_replicated=1);"
        "DELETE FROM topology WHERE bundle_id IN"
        " (SELECT id FROM bundle WHERE is_replicated=1);"
        "DELETE FROM bundle_busterm WHERE bundle_id IN"
        " (SELECT id FROM bundle WHERE is_replicated=1);"
        "DELETE FROM bundle_net WHERE bundle_id IN"
        " (SELECT id FROM bundle WHERE is_replicated=1);"
        "DELETE FROM bundle WHERE is_replicated=1;");
}

// ── Abstract-NUTS bus routing persistence ────────────────────────────────────

void BDB::add_bus_segment(const BusSegRow& r) {
    Stmt s(_db,
        "INSERT OR REPLACE INTO bus_segment(bundle_id,seg_idx,layer,is_horiz,"
        "x1,y1,x2,y2,track_position,width,placed,is_jog,"
        "interval_lo,interval_hi,track_lo_bound,track_hi_bound)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)");
    sqlite3_bind_text  (s, 1, r.id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int   (s, 2, r.seg_idx);
    sqlite3_bind_int   (s, 3, r.layer);
    sqlite3_bind_int   (s, 4, r.is_horiz ? 1 : 0);
    sqlite3_bind_double(s, 5, r.x1);
    sqlite3_bind_double(s, 6, r.y1);
    sqlite3_bind_double(s, 7, r.x2);
    sqlite3_bind_double(s, 8, r.y2);
    sqlite3_bind_double(s, 9, r.track_position);
    sqlite3_bind_double(s, 10, r.width);
    sqlite3_bind_int   (s, 11, r.placed ? 1 : 0);
    sqlite3_bind_int   (s, 12, r.is_jog ? 1 : 0);
    sqlite3_bind_double(s, 13, r.interval_lo);
    sqlite3_bind_double(s, 14, r.interval_hi);
    // Infinities are not valid SQL literals in a *.bdb.sql dump — store NULL
    // for unbounded and translate back on read.
    if (std::isinf(r.track_lo_bound)) sqlite3_bind_null(s, 15);
    else                              sqlite3_bind_double(s, 15, r.track_lo_bound);
    if (std::isinf(r.track_hi_bound)) sqlite3_bind_null(s, 16);
    else                              sqlite3_bind_double(s, 16, r.track_hi_bound);
    sqlite3_step(s);
}

void BDB::add_bus_via(const BusViaRow& r) {
    Stmt s(_db,
        "INSERT OR REPLACE INTO bus_via(bundle_id,from_seg,to_seg,from_layer,"
        "to_layer,x,y,bit_width) VALUES(?,?,?,?,?,?,?,?)");
    sqlite3_bind_text  (s, 1, r.id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int   (s, 2, r.from_seg);
    sqlite3_bind_int   (s, 3, r.to_seg);
    sqlite3_bind_int   (s, 4, r.from_layer);
    sqlite3_bind_int   (s, 5, r.to_layer);
    sqlite3_bind_double(s, 6, r.x);
    sqlite3_bind_double(s, 7, r.y);
    sqlite3_bind_int   (s, 8, r.bit_width);
    sqlite3_step(s);
}

void BDB::clear_bus_routing() {
    // The route_snapshot fingerprint no longer describes the DB once the bus rows
    // are gone, so drop it too (run_nuts rewrites both together). The detailed
    // net rows are derived from these bus rows, so re-solving abstract NUTS
    // invalidates them as well.
    _exec("DELETE FROM route_snapshot;"
          "DELETE FROM net_via; DELETE FROM net_segment;"
          "DELETE FROM bus_via; DELETE FROM bus_segment;");
}

void BDB::set_route_snapshot(const std::string& hash, int n_bus_segments,
                             int n_bus_vias, const std::string& stage,
                             int n_net_segments, int n_net_vias) {
    // Singleton row (id=1): the fingerprint of the current routed output.
    Stmt s(_db,
        "INSERT OR REPLACE INTO route_snapshot(id,hash,n_bus_segments,n_bus_vias,"
        "stage,n_net_segments,n_net_vias) VALUES(1,?,?,?,?,?,?)");
    sqlite3_bind_text(s, 1, hash.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int (s, 2, n_bus_segments);
    sqlite3_bind_int (s, 3, n_bus_vias);
    sqlite3_bind_text(s, 4, stage.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int (s, 5, n_net_segments);
    sqlite3_bind_int (s, 6, n_net_vias);
    sqlite3_step(s);
}

RouteSnapshotRow BDB::route_snapshot() const {
    Stmt q(_db,
        "SELECT hash,n_bus_segments,n_bus_vias,stage,n_net_segments,n_net_vias"
        " FROM route_snapshot WHERE id=1");
    RouteSnapshotRow r;
    if (sqlite3_step(q) == SQLITE_ROW) {
        const unsigned char* h = sqlite3_column_text(q, 0);
        r.hash           = h ? reinterpret_cast<const char*>(h) : std::string();
        r.n_bus_segments = sqlite3_column_int(q, 1);
        r.n_bus_vias     = sqlite3_column_int(q, 2);
        const unsigned char* st = sqlite3_column_text(q, 3);
        r.stage          = st ? reinterpret_cast<const char*>(st) : std::string();
        r.n_net_segments = sqlite3_column_int(q, 4);
        r.n_net_vias     = sqlite3_column_int(q, 5);
    }
    return r;
}

std::vector<BusSegRow> BDB::bus_segments(const std::string& bundle_id) const {
    Stmt q(_db,
        "SELECT bundle_id,seg_idx,layer,is_horiz,x1,y1,x2,y2,track_position,"
        "width,placed,is_jog,interval_lo,interval_hi,track_lo_bound,"
        "track_hi_bound FROM bus_segment WHERE bundle_id=? ORDER BY seg_idx");
    sqlite3_bind_text(q, 1, bundle_id.c_str(), -1, SQLITE_TRANSIENT);
    std::vector<BusSegRow> out;
    while (sqlite3_step(q) == SQLITE_ROW) {
        BusSegRow r;
        r.id             = reinterpret_cast<const char*>(sqlite3_column_text(q, 0));
        r.seg_idx        = sqlite3_column_int(q, 1);
        r.layer          = sqlite3_column_int(q, 2);
        r.is_horiz       = sqlite3_column_int(q, 3) != 0;
        r.x1 = sqlite3_column_double(q, 4);
        r.y1 = sqlite3_column_double(q, 5);
        r.x2 = sqlite3_column_double(q, 6);
        r.y2 = sqlite3_column_double(q, 7);
        r.track_position = sqlite3_column_double(q, 8);
        r.width          = sqlite3_column_double(q, 9);
        r.placed         = sqlite3_column_int(q, 10) != 0;
        r.is_jog         = sqlite3_column_int(q, 11) != 0;
        // NULL (pre-v9 rows / unbounded) -> defaults: 0 interval, +/-inf bounds.
        if (sqlite3_column_type(q, 12) != SQLITE_NULL)
            r.interval_lo = sqlite3_column_double(q, 12);
        if (sqlite3_column_type(q, 13) != SQLITE_NULL)
            r.interval_hi = sqlite3_column_double(q, 13);
        if (sqlite3_column_type(q, 14) != SQLITE_NULL)
            r.track_lo_bound = sqlite3_column_double(q, 14);
        if (sqlite3_column_type(q, 15) != SQLITE_NULL)
            r.track_hi_bound = sqlite3_column_double(q, 15);
        out.push_back(std::move(r));
    }
    return out;
}

std::vector<BusViaRow> BDB::bus_vias(const std::string& bundle_id) const {
    Stmt q(_db,
        "SELECT bundle_id,from_seg,to_seg,from_layer,to_layer,x,y,bit_width"
        " FROM bus_via WHERE bundle_id=? ORDER BY from_seg, to_seg");
    sqlite3_bind_text(q, 1, bundle_id.c_str(), -1, SQLITE_TRANSIENT);
    std::vector<BusViaRow> out;
    while (sqlite3_step(q) == SQLITE_ROW) {
        BusViaRow r;
        r.id         = reinterpret_cast<const char*>(sqlite3_column_text(q, 0));
        r.from_seg   = sqlite3_column_int(q, 1);
        r.to_seg     = sqlite3_column_int(q, 2);
        r.from_layer = sqlite3_column_int(q, 3);
        r.to_layer   = sqlite3_column_int(q, 4);
        r.x = sqlite3_column_double(q, 5);
        r.y = sqlite3_column_double(q, 6);
        r.bit_width  = sqlite3_column_int(q, 7);
        out.push_back(std::move(r));
    }
    return out;
}

// ── Detailed-NUTS per-bit routing persistence ────────────────────────────────

void BDB::add_net_segment(const NetSegRow& r) {
    Stmt s(_db,
        "INSERT OR REPLACE INTO net_segment(bundle_id,seg_idx,bit_index,net_id,"
        "layer,is_horiz,x1,y1,x2,y2,track_position,width)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)");
    sqlite3_bind_text(s, 1, r.id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int (s, 2, r.seg_idx);
    sqlite3_bind_int (s, 3, r.bit_index);
    if (r.net_name.empty())
        sqlite3_bind_null(s, 4);
    else
        sqlite3_bind_int(s, 4, _ensure_net(r.net_name));
    sqlite3_bind_int   (s, 5, r.layer);
    sqlite3_bind_int   (s, 6, r.is_horiz ? 1 : 0);
    sqlite3_bind_double(s, 7, r.x1);
    sqlite3_bind_double(s, 8, r.y1);
    sqlite3_bind_double(s, 9, r.x2);
    sqlite3_bind_double(s, 10, r.y2);
    sqlite3_bind_double(s, 11, r.track_position);
    sqlite3_bind_double(s, 12, r.width);
    sqlite3_step(s);
}

void BDB::add_net_via(const NetViaRow& r) {
    Stmt s(_db,
        "INSERT OR REPLACE INTO net_via(bundle_id,from_seg,to_seg,bit_index,"
        "net_id,from_layer,to_layer,x,y) VALUES(?,?,?,?,?,?,?,?,?)");
    sqlite3_bind_text(s, 1, r.id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int (s, 2, r.from_seg);
    sqlite3_bind_int (s, 3, r.to_seg);
    sqlite3_bind_int (s, 4, r.bit_index);
    if (r.net_name.empty())
        sqlite3_bind_null(s, 5);
    else
        sqlite3_bind_int(s, 5, _ensure_net(r.net_name));
    sqlite3_bind_int   (s, 6, r.from_layer);
    sqlite3_bind_int   (s, 7, r.to_layer);
    sqlite3_bind_double(s, 8, r.x);
    sqlite3_bind_double(s, 9, r.y);
    sqlite3_step(s);
}

void BDB::clear_detailed_routing() {
    // The route_snapshot was hashed over these rows, so it goes with them; the
    // caller (persist path) rewrites it after re-persisting. Bus rows stay.
    _exec("DELETE FROM route_snapshot;"
          "DELETE FROM net_via; DELETE FROM net_segment;");
}

std::vector<NetSegRow> BDB::net_segments(const std::string& bundle_id) const {
    Stmt q(_db,
        "SELECT s.bundle_id,s.seg_idx,s.bit_index,s.net_id,COALESCE(n.name,''),"
        "s.layer,s.is_horiz,s.x1,s.y1,s.x2,s.y2,s.track_position,s.width"
        " FROM net_segment s LEFT JOIN net n ON s.net_id = n.id"
        " WHERE s.bundle_id=? ORDER BY s.seg_idx, s.bit_index");
    sqlite3_bind_text(q, 1, bundle_id.c_str(), -1, SQLITE_TRANSIENT);
    std::vector<NetSegRow> out;
    while (sqlite3_step(q) == SQLITE_ROW) {
        NetSegRow r;
        r.id        = reinterpret_cast<const char*>(sqlite3_column_text(q, 0));
        r.seg_idx   = sqlite3_column_int(q, 1);
        r.bit_index = sqlite3_column_int(q, 2);
        r.net_id    = sqlite3_column_type(q, 3) == SQLITE_NULL
                          ? -1 : sqlite3_column_int(q, 3);
        r.net_name  = reinterpret_cast<const char*>(sqlite3_column_text(q, 4));
        r.layer     = sqlite3_column_int(q, 5);
        r.is_horiz  = sqlite3_column_int(q, 6) != 0;
        r.x1 = sqlite3_column_double(q, 7);
        r.y1 = sqlite3_column_double(q, 8);
        r.x2 = sqlite3_column_double(q, 9);
        r.y2 = sqlite3_column_double(q, 10);
        r.track_position = sqlite3_column_double(q, 11);
        r.width          = sqlite3_column_double(q, 12);
        out.push_back(std::move(r));
    }
    return out;
}

std::vector<NetViaRow> BDB::net_vias(const std::string& bundle_id) const {
    Stmt q(_db,
        "SELECT v.bundle_id,v.from_seg,v.to_seg,v.bit_index,v.net_id,"
        "COALESCE(n.name,''),v.from_layer,v.to_layer,v.x,v.y"
        " FROM net_via v LEFT JOIN net n ON v.net_id = n.id"
        " WHERE v.bundle_id=? ORDER BY v.from_seg, v.to_seg, v.bit_index");
    sqlite3_bind_text(q, 1, bundle_id.c_str(), -1, SQLITE_TRANSIENT);
    std::vector<NetViaRow> out;
    while (sqlite3_step(q) == SQLITE_ROW) {
        NetViaRow r;
        r.id        = reinterpret_cast<const char*>(sqlite3_column_text(q, 0));
        r.from_seg  = sqlite3_column_int(q, 1);
        r.to_seg    = sqlite3_column_int(q, 2);
        r.bit_index = sqlite3_column_int(q, 3);
        r.net_id    = sqlite3_column_type(q, 4) == SQLITE_NULL
                          ? -1 : sqlite3_column_int(q, 4);
        r.net_name  = reinterpret_cast<const char*>(sqlite3_column_text(q, 5));
        r.from_layer = sqlite3_column_int(q, 6);
        r.to_layer   = sqlite3_column_int(q, 7);
        r.x = sqlite3_column_double(q, 8);
        r.y = sqlite3_column_double(q, 9);
        out.push_back(std::move(r));
    }
    return out;
}

std::string BDB::new_group(const std::string&, const std::string&,
                            const std::string&) { return {}; }
void BDB::add_grp_member(const std::string&,const std::string&,
                          const std::string&) {}
void BDB::remove_grp_member(const std::string&,const std::string&,
                              const std::string&) {}
void BDB::delete_group(const std::string&) {}
std::vector<GrpRow> BDB::all_groups() const { return {}; }

}  // namespace buda
