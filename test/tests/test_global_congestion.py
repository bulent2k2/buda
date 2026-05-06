"""
Global congestion & topology selection tests.

Covers the Gherkin scenarios in features/global_congestion.feature,
specifically the new multi-layer V routing scenarios added alongside
the M3/M5/M7 support.

Note: pytest_bdd steps for the legacy scenarios are omitted (those
scenarios describe intended long-term behaviour not yet fully wired
to step defs).  The two new scenarios are exercised as plain pytest
functions below.
"""
import pytest
import interconnect


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_v_segment(x, y_lo, y_hi, layer):
    """Return a Segment with a vertical span (start.x == end.x)."""
    seg = interconnect.Segment()
    seg.start = interconnect.Point(x, y_lo)
    seg.end   = interconnect.Point(x, y_hi)
    seg.layer_hint = layer
    return seg


def make_bundle_wrapper(bid, width, seg):
    """BundleWrapper with a single-segment topology."""
    topo = interconnect.Topology()
    topo.type = "TEST_V"
    topo.segments = [seg]

    bundle = interconnect.Bundle()
    bundle.id = bid

    w = interconnect.BundleWrapper()
    w.original_bundle = bundle
    w.width = width
    w.candidates = [topo]
    w.selected_topology_index = 0
    return w


# ---------------------------------------------------------------------------
# Scenario: optimize_topologies returns BundleAssignment list
# ---------------------------------------------------------------------------

def test_optimise_returns_bundle_assignments():
    """
    Scenario: Multi-layer V routing — optimize_topologies returns BundleAssignment list
    With M3(V)/M4(H)/M5(V)/M7(V) and ample channel capacity, a single bundle
    should be assigned to M3 (lowest-ID V layer, first preference).
    The return value must be a list of BundleAssignment objects.
    """
    # Wide open channel: two tall blocks with a large gap between them.
    fp = interconnect.Floorplan()
    fp.add_block("src_blk", 0,   0, 100, 500)
    fp.add_block("dst_blk", 400, 0, 500, 500)

    ls = interconnect.LayerStack()
    ls.add_layer(3, "M3", interconnect.LayerDir.VERTICAL,   interconnect.LayerType.TOP)
    ls.add_layer(4, "M4", interconnect.LayerDir.HORIZONTAL, interconnect.LayerType.TOP)
    ls.add_layer(5, "M5", interconnect.LayerDir.VERTICAL,   interconnect.LayerType.TOP)
    ls.add_layer(7, "M7", interconnect.LayerDir.VERTICAL,   interconnect.LayerType.TOP)

    router = interconnect.GlobalRouter(fp, ls)
    router.build_congestion_map()

    # V segment crossing the central horizontal cut (y=250 is inside both blocks → cut is unblocked there)
    seg = make_v_segment(x=250, y_lo=0, y_hi=500, layer=5)
    w   = make_bundle_wrapper(bid=1, width=10.0, seg=seg)

    assignments = router.optimize_topologies([w], 1)

    # Return type must be a list of BundleAssignment
    assert isinstance(assignments, list), "optimize_topologies must return a list"
    assert len(assignments) == 1

    asn = assignments[0]
    assert hasattr(asn, 'bundle_id'),  "BundleAssignment must have bundle_id"
    assert hasattr(asn, 'topo_index'), "BundleAssignment must have topo_index"
    assert hasattr(asn, 'v_layer_id'), "BundleAssignment must have v_layer_id"

    assert asn.bundle_id  == 1
    assert asn.topo_index == 0
    assert asn.v_layer_id == 3, (
        f"With M3/M5/M7 all uncongested, M3 (lowest ID) should be chosen; got M{asn.v_layer_id}"
    )


# ---------------------------------------------------------------------------
# Scenario: spill from M3 to M5 when M3 is full
# ---------------------------------------------------------------------------

def test_v_layer_spill_m3_to_m5():
    """
    Scenario: Multi-layer V routing — spill from M3 to M5 under congestion

    A narrow bottleneck channel (two blocks that span almost the full X range,
    leaving only a tiny gap) limits the H-cut capacity to < 2 × bundle width.
    The first (heavier) bundle fills M3; the second must spill to M5.
    """
    # Blocks span x=[0,8] and x=[12,20] at y=[0,100].
    # The H-cut at y_mid=50 passes through both blocks.
    # Unblocked x range = gap at x=[8,12] = width 4.
    # Bundle width = 3  →  first bundle uses 3/4 of M3 capacity.
    # Second bundle needs 3 more but only 1 left → overflow on M3, spill to M5.
    fp = interconnect.Floorplan()
    fp.add_block("wall_left",  0,  0,  8, 100)
    fp.add_block("wall_right", 12, 0, 20, 100)

    ls = interconnect.LayerStack()
    ls.add_layer(3, "M3", interconnect.LayerDir.VERTICAL,   interconnect.LayerType.TOP)
    ls.add_layer(4, "M4", interconnect.LayerDir.HORIZONTAL, interconnect.LayerType.TOP)
    ls.add_layer(5, "M5", interconnect.LayerDir.VERTICAL,   interconnect.LayerType.TOP)

    router = interconnect.GlobalRouter(fp, ls)
    router.build_congestion_map()

    # Confirm the bottleneck cut has the expected small capacity.
    h_cuts = [c for c in router.get_cuts()
              if c.dir == interconnect.LayerDir.HORIZONTAL and c.layer_id == 3]
    assert h_cuts, "Should have at least one H-cut for M3"
    min_cap = min(c.capacity for c in h_cuts)
    assert min_cap == pytest.approx(4.0), (
        f"Expected bottleneck capacity 4.0, got {min_cap}"
    )

    # Two equal-width bundles; planner processes widest first (they're equal
    # so order is stable by index — bundle id=1 comes first).
    bundle_width = 3.0
    seg1 = make_v_segment(x=10, y_lo=0, y_hi=100, layer=5)
    seg2 = make_v_segment(x=10, y_lo=0, y_hi=100, layer=5)
    w1 = make_bundle_wrapper(bid=1, width=bundle_width, seg=seg1)
    w2 = make_bundle_wrapper(bid=2, width=bundle_width, seg=seg2)

    assignments = router.optimize_topologies([w1, w2], 1)

    assert len(assignments) == 2
    by_id = {a.bundle_id: a for a in assignments}

    # First bundle assigned should be on M3 (no overflow yet)
    # Second bundle should spill to M5 (M3 usage = 3, remaining = 1 < 3)
    assigned_layers = {a.v_layer_id for a in assignments}
    assert 3 in assigned_layers, "At least one bundle should be on M3"
    assert 5 in assigned_layers, (
        "Second bundle should spill to M5 when M3 is saturated; "
        f"got layers {assigned_layers}"
    )

    # Additionally verify that the first-processed bundle went to M3
    # (fattest-first = equal width, so bundle id=1 is processed first by stable sort)
    assert by_id[1].v_layer_id == 3, f"Bundle 1 should be on M3, got M{by_id[1].v_layer_id}"
    assert by_id[2].v_layer_id == 5, f"Bundle 2 should spill to M5, got M{by_id[2].v_layer_id}"


# ---------------------------------------------------------------------------
# Scenario: dilution factor is reflected in cut usage
# ---------------------------------------------------------------------------

def test_dilution_factor_increases_cut_usage():
    """
    Scenario: effective Width Calculation (Dilution)

    25% overhead on a V layer means the cut usage for a bundle of raw width
    10 should be 10 * (100/(100-25)) ≈ 13.33, not 10.
    We verify this indirectly: after optimizing, the H-cut current_usage
    should reflect the diluted width.
    """
    fp = interconnect.Floorplan()
    fp.add_block("blk_a", 0, 0, 100, 200)
    fp.add_block("blk_b", 200, 0, 300, 200)

    ls = interconnect.LayerStack()
    ls.add_layer(4, "M4", interconnect.LayerDir.HORIZONTAL, interconnect.LayerType.TOP)
    ls.add_layer(5, "M5", interconnect.LayerDir.VERTICAL,   interconnect.LayerType.TOP)

    router = interconnect.GlobalRouter(fp, ls)
    router.set_layer_overhead(5, 25.0)   # M5 V: 25% overhead
    router.build_congestion_map()

    seg = make_v_segment(x=150, y_lo=0, y_hi=200, layer=5)
    w   = make_bundle_wrapper(bid=1, width=10.0, seg=seg)

    router.optimize_topologies([w], 1)

    # H-cuts for M5 that the segment crosses should show diluted usage.
    h_cuts_m5 = [c for c in router.get_cuts()
                 if c.dir == interconnect.LayerDir.HORIZONTAL and c.layer_id == 5]
    crossed = [c for c in h_cuts_m5 if c.current_usage > 0]
    assert crossed, "At least one M5 H-cut should have usage > 0"

    expected_eff_width = 10.0 * (100.0 / (100.0 - 25.0))   # 13.333...
    for c in crossed:
        assert c.current_usage == pytest.approx(expected_eff_width, rel=1e-3), (
            f"Expected diluted usage {expected_eff_width:.3f}, got {c.current_usage:.3f}"
        )
