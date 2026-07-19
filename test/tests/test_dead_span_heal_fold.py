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


def _stage_b_session(fold, keepout=True):
    """L-shape flow with its H stub pinned to a LOW M2, taken through run_nuts
    + run_detailed_nuts so a stage-b (DNUTS-open) state exists.  With
    keepout=True the M2 tracks are all killed (the stub is dead); with
    keepout=False the stub has real supply (a live LOW, nothing to escalate).
    `fold` sets the healer auto-escalation flag.  Returns the session and the
    pinned H-segment index."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    s._heal_dead_spans_in_healers = fold
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        for c in _SETUP:
            s.do_command(c)
        if keepout:
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


def test_heal_checkpoints_route_when_bdb_open(monkeypatch):
    """A heal-only fix must persist: when a BDB is open and the escalation
    moves segments, `_heal_dead_spans` checkpoints the healed route itself,
    so a heal that fixes the opens with no following hill-climb commit is not
    lost on load_pipeline (Codex #349 P2)."""
    s, _ = _stage_b_session(fold=True)
    calls = []
    s.bdb = object()   # non-None sentinel; _checkpoint_routing is stubbed
    monkeypatch.setattr(s, "_checkpoint_routing", lambda *a, **k: calls.append(1))
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        n = s._heal_dead_spans("b")
    assert n >= 1
    assert calls == [1]        # the heal persisted its own route


def test_heal_no_checkpoint_when_nothing_dead(monkeypatch):
    """No dead segment → no escalation → no spurious checkpoint (the heal is a
    true no-op, so it must not touch the BDB)."""
    s, _ = _stage_b_session(fold=True, keepout=False)   # live LOW stub
    assert s.detailed_result.num_unplaced == 0
    calls = []
    s.bdb = object()
    monkeypatch.setattr(s, "_checkpoint_routing", lambda *a, **k: calls.append(1))
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        n = s._heal_dead_spans("b")
    assert n == 0
    assert calls == []


def _run_nuts_session(with_healer_script, tmp_path, auto=True):
    """Build the dead-M2-stub scenario, point script_path at a .buda file that
    does (or does not) contain a healer, then run run_nuts.  Returns the
    session + pinned H-seg index.  No manual escalation flag and no healer is
    actually executed — this isolates the run_nuts auto-escalation gate."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    s._dead_span_auto_at_run_nuts = auto
    script = tmp_path / "flow.buda"
    script.write_text("ripup_reroute\n" if with_healer_script else "check_design\n")
    s.script_path = str(script)
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        for c in _SETUP:
            s.do_command(c)
        s.do_command("add_keepout 0 0 200 500 2")
    w = s.bundles[0]
    topo = w.input.candidates[w.plan.selected_topology_index]
    sl = list(w.plan.seg_layers)
    h_idx = None
    for si, seg in enumerate(topo.segments):
        if seg.start.y == seg.end.y:
            sl[si] = 2
            h_idx = si
    w.plan.seg_layers = sl
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        s.do_command("run_nuts")
    return s, h_idx


def test_run_nuts_auto_escalates_when_healer_in_flow(tmp_path):
    """A healer in the flow script makes run_nuts auto-escalate the dead M2
    stub to TOP (M4) — before the healers run, the measured-better timing —
    with no manual `set_dead_span_escalate`."""
    s, h_idx = _run_nuts_session(with_healer_script=True, tmp_path=tmp_path)
    assert list(s.bundles[0].plan.seg_layers)[h_idx] == 4   # TOP M4


def test_run_nuts_no_escalate_without_healer_in_flow(tmp_path):
    """No healer in the flow → run_nuts does not auto-escalate (the stage-b
    fold would handle it if a healer were run interactively); the dead stub
    stays on LOW M2."""
    s, h_idx = _run_nuts_session(with_healer_script=False, tmp_path=tmp_path)
    assert list(s.bundles[0].plan.seg_layers)[h_idx] == 2   # unchanged LOW M2


def test_run_nuts_auto_knob_off(tmp_path):
    """The `_dead_span_auto_at_run_nuts` rollback knob disables the run_nuts
    auto-escalation even with a healer in the flow."""
    s, h_idx = _run_nuts_session(with_healer_script=True, tmp_path=tmp_path,
                                 auto=False)
    assert list(s.bundles[0].plan.seg_layers)[h_idx] == 2   # unchanged LOW M2
