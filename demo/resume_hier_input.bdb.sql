-- BUDA BDB text dump (sqlite3 iterdump); regenerate via tools/bdb_serialize.py
PRAGMA user_version=29;
BEGIN TRANSACTION;
CREATE TABLE bundle (
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
    , ndr_rule TEXT DEFAULT '');
CREATE TABLE bundle_busterm (
        bundle_id  TEXT REFERENCES bundle(id),
        busterm_id TEXT,
        role       TEXT DEFAULT '',   -- 'entry' | 'exit' (hier flow)
        PRIMARY KEY (bundle_id, busterm_id, role)
    );
CREATE TABLE bundle_net (
        bundle_id TEXT REFERENCES bundle(id),
        net_id    INTEGER REFERENCES net(id),
        ord       INTEGER DEFAULT -1,  -- bit order within the bundle (v10)
        -- Per-BIT endpoints of a fan-in / fan-out bundle (v27), i.e.
        -- HBundle::net_drivers[ord] and net_receivers[ord].  They are what
        -- makes the per-bit taper (Topology::seg_bits) derivable, and they
        -- are stored rather than re-derived because the roles they encode
        -- come from a subtle pass (deepest OUTPUT, path-maximal receivers,
        -- INOUT/UNKNOWN fallbacks, extra-driver attachment) that a second
        -- implementation would drift from.  Empty for every other bundle.
        drv_path  TEXT DEFAULT '',      -- driver block path for this bit
        rcv_paths TEXT DEFAULT '',      -- JSON array of receiver block paths
        PRIMARY KEY (bundle_id, net_id)
    );
CREATE TABLE bus_segment (
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
CREATE TABLE bus_via (
        bundle_id  TEXT NOT NULL REFERENCES bundle(id),   -- NOT NULL: see bus_segment
        from_seg   INTEGER,
        to_seg     INTEGER,
        from_layer INTEGER,
        to_layer   INTEGER,
        x REAL, y REAL,
        bit_width  INTEGER,
        PRIMARY KEY (bundle_id, from_seg, to_seg)
    );
CREATE TABLE busterm (
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
CREATE TABLE cell (
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
INSERT INTO "cell" VALUES('tile_cell',180.0,230.0,'',0,-1,-1);
INSERT INTO "cell" VALUES('leaf_cell',70.0,130.0,'',0,-1,-1);
CREATE TABLE cell_children (
            parent_cell TEXT NOT NULL REFERENCES cell(name),
            inst_name   TEXT NOT NULL,
            child_cell  TEXT NOT NULL REFERENCES cell(name),
            x           REAL NOT NULL DEFAULT 0,
            y           REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (parent_cell, inst_name)
        );
INSERT INTO "cell_children" VALUES('tile_cell','lo','leaf_cell',10.0,50.0);
INSERT INTO "cell_children" VALUES('tile_cell','hi','leaf_cell',90.0,50.0);
CREATE TABLE cell_layer_share (
            cell     TEXT NOT NULL REFERENCES cell(name),
            layer_id INTEGER NOT NULL,
            share    REAL NOT NULL,
            PRIMARY KEY (cell, layer_id)
        );
CREATE TABLE cell_pin (
            cell      TEXT NOT NULL REFERENCES cell(name),
            pin_name  TEXT NOT NULL,
            dir       TEXT NOT NULL DEFAULT 'INOUT',
            px        REAL NOT NULL DEFAULT -1,
            py        REAL NOT NULL DEFAULT -1,
            PRIMARY KEY (cell, pin_name)
        );
INSERT INTO "cell_pin" VALUES('leaf_cell','out','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('leaf_cell','in','INPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('tile_cell','x_tile_0','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('tile_cell','x_tile_1','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('tile_cell','x_tile_2','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('tile_cell','x_tile_3','OUTPUT',-1.0,-1.0);
CREATE TABLE component (
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
INSERT INTO "component" VALUES(1,'t0','tile_cell',NULL,0,0.0,0.0,180.0,230.0,0,0,0,'N');
INSERT INTO "component" VALUES(2,'t0/hi','leaf_cell',1,1,90.0,50.0,160.0,180.0,1,0,0,'N');
INSERT INTO "component" VALUES(3,'t0/lo','leaf_cell',1,1,10.0,50.0,80.0,180.0,1,0,0,'N');
INSERT INTO "component" VALUES(4,'t1','tile_cell',NULL,0,450.0,0.0,630.0,230.0,0,0,0,'N');
INSERT INTO "component" VALUES(5,'t1/hi','leaf_cell',4,1,540.0,50.0,610.0,180.0,1,0,0,'N');
INSERT INTO "component" VALUES(6,'t1/lo','leaf_cell',4,1,460.0,50.0,530.0,180.0,1,0,0,'N');
CREATE TABLE grid_override (
            layer_id INTEGER NOT NULL,
            x1 INTEGER NOT NULL, y1 INTEGER NOT NULL,
            x2 INTEGER NOT NULL, y2 INTEGER NOT NULL,
            origin REAL NOT NULL DEFAULT 0,
            slots  TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY (layer_id, x1, y1, x2, y2)
        );
CREATE TABLE grp (
            id        TEXT PRIMARY KEY,
            name      TEXT NOT NULL,
            color     TEXT,
            parent_id TEXT REFERENCES grp(id)
        );
CREATE TABLE grp_member (
            grp_id TEXT REFERENCES grp(id),
            kind   TEXT,
            ref    TEXT,
            PRIMARY KEY (grp_id, kind, ref)
        );
CREATE TABLE keepout (
            x1 INTEGER NOT NULL, y1 INTEGER NOT NULL,
            x2 INTEGER NOT NULL, y2 INTEGER NOT NULL,
            layers       TEXT NOT NULL DEFAULT '',
            inside_block INTEGER NOT NULL DEFAULT 0,
            net          TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (x1, y1, x2, y2, layers)
        );
CREATE TABLE meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
INSERT INTO "meta" VALUES('schema_version','29');
INSERT INTO "meta" VALUES('bdb_tool','buda-bdb');
INSERT INTO "meta" VALUES('die_w','850.000000');
INSERT INTO "meta" VALUES('die_h','500.000000');
CREATE TABLE ndr_rule (
            name         TEXT PRIMARY KEY,
            width_x      REAL NOT NULL DEFAULT 1,
            spacing_x    REAL NOT NULL DEFAULT 1,
            shield_mode  INTEGER NOT NULL DEFAULT 0,
            shield_per_n INTEGER NOT NULL DEFAULT 0,
            shield_net   TEXT NOT NULL DEFAULT 'GND',
            layers       TEXT NOT NULL DEFAULT '',
            credit       INTEGER NOT NULL DEFAULT 0,
            bond         INTEGER NOT NULL DEFAULT 0,
            width_abs    REAL NOT NULL DEFAULT 0,
            spacing_abs  REAL NOT NULL DEFAULT 0,
            per_layer    TEXT NOT NULL DEFAULT '',
            metal        INTEGER NOT NULL DEFAULT 0
        );
CREATE TABLE ndr_scope (
            prefix TEXT PRIMARY KEY,
            rule   TEXT NOT NULL REFERENCES ndr_rule(name)
        );
CREATE TABLE net (
            id   INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL
        );
INSERT INTO "net" VALUES(1,'b_lohi_t0_0');
INSERT INTO "net" VALUES(2,'b_lohi_t0_1');
INSERT INTO "net" VALUES(3,'b_lohi_t0_2');
INSERT INTO "net" VALUES(4,'b_lohi_t0_3');
INSERT INTO "net" VALUES(5,'b_lohi_t1_0');
INSERT INTO "net" VALUES(6,'b_lohi_t1_1');
INSERT INTO "net" VALUES(7,'b_lohi_t1_2');
INSERT INTO "net" VALUES(8,'b_lohi_t1_3');
INSERT INTO "net" VALUES(9,'x_tile_0');
INSERT INTO "net" VALUES(10,'x_tile_1');
INSERT INTO "net" VALUES(11,'x_tile_2');
INSERT INTO "net" VALUES(12,'x_tile_3');
CREATE TABLE net_props (
            net_id       INTEGER PRIMARY KEY REFERENCES net(id),
            hpwl         REAL,
            fanout       INTEGER,
            driver_comp  TEXT,
            bus_name     TEXT,
            bit_index    INTEGER,
            bundle_id    INTEGER
        );
INSERT INTO "net_props" VALUES(1,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(2,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(3,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(4,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(5,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(6,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(7,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(8,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(9,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(10,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(11,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(12,NULL,NULL,NULL,NULL,NULL,NULL);
CREATE TABLE net_segment (
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
CREATE TABLE net_via (
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
CREATE TABLE pin (
            net_id   INTEGER REFERENCES net(id),
            comp_id  INTEGER REFERENCES component(id),
            pin_name TEXT,
            dir      TEXT,
            px       REAL,    -- absolute pin x in um (-1 if unknown)
            py       REAL,    -- absolute pin y in um (-1 if unknown)
            PRIMARY KEY (net_id, comp_id, pin_name)
        );
INSERT INTO "pin" VALUES(1,3,'out','OUTPUT',45.0,115.0);
INSERT INTO "pin" VALUES(1,2,'in','INPUT',125.0,115.0);
INSERT INTO "pin" VALUES(2,3,'out','OUTPUT',45.0,115.0);
INSERT INTO "pin" VALUES(2,2,'in','INPUT',125.0,115.0);
INSERT INTO "pin" VALUES(3,3,'out','OUTPUT',45.0,115.0);
INSERT INTO "pin" VALUES(3,2,'in','INPUT',125.0,115.0);
INSERT INTO "pin" VALUES(4,3,'out','OUTPUT',45.0,115.0);
INSERT INTO "pin" VALUES(4,2,'in','INPUT',125.0,115.0);
INSERT INTO "pin" VALUES(5,6,'out','OUTPUT',495.0,115.0);
INSERT INTO "pin" VALUES(5,5,'in','INPUT',575.0,115.0);
INSERT INTO "pin" VALUES(6,6,'out','OUTPUT',495.0,115.0);
INSERT INTO "pin" VALUES(6,5,'in','INPUT',575.0,115.0);
INSERT INTO "pin" VALUES(7,6,'out','OUTPUT',495.0,115.0);
INSERT INTO "pin" VALUES(7,5,'in','INPUT',575.0,115.0);
INSERT INTO "pin" VALUES(8,6,'out','OUTPUT',495.0,115.0);
INSERT INTO "pin" VALUES(8,5,'in','INPUT',575.0,115.0);
INSERT INTO "pin" VALUES(9,2,'out','OUTPUT',125.0,115.0);
INSERT INTO "pin" VALUES(9,1,'x_tile_0','OUTPUT',90.0,115.0);
INSERT INTO "pin" VALUES(9,6,'in','INPUT',495.0,115.0);
INSERT INTO "pin" VALUES(9,4,'x_tile_0','INPUT',540.0,115.0);
INSERT INTO "pin" VALUES(10,2,'out','OUTPUT',125.0,115.0);
INSERT INTO "pin" VALUES(10,1,'x_tile_1','OUTPUT',90.0,115.0);
INSERT INTO "pin" VALUES(10,6,'in','INPUT',495.0,115.0);
INSERT INTO "pin" VALUES(10,4,'x_tile_1','INPUT',540.0,115.0);
INSERT INTO "pin" VALUES(11,2,'out','OUTPUT',125.0,115.0);
INSERT INTO "pin" VALUES(11,1,'x_tile_2','OUTPUT',90.0,115.0);
INSERT INTO "pin" VALUES(11,6,'in','INPUT',495.0,115.0);
INSERT INTO "pin" VALUES(11,4,'x_tile_2','INPUT',540.0,115.0);
INSERT INTO "pin" VALUES(12,2,'out','OUTPUT',125.0,115.0);
INSERT INTO "pin" VALUES(12,1,'x_tile_3','OUTPUT',90.0,115.0);
INSERT INTO "pin" VALUES(12,6,'in','INPUT',495.0,115.0);
INSERT INTO "pin" VALUES(12,4,'x_tile_3','INPUT',540.0,115.0);
CREATE TABLE route_snapshot (
        id             INTEGER PRIMARY KEY,   -- always 1 (current routing)
        hash           TEXT,
        n_bus_segments INTEGER DEFAULT 0,
        n_bus_vias     INTEGER DEFAULT 0,
        stage          TEXT,                  -- 'abstract_nuts' / 'detailed_nuts'
        n_net_segments INTEGER DEFAULT 0,
        n_net_vias     INTEGER DEFAULT 0
    );
CREATE TABLE topology (
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
CREATE TABLE topology_bridge_segment (
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
CREATE TABLE topology_seg_busterm (
        bundle_id  TEXT,
        cand_index INTEGER,
        seg_index  INTEGER,
        endpoint   TEXT,              -- 'start' | 'end'
        busterm_id TEXT REFERENCES busterm(id),
        PRIMARY KEY (bundle_id, cand_index, seg_index, endpoint),
        FOREIGN KEY (bundle_id, cand_index)
            REFERENCES topology(bundle_id, cand_index)
    );
CREATE TABLE topology_seg_conn (
        bundle_id  TEXT,
        cand_index INTEGER,
        seg_index  INTEGER,
        endpoint   TEXT,              -- 'start' | 'end'
        other_seg  INTEGER,
        PRIMARY KEY (bundle_id, cand_index, seg_index, endpoint, other_seg),
        FOREIGN KEY (bundle_id, cand_index)
            REFERENCES topology(bundle_id, cand_index)
    );
CREATE TABLE topology_segment (
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
CREATE TABLE track_pattern (
            layer_id INTEGER PRIMARY KEY,
            origin   REAL NOT NULL DEFAULT 0,
            is_horiz INTEGER NOT NULL DEFAULT 0,
            bounded  INTEGER NOT NULL DEFAULT 0,
            bound_lo REAL NOT NULL DEFAULT 0,
            bound_hi REAL NOT NULL DEFAULT 0,
            source   TEXT NOT NULL DEFAULT 'script',
            slots    TEXT NOT NULL DEFAULT '[]'
        );
COMMIT;
