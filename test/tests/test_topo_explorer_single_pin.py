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

"""A bundle has at most ONE pinned topology in the TopologyExplorer.

Regression (flow/sel_topo_conflict_test): _current_is_selected used to OR three
independent criteria — a sidecar's topo_index_hint, its (type, wirelength)
match, and the live plan pin — so a single bundle could show several topologies
as "pinned" at once (made worse by _find_selection matching a stale sidecar by
bundle_id).  The live pin must win and resolve to a single index.
"""
import os
import sys
import json

import matplotlib
matplotlib.use("Agg")   # headless; no window

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
import buda_cli  # noqa: E402
import buda_viz  # noqa: E402


def _session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    for cmd in (
        "def_layer 6 M6 V TOP 10",
        "def_layer 5 M5 H TOP 10",
        "add_block b1 0 0 100 100",
        "add_block b2 200 0 300 100",
        "add_bus v[2] b1.o b2.i",
        "add_bus w[2] b2.i b1.o",
        "run_bundler strict",
        "generate_topologies",
        "select_topology 1 2",          # pin bundle 1 -> topo 2 (1-based)
        "run_planner",
    ):
        s.do_command(cmd)
    return s


def _n_pinned(exp, bidx):
    exp.bidx = bidx
    n = 0
    for i in range(len(exp.topos)):
        exp.idx = i
        if exp._current_is_selected():
            n += 1
    return n


def test_explorer_pins_at_most_one_topology_per_bundle(tmp_path):
    s = _session()
    # A stale sidecar from another design: hint 'n1' matches no current net, but
    # bundle_id 1 + (type, wl) + topo_index_hint each used to light up a
    # *different* topo of bundle 1.
    side = tmp_path / "sc.json"
    side.write_text(json.dumps({"selections": [{
        "bundle_hint": "n1", "bundle_id": 1,
        "topo_type": "I_H", "topo_wl": 100, "topo_index_hint": 4,
        "note": "", "selected_at": "2026-06-15T12:00:00",
    }]}))

    exp = buda_viz.TopologyExplorer(
        s.fp, s.bundles, sidecar_path=str(side), layer_stack=s.layers)
    try:
        # No bundle may show more than one pinned topology.
        for bidx in range(len(s.bundles)):
            assert _n_pinned(exp, bidx) <= 1
        # Bundle 1 was script-pinned to topo 2 (index 1) -> exactly that one.
        assert _n_pinned(exp, 0) == 1
        exp.bidx, exp.idx = 0, 1
        assert exp._current_is_selected()           # topo 2
        assert exp._selected_topo_index() == 1      # the live script pin wins
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')
