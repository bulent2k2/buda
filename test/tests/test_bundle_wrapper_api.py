"""
Phase D tests: BundleWrapper sub-struct API.

Tests cover:
  - BundleWrapper has .input, .plan, .hier sub-objects
  - BundleInput fields accessible and assignable
  - BundlePlan fields accessible and assignable
  - BundleHierMeta fields accessible and assignable
  - CongestionPlanner writes plan fields into .plan sub-struct
  - NUTS reads .plan.seg_layers and .plan.seg_perp correctly
"""
import pytest
import buda


# ── Sub-struct existence and defaults ─────────────────────────────────────────

def test_bundlewrapper_has_input_plan_hier():
    """BundleWrapper exposes .input, .plan, and .hier sub-objects."""
    w = buda.BundleWrapper()
    assert hasattr(w, "input"), "BundleWrapper missing .input"
    assert hasattr(w, "plan"),  "BundleWrapper missing .plan"
    assert hasattr(w, "hier"),  "BundleWrapper missing .hier"


def test_bundlewrapper_no_flat_fields():
    """Flat BundleWrapper fields from before the split no longer exist."""
    w = buda.BundleWrapper()
    for old_field in ("width", "candidates", "selected_topology_index",
                      "seg_layers", "seg_perp", "level", "priority",
                      "has_reservation"):
        assert not hasattr(w, old_field), (
            f"BundleWrapper still exposes old flat field '.{old_field}' "
            "(should be under .input / .plan / .hier)"
        )


def test_bundleinput_defaults():
    """BundleInput fields have sensible defaults."""
    inp = buda.BundleInput()
    assert inp.width == 1.0
    assert inp.assigned_v_layer == -1
    assert inp.assigned_h_layer == -1
    assert inp.topology_pinned is False
    assert inp.pinned_seg_layers == []
    assert inp.candidates == []


def test_bundleplan_defaults():
    """BundlePlan fields default to -1 / empty."""
    plan = buda.BundlePlan()
    assert plan.selected_topology_index == -1
    assert plan.seg_layers == []
    assert plan.seg_perp == []


def test_bundlehiermeta_defaults():
    """BundleHierMeta fields default to 0 / False."""
    hier = buda.BundleHierMeta()
    assert hier.level == 0
    assert hier.priority == pytest.approx(0.0)
    assert hier.has_reservation is False
    assert hier.res_x1 == hier.res_y1 == hier.res_x2 == hier.res_y2 == 0


# ── Sub-struct field read/write ────────────────────────────────────────────────

def test_bundleinput_field_assignment():
    """BundleInput fields can be set on a BundleWrapper."""
    w = buda.BundleWrapper()
    w.input.width = 8.0
    w.input.topology_pinned = True
    w.input.pinned_seg_layers = [3, 4]
    w.input.assigned_v_layer = 4
    w.input.assigned_h_layer = 3
    assert w.input.width == 8.0
    assert w.input.topology_pinned is True
    assert w.input.pinned_seg_layers == [3, 4]
    assert w.input.assigned_v_layer == 4
    assert w.input.assigned_h_layer == 3


def test_bundleplan_field_assignment():
    """BundlePlan fields can be set on a BundleWrapper."""
    w = buda.BundleWrapper()
    w.plan.selected_topology_index = 2
    w.plan.seg_layers = [3, 4, 3]
    w.plan.seg_perp = [50, 100, 50]
    assert w.plan.selected_topology_index == 2
    assert w.plan.seg_layers == [3, 4, 3]
    assert w.plan.seg_perp == [50, 100, 50]


def test_bundlehiermeta_field_assignment():
    """BundleHierMeta fields can be set on a BundleWrapper."""
    w = buda.BundleWrapper()
    w.hier.level = 2
    w.hier.priority = -20004.0
    w.hier.has_reservation = True
    w.hier.res_x1 = 10; w.hier.res_y1 = 20
    w.hier.res_x2 = 110; w.hier.res_y2 = 120
    assert w.hier.level == 2
    assert w.hier.priority == pytest.approx(-20004.0)
    assert w.hier.has_reservation is True
    assert (w.hier.res_x1, w.hier.res_y1) == (10, 20)
    assert (w.hier.res_x2, w.hier.res_y2) == (110, 120)


def test_bundleinput_original_bundle_roundtrip():
    """Setting .input.original_bundle and reading it back works."""
    w = buda.BundleWrapper()
    b = buda.HBundle()
    b.id = 42
    b.net_names = ["a", "b", "c"]
    w.input.original_bundle = b
    assert w.input.original_bundle.id == 42
    assert w.input.original_bundle.net_names == ["a", "b", "c"]


def test_bundleinput_candidates_roundtrip():
    """Setting .input.candidates to a topology list and reading it back works."""
    w = buda.BundleWrapper()
    t = buda.Topology()
    t.type = "L_HV"
    w.input.candidates = [t]
    assert len(w.input.candidates) == 1
    assert w.input.candidates[0].type == "L_HV"


# ── BundleInput / BundlePlan sub-objects are independent Python class instances ──

def test_sub_objects_independent():
    """Mutating one BundleWrapper does not affect another."""
    w1 = buda.BundleWrapper()
    w2 = buda.BundleWrapper()
    w1.input.width = 16.0
    w1.plan.selected_topology_index = 3
    w1.hier.level = 1
    assert w2.input.width == 1.0
    assert w2.plan.selected_topology_index == -1
    assert w2.hier.level == 0


# ── CongestionPlanner + NUTS end-to-end via sub-structs ───────────────────────

def _make_two_block_floorplan():
    fp = buda.Floorplan()
    fp.add_block("A", 0,   0, 100, 100)
    fp.add_block("B", 200, 0, 300, 100)
    return fp


def _make_layer_stack():
    ls = buda.LayerStack()
    ls.add_layer(3, "M3", buda.LayerDir.HORIZONTAL, buda.LayerType.LOW)
    ls.add_layer(4, "M4", buda.LayerDir.VERTICAL,   buda.LayerType.TOP)
    ls.add_layer(5, "M5", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    return ls


def _make_simple_wrapper(bundle_id: int, width: float) -> buda.BundleWrapper:
    fp = _make_two_block_floorplan()
    gen = buda.TopologyGenerator(fp)
    gen.set_layer_ids(5, 4)   # h_layer=5, v_layer=4
    candidates = gen.generate_candidates("A", ["B"])

    bundle = buda.HBundle()
    bundle.id = bundle_id
    bundle.net_names = ["net0"]

    w = buda.BundleWrapper()
    w.input.original_bundle = bundle
    w.input.width = width
    w.input.candidates = candidates
    return w


def _apply_assignments(wrappers, assignments):
    """Mirror what buda_cli does: copy BundleAssignment fields back to BundleWrapper.plan."""
    by_id = {w.input.original_bundle.id: w for w in wrappers}
    for asn in assignments:
        w = by_id.get(asn.bundle_id)
        if w is None:
            continue
        w.plan.selected_topology_index = asn.topo_index
        w.plan.seg_layers = list(asn.seg_layers)
        w.plan.seg_perp = list(asn.seg_perp)


def test_congestion_planner_returns_assignment_with_topo_index():
    """optimize_topologies returns BundleAssignment objects with topo_index >= 0."""
    fp = _make_two_block_floorplan()
    ls = _make_layer_stack()
    planner = buda.CongestionPlanner(fp, ls)
    planner.build_congestion_map()

    w = _make_simple_wrapper(0, 4.0)
    assignments = planner.optimize_topologies([w], 1)

    assert len(assignments) == 1, f"Expected 1 assignment, got {len(assignments)}"
    asn = assignments[0]
    assert asn.topo_index >= 0, (
        f"BundleAssignment.topo_index should be >= 0, got {asn.topo_index}"
    )
    assert asn.bundle_id == 0


def test_congestion_planner_assignment_seg_layers_match_topo():
    """BundleAssignment.seg_layers has one entry per segment in the selected topology."""
    fp = _make_two_block_floorplan()
    ls = _make_layer_stack()
    planner = buda.CongestionPlanner(fp, ls)
    planner.build_congestion_map()

    w = _make_simple_wrapper(0, 4.0)
    assignments = planner.optimize_topologies([w], 1)

    asn = assignments[0]
    topo = w.input.candidates[asn.topo_index]
    assert len(asn.seg_layers) == len(topo.segments), (
        f"seg_layers length {len(asn.seg_layers)} != segments length {len(topo.segments)}"
    )


def test_congestion_planner_does_not_touch_input_fields():
    """CongestionPlanner must not modify .input.width or .input.candidates."""
    fp = _make_two_block_floorplan()
    ls = _make_layer_stack()
    planner = buda.CongestionPlanner(fp, ls)
    planner.build_congestion_map()

    w = _make_simple_wrapper(0, 8.0)
    orig_width = w.input.width
    orig_n_candidates = len(w.input.candidates)

    planner.optimize_topologies([w], 1)

    assert w.input.width == orig_width, "CongestionPlanner mutated .input.width"
    assert len(w.input.candidates) == orig_n_candidates, (
        "CongestionPlanner mutated .input.candidates"
    )


# ── NUTSEngine reads .plan fields ─────────────────────────────────────────────

def test_nuts_reads_plan_fields_for_track_placement():
    """NUTSEngine.run() reads .plan.selected_topology_index, .plan.seg_layers,
    and .input.width to produce TrackSegments.

    The caller applies BundleAssignment back to .plan (same pattern as buda_cli).
    """
    fp = _make_two_block_floorplan()
    ls = _make_layer_stack()
    planner = buda.CongestionPlanner(fp, ls)
    planner.build_congestion_map()

    w = _make_simple_wrapper(0, 4.0)
    assignments = planner.optimize_topologies([w], 1)
    _apply_assignments([w], assignments)   # caller writes back to .plan

    engine = buda.NUTSEngine(fp, ls)
    result = engine.run([w])

    assert result.num_violations == 0, f"NUTS violations: {result.num_violations}"
    assert len(result.segments) > 0, "NUTS produced no track segments"
    for ts in result.segments:
        assert ts.bundle_id == 0
        assert ts.width == pytest.approx(4.0)


def test_nuts_result_bundle_id_matches_input_bundle_id():
    """TrackSegment.bundle_id from NUTS matches the HBundle.id stored in .input."""
    fp = _make_two_block_floorplan()
    ls = _make_layer_stack()
    planner = buda.CongestionPlanner(fp, ls)
    planner.build_congestion_map()

    w = _make_simple_wrapper(99, 2.0)
    assignments = planner.optimize_topologies([w], 1)
    _apply_assignments([w], assignments)

    engine = buda.NUTSEngine(fp, ls)
    result = engine.run([w])

    for ts in result.segments:
        assert ts.bundle_id == 99, (
            f"Expected bundle_id=99, got {ts.bundle_id}"
        )
