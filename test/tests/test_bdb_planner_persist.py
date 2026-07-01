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
    assert db.schema_version() == 6
    assert db.meta_get("schema_version") == "6"
    del db
    con = sqlite3.connect(p)
    cols = {r[1] for r in con.execute("PRAGMA table_info(topology_segment)")}
    con.close()
    assert "assigned_layer" in cols
