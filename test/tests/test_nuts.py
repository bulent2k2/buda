"""
NUTS – Non-Uniform Track Sharing tests.

Tests are scenario-driven to match nuts_track_assignment.feature.
They create TrackSegment / BundleWrapper objects directly and call
NUTSEngine.run() so that the C++ algorithm can be exercised without
needing a full .buda script.
"""
import pytest
import buda


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_bundle(bundle_id, nets, width, segments):
    """Build a BundleWrapper with a single pre-built topology."""
    bundle = buda.HBundle()
    bundle.id = bundle_id
    bundle.net_names = nets          # list[str]

    topo = buda.Topology()
    topo.type = "TEST"
    # Build the segment list first, then assign in one shot.
    # Assigning via append to the pybind11 proxy would modify a copy, not the C++ vector.
    seg_list = []
    for s in segments:
        seg = buda.Segment()
        seg.start = buda.Point(s['x1'], s['y1'])
        seg.end   = buda.Point(s['x2'], s['y2'])
        seg.layer_hint = s['layer']
        seg_list.append(seg)
    topo.segments = seg_list

    wrapper = buda.BundleWrapper()
    wrapper.original_bundle = bundle
    wrapper.width = width
    wrapper.candidates = [topo]
    wrapper.selected_topology_index = 0
    return wrapper


def make_floorplan(*blocks):
    """blocks: (name, x1, y1, x2, y2)"""
    fp = buda.Floorplan()
    for b in blocks:
        fp.add_block(b[0], b[1], b[2], b[3], b[4])
    return fp


# ---------------------------------------------------------------------------
# Scenario: Single segment placed within interval
# ---------------------------------------------------------------------------

def test_single_segment_placed_in_interval():
    fp = make_floorplan(("src", 0, 100, 100, 200), ("dst", 300, 100, 400, 200))
    w = make_bundle(1, ["net_a"], width=10.0,
                    segments=[{'x1': 0, 'y1': 150, 'x2': 300, 'y2': 150, 'layer': 3}])
    ls = buda.LayerStack()
    engine = buda.NUTSEngine(fp, ls)
    engine.set_track_pitch(1.0)
    result = engine.run([w])

    assert len(result.segments) == 1
    ts = result.segments[0]
    assert ts.placed
    assert ts.track_position >= ts.interval_lo
    assert ts.track_position + ts.width <= ts.interval_hi


# ---------------------------------------------------------------------------
# Scenario: Non-overlapping spans → no conflicts
# ---------------------------------------------------------------------------

def test_non_overlapping_spans_no_conflict():
    fp = make_floorplan(("a", 0, 100, 100, 200), ("b", 150, 100, 300, 200))
    w1 = make_bundle(1, ["n1"], width=10.0,
                     segments=[{'x1': 0, 'y1': 150, 'x2': 100, 'y2': 150, 'layer': 3}])
    w2 = make_bundle(2, ["n2"], width=10.0,
                     segments=[{'x1': 150, 'y1': 150, 'x2': 300, 'y2': 150, 'layer': 3}])
    ls = buda.LayerStack()
    engine = buda.NUTSEngine(fp, ls)
    engine.set_track_pitch(1.0)
    result = engine.run([w1, w2])

    assert result.num_overlaps == 0
    assert result.num_violations == 0


# ---------------------------------------------------------------------------
# Scenario: Overlapping spans are separated to different tracks
# ---------------------------------------------------------------------------

def test_overlapping_spans_separated():
    # y=250 is in the Hanan cell [0, 500] formed by the block boundaries
    fp = make_floorplan(("a", 0, 0, 100, 500), ("b", 300, 0, 400, 500))
    w1 = make_bundle(1, ["n1"], width=20.0,
                     segments=[{'x1': 0, 'y1': 250, 'x2': 300, 'y2': 250, 'layer': 3}])
    w2 = make_bundle(2, ["n2"], width=20.0,
                     segments=[{'x1': 100, 'y1': 250, 'x2': 400, 'y2': 250, 'layer': 3}])
    ls = buda.LayerStack()
    engine = buda.NUTSEngine(fp, ls)
    engine.set_track_pitch(1.0)
    result = engine.run([w1, w2])

    assert result.num_overlaps == 0

    ts1 = next((s for s in result.segments if s.bundle_id == 1), None)
    ts2 = next((s for s in result.segments if s.bundle_id == 2), None)
    assert ts1 is not None and ts2 is not None, "Both segments should be placed"
    # The second bus must start at least (width + pitch) above the first.
    gap = abs(ts2.track_position - ts1.track_position)
    assert gap >= 20.0 + 1.0  # width + pitch


# ---------------------------------------------------------------------------
# Scenario: Non-uniform widths
# ---------------------------------------------------------------------------

def test_nonuniform_widths():
    fp = make_floorplan(("a", 0, 0, 100, 1000), ("b", 300, 0, 400, 1000))
    # All three segments span the same x-range → all conflict
    y_mid = 500
    w1 = make_bundle(1, ["n1"], width=5.0,
                     segments=[{'x1': 0, 'y1': y_mid, 'x2': 300, 'y2': y_mid, 'layer': 3}])
    w2 = make_bundle(2, ["n2"], width=30.0,
                     segments=[{'x1': 0, 'y1': y_mid, 'x2': 300, 'y2': y_mid, 'layer': 3}])
    w3 = make_bundle(3, ["n3"], width=15.0,
                     segments=[{'x1': 0, 'y1': y_mid, 'x2': 300, 'y2': y_mid, 'layer': 3}])
    ls = buda.LayerStack()
    engine = buda.NUTSEngine(fp, ls)
    engine.set_track_pitch(2.0)
    result = engine.run([w1, w2, w3])

    assert result.num_overlaps == 0
    for ts in result.segments:
        assert ts.placed
        assert ts.track_position >= 0


# ---------------------------------------------------------------------------
# Scenario: H and V layers solved independently
# ---------------------------------------------------------------------------

def test_layers_solved_independently():
    fp = make_floorplan(("a", 0, 100, 100, 200), ("b", 300, 100, 400, 200))
    # Horizontal segment on M3
    wH = make_bundle(1, ["n1"], width=10.0,
                     segments=[{'x1': 0, 'y1': 150, 'x2': 300, 'y2': 150, 'layer': 3}])
    # Vertical segment on M4 (same routing range in y)
    wV = make_bundle(2, ["n2"], width=10.0,
                     segments=[{'x1': 150, 'y1': 0, 'x2': 150, 'y2': 300, 'layer': 4}])
    ls = buda.LayerStack()
    engine = buda.NUTSEngine(fp, ls)
    engine.set_track_pitch(1.0)
    result = engine.run([wH, wV])

    # Different layers → can never overlap
    assert result.num_overlaps == 0


# ---------------------------------------------------------------------------
# Regression: co-starting segments must be separated (active-set push bug)
# Two segments with the same span_lo and overlapping spans must land at
# distinct perpendicular positions — the sweep-line must register each
# placed segment as occupied before processing the next one.
# ---------------------------------------------------------------------------

def test_co_starting_two_segments_separated():
    """Regression for missing active.push_back in solve_layer sweep."""
    fp = make_floorplan(("a", 0, 200, 100, 500), ("b", 400, 200, 500, 500))
    w1 = make_bundle(1, ["n1"], width=10.0,
                     segments=[{'x1': 0, 'y1': 350, 'x2': 400, 'y2': 350, 'layer': 3}])
    w2 = make_bundle(2, ["n2"], width=10.0,
                     segments=[{'x1': 0, 'y1': 350, 'x2': 300, 'y2': 350, 'layer': 3}])
    ls = buda.LayerStack()
    engine = buda.NUTSEngine(fp, ls)
    engine.set_track_pitch(1.0)
    result = engine.run([w1, w2])

    assert result.num_overlaps == 0
    ts1 = next(s for s in result.segments if s.bundle_id == 1)
    ts2 = next(s for s in result.segments if s.bundle_id == 2)
    assert abs(ts2.track_position - ts1.track_position) >= 10.0 + 1.0


def test_co_starting_three_segments_all_separated():
    """Three segments all at span_lo=0; each must see the previous as occupied."""
    fp = make_floorplan(("a", 0, 0, 100, 1000), ("b", 500, 0, 600, 1000))
    w1 = make_bundle(1, ["n1"], width=10.0,
                     segments=[{'x1': 0, 'y1': 500, 'x2': 500, 'y2': 500, 'layer': 3}])
    w2 = make_bundle(2, ["n2"], width=10.0,
                     segments=[{'x1': 0, 'y1': 500, 'x2': 400, 'y2': 500, 'layer': 3}])
    w3 = make_bundle(3, ["n3"], width=10.0,
                     segments=[{'x1': 0, 'y1': 500, 'x2': 300, 'y2': 500, 'layer': 3}])
    ls = buda.LayerStack()
    engine = buda.NUTSEngine(fp, ls)
    engine.set_track_pitch(1.0)
    result = engine.run([w1, w2, w3])

    assert result.num_overlaps == 0


# ---------------------------------------------------------------------------
# Scenario: Interval too narrow → violation counted, segment still placed
# ---------------------------------------------------------------------------

def test_interval_too_narrow_violation():
    fp = make_floorplan(("a", 0, 100, 100, 106), ("b", 300, 100, 400, 106))
    # width=20, but interval only spans 5 units (105-100)
    w = make_bundle(1, ["n1"], width=20.0,
                    segments=[{'x1': 0, 'y1': 103, 'x2': 300, 'y2': 103, 'layer': 3}])
    ls = buda.LayerStack()
    engine = buda.NUTSEngine(fp, ls)
    engine.set_track_pitch(1.0)
    result = engine.run([w])

    assert len(result.segments) == 1
    assert result.segments[0].placed        # best-effort placement
    assert result.num_violations >= 1
