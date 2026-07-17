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

"""Same-bundle bit-track consistency in DetailedNUTS (the BIT_SHORT heal).

The abstract same-bundle track-sharing exemption is only sound BIT-FOR-BIT:
bit k of two segments of one bundle is the same net (sharing a track is a
join at their junction), while bit k and bit j != k are different nets —
sharing a track over interacting spans is a physical short (the BIT_SHORT
class check_dnuts audits; big.buda shipped 44 of them, mix.buda 90).

place_by_layer now resolves the hazard at selection time, natural-first:

1. NO CONFLICT — the historical selection shares no track with a
   span-interacting same-bundle sibling carrying a different bit: kept
   byte-identical (the dogleg split trunks and every already-clean corpus
   flow are untouched).
2. Conflict -> ALIGN: adopt the sibling's exact per-bit track list when the
   mapping matches and every track is usable — bit k lands on the SAME
   track on both segments and their wires join as one net.
3. Alignment impossible -> DISJOINT: repick with the sibling's tracks
   reserved so the windows cannot interleave; if that leaves too few tracks
   the bits go honestly unplaced rather than silently shorting.

Pattern (origin 0, unit_pitch 14): signal centres 3.5, 5.5, 10.5, 12.5,
17.5, 19.5, 24.5, 26.5, ...
"""
import pytest
import buda


def _pattern(origin=0.0):
    mk = lambda t, l, w, s: buda.TrackSlot(type=t, label=l, width=w, space_after=s)
    return buda.TrackPattern(origin=origin, slots=[
        mk("POWER",  "VDD", 2.0, 1.0),
        mk("SIGNAL", "sig", 1.0, 1.0),
        mk("SIGNAL", "sig", 1.0, 1.0),
        mk("GROUND", "GND", 2.0, 1.0),
        mk("SIGNAL", "sig", 1.0, 1.0),
        mk("SIGNAL", "sig", 1.0, 1.0),
    ])


def _stack(layer=4):
    st = buda.RoutingGridStack()
    st.define_layer(layer, _pattern(), True)
    return st


def _seg(bundle_id=1, seg_idx=0, span=(0.0, 100.0), interval=(0.0, 28.0),
         bits=2, anchor=float("nan"), lo_bound=None):
    s = buda.BusSegment()
    s.bundle_id, s.seg_idx, s.layer = bundle_id, seg_idx, 4
    s.span_lo, s.span_hi = span
    s.interval_lo, s.interval_hi = interval
    s.bit_width = bits
    s.bit_order = "LO_HI"
    s.abstract_pos = anchor
    if lo_bound is not None:
        s.track_lo_bound = lo_bound
    return s


def _tracks(result, seg_idx):
    return sorted(ns.track_position for ns in result.net_segments
                  if ns.seg_idx == seg_idx)


def _bit_shorts(result):
    """Different bits of one bundle sharing a layer+track (any span relation:
    at selection the spans still equal the abstract spans, and touching
    junction spans overlap after the bit-span adjustment)."""
    n = 0
    ns = result.net_segments
    for i in range(len(ns)):
        for j in range(i + 1, len(ns)):
            a, b = ns[i], ns[j]
            if (a.bundle_id == b.bundle_id and a.bit_index != b.bit_index and
                    a.layer == b.layer and
                    abs(a.track_position - b.track_position) < 1e-9 and
                    a.span_lo <= b.span_hi and b.span_lo <= a.span_hi):
                n += 1
    return n


def test_no_conflict_keeps_natural_choice():
    """Sibling windows that never share a track are untouched (the dogleg
    split-trunk shape: two collinear pieces on separate bands)."""
    a = _seg(seg_idx=0, span=(0.0, 100.0), anchor=4.5)     # -> {3.5, 5.5}
    b = _seg(seg_idx=1, span=(100.0, 200.0), anchor=11.5)  # -> {10.5, 12.5}
    r = buda.DetailedNUTSEngine(_stack()).run([a, b])
    assert r.num_unplaced == 0
    assert _tracks(r, 0) == pytest.approx([3.5, 5.5])
    assert _tracks(r, 1) == pytest.approx([10.5, 12.5])   # natural, not aligned
    assert _bit_shorts(r) == 0


def test_conflicting_sibling_aligns_bit_for_bit():
    """B's natural window {5.5, 10.5} puts its bit 0 on A's bit-1 track ->
    conflict -> B adopts A's exact list {3.5, 5.5}: bit k on the same track
    on both segments (same net), zero different-bit sharing."""
    a = _seg(seg_idx=0, span=(0.0, 100.0), anchor=4.5)     # -> {3.5, 5.5}
    b = _seg(seg_idx=1, span=(100.0, 200.0), anchor=8.0)   # natural {5.5, 10.5}
    r = buda.DetailedNUTSEngine(_stack()).run([a, b])
    assert r.num_unplaced == 0
    assert _tracks(r, 0) == pytest.approx([3.5, 5.5])
    assert _tracks(r, 1) == pytest.approx([3.5, 5.5])      # aligned adoption
    # Per-track bit identity: bit k rides the same track on both segments.
    by_seg = {0: {}, 1: {}}
    for ns in r.net_segments:
        by_seg[ns.seg_idx][ns.bit_index] = ns.track_position
    assert by_seg[0] == by_seg[1]
    assert _bit_shorts(r) == 0


def test_unalignable_sibling_gets_disjoint_window():
    """A corner bound keeps B off A's low track, so alignment is impossible;
    B repicks with A's tracks reserved -> fully disjoint window."""
    a = _seg(seg_idx=0, span=(0.0, 100.0), anchor=4.5)               # {3.5, 5.5}
    b = _seg(seg_idx=1, span=(100.0, 200.0), anchor=8.0, lo_bound=5.0)
    r = buda.DetailedNUTSEngine(_stack()).run([a, b])
    assert r.num_unplaced == 0
    assert _tracks(r, 0) == pytest.approx([3.5, 5.5])
    assert _tracks(r, 1) == pytest.approx([10.5, 12.5])    # disjoint repick
    assert _bit_shorts(r) == 0


def test_overlapping_spans_same_side_never_interleave():
    """The bundle-59 shape: two same-side siblings with genuinely overlapping
    spans.  Whatever branch fires, no track may carry two different bits."""
    a = _seg(seg_idx=0, span=(0.0, 150.0), bits=3, anchor=6.0)
    b = _seg(seg_idx=1, span=(0.0, 300.0), bits=3, anchor=9.0)
    r = buda.DetailedNUTSEngine(_stack()).run([a, b])
    assert r.num_unplaced == 0
    assert _bit_shorts(r) == 0


def test_far_apart_siblings_may_share_tracks():
    """Same bundle, same layer, spans FAR apart (no interaction even after
    junction stagger): the exemption stays — both may use the same band."""
    a = _seg(seg_idx=0, span=(0.0, 100.0), anchor=4.5)
    b = _seg(seg_idx=1, span=(1000.0, 1100.0), anchor=4.5)
    r = buda.DetailedNUTSEngine(_stack()).run([a, b])
    assert r.num_unplaced == 0
    assert _tracks(r, 0) == _tracks(r, 1) == pytest.approx([3.5, 5.5])


def test_different_bundles_unaffected():
    """Cross-bundle behaviour unchanged: overlapping-span competitors still
    resolve by the reservation model (disjoint windows)."""
    a = _seg(bundle_id=1, seg_idx=0, span=(0.0, 100.0), anchor=4.5)
    b = _seg(bundle_id=2, seg_idx=0, span=(0.0, 100.0), anchor=4.5)
    r = buda.DetailedNUTSEngine(_stack()).run([a, b])
    assert r.num_unplaced == 0
    ta, tb = (sorted(ns.track_position for ns in r.net_segments
                     if ns.bundle_id == bid) for bid in (1, 2))
    assert not set(ta) & set(tb)
