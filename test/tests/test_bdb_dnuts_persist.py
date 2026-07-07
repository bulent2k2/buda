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

"""Stage-9 detailed-NUTS persistence into the BDB (schema v8).

`run_detailed_nuts` writes each bit-wire into `net_segment` (placed rectangle +
the bit's net identity, name-resolved to `net_id` via _ensure_net) and each
per-bit via into `net_via` (the symbolic `bus_via` fanned out per bit — same
(bundle_id, from_seg, to_seg) key + bit_index). The route_snapshot fingerprint
is rewritten with stage 'detailed_nuts', hashing the net rows too.
"""

import pytest

# Moved to the mid tier: full-pipeline / BDB round-trip / interchange
# integration (keeps the fast tier < 10s). See
# docs/internal/test_runtime_analysis.md.
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


def _detailed_session(bdb_path):
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "source flow/rnr/mix_tracks.buda",
           f"open_bdb {bdb_path}",
           "add_block A 0 0 200 400",
           "add_block B 600 800 800 1200",
           "add_bus d[8] A.p B.p",
           "run_bundler", "generate_topologies", "run_planner 3",
           "run_nuts", "run_detailed_nuts")
    assert s.detailed_result.num_unplaced == 0
    return s


def test_net_segments_persist(tmp_path):
    s = _detailed_session(str(tmp_path / "d.bdb"))
    bid = str(s.bundles[0].input.original_bundle.id)
    segs = s.bdb.net_segments(bid)
    assert len(segs) == len(s.detailed_result.net_segments) == 16   # 2 segs x 8 bits
    ns_map = {(n.seg_idx, n.bit_index): n
              for n in s.detailed_result.net_segments}
    for g in segs:
        ns = ns_map[(g.seg_idx, g.bit_index)]
        assert g.layer == ns.layer and g.width == ns.width
        # Placed-rectangle geometry mirrors the in-memory bit-wire (spans as-is).
        if g.is_horiz:
            assert (g.x1, g.x2) == (ns.span_lo, ns.span_hi)
            assert g.y1 < ns.track_position < g.y2
        else:
            assert (g.y1, g.y2) == (ns.span_lo, ns.span_hi)
            assert g.x1 < ns.track_position < g.x2


def test_net_identity_resolves(tmp_path):
    # net_name = net_names[bit_index] (logical bit), resolved to a real net row.
    s = _detailed_session(str(tmp_path / "d.bdb"))
    w = s.bundles[0]
    bid = str(w.input.original_bundle.id)
    names = list(w.input.original_bundle.get_net_names())
    for g in s.bdb.net_segments(bid):
        assert g.net_name == names[g.bit_index]
        assert g.net_id >= 0
    for v in s.bdb.net_vias(bid):
        assert v.net_name == names[v.bit_index]
        assert v.net_id >= 0
    del s
    # The join is DB-real: raw sqlite JOIN net returns the same names.
    con = sqlite3.connect(str(tmp_path / "d.bdb"))
    rows = con.execute(
        "SELECT s.bit_index, n.name FROM net_segment s"
        " JOIN net n ON s.net_id = n.id WHERE s.bundle_id=?", (bid,)).fetchall()
    con.close()
    assert rows and all(name == names[bit] for bit, name in rows)


def test_net_vias_fan_out_bus_vias(tmp_path):
    # Every symbolic bus_via fans out to bit_width net_via rows sharing its
    # (from_seg, to_seg) key.
    s = _detailed_session(str(tmp_path / "d.bdb"))
    bid = str(s.bundles[0].input.original_bundle.id)
    bus_keys = {(v.from_seg, v.to_seg) for v in s.bdb.bus_vias(bid)}
    net_vias = s.bdb.net_vias(bid)
    assert len(net_vias) == len(bus_keys) * 8
    assert {(v.from_seg, v.to_seg) for v in net_vias} == bus_keys
    # Rows mirror the in-memory NetVias exactly.
    mem = {(v.from_seg, v.to_seg, v.bit_index): (v.from_layer, v.to_layer, v.x, v.y)
           for v in s.detailed_result.net_vias}
    for v in net_vias:
        assert mem[(v.from_seg, v.to_seg, v.bit_index)] == \
               (v.from_layer, v.to_layer, v.x, v.y)


def test_route_snapshot_covers_detailed_stage(tmp_path):
    s = _detailed_session(str(tmp_path / "d.bdb"))
    snap = s.bdb.route_snapshot()
    assert snap.stage == "detailed_nuts"
    assert snap.hash
    # Bus counts survive the detailed rewrite; net counts are recorded.
    bid = str(s.bundles[0].input.original_bundle.id)
    assert snap.n_bus_segments == len(s.bdb.bus_segments(bid))
    assert snap.n_bus_vias == len(s.bdb.bus_vias(bid))
    assert snap.n_net_segments == len(s.bdb.net_segments(bid)) == 16
    assert snap.n_net_vias == len(s.bdb.net_vias(bid)) == 8


def test_detailed_hash_differs_from_abstract(tmp_path):
    # The net rows are hashed too, so the detailed-stage fingerprint differs
    # from the abstract-stage one over the same bus routing.
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "source flow/rnr/mix_tracks.buda",
           f"open_bdb {tmp_path / 'd.bdb'}",
           "add_block A 0 0 200 400",
           "add_block B 600 800 800 1200",
           "add_bus d[8] A.p B.p",
           "run_bundler", "generate_topologies", "run_planner 3", "run_nuts")
    h_abs = s.bdb.route_snapshot().hash
    assert s.bdb.route_snapshot().stage == "abstract_nuts"
    _quiet(s, "run_detailed_nuts")
    h_det = s.bdb.route_snapshot().hash
    assert h_det and h_det != h_abs


def test_rerun_detailed_nuts_replaces_rows(tmp_path):
    s = _detailed_session(str(tmp_path / "d.bdb"))
    bid = str(s.bundles[0].input.original_bundle.id)
    n1 = (len(s.bdb.net_segments(bid)), len(s.bdb.net_vias(bid)))
    h1 = s.bdb.route_snapshot().hash
    _quiet(s, "run_detailed_nuts")            # re-solve: cleared + rewritten
    assert (len(s.bdb.net_segments(bid)), len(s.bdb.net_vias(bid))) == n1
    assert s.bdb.route_snapshot().hash == h1  # identical routing, stable hash


def test_rerun_nuts_invalidates_detailed_rows(tmp_path):
    # The net rows are derived from the bus rows; re-solving abstract NUTS
    # must wipe them and drop the stage back to abstract_nuts.
    s = _detailed_session(str(tmp_path / "d.bdb"))
    bid = str(s.bundles[0].input.original_bundle.id)
    assert s.bdb.net_segments(bid)
    _quiet(s, "run_nuts")
    assert not s.bdb.net_segments(bid) and not s.bdb.net_vias(bid)
    snap = s.bdb.route_snapshot()
    assert snap.stage == "abstract_nuts"
    assert snap.n_net_segments == 0 and snap.n_net_vias == 0


def test_rebundle_wipes_net_rows(tmp_path):
    # clear_bundles (via re-persisting the bundler output) drops the net rows
    # before their parent bundles — FK-safe, no stale fingerprint.
    s = _detailed_session(str(tmp_path / "d.bdb"))
    bid = str(s.bundles[0].input.original_bundle.id)
    assert s.bdb.net_segments(bid)
    _quiet(s, "run_bundler")
    assert not s.bdb.net_segments(bid) and not s.bdb.net_vias(bid)
    assert not s.bdb.route_snapshot().hash


def test_roundtrip_through_sql(tmp_path):
    path = str(tmp_path / "d.bdb")
    s = _detailed_session(path)
    bid = str(s.bundles[0].input.original_bundle.id)
    before_segs = [(g.seg_idx, g.bit_index, g.net_name, g.layer,
                    g.x1, g.y1, g.x2, g.y2) for g in s.bdb.net_segments(bid)]
    before_vias = [(v.from_seg, v.to_seg, v.bit_index, v.net_name,
                    v.from_layer, v.to_layer, v.x, v.y)
                   for v in s.bdb.net_vias(bid)]
    before_snap = s.bdb.route_snapshot()
    assert before_segs and before_vias

    sql = str(tmp_path / "rt.bdb.sql")
    bdb_serialize.dump(path, sql)
    db2 = buda.BDB(bdb_serialize.load(sql, str(tmp_path / "rebuilt.bdb")))
    assert [(g.seg_idx, g.bit_index, g.net_name, g.layer,
             g.x1, g.y1, g.x2, g.y2) for g in db2.net_segments(bid)] == before_segs
    assert [(v.from_seg, v.to_seg, v.bit_index, v.net_name,
             v.from_layer, v.to_layer, v.x, v.y)
            for v in db2.net_vias(bid)] == before_vias
    snap2 = db2.route_snapshot()
    assert (snap2.hash, snap2.stage, snap2.n_net_segments, snap2.n_net_vias) == \
           (before_snap.hash, before_snap.stage,
            before_snap.n_net_segments, before_snap.n_net_vias)


def test_hier_replan_with_net_rows_is_fk_safe(bdb_input):
    # Hier expanded (is_replicated=1) bundles can own net rows after
    # run_detailed_nuts; re-persisting the planner output (clear_expanded_bundles)
    # must drop them FIRST or the FK would reject deleting the bundle. The
    # hier_mixed fixture isn't detailed-routable (all bits unplaced on its tiny
    # geometry), so write the net rows against an expanded bundle directly —
    # the delete ordering is what's under test.
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "source flow/rnr/mix_tracks.buda",
           f"open_bdb {bdb_input('hier_mixed')}",
           "derive_busterms 1",
           "run_hier_bundler depth 1",
           "generate_hier_topologies",
           "run_planner hier 3")
    reps = [b for b in s.bdb.all_bundles() if b.is_replicated]
    assert reps
    g = buda.NetSegRow()
    g.id = reps[0].id
    g.seg_idx, g.bit_index, g.net_name, g.layer = 0, 0, "hbit", 4
    s.bdb.add_net_segment(g)
    v = buda.NetViaRow()
    v.id = reps[0].id
    v.from_seg, v.to_seg, v.bit_index, v.net_name = 0, 1, 0, "hbit"
    s.bdb.add_net_via(v)
    assert s.bdb.net_segments(reps[0].id) and s.bdb.net_vias(reps[0].id)
    with contextlib.redirect_stdout(io.StringIO()):
        s._persist_planner_output()           # clear_expanded_bundles: no FK error
    assert [b for b in s.bdb.all_bundles() if b.is_replicated]
    assert not s.bdb.net_segments(reps[0].id)  # child rows went with the bundle


def test_late_open_bdb_persists_abstract_stage_too(tmp_path):
    # BDB opened only AFTER run_nuts: the detailed persist must materialize the
    # abstract bus rows first — otherwise the DB would record stage
    # 'detailed_nuts' with zero bus counts and net rows describing bus routing
    # that isn't there (Codex #134 P2).
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "source flow/rnr/mix_tracks.buda",
           "add_block A 0 0 200 400",
           "add_block B 600 800 800 1200",
           "add_bus d[8] A.p B.p",
           "run_bundler", "generate_topologies", "run_planner 3",
           "run_nuts",                            # no BDB yet
           f"open_bdb {tmp_path / 'late.bdb'}",   # open late
           "run_detailed_nuts")
    bid = str(s.bundles[0].input.original_bundle.id)
    assert s.bdb.bus_segments(bid) and s.bdb.bus_vias(bid)   # abstract persisted
    assert s.bdb.net_segments(bid) and s.bdb.net_vias(bid)
    snap = s.bdb.route_snapshot()
    assert snap.stage == "detailed_nuts"
    assert snap.n_bus_segments == len(s.bdb.bus_segments(bid)) >= 1
    assert snap.n_bus_vias == len(s.bdb.bus_vias(bid)) >= 1
    assert snap.n_net_segments == len(s.bdb.net_segments(bid)) >= 1


def test_no_bdb_is_noop():
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "source flow/rnr/mix_tracks.buda",
           "add_block A 0 0 200 400",
           "add_block B 600 800 800 1200",
           "add_bus d[8] A.p B.p",
           "run_bundler", "generate_topologies", "run_planner 3",
           "run_nuts", "run_detailed_nuts")
    assert s.detailed_result is not None
    assert s._persist_detailed_nuts() == (0, 0)


def test_orphan_and_null_net_rows_rejected(tmp_path):
    # FK + NOT NULL are DB-enforced on the new tables too.
    path = str(tmp_path / "d.bdb")
    s = _detailed_session(path)
    del s
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON;")
    try:
        for stmt in (
                "INSERT INTO net_segment(bundle_id,seg_idx,bit_index) "
                "VALUES('nope',0,0)",
                "INSERT INTO net_segment(bundle_id,seg_idx,bit_index) "
                "VALUES(NULL,0,0)",
                "INSERT INTO net_via(bundle_id,from_seg,to_seg,bit_index) "
                "VALUES('nope',0,1,0)",
                "INSERT INTO net_via(bundle_id,from_seg,to_seg,bit_index) "
                "VALUES(NULL,0,1,0)"):
            raised = False
            try:
                con.execute(stmt)
                con.commit()
            except sqlite3.IntegrityError:
                raised = True
            assert raised, f"accepted: {stmt}"
    finally:
        con.close()


def test_v7_db_migrates_to_v8(tmp_path):
    # A v7 DB (route_snapshot WITHOUT the net-count columns) migrates: the net
    # tables appear and route_snapshot gains both columns.
    p = str(tmp_path / "v7.bdb")
    con = sqlite3.connect(p)
    con.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO meta VALUES('schema_version','7');
        CREATE TABLE route_snapshot (
            id INTEGER PRIMARY KEY, hash TEXT,
            n_bus_segments INTEGER DEFAULT 0, n_bus_vias INTEGER DEFAULT 0,
            stage TEXT);
        INSERT INTO route_snapshot VALUES(1,'abc',2,1,'abstract_nuts');
        PRAGMA user_version = 7;
        """
    )
    con.commit()
    con.close()

    db = buda.BDB(p)
    assert db.schema_version() == buda.BDB.SCHEMA_VERSION
    assert db.meta_get("schema_version") == str(buda.BDB.SCHEMA_VERSION)
    snap = db.route_snapshot()                # pre-migration row survives
    assert (snap.hash, snap.n_bus_segments, snap.n_bus_vias) == ("abc", 2, 1)
    assert snap.n_net_segments == 0 and snap.n_net_vias == 0
    del db
    con = sqlite3.connect(p)
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    cols = {r[1] for r in con.execute("PRAGMA table_info(route_snapshot)")}
    con.close()
    assert {"net_segment", "net_via"} <= tables
    assert {"n_net_segments", "n_net_vias"} <= cols
