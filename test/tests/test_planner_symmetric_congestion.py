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

"""Issue #22: a mirror-symmetric layout must produce symmetric band usage.

Two bundles pinned to mirror-image Z topologies meet at the shared Hanan
midpoint x=150.  Each mirror band (y~=50 and y~=200) carries B1+B2 = equal H
demand, so the x=150 vertical cut must report equal usage on both.

Regression: `for_each_band` used a half-open `[lo, hi)` cut test, so the arm
that *ends* at x=150 was dropped while the arm that *starts* there was counted.
The y~=50 band silently lost its demand (use=[0,0,0,74,0]).  The closed
interval counts both mirror arms.
"""
import os
import sys
import io
import contextlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
import buda_cli  # noqa: E402


def _planner7():
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = (
        "def_layer 4 M4 V TOP 55.56", "def_layer 5 M5 H TOP 55.56", "def_layer 6 M6 V TOP 55.56",
        "def_track_pattern 6 0 POWER 2 1 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5 GROUND 2 1 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5",
        "def_track_pattern 5 0 POWER 2 1 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5 GROUND 2 1 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5",
        "add_block b1 0 0 50 50", "add_block b2 250 200 300 250",
        "add_bus v[16] b1.o b2.i", "add_bus w[16] b2.i b1.o",
        "run_bundler strict", "generate_topologies",
        "select_topologies 1,2 3",   # both pinned to mirror Z_HVH (topo 3)
        "run_planner",
    )
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    return s


def _vcut150_on_m5(s):
    return next(c for c in s.planner.get_cuts()
               if c.layer_id == 5 and 'VERT' in str(c.dir) and c.cut_coord == 150)


def test_symmetric_layout_has_symmetric_band_usage():
    s = _planner7()
    c = _vcut150_on_m5(s)
    use = list(c.band_usage)
    # Mirror bands (y~=50 at index 1, y~=200 at index 3) carry equal H demand.
    assert use[1] == use[3], f"asymmetric band usage on Vcut@150: {use}"
    # Both mirror bands carry two arms and overflow their cap=50.
    assert use[1] > c.cap(1), f"y~=50 band under-counted: use={use[1]} cap={c.cap(1)}"
