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

"""Stage-2 candidate-topology persistence into the BDB (schema v4).

`generate_topologies` (flat) and `generate_hier_topologies` (hier) write ALL
candidate topologies — before run_planner — into the `topology` /
`topology_segment` tables, so a design's candidates are inspectable/tweakable up
front. Candidates round-trip through the diffable *.bdb.sql serialization.
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


def _flat_session(bdb_path):
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           f"open_bdb {bdb_path}",
           "source flow/rnr/mix_tracks.buda",
           "add_block A 0 0 200 400",
           "add_block B 1000 0 1200 400",
           "add_bus d[8] A.p B.p",
           "run_bundler",
           "generate_topologies")
    return s


def _hier_session(bdb_path):
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "source flow/rnr/mix_tracks.buda",
           f"open_bdb {bdb_path}",
           "derive_busterms 1",
           "run_hier_bundler depth 1",
           "generate_hier_topologies")
    return s


def test_flat_topologies_persist_before_planner(tmp_path):
    s = _flat_session(str(tmp_path / "flat.bdb"))
    w = s.bundles[0]
    bid = str(w.input.original_bundle.id)
    topos = s.bdb.topologies(bid)
    # Every in-memory candidate is persisted, in order.
    assert len(topos) == len(w.input.candidates) >= 1
    assert [t.cand_index for t in topos] == list(range(len(topos)))
    assert [t.type for t in topos] == [c.type for c in w.input.candidates]
    # No selection yet (planner has not run).
    assert all(not t.is_selected for t in topos)
    # Segments match the in-memory geometry for candidate 0.
    segs = s.bdb.topology_segments(bid, 0)
    cand0 = w.input.candidates[0]
    assert len(segs) == len(cand0.segments)
    for sr, seg in zip(segs, cand0.segments):
        assert (sr.x1, sr.y1, sr.x2, sr.y2) == (seg.start.x, seg.start.y,
                                                seg.end.x, seg.end.y)
        assert sr.layer_hint == seg.layer_hint


def test_hier_topologies_persist(bdb_input):
    s = _hier_session(bdb_input("hier_mixed"))
    total = sum(len(s.bdb.topologies(b.id)) for b in s.bdb.all_bundles())
    in_mem = sum(len(w.input.candidates) for w in s.bundles)
    assert total == in_mem >= 1


def test_topologies_roundtrip_through_sql(tmp_path):
    path = str(tmp_path / "flat.bdb")
    s = _flat_session(path)
    bid = str(s.bundles[0].input.original_bundle.id)
    before = [(t.cand_index, t.type, t.wirelength,
               tuple((x.x1, x.y1, x.x2, x.y2, x.layer_hint)
                     for x in s.bdb.topology_segments(bid, t.cand_index)))
              for t in s.bdb.topologies(bid)]
    assert before

    sql = str(tmp_path / "rt.bdb.sql")
    bdb_serialize.dump(path, sql)
    rebuilt = bdb_serialize.load(sql, str(tmp_path / "rebuilt.bdb"))
    db2 = buda.BDB(rebuilt)
    after = [(t.cand_index, t.type, t.wirelength,
              tuple((x.x1, x.y1, x.x2, x.y2, x.layer_hint)
                    for x in db2.topology_segments(bid, t.cand_index)))
             for t in db2.topologies(bid)]
    assert after == before


def test_regenerate_replaces_topologies(tmp_path):
    s = _flat_session(str(tmp_path / "flat.bdb"))
    bid = str(s.bundles[0].input.original_bundle.id)
    n1 = len(s.bdb.topologies(bid))
    _quiet(s, "generate_topologies")          # regenerate
    n2 = len(s.bdb.topologies(bid))
    assert n1 == n2 >= 1                        # clear_topologies prevents dupes


def test_rerun_bundler_clears_stale_topologies(tmp_path):
    s = _flat_session(str(tmp_path / "flat.bdb"))
    bid = str(s.bundles[0].input.original_bundle.id)
    assert s.bdb.topologies(bid)               # topologies exist
    _quiet(s, "run_bundler")                   # re-bundle wipes bundles + topologies
    assert s.bdb.topologies(bid) == []         # stale topologies gone


def test_generate_topologies_without_bdb_is_noop():
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "source flow/rnr/mix_tracks.buda",
           "add_block A 0 0 200 400",
           "add_block B 1000 0 1200 400",
           "add_bus d[8] A.p B.p",
           "run_bundler",
           "generate_topologies")
    assert s.bundles[0].input.candidates       # candidates exist in memory
    assert s._persist_topologies() == 0        # no BDB → no-op, no crash


def test_generate_after_late_open_bdb_persists(tmp_path):
    # run_bundler BEFORE open_bdb: bundles weren't persisted at bundler time, so
    # _persist_topologies must first sync the FK-parent bundle rows, else the
    # topology.bundle_id FK silently rejects every insert (Codex #126 P2).
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "source flow/rnr/mix_tracks.buda",
           "add_block A 0 0 200 400",
           "add_block B 1000 0 1200 400",
           "add_bus d[8] A.p B.p",
           "run_bundler",                                  # no BDB yet
           f"open_bdb {tmp_path / 'late.bdb'}",            # open AFTER bundling
           "generate_topologies")
    bid = str(s.bundles[0].input.original_bundle.id)
    assert len(s.bdb.all_bundles()) == 1                    # bundle row synced
    assert len(s.bdb.topologies(bid)) >= 1                  # topologies actually landed


def test_select_topology_refreshes_is_selected(tmp_path):
    # Pinning a candidate before run_planner must update is_selected in the BDB,
    # not just the in-memory index (Codex #126 P2).
    s = _flat_session(str(tmp_path / "flat.bdb"))
    bid = str(s.bundles[0].input.original_bundle.id)
    assert not any(t.is_selected for t in s.bdb.topologies(bid))   # none pinned yet
    _quiet(s, f"select_topology {bid} 2")                   # pin 1-based #2 -> index 1
    selected = [t.cand_index for t in s.bdb.topologies(bid) if t.is_selected]
    assert selected == [1]


_NO_LO, _NO_HI = -2147483648, 2147483647   # Segment perp_clamp INT_MIN/INT_MAX sentinels


def _overlap_session(bdb_path):
    """Overlapping endpoint blocks A/B ('/' stagger) + an 8-bit bus → the
    corner-wrapping U_OVL/UU_OVL candidates carry per-segment perp clamps.  Opened
    on a BDB so generate_topologies checkpoints them (with clamps, v16)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           f"open_bdb {bdb_path}",
           "def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
           "add_block A 0 0 400 400", "add_block B 200 200 600 600",
           "add_bus n[8] A.tx B.rx",
           "run_bundler strict",
           "generate_topologies double_detour")
    return s


def test_overlap_u_perp_clamp_persists_and_roundtrips(tmp_path):
    """v16: the overlap-U perp clamps are written to topology_segment and survive
    the *.bdb.sql round-trip.  The in-memory clamp of every U_OVL/UU_OVL segment
    equals its persisted row, and at least one segment per candidate is clamped."""
    path = str(tmp_path / "ovl.bdb")
    s = _overlap_session(path)
    w = s.bundles[0]
    bid = str(w.input.original_bundle.id)
    ovl = [(i, c) for i, c in enumerate(w.input.candidates)
           if c.type.startswith(("U_OVL", "UU_OVL"))]
    assert ovl, [c.type for c in w.input.candidates]
    for ci, cand in ovl:
        rows = s.bdb.topology_segments(bid, ci)
        assert len(rows) == len(cand.segments)
        assert any(r.perp_clamp_lo != _NO_LO or r.perp_clamp_hi != _NO_HI for r in rows), \
            f"{cand.type}: no persisted perp clamp"
        for r, seg in zip(rows, cand.segments):
            assert (r.perp_clamp_lo, r.perp_clamp_hi) == \
                   (seg.perp_clamp_lo, seg.perp_clamp_hi), f"{cand.type} seg {r.seg_index}"

    before = {(t.cand_index, x.seg_index): (x.perp_clamp_lo, x.perp_clamp_hi)
              for t in s.bdb.topologies(bid)
              for x in s.bdb.topology_segments(bid, t.cand_index)}
    sqlp = str(tmp_path / "ovl.bdb.sql")
    bdb_serialize.dump(path, sqlp)
    rebuilt = bdb_serialize.load(sqlp, str(tmp_path / "ovl_rebuilt.bdb"))
    db2 = buda.BDB(rebuilt)
    after = {(t.cand_index, x.seg_index): (x.perp_clamp_lo, x.perp_clamp_hi)
             for t in db2.topologies(bid)
             for x in db2.topology_segments(bid, t.cand_index)}
    assert after == before


def test_overlap_u_perp_clamp_survives_load_pipeline_resume(tmp_path):
    """The clamp is load-bearing: a U_OVL checkpointed and resumed via load_pipeline
    BEFORE NUTS must reload clamped, so its face-tap arm still cannot slide into B.
    Pre-v16 the reloaded segment came back unclamped (window = A's full face) and
    could collapse through B."""
    path = str(tmp_path / "ovl_resume.bdb")
    _overlap_session(path)                                  # checkpoints to the BDB
    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    _quiet(s2,
           f"open_bdb {path}",
           "def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
           "add_block A 0 0 400 400", "add_block B 200 200 600 600",
           "load_pipeline")
    w = s2.bundles[0]
    hvh = next(c for c in w.input.candidates if c.type == "U_OVL_HVH")
    ct = buda.ConnTopology(); ct.build(hvh, s2.fp)
    seg0 = ct.segs()[0]                                     # A-tap H arm, below B
    assert seg0.perp_hi > seg0.perp_lo
    assert seg0.perp_lo >= 0 and seg0.perp_hi <= 200, (
        f"reloaded U_OVL_HVH tap arm window [{seg0.perp_lo},{seg0.perp_hi}] escapes "
        "A's exclusive band [0,200] — perp clamp lost on resume")


def test_v3_db_migrates_to_v4_adds_topology_tables(tmp_path):
    p = str(tmp_path / "v3.bdb")
    con = sqlite3.connect(p)
    con.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO meta VALUES('schema_version','3');
        PRAGMA user_version = 3;
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
    assert {"topology", "topology_segment"} <= tables   # v4 step ran
