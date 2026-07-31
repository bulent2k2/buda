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

"""The supply-doomed-seat census (#536 option 1, report-only).

`check_design` (nuts/dnuts stages) names every placed segment whose assigned
layer's real signal-track supply in the PLACED window falls short of its
member bits by the exact DNUTS admission arithmetic — a static
width-infeasibility where every bit is guaranteed to strand, knowable before
DNUTS runs.  Unlike the dead-span escalation family (LOW layers, heal
attached), the census covers TOP seats too — the mix2 b61 class, where a
measured healer move commits a seat with 12 tracks for 16 bits and the loss
surfaces only as unexplained DNUTS opens — and attaches no heal: it exists
to separate the STATIC dooms from the DYNAMIC reservation conflicts in every
flow log.  Locked bottom-up copies are excluded (their bits arrive via the
template-copy path, which never runs per-instance admission).
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


def _build(pin_layer=None, keepout=None):
    """The dead-span L-shape scaffold: optionally pin the H stub to
    `pin_layer` and lay a keepout, then run abstract NUTS."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        for c in _SETUP:
            s.do_command(c)
        if keepout:
            s.do_command(keepout)
    if pin_layer is not None:
        w = s.bundles[0]
        sel = w.plan.selected_topology_index
        sl = list(w.plan.seg_layers)
        for si, seg in enumerate(w.input.candidates[sel].segments):
            if seg.start.y == seg.end.y:          # horizontal stub
                sl[si] = pin_layer
        w.plan.seg_layers = sl
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        s.do_command("run_nuts")
    return s


def _check(s, stage="nuts"):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), buda.ostream_redirect():
        s.do_command(f"check_design {stage}")
    return buf.getvalue()


def test_low_doomed_seat_reported():
    """An H stub pinned to a fully-keepout-dead LOW layer is a doomed seat:
    the census names it with the honest pool (0) vs member bits (8)."""
    s = _build(pin_layer=2, keepout="add_keepout 0 0 200 500 2")
    out = _check(s)
    assert "supply-doomed seat: bundle 1" in out
    assert "on M2 (LOW) — 0 signal track(s)" in out
    assert "< 8 member bit(s)" in out
    assert "1 supply-doomed seat(s) (0 TOP, 1 LOW; 8 guaranteed-stranded" \
        in out


def test_top_doomed_seat_reported_and_matches_dnuts():
    """The census's reason to exist: a TOP seat with partial supply (the
    keepout leaves 4 of the interval's tracks alive, fewer than the 8-bit
    bus needs) — no escalation family covers TOP, so the doom surfaces only
    as DNUTS opens.  The advisory must name it, and DNUTS must confirm the
    all-or-nothing strand."""
    s = _build(pin_layer=4, keepout="add_keepout 0 0 200 32 4")
    out = _check(s)
    assert "on M4 (TOP) — 4 signal track(s)" in out
    assert "< 8 member bit(s)" in out
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        s.do_command("run_detailed_nuts")
    assert s.detailed_result.num_unplaced == 8    # the promised full strand
    assert "on M4 (TOP)" in _check(s, "dnuts")    # dnuts stage reports too


def test_no_advisory_when_supply_suffices():
    """Clean flow — no keepout, every seat has supply: the census is silent
    (byte-identical check_design output)."""
    out = _check(_build(pin_layer=2))
    assert "doomed" not in out


def test_locked_copies_excluded():
    """A hier.locked wrapper's seats are never reported: its bits arrive via
    the template-copy path (per-instance admission never runs), so the
    'every bit strands' claim would be false — the aligned_sql copies flag
    pool-0 seats yet place every bit."""
    s = _build(pin_layer=2, keepout="add_keepout 0 0 200 500 2")
    s.bundles[0].hier.locked = True
    assert "doomed" not in _check(s)


def test_advisory_uses_configured_layer_name():
    """A layer's id and name are independent (`def_layer 2 Hmetal H 50`);
    the advisory must print the configured name, not a synthesized M{id}
    (Codex P2 on #548)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    setup = ["def_layer 2 Hmetal H 50" if c.startswith("def_layer 2")
             else c for c in _SETUP]
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        for c in setup:
            s.do_command(c)
        s.do_command("add_keepout 0 0 200 500 2")
    w = s.bundles[0]
    sel = w.plan.selected_topology_index
    sl = list(w.plan.seg_layers)
    for si, seg in enumerate(w.input.candidates[sel].segments):
        if seg.start.y == seg.end.y:
            sl[si] = 2
    w.plan.seg_layers = sl
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        s.do_command("run_nuts")
    out = _check(s)
    assert "on Hmetal (LOW)" in out
    assert "on M2" not in out


def test_advisory_does_not_disturb_qor_parse():
    """tools/qor_corpus.py parses check_design's summary via the regex
    `across (\\d+) bundle\\(s\\)` (first match wins) and the literal
    'no violations found'.  The advisory lines must never satisfy that
    regex ahead of the real summary — pin the phrasing."""
    import re
    s = _build(pin_layer=2, keepout="add_keepout 0 0 200 500 2")
    out = _check(s)
    for line in out.splitlines():
        if "doomed" in line:
            assert re.search(r"across (\d+) bundle\(s\)", line) is None
