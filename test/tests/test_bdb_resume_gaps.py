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

import pytest

# Moved to the mid tier: full-pipeline / BDB round-trip / interchange
# integration (keeps the fast tier < 10s). See
# docs/internal/test_runtime_analysis.md.
pytestmark = pytest.mark.mid

import contextlib
import io
import sqlite3

import buda

from test_bdb_resume import _fresh, _quiet


# Pure-TEG block: two disjoint rects with an x-gap; a source above the gap makes
# the V-trunk candidates fall in it.  Since open 1(a) the gap connection is
# ordinary per-rect stubs joined through the trunk — generation emits NO
# bridge_segments — but the v11 `topology_bridge_segment` table and its
# load_pipeline restore stay: a PRE-CHANGE checkpoint still holds bridge rows,
# and dropping them on load would silently change the restored candidate's
# identity (topo_uid hashes bridges) and hide the unrealized bridge from the
# TEG_OPEN audit's "unrealized vs absent" wording.
TEG_SETUP = ("source flow/rnr/mix_tracks.buda",
             "add_block T rect 0 0 200 400 rect 500 0 700 400 teg_mode over",
             "add_block src 300 800 400 900",
             "add_bus d[4] src.tx T.rx")


def _bridges(w):
    return [{name: (seg.start.x, seg.start.y, seg.end.x, seg.end.y,
                    seg.layer_hint, seg.is_jog)
             for name, seg in t.bridge_segments.items()}
            for t in w.input.candidates]


def test_generation_persists_no_bridge_rows(tmp_path):
    # Open 1(a): the TEG connection metal is ordinary segments, so a freshly
    # generated checkpoint holds ZERO bridge rows (and the in-memory maps are
    # empty) — the table exists solely for pre-change checkpoints.
    s, _ = _fresh(*TEG_SETUP, f"open_bdb {tmp_path / 't.bdb'}",
                  "run_bundler", "generate_topologies")
    w = s.bundles[0]
    assert not any(_bridges(w)), "generation must no longer emit bridges"
    for ci in range(len(w.input.candidates)):
        assert not s.bdb.topology_bridges("1", ci)


def test_pre_change_bridge_rows_survive_resume(tmp_path):
    # A PRE-CHANGE checkpoint's bridge rows (emulated by injecting rows the
    # old generation would have written — add_topology_bridge is the same v11
    # codec) must still restore into Topology.bridge_segments on
    # load_pipeline, so the restored candidate keeps its recorded content and
    # the TEG_OPEN audit can report the bridge as "unrealized".
    db = str(tmp_path / "t.bdb")
    s1, _ = _fresh(*TEG_SETUP, f"open_bdb {db}",
                   "run_bundler", "generate_topologies")
    n_cands = len(s1.bundles[0].input.candidates)
    assert n_cands >= 1
    r = buda.TopoBridgeRow()
    r.id = "1"
    r.cand_index = 0
    r.block_name = "T"
    r.x1, r.y1, r.x2, r.y2 = 0, 400, 700, 400      # the old union-face shape
    r.layer_hint = 4
    r.is_jog = False
    s1.bdb.add_topology_bridge(r)
    del s1

    s2, out = _fresh("source flow/rnr/mix_tracks.buda",
                     "add_block T rect 0 0 200 400 rect 500 0 700 400 teg_mode over",
                     "add_block src 300 800 400 900",
                     f"open_bdb {db}", "load_pipeline")
    assert "rehydrated" in out
    mem = _bridges(s2.bundles[0])
    assert mem[0] == {"T": (0, 400, 700, 400, 4, False)}
    assert not any(mem[1:])


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


def test_expanded_bundle_bridge_roundtrip(bdb_input):
    # The hier expanded persist path (_add_expanded_bundle) writes bridges via
    # the same _persist_topology_annotations choke point, and `load_pipeline
    # expanded` restores them with the compact-index remap. The hier flow
    # cannot yet PRODUCE bridges (cell-local floorplans build single-rect
    # blocks only — flat multi-rect TEG blocks are the only producer), so the
    # bridge is injected into an expanded instance's selected candidate: this
    # exercises exactly the persist/restore code a hier TEG design would use.
    db = bdb_input("hier_mixed")
    s1, _ = _fresh("source flow/rnr/mix_tracks.buda", f"open_bdb {db}",
                   "derive_busterms 1", "run_hier_bundler depth 1",
                   "generate_hier_topologies", "run_planner hier 3")
    rep_ids = {b.id for b in s1.bdb.all_bundles() if b.is_replicated}
    w = next(w for w in s1.bundles
             if str(w.input.original_bundle.id) in rep_ids)
    bid = str(w.input.original_bundle.id)
    sel = w.plan.selected_topology_index
    # Inject a bridge into the selected candidate (reassign: pybind copies).
    cands = list(w.input.candidates)
    topo = cands[sel]
    seg = buda.Segment()
    seg.start = buda.Point(700, 0)
    seg.end = buda.Point(700, 400)
    seg.layer_hint = 7
    topo.bridge_segments = {"proc_a/tegblk": seg}
    cands[sel] = topo
    w.input.candidates = cands
    with contextlib.redirect_stdout(io.StringIO()):
        s1._persist_planner_output()          # expanded re-persist incl. bridge
    rows = s1.bdb.topology_bridges(bid, sel)
    assert [(r.block_name, r.x1, r.y1, r.x2, r.y2, r.layer_hint)
            for r in rows] == [("proc_a/tegblk", 700, 0, 700, 400, 7)]
    del s1

    s2, _ = _fresh("source flow/rnr/mix_tracks.buda", f"open_bdb {db}",
                   "add_blocks_from_bdb 0", "add_blocks_from_bdb 1 skip",
                   "add_blocks_from_bdb 2 skip", "load_pipeline expanded")
    w2 = next(w for w in s2.bundles if str(w.input.original_bundle.id) == bid)
    t2 = w2.input.candidates[w2.plan.selected_topology_index]
    got = {n: (sg.start.x, sg.start.y, sg.end.x, sg.end.y, sg.layer_hint)
           for n, sg in t2.bridge_segments.items()}
    assert got == {"proc_a/tegblk": (700, 0, 700, 400, 7)}


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
