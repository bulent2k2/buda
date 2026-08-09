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


def test_the_stop_can_be_overridden_but_only_out_loud(tmp_path):
    _s, out = _run(tmp_path, extra="allow_missing_footprints",
                   deff=_DEF.replace("- i0 m +", "- i0 nosuchcell +"))
    assert "Warning" in out and "geometry of those instances is fiction" in out


# ── what reaches the database ──────────────────────────────────────────────

def test_components_and_their_footprints(tmp_path):
    s, _ = _run(tmp_path)
    comps = {c.name: c for c in s.bdb.all_components()}
    assert set(comps) == {"i0", "i1", "out"}
    assert (comps["i0"].x1, comps["i0"].y1) == (1.0, 2.0)      # DBU / 1000
    assert (comps["i0"].x2 - comps["i0"].x1) == 10.0           # LEF SIZE


def test_a_die_port_becomes_a_component_flagged_as_one(tmp_path):
    """Reading PINS does not make them endpoints — endpoint derivation is
    keyed by component, so a net reaching the die edge would be silently
    incomplete.  The port becomes a component so it is routable, and carries
    `is_port` so the fiction cannot pass as a real instance."""
    s, _ = _run(tmp_path)
    port = [c for c in s.bdb.all_components() if c.name == "out"][0]
    assert port.is_port
    assert not any(c.is_port for c in s.bdb.all_components() if c.name != "out")
    # …and it is where the DEF put it, grown by its port shape.
    assert port.x1 == pytest.approx(99.0 - 0.05)
    assert port.x2 == pytest.approx(99.0 + 0.05)


def test_a_net_reaching_the_die_edge_connects_to_the_port(tmp_path):
    """The whole point of 3d: `( PIN out )` has to resolve to something the
    pipeline can route to."""
    s, _ = _run(tmp_path)
    comps = {c.id: c.name for c in s.bdb.all_components()}
    pins = [(comps.get(p.comp_id), p.pin_name) for p in s.bdb.all_pins()]
    assert ("i0", "a") in pins
    assert ("out", "out") in pins


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


def test_blockages_and_halos_become_keepouts(tmp_path):
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
    # …and the HALO ring around i1, which the placer honoured and the router
    # must too: 50000 DBU = 50 lu, minus the 0.1 lu halo.
    assert any(b[0] == 49 or b[0] == 50 for b in boxes), boxes


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
