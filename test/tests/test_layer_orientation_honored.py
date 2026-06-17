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

"""def_layer's orientation must be honored in the UI, and run_nuts needs a plan.

Regression (flow/non_default_routing_orientation.flow): a script that overrides
the default metal orientation (e.g. M4 V instead of M4 H) showed the *default*
orientation in the layer panel/legend because labels came from a hardcoded
table. Separately, that flow omitted run_planner, so run_nuts silently placed 0
segments; it now reminds the user to plan first.
"""
import os
import sys

import matplotlib
matplotlib.use("Agg")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
import buda_cli  # noqa: E402
import buda_viz  # noqa: E402


def _session_inverted_layers():
    s = buda_cli.BudaSession()
    s.no_viz = True
    # Invert the defaults: M4 is normally H, M5 normally V.
    s.do_command("def_layer 4 M4 V TOP 10")
    s.do_command("def_layer 5 M5 H TOP 10")
    s.do_command("def_layer 6 M6 V TOP 10")
    return s


def test_layer_label_honors_stack_orientation():
    s = _session_inverted_layers()
    assert buda_viz._layer_label(4, s.layers) == "M4 V"   # overridden (default H)
    assert buda_viz._layer_label(5, s.layers) == "M5 H"   # overridden (default V)
    assert buda_viz._layer_label(6, s.layers) == "M6 V"


def test_layer_label_falls_back_without_stack():
    # No stack → default-orientation table.
    assert buda_viz._layer_label(4, None) == "M4 H"
    assert buda_viz._layer_label(5, None) == "M5 V"


def _run_flow_no_planner(s):
    for cmd in (
        "add_block b1 0 0 100 100",
        "add_block b2 200 200 300 300",
        "add_bus v[4] b1.o b2.i",
        "add_bus w[4] b2.i b1.o",
        "run_bundler strict",
        "generate_topologies",
        "run_nuts",                 # no run_planner!
    ):
        s.do_command(cmd)


def test_run_nuts_without_planner_reminds(capsys):
    s = _session_inverted_layers()
    _run_flow_no_planner(s)
    out = capsys.readouterr().out
    assert "run_planner" in out
    assert s.nuts_result is None          # skipped the empty placement


def test_run_nuts_with_no_bundles_reminds(capsys):
    s = _session_inverted_layers()
    s.do_command("run_nuts")              # nothing at all
    out = capsys.readouterr().out
    assert "run_bundler" in out


def test_planner_and_nuts_honor_orientation(capsys):
    # The pipeline itself already routes onto the right layers; confirm a clean
    # run with the inverted stack places segments with no overflow.
    s = _session_inverted_layers()
    for cmd in (
        "add_block b1 0 0 100 100",
        "add_block b2 200 200 300 300",
        "add_bus v[4] b1.o b2.i",
        "run_bundler strict",
        "generate_topologies",
        "run_planner",
        "run_nuts",
    ):
        s.do_command(cmd)
    assert s.nuts_result is not None
    assert s.nuts_result.num_violations == 0
