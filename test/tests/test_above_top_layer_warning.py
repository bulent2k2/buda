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

"""Config-smell warning: a metal declared non-TOP but sitting ABOVE the TOP
band (opens.md item 11, wishlist-planner "A metal above the TOP band is
still a top metal").

`LayerType` is a hand-labelled binary flag with no notion of stack
position, so a metal declared above the highest TOP layer but not marked
TOP (e.g. M7 over M5/M6 TOP) reads to the planner's `base_cost_non_top`
penalty as a cheap non-TOP stub-offload target — physically backwards.
The planner warns at `build_congestion_map` so the mis-label is visible;
the fix is to mark the layer TOP in `def_layer` (measured a win across the
hbundles suite when `tracks.buda`'s M7 was corrected).
"""
import io
import contextlib

import buda
import buda_cli


def _plan_out(m7_top):
    """Two TOP layers M5/M6 with M7 declared above them, TOP or not; return
    the planner stdout (the warning is C++ cout, so route it through
    buda.ostream_redirect into the Python capture)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), buda.ostream_redirect():
        for c in ["def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
                  "def_layer 6 M6 H TOP 50",
                  f"def_layer 7 M7 V {'TOP ' if m7_top else ''}50",
                  "add_block A 0 0 100 100", "add_block B 0 400 100 500",
                  "add_net n0 A.p B.p", "run_bundler strict",
                  "generate_topologies", "run_planner"]:
            s.do_command(c)
    return buf.getvalue()


def test_warns_when_non_top_layer_sits_above_top_band():
    """M7 declared non-TOP above M5/M6 TOP → the planner flags the mis-label
    and names the offending layer."""
    out = _plan_out(m7_top=False)
    assert "ABOVE the TOP band" in out
    assert "M7" in out
    # and it points at the fix
    assert "Mark them TOP" in out


def test_no_warning_when_declared_top():
    """M7 marked TOP → no config-smell warning (correct declaration)."""
    out = _plan_out(m7_top=True)
    assert "ABOVE the TOP band" not in out


def test_no_warning_without_above_top_layer():
    """A plain LOW-below-TOP stack (M4 LOW under M5/M6 TOP) must not warn —
    a layer below the TOP band is a genuine low layer."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), buda.ostream_redirect():
        for c in ["def_layer 2 M2 H 50", "def_layer 5 M5 V TOP 50",
                  "def_layer 6 M6 H TOP 50",
                  "add_block A 0 0 100 100", "add_block B 300 0 400 100",
                  "add_net n0 A.p B.p", "run_bundler strict",
                  "generate_topologies", "run_planner"]:
            s.do_command(c)
    assert "ABOVE the TOP band" not in buf.getvalue()
