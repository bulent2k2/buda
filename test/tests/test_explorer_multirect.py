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

"""The topology explorer must OPEN on a multi-rect bundle.

Field report (2026-08-23): `buda -v lShape1.buda` + `v` in the viewer crashed
TopologyExplorer.__init__ with `AttributeError: 'tuple' object has no
attribute 'x1'` — `_bundle_hanan_grid` (viz_explorer/analysis.py) assumed
`fp.get_block_rects()` returns Rect objects, but the binding returns
4-TUPLES; its single-rect fallback (`get_block_bounds`, a real Rect) is why
every single-rect design worked and every multi-rect one crashed the
explorer at draw time.  The `_crossed_blocks` helper 100 lines above already
used the correct tuple idiom.
"""
import contextlib
import io

import matplotlib
matplotlib.use("Agg")   # headless; no window

import pytest

import buda_cli
import buda_viz


@pytest.fixture(autouse=True)
def _close_figs():
    yield
    import matplotlib.pyplot as plt
    plt.close('all')


def _lshape_session():
    """flow/lShape1.buda's design, driven to the state the viewer opens in."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    for c in [
        "add_block src 500 150 600 250",
        "add_block L rect 0 0 100 400 rect 0 0 400 100 teg_mode over",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_net clk src.tx L.rx",
        "run_bundler STRICT",
        "generate_topologies_for_bundle clk src L",
        "run_planner",
        "run_nuts",
    ]:
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(c)
    return s


def test_explorer_opens_on_multi_rect_bundle(tmp_path):
    # Constructing the explorer runs _draw -> _bundle_hanan_grid, the exact
    # path the viewer's [Topos]/'v' button takes; before the fix this raised
    # in __init__ and the viewer had to be killed.
    s = _lshape_session()
    exp = buda_viz.TopologyExplorer(
        s.fp, s.bundles, sidecar_path=str(tmp_path / "sel.json"),
        layer_stack=s.layers)
    assert exp is not None


def test_bundle_hanan_grid_carries_every_rects_edges(tmp_path):
    # The grid must include EACH rect's edges (that is the method's whole
    # point — per-rect lines, not the union bbox), plus src's, plus the two
    # out-of-bounds detour lines per axis.
    s = _lshape_session()
    exp = buda_viz.TopologyExplorer(
        s.fp, s.bundles, sidecar_path=str(tmp_path / "sel.json"),
        layer_stack=s.layers)
    xs, ys = exp._bundle_hanan_grid()
    assert {0, 100, 400, 500, 600} <= set(xs)   # arm/base edges + src
    assert {0, 100, 400, 150, 250} <= set(ys)
