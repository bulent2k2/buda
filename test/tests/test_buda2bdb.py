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

import pytest

# Moved to the mid tier: full-pipeline / BDB round-trip / interchange
# integration (keeps the fast tier < 10s). See
# docs/internal/test_runtime_analysis.md.
pytestmark = pytest.mark.mid

import os
import sys

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import buda_db
from tools import buda2bdb
from tools import bdb2buda
from tools import bdb_serialize


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def _comps(bdb_path):
    db = buda_db.BDB(bdb_path)
    return {c.name: c for c in db.all_components()}


# ──────────────────────────────────────────────────────────────────────────
# Basic creation
# ──────────────────────────────────────────────────────────────────────────

def test_basic_create_with_set_die(tmp_path):
    script = _write(tmp_path, "cpu.buda",
                    "set_die 1000 800\n"
                    "add_block a 100 100 300 200\n"
                    "add_block b 400 400 600 500\n"
                    "add_net n0 a.o b.i\n")
    bdb = str(tmp_path / "out.bdb")
    stats = buda2bdb.convert(script, bdb, "cpu")
    assert stats["w"] == 1000 and stats["h"] == 800
    assert stats["n_blocks"] == 2 and stats["n_nets"] == 1

    db = buda_db.BDB(bdb)
    cells = {c.name: c for c in db.all_cells()}
    assert cells["cpu"].width == 1000 and cells["cpu"].height == 800

    comps = {c.name: c for c in db.all_components()}
    # Representative instance + two children.
    assert "cpu" in comps
    assert (comps["cpu/a"].x1, comps["cpu/a"].y1,
            comps["cpu/a"].x2, comps["cpu/a"].y2) == (100, 100, 300, 200)
    assert comps["cpu"].cell == "cpu"

    nets = {n.name for n in db.all_nets()}
    assert "n0" in nets
    # Pins live on the two child components (so bdb2buda can read them back).
    cpu_a = comps["cpu/a"].id
    cpu_b = comps["cpu/b"].id
    pin_comp_ids = {p.comp_id for p in db.all_pins()}
    assert cpu_a in pin_comp_ids and cpu_b in pin_comp_ids


def test_no_set_die_uses_bbox_and_shifts(tmp_path):
    # Blocks span x[-50..250], y[100..400] → bbox 300 x 300, shifted to origin.
    script = _write(tmp_path, "blk.buda",
                    "add_block p -50 100 50 200\n"
                    "add_block q 150 300 250 400\n"
                    "add_net e p.o q.i\n")
    bdb = str(tmp_path / "b.bdb")
    stats = buda2bdb.convert(script, bdb, "blk")
    assert stats["w"] == 300 and stats["h"] == 300

    comps = _comps(bdb)
    # p shifts by (-(-50), -100) = (+50, -100): (-50,100)->(0,0)
    assert (comps["blk/p"].x1, comps["blk/p"].y1) == (0, 0)
    assert (comps["blk/q"].x1, comps["blk/q"].y1) == (200, 200)


def test_add_bus_expands_to_nets(tmp_path):
    script = _write(tmp_path, "bus.buda",
                    "set_die 500 500\n"
                    "add_block s 0 0 50 50\n"
                    "add_block d 200 200 250 250\n"
                    "add_bus w[4] s.o d.i\n")
    bdb = str(tmp_path / "bus.bdb")
    stats = buda2bdb.convert(script, bdb, "bus")
    assert stats["n_nets"] == 4
    db = buda_db.BDB(bdb)
    names = {n.name for n in db.all_nets()}
    assert {"w_0", "w_1", "w_2", "w_3"} <= names


def test_bus_range_form(tmp_path):
    script = _write(tmp_path, "r.buda",
                    "set_die 500 500\n"
                    "add_block s 0 0 50 50\n"
                    "add_block d 200 200 250 250\n"
                    "add_bus a[2:4] s.o d.i\n")
    bdb = str(tmp_path / "r.bdb")
    buda2bdb.convert(script, bdb, "r")
    names = {n.name for n in buda_db.BDB(bdb).all_nets()}
    assert {"a_2", "a_3", "a_4"} <= names


# ──────────────────────────────────────────────────────────────────────────
# Replace an existing cell
# ──────────────────────────────────────────────────────────────────────────

def test_replace_cell_drops_old_blocks_and_nets(tmp_path):
    bdb = str(tmp_path / "rep.bdb")
    s1 = _write(tmp_path, "c.buda",
                "set_die 1000 1000\n"
                "add_block old1 0 0 100 100\n"
                "add_block old2 200 200 300 300\n"
                "add_net oldnet old1.o old2.i\n")
    buda2bdb.convert(s1, bdb, "c")

    s2 = _write(tmp_path, "c2.buda",
                "set_die 600 600\n"
                "add_block new1 0 0 100 100\n"
                "add_block new2 200 200 300 300\n"
                "add_net newnet new1.o new2.i\n")
    buda2bdb.convert(s2, bdb, "c")

    comps = _comps(bdb)
    assert "c/new1" in comps and "c/new2" in comps
    assert "c/old1" not in comps and "c/old2" not in comps

    nets = {n.name for n in buda_db.BDB(bdb).all_nets()}
    assert "newnet" in nets and "oldnet" not in nets

    cells = {c.name: c for c in buda_db.BDB(bdb).all_cells()}
    assert cells["c"].width == 600 and cells["c"].height == 600
    # Stale synthetic child cells removed.
    assert "c__old1" not in cells and "c__new1" in cells


def test_replace_does_not_corrupt_prefix_collision_cell(tmp_path):
    # Cell names with '_' must be matched literally: replacing "cpu_0" must not
    # touch "cpuA0" (which a SQL LIKE 'cpu_0/%' would wrongly match).
    bdb = str(tmp_path / "coll.bdb")
    a = _write(tmp_path, "cpu_0.buda",
               "set_die 400 400\nadd_block x 0 0 100 100\n")
    b = _write(tmp_path, "cpuA0.buda",
               "set_die 400 400\nadd_block y 0 0 100 100\nadd_net m y.o y.i\n")
    buda2bdb.convert(a, bdb, "cpu_0")
    buda2bdb.convert(b, bdb, "cpuA0")

    # Replace cpu_0 — cpuA0's subtree and net must survive intact.
    a2 = _write(tmp_path, "cpu_0b.buda",
                "set_die 200 200\nadd_block x 0 0 50 50\n")
    buda2bdb.convert(a2, bdb, "cpu_0")

    comps = _comps(bdb)
    assert "cpuA0" in comps and "cpuA0/y" in comps, "unrelated cell corrupted"
    nets = {n.name for n in buda_db.BDB(bdb).all_nets()}
    assert "m" in nets, "unrelated cell's net deleted"


# ──────────────────────────────────────────────────────────────────────────
# Sync existing cell instances to the new size
# ──────────────────────────────────────────────────────────────────────────

def test_other_instances_resized_to_new_cell_size(tmp_path):
    bdb = str(tmp_path / "sync.bdb")
    s1 = _write(tmp_path, "k.buda",
                "set_die 900 900\n"
                "add_block a 0 0 100 100\n"
                "add_block b 200 200 300 300\n")
    buda2bdb.convert(s1, bdb, "k")

    # Place a second instance of cell "k" elsewhere.
    db = buda_db.BDB(bdb)
    db.add_inst("u_other", "k", "", 2000.0, 2000.0)
    other = next(c for c in db.all_components() if c.name == "u_other")
    assert (other.x2 - other.x1, other.y2 - other.y1) == (900, 900)
    del db

    # Re-run with a smaller die; the other instance must resize.
    s2 = _write(tmp_path, "k2.buda",
                "set_die 500 500\n"
                "add_block a 0 0 100 100\n")
    buda2bdb.convert(s2, bdb, "k")

    comps = _comps(bdb)
    o = comps["u_other"]
    assert (o.x1, o.y1) == (2000, 2000), "origin preserved"
    assert (o.x2 - o.x1, o.y2 - o.y1) == (500, 500), "resized to new cell size"


# ──────────────────────────────────────────────────────────────────────────
# Round-trip via bdb2buda (the oracle)
# ──────────────────────────────────────────────────────────────────────────

def _sections(script_text):
    """Split a .buda script into (die_line, set(block_lines), set(conn_lines))."""
    die, blocks, conns = None, set(), set()
    for line in script_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("set_die"):
            die = line
        elif line.startswith("add_block"):
            blocks.add(line)
        elif line.startswith(("add_net", "add_bus")):
            conns.add(line)
    return die, blocks, conns


def test_roundtrip_via_bdb2buda(tmp_path):
    # Authored already in bdb2buda's canonical frame (origin 0, integer coords)
    # so the round-trip is exact.
    src = ("set_die 800 600\n"
           "add_block a 0 0 100 100\n"
           "add_block b 300 0 400 100\n"
           "add_block c 600 0 700 100\n"
           "add_net clk a.o b.i\n"
           "add_bus d[3] a.o c.i\n")
    script = _write(tmp_path, "chip.buda", src)
    bdb = str(tmp_path / "chip.bdb")
    buda2bdb.convert(script, bdb, "chip")

    out = bdb2buda.convert(bdb, cell_name="chip", scale=1.0)

    d_in, b_in, c_in = _sections(src)
    d_out, b_out, c_out = _sections(out)
    assert d_in == d_out, f"die: {d_in!r} != {d_out!r}"
    assert b_in == b_out, f"blocks: {b_in} != {b_out}"
    assert c_in == c_out, f"conns: {c_in} != {c_out}"


def test_bdb2buda_accepts_bdb_sql_input(tmp_path):
    # bdb2buda takes a diffable *.bdb.sql text fixture as transparently as a
    # binary *.bdb (materialized read-only) — identical script from either.
    src = ("set_die 800 600\n"
           "add_block a 0 0 100 100\n"
           "add_block b 300 0 400 100\n"
           "add_net clk a.o b.i\n")
    script = _write(tmp_path, "chip.buda", src)
    bdb = str(tmp_path / "chip.bdb")
    buda2bdb.convert(script, bdb, "chip")

    sql = str(tmp_path / "chip.bdb.sql")
    bdb_serialize.dump(bdb, sql)

    out_bin = bdb2buda.convert(bdb, cell_name="chip", scale=1.0)
    out_sql = bdb2buda.convert(sql, cell_name="chip", scale=1.0)
    assert out_sql.strip() and out_sql == out_bin
    # the source .sql is never mutated by conversion (read-only materialize)
    assert (tmp_path / "chip.bdb.sql").read_text().startswith("--")

    # A malformed .sql fails cleanly as a ValueError (caught by main()).
    bad = _write(tmp_path, "bad.bdb.sql", "this is not sql;\n")
    with pytest.raises(ValueError, match="could not read serialized BDB"):
        bdb2buda.convert(bad)


def _comps_from_sql(tmp_path, sql, stem):
    binp = str(tmp_path / f"{stem}.mat.bdb")
    bdb_serialize.load(sql, binp)
    return _comps(binp)


def test_buda2bdb_writes_bdb_sql_output(tmp_path):
    # A *.bdb.sql target is written as diffable text (via a temp binary),
    # reloading to the same design as a binary target.
    src = ("set_die 1000 800\n"
           "add_block a 100 100 300 200\n"
           "add_block b 400 400 600 500\n"
           "add_net n0 a.o b.i\n")
    script = _write(tmp_path, "cpu.buda", src)

    sql = str(tmp_path / "cpu.bdb.sql")
    stats = buda2bdb.convert(script, sql, "cpu")
    assert stats["n_blocks"] == 2 and stats["n_nets"] == 1
    text = (tmp_path / "cpu.bdb.sql").read_text()
    assert text.startswith("--") and "PRAGMA user_version=" in text

    binp = str(tmp_path / "cpu.bdb")
    buda2bdb.convert(script, binp, "cpu")

    def key(comps):
        return sorted((n, c.cell, c.x1, c.y1, c.x2, c.y2)
                      for n, c in comps.items())
    assert key(_comps_from_sql(tmp_path, sql, "cpu")) == key(_comps(binp))


def test_buda2bdb_replace_into_existing_sql_preserves_other_cells(tmp_path):
    # Adding a cell INTO an existing *.bdb.sql keeps the cells already there:
    # the .sql is materialized before editing, not overwritten from scratch.
    sql = str(tmp_path / "lib.bdb.sql")
    buda2bdb.convert(_write(tmp_path, "cpu.buda",
                            "set_die 400 400\nadd_block a 0 0 100 100\n"),
                     sql, "cpu")
    buda2bdb.convert(_write(tmp_path, "mem.buda",
                            "set_die 400 400\nadd_block m 0 0 100 100\n"),
                     sql, "mem")
    binp = str(tmp_path / "lib.mat.bdb")
    bdb_serialize.load(sql, binp)
    cells = {c.name for c in buda_db.BDB(binp).all_cells()}
    assert {"cpu", "mem"} <= cells


def test_buda2bdb_then_bdb2buda_sql_only_roundtrip(tmp_path):
    # End-to-end with .bdb.sql on both sides: script -> .bdb.sql (buda2bdb) ->
    # script (bdb2buda reading the .sql), stable across the round-trip.
    src = ("set_die 800 600\n"
           "add_block a 0 0 100 100\n"
           "add_block b 300 0 400 100\n"
           "add_net clk a.o b.i\n")
    script = _write(tmp_path, "chip.buda", src)
    sql = str(tmp_path / "chip.bdb.sql")
    buda2bdb.convert(script, sql, "chip")

    out = bdb2buda.convert(sql, cell_name="chip", scale=1.0)
    d_in, b_in, c_in = _sections(src)
    d_out, b_out, c_out = _sections(out)
    assert (d_in, b_in, c_in) == (d_out, b_out, c_out)


def test_roundtrip_flow_two(tmp_path):
    # Round-trip the real flow/two.buda: parse -> BDB -> back. Because two.buda
    # has no set_die (bbox-shifted), compare the SECOND pass to itself (stable).
    two = os.path.join(_ROOT, "flow", "two.buda")
    bdb = str(tmp_path / "two.bdb")
    buda2bdb.convert(two, bdb, "two")
    out1 = bdb2buda.convert(bdb, cell_name="two", scale=1.0)

    # Feed the emitted script back in; the result must be identical (fixpoint).
    script2 = _write(tmp_path, "two_rt.buda", out1)
    bdb2 = str(tmp_path / "two2.bdb")
    buda2bdb.convert(script2, bdb2, "two")
    out2 = bdb2buda.convert(bdb2, cell_name="two", scale=1.0)

    assert _sections(out1) == _sections(out2)
