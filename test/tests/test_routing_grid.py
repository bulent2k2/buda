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

"""
Routing Grid (Stage 8) — TDD tests.

Tests are written against the planned API from CLAUDE.md Stage 8.
All tests are expected to FAIL until the C++ implementation is complete
and exposed via pybind11.  That is intentional — this file is the
red phase of TDD.

Expected Python API (to be implemented):
    buda.TrackSlot(type, label, width, space_after)
    buda.TrackPattern(origin, slots)
      .unit_pitch()         -> float
      .signal_density()     -> float
      .dilution_factor()    -> float
      .tracks_in_range(lo, hi) -> list[(centre: float, slot: TrackSlot)]
    buda.RoutingGridStack()
      .define_layer(layer_id: int, pattern: TrackPattern)
      .add_override(layer_id, x1, y1, x2, y2, pattern: TrackPattern)
      .get_layer_grid(layer_id) -> RoutingGrid
    buda.RoutingGrid
      .effective_pattern_at(x, y) -> TrackPattern
      .signal_tracks_in(x, lo, hi) -> list[(centre: float, slot: TrackSlot)]
"""
import math
import pytest
import buda


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_slot(slot_type, label, width, space_after):
    return buda.TrackSlot(
        type=slot_type, label=label, width=width, space_after=space_after
    )


def make_standard_pattern(origin=0.0):
    """[POWER(w=2,sp=1), SIG(w=1,sp=1), SIG(w=1,sp=1),
        GROUND(w=2,sp=1), SIG(w=1,sp=1), SIG(w=1,sp=1)]
    unit_pitch=14, signal_density=4/14, dilution_factor=3.5
    Centres (origin=0): POWER@1, SIG@3.5, SIG@5.5, GND@8, SIG@10.5, SIG@12.5
    """
    slots = [
        make_slot("POWER",  "VDD", 2.0, 1.0),
        make_slot("SIGNAL", "sig", 1.0, 1.0),
        make_slot("SIGNAL", "sig", 1.0, 1.0),
        make_slot("GROUND", "GND", 2.0, 1.0),
        make_slot("SIGNAL", "sig", 1.0, 1.0),
        make_slot("SIGNAL", "sig", 1.0, 1.0),
    ]
    return buda.TrackPattern(origin=origin, slots=slots)


def centres_of(tracks):
    """Extract list of track centre positions from tracks_in_range result."""
    return [c for c, _ in tracks]


def types_of(tracks):
    """Extract list of slot types from tracks_in_range result."""
    return [s.type for _, s in tracks]


# ---------------------------------------------------------------------------
# TrackPattern — unit_pitch
# ---------------------------------------------------------------------------

def test_unit_pitch_sum_of_width_plus_space_after():
    p = make_standard_pattern()
    # (2+1) + (1+1) + (1+1) + (2+1) + (1+1) + (1+1) = 3+2+2+3+2+2 = 14
    assert p.unit_pitch() == pytest.approx(14.0)


def test_unit_pitch_single_slot():
    p = buda.TrackPattern(
        origin=0.0,
        slots=[make_slot("SIGNAL", "s", width=3.0, space_after=2.0)]
    )
    assert p.unit_pitch() == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# TrackPattern — signal_density and dilution_factor
# ---------------------------------------------------------------------------

def test_signal_density_fraction_of_signal_widths():
    p = make_standard_pattern()
    # 4 signal-width units out of 14 total
    assert p.signal_density() == pytest.approx(4.0 / 14.0, rel=1e-4)


def test_dilution_factor_reciprocal_of_signal_density():
    p = make_standard_pattern()
    assert p.dilution_factor() == pytest.approx(14.0 / 4.0, rel=1e-4)


def test_all_signal_slots_density_is_one():
    p = buda.TrackPattern(
        origin=0.0,
        slots=[
            make_slot("SIGNAL", "s", 1.0, 0.0),
            make_slot("SIGNAL", "s", 1.0, 0.0),
        ]
    )
    assert p.signal_density() == pytest.approx(1.0)
    assert p.dilution_factor() == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# TrackPattern — tracks_in_range
# ---------------------------------------------------------------------------

def test_tracks_in_range_one_full_unit():
    p = make_standard_pattern(origin=0.0)
    tracks = p.tracks_in_range(lo=0.0, hi=14.0)
    assert len(tracks) == 6
    expected_centres = [1.0, 3.5, 5.5, 8.0, 10.5, 12.5]
    for (c, _), exp in zip(tracks, expected_centres):
        assert c == pytest.approx(exp)


def test_tracks_in_range_types_match_slot_order():
    p = make_standard_pattern(origin=0.0)
    tracks = p.tracks_in_range(lo=0.0, hi=14.0)
    assert types_of(tracks) == ["POWER", "SIGNAL", "SIGNAL", "GROUND", "SIGNAL", "SIGNAL"]


def test_tracks_in_range_two_full_units():
    p = make_standard_pattern(origin=0.0)
    tracks = p.tracks_in_range(lo=0.0, hi=28.0)
    assert len(tracks) == 12
    # 7th track (index 6) is the POWER of the second unit: centre = 14+1 = 15
    assert tracks[6][0] == pytest.approx(15.0)
    assert tracks[6][1].type == "POWER"


def test_tracks_in_range_excludes_centres_outside_interval():
    p = make_standard_pattern(origin=0.0)
    # Centres in [0,14]: 1.0(out), 3.5(out), 5.5(in), 8.0(in), 10.5(in), 12.5(out)
    tracks = p.tracks_in_range(lo=4.0, hi=11.0)
    cs = centres_of(tracks)
    assert len(cs) == 3
    for c in cs:
        assert 4.0 <= c <= 11.0


def test_tracks_in_range_non_zero_origin_shifts_centres():
    p = make_standard_pattern(origin=3.0)
    tracks = p.tracks_in_range(lo=3.0, hi=17.0)
    # First centre = 3 + 1 = 4.0 (POWER)
    assert tracks[0][0] == pytest.approx(4.0)
    assert tracks[0][1].type == "POWER"


def test_tracks_in_range_empty_when_interval_contains_no_centres():
    p = make_standard_pattern(origin=0.0)
    # Interval [1.1, 3.4] — no centres (1.0 just outside, 3.5 just outside)
    tracks = p.tracks_in_range(lo=1.1, hi=3.4)
    assert tracks == []


# ---------------------------------------------------------------------------
# signal_tracks_in — SIGNAL-only filter
# ---------------------------------------------------------------------------

def test_signal_tracks_in_returns_only_signal_type():
    p = make_standard_pattern(origin=0.0)
    tracks = p.tracks_in_range(lo=0.0, hi=14.0)
    signal_only = [(c, s) for c, s in tracks if s.type == "SIGNAL"]
    # Verify via dedicated helper if RoutingGrid is the intended caller
    assert all(s.type == "SIGNAL" for _, s in signal_only)
    assert len(signal_only) == 4
    assert centres_of(signal_only) == pytest.approx([3.5, 5.5, 10.5, 12.5])


def test_signal_tracks_in_narrow_interval():
    p = make_standard_pattern(origin=0.0)
    # [0, 7] contains POWER@1, SIG@3.5, SIG@5.5 — GND@8 is outside
    tracks = p.tracks_in_range(lo=0.0, hi=7.0)
    sig = [(c, s) for c, s in tracks if s.type == "SIGNAL"]
    assert len(sig) == 2
    assert centres_of(sig) == pytest.approx([3.5, 5.5])


# ---------------------------------------------------------------------------
# RoutingGridStack — define_layer and get_layer_grid
# ---------------------------------------------------------------------------

def test_routing_grid_stack_define_and_retrieve():
    stack = buda.RoutingGridStack()
    p = make_standard_pattern()
    stack.define_layer(4, p, True)
    grid = stack.get_layer_grid(4)
    assert grid is not None


def test_routing_grid_signal_tracks_in_via_stack():
    stack = buda.RoutingGridStack()
    stack.define_layer(4, make_standard_pattern(origin=0.0), True)
    grid = stack.get_layer_grid(4)
    tracks = grid.signal_tracks_in(x=0.0, lo=0.0, hi=14.0)
    assert len(tracks) == 4
    assert all(s.type == "SIGNAL" for _, s in tracks)
    assert centres_of(tracks) == pytest.approx([3.5, 5.5, 10.5, 12.5])


def test_routing_grid_signal_tracks_two_units():
    stack = buda.RoutingGridStack()
    stack.define_layer(4, make_standard_pattern(origin=0.0), True)
    grid = stack.get_layer_grid(4)
    tracks = grid.signal_tracks_in(x=0.0, lo=0.0, hi=28.0)
    assert len(tracks) == 8  # 4 signal per unit × 2 units


def test_count_signal_tracks_in_matches_size():
    """count_signal_tracks_in (the allocation-free hot-path twin used by the
    congestion planner's signal-track capacity) must return exactly
    len(signal_tracks_in(...)) for every interval — including keepout-blocked
    tracks and multi-unit ranges."""
    stack = buda.RoutingGridStack()
    stack.define_layer(4, make_standard_pattern(origin=0.0), True)
    # Block one signal track band with a keepout (H layer: y=track coord).
    stack.add_keepout(4, x1=-1000, y1=5, x2=1000, y2=6)   # covers SIG@5.5
    grid = stack.get_layer_grid(4)
    for lo, hi in [(0.0, 14.0), (0.0, 7.0), (0.0, 28.0), (3.0, 11.0),
                   (5.4, 5.6), (100.0, 100.0), (-5.0, 3.6)]:
        want = len(grid.signal_tracks_in(x=0.0, lo=lo, hi=hi))
        got = grid.count_signal_tracks_in(x=0.0, lo=lo, hi=hi)
        assert got == want, f"[{lo},{hi}]: count={got} vs size={want}"


def test_get_undefined_layer_raises():
    stack = buda.RoutingGridStack()
    with pytest.raises(Exception):
        stack.get_layer_grid(99)


# ---------------------------------------------------------------------------
# PatternOverride — region-scoped pattern takes precedence
# ---------------------------------------------------------------------------

def make_two_slot_pattern(origin=0.0):
    """Simple 2-slot pattern: [POWER(w=1,sp=0), SIGNAL(w=1,sp=0)] — unit_pitch=2"""
    slots = [
        make_slot("POWER",  "VDD", 1.0, 0.0),
        make_slot("SIGNAL", "sig", 1.0, 0.0),
    ]
    return buda.TrackPattern(origin=origin, slots=slots)


def test_override_pattern_applies_inside_region():
    stack = buda.RoutingGridStack()
    stack.define_layer(4, make_standard_pattern(origin=0.0), True)
    override_pat = make_two_slot_pattern(origin=0.0)
    stack.add_override(4, x1=100, y1=0, x2=200, y2=500, pattern=override_pat)

    grid = stack.get_layer_grid(4)
    pat = grid.effective_pattern_at(x=150.0, y=250.0)
    assert pat.unit_pitch() == pytest.approx(2.0)


def test_global_pattern_applies_outside_override_region():
    stack = buda.RoutingGridStack()
    stack.define_layer(4, make_standard_pattern(origin=0.0), True)
    override_pat = make_two_slot_pattern(origin=0.0)
    stack.add_override(4, x1=100, y1=0, x2=200, y2=500, pattern=override_pat)

    grid = stack.get_layer_grid(4)
    pat = grid.effective_pattern_at(x=50.0, y=250.0)
    assert pat.unit_pitch() == pytest.approx(14.0)


def test_first_matching_override_wins():
    """When two overrides cover the same point, the first-added takes precedence."""
    stack = buda.RoutingGridStack()
    stack.define_layer(4, make_standard_pattern(origin=0.0), True)

    pat_a = make_two_slot_pattern(origin=0.0)           # unit_pitch=2
    pat_b = buda.TrackPattern(                   # unit_pitch=6
        origin=0.0,
        slots=[make_slot("SIGNAL", "s", 3.0, 3.0)]
    )
    stack.add_override(4, x1=0, y1=0, x2=300, y2=300, pattern=pat_a)
    stack.add_override(4, x1=0, y1=0, x2=300, y2=300, pattern=pat_b)

    grid = stack.get_layer_grid(4)
    pat = grid.effective_pattern_at(x=150.0, y=150.0)
    assert pat.unit_pitch() == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# dilution_factor integration — used by abstract NUTS
# ---------------------------------------------------------------------------

def test_dilution_factor_scales_abstract_bus_width():
    """The dilution_factor tells abstract NUTS how much wider an abstract segment
    must be to reserve the right amount of physical space when power/clock tracks
    consume part of the perpendicular interval."""
    p = make_standard_pattern()
    # 4 signal units in 14 → a 4-unit abstract bus needs 4 × 3.5 = 14 physical units
    assert 4.0 * p.dilution_factor() == pytest.approx(14.0, rel=1e-4)


# ---------------------------------------------------------------------------
# count_signal_tracks_in_span — lockstep with signal_tracks_in_span
# ---------------------------------------------------------------------------

def test_count_signal_tracks_in_span_lockstep():
    """count_signal_tracks_in_span is the allocation-free twin of
    signal_tracks_in_span (the planner's kPeak supply floor only needs the
    count).  "Kept in lockstep" is a promise only a test can keep: assert
    count == len(vector twin) across plain, keepout-clipped (span-aware:
    overlapping the span but missing its midpoint), override, and
    empty/degenerate windows, so drift is caught the moment someone edits
    one walker and not the other."""
    stack = buda.RoutingGridStack()
    stack.define_layer(4, make_standard_pattern(), True)
    # keepout overlapping the span [0, 100] but missing its midpoint 50 —
    # invisible to the point query, visible to both span-aware walkers
    stack.add_keepout(4, x1=60, y1=3, x2=80, y2=6)
    # region override with a different (power-heavy) pattern
    stack.add_override(4, x1=100, y1=0, x2=200, y2=500,
                       pattern=buda.TrackPattern(origin=0.0, slots=[
                           make_slot("POWER", "VDD", 28.0, 1.0),
                           make_slot("SIGNAL", "sig", 1.0, 0.0)]))
    grid = stack.get_layer_grid(4)
    windows = [
        (0.0, 100.0, 0.0, 14.0),      # global pattern, keepout clips the span
        (0.0, 50.0, 0.0, 14.0),       # span ends before the keepout
        (120.0, 180.0, 0.0, 60.0),    # inside the override region
        (0.0, 100.0, 7.0, 3.0),       # inverted perp window (degenerate)
        (90.0, 10.0, 0.0, 14.0),      # inverted along window (walkers swap)
        (0.0, 0.0, 0.0, 14.0),        # zero-length span (point special case)
    ]
    for a_lo, a_hi, p_lo, p_hi in windows:
        assert (grid.count_signal_tracks_in_span(a_lo, a_hi, p_lo, p_hi)
                == len(grid.signal_tracks_in_span(a_lo, a_hi, p_lo, p_hi))), \
            (a_lo, a_hi, p_lo, p_hi)


def test_boundary_touching_override_does_not_claim_band_above():
    """Per-slice pattern resolution (wishlist-nuts "override-boundary" fix):
    the span walkers used to resolve the pattern at the window's perp_lo, so
    an override whose edge exactly touched a Hanan row claimed the ENTIRE
    band above it — a physically healthy band read as supply-dead (this
    stranded the kPeak supply test's detour route during development).  The
    window now splits at override perp edges, each slice sampling its own
    midpoint."""
    stack = buda.RoutingGridStack()
    # healthy global: 1 signal track per 2 units
    stack.define_layer(4, buda.TrackPattern(origin=0.0, slots=[
        make_slot("SIGNAL", "sig", 1.0, 1.0)]), True)
    # power-heavy override ending EXACTLY at 120
    stack.add_override(4, x1=0, y1=0, x2=1400, y2=120,
                       pattern=buda.TrackPattern(origin=0.0, slots=[
                           make_slot("POWER", "VDD", 28.0, 1.0),
                           make_slot("SIGNAL", "sig", 1.0, 0.0)]))
    grid = stack.get_layer_grid(4)
    # the band above the boundary is on the GLOBAL pattern: 10 tracks
    assert grid.count_signal_tracks_in_span(100, 900, 120, 140) == 10
    # inside the override: the power-heavy pattern (~4 tracks in [0,120])
    assert grid.count_signal_tracks_in_span(100, 900, 0, 100) == 3
    # a window CROSSING the boundary counts both slices (1 override + 10 global)
    assert grid.count_signal_tracks_in_span(100, 900, 100, 140) == 11
    # and the twins agree on the sliced walk too
    assert (grid.count_signal_tracks_in_span(100, 900, 100, 140)
            == len(grid.signal_tracks_in_span(100, 900, 100, 140)))
