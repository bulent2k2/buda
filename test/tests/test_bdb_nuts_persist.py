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

"""Stage-4 abstract-NUTS bus-routing persistence into the BDB (schema v5).

`run_nuts` writes each placed bus segment into `bus_segment` (placed rectangle +
layer) and one **symbolic bus-via** per bus-level layer transition into `bus_via`.
Both round-trip through the diffable *.bdb.sql serialization. `bundle_id` is a soft
link (the hier flow's expanded per-instance ids need not have bundle-table rows).
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


def _flat_routed_session(bdb_path):
    # A and B offset in BOTH x and y so the route bends (H seg + V seg) -> a via.
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "source flow/rnr/mix_tracks.buda",
           f"open_bdb {bdb_path}",
           "add_block A 0 0 200 400",
           "add_block B 600 800 800 1200",
           "add_bus d[8] A.p B.p",
           "run_bundler",
           "generate_topologies",
           "run_planner 3",
           "run_nuts")
    return s


def test_flat_bus_segments_persist(tmp_path):
    s = _flat_routed_session(str(tmp_path / "flat.bdb"))
    bid = str(s.bundles[0].input.original_bundle.id)
    segs = s.bdb.bus_segments(bid)
    assert len(segs) == len(s.nuts_result.segments) >= 1
    # Persisted geometry matches the placed TrackSegments.
    ts_map = {ts.seg_idx: ts for ts in s.nuts_result.segments
              if ts.bundle_id == s.bundles[0].input.original_bundle.id}
    for sg in segs:
        ts = ts_map[sg.seg_idx]
        assert sg.layer == ts.layer and sg.is_horiz == ts.horiz and sg.placed
        if ts.horiz:
            assert (sg.x1, sg.x2) == (ts.span_lo, ts.span_hi)
            assert sg.y1 < ts.track_position < sg.y2
        else:
            assert (sg.y1, sg.y2) == (ts.span_lo, ts.span_hi)
            assert sg.x1 < ts.track_position < sg.x2


def test_bus_via_at_layer_transition(tmp_path):
    s = _flat_routed_session(str(tmp_path / "flat.bdb"))
    bid = str(s.bundles[0].input.original_bundle.id)
    vias = s.bdb.bus_vias(bid)
    assert len(vias) >= 1                       # the L-bend is a layer transition
    v = vias[0]
    assert v.from_layer != v.to_layer           # it IS a transition
    assert v.bit_width == 8                      # d[8]
    # The two segments it joins were persisted on those layers.
    seg_layers = {sg.seg_idx: sg.layer for sg in s.bdb.bus_segments(bid)}
    assert seg_layers[v.from_seg] == v.from_layer
    assert seg_layers[v.to_seg] == v.to_layer


def test_via_at_t_junction_multiterminal(tmp_path):
    # A 3-way multicast (A -> [B, C, D]) routes as a trunk with stubs that land on
    # the trunk's INTERIOR (T-junctions), not just shared corners. Vias must be
    # recorded for those cross-layer T-junctions too — endpoint-equality adjacency
    # would miss them (Codex #127 P1). Derivation must match the ConnTopology
    # connection model (which infers T-junctions).
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "source flow/rnr/mix_tracks.buda",
           f"open_bdb {tmp_path / 't.bdb'}",
           "add_block A 0 550 200 650",           # driver, mid-left
           "add_block B 900 100 1100 200",        # receivers stacked on the right
           "add_block C 900 550 1100 650",
           "add_block D 900 1000 1100 1100",
           "add_bus bus[8] A.p B.p,C.p,D.p",
           "run_bundler", "generate_topologies", "run_planner 5", "run_nuts")
    w = s.bundles[0]
    bid = str(w.input.original_bundle.id)
    topo = w.input.candidates[w.plan.selected_topology_index]
    vias = s.bdb.bus_vias(bid)

    # Expected cross-layer SEG-connected pairs from the canonical connection model.
    ts_map = {ts.seg_idx: ts for ts in s.nuts_result.segments
              if ts.bundle_id == w.input.original_bundle.id}
    ct = buda.ConnTopology()
    ct.build(topo, s.fp)
    expected = set()
    for a, cs in enumerate(ct.segs()):
        ta = ts_map.get(a)
        if not ta or not ta.placed:
            continue
        for conn in cs.conns:
            if conn.kind != buda.SegConnKind.SEG:
                continue
            tb = ts_map.get(conn.seg_idx)
            if tb and tb.placed and ta.layer != tb.layer:
                expected.add((min(a, conn.seg_idx), max(a, conn.seg_idx)))
    persisted = {(v.from_seg, v.to_seg) for v in vias}
    assert persisted == expected and expected      # complete, no spurious, non-empty

    # At least one via is a genuine T-junction (segments share NO nominal endpoint)
    # — the case the old endpoint-only logic dropped. Guards test coverage too.
    eps = [{(seg.start.x, seg.start.y), (seg.end.x, seg.end.y)}
           for seg in topo.segments]
    assert any(not (eps[a] & eps[b]) for (a, b) in persisted), \
        "scenario no longer exercises a T-junction via"


def test_bus_routing_roundtrip_through_sql(tmp_path):
    path = str(tmp_path / "flat.bdb")
    s = _flat_routed_session(path)
    bid = str(s.bundles[0].input.original_bundle.id)
    before_segs = [(g.seg_idx, g.layer, g.x1, g.y1, g.x2, g.y2)
                   for g in s.bdb.bus_segments(bid)]
    before_vias = [(v.from_seg, v.to_seg, v.from_layer, v.to_layer, v.x, v.y,
                    v.bit_width) for v in s.bdb.bus_vias(bid)]
    assert before_segs

    sql = str(tmp_path / "rt.bdb.sql")
    bdb_serialize.dump(path, sql)
    db2 = buda.BDB(bdb_serialize.load(sql, str(tmp_path / "rebuilt.bdb")))
    after_segs = [(g.seg_idx, g.layer, g.x1, g.y1, g.x2, g.y2)
                  for g in db2.bus_segments(bid)]
    after_vias = [(v.from_seg, v.to_seg, v.from_layer, v.to_layer, v.x, v.y,
                   v.bit_width) for v in db2.bus_vias(bid)]
    assert after_segs == before_segs
    assert after_vias == before_vias


def test_rerun_nuts_replaces_bus_routing(tmp_path):
    s = _flat_routed_session(str(tmp_path / "flat.bdb"))
    bid = str(s.bundles[0].input.original_bundle.id)
    n1 = len(s.bdb.bus_segments(bid))
    _quiet(s, "run_nuts")                       # re-solve
    assert len(s.bdb.bus_segments(bid)) == n1   # cleared + rewritten, no dupes


def test_run_nuts_without_bdb_is_noop():
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "source flow/rnr/mix_tracks.buda",
           "add_block A 0 0 200 400",
           "add_block B 600 800 800 1200",
           "add_bus d[8] A.p B.p",
           "run_bundler", "generate_topologies", "run_planner 3", "run_nuts")
    assert s.nuts_result is not None
    assert s._persist_nuts() == (0, 0)          # no BDB → no-op, no crash


def test_v4_db_migrates_to_v5_adds_bus_tables(tmp_path):
    p = str(tmp_path / "v4.bdb")
    con = sqlite3.connect(p)
    con.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO meta VALUES('schema_version','4');
        PRAGMA user_version = 4;
        """
    )
    con.commit()
    con.close()

    db = buda.BDB(p)                            # migrates forward to current
    assert db.schema_version() == buda.BDB.SCHEMA_VERSION
    assert db.meta_get("schema_version") == str(buda.BDB.SCHEMA_VERSION)
    del db
    con = sqlite3.connect(p)
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert {"bus_segment", "bus_via"} <= tables   # v5 step ran
