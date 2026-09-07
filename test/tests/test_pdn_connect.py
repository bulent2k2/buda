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

"""`pdn_connect.py` — what pdngen actually connected, read off its output DEF.

The audit's whole claim is that it needs no model of pdngen: the metal in the
output has already survived every cut, halo and obstruction subtraction.  So
the tests are about READING that metal exactly (a `+ SHAPE STRIPE` clause
between the width and the first point is what lost every stripe the last time
someone wrote a special-wire reader here) and about the one verdict that says
the reading is wrong — a via with no same-net cross-layer crossing, which
pdngen's rule (`Grid::getIntersections`) makes impossible.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

_T1A = Path(__file__).resolve().parents[2] / "flow" / "librelane" / "tier1a"
sys.path.insert(0, str(_T1A))
import pdn_connect as P                                    # noqa: E402
from pdn_phase import InputShape, read_lef                 # noqa: E402

DBU = 1000.0

# pe_cell: 128 x 150.  The met5 pins start at x=60 so they do NOT cross the
# cell's own met4 pins (x 10..12 and 30..32) -- otherwise every pin would be
# its own partner and the fixture could not produce a floating one.
LEF = """MACRO pe_cell
  CLASS BLOCK ;
  SIZE 128 BY 150 ;
  PIN VPWR
    USE POWER ;
    PORT
      LAYER met4 ;
        RECT 10 0 12 150 ;
      LAYER met5 ;
        RECT 60 20 128 22 ;
    END
  END VPWR
  PIN VGND
    USE GROUND ;
    PORT
      LAYER met4 ;
        RECT 30 0 32 150 ;
      LAYER met5 ;
        RECT 60 60 128 62 ;
    END
  END VGND
END pe_cell
"""

# One placed pe_cell at (100, 200).  VPWR gets both a met5 strap crossing its
# met4 pin and a met4 strap crossing its met5 pin, each with a via -> both
# connected.  VGND gets a met5 strap over its met4 pin but NO via
# (partner-no-via) and nothing at all under its met5 pin (no-partner).
DEF = """VERSION 5.8 ;
DESIGN top ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 600000 700000 ) ;
COMPONENTS 3 ;
- row_0.pe_0 pe_cell + PLACED ( 100000 200000 ) N ;
- row_0.pe_1 pe_cell + UNPLACED ;
- u_misc other_cell + PLACED ( 0 0 ) N ;
END COMPONENTS
SPECIALNETS 2 ;
- VPWR ( * VPWR )
  + ROUTED met5 1600 + SHAPE STRIPE ( 0 300000 ) ( 500000 * )
  NEW met4 1600 + SHAPE STRIPE ( 200000 0 ) ( * 600000 )
  NEW met4 0 ( 111000 300000 ) via4_5
  NEW met4 0 ( 200000 221000 ) via4_5
  NEW met4 0 ( 200000 300000 ) via4_5
  + USE POWER ;
- VGND ( * VGND )
  + ROUTED met5 1600 + SHAPE STRIPE ( 0 320000 ) ( 500000 * )
  + USE GROUND ;
END SPECIALNETS
END DESIGN
"""

LAYERS = ("met4", "met5")


def _lefs(text=LEF, tmp_path=None):
    p = (tmp_path or Path("/tmp")) / "cell.lef"
    p.write_text(text)
    return read_lef(str(p))


def _run(def_text=DEF, lef_text=LEF, layers=LAYERS, tmp_path=None, **kw):
    return P.run_audit(def_text, _lefs(lef_text, tmp_path), layers, **kw)


def _by(res, net, layer):
    return [f for f in res["findings"] if f["net"] == net and f["layer"] == layer]


# ── the special-wire reader ───────────────────────────────────────────────
def test_shape_clause_does_not_lose_the_stripe():
    """`+ SHAPE STRIPE` sits between the width and the first point.  A reader
    that starts its point walk at the token after the width reads STRIPE as a
    coordinate and loses the whole stripe — which is how every stripe a PDN
    generator writes goes missing."""
    nets, census = P.read_specialnets(DEF, DBU)
    assert census == {}
    rects = nets["VPWR"]["rects"]
    # met5 stripe centred on y=300, width 1.6 -> 299.2 .. 300.8 across the run,
    # and half a width past each end too (DEF's default extension for SPECIAL
    # wiring), so x runs -0.8 .. 500.8
    assert ("met5", -0.8, 299.2, 500.8, 300.8) in rects
    # met4 stripe centred on x=200, the `*` repeating the previous x
    assert ("met4", 199.2, -0.8, 200.8, 600.8) in rects


def test_entry_tail_is_not_read_as_a_via_name():
    """`+ USE POWER ;` follows the last point of the last wire.  Read as a
    trailing token it would look like a via name on a multi-point path and be
    censused as an unread `via_mid_path`."""
    nets, census = P.read_specialnets(DEF, DBU)
    assert "via_mid_path" not in census
    assert [v[3] for v in nets["VPWR"]["vias"]] == ["via4_5"] * 3
    assert nets["VGND"]["vias"] == []


def test_one_point_path_with_a_via_name_is_a_via_not_a_wire():
    nets, _ = P.read_specialnets(DEF, DBU)
    assert ("met4", 111.0, 300.0, "via4_5") in nets["VPWR"]["vias"]
    # and contributes no wire rectangle
    assert not any(abs(r[1] - 111.0) < 1e-9 for r in nets["VPWR"]["rects"])


def test_pin_net_mapping_comes_from_the_def():
    nets, _ = P.read_specialnets(DEF, DBU)
    assert P.pin_nets(nets) == {"VPWR": "VPWR", "VGND": "VGND"}


def test_a_pin_claimed_by_two_nets_is_refused():
    bad = DEF.replace("- VGND ( * VGND )", "- VGND ( * VPWR )")
    nets, _ = P.read_specialnets(bad, DBU)
    with pytest.raises(InputShape, match="both"):
        P.pin_nets(nets)


def test_missing_units_is_refused():
    with pytest.raises(InputShape, match="UNITS"):
        P.read_units(DEF.replace("UNITS DISTANCE MICRONS 1000 ;", ""))


# ── the verdicts ──────────────────────────────────────────────────────────
def test_the_four_verdicts(tmp_path):
    res = _run(tmp_path=tmp_path)
    assert res["macros"] == 1 and res["pins"] == 4
    assert _by(res, "VPWR", "met4")[0]["verdict"] == "connected"
    assert _by(res, "VPWR", "met5")[0]["verdict"] == "connected"
    assert _by(res, "VGND", "met4")[0]["verdict"] == "partner-no-via"
    assert _by(res, "VGND", "met5")[0]["verdict"] == "no-partner"
    # the exit-driving count is per TERMINAL: VGND's two rects are one pin
    assert res["floating"] == 1 and res["n_terminals"] == 2


def test_partner_no_via_names_the_crossing_it_found(tmp_path):
    f = _by(_run(tmp_path=tmp_path), "VGND", "met4")[0]
    assert f["via"] is None
    assert f["partner"]["kind"] == "strap" and f["partner"]["layer"] == "met5"
    assert f["partner"]["overlap"] == [2.0, 1.6]


def test_a_macro_pin_can_be_its_own_partner(tmp_path):
    """`InstanceGrid::getInstancePins` injects the macro's own pins as shapes,
    so a met4 pin crossing the same macro's met5 pin is a legal intersection.
    Move the met5 pins over the met4 ones and the crossing appears with no
    strap anywhere."""
    lef = LEF.replace("RECT 60 20 128 22 ;", "RECT 0 20 128 22 ;")
    deff = DEF.replace("""  + ROUTED met5 1600 + SHAPE STRIPE ( 0 320000 ) ( 500000 * )\n""", "")
    res = _run(deff, lef, tmp_path=tmp_path)
    f = _by(res, "VPWR", "met5")[0]
    assert f["partner"]["kind"] in ("pin", "strap")
    # VGND now has no strap at all, so its met4 pin's only hope is its own
    # met5 pin -- which starts at x=60 and does not reach x=30..32.
    assert _by(res, "VGND", "met4")[0]["verdict"] == "no-partner"


def test_via_with_no_crossing_is_a_reader_fault_and_fails_alone(tmp_path):
    """pdngen vias a same-net cross-layer overlap and nothing else, so this
    combination cannot occur in a correctly-read DEF.  It must not be counted
    as floating (it is not a routing fault) and must still fail the run."""
    deff = DEF.replace("  + USE GROUND ;",
                       "  NEW met5 0 ( 180000 261000 ) via4_5\n  + USE GROUND ;")
    res = _run(deff, tmp_path=tmp_path)
    assert res["counts"]["via-no-partner"] == 1
    # the via feeds the terminal, so VGND is no longer floating -- and the
    # reader fault must still fail the run on its own
    assert res["floating"] == 0
    out = []
    P.report(res, out=type("W", (), {"write": lambda self, s: out.append(s)})())
    assert "READER FAULT" in "".join(out)


def test_via_must_land_inside_the_pin(tmp_path):
    """A via elsewhere on the net does not connect this pin."""
    deff = DEF.replace("( 111000 300000 )", "( 400000 300000 )")
    assert _by(_run(deff, tmp_path=tmp_path), "VPWR", "met4")[0]["verdict"] == "partner-no-via"


def test_via_min_floor_is_applied(tmp_path):
    """A crossing narrower than a via is not a crossing.  The met4 VGND pin's
    overlap with its strap is 2.0 x 1.6; raise the floor past 1.6 and the
    partner is gone."""
    res = _run(tmp_path=tmp_path, via_min=1.8)
    assert _by(res, "VGND", "met4")[0]["verdict"] == "no-partner"


def test_orientation_moves_the_pins(tmp_path):
    """FN mirrors in x, so the met4 VPWR pin at cell-local x 10..12 lands at
    the far side of the 128 um cell and the via at x=111 no longer sits on it.
    FS mirrors in y, which leaves that same full-height pin exactly where it
    was — the pair is worth testing together, because a checker that mixed the
    two up would still pass on one of them."""
    fn = _by(_run(DEF.replace("( 100000 200000 ) N ;", "( 100000 200000 ) FN ;"),
                  tmp_path=tmp_path), "VPWR", "met4")[0]
    assert fn["rect"][0] == pytest.approx(100.0 + 128.0 - 12.0)
    # the die-wide met5 strap still crosses it there; the via does not follow
    assert fn["verdict"] == "partner-no-via"

    fs = _by(_run(DEF.replace("( 100000 200000 ) N ;", "( 100000 200000 ) FS ;"),
                  tmp_path=tmp_path), "VPWR", "met4")[0]
    assert fs["rect"] == [110.0, 200.0, 112.0, 350.0]
    assert fs["verdict"] == "connected"


def test_unplaced_and_lefless_components_are_reported_not_audited(tmp_path):
    res = _run(tmp_path=tmp_path)
    assert res["unplaced"] == ["row_0.pe_1"]
    assert res["missing_lef"] == {"other_cell": 1}


def test_rows_aggregate_by_cell_net_layer(tmp_path):
    """The signature the study is chasing is a per-CELL, per-NET one (only
    pe_cell's VGND floats), so the rollup has to carry all three keys."""
    deff = DEF.replace(
        "- u_misc other_cell + PLACED ( 0 0 ) N ;",
        "- row_0.pe_2 pe_cell + PLACED ( 100000 400000 ) N ;")
    res = _run(deff, tmp_path=tmp_path)
    rows = {(r["cell"], r["net"], r["layer"], r["verdict"]): r["count"] for r in res["rows"]}
    assert rows[("pe_cell", "VGND", "met5", "no-partner")] == 2
    assert res["macros"] == 2 and res["pins"] == 8


# ── the silent-pass shapes ────────────────────────────────────────────────
def test_zero_pins_audited_is_refused_not_a_pass(tmp_path):
    """A clean-looking pass over nothing is the failure this script exists to
    prevent, so each way of reaching it names itself."""
    with pytest.raises(InputShape, match="add_pdn_connect pair"):
        _run(layers=("met1", "met2"), tmp_path=tmp_path)
    with pytest.raises(InputShape, match="USE POWER"):
        _run(lef_text=LEF.replace("USE POWER ;", "USE SIGNAL ;")
                         .replace("USE GROUND ;", "USE SIGNAL ;"), tmp_path=tmp_path)
    with pytest.raises(InputShape, match=r"\( \* <pin> \)"):
        _run(DEF.replace("( * VPWR )", "( )").replace("( * VGND )", "( )"), tmp_path=tmp_path)


def test_no_specialnets_section_reads_as_no_shapes(tmp_path):
    """Not an error in itself — a DEF written before the PDN stage has none —
    but then nothing can be connected, and the census says why."""
    deff = DEF[:DEF.index("SPECIALNETS")] + "END DESIGN\n"
    nets, census = P.read_specialnets(deff, DBU)
    assert nets == {} and census == {"no_section": 1}


# ── the command ───────────────────────────────────────────────────────────
def _cli(tmp_path, def_text=DEF, lef_text=LEF, *args):
    d, l = tmp_path / "pdn.def", tmp_path / "cell.lef"
    d.write_text(def_text)
    l.write_text(lef_text)
    return subprocess.run([sys.executable, str(_T1A / "pdn_connect.py"), str(d), str(l), *args],
                          capture_output=True, text=True)


def test_cli_exit_codes_and_json(tmp_path):
    out = tmp_path / "r.json"
    r = _cli(tmp_path, DEF, LEF, "--json", str(out))
    assert r.returncode == 1, r.stderr
    assert "1 terminal(s) connected, 1 floating" in r.stdout
    res = json.loads(out.read_text())
    assert res["floating"] == 1 and res["layers"] == ["met4", "met5"]

    r = _cli(tmp_path, DEF, LEF, "--allow-floating", "1")
    assert r.returncode == 0

    r = _cli(tmp_path, DEF, LEF, "--layers", "met4")
    assert r.returncode == 2 and "exactly two layers" in r.stderr


def test_cli_refuses_a_missing_def_with_the_right_pointer(tmp_path):
    l = tmp_path / "cell.lef"
    l.write_text(LEF)
    r = subprocess.run([sys.executable, str(_T1A / "pdn_connect.py"),
                        str(tmp_path / "nope.def"), str(l)], capture_output=True, text=True)
    assert r.returncode == 2 and "pdn_phase.py is the before-the-run check" in r.stderr


# ── the LEF-only question ─────────────────────────────────────────────────
def test_self_cross_is_a_property_of_the_cell(tmp_path):
    """A macro's own pins are shapes in the same set the straps are in, so a
    pin crossing its own net on the other connect layer is connected by that
    alone.  The fixture's met5 pins start at x=60 and its met4 pins sit at
    x 10..12 / 30..32, so neither net self-crosses."""
    res = P.self_cross(_lefs(tmp_path=tmp_path), LAYERS)
    assert {r["pin"]: r["self_crossed"] for r in res["pins"]} == {"VPWR": False, "VGND": False}
    assert all(r["rects"] == {"met4": 1, "met5": 1} for r in res["pins"])


def test_self_cross_split_is_the_shape_that_explains_one_floating_net(tmp_path):
    """Widen only VPWR's met5 pin so it reaches its met4 pin.  VPWR then
    connects on any phase and VGND on none — a per-CELL asymmetry no strap
    offset can fix, which is exactly the signature to look for when one net of
    one cell floats."""
    lef = LEF.replace("RECT 60 20 128 22 ;", "RECT 0 20 128 22 ;")
    res = P.self_cross(_lefs(lef, tmp_path), LAYERS)
    got = {r["pin"]: (r["self_crossed"], r["overlap"]) for r in res["pins"]}
    assert got["VPWR"] == (True, 2.0) and got["VGND"][0] is False
    out = []
    P.report_self(res, out=type("W", (), {"write": lambda self, s: out.append(s)})())
    assert "SPLIT: pe_cell" in "".join(out)


def test_self_cross_honours_the_via_floor(tmp_path):
    lef = LEF.replace("RECT 60 20 128 22 ;", "RECT 0 20 128 22 ;")
    res = P.self_cross(_lefs(lef, tmp_path), LAYERS, via_min=2.5)
    assert {r["pin"]: r["self_crossed"] for r in res["pins"]}["VPWR"] is False


def test_self_cross_cli_takes_lefs_in_every_positional(tmp_path):
    """The mode reads no DEF, so the first positional must not be swallowed as
    one — the shape that made `--self-cross a.lef b.lef` refuse its own input."""
    a, b = tmp_path / "a.lef", tmp_path / "b.lef"
    a.write_text(LEF)
    b.write_text(LEF.replace("MACRO pe_cell", "MACRO acc_cell").replace("END pe_cell", "END acc_cell"))
    r = subprocess.run([sys.executable, str(_T1A / "pdn_connect.py"), "--self-cross",
                        str(a), str(b)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "pe_cell" in r.stdout and "acc_cell" in r.stdout


def test_no_def_and_no_self_cross_says_which_question_to_ask(tmp_path):
    l = tmp_path / "cell.lef"
    l.write_text(LEF)
    r = subprocess.run([sys.executable, str(_T1A / "pdn_connect.py")],
                       capture_output=True, text=True)
    assert r.returncode == 2 and "--self-cross" in r.stderr


# ── the three review findings, each pinned by the case that reproduced it ──
def test_a_strap_ending_beside_a_pin_still_reaches_it(tmp_path):
    """DEF's default extension for SPECIAL wiring is half the width at the
    ENDS as well as across the run (`src/bdb.cpp` applies the same expansion
    on all four sides).  Here the met4 strap's centreline STOPS at y=221,
    1.0 um into the met5 pin's 220..222 band — under the via floor.  Its end
    cap carries it to 221.8, which clears.  Without the caps this reads
    `no-partner` on metal that is really there."""
    deff = DEF.replace("NEW met4 1600 + SHAPE STRIPE ( 200000 0 ) ( * 600000 )",
                       "NEW met4 1600 + SHAPE STRIPE ( 200000 0 ) ( * 221000 )")
    f = _by(_run(deff, tmp_path=tmp_path), "VPWR", "met5")[0]
    assert f["partner"] is not None
    assert f["partner"]["overlap"] == [1.6, 1.8]     # 1.0 without the end cap


def test_a_via_of_another_layer_pair_does_not_connect_the_pin(tmp_path):
    """A DEF carries every connect statement's vias.  A met3/met4 via whose
    point falls inside a met5 pin says nothing about that pin, and taking it
    would report a floating pin as connected — the one direction this audit
    must never fail in.  pdngen writes a via on the LOWER of the two layers it
    joins, so a via of the audited pair is written on one of the two."""
    foreign = DEF.replace("  + USE GROUND ;",
                          "  NEW met3 0 ( 180000 261000 ) via3_4\n  + USE GROUND ;")
    f = _by(_run(foreign, tmp_path=tmp_path), "VGND", "met5")[0]
    assert f["via"] is None and f["verdict"] == "no-partner"

    # control: the same point on met4 IS of this pair and IS taken (and, with
    # no crossing there, is then the reader fault it should be)
    own = DEF.replace("  + USE GROUND ;",
                      "  NEW met4 0 ( 180000 261000 ) via4_5\n  + USE GROUND ;")
    g = _by(_run(own, tmp_path=tmp_path), "VGND", "met5")[0]
    assert g["via"]["via"] == "via4_5" and g["verdict"] == "via-no-partner"


def test_one_via_feeds_the_whole_terminal(tmp_path):
    """A LEF `PIN` is one node; its several `RECT`s are alternative access
    shapes connected inside the macro.  Give VPWR a second met4 access shape
    that has a crossing but no via: the rectangle is `partner-no-via` and the
    TERMINAL is still connected, because pdngen viaing one access shape and
    not the other is the normal case, not a floating pin."""
    lef = LEF.replace("""        RECT 10 0 12 150 ;
      LAYER met5 ;
        RECT 60 20 128 22 ;""",
                      """        RECT 10 0 12 150 ;
        RECT 60 0 62 150 ;
      LAYER met5 ;
        RECT 60 20 128 22 ;""")
    res = _run(lef_text=lef, tmp_path=tmp_path)
    m4 = {f["rect"][0]: f["verdict"] for f in _by(res, "VPWR", "met4")}
    assert m4 == {110.0: "connected", 160.0: "partner-no-via"}

    term = {(t["net"], t["pin"]): t for t in res["terminals"]}
    assert term[("VPWR", "VPWR")]["verdict"] == "connected"
    assert term[("VPWR", "VPWR")]["rects"] == 3 and term[("VPWR", "VPWR")]["vias"] == 2
    assert res["floating"] == 1                       # VGND's terminal, and only it


def test_terminal_rows_are_the_verdict_and_rect_rows_the_diagnostic(tmp_path):
    """The report prints both: the per-cell/net terminal rollup decides, the
    per-layer rectangle table is what tells you WHERE (`256 met4 + 256 met5`
    is the shape the study is chasing)."""
    res = _run(tmp_path=tmp_path)
    assert {(r["net"], r["verdict"]): r["count"] for r in res["terminal_rows"]} == {
        ("VGND", "partner-no-via"): 1, ("VPWR", "connected"): 1}
    assert len(res["rows"]) == 4                      # one per (net, layer, verdict)
    out = []
    P.report(res, out=type("W", (), {"write": lambda self, s: out.append(s)})())
    text = "".join(out)
    assert "terminals (a LEF pin is one node" in text and "access rectangles" in text


# ── is the NETWORK whole? ─────────────────────────────────────────────────
# The failure mode the tables above structurally cannot see: a terminal keeps
# its via while the metal it vias onto is a fragment cut off from the grid.
# Measured on the study's own N=8 run, where the FAILING DEF and the passing
# one both audited "208 terminals connected, 0 floating" (#893).
def test_a_whole_grid_reports_one_component_per_net(tmp_path):
    res = _run(tmp_path=tmp_path)
    assert res["fragments"] == 0 and res["stranded_terminals"] == 0
    vp = res["connectivity"]["VPWR"]["components"]
    assert len(vp) == 1 and vp[0]["layers"] == {"met4": 1, "met5": 1}


def test_crossing_metal_needs_a_via_to_be_one_network(tmp_path):
    """VPWR's met4 and met5 straps cross, and metal on two layers is not
    connected without a via — that is what the via is for.  Drop the crossing
    via and the net is two pieces; it is the only difference."""
    deff = DEF.replace("  NEW met4 0 ( 200000 300000 ) via4_5\n", "")
    res = _run(deff, tmp_path=tmp_path)
    comps = res["connectivity"]["VPWR"]["components"]
    assert len(comps) == 2
    assert {tuple(sorted(c["layers"])) for c in comps} == {("met4",), ("met5",)}
    assert res["fragments"] == 1


def test_a_fragment_carrying_terminals_strands_them_and_fails(tmp_path):
    """The exit has to move: the per-pin tables still say every terminal has
    its via, which is exactly the false clean result #893 hit."""
    deff = DEF.replace("  NEW met4 0 ( 200000 300000 ) via4_5\n", "")
    res = _run(deff, tmp_path=tmp_path)
    assert res["floating"] == 1                       # unchanged, VGND's
    assert res["terminal_counts"]["connected"] == 1   # VPWR still "connected"
    assert res["stranded_terminals"] >= 1             # and yet stranded
    frag = res["connectivity"]["VPWR"]["components"][1]
    assert "row_0.pe_0.VPWR" in frag["terminals"]

    r = _cli(tmp_path, deff, LEF, "--allow-floating", "9")
    assert r.returncode == 1, r.stdout
    assert "fragment(s) off the main network" in r.stdout
    r = _cli(tmp_path, deff, LEF, "--allow-floating", "9", "--allow-stranded", "9")
    assert r.returncode == 0, r.stdout


def test_a_terminal_bridging_two_components_is_reported_not_applied(tmp_path):
    """A hard macro's internal PDN really does join two fragments landing on
    one of its pins, but PSM cannot traverse an abstract LEF and does not
    credit it.  Agreeing with the verdict matters more than being physically
    complete, so the bridge is a NOTE and never a union."""
    deff = DEF.replace("  NEW met4 0 ( 200000 300000 ) via4_5\n", "")
    res = _run(deff, tmp_path=tmp_path)
    bridged = res["connectivity"]["VPWR"]["bridged_by"]
    assert [b["terminal"] for b in bridged] == ["row_0.pe_0.VPWR"]
    assert bridged[0]["components"] == [0, 1]
    assert len(res["connectivity"]["VPWR"]["components"]) == 2   # still split


def test_a_fragment_with_no_terminal_is_reported_but_strands_nothing(tmp_path):
    """Floating stub metal is worth naming and is not a stranded pin, so it
    must not fail the run on its own."""
    deff = DEF.replace("  + USE GROUND ;",
                       "  NEW met5 1600 + SHAPE STRIPE ( 0 500000 ) ( 100000 * )\n"
                       "  + USE GROUND ;")
    res = _run(deff, tmp_path=tmp_path)
    frags = res["connectivity"]["VGND"]["components"][1:]
    assert len(frags) == 1 and frags[0]["terminals"] == []
    assert res["stranded_terminals"] == 0
    r = _cli(tmp_path, deff, LEF, "--allow-floating", "9")
    assert r.returncode == 0, r.stdout


def test_a_strap_written_as_segments_is_one_component(tmp_path):
    """pdngen writes a strap as path segments that meet end to end.  A
    strict-overlap rule would fragment every strap at every join and report a
    whole grid as rubble, so touching counts."""
    deff = DEF.replace("  + ROUTED met5 1600 + SHAPE STRIPE ( 0 320000 ) ( 500000 * )",
                       "  + ROUTED met5 1600 + SHAPE STRIPE ( 0 320000 ) ( 250000 * )\n"
                       "  NEW met5 1600 + SHAPE STRIPE ( 250000 320000 ) ( 500000 * )")
    res = _run(deff, tmp_path=tmp_path)
    comps = res["connectivity"]["VGND"]["components"]
    assert len(comps) == 1 and comps[0]["shapes"] == 2
    assert res["fragments"] == 0


def test_components_are_ordered_with_the_grid_first(tmp_path):
    """`main` is the largest by area — a fragment is what is NOT the grid, so
    a one-shape stub must never be read as the network."""
    deff = DEF.replace("  + USE GROUND ;",
                       "  NEW met5 1600 + SHAPE STRIPE ( 0 500000 ) ( 100000 * )\n"
                       "  + USE GROUND ;")
    comps = _run(deff, tmp_path=tmp_path)["connectivity"]["VGND"]["components"]
    assert comps[0]["area"] > comps[1]["area"]
    assert [c["id"] for c in comps] == [0, 1]


def test_the_index_agrees_with_a_naive_reference(tmp_path):
    """`_BinIndex` replaced an exact O(N^2) scan, and an index that quietly
    drops a candidate would merge nothing and report a whole grid as rubble.
    So the partition is checked against a plain pairwise implementation on
    random geometry — a second implementation, not a re-run of the first."""
    import itertools
    import random

    def naive(sn):
        rects = list(sn["N"]["rects"])
        uf = P._UF(len(rects))
        for i, j in itertools.combinations(range(len(rects)), 2):
            if rects[i][0] == rects[j][0] and P._touch(rects[i][1:], rects[j][1:]):
                uf.union(i, j)
        for (_l, vx, vy, _n) in sn["N"]["vias"]:
            hit = [i for i, r in enumerate(rects)
                   if r[1] - P.EPS <= vx <= r[3] + P.EPS
                   and r[2] - P.EPS <= vy <= r[4] + P.EPS]
            for j in hit[1:]:
                uf.union(hit[0], j)
        g = {}
        for i in range(len(rects)):
            g.setdefault(uf.find(i), 0)
            g[uf.find(i)] += 1
        return sorted(g.values())

    rng = random.Random(7)
    for _ in range(120):
        rects, vias = [], []
        for _r in range(rng.randint(2, 30)):
            x, y = rng.uniform(0, 60), rng.uniform(0, 60)
            rects.append((rng.choice(["met4", "met5"]), round(x, 2), round(y, 2),
                          round(x + rng.uniform(0.5, 25), 2),
                          round(y + rng.uniform(0.5, 25), 2)))
        for _v in range(rng.randint(0, 12)):
            vias.append(("met4", round(rng.uniform(0, 60), 2),
                         round(rng.uniform(0, 60), 2), "v"))
        sn = {"N": {"rects": rects, "vias": vias, "pins": ["N"]}}
        got = sorted(c["shapes"] for c in P.net_components(sn, {})["N"]["components"])
        assert got == naive(sn), (rects, vias)


def test_a_foreign_pairs_via_does_not_attach_a_terminal(tmp_path):
    """A met3/met4 via sitting inside a met5 pin joins met3 to met4 and does
    nothing for that pin.  Crediting it attaches a healthy terminal to a met3
    stub and reports the stub as stranding it — a false failure, the one
    direction the exit must not move in.  The union-find still takes the via:
    it really does join that net's met3 and met4 metal, so it belongs in the
    NETWORK; only the terminal attachment is pair-filtered."""
    deff = DEF.replace(
        "  + USE GROUND ;",
        "  NEW met3 1600 + SHAPE STRIPE ( 175000 261000 ) ( 185000 * )\n"
        "  NEW met3 0 ( 180000 261000 ) via3_4\n"
        "  + USE GROUND ;")
    res = _run(deff, tmp_path=tmp_path)
    comps = res["connectivity"]["VGND"]["components"]
    assert len(comps) == 2 and comps[1]["layers"] == {"met3": 1}
    assert comps[1]["terminals"] == []               # the met5 pin is not on it
    assert res["stranded_terminals"] == 0
    r = _cli(tmp_path, deff, LEF, "--allow-floating", "9")
    assert r.returncode == 0, r.stdout


def test_a_terminal_on_two_fragments_is_one_stranded_terminal(tmp_path):
    """`--allow-stranded` is a threshold on TERMINALS, so a terminal appearing
    on two fragments must count once — otherwise a design with exactly one
    stranded terminal is rejected at `--allow-stranded 1`."""
    deff = (DEF.replace("  NEW met4 0 ( 200000 300000 ) via4_5\n", "")
               .replace("  NEW met4 0 ( 111000 300000 ) via4_5",
                        "  NEW met4 1600 + SHAPE STRIPE ( 111000 190000 ) ( * 210000 )\n"
                        "  NEW met4 0 ( 111000 300000 ) via4_5"))
    res = _run(deff, tmp_path=tmp_path)
    comps = res["connectivity"]["VPWR"]["components"]
    frags = [c for c in comps[1:] if "row_0.pe_0.VPWR" in c["terminals"]]
    assert len(frags) == 2                            # named on both
    assert res["connectivity"]["VPWR"]["stranded_terminals"] == 1   # counted once
    r = _cli(tmp_path, deff, LEF, "--allow-floating", "9", "--allow-stranded", "1")
    assert r.returncode == 0, r.stdout
