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

"""Bottom-up template CLASS moves in ripup_reroute.

A `hier.locked` wrapper (bottom-up template instance) is skipped by every
contender/global pass — its routing is a uniform fixed copy of the cell
template's local solve.  When the residual contention sits on locked
bundles, the healer's class pass re-pins the TEMPLATE and re-routes the
whole rotation class in one measured move (the mix2_fast_bottomup
2-overlap/16-open plateau).  See docs/internal/bottomup_healer_templates.md
plan item A.

Covers: the apply/propagate mechanics (pin + layers to every instance,
overrides cleared, locked recomputed), the extended snapshot/restore
round-trip (template plan state, dogleg bookkeeping, planner grids, derived
caches), the user-pin provenance skip, the flat/no-locked no-op guarantee,
and the end-to-end heal on the real mix2 bottom-up flow (slow tier).
"""

import contextlib
import io
import pathlib

import pytest
import buda
import buda_cli

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_RNR = _ROOT / "flow" / "rnr"


def _run_cmd(s, cmd):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        s.do_command(cmd)
    return out.getvalue()


def _two_inst_db(x2=500, y2=0, cross_net=False):
    """proc_cell (two pipe_cell children + 4-bit cell-local buses)
    instantiated twice — the template-sharing vehicle (same shape as
    test_hier_bottom_up's fixture)."""
    db = buda.BDB(":memory:")
    db.add_cell("proc_cell", 420, 200)
    db.add_cell("pipe_cell", 110, 80)
    db.add_inst_to_cell("proc_cell", "pa_i", "pipe_cell", 20, 60)
    db.add_inst_to_cell("proc_cell", "pb_i", "pipe_cell", 155, 60)
    db.add_inst("proc_i1", "proc_cell", "", 0, 0)
    db.add_inst("proc_i2", "proc_cell", "", x2, y2)
    for i in range(4):
        db.add_net_pins(f"ab1_{i}", "proc_i1/pa_i.out", ["proc_i1/pb_i.in"])
        db.add_net_pins(f"ab2_{i}", "proc_i2/pa_i.out", ["proc_i2/pb_i.in"])
    if cross_net:
        db.add_cell("leaf_cell", 60, 60)
        db.add_inst("L0", "leaf_cell", "", -200, 70)
        db.add_inst("R0", "leaf_cell", "", 1200, 70)
        for i in range(4):
            db.add_net_pins(f"lr_{i}", "L0.out", ["R0.in"])
    buda.BustermGen(db).derive(1)
    return db


def _bottom_up_session(db):
    """Full bottom-up flow through run_nuts on the given BDB."""
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
    """(template wrapper, cell wrapper list) for the marked cell."""
    by_cell = s._rr_class_cell_wrappers()
    assert cell in by_cell and by_cell[cell]
    return by_cell[cell][0], by_cell[cell]


def _tmpl_state(w):
    return (w.plan.selected_topology_index, w.input.topology_pinned,
            len(w.input.candidates), list(w.plan.seg_layers),
            list(w.plan.seg_perp), list(w.plan.seg_net_pull),
            list(w.plan.seg_slide_lo), list(w.plan.seg_slide_hi),
            list(w.input.pinned_seg_layers))


def _inst_state(iw):
    return (iw.plan.selected_topology_index, iw.input.topology_pinned,
            bool(iw.hier.locked), len(iw.input.candidates),
            list(iw.plan.seg_layers), list(iw.input.pinned_seg_layers),
            list(iw.plan.seg_net_pull))


# ── no-op guarantees ─────────────────────────────────────────────────────────

def test_class_pass_noop_on_flat_session():
    """A non-hier session must return (False, 0) with NO output — the class
    pass is structurally inert outside bottom-up flows (byte-identity)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ok, trials = s._rr_class_pass('a', lambda: 1, 1)
    assert (ok, trials) == (False, 0)
    assert buf.getvalue() == ""


def test_class_pass_noop_without_locked_contention():
    """A bottom-up session whose contention holds no locked bundle: no class
    to move, no output."""
    s = _bottom_up_session(_two_inst_db())
    assert any(w.hier.locked for w in s.bundles)
    s._rr_locked_contenders = lambda stage: []          # no locked contention
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ok, trials = s._rr_class_pass('a', lambda: 1, 1)
    assert (ok, trials) == (False, 0)
    assert buf.getvalue() == ""


def test_no_class_moves_token_accepted():
    """`no_class_moves` must be in the flag vocabulary (not an unknown
    option)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = _run_cmd(s, "ripup_reroute no_class_moves")
    assert "unknown option" not in out.lower()
    assert "no bundles" in out                            # parsed past flags


# ── apply/propagate mechanics ────────────────────────────────────────────────

def test_class_apply_repins_template_and_all_instances():
    """_rr_class_apply must pin the alternate on the template, re-assign its
    layers via the cell-local solve, and propagate index+layers+locked to
    EVERY instance wrapper with the per-segment overrides cleared."""
    s = _bottom_up_session(_two_inst_db())
    tw, cell_ws = _template(s)
    tid = tw.input.original_bundle.id
    old = tw.plan.selected_topology_index
    alt = next(i for i in range(len(tw.input.candidates)) if i != old)
    s._rr_class_apply(tw, "proc_cell", cell_ws, alt)
    assert tw.plan.selected_topology_index == alt
    assert tw.input.topology_pinned
    assert list(tw.input.pinned_seg_layers) == list(tw.plan.seg_layers)
    assert len(tw.plan.seg_layers) == \
        len(tw.input.candidates[alt].segments)
    insts = s._hier_expansion_map[tid]
    assert len(insts) == 2
    for iw in insts:
        assert iw.plan.selected_topology_index == alt
        assert iw.input.topology_pinned and iw.hier.locked
        assert list(iw.plan.seg_layers) == list(tw.plan.seg_layers)
        assert list(iw.input.pinned_seg_layers) == \
            list(tw.input.pinned_seg_layers)
        assert list(iw.plan.seg_net_pull) == []
        assert list(iw.plan.seg_slide_lo) == []
        # seg_perp must be cleared: the planner's recharge (commit_plan)
        # consumes it as a band-charge override for the NEW candidate, so
        # stale old-candidate values would mischarge congestion for every
        # later replan (Codex #472 P1).
        assert list(iw.plan.seg_perp) == []
    # Derived caches invalidated — the next NUTS recomputes the fixed
    # copies from the new pin.
    assert s._bu_fixed_cache is None
    assert s._template_track_verdict is None
    assert s._bu_dnuts_plan_cache is None


def test_class_move_rerun_keeps_uniform_copies():
    """After apply + the no-replan re-run, the instances' fixed copies are
    uniform translates of the NEW template solve (the class stays a
    class)."""
    s = _bottom_up_session(_two_inst_db(x2=500, y2=300))
    tw, cell_ws = _template(s)
    tid = tw.input.original_bundle.id
    old = tw.plan.selected_topology_index
    alt = next(i for i in range(len(tw.input.candidates)) if i != old)
    s._rr_class_apply(tw, "proc_cell", cell_ws, alt)
    s._rr_rerun('a', full=True, skip_replan=True)
    inst_of = {w.input.original_bundle.id: b.instances[0]
               for w in s.bundles
               for b in [w.input.original_bundle] if b.cell_context}
    segs = {(inst_of[ts.bundle_id], ts.seg_idx): ts
            for ts in s.nuts_result.segments if ts.bundle_id in inst_of}
    n = len({k[1] for k in segs if k[0] == "proc_i1"})
    assert n > 0
    for si in range(n):
        a, b = segs[("proc_i1", si)], segs[("proc_i2", si)]
        assert a.placed and b.placed
        assert a.layer == b.layer and a.horiz == b.horiz
        d_along, d_perp = (500, 300) if a.horiz else (300, 500)
        assert b.track_position == pytest.approx(a.track_position + d_perp)
        assert min(b.span_lo, b.span_hi) == pytest.approx(
            min(a.span_lo, a.span_hi) + d_along)


# ── snapshot / restore round-trip ────────────────────────────────────────────

def test_class_snapshot_restore_roundtrip():
    """A rejected class trial must leave NO divergence: template + instance
    plan state, dogleg bookkeeping, planner grids, derived caches, and the
    result refs all restore exactly, and a NUTS re-solve from the restored
    state reproduces the committed track positions."""
    s = _bottom_up_session(_two_inst_db())
    tw, cell_ws = _template(s)
    tid = tw.input.original_bundle.id
    before_t = _tmpl_state(tw)
    before_i = [_inst_state(iw) for iw in s._hier_expansion_map[tid]]
    before_nuts = s.nuts_result
    before_fixed = s._bu_fixed_cache
    before_pos = {(ts.bundle_id, ts.seg_idx): ts.track_position
                  for ts in s.nuts_result.segments}

    snap = s._rr_class_snapshot()
    old = tw.plan.selected_topology_index
    alt = next(i for i in range(len(tw.input.candidates)) if i != old)
    s._rr_class_apply(tw, "proc_cell", cell_ws, alt)
    s._rr_rerun('a', full=True, skip_replan=True)
    assert tw.plan.selected_topology_index == alt      # trial took effect
    s._rr_class_restore(snap)

    assert _tmpl_state(tw) == before_t
    assert [_inst_state(iw)
            for iw in s._hier_expansion_map[tid]] == before_i
    assert s.nuts_result is before_nuts                # result ref restored
    assert s._bu_fixed_cache is before_fixed           # committed cache back
    # Determinism: re-solving from the restored plan reproduces the
    # committed positions exactly.
    s._run_nuts_internal()
    after_pos = {(ts.bundle_id, ts.seg_idx): ts.track_position
                 for ts in s.nuts_result.segments}
    assert after_pos == before_pos


# ── provenance: user pins are inviolable ─────────────────────────────────────

def test_user_pinned_template_is_skipped():
    """A template the USER pinned before the local solve must never be moved
    by the class pass (_bu_user_pinned provenance)."""
    s = _bottom_up_session(_two_inst_db())
    tw, _cell_ws = _template(s)
    tid = tw.input.original_bundle.id
    inst_bid = s._hier_expansion_map[tid][0].input.original_bundle.id
    s._bu_user_pinned = {tid}
    s._rr_locked_contenders = lambda stage: [inst_bid]
    before = _tmpl_state(tw)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ok, trials = s._rr_class_pass('a', lambda: 1, 1)
    assert (ok, trials) == (False, 0)
    assert "user-pinned" in buf.getvalue()
    assert _tmpl_state(tw) == before                   # untouched


def test_replica_contender_resolves_canonical_template():
    """_hier_expansion_map also aliases REPLICA bundle ids to their
    instance's wrapper; the class pass must map a locked contender to its
    CANONICAL template, not the alias — else the class is silently skipped
    (the replica id is not a cell template; Codex #472 P1)."""
    s = _bottom_up_session(_two_inst_db())
    tw, _cell_ws = _template(s)
    tid = tw.input.original_bundle.id
    canon = {w.input.original_bundle.id
             for ws in s._rr_class_cell_wrappers().values() for w in ws}
    # The wrapper a replica id aliases (proc_i2's instance).
    aliased = [iws[0] for rid, iws in s._hier_expansion_map.items()
               if rid not in canon]
    assert aliased, "fixture should produce a replica alias"
    inst_bid = aliased[0].input.original_bundle.id
    assert inst_bid in {iw.input.original_bundle.id
                        for iw in s._hier_expansion_map[tid]}
    s._rr_locked_contenders = lambda stage: [inst_bid]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ok, trials = s._rr_class_pass('a', lambda: (1 << 30), 0)
    assert not ok                       # nothing improves a metric of 0
    assert trials > 0, buf.getvalue()   # but the class WAS found and tried
    assert "CLASS pass" in buf.getvalue()


# ── end-to-end heal (slow): the mix2 bottom-up plateau ───────────────────────

def _mix2_bottom_up_healed(class_moves):
    s = buda_cli.BudaSession()
    s.no_viz = True
    rr = "ripup_reroute" + ("" if class_moves else " no_class_moves")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        for c in [f"source {_RNR / 'mix_tracks.buda'}",
                  f"open_bdb {_RNR / 'mix2.bdb.sql'}",
                  "set_bottom_up dnuts1", "set_bottom_up dnuts2",
                  "set_bottom_up dogleg1", "set_bottom_up dogleg2",
                  "derive_busterms 2",
                  "add_blocks_from_bdb 0",
                  "add_blocks_from_bdb 1 skip",
                  "add_blocks_from_bdb 2 skip",
                  "run_hier_bundler depth 2",
                  "generate_hier_topologies",
                  "run_planner hier 5 signal_tracks",
                  "run_nuts",
                  "negotiate_congestion", rr,
                  "check_template_tracks on_mismatch independent",
                  "run_detailed_nuts",
                  "negotiate_congestion", rr]:
            s.do_command(c)
    return s, out.getvalue()


@pytest.mark.slow
def test_mix2_bottom_up_class_moves_heal_end_to_end():
    """The real vehicle (docs/internal/bottomup_healer_templates.md): with
    the healers' locked-bundle plateau, class moves must (a) commit at
    least one template move or already be clean, (b) end with no more DNUTS
    opens than the no_class_moves run, and (c) preserve class uniformity —
    every template's locked instances share one selected index."""
    s_off, _ = _mix2_bottom_up_healed(class_moves=False)
    s_on, out_on = _mix2_bottom_up_healed(class_moves=True)
    opens_off = s_off.detailed_result.num_unplaced
    opens_on = s_on.detailed_result.num_unplaced
    assert opens_on <= opens_off, (opens_on, opens_off, out_on)
    # The pass either heals further (a commit happened) or there was
    # nothing locked left to move.
    if opens_off > 0:
        assert ("CLASS COMMIT" in out_on) or (opens_on < opens_off), out_on
    # Class uniformity: locked siblings agree on one candidate index.
    for tid, iws in (s_on._hier_expansion_map or {}).items():
        idxs = {iw.plan.selected_topology_index
                for iw in iws if iw.hier.locked}
        assert len(idxs) <= 1, (tid, idxs)
