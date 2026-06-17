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

"""When a PINNED bundle overflows, the planner can't pick a 'least-cost'
candidate — it must commit the one pinned topology. The warning must say so.

Regression (flow/planner7_noop.buda): the planner logged "committing least-cost
with overflow=N" for a pinned, overflowing bundle, which is misleading since a
pinned bundle has no candidate choice.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
import buda_cli  # noqa: E402


def _two_bundles_pinned_to_conflicting_topo():
    s = buda_cli.BudaSession()
    s.no_viz = True
    # Flipped-orientation top layers with dense track patterns so a 16-bit bus
    # is wide enough to overflow a shared channel.
    for c in (
        "def_layer 4 M4 V TOP 55.56",
        "def_layer 5 M5 H TOP 55.56",
        "def_layer 6 M6 V TOP 55.56",
        "def_track_pattern 6 0 POWER 2 1 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5 "
        "SIGNAL 1 0.5 GROUND 2 1 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5",
        "def_track_pattern 5 0 POWER 2 1 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5 "
        "SIGNAL 1 0.5 GROUND 2 1 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5",
        "add_block b1 0 0 50 50",
        "add_block b2 250 200 300 250",
        "add_bus v[16] b1.o b2.i",
        "add_bus w[16] b2.i b1.o",
        "run_bundler strict",
        "generate_topologies",
        "select_topologies 1,2 3",   # both pinned to the same conflicting Z topo
        "run_planner",
    ):
        s.do_command(c)
    return s


def test_pinned_overflow_message_is_not_least_cost(capfd):
    _two_bundles_pinned_to_conflicting_topo()
    out = capfd.readouterr().out
    # A pinned bundle overflowed — the warning must name the pinned topology, not
    # claim a least-cost candidate choice that doesn't exist.
    assert "pinned topology overflows" in out
    assert "committing the pinned topology" in out
    assert "committing least-cost" not in out
