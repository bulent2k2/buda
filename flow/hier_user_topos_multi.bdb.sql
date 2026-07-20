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
INSERT INTO "bundle" VALUES('1',1,'STRICT','DRV:core_i1/c0|REC:core_i1/c1,core_i1/c2,core_i1/c3,',4,'core_cell','["core_i1", "core_i2"]',NULL,0,-1,-1,'','[]','',0,0,'');
INSERT INTO "bundle" VALUES('5',1,'STRICT','DRV:io_i1/p0|REC:io_i1/p1,io_i1/p2,io_i1/p3,',4,'io_cell','["io_i1", "io_i2"]',NULL,0,-1,-1,'','[]','',0,0,'');
INSERT INTO "bundle" VALUES('2',0,'STRICT','DRV:core_i1/c1|REC:io_i1/p0,',2,'','[]',NULL,0,-1,-1,'','[]','',0,0,'');
INSERT INTO "bundle" VALUES('3',1,'STRICT','DRV:core_i2/c0|REC:core_i2/c1,core_i2/c2,core_i2/c3,',4,'core_cell','["core_i2"]','1',0,-1,-1,'','[]','',0,0,'');
INSERT INTO "bundle" VALUES('4',0,'STRICT','DRV:core_i2/c1|REC:io_i2/p0,',2,'','[]',NULL,0,-1,-1,'','[]','',0,0,'');
INSERT INTO "bundle" VALUES('6',1,'STRICT','DRV:io_i2/p0|REC:io_i2/p1,io_i2/p2,io_i2/p3,',4,'io_cell','["io_i2"]','5',0,-1,-1,'','[]','',0,0,'');
INSERT INTO "bundle" VALUES('7',1,'STRICT','DRV:core_i1/c0|REC:core_i1/c1,core_i1/c2,core_i1/c3,',4,'core_cell','["core_i1"]','1',1,-1,-1,'','[]','',1,0,'');
INSERT INTO "bundle" VALUES('8',1,'STRICT','DRV:core_i1/c0|REC:core_i1/c1,core_i1/c2,core_i1/c3,',4,'core_cell','["core_i2"]','1',1,-1,-1,'','[]','',1,0,'');
INSERT INTO "bundle" VALUES('9',1,'STRICT','DRV:io_i1/p0|REC:io_i1/p1,io_i1/p2,io_i1/p3,',4,'io_cell','["io_i1"]','5',1,-1,-1,'','[]','',1,0,'');
INSERT INTO "bundle" VALUES('10',1,'STRICT','DRV:io_i1/p0|REC:io_i1/p1,io_i1/p2,io_i1/p3,',4,'io_cell','["io_i2"]','5',1,-1,-1,'','[]','',1,0,'');
CREATE TABLE bundle_busterm (
        bundle_id  TEXT REFERENCES bundle(id),
        busterm_id TEXT,
        role       TEXT DEFAULT '',   -- 'entry' | 'exit' (hier flow)
        PRIMARY KEY (bundle_id, busterm_id, role)
    );
INSERT INTO "bundle_busterm" VALUES('1','bt:core_i1/c0','entry');
INSERT INTO "bundle_busterm" VALUES('1','bt:core_i1/c1','exit');
INSERT INTO "bundle_busterm" VALUES('1','bt:core_i1/c2','exit');
INSERT INTO "bundle_busterm" VALUES('1','bt:core_i1/c3','exit');
INSERT INTO "bundle_busterm" VALUES('2','bt:core_i1/c1','entry');
INSERT INTO "bundle_busterm" VALUES('2','bt:io_i1/p0','exit');
INSERT INTO "bundle_busterm" VALUES('3','bt:core_i2/c0','entry');
INSERT INTO "bundle_busterm" VALUES('3','bt:core_i2/c1','exit');
INSERT INTO "bundle_busterm" VALUES('3','bt:core_i2/c2','exit');
INSERT INTO "bundle_busterm" VALUES('3','bt:core_i2/c3','exit');
INSERT INTO "bundle_busterm" VALUES('4','bt:core_i2/c1','entry');
INSERT INTO "bundle_busterm" VALUES('4','bt:io_i2/p0','exit');
INSERT INTO "bundle_busterm" VALUES('5','bt:io_i1/p0','entry');
INSERT INTO "bundle_busterm" VALUES('5','bt:io_i1/p1','exit');
INSERT INTO "bundle_busterm" VALUES('5','bt:io_i1/p2','exit');
INSERT INTO "bundle_busterm" VALUES('5','bt:io_i1/p3','exit');
INSERT INTO "bundle_busterm" VALUES('6','bt:io_i2/p0','entry');
INSERT INTO "bundle_busterm" VALUES('6','bt:io_i2/p1','exit');
INSERT INTO "bundle_busterm" VALUES('6','bt:io_i2/p2','exit');
INSERT INTO "bundle_busterm" VALUES('6','bt:io_i2/p3','exit');
INSERT INTO "bundle_busterm" VALUES('7','bt:core_i1/c0','entry');
INSERT INTO "bundle_busterm" VALUES('7','bt:core_i1/c1','exit');
INSERT INTO "bundle_busterm" VALUES('7','bt:core_i1/c2','exit');
INSERT INTO "bundle_busterm" VALUES('7','bt:core_i1/c3','exit');
INSERT INTO "bundle_busterm" VALUES('8','bt:core_i2/c0','entry');
INSERT INTO "bundle_busterm" VALUES('8','bt:core_i2/c1','exit');
INSERT INTO "bundle_busterm" VALUES('8','bt:core_i2/c2','exit');
INSERT INTO "bundle_busterm" VALUES('8','bt:core_i2/c3','exit');
INSERT INTO "bundle_busterm" VALUES('9','bt:io_i1/p0','entry');
INSERT INTO "bundle_busterm" VALUES('9','bt:io_i1/p1','exit');
INSERT INTO "bundle_busterm" VALUES('9','bt:io_i1/p2','exit');
INSERT INTO "bundle_busterm" VALUES('9','bt:io_i1/p3','exit');
INSERT INTO "bundle_busterm" VALUES('10','bt:io_i2/p0','entry');
INSERT INTO "bundle_busterm" VALUES('10','bt:io_i2/p1','exit');
INSERT INTO "bundle_busterm" VALUES('10','bt:io_i2/p2','exit');
INSERT INTO "bundle_busterm" VALUES('10','bt:io_i2/p3','exit');
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
INSERT INTO "bundle_net" VALUES('2',17,0);
INSERT INTO "bundle_net" VALUES('2',18,1);
INSERT INTO "bundle_net" VALUES('2',19,2);
INSERT INTO "bundle_net" VALUES('2',20,3);
INSERT INTO "bundle_net" VALUES('3',5,0);
INSERT INTO "bundle_net" VALUES('3',6,1);
INSERT INTO "bundle_net" VALUES('3',7,2);
INSERT INTO "bundle_net" VALUES('3',8,3);
INSERT INTO "bundle_net" VALUES('4',21,0);
INSERT INTO "bundle_net" VALUES('4',22,1);
INSERT INTO "bundle_net" VALUES('4',23,2);
INSERT INTO "bundle_net" VALUES('4',24,3);
INSERT INTO "bundle_net" VALUES('5',9,0);
INSERT INTO "bundle_net" VALUES('5',10,1);
INSERT INTO "bundle_net" VALUES('5',11,2);
INSERT INTO "bundle_net" VALUES('5',12,3);
INSERT INTO "bundle_net" VALUES('6',13,0);
INSERT INTO "bundle_net" VALUES('6',14,1);
INSERT INTO "bundle_net" VALUES('6',15,2);
INSERT INTO "bundle_net" VALUES('6',16,3);
INSERT INTO "bundle_net" VALUES('7',1,0);
INSERT INTO "bundle_net" VALUES('7',2,1);
INSERT INTO "bundle_net" VALUES('7',3,2);
INSERT INTO "bundle_net" VALUES('7',4,3);
INSERT INTO "bundle_net" VALUES('8',5,0);
INSERT INTO "bundle_net" VALUES('8',6,1);
INSERT INTO "bundle_net" VALUES('8',7,2);
INSERT INTO "bundle_net" VALUES('8',8,3);
INSERT INTO "bundle_net" VALUES('9',9,0);
INSERT INTO "bundle_net" VALUES('9',10,1);
INSERT INTO "bundle_net" VALUES('9',11,2);
INSERT INTO "bundle_net" VALUES('9',12,3);
INSERT INTO "bundle_net" VALUES('10',13,0);
INSERT INTO "bundle_net" VALUES('10',14,1);
INSERT INTO "bundle_net" VALUES('10',15,2);
INSERT INTO "bundle_net" VALUES('10',16,3);
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
INSERT INTO "bus_segment" VALUES('7',0,4,1,1.21666666666666671e+02,1.16666666666666671e+02,1.63333333333333342e+02,1.23333333333333328e+02,120.0,6.66666666666666696e+00,1,0,105.6,134.4,NULL,NULL);
INSERT INTO "bus_segment" VALUES('7',1,5,0,1.18333333333333342e+02,100.0,125.0,120.0,1.21666666666666671e+02,6.66666666666666696e+00,1,0,25.0,125.0,NULL,NULL);
INSERT INTO "bus_segment" VALUES('7',2,5,0,160.0,100.0,1.66666666666666685e+02,120.0,1.63333333333333342e+02,6.66666666666666696e+00,1,0,160.0,260.0,NULL,NULL);
INSERT INTO "bus_segment" VALUES('7',3,5,0,1.18333333333333342e+02,120.0,125.0,140.0,1.21666666666666671e+02,6.66666666666666696e+00,1,0,25.0,125.0,NULL,NULL);
INSERT INTO "bus_segment" VALUES('7',4,5,0,160.0,120.0,1.66666666666666685e+02,140.0,1.63333333333333342e+02,6.66666666666666696e+00,1,0,160.0,260.0,NULL,NULL);
INSERT INTO "bus_segment" VALUES('8',0,4,1,1.21666666666666671e+02,5.16666666666666628e+02,1.63333333333333342e+02,5.23333333333333371e+02,520.0,6.66666666666666696e+00,1,0,505.6,534.4,NULL,NULL);
INSERT INTO "bus_segment" VALUES('8',1,5,0,1.18333333333333342e+02,500.0,125.0,520.0,1.21666666666666671e+02,6.66666666666666696e+00,1,0,25.0,125.0,NULL,NULL);
INSERT INTO "bus_segment" VALUES('8',2,5,0,160.0,500.0,1.66666666666666685e+02,520.0,1.63333333333333342e+02,6.66666666666666696e+00,1,0,160.0,260.0,NULL,NULL);
INSERT INTO "bus_segment" VALUES('8',3,5,0,1.18333333333333342e+02,520.0,125.0,540.0,1.21666666666666671e+02,6.66666666666666696e+00,1,0,25.0,125.0,NULL,NULL);
INSERT INTO "bus_segment" VALUES('8',4,5,0,160.0,520.0,1.66666666666666685e+02,540.0,1.63333333333333342e+02,6.66666666666666696e+00,1,0,160.0,260.0,NULL,NULL);
INSERT INTO "bus_segment" VALUES('2',0,4,1,2.56666666666666685e+02,102.0,6.28333333333333371e+02,1.08666666666666657e+02,1.05333333333333328e+02,6.66666666666666696e+00,1,0,102.0,586.2,NULL,NULL);
INSERT INTO "bus_segment" VALUES('2',1,5,0,2.53333333333333342e+02,100.0,260.0,1.05333333333333328e+02,2.56666666666666685e+02,6.66666666666666696e+00,1,0,160.0,260.0,NULL,NULL);
INSERT INTO "bus_segment" VALUES('2',2,5,0,625.0,100.0,6.31666666666666742e+02,1.05333333333333328e+02,6.28333333333333371e+02,6.66666666666666696e+00,1,0,625.0,725.0,NULL,NULL);
INSERT INTO "bus_segment" VALUES('4',0,4,1,265.0,4.56666666666666685e+02,620.0,4.63333333333333314e+02,460.0,6.66666666666666696e+00,1,0,425.0,495.0,NULL,NULL);
INSERT INTO "bus_segment" VALUES('9',0,4,1,7.21666666666666628e+02,1.16666666666666671e+02,7.63333333333333371e+02,1.23333333333333328e+02,120.0,6.66666666666666696e+00,1,0,105.6,134.4,NULL,NULL);
INSERT INTO "bus_segment" VALUES('9',1,5,0,7.18333333333333257e+02,100.0,725.0,120.0,7.21666666666666628e+02,6.66666666666666696e+00,1,0,625.0,725.0,NULL,NULL);
INSERT INTO "bus_segment" VALUES('9',2,5,0,760.0,100.0,7.66666666666666742e+02,120.0,7.63333333333333371e+02,6.66666666666666696e+00,1,0,760.0,860.0,NULL,NULL);
INSERT INTO "bus_segment" VALUES('9',3,5,0,7.18333333333333257e+02,120.0,725.0,140.0,7.21666666666666628e+02,6.66666666666666696e+00,1,0,625.0,725.0,NULL,NULL);
INSERT INTO "bus_segment" VALUES('9',4,5,0,760.0,120.0,7.66666666666666742e+02,140.0,7.63333333333333371e+02,6.66666666666666696e+00,1,0,760.0,860.0,NULL,NULL);
INSERT INTO "bus_segment" VALUES('10',0,4,1,7.21666666666666628e+02,5.16666666666666628e+02,7.63333333333333371e+02,5.23333333333333371e+02,520.0,6.66666666666666696e+00,1,0,505.6,534.4,NULL,NULL);
INSERT INTO "bus_segment" VALUES('10',1,5,0,7.18333333333333257e+02,500.0,725.0,520.0,7.21666666666666628e+02,6.66666666666666696e+00,1,0,625.0,725.0,NULL,NULL);
INSERT INTO "bus_segment" VALUES('10',2,5,0,760.0,500.0,7.66666666666666742e+02,520.0,7.63333333333333371e+02,6.66666666666666696e+00,1,0,760.0,860.0,NULL,NULL);
INSERT INTO "bus_segment" VALUES('10',3,5,0,7.18333333333333257e+02,520.0,725.0,540.0,7.21666666666666628e+02,6.66666666666666696e+00,1,0,625.0,725.0,NULL,NULL);
INSERT INTO "bus_segment" VALUES('10',4,5,0,760.0,520.0,7.66666666666666742e+02,540.0,7.63333333333333371e+02,6.66666666666666696e+00,1,0,760.0,860.0,NULL,NULL);
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
INSERT INTO "bus_via" VALUES('7',0,1,4,5,1.21666666666666671e+02,120.0,4);
INSERT INTO "bus_via" VALUES('7',0,3,4,5,1.21666666666666671e+02,120.0,4);
INSERT INTO "bus_via" VALUES('7',0,2,4,5,1.63333333333333342e+02,120.0,4);
INSERT INTO "bus_via" VALUES('7',0,4,4,5,1.63333333333333342e+02,120.0,4);
INSERT INTO "bus_via" VALUES('8',0,1,4,5,1.21666666666666671e+02,520.0,4);
INSERT INTO "bus_via" VALUES('8',0,3,4,5,1.21666666666666671e+02,520.0,4);
INSERT INTO "bus_via" VALUES('8',0,2,4,5,1.63333333333333342e+02,520.0,4);
INSERT INTO "bus_via" VALUES('8',0,4,4,5,1.63333333333333342e+02,520.0,4);
INSERT INTO "bus_via" VALUES('2',0,1,4,5,2.56666666666666685e+02,1.05333333333333328e+02,4);
INSERT INTO "bus_via" VALUES('2',0,2,4,5,6.28333333333333371e+02,1.05333333333333328e+02,4);
INSERT INTO "bus_via" VALUES('9',0,1,4,5,7.21666666666666628e+02,120.0,4);
INSERT INTO "bus_via" VALUES('9',0,3,4,5,7.21666666666666628e+02,120.0,4);
INSERT INTO "bus_via" VALUES('9',0,2,4,5,7.63333333333333371e+02,120.0,4);
INSERT INTO "bus_via" VALUES('9',0,4,4,5,7.63333333333333371e+02,120.0,4);
INSERT INTO "bus_via" VALUES('10',0,1,4,5,7.21666666666666628e+02,520.0,4);
INSERT INTO "bus_via" VALUES('10',0,3,4,5,7.21666666666666628e+02,520.0,4);
INSERT INTO "bus_via" VALUES('10',0,2,4,5,7.63333333333333371e+02,520.0,4);
INSERT INTO "bus_via" VALUES('10',0,4,4,5,7.63333333333333371e+02,520.0,4);
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
INSERT INTO "busterm" VALUES('bt:core_i1',1,'core_i1',0,0.0,0.0,300.0,220.0,'BLOCK',NULL,NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:core_i1/c0',2,'core_i1/c0',1,20.0,20.0,130.0,100.0,'SPATIAL_CLUSTER','bt:core_i1',NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:core_i1/c1',3,'core_i1/c1',1,155.0,20.0,265.0,100.0,'SPATIAL_CLUSTER','bt:core_i1',NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:core_i1/c2',4,'core_i1/c2',1,20.0,140.0,130.0,220.0,'SPATIAL_CLUSTER','bt:core_i1',NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:core_i1/c3',5,'core_i1/c3',1,155.0,140.0,265.0,220.0,'SPATIAL_CLUSTER','bt:core_i1',NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:core_i2',6,'core_i2',0,0.0,400.0,300.0,620.0,'BLOCK',NULL,NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:core_i2/c0',7,'core_i2/c0',1,20.0,420.0,130.0,500.0,'SPATIAL_CLUSTER','bt:core_i2',NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:core_i2/c1',8,'core_i2/c1',1,155.0,420.0,265.0,500.0,'SPATIAL_CLUSTER','bt:core_i2',NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:core_i2/c2',9,'core_i2/c2',1,20.0,540.0,130.0,620.0,'SPATIAL_CLUSTER','bt:core_i2',NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:core_i2/c3',10,'core_i2/c3',1,155.0,540.0,265.0,620.0,'SPATIAL_CLUSTER','bt:core_i2',NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:io_i1',11,'io_i1',0,600.0,0.0,900.0,220.0,'BLOCK',NULL,NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:io_i1/p0',12,'io_i1/p0',1,620.0,20.0,730.0,100.0,'SPATIAL_CLUSTER','bt:io_i1',NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:io_i1/p1',13,'io_i1/p1',1,755.0,20.0,865.0,100.0,'SPATIAL_CLUSTER','bt:io_i1',NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:io_i1/p2',14,'io_i1/p2',1,620.0,140.0,730.0,220.0,'SPATIAL_CLUSTER','bt:io_i1',NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:io_i1/p3',15,'io_i1/p3',1,755.0,140.0,865.0,220.0,'SPATIAL_CLUSTER','bt:io_i1',NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:io_i2',16,'io_i2',0,600.0,400.0,900.0,620.0,'BLOCK',NULL,NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:io_i2/p0',17,'io_i2/p0',1,620.0,420.0,730.0,500.0,'SPATIAL_CLUSTER','bt:io_i2',NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:io_i2/p1',18,'io_i2/p1',1,755.0,420.0,865.0,500.0,'SPATIAL_CLUSTER','bt:io_i2',NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:io_i2/p2',19,'io_i2/p2',1,620.0,540.0,730.0,620.0,'SPATIAL_CLUSTER','bt:io_i2',NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('bt:io_i2/p3',20,'io_i2/p3',1,755.0,540.0,865.0,620.0,'SPATIAL_CLUSTER','bt:io_i2',NULL,'THRU',0.0,0.0,0.0,0.0);
INSERT INTO "busterm" VALUES('tb:c1:9de8e535',NULL,'c1',-1,160.0,25.0,260.0,95.0,'BLOCK',NULL,NULL,'THRU',155.0,20.0,265.0,100.0);
INSERT INTO "busterm" VALUES('tb:c3:04987d71',NULL,'c3',-1,160.0,145.0,260.0,215.0,'BLOCK',NULL,NULL,'THRU',155.0,140.0,265.0,220.0);
INSERT INTO "busterm" VALUES('tb:c0:45852417',NULL,'c0',-1,25.0,25.0,125.0,95.0,'BLOCK',NULL,NULL,'THRU',20.0,20.0,130.0,100.0);
INSERT INTO "busterm" VALUES('tb:c2:57747c65',NULL,'c2',-1,25.0,145.0,125.0,215.0,'BLOCK',NULL,NULL,'THRU',20.0,140.0,130.0,220.0);
INSERT INTO "busterm" VALUES('tb:p1:9de8e535',NULL,'p1',-1,160.0,25.0,260.0,95.0,'BLOCK',NULL,NULL,'THRU',155.0,20.0,265.0,100.0);
INSERT INTO "busterm" VALUES('tb:p3:04987d71',NULL,'p3',-1,160.0,145.0,260.0,215.0,'BLOCK',NULL,NULL,'THRU',155.0,140.0,265.0,220.0);
INSERT INTO "busterm" VALUES('tb:p0:45852417',NULL,'p0',-1,25.0,25.0,125.0,95.0,'BLOCK',NULL,NULL,'THRU',20.0,20.0,130.0,100.0);
INSERT INTO "busterm" VALUES('tb:p2:57747c65',NULL,'p2',-1,25.0,145.0,125.0,215.0,'BLOCK',NULL,NULL,'THRU',20.0,140.0,130.0,220.0);
INSERT INTO "busterm" VALUES('tb:core_i1/c0:45852417',NULL,'core_i1/c0',-1,25.0,25.0,125.0,95.0,'BLOCK',NULL,NULL,'THRU',20.0,20.0,130.0,100.0);
INSERT INTO "busterm" VALUES('tb:core_i1/c1:9de8e535',NULL,'core_i1/c1',-1,160.0,25.0,260.0,95.0,'BLOCK',NULL,NULL,'THRU',155.0,20.0,265.0,100.0);
INSERT INTO "busterm" VALUES('tb:core_i1/c2:57747c65',NULL,'core_i1/c2',-1,25.0,145.0,125.0,215.0,'BLOCK',NULL,NULL,'THRU',20.0,140.0,130.0,220.0);
INSERT INTO "busterm" VALUES('tb:core_i1/c3:04987d71',NULL,'core_i1/c3',-1,160.0,145.0,260.0,215.0,'BLOCK',NULL,NULL,'THRU',155.0,140.0,265.0,220.0);
INSERT INTO "busterm" VALUES('tb:core_i2/c0:a4b2ea53',NULL,'core_i2/c0',-1,25.0,425.0,125.0,495.0,'BLOCK',NULL,NULL,'THRU',20.0,420.0,130.0,500.0);
INSERT INTO "busterm" VALUES('tb:core_i2/c1:3fa69235',NULL,'core_i2/c1',-1,160.0,425.0,260.0,495.0,'BLOCK',NULL,NULL,'THRU',155.0,420.0,265.0,500.0);
INSERT INTO "busterm" VALUES('tb:core_i2/c2:4557d89a',NULL,'core_i2/c2',-1,25.0,545.0,125.0,615.0,'BLOCK',NULL,NULL,'THRU',20.0,540.0,130.0,620.0);
INSERT INTO "busterm" VALUES('tb:core_i2/c3:3babf00b',NULL,'core_i2/c3',-1,160.0,545.0,260.0,615.0,'BLOCK',NULL,NULL,'THRU',155.0,540.0,265.0,620.0);
INSERT INTO "busterm" VALUES('tb:io_i1/p0:3c92ce8b',NULL,'io_i1/p0',-1,625.0,25.0,725.0,95.0,'BLOCK',NULL,NULL,'THRU',620.0,20.0,730.0,100.0);
INSERT INTO "busterm" VALUES('tb:io_i1/p1:7aa75d71',NULL,'io_i1/p1',-1,760.0,25.0,860.0,95.0,'BLOCK',NULL,NULL,'THRU',755.0,20.0,865.0,100.0);
INSERT INTO "busterm" VALUES('tb:io_i1/p2:a24c4f13',NULL,'io_i1/p2',-1,625.0,145.0,725.0,215.0,'BLOCK',NULL,NULL,'THRU',620.0,140.0,730.0,220.0);
INSERT INTO "busterm" VALUES('tb:io_i1/p3:c95c4bd2',NULL,'io_i1/p3',-1,760.0,145.0,860.0,215.0,'BLOCK',NULL,NULL,'THRU',755.0,140.0,865.0,220.0);
INSERT INTO "busterm" VALUES('tb:io_i2/p0:c6afff11',NULL,'io_i2/p0',-1,625.0,425.0,725.0,495.0,'BLOCK',NULL,NULL,'THRU',620.0,420.0,730.0,500.0);
INSERT INTO "busterm" VALUES('tb:io_i2/p1:54856147',NULL,'io_i2/p1',-1,760.0,425.0,860.0,495.0,'BLOCK',NULL,NULL,'THRU',755.0,420.0,865.0,500.0);
INSERT INTO "busterm" VALUES('tb:io_i2/p2:d40cce8c',NULL,'io_i2/p2',-1,625.0,545.0,725.0,615.0,'BLOCK',NULL,NULL,'THRU',620.0,540.0,730.0,620.0);
INSERT INTO "busterm" VALUES('tb:io_i2/p3:559d80b6',NULL,'io_i2/p3',-1,760.0,545.0,860.0,615.0,'BLOCK',NULL,NULL,'THRU',755.0,540.0,865.0,620.0);
CREATE TABLE cell (
            name      TEXT PRIMARY KEY,
            width     REAL NOT NULL,
            height    REAL NOT NULL,
            bottom_up INTEGER NOT NULL DEFAULT 0
        );
INSERT INTO "cell" VALUES('core_cell',300.0,220.0,0);
INSERT INTO "cell" VALUES('io_cell',300.0,220.0,0);
INSERT INTO "cell" VALUES('pipe_cell',110.0,80.0,0);
CREATE TABLE cell_children (
            parent_cell TEXT NOT NULL REFERENCES cell(name),
            inst_name   TEXT NOT NULL,
            child_cell  TEXT NOT NULL REFERENCES cell(name),
            x           REAL NOT NULL DEFAULT 0,
            y           REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (parent_cell, inst_name)
        );
INSERT INTO "cell_children" VALUES('core_cell','c0','pipe_cell',20.0,20.0);
INSERT INTO "cell_children" VALUES('core_cell','c1','pipe_cell',155.0,20.0);
INSERT INTO "cell_children" VALUES('core_cell','c2','pipe_cell',20.0,140.0);
INSERT INTO "cell_children" VALUES('core_cell','c3','pipe_cell',155.0,140.0);
INSERT INTO "cell_children" VALUES('io_cell','p0','pipe_cell',20.0,20.0);
INSERT INTO "cell_children" VALUES('io_cell','p1','pipe_cell',155.0,20.0);
INSERT INTO "cell_children" VALUES('io_cell','p2','pipe_cell',20.0,140.0);
INSERT INTO "cell_children" VALUES('io_cell','p3','pipe_cell',155.0,140.0);
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
INSERT INTO "cell_pin" VALUES('core_cell','x1_0','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('io_cell','x1_0','INPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('core_cell','x1_1','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('io_cell','x1_1','INPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('core_cell','x1_2','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('io_cell','x1_2','INPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('core_cell','x1_3','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('io_cell','x1_3','INPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('core_cell','x2_0','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('io_cell','x2_0','INPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('core_cell','x2_1','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('io_cell','x2_1','INPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('core_cell','x2_2','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('io_cell','x2_2','INPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('core_cell','x2_3','OUTPUT',-1.0,-1.0);
INSERT INTO "cell_pin" VALUES('io_cell','x2_3','INPUT',-1.0,-1.0);
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
INSERT INTO "component" VALUES(1,'core_i1','core_cell',NULL,0,0.0,0.0,300.0,220.0,0,0,'N');
INSERT INTO "component" VALUES(2,'core_i1/c0','pipe_cell',1,1,20.0,20.0,130.0,100.0,1,0,'N');
INSERT INTO "component" VALUES(3,'core_i1/c1','pipe_cell',1,1,155.0,20.0,265.0,100.0,1,0,'N');
INSERT INTO "component" VALUES(4,'core_i1/c2','pipe_cell',1,1,20.0,140.0,130.0,220.0,1,0,'N');
INSERT INTO "component" VALUES(5,'core_i1/c3','pipe_cell',1,1,155.0,140.0,265.0,220.0,1,0,'N');
INSERT INTO "component" VALUES(6,'core_i2','core_cell',NULL,0,0.0,400.0,300.0,620.0,0,0,'N');
INSERT INTO "component" VALUES(7,'core_i2/c0','pipe_cell',6,1,20.0,420.0,130.0,500.0,1,0,'N');
INSERT INTO "component" VALUES(8,'core_i2/c1','pipe_cell',6,1,155.0,420.0,265.0,500.0,1,0,'N');
INSERT INTO "component" VALUES(9,'core_i2/c2','pipe_cell',6,1,20.0,540.0,130.0,620.0,1,0,'N');
INSERT INTO "component" VALUES(10,'core_i2/c3','pipe_cell',6,1,155.0,540.0,265.0,620.0,1,0,'N');
INSERT INTO "component" VALUES(11,'io_i1','io_cell',NULL,0,600.0,0.0,900.0,220.0,0,0,'N');
INSERT INTO "component" VALUES(12,'io_i1/p0','pipe_cell',11,1,620.0,20.0,730.0,100.0,1,0,'N');
INSERT INTO "component" VALUES(13,'io_i1/p1','pipe_cell',11,1,755.0,20.0,865.0,100.0,1,0,'N');
INSERT INTO "component" VALUES(14,'io_i1/p2','pipe_cell',11,1,620.0,140.0,730.0,220.0,1,0,'N');
INSERT INTO "component" VALUES(15,'io_i1/p3','pipe_cell',11,1,755.0,140.0,865.0,220.0,1,0,'N');
INSERT INTO "component" VALUES(16,'io_i2','io_cell',NULL,0,600.0,400.0,900.0,620.0,0,0,'N');
INSERT INTO "component" VALUES(17,'io_i2/p0','pipe_cell',16,1,620.0,420.0,730.0,500.0,1,0,'N');
INSERT INTO "component" VALUES(18,'io_i2/p1','pipe_cell',16,1,755.0,420.0,865.0,500.0,1,0,'N');
INSERT INTO "component" VALUES(19,'io_i2/p2','pipe_cell',16,1,620.0,540.0,730.0,620.0,1,0,'N');
INSERT INTO "component" VALUES(20,'io_i2/p3','pipe_cell',16,1,755.0,540.0,865.0,620.0,1,0,'N');
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
INSERT INTO "meta" VALUES('user_ops:1:4ce78f1c89e03913','{"base": "new", "ops": ["edit_add_trunk H 115 20 265", "edit_add_stub c0 0", "edit_add_stub c1 0", "edit_add_stub c2 0", "edit_add_stub c3 0", "edit_set_span 0 75 210"]}');
INSERT INTO "meta" VALUES('user_ops:5:e1095f3ba0d36c73','{"base": "new", "ops": ["edit_add_trunk H 115 20 265", "edit_add_stub p0 0", "edit_add_stub p1 0", "edit_add_stub p2 0", "edit_add_stub p3 0", "edit_set_span 0 75 210"]}');
INSERT INTO "meta" VALUES('user_ops:2:d08d7a1dd5fb5c99','{"base": "new", "ops": ["edit_add_trunk H 115 200 680", "edit_add_stub core_i1/c1 0", "edit_add_stub io_i1/p0 0", "edit_set_span 0 232 650"]}');
CREATE TABLE net (
            id   INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL
        );
INSERT INTO "net" VALUES(1,'core_a1_0');
INSERT INTO "net" VALUES(2,'core_a1_1');
INSERT INTO "net" VALUES(3,'core_a1_2');
INSERT INTO "net" VALUES(4,'core_a1_3');
INSERT INTO "net" VALUES(5,'core_a2_0');
INSERT INTO "net" VALUES(6,'core_a2_1');
INSERT INTO "net" VALUES(7,'core_a2_2');
INSERT INTO "net" VALUES(8,'core_a2_3');
INSERT INTO "net" VALUES(9,'io_a1_0');
INSERT INTO "net" VALUES(10,'io_a1_1');
INSERT INTO "net" VALUES(11,'io_a1_2');
INSERT INTO "net" VALUES(12,'io_a1_3');
INSERT INTO "net" VALUES(13,'io_a2_0');
INSERT INTO "net" VALUES(14,'io_a2_1');
INSERT INTO "net" VALUES(15,'io_a2_2');
INSERT INTO "net" VALUES(16,'io_a2_3');
INSERT INTO "net" VALUES(17,'x1_0');
INSERT INTO "net" VALUES(18,'x1_1');
INSERT INTO "net" VALUES(19,'x1_2');
INSERT INTO "net" VALUES(20,'x1_3');
INSERT INTO "net" VALUES(21,'x2_0');
INSERT INTO "net" VALUES(22,'x2_1');
INSERT INTO "net" VALUES(23,'x2_2');
INSERT INTO "net" VALUES(24,'x2_3');
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
INSERT INTO "net_props" VALUES(13,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(14,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(15,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(16,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(17,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(18,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(19,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(20,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(21,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(22,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(23,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "net_props" VALUES(24,NULL,NULL,NULL,NULL,NULL,NULL);
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
INSERT INTO "pin" VALUES(1,2,'out','OUTPUT',75.0,60.0);
INSERT INTO "pin" VALUES(1,3,'in','INPUT',210.0,60.0);
INSERT INTO "pin" VALUES(1,4,'in','INPUT',75.0,180.0);
INSERT INTO "pin" VALUES(1,5,'in','INPUT',210.0,180.0);
INSERT INTO "pin" VALUES(2,2,'out','OUTPUT',75.0,60.0);
INSERT INTO "pin" VALUES(2,3,'in','INPUT',210.0,60.0);
INSERT INTO "pin" VALUES(2,4,'in','INPUT',75.0,180.0);
INSERT INTO "pin" VALUES(2,5,'in','INPUT',210.0,180.0);
INSERT INTO "pin" VALUES(3,2,'out','OUTPUT',75.0,60.0);
INSERT INTO "pin" VALUES(3,3,'in','INPUT',210.0,60.0);
INSERT INTO "pin" VALUES(3,4,'in','INPUT',75.0,180.0);
INSERT INTO "pin" VALUES(3,5,'in','INPUT',210.0,180.0);
INSERT INTO "pin" VALUES(4,2,'out','OUTPUT',75.0,60.0);
INSERT INTO "pin" VALUES(4,3,'in','INPUT',210.0,60.0);
INSERT INTO "pin" VALUES(4,4,'in','INPUT',75.0,180.0);
INSERT INTO "pin" VALUES(4,5,'in','INPUT',210.0,180.0);
INSERT INTO "pin" VALUES(5,7,'out','OUTPUT',75.0,460.0);
INSERT INTO "pin" VALUES(5,8,'in','INPUT',210.0,460.0);
INSERT INTO "pin" VALUES(5,9,'in','INPUT',75.0,580.0);
INSERT INTO "pin" VALUES(5,10,'in','INPUT',210.0,580.0);
INSERT INTO "pin" VALUES(6,7,'out','OUTPUT',75.0,460.0);
INSERT INTO "pin" VALUES(6,8,'in','INPUT',210.0,460.0);
INSERT INTO "pin" VALUES(6,9,'in','INPUT',75.0,580.0);
INSERT INTO "pin" VALUES(6,10,'in','INPUT',210.0,580.0);
INSERT INTO "pin" VALUES(7,7,'out','OUTPUT',75.0,460.0);
INSERT INTO "pin" VALUES(7,8,'in','INPUT',210.0,460.0);
INSERT INTO "pin" VALUES(7,9,'in','INPUT',75.0,580.0);
INSERT INTO "pin" VALUES(7,10,'in','INPUT',210.0,580.0);
INSERT INTO "pin" VALUES(8,7,'out','OUTPUT',75.0,460.0);
INSERT INTO "pin" VALUES(8,8,'in','INPUT',210.0,460.0);
INSERT INTO "pin" VALUES(8,9,'in','INPUT',75.0,580.0);
INSERT INTO "pin" VALUES(8,10,'in','INPUT',210.0,580.0);
INSERT INTO "pin" VALUES(9,12,'out','OUTPUT',675.0,60.0);
INSERT INTO "pin" VALUES(9,13,'in','INPUT',810.0,60.0);
INSERT INTO "pin" VALUES(9,14,'in','INPUT',675.0,180.0);
INSERT INTO "pin" VALUES(9,15,'in','INPUT',810.0,180.0);
INSERT INTO "pin" VALUES(10,12,'out','OUTPUT',675.0,60.0);
INSERT INTO "pin" VALUES(10,13,'in','INPUT',810.0,60.0);
INSERT INTO "pin" VALUES(10,14,'in','INPUT',675.0,180.0);
INSERT INTO "pin" VALUES(10,15,'in','INPUT',810.0,180.0);
INSERT INTO "pin" VALUES(11,12,'out','OUTPUT',675.0,60.0);
INSERT INTO "pin" VALUES(11,13,'in','INPUT',810.0,60.0);
INSERT INTO "pin" VALUES(11,14,'in','INPUT',675.0,180.0);
INSERT INTO "pin" VALUES(11,15,'in','INPUT',810.0,180.0);
INSERT INTO "pin" VALUES(12,12,'out','OUTPUT',675.0,60.0);
INSERT INTO "pin" VALUES(12,13,'in','INPUT',810.0,60.0);
INSERT INTO "pin" VALUES(12,14,'in','INPUT',675.0,180.0);
INSERT INTO "pin" VALUES(12,15,'in','INPUT',810.0,180.0);
INSERT INTO "pin" VALUES(13,17,'out','OUTPUT',675.0,460.0);
INSERT INTO "pin" VALUES(13,18,'in','INPUT',810.0,460.0);
INSERT INTO "pin" VALUES(13,19,'in','INPUT',675.0,580.0);
INSERT INTO "pin" VALUES(13,20,'in','INPUT',810.0,580.0);
INSERT INTO "pin" VALUES(14,17,'out','OUTPUT',675.0,460.0);
INSERT INTO "pin" VALUES(14,18,'in','INPUT',810.0,460.0);
INSERT INTO "pin" VALUES(14,19,'in','INPUT',675.0,580.0);
INSERT INTO "pin" VALUES(14,20,'in','INPUT',810.0,580.0);
INSERT INTO "pin" VALUES(15,17,'out','OUTPUT',675.0,460.0);
INSERT INTO "pin" VALUES(15,18,'in','INPUT',810.0,460.0);
INSERT INTO "pin" VALUES(15,19,'in','INPUT',675.0,580.0);
INSERT INTO "pin" VALUES(15,20,'in','INPUT',810.0,580.0);
INSERT INTO "pin" VALUES(16,17,'out','OUTPUT',675.0,460.0);
INSERT INTO "pin" VALUES(16,18,'in','INPUT',810.0,460.0);
INSERT INTO "pin" VALUES(16,19,'in','INPUT',675.0,580.0);
INSERT INTO "pin" VALUES(16,20,'in','INPUT',810.0,580.0);
INSERT INTO "pin" VALUES(17,3,'out','OUTPUT',210.0,60.0);
INSERT INTO "pin" VALUES(17,1,'x1_0','OUTPUT',150.0,110.0);
INSERT INTO "pin" VALUES(17,12,'in','INPUT',675.0,60.0);
INSERT INTO "pin" VALUES(17,11,'x1_0','INPUT',750.0,110.0);
INSERT INTO "pin" VALUES(18,3,'out','OUTPUT',210.0,60.0);
INSERT INTO "pin" VALUES(18,1,'x1_1','OUTPUT',150.0,110.0);
INSERT INTO "pin" VALUES(18,12,'in','INPUT',675.0,60.0);
INSERT INTO "pin" VALUES(18,11,'x1_1','INPUT',750.0,110.0);
INSERT INTO "pin" VALUES(19,3,'out','OUTPUT',210.0,60.0);
INSERT INTO "pin" VALUES(19,1,'x1_2','OUTPUT',150.0,110.0);
INSERT INTO "pin" VALUES(19,12,'in','INPUT',675.0,60.0);
INSERT INTO "pin" VALUES(19,11,'x1_2','INPUT',750.0,110.0);
INSERT INTO "pin" VALUES(20,3,'out','OUTPUT',210.0,60.0);
INSERT INTO "pin" VALUES(20,1,'x1_3','OUTPUT',150.0,110.0);
INSERT INTO "pin" VALUES(20,12,'in','INPUT',675.0,60.0);
INSERT INTO "pin" VALUES(20,11,'x1_3','INPUT',750.0,110.0);
INSERT INTO "pin" VALUES(21,8,'out','OUTPUT',210.0,460.0);
INSERT INTO "pin" VALUES(21,6,'x2_0','OUTPUT',150.0,510.0);
INSERT INTO "pin" VALUES(21,17,'in','INPUT',675.0,460.0);
INSERT INTO "pin" VALUES(21,16,'x2_0','INPUT',750.0,510.0);
INSERT INTO "pin" VALUES(22,8,'out','OUTPUT',210.0,460.0);
INSERT INTO "pin" VALUES(22,6,'x2_1','OUTPUT',150.0,510.0);
INSERT INTO "pin" VALUES(22,17,'in','INPUT',675.0,460.0);
INSERT INTO "pin" VALUES(22,16,'x2_1','INPUT',750.0,510.0);
INSERT INTO "pin" VALUES(23,8,'out','OUTPUT',210.0,460.0);
INSERT INTO "pin" VALUES(23,6,'x2_2','OUTPUT',150.0,510.0);
INSERT INTO "pin" VALUES(23,17,'in','INPUT',675.0,460.0);
INSERT INTO "pin" VALUES(23,16,'x2_2','INPUT',750.0,510.0);
INSERT INTO "pin" VALUES(24,8,'out','OUTPUT',210.0,460.0);
INSERT INTO "pin" VALUES(24,6,'x2_3','OUTPUT',150.0,510.0);
INSERT INTO "pin" VALUES(24,17,'in','INPUT',675.0,460.0);
INSERT INTO "pin" VALUES(24,16,'x2_3','INPUT',750.0,510.0);
CREATE TABLE route_snapshot (
        id             INTEGER PRIMARY KEY,   -- always 1 (current routing)
        hash           TEXT,
        n_bus_segments INTEGER DEFAULT 0,
        n_bus_vias     INTEGER DEFAULT 0,
        stage          TEXT,                  -- 'abstract_nuts' / 'detailed_nuts'
        n_net_segments INTEGER DEFAULT 0,
        n_net_vias     INTEGER DEFAULT 0
    );
INSERT INTO "route_snapshot" VALUES(1,'d1c26fce86ecf6de600bad02088ad9dfb4f1b3ae2b0c2e2cfc1386b4cd75aeee',24,18,'abstract_nuts',0,0);
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
INSERT INTO "topology" VALUES('1',0,'TRUNK_V@x130',100,130,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'50b7820b829a6e30','generated');
INSERT INTO "topology" VALUES('1',1,'TRUNK_V@x155',100,155,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'51f7a20b002bd73c','generated');
INSERT INTO "topology" VALUES('1',2,'TRUNK_V@x142',100,142,0,'["c0", "c1", "c2", "c3"]','[]',0,0,'68a47a079edcbf68','generated');
INSERT INTO "topology" VALUES('1',3,'TRUNK_H@y100',115,100,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'7421b479cc8e1665','generated');
INSERT INTO "topology" VALUES('1',4,'TRUNK_H@y140',115,140,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'ee21f9693f2b1129','generated');
INSERT INTO "topology" VALUES('1',5,'TRUNK_H@y120',115,120,0,'["c0", "c1", "c2", "c3"]','[]',0,0,'8e5d0a487338fb47','generated');
INSERT INTO "topology" VALUES('1',6,'TRUNK_H+MST@y100',140,100,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'e2e508f96fa91a9a','generated');
INSERT INTO "topology" VALUES('1',7,'TRUNK_H+MST@y140',140,140,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'567ac00580a4c518','generated');
INSERT INTO "topology" VALUES('1',8,'TRUNK_H+MST@y180',180,180,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'96b68f8af660c1b5','generated');
INSERT INTO "topology" VALUES('1',9,'TRUNK_H+MST@y60',180,60,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'040e68fe656e88bb','generated');
INSERT INTO "topology" VALUES('1',10,'TRUNK_H@y180',195,180,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'75c27a3d779259d3','generated');
INSERT INTO "topology" VALUES('1',11,'TRUNK_H@y60',195,60,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'c4c163044f79a76d','generated');
INSERT INTO "topology" VALUES('1',12,'TRUNK_V@x210',210,210,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'29d830c03fff8a6c','generated');
INSERT INTO "topology" VALUES('1',13,'TRUNK_V@x75',210,75,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'982ebd3de485a617','generated');
INSERT INTO "topology" VALUES('1',14,'TRUNK_V+MST@x210',225,210,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'d3c6d008623bd500','generated');
INSERT INTO "topology" VALUES('1',15,'TRUNK_V+MST@x75',225,75,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'a81804aa7d55fe2a','generated');
INSERT INTO "topology" VALUES('1',16,'MST_HV',290,0,0,'["c0", "c1", "c2", "c3"]','[]',0,0,'13c54f02b6475e23','generated');
INSERT INTO "topology" VALUES('1',17,'MST_VH',290,0,0,'["c0", "c1", "c2", "c3"]','[]',0,0,'8d9ce7468dd0e15f','generated');
INSERT INTO "topology" VALUES('1',18,'TRUNK_H_OOB@y0',355,0,0,'["c0", "c1", "c2", "c3"]','[]',0,0,'7db0a998768e032d','generated');
INSERT INTO "topology" VALUES('1',19,'TRUNK_H_OOB@y240',355,240,0,'["c0", "c1", "c2", "c3"]','[]',0,0,'152d3242e24cc76d','generated');
INSERT INTO "topology" VALUES('1',20,'TRUNK_H_OOB+MST@y0',365,0,0,'["c0", "c1", "c2", "c3"]','[]',0,0,'1c539b219b2bb84e','generated');
INSERT INTO "topology" VALUES('1',21,'TRUNK_H_OOB+MST@y240',365,240,0,'["c0", "c1", "c2", "c3"]','[]',0,0,'903e6f6b992b07c3','generated');
INSERT INTO "topology" VALUES('1',22,'TRUNK_V_OOB@x-4',368,-4,0,'["c0", "c1", "c2", "c3"]','[]',0,0,'19c37df263a66ba0','generated');
INSERT INTO "topology" VALUES('1',23,'TRUNK_V_OOB@x289',368,289,0,'["c0", "c1", "c2", "c3"]','[]',0,0,'e77c8a1962c0af4a','generated');
INSERT INTO "topology" VALUES('1',24,'BITRUNK_H',550,0,0,'["c0", "c1", "c2", "c3"]','[]',0,0,'dc320ade885b4dc1','generated');
INSERT INTO "topology" VALUES('1',25,'USER',215,0,0,'["c0", "c1", "c2", "c3"]','[]',1,1,'4ce78f1c89e03913','user');
INSERT INTO "topology" VALUES('2',0,'I_H',355,0,0,'["core_i1/c1", "io_i1/p0"]','[]',0,0,'33f550a482865ff2','generated');
INSERT INTO "topology" VALUES('2',1,'U_VHV@y0',405,0,0,'["core_i1/c1", "io_i1/p0"]','[]',0,0,'91e767c6a9b2889b','generated');
INSERT INTO "topology" VALUES('2',2,'U_VHV@y120',405,0,0,'["core_i1/c1", "io_i1/p0"]','[]',0,0,'32f66a6915fb70b8','generated');
INSERT INTO "topology" VALUES('2',3,'Z_HVH@x442@y25',425,0,0,'["core_i1/c1", "io_i1/p0"]','[]',0,0,'be50f6c50463ca56','generated');
INSERT INTO "topology" VALUES('2',4,'Z_HVH@x442@y95',425,0,0,'["core_i1/c1", "io_i1/p0"]','[]',0,0,'7fd219b2b0b66051','generated');
INSERT INTO "topology" VALUES('2',5,'USER',448,0,0,'["core_i1/c1", "io_i1/p0"]','[]',1,1,'d08d7a1dd5fb5c99','user');
INSERT INTO "topology" VALUES('3',0,'TRUNK_V@x130',100,130,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'50b7820b829a6e30','generated');
INSERT INTO "topology" VALUES('3',1,'TRUNK_V@x155',100,155,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'51f7a20b002bd73c','generated');
INSERT INTO "topology" VALUES('3',2,'TRUNK_V@x142',100,142,0,'["c0", "c1", "c2", "c3"]','[]',0,0,'68a47a079edcbf68','generated');
INSERT INTO "topology" VALUES('3',3,'TRUNK_H@y100',115,100,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'7421b479cc8e1665','generated');
INSERT INTO "topology" VALUES('3',4,'TRUNK_H@y140',115,140,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'ee21f9693f2b1129','generated');
INSERT INTO "topology" VALUES('3',5,'TRUNK_H@y120',115,120,0,'["c0", "c1", "c2", "c3"]','[]',0,0,'8e5d0a487338fb47','generated');
INSERT INTO "topology" VALUES('3',6,'TRUNK_H+MST@y100',140,100,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'e2e508f96fa91a9a','generated');
INSERT INTO "topology" VALUES('3',7,'TRUNK_H+MST@y140',140,140,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'567ac00580a4c518','generated');
INSERT INTO "topology" VALUES('3',8,'TRUNK_H+MST@y180',180,180,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'96b68f8af660c1b5','generated');
INSERT INTO "topology" VALUES('3',9,'TRUNK_H+MST@y60',180,60,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'040e68fe656e88bb','generated');
INSERT INTO "topology" VALUES('3',10,'TRUNK_H@y180',195,180,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'75c27a3d779259d3','generated');
INSERT INTO "topology" VALUES('3',11,'TRUNK_H@y60',195,60,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'c4c163044f79a76d','generated');
INSERT INTO "topology" VALUES('3',12,'TRUNK_V@x210',210,210,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'29d830c03fff8a6c','generated');
INSERT INTO "topology" VALUES('3',13,'TRUNK_V@x75',210,75,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'982ebd3de485a617','generated');
INSERT INTO "topology" VALUES('3',14,'TRUNK_V+MST@x210',225,210,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'d3c6d008623bd500','generated');
INSERT INTO "topology" VALUES('3',15,'TRUNK_V+MST@x75',225,75,2,'["c0", "c1", "c2", "c3"]','[]',0,0,'a81804aa7d55fe2a','generated');
INSERT INTO "topology" VALUES('3',16,'MST_HV',290,0,0,'["c0", "c1", "c2", "c3"]','[]',0,0,'13c54f02b6475e23','generated');
INSERT INTO "topology" VALUES('3',17,'MST_VH',290,0,0,'["c0", "c1", "c2", "c3"]','[]',0,0,'8d9ce7468dd0e15f','generated');
INSERT INTO "topology" VALUES('3',18,'TRUNK_H_OOB@y0',355,0,0,'["c0", "c1", "c2", "c3"]','[]',0,0,'7db0a998768e032d','generated');
INSERT INTO "topology" VALUES('3',19,'TRUNK_H_OOB@y240',355,240,0,'["c0", "c1", "c2", "c3"]','[]',0,0,'152d3242e24cc76d','generated');
INSERT INTO "topology" VALUES('3',20,'TRUNK_H_OOB+MST@y0',365,0,0,'["c0", "c1", "c2", "c3"]','[]',0,0,'1c539b219b2bb84e','generated');
INSERT INTO "topology" VALUES('3',21,'TRUNK_H_OOB+MST@y240',365,240,0,'["c0", "c1", "c2", "c3"]','[]',0,0,'903e6f6b992b07c3','generated');
INSERT INTO "topology" VALUES('3',22,'TRUNK_V_OOB@x-4',368,-4,0,'["c0", "c1", "c2", "c3"]','[]',0,0,'19c37df263a66ba0','generated');
INSERT INTO "topology" VALUES('3',23,'TRUNK_V_OOB@x289',368,289,0,'["c0", "c1", "c2", "c3"]','[]',0,0,'e77c8a1962c0af4a','generated');
INSERT INTO "topology" VALUES('3',24,'BITRUNK_H',550,0,0,'["c0", "c1", "c2", "c3"]','[]',0,0,'dc320ade885b4dc1','generated');
INSERT INTO "topology" VALUES('4',0,'I_H',355,0,0,'["core_i2/c1", "io_i2/p0"]','[]',1,0,'ecf8a670773eb536','generated');
INSERT INTO "topology" VALUES('4',1,'U_VHV@y400',405,0,0,'["core_i2/c1", "io_i2/p0"]','[]',0,0,'3f532f4fdad86d91','generated');
INSERT INTO "topology" VALUES('4',2,'U_VHV@y520',405,0,0,'["core_i2/c1", "io_i2/p0"]','[]',0,0,'cac7162f35d75900','generated');
INSERT INTO "topology" VALUES('4',3,'Z_HVH@x442@y425',425,0,0,'["core_i2/c1", "io_i2/p0"]','[]',0,0,'b36b38e2ce6d606b','generated');
INSERT INTO "topology" VALUES('4',4,'Z_HVH@x442@y495',425,0,0,'["core_i2/c1", "io_i2/p0"]','[]',0,0,'e93b4fb4a6f1fca8','generated');
INSERT INTO "topology" VALUES('5',0,'TRUNK_V@x130',100,130,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'ba8faea03b755504','generated');
INSERT INTO "topology" VALUES('5',1,'TRUNK_V@x155',100,155,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'59d3f491b7432580','generated');
INSERT INTO "topology" VALUES('5',2,'TRUNK_V@x142',100,142,0,'["p0", "p1", "p2", "p3"]','[]',0,0,'79159e4fe2b17a28','generated');
INSERT INTO "topology" VALUES('5',3,'TRUNK_H@y100',115,100,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'ac5bb8123dcc0215','generated');
INSERT INTO "topology" VALUES('5',4,'TRUNK_H@y140',115,140,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'5a9b8b9493e4388d','generated');
INSERT INTO "topology" VALUES('5',5,'TRUNK_H@y120',115,120,0,'["p0", "p1", "p2", "p3"]','[]',0,0,'959f28d835d4b887','generated');
INSERT INTO "topology" VALUES('5',6,'TRUNK_H+MST@y100',140,100,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'7ca012c591f22a59','generated');
INSERT INTO "topology" VALUES('5',7,'TRUNK_H+MST@y140',140,140,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'502f59f63389d05b','generated');
INSERT INTO "topology" VALUES('5',8,'TRUNK_H+MST@y180',180,180,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'78c6b0f749366ba6','generated');
INSERT INTO "topology" VALUES('5',9,'TRUNK_H+MST@y60',180,60,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'38ebfbcb50f18be0','generated');
INSERT INTO "topology" VALUES('5',10,'TRUNK_H@y180',195,180,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'00ca3f83e1056633','generated');
INSERT INTO "topology" VALUES('5',11,'TRUNK_H@y60',195,60,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'4bdb2644bcc6d49d','generated');
INSERT INTO "topology" VALUES('5',12,'TRUNK_V@x210',210,210,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'4902c33efae24c58','generated');
INSERT INTO "topology" VALUES('5',13,'TRUNK_V@x75',210,75,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'56104d2cbe616cab','generated');
INSERT INTO "topology" VALUES('5',14,'TRUNK_V+MST@x210',225,210,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'6364e5ae48645e93','generated');
INSERT INTO "topology" VALUES('5',15,'TRUNK_V+MST@x75',225,75,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'178ee54f116ae3d9','generated');
INSERT INTO "topology" VALUES('5',16,'MST_HV',290,0,0,'["p0", "p1", "p2", "p3"]','[]',0,0,'a7adc274cfd21f1f','generated');
INSERT INTO "topology" VALUES('5',17,'MST_VH',290,0,0,'["p0", "p1", "p2", "p3"]','[]',0,0,'1fd5094cf17e9c9b','generated');
INSERT INTO "topology" VALUES('5',18,'TRUNK_H_OOB@y0',355,0,0,'["p0", "p1", "p2", "p3"]','[]',0,0,'bcdcc7065e9f7c5d','generated');
INSERT INTO "topology" VALUES('5',19,'TRUNK_H_OOB@y240',355,240,0,'["p0", "p1", "p2", "p3"]','[]',0,0,'5ec640d50c19949d','generated');
INSERT INTO "topology" VALUES('5',20,'TRUNK_H_OOB+MST@y0',365,0,0,'["p0", "p1", "p2", "p3"]','[]',0,0,'3a61d229302d1c12','generated');
INSERT INTO "topology" VALUES('5',21,'TRUNK_H_OOB+MST@y240',365,240,0,'["p0", "p1", "p2", "p3"]','[]',0,0,'dab66efd284ddca3','generated');
INSERT INTO "topology" VALUES('5',22,'TRUNK_V_OOB@x-4',368,-4,0,'["p0", "p1", "p2", "p3"]','[]',0,0,'29bfe76fb9823274','generated');
INSERT INTO "topology" VALUES('5',23,'TRUNK_V_OOB@x289',368,289,0,'["p0", "p1", "p2", "p3"]','[]',0,0,'52cc4db1f5d65c56','generated');
INSERT INTO "topology" VALUES('5',24,'BITRUNK_H',550,0,0,'["p0", "p1", "p2", "p3"]','[]',0,0,'ffe3d0709cdc5d29','generated');
INSERT INTO "topology" VALUES('5',25,'USER',215,0,0,'["p0", "p1", "p2", "p3"]','[]',1,1,'e1095f3ba0d36c73','user');
INSERT INTO "topology" VALUES('6',0,'TRUNK_V@x130',100,130,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'ba8faea03b755504','generated');
INSERT INTO "topology" VALUES('6',1,'TRUNK_V@x155',100,155,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'59d3f491b7432580','generated');
INSERT INTO "topology" VALUES('6',2,'TRUNK_V@x142',100,142,0,'["p0", "p1", "p2", "p3"]','[]',0,0,'79159e4fe2b17a28','generated');
INSERT INTO "topology" VALUES('6',3,'TRUNK_H@y100',115,100,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'ac5bb8123dcc0215','generated');
INSERT INTO "topology" VALUES('6',4,'TRUNK_H@y140',115,140,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'5a9b8b9493e4388d','generated');
INSERT INTO "topology" VALUES('6',5,'TRUNK_H@y120',115,120,0,'["p0", "p1", "p2", "p3"]','[]',0,0,'959f28d835d4b887','generated');
INSERT INTO "topology" VALUES('6',6,'TRUNK_H+MST@y100',140,100,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'7ca012c591f22a59','generated');
INSERT INTO "topology" VALUES('6',7,'TRUNK_H+MST@y140',140,140,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'502f59f63389d05b','generated');
INSERT INTO "topology" VALUES('6',8,'TRUNK_H+MST@y180',180,180,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'78c6b0f749366ba6','generated');
INSERT INTO "topology" VALUES('6',9,'TRUNK_H+MST@y60',180,60,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'38ebfbcb50f18be0','generated');
INSERT INTO "topology" VALUES('6',10,'TRUNK_H@y180',195,180,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'00ca3f83e1056633','generated');
INSERT INTO "topology" VALUES('6',11,'TRUNK_H@y60',195,60,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'4bdb2644bcc6d49d','generated');
INSERT INTO "topology" VALUES('6',12,'TRUNK_V@x210',210,210,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'4902c33efae24c58','generated');
INSERT INTO "topology" VALUES('6',13,'TRUNK_V@x75',210,75,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'56104d2cbe616cab','generated');
INSERT INTO "topology" VALUES('6',14,'TRUNK_V+MST@x210',225,210,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'6364e5ae48645e93','generated');
INSERT INTO "topology" VALUES('6',15,'TRUNK_V+MST@x75',225,75,2,'["p0", "p1", "p2", "p3"]','[]',0,0,'178ee54f116ae3d9','generated');
INSERT INTO "topology" VALUES('6',16,'MST_HV',290,0,0,'["p0", "p1", "p2", "p3"]','[]',0,0,'a7adc274cfd21f1f','generated');
INSERT INTO "topology" VALUES('6',17,'MST_VH',290,0,0,'["p0", "p1", "p2", "p3"]','[]',0,0,'1fd5094cf17e9c9b','generated');
INSERT INTO "topology" VALUES('6',18,'TRUNK_H_OOB@y0',355,0,0,'["p0", "p1", "p2", "p3"]','[]',0,0,'bcdcc7065e9f7c5d','generated');
INSERT INTO "topology" VALUES('6',19,'TRUNK_H_OOB@y240',355,240,0,'["p0", "p1", "p2", "p3"]','[]',0,0,'5ec640d50c19949d','generated');
INSERT INTO "topology" VALUES('6',20,'TRUNK_H_OOB+MST@y0',365,0,0,'["p0", "p1", "p2", "p3"]','[]',0,0,'3a61d229302d1c12','generated');
INSERT INTO "topology" VALUES('6',21,'TRUNK_H_OOB+MST@y240',365,240,0,'["p0", "p1", "p2", "p3"]','[]',0,0,'dab66efd284ddca3','generated');
INSERT INTO "topology" VALUES('6',22,'TRUNK_V_OOB@x-4',368,-4,0,'["p0", "p1", "p2", "p3"]','[]',0,0,'29bfe76fb9823274','generated');
INSERT INTO "topology" VALUES('6',23,'TRUNK_V_OOB@x289',368,289,0,'["p0", "p1", "p2", "p3"]','[]',0,0,'52cc4db1f5d65c56','generated');
INSERT INTO "topology" VALUES('6',24,'BITRUNK_H',550,0,0,'["p0", "p1", "p2", "p3"]','[]',0,0,'ffe3d0709cdc5d29','generated');
INSERT INTO "topology" VALUES('7',25,'USER',215,0,0,'["core_i1/c0", "core_i1/c1", "core_i1/c2", "core_i1/c3"]','[]',1,0,'3b04b300243673db','user');
INSERT INTO "topology" VALUES('8',25,'USER',215,0,0,'["core_i2/c0", "core_i2/c1", "core_i2/c2", "core_i2/c3"]','[]',1,0,'7320128af50d3bdf','user');
INSERT INTO "topology" VALUES('9',25,'USER',215,0,0,'["io_i1/p0", "io_i1/p1", "io_i1/p2", "io_i1/p3"]','[]',1,0,'8bd2f7c7866fcb90','user');
INSERT INTO "topology" VALUES('10',25,'USER',215,0,0,'["io_i2/p0", "io_i2/p1", "io_i2/p2", "io_i2/p3"]','[]',1,0,'a9f46dbb35d0d844','user');
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
INSERT INTO "topology_seg_busterm" VALUES('1',0,1,'start','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('1',0,2,'start','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('1',1,1,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('1',1,2,'start','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('1',2,1,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('1',2,2,'start','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('1',2,3,'start','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('1',2,4,'start','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('1',3,1,'start','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('1',3,2,'start','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('1',4,1,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('1',4,2,'start','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('1',5,1,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('1',5,2,'start','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('1',5,3,'start','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('1',5,4,'start','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('1',6,0,'end','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('1',6,1,'end','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('1',6,2,'end','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('1',7,0,'end','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('1',7,1,'end','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('1',7,2,'end','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('1',8,0,'start','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('1',8,0,'end','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('1',8,2,'end','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('1',9,0,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('1',9,0,'end','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('1',9,2,'end','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('1',10,0,'start','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('1',10,0,'end','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('1',10,1,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('1',10,2,'start','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('1',11,0,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('1',11,0,'end','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('1',11,1,'start','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('1',11,2,'start','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('1',12,0,'start','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('1',12,0,'end','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('1',12,1,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('1',12,2,'start','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('1',13,0,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('1',13,0,'end','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('1',13,1,'start','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('1',13,2,'start','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('1',14,0,'start','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('1',14,0,'end','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('1',14,2,'end','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('1',15,0,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('1',15,0,'end','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('1',15,2,'end','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('1',16,0,'end','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('1',16,1,'end','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('1',17,0,'end','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('1',17,1,'end','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('1',18,1,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('1',18,2,'start','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('1',18,3,'start','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('1',18,4,'start','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('1',19,1,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('1',19,2,'start','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('1',19,3,'start','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('1',19,4,'start','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('1',20,2,'start','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('1',20,3,'end','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('1',21,2,'start','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('1',21,3,'end','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('1',22,1,'start','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('1',22,2,'start','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('1',23,1,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('1',23,2,'start','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('1',24,3,'start','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('1',24,4,'start','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('1',25,1,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('1',25,2,'start','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('1',25,3,'start','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('1',25,4,'start','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('2',0,0,'start','tb:core_i1/c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('2',0,0,'end','tb:io_i1/p0:3c92ce8b');
INSERT INTO "topology_seg_busterm" VALUES('2',1,0,'start','tb:core_i1/c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('2',1,2,'end','tb:io_i1/p0:3c92ce8b');
INSERT INTO "topology_seg_busterm" VALUES('2',2,0,'start','tb:core_i1/c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('2',2,2,'end','tb:io_i1/p0:3c92ce8b');
INSERT INTO "topology_seg_busterm" VALUES('2',3,0,'start','tb:core_i1/c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('2',3,2,'end','tb:io_i1/p0:3c92ce8b');
INSERT INTO "topology_seg_busterm" VALUES('2',4,0,'start','tb:core_i1/c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('2',4,2,'end','tb:io_i1/p0:3c92ce8b');
INSERT INTO "topology_seg_busterm" VALUES('2',5,1,'start','tb:core_i1/c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('2',5,2,'start','tb:io_i1/p0:3c92ce8b');
INSERT INTO "topology_seg_busterm" VALUES('3',0,1,'start','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('3',0,2,'start','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('3',1,1,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('3',1,2,'start','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('3',2,1,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('3',2,2,'start','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('3',2,3,'start','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('3',2,4,'start','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('3',3,1,'start','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('3',3,2,'start','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('3',4,1,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('3',4,2,'start','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('3',5,1,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('3',5,2,'start','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('3',5,3,'start','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('3',5,4,'start','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('3',6,0,'end','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('3',6,1,'end','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('3',6,2,'end','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('3',7,0,'end','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('3',7,1,'end','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('3',7,2,'end','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('3',8,0,'start','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('3',8,0,'end','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('3',8,2,'end','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('3',9,0,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('3',9,0,'end','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('3',9,2,'end','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('3',10,0,'start','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('3',10,0,'end','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('3',10,1,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('3',10,2,'start','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('3',11,0,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('3',11,0,'end','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('3',11,1,'start','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('3',11,2,'start','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('3',12,0,'start','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('3',12,0,'end','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('3',12,1,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('3',12,2,'start','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('3',13,0,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('3',13,0,'end','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('3',13,1,'start','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('3',13,2,'start','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('3',14,0,'start','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('3',14,0,'end','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('3',14,2,'end','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('3',15,0,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('3',15,0,'end','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('3',15,2,'end','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('3',16,0,'end','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('3',16,1,'end','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('3',17,0,'end','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('3',17,1,'end','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('3',18,1,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('3',18,2,'start','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('3',18,3,'start','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('3',18,4,'start','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('3',19,1,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('3',19,2,'start','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('3',19,3,'start','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('3',19,4,'start','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('3',20,2,'start','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('3',20,3,'end','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('3',21,2,'start','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('3',21,3,'end','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('3',22,1,'start','tb:c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('3',22,2,'start','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('3',23,1,'start','tb:c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('3',23,2,'start','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('3',24,3,'start','tb:c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('3',24,4,'start','tb:c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('4',0,0,'start','tb:core_i2/c1:3fa69235');
INSERT INTO "topology_seg_busterm" VALUES('4',0,0,'end','tb:io_i2/p0:c6afff11');
INSERT INTO "topology_seg_busterm" VALUES('4',1,0,'start','tb:core_i2/c1:3fa69235');
INSERT INTO "topology_seg_busterm" VALUES('4',1,2,'end','tb:io_i2/p0:c6afff11');
INSERT INTO "topology_seg_busterm" VALUES('4',2,0,'start','tb:core_i2/c1:3fa69235');
INSERT INTO "topology_seg_busterm" VALUES('4',2,2,'end','tb:io_i2/p0:c6afff11');
INSERT INTO "topology_seg_busterm" VALUES('4',3,0,'start','tb:core_i2/c1:3fa69235');
INSERT INTO "topology_seg_busterm" VALUES('4',3,2,'end','tb:io_i2/p0:c6afff11');
INSERT INTO "topology_seg_busterm" VALUES('4',4,0,'start','tb:core_i2/c1:3fa69235');
INSERT INTO "topology_seg_busterm" VALUES('4',4,2,'end','tb:io_i2/p0:c6afff11');
INSERT INTO "topology_seg_busterm" VALUES('5',0,1,'start','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('5',0,2,'start','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('5',1,1,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('5',1,2,'start','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('5',2,1,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('5',2,2,'start','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('5',2,3,'start','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('5',2,4,'start','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('5',3,1,'start','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('5',3,2,'start','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('5',4,1,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('5',4,2,'start','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('5',5,1,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('5',5,2,'start','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('5',5,3,'start','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('5',5,4,'start','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('5',6,0,'end','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('5',6,1,'end','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('5',6,2,'end','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('5',7,0,'end','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('5',7,1,'end','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('5',7,2,'end','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('5',8,0,'start','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('5',8,0,'end','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('5',8,2,'end','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('5',9,0,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('5',9,0,'end','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('5',9,2,'end','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('5',10,0,'start','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('5',10,0,'end','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('5',10,1,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('5',10,2,'start','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('5',11,0,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('5',11,0,'end','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('5',11,1,'start','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('5',11,2,'start','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('5',12,0,'start','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('5',12,0,'end','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('5',12,1,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('5',12,2,'start','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('5',13,0,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('5',13,0,'end','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('5',13,1,'start','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('5',13,2,'start','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('5',14,0,'start','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('5',14,0,'end','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('5',14,2,'end','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('5',15,0,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('5',15,0,'end','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('5',15,2,'end','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('5',16,0,'end','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('5',16,1,'end','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('5',17,0,'end','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('5',17,1,'end','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('5',18,1,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('5',18,2,'start','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('5',18,3,'start','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('5',18,4,'start','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('5',19,1,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('5',19,2,'start','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('5',19,3,'start','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('5',19,4,'start','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('5',20,2,'start','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('5',20,3,'end','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('5',21,2,'start','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('5',21,3,'end','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('5',22,1,'start','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('5',22,2,'start','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('5',23,1,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('5',23,2,'start','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('5',24,3,'start','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('5',24,4,'start','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('5',25,1,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('5',25,2,'start','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('5',25,3,'start','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('5',25,4,'start','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('6',0,1,'start','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('6',0,2,'start','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('6',1,1,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('6',1,2,'start','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('6',2,1,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('6',2,2,'start','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('6',2,3,'start','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('6',2,4,'start','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('6',3,1,'start','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('6',3,2,'start','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('6',4,1,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('6',4,2,'start','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('6',5,1,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('6',5,2,'start','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('6',5,3,'start','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('6',5,4,'start','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('6',6,0,'end','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('6',6,1,'end','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('6',6,2,'end','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('6',7,0,'end','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('6',7,1,'end','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('6',7,2,'end','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('6',8,0,'start','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('6',8,0,'end','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('6',8,2,'end','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('6',9,0,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('6',9,0,'end','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('6',9,2,'end','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('6',10,0,'start','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('6',10,0,'end','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('6',10,1,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('6',10,2,'start','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('6',11,0,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('6',11,0,'end','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('6',11,1,'start','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('6',11,2,'start','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('6',12,0,'start','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('6',12,0,'end','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('6',12,1,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('6',12,2,'start','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('6',13,0,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('6',13,0,'end','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('6',13,1,'start','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('6',13,2,'start','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('6',14,0,'start','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('6',14,0,'end','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('6',14,2,'end','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('6',15,0,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('6',15,0,'end','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('6',15,2,'end','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('6',16,0,'end','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('6',16,1,'end','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('6',17,0,'end','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('6',17,1,'end','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('6',18,1,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('6',18,2,'start','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('6',18,3,'start','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('6',18,4,'start','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('6',19,1,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('6',19,2,'start','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('6',19,3,'start','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('6',19,4,'start','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('6',20,2,'start','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('6',20,3,'end','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('6',21,2,'start','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('6',21,3,'end','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('6',22,1,'start','tb:p1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('6',22,2,'start','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('6',23,1,'start','tb:p0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('6',23,2,'start','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('6',24,3,'start','tb:p2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('6',24,4,'start','tb:p3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('7',25,1,'start','tb:core_i1/c0:45852417');
INSERT INTO "topology_seg_busterm" VALUES('7',25,2,'start','tb:core_i1/c1:9de8e535');
INSERT INTO "topology_seg_busterm" VALUES('7',25,3,'start','tb:core_i1/c2:57747c65');
INSERT INTO "topology_seg_busterm" VALUES('7',25,4,'start','tb:core_i1/c3:04987d71');
INSERT INTO "topology_seg_busterm" VALUES('8',25,1,'start','tb:core_i2/c0:a4b2ea53');
INSERT INTO "topology_seg_busterm" VALUES('8',25,2,'start','tb:core_i2/c1:3fa69235');
INSERT INTO "topology_seg_busterm" VALUES('8',25,3,'start','tb:core_i2/c2:4557d89a');
INSERT INTO "topology_seg_busterm" VALUES('8',25,4,'start','tb:core_i2/c3:3babf00b');
INSERT INTO "topology_seg_busterm" VALUES('9',25,1,'start','tb:io_i1/p0:3c92ce8b');
INSERT INTO "topology_seg_busterm" VALUES('9',25,2,'start','tb:io_i1/p1:7aa75d71');
INSERT INTO "topology_seg_busterm" VALUES('9',25,3,'start','tb:io_i1/p2:a24c4f13');
INSERT INTO "topology_seg_busterm" VALUES('9',25,4,'start','tb:io_i1/p3:c95c4bd2');
INSERT INTO "topology_seg_busterm" VALUES('10',25,1,'start','tb:io_i2/p0:c6afff11');
INSERT INTO "topology_seg_busterm" VALUES('10',25,2,'start','tb:io_i2/p1:54856147');
INSERT INTO "topology_seg_busterm" VALUES('10',25,3,'start','tb:io_i2/p2:d40cce8c');
INSERT INTO "topology_seg_busterm" VALUES('10',25,4,'start','tb:io_i2/p3:559d80b6');
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
INSERT INTO "topology_seg_conn" VALUES('1',0,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',0,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('1',0,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',0,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',1,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',1,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('1',1,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',1,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',2,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',2,0,'start',2);
INSERT INTO "topology_seg_conn" VALUES('1',2,0,'end',3);
INSERT INTO "topology_seg_conn" VALUES('1',2,0,'end',4);
INSERT INTO "topology_seg_conn" VALUES('1',2,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',2,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',2,3,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',2,4,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',3,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',3,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('1',3,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',3,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',4,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',4,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('1',4,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',4,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',5,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',5,0,'start',3);
INSERT INTO "topology_seg_conn" VALUES('1',5,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('1',5,0,'end',4);
INSERT INTO "topology_seg_conn" VALUES('1',5,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',5,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',5,3,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',5,4,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',6,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',6,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('1',6,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',7,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',7,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('1',7,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',8,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('1',8,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',8,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',9,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('1',9,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',9,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',10,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',10,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',11,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',11,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',12,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',12,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',13,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',13,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',14,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('1',14,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',14,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',15,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('1',15,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',15,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',16,0,'start',2);
INSERT INTO "topology_seg_conn" VALUES('1',16,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('1',16,2,'start',0);
INSERT INTO "topology_seg_conn" VALUES('1',16,2,'end',1);
INSERT INTO "topology_seg_conn" VALUES('1',17,0,'start',2);
INSERT INTO "topology_seg_conn" VALUES('1',17,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('1',17,2,'start',0);
INSERT INTO "topology_seg_conn" VALUES('1',17,2,'end',1);
INSERT INTO "topology_seg_conn" VALUES('1',18,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',18,0,'start',3);
INSERT INTO "topology_seg_conn" VALUES('1',18,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('1',18,0,'end',4);
INSERT INTO "topology_seg_conn" VALUES('1',18,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',18,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',18,3,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',18,4,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',19,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',19,0,'start',3);
INSERT INTO "topology_seg_conn" VALUES('1',19,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('1',19,0,'end',4);
INSERT INTO "topology_seg_conn" VALUES('1',19,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',19,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',19,3,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',19,4,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',20,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',20,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('1',20,1,'start',5);
INSERT INTO "topology_seg_conn" VALUES('1',20,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',20,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',20,3,'start',4);
INSERT INTO "topology_seg_conn" VALUES('1',20,4,'start',5);
INSERT INTO "topology_seg_conn" VALUES('1',20,4,'end',3);
INSERT INTO "topology_seg_conn" VALUES('1',20,5,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',20,5,'end',4);
INSERT INTO "topology_seg_conn" VALUES('1',21,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',21,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('1',21,1,'start',5);
INSERT INTO "topology_seg_conn" VALUES('1',21,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',21,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',21,3,'start',4);
INSERT INTO "topology_seg_conn" VALUES('1',21,4,'start',3);
INSERT INTO "topology_seg_conn" VALUES('1',21,4,'end',5);
INSERT INTO "topology_seg_conn" VALUES('1',21,5,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',21,5,'end',4);
INSERT INTO "topology_seg_conn" VALUES('1',22,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',22,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('1',22,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',22,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',23,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',23,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('1',23,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',23,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',24,0,'start',3);
INSERT INTO "topology_seg_conn" VALUES('1',24,0,'end',4);
INSERT INTO "topology_seg_conn" VALUES('1',24,2,'start',0);
INSERT INTO "topology_seg_conn" VALUES('1',24,2,'end',1);
INSERT INTO "topology_seg_conn" VALUES('1',24,3,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',24,4,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',25,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('1',25,0,'start',3);
INSERT INTO "topology_seg_conn" VALUES('1',25,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('1',25,0,'end',4);
INSERT INTO "topology_seg_conn" VALUES('1',25,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',25,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',25,3,'end',0);
INSERT INTO "topology_seg_conn" VALUES('1',25,4,'end',0);
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
INSERT INTO "topology_seg_conn" VALUES('2',5,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('2',5,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('2',5,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('2',5,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',0,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('3',0,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('3',0,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',0,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',1,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('3',1,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('3',1,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',1,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',2,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('3',2,0,'start',2);
INSERT INTO "topology_seg_conn" VALUES('3',2,0,'end',3);
INSERT INTO "topology_seg_conn" VALUES('3',2,0,'end',4);
INSERT INTO "topology_seg_conn" VALUES('3',2,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',2,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',2,3,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',2,4,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',3,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('3',3,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('3',3,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',3,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',4,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('3',4,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('3',4,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',4,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',5,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('3',5,0,'start',3);
INSERT INTO "topology_seg_conn" VALUES('3',5,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('3',5,0,'end',4);
INSERT INTO "topology_seg_conn" VALUES('3',5,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',5,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',5,3,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',5,4,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',6,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('3',6,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('3',6,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('3',7,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('3',7,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('3',7,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('3',8,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('3',8,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',8,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('3',9,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('3',9,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',9,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('3',10,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',10,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',11,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',11,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',12,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',12,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',13,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',13,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',14,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('3',14,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',14,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('3',15,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('3',15,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',15,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('3',16,0,'start',2);
INSERT INTO "topology_seg_conn" VALUES('3',16,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('3',16,2,'start',0);
INSERT INTO "topology_seg_conn" VALUES('3',16,2,'end',1);
INSERT INTO "topology_seg_conn" VALUES('3',17,0,'start',2);
INSERT INTO "topology_seg_conn" VALUES('3',17,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('3',17,2,'start',0);
INSERT INTO "topology_seg_conn" VALUES('3',17,2,'end',1);
INSERT INTO "topology_seg_conn" VALUES('3',18,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('3',18,0,'start',3);
INSERT INTO "topology_seg_conn" VALUES('3',18,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('3',18,0,'end',4);
INSERT INTO "topology_seg_conn" VALUES('3',18,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',18,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',18,3,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',18,4,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',19,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('3',19,0,'start',3);
INSERT INTO "topology_seg_conn" VALUES('3',19,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('3',19,0,'end',4);
INSERT INTO "topology_seg_conn" VALUES('3',19,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',19,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',19,3,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',19,4,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',20,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('3',20,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('3',20,1,'start',5);
INSERT INTO "topology_seg_conn" VALUES('3',20,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',20,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',20,3,'start',4);
INSERT INTO "topology_seg_conn" VALUES('3',20,4,'start',5);
INSERT INTO "topology_seg_conn" VALUES('3',20,4,'end',3);
INSERT INTO "topology_seg_conn" VALUES('3',20,5,'start',1);
INSERT INTO "topology_seg_conn" VALUES('3',20,5,'end',4);
INSERT INTO "topology_seg_conn" VALUES('3',21,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('3',21,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('3',21,1,'start',5);
INSERT INTO "topology_seg_conn" VALUES('3',21,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',21,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',21,3,'start',4);
INSERT INTO "topology_seg_conn" VALUES('3',21,4,'start',3);
INSERT INTO "topology_seg_conn" VALUES('3',21,4,'end',5);
INSERT INTO "topology_seg_conn" VALUES('3',21,5,'start',1);
INSERT INTO "topology_seg_conn" VALUES('3',21,5,'end',4);
INSERT INTO "topology_seg_conn" VALUES('3',22,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('3',22,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('3',22,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',22,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',23,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('3',23,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('3',23,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',23,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',24,0,'start',3);
INSERT INTO "topology_seg_conn" VALUES('3',24,0,'end',4);
INSERT INTO "topology_seg_conn" VALUES('3',24,2,'start',0);
INSERT INTO "topology_seg_conn" VALUES('3',24,2,'end',1);
INSERT INTO "topology_seg_conn" VALUES('3',24,3,'end',0);
INSERT INTO "topology_seg_conn" VALUES('3',24,4,'end',0);
INSERT INTO "topology_seg_conn" VALUES('4',1,0,'end',1);
INSERT INTO "topology_seg_conn" VALUES('4',1,1,'start',0);
INSERT INTO "topology_seg_conn" VALUES('4',1,1,'end',2);
INSERT INTO "topology_seg_conn" VALUES('4',1,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('4',2,0,'end',1);
INSERT INTO "topology_seg_conn" VALUES('4',2,1,'start',0);
INSERT INTO "topology_seg_conn" VALUES('4',2,1,'end',2);
INSERT INTO "topology_seg_conn" VALUES('4',2,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('4',3,0,'end',1);
INSERT INTO "topology_seg_conn" VALUES('4',3,1,'start',0);
INSERT INTO "topology_seg_conn" VALUES('4',3,1,'end',2);
INSERT INTO "topology_seg_conn" VALUES('4',3,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('4',4,0,'end',1);
INSERT INTO "topology_seg_conn" VALUES('4',4,1,'start',0);
INSERT INTO "topology_seg_conn" VALUES('4',4,1,'end',2);
INSERT INTO "topology_seg_conn" VALUES('4',4,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('5',0,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('5',0,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('5',0,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',0,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',1,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('5',1,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('5',1,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',1,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',2,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('5',2,0,'start',2);
INSERT INTO "topology_seg_conn" VALUES('5',2,0,'end',3);
INSERT INTO "topology_seg_conn" VALUES('5',2,0,'end',4);
INSERT INTO "topology_seg_conn" VALUES('5',2,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',2,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',2,3,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',2,4,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',3,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('5',3,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('5',3,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',3,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',4,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('5',4,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('5',4,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',4,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',5,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('5',5,0,'start',3);
INSERT INTO "topology_seg_conn" VALUES('5',5,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('5',5,0,'end',4);
INSERT INTO "topology_seg_conn" VALUES('5',5,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',5,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',5,3,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',5,4,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',6,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('5',6,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('5',6,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('5',7,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('5',7,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('5',7,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('5',8,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('5',8,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',8,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('5',9,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('5',9,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',9,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('5',10,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',10,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',11,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',11,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',12,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',12,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',13,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',13,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',14,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('5',14,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',14,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('5',15,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('5',15,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',15,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('5',16,0,'start',2);
INSERT INTO "topology_seg_conn" VALUES('5',16,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('5',16,2,'start',0);
INSERT INTO "topology_seg_conn" VALUES('5',16,2,'end',1);
INSERT INTO "topology_seg_conn" VALUES('5',17,0,'start',2);
INSERT INTO "topology_seg_conn" VALUES('5',17,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('5',17,2,'start',0);
INSERT INTO "topology_seg_conn" VALUES('5',17,2,'end',1);
INSERT INTO "topology_seg_conn" VALUES('5',18,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('5',18,0,'start',3);
INSERT INTO "topology_seg_conn" VALUES('5',18,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('5',18,0,'end',4);
INSERT INTO "topology_seg_conn" VALUES('5',18,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',18,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',18,3,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',18,4,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',19,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('5',19,0,'start',3);
INSERT INTO "topology_seg_conn" VALUES('5',19,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('5',19,0,'end',4);
INSERT INTO "topology_seg_conn" VALUES('5',19,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',19,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',19,3,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',19,4,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',20,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('5',20,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('5',20,1,'start',5);
INSERT INTO "topology_seg_conn" VALUES('5',20,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',20,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',20,3,'start',4);
INSERT INTO "topology_seg_conn" VALUES('5',20,4,'start',5);
INSERT INTO "topology_seg_conn" VALUES('5',20,4,'end',3);
INSERT INTO "topology_seg_conn" VALUES('5',20,5,'start',1);
INSERT INTO "topology_seg_conn" VALUES('5',20,5,'end',4);
INSERT INTO "topology_seg_conn" VALUES('5',21,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('5',21,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('5',21,1,'start',5);
INSERT INTO "topology_seg_conn" VALUES('5',21,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',21,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',21,3,'start',4);
INSERT INTO "topology_seg_conn" VALUES('5',21,4,'start',3);
INSERT INTO "topology_seg_conn" VALUES('5',21,4,'end',5);
INSERT INTO "topology_seg_conn" VALUES('5',21,5,'start',1);
INSERT INTO "topology_seg_conn" VALUES('5',21,5,'end',4);
INSERT INTO "topology_seg_conn" VALUES('5',22,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('5',22,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('5',22,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',22,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',23,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('5',23,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('5',23,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',23,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',24,0,'start',3);
INSERT INTO "topology_seg_conn" VALUES('5',24,0,'end',4);
INSERT INTO "topology_seg_conn" VALUES('5',24,2,'start',0);
INSERT INTO "topology_seg_conn" VALUES('5',24,2,'end',1);
INSERT INTO "topology_seg_conn" VALUES('5',24,3,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',24,4,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',25,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('5',25,0,'start',3);
INSERT INTO "topology_seg_conn" VALUES('5',25,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('5',25,0,'end',4);
INSERT INTO "topology_seg_conn" VALUES('5',25,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',25,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',25,3,'end',0);
INSERT INTO "topology_seg_conn" VALUES('5',25,4,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',0,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('6',0,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('6',0,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',0,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',1,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('6',1,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('6',1,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',1,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',2,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('6',2,0,'start',2);
INSERT INTO "topology_seg_conn" VALUES('6',2,0,'end',3);
INSERT INTO "topology_seg_conn" VALUES('6',2,0,'end',4);
INSERT INTO "topology_seg_conn" VALUES('6',2,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',2,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',2,3,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',2,4,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',3,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('6',3,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('6',3,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',3,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',4,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('6',4,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('6',4,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',4,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',5,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('6',5,0,'start',3);
INSERT INTO "topology_seg_conn" VALUES('6',5,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('6',5,0,'end',4);
INSERT INTO "topology_seg_conn" VALUES('6',5,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',5,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',5,3,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',5,4,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',6,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('6',6,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('6',6,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('6',7,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('6',7,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('6',7,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('6',8,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('6',8,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',8,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('6',9,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('6',9,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',9,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('6',10,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',10,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',11,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',11,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',12,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',12,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',13,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',13,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',14,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('6',14,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',14,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('6',15,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('6',15,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',15,2,'start',1);
INSERT INTO "topology_seg_conn" VALUES('6',16,0,'start',2);
INSERT INTO "topology_seg_conn" VALUES('6',16,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('6',16,2,'start',0);
INSERT INTO "topology_seg_conn" VALUES('6',16,2,'end',1);
INSERT INTO "topology_seg_conn" VALUES('6',17,0,'start',2);
INSERT INTO "topology_seg_conn" VALUES('6',17,1,'start',2);
INSERT INTO "topology_seg_conn" VALUES('6',17,2,'start',0);
INSERT INTO "topology_seg_conn" VALUES('6',17,2,'end',1);
INSERT INTO "topology_seg_conn" VALUES('6',18,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('6',18,0,'start',3);
INSERT INTO "topology_seg_conn" VALUES('6',18,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('6',18,0,'end',4);
INSERT INTO "topology_seg_conn" VALUES('6',18,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',18,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',18,3,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',18,4,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',19,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('6',19,0,'start',3);
INSERT INTO "topology_seg_conn" VALUES('6',19,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('6',19,0,'end',4);
INSERT INTO "topology_seg_conn" VALUES('6',19,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',19,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',19,3,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',19,4,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',20,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('6',20,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('6',20,1,'start',5);
INSERT INTO "topology_seg_conn" VALUES('6',20,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',20,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',20,3,'start',4);
INSERT INTO "topology_seg_conn" VALUES('6',20,4,'start',5);
INSERT INTO "topology_seg_conn" VALUES('6',20,4,'end',3);
INSERT INTO "topology_seg_conn" VALUES('6',20,5,'start',1);
INSERT INTO "topology_seg_conn" VALUES('6',20,5,'end',4);
INSERT INTO "topology_seg_conn" VALUES('6',21,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('6',21,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('6',21,1,'start',5);
INSERT INTO "topology_seg_conn" VALUES('6',21,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',21,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',21,3,'start',4);
INSERT INTO "topology_seg_conn" VALUES('6',21,4,'start',3);
INSERT INTO "topology_seg_conn" VALUES('6',21,4,'end',5);
INSERT INTO "topology_seg_conn" VALUES('6',21,5,'start',1);
INSERT INTO "topology_seg_conn" VALUES('6',21,5,'end',4);
INSERT INTO "topology_seg_conn" VALUES('6',22,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('6',22,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('6',22,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',22,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',23,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('6',23,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('6',23,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',23,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',24,0,'start',3);
INSERT INTO "topology_seg_conn" VALUES('6',24,0,'end',4);
INSERT INTO "topology_seg_conn" VALUES('6',24,2,'start',0);
INSERT INTO "topology_seg_conn" VALUES('6',24,2,'end',1);
INSERT INTO "topology_seg_conn" VALUES('6',24,3,'end',0);
INSERT INTO "topology_seg_conn" VALUES('6',24,4,'end',0);
INSERT INTO "topology_seg_conn" VALUES('7',25,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('7',25,0,'start',3);
INSERT INTO "topology_seg_conn" VALUES('7',25,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('7',25,0,'end',4);
INSERT INTO "topology_seg_conn" VALUES('7',25,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('7',25,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('7',25,3,'end',0);
INSERT INTO "topology_seg_conn" VALUES('7',25,4,'end',0);
INSERT INTO "topology_seg_conn" VALUES('8',25,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('8',25,0,'start',3);
INSERT INTO "topology_seg_conn" VALUES('8',25,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('8',25,0,'end',4);
INSERT INTO "topology_seg_conn" VALUES('8',25,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('8',25,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('8',25,3,'end',0);
INSERT INTO "topology_seg_conn" VALUES('8',25,4,'end',0);
INSERT INTO "topology_seg_conn" VALUES('9',25,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('9',25,0,'start',3);
INSERT INTO "topology_seg_conn" VALUES('9',25,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('9',25,0,'end',4);
INSERT INTO "topology_seg_conn" VALUES('9',25,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('9',25,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('9',25,3,'end',0);
INSERT INTO "topology_seg_conn" VALUES('9',25,4,'end',0);
INSERT INTO "topology_seg_conn" VALUES('10',25,0,'start',1);
INSERT INTO "topology_seg_conn" VALUES('10',25,0,'start',3);
INSERT INTO "topology_seg_conn" VALUES('10',25,0,'end',2);
INSERT INTO "topology_seg_conn" VALUES('10',25,0,'end',4);
INSERT INTO "topology_seg_conn" VALUES('10',25,1,'end',0);
INSERT INTO "topology_seg_conn" VALUES('10',25,2,'end',0);
INSERT INTO "topology_seg_conn" VALUES('10',25,3,'end',0);
INSERT INTO "topology_seg_conn" VALUES('10',25,4,'end',0);
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
INSERT INTO "topology_segment" VALUES('1',0,0,130,95,130,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',0,1,155,95,130,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',0,2,155,145,130,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',1,0,155,95,155,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',1,1,130,95,155,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',1,2,130,145,155,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',2,0,142,95,142,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',2,1,130,95,142,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',2,2,155,95,142,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',2,3,130,145,142,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',2,4,155,145,142,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',3,0,125,100,160,100,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',3,1,125,140,125,100,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',3,2,160,140,160,100,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',4,0,125,140,160,140,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',4,1,125,100,125,140,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',4,2,160,100,160,140,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',5,0,125,120,160,120,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',5,1,125,100,125,120,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',5,2,160,100,160,120,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',5,3,125,140,125,120,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',5,4,160,140,160,120,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',6,0,125,100,155,100,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',6,1,125,180,125,100,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',6,2,125,180,155,180,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',7,0,125,140,155,140,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',7,1,125,60,125,140,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',7,2,125,60,155,60,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',8,0,125,180,155,180,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',8,1,125,60,125,180,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',8,2,125,60,155,60,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',9,0,125,60,155,60,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',9,1,125,180,125,60,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',9,2,125,180,155,180,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',10,0,125,180,160,180,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',10,1,125,100,125,180,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',10,2,160,100,160,180,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',11,0,125,60,160,60,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',11,1,125,140,125,60,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',11,2,160,140,160,60,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',12,0,210,95,210,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',12,1,130,95,210,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',12,2,130,145,210,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',13,0,75,95,75,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',13,1,155,95,75,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',13,2,155,145,75,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',14,0,210,95,210,140,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',14,1,75,95,210,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',14,2,75,95,75,140,5,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',15,0,75,95,75,140,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',15,1,210,95,75,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',15,2,210,95,210,140,5,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',16,0,75,60,160,60,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',16,1,75,180,160,180,4,0,-1,1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',16,2,75,60,75,180,5,0,-1,2,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',17,0,75,60,160,60,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',17,1,75,180,160,180,4,0,-1,1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',17,2,75,60,75,180,5,0,-1,2,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',18,0,125,0,160,0,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',18,1,125,20,125,0,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',18,2,160,20,160,0,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',18,3,125,140,125,0,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',18,4,160,140,160,0,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',19,0,125,240,160,240,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',19,1,125,100,125,240,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',19,2,160,100,160,240,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',19,3,125,220,125,240,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',19,4,160,220,160,240,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',20,0,125,0,160,0,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',20,1,125,60,125,0,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',20,2,160,20,160,0,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',20,3,75,180,155,180,4,0,-1,1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',20,4,75,60,75,180,5,0,-1,2,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',20,5,125,60,75,60,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',21,0,125,240,160,240,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',21,1,125,180,125,240,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',21,2,160,220,160,240,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',21,3,75,60,155,60,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',21,4,75,60,75,180,5,0,-1,2,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',21,5,125,180,75,180,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',22,0,-4,95,-4,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',22,1,155,95,-4,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',22,2,155,145,-4,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',23,0,289,95,289,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',23,1,130,95,289,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',23,2,130,145,289,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',24,0,75,60,210,60,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',24,1,75,180,210,180,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',24,2,142,60,142,180,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',24,3,75,140,75,60,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',24,4,210,140,210,60,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',25,0,75,115,210,115,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',25,1,75,100,75,115,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',25,2,210,100,210,115,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',25,3,75,140,75,115,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('1',25,4,210,140,210,115,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',0,0,265,60,620,60,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',1,0,260,20,260,0,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',1,1,260,0,625,0,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',1,2,625,0,625,20,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',2,0,260,100,260,120,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',2,1,260,120,625,120,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',2,2,625,120,625,100,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',3,0,265,25,442,25,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',3,1,442,25,442,95,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',3,2,442,95,620,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',4,0,265,95,442,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',4,1,442,95,442,25,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',4,2,442,25,620,25,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',5,0,232,115,650,115,4,0,4,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',5,1,232,100,232,115,5,0,5,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('2',5,2,650,100,650,115,5,0,5,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',0,0,130,95,130,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',0,1,155,95,130,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',0,2,155,145,130,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',1,0,155,95,155,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',1,1,130,95,155,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',1,2,130,145,155,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',2,0,142,95,142,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',2,1,130,95,142,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',2,2,155,95,142,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',2,3,130,145,142,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',2,4,155,145,142,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',3,0,125,100,160,100,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',3,1,125,140,125,100,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',3,2,160,140,160,100,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',4,0,125,140,160,140,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',4,1,125,100,125,140,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',4,2,160,100,160,140,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',5,0,125,120,160,120,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',5,1,125,100,125,120,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',5,2,160,100,160,120,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',5,3,125,140,125,120,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',5,4,160,140,160,120,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',6,0,125,100,155,100,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',6,1,125,180,125,100,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',6,2,125,180,155,180,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',7,0,125,140,155,140,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',7,1,125,60,125,140,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',7,2,125,60,155,60,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',8,0,125,180,155,180,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',8,1,125,60,125,180,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',8,2,125,60,155,60,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',9,0,125,60,155,60,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',9,1,125,180,125,60,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',9,2,125,180,155,180,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',10,0,125,180,160,180,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',10,1,125,100,125,180,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',10,2,160,100,160,180,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',11,0,125,60,160,60,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',11,1,125,140,125,60,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',11,2,160,140,160,60,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',12,0,210,95,210,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',12,1,130,95,210,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',12,2,130,145,210,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',13,0,75,95,75,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',13,1,155,95,75,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',13,2,155,145,75,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',14,0,210,95,210,140,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',14,1,75,95,210,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',14,2,75,95,75,140,5,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',15,0,75,95,75,140,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',15,1,210,95,75,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',15,2,210,95,210,140,5,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',16,0,75,60,160,60,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',16,1,75,180,160,180,4,0,-1,1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',16,2,75,60,75,180,5,0,-1,2,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',17,0,75,60,160,60,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',17,1,75,180,160,180,4,0,-1,1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',17,2,75,60,75,180,5,0,-1,2,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',18,0,125,0,160,0,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',18,1,125,20,125,0,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',18,2,160,20,160,0,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',18,3,125,140,125,0,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',18,4,160,140,160,0,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',19,0,125,240,160,240,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',19,1,125,100,125,240,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',19,2,160,100,160,240,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',19,3,125,220,125,240,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',19,4,160,220,160,240,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',20,0,125,0,160,0,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',20,1,125,60,125,0,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',20,2,160,20,160,0,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',20,3,75,180,155,180,4,0,-1,1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',20,4,75,60,75,180,5,0,-1,2,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',20,5,125,60,75,60,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',21,0,125,240,160,240,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',21,1,125,180,125,240,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',21,2,160,220,160,240,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',21,3,75,60,155,60,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',21,4,75,60,75,180,5,0,-1,2,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',21,5,125,180,75,180,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',22,0,-4,95,-4,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',22,1,155,95,-4,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',22,2,155,145,-4,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',23,0,289,95,289,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',23,1,130,95,289,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',23,2,130,145,289,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',24,0,75,60,210,60,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',24,1,75,180,210,180,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',24,2,142,60,142,180,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',24,3,75,140,75,60,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('3',24,4,210,140,210,60,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('4',0,0,265,460,620,460,4,0,4,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('4',1,0,260,420,260,400,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('4',1,1,260,400,625,400,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('4',1,2,625,400,625,420,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('4',2,0,260,500,260,520,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('4',2,1,260,520,625,520,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('4',2,2,625,520,625,500,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('4',3,0,265,425,442,425,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('4',3,1,442,425,442,495,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('4',3,2,442,495,620,495,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('4',4,0,265,495,442,495,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('4',4,1,442,495,442,425,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('4',4,2,442,425,620,425,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',0,0,130,95,130,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',0,1,155,95,130,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',0,2,155,145,130,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',1,0,155,95,155,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',1,1,130,95,155,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',1,2,130,145,155,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',2,0,142,95,142,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',2,1,130,95,142,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',2,2,155,95,142,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',2,3,130,145,142,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',2,4,155,145,142,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',3,0,125,100,160,100,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',3,1,125,140,125,100,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',3,2,160,140,160,100,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',4,0,125,140,160,140,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',4,1,125,100,125,140,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',4,2,160,100,160,140,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',5,0,125,120,160,120,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',5,1,125,100,125,120,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',5,2,160,100,160,120,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',5,3,125,140,125,120,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',5,4,160,140,160,120,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',6,0,125,100,155,100,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',6,1,125,180,125,100,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',6,2,125,180,155,180,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',7,0,125,140,155,140,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',7,1,125,60,125,140,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',7,2,125,60,155,60,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',8,0,125,180,155,180,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',8,1,125,60,125,180,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',8,2,125,60,155,60,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',9,0,125,60,155,60,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',9,1,125,180,125,60,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',9,2,125,180,155,180,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',10,0,125,180,160,180,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',10,1,125,100,125,180,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',10,2,160,100,160,180,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',11,0,125,60,160,60,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',11,1,125,140,125,60,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',11,2,160,140,160,60,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',12,0,210,95,210,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',12,1,130,95,210,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',12,2,130,145,210,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',13,0,75,95,75,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',13,1,155,95,75,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',13,2,155,145,75,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',14,0,210,95,210,140,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',14,1,75,95,210,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',14,2,75,95,75,140,5,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',15,0,75,95,75,140,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',15,1,210,95,75,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',15,2,210,95,210,140,5,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',16,0,75,60,160,60,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',16,1,75,180,160,180,4,0,-1,1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',16,2,75,60,75,180,5,0,-1,2,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',17,0,75,60,160,60,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',17,1,75,180,160,180,4,0,-1,1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',17,2,75,60,75,180,5,0,-1,2,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',18,0,125,0,160,0,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',18,1,125,20,125,0,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',18,2,160,20,160,0,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',18,3,125,140,125,0,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',18,4,160,140,160,0,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',19,0,125,240,160,240,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',19,1,125,100,125,240,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',19,2,160,100,160,240,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',19,3,125,220,125,240,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',19,4,160,220,160,240,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',20,0,125,0,160,0,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',20,1,125,60,125,0,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',20,2,160,20,160,0,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',20,3,75,180,155,180,4,0,-1,1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',20,4,75,60,75,180,5,0,-1,2,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',20,5,125,60,75,60,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',21,0,125,240,160,240,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',21,1,125,180,125,240,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',21,2,160,220,160,240,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',21,3,75,60,155,60,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',21,4,75,60,75,180,5,0,-1,2,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',21,5,125,180,75,180,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',22,0,-4,95,-4,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',22,1,155,95,-4,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',22,2,155,145,-4,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',23,0,289,95,289,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',23,1,130,95,289,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',23,2,130,145,289,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',24,0,75,60,210,60,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',24,1,75,180,210,180,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',24,2,142,60,142,180,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',24,3,75,140,75,60,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',24,4,210,140,210,60,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',25,0,75,115,210,115,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',25,1,75,100,75,115,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',25,2,210,100,210,115,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',25,3,75,140,75,115,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('5',25,4,210,140,210,115,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',0,0,130,95,130,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',0,1,155,95,130,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',0,2,155,145,130,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',1,0,155,95,155,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',1,1,130,95,155,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',1,2,130,145,155,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',2,0,142,95,142,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',2,1,130,95,142,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',2,2,155,95,142,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',2,3,130,145,142,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',2,4,155,145,142,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',3,0,125,100,160,100,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',3,1,125,140,125,100,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',3,2,160,140,160,100,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',4,0,125,140,160,140,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',4,1,125,100,125,140,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',4,2,160,100,160,140,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',5,0,125,120,160,120,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',5,1,125,100,125,120,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',5,2,160,100,160,120,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',5,3,125,140,125,120,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',5,4,160,140,160,120,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',6,0,125,100,155,100,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',6,1,125,180,125,100,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',6,2,125,180,155,180,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',7,0,125,140,155,140,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',7,1,125,60,125,140,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',7,2,125,60,155,60,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',8,0,125,180,155,180,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',8,1,125,60,125,180,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',8,2,125,60,155,60,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',9,0,125,60,155,60,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',9,1,125,180,125,60,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',9,2,125,180,155,180,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',10,0,125,180,160,180,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',10,1,125,100,125,180,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',10,2,160,100,160,180,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',11,0,125,60,160,60,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',11,1,125,140,125,60,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',11,2,160,140,160,60,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',12,0,210,95,210,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',12,1,130,95,210,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',12,2,130,145,210,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',13,0,75,95,75,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',13,1,155,95,75,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',13,2,155,145,75,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',14,0,210,95,210,140,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',14,1,75,95,210,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',14,2,75,95,75,140,5,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',15,0,75,95,75,140,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',15,1,210,95,75,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',15,2,210,95,210,140,5,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',16,0,75,60,160,60,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',16,1,75,180,160,180,4,0,-1,1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',16,2,75,60,75,180,5,0,-1,2,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',17,0,75,60,160,60,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',17,1,75,180,160,180,4,0,-1,1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',17,2,75,60,75,180,5,0,-1,2,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',18,0,125,0,160,0,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',18,1,125,20,125,0,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',18,2,160,20,160,0,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',18,3,125,140,125,0,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',18,4,160,140,160,0,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',19,0,125,240,160,240,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',19,1,125,100,125,240,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',19,2,160,100,160,240,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',19,3,125,220,125,240,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',19,4,160,220,160,240,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',20,0,125,0,160,0,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',20,1,125,60,125,0,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',20,2,160,20,160,0,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',20,3,75,180,155,180,4,0,-1,1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',20,4,75,60,75,180,5,0,-1,2,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',20,5,125,60,75,60,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',21,0,125,240,160,240,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',21,1,125,180,125,240,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',21,2,160,220,160,240,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',21,3,75,60,155,60,4,0,-1,0,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',21,4,75,60,75,180,5,0,-1,2,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',21,5,125,180,75,180,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',22,0,-4,95,-4,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',22,1,155,95,-4,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',22,2,155,145,-4,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',23,0,289,95,289,145,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',23,1,130,95,289,95,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',23,2,130,145,289,145,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',24,0,75,60,210,60,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',24,1,75,180,210,180,4,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',24,2,142,60,142,180,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',24,3,75,140,75,60,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('6',24,4,210,140,210,60,5,0,-1,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('7',25,0,75,115,210,115,4,0,4,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('7',25,1,75,100,75,115,5,0,5,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('7',25,2,210,100,210,115,5,0,5,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('7',25,3,75,140,75,115,5,0,5,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('7',25,4,210,140,210,115,5,0,5,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('8',25,0,75,515,210,515,4,0,4,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('8',25,1,75,500,75,515,5,0,5,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('8',25,2,210,500,210,515,5,0,5,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('8',25,3,75,540,75,515,5,0,5,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('8',25,4,210,540,210,515,5,0,5,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('9',25,0,675,115,810,115,4,0,4,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('9',25,1,675,100,675,115,5,0,5,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('9',25,2,810,100,810,115,5,0,5,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('9',25,3,675,140,675,115,5,0,5,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('9',25,4,810,140,810,115,5,0,5,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('10',25,0,675,515,810,515,4,0,4,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('10',25,1,675,500,675,515,5,0,5,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('10',25,2,810,500,810,515,5,0,5,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('10',25,3,675,540,675,515,5,0,5,-1,-2147483648,2147483647);
INSERT INTO "topology_segment" VALUES('10',25,4,810,540,810,515,5,0,5,-1,-2147483648,2147483647);
COMMIT;
