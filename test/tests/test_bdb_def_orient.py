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

"""DEF/LEF import records instance orientation (component.orient, v13).

`import_def_lef` used to discard the DEF `COMPONENTS` orientation token and
apply the LEF `SIZE` axis-aligned regardless — so a rotated macro got the
wrong bbox and no orientation. It now maps the DEF token to BDB's orient
convention and swaps the placed dims for 90/270, so a DEF-placed design
exports to GDSII with orientation intact.

A die `PIN` carries an orientation too, and it transforms the pin's `PORT`
geometry exactly as a component's transforms the cell's (#912).
"""

import textwrap

import buda

_LEF = "MACRO m\n  SIZE 100 BY 40 ;\nEND m\n"


def _def(*placements):
    rows = "\n".join(f"  - {n} m + PLACED ( {x} {y} ) {o} ;"
                     for n, x, y, o in placements)
    return textwrap.dedent("""\
        VERSION 5.8 ;
        UNITS DISTANCE MICRONS 1 ;
        DIEAREA ( 0 0 ) ( 2000 2000 ) ;
        COMPONENTS {n} ;
        {rows}
        END COMPONENTS
        NETS 0 ;
        END NETS
        END DESIGN
        """).format(n=len(placements), rows=rows)


def _import(tmp_path, *placements):
    (tmp_path / "m.lef").write_text(_LEF)
    (tmp_path / "d.def").write_text(_def(*placements))
    db = buda.BDB(str(tmp_path / "a.bdb"))
    db.import_def_lef(str(tmp_path / "d.def"), str(tmp_path / "m.lef"))
    return db


def test_def_orient_tokens_and_dims(tmp_path):
    # DEF's pure rotations coincide with BDB's; the flip tokens permute (DEF
    # mirrors about Y, BDB about X); 90/270 swap the placed dims vs LEF SIZE.
    db = _import(
        tmp_path,
        ("c_n",  100, 100, "N"), ("c_w",  300, 100, "W"),
        ("c_s",  500, 100, "S"), ("c_e",  700, 100, "E"),
        ("c_fn", 100, 300, "FN"), ("c_fs", 300, 300, "FS"),
        ("c_fe", 500, 300, "FE"), ("c_fw", 700, 300, "FW"),
    )
    got = {c.name: (c.orient, c.x2 - c.x1, c.y2 - c.y1)
           for c in db.all_components()}
    assert got == {
        "c_n":  ("N",  100.0, 40.0),
        "c_s":  ("S",  100.0, 40.0),
        "c_w":  ("W",  40.0, 100.0),   # 90 -> swap
        "c_e":  ("E",  40.0, 100.0),   # 270 -> swap
        "c_fn": ("FS", 100.0, 40.0),   # DEF FN (mirror-Y) == BDB FS
        "c_fs": ("FN", 100.0, 40.0),   # DEF FS (mirror-X) == BDB FN
        "c_fe": ("FW", 40.0, 100.0),   # DEF FE == BDB FW, swap
        "c_fw": ("FE", 40.0, 100.0),   # DEF FW == BDB FE, swap
    }


def test_def_orient_survives_gds_export(tmp_path):
    # The payoff: a DEF-placed rotated/mirrored design exports to GDSII and
    # re-imports with the same placement AND orientation — no dim-mismatch
    # warning, because the oriented cell extent matches the placed bbox.
    db = _import(tmp_path,
                 ("c_n", 100, 100, "N"),
                 ("c_w", 300, 100, "W"),
                 ("c_fe", 500, 100, "FE"))
    before = {c.name: (c.x1, c.y1, c.x2, c.y2, c.orient)
              for c in db.all_components()}
    st = db.export_gds(str(tmp_path / "out.gds"))
    assert not any("footprint" in w for w in st.warnings)
    db2 = buda.BDB(str(tmp_path / "b.bdb"))
    db2.import_gds(str(tmp_path / "out.gds"))
    after = {c.name: (c.x1, c.y1, c.x2, c.y2, c.orient)
             for c in db2.all_components()}
    assert after == before


def test_def_unknown_orient_defaults_identity(tmp_path):
    # A malformed/unknown orientation token falls back to identity ('N', no
    # swap) rather than corrupting the placement.
    db = _import(tmp_path, ("c_x", 100, 100, "ZZ"))
    (c,) = db.all_components()
    assert (c.orient, c.x2 - c.x1, c.y2 - c.y1) == ("N", 100.0, 40.0)


_PIN_DEF = """\
VERSION 5.8 ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 128000 150000 ) ;
PINS {n} ;
{rows}
END PINS
END DESIGN
"""


def _import_pins(tmp_path, orients, rect="( -1000 -150 ) ( 1000 150 )"):
    """One pin per orientation, every one carrying the SAME offset rect."""
    rows = "\n".join(
        f"  - p_{o} + NET p_{o} + DIRECTION INPUT + USE SIGNAL"
        f" + LAYER met3 {rect} + PLACED ( {5000 + 1000 * i} 24820 ) {o} ;"
        for i, o in enumerate(orients))
    (tmp_path / "e.lef").write_text("VERSION 5.8 ;\nEND LIBRARY\n")
    (tmp_path / "p.def").write_text(_PIN_DEF.format(n=len(orients), rows=rows))
    db = buda.BDB(str(tmp_path / "p.bdb"))
    db.import_def_lef(str(tmp_path / "p.def"), str(tmp_path / "e.lef"))
    return {c.name: (round(c.x2 - c.x1, 4), round(c.y2 - c.y1, 4))
            for c in db.all_components()}


def test_a_pins_orientation_transforms_its_port_rect(tmp_path):
    """DEF 5.8 gives a `PORT`'s geometry relative to the pin's own origin and
    transforms it by the pin's orientation.  The reader ignored that, so every
    orientation imported with the shape an `N` pin would have and the four 90
    degree ones came out with width and height exchanged (#912).

    The four direction-preserving ones were already right — a mirror of a
    rect about its own origin is that rect once the corners are re-min/maxed
    — which is why it went unseen: `emit_pin_def` writes `N` for every pin,
    so nothing in the LibreLane study could reach it.  Only somebody else's
    DEF can."""
    wh = _import_pins(tmp_path, ["N", "S", "FN", "FS", "W", "E", "FW", "FE"])
    for o in ("N", "S", "FN", "FS"):
        assert wh[f"PIN/p_{o}"] == (2.0, 0.3), (o, wh)
    for o in ("W", "E", "FW", "FE"):
        assert wh[f"PIN/p_{o}"] == (0.3, 2.0), (o, wh)


def test_a_pin_rect_is_transformed_about_its_origin_not_a_box(tmp_path):
    """The transform is anchored at the pin's origin, which is what makes it
    different from a component's: a cell rect is normalized inside the cell's
    `w x h` box so the transformed box keeps its lower-left, while a `PORT`
    rect has no box and may be negative on either axis.

    Measured with an ASYMMETRIC rect, where a box-normalized transform and an
    origin-anchored one give different answers rather than merely different
    reasoning: `( 0 0 ) ( 2000 500 )` under `S` is (-2000, -500)..(0, 0)
    about the origin, so the pin's extent runs BELOW and LEFT of its placed
    point."""
    orients = ["N", "S"]
    wh = _import_pins(tmp_path, orients, rect="( 0 0 ) ( 2000 500 )")
    assert wh["PIN/p_N"] == (2.0, 0.5) and wh["PIN/p_S"] == (2.0, 0.5)

    rows = "\n".join(
        f"  - p_{o} + NET p_{o} + DIRECTION INPUT + USE SIGNAL"
        f" + LAYER met3 ( 0 0 ) ( 2000 500 ) + PLACED ( 40000 24000 ) {o} ;"
        for o in orients)
    (tmp_path / "e2.lef").write_text("VERSION 5.8 ;\nEND LIBRARY\n")
    (tmp_path / "p2.def").write_text(_PIN_DEF.format(n=2, rows=rows))
    db = buda.BDB(str(tmp_path / "p2.bdb"))
    db.import_def_lef(str(tmp_path / "p2.def"), str(tmp_path / "e2.lef"))
    box = {c.name: tuple(round(v, 4) for v in (c.x1, c.y1, c.x2, c.y2))
           for c in db.all_components()}
    # N: the rect runs up-and-right from the placed point
    assert box["PIN/p_N"] == (40.0, 24.0, 42.0, 24.5), box
    # S: down-and-left of it — a box-normalized transform cannot produce this
    assert box["PIN/p_S"] == (38.0, 23.5, 40.0, 24.0), box
