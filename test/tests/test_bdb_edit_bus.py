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
