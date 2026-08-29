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

"""Schema v7: route_snapshot fingerprint + hard FK on bus_segment/bus_via.

`run_nuts` now records a singleton `route_snapshot` row — a content hash over the
whole routed output (bus_segment + bus_via) plus its row counts — so a routing
change is one reviewable line in the *.bdb.sql diff. `bus_segment.bundle_id` and
`bus_via.bundle_id` are now hard foreign keys to `bundle(id)`: every bus row joins
a persisted bundle, and clearing bundles must drop their bus rows first.
"""

import pytest

# Moved to the mid tier: full-pipeline / BDB round-trip / interchange
# integration (keeps the fast tier < 10s). See
# docs/internal/test/runtime_analysis.md.
pytestmark = pytest.mark.mid

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


def _flat_routed_session(bdb_path):
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "source flow/rnr/mix_tracks.buda",
           f"open_bdb {bdb_path}",
           "add_block A 0 0 200 400",
           "add_block B 600 800 800 1200",
           "add_bus d[8] A.p B.p",
           "run_bundler", "generate_topologies", "run_planner 3", "run_nuts")
    return s


def test_route_snapshot_recorded(tmp_path):
    s = _flat_routed_session(str(tmp_path / "flat.bdb"))
    snap = s.bdb.route_snapshot()
    assert snap.hash                                    # a digest was written
    assert snap.stage == "abstract_nuts"
    # Counts mirror what was persisted.
    bid = str(s.bundles[0].input.original_bundle.id)
    assert snap.n_bus_segments == len(s.bdb.bus_segments(bid))
    assert snap.n_bus_vias == len(s.bdb.bus_vias(bid))
    assert snap.n_bus_segments >= 1


def test_route_snapshot_hash_is_stable(tmp_path):
    # Re-solving the identical routing must yield the identical hash — the digest
    # is over a canonical (order-independent) serialization, so it is diff-stable.
    s = _flat_routed_session(str(tmp_path / "flat.bdb"))
    h1 = s.bdb.route_snapshot().hash
    _quiet(s, "run_nuts")
    assert s.bdb.route_snapshot().hash == h1


def test_route_snapshot_hash_changes_on_reroute(tmp_path):
    # Pinning a different candidate topology changes the routed geometry, so the
    # fingerprint must change.
    s = _flat_routed_session(str(tmp_path / "flat.bdb"))
    h1 = s.bdb.route_snapshot().hash
    bid = str(s.bundles[0].input.original_bundle.id)
    ncand = len(s.bundles[0].input.candidates)
    first = s.bundles[0].plan.selected_topology_index
    other = next(i for i in range(ncand) if i != first)
    _quiet(s, f"select_topology {bid} {other + 1}", "run_planner 3", "run_nuts")
    assert s.bdb.route_snapshot().hash != h1


def test_route_snapshot_roundtrips_through_sql(tmp_path):
    path = str(tmp_path / "flat.bdb")
    s = _flat_routed_session(path)
    before = s.bdb.route_snapshot()
    sql = str(tmp_path / "rt.bdb.sql")
    bdb_serialize.dump(path, sql)
    db2 = buda.BDB(bdb_serialize.load(sql, str(tmp_path / "rebuilt.bdb")))
    after = db2.route_snapshot()
    assert (after.hash, after.n_bus_segments, after.n_bus_vias, after.stage) == \
           (before.hash, before.n_bus_segments, before.n_bus_vias, before.stage)


def test_bus_rows_reference_a_bundle(tmp_path):
    # Hard FK: every persisted bus row's bundle_id resolves to a bundle row.
    s = _flat_routed_session(str(tmp_path / "flat.bdb"))
    bundle_ids = {b.id for b in s.bdb.all_bundles()}
    bid = str(s.bundles[0].input.original_bundle.id)
    assert bid in bundle_ids
    assert all(g.id in bundle_ids for g in s.bdb.bus_segments(bid))
    assert all(v.id in bundle_ids for v in s.bdb.bus_vias(bid))


def test_orphan_bus_row_rejected_by_fk(tmp_path):
    # The FK is DB-enforced: inserting a bus_segment for a non-existent bundle fails.
    path = str(tmp_path / "flat.bdb")
    s = _flat_routed_session(path)          # ensure schema exists (v7)
    del s
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON;")
    try:
        raised = False
        try:
            con.execute(
                "INSERT INTO bus_segment(bundle_id,seg_idx,layer) "
                "VALUES('nope_no_such_bundle', 0, 1)")
            con.commit()
        except sqlite3.IntegrityError:
            raised = True
        assert raised, "FK did not reject an orphan bus_segment row"
    finally:
        con.close()


def test_null_bundle_id_bus_row_rejected(tmp_path):
    # SQLite treats a NULL child key as satisfying the FK, so bundle_id must be
    # NOT NULL for the "every bus row joins a bundle" invariant to hold even for
    # direct SQL / hand-edited dump loads (Codex #129 P2).
    path = str(tmp_path / "flat.bdb")
    s = _flat_routed_session(path)          # ensure schema exists (v7)
    del s
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON;")
    try:
        for stmt in (
                "INSERT INTO bus_segment(bundle_id,seg_idx,layer) VALUES(NULL,0,1)",
                "INSERT INTO bus_via(bundle_id,from_seg,to_seg) VALUES(NULL,0,1)"):
            raised = False
            try:
                con.execute(stmt)
                con.commit()
            except sqlite3.IntegrityError:
                raised = True
            assert raised, f"NULL bundle_id was accepted: {stmt}"
    finally:
        con.close()


def test_route_snapshot_invalidated_when_bus_rows_cleared(tmp_path):
    # Clearing the bus tables (without recomputing) must not leave a stale
    # fingerprint that no longer describes the DB (Codex #129 P2).
    s = _flat_routed_session(str(tmp_path / "flat.bdb"))
    assert s.bdb.route_snapshot().hash          # present after run_nuts
    s.bdb.clear_bus_routing()
    snap = s.bdb.route_snapshot()
    assert not snap.hash and snap.n_bus_segments == 0 and snap.n_bus_vias == 0


def test_route_snapshot_invalidated_when_bundles_recomputed(tmp_path):
    # Re-bundling (clear_bundles via _persist_bundles) drops the bus rows too, so
    # the fingerprint must be cleared rather than left describing stale routing.
    s = _flat_routed_session(str(tmp_path / "flat.bdb"))
    assert s.bdb.route_snapshot().hash
    _quiet(s, "run_bundler")                     # re-persists bundles -> clear_bundles
    assert not s.bdb.route_snapshot().hash


def test_hier_reexpand_after_nuts_is_fk_safe(bdb_input):
    # Expanded (is_replicated=1) bundles own bus rows after run_nuts. Re-persisting
    # the planner output calls clear_expanded_bundles(), which must drop those bus
    # rows FIRST or the FK to bundle(id) would reject deleting a still-referenced
    # bundle. (Re-persist rather than a second run_planner hier, which re-expands.)
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "source flow/rnr/mix_tracks.buda",
           f"open_bdb {bdb_input('hier_mixed')}",
           "derive_busterms 1",
           "run_hier_bundler depth 1",
           "generate_hier_topologies",
           "run_planner hier 3",
           "run_nuts")
    reps = [b for b in s.bdb.all_bundles() if b.is_replicated]
    assert reps
    # Every expanded bundle that has bus rows exercises the FK-safe clear below.
    assert any(s.bdb.bus_segments(b.id) for b in reps)
    with contextlib.redirect_stdout(io.StringIO()):
        s._persist_planner_output()          # clears expanded bundles (+ their bus rows)
    assert [b for b in s.bdb.all_bundles() if b.is_replicated]


def test_run_nuts_before_planner_persist_satisfies_fk(tmp_path):
    # run_nuts reached without the planner output having been persisted: _persist_nuts
    # must persist the parents first so the bus-row FK is satisfiable.
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "source flow/rnr/mix_tracks.buda",
           "add_block A 0 0 200 400",
           "add_block B 600 800 800 1200",
           "add_bus d[8] A.p B.p",
           "run_bundler", "generate_topologies", "run_planner 3",
           f"open_bdb {tmp_path / 'late.bdb'}",   # BDB opened AFTER planning
           "run_nuts")
    bid = str(s.bundles[0].input.original_bundle.id)
    bundle_ids = {b.id for b in s.bdb.all_bundles()}
    assert bid in bundle_ids and s.bdb.bus_segments(bid)


def test_v6_db_migrates_and_filters_orphan_bus_rows(tmp_path):
    # A v6 DB with a pre-FK orphan bus_segment (no matching bundle) migrates to
    # v7; the rebuild drops the orphan and the route_snapshot table appears.
    p = str(tmp_path / "v6.bdb")
    con = sqlite3.connect(p)
    con.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO meta VALUES('schema_version','6');
        CREATE TABLE bundle (id TEXT PRIMARY KEY);
        INSERT INTO bundle VALUES('1');
        CREATE TABLE bus_segment (
            bundle_id TEXT, seg_idx INTEGER, layer INTEGER, is_horiz INTEGER,
            x1 REAL, y1 REAL, x2 REAL, y2 REAL, track_position REAL, width REAL,
            placed INTEGER, is_jog INTEGER, PRIMARY KEY(bundle_id, seg_idx));
        INSERT INTO bus_segment VALUES('1',0,1,1,0,0,0,0,0,0,0,0);
        INSERT INTO bus_segment VALUES('orphan',0,1,1,0,0,0,0,0,0,0,0);
        CREATE TABLE bus_via (
            bundle_id TEXT, from_seg INTEGER, to_seg INTEGER, from_layer INTEGER,
            to_layer INTEGER, x REAL, y REAL, bit_width INTEGER,
            PRIMARY KEY(bundle_id, from_seg, to_seg));
        INSERT INTO bus_via VALUES('orphan',0,1,1,2,0,0,8);
        PRAGMA user_version = 6;
        """
    )
    con.commit()
    con.close()

    db = buda.BDB(p)
    assert db.schema_version() == buda.BDB.SCHEMA_VERSION
    assert db.bus_segments("1")                 # the valid row survived
    assert not db.bus_segments("orphan")        # the orphan was dropped
    assert not db.bus_vias("orphan")
    del db
    con = sqlite3.connect(p)
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert "route_snapshot" in tables
