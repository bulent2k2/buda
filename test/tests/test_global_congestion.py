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
import buda


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_v_segment(x, y_lo, y_hi, layer):
    """Return a Segment with a vertical span (start.x == end.x)."""
    seg = buda.Segment()
    seg.start = buda.Point(x, y_lo)
    seg.end   = buda.Point(x, y_hi)
    seg.layer_hint = layer
    return seg


def make_bundle_wrapper(bid, width, seg):
    """BundleWrapper with a single-segment topology."""
    topo = buda.Topology()
    topo.type = "TEST_V"
    topo.segments = [seg]

    bundle = buda.HBundle()
    bundle.id = bid

    w = buda.BundleWrapper()
    w.input.original_bundle = bundle
    w.input.width = width
    w.input.candidates = [topo]
    w.plan.selected_topology_index = 0
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
    fp = buda.Floorplan()
    fp.add_block("src_blk", 0,   0, 100, 500)
    fp.add_block("dst_blk", 400, 0, 500, 500)

    ls = buda.LayerStack()
    ls.add_layer(3, "M3", buda.LayerDir.VERTICAL,   buda.LayerType.TOP)
    ls.add_layer(4, "M4", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    ls.add_layer(5, "M5", buda.LayerDir.VERTICAL,   buda.LayerType.TOP)
    ls.add_layer(7, "M7", buda.LayerDir.VERTICAL,   buda.LayerType.TOP)

    router = buda.CongestionPlanner(fp, ls)
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
    # M7 is the last TOP layer added, so it has zero affinity cost and is chosen
    # when all layers are uncongested.
    assert asn.v_layer_id == 7, (
        f"With M3/M5/M7 all uncongested, M7 (last TOP, zero affinity) should be chosen; got M{asn.v_layer_id}"
    )
    # The per-segment assignment must also be set.
    assert hasattr(asn, 'seg_layers'), "BundleAssignment must have seg_layers"
    assert len(asn.seg_layers) == 1
    assert asn.seg_layers[0] == 7


# ---------------------------------------------------------------------------
# Scenario: spill from M3 to M5 when M3 is full
# ---------------------------------------------------------------------------

def test_v_layer_spill_m3_to_m5():
    """
    Scenario: Multi-layer V routing — spill from M5 to M3 under congestion

    A narrow bottleneck channel limits the M5 H-cut capacity to < 2 × bundle
    width.  The first bundle fills M5; the second must spill to M3.

    Block footprints don't reduce capacity on TOP layers, and the slide-aware
    band lookup lets a bundle slide sideways into any band with room — so the
    walls alone no longer force a spill (see test_v_layer_slide_within_m5).
    M5 keepouts over both walls zero those bands, leaving the 4-wide gap as
    the only M5 capacity.
    """
    # Blocks span x=[0,8] and x=[12,20] at y=[0,100].
    # The H-cut at y_mid=50 passes through both blocks.
    # M5 keepouts over the walls → usable M5 x range = gap [8,12] = width 4.
    # Bundle width = 3  →  first bundle uses 3/4 of M5 gap capacity.
    # Second bundle needs 3 more but only 1 left → overflow on M5, spill to M3.
    fp = buda.Floorplan()
    fp.add_block("wall_left",  0,  0,  8, 100)
    fp.add_block("wall_right", 12, 0, 20, 100)
    fp.add_keepout_zone(0,  0,  8, 100, [5])
    fp.add_keepout_zone(12, 0, 20, 100, [5])

    ls = buda.LayerStack()
    ls.add_layer(3, "M3", buda.LayerDir.VERTICAL,   buda.LayerType.TOP)
    ls.add_layer(4, "M4", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    ls.add_layer(5, "M5", buda.LayerDir.VERTICAL,   buda.LayerType.TOP)

    router = buda.CongestionPlanner(fp, ls)
    router.build_congestion_map()

    # Confirm the bottleneck: M5 has only the 4-wide gap band open.
    # Hanan X grid: [0,8,12,20]; V-segments at x=10 land in band [8,12] (idx 1).
    m5_cuts = [c for c in router.get_cuts()
               if c.dir == buda.LayerDir.HORIZONTAL and c.layer_id == 5]
    assert m5_cuts, "Should have at least one H-cut for M5"
    for c in m5_cuts:
        assert list(c.band_cap) == pytest.approx([0.0, 4.0, 0.0]), (
            f"M5 keepouts should leave only the gap band open, got {list(c.band_cap)}"
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

    # The planner prefers M5 (TOP layer) first, then spills to M3.
    # Both layers must be used — the key behaviour is that spill occurs.
    assigned_layers = {a.v_layer_id for a in assignments}
    assert 3 in assigned_layers, "At least one bundle should be on M3"
    assert 5 in assigned_layers, (
        "One bundle should spill to M3 when M5 is saturated; "
        f"got layers {assigned_layers}"
    )

    # With M5 as TOP (zero affinity), Bundle 1 claims M5; Bundle 2 spills to M3.
    assert by_id[1].v_layer_id == 5, f"Bundle 1 should claim M5 (TOP), got M{by_id[1].v_layer_id}"
    assert by_id[2].v_layer_id == 3, f"Bundle 2 should spill to M3, got M{by_id[2].v_layer_id}"


# ---------------------------------------------------------------------------
# Scenario: slide-aware band lookup avoids a layer spill
# ---------------------------------------------------------------------------

def test_v_layer_slide_within_m5():
    """
    Scenario: slide-aware band lookup — slide sideways instead of spilling

    Same walls as test_v_layer_spill_m3_to_m5 but WITHOUT the M5 keepouts:
    block footprints don't reduce TOP-layer capacity, so the over-wall bands
    [0,8] and [12,20] have cap 8 each.  The gap band [8,12] (cap 4) fits only
    one width-3 bundle; the second bundle's slide-aware lookup must find the
    free over-wall band on M5 rather than spill to M3.
    """
    fp = buda.Floorplan()
    fp.add_block("wall_left",  0,  0,  8, 100)
    fp.add_block("wall_right", 12, 0, 20, 100)

    ls = buda.LayerStack()
    ls.add_layer(3, "M3", buda.LayerDir.VERTICAL,   buda.LayerType.TOP)
    ls.add_layer(4, "M4", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    ls.add_layer(5, "M5", buda.LayerDir.VERTICAL,   buda.LayerType.TOP)

    router = buda.CongestionPlanner(fp, ls)
    router.build_congestion_map()

    w1 = make_bundle_wrapper(bid=1, width=3.0, seg=make_v_segment(x=10, y_lo=0, y_hi=100, layer=5))
    w2 = make_bundle_wrapper(bid=2, width=3.0, seg=make_v_segment(x=10, y_lo=0, y_hi=100, layer=5))

    assignments = router.optimize_topologies([w1, w2], 1)

    # Both bundles fit on M5 — no spill.
    assert {a.v_layer_id for a in assignments} == {5}, (
        f"Both bundles should stay on M5 via sideways slide; got "
        f"{[(a.bundle_id, a.v_layer_id) for a in assignments]}"
    )

    # The M5 cut must show demand in two different bands: the gap (idx 1,
    # nominal x=10) and exactly one over-wall band — proof the second bundle
    # was charged to a slid position, not stacked into the full gap.
    m5_cuts = [c for c in router.get_cuts()
               if c.dir == buda.LayerDir.HORIZONTAL and c.layer_id == 5]
    assert m5_cuts
    for c in m5_cuts:
        usage = list(c.band_usage)
        assert usage[1] == pytest.approx(3.0), f"Gap band should hold one bundle, got {usage}"
        wall_usage = sorted((usage[0], usage[2]))
        assert wall_usage == pytest.approx([0.0, 3.0]), (
            f"Second bundle should slide to exactly one over-wall band, got {usage}"
        )


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
    fp = buda.Floorplan()
    fp.add_block("blk_a", 0, 0, 100, 200)
    fp.add_block("blk_b", 200, 0, 300, 200)

    ls = buda.LayerStack()
    ls.add_layer(4, "M4", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    ls.add_layer(5, "M5", buda.LayerDir.VERTICAL,   buda.LayerType.TOP)
    ls.set_layer_overhead(5, 25.0)   # M5 V: 25% overhead

    router = buda.CongestionPlanner(fp, ls)
    router.build_congestion_map()

    seg = make_v_segment(x=150, y_lo=0, y_hi=200, layer=5)
    w   = make_bundle_wrapper(bid=1, width=10.0, seg=seg)

    router.optimize_topologies([w], 1)

    # H-cuts for M5 that the segment crosses should show diluted per-band usage.
    # optimize_topologies extends the Hanan grid with segment endpoint x=150,
    # so the exact band index is not fixed — find it dynamically.
    h_cuts_m5 = [c for c in router.get_cuts()
                 if c.dir == buda.LayerDir.HORIZONTAL and c.layer_id == 5]
    crossed = [c for c in h_cuts_m5 if any(u > 0 for u in c.band_usage)]
    assert crossed, "At least one M5 H-cut should have non-zero band usage"

    expected_eff_width = 10.0 * (100.0 / (100.0 - 25.0))   # 13.333...
    for c in crossed:
        used = [u for u in c.band_usage if u > 0]
        assert len(used) == 1, f"Expected exactly one used band, got {c.band_usage}"
        assert used[0] == pytest.approx(expected_eff_width, rel=1e-3), (
            f"Expected diluted usage {expected_eff_width:.3f}, got {used[0]:.3f}"
        )


# ---------------------------------------------------------------------------
# Scenario: pinned bundle whose only candidate is infeasible must not crash
# ---------------------------------------------------------------------------

def test_pinned_infeasible_candidate_gets_best_effort_assignment():
    """
    Regression (flow/channel_stress.buda segfault): when every scored
    candidate is infeasible (slide window < effective bus width) — which is
    always "all" for a pinned bundle — the planner committed an EMPTY
    best_seg_layers and indexed it out of bounds.  A second scoring pass with
    the feasibility gate off must produce a best-effort assignment instead.
    """
    fp = buda.Floorplan()
    # Tiny 4-unit faces: the H segment's slide window is [0,4].
    fp.add_block("src", 0,   0, 100, 4)
    fp.add_block("dst", 400, 0, 500, 4)

    ls = buda.LayerStack()
    ls.add_layer(4, "M4", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    ls.add_layer(5, "M5", buda.LayerDir.VERTICAL,   buda.LayerType.TOP)

    router = buda.CongestionPlanner(fp, ls)
    router.build_congestion_map()

    seg = buda.Segment()
    seg.start = buda.Point(100, 2)
    seg.end   = buda.Point(400, 2)
    seg.layer_hint = 4
    topo = buda.Topology()
    topo.type = "TEST_H"
    topo.segments = [seg]
    bundle = buda.HBundle()
    bundle.id = 1
    w = buda.BundleWrapper()
    w.input.original_bundle = bundle
    w.input.width = 50.0                      # 50 >> 4-unit slide window: infeasible
    w.input.candidates = [topo]
    w.plan.selected_topology_index = 0
    w.input.topology_pinned = True            # exactly one candidate is scored

    assignments = router.optimize_topologies([w], 1)

    assert len(assignments) == 1
    asn = assignments[0]
    assert asn.topo_index == 0
    assert len(asn.seg_layers) == 1, (
        "best-effort pass must assign a layer per segment, not crash on an "
        "empty assignment"
    )
    assert asn.seg_layers[0] == 4
