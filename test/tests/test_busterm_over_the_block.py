"""
pytest-bdd step definitions for features/busterm_over_the_block.feature.

Over-the-block vs thru-the-block TEG (Terminal Equivalence Group) routing modes.

The teg_mode flag per block and the bridge segment generation are not yet
implemented in the C++ API; all scenarios are xfail.
"""
import pytest
import interconnect
from pytest_bdd import scenarios, given, when, then, parsers
from conftest import _find_candidate, _segs_of, _build_all_cts

pytestmark = pytest.mark.xfail(
    strict=False,
    reason='teg_mode and over-the-block bridge generation not yet implemented in C++',
)

scenarios('features/busterm_over_the_block.feature')


# ---------------------------------------------------------------------------
# Context fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def ctx():
    return {}


# ---------------------------------------------------------------------------
# Given — block and layer setup
# ---------------------------------------------------------------------------

@given(parsers.re(r'a block "(?P<name>[^"]+)" at \((?P<x1>\d+),(?P<y1>\d+)\)-\((?P<x2>\d+),(?P<y2>\d+)\)'))
def given_single_rect_block(ctx, name, x1, y1, x2, y2):
    if 'fp' not in ctx:
        ctx['fp'] = interconnect.Floorplan()
    ctx['fp'].add_block(name, int(x1), int(y1), int(x2), int(y2))


@given(parsers.re(
    r'a block "(?P<name>[^"]+)" with rects '
    r'\((?P<rx1>\d+),(?P<ry1>\d+)\)-\((?P<rx2>\d+),(?P<ry2>\d+)\) and '
    r'\((?P<sx1>\d+),(?P<sy1>\d+)\)-\((?P<sx2>\d+),(?P<sy2>\d+)\) and '
    r'teg_mode "(?P<mode>over|thru)"'
))
def given_multi_rect_block_with_teg(ctx, name, rx1, ry1, rx2, ry2, sx1, sy1, sx2, sy2, mode):
    if 'fp' not in ctx:
        ctx['fp'] = interconnect.Floorplan()
    ctx['fp'].add_block_rects(
        name,
        [(int(rx1), int(ry1), int(rx2), int(ry2)),
         (int(sx1), int(sy1), int(sx2), int(sy2))],
        teg_mode=mode,
    )


@given(parsers.re(
    r'a block "(?P<name>[^"]+)" with rects '
    r'\((?P<rx1>\d+),(?P<ry1>\d+)\)-\((?P<rx2>\d+),(?P<ry2>\d+)\) and '
    r'\((?P<sx1>\d+),(?P<sy1>\d+)\)-\((?P<sx2>\d+),(?P<sy2>\d+)\)'
))
def given_multi_rect_block_no_teg(ctx, name, rx1, ry1, rx2, ry2, sx1, sy1, sx2, sy2):
    if 'fp' not in ctx:
        ctx['fp'] = interconnect.Floorplan()
    ctx['fp'].add_block_rects(
        name,
        [(int(rx1), int(ry1), int(rx2), int(ry2)),
         (int(sx1), int(sy1), int(sx2), int(sy2))],
    )


@given(parsers.re(r'layer (?P<layer_name>\w+) is HORIZONTAL with id (?P<lid>\d+)'))
def given_layer_h(ctx, layer_name, lid):
    ctx['layer_h'] = int(lid)
    ctx.setdefault('layer_names', {})[layer_name] = int(lid)


@given(parsers.re(r'layer (?P<layer_name>\w+) is VERTICAL with id (?P<lid>\d+)'))
def given_layer_v(ctx, layer_name, lid):
    ctx['layer_v'] = int(lid)
    ctx.setdefault('layer_names', {})[layer_name] = int(lid)


@given(parsers.parse('both "thru" and "over" teg_mode candidates are generated for "{block}"'))
def given_both_teg_modes(ctx, block):
    ctx['both_teg_modes_block'] = block


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------

@when(parsers.re(
    r'I generate multicast candidates from "(?P<src>[^"]+)" to \["(?P<dsts>[^"]+)"\] using layers M4,M5'
))
def when_generate_multicast(ctx, src, dsts):
    gen = interconnect.TopologyGenerator(ctx['fp'])
    gen.set_layer_ids(ctx['layer_h'], ctx['layer_v'])
    dst_list = [d.strip().strip('"') for d in dsts.split(',')]
    ctx['candidates'] = gen.generate_multicast_candidates(src, dst_list)


@when(parsers.re(
    r'I generate multicast candidates from "(?P<src>[^"]+)" to \["(?P<dst1>[^"]+)","(?P<dst2>[^"]+)"\] using layers M4,M5'
))
def when_generate_multicast_two(ctx, src, dst1, dst2):
    gen = interconnect.TopologyGenerator(ctx['fp'])
    gen.set_layer_ids(ctx['layer_h'], ctx['layer_v'])
    ctx['candidates'] = gen.generate_multicast_candidates(src, [dst1, dst2])


@when(parsers.re(r'I rank the (?P<trunk_key>\S+) candidates by adjusted wirelength'))
def when_rank_candidates(ctx, trunk_key):
    cands = [c for c in ctx['candidates'] if _trunk_key_matches(c, trunk_key)]
    ctx['ranked_candidates'] = sorted(cands, key=lambda c: c.adjusted_wl)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trunk_key_matches(candidate, key):
    """Match a candidate by trunk description key like TRUNK_H@y200."""
    return getattr(candidate, 'trunk_key', '') == key or candidate.type == key


def _find_by_trunk(candidates, trunk_key):
    for c in candidates:
        if _trunk_key_matches(c, trunk_key):
            return c
    return None


def _vstubs_for_block(candidate, block_name):
    """Return list of vertical stub ConnSegs for the named block."""
    stubs = []
    for cs in _segs_of(candidate):
        if cs.horiz:
            continue
        for conn in cs.conns:
            if (getattr(conn, 'kind', None) == interconnect.SegConnKind.BUSTERM
                    and conn.block_name == block_name):
                stubs.append(cs)
    return stubs


def _bridge_for_block(candidate, block_name):
    """Return the bridge ConnSeg for the named block, or None."""
    bridge = getattr(candidate, 'bridge_segments', {})
    return bridge.get(block_name)


# ---------------------------------------------------------------------------
# Then — thru-the-block assertions
# ---------------------------------------------------------------------------

@then(parsers.re(r'a candidate of type "(?P<trunk_key>[^"]+)" exists'))
def then_candidate_exists(ctx, trunk_key):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'No candidate matching {trunk_key!r}; got {[getattr(c,"type","?") for c in ctx["candidates"]]}'


@then(parsers.re(
    r'in the (?P<trunk_key>\S+) candidate "(?P<block>[^"]+)" has exactly 1 V stub connecting to its lower rect'
))
def then_one_vstub_lower(ctx, trunk_key, block):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    stubs = _vstubs_for_block(c, block)
    assert len(stubs) == 1, f'Expected 1 V stub for {block!r}, got {len(stubs)}'
    # Lower rect: stub goes downward (lower y end touches block face)
    stub = stubs[0]
    assert stub.is_lower_rect_connection, f'V stub does not connect to lower rect of {block!r}'


@then(parsers.re(
    r'the V stub from "(?P<block>[^"]+)" in the (?P<trunk_key>\S+) candidate has length (?P<length>\d+)'
))
def then_vstub_length(ctx, block, trunk_key, length):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    stubs = _vstubs_for_block(c, block)
    assert stubs, f'No V stub found for {block!r} in {trunk_key!r}'
    stub = stubs[0]
    stub_len = abs(stub.along_hi - stub.along_lo)
    assert stub_len == int(length), f'Stub length: expected {length}, got {stub_len}'


@then(parsers.re(
    r'the (?P<trunk_key>\S+) candidate has no bridge segment for "(?P<block>[^"]+)"'
))
def then_no_bridge(ctx, trunk_key, block):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    bridge = _bridge_for_block(c, block)
    assert bridge is None, f'Expected no bridge for {block!r} in {trunk_key!r}, but found one'


@then(parsers.re(
    r'"(?P<block>[^"]+)"\'s upper rect is not connected in the (?P<trunk_key>\S+) candidate'
))
def then_upper_rect_not_connected(ctx, block, trunk_key):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    stubs = _vstubs_for_block(c, block)
    for stub in stubs:
        assert not stub.is_upper_rect_connection, \
            f'Upper rect of {block!r} is connected in {trunk_key!r} but should not be'


@then(parsers.re(
    r'in the (?P<trunk_key>\S+) candidate "(?P<block>[^"]+)" has no V stub \(Direct connection via lower rect\)'
))
def then_no_vstub_direct(ctx, trunk_key, block):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    stubs = _vstubs_for_block(c, block)
    assert len(stubs) == 0, f'Expected no V stub (Direct) for {block!r} in {trunk_key!r}, got {len(stubs)}'


# ---------------------------------------------------------------------------
# Then — over-the-block assertions
# ---------------------------------------------------------------------------

@then(parsers.re(
    r'in the (?P<trunk_key>\S+) candidate "(?P<block>[^"]+)" has 2 V stubs \(one to each rect\)'
))
def then_two_vstubs(ctx, trunk_key, block):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    stubs = _vstubs_for_block(c, block)
    assert len(stubs) == 2, f'Expected 2 V stubs for {block!r} in {trunk_key!r}, got {len(stubs)}'


@then(parsers.re(
    r'the V stub down from "(?P<block>[^"]+)" in (?P<trunk_key>\S+) has length (?P<length>\d+)'
))
def then_vstub_down_length(ctx, block, trunk_key, length):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None
    stubs = [s for s in _vstubs_for_block(c, block) if s.is_lower_rect_connection]
    assert stubs, f'No downward V stub for {block!r} in {trunk_key!r}'
    stub_len = abs(stubs[0].along_hi - stubs[0].along_lo)
    assert stub_len == int(length), f'Down stub length: expected {length}, got {stub_len}'


@then(parsers.re(
    r'the V stub up\s+from "(?P<block>[^"]+)" in (?P<trunk_key>\S+) has length (?P<length>\d+)'
))
def then_vstub_up_length(ctx, block, trunk_key, length):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None
    stubs = [s for s in _vstubs_for_block(c, block) if s.is_upper_rect_connection]
    assert stubs, f'No upward V stub for {block!r} in {trunk_key!r}'
    stub_len = abs(stubs[0].along_hi - stubs[0].along_lo)
    assert stub_len == int(length), f'Up stub length: expected {length}, got {stub_len}'


@then(parsers.re(
    r'the (?P<trunk_key>\S+) candidate has a bridge segment for "(?P<block>[^"]+)" at y=(?P<y>\d+)'
))
def then_bridge_at_y(ctx, trunk_key, block, y):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    bridge = _bridge_for_block(c, block)
    assert bridge is not None, f'No bridge found for {block!r} in {trunk_key!r}'
    bridge_y = getattr(bridge, 'perp', None) or getattr(bridge, 'y', None)
    assert bridge_y == int(y), f'Bridge y: expected {y}, got {bridge_y}'


@then(parsers.parse('the bridge segment spans from B\'s leftmost rect face to B\'s rightmost rect face'))
def then_bridge_spans_rects(ctx):
    c = _find_by_trunk(ctx['candidates'], 'TRUNK_H@y200')
    assert c is not None
    bridge = _bridge_for_block(c, 'B')
    assert bridge is not None
    fp = ctx['fp']
    rects = fp.get_block_rects('B')
    expected_lo = min(r.x1 for r in rects)
    expected_hi = max(r.x2 for r in rects)
    assert bridge.along_lo == expected_lo, f'Bridge lo: expected {expected_lo}, got {bridge.along_lo}'
    assert bridge.along_hi == expected_hi, f'Bridge hi: expected {expected_hi}, got {bridge.along_hi}'


@then(parsers.re(r'the (?P<trunk_key>\S+) candidate has a bridge segment for "(?P<block>[^"]+)"'))
def then_has_bridge(ctx, trunk_key, block):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    bridge = _bridge_for_block(c, block)
    assert bridge is not None, f'No bridge found for {block!r} in {trunk_key!r}'


@then(parsers.re(r'the bridge segment runs along the top face of "(?P<block>[^"]+)"\'s union bounding box'))
def then_bridge_along_top(ctx, block):
    c = _find_by_trunk(ctx['candidates'], 'TRUNK_V@x300')
    assert c is not None
    bridge = _bridge_for_block(c, block)
    assert bridge is not None, f'No bridge for {block!r}'
    fp = ctx['fp']
    rects = fp.get_block_rects(block)
    union_y2 = max(r.y2 for r in rects)
    bridge_y = getattr(bridge, 'perp', None) or getattr(bridge, 'y', None)
    assert bridge_y == union_y2, f'Bridge y: expected top of union bbox ({union_y2}), got {bridge_y}'


@then(parsers.re(r'in the (?P<trunk_key>\S+) candidate "(?P<block>[^"]+)" has no bridge segment'))
def then_no_bridge_inline(ctx, trunk_key, block):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    bridge = _bridge_for_block(c, block)
    assert bridge is None, f'Expected no bridge for {block!r} in {trunk_key!r}'


@then(parsers.re(r'"(?P<block>[^"]+)" has a Direct connection in the (?P<trunk_key>\S+) candidate'))
def then_direct_connection(ctx, block, trunk_key):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    for cs in _segs_of(c):
        for conn in cs.conns:
            if (getattr(conn, 'kind', None) == interconnect.SegConnKind.BUSTERM
                    and conn.block_name == block):
                if getattr(conn, 'is_direct', False):
                    return
    pytest.fail(f'{block!r} has no Direct connection in {trunk_key!r}')


@then(parsers.re(
    r'in (?P<trunk_key>\S+) "(?P<b1>[^"]+)" has a bridge segment and "(?P<b2>[^"]+)" does not'
))
def then_b1_has_bridge_b2_does_not(ctx, trunk_key, b1, b2):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    assert _bridge_for_block(c, b1) is not None, f'{b1!r} has no bridge in {trunk_key!r}'
    assert _bridge_for_block(c, b2) is None, f'{b2!r} has a bridge in {trunk_key!r} but should not'


# ---------------------------------------------------------------------------
# Then — ranking assertions
# ---------------------------------------------------------------------------

@then('the thru-the-block candidate ranks before the over-the-block candidate')
def then_thru_ranks_before_over(ctx):
    ranked = ctx.get('ranked_candidates', [])
    assert len(ranked) >= 2, 'Need at least 2 ranked candidates (thru and over)'
    teg_modes = [getattr(c, 'teg_mode', None) for c in ranked]
    assert 'thru' in teg_modes and 'over' in teg_modes, \
        f'Expected both thru and over candidates; got modes: {teg_modes}'
    thru_idx = teg_modes.index('thru')
    over_idx = teg_modes.index('over')
    assert thru_idx < over_idx, \
        f'thru (idx {thru_idx}) must rank before over (idx {over_idx})'
