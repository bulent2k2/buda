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
pytest-bdd step definitions for features/busterm_over_the_block.feature.

Over-the-block vs thru-the-block TEG (Terminal Equivalence Group) routing modes.

Since the bridge-emission redesign (teg_multirect_status.md open 1(a)) the
OVER connection metal is ORDINARY topology segments — gap stubs joined through
the trunk, and perpendicular connector legs for rectilinear blocks — so these
scenarios assert real stub/leg geometry and the ABSENCE of the former
union-face bridge (Topology.bridge_segments stays empty at generation; it
survives only on candidates restored from pre-change checkpoints).

All 9 scenarios test currently-implemented behaviour and must PASS.
Scenario 9 (thru-before-over ranking) asserts the MEASURED post-emission
  property: teg_mode is a property of the BLOCK (one pool cannot mix modes),
  and the OVER connection metal is real segments priced in
  estimated_wirelength — so the same-locus over twin carries strictly more
  WL than its thru twin, and the extra metal is real segments.  Pools are
  WL-sorted, so that is what "thru ranks before over, all else equal"
  means; a cross-pool ORDINAL comparison is deliberately not asserted —
  the two pools are different candidate populations, so an ordinal would
  be confounded by the other OVER-affected members.  (The scenario was
  xfail while it asserted a phantom `Topology.adjusted_wl` / per-topology
  teg_mode API; post-emission no separate "adjusted" figure exists —
  priced metal IS built metal.)
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
          'Over-the-block — trunk in gap stubs both rects, joined through the trunk')
def test_overtheblock__trunk_in_gap_stubs_both_rects_joined_through_trunk():
    pass


@scenario('features/busterm_over_the_block.feature',
          'Over-the-block — trunk inside one disjoint rect stubs the other rect')
def test_overtheblock__trunk_inside_one_disjoint_rect_stubs_the_other_rect():
    pass


@scenario('features/busterm_over_the_block.feature',
          'Over-the-block — L-shaped block with V trunk beside notch')
def test_overtheblock__lshaped_block_with_v_trunk_beside_notch():
    pass


@scenario('features/busterm_over_the_block.feature',
          'Over-the-block — V trunk with horizontal gap (pure TEG, stubs joined through the trunk)')
def test_overtheblock__v_trunk_horizontal_gap_stubs_joined_through_trunk():
    pass


@scenario('features/busterm_over_the_block.feature',
          'Over-the-block — bridge is omitted when rects are adjacent (no gap)')
def test_overtheblock__bridge_is_omitted_when_rects_are_adjacent_no_gap():
    pass


@scenario('features/busterm_over_the_block.feature',
          'Global teg_mode overridden per block')
def test_global_teg_mode_overridden_per_block():
    pass


@scenario('features/busterm_over_the_block.feature',
          'Over-the-block connection metal is real priced wirelength, '
          'so over ranks after thru')
def test_overtheblock_connection_metal_ranks_after_thru():
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


# ---------------------------------------------------------------------------
# When — two pools on the same geometry, one per teg mode (scenario 9)
# ---------------------------------------------------------------------------

@when(parsers.re(
    r'I generate candidate pools from "(?P<src>[^"]+)" for block "(?P<name>[^"]+)" '
    r'with rects \((?P<rx1>\d+),(?P<ry1>\d+)\)-\((?P<rx2>\d+),(?P<ry2>\d+)\) and '
    r'\((?P<sx1>\d+),(?P<sy1>\d+)\)-\((?P<sx2>\d+),(?P<sy2>\d+)\) under both teg modes'
))
def when_generate_both_mode_pools(ctx, src, name, rx1, ry1, rx2, ry2,
                                  sx1, sy1, sx2, sy2):
    # teg_mode is a property of the BLOCK, so one pool cannot hold both
    # modes: build the pool twice on the same geometry, once per mode.
    src_bb = ctx['fp'].get_block_bounds(src)
    rects = [(int(rx1), int(ry1), int(rx2), int(ry2)),
             (int(sx1), int(sy1), int(sx2), int(sy2))]
    pools = {}
    for mode_name, mode in (('thru', buda.TegMode.THRU),
                            ('over', buda.TegMode.OVER)):
        fp = buda.Floorplan()
        fp.add_block(src, src_bb.x1, src_bb.y1, src_bb.x2, src_bb.y2)
        fp.add_block_rects(name, rects, teg_mode=mode)
        g = buda.TopologyGenerator(fp)
        g.set_layer_ids(ctx['layer_h'], ctx['layer_v'])
        pools[mode_name] = g.generate_candidates(src, [name])
    ctx['mode_pools'] = pools


def _locus_candidate(pool, trunk_key):
    """Return (1-based rank, candidate) of the first trunk_key match."""
    for i, c in enumerate(pool):
        if c.type == trunk_key or c.type.startswith(trunk_key):
            return i + 1, c
    return None, None


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
    r'both V stubs of "(?P<block>[^"]+)" in the (?P<trunk_key>\S+) candidate '
    r'end on the trunk at y=(?P<y>\d+)'
))
def then_vstubs_end_on_trunk(ctx, block, trunk_key, y):
    # The connectivity claim of the redesign: each gap stub T-junctions the
    # spine, so the OVER block's rects are joined THROUGH the trunk by real
    # metal (no side-map bridge involved).
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    stubs = _vstubs_for_block(c, block)
    assert len(stubs) == 2, f'Expected 2 V stubs, got {len(stubs)}'
    for i, seg in stubs:
        assert int(y) in (seg.start.y, seg.end.y), (
            f'V stub seg {i} of {block!r} does not reach the trunk at y={y}: '
            f'({seg.start.x},{seg.start.y})-({seg.end.x},{seg.end.y})'
        )


def _hstubs_for_block(candidate, block_name):
    """Return (seg_idx, Segment) for every horizontal segment tapping block_name."""
    result = []
    for i, seg in enumerate(candidate.segments):
        if seg.start.y != seg.end.y:  # not horizontal
            continue
        bt_pair = candidate.seg_busterms.get(i, (None, None))
        if any(bt is not None and bt.block_name == block_name for bt in bt_pair):
            result.append((i, seg))
    return result


@then(parsers.re(
    r'the (?P<trunk_key>\S+) candidate has an H connector leg tapping '
    r'"(?P<block>[^"]+)" from x=(?P<x_face>\d+) to the trunk at x=(?P<x_trunk>\d+) '
    r'at y=(?P<y>\d+)'
))
def then_h_connector_leg(ctx, trunk_key, block, x_face, x_trunk, y):
    # The rectilinear-branch redesign: the un-spanned rect is reached by a REAL
    # perpendicular leg — endpoint on the rect's face, other endpoint
    # T-junctioning the spine — instead of the former floating union-face
    # bridge (teg_multirect_status.md §1.3).
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    legs = _hstubs_for_block(c, block)
    want = {int(x_face), int(x_trunk)}
    for i, seg in legs:
        if seg.start.y == int(y) and {seg.start.x, seg.end.x} == want:
            return
    pytest.fail(
        f'No H connector leg for {block!r} in {trunk_key!r} with '
        f'x endpoints {sorted(want)} at y={y}; taps found: '
        f'{[((s.start.x, s.start.y), (s.end.x, s.end.y)) for _, s in legs]}'
    )


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
    r'in the (?P<trunk_key>\S+) candidate '
    r'"(?P<block>[^"]+)" has 2 H stubs \(one to each rect\)'
))
def then_two_hstubs(ctx, trunk_key, block):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    stubs = _hstubs_for_block(c, block)
    assert len(stubs) == 2, \
        f'Expected 2 H stubs for {block!r} in {trunk_key!r}, got {len(stubs)}'


@then(parsers.re(
    r'both H stubs of "(?P<block>[^"]+)" in the (?P<trunk_key>\S+) candidate '
    r'end on the trunk at x=(?P<x>\d+)'
))
def then_hstubs_end_on_trunk(ctx, block, trunk_key, x):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    stubs = _hstubs_for_block(c, block)
    assert len(stubs) == 2, f'Expected 2 H stubs, got {len(stubs)}'
    for i, seg in stubs:
        assert int(x) in (seg.start.x, seg.end.x), (
            f'H stub seg {i} of {block!r} does not reach the trunk at x={x}: '
            f'({seg.start.x},{seg.start.y})-({seg.end.x},{seg.end.y})'
        )


@then(parsers.re(
    r'in the (?P<trunk_key>\S+) candidate "(?P<block>[^"]+)" has exactly 1 V stub$'
))
def then_exactly_one_vstub(ctx, trunk_key, block):
    c = _find_by_trunk(ctx['candidates'], trunk_key)
    assert c is not None, f'Candidate {trunk_key!r} not found'
    stubs = _vstubs_for_block(c, block)
    assert len(stubs) == 1, \
        f'Expected exactly 1 V stub for {block!r} in {trunk_key!r}, got {len(stubs)}'


# ---------------------------------------------------------------------------
# Then — measured ranking assertions (scenario 9)
# ---------------------------------------------------------------------------

def _mode_twins(ctx, trunk_key):
    _, thru_c = _locus_candidate(ctx['mode_pools']['thru'], trunk_key)
    _, over_c = _locus_candidate(ctx['mode_pools']['over'], trunk_key)
    assert thru_c is not None, f'{trunk_key!r} missing from thru pool'
    assert over_c is not None, f'{trunk_key!r} missing from over pool'
    return thru_c, over_c


@then(parsers.re(
    r'the same-locus (?P<trunk_key>\S+) candidate has strictly higher '
    r'estimated wirelength under over than under thru'
))
def then_over_twin_wl_higher(ctx, trunk_key):
    # The connection metal (both gap stubs to the trunk here) is ordinary
    # segments, so it shows up as real estimated_wirelength — the property
    # the retired "adjusted_wl" concept was a placeholder for.  Pools are
    # WL-sorted, so this is what makes the over twin sort later among
    # otherwise-equal competitors; a cross-pool ordinal is NOT compared
    # (the two pools are different populations — see the feature comment).
    thru_c, over_c = _mode_twins(ctx, trunk_key)
    assert over_c.estimated_wirelength > thru_c.estimated_wirelength, (
        f'over twin must carry MORE priced metal: '
        f'thru wl={thru_c.estimated_wirelength}, over wl={over_c.estimated_wirelength}'
    )


@then(parsers.re(
    r'the over twin of (?P<trunk_key>\S+) carries its extra wirelength '
    r'as real segments'
))
def then_over_twin_extra_metal_is_segments(ctx, trunk_key):
    # The extra WL is not an annotation on the side (the retired bridge was
    # exactly that) — it is more Topology.segments, which is what the
    # planner routes and the audits walk.
    thru_c, over_c = _mode_twins(ctx, trunk_key)
    assert len(over_c.segments) > len(thru_c.segments), (
        f'the extra metal must be real segments: '
        f'thru segs={len(thru_c.segments)}, over segs={len(over_c.segments)}'
    )
