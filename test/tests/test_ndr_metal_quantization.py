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
    # RAIL-TERMINATED so the period cannot join its own next copy — that
    # would be the cross-period splice, which is a different (and legal)
    # thing, covered below.
    split = _pattern([("SIGNAL", 1, 1)] * 2 + [("POWER", 2, 1)]
                     + [("SIGNAL", 1, 1)] * 3 + [("GROUND", 2, 1)])
    g = split.ndr_geom()
    assert [len(r) for r in g.runs] == [2, 3]
    assert buda.ndr_max_slots(g) == 3
    assert buda.ndr_metal_for_slots(g, 3) == pytest.approx(5)
    assert buda.ndr_metal_for_slots(g, 4) < 0, "a 4-slot wire would cross the rail"


def test_runs_join_across_the_period_boundary_even_with_an_interior_rail():
    """The tail run of one period abuts the head run of the next whenever no
    rail sits between — which depends on the RAIL, not on how many runs the
    period happens to have (Codex P2 on #717).

    `SIGNAL POWER SIGNAL` is two runs of one, yet a legal TWO-slot wire
    straddles the boundary; guarding on `runs.size() == 1` reported max 1 and
    would have falsely refused it."""
    g = _pattern([("SIGNAL", 1, 1), ("POWER", 2, 1),
                  ("SIGNAL", 1, 1)]).ndr_geom()
    assert buda.ndr_max_slots(g) == 2
    # …and the interior rail still blocks a wire that would cross IT.
    assert buda.ndr_metal_for_slots(g, 3) < 0


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


def _spec(width_abs=0.0, spacing_abs=0.0, metal=True):
    """Metal-shaped by default HERE because that is what this file is about.

    The engine default is the CHANNEL reading (`metal_quant=False`): the
    metal one is opt-in per rule via `def_ndr ... metal`, because it is
    stricter rather than merely different — a layer whose signal slots are
    isolated between rails can deliver only one slot's metal, so a width the
    channel reading accepted (by silently delivering half of it) becomes an
    R3 refusal.  See `test_the_channel_reading_is_still_the_default`."""
    s = buda.NdrSpec()
    s.width_abs, s.spacing_abs = width_abs, spacing_abs
    s.metal_quant = metal
    return s


def test_the_channel_reading_is_still_the_default():
    """R12: a design that does not ask for metal must be byte-identical.

    The same declaration reads differently under the two, which is the whole
    point — `width 8` on the sparse pattern costs 1 slot as channel (period
    40 over 4 signal slots -> pitch 10) and 3 as metal (4k-2 >= 8)."""
    sg = _pattern(SPARSE).ndr_geom()
    pitch = _pattern(SPARSE).unit_pitch() / 4
    channel = _spec(width_abs=8, metal=False)
    assert not channel.metal_quant
    got, ok = buda.ndr_resolve_on_layer(channel, 6, sg, pitch)
    assert ok and got.width_slots == 1, "the default reading must not move"
    got, ok = buda.ndr_resolve_on_layer(_spec(width_abs=8), 6, sg, pitch)
    assert ok and got.width_slots == 3


def test_metal_is_stricter_where_a_layer_cannot_deliver():
    """Why metal is opt-in: it REFUSES designs the channel reading routes.

    A pattern whose signal slots are isolated between rails offers runs of
    one slot.  `width 4` there is channel-quantized to 1 slot — delivering 2
    units, half what was declared, silently — while metal reports it as
    unrealizable, which is R3's job."""
    iso = _pattern([("POWER", 2, 2), ("SIGNAL", 2, 2),
                    ("GROUND", 2, 2), ("SIGNAL", 2, 2)] * 3)
    g, pitch = iso.ndr_geom(), iso.unit_pitch() / 6
    assert buda.ndr_max_slots(g) == 1 and buda.ndr_metal_for_slots(g, 1) == 2
    got, ok = buda.ndr_resolve_on_layer(_spec(width_abs=4, metal=False), 4, g, pitch)
    assert ok and got.width_slots == 1, "channel accepts it, delivering 2 of 4"
    _got, ok = buda.ndr_resolve_on_layer(_spec(width_abs=4), 4, g, pitch)
    assert not ok, "metal must refuse what the layer cannot deliver"


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


def _flow(tmp_path, body):
    """Run a .buda body through the CLI, returning (stdout+stderr, rc)."""
    import subprocess, sys, os
    f = tmp_path / "t.buda"
    f.write_text(body)
    env = dict(os.environ, PYTHONPATH="build:src:tools")
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    r = subprocess.run([sys.executable, "src/buda_cli.py", str(f), "--no-viz"],
                       capture_output=True, text=True, cwd=root, env=env)
    return r.stdout + r.stderr, r.returncode


_STACK = """add_block a 0 0 200 200
add_block b 900 0 1100 200
add_bus w_[2] a.p b.q
def_layer 3 M3 H TOP 20
def_layer 4 M4 V 20
"""
_DENSE = "def_track_pattern 3 0 VDD 2 1 (_ 1 1)x12 GND 2 1\n"
# Every signal slot isolated between rails: runs of ONE, 2 units of metal.
_ISO = "def_track_pattern 4 0 (VDD 2 2 _ 2 2 GND 2 2 _ 2 2)x3\n"


def test_an_unrealizable_metal_rule_is_refused_not_quantized_to_the_clamp(tmp_path):
    """R3 at the declaration.  `ndr_resolve_on_layer` CLAMPS an unrealizable
    value to the layer's longest run, so caching that count would let the
    later check see a number that fits by construction and route the rule
    below its declared width — silently (Codex P1 on #717)."""
    out, rc = _flow(tmp_path, _STACK + _DENSE
                    + "def_track_pattern 4 0 VDD 2 1 (_ 1 1)x12 GND 2 1\n"
                    + "def_ndr big width 40 metal\nset_ndr w_ big\n"
                      "run_bundler STRICT\n")
    assert rc != 0, out
    assert "cannot realize the declared value" in out, out
    assert "NDR rule 'big'" in out, "the refusal must name the rule"


def test_a_per_layer_override_is_validated_in_the_parent_rules_reading(tmp_path):
    """The `def_ndr_layer` probe left `metal_quant` at its default and passed
    no pitch, so it took the channel branch, returned early and reported OK
    unconditionally — an advertised R3 check that tested nothing."""
    out, _rc = _flow(tmp_path, _STACK + _DENSE + _ISO
                     + "def_ndr em width 2 metal\n"
                       "def_ndr_layer em M4 width 9\n")
    assert "def_ndr_layer 'em'" in out and "cannot realize" in out, out


def test_the_same_override_is_accepted_when_the_layer_can_deliver(tmp_path):
    """Non-vacuity: the refusal above must be about the ARITHMETIC, not about
    per-layer overrides being rejected generally."""
    out, rc = _flow(tmp_path, _STACK + _DENSE
                    + "def_track_pattern 4 0 VDD 2 1 (_ 1 1)x12 GND 2 1\n"
                    + "def_ndr em width 2 metal\n"
                      "def_ndr_layer em M4 width 9\n")
    assert rc == 0, out
    assert "cannot realize" not in out, out
