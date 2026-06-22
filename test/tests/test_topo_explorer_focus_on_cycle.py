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

"""TopologyExplorer focuses a bundle's pinned topology consistently — on open
AND when cycling between bundles.

Regression (flow/sel_topo_conflict_test.buda): __init__ jumped to the sidecar's
topo for the start bundle, but _step_bundle/show_bundle_index used only
plan.selected_topology_index, so cycling away and back lost the pin and snapped
to topo 1. All three paths now share _focus_topo_index().
"""
import os
import sys
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
import pytest  # noqa: E402
import buda_cli  # noqa: E402
import buda_viz  # noqa: E402

# Builds a full TopologyExplorer over a routed flow (integration) -> mid tier,
# so the fast tier (`bb -t`) stays under ~10s.
pytestmark = pytest.mark.mid


def _session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    for c in (
        "def_layer 5 M5 V TOP 10", "def_layer 6 M6 H TOP 10", "def_layer 7 M7 V TOP 10",
        "add_block b1 0 0 100 100", "add_block b2 200 200 300 300",
        "add_bus v[4] b1.o b2.i", "add_bus w[4] b2.i b1.o",
        "run_bundler strict", "generate_topologies",   # no run_planner: pre-plan / sidecar baseline
    ):
        s.do_command(c)
    return s


def test_focus_pinned_topo_on_open_and_after_cycling(tmp_path):
    s = _session()
    assert len(s.bundles[0].input.candidates) >= 4   # need a topo 4 (index 3)

    side = tmp_path / "sc.json"
    side.write_text(json.dumps({"selections": [{
        "bundle_hint": "v_0", "bundle_id": 1,
        "topo_type": s.bundles[0].input.candidates[3].type,
        "topo_wl": s.bundles[0].input.candidates[3].estimated_wirelength,
        "topo_index_hint": 3, "note": "", "selected_at": "x",
        "seg_layers": [5, 6, 7],
    }]}))

    exp = buda_viz.TopologyExplorer(
        s.fp, s.bundles, sidecar_path=str(side), layer_stack=s.layers)
    try:
        # Bundle 1 (start) opens on its pinned topo (index 3).
        assert exp.bidx == 0 and exp.idx == 3

        # Cycle to bundle 2 (no pin -> topo 0) and back to bundle 1.
        exp._step_bundle(+1)
        assert exp.bidx == 1 and exp.idx == 0
        exp._step_bundle(+1)
        assert exp.bidx == 0 and exp.idx == 3   # re-focuses the pin, not topo 0

        # Jumping directly also lands on the pin.
        exp.show_bundle_index(1)
        assert exp.bidx == 1 and exp.idx == 0
        exp.show_bundle_index(0)
        assert exp.bidx == 0 and exp.idx == 3
    finally:
        plt.close('all')
