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

"""Stage-3 planner-output persistence into the BDB (schema v6).

`run_planner` records its decision: the selected topology (`topology.is_selected`)
and per-segment assigned layers (`topology_segment.assigned_layer`). For the hier
flow, `run_planner hier` expands bundles into per-instance wrappers; those are now
persisted as `is_replicated=1` bundle rows (parent_id = template) with their
selected topology, so `bus_segment` rows join back to a bundle.
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


def _flat_planned(bdb_path):
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "source flow/rnr/mix_tracks.buda",
           f"open_bdb {bdb_path}",
           "add_block A 0 0 200 400",
           "add_block B 600 800 800 1200",
           "add_bus d[8] A.p B.p",
           "run_bundler", "generate_topologies", "run_planner 3")
    return s


def _hier_planned(bdb_path):
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "source flow/rnr/mix_tracks.buda",
           f"open_bdb {bdb_path}",
           "derive_busterms 1",
           "run_hier_bundler depth 1",
           "generate_hier_topologies",
           "run_planner hier 3")
    return s


def test_flat_planner_records_selection_and_layers(tmp_path):
    s = _flat_planned(str(tmp_path / "flat.bdb"))
    w = s.bundles[0]
    bid = str(w.input.original_bundle.id)
    sel = w.plan.selected_topology_index
    # is_selected marks exactly the planner's chosen candidate.
    assert [t.cand_index for t in s.bdb.topologies(bid) if t.is_selected] == [sel]
    # assigned_layer on the selected topology's segments == the planner's seg_layers.
    segs = s.bdb.topology_segments(bid, sel)
    assert [sg.assigned_layer for sg in segs] == list(w.plan.seg_layers)
    # Non-selected candidates keep assigned_layer unset (-1).
    for t in s.bdb.topologies(bid):
        if t.cand_index != sel:
            assert all(sg.assigned_layer == -1
                       for sg in s.bdb.topology_segments(bid, t.cand_index))


def test_hier_expanded_bundles_persist(bdb_input):
    s = _hier_planned(bdb_input("hier_mixed"))
    allb = s.bdb.all_bundles()
    templates = [b for b in allb if not b.is_replicated]
    instances = [b for b in allb if b.is_replicated]
    assert templates and instances                       # both coexist
    # Each expanded instance links back to a template via parent_id.
    tpl_ids = {b.id for b in templates}
    for b in instances:
        assert b.parent_id in tpl_ids
        assert b.cell_context                            # instances come from cells
        # Its selected topology is persisted with assigned layers.
        sel_topos = [t for t in s.bdb.topologies(b.id) if t.is_selected]
        assert len(sel_topos) == 1
        segs = s.bdb.topology_segments(b.id, sel_topos[0].cand_index)
        assert segs and all(sg.assigned_layer >= 0 for sg in segs)


def test_hier_bus_segments_join_a_bundle_row(bdb_input):
    s = _hier_planned(bdb_input("hier_mixed"))
    _quiet(s, "run_nuts")
    bundle_ids = {b.id for b in s.bdb.all_bundles()}
    bus_ids = {sg.id for w in s.bundles
               for sg in s.bdb.bus_segments(str(w.input.original_bundle.id))}
    assert bus_ids and bus_ids <= bundle_ids             # every bus row joins a bundle


def test_repersist_is_idempotent(bdb_input):
    # Re-persisting the same planner state must not accumulate expanded rows —
    # clear_expanded_bundles() drops the prior is_replicated=1 rows first.
    s = _hier_planned(bdb_input("hier_mixed"))
    n1 = len([b for b in s.bdb.all_bundles() if b.is_replicated])
    with contextlib.redirect_stdout(io.StringIO()):
        s._persist_planner_output()                       # persist again, same state
    n2 = len([b for b in s.bdb.all_bundles() if b.is_replicated])
    assert n1 == n2 >= 1


def test_planner_output_roundtrip_through_sql(tmp_path):
    path = str(tmp_path / "flat.bdb")
    s = _flat_planned(path)
    bid = str(s.bundles[0].input.original_bundle.id)
    sel = s.bundles[0].plan.selected_topology_index
    before = [(sg.seg_index, sg.assigned_layer)
              for sg in s.bdb.topology_segments(bid, sel)]
    db2 = buda.BDB(bdb_serialize.load(
        (lambda p: (bdb_serialize.dump(path, p), p)[1])(str(tmp_path / "rt.sql")),
        str(tmp_path / "rebuilt.bdb")))
    after = [(sg.seg_index, sg.assigned_layer)
             for sg in db2.topology_segments(bid, sel)]
    assert after == before and before
    assert [t.cand_index for t in db2.topologies(bid) if t.is_selected] == [sel]


def test_flat_late_open_bdb_persists_as_normal_bundle(tmp_path):
    # Flat flow that opens the BDB AFTER run_bundler/generate_topologies: the
    # bundle is absent from the BDB at run_planner, but it must be persisted as a
    # NORMAL bundle (is_replicated=0, all candidates) — not misclassified as a hier
    # expanded instance (Codex #128 P2).
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "source flow/rnr/mix_tracks.buda",
           "add_block A 0 0 200 400",
           "add_block B 600 800 800 1200",
           "add_bus d[8] A.p B.p",
           "run_bundler", "generate_topologies",     # no BDB yet
           f"open_bdb {tmp_path / 'late.bdb'}",       # open late
           "run_planner 3")
    rows = s.bdb.all_bundles()
    assert len(rows) == 1 and not rows[0].is_replicated
    bid = rows[0].id
    # ALL candidates persisted (not just the selected one).
    assert len(s.bdb.topologies(bid)) == len(s.bundles[0].input.candidates)
    sel = s.bundles[0].plan.selected_topology_index
    assert [t.cand_index for t in s.bdb.topologies(bid) if t.is_selected] == [sel]


def test_replan_resets_stale_assigned_layers(tmp_path):
    # Re-planning onto a different candidate must clear the prior candidate's
    # assigned_layer rows — only the currently selected candidate carries layers
    # (Codex #128 P2).
    s = _flat_planned(str(tmp_path / "flat.bdb"))
    bid = str(s.bundles[0].input.original_bundle.id)
    ncand = len(s.bundles[0].input.candidates)
    assert ncand >= 2
    first = s.bundles[0].plan.selected_topology_index
    other = next(i for i in range(ncand) if i != first)
    _quiet(s, f"select_topology {bid} {other + 1}", "run_planner 3")  # pin a different candidate
    with_layers = {t.cand_index for t in s.bdb.topologies(bid)
                   if any(sg.assigned_layer >= 0
                          for sg in s.bdb.topology_segments(bid, t.cand_index))}
    selected = {t.cand_index for t in s.bdb.topologies(bid) if t.is_selected}
    assert with_layers <= selected           # no stale layers on non-selected candidates


def test_v5_db_migrates_to_v6_adds_assigned_layer(tmp_path):
    p = str(tmp_path / "v5.bdb")
    con = sqlite3.connect(p)
    con.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO meta VALUES('schema_version','5');
        PRAGMA user_version = 5;
        """
    )
    con.commit()
    con.close()

    db = buda.BDB(p)
    assert db.schema_version() == buda.BDB.SCHEMA_VERSION
    assert db.meta_get("schema_version") == str(buda.BDB.SCHEMA_VERSION)
    del db
    con = sqlite3.connect(p)
    cols = {r[1] for r in con.execute("PRAGMA table_info(topology_segment)")}
    con.close()
    assert "assigned_layer" in cols


# ── selective re-persist (chip_flow_parallelism.md C1) ───────────────────────
# The RE-persist sites (run_nuts escalation, _checkpoint_routing, the FK
# fallbacks) pass selective=True: only expanded bundles whose fingerprint
# (selected uid, seg_layers, lock, USER extras) changed are rewritten.  The
# contract is BYTE-IDENTITY with a full rewrite — verified through the
# serialized SQL dump (line-sorted: a selective rewrite reinserts a changed
# bundle's rows at new rowids, so physical order may differ; content not).

def _dump_sorted(session, tmp_path, tag):
    out = str(tmp_path / f"{tag}.sql")
    bdb_serialize.dump(session._bdb_open_path, out)
    return sorted(open(out).read().splitlines())


def test_selective_repersist_noop_skips_and_matches(bdb_input, tmp_path,
                                                    monkeypatch):
    s = _hier_planned(bdb_input("hier_mixed"))
    base = _dump_sorted(s, tmp_path, "base")
    calls = []
    orig = type(s)._add_expanded_bundle
    monkeypatch.setattr(type(s), "_add_expanded_bundle",
                        lambda self, *a, **k: (calls.append(1),
                                               orig(self, *a, **k))[1])
    with contextlib.redirect_stdout(io.StringIO()):
        s._persist_planner_output(selective=True)
    assert calls == []                       # nothing changed: zero rewrites
    assert _dump_sorted(s, tmp_path, "sel") == base


def test_selective_repersist_change_matches_full(bdb_input, tmp_path):
    s = _hier_planned(bdb_input("hier_mixed"))
    # Mutate ONE expanded instance's assigned layers.
    exp_ids = {ew.input.original_bundle.id
               for ws in s._hier_expansion_map.values() for ew in ws}
    w = next(w for w in s.bundles
             if w.input.original_bundle.id in exp_ids and w.plan.seg_layers)
    sl = list(w.plan.seg_layers)
    sl[0] = next(l for l in s.layers.get_layer_ids_by_dir(
        buda.LayerDir.HORIZONTAL) + s.layers.get_layer_ids_by_dir(
        buda.LayerDir.VERTICAL) if l != sl[0])
    w.plan.seg_layers = sl
    with contextlib.redirect_stdout(io.StringIO()):
        s._persist_planner_output(selective=True)
    sel_dump = _dump_sorted(s, tmp_path, "sel")
    # Force the historical full rewrite of the SAME state and compare.
    s._persisted_plan_fp = None
    with contextlib.redirect_stdout(io.StringIO()):
        s._persist_planner_output()
    assert _dump_sorted(s, tmp_path, "full") == sel_dump


def test_selective_falls_back_on_new_expansion(bdb_input, monkeypatch):
    # A memo whose bundle-id set mismatches (fresh expansion) must take the
    # full clear+rewrite path, never a partial skip.
    s = _hier_planned(bdb_input("hier_mixed"))
    s._persisted_plan_fp = {-999: ("x", (), False, ())}   # wrong id set
    cleared = []
    orig = type(s.bdb).clear_expanded_bundles
    monkeypatch.setattr(type(s.bdb), "clear_expanded_bundles",
                        lambda db: (cleared.append(1), orig(db))[1])
    with contextlib.redirect_stdout(io.StringIO()):
        s._persist_planner_output(selective=True)
    assert cleared                            # full path engaged
