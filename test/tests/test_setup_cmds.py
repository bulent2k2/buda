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

"""Setup-command regressions from the 2026-07 audit (P5-01, P5-02) — the
first direct coverage of the setup_cmds wrapper layer."""
import contextlib
import io
import pathlib

import pytest

import buda_cli


def _session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    return s


def _run(s, cmd):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command(cmd)
    return buf.getvalue()


def test_def_layer_rejects_bad_direction_token():
    # Audit P5-01: any token that was not exactly 'H' silently became
    # VERTICAL (typos, swapped args). Must fail fast instead.
    s = _session()
    out = _run(s, "def_layer 4 M4 X TOP 44.44")
    assert "Error" in out and "H or V" in out
    assert s.layers.get_layer_ids_by_dir(__import__("buda").LayerDir.VERTICAL) == []


def test_def_layer_accepts_lowercase_direction():
    s = _session()
    _run(s, "def_layer 4 M4 h TOP 44.44")
    import buda
    assert 4 in list(s.layers.get_layer_ids_by_dir(buda.LayerDir.HORIZONTAL))


def test_add_bus_descending_range_creates_all_nets():
    # Audit P5-02: bus[7:0] made range(lo, hi+1) empty -> ZERO nets created,
    # silently. The Verilog-style descending range must normalize to 0..7.
    s = _session()
    _run(s, "add_block A 0 0 100 100")
    _run(s, "add_block B 300 0 400 100")
    _run(s, "add_bus w[7:0] A.o B.i")
    names = {n for n in s._net_endpoints if n.startswith("w_")}
    assert names == {f"w_{i}" for i in range(8)}, names


@pytest.mark.mid
def test_select_topology_pins_expanded_hier_template():
    # Audit P3-02: after run_planner hier, pinning a TEMPLATE bundle id goes
    # through the expansion-map fallback, which read wrappers[0].candidates —
    # an attribute BundleWrapper does not have (the pool is .input.candidates)
    # — so the whole pin-all-instances feature crashed with AttributeError
    # before any pin was applied.
    s = _session()
    flow = pathlib.Path("flow/hbundles/01_pipeline_hier.buda")
    for line in flow.read_text().splitlines():
        c = line.split("#", 1)[0].strip()
        if not c or c.startswith("visualize"):
            continue
        c = c.replace("source ../", "source flow/")
        _run(s, c)
        if c.startswith("run_planner hier"):
            break
    tmpl_ids = list(s._hier_expansion_map)
    assert tmpl_ids, "expected expanded template bundles"
    bid = tmpl_ids[0]
    out = _run(s, f"select_topology {bid} 1")
    assert "Pinned bundle" in out, out
    for w in s._hier_expansion_map[bid]:
        assert w.input.topology_pinned
        assert w.plan.selected_topology_index == 0


@pytest.mark.mid
def test_check_template_tracks_subset_template_reports_not_crashes():
    # Audit P1-02: a bottom-up cell with two cell-local templates covering
    # DIFFERENT instance subsets (a bus existing only in some occurrences)
    # crashed check_template_tracks with a raw StopIteration — and the
    # partially-built verdict then silently DISABLED the bottom-up track
    # gate, letting run_detailed_nuts copy without any alignment check. The
    # subset group must instead be reported misaligned (no comparison base)
    # and the default stop policy must refuse DNUTS loudly.
    import buda
    db = buda.BDB(":memory:")
    db.add_cell("proc_cell", 420, 200)
    db.add_cell("pipe_cell", 110, 80)
    db.add_inst_to_cell("proc_cell", "pa_i", "pipe_cell", 20, 60)
    db.add_inst_to_cell("proc_cell", "pb_i", "pipe_cell", 155, 60)
    for j, x in enumerate((0, 500, 1000), 1):
        db.add_inst(f"proc_i{j}", "proc_cell", "", x, 0)
    for i in range(4):
        for inst in ("proc_i1", "proc_i2", "proc_i3"):
            db.add_net_pins(f"ab_{inst}_{i}", f"{inst}/pa_i.out",
                            [f"{inst}/pb_i.in"])
        for inst in ("proc_i2", "proc_i3"):     # subset bus: not in proc_i1
            db.add_net_pins(f"cd_{inst}_{i}", f"{inst}/pb_i.out2",
                            [f"{inst}/pa_i.in2"])
    import buda_db
    buda_db.BustermGen(db).derive(1)

    s = _session()
    s.bdb = db
    for c in ("def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
              "def_layer 4 M4 H 50", "def_layer 5 M5 V 50",
              "def_track_pattern 6 0 SIGNAL 1 1",
              "def_track_pattern 7 0 SIGNAL 1 1",
              "def_track_pattern 4 0 SIGNAL 1 1",
              "def_track_pattern 5 0 SIGNAL 1 1",
              "set_bottom_up proc_cell", "run_hier_bundler",
              "generate_hier_topologies", "run_planner hier", "run_nuts"):
        _run(s, c)
    out = _run(s, "check_template_tracks")          # crashed pre-fix
    assert "MISALIGNED" in out and "no comparison base" in out, out
    out = _run(s, "run_detailed_nuts")
    assert "Error" in out and "bottom-up instance" in out, \
        "stop policy must refuse DNUTS on the subset-template report"


@pytest.mark.mid
def test_rerun_all_preserves_bottom_up_fixed_copies():
    # Audit P4-02: the explorer Re-run path (_rerun_all) built a fresh
    # NUTSEngine WITHOUT _inject_bottom_up_fixed while its sibling paths
    # (post_nuts, run_nuts_on_layer) inject — so a re-run on a bottom-up
    # design re-solved without the frozen template copies and could route
    # through or shift the frozen interconnect under contention.
    # NOTE: on this small uncontended fixture the unfixed code happens to
    # re-place identically, so this test is an invariant CANARY (uniform
    # replacement + no overlap growth), not a discriminating repro; the fix
    # itself restores the documented inject-on-every-engine contract that
    # both sibling paths follow.
    import buda
    import buda_db
    db = buda.BDB(":memory:")
    db.add_cell("proc_cell", 420, 200)
    db.add_cell("pipe_cell", 110, 80)
    db.add_inst_to_cell("proc_cell", "pa_i", "pipe_cell", 20, 60)
    db.add_inst_to_cell("proc_cell", "pb_i", "pipe_cell", 155, 60)
    for j, x in enumerate((0, 500), 1):
        db.add_inst(f"proc_i{j}", "proc_cell", "", x, 0)
    for i in range(4):
        for inst in ("proc_i1", "proc_i2"):
            db.add_net_pins(f"ab_{inst}_{i}", f"{inst}/pa_i.out",
                            [f"{inst}/pb_i.in"])
    buda_db.BustermGen(db).derive(1)
    s = _session()
    s.bdb = db
    for c in ("def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
              "def_layer 4 M4 H 50", "def_layer 5 M5 V 50",
              "def_track_pattern 6 0 SIGNAL 1 1",
              "def_track_pattern 7 0 SIGNAL 1 1",
              "def_track_pattern 4 0 SIGNAL 1 1",
              "def_track_pattern 5 0 SIGNAL 1 1",
              "set_bottom_up proc_cell", "run_hier_bundler",
              "generate_hier_topologies", "run_planner hier", "run_nuts"):
        _run(s, c)
    fixed_before = {(t.bundle_id, t.seg_idx, t.layer, t.track_position)
                    for t in s.nuts_result.segments}
    n_ovl_before = s.nuts_result.num_overlaps
    s._rerun_all()
    fixed_after = {(t.bundle_id, t.seg_idx, t.layer, t.track_position)
                   for t in s.nuts_result.segments}
    assert s.nuts_result.num_overlaps <= n_ovl_before
    # The bottom-up copies (and everything else, absent other changes) must
    # re-place identically — the re-run sees the same frozen context.
    assert fixed_after == fixed_before


def test_clear_bundles_keep_user_preserves_absent_bundle_membership(tmp_path):
    # Audit P3-04: clear_bundles(keep_user=True) kept the bundle row of a
    # hand-committed USER topology (FK) but unconditionally wiped ALL
    # bundle_net/bundle_busterm rows — and the re-add loop only covers the
    # CURRENT bundler run. A kept bundle absent from a later session's
    # netlist therefore permanently lost its net membership, reloading as a
    # zero-net, zero-width bundle whose "kept" user routing routes nothing.
    import buda
    db = buda.BDB(str(tmp_path / "p304.bdb"))
    s1 = _session()
    s1.bdb = db
    for c in ("def_layer 4 M4 H TOP 0.0", "def_layer 5 M5 V TOP 0.0",
              "add_block A 0 0 100 100", "add_block B 400 300 500 400",
              "add_block C 0 300 100 400",
              "add_bus u[4] A.o B.i", "add_bus v[4] A.o2 C.i2",
              "run_bundler STRICT", "generate_topologies"):
        _run(s1, c)
    wv = next(w for w in s1.bundles
              if "v_0" in w.input.original_bundle.get_net_names())
    vid = str(wv.input.original_bundle.id)
    _run(s1, f"edit_topology {vid} 1")
    _run(s1, "edit_commit pin")                 # USER candidate FK-keeps vid
    assert db.bundle_nets(vid) == [f"v_{i}" for i in range(4)]

    # Session B against the same BDB: re-bundle WITHOUT the v bus.
    s2 = _session()
    s2.bdb = db
    for c in ("def_layer 4 M4 H TOP 0.0", "def_layer 5 M5 V TOP 0.0",
              "add_block A 0 0 100 100", "add_block B 400 300 500 400",
              "add_bus u[4] A.o B.i", "run_bundler STRICT"):
        _run(s2, c)
    ids = {r.id for r in db.all_bundles()}
    assert vid in ids, "keep_user must preserve the USER-topology bundle row"
    assert db.bundle_nets(vid) == [f"v_{i}" for i in range(4)], \
        "FK-kept bundle lost its net membership (audit P3-04)"


def test_select_topology_clears_stale_slide_overrides():
    # Audit P5-03: select_topology re-pinned a bundle without clearing the
    # plan.seg_slide_lo/hi (and seg_net_pull/seg_perp) staged by a previous
    # edit_commit pin. NUTS's only staleness guard is an array-LENGTH match,
    # so a newly selected candidate with the same segment count (all Z/U
    # shapes have 3 segments) had the old candidate's slide windows applied
    # verbatim to unrelated segments — clamping e.g. the new y-trunk into a
    # window staged for the old x-trunk, silently. Re-pinning the SAME
    # candidate must keep its overrides.
    s = _session()
    for c in ("def_layer 4 M4 H TOP 0.0", "def_layer 5 M5 V TOP 0.0",
              "add_block A 0 0 100 100", "add_block B 400 300 500 400",
              "add_bus d[4] A.o B.i", "run_bundler STRICT",
              "generate_topologies"):
        _run(s, c)
    w = s.bundles[0]
    z3 = [i for i, c in enumerate(w.input.candidates)
          if len(c.segments) == 3]
    assert len(z3) >= 2, "fixture needs two 3-segment candidates"
    _run(s, f"edit_topology 1 {z3[0] + 1}")
    _run(s, "edit_set_slide 1 150 180")
    _run(s, "edit_commit pin")                # appends USER cand + pins it
    user_idx = w.plan.selected_topology_index
    assert list(w.plan.seg_slide_lo), "commit must stage the slide override"
    # Re-pinning the SAME (user) candidate keeps its staged windows.
    _run(s, f"select_topology 1 {user_idx + 1}")
    assert list(w.plan.seg_slide_lo), \
        "same-candidate re-pin must keep the staged override"
    # Pinning a DIFFERENT same-seg-count candidate must drop them.
    _run(s, f"select_topology 1 {z3[1] + 1}")
    assert list(w.plan.seg_slide_lo) == [], \
        "stale slide windows survived onto a different topology"
    assert list(w.plan.seg_slide_hi) == []
    assert list(w.plan.seg_net_pull) == []


def test_leaf_keepouts_cover_layers_patterned_after_first_solve():
    # Audit P4-01: _install_leaf_keepouts guarded on grid-object IDENTITY,
    # making it a one-shot — a LOW layer patterned AFTER the first solve
    # (def_track_pattern reuses the same RoutingGridStack) never received
    # its implicit solid-leaf-cell keepouts, so DetailedNUTS placed
    # bit-wires straight across solid cells on that layer and the planner's
    # signal-track supply over-counted it. The guard must be content-aware.
    s = _session()
    for c in ("def_layer 3 M3 H 20", "def_layer 4 M4 H TOP 20",
              "def_layer 5 M5 V 20", "def_layer 6 M6 V TOP 20",
              "add_block A 0 0 100 100", "add_block B 500 0 600 100",
              "add_block C 200 300 300 400",   # solid leaf -> LOW keepout
              "add_net n0 A.p B.q",
              "run_bundler STRICT", "generate_topologies",
              "def_track_pattern 3 0 POWER 2 1 SIGNAL 1 1 SIGNAL 1 1",
              "run_planner 5 signal_tracks",    # first install: M3 only
              "def_track_pattern 5 0 POWER 2 1 SIGNAL 1 1 SIGNAL 1 1",
              "run_nuts", "run_detailed_nuts"):
        _run(s, c)
    rg = s.routing_grid
    # Block C occupies (200,300)-(300,400); count signal tracks in a window
    # fully inside it. M3 is H (perp=y), M5 is V (perp=x).
    n3 = rg.get_layer_grid(3).count_signal_tracks_in_span(
        210.0, 290.0, 310.0, 390.0)
    n5 = rg.get_layer_grid(5).count_signal_tracks_in_span(
        310.0, 390.0, 210.0, 290.0)
    assert n3 == 0, "layer patterned before the first solve lost its keepout"
    assert n5 == 0, \
        "layer patterned AFTER the first solve must still get leaf keepouts"


def test_sidecar_selection_exact_match_beats_prefix_collision(tmp_path):
    # Audit P1-01: sidecar selections resolved by first-net-name PREFIX, so
    # a selection for bus "d" (hint "d_0") could land on bus "d_0x" (first
    # net "d_0x_0") — whichever came first in self.bundles — a silent wrong
    # pin. Exact match must win; ambiguous legacy prefixes warn and skip.
    s = _session()
    _run(s, "add_block A 0 0 100 100")
    _run(s, "add_block B 300 0 400 100")
    _run(s, "def_layer 4 M4 H TOP 44.44")
    _run(s, "def_layer 5 M5 V TOP 50.0")
    _run(s, "add_block C 300 200 400 300")
    _run(s, "add_bus d_0x[4] A.o B.i")     # first net d_0x_0 (prefix trap)
    _run(s, "add_bus d[4] A.o2 C.i2")      # first net d_0 (separate bundle)
    _run(s, "run_bundler")
    _run(s, "generate_topologies")
    import json
    sidecar = tmp_path / "sel.sidecar.json"
    sidecar.write_text(json.dumps({"selections": [
        {"bundle_hint": "d_0", "topo_index_hint": 1, "topo_type": "",
         "topo_wl": -1}]}))
    s._sidecar_path = lambda: str(sidecar)
    s._apply_selections()
    by_first_net = {w.input.original_bundle.get_net_names()[0]: w
                    for w in s.bundles}
    assert by_first_net["d_0"].input.topology_pinned, \
        "exact-hint bundle must receive the selection"
    assert not by_first_net["d_0x_0"].input.topology_pinned, \
        "prefix-collision bundle must NOT be pinned"
