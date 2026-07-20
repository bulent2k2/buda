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

"""Ripup convergence guard: stop stage-b ripup early on an over-capacity
design that can't converge, to save the (large) runtime it would otherwise
burn for ~zero gain.

The guard fires only when ALL of: (a) >= WINDOW committed iterations have run,
(b) the primary metric is still >= FLOOR (far above any converging corpus
flow's mid-ripup residual), and (c) < FRAC of the window-start metric cleared
over the last WINDOW iterations — so it provably cannot fire on a flow that
converges in < WINDOW iters or drops below FLOOR.  Measured: tc3a
56.8s -> 16.3s (fires at iter 12); bigHalf (2 iters) and mix2 (5 iters) reach
0/0 unaffected.  See docs/internal/healer_effectiveness_2026-07.md.
"""
import contextlib
import io
import os
from pathlib import Path

import pytest

import buda
import buda_cli
import buda_session.ripup as rr
from buda_session.util import _RR_CONVERGE_GUARD_DEFAULT

_ROOT = Path(__file__).parents[2]
_HEALERS = ("negotiate_congestion", "ripup_reroute")
_SKIP = ("#", "visualize", "exit")


def test_converge_guard_default_on():
    """The guard is on by default."""
    assert _RR_CONVERGE_GUARD_DEFAULT is True


def test_converge_guard_keyword_parsed(monkeypatch):
    """`ripup_reroute [no_]converge_guard` reaches _ripup_reroute as the
    tri-state converge_guard kwarg (None = default, True/False = forced)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    seen = {}
    monkeypatch.setattr(s, "_ripup_reroute",
                        lambda **kw: seen.update(kw))
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("ripup_reroute")
        assert seen["converge_guard"] is None
        s.do_command("ripup_reroute no_converge_guard")
        assert seen["converge_guard"] is False
        s.do_command("ripup_reroute converge_guard")
        assert seen["converge_guard"] is True


def _run_tc3a_to_stage_b(s):
    """Drive tc3a through its pipeline (healers stripped) to a stage-b state
    with many DNUTS opens, ready for a single stage-b ripup."""
    absf = _ROOT / "flow" / "big_data_test" / "tc3a.buda"
    os.chdir(absf.parent)
    s.no_viz = True
    s.script_path = str(absf)
    s._dead_span_auto_at_run_nuts = False
    cmds = []
    for raw in absf.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(_SKIP):
            continue
        w = line.split()[0]
        if w in _HEALERS or w == "run_detailed_nuts":
            continue
        cmds.append(line)
        if w == "run_nuts":
            break
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        for c in cmds:
            s.do_command(c)
        # canonical stage-a + dnuts + stage-b negotiate, leaving many opens
        for c in ("negotiate_congestion", "ripup_reroute",
                  "run_detailed_nuts", "negotiate_congestion"):
            s.do_command(c)


@pytest.mark.slow
def test_converge_guard_fires_on_tc3a():
    """tc3a is over-capacity (thousands of DNUTS opens) — the stage-b ripup
    cannot converge, so the guard stops it early (well before max_iter) with
    the 'not converging' note, and leaves the metric non-zero."""
    s = buda_cli.BudaSession()
    _run_tc3a_to_stage_b(s)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), buda.ostream_redirect():
        s.do_command("ripup_reroute 30")
    out = buf.getvalue()
    assert "not converging" in out, out[-1500:]
    commits = out.count("COMMIT")
    assert commits < 30, f"guard did not stop early: {commits} commits"
    assert s.detailed_result.num_unplaced > 100   # still over-capacity


@pytest.mark.slow
def test_no_converge_guard_grinds_to_max_iter():
    """With `no_converge_guard` the same tc3a stage-b ripup grinds the full
    budget (no early stop) — the guard is opt-out-able."""
    s = buda_cli.BudaSession()
    _run_tc3a_to_stage_b(s)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), buda.ostream_redirect():
        s.do_command("ripup_reroute 12 no_converge_guard")
    out = buf.getvalue()
    assert "not converging" not in out
