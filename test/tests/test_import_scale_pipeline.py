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

"""The import scale end to end: DEF → plan → NUTS at two scales (Phase 1a).

`test_import_scale.py` pins the conversion.  This pins the thing the review
of the plan actually worried about: **scaling the coordinates is not enough**.
Bus widths, track pitches and every script-declared distance are physical
quantities too, and a scale that moved the coordinates while leaving those
behind would produce a run that looks gloriously routable and means nothing —
at 2000 DBU/µm a bus would reserve ~1/2000 of the space it needs.

So the test runs the SAME design twice — once in microns, once in DBU with
the script-declared distances scaled to match — and requires the routed
result to be the same route in a different unit: identical bundle and segment
counts, identical placement quality, and an abstract wirelength that scales by
exactly the factor.  A width model left in the old unit cannot pass this: it
would change which candidate the planner picks, and the wirelengths would
stop being proportional.
"""
import contextlib
import io
import textwrap

import pytest

# Full-pipeline integration (DEF import → planner → NUTS), mid tier.
pytestmark = pytest.mark.mid

import buda_cli

_UNITS = 2000                       # DEF database units per micron
_SCALE = float(_UNITS)

_LEF = "\n".join(
    f"MACRO m{i}\n  SIZE 60 BY 40 ;\n"
    "  PIN p\n    DIRECTION INOUT ;\n    PORT\n      RECT 28 18 32 22 ;\n"
    "    END\n  END p\nEND m{i}\n".replace("{i}", str(i))
    for i in range(4))

_PLACE = [("i0", 0, 0), ("i1", 600_000, 0),
          ("i2", 0, 400_000), ("i3", 600_000, 400_000)]   # DBU


def _def_text():
    # Built line by line, NOT via textwrap.dedent: the DEF section headers
    # must start at column 0 (`^COMPONENTS \d+ ;`), and a dedent whose
    # substituted rows begin at column 0 leaves the literal lines indented.
    return "\n".join([
        "VERSION 5.8 ;",
        f"UNITS DISTANCE MICRONS {_UNITS} ;",
        "DIEAREA ( 0 0 ) ( 800000 600000 ) ;",
        f"COMPONENTS {len(_PLACE)} ;",
        *(f"  - {n} m{k} + PLACED ( {x} {y} ) N ;"
          for k, (n, x, y) in enumerate(_PLACE)),
        "END COMPONENTS",
        "NETS 8 ;",
        *(f"  - b_{b} ( i0 p ) ( i3 p ) ;" for b in range(8)),
        "END NETS",
        "END DESIGN",
        ""])


def _pattern(s):
    return (f"POWER {0.2 * s:g} {0.1 * s:g} SIGNAL {0.1 * s:g} {0.1 * s:g} "
            f"SIGNAL {0.1 * s:g} {0.1 * s:g} GROUND {0.2 * s:g} {0.1 * s:g}")


def _script(tmp_path, scale):
    """The same flow at either scale.  `s` multiplies every script-declared
    distance — the track pattern and the corner margin — which is exactly the
    consistency the import factor does NOT provide and the author owns."""
    s = 1.0 if scale is None else _SCALE
    pat = _pattern(s)
    head = "" if scale is None else f"set_import_scale {scale}\n"
    return textwrap.dedent(f"""\
        open_bdb {tmp_path / 'd.bdb'}
        {head}import_def_lef {tmp_path / 'd.def'} {tmp_path / 'm.lef'}
        def_layer 4 M4 H TOP 30
        def_layer 5 M5 V TOP 30
        def_track_pattern 4 0 {pat}
        def_track_pattern 5 0 {pat}
        corner_margin dx {2 * s:g}
        add_blocks_from_bdb 0
        add_bus bus[8] i0.p i3.p
        run_bundler STRICT
        generate_topologies
        run_planner 3
        run_nuts
        run_detailed_nuts
        """)


def _run(tmp_path, scale, tag):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "m.lef").write_text(_LEF)
    (tmp_path / "d.def").write_text(_def_text())
    p = tmp_path / f"{tag}.buda"
    p.write_text(_script(tmp_path, scale))
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command(f"source {p}")
    return s


def _abstract_wl(s):
    return sum(abs(x.span_hi - x.span_lo)
               for x in s.nuts_result.segments if getattr(x, "placed", True))


def test_dbu_scale_routes_the_same_design_in_a_different_unit(tmp_path):
    um = _run(tmp_path / "um", None, "um")
    dbu = _run(tmp_path / "dbu", "dbu", "dbu")

    # Same design: the scale must not change what gets bundled or how the
    # bundle is shaped.
    assert len(um.bundles) == len(dbu.bundles) >= 1
    for a, b in zip(um.bundles, dbu.bundles):
        ta = a.input.candidates[a.plan.selected_topology_index]
        tb = b.input.candidates[b.plan.selected_topology_index]
        # The type string carries the trunk locus (`L_HV@x302@y38`), which is
        # a COORDINATE and so legitimately differs by the scale factor; the
        # shape before the `@` is what must match.
        assert ta.type.split("@")[0] == tb.type.split("@")[0]
        assert len(ta.segments) == len(tb.segments)
        assert [s.layer_hint for s in ta.segments] == \
               [s.layer_hint for s in tb.segments]

    # Same placement quality.  A width model left in microns while the
    # coordinates moved to DBU would under-reserve by 2000x — the run would
    # not merely differ, it would look BETTER while meaning nothing.
    assert um.nuts_result.num_overlaps == dbu.nuts_result.num_overlaps
    assert um.detailed_result.num_unplaced == dbu.detailed_result.num_unplaced

    # …and the route is literally the same length in the other unit.
    wl_um, wl_dbu = _abstract_wl(um), _abstract_wl(dbu)
    assert wl_um > 0
    assert abs(wl_dbu - wl_um * _SCALE) / (wl_um * _SCALE) < 0.02


def test_forgetting_to_scale_the_pattern_is_caught_not_silently_planned(tmp_path):
    """The scaling hole, closed.  `set_import_scale` moves the imported
    coordinates and nothing else — the track pattern in the script is the
    author's to scale.  Forget it and every bus reserves ~1/2000 of its real
    width, which the planner happily reports as routable.  The
    unit-plausibility guard is what makes that a stop."""
    d = tmp_path / "hole"
    d.mkdir(parents=True, exist_ok=True)
    (d / "m.lef").write_text(_LEF)
    (d / "d.def").write_text(_def_text())
    # DBU import, MICRON-scale track pattern: the half-converted flow.
    mixed = _script(d, "dbu").replace(
        f"def_track_pattern 4 0 {_pattern(_SCALE)}",
        f"def_track_pattern 4 0 {_pattern(1.0)}").replace(
        f"def_track_pattern 5 0 {_pattern(_SCALE)}",
        f"def_track_pattern 5 0 {_pattern(1.0)}")
    p = d / "mixed.buda"
    p.write_text(mixed)
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with pytest.raises(ValueError) as exc:
            s.do_command(f"source {p}")
    assert "implausible design scale" in str(exc.value)


def test_the_dbu_run_is_the_one_that_kept_the_sub_micron_detail(tmp_path):
    """Why bother: the micron run quantizes the floorplan to whole microns on
    the way into the engine.  The DBU run does not — that is the entire point
    of the scale, and it is invisible in any metric that only compares
    ratios."""
    um = _run(tmp_path / "um", None, "um")
    dbu = _run(tmp_path / "dbu", "dbu", "dbu")
    um_bounds = {n: um.fp.get_block_bounds(n) for n, _r in um.fp.get_all_blocks()}
    dbu_bounds = {n: dbu.fp.get_block_bounds(n)
                  for n, _r in dbu.fp.get_all_blocks()}
    assert um_bounds and set(um_bounds) == set(dbu_bounds)
    # Cell width is 60 µm = 120000 DBU; in micron mode the engine sees 60.
    for n in um_bounds:
        assert dbu_bounds[n].x2 - dbu_bounds[n].x1 == 120_000
        assert um_bounds[n].x2 - um_bounds[n].x1 == 60
