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
