-- BUDA BDB text dump (sqlite3 iterdump); regenerate via tools/bdb_serialize.py
PRAGMA user_version=20;
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
    );
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
INSERT INTO "busterm" VALUES('bt:src_a',1,'src_a',0,400.0,400.0,2000.0,2000.0,'BLOCK',NULL,NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:src_a/gen_i',2,'src_a/gen_i',1,880.0,880.0,1520.0,1520.0,'PORT','bt:src_a',NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:proc_a',3,'proc_a',0,2800.0,2000.0,6160.0,4400.0,'BLOCK',NULL,NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:proc_a/pa_i',4,'proc_a/pa_i',1,2960.0,2320.0,3840.0,2960.0,'PORT','bt:proc_a',NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:proc_a/pb_i',5,'proc_a/pb_i',1,4040.0,3440.0,4920.0,4080.0,'PORT','bt:proc_a',NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:proc_a/pc_i',6,'proc_a/pc_i',1,5120.0,2320.0,6000.0,2960.0,'PORT','bt:proc_a',NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:snk_a',7,'snk_a',0,6960.0,400.0,8560.0,2000.0,'BLOCK',NULL,NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:snk_a/rcv_i',8,'snk_a/rcv_i',1,7440.0,880.0,8080.0,1520.0,'PORT','bt:snk_a',NULL,'THRU',0.0,0.0,0.0,0.0);
CREATE TABLE cell (
            name        TEXT PRIMARY KEY,
            width       REAL NOT NULL,
            height      REAL NOT NULL,
            bottom_up   INTEGER NOT NULL DEFAULT 0,
            layer_cap   INTEGER NOT NULL DEFAULT -1,
            layer_floor INTEGER NOT NULL DEFAULT -1
        );
INSERT INTO "cell" VALUES('proc_cell',3360.0,2400.0,0,-1,-1);
INSERT INTO "cell" VALUES('pipe_cell',880.0,640.0,0,-1,-1);
INSERT INTO "cell" VALUES('src_cell',1600.0,1600.0,0,-1,-1);
INSERT INTO "cell" VALUES('snk_cell',1600.0,1600.0,0,-1,-1);
INSERT INTO "cell" VALUES('gen_cell',640.0,640.0,0,-1,-1);
INSERT INTO "cell" VALUES('rcv_cell',640.0,640.0,0,-1,-1);
CREATE TABLE cell_children (
            parent_cell TEXT NOT NULL REFERENCES cell(name),
            inst_name   TEXT NOT NULL,
            child_cell  TEXT NOT NULL REFERENCES cell(name),
            x           REAL NOT NULL DEFAULT 0,
            y           REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (parent_cell, inst_name)
        );
INSERT INTO "cell_children" VALUES('proc_cell','pa_i','pipe_cell',160.0,320.0);
INSERT INTO "cell_children" VALUES('proc_cell','pb_i','pipe_cell',1240.0,1440.0);
INSERT INTO "cell_children" VALUES('proc_cell','pc_i','pipe_cell',2320.0,320.0);
INSERT INTO "cell_children" VALUES('src_cell','gen_i','gen_cell',480.0,480.0);
INSERT INTO "cell_children" VALUES('snk_cell','rcv_i','rcv_cell',480.0,480.0);
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
INSERT INTO "cell_pin" VALUES('gen_cell','out','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('src_cell','s2p_0','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('pipe_cell','in','INPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('proc_cell','s2p_0','INPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('pipe_cell','out','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('proc_cell','p2s_0','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('rcv_cell','in','INPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('snk_cell','p2s_0','INPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('src_cell','s2p_1','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('proc_cell','s2p_1','INPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('proc_cell','p2s_1','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('snk_cell','p2s_1','INPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('src_cell','s2p_2','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('proc_cell','s2p_2','INPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('proc_cell','p2s_2','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('snk_cell','p2s_2','INPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('src_cell','s2p_3','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('proc_cell','s2p_3','INPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('proc_cell','p2s_3','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('snk_cell','p2s_3','INPUT',-1.0,-1.0);
CREATE TABLE component (
            id           INTEGER PRIMARY KEY,
            name         TEXT UNIQUE NOT NULL,
            cell         TEXT,
            parent_id    INTEGER REFERENCES component(id),
            depth        INTEGER DEFAULT 0,
            x1 REAL, y1 REAL, x2 REAL, y2 REAL,
            is_leaf      INTEGER DEFAULT 1,
            is_replicated INTEGER DEFAULT 0,
            orient       TEXT DEFAULT 'N'
        );
INSERT INTO "component" VALUES(1,'src_a','src_cell',NULL,0,400.0,400.0,2000.0,2000.0,0,0,'N');
INSERT INTO "component" VALUES(2,'src_a/gen_i','gen_cell',1,1,880.0,880.0,1520.0,1520.0,1,0,'N');
INSERT INTO "component" VALUES(3,'proc_a','proc_cell',NULL,0,2800.0,2000.0,6160.0,4400.0,0,0,'N');
INSERT INTO "component" VALUES(4,'proc_a/pa_i','pipe_cell',3,1,2960.0,2320.0,3840.0,2960.0,1,0,'N');
INSERT INTO "component" VALUES(5,'proc_a/pb_i','pipe_cell',3,1,4040.0,3440.0,4920.0,4080.0,1,0,'N');
INSERT INTO "component" VALUES(6,'proc_a/pc_i','pipe_cell',3,1,5120.0,2320.0,6000.0,2960.0,1,0,'N');
INSERT INTO "component" VALUES(7,'snk_a','snk_cell',NULL,0,6960.0,400.0,8560.0,2000.0,0,0,'N');
INSERT INTO "component" VALUES(8,'snk_a/rcv_i','rcv_cell',7,1,7440.0,880.0,8080.0,1520.0,1,0,'N');
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
CREATE TABLE meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
INSERT INTO "meta" VALUES('schema_version','20');
INSERT INTO "meta" VALUES('bdb_tool','buda-bdb');
CREATE TABLE net (
            id   INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL
        );
INSERT INTO "net" VALUES(1,'s2p_0');
INSERT INTO "net" VALUES(2,'p2s_0');
INSERT INTO "net" VALUES(3,'ab_fwd_0');
INSERT INTO "net" VALUES(4,'ab_rev_0');
INSERT INTO "net" VALUES(5,'s2p_1');
INSERT INTO "net" VALUES(6,'p2s_1');
INSERT INTO "net" VALUES(7,'ab_fwd_1');
INSERT INTO "net" VALUES(8,'ab_rev_1');
INSERT INTO "net" VALUES(9,'s2p_2');
INSERT INTO "net" VALUES(10,'p2s_2');
INSERT INTO "net" VALUES(11,'ab_fwd_2');
INSERT INTO "net" VALUES(12,'ab_rev_2');
INSERT INTO "net" VALUES(13,'s2p_3');
INSERT INTO "net" VALUES(14,'p2s_3');
INSERT INTO "net" VALUES(15,'ab_fwd_3');
INSERT INTO "net" VALUES(16,'ab_rev_3');
INSERT INTO "net" VALUES(17,'ab_extra');
CREATE TABLE net_props (
            net_id       INTEGER PRIMARY KEY REFERENCES net(id),
            hpwl         REAL,
            fanout       INTEGER,
            driver_comp  TEXT,
            bus_name     TEXT,
            bit_index    INTEGER,
            bundle_id    INTEGER
        );
INSERT INTO "net_props" VALUES(1,5280.0,2,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(2,5280.0,2,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(3,2200.0,1,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(4,2200.0,1,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(5,5280.0,2,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(6,5280.0,2,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(7,2200.0,1,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(8,2200.0,1,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(9,5280.0,2,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(10,5280.0,2,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(11,2200.0,1,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(12,2200.0,1,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(13,5280.0,2,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(14,5280.0,2,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(15,2200.0,1,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(16,2200.0,1,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(17,2200.0,1,NULL,NULL,NULL,NULL);
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
INSERT INTO "pin" VALUES(1,2,'out','OUTPUT',1200.0,1200.0);
INSERT INTO "pin" VALUES(1,1,'s2p_0','OUTPUT',1200.0,1200.0);
INSERT INTO "pin" VALUES(1,4,'in','INPUT',3400.0,2640.0);
INSERT INTO "pin" VALUES(1,3,'s2p_0','INPUT',4480.0,3200.0);
INSERT INTO "pin" VALUES(2,6,'out','OUTPUT',5560.0,2640.0);
INSERT INTO "pin" VALUES(2,3,'p2s_0','OUTPUT',4480.0,3200.0);
INSERT INTO "pin" VALUES(2,8,'in','INPUT',7760.0,1200.0);
INSERT INTO "pin" VALUES(2,7,'p2s_0','INPUT',7760.0,1200.0);
INSERT INTO "pin" VALUES(3,4,'out','OUTPUT',3400.0,2640.0);
INSERT INTO "pin" VALUES(3,5,'in','INPUT',4480.0,3760.0);
INSERT INTO "pin" VALUES(4,5,'out','OUTPUT',4480.0,3760.0);
INSERT INTO "pin" VALUES(4,4,'in','INPUT',3400.0,2640.0);
INSERT INTO "pin" VALUES(5,2,'out','OUTPUT',1200.0,1200.0);
INSERT INTO "pin" VALUES(5,1,'s2p_1','OUTPUT',1200.0,1200.0);
INSERT INTO "pin" VALUES(5,4,'in','INPUT',3400.0,2640.0);
INSERT INTO "pin" VALUES(5,3,'s2p_1','INPUT',4480.0,3200.0);
INSERT INTO "pin" VALUES(6,6,'out','OUTPUT',5560.0,2640.0);
INSERT INTO "pin" VALUES(6,3,'p2s_1','OUTPUT',4480.0,3200.0);
INSERT INTO "pin" VALUES(6,8,'in','INPUT',7760.0,1200.0);
INSERT INTO "pin" VALUES(6,7,'p2s_1','INPUT',7760.0,1200.0);
INSERT INTO "pin" VALUES(7,4,'out','OUTPUT',3400.0,2640.0);
INSERT INTO "pin" VALUES(7,5,'in','INPUT',4480.0,3760.0);
INSERT INTO "pin" VALUES(8,5,'out','OUTPUT',4480.0,3760.0);
INSERT INTO "pin" VALUES(8,4,'in','INPUT',3400.0,2640.0);
INSERT INTO "pin" VALUES(9,2,'out','OUTPUT',1200.0,1200.0);
INSERT INTO "pin" VALUES(9,1,'s2p_2','OUTPUT',1200.0,1200.0);
INSERT INTO "pin" VALUES(9,4,'in','INPUT',3400.0,2640.0);
INSERT INTO "pin" VALUES(9,3,'s2p_2','INPUT',4480.0,3200.0);
INSERT INTO "pin" VALUES(10,6,'out','OUTPUT',5560.0,2640.0);
INSERT INTO "pin" VALUES(10,3,'p2s_2','OUTPUT',4480.0,3200.0);
INSERT INTO "pin" VALUES(10,8,'in','INPUT',7760.0,1200.0);
INSERT INTO "pin" VALUES(10,7,'p2s_2','INPUT',7760.0,1200.0);
INSERT INTO "pin" VALUES(11,4,'out','OUTPUT',3400.0,2640.0);
INSERT INTO "pin" VALUES(11,5,'in','INPUT',4480.0,3760.0);
INSERT INTO "pin" VALUES(12,5,'out','OUTPUT',4480.0,3760.0);
INSERT INTO "pin" VALUES(12,4,'in','INPUT',3400.0,2640.0);
INSERT INTO "pin" VALUES(13,2,'out','OUTPUT',1200.0,1200.0);
INSERT INTO "pin" VALUES(13,1,'s2p_3','OUTPUT',1200.0,1200.0);
INSERT INTO "pin" VALUES(13,4,'in','INPUT',3400.0,2640.0);
INSERT INTO "pin" VALUES(13,3,'s2p_3','INPUT',4480.0,3200.0);
INSERT INTO "pin" VALUES(14,6,'out','OUTPUT',5560.0,2640.0);
INSERT INTO "pin" VALUES(14,3,'p2s_3','OUTPUT',4480.0,3200.0);
INSERT INTO "pin" VALUES(14,8,'in','INPUT',7760.0,1200.0);
INSERT INTO "pin" VALUES(14,7,'p2s_3','INPUT',7760.0,1200.0);
INSERT INTO "pin" VALUES(15,4,'out','OUTPUT',3400.0,2640.0);
INSERT INTO "pin" VALUES(15,5,'in','INPUT',4480.0,3760.0);
INSERT INTO "pin" VALUES(16,5,'out','OUTPUT',4480.0,3760.0);
INSERT INTO "pin" VALUES(16,4,'in','INPUT',3400.0,2640.0);
INSERT INTO "pin" VALUES(17,4,'out','OUTPUT',3400.0,2640.0);
INSERT INTO "pin" VALUES(17,5,'in','INPUT',4480.0,3760.0);
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
COMMIT;
