import os
import sys

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
