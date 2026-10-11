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

"""`fix_pin`: a block's pins are a SET OF LANDINGS (docs/internal/fixed_pin_design.md).

A fix narrows where a wire may END on a block — faces, one declared rect, a
window along the face — and never changes what the block BLOCKS.  The strips
are the block's landing geometry (`Floorplan.landing_rects`); each carries the
ONE edge a wire may end on, so a stub ending on a strip's inner edge or short
end is not a landing.
"""
import contextlib
import io
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
from subprocess_env import buda_env  # noqa: E402

import buda  # noqa: E402
import buda_cli  # noqa: E402

FLOW = _ROOT / "flow" / "pin_fix"
N, S, E, W = 1, 2, 4, 8

TECH = ["def_layer 4 M4 H TOP 0", "def_layer 5 M5 V TOP 0",
        "def_track_pattern 4 0 (SIGNAL 2 2)x60",
        "def_track_pattern 5 0 (SIGNAL 2 2)x60"]


def _session(*cmds):
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        for c in cmds:
            s.do_command(c)
    return s, out.getvalue()


def _say(s, cmd):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        s.do_command(cmd)
    return out.getvalue()


# ── strip geometry ────────────────────────────────────────────────────────

def _fp(rects=None):
    fp = buda.Floorplan()
    if rects is None:
        fp.add_block("M", 400, 400, 700, 700)
    else:
        fp.add_block_rects("M", rects)
    return fp


def test_a_face_becomes_one_thin_strip_tagged_with_that_face():
    fp = _fp()
    fp.set_pin_fix("M", E)
    assert fp.pin_fix_strips("M") == [(699, 400, 700, 700, E)]
    fp.set_pin_fix("M", W)
    assert fp.pin_fix_strips("M") == [(400, 400, 401, 700, W)]
    fp.set_pin_fix("M", N)
    assert fp.pin_fix_strips("M") == [(400, 699, 700, 700, N)]
    fp.set_pin_fix("M", S)
    assert fp.pin_fix_strips("M") == [(400, 400, 700, 401, S)]


def test_no_face_token_admits_every_face():
    fp = _fp()
    fp.set_pin_fix("M", 0)
    assert sorted(f for *_r, f in fp.pin_fix_strips("M")) == sorted([N, S, E, W])


def test_a_window_is_block_local_and_clipped_to_the_face():
    fp = _fp()
    fp.set_pin_fix("M", E, -1, True, 100, 160)     # block origin is (400,400)
    assert fp.pin_fix_strips("M") == [(699, 500, 700, 560, E)]
    fp.set_pin_fix("M", N, -1, True, 250, 900)     # past the face: clipped
    assert fp.pin_fix_strips("M") == [(650, 699, 700, 700, N)]
    fp.set_pin_fix("M", E, -1, True, 400, 500)     # wholly off the face
    assert fp.pin_fix_strips("M") == []


def test_a_rect_selector_picks_one_declared_rect():
    # an L: a wide bottom rect and a narrow top-left rect sitting on it
    fp = _fp([(0, 0, 300, 100), (0, 100, 100, 300)])
    fp.set_pin_fix("M", E, 0)
    assert fp.pin_fix_strips("M") == [(299, 0, 300, 100, E)]
    fp.set_pin_fix("M", E, 1)
    assert fp.pin_fix_strips("M") == [(99, 100, 100, 300, E)]


def test_only_the_exposed_stretch_of_a_partly_covered_edge_is_a_face():
    """Codex on #980: a midpoint probe cannot tell.  The bottom rect's north
    edge is covered where the top rect sits on it (x 0..100) and exposed from
    x=100 to 300 — one strip, not the whole edge."""
    fp = _fp([(0, 0, 300, 100), (0, 100, 100, 300)])
    fp.set_pin_fix("M", N, 0)
    assert fp.pin_fix_strips("M") == [(100, 99, 300, 100, N)]


def test_a_cover_in_the_middle_leaves_both_ends_exposed():
    # a block on top of the middle of a long rect: the exposed north edge is
    # two stretches, and the midpoint (which IS covered) is the one a probe sees
    fp = _fp([(0, 0, 300, 100), (100, 100, 200, 200)])
    fp.set_pin_fix("M", N, 0)
    assert fp.pin_fix_strips("M") == [(0, 99, 100, 100, N), (200, 99, 300, 100, N)]


def test_an_abutting_rect_hides_the_shared_edge():
    fp = _fp([(0, 0, 100, 100), (100, 0, 200, 100)])
    fp.set_pin_fix("M", E, 0)
    assert fp.pin_fix_strips("M") == []          # rect 0's east edge is shared


def test_a_fix_changes_what_the_block_blocks_not_at_all():
    fp = _fp()
    before = fp.obstruction_rects("M")
    fp.set_pin_fix("M", E, -1, True, 100, 160)
    assert fp.obstruction_rects("M") == before
    assert fp.get_block_rects("M") == []
    b = fp.get_block_bounds("M")
    assert (b.x1, b.y1, b.x2, b.y2) == (400, 400, 700, 700)


def test_a_clear_restores_the_unfixed_geometry():
    fp = _fp()
    raw = fp.landing_rects("M")
    fp.set_pin_fix("M", E)
    assert fp.landing_rects("M") != raw
    fp.clear_pin_fix("M")
    assert fp.landing_rects("M") == raw and not fp.has_pin_fix("M")


# ── landing recognition: only the tagged edge is a landing ─────────────────

def _taps(fp, p0, p1):
    t = buda.Topology()
    sg = buda.Segment()
    sg.start, sg.end, sg.layer_hint = buda.Point(*p0), buda.Point(*p1), 4
    t.segments = [sg]
    buda.annotate_topology(t, fp)
    return [(a is not None, b is not None) for a, b in t.seg_busterms.values()][0]


def test_only_the_tagged_edge_of_a_strip_is_a_landing():
    fp = _fp()
    fp.set_pin_fix("M", E)
    assert _taps(fp, (750, 500), (700, 500)) == (False, True)   # the east face
    assert _taps(fp, (650, 500), (699, 500)) == (False, False)  # the INNER edge
    assert _taps(fp, (699, 350), (699, 400)) == (False, False)  # a short END
    assert _taps(fp, (500, 350), (500, 400)) == (False, False)  # the south face
    fp.clear_pin_fix("M")
    assert _taps(fp, (500, 350), (500, 400)) == (False, True)   # unfixed: a landing


# ── the command ───────────────────────────────────────────────────────────

BASE = ["add_block MAC 400 400 700 700", "add_block a 0 450 150 650", *TECH]


def test_fix_pin_declares_reports_and_removes():
    s, out = _session(*BASE, "fix_pin MAC east at 100..160")
    assert "[PinFix] MAC: east at 100..160 -> 1 landing strip(s)" in out
    assert "east" in _say(s, "dump_pin_fixes") and "[699,500 .. 700,560]" in _say(s, "dump_pin_fixes")
    assert s.fp.has_pin_fix("MAC")
    assert "fix removed" in _say(s, "fix_pin MAC off")
    assert not s.fp.has_pin_fix("MAC")
    assert "no fixed pins" in _say(s, "dump_pin_fixes")


def test_h_and_v_are_sugar_for_two_faces():
    s, _ = _session(*BASE, "fix_pin MAC H")
    assert sorted(f for *_r, f in s.fp.pin_fix_strips("MAC")) == [E, W]
    _say(s, "fix_pin MAC V")
    assert sorted(f for *_r, f in s.fp.pin_fix_strips("MAC")) == [N, S]


@pytest.mark.parametrize("line,needle", [
    ("fix_pin MAC.d_in east", "per-pin selector"),
    ("fix_pin MAC sideways", "unknown token"),
    ("fix_pin MAC east at 200..100", "is empty"),
    ("fix_pin MAC east at 5", "lo..hi"),
    ("fix_pin MAC rect 3", "out of range"),
    ("fix_pin MAC east at 400..500", "admits no landing"),
    ("fix_pin MAC east layer M5", "not supported yet"),
])
def test_a_bad_declaration_is_refused_loudly_and_changes_nothing(line, needle):
    s, _ = _session(*BASE)
    out = _say(s, line)
    assert "Error:" in out and needle in out, out
    assert not s.fp.has_pin_fix("MAC")


def test_a_refused_redeclaration_keeps_the_previous_fix():
    s, _ = _session(*BASE, "fix_pin MAC east")
    assert "Error:" in _say(s, "fix_pin MAC east at 400..500")
    assert s.fp.pin_fix_strips("MAC") == [(699, 400, 700, 700, E)]


def test_a_fix_declared_before_its_block_is_checked_when_the_block_arrives():
    """Codex on #980: the (block not declared yet) path skipped every check and
    nothing revisited it, so a bad fix stranded the bus at generation."""
    s, out = _session("fix_pin LATE east at 400..500")
    assert "block not declared yet" in out
    out = _say(s, "add_block LATE 0 0 100 100")
    assert "Error:" in out and "dropped" in out, out
    assert not s.fp.has_pin_fix("LATE") and "LATE" not in s._pin_fixes
    s, _ = _session("fix_pin OK east at 10..50")
    assert "Error:" not in _say(s, "add_block OK 0 0 100 100")
    assert s.fp.pin_fix_strips("OK") == [(99, 10, 100, 50, E)]
    s, _ = _session("fix_pin R rect 4")
    assert "out of range" in _say(s, "add_block R 0 0 100 100")


def test_the_command_is_registered_for_every_front_end():
    # the Tcl front end builds buda::fix_pin / buda::dump_pin_fixes from the registry
    assert {"fix_pin", "dump_pin_fixes"} <= set(buda_cli.KNOWN_COMMANDS)


# ── routing: where the bus actually lands ─────────────────────────────────

ROUTE = ["add_bus wd[4] a.tx MAC.rx", "run_bundler STRICT", "generate_topologies"]


def _landings(s):
    """(x, y) of every tap the selected-pool candidates make on MAC."""
    pts = []
    for t in s.bundles[0].input.candidates:
        for k, (a, b) in t.seg_busterms.items():
            sg = t.segments[k]
            if a is not None and a.block_name == "MAC":
                pts.append((sg.start.x, sg.start.y))
            if b is not None and b.block_name == "MAC":
                pts.append((sg.end.x, sg.end.y))
    return pts


def test_every_candidate_lands_on_the_fixed_face():
    s, _ = _session(*BASE, "fix_pin MAC east", *ROUTE)
    pts = _landings(s)
    assert pts and all(x == 700 for x, _y in pts), pts


def test_every_candidate_lands_inside_the_window():
    s, _ = _session(*BASE, "fix_pin MAC west at 100..160", *ROUTE)
    pts = _landings(s)
    assert pts and all(x == 400 and 500 <= y <= 560 for x, y in pts), pts


def test_the_shapes_that_cannot_land_are_dropped_with_a_note(capfd):
    s, _ = _session(*BASE, "fix_pin MAC east", *ROUTE)
    free, _ = _session(*BASE, *ROUTE)
    assert len(s.bundles[0].input.candidates) < len(free.bundles[0].input.candidates)


def test_the_unfixed_pool_is_untouched():
    a, _ = _session(*BASE, *ROUTE)
    b, _ = _session(*BASE, "fix_pin MAC east", "fix_pin MAC off", *ROUTE)
    ta = [(t.type, t.estimated_wirelength) for t in a.bundles[0].input.candidates]
    tb = [(t.type, t.estimated_wirelength) for t in b.bundles[0].input.candidates]
    assert ta == tb


def test_a_bare_east_fix_admits_no_pass_through():
    """A pass-through is two landings (in on one face, out on the opposite);
    a single admitted face is not one, so a trunk crossing the block does not
    count as covering it."""
    fp = _fp()
    fp.set_pin_fix("M", E)
    far = fp.passthrough_rects("M")
    assert len(far) == 1 and far[0][0] < -10 ** 8       # the sentinel: covers nothing
    fp.set_pin_fix("M", E | W)
    assert fp.passthrough_rects("M") == [(400, 400, 700, 700)]


# ── flows ─────────────────────────────────────────────────────────────────

def _run(flow):
    p = subprocess.run([sys.executable, str(_ROOT / "src" / "buda_cli.py"),
                        "--no-viz", str(FLOW / flow)],
                       capture_output=True, text=True, env=buda_env(_ROOT),
                       timeout=300)
    return p.returncode, p.stdout + p.stderr


@pytest.mark.parametrize("flow", ["faces.buda", "window.buda", "hier.buda"])
def test_fix_pin_flows_route_clean(flow):
    rc, out = _run(flow)
    assert rc == 0, out[-800:]
    assert "Error:" not in out, out[-800:]
    assert out.count("Success: no violations found") >= 2, out[-1500:]
    assert "fix_pin" in out


def test_the_hier_flow_lands_inside_its_windows():
    """hier.buda: buf's east pins sit between y=10 and y=30 of an 80-high block
    at (150,110); rcv's west pins between 50 and 70 of the block at (890,110)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command(f"source {FLOW / 'hier.buda'}")
    segs = s.detailed_result.net_segments
    assert segs
    at_buf = [n for n in segs if n.layer and n.span_lo <= 230 <= n.span_hi
              and 110 <= n.track_position <= 190 and n.bundle_id]
    assert at_buf
    for n in segs:
        # any horizontal wire reaching buf's east edge (x=230) or rcv's west edge
        # (x=890) at a y inside the block must sit inside the declared window
        if n.span_hi == 230 or n.span_lo == 230:
            if 110 <= n.track_position <= 190:
                assert 120 <= n.track_position <= 140, n.track_position
        if n.span_lo == 890 or n.span_hi == 890:
            if 110 <= n.track_position <= 190:
                assert 160 <= n.track_position <= 180, n.track_position
