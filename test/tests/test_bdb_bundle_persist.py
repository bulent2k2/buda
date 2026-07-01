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

"""Stage-1 bundle persistence into the BDB (schema v2), flat and hier flows.

`run_bundler` (flat) and `run_hier_bundler` (hier) write their bundles into the
`bundle` / `bundle_net` / `bundle_busterm` tables when a BDB is open. Membership
is keyed by net *name* so the flat flow persists even though its nets need not
have rows in the BDB `net` table. Bundles round-trip through the diffable *.bdb.sql
serialization.
"""

import contextlib
import io
import sqlite3

import buda
import buda_cli
import bdb_serialize


def _quiet(session, *cmds):
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            session.do_command(c)


def _hier_session(bdb_path, strategy="STRICT"):
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           f"open_bdb {bdb_path}",
           "derive_busterms 1",
           f"run_hier_bundler depth 1 {strategy}")
    return s


def test_hier_bundles_persist(bdb_input):
    s = _hier_session(bdb_input("hier_mixed"))
    rows = s.bdb.all_bundles()
    assert len(rows) == len(s.bundles) >= 1
    # Persisted membership matches the in-memory bundles' nets.
    for w in s.bundles:
        hb = w.input.original_bundle
        assert sorted(s.bdb.bundle_nets(str(hb.id))) == sorted(hb.get_net_names())
    # Hier bundles carry entry/exit busterm roles.
    roles = {r for row in rows for (_bt, r) in s.bdb.bundle_busterms(row.id)}
    assert {"entry", "exit"} & roles
    assert all(r.strategy == "STRICT" for r in rows)
    assert any(r.level >= 1 for r in rows)


def test_flat_bundles_persist_by_net_name(tmp_path):
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           f"open_bdb {tmp_path / 'flat.bdb'}",
           "add_block A 0 0 200 400",
           "add_block B 1000 0 1200 400",
           "add_bus d[8] A.p B.p",
           "run_bundler")
    rows = s.bdb.all_bundles()
    assert len(rows) == len(s.bundles) >= 1
    assert s.bdb.bundle_nets(rows[0].id) == [f"d_{i}" for i in range(8)]
    assert rows[0].strategy == "STRICT"
    assert rows[0].level == 0                       # flat = top level


def test_bundles_roundtrip_through_sql(bdb_input, tmp_path):
    path = bdb_input("hier_mixed")                  # materialized temp binary
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, f"open_bdb {path}", "derive_busterms 1", "run_hier_bundler depth 1")
    before = {r.id: (r.reason, tuple(s.bdb.bundle_nets(r.id)))
              for r in s.bdb.all_bundles()}
    assert before                                   # bundles were persisted

    # Dump the working binary (WAL writes are visible to the ro dump) and rebuild.
    sql = str(tmp_path / "rt.bdb.sql")
    bdb_serialize.dump(path, sql)
    rebuilt = bdb_serialize.load(sql, str(tmp_path / "rebuilt.bdb"))
    db2 = buda.BDB(rebuilt)
    after = {r.id: (r.reason, tuple(db2.bundle_nets(r.id)))
             for r in db2.all_bundles()}
    assert after == before


def test_rerun_bundler_replaces_not_appends(bdb_input):
    s = _hier_session(bdb_input("hier_mixed"))
    n1 = len(s.bdb.all_bundles())
    _quiet(s, "run_hier_bundler depth 1")           # run again
    n2 = len(s.bdb.all_bundles())
    assert n1 == n2 >= 1                             # clear_bundles prevents dupes


def test_flat_run_bundler_without_bdb_is_noop_persist():
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "add_block A 0 0 200 400",
           "add_block B 1000 0 1200 400",
           "add_bus d[4] A.p B.p",
           "run_bundler")
    assert len(s.bundles) >= 1
    assert s._persist_bundles("STRICT") == 0        # no BDB → no-op, no crash


def test_v1_bundle_schema_migrates_to_v2(tmp_path):
    # A v1 DB with the OLD bundle_net (net_id) schema must migrate: the bundle
    # tables are rebuilt to the v2 shape (net_name membership, busterm role).
    p = str(tmp_path / "old.bdb")
    con = sqlite3.connect(p)
    con.executescript(
        """
        CREATE TABLE bundle (id TEXT PRIMARY KEY, depth INTEGER, strategy TEXT,
            parent_id TEXT, is_replicated INTEGER);
        CREATE TABLE bundle_net (bundle_id TEXT, net_id INTEGER,
            PRIMARY KEY (bundle_id, net_id));
        CREATE TABLE bundle_busterm (bundle_id TEXT, busterm_id TEXT,
            PRIMARY KEY (bundle_id, busterm_id));
        PRAGMA user_version = 1;
        """
    )
    con.commit()
    con.close()

    db = buda.BDB(p)                                 # opening migrates v1 -> v2
    assert db.schema_version() == 2
    # The mirrored meta.schema_version must be refreshed on the v1->v2 bump too,
    # not left at "1" (keeps the .bdb.sql dump's version consistent).
    assert db.meta_get("schema_version") == "2"
    del db
    con = sqlite3.connect(p)
    net_cols = {r[1] for r in con.execute("PRAGMA table_info(bundle_net)")}
    bt_cols = {r[1] for r in con.execute("PRAGMA table_info(bundle_busterm)")}
    con.close()
    assert "net_name" in net_cols and "net_id" not in net_cols
    assert "role" in bt_cols
