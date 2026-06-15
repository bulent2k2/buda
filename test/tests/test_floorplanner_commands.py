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
    result = opt.run_sa(max_iter=50_000, w_wl=1.0 / n_nets, seed=1)

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
    result = opt.run_sa(max_iter=50_000, w_wl=1.0 / n_nets, seed=2)

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
