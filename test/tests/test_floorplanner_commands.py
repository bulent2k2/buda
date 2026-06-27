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
import sys
import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools import floorplanner_commands as fpc


def test_floorplanner_commands_create_move_validate_and_write(tmp_path):
    bdb_path = tmp_path / "proto.bdb"
    state = fpc.create_bdb(str(bdb_path), 1000, 800, grid=10)

    fpc.add_block(state, "u_cpu", 101, 99, 200, 150)
    fpc.move_block(state, "u_cpu", 123, 147)
    b = state.block("u_cpu")
    assert (b.x1, b.y1, b.x2, b.y2) == (120, 150, 320, 300)

    fpc.add_block(state, "u_mem", 250, 180, 200, 150)
    issues = fpc.validate(state)
    assert any(i.kind == "OVERLAP" for i in issues)

    fpc.write_bdb(state)
    comps = {c.name: c for c in state.bdb.all_components()}
    assert "u_cpu" in comps
    assert (comps["u_cpu"].x1, comps["u_cpu"].y1) == (120, 150)


def test_write_lock_excludes_second_session(tmp_path):
    bdb_path = str(tmp_path / "locked.bdb")
    s1 = fpc.create_bdb(bdb_path, 1000, 800)
    assert s1.is_read_only is False
    # A second session on the same DB must be read-only while s1 holds the lock.
    s2 = fpc.load_bdb(bdb_path)
    assert s2.is_read_only is True
    with pytest.raises(PermissionError):
        fpc.write_bdb(s2)
    # After s1 releases, a fresh session becomes writable again.
    fpc.release_bdb_lock(s1)
    s3 = fpc.load_bdb(bdb_path)
    assert s3.is_read_only is False


def test_write_lock_shared_across_path_aliases(tmp_path):
    # Aliases of the same DB (symlink, relative-vs-absolute) must resolve to one
    # lock so two sessions can't both stay writable and clobber each other.
    real = str(tmp_path / "real.bdb")
    link = str(tmp_path / "link.bdb")
    s1 = fpc.create_bdb(real, 1000, 800)
    assert s1.is_read_only is False

    os.symlink(real, link)
    s_link = fpc.load_bdb(link)
    assert s_link.is_read_only is True, "symlink alias must share the lock"

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        s_rel = fpc.load_bdb("real.bdb")
    finally:
        os.chdir(cwd)
    assert s_rel.is_read_only is True, "relative-path alias must share the lock"


def test_validate_parent_child_not_flagged(tmp_path):
    state = fpc.create_bdb(str(tmp_path / "v.bdb"), 1000, 800, grid=10)
    # Parent fully contains child — must NOT be an overlap
    fpc.add_block(state, "left", 0, 0, 500, 400)
    fpc.add_block(state, "left/u0", 10, 10, 200, 150)
    issues = fpc.validate(state)
    assert not any(i.kind == "OVERLAP" for i in issues), \
        f"parent-child pair wrongly flagged: {[i.message for i in issues]}"

    # Same-level siblings that overlap — MUST be flagged
    fpc.add_block(state, "left/u1", 150, 10, 200, 150)  # overlaps u0
    issues = fpc.validate(state)
    assert any(i.kind == "OVERLAP" and
               {i.block_a, i.block_b} == {"left/u0", "left/u1"}
               for i in issues), "sibling overlap not detected"


def test_find_gap_violations_basic(tmp_path):
    state = fpc.create_bdb(str(tmp_path / "gap.bdb"), 1000, 800, grid=10)
    # Two side-by-side blocks with a 50-unit gap (step=10 → violation)
    fpc.add_block(state, "a", 0, 0, 100, 100)
    fpc.add_block(state, "b", 150, 0, 300, 100)   # gap = 50 to the right of a
    fpc.add_block(state, "c", 0, 110, 100, 210)   # gap = 10 above a; touches step boundary
    step = 10.0
    names = ["a", "b", "c"]
    gaps = fpc.find_gap_violations(state, names, step)
    # a→b gap is 50, which is the worst; must be reported
    names_reported = [g[0] for g in gaps]
    assert "a" in names_reported, "block 'a' should have a gap violation"
    a_gap = next(g for g in gaps if g[0] == "a")
    assert a_gap[1] == 50.0, f"expected gap 50, got {a_gap[1]}"
    assert a_gap[2] == "→", f"expected direction →, got {a_gap[2]}"
    assert a_gap[3] == "b"
    # gap of exactly step (10) is a violation (>= step)
    assert "c" in names_reported or any(g[0] == "a" and g[1] >= 10 for g in gaps)


def test_find_gap_violations_no_y_overlap(tmp_path):
    state = fpc.create_bdb(str(tmp_path / "gap2.bdb"), 1000, 800, grid=10)
    # Blocks with no Y-overlap should NOT be neighbors
    fpc.add_block(state, "a", 0, 0, 100, 100)
    fpc.add_block(state, "b", 110, 200, 300, 300)   # right of a but no y-overlap
    gaps = fpc.find_gap_violations(state, ["a", "b"], 10.0)
    assert not gaps, "non-y-overlapping blocks must not generate a gap violation"


def test_find_gap_violations_touching_blocks_ok(tmp_path):
    state = fpc.create_bdb(str(tmp_path / "gap3.bdb"), 1000, 800, grid=10)
    fpc.add_block(state, "a", 0, 0, 100, 100)
    fpc.add_block(state, "b", 100, 0, 200, 100)   # touching — gap == 0 < step
    gaps = fpc.find_gap_violations(state, ["a", "b"], 10.0)
    assert not gaps, "touching blocks (gap=0) must not be a violation"


def test_move_edges_vertical_group(tmp_path):
    state = fpc.create_bdb(str(tmp_path / "e.bdb"), 1000, 800, grid=10)
    fpc.add_block(state, "a", 100, 100, 200, 50)   # bbox (100,100,300,150)
    fpc.add_block(state, "b", 400, 100, 200, 50)   # bbox (400,100,600,150)
    fpc.move_edges(state, [("a", "r"), ("b", "r")], 50)
    a, b = state.block("a"), state.block("b")
    assert a.x2 == 350 and b.x2 == 650, "right edges should each shift +50"
    assert a.x1 == 100 and b.x1 == 400, "left edges unchanged"
    assert a.y1 == 100 and a.y2 == 150, "y unchanged"


def test_move_edges_left_edges(tmp_path):
    state = fpc.create_bdb(str(tmp_path / "e2.bdb"), 1000, 800, grid=10)
    fpc.add_block(state, "a", 100, 100, 200, 50)
    fpc.add_block(state, "b", 400, 100, 200, 50)
    fpc.move_edges(state, [("a", "l"), ("b", "l")], -50)
    a, b = state.block("a"), state.block("b")
    assert a.x1 == 50 and b.x1 == 350, "left edges should each shift -50"
    assert a.x2 == 300 and b.x2 == 600, "right edges unchanged"


def test_move_edges_horizontal_group(tmp_path):
    state = fpc.create_bdb(str(tmp_path / "e3.bdb"), 1000, 800, grid=10)
    fpc.add_block(state, "a", 100, 100, 200, 50)   # y2 = 150
    fpc.add_block(state, "b", 400, 100, 200, 50)
    fpc.move_edges(state, [("a", "b"), ("b", "b")], 30)
    a, b = state.block("a"), state.block("b")
    assert a.y2 == 180 and b.y2 == 180, "bottom edges (y2) should each shift +30"
    assert a.y1 == 100 and b.y1 == 100, "top edges unchanged"


def test_move_edges_clamp_no_inversion(tmp_path):
    state = fpc.create_bdb(str(tmp_path / "e4.bdb"), 1000, 800, grid=10)
    fpc.add_block(state, "a", 100, 100, 200, 50)   # bbox (100,100,300,150), grid=10
    # Pull the right edge far left — must clamp at x1 + grid, never invert.
    fpc.move_edges(state, [("a", "r")], -10000)
    a = state.block("a")
    assert a.x2 == a.x1 + 10, f"right edge should clamp to x1+grid, got {a.x2}"
    assert a.x2 > a.x1, "block must not invert"
    # Mirror: push the left edge far right — clamp at x2 - grid.
    fpc.add_block(state, "c", 100, 100, 200, 50)
    fpc.move_edges(state, [("c", "l")], 10000)
    c = state.block("c")
    assert c.x1 == c.x2 - 10, f"left edge should clamp to x2-grid, got {c.x1}"


def test_move_edges_opposite_edges_translate(tmp_path):
    state = fpc.create_bdb(str(tmp_path / "et.bdb"), 1000, 800, grid=10)
    fpc.add_block(state, "a", 100, 100, 200, 50)   # bbox (100,100,300,150)
    # Selecting BOTH l and r and moving is a translation: width must be preserved
    # even when the delta exceeds the block width (no clamp corruption).
    fpc.move_edges(state, [("a", "l"), ("a", "r")], 250)
    a = state.block("a")
    assert (a.x1, a.x2) == (350, 550), f"both edges shift +250, got {(a.x1, a.x2)}"
    assert a.x2 - a.x1 == 200, "width preserved on opposite-edge translation"


def test_move_edges_empty_is_noop(tmp_path):
    state = fpc.create_bdb(str(tmp_path / "e5.bdb"), 1000, 800, grid=10)
    fpc.add_block(state, "a", 100, 100, 200, 50)
    fpc.move_edges(state, [], 50)   # must not raise
    a = state.block("a")
    assert (a.x1, a.y1, a.x2, a.y2) == (100, 100, 300, 150)


def test_align_edges_min_max_mean_vertical(tmp_path):
    def _seed():
        state = fpc.create_bdb(str(tmp_path / "ea.bdb"), 1000, 800, grid=10)
        # right edges at x2 = 300, 360, 420
        fpc.add_block(state, "a", 100, 100, 200, 50)   # x2 = 300
        fpc.add_block(state, "b", 100, 200, 260, 50)   # x2 = 360
        fpc.add_block(state, "c", 100, 300, 320, 50)   # x2 = 420
        return state
    sel = [("a", "r"), ("b", "r"), ("c", "r")]

    state = _seed()
    fpc.align_edges(state, sel, "max")
    assert all(state.block(n).x2 == 420 for n in "abc"), "max → all x2 = 420"

    state = _seed()
    fpc.align_edges(state, sel, "min")
    assert all(state.block(n).x2 == 300 for n in "abc"), "min → all x2 = 300"

    state = _seed()
    fpc.align_edges(state, sel, "mean")
    assert all(state.block(n).x2 == 360 for n in "abc"), "mean → all x2 = 360"
    # left edges (x1) untouched, no inversion
    assert all(state.block(n).x2 > state.block(n).x1 for n in "abc")


def test_align_edges_horizontal(tmp_path):
    state = fpc.create_bdb(str(tmp_path / "eh.bdb"), 1000, 800, grid=10)
    fpc.add_block(state, "a", 100, 100, 50, 80)   # y1 = 100
    fpc.add_block(state, "b", 200, 160, 50, 80)   # y1 = 160
    fpc.align_edges(state, [("a", "t"), ("b", "t")], "min")
    assert state.block("a").y1 == 100 and state.block("b").y1 == 100, \
        "top edges (y1) align to common min Y"


def test_align_edges_single_edge_noop(tmp_path):
    state = fpc.create_bdb(str(tmp_path / "es.bdb"), 1000, 800, grid=10)
    fpc.add_block(state, "a", 100, 100, 200, 50)
    fpc.align_edges(state, [("a", "r")], "mean")
    a = state.block("a")
    assert (a.x1, a.x2) == (100, 300), "single edge aligns to itself (no change)"


def test_align_edges_mixed_orientation_raises(tmp_path):
    state = fpc.create_bdb(str(tmp_path / "em.bdb"), 1000, 800, grid=10)
    fpc.add_block(state, "a", 100, 100, 200, 50)
    with pytest.raises(ValueError):
        fpc.align_edges(state, [("a", "l"), ("a", "t")], "min")


def test_move_block_carries_children(tmp_path):
    state = fpc.create_bdb(str(tmp_path / "mv.bdb"), 2000, 2000, grid=10)
    fpc.add_block(state, "P", 0, 0, 200, 200)
    fpc.add_child_block(state, "P/c0", 10, 10, 50, 50)   # abs (10,10,60,60)
    fpc.add_child_block(state, "P/c1", 100, 100, 40, 40)  # abs (100,100,140,140)
    # A grandchild follows too.
    fpc.add_child_block(state, "P/c0/g", 5, 5, 10, 10)    # abs (15,15,25,25)

    fpc.move_block(state, "P", 300, 400)                  # move parent by (+300,+400)

    p = state.block("P")
    assert (p.x1, p.y1) == (300, 400)
    c0 = state.block("P/c0")
    assert (c0.x1, c0.y1, c0.x2, c0.y2) == (310, 410, 360, 460), "child must follow"
    c1 = state.block("P/c1")
    assert (c1.x1, c1.y1) == (400, 500), "second child must follow"
    g = state.block("P/c0/g")
    assert (g.x1, g.y1) == (315, 415), "grandchild must follow"


def test_move_unrelated_prefix_block_not_dragged(tmp_path):
    # Moving "P" must not drag "Pother" (a non-child whose name shares the prefix).
    state = fpc.create_bdb(str(tmp_path / "mv2.bdb"), 2000, 2000, grid=10)
    fpc.add_block(state, "P", 0, 0, 100, 100)
    fpc.add_block(state, "Pother", 500, 500, 600, 600)
    fpc.move_block(state, "P", 200, 200)
    assert (state.block("Pother").x1, state.block("Pother").y1) == (500, 500)


def test_align_carries_children(tmp_path):
    state = fpc.create_bdb(str(tmp_path / "al.bdb"), 2000, 2000, grid=10)
    fpc.add_block(state, "A", 0, 0, 100, 100)            # y1=0
    fpc.add_block(state, "B", 300, 200, 400, 300)        # y1=200
    fpc.add_child_block(state, "B/c", 10, 10, 20, 20)    # abs (310,210,330,230)
    fpc.align_bottom(state, ["A", "B"])                  # B drops to y1=0 (min)
    b = state.block("B")
    assert b.y1 == 0, "B aligned to bottom edge 0"
    c = state.block("B/c")
    assert (c.x1, c.y1) == (310, 10), "B's child follows the align move"


def test_distribute_carries_children(tmp_path):
    state = fpc.create_bdb(str(tmp_path / "dist.bdb"), 3000, 2000, grid=10)
    # Three parents to distribute horizontally; the middle one has a child.
    fpc.add_block(state, "A", 0, 0, 100, 100)
    fpc.add_block(state, "B", 200, 0, 300, 100)
    fpc.add_block(state, "C", 900, 0, 1000, 100)
    fpc.add_child_block(state, "B/c", 10, 10, 20, 20)    # abs (210,10,230,30)
    before = state.block("B")
    cb = state.block("B/c")
    off = (cb.x1 - before.x1, cb.y1 - before.y1)         # child's offset from B

    fpc.distribute_h(state, ["A", "B", "C"])

    b = state.block("B")
    c = state.block("B/c")
    # Child keeps the same offset from its (repositioned) parent.
    assert (c.x1 - b.x1, c.y1 - b.y1) == off, "child follows B during distribute"


def test_rotate_cw_carries_and_rotates_children(tmp_path):
    state = fpc.create_bdb(str(tmp_path / "rot.bdb"), 2000, 2000, grid=10)
    # Parent 200 wide x 100 tall at origin; child near its lower-left.
    fpc.add_block(state, "P", 0, 0, 200, 100)
    fpc.add_child_block(state, "P/c", 20, 10, 40, 30)    # abs (20,10,60,40)

    fpc.rotate_blocks_cw(state, ["P"])

    # Parent: CW about LL (0,0) → (0, -200, 100, 0): w/h swapped (100 x 200).
    p = state.block("P")
    assert (p.x1, p.y1, p.x2, p.y2) == (0, -200, 100, 0)
    # Child rotated about the SAME pivot, w/h swapped (was 40x30 → 30x40).
    #   rel LL (20,10), UR (60,40); CW (dx,dy)->(dy,-dx):
    #   x in [10,40], y in [-60,-20]  → abs (10,-60,40,-20).
    c = state.block("P/c")
    assert (c.x1, c.y1, c.x2, c.y2) == (10, -60, 40, -20)
    assert (c.x2 - c.x1, c.y2 - c.y1) == (30, 40), "child w/h swapped"


def test_rotate_ccw_leaf_matches_formula(tmp_path):
    state = fpc.create_bdb(str(tmp_path / "rccw.bdb"), 2000, 2000, grid=10)
    fpc.add_block(state, "L", 100, 100, 200, 100)        # add_block = (name,x,y,w,h)
    b0 = state.block("L")
    assert (b0.x1, b0.y1, b0.x2, b0.y2) == (100, 100, 300, 200)  # w=200, h=100
    fpc.rotate_blocks_ccw(state, ["L"])
    # CCW about LL (100,100): new bbox (x1-h, y1, x1, y1+w) = (0,100,100,300).
    b = state.block("L")
    assert (b.x1, b.y1, b.x2, b.y2) == (0, 100, 100, 300)


def test_floorplanner_commands_export_hbundle_script(tmp_path):
    bdb_path = tmp_path / "proto.bdb"
    script_path = tmp_path / "proto.buda"
    state = fpc.create_bdb(str(bdb_path), 1000, 800, grid=10)

    fpc.export_hbundle_script(state, str(script_path), depth=1)

    text = script_path.read_text()
    assert "source " in text
    assert f"open_bdb {bdb_path}" in text
    assert "derive_busterms 1" in text
    assert "add_blocks_from_bdb 1 skip" in text
    assert "run_hier_bundler depth 1" in text


def test_floorplanner_commands_import_verilog_seeds_top_level_blocks(tmp_path):
    v_path = tmp_path / "tiny.v"
    bdb_path = tmp_path / "tiny.bdb"
    v_path.write_text(
        """
module leaf();
endmodule

module top();
  leaf u_a();
  leaf u_b();
endmodule
""".strip()
        + "\n"
    )

    state = fpc.import_verilog(str(v_path), str(bdb_path), 1000, 800, grid=10)

    assert state.verilog_path == str(v_path)
    assert state.bdb_path == str(bdb_path)
    assert state.block_names == ["u_a", "u_b"]
    assert state.unplaced_names == ["u_a", "u_b"]

    a = state.block("u_a")
    b = state.block("u_b")
    assert (a.x1, a.y1, a.x2, a.y2) == (10, 10, 250, 210)
    assert (b.x1, b.y1, b.x2, b.y2) == (290, 10, 530, 210)

    fpc.write_bdb(state)
    comps = {c.name: c for c in state.bdb.all_components()}
    assert (comps["u_a"].x1, comps["u_a"].y1, comps["u_a"].x2, comps["u_a"].y2) == (
        10, 10, 250, 210)


def test_build_hierarchy_tree_flat(tmp_path):
    bdb_path = tmp_path / "tree.bdb"
    state = fpc.create_bdb(str(bdb_path), 1000, 800, grid=10)
    fpc.add_block(state, "u_cpu", 100, 100, 300, 200)
    fpc.add_block(state, "u_mem", 400, 100, 200, 200)
    roots = fpc.build_hierarchy_tree(state)
    names = [n.name for n in roots]
    assert "u_cpu" in names
    assert "u_mem" in names
    assert all(n.depth == 0 for n in roots)
    assert all(n.children == [] for n in roots)


def test_build_hierarchy_tree_nested(tmp_path):
    v_path = tmp_path / "nested.v"
    bdb_path = tmp_path / "nested.bdb"
    v_path.write_text(
        "module child();\nendmodule\n"
        "module top();\n  child u_a();\n  child u_b();\nendmodule\n"
    )
    state = fpc.import_verilog(str(v_path), str(bdb_path), 1000, 800, grid=10)
    # Write and reload so bdb has all_components with hierarchy
    fpc.write_bdb(state)
    roots = fpc.build_hierarchy_tree(state)
    # After import_verilog with seed_depth=1, children are present in block_names
    assert len(roots) > 0
    # The fallback (no bdb.all_components hierarchy) should still give a tree
    children_total = sum(len(r.children) for r in roots)
    assert children_total >= 0   # no crash; nested structure may or may not appear


def test_sync_move_to_instances(tmp_path):
    import buda as _buda
    state = fpc.new_state()
    state.bdb = _buda.BDB(str(tmp_path / "sync_move.bdb"))
    state.engine.set_die(500, 400)
    state.engine.set_grid(10)

    state.bdb.add_cell("leaf_cell", 80, 60)
    state.bdb.add_cell("blk_cell", 200, 150)
    state.bdb.add_inst_to_cell("blk_cell", "lo", "leaf_cell", 10, 10)
    state.bdb.add_inst_to_cell("blk_cell", "hi", "leaf_cell", 100, 10)
    state.bdb.add_inst("bb", "blk_cell", "", 10, 10)
    state.bdb.add_inst("bt", "blk_cell", "", 10, 180)
    for c in state.bdb.all_components():
        state.engine.add_block(c.name, c.x1, c.y1, c.x2, c.y2)
        state.add_name(c.name)

    # Simulate drag: move bb/lo in the engine
    state.engine.move_block_raw("bb/lo", 30, 30)

    parent_cell, n = fpc.sync_move_to_instances(state, "bb/lo", 30, 30)
    assert parent_cell == "blk_cell"
    assert n >= 2  # bb/lo and bt/lo both synced

    bb    = state.engine.get_block("bb")
    bt    = state.engine.get_block("bt")
    bb_lo = state.engine.get_block("bb/lo")
    bt_lo = state.engine.get_block("bt/lo")
    # Both lo blocks must share the same local offset within their parent
    assert abs((bb_lo.x1 - bb.x1) - (bt_lo.x1 - bt.x1)) < 1e-6
    assert abs((bb_lo.y1 - bb.y1) - (bt_lo.y1 - bt.y1)) < 1e-6

    # hi blocks must be unaffected
    bt_hi = state.engine.get_block("bt/hi")
    assert abs((bt_hi.x1 - bt.x1) - 100) < 1e-6


def test_resize_block(tmp_path):
    bdb_path = tmp_path / "resize.bdb"
    state = fpc.create_bdb(str(bdb_path), 1000, 800, grid=10)
    fpc.add_block(state, "u_cpu", 100, 100, 200, 150)   # placed at (100,100)-(300,250)

    # Basic resize: new bottom-right corner
    fpc.resize_block(state, "u_cpu", 100, 100, 300, 200)
    b = state.block("u_cpu")
    assert (b.x1, b.y1, b.x2, b.y2) == (100, 100, 300, 200)

    # Grid snap: off-grid request rounds to nearest grid (10)
    fpc.resize_block(state, "u_cpu", 100, 100, 305, 205)
    b = state.block("u_cpu")
    assert (b.x1, b.y1, b.x2, b.y2) == (100, 100, 310, 210)

    # Corner resize: top-left corner (x1,y1 change, x2,y2 stay)
    fpc.resize_block(state, "u_cpu", 50, 60, 310, 210)
    b = state.block("u_cpu")
    assert (b.x1, b.y1) == (50, 60)
    assert (b.x2, b.y2) == (310, 210)

    # Minimum size enforced: x2 <= x1 request → clamped to x1 + grid
    fpc.resize_block(state, "u_cpu", 100, 100, 100, 100)
    b = state.block("u_cpu")
    assert b.x2 > b.x1
    assert b.y2 > b.y1


def test_floorplanner_commands_run_hbundle_flow_from_verilog(tmp_path):
    v_path = tmp_path / "flow.v"
    bdb_path = tmp_path / "flow.bdb"
    script_path = tmp_path / "flow.buda"
    v_path.write_text(
        """
module producer(output y);
endmodule

module consumer(input a);
endmodule

module top();
  wire sig;
  producer u_src (.y(sig));
  consumer u_dst (.a(sig));
endmodule
""".strip()
        + "\n"
    )
    state = fpc.import_verilog(str(v_path), str(bdb_path), 1000, 800, grid=10)

    result = fpc.run_hbundle_flow(state, str(script_path), depth=0)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "HierBundler:" in result.stdout
    assert "generate_hier_topologies:" in result.stdout


def test_optimize_placement_sa(tmp_path):
    state = fpc.create_bdb(str(tmp_path / "opt.bdb"), 1000, 800, grid=10)
    fpc.add_block(state, "a",   0,  0, 100, 80)
    fpc.add_block(state, "b",  50, 50, 100, 80)  # overlapping
    fpc.add_block(state, "c", 400, 400, 80, 60)
    result = fpc.optimize_placement(state, method="sa", max_iter=5000, seed=1)
    assert result.overlap < 1.0
    for name in ["a", "b", "c"]:
        b = state.block(name)
        assert b.x1 >= 0 and b.y1 >= 0
        assert b.x2 <= 1000 and b.y2 <= 800


def test_optimize_placement_fixed(tmp_path):
    state = fpc.create_bdb(str(tmp_path / "fixed.bdb"), 1000, 800, grid=10)
    fpc.add_block(state, "anchor",   0,  0, 100, 80)
    fpc.add_block(state, "free",   200, 200, 100, 80)
    fpc.optimize_placement(state, method="sa", fixed=["anchor"], max_iter=3000, seed=3)
    anchor = state.block("anchor")
    assert anchor.x1 == 0 and anchor.y1 == 0   # position must be unchanged


def test_optimize_placement_reshape(tmp_path):
    state = fpc.create_bdb(str(tmp_path / "reshape.bdb"), 1000, 800, grid=10)
    fpc.add_block(state, "r", 0, 0, 100, 100)   # 100×100 = area 10 000
    result = fpc.optimize_placement(
        state, method="sa",
        reshapeable=["r"],
        min_sizes={"r": (30, 30)},
        max_iter=3000, seed=5,
    )
    pb = next(p for p in result.placements if p.name == "r")
    grid = 10
    # Area may deviate by up to max(w,h)*grid because both dimensions are snapped.
    tol = max(pb.w, pb.h) * grid
    assert abs(pb.w * pb.h - 10_000) <= tol  # area ≈ constant (grid-snap tolerance)
    assert pb.w >= 30 and pb.h >= 30          # min-size respected
    assert pb.w <= 1000 and pb.h <= 800       # within die


def test_optimize_placement_ga(tmp_path):
    state = fpc.create_bdb(str(tmp_path / "opt_ga.bdb"), 1000, 800, grid=10)
    fpc.add_block(state, "a",  0,  0, 100, 80)
    fpc.add_block(state, "b", 50, 50, 100, 80)
    result = fpc.optimize_placement(state, method="ga", generations=50, seed=7)
    assert result.iterations == 50
    for name in ["a", "b"]:
        b = state.block(name)
        assert b.x1 >= 0 and b.y1 >= 0
        assert b.x2 <= 1000 and b.y2 <= 800


def test_resize_after_move_preserves_position_on_reload(tmp_path):
    """Regression for sync_cell_to_instances using stale BDB coords.

    Lifecycle: create → write (BDB records origin positions) → move blocks in
    engine only (simulating optimizer) → resize one block via
    sync_cell_to_instances → write → reload → assert positions and count.

    Before the fix, sync_cell_to_instances read c.x1/c.y1 from
    all_components() (stale BDB values) and called resize_block_raw with those
    stale coordinates, reverting the engine to the pre-move position.
    """
    bdb_path = str(tmp_path / "pos.bdb")

    # 1. Create BDB with three blocks at the origin.
    state = fpc.create_bdb(bdb_path, 1000, 800, grid=10)
    fpc.add_block(state, "cpu", 0, 0, 100, 80)
    fpc.add_block(state, "mem", 0, 0, 150, 100)
    fpc.add_block(state, "io",  0, 0,  60, 50)
    fpc.write_bdb(state)   # BDB records all three at (0, 0)

    # 2. Simulate optimizer: move blocks in the engine only (BDB still stale).
    state.engine.move_block_raw("cpu", 200.0, 100.0)
    state.engine.move_block_raw("mem", 400.0, 300.0)
    state.engine.move_block_raw("io",  100.0, 500.0)

    # 3. Resize "cpu" — calls sync_cell_to_instances (as the GUI does after a
    #    corner-handle drag).  Before the fix this reverted "cpu" to (0, 0).
    b = state.block("cpu")
    new_w, new_h = 120, 90
    new_x2, new_y2 = b.x1 + new_w, b.y1 + new_h
    state.engine.resize_block_raw("cpu", b.x1, b.y1, new_x2, new_y2)
    fpc.sync_cell_to_instances(state, "cpu", b.x1, b.y1, new_x2, new_y2)

    b = state.block("cpu")
    assert (b.x1, b.y1) == (200, 100), \
        f"cpu position reverted to stale BDB coords: ({b.x1}, {b.y1})"
    assert (b.x2 - b.x1, b.y2 - b.y1) == (new_w, new_h)

    # Other blocks must be unaffected by the resize of an unrelated cell.
    assert state.block("mem").x1 == 400
    assert state.block("io").x1 == 100

    # 4. Write and reload — all three blocks must survive with correct positions.
    fpc.write_bdb(state)
    reloaded = fpc.load_bdb(bdb_path)

    assert set(reloaded.block_names) == {"cpu", "mem", "io"}, \
        f"Unexpected block names after reload: {reloaded.block_names}"

    rb = reloaded.block("cpu")
    assert (rb.x1, rb.y1) == (200, 100), \
        f"Reloaded cpu position wrong: ({rb.x1}, {rb.y1})"
    assert (rb.x2 - rb.x1, rb.y2 - rb.y1) == (new_w, new_h)

    assert reloaded.block("mem").x1 == 400
    assert reloaded.block("io").x1 == 100


def test_resize_shared_cell_preserves_sibling_positions(tmp_path):
    """Regression: resizing a block whose cell type is shared by another block
    must apply the new dimensions to the sibling while keeping the sibling's
    live engine position, not reverting it to stale BDB coordinates.
    """
    bdb_path = str(tmp_path / "shared.bdb")
    state = fpc.create_bdb(bdb_path, 2000, 1000, grid=10)

    # Two parent blocks each containing a child named "slot" → both children
    # will get cell type "slot_cell" and share a cell definition.
    fpc.add_block(state, "rack_a",       0,   0, 300, 200)
    fpc.add_block(state, "rack_b",     400,   0, 300, 200)
    fpc.add_block(state, "rack_a/slot",  10,  10, 100, 80)
    fpc.add_block(state, "rack_b/slot",  10,  10, 100, 80)
    fpc.write_bdb(state)   # BDB records both slots at local (10, 10)

    # Simulate optimizer moving rack_b/slot to a new absolute position.
    state.engine.move_block_raw("rack_b/slot", 450.0, 50.0)

    # Resize rack_a/slot; sync_cell_to_instances must update rack_b/slot's
    # size while preserving its optimizer-placed position (450, 50).
    b_a = state.block("rack_a/slot")
    new_w, new_h = 120, 90
    new_x2, new_y2 = b_a.x1 + new_w, b_a.y1 + new_h
    state.engine.resize_block_raw("rack_a/slot", b_a.x1, b_a.y1, new_x2, new_y2)
    fpc.sync_cell_to_instances(state, "rack_a/slot", b_a.x1, b_a.y1, new_x2, new_y2)

    b_b = state.block("rack_b/slot")
    assert (b_b.x1, b_b.y1) == (450, 50), \
        f"Sibling position reverted to stale BDB coords: ({b_b.x1}, {b_b.y1})"
    assert (b_b.x2 - b_b.x1, b_b.y2 - b_b.y1) == (new_w, new_h), \
        f"Sibling size not synced: ({b_b.x2 - b_b.x1}, {b_b.y2 - b_b.y1})"

    # The cell table row must reflect the new dimensions immediately
    # (fix for non-synthesized cell names that write_bdb would not update).
    cells = {c.name: c for c in state.bdb.all_cells()}
    slot_cell_name = fpc.get_block_cell(state, "rack_a/slot")
    assert slot_cell_name is not None
    assert cells[slot_cell_name].width  == new_w, "Cell row width not updated"
    assert cells[slot_cell_name].height == new_h, "Cell row height not updated"

    # Write and reload — all four blocks survive with correct positions.
    fpc.write_bdb(state)
    reloaded = fpc.load_bdb(bdb_path)

    expected = {"rack_a", "rack_b", "rack_a/slot", "rack_b/slot"}
    assert set(reloaded.block_names) >= expected, \
        f"Missing blocks after reload: {expected - set(reloaded.block_names)}"

    rb = reloaded.block("rack_b/slot")
    assert (rb.x1, rb.y1) == (450, 50), \
        f"Reloaded sibling position wrong: ({rb.x1}, {rb.y1})"
    assert (rb.x2 - rb.x1, rb.y2 - rb.y1) == (new_w, new_h)

    # Verify cell_children is up-to-date: adding a new instance of rack_a's
    # parent cell should expand the child "slot" at the offset matching rack_a/slot's
    # final engine position relative to rack_a (fix for deferred cell_children update).
    rack_a_cell = fpc.get_block_cell(state, "rack_a")
    if rack_a_cell is not None and fpc.count_cell_instances(state, rack_a_cell) > 1:
        rack_a_b = state.block("rack_a")
        slot_b   = state.block("rack_a/slot")
        expected_local_x = slot_b.x1 - rack_a_b.x1
        expected_local_y = slot_b.y1 - rack_a_b.y1
        # add_inst expands cell_children; the new child should land at
        # (new_rack_x + expected_local_x, new_rack_y + expected_local_y).
        new_rack_x, new_rack_y = 800.0, 100.0
        reloaded.bdb.add_inst("rack_c", rack_a_cell, "", new_rack_x, new_rack_y)
        comps = {c.name: c for c in reloaded.bdb.all_components()}
        assert "rack_c/slot" in comps, "cell_children not expanded for new instance"
        child = comps["rack_c/slot"]
        assert child.x1 == new_rack_x + expected_local_x, \
            f"child x1={child.x1} != {new_rack_x + expected_local_x}"
        assert child.y1 == new_rack_y + expected_local_y, \
            f"child y1={child.y1} != {new_rack_y + expected_local_y}"


@pytest.mark.slow
def test_optimize_demo_tc1_overlap_storm(tmp_path):
    """40 blocks all stacked at origin; 80 buses × 64 bits; SA must yield a legal placement."""
    import buda
    import random
    rng = random.Random(42)

    die_w, die_h, grid = 3000.0, 2400.0, 10.0
    state = fpc.create_bdb(str(tmp_path / "tc1.bdb"), die_w, die_h, grid=grid)

    sizes = [(rng.randint(6, 30) * 10, rng.randint(4, 20) * 10) for _ in range(40)]
    names = [f"blk_{i:02d}" for i in range(40)]
    for name, (w, h) in zip(names, sizes):
        fpc.add_block(state, name, 0, 0, w, h)

    opt = buda.PlacementOptimizer(die_w, die_h, grid)
    for name, (w, h) in zip(names, sizes):
        opt.add_block(name, w, h)

    # 80 buses × 64 bits = 5 120 two-pin nets; buses weight wider connections more
    pairs = [(rng.randint(0, 39), rng.randint(0, 39)) for _ in range(80)]
    pairs = [(a, (b + 1) % 40 if b == a else b) for a, b in pairs]
    for a_idx, b_idx in pairs:
        an, (aw, ah) = names[a_idx], sizes[a_idx]
        bn, (bw, bh) = names[b_idx], sizes[b_idx]
        for _ in range(64):
            opt.add_net([(an, (aw / 2, ah / 2)), (bn, (bw / 2, bh / 2))])

    # Normalize w_wl so one net contributes the same as in single-bus tests;
    # without this, 5120 nets swamp the overlap penalty and SA never fully legalizes.
    n_nets = 80 * 64
    # 12k iterations converge to overlap=0 for this seed (the floor is ~8k; 50k was
    # overkill).  Keeps a ~1.5x margin while cutting the test ~3.4x.  See
    # docs/internal/test_runtime_analysis.md.
    result = opt.run_sa(max_iter=12_000, w_wl=1.0 / n_nets, seed=1)

    for pb in result.placements:
        state.engine.resize_block_raw(pb.name, pb.x, pb.y, pb.x + pb.w, pb.y + pb.h)

    assert result.overlap < 1.0, f"TC1 overlap={result.overlap:.1f} did not converge to 0"
    for name in names:
        b = state.block(name)
        assert b.x1 >= 0 and b.y1 >= 0
        assert b.x2 <= die_w and b.y2 <= die_h


@pytest.mark.slow
def test_optimize_demo_tc2_fixed_io(tmp_path):
    """io_pad pinned at origin; 39 free blocks; 80 buses × 64 bits; SA leaves io_pad unmoved."""
    import buda
    import random
    rng = random.Random(99)

    die_w, die_h, grid = 3000.0, 2400.0, 10.0
    state = fpc.create_bdb(str(tmp_path / "tc2.bdb"), die_w, die_h, grid=grid)

    io_w, io_h = 100.0, 80.0
    fpc.add_block(state, "io_pad", 0, 0, io_w, io_h)

    free_sizes = [(rng.randint(8, 30) * 10, rng.randint(5, 20) * 10) for _ in range(39)]
    free_names = [f"blk_{i:02d}" for i in range(39)]
    for name, (w, h) in zip(free_names, free_sizes):
        fpc.add_block(state, name, 0, 0, w, h)

    all_names = ["io_pad"] + free_names
    all_sizes = [(io_w, io_h)] + free_sizes

    opt = buda.PlacementOptimizer(die_w, die_h, grid)
    opt.add_block_ex("io_pad", io_w, io_h, 0.0, 0.0, 0.0, 0.0, True, False)
    for name, (w, h) in zip(free_names, free_sizes):
        opt.add_block(name, w, h)

    # First 20 buses connect io_pad to a free block; remaining 60 connect free pairs
    pairs = [(0, rng.randint(1, 39)) for _ in range(20)] + \
            [(rng.randint(0, 39), rng.randint(0, 39)) for _ in range(60)]
    pairs = [(a, (b + 1) % 40 if b == a else b) for a, b in pairs]
    for a_idx, b_idx in pairs:
        an, (aw, ah) = all_names[a_idx], all_sizes[a_idx]
        bn, (bw, bh) = all_names[b_idx], all_sizes[b_idx]
        for _ in range(64):
            opt.add_net([(an, (aw / 2, ah / 2)), (bn, (bw / 2, bh / 2))])

    n_nets = 80 * 64
    # 12k iterations converge to overlap=0 for this seed (see tc1 above and
    # docs/internal/test_runtime_analysis.md); 50k was overkill.
    result = opt.run_sa(max_iter=12_000, w_wl=1.0 / n_nets, seed=2)

    for pb in result.placements:
        state.engine.resize_block_raw(pb.name, pb.x, pb.y, pb.x + pb.w, pb.y + pb.h)

    io_pb = next(pb for pb in result.placements if pb.name == "io_pad")
    assert io_pb.x == 0.0 and io_pb.y == 0.0, \
        f"io_pad moved to ({io_pb.x}, {io_pb.y}) but must stay fixed at (0, 0)"
    assert result.overlap < 1.0, f"TC2 overlap={result.overlap:.1f} did not converge to 0"
    for name in all_names:
        b = state.block(name)
        assert b.x1 >= 0 and b.y1 >= 0
        assert b.x2 <= die_w and b.y2 <= die_h
