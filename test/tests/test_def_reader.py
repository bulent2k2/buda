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

"""The DEF reader (Phase 3 of docs/internal/lefdef_interface_plan.md).

What it replaces: a three-state line-at-a-time `std::regex` machine reading
UNITS / DIEAREA / COMPONENTS / NETS.  On the checked-in `demo/ariane` DEF
that discarded 20 TRACKS, 6 GCELLGRIDs and 495 PINS — silently.

The properties worth stating as tests are the ones where the old reader was
not merely incomplete but *quietly wrong*:

  * an entry may wrap across lines, and a reader that misses it drops a
    component from the floorplan without saying so;
  * DEF states its own counts, so a reader that ignores them cannot tell a
    fully-read file from a half-read one — and a half-read floorplan is
    still a floorplan;
  * a cell with no LEF footprint became a 0.5 x 0.5 um speck, which turns a
    wrong-LEF run into a plausible and entirely wrong design.
"""
import time

import pytest

import buda


def _def(body, units=1000, die="( 0 0 ) ( 100000 100000 )"):
    return (f"VERSION 5.8 ;\nDESIGN t ;\nUNITS DISTANCE MICRONS {units} ;\n"
            f"DIEAREA {die} ;\n{body}END DESIGN\n")


def _parse(body, **kw):
    return buda.parse_def(_def(body, **kw), "t.def")


# ── the shape of a design ──────────────────────────────────────────────────

_COMPS = """COMPONENTS 2 ;
  - i0 m + PLACED ( 1000 2000 ) N ;
  - i1 m + FIXED ( 5000 6000 ) FS ;
END COMPONENTS
"""


def test_reads_units_die_and_components():
    d = _parse(_COMPS)
    assert d.design == "t" and d.units == 1000
    assert (d.die.x2, d.die.y2) == (100000.0, 100000.0)
    assert [c.name for c in d.components] == ["i0", "i1"]
    assert (d.components[0].x, d.components[0].y) == (1000.0, 2000.0)
    assert d.components[1].orient == "FS" and d.components[1].placed


def test_an_entry_may_wrap_across_lines():
    """DEF is `;`-terminated, not newline-terminated.  The reader this
    replaces matched one regex per LINE, so a wrapped COMPONENTS entry — what
    every tool-written DEF eventually contains — was skipped, silently
    dropping the instance from the floorplan."""
    wrapped = """COMPONENTS 2 ;
  - i0
      m
      + PLACED ( 1000 2000 )
        N ;
  - i1 m + FIXED ( 5000 6000 ) FS ;
END COMPONENTS
"""
    a, b = _parse(_COMPS), _parse(wrapped)
    assert [c.name for c in a.components] == [c.name for c in b.components]
    assert (a.components[0].x, a.components[0].y) == \
           (b.components[0].x, b.components[0].y)


def test_the_declared_counts_are_kept_so_they_can_be_reconciled():
    d = _parse(_COMPS.replace("COMPONENTS 2 ;", "COMPONENTS 7 ;"))
    assert d.declared_components == 7      # what the file CLAIMED
    assert len(d.components) == 2          # what was there


def test_nets_connect_instances_and_ports():
    d = _parse("""NETS 1 ;
  - n0 ( i0 A ) ( PIN out ) ;
END NETS
""")
    n = d.nets[0]
    assert [(c.inst, c.pin) for c in n.conns] == [("i0", "A"), ("PIN", "out")]
    assert n.conns[1].is_port() and not n.conns[0].is_port()


def test_escaped_names_are_unescaped_once():
    """A DEF hierarchical name escapes its brackets.  A token that keeps the
    escape misses its lookup, and the connection is dropped in silence."""
    d = _parse("""NETS 1 ;
  - \\mem\\[0\\] ( \\mem\\[0\\]/u1 A ) ;
END NETS
""")
    assert d.nets[0].name == "mem[0]"
    assert d.nets[0].conns[0].inst == "mem[0]/u1"


def test_tracks_and_gcellgrid_are_read():
    d = _parse("""TRACKS X 500 DO 100 STEP 200 LAYER metal2 metal4 ;
TRACKS Y 250 DO 50 STEP 400 LAYER metal3 ;
GCELLGRID X 0 DO 10 STEP 1000 ;
""")
    assert len(d.tracks) == 2 and len(d.gcellgrid) == 1
    t0 = d.tracks[0]
    assert (t0.dir, t0.start, t0.count, t0.step) == ("X", 500.0, 100, 200.0)
    assert list(t0.layers) == ["metal2", "metal4"]
    assert t0.last() == 500.0 + 200.0 * 99


def test_blockages_and_halos_are_read():
    d = _parse("""COMPONENTS 1 ;
  - i0 m + PLACED ( 0 0 ) N + HALO 100 200 300 400 ;
END COMPONENTS
BLOCKAGES 2 ;
  - LAYER metal3 RECT ( 10 20 ) ( 30 40 ) ;
  - PLACEMENT + PARTIAL 0.5 RECT ( 0 0 ) ( 5 5 ) ;
END BLOCKAGES
""")
    c = d.components[0]
    assert c.has_halo and (c.halo_l, c.halo_b, c.halo_r, c.halo_t) == \
        (100.0, 200.0, 300.0, 400.0)
    assert d.blockages[0].layer == "metal3"
    assert d.blockages[1].is_placement and d.blockages[1].has_density
    assert d.blockages[1].max_density == 0.5


def test_pins_carry_a_location_and_a_layer():
    d = _parse("""PINS 1 ;
  - out + NET out + DIRECTION OUTPUT + USE SIGNAL
      + LAYER metal3 ( -50 -50 ) ( 50 50 )
      + PLACED ( 1000 2000 ) N ;
END PINS
""")
    p = d.pins[0]
    assert (p.name, p.net, p.dir, p.layer) == ("out", "out", "OUTPUT", "metal3")
    assert p.placed and (p.x, p.y) == (1000.0, 2000.0)
    assert len(p.rects) == 1 and p.rects[0].x1 == -50.0


def test_special_net_wires_become_polylines():
    d = _parse("""SPECIALNETS 1 ;
  - VDD ( * VDD ) + ROUTED metal5 400 ( 1000 1000 ) ( 9000 * )
      NEW metal5 400 ( 1000 1000 ) ( * 9000 ) + USE POWER ;
END SPECIALNETS
""")
    assert len(d.special_wires) == 2
    w = d.special_wires[0]
    assert w.net == "VDD" and w.layer == "metal5" and w.width == 400.0
    # `*` repeats the previous coordinate — the shorthand every real
    # SPECIALNETS uses for orthogonal runs.
    assert list(w.pts) == [(1000.0, 1000.0), (9000.0, 1000.0)]
    assert list(d.special_wires[1].pts) == [(1000.0, 1000.0), (1000.0, 9000.0)]


# ── saying what was NOT read ───────────────────────────────────────────────
#
# The reader handles the forms a PDN generator emits: a width-plus-polyline
# with or without the optional `+ SHAPE`/`+ STYLE` clauses, and the
# single-point via placement.  What these pin is the CENSUS: whatever it
# cannot read, it may not report as metal the DEF never drew.  The forms
# still outside it (a via MID-path, `RECT`, `POLYGON`) are legal DEF that
# nothing available emits — see test/tests/data/pdn_goldens/ReadMe.md.

def _sn(construct, d):
    return [u for u in d.unmodelled if u.construct == construct]


@pytest.mark.parametrize("clause", [
    "+ SHAPE STRIPE",
    "+ SHAPE FOLLOWPIN",
    "+ STYLE 1",
    "+ SHAPE STRIPE + STYLE 1",
])
def test_a_stripe_written_with_SHAPE_or_STYLE_is_read(clause):
    # DEF's grammar is `ROUTED <layer> <width> [+ SHAPE t] [+ STYLE n] pts`,
    # so these sit BETWEEN the width and the first point and a point walk
    # that starts only on `(` never starts.  This is not an exotic form: it
    # is what pdngen writes on every stripe and every rail (item 15 measured
    # 685 metal paths in its goldens, of which the reader read zero).
    d = _parse(f"""SPECIALNETS 1 ;
  - VDD ( * VDD ) + ROUTED metal5 400 {clause} ( 1000 1000 ) ( 9000 * )
      + USE POWER ;
END SPECIALNETS
""")
    assert len(d.special_wires) == 1
    w = d.special_wires[0]
    assert (w.layer, w.width) == ("metal5", 400.0)
    assert list(w.pts) == [(1000.0, 1000.0), (9000.0, 1000.0)]
    # Read in full, so nothing to census.
    assert not [u for u in d.unmodelled if u.construct.startswith("SPECIALNETS")]


def test_the_declared_SHAPE_is_kept():
    # A rail inside a standard-cell row and a stripe crossing the die are
    # different obstacles, and this clause is the only thing that says which.
    # It also distinguishes a reader that RECOGNISES the clause from one that
    # merely tolerates it — which is what the first cut of this fix did.
    d = _parse("""SPECIALNETS 1 ;
  - VDD ( * VDD ) + ROUTED metal1 340 + SHAPE FOLLOWPIN ( 100 200 ) ( 900 * )
      NEW metal5 400 + SHAPE STRIPE ( 100 200 ) ( * 900 )
      NEW metal5 400 ( 100 300 ) ( * 900 ) + USE POWER ;
END SPECIALNETS
""")
    assert [w.shape for w in d.special_wires] == ["FOLLOWPIN", "STRIPE", ""]


def test_a_via_placement_is_not_counted_as_an_unread_wire():
    # `NEW <layer> 0 ( x y ) <viaName>` — one point, no run.  pdngen emits
    # 6781 of these against 685 real polylines, so censusing them as unread
    # WIRE would bury the wires that really were lost under a 10:1 majority
    # of things that are not wires.  What goes unmodelled is the via's
    # enclosure metal, whose extent lives in the DEF's VIAS section.
    d = _parse("""SPECIALNETS 1 ;
  - VDD ( * VDD ) + ROUTED metal6 1860 + SHAPE STRIPE ( 1000 1000 ) ( * 9000 )
      NEW metal6 0 + SHAPE STRIPE ( 1000 5000 ) via6_7_1860_2800_4_2_600_600
      + USE POWER ;
END SPECIALNETS
""")
    assert len(d.special_wires) == 1            # the stripe, not the via
    assert not _sn("SPECIALNETS.unread_wire", d)
    got = _sn("SPECIALNETS.via_placement", d)
    assert len(got) == 1 and "via6_7" in got[0].detail
    assert not _sn("SPECIALNETS.no_geometry", d)


def test_a_via_that_a_path_CONTINUES_past_is_not_a_placement():
    """The one-point prefix does not decide it — what follows the via does.

    `( 0 0 ) M5_M6 ( 0 0 ) ( * 1000 )` is a via at the START of a path, and
    the run after it is on the via's other layer: a MID-PATH via, which this
    reader does not represent.  Reading the prefix alone called it a
    placement, consumed the via name and dropped the run in SILENCE — worse
    than the truncation it replaced, since `unread_wire` at least said so
    (Codex on #765).
    """
    d = _parse("""SPECIALNETS 1 ;
  - VDD ( * VDD ) + ROUTED metal5 400 ( 0 0 ) M5_M6 ( 0 0 ) ( * 1000 )
      + USE POWER ;
END SPECIALNETS
""")
    assert not d.special_wires
    assert not _sn("SPECIALNETS.via_placement", d)
    got = _sn("SPECIALNETS.unread_wire", d)
    assert got and "M5_M6" in got[0].detail
    assert not _sn("SPECIALNETS.no_geometry", d)


@pytest.mark.parametrize("tail", [";", "+ USE POWER ;", "NEW metal5 400 ( 0 0 ) ( * 9 ) ;"])
def test_a_via_is_terminal_when_the_clause_ends_however_it_ends(tail):
    # The mirror of the test above: a via placement is one nothing follows on
    # the path.  `NEW` opens a new path, so it terminates this one — and the
    # via name must still be consumed, or that `NEW` is read as a via name.
    d = _parse(f"""SPECIALNETS 1 ;
  - VDD ( * VDD ) + ROUTED metal6 0 ( 100 200 ) via6_7_x {tail}
END SPECIALNETS
""")
    assert len(_sn("SPECIALNETS.via_placement", d)) == 1
    assert not _sn("SPECIALNETS.unread_wire", d)
    # The trailing NEW case really did read its own wire.
    assert len(d.special_wires) == (1 if tail.startswith("NEW") else 0)


def test_a_path_truncated_by_a_via_reports_the_part_it_dropped():
    # The run before the via is kept; everything past it is discarded — which
    # was entirely silent, since a kept wire looked like a complete read.
    d = _parse("""SPECIALNETS 1 ;
  - VDD ( * VDD ) + ROUTED metal5 400 ( 1000 1000 ) ( 9000 * )
      M5_M6 ( 9000 1000 ) ( * 9000 ) + USE POWER ;
END SPECIALNETS
""")
    assert len(d.special_wires) == 1
    assert list(d.special_wires[0].pts) == [(1000.0, 1000.0), (9000.0, 1000.0)]
    got = _sn("SPECIALNETS.partial_wire", d)
    assert got and "M5_M6" in got[0].detail     # names what defeated it


@pytest.mark.parametrize("form", [
    "RECT ( 1000 1000 ) ( 2000 9000 )",
    "POLYGON ( 0 0 ) ( 100 0 ) ( 100 50 )",
])
def test_a_special_wire_shape_the_reader_cannot_represent_is_reported(form):
    d = _parse(f"""SPECIALNETS 1 ;
  - VDD ( * VDD ) + ROUTED metal5 400 {form} + USE POWER ;
END SPECIALNETS
""")
    assert len(d.special_wires) == 0
    assert _sn("SPECIALNETS.unread_wire", d)
    assert not _sn("SPECIALNETS.no_geometry", d)


def test_a_net_that_really_has_no_wires_still_says_no_geometry():
    # The twin that keeps the rule honest: `no_geometry` is a claim about the
    # FILE, and it is TRUE here — connectivity and no `+ ROUTED` at all, which
    # is exactly what demo/ariane/ariane.def carries.  Suppressing it whenever
    # anything else improved would trade one wrong census for another.
    d = _parse("""SPECIALNETS 2 ;
  - VDD ( * VDD ) + USE POWER ;
  - VSS ( * VSS ) + USE GROUND ;
END SPECIALNETS
""")
    assert len(d.special_wires) == 0
    assert len(_sn("SPECIALNETS.no_geometry", d)) == 2
    assert not _sn("SPECIALNETS.unread_wire", d)


def test_a_property_VALUE_spelled_like_a_route_keyword_is_not_a_route():
    # The lexer strips the quotes, so `+ PROPERTY mode "ROUTED"` yields a
    # token identical to the keyword.  Matching by spelling would make this
    # net look like it had wiring — and since that decides whether it may say
    # `no_geometry`, a DEF the reader handled perfectly would be miscensused.
    d = _parse("""SPECIALNETS 1 ;
  - VDD ( * VDD ) + PROPERTY mode "ROUTED" + USE POWER ;
END SPECIALNETS
""")
    assert len(d.special_wires) == 0
    assert _sn("SPECIALNETS.no_geometry", d)     # this net really has none
    assert not _sn("SPECIALNETS.unread_wire", d)


def test_a_property_VALUE_of_NEW_does_not_forge_a_path_on_a_read_route():
    # The mirror: a route we read in full, followed by a property whose value
    # is `NEW`.  `NEW` continues a wiring clause, so one after `+ PROPERTY`
    # is not a path — counting it invents an unread wire on a clean import.
    d = _parse("""SPECIALNETS 1 ;
  - VDD ( * VDD ) + ROUTED metal5 400 ( 1000 1000 ) ( 9000 * )
      + PROPERTY mode "NEW" + USE POWER ;
END SPECIALNETS
""")
    assert len(d.special_wires) == 1
    assert not [u for u in d.unmodelled if u.construct.startswith("SPECIALNETS")]


def test_SHAPE_does_not_end_the_wiring_clause_a_following_NEW_continues():
    # `+ SHAPE`/`+ STYLE` sit INSIDE the wiring clause, so the `NEW` after one
    # is still a path.  If a `+` were taken to always end the clause, this
    # second stripe would not be read at all.
    d = _parse("""SPECIALNETS 1 ;
  - VDD ( * VDD ) + ROUTED metal5 400 + SHAPE STRIPE ( 1000 1000 ) ( 9000 * )
      NEW metal5 400 ( 1000 1000 ) ( * 9000 ) + USE POWER ;
END SPECIALNETS
""")
    assert len(d.special_wires) == 2
    assert [w.shape for w in d.special_wires] == ["STRIPE", ""]
    assert not [u for u in d.unmodelled if u.construct.startswith("SPECIALNETS")]


def test_a_wire_the_reader_reads_in_full_is_censused_as_nothing():
    # The false-positive guard: the plain form our own DEFs use must stay
    # silent, or every clean import grows a warning about metal it did read.
    d = _parse("""SPECIALNETS 1 ;
  - VDD ( * VDD ) + ROUTED metal5 400 ( 1000 1000 ) ( 9000 * )
      NEW metal5 400 ( 1000 1000 ) ( * 9000 ) + USE POWER ;
END SPECIALNETS
""")
    assert len(d.special_wires) == 2
    assert not [u for u in d.unmodelled if u.construct.startswith("SPECIALNETS")]


# ── failing loud ───────────────────────────────────────────────────────────

def test_a_section_closed_by_the_wrong_name_is_an_error():
    with pytest.raises(RuntimeError) as e:
        _parse("COMPONENTS 1 ;\n  - i0 m ;\nEND NETS\n")
    assert "mismatched section END" in str(e.value)


def test_an_unterminated_entry_is_an_error():
    with pytest.raises(RuntimeError):
        buda.parse_def("COMPONENTS 1 ;\n  - i0 m + PLACED ( 0 0 ) N\n", "t.def")


def test_read_def_reports_a_missing_file():
    with pytest.raises(RuntimeError) as e:
        buda.read_def("/nonexistent/x.def")
    assert "cannot open" in str(e.value)


# ── the real file the plan named ───────────────────────────────────────────

def test_the_checked_in_ariane_def_gives_up_nothing():
    """The plan's own example of what was being discarded: 20 TRACKS, 6
    GCELLGRIDs and 495 PINS in a file whose reader kept only components."""
    from pathlib import Path
    p = Path(__file__).parents[2] / "demo" / "ariane" / "ariane.def"
    d = buda.read_def(str(p))
    assert len(d.components) == d.declared_components == 133
    assert len(d.pins) == d.declared_pins == 495
    assert len(d.tracks) == 20
    assert len(d.gcellgrid) == 6


@pytest.mark.mid
def test_a_million_line_def_parses_in_seconds():
    """The plan's explicit acceptance criterion.  The reader has only ever
    run on a 3 878-line floorplan DEF; a real post-place DEF is 10^6-10^8
    lines, and per-line `std::regex` does not survive that.

    The bound here is deliberately loose (it runs on shared CI hardware) —
    it is there to catch a return to per-line regex, which was ~100x slower,
    not to police small changes."""
    n = 340_000                     # 3 lines each -> ~10^6 lines
    body = ["COMPONENTS %d ;" % n]
    for i in range(n):
        body.append(f"  - i{i}\n      m\n      + PLACED ( {i * 10} {i * 7} ) N ;")
    body.append("END COMPONENTS")
    text = _def("\n".join(body) + "\n")
    assert text.count("\n") > 1_000_000, text.count("\n")

    t0 = time.time()
    d = buda.parse_def(text, "big.def")
    dt = time.time() - t0
    assert len(d.components) == n
    assert dt < 30.0, f"{text.count(chr(10))} lines took {dt:.1f}s"
    print(f"\n[bench] {text.count(chr(10)):,} lines, {n:,} components "
          f"in {dt:.2f}s ({n / dt:,.0f} comps/s)")
