# Copyright 2026 Ben Bulent Basaran
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""BDB schema versioning (PRAGMA user_version + migration ladder) and provenance.

Covers: a fresh DB is stamped to the current version with provenance meta; the
version survives a *.bdb.sql round-trip (dump emits PRAGMA user_version); and a
pre-versioning v0 DB (missing the busterm.rects column) migrates cleanly on open.
"""

import sqlite3

import pytest

import buda
import bdb_serialize


def test_fresh_db_is_stamped_and_has_provenance(tmp_path):
    db = buda.BDB(str(tmp_path / "fresh.bdb"))
    db.add_cell("c", 10, 10)
    assert db.schema_version() == buda.BDB.SCHEMA_VERSION
    assert db.meta_get("schema_version") == str(buda.BDB.SCHEMA_VERSION)
    assert db.meta_get("bdb_tool") == "buda-bdb"


def test_meta_get_default(tmp_path):
    db = buda.BDB(str(tmp_path / "m.bdb"))
    assert db.meta_get("does_not_exist") == ""
    assert db.meta_get("does_not_exist", "fallback") == "fallback"


def test_roundtrip_preserves_user_version(tmp_path):
    p = str(tmp_path / "rt.bdb")
    db = buda.BDB(p)
    db.add_cell("c", 10, 10)
    del db  # release the connection before serializing

    sql = str(tmp_path / "rt.bdb.sql")
    bdb_serialize.dump(p, sql)
    assert f"PRAGMA user_version={buda.BDB.SCHEMA_VERSION};" in open(sql).read()

    rebuilt = str(tmp_path / "rebuilt.bdb")
    bdb_serialize.load(sql, rebuilt)
    # The raw file carries the version even before buda.BDB reopens/migrates it.
    raw = sqlite3.connect(rebuilt)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == buda.BDB.SCHEMA_VERSION
    raw.close()
    assert buda.BDB(rebuilt).schema_version() == buda.BDB.SCHEMA_VERSION


def test_v0_db_missing_rects_migrates_on_open(tmp_path):
    p = str(tmp_path / "old.bdb")
    # Pre-versioning DB: busterm WITHOUT the rects column, user_version left at 0.
    con = sqlite3.connect(p)
    con.executescript(
        """
        CREATE TABLE busterm (id TEXT PRIMARY KEY, comp_id INTEGER,
            hier_path TEXT NOT NULL, depth INTEGER,
            x1 REAL, y1 REAL, x2 REAL, y2 REAL,
            resolution TEXT DEFAULT 'BLOCK', parent_id TEXT);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO meta VALUES('units','1000');
        """
    )
    con.commit()
    assert con.execute("PRAGMA user_version").fetchone()[0] == 0
    assert "rects" not in {r[1] for r in con.execute("PRAGMA table_info(busterm)")}
    con.close()

    db = buda.BDB(p)  # opening migrates
    assert db.schema_version() == buda.BDB.SCHEMA_VERSION
    assert db.meta_get("bdb_tool") == "buda-bdb"
    assert db.units() == 1000  # pre-existing data preserved
    del db

    con = sqlite3.connect(p)
    assert "rects" in {r[1] for r in con.execute("PRAGMA table_info(busterm)")}
    con.close()


def test_pre_v30_db_gains_the_multirect_footprint_tables_on_open(tmp_path):
    # v29 to v30: the cell's MULTI-RECT footprint (cell_rect + cell.teg_mode).
    # A pre-v30 cell was ONE `width x height` box by construction, so there is
    # nothing to migrate — an empty cell_rect table and 'THRU' are exactly the
    # design that was stored, and the existing cell rows must survive.
    p = str(tmp_path / "v29.bdb")
    db = buda.BDB(p)
    db.add_cell("mac", 400, 400)
    del db

    # Rewind to the v29 shape: drop cell_rect and rebuild `cell` without
    # teg_mode, then stamp user_version back.
    con = sqlite3.connect(p)
    con.executescript(
        """
        DROP TABLE cell_rect;
        ALTER TABLE cell RENAME TO cell_old;
        CREATE TABLE cell (name TEXT PRIMARY KEY, width REAL NOT NULL,
            height REAL NOT NULL, cls TEXT NOT NULL DEFAULT '',
            bottom_up INTEGER NOT NULL DEFAULT 0,
            layer_cap INTEGER NOT NULL DEFAULT -1,
            layer_floor INTEGER NOT NULL DEFAULT -1);
        INSERT INTO cell SELECT name,width,height,cls,bottom_up,layer_cap,
            layer_floor FROM cell_old;
        DROP TABLE cell_old;
        PRAGMA user_version = 29;
        """
    )
    con.commit()
    assert "teg_mode" not in {r[1] for r in con.execute("PRAGMA table_info(cell)")}
    con.close()

    db = buda.BDB(p)                       # opening migrates
    assert db.schema_version() == buda.BDB.SCHEMA_VERSION
    assert db.multirect_cells() == []      # a pre-v30 design declared none
    assert db.cell_teg_mode("mac") == "THRU"
    assert {c.name for c in db.all_cells()} == {"mac"}   # data preserved
    # …and the migrated DB accepts a footprint like a fresh one.
    db.set_cell_rects("mac", [(0, 0, 400, 100), (0, 0, 100, 400)], "OVER")
    assert db.multirect_cells() == ["mac"]


def test_pre_v32_db_gains_the_pin_port_table_on_open(tmp_path):
    # v31 to v32: LEF pin PORT geometry (cell_pin_port).  A pre-v32 checkpoint
    # never held it, so an empty table is exactly the design that was stored.
    p = str(tmp_path / "v31.bdb")
    db = buda.BDB(p)
    db.add_cell("mac", 100, 100)
    del db
    con = sqlite3.connect(p)
    con.executescript("DROP TABLE cell_pin_port; PRAGMA user_version = 31;")
    con.commit()
    con.close()

    db = buda.BDB(p)                       # opening migrates
    assert db.schema_version() == buda.BDB.SCHEMA_VERSION
    assert db.cell_pin_ports("mac") == []  # a pre-v32 design stored none
    db.set_cell_pin_ports("mac", "a", [("M6", 0, 20, 2, 60), ("M6", 0, 70, 2, 90)])
    assert db.cell_pin_ports("mac") == [("a", "M6", 0, 20, 2, 60),
                                        ("a", "M6", 0, 70, 2, 90)]
    db.set_cell_pin_ports("mac", "a", [("M6", 0, 10, 2, 30)])     # REPLACES the pin's rows
    assert db.cell_pin_ports("mac") == [("a", "M6", 0, 10, 2, 30)]


def test_newer_schema_version_is_rejected(tmp_path):
    # A DB written by a newer BUDA (user_version > SCHEMA_VERSION) must fail fast
    # rather than be opened writable by an older binary that can't understand it.
    p = str(tmp_path / "future.bdb")
    con = sqlite3.connect(p)
    con.execute(f"PRAGMA user_version = {buda.BDB.SCHEMA_VERSION + 1}")
    con.commit()
    con.close()
    with pytest.raises(Exception, match="newer than this build"):
        buda.BDB(p)
