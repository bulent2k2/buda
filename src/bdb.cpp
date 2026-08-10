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
#include "lef_io.h"
#include "def_io.h"
#include <set>
#include <iostream>
#include <stdexcept>
#include <fstream>
#include <iterator>
#include <sstream>
#include <regex>
#include <algorithm>
#include <cmath>
#include <map>
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

// Null-safe TEXT column read: constructing std::string from a NULL
// sqlite3_column_text is undefined behavior (crash).  Nullable TEXT columns
// (component.cell, meta.value, …) must go through this (audit C6-06).
inline std::string col_txt(sqlite3_stmt* st, int c) {
    const unsigned char* p = sqlite3_column_text(st, c);
    return p ? reinterpret_cast<const char*>(p) : std::string();
}

// Run a persist INSERT/UPDATE to completion, THROWING on any non-DONE result
// (SQLITE_CONSTRAINT — e.g. an FK violation from a persist-ordering regression
// — or SQLITE_FULL on a disk-full checkpoint) instead of silently dropping the
// row (audit C6-09).  The old fire-and-forget `sqlite3_step` let a failed
// insert vanish, after which route_snapshot was written over the incomplete
// row set and a later load_pipeline resume restored a routing state missing
// rows with no diagnostic.  Fail LOUD so the caller aborts the checkpoint.
inline void step_checked(sqlite3* db, sqlite3_stmt* st, const char* what) {
    if (sqlite3_step(st) != SQLITE_DONE)
        throw std::runtime_error(std::string("BDB persist failed (") + what +
                                 "): " + sqlite3_errmsg(db));
}
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

// Write batching (see bdb.h): fold a burst of autocommit inserts into one
// transaction so the WAL is fsync'd once, not per statement.  Depth-counted so
// composing persist helpers nest safely — only depth 0↔1 transitions touch SQL.
// The counter is advanced ONLY AFTER the SQL succeeds, so a throwing BEGIN/
// COMMIT leaves depth consistent with SQLite's actual transaction state: a
// failed outer BEGIN keeps depth 0 (no phantom nesting), and a failed COMMIT
// keeps depth 1 (the transaction is still open) so the caller can recover with
// rollback_batch() rather than the next begin_batch() issuing BEGIN inside it.
void BDB::begin_batch() {
    if (_batch_depth == 0) _exec("BEGIN");   // throws → depth stays 0
    ++_batch_depth;
}
void BDB::commit_batch() {
    if (_batch_depth == 0) return;
    if (_batch_depth == 1) _exec("COMMIT");  // throws → depth stays 1 (txn open)
    --_batch_depth;
}
void BDB::rollback_batch() {
    if (_batch_depth == 0) return;
    _batch_depth = 0;                        // reset first: a batch is never left
    _exec("ROLLBACK");                       // half-open from our bookkeeping's view
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
        rcv_spec_paths TEXT,                -- JSON array
        gen_knobs      TEXT DEFAULT '',     -- additive-generation knob memo (v15)
        is_expanded    INTEGER DEFAULT 0,   -- planner-expanded instance row (v18)
        bu_locked      INTEGER DEFAULT 0,   -- bottom-up template copy (v18)
        cloned_from    TEXT DEFAULT ''      -- rotation-class clone origin (v19)
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
        topo_uid           TEXT DEFAULT '',    -- stable content identity (v14)
        source             TEXT DEFAULT 'generated',  -- generated|user|dogleg (v15)
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
        edge_id    INTEGER DEFAULT -1,      -- MST-edge identity (v14)
        perp_clamp_lo INTEGER DEFAULT (-2147483648), -- overlap-U perp slide clamp (v16;
        perp_clamp_hi INTEGER DEFAULT ( 2147483647), -- INT_MIN/INT_MAX = unclamped)
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
    -- Seg-to-seg junction links (Topology::seg_conns, v12): one row per real
    -- junction — endpoint `endpoint` of seg_index lands on other_seg.  A missing
    -- (seg, endpoint) is a free end or a busterm tap; one endpoint can join
    -- several segments (3+ meeting at a point), hence other_seg in the PK.
    -- Lets a reloaded topology restore its junction graph LOGICALLY, with no
    -- geometric re-derivation (single_source_topo_truth.md Phase 5).
    CREATE TABLE IF NOT EXISTS topology_seg_conn (
        bundle_id  TEXT,
        cand_index INTEGER,
        seg_index  INTEGER,
        endpoint   TEXT,              -- 'start' | 'end'
        other_seg  INTEGER,
        PRIMARY KEY (bundle_id, cand_index, seg_index, endpoint, other_seg),
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

// Abstract-NUTS bus routing tables (schema v5). bundle_id is a HARD FK to
// bundle(id) (NOT NULL REFERENCES, since v7): a persist path must ensure the
// parent bundle exists first.  An FK-rejected insert now THROWS (audit C6-09:
// the persist mutators go through step_checked), no longer silently dropping
// the row — the hier re-plan expanded-parent staleness that used to produce
// FK-orphaned rows was fixed alongside (re-expand from templates, not the
// prior run's per-instance wrappers).
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
            is_port      INTEGER NOT NULL DEFAULT 0,
            is_replicated INTEGER DEFAULT 0,
            orient       TEXT DEFAULT 'N'
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
            name        TEXT PRIMARY KEY,
            width       REAL NOT NULL,
            height      REAL NOT NULL,
            -- LEF MACRO CLASS, verbatim ('BLOCK', 'CORE', 'PAD', …).  The
            -- authority on whether a cell is a hard macro or a standard
            -- cell, which no other column can answer.  '' = not stated;
            -- LEF's own default for an absent CLASS is CORE.
            cls         TEXT NOT NULL DEFAULT '',
            bottom_up   INTEGER NOT NULL DEFAULT 0,
            layer_cap   INTEGER NOT NULL DEFAULT -1,
            layer_floor INTEGER NOT NULL DEFAULT -1
        );
        CREATE TABLE IF NOT EXISTS cell_layer_share (
            cell     TEXT NOT NULL REFERENCES cell(name),
            layer_id INTEGER NOT NULL,
            share    REAL NOT NULL,
            PRIMARY KEY (cell, layer_id)
        );
        CREATE TABLE IF NOT EXISTS ndr_rule (
            name         TEXT PRIMARY KEY,
            width_x      REAL NOT NULL DEFAULT 1,
            spacing_x    REAL NOT NULL DEFAULT 1,
            shield_mode  INTEGER NOT NULL DEFAULT 0,
            shield_per_n INTEGER NOT NULL DEFAULT 0,
            shield_net   TEXT NOT NULL DEFAULT 'GND',
            layers       TEXT NOT NULL DEFAULT '',
            credit       INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS ndr_scope (
            prefix TEXT PRIMARY KEY,
            rule   TEXT NOT NULL REFERENCES ndr_rule(name)
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
        std::string k = col_txt(mq, 0);
        std::string v = col_txt(mq, 1);
        if (k == "units") _units = std::stoi(v);
        else if (k == "die_w") _die_w = std::stod(v);
        else if (k == "die_h") _die_h = std::stod(v);
        else if (k == "lu_per_um") {
            // The scale the stored coordinates were WRITTEN in.  Restoring it
            // is what makes a reopened design self-describing: without it a
            // DBU-scale BDB would be read back as microns and every derived
            // physical quantity would be off by the same factor.
            try { double d = std::stod(v); if (d > 0.0) _lu_per_um = d; }
            catch (...) {}
        }
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

void BDB::meta_set(const std::string& key, const std::string& value) {
    _set_meta(key, value);
}

void BDB::save_copy(const std::string& dest_path) const {
    sqlite3* out = nullptr;
    if (sqlite3_open(dest_path.c_str(), &out) != SQLITE_OK) {
        std::string err = out ? sqlite3_errmsg(out) : "cannot open";
        sqlite3_close(out);
        throw std::runtime_error("save_copy: cannot open " + dest_path +
                                 ": " + err);
    }
    sqlite3_backup* bk = sqlite3_backup_init(out, "main", _db, "main");
    if (!bk) {
        std::string err = sqlite3_errmsg(out);
        sqlite3_close(out);
        throw std::runtime_error("save_copy: backup init failed for " +
                                 dest_path + ": " + err);
    }
    sqlite3_backup_step(bk, -1);          // whole database in one step
    sqlite3_backup_finish(bk);
    const int rc = sqlite3_errcode(out);
    sqlite3_close(out);
    if (rc != SQLITE_OK && rc != SQLITE_DONE)
        throw std::runtime_error("save_copy: backup failed for " + dest_path);
}

std::string BDB::meta_get(const std::string& key, const std::string& def) const {
    Stmt q(_db, "SELECT value FROM meta WHERE key=?");
    sqlite3_bind_text(q, 1, key.c_str(), -1, SQLITE_TRANSIENT);
    if (sqlite3_step(q) == SQLITE_ROW)
        return col_txt(q, 0);            // NULL value → empty, never a crash (C6-06)
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
        // v1 -> v2: bundle-persistence schema. Legacy pre-v2 bundle tables were
        // always empty, so rebuild them to the new shape rather than a long
        // ALTER chain.  But a modern-schema DB can reach here with
        // user_version reading 0 (e.g. a serialize round-trip that lost the
        // pragma) and its bundle table is already current-shape WITH data — the
        // unconditional DROP silently destroyed it (audit C6-05).  Guard on the
        // modern shape actually being absent (the `cell_context` column, added
        // in v2); when present the tables are already current, so skip.
        bool modern_bundle = false;
        { Stmt q(_db, "PRAGMA table_info(bundle)");
          while (sqlite3_step(q) == SQLITE_ROW)
              if (col_txt(q, 1) == "cell_context") modern_bundle = true; }
        if (!modern_bundle) {
            _exec("DROP TABLE IF EXISTS bundle_busterm;"
                  "DROP TABLE IF EXISTS bundle_net;"
                  "DROP TABLE IF EXISTS bundle;");
            _exec(BUNDLE_DDL);
        }
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
    if (v < 12) {
        // v11 -> v12: seg-to-seg junction links (topology_seg_conn — brand new
        // table, created via TOPOLOGY_DDL; IF NOT EXISTS no-ops the rest).
        _exec(TOPOLOGY_DDL);
    }
    if (v < 13) {
        // v12 -> v13: per-instance orientation token (GDS round-trip).
        // Ignored if the column already exists (v6-style idempotent ALTER).
        sqlite3_exec(_db,
            "ALTER TABLE component ADD COLUMN orient TEXT DEFAULT 'N'",
            nullptr, nullptr, nullptr);
    }
    if (v < 14) {
        // v13 -> v14: stable candidate identity (topo_conn_unification Phase E1).
        // topo_uid is recomputable from persisted content alone, so pre-v14 rows
        // need no data migration — load_pipeline backfills silently on reload,
        // mirroring the pre-v12 seg_conns fallback.  edge_id closes the documented
        // Segment::edge_id round-trip gap (topology.h).  Idempotent ALTERs.
        sqlite3_exec(_db,
            "ALTER TABLE topology ADD COLUMN topo_uid TEXT DEFAULT ''",
            nullptr, nullptr, nullptr);
        sqlite3_exec(_db,
            "ALTER TABLE topology_segment ADD COLUMN edge_id INTEGER DEFAULT -1",
            nullptr, nullptr, nullptr);
    }
    if (v < 15) {
        // v14 -> v15: candidate provenance + per-bundle generation-knob memo
        // (topo_conn_unification Phases E2b/E4).  Idempotent ALTERs; existing
        // rows default to source='generated' (correct: user candidates did not
        // exist before v15).
        sqlite3_exec(_db,
            "ALTER TABLE topology ADD COLUMN source TEXT DEFAULT 'generated'",
            nullptr, nullptr, nullptr);
        sqlite3_exec(_db,
            "ALTER TABLE bundle ADD COLUMN gen_knobs TEXT DEFAULT ''",
            nullptr, nullptr, nullptr);
    }
    if (v < 16) {
        // v15 -> v16: overlap-U per-segment perp slide clamp
        // (topology.h Segment::perp_clamp_lo/hi).  The clamp is a deterministic
        // function of the segment geometry, so pre-v16 rows need no data
        // migration — they default to the INT_MIN/INT_MAX unclamped sentinels,
        // which is correct for every non-U_OVL segment.  Idempotent ALTERs.
        sqlite3_exec(_db,
            "ALTER TABLE topology_segment ADD COLUMN perp_clamp_lo INTEGER DEFAULT (-2147483648)",
            nullptr, nullptr, nullptr);
        sqlite3_exec(_db,
            "ALTER TABLE topology_segment ADD COLUMN perp_clamp_hi INTEGER DEFAULT ( 2147483647)",
            nullptr, nullptr, nullptr);
    }
    if (v < 17) {
        // v16 -> v17: bottom-up template planning flag on cell types.  Pre-v17
        // designs have no marked cells, so the 0 default is correct and no
        // data migration is needed.  Idempotent ALTER.
        sqlite3_exec(_db,
            "ALTER TABLE cell ADD COLUMN bottom_up INTEGER NOT NULL DEFAULT 0",
            nullptr, nullptr, nullptr);
    }
    if (v < 18) {
        // v17 -> v18: bottom-up copy provenance on expanded per-instance
        // bundle rows.  Pre-v18 designs carry no bottom-up routing, so the 0
        // default is correct.  Idempotent ALTER.
        sqlite3_exec(_db,
            "ALTER TABLE bundle ADD COLUMN is_expanded INTEGER DEFAULT 0",
            nullptr, nullptr, nullptr);
        sqlite3_exec(_db,
            "ALTER TABLE bundle ADD COLUMN bu_locked INTEGER DEFAULT 0",
            nullptr, nullptr, nullptr);
    }
    if (v < 19) {
        // v18 -> v19: rotation-class clone template provenance.  Pre-v19
        // designs have no clone templates, so the '' default is correct.
        // Idempotent ALTER.
        sqlite3_exec(_db,
            "ALTER TABLE bundle ADD COLUMN cloned_from TEXT DEFAULT ''",
            nullptr, nullptr, nullptr);
    }
    if (v < 20) {
        // v19 -> v20: per-cell layer band (docs/internal/hier_layer_caps.md)
        // plus the fractional-share table (Phase-3 consumer).  Pre-v20
        // designs carry no policy, so the -1 defaults are correct.
        // Idempotent ALTERs; the share table is created by the fresh-DB DDL
        // (CREATE IF NOT EXISTS) before _migrate runs.
        sqlite3_exec(_db,
            "ALTER TABLE cell ADD COLUMN layer_cap INTEGER NOT NULL DEFAULT -1",
            nullptr, nullptr, nullptr);
        sqlite3_exec(_db,
            "ALTER TABLE cell ADD COLUMN layer_floor INTEGER NOT NULL DEFAULT -1",
            nullptr, nullptr, nullptr);
    }
    if (v < 21) {
        // v20 -> v21: NDR rule persistence.  The ndr_rule/ndr_scope tables
        // are created by the fresh-DB DDL (CREATE IF NOT EXISTS) before
        // _migrate runs; only the bundle provenance column needs an ALTER.
        // Pre-v21 designs carry no rules, so the '' default is correct.
        sqlite3_exec(_db,
            "ALTER TABLE bundle ADD COLUMN ndr_rule TEXT DEFAULT ''",
            nullptr, nullptr, nullptr);
    }
    if (v < 22) {
        // v21 -> v22: R5a crediting opt-in on the rule row.  Pre-v22 rules
        // never credited, so the 0 default is correct.
        sqlite3_exec(_db,
            "ALTER TABLE ndr_rule ADD COLUMN credit INTEGER NOT NULL DEFAULT 0",
            nullptr, nullptr, nullptr);
    }
    if (v < 23) {
        // v22 -> v23: die ports as boundary components (Phase 3d).  Reading
        // DEF PINS does not make them endpoints — endpoint derivation is
        // keyed by component — so each placed port becomes a zero-area
        // component, and this flag is what keeps that fiction VISIBLE to the
        // database and to the audits rather than passing as a real instance.
        // Pre-v23 designs have no ports, so the 0 default is correct.
        sqlite3_exec(_db,
            "ALTER TABLE component ADD COLUMN is_port INTEGER NOT NULL DEFAULT 0",
            nullptr, nullptr, nullptr);
    }
    if (v < 24) {
        // v23 -> v24: LEF MACRO CLASS on the cell row.  It is the only
        // authoritative answer to "is this a hard macro or a standard
        // cell", and `import_verilog` needs it to decide which instances of
        // undefined modules belong in the routing hierarchy.  Pre-v24
        // designs never recorded it, so '' (not stated) is correct.
        sqlite3_exec(_db,
            "ALTER TABLE cell ADD COLUMN cls TEXT NOT NULL DEFAULT ''",
            nullptr, nullptr, nullptr);
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
    // else it disappears from HPWL/fanout (C6-07); the name goes with it so
    // a `<bus>[<k>]` net is classified here exactly as the importers do.
    _ensure_net_props(net_id, net_name);

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
        // An empty path (endpoint like ".pin" or "") has no ancestors — and
        // seg.size()-1 on size_t would underflow to SIZE_MAX and index the
        // empty vector (audit C6-02).
        if (seg.empty()) continue;
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
    // else it disappears from HPWL/fanout (C6-07); the name goes with it so
    // a `<bus>[<k>]` net is classified here exactly as the importers do.
    _ensure_net_props(net_id, net_name);

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
        if (seg.empty()) continue;   // audit C6-02: size_t underflow on empty path
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
    // else it disappears from HPWL/fanout (C6-07); the name goes with it so
    // a `<bus>[<k>]` net is classified here exactly as the importers do.
    _ensure_net_props(net_id, net_name);

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
        if (seg.empty()) continue;   // audit C6-02: size_t underflow on empty path
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

void BDB::set_import_scale(double lu_per_um) {
    if (!(lu_per_um > 0.0))
        throw std::runtime_error("set_import_scale: layout units per micron "
                                 "must be positive, got " +
                                 std::to_string(lu_per_um));
    _lu_per_um = lu_per_um;
    _lu_from_def_units = false;
    _set_meta("lu_per_um", std::to_string(_lu_per_um));
}

void BDB::set_import_scale_from_def_units() {
    // Deferred, not resolved now: the DEF's UNITS is only known once the file
    // is being read, and asking the caller to supply it would reintroduce the
    // out-of-band knowledge this mode exists to remove.
    _lu_from_def_units = true;
}

double BDB::import_scale() const { return _lu_per_um; }

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

// (The old `normalize_def_name` lived here.  It stripped a backslash only
// before `[` or `]`, and lost its last caller when the regex DEF scanner was
// replaced in Phase 3.  `def_io.cpp`'s `unescape` supersedes it and is
// STRICTLY better for the job the comment claimed: DEF escapes the LEADING
// character of a hierarchical name too, so `\\mem\\[0\\]/u1` normalized to
// `\\mem[0]/u1` under the old rule and would NOT have matched the Verilog
// path `mem[0]/u1`, which is what it existed to match.  Pinned by
// test_def_reader.py::test_escaped_names_are_unescaped_once and by
// test_bdb_import_edges.py::test_an_escaped_def_name_matches_its_verilog_path.)

// Map a DEF/LEF orientation token to BDB's component.orient token and whether
// the placed bbox dims swap vs the LEF SIZE. BDB's orient convention is
// (mirror-about-X-first, CCW angle); DEF's pure rotations N/W/S/E coincide,
// but DEF's flip tokens mirror about the Y axis (FN = MY), so they permute
// under BDB's mirror-about-X form: DEF FN<->BDB FS, DEF FS<->BDB FN,
// DEF FE<->BDB FW, DEF FW<->BDB FE. swap_wh is set for the 90/270 rotations.
// A die PORT's direction, as seen from INSIDE the design.
//
// A boundary component is a stand-in for the world outside the die, and a
// port declared INPUT is one the outside world DRIVES — so as a terminal on
// that fictional component it is an output, and an OUTPUT port is something
// the outside world receives.  Storing the declared direction unflipped left
// every input-port net with two receivers and no driver, so the bundler
// dropped it: "N net(s) not placed in any bundle".  A net reaching the die
// edge being silently unrouted is the exact failure the boundary components
// exist to prevent.
static std::string port_dir_inward(const std::string& d) {
    if (d == "INPUT")  return "OUTPUT";
    if (d == "OUTPUT") return "INPUT";
    return d.empty() ? "UNKNOWN" : d;      // INOUT / UNKNOWN pass through
}

static std::pair<std::string,bool> def_orient_to_bdb(const std::string& o) {
    if (o == "N")  return {"N",  false};
    if (o == "S")  return {"S",  false};
    if (o == "W")  return {"W",  true};
    if (o == "E")  return {"E",  true};
    if (o == "FN") return {"FS", false};   // DEF FN (MY) == BDB FS (mirrorX,180)
    if (o == "FS") return {"FN", false};   // DEF FS (MX) == BDB FN (mirrorX,0)
    if (o == "FE") return {"FW", true};    // DEF FE == BDB FW (mirrorX,90)
    if (o == "FW") return {"FE", true};    // DEF FW == BDB FE (mirrorX,270)
    return {"N", false};                   // unknown -> identity
}

static std::vector<std::string> split_ws(const std::string& s) {
    std::istringstream ss(s);
    return {std::istream_iterator<std::string>(ss), {}};
}

// A net named `<base>[<k>]` is bit k of bus <base>.  ONE reading of the
// convention, shared by both importers, so a DEF net and the Verilog net it
// merges with cannot be classified differently — `net_props.bus_name` /
// `bit_index` have been in the schema for this and nothing filled them in.
//
// Purely a naming convention, so it is applied to the STORED name and asks
// nothing about how the name was spelled in its file: a DEF `\w\[0\]` and a
// Verilog `w[0]` both arrive here as `w[0]`.
static bool derive_bus_bit(const std::string& net_name,
                           std::string& bus, int& bit) {
    if (net_name.size() < 4 || net_name.back() != ']') return false;
    const auto br = net_name.rfind('[');
    if (br == std::string::npos || br == 0) return false;
    const std::string inner = net_name.substr(br + 1, net_name.size() - br - 2);
    if (inner.empty()) return false;
    for (char c : inner) if (!std::isdigit((unsigned char)c)) return false;
    bus = net_name.substr(0, br);
    bit = std::stoi(inner);
    return true;
}

// ── LEF adapter ──────────────────────────────────────────────────────────────
// The parsing itself moved to src/lef_io.cpp (Phase 2a).  What remains here is
// the PROJECTION onto what the BDB stores — footprints and one point per pin —
// kept separate from the reading, so widening the model later is an edit here
// and not a change to a parser.
//
// One read, both maps: the two helpers this replaces each opened and scanned
// the file independently.

BDB::LefCells BDB::_lef_cells(const LefLibrary& lib) {
    LefCells sizes;
    for (const auto& m : lib.macros) sizes[m.name] = {m.w, m.h, m.cls};
    return sizes;
}

BDB::LefPins BDB::_lef_pins(const LefLibrary& lib) {
    LefPins result;
    for (const auto& m : lib.macros) {
        for (const auto& p : m.pins) {
            // Power/ground/clock pins are pre-routes, not signal terminals:
            // creating BDB pins for them would put every macro's rails into
            // the netlist.  They are READ (and reachable through the
            // LefLibrary) but not projected here — "not stored" is now a
            // decision at this boundary rather than a silent drop inside a
            // scanner.
            if (p.use == "POWER" || p.use == "GROUND" || p.use == "CLOCK")
                continue;
            double cx = 0, cy = 0;
            if (!p.centroid(cx, cy)) continue;    // no shapes: nothing to place
            // ORIGIN is the offset from the placement point to the geometry
            // origin, so pin coordinates are relative to it.  The scanner this
            // replaces ignored it, putting every pin of a non-zero-ORIGIN
            // macro somewhere it is not — with no symptom until routing lands
            // on nothing.
            result[m.name][p.name] = {cx - m.ox, cy - m.oy,
                                      p.dir.empty() ? "UNKNOWN" : p.dir};
        }
    }
    return result;
}

// A census of what the file said and the model cannot hold, most common
// first.  Stored as meta so it survives the import and can be diffed between
// technology drops; printed so it is seen at least once.
std::string BDB::_lef_unmodelled_census(const LefLibrary& lib) {
    std::map<std::string, int> counts;
    for (const auto& u : lib.unmodelled) ++counts[u.construct];
    std::vector<std::pair<std::string,int>> rows(counts.begin(), counts.end());
    std::sort(rows.begin(), rows.end(), [](const auto& a, const auto& b) {
        if (a.second != b.second) return a.second > b.second;   // count desc
        return a.first < b.first;                               // then name
    });
    std::string s;
    for (const auto& [k, n] : rows) {
        if (!s.empty()) s += ", ";
        s += k + ":" + std::to_string(n);
    }
    return s;
}

// ── DEF importer ─────────────────────────────────────────────────────────────

void BDB::clear_design() {
    // A fresh design load must also drop everything DERIVED from the old
    // design, children before parents: the persisted pipeline rows FK to
    // net/bundle (clear_bundles handles that whole subtree incl. the 'tb:'
    // busterm rows), hier busterms FK to component, and cell_children /
    // cell_pin FK to cell. Without this, re-importing into a BDB that holds
    // a routed checkpoint trips the FK constraints.
    clear_bundles();
    _exec("DELETE FROM busterm; "
          "DELETE FROM pin; DELETE FROM net_props; DELETE FROM net; "
          "DELETE FROM cell_pin; DELETE FROM cell_children; "
          "DELETE FROM cell_layer_share; "         // FKs cell (v20)
          "DELETE FROM component; DELETE FROM cell;");
    // The top-module memo describes the design being wiped, and the GDS
    // export ADOPTS the cell it names as the top structure — stale, it
    // could claim an unrelated cell of the next design that happens to
    // share the old top's name (Codex P2 on #662).
    _exec("DELETE FROM meta WHERE key='verilog_top';");
}

void BDB::add_label_pin(const std::string& net_name, int comp_id,
                        const std::string& pin_name, double px, double py) {
    int net_id = _ensure_net(net_name);
    // A net_props row per net, like the DEF/Verilog importers — without it the
    // recovered net has no HPWL/fanout row and disappears from compute_all()
    // and nets_by_hpwl() even though its pins exist.
    {
        Stmt np(_db, "INSERT OR IGNORE INTO net_props(net_id) VALUES(?)");
        sqlite3_bind_int(np, 1, net_id);
        sqlite3_step(np);
    }
    Stmt s(_db,
        "INSERT OR IGNORE INTO pin(net_id,comp_id,pin_name,dir,px,py)"
        " VALUES(?,?,?,'UNKNOWN',?,?)");
    sqlite3_bind_int   (s, 1, net_id);
    sqlite3_bind_int   (s, 2, comp_id);
    sqlite3_bind_text  (s, 3, pin_name.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_double(s, 4, px);
    sqlite3_bind_double(s, 5, py);
    sqlite3_step(s);
}

DefImportStats BDB::import_def_lef(const std::string& def_path,
                                   const std::string& lef_path) {
    DefImportStats stats;

    // An ABSENT LEF used to be tolerated silently (a probe guarded the read,
    // "as before" — inherited from the old line scanners, not chosen).  That
    // failed in two shapes: with a populated DEF, every cell became a
    // missing-footprint error whose advice ("check that the LEF matches this
    // DEF") misdiagnoses a file that is not there at all; with
    // allow_missing_footprints, a typo'd LEF path silently produced the
    // all-specks floorplan that waiver exists to make explicit-only.  The DEF
    // has thrown on a missing file all along (read_def) — the LEF now matches.
    LefLibrary lef = read_lef(lef_path);
    auto lef_sizes = _lef_cells(lef);
    auto lef_pins  = _lef_pins(lef);

    // ONE parse of the DEF (Phase 3a).  The reader this replaces was a
    // three-state line-at-a-time std::regex machine: a legal multi-line
    // COMPONENTS entry was skipped rather than read, and per-line regex does
    // not survive the 10^6..10^8 lines of a real post-place DEF.
    const DefDesign def = read_def(def_path);
    if (def.units > 0) _units = def.units;

    // Full design wipe incl. derived pipeline rows.
    clear_design();

    Stmt s_comp(_db,
        "INSERT OR IGNORE INTO component(name,cell,depth,x1,y1,x2,y2,is_leaf,orient,is_port)"
        " VALUES(?,?,0,?,?,?,?,1,?,?)");
    Stmt s_net (_db, "INSERT OR IGNORE INTO net(name) VALUES(?)");
    Stmt s_pin (_db,
        "INSERT OR IGNORE INTO pin(net_id,comp_id,pin_name,dir,px,py)"
        " VALUES(?,?,?,?,?,?)");
    // Prepared rather than routed through `_ensure_net_props(id, name)`:
    // this runs once per net on a real design.  Same classification.
    Stmt s_np  (_db, "INSERT OR IGNORE INTO net_props(net_id,bus_name,bit_index)"
                     " VALUES(?,?,?)");
    // One place per importer that turns a net NAME into its bus/bit columns,
    // so the two cannot drift apart on the merge (`derive_bus_bit`).
    auto bind_net_props = [&](int nid, const std::string& nm) {
        std::string bus; int bit = 0;
        sqlite3_bind_int(s_np, 1, nid);
        if (derive_bus_bit(nm, bus, bit)) {
            sqlite3_bind_text(s_np, 2, bus.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_int (s_np, 3, bit);
        } else {
            sqlite3_bind_null(s_np, 2);
            sqlite3_bind_null(s_np, 3);
        }
        sqlite3_step(s_np); sqlite3_reset(s_np);
    };
    Stmt s_find_comp(_db, "SELECT id FROM component WHERE name=?");
    Stmt s_find_net (_db, "SELECT id FROM net WHERE name=?");

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

    // ── the ONE scale decision (docs/internal/engine_units.md) ───────────
    // DEF integers are DBU, LEF numbers are µm; both convert here and nowhere
    // downstream.  In DBU mode dbu_to_lu is exactly 1.0.
    auto dbu_to_lu = [&](double dbu) {
        return _lu_from_def_units ? dbu : dbu * _lu_per_um / double(_units);
    };
    auto um_to_lu  = [&](double um) {
        return um * (_lu_from_def_units ? double(_units) : _lu_per_um);
    };

    if (def.has_die) {
        _die_w = dbu_to_lu(def.die.x2);
        _die_h = dbu_to_lu(def.die.y2);
    }

    // ── COMPONENTS ───────────────────────────────────────────────────────
    std::set<std::string> missing;
    std::set<std::string> comp_names;
    for (const auto& c : def.components) comp_names.insert(c.name);
    for (const auto& c : def.components) {
        double w = 0, h = 0;
        auto cs = lef_sizes.find(c.cell);
        if (cs != lef_sizes.end()) {
            w = um_to_lu(cs->second.w);
            h = um_to_lu(cs->second.h);
        } else {
            // The silent 0.5 x 0.5 µm fallback this replaces turned a
            // wrong-LEF run into a plausible, entirely wrong floorplan — every
            // macro a half-micron speck, every route between them meaningless,
            // and no diagnostic anywhere.  The size is still filled in so the
            // rest of the import can proceed and report ALL the missing cells
            // at once, but the caller is told.
            w = um_to_lu(0.5);
            h = um_to_lu(0.5);
            missing.insert(c.cell);
        }
        auto [orient, swap_wh] = def_orient_to_bdb(c.orient);
        if (swap_wh) std::swap(w, h);
        const double x1 = dbu_to_lu(c.x), y1 = dbu_to_lu(c.y);
        // An `UNPLACED` component has no coordinates, and the reader's
        // defaults are (0,0) — so writing a normal bbox would put EVERY
        // unplaced instance on top of every other one at the die origin, and
        // the pipeline would route that pile as though it were a floorplan
        // (Codex P1 on #649).  A note at the end does not undo geometry the
        // next stage has already believed.
        //
        // The repo already has a convention for "this component has no
        // placement": `import_verilog` writes -1,-1,-1,-1 for a component it
        // knows only from the netlist.  Reusing it keeps ONE meaning of
        // unplaced across both importers rather than adding a second, and it
        // is what `add_blocks_from_bdb` and the floorplan already meet.
        const bool has_pos = c.placed;
        sqlite3_bind_text  (s_comp,1,c.name.c_str(),-1,SQLITE_TRANSIENT);
        sqlite3_bind_text  (s_comp,2,c.cell.c_str(),-1,SQLITE_TRANSIENT);
        sqlite3_bind_double(s_comp,3,has_pos ? x1     : -1);
        sqlite3_bind_double(s_comp,4,has_pos ? y1     : -1);
        sqlite3_bind_double(s_comp,5,has_pos ? x1 + w : -1);
        sqlite3_bind_double(s_comp,6,has_pos ? y1 + h : -1);
        sqlite3_bind_text  (s_comp,7,orient.c_str(),-1,SQLITE_TRANSIENT);
        sqlite3_bind_int   (s_comp,8,0);
        sqlite3_step(s_comp); sqlite3_reset(s_comp);
        ++stats.imported_components;
        if (c.placed) ++stats.placed_components;

        // HALO — a keep-clear margin the placer honoured and the router must
        // too (Phase 3c).  Emitted as a keepout ring in layout units.  Only
        // for a PLACED instance: an unplaced one has no location, so its halo
        // would blockade the die origin.
        if (c.has_halo && has_pos) {
            stats.keepouts.push_back({"", x1 - dbu_to_lu(c.halo_l),
                                      y1 - dbu_to_lu(c.halo_b),
                                      x1 + w + dbu_to_lu(c.halo_r),
                                      y1 + h + dbu_to_lu(c.halo_t),
                                      "HALO of " + c.name});
        }
    }
    stats.declared_components = def.declared_components;
    for (const auto& m : missing) stats.missing_cells.push_back(m);

    // ── PINS → boundary components (Phase 3d) ────────────────────────────
    // Reading PINS is not enough to make them endpoints: PinRow is keyed by
    // component, and endpoint derivation skips any pin whose comp_id is not a
    // component at the bundling depth — so a net reaching the die edge would
    // be SILENTLY incomplete, which is precisely the failure check_design's
    // BUSTERM_OPEN exists to catch, arriving before the audit can see it.
    //
    // Of the plan's two options this is (i): a zero-area boundary COMPONENT
    // per port, behind an explicit `is_port` flag so the fiction is visible
    // in the database and to the audits.  Every downstream stage already
    // understands components; the cost is one fictional instance per port,
    // and the flag is what keeps it from passing as a real one.
    // DEF ports and component instances are SEPARATE namespaces, so a legal
    // design may name both the same.  Inserting the port under its bare name
    // would then be dropped by INSERT OR IGNORE, and the `( PIN name )`
    // lookup below would resolve to the real instance — attaching the
    // boundary connection to the wrong component and never setting is_port
    // (Codex P2 on #647).  An internal name that cannot collide keeps them
    // apart; `port_comp` maps the external name back to it.
    std::map<std::string, std::string> port_comp;
    // The __PORT__ cell rows (opens item 3): the boundary components
    // reference them, and without a cell row the GDS export emits SREFs to
    // a structure that is never defined — and with the port components gone
    // on re-import, every net-name label lands "outside every component"
    // and the whole netlist recovery silently dies with them.  A GDS
    // structure has ONE footprint, so ports are grouped into cells BY SIZE
    // — one shared cell when every port agrees (the measured common case:
    // one PIN template), a numbered sibling per further size (Codex P2:
    // taking independent maxes over a 1x10 and a 10x1 port invents a 10x10
    // footprint that matches neither, and re-import would expand every
    // port to it).  Class order follows DEF order, so the names are
    // deterministic.
    std::vector<std::pair<double,double>> port_sizes;   // size classes
    auto port_cell_for = [&](double w, double h) -> std::string {
        for (size_t i = 0; i < port_sizes.size(); ++i)
            if (std::fabs(port_sizes[i].first - w) < 1e-9 &&
                std::fabs(port_sizes[i].second - h) < 1e-9)
                return i == 0 ? "__PORT__"
                              : "__PORT__" + std::to_string(i + 1);
        port_sizes.push_back({w, h});
        std::string cell = port_sizes.size() == 1
            ? "__PORT__" : "__PORT__" + std::to_string(port_sizes.size());
        Stmt uc(_db, "INSERT INTO cell(name,width,height) VALUES(?1,?2,?3)"
                     " ON CONFLICT(name) DO UPDATE SET"
                     " width=excluded.width, height=excluded.height");
        sqlite3_bind_text  (uc, 1, cell.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_double(uc, 2, w);
        sqlite3_bind_double(uc, 3, h);
        sqlite3_step(uc);
        return cell;
    };
    for (const auto& p : def.pins) {
        if (!p.placed) continue;                 // no location: not an endpoint
        std::string cname = "PIN/" + p.name;
        if (comp_names.count(cname)) {
            // Even the prefixed form can collide with a real instance path.
            int k = 2;
            while (comp_names.count(cname + "#" + std::to_string(k))) ++k;
            cname += "#" + std::to_string(k);
        }
        port_comp[p.name] = cname;
        const double px = dbu_to_lu(p.x), py = dbu_to_lu(p.y);
        double x1 = px, y1 = py, x2 = px, y2 = py;
        for (const auto& r : p.rects) {          // shapes are relative to PLACED
            x1 = std::min(x1, px + dbu_to_lu(r.x1));
            y1 = std::min(y1, py + dbu_to_lu(r.y1));
            x2 = std::max(x2, px + dbu_to_lu(r.x2));
            y2 = std::max(y2, py + dbu_to_lu(r.y2));
        }
        const std::string pcell = port_cell_for(x2 - x1, y2 - y1);
        sqlite3_bind_text  (s_comp,1,cname.c_str(),-1,SQLITE_TRANSIENT);
        sqlite3_bind_text  (s_comp,2,pcell.c_str(),-1,SQLITE_TRANSIENT);
        sqlite3_bind_double(s_comp,3,x1);
        sqlite3_bind_double(s_comp,4,y1);
        sqlite3_bind_double(s_comp,5,x2);
        sqlite3_bind_double(s_comp,6,y2);
        sqlite3_bind_text  (s_comp,7,"N",-1,SQLITE_STATIC);
        sqlite3_bind_int   (s_comp,8,1);
        sqlite3_step(s_comp); sqlite3_reset(s_comp);
        ++stats.port_components;
        ++stats.imported_pins;
    }
    stats.declared_pins = def.declared_pins;

    // ── NETS ─────────────────────────────────────────────────────────────
    // Index instances and ports by name ONCE.  Resolving each connection by
    // scanning `def.components` made the import O(components x connections),
    // and a placed design has O(N) of each — so the reader that exists to
    // survive a 10^6-line DEF would have spent its time walking a vector
    // (Codex P1 on #649).  The DB ids are already looked up this way; these
    // are the two places that were not.
    std::unordered_map<std::string, const DefComponent*> comp_by_name;
    comp_by_name.reserve(def.components.size() * 2);
    for (const auto& c : def.components) comp_by_name.emplace(c.name, &c);
    std::unordered_map<std::string, const DefPin*> pin_by_name;
    pin_by_name.reserve(def.pins.size() * 2);
    for (const auto& p : def.pins) pin_by_name.emplace(p.name, &p);

    for (const auto& n : def.nets) {
        sqlite3_bind_text(s_net,1,n.name.c_str(),-1,SQLITE_TRANSIENT);
        sqlite3_step(s_net); sqlite3_reset(s_net);
        const int nid = get_net_id(n.name);
        if (nid < 0) continue;
        bind_net_props(nid, n.name);
        ++stats.imported_nets;
        for (const auto& cn : n.conns) {
            // A `( PIN <portName> )` connection names a die port, which is a
            // boundary component above; anything else names an instance.
            std::string inst = cn.inst;
            if (cn.is_port()) {
                auto pit = port_comp.find(cn.pin);
                if (pit == port_comp.end()) continue;   // unplaced port
                inst = pit->second;
            }
            const int cid = get_comp_id(inst);
            if (cid < 0) continue;
            std::string dir = "UNKNOWN";
            double ppx = -1, ppy = -1;
            if (cn.is_port()) {
                auto dp = pin_by_name.find(cn.pin);
                if (dp != pin_by_name.end()) {
                    dir = port_dir_inward(dp->second->dir);
                    ppx = dbu_to_lu(dp->second->x);
                    ppy = dbu_to_lu(dp->second->y);
                }
            } else {
                // Resolve the pin offset from the instance's cell in LEF.
                auto ic = comp_by_name.find(inst);
                if (ic != comp_by_name.end()) {
                    const DefComponent& c = *ic->second;
                    auto ci = lef_pins.find(c.cell);
                    if (ci != lef_pins.end()) {
                        auto pi = ci->second.find(cn.pin);
                        if (pi != ci->second.end()) {
                            // DIRECTION comes from the cell, so it is known
                            // whether or not the instance is placed; the
                            // absolute POSITION is not.  Leaving it at -1 says
                            // "unknown", which is what the column already
                            // means, rather than claiming a pin at the origin.
                            dir = pi->second.dir;
                            if (c.placed) {
                                ppx = dbu_to_lu(c.x) + um_to_lu(pi->second.ox);
                                ppy = dbu_to_lu(c.y) + um_to_lu(pi->second.oy);
                            }
                        }
                    }
                }
            }
            sqlite3_bind_int   (s_pin,1,nid);
            sqlite3_bind_int   (s_pin,2,cid);
            sqlite3_bind_text  (s_pin,3,cn.pin.c_str(),-1,SQLITE_TRANSIENT);
            sqlite3_bind_text  (s_pin,4,dir.c_str(),-1,SQLITE_TRANSIENT);
            sqlite3_bind_double(s_pin,5,ppx);
            sqlite3_bind_double(s_pin,6,ppy);
            sqlite3_step(s_pin); sqlite3_reset(s_pin);
        }
    }
    stats.declared_nets = def.declared_nets;

    // ── TRACKS (Phase 3b) ────────────────────────────────────────────────
    for (const auto& tr : def.tracks)
        stats.tracks.push_back({tr.dir, dbu_to_lu(tr.start), dbu_to_lu(tr.step),
                                tr.count, tr.layers});

    // ── BLOCKAGES + macro OBS (Phase 3c) ─────────────────────────────────
    //
    // Only a HARD LAYER blockage is a routing keepout.  The other two kinds
    // in the DEF grammar say something else entirely, and importing them as
    // keepouts asserts a constraint the file never made (Codex P1 on #649):
    //
    //   * a PLACEMENT blockage constrains where CELLS may go.  It carries no
    //     layer, and the session maps a layerless keepout onto EVERY routing
    //     layer, so importing one forbade signal routing through an area the
    //     DEF left completely routable.  The round trip made it acute: Phase
    //     4b emits `PLACEMENT + PARTIAL` blockages over its own corridors, so
    //     BUDA -> DEF -> BUDA turned each planned corridor into a hard
    //     keepout against itself — the exact inversion Phase 4 exists to
    //     avoid.  BUDA has no placement-legalisation stage, so there is
    //     nothing to apply it to; it is recorded as unmodelled.
    //   * `+ PARTIAL <density>` is a DENSITY CAP, not a prohibition.  A hard
    //     keepout over-blocks it in the same direction, so it is recorded
    //     rather than approximated.
    int skipped_placement = 0, skipped_partial = 0;
    for (const auto& b : def.blockages) {
        if (b.is_placement) { skipped_placement += (int)b.rects.size(); continue; }
        if (b.has_density)  { skipped_partial   += (int)b.rects.size(); continue; }
        for (const auto& r : b.rects)
            stats.keepouts.push_back({b.layer, dbu_to_lu(r.x1), dbu_to_lu(r.y1),
                                      dbu_to_lu(r.x2), dbu_to_lu(r.y2),
                                      "BLOCKAGES"});
    }
    // (both counts are folded into the unmodelled census below)
    for (const auto& c : def.components) {
        if (!c.placed) continue;    // no location: its OBS is nowhere
        const LefMacro* m = lef.find_macro(c.cell);
        if (!m) continue;
        // An OBS rect is in the MACRO's frame.  Translating it without
        // applying the instance's orientation puts the keepout somewhere the
        // obstruction is not — blocking empty space while the real
        // obstruction stays routable, which is a physically invalid route
        // the audit cannot see (Codex P1 on #647).
        //
        // The macro's placed extent is [0, w] x [0, h] after rotation, with
        // the lower-left at the DEF placement point (the convention the
        // component bbox above already uses), so the transform maps the
        // macro's own box onto that.
        const double mw = um_to_lu(m->w), mh = um_to_lu(m->h);
        const auto [otok, swap_wh] = def_orient_to_bdb(c.orient);
        const std::string o8 = c.orient.empty() ? "N" : c.orient;
        auto xform = [&](double x, double y, double& ox, double& oy) {
            // x, y are macro-frame layout units with ORIGIN already removed.
            if      (o8 == "N")  { ox = x;        oy = y;        }
            else if (o8 == "S")  { ox = mw - x;   oy = mh - y;   }
            else if (o8 == "FN") { ox = mw - x;   oy = y;        }   // mirror Y
            else if (o8 == "FS") { ox = x;        oy = mh - y;   }   // mirror X
            else if (o8 == "W")  { ox = mh - y;   oy = x;        }   // CCW 90
            else if (o8 == "E")  { ox = y;        oy = mw - x;   }   // CW 90
            else if (o8 == "FW") { ox = y;        oy = x;        }
            else if (o8 == "FE") { ox = mh - y;   oy = mw - x;   }
            else                 { ox = x;        oy = y;        }
        };
        (void)swap_wh;
        for (const auto& o : m->obs)
            for (const auto& r : o.rects) {
                double ax, ay, bx, by;
                xform(um_to_lu(r.x1 - m->ox), um_to_lu(r.y1 - m->oy), ax, ay);
                xform(um_to_lu(r.x2 - m->ox), um_to_lu(r.y2 - m->oy), bx, by);
                stats.keepouts.push_back(
                    {o.layer,
                     dbu_to_lu(c.x) + std::min(ax, bx),
                     dbu_to_lu(c.y) + std::min(ay, by),
                     dbu_to_lu(c.x) + std::max(ax, bx),
                     dbu_to_lu(c.y) + std::max(ay, by),
                     "OBS of " + c.name});
            }
    }
    // Power straps are real metal a signal cannot use.
    for (const auto& w : def.special_wires) {
        if (w.pts.size() < 2 || w.width <= 0) continue;
        const double hw = dbu_to_lu(w.width) / 2.0;
        for (size_t i = 1; i < w.pts.size(); ++i) {
            const double ax = dbu_to_lu(w.pts[i-1].first),  ay = dbu_to_lu(w.pts[i-1].second);
            const double bx = dbu_to_lu(w.pts[i].first),    by = dbu_to_lu(w.pts[i].second);
            stats.keepouts.push_back({w.layer,
                                      std::min(ax,bx)-hw, std::min(ay,by)-hw,
                                      std::max(ax,bx)+hw, std::max(ay,by)+hw,
                                      "SPECIALNET " + w.net});
        }
    }

    // Cell footprints, in the same layout units as the component bboxes.
    {
        // CLASS rides along: it is the only authoritative answer to "hard
        // macro or standard cell", and `import_verilog` needs it to decide
        // which instances of undefined modules belong in the hierarchy.
        Stmt sc(_db,
            "INSERT OR REPLACE INTO cell(name,width,height,cls) VALUES(?,?,?,?)");
        for (auto& [cname, sz] : lef_sizes) {
            sqlite3_bind_text  (sc, 1, cname.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_double(sc, 2, um_to_lu(sz.w));
            sqlite3_bind_double(sc, 3, um_to_lu(sz.h));
            sqlite3_bind_text  (sc, 4, sz.cls.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_step(sc); sqlite3_reset(sc);
        }
    }

    if (_lu_from_def_units) {
        _lu_per_um = double(_units);
        _lu_from_def_units = false;
    }

    Stmt sm(_db, "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)");
    auto save_meta = [&](const char* k, const std::string& v) {
        sqlite3_bind_text(sm,1,k,-1,SQLITE_STATIC);
        sqlite3_bind_text(sm,2,v.c_str(),-1,SQLITE_TRANSIENT);
        sqlite3_step(sm); sqlite3_reset(sm);
    };
    const std::string lef_census = _lef_unmodelled_census(lef);
    save_meta("lef_unmodelled", lef_census);
    save_meta("lef_units_dbu",
              lef.units_dbu > 0 ? std::to_string(lef.units_dbu) : "");
    save_meta("lef_manufacturing_grid",
              lef.manufacturing_grid > 0
                  ? std::to_string(lef.manufacturing_grid) : "");
    {
        std::map<std::string,int> counts;
        for (const auto& u : def.unmodelled) ++counts[u.construct];
        // Blockages we READ but deliberately did not apply belong in the same
        // census: "loud on the unmodelled" is the phase's own gate, and a
        // blockage silently dropped is exactly as misleading as one silently
        // misapplied.
        if (skipped_placement) counts["BLOCKAGES.PLACEMENT"] += skipped_placement;
        if (skipped_partial)   counts["BLOCKAGES.PARTIAL"]   += skipped_partial;
        std::vector<std::pair<std::string,int>> rows(counts.begin(), counts.end());
        std::sort(rows.begin(), rows.end(), [](const auto& a, const auto& b) {
            if (a.second != b.second) return a.second > b.second;
            return a.first < b.first;
        });
        std::string s;
        for (const auto& [k, n] : rows) {
            if (!s.empty()) s += ", ";
            s += k + ":" + std::to_string(n);
        }
        stats.unmodelled = s;
        save_meta("def_unmodelled", s);
    }
    save_meta("units", std::to_string(_units));
    save_meta("lu_per_um", std::to_string(_lu_per_um));
    save_meta("die_w", std::to_string(_die_w));
    save_meta("die_h", std::to_string(_die_h));

    _exec("COMMIT");
    return stats;
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
            "SELECT id,name,cell,COALESCE(parent_id,-1),depth,x1,y1,x2,y2,is_leaf,is_replicated,"
            "COALESCE(orient,'N'),COALESCE(is_port,0)"
            " FROM component ORDER BY id",
            -1, &_q_all_components, nullptr);
    sqlite3_reset(_q_all_components);
    std::vector<ComponentRow> rows;
    while (sqlite3_step(_q_all_components) == SQLITE_ROW) {
        auto q = _q_all_components;
        ComponentRow r;
        r.id           = sqlite3_column_int(q,0);
        r.name         = col_txt(q,1);
        r.cell         = col_txt(q,2);
        r.parent_id    = sqlite3_column_int(q,3);
        r.depth        = sqlite3_column_int(q,4);
        r.x1           = sqlite3_column_double(q,5);
        r.y1           = sqlite3_column_double(q,6);
        r.x2           = sqlite3_column_double(q,7);
        r.y2           = sqlite3_column_double(q,8);
        r.is_leaf      = sqlite3_column_int(q,9);
        r.is_replicated= sqlite3_column_int(q,10);
        r.orient       = col_txt(q,11);
        r.is_port      = sqlite3_column_int(q,12);
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

VerilogImportStats BDB::import_verilog(const std::string& v_path) {
    VerilogImportStats vstats;

    // Cell types the technology says are HARD MACROS.
    //
    // An instance of a module the netlist does not define is a library cell,
    // and dropping those is what stops a million gates becoming a million
    // component rows.  But the test was "the instance name is not
    // backslash-escaped", and a hard macro is normally instantiated with an
    // ordinary name — `fakeram45_256x16 u_mem (...)` — so it read as a
    // standard cell and never joined the hierarchy.  In a DEF+Verilog merge
    // that is quieter and worse than it sounds: the DEF's row survives but
    // is never given a parent, so its CONTAINER ends up with no children,
    // cannot be sized by `derive_container_bboxes`, and the routing
    // interface loses a whole level.
    //
    // The authority on "hard macro or standard cell" is the LEF, and it
    // states the answer outright: `MACRO … CLASS BLOCK` vs `CLASS CORE`.
    // Nothing else here can answer it — NOT the cell name (std cells are
    // all-lowercase in some libraries, e.g. `sky130_fd_sc_hd__inv_1`), and
    // NOT the placement, because a DEF for a gate-level design places every
    // standard cell too (measured: 8 std cells elaborated into pins, nets
    // and busterms, which is exactly the explosion the filter exists to
    // prevent — Codex P1 on #654).
    //
    // An absent CLASS is treated as CORE, which is LEF's own default for it.
    std::unordered_set<std::string> macro_cells;
    {
        Stmt q(_db, "SELECT name, cls FROM cell WHERE cls <> '' AND cls <> 'CORE'");
        while (sqlite3_step(q) == SQLITE_ROW) macro_cells.insert(col_txt(q, 0));
    }

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

    // Parse a port-map value expression into a net REFERENCE: the identifier
    // plus whatever part of it the expression selects.
    //
    // The selector used to be thrown away (`e.resize(e.find('['))`), which
    // made `.a0(w[0]), .a1(w[1])` two connections to one net named `w`.  That
    // is not only the width collapse it was filed as — a 4-bit bus arriving
    // 1 bit wide — it SHORTS the bus: every bit of `w` became the same net,
    // so two pins the netlist keeps apart came back electrically joined, with
    // no diagnostic.  Keeping the selector is what makes a bus a bus, and it
    // makes the Verilog side agree with the DEF side, which has always named
    // bits individually (`w[0]`, `w[1]`).
    //
    // The one trap is that `[` does not always mean a select.  A Verilog
    // ESCAPED identifier runs from `\` to whitespace and takes any brackets
    // with it: `\w[0]` is an identifier *named* `w[0]`, not bit 0 of `w`.
    // The two are told apart here and nowhere else — see `escaped`.
    // Non-nets are recorded on the ref rather than dropped at parse time, so
    // the ones that are OPENS can be counted per elaborated INSTANCE.  The
    // port map is parsed once per module DEFINITION, so counting here would
    // report one open for a module instantiated a hundred times and one for
    // a module never instantiated at all (Codex P2 on #661).
    enum class RefSkip { NONE, CONCAT, CONSTANT, UNCONNECTED, EXPR };
    struct NetRef {
        std::string base;                       // identifier, escapes stripped
        enum Kind { WHOLE, BIT, RANGE } kind = WHOLE;
        int  hi = 0, lo = 0;                    // BIT: hi == lo
        bool escaped = false;                   // brackets are part of the name
        RefSkip skip = RefSkip::NONE;           // NONE = this names a net
    };
    auto parse_net_ref = [](const std::string& expr, RefSkip& skip) -> NetRef {
        skip = RefSkip::NONE;
        NetRef r;
        std::string e = expr;
        while (!e.empty() && std::isspace((unsigned char)e.front())) e.erase(e.begin());
        while (!e.empty() && std::isspace((unsigned char)e.back()))  e.pop_back();
        if (e.empty()) { skip = RefSkip::EXPR; return r; }
        if (std::isdigit((unsigned char)e[0])) { skip = RefSkip::CONSTANT;    return r; }
        if (e[0] == '{')                       { skip = RefSkip::CONCAT;      return r; }
        if (e.size() >= 11 && e.compare(0, 11, "UNCONNECTED") == 0) {
            skip = RefSkip::UNCONNECTED; return r;
        }
        if (e[0] == '\\') {
            // Escaped identifier: the whole token is the name.  Any backslash
            // inside is an escape too (a DEF writes `\w\[0\]`), and stripping
            // it wherever it appears is what makes the two readers agree —
            // see the `normalize_def_name` removal in #654.
            r.escaped = true;
            e.erase(std::remove(e.begin(), e.end(), '\\'), e.end());
            r.base = e;
            return r;
        }
        const auto br = e.find('[');
        if (br == std::string::npos || e.back() != ']') { r.base = e; return r; }
        const std::string inner = e.substr(br + 1, e.size() - br - 2);
        r.base = e.substr(0, br);
        while (!r.base.empty() && std::isspace((unsigned char)r.base.back()))
            r.base.pop_back();
        const auto colon = inner.find(':');
        // `w[ 0 ]` and `w[3 : 0]` are legal and their indices ARE literal, so
        // an untrimmed digit test classified them as unresolvable expressions
        // and opened the pin (Codex P2 on #661).
        auto trim = [](std::string s) {
            while (!s.empty() && std::isspace((unsigned char)s.front())) s.erase(s.begin());
            while (!s.empty() && std::isspace((unsigned char)s.back()))  s.pop_back();
            return s;
        };
        auto all_digits = [](const std::string& s) {
            if (s.empty()) return false;
            for (char c : s) if (!std::isdigit((unsigned char)c)) return false;
            return true;
        };
        if (colon == std::string::npos) {
            const std::string one = trim(inner);
            if (!all_digits(one)) { skip = RefSkip::EXPR; return r; }    // w[i]
            r.kind = NetRef::BIT;
            r.hi = r.lo = std::stoi(one);
        } else {
            const std::string a = trim(inner.substr(0, colon)),
                              b = trim(inner.substr(colon + 1));
            if (!all_digits(a) || !all_digits(b)) { skip = RefSkip::EXPR; return r; }
            r.kind = NetRef::RANGE;
            r.hi = std::stoi(a); r.lo = std::stoi(b);
        }
        return r;
    };

    // Parse ".port (net), ..." text → vector of (port, net reference) pairs
    auto parse_portmap = [&](const std::string& text)
        -> std::vector<std::pair<std::string,NetRef>>
    {
        std::vector<std::pair<std::string,NetRef>> result;
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
            RefSkip skip;
            NetRef ref = parse_net_ref(text.substr(i, k - i - 1), skip);
            ref.skip = skip;
            if (port.empty()) { i = k; continue; }
            // A CONCAT or EXPR is carried through rather than dropped: it is
            // an OPEN in the imported design, and an open belongs to each
            // ELABORATED instance, not to the module text.  A deliberate
            // non-connection (constant, UNCONNECTED) is not an open and is
            // dropped here as before.
            if (skip == RefSkip::NONE && !ref.base.empty())
                result.emplace_back(port, ref);
            else if (skip == RefSkip::CONCAT || skip == RefSkip::EXPR)
                result.emplace_back(port, ref);
            i = k;
        }
        return result;
    };

    // ── Phase 2: parse module bodies ─────────────────────────────────────────

    struct VInst {
        std::string cell, name;   // cell type, instance name (both unescaped)
        std::vector<std::pair<std::string,NetRef>> portmap;
    };
    struct VMod {
        std::vector<VInst> insts;
        std::unordered_map<std::string, std::string> port_dirs;
        // Declared width per port: `input [3:0] a` is 4, a bare `input a` is
        // 1.  Needed to know how much of a part-select a port can actually
        // take — Verilog width-adapts, so `.s(w[3:0])` on a SCALAR `s`
        // connects bit 0 alone (Codex P1 on #661).
        std::unordered_map<std::string, int> port_width;
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
            // Each clause runs to the next DIRECTION keyword, not to the next
            // `;`/`)`.  An ANSI header — `module top (input a, output b);` —
            // is one statement, so stopping only at the terminator let the
            // first `input` swallow `a, output b` and every port in the
            // module was recorded INPUT (Codex P2 on #650).  Pre-existing,
            // and it reaches `cell_pin` directions for any ANSI-header
            // netlist, so an OUTPUT pin was indexed as an INPUT.
            static const std::regex decl_re(
                R"(\b(input|output|inout)\b((?:(?!\b(?:input|output|inout)\b)[^;)])*))");
            auto begin = std::sregex_iterator(text.begin(), text.end(), decl_re);
            auto end = std::sregex_iterator();
            for (auto it = begin; it != end; ++it) {
                std::string dir = (*it)[1].str();
                std::transform(dir.begin(), dir.end(), dir.begin(),
                               [](unsigned char c){ return std::toupper(c); });
                std::string rest = (*it)[2].str();
                // The clause's range comes once, before the names, and
                // applies to all of them: `input [3:0] a, b;` declares two
                // 4-bit ports.  Read it BEFORE the strip below throws every
                // bracket away.  Only a literal `[<n>:<m>]` counts — an
                // escaped identifier can carry brackets with no colon.
                int decl_w = 1;
                {
                    static const std::regex range_re(
                        R"(^\s*\[\s*(\d+)\s*:\s*(\d+)\s*\])");
                    std::smatch rm;
                    if (std::regex_search(rest, rm, range_re)) {
                        const int a = std::stoi(rm[1].str()),
                                  b = std::stoi(rm[2].str());
                        decl_w = std::abs(a - b) + 1;
                    }
                }
                rest = std::regex_replace(rest, std::regex(R"(\[[^\]]+\])"), " ");
                rest = std::regex_replace(rest, std::regex(R"(\b(wire|reg|logic|signed|unsigned)\b)"), " ");
                std::stringstream ss(rest);
                std::string item;
                while (std::getline(ss, item, ',')) {
                    std::string name = clean_port_name(item);
                    if (!name.empty()) {
                        mod_lib[cur_mod].port_dirs[name] = dir;
                        mod_lib[cur_mod].port_width[name] = decl_w;
                    }
                }
            }
        };

        // Distinct cell names already named in the skip census, so the report
        // lists eight KINDS of cell rather than the first eight instances.
        std::unordered_set<std::string> skipped_seen;

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
            if (!is_defined) {
                // An instance of a module the netlist does not define: a
                // library cell.  Keep it only on a reason, in this order.
                //
                // 1. The TECHNOLOGY says the cell is a hard macro (a LEF
                //    CLASS other than CORE).  Not a guess, and independent
                //    of how the instance name is spelled — which is what
                //    carries a macro through a DEF+Verilog merge.
                // 2. Otherwise the legacy heuristic, unchanged, for an
                //    import with no LEF to ask: a backslash-escaped instance
                //    name AND a cell name containing a lowercase letter
                //    (`fakeram45_*` yes, `DFFR_X1` no — Genus writes escaped
                //    names for both).
                //
                // Anything else is skipped and COUNTED.  The count is the
                // point: rule 2 is still a heuristic, and a silently missing
                // instance is indistinguishable from a design that never had
                // one.
                bool keep = macro_cells.count(cell) > 0;
                if (!keep && esc_inst) {
                    for (char c : cell)
                        if (std::islower((unsigned char)c)) { keep = true; break; }
                }
                if (!keep) {
                    ++vstats.skipped_library_cells;
                    if (skipped_seen.insert(cell).second) {
                        ++vstats.skipped_kinds;
                        if (vstats.skipped_cells.size() < 8)
                            vstats.skipped_cells.push_back(cell);
                    }
                    return;
                }
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
    if (top_mod.empty()) return vstats;
    vstats.top_module = top_mod;
    // Recorded so the GDS export can adopt this cell as the TOP structure's
    // name instead of emitting it as an empty orphan beside a synthetic
    // 'top' (opens item 3) — the netlist declared it top; believe it.
    meta_set("verilog_top", top_mod);

    // ── Phase 4: elaborate hierarchy → BDB ───────────────────────────────────
    // Preserve component placement from any prior import_def_lef call.
    // Clear only net/pin tables.
    // The die's own ports, saved before the wipe and restored after
    // elaboration.
    //
    // Elaboration walks INSTANCES, and a top-level port is not one, so the
    // boundary components `import_def_lef` synthesizes from DEF `PINS`
    // (Phase 3d) would come out of the rebuild with no pin rows at all —
    // components that look fine while a net reaching the die edge is
    // silently endpoint-less and the bundler drops it as "not placed in any
    // bundle".  That is exactly the silent open those components exist to
    // prevent, reappearing in the DEF+Verilog merge they were built for.
    //
    // Saved rather than RECONSTRUCTED: the DEF stated each port's position
    // and direction, and reproducing them from the component bbox gets both
    // wrong.  The bbox is the union of the PLACED point and every shape, so
    // its midpoint is not the pin for an asymmetric or multi-shape port and
    // can land in empty space; and the direction would come from
    // `port_dirs`, which is the netlist's word for something the DEF already
    // said (Codex P2 x2 on #650).  Nets are keyed by NAME because their ids
    // do not survive the rebuild.
    struct SavedPortPin {
        int comp_id; std::string net, pin, dir; double px, py;
    };
    std::vector<SavedPortPin> saved_port_pins;
    {
        Stmt q(_db,
            "SELECT p.comp_id, n.name, p.pin_name, p.dir, p.px, p.py"
            "  FROM pin p JOIN net n ON n.id = p.net_id"
            "             JOIN component c ON c.id = p.comp_id"
            " WHERE c.is_port = 1"
            " ORDER BY p.comp_id, p.pin_name");   // deterministic restore
        while (sqlite3_step(q) == SQLITE_ROW)
            saved_port_pins.push_back({
                sqlite3_column_int(q, 0), col_txt(q, 1), col_txt(q, 2),
                col_txt(q, 3), sqlite3_column_double(q, 4),
                sqlite3_column_double(q, 5)});
    }

    _exec("DELETE FROM pin; DELETE FROM net_props; DELETE FROM net;");

    // UPSERT: keep existing x1/y1/x2/y2 (from DEF), update hierarchy fields
    Stmt s_comp(_db, R"(
        INSERT INTO component(name,cell,parent_id,depth,x1,y1,x2,y2,is_leaf)
        VALUES(?,?,?,?,-1,-1,-1,-1,?)
        ON CONFLICT(name) DO UPDATE SET
          cell=excluded.cell, parent_id=excluded.parent_id,
          depth=excluded.depth,
          -- `?6` is the leaf verdict for a row the DEF already PLACED; the
          -- `excluded` value is the verdict for a netlist-only row.  See
          -- the table above `leaf_placed` at the call site for why the two
          -- differ, and why "was it placed" alone is NOT the discriminator.
          is_leaf=CASE WHEN component.x1 >= 0 THEN ?6 ELSE excluded.is_leaf END
    )");
    Stmt s_net (_db, "INSERT OR IGNORE INTO net(name) VALUES(?)");
    Stmt s_pin (_db,
        "INSERT OR IGNORE INTO pin(net_id,comp_id,pin_name,dir,px,py)"
        " VALUES(?,?,?,?,?,?)");
    // Prepared rather than routed through `_ensure_net_props(id, name)`:
    // this runs once per net on a real design.  Same classification.
    Stmt s_np  (_db, "INSERT OR IGNORE INTO net_props(net_id,bus_name,bit_index)"
                     " VALUES(?,?,?)");
    // One place per importer that turns a net NAME into its bus/bit columns,
    // so the two cannot drift apart on the merge (`derive_bus_bit`).
    auto bind_net_props = [&](int nid, const std::string& nm) {
        std::string bus; int bit = 0;
        sqlite3_bind_int(s_np, 1, nid);
        if (derive_bus_bit(nm, bus, bit)) {
            sqlite3_bind_text(s_np, 2, bus.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_int (s_np, 3, bit);
        } else {
            sqlite3_bind_null(s_np, 2);
            sqlite3_bind_null(s_np, 3);
        }
        sqlite3_step(s_np); sqlite3_reset(s_np);
    };
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
                           int par_id, int depth, bool is_leaf,
                           bool is_leaf_if_placed) -> int {
        sqlite3_bind_text(s_comp,1,path.c_str(),-1,SQLITE_TRANSIENT);
        sqlite3_bind_text(s_comp,2,cell.c_str(),-1,SQLITE_TRANSIENT);
        if (par_id >= 0) sqlite3_bind_int(s_comp,3,par_id);
        else             sqlite3_bind_null(s_comp,3);
        sqlite3_bind_int(s_comp,4,depth);
        sqlite3_bind_int(s_comp,5,is_leaf ? 1 : 0);
        sqlite3_bind_int(s_comp,6,is_leaf_if_placed ? 1 : 0);
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
        bind_net_props(id, name);
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
            // Two verdicts, because a netlist-only row and a DEF-PLACED row
            // are asking different questions of the same module:
            //
            //   module          netlist-only     DEF-placed
            //   ------------------------------------------------
            //   undefined       leaf             leaf
            //   has instances   container        container
            //   defined, EMPTY  container [1]    leaf      [2]
            //
            // [1] the landed rule (features/bdb_import.feature): declaring a
            //     module makes it a hierarchy level even with an empty body.
            // [2] an empty body is also how a netlist gives a hard macro its
            //     port directions while leaving geometry to the LEF.  A row
            //     the DEF placed from a LEF FOOTPRINT is that macro, and
            //     calling it a container stops its footprint blocking low
            //     layers and sends busterm derivation into a cell with no
            //     children.
            //
            // Note what is NOT the discriminator: placement alone.  A DEF
            // may place hierarchy envelopes as well as their descendants
            // (features/bdb_combined.feature does exactly that for ai/bi/ci),
            // and those have instances — so they stay containers on both
            // sides of the table (Codex P1 on #650).
            auto sub = mod_lib.find(vi.cell);
            const bool defined = (sub != mod_lib.end());
            const bool empty_body = defined && sub->second.insts.empty();
            bool is_leaf = !defined;
            bool is_leaf_if_placed = !defined || empty_body;
            // Instance path uses unescaped name (matches DEF convention)
            std::string inst_path = path.empty() ? vi.name : path + "/" + vi.name;

            int cid = upsert_comp(inst_path, vi.cell, par_id, depth, is_leaf,
                                  is_leaf_if_placed);
            ++vstats.elaborated;

            // Build child context and create pin records
            Ctx child_ctx;
            for (auto& [port, ref] : vi.portmap) {
                // Resolve the IDENTIFIER through the current ctx (port
                // connections from the parent), then re-apply the selector.
                // Resolving base-then-select is what makes a bit-select work
                // across a hierarchy boundary: with `.a(w)` in the parent,
                // ctx maps `a` to `…/w`, so the child's own `a[0]` lands on
                // `…/w[0]` — the same net the parent's `.x(w[0])` names.
                auto it = ctx.find(ref.base);
                const std::string qbase = (it != ctx.end())
                    ? it->second
                    : (path.empty() ? ref.base : path + "/" + ref.base);

                auto add_pin = [&](const std::string& qnet) {
                    const int nid = get_net(qnet);
                    if (nid <= 0 || cid <= 0) return;
                    sqlite3_bind_int   (s_pin,1,nid);
                    sqlite3_bind_int   (s_pin,2,cid);
                    sqlite3_bind_text  (s_pin,3,port.c_str(),-1,SQLITE_TRANSIENT);
                    sqlite3_bind_text  (s_pin,4,"UNKNOWN",-1,SQLITE_STATIC);
                    sqlite3_bind_double(s_pin,5,-1.0);
                    sqlite3_bind_double(s_pin,6,-1.0);
                    sqlite3_step(s_pin); sqlite3_reset(s_pin);
                };
                auto bit_of = [&](int b) {
                    return qbase + "[" + std::to_string(b) + "]";
                };

                if (ref.kind == NetRef::BIT) {
                    // The port names one bit, so the port IS that bit: a child
                    // referring to the port plainly must land on it.
                    child_ctx[port] = bit_of(ref.lo);
                    add_pin(child_ctx[port]);
                    ++vstats.bit_selects;
                } else if (ref.kind == NetRef::RANGE) {
                    // A part-select on a port BUDA models as one pin.  Each
                    // bit the PORT can actually take gets a pin row (same
                    // pin_name — the pin key is (net,comp,pin), so that is
                    // representable), which keeps the connection to those
                    // bits rather than inventing a net called `w[3:0]` that
                    // the netlist never declared.
                    //
                    // How many bits the port can take is the formal's
                    // DECLARED width, not the select's: Verilog width-adapts,
                    // so `.s(w[3:0])` on a scalar `s` connects bit 0 and
                    // nothing else.  Expanding to the select's width regard-
                    // less put four nets on that one pin and reported three
                    // connections the netlist does not have (Codex P1 on
                    // #661).  Bits are taken LSB-first because that is the
                    // end Verilog aligns.
                    //
                    // An UNKNOWN formal width (an undefined module — a macro
                    // kept by the library-cell filter, which declares no
                    // ports here) is not guessed either way.  Bit 0 is
                    // connected because it is connected for EVERY width >= 1,
                    // and the rest is reported rather than invented.
                    const int nsel = std::abs(ref.hi - ref.lo) + 1;
                    int take = nsel;
                    bool width_known = false;
                    if (defined) {
                        auto pw = sub->second.port_width.find(port);
                        if (pw != sub->second.port_width.end()) {
                            width_known = true;
                            take = std::min(nsel, pw->second);
                        }
                    }
                    if (!width_known) { take = 1; ++vstats.unsized_part_selects; }

                    const int base_bit = std::min(ref.hi, ref.lo);
                    for (int k = 0; k < take; ++k) add_pin(bit_of(base_bit + k));

                    // The child context: a single connected bit makes the
                    // port that bit (as in the BIT case).  Otherwise the
                    // resolved BASE, so a child's `a[k]` composes to `w[k]` —
                    // exact for the usual `w[N:0]` and OFF BY `lo` otherwise,
                    // which is reported rather than quietly mapped.
                    child_ctx[port] = (take == 1) ? bit_of(base_bit) : qbase;
                    ++vstats.part_selects;
                    if (take > 1 && ref.lo != 0 && defined &&
                        !sub->second.insts.empty())
                        ++vstats.offset_part_selects;
                } else if (ref.skip != RefSkip::NONE) {
                    // A concatenation or a non-literal select: an OPEN, and
                    // one per ELABORATED instance — a module instantiated a
                    // hundred times loses the connection a hundred times.
                    ++vstats.unresolved_conns;
                } else {
                    child_ctx[port] = qbase;
                    add_pin(qbase);
                }
            }

            if (!is_leaf)
                elaborate(vi.cell, inst_path, cid, depth + 1, child_ctx);
        }
    };

    elaborate(top_mod, "", -1, 0, {});

    // ── the die's own ports, restored ────────────────────────────────────
    // Exactly the rows the DEF wrote — position and direction included.
    // See the save site above the pin-table wipe for why they are carried
    // rather than reconstructed.
    for (const auto& sp : saved_port_pins) {
        const int nid = get_net(sp.net);
        if (nid <= 0) continue;
        sqlite3_bind_int   (s_pin, 1, nid);
        sqlite3_bind_int   (s_pin, 2, sp.comp_id);
        sqlite3_bind_text  (s_pin, 3, sp.pin.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_text  (s_pin, 4, sp.dir.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_double(s_pin, 5, sp.px);
        sqlite3_bind_double(s_pin, 6, sp.py);
        sqlite3_step(s_pin); sqlite3_reset(s_pin);
    }
    _exec("COMMIT");
    return vstats;
}

// ── Mutations ─────────────────────────────────────────────────────────────────

void BDB::move_comp(const std::string& name, double x, double y) {
    // Move the component's lower-left corner to (x, y).  Implemented as a pure
    // translation so the SUBTREE and its (absolute) pin positions ride along —
    // the old in-place bbox rewrite left pin.px/py stale, then compute_hpwl()
    // recomputed HPWL from the stale pins: refreshed-looking but wrong values
    // (audit C6-08).  translate_comp shifts pins + subtree and runs compute_hpwl.
    double dx, dy;
    { Stmt q(_db, "SELECT x1, y1 FROM component WHERE name=?");
      sqlite3_bind_text(q, 1, name.c_str(), -1, SQLITE_TRANSIENT);
      if (sqlite3_step(q) != SQLITE_ROW)
          throw std::runtime_error("move_comp: not found: " + name);
      dx = x - sqlite3_column_double(q, 0);
      dy = y - sqlite3_column_double(q, 1); }
    translate_comp(name, dx, dy);
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

int BDB::derive_container_bboxes(double margin,
                                 std::vector<std::string>* unresolved) {
    // Deepest-FIRST, so a container of containers is resolved from children
    // that were themselves just resolved.  Doing it the other way round
    // would give an outer container the union of whatever happened to be
    // placed already — a box that is right only by accident.
    auto comps = all_components();
    std::unordered_map<int, std::vector<int>> kids;   // parent id -> child ids
    std::unordered_map<int, size_t> index;
    for (size_t i = 0; i < comps.size(); ++i) {
        index[comps[i].id] = i;
        if (comps[i].parent_id >= 0) kids[comps[i].parent_id].push_back(comps[i].id);
    }
    std::vector<size_t> order(comps.size());
    for (size_t i = 0; i < comps.size(); ++i) order[i] = i;
    std::sort(order.begin(), order.end(), [&](size_t a, size_t b) {
        if (comps[a].depth != comps[b].depth) return comps[a].depth > comps[b].depth;
        return comps[a].name < comps[b].name;      // deterministic
    });

    int placed = 0;
    for (size_t oi : order) {
        ComponentRow& c = comps[oi];
        if (c.x1 >= 0) continue;                   // already has a position
        auto kit = kids.find(c.id);
        if (kit == kids.end() || kit->second.empty()) {
            // Not a container at all — an unplaced LEAF is a real fact about
            // the design (a netlist instance the DEF never placed), and
            // inventing a box for it would be a lie, not a convenience.
            continue;
        }
        bool any = false;
        double x1 = 0, y1 = 0, x2 = 0, y2 = 0;
        for (int kid : kit->second) {
            const ComponentRow& k = comps[index[kid]];
            if (k.x1 < 0) continue;                // still unplaced: skip
            if (!any) { x1 = k.x1; y1 = k.y1; x2 = k.x2; y2 = k.y2; any = true; }
            else {
                x1 = std::min(x1, k.x1); y1 = std::min(y1, k.y1);
                x2 = std::max(x2, k.x2); y2 = std::max(y2, k.y2);
            }
        }
        if (!any) {
            if (unresolved) unresolved->push_back(c.name);
            continue;
        }
        x1 -= margin; y1 -= margin; x2 += margin; y2 += margin;
        set_comp_bbox(c.name, x1, y1, x2, y2);
        c.x1 = x1; c.y1 = y1; c.x2 = x2; c.y2 = y2;   // visible to its parent
        // A container is not a leaf, whatever the netlist reader guessed:
        // it has children, and the routing model treats a leaf footprint as
        // a LOW-layer keepout while a container is transparent to them.
        set_comp_is_leaf(c.name, false);
        ++placed;
    }

    // Write the derived size back to the CELL definition (opens item 3).
    // A merge-created container cell carries width=height=0 — neither input
    // file states a size for it — so the GDS export emitted its structure
    // with no outline and warned that every placement's bbox differs from
    // the (0x0) cell footprint.  The rule here was MEASURED before it was
    // chosen: instances of a derived cell are size-uniform (containers come
    // from congruent templates), so the common instance size IS the cell
    // size.  A cell whose instances disagree, or has an unplaced instance,
    // is left 0x0 — the export's dim-mismatch warning keeps that gap
    // visible, and inventing a max would claim a footprint no instance has.
    // The SQL guard (width=0 AND height=0) means a real size — LEF SIZE, a
    // hand resize_cell — is never overwritten.
    {
        std::unordered_map<std::string, std::pair<double,double>> common;
        std::unordered_set<std::string> bad;
        auto swapped = [](const std::string& o) {
            return o == "E" || o == "W" || o == "FE" || o == "FW";
        };
        for (const auto& c : comps) {
            if (c.x1 < 0) { bad.insert(c.cell); continue; }   // unplaced
            double w = c.x2 - c.x1, h = c.y2 - c.y1;
            if (swapped(c.orient)) std::swap(w, h);           // cell frame
            auto it = common.find(c.cell);
            if (it == common.end()) common[c.cell] = {w, h};
            else if (std::fabs(it->second.first - w) > 1e-6 ||
                     std::fabs(it->second.second - h) > 1e-6)
                bad.insert(c.cell);
        }
        Stmt u(_db, "UPDATE cell SET width=?2, height=?3"
                    " WHERE name=?1 AND width=0 AND height=0");
        for (const auto& [cell, wh] : common) {
            if (bad.count(cell)) continue;
            sqlite3_bind_text  (u, 1, cell.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_double(u, 2, wh.first);
            sqlite3_bind_double(u, 3, wh.second);
            sqlite3_step(u); sqlite3_reset(u);
        }
    }
    return placed;
}

void BDB::resize_cell(const std::string& cell, double w, double h) {
    // Keep the cell definition in sync.  Upsert, NOT INSERT OR REPLACE:
    // REPLACE deletes + re-inserts the row, which would reset non-geometry
    // cell attributes (bottom_up) as a side effect of a resize.
    Stmt uc(_db, "INSERT INTO cell(name,width,height) VALUES(?,?,?)"
                 " ON CONFLICT(name) DO UPDATE SET"
                 " width=excluded.width, height=excluded.height");
    sqlite3_bind_text  (uc, 1, cell.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_double(uc, 2, w);
    sqlite3_bind_double(uc, 3, h);
    sqlite3_step(uc);
    // Update every instance's bounding box.  A 90/270-rotated instance
    // (orient E/W/FE/FW) carries SWAPPED dimensions — writing w/h straight
    // would break the bbox↔orient invariant the v13 GDS round-trip depends
    // on (audit C6-03).
    Stmt u(_db, "UPDATE component SET"
                " x2 = x1 + CASE WHEN orient IN ('E','W','FE','FW')"
                "               THEN ?2 ELSE ?1 END,"
                " y2 = y1 + CASE WHEN orient IN ('E','W','FE','FW')"
                "               THEN ?1 ELSE ?2 END"
                " WHERE cell=?3");
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
                  double x1, double y1, double x2, double y2, bool is_leaf,
                  const std::string& orient) {
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
        INSERT INTO component(name,cell,parent_id,depth,x1,y1,x2,y2,is_leaf,orient)
        VALUES(?,?,?,?,?,?,?,?,?,?)
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
    sqlite3_bind_text  (ins, 10, orient.c_str(), -1, SQLITE_TRANSIENT);
    if (sqlite3_step(ins) != SQLITE_DONE)
        throw std::runtime_error("add_comp: insert failed (name exists?): " + name);
    int id = (int)sqlite3_last_insert_rowid(_db);
    compute_hpwl();
    return id;
}

void BDB::add_cell(const std::string& name, double w, double h) {
    // Upsert (not REPLACE) so re-declaring a cell's size keeps its bottom_up
    // flag and layer band.
    Stmt ins(_db, "INSERT INTO cell(name,width,height) VALUES(?,?,?)"
                  " ON CONFLICT(name) DO UPDATE SET"
                  " width=excluded.width, height=excluded.height");
    sqlite3_bind_text  (ins, 1, name.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_double(ins, 2, w);
    sqlite3_bind_double(ins, 3, h);
    if (sqlite3_step(ins) != SQLITE_DONE)
        throw std::runtime_error("add_cell: failed for: " + name);
}

std::vector<CellRow> BDB::all_cells() const {
    Stmt q(_db, "SELECT name, width, height, bottom_up, layer_cap, layer_floor"
                " FROM cell ORDER BY name");
    std::vector<CellRow> result;
    while (sqlite3_step(q) == SQLITE_ROW)
        result.push_back({ (const char*)sqlite3_column_text(q,0),
                           sqlite3_column_double(q,1),
                           sqlite3_column_double(q,2),
                           sqlite3_column_int(q,3) != 0,
                           sqlite3_column_int(q,4),
                           sqlite3_column_int(q,5) });
    return result;
}

void BDB::set_cell_bottom_up(const std::string& cell, bool on) {
    Stmt u(_db, "UPDATE cell SET bottom_up=? WHERE name=?");
    sqlite3_bind_int (u, 1, on ? 1 : 0);
    sqlite3_bind_text(u, 2, cell.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_step(u);
    if (sqlite3_changes(_db) == 0)
        throw std::runtime_error("set_cell_bottom_up: cell not defined: " + cell);
}

bool BDB::cell_bottom_up(const std::string& cell) const {
    Stmt q(_db, "SELECT bottom_up FROM cell WHERE name=?");
    sqlite3_bind_text(q, 1, cell.c_str(), -1, SQLITE_TRANSIENT);
    return sqlite3_step(q) == SQLITE_ROW && sqlite3_column_int(q, 0) != 0;
}

std::vector<std::string> BDB::bottom_up_cells() const {
    Stmt q(_db, "SELECT name FROM cell WHERE bottom_up!=0 ORDER BY name");
    std::vector<std::string> result;
    while (sqlite3_step(q) == SQLITE_ROW)
        result.push_back((const char*)sqlite3_column_text(q, 0));
    return result;
}

void BDB::set_cell_layer_band(const std::string& cell, int floor, int cap) {
    Stmt u(_db, "UPDATE cell SET layer_floor=?, layer_cap=? WHERE name=?");
    sqlite3_bind_int (u, 1, floor);
    sqlite3_bind_int (u, 2, cap);
    sqlite3_bind_text(u, 3, cell.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_step(u);
    if (sqlite3_changes(_db) == 0)
        throw std::runtime_error("set_cell_layer_band: cell not defined: " + cell);
}

std::pair<int,int> BDB::cell_layer_band(const std::string& cell) const {
    Stmt q(_db, "SELECT layer_floor, layer_cap FROM cell WHERE name=?");
    sqlite3_bind_text(q, 1, cell.c_str(), -1, SQLITE_TRANSIENT);
    if (sqlite3_step(q) == SQLITE_ROW)
        return { sqlite3_column_int(q, 0), sqlite3_column_int(q, 1) };
    return { -1, -1 };
}

std::vector<std::string> BDB::layer_capped_cells() const {
    Stmt q(_db, "SELECT name FROM cell WHERE layer_cap>=0 ORDER BY name");
    std::vector<std::string> result;
    while (sqlite3_step(q) == SQLITE_ROW)
        result.push_back((const char*)sqlite3_column_text(q, 0));
    return result;
}

std::vector<std::pair<std::string,std::string>> BDB::cell_child_edges() const {
    Stmt q(_db, "SELECT DISTINCT parent_cell, child_cell FROM cell_children"
                " ORDER BY parent_cell, child_cell");
    std::vector<std::pair<std::string,std::string>> result;
    while (sqlite3_step(q) == SQLITE_ROW)
        result.emplace_back((const char*)sqlite3_column_text(q, 0),
                            (const char*)sqlite3_column_text(q, 1));
    return result;
}

void BDB::set_cell_layer_share(const std::string& cell, int layer_id, double share) {
    {   // The FK target must exist (matches set_cell_layer_band's contract).
        Stmt q(_db, "SELECT 1 FROM cell WHERE name=?");
        sqlite3_bind_text(q, 1, cell.c_str(), -1, SQLITE_TRANSIENT);
        if (sqlite3_step(q) != SQLITE_ROW)
            throw std::runtime_error("set_cell_layer_share: cell not defined: " + cell);
    }
    if (share <= 0.0) {
        Stmt d(_db, "DELETE FROM cell_layer_share WHERE cell=? AND layer_id=?");
        sqlite3_bind_text(d, 1, cell.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_int (d, 2, layer_id);
        sqlite3_step(d);
        return;
    }
    Stmt s(_db, "INSERT INTO cell_layer_share(cell,layer_id,share) VALUES(?,?,?)"
                " ON CONFLICT(cell,layer_id) DO UPDATE SET share=excluded.share");
    sqlite3_bind_text  (s, 1, cell.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int   (s, 2, layer_id);
    sqlite3_bind_double(s, 3, share);
    sqlite3_step(s);
}

std::vector<std::pair<int,double>> BDB::cell_layer_shares(const std::string& cell) const {
    Stmt q(_db, "SELECT layer_id, share FROM cell_layer_share WHERE cell=?"
                " ORDER BY layer_id");
    sqlite3_bind_text(q, 1, cell.c_str(), -1, SQLITE_TRANSIENT);
    std::vector<std::pair<int,double>> result;
    while (sqlite3_step(q) == SQLITE_ROW)
        result.emplace_back(sqlite3_column_int(q, 0), sqlite3_column_double(q, 1));
    return result;
}

std::vector<std::string> BDB::layer_share_cells() const {
    Stmt q(_db, "SELECT DISTINCT cell FROM cell_layer_share ORDER BY cell");
    std::vector<std::string> result;
    while (sqlite3_step(q) == SQLITE_ROW)
        result.push_back((const char*)sqlite3_column_text(q, 0));
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

// ── Orientation tokens (component.orient, v12) ───────────────────────────────
// Must match gds_io.cpp's transform_to_orient/orient_angle: an 8-orientation
// token is (mirror-about-X-first, CCW angle). N/W/S/E = 0/90/180/270, F* =
// mirrored. rotate_comp/flip_comp compose the applied transform onto the ROOT
// instance's orient so bbox and orient stay consistent (a faithful GDS SREF
// re-emit needs both). Descendants rotate rigidly with the root, so their
// orientation relative to their own parent is unchanged.
void orient_parse(const std::string& o, bool& mirror, int& angle) {
    mirror = (!o.empty() && o[0] == 'F');
    const std::string t = mirror ? o.substr(1) : o;
    angle = (t == "W") ? 90 : (t == "S") ? 180 : (t == "E") ? 270 : 0;
}
std::string orient_format(bool mirror, int angle) {
    int a = ((angle % 360) + 360) % 360;
    static const char* rot[4]  = {"N",  "W",  "S",  "E"};
    static const char* frot[4] = {"FN", "FW", "FS", "FE"};
    return mirror ? frot[a / 90] : rot[a / 90];
}
// Left-compose a CCW rotation (parent frame) onto an existing orientation:
// Rot(d) ∘ [Rot(a) ∘ MirrorX^m] = Rot(a+d) ∘ MirrorX^m.
std::string orient_rotate(const std::string& o, int degrees) {
    bool m; int a; orient_parse(o, m, a);
    return orient_format(m, a + degrees);
}
// Left-compose a mirror. flip_x = mirror about the vertical centre (Y axis),
// flip_y (!flip_x) = mirror about the horizontal centre (X axis):
//   MirrorX ∘ Rot(a) ∘ MirrorX^m = Rot(-a)     ∘ MirrorX^(1+m)   (flip_y)
//   MirrorY ∘ Rot(a) ∘ MirrorX^m = Rot(180-a)  ∘ MirrorX^(1+m)   (flip_x)
std::string orient_flip(const std::string& o, bool flip_x) {
    bool m; int a; orient_parse(o, m, a);
    return orient_format(!m, (flip_x ? 180 - a : -a));
}
std::string comp_orient(sqlite3* db, int id) {
    Stmt q(db, "SELECT COALESCE(orient,'N') FROM component WHERE id=?");
    sqlite3_bind_int(q, 1, id);
    return (sqlite3_step(q) == SQLITE_ROW)
        ? (const char*)sqlite3_column_text(q, 0) : std::string("N");
}
void set_comp_orient(sqlite3* db, int id, const std::string& o) {
    Stmt u(db, "UPDATE component SET orient=? WHERE id=?");
    sqlite3_bind_text(u, 1, o.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int(u, 2, id);
    sqlite3_step(u);
}

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

void BDB::translate_comp(const std::string& name, double dx, double dy) {
    std::vector<BBoxRow> rows;
    gather_subtree(_db, name, rows);
    Stmt u(_db, "UPDATE component SET x1=x1+?1, y1=y1+?2, x2=x2+?1, y2=y2+?2"
                " WHERE id=?3");
    // Pin positions are ABSOLUTE, so the subtree's pins ride along — unlike
    // flip/rotate (where the per-pin transform is nontrivial and pins stay
    // stale by convention), a pure translation is exact.  This keeps the
    // compute_hpwl() below meaningful and the pin consumers (export_gds
    // TEXT positions, viz flylines, nets_by_hpwl) honest after an align.
    // The (-1, -1) unknown-position sentinel must not be shifted.
    Stmt up(_db, "UPDATE pin SET px=px+?1, py=py+?2"
                 " WHERE comp_id=?3 AND px>=0 AND py>=0");
    for (const auto& r : rows) {
        sqlite3_reset(u);
        sqlite3_bind_double(u, 1, dx);
        sqlite3_bind_double(u, 2, dy);
        sqlite3_bind_int   (u, 3, r.id);
        sqlite3_step(u);
        sqlite3_reset(up);
        sqlite3_bind_double(up, 1, dx);
        sqlite3_bind_double(up, 2, dy);
        sqlite3_bind_int   (up, 3, r.id);
        sqlite3_step(up);
    }
    compute_hpwl();
}

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
    // Compose the flip onto the root's orientation ONLY when the subtree has
    // no descendants (a leaf or childless block). With descendants, the loop
    // above already rewrote their absolute bboxes — GDS export reconstructs
    // the cell from those and would re-apply the orient via the root SREF, so
    // setting orient too would double-transform. A childless root has no such
    // baked geometry, so orient faithfully carries the flip.
    if (rows.size() == 1)
        set_comp_orient(_db, root.id, orient_flip(comp_orient(_db, root.id), flip_x));
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
    // Compose the rotation onto the root's orientation ONLY for a childless
    // subtree — see flip_comp for why (descendant rewrites already carry the
    // rotation for a hierarchical block; setting orient too would make GDS
    // export double-transform).
    if (rows.size() == 1)
        set_comp_orient(_db, root.id, orient_rotate(comp_orient(_db, root.id), degrees));
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
    step_checked(_db, s, "add_busterm");
}

void BDB::clear_busterms() {
    _exec("DELETE FROM busterm");
}

std::vector<ComponentRow> BDB::components_at_depth(int depth) const {
    if (!_q_components_at_depth)
        sqlite3_prepare_v2(_db,
            "SELECT id,name,cell,COALESCE(parent_id,-1),depth,x1,y1,x2,y2,is_leaf,is_replicated,"
            "COALESCE(orient,'N'),COALESCE(is_port,0)"
            " FROM component WHERE depth=? ORDER BY id",
            -1, &_q_components_at_depth, nullptr);
    sqlite3_reset(_q_components_at_depth);
    sqlite3_bind_int(_q_components_at_depth, 1, depth);
    std::vector<ComponentRow> rows;
    while (sqlite3_step(_q_components_at_depth) == SQLITE_ROW) {
        auto q = _q_components_at_depth;
        ComponentRow r;
        r.id           = sqlite3_column_int(q, 0);
        r.name         = col_txt(q, 1);
        r.cell         = col_txt(q, 2);
        r.parent_id    = sqlite3_column_int(q, 3);
        r.depth        = sqlite3_column_int(q, 4);
        r.x1           = sqlite3_column_double(q, 5);
        r.y1           = sqlite3_column_double(q, 6);
        r.x2           = sqlite3_column_double(q, 7);
        r.y2           = sqlite3_column_double(q, 8);
        r.is_leaf      = sqlite3_column_int(q, 9);
        r.is_replicated= sqlite3_column_int(q, 10);
        r.orient       = col_txt(q, 11);
        r.is_port      = sqlite3_column_int(q, 12);
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
        "drv_spec_path,rcv_spec_paths,is_expanded,bu_locked,cloned_from,ndr_rule)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(id) DO UPDATE SET level=excluded.level,"
        " strategy=excluded.strategy, reason=excluded.reason,"
        " num_terminals=excluded.num_terminals, cell_context=excluded.cell_context,"
        " instances=excluded.instances, parent_id=excluded.parent_id,"
        " is_replicated=excluded.is_replicated, drv_spec_depth=excluded.drv_spec_depth,"
        " rcv_spec_depth=excluded.rcv_spec_depth, drv_spec_path=excluded.drv_spec_path,"
        " rcv_spec_paths=excluded.rcv_spec_paths,"
        " is_expanded=excluded.is_expanded, bu_locked=excluded.bu_locked,"
        " cloned_from=excluded.cloned_from, ndr_rule=excluded.ndr_rule");
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
    sqlite3_bind_int   (s, 14, br.is_expanded ? 1 : 0);
    sqlite3_bind_int   (s, 15, br.bu_locked ? 1 : 0);
    sqlite3_bind_text  (s, 16, br.cloned_from.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text  (s, 17, br.ndr_rule.c_str(),    -1, SQLITE_TRANSIENT);
    step_checked(_db, s, "add_bundle");
}

// ── NDR rule persistence (v21) ───────────────────────────────────────────────

void BDB::set_ndr_rule(const NdrRuleRow& r) {
    Stmt s(_db,
        "INSERT INTO ndr_rule(name,width_x,spacing_x,shield_mode,shield_per_n,"
        "shield_net,layers,credit) VALUES(?,?,?,?,?,?,?,?)"
        " ON CONFLICT(name) DO UPDATE SET width_x=excluded.width_x,"
        " spacing_x=excluded.spacing_x, shield_mode=excluded.shield_mode,"
        " shield_per_n=excluded.shield_per_n, shield_net=excluded.shield_net,"
        " layers=excluded.layers, credit=excluded.credit");
    sqlite3_bind_text  (s, 1, r.name.c_str(),       -1, SQLITE_TRANSIENT);
    sqlite3_bind_double(s, 2, r.width_x);
    sqlite3_bind_double(s, 3, r.spacing_x);
    sqlite3_bind_int   (s, 4, r.shield_mode);
    sqlite3_bind_int   (s, 5, r.shield_per_n);
    sqlite3_bind_text  (s, 6, r.shield_net.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text  (s, 7, r.layers.c_str(),     -1, SQLITE_TRANSIENT);
    sqlite3_bind_int   (s, 8, r.credit);
    step_checked(_db, s, "set_ndr_rule");
}

std::vector<NdrRuleRow> BDB::ndr_rules() const {
    Stmt q(_db, "SELECT name,width_x,spacing_x,shield_mode,shield_per_n,"
                "shield_net,layers,credit FROM ndr_rule ORDER BY name");
    auto txt = [](sqlite3_stmt* st, int c) -> std::string {
        const unsigned char* p = sqlite3_column_text(st, c);
        return p ? reinterpret_cast<const char*>(p) : std::string();
    };
    std::vector<NdrRuleRow> rows;
    while (sqlite3_step(q) == SQLITE_ROW) {
        NdrRuleRow r;
        r.name         = txt(q, 0);
        r.width_x      = sqlite3_column_double(q, 1);
        r.spacing_x    = sqlite3_column_double(q, 2);
        r.shield_mode  = sqlite3_column_int(q, 3);
        r.shield_per_n = sqlite3_column_int(q, 4);
        r.shield_net   = txt(q, 5);
        r.layers       = txt(q, 6);
        r.credit       = sqlite3_column_int(q, 7);
        rows.push_back(std::move(r));
    }
    return rows;
}

void BDB::set_ndr_scope(const std::string& prefix, const std::string& rule) {
    // FK made LOUD: sqlite only enforces REFERENCES with foreign_keys=ON,
    // and a dangling scope would silently resolve nets to a rule that no
    // longer exists on reload.
    { Stmt q(_db, "SELECT 1 FROM ndr_rule WHERE name=?");
      sqlite3_bind_text(q, 1, rule.c_str(), -1, SQLITE_TRANSIENT);
      if (sqlite3_step(q) != SQLITE_ROW)
          throw std::runtime_error("set_ndr_scope: rule not declared: " + rule); }
    Stmt s(_db, "INSERT INTO ndr_scope(prefix,rule) VALUES(?,?)"
                " ON CONFLICT(prefix) DO UPDATE SET rule=excluded.rule");
    sqlite3_bind_text(s, 1, prefix.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(s, 2, rule.c_str(),   -1, SQLITE_TRANSIENT);
    step_checked(_db, s, "set_ndr_scope");
}

void BDB::delete_ndr_scope(const std::string& prefix) {
    Stmt s(_db, "DELETE FROM ndr_scope WHERE prefix=?");
    sqlite3_bind_text(s, 1, prefix.c_str(), -1, SQLITE_TRANSIENT);
    step_checked(_db, s, "delete_ndr_scope");
}

std::vector<std::pair<std::string,std::string>> BDB::ndr_scopes() const {
    Stmt q(_db, "SELECT prefix, rule FROM ndr_scope ORDER BY prefix");
    auto txt = [](sqlite3_stmt* st, int c) -> std::string {
        const unsigned char* p = sqlite3_column_text(st, c);
        return p ? reinterpret_cast<const char*>(p) : std::string();
    };
    std::vector<std::pair<std::string,std::string>> out;
    while (sqlite3_step(q) == SQLITE_ROW)
        out.emplace_back(txt(q, 0), txt(q, 1));
    return out;
}

void BDB::clear_ndr() {
    _exec("DELETE FROM ndr_scope; DELETE FROM ndr_rule;");
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

void BDB::_ensure_net_props(int net_id) {
    Stmt np(_db, "INSERT OR IGNORE INTO net_props(net_id) VALUES(?)");
    sqlite3_bind_int(np, 1, net_id);
    sqlite3_step(np);
}

void BDB::_ensure_net_props(int net_id, const std::string& net_name) {
    std::string bus; int bit = 0;
    if (!derive_bus_bit(net_name, bus, bit)) { _ensure_net_props(net_id); return; }
    // DO UPDATE rather than OR IGNORE: the props row may already exist (a
    // net is reached from several pins), and the classification must land on
    // it either way — an IGNORE here would record the bus only for whichever
    // pin happened to create the row first.
    Stmt np(_db,
        "INSERT INTO net_props(net_id,bus_name,bit_index) VALUES(?,?,?)"
        " ON CONFLICT(net_id) DO UPDATE SET"
        " bus_name=excluded.bus_name, bit_index=excluded.bit_index");
    sqlite3_bind_int (np, 1, net_id);
    sqlite3_bind_text(np, 2, bus.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int (np, 3, bit);
    sqlite3_step(np);
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

void BDB::clear_bundles(bool keep_user) {
    // Topology and bus/net routing all FK to bundle, so clear them first
    // (children before parents) or the FK constraint would reject the bundle
    // delete. The route_snapshot fingerprint describes the routed rows, so it
    // is invalidated too.
    _exec("DELETE FROM route_snapshot;"
          "DELETE FROM net_via; DELETE FROM net_segment;"
          "DELETE FROM bus_via; DELETE FROM bus_segment;");
    // Topology tables: with keep_user, user-sourced candidates survive
    // (Phase E4 — a re-bundle must not delete a hand-committed route).
    clear_topologies(keep_user);
    _exec("DELETE FROM bundle_busterm; DELETE FROM bundle_net;");
    if (keep_user) {
        // Keep the bundle rows still referenced by kept topology rows (FK);
        // the re-add upserts them in place. A kept child bundle's parent_id
        // self-FK must survive too, so also keep the transitive parent
        // closure (audit C6-04): deleting a parent still referenced by a kept
        // child aborted the whole re-bundle with 'FOREIGN KEY constraint
        // failed'.
        _exec("DELETE FROM bundle WHERE id NOT IN ("
              " WITH RECURSIVE keep(id) AS ("
              "   SELECT DISTINCT bundle_id FROM topology"
              "   UNION"
              "   SELECT b.parent_id FROM bundle b JOIN keep k ON b.id=k.id"
              "     WHERE b.parent_id IS NOT NULL AND b.parent_id<>''"
              " ) SELECT id FROM keep);");
    } else {
        _exec("DELETE FROM bundle;");
    }
}

std::vector<BundleRow> BDB::all_bundles() const {
    Stmt q(_db,
        "SELECT id,level,strategy,reason,num_terminals,cell_context,instances,"
        "parent_id,is_replicated,drv_spec_depth,rcv_spec_depth,drv_spec_path,"
        "rcv_spec_paths,is_expanded,bu_locked,cloned_from,ndr_rule"
        " FROM bundle ORDER BY CAST(id AS INTEGER), id");
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
        b.is_expanded    = sqlite3_column_int(q, 13) != 0;
        b.bu_locked      = sqlite3_column_int(q, 14) != 0;
        b.cloned_from    = txt(q, 15);
        b.ndr_rule       = txt(q, 16);
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
        "pass_through_count,connected_blocks,feedthru_blocks,is_selected,is_pinned,"
        "topo_uid,source)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(bundle_id,cand_index) DO UPDATE SET type=excluded.type,"
        " wirelength=excluded.wirelength, trunk_location=excluded.trunk_location,"
        " pass_through_count=excluded.pass_through_count,"
        " connected_blocks=excluded.connected_blocks,"
        " feedthru_blocks=excluded.feedthru_blocks, is_selected=excluded.is_selected,"
        " is_pinned=excluded.is_pinned, topo_uid=excluded.topo_uid,"
        " source=excluded.source");
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
    sqlite3_bind_text  (s, 11, tr.topo_uid.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text  (s, 12, tr.source.c_str(),   -1, SQLITE_TRANSIENT);
    step_checked(_db, s, "add_topology");
}

void BDB::add_topology_segment(const TopoSegRow& sr) {
    Stmt s(_db,
        "INSERT OR REPLACE INTO topology_segment(bundle_id,cand_index,seg_index,"
        "x1,y1,x2,y2,layer_hint,is_jog,assigned_layer,edge_id,"
        "perp_clamp_lo,perp_clamp_hi)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)");
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
    sqlite3_bind_int   (s, 11, sr.edge_id);
    sqlite3_bind_int   (s, 12, sr.perp_clamp_lo);
    sqlite3_bind_int   (s, 13, sr.perp_clamp_hi);
    step_checked(_db, s, "add_topology_segment");
}

void BDB::clear_topologies(bool keep_user) {
    // Children before parents (FK): the seg-busterm links reference both topology
    // and busterm.  The routing-time 'tb:' busterm rows exist only to back those
    // links, so drop them too (hier-derived 'bt:' rows are untouched); re-persist
    // recreates them idempotently, keeping the *.bdb.sql dump deterministic.
    if (!keep_user) {
        _exec("DELETE FROM topology_seg_busterm;"
              "DELETE FROM topology_seg_conn;"
              "DELETE FROM topology_bridge_segment;"
              "DELETE FROM topology_segment; DELETE FROM topology;"
              "DELETE FROM busterm WHERE id LIKE 'tb:%';");
        return;
    }
    // keep_user (v15, Phase E4): a hand-committed candidate must survive a bulk
    // re-persist even when it is absent from the in-memory pool (a checkpoint
    // from an earlier session).  Delete only non-user rows — and only the
    // annotation rows backing them — then garbage-collect unreferenced 'tb:'
    // busterms.
    static const char* kKeep =
        " NOT EXISTS (SELECT 1 FROM topology t WHERE t.bundle_id = x.bundle_id"
        "  AND t.cand_index = x.cand_index AND t.source = 'user')";
    std::string sql;
    for (const char* tbl : {"topology_seg_busterm", "topology_seg_conn",
                            "topology_bridge_segment", "topology_segment"})
        sql += "DELETE FROM " + std::string(tbl) + " AS x WHERE" + kKeep + ";";
    sql += "DELETE FROM topology WHERE source IS NULL OR source != 'user';";
    sql += "DELETE FROM busterm WHERE id LIKE 'tb:%' AND id NOT IN"
           " (SELECT busterm_id FROM topology_seg_busterm);";
    _exec(sql.c_str());
}

void BDB::renumber_topology(const std::string& bundle_id, int old_ci, int new_ci) {
    // FK discipline (Codex #198 P1): the four child tables have IMMEDIATE FKs
    // to topology(bundle_id, cand_index) with no ON UPDATE CASCADE, so neither
    // parent-first nor child-first UPDATEs pass enforcement (foreign_keys=ON).
    // Move in three FK-clean steps instead: copy the parent row to the new
    // index, re-point the children (their parent now exists), then delete the
    // old parent (no children reference it anymore).
    {
        Stmt s(_db,
            "INSERT INTO topology(bundle_id,cand_index,type,wirelength,"
            "trunk_location,pass_through_count,connected_blocks,feedthru_blocks,"
            "is_selected,is_pinned,topo_uid,source)"
            " SELECT bundle_id,?,type,wirelength,trunk_location,"
            "pass_through_count,connected_blocks,feedthru_blocks,"
            "is_selected,is_pinned,topo_uid,source"
            " FROM topology WHERE bundle_id=? AND cand_index=?");
        sqlite3_bind_int (s, 1, new_ci);
        sqlite3_bind_text(s, 2, bundle_id.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_int (s, 3, old_ci);
        step_checked(_db, s, "renumber_topology(copy)");
    }
    for (const char* tbl : {"topology_segment", "topology_seg_busterm",
                            "topology_seg_conn", "topology_bridge_segment"}) {
        Stmt s(_db, ("UPDATE " + std::string(tbl) +
                     " SET cand_index=? WHERE bundle_id=? AND cand_index=?").c_str());
        sqlite3_bind_int (s, 1, new_ci);
        sqlite3_bind_text(s, 2, bundle_id.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_int (s, 3, old_ci);
        step_checked(_db, s, "renumber_topology(repoint)");
    }
    {
        Stmt s(_db, "DELETE FROM topology WHERE bundle_id=? AND cand_index=?");
        sqlite3_bind_text(s, 1, bundle_id.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_int (s, 2, old_ci);
        step_checked(_db, s, "renumber_topology(delete)");
    }
}

void BDB::delete_topology(const std::string& bundle_id, int ci) {
    for (const char* tbl : {"topology_seg_busterm", "topology_seg_conn",
                            "topology_bridge_segment", "topology_segment",
                            "topology"}) {
        Stmt s(_db, ("DELETE FROM " + std::string(tbl) +
                     " WHERE bundle_id=? AND cand_index=?").c_str());
        sqlite3_bind_text(s, 1, bundle_id.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_int (s, 2, ci);
        sqlite3_step(s);
    }
}

void BDB::set_bundle_gen_knobs(const std::string& bundle_id,
                               const std::string& knobs) {
    Stmt s(_db, "UPDATE bundle SET gen_knobs=? WHERE id=?");
    sqlite3_bind_text(s, 1, knobs.c_str(),     -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(s, 2, bundle_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_step(s);
}

std::string BDB::bundle_gen_knobs(const std::string& bundle_id) const {
    Stmt q(_db, "SELECT gen_knobs FROM bundle WHERE id=?");
    sqlite3_bind_text(q, 1, bundle_id.c_str(), -1, SQLITE_TRANSIENT);
    if (sqlite3_step(q) == SQLITE_ROW && sqlite3_column_text(q, 0))
        return reinterpret_cast<const char*>(sqlite3_column_text(q, 0));
    return "";
}

std::vector<TopoRow> BDB::topologies(const std::string& bundle_id) const {
    Stmt q(_db,
        "SELECT bundle_id,cand_index,type,wirelength,trunk_location,"
        "pass_through_count,connected_blocks,feedthru_blocks,is_selected,is_pinned,"
        "topo_uid,source FROM topology WHERE bundle_id=? ORDER BY cand_index");
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
        t.topo_uid           = txt(q, 10);
        t.source             = txt(q, 11);
        if (t.source.empty()) t.source = "generated";
        out.push_back(std::move(t));
    }
    return out;
}

std::vector<TopoSegRow> BDB::topology_segments(const std::string& bundle_id,
                                               int cand_index) const {
    Stmt q(_db,
        "SELECT bundle_id,cand_index,seg_index,x1,y1,x2,y2,layer_hint,is_jog,"
        "assigned_layer,edge_id,perp_clamp_lo,perp_clamp_hi FROM topology_segment"
        " WHERE bundle_id=? AND cand_index=? ORDER BY seg_index");
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
        s.edge_id        = sqlite3_column_int(q, 10);
        s.perp_clamp_lo  = sqlite3_column_int(q, 11);
        s.perp_clamp_hi  = sqlite3_column_int(q, 12);
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

void BDB::add_topology_seg_conn(const TopoSegConnRow& r) {
    Stmt s(_db,
        "INSERT OR REPLACE INTO topology_seg_conn"
        "(bundle_id,cand_index,seg_index,endpoint,other_seg) VALUES(?,?,?,?,?)");
    sqlite3_bind_text(s, 1, r.bundle_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int (s, 2, r.cand_index);
    sqlite3_bind_int (s, 3, r.seg_index);
    sqlite3_bind_text(s, 4, r.endpoint.c_str(),  -1, SQLITE_TRANSIENT);
    sqlite3_bind_int (s, 5, r.other_seg);
    sqlite3_step(s);
}

std::vector<TopoSegConnRow> BDB::topology_seg_conns(
    const std::string& bundle_id, int cand_index) const {
    // Ordered for deterministic dumps and so the reload rebuilds seg_conns in
    // the same i-ascending / endpoint / other-ascending order generation used.
    Stmt q(_db,
        "SELECT bundle_id,cand_index,seg_index,endpoint,other_seg"
        " FROM topology_seg_conn WHERE bundle_id=? AND cand_index=?"
        " ORDER BY seg_index,endpoint,other_seg");
    sqlite3_bind_text(q, 1, bundle_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int (q, 2, cand_index);
    std::vector<TopoSegConnRow> out;
    while (sqlite3_step(q) == SQLITE_ROW) {
        TopoSegConnRow r;
        r.bundle_id  = reinterpret_cast<const char*>(sqlite3_column_text(q, 0));
        r.cand_index = sqlite3_column_int(q, 1);
        r.seg_index  = sqlite3_column_int(q, 2);
        r.endpoint   = reinterpret_cast<const char*>(sqlite3_column_text(q, 3));
        r.other_seg  = sqlite3_column_int(q, 4);
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
        "DELETE FROM topology_seg_conn WHERE bundle_id IN"
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

void BDB::clear_expanded_bundle(const std::string& bundle_id) {
    // Per-bundle twin of clear_expanded_bundles (same child-before-parent
    // order, same route_snapshot invalidation) for the selective re-persist
    // path — only rows keyed to THIS bundle id are dropped.
    static const char* kTables[] = {
        "net_via", "net_segment", "bus_via", "bus_segment",
        "topology_seg_busterm", "topology_seg_conn",
        "topology_bridge_segment", "topology_segment", "topology",
        "bundle_busterm", "bundle_net",
    };
    _exec("DELETE FROM route_snapshot;");
    for (const char* t : kTables) {
        Stmt s(_db, ("DELETE FROM " + std::string(t) +
                     " WHERE bundle_id=?").c_str());
        sqlite3_bind_text(s, 1, bundle_id.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_step(s);
    }
    Stmt s(_db, "DELETE FROM bundle WHERE id=? AND is_replicated=1");
    sqlite3_bind_text(s, 1, bundle_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_step(s);
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
    step_checked(_db, s, "add_bus_segment");
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
    step_checked(_db, s, "add_bus_via");
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
    step_checked(_db, s, "add_net_segment");
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
    step_checked(_db, s, "add_net_via");
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
