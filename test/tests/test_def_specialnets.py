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

"""The special-wire reader against REAL generator output.

`test_def_reader.py` pins the forms one clause at a time, in DEF this file's
author typed.  That is exactly the validation that failed for two years:
`opens_interchange.md` item 15 was invisible because the only power-routed
DEFs in the tree were ours, both written in the one form the reader handled,
so "the reader is complete" had been checked against the assumption it was
built on.

So these check the same reader against bytes written by pdngen — upstream's
own regression goldens, which before the item-15 fix parsed to **0 wires out
of 685**, every one defeated by the `+ SHAPE` clause DEF puts between the
width and the first point.

Two tiers, and the split is deliberate:

  * `pdngen_excerpt.def` is checked in (four clauses, quoted with
    attribution), so the claim "we read what a generator writes" is verified
    on every run, network or not.
  * the whole goldens are fetched, so the COUNTS are checked against a real
    grid's worth of geometry.  Skipped when absent, naming the command.

See test/tests/data/pdn_goldens/ReadMe.md.
"""
import contextlib
import io
from collections import Counter
from pathlib import Path

import pytest

import buda
import buda_cli

_DATA = Path(__file__).resolve().parent / "data" / "pdn_goldens"
_EXCERPT = _DATA / "pdngen_excerpt.def"

_FETCH = ("python3 test/tests/data/pdn_goldens/fetch.py")

# Independently classified from the goldens' text (a regex sweep over the
# SPECIALNETS section, not the reader under test), so agreement is evidence
# and not a tautology.  metal paths / via placements.
_GOLDENS = {
    "core_grid.defok":      (57, 0),
    "macros.defok":         (254, 1686),
    "existing.defok":       (278, 1806),
    "core_grid_snap.defok": (96, 3289),
}


def _census(d, kind):
    return [u for u in d.unmodelled if u.construct == f"SPECIALNETS.{kind}"]


# ── always run: real bytes, checked in ─────────────────────────────────────

def test_the_checked_in_excerpt_is_read_in_full():
    d = buda.read_def(str(_EXCERPT))
    assert [(w.layer, w.shape) for w in d.special_wires] == [
        ("metal1", "FOLLOWPIN"),      # a standard-cell rail
        ("metal6", "STRIPE"),         # a die-crossing stripe
        ("metal6", "RING"),           # a ring segment
    ]
    # Every one is a real run with real coordinates, not an empty shell that
    # merely survived the clause.
    assert all(len(w.pts) == 2 and w.width > 0 for w in d.special_wires)
    assert not _census(d, "unread_wire")
    # The fourth clause is a via placement, which is not a wire.
    assert len(_census(d, "via_placement")) == 1
    # And the net plainly HAS geometry, so it may not claim otherwise.
    assert not _census(d, "no_geometry")


def test_a_generator_written_stripe_becomes_a_keepout(tmp_path):
    """The point of reading it at all.

    A strap is metal a signal cannot use, so the wire has to reach the
    consumer — `bdb.cpp` turns each into a keepout tagged `SPECIALNET <net>`.
    Nothing pinned that composition before: the reader tests stopped at
    `special_wires` and the import tests never carried a special net, so a
    wire read and then dropped on the floor would have looked identical to
    the fix working.
    """
    lef = ("LAYER metal6\n  TYPE ROUTING ;\n  DIRECTION VERTICAL ;\n"
           "  PITCH 0.4 ;\n  WIDTH 0.2 ;\nEND metal6\nEND LIBRARY\n")
    deff = """VERSION 5.8 ;
DESIGN t ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 100000 100000 ) ;
SPECIALNETS 1 ;
  - VDD ( * VDD ) + ROUTED metal6 2000 + SHAPE STRIPE ( 50000 10000 ) ( * 90000 )
      + USE POWER ;
END SPECIALNETS
END DESIGN
"""
    (tmp_path / "m.lef").write_text(lef)
    (tmp_path / "d.def").write_text(deff)
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command(f"open_bdb {tmp_path / 'a.bdb'}")
        s.do_command(f"import_lef_tech {tmp_path / 'm.lef'}")
        s.do_command(f"import_def_lef {tmp_path / 'd.def'} {tmp_path / 'm.lef'}")

    boxes = [(z.bbox.x1, z.bbox.y1, z.bbox.x2, z.bbox.y2)
             for z in s.fp.get_keepout_zones()]
    # The wire is 2000 DBU wide at 1000 DBU/µm = 2 µm, running x=50 from
    # y=10 to y=90.  The consumer grows the segment by the half-width on
    # every side, so the run's ends carry an end cap too.
    assert (49, 9, 51, 91) in boxes, boxes


# ── fetched: the counts, on a real grid's worth of geometry ────────────────

def _golden(name):
    p = _DATA / name
    if not p.exists():
        pytest.skip(f"{name} not fetched — run: {_FETCH}")
    return buda.read_def(str(p))


@pytest.mark.parametrize("name", sorted(_GOLDENS))
def test_every_metal_path_in_a_pdngen_golden_is_read(name):
    metal, vias = _GOLDENS[name]
    d = _golden(name)
    assert len(d.special_wires) == metal
    assert len(_census(d, "via_placement")) == vias
    # The headline: not one path left unread.  Before the fix this was the
    # whole file.
    assert not _census(d, "unread_wire")
    # Nor may a net with thousands of wires be reported as drawing none.
    assert not _census(d, "no_geometry")


@pytest.mark.parametrize("name", sorted(_GOLDENS))
def test_a_golden_yields_only_shapes_a_generator_emits(name):
    d = _golden(name)
    shapes = Counter(w.shape for w in d.special_wires)
    assert set(shapes) <= {"STRIPE", "FOLLOWPIN", "RING"}, shapes
    # Every wire carries one: a PDN generator states the shape on all of
    # them, which is precisely why the missing clause cost 100% of them.
    assert "" not in shapes


def test_the_goldens_carry_no_form_the_reader_still_cannot_read():
    """The honest boundary.

    A via MID-path, `RECT` and `POLYGON` special wires are legal DEF and are
    still not represented — deliberately, because nothing available emits
    them, and item 12's lesson is that building against no evidence is how
    the wrong thing gets built.  If a future golden does carry one, this
    fails and the boundary gets revisited with a vehicle in hand rather than
    an assumption.
    """
    for name in sorted(_GOLDENS):
        d = _golden(name)
        assert not _census(d, "partial_wire"), name    # a via mid-path
        assert not _census(d, "unread_wire"), name     # RECT / POLYGON
