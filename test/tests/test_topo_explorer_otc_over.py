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

"""The TopologyExplorer j/k segment banner's `otc-over:` field is opt-in.

The over-the-cell fly-over list (unrelated blocks a wire merely passes over) is
often long and rarely relevant to topo design, so it is hidden by default —
the banner shows a terse `otc:N` count — and 'o' toggles the full
`otc-over: names`.  The topo-relevant `low-cross:` field is unaffected.
"""
import contextlib
import io
import os
import sys
from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg")   # headless; no window

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
import buda_cli   # noqa: E402
import buda_viz   # noqa: E402


def _session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = (
        "def_layer 4 M4 H TOP 0", "def_layer 5 M5 V TOP 0",
        "add_block b1 0 0 100 200", "add_block b2 900 0 1000 200",
        # An unrelated block spanning the mid channel: the b1->b2 trunk flies
        # over it (benign over-the-cell routing = an `otc-over` entry).
        "add_block MID 400 60 500 140",
        "add_bus v[4] b1.o b2.i", "run_bundler", "generate_topologies",
    )
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    return s


def _otc_lines(exp):
    """Every segment banner across all candidates that mentions an otc field."""
    w = exp.wrappers[exp.bidx]
    out = []
    with contextlib.redirect_stdout(io.StringIO()):
        for ci in range(len(w.input.candidates)):
            exp.idx = ci
            topo = exp._shown_topo()
            cs_list = list(exp._build_conn_topo(topo).segs())
            for si, cs in enumerate(cs_list):
                line = exp._seg_conn_line(cs, si)
                if "otc" in line:
                    out.append(line)
    return out


def _key(exp, key):
    exp._on_key(SimpleNamespace(key=key, xdata=None, ydata=None))


def test_otc_over_hidden_by_default_shows_count():
    exp = buda_viz.TopologyExplorer(_session().fp,
                                    _session().bundles, layer_stack=_session().layers)
    lines = _otc_lines(exp)
    assert lines, "scenario should produce at least one over-the-cell fly-over"
    # Default: terse count, never the full name list.
    assert all("otc-over:" not in l for l in lines)
    assert any("otc:" in l for l in lines)


def test_o_key_toggles_full_otc_over_names():
    s = _session()
    exp = buda_viz.TopologyExplorer(s.fp, s.bundles, layer_stack=s.layers)
    assert exp._show_otc_over is False

    _key(exp, "o")                       # toggle ON
    assert exp._show_otc_over is True
    on = _otc_lines(exp)
    assert any("otc-over: MID" in l for l in on)
    assert all("otc:" not in l.replace("otc-over:", "") for l in on)

    _key(exp, "o")                       # toggle OFF again
    assert exp._show_otc_over is False
    off = _otc_lines(exp)
    assert all("otc-over:" not in l for l in off)
    assert any("otc:" in l for l in off)
