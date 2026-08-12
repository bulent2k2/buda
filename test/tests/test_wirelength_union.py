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

"""Wirelength is METAL, so shared metal is counted once.

NUTS deliberately lets same-bundle segments share a track — they are the same
nets, so they may occupy the same wire — and when it does, one physical wire
reaches the report as two or three overlapping segments.  Summing their spans
counts the shared stretch once per segment, which over-states the metric
exactly where the router did well: the better the sharing, the worse the
number.  `_wirelength_by_bundle` takes the union per (bundle, bit, layer,
track) instead.

Measured before the fix, on the 48-flow corpus: 870,386 units over-counted
(0.106%) across 22 flows and 4,547 places — 7.8% on big2/b3_bus_023, and every
chip flow affected.  That is the same order as the WL deltas the corpus is used
to judge, which is why it matters at 0.1%.

The helper is duck-typed over TrackSegment (abstract) and NetSegment
(detailed), so these build the minimum a segment must look like.  A TrackSegment
has no bit_index — the key degrades to (bundle, layer, track), which is the
right grouping for a whole-bus segment.
"""
import pytest

import buda_cli

pytestmark = pytest.mark.mid

# An empty session: no bundles, so no taper membership — the
# untapered path these unit cases exercise.  Bound to an INSTANCE
# because the helper needs the session to know which segments carry
# which bits (see test_tapered_abstract_segments_do_not_merge).
_WL = buda_cli.BudaSession()._wirelength_by_bundle


class _Seg:
    """The duck the helper reads: a NetSegment when bit_index is given, a
    TrackSegment when it is not."""
    def __init__(self, span_lo, span_hi, bundle_id=1, layer=4,
                 track_position=100.0, bit_index=None, placed=True):
        self.span_lo, self.span_hi = span_lo, span_hi
        self.bundle_id, self.layer = bundle_id, layer
        self.track_position, self.placed = track_position, placed
        if bit_index is not None:
            self.bit_index = bit_index


def _total(segs):
    return _WL(segs)[2]


def test_one_segment_is_its_own_length():
    assert _total([_Seg(10, 30, bit_index=0)]) == 20


def test_overlapping_same_track_metal_is_counted_once():
    """THE regression: the b3_bus_023 shape, where a stub from one block is
    seated on the track another stub already occupies."""
    segs = [_Seg(1000, 2082.5, bit_index=0), _Seg(1425, 2082.5, bit_index=0)]
    assert _total(segs) == 1082.5          # not 1082.5 + 657.5


def test_a_contained_segment_adds_nothing():
    segs = [_Seg(0, 100, bit_index=0), _Seg(40, 60, bit_index=0)]
    assert _total(segs) == 100


def test_abutting_segments_are_two_wires_not_an_overlap():
    """`max(lo, reach)` has to keep a touching span at full price: end-to-end
    wires are two wires.  In b3_bus_023 a third stub abuts the shared one at
    exactly 2082.5 and must count in full."""
    segs = [_Seg(1000, 2082.5, bit_index=0), _Seg(2082.5, 3365, bit_index=0)]
    assert _total(segs) == (2082.5 - 1000) + (3365 - 2082.5)


def test_three_way_overlap_collapses_to_the_union():
    segs = [_Seg(0, 50, bit_index=0), _Seg(25, 75, bit_index=0),
            _Seg(60, 120, bit_index=0)]
    assert _total(segs) == 120


def test_a_gap_between_runs_is_not_bridged():
    segs = [_Seg(0, 10, bit_index=0), _Seg(50, 60, bit_index=0)]
    assert _total(segs) == 20


# ------------------------------------------- what must STAY two wires

def test_different_tracks_are_parallel_wires():
    """Same layer, different track = different metal, however much the spans
    overlap.  Merging these would UNDER-state."""
    segs = [_Seg(0, 100, bit_index=0, track_position=100.0),
            _Seg(0, 100, bit_index=0, track_position=102.0)]
    assert _total(segs) == 200


def test_different_bits_are_different_nets():
    """Two bits on one track is a BIT_SHORT, not shared metal — the audit's
    business, and never something this may quietly merge away."""
    segs = [_Seg(0, 100, bit_index=0), _Seg(0, 100, bit_index=1)]
    assert _total(segs) == 200


def test_different_layers_are_different_metal():
    segs = [_Seg(0, 100, bit_index=0, layer=4),
            _Seg(0, 100, bit_index=0, layer=6)]
    assert _total(segs) == 200


def test_different_bundles_never_merge():
    segs = [_Seg(0, 100, bit_index=0, bundle_id=1),
            _Seg(0, 100, bit_index=0, bundle_id=2)]
    assert _total(segs) == 200


def test_an_abstract_segment_has_no_bit_and_still_groups():
    """A TrackSegment is the whole bus at one track, so (bundle, layer, track)
    is the correct key — the same overlap must collapse without a bit_index."""
    segs = [_Seg(0, 100), _Seg(40, 60)]
    assert _total(segs) == 100


# ------------------------------------------------------- bookkeeping

def test_reversed_spans_are_normalized():
    """span_lo/span_hi keep endpoint IDENTITY and may be stored reversed."""
    assert _total([_Seg(30, 10, bit_index=0)]) == 20
    assert _total([_Seg(100, 0, bit_index=0), _Seg(60, 40, bit_index=0)]) == 100


def test_unplaced_segments_are_counted_not_measured():
    pb, pl, total, unpl = _WL([_Seg(0, 100, bit_index=0),
                               _Seg(0, 999, bit_index=1, placed=False)])
    assert unpl == 1 and total == 100


def test_per_bundle_and_per_layer_reconcile_with_the_total():
    """The union is attributed to the group's own bundle and layer, so the
    breakdowns must still add up — a split that lost the merged length
    somewhere would show here."""
    segs = [_Seg(0, 100, bit_index=0, bundle_id=1, layer=4),
            _Seg(40, 160, bit_index=0, bundle_id=1, layer=4),
            _Seg(0, 70, bit_index=0, bundle_id=2, layer=6)]
    pb, pl, total, _ = _WL(segs)
    assert total == 160 + 70
    assert sum(pb.values()) == total
    assert sum(pl.values()) == total
    assert pb[1] == 160 and pb[2] == 70
    assert pl[4] == 160 and pl[6] == 70


def test_the_corpus_tool_and_the_report_share_one_implementation():
    """`qor_corpus.py` used to carry its own copy of this sum, and the copy is
    how the two drifted into being wrong together.  A corpus WL that disagrees
    with the flow log is a bug either way, so there must be nothing to
    disagree with."""
    import inspect
    import qor_corpus
    src = inspect.getsource(qor_corpus._wirelengths)
    assert "_wirelength_by_bundle" in src, \
        "qor_corpus must delegate to the canonical helper, not re-sum spans"
    assert not hasattr(qor_corpus, "_seg_wl"), \
        "the duplicate per-segment length helper should be gone"


# ------------------------------------------ the taper: same bundle, other nets

class _FakeTaperSession:
    """A session whose selected candidate declares per-segment bit membership.

    `_seg_taper_bits` reads exactly this shape off the real wrappers; building
    it by hand keeps the case exact instead of hunting a corpus flow that
    happens to produce a tapered overlap."""
    class _Topo:
        def __init__(self, seg_bits): self.seg_bits = seg_bits

    def __init__(self, seg_bits, bundle_id=1):
        topo = self._Topo(seg_bits)
        plan = type("P", (), {"selected_topology_index": 0})()
        inp = type("I", (), {"candidates": [topo],
                             "original_bundle": type("B", (), {"id": bundle_id})()})()
        self.bundles = [type("W", (), {"input": inp, "plan": plan})()]

    _seg_taper_bits = buda_cli.BudaSession._seg_taper_bits
    _wirelength_by_bundle = buda_cli.BudaSession._wirelength_by_bundle


def _abstract(span_lo, span_hi, seg_idx, bundle_id=1):
    s = _Seg(span_lo, span_hi, bundle_id=bundle_id)   # no bit_index = abstract
    s.seg_idx = seg_idx
    return s


def test_tapered_abstract_segments_carrying_different_bits_do_not_merge():
    """A TrackSegment is a whole BUS and has no bit_index, so two same-bundle
    segments at one track look identical — but under a fan-in/fan-OUT taper
    they can carry DISJOINT bits, which makes them different nets on different
    metal.  Merging them would UNDER-state, the opposite of the bug this
    change fixes and a worse failure: an under-count is invisible."""
    s = _FakeTaperSession({0: [0, 1, 2, 3], 1: [4, 5, 6, 7]})
    segs = [_abstract(0, 100, seg_idx=0), _abstract(40, 60, seg_idx=1)]
    assert s._wirelength_by_bundle(segs)[2] == 100 + 20


def test_tapered_abstract_segments_carrying_the_SAME_bits_still_merge():
    """The other half: identical membership on one track is one wire, and the
    taper guard must not block the merge it was added to qualify."""
    s = _FakeTaperSession({0: [0, 1, 2, 3], 1: [0, 1, 2, 3]})
    segs = [_abstract(0, 100, seg_idx=0), _abstract(40, 60, seg_idx=1)]
    assert s._wirelength_by_bundle(segs)[2] == 100


def test_an_untapered_bundle_has_no_membership_and_merges():
    """No seg_bits at all = every segment carries every bit, which is every
    non-fan-in bundle.  The guard must be inert there."""
    s = _FakeTaperSession({})
    segs = [_abstract(0, 100, seg_idx=0), _abstract(40, 60, seg_idx=1)]
    assert s._wirelength_by_bundle(segs)[2] == 100
