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

"""Issue #22 (behavioral consequence): a pinned bundle's arm that ends on a cut
must register congestion there, so a free bundle can't route straight through it.

Only bundle 2 is pinned (Z@x150@y200); bundle 1 is free.  Bundle 1's straight
L_HV arm overlaps bundle 2's pinned arm on the y=50 / x[50,150] channel
(24+24 units).  With the half-open cut test, B2's arm (ending at x=150) was
dropped, the y=50 band read 37 < cap, and B1 was placed straight through the
pinned route.  The closed interval makes the overlap visible (~74 > cap), so
B1 detours instead.
"""
import os
import sys
import io
import contextlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
import buda_cli  # noqa: E402


def _planner8():
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = (
        "def_layer 4 M4 V TOP 55.56", "def_layer 5 M5 H TOP 55.56", "def_layer 6 M6 V TOP 55.56",
        "def_track_pattern 6 0 POWER 2 1 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5 GROUND 2 1 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5",
        "def_track_pattern 5 0 POWER 2 1 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5 GROUND 2 1 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5",
        "add_block b1 0 0 50 50", "add_block b2 250 200 300 250",
        "add_bus v[16] b1.o b2.i", "add_bus w[16] b2.i b1.o",
        "run_bundler strict", "generate_topologies",
        "select_topology 2 3",          # ONLY bundle 2 pinned (Z@x150@y200); bundle 1 free
        "run_planner",
    )
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    return s


def test_pinned_arm_congestion_is_visible_to_free_bundle():
    s = _planner8()

    # Behavioral fix: the free bundle now SEES bundle 2's pinned arm at the
    # x=150 cut and detours, instead of routing straight (L_HV, topo index 1)
    # through the hidden demand.
    assert s.bundles[0].plan.selected_topology_index != 1, \
        "free bundle 1 routed straight (L_HV) through pinned bundle 2"

    # Because the free bundle detoured, the committed y=50 channel carries only
    # bundle 2's single arm and no longer overlaps: the planner avoided the
    # collision the under-count used to hide.
    y = list(s.planner.get_y_grid())
    cut = next(c for c in s.planner.get_cuts()
               if c.layer_id == 5 and 'VERT' in str(c.dir) and c.cut_coord == 150)
    use, cap = list(cut.band_usage), list(cut.band_cap)
    b = next(i for i in range(len(y) - 1) if y[i] <= 50 <= y[i + 1])
    assert use[b] <= cap[b], f"y=50 band still overflows: use={use[b]} cap={cap[b]}"
