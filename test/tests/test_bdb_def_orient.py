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

"""DEF/LEF import records instance orientation (component.orient, v12).

`import_def_lef` used to discard the DEF `COMPONENTS` orientation token and
apply the LEF `SIZE` axis-aligned regardless — so a rotated macro got the
wrong bbox and no orientation. It now maps the DEF token to BDB's orient
convention and swaps the placed dims for 90/270, so a DEF-placed design
exports to GDSII with orientation intact.
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
