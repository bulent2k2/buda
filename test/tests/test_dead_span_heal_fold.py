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

"""Dead-span escalation folded into the stage-b healers (`_heal_dead_spans`).

A dead LOW segment (zero keepout-clear signal tracks over its placed
geometry) is a guaranteed DetailedNUTS open that no candidate re-pin can
fix — it's a layer-assignment fault.  The healers (`ripup_reroute`,
`negotiate_congestion`) run the escalation ONCE before their hill-climb so a
user gets the fix automatically, without the manual `set_dead_span_escalate`
flag.  Default on; only stage-b runs are touched; a dead LOW segment strands
100% of its bits, so escalating strictly reduces opens.  Measured: bigHalf
healer flow opens 315→179 (no overlap cost).  See wishlist-planner
"dead-span discriminator".
"""
import contextlib
import io

import buda
import buda_cli


_SETUP = [
    "def_layer 2 M2 H 50",
    "def_layer 3 M3 V 50",
    "def_layer 4 M4 H TOP 50",
    "def_layer 5 M5 V TOP 50",
    "def_track_pattern 2 0 SIGNAL 1 1",
    "def_track_pattern 3 0 SIGNAL 1 1",
    "def_track_pattern 4 0 SIGNAL 1 1",
    "def_track_pattern 5 0 SIGNAL 1 1",
    "add_block A 0 0 40 40",
    "add_block B 60 400 100 440",
    "add_bus d[8] A.p B.p",
    "run_bundler strict",
    "generate_topologies",
    "run_planner 5",
]


def _stage_b_session(fold):
    """L-shape flow with its H stub pinned to a keepout-dead LOW M2, taken
    through run_nuts + run_detailed_nuts so a stage-b (DNUTS-open) state
    exists.  `fold` sets the healer auto-escalation flag.  Returns the
    session and the pinned H-segment index."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    s._heal_dead_spans_in_healers = fold
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        for c in _SETUP:
            s.do_command(c)
        s.do_command("add_keepout 0 0 200 500 2")   # kills every M2 track
    w = s.bundles[0]
    sel = w.plan.selected_topology_index
    topo = w.input.candidates[sel]
    sl = list(w.plan.seg_layers)
    h_idx = None
    for si, seg in enumerate(topo.segments):
        if seg.start.y == seg.end.y:
            sl[si] = 2
            h_idx = si
    w.plan.seg_layers = sl
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        s.do_command("run_nuts")
        s.do_command("run_detailed_nuts")
    return s, h_idx


def test_heal_default_flag_on():
    """The healer auto-escalation is on by default (users get the fix without
    the manual `set_dead_span_escalate` flag)."""
    s = buda_cli.BudaSession()
    assert s._heal_dead_spans_in_healers is True


def test_heal_escalates_dead_low_stage_b():
    """With the fold on, `_heal_dead_spans('b')` escalates the dead M2 stub to
    the TOP H layer, refreshes the detailed result, and reports the move."""
    s, h_idx = _stage_b_session(fold=True)
    assert s.detailed_result.num_unplaced == 8      # dead before heal
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        n = s._heal_dead_spans("b")
    assert n >= 1
    assert list(s.bundles[0].plan.seg_layers)[h_idx] == 4   # TOP M4
    assert s.detailed_result.num_unplaced == 0             # refreshed metric


def test_heal_off_leaves_it():
    """With the fold flag cleared, `_heal_dead_spans` is a no-op — the stub
    stays on the dead LOW layer (bit-identical to pre-feature healers)."""
    s, h_idx = _stage_b_session(fold=False)
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        n = s._heal_dead_spans("b")
    assert n == 0
    assert list(s.bundles[0].plan.seg_layers)[h_idx] == 2   # still LOW M2
    assert s.detailed_result.num_unplaced == 8


def test_heal_stage_a_is_noop():
    """Stage a (abstract overlaps) is not the escalation's domain — the fold
    never fires there even with the flag on."""
    s, _ = _stage_b_session(fold=True)
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        n = s._heal_dead_spans("a")
    assert n == 0


def test_ripup_auto_heals_dead_low():
    """End-to-end: `ripup_reroute` on the dead-stub stage-b state clears the
    8 opens automatically (fold on, no manual flag)."""
    s, _ = _stage_b_session(fold=True)
    assert s.detailed_result.num_unplaced == 8
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        s.do_command("ripup_reroute")
    assert s.detailed_result.num_unplaced == 0
