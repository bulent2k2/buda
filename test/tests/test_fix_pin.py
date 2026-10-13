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


def test_the_hier_flow_says_selectors_are_not_replayed():
    s, _ = _session("add_block M 0 0 100 100", "fix_pin M.tx east")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        s._apply_fp_session_settings(buda.Floorplan())
    assert "per-pin selectors are not replayed" in out.getvalue()


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
    s2, out = _session(*pre, "fix_pin M north", f"open_bdb {path}",
                       "add_bus x[2] S.o M.i", "load_pipeline")
    assert "voided" in out and len(s2.bundles) == 0
    s3, out3 = _session(*pre, f"open_bdb {path}", "add_bus x[2] S.o M.i",
                        "load_pipeline")
    assert "voided" not in out3 and len(s3.bundles) == 1


def test_a_fix_dropped_by_delayed_validation_is_persisted(tmp_path):
    """Codex P2 on #989: the drop at add_block was never written back."""
    (s, _), path = _bdb_session(tmp_path, "fix_pin M rect 5")
    s2, _ = _session(f"open_bdb {path}", "add_block M 0 0 100 100")
    assert "M" not in s2._pin_fixes
    s3, _ = _session(f"open_bdb {path}")
    assert "M" not in s3._pin_fixes
