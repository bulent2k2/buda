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

"""A bundle the planner could not route must never read as a clean design.

`flow/ndr_layer_mask_starved.buda` covers the single-plan case end to end.
These pin the two ways the report could still be evaded, both of which need
a RE-PLAN and so cannot be expressed as a one-pass flow:

  * a bundle that was routed by an EARLIER planner run keeping that run's
    selection when the current one commits nothing (Codex P1 on #691), and
  * a design in which NO bundle is selected, where the topo stage's
    auto-promotion to all-candidates mode would audit the candidate POOL —
    valid by construction — and report Success over a design with no routes.
"""
import contextlib
import io

import buda_cli


def _run(s, cmd):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command(cmd)
    return buf.getvalue()


def _planned_session():
    """One L-shaped bus, planned successfully on a full H+V stack."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    for line in ("add_block a 0 0 200 200", "add_block b 800 600 1000 800",
                 "add_bus v_[3] a.p b.q",
                 "def_layer 3 M3 H TOP 20", "def_layer 4 M4 V TOP 20",
                 "def_track_pattern 3 0 VDD 2 1 (_ 1 1)x12 GND 2 1",
                 "def_track_pattern 4 0 VDD 2 1 (_ 1 1)x12 GND 2 1",
                 "run_bundler STRICT", "generate_topologies",
                 "set_track_pitch 3", "run_planner 1"):
        _run(s, line)
    return s


def test_a_replan_that_commits_nothing_clears_the_stale_plan():
    """The wrapper must not keep the PREVIOUS run's route.

    Worse than stale bookkeeping: the tightened mask here forbids exactly the
    horizontal layer the old plan names, so NUTS would place the bundle on a
    layer the policy excludes and the audit — which walks bundles by their
    selected candidate — would check the old route and pass it."""
    s = _planned_session()
    w = s.bundles[0]
    assert w.plan.selected_topology_index >= 0
    assert 3 in list(w.plan.seg_layers), "the vehicle must route on M3"

    # Tighten to V-only: the L-shaped bus now has no legal layer for its H
    # leg, so the planner can commit nothing in any mode.
    w.input.allowed_layers = [4]
    out = _run(s, "run_planner 1")

    assert "NOTHING committed" in out, out
    assert "contain no HORIZONTAL layer" in out, out
    assert w.plan.selected_topology_index == -1, "stale selection survived"
    assert list(w.plan.seg_layers) == [], "stale layer assignment survived"


def test_check_design_reports_a_design_with_no_routes_at_all():
    """Every selection unset AFTER planning is a total failure, not the
    pre-planner state the all-candidates promotion exists for."""
    s = _planned_session()
    s.bundles[0].input.allowed_layers = [4]
    _run(s, "run_planner 1")
    out = _run(s, "check_design")

    assert "UNROUTED_BUNDLE" in out, out
    assert "Success: no violations found" not in out, out


def test_the_all_candidates_promotion_still_applies_before_planning():
    """The gate must not cost the pre-planner behaviour it replaces: with no
    planner run, an unselected bundle is normal and check_design still audits
    the whole candidate pool."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    for line in ("add_block a 0 0 200 200", "add_block b 800 600 1000 800",
                 "add_bus v_[3] a.p b.q",
                 "def_layer 3 M3 H TOP 20", "def_layer 4 M4 V TOP 20",
                 "run_bundler STRICT", "generate_topologies"):
        _run(s, line)
    out = _run(s, "check_design")
    assert "(all candidates)" in out, out
    assert "UNROUTED_BUNDLE" not in out, out
