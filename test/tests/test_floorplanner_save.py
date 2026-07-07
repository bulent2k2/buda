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

import pytest

# Moved to the mid tier: full-pipeline / BDB round-trip / interchange
# integration (keeps the fast tier < 10s). See
# docs/internal/test_runtime_analysis.md.
pytestmark = pytest.mark.mid

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


def test_two_sql_windows_lock_the_source_not_the_temp(tmp_path):
    # Two sessions opening the SAME *.bdb.sql must not both stay writable — the
    # write lock keys on the shared source, not each session's throwaway temp
    # binary — else both Save back to it and silently clobber (Codex #166 P1).
    fpc.release_bdb_lock(_design_and_dump(tmp_path))
    sql = str(tmp_path / "fix.bdb.sql")

    bin1 = bdb_serialize.materialize_if_sql(sql)
    s1 = fpc.load_bdb(bin1, sql_source=sql)
    bin2 = bdb_serialize.materialize_if_sql(sql)          # a DIFFERENT temp
    s2 = fpc.load_bdb(bin2, sql_source=sql)
    assert bin1 != bin2
    assert not s1.is_read_only                            # first writer
    assert s2.is_read_only                                # source already locked
    with pytest.raises(PermissionError):
        fpc.save_sql(s2)
    fpc.release_bdb_lock(s2)
    fpc.release_bdb_lock(s1)


def test_save_as_binary_refuses_locked_target(tmp_path):
    # Save As to an existing binary another session is editing must FAIL before
    # the destructive dump->load, not clobber the live DB (Codex #166 P1).
    holder = _design(tmp_path, "target.bdb")             # holds target's lock
    holder_names = {c.name for c in holder.bdb.all_components()}
    src = _design(tmp_path, "src.bdb")
    fpc.add_block(src, "u_extra", 700, 500, 100, 100)    # distinct content
    with pytest.raises(PermissionError, match="another floorplanner session"):
        fpc.save_bdb_as_binary(src, str(tmp_path / "target.bdb"))
    # Target untouched: still the holder's design, not src's.
    assert {c.name for c in holder.bdb.all_components()} == holder_names
    assert "u_extra" not in holder_names
    fpc.release_bdb_lock(src)
    fpc.release_bdb_lock(holder)


def test_save_as_binary_onto_self_is_in_place_write(tmp_path):
    # Save As onto the currently-open binary is a plain Write (no self-lock
    # conflict, same state returned).
    state = _design(tmp_path, "self.bdb")
    fpc.move_block(state, "u_cpu", 500, 300)
    same = fpc.save_bdb_as_binary(state, state.bdb_path)
    assert same is state
    comps = {c.name: (c.x1, c.y1) for c in state.bdb.all_components()}
    assert comps["u_cpu"] == (500, 300)
    fpc.release_bdb_lock(state)
