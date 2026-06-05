"""
pytest-bdd step definitions for features/busterm_over_the_block.feature.

Over-the-block vs thru-the-block TEG (Terminal Equivalence Group) routing modes.

Scenarios 1–7 test currently-implemented behaviour and must PASS.
Scenario 8 (adjusted-wirelength ranking) is xfail: Topology.adjusted_wl and
  per-topology teg_mode attribute are not yet in the C++ API.
"""
import pytest
import buda
from pytest_bdd import scenario, given, when, then, parsers


# ---------------------------------------------------------------------------
# Scenario test functions  (explicit per-scenario so we can mark selectively)
# ---------------------------------------------------------------------------

@scenario('features/busterm_over_the_block.feature',
          'Thru-the-block (default) — trunk in gap connects to nearest rect only')
def test_thrutheblock_default__trunk_in_gap_connects_to_nearest_rect_only():
    pass


@scenario('features/busterm_over_the_block.feature',
          'Thru-the-block — trunk inside a rect needs no stub (Direct connection)')
def test_thrutheblock__trunk_inside_a_rect_needs_no_stub_direct_connection():
    pass


@scenario('features/busterm_over_the_block.feature',
          'Over-the-block — trunk in gap generates bridge over block')
def test_overtheblock__trunk_in_gap_generates_bridge_over_block():
    pass


@scenario('features/busterm_over_the_block.feature',
          'Over-the-block — trunk inside one rect → no bridge needed')
def test_overtheblock__trunk_inside_one_rect__no_bridge_needed():
    pass


@scenario('features/busterm_over_the_block.feature',
          'Over-the-block — L-shaped block with H trunk above notch')
def test_overtheblock__lshaped_block_with_h_trunk_above_notch():
    pass


@scenario('features/busterm_over_the_block.feature',
          'Over-the-block — bridge is omitted when rects are adjacent (no gap)')
def test_overtheblock__bridge_is_omitted_when_rects_are_adjacent_no_gap():
    pass


@scenario('features/busterm_over_the_block.feature',
          'Global teg_mode overridden per block')
def test_global_teg_mode_overridden_per_block():
    pass


@pytest.mark.xfail(
    strict=False,
    reason='Topology.adjusted_wl and per-topology teg_mode attribute not yet in C++ API',
)
@scenario('features/busterm_over_the_block.feature',
          'Over-the-block bridge topology has higher adjusted wirelength than thru')
def test_overtheblock_bridge_topology_has_higher_adjusted_wirelength_than_thru():
    pass


# ---------------------------------------------------------------------------
# Given — multi-rect block with explicit teg_mode (unique to this file)
# ---------------------------------------------------------------------------

@given(parsers.re(
    r'a block "(?P<name>[^"]+)" with rects '
    r'\((?P<rx1>\d+),(?P<ry1>\d+)\)-\((?P<rx2>\d+),(?P<ry2>\d+)\) and '
    r'\((?P<sx1>\d+),(?P<sy1>\d+)\)-\((?P<sx2>\d+),(?P<sy2>\d+)\) and '
    r'teg_mode "(?P<mode>over|thru)"'
))
def given_multi_rect_block_with_teg(ctx, name, rx1, ry1, rx2, ry2,
                                    sx1, sy1, sx2, sy2, mode):
    teg = buda.TegMode.OVER if mode.lower() == 'over' else buda.TegMode.THRU
    ctx['fp'].add_block_rects(
        name,
        [(int(rx1), int(ry1), int(rx2), int(ry2)),
         (int(sx1), int(sy1), int(sx2), int(sy2))],
        teg_mode=teg,
    )
    ctx.setdefault('block_names', []).append(name)
    ctx['block_rects'][name] = [
        (int(rx1), int(ry1), int(rx2), int(ry2)),
        (int(sx1), int(sy1), int(sx2), int(sy2)),
    ]


@given(parsers.parse('both "thru" and "over" teg_mode candidates are generated for "{block}"'))
def given_both_teg_modes(ctx, block):
    ctx['both_teg_modes_block'] = block


# ---------------------------------------------------------------------------
# When — wirelength ranking (scenario 8 only; scenario 8 is xfail)
# ---------------------------------------------------------------------------

@when(parsers.re(r'I rank the (?P<trunk_key>\S+) candidates by adjusted wirelength'))
def when_rank_candidates(ctx, trunk_key):
    cands = [c for c in ctx['candidates']
             if c.type == trunk_key or c.type.startswith(trunk_key)]
    ctx['ranked_candidates'] = sorted(cands, key=lambda c: c.adjusted_wl)


# ---------------------------------------------------------------------------
# Helpers — work directly on Topology (raw segments + seg_busterms)
# ---------------------------------------------------------------------------

def _find_by_trunk(candidates, trunk_key):
    for c in candidates:
        if c.type == trunk_key or c.type.startswith(trunk_key):
            return c
    return None


def _vstubs_for_block(candidate, block_name):
    """Return (seg_idx, Segment) for every vertical segment connecting to block_name."""
    result = []
    for i, seg in enumerate(candidate.segments):
        if seg.start.x != seg.end.x:  # not vertical
            continue
        bt_pair = candidate.seg_busterms.get(i, (None, None))
        if any(bt is not None and bt.block_name == block_name for bt in bt_pair):
            result.append((i, seg))
    return result


def _bridge_for_block(candidate, block_name):
    """Return the bridge Segment for block_name, or None."""
    return candidate.bridge_segments.get(block_name)


# ---------------------------------------------------------------------------
# Then — thru-the-block assertions
# ---------------------------------------------------------------------------

@then(parsers.re(
    r'in the (?P<trunk_key>\S+) candidate '
    r'"(?P<block>[^"]+)" has exactly 1 V stub connecting to its lower rect'
))
def then_one_vstub_lower(ctx, trunk_key, block):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    rects = ctx['fp'].get_block_rects(block)
    lower_face_y = min(r[3] for r in rects)  # top face of lowest rect
    stubs = _vstubs_for_block(c, block)
    lower_stubs = [
        (i, s) for i, s in stubs
        if s.start.y == lower_face_y or s.end.y == lower_face_y
    ]
    assert len(lower_stubs) == 1, (
        f'Expected 1 lower-rect V stub for {block!r} in {trunk_key!r}, '
        f'got {len(lower_stubs)} (total stubs={len(stubs)}, lower_face_y={lower_face_y})'
    )


@then(parsers.re(
    r'the V stub from "(?P<block>[^"]+)" '
    r'in the (?P<trunk_key>\S+) candidate has length (?P<length>\d+)'
))
def then_vstub_length(ctx, block, trunk_key, length):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    stubs = _vstubs_for_block(c, block)
    assert stubs, f'No V stub for {block!r} in {trunk_key!r}'
    _, seg = stubs[0]
    stub_len = abs(seg.start.y - seg.end.y)
    assert stub_len == int(length), f'Stub length: expected {length}, got {stub_len}'


@then(parsers.re(
    r'the (?P<trunk_key>\S+) candidate has no bridge segment for "(?P<block>[^"]+)"'
))
def then_no_bridge(ctx, trunk_key, block):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    assert _bridge_for_block(c, block) is None, \
        f'Expected no bridge for {block!r} in {trunk_key!r}'


@then(parsers.re(
    r'"(?P<block>[^"]+)"\'s upper rect is not connected in the (?P<trunk_key>\S+) candidate'
))
def then_upper_rect_not_connected(ctx, block, trunk_key):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    rects = ctx['fp'].get_block_rects(block)
    upper_rect = max(rects, key=lambda r: r[1])  # max y1
    upper_faces = {upper_rect[1], upper_rect[3]}
    for i, seg in enumerate(c.segments):
        bt_pair = c.seg_busterms.get(i, (None, None))
        for bt in bt_pair:
            if bt is not None and bt.block_name == block:
                for y in (seg.start.y, seg.end.y):
                    if y in upper_faces:
                        pytest.fail(
                            f'Upper rect of {block!r} is connected at y={y} '
                            f'in {trunk_key!r} (upper_faces={upper_faces})'
                        )


@then(parsers.re(
    r'in the (?P<trunk_key>\S+) candidate '
    r'"(?P<block>[^"]+)" has no V stub \(Direct connection via lower rect\)'
))
def then_no_vstub_direct(ctx, trunk_key, block):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    stubs = _vstubs_for_block(c, block)
    assert len(stubs) == 0, \
        f'Expected no V stub for {block!r} in {trunk_key!r}, got {len(stubs)}'


# ---------------------------------------------------------------------------
# Then — over-the-block assertions
# ---------------------------------------------------------------------------

@then(parsers.re(
    r'in the (?P<trunk_key>\S+) candidate '
    r'"(?P<block>[^"]+)" has 2 V stubs \(one to each rect\)'
))
def then_two_vstubs(ctx, trunk_key, block):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    stubs = _vstubs_for_block(c, block)
    assert len(stubs) == 2, \
        f'Expected 2 V stubs for {block!r} in {trunk_key!r}, got {len(stubs)}'


@then(parsers.re(
    r'the V stub down from "(?P<block>[^"]+)" in (?P<trunk_key>\S+) has length (?P<length>\d+)'
))
def then_vstub_down_length(ctx, block, trunk_key, length):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    rects = ctx['fp'].get_block_rects(block)
    lower_face_y = min(r[3] for r in rects)  # top face of lowest rect
    stubs = _vstubs_for_block(c, block)
    lower = [(i, s) for i, s in stubs
             if s.start.y == lower_face_y or s.end.y == lower_face_y]
    assert lower, f'No downward V stub (touching y={lower_face_y}) for {block!r} in {trunk_key!r}'
    _, seg = lower[0]
    stub_len = abs(seg.start.y - seg.end.y)
    assert stub_len == int(length), f'Down stub length: expected {length}, got {stub_len}'


@then(parsers.re(
    r'the V stub up\s+from "(?P<block>[^"]+)" in (?P<trunk_key>\S+) has length (?P<length>\d+)'
))
def then_vstub_up_length(ctx, block, trunk_key, length):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    rects = ctx['fp'].get_block_rects(block)
    upper_face_y = max(r[1] for r in rects)  # bottom face of topmost rect
    stubs = _vstubs_for_block(c, block)
    upper = [(i, s) for i, s in stubs
             if s.start.y == upper_face_y or s.end.y == upper_face_y]
    assert upper, f'No upward V stub (touching y={upper_face_y}) for {block!r} in {trunk_key!r}'
    _, seg = upper[0]
    stub_len = abs(seg.start.y - seg.end.y)
    assert stub_len == int(length), f'Up stub length: expected {length}, got {stub_len}'


@then(parsers.re(
    r'the (?P<trunk_key>\S+) candidate has a bridge segment for "(?P<block>[^"]+)" at y=(?P<y>\d+)'
))
def then_bridge_at_y(ctx, trunk_key, block, y):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    bridge = _bridge_for_block(c, block)
    assert bridge is not None, f'No bridge for {block!r} in {trunk_key!r}'
    bridge_y = bridge.start.y  # bridge is horizontal; both endpoints at same y
    assert bridge_y == int(y), f'Bridge y: expected {y}, got {bridge_y}'


@then(parsers.parse(
    "the bridge segment spans from B's leftmost rect face to B's rightmost rect face"
))
def then_bridge_spans_rects(ctx):
    c = _find_by_trunk(ctx['candidates'], 'TRUNK_H@y200')
    assert c is not None, 'TRUNK_H@y200 not found'
    bridge = _bridge_for_block(c, 'B')
    assert bridge is not None, 'No bridge for B'
    rects = ctx['fp'].get_block_rects('B')
    expected_lo = min(r[0] for r in rects)   # min x1 across all rects
    expected_hi = max(r[2] for r in rects)   # max x2 across all rects
    bridge_lo = min(bridge.start.x, bridge.end.x)
    bridge_hi = max(bridge.start.x, bridge.end.x)
    assert bridge_lo == expected_lo, f'Bridge lo x: expected {expected_lo}, got {bridge_lo}'
    assert bridge_hi == expected_hi, f'Bridge hi x: expected {expected_hi}, got {bridge_hi}'


@then(parsers.re(
    r'the (?P<trunk_key>\S+) candidate has a bridge segment for "(?P<block>[^"]+)"$'
))
def then_has_bridge(ctx, trunk_key, block):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    assert _bridge_for_block(c, block) is not None, \
        f'No bridge for {block!r} in {trunk_key!r}'


@then(parsers.re(
    r'the bridge segment runs along the top face of "(?P<block>[^"]+)"\'s union bounding box'
))
def then_bridge_along_top(ctx, block):
    c = _find_by_trunk(ctx['candidates'], 'TRUNK_V@x250')
    assert c is not None, 'TRUNK_V@x300 not found'
    bridge = _bridge_for_block(c, block)
    assert bridge is not None, f'No bridge for {block!r}'
    rects = ctx['fp'].get_block_rects(block)
    union_y2 = max(r[3] for r in rects)
    assert bridge.start.y == union_y2, \
        f'Bridge y: expected top of union bbox ({union_y2}), got {bridge.start.y}'


@then(parsers.re(
    r'in the (?P<trunk_key>\S+) candidate "(?P<block>[^"]+)" has no bridge segment'
))
def then_no_bridge_inline(ctx, trunk_key, block):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    assert _bridge_for_block(c, block) is None, \
        f'Expected no bridge for {block!r} in {trunk_key!r}'


@then(parsers.re(
    r'"(?P<block>[^"]+)" has a Direct connection in the (?P<trunk_key>\S+) candidate'
))
def then_direct_connection(ctx, block, trunk_key):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    # Direct = block's busterm appears on a horizontal (trunk) segment, not a V stub
    for i, seg in enumerate(c.segments):
        if seg.start.y != seg.end.y:  # skip vertical stubs
            continue
        bt_pair = c.seg_busterms.get(i, (None, None))
        if any(bt is not None and bt.block_name == block for bt in bt_pair):
            return
    pytest.fail(f'{block!r} has no Direct connection on a horizontal segment in {trunk_key!r}')


@then(parsers.re(
    r'in (?P<trunk_key>\S+) "(?P<b1>[^"]+)" has a bridge segment and "(?P<b2>[^"]+)" does not'
))
def then_b1_has_bridge_b2_does_not(ctx, trunk_key, b1, b2):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    assert _bridge_for_block(c, b1) is not None, \
        f'{b1!r} has no bridge in {trunk_key!r}'
    assert _bridge_for_block(c, b2) is None, \
        f'{b2!r} should not have a bridge in {trunk_key!r}'


# ---------------------------------------------------------------------------
# Then — ranking assertion (scenario 8, xfail)
# ---------------------------------------------------------------------------

@then('the thru-the-block candidate ranks before the over-the-block candidate')
def then_thru_ranks_before_over(ctx):
    ranked = ctx.get('ranked_candidates', [])
    assert len(ranked) >= 2, 'Need at least 2 ranked candidates'
    teg_modes = [getattr(c, 'teg_mode', None) for c in ranked]
    thru_idx = next((i for i, m in enumerate(teg_modes) if m == 'thru'), None)
    over_idx  = next((i for i, m in enumerate(teg_modes) if m == 'over'), None)
    assert thru_idx is not None and over_idx is not None, \
        f'Expected both thru and over candidates; got modes: {teg_modes}'
    assert thru_idx < over_idx, \
        f'thru (idx {thru_idx}) must rank before over (idx {over_idx})'
