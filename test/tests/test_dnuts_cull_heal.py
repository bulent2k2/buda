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

"""The #523 spreader outcome: span-clear-first ranking + the DNUTS cull heal.

The anchor-interval pull model concentrates segments at accurate optima, and
on HEALERLESS flows the loss materialized as keepout CULLS: a LOW segment
whose abstract span crosses a keepout has span-clear pool < member bits, the
midpoint fallback admits the bits anyway (the keepout misses the along-
midpoint sample), and cull_keepout_crossers then strands them.  Two levers,
both measured on the bigHalf no-rr vehicle (102 culled bits -> 0/0):

1. SPAN-CLEAR-FIRST ranking (detailed_nuts.cpp): when the midpoint fallback
   engages, the pool mixes guaranteed-survivor tracks (clear across the whole
   abstract span) with midpoint-only tracks (survive only if the final
   junction-snapped span dodges the keepout).  Path A's nearest-to-anchor
   pick was blind to the difference — bigHalf b21 left 8 clear tracks idle
   while all 36 bits culled.  Clear tracks now rank first.

2. THE CULL HEAL (_final_cull_heal, hooked at run_detailed_nuts): the
   cull-risk escalation tier lives in ripup's refold, which healerless flows
   never reach.  The same tier runs after the DNUTS solve under a
   COMPONENTWISE accept — opens strictly down AND overlaps not up (the
   refold's lexicographic trade is safe only when a later ripup grinds the
   collateral overlaps; healerless flows keep them) — bisecting the batch on
   rejection so an accepted escalation is always pure improvement.

Fixture geometry (the L-shape scaffold): the H stub's abstract span is
[40, 68] with along-midpoint 54; a keepout at x in [56, 68] OVERLAPS the span
but MISSES the midpoint sample, which is exactly the admit-then-cull shape.
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
    "def_track_pattern 2 0 SIGNAL 1 1",
    "def_track_pattern 3 0 SIGNAL 1 1",
    "def_track_pattern 4 0 SIGNAL 1 1",
    "def_track_pattern 5 0 SIGNAL 1 1",
    "add_block A 0 0 40 40",
    "add_block B 60 400 100 440",
    "add_bus d[8] A.p B.p",
    "run_bundler strict",
    "generate_topologies",
    "run_planner 5",
]


def _build(keepout):
    """L-shape flow with the H stub pinned to LOW M2 and the given keepout
    (declared post-planning, pre-NUTS — the same shape the escalation tests
    use).  Returns (session, h_seg_idx)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        for c in _SETUP:
            s.do_command(c)
        s.do_command(keepout)
    w = s.bundles[0]
    sel = w.plan.selected_topology_index
    sl = list(w.plan.seg_layers)
    h_idx = None
    for si, seg in enumerate(w.input.candidates[sel].segments):
        if seg.start.y == seg.end.y:
            sl[si] = 2
            h_idx = si
    w.plan.seg_layers = sl
    return s, h_idx


def _nuts(s, heal=True):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), buda.ostream_redirect():
        if not heal:
            s._heal_dead_spans_in_healers = False
        s.do_command("run_nuts")
        s.do_command("run_detailed_nuts")
    return buf.getvalue()


def test_admit_then_cull_shape_without_heal():
    """Baseline (heal family opted out): the keepout overlaps the span but
    misses the along-midpoint, so all 8 bits ADMIT via the midpoint pool and
    are then culled — unplaced == 8, all of them keepout-cull bits."""
    s, h_idx = _build("add_keepout 56 0 68 500 2")
    _nuts(s, heal=False)
    assert s.detailed_result.num_unplaced == 8
    assert s.detailed_result.num_keepout_bits == 8
    assert list(s.bundles[0].plan.seg_layers)[h_idx] == 2   # still LOW M2


def test_cull_heal_escalates_and_places_all_bits():
    """Default: the cull heal fires at run_detailed_nuts, escalates the
    doomed LOW stub to TOP M4 under the componentwise accept, and every bit
    places."""
    s, h_idx = _build("add_keepout 56 0 68 500 2")
    out = _nuts(s, heal=True)
    assert "CULL-HEAL" in out
    assert s.detailed_result.num_unplaced == 0
    assert s.nuts_result.num_overlaps == 0
    assert list(s.bundles[0].plan.seg_layers)[h_idx] == 4   # TOP M4


def test_cull_heal_noop_without_culls():
    """No keepout — nothing culls, the heal never fires, the stub stays on
    its LOW layer (byte-identical no-op path)."""
    s, h_idx = _build("corner_margin dx 0")     # harmless no-op command
    out = _nuts(s, heal=True)
    assert "CULL-HEAL" not in out
    assert s.detailed_result.num_unplaced == 0
    assert list(s.bundles[0].plan.seg_layers)[h_idx] == 2   # unchanged LOW M2


def test_cull_heal_checkpoints_route_when_bdb_open(monkeypatch):
    """An ACCEPTED heal changed plan.seg_layers and the abstract solve; with
    an open BDB the pre-heal bus_segment/bus_via rows and planner
    assignments would otherwise go stale under the healed detailed rows,
    and load_pipeline would resume the un-healed abstract route (Codex P1
    on #543).  The accept path must re-checkpoint the whole routing state,
    exactly as the refold and the stage-b heal do."""
    s, h_idx = _build("add_keepout 56 0 68 500 2")
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        s.do_command("run_nuts")
        s._run_detailed_nuts(bit_order="LO_HI")   # solve only, no heal yet
    assert s.detailed_result.num_keepout_bits == 8
    calls = []
    s.bdb = object()   # non-None sentinel; _checkpoint_routing is stubbed
    monkeypatch.setattr(s, "_checkpoint_routing",
                        lambda *a, **k: calls.append(1))
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        n = s._final_cull_heal()
    assert n >= 1
    assert calls == [1]        # the heal persisted its own route
    assert s.detailed_result.num_unplaced == 0


def test_cull_heal_no_checkpoint_when_nothing_culled(monkeypatch):
    """No culled bits → the heal is a true no-op and must not touch the
    BDB."""
    s, h_idx = _build("corner_margin dx 0")
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        s.do_command("run_nuts")
        s._run_detailed_nuts(bit_order="LO_HI")
    calls = []
    s.bdb = object()
    monkeypatch.setattr(s, "_checkpoint_routing",
                        lambda *a, **k: calls.append(1))
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        n = s._final_cull_heal()
    assert n == 0
    assert calls == []


def test_cull_heal_scoped_out_of_bottom_up_sessions():
    """A session with any hier.locked wrapper (a bottom-up flow) must not
    run the heal — the same blunt boundary as ripup's width gate: the
    componentwise accept protects THIS stage, but a locked-template flow's
    downstream healers re-roll from the changed start state (aligned_sql
    walked 2/16/1 -> 6/58/9 before the scope-out).  The doomed stub
    therefore stays stranded on its LOW layer, exactly as with the heal
    family opted out."""
    s, h_idx = _build("add_keepout 56 0 68 500 2")
    s.bundles[0].hier.locked = True
    out = _nuts(s, heal=True)
    assert "CULL-HEAL" not in out
    assert s.detailed_result.num_unplaced == 8
    assert list(s.bundles[0].plan.seg_layers)[h_idx] == 2   # untouched LOW M2


def test_span_clear_tracks_rank_first():
    """The ranking lever, isolated on a raw engine fixture: a keepout at
    y <= 32, x in [56, 68] overlaps the span [40, 100] but misses its
    along-midpoint (70), leaving 4 span-CLEAR tracks (centres 32.5..39.5 of
    the 20 in interval [0, 40]) and 16 midpoint-only ones.  The 8-bit bus
    is anchored at 20 — nearest-to-anchor alone would pick 8 tracks around
    y=20, ALL of which cull.  Span-clear-first must claim the 4 guaranteed
    survivors, so exactly 4 bits cull instead of 8."""
    slots = [buda.TrackSlot(type="SIGNAL", label="sig",
                            width=1.0, space_after=1.0)]
    stack = buda.RoutingGridStack()
    stack.define_layer(2, buda.TrackPattern(origin=0.0, slots=slots), True)
    stack.add_keepout(2, 56, 0, 68, 32)
    bs = buda.BusSegment()
    bs.bundle_id = 1
    bs.seg_idx = 0
    bs.layer = 2
    bs.span_lo, bs.span_hi = 40.0, 100.0
    bs.interval_lo, bs.interval_hi = 0.0, 40.0
    bs.bit_width = 8
    bs.abstract_pos = 20.0
    r = buda.DetailedNUTSEngine(stack).run([bs])
    placed = sorted(ns.track_position for ns in r.net_segments)
    clear_used = sum(1 for p in placed if p > 32)
    assert clear_used == 4, (
        f"span-clear survivors not claimed: placed={placed}")
    assert r.num_keepout_bits == 4
    assert r.num_unplaced == 4
