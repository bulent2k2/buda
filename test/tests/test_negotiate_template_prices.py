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

"""Negotiate v2 — bottom-up template price translation
(docs/internal/bottomup_healer_templates.md item D, OPT-IN `class_moves`).

`negotiate_congestion` prices contention on GLOBAL Hanan bands, but a
bottom-up template plans in its CELL-LOCAL frame — so a locked template
instance could never feel the injected demand.  The v2 layer translates
each instance's injected band-demand records into cell coordinates
(clip to the instance bbox, inverse orientation map), SUMS them across
instances by injecting all translated records into one cell-local
planner, re-plans the target templates UNPINNED under that aggregated
price field, and propagates the result to every instance.

Covers: the geometric translation (N and mirrored instances, clipping,
H/V window swap), target derivation (locked-affected only, canonical
templates, user-pin exclusion), the per-cell re-plan + propagation, the
grid pre-extension contract that keeps injections alive through
optimize_topologies, and the opt-in token plumbing.
"""

import contextlib
import io

import pytest
import buda
import buda_cli


def _run_cmd(s, cmd):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        s.do_command(cmd)
    return out.getvalue()


def _two_inst_db(x2=500, y2=0, rotate2=None):
    """proc_cell (two pipe_cell children + 4-bit cell-local buses)
    instantiated twice (the template-sharing vehicle); `rotate2` applies
    rotate_comp to proc_i2 (180 keeps bottom-up congruence)."""
    db = buda.BDB(":memory:")
    db.add_cell("proc_cell", 420, 200)
    db.add_cell("pipe_cell", 110, 80)
    db.add_inst_to_cell("proc_cell", "pa_i", "pipe_cell", 20, 60)
    db.add_inst_to_cell("proc_cell", "pb_i", "pipe_cell", 155, 60)
    db.add_inst("proc_i1", "proc_cell", "", 0, 0)
    db.add_inst("proc_i2", "proc_cell", "", x2, y2)
    if rotate2:
        db.rotate_comp("proc_i2", rotate2)
    for i in range(4):
        db.add_net_pins(f"ab1_{i}", "proc_i1/pa_i.out", ["proc_i1/pb_i.in"])
        db.add_net_pins(f"ab2_{i}", "proc_i2/pa_i.out", ["proc_i2/pb_i.in"])
    buda.BustermGen(db).derive(1)
    return db


def _bottom_up_session(db):
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.bdb = db
    for c in ["def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
              "def_layer 4 M4 H 50", "def_layer 5 M5 V 50",
              "run_hier_bundler", "generate_hier_topologies",
              "set_bottom_up proc_cell", "run_planner hier", "run_nuts"]:
        _run_cmd(s, c)
    return s


def _template(s, cell="proc_cell"):
    by_cell = s._rr_class_cell_wrappers()
    assert cell in by_cell and by_cell[cell]
    return by_cell[cell][0], by_cell[cell]


# ── geometric translation ────────────────────────────────────────────────────

def test_translate_clips_and_offsets_per_instance():
    """A global H-layer record over proc_i2's interior translates to ONE
    cell-frame record (origin subtracted); a record spanning both
    instances yields one per instance; a record outside both yields
    none."""
    s = _bottom_up_session(_two_inst_db(x2=500, y2=300))
    _tw, cell_ws = _template(s)
    # proc_i2 bbox: [500,920] x [300,500]; layer 6 is H (span=x, perp=y).
    recs = s._translate_injections_to_cell(
        "proc_cell", cell_ws, [(6, 560.0, 700.0, 350.0, 360.0, 5.0)])
    assert recs == [(6, 60.0, 200.0, 50.0, 60.0, 5.0)]
    # Spanning both instances (proc_i1 [0,420]x[0,200] misses y 350):
    # only proc_i2 intersects.
    recs = s._translate_injections_to_cell(
        "proc_cell", cell_ws, [(6, 0.0, 2000.0, 350.0, 360.0, 2.0)])
    assert recs == [(6, 0.0, 420.0, 50.0, 60.0, 2.0)]
    # One record per intersecting instance, summed by injection.
    recs = s._translate_injections_to_cell(
        "proc_cell", cell_ws, [(6, 0.0, 2000.0, 100.0, 110.0, 2.0),
                               (6, 0.0, 2000.0, 400.0, 410.0, 3.0)])
    assert (6, 0.0, 420.0, 100.0, 110.0, 2.0) in recs   # proc_i1, as-is
    assert (6, 0.0, 420.0, 100.0, 110.0, 3.0) in recs   # proc_i2, -300
    assert len(recs) == 2
    # Outside both instances entirely.
    assert s._translate_injections_to_cell(
        "proc_cell", cell_ws, [(6, 0.0, 100.0, 900.0, 910.0, 1.0)]) == []


def test_translate_v_layer_swaps_windows():
    """On a V layer the record's span window is Y and its perp window is
    X — the translation must un-swap and re-swap correctly."""
    s = _bottom_up_session(_two_inst_db(x2=500, y2=300))
    _tw, cell_ws = _template(s)
    # Layer 7 is V: span=y [350,360], perp=x [560,570] -> inside proc_i2.
    recs = s._translate_injections_to_cell(
        "proc_cell", cell_ws, [(7, 350.0, 360.0, 560.0, 570.0, 4.0)])
    assert recs == [(7, 50.0, 60.0, 60.0, 70.0, 4.0)]


def test_translate_mirrored_instance_reflects():
    """A 180-rotated instance (orient S over the reference box) maps a
    global window to the REFLECTED cell window: local = dim - x."""
    db = _two_inst_db(rotate2=180)
    s = _bottom_up_session(db)
    _tw, cell_ws = _template(s)
    orients = s._detect_instance_orients(
        "proc_cell", db.all_components(), ref_name="proc_i1")
    assert orients["proc_i2"] == "S"
    # proc_i2 bbox: [500,920] x [0,200] (180 keeps the outline).  A record
    # at instance-local x [60,200], y [50,60] reflects to cell
    # x [420-200, 420-60] = [220,360], y [200-60, 200-50] = [140,150].
    recs = s._translate_injections_to_cell(
        "proc_cell", cell_ws, [(6, 560.0, 700.0, 50.0, 60.0, 5.0)])
    assert recs == [(6, 220.0, 360.0, 140.0, 150.0, 5.0)]


def test_translate_unions_instances_without_double_count():
    """The instance walk is the de-duplicated UNION of every template's
    list (Codex #478: a template whose occurrences are a different subset
    must still have its surroundings priced) — and a shared instance must
    contribute its translated records exactly ONCE, or the aggregated
    price field would be inflated by the number of templates in the
    cell."""
    s = _bottom_up_session(_two_inst_db(x2=500, y2=300))
    tw, cell_ws = _template(s)
    rec = [(6, 560.0, 700.0, 350.0, 360.0, 5.0)]
    base = s._translate_injections_to_cell("proc_cell", cell_ws, rec)
    doubled = s._translate_injections_to_cell("proc_cell",
                                              cell_ws + cell_ws, rec)
    assert doubled == base


# ── target derivation ────────────────────────────────────────────────────────

def test_neg_template_targets_locked_affected_only():
    s = _bottom_up_session(_two_inst_db())
    tw, cell_ws = _template(s)
    tid = tw.input.original_bundle.id
    inst_bid = s._hier_expansion_map[tid][0].input.original_bundle.id
    tgts = s._neg_template_targets([inst_bid])
    assert len(tgts) == 1
    cell, targets, ws = tgts[0]
    assert cell == "proc_cell"
    assert [t.input.original_bundle.id for t in targets] == [tid]
    assert ws == cell_ws
    # An unlocked affected bundle contributes no target.
    free = [w.input.original_bundle.id for w in s.bundles
            if not w.hier.locked]
    assert s._neg_template_targets(free[:1]) == []
    # A user-pinned template is never negotiated.
    s._bu_user_pinned = {tid}
    assert s._neg_template_targets([inst_bid]) == []


# ── per-cell priced re-plan + propagation ────────────────────────────────────

def test_neg_replan_repins_and_propagates():
    """After the priced re-plan the target template is pinned again (the
    solve's write-back), its instances carry the propagated pin
    (uniform index, locked, layers copied, overrides cleared), and the
    derived caches are invalidated."""
    s = _bottom_up_session(_two_inst_db())
    tw, cell_ws = _template(s)
    tid = tw.input.original_bundle.id
    inj = [(6, 0.0, 400.0, 100.0, 110.0, 50.0)]
    with contextlib.redirect_stdout(io.StringIO()):
        s._neg_replan_cell_templates("proc_cell", [tw], cell_ws, inj)
    assert tw.input.topology_pinned
    assert list(tw.input.pinned_seg_layers) == list(tw.plan.seg_layers)
    sel = tw.plan.selected_topology_index
    for iw in s._hier_expansion_map[tid]:
        assert iw.plan.selected_topology_index == sel
        assert iw.hier.locked and iw.input.topology_pinned
        assert list(iw.plan.seg_layers) == list(tw.plan.seg_layers)
        assert list(iw.plan.seg_perp) == []
    assert s._bu_fixed_cache is None
    assert s._template_track_verdict is None


def test_injections_survive_local_optimize():
    """The grid pre-extension contract: a demand record injected via
    _plan_bottom_up_cell(inject=...) must still be charged AFTER
    optimize_topologies ran (the optimizer's own grid extension rebuilds
    the cuts and wipes injections unless extend_grid_for pre-extended) —
    otherwise the whole price translation is silently a no-op."""
    s = _bottom_up_session(_two_inst_db())
    tw, cell_ws = _template(s)
    # A huge price on the template's currently selected geometry must
    # steer the UNPINNED re-plan away from it (any candidate has
    # alternatives in this pool).
    sel = tw.plan.selected_topology_index
    topo = tw.input.candidates[sel]
    seg = topo.segments[0]
    horiz = (seg.start.y == seg.end.y)
    lid = tw.plan.seg_layers[0]
    span = sorted((seg.start.x, seg.end.x) if horiz
                  else (seg.start.y, seg.end.y))
    perp = seg.start.y if horiz else seg.start.x
    tw.input.topology_pinned = False
    tw.input.pinned_seg_layers = []
    inj = [(lid, float(span[0]), float(span[1]),
            float(perp - 2), float(perp + 2), 1e6)]
    with contextlib.redirect_stdout(io.StringIO()):
        s._plan_bottom_up_cell("proc_cell", cell_ws, 3, persist=False,
                               inject=inj)
    new_sel = tw.plan.selected_topology_index
    new_topo = tw.input.candidates[new_sel]
    new_seg = new_topo.segments[0]
    new_perp = (new_seg.start.y if (new_seg.start.y == new_seg.end.y)
                else new_seg.start.x)
    # Either a different candidate won, or the same shape moved off the
    # priced band — staying exactly on a 1e6-priced band would mean the
    # injection was wiped.
    assert (new_sel != sel) or (new_perp != perp) \
        or (tw.plan.seg_layers[0] != lid)


# ── opt-in plumbing ──────────────────────────────────────────────────────────

def test_negotiate_tokens_accepted_and_default_off():
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = _run_cmd(s, "negotiate_congestion class_moves")
    assert "unknown option" not in out.lower()
    assert "no bundles" in out
    out = _run_cmd(s, "negotiate_congestion no_class_moves")
    assert "unknown option" not in out.lower()
    with pytest.raises(SystemExit):        # unknown flags are a hard error
        _run_cmd(s, "negotiate_congestion bogus_flag")
    # Default OFF: the session-level entry point must not derive template
    # targets unless opted in.
    import inspect
    sig = inspect.signature(
        buda_cli.BudaSession._negotiate_congestion)
    assert sig.parameters["use_class_moves"].default is False
