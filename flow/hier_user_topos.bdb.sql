-- BUDA BDB text dump (sqlite3 iterdump); regenerate via tools/bdb_serialize.py
PRAGMA user_version=19;
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
INSERT INTO "bundle" VALUES('1',1,'STRICT','DRV:alu_i/pa_i|REC:alu_i/pb_i,',2,'alu_cell','["alu_i"]',NULL,0,-1,-1,'','[]','',0,0,'');
INSERT INTO "bundle" VALUES('3',1,'STRICT','DRV:mem_i/qa_i|REC:mem_i/qb_i,',2,'mem_cell','["mem_i"]',NULL,0,-1,-1,'','[]','',0,0,'');
INSERT INTO "bundle" VALUES('2',0,'STRICT','DRV:alu_i/pb_i|REC:mem_i/qa_i,',2,'','[]',NULL,0,-1,-1,'','[]','',0,0,'');
INSERT INTO "bundle" VALUES('4',1,'STRICT','DRV:alu_i/pa_i|REC:alu_i/pb_i,',2,'alu_cell','["alu_i"]','1',1,-1,-1,'','[]','',1,0,'');
INSERT INTO "bundle" VALUES('5',1,'STRICT','DRV:mem_i/qa_i|REC:mem_i/qb_i,',2,'mem_cell','["mem_i"]','3',1,-1,-1,'','[]','',1,0,'');
CREATE TABLE bundle_busterm (
        bundle_id  TEXT REFERENCES bundle(id),
        busterm_id TEXT,
        role       TEXT DEFAULT '',   -- 'entry' | 'exit' (hier flow)
        PRIMARY KEY (bundle_id, busterm_id, role)
    );
INSERT INTO "bundle_busterm" VALUES('1','bt:alu_i/pa_i','entry');
INSERT INTO "bundle_busterm" VALUES('1','bt:alu_i/pb_i','exit');
INSERT INTO "bundle_busterm" VALUES('2','bt:alu_i/pb_i','entry');
INSERT INTO "bundle_busterm" VALUES('2','bt:mem_i/qa_i','exit');
INSERT INTO "bundle_busterm" VALUES('3','bt:mem_i/qa_i','entry');
INSERT INTO "bundle_busterm" VALUES('3','bt:mem_i/qb_i','exit');
INSERT INTO "bundle_busterm" VALUES('4','bt:alu_i/pa_i','entry');
INSERT INTO "bundle_busterm" VALUES('4','bt:alu_i/pb_i','exit');
INSERT INTO "bundle_busterm" VALUES('5','bt:mem_i/qa_i','entry');
INSERT INTO "bundle_busterm" VALUES('5','bt:mem_i/qb_i','exit');
CREATE TABLE bundle_net (
        bundle_id TEXT REFERENCES bundle(id),
        net_id    INTEGER REFERENCES net(id),
        ord       INTEGER DEFAULT -1,  -- bit order within the bundle (v10)
        PRIMARY KEY (bundle_id, net_id)
    );
INSERT INTO "bundle_net" VALUES('1',1,0);
INSERT INTO "bundle_net" VALUES('1',2,1);
INSERT INTO "bundle_net" VALUES('1',3,2);
INSERT INTO "bundle_net" VALUES('1',4,3);
INSERT INTO "bundle_net" VALUES('2',9,0);
INSERT INTO "bundle_net" VALUES('2',10,1);
INSERT INTO "bundle_net" VALUES('2',11,2);
INSERT INTO "bundle_net" VALUES('2',12,3);
INSERT INTO "bundle_net" VALUES('3',5,0);
INSERT INTO "bundle_net" VALUES('3',6,1);
INSERT INTO "bundle_net" VALUES('3',7,2);
INSERT INTO "bundle_net" VALUES('3',8,3);
INSERT INTO "bundle_net" VALUES('4',1,0);
INSERT INTO "bundle_net" VALUES('4',2,1);
INSERT INTO "bundle_net" VALUES('4',3,2);
INSERT INTO "bundle_net" VALUES('4',4,3);
INSERT INTO "bundle_net" VALUES('5',5,0);
INSERT INTO "bundle_net" VALUES('5',6,1);
INSERT INTO "bundle_net" VALUES('5',7,2);
INSERT INTO "bundle_net" VALUES('5',8,3);
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
INSERT INTO "bus_segment" VALUES('4',0,4,1,75.0,5.13333333333333285e+01,210.0,58.0,5.46666666666666643e+01,6.66666666666666696e+00,1,0,5.80000000000000071e+00,58.0,NULL,NULL);
INSERT INTO "bus_segment" VALUES('4',1,5,0,1.18333333333333342e+02,5.46666666666666643e+01,125.0,60.0,1.21666666666666671e+02,6.66666666666666696e+00,1,0,25.0,125.0,NULL,NULL);
INSERT INTO "bus_segment" VALUES('4',2,5,0,160.0,5.46666666666666643e+01,1.66666666666666685e+02,60.0,1.63333333333333342e+02,6.66666666666666696e+00,1,0,160.0,260.0,NULL,NULL);
INSERT INTO "bus_segment" VALUES('2',0,4,1,200.0,4.26666666666666571e+01,760.0,4.93333333333333285e+01,4.59999999999999928e+01,6.66666666666666696e+00,1,0,5.80000000000000071e+00,58.0,NULL,NULL);
INSERT INTO "bus_segment" VALUES('2',1,5,0,2.53333333333333342e+02,4.59999999999999928e+01,260.0,60.0,2.56666666666666685e+02,6.66666666666666696e+00,1,0,160.0,260.0,NULL,NULL);
INSERT INTO "bus_segment" VALUES('2',2,5,0,725.0,4.59999999999999928e+01,7.31666666666666742e+02,60.0,7.28333333333333371e+02,6.66666666666666696e+00,1,0,725.0,825.0,NULL,NULL);
INSERT INTO "bus_segment" VALUES('5',0,4,1,775.0,5.13333333333333285e+01,910.0,58.0,5.46666666666666643e+01,6.66666666666666696e+00,1,0,5.80000000000000071e+00,58.0,NULL,NULL);
INSERT INTO "bus_segment" VALUES('5',1,5,0,8.18333333333333257e+02,5.46666666666666643e+01,825.0,60.0,8.21666666666666628e+02,6.66666666666666696e+00,1,0,725.0,825.0,NULL,NULL);
INSERT INTO "bus_segment" VALUES('5',2,5,0,860.0,5.46666666666666643e+01,8.66666666666666742e+02,60.0,8.63333333333333371e+02,6.66666666666666696e+00,1,0,860.0,960.0,NULL,NULL);
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
INSERT INTO "bus_via" VALUES('4',0,1,4,5,1.21666666666666671e+02,5.46666666666666643e+01,4);
INSERT INTO "bus_via" VALUES('4',0,2,4,5,1.63333333333333342e+02,5.46666666666666643e+01,4);
INSERT INTO "bus_via" VALUES('2',0,1,4,5,2.56666666666666685e+02,4.59999999999999928e+01,4);
INSERT INTO "bus_via" VALUES('2',0,2,4,5,7.28333333333333371e+02,4.59999999999999928e+01,4);
INSERT INTO "bus_via" VALUES('5',0,1,4,5,8.21666666666666628e+02,5.46666666666666643e+01,4);
INSERT INTO "bus_via" VALUES('5',0,2,4,5,8.63333333333333371e+02,5.46666666666666643e+01,4);
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
INSERT INTO "busterm" VALUES('bt:alu_i',1,'alu_i',0,0.0,0.0,420.0,200.0,'BLOCK',NULL,NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:alu_i/pa_i',2,'alu_i/pa_i',1,20.0,60.0,130.0,140.0,'SPATIAL_CLUSTER','bt:alu_i',NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:alu_i/pb_i',3,'alu_i/pb_i',1,155.0,60.0,265.0,140.0,'SPATIAL_CLUSTER','bt:alu_i',NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:mem_i',4,'mem_i',0,700.0,0.0,1120.0,200.0,'BLOCK',NULL,NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:mem_i/qa_i',5,'mem_i/qa_i',1,720.0,60.0,830.0,140.0,'SPATIAL_CLUSTER','bt:mem_i',NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:mem_i/qb_i',6,'mem_i/qb_i',1,855.0,60.0,965.0,140.0,'SPATIAL_CLUSTER','bt:mem_i',NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('tb:pa_i:f96ba558',NULL,'pa_i',-1,25.0,65.0,125.0,135.0,'BLOCK',NULL,NULL,'THRU',20.0,60.0,130.0,140.0);
INSERT INTO "busterm" VALUES('tb:pb_i:55fabb5a',NULL,'pb_i',-1,160.0,65.0,260.0,135.0,'BLOCK',NULL,NULL,'THRU',155.0,60.0,265.0,140.0);
INSERT INTO "busterm" VALUES('tb:qa_i:f96ba558',NULL,'qa_i',-1,25.0,65.0,125.0,135.0,'BLOCK',NULL,NULL,'THRU',20.0,60.0,130.0,140.0);
INSERT INTO "busterm" VALUES('tb:qb_i:55fabb5a',NULL,'qb_i',-1,160.0,65.0,260.0,135.0,'BLOCK',NULL,NULL,'THRU',155.0,60.0,265.0,140.0);
INSERT INTO "busterm" VALUES('tb:alu_i/pa_i:f96ba558',NULL,'alu_i/pa_i',-1,25.0,65.0,125.0,135.0,'BLOCK',NULL,NULL,'THRU',20.0,60.0,130.0,140.0);
INSERT INTO "busterm" VALUES('tb:alu_i/pb_i:55fabb5a',NULL,'alu_i/pb_i',-1,160.0,65.0,260.0,135.0,'BLOCK',NULL,NULL,'THRU',155.0,60.0,265.0,140.0);
INSERT INTO "busterm" VALUES('tb:mem_i/qa_i:5308c2f8',NULL,'mem_i/qa_i',-1,725.0,65.0,825.0,135.0,'BLOCK',NULL,NULL,'THRU',720.0,60.0,830.0,140.0);
INSERT INTO "busterm" VALUES('tb:mem_i/qb_i:7b3fd8a7',NULL,'mem_i/qb_i',-1,860.0,65.0,960.0,135.0,'BLOCK',NULL,NULL,'THRU',855.0,60.0,965.0,140.0);
CREATE TABLE cell (
            name      TEXT PRIMARY KEY,
            width     REAL NOT NULL,
            height    REAL NOT NULL,
            bottom_up INTEGER NOT NULL DEFAULT 0
        );
INSERT INTO "cell" VALUES('alu_cell',420.0,200.0,0);
INSERT INTO "cell" VALUES('mem_cell',420.0,200.0,0);
INSERT INTO "cell" VALUES('pipe_cell',110.0,80.0,0);
CREATE TABLE cell_children (
            parent_cell TEXT NOT NULL REFERENCES cell(name),
            inst_name   TEXT NOT NULL,
            child_cell  TEXT NOT NULL REFERENCES cell(name),
            x           REAL NOT NULL DEFAULT 0,
            y           REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (parent_cell, inst_name)
        );
INSERT INTO "cell_children" VALUES('alu_cell','pa_i','pipe_cell',20.0,60.0);
INSERT INTO "cell_children" VALUES('alu_cell','pb_i','pipe_cell',155.0,60.0);
INSERT INTO "cell_children" VALUES('mem_cell','qa_i','pipe_cell',20.0,60.0);
INSERT INTO "cell_children" VALUES('mem_cell','qb_i','pipe_cell',155.0,60.0);
CREATE TABLE cell_pin (
            cell      TEXT NOT NULL REFERENCES cell(name),
            pin_name  TEXT NOT NULL,
            dir       TEXT NOT NULL DEFAULT 'INOUT',
            px        REAL NOT NULL DEFAULT -1,
            py        REAL NOT NULL DEFAULT -1,
            PRIMARY KEY (cell, pin_name)
        );
INSERT INTO "cell_pin" VALUES('pipe_cell','out','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('pipe_cell','in','INPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('alu_cell','top_ab_0','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('mem_cell','top_ab_0','INPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('alu_cell','top_ab_1','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('mem_cell','top_ab_1','INPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('alu_cell','top_ab_2','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('mem_cell','top_ab_2','INPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('alu_cell','top_ab_3','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('mem_cell','top_ab_3','INPUT',-1.0,-1.0);
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
INSERT INTO "component" VALUES(1,'alu_i','alu_cell',NULL,0,0.0,0.0,420.0,200.0,0,0,'N');
INSERT INTO "component" VALUES(2,'alu_i/pa_i','pipe_cell',1,1,20.0,60.0,130.0,140.0,1,0,'N');
INSERT INTO "component" VALUES(3,'alu_i/pb_i','pipe_cell',1,1,155.0,60.0,265.0,140.0,1,0,'N');
INSERT INTO "component" VALUES(4,'mem_i','mem_cell',NULL,0,700.0,0.0,1120.0,200.0,0,0,'N');
INSERT INTO "component" VALUES(5,'mem_i/qa_i','pipe_cell',4,1,720.0,60.0,830.0,140.0,1,0,'N');
INSERT INTO "component" VALUES(6,'mem_i/qb_i','pipe_cell',4,1,855.0,60.0,965.0,140.0,1,0,'N');
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
INSERT INTO "meta" VALUES('schema_version','19');
INSERT INTO "meta" VALUES('bdb_tool','buda-bdb');
INSERT INTO "meta" VALUES('user_ops:1:25c50f104a2a8284','{"base": "new", "ops": ["edit_add_trunk H 30 75 210", "edit_add_stub pa_i 0", "edit_add_stub pb_i 0"]}');
INSERT INTO "meta" VALUES('user_ops:3:7d21bcc030e06124','{"base": "new", "ops": ["edit_add_trunk H 30 75 210", "edit_add_stub qa_i 0", "edit_add_stub qb_i 0"]}');
INSERT INTO "meta" VALUES('user_ops:2:0352631ebf8248f3','{"base": "new", "ops": ["edit_add_trunk H 30 200 760", "edit_add_stub alu_i/pb_i 0", "edit_add_stub mem_i/qa_i 0"]}');
CREATE TABLE net (
            id   INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL
        );
INSERT INTO "net" VALUES(1,'alu_ab_0');
INSERT INTO "net" VALUES(2,'alu_ab_1');
INSERT INTO "net" VALUES(3,'alu_ab_2');
INSERT INTO "net" VALUES(4,'alu_ab_3');
INSERT INTO "net" VALUES(5,'mem_ab_0');
INSERT INTO "net" VALUES(6,'mem_ab_1');
INSERT INTO "net" VALUES(7,'mem_ab_2');
INSERT INTO "net" VALUES(8,'mem_ab_3');
INSERT INTO "net" VALUES(9,'top_ab_0');
INSERT INTO "net" VALUES(10,'top_ab_1');
INSERT INTO "net" VALUES(11,'top_ab_2');
INSERT INTO "net" VALUES(12,'top_ab_3');
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
INSERT INTO "pin" VALUES(1,2,'out','OUTPUT',75.0,100.0);
INSERT INTO "pin" VALUES(1,3,'in','INPUT',210.0,100.0);
INSERT INTO "pin" VALUES(2,2,'out','OUTPUT',75.0,100.0);
INSERT INTO "pin" VALUES(2,3,'in','INPUT',210.0,100.0);
INSERT INTO "pin" VALUES(3,2,'out','OUTPUT',75.0,100.0);
INSERT INTO "pin" VALUES(3,3,'in','INPUT',210.0,100.0);
INSERT INTO "pin" VALUES(4,2,'out','OUTPUT',75.0,100.0);
INSERT INTO "pin" VALUES(4,3,'in','INPUT',210.0,100.0);
INSERT INTO "pin" VALUES(5,5,'out','OUTPUT',775.0,100.0);
INSERT INTO "pin" VALUES(5,6,'in','INPUT',910.0,100.0);
INSERT INTO "pin" VALUES(6,5,'out','OUTPUT',775.0,100.0);
INSERT INTO "pin" VALUES(6,6,'in','INPUT',910.0,100.0);
INSERT INTO "pin" VALUES(7,5,'out','OUTPUT',775.0,100.0);
INSERT INTO "pin" VALUES(7,6,'in','INPUT',910.0,100.0);
INSERT INTO "pin" VALUES(8,5,'out','OUTPUT',775.0,100.0);
INSERT INTO "pin" VALUES(8,6,'in','INPUT',910.0,100.0);
INSERT INTO "pin" VALUES(9,3,'out','OUTPUT',210.0,100.0);
INSERT INTO "pin" VALUES(9,1,'top_ab_0','OUTPUT',210.0,100.0);
INSERT INTO "pin" VALUES(9,5,'in','INPUT',775.0,100.0);
INSERT INTO "pin" VALUES(9,4,'top_ab_0','INPUT',910.0,100.0);
INSERT INTO "pin" VALUES(10,3,'out','OUTPUT',210.0,100.0);
INSERT INTO "pin" VALUES(10,1,'top_ab_1','OUTPUT',210.0,100.0);
INSERT INTO "pin" VALUES(10,5,'in','INPUT',775.0,100.0);
INSERT INTO "pin" VALUES(10,4,'top_ab_1','INPUT',910.0,100.0);
INSERT INTO "pin" VALUES(11,3,'out','OUTPUT',210.0,100.0);
INSERT INTO "pin" VALUES(11,1,'top_ab_2','OUTPUT',210.0,100.0);
INSERT INTO "pin" VALUES(11,5,'in','INPUT',775.0,100.0);
INSERT INTO "pin" VALUES(11,4,'top_ab_2','INPUT',910.0,100.0);
INSERT INTO "pin" VALUES(12,3,'out','OUTPUT',210.0,100.0);
INSERT INTO "pin" VALUES(12,1,'top_ab_3','OUTPUT',210.0,100.0);
INSERT INTO "pin" VALUES(12,5,'in','INPUT',775.0,100.0);
INSERT INTO "pin" VALUES(12,4,'top_ab_3','INPUT',910.0,100.0);
CREATE TABLE route_snapshot (
        id             INTEGER PRIMARY KEY,   -- always 1 (current routing)
        hash           TEXT,
        n_bus_segments INTEGER DEFAULT 0,
        n_bus_vias     INTEGER DEFAULT 0,
        stage          TEXT,                  -- 'abstract_nuts' / 'detailed_nuts'
        n_net_segments INTEGER DEFAULT 0,
        n_net_vias     INTEGER DEFAULT 0
    );
INSERT INTO "route_snapshot" VALUES(1,'c188eb5429a6d1ea45b7a04819d72b60b740877d1f72080676018a437a876b29',9,6,'abstract_nuts',0,0);
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
INSERT INTO "topology" VALUES('1',0,'I_H',25,0,0,'["pa_i", "pb_i"]','[]',0,0,'472b3858725ec5ff','generated');
INSERT INTO "topology" VALUES('1',1,'U_VHV@y148',51,0,0,'["pa_i", "pb_i"]','[]',0,0,'450bb1d41a2d07c7','generated');
INSERT INTO "topology" VALUES('1',2,'U_VHV@y52',51,0,0,'["pa_i", "pb_i"]','[]',0,0,'c0a59fbb1d571246','generated');
INSERT INTO "topology" VALUES('1',3,'Z_HVH@x142@y135',95,0,0,'["pa_i", "pb_i"]','[]',0,0,'ae30b51867a76f39','generated');
INSERT INTO "topology" VALUES('1',4,'Z_HVH@x142@y65',95,0,0,'["pa_i", "pb_i"]','[]',0,0,'d4ee6348d97edd2c','generated');
INSERT INTO "topology" VALUES('1',5,'USER',195,0,0,'["pa_i", "pb_i"]','[]',1,1,'25c50f104a2a8284','user');
INSERT INTO "topology" VALUES('2',0,'I_H',455,0,0,'["alu_i/pb_i", "mem_i/qa_i"]','[]',0,0,'223d35e0f83e2462','generated');
INSERT INTO "topology" VALUES('2',1,'U_VHV@y160',505,0,0,'["alu_i/pb_i", "mem_i/qa_i"]','[]',0,0,'423dafc619997f7c','generated');
INSERT INTO "topology" VALUES('2',2,'U_VHV@y40',505,0,0,'["alu_i/pb_i", "mem_i/qa_i"]','[]',0,0,'39d1b35018c110a0','generated');
INSERT INTO "topology" VALUES('2',3,'Z_HVH@x492@y135',525,0,0,'["alu_i/pb_i", "mem_i/qa_i"]','[]',0,0,'5a8430a61bf30326','generated');
INSERT INTO "topology" VALUES('2',4,'Z_HVH@x492@y65',525,0,0,'["alu_i/pb_i", "mem_i/qa_i"]','[]',0,0,'9245be0cbea9647b','generated');
INSERT INTO "topology" VALUES('2',5,'USER',620,0,0,'["alu_i/pb_i", "mem_i/qa_i"]','[]',1,1,'0352631ebf8248f3','user');
INSERT INTO "topology" VALUES('3',0,'I_H',25,0,0,'["qa_i", "qb_i"]','[]',0,0,'a6d263738835626f','generated');
INSERT INTO "topology" VALUES('3',1,'U_VHV@y148',51,0,0,'["qa_i", "qb_i"]','[]',0,0,'5cf8b6da7e8066a7','generated');
INSERT INTO "topology" VALUES('3',2,'U_VHV@y52',51,0,0,'["qa_i", "qb_i"]','[]',0,0,'08497293062c6426','generated');
INSERT INTO "topology" VALUES('3',3,'Z_HVH@x142@y135',95,0,0,'["qa_i", "qb_i"]','[]',0,0,'bb65b10d0100ce49','generated');
INSERT INTO "topology" VALUES('3',4,'Z_HVH@x142@y65',95,0,0,'["qa_i", "qb_i"]','[]',0,0,'016a7a1b42ff6e7c','generated');
INSERT INTO "topology" VALUES('3',5,'USER',195,0,0,'["qa_i", "qb_i"]','[]',1,1,'7d21bcc030e06124','user');
INSERT INTO "topology" VALUES('4',5,'USER',195,0,0,'["alu_i/pa_i", "alu_i/pb_i"]','[]',1,0,'8f70ba0e4007607c','user');
INSERT INTO "topology" VALUES('5',5,'USER',195,0,0,'["mem_i/qa_i", "mem_i/qb_i"]','[]',1,0,'5233c790b5ebe1f4','user');
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
INSERT INTO "topology_seg_busterm" VALUES('1',0,0,'start','tb:pa_i:f96ba558');
INSERT INTO "topology_seg_busterm" VALUES('1',0,0,'end','tb:pb_i:55fabb5a');
INSERT INTO "topology_seg_busterm" VALUES('1',1,0,'start','tb:pa_i:f96ba558');
INSERT INTO "topology_seg_busterm" VALUES('1',1,2,'end','tb:pb_i:55fabb5a');
INSERT INTO "topology_seg_busterm" VALUES('1',2,0,'start','tb:pa_i:f96ba558');
INSERT INTO "topology_seg_busterm" VALUES('1',2,2,'end','tb:pb_i:55fabb5a');
INSERT INTO "topology_seg_busterm" VALUES('1',3,0,'start','tb:pa_i:f96ba558');
INSERT INTO "topology_seg_busterm" VALUES('1',3,2,'end','tb:pb_i:55fabb5a');
INSERT INTO "topology_seg_busterm" VALUES('1',4,0,'start','tb:pa_i:f96ba558');
INSERT INTO "topology_seg_busterm" VALUES('1',4,2,'end','tb:pb_i:55fabb5a');
INSERT INTO "topology_seg_busterm" VALUES('1',5,1,'start','tb:pa_i:f96ba558');
INSERT INTO "topology_seg_busterm" VALUES('1',5,2,'start','tb:pb_i:55fabb5a');
INSERT INTO "topology_seg_busterm" VALUES('2',0,0,'start','tb:alu_i/pb_i:55fabb5a');
INSERT INTO "topology_seg_busterm" VALUES('2',0,0,'end','tb:mem_i/qa_i:5308c2f8');
INSERT INTO "topology_seg_busterm" VALUES('2',1,0,'start','tb:alu_i/pb_i:55fabb5a');
INSERT INTO "topology_seg_busterm" VALUES('2',1,2,'end','tb:mem_i/qa_i:5308c2f8');
INSERT INTO "topology_seg_busterm" VALUES('2',2,0,'start','tb:alu_i/pb_i:55fabb5a');
INSERT INTO "topology_seg_busterm" VALUES('2',2,2,'end','tb:mem_i/qa_i:5308c2f8');
INSERT INTO "topology_seg_busterm" VALUES('2',3,0,'start','tb:alu_i/pb_i:55fabb5a');
INSERT INTO "topology_seg_busterm" VALUES('2',3,2,'end','tb:mem_i/qa_i:5308c2f8');
INSERT INTO "topology_seg_busterm" VALUES('2',4,0,'start','tb:alu_i/pb_i:55fabb5a');
INSERT INTO "topology_seg_busterm" VALUES('2',4,2,'end','tb:mem_i/qa_i:5308c2f8');
INSERT INTO "topology_seg_busterm" VALUES('2',5,1,'start','tb:alu_i/pb_i:55fabb5a');
INSERT INTO "topology_seg_busterm" VALUES('2',5,2,'start','tb:mem_i/qa_i:5308c2f8');
INSERT INTO "topology_seg_busterm" VALUES('3',0,0,'start','tb:qa_i:f96ba558');
INSERT INTO "topology_seg_busterm" VALUES('3',0,0,'end','tb:qb_i:55fabb5a');
INSERT INTO "topology_seg_busterm" VALUES('3',1,0,'start','tb:qa_i:f96ba558');
INSERT INTO "topology_seg_busterm" VALUES('3',1,2,'end','tb:qb_i:55fabb5a');
INSERT INTO "topology_seg_busterm" VALUES('3',2,0,'start','tb:qa_i:f96ba558');
INSERT INTO "topology_seg_busterm" VALUES('3',2,2,'end','tb:qb_i:55fabb5a');
INSERT INTO "topology_seg_busterm" VALUES('3',3,0,'start','tb:qa_i:f96ba558');
INSERT INTO "topology_seg_busterm" VALUES('3',3,2,'end','tb:qb_i:55fabb5a');
INSERT INTO "topology_seg_busterm" VALUES('3',4,0,'start','tb:qa_i:f96ba558');
INSERT INTO "topology_seg_busterm" VALUES('3',4,2,'end','tb:qb_i:55fabb5a');
INSERT INTO "topology_seg_busterm" VALUES('3',5,1,'start','tb:qa_i:f96ba558');
INSERT INTO "topology_seg_busterm" VALUES('3',5,2,'start','tb:qb_i:55fabb5a');
INSERT INTO "topology_seg_busterm" VALUES('4',5,1,'start','tb:alu_i/pa_i:f96ba558');
INSERT INTO "topology_seg_busterm" VALUES('4',5,2,'start','tb:alu_i/pb_i:55fabb5a');
INSERT INTO "topology_seg_busterm" VALUES('5',5,1,'start','tb:mem_i/qa_i:5308c2f8');
INSERT INTO "topology_seg_busterm" VALUES('5',5,2,'start','tb:mem_i/qb_i:7b3fd8a7');
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
INSERT INTO "topology_seg_conn" VALUES('1',1,0,'end',1);
INSERT INTO "topology_seg_conn" VALUES('1',1,1,'start',0);
INSERT INTO "topology_seg_conn" VALUES('1',1,1,'end',2);
INSERT INTO "topology_seg_conn" VALUES('1',1,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',2,0,'end',1);
INSERT INTO "topology_seg_conn" VALUES('1',2,1,'start',0);
INSERT INTO "topology_seg_conn" VALUES('1',2,1,'end',2);
INSERT INTO "topology_seg_conn" VALUES('1',2,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',3,0,'end',1);
INSERT INTO "topology_seg_conn" VALUES('1',3,1,'start',0);
INSERT INTO "topology_seg_conn" VALUES('1',3,1,'end',2);
INSERT INTO "topology_seg_conn" VALUES('1',3,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',4,0,'end',1);
INSERT INTO "topology_seg_conn" VALUES('1',4,1,'start',0);
INSERT INTO "topology_seg_conn" VALUES('1',4,1,'end',2);
INSERT INTO "topology_seg_conn" VALUES('1',4,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',5,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',5,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('2',1,0,'end',1);
INSERT INTO "topology_seg_conn" VALUES('2',1,1,'start',0);
INSERT INTO "topology_seg_conn" VALUES('2',1,1,'end',2);
INSERT INTO "topology_seg_conn" VALUES('2',1,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('2',2,0,'end',1);
INSERT INTO "topology_seg_conn" VALUES('2',2,1,'start',0);
INSERT INTO "topology_seg_conn" VALUES('2',2,1,'end',2);
INSERT INTO "topology_seg_conn" VALUES('2',2,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('2',3,0,'end',1);
INSERT INTO "topology_seg_conn" VALUES('2',3,1,'start',0);
INSERT INTO "topology_seg_conn" VALUES('2',3,1,'end',2);
INSERT INTO "topology_seg_conn" VALUES('2',3,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('2',4,0,'end',1);
INSERT INTO "topology_seg_conn" VALUES('2',4,1,'start',0);
INSERT INTO "topology_seg_conn" VALUES('2',4,1,'end',2);
INSERT INTO "topology_seg_conn" VALUES('2',4,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('2',5,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('2',5,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',1,0,'end',1);
INSERT INTO "topology_seg_conn" VALUES('3',1,1,'start',0);
INSERT INTO "topology_seg_conn" VALUES('3',1,1,'end',2);
INSERT INTO "topology_seg_conn" VALUES('3',1,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('3',2,0,'end',1);
INSERT INTO "topology_seg_conn" VALUES('3',2,1,'start',0);
INSERT INTO "topology_seg_conn" VALUES('3',2,1,'end',2);
INSERT INTO "topology_seg_conn" VALUES('3',2,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('3',3,0,'end',1);
INSERT INTO "topology_seg_conn" VALUES('3',3,1,'start',0);
INSERT INTO "topology_seg_conn" VALUES('3',3,1,'end',2);
INSERT INTO "topology_seg_conn" VALUES('3',3,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('3',4,0,'end',1);
INSERT INTO "topology_seg_conn" VALUES('3',4,1,'start',0);
INSERT INTO "topology_seg_conn" VALUES('3',4,1,'end',2);
INSERT INTO "topology_seg_conn" VALUES('3',4,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('3',5,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',5,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('4',5,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('4',5,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',5,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',5,2,'end',0);
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
INSERT INTO "topology_segment" VALUES('1',0,0,130,100,155,100,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',1,0,125,140,125,148,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',1,1,125,148,160,148,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',1,2,160,148,160,140,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',2,0,125,60,125,52,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',2,1,125,52,160,52,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',2,2,160,52,160,60,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',3,0,130,135,142,135,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',3,1,142,135,142,65,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',3,2,142,65,155,65,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',4,0,130,65,142,65,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',4,1,142,65,142,135,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',4,2,142,135,155,135,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',5,0,75,30,210,30,4,0,-1,-1,-2147483648,60);
INSERT INTO "topology_segment" VALUES('1',5,1,102,60,102,30,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',5,2,182,60,182,30,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',0,0,265,100,720,100,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',1,0,260,140,260,160,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',1,1,260,160,725,160,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',1,2,725,160,725,140,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',2,0,260,60,260,40,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',2,1,260,40,725,40,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',2,2,725,40,725,60,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',3,0,265,135,492,135,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',3,1,492,135,492,65,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',3,2,492,65,720,65,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',4,0,265,65,492,65,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',4,1,492,65,492,135,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',4,2,492,135,720,135,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',5,0,200,30,760,30,4,0,4,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',5,1,232,60,232,30,5,0,5,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',5,2,740,60,740,30,5,0,5,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',0,0,130,100,155,100,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',1,0,125,140,125,148,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',1,1,125,148,160,148,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',1,2,160,148,160,140,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',2,0,125,60,125,52,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',2,1,125,52,160,52,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',2,2,160,52,160,60,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',3,0,130,135,142,135,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',3,1,142,135,142,65,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',3,2,142,65,155,65,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',4,0,130,65,142,65,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',4,1,142,65,142,135,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',4,2,142,135,155,135,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',5,0,75,30,210,30,4,0,-1,-1,-2147483648,60);
INSERT INTO "topology_segment" VALUES('3',5,1,102,60,102,30,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',5,2,182,60,182,30,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('4',5,0,75,30,210,30,4,0,4,-1,-2147483648,60);
INSERT INTO "topology_segment" VALUES('4',5,1,102,60,102,30,5,0,5,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('4',5,2,182,60,182,30,5,0,5,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',5,0,775,30,910,30,4,0,4,-1,-2147483648,60);
INSERT INTO "topology_segment" VALUES('5',5,1,802,60,802,30,5,0,5,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',5,2,882,60,882,30,5,0,5,-1,-2147483648,2147483647);
COMMIT;
