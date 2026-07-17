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

"""Opt-in kWLSpread realization-risk WL penalty (flow/big_data_test/b44.buda).

The planner's kWL term scores the nominal segment-sum, but NUTS realizations
wander within the candidate's slide/span DOF envelope [wl_lo, wl_hi]: a
wide-envelope shape (many slide-coupled segments) realizes far ABOVE its
nominal while a tight 2-seg shape realizes at or below it.  b44's 52-bit
multicast picks a 6-seg TRUNK_H+MST (nominal 3510) that realizes 4510/bit
over a 2-seg TRUNK_V (nominal 4010) realizing 3715/bit — the nominal
ranking inverts the true one.  `set_planner_param kWLSpread <alpha>` adds
alpha * (wl_hi - wl_lo) to the scored WL (base stays the nominal; envelope
annotated session-side per candidate), demoting realization-risky shapes.
"""
import io
import contextlib

import pytest

import buda
import buda_cli


def _b44_session(knob):
    """The b44 repro floorplan: one 52-bit multicast, blk_23 -> {blk_07,
    io_pad_tl}."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = [
        # The b44 flow's real layer/track fixture (pytest cwd = repo root).
        "source flow/tracks/tracks4top.buda",
        "add_block blk_07 2960 9750 4660 10250",
        "add_block blk_23 200 10830 2700 11830",
        "add_block io_pad_tl 200 12000 1200 12800",
        "add_bus bus_060[52] blk_23.p blk_07.p,io_pad_tl.p",
        "run_bundler", "generate_topologies",
    ]
    if knob:
        cmds.append("set_planner_param kWLSpread 0.125")
    cmds += ["run_planner", "run_nuts", "run_detailed_nuts"]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), buda.ostream_redirect():
        for c in cmds:
            s.do_command(c)
    return s, buf.getvalue()


def _detailed_wl(s):
    return sum(abs(ns.span_hi - ns.span_lo)
               for ns in s.detailed_result.net_segments)


def test_b44_default_nominal_ranking_picks_structural_tie_winner():
    """Baseline lock-in, updated for the structural (wl, nsegs, type)
    tie-break: the nominal ranking used to select the slide-coupled 6-seg
    TRUNK_H+MST at the 3510 tie by ALPHABET ('+' < '@'; the b44 mis-ranking
    kWLSpread was shipped for).  The tie-break now sorts the 3-seg plain
    trunks ahead of the staircase, so the default equal-score pick is a
    structurally-tight candidate — the mis-pick class this knob guarded
    against at equal nominal WL is resolved upstream (the knob remains the
    guard for UNEQUAL-nominal envelope risk)."""
    s, _ = _b44_session(knob=False)
    w = s.bundles[0]
    sel = w.input.candidates[w.plan.selected_topology_index]
    assert sel.estimated_wirelength == 3510   # still the nominal floor group
    assert len(sel.segments) <= 3             # structural tie winner, not the
    assert "MST" not in sel.type              # 6-seg staircase
    assert s.detailed_result.num_unplaced == 0


def test_b44_kwlspread_flips_to_tight_envelope_candidate():
    """With kWLSpread 0.125 the planner demotes the wide-envelope MST and
    picks a tight-envelope candidate.  History: pre-clamp, this flip was
    worth ~20% detailed WL (234546 -> ~188513) because the MST's greedy pull
    placement overshot to the window edges; the NUTS pull-target BREAKPOINT
    clamp (ConnSeg::pull_break) then fixed the realization itself, so the
    baseline now lands within ~1% of the knob's pick and the assertion is
    "not worse", not "20% better".  The knob remains the selection-level
    guard for realization risk the clamp cannot remove (e.g. contention)."""
    s0, _ = _b44_session(knob=False)
    s1, out = _b44_session(knob=True)
    assert "WL envelopes annotated" in out
    w = s1.bundles[0]
    sel = w.input.candidates[w.plan.selected_topology_index]
    # Tight-envelope pick: few segments, and every candidate's envelope is
    # annotated (wl_lo/wl_hi set by the session).
    assert len(sel.segments) <= 3
    assert sel.wl_lo >= 0.0 and sel.wl_hi >= sel.wl_lo
    assert s1.detailed_result.num_unplaced == 0
    # The knob's pick must not realize worse than the nominal ranking's
    # (small tolerance for host near-tie noise).
    assert _detailed_wl(s1) <= 1.02 * _detailed_wl(s0)


def test_kwlspread_off_by_default_no_annotation():
    """Without the knob no envelope annotation runs and candidates keep the
    -1 sentinel (planner scores the plain nominal)."""
    s, out = _b44_session(knob=False)
    assert "WL envelopes annotated" not in out
    w = s.bundles[0]
    assert all(c.wl_lo < 0 for c in w.input.candidates)


def test_annotation_failure_clears_stale_stamp(monkeypatch):
    """A candidate stamped on an earlier run whose recompute later FAILS
    (mutated topology) must be reset to the -1 sentinel — the planner falls
    back to the nominal, never a stale pre-mutation envelope (Codex #312)."""
    s, _ = _b44_session(knob=False)
    w = s.bundles[0]
    # First pass stamps everything.
    s._annotate_wl_envelopes([w])
    assert all(c.wl_lo >= 0 for c in w.input.candidates)

    def boom(*a, **k):
        raise RuntimeError("mutated beyond derivation")
    monkeypatch.setattr(type(s), "_topology_wl_interval", boom)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s._annotate_wl_envelopes([w])
    assert all(c.wl_lo < 0 and c.wl_hi < 0 for c in w.input.candidates)
    assert "underivable -> nominal fallback" in buf.getvalue()


def test_annotation_fp_override_stamps_all_wrappers():
    """The explicit-floorplan flavour (the bottom-up template solve hands the
    cell-local floorplan it already built) stamps every candidate."""
    s, _ = _b44_session(knob=False)
    w = s.bundles[0]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s._annotate_wl_envelopes([w], fp=s.fp)
    assert all(c.wl_lo >= 0.0 and c.wl_hi >= c.wl_lo
               for c in w.input.candidates)


def test_kwlspread_param_recognized():
    """set_planner_param kWLSpread must not hit the unknown-param warning."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.do_command("def_layer 4 M4 H TOP 50")
    s.do_command("add_block A 0 0 10 10")
    s.do_command("add_block B 20 0 30 10")
    s.do_command("add_net n A.p B.p")
    s.do_command("run_bundler strict")
    s.do_command("generate_topologies")
    s.do_command("run_planner")          # planner instance now exists
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), buda.ostream_redirect():
        s.do_command("set_planner_param kWLSpread 0.125")
    assert "unknown param" not in buf.getvalue()
