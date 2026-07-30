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
1e-6 µm key (track_key) with NEIGHBOR-BUCKET tolerance (|key_a - key_b|
<= 1): plain bucket equality is not boundary-independent — two ulp-close
representations of one track can straddle a half-quantum rounding
boundary and land in ADJACENT buckets (Codex P1 on #528):

    w, sp = 0.010001, 0.269999          # exact centre ON a half-quantum
    25 * (w + sp) + w/2 == 7.0050004999999995   -> key 7005000
    w/2 + 7            == 7.0050005             -> key 7005001

Both failure modes are pinned for both value classes: the plain
ulp-mismatch (same bucket) and the half-quantum straddle (adjacent
buckets).
"""
import math

import pytest
import buda


def llround_key(v):
    """C++ track_key: llround(v * 1e6) — half rounds AWAY from zero
    (Python's round() is banker's rounding, so don't use it here)."""
    x = v * 1e6
    return math.floor(x + 0.5) if x >= 0 else math.ceil(x - 0.5)


LAYER = 4

# (width, space_after, n_unit, dy) — n_unit * (w+sp) == dy exactly in real
# arithmetic, so the copy carries `ref_track + dy` for the track this run
# enumerates at unit n_unit.
CASES = {
    # Plain ulp mismatch: enumeration and copy differ in the last ulp but
    # round into the SAME 1e-6 bucket.
    "ulp_same_bucket": (0.14, 0.14, 25, 7),
    # Half-quantum straddle: the exact centre (7.0050005) sits ON a
    # half-quantum boundary, so the two ulp-close representations round
    # into ADJACENT buckets (keys 7005000 vs 7005001).
    "half_quantum_straddle": (0.010001, 0.269999, 25, 7),
}


def case_values(name):
    w, sp, n_unit, dy = CASES[name]
    up = w + sp
    t_enum = float(n_unit) * up + w / 2.0   # this run's enumeration
    t_copy = (0.0 * up + w / 2.0) + dy      # offset_net_segment's copy
    return w, sp, up, t_enum, t_copy


def make_stack(w, sp):
    slots = [buda.TrackSlot(type="SIGNAL", label="sig",
                            width=w, space_after=sp)]
    stack = buda.RoutingGridStack()
    stack.define_layer(LAYER, buda.TrackPattern(origin=0.0, slots=slots), True)
    return stack


def make_fixed_bit(bundle_id, bit_index, t_copy, w):
    ns = buda.NetSegment()
    ns.bundle_id = bundle_id
    ns.seg_idx = 0
    ns.bit_index = bit_index
    ns.layer = LAYER
    ns.span_lo, ns.span_hi = 0.0, 100.0
    ns.track_position = t_copy
    ns.width = w
    return ns


def make_seg(bundle_id, t_enum):
    bs = buda.BusSegment()
    bs.bundle_id = bundle_id
    bs.seg_idx = 1
    bs.layer = LAYER
    bs.span_lo, bs.span_hi = 0.0, 100.0
    bs.interval_lo, bs.interval_hi = t_enum - 1.0, t_enum + 1.0
    bs.bit_width = 1
    bs.abstract_pos = t_enum   # anchor exactly on the copied track
    return bs


@pytest.mark.parametrize("case", CASES)
def test_ulp_premise_holds(case):
    # The scenarios are only regression tests while the two arithmetic
    # paths actually disagree in the last ulp (IEEE-754 double,
    # deterministic).
    _, _, _, t_enum, t_copy = case_values(case)
    assert t_enum != t_copy
    assert abs(t_enum - t_copy) < 1e-9


def test_half_quantum_case_straddles_buckets():
    # The boundary case must actually straddle a rounding boundary —
    # plain bucket equality would pass the other case yet miss this one.
    _, _, _, t_enum, t_copy = case_values("half_quantum_straddle")
    assert llround_key(t_enum) != llround_key(t_copy)


@pytest.mark.parametrize("case", CASES)
def test_fixed_bit_reserves_track_across_ulp_mismatch(case):
    # Cross-bundle: bundle 1's fixed (copied) bit occupies t_copy; bundle 2
    # is anchored exactly there.  The reservation must hold despite the ulp
    # mismatch — bundle 2's bit lands on a NEIGHBORING track, not on the
    # fixed bit's track.
    w, sp, up, t_enum, t_copy = case_values(case)
    engine = buda.DetailedNUTSEngine(make_stack(w, sp))
    engine.add_fixed_bits([make_fixed_bit(1, 0, t_copy, w)])
    result = engine.run([make_seg(2, t_enum)])
    assert result.num_unplaced == 0
    assert len(result.net_segments) == 1
    placed = result.net_segments[0].track_position
    assert abs(placed - t_copy) > up / 2, (
        f"bit placed at {placed!r} shares the fixed bit's track {t_copy!r} "
        f"(enumerated {t_enum!r}) — reservation missed the ulp-off copy")


@pytest.mark.parametrize("case", CASES)
def test_same_bundle_different_bit_hazard_across_ulp_mismatch(case):
    # Same-bundle, DIFFERENT bit: bit 1's fixed copy occupies t_copy; bit 0
    # of another segment of the same bundle is anchored there.  Two bits are
    # two different nets — the hazard check must see the conflict through
    # the ulp mismatch and repair to a different track (else BIT_SHORT).
    w, sp, up, t_enum, t_copy = case_values(case)
    engine = buda.DetailedNUTSEngine(make_stack(w, sp))
    engine.add_fixed_bits([make_fixed_bit(2, 1, t_copy, w)])
    result = engine.run([make_seg(2, t_enum)])
    assert result.num_unplaced == 0
    assert len(result.net_segments) == 1
    placed = result.net_segments[0].track_position
    assert abs(placed - t_copy) > up / 2, (
        f"bit 0 placed at {placed!r} shares bit 1's track {t_copy!r} — "
        f"same-bundle hazard missed the ulp-off copy (silent BIT_SHORT)")
