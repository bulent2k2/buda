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

"""The Terminals (busterms) view setting carries over to the TopologyExplorer.

The explorer shares the main viz's ViewState, so Blocks and Hanan already carry
over (their draw helpers read ui_state).  Busterm markers used to be drawn
unconditionally; now `_draw_busterm_markers` honors `ui_state.busterms`, so the
setting carries over at open time and on live toggle (the explorer is a
ViewState listener), matching Blocks/Hanan.
"""
import os
import sys

import matplotlib
matplotlib.use("Agg")   # headless; no window

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import buda_cli   # noqa: E402
import buda_viz   # noqa: E402
from ui_state import ViewState  # noqa: E402


def _session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    for cmd in (
        "def_layer 6 M6 V TOP 10",
        "def_layer 5 M5 H TOP 10",
        "add_block b1 0 0 100 100",
        "add_block b2 200 0 300 100",
        "add_bus v[2] b1.o b2.i",
        "run_bundler strict",
        "generate_topologies",
        "run_planner",
    ):
        s.do_command(cmd)
    return s


def _n_diamonds(exp):
    # Busterm markers are drawn as diamond ('D') Line2D markers.
    return sum(1 for ln in exp.ax.get_lines() if ln.get_marker() == "D")


def test_terminals_setting_carries_over_to_explorer():
    import matplotlib.pyplot as plt
    s = _session()
    st = ViewState()
    st.busterms = False                 # Terminals OFF in the main viz
    exp = buda_viz.TopologyExplorer(
        s.fp, s.bundles, layer_stack=s.layers, ui_state=st)
    try:
        assert exp.ui_state is st        # explorer shares the viz ViewState
        exp.fig_redraw()
        assert _n_diamonds(exp) == 0, "Terminals OFF should hide busterm markers"

        st.toggle_busterms()             # notify -> explorer redraws
        assert _n_diamonds(exp) > 0, "Terminals ON should show busterm markers"
    finally:
        plt.close("all")


def test_explorer_arrow_keys_pan_not_navigate():
    import types
    import matplotlib.pyplot as plt
    s = _session()
    exp = buda_viz.TopologyExplorer(
        s.fp, s.bundles, layer_stack=s.layers, ui_state=ViewState())
    try:
        exp.fig_redraw()
        idx0 = exp.idx
        x0 = exp.ax.get_xlim()
        exp._on_key(types.SimpleNamespace(key="right"))   # arrow pans, no nav
        assert exp.idx == idx0, "arrow keys must not change the topology"
        assert exp.ax.get_xlim()[0] > x0[0], "right should pan the view"
        # the letter alias still navigates topologies
        if len(exp.topos) > 1:
            exp._on_key(types.SimpleNamespace(key="d"))
            assert exp.idx != idx0
    finally:
        plt.close("all")


def test_explorer_g_key_toggles_hanan():
    import types
    import matplotlib.pyplot as plt
    s = _session()
    st = ViewState()
    exp = buda_viz.TopologyExplorer(
        s.fp, s.bundles, layer_stack=s.layers, ui_state=st)
    try:
        before = st.hanan_grid
        exp._on_key(types.SimpleNamespace(key="g"))   # 'g' toggles the Hanan grid
        assert st.hanan_grid != before
    finally:
        plt.close("all")


def test_explorer_t_key_toggles_terminals():
    import matplotlib.pyplot as plt
    s = _session()
    st = ViewState()                     # busterms defaults True
    exp = buda_viz.TopologyExplorer(
        s.fp, s.bundles, layer_stack=s.layers, ui_state=st)
    try:
        exp.fig_redraw()
        assert _n_diamonds(exp) > 0

        class _Evt:
            key = "t"
        exp._on_key(_Evt())              # 't' toggles Terminals in the explorer
        assert st.busterms is False
        assert _n_diamonds(exp) == 0
    finally:
        plt.close("all")
