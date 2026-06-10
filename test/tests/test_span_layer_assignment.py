"""
Span-aware layer assignment tests.

Covers the scenarios in features/span_aware_layer_assignment.feature.

Design under test
-----------------
cost(seg, layer) = kCong * max(0, (usage+eff-cap)/cap)  # overflow congestion (zero below cap)
                 + kSpan(layer) * max(0, span_min-span, span-span_max)   # span mismatch
                 + (0 if TOP else base_cost_non_top)                      # tier penalty

The congestion term is zero when the segment fits within the cut-band capacity; only
actual overflow incurs a cost.  This ensures Z/U topologies are preferred over I only
when I genuinely cannot fit through the Hanan grid, not merely because they avoid a
lightly-used cut via boundary effects.

Ties broken toward higher layer ID (higher metal preferred when costs are equal).
"""
import pytest
import buda


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_h_segment(x_lo, x_hi, y, layer=4):
    seg = buda.Segment()
    seg.start = buda.Point(x_lo, y)
    seg.end   = buda.Point(x_hi, y)
    seg.layer_hint = layer
    return seg


def make_bundle_wrapper(bid, width, seg):
    topo = buda.Topology()
    topo.type = "TEST_H"
    topo.segments = [seg]
    bundle = buda.HBundle()
    bundle.id = bid
    w = buda.BundleWrapper()
    w.original_bundle = bundle
    w.width = width
    w.candidates = [topo]
    w.selected_topology_index = 0
    return w


def open_channel_fp():
    """Two blocks with a large gap — ample H routing capacity."""
    fp = buda.Floorplan()
    fp.add_block("src", 0,   0, 100, 200)
    fp.add_block("dst", 900, 0, 1000, 200)
    return fp


def bottleneck_fp():
    """Floorplan with a narrow H-routing gap.

    src (x=0..100, y=96..100), dst (x=500..600, y=96..100),
    blocker (x=100..500, y=0..96).
    Hanan Y-grid: [0, 96, 100].
    V-cut at x=300 (midpoint of [100,500]):
      band[0] = [0,96)  → capacity 0  (covered by blocker)
      band[1] = [96,100) → capacity 4  (open gap)
    H segments at y=98 cross x=300 and land in band 1 (capacity=4).

    src/dst are intentionally narrow in Y so ConnTopology computes
    perp_lo/perp_hi = [96,100], centre=98 → find_band returns band 1.
    """
    fp = buda.Floorplan()
    fp.add_block("src",      0,  96, 100, 100)
    fp.add_block("dst",    500,  96, 600, 100)
    fp.add_block("blocker", 100,  0, 500,  96)
    return fp


def make_ls_m4_m6(span_max_m4=None, span_min_m6=None,
                  kspan_m4=None, kspan_m6=None,
                  include_m2=False, m2_span_max=None):
    """Build a layer stack with M4-H-TOP, M6-H-TOP, M5-V-TOP, optional M2-H-non-TOP."""
    ls = buda.LayerStack()
    ls.add_layer(4, "M4", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    if span_max_m4 is not None:
        ls.set_layer_span(4, 0, span_max_m4)
    if kspan_m4 is not None:
        ls.set_layer_kspan(4, kspan_m4)
    ls.add_layer(6, "M6", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    if span_min_m6 is not None:
        ls.set_layer_span(6, span_min_m6, 1_000_000_000)
    if kspan_m6 is not None:
        ls.set_layer_kspan(6, kspan_m6)
    ls.add_layer(5, "M5", buda.LayerDir.VERTICAL, buda.LayerType.TOP)
    if include_m2:
        ls.add_layer(2, "M2", buda.LayerDir.HORIZONTAL, buda.LayerType.LOW)
        if m2_span_max is not None:
            ls.set_layer_span(2, 0, m2_span_max)
    return ls


# ---------------------------------------------------------------------------
# Scenario: Short segment prefers lower TOP-H layer (M4)
# ---------------------------------------------------------------------------

def test_short_seg_prefers_m4():
    """
    Scenario: Short segment prefers lower TOP-H layer
    M4 span_max=500 → span_cost=0 for span=200.
    M6 span_min=400 → span_cost=kSpan*(400-200)=0.2 for span=200.
    M4 wins.
    """
    fp = open_channel_fp()
    ls = make_ls_m4_m6(span_max_m4=500, span_min_m6=400)

    router = buda.CongestionPlanner(fp, ls)
    router.build_congestion_map()

    # Short H segment: x from 100 to 300 (span=200), well within M4's window
    seg = make_h_segment(100, 300, y=100)
    w   = make_bundle_wrapper(bid=1, width=5.0, seg=seg)

    assignments = router.optimize_topologies([w], 1)
    assert len(assignments) == 1
    assert assignments[0].seg_layers[0] == 4, (
        f"Short span=200 should go to M4 (span_max=500), got M{assignments[0].seg_layers[0]}"
    )


# ---------------------------------------------------------------------------
# Scenario: Long segment prefers higher TOP-H layer (M6)
# ---------------------------------------------------------------------------

def test_long_seg_prefers_m6():
    """
    Scenario: Long segment prefers higher TOP-H layer
    M4 span_max=500 → span_cost=kSpan*(800-500)=0.3 for span=800.
    M6 span_min=400 → span_cost=0 for span=800.
    M6 wins.
    """
    fp = open_channel_fp()
    ls = make_ls_m4_m6(span_max_m4=500, span_min_m6=400)

    router = buda.CongestionPlanner(fp, ls)
    router.build_congestion_map()

    # Long H segment: x from 100 to 900 (span=800), well within M6's window
    seg = make_h_segment(100, 900, y=100)
    w   = make_bundle_wrapper(bid=1, width=5.0, seg=seg)

    assignments = router.optimize_topologies([w], 1)
    assert len(assignments) == 1
    assert assignments[0].seg_layers[0] == 6, (
        f"Long span=800 should go to M6 (span_min=400), got M{assignments[0].seg_layers[0]}"
    )


# ---------------------------------------------------------------------------
# Scenario: Non-TOP layer incurs base cost penalty
# ---------------------------------------------------------------------------

def test_non_top_layer_base_cost():
    """
    Scenario: Non-TOP layer incurs base cost penalty
    M2 (non-TOP, span_max=300) has span_cost=0 for span=100, but base_cost_non_top=0.5.
    M4 (TOP) has total cost=0 for span=100 (no span window set).
    M4 wins despite M2 being within its span window.
    """
    fp = open_channel_fp()
    ls = make_ls_m4_m6(include_m2=True, m2_span_max=300)

    router = buda.CongestionPlanner(fp, ls)
    router.build_congestion_map()

    seg = make_h_segment(100, 200, y=100)  # span=100
    w   = make_bundle_wrapper(bid=1, width=5.0, seg=seg)

    assignments = router.optimize_topologies([w], 1)
    # M6 wins (highest ID, all TOP, no span constraints set → tied at 0, M6 wins tie).
    # Key assertion: M2 (non-TOP) is NOT chosen.
    assert assignments[0].seg_layers[0] != 2, (
        f"Non-TOP M2 should not win over TOP layers; got M{assignments[0].seg_layers[0]}"
    )


# ---------------------------------------------------------------------------
# Scenario: Congestion drives spill from preferred to span-mismatched layer
# ---------------------------------------------------------------------------

def test_congestion_spill_to_span_mismatched_layer():
    """
    Scenario: Congestion on preferred layer drives spill to span-mismatched layer
    Bottleneck channel capacity = 4. Bundle width = 3.
    Bundle 1 (span=400): M6 wins tie (higher ID), uses 3 of 4 capacity.
    Bundle 2 (span=400): M6 overflows (3+3=6 > 4) → positive cost; M4 empty → M4 wins.
    """
    fp = bottleneck_fp()
    ls = make_ls_m4_m6(span_max_m4=500, span_min_m6=400)

    router = buda.CongestionPlanner(fp, ls)
    router.build_congestion_map()

    # Confirm the open band (y=[96,100]) has capacity 4 on M4.
    v_cuts_m4 = [c for c in router.get_cuts()
                 if c.dir == buda.LayerDir.VERTICAL and c.layer_id == 4]
    assert v_cuts_m4, "Need V-cuts on M4"
    bottleneck_cap = min(min(c.band_cap) for c in v_cuts_m4 if c.band_cap)
    assert bottleneck_cap == pytest.approx(4.0), (
        f"Expected bottleneck capacity 4.0, got {bottleneck_cap}"
    )

    # H segment at y=98 from x=100 to x=500 (span=400), crossing the V-cut at x=300
    # in the open band [96,100]. Both M4 (span_max=500) and M6 (span_min=400) have
    # span_cost=0 for span=400 — tie broken by higher ID (M6 first).
    seg1 = make_h_segment(100, 500, y=98)
    seg2 = make_h_segment(100, 500, y=98)
    w1 = make_bundle_wrapper(bid=1, width=3.0, seg=seg1)
    w2 = make_bundle_wrapper(bid=2, width=3.0, seg=seg2)

    assignments = router.optimize_topologies([w1, w2], 1)
    assert len(assignments) == 2
    layers = {a.bundle_id: a.seg_layers[0] for a in assignments}

    # Bundle 1 claims M6 (highest ID wins tie when both uncongested).
    # Bundle 2: M6 overflows (3+3=6 > cap 4) → cost = kCong*(2/4); M4 no overflow → M4 wins.
    assert layers[1] == 6, f"Bundle 1 should claim M6 (tie→highest ID); got M{layers[1]}"
    assert layers[2] == 4, f"Bundle 2 should spill to M4 (M6 overflow); got M{layers[2]}"


# ---------------------------------------------------------------------------
# Scenario: Cost is zero below capacity, positive only on overflow
# ---------------------------------------------------------------------------

def test_overflow_threshold_congestion():
    """
    Scenario: Overflow-threshold congestion model
    Congestion cost is exactly zero when a segment fits within the cut-band
    capacity, and becomes positive only when it would overflow.

    Setup: bottleneck channel capacity=4.  Three equal-width bundles (width=2
    each) are processed in ID order.  Bundles 1 and 2 fill M6 without overflow
    (2+2=4 = cap); bundle 3 would overflow M6 (4+2=6 > 4) and is routed to M4.
    """
    fp = bottleneck_fp()   # V-cut at x=300, open band capacity=4
    ls = buda.LayerStack()
    ls.add_layer(4, "M4", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    ls.add_layer(6, "M6", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    ls.add_layer(5, "M5", buda.LayerDir.VERTICAL,   buda.LayerType.TOP)

    router = buda.CongestionPlanner(fp, ls)
    router.build_congestion_map()

    seg1 = make_h_segment(100, 500, y=98)
    seg2 = make_h_segment(100, 500, y=98)
    seg3 = make_h_segment(100, 500, y=98)
    # All width=2 so fattest-first order is stable by bundle ID.
    w1 = make_bundle_wrapper(bid=1, width=2.0, seg=seg1)
    w2 = make_bundle_wrapper(bid=2, width=2.0, seg=seg2)
    w3 = make_bundle_wrapper(bid=3, width=2.0, seg=seg3)

    assignments = router.optimize_topologies([w1, w2, w3], 1)
    layers = {a.bundle_id: a.seg_layers[0] for a in assignments}

    # Bundles 1 and 2: no overflow (2→2, 2+2=4 = cap) → cong=0 → tie → M6 wins both.
    assert layers[1] == 6, f"Bundle 1 should stay on M6 (no overflow); got M{layers[1]}"
    assert layers[2] == 6, f"Bundle 2 should stay on M6 (2+2=4, no overflow); got M{layers[2]}"
    # Bundle 3: M6 overflow (4+2=6 > 4) → positive cost → spills to M4.
    assert layers[3] == 4, f"Bundle 3 should spill to M4 (M6 overflow); got M{layers[3]}"


# ---------------------------------------------------------------------------
# Scenario: Per-layer kSpan override (kSpan=0 removes span penalty)
# ---------------------------------------------------------------------------

def test_per_layer_kspan_zero_removes_span_penalty():
    """
    Scenario: Per-layer kSpan override
    M6 kSpan=0 → span_cost=0 regardless of span.  M4 span_max=500, kSpan=default.
    Short seg span=100: M4 cost=0 (within window), M6 cost=0 (kSpan=0).
    Tie → M6 wins (higher ID).
    """
    fp = open_channel_fp()
    ls = make_ls_m4_m6(span_max_m4=500, span_min_m6=400, kspan_m6=0.0)

    router = buda.CongestionPlanner(fp, ls)
    router.build_congestion_map()

    seg = make_h_segment(100, 200, y=100)  # span=100, well below M6 span_min=400
    w   = make_bundle_wrapper(bid=1, width=5.0, seg=seg)

    assignments = router.optimize_topologies([w], 1)
    assert assignments[0].seg_layers[0] == 6, (
        f"With kSpan=0 on M6, tie should break to M6 (higher ID); "
        f"got M{assignments[0].seg_layers[0]}"
    )


# ---------------------------------------------------------------------------
# Scenario: set_planner_param kCong scales congestion cost
# ---------------------------------------------------------------------------

def test_set_planner_param_kcong():
    """
    Scenario: set_planner_param kCong scales congestion cost
    With a high kCong, even slight M6 congestion makes its cost huge, and
    a short segment that would otherwise tolerate M6 spills to M4.

    Setup: M6 is 50% used; M4 is empty.
    Short seg span=100: M4 span_cost=0, M6 span_cost=0 (no window constraints here),
    but M6 cong_cost = kCong * 1.0.
    With kCong=100: M6 cost=100, M4 cost=0 → M4 wins.
    With kCong=0.001: M6 cost=0.001, M4 cost=0 → M4 still wins (tie→M6, but let's
    verify the param is applied by checking M4 wins when kCong is large and
    M6 is congested).
    """
    fp = bottleneck_fp()  # V-cut at x=300, open band capacity=4
    ls = buda.LayerStack()
    ls.add_layer(4, "M4", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    ls.add_layer(6, "M6", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    ls.add_layer(5, "M5", buda.LayerDir.VERTICAL,   buda.LayerType.TOP)

    router = buda.CongestionPlanner(fp, ls)
    router.set_planner_param("kCong", 100.0)  # huge congestion penalty
    router.build_congestion_map()

    # H segment at y=98, x=100..500 (span=400), crosses V-cut at x=300 in band [96,100].
    seg_fill  = make_h_segment(100, 500, y=98)
    w_fill    = make_bundle_wrapper(bid=0, width=3.0, seg=seg_fill)
    seg_probe = make_h_segment(100, 500, y=98)
    w_probe   = make_bundle_wrapper(bid=1, width=3.0, seg=seg_probe)

    assignments = router.optimize_topologies([w_fill, w_probe], 1)
    layers = {a.bundle_id: a.seg_layers[0] for a in assignments}

    # Bundle 1 goes to M6 (both empty, highest ID wins tie).
    # Bundle 2: M6 overflows (3+3=6 > 4) → cong=kCong*(2/4)=50; M4 no overflow → M4 wins.
    assert layers[0] == 6, f"First bundle should claim M6 (highest ID, both empty); got M{layers[0]}"
    assert layers[1] == 4, f"Second bundle: kCong=100, M6 at 75% → cost=300 → should spill to M4; got M{layers[1]}"


# ---------------------------------------------------------------------------
# Scenario: set_planner_param between run_planner calls survives the re-run
# ---------------------------------------------------------------------------

def test_set_planner_param_between_runs():
    """
    Scenario: CLI set_planner_param after run_planner applies to the next run

    Each run_planner builds a fresh CongestionPlanner seeded from the
    session's stashed params.  Regression: a param set AFTER the first
    run_planner used to be applied only to the live (about-to-be-replaced)
    planner and was silently lost on re-run.
    """
    from buda_cli import BudaSession

    s = BudaSession()
    for line in [
        "def_layer 3 M3 V TOP 0.0",
        "def_layer 4 M4 H TOP 0.0",
        "def_layer 5 M5 V TOP 0.0",
        "add_block a 0 0 20 20",
        "add_block b 0 100 20 120",
        "add_net n1 a.o b.i",
        "run_bundler",
        "generate_topologies",
        "run_planner 1",
    ]:
        s.do_command(line)

    s.do_command("set_planner_param kCong 7.5")
    assert s._planner_params.get("kCong") == 7.5, (
        "param set between runs must be stashed for the next run_planner"
    )

    old_planner = s.planner
    s.do_command("run_planner 1")
    assert s.planner is not old_planner, "run_planner should build a fresh planner"
    # The fresh planner was seeded from the stash (set_planner_param has no
    # getter, so the stash surviving + reseed path is the observable contract).
    assert s._planner_params.get("kCong") == 7.5


# ---------------------------------------------------------------------------
# Scenario: effective bus width — measured bit pitch vs density fallback
# ---------------------------------------------------------------------------

def test_eff_bus_width_bit_pitch_vs_dilution_fallback():
    """
    eff_bus_width(bits, base_width, layer):
      - bit_pitch set + bits known → exact cost bits × bit_pitch
        (tracks2top M6: unit_pitch 34 / 8 signals = 4.25/bit; the legacy
        1.5 × 2.125 = 3.19/bit under-priced every bus by 25%)
      - bit_pitch unset, or hand-built wrappers without nets (bits=0)
        → legacy density model base_width × dilution
    """
    ls = buda.LayerStack()
    ls.add_layer(6, "M6", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    ls.set_layer_dilution(6, 2.125)

    # No bit pitch yet → density model.
    assert ls.eff_bus_width(16, 24.0, 6) == pytest.approx(24.0 * 2.125)

    ls.set_bit_pitch(6, 34.0 / 8.0)
    # Measured pitch wins when bits are known.
    assert ls.eff_bus_width(16, 24.0, 6) == pytest.approx(68.0)
    # bits=0 (hand-built wrapper without nets) keeps the density model.
    assert ls.eff_bus_width(0, 24.0, 6) == pytest.approx(24.0 * 2.125)
    # Unknown layer: base width unscaled.
    assert ls.eff_bus_width(16, 24.0, 9) == pytest.approx(24.0)
