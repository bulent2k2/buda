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

"""The TOP re-seat heal (#536 follow-up to the supply-doomed seat census).

The dead-span escalation family's only move is LOW -> TOP, so a TOP seat
whose placed window's real signal-track supply falls short of its member
bits (the census predicate) has no escape and strands every bit.  The heal
(`_final_reseat_heal`, hooked at run_detailed_nuts after the cull heal)
re-seats such a segment on an alternate same-direction TOP layer whose
SPAN-CLEAR supply in the same bounded window covers the member bits, under
the cull heal's componentwise accept (opens strictly down AND overlaps not
up, bisect on rejection).  Boundary: it skips when a healer already ran
this session (`_healers_ran`) instead of the cull heal's blanket
locked-session scope-out — the re-roll hazard needs downstream healers,
and the heal's market (chip_bottomup's TOP seats) IS a locked session with
no healers.

Fixture: the dead-span L-shape scaffold with TWO TOP H layers (M4, M6).
A keepout kills all but 4 of M4's tracks in the H stub's window (< the
8-bit bus), so the M4-pinned stub is a doomed TOP seat; M6 is clean, so
the re-seat has a guaranteed host.
"""
import contextlib
import io

import buda
import buda_cli


_SETUP = [
    "def_layer 2 M2 H 50",
    "def_layer 3 M3 V 50",
    "def_layer 4 M4 H TOP 50",
    "def_layer 5 M5 V TOP 50",
    "def_layer 6 M6 H TOP 50",
    "def_track_pattern 2 0 SIGNAL 1 1",
    "def_track_pattern 3 0 SIGNAL 1 1",
    "def_track_pattern 4 0 SIGNAL 1 1",
    "def_track_pattern 5 0 SIGNAL 1 1",
    "def_track_pattern 6 0 SIGNAL 1 1",
    "add_block A 0 0 40 40",
    "add_block B 60 400 100 440",
    "add_bus d[8] A.p B.p",
    "run_bundler strict",
    "generate_topologies",
    "run_planner 5",
]


def _build(keepouts=()):
    """Pin the H stub to TOP M4, apply the given keepouts."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        for c in _SETUP:
            s.do_command(c)
        for k in keepouts:
            s.do_command(k)
    w = s.bundles[0]
    sel = w.plan.selected_topology_index
    sl = list(w.plan.seg_layers)
    h_idx = None
    for si, seg in enumerate(w.input.candidates[sel].segments):
        if seg.start.y == seg.end.y:
            sl[si] = 4
            h_idx = si
    w.plan.seg_layers = sl
    return s, h_idx


def _nuts(s):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), buda.ostream_redirect():
        s.do_command("run_nuts")
        s.do_command("run_detailed_nuts")
    return buf.getvalue()


_DOOM_M4 = "add_keepout 0 0 200 32 4"    # 4 of M4's window tracks survive


def test_doomed_top_seat_strands_without_heal():
    """Baseline (heal switched off): the M4 seat has 4 tracks for 8 bits —
    admission strands all 8 bits, and the seat stays on M4."""
    s, h_idx = _build([_DOOM_M4])
    s._final_reseat_heal_in_dnuts = False
    _nuts(s)
    assert s.detailed_result.num_unplaced == 8
    assert list(s.bundles[0].plan.seg_layers)[h_idx] == 4


def test_reseat_heal_moves_seat_to_alternate_top():
    """Default: the heal fires at run_detailed_nuts, re-seats the doomed M4
    stub on the clean sibling TOP layer M6, and every bit places with no
    overlap cost."""
    s, h_idx = _build([_DOOM_M4])
    out = _nuts(s)
    assert "RESEAT-HEAL" in out
    assert s.detailed_result.num_unplaced == 0
    assert s.nuts_result.num_overlaps == 0
    assert list(s.bundles[0].plan.seg_layers)[h_idx] == 6


def test_reseat_heal_noop_when_supply_suffices():
    """No keepout — the M4 seat hosts all 8 bits, the heal never fires."""
    s, h_idx = _build()
    out = _nuts(s)
    assert "RESEAT-HEAL" not in out
    assert s.detailed_result.num_unplaced == 0
    assert list(s.bundles[0].plan.seg_layers)[h_idx] == 4


def test_reseat_heal_leaves_seat_when_no_alternate_hosts():
    """The b61 shape: every same-direction TOP alternate is also short of
    supply (the keepout carves M6's window too) — the mover finds no
    guaranteed host and leaves the seat alone rather than churning."""
    s, h_idx = _build([_DOOM_M4, "add_keepout 0 0 200 32 6"])
    out = _nuts(s)
    assert "RESEAT-HEAL" not in out
    assert s.detailed_result.num_unplaced == 8
    assert list(s.bundles[0].plan.seg_layers)[h_idx] == 4


def test_reseat_heal_skips_after_healers_ran():
    """The past-healer gate: once negotiate/ripup/refine have run in this
    session, the flow owns its healing — the final re-seat heal must stand
    down (the aligned_sql re-roll hazard)."""
    s, h_idx = _build([_DOOM_M4])
    s._healers_ran = True
    out = _nuts(s)
    assert "RESEAT-HEAL" not in out
    assert s.detailed_result.num_unplaced == 8
    assert list(s.bundles[0].plan.seg_layers)[h_idx] == 4


def test_healer_commands_stamp_the_gate():
    """negotiate_congestion / ripup_reroute / refine_selection stamp
    `_healers_ran` even on their error-return paths, so ANY healer
    invocation closes the gate."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    assert not getattr(s, "_healers_ran", False)
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        s.do_command("negotiate_congestion")     # errors out (no bundles)...
    assert getattr(s, "_healers_ran", False)     # ...but the stamp is set


def test_reseat_heal_excludes_locked_wrappers():
    """A hier.locked wrapper's seats are never re-seated (its routing is a
    uniform fixed copy) — with the only doomed seat on a locked bundle the
    heal is a no-op, though the SESSION is not scoped out."""
    s, h_idx = _build([_DOOM_M4])
    s.bundles[0].hier.locked = True
    out = _nuts(s)
    assert "RESEAT-HEAL" not in out
    assert list(s.bundles[0].plan.seg_layers)[h_idx] == 4
