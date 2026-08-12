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

"""R1 metal-shaped NDR quantization (`opens_ndr.md` §2).

An absolute `def_ndr` width used to be quantized by the layer's per-signal-slot
CHANNEL cost, which answers "how much routing channel does this consume" — the
planner's question — and NOT "how much metal does the bit get", which is what a
width declared for EM, resistance or RC means.  The two share no term:
`bit_pitch` counts the power rails, metal does not.

These tests pin the metal reading and the two facts that make it more than a
formula change: a k-slot wire may not span a rail, and when the gaps vary the
NARROWEST window is the only guaranteed one.
"""
import pytest

import buda


def _pattern(spec):
    """(type, width, space_after) triples -> TrackPattern."""
    return buda.TrackPattern(
        0.0, [buda.TrackSlot(t, "", w, sp) for t, w, sp in spec])


# The two patterns flow/ndr_abs_divisor.buda is built from.
DENSE = [("POWER", 2, 1)] + [("SIGNAL", 1, 1)] * 12 + [("GROUND", 2, 1)]
SPARSE = [("POWER", 10, 2)] + [("SIGNAL", 2, 2)] * 4 + [("GROUND", 10, 2)]


def test_metal_is_not_the_channel_cost():
    """The whole reason the reading had to change.

    Dense: period 30 over 12 signal slots -> bit_pitch 2.5, while a k-slot bit
    gets 2k-1 units of metal.  Sparse: period 40 over 4 -> bit_pitch 10.0,
    metal 4k-2.  Neither pair agrees, and on the sparse layer the channel cost
    is FIVE TIMES the metal one slot delivers."""
    dense, sparse = _pattern(DENSE), _pattern(SPARSE)
    assert dense.unit_pitch() / 12 == pytest.approx(2.5)
    assert sparse.unit_pitch() / 4 == pytest.approx(10.0)
    dg, sg = dense.ndr_geom(), sparse.ndr_geom()
    for k in range(1, 13):
        assert buda.ndr_metal_for_slots(dg, k) == pytest.approx(2 * k - 1)
    for k in range(1, 5):
        assert buda.ndr_metal_for_slots(sg, k) == pytest.approx(4 * k - 2)


def test_a_wire_may_not_span_a_rail():
    """A k-slot wire is k CONSECUTIVE signal slots.  A period whose signal
    slots are split by a rail offers only its longest run, and asking for more
    is unrealizable (R3) rather than a number to round."""
    split = _pattern([("SIGNAL", 1, 1)] * 2 + [("POWER", 2, 1)]
                     + [("SIGNAL", 1, 1)] * 3)
    g = split.ndr_geom()
    assert [len(r) for r in g.runs] == [2, 3]
    assert buda.ndr_max_slots(g) == 3
    assert buda.ndr_metal_for_slots(g, 3) == pytest.approx(5)
    assert buda.ndr_metal_for_slots(g, 4) < 0, "a 4-slot wire would cross the rail"


def test_runs_splice_across_periods_only_when_no_rail_separates_them():
    """A pattern TILES, so the last run of one period abuts the first run of
    the next — but they are one physical run only when no rail sits between.
    Splicing unconditionally is what would let a wire cross a rail."""
    allsig = _pattern([("SIGNAL", 1, 1)] * 4)
    assert buda.ndr_max_slots(allsig.ndr_geom()) > 4, "abutting periods are one run"
    railed = _pattern([("SIGNAL", 1, 1)] * 4 + [("POWER", 2, 1)])
    assert buda.ndr_max_slots(railed.ndr_geom()) == 4, "the rail ends the run"


def test_the_narrowest_window_wins_when_gaps_vary():
    """Measured on the repo's own patterns: 20 of 345 have non-uniform signal
    gaps (widths agree, trailing gaps differ).  The placer chooses the seat,
    not the rule, so a rule is only guaranteed met at the WORST window — a
    single `k*w + (k-1)*sp` formula would report the wrong number for those."""
    mixed = _pattern([("SIGNAL", 2, 1), ("SIGNAL", 2, 2),
                      ("SIGNAL", 2, 1), ("SIGNAL", 2, 2), ("POWER", 4, 1)])
    g = mixed.ndr_geom()
    # k=2 windows measure 5, 6, 5 — the narrowest is what a rule can rely on.
    assert buda.ndr_metal_for_slots(g, 2) == pytest.approx(5)


def _spec(width_abs=0.0, spacing_abs=0.0):
    s = buda.NdrSpec()
    s.width_abs, s.spacing_abs = width_abs, spacing_abs
    return s


def test_the_four_divisor_rows_now_deliver_what_they_declared():
    """flow/ndr_abs_divisor.buda's table, re-measured.

    Channel-shaped gave 3 / 2 / 7 / 2 units for declared 3 / 3 / 8 / 8 — three
    of the four short, and the last one so short that the spec read INACTIVE
    and the bus routed ungoverned."""
    dg, sg = _pattern(DENSE).ndr_geom(), _pattern(SPARSE).ndr_geom()
    for declared, geom, layer in ((3, dg, 5), (3, sg, 6), (8, dg, 5), (8, sg, 6)):
        spec, ok = buda.ndr_resolve_on_layer(_spec(width_abs=declared), layer, geom)
        assert ok, f"width {declared} must be realizable here"
        got = buda.ndr_metal_for_slots(geom, spec.width_slots)
        assert got >= declared, f"declared {declared}, delivered {got}"
        # …and it must be the SMALLEST such k: over-delivering is waste.
        assert buda.ndr_metal_for_slots(geom, spec.width_slots - 1) < declared


def test_an_unrealizable_width_is_reported_not_clamped_silently():
    dg = _pattern(DENSE).ndr_geom()
    assert buda.ndr_metal_for_slots(dg, 12) == pytest.approx(23)
    spec, ok = buda.ndr_resolve_on_layer(_spec(width_abs=40), 5, dg)
    assert not ok, "40 units cannot be met by a 12-slot run delivering 23"
    # Still bounded, so a caller that ignores `ok` cannot loop or overflow.
    assert spec.width_slots == 12


def test_a_per_layer_value_overrides_the_rule_default():
    """The half of R1 that phase 1 dropped.  A physical rule's values differ
    per layer — EM limits, sheet resistance and RC all do — so one absolute
    width applied everywhere is over-wide on top or under-wide at the bottom."""
    dg, sg = _pattern(DENSE).ndr_geom(), _pattern(SPARSE).ndr_geom()
    s = _spec(width_abs=3)
    pl = buda.NdrLayerRule()
    pl.width_abs = 8.0
    s.set_layer_rule(6, pl)
    on5, _ = buda.ndr_resolve_on_layer(s, 5, dg)
    on6, _ = buda.ndr_resolve_on_layer(s, 6, sg)
    assert buda.ndr_metal_for_slots(dg, on5.width_slots) >= 3      # inherited
    assert buda.ndr_metal_for_slots(sg, on6.width_slots) >= 8      # overridden


def test_per_layer_cannot_be_mutated_through_the_read_only_view():
    """pybind11 converts std::map BY VALUE.  Exposing `per_layer` writable
    would accept `spec.per_layer[id] = rule`, mutate a copy, and leave the
    rule reading as declared while governing nothing — the silent-no-op class
    this feature keeps producing.  Caught by these tests before it shipped."""
    s = _spec(width_abs=3)
    pl = buda.NdrLayerRule()
    pl.width_abs = 8.0
    s.per_layer[6] = pl                      # a copy: must NOT take effect
    assert 6 not in s.per_layer
    s.set_layer_rule(6, pl)                  # the only way that works
    assert 6 in s.per_layer


def test_a_multiplier_override_wins_over_an_inherited_absolute():
    """The more specific declaration is the one the user made about THIS
    layer, and a multiplier is already quantized — nothing left to resolve."""
    dg = _pattern(DENSE).ndr_geom()
    s = _spec(width_abs=8)
    pl = buda.NdrLayerRule()
    pl.width_slots = 2
    s.set_layer_rule(5, pl)
    on5, ok = buda.ndr_resolve_on_layer(s, 5, dg)
    assert ok and on5.width_slots == 2


def test_multiplier_and_ungoverned_specs_are_untouched():
    """The R12 guarantee: a design declaring no absolute value must be
    byte-identical, so resolution has to be the identity for it."""
    dg = _pattern(DENSE).ndr_geom()
    mult = buda.NdrSpec()
    mult.width_slots = 2
    got, ok = buda.ndr_resolve_on_layer(mult, 5, dg)
    assert ok and got.width_slots == 2 and got.guard_slots == 0
    plain = buda.NdrSpec()
    got, ok = buda.ndr_resolve_on_layer(plain, 5, dg)
    assert ok and not got.active()
