"""
Keepout Zone feature tests.
Covers the scenarios in features/keepout_zone.feature.
"""
import pytest
import buda
from pytest_bdd import given, when, then, parsers, scenario

# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

@scenario('features/keepout_zone.feature', 'Global Planner Detours Around Keepout')
def test_global_planner_detours_around_keepout():
    pass

@scenario('features/keepout_zone.feature', 'Detailed Router Blocks Tracks in Keepout')
def test_detailed_router_blocks_tracks_in_keepout():
    pass

# ---------------------------------------------------------------------------
# Step Definitions
# ---------------------------------------------------------------------------

@given('a layer stack with:')
def given_layer_stack(ctx, datatable):
    ls = buda.LayerStack()
    for name, lid_str, dir_str, type_str in datatable[1:]:
        lid = int(lid_str)
        dir = buda.LayerDir.HORIZONTAL if dir_str == "HORIZONTAL" else buda.LayerDir.VERTICAL
        type = buda.LayerType.TOP if type_str == "TOP" else buda.LayerType.LOW
        ls.add_layer(lid, name, dir, type)
    ctx['ls'] = ls

@given('a floorplan with blocks:')
def given_floorplan_blocks(ctx, datatable):
    for name, x1, y1, x2, y2 in datatable[1:]:
        ctx['fp'].add_block(name, int(x1), int(y1), int(x2), int(y2))

@given(parsers.parse('a keepout zone at ({x1:d}, {y1:d}) to ({x2:d}, {y2:d}) for layer "{layer_name}"'), target_fixture='koz_layer')
def given_keepout_zone_name(ctx, x1, y1, x2, y2, layer_name):
    # Map name to ID
    lid = 4 if layer_name == "M4" else 5
    ctx['fp'].add_keepout_zone(x1, y1, x2, y2, [lid])
    return lid

@given(parsers.parse('a keepout zone at ({x1:d}, {y1:d}) to ({x2:d}, {y2:d}) for layer {layer_id:d}'))
def given_keepout_zone_id(ctx, x1, y1, x2, y2, layer_id):
    ctx['fp'].add_keepout_zone(x1, y1, x2, y2, [layer_id])

@when(parsers.parse('I generate topologies for bundle "{bundle_hint}"'))
def when_generate_topologies(ctx, bundle_hint):
    gen = buda.TopologyGenerator(ctx['fp'])
    gen.set_layer_ids(ctx['layer_h'], ctx['layer_v'])
    
    # We need to find the blocks for this bundle.
    # In the feature file unit1->unit2
    src, dst = bundle_hint.split("->")
    ctx['candidates'] = gen.generate_candidates(src, dst)

@when('I run the global planner')
def when_run_global_planner(ctx):
    ls = ctx.get('ls')
    if ls is None:
        ls = buda.LayerStack()
        ls.add_layer(4, "M4", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
        ls.add_layer(5, "M5", buda.LayerDir.VERTICAL,   buda.LayerType.TOP)

    router = buda.CongestionPlanner(ctx['fp'], ls)
    router.build_congestion_map()

    bundle = buda.HBundle()
    bundle.id = 1

    w = buda.BundleWrapper()
    w.original_bundle = bundle
    w.width = 10.0
    w.candidates = ctx['candidates']
    
    assignments = router.optimize_topologies([w], 1)
    ctx['assignment'] = assignments[0] if assignments else None

@then(parsers.parse('the selected topology should not intersect the keepout region on "{layer_name}"'))
def then_topology_not_intersect(ctx, layer_name):
    asn = ctx['assignment']
    assert asn is not None
    topo = ctx['candidates'][asn.topo_index]
    
    # Check each segment on the specified layer
    lid = 4 if layer_name == "M4" else 5
    kozs = ctx['fp'].get_keepout_zones()
    
    for i, seg in enumerate(topo.segments):
        if asn.seg_layers[i] == lid:
            # Check intersection with any KOZ for this layer
            for koz in kozs:
                if lid in koz.layer_ids:
                    r = koz.bbox
                    # Axis-aligned segment intersection with Rect
                    is_h = (seg.start.y == seg.end.y)
                    if is_h:
                        if seg.start.y >= r.y1 and seg.start.y <= r.y2:
                            slo = min(seg.start.x, seg.end.x)
                            shi = max(seg.start.x, seg.end.x)
                            if not (shi < r.x1 or slo > r.x2):
                                pytest.fail(f"Horizontal segment {i} intersects KOZ at y={seg.start.y}")
                    else:
                        if seg.start.x >= r.x1 and seg.start.x <= r.x2:
                            slo = min(seg.start.y, seg.end.y)
                            shi = max(seg.start.y, seg.end.y)
                            if not (shi < r.y1 or slo > r.y2):
                                pytest.fail(f"Vertical segment {i} intersects KOZ at x={seg.start.x}")

@then(parsers.parse('the selected topology should be "{type_prefix}" or a detour shape'))
def then_topology_detour(ctx, type_prefix):
    asn = ctx['assignment']
    topo = ctx['candidates'][asn.topo_index]
    # L_HV usually would be chosen (index 0 or 1), but KOZ should force detour
    assert not topo.type.startswith("L_HV"), f"Should have detoured, but got {topo.type}"

@given(parsers.parse('a track pattern for layer {layer_id:d} with "{type}" width {width:f} and spacing {spacing:f}'), target_fixture='grid_stack')
def given_track_pattern(layer_id, type, width, spacing):
    stack = buda.RoutingGridStack()
    slots = [buda.TrackSlot(type, type.lower(), width, spacing)]
    pat = buda.TrackPattern(0.0, slots)
    # Assume horizontal for layer 4 for testing
    stack.define_layer(layer_id, pat, layer_id == 4)
    return stack

@when(parsers.parse('I query signal tracks for layer {layer_id:d} in range y=[{ylo:d}, {yhi:d}] at x={x:d}'), target_fixture='query_result')
def when_query_signal_tracks(ctx, grid_stack, layer_id, ylo, yhi, x):
    # Ensure keepouts are synced from FP to GridStack if not already
    for koz in ctx['fp'].get_keepout_zones():
        if layer_id in koz.layer_ids:
            grid_stack.add_keepout(layer_id, koz.bbox.x1, koz.bbox.y1, koz.bbox.x2, koz.bbox.y2)
            
    grid = grid_stack.get_layer_grid(layer_id)
    # x is along coordinate, [lo, hi] is perpendicular range
    # Wait, the feature file says x=400, y=[0, 400]. 
    # If horizontal (is_horizontal=True), then 'x' is coordinate along track (X), 
    # and [lo, hi] is perpendicular range (Y).
    return grid.signal_tracks_in(float(x), float(ylo), float(yhi))

@then(parsers.parse('the number of available signal tracks should be {count:d}'))
def then_num_tracks(query_result, count):
    assert len(query_result) == count

@then('the number of available signal tracks should be greater than 0')
def then_num_tracks_gt0(query_result):
    assert len(query_result) > 0
