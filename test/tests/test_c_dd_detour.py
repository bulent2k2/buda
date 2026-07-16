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

"""C-double-double detour (flow/c_dd_detour.buda): one more detour level on
top of the C — two OOB V trunks to the RIGHT of the blocks on the SAME Hanan
line with DISJOINT spans (up & above / lo & below), H stubs from each block,
and the outer H trunks auto-connecting to the right trunks via shared
endpoints (no edit_connect).  Guards the flow end-to-end and the same-line
disjoint-trunk pattern."""
import contextlib
import io
from pathlib import Path

import pytest

import buda
import buda_cli

pytestmark = pytest.mark.mid

_FLOW = Path(__file__).parents[2] / "flow" / "c_dd_detour.buda"


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


def test_dd_detour_is_pinned_user_with_7_segments(flow):
    w, topo = _selected(flow)
    assert topo.type == "USER"
    assert w.input.topology_pinned
    assert len(topo.segments) == 7   # 2 H trunks + spine + 2 right V + 2 stubs


def test_dd_detour_routes_clean(flow):
    assert flow.nuts_result.num_overlaps == 0
    assert flow.nuts_result.num_violations == 0
    assert flow.detailed_result.num_unplaced == 0
    assert len([n for n in flow.detailed_result.net_segments]) == 28  # 7×4


def test_two_right_trunks_share_line_with_disjoint_spans(flow):
    """The pattern the dd-detour exists for: two V trunks on x=500, one
    spanning `up` and above, one `lo` and below — disjoint."""
    _, topo = _selected(flow)
    right = [sorted((sg.start.y, sg.end.y)) for sg in topo.segments
             if sg.start.x == sg.end.x == 500]
    assert len(right) == 2
    (a_lo, a_hi), (b_lo, b_hi) = sorted(right)
    assert a_hi <= b_lo                       # disjoint on the shared line
    assert (a_lo, a_hi) == (-300, 100)        # lo & below
    assert (b_lo, b_hi) == (1300, 1700)       # up & above


def test_h_trunks_auto_connect_to_right_trunks(flow):
    """Shared endpoints at (500, ±) are recorded as junctions by
    annotate_seg_conns — the H trunks connect to the right V trunks with no
    edit_connect, and the whole graph is ONE island (no DISCONNECTED)."""
    w, topo = _selected(flow)
    ct = buda.ConnTopology()
    ct.build(topo, flow.fp)
    segs = list(ct.segs())
    # Each H trunk must reference a V segment at x=500 among its conns.
    for i, cs in enumerate(segs):
        if not cs.horiz:
            continue
        if (cs.along_lo, cs.along_hi) != (-700, 500):
            continue                          # the stubs' H pieces, if any
        partners = {c.seg_idx for c in cs.conns
                    if c.kind == buda.SegConnKind.SEG}
        assert any(topo.segments[j].start.x == topo.segments[j].end.x == 500
                   for j in partners), f"H trunk seg {i} missed its right trunk"
    kinds = {str(v.kind).split('.')[-1]
             for v in buda.check_topo(ct, topo, flow.fp, 1).violations}
    assert 'DISCONNECTED' not in kinds
    assert not kinds                          # fully clean, in fact
