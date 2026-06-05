import os
import textwrap
import pytest
from pytest_bdd import scenarios, given, when, then, parsers
import buda

scenarios('features/bdb_import.feature')
scenarios('features/bdb_combined.feature')

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

@then(parsers.parse("the database contains {count:d} components"))
def check_component_count(context, count):
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
