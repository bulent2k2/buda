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
    ("fix_pin MAC.d_in east at 400..500", "admits no landing"),
    ("fix_pin MAC sideways", "unknown token"),
    ("fix_pin MAC east at 200..100", "is empty"),
    ("fix_pin MAC east at 5", "lo..hi"),
    ("fix_pin MAC rect 3", "out of range"),
    ("fix_pin MAC east at 400..500", "admits no landing"),
    ("fix_pin MAC east order sideways", "takes index"),
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


def test_every_strip_of_a_split_face_can_carry_a_pass_through():
    """Codex on #980: a face another rect splits has several strips and the
    pass-through derivation kept only the last.  Rect (0,0)-(100,300) with an
    abutting rect on its east side over y 100..200: east strips at y 0..100
    and 200..300, one full west strip — a crossing at y=50 is admitted."""
    fp = _fp([(0, 0, 100, 300), (100, 100, 200, 200)])
    fp.set_pin_fix("M", E | W, 0)
    assert sorted(fp.passthrough_rects("M")) == [(0, 0, 100, 100), (0, 200, 100, 300)]


def test_a_fix_declared_before_hier_blocks_is_checked_when_they_are_projected(tmp_path):
    """Codex on #980: `add_block` was the only validation point.  The hier
    projection arrives through `_fp_add_comp`, for the session floorplan and
    every derived frame."""
    txt = (FLOW / "hier.buda").read_text()
    txt = txt.replace("source ../tracks/tracks.buda",
                      f"source {_ROOT / 'flow' / 'tracks' / 'tracks.buda'}")
    txt = txt.replace("fix_pin src_i/buf_i east at 10..30", "fix_pin src_i/buf_i east at 500..600")
    f = tmp_path / "bad_hier.buda"
    f.write_text(txt)
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        s.do_command(f"source {f}")
    assert "fix_pin src_i/buf_i" in out.getvalue() and "dropped" in out.getvalue(), out.getvalue()[-600:]
    assert "src_i/buf_i" not in s._pin_fixes


def test_a_corner_margin_does_not_shrink_a_declared_window():
    """Codex on #980: derive_slide_ranges re-applied the block's margin to the
    already-physical window, so `corner_margin 5` + `at 100..160` slid inside
    105..155.  The declared window is the slide window."""
    s, _ = _session(*BASE, "corner_margin dx 5 dy 5", "fix_pin MAC west at 100..160",
                    *ROUTE)
    ct = buda.ConnTopology()
    sels = []
    for t in s.bundles[0].input.candidates:
        ct.build(t, s.fp)
        for cs in ct.segs():
            for conn in cs.conns:
                if conn.block_name == "MAC":
                    sels.append((cs.perp_lo, cs.perp_hi))
    assert sels and any(lo == 500 and hi == 560 for lo, hi in sels), sels


def test_the_pass_through_reporters_read_the_two_face_geometry():
    """Codex on #980: with an east-only fix a horizontal segment spanning the
    macro crosses the east strip but is NOT a pass-through (the west face is not
    admitted); the reporters must agree with generation and the audit."""
    import buda_session.util as u
    s, _ = _session(*BASE, "fix_pin MAC east")
    cs = type("CS", (), dict(horiz=True, perp_pos=500, along_lo=0, along_hi=800))()
    ub = s.fp.get_block_bounds("MAC")
    assert not u.seg_spans_block(cs, "MAC", ub, s.fp)
    _say(s, "fix_pin MAC H")
    assert u.seg_spans_block(cs, "MAC", ub, s.fp)


def test_a_dotted_block_name_is_a_block_name():
    """Codex on #980: `u.core` / `IO.PAD` were split as a per-pin selector."""
    s, out = _session("add_block u.core 0 0 100 100", "fix_pin u.core east",
                      "fix_pin u.core.* west")
    assert "Error:" not in out
    assert s.fp.has_pin_fix("u.core") and "u.core" in s._pin_fixes
    # the LAST dot splits a pin off a block; the whole name stays a block
    assert "pin selector" in _say(s, "fix_pin u.core.d_in east")
    assert ("name", "d_in") in s._pin_fix_sel["u.core"]
    assert "u.core.d_in" not in s._pin_fixes


def test_a_fixed_multi_rect_over_block_is_audited_as_thru():
    """Codex on #980: the strips are joined by the block's own metal (THRU), so
    the audit must not read the declared `over` and demand metal on every strip."""
    fp = buda.Floorplan()
    fp.add_block_rects("T", [(300, 0, 400, 100), (0, 0, 100, 100)], buda.TegMode.OVER)
    assert fp.get_block_teg_mode("T") == buda.TegMode.OVER
    fp.set_pin_fix("T", E | W)
    assert fp.get_block_teg_mode("T") == buda.TegMode.THRU
    fp.clear_pin_fix("T")
    assert fp.get_block_teg_mode("T") == buda.TegMode.OVER


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


# ── per-pin selectors (PR 1c) ───────────────────────────────────────────────

PINS_FLOW = FLOW / "pins.buda"


def _run_pins(*extra, nets=None):
    lines = [l.strip() for l in PINS_FLOW.read_text().splitlines()
             if l.strip() and not l.startswith("#")
             and not l.startswith(("visualize", "dump_", "report_", "check_design"))]
    s, out = _session(*lines, *extra)
    return s, out


def _face_of(topo, block_rect):
    """Faces of `block_rect` (x1,y1,x2,y2) that a segment END lies on."""
    x1, y1, x2, y2 = block_rect
    faces = set()
    for g in topo.segments:
        for p in (g.start, g.end):
            if x1 <= p.x <= x2 and p.y == y2: faces.add("N")
            if x1 <= p.x <= x2 and p.y == y1: faces.add("S")
            if y1 <= p.y <= y2 and p.x == x2: faces.add("E")
            if y1 <= p.y <= y2 and p.x == x1: faces.add("W")
    return faces


def test_each_pin_of_one_macro_lands_on_its_own_face():
    s, out = _run_pins()
    assert "[PinFix] split bundle" in out and "3 fix-uniform part" in out
    by_net = {w.input.original_bundle.get_net_names()[0]: w for w in s.bundles}
    mac = (400, 400, 600, 600)
    lo = _face_of(by_net["lo_0"].input.candidates[0], mac)
    hi = _face_of(by_net["hi_0"].input.candidates[0], mac)
    mid = _face_of(by_net["mid_0"].input.candidates[0], mac)
    assert lo == {"W"}, lo
    assert "N" in hi, hi
    assert "S" in mid, mid
    # the bundles carry their OWN effective map, not the floorplan's
    assert by_net["hi_0"].input.candidates[0].block_fixes["MAC"][0] == N
    assert by_net["lo_0"].input.candidates[0].block_fixes["MAC"][0] == W
    assert by_net["mid_0"].input.candidates[0].block_fixes["MAC"][0] == S


def test_the_per_pin_flow_audits_clean_at_both_stages():
    r = subprocess.run([sys.executable, str(_ROOT / "src" / "buda_cli.py"),
                        "--no-viz", str(PINS_FLOW)],
                       capture_output=True, text=True, env=buda_env(_ROOT),
                       cwd=str(_ROOT))
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("Success: no violations found.") == 2, r.stdout


def test_selector_precedence_exact_pin_then_bus_then_prefix_then_block():
    s, _ = _session("add_block M 0 0 100 100",
                    "add_block A 200 0 300 100",
                    "add_net n0 A.x M.cfg_a",
                    "add_net n1 A.x M.cfg_b",
                    "add_net n2 A.x M.d[0]",
                    "add_net n3 A.x M.other",
                    "fix_pin M east",             # block-level
                    "fix_pin M.cfg* north",        # prefix
                    "fix_pin M.d south",           # bus name (matches d[0])
                    "fix_pin M.cfg_b west")        # exact pin
    from buda_cmds.pin_fix_cmds import resolve_fix
    f = lambda pin: resolve_fix(s, "M", pin)["faces"]
    assert f("cfg_b") == W          # exact beats the prefix
    assert f("cfg_a") == N          # prefix beats the block
    assert f("d[0]") == S           # bus name
    assert f("other") == E          # block-level


def test_a_longer_prefix_wins():
    s, _ = _session("add_block M 0 0 100 100", "fix_pin M.c* north",
                    "fix_pin M.cfg* south")
    from buda_cmds.pin_fix_cmds import resolve_fix
    assert resolve_fix(s, "M", "cfg_a")["faces"] == S
    assert resolve_fix(s, "M", "clk")["faces"] == N


def test_a_selector_can_be_removed_and_a_bad_one_is_refused():
    s, _ = _session("add_block M 0 0 100 100", "fix_pin M.tx east")
    assert "no such pin fix" in _say(s, "fix_pin M.rx off")
    assert "pin fix removed" in _say(s, "fix_pin M.tx off")
    assert not s._pin_fix_sel
    out = _say(s, "fix_pin M.tx east at 400..500")
    assert "Error:" in out and "admits no landing" in out and not s._pin_fix_sel


def test_a_selector_needs_its_block_declared_first():
    """Before the block exists `M.tx` can only be read as a dotted block name."""
    s, _ = _session("fix_pin M.tx east")
    assert "M.tx" in s._pin_fixes and not s._pin_fix_sel


def test_no_selector_leaves_generation_untouched():
    s, _ = _session("add_block S 0 0 100 100", "add_block M 400 0 500 100", *TECH,
                    "fix_pin M west", "add_bus b[2] S.o M.i", "run_bundler STRICT",
                    "generate_topologies")
    assert not s.bundles[0].input.candidates[0].has_block_fixes
    assert s.fp.has_pin_fix("M")


def test_block_fix_scope_swaps_the_floorplans_landings():
    s, _ = _run_pins()
    hi = next(w for w in s.bundles
              if w.input.original_bundle.get_net_names()[0] == "hi_0")
    assert not s.fp.has_pin_fix("MAC")          # nothing block-level declared
    with buda.BlockFixScope(hi.input.candidates[0]):
        assert s.fp.has_pin_fix("MAC")
        assert {f for *_r, f in s.fp.pin_fix_strips("MAC")} == {N}
    assert not s.fp.has_pin_fix("MAC")          # restored on exit


def test_a_selector_is_no_longer_reported_as_unreplayed_on_a_hier_frame():
    """PR 3 replays selectors per bundle (a map on the generator), so the frame
    replay no longer says they are ignored."""
    s, _ = _session("add_block M 0 0 100 100", "fix_pin M.tx east")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        s._apply_fp_session_settings(buda.Floorplan())
    assert "not replayed" not in out.getvalue()


def test_auto_bit_cap_is_sized_by_the_selected_landing_window():
    """Codex on #983: the auto cap read the block's whole bounds, not the pin's window."""
    pre = ["add_block S 0 0 100 100", "add_block M 400 0 600 200", *TECH]
    post = ["set_max_bundle_bits auto", "add_bus b[4] S.o M.tx", "run_bundler STRICT"]
    s0, _ = _session(*pre, *post)
    s1, _ = _session(*pre, "fix_pin M.tx west at 80..90", *post)
    assert len(s0.bundles) == 1
    assert len(s1.bundles) > 1


def test_annotate_topology_reads_the_topologys_own_fixes():
    """Codex on #983: annotate_topology used the floorplan's block-level fix."""
    s, _ = _run_pins()
    w = next(w for w in s.bundles
             if w.input.original_bundle.get_net_names()[0] == "hi_0")
    topo = w.input.candidates[0]
    s.fp.set_pin_fix("MAC", E, -1, False, 0, 0)       # a conflicting block-level fix
    topo2 = buda.offset_topology(topo, 0, 0)
    topo2.clear_analysis_cache()
    buda.annotate_topology(topo2, s.fp)
    assert any(ep[1] is not None or ep[0] is not None
               for ep in topo2.seg_busterms.values())
    ct = buda.ConnTopology()
    ct.build(topo2, s.fp)
    res = buda.check_topo(ct, topo2, s.fp, 0)
    assert not [v for v in res.violations if "BUSTERM" in str(v.kind)]


def test_a_fan_in_is_split_only_where_fixes_conflict_on_one_block():
    """Codex on #983: nets from different fixed blocks into one sink stay bundled."""
    pre = ["add_block A 0 0 100 100", "add_block B 0 300 100 400",
           "add_block Z 600 100 800 300", *TECH,
           "fix_pin A.out south", "fix_pin B.out north"]
    s, out = _session(*pre, "add_bus x[2] A.out Z.in", "add_bus y[2] B.out Z.in",
                      "run_bundler CONVERGENT")
    assert "[PinFix] split" not in out and len(s.bundles) == 1
    s2, out2 = _session(*pre, "fix_pin Z.in1 west", "fix_pin Z.in2 east",
                        "add_bus p[2] A.out Z.in1", "add_bus q[2] A.out Z.in2",
                        "run_bundler CONVERGENT")
    assert "[PinFix] split" in out2 and len(s2.bundles) == 2


def test_auto_cap_of_a_fixed_pin_is_its_face_length():
    """Codex on #983: a one-unit-deep strip must not cap the bus at one bit."""
    pre = ["add_block S 0 0 100 100", "add_block M 400 0 600 200", *TECH,
           "fix_pin M.tx west at 80..100", "set_max_bundle_bits auto"]
    s, _ = _session(*pre, "add_bus b[4] S.o M.tx", "run_bundler STRICT")
    assert len(s.bundles) == 1          # 20 units / pitch 4 = 5 bits >= 4


def test_one_net_on_two_differently_fixed_pins_of_a_block_is_rejected():
    """Codex P1 on #983: the last pin's fix silently replaced the first's."""
    s, _ = _session("add_block A 0 0 100 100", "add_block B 400 0 600 200", *TECH,
                    "fix_pin B.x west", "fix_pin B.y north",
                    "add_net n A.o B.x,B.y")
    with pytest.raises(SystemExit):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            s.do_command("run_bundler STRICT")
    s2, _ = _session("add_block A 0 0 100 100", "add_block B 400 0 600 200", *TECH,
                     "fix_pin B.x west", "fix_pin B.y west",
                     "add_net n A.o B.x,B.y", "run_bundler STRICT")
    assert len(s2.bundles) == 1


def test_public_topology_helpers_read_the_topologys_own_fixes():
    """Codex on #983: add_mst_teg_attachments built busterms from the floorplan's fixes."""
    s, _ = _run_pins()
    w = next(w for w in s.bundles
             if w.input.original_bundle.get_net_names()[0] == "hi_0")
    topo = buda.offset_topology(w.input.candidates[0], 0, 0)
    s.fp.set_pin_fix("MAC", E, -1, False, 0, 0)       # conflicting block-level fix
    before = [(g.start.x, g.start.y, g.end.x, g.end.y) for g in topo.segments]
    buda.add_mst_teg_attachments(topo, s.fp, 4, 5)
    after = [(g.start.x, g.start.y, g.end.x, g.end.y) for g in topo.segments]
    assert after == before        # MAC is THRU under its own map: nothing to attach


def test_conn_dump_of_a_bundle_with_no_candidates_does_not_raise():
    """Codex on #983: the scope wrapper indexed candidates before the bounds check."""
    s, _ = _session("add_block A 0 0 100 100", "add_block B 400 0 500 100", *TECH,
                    "add_bus b[2] A.o B.i", "run_bundler STRICT")
    assert not list(s.bundles[0].input.candidates)      # nothing generated yet
    out = _say(s, "dump_topologies --conn")
    assert "Traceback" not in out and "IndexError" not in out


# ── persistence ──────────────────────────────────────────────────────────────

def _bdb_session(tmp_path, *cmds):
    path = str(tmp_path / "pf.bdb")
    return _session(f"open_bdb {path}", *cmds), path


def test_fixes_and_selectors_survive_reopening_the_bdb(tmp_path):
    (s, _), path = _bdb_session(tmp_path, "add_block M 0 0 100 100",
                                "fix_pin M west", "fix_pin M.tx north at 10..20",
                                "fix_pin M.d* south")
    s.bdb.save_bdb(path) if hasattr(s.bdb, "save_bdb") else None
    s2, out = _session(f"open_bdb {path}")
    assert "restored 3 persisted fix" in out, out
    assert s2._pin_fixes["M"]["faces"] == W
    assert s2._pin_fix_sel["M"][("name", "tx")]["faces"] == N
    assert s2._pin_fix_sel["M"][("name", "tx")]["has_window"]
    assert s2._pin_fix_sel["M"][("prefix", "d")]["faces"] == S
    assert s2.fp.has_pin_fix("M")


def test_a_typed_fix_outranks_a_stored_one_and_off_is_stored(tmp_path):
    (s, _), path = _bdb_session(tmp_path, "add_block M 0 0 100 100", "fix_pin M west")
    s2, _ = _session("fix_pin M east", f"open_bdb {path}")
    assert s2._pin_fixes["M"]["faces"] == E
    s3, _ = _session(f"open_bdb {path}", "fix_pin M off")
    s4, out4 = _session(f"open_bdb {path}")
    assert "M" not in s4._pin_fixes


def test_restored_selectors_restamp_loaded_candidates(tmp_path):
    (s, _), path = _bdb_session(
        tmp_path, "add_block S 0 400 100 600", "add_block M 400 400 600 600", *TECH,
        "fix_pin M.hi_in north", "add_bus hi[4] S.o M.hi_in", "bdb_net_mode on",
        "run_bundler STRICT", "generate_topologies", "run_planner")
    s2, _ = _session("add_block S 0 400 100 600", "add_block M 400 400 600 600", *TECH,
                     f"open_bdb {path}", "add_bus hi[4] S.o M.hi_in",
                     "load_pipeline")
    assert s2.bundles
    t = s2.bundles[0].input.candidates[0]
    assert t.has_block_fixes and t.block_fixes["M"][0] == N


def test_persist_flows_build_then_resume_from_the_checkpoint(tmp_path):
    import shutil
    for n in ("persist_build.buda", "persist_resume.buda"):
        shutil.copy(FLOW / n, tmp_path / n)
    outs = []
    for n in ("persist_build.buda", "persist_resume.buda"):
        r = subprocess.run([sys.executable, str(_ROOT / "src" / "buda_cli.py"),
                            "--no-viz", str(tmp_path / n)],
                           capture_output=True, text=True, env=buda_env(_ROOT),
                           cwd=str(_ROOT))
        assert r.returncode == 0, r.stdout + r.stderr
        outs.append(r.stdout)
    assert "restored 3 persisted fix" in outs[1], outs[1]
    assert "Error:" not in outs[1]
    assert outs[1].count("Success: no violations found.") == 3, outs[1]


def test_a_replacement_typed_after_a_restore_survives_opening_another_bdb(tmp_path):
    """Codex P1 on #989: a typed replacement was still tracked as 'restored'."""
    (s, _), pa = _bdb_session(tmp_path, "add_block M 0 0 100 100",
                              "fix_pin M west", "fix_pin M.tx north")
    pb = str(tmp_path / "other.bdb")
    s2, _ = _session("add_block M 0 0 100 100", f"open_bdb {pa}",
                     "fix_pin M east", "fix_pin M.tx south", f"open_bdb {pb}")
    assert s2._pin_fixes["M"]["faces"] == E
    assert s2._pin_fix_sel["M"][("name", "tx")]["faces"] == S
    # an UNTOUCHED restored fix still drops when another BDB is opened
    pc = str(tmp_path / "fresh.bdb")       # pb now holds s2's typed fixes
    s3, _ = _session("add_block M 0 0 100 100", f"open_bdb {pa}", f"open_bdb {pc}")
    assert "M" not in s3._pin_fixes and not s3._pin_fix_sel


def test_a_restored_bundle_that_current_fixes_repartition_is_voided(tmp_path):
    pre = ["add_block S 0 400 100 600", "add_block M 400 400 600 600", *TECH]
    (s, _), path = _bdb_session(
        tmp_path, *pre, "fix_pin M.a west", "fix_pin M.b west",
        "add_bus x[2] S.o M.a", "add_bus y[2] S.o M.b", "bdb_net_mode on",
        "run_bundler STRICT", "generate_topologies", "run_planner")
    assert len(s.bundles) == 1                      # same fix -> one bundle
    s2, out = _session(*pre, "fix_pin M.b north", f"open_bdb {path}",
                       "add_bus x[2] S.o M.a", "add_bus y[2] S.o M.b",
                       "load_pipeline")
    assert "voided" in out and len(s2.bundles) == 0


def test_a_typed_off_survives_opening_another_bdb(tmp_path):
    """Codex P1 on #989: a typed removal left no tombstone, so the next BDB restored it."""
    (s, _), pa = _bdb_session(tmp_path, "add_block M 0 0 100 100",
                              "fix_pin M west", "fix_pin M.tx north")
    pb = str(tmp_path / "b.bdb")
    (sb, _), _ = _session("open_bdb " + pb, "add_block M 0 0 100 100",
                          "fix_pin M east", "fix_pin M.tx south"), None
    s2, _ = _session("add_block M 0 0 100 100", f"open_bdb {pa}",
                     "fix_pin M off", "fix_pin M.tx off", f"open_bdb {pb}")
    assert "M" not in s2._pin_fixes and not s2._pin_fix_sel.get("M")


def test_a_fix_changed_before_opening_voids_a_uniform_checkpoint(tmp_path):
    """Codex P1 on #989: one partition does not mean the generated geometry is current."""
    pre = ["add_block S 0 400 100 600", "add_block M 400 400 600 600", *TECH]
    (s, _), path = _bdb_session(
        tmp_path, *pre, "fix_pin M west", "add_bus x[2] S.o M.i", "bdb_net_mode on",
        "run_bundler STRICT", "generate_topologies", "run_planner")
    pristine = str(tmp_path / "pristine.bdb")
    _say(s, f"save_bdb {pristine}")    # a snapshot, not a file copy of an open WAL db
    s2, out = _session(*pre, "fix_pin M north", f"open_bdb {path}",
                       "add_bus x[2] S.o M.i", "load_pipeline")
    assert "voided" in out and len(s2.bundles) == 0
    assert s2.nuts_result is None and s2.detailed_result is None
    s3, out3 = _session(*pre, f"open_bdb {pristine}", "add_bus x[2] S.o M.i",
                        "load_pipeline")
    assert "voided" not in out3 and len(s3.bundles) == 1


def test_a_fix_dropped_by_delayed_validation_is_persisted(tmp_path):
    """Codex P2 on #989: the drop at add_block was never written back."""
    (s, _), path = _bdb_session(tmp_path, "fix_pin M rect 5")
    s2, _ = _session(f"open_bdb {path}", "add_block M 0 0 100 100")
    assert "M" not in s2._pin_fixes
    s3, _ = _session(f"open_bdb {path}")
    assert "M" not in s3._pin_fixes


def test_an_off_typed_before_any_open_beats_the_checkpoint(tmp_path):
    """Codex P1 on #989: no fix was live, so no tombstone was inferred."""
    (s, _), path = _bdb_session(tmp_path, "add_block M 0 0 100 100",
                                "fix_pin M west", "fix_pin M.tx north")
    s2, _ = _session("add_block M 0 0 100 100", "fix_pin M off",
                     "fix_pin M.tx off", f"open_bdb {path}")
    assert "M" not in s2._pin_fixes and not s2._pin_fix_sel.get("M")


def test_a_fix_typed_after_bundling_voids_the_next_resume(tmp_path):
    """Codex P1 on #989: the single pin_fixes row was rewritten without the pipeline."""
    pre = ["add_block S 0 400 100 600", "add_block M 400 400 600 600", *TECH]
    (s, _), path = _bdb_session(
        tmp_path, *pre, "fix_pin M west", "add_bus x[2] S.o M.i", "bdb_net_mode on",
        "run_bundler STRICT", "generate_topologies", "run_planner",
        "fix_pin M north")                      # typed AFTER the pipeline was stored
    s2, out = _session(*pre, f"open_bdb {path}", "add_bus x[2] S.o M.i",
                       "load_pipeline")
    assert "voided" in out and len(s2.bundles) == 0


def test_a_generic_repersist_does_not_restamp_the_generation(tmp_path):
    """Codex P1 on #989: select_topology re-persisted the bundles and restamped the fixes."""
    pre = ["add_block S 0 400 100 600", "add_block M 400 400 600 600", *TECH]
    (s, _), path = _bdb_session(
        tmp_path, *pre, "fix_pin M west", "add_bus x[2] S.o M.i", "bdb_net_mode on",
        "run_bundler STRICT", "generate_topologies", "run_planner",
        "fix_pin M north", "select_topology x 1")
    s2, out = _session(*pre, f"open_bdb {path}", "add_bus x[2] S.o M.i",
                       "load_pipeline")
    assert "voided" in out and len(s2.bundles) == 0


def test_a_bundler_run_before_open_bdb_still_binds_its_fixes(tmp_path):
    """Codex P1 on #989: no stamp was written, so the live row stood in for it."""
    pre = ["add_block S 0 400 100 600", "add_block M 400 400 600 600", *TECH]
    path = str(tmp_path / "late.bdb")
    s, _ = _session(*pre, "fix_pin M west", "add_bus x[2] S.o M.i",
                    "run_bundler STRICT", f"open_bdb {path}",
                    "generate_topologies", "run_planner", "fix_pin M north")
    s2, out = _session(*pre, f"open_bdb {path}", "add_bus x[2] S.o M.i",
                       "load_pipeline")
    assert "voided" in out and len(s2.bundles) == 0


def test_rebundling_then_reloading_in_one_session_is_not_voided(tmp_path):
    """Codex P2 on #989: the cached snapshot outlived the new generation."""
    pre = ["add_block S 0 400 100 600", "add_block M 400 400 600 600", *TECH]
    (s, _), path = _bdb_session(
        tmp_path, *pre, "fix_pin M west", "add_bus x[2] S.o M.i", "bdb_net_mode on",
        "run_bundler STRICT", "generate_topologies", "run_planner")
    out = _say(s, "fix_pin M north")
    out += _say(s, "run_bundler STRICT") + _say(s, "generate_topologies")
    out += _say(s, "run_planner") + _say(s, "load_pipeline")
    assert "voided" not in out, out


def test_a_corrupt_generation_stamp_is_unverifiable(tmp_path):
    """Codex P2 on #989: a malformed pin_fixes_gen row skipped the mismatch check."""
    pre = ["add_block S 0 400 100 600", "add_block M 400 400 600 600", *TECH]
    (s, _), path = _bdb_session(
        tmp_path, *pre, "fix_pin M west", "add_bus x[2] S.o M.i", "bdb_net_mode on",
        "run_bundler STRICT", "generate_topologies", "run_planner")
    s.bdb.meta_set("pin_fixes_gen", "{not json")
    s2, out = _session(*pre, f"open_bdb {path}", "add_bus x[2] S.o M.i",
                       "load_pipeline")
    assert "voided" in out and len(s2.bundles) == 0


def test_selectors_without_declared_nets_void_the_resume(tmp_path):
    """Codex P1 on #989: with no net endpoints every bundle got an explicit empty map."""
    pre = ["add_block S 0 400 100 600", "add_block M 400 400 600 600", *TECH]
    (s, _), path = _bdb_session(
        tmp_path, *pre, "fix_pin M.i north", "add_bus x[2] S.o M.i", "bdb_net_mode on",
        "run_bundler STRICT", "generate_topologies", "run_planner")
    s2, out = _session(*pre, f"open_bdb {path}", "load_pipeline")   # nets not redeclared
    assert "voided" in out and len(s2.bundles) == 0


def test_a_failed_load_does_not_void_the_live_pipeline(tmp_path):
    """Codex P2 on #989: restamp ran after a load that restored nothing."""
    pre = ["add_block S 0 400 100 600", "add_block M 400 400 600 600", *TECH]
    s, out = _session(*pre, "fix_pin M.i north", "add_bus x[2] S.o M.i",
                      "run_bundler STRICT", f"open_bdb {tmp_path / 'empty.bdb'}",
                      "load_pipeline")
    assert "voided" not in out and len(s.bundles) == 1


def test_removing_the_last_fix_does_not_launder_an_unknown_generation(tmp_path):
    """Codex P1 on #989: the live row was empty after `off`, hiding the missing stamp."""
    pre = ["add_block S 0 400 100 600", "add_block M 400 400 600 600", *TECH]
    (s, _), path = _bdb_session(tmp_path, *pre, "fix_pin M west")
    s.bdb.meta_set("pin_fixes_gen", "")          # a checkpoint from before the stamp
    s2, _ = _session(*pre, f"open_bdb {path}", "fix_pin M off")
    from buda_cmds import pin_fix_cmds
    assert pin_fix_cmds.fixes_changed_since_generation(s2)


def test_a_non_object_pin_fixes_row_does_not_abort_open_bdb(tmp_path):
    """Codex P2 on #989: valid JSON of the wrong shape raised AttributeError."""
    (s, _), path = _bdb_session(tmp_path, "add_block M 0 0 100 100", "fix_pin M west")
    for bad in ("[]", "null", "3"):
        s.bdb.meta_set("pin_fixes", bad)
        s2, out = _session(f"open_bdb {path}")
        assert "unreadable" in out and not s2._pin_fixes


def test_restoring_different_fixes_voids_live_bundles(tmp_path):
    """Codex P1 on #989: bundles built before open_bdb were never split for them."""
    pre = ["add_block S 0 400 100 600", "add_block M 400 400 600 600", *TECH]
    (s, _), path = _bdb_session(tmp_path, *pre, "fix_pin M.a west", "fix_pin M.b north")
    s2, out = _session(*pre, "add_bus x[2] S.o M.a", "add_bus y[2] S.o M.b",
                       "run_bundler STRICT", f"open_bdb {path}")
    assert "voided" in out and len(s2.bundles) == 0


def test_a_malformed_nested_record_leaves_the_session_untouched(tmp_path):
    """Codex P2 on #989: nested garbage was installed before revalidate() choked on it."""
    import json
    good = {"faces": 8, "rect": -1, "has_window": False, "lo": 0, "hi": 0}
    cases = ['{"blocks":{},"selectors":[["M","name","tx",null]]}',
             '{"blocks":{"A":%s,"B":{"faces":"x"}},"selectors":[]}' % json.dumps(good),
             '{"blocks":[],"selectors":{}}',
             '{"blocks":{"M":{"faces":16,"rect":-1,"has_window":false,"lo":0,"hi":0}},"selectors":[]}',
             '{"blocks":{"M":{"faces":8,"rect":-2,"has_window":false,"lo":0,"hi":0}},"selectors":[]}',
             '{"blocks":{"M":{"faces":8,"rect":-1,"has_window":true,"lo":9,"hi":9}},"selectors":[]}',
             '{"blocks":{"M":{"faces":100000000000000000000,"rect":-1,'
             '"has_window":false,"lo":0,"hi":0}},"selectors":[]}']
    for i, bad in enumerate(cases):
        path = str(tmp_path / f"n{i}.bdb")
        s, _ = _session(f"open_bdb {path}", "add_block M 0 0 100 100", "fix_pin M west")
        s.bdb.meta_set("pin_fixes", bad)
        s2, out = _session(f"open_bdb {path}")
        assert "unreadable" in out and not s2._pin_fixes and not s2._pin_fix_sel, bad


# ── layer (PR 2a) ────────────────────────────────────────────────────────────

LAYERS_FLOW = FLOW / "layers.buda"


def _layers_lines(drop=()):
    return [l.strip() for l in LAYERS_FLOW.read_text().splitlines()
            if l.strip() and not l.startswith("#")
            and not l.startswith(("visualize", "dump_", "report_", "check_design"))
            and not any(l.startswith(d) for d in drop)]


def _layer_names(s):
    names = {lid: n for n, lid in s._layer_name_map.items()}
    return {w.input.original_bundle.get_net_names()[0].split("_")[0]:
            [names[l] for l in w.plan.seg_layers] for w in s.bundles}


def test_a_fixed_layer_is_where_the_landing_wire_goes():
    s, out = _session(*_layers_lines())
    got = _layer_names(s)
    assert got["fixed"] == ["M3"], got
    assert got["down"] == ["M4"], got
    assert got["free"] == ["M5"], got        # untouched bus keeps the planner's choice


def test_without_the_layer_fix_the_same_buses_take_the_top_layers():
    s, _ = _session(*_layers_lines(drop=("fix_pin",)))
    got = _layer_names(s)
    assert got["fixed"] != ["M3"] and got["down"] != ["M4"], got


def test_a_layer_narrows_the_faces_to_the_ones_it_can_reach():
    s, _ = _session(*BASE, "fix_pin MAC layer M4")            # M4 is horizontal
    assert {st[4] for st in s.fp.pin_fix_strips("MAC")} == {E, W}
    s2, _ = _session(*BASE, "fix_pin MAC layer M4,M5")        # + M5, vertical
    assert {st[4] for st in s2.fp.pin_fix_strips("MAC")} == {N, S, E, W}


@pytest.mark.parametrize("line,needle", [
    ("fix_pin MAC north layer M4", "cannot reach"),
    ("fix_pin MAC east layer M5", "cannot reach"),
    ("fix_pin MAC east layer M9", "unknown layer"),
    ("fix_pin MAC east layer", "takes a layer"),
])
def test_a_bad_layer_declaration_is_refused_and_changes_nothing(line, needle):
    s, _ = _session(*BASE)
    out = _say(s, line)
    assert "Error:" in out and needle in out, out
    assert not s.fp.has_pin_fix("MAC")


def test_two_pins_that_share_no_layer_cannot_share_a_bundle():
    pre = ["add_block S 0 400 100 600", "add_block A 400 400 600 600",
           "add_block B 400 0 600 200", *TECH,
           "def_layer 3 M3 H LOW 0", "def_layer 2 M2 H LOW 0",
           "def_track_pattern 3 0 (SIGNAL 2 2)x60",
           "def_track_pattern 2 0 (SIGNAL 2 2)x60",
           "fix_pin A.i west layer M3", "fix_pin B.i west layer M2",
           "add_net x_0 S.o A.i,B.i"]
    with pytest.raises(SystemExit):
        _session(*pre, "run_bundler STRICT")


def test_a_layer_fix_and_set_bus_layers_must_share_a_layer():
    pre = ["add_block S 0 400 100 600", "add_block A 400 400 600 600", *TECH,
           "def_layer 3 M3 H LOW 0", "def_track_pattern 3 0 (SIGNAL 2 2)x60",
           "fix_pin A.i west layer M3", "set_bus_layers x_ M4",
           "add_net x_0 S.o A.i"]
    with pytest.raises(SystemExit):
        _session(*pre, "run_bundler STRICT")


def test_a_wire_off_its_fixed_layer_is_reported_as_pin_fix():
    s, _ = _session(*_layers_lines())
    w = next(w for w in s.bundles
             if w.input.original_bundle.get_net_names()[0].startswith("fixed"))
    wrong = s._layer_name_map["M5"]
    w.plan.seg_layers = [wrong] * len(w.plan.seg_layers)
    out = _say(s, "check_design")
    assert "PIN_FIX" in out and "outside the fixed layer" in out, out


def test_the_layer_flow_routes_clean_and_audits_clean():
    rc, out = _run("layers.buda")
    assert rc == 0, out[-800:]
    assert "Error:" not in out and "PIN_FIX" not in out, out[-800:]
    assert out.count("Success: no violations found") >= 2, out[-1500:]


def test_a_layer_survives_the_checkpoint(tmp_path):
    path = str(tmp_path / "ly.bdb")
    s, _ = _session(f"open_bdb {path}", "add_block M 0 0 100 100",
                    "def_layer 3 M3 H LOW 0", "fix_pin M west layer M3",
                    "fix_pin M.t west layer M3")
    s2, out = _session("def_layer 3 M3 H LOW 0", f"open_bdb {path}")
    assert s2._pin_fixes["M"]["layers"] == ["M3"], out
    assert s2._pin_fix_sel["M"][("name", "t")]["layers"] == ["M3"]
    assert "layer M3" in _say(s2, "dump_pin_fixes")


def test_a_stored_layers_field_of_the_wrong_shape_is_unreadable(tmp_path):
    path = str(tmp_path / "ly2.bdb")
    s, _ = _session(f"open_bdb {path}", "add_block M 0 0 100 100", "fix_pin M west")
    s.bdb.meta_set("pin_fixes", '{"blocks":{"M":{"faces":8,"rect":-1,'
                   '"has_window":false,"lo":0,"hi":0,"layers":[3]}},"selectors":[]}')
    s2, out = _session(f"open_bdb {path}")
    assert "unreadable" in out and not s2._pin_fixes


# ── order (PR 2b) ────────────────────────────────────────────────────────────

ORDER_FLOW = FLOW / "order.buda"


def _order_lines(drop=()):
    return [l.strip() for l in ORDER_FLOW.read_text().splitlines()
            if l.strip() and not l.startswith("#")
            and not l.startswith(("visualize", "dump_", "report_", "check_design"))
            and not any(l.startswith(d) for d in drop)]


def _tracks_by_bit(s, first_net):
    """{bit: track} of the landing segment of the bundle whose first net
    starts with `first_net` — the one with the most bits on the pin's side."""
    bid = next(w.input.original_bundle.id for w in s.bundles
               if w.input.original_bundle.get_net_names()[0].startswith(first_net))
    segs = {}
    for n in s.detailed_result.net_segments:
        if n.bundle_id == bid:
            segs.setdefault(n.seg_idx, {})[n.bit_index] = n.track_position
    return segs


def test_index_runs_bit0_low_and_rindex_runs_it_high():
    s, _ = _session(*_order_lines())
    up = next(iter(_tracks_by_bit(s, "up").values()))
    dn = next(iter(_tracks_by_bit(s, "dn").values()))
    assert [up[b] for b in sorted(up)] == sorted(up.values())          # ascending
    assert [dn[b] for b in sorted(dn)] == sorted(dn.values(), reverse=True)


def test_without_order_both_buses_run_the_same_way():
    s, _ = _session(*_order_lines(drop=("fix_pin MAC.dn_in",)),
                    "fix_pin MAC.dn_in west at 20..80")
    dn = next(iter(_tracks_by_bit(s, "dn").values()))
    assert [dn[b] for b in sorted(dn)] == sorted(dn.values())          # default LO_HI


def test_a_straight_wire_between_opposite_orders_is_dropped_and_a_bend_reverses():
    s, out = _session(*_order_lines())
    assert "dropped 1 candidate(s) that join fixed pins of opposite order" in out
    w = next(w for w in s.bundles
             if w.input.original_bundle.get_net_names()[0].startswith("x_"))
    assert all(not c.type.startswith("I_") for c in w.input.candidates)
    legs = _tracks_by_bit(s, "x_")
    ends = [t for t in legs.values() if len(t) == 4]
    asc = [sorted(t, key=t.get) == sorted(t) for t in ends]
    assert True in asc and False in asc, legs        # one leg ascending, one reversed


def test_when_every_candidate_conflicts_the_pool_is_kept_and_reported():
    pre = ["add_block A 0 0 100 100", "add_block B 100 0 200 100", *TECH,
           "fix_pin A.o east order index", "fix_pin B.i west order rindex",
           "add_bus x[4] A.o B.i", "run_bundler STRICT"]
    s, out = _session(*pre, "generate_topologies")
    assert "every candidate of bundle" in out and "opposite order" in out, out
    assert s.bundles[0].input.candidates       # never stranded


def test_the_order_flow_routes_clean():
    rc, out = _run("order.buda")
    assert rc == 0, out[-800:]
    assert "Error:" not in out and "PIN_FIX" not in out, out[-800:]
    assert out.count("Success: no violations found") >= 2, out[-1500:]


def test_an_order_survives_the_checkpoint_and_a_bad_one_is_unreadable(tmp_path):
    path = str(tmp_path / "ord.bdb")
    s, _ = _session(f"open_bdb {path}", "add_block M 0 0 100 100",
                    "fix_pin M west order rindex", "fix_pin M.t east order index")
    s2, out = _session(f"open_bdb {path}")
    assert s2._pin_fixes["M"]["order"] == "rindex", out
    assert s2._pin_fix_sel["M"][("name", "t")]["order"] == "index"
    assert "order rindex" in _say(s2, "dump_pin_fixes")
    s2.bdb.meta_set("pin_fixes", '{"blocks":{"M":{"faces":8,"rect":-1,'
                    '"has_window":false,"lo":0,"hi":0,"order":"sideways"}},'
                    '"selectors":[]}')
    s3, out3 = _session(f"open_bdb {path}")
    assert "unreadable" in out3 and not s3._pin_fixes


def test_a_selector_fix_with_a_layer_narrows_the_faces_the_generator_sees():
    """Codex P1 on #990: the per-bundle map carried the DECLARED faces."""
    pre = ["add_block S 0 400 100 600", "add_block M 400 400 600 600", *TECH,
           "def_layer 3 M3 H LOW 0", "def_track_pattern 3 0 (SIGNAL 2 2)x60",
           "fix_pin M.i layer M3", "add_bus x[2] S.o M.i", "run_bundler STRICT",
           "generate_topologies"]
    s, _ = _session(*pre)
    m = s.bundles[0].input.candidates[0].block_fixes
    assert m["M"][0] == (E | W), m
    # the pool is the one an explicit `east west` selector produces
    ref, _ = _session(*[("fix_pin M.i east west" if l == "fix_pin M.i layer M3" else l)
                        for l in pre])
    key = lambda sess: sorted(c.type for c in sess.bundles[0].input.candidates)
    assert key(s) == key(ref) and key(s)


def test_an_orthogonal_only_overlap_with_set_bus_layers_is_refused():
    """Codex P2 on #990: the other axis's layers made the overlap look non-empty."""
    pre = ["add_block S 0 400 100 600", "add_block A 400 400 600 600", *TECH,
           "def_layer 3 M3 H LOW 0", "def_track_pattern 3 0 (SIGNAL 2 2)x60",
           "fix_pin A.i west layer M3", "set_bus_layers x_ M5",     # M5 is vertical
           "add_net x_0 S.o A.i"]
    with pytest.raises(SystemExit):
        _session(*pre, "run_bundler STRICT")


def test_layer_order_in_a_declaration_does_not_split_a_bundle():
    """Codex P2 on #990: `layer M4,M5` and `layer M5,M4` are the same constraint."""
    s, _ = _session("add_block S 0 400 100 600", "add_block M 400 400 600 600", *TECH,
                    "fix_pin M.a layer M4,M5", "fix_pin M.b layer M5,M4",
                    "add_bus x[2] S.o M.a", "add_bus y[2] S.o M.b",
                    "run_bundler STRICT")
    assert len(s.bundles) == 1


def test_a_restored_layer_fix_narrows_once_its_layer_is_declared(tmp_path):
    """Codex P1 on #990: open_bdb before the stack left the faces un-narrowed."""
    path = str(tmp_path / "late_layer.bdb")
    s, _ = _session(*TECH, f"open_bdb {path}", "add_block M 0 0 100 100",
                    "fix_pin M layer M4")                      # M4 is horizontal
    s2, _ = _session(f"open_bdb {path}", "add_block M 0 0 100 100")
    assert s2._pin_fixes["M"]["layers"] == ["M4"]
    assert {st[4] for st in s2.fp.pin_fix_strips("M")} == {N, S, E, W}   # layer unknown yet
    _say(s2, "def_layer 4 M4 H TOP 0")
    assert {st[4] for st in s2.fp.pin_fix_strips("M")} == {E, W}


def test_a_pin_layer_must_have_a_layer_on_its_axis_in_an_existing_ndr_mask():
    """Codex P2 on #990: only set_bus_layers was checked per axis."""
    pre = ["add_block S 0 400 100 600", "add_block A 400 400 600 600", *TECH,
           "def_layer 3 M3 H LOW 0", "def_track_pattern 3 0 (SIGNAL 2 2)x60",
           "def_ndr w width x2 layers M5", "set_ndr x_ w",
           "fix_pin A.i west layer M3", "add_net x_0 S.o A.i"]
    sess = buda_cli.BudaSession()
    sess.no_viz = True
    out = io.StringIO()
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit):
        for c in pre + ["run_bundler STRICT"]:
            sess.do_command(c)
    assert "no layer in common" in out.getvalue(), out.getvalue()


def test_the_order_gate_also_filters_a_knob_replay_pool():
    """Codex P2 on #990: _gen_fixed (knob-memo replays) skipped the filter."""
    s, _ = _session(*_order_lines(drop=("generate_topologies", "run_planner",
                                        "run_nuts", "run_detailed_nuts")))
    import buda
    w = next(w for w in s.bundles
             if w.input.original_bundle.get_net_names()[0].startswith("x_"))
    pool = s._gen_fixed(buda.TopologyGenerator(s.fp), w, "LEFT", ["RIGHT"])
    assert pool and all(not c.type.startswith("I_") for c in pool), \
        [c.type for c in pool]


def test_a_restored_layer_that_cannot_reach_its_faces_is_dropped(tmp_path):
    """Codex P2 on #990: it was restored un-narrowed instead of refused."""
    path = str(tmp_path / "bad_dir.bdb")
    s, _ = _session(*TECH, f"open_bdb {path}", "add_block M 0 0 100 100",
                    "fix_pin M east layer M4")                  # M4 is horizontal
    s2, _ = _session(f"open_bdb {path}", "add_block M 0 0 100 100")
    assert "M" in s2._pin_fixes
    out = _say(s2, "def_layer 4 M4 V TOP 0")                    # ... now vertical
    assert "Error:" in out and "cannot reach" in out, out
    assert "M" not in s2._pin_fixes


def test_a_dropped_restored_fix_does_not_wake_up_when_its_block_is_declared(tmp_path):
    """Codex P1 on #990: the floorplan kept the entry for a block not yet declared."""
    path = str(tmp_path / "early.bdb")
    s, _ = _session(*TECH, f"open_bdb {path}", "add_block M 0 0 100 100",
                    "fix_pin M east layer M4")
    s2, _ = _session(f"open_bdb {path}")                  # restored, no block, no layer
    out = _say(s2, "def_layer 4 M4 V TOP 0")              # now incompatible
    assert "cannot reach" in out, out
    _say(s2, "add_block M 0 0 100 100")
    assert not s2.fp.has_pin_fix("M")
    assert "M" not in s2._pin_fixes


def test_a_user_candidate_joining_opposite_orders_is_reported_by_check_design():
    """Codex P2 on #990: the generation gate cannot see a hand-edited candidate."""
    lines = _order_lines(drop=("generate_topologies", "run_planner", "run_nuts",
                               "run_detailed_nuts"))
    s, _ = _session(*lines, "generate_topologies")
    import buda
    w = next(w for w in s.bundles
             if w.input.original_bundle.get_net_names()[0].startswith("x_"))
    raw = buda.TopologyGenerator(s.fp).generate_candidates("LEFT", ["RIGHT"])
    straight = next(i for i, c in enumerate(raw) if c.type.startswith("I_"))
    w.input.candidates = list(raw)          # as a USER candidate would arrive
    bid = w.input.original_bundle.id
    _say(s, f"select_topology id:{bid} {straight + 1}")
    _say(s, "run_planner")
    _say(s, "run_nuts")
    out = _say(s, "check_design")
    assert "PIN_FIX" in out and "opposite order" in out, out


def test_rebundling_does_not_read_the_previous_wrappers_layer_base():
    """Codex P2 on #990: the id-keyed base outlived its wrappers."""
    pre = ["add_block S 0 400 100 600", "add_block A 400 400 600 600", *TECH,
           "def_layer 3 M3 H LOW 0", "def_track_pattern 3 0 (SIGNAL 2 2)x60",
           "def_layer 6 M6 V TOP 0", "def_track_pattern 6 0 (SIGNAL 2 2)x60",
           "def_ndr w width x2 layers M3,M5", "set_ndr x_ w",
           "fix_pin A.i west layer M3", "add_net x_0 S.o A.i",
           "run_bundler STRICT"]
    s, _ = _session(*pre, "set_ndr x_ off", "run_bundler STRICT")
    got = sorted(s.bundles[0].input.allowed_layers)
    ref, _ = _session(*[l for l in pre if not l.startswith(("def_ndr", "set_ndr"))])
    assert got == sorted(ref.bundles[0].input.allowed_layers) and 6 in got, got


# ── cell-level fix, turned with the instance (PR 3) ─────────────────────────

import orient_rect  # noqa: E402
from buda_cmds import pin_fix_cmds  # noqa: E402

_ALL_O = sorted(orient_rect.ORIENT_MAPS)


def _cell_fix(**kw):
    base = {"faces": 0, "rect": -1, "has_window": False, "lo": 0, "hi": 0,
            "layers": [], "order": ""}
    base.update(kw)
    return base


def _strips_of(fix, w, h):
    fp = buda.Floorplan()
    fp.add_block("B", 0, 0, w, h)
    fp.set_pin_fix("B", fix["faces"], fix["rect"], fix["has_window"],
                   fix["lo"], fix["hi"])
    return sorted(fp.pin_fix_strips("B"))


@pytest.mark.parametrize("orient", _ALL_O)
@pytest.mark.parametrize("faces,win", [(W, (20, 60)), (E, (10, 90)), (N, (5, 40)),
                                       (S, (30, 50)), (E | W, (25, 75)),
                                       (N | S, (10, 20))])
def test_the_transformed_fix_lands_where_the_footprint_transform_puts_the_strips(
        orient, faces, win):
    """The cell's strips, pushed through the SAME 8-orientation rect transform
    the multi-rect footprint uses, are the strips of the transformed fix."""
    cw, ch = 60, 100
    fix = _cell_fix(faces=faces, has_window=True, lo=win[0], hi=win[1])
    tfix = pin_fix_cmds.xf_fix(fix, orient, cw, ch)
    swap = orient_rect.ORIENT_MAPS[orient][0]
    bw, bh = (ch, cw) if swap else (cw, ch)
    got = _strips_of(tfix, bw, bh)
    want = []
    for (x1, y1, x2, y2, face) in _strips_of(fix, cw, ch):
        r = orient_rect.oxf_rect(orient, x1, y1, x2, y2, cw, ch)
        want.append(r)
    assert [g[:4] for g in got] == sorted(want)


def test_a_flipped_instance_reverses_the_order_it_was_declared_in():
    """`order` is bit 0 at the low coordinate in the PLACED frame; the cell's
    pins keep their bit order, so a transform that reverses the along-axis
    reverses the order."""
    f = _cell_fix(faces=W, has_window=True, lo=10, hi=50, order="index")
    assert pin_fix_cmds.xf_fix(f, "N", 60, 100)["order"] == "index"
    assert pin_fix_cmds.xf_fix(f, "FN", 60, 100)["order"] == "rindex"   # y flipped
    assert pin_fix_cmds.xf_fix(f, "FS", 60, 100)["order"] == "index"    # x flipped only
    assert pin_fix_cmds.xf_fix(f, "S", 60, 100)["order"] == "rindex"


def _hier_session(*extra):
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        s.do_command(f"source {FLOW / 'hier_cell.buda'}")
        for c in extra:
            s.do_command(c)
    return s, out.getvalue()


def test_the_cell_flow_turns_one_declaration_into_three_faces():
    s, out = _hier_session()
    assert "Error:" not in out
    st = {b: s.fp.pin_fix_strips(b) for b in ("mac_n", "mac_s", "mac_e")}
    assert st["mac_n"] == [(500, 720, 501, 760, W)]        # west, y 20..60 of the block
    assert st["mac_s"] == [(599, 390, 600, 430, E)]        # turned 180: east, window flips
    assert st["mac_e"] == [(540, 50, 580, 51, S)]          # turned 90: the axes swap
    assert out.count("Success: no violations found") >= 2, out[-600:]


def test_an_instance_level_fix_replaces_the_cell_level_one():
    s, out = _hier_session()
    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    txt = (FLOW / "hier_cell.buda").read_text().replace(
        "dump_pin_fixes\n\nderive_busterms",
        "fix_pin mac_s north\ndump_pin_fixes\n\nderive_busterms", 1)
    with contextlib.redirect_stdout(io.StringIO()):
        for line in txt.splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                s2.do_command(line.replace("source ../tracks/tracks.buda",
                                           f"source {_ROOT / 'flow/tracks/tracks.buda'}"))
    assert s2.fp.pin_fix_strips("mac_s")[0][4] == N      # the instance's own fix
    assert s2.fp.pin_fix_strips("mac_n")[0][4] == W      # the others still follow the cell


def test_a_cell_fix_needs_a_known_cell_and_a_landing():
    s, _ = _session("open_bdb :memory:", "add_cell k 60 100")
    assert "no BDB cell named 'nope'" in _say(s, "fix_pin cell:nope west")
    assert "cell:nope" not in s._pin_fixes
    out = _say(s, "fix_pin cell:k west at 500..600")
    assert "admits no landing" in out and "cell:k" not in s._pin_fixes
    out = _say(s, "fix_pin cell:k rect 3 west")
    assert "out of range" in out
    out = _say(s, "fix_pin cell:k at 10..40")
    assert "ONE axis" in out and "cell:k" not in s._pin_fixes
    out = _say(s, "fix_pin cell:k west at 10..40")
    assert "every instance" in out and "cell:k" in s._pin_fixes


def test_a_cell_fix_round_trips_through_the_checkpoint(tmp_path):
    db = tmp_path / "c.bdb"
    s, _ = _session(f"open_bdb {db}", "add_cell k 60 100", "fix_pin cell:k west at 10..40")
    s2, out = _session(f"open_bdb {db}")
    assert s2._pin_fixes.get("cell:k", {}).get("has_window"), out


# ── per-pin selectors and layer/order in hierarchical frames (PR 3) ─────────

def _run_flow_text(text, name="x.buda"):
    """Run flow text with the checked-in track fixtures, return (session, out)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = io.StringIO()
    text = text.replace("source ../tracks/tracks.buda",
                        f"source {_ROOT / 'flow/tracks/tracks.buda'}")
    exited = False
    with contextlib.redirect_stdout(out):
        try:
            for line in text.splitlines():
                if line.strip() and not line.lstrip().startswith("#") \
                        and not line.startswith(("visualize", "dump_")):
                    s.do_command(line)
        except SystemExit:
            exited = True
    return s, out.getvalue() + ("\n[exit]" if exited else "")


def _flow_text(name, *edits):
    txt = (FLOW / name).read_text()
    for old, new in edits:
        assert old in txt, old
        txt = txt.replace(old, new, 1)
    return txt


def _faces_of(s, w):
    return {b: v[0] for b, v in dict(w.input.candidates[0].block_fixes).items()}


def test_a_cell_pin_selector_splits_the_hier_bundle_and_turns_with_the_instance():
    s, out = _run_flow_text(_flow_text("hier_pins.buda"))
    assert out.count("split hbundle") == 2, out
    assert "Error:" not in out
    by = {w.input.original_bundle.get_net_names()[0][:4]: _faces_of(s, w)
          for w in s.bundles}
    assert by["lo_a"] == {"mac_a": W} and by["hi_a"] == {"mac_a": N}
    # turned 180 degrees: west -> east, north -> south
    assert by["lo_b"] == {"mac_b": E} and by["hi_b"] == {"mac_b": S}
    assert out.count("Success: no violations found") >= 2, out[-500:]


def test_the_hier_pins_flow_lands_each_bus_on_its_own_face():
    s, _ = _run_flow_text(_flow_text("hier_pins.buda"))
    comps = {c.name: c for c in s.bdb.all_components()}
    for net, blk, face in (("lo_a", "mac_a", W), ("hi_a", "mac_a", N),
                           ("lo_b", "mac_b", E), ("hi_b", "mac_b", S)):
        c = comps[blk]
        w = next(w for w in s.bundles
                 if w.input.original_bundle.get_net_names()[0].startswith(net))
        t = w.input.candidates[w.plan.selected_topology_index]
        edge = {W: lambda p: p.x == c.x1, E: lambda p: p.x == c.x2,
                N: lambda p: p.y == c.y2, S: lambda p: p.y == c.y1}[face]
        assert any(edge(p) for sg in t.segments for p in (sg.start, sg.end)), \
            (net, blk, [(sg.start.x, sg.start.y, sg.end.x, sg.end.y) for sg in t.segments])


def test_cell_local_templates_and_replicas_get_one_congruent_map():
    s, out = _run_flow_text(_flow_text("hier_cell_local.buda"))
    assert "Error:" not in out, out[-800:]
    assert out.count("Success: no violations found") >= 2
    for w in s.bundles:
        fm = dict(w.input.candidates[0].block_fixes)
        # the absolute-frame names of the instance this wrapper routes
        assert fm and all("/" in b for b in fm), fm
        for b, v in fm.items():
            if b.endswith("pb_i") or b.endswith("pc_i"):
                if w.input.original_bundle.get_net_names()[0].startswith("pa_pb") and b.endswith("pb_i"):
                    assert v[0] == N
    layers = {s.layers.get_layer_name(l) if hasattr(s.layers, "get_layer_name") else l
              for w in s.bundles for l in w.plan.seg_layers}
    assert layers


def test_the_fixed_layer_reaches_hier_bundles():
    text = _flow_text("hier_cell_local.buda", ("\nfix_pin cell:pipe_cell.in  north layer M5", "\nfix_pin cell:pipe_cell.in  north layer M3"))
    s, out = _run_flow_text(text)
    assert "Error:" not in out, out[-600:]
    names = {v: k for k, v in s._layer_name_map.items()}
    for w in s.bundles:
        if w.input.original_bundle.get_net_names()[0].startswith("pa_pb"):
            # the V leg that lands on pb's north face is on M3, not the planner's M5
            assert "M3" in [names[l] for l in w.plan.seg_layers], \
                [names[l] for l in w.plan.seg_layers]


def test_an_instance_specific_selector_on_a_replicated_template_is_refused():
    text = _flow_text("hier_cell_local.buda",
                      ("\nfix_pin cell:pipe_cell.out east",
                       "\nfix_pin cell:pipe_cell.out east\nfix_pin proc_i/pb_i.in south"))
    _s, out = _run_flow_text(text)
    # the fix lives on ONE occurrence of a template solved once and copied
    assert "INCONSISTENTLY" in out and out.endswith("[exit]"), out[-600:]


def test_a_local_name_selector_governs_every_occurrence():
    text = _flow_text("hier_cell_local.buda",
                      ("\nfix_pin cell:pipe_cell.out east",
                       "\nfix_pin cell:pipe_cell.out east\nfix_pin pb_i.in south"))
    s, out = _run_flow_text(text)
    assert "Error:" not in out, out[-600:]
    for w in s.bundles:
        if w.input.original_bundle.get_net_names()[0].startswith("pa_pb"):
            fm = dict(w.input.candidates[0].block_fixes)
            assert {v[0] for b, v in fm.items() if b.endswith("pb_i")} == {S}, fm


def test_hier_order_follows_the_instance_orientation():
    text = _flow_text("hier_pins.buda",
                      ("\nfix_pin cell:mac_cell.lo_in west",
                       "\nfix_pin cell:mac_cell.lo_in west at 20..80 order index"))
    s, out = _run_flow_text(text)
    assert "Error:" not in out, out[-600:]
    names = {w.input.original_bundle.get_net_names()[0][:4]: w for w in s.bundles}
    for key, want in (("lo_a", "index"), ("lo_b", "rindex")):
        w = names[key]
        topo = w.input.candidates[w.plan.selected_topology_index]
        orders = pin_fix_cmds.segment_orders(s, w, topo)
        assert {o for v in orders.values() for o in v} == {want}, (key, orders)


def test_the_cell_flows_route_clean():
    for flow in ("hier_cell.buda", "hier_pins.buda", "hier_cell_local.buda"):
        rc, out = _run(flow)
        assert rc == 0 and "Error:" not in out, (flow, out[-600:])
        assert out.count("Success: no violations found") >= 2, (flow, out[-800:])


def test_hier_selectors_survive_a_checkpoint_resume(tmp_path):
    """Fixes come back with the BDB, nets come back with it too (no add_bus in
    the resuming session), and load_pipeline re-stamps each bundle's map."""
    db = tmp_path / "c.bdb"
    _s, out = _run_flow_text(_flow_text("hier_pins.buda",
                                        ("open_bdb :memory:", f"open_bdb {db}")))
    assert "Error:" not in out
    resume = f"""source ../tracks/tracks.buda
corner_margin dx 5 dy 5
set_min_stub_length 2
open_bdb {db}
add_blocks_from_bdb 0
load_pipeline
run_planner hier 5
run_nuts
check_design nuts
run_detailed_nuts
check_design dnuts
"""
    s2, out2 = _run_flow_text(resume)
    assert "restored" in out2 and "voided" not in out2, out2[-800:]
    by = {w.input.original_bundle.get_net_names()[0][:4]: _faces_of(s2, w)
          for w in s2.bundles}
    assert by == {"lo_a": {"mac_a": W}, "hi_a": {"mac_a": N},
                  "lo_b": {"mac_b": E}, "hi_b": {"mac_b": S}}
    assert out2.count("Success: no violations found") >= 2


def test_a_wire_off_a_cell_pins_fixed_layer_is_reported_in_a_hier_flow():
    text = _flow_text("hier_pins.buda",
                      ("\nfix_pin cell:mac_cell.lo_in west",
                       "\nfix_pin cell:mac_cell.lo_in west layer M4"))
    s, out = _run_flow_text(text)
    assert "Error:" not in out and "PIN_FIX" not in out, out[-600:]
    names = {v: k for k, v in s._layer_name_map.items()}
    w = next(w for w in s.bundles
             if w.input.original_bundle.get_net_names()[0].startswith("lo_a"))
    assert [names[l] for l in w.plan.seg_layers] == ["M4"], \
        [names[l] for l in w.plan.seg_layers]       # the mask put the wire on M4
    w.plan.seg_layers = [s._layer_name_map["M6"]] * len(w.plan.seg_layers)
    out = _say(s, "check_design")
    assert "PIN_FIX" in out and "outside the fixed layer" in out, out


# ── Codex on #992 ──────────────────────────────────────────────────────────

def _hier_cell_upto_blocks():
    """hier_cell.buda without its cell fix, stopped after the blocks exist."""
    lines = []
    for line in (FLOW / "hier_cell.buda").read_text().splitlines():
        if line.startswith("fix_pin") or line.startswith("dump_pin_fixes"):
            continue
        if line.startswith("derive_busterms"):
            lines += [line, "add_blocks_from_bdb 0"]
            break
        lines.append(line)
    return "\n".join(lines)


def test_a_cell_fix_declared_after_the_blocks_exist_reaches_them():
    """P1: the fix was stored but the projected blocks never heard of it."""
    s, _ = _run_flow_text(_hier_cell_upto_blocks())
    assert s.fp.pin_fix_strips("mac_n") == []
    _say(s, "fix_pin cell:mac_cell west at 20..60")
    assert s.fp.pin_fix_strips("mac_n") == [(500, 720, 501, 760, W)]
    assert s.fp.pin_fix_strips("mac_s")[0][4] == E        # turned with the instance
    _say(s, "fix_pin cell:mac_cell north")                # replaced
    assert s.fp.pin_fix_strips("mac_n")[0][4] == N
    _say(s, "fix_pin mac_n east")                         # an instance override
    assert s.fp.pin_fix_strips("mac_n")[0][4] == E
    _say(s, "fix_pin mac_n off")                          # ... removed: the cell's comes back
    assert s.fp.pin_fix_strips("mac_n")[0][4] == N
    _say(s, "fix_pin cell:mac_cell off")
    assert s.fp.pin_fix_strips("mac_n") == [] and s.fp.pin_fix_strips("mac_s") == []


def test_editing_an_expanded_bundle_sees_a_local_name_selector():
    """P2: the edit session resolved with the wrapper's absolute locality only."""
    text = _flow_text("hier_cell_local.buda",
                      ("\nfix_pin cell:pipe_cell.out east",
                       "\nfix_pin cell:pipe_cell.out east\nfix_pin pb_i.in south"))
    s, out = _run_flow_text(text)
    assert "Error:" not in out
    w = next(w for w in s.bundles
             if w.input.original_bundle.get_net_names()[0].startswith("pa_pb_i"))
    _say(s, f"edit_topology {w.input.original_bundle.id} new")
    fm = dict(s._edit_topo.block_fixes)
    assert {v[0] for b, v in fm.items() if b.endswith("pb_i")} == {S}, fm
    _say(s, "edit_abort")


def test_an_instance_block_fix_on_a_replicated_template_is_refused_too():
    """P2: the congruence check was gated on selectors; a block-level override
    on one occurrence is just as unable to ride a template solved once."""
    text = _flow_text("hier_cell_local.buda",
                      ("\nfix_pin cell:pipe_cell.out east",
                       "\nfix_pin cell:pipe_cell.out east\nfix_pin proc_i/pb_i south"))
    _s, out = _run_flow_text(text)
    assert "INCONSISTENTLY" in out and out.endswith("[exit]"), out[-600:]


def test_a_turned_replica_with_composed_tokens_is_congruent_with_its_template():
    """P2 (#992): a cell fix reads as different PLACED faces on a turned
    occurrence; congruence compares the declarations, and the expansion's
    transform accounts for the turn."""
    text = _flow_text(
        "hier_cell_local.buda",
        ("\nderive_busterms 1",
         "\nrotate_comp proc_j 180\nrotate_comp proc_j/pa_i 180\n"
         "rotate_comp proc_j/pb_i 180\nrotate_comp proc_j/pc_i 180\nderive_busterms 1"))
    s, out = _run_flow_text(text)
    assert "INCONSISTENTLY" not in out and "Error:" not in out, out[-600:]
    assert out.count("Success: no violations found") >= 2, out[-800:]
    got = {w.input.original_bundle.get_net_names()[0]:
           {b.rsplit("/", 1)[-1]: v[0] for b, v in dict(w.input.candidates[0].block_fixes).items()}
           for w in s.bundles}
    assert got["pa_pb_i_0"]["pb_i"] == N and got["pa_pb_j_0"]["pb_i"] == S


def test_a_layer_declared_after_the_instances_narrows_a_restored_cell_fix(tmp_path):
    """P2 (#992): a restored cell fix naming a layer projects un-narrowed until
    the layer exists; declaring it must re-read every projected instance."""
    db = tmp_path / "c.bdb"
    pre = [f"open_bdb {db}", "add_cell k 100 100", "add_inst a k - 0 0", *TECH,
           "fix_pin cell:k layer M4"]
    _session(*pre)
    s, _ = _session(f"open_bdb {db}", "add_blocks_from_bdb 0")
    assert {st[4] for st in s.fp.pin_fix_strips("a")} == {N, S, E, W}   # layer unknown yet
    _say(s, "def_layer 4 M4 H TOP 0")
    assert {st[4] for st in s.fp.pin_fix_strips("a")} == {E, W}
