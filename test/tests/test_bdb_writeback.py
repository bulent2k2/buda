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

"""`open_bdb <file>.sql writeback` — opt-in write-back of a serialized fixture.

By default `open_bdb *.sql` materializes a throwaway temp binary and discards
changes. With the `writeback` keyword, the working binary is dumped back to the
source `.sql` on `save_bdb`, on the next `open_bdb`, and at exit / end of run —
so a flow can deliberately update its committed fixture. A read-only flow (no
keyword) must never rewrite the fixture.
"""

import contextlib
import io

import pytest

import buda
import buda_cli
import bdb_serialize


def _make_sql(tmp_path, name="w"):
    """Create a serialized fixture (<name>.bdb.sql) containing one cell 'ORIG'."""
    binp = str(tmp_path / f"{name}.bdb")
    db = buda.BDB(binp)
    db.add_cell("ORIG", 10, 10)
    del db  # release the connection before serializing
    sql = str(tmp_path / f"{name}.bdb.sql")
    bdb_serialize.dump(binp, sql)
    return sql


def _session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    return s


def test_writeback_persists_on_save(tmp_path):
    sql = _make_sql(tmp_path)
    s = _session()
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command(f"open_bdb {sql} writeback")
        s.bdb.add_cell("ADDED", 20, 20)   # mutate the working temp binary
        s.do_command("save_bdb")
    txt = open(sql).read()
    assert "ADDED" in txt and "ORIG" in txt
    # A fresh open of the .sql sees the persisted cell.
    names = {c.name for c in buda.BDB(bdb_serialize.materialize_if_sql(sql)).all_cells()}
    assert {"ORIG", "ADDED"} <= names


def test_no_writeback_leaves_sql_untouched(tmp_path):
    sql = _make_sql(tmp_path)
    before = open(sql).read()
    s = _session()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command(f"open_bdb {sql}")   # no writeback keyword
        s.bdb.add_cell("ADDED", 20, 20)
        s.do_command("save_bdb")
    assert open(sql).read() == before          # fixture untouched
    assert "nothing to write" in buf.getvalue()


def test_exit_flushes_writeback(tmp_path):
    sql = _make_sql(tmp_path)
    s = _session()
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command(f"open_bdb {sql} writeback")
        s.bdb.add_cell("VIAEXIT", 5, 5)
        with pytest.raises(SystemExit):
            s.do_command("exit 0")
    assert "VIAEXIT" in open(sql).read()


def test_reopen_flushes_previous_writeback(tmp_path):
    sql1 = _make_sql(tmp_path, "a")
    sql2 = _make_sql(tmp_path, "b")
    s = _session()
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command(f"open_bdb {sql1} writeback")
        s.bdb.add_cell("FIRST", 5, 5)
        s.do_command(f"open_bdb {sql2}")   # switching BDBs flushes sql1
    assert "FIRST" in open(sql1).read()


def test_inline_comment_does_not_arm_writeback(tmp_path):
    # `writeback` must be the explicit arg after the path — the word appearing in a
    # trailing comment (do_command only strips full-line comments) must NOT arm it,
    # or a read-looking line could rewrite the committed fixture.
    sql = _make_sql(tmp_path)
    before = open(sql).read()
    s = _session()
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command(f"open_bdb {sql} # no writeback")   # comment mentions writeback
        assert s._bdb_writeback_src is None              # not armed
        s.bdb.add_cell("SHOULDNOTPERSIST", 7, 7)
        with pytest.raises(SystemExit):
            s.do_command("exit 0")                       # exit flush is a no-op
    assert open(sql).read() == before                    # fixture untouched


def test_writeback_on_binary_path_is_ignored(tmp_path):
    binp = str(tmp_path / "x.bdb")
    db = buda.BDB(binp)
    db.add_cell("Z", 1, 1)
    del db
    s = _session()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command(f"open_bdb {binp} writeback")
    assert "applies only to a serialized" in buf.getvalue()
    assert s._bdb_writeback_src is None       # not armed for a binary open
