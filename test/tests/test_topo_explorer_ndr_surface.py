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

"""The explorer's NDR surface (docs/NDR_UI.md Decision 3, read-only):

- a governed bundle's header carries the NDR BADGE (rule + group demand);
- the rule-derived shield GHOST overlay flanks each segment at the run's
  outer-slot offsets (declaration-derived, nothing persisted);
- the DEBUG cost view tints a candidate whose slide windows cannot host
  the rule's demand on any of its layers (realizability flag);
- an ungoverned bundle renders byte-identically to the pre-NDR explorer
  (no badge, no ghosts, no tint).
"""

import pytest

pytestmark = pytest.mark.mid

import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
import buda_cli  # noqa: E402
import buda_viz  # noqa: E402

_SHIELD_COL = '#e0d4f7'   # viz_common._PREROUTE_COLOR['SHIELD']

# 9 SIGNAL slots per period — plenty of window supply (feasible).
_DENSE = "0 VDD 2 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 GND 2 1"
# ONE signal slot per 50-unit period — the 13-slot demand cannot fit any
# slide window of this small design (infeasible, deterministically).
_COARSE = "0 VDD 24 1 _ 1 24"


def _session(pattern):
    s = buda_cli.BudaSession()
    s.no_viz = True
    for c in [
        "add_block blkA 0 0 200 400",
        "add_block blkB 800 0 1000 400",
        "add_bus clk[4] blkA.p blkB.q",
        "add_bus data[8] blkA.d blkB.e",
        "def_layer 3 M3 H 20",
        "def_layer 4 M4 V 20",
        "def_layer 5 M5 H TOP 20",
        "def_layer 6 M6 V TOP 20",
        "def_ndr clk2x width x2 spacing x2 shield bus net GND",
        "set_ndr clk_ clk2x",
        "run_bundler STRICT",
        "generate_topologies",
    ] + [f"def_track_pattern {lid} {pattern}" for lid in (3, 4, 5, 6)]:
        s.do_command(c)
    return s


def _explorer(s, bidx, debug=False):
    return buda_viz.TopologyExplorer(
        s.fp, s.bundles, layer_stack=s.layers, start_bidx=bidx,
        routing_grid=s.routing_grid,
        cost_fn=(s._candidate_costs if debug else None))


def _bidx(s, governed):
    return next(i for i, w in enumerate(s.bundles)
                if w.input.ndr.active() == governed)


def _ghost_lines(exp):
    return [l for l in exp.ax.get_lines()
            if l.get_linestyle() == '--' and l.get_color() == _SHIELD_COL]


def test_governed_bundle_shows_badge_and_ghosts():
    s = _session(_DENSE)
    exp = _explorer(s, _bidx(s, True))
    try:
        exp._draw()
        title = exp.ax.get_title()
        # Badge: rule name + the 13-slot group demand (4 bits x 2 slots +
        # 3 guard gaps + 2 end shields).
        assert "NDR:clk2x 13s/4b" in title
        # Ghost overlay: two dashed shield-colored envelope edges per
        # visible segment.
        ghosts = _ghost_lines(exp)
        assert len(ghosts) >= 2
    finally:
        plt.close('all')


def test_ungoverned_bundle_is_unchanged():
    s = _session(_DENSE)
    exp = _explorer(s, _bidx(s, False))
    try:
        exp._draw()
        assert "NDR:" not in exp.ax.get_title()
        assert not _ghost_lines(exp)
    finally:
        plt.close('all')


def test_debug_view_flags_infeasible_candidate():
    s = _session(_COARSE)
    exp = _explorer(s, _bidx(s, True), debug=True)
    try:
        exp._draw()
        title = exp.ax.get_title()
        assert "⚠ NDR 'clk2x'" in title and "demand 13" in title
        assert exp.ax.title.get_color() == '#aa2222'
    finally:
        plt.close('all')


def test_debug_view_feasible_candidate_not_flagged():
    s = _session(_DENSE)
    exp = _explorer(s, _bidx(s, True), debug=True)
    try:
        exp._draw()
        title = exp.ax.get_title()
        assert "⚠ NDR" not in title
        assert exp.ax.title.get_color() != '#aa2222'
    finally:
        plt.close('all')


def test_no_grid_explorer_still_renders_badge_only():
    # An orphan explorer with no routing grid: badge still shows (spec
    # only), ghosts and tint silently off — never an exception.
    s = _session(_DENSE)
    exp = buda_viz.TopologyExplorer(s.fp, s.bundles, layer_stack=s.layers,
                                    start_bidx=_bidx(s, True))
    try:
        exp._draw()
        assert "NDR:clk2x" in exp.ax.get_title()
        assert not _ghost_lines(exp)
    finally:
        plt.close('all')


def _credit_session(pattern, extra_ndr="credit"):
    """Same design, but the rule may rail-credit its end shields."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    for c in [
        "add_block blkA 0 0 200 400",
        "add_block blkB 800 0 1000 400",
        "add_bus clk[4] blkA.p blkB.q",
        "add_bus data[8] blkA.d blkB.e",
        "def_layer 3 M3 H 20",
        "def_layer 4 M4 V 20",
        "def_layer 5 M5 H TOP 20",
        "def_layer 6 M6 V TOP 20",
        f"def_ndr clk2x width x2 spacing x2 shield bus net GND {extra_ndr}",
        "set_ndr clk_ clk2x",
        "run_bundler STRICT",
        "generate_topologies",
    ] + [f"def_track_pattern {lid} {pattern}" for lid in (3, 4, 5, 6)]:
        s.do_command(c)
    return s


def test_credit_rule_priced_at_its_realizable_minimum():
    """Codex #627: a crediting rule must be priced at the demand DNUTS can
    actually seat (both end shields rail-credited = 11 slots), not the
    uncredited 13-slot worst case — else a window the engine seats fine
    renders red.  The badge still reports the planner-charged uncredited
    demand (R7 prices the worst case), and the flag's message carries the
    realizable number."""
    s_credit = _credit_session(_COARSE, extra_ndr="credit")
    s_plain = _credit_session(_COARSE, extra_ndr="")
    try:
        e1 = _explorer(s_credit, _bidx(s_credit, True), debug=True)
        spec_c = s_credit.bundles[_bidx(s_credit, True)].input.ndr
        e2 = _explorer(s_plain, _bidx(s_plain, True), debug=True)
        spec_p = s_plain.bundles[_bidx(s_plain, True)].input.ndr
        # The realizability basis: credited minimum vs uncredited worst case.
        assert e1._ndr_seg_demand(spec_c, 0) == 11
        assert e2._ndr_seg_demand(spec_p, 0) == 13
        e1._draw()
        e2._draw()
        # Both are genuinely infeasible on this 1-slot-per-period pattern,
        # but each is flagged against ITS OWN realizable demand...
        assert "demand 11" in e1.ax.get_title()
        assert "demand 13" in e2.ax.get_title()
        # ...while the badge keeps reporting the planner-charged demand.
        assert "NDR:clk2x 13s/4b+credit" in e1.ax.get_title()
    finally:
        plt.close('all')


def test_tapered_segment_prices_its_own_bits():
    """Codex #627: a fan-in branch carrying a SUBSET of the bundle's bits
    (Topology::seg_bits) must be priced on that subset — the way planner /
    NUTS / DNUTS do — so its ghost envelope is narrower and it is not
    falsely flagged.  A 1-bit subset on seg 0 shrinks that segment's
    demand from the 4-bit group's 13 slots to a 1-bit run, leaving its
    untapered siblings untouched."""
    s = _session(_DENSE)
    bidx = _bidx(s, True)
    exp = _explorer(s, bidx)
    try:
        spec = s.bundles[bidx].input.ndr
        # Untapered: the whole bundle's demand on every segment.
        assert exp._ndr_seg_demand(spec, 0) == 13
        # The candidate vector hands out copies, so taper a local copy and
        # show it (what an edit session does with its working copy).
        topo = exp.topos[exp.idx]
        topo.seg_bits = {0: [0]}          # seg 0 carries one bit only
        exp._shown_topo = lambda _t=topo: _t
        assert exp._ndr_nbits(0) == 1
        assert exp._ndr_seg_demand(spec, 0) == 4   # 1x2 slots + 2 shields
        assert exp._ndr_seg_demand(spec, 1) == 13  # untapered sibling
    finally:
        plt.close('all')
