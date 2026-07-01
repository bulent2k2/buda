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

"""Diffable BDB test-data management.

Exercises the `bdb_input` conftest fixture and `tools/bdb_serialize`:

  * a checked-in `*.bdb.sql` text fixture materializes into a throwaway temp .bdb,
  * running the hier pipeline against it mutates ONLY the temp copy (the committed
    fixture is never dirtied), and
  * dump→load→dump is byte-stable (deterministic, so regeneration is a no-op diff).
"""

import os
import filecmp

import buda
import buda_cli
import bdb_serialize
import conftest

DATA_DIR = conftest.DATA_DIR
FIXTURE_SQL = os.path.join(DATA_DIR, "hier_mixed.bdb.sql")


def test_fixture_materializes_and_opens(bdb_input):
    # The fixture rebuilds a real binary BDB in tmp_path from the committed SQL.
    path = bdb_input("hier_mixed")
    assert os.path.exists(path)
    db = buda.BDB(path)
    names = {n.name for n in db.all_nets()}
    assert {"s2p_0", "p2s_0", "ab_fwd_0", "ab_rev_0", "ab_extra"} <= names
    assert db.all_busterms()  # derive_busterms output round-tripped through the dump


def test_hier_pipeline_does_not_dirty_checked_in_fixture(bdb_input):
    # Snapshot the committed SQL, run a mutating hier flow on the temp copy, and
    # confirm the checked-in fixture is byte-for-byte unchanged.
    before = open(FIXTURE_SQL, encoding="utf-8").read()
    path = bdb_input("hier_mixed")

    sess = buda_cli.BudaSession()
    sess.no_viz = True
    for cmd in (f"open_bdb {path}",
                "def_layer 4 M4 H TOP 44.44", "def_layer 5 M5 V TOP 50.00",
                "add_blocks_from_bdb 0", "add_blocks_from_bdb 1 skip",
                "run_hier_bundler depth 1 bidirectional"):
        sess.do_command(cmd)
    assert sess.bundles  # pipeline actually ran

    after = open(FIXTURE_SQL, encoding="utf-8").read()
    assert before == after, "checked-in fixture was modified by the pipeline"


def test_dump_load_dump_is_stable(bdb_input, tmp_path):
    path = bdb_input("hier_mixed")
    sql1 = str(tmp_path / "a.bdb.sql")
    rebuilt = str(tmp_path / "rebuilt.bdb")
    sql2 = str(tmp_path / "b.bdb.sql")
    bdb_serialize.dump(path, sql1)
    bdb_serialize.load(sql1, rebuilt)
    bdb_serialize.dump(rebuilt, sql2)
    assert filecmp.cmp(sql1, sql2, shallow=False)


def test_committed_fixture_is_up_to_date():
    # Guards against a stale fixture: the committed SQL must match what the builder
    # produces today (schema drift or a builder edit would surface here).
    import build_fixtures

    assert build_fixtures.main(["build_fixtures.py", "--check"]) == 0, (
        "test/tests/data/*.bdb.sql is stale — "
        "regenerate via test/tests/data/build_fixtures.py")


def test_readonly_conn_cannot_write(bdb_input):
    path = bdb_input("hier_mixed")
    conn = conftest.readonly_conn(path)
    try:
        n = conn.execute("SELECT COUNT(*) FROM net").fetchone()[0]
        assert n >= 5
        import sqlite3
        try:
            conn.execute("DELETE FROM net")
            raise AssertionError("read-only connection allowed a write")
        except sqlite3.OperationalError:
            pass
    finally:
        conn.close()
