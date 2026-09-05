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

"""The phase-0 vehicle of the LibreLane study (flow/librelane/phase0/).

No EDA tool runs here, so these pin what CAN be checked without one: the
configs parse and agree with each other and with the RTL, the pin-DEF
generator writes the template LibreLane's `ApplyDEFTemplate` expects (every
RTL pin, once, on grid, inside the die), and the guide/DEF helpers the two
measurements' verdicts rest on read what they claim to.  A recipe whose
inputs are malformed would fail on the user's machine, after the Docker pull.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_P0 = _ROOT / "flow" / "librelane" / "phase0"
_M = _P0 / "measure"


def _cfg(p):
    return json.loads((_P0 / p).read_text())


def test_the_block_configs_differ_only_by_the_pin_template():
    a, b = _cfg("reg32/config.json"), _cfg("reg32/config_pins.json")
    extra = {k: b[k] for k in b if k not in a}
    assert extra == {"FP_DEF_TEMPLATE": "dir::pins.def",
                     "FP_TEMPLATE_MATCH_MODE": "strict"}, extra
    assert {k: a[k] for k in a} == {k: b[k] for k in a}


def test_the_top_names_the_blocks_final_views_and_places_both_instances():
    top = _cfg("two_reg32/config.json")
    macro = top["MACROS"]["reg32"]
    assert set(macro["instances"]) == {"u0", "u1"}
    for name, inst in macro["instances"].items():
        assert inst["orientation"] == "N" and len(inst["location"]) == 2, name
    # Two 80x80 macros inside a 250x120 die, side by side, no overlap;
    # each macro's 10 um halo reaches its die edge, so every standard cell
    # sits in the channel and no strip of rows is left for the PDN to miss.
    (x0, y0), (x1, y1) = (macro["instances"]["u0"]["location"],
                          macro["instances"]["u1"]["location"])
    die = _cfg("reg32/config.json")["DIE_AREA"]
    w = die[2] - die[0]
    assert x0 + w <= x1 and x1 + w <= top["DIE_AREA"][2]
    assert y0 + w <= top["DIE_AREA"][3]
    for key in ("gds", "lef", "nl"):
        for p in macro[key]:
            assert p.startswith("dir::../reg32/runs/phase0/final/"), p
    assert set(macro["spef"]) == {"nom_*", "min_*", "max_*"}


def _rtl_pins(v):
    text = (_P0 / v).read_text()
    pins = []
    for m in re.finditer(r"^\s*(input|output)\s+(?:wire|reg)?\s*(?:\[(\d+):(\d+)\])?\s*(\w+)",
                         text, re.M):
        hi, lo, name = m.group(2), m.group(3), m.group(4)
        if hi is None:
            pins.append(name)
        else:
            pins += [f"{name}[{i}]" for i in range(int(lo), int(hi) + 1)]
    return pins


def test_the_pin_def_places_every_rtl_pin_once_on_grid_inside_the_die():
    out = subprocess.run([sys.executable, str(_P0 / "reg32" / "gen_pins_def.py")],
                         check=True, capture_output=True, text=True).stdout
    assert "DESIGN reg32 ;" in out and "END PINS" in out
    die = int(re.search(r"DIEAREA \( 0 0 \) \( (\d+) (\d+) \)", out).group(1))
    pins = re.findall(
        r"^\s+- (\S+) \+ NET (\S+) \+ DIRECTION (INPUT|OUTPUT) \+ USE SIGNAL"
        r" \+ LAYER (met\d) \( (-?\d+) (-?\d+) \) \( (-?\d+) (-?\d+) \)"
        r" \+ PLACED \( (\d+) (\d+) \) N ;$", out, re.M)
    names = [p[0] for p in pins]
    assert sorted(names) == sorted(_rtl_pins("reg32/src/reg32.v"))
    assert len(names) == len(set(names)) == 66
    assert f"PINS {len(pins)} ;" in out
    for name, net, dirn, layer, x1, y1, x2, y2, px, py in pins:
        assert net == name
        assert dirn == ("OUTPUT" if name.startswith("q") else "INPUT")
        for v in (x1, y1, x2, y2, px, py):
            assert int(v) % 5 == 0, (name, v)            # manufacturing grid
        # The pin's rect, at its placement, lies inside the die.
        for ax, ay in ((int(px) + int(x1), int(py) + int(y1)),
                       (int(px) + int(x2), int(py) + int(y2))):
            assert 0 <= ax <= die and 0 <= ay <= die, (name, ax, ay)
    # d on the west edge, q on the east, on the H layer; clk/rst south on V.
    by = {p[0]: p for p in pins}
    assert all(by[f"d[{i}]"][8] == "0" and by[f"d[{i}]"][3] == "met3" for i in range(32))
    assert all(by[f"q[{i}]"][8] == str(die) and by[f"q[{i}]"][3] == "met3" for i in range(32))
    assert by["clk"][9] == "0" and by["clk"][3] == "met2"


@pytest.fixture
def helpers(monkeypatch):
    monkeypatch.syspath_prepend(str(_M))
    import guide_io, def_wires
    return guide_io, def_wires


def test_the_guide_reader_round_trips_and_refuses_the_wrong_shape(tmp_path, helpers):
    guide_io, _ = helpers
    src = tmp_path / "a.guide"
    src.write_text("mid[0]\n(\n1000 2000 3000 2500 met3\n3000 2000 3400 4000 met2\n)\n"
                   "clk\n(\n0 0 10 10 met1\n)\n")
    g = guide_io.read_guides(src)
    assert list(g) == ["mid[0]", "clk"] and len(g["mid[0]"]) == 2
    assert g["mid[0]"][0] == (1000, 2000, 3000, 2500, "met3")
    dst = tmp_path / "b.guide"
    guide_io.write_guides(dst, g)
    assert guide_io.read_guides(dst) == g
    bad = tmp_path / "bad.guide"
    bad.write_text("mid[0]\n(\n1000 2000 met3\n)\n")
    with pytest.raises(ValueError, match="bad.guide:3"):
        guide_io.read_guides(bad)


_DEF = """VERSION 5.8 ;
NETS 2 ;
- mid[0] ( u0 q[0] ) ( u1 d[0] ) + USE SIGNAL
  + ROUTED met3 ( 100000 41000 ) ( 160000 41000 )
    NEW met2 ( 160000 41000 ) ( 160000 45000 ) ;
- clk ( PIN clk ) + USE SIGNAL
  + ROUTED met2 ( 5000 0 ) ( 5000 9000 ) ;
END NETS
"""


def test_the_guide_reader_keys_by_net_name_and_writes_the_files_spelling(tmp_path, helpers):
    """OpenROAD's write_guides spelled the 32 bus nets `mid\\[0\\]` and the
    port nets `d[0]` in the same file (two_reg32, 2026-09-05), so the reader
    keys by the plain name -- the key def_wires uses, so the containment
    check can pair a net's guides with its wiring -- and the writer gives
    each name back as the file spelled it, escaping `[`/`]` only for a name
    it never read (what OpenROAD did for every non-port net)."""
    gio, _ = helpers
    src = tmp_path / "a.guide"
    src.write_text("mid\\[0\\]\n(\n0 0 10 10 met3\n)\nd[0]\n(\n0 0 5 5 met2\n)\n")
    g = gio.read_guides(src)
    assert list(g) == ["mid[0]", "d[0]"]
    assert g.spelling == {"mid[0]": "mid\\[0\\]", "d[0]": "d[0]"}
    dst = tmp_path / "b.guide"
    gio.write_guides(dst, {"mid[0]": g["mid[0]"], "d[0]": g["d[0]"], "new[1]": [(1, 1, 2, 2, "met1")]},
                     g.spelling)
    assert dst.read_text().splitlines()[::4] == ["mid\\[0\\]", "d[0]", "new\\[1\\]"]
    assert gio.read_guides(dst)["mid[0]"] == g["mid[0]"]     # round trip by name


def test_the_def_wire_reader_finds_bus_points_with_their_layers(helpers):
    _, dw = helpers
    ents = dw.net_entries(_DEF, "mid[")
    assert list(ents) == ["mid[0]"]
    # One item per DEF path: `NEW` starts a new one, so no segment is ever
    # drawn across it -- the shape check_inside walks segment by segment.
    assert dw.paths(ents["mid[0]"]) == [
        ("met3", [(100000, 41000), (160000, 41000)]),
        ("met2", [(160000, 41000), (160000, 45000)])]
    assert dw.points(ents["mid[0]"]) == [
        (100000, 41000, "met3"), (160000, 41000, "met3"),
        (160000, 41000, "met2"), (160000, 45000, "met2")]
    # DEF's shorthand: `*` repeats the previous coordinate, a trailing
    # extension value and a via name after a point carry no geometry.
    e = "- x + ROUTED met3 ( 100 200 ) ( * 300 0 ) M3M4_PR NEW met4 ( 100 300 ) ( 500 * ) ;"
    assert dw.paths(e) == [("met3", [(100, 200), (100, 300)]),
                           ("met4", [(100, 300), (500, 300)])]
    with pytest.raises(ValueError, match="no previous point"):
        dw.paths("- x + ROUTED met3 ( * 300 ) ;")
    # A via's patch metal, `RECT ( dx1 dy1 dx2 dy2 )` relative to the point
    # before it, is not a point on the path (the first real DEF: read as one,
    # it became a wire to (-0.39, -0.15) um, outside every guide).
    e = "- x + ROUTED met3 ( 100 200 ) ( 100 300 ) NEW met3 ( 100 300 ) RECT ( -390 -150 0 150 ) ;"
    assert dw.paths(e) == [("met3", [(100, 200), (100, 300)]), ("met3", [(100, 300)])]
    # ... but it IS metal, resolved against that point (Codex #877).
    assert dw.patches(e) == [("met3", -290, 150, 100, 450)]
    with pytest.raises(ValueError, match="RECT with no previous point"):
        dw.paths("- x + ROUTED met3 RECT ( 0 0 1 1 ) ;")
    assert dw.paths("- x ( u0 q[0] ) ( u1 d[0] ) + USE SIGNAL ;") == []   # unrouted
    # OpenROAD writes a routed DEF with the names DEF-escaped (`mid\\[0\\]`),
    # and its guide file with them plain (`mid[0]`).  The reader keys on the
    # plain name, so a `mid[` prefix finds the bus in BOTH files -- it found
    # 0 of 32 in the first real DEF -- while the entry text is kept verbatim
    # for mark_fixed's byte-preserving rewrite.
    esc = _DEF.replace("- mid[0]", "- mid\\[0\\]")
    ents = dw.net_entries(esc, "mid[")
    assert list(ents) == ["mid[0]"]
    assert ents["mid[0]"].lstrip().startswith("- mid\\[0\\]")
    assert dw.net_entries(esc, "mid\\[") == {}


def test_mark_fixed_changes_only_the_bus_entries(tmp_path):
    src, dst = tmp_path / "a.def", tmp_path / "b.def"
    src.write_text(_DEF)
    subprocess.run([sys.executable, str(_M / "mark_fixed.py"), str(src), str(dst)],
                   check=True, capture_output=True)
    out = dst.read_text()
    # A bus net with NO wiring is refused, not passed through: its untouched
    # entry would later compare "unchanged" and count as FIXED wiring the
    # routers honoured, when there was never any (Codex #875 P1).
    unrouted = tmp_path / "u.def"
    unrouted.write_text(_DEF.replace("- clk (", "- mid[1] ( u0 q[1] ) ( u1 d[1] ) + USE SIGNAL ;\n- clk ("))
    r = subprocess.run([sys.executable, str(_M / "mark_fixed.py"), str(unrouted), str(tmp_path / "v.def")],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "NO routed wiring" in r.stderr and "mid[1]" in r.stderr
    assert not (tmp_path / "v.def").exists()
    assert "- mid[0]" in out and "+ FIXED met3" in out
    assert out.count("+ ROUTED") == 1 and "- clk ( PIN clk ) + USE SIGNAL\n  + ROUTED" in out
    # compare_bus_wires: identical before/after passes, a moved wire fails.
    r = subprocess.run([sys.executable, str(_M / "compare_bus_wires.py"), str(dst), str(dst)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    moved = tmp_path / "c.def"
    moved.write_text(out.replace("( 160000 45000 )", "( 160000 46000 )"))
    r = subprocess.run([sys.executable, str(_M / "compare_bus_wires.py"), str(dst), str(moved)],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "CHANGED: mid[0]" in r.stdout


def test_check_inside_passes_within_guides_and_names_the_offender(tmp_path):
    d = tmp_path / "g.def"
    d.write_text(_DEF)
    g = tmp_path / "bus.guide"
    g.write_text("mid[0]\n(\n99000 40000 161000 42000 met3\n159000 40000 161000 46000 met2\n)\n")
    r = subprocess.run([sys.executable, str(_M / "check_inside.py"), str(d), str(g)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    g.write_text("mid[0]\n(\n99000 40000 161000 42000 met3\n)\n")   # met2 leg uncovered
    r = subprocess.run([sys.executable, str(_M / "check_inside.py"), str(d), str(g)],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "OUTSIDE: mid[0] point (160.000, 45.000) on met2" in r.stdout
    # A RECT patch is metal too: one poking out of the guide is a miss even
    # when every path point is inside (Codex #877).
    g.write_text("mid[0]\n(\n99000 40000 161000 42000 met3\n159000 40000 161000 46000 met2\n)\n")
    d.write_text(_DEF.replace("( 160000 45000 ) ;", "( 160000 45000 ) NEW met3 ( 160000 41000 ) RECT ( -200 -100 3000 100 ) ;"))
    r = subprocess.run([sys.executable, str(_M / "check_inside.py"), str(d), str(g)],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "OUTSIDE: mid[0] patch (159.800, 40.900)-(163.000, 41.100) on met3 [corridor]" in r.stdout


def test_check_inside_exit_code_is_the_verdict_at_a_stated_threshold(tmp_path):
    """The recipe's pass is "98.1 % inside" on a real routed DEF, and the
    strict rule exits 1 on it -- a script no harness can gate on.  With
    `--max-outside-pct` the exit code IS the verdict: the strict measure
    (wire length outside its own layer's boxes, as a share of the bus) at
    or under the number passes, over it fails, and an unrouted bit fails
    at any threshold, since no threshold makes missing wire a pass."""
    d = tmp_path / "g.def"
    d.write_text(_DEF)
    g = tmp_path / "bus.guide"
    # The met3 run 100..160 is covered; the 5 um met2 leg at x=160 is not:
    # 4 of 64 um = 6.2 % of the wire outside its own layer's boxes.
    g.write_text("mid[0]\n(\n99000 40000 161000 42000 met3\n)\n")
    run = lambda *extra: subprocess.run([sys.executable, str(_M / "check_inside.py"), str(d), str(g), *extra],
                                        capture_output=True, text=True)
    r = run()
    assert r.returncode == 1 and "OUTSIDE:" in r.stdout            # strict: any miss fails
    r = run("--max-outside-pct", "10")
    assert r.returncode == 0, r.stdout
    assert "PASS: 6.2% of the bus wire outside its own layer's guides, threshold 10%" in r.stdout
    r = run("--max-outside-pct", "5")
    assert r.returncode == 1 and "FAIL: 6.2%" in r.stdout
    r = run("--max-outside-pct", "0")                               # 0 is a real threshold, not "unset"
    assert r.returncode == 1 and "FAIL: 6.2%" in r.stdout
    r = run("--max-outside-pct", "120")
    assert r.returncode == 1 and "a percentage, 0..100" in r.stderr
    # An unrouted bit fails at any threshold.
    head, tail = _DEF.index("+ ROUTED met3"), _DEF.index("- clk")
    d.write_text(_DEF[:head] + ";\n" + _DEF[tail:])              # mid[0]: an entry with no wiring
    r = run("--max-outside-pct", "100")
    assert r.returncode == 1 and "UNROUTED: mid[0]" in r.stdout and "1 unrouted" in r.stdout


def test_the_measure_scripts_compile_without_escape_warnings():
    """`mid\\[0\\]` in a non-raw docstring is an invalid escape -- a
    DeprecationWarning today and an error in a later Python.  Every
    measure script must compile clean under -W error."""
    for f in sorted(_M.glob("*.py")):
        r = subprocess.run([sys.executable, "-W", "error", "-c",
                            f"compile(open({str(f)!r}).read(), {str(f)!r}, 'exec')"],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"{f.name}: {r.stderr}"


def test_check_inside_refuses_an_unrouted_net_and_a_gap_crossing_segment(tmp_path):
    """The two vacuous passes Codex #875 named, each pinned as a refusal.

    An unrouted bus bit still has a `- mid[k] ... ;` entry, so the entry's
    existence proves nothing; and a segment whose endpoints each sit in a
    guide box while its middle crosses a gap is metal outside the corridor,
    whatever its vertices say.
    """
    d = tmp_path / "g.def"
    d.write_text(_DEF.replace("- clk (", "- mid[1] ( u0 q[1] ) ( u1 d[1] ) + USE SIGNAL ;\n- clk ("))
    g = tmp_path / "bus.guide"
    g.write_text("mid[0]\n(\n99000 40000 161000 42000 met3\n159000 40000 161000 46000 met2\n)\n")
    r = subprocess.run([sys.executable, str(_M / "check_inside.py"), str(d), str(g)],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "1 unrouted" in r.stdout and "UNROUTED: mid[1]" in r.stdout

    # Both endpoints of the met3 run are inside boxes; the middle 120..140 is not.
    d.write_text(_DEF)
    g.write_text("mid[0]\n(\n99000 40000 120000 42000 met3\n140000 40000 161000 42000 met3\n"
                 "159000 40000 161000 46000 met2\n)\n")
    r = subprocess.run([sys.executable, str(_M / "check_inside.py"), str(d), str(g)],
                       capture_output=True, text=True)
    assert r.returncode == 1, r.stdout
    assert "segment (100.000, 41.000)-(160.000, 41.000) on met3, uncovered 120.500..139.500" in r.stdout
    # ...and a box on the WRONG layer over that stretch does not cover it.
    g.write_text("mid[0]\n(\n99000 40000 120000 42000 met3\n140000 40000 161000 42000 met3\n"
                 "119000 40000 141000 42000 met4\n159000 40000 161000 46000 met2\n)\n")
    r = subprocess.run([sys.executable, str(_M / "check_inside.py"), str(d), str(g)],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "uncovered 120.500..139.500" in r.stdout


def test_read_resolved_matches_the_corner_against_wildcard_keys(tmp_path):
    """LibreLane keys TECH_LEFS by corner WILDCARD (`nom_*`) while
    DEFAULT_CORNER is concrete (`nom_tt_025C_1v80`); an exact lookup raised
    KeyError inside a process substitution and surfaced as an unbound shell
    variable at the docker line (Codex #875 P1)."""
    rj = tmp_path / "resolved.json"
    rj.write_text(json.dumps({
        "DESIGN_NAME": "two_reg32", "RT_MIN_LAYER": "met1", "RT_MAX_LAYER": "met5",
        "DEFAULT_CORNER": "nom_tt_025C_1v80",
        "TECH_LEFS": {"nom_*": "/pdk/nom.tlef", "min_*": "/pdk/min.tlef", "max_*": "/pdk/max.tlef"},
        "CELL_LEFS": ["/pdk/a.lef", "/pdk/b.lef"]}))
    r = subprocess.run([sys.executable, str(_M / "read_resolved.py"), str(rj)],
                       check=True, capture_output=True, text=True)
    assert r.stdout.splitlines() == ["met1", "met5", "/pdk/nom.tlef", "/pdk/a.lef /pdk/b.lef", "two_reg32"]
    rj.write_text(json.dumps({"DESIGN_NAME": "x", "RT_MIN_LAYER": "met1", "RT_MAX_LAYER": "met5",
                              "DEFAULT_CORNER": "typ", "TECH_LEFS": {"nom_*": "a", "min_*": "b"},
                              "CELL_LEFS": []}))
    r = subprocess.run([sys.executable, str(_M / "read_resolved.py"), str(rj)],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "matches 0 of the keys" in r.stderr


def test_a_shifted_corridor_keeps_terminal_boxes_and_adds_risers(tmp_path):
    """`--dy` moves only the channel -- the metal over the macros stays where
    the pins are (Codex #875 P2) -- and bridges each CUT with a riser on the
    vertical layer next to the cut box, two gcell columns wide, so the guide
    stays one connected set of boxes.  All of it in gcell units, which is
    what a guide is to the router (DRT-0229 on anything else, measured)."""
    src, dst = tmp_path / "all.guide", tmp_path / "bus.guide"
    sys.path.insert(0, str(_M))
    import guide_io
    # The real shape: OpenROAD's boxes are gcell-aligned (6.9 um) and merged
    # along a run, so the channel run is ONE box overhanging both macro edges
    # (89.7..165.6 for a 90..160 channel).  The channel snaps INWARD to the
    # gcell grid (96.6..158.7), the overhangs stay, the inside shifts by a
    # whole gcell, and a met4 riser straddles each cut.
    src.write_text("mid[0]\n(\n89700 41400 165600 48300 met3\n)\nclk\n(\n0 0 6900 6900 met1\n)\n")
    r = subprocess.run([sys.executable, str(_M / "extract_bus_guides.py"), str(src), str(dst), "--dy", "6.9"],
                       check=True, capture_output=True, text=True)
    assert "1 channel piece(s) shifted 6.9 um in y within x=96.6..158.7 (1 of them clipped" in r.stdout
    assert "2 riser(s) at the cuts" in r.stdout
    g = guide_io.read_guides(dst)
    assert list(g) == ["mid[0]"]                       # clk is not a bus net
    assert set(g["mid[0]"]) == {
        (89700, 41400, 96600, 48300, "met3"), (158700, 41400, 165600, 48300, "met3"),   # kept
        (96600, 48300, 158700, 55200, "met3"),                                          # shifted
        (89700, 41400, 103500, 55200, "met4"), (151800, 41400, 165600, 55200, "met4")}  # risers
    # A box wholly inside the channel moves with its neighbours and gets no
    # riser; a met5 run is bridged on met4 (met2 is not adjacent to met5).
    src.write_text("mid[0]\n(\n89700 41400 165600 48300 met5\n103500 41400 110400 62100 met4\n)\n")
    subprocess.run([sys.executable, str(_M / "extract_bus_guides.py"), str(src), str(dst), "--dy", "6.9"],
                   check=True, capture_output=True, text=True)
    rects = set(guide_io.read_guides(dst)["mid[0]"])
    assert (103500, 48300, 110400, 69000, "met4") in rects           # shifted, no riser of its own
    assert (89700, 41400, 103500, 55200, "met4") in rects            # the met5 run's riser, on met4
    assert not [r for r in rects if r[4] == "met2"]
    # A shift that is not a whole gcell is refused (the box would straddle
    # the gcell it left), and so is one that would move nothing.
    r = subprocess.run([sys.executable, str(_M / "extract_bus_guides.py"), str(src), str(dst), "--dy", "3"],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "not a whole number of gcells" in r.stderr
    r = subprocess.run([sys.executable, str(_M / "extract_bus_guides.py"), str(src), str(dst),
                        "--dy", "6.9", "--channel", "300", "400"], capture_output=True, text=True)
    assert r.returncode == 1 and "nothing to shift" in r.stderr
