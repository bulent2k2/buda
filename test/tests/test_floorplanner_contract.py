import pytest
from pytest_bdd import scenario, scenarios, given, when, then, parsers
import buda


_GUI_FEATURE = "features/floorplanner_gui_basic.feature"


@pytest.mark.xfail(strict=False, reason="Floorplanner GUI is not implemented yet")
@scenario(_GUI_FEATURE, "create a design canvas with die size and grid")
def test_gui_create_design_canvas():
    pass


@pytest.mark.xfail(strict=False, reason="Floorplanner GUI is not implemented yet")
@scenario(_GUI_FEATURE, "place top-level modules by dragging them on the canvas")
def test_gui_place_top_level_modules():
    pass


@pytest.mark.xfail(strict=False, reason="Floorplanner GUI is not implemented yet")
@scenario(_GUI_FEATURE, "resize a block with handles")
def test_gui_resize_block_with_handles():
    pass


@pytest.mark.xfail(strict=False, reason="Floorplanner GUI is not implemented yet")
@scenario(_GUI_FEATURE, "align selected blocks to a common edge")
def test_gui_align_selected_blocks():
    pass


@pytest.mark.xfail(strict=False, reason="Floorplanner GUI is not implemented yet")
@scenario(_GUI_FEATURE, "distribute selected blocks horizontally")
def test_gui_distribute_selected_blocks():
    pass


@pytest.mark.xfail(strict=False, reason="Floorplanner GUI is not implemented yet")
@scenario(_GUI_FEATURE, "edit a child block inside a hierarchical parent")
def test_gui_edit_child_block():
    pass


@pytest.mark.xfail(strict=False, reason="Floorplanner GUI is not implemented yet")
@scenario(_GUI_FEATURE, "rotate a block and transform its descendants")
def test_gui_rotate_block():
    pass


@pytest.mark.xfail(strict=False, reason="Floorplanner GUI is not implemented yet")
@scenario(_GUI_FEATURE, "validate a floorplan before HBundle generation")
def test_gui_validate_floorplan():
    pass


@pytest.mark.xfail(strict=False, reason="Floorplanner GUI is not implemented yet")
@scenario(_GUI_FEATURE, "export an HBundle-ready BUDA script")
def test_gui_export_hbundle_script():
    pass


scenarios("features/floorplanner_engine_api.feature")


@pytest.fixture
def floorplanner_context():
    return {}


@given("a new floorplanner GUI session")
def given_gui_session(floorplanner_context):
    pytest.fail("floorplanner GUI session is not implemented")


@given("a new floorplanner engine")
def given_engine(floorplanner_context):
    floorplanner_context["engine"] = buda.FloorplannerEngine()


@given(parsers.re(r'the user has imported Verilog hierarchy "(?P<top>[^"]+)"'))
def given_imported_verilog(floorplanner_context, top):
    pass


@given(parsers.re(r'top-level modules "(?P<a>[^"]+)", "(?P<b>[^"]+)", and "(?P<c>[^"]+)" are unplaced'))
def given_unplaced_modules(floorplanner_context, a, b, c):
    pass


@given(parsers.re(r'block "(?P<name>[^"]+)" spans from \((?P<x1>-?\d+), (?P<y1>-?\d+)\) to \((?P<x2>-?\d+), (?P<y2>-?\d+)\)'))
def given_block_span(floorplanner_context, name, x1, y1, x2, y2):
    pass


@given(parsers.re(r'engine block "(?P<name>[^"]+)" spans from \((?P<x1>-?\d+), (?P<y1>-?\d+)\) to \((?P<x2>-?\d+), (?P<y2>-?\d+)\)'))
def given_engine_block_span(floorplanner_context, name, x1, y1, x2, y2):
    floorplanner_context["engine"].add_block(
        name, float(x1), float(y1), float(x2), float(y2))


@given(parsers.re(r'the engine has die size (?P<w>\d+) by (?P<h>\d+) microns'))
def given_engine_die(floorplanner_context, w, h):
    floorplanner_context["engine"].set_die(float(w), float(h))


@given(parsers.re(r'the engine has placement grid (?P<grid>\d+) microns'))
def given_engine_grid(floorplanner_context, grid):
    floorplanner_context["engine"].set_grid(float(grid))


@given(parsers.re(r'child block "(?P<name>[^"]+)" has local origin \((?P<x>-?\d+), (?P<y>-?\d+)\) and size (?P<w>\d+) by (?P<h>\d+)'))
def given_child_local(floorplanner_context, name, x, y, w, h):
    pass


@given(parsers.re(r'engine child block "(?P<name>[^"]+)" has local origin \((?P<x>-?\d+), (?P<y>-?\d+)\) and size (?P<w>\d+) by (?P<h>\d+)'))
def given_engine_child_local(floorplanner_context, name, x, y, w, h):
    floorplanner_context["engine"].add_child_block(
        name, float(x), float(y), float(w), float(h))


@given("the user has placed all modules through depth 1")
def given_all_modules_placed_depth1(floorplanner_context):
    pass


@when(parsers.re(r'the user sets the die to (?P<w>\d+) by (?P<h>\d+) microns'))
def when_user_sets_die(floorplanner_context, w, h):
    pass


@when(parsers.re(r'the user sets the placement grid to (?P<grid>\d+) microns'))
def when_user_sets_grid(floorplanner_context, grid):
    pass


@when(parsers.re(r'the user places "(?P<name>[^"]+)" at \((?P<x>-?\d+), (?P<y>-?\d+)\) with size (?P<w>\d+) by (?P<h>\d+)'))
def when_user_places_block(floorplanner_context, name, x, y, w, h):
    pass


@when(parsers.re(r'the user drags the east resize handle of "(?P<name>[^"]+)" to x=(?P<x>-?\d+)'))
def when_user_resizes_east(floorplanner_context, name, x):
    pass


@when(parsers.re(r'the user selects "(?P<a>[^"]+)" and "(?P<b>[^"]+)"'))
def when_user_selects_two(floorplanner_context, a, b):
    pass


@when(parsers.re(r'the user selects "(?P<a>[^"]+)", "(?P<b>[^"]+)", and "(?P<c>[^"]+)"'))
def when_user_selects_three(floorplanner_context, a, b, c):
    pass


@when("the user aligns the selected blocks to the bottom edge")
def when_user_aligns_bottom(floorplanner_context):
    pass


@when("the user distributes the selected blocks horizontally")
def when_user_distributes_h(floorplanner_context):
    pass


@when(parsers.re(r'the user descends into "(?P<name>[^"]+)"'))
def when_user_descends(floorplanner_context, name):
    pass


@when(parsers.re(r'the user moves child block "(?P<name>[^"]+)" to local origin \((?P<x>-?\d+), (?P<y>-?\d+)\)'))
def when_user_moves_child_local(floorplanner_context, name, x, y):
    pass


@when(parsers.re(r'the user rotates "(?P<name>[^"]+)" by (?P<degrees>\d+) degrees'))
def when_user_rotates(floorplanner_context, name, degrees):
    pass


@when("the user runs floorplan validation")
def when_user_validates(floorplanner_context):
    pass


@when("the user exports the hierarchical bundle flow script for depth 1")
def when_user_exports_script(floorplanner_context):
    pass


@when(parsers.re(r'the engine moves block "(?P<name>[^"]+)" to raw origin \((?P<x>-?\d+), (?P<y>-?\d+)\)'))
def when_engine_moves_raw(floorplanner_context, name, x, y):
    floorplanner_context["engine"].move_block_raw(name, float(x), float(y))


@when(parsers.re(r'the engine sets die size to (?P<w>-?\d+) by (?P<h>-?\d+) microns'))
def when_engine_sets_die(floorplanner_context, w, h):
    floorplanner_context["engine"].set_die(float(w), float(h))


@when("the engine validates placement")
def when_engine_validates(floorplanner_context):
    floorplanner_context["issues"] = floorplanner_context["engine"].validate()


@when(parsers.re(r'the engine aligns blocks "(?P<names>[^"]+)" to the bottom edge'))
def when_engine_aligns_bottom(floorplanner_context, names):
    floorplanner_context["engine"].align_bottom(names.split(","))


@when(parsers.re(r'the engine moves child block "(?P<name>[^"]+)" to local origin \((?P<x>-?\d+), (?P<y>-?\d+)\)'))
def when_engine_moves_child_local(floorplanner_context, name, x, y):
    floorplanner_context["engine"].move_child_local(name, float(x), float(y))


@when("the engine writes placements to BDB")
def when_engine_writes_bdb(floorplanner_context):
    db = buda.BDB(":memory:")
    floorplanner_context["engine"].write_bdb(db)
    floorplanner_context["bdb"] = db


@then(parsers.re(r'the canvas shows a (?P<w>\d+) by (?P<h>\d+) micron die'))
def then_canvas_die(floorplanner_context, w, h):
    pass


@then(parsers.re(r'block drag operations snap to the (?P<grid>\d+) micron grid'))
def then_drag_snaps(floorplanner_context, grid):
    pass


@then(parsers.re(r'"(?P<name>[^"]+)" spans from \((?P<x1>-?\d+), (?P<y1>-?\d+)\) to \((?P<x2>-?\d+), (?P<y2>-?\d+)\)'))
def then_block_spans(floorplanner_context, name, x1, y1, x2, y2):
    pass


@then(parsers.re(r'engine block "(?P<name>[^"]+)" spans from \((?P<x1>-?\d+), (?P<y1>-?\d+)\) to \((?P<x2>-?\d+), (?P<y2>-?\d+)\)'))
def then_engine_block_spans(floorplanner_context, name, x1, y1, x2, y2):
    b = floorplanner_context["engine"].get_block(name)
    got = (b.x1, b.y1, b.x2, b.y2)
    exp = (float(x1), float(y1), float(x2), float(y2))
    assert got == exp, f"{name}: expected {exp}, got {got}"


@then(parsers.re(r'the cell size for "(?P<name>[^"]+)" is (?P<w>\d+) by (?P<h>\d+)'))
def then_cell_size(floorplanner_context, name, w, h):
    pass


@then("the horizontal gaps between selected blocks are equal")
def then_gaps_equal(floorplanner_context):
    pass


@then(parsers.re(r'child block "(?P<name>[^"]+)" has local origin \((?P<x>-?\d+), (?P<y>-?\d+)\)'))
def then_child_local(floorplanner_context, name, x, y):
    pass


@then(parsers.re(r'engine child block "(?P<name>[^"]+)" has local origin \((?P<x>-?\d+), (?P<y>-?\d+)\)'))
def then_engine_child_local(floorplanner_context, name, x, y):
    got = floorplanner_context["engine"].get_child_local_origin(name)
    exp = (float(x), float(y))
    assert got == exp, f"{name}: expected local origin {exp}, got {got}"


@then(parsers.re(r'descendant "(?P<name>[^"]+)" is transformed with the rotation'))
def then_descendant_rotated(floorplanner_context, name):
    pass


@then(parsers.re(r'validation reports an overlap between "(?P<a>[^"]+)" and "(?P<b>[^"]+)"'))
def then_validation_overlap(floorplanner_context, a, b):
    pass


@then(parsers.re(r'the engine reports an overlap between "(?P<a>[^"]+)" and "(?P<b>[^"]+)"'))
def then_engine_overlap(floorplanner_context, a, b):
    issues = floorplanner_context.get("issues")
    if issues is None:
        issues = floorplanner_context["engine"].validate()
    assert any(
        i.kind == "OVERLAP" and {i.block_a, i.block_b} == {a, b}
        for i in issues
    ), f"Expected overlap between {a} and {b}; got {[(i.kind, i.block_a, i.block_b) for i in issues]}"


@then(parsers.re(r'validation reports "(?P<name>[^"]+)" outside the die'))
def then_validation_outside(floorplanner_context, name):
    pass


@then(parsers.re(r'the engine reports "(?P<name>[^"]+)" outside the die'))
def then_engine_outside(floorplanner_context, name):
    issues = floorplanner_context.get("issues")
    if issues is None:
        issues = floorplanner_context["engine"].validate()
    assert any(
        i.kind == "OUTSIDE_DIE" and i.block_a == name
        for i in issues
    ), f"Expected {name} outside die; got {[(i.kind, i.block_a) for i in issues]}"


@then(parsers.re(r'the engine reports a floorplan error containing "(?P<text>[^"]+)"'))
def then_engine_error(floorplanner_context, text):
    issues = floorplanner_context.get("issues")
    if issues is None:
        issues = floorplanner_context["engine"].validate()
    assert any(
        i.kind == "ERROR" and text in i.message
        for i in issues
    ), f"Expected error containing {text!r}; got {[(i.kind, i.message) for i in issues]}"


@then(parsers.re(r'the script contains "(?P<line>[^"]+)"'))
def then_script_contains(floorplanner_context, line):
    pass


@then(parsers.re(r'BDB component "(?P<name>[^"]+)" spans from \((?P<x1>-?\d+), (?P<y1>-?\d+)\) to \((?P<x2>-?\d+), (?P<y2>-?\d+)\)'))
def then_bdb_component_spans(floorplanner_context, name, x1, y1, x2, y2):
    comps = {c.name: c for c in floorplanner_context["bdb"].all_components()}
    assert name in comps, f"BDB component {name!r} not found"
    c = comps[name]
    got = (c.x1, c.y1, c.x2, c.y2)
    exp = (float(x1), float(y1), float(x2), float(y2))
    assert got == exp, f"{name}: expected BDB bbox {exp}, got {got}"
