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

"""The import scale — one layout unit is whatever the design says (Phase 1a).

The gap it closes: DEF gives DBU integers, import divided them to double µm,
the BDB stored REAL, and the engine truncated to integer µm — throwing away
~2000 DBU per unit at a typical 2000 DBU/µm, roughly 20-25 track pitches on
an advanced node.  The algorithms never needed microns (the engine is
unit-agnostic); only the import convention did.

What has to hold: the default is bit-identical, DBU mode is EXACT, the scale
is a property of the stored design rather than of the session that imported
it, and everything derived from those coordinates moves with them — a scale
that reached the coordinates but not the pin positions or the cell sizes
would be worse than no scale at all.
"""
import textwrap

import buda

_LEF = ("MACRO m\n"
        "  SIZE 10 BY 4 ;\n"
        "  PIN a\n"
        "    DIRECTION INPUT ;\n"
        "    PORT\n"
        "      RECT 2 1 4 3 ;\n"      # centroid (3, 2) µm
        "    END\n"
        "  END a\n"
        "END m\n")

_UNITS = 2000            # DBU per micron — a typical advanced-node value


def _def(units=_UNITS):
    """Two placements whose µm coordinates are NOT integers: 3000/2000 = 1.5
    and 7001/2000 = 3.5005.  Integer-µm quantization is visible on both, so
    the fixture cannot pass by accident."""
    return textwrap.dedent(f"""\
        VERSION 5.8 ;
        UNITS DISTANCE MICRONS {units} ;
        DIEAREA ( 0 0 ) ( 40000 20000 ) ;
        COMPONENTS 2 ;
          - i0 m + PLACED ( 3000 3000 ) N ;
          - i1 m + PLACED ( 7001 5000 ) N ;
        END COMPONENTS
        NETS 1 ;
          - n0 ( i0 a ) ( i1 a ) ;
        END NETS
        END DESIGN
        """)


def _import(tmp_path, scale=None, name="a.bdb"):
    (tmp_path / "m.lef").write_text(_LEF)
    (tmp_path / "d.def").write_text(_def())
    db = buda.BDB(str(tmp_path / name))
    if scale == "dbu":
        db.set_import_scale_from_def_units()
    elif scale is not None:
        db.set_import_scale(scale)
    db.import_def_lef(str(tmp_path / "d.def"), str(tmp_path / "m.lef"))
    return db


def _by_name(db):
    return {c.name: c for c in db.all_components()}


# ── the default is the old behaviour ───────────────────────────────────────

def test_default_scale_is_microns(tmp_path):
    db = _import(tmp_path)
    assert db.import_scale() == 1.0
    c = _by_name(db)["i0"]
    assert (c.x1, c.y1) == (1.5, 1.5)          # 3000 DBU / 2000
    assert (c.x2 - c.x1, c.y2 - c.y1) == (10.0, 4.0)   # LEF SIZE, µm


def test_dbu_and_micron_modes_agree_on_the_geometry(tmp_path):
    """The scale must be a pure change of unit: every length in DBU mode is
    exactly `units` times its micron-mode value.  If it were not, the scale
    would be changing the design, not its units."""
    um = _by_name(_import(tmp_path, name="um.bdb"))
    dbu = _by_name(_import(tmp_path, scale="dbu", name="dbu.bdb"))
    for n in ("i0", "i1"):
        for a, b in ((um[n].x1, dbu[n].x1), (um[n].y1, dbu[n].y1),
                     (um[n].x2, dbu[n].x2), (um[n].y2, dbu[n].y2)):
            assert abs(a * _UNITS - b) < 1e-6, (n, a, b)


# ── what DBU mode buys ─────────────────────────────────────────────────────

def test_dbu_mode_is_exact_where_micron_mode_quantizes(tmp_path):
    """The point of the whole exercise.  i1 sits at 7001 DBU = 3.5005 µm; the
    engine takes int(round(...)) of whatever the BDB holds, so micron mode
    lands it on 4 — a 999 DBU error, ~20 track pitches on an advanced node.
    In DBU mode the stored value IS the integer the DEF wrote."""
    um = _by_name(_import(tmp_path, name="um.bdb"))["i1"]
    dbu = _by_name(_import(tmp_path, scale="dbu", name="dbu.bdb"))["i1"]
    assert dbu.x1 == 7001.0
    assert int(round(dbu.x1)) == 7001            # no loss at all
    assert um.x1 == 3.5005 and int(round(um.x1)) == 4
    lost_dbu = abs(int(round(um.x1)) * _UNITS - 7001)
    assert lost_dbu > 900


def test_pin_positions_and_cell_sizes_scale_with_the_coordinates(tmp_path):
    """LEF is microns while DEF is DBU, so they convert by DIFFERENT factors
    — the easy bug is to scale one and not the other, which puts every pin
    somewhere near the origin and leaves cells microscopic."""
    db = _import(tmp_path, scale="dbu")
    c = _by_name(db)["i0"]
    assert (c.x2 - c.x1, c.y2 - c.y1) == (20000.0, 8000.0)   # 10x4 µm in DBU
    pins = [p for p in db.all_pins() if p.pin_name == "a"]
    assert pins, "no pins imported"
    p0 = [p for p in pins if p.comp_id == c.id][0]
    assert abs(p0.px - (3000 + 3 * _UNITS)) < 1e-6           # centroid 3 µm
    assert abs(p0.py - (3000 + 2 * _UNITS)) < 1e-6


def test_die_area_scales_too(tmp_path):
    db = _import(tmp_path, scale="dbu")
    assert (db.die_w(), db.die_h()) == (40000.0, 20000.0)


# ── the scale belongs to the design, not the session ───────────────────────

def test_scale_is_persisted_and_restored(tmp_path):
    """A reopened BDB must know what its own numbers mean; otherwise a
    DBU-scale design read back as microns is wrong by 2000x everywhere, with
    nothing to say so."""
    path = str(tmp_path / "p.bdb")
    _import(tmp_path, scale="dbu", name="p.bdb")
    del_db = buda.BDB(path)
    assert del_db.import_scale() == float(_UNITS)


def test_dbu_mode_records_a_number_not_a_mode(tmp_path):
    """`dbu` resolves against the DEF being imported.  Persisting the MODE
    would re-resolve it against whatever DEF is imported next, silently
    restating the old coordinates in a new unit."""
    db = _import(tmp_path, scale="dbu")
    assert db.meta_get("lu_per_um").startswith(str(_UNITS))


def test_explicit_scale_is_accepted_and_validated(tmp_path):
    db = _import(tmp_path, scale=1000.0)
    assert db.import_scale() == 1000.0
    assert _by_name(db)["i0"].x1 == 1500.0        # 1.5 µm at 1000 lu/µm
    for bad in (0.0, -1.0):
        try:
            db.set_import_scale(bad)
        except Exception as e:
            assert "positive" in str(e)
        else:
            raise AssertionError(f"set_import_scale({bad}) was accepted")
