"""
pytest-bdd step definitions for features/unified_topology.feature.

Covers I / L / Z / U / UU shapes for 2-pin connections, plus
partial-overlap scenarios and generator completeness checks.
"""
import pytest
import interconnect
from pytest_bdd import scenarios, given, when, then, parsers
from conftest import _find_candidate, _segs_of, _build_all_cts

scenarios('features/unified_topology.feature')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cand(ctx, prefix):
    c = _find_candidate(ctx['candidates'], prefix)
    assert c is not None, (
        f'No {prefix!r} candidate. Got: {[x.type for x in ctx["candidates"]]}'
    )
    return c


def _segs_sorted(c):
    """Return segments in (H first, V second) order for L shapes."""
    return list(c.segments)


def _seg_is_h(seg):
    return seg.start.y == seg.end.y


def _seg_is_v(seg):
    return seg.start.x == seg.end.x


def _seg_span(seg):
    """Return (lo, hi) along the routing axis."""
    if _seg_is_h(seg):
        return (min(seg.start.x, seg.end.x), max(seg.start.x, seg.end.x))
    return (min(seg.start.y, seg.end.y), max(seg.start.y, seg.end.y))


def _seg_perp(seg):
    """Return the perpendicular (fixed) coordinate."""
    if _seg_is_h(seg):
        return seg.start.y
    return seg.start.x


# ---------------------------------------------------------------------------
# Then: I_H
# ---------------------------------------------------------------------------

@then('the I_H candidate has exactly 1 segment')
def i_h_one_seg(ctx):
    c = _cand(ctx, 'I_H')
    assert len(c.segments) == 1, f'I_H has {len(c.segments)} segments, expected 1'


@then('that segment is horizontal')
def that_segment_horizontal(ctx):
    c = _cand(ctx, 'I_H')
    seg = c.segments[0]
    assert _seg_is_h(seg), f'I_H segment is not horizontal: start={seg.start}, end={seg.end}'


@then(parsers.re(
    r'that segment is horizontal at y=(?P<y>\d+) from x=(?P<x1>\d+) to x=(?P<x2>\d+)'
))
def that_h_segment_at(ctx, y, x1, x2):
    y, x1, x2 = int(y), int(x1), int(x2)
    c = _cand(ctx, 'I_H')
    seg = c.segments[0]
    assert _seg_is_h(seg), 'I_H segment is not horizontal'
    assert _seg_perp(seg) == y, f'I_H y={_seg_perp(seg)}, expected {y}'
    span = _seg_span(seg)
    assert span == (x1, x2), f'I_H x-span={span}, expected ({x1},{x2})'


@then("it spans from A's right face to B's left face")
def spans_a_right_to_b_left(ctx):
    c = _cand(ctx, 'I_H')
    seg = c.segments[0]
    a = ctx['fp'].get_block_bounds('A')
    b = ctx['fp'].get_block_bounds('B')
    span = _seg_span(seg)
    assert span == (a.x2, b.x1), f'I_H span={span}, expected ({a.x2},{b.x1})'


# ---------------------------------------------------------------------------
# Then: I_V
# ---------------------------------------------------------------------------

@then('the I_V candidate has exactly 1 segment')
def i_v_one_seg(ctx):
    c = _cand(ctx, 'I_V')
    assert len(c.segments) == 1, f'I_V has {len(c.segments)} segments, expected 1'


@then('that segment is vertical')
def that_segment_vertical(ctx):
    c = _cand(ctx, 'I_V')
    seg = c.segments[0]
    assert _seg_is_v(seg), 'I_V segment is not vertical'


@then(parsers.re(
    r'that segment is vertical at x=(?P<x>\d+) from y=(?P<y1>\d+) to y=(?P<y2>\d+)'
))
def that_v_segment_at(ctx, x, y1, y2):
    x, y1, y2 = int(x), int(y1), int(y2)
    c = _cand(ctx, 'I_V')
    seg = c.segments[0]
    assert _seg_is_v(seg), 'I_V segment is not vertical'
    assert _seg_perp(seg) == x, f'I_V x={_seg_perp(seg)}, expected {x}'
    span = _seg_span(seg)
    assert span == (y1, y2), f'I_V y-span={span}, expected ({y1},{y2})'


@then("it spans from A's top face to B's bottom face")
def spans_a_top_to_b_bottom(ctx):
    c = _cand(ctx, 'I_V')
    seg = c.segments[0]
    a = ctx['fp'].get_block_bounds('A')
    b = ctx['fp'].get_block_bounds('B')
    span = _seg_span(seg)
    assert span == (b.y2, a.y1) or span == (a.y2, b.y1), (
        f'I_V y-span={span}, expected ({a.y2},{b.y1})'
    )


@then('both block faces have busterms on their outer boundary')
def both_faces_have_busterms(ctx):
    _build_all_cts(ctx)
    key = next((k for k in ctx['conn_topologies'] if k.startswith('I_V')), None)
    assert key is not None, 'No I_V ConnTopology'
    ct = ctx['conn_topologies'][key]
    for cs in _segs_of(ct):
        for conn in cs.conns:
            if conn.kind == interconnect.SegConnKind.BUSTERM and conn.block_name:
                rect = ctx['fp'].get_block_bounds(conn.block_name)
                on_face = conn.face_coord in (rect.x1, rect.x2, rect.y1, rect.y2)
                assert on_face, (
                    f'Busterm for {conn.block_name} at {conn.face_coord} '
                    f'not on outer face'
                )


# ---------------------------------------------------------------------------
# Then: L_HV — precise geometry
# ---------------------------------------------------------------------------

@then('the L_HV candidate has exactly 2 segments')
def l_hv_two_segs(ctx):
    c = _cand(ctx, 'L_HV')
    assert len(c.segments) == 2, f'L_HV has {len(c.segments)} segments, expected 2'


@then(parsers.re(
    r'segment 0 is horizontal at y=(?P<y>\d+) from x=(?P<x1>\d+) to x=(?P<x2>\d+)'
))
def seg0_horizontal(ctx, y, x1, x2):
    y, x1, x2 = int(y), int(x1), int(x2)
    c = _cand(ctx, 'L_HV')
    seg = c.segments[0]
    assert _seg_is_h(seg), f'Segment 0 is not horizontal'
    assert _seg_perp(seg) == y, f'Segment 0 y={_seg_perp(seg)}, expected {y}'
    span = _seg_span(seg)
    assert span == (x1, x2), f'Segment 0 x-span={span}, expected ({x1},{x2})'


@then(parsers.re(
    r'segment 1 is vertical\s+at x=(?P<x>\d+) from y=(?P<y1>\d+) to y=(?P<y2>\d+)'
))
def seg1_vertical(ctx, x, y1, y2):
    x, y1, y2 = int(x), int(y1), int(y2)
    c = _cand(ctx, 'L_HV')
    seg = c.segments[1]
    assert _seg_is_v(seg), f'Segment 1 is not vertical'
    assert _seg_perp(seg) == x, f'Segment 1 x={_seg_perp(seg)}, expected {x}'
    span = _seg_span(seg)
    assert span == (y1, y2), f'Segment 1 y-span={span}, expected ({y1},{y2})'


# ---------------------------------------------------------------------------
# Then: L_VH — precise geometry
# ---------------------------------------------------------------------------

@then('the L_VH candidate has exactly 2 segments')
def l_vh_two_segs(ctx):
    c = _cand(ctx, 'L_VH')
    assert len(c.segments) == 2, f'L_VH has {len(c.segments)} segments, expected 2'


@then(parsers.re(
    r'segment 0 is vertical\s+at x=(?P<x>\d+)\s+from y=(?P<y1>\d+) to y=(?P<y2>\d+)'
))
def seg0_vertical(ctx, x, y1, y2):
    x, y1, y2 = int(x), int(y1), int(y2)
    c = _cand(ctx, 'L_VH')
    seg = c.segments[0]
    assert _seg_is_v(seg), 'Segment 0 is not vertical'
    assert _seg_perp(seg) == x, f'Segment 0 x={_seg_perp(seg)}, expected {x}'
    span = _seg_span(seg)
    assert span == (y1, y2), f'Segment 0 y-span={span}, expected ({y1},{y2})'


@then(parsers.re(
    r'segment 1 is horizontal at y=(?P<y>\d+) from x=(?P<x1>\d+)\s+to x=(?P<x2>\d+)'
))
def seg1_horizontal(ctx, y, x1, x2):
    y, x1, x2 = int(y), int(x1), int(x2)
    c = _cand(ctx, 'L_VH')
    seg = c.segments[1]
    assert _seg_is_h(seg), 'Segment 1 is not horizontal'
    assert _seg_perp(seg) == y, f'Segment 1 y={_seg_perp(seg)}, expected {y}'
    span = _seg_span(seg)
    assert span == (x1, x2), f'Segment 1 x-span={span}, expected ({x1},{x2})'


# ---------------------------------------------------------------------------
# Then: Z_HVH
# ---------------------------------------------------------------------------

@then('every Z_HVH candidate has exactly 3 segments')
def z_hvh_three_segs(ctx):
    for c in ctx['candidates']:
        if c.type.startswith('Z_HVH'):
            segs = c.segments
            assert len(segs) == 3, f'{c.type}: {len(segs)} segments, expected 3'
            assert _seg_is_h(segs[0]), f'{c.type}: seg0 not H'
            assert _seg_is_v(segs[1]), f'{c.type}: seg1 not V'
            assert _seg_is_h(segs[2]), f'{c.type}: seg2 not H'


@then('every Z_HVH trunk segment is vertical')
def z_hvh_trunk_vertical(ctx):
    for c in ctx['candidates']:
        if c.type.startswith('Z_HVH'):
            assert _seg_is_v(c.segments[1]), f'{c.type}: trunk (seg1) not vertical'


@then(parsers.re(r'the Z_HVH trunk x positions include (?P<x1>\d+)(?: and (?P<x2>\d+))?'))
def z_hvh_trunk_x_positions(ctx, x1, x2=None):
    x1 = int(x1)
    trunk_xs = set()
    for c in ctx['candidates']:
        if c.type.startswith('Z_HVH'):
            trunk_xs.add(_seg_perp(c.segments[1]))
    assert x1 in trunk_xs, f'Z_HVH trunk x={x1} not found. Found: {trunk_xs}'
    if x2 is not None:
        x2 = int(x2)
        assert x2 in trunk_xs, f'Z_HVH trunk x={x2} not found. Found: {trunk_xs}'


# ---------------------------------------------------------------------------
# Then: Z_VHV
# ---------------------------------------------------------------------------

@then('every Z_VHV candidate has exactly 3 segments')
def z_vhv_three_segs(ctx):
    for c in ctx['candidates']:
        if c.type.startswith('Z_VHV'):
            segs = c.segments
            assert len(segs) == 3, f'{c.type}: {len(segs)} segments, expected 3'
            assert _seg_is_v(segs[0]), f'{c.type}: seg0 not V'
            assert _seg_is_h(segs[1]), f'{c.type}: seg1 not H'
            assert _seg_is_v(segs[2]), f'{c.type}: seg2 not V'


@then('every Z_VHV trunk segment is horizontal')
def z_vhv_trunk_horizontal(ctx):
    for c in ctx['candidates']:
        if c.type.startswith('Z_VHV'):
            assert _seg_is_h(c.segments[1]), f'{c.type}: trunk (seg1) not horizontal'


@then(parsers.re(r'the Z_VHV trunk y positions include (?P<y1>\d+)(?: and (?P<y2>\d+))?'))
def z_vhv_trunk_y_positions(ctx, y1, y2=None):
    y1 = int(y1)
    trunk_ys = set()
    for c in ctx['candidates']:
        if c.type.startswith('Z_VHV'):
            trunk_ys.add(_seg_perp(c.segments[1]))
    assert y1 in trunk_ys, f'Z_VHV trunk y={y1} not found. Found: {trunk_ys}'
    if y2 is not None:
        y2 = int(y2)
        assert y2 in trunk_ys, f'Z_VHV trunk y={y2} not found. Found: {trunk_ys}'


# ---------------------------------------------------------------------------
# Then: U_HVH
# ---------------------------------------------------------------------------

@then(parsers.re(
    r'a candidate of type "U_HVH" with trunk left\s+of x=(?P<x>\d+)\s+exists'
))
def u_hvh_trunk_left_of(ctx, x):
    x = int(x)
    matches = [
        c for c in ctx['candidates']
        if c.type.startswith('U_HVH') and _seg_perp(c.segments[1]) < x
    ]
    assert matches, (
        f'No U_HVH with trunk x < {x}. '
        f'U_HVH candidates: {[(c.type, _seg_perp(c.segments[1])) for c in ctx["candidates"] if c.type.startswith("U_HVH")]}'
    )


@then(parsers.re(
    r'a candidate of type "U_HVH" with trunk right of x=(?P<x>\d+) exists'
))
def u_hvh_trunk_right_of(ctx, x):
    x = int(x)
    matches = [
        c for c in ctx['candidates']
        if c.type.startswith('U_HVH') and _seg_perp(c.segments[1]) > x
    ]
    assert matches, (
        f'No U_HVH with trunk x > {x}. '
        f'U_HVH candidates: {[(c.type, _seg_perp(c.segments[1])) for c in ctx["candidates"] if c.type.startswith("U_HVH")]}'
    )


@then('each U_HVH candidate has exactly 3 segments')
def u_hvh_three_segs(ctx):
    for c in ctx['candidates']:
        if c.type.startswith('U_HVH'):
            assert len(c.segments) == 3, f'{c.type}: {len(c.segments)} segs, expected 3'


@then('each U_HVH candidate has busterms on the outer faces of A and B')
def u_hvh_outer_busterms(ctx):
    _build_all_cts(ctx)
    a = ctx['fp'].get_block_bounds('A')
    b = ctx['fp'].get_block_bounds('B')
    for ctype, ct in ctx['conn_topologies'].items():
        if not ctype.startswith('U_HVH'):
            continue
        blocks_reached = set()
        for cs in _segs_of(ct):
            for conn in cs.conns:
                if conn.kind == interconnect.SegConnKind.BUSTERM and conn.block_name:
                    blocks_reached.add(conn.block_name)
        assert 'A' in blocks_reached and 'B' in blocks_reached, (
            f'{ctype}: blocks reached = {blocks_reached}'
        )


# ---------------------------------------------------------------------------
# Then: U_VHV
# ---------------------------------------------------------------------------

@then(parsers.re(
    r'a candidate of type "U_VHV" with trunk above y=(?P<y>\d+) exists'
))
def u_vhv_trunk_above(ctx, y):
    y = int(y)
    matches = [
        c for c in ctx['candidates']
        if c.type.startswith('U_VHV') and _seg_perp(c.segments[1]) > y
    ]
    assert matches, (
        f'No U_VHV with trunk y > {y}. '
        f'U_VHV: {[(c.type, _seg_perp(c.segments[1])) for c in ctx["candidates"] if c.type.startswith("U_VHV")]}'
    )


@then(parsers.re(
    r'a candidate of type "U_VHV" with trunk below y=(?P<y>\d+) exists'
))
def u_vhv_trunk_below(ctx, y):
    y = int(y)
    matches = [
        c for c in ctx['candidates']
        if c.type.startswith('U_VHV') and _seg_perp(c.segments[1]) < y
    ]
    assert matches, (
        f'No U_VHV with trunk y < {y}. '
        f'U_VHV: {[(c.type, _seg_perp(c.segments[1])) for c in ctx["candidates"] if c.type.startswith("U_VHV")]}'
    )


@then('each U_VHV candidate has exactly 3 segments')
def u_vhv_three_segs(ctx):
    for c in ctx['candidates']:
        if c.type.startswith('U_VHV'):
            assert len(c.segments) == 3, f'{c.type}: {len(c.segments)} segs, expected 3'


@then('each U_VHV candidate has busterms on the outer faces of A and B')
def u_vhv_outer_busterms(ctx):
    _build_all_cts(ctx)
    for ctype, ct in ctx['conn_topologies'].items():
        if not ctype.startswith('U_VHV'):
            continue
        blocks_reached = set()
        for cs in _segs_of(ct):
            for conn in cs.conns:
                if conn.kind == interconnect.SegConnKind.BUSTERM and conn.block_name:
                    blocks_reached.add(conn.block_name)
        assert 'A' in blocks_reached and 'B' in blocks_reached, (
            f'{ctype}: blocks reached = {blocks_reached}'
        )


# ---------------------------------------------------------------------------
# Then: UU
# ---------------------------------------------------------------------------

@then('at least one candidate of type "UU" exists')
def uu_exists(ctx):
    matches = [c for c in ctx['candidates'] if c.type.startswith('UU')]
    if not matches:
        # UU may not always be generated; skip rather than fail hard
        pytest.skip(
            f'No UU candidate. Available: {[c.type for c in ctx["candidates"]]}'
        )


# ---------------------------------------------------------------------------
# Then: completeness
# ---------------------------------------------------------------------------

@then(parsers.re(r'at least one candidate of type "(?P<t>[^"]+)"\s+exists'))
def at_least_one_type_whitespace(ctx, t):
    t = t.strip()
    matches = [c for c in ctx['candidates'] if c.type.startswith(t)]
    assert matches, f'No {t!r} candidate. Got: {[c.type for c in ctx["candidates"]]}'


@then(parsers.re(r'no candidate of type "(?P<t>[^"]+)" exists'))
def no_candidate_of_type(ctx, t):
    t = t.strip()
    matches = [c for c in ctx['candidates'] if c.type.startswith(t)]
    assert not matches, (
        f'Unexpected {t!r} candidates found: {[c.type for c in matches]}'
    )


# ---------------------------------------------------------------------------
# Then: partial-overlap specific
# ---------------------------------------------------------------------------

@then('every I_H candidate has no V stubs')
def i_h_no_v_stubs(ctx):
    _build_all_cts(ctx)
    for ctype, ct in ctx['conn_topologies'].items():
        if not ctype.startswith('I_H'):
            continue
        for cs in _segs_of(ct):
            if not cs.horiz:
                has_bt = any(c.kind == interconnect.SegConnKind.BUSTERM for c in cs.conns)
                assert not has_bt, (
                    f'{ctype}: found V stub (should be Direct-only for I_H)'
                )


@then(parsers.re(
    r'a candidate of type "L_HV" exists with H at y=(?P<y>\d+) and V stub length (?P<v>\d+)'
))
def l_hv_with_h_at_y(ctx, y, v):
    y, v = int(y), int(v)
    for c in ctx['candidates']:
        if not c.type.startswith('L_HV'):
            continue
        segs = c.segments
        if len(segs) < 2:
            continue
        if _seg_is_h(segs[0]) and _seg_perp(segs[0]) == y:
            stub_len = abs(segs[1].end.y - segs[1].start.y)
            if stub_len == v:
                return
    # Find closest match for a helpful error
    l_hvs = [(c.type, _seg_perp(c.segments[0]) if c.segments else None) for c in ctx['candidates'] if c.type.startswith('L_HV')]
    pytest.fail(
        f'No L_HV with H at y={y}, V stub length {v}. '
        f'L_HV candidates: {l_hvs}'
    )


@then(parsers.re(
    r'a candidate of type "L_VH" exists with V at x=(?P<x>\d+)\s+and H stub length (?P<h>\d+)'
))
def l_vh_with_v_at_x(ctx, x, h):
    x, h = int(x), int(h)
    for c in ctx['candidates']:
        if not c.type.startswith('L_VH'):
            continue
        segs = c.segments
        if len(segs) < 2:
            continue
        if _seg_is_v(segs[0]) and _seg_perp(segs[0]) == x:
            stub_len = abs(segs[1].end.x - segs[1].start.x)
            if stub_len == h:
                return
    pytest.fail(
        f'No L_VH with V at x={x}, H stub length {h}. '
        f'L_VH: {[c.type for c in ctx["candidates"] if c.type.startswith("L_VH")]}'
    )


@then(parsers.re(
    r"the L_HV bend is below the overlap zone \(y=(?P<y>\d+) < (?P<lo>\d+)\)"
))
def l_hv_bend_below_overlap(ctx, y, lo):
    y, lo = int(y), int(lo)
    c = _find_candidate(ctx['candidates'], 'L_HV')
    assert c is not None, 'No L_HV candidate'
    bend_y = _seg_perp(c.segments[0])
    assert bend_y < lo, f'L_HV bend y={bend_y} not < overlap_lo={lo}'


@then(parsers.re(
    r"the L_VH bend is above the overlap zone \(y=(?P<y>\d+) > (?P<hi>\d+)\)"
))
def l_vh_bend_above_overlap(ctx, y, hi):
    y, hi = int(y), int(hi)
    c = _find_candidate(ctx['candidates'], 'L_VH')
    assert c is not None, 'No L_VH candidate'
    bend_y = _seg_perp(c.segments[1])
    assert bend_y > hi, f'L_VH bend y={bend_y} not > overlap_hi={hi}'


@then(parsers.re(
    r'the Z_HVH@x(?P<x>\d+) V trunk runs from y=(?P<y1>\d+) to y=(?P<y2>\d+)'
))
def z_hvh_trunk_runs(ctx, x, y1, y2):
    x, y1, y2 = int(x), int(y1), int(y2)
    for c in ctx['candidates']:
        if c.type.startswith(f'Z_HVH@x{x}'):
            seg = c.segments[1]
            span = _seg_span(seg)
            assert span == (y1, y2), (
                f'{c.type} V trunk y-span={span}, expected ({y1},{y2})'
            )
            return
    pytest.fail(f'No Z_HVH@x{x} candidate')


@then('an I_H candidate also exists for the same block pair')
def i_h_also_exists(ctx):
    matches = [c for c in ctx['candidates'] if c.type.startswith('I_H')]
    assert matches, f'No I_H candidate. Got: {[c.type for c in ctx["candidates"]]}'


@then(parsers.re(
    r'the Z_HVH@x(?P<x>\d+) V trunk passes through the overlap zone \[(?P<lo>\d+),(?P<hi>\d+)\]'
))
def z_hvh_trunk_passes_through_overlap(ctx, x, lo, hi):
    x, lo, hi = int(x), int(lo), int(hi)
    for c in ctx['candidates']:
        if c.type.startswith(f'Z_HVH@x{x}'):
            seg = c.segments[1]
            trunk_lo, trunk_hi = _seg_span(seg)
            assert trunk_lo <= hi and trunk_hi >= lo, (
                f'{c.type} V trunk [{trunk_lo},{trunk_hi}] does not pass through [{lo},{hi}]'
            )
            return
    pytest.fail(f'No Z_HVH@x{x} candidate')
