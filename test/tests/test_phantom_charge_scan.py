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

"""The predicate `tools/experiment/phantom_charge.py` rests its verdict on.

The finding — that the same-bundle double charge reaches committed geometry on
0.26% of segments — is a COUNT, so it is only as good as what gets counted.
`coincident_pairs` decides that, and each of its four conditions rejects a shape
that would otherwise be miscounted as duplicated metal:

  different layers   -> different metal, two charges correct
  crossing (H vs V)  -> not a duplicate at all
  parallel but apart -> each wire needs its own width, two charges CORRECT
  merely touching    -> shares no metal

Over-matching would inflate the incidence and manufacture a problem; under-
matching would hide one.  Both directions are pinned here.
"""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(_ROOT / "tools" / "experiment"))

from phantom_charge import coincident_pairs, orient, perp_of  # noqa: E402

pytestmark = pytest.mark.mid


class _P:
    def __init__(self, x, y): self.x, self.y = x, y


class _Seg:
    def __init__(self, x1, y1, x2, y2):
        self.start, self.end = _P(x1, y1), _P(x2, y2)


class _Topo:
    def __init__(self, segs): self.segments = segs


def _pairs(segs, layers):
    return [(p[0], p[1]) for p in coincident_pairs(_Topo(segs), layers)]


# ------------------------------------------------------- the duplicate itself

def test_collinear_overlapping_same_layer_is_a_duplicate():
    """The shape the census exists to count: one wire, charged twice."""
    segs = [_Seg(0, 0, 0, 100), _Seg(0, 0, 0, 400)]
    assert _pairs(segs, [5, 5]) == [(0, 1)]


def test_the_overlap_extent_is_the_shared_part_only():
    segs = [_Seg(0, 50, 0, 200), _Seg(0, 100, 0, 400)]
    (i, j, lo, hi, o, perp, layer), = coincident_pairs(_Topo(segs), [5, 5])
    assert (lo, hi) == (100, 200), "overlap must be the intersection"
    assert (o, perp, layer) == ("V", 0, 5)


# --------------------------------------------- each rejection is load-bearing

def test_different_layers_are_not_a_duplicate():
    """Different metal. Charging both is correct — counting it would inflate
    the incidence that the whole conclusion rests on."""
    segs = [_Seg(0, 0, 0, 100), _Seg(0, 0, 0, 400)]
    assert _pairs(segs, [5, 7]) == []


def test_parallel_but_apart_is_NOT_a_duplicate():
    """The most important rejection: two same-bundle wires at different
    perpendicular positions each need their own width, so two charges is the
    CORRECT answer, not a phantom."""
    segs = [_Seg(0, 0, 0, 400), _Seg(10, 0, 10, 400)]
    assert _pairs(segs, [5, 5]) == []


def test_a_crossing_is_not_a_duplicate():
    segs = [_Seg(0, 0, 0, 400), _Seg(-100, 200, 100, 200)]
    assert _pairs(segs, [5, 5]) == []


def test_segments_that_merely_touch_share_no_metal():
    """End-to-end abutment: the overlap is a point, so `lo < hi` is strict."""
    segs = [_Seg(0, 0, 0, 100), _Seg(0, 100, 0, 400)]
    assert _pairs(segs, [5, 5]) == []


def test_an_unassigned_layer_is_skipped_not_matched():
    """A -1 layer means the planner never assigned it; pairing two of them
    would invent duplicates out of unplanned geometry."""
    segs = [_Seg(0, 0, 0, 100), _Seg(0, 0, 0, 400)]
    assert _pairs(segs, [-1, -1]) == []


def test_a_missing_layer_entry_is_treated_as_unassigned():
    """seg_layers can be shorter than segments; indexing past it must not throw
    and must not match."""
    segs = [_Seg(0, 0, 0, 100), _Seg(0, 0, 0, 400)]
    assert _pairs(segs, [5]) == []


# ----------------------------------------------------------------- primitives

def test_orient_rejects_degenerate_and_diagonal():
    assert orient(_Seg(0, 0, 0, 10)) == "V"
    assert orient(_Seg(0, 0, 10, 0)) == "H"
    assert orient(_Seg(5, 5, 5, 5)) is None      # a point
    assert orient(_Seg(0, 0, 10, 10)) is None    # diagonal


def test_perp_is_the_axis_the_segment_does_not_run_along():
    assert perp_of(_Seg(0, 7, 100, 7), "H") == 7
    assert perp_of(_Seg(7, 0, 7, 100), "V") == 7


def test_three_collinear_segments_report_every_duplicated_pair():
    """Coverage is pairwise: a triple stack duplicates three ways, and the
    census must not stop at the first."""
    segs = [_Seg(0, 0, 0, 100), _Seg(0, 0, 0, 200), _Seg(0, 0, 0, 300)]
    assert _pairs(segs, [5, 5, 5]) == [(0, 1), (0, 2), (1, 2)]
