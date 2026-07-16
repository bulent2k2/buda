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

"""C-triple detour (flow/c_ddd_detour.buda): the right-edge H stubs of the
dd-detour are replaced by two CHANNEL trunks at explicit mid-channel rows
(y=850/550 — NOT Hanan lines; the channel between `lo` and `up` is empty),
each W-restricted (edit_set_slide) to its half of the channel, with V stubs
dropping from `up`'s lower edge and rising from `lo`'s upper edge."""
import contextlib
import io
import math
from pathlib import Path

import pytest

import buda
import buda_cli

pytestmark = pytest.mark.mid

_FLOW = Path(__file__).parents[2] / "flow" / "c_ddd_detour.buda"


@pytest.fixture(scope="module")
def flow():
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for line in _FLOW.read_text().splitlines():
            c = line.split("#", 1)[0].strip()
            if c:
                s.do_command(c)
    return s


def _selected(s):
    w = s.bundles[0]
    return w, w.input.candidates[w.plan.selected_topology_index]


def test_ddd_detour_is_pinned_user_with_9_segments(flow):
    w, topo = _selected(flow)
    assert topo.type == "USER"
    assert w.input.topology_pinned
    assert len(topo.segments) == 9   # outer C (3) + 2 right V + 2 channel + 2 stubs


def test_ddd_detour_routes_clean(flow):
    assert flow.nuts_result.num_overlaps == 0
    assert flow.nuts_result.num_violations == 0
    assert flow.detailed_result.num_unplaced == 0
    assert len([n for n in flow.detailed_result.net_segments]) == 36  # 9×4


def test_channel_trunks_off_grid_with_half_channel_windows(flow):
    """The point of the ddd level: two H trunks at EXPLICIT mid-channel rows
    (850/550 — no Hanan line exists in the empty channel), their slide
    windows restricted to disjoint halves via edit_set_slide, honored on the
    plan and by NUTS placement."""
    w, topo = _selected(flow)
    rows = sorted(sg.start.y for sg in topo.segments
                  if sg.start.y == sg.end.y and 400 < sg.start.y < 1000)
    assert rows == [550, 850]                     # mid-channel, off-grid
    # The plan carries the two disjoint half-channel windows.
    wins = {i: (lo, hi) for i, (lo, hi)
            in enumerate(zip(w.plan.seg_slide_lo, w.plan.seg_slide_hi))
            if not math.isnan(lo)}
    assert sorted(wins.values()) == [(420.0, 700.0), (700.0, 980.0)]
    # NUTS placed each trunk INSIDE its window (no collapse onto one row).
    placed = sorted(g.track_position for g in flow.nuts_result.segments
                    if g.horiz and 400 < g.track_position < 1000)
    assert len(placed) == 2
    assert 420 <= placed[0] <= 700 <= placed[1] <= 980


def test_stubs_leave_the_facing_block_edges(flow):
    """The V stubs use the channel-facing edges: from `up`'s LOWER edge
    (y=1000) down to the upper trunk, from `lo`'s UPPER edge (y=400) up to
    the lower trunk."""
    _, topo = _selected(flow)
    taps = {bt.block_name: si
            for si, eps in topo.seg_busterms.items()
            for bt in eps if bt is not None}
    up_stub = topo.segments[taps["up"]]
    lo_stub = topo.segments[taps["lo"]]
    assert sorted((up_stub.start.y, up_stub.end.y)) == [850, 1000]
    assert sorted((lo_stub.start.y, lo_stub.end.y)) == [400, 550]
    # Whole graph is one island (junction-record connectivity).
    ct = buda.ConnTopology()
    ct.build(topo, flow.fp)
    kinds = {str(v.kind).split('.')[-1]
             for v in buda.check_topo(ct, topo, flow.fp, 1).violations}
    assert not kinds
