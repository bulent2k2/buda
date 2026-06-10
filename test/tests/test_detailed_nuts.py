"""
Detailed NUTS (Stage 9) — TDD tests.

Tests are written against the planned API from CLAUDE.md Stage 9.
All tests are expected to FAIL until the C++ implementation is complete.
That is intentional — this file is the red phase of TDD.

Expected Python API (to be implemented):
    buda.TrackSlot(type, label, width, space_after)
    buda.TrackPattern(origin, slots)
    buda.RoutingGridStack()
      .define_layer(layer_id, pattern)
    buda.BusSegment()
      .bundle_id, .seg_idx, .layer
      .span_lo, .span_hi
      .interval_lo, .interval_hi
      .bit_width             int  — number of signal tracks needed
      .bit_order             str  — "LO_HI" or "HI_LO"
      .timing_critical       bool
    buda.DetailedNUTSEngine(routing_grid_stack)
      .run(bus_segments) -> DetailedNUTSResult
    buda.DetailedNUTSResult
      .net_segments          list[NetSegment]
      .num_unplaced          int
    buda.NetSegment
      .bundle_id, .seg_idx, .bit_index
      .track_position        float
      .width                 float
"""
import pytest
import buda


# ---------------------------------------------------------------------------
# Helpers — shared with test_routing_grid; duplicated here for isolation
# ---------------------------------------------------------------------------

def make_slot(slot_type, label, width, space_after):
    return buda.TrackSlot(
        type=slot_type, label=label, width=width, space_after=space_after
    )


def make_standard_pattern(origin=0.0):
    """[POWER(w=2,sp=1), SIG(w=1,sp=1), SIG(w=1,sp=1),
        GROUND(w=2,sp=1), SIG(w=1,sp=1), SIG(w=1,sp=1)]
    unit_pitch=14
    Signal centres (origin=0): 3.5, 5.5, 10.5, 12.5
    """
    slots = [
        make_slot("POWER",  "VDD", 2.0, 1.0),
        make_slot("SIGNAL", "sig", 1.0, 1.0),
        make_slot("SIGNAL", "sig", 1.0, 1.0),
        make_slot("GROUND", "GND", 2.0, 1.0),
        make_slot("SIGNAL", "sig", 1.0, 1.0),
        make_slot("SIGNAL", "sig", 1.0, 1.0),
    ]
    return buda.TrackPattern(origin=origin, slots=slots)


def make_stack_with_standard_pattern(layer_id=4):
    stack = buda.RoutingGridStack()
    stack.define_layer(layer_id, make_standard_pattern(origin=0.0), True)
    return stack


def make_bus_segment(bundle_id=1, seg_idx=0, layer=4,
                     span_lo=0.0, span_hi=100.0,
                     interval_lo=0.0, interval_hi=14.0,
                     bit_width=1, bit_order="LO_HI",
                     timing_critical=False):
    seg = buda.BusSegment()
    seg.bundle_id      = bundle_id
    seg.seg_idx        = seg_idx
    seg.layer          = layer
    seg.span_lo        = span_lo
    seg.span_hi        = span_hi
    seg.interval_lo    = interval_lo
    seg.interval_hi    = interval_hi
    seg.bit_width      = bit_width
    seg.bit_order      = bit_order
    seg.timing_critical = timing_critical
    return seg


def net_segs_for(result, bundle_id):
    return sorted(
        [s for s in result.net_segments if s.bundle_id == bundle_id],
        key=lambda s: s.bit_index
    )


# ---------------------------------------------------------------------------
# LO_HI bit ordering
# ---------------------------------------------------------------------------

def test_lo_hi_two_bits_assigned_from_lowest_signal_tracks():
    stack = make_stack_with_standard_pattern()
    seg = make_bus_segment(bit_width=2, interval_lo=0.0, interval_hi=14.0, bit_order="LO_HI")
    engine = buda.DetailedNUTSEngine(stack)
    result = engine.run([seg])

    segs = net_segs_for(result, bundle_id=1)
    assert len(segs) == 2
    assert segs[0].track_position == pytest.approx(3.5)   # bit_index=0
    assert segs[1].track_position == pytest.approx(5.5)   # bit_index=1


def test_lo_hi_four_bits_fills_all_signal_tracks_in_unit():
    stack = make_stack_with_standard_pattern()
    seg = make_bus_segment(bit_width=4, interval_lo=0.0, interval_hi=14.0, bit_order="LO_HI")
    engine = buda.DetailedNUTSEngine(stack)
    result = engine.run([seg])

    assert result.num_unplaced == 0
    positions = [s.track_position for s in net_segs_for(result, 1)]
    assert positions == pytest.approx([3.5, 5.5, 10.5, 12.5])


# ---------------------------------------------------------------------------
# HI_LO bit ordering
# ---------------------------------------------------------------------------

def test_hi_lo_two_bits_assigned_from_highest_signal_tracks():
    stack = make_stack_with_standard_pattern()
    seg = make_bus_segment(bit_width=2, interval_lo=0.0, interval_hi=14.0, bit_order="HI_LO")
    engine = buda.DetailedNUTSEngine(stack)
    result = engine.run([seg])

    segs = net_segs_for(result, bundle_id=1)
    assert len(segs) == 2
    assert segs[0].track_position == pytest.approx(12.5)  # bit_index=0 (highest)
    assert segs[1].track_position == pytest.approx(10.5)  # bit_index=1


# ---------------------------------------------------------------------------
# Power/ground tracks are never used for signal bits
# ---------------------------------------------------------------------------

def test_power_and_ground_track_centres_never_assigned():
    stack = make_stack_with_standard_pattern()
    seg = make_bus_segment(bit_width=4, interval_lo=0.0, interval_hi=14.0, bit_order="LO_HI")
    engine = buda.DetailedNUTSEngine(stack)
    result = engine.run([seg])

    positions = {s.track_position for s in result.net_segments}
    assert 1.0 not in positions   # POWER centre
    assert 8.0 not in positions   # GROUND centre


# ---------------------------------------------------------------------------
# Narrow intervals restrict which signal tracks are available
# ---------------------------------------------------------------------------

def test_narrow_interval_returns_subset_of_signal_tracks():
    stack = make_stack_with_standard_pattern()
    # [4.0, 11.0] contains signal tracks at 5.5 and 10.5 only
    seg = make_bus_segment(bit_width=2, interval_lo=4.0, interval_hi=11.0, bit_order="LO_HI")
    engine = buda.DetailedNUTSEngine(stack)
    result = engine.run([seg])

    positions = [s.track_position for s in net_segs_for(result, 1)]
    assert positions == pytest.approx([5.5, 10.5])


# ---------------------------------------------------------------------------
# timing_critical — contiguous signal track windows
# ---------------------------------------------------------------------------

def test_timing_critical_selects_contiguous_pair_at_lo_end():
    stack = make_stack_with_standard_pattern()
    # Signal tracks in [0,14]: 3.5, 5.5, 10.5, 12.5
    # (3.5, 5.5) is contiguous; (5.5, 10.5) has GROUND@8.0 between → not contiguous
    seg = make_bus_segment(bit_width=2, interval_lo=0.0, interval_hi=14.0,
                           bit_order="LO_HI", timing_critical=True)
    engine = buda.DetailedNUTSEngine(stack)
    result = engine.run([seg])

    positions = [s.track_position for s in net_segs_for(result, 1)]
    assert positions == pytest.approx([3.5, 5.5])


def test_timing_critical_skips_non_contiguous_and_finds_next_window():
    stack = make_stack_with_standard_pattern()
    # Signal tracks in [5.0, 14.0]: 5.5, 10.5, 12.5
    # (5.5, 10.5): GROUND@8.0 between → not contiguous
    # (10.5, 12.5): nothing between → contiguous
    seg = make_bus_segment(bit_width=2, interval_lo=5.0, interval_hi=14.0,
                           bit_order="LO_HI", timing_critical=True)
    engine = buda.DetailedNUTSEngine(stack)
    result = engine.run([seg])

    positions = [s.track_position for s in net_segs_for(result, 1)]
    assert positions == pytest.approx([10.5, 12.5])


def test_timing_critical_with_no_valid_window_leaves_bus_unplaced():
    stack = make_stack_with_standard_pattern()
    # Contiguous windows in [0,14]: (3.5,5.5) size=2, (10.5,12.5) size=2
    # No contiguous window of size 3 → unplaced
    seg = make_bus_segment(bit_width=3, interval_lo=0.0, interval_hi=14.0,
                           bit_order="LO_HI", timing_critical=True)
    engine = buda.DetailedNUTSEngine(stack)
    result = engine.run([seg])

    assert result.num_unplaced == 3


# ---------------------------------------------------------------------------
# num_unplaced when bit_width exceeds available signal tracks
# ---------------------------------------------------------------------------

def test_num_unplaced_when_more_bits_than_signal_tracks():
    stack = make_stack_with_standard_pattern()
    # Only 4 signal tracks in [0,14]; requesting 6
    seg = make_bus_segment(bit_width=6, interval_lo=0.0, interval_hi=14.0, bit_order="LO_HI")
    engine = buda.DetailedNUTSEngine(stack)
    result = engine.run([seg])

    assert result.num_unplaced == 6   # entire bus unplaced


def test_num_unplaced_zero_when_all_bits_fit():
    stack = make_stack_with_standard_pattern()
    seg = make_bus_segment(bit_width=4, interval_lo=0.0, interval_hi=14.0, bit_order="LO_HI")
    engine = buda.DetailedNUTSEngine(stack)
    result = engine.run([seg])

    assert result.num_unplaced == 0


# ---------------------------------------------------------------------------
# NetSegment metadata
# ---------------------------------------------------------------------------

def test_net_segment_width_matches_track_slot_width():
    stack = make_stack_with_standard_pattern()
    seg = make_bus_segment(bit_width=1, interval_lo=0.0, interval_hi=14.0, bit_order="LO_HI")
    engine = buda.DetailedNUTSEngine(stack)
    result = engine.run([seg])

    assert len(result.net_segments) == 1
    assert result.net_segments[0].track_position == pytest.approx(3.5)
    assert result.net_segments[0].width == pytest.approx(1.0)  # from TrackSlot, not BusSegment


def test_net_segment_bit_index_is_zero_based():
    stack = make_stack_with_standard_pattern()
    seg = make_bus_segment(bit_width=3, interval_lo=0.0, interval_hi=14.0, bit_order="LO_HI")
    engine = buda.DetailedNUTSEngine(stack)
    result = engine.run([seg])

    indices = sorted(s.bit_index for s in result.net_segments)
    assert indices == [0, 1, 2]


def test_net_segment_bundle_id_and_seg_idx_propagated():
    stack = make_stack_with_standard_pattern()
    seg = make_bus_segment(bundle_id=7, seg_idx=2, bit_width=1,
                           interval_lo=0.0, interval_hi=14.0, bit_order="LO_HI")
    engine = buda.DetailedNUTSEngine(stack)
    result = engine.run([seg])

    ns = result.net_segments[0]
    assert ns.bundle_id == 7
    assert ns.seg_idx   == 2


# ---------------------------------------------------------------------------
# Multiple BusSegments expanded independently
# ---------------------------------------------------------------------------

def test_two_bus_segments_expanded_independently():
    stack = make_stack_with_standard_pattern()
    seg_a = make_bus_segment(bundle_id=1, bit_width=2, interval_lo=0.0,  interval_hi=7.0,
                             bit_order="LO_HI")
    seg_b = make_bus_segment(bundle_id=2, bit_width=2, interval_lo=7.0,  interval_hi=14.0,
                             bit_order="LO_HI")
    engine = buda.DetailedNUTSEngine(stack)
    result = engine.run([seg_a, seg_b])

    assert result.num_unplaced == 0
    assert len(result.net_segments) == 4

    pos_a = sorted(s.track_position for s in result.net_segments if s.bundle_id == 1)
    pos_b = sorted(s.track_position for s in result.net_segments if s.bundle_id == 2)
    assert pos_a == pytest.approx([3.5, 5.5])
    assert pos_b == pytest.approx([10.5, 12.5])


# ---------------------------------------------------------------------------
# Pattern tiling across multiple units
# ---------------------------------------------------------------------------

def test_interval_spanning_two_units_yields_eight_signal_tracks():
    stack = make_stack_with_standard_pattern()
    # [0, 28] = two full units → 4 signal tracks each = 8 total
    seg = make_bus_segment(bit_width=8, interval_lo=0.0, interval_hi=28.0, bit_order="LO_HI")
    engine = buda.DetailedNUTSEngine(stack)
    result = engine.run([seg])

    assert result.num_unplaced == 0
    assert len(result.net_segments) == 8
    # First four from unit 0, next four from unit 1 (centres +14)
    positions = sorted(s.track_position for s in result.net_segments)
    assert positions == pytest.approx([3.5, 5.5, 10.5, 12.5, 17.5, 19.5, 24.5, 26.5])


# ---------------------------------------------------------------------------
# Span-adjustment post-pass: endpoint conns snap (extend OR retract) their end
# to the connected bit's position; mid-span conns only require coverage.
# Regression (flow/dnuts_bit_span_reg.buda): a mid-span stub classified into
# the trunk's lo half made the pass extend-only, so trunk bits whose stub bit
# sat RIGHT of the abstract span end kept a dangling overhang past their stub.
# ---------------------------------------------------------------------------

def test_span_adjust_endpoint_retracts_despite_midspan_conn():
    stack = buda.RoutingGridStack()
    stack.define_layer(5, make_standard_pattern(origin=0.0), False)  # V stubs
    stack.define_layer(6, make_standard_pattern(origin=0.0), True)   # H trunk

    # Trunk: abstract span [180, 600], 2 bits.  hi end is a busterm face
    # (no endpoint conn) and must keep the abstract 600.
    trunk = make_bus_segment(bundle_id=1, seg_idx=0, layer=6,
                             span_lo=180.0, span_hi=600.0,
                             interval_lo=0.0, interval_hi=14.0, bit_width=2)
    # Stub A: endpoint conn at the trunk's lo end.  Its bits land on layer-5
    # tracks at x=185.5 and 187.5 — both RIGHT of the abstract lo end 180,
    # so the trunk bits must RETRACT to them.
    stub_a = make_bus_segment(bundle_id=1, seg_idx=1, layer=5,
                              span_lo=0.0, span_hi=100.0,
                              interval_lo=182.0, interval_hi=196.0, bit_width=2)
    # Stub B: mid-span conn; bits at x=395.5, 397.5 — already covered.
    stub_b = make_bus_segment(bundle_id=1, seg_idx=2, layer=5,
                              span_lo=0.0, span_hi=100.0,
                              interval_lo=392.0, interval_hi=406.0, bit_width=2)

    conn_a = buda.BusSegmentConn()
    conn_a.seg_idx = 1; conn_a.at_pos = 180.0
    conn_a.is_endpoint = True; conn_a.lo_end = True
    conn_b = buda.BusSegmentConn()
    conn_b.seg_idx = 2; conn_b.at_pos = 400.0
    conn_b.is_endpoint = False; conn_b.lo_end = True   # mid-span, lo half
    trunk.connections.append(conn_a)
    trunk.connections.append(conn_b)

    engine = buda.DetailedNUTSEngine(stack)
    result = engine.run([trunk, stub_a, stub_b])
    assert result.num_unplaced == 0

    stub_a_pos = {s.bit_index: s.track_position
                  for s in result.net_segments if s.seg_idx == 1}
    stub_b_pos = {s.bit_index: s.track_position
                  for s in result.net_segments if s.seg_idx == 2}
    assert sorted(stub_a_pos.values()) == pytest.approx([185.5, 187.5])

    trunk_bits = [s for s in result.net_segments if s.seg_idx == 0]
    assert len(trunk_bits) == 2
    for ns in trunk_bits:
        # lo end snaps to the bit's own stub position (retraction past 180).
        assert ns.span_lo == pytest.approx(stub_a_pos[ns.bit_index]), (
            f"bit {ns.bit_index}: span_lo {ns.span_lo} must snap to its stub "
            f"at {stub_a_pos[ns.bit_index]}, not dangle at the abstract 180"
        )
        # busterm hi end untouched; mid-span stub stays covered.
        assert ns.span_hi == pytest.approx(600.0)
        assert ns.span_lo <= stub_b_pos[ns.bit_index] <= ns.span_hi
