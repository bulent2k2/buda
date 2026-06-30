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

"""Spec: a trunk touches an endpoint block without an *artificial* edge tap.

A trunk (TRUNK_V here) that connects an endpoint block B stays connected as long
as its contact with B cannot slide off B.  The conn-segs attached to the trunk
decide this, along the trunk's axis (y for a V trunk):

- **Straddle** — the conn-segs pull the trunk's contact in *opposing* directions
  (their span already overlaps B), so the contact is pinned inside B.  The trunk
  touches B by overlap; we must **not** extend the spine to B's top/bottom face to
  manufacture a tap.  (b34_bus_028: blk_15 below the junction pulls down, blk_32
  above pulls up.)
- **One-directional** — all conn-segs pull the *same* way, off B through one edge.
  The contact would separate, so the spine extends to B's **near edge** (the one
  the pull exits through) and taps it.  Tapping any *other* edge is wrong.

For a vertical trunk the along-axis edges are B's TOP and BOTTOM; LEFT/RIGHT taps
come from the horizontal stubs landing on faces and are incidental here.  These
tests pin the tap-edge decision so we can drop the artificial-tap bug without
regressing the cases where a near-edge tap is genuinely required.
"""
import pytest
import buda


def _fp(blocks):
    f = buda.Floorplan()
    for n, (x1, y1, x2, y2) in blocks.items():
        f.add_block(n, x1, y1, x2, y2)
    return f


def _trunk_inside(fp, drv, rcvs, Bname, axis):
    """The single non-MST trunk of the given axis whose spine is inside B.

    axis='V': vertical trunk, trunk_location is x, compared to B's x-extent.
    axis='H': horizontal trunk, trunk_location is y, compared to B's y-extent.
    """
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(4, 5)
    B = fp.get_block_bounds(Bname)
    want = "TRUNK_V" if axis == "V" else "TRUNK_H"
    lo, hi = (B.x1, B.x2) if axis == "V" else (B.y1, B.y2)
    hits = []
    for c in g.generate_candidates(drv, rcvs):
        if not c.type.startswith(want) or "MST" in c.type:
            continue
        if lo <= c.trunk_location <= hi:
            hits.append(c)
    assert hits, f"no in-B {want} candidate for {Bname}"
    return hits[0], B


def _v_trunk_inside(fp, drv, rcvs, Bname):
    return _trunk_inside(fp, drv, rcvs, Bname, "V")


def _faces_tapped(c, B):
    """Faces of B that carry a segment endpoint (a tap)."""
    tapped = set()
    for s in c.segments:
        for p in (s.start, s.end):
            if p.x == B.x1 and B.y1 <= p.y <= B.y2:
                tapped.add("LEFT")
            if p.x == B.x2 and B.y1 <= p.y <= B.y2:
                tapped.add("RIGHT")
            if p.y == B.y1 and B.x1 <= p.x <= B.x2:
                tapped.add("BOTTOM")
            if p.y == B.y2 and B.x1 <= p.x <= B.x2:
                tapped.add("TOP")
    return tapped


def _overlaps(c, B):
    for s in c.segments:
        sx1, sy1 = min(s.start.x, s.end.x), min(s.start.y, s.end.y)
        sx2, sy2 = max(s.start.x, s.end.x), max(s.start.y, s.end.y)
        if not (sx2 <= B.x1 or sx1 >= B.x2 or sy2 <= B.y1 or sy1 >= B.y2):
            return True
    return False


# --- Straddle (opposing pulls): no artificial top/bottom tap -----------------

# blk_15 (driver) sits below the y=4615 junction; blk_32 (receiver) above it.
# Their stubs pull the trunk's contact in opposing y-directions, so it stays
# inside blk_00 (y[3780,4775]).  The trunk should touch blk_00 by overlap with NO
# tap on blk_00's top or bottom face.
_STRADDLE = {
    "blk_00": (1250, 3780, 2230, 4775),
    "blk_15": (2230, 3365, 3500, 4615),
    "blk_32": (2230, 4615, 3500, 5350),
}


@pytest.mark.xfail(reason="Artificial TOP tap still emitted in the straddle case "
                          "(to be fixed: opposing pulls => no edge tap, let it be).",
                   strict=True)
def test_straddle_no_artificial_top_or_bottom_tap():
    c, B = _v_trunk_inside(_fp(_STRADDLE), "blk_15", ["blk_32", "blk_00"], "blk_00")
    assert _overlaps(c, B), "trunk should touch blk_00 by overlap"
    taps = _faces_tapped(c, B)
    assert "TOP" not in taps and "BOTTOM" not in taps, (
        f"opposing pulls pin the trunk inside blk_00 — no along-axis edge tap "
        f"should be manufactured, got taps={sorted(taps)}"
    )


# --- One-directional pull UP: tap B's TOP (near) edge ------------------------

# blk_00 (receiver) is at the bottom; blk_15 (driver) and blk_32 (receiver) are
# both above it, so both stubs pull up and away.  The trunk must reach DOWN to
# blk_00's top (near) edge and tap it — and only that edge.
_PULL_UP = {
    "blk_00": (1250, 3000, 2230, 3800),
    "blk_15": (2230, 3800, 3500, 4600),
    "blk_32": (2230, 4600, 3500, 5400),
}


def test_pull_up_taps_top_near_edge():
    c, B = _v_trunk_inside(_fp(_PULL_UP), "blk_15", ["blk_32", "blk_00"], "blk_00")
    taps = _faces_tapped(c, B)
    assert "TOP" in taps, f"one-directional up-pull must tap blk_00's TOP (near) edge, got {sorted(taps)}"
    assert "BOTTOM" not in taps, f"must not tap the far (BOTTOM) edge, got {sorted(taps)}"


# --- One-directional pull DOWN: tap B's BOTTOM (near) edge -------------------

# blk_00 (receiver) is at the top; the others are below, pulling down.  The trunk
# must reach UP to blk_00's bottom (near) edge and tap it — and only that edge.
_PULL_DOWN = {
    "blk_00": (1250, 4600, 2230, 5400),
    "blk_15": (2230, 3800, 3500, 4600),
    "blk_32": (2230, 3000, 3500, 3800),
}


def test_pull_down_taps_bottom_near_edge():
    c, B = _v_trunk_inside(_fp(_PULL_DOWN), "blk_15", ["blk_32", "blk_00"], "blk_00")
    taps = _faces_tapped(c, B)
    assert "BOTTOM" in taps, f"one-directional down-pull must tap blk_00's BOTTOM (near) edge, got {sorted(taps)}"
    assert "TOP" not in taps, f"must not tap the far (TOP) edge, got {sorted(taps)}"


# =============================================================================
# H-trunk: the x<->y mirror.  A horizontal trunk's along-axis is x, so its
# along-axis edges are B's LEFT and RIGHT; the vertical conn-segs (stubs) land on
# B's TOP/BOTTOM faces incidentally.  Same rule: opposing pulls (conn-seg span
# overlaps B) => no LEFT/RIGHT tap; one-directional pull => tap B's near edge.
# =============================================================================

# --- Straddle (opposing pulls): no artificial left/right tap -----------------

# Mirror of _STRADDLE: blk_15 (driver) left of the x=4615 junction, blk_32 right.
# Their stubs pull the contact in opposing x-directions, pinning it inside blk_00
# (x[3780,4775]).  No tap on blk_00's left or right face should be manufactured.
_H_STRADDLE = {
    "blk_00": (3780, 1250, 4775, 2230),
    "blk_15": (3365, 2230, 4615, 3500),
    "blk_32": (4615, 2230, 5350, 3500),
}


@pytest.mark.xfail(reason="Artificial RIGHT tap still emitted in the H straddle "
                          "case (to be fixed: opposing pulls => no edge tap).",
                   strict=True)
def test_h_straddle_no_artificial_left_or_right_tap():
    c, B = _trunk_inside(_fp(_H_STRADDLE), "blk_15", ["blk_32", "blk_00"], "blk_00", "H")
    assert _overlaps(c, B), "trunk should touch blk_00 by overlap"
    taps = _faces_tapped(c, B)
    assert "LEFT" not in taps and "RIGHT" not in taps, (
        f"opposing pulls pin the trunk inside blk_00 — no along-axis edge tap "
        f"should be manufactured, got taps={sorted(taps)}"
    )


# --- One-directional pull RIGHT: tap B's RIGHT (near) edge -------------------

# blk_00 (receiver) is on the left; blk_15/blk_32 are both to its right, so both
# stubs pull right and away.  The trunk reaches RIGHT to blk_00's near edge.
_H_PULL_RIGHT = {
    "blk_00": (3000, 1250, 3800, 2230),
    "blk_15": (3800, 2230, 4600, 3500),
    "blk_32": (4600, 2230, 5400, 3500),
}


def test_h_pull_right_taps_right_near_edge():
    c, B = _trunk_inside(_fp(_H_PULL_RIGHT), "blk_15", ["blk_32", "blk_00"], "blk_00", "H")
    taps = _faces_tapped(c, B)
    assert "RIGHT" in taps, f"one-directional right-pull must tap blk_00's RIGHT (near) edge, got {sorted(taps)}"
    assert "LEFT" not in taps, f"must not tap the far (LEFT) edge, got {sorted(taps)}"


# --- One-directional pull LEFT: tap B's LEFT (near) edge ---------------------

# blk_00 (receiver) is on the right; the others are to its left, pulling left.
# The trunk reaches LEFT to blk_00's near edge.
_H_PULL_LEFT = {
    "blk_00": (4600, 1250, 5400, 2230),
    "blk_15": (3800, 2230, 4600, 3500),
    "blk_32": (3000, 2230, 3800, 3500),
}


def test_h_pull_left_taps_left_near_edge():
    c, B = _trunk_inside(_fp(_H_PULL_LEFT), "blk_15", ["blk_32", "blk_00"], "blk_00", "H")
    taps = _faces_tapped(c, B)
    assert "LEFT" in taps, f"one-directional left-pull must tap blk_00's LEFT (near) edge, got {sorted(taps)}"
    assert "RIGHT" not in taps, f"must not tap the far (RIGHT) edge, got {sorted(taps)}"
