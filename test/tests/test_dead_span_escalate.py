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

"""Post-NUTS dead-span escalation (opt-in `set_dead_span_escalate on`).

A LOW-layer segment whose ACTUAL placed geometry (span + Hanan interval)
offers fewer keepout-clear signal tracks than its MEMBER BITS is a
guaranteed DetailedNUTS open — admission is all-or-nothing
(`place_by_layer`: span-clear pool, midpoint fallback, then the whole
segment strands on `n_sig < bus_seg_nbits`), so a partial-supply shortfall
loses every bit, not just the overflow.  (The historical threshold was
ZERO tracks — the member-bits predicate specialized to a 1-bit bus — and
was blind to the #518/#527 16-bits-over-4-tracks shape.)  The plan-time
`nontop_dead_span_gate` cannot tell such a cull from a survivor (the wide
slide window vs the narrow final interval), but the admission arithmetic on
the FINAL placed geometry fires only on segments whose every bit is already
lost.  The escalation moves each such LOW segment to the cheapest
same-direction TOP layer (full signal supply) and re-solves.  Measured
clean wins: bigHalf no-rr opens 315→171, mix-loci 42→16; byte-identical on
the rest of the corpus.  See wishlist-planner "dead-span discriminator".

The feature is OFF by default (every checked-in flow stays bit-identical); a
regression on the pathological mempool_tile stress demo is why it is opt-in.
"""
import contextlib
import io

import buda
import buda_cli


# LOW + TOP layer pair per direction, each with a simple 1-wide signal
# pattern.  Two blocks form an L-shape (a short horizontal reach + a tall
# vertical trunk); an 8-bit bus so a dead segment strands 8 bits, not 1.
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


def _build(pin_low, keepout_m2, partial=False):
    """Set up the L-shape flow; optionally pin its H stub to LOW M2 and/or
    lay a keepout on M2 — full-design (kills every M2 signal track) or
    `partial` (y <= 32: leaves 4 of the interval's 20 tracks alive, fewer
    than the 8-bit bus needs).  Returns the session and the pinned
    H-segment index (or None)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        for c in _SETUP:
            s.do_command(c)
        if keepout_m2:
            s.do_command("add_keepout 0 0 200 32 2" if partial
                         else "add_keepout 0 0 200 500 2")
    h_idx = None
    if pin_low:
        w = s.bundles[0]
        sel = w.plan.selected_topology_index
        topo = w.input.candidates[sel]
        sl = list(w.plan.seg_layers)
        for si, seg in enumerate(topo.segments):
            if seg.start.y == seg.end.y:      # horizontal stub
                sl[si] = 2                    # M2 (LOW-H)
                h_idx = si
        w.plan.seg_layers = sl
    return s, h_idx


def _nuts(s, escalate):
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        if escalate:
            s.do_command("set_dead_span_escalate on")
        s.do_command("run_nuts")
        # Subject here is the ESCALATION KNOB's own semantics.  The DNUTS
        # cull heal now engages on admission strands too (culled OR plain
        # unplaced bits), which would heal the "without escalation" baselines
        # these tests pin — hold it off so the knob stays observable in
        # isolation (test_dnuts_cull_heal.py owns the heal's coverage).
        s._final_cull_heal_in_dnuts = False
        s.do_command("run_detailed_nuts")


def test_dead_low_segment_strands_without_escalation():
    """Baseline: an H stub pinned to a keepout-dead LOW layer strands all 8
    bits (a DetailedNUTS open) and stays on the dead layer."""
    s, h_idx = _build(pin_low=True, keepout_m2=True)
    _nuts(s, escalate=False)
    assert s.detailed_result.num_unplaced == 8
    assert list(s.bundles[0].plan.seg_layers)[h_idx] == 2   # still LOW M2


def test_escalation_moves_dead_low_segment_to_top():
    """With escalation on, the dead M2 stub is moved to the cheapest TOP H
    layer (M4) and all 8 bits place."""
    s, h_idx = _build(pin_low=True, keepout_m2=True)
    _nuts(s, escalate=True)
    assert s.detailed_result.num_unplaced == 0
    assert list(s.bundles[0].plan.seg_layers)[h_idx] == 4   # TOP M4


def test_escalation_leaves_live_low_segment_in_place():
    """No false positives: with escalation on but NO keepout, the LOW-pinned
    stub has real signal supply, so it is not dead and is left on M2."""
    s, h_idx = _build(pin_low=True, keepout_m2=False)
    _nuts(s, escalate=True)
    assert s.detailed_result.num_unplaced == 0
    assert list(s.bundles[0].plan.seg_layers)[h_idx] == 2   # unchanged LOW M2


def test_default_off_is_identical():
    """The flag defaults off; a fresh session does not escalate even a dead
    LOW segment (bit-identical to pre-feature behavior)."""
    s, h_idx = _build(pin_low=True, keepout_m2=True)
    assert s._dead_span_escalate is False
    _nuts(s, escalate=False)
    assert s.detailed_result.num_unplaced == 8


def test_partial_supply_strands_all_bits_without_escalation():
    """PARTIAL supply is a full strand, not a partial one: DetailedNUTS
    admission is all-or-nothing (`n_sig < bus_seg_nbits` strands the whole
    segment), so 4 surviving tracks against an 8-bit bus lose all 8 bits —
    the #518/#527 mix2 residual shape (16 bits, supply > 0, every healer's
    zero-threshold test blind to it)."""
    s, h_idx = _build(pin_low=True, keepout_m2=True, partial=True)
    _nuts(s, escalate=False)
    assert s.detailed_result.num_unplaced == 8
    assert list(s.bundles[0].plan.seg_layers)[h_idx] == 2   # still LOW M2


def test_escalation_fires_on_partial_supply():
    """The escalation predicate is DNUTS's admission arithmetic — fewer
    keepout-clear tracks than the segment's member bits — not merely "zero
    tracks".  4 < 8 must escalate the stub to TOP M4 and place all 8 bits.
    (The historical zero threshold was the member-bits predicate specialized
    to a 1-bit bus; this pins the generalization.)"""
    s, h_idx = _build(pin_low=True, keepout_m2=True, partial=True)
    _nuts(s, escalate=True)
    assert s.detailed_result.num_unplaced == 0
    assert list(s.bundles[0].plan.seg_layers)[h_idx] == 4   # TOP M4


def _bound_m2_seg(s, lo_bound):
    """Clamp the placed M2 segment's cross-layer corner bound (the DNUTS
    track_lo_bound carried by make_bus_segments) and return its seg count."""
    n = 0
    for seg in s.nuts_result.segments:
        if seg.layer == 2:
            seg.track_lo_bound = lo_bound
            n += 1
    return n


def test_corner_bounds_shrink_the_admission_pool():
    """DNUTS filters the chosen pool to the segment's cross-layer corner
    bounds BEFORE the n_sig check (place_by_layer), so the escalation
    predicate must count inside the bounds too (Codex P2 on #534): with no
    keepout the M2 stub's midpoint pool is 20 tracks, but a lo-bound at 34
    leaves only 3 in [34, 40] against 8 bits — a guaranteed full strand the
    interval-wide count would miss.  The predicate must escalate it."""
    s, h_idx = _build(pin_low=True, keepout_m2=False)
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        s.do_command("run_nuts")
    assert _bound_m2_seg(s, 34.0) == 1
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        n = s._escalate_dead_low_segments()
    assert n == 1
    assert list(s.bundles[0].plan.seg_layers)[h_idx] == 4   # TOP M4


def test_corner_bounds_with_enough_tracks_do_not_escalate():
    """No false positives from the bounds path: a lo-bound at 20 leaves 10
    of the 20 tracks — enough for the 8-bit bus — so the bounded predicate
    must leave the live LOW segment in place."""
    s, h_idx = _build(pin_low=True, keepout_m2=False)
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        s.do_command("run_nuts")
    assert _bound_m2_seg(s, 20.0) == 1
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        n = s._escalate_dead_low_segments()
    assert n == 0
    assert list(s.bundles[0].plan.seg_layers)[h_idx] == 2   # unchanged LOW M2
