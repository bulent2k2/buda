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

"""`emit_guides <file.guide>` -- the corridor in the form a router READS.

Phase 0 of docs/internal/librelane_hier_flow.md measured what OpenROAD's
`read_guides` needs (gcell-aligned boxes, adjacent layers sharing a gcell at
every junction, DEF-escaped names, pin access) and phase 1 is BUDA writing
that file itself.  These tests hold the writer to each measured rule, using
the phase-0 measure scripts as the reader -- the same code the recipe runs
on the router's output, so "inside its guides" means one thing.
"""
import contextlib
import io
import math
import sys
from pathlib import Path

import pytest

import buda
import buda_cli
from buda_session.advisory import GcellGrid, gcell_from_def, merge_boxes

_ROOT = Path(__file__).resolve().parents[2]
_MEASURE = _ROOT / "flow" / "librelane" / "phase0" / "measure"
sys.path.insert(0, str(_MEASURE))
import guide_io                                   # noqa: E402
from check_inside import segment_gap              # noqa: E402

_FLOW = """def_layer 3 met3 H 30
def_layer 4 met4 V TOP 30
def_layer 5 met5 H TOP 30
def_track_pattern 3 0 VDD 2 1 _ 1 1 _ 1 1 GND 2 1
def_track_pattern 4 0 VDD 2 1 _ 1 1 _ 1 1 GND 2 1
def_track_pattern 5 0 VDD 2 1 _ 1 1 _ 1 1 GND 2 1
add_block a 0 0 100 100
add_block b 800 600 900 700
add_bus d[8] a.o b.i
add_net m[0] a.o b.i
add_net m[1] a.o b.i
run_bundler STRICT
generate_topologies
run_planner 3
run_nuts
"""
GC = 50                       # gcell in layout units (= um at the default scale)
DBU = 1000


def _run(lines, extra=()):
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for line in lines.strip().splitlines():
            s.do_command(line.strip())
        for c in extra:
            s.do_command(c)
    return s, buf.getvalue()


def _routed(tmp_path, extra=(), detailed=True):
    return _run(_FLOW + ("run_detailed_nuts\n" if detailed else ""), extra)


def _layer_names(s):
    return {lid: n for n, lid in s._layer_name_map.items()}


def _wire_rect_dbu(s, ns):
    h = set(s.layers.get_layer_ids_by_dir(buda.LayerDir.HORIZONTAL))
    lo, hi = sorted((ns.span_lo, ns.span_hi))
    t = ns.track_position
    p, q = ((lo, t), (hi, t)) if int(ns.layer) in h else ((t, lo), (t, hi))
    return [(int(round(v * DBU)) for v in pt) for pt in (p, q)]


# ── the alignment rule ─────────────────────────────────────────────────────

def test_the_gcell_rule_puts_a_shared_point_in_one_gcell_for_both_wires():
    """`cover` floors at BOTH ends: a wire ending exactly on a gcell boundary
    claims the gcell that starts there -- the one its perpendicular partner
    at that point claims too.  Ceil-ing the high end would put the two in
    adjacent gcells, which the router reads as unconnected (DRT-0218)."""
    g = GcellGrid(0, 6900, 0, 6900)
    assert g.cover(0, 13800, "x") == (0, 20700)           # 0..13800 touches gcell 2 at 13800
    assert g.cover(13800, 13800, "x") == (13800, 20700)   # the partner's gcell at x=13800
    assert g.cover(13800, 0, "x") == (0, 20700)           # order-free
    assert g.cell(13800, 100) == (13800, 0, 20700, 6900)
    # A grid that starts off zero indexes from its own origin.
    g = GcellGrid(5000, 6900, 5000, 6900)
    assert g.cover(5000, 5000, "x") == (5000, 11900)
    assert g.cover(4999, 4999, "x") == (-1900, 5000)
    with pytest.raises(ValueError):
        GcellGrid(0, 0, 0, 6900)


def test_gcell_from_def_takes_the_main_grid_not_the_die_edge_remainder(tmp_path):
    """OpenROAD writes a one-or-two-cell GCELLGRID at the die edge beside the
    real period; the period is the entry with the most cells."""
    d = tmp_path / "g.def"
    d.write_text("VERSION 5.8 ;\nDESIGN x ;\nUNITS DISTANCE MICRONS 1000 ;\n"
                 "DIEAREA ( 0 0 ) ( 250000 120000 ) ;\n"
                 "GCELLGRID X 0 DO 2 STEP 1900 ;\nGCELLGRID X 1900 DO 36 STEP 6900 ;\n"
                 "GCELLGRID Y 0 DO 18 STEP 6900 ;\n"
                 "END DESIGN\n")
    g = gcell_from_def(str(d))
    assert g.step == {"x": 6900, "y": 6900} and g.start == {"x": 1900, "y": 0}
    assert g.units == 1000 and "DEF GCELLGRID" in g.describe()
    d.write_text("VERSION 5.8 ;\nDESIGN x ;\nUNITS DISTANCE MICRONS 1000 ;\nEND DESIGN\n")
    assert gcell_from_def(str(d)) is None
    # flow/def's vehicle carries one, and the import keeps it on the session.
    assert gcell_from_def(str(_ROOT / "flow" / "def" / "chip.def")) is not None
    s, _ = _run(f"open_bdb :memory:\nset_import_scale dbu\n"
                f"import_lef_tech {_ROOT / 'flow/def/chip.lef'}\n"
                f"import_def_lef {_ROOT / 'flow/def/chip.def'} {_ROOT / 'flow/def/chip.lef'}\n")
    assert s._def_gcell is not None and s._def_gcell.source == "DEF GCELLGRID"


def test_merge_boxes_unions_contained_and_collinear_boxes_only():
    a = (0, 0, 100, 10, "met3")
    assert merge_boxes([a, (10, 0, 50, 10, "met3")]) == [a]                       # contained
    assert merge_boxes([a, (100, 0, 200, 10, "met3")]) == [(0, 0, 200, 10, "met3")]  # abutting row
    assert merge_boxes([a, (0, 10, 100, 20, "met3")]) == [(0, 0, 100, 20, "met3")]   # stacked column
    two = sorted([a, (100, 0, 200, 10, "met4")])
    assert merge_boxes(two) == two                                                 # other layer
    assert merge_boxes([a, (50, 5, 150, 15, "met3")]) == sorted([a, (50, 5, 150, 15, "met3")])


# ── the file ───────────────────────────────────────────────────────────────

def test_every_bit_wire_is_inside_its_nets_boxes_on_its_layer(tmp_path):
    """The containment the recipe measures on the ROUTER's output, applied to
    BUDA's own: every bit-wire lies inside its net's gcell boxes on its own
    layer, at zero slack, checked along its length by the same
    `check_inside.segment_gap` the measurement uses."""
    p = tmp_path / "bus.guide"
    s, out = _routed(tmp_path, [f"emit_guides {p} gcell {GC}"])
    assert "from detailed NUTS" in out and "every one of" in out, out
    g = guide_io.read_guides(p)
    names = _layer_names(s)
    bid_names = {w.input.original_bundle.id: list(w.input.original_bundle.get_net_names())
                 for w in s.bundles}
    n = 0
    for ns in s.detailed_result.net_segments:
        net = bid_names[ns.bundle_id][ns.bit_index]
        (p1, p2) = _wire_rect_dbu(s, ns)
        assert segment_gap(tuple(p1), tuple(p2), names[int(ns.layer)], g[net], 0) is None, \
            f"{net} seg {ns.seg_idx} on {names[int(ns.layer)]} leaves its guide"
        n += 1
    assert n and set(g) == {f"d_{i}" for i in range(8)} | {"m[0]", "m[1]"}


def test_boxes_are_gcell_aligned_and_names_are_def_escaped(tmp_path):
    """Every coordinate is a multiple of the gcell (a box off the grid stops
    the router, DRT-0229) and a bracketed net is spelled `m\\[0\\]` as
    OpenROAD's own writer spells a non-port net; `plain_names` turns the
    escaping off for a flow whose router wants the plain form."""
    p = tmp_path / "bus.guide"
    _routed(tmp_path, [f"emit_guides {p} gcell {GC}"])
    text = p.read_text()
    assert "m\\[0\\]\n(" in text and "m[0]\n(" not in text
    for x1, y1, x2, y2, _ in (b for bx in guide_io.read_guides(p).values() for b in bx):
        for v in (x1, y1, x2, y2):
            assert v % (GC * DBU) == 0, v
        assert x2 - x1 >= GC * DBU and y2 - y1 >= GC * DBU
    _routed(tmp_path, [f"emit_guides {p} gcell {GC} plain_names"])
    assert "m[0]\n(" in p.read_text()


def test_every_via_sits_in_a_gcell_its_net_holds_on_both_layers(tmp_path):
    """Adjacent-layer boxes connect only where they SHARE a gcell (DRT-0218,
    measured).  Checked here independently of the writer's own self-check."""
    p = tmp_path / "bus.guide"
    s, out = _routed(tmp_path, [f"emit_guides {p} gcell {GC}"])
    g = guide_io.read_guides(p)
    names = _layer_names(s)
    bid_names = {w.input.original_bundle.id: list(w.input.original_bundle.get_net_names())
                 for w in s.bundles}
    grid = GcellGrid(0, GC * DBU, 0, GC * DBU)
    n = 0
    for v in s.detailed_result.net_vias:
        net = bid_names[v.bundle_id][v.bit_index]
        cx1, cy1, cx2, cy2 = grid.cell(int(round(v.x * DBU)), int(round(v.y * DBU)))
        for lid in (v.from_layer, v.to_layer):
            assert any(b[4] == names[int(lid)] and b[0] <= cx1 and cx2 <= b[2]
                       and b[1] <= cy1 and cy2 <= b[3] for b in g[net]), (net, names[int(lid)], v.x, v.y)
        n += 1
    assert n, "no vias -- the test would prove nothing"
    assert f"every one of {n} via(s)" in out


def test_a_free_end_gets_a_terminal_box_on_the_terminal_layers(tmp_path):
    """A wire end that is not a via is a block landing; the router has to
    reach the block's PIN there, on the pin's layer, which BUDA does not
    model -- so `terminal` names it and the landing's gcell is added on it."""
    p = tmp_path / "bus.guide"
    s, out = _routed(tmp_path, [f"emit_guides {p} gcell {GC} terminal met3"])
    assert "terminal box(es), 0 joined to a pin" in out          # no BDB, no pins
    g = guide_io.read_guides(p)
    grid = GcellGrid(0, GC * DBU, 0, GC * DBU)
    # Block a's face at x=100: every net has a landing there, so its gcell
    # is in the net's met3 boxes.
    for net, bx in g.items():
        landings = [b for b in bx if b[4] == "met3" and b[0] <= 100 * DBU <= b[2]]
        assert landings, net
    # An undeclared terminal layer is refused by name.
    _s, out = _routed(tmp_path, [f"emit_guides {p} gcell {GC} terminal met9"])
    assert "terminal layer 'met9' is not a declared layer" in out


def test_abstract_fallback_guides_every_net_of_a_corridor(tmp_path):
    p = tmp_path / "bus.guide"
    s, out = _routed(tmp_path, [f"emit_guides {p} gcell {GC}"], detailed=False)
    assert "from abstract NUTS" in out and "from ABSTRACT bus segments" in out
    g = guide_io.read_guides(p)
    assert set(g) == {f"d_{i}" for i in range(8)} | {"m[0]", "m[1]"}
    for bx in g.values():
        assert bx and all(v % (GC * DBU) == 0 for b in bx for v in b[:4])


def test_the_gcell_is_required_and_the_options_belong_to_one_output_each(tmp_path):
    p = tmp_path / "bus.guide"
    _s, out = _routed(tmp_path, [f"emit_guides {p}"])
    assert "needs the router's gcell size" in out and not p.exists()
    _s, out = _routed(tmp_path, [f"emit_guides {p} gcell 0"])
    assert "gcell must be a positive length" in out
    _s, out = _routed(tmp_path, [f"emit_guides {p} gcell {GC} margin 3"])
    assert "belong to the manifest" in out
    _s, out = _routed(tmp_path, [f"emit_guides {tmp_path / 'g.json'} gcell {GC}"])
    assert "apply to a .guide output only" in out
    with pytest.raises(SystemExit):                 # an unknown option is fail-fast
        _routed(tmp_path, [f"emit_guides {p} gcell {GC} bogus 1"])


def test_before_nuts_the_file_is_empty_and_says_so(tmp_path):
    p = tmp_path / "bus.guide"
    s, out = _run(_FLOW.replace("run_nuts\n", ""), [f"emit_guides {p} gcell {GC}"])
    assert "no placed bus segments" in out and p.read_text() == ""


def test_two_runs_write_the_same_bytes(tmp_path):
    a, b = tmp_path / "a.guide", tmp_path / "b.guide"
    _routed(tmp_path, [f"emit_guides {a} gcell {GC}"])
    _routed(tmp_path, [f"emit_guides {b} gcell {GC}"])
    assert a.read_bytes() == b.read_bytes() and a.stat().st_size > 0


# ── with a DEF-imported design: gcells from the DEF, strips to the pins ────

_CHIP = """open_bdb :memory:
set_import_scale dbu
corner_margin dx 2000 dy 2000
set_min_stub_length 1000
import_lef_tech {lef}
import_def_lef {d} {lef}
import_verilog {v}
derive_container_bboxes margin 1500
derive_busterms 2
add_blocks_from_bdb 0
add_blocks_from_bdb 1 skip
add_blocks_from_bdb 2 skip
run_hier_bundler depth 2
generate_hier_topologies
run_planner hier 5
run_nuts
run_detailed_nuts
"""


@pytest.mark.mid
def test_a_def_imported_design_uses_its_gcellgrid_and_joins_landings_to_pins(tmp_path):
    """flow/def's vehicle end to end: the gcell comes from the DEF (no
    `gcell` option), the scale is DBU, and every free end is joined to the
    net's nearest PIN by a strip that holds the pin's gcell on the terminal
    layer -- the piece of the guide that makes it 'connected to design'."""
    fd = _ROOT / "flow" / "def"
    flow = _CHIP.format(lef=fd / "chip.lef", d=fd / "chip.def", v=fd / "chip.v")
    p = tmp_path / "chip.guide"
    s, out = _run(flow, [f"emit_guides {p} terminal M3"])
    assert "[DEF GCELLGRID]" in out and "joined to a pin" in out, out
    assert "WARNING: emit_guides" not in out
    g = guide_io.read_guides(p)
    assert g, out
    grid = s._def_gcell
    units, lu = 1000, float(s.bdb.import_scale())
    from buda_session.advisory import _pin_positions
    pins = _pin_positions(s)
    joined = 0
    for net, bx in g.items():
        for px, py in pins.get(net, []):
            cx1, cy1, cx2, cy2 = grid.cell(int(round(px / lu * units)), int(round(py / lu * units)))
            if any(b[4] == "M3" and b[0] <= cx1 and cx2 <= b[2] and b[1] <= cy1 and cy2 <= b[3]
                   for b in bx):
                joined += 1
    assert joined, "no pin gcell found in any net's M3 boxes"
