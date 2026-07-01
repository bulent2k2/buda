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

    db = buda.BDB(p)
    assert db.schema_version() == 5
    assert db.meta_get("schema_version") == "5"
    del db
    con = sqlite3.connect(p)
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert {"bus_segment", "bus_via"} <= tables
