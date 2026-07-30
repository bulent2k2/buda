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

"""Fixed-bit track-identity quantization (detailed_nuts.cpp track_key).

DetailedNUTS's reservation / hazard logic compares track positions for
IDENTITY.  Positions enumerated within one run are bit-identical (one
`origin + n*pitch + prefix` walk, absolute unit index), so an exact double
compare is sound there — but a bottom-up fixed-bit copy
(offset/transform_net_segment) reaches the same physical track via
`+dy` / `perp_dim - p`, a different arithmetic path.  For non-representable
decimal patterns (0.07 / 0.095 / 0.14 µm — the def_cluster ISPD-style
patterns) the two paths can land ~1 ulp apart:

    up = 0.14 + 0.14
    25 * up + 0.07  == 7.070000000000001     (fresh enumeration)
    0.07 + 7        == 7.07                  (reference track + integer dy)

With an exact compare the reservation silently misses — another bundle's
bit lands ON the copied bundle's track (a cross-bundle short the
same-bundle BIT_SHORT audit never sees) and a same-bundle different-bit
hazard goes undetected.  The engine therefore quantizes identity to a
1e-6 µm key (track_key), the same convention as verify.cpp's BIT_SHORT
bucketing.  These tests pin the two failure modes with the ulp-off copy
values above.
"""
import buda


LAYER = 4
UP = 0.14 + 0.14          # non-dyadic unit pitch (SIGNAL w=0.14 sp=0.14)
N_UNIT, DY = 25, 7        # 25 * 0.28 == 7 exactly in real arithmetic
T_ENUM = float(N_UNIT) * UP + 0.07   # what this run's enumeration yields
T_COPY = 0.07 + DY                   # what offset_net_segment's copy carries


def make_stack():
    slots = [buda.TrackSlot(type="SIGNAL", label="sig",
                            width=0.14, space_after=0.14)]
    stack = buda.RoutingGridStack()
    stack.define_layer(LAYER, buda.TrackPattern(origin=0.0, slots=slots), True)
    return stack


def make_fixed_bit(bundle_id, bit_index):
    ns = buda.NetSegment()
    ns.bundle_id = bundle_id
    ns.seg_idx = 0
    ns.bit_index = bit_index
    ns.layer = LAYER
    ns.span_lo, ns.span_hi = 0.0, 100.0
    ns.track_position = T_COPY
    ns.width = 0.14
    return ns


def make_seg(bundle_id):
    bs = buda.BusSegment()
    bs.bundle_id = bundle_id
    bs.seg_idx = 1
    bs.layer = LAYER
    bs.span_lo, bs.span_hi = 0.0, 100.0
    bs.interval_lo, bs.interval_hi = T_ENUM - 1.0, T_ENUM + 1.0
    bs.bit_width = 1
    bs.abstract_pos = T_ENUM   # anchor exactly on the copied track
    return bs


def test_ulp_premise_holds():
    # The scenario is only a regression test while the two arithmetic paths
    # actually disagree in the last ulp (IEEE-754 double, deterministic).
    assert T_ENUM != T_COPY
    assert abs(T_ENUM - T_COPY) < 1e-9


def test_fixed_bit_reserves_track_across_ulp_mismatch():
    # Cross-bundle: bundle 1's fixed (copied) bit occupies T_COPY; bundle 2
    # is anchored exactly there.  The reservation must hold despite the ulp
    # mismatch — bundle 2's bit lands on a NEIGHBORING track, not on the
    # fixed bit's track.
    engine = buda.DetailedNUTSEngine(make_stack())
    engine.add_fixed_bits([make_fixed_bit(bundle_id=1, bit_index=0)])
    result = engine.run([make_seg(bundle_id=2)])
    assert result.num_unplaced == 0
    assert len(result.net_segments) == 1
    placed = result.net_segments[0].track_position
    assert abs(placed - T_COPY) > UP / 2, (
        f"bit placed at {placed!r} shares the fixed bit's track {T_COPY!r} "
        f"(enumerated {T_ENUM!r}) — reservation missed the ulp-off copy")


def test_same_bundle_different_bit_hazard_across_ulp_mismatch():
    # Same-bundle, DIFFERENT bit: bit 1's fixed copy occupies T_COPY; bit 0
    # of another segment of the same bundle is anchored there.  Two bits are
    # two different nets — the hazard check must see the conflict through
    # the ulp mismatch and repair to a different track (else BIT_SHORT).
    engine = buda.DetailedNUTSEngine(make_stack())
    engine.add_fixed_bits([make_fixed_bit(bundle_id=2, bit_index=1)])
    result = engine.run([make_seg(bundle_id=2)])
    assert result.num_unplaced == 0
    assert len(result.net_segments) == 1
    placed = result.net_segments[0].track_position
    assert abs(placed - T_COPY) > UP / 2, (
        f"bit 0 placed at {placed!r} shares bit 1's track {T_COPY!r} — "
        f"same-bundle hazard missed the ulp-off copy (silent BIT_SHORT)")
