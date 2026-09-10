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

"""The DEF orientation transform has THREE implementations.  This measures
their agreement instead of assuming it.

`tools/def_orient.py`'s `DEF_ORIENT_POINT` is the canonical table (#915).  The
other two are there for reasons neither can give up:

  * `def_orient_xf` in `src/bdb.cpp` -- C++, inside the importer.
  * `_ORIENT` in `flow/librelane/tier1a/pdn_phase.py` -- a stdlib-only script
    meant to run inside a LibreLane run tree, so it cannot import from the
    repo's `tools/` without giving up being self-contained.

#912 is why this file exists: a FOURTH copy of this transform was wrong for a
day, in the direction no test could see, because agreement between copies was
argued rather than measured.  The lesson is the one CLAUDE.md already records
for `_split_args` -- a twin in another language (or another deployment unit)
needs its agreement MEASURED.

The BDB convention is a DIFFERENT transform with its own twins
(`src/orient_rect.py` <-> `orient_map` in `src/topology.cpp`); it mirrors about
X first and so DISAGREES with DEF on all four flips.  `def_orient_to_bdb` in
`bdb.cpp` is the permutation.  Nothing here should be read as covering it.
"""

import sys
import textwrap
from pathlib import Path

import pytest

import buda
from def_orient import DEF_ORIENT_POINT

_T1A = Path(__file__).resolve().parents[2] / "flow" / "librelane" / "tier1a"
if str(_T1A) not in sys.path:
    sys.path.insert(0, str(_T1A))

ORIENTS = ("N", "S", "FN", "FS", "W", "E", "FW", "FE")

# Probe points chosen so no two table entries agree on all of them: asymmetric
# in |x| vs |y| (else a swap reads as identity), negative on each axis
# independently (else a reflection hides), and the degenerate origin.
_PROBES = ((0, 0), (7, 3), (-7, 3), (7, -3), (-7, -3), (3, 7), (1, 0), (0, 1))


def test_the_canonical_table_distinguishes_all_eight_orientations():
    """A prerequisite for the two agreement tests below: if the probes could
    not tell two orientations apart, a copy that confused them would pass."""
    seen = {}
    for o in ORIENTS:
        key = tuple(DEF_ORIENT_POINT[o](x, y) for x, y in _PROBES)
        assert key not in seen, f"{o} indistinguishable from {seen[key]}"
        seen[key] = o


def test_pdn_phases_orientation_table_is_the_canonical_one():
    """`pdn_phase._ORIENT` is a verbatim third copy.  It agrees today; this is
    what will say so tomorrow."""
    import pdn_phase

    assert set(pdn_phase._ORIENT) == set(DEF_ORIENT_POINT), "token set differs"
    for o in ORIENTS:
        for (x, y) in _PROBES:
            assert pdn_phase._ORIENT[o](x, y) == DEF_ORIENT_POINT[o](x, y), (
                f"{o} at ({x},{y}): pdn_phase {pdn_phase._ORIENT[o](x, y)} "
                f"vs canonical {DEF_ORIENT_POINT[o](x, y)}")


_PIN_DEF = """\
VERSION 5.8 ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 128000 150000 ) ;
PINS {n} ;
{rows}
END PINS
END DESIGN
"""

# ASYMMETRIC on both axes AND clear of the origin on x, so (a) each of the
# eight lands on its own box and (b) the bbox cannot be right by accident if
# the placed point is unioned in -- see the seeding test below.
_RECT = (200, -1500, 2000, 500)
_PX, _PY = 40000, 24000


def _import_pins(tmp_path, orients, rect=_RECT, px=_PX, py=_PY):
    rows = "\n".join(
        f"  - p_{o} + NET p_{o} + DIRECTION INPUT + USE SIGNAL"
        f" + LAYER met3 ( {rect[0]} {rect[1]} ) ( {rect[2]} {rect[3]} )"
        f" + PLACED ( {px} {py} ) {o} ;" for o in orients)
    (tmp_path / "e.lef").write_text("VERSION 5.8 ;\nEND LIBRARY\n")
    (tmp_path / "p.def").write_text(_PIN_DEF.format(n=len(orients), rows=rows))
    db = buda.BDB(str(tmp_path / "p.bdb"))
    db.import_def_lef(str(tmp_path / "p.def"), str(tmp_path / "e.lef"))
    return {c.name: tuple(round(v, 4) for v in (c.x1, c.y1, c.x2, c.y2))
            for c in db.all_components()}


def _predict(orient, rect=_RECT, px=_PX, py=_PY):
    """The imported bbox, DERIVED from the canonical table -- so the C++ is
    measured against that table rather than against a hand-typed number that
    once matched it."""
    f = DEF_ORIENT_POINT[orient]
    x1, y1, x2, y2 = rect
    pts = [f(x1, y1), f(x2, y1), f(x1, y2), f(x2, y2)]
    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
    return tuple(round(v / 1000.0, 4) for v in
                 (px + min(xs), py + min(ys), px + max(xs), py + max(ys)))


def test_the_importers_pin_transform_is_the_canonical_table(tmp_path):
    got = _import_pins(tmp_path, ORIENTS)
    assert len({_predict(o) for o in ORIENTS}) == 8, "probe rect is degenerate"
    for o in ORIENTS:
        assert got[f"PIN/p_{o}"] == _predict(o), (o, got[f"PIN/p_{o}"])


def test_a_port_rect_clear_of_its_origin_does_not_stretch_to_it(tmp_path):
    """The bbox is the PORT METAL's extent.  A `PORT` rect is origin-RELATIVE
    and need not contain the origin, and the box used to be SEEDED with the
    placed point, so an off-origin rect had its extent stretched out to the
    point -- claiming metal the DEF never drew.

    Invisible on every DEF here: `flow/ariane133`'s rects straddle their
    origin (+-70 DBU) and `emit_pin_def` writes rects anchored at it, so only
    somebody else's DEF reaches it -- the same blind spot as #912 itself.
    Found by deriving the expectation above from the table instead of typing
    it, which is the whole point of doing that."""
    # 200..2000 DBU on x: 1800 wide, and 2000 if the origin is unioned in.
    got = _import_pins(tmp_path, ["N"])["PIN/p_N"]
    assert round(got[2] - got[0], 4) == 1.8, got
    assert got[0] == 40.2, got          # starts AT the rect, not at the pin

    # A pin with NO port rect keeps the placed point as its degenerate box --
    # that is what the seed was for, and it must survive.
    (tmp_path / "b.lef").write_text("VERSION 5.8 ;\nEND LIBRARY\n")
    (tmp_path / "b.def").write_text(_PIN_DEF.format(
        n=1, rows="  - bare + NET bare + DIRECTION INPUT + USE SIGNAL"
                  f" + PLACED ( {_PX} {_PY} ) N ;"))
    db = buda.BDB(str(tmp_path / "b.bdb"))
    db.import_def_lef(str(tmp_path / "b.def"), str(tmp_path / "b.lef"))
    (c,) = [c for c in db.all_components() if c.name == "PIN/bare"]
    assert (c.x1, c.y1, c.x2, c.y2) == (40.0, 24.0, 40.0, 24.0)


def test_where_the_two_conventions_actually_diverge(tmp_path):
    """Guards the docstring's claim that this file covers DEF only -- and pins
    WHERE the two differ, which is not where the prose used to say.

    Measured, in the point form and the box form alike: they differ on the two
    DIRECTION-PRESERVING flips (`FN`, `FS`) and AGREE exactly on the two
    axis-SWAPPING ones (`FW` is the plain transpose `(y, x)` under both, and
    `FE` is `(h-y, w-x)` under both).  `tools/def_orient.py` said "all four
    flips"; its own parenthetical example was `FN`, which is one of the two
    that do.

    The probe rect is off-centre on BOTH axes -- a rect centred on either one
    makes that axis's reflection a no-op and three of the eight then coincide,
    which is how a rect y-centred in its box first produced the wrong answer
    here."""
    import orient_rect

    R, W, H = (1.0, 0.5, 4.0, 2.0), 10.0, 6.0

    def def_box(o):
        f = DEF_ORIENT_POINT[o]
        pts = [f(x, y) for x in (R[0], R[2]) for y in (R[1], R[3])]
        cs = [f(x, y) for x in (0, W) for y in (0, H)]
        ox, oy = min(c[0] for c in cs), min(c[1] for c in cs)
        return (round(min(p[0] for p in pts) - ox, 3),
                round(min(p[1] for p in pts) - oy, 3),
                round(max(p[0] for p in pts) - ox, 3),
                round(max(p[1] for p in pts) - oy, 3))

    differ = [o for o in ORIENTS if orient_rect.oxf_rect(o, *R, W, H) != def_box(o)]
    assert differ == ["FN", "FS"], differ

    # and as POINT transforms, the same two and only those
    def bdb_point(o, x, y):
        sw, rx, ry = orient_rect.ORIENT_MAPS[o]
        if sw:
            x, y = y, x
        return (-x if rx else x, -y if ry else y)

    pdiffer = [o for o in ORIENTS
               if bdb_point(o, 7, 3) != DEF_ORIENT_POINT[o](7, 3)]
    assert pdiffer == ["FN", "FS"], pdiffer


_RT_LEF = ("MACRO m\n  SIZE 20 BY 10 ;\n  PIN A\n    DIRECTION INPUT ;\n"
           "    PORT\n      LAYER met1 ;\n        RECT 1 1 2 2 ;\n    END\n"
           "  END A\nEND m\n")

_RT_DEF = """\
VERSION 5.8 ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 128000 150000 ) ;
COMPONENTS 1 ;
  - u1 m + PLACED ( 60000 60000 ) N ;
END COMPONENTS
PINS 1 ;
  - vout + NET vout + DIRECTION OUTPUT + USE SIGNAL
{layers}
    + PLACED ( 40000 24000 ) N ;
END PINS
NETS 1 ;
  - vout ( PIN vout ) ( u1 A ) ;
END NETS
END DESIGN
"""


def _port_round_trip(tmp_path, rects, tag):
    """Import a DEF whose one die port carries `rects` (DBU, origin-relative),
    export to GDS, re-import.  Returns the pin position, the port component
    bbox, the skipped-label count and the pin count after the round trip."""
    if rects and not isinstance(rects[0], (list, tuple)):
        rects = [rects]
    layers = "\n".join(f"    + LAYER met3 ( {r[0]} {r[1]} ) ( {r[2]} {r[3]} )"
                        for r in rects)
    (tmp_path / f"{tag}.lef").write_text(_RT_LEF)
    (tmp_path / f"{tag}.def").write_text(_RT_DEF.format(layers=layers))
    db = buda.BDB(str(tmp_path / f"{tag}a.bdb"))
    db.import_def_lef(str(tmp_path / f"{tag}.def"), str(tmp_path / f"{tag}.lef"))
    pin = next(p for p in db.all_pins() if p.pin_name == "vout")
    (bb,) = [(c.x1, c.y1, c.x2, c.y2) for c in db.all_components()
             if c.name.startswith("PIN/")]
    db.export_gds(str(tmp_path / f"{tag}.gds"))
    db2 = buda.BDB(str(tmp_path / f"{tag}b.bdb"))
    st = db2.import_gds(str(tmp_path / f"{tag}.gds"), [63])
    return ((round(pin.px, 4), round(pin.py, 4)), bb,
            st.n_labels_skipped, len(db2.all_pins()))


def test_a_die_ports_pin_sits_on_its_metal_not_its_placed_origin(tmp_path):
    """A macro pin is placed at the centroid of its own RECTs
    (`LefPinDef::centroid`); a die port used its PLACED origin, which is not on
    its metal at all once the `PORT` rect is clear of that origin.

    That became load-bearing the moment the bbox stopped stretching to the
    origin (the test above): `export_gds` writes the net label at the pin's
    stored position and `import_gds` gives a label to the component CONTAINING
    it, so an off-metal position drops the port's pin on a DEF -> BDB -> GDS ->
    BDB round trip.  Reported by Codex on #923 and reproduced before fixing --
    the label came back `outside every component -- skipped (nearest:
    'PIN/vout', 0.2 um away)`.

    The bbox CENTRE is used rather than a centroid of rect centres because the
    property needed is a point inside the port component, which the centre is
    by construction and a centroid of disjoint rects need not be."""
    # The PLACED point is on the rect here, so it stands: that is every DEF in
    # this tree, and why the corpus cannot see any of this.
    pos, _bb, skipped, npins = _port_round_trip(
        tmp_path, (-1000, -150, 1000, 150), "sym")
    assert pos == (40.0, 24.0) and skipped == 0 and npins == 2

    # clear of its origin on x: the pin moves onto its metal and survives.
    pos, _bb, skipped, npins = _port_round_trip(
        tmp_path, (200, -1500, 2000, 500), "off")
    assert pos == (41.1, 23.5), pos      # the rect's centre, not the placed point
    assert skipped == 0 and npins == 2


def _on_metal(pos, rects_um):
    return any(x1 <= pos[0] <= x2 and y1 <= pos[1] <= y2
               for (x1, y1, x2, y2) in rects_um)


def test_a_multi_rect_ports_pin_is_on_a_rect_and_never_in_the_gap(tmp_path):
    """The bbox MIDPOINT is not a pin.  For disjoint rects it can fall in the
    gap between them, on no metal at all -- which
    `test_a_die_ports_pin_survives_the_merge_unchanged` already recorded from an
    earlier review of this same reasoning (Codex P2 on #650), and which the
    first cut of this fix walked straight into: rects at 39.8..40.2 and
    60.0..60.4 um put the pin at 50.1, in open die.

    So: the PLACED point wins whenever it lies on a rect -- the DEF's own
    statement, and nothing moves where nothing was wrong -- and otherwise the
    LARGEST rect's centre, which is on metal by construction and inside the
    component bbox because the bbox is the union.  Reported by Codex on #923."""
    # (a) two disjoint rects, the placed point on the FIRST: it stands, and the
    #     bbox midpoint (50.1) is not used.
    pos, bb, skipped, npins = _port_round_trip(
        tmp_path, [(-200, -200, 200, 200), (20000, -200, 20400, 200)], "gap")
    metal = [(39.8, 23.8, 40.2, 24.2), (60.0, 23.8, 60.4, 24.2)]
    assert pos == (40.0, 24.0), pos
    assert _on_metal(pos, metal), (pos, metal)
    assert pos[0] != 50.1                      # the midpoint of that bbox
    assert bb[0] <= pos[0] <= bb[2] and bb[1] <= pos[1] <= bb[3]
    assert skipped == 0 and npins == 2

    # (b) two disjoint rects, the placed point on NEITHER: the LARGER rect's
    #     centre, still on metal and still inside the component.
    pos, bb, skipped, npins = _port_round_trip(
        tmp_path, [(5000, -200, 5400, 200), (20000, -2000, 24000, 2000)], "big")
    metal = [(45.0, 23.8, 45.4, 24.2), (60.0, 22.0, 64.0, 26.0)]
    assert pos == (62.0, 24.0), pos             # centre of the 4 x 4 um rect
    assert _on_metal(pos, metal), (pos, metal)
    assert bb[0] <= pos[0] <= bb[2] and bb[1] <= pos[1] <= bb[3]
    assert skipped == 0 and npins == 2

    # (c) the placed point on the SMALLER rect, a much larger one elsewhere.
    #     This is the case that separates the two halves of the rule: "keep the
    #     placed point when it is on metal" answers (40, 24) and "always the
    #     largest rect" answers (62, 24).  Cases (a) and (b) above cannot tell
    #     them apart -- (a)'s two rects have EQUAL area, so first-wins returns
    #     the placed point by coincidence -- which a mutation run is how I found
    #     out rather than assumed.
    pos, bb, skipped, npins = _port_round_trip(
        tmp_path, [(-200, -200, 200, 200), (20000, -2000, 24000, 2000)], "small")
    metal = [(39.8, 23.8, 40.2, 24.2), (60.0, 22.0, 64.0, 26.0)]
    assert pos == (40.0, 24.0), pos             # the DEF's own point, kept
    assert _on_metal(pos, metal), (pos, metal)
    assert bb[0] <= pos[0] <= bb[2] and bb[1] <= pos[1] <= bb[3]
    assert skipped == 0 and npins == 2
