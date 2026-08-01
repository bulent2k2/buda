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

"""The measured-accept pairwise-overlap alignment heal (path 1).

Two same-net stubs straddling a trunk align — share one track window, a
tidier route — only when the first-placed one lands where the second can
reach; the greedy ordered-anchor placer often splits them.  The
UNCONDITIONAL fix (restrict each stub's pool to the pair overlap) was corpus
net-negative (concentration starves tracks).  The heal keeps the win without
the loss: re-solve DNUTS with pair-align on and ACCEPT only when
unplaced/overlaps don't rise and detailed WL strictly drops.

Fixture: the seat repro — a driver fanning out to two upper and three lower
blocks with a half-phase x-offset, pinned to the mid-channel TRUNK_H.  Its
right column (u3/l3) splits under the greedy placer; the heal aligns it.
"""
import contextlib
import io
from collections import defaultdict

import buda
import buda_cli


_CMDS = [
    "def_layer 4 M4 H TOP 44.44",
    "def_layer 5 M5 V TOP 50.00",
    "def_track_pattern 4 -200  VDD 2 1  _ 2 1 _ 2 1 _ 2 1 _ 2 1 _ 2 1"
    "  VSS 2 1  _ 2 1 _ 2 1 _ 2 1 _ 2 1 _ 2 1",
    "def_track_pattern 5 -200  VDD 3 1  _ 2 1 _ 2 1 _ 2 1 _ 2 1"
    "        VSS 3 1  _ 2 1 _ 2 1 _ 2 1 _ 2 1",
    "corner_margin dx 1 dy 2",
    "add_block u1 100 500 200 700",
    "add_block l1 150 100 250 300",
    "add_block u2 300 500 500 700",
    "add_block l2 350 100 450 300",
    "add_block u3 600 500 700 700",
    "add_block l3 550 100 650 300",
    "add_bus bus1[8] u1 u2,u3,l1,l2,l3",
    "run_bundler",
    "generate_topologies",
    "select_topology 1 15",
    "run_planner",
    "run_nuts",
    "run_detailed_nuts",
]


def _session(heal=False, lock=False):
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), buda.ostream_redirect():
        for c in _CMDS:
            s.do_command(c)
        if lock:
            for w in s.bundles:
                w.hier.locked = True
        if heal:
            s.do_command("set_pair_align_heal on")
            s.do_command("run_detailed_nuts")   # re-solve so the heal runs
    return s, buf.getvalue()


def _v_tracks(s):
    by = defaultdict(list)
    for ns in s.detailed_result.net_segments:
        ts = next((t for t in s.nuts_result.segments
                   if t.bundle_id == ns.bundle_id and t.seg_idx == ns.seg_idx),
                  None)
        if ts is not None and not ts.horiz:
            by[ns.seg_idx].append(round(ns.track_position, 1))
    return {k: sorted(v) for k, v in by.items()}


def _dwl(s):
    return sum(abs(ns.span_hi - ns.span_lo)
               for ns in s.detailed_result.net_segments)


def test_baseline_right_column_splits():
    """Without the heal, the greedy placer aligns the left/middle stub pairs
    but splits the right pair (u3=seg3, l3=seg6 land on different tracks)."""
    s, _ = _session(heal=False)
    t = _v_tracks(s)
    assert t[1] == t[4]        # u1/l1 aligned
    assert t[2] == t[5]        # u2/l2 aligned
    assert t[3] != t[6]        # u3/l3 SPLIT — the artifact


def test_heal_aligns_right_column_and_drops_wl():
    """With the heal on, the re-solve aligns the right pair and is accepted
    (detailed WL strictly lower, no bits stranded)."""
    base, _ = _session(heal=False)
    healed, out = _session(heal=True)
    assert "PAIR-ALIGN" in out
    t = _v_tracks(healed)
    assert t[3] == t[6]                                   # right pair aligned
    assert healed.detailed_result.num_unplaced == 0
    assert _dwl(healed) < _dwl(base)                      # strictly shorter


def test_heal_off_is_noop():
    """Default (no command): the heal never runs — byte-identical placement,
    the right pair stays split."""
    s, out = _session(heal=False)
    assert "PAIR-ALIGN" not in out
    t = _v_tracks(s)
    assert t[3] != t[6]


def test_heal_scoped_out_of_locked_sessions():
    """A bottom-up (any hier.locked) session must not run the heal — its
    copies stay uniform and its downstream healers re-roll from a changed
    start, the same boundary as the cull/reseat heals."""
    s, out = _session(heal=True, lock=True)
    assert "PAIR-ALIGN" not in out
    t = _v_tracks(s)
    assert t[3] != t[6]        # untouched — still split


def test_heal_returns_zero_when_no_win():
    """`_final_pair_align_heal` is self-guarding: once a design is already
    aligned (the heal ran and was accepted), a REPEAT finds no further WL
    win, accepts nothing, and restores the same baseline result object."""
    s, out = _session(heal=True)     # first heal accepted (aligned the right pair)
    assert "PAIR-ALIGN" in out
    before = s.detailed_result       # already-aligned, WL-minimal baseline
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        n = s._final_pair_align_heal()
    assert n == 0
    assert s.detailed_result is before
