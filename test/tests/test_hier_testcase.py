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

"""
Hierarchical test vehicle — pipeline subsystem.

Three-block data-pipeline:  src_i → proc_i (3×pipe_cell stages) → snk_i

depth 0:  src_i  proc_i  snk_i
depth 1:  gen_i  buf_i   pa_i  pb_i  pc_i  rcv_i  store_i

pipe_cell is used three times (pa_i, pb_i, pc_i) — the multiple-occurrence
pattern that will drive cell-level HBundle routing in Phase C.
"""
import pytest
from pytest_bdd import scenarios, given, when, then, parsers
import buda

scenarios('features/hier_testcase.feature')


# ── Hierarchy builder ──────────────────────────────────────────────────────────
#
# Physical layout (µm, not to scale):
#
#  x:  50        250      350                  770   870        1070
#      |  src_i  |  gap   |       proc_i       | gap |  snk_i   |
#  y:  50 ┌─────┐        ┌───────────────────────┐   ┌─────────┐
#         │gen_i│        │ pa_i  | pb_i  | pc_i  │   │rcv_i    │
# 110  ┌──┤ ──  │  ──────┤       │       │       ├───┤    ──   │
#      │  │buf_i│        │       │       │       │   │store_i  │
# 190  └──┤─────┘        └───────┴───────┴───────┘   └─────────┘
#     250
#
# Absolute bounding boxes (x1, y1, x2, y2):
#   src_i         ( 50,  50, 250, 250)
#   src_i/gen_i   ( 70, 110, 150, 190)
#   src_i/buf_i   (150, 110, 230, 190)
#   proc_i        (350,  50, 770, 250)
#   proc_i/pa_i   (370, 110, 480, 190)
#   proc_i/pb_i   (505, 110, 615, 190)
#   proc_i/pc_i   (640, 110, 750, 190)
#   snk_i         (870,  50,1070, 250)
#   snk_i/rcv_i   (890, 110, 970, 190)
#   snk_i/store_i (970, 110,1050, 190)

def _build_pipeline_bdb():
    db = buda.BDB(":memory:")
    # Cell sizes
    db.add_cell("src_cell",    200, 200)
    db.add_cell("proc_cell",   420, 200)
    db.add_cell("snk_cell",    200, 200)
    db.add_cell("gen_cell",     80,  80)
    db.add_cell("buf_cell",     80,  80)
    db.add_cell("pipe_cell",   110,  80)
    db.add_cell("rcv_cell",     80,  80)
    db.add_cell("store_cell",   80,  80)
    # Cell structure (structural templates only — no component rows yet)
    db.add_inst_to_cell("src_cell",  "gen_i",   "gen_cell",    20,  60)
    db.add_inst_to_cell("src_cell",  "buf_i",   "buf_cell",   100,  60)
    db.add_inst_to_cell("proc_cell", "pa_i",    "pipe_cell",   20,  60)
    db.add_inst_to_cell("proc_cell", "pb_i",    "pipe_cell",  155,  60)
    db.add_inst_to_cell("proc_cell", "pc_i",    "pipe_cell",  290,  60)
    db.add_inst_to_cell("snk_cell",  "rcv_i",   "rcv_cell",    20,  60)
    db.add_inst_to_cell("snk_cell",  "store_i", "store_cell", 100,  60)
    # Place top-level instances (eagerly expands cell_children subtrees)
    db.add_inst("src_i",  "src_cell",  "",  50,  50)
    db.add_inst("proc_i", "proc_cell", "", 350,  50)
    db.add_inst("snk_i",  "snk_cell",  "", 870,  50)
    return db


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def context():
    return {}


# ── Background ─────────────────────────────────────────────────────────────────

@given("a BDB populated with the pipeline hierarchy")
def load_pipeline(context):
    db = _build_pipeline_bdb()
    context['db']    = db
    context['comps'] = {c.name: c for c in db.all_components()}


# ── Component count steps ──────────────────────────────────────────────────────

@then(parsers.re(r'the database contains (?P<n>\d+) components'))
def check_component_count(context, n):
    expected = int(n)
    got = len(context['comps'])
    assert got == expected, (
        f"Expected {expected} components, got {got}: {sorted(context['comps'])}"
    )


@then(parsers.re(r'there are (?P<n>\d+) components at depth (?P<d>\d+)'))
def check_components_at_depth(context, n, d):
    expected, depth = int(n), int(d)
    at_depth = [r.name for r in context['comps'].values() if r.depth == depth]
    assert len(at_depth) == expected, (
        f"Expected {expected} components at depth {depth}, "
        f"got {len(at_depth)}: {sorted(at_depth)}"
    )


@then(parsers.re(r'component "(?P<name>[^"]+)" has cell "(?P<cell>[^"]+)" and depth (?P<d>\d+)'))
def check_component_cell_depth(context, name, cell, d):
    r = context['comps'].get(name)
    assert r is not None, f"Component {name!r} not found; present: {sorted(context['comps'])}"
    assert r.cell  == cell,   f"{name!r}: cell {r.cell!r} ≠ {cell!r}"
    assert r.depth == int(d), f"{name!r}: depth {r.depth} ≠ {d}"


# ── Multiple-occurrence steps ──────────────────────────────────────────────────

@then(parsers.re(r'there are (?P<n>\d+) components with cell "(?P<cell>[^"]+)"'))
def check_cell_count(context, n, cell):
    found = [r.name for r in context['comps'].values() if r.cell == cell]
    assert len(found) == int(n), (
        f"Expected {n} instance(s) of {cell!r}, got {len(found)}: {sorted(found)}"
    )


@then(parsers.re(r'the "(?P<cell>[^"]+)" instances are named "(?P<names>[^"]+)"'))
def check_cell_instance_names(context, cell, names):
    expected = {n.strip() for n in names.split(",")}
    found    = {r.name for r in context['comps'].values() if r.cell == cell}
    assert found == expected, (
        f"pipe_cell instances: expected {sorted(expected)}, got {sorted(found)}"
    )


# ── Coordinate steps ───────────────────────────────────────────────────────────

@then(parsers.re(r'component "(?P<name>[^"]+)" has approx bbox '
                 r'(?P<x1>[\d.]+) (?P<y1>[\d.]+) (?P<x2>[\d.]+) (?P<y2>[\d.]+)'))
def check_bbox(context, name, x1, y1, x2, y2):
    r = context['comps'].get(name)
    assert r is not None, f"Component {name!r} not found"
    tol = 0.5  # allow 0.5 µm rounding tolerance
    for axis, got, want in [('x1', r.x1, float(x1)), ('y1', r.y1, float(y1)),
                             ('x2', r.x2, float(x2)), ('y2', r.y2, float(y2))]:
        assert abs(got - want) <= tol, f"{name!r}.{axis}: got {got}, expected {want}"


# ── BustermGen steps ───────────────────────────────────────────────────────────

@when(parsers.re(r'derive_busterms is called with max_depth (?P<d>\d+)'))
def step_derive_busterms(context, d):
    gen = buda.BustermGen(context['db'])
    gen.derive(int(d))
    context['busterms'] = {b.id: b for b in context['db'].all_busterms()}


@then(parsers.re(r'the database contains (?P<n>\d+) busterms'))
def check_busterm_count(context, n):
    bts = context.get('busterms', {})
    assert len(bts) == int(n), (
        f"Expected {n} busterms, got {len(bts)}: {sorted(bts)}"
    )


@then(parsers.re(r'busterm "(?P<bt_id>[^"]+)" exists with depth (?P<d>\d+) and no parent'))
def check_busterm_root(context, bt_id, d):
    bts = context.get('busterms', {})
    bt = bts.get(bt_id)
    assert bt is not None, f"Busterm {bt_id!r} not found; present: {sorted(bts)}"
    assert bt.depth == int(d), f"{bt_id!r}: depth {bt.depth} ≠ {d}"
    assert bt.parent_id == "", (
        f"{bt_id!r}: expected no parent, got parent_id={bt.parent_id!r}"
    )


@then(parsers.re(r'busterm "(?P<bt_id>[^"]+)" has parent "(?P<parent_id>[^"]+)"'))
def check_busterm_parent(context, bt_id, parent_id):
    bts = context.get('busterms', {})
    bt = bts.get(bt_id)
    assert bt is not None, f"Busterm {bt_id!r} not found; present: {sorted(bts)}"
    assert bt.parent_id == parent_id, (
        f"{bt_id!r}: parent {bt.parent_id!r} ≠ {parent_id!r}"
    )


# ── Standalone routing smoke test ──────────────────────────────────────────────

def test_pipeline_bdb_structure():
    """Verify the hierarchy builder produces the expected 10-component structure."""
    db = _build_pipeline_bdb()
    comps = {c.name: c for c in db.all_components()}
    assert len(comps) == 10, f"Expected 10, got {len(comps)}: {sorted(comps)}"
    # Depth-0 blocks
    assert comps['src_i'].depth  == 0
    assert comps['proc_i'].depth == 0
    assert comps['snk_i'].depth  == 0
    # Depth-1 leaves
    for name in ('src_i/gen_i', 'src_i/buf_i',
                 'proc_i/pa_i', 'proc_i/pb_i', 'proc_i/pc_i',
                 'snk_i/rcv_i', 'snk_i/store_i'):
        assert comps[name].depth == 1, f"{name}: depth {comps[name].depth}"
    # pipe_cell used 3 times
    pipes = [c.name for c in comps.values() if c.cell == 'pipe_cell']
    assert sorted(pipes) == ['proc_i/pa_i', 'proc_i/pb_i', 'proc_i/pc_i']


def test_busterms_depth0():
    """BustermGen at depth 0 creates exactly 3 root busterms."""
    db  = _build_pipeline_bdb()
    gen = buda.BustermGen(db)
    gen.derive(0)
    bts = {b.id: b for b in db.all_busterms()}
    assert len(bts) == 3, f"Expected 3 busterms, got {len(bts)}: {sorted(bts)}"
    for bt_id in ('bt:src_i', 'bt:proc_i', 'bt:snk_i'):
        assert bt_id in bts, f"Missing {bt_id}"
        assert bts[bt_id].depth     == 0
        assert bts[bt_id].parent_id == ""


def test_busterms_depth1():
    """BustermGen at depth 1 creates 10 busterms with correct parent links."""
    db  = _build_pipeline_bdb()
    gen = buda.BustermGen(db)
    gen.derive(1)
    bts = {b.id: b for b in db.all_busterms()}
    assert len(bts) == 10, f"Expected 10 busterms, got {len(bts)}: {sorted(bts)}"
    # Depth-1 busterms must link to their depth-0 parents
    expected_parents = {
        'bt:src_i/gen_i':    'bt:src_i',
        'bt:src_i/buf_i':    'bt:src_i',
        'bt:proc_i/pa_i':    'bt:proc_i',
        'bt:proc_i/pb_i':    'bt:proc_i',
        'bt:proc_i/pc_i':    'bt:proc_i',
        'bt:snk_i/rcv_i':    'bt:snk_i',
        'bt:snk_i/store_i':  'bt:snk_i',
    }
    for bt_id, expected_parent in expected_parents.items():
        assert bt_id in bts, f"Missing {bt_id}"
        assert bts[bt_id].parent_id == expected_parent, (
            f"{bt_id}: parent {bts[bt_id].parent_id!r} ≠ {expected_parent!r}"
        )


def test_busterms_idempotent():
    """Calling derive twice gives the same count as calling once."""
    db  = _build_pipeline_bdb()
    gen = buda.BustermGen(db)
    gen.derive(1)
    n1  = len(db.all_busterms())
    gen.derive(1)
    n2  = len(db.all_busterms())
    assert n1 == n2 == 10
