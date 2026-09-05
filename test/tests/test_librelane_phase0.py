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
    # Two 80x80 macros inside a 260x120 die, side by side, no overlap.
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


def test_the_def_wire_reader_finds_bus_points_with_their_layers(helpers):
    _, dw = helpers
    ents = dw.net_entries(_DEF, "mid[")
    assert list(ents) == ["mid[0]"]
    assert dw.points(ents["mid[0]"]) == [
        (100000, 41000, "met3"), (160000, 41000, "met3"),
        (160000, 41000, "met2"), (160000, 45000, "met2")]


def test_mark_fixed_changes_only_the_bus_entries(tmp_path):
    src, dst = tmp_path / "a.def", tmp_path / "b.def"
    src.write_text(_DEF)
    subprocess.run([sys.executable, str(_M / "mark_fixed.py"), str(src), str(dst)],
                   check=True, capture_output=True)
    out = dst.read_text()
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
    assert r.returncode == 1 and "OUTSIDE: mid[0] at (160.000, 45.000)" in r.stdout
