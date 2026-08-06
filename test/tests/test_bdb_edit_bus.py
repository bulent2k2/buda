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
# See the License for the specific language governing permissions and
# limitations under the License.

"""tools/bdb_edit_bus.py — resize / delete a bus's bit set in a BDB netlist."""
import os
import sqlite3
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import bdb_edit_bus as beb


def _make_bdb(path, base, n_bits, sep="_b", width=2):
    """A minimal BDB with just the net-referencing tables and one bus of n_bits,
    each bit carrying two pins (a driver + a receiver) plus a per-bit interface
    pin named after the net."""
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE net(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
        CREATE TABLE pin(net_id INTEGER, comp_id INTEGER, pin_name TEXT,
                         dir TEXT, px REAL, py REAL,
                         PRIMARY KEY(net_id, comp_id, pin_name));
        CREATE TABLE net_props(net_id INTEGER PRIMARY KEY, hpwl REAL, fanout INT,
                               driver_comp TEXT, bus_name TEXT, bit_index INT,
                               bundle_id INT);
        CREATE TABLE bundle_net(bundle_id TEXT, net_id INTEGER, ord INTEGER,
                                PRIMARY KEY(bundle_id, net_id));
        CREATE TABLE net_segment(bundle_id TEXT, seg_idx INT, bit_index INT,
                                 net_id INT);
    """)
    for i in range(n_bits):
        name = f"{base}{sep}{str(i).zfill(width)}"
        con.execute("INSERT INTO net(name) VALUES(?)", (name,))
        nid = con.execute("SELECT id FROM net WHERE name=?", (name,)).fetchone()[0]
        con.execute("INSERT INTO pin VALUES(?,?,?,?,?,?)",
                    (nid, 1, "drv", "OUTPUT", 10.0, 10.0))
        con.execute("INSERT INTO pin VALUES(?,?,?,?,?,?)",
                    (nid, 2, name, "INPUT", 90.0, 90.0))   # per-bit interface pin
        con.execute("INSERT INTO net_props VALUES(?,?,?,?,?,?,?)",
                    (nid, 0.0, 1, "c1", base, i, 7))
        con.execute("INSERT INTO bundle_net VALUES(?,?,?)", ("7", nid, i))
    con.commit()
    con.close()


def _names(path, like):
    con = sqlite3.connect(path)
    r = [x[0] for x in con.execute(
        "SELECT name FROM net WHERE name LIKE ? ORDER BY id", (like,))]
    con.close()
    return r


def _count(path, table):
    con = sqlite3.connect(path)
    n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    con.close()
    return n


def test_grow_zero_padded_bus(tmp_path):
    db = str(tmp_path / "g.bdb")
    _make_bdb(db, "bus_011", 2)
    assert beb.main([db, "--bus", "bus_011", "--set-bits", "16"]) == 0
    names = _names(db, "bus_011%")
    assert names == [f"bus_011_b{i:02d}" for i in range(16)]
    # New bits carry the cloned pins; the interface pin is renamed per bit.
    con = sqlite3.connect(db)
    pins = {r[0] for r in con.execute(
        "SELECT pin_name FROM pin p JOIN net n ON p.net_id=n.id "
        "WHERE n.name='bus_011_b15'")}
    con.close()
    assert pins == {"drv", "bus_011_b15"}          # leaf port kept, interface renamed
    # And the new bits joined the same bundle.
    assert _count(db, "bundle_net") == 16


def test_prune_keeps_low_bits(tmp_path):
    db = str(tmp_path / "p.bdb")
    _make_bdb(db, "bus_011", 64)
    assert beb.main([db, "--bus", "bus_011", "--set-bits", "4"]) == 0
    assert _names(db, "bus_011%") == [f"bus_011_b{i:02d}" for i in range(4)]
    assert _count(db, "pin") == 4 * 2                # dependent rows pruned too
    assert _count(db, "bundle_net") == 4
    # No orphaned rows referencing deleted nets.
    con = sqlite3.connect(db)
    orphans = con.execute(
        "SELECT count(*) FROM pin p WHERE NOT EXISTS"
        "(SELECT 1 FROM net n WHERE n.id=p.net_id)").fetchone()[0]
    con.close()
    assert orphans == 0


def test_delete_removes_the_whole_bus(tmp_path):
    db = str(tmp_path / "d.bdb")
    _make_bdb(db, "s2p", 3, sep="_", width=1)
    assert beb.main([db, "--bus", "s2p", "--delete"]) == 0
    assert _names(db, "s2p%") == []
    assert _count(db, "pin") == 0
    assert _count(db, "net_props") == 0
    assert _count(db, "bundle_net") == 0


def test_dry_run_writes_nothing(tmp_path):
    db = str(tmp_path / "dr.bdb")
    _make_bdb(db, "bus_011", 2)
    before = _names(db, "bus_011%")
    assert beb.main([db, "--bus", "bus_011", "--set-bits", "8", "--dry-run"]) == 0
    assert _names(db, "bus_011%") == before          # unchanged


def test_set_bits_zero_is_delete(tmp_path):
    db = str(tmp_path / "z.bdb")
    _make_bdb(db, "bus_011", 4)
    assert beb.main([db, "--bus", "bus_011", "--set-bits", "0"]) == 0
    assert _names(db, "bus_011%") == []


def test_unpadded_bus_crosses_decimal_boundary(tmp_path):
    # An unpadded bus s2p_0..s2p_15 mixes 1- and 2-digit suffixes; it must stay
    # ONE bus (a prior width-in-frame bug split it and left bits 10-15 behind on
    # delete/prune, and could collide on grow).  Codex P1.
    db = str(tmp_path / "u.bdb")
    _make_bdb(db, "s2p", 16, sep="_", width=1)   # width=1 => unpadded (s2p_10, not s2p_010)
    con = sqlite3.connect(db)
    bits = beb.find_bus_bits(con, "s2p")
    con.close()
    assert [b.index for b in bits] == list(range(16))   # all 16 seen, not just 0-9
    # Delete removes ALL of them (including 10-15).
    assert beb.main([db, "--bus", "s2p", "--delete"]) == 0
    assert _names(db, "s2p%") == []


def test_grow_unpadded_across_boundary_names(tmp_path):
    # Growing an unpadded bus past bit 9 must produce natural-width names
    # (s2p_10, never s2p_010).
    db = str(tmp_path / "gu.bdb")
    _make_bdb(db, "s2p", 8, sep="_", width=1)
    assert beb.main([db, "--bus", "s2p", "--set-bits", "12"]) == 0
    assert _names(db, "s2p%") == [f"s2p_{i}" for i in range(12)]


def test_prefix_collision_not_matched(tmp_path):
    # bus_01 must not swallow bus_011's bits.
    db = str(tmp_path / "c.bdb")
    _make_bdb(db, "bus_011", 3)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO net(name) VALUES('bus_01_b00')")   # a DIFFERENT bus
    con.commit(); con.close()
    con = sqlite3.connect(db)
    bits = beb.find_bus_bits(con, "bus_011")
    con.close()
    assert sorted(b.name for b in bits) == \
        ["bus_011_b00", "bus_011_b01", "bus_011_b02"]


def test_missing_bus_errors(tmp_path):
    db = str(tmp_path / "m.bdb")
    _make_bdb(db, "bus_011", 2)
    with pytest.raises(SystemExit):
        beb.main([db, "--bus", "nope", "--set-bits", "4"])


def test_output_leaves_input_untouched(tmp_path):
    src = str(tmp_path / "src.bdb")
    out = str(tmp_path / "out.bdb")
    _make_bdb(src, "bus_011", 8)
    assert beb.main([src, "--bus", "bus_011", "--set-bits", "4", "-o", out]) == 0
    assert _names(src, "bus_011%") == [f"bus_011_b{i:02d}" for i in range(8)]  # input intact
    assert _names(out, "bus_011%") == [f"bus_011_b{i:02d}" for i in range(4)]  # output pruned


def test_output_converts_binary_to_sql_and_back(tmp_path):
    src = str(tmp_path / "src.bdb")
    sql = str(tmp_path / "out.bdb.sql")
    back = str(tmp_path / "back.bdb")
    _make_bdb(src, "bus_011", 4)
    # .bdb -> grow -> .bdb.sql (text)
    assert beb.main([src, "--bus", "bus_011", "--set-bits", "8", "-o", sql]) == 0
    assert os.path.exists(sql)
    with open(sql) as f:
        head = f.read(64)
    assert head.startswith("-- BUDA BDB text dump")
    # .bdb.sql -> prune -> .bdb (binary), pins survive the round-trip
    assert beb.main([sql, "--bus", "bus_011", "--set-bits", "2", "-o", back]) == 0
    assert _names(back, "bus_011%") == ["bus_011_b00", "bus_011_b01"]
    assert _count(back, "pin") == 2 * 2


def test_output_rejected_for_list(tmp_path):
    db = str(tmp_path / "l.bdb")
    _make_bdb(db, "bus_011", 2)
    with pytest.raises(SystemExit):
        beb.main([db, "--list", "-o", str(tmp_path / "x.bdb")])


def test_shorter_base_does_not_match_longer_bus(tmp_path):
    # `--bus data` must NOT capture `data_out` (base match is EXACT).  Codex P1.
    db = str(tmp_path / "s.bdb")
    _make_bdb(db, "data", 2, sep="_", width=1)
    con = sqlite3.connect(db)
    for i in range(3):
        con.execute("INSERT INTO net(name) VALUES(?)", (f"data_out_{i}",))
    con.commit(); con.close()
    con = sqlite3.connect(db)
    picked = sorted(b.name for b in beb.find_bus_bits(con, "data"))
    con.close()
    assert picked == ["data_0", "data_1"]                 # not data_out_*
    assert beb.main([db, "--bus", "data", "--delete"]) == 0
    assert _names(db, "data%") == ["data_out_0", "data_out_1", "data_out_2"]  # sibling intact


def test_noop_resize_does_not_clear_routing(tmp_path):
    # --set-bits N when already N bits is a no-op: it must NOT clear routing even
    # with --clear-routing, and must leave the file untouched.  Codex P2.
    db = str(tmp_path / "n.bdb")
    _make_bdb(db, "bus_011", 4)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO net_segment VALUES('7', 0, 0, 1)")   # some routing
    con.commit(); con.close()
    assert beb.main([db, "--bus", "bus_011", "--set-bits", "4",
                     "--clear-routing"]) == 0
    assert _count(db, "bundle_net") == 4          # routing preserved
    assert _count(db, "net_segment") == 1
    assert _names(db, "bus_011%") == [f"bus_011_b{i:02d}" for i in range(4)]


def test_wal_only_committed_rows_are_snapshotted(tmp_path):
    # If the input's committed rows live only in a -wal sidecar (the engine opens
    # every BDB with journal_mode=WAL), _load must snapshot them via SQLite's
    # backup API — a raw byte copy of the main file would miss them.  Codex P1.
    db = str(tmp_path / "w.bdb")
    hold = sqlite3.connect(db)
    hold.execute("PRAGMA journal_mode=WAL")
    hold.execute("PRAGMA wal_autocheckpoint=0")   # keep commits in the -wal file
    hold.executescript(
        "CREATE TABLE net(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);")
    for i in range(6):
        hold.execute("INSERT INTO net(name) VALUES(?)", (f"bus_011_b{i:02d}",))
    hold.commit()                                  # committed, but only in -wal
    try:
        con, work = beb._load(db)
        try:
            bits = [b.name for b in beb.find_bus_bits(con, "bus_011")]
        finally:
            con.close()
            os.unlink(work)
    finally:
        hold.close()
    assert bits == [f"bus_011_b{i:02d}" for i in range(6)]   # saw the WAL rows


def test_write_leaves_no_temp_siblings(tmp_path):
    db = str(tmp_path / "t.bdb")
    _make_bdb(db, "bus_011", 4)
    assert beb.main([db, "--bus", "bus_011", "--set-bits", "2"]) == 0
    leftovers = [p for p in os.listdir(tmp_path) if p.startswith(".bdb_edit_bus.")]
    assert leftovers == []


@pytest.mark.skipif(sys.platform == "win32",
                    reason="POSIX permission modes: Windows chmod models only "
                           "the read-only bit, so 0o644 cannot round-trip "
                           "(measured 0o666, windows-2022 doc-validation run 8)")
def test_write_preserves_target_mode(tmp_path):
    # An in-place edit must keep the target's permission mode: mkstemp() creates
    # the temp at 0600, so without an explicit chmod a 0644 BDB would silently
    # become 0600 after os.replace().
    db = str(tmp_path / "t.bdb")
    _make_bdb(db, "bus_011", 4)
    os.chmod(db, 0o644)
    assert beb.main([db, "--bus", "bus_011", "--set-bits", "2"]) == 0
    import stat as _stat
    assert _stat.S_IMODE(os.stat(db).st_mode) == 0o644


def test_write_removes_stale_wal_sidecars(tmp_path):
    # A leftover -wal/-shm from the PREVIOUS database would be replayed over our
    # freshly written (complete, rollback-mode) replacement on the next open and
    # corrupt it, so the write path drops them after the atomic replace.
    db = str(tmp_path / "t.bdb")
    _make_bdb(db, "bus_011", 4)
    for side in ("-wal", "-shm"):
        with open(db + side, "wb") as fh:
            fh.write(b"stale")
    assert beb.main([db, "--bus", "bus_011", "--set-bits", "2"]) == 0
    assert not os.path.exists(db + "-wal")
    assert not os.path.exists(db + "-shm")
    # The edit landed and the DB opens cleanly (no stale-WAL replay).
    con = sqlite3.connect(db)
    n = con.execute("SELECT COUNT(*) FROM net WHERE name LIKE 'bus_011%'").fetchone()[0]
    con.close()
    assert n == 2
