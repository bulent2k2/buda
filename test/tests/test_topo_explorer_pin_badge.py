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

"""A script pin is "PINNED", not "PLANNER SELECTED", until run_planner runs.

Regression (flow/sel_topo_in_viz_topo.buda): topologies pinned with
select_topologies (and no run_planner) were labelled "PLANNER SELECTED
(PINNED)" in the TopologyExplorer, because is_planner_active only checked
selected_topology_index — which select_topology(ies) also sets. The planner is
"active" only once it has actually run (and assigned per-segment layers).
"""

import pytest

# Moved to the mid tier: full-pipeline / BDB round-trip / interchange
# integration (keeps the fast tier < 10s). See
# docs/internal/test/runtime_analysis.md.
pytestmark = pytest.mark.mid
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
import buda_cli  # noqa: E402
import buda_viz  # noqa: E402


def _session(with_planner):
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = [
        "def_layer 4 M4 V TOP 10",
        "def_layer 5 M5 H TOP 10",
        "def_layer 6 M6 V TOP 10",
        "add_block b1 0 0 100 100",
        "add_block b2 200 200 300 300",
        "add_bus v[4] b1.o b2.i",
        "add_bus w[4] b2.i b1.o",
        "run_bundler strict",
        "generate_topologies",
        "select_topologies 1 3 2 4",   # pin b1->topo3, b2->topo4
    ]
    if with_planner:
        cmds.append("run_planner")
    for c in cmds:
        s.do_command(c)
    return s


def _title_for_pinned_topo(s):
    exp = buda_viz.TopologyExplorer(s.fp, s.bundles, layer_stack=s.layers)
    try:
        exp.bidx = 0
        exp.idx = s.bundles[0].plan.selected_topology_index  # the pinned topo
        exp._draw()
        return exp.ax.get_title()
    finally:
        plt.close('all')


def test_script_pin_without_planner_is_pinned_only():
    title = _title_for_pinned_topo(_session(with_planner=False))
    assert "PINNED" in title
    assert "PLANNER SELECTED" not in title


def test_script_pin_with_planner_is_planner_selected_pinned():
    title = _title_for_pinned_topo(_session(with_planner=True))
    assert "PLANNER SELECTED" in title and "PINNED" in title
