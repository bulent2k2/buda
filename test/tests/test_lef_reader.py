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

"""The LEF reader (Phase 2a of docs/internal/lefdef_interface_plan.md).

What it replaces: two line-oriented scanners that recognised exactly
MACRO/SIZE/PIN/DIRECTION/USE/RECT.  Everything else was skipped in silence —
including `ORIGIN`, which shifts every pin of the macro it appears on.

Three properties are worth stating as tests, because each is a way the old
scanner produced a plausible-looking wrong answer rather than an error:

  * layout is not syntax — LEF is a token language with `;` terminators, so a
    statement wrapped across lines must read the same as one on a single line;
  * a construct that cannot be modelled must be RECORDED, not dropped, so
    "we ignored it" and "it wasn't in the file" stop looking the same;
  * a malformed file must fail with a line number, rather than yielding a
    partial library that reads as complete.
"""
import pytest

import buda


def _lib(text):
    return buda.parse_lef(text, "t.lef")


# ── the shape of a macro ───────────────────────────────────────────────────

_SIMPLE = """
MACRO m
  CLASS CORE ;
  SIZE 10 BY 4 ;
  PIN a
    DIRECTION INPUT ;
    USE SIGNAL ;
    PORT
      LAYER M1 ;
      RECT 2 1 4 3 ;
    END
  END a
END m
"""


def test_reads_size_class_and_a_pin():
    lib = _lib(_SIMPLE)
    assert len(lib.macros) == 1
    m = lib.macros[0]
    assert (m.name, m.cls, m.w, m.h) == ("m", "CORE", 10.0, 4.0)
    p = m.find_pin("a")
    assert (p.dir, p.use) == ("INPUT", "SIGNAL")
    assert p.centroid() == (3.0, 2.0)
    assert p.ports[0].layer == "M1"


def test_statements_may_wrap_across_lines():
    """LEF is `;`-terminated, not newline-terminated.  The scanner this
    replaces read line by line, so a wrapped statement — legal, and what
    every tool-written file eventually contains — was simply not seen."""
    wrapped = _SIMPLE.replace("SIZE 10 BY 4 ;", "SIZE 10\n    BY\n    4 ;")
    wrapped = wrapped.replace("RECT 2 1 4 3 ;", "RECT 2 1\n         4 3 ;")
    a, b = _lib(_SIMPLE).macros[0], _lib(wrapped).macros[0]
    assert (a.w, a.h) == (b.w, b.h)
    assert a.find_pin("a").centroid() == b.find_pin("a").centroid()


def test_comments_and_quotes_are_lexical_not_content():
    lib = _lib("""
        # a leading comment
        MACRO m   # trailing
          SIZE 10 BY 4 ;   # and here
          FOREIGN "m gds" ;
        END m
        """)
    assert lib.macros[0].w == 10.0
    assert lib.macros[0].foreign == "m gds"


def test_multi_port_pin_keeps_every_landing():
    """A pin with two physically separate ports is one electrical node with
    two places to land.  Both must survive: collapsing them loses the choice,
    and taking only the first loses the other landing entirely."""
    lib = _lib("""
        MACRO m
          SIZE 10 BY 10 ;
          PIN a
            DIRECTION INOUT ;
            PORT
              LAYER M1 ;
              RECT 0 0 2 2 ;
            END
            PORT
              LAYER M2 ;
              RECT 8 8 10 10 ;
            END
          END a
        END m
        """)
    p = lib.macros[0].find_pin("a")
    assert [q.layer for q in p.ports] == ["M1", "M2"]
    assert p.centroid() == (5.0, 5.0)          # both, not just the first


def test_obs_shapes_are_read():
    lib = _lib("""
        MACRO m
          SIZE 10 BY 10 ;
          OBS
            LAYER M1 ;
            RECT 1 1 9 9 ;
          END
        END m
        """)
    obs = lib.macros[0].obs
    assert len(obs) == 1 and obs[0].layer == "M1"
    assert (obs[0].rects[0].x1, obs[0].rects[0].x2) == (1.0, 9.0)


def test_origin_is_read_rather_than_assumed_zero():
    """ORIGIN is the offset from the placement point to the geometry origin.
    Ignoring it — as the old scanner did — puts every pin of the macro
    somewhere it is not, with no symptom until routing lands on nothing."""
    lib = _lib(_SIMPLE.replace("SIZE 10 BY 4 ;", "ORIGIN 1 2 ;\n  SIZE 10 BY 4 ;"))
    m = lib.macros[0]
    assert (m.ox, m.oy) == (1.0, 2.0)


def test_a_pin_with_no_shapes_is_distinguishable_from_one_at_the_origin():
    lib = _lib("""
        MACRO m
          SIZE 10 BY 10 ;
          PIN a
            DIRECTION INPUT ;
          END a
        END m
        """)
    p = lib.macros[0].find_pin("a")
    assert not p.has_geometry()
    assert p.centroid() is None


# ── the model gap, made visible ────────────────────────────────────────────

def test_unmodelled_constructs_are_recorded_with_their_line():
    """The honest half of the reader: a spacing table has no home in the
    current model, and recording it is what keeps 'not modelled' from being
    indistinguishable from 'not present'."""
    lib = _lib("""
        LAYER M1
          TYPE ROUTING ;
          DIRECTION HORIZONTAL ;
          PITCH 0.19 ;
          SPACINGTABLE PARALLELRUNLENGTH 0 1.5 WIDTH 0 0.05 0.05 ;
        END M1
        """)
    kinds = {u.construct for u in lib.unmodelled}
    assert "LAYER.SPACINGTABLE" in kinds
    rec = [u for u in lib.unmodelled if u.construct == "LAYER.SPACINGTABLE"][0]
    assert rec.line == 6, rec.line          # points AT the statement
    assert "PARALLELRUNLENGTH" in rec.detail


def test_polygon_geometry_is_recorded_not_approximated():
    """A Rect model cannot hold a polygon.  Substituting its bounding box
    would be a silent lie about where metal is; a pin reported without
    geometry is at least checkable."""
    lib = _lib("""
        MACRO m
          SIZE 10 BY 10 ;
          PIN a
            PORT
              LAYER M1 ;
              POLYGON 0 0 2 0 2 2 1 2 1 1 0 1 ;
            END
          END a
        END m
        """)
    assert any(u.construct == "PIN.PORT.POLYGON" for u in lib.unmodelled)
    assert not lib.macros[0].find_pin("a").has_geometry()


def test_deferred_blocks_are_skipped_as_units():
    """VIA/VIARULE are deferred to the router path.  Skipping them must not
    lose the file: everything after has to parse normally."""
    lib = _lib("""
        VIARULE vr GENERATE
          LAYER M1 ;
            ENCLOSURE 0.05 0.01 ;
          LAYER M2 ;
            ENCLOSURE 0.01 0.05 ;
        END vr
        MACRO m
          SIZE 3 BY 3 ;
        END m
        """)
    assert [m.name for m in lib.macros] == ["m"]
    assert any(u.construct == "VIARULE" for u in lib.unmodelled)


def test_a_nested_block_does_not_end_its_parent():
    """The one real hazard in skipping: `LAYER m1 … END m1` inside a
    NONDEFAULTRULE is a BLOCK while `LAYER m1 ;` is a STATEMENT, and counting
    bare ENDs would end the rule early and feed the rest of the file to the
    top-level loop as garbage."""
    lib = _lib("""
        NONDEFAULTRULE wide
          LAYER M1
            WIDTH 0.2 ;
            SPACING 0.2 ;
          END M1
        END wide
        MACRO m
          SIZE 7 BY 7 ;
        END m
        """)
    assert [m.name for m in lib.macros] == ["m"]
    assert lib.macros[0].w == 7.0


# ── units ──────────────────────────────────────────────────────────────────

def test_units_and_manufacturing_grid():
    lib = _lib("""
        VERSION 5.8 ;
        UNITS
          DATABASE MICRONS 2000 ;
        END UNITS
        MANUFACTURINGGRID 0.005 ;
        MACRO m
          SIZE 1 BY 1 ;
        END m
        """)
    assert lib.units_dbu == 2000.0
    assert lib.manufacturing_grid == 0.005


# ── failing loud ───────────────────────────────────────────────────────────

def test_a_truncated_file_fails_with_a_line_number():
    with pytest.raises(RuntimeError) as e:
        _lib("MACRO m\n  SIZE 10 BY 4 ;\n  PIN a\n    DIRECTION INPUT ;\n")
    assert "t.lef:" in str(e.value)


def test_an_unterminated_statement_is_an_error_not_a_shrug():
    with pytest.raises(RuntimeError) as e:
        _lib("MACRO m\n  SIZE 10 BY 4\nEND m\n")
    assert "t.lef:" in str(e.value)


def test_a_malformed_number_names_the_statement():
    with pytest.raises(RuntimeError) as e:
        _lib("MACRO m\n  SIZE wide BY 4 ;\nEND m\n")
    assert "SIZE" in str(e.value)


def test_read_lef_reports_a_missing_file():
    with pytest.raises(RuntimeError) as e:
        buda.read_lef("/nonexistent/x.lef")
    assert "cannot open" in str(e.value)


# ── what reaches the BDB ───────────────────────────────────────────────────

_DEF = """VERSION 5.8 ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 100000 100000 ) ;
COMPONENTS 1 ;
  - i0 m + PLACED ( 10000 20000 ) N ;
END COMPONENTS
NETS 1 ;
  - n0 ( i0 a ) ;
END NETS
END DESIGN
"""


def _import(tmp_path, lef_text):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "m.lef").write_text(lef_text)
    (tmp_path / "d.def").write_text(_DEF)
    db = buda.BDB(str(tmp_path / "a.bdb"))
    db.import_def_lef(str(tmp_path / "d.def"), str(tmp_path / "m.lef"))
    return db


def test_origin_shifts_the_pin_position_in_the_bdb(tmp_path):
    """The end-to-end consequence of ORIGIN: pin coordinates are relative to
    the geometry origin, so a macro with `ORIGIN 1 2` lands its pins one and
    two microns lower than a naive read puts them.  The component is placed
    at (10, 20) µm and pin `a` sits at (3, 2) in macro geometry, so the
    absolute position is (10+3-1, 20+2-2) = (12, 20)."""
    db = _import(tmp_path, _SIMPLE.replace("SIZE 10 BY 4 ;",
                                           "ORIGIN 1 2 ;\n  SIZE 10 BY 4 ;"))
    pin = [p for p in db.all_pins() if p.pin_name == "a"][0]
    assert (pin.px, pin.py) == (12.0, 20.0)

    # …and with no ORIGIN the same macro lands at (13, 22): the fix is a
    # shift, not a re-interpretation of everything else.
    db0 = _import(tmp_path / "zero", _SIMPLE)
    pin0 = [p for p in db0.all_pins() if p.pin_name == "a"][0]
    assert (pin0.px, pin0.py) == (13.0, 22.0)


def test_a_malformed_lef_now_stops_the_import(tmp_path):
    """A behaviour change worth being explicit about: the scanner this
    replaces ignored anything it did not recognise, so a truncated library
    imported as a partial one and the run continued on missing footprints.
    Reading a technology file that cannot be read is not a recoverable
    state."""
    with pytest.raises(RuntimeError):
        _import(tmp_path, "MACRO m\n  SIZE 10 BY 4 ;\n  PIN a\n")


def test_the_import_records_a_census_of_what_it_could_not_model(tmp_path):
    """After the import, the gap between the file and the model is queryable
    rather than a matter of trust.  Stored as meta so it survives the run and
    can be diffed between technology drops."""
    db = _import(tmp_path, """
        MACRO m
          SIZE 10 BY 4 ;
          PIN a
            DIRECTION INPUT ;
            ANTENNAGATEAREA 0.5 ;
            PORT
              LAYER M1 ;
              RECT 2 1 4 3 ;
            END
          END a
        END m
        """)
    census = db.meta_get("lef_unmodelled")
    assert "PIN.ANTENNA" in census, census
    # …and the pin itself still imported: recording a gap is not a refusal.
    assert [p.pin_name for p in db.all_pins()] == ["a"]


def test_lef_units_are_recorded_even_though_def_units_govern(tmp_path):
    """LEF carries its own DATABASE MICRONS.  DEF's is what the coordinates
    are actually in, so LEF's does not override it — but a disagreement
    between the two is worth being able to see."""
    db = _import(tmp_path, "UNITS\n  DATABASE MICRONS 2000 ;\nEND UNITS\n"
                 + "MANUFACTURINGGRID 0.005 ;\n" + _SIMPLE)
    assert db.meta_get("lef_units_dbu").startswith("2000")
    assert db.meta_get("lef_manufacturing_grid").startswith("0.005")
    assert db.units() == 1000        # the DEF's, unchanged


# ── review findings (Codex on #646) ────────────────────────────────────────

def test_a_nameless_block_is_not_given_its_first_body_token_as_a_name():
    """`PROPERTYDEFINITIONS` and `SPACING` take no name.  Reading the first
    BODY token as one meant `END PROPERTYDEFINITIONS` never matched, the
    terminator stayed in the stream, and everything after it parsed as
    garbage."""
    lib = _lib("""
        PROPERTYDEFINITIONS
          LIBRARY intprop INTEGER ;
          MACRO stringprop STRING ;
        END PROPERTYDEFINITIONS
        SPACING
          SAMENET M1 M1 0.1 ;
        END SPACING
        MACRO m
          SIZE 5 BY 6 ;
        END m
        """)
    assert [m.name for m in lib.macros] == ["m"]
    assert (lib.macros[0].w, lib.macros[0].h) == (5.0, 6.0)


def test_a_vendor_extension_block_ends_at_endext():
    lib = _lib("""
        BEGINEXT "vendor stuff"
          CREATOR "someone" ;
        ENDEXT
        MACRO m
          SIZE 5 BY 6 ;
        END m
        """)
    assert [m.name for m in lib.macros] == ["m"]


def test_a_mismatched_end_is_an_error_not_a_silent_skip():
    """`MACRO m … END n` used to return macro `m` and then swallow the NEXT
    macro's opener and SIZE as an unknown top-level statement — a library
    that comes back plausible and short by one macro."""
    with pytest.raises(RuntimeError) as e:
        _lib("""
            MACRO m
              SIZE 1 BY 1 ;
            END n
            MACRO n
              SIZE 2 BY 2 ;
            END n
            """)
    assert "mismatched END" in str(e.value), str(e.value)


def test_an_iterated_rect_keeps_its_base_shape_and_records_the_repetition():
    """`RECT 0 0 1 1 ITERATE DO 2 BY 3 STEP 5 6 ;` is valid LEF whose last
    four tokens are `… STEP 5 6`, not coordinates — a last-four heuristic
    rejected the whole file as malformed."""
    lib = _lib("""
        MACRO m
          SIZE 20 BY 20 ;
          PIN a
            PORT
              LAYER M1 ;
              RECT 0 0 1 1 ITERATE DO 2 BY 3 STEP 5 6 ;
            END
          END a
        END m
        """)
    p = lib.macros[0].find_pin("a")
    assert p.has_geometry()
    r = p.ports[0].rects[0]
    assert (r.x1, r.y1, r.x2, r.y2) == (0.0, 0.0, 1.0, 1.0)
    assert any("ITERATE" in u.construct for u in lib.unmodelled)


def test_a_masked_rect_still_reads_its_coordinates():
    lib = _lib("""
        MACRO m
          SIZE 20 BY 20 ;
          PIN a
            PORT
              LAYER M1 ;
              RECT MASK 2 3 4 5 6 ;
            END
          END a
        END m
        """)
    r = lib.macros[0].find_pin("a").ports[0].rects[0]
    assert (r.x1, r.y1, r.x2, r.y2) == (3.0, 4.0, 5.0, 6.0)


def test_reimporting_a_clean_lef_clears_the_previous_census(tmp_path):
    """import_def_lef replaces the design tables, so a census left over from
    a previous technology file would have the database reporting constructs
    from a LEF that is no longer loaded."""
    dirty = _SIMPLE.replace("USE SIGNAL ;", "USE SIGNAL ;\n    ANTENNAGATEAREA 0.5 ;")
    db = _import(tmp_path / "a", dirty)
    assert "PIN.ANTENNA" in db.meta_get("lef_unmodelled")
    del db
    db2 = _import(tmp_path / "a", _SIMPLE)          # same BDB path, clean LEF
    assert db2.meta_get("lef_unmodelled") == "", db2.meta_get("lef_unmodelled")
