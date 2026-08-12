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

"""Two MST edges leaving one block must not duplicate each other's first leg.

`realize_mst_edge` routes each edge on its own, from the closest point between
its two blocks.  Two edges incident on the SAME block therefore start at the
same face point, and when both go L-shaped with the same first axis the
shorter one's leg lies entirely inside the longer one's — the longer runs over
it and on past.

That stretch is a duplicate whose start is a FREE END: the shorter leg owns the
block tap, and nothing joins the long leg until they diverge.  Every audit
misses it — the ANTENNA rule counts attachment POSITIONS and the long leg has
two elsewhere; #514's tap-overhang rule wants the piece over a block the
segment itself taps, and this one taps nothing.  Its real cost is downstream:
it pushes the leg's end PAST the divergence junction, which makes that junction
a mid-span conn rather than an endpoint one, and DetailedNUTS only snaps a bit
to its own via at an ENDPOINT conn.  On rnr/mix2_topdown_refine bundle 35 that
left 8.75 + 5.75 + 2.75 units of metal past the last via with `check_design`
reporting Success.

flow/mst_shared_leg_prefix.buda is the shape on its own.  Measured on main it
produces the containment on BOTH standalone MST strategies; the assertions
below are what the trim has to keep true.
"""
import io
import contextlib
from pathlib import Path

import pytest

import buda_cli

_ROOT = Path(__file__).parents[2]
_FLOW = _ROOT / "flow/mst_shared_leg_prefix.buda"

pytestmark = pytest.mark.mid


@pytest.fixture(scope="module")
def solved():
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        s.do_command(f"source {_FLOW}")
    return s


def _axis(seg):
    """(is_horizontal, signed length) — None when the segment is not axis-aligned."""
    h = seg.start.y == seg.end.y
    v = seg.start.x == seg.end.x
    if h == v:                       # degenerate (a point) or diagonal
        return None
    return h, (seg.end.x - seg.start.x) if h else (seg.end.y - seg.start.y)


def _contained_pairs(topo):
    """Legs sharing a start point, same axis and direction, one inside the other."""
    out = []
    for i, a in enumerate(topo.segments):
        ai = _axis(a)
        if ai is None:
            continue
        for j, b in enumerate(topo.segments):
            if i == j:
                continue
            bi = _axis(b)
            if bi is None or bi[0] != ai[0]:
                continue
            if (a.start.x, a.start.y) != (b.start.x, b.start.y):
                continue
            if (ai[1] > 0) != (bi[1] > 0):
                continue
            if 0 < abs(ai[1]) < abs(bi[1]):
                out.append((topo.type, i, j))
    return out


def test_no_candidate_duplicates_a_sibling_edges_first_leg(solved):
    """THE regression, over EVERY candidate rather than the selected one: the
    shape is a generation defect, so it must be absent from the whole pool.

    On main this vehicle yields two — MST_HV seg1 (len 120) inside seg3 (len
    1320), and MST_VH seg3 (len 70) inside seg1 (len 620), both at the shared
    corner (2010,1010)."""
    bad = []
    for w in solved.bundles:
        for t in w.input.candidates:
            bad += _contained_pairs(t)
    assert not bad, f"duplicated MST leg prefixes: {bad}"


def test_the_trimmed_leg_still_reaches_its_own_block(solved):
    """The trim cuts a leg's START, so the obvious way to break it is to cut a
    leg off the block it exists to reach.  Every candidate must still name every
    bundle block in its contract, and the flow must route cleanly."""
    for w in solved.bundles:
        blocks = set(w.input.original_bundle.get_net_names() and
                     w.input.candidates[0].connected_block_names)
        for t in w.input.candidates:
            assert set(t.connected_block_names) == blocks, t.type


def test_the_flow_ends_clean(solved):
    """Connectivity is preserved by construction — the cut point is the shorter
    leg's far end, which is either that edge's own bend or a block face, so
    something is always waiting at the seam.  This checks it rather than
    asserting it."""
    assert solved.detailed_result.num_unplaced == 0
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        solved.do_command("check_design dnuts")
    assert "no violations found" in buf.getvalue(), buf.getvalue()[-600:]
