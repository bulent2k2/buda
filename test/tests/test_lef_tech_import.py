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


# ── a stack whose own names collide (IHP sg13g2 / sg13cmos5l) ──────────────
#
# The name-derived rule was validated against stacks where the trailing
# integer IS the stack index (sky130 `met1..met5`, NanGate45
# `metal1..metal10`).  That is not a property of LEF: IHP calls its two thick
# top layers `TopMetal1`/`TopMetal2`, which collide with `Metal1`/`Metal2` —
# in BOTH of its open PDKs.  This is that stack's shape.
_IHP = "VERSION 5.8 ;\n" + "".join(
    "LAYER %s\n  TYPE ROUTING ;\n  DIRECTION %s ;\n  PITCH %s ;\n"
    "  WIDTH %s ;\nEND %s\n" % (n, d, p, w, n)
    for n, d, p, w in [
        ("Metal1",    "HORIZONTAL", "0.42", "0.16"),
        ("Metal2",    "VERTICAL",   "0.48", "0.20"),
        ("Metal3",    "HORIZONTAL", "0.42", "0.20"),
        ("Metal4",    "VERTICAL",   "0.48", "0.20"),
        ("Metal5",    "HORIZONTAL", "0.42", "0.20"),
        ("TopMetal1", "VERTICAL",   "3.28", "1.64"),
        ("TopMetal2", "HORIZONTAL", "4.0",  "2.0"),
    ]) + "END LIBRARY\n"


def test_a_stack_whose_own_names_collide_is_numbered_by_the_files_order(tmp_path):
    """Two of the FILE's names deriving one id is not a per-layer question:
    no layer owns the clash, and the name-derived reading simply does not
    apply to this technology.  So the whole stack takes the file's order —
    which is the fact the ids are for, LEF listing routing layers bottom-up."""
    s, out = _run(tmp_path, "import_lef_tech @TECH@", _IHP)
    assert [s._layer_name_map[n] for n in
            ("Metal1", "Metal2", "Metal3", "Metal4", "Metal5",
             "TopMetal1", "TopMetal2")] == [1, 2, 3, 4, 5, 6, 7], out
    assert "BUDA-1617" in out, out
    # The report names the collision that forced it — the FIRST one, since
    # the whole stack renumbers on it and scanning further is work for
    # nothing — and EVERY id that moved, which is what a script saying
    # `def_layer 6` needs to see.
    assert "Metal1 and TopMetal1 share a trailing number" in out, out
    assert "TopMetal1 1->6" in out and "TopMetal2 2->7" in out


def test_the_renumber_puts_TOP_on_the_thick_top_layers(tmp_path):
    """The consequence that made the refusal serious, not the numbering
    itself.  TOP is "the topmost layer per direction", so dropping the two
    highest layers did not merely lose them — it moved TOP onto Metal4/Metal5
    and ran the planner's whole TOP-vs-LOW economics against a stack this
    technology does not have."""
    s, _ = _run(tmp_path, "import_lef_tech @TECH@", _IHP)
    tops = {n for n in s._layer_name_map
            if s.layers.get_layer_type(s._layer_name_map[n]) ==
            buda.LayerType.TOP}
    assert tops == {"TopMetal1", "TopMetal2"}
    # ...and they are the layers whose geometry is actually thick: an ~8x
    # pitch step is what makes a TOP/LOW distinction mean anything here.
    assert _pitch(s, s._layer_name_map["TopMetal1"]) > \
        6 * _pitch(s, s._layer_name_map["Metal4"])


def test_the_clash_is_the_FILES_and_survives_a_script_predeclaring_its_names(tmp_path):
    """A name the script also declared still OCCUPIES its trailing number in
    the file.  The first cut detected the clash over only the layers left to
    assign, so predeclaring `Metal1`/`Metal2` by name — a supported
    precedence path — left `Metal3`..`Metal5`, `TopMetal1`, `TopMetal2`,
    whose trailing numbers are all distinct: no clash seen, ids 1 and 2 read
    as merely script-held, and both top metals skipped again.  Measured
    before the fix: `imported 3 routing layer(s)` plus two skips, i.e. the
    five-layer IHP model this whole change exists to remove (Codex P1, #929).
    """
    s, out = _run(tmp_path, """
        def_layer 1 Metal1 H 50
        def_layer 2 Metal2 V 50
        import_lef_tech @TECH@
        """, _IHP)
    assert "skipped layer" not in out, out
    assert "BUDA-1617" in out, out
    # every one of the file's seven layers is present, and the two the script
    # declared keep the ids it gave them
    assert s._layer_name_map["Metal1"] == 1 and s._layer_name_map["Metal2"] == 2
    assert [s._layer_name_map[n] for n in
            ("Metal3", "Metal4", "Metal5", "TopMetal1", "TopMetal2")] == \
        [3, 4, 5, 6, 7], out
    # ...and the report names the collision, whose two names are SPLIT across
    # the script's group and the file's here — reading only the assigned
    # layers printed an empty list.
    assert "Metal1 and TopMetal1 share a trailing number" in out, out
    tops = {n for n in s._layer_name_map
            if s.layers.get_layer_type(s._layer_name_map[n]) ==
            buda.LayerType.TOP}
    assert tops == {"TopMetal1", "TopMetal2"}, out


_UNNUMBERED = "VERSION 5.8 ;\n" + "".join(
    "LAYER %s\n  TYPE ROUTING ;\n  DIRECTION %s ;\n  PITCH 0.2 ;\n"
    "  WIDTH 0.1 ;\nEND %s\n" % (n, d, n)
    for n, d in [("local", "HORIZONTAL"), ("M1", "VERTICAL"),
                 ("M2", "HORIZONTAL")]) + "END LIBRARY\n"


def test_an_unnumbered_name_can_collide_too_and_it_is_the_files_clash(tmp_path):
    """A name with no trailing number takes the next free id, and that id is
    a claim like any other: `local` takes 1, which `M1` then derives.  The
    first cut tested only for two names sharing a trailing NUMBER, so it saw
    no clash — and `M1` was dropped with "layer id already in use — rename or
    declare it explicitly", blaming a script that had declared nothing
    (Codex P2, #929; measured `imported 2 routing layer(s)` with M1 skipped).

    Both collisions are the FILE's and neither is per-layer, which is why one
    walk answers them: an id claimed by a file layer forces the whole-stack
    fallback, an id held by the SCRIPT refuses that one layer."""
    s, out = _run(tmp_path, "import_lef_tech @TECH@", _UNNUMBERED)
    assert "skipped layer" not in out, out
    assert [s._layer_name_map[n] for n in ("local", "M1", "M2")] == [1, 2, 3], out
    # ...and the cause is named as what it is, not as a trailing-number clash
    assert "local has no trailing number and takes id 1" in out, out
    assert "which M1 derives from its own name" in out, out


def test_an_unnumbered_name_after_a_numbered_one_is_not_a_collision(tmp_path):
    """The mirror, and the reason the check has to mirror the ASSIGNMENT
    rather than test ids in the abstract: with `M1` first it takes 1, so
    `local` takes 2 and nothing collides.  A cheaper check that asked only
    whether some numbered name derives an unnumbered one's id would renumber
    this stack for no reason."""
    tech = "VERSION 5.8 ;\n" + "".join(
        "LAYER %s\n  TYPE ROUTING ;\n  DIRECTION %s ;\n  PITCH 0.2 ;\n"
        "  WIDTH 0.1 ;\nEND %s\n" % (n, d, n)
        for n, d in [("M1", "VERTICAL"), ("local", "HORIZONTAL")]) + \
        "END LIBRARY\n"
    s, out = _run(tmp_path, "import_lef_tech @TECH@", tech)
    assert "BUDA-1617" not in out, out
    assert s._layer_name_map["M1"] == 1 and s._layer_name_map["local"] == 2


def test_a_script_held_id_is_stepped_over_and_the_order_still_holds(tmp_path):
    """The script's numbering is still the script's.  The renumber takes the
    ids it has not claimed, and the stack stays increasing in file order —
    which is what BUDA's ids are for, since adjacency decides which layers a
    via may join."""
    s, _ = _run(tmp_path, """
        def_layer 3 MINE V LOW 30
        import_lef_tech @TECH@
        """, _IHP)
    ids = [s._layer_name_map[n] for n in
           ("Metal1", "Metal2", "Metal3", "Metal4", "Metal5",
            "TopMetal1", "TopMetal2")]
    assert 3 not in ids, ids
    assert ids == sorted(ids) and len(set(ids)) == len(ids), ids
    assert s._layer_name_map["MINE"] == 3


def test_a_file_internal_clash_and_a_script_held_id_are_different_things(tmp_path):
    """One file, two outcomes, and the difference is who owns the id.  The
    script holding an id refuses that ONE layer (it may describe something
    else entirely); the file clashing with itself renumbers the whole stack.
    Conflating them is how seven layers became five."""
    _s1, out1 = _run(tmp_path, """
        def_layer 2 MYM2 V LOW 30
        import_lef_tech @TECH@
        """)                                    # _TECH: M1/M2/M3, no clash
    assert "skipped layer M2" in out1 and "BUDA-1617" not in out1, out1

    _s2, out2 = _run(tmp_path, "import_lef_tech @TECH@", _IHP)
    assert "BUDA-1617" in out2 and "skipped layer" not in out2, out2


def test_a_script_id_that_differs_from_the_names_number_is_not_a_file_claim(tmp_path):
    """The script's id and the file's claim are DIFFERENT NUMBERS for the
    same layer, and treating them as one renumbers a stack that never
    clashed (Codex P2, #929).

    `def_layer 2 M1` holds **2** for the script; the file's `M1` still claims
    **1** by its name.  Recording the script's 2 as a file claim made the
    file's own `M2` look like a file-internal collision, so the whole stack
    took file order — putting `M2` at 1, *below* the script-owned `M1` at 2.
    Stack order is the one thing BUDA's ids are for, since adjacency decides
    which layers a via may join, so an inversion is worse than a refusal.

    The BUDA-1617 it printed was false in its own terms too — *"M1 and M2
    share a trailing number"* about two names whose trailing numbers are 1
    and 2 — which is the tell: a report that can say something untrue about
    the file is reading the wrong thing, as it was twice before here.

    Under the documented ownership rule id 2 is held by the SCRIPT, so `M2`
    is refused and nothing else moves.
    """
    s, out = _run(tmp_path, """
        def_layer 2 M1 H LOW 30
        import_lef_tech @TECH@
        """)                                    # _TECH: M1/M2/M3, distinct
    assert "BUDA-1617" not in out, out
    assert "skipped layer M2" in out, out
    assert s._layer_name_map["M1"] == 2, out    # the script's, untouched
    assert s._layer_name_map["M3"] == 3, out    # its own name's, untouched
    assert "M2" not in s._layer_name_map, out


def test_a_stack_with_distinct_names_is_untouched_by_any_of_this(tmp_path):
    """The guard on every flow in the tree: sky130 and NanGate45 name their
    layers by stack index, so nothing here may reach them."""
    s, out = _run(tmp_path, "import_lef_tech @TECH@")
    assert [s._layer_name_map[n] for n in ("M1", "M2", "M3")] == [1, 2, 3]
    assert "BUDA-1617" not in out, out
