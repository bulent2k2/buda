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

"""Resume-gap closure (schema v11).

Three gaps left after the load_pipeline series:
1. `Topology.bridge_segments` (TEG-over bridges) was the one un-persisted
   Topology field — persisted now in `topology_bridge_segment` and restored by
   `load_pipeline`, so TEG-over multi-rect designs resume losslessly.
2. `run_nuts_on_layer` re-solved a layer without re-persisting, leaving a BDB
   checkpoint holding the pre-rerun routing (the same stale-state bug
   ripup_reroute had) — `_rerun_nuts_layer` now re-persists (bus + detailed).
3. Hier detailed-persistence had no flow-level coverage (hier_mixed is not
   detailed-routable) — the `hier_routed` fixture closes that.
"""

import contextlib
import io
import sqlite3

import buda
import buda_cli


def _quiet(session, *cmds):
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        for c in cmds:
            session.do_command(c)
    return buf.getvalue()


def _fresh(*cmds):
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = _quiet(s, *cmds)
    return s, out


# Pure-TEG block: two disjoint rects with an x-gap; a source above the gap makes
# the V-trunk candidates fall in it -> an explicit bridge segment on the union
# bbox's outer face (teg_mode over).
TEG_SETUP = ("source flow/rnr/mix_tracks.buda",
             "add_block T rect 0 0 200 400 rect 500 0 700 400 teg_mode over",
             "add_block src 300 800 400 900",
             "add_bus d[4] src.tx T.rx")


def _bridges(w):
    return [{name: (seg.start.x, seg.start.y, seg.end.x, seg.end.y,
                    seg.layer_hint, seg.is_jog)
             for name, seg in t.bridge_segments.items()}
            for t in w.input.candidates]


def test_bridge_segments_persist(tmp_path):
    s, _ = _fresh(*TEG_SETUP, f"open_bdb {tmp_path / 't.bdb'}",
                  "run_bundler", "generate_topologies")
    w = s.bundles[0]
    mem = _bridges(w)
    assert any(mem), "TEG scenario should produce bridge candidates"
    for ci, m in enumerate(mem):
        rows = s.bdb.topology_bridges("1", ci)
        got = {r.block_name: (r.x1, r.y1, r.x2, r.y2, r.layer_hint, r.is_jog)
               for r in rows}
        assert got == m                      # row-for-row, incl. empty (no rows)


def test_bridge_segments_survive_resume(tmp_path):
    # Checkpoint after topo-gen -> fresh session -> load_pipeline: every
    # candidate's bridge_segments round-trips (the field load_pipeline used to
    # silently drop — TEG-over designs now resume losslessly).
    db = str(tmp_path / "t.bdb")
    s1, _ = _fresh(*TEG_SETUP, f"open_bdb {db}",
                   "run_bundler", "generate_topologies")
    mem = _bridges(s1.bundles[0])
    assert any(mem)
    del s1

    s2, out = _fresh("source flow/rnr/mix_tracks.buda",
                     "add_block T rect 0 0 200 400 rect 500 0 700 400 teg_mode over",
                     "add_block src 300 800 400 900",
                     f"open_bdb {db}", "load_pipeline")
    assert "rehydrated" in out
    assert _bridges(s2.bundles[0]) == mem


def test_run_nuts_on_layer_repersists(tmp_path):
    # Re-solving one layer must re-persist: a checkpoint after run_nuts_on_layer
    # reflects the re-solved routing, not the pre-rerun rows (same stale-state
    # class of bug ripup_reroute had).
    s, _ = _fresh("source flow/rnr/mix_tracks.buda",
                  f"open_bdb {tmp_path / 'n.bdb'}",
                  "add_block A 0 0 200 400",
                  "add_block B 600 800 800 1200",
                  "add_bus d[8] A.p B.p",
                  "run_bundler", "generate_topologies", "run_planner 3",
                  "run_nuts")
    # Wipe the persisted routing, then rerun one layer: only the re-persist in
    # _rerun_nuts_layer can repopulate the rows.
    s.bdb.clear_bus_routing()
    assert not s.bdb.bus_segments("1")
    layer_id = s.nuts_result.segments[0].layer
    layer_name = s._make_layer_names()[layer_id]
    _quiet(s, f"run_nuts_on_layer {layer_name}")
    rows = s.bdb.bus_segments("1")
    assert rows                                          # re-persisted
    ts = {t.seg_idx: t for t in s.nuts_result.segments}
    for g in rows:                                       # ...and CURRENT
        assert g.track_position == ts[g.seg_idx].track_position
    assert s.bdb.route_snapshot().hash                   # snapshot rewritten


def test_run_nuts_on_layer_repersists_detailed(tmp_path):
    # In detailed mode the rerun cascades into DNUTS; net rows must refresh too.
    s, _ = _fresh("source flow/rnr/mix_tracks.buda",
                  f"open_bdb {tmp_path / 'n.bdb'}",
                  "add_block A 0 0 200 400",
                  "add_block B 600 800 800 1200",
                  "add_bus d[8] A.p B.p",
                  "run_bundler", "generate_topologies", "run_planner 3",
                  "run_nuts", "run_detailed_nuts")
    s.bdb.clear_detailed_routing()
    assert not s.bdb.net_segments("1")
    layer_id = s.nuts_result.segments[0].layer
    layer_name = s._make_layer_names()[layer_id]
    _quiet(s, f"run_nuts_on_layer {layer_name}")
    assert s.bdb.net_segments("1")                       # detailed re-persisted
    snap = s.bdb.route_snapshot()
    assert snap.stage == "detailed_nuts"
    assert snap.n_net_segments == len(s.bdb.net_segments("1"))


def test_hier_detailed_persistence_flow_level(bdb_input):
    # hier_routed is detailed-routable: the hier flow persists net rows through
    # the real pipeline (no direct row writes needed — closes the coverage gap
    # noted when hier_mixed left every bit unplaced).
    s, _ = _fresh("source flow/rnr/mix_tracks.buda",
                  f"open_bdb {bdb_input('hier_routed')}",
                  "derive_busterms 1", "run_hier_bundler depth 1",
                  "generate_hier_topologies", "run_planner hier 3",
                  "run_nuts", "run_detailed_nuts")
    assert s.detailed_result.num_unplaced == 0
    assert s.detailed_result.net_vias                    # real per-bit vias
    reps = [b for b in s.bdb.all_bundles() if b.is_replicated]
    assert reps
    assert any(s.bdb.net_segments(b.id) for b in reps)   # expanded own net rows
    snap = s.bdb.route_snapshot()
    assert snap.stage == "detailed_nuts" and snap.n_net_vias >= 1


def test_hier_resume_to_dnuts_flow_level(bdb_input):
    # Full hier two-phase: checkpoint after run_nuts -> fresh session ->
    # load_pipeline expanded -> run_detailed_nuts reproduces the single-session
    # per-bit routing exactly.
    db = bdb_input("hier_routed")
    s1, _ = _fresh("source flow/rnr/mix_tracks.buda", f"open_bdb {db}",
                   "derive_busterms 1", "run_hier_bundler depth 1",
                   "generate_hier_topologies", "run_planner hier 3",
                   "run_nuts", "run_detailed_nuts")
    assert s1.detailed_result.num_unplaced == 0
    ref = sorted((n.bundle_id, n.seg_idx, n.bit_index, n.layer, n.track_position)
                 for n in s1.detailed_result.net_segments)
    ref_hash = s1.bdb.route_snapshot().hash
    # Roll the BDB back to the post-NUTS checkpoint (drop the detailed rows).
    s1.bdb.clear_detailed_routing()
    s1._persist_route_snapshot(s1.bdb.route_snapshot().n_bus_segments, 0,
                               "abstract_nuts")
    del s1

    s2, out = _fresh("source flow/rnr/mix_tracks.buda", f"open_bdb {db}",
                     "add_blocks_from_bdb 0", "add_blocks_from_bdb 1 skip",
                     "add_blocks_from_bdb 2 skip", "load_pipeline expanded")
    assert "placed bus segment(s)" in out
    _quiet(s2, "run_detailed_nuts")
    assert s2.detailed_result.num_unplaced == 0
    got = sorted((n.bundle_id, n.seg_idx, n.bit_index, n.layer, n.track_position)
                 for n in s2.detailed_result.net_segments)
    assert got == ref
    assert s2.bdb.route_snapshot().hash == ref_hash


def test_v10_db_migrates_to_v11(tmp_path):
    p = str(tmp_path / "v10.bdb")
    con = sqlite3.connect(p)
    con.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO meta VALUES('schema_version','10');
        PRAGMA user_version = 10;
        """
    )
    con.commit()
    con.close()

    db = buda.BDB(p)
    assert db.schema_version() == buda.BDB.SCHEMA_VERSION
    assert db.meta_get("schema_version") == str(buda.BDB.SCHEMA_VERSION)
    del db
    con = sqlite3.connect(p)
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert "topology_bridge_segment" in tables
