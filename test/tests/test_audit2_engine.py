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
# See the License for the specific language governing permissions and
# limitations under the License.

"""Engine/pipeline regressions confirmed by the 2026-07 audit's adversarial
verification pass (slice 1: silent-corruption + crash class)."""
import math
import sqlite3

import pytest

import buda
import buda_db


# ── C6-06: NULL TEXT columns must not crash the readers ──────────────────────

def test_all_components_survives_null_cell(tmp_path):
    # Audit C6-06: reading component.cell / meta.value directly from
    # sqlite3_column_text constructs std::string(nullptr) on a NULL — UB /
    # crash. A hand-edited fixture with a NULL cell must read as "".
    path = str(tmp_path / "nullcell.bdb")
    buda_db.BDB(path)                       # create schema
    con = sqlite3.connect(path)
    con.execute("INSERT INTO component(name,depth,x1,y1,x2,y2)"
                " VALUES('blkA',0,0,0,10,10)")           # cell omitted -> NULL
    con.execute("INSERT INTO meta(key,value) VALUES('dangling',NULL)")
    con.commit()
    con.close()
    db = buda_db.BDB(path)
    rows = db.all_components()
    assert len(rows) == 1 and rows[0].cell == ""
    assert db.meta_get("dangling", "def") == ""    # NULL -> "", not a crash


# ── C6-05: v<2 migration must not destroy a modern DB reading user_version 0 ──

def test_v0_reopen_preserves_modern_bundle_rows(tmp_path):
    # Audit C6-05: the v<2 migration DROPped the bundle tables assuming they
    # were empty legacy shape; a modern-schema DB whose user_version reads 0
    # (e.g. a serialize round-trip that lost the pragma) had its bundle rows
    # silently destroyed / became unopenable.
    path = str(tmp_path / "v0.bdb")
    buda_db.BDB(path)                       # fresh modern schema
    con = sqlite3.connect(path)
    con.execute("INSERT INTO bundle(id,level,strategy,reason,num_terminals)"
                " VALUES('b0',0,'STRICT','probe',2)")
    con.execute("PRAGMA user_version = 0")   # simulate the lost-pragma case
    con.commit()
    con.close()
    db = buda_db.BDB(path)                   # migrate on reopen
    ids = {b.id for b in db.all_bundles()}
    assert "b0" in ids, "v<2 migration destroyed a modern bundle row"


# ── C6-07: add_net_pins nets must get a net_props row ────────────────────────

def test_add_net_pins_creates_net_props_row(tmp_path):
    # Audit C6-07: nets from add_net_pins* had no net_props row, so
    # compute_hpwl/fanout and nets_by_hpwl silently ignored them (unlike
    # DEF/Verilog/label nets, which insert the row).
    path = str(tmp_path / "np.bdb")
    db = buda_db.BDB(path)
    db.set_die(1000, 1000)
    db.add_cell("CELLA", 10, 10)
    db.add_cell_pin("CELLA", "P", "OUTPUT", 5, 5)
    db.add_cell("CELLB", 10, 10)
    db.add_cell_pin("CELLB", "P", "INPUT", 5, 5)
    db.add_comp("u1", "CELLA", "", 1, 1, 11, 11)
    db.add_comp("u2", "CELLB", "", 101, 101, 111, 111)
    db.add_net_pins("n_probe", "u1.P", ["u2.P"])
    db.compute_hpwl()
    con = sqlite3.connect(path)
    nrows = con.execute("SELECT COUNT(*) FROM net").fetchone()[0]
    prows = con.execute("SELECT COUNT(*) FROM net_props").fetchone()[0]
    con.close()
    assert nrows >= 1 and prows >= 1, \
        "add_net_pins net has no net_props row — invisible to HPWL/fanout"
    assert db.nets_by_hpwl(0.0, 1e9), "net absent from nets_by_hpwl"


# ── C6-08: move_comp must shift the subtree's pins (no stale-pin HPWL) ────────

def test_move_comp_shifts_pins(tmp_path):
    # Audit C6-08: move_comp rewrote the bbox but left pin.px/py stale, then
    # compute_hpwl() recomputed from the stale pins — refreshed-looking but
    # wrong. A pure move must carry the (absolute) pins along, like
    # translate_comp.
    db = buda_db.BDB(":memory:")             # in-memory: no WAL cross-conn skew
    db.add_cell("CC", 10, 10)
    db.add_cell_pin("CC", "o", "OUTPUT", 1, 1)
    db.add_cell_pin("CC", "i", "INPUT", 1, 1)
    db.add_comp("u1", "CC", "", 100, 100, 110, 110)
    db.add_comp("u2", "CC", "", 300, 100, 310, 110)
    db.add_net_pins("n1", "u1.o", ["u2.i"])   # creates u1's pin row

    def _u1_pin():
        cid = {c.name: c.id for c in db.all_components()}["u1"]
        p = db.pins_by_comp(cid)[0]
        return (p.px, p.py)

    px0, py0 = _u1_pin()
    db.move_comp("u1", 500, 300)             # dx=400, dy=200
    px1, py1 = _u1_pin()
    assert (px1, py1) == (px0 + 400, py0 + 200), \
        f"move_comp left the pin stale: {(px0, py0)} -> {(px1, py1)}"


# ── C10-04: multi-rect busterm coords must round-trip past 1e6 ────────────────

def test_multirect_busterm_coords_round_trip_past_1e6(tmp_path):
    # Audit C10-04: encode_rects_json used %g (6 sig figs), corrupting any
    # multi-rect busterm coordinate >= 1e6 on the persist/reload round-trip
    # (1234567 -> "1.23457e+06" -> 1234570). %.17g is round-trip-exact.
    # Driven through the real busterm persist path (persist/load_seg_busterms).
    O = 1234567                               # 7-significant-digit coords
    db = buda.BDB(str(tmp_path / "c1004.bdb"))
    fp = buda.Floorplan()
    fp.add_block_rects("bt1", [(O + 0, O + 600, O + 300, O + 800),
                               (O + 0, O + 200, O + 200, O + 400)],
                       buda.TegMode.OVER)
    fp.add_block("bt2", O + 400, O + 0, O + 600, O + 100)
    t = buda.TopologyGenerator(fp).generate_candidates("bt2", ["bt1"])[0]
    # Confirm a multi-rect tap with a >=1e6 coordinate is present.
    orig = {si: [(r.x1, r.y1, r.x2, r.y2) for r in bt.rects]
            for si, eps in t.seg_busterms.items() for bt in eps
            if bt is not None and len(bt.rects) > 1}
    assert orig, "fixture must produce a multi-rect busterm tap"

    row = buda.BundleRow()
    row.id = "1"
    db.add_bundle(row)
    tr = buda.TopoRow()
    tr.id = "1"
    tr.cand_index = 0
    tr.type = t.type
    tr.topo_uid = buda.topo_uid(t)
    db.add_topology(tr)
    buda.persist_seg_busterms(db, "1", 0, t)

    r = buda.Topology()
    r.type = t.type
    r.segments = list(t.segments)
    buda.load_seg_busterms(db, "1", 0, r)
    back = {si: [(bt.x1, bt.y1, bt.x2, bt.y2) for bt in b.rects]
            for si, eps in r.seg_busterms.items() for b in eps
            if b is not None and len(b.rects) > 1}
    assert back == orig, \
        f"multi-rect coords corrupted on round-trip: {orig} -> {back}"


# ── C1-02: NUTS extract guards a too-large selected_topology_index ───────────

def test_nuts_out_of_range_selection_does_not_crash():
    # Audit C1-02: extract_segments / build_nuts_maps indexed
    # candidates[selected_topology_index] guarding only < 0 and non-empty —
    # an index >= size() is an out-of-bounds vector read (UB). It must be
    # guarded like the sibling sites (skipped, not dereferenced).
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 100)
    fp.add_block("B", 300, 0, 400, 100)
    tg = buda.TopologyGenerator(fp)
    tg.set_layer_ids(4, 5)
    w = buda.BundleWrapper()
    w.input.candidates = tg.generate_candidates("A", ["B"])
    w.plan.selected_topology_index = 999      # far out of range
    layers = buda.LayerStack()
    layers.add_layer(4, "M4", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    layers.add_layer(5, "M5", buda.LayerDir.VERTICAL, buda.LayerType.TOP)
    nuts = buda.NUTSEngine(fp, layers)
    r = nuts.run([w])                         # must not segfault (UB pre-fix)
    assert r is not None


# ── C3-03: injected-demand records must not survive a cut rebuild ────────────

def test_injected_demand_cleared_on_cut_rebuild():
    # Audit C3-03: inject_band_demand records key cuts by index; a
    # build_congestion_map() rebuild reorders/resizes cuts_, so a later
    # clear_injected_demand subtracted the stale demand from a freshly-zeroed
    # (or differently-sized) cut — negative/mischarged usage, or an
    # out-of-bounds band write. The rebuild must drop the stale records.
    layers = buda.LayerStack()
    layers.add_layer(4, "M4", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    layers.add_layer(5, "M5", buda.LayerDir.VERTICAL, buda.LayerType.TOP)
    fp = buda.Floorplan()
    for i, x in enumerate([0, 100, 200, 300, 400, 500]):
        fp.add_block(f"bx{i}", x, 0, x + 100, 200)
    pl = buda.CongestionPlanner(fp, layers)
    pl.build_congestion_map()
    pl.inject_band_demand(5, 0.0, 100.0, 540.0, 560.0, 7.0)
    pl.build_congestion_map()                 # rebuild — records now stale
    pl.clear_injected_demand()                # must be a no-op, not a mischarge
    for c in pl.get_cuts():
        for u in c.band_usage:
            assert u >= -1e-9, \
                f"stale injected record mischarged a rebuilt cut: usage={u}"


# ── P4-03: post_nuts re-solve must refresh (not strand) the detailed route ───

@pytest.mark.mid
def test_post_nuts_planner_refreshes_detailed_result():
    # Audit P4-03: run_planner post_nuts replaced nuts_result with a fresh
    # abstract solve but left the detailed_result from the PREVIOUS solve
    # live — a detailed route inconsistent with the new bus placement. It
    # must re-run detailed NUTS (as _rerun_all does).
    import contextlib
    import io

    import buda_cli
    s = buda_cli.BudaSession()
    s.no_viz = True

    def _q(*cmds):
        with contextlib.redirect_stdout(io.StringIO()):
            for c in cmds:
                s.do_command(c)

    _q("def_layer 3 M3 H 20", "def_layer 4 M4 H TOP 20",
       "def_layer 5 M5 V 20", "def_layer 6 M6 V TOP 20",
       "def_track_pattern 3 0 SIGNAL 1 1", "def_track_pattern 4 0 SIGNAL 1 1",
       "def_track_pattern 5 0 SIGNAL 1 1", "def_track_pattern 6 0 SIGNAL 1 1",
       "add_block A 0 0 100 100", "add_block B 500 0 600 100",
       "add_bus d[4] A.o B.i", "run_bundler STRICT", "generate_topologies",
       "run_planner 5", "run_nuts", "run_detailed_nuts")
    before_id = id(s.detailed_result)
    _q("run_planner post_nuts V 10 10000 H 10 10000")
    # Pre-fix, post_nuts replaced nuts_result but left the SAME detailed_result
    # object untouched (stale); the fix re-runs detailed NUTS, producing a
    # fresh result object.
    assert s.detailed_result is not None
    assert id(s.detailed_result) != before_id, \
        "post_nuts left the stale detailed_result — it must re-run DNUTS"
    # And the fresh detail is consistent with the post_nuts bus solve.
    bus_layer = {(t.bundle_id, t.seg_idx): t.layer
                 for t in s.nuts_result.segments}
    for n in s.detailed_result.net_segments:
        assert bus_layer.get((n.bundle_id, n.seg_idx)) == n.layer, \
            "detailed route layer disagrees with the post_nuts bus solve"


# ── P2-01: a failed local solve must not poison the fixed-segment cache ───────

def test_bottom_up_fixed_cache_not_poisoned_on_exception():
    # Audit P2-01: _bottom_up_fixed_segments published self._bu_fixed_cache =
    # fixed BEFORE filling it, so an exception during the local solve left a
    # permanently-cached PARTIAL/empty list every later call returned. The
    # wrapper must reset the cache to None on failure so a retry recomputes.
    import buda_cli
    s = buda_cli.BudaSession()
    s.no_viz = True
    s._bu_fixed_cache = None
    boom = RuntimeError("local solve blew up")

    def _raise():
        raise boom
    s._bottom_up_fixed_segments_compute = _raise
    with pytest.raises(RuntimeError):
        s._bottom_up_fixed_segments()
    assert getattr(s, "_bu_fixed_cache", "SENTINEL") is None, \
        "a failed local solve poisoned the fixed-segment cache"
    # A subsequent successful compute is cached normally.
    s._bottom_up_fixed_segments_compute = lambda: ["ok"]
    assert s._bottom_up_fixed_segments() == ["ok"]
    assert s._bu_fixed_cache == ["ok"]
