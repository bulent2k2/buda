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

import os
import textwrap
import pytest
from pytest_bdd import scenarios, given, when, then, parsers
import buda

scenarios('features/bdb_import.feature')
scenarios('features/bdb_combined.feature')
scenarios('features/bdb_mutations.feature')
scenarios('features/bdb_cell_inst.feature')
scenarios('features/bdb_add_blocks.feature')
scenarios('features/bdb_inst_to_cell.feature')
scenarios('features/bdb_flip_rotate.feature')
scenarios('features/bdb_net_pins.feature')

# ---------------------------------------------------------------------------
# Verilog fixture
# ---------------------------------------------------------------------------
#
# Hierarchy:
#   hier_test1
#     ai (cell a)
#       a1i1 (cell a1)   ─┐ share internal wire ai/w1
#       a1i2 (cell a1)   ─┘
#       a2i  (cell a2)     connected to top-level ab_bus via port binding
#     bi (cell b)
#       b1i  (cell b1)
#       a1i1 (cell a1)     connected to ab_bus via port binding
#     ci (cell c)
#       c1i  (cell c1)
#       c2i  (cell c2)
#       a1i1 (cell a1)
#       a1i2 (cell a1)
#
# Cell a1 is defined in all three sub-modules, exercising shared-cell path
# disambiguation.  Nets propagated through port bindings verify that
# import_verilog scopes net names correctly across hierarchy levels.

_HIER_TEST1 = textwrap.dedent("""\
    module a1 ();
    endmodule
    module a2 ();
    endmodule
    module b1 ();
    endmodule
    module c1 ();
    endmodule
    module c2 ();
    endmodule
    module a (input [3:0] data_in);
        wire [3:0] w1;
        a1 a1i1 (.q(w1));
        a1 a1i2 (.d(w1));
        a2 a2i (.x(data_in));
    endmodule
    module b (input [3:0] data_in);
        b1 b1i ();
        a1 a1i1 (.q(data_in));
    endmodule
    module c ();
        c1 c1i ();
        c2 c2i ();
        a1 a1i1 ();
        a1 a1i2 ();
    endmodule
    module hier_test1 ();
        wire [3:0] ab_bus;
        a ai (.data_in(ab_bus));
        b bi (.data_in(ab_bus));
        c ci ();
    endmodule
""")


@pytest.fixture
def context():
    return {}


# ---------------------------------------------------------------------------
# Background step
# ---------------------------------------------------------------------------

@given("a BDB populated from the hier_test1 Verilog")
def load_hier_test1(context, tmp_path):
    v_file = tmp_path / "hier_test1.v"
    v_file.write_text(_HIER_TEST1)
    db = buda.BDB(":memory:")
    db.import_verilog(str(v_file))
    context['db']    = db
    context['comps'] = {r.name: r for r in db.all_components()}
    context['nets']  = {r.name: r for r in db.all_nets()}
    context['pins']  = db.all_pins()


# ---------------------------------------------------------------------------
# Component steps
# ---------------------------------------------------------------------------

@then(parsers.re(r'the database contains (?P<count>\d+) components?'))
def check_component_count(context, count):
    count = int(count)
    _refresh(context)
    assert len(context['comps']) == count, (
        f"Expected {count} components, got {len(context['comps'])}: "
        f"{sorted(context['comps'])}"
    )


@then(parsers.re(r'component "(?P<name>[^"]+)" has cell "(?P<cell>[^"]+)" and depth (?P<depth>\d+)'))
def check_component_cell_depth(context, name, cell, depth):
    depth = int(depth)
    r = context['comps'].get(name)
    assert r is not None, f"Component {name!r} not found. Present: {sorted(context['comps'])}"
    assert r.cell == cell, f"{name!r}: expected cell {cell!r}, got {r.cell!r}"
    assert r.depth == depth, f"{name!r}: expected depth {depth}, got {r.depth}"


@then(parsers.re(r'"(?P<child>[^"]+)" is a child of "(?P<parent>[^"]+)"'))
def check_parent_child(context, child, parent):
    child_r  = context['comps'].get(child)
    parent_r = context['comps'].get(parent)
    assert child_r  is not None, f"Child {child!r} not found"
    assert parent_r is not None, f"Parent {parent!r} not found"
    assert child_r.parent_id == parent_r.id, (
        f"{child!r}.parent_id={child_r.parent_id}, expected {parent_r.id} (id of {parent!r})"
    )


@then(parsers.re(r'component "(?P<name>[^"]+)" has no parent'))
def check_no_parent(context, name):
    r = context['comps'].get(name)
    assert r is not None, f"Component {name!r} not found"
    assert r.parent_id == -1, f"{name!r}: expected no parent, got parent_id={r.parent_id}"


@then(parsers.parse('{count:d} components have cell "{cell}"'))
def check_cell_instance_count(context, count, cell):
    found = [r.name for r in context['comps'].values() if r.cell == cell]
    assert len(found) == count, (
        f"Expected {count} instance(s) of cell {cell!r}, got {len(found)}: {sorted(found)}"
    )


@then(parsers.re(r'a component named "(?P<name>[^"]+)" exists'))
def check_component_exists(context, name):
    assert name in context['comps'], (
        f"Component {name!r} not found. Present: {sorted(context['comps'])}"
    )


@then(parsers.re(r'component "(?P<name>[^"]+)" is not a leaf'))
def check_not_leaf(context, name):
    r = context['comps'].get(name)
    assert r is not None, f"Component {name!r} not found"
    assert not r.is_leaf, f"{name!r}: expected is_leaf=False"


# ---------------------------------------------------------------------------
# Net steps
# ---------------------------------------------------------------------------

@then(parsers.parse("the database contains {count:d} nets"))
def check_net_count(context, count):
    assert len(context['nets']) == count, (
        f"Expected {count} nets, got {len(context['nets'])}: {sorted(context['nets'])}"
    )


@then(parsers.re(r'net "(?P<name>[^"]+)" exists'))
def check_net_exists(context, name):
    assert name in context['nets'], (
        f"Net {name!r} not found. Present: {sorted(context['nets'])}"
    )


@then(parsers.re(r'net "(?P<name>[^"]+)" has (?P<count>\d+) pins'))
def check_net_pin_count(context, name, count):
    count = int(count)
    assert name in context['nets'], f"Net {name!r} not found"
    net_id = context['nets'][name].id
    pins = [p for p in context['pins'] if p.net_id == net_id]
    assert len(pins) == count, (
        f"Net {name!r}: expected {count} pins, got {len(pins)}: "
        + str([(context['comps'][p.comp_id].name if p.comp_id in {r.id: r for r in context['comps'].values()} else p.comp_id, p.pin_name)
               for p in pins])
    )


@then(parsers.re(r'net "(?P<net>[^"]+)" connects component "(?P<comp>[^"]+)" at pin "(?P<pin>[^"]+)"'))
def check_pin_connection(context, net, comp, pin):
    assert net  in context['nets'],  f"Net {net!r} not found"
    assert comp in context['comps'], f"Component {comp!r} not found"
    net_id  = context['nets'][net].id
    comp_id = context['comps'][comp].id
    match = any(
        p.net_id == net_id and p.comp_id == comp_id and p.pin_name == pin
        for p in context['pins']
    )
    assert match, (
        f"No pin ({net!r} → {comp!r}.{pin}). "
        f"Pins on {net!r}: "
        + str([(p.comp_id, p.pin_name) for p in context['pins'] if p.net_id == net_id])
    )


# ---------------------------------------------------------------------------
# Combined DEF + Verilog import
# ---------------------------------------------------------------------------
#
# Layout (all units in μm, UNITS DISTANCE MICRONS 1):
#
#   die: 2000 × 500
#
#   module  cell  placed-at  LEF-size  bbox
#   ──────  ────  ─────────  ────────  ──────────────────
#   ai      a     (100,100)  500×200   (100,100)-(600,300)
#   bi      b     (750,100)  350×200   (750,100)-(1100,300)
#   ci      c    (1200,100)  650×200   (1200,100)-(1850,300)
#
#   ai/a1i1 a1    (150,150)  100×100   (150,150)-(250,250)
#   ai/a1i2 a1    (300,150)  100×100   (300,150)-(400,250)
#   ai/a2i  a2    (450,150)  100×100   (450,150)-(550,250)
#   bi/b1i  b1    (800,150)  100×100   (800,150)-(900,250)
#   bi/a1i1 a1    (950,150)  100×100   (950,150)-(1050,250)
#   ci/c1i  c1   (1250,150)  100×100  (1250,150)-(1350,250)
#   ci/c2i  c2   (1400,150)  100×100  (1400,150)-(1500,250)
#   ci/a1i1 a1   (1550,150)  100×100  (1550,150)-(1650,250)
#   ci/a1i2 a1   (1700,150)  100×100  (1700,150)-(1800,250)

_HIER_TEST1_LEF = textwrap.dedent("""\
    MACRO a1
      SIZE 100 BY 100 ;
    END a1
    MACRO a2
      SIZE 100 BY 100 ;
    END a2
    MACRO b1
      SIZE 100 BY 100 ;
    END b1
    MACRO c1
      SIZE 100 BY 100 ;
    END c1
    MACRO c2
      SIZE 100 BY 100 ;
    END c2
    MACRO a
      SIZE 500 BY 200 ;
    END a
    MACRO b
      SIZE 350 BY 200 ;
    END b
    MACRO c
      SIZE 650 BY 200 ;
    END c
""")

_HIER_TEST1_DEF = textwrap.dedent("""\
    VERSION 5.8 ;
    UNITS DISTANCE MICRONS 1 ;
    DIEAREA ( 0 0 ) ( 2000 500 ) ;
    COMPONENTS 12 ;
      - ai a + PLACED ( 100 100 ) N ;
      - bi b + PLACED ( 750 100 ) N ;
      - ci c + PLACED ( 1200 100 ) N ;
      - ai/a1i1 a1 + PLACED ( 150 150 ) N ;
      - ai/a1i2 a1 + PLACED ( 300 150 ) N ;
      - ai/a2i a2 + PLACED ( 450 150 ) N ;
      - bi/b1i b1 + PLACED ( 800 150 ) N ;
      - bi/a1i1 a1 + PLACED ( 950 150 ) N ;
      - ci/c1i c1 + PLACED ( 1250 150 ) N ;
      - ci/c2i c2 + PLACED ( 1400 150 ) N ;
      - ci/a1i1 a1 + PLACED ( 1550 150 ) N ;
      - ci/a1i2 a1 + PLACED ( 1700 150 ) N ;
    END COMPONENTS
    NETS 0 ;
    END NETS
    END DESIGN
""")

# Written once per run so the user can open it with any SQLite browser.
BDB_ARTIFACT_PATH = os.path.join(os.path.dirname(__file__), "hier_test1.bdb")


@given("a combined BDB for hier_test1 with DEF placement and Verilog hierarchy")
def load_combined_hier_test1(context, tmp_path):
    def_file = tmp_path / "hier_test1.def"
    lef_file = tmp_path / "hier_test1.lef"
    v_file   = tmp_path / "hier_test1.v"
    def_file.write_text(_HIER_TEST1_DEF)
    lef_file.write_text(_HIER_TEST1_LEF)
    v_file.write_text(_HIER_TEST1)

    for p in [BDB_ARTIFACT_PATH,
              BDB_ARTIFACT_PATH + "-wal",
              BDB_ARTIFACT_PATH + "-shm"]:
        if os.path.exists(p):
            os.unlink(p)

    db = buda.BDB(BDB_ARTIFACT_PATH)
    db.import_def_lef(str(def_file), str(lef_file))
    db.import_verilog(str(v_file))
    db.compute_all()

    context['db']    = db
    context['comps'] = {r.name: r for r in db.all_components()}
    context['nets']  = {r.name: r for r in db.all_nets()}
    context['pins']  = db.all_pins()


@then(parsers.re(r'the die is (?P<w>[\d.]+) by (?P<h>[\d.]+) microns'))
def check_die_dimensions(context, w, h):
    assert context['db'].die_w() == pytest.approx(float(w))
    assert context['db'].die_h() == pytest.approx(float(h))


@then(parsers.re(
    r'component "(?P<name>[^"]+)" spans from '
    r'\((?P<x1>[\d.]+), (?P<y1>[\d.]+)\) to \((?P<x2>[\d.]+), (?P<y2>[\d.]+)\)'
))
def check_component_bbox(context, name, x1, y1, x2, y2):
    r = context['comps'].get(name)
    assert r is not None, f"Component {name!r} not found"
    assert r.x1 == pytest.approx(float(x1)), f"{name}.x1: expected {x1}, got {r.x1}"
    assert r.y1 == pytest.approx(float(y1)), f"{name}.y1: expected {y1}, got {r.y1}"
    assert r.x2 == pytest.approx(float(x2)), f"{name}.x2: expected {x2}, got {r.x2}"
    assert r.y2 == pytest.approx(float(y2)), f"{name}.y2: expected {y2}, got {r.y2}"


@then(parsers.re(
    r'comps_in_rect \((?P<xl>[\d.]+), (?P<yl>[\d.]+), (?P<xh>[\d.]+), (?P<yh>[\d.]+)\) '
    r'returns exactly "(?P<names>[^"]+)"'
))
def check_comps_in_rect(context, xl, yl, xh, yh, names):
    expected = sorted(n.strip() for n in names.split(','))
    result   = sorted(context['db'].comps_in_rect(
        float(xl), float(yl), float(xh), float(yh)))
    assert result == expected, f"Expected {expected}, got {result}"


# ---------------------------------------------------------------------------
# Mutation steps
# ---------------------------------------------------------------------------

def _refresh(context):
    """Re-read all_components into context after a mutation."""
    context['comps'] = {r.name: r for r in context['db'].all_components()}


# ---------------------------------------------------------------------------
# Cell definition and add_inst steps
# ---------------------------------------------------------------------------

@given("an empty in-memory BDB")
def empty_bdb(context):
    db = buda.BDB(":memory:")
    context['db']    = db
    context['comps'] = {}
    context['cells'] = {}


def _refresh_cells(context):
    context['cells'] = {r.name: r for r in context['db'].all_cells()}


@when(parsers.re(r'I define cell "(?P<name>[^"]+)" with size (?P<w>[\d.]+) by (?P<h>[\d.]+)'))
def step_add_cell(context, name, w, h):
    context['db'].add_cell(name, float(w), float(h))
    _refresh_cells(context)
    _refresh(context)


@when(parsers.re(
    r'I place instance "(?P<inst>[^"]+)" of cell "(?P<cell>[^"]+)" '
    r'under "(?P<parent>[^"]+)" at \((?P<x>[\d.]+), (?P<y>[\d.]+)\)'
))
def step_add_inst(context, inst, cell, parent, x, y):
    par = "" if parent == "-" else parent
    context['db'].add_inst(inst, cell, par, float(x), float(y))
    _refresh(context)


@then(parsers.re(r'cell "(?P<name>[^"]+)" has width (?P<w>[\d.]+) and height (?P<h>[\d.]+)'))
def check_cell_size(context, name, w, h):
    _refresh_cells(context)
    r = context['cells'].get(name)
    assert r is not None, f"Cell {name!r} not in cell table. Present: {sorted(context['cells'])}"
    assert r.width  == pytest.approx(float(w)), f"{name}.width: expected {w}, got {r.width}"
    assert r.height == pytest.approx(float(h)), f"{name}.height: expected {h}, got {r.height}"


@then(parsers.parse("the cell table contains {count:d} cells"))
def check_cell_count(context, count):
    _refresh_cells(context)
    assert len(context['cells']) == count, (
        f"Expected {count} cells, got {len(context['cells'])}: {sorted(context['cells'])}"
    )


@then(parsers.re(r'component "(?P<name>[^"]+)" has depth (?P<depth>\d+)'))
def check_component_depth(context, name, depth):
    r = context['comps'].get(name)
    assert r is not None, f"Component {name!r} not found"
    assert r.depth == int(depth), f"{name!r}: expected depth {depth}, got {r.depth}"


# ---------------------------------------------------------------------------
# Mutation steps
# ---------------------------------------------------------------------------

@when(parsers.re(r'I move component "(?P<name>[^"]+)" to \((?P<x>[\d.]+), (?P<y>[\d.]+)\)'))
def step_move_comp(context, name, x, y):
    context['db'].move_comp(name, float(x), float(y))
    _refresh(context)


@when(parsers.re(r'I resize cell "(?P<cell>[^"]+)" to (?P<w>[\d.]+) by (?P<h>[\d.]+)'))
def step_resize_cell(context, cell, w, h):
    context['db'].resize_cell(cell, float(w), float(h))
    _refresh(context)


@when(parsers.re(
    r'I add component "(?P<name>[^"]+)" of cell "(?P<cell>[^"]+)" '
    r'under parent "(?P<parent>[^"]+)" '
    r'at \((?P<x1>[\d.]+), (?P<y1>[\d.]+)\)-\((?P<x2>[\d.]+), (?P<y2>[\d.]+)\)'
))
def step_add_comp(context, name, cell, parent, x1, y1, x2, y2):
    par = "" if parent == "-" else parent
    context['db'].add_comp(name, cell, par,
                           float(x1), float(y1), float(x2), float(y2))
    _refresh(context)


# ---------------------------------------------------------------------------
# add_blocks_from_bdb steps
# ---------------------------------------------------------------------------
#
# Fixture hierarchy (mixed depths to exercise all three modes):
#
#   u_a  (depth 0)  placed
#     u_a/sub0  (depth 1)  placed
#       u_a/sub0/leaf0  (depth 2)  placed
#       u_a/sub0/leaf1  (depth 2)  placed
#   u_b  (depth 0)  placed
#     u_b/sub0  (depth 1)  placed
#       u_b/sub0/leaf0  (depth 2)  placed
#       u_b/sub0/unplaced  (depth 2)  x1=-1 (no placement)
#   u_c  (depth 0, no children — shallow branch)  placed
#
# At depth 2: deepest adds leaf0/leaf1/leaf0 + u_c fallback = 4
#             skip   adds leaf0/leaf1/leaf0 only            = 3
#             error  aborts (u_c is shallow)                = 0


import sys
import io
from buda_cli import BudaSession


def _make_routing_bdb(tmp_path):
    """Build a BDB with the mixed-depth hierarchy above."""
    db = buda.BDB(":memory:")
    rows = [
        # (name, cell, parent, x1, y1, x2, y2)
        ("u_a",                "A",  "",         0,   0, 200, 200),
        ("u_a/sub0",           "S",  "u_a",     10,  10, 190, 190),
        ("u_a/sub0/leaf0",     "L",  "u_a/sub0", 10,  10,  60,  60),
        ("u_a/sub0/leaf1",     "L",  "u_a/sub0", 70,  10, 120,  60),
        ("u_b",                "B",  "",        300,   0, 500, 200),
        ("u_b/sub0",           "S",  "u_b",      10,  10, 190, 190),
        ("u_b/sub0/leaf0",     "L",  "u_b/sub0", 10,  10,  60,  60),
        ("u_b/sub0/unplaced",  "L",  "u_b/sub0", -1,  -1,  -1,  -1),
        ("u_c",                "C",  "",        600,   0, 700, 100),
    ]
    for name, cell, par, x1, y1, x2, y2 in rows:
        db.add_comp(name, cell, par, x1, y1, x2, y2)
    return db


@given("a BDB with a mixed-depth hierarchy for routing tests")
def load_routing_bdb(context, tmp_path):
    context['db'] = _make_routing_bdb(tmp_path)
    context['comps'] = {r.name: r for r in context['db'].all_components()}
    context['nets']  = {}
    context['pins']  = []
    # A BudaSession gives us a real Floorplan to inspect after add_blocks
    context['session'] = BudaSession()
    context['session'].bdb = context['db']


@when(parsers.re(r'I call add_blocks_from_bdb at depth (?P<depth>\d+) mode (?P<mode>\w+)'))
def step_add_blocks(context, depth, mode):
    # Capture stdout so error-mode warnings don't pollute test output
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        context['session']._add_blocks_from_bdb(int(depth), mode)
    finally:
        sys.stdout = old
    context['add_blocks_output'] = buf.getvalue()


def _fp_block_names(session):
    """Return the set of block names currently in the floorplan."""
    return {name for name, _rect in session.fp.get_all_blocks()}


@then(parsers.re(r'the floorplan contains blocks "(?P<names>[^"]+)"'))
def check_fp_blocks(context, names):
    expected = {n.strip() for n in names.split(',')}
    actual   = _fp_block_names(context['session'])
    assert expected == actual, (
        f"Expected blocks {sorted(expected)}, got {sorted(actual)}"
    )


@then(parsers.parse("the floorplan block count is {count:d}"))
def check_fp_block_count(context, count):
    actual = len(_fp_block_names(context['session']))
    assert actual == count, (
        f"Expected {count} blocks, got {actual}: "
        f"{sorted(_fp_block_names(context['session']))}"
    )


@then(parsers.re(r'the floorplan does not contain block "(?P<name>[^"]+)"'))
def check_fp_no_block(context, name):
    actual = _fp_block_names(context['session'])
    assert name not in actual, f"Block {name!r} should not be in floorplan"


# ---------------------------------------------------------------------------
# add_inst_to_cell steps
# ---------------------------------------------------------------------------

@when(parsers.re(
    r'cell "(?P<parent_cell>[^"]+)" contains "(?P<inst_name>[^"]+)" '
    r'of "(?P<child_cell>[^"]+)" at \((?P<x>-?\d+), (?P<y>-?\d+)\)'
))
def step_add_inst_to_cell(context, parent_cell, inst_name, child_cell, x, y):
    context['db'].add_inst_to_cell(parent_cell, inst_name, child_cell, int(x), int(y))
    _refresh(context)


@then(parsers.re(r'component "(?P<name>[^"]+)" does not exist'))
def check_component_absent(context, name):
    _refresh(context)
    assert name not in context['comps'], (
        f"Component {name!r} unexpectedly found in DB"
    )


# ---------------------------------------------------------------------------
# flip_comp / rotate_comp steps
# ---------------------------------------------------------------------------

@when(parsers.re(
    r'I flip component "(?P<name>[^"]+)" in the (?P<axis>x|y) direction'
))
def step_flip_comp(context, name, axis):
    context['db'].flip_comp(name, axis == 'x')
    _refresh(context)


@when(parsers.re(r'I rotate component "(?P<name>[^"]+)" by (?P<degrees>\d+) degrees'))
def step_rotate_comp(context, name, degrees):
    context['db'].rotate_comp(name, int(degrees))
    _refresh(context)


# ---------------------------------------------------------------------------
# Net / pin / bdb_net_mode steps
# ---------------------------------------------------------------------------

def _make_two_level_bdb():
    """
    Two top-level blocks (u1, u2), each containing one leaf instance (l1).
    Cell 'blk': 200×100.  Cell 'leaf': 100×50.
    u1 at (0,0), u1/l1 at relative (60,35) → absolute (60,35).
    u2 at (300,0), u2/l1 at relative (60,35) → absolute (360,35).
    """
    db = buda.BDB(":memory:")
    db.add_cell('blk',  200.0, 100.0)
    db.add_cell('leaf', 100.0,  50.0)
    db.add_inst('u1',    'blk',  '',   0.0,   0.0)
    db.add_inst('u1/l1', 'leaf', 'u1', 60.0, 35.0)
    db.add_inst('u2',    'blk',  '',  300.0,  0.0)
    db.add_inst('u2/l1', 'leaf', 'u2', 60.0, 35.0)
    return db


def _make_three_level_bdb():
    """
    top → mid + mid2, each with a leaf child.
    Cell 'top': 500×300.  Cell 'mid': 200×100.  Cell 'leaf': 80×40.
    """
    db = buda.BDB(":memory:")
    db.add_cell('top',  500.0, 300.0)
    db.add_cell('mid',  200.0, 100.0)
    db.add_cell('leaf',  80.0,  40.0)
    db.add_inst('top',          'top',  '',       0.0,   0.0)
    db.add_inst('top/mid',      'mid',  'top',   50.0,  50.0)
    db.add_inst('top/mid/leaf', 'leaf', 'top/mid', 20.0, 10.0)
    db.add_inst('top/mid2',     'mid',  'top',  300.0,  50.0)
    db.add_inst('top/mid2/leaf','leaf', 'top/mid2', 20.0, 10.0)
    return db


@given("an empty in-memory BDB with net support")
def empty_bdb_net(context):
    db = buda.BDB(":memory:")
    context['db']        = db
    context['comps']     = {}
    context['cells']     = {}
    context['cell_pins'] = {}


@given("an empty in-memory BDB with a two-level hierarchy")
def empty_bdb_two_level(context):
    db = _make_two_level_bdb()
    context['db']        = db
    context['comps']     = {r.name: r for r in db.all_components()}
    context['nets']      = {r.name: r for r in db.all_nets()}
    context['pins']      = db.all_pins()
    context['cell_pins'] = {(r.cell, r.pin_name): r for r in db.all_cell_pins()}


@given("an empty in-memory BDB with a three-level hierarchy")
def empty_bdb_three_level(context):
    db = _make_three_level_bdb()
    context['db']        = db
    context['comps']     = {r.name: r for r in db.all_components()}
    context['nets']      = {r.name: r for r in db.all_nets()}
    context['pins']      = db.all_pins()
    context['cell_pins'] = {(r.cell, r.pin_name): r for r in db.all_cell_pins()}


def _refresh_pins(context):
    context['nets']      = {r.name: r for r in context['db'].all_nets()}
    context['pins']      = context['db'].all_pins()
    context['cell_pins'] = {(r.cell, r.pin_name): r for r in context['db'].all_cell_pins()}
    _refresh(context)


@when(parsers.re(r'I add cell pin "(?P<cell>[^"]+)"\."(?P<pin>[^"]+)" direction (?P<dir>\w+) at \((?P<px>-?[\d.]+), (?P<py>-?[\d.]+)\)'))
def step_add_cell_pin(context, cell, pin, dir, px, py):
    context['db'].add_cell_pin(cell, pin, dir, float(px), float(py))
    _refresh_pins(context)


@when(parsers.re(r'I add cell pin "(?P<cell>[^"]+)"\."(?P<pin>[^"]+)" direction (?P<dir>\w+) without position'))
def step_add_cell_pin_no_pos(context, cell, pin, dir):
    context['db'].add_cell_pin(cell, pin, dir)
    _refresh_pins(context)


@when(parsers.re(
    r'I call add_net_pins "(?P<net>[^"]+)" driver "(?P<drv>[^"]+)" receivers "(?P<rcvs>[^"]+)"'
))
def step_add_net_pins(context, net, drv, rcvs):
    rcv_list = [r.strip() for r in rcvs.split(',')]
    context['db'].add_net_pins(net, drv, rcv_list)
    _refresh_pins(context)


@then(parsers.re(r'cell pin "(?P<cell>[^"]+)"\."(?P<pin>[^"]+)" has direction (?P<dir>\w+) and offset \((?P<px>-?[\d.]+), (?P<py>-?[\d.]+)\)'))
def check_cell_pin(context, cell, pin, dir, px, py):
    key = (cell, pin)
    cp = context['cell_pins'].get(key)
    assert cp is not None, f"cell_pin ({cell!r}, {pin!r}) not found. Present: {sorted(context['cell_pins'])}"
    assert cp.dir == dir, f"({cell}.{pin}).dir: expected {dir!r}, got {cp.dir!r}"
    assert cp.px == pytest.approx(float(px)), f"({cell}.{pin}).px: expected {px}, got {cp.px}"
    assert cp.py == pytest.approx(float(py)), f"({cell}.{pin}).py: expected {py}, got {cp.py}"


@then(parsers.re(r'net "(?P<name>[^"]+)" exists in BDB'))
def check_net_in_bdb(context, name):
    assert name in context['nets'], (
        f"Net {name!r} not found. Present: {sorted(context['nets'])}"
    )


@then(parsers.re(r'net "(?P<net>[^"]+)" has (?P<count>\d+) pins total'))
def check_net_pin_count_total(context, net, count):
    count = int(count)
    assert net in context['nets'], f"Net {net!r} not found"
    net_id = context['nets'][net].id
    pins = [p for p in context['pins'] if p.net_id == net_id]
    assert len(pins) == count, (
        f"Net {net!r}: expected {count} pins, got {len(pins)}: "
        + str([(p.comp_id, p.pin_name) for p in pins])
    )


@then(parsers.re(
    r'pin on net "(?P<net>[^"]+)" at component "(?P<comp>[^"]+)" is named "(?P<pin>[^"]+)" with direction (?P<dir>\w+)'
))
def check_pin_direction(context, net, comp, pin, dir):
    assert net  in context['nets'],  f"Net {net!r} not found"
    assert comp in context['comps'], f"Component {comp!r} not found"
    net_id  = context['nets'][net].id
    comp_id = context['comps'][comp].id
    matches = [p for p in context['pins']
               if p.net_id == net_id and p.comp_id == comp_id and p.pin_name == pin]
    assert matches, (
        f"No pin ({net!r} → {comp!r}.{pin}). "
        f"Pins on {net!r}: "
        + str([(p.comp_id, p.pin_name) for p in context['pins'] if p.net_id == net_id])
    )
    assert matches[0].dir == dir, f"Pin ({comp}.{pin}) dir: expected {dir!r}, got {matches[0].dir!r}"


@then(parsers.re(
    r'pin on net "(?P<net>[^"]+)" at component "(?P<comp>[^"]+)" has absolute position \((?P<px>[\d.]+), (?P<py>[\d.]+)\)'
))
def check_pin_abs_position(context, net, comp, px, py):
    assert net  in context['nets'],  f"Net {net!r} not found"
    assert comp in context['comps'], f"Component {comp!r} not found"
    net_id  = context['nets'][net].id
    comp_id = context['comps'][comp].id
    matches = [p for p in context['pins'] if p.net_id == net_id and p.comp_id == comp_id]
    assert matches, f"No pin for ({net!r} → {comp!r})"
    assert matches[0].px == pytest.approx(float(px), abs=1e-6), \
        f"Pin ({comp}).px: expected {px}, got {matches[0].px}"
    assert matches[0].py == pytest.approx(float(py), abs=1e-6), \
        f"Pin ({comp}).py: expected {py}, got {matches[0].py}"


# ---------------------------------------------------------------------------
# BudaSession CLI steps for bdb_net_mode
# ---------------------------------------------------------------------------

@given("a BudaSession with an open BDB and a two-level hierarchy")
def session_with_bdb(context):
    db = _make_two_level_bdb()
    session = BudaSession()
    session.bdb = db
    context['db']      = db
    context['session'] = session


@when(parsers.re(r'I run "(?P<cmd_line>[^"]+)" through the session'))
def step_run_session_cmd(context, cmd_line):
    context['session'].do_command(cmd_line)
    _refresh_pins(context)


@then(parsers.re(r'net "(?P<name>[^"]+)" is not present in the session BDB'))
def check_net_absent_in_bdb(context, name):
    nets = {r.name for r in context['db'].all_nets()}
    assert name not in nets, f"Net {name!r} should not be in BDB, but was found"


@then(parsers.re(r'net "(?P<name>[^"]+)" exists in the session BDB'))
def check_net_present_in_session_bdb(context, name):
    nets = {r.name for r in context['db'].all_nets()}
    assert name in nets, f"Net {name!r} not found in session BDB. Present: {sorted(nets)}"


@then(parsers.re(r'the session BDB has (?P<count>\d+) pins for net "(?P<net>[^"]+)"'))
def check_session_net_pin_count(context, count, net):
    count = int(count)
    nets = {r.name: r for r in context['db'].all_nets()}
    assert net in nets, f"Net {net!r} not found"
    net_id = nets[net].id
    pins = [p for p in context['db'].all_pins() if p.net_id == net_id]
    assert len(pins) == count, (
        f"Net {net!r}: expected {count} pins, got {len(pins)}: "
        + str([(p.comp_id, p.pin_name) for p in pins])
    )


# ── BUDA-1917: a second open_bdb replaces an already-open file-backed BDB ──

def _open_bdb_output(tmp_path, commands):
    s = BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    import contextlib
    with contextlib.redirect_stdout(buf):
        for c in commands:
            s.do_command(c)
    return buf.getvalue()


def test_second_open_bdb_warns_at_the_moment_it_replaces(tmp_path):
    """Persistence splits across two files the moment a flow's own open_bdb
    supersedes an earlier one — the `btcl -i <flow> <ckpt>` shape, where the
    driver arms <ckpt> BEFORE the flow and the only notice was a banner
    after the whole flow had run.  BUDA-1917 says so AT the replacing line,
    naming both files."""
    a, b = str(tmp_path / "a.bdb"), str(tmp_path / "b.bdb")
    out = _open_bdb_output(tmp_path, [f"open_bdb {a}", f"open_bdb {b}"])
    lines = [l for l in out.splitlines() if "BUDA-1917" in l]
    assert len(lines) == 1
    assert a in lines[0] and b in lines[0]


def test_first_open_and_same_file_reopen_stay_silent(tmp_path):
    """A first open has nothing to split from, and re-opening the SAME file
    is a refresh, not a split — neither may warn."""
    a = str(tmp_path / "a.bdb")
    out = _open_bdb_output(tmp_path, [f"open_bdb {a}", f"open_bdb {a}"])
    assert "BUDA-1917" not in out


def test_replacing_a_file_bdb_with_memory_warns_too(tmp_path):
    """A `:memory:` open after a file-backed one abandons the durable file
    just the same (the shape btcl -b's preflight refuses); a :memory:
    PREDECESSOR held nothing durable, so the reverse direction is silent."""
    a = str(tmp_path / "a.bdb")
    out = _open_bdb_output(tmp_path, [f"open_bdb {a}", "open_bdb :memory:"])
    assert out.count("BUDA-1917") == 1
    out = _open_bdb_output(tmp_path, ["open_bdb :memory:", f"open_bdb {a}"])
    assert "BUDA-1917" not in out


def test_same_sql_fixture_reopen_stays_silent(tmp_path):
    """A `.sql` fixture materializes to a FRESH temp binary on every open, so
    a live-connection-path comparison read a same-fixture re-open as a
    different file and false-fired — acutely with `writeback`, where the
    first temp flushed to the source and the second rebuilds from it, so
    nothing split (Codex P2 on #814).  The comparison uses the LOGICAL path;
    a re-open of the same fixture is silent, a different fixture still warns
    with the logical names."""
    fa, fb = tmp_path / "a.bdb.sql", tmp_path / "b.bdb.sql"
    for f in (fa, fb):
        # save-as writes the diffable .sql text this test needs as input.
        _open_bdb_output(tmp_path, [f"open_bdb {tmp_path / 'seed.bdb'}",
                                    f"save_bdb {f}"])
    out = _open_bdb_output(tmp_path, [f"open_bdb {fa} writeback",
                                      f"open_bdb {fa} writeback"])
    assert "BUDA-1917" not in out
    out = _open_bdb_output(tmp_path, [f"open_bdb {fa}", f"open_bdb {fb}"])
    lines = [l for l in out.splitlines() if "BUDA-1917" in l]
    assert len(lines) == 1
    assert str(fa) in lines[0] and str(fb) in lines[0]


def test_exec_error_names_the_failing_substatement(tmp_path):
    # _exec walks the batch statement-by-statement (Codex #819): the error
    # label must be the substatement that FAILED — labeling with the head of
    # the whole input blamed an earlier, successful DELETE, and a DDL
    # literal opening with a newline labeled as "(in: )".  net_via is the
    # SECOND statement of clear_bundles' route-table batch.
    p = str(tmp_path / "x.bdb")
    db = buda.BDB(p)
    import sqlite3
    con = sqlite3.connect(p)
    con.execute("DROP TABLE net_via")
    con.commit()
    con.close()
    with pytest.raises(RuntimeError, match=r"\(in: DELETE FROM net_via\)"):
        db.clear_bundles(False)
