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

"""USER-topology persistence through the BDB topology tables (opens item 12).

Hand-built USER candidates are first-class BDB citizens in BOTH flows:

- FLAT: edit_commit persists the candidate (source='user', v15) with taps and
  the pin; load_pipeline restores it by topo_uid — no sidecar needed.
- HIER, pre-expansion: a TopoEdit session on a CELL-LOCAL template edits in
  the template's own frame (cell-local block names / coordinates — the same
  _floorplan_for_hbundle resolution check_design uses); the committed pin
  survives a pre-planner checkpoint (the loader resolves each bundle's frame
  before the missing-block gate), and the resumed `run_planner hier`
  REPLICATES the USER candidate to every instance.
- HIER, post-expansion: an instance-local edit commits through the planner's
  expanded persist path (NOT the pre-expansion _persist_topologies, which
  would clobber the is_expanded checkpoint), stays instance-local, and
  round-trips through `load_pipeline expanded`.
"""
import contextlib
import io

import pytest

import buda
import buda_cli

pytestmark = pytest.mark.mid


def _session(*cmds):
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = []
    for c in cmds:
        with contextlib.redirect_stdout(io.StringIO()) as b:
            s.do_command(c)
        out.append(b.getvalue())
    return s, "".join(out)


LAYERS = ("def_layer 4 M4 H TOP 10", "def_layer 5 M5 V TOP 10")


# ── flat flow ─────────────────────────────────────────────────────────────────

def test_flat_user_topo_bdb_round_trip(tmp_path):
    """Lock in the v15 behavior: a flat edit_commit pin persists the USER
    candidate; a fresh session's load_pipeline restores it — same uid, taps
    intact, pin resolved."""
    db = str(tmp_path / "flat.bdb")
    s1, _ = _session(
        f"open_bdb {db}", *LAYERS,
        "add_block lo 0 0 400 400", "add_block up 0 1000 400 1400",
        "add_bus w[4] lo.o up.i", "run_bundler", "generate_topologies",
        "edit_topology 1 new",
        "edit_add_trunk V 500 200 1200",
        "edit_add_stub up 0", "edit_add_stub lo 0",
        "edit_commit pin", "save_bdb")
    w1 = s1.bundles[0]
    uid = buda.topo_uid(w1.input.candidates[w1.plan.selected_topology_index])
    del s1

    s2, _ = _session(
        f"open_bdb {db}", *LAYERS,
        "add_block lo 0 0 400 400", "add_block up 0 1000 400 1400",
        "load_pipeline")
    w2 = s2.bundles[0]
    sel = w2.plan.selected_topology_index
    assert w2.input.topology_pinned
    assert w2.input.candidates[sel].type == "USER"
    assert buda.topo_uid(w2.input.candidates[sel]) == uid
    # Taps restored logically (topology_seg_busterm links, never re-derived).
    taps = {b.block_name for eps in w2.input.candidates[sel].seg_busterms.values()
            for b in eps if b is not None}
    assert taps == {"lo", "up"}


# ── hier flow ─────────────────────────────────────────────────────────────────

def _two_instance_design(path, cross=False):
    """proc_cell (pa_i, pb_i) instantiated twice, 4 cell-local nets each —
    the template/replica shape from test_hier_bundler, as a session BDB.
    `cross` adds 4 level-0 nets between the two instances (a NORMAL
    cross-block bundle that survives expansion un-expanded)."""
    db = buda.BDB(path)
    db.add_cell("proc_cell", 420, 200)
    db.add_cell("pipe_cell", 110, 80)
    db.add_inst_to_cell("proc_cell", "pa_i", "pipe_cell", 20, 60)
    db.add_inst_to_cell("proc_cell", "pb_i", "pipe_cell", 155, 60)
    db.add_inst("proc_i1", "proc_cell", "", 0, 0)
    db.add_inst("proc_i2", "proc_cell", "", 500, 0)
    for i in range(4):
        db.add_net_pins(f"ab1_{i}", "proc_i1/pa_i.out", ["proc_i1/pb_i.in"])
        db.add_net_pins(f"ab2_{i}", "proc_i2/pa_i.out", ["proc_i2/pb_i.in"])
        if cross:
            db.add_net_pins(f"x_{i}", "proc_i1/pb_i.out", ["proc_i2/pa_i.in"])
    db.compute_all()


HIER_SETUP = (*LAYERS, "add_blocks_from_bdb 0", "add_blocks_from_bdb 1 skip")


def _template_edit_checkpoint(tmp_path):
    """Session 1: hier-bundle, hand-build a USER topo on the 2-instance
    template IN ITS CELL-LOCAL FRAME, pin it, checkpoint pre-planner.
    Returns (checkpoint_path, template_id, user_uid)."""
    src = str(tmp_path / "two.bdb")
    ck = str(tmp_path / "pre.bdb")
    _two_instance_design(src)
    s1, out = _session(
        *LAYERS, f"open_bdb {src}",
        "add_blocks_from_bdb 0", "add_blocks_from_bdb 1 skip",
        "derive_busterms 1", "run_hier_bundler depth 1",
        "generate_hier_topologies")
    tmpl = next(w for w in s1.bundles
                if len(list(w.input.original_bundle.instances)) == 2)
    tid = tmpl.input.original_bundle.id
    outs = []
    for c in (f"edit_topology {tid} new",
              "edit_add_trunk H 30 75 210",       # cell-local row below the pipes
              "edit_add_stub pa_i 0",             # CELL-LOCAL block names
              "edit_add_stub pb_i 0",
              "edit_commit pin",
              f"save_bdb {ck}"):
        with contextlib.redirect_stdout(io.StringIO()) as b:
            s1.do_command(c)
        outs.append(b.getvalue())
    joined = "".join(outs)
    assert "cell-local: proc_cell" in joined       # edits used the cell frame
    assert "committed as candidate" in joined
    uid = buda.topo_uid(tmpl.input.candidates[tmpl.plan.selected_topology_index])
    return ck, tid, uid


def test_hier_template_user_topo_pre_planner_checkpoint_resumes(tmp_path):
    """A pre-planner hier checkpoint with a hand-committed template USER
    candidate LOADS (the loader validates cell-local frames — this used to
    trip the missing-block gate on pa_i/pb_i) and the pin resolves."""
    ck, tid, uid = _template_edit_checkpoint(tmp_path)
    s2, out = _session(f"open_bdb {ck}", *HIER_SETUP, "load_pipeline")
    assert "rehydrated" in out and "Error" not in out
    w = next(x for x in s2.bundles if x.input.original_bundle.id == tid)
    sel = w.plan.selected_topology_index
    assert w.input.topology_pinned
    assert w.input.candidates[sel].type == "USER"
    assert buda.topo_uid(w.input.candidates[sel]) == uid
    # The restored template's frame is resolvable again (entry busterms came
    # back), so check_design audits it in cell-local coordinates — clean.
    with contextlib.redirect_stdout(io.StringIO()) as b:
        s2.do_command("check_design topo")
    assert "no violations" in b.getvalue()


def test_hier_template_user_topo_replicates_to_instances(tmp_path):
    """Resuming the pre-planner checkpoint and CONTINUING with `run_planner
    hier` lands the pinned USER template on EVERY instance, offset to its
    placement — the solve-once-instantiate-everywhere contract now covers
    hand-built candidates."""
    ck, tid, uid = _template_edit_checkpoint(tmp_path)
    s2, _ = _session(f"open_bdb {ck}", *HIER_SETUP, "load_pipeline")
    with contextlib.redirect_stdout(io.StringIO()) as b:
        s2.do_command("run_planner hier 3")
    assert "USER [pinned]" in b.getvalue()
    inst = {w.input.original_bundle.id: w
            for ws in s2._hier_expansion_map.values() for w in ws}
    assert len(inst) == 2
    spans = set()
    for w in inst.values():
        t = w.input.candidates[w.plan.selected_topology_index]
        assert t.type == "USER"
        sg = t.segments[0]                        # the hand-built H trunk
        spans.add((min(sg.start.x, sg.end.x), max(sg.start.x, sg.end.x)))
    # One copy per instance, offset by the instance placement (+500 in x).
    assert spans == {(75, 210), (575, 710)}


def test_hier_instance_local_edit_round_trips_expanded(tmp_path):
    """Post-expansion: an edit on ONE instance wrapper commits through the
    planner's expanded persist (the pre-expansion path used to clobber the
    is_expanded checkpoint — `load_pipeline expanded` then found nothing and
    tripped the missing-block gate), stays instance-local, and round-trips."""
    ck, tid, uid = _template_edit_checkpoint(tmp_path)
    post = str(tmp_path / "post.bdb")
    s2, _ = _session(f"open_bdb {ck}", *HIER_SETUP,
                     "load_pipeline", "run_planner hier 3")
    iids = sorted(w.input.original_bundle.id
                  for ws in s2._hier_expansion_map.values() for w in ws)
    edit_id, sibling_id = iids[0], iids[1]
    outs = []
    for c in (f"edit_topology {edit_id} 1",       # absolute frame (expanded)
              "edit_set_span 0 75 250",           # instance-local tweak
              "edit_commit pin",
              f"save_bdb {post}"):
        with contextlib.redirect_stdout(io.StringIO()) as b:
            s2.do_command(c)
        outs.append(b.getvalue())
    assert "re-persisted expanded planner state" in "".join(outs)
    del s2

    s3, out = _session(f"open_bdb {post}", *HIER_SETUP,
                       "load_pipeline expanded")
    assert "rehydrated" in out and "Error" not in out
    by_id = {w.input.original_bundle.id: w for w in s3.bundles}
    for bid, want in ((edit_id, (75, 250)),       # the local edit
                      (sibling_id, (575, 710))):  # the sibling: untouched
        t = by_id[bid].input.candidates[by_id[bid].plan.selected_topology_index]
        assert t.type == "USER"
        sg = t.segments[0]
        assert (min(sg.start.x, sg.end.x), max(sg.start.x, sg.end.x)) == want


def test_hier_cross_block_user_edit_post_expansion_persists(tmp_path):
    """Codex #306: after `run_planner hier`, self.bundles mixes expanded
    instance wrappers with NORMAL (never-expanded) cross-block bundles.  An
    edit_commit on one of those normal bundles used to 'save' via
    set_topology_selected alone — pointing is_selected at a candidate the BDB
    never received, so the reload dropped the USER topology.  The commit now
    refreshes the bundle's candidate rows first."""
    src = str(tmp_path / "twox.bdb")
    post = str(tmp_path / "postx.bdb")
    _two_instance_design(src, cross=True)
    s, _ = _session(
        *LAYERS, f"open_bdb {src}",
        "add_blocks_from_bdb 0", "add_blocks_from_bdb 1 skip",
        "derive_busterms 1", "run_hier_bundler depth 1",
        "generate_hier_topologies", "run_planner hier 3")
    expanded_ids = {w.input.original_bundle.id
                    for ws in s._hier_expansion_map.values() for w in ws}
    normal = next(w for w in s.bundles
                  if w.input.original_bundle.id not in expanded_ids)
    nid = normal.input.original_bundle.id
    outs = []
    for c in (f"edit_topology {nid} 1",        # absolute frame (cross-block)
              "edit_commit pin",
              f"save_bdb {post}"):
        with contextlib.redirect_stdout(io.StringIO()) as b:
            s.do_command(c)
        outs.append(b.getvalue())
    assert "committed as candidate" in "".join(outs)
    uid = buda.topo_uid(
        normal.input.candidates[normal.plan.selected_topology_index])
    n_pool = len(normal.input.candidates)
    del s

    s2, out = _session(f"open_bdb {post}", *HIER_SETUP,
                       "load_pipeline expanded")
    assert "rehydrated" in out and "Error" not in out
    w2 = next(w for w in s2.bundles if w.input.original_bundle.id == nid)
    assert len(w2.input.candidates) == n_pool          # full pool, USER included
    sel = w2.plan.selected_topology_index
    assert w2.input.candidates[sel].type == "USER"
    assert buda.topo_uid(w2.input.candidates[sel]) == uid
