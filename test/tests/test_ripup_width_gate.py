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

"""ripup's static width-feasibility gate (issue #523).

A candidate carrying a segment whose bit demand cannot fit its own slide
window — `bits x best-case bit-pitch > window` — strands those bits at
DetailedNUTS on every layer at every legal position.  The unpinned planner's
STRICT refuses such a candidate via capacity overflow; only ripup's PINNED
trials (whose ladder ends in BEST_EFFORT, by design) can commit one.
`_rr_width_infeasible` gates them out of `_rr_candidate_order`, the single
choke point behind the contender scan, the global pass, the release pass and
refine_selection.

The unit fixture makes the verdict arithmetic exact: two 50-tall blocks face
each other across a channel on layers whose track pattern gives bit-pitch
22/8 = 2.75, so any H segment tapping a face inherits a 50-unit slide window.
A 32-bit bus demands 32 x 2.75 = 88 > 50 (gated); a 4-bit bus demands 11
(fits).  The same geometry flips the verdict on bus width alone.

The end-to-end `@mid` test pins the prophylactic contract on `mix.buda`,
the flow whose trial sets demonstrably contain width-infeasible candidates:
the gate fires AND the endpoint stays fully clean.  (On the pre-#526 main the
gate was what healed mix's residual overlap; current main heals it either
way.)
"""
import contextlib
import io
import os
import sys
from pathlib import Path

import pytest

import buda

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
import buda_cli  # noqa: E402

_ROOT = Path(__file__).parents[2]

_PAT = ("VDD 2 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 "
        "VSS 2 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1")   # unit 22, 8 SIGNAL -> bit-pitch 2.75


def _session(nbits, patterns=True):
    """Two 50-tall blocks facing across a channel; one nbits-wide bus.

    Every H-leg candidate (the straight I_H, an L's H leg, ...) taps a
    50-unit face, so its slide window is 50; V legs ride the 200-unit block
    widths.  With the 2.75 bit-pitch pattern the H legs are width-infeasible
    exactly when nbits x 2.75 > 50."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = ["def_layer 4 M4 H TOP 50",
            "def_layer 5 M5 V TOP 50"]
    if patterns:
        cmds += [f"def_track_pattern 4 0 {_PAT}",
                 f"def_track_pattern 5 0 {_PAT}"]
    cmds += ["add_block A 0 1000 200 1050",
             "add_block B 2400 1000 2600 1050",
             f"add_bus x[{nbits}] A.p B.p",
             "run_bundler", "generate_topologies"]
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    return s


def _verdicts(s):
    w = s.bundles[0]
    with contextlib.redirect_stdout(io.StringIO()):
        return w, {i: s._rr_width_infeasible(w, i)
                   for i in range(len(w.input.candidates))}


def _i_h_index(w):
    return next(i for i, c in enumerate(w.input.candidates)
                if c.type.startswith("I_H"))


# ── the predicate ────────────────────────────────────────────────────────────

def test_narrow_window_candidate_is_gated():
    """32 bits x 2.75 = 88 track-units of demand against the 50-unit face
    window: the straight I_H (and every other H-leg shape) is statically
    width-infeasible."""
    s = _session(32)
    w, v = _verdicts(s)
    assert v[_i_h_index(w)], "I_H over a 50-unit window must gate at 32 bits"
    assert any(v.values())


def test_fitting_bus_is_not_gated():
    """Same geometry, 4-bit bus: 11 <= 50 everywhere — nothing gates.  The
    verdict is a function of demand vs window, not of the shape."""
    s = _session(4)
    _, v = _verdicts(s)
    assert not any(v.values()), \
        [f"{i}:{s.bundles[0].input.candidates[i].type}"
         for i, g in v.items() if g]


def test_no_track_pattern_never_gates():
    """A direction where any layer lacks a track pattern has no reliable
    per-track demand bound (the width capacity model is not track-based), so
    the gate must stand down even on the 32-bit fixture."""
    s = _session(32, patterns=False)
    _, v = _verdicts(s)
    assert not any(v.values())


def test_override_only_layer_stands_down():
    """has_layer() proves a RoutingGrid exists, not that a global pattern set
    the per-track bit pitch: an add_grid_override on an undefined layer
    default-constructs the grid, and eff_bus_width then returns the
    width/dilution fallback — not a pitch — which must not be multiplied by a
    bit count (Codex P2 on #531).  The gate stands down for the direction."""
    s = _session(32, patterns=False)
    stack = buda.RoutingGridStack()
    empty = buda.TrackPattern(0.0, [])
    stack.add_override(4, 0, 0, 3000, 3000, empty)
    stack.add_override(5, 0, 0, 3000, 3000, empty)
    assert stack.has_layer(4) and stack.has_layer(5)   # the trap Codex named
    s.routing_grid = stack
    _, v = _verdicts(s)
    assert not any(v.values())


def test_env_optout_disables_the_gate(monkeypatch):
    """BUDA_RR_WIDTH_GATE=0 is the study opt-out: the gated fixture's
    verdicts all flip to feasible."""
    monkeypatch.setenv("BUDA_RR_WIDTH_GATE", "0")
    s = _session(32)
    _, v = _verdicts(s)
    assert not any(v.values())


def test_bottom_up_session_is_scoped_out():
    """Any hier.locked wrapper marks a bottom-up session: class moves re-route
    every instance of a template in one step, the coarsest move in the healer
    and the most sensitive to trial-list perturbation — measured on
    mix2_fast_bottomup, the unscoped gate landed 6/60/4 without ever
    excluding a winning move.  The gate stands down for the whole session."""
    s = _session(32)
    w, v = _verdicts(s)
    assert v[_i_h_index(w)]                  # sanity: gated before the lock
    s.bundles[0].hier.locked = True
    s._rr_width_memo = {}                    # verdicts are memoized per run
    _, v2 = _verdicts(s)
    assert not any(v2.values())


def test_candidate_order_excludes_gated_indices():
    """The gate is applied inside _rr_candidate_order — the trial list the
    contender scan / global pass / release pass / refine_selection all
    consume — so a gated index must not appear, while feasible alternates
    survive."""
    s = _session(32)
    w, v = _verdicts(s)
    gated = {i for i, g in v.items() if g}
    keep = next(i for i, g in v.items() if not g)
    with contextlib.redirect_stdout(io.StringIO()):
        order = s._rr_candidate_order(w, old_tidx=keep, stage='a',
                                      sites=[(True, 1025.0)])
    assert gated, "fixture must produce at least one gated candidate"
    assert not (set(order) & gated), (order, sorted(gated))
    assert set(order), "feasible alternates must survive the gate"


# ── end to end: the gate participates and the endpoint stays clean ───────────

@pytest.mark.mid
def test_mix_flow_gate_participates_and_endpoint_clean():
    """mix.buda is the flow where the gate demonstrably participates (its
    trial sets contain width-infeasible candidates — on the pre-#526 main it
    was the gate that healed the flow's residual overlap; current main heals
    it either way).  The regression contract for a prophylactic: the gate
    fires AND the endpoint stays fully clean — the exclusions never cost the
    climb its 0/0.  Endpoint read from the session, not from log lines
    (healer re-solves print intermediate NUTS lines after the final restored
    state)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    cwd = os.getcwd()
    os.chdir(_ROOT / "flow" / "rnr")
    try:
        with contextlib.redirect_stdout(buf), buda.ostream_redirect():
            for line in (_ROOT / "flow" / "rnr" / "mix.buda").read_text() \
                                                             .splitlines():
                line = line.split("#")[0].strip()
                if line:
                    s.do_command(line)
    finally:
        os.chdir(cwd)
    assert "width-gate" in buf.getvalue(), "the gate never fired on mix.buda"
    assert s.nuts_result.num_overlaps == 0
    assert s.detailed_result.num_unplaced == 0
