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

"""set_planner_param kSegs — the segment-count penalty (opt-in, default 0).

The kWL term scores  estimated_wirelength + kSegs * n_segments, demoting
many-segment trees whose junction vias (per bit) and realization DOF the
WL estimate omits.  The b61 geometry (flow/big_data_test/b61.buda): the
WL-cheapest candidate is a 10-segment TRUNK_H+MST at estWL 15940, but a
5-segment TRUNK_V+MST (estWL 17480, +9.7%) realizes only +2.2% detailed
WL with 56% fewer per-bit vias, and a 3-segment V+H+stub shape exists at
estWL 21110.  Corpus (kSegs=500): big2 unplaced 108->0 / overlaps 4->0 at
+1.8% detWL; bigHalf unplaced 593->214, overlaps 3->1, detWL -3.1%.
"""
import contextlib
import io

import pytest

import buda_cli

pytestmark = pytest.mark.mid

# The b61 repro geometry (flow/big_data_test/b61.buda) inline.
_B61 = (
    "def_layer 2 M2 H 25",
    "def_layer 3 M3 V 25",
    "def_layer 4 M4 H TOP 44.44",
    "def_layer 5 M5 V TOP 50.00",
    "def_layer 6 M6 H TOP 44.44",
    "def_layer 7 M7 V TOP 50.00",
    "add_block blk_01 10680 2310 13580 3310",
    "add_block blk_22 11380 4970 13580 6870",
    "add_block blk_24 9250 7990 10950 9090",
    "add_block blk_29 11580 3780 13580 4680",
    "add_block blk_31 200 7990 2700 9190",
    "add_block blk_32 4870 9430 7470 10630",
    "add_bus bus_034[16] blk_32.p blk_01.p,blk_29.p,blk_22.p,blk_24.p,blk_31.p",
    "run_bundler",
    "generate_topologies",
)


def _plan(ksegs):
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in _B61:
            s.do_command(c)
        if ksegs:
            s.do_command(f"set_planner_param kSegs {ksegs}")
        s.do_command("run_planner")
    w = s.bundles[0]
    t = w.input.candidates[w.plan.selected_topology_index]
    return t, s


def test_ksegs_param_recognized(capsys):
    s = buda_cli.BudaSession()
    s.no_viz = True
    for c in ("def_layer 4 M4 H TOP 10", "def_layer 5 M5 V TOP 10",
              "add_block a 0 0 10 10", "add_block b 20 0 30 10",
              "add_net n a.o b.i", "run_bundler", "generate_topologies",
              "set_planner_param kSegs 500", "run_planner"):
        s.do_command(c)
    assert "unknown param" not in capsys.readouterr().out


def test_ksegs_demotes_many_segment_trees():
    """Default: the WL-cheapest many-segment TRUNK+MST wins.  kSegs=500:
    the planner pays ~500 WL-units per segment and picks a topology with
    STRICTLY fewer segments at slightly higher estimated WL; the penalty
    is monotone (more kSegs never selects more segments)."""
    t0, _ = _plan(0)
    t500, _ = _plan(500)
    t2400, _ = _plan(2400)
    assert len(t0.segments) >= 9          # the WL-cheapest is a big tree
    assert len(t500.segments) < len(t0.segments)
    assert len(t500.segments) <= 6        # the compact-tree region
    assert t500.estimated_wirelength >= t0.estimated_wirelength
    # Monotone: cranking the penalty further never adds segments back.
    assert len(t2400.segments) <= len(t500.segments)
    # And at the high end the minimal 3-segment V+H+stub shape wins.
    assert len(t2400.segments) == 3


def test_ksegs_default_off_keeps_selection():
    """kSegs defaults to 0 — an un-set knob must not change the planner's
    choice (the WL-cheapest candidate keeps winning)."""
    t0, s = _plan(0)
    sel = s.bundles[0].plan.selected_topology_index
    assert sel == 0                       # pool is WL-sorted; cheapest wins
    assert t0.estimated_wirelength == min(
        c.estimated_wirelength for c in s.bundles[0].input.candidates)
