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

"""
Block corner margin tests.

Covers the scenarios in features/corner_margin.feature.

Corner margin per block keeps trunk/stub connections away from block corners.
  dx: margin on horizontal faces (top/bottom), applied in the X direction.
  dy: margin on vertical faces   (left/right), applied in the Y direction.

Pass 1 of ConnTopology.compute_slide_ranges shrinks the face extent inward:
  H segment on left/right face:  perp range = [y1+dy, y2-dy]
  V segment on top/bottom face:  perp range = [x1+dx, x2-dx]
Guard: if 2*margin >= face extent, the full extent is kept (no inversion).
"""
import pytest
import buda


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_fp_with_block(name, x1, y1, x2, y2, dx=0, dy=0):
    fp = buda.Floorplan()
    fp.add_block(name, x1, y1, x2, y2)
    if dx > 0 or dy > 0:
        fp.set_block_corner_margin(name, dx, dy)
    return fp


def make_topo_h_on_left_face(blk_name, fp):
    """L_HV-style topology: H segment whose start is on the left face of blk_name."""
    rect = fp.get_block_bounds(blk_name)
    # H segment: from (rect.x1, mid_y) to (rect.x1 + 500, mid_y)
    mid_y = (rect.y1 + rect.y2) // 2
    src_blk = "src_helper"
    fp.add_block(src_blk, rect.x1 + 500, rect.y1, rect.x1 + 600, rect.y2)

    topo = buda.Topology()
    topo.type = "TEST_H_LEFT"
    seg = buda.Segment()
    seg.start = buda.Point(rect.x1, mid_y)
    seg.end   = buda.Point(rect.x1 + 500, mid_y)
    seg.layer_hint = 4
    topo.segments = [seg]
    # Annotate: start endpoint = blk_name
    bt = buda.Busterm()
    bt.block_name = blk_name
    bt.bbox = rect
    topo.seg_busterms = {0: (bt, None)}
    return topo


def make_topo_h_on_right_face(blk_name, fp):
    """H segment whose end is on the right face of blk_name."""
    rect = fp.get_block_bounds(blk_name)
    mid_y = (rect.y1 + rect.y2) // 2
    fp.add_block("src_helper2", rect.x2 - 600, rect.y1, rect.x2 - 500, rect.y2)

    topo = buda.Topology()
    topo.type = "TEST_H_RIGHT"
    seg = buda.Segment()
    seg.start = buda.Point(rect.x2 - 500, mid_y)
    seg.end   = buda.Point(rect.x2, mid_y)
    seg.layer_hint = 4
    topo.segments = [seg]
    bt = buda.Busterm()
    bt.block_name = blk_name
    bt.bbox = rect
    topo.seg_busterms = {0: (None, bt)}
    return topo


def make_topo_v_on_top_face(blk_name, fp):
    """V segment whose end is on the top face of blk_name."""
    rect = fp.get_block_bounds(blk_name)
    mid_x = (rect.x1 + rect.x2) // 2
    fp.add_block("src_helper3", rect.x1, rect.y2 - 600, rect.x2, rect.y2 - 500)

    topo = buda.Topology()
    topo.type = "TEST_V_TOP"
    seg = buda.Segment()
    seg.start = buda.Point(mid_x, rect.y2 - 500)
    seg.end   = buda.Point(mid_x, rect.y2)
    seg.layer_hint = 5
    topo.segments = [seg]
    bt = buda.Busterm()
    bt.block_name = blk_name
    bt.bbox = rect
    topo.seg_busterms = {0: (None, bt)}
    return topo


def make_topo_v_on_bottom_face(blk_name, fp):
    """V segment whose start is on the bottom face of blk_name."""
    rect = fp.get_block_bounds(blk_name)
    mid_x = (rect.x1 + rect.x2) // 2
    fp.add_block("src_helper4", rect.x1, rect.y1 + 500, rect.x2, rect.y1 + 600)

    topo = buda.Topology()
    topo.type = "TEST_V_BOT"
    seg = buda.Segment()
    seg.start = buda.Point(mid_x, rect.y1)
    seg.end   = buda.Point(mid_x, rect.y1 + 500)
    seg.layer_hint = 5
    topo.segments = [seg]
    bt = buda.Busterm()
    bt.block_name = blk_name
    bt.bbox = rect
    topo.seg_busterms = {0: (bt, None)}
    return topo


def slide_range(ct, seg_idx=0):
    """Return (perp_lo, perp_hi) of the given ConnSeg."""
    cs = ct.segs()[seg_idx]
    return (cs.perp_lo, cs.perp_hi)


# ---------------------------------------------------------------------------
# Scenario: No margin — full face extent
# ---------------------------------------------------------------------------

def test_no_margin_full_face_extent():
    """Scenario: No margin — slide range is the full face extent."""
    fp = make_fp_with_block("blk", 0, 0, 100, 200)
    topo = make_topo_h_on_left_face("blk", fp)

    ct = buda.ConnTopology()
    ct.build(topo, fp)

    lo, hi = slide_range(ct)
    assert lo == 0,   f"Expected perp_lo=0,   got {lo}"
    assert hi == 200, f"Expected perp_hi=200, got {hi}"


# ---------------------------------------------------------------------------
# Scenario: Absolute dy — vertical face shrinks in Y
# ---------------------------------------------------------------------------

def test_absolute_dy_shrinks_vertical_face():
    """Scenario: Absolute dy margin — vertical face slide range shrinks symmetrically."""
    fp = make_fp_with_block("blk", 0, 0, 100, 200, dy=20)
    topo = make_topo_h_on_left_face("blk", fp)

    ct = buda.ConnTopology()
    ct.build(topo, fp)

    lo, hi = slide_range(ct)
    assert lo == 20,  f"Expected perp_lo=20,  got {lo}"
    assert hi == 180, f"Expected perp_hi=180, got {hi}"


# ---------------------------------------------------------------------------
# Scenario: Absolute dx — horizontal face shrinks in X
# ---------------------------------------------------------------------------

def test_absolute_dx_shrinks_horizontal_face():
    """Scenario: Absolute dx margin — horizontal face slide range shrinks symmetrically."""
    fp = make_fp_with_block("blk", 0, 0, 200, 100, dx=15)
    topo = make_topo_v_on_top_face("blk", fp)

    ct = buda.ConnTopology()
    ct.build(topo, fp)

    lo, hi = slide_range(ct)
    assert lo == 15,  f"Expected perp_lo=15,  got {lo}"
    assert hi == 185, f"Expected perp_hi=185, got {hi}"


# ---------------------------------------------------------------------------
# Scenario: pct_v margin
# ---------------------------------------------------------------------------

def test_pct_v_margin_computes_dy():
    """Scenario: Percentage pct_v margin — dy computed as fraction of block height."""
    # Block height = 200; 10% → dy = 20
    fp = make_fp_with_block("blk", 0, 0, 100, 200)
    rect = fp.get_block_bounds("blk")
    dy = round((rect.y2 - rect.y1) * 10 / 100)  # 20
    fp.set_block_corner_margin("blk", 0, dy)

    topo = make_topo_h_on_right_face("blk", fp)
    ct = buda.ConnTopology()
    ct.build(topo, fp)

    lo, hi = slide_range(ct)
    assert lo == 20,  f"Expected perp_lo=20,  got {lo}"
    assert hi == 180, f"Expected perp_hi=180, got {hi}"


# ---------------------------------------------------------------------------
# Scenario: pct_h margin
# ---------------------------------------------------------------------------

def test_pct_h_margin_computes_dx():
    """Scenario: Percentage pct_h margin — dx computed as fraction of block width."""
    # Block width = 200; 10% → dx = 20
    fp = make_fp_with_block("blk", 0, 0, 200, 100)
    rect = fp.get_block_bounds("blk")
    dx = round((rect.x2 - rect.x1) * 10 / 100)  # 20
    fp.set_block_corner_margin("blk", dx, 0)

    topo = make_topo_v_on_bottom_face("blk", fp)
    ct = buda.ConnTopology()
    ct.build(topo, fp)

    lo, hi = slide_range(ct)
    assert lo == 20,  f"Expected perp_lo=20,  got {lo}"
    assert hi == 180, f"Expected perp_hi=180, got {hi}"


# ---------------------------------------------------------------------------
# Scenario: Block too small — guard prevents inverted interval
# ---------------------------------------------------------------------------

def test_small_block_guard_prevents_inversion():
    """Scenario: Block too small — margin guard prevents inverted interval.
    Block height=30, dy=20 → y1+dy=20, y2-dy=10 → inverted → use full [0, 30].
    """
    fp = make_fp_with_block("blk", 0, 0, 100, 30, dy=20)
    topo = make_topo_h_on_left_face("blk", fp)

    ct = buda.ConnTopology()
    ct.build(topo, fp)

    lo, hi = slide_range(ct)
    assert lo == 0,  f"Expected perp_lo=0  (margin guard), got {lo}"
    assert hi == 30, f"Expected perp_hi=30 (margin guard), got {hi}"


# ---------------------------------------------------------------------------
# Scenario: Specifying only dy mirrors to dx (tested via CLI parsing logic)
# ---------------------------------------------------------------------------

def test_nominal_at_lower_face_boundary_skips_margin():
    """
    Scenario: Nominal at face boundary — margin not applied (topology-generator case).
    Block "src" at (200,400)-(300,600) with dy=20.
    H segment at nominal y=400 (= src.y1, bottom face boundary).
    Margin range = [420, 580]; 400 < 420 → guard fires → slide = [400, 600].
    Segment end is in open space (no dst block) to avoid extra constraints.
    """
    fp = make_fp_with_block("src", 200, 400, 300, 600, dy=20)
    # H segment: start on src's right face at y=400 (= src.y1), end in open space.
    topo = buda.Topology()
    topo.type = "TEST_H_BOUNDARY"
    seg = buda.Segment()
    seg.start = buda.Point(300, 400)
    seg.end   = buda.Point(900, 400)  # open space — no block there
    seg.layer_hint = 4
    topo.segments = [seg]
    bt = buda.Busterm()
    bt.block_name = "src"
    bt.bbox = fp.get_block_bounds("src")
    topo.seg_busterms = {0: (bt, None)}  # only start annotated

    ct = buda.ConnTopology()
    ct.build(topo, fp)
    lo, hi = slide_range(ct)
    assert lo == 400, f"Expected perp_lo=400 (guard: nominal at boundary), got {lo}"
    assert hi == 600, f"Expected perp_hi=600 (guard: nominal at boundary), got {hi}"


def test_nominal_at_upper_face_boundary_skips_margin():
    """
    Scenario: Nominal at opposite face boundary — margin not applied.
    Block "src" at (200,400)-(300,600) with dx=15.
    V segment at nominal x=300 (= src.x2, right face boundary).
    Margin range [215, 285]; 300 > 285 → guard fires → slide = [200, 300].
    Segment end is in open space to avoid extra constraints.
    """
    fp = make_fp_with_block("src", 200, 400, 300, 600, dx=15)
    # V segment: start on src's right face at x=300, going downward to open space.
    topo = buda.Topology()
    topo.type = "TEST_V_BOUNDARY"
    seg = buda.Segment()
    seg.start = buda.Point(300, 400)
    seg.end   = buda.Point(300, 0)   # open space — no block there
    seg.layer_hint = 5
    topo.segments = [seg]
    bt = buda.Busterm()
    bt.block_name = "src"
    bt.bbox = fp.get_block_bounds("src")
    topo.seg_busterms = {0: (bt, None)}  # only start annotated

    ct = buda.ConnTopology()
    ct.build(topo, fp)
    lo, hi = slide_range(ct)
    assert lo == 200, f"Expected perp_lo=200 (guard: nominal at boundary), got {lo}"
    assert hi == 300, f"Expected perp_hi=300 (guard: nominal at boundary), got {hi}"


def test_dx_dy_applied_independently():
    """dy applies to vertical faces, dx to horizontal — both can coexist on one block."""
    fp = make_fp_with_block("blk", 0, 0, 200, 200, dx=25, dy=25)

    # H segment on left face → uses dy=25
    topo_h = make_topo_h_on_left_face("blk", fp)
    ct_h = buda.ConnTopology()
    ct_h.build(topo_h, fp)
    lo_h, hi_h = slide_range(ct_h)
    assert lo_h == 25  and hi_h == 175, f"H slide range wrong: [{lo_h}, {hi_h}]"

    # V segment on top face → uses dx=25 (fresh fp to avoid helper block collisions)
    fp2 = make_fp_with_block("blk", 0, 0, 200, 200, dx=25, dy=25)
    topo_v = make_topo_v_on_top_face("blk", fp2)
    ct_v = buda.ConnTopology()
    ct_v.build(topo_v, fp2)
    lo_v, hi_v = slide_range(ct_v)
    assert lo_v == 25 and hi_v == 175, f"V slide range wrong: [{lo_v}, {hi_v}]"


# ---------------------------------------------------------------------------
# Scenario: NUTS places trunk within margin-adjusted range
# ---------------------------------------------------------------------------

def test_nuts_respects_margin_adjusted_range():
    """Scenario: NUTS places trunk within margin-adjusted range.
    Block (0,0)-(100,200) with dy=30 → slide [30, 170].
    H segment at nominal y=100: NUTS should place in [30, 170].
    """
    fp = make_fp_with_block("blk", 0, 0, 100, 200, dy=30)
    # Add a second block so NUTS has a valid 2-block problem
    fp.add_block("dst", 600, 50, 700, 150)
    topo = make_topo_h_on_left_face("blk", fp)

    ct = buda.ConnTopology()
    ct.build(topo, fp)

    lo, hi = slide_range(ct)
    assert lo == 30 and hi == 170, f"Slide range wrong: [{lo}, {hi}]"

    # Feed into NUTS and verify the placed position respects the range.
    ls = buda.LayerStack()
    nuts = buda.NUTSEngine(fp, ls)
    nuts.set_track_pitch(1.0)

    # Build BundleWrapper with the topology segment.
    seg_h = topo.segments[0]
    track_seg = buda.TrackSegment()
    track_seg.bundle_id   = 1
    track_seg.seg_idx     = 0
    track_seg.layer       = seg_h.layer_hint
    track_seg.span_lo     = min(seg_h.start.x, seg_h.end.x)
    track_seg.span_hi     = max(seg_h.start.x, seg_h.end.x)
    track_seg.interval_lo = lo
    track_seg.interval_hi = hi
    track_seg.width       = 10.0
    track_seg.track_position = ct.segs()[0].perp_pos  # nominal = 100

    # NUTS result wrapping
    bundle = buda.HBundle()
    bundle.id = 1
    bw = buda.BundleWrapper()
    bw.input.original_bundle = bundle
    bw.input.width = 10.0
    bw.plan.selected_topology_index = 0
    bw.input.candidates = [topo]
    bw.plan.seg_layers = [seg_h.layer_hint]

    result = nuts.run([bw])
    placed_segs = [s for s in result.segments if s.bundle_id == 1]
    assert placed_segs, "NUTS should produce at least one placed segment"
    pos = placed_segs[0].track_position
    assert lo <= pos <= hi, (
        f"NUTS placed track at {pos}, outside margin-adjusted range [{lo}, {hi}]"
    )


# ---------------------------------------------------------------------------
# CLI parsing: pct_h / pct_v produce correct absolute margins
# ---------------------------------------------------------------------------

def test_cli_pct_parsing_produces_correct_margin():
    """Verify that pct_h/pct_v are correctly converted to absolute dx/dy."""
    # Simulate what buda_cli.py does for:
    #   add_block blk 0 0 200 400 corner_margin pct_h 10 pct_v 5
    x1, y1, x2, y2 = 0, 0, 200, 400
    pct_h, pct_v = 10.0, 5.0
    expected_dx = round((x2 - x1) * pct_h / 100.0)  # 20
    expected_dy = round((y2 - y1) * pct_v / 100.0)  # 20

    fp = buda.Floorplan()
    fp.add_block("blk", x1, y1, x2, y2)
    fp.set_block_corner_margin("blk", expected_dx, expected_dy)

    cm = fp.get_block_corner_margin("blk")
    assert cm.dx == expected_dx, f"Expected dx={expected_dx}, got {cm.dx}"
    assert cm.dy == expected_dy, f"Expected dy={expected_dy}, got {cm.dy}"


# ---------------------------------------------------------------------------
# Scenario: Global margin applies to blocks with no per-block override
# ---------------------------------------------------------------------------

def test_global_margin_applies_to_unoverridden_block():
    """Scenario: Global margin applies to blocks with no per-block override.
    global dy=20 → block 'a' (no override) gets [20,180]; block 'b' (dy=10) gets [10,190].
    """
    fp = buda.Floorplan()
    fp.set_global_corner_margin(20, 20)
    fp.add_block("a", 0, 0, 100, 200)            # no per-block override → global dy=20
    fp.add_block("b", 0, 0, 100, 200)            # per-block dy=10 overrides global
    fp.set_block_corner_margin("b", 10, 10)

    # Block "a" should use global margin dy=20.
    topo_a = make_topo_h_on_left_face("a", fp)
    ct_a = buda.ConnTopology()
    ct_a.build(topo_a, fp)
    lo_a, hi_a = slide_range(ct_a)
    assert lo_a == 20 and hi_a == 180, \
        f"Block 'a' (global margin) slide wrong: [{lo_a}, {hi_a}]"

    # Block "b" should use per-block dy=10 (overrides global).  fp2 has only "b",
    # so annotate_topology taps just "b" (the segment end is in open space) — no
    # helper block needed to expose the per-block margin.
    fp2 = buda.Floorplan()
    fp2.set_global_corner_margin(20, 20)
    fp2.add_block("b", 0, 0, 100, 200)
    fp2.set_block_corner_margin("b", 10, 10)
    rect_b = fp2.get_block_bounds("b")
    mid_y_b = (rect_b.y1 + rect_b.y2) // 2
    topo_b = buda.Topology()
    topo_b.type = "TEST_H_LEFT_B"
    seg_b = buda.Segment()
    seg_b.start = buda.Point(rect_b.x1, mid_y_b)   # on "b"'s left face
    seg_b.end   = buda.Point(rect_b.x1 + 499, mid_y_b)  # open space, no block
    seg_b.layer_hint = 4
    topo_b.segments = [seg_b]
    buda.annotate_topology(topo_b, fp2)
    ct_b = buda.ConnTopology()
    ct_b.build(topo_b, fp2)
    lo_b, hi_b = slide_range(ct_b)
    assert lo_b == 10 and hi_b == 190, \
        f"Block 'b' (per-block margin) slide wrong: [{lo_b}, {hi_b}]"
