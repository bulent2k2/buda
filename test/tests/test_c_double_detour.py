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

"""C-double-detour: the hand-built "large C" flow (flow/c_double_detour.buda)
that showcases the TopoEdit grid-line span pin (P).

Guards the whole vehicle end-to-end — the topology builds via the edit_* ops,
routes cleanly through NUTS + DetailedNUTS — and locks in the three findings
from the review:
  * the two H trunks retract onto their stubs (no over-cell overhang),
  * net_pull is derived: the trunk/spine segments carry a pull, the two
    perpendicular V stubs correctly carry none,
  * the H trunks connect to the V spine via the shared-endpoint junction
    annotation (ConnTopology reads seg_conns; no edit_connect needed).
"""
import contextlib
import io
from pathlib import Path

import pytest

import buda
import buda_cli

pytestmark = pytest.mark.mid

_FLOW = Path(__file__).parents[2] / "flow" / "c_double_detour.buda"


def _run_flow():
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for line in _FLOW.read_text().splitlines():
            c = line.split("#", 1)[0].strip()
            if c:
                s.do_command(c)
    return s


@pytest.fixture(scope="module")
def flow():
    return _run_flow()


def _selected(s):
    w = s.bundles[0]
    return w, w.input.candidates[w.plan.selected_topology_index]


def test_c_detour_is_the_pinned_user_candidate(flow):
    w, topo = _selected(flow)
    assert topo.type == "USER"
    assert w.input.topology_pinned
    assert len(topo.segments) == 5          # 2 H trunks + V spine + 2 V stubs


def test_c_detour_routes_clean(flow):
    """The whole point: a valid, physically self-connected C that NUTS and
    DetailedNUTS place with no overlaps and no stranded bits."""
    assert flow.nuts_result.num_overlaps == 0
    assert flow.nuts_result.num_violations == 0
    assert flow.detailed_result.num_unplaced == 0
    # 5 segments x 4 bits = 20 bit-wires.
    assert len([n for n in flow.detailed_result.net_segments]) == 20


def _conn_segs(s):
    w, topo = _selected(s)
    ct = buda.ConnTopology()
    ct.build(topo, s.fp)
    return list(ct.segs())


def test_h_trunks_retracted_onto_their_stubs(flow):
    """Issue #2: each H trunk stops at the stub column (x=200, the block
    centre), not the full block width — no dangling over-cell overhang."""
    segs = _conn_segs(flow)
    # The two horizontal segments are the trunks; both span x[-700, 200].
    trunks = [cs for cs in segs if cs.horiz]
    assert len(trunks) == 2
    for cs in trunks:
        assert (cs.along_lo, cs.along_hi) == (-700, 200)
    # The V stubs land at x=200 — exactly the trunks' retracted right end.
    stubs = [cs for cs in segs if not cs.horiz and cs.perp_pos == 200]
    assert len(stubs) == 2


def test_net_pull_pulls_the_c_toward_the_block_edge(flow):
    """net_pull tightens the detour toward the blocks.  An OOB trunk created via
    edit_add_trunk is seeded with the half-open Hanan cell as its slide range
    (perp_clamp), so the V spine is bounded at the block's left edge (x=0): its
    +pull then has a finite target AND, because derive_net_pull reads that
    bounded interval, the two V stubs — which sit past the spine — register a
    -pull toward the block edge, shortening the H trunks.  Every segment pulls;
    nothing is left with a dead (unbounded) pull."""
    segs = _conn_segs(flow)
    by = {(cs.horiz, cs.perp_pos): cs for cs in segs}
    spine = by[(False, -700)]
    assert spine.perp_hi == 0 and spine.net_pull > 0   # bounded to the block edge
    # Both V stubs (x=200, past the spine) pull toward the low side (block edge).
    stubs = [cs for cs in segs if not cs.horiz and cs.perp_pos == 200]
    assert len(stubs) == 2
    assert all(cs.net_pull < 0 for cs in stubs)        # -1: shorten the trunks
    # No segment is left with a nonzero pull and no finite target on its pull
    # side (that is the dead-pull bug this fixes).
    for cs in segs:
        if cs.net_pull > 0:
            assert cs.perp_hi < 5 * 10**8               # finite hi target
        if cs.net_pull < 0:
            assert cs.perp_lo > -5 * 10**8              # finite lo target


def test_h_trunks_connect_to_v_spine(flow):
    """Issue #3: the H trunks connect to the V spine because each trunk's left
    endpoint lands on the spine — recorded by annotate_seg_conns and read by
    ConnTopology (no edit_connect needed).  The spine (the only free-standing
    vertical segment with no busterm) must reference BOTH trunks."""
    segs = _conn_segs(flow)
    spine = next(cs for cs in segs if not cs.horiz
                 and all(c.kind != buda.SegConnKind.BUSTERM for c in cs.conns))
    trunk_ids = {i for i, cs in enumerate(segs) if cs.horiz}
    spine_seg_conns = {c.seg_idx for c in spine.conns
                       if c.kind == buda.SegConnKind.SEG}
    assert trunk_ids <= spine_seg_conns     # spine reaches both H trunks
