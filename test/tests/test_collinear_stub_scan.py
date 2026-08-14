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

"""The predicate `tools/scan_collinear_stubs.py` rests its conclusion on.

The scan's finding — that only ~22% of collinear stub pairs are removable, and
that none of the removable ones reaches a selected candidate — turns entirely on
one distinction: does the long leg CROSS the short leg's block, or merely GRAZE
its edge?  Get that backwards and every verdict flips.

It is worth its own test because the boundary case is not incidental, it is the
MAJORITY case: two blocks sharing an x1 make both stubs leave the trunk on the
same line, and the long one then runs exactly along the short one's edge.  That
is `mix2_topdown_refine` bundle 14, and BUDA agrees the block is not covered —
the topology reports `pass_through_count: 0`.
"""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(_ROOT / "tools"))

from scan_collinear_stubs import _crosses_interior, _ray, contained_pairs  # noqa: E402

pytestmark = pytest.mark.mid


class _P:
    def __init__(self, x, y): self.x, self.y = x, y


class _Seg:
    def __init__(self, x1, y1, x2, y2):
        self.start, self.end = _P(x1, y1), _P(x2, y2)


class _R:
    def __init__(self, x1, y1, x2, y2):
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2


# ------------------------------------------------- crossing vs grazing

def test_a_vertical_leg_along_the_left_edge_does_NOT_cover_the_block():
    """Bundle 14 exactly: seg3 at x=640, u2 at x[640,690] y[1170,1220].

    If this returned True the scan would call the pair redundant, and deleting
    the stub would open u2 — the single most consequential way to get this
    wrong.
    """
    seg = _Seg(640, 1090, 640, 1887)
    assert _crosses_interior(seg, _R(640, 1170, 690, 1220)) is False


def test_a_vertical_leg_through_the_middle_DOES_cover_the_block():
    seg = _Seg(665, 1090, 665, 1887)
    assert _crosses_interior(seg, _R(640, 1170, 690, 1220)) is True


def test_the_right_edge_is_also_only_a_graze():
    """Both edges, not just the one the corpus happened to show."""
    seg = _Seg(690, 1090, 690, 1887)
    assert _crosses_interior(seg, _R(640, 1170, 690, 1220)) is False


def test_a_horizontal_leg_grazes_and_crosses_symmetrically():
    r = _R(100, 200, 300, 400)
    assert _crosses_interior(_Seg(0, 200, 500, 200), r) is False   # bottom edge
    assert _crosses_interior(_Seg(0, 400, 500, 400), r) is False   # top edge
    assert _crosses_interior(_Seg(0, 300, 500, 300), r) is True    # through


def test_a_leg_that_stops_short_does_not_cover():
    """Interior perp is necessary but not sufficient — the leg must also reach
    along the block."""
    r = _R(100, 200, 300, 400)
    assert _crosses_interior(_Seg(0, 300, 90, 300), r) is False
    assert _crosses_interior(_Seg(0, 300, 110, 300), r) is True


def test_touching_the_far_corner_is_not_a_crossing():
    """`lo < a_hi and hi > a_lo` is a strict overlap, so abutting the block's
    along-extent at a single point does not count."""
    r = _R(100, 200, 300, 400)
    assert _crosses_interior(_Seg(0, 300, 100, 300), r) is False


# --------------------------------------------------- the ray/containment half

def test_a_ray_is_anchored_on_a_point_not_on_seg_start():
    """Whether the shared end is `start` or `end` is an artifact of how the
    generator emitted the edge, not a property of the shape — so both
    orientations must produce the same ray."""
    a, b = _P(0, 0), _P(0, 100)
    assert _ray(a, b) == (False, True, 100)
    assert _ray(b, a) == (False, False, 100)


def test_a_degenerate_or_diagonal_leg_is_not_a_ray():
    assert _ray(_P(5, 5), _P(5, 5)) is None          # a point
    assert _ray(_P(0, 0), _P(10, 10)) is None        # diagonal


class _Topo:
    def __init__(self, segs): self.segments = segs


def test_containment_is_found_at_either_shared_endpoint():
    """Same geometry, mirrored: the short leg inside the long one, sharing a
    START in one case and an END in the other.  Both must be found — that
    asymmetry is 5a vs 5b."""
    shared_start = _Topo([_Seg(0, 0, 0, 100), _Seg(0, 0, 0, 400)])
    # Both END at (0,300) and extend the SAME way (+y) from it — lengths 100
    # and 400.  Extending opposite ways from a shared point is a different
    # shape entirely and is pinned separately below.
    shared_end = _Topo([_Seg(0, 400, 0, 300), _Seg(0, 700, 0, 300)])
    assert [(p[0], p[1]) for p in contained_pairs(shared_start)] == [(0, 1)]
    assert [(p[0], p[1]) for p in contained_pairs(shared_end)] == [(0, 1)]


def test_opposite_directions_are_not_a_containment():
    """Two legs leaving one point in OPPOSITE directions overlap nowhere; the
    sign check is what keeps them apart."""
    t = _Topo([_Seg(0, 0, 0, 100), _Seg(0, 0, 0, -400)])
    assert contained_pairs(t) == []


def test_perpendicular_legs_are_not_a_containment():
    t = _Topo([_Seg(0, 0, 0, 100), _Seg(0, 0, 400, 0)])
    assert contained_pairs(t) == []


def test_equal_length_legs_are_not_reported_twice():
    """`ra[2] < rb[2]` is strict, so a duplicate of EQUAL length yields no pair
    in either direction — worth pinning, because a non-strict comparison would
    report the same overlap twice with the roles swapped."""
    t = _Topo([_Seg(0, 0, 0, 100), _Seg(0, 0, 0, 100)])
    assert contained_pairs(t) == []
