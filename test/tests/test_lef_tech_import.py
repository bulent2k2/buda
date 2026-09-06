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

"""`import_lef_tech` — the technology stack from LEF (Phase 2b).

This replaces a hand-typed `def_layer` + `def_track_pattern` stack with one
read from the technology file.  The load-bearing property is not the reading
but the **precedence**: an explicit script declaration always outranks
imported data, *in either declaration order*.  Declared first, the import
skips it; declared later, it replaces what the import installed.  Without
both directions the command could only be used at the very top of a script,
and any flow that overrides one layer would silently get the imported one.
"""
import contextlib
import io
import textwrap

import pytest

import buda
import buda_cli

_TECH = """VERSION 5.8 ;
LAYER M1
  TYPE ROUTING ;
  DIRECTION HORIZONTAL ;
  PITCH 0.20 ;
  WIDTH 0.08 ;
  SPACING 0.08 ;
  OFFSET 0.10 ;
END M1
LAYER via1
  TYPE CUT ;
END via1
LAYER M2
  TYPE ROUTING ;
  DIRECTION VERTICAL ;
  PITCH 0.24 ;
  WIDTH 0.10 ;
END M2
LAYER M3
  TYPE ROUTING ;
  DIRECTION HORIZONTAL ;
  PITCH 0.40 ;
  WIDTH 0.20 ;
END M3
END LIBRARY
"""


def _run(tmp_path, script, tech=_TECH):
    (tmp_path / "tech.lef").write_text(tech)
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for line in textwrap.dedent(script).strip().splitlines():
            line = line.strip()
            if line:
                s.do_command(line.replace("@TECH@", str(tmp_path / "tech.lef")))
    return s, buf.getvalue()


def _pitch(s, lid):
    return s.routing_grid.get_layer_grid(lid).global_pattern().unit_pitch()


# ── what a tech import produces ────────────────────────────────────────────

def test_routing_layers_become_layers_with_track_patterns(tmp_path):
    s, out = _run(tmp_path, "import_lef_tech @TECH@")
    assert s.layers.has_layer(1) and s.layers.has_layer(2) and s.layers.has_layer(3)
    assert s.layers.get_layer_dir(1) == buda.LayerDir.HORIZONTAL
    assert s.layers.get_layer_dir(2) == buda.LayerDir.VERTICAL
    # PITCH becomes the track period, so one signal track per pitch.
    assert _pitch(s, 1) == pytest.approx(0.20)
    assert _pitch(s, 2) == pytest.approx(0.24)
    assert s.layers.eff_bus_width(1, 0.0, 1) == pytest.approx(0.20)


def test_the_layer_id_comes_from_the_name(tmp_path):
    """LEF names layers, BUDA numbers them.  Using the trailing integer is
    what makes an imported stack and a script that says `def_layer 4` mean
    the same layer — inventing ids would renumber a stack whose numbers are
    how the script refers to it."""
    s, _ = _run(tmp_path, "import_lef_tech @TECH@")
    assert s._layer_name_map == {"M1": 1, "M2": 2, "M3": 3}


def test_cut_layers_are_not_routing_layers(tmp_path):
    s, out = _run(tmp_path, "import_lef_tech @TECH@")
    assert "via1" not in s._layer_name_map
    assert "imported 3 routing layer(s)" in out


def test_top_is_the_highest_layer_per_direction(tmp_path):
    """LEF says nothing about which layers a planner should prefer for
    trunks — TOP is a BUDA notion.  The topmost layer in each direction is
    the defensible default, and it is overridable."""
    s, _ = _run(tmp_path, "import_lef_tech @TECH@")
    assert s.layers.get_top_layer(buda.LayerDir.HORIZONTAL) == 3   # M3, not M1
    assert s.layers.get_top_layer(buda.LayerDir.VERTICAL) == 2


def test_a_property_definitions_block_is_skipped_as_statements(tmp_path):
    """sky130's tech LEF opens with `PROPERTYDEFINITIONS … LAYER LEF58_TYPE
    STRING ; … END PROPERTYDEFINITIONS`: the object TYPE is spelled with the
    block keyword.  Read by the LAYER branch that line became a layer block
    named LEF58_TYPE and the section's END was reported as mismatched --
    the first real tech LEF the command met refused to load (2026-09-06).
    The section is statements, so it is read as statements."""
    tech = _TECH.replace("VERSION 5.8 ;\n", (
        "VERSION 5.8 ;\nUNITS\n  DATABASE MICRONS 1000 ;\nEND UNITS\n"
        "MANUFACTURINGGRID 0.005 ;\nUSEMINSPACING OBS OFF ;\n"
        "PROPERTYDEFINITIONS\n  LAYER LEF58_TYPE STRING ;\n  MACRO foo INTEGER RANGE 0 10 ;\n"
        "END PROPERTYDEFINITIONS\n"
        "SITE unithd\n  SYMMETRY Y ;\n  CLASS CORE ;\n  SIZE 0.46 BY 2.72 ;\nEND unithd\n"))
    s, out = _run(tmp_path, "import_lef_tech @TECH@", tech)
    assert s._layer_name_map == {"M1": 1, "M2": 2, "M3": 3}
    assert "LEF58_TYPE" not in s._layer_name_map and "imported 3 routing layer(s)" in out


def test_a_layer_without_a_direction_is_skipped_loudly(tmp_path):
    """BUDA has no undirected layer, so there is nothing to import — and
    silently dropping it would leave a hole in the stack the script never
    hears about."""
    tech = _TECH.replace("  DIRECTION VERTICAL ;\n", "")
    s, out = _run(tmp_path, "import_lef_tech @TECH@", tech)
    assert "skipped layer M2" in out and "no DIRECTION" in out
    assert "M2" not in s._layer_name_map


def test_a_layer_without_a_pitch_still_becomes_a_layer(tmp_path):
    """No PITCH means no track pattern to synthesize — but the layer itself
    is real, and dropping it would break every reference to it."""
    tech = _TECH.replace("  PITCH 0.24 ;\n", "")
    s, _ = _run(tmp_path, "import_lef_tech @TECH@", tech)
    assert s.layers.has_layer(2)
    assert not s.routing_grid.has_layer(2)


def test_an_impossible_pitch_is_reported_rather_than_producing_a_bad_pattern(tmp_path):
    tech = _TECH.replace("  PITCH 0.24 ;\n  WIDTH 0.10 ;",
                         "  PITCH 0.05 ;\n  WIDTH 0.10 ;")
    s, out = _run(tmp_path, "import_lef_tech @TECH@", tech)
    assert "no room for spacing" in out
    assert not s.routing_grid.has_layer(2)


# ── precedence: the script always wins, in either order ────────────────────

def test_an_id_collision_still_skips_the_layer_outright(tmp_path):
    """A DIFFERENT name for an id the script already used is a collision, not
    a match: the import cannot tell whether the two describe the same layer,
    so it takes nothing at all."""
    s, out = _run(tmp_path, """
        def_layer 2 MYM2 V LOW 30
        import_lef_tech @TECH@
        """)
    assert "skipped layer M2" in out and "layer id already in use" in out, out
    assert s._layer_name_map["MYM2"] == 2
    assert s.layers.get_layer_type(2) == buda.LayerType.LOW   # the script's
    assert not s.routing_grid.has_layer(2)      # and no imported pattern


def test_a_script_layer_keeps_its_IDENTITY_and_takes_the_files_GEOMETRY(tmp_path):
    """Precedence is per FACT, not per layer.

    `def_layer` declares id, name, direction, TOP/LOW and overhead — and has
    no syntax for PITCH or WIDTH.  Skipping the layer outright therefore threw
    away the only facts in the file the script could not have stated, and left
    the design with a layer that has no track geometry at all.

    Measured on `flow/ariane133`, which declares its ten layers by hand: every
    routing layer came out as one FULL-PITCH signal slot — a wire occupying
    its whole track with no space beside it — so no NDR width, spacing or
    shield rule could mean anything on that design."""
    s, out = _run(tmp_path, """
        def_layer 2 M2 V LOW 30
        import_lef_tech @TECH@
        """)
    # Identity: the script's, untouched.
    assert s._layer_name_map["M2"] == 2
    assert s.layers.get_layer_type(2) == buda.LayerType.LOW
    assert s.layers.get_layer_dir(2) == buda.LayerDir.VERTICAL
    # Geometry: the file's, and said out loud rather than done silently.
    assert "took track geometry" in out and "M2" in out, out
    assert s.routing_grid.has_layer(2)
    assert _pitch(s, 2) == pytest.approx(0.24)


def test_a_script_PATTERN_still_outranks_the_files_geometry(tmp_path):
    """The other half of the same rule: a geometry declaration DOES assert
    geometry, so it wins — in this order as well as the reverse (which
    `test_a_pattern_declared_after_the_import_replaces_it` covers).  Without
    this, letting geometry travel past a declared layer would have quietly
    taken the pattern too."""
    s, out = _run(tmp_path, """
        def_layer 2 M2 V LOW 30
        def_track_pattern 2 0 VDD 2 1 _ 1 1 GND 2 1
        import_lef_tech @TECH@
        """)
    assert "script-declared pattern wins" in out, out
    assert _pitch(s, 2) == pytest.approx(8.0)      # the script's, not 0.24


def test_a_layer_declared_after_the_import_replaces_it(tmp_path):
    """`add_layer` appends, so a duplicate id would leave BOTH rows in the
    stack with lookups silently taking the first — i.e. the imported one.
    The override has to remove, not shadow."""
    s, out = _run(tmp_path, """
        import_lef_tech @TECH@
        def_layer 2 M2 V LOW 40
        """)
    assert "overrides the imported layer 2" in out
    assert s.layers.get_layer_type(2) == buda.LayerType.LOW
    assert s.layers.get_top_layer(buda.LayerDir.VERTICAL) != 2   # TOP re-derived


def test_a_pattern_declared_after_the_import_replaces_it(tmp_path):
    s, out = _run(tmp_path, """
        import_lef_tech @TECH@
        def_track_pattern 2 0 VDD 2 1 _ 1 1 GND 2 1
        """)
    assert "overrides the imported pattern on layer 2" in out
    assert _pitch(s, 2) == pytest.approx(8.0)      # the script's, not 0.24


def test_overriding_a_layers_direction_re_registers_its_pattern(tmp_path):
    """The routing grid stores the direction alongside the pattern, so an
    override that flips H/V would otherwise leave the imported pattern
    registered the old way — tracks running across the wires that use them."""
    s, _ = _run(tmp_path, """
        import_lef_tech @TECH@
        def_layer 1 M1 V TOP 30
        """)
    assert s.layers.get_layer_dir(1) == buda.LayerDir.VERTICAL
    assert s.routing_grid.get_layer_grid(1).is_horizontal() is False


def test_a_duplicate_script_layer_is_still_an_error(tmp_path):
    """The precedence rule must not become a licence to redefine: two script
    declarations of one id are still the collision they always were."""
    with pytest.raises(SystemExit):
        _run(tmp_path, """
            def_layer 2 A V TOP 30
            def_layer 2 B V TOP 30
            """)


# ── failing loud ───────────────────────────────────────────────────────────

def test_a_missing_or_malformed_tech_file_stops_the_run(tmp_path):
    with pytest.raises(SystemExit):
        _run(tmp_path, "import_lef_tech /nonexistent/none.lef")


def test_a_lef_with_no_routing_layers_is_a_reported_no_op(tmp_path):
    s, out = _run(tmp_path, "import_lef_tech @TECH@",
                  "LAYER via1\n  TYPE CUT ;\nEND via1\nEND LIBRARY\n")
    assert "no ROUTING layers" in out
    assert not s._layer_name_map
