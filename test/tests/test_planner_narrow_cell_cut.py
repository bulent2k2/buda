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

"""Issue #22 follow-up (PR #23 review): the closed-interval cut match must use
the EXACT midpoint, not the integer-truncated one.

Cuts sit at cell midpoints `(g_lo + g_hi) / 2` (integer division).  For a
width-1 Hanan cell `[g, g+1]` the true midpoint g+0.5 truncates onto the lower
grid line g, collapsing the cut onto the cell boundary.  With a naive closed
test `cut_coord <= hi` a segment from the PREVIOUS cell ending exactly at x=g
would then falsely charge the `[g, g+1]` cut, even though it never enters that
cell.  Matching in doubled coordinates (cut_coord_2x vs 2*lo / 2*hi) keeps the
half-integer midpoint exact and rejects that false charge.

Layout: b1 (source) and b2 (dest) with a tiny block b3 injecting grid lines
x=61,62 so [60,61] is a width-1 cell whose midpoint collapses onto x=60.  The
pinned L_HV's horizontal arm runs (40,20)->(60,20): it ends exactly at x=60 and
turns vertical, never crossing into [60,61].  Pre-fix that arm charged Vcut@60
(14.33 in the y~=20 band); fixed, Vcut@60 carries nothing.
"""
import os
import sys
import io
import contextlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
import buda_cli  # noqa: E402


def _session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = (
        "def_layer 4 M4 H TOP 10", "def_layer 5 M5 V TOP 10",
        "add_block b1 0 0 40 20", "add_block b2 60 200 100 220",
        "add_block b3 61 100 62 101",      # inject x=61,62 -> width-1 cell [60,61]
        "add_bus v[8] b1.o b2.i",
        "run_bundler strict", "generate_topologies",
        "select_topology 1 1",             # pin L_HV (idx 0): arm (40,20)->(60,20)
        "run_planner",
    )
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    return s


def _vcut(s, coord):
    return next((c for c in s.planner.get_cuts()
                 if 'VERT' in str(c.dir) and c.layer_id == 4 and c.cut_coord == coord),
                None)


def test_width1_cell_cut_not_falsely_charged():
    s = _session()
    xg = list(s.planner.get_x_grid())
    # Sanity: the layout really produced the collapsing width-1 cell [60,61].
    assert 60 in xg and 61 in xg and xg[xg.index(60) + 1] == 61

    # The arm spans x[40,60]: it genuinely crosses the wide cell [40,60]
    # (midpoint 50), so that cut must be charged.
    wide = _vcut(s, 50)
    assert wide is not None and max(wide.band_usage) > 0, \
        "arm not counted on the cell it actually crosses"

    # It ENDS at x=60 (turns vertical) and never enters [60,61], whose midpoint
    # collapses onto x=60.  That cut must carry nothing — the bug charged it.
    narrow = _vcut(s, 60)
    assert narrow is not None, "no V-cut at the collapsed width-1 midpoint x=60"
    assert max(narrow.band_usage) == 0, \
        f"width-1 cell [60,61] falsely charged: use={list(narrow.band_usage)}"
