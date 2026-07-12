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

"""kPeak absolute-supply floor (wishlist-planner "Selection basis" follow-on).

`peak_util_segment`'s usage/cap is purely RELATIVE: a band reports util=0
— maximally attractive — whether it is empty-and-roomy or empty because a
power-heavy pattern override leaves it too few real signal tracks to host
the bus at all.  The width capacity model cannot see the difference either
(eff_bus_width uses the LAYER's global pattern; a region override is
invisible to it), so the planner happily routes an 8-bit bus through a
band whose supply is 3 tracks — an honest, deterministic DetailedNUTS
"insufficient signal tracks" open.

The floor: when kPeak > 0 and the layer has a def_track_pattern, the
band's real span-wide SIGNAL supply (count_signal_tracks_in_span — the
same override-aware, keepout-aware pool DetailedNUTS places from) is
checked against the bundle's bit count, and util is clamped to >= 1 when
it falls short: an empty-because-unroutable band must never rank better
than a 100%-full one.

Scenario: S→D straight I_H crosses a corridor band whose M4 pattern is
overridden to ~3 signal tracks; the helper block's Hanan row offers a
detour band on the healthy global pattern (~50 tracks).  Everything is
congestion-free, so only the supply floor differentiates.

Default OFF: with kPeak=0 the term (and the floor) is skipped entirely —
the blind selection and its DNUTS opens are asserted too, documenting the
exact failure the floor removes.
"""
import io
import contextlib

import buda_cli


def _route(kpeak, bits):
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = ["def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
            "add_block S 0 0 100 100", "add_block D 900 0 1000 100",
            # helper above the corridor: provides the detour Hanan row
            "add_block H1 1200 140 1300 200",
            # healthy global patterns (pitch 2, 50% signal density)
            "def_track_pattern 4 0 SIGNAL 1 1",
            "def_track_pattern 5 0 SIGNAL 1 1",
            # corridor band override: power-heavy, ~3 signal tracks in y[0,100].
            # (Historical note: this ends at 99 because the span walkers used
            # to resolve the pattern at the window's perp_lo, so an override
            # reaching the Hanan row at 100 claimed the band above it too.
            # Per-slice resolution fixed that — see
            # test_boundary_touching_override_detour_places below — and 99 is
            # kept so this fixture stays byte-stable.)
            "add_grid_override 4 0 0 1400 99 0 POWER 28 1 SIGNAL 1 0",
            f"add_bus d[{bits}] S.p D.p",
            "run_bundler strict", "generate_topologies"]
    if kpeak > 0:
        cmds.append(f"set_planner_param kPeak {kpeak}")
    cmds += ["run_planner", "run_nuts", "run_detailed_nuts"]
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    w = s.bundles[0]
    sel = w.input.candidates[w.plan.selected_topology_index].type
    return s, sel


def test_default_routes_into_the_supply_poor_band():
    """kPeak=0: the corridor is empty and the width model can't see the
    override, so the shortest route wins — and DetailedNUTS honestly strands
    every bit (the failure mode the floor exists to prevent)."""
    s, sel = _route(0, bits=8)
    assert sel == "I_H", sel
    assert s.detailed_result.num_unplaced == 8


def test_kpeak_floor_steers_off_the_supply_poor_band():
    """kPeak on: the corridor band's supply (3) can't host 8 bits, so the
    floor prices it as full and the probe detours to the healthy band —
    all bits place."""
    s, sel = _route(0.2, bits=8)
    assert sel.startswith("U_"), sel
    assert s.detailed_result.num_unplaced == 0


def test_floor_silent_when_supply_suffices():
    """A 2-bit bus fits the corridor's 3 tracks: the floor must not fire,
    and the shortest route keeps winning with kPeak on."""
    s, sel = _route(0.2, bits=2)
    assert sel == "I_H", sel
    assert s.detailed_result.num_unplaced == 0


def test_boundary_touching_override_detour_places():
    """End-to-end regression for the override-boundary fix (per-slice pattern
    resolution in the span walkers): with the corridor override ending
    EXACTLY on the Hanan row at 120, the pre-fix walkers read the detour
    band above it as supply-dead (the override claimed the whole band via
    the perp_lo point sample) — the kPeak-steered detour route itself
    stranded all 8 bits.  With per-slice resolution the detour band reads
    its real (healthy) pattern and every bit places."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = ["def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
            "add_block S 0 0 100 100", "add_block D 900 0 1000 100",
            "add_block H1 1200 140 1300 200",
            "def_track_pattern 4 0 SIGNAL 1 1",
            "def_track_pattern 5 0 SIGNAL 1 1",
            "add_grid_override 4 0 0 1400 120 0 POWER 28 1 SIGNAL 1 0",
            "add_bus d[8] S.p D.p",
            "run_bundler strict", "generate_topologies",
            "set_planner_param kPeak 0.2",
            "run_planner", "run_nuts", "run_detailed_nuts"]
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    w = s.bundles[0]
    sel = w.input.candidates[w.plan.selected_topology_index].type
    assert sel != "I_H", sel                     # floor still steers off the corridor
    assert s.detailed_result.num_unplaced == 0   # and the detour now places
