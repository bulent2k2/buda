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

"""Phase G — the placed-segment hierarchy and first-class pre-routes
(docs/internal/placed_segment_preroutes.md).

Covers the SegKind tags through pybind, PreRoutedSegment's field surface,
and the RoutingGridStack.preroutes() enumeration: non-SIGNAL slots only,
label fallback to the slot type, deterministic track_index, global-pattern
spans covering the along window, and PatternOverride emission clipped to
(region ∩ windows) with the local pattern's own origin.
"""
import buda


def _pattern(origin=0.0):
    # |VDD(2)+1| sig(1)+1 | sig(1)+1 | GND(2)+1|  -> unit pitch 10
    return buda.TrackPattern(origin, [
        buda.TrackSlot('POWER', 'VDD', 2.0, 1.0),
        buda.TrackSlot('SIGNAL', '', 1.0, 1.0),
        buda.TrackSlot('SIGNAL', '', 1.0, 1.0),
        buda.TrackSlot('GROUND', '', 2.0, 1.0),   # empty label: falls back to type
    ])


def _stack():
    s = buda.RoutingGridStack()
    s.define_layer(4, _pattern(), True)    # horizontal
    s.define_layer(5, _pattern(), False)   # vertical
    return s


def test_kind_tags():
    assert buda.TrackSegment().kind == buda.SegKind.BUS
    assert buda.NetSegment().kind == buda.SegKind.NET
    pr = buda.PreRoutedSegment()
    assert pr.kind == buda.SegKind.PREROUTE
    assert pr.placed          # pre-routes are placed by construction
    assert pr.track_index == -1 and pr.label == '' and pr.slot_type == ''


def test_preroutes_non_signal_only_with_label_fallback():
    rows = _stack().preroutes(4, 0.0, 20.0, 0.0, 100.0)
    # Two pattern units in [0, 20]: VDD@1, GND@8, VDD@11, GND@18.
    assert [(r.label, r.slot_type, r.track_position) for r in rows] == [
        ('VDD', 'POWER', 1.0), ('GROUND', 'GROUND', 8.0),
        ('VDD', 'POWER', 11.0), ('GROUND', 'GROUND', 18.0)]
    assert all(r.kind == buda.SegKind.PREROUTE and r.placed for r in rows)
    assert all(r.layer == 4 for r in rows)
    assert [r.track_index for r in rows] == [0, 1, 2, 3]
    # Global-pattern pre-routes span the full along window.
    assert all((r.span_lo, r.span_hi) == (0.0, 100.0) for r in rows)
    assert {r.width for r in rows} == {2.0}


def test_preroutes_vertical_layer_same_enumeration():
    rows = _stack().preroutes(5, 0.0, 20.0, 10.0, 90.0)
    assert [r.track_position for r in rows] == [1.0, 8.0, 11.0, 18.0]
    assert all((r.span_lo, r.span_hi) == (10.0, 90.0) for r in rows)


def test_preroutes_override_clipped_to_region():
    s = _stack()
    # Horizontal layer 4: override region x in [20, 60], y in [0, 10] with a
    # denser local pattern (own origin): VDD every 5.
    local = buda.TrackPattern(0.0, [
        buda.TrackSlot('POWER', 'VDD2', 1.0, 4.0)])
    s.add_override(4, 20, 0, 60, 10, local)
    rows = s.preroutes(4, 0.0, 20.0, 0.0, 100.0)
    glob = [r for r in rows if r.label != 'VDD2']
    ovr = [r for r in rows if r.label == 'VDD2']
    # Global emission unchanged (v1: not split under the override).
    assert [r.track_position for r in glob] == [1.0, 8.0, 11.0, 18.0]
    # Override slots: centres 0.5, 5.5 (10.5 > region y2=10 excluded), spans
    # clipped to the region's along extent [20, 60].
    assert [r.track_position for r in ovr] == [0.5, 5.5]
    assert all((r.span_lo, r.span_hi) == (20.0, 60.0) for r in ovr)
    # track_index stays a single deterministic running sequence.
    assert [r.track_index for r in rows] == list(range(len(rows)))


def test_preroutes_empty_cases():
    s = _stack()
    assert s.preroutes(4, 30.0, 30.5, 0.0, 100.0) == []   # no centre in window
    # Signal-only pattern -> no pre-routes at all.
    s2 = buda.RoutingGridStack()
    s2.define_layer(2, buda.TrackPattern(0.0, [
        buda.TrackSlot('SIGNAL', '', 1.0, 1.0)]), True)
    assert s2.preroutes(2, 0.0, 100.0, 0.0, 100.0) == []


def test_preroutes_include_signal_adds_signal_rails():
    """include_signal=True (the [Tracks] rail view's enumeration) adds the
    SIGNAL slots to the same walk; the default keeps the pre-route contract
    (non-SIGNAL only)."""
    rows = _stack().preroutes(4, 0.0, 20.0, 0.0, 100.0, include_signal=True)
    assert [(r.slot_type, r.track_position) for r in rows] == [
        ('POWER', 1.0), ('SIGNAL', 3.5), ('SIGNAL', 5.5), ('GROUND', 8.0),
        ('POWER', 11.0), ('SIGNAL', 13.5), ('SIGNAL', 15.5), ('GROUND', 18.0)]
    assert [r.track_index for r in rows] == list(range(8))
    # SIGNAL rails carry the slot geometry like any other band.
    sig = [r for r in rows if r.slot_type == 'SIGNAL']
    assert {r.width for r in sig} == {1.0}
    assert all((r.span_lo, r.span_hi) == (0.0, 100.0) for r in sig)
    # Default excludes them (same window: only the 4 non-SIGNAL slots).
    assert len(_stack().preroutes(4, 0.0, 20.0, 0.0, 100.0)) == 4
