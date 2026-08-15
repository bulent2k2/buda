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

"""What `tools/experiment/phantom_charge.py` rests its verdict on.

The finding — that the same-bundle double charge reaches committed geometry on
0.26% of segments, and lands on three bands none of which overflows — is a
COUNT plus an arithmetic, so it is only as good as what gets counted and what
the per-band numbers are read from.

`coincident_pairs` decides the counting.  Each of its four conditions rejects a
shape that would otherwise be miscounted as duplicated metal:

  different layers   -> different metal, two charges correct
  crossing (H vs V)  -> not a duplicate at all
  parallel but apart -> each wire needs its own width, two charges CORRECT
  merely touching    -> shares no metal

Over-matching would inflate the incidence and manufacture a problem; under-
matching would hide one.  Both directions are pinned here.

`CongestionPlanner::committed_charges()` supplies the arithmetic, and the last
section pins the property that makes it trustworthy: summed per (cut, band) it
reproduces `GlobalCut::usage` exactly.  That is what a hand-rolled
`for_each_band_w` could not do — it got four things wrong (Codex #754) and the
numbers were withdrawn — so the identity is the guard against re-deriving by
accident.  `band_phantoms` then does only set arithmetic on those records, and
its one judgement call (a group of N pays once, at the LARGEST amount) is
pinned too.
"""
import contextlib
import io
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(_ROOT / "tools" / "experiment"))

from phantom_charge import (band_phantoms, coincident_pairs,  # noqa: E402
                            orient, perp_of)

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


# ------------------------------------------------- P2: the per-band arithmetic

class _FakeCut:
    def __init__(self, cap, usage, layer_id=5):
        self._cap, self._use, self.layer_id = cap, usage, layer_id

    def cap(self, _b): return self._cap
    def usage(self, _b): return self._use


class _FakeCharge:
    def __init__(self, bid, si, ci, b, amt):
        self.bundle_id, self.seg_idx = bid, si
        self.cut_index, self.band, self.amount = ci, b, amt


class _FakePlanner:
    """Stands in for the engine, supplying records in its exact shape.

    Only `band_phantoms`' own set arithmetic is under test here — the records
    themselves are pinned against the real engine by the identity test below.
    """
    def __init__(self, charges, cuts):
        self._c, self._cuts = charges, cuts

    def committed_charges(self): return self._c
    def get_cuts(self): return self._cuts


def test_a_pair_on_one_band_is_charged_once_too_often():
    p = _FakePlanner([_FakeCharge(1, 0, 7, 3, 40.0),
                      _FakeCharge(1, 1, 7, 3, 40.0)],
                     {7: _FakeCut(100.0, 80.0)})
    (row,) = band_phantoms(p, {1: [{0, 1}]})
    assert row[:5] == (7, 3, 40.0, 100.0, 80.0)


def test_a_band_only_one_member_charged_is_not_affected():
    """The shared CUT is what licenses treating the group as one wire: a band
    only one segment reaches carries no duplicate, whatever the geometry says
    about the pair elsewhere."""
    p = _FakePlanner([_FakeCharge(1, 0, 7, 3, 40.0),
                      _FakeCharge(1, 1, 9, 3, 40.0)],
                     {7: _FakeCut(100.0, 80.0), 9: _FakeCut(100.0, 80.0)})
    assert band_phantoms(p, {1: [{0, 1}]}) == []


def test_a_triple_stack_pays_once_not_three_times():
    """The reason groups are coincidence CLASSES rather than pairs.  Three
    collinear segments are one wire charged three times, so the phantom is two
    charges — summing the three pairwise minima would report three."""
    p = _FakePlanner([_FakeCharge(1, s, 7, 3, 40.0) for s in (0, 1, 2)],
                     {7: _FakeCut(200.0, 120.0)})
    (row,) = band_phantoms(p, {1: [{0, 1, 2}]})
    assert row[2] == 80.0


def test_unequal_amounts_keep_the_LARGEST():
    """`sum - max`, not `sum - min`: the surviving wire is the one that was
    charged most, so the reported phantom is the conservative figure."""
    p = _FakePlanner([_FakeCharge(1, 0, 7, 3, 10.0),
                      _FakeCharge(1, 1, 7, 3, 40.0)],
                     {7: _FakeCut(100.0, 80.0)})
    (row,) = band_phantoms(p, {1: [{0, 1}]})
    assert row[2] == 10.0


def test_two_bundles_on_one_band_accumulate_and_are_both_named():
    p = _FakePlanner([_FakeCharge(1, 0, 7, 3, 40.0),
                      _FakeCharge(1, 1, 7, 3, 40.0),
                      _FakeCharge(2, 0, 7, 3, 25.0),
                      _FakeCharge(2, 1, 7, 3, 25.0)],
                     {7: _FakeCut(300.0, 130.0)})
    (row,) = band_phantoms(p, {1: [{0, 1}], 2: [{0, 1}]})
    assert row[2] == 65.0
    assert row[6] == [1, 2]


def test_separate_classes_of_one_bundle_do_not_merge():
    """Two independent duplicate pairs at different perpendiculars are two
    wires each charged twice — a phantom of two charges, not three."""
    p = _FakePlanner([_FakeCharge(1, s, 7, 3, 20.0) for s in (0, 1, 2, 3)],
                     {7: _FakeCut(200.0, 80.0)})
    (row,) = band_phantoms(p, {1: [{0, 1}, {2, 3}]})
    assert row[2] == 40.0


def test_pairs_on_different_placed_tracks_are_separate_classes(monkeypatch):
    """Codex #762: the class key must be the PLACED track, not the nominal
    perpendicular.

    Being one wire is a placed fact, and the two properties can disagree — four
    segments can share a nominal perpendicular while NUTS seats them as two
    pairs on two different tracks.  That is two wires each charged twice, so
    the phantom is TWO charges; a nominal key merges all four into one class
    and reports THREE, inflating the affected-band and overflow conclusions the
    whole finding rests on.

    Driven through `run` rather than `band_phantoms`, because the defect was in
    how the classes are BUILT, not in how they are summed.
    """
    import phantom_charge as pc

    segs = [_Seg(0, 0, 0, 400), _Seg(0, 0, 0, 300),      # pair A, track 10
            _Seg(0, 0, 0, 200), _Seg(0, 0, 0, 100)]      # pair B, track 20
    topo = _Topo(segs)
    tracks = {0: 10.0, 1: 10.0, 2: 20.0, 3: 20.0}

    class _TS:
        def __init__(self, si):
            self.bundle_id, self.seg_idx = 1, si
            self.track_position, self.placed = tracks[si], True

    class _Plan:
        selected_topology_index = 0
        seg_layers = [5, 5, 5, 5]

    class _Bundle:
        id = 1

    class _Input:
        candidates = [topo]
        original_bundle = _Bundle()

    class _W:
        plan, input = _Plan(), _Input()

    class _NR:
        segments = [_TS(s) for s in range(4)]

    charges = [_FakeCharge(1, s, 7, 3, 20.0) for s in range(4)]

    class _S:
        nuts_result = _NR()
        bundles = [_W()]
        planner = _FakePlanner(charges, {7: _FakeCut(500.0, 80.0)})
        planner.charge_log_active = lambda: True

    monkeypatch.setattr(pc, "solve", lambda _flow: _S())
    p0, totals, bands = {}, __import__("collections").Counter(), []
    p0 = __import__("collections").Counter()
    pc.run("dummy.buda", p0, totals, bands)

    assert p0["co-placed (phantom)"] == 2, dict(p0)
    (row,) = bands
    # sum-max over each PAIR: 20 + 20.  Merged as one class of four it is 60.
    assert row[3] == 40.0, row


# ------------------------------------------ the identity that makes it trusted

@pytest.mark.parametrize("flow", [
    "flow/four_blocks.buda",
    # A HEALING flow, so the identity is held across the paths that make the
    # record hard: rip-up erases a key and re-commits it, and
    # recharge_committed drops the whole log and rebuilds it.  A plan-only flow
    # would only ever exercise the append.
    "flow/rnr/mix.buda",
])
def test_committed_charges_reproduces_band_usage_exactly(flow):
    """The record IS the committed field, stated as an equation.

    `commit_plan` keeps `charge_log_` so a rip-up can subtract precisely what
    it added, so summing it per (cut, band) must land back on
    `GlobalCut::usage`.  A drift here means the accessor has stopped describing
    what the planner actually charged — which is exactly the failure the
    withdrawn Python re-derivation had, arrived at from the other direction.
    """
    sys.path.insert(0, str(_ROOT / "src"))
    import buda_cli
    from collections import defaultdict

    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        try:
            s.do_command(f"source {_ROOT / flow}")
        except SystemExit:
            pass

    p = s.planner
    assert p is not None and p.charge_log_active()
    charges = p.committed_charges()
    assert charges, "the flow committed nothing — the identity would be vacuous"

    agg = defaultdict(float)
    for c in charges:
        agg[(c.cut_index, c.band)] += c.amount

    cuts = p.get_cuts()
    for (ci, b), amount in agg.items():
        assert abs(cuts[ci].usage(b) - amount) < 1e-9, (ci, b)
    # And nothing charged is missing from the log: every band carrying usage
    # must be accounted for, or the phantom could hide in the gap.
    for ci, cut in enumerate(cuts):
        for b in range(cut.num_bands()):
            if abs(cut.usage(b)) > 1e-9:
                assert (ci, b) in agg, (ci, b)
