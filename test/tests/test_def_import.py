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

"""What `import_def_lef` does with what the DEF reader gives it (Phase 3).

`test_def_reader.py` pins the parsing.  This pins the PROJECTION: which of
those facts reach the database, which reach the session, and — the part that
mattered most — which of them are now impossible to get wrong in silence.
"""
import contextlib
import io
import textwrap

import pytest

import buda
import buda_cli

_LEF = """MACRO m
  SIZE 10 BY 4 ;
  PIN a
    DIRECTION INPUT ;
    PORT
      LAYER metal1 ;
      RECT 4 1 6 3 ;
    END
  END a
END m
LAYER metal2
  TYPE ROUTING ;
  DIRECTION HORIZONTAL ;
  PITCH 0.4 ;
  WIDTH 0.2 ;
END metal2
LAYER metal3
  TYPE ROUTING ;
  DIRECTION VERTICAL ;
  PITCH 0.4 ;
  WIDTH 0.2 ;
END metal3
END LIBRARY
"""

_DEF = """VERSION 5.8 ;
DESIGN t ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 100000 100000 ) ;
TRACKS Y 500 DO 10 STEP 400 LAYER metal2 ;
COMPONENTS 2 ;
  - i0 m + PLACED ( 1000 2000 ) N ;
  - i1 m + PLACED ( 50000 60000 ) N + HALO 100 100 100 100 ;
END COMPONENTS
PINS 1 ;
  - out + NET n0 + DIRECTION OUTPUT + LAYER metal3 ( -50 -50 ) ( 50 50 )
      + PLACED ( 99000 50000 ) N ;
END PINS
BLOCKAGES 1 ;
  - LAYER metal2 RECT ( 20000 20000 ) ( 30000 30000 ) ;
END BLOCKAGES
NETS 1 ;
  - n0 ( i0 a ) ( PIN out ) ;
END NETS
END DESIGN
"""


def _run(tmp_path, extra="", lef=_LEF, deff=_DEF, tech=True):
    (tmp_path / "m.lef").write_text(lef)
    (tmp_path / "d.def").write_text(deff)
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = [f"open_bdb {tmp_path / 'a.bdb'}"]
    if tech:
        cmds.append(f"import_lef_tech {tmp_path / 'm.lef'}")
    cmds.append(f"import_def_lef {tmp_path / 'd.def'} "
                f"{tmp_path / 'm.lef'} {extra}".rstrip())
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for c in cmds:
            s.do_command(c)
    return s, buf.getvalue()


# ── the counts, reconciled ─────────────────────────────────────────────────

def test_the_import_reports_what_it_read_against_what_was_declared(tmp_path):
    _s, out = _run(tmp_path)
    assert "components: imported 2 of 2" in out, out
    assert "ports: imported 1 of 1" in out, out
    assert "nets: imported 1 of 1" in out, out


def test_a_count_mismatch_is_marked(tmp_path):
    """A DEF states its own counts.  Ignoring them means a truncated file and
    a complete one look the same — and a half-read floorplan is still a
    floorplan."""
    _s, out = _run(tmp_path, deff=_DEF.replace("COMPONENTS 2 ;", "COMPONENTS 5 ;"))
    assert "imported 2 of 5" in out and "MISMATCH" in out, out


def test_a_cell_with_no_lef_footprint_stops_the_run(tmp_path):
    """The silent 0.5 x 0.5 um fallback turned a wrong-LEF run into a
    plausible, entirely wrong floorplan: every macro a speck, every route
    between them meaningless, and no diagnostic anywhere.

    This is not hypothetical — the checked-in demo/ariane pair has exactly
    this defect, and nothing reported it until now."""
    with pytest.raises(SystemExit):
        _run(tmp_path, deff=_DEF.replace("- i0 m +", "- i0 nosuchcell +"))


def test_a_missing_lef_file_is_an_error_like_a_missing_def(tmp_path):
    """An ABSENT LEF used to be tolerated silently while the DEF has always
    thrown — an asymmetry inherited from the old line scanners, not chosen.
    It failed in two shapes: with a populated DEF, every cell drew the
    missing-footprint error whose advice ("check that the LEF matches this
    DEF") misdiagnoses a file that is not there at all; and under
    `allow_missing_footprints`, a TYPO'D LEF PATH silently produced the
    all-specks floorplan that waiver exists to make explicit-only — the
    waiver-user accepted an incomplete library, not an unread one."""
    import buda
    db = buda.BDB(str(tmp_path / "a.bdb"))
    (tmp_path / "d.def").write_text(_DEF)
    with pytest.raises(RuntimeError, match="cannot open.*no_such"):
        db.import_def_lef(str(tmp_path / "d.def"),
                          str(tmp_path / "no_such.lef"))

    # …and the waiver does NOT reach past it: the file must exist for the
    # question "tolerate its gaps?" to arise at all.
    s = buda_cli.BudaSession()
    s.no_viz = True
    err = None
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            s.do_command(f"open_bdb {tmp_path / 'b.bdb'}")
            s.do_command(f"import_def_lef {tmp_path / 'd.def'} "
                         f"{tmp_path / 'no_such.lef'} "
                         f"allow_missing_footprints")
        except RuntimeError as e:
            err = e
    assert err is not None and "no_such" in str(err)


def test_the_stop_can_be_overridden_but_only_out_loud(tmp_path):
    _s, out = _run(tmp_path, extra="allow_missing_footprints",
                   deff=_DEF.replace("- i0 m +", "- i0 nosuchcell +"))
    # The waiver does not make the fault disappear, it downgrades it — and
    # the downgraded form has its OWN id (BUDA-1606), so a methodology can
    # allow the waiver and still find every design that used it.
    assert "BUDA-1606: WARNING:" in out, out
    assert "geometry of those instances is fiction" in out


# ── what reaches the database ──────────────────────────────────────────────

def test_components_and_their_footprints(tmp_path):
    s, _ = _run(tmp_path)
    comps = {c.name: c for c in s.bdb.all_components()}
    # Two instances plus the boundary component for the die port, which
    # carries its own collision-proof name (ports and instances are separate
    # DEF namespaces).
    assert {n for n, c in comps.items() if not c.is_port} == {"i0", "i1"}
    assert len([c for c in comps.values() if c.is_port]) == 1
    assert (comps["i0"].x1, comps["i0"].y1) == (1.0, 2.0)      # DBU / 1000
    assert (comps["i0"].x2 - comps["i0"].x1) == 10.0           # LEF SIZE


def test_a_die_port_becomes_a_component_flagged_as_one(tmp_path):
    """Reading PINS does not make them endpoints — endpoint derivation is
    keyed by component, so a net reaching the die edge would be silently
    incomplete.  The port becomes a component so it is routable, and carries
    `is_port` so the fiction cannot pass as a real instance."""
    s, _ = _run(tmp_path)
    ports = [c for c in s.bdb.all_components() if c.is_port]
    assert len(ports) == 1
    port = ports[0]
    assert "out" in port.name          # the DEF port it stands for
    assert port.cell == "__PORT__"
    # …and it is where the DEF put it, grown by its port shape.
    assert port.x1 == pytest.approx(99.0 - 0.05)
    assert port.x2 == pytest.approx(99.0 + 0.05)


def test_a_net_reaching_the_die_edge_connects_to_the_port(tmp_path):
    """The whole point of 3d: `( PIN out )` has to resolve to something the
    pipeline can route to."""
    s, _ = _run(tmp_path)
    by_id = {c.id: c for c in s.bdb.all_components()}
    pins = [(by_id[p.comp_id], p.pin_name) for p in s.bdb.all_pins()]
    assert ("i0", "a") in [(c.name, n) for c, n in pins]
    assert any(c.is_port and n == "out" for c, n in pins)


# ── what reaches the session ───────────────────────────────────────────────

def test_def_tracks_install_a_bounded_pattern(tmp_path):
    """A `TRACKS … DO n STEP s` is an ENUMERATION, not a rule: tiling past
    the declared range invents tracks the technology does not have, silently
    and in the direction of optimism."""
    s, out = _run(tmp_path)
    assert "tracks installed (bounded)" in out, out
    lid = s._layer_name_map["metal2"]
    pat = s.routing_grid.get_layer_grid(lid).global_pattern()
    assert pat.bounded
    assert pat.bound_lo == pytest.approx(0.5)          # 500 DBU
    assert pat.bound_hi == pytest.approx(0.5 + 0.4 * 9)
    assert len(pat.tracks_in_range(100.0, 200.0)) == 0   # far outside: none


def test_hard_layer_blockages_become_keepouts(tmp_path):
    """The keepout machinery already existed; its absence here was the single
    most surprising omission for a routing tool — a router that cannot see a
    blockage plans through it."""
    s, out = _run(tmp_path)
    assert "keepouts added" in out, out
    zones = s.fp.get_keepout_zones()
    assert zones, "no keepouts installed"
    # The BLOCKAGES rect, in layout units.
    boxes = [(z.bbox.x1, z.bbox.y1, z.bbox.x2, z.bbox.y2) for z in zones]
    assert (20, 20, 30, 30) in boxes, boxes


def test_a_component_halo_is_not_a_routing_keepout(tmp_path):
    """REVERSES what this file used to assert, and the old comment said why
    it was wrong: "the HALO ring around i1, which the placer honoured and the
    router must too".  The router must NOT.

    DEF has two halos and they are different constraints:

      * `+ HALO [SOFT] l b r t` keeps other CELLS away.  It is placement
        information and carries NO layer — so a layerless keepout maps onto
        every routing layer and forbids routing an area the DEF left
        completely routable.
      * `+ ROUTEHALO dist minLayer maxLayer` is the routing one, and names
        the layers it applies to.

    We honoured the placement construct as routing and ignore the routing
    construct — backwards, and the same mistake already recorded for
    PLACEMENT blockages, which are dropped for exactly this reason.

    Measured on `flow/ariane133`: 133 macros each carrying `HALO 10000`
    (5 um a side) produced 195 track overlaps and 83 supply-doomed seats with
    ZERO signal tracks in their placed windows — on layers carrying 4,848
    tracks.  Dropping them takes that flow to 0 overlaps with its OBS
    obstruction model still fully enforced.
    """
    s, out = _run(tmp_path)
    boxes = [(z.bbox.x1, z.bbox.y1, z.bbox.x2, z.bbox.y2)
             for z in s.fp.get_keepout_zones()]
    # i1 sits at 50000 DBU = 50 lu; its halo ring would start just inside.
    assert not any(b[0] in (49, 50) for b in boxes), \
        f"a placement halo became a routing keepout: {boxes}"


def test_the_halo_is_reported_rather_than_silently_dropped(tmp_path):
    """Not applying it is not the same as not reading it.  A halo is real
    placement information BUDA has no stage to apply, so it is censused —
    beside BLOCKAGES.PLACEMENT, which it is the same kind of thing as."""
    s, out = _run(tmp_path)
    assert "COMPONENTS.HALO" in out, out


def test_an_unplaced_component_s_halo_is_censused_too(tmp_path):
    """The census asks "what did the file say that we do not model?", and
    placement has no bearing on that answer.

    The first cut gated the count on `has_pos` — a leftover from the days
    when this emitted a keepout, which genuinely needs a position.  A census
    does not, and the gate left exactly the unreported case the census
    exists to prevent: an UNPLACED component whose halo is read, deliberately
    not applied, and never mentioned (Codex P2 on #739).
    """
    s, out = _run(tmp_path, deff=_DEF_UNPLACED)
    assert "COMPONENTS.HALO" in out, out
    # …and it is still reported as unplaced, which is a separate fact.
    assert "COMPONENTS.UNPLACED" in out, out


def test_the_physical_import_can_be_declined(tmp_path):
    s, out = _run(tmp_path, extra="no_tracks no_blockages")
    assert "tracks installed" not in out
    assert "keepouts added" not in out
    assert not s.fp.get_keepout_zones()


def test_a_script_declared_pattern_outranks_the_def(tmp_path):
    """Same precedence rule as the LEF tech import: what the script says
    wins."""
    (tmp_path / "m.lef").write_text(_LEF)
    (tmp_path / "d.def").write_text(_DEF)
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for c in [f"open_bdb {tmp_path / 'a.bdb'}",
                  "def_layer 2 metal2 H TOP 30",
                  "def_track_pattern 2 0 VDD 2 1 _ 1 1 GND 2 1",
                  f"import_def_lef {tmp_path / 'd.def'} {tmp_path / 'm.lef'}"]:
            s.do_command(c)
    out = buf.getvalue()
    assert "script-declared pattern wins" in out, out
    pat = s.routing_grid.get_layer_grid(2).global_pattern()
    assert not pat.bounded and pat.unit_pitch() == pytest.approx(8.0)


# ── review findings (Codex on #647) ────────────────────────────────────────

_BOTH_DIRS = _DEF.replace(
    "TRACKS Y 500 DO 10 STEP 400 LAYER metal2 ;",
    "TRACKS Y 500 DO 10 STEP 400 LAYER metal2 metal3 ;\n"
    "TRACKS X 300 DO 20 STEP 200 LAYER metal2 metal3 ;")


def test_only_the_routing_direction_grid_is_installed(tmp_path):
    """A DEF supplies TRACKS in BOTH directions for most layers — the
    off-direction ones exist for pin access and vias, not routing.  Taking
    every statement meant the LAST one won, because `define_layer` replaces
    the orientation too: every mapped layer ended up on its Y grid and marked
    horizontal, including layers the technology declares vertical."""
    s, out = _run(tmp_path, deff=_BOTH_DIRS)
    h_id = s._layer_name_map["metal2"]        # HORIZONTAL in the tech LEF
    v_id = s._layer_name_map["metal3"]        # VERTICAL
    assert s.layers.get_layer_dir(h_id) == buda.LayerDir.HORIZONTAL
    assert s.layers.get_layer_dir(v_id) == buda.LayerDir.VERTICAL
    assert s.routing_grid.get_layer_grid(h_id).is_horizontal() is True
    assert s.routing_grid.get_layer_grid(v_id).is_horizontal() is False
    # …and each took ITS grid: metal2 the Y one (step 400), metal3 the X one.
    assert s.routing_grid.get_layer_grid(h_id).global_pattern().unit_pitch() \
        == pytest.approx(400.0 / 1000)
    assert s.routing_grid.get_layer_grid(v_id).global_pattern().unit_pitch() \
        == pytest.approx(200.0 / 1000)


def test_a_def_track_start_is_a_track_CENTRE(tmp_path):
    """`TRACKS <start>` is the centreline of the first track, while
    TrackPattern's origin is the START of its first slot and the generator
    returns `origin + width/2`.  Anchoring at `start` shifted every imported
    track by half its width."""
    s, _ = _run(tmp_path)
    lid = s._layer_name_map["metal2"]
    pat = s.routing_grid.get_layer_grid(lid).global_pattern()
    centres = [c for c, _slot in pat.tracks_in_range(-10.0, 10.0)]
    assert centres, "no tracks generated"
    assert centres[0] == pytest.approx(0.5)         # 500 DBU, as declared
    assert len(centres) == 10                       # DO 10 — all of them
    assert centres[-1] == pytest.approx(0.5 + 0.4 * 9)


def test_a_port_named_like_an_instance_keeps_its_own_identity(tmp_path):
    """DEF ports and instances are separate namespaces, so a legal design may
    name both the same.  Under one namespace the port row was dropped by
    INSERT OR IGNORE and `( PIN i0 )` resolved to the real instance —
    attaching the boundary connection to the wrong component, with is_port
    never set."""
    clash = _DEF.replace("- out + NET n0", "- i0 + NET n0") \
                .replace("( PIN out )", "( PIN i0 )")
    s, _ = _run(tmp_path, deff=clash)
    comps = {c.name: c for c in s.bdb.all_components()}
    inst = [c for c in comps.values() if not c.is_port and c.name == "i0"]
    ports = [c for c in comps.values() if c.is_port]
    assert len(inst) == 1 and len(ports) == 1
    assert ports[0].name != "i0"          # its own, collision-proof identity
    # The net's boundary connection went to the PORT, not to the instance.
    by_id = {c.id: c for c in comps.values()}
    port_pins = [p for p in s.bdb.all_pins() if by_id[p.comp_id].is_port]
    assert len(port_pins) == 1


def test_macro_obstructions_follow_the_instance_orientation(tmp_path):
    """An OBS rect is in the MACRO's frame.  Translating it without applying
    the instance's orientation puts the keepout where the obstruction is not
    — blocking empty space while the real obstruction stays routable."""
    # count=1: "END m" is also a prefix of "END metal2"/"END metal3", so an
    # unbounded replace would corrupt the tech layers below.
    lef = _LEF.replace("END m\n", "  OBS\n    LAYER metal2 ;\n"
                                  "    RECT 0 0 2 1 ;\n  END\nEND m\n", 1)
    # The macro is 10 x 4; rotated 90 (W) its placed box is 4 x 10, and the
    # obstruction that was 2 wide x 1 tall at the origin becomes 1 x 2.
    rot = _DEF.replace("- i0 m + PLACED ( 1000 2000 ) N ;",
                       "- i0 m + PLACED ( 1000 2000 ) W ;")
    s, _ = _run(tmp_path, lef=lef, deff=rot)
    obs = [(z.bbox.x1, z.bbox.y1, z.bbox.x2, z.bbox.y2)
           for z in s.fp.get_keepout_zones()]
    # Unrotated it would be (1,2)-(3,3); rotated it is a 1 x 2 box.
    rotated = [b for b in obs if (b[2] - b[0]) == 1 and (b[3] - b[1]) == 2]
    assert rotated, f"no rotated OBS keepout among {obs}"


# ── what a blockage MEANS (Codex P1 on #649) ───────────────────────────────

_DEF_SOFT = _DEF.replace(
    """BLOCKAGES 1 ;
  - LAYER metal2 RECT ( 20000 20000 ) ( 30000 30000 ) ;
END BLOCKAGES""",
    """BLOCKAGES 3 ;
  - LAYER metal2 RECT ( 20000 20000 ) ( 30000 30000 ) ;
  - PLACEMENT RECT ( 40000 40000 ) ( 45000 45000 ) ;
  - LAYER metal3 + PARTIAL 0.6 RECT ( 60000 60000 ) ( 65000 65000 ) ;
END BLOCKAGES""")


def _boxes(session):
    return [(z.bbox.x1, z.bbox.y1, z.bbox.x2, z.bbox.y2)
            for z in session.fp.get_keepout_zones()]


def test_a_placement_blockage_is_not_a_routing_keepout(tmp_path):
    """A DEF PLACEMENT blockage says where CELLS may not go.  It carries no
    layer, and a layerless keepout is installed on EVERY routing layer, so
    importing one forbade signal routing through an area the file left
    completely routable.

    The round trip is what makes it acute rather than academic: Phase 4b
    emits `PLACEMENT + PARTIAL` blockages over its own corridors, so
    BUDA -> DEF -> BUDA turned each planned corridor into a hard keepout
    against itself — the exact inversion Phase 4 exists to prevent."""
    s, out = _run(tmp_path, deff=_DEF_SOFT)
    boxes = _boxes(s)
    assert (20, 20, 30, 30) in boxes, boxes         # the hard one still lands
    assert (40, 40, 45, 45) not in boxes, boxes     # the placement one does not


def test_a_partial_density_blockage_is_not_a_prohibition(tmp_path):
    """`+ PARTIAL <density>` caps how full a region may get.  A hard keepout
    over-blocks it in the same direction, so it is recorded rather than
    approximated."""
    s, _out = _run(tmp_path, deff=_DEF_SOFT)
    assert (60, 60, 65, 65) not in _boxes(s), _boxes(s)


def test_what_is_read_but_not_applied_is_still_reported(tmp_path):
    """The phase's own gate: loud on the unmodelled.  A blockage silently
    DROPPED is exactly as misleading as one silently misapplied."""
    _s, out = _run(tmp_path, deff=_DEF_SOFT)
    assert "BLOCKAGES.PLACEMENT:1" in out, out
    assert "BLOCKAGES.PARTIAL:1" in out, out


# ── UNPLACED components (Codex P1 on #649) ─────────────────────────────────

_DEF_UNPLACED = _DEF.replace(
    "  - i1 m + PLACED ( 50000 60000 ) N + HALO 100 100 100 100 ;",
    "  - i1 m + UNPLACED + HALO 100 100 100 100 ;")


def test_an_unplaced_component_does_not_land_on_the_origin(tmp_path):
    """`UNPLACED` means the component has no coordinates, and the reader's
    defaults are (0,0) — so a normal bbox puts EVERY unplaced instance on
    top of every other one at the die origin, and the pipeline routes that
    pile as though it were a floorplan.  A note printed afterwards does not
    undo geometry the next stage has already believed.

    -1,-1,-1,-1 is the repo's existing "no placement" convention, written by
    `import_verilog` for a component it knows only from the netlist; reusing
    it keeps ONE meaning of unplaced across both importers."""
    s, out = _run(tmp_path, deff=_DEF_UNPLACED)
    comps = {c.name: c for c in s.bdb.all_components()}
    assert comps["i0"].x1 == pytest.approx(1.0)        # placed: real position
    u = comps["i1"]
    assert (u.x1, u.y1, u.x2, u.y2) == (-1, -1, -1, -1), \
        (u.x1, u.y1, u.x2, u.y2)
    assert "1 component(s) are UNPLACED" in out, out


def test_an_unplaced_component_brings_no_geometry_with_it(tmp_path):
    """Its halo and its macro obstructions have nowhere to be either — and a
    keepout at the origin is worse than none, because it blocks real area."""
    s, _out = _run(tmp_path, deff=_DEF_UNPLACED)
    for b in _boxes(s):
        assert not (b[0] <= 0 and b[1] <= 0), \
            f"keepout {b} sits at the origin — from an unplaced instance?"


def test_an_unplaced_instances_pin_has_no_position_but_keeps_its_direction(
        tmp_path):
    """Direction comes from the cell, so it is known whether or not the
    instance is placed; the absolute position is not.  -1 is what the column
    already means by unknown."""
    deff = _DEF_UNPLACED.replace("  - n0 ( i0 a ) ( PIN out ) ;",
                                 "  - n0 ( i1 a ) ( PIN out ) ;")
    s, _out = _run(tmp_path, deff=deff)
    cid = {c.name: c.id for c in s.bdb.all_components()}["i1"]
    pins = [p for p in s.bdb.pins_by_comp(cid) if p.pin_name == "a"]
    assert pins, "the connection was dropped entirely"
    assert pins[0].dir == "INPUT", pins[0].dir
    assert (pins[0].px, pins[0].py) == (-1, -1), (pins[0].px, pins[0].py)


# ── the streaming import's contracts (opens item 5) ────────────────────────
# The import consumes COMPONENTS/NETS entries as they parse and resolves
# connections against the database's own name index; what could not resolve
# mid-file is deferred, and the whole import lives in one transaction.

def test_section_order_does_not_change_what_resolves(tmp_path):
    """NETS before COMPONENTS: the spec orders sections, correctness must
    not hang on it.  Every connection lands in the deferred buffer and
    resolves after the parse — identical rows to the well-ordered file."""
    # Move the NETS section ahead of COMPONENTS wholesale.
    head, comps, tail = _DEF.partition("COMPONENTS 2 ;")
    comps_block, netskw, nets_block = (comps + tail).partition("NETS 1 ;")
    nets_sec = netskw + nets_block.split("END NETS")[0] + "END NETS\n"
    reordered = head + nets_sec + comps_block + \
        nets_block.split("END NETS")[1]
    assert reordered.index("NETS 1 ;") < reordered.index("COMPONENTS 2 ;")
    s_good, _ = _run(tmp_path, deff=_DEF)
    good_cid = {c.name: c.id for c in s_good.bdb.all_components()}["i0"]
    good = [(p.pin_name, p.dir, p.px, p.py)
            for p in s_good.bdb.pins_by_comp(good_cid)]
    (tmp_path / "r").mkdir()
    s_re, _ = _run(tmp_path / "r", deff=reordered)
    re_cid = {c.name: c.id for c in s_re.bdb.all_components()}["i0"]
    got = [(p.pin_name, p.dir, p.px, p.py)
           for p in s_re.bdb.pins_by_comp(re_cid)]
    assert got == good and good, (got, good)
    # ...and the die-port connection resolved too.
    ports = [c for c in s_re.bdb.all_components() if c.is_port]
    assert len(ports) == 1
    assert s_re.bdb.pins_by_comp(ports[0].id), "port connection dropped"


def test_a_malformed_def_rolls_back_to_the_previous_design(tmp_path):
    """The design wipe now happens inside the import's transaction, before
    the streaming parse — so a parse error must ROLL BACK and leave the
    previously imported design intact, exactly as it was when the wipe only
    ran after a successful parse."""
    s, _ = _run(tmp_path)
    before = sorted(c.name for c in s.bdb.all_components())
    assert before, "the good import produced nothing"
    bad = _DEF.replace("  - i1 m + PLACED ( 50000 60000 ) N "
                       "+ HALO 100 100 100 100 ;",
                       "  - i1 m + PLACED ( 50000")   # truncated mid-entry
    # ...and give the doomed file a DIFFERENT scale: the rollback must
    # restore the OBJECT too — latch_units takes the rejected file's
    # divisor as soon as an entry streams, and left stale it would scale
    # a later UNITS-less import by the failed file's units (Codex P2).
    bad = bad.replace("UNITS DISTANCE MICRONS 1000 ;",
                      "UNITS DISTANCE MICRONS 2000 ;")
    (tmp_path / "bad.def").write_text(bad)
    with pytest.raises(RuntimeError):
        s.bdb.import_def_lef(str(tmp_path / "bad.def"),
                             str(tmp_path / "m.lef"))
    after = sorted(c.name for c in s.bdb.all_components())
    assert after == before, (after, before)
    # A re-import that states no UNITS scales under the SURVIVING design's
    # divisor (1000), not the rejected file's (2000): i0 at DBU 1000 is
    # 1.0 um, not 0.5.
    unitless = "\n".join(l for l in _DEF.splitlines()
                         if not l.startswith("UNITS")) + "\n"
    (tmp_path / "u.def").write_text(unitless)
    s.bdb.import_def_lef(str(tmp_path / "u.def"), str(tmp_path / "m.lef"))
    i0 = {c.name: c for c in s.bdb.all_components()}["i0"]
    assert i0.x1 == pytest.approx(1.0), i0.x1


def test_units_after_a_streamed_entry_is_a_hard_error(tmp_path):
    """Streaming converts coordinates at delivery time, so a UNITS statement
    after a COMPONENTS entry has already streamed cannot be applied
    retroactively — refused loud, never two scales in one import."""
    head, mid = _DEF.split("UNITS DISTANCE MICRONS 1000 ;\n")
    late_units = head + mid.replace(
        "END COMPONENTS\n",
        "END COMPONENTS\nUNITS DISTANCE MICRONS 1000 ;\n")
    (tmp_path / "m.lef").write_text(_LEF)
    (tmp_path / "late.def").write_text(late_units)
    b = buda.BDB(str(tmp_path / "b.bdb"))
    with pytest.raises(RuntimeError, match="UNITS"):
        b.import_def_lef(str(tmp_path / "late.def"), str(tmp_path / "m.lef"))
