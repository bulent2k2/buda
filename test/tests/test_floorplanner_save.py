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

"""Floorplanner Save → *.bdb.sql and Save As (floorplanner_commands).

The GUI's Save re-serializes the working binary to a diffable *.bdb.sql (the
same text form `bdb_serialize.dump` / the CLI's `open_bdb … writeback` write),
and Save As targets a chosen .sql or a fresh binary — see
docs/internal/wishlist-bdb.md.
"""

import os
import sys

import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools import floorplanner_commands as fpc
from tools import bdb_serialize


def _design(tmp_path, name="d.bdb"):
    state = fpc.create_bdb(str(tmp_path / name), 1000, 800, grid=10)
    fpc.add_block(state, "u_cpu", 100, 100, 200, 150)
    fpc.add_block(state, "u_mem", 400, 100, 200, 150)
    return state


def _blocks_from_sql(tmp_path, sql_path, stem):
    """Materialize a .sql to a binary and return {name: (x1,y1,x2,y2)}."""
    binp = str(tmp_path / f"{stem}.bin.bdb")
    bdb_serialize.load(sql_path, binp)
    st = fpc.load_bdb(binp)
    out = {c.name: (c.x1, c.y1, c.x2, c.y2) for c in st.bdb.all_components()}
    fpc.release_bdb_lock(st)
    return out


def test_save_as_sql_writes_diffable_text(tmp_path):
    state = _design(tmp_path)
    sql = str(tmp_path / "out.bdb.sql")
    target = fpc.save_sql(state, sql)
    assert target == os.path.abspath(sql)
    assert state.sql_source == os.path.abspath(sql)      # remembered for Save
    text = (tmp_path / "out.bdb.sql").read_text()
    assert "PRAGMA user_version=" in text                # version survives
    assert "u_cpu" in text and "u_mem" in text
    # Round-trips back to the same placement.
    assert _blocks_from_sql(tmp_path, sql, "rt") == {
        "u_cpu": (100, 100, 300, 250),
        "u_mem": (400, 100, 600, 250),
    }


def test_save_writes_back_to_opened_sql(tmp_path):
    # Build a .sql, reopen it the way the GUI does (materialize + sql_source),
    # move a block, Save (no arg) -> the .sql reflects the edit.
    fpc.release_bdb_lock(_design_and_dump(tmp_path))
    sql = str(tmp_path / "fix.bdb.sql")
    binp = bdb_serialize.materialize_if_sql(sql)
    state = fpc.load_bdb(binp, sql_source=sql)
    assert state.sql_source == os.path.abspath(sql)
    fpc.move_block(state, "u_cpu", 500, 300)
    saved = fpc.save_sql(state)                          # write-back, no path
    assert saved == os.path.abspath(sql)
    fpc.release_bdb_lock(state)
    assert _blocks_from_sql(tmp_path, sql, "rt2")["u_cpu"] == (500, 300, 700, 450)


def _design_and_dump(tmp_path):
    state = _design(tmp_path)
    fpc.save_sql(state, str(tmp_path / "fix.bdb.sql"))
    return state


def test_save_as_binary_switches_session(tmp_path):
    state = _design(tmp_path)
    fpc.release_bdb_lock(state)
    new_bin = str(tmp_path / "copy.bdb")
    state2 = fpc.save_bdb_as_binary(state, new_bin)
    assert state2.bdb_path == os.path.abspath(new_bin)
    assert state2.sql_source == ""                       # binary is not a .sql target
    assert not state2.is_read_only                       # re-locked on the copy
    names = {c.name for c in state2.bdb.all_components()}
    assert {"u_cpu", "u_mem"} <= names
    assert not os.path.exists(new_bin + ".saveas.tmp.sql")   # temp cleaned up
    fpc.release_bdb_lock(state2)


def test_save_sql_without_target_raises(tmp_path):
    state = _design(tmp_path)                             # binary-only, no source
    with pytest.raises(RuntimeError, match="No .sql target"):
        fpc.save_sql(state)


def test_save_sql_read_only_raises(tmp_path):
    holder = _design(tmp_path, "shared.bdb")             # holds the write lock
    ro = fpc.load_bdb(str(tmp_path / "shared.bdb"))      # second session: read-only
    assert ro.is_read_only
    with pytest.raises(PermissionError):
        fpc.save_sql(ro, str(tmp_path / "ro.bdb.sql"))
    fpc.release_bdb_lock(ro)
    fpc.release_bdb_lock(holder)
