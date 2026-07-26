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

"""Slide-aware block-coverage gate — issue #438.

A trunk whose locus lands EXACTLY on a receiver block's boundary face "covers"
it only as an edge-graze.  `check_topo`'s block-coverage test used the nominal
`perp_pos` with a boundary-inclusive span test, so the graze counted as a
pass-through and `filter_uncovered` kept the fragile candidate — but the
covering segment's slide window need not include the graze line (the min-stub
floor / junction constraints can push it entirely to one side), so NUTS seats
the trunk off the face and the block's bits open.

Exact geometry from tc3a bundle 68 (`DRV:blk_35 | REC:blk_10,blk_15,blk_25`),
selected candidate `TRUNK_H+MST@y180`:
  seg0  H (476,180)->(826,180)   taps blk_35 (driver) + blk_25
  seg1  V (809,180)->(809,693)   GRAZES blk_10's left face x=809 (blk_10 spans
                                 x[809,969]); its own endpoints never touch
                                 blk_10's y-span [305,395], so it taps nothing
  seg2  H (809,693)->(720,693)   taps blk_15
blk_10 is listed in connected_block_names but is only edge-grazed.  With a
min-stub floor its junction with seg2 caps seg1's slide window at 806, so the
window [740,806] can NEVER reach x=809 — the block is unreachable and must be
flagged BUSTERM_OPEN (so the fragile candidate is dropped and a robust
interior-pass-through sibling wins).  When the window CAN reach the graze line
(no min-stub floor), the cover is legitimate and must NOT be flagged.
"""
import buda

_BLOCKS = {
    "blk_35": (266, 20, 476, 210),   # driver
    "blk_10": (809, 305, 969, 395),  # receiver grazed by the V trunk's left edge
    "blk_15": (490, 693, 720, 873),  # receiver tapped by seg2
    "blk_25": (826, 20, 956, 180),   # receiver tapped by seg0
}


def _seg(x1, y1, x2, y2, hint):
    g = buda.Segment()
    g.start = buda.Point(x1, y1)
    g.end = buda.Point(x2, y2)
    g.layer_hint = hint
    return g


def _build(min_stub):
    fp = buda.Floorplan()
    fp.set_min_stub_length(min_stub)
    for name, (x1, y1, x2, y2) in _BLOCKS.items():
        fp.add_block(name, x1, y1, x2, y2)
    t = buda.Topology()
    t.type = "TRUNK_H+MST@y180"
    t.segments = [
        _seg(476, 180, 826, 180, 6),   # seg0 H spine
        _seg(809, 180, 809, 693, 7),   # seg1 V trunk grazing blk_10's edge
        _seg(809, 693, 720, 693, 6),   # seg2 H stub -> blk_15
    ]
    t.connected_block_names = ["blk_35", "blk_10", "blk_15", "blk_25"]
    buda.annotate_topology(t, fp)
    ct = buda.ConnTopology()
    ct.build(t, fp)
    res = buda.check_topo(ct, t, fp, 68)
    opens = {(str(v.kind).split(".")[-1], v.block_name) for v in res.violations}
    # slide window of the grazing V trunk (seg index 1)
    seg1 = ct.segs()[1]
    return opens, (seg1.perp_pos, seg1.perp_lo, seg1.perp_hi)


def test_unreachable_graze_is_flagged_open():
    """With a min-stub floor the grazing trunk's window [740,806] cannot reach
    blk_10's left face x=809 — the block is really uncovered."""
    opens, (perp_pos, lo, hi) = _build(min_stub=20)
    # sanity: the graze nominal sits OUTSIDE its own feasible window
    assert perp_pos == 809 and (lo, hi) == (740, 806), (perp_pos, lo, hi)
    assert ("BUSTERM_OPEN", "blk_10") in opens, opens


def test_reachable_graze_is_not_flagged():
    """With no min-stub floor the window still reaches x=809 (lo==809), so the
    graze is a legitimate cover and must NOT be flagged — the gate is precise,
    not a blanket boundary-graze rejection."""
    opens, (perp_pos, lo, hi) = _build(min_stub=0)
    assert lo <= 809 <= hi, (perp_pos, lo, hi)
    assert ("BUSTERM_OPEN", "blk_10") not in opens, opens
