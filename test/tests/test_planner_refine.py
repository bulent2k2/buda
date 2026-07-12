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

"""`set_planner_param refine_passes` — the hier level-ordering synthesis
(opens.md item 8; docs/congestion_planner.md "Level ordering").

Pass 1 of `run_planner hier` plans top-down, protecting later cell-local
bundles with pessimistic cell-interior demand reservations.  Those
reservations OVER-COUNT, so an early global can detour around phantom
congestion (the deep-first A/B: hbundles/01/02 pick 3-segment Z shapes
where a straight I_H provably fits).  Each refinement pass revisits every
committed, unlocked bundle DEEPEST-FIRST against the now-REAL usage of
everyone else — rip up, then adopt an unrestricted STRICT replan only when
it beats the best plan KEEPING the old topology, both scored on the same
state (the strictly-better-than-keeping rule; adopting any replan was
measured and rejected — hbundles/10 accepted 23 score-equal lateral moves
and went 7 -> 78 DNUTS opens).

Measured with the strict rule (hier corpus): 01 WL -21%, 02 WL -32%,
05 opens 47->8 at 2 passes, 10 heals 1 overlap / 7 opens -> 0/0; all
other flows and mix2_fast byte-identical no-ops.  Default 0 skips the
loop entirely — existing flows are bit-identical (golden corpora).
"""
import io
import contextlib
from pathlib import Path

import buda_cli

_ROOT = Path(__file__).parents[2]


def _run_01(refine_passes, set_param=True):
    """Drive flow/hbundles/01_pipeline_hier.buda with the knob set on the
    session up front (planner params persist and are applied at planner
    construction, so the flow's own run_planner hier picks it up)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        if set_param:
            s.do_command(f"set_planner_param refine_passes {refine_passes}")
        s.do_command(f"source {_ROOT / 'flow/hbundles/01_pipeline_hier.buda'}")
    return s


def _abstract_wl(s):
    total = 0.0
    for t in s.nuts_result.segments:
        total += abs(t.span_hi - t.span_lo)
    return total


def test_refinement_straightens_phantom_detours():
    """01_pipeline_hier: pass-1 reservations detour two D0 buses into
    3-segment Z shapes; the refinement pass proves the straight I_H fits
    against real usage and adopts it — abstract WL 418 -> 330, still clean."""
    base = _run_01(0)
    ref = _run_01(1)
    assert base.nuts_result.num_overlaps == 0
    assert ref.nuts_result.num_overlaps == 0
    wl_base, wl_ref = _abstract_wl(base), _abstract_wl(ref)
    assert wl_ref < wl_base, (wl_base, wl_ref)
    # every bundle ends as a straight single-segment wire
    for w in ref.bundles:
        sel = w.plan.selected_topology_index
        assert len(w.input.candidates[sel].segments) == 1, \
            w.input.candidates[sel].type


def test_refinement_default_off_is_identical():
    """refine_passes unset vs explicitly 0: same selections either way (the
    loop is skipped entirely; the golden corpora guard flows at large)."""
    a = _run_01(0, set_param=False)      # knob never touched
    b = _run_01(0, set_param=True)       # explicit 0
    sel_a = [w.plan.selected_topology_index for w in a.bundles]
    sel_b = [w.plan.selected_topology_index for w in b.bundles]
    assert sel_a == sel_b


def test_refine_passes_param_recognized():
    """`set_planner_param refine_passes` must not hit the unknown-param
    warning."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for c in ("def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
                  "add_block A 0 0 100 100", "add_block B 300 0 400 100",
                  "add_net n0 A.p B.p", "run_bundler strict",
                  "generate_topologies", "set_planner_param refine_passes 2",
                  "run_planner"):
            s.do_command(c)
    assert "unknown param" not in buf.getvalue()
