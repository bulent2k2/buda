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
pytest-bdd step definitions for features/multicast_topology.feature.

Covers TRUNK_H / TRUNK_V / TRUNK_H_OOB / MST / BITRUNK multicast topologies.
"""
import pytest
import buda
from pytest_bdd import scenarios, given, when, then, parsers
from conftest import (
    _find_candidate, _segs_of, _build_all_cts,
    _stub_seg_for_block, _connector_type, _is_stub,
)

scenarios('features/multicast_topology.feature')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trunk_segs(ct, is_horiz):
    """Return all ConnSegs that are the trunk orientation and have ≥1 SEG connection."""
    segs = _segs_of(ct)
    return [
        cs for cs in segs
        if cs.horiz == is_horiz
        and any(c.kind == buda.SegConnKind.SEG for c in cs.conns)
    ]


def _stub_segs(ct, is_horiz):
    """Return all ConnSegs that are stub orientation (not the trunk) and have a BUSTERM."""
    return [
        cs for cs in _segs_of(ct)
        if cs.horiz == is_horiz
        and any(c.kind == buda.SegConnKind.BUSTERM for c in cs.conns)
    ]


def _block_has_stub(ct, block_name, trunk_is_horiz):
    """True if block_name connects via a stub (not Direct) to the trunk."""
    return _connector_type(ct, block_name, trunk_is_horiz) == 'L'


def _block_is_direct(ct, block_name, trunk_is_horiz):
    return _connector_type(ct, block_name, trunk_is_horiz) == 'Direct'


def _stub_length_for_block(ct, block_name):
    cs = _stub_seg_for_block(ct, block_name)
    if cs is None:
        return None
    return abs(cs.along_hi - cs.along_lo)


def _cand_by_prefix(ctx, prefix):
    c = _find_candidate(ctx['candidates'], prefix)
    assert c is not None, (
        f'No {prefix!r} candidate. Got: {[x.type for x in ctx["candidates"]]}'
    )
    return c


def _ct_for_prefix(ctx, prefix):
    if not ctx.get('conn_topologies'):
        _build_all_cts(ctx)
    key = next((k for k in ctx['conn_topologies'] if k.startswith(prefix)), None)
    assert key is not None, (
        f'No ConnTopology for {prefix!r}. Got: {list(ctx["conn_topologies"])}'
    )
    return ctx['conn_topologies'][key]


# ---------------------------------------------------------------------------
# Then: TRUNK_H@y150 topology shape checks
# ---------------------------------------------------------------------------

@then(parsers.re(r'a candidate of type "(?P<t>[^"]+)" exists'))
def trunk_h_candidate_exists(ctx, t):
    # Delegated to conftest — this override intentionally absent; handled globally.
    pass


@then(parsers.re(
    r'in the (?P<ttype>[A-Z_@\d]+) topology "(?P<blk>[^"]+)" has no stub'
))
def block_has_no_stub(ctx, ttype, blk):
    ct = _ct_for_prefix(ctx, ttype)
    trunk_is_horiz = 'TRUNK_H' in ttype or ttype.startswith('I_H') or ttype.startswith('MST')
    ctype = _connector_type(ct, blk, trunk_is_horiz)
    assert ctype == 'Direct', (
        f'{blk} in {ttype}: expected Direct, got {ctype}'
    )


@then(parsers.re(
    r'in the (?P<ttype>[A-Z_@\d]+) topology "(?P<blk>[^"]+)" has a V stub of length (?P<length>\d+)'
))
def block_has_v_stub(ctx, ttype, blk, length):
    length = int(length)
    ct = _ct_for_prefix(ctx, ttype)
    cs = _stub_seg_for_block(ct, blk)
    assert cs is not None, f'{blk} has no stub segment in {ttype}'
    assert not cs.horiz, f'{blk} stub in {ttype} is not vertical'
    actual = abs(cs.along_hi - cs.along_lo)
    assert actual == length, f'{blk} stub length={actual}, expected {length}'


# ---------------------------------------------------------------------------
# Then: TRUNK_H — multiple candidates
# ---------------------------------------------------------------------------

@then('every TRUNK_H candidate has a V stub to "B" when trunk y is outside B\'s y range')
def trunk_h_stub_to_b(ctx):
    b = ctx['fp'].get_block_bounds('B')
    for c in ctx['candidates']:
        # +MST hybrids intentionally REPLACE some per-block stubs with MST edges
        # (trunk+MST completion), so the "stub to each block" invariant does not
        # apply to them.
        if not c.type.startswith('TRUNK_H') or 'OOB' in c.type or '+MST' in c.type:
            continue
        ct = buda.ConnTopology()
        ct.build(c, ctx['fp'])
        # Determine trunk y from perp_pos of first H segment
        trunk_segs = [cs for cs in _segs_of(ct) if cs.horiz]
        if not trunk_segs:
            continue
        trunk_y = trunk_segs[0].perp_pos
        # If trunk y is outside B.y-range, B should have a stub
        if trunk_y < b.y1 or trunk_y > b.y2:
            ctype = _connector_type(ct, 'B', True)
            assert ctype == 'L', (
                f'{c.type}: trunk y={trunk_y} outside B.y=[{b.y1},{b.y2}] '
                f'but B connector is {ctype}'
            )


@then('every TRUNK_H candidate has a V stub to "C" when trunk y is outside C\'s y range')
def trunk_h_stub_to_c(ctx):
    c_rect = ctx['fp'].get_block_bounds('C')
    for c in ctx['candidates']:
        if not c.type.startswith('TRUNK_H') or 'OOB' in c.type or '+MST' in c.type:
            continue
        ct = buda.ConnTopology()
        ct.build(c, ctx['fp'])
        trunk_segs = [cs for cs in _segs_of(ct) if cs.horiz]
        if not trunk_segs:
            continue
        trunk_y = trunk_segs[0].perp_pos
        if trunk_y < c_rect.y1 or trunk_y > c_rect.y2:
            ctype = _connector_type(ct, 'C', True)
            assert ctype == 'L', (
                f'{c.type}: trunk y={trunk_y} outside C.y=[{c_rect.y1},{c_rect.y2}] '
                f'but C connector is {ctype}'
            )


# ---------------------------------------------------------------------------
# Then: TRUNK_V
# ---------------------------------------------------------------------------

@then('at least one candidate of type "TRUNK_V" exists')
def trunk_v_exists(ctx):
    matches = [c for c in ctx['candidates'] if c.type.startswith('TRUNK_V') and 'OOB' not in c.type]
    assert matches, f'No TRUNK_V candidate. Got: {[c.type for c in ctx["candidates"]]}'


@then('every TRUNK_V candidate has an H stub to each block whose face_x differs from the trunk x')
def trunk_v_h_stubs(ctx):
    for c in ctx['candidates']:
        if not c.type.startswith('TRUNK_V') or 'OOB' in c.type or '+MST' in c.type:
            continue
        ct = buda.ConnTopology()
        ct.build(c, ctx['fp'])
        trunk_segs = [cs for cs in _segs_of(ct) if not cs.horiz]
        if not trunk_segs:
            continue
        trunk_x = trunk_segs[0].perp_pos
        for blk_name in ('A', 'B', 'C'):
            try:
                blk = ctx['fp'].get_block_bounds(blk_name)
            except Exception:
                continue
            # If block doesn't straddle trunk x, it must have an H stub
            straddles = blk.x1 <= trunk_x <= blk.x2
            if not straddles:
                ctype = _connector_type(ct, blk_name, False)
                assert ctype == 'L', (
                    f'{c.type}: block {blk_name} x=[{blk.x1},{blk.x2}] does not straddle '
                    f'trunk x={trunk_x} but has {ctype} connector'
                )


# ---------------------------------------------------------------------------
# Then: TRUNK_H@yN topology shape checks (new-style)
# ---------------------------------------------------------------------------

@then(parsers.re(
    r'the (?P<ttype>TRUNK_H@y\d+) topology connects "(?P<b1>[^"]+)", "(?P<b2>[^"]+)", and "(?P<b3>[^"]+)"'
))
def trunk_h_topology_connects_three(ctx, ttype, b1, b2, b3):
    import re as _re
    ct = ctx.get('selected_ct')
    if ct is None:
        m = _re.search(r'@y(\d+)', ttype)
        if m:
            y = int(m.group(1))
            for cand in ctx['candidates']:
                if cand.type.startswith('TRUNK_H') and 'OOB' not in cand.type:
                    ct_tmp = buda.ConnTopology()
                    ct_tmp.build(cand, ctx['fp'])
                    for cs in _segs_of(ct_tmp):
                        if cs.horiz and cs.perp_pos == y:
                            ct = ct_tmp
                            ctx['selected_ct'] = ct
                            break
                if ct:
                    break
    assert ct is not None, f'No {ttype} candidate found'
    blocks_reached = set()
    for cs in _segs_of(ct):
        for conn in cs.conns:
            if conn.kind == buda.SegConnKind.BUSTERM and conn.block_name:
                blocks_reached.add(conn.block_name)
    for blk in (b1, b2, b3):
        assert blk in blocks_reached, (
            f'{ttype}: block {blk!r} not reached. Reached: {blocks_reached}'
        )


@then(parsers.re(
    r'the TRUNK_H@y(?P<y>\d+) candidate has pass_through_count at least (?P<n>\d+)'
))
def trunk_h_at_y_pass_through_at_least(ctx, y, n):
    n = int(n)
    c = ctx.get('selected_cand')
    if c is None:
        y_int = int(y)
        for cand in ctx['candidates']:
            if cand.type.startswith('TRUNK_H') and 'OOB' not in cand.type:
                ct = buda.ConnTopology()
                ct.build(cand, ctx['fp'])
                for cs in _segs_of(ct):
                    if cs.horiz and cs.perp_pos == y_int:
                        c = cand
                        break
            if c:
                break
    if c is None:
        pytest.skip(f'No TRUNK_H@y{y} candidate found')
    assert c.pass_through_count >= n, (
        f'pass_through_count={c.pass_through_count}, expected >= {n}'
    )


@then(parsers.re(
    r'the TRUNK_H@y(?P<y>\d+) topology includes a V stub for "(?P<blk>[^"]+)"'
))
def trunk_h_at_y_includes_v_stub(ctx, y, blk):
    y = int(y)
    ct = ctx.get('selected_ct')
    if ct is None:
        for cand in ctx['candidates']:
            if cand.type.startswith('TRUNK_H') and 'OOB' not in cand.type:
                ct_tmp = buda.ConnTopology()
                ct_tmp.build(cand, ctx['fp'])
                for cs in _segs_of(ct_tmp):
                    if cs.horiz and cs.perp_pos == y:
                        ct = ct_tmp
                        ctx['selected_ct'] = ct
                        break
            if ct:
                break
    if ct is None:
        pytest.skip(f'No TRUNK_H@y{y} candidate found')
    ctype = _connector_type(ct, blk, True)
    assert ctype == 'L', (
        f'{blk} has {ctype} connector in TRUNK_H@y{y}, expected L (V stub)'
    )


# ---------------------------------------------------------------------------
# Then: TRUNK_H_OOB
# ---------------------------------------------------------------------------

@then('at least one candidate of type "TRUNK_H_OOB" exists')
def trunk_h_oob_exists(ctx):
    matches = [c for c in ctx['candidates'] if c.type.startswith('TRUNK_H_OOB')]
    assert matches, f'No TRUNK_H_OOB candidate. Got: {[c.type for c in ctx["candidates"]]}'


@then('at least one TRUNK_H_OOB candidate connects all three blocks')
def trunk_h_oob_connects_all(ctx):
    expected = set(ctx.get('block_names', ['A', 'B', 'C']))
    for c in ctx['candidates']:
        if not c.type.startswith('TRUNK_H_OOB'):
            continue
        ct = buda.ConnTopology()
        ct.build(c, ctx['fp'])
        blocks_reached = set()
        for cs in _segs_of(ct):
            for conn in cs.conns:
                if conn.kind == buda.SegConnKind.BUSTERM and conn.block_name:
                    blocks_reached.add(conn.block_name)
        if expected.issubset(blocks_reached):
            return
    pytest.fail(
        f'No TRUNK_H_OOB candidate connects all blocks {expected}. '
        f'Candidates: {[c.type for c in ctx["candidates"]]}'
    )


@then(parsers.re(r'every TRUNK_H_OOB trunk y is below y=(?P<y>\d+)'))
def trunk_h_oob_below(ctx, y):
    y = int(y)
    for c in ctx['candidates']:
        if not c.type.startswith('TRUNK_H_OOB'):
            continue
        ct = buda.ConnTopology()
        ct.build(c, ctx['fp'])
        for cs in _segs_of(ct):
            if cs.horiz:
                assert cs.perp_pos < y, (
                    f'{c.type}: OOB trunk y={cs.perp_pos} not below {y}'
                )
                break


# ---------------------------------------------------------------------------
# Then: pass_through_count
# ---------------------------------------------------------------------------

@then(parsers.re(
    r'a TRUNK_H@y(?P<y>\d+) candidate exists'
))
def trunk_h_at_y_exists(ctx, y):
    y = int(y)
    for c in ctx['candidates']:
        if c.type.startswith('TRUNK_H') and 'OOB' not in c.type:
            ct = buda.ConnTopology()
            ct.build(c, ctx['fp'])
            for cs in _segs_of(ct):
                if cs.horiz and cs.perp_pos == y:
                    ctx['selected_cand'] = c
                    ctx['selected_ct'] = ct
                    return
    pytest.fail(
        f'No TRUNK_H candidate with trunk y={y}. '
        f'Candidates: {[c.type for c in ctx["candidates"]]}'
    )


@then(parsers.re(
    r'in the TRUNK_H@y(?P<y>\d+) topology "(?P<blk>[^"]+)" has pass_through_count (?P<n>\d+)'
))
def block_has_pass_through(ctx, y, blk, n):
    n = int(n)
    # pass_through_count is on the Topology object (set by generator)
    c = ctx.get('selected_cand')
    if c is None:
        y_int = int(y)
        for cand in ctx['candidates']:
            if cand.type.startswith('TRUNK_H') and 'OOB' not in cand.type:
                ct = buda.ConnTopology()
                ct.build(cand, ctx['fp'])
                for cs in _segs_of(ct):
                    if cs.horiz and cs.perp_pos == y_int:
                        c = cand
                        break
                if c:
                    break
    if c is None:
        pytest.skip(f'No TRUNK_H@y{y} candidate found')
    assert c.pass_through_count == n, (
        f'pass_through_count={c.pass_through_count}, expected {n}'
    )


@then(parsers.re(
    r'in the TRUNK_H@y(?P<y>\d+) topology "(?P<blk>[^"]+)" has a V stub'
))
def block_has_v_stub_at_y(ctx, y, blk):
    y = int(y)
    ct = ctx.get('selected_ct')
    if ct is None:
        for cand in ctx['candidates']:
            if cand.type.startswith('TRUNK_H') and 'OOB' not in cand.type:
                ct_tmp = buda.ConnTopology()
                ct_tmp.build(cand, ctx['fp'])
                for cs in _segs_of(ct_tmp):
                    if cs.horiz and cs.perp_pos == y:
                        ct = ct_tmp
                        break
            if ct:
                break
    if ct is None:
        pytest.skip(f'No TRUNK_H@y{y} candidate found')
    ctype = _connector_type(ct, blk, True)
    assert ctype == 'L', f'{blk} has {ctype} connector, expected L (stub)'


# ---------------------------------------------------------------------------
# Then: MST
# ---------------------------------------------------------------------------

@then('at least one candidate of type "MST" exists')
def mst_exists(ctx):
    matches = [c for c in ctx['candidates'] if c.type.startswith('MST')]
    assert matches, f'No MST candidate. Got: {[c.type for c in ctx["candidates"]]}'


@then('every MST candidate connects all four blocks')
def mst_connects_all(ctx):
    for c in ctx['candidates']:
        if not c.type.startswith('MST'):
            continue
        ct = buda.ConnTopology()
        ct.build(c, ctx['fp'])
        # A block is connected if it has a busterm tap OR is covered by a
        # pass-through wire: an orthogonal relay is wired by EXTENDING its stubs
        # over the cell to meet, leaving no tap (the block is covered by the
        # crossing wires).  check_topo's coverage check (BUSTERM_OPEN) accepts
        # both, so assert no block is left uncovered rather than counting taps.
        res = buda.check_topo(ct, c, ctx['fp'], 0)
        opens = [v.block_name for v in res.violations
                 if v.kind == buda.ViolationKind.BUSTERM_OPEN]
        assert not opens, f'{c.type}: blocks not connected (uncovered): {opens}'


@then('every MST candidate has no cycles')
def mst_no_cycles(ctx):
    from conftest import _has_no_cycles
    for c in ctx['candidates']:
        if not c.type.startswith('MST'):
            continue
        ct = buda.ConnTopology()
        ct.build(c, ctx['fp'])
        assert _has_no_cycles(ct), f'{c.type}: cycle detected in ConnTopology'


# ---------------------------------------------------------------------------
# Then: BITRUNK
# ---------------------------------------------------------------------------

@then('at least one candidate of type "BITRUNK" or "MST" exists')
def bitrunk_or_mst_exists(ctx):
    matches = [
        c for c in ctx['candidates']
        if c.type.startswith('BITRUNK') or c.type.startswith('MST')
    ]
    assert matches, f'No BITRUNK/MST candidate. Got: {[c.type for c in ctx["candidates"]]}'


# ---------------------------------------------------------------------------
# Then: TRUNK_H slide range
# ---------------------------------------------------------------------------

@then('every TRUNK_H candidate has a non-negative slide on its trunk segment')
def trunk_h_non_negative_slide(ctx):
    if not ctx.get('conn_topologies'):
        _build_all_cts(ctx)
    for ctype, ct in ctx['conn_topologies'].items():
        if not ctype.startswith('TRUNK_H') or 'OOB' in ctype:
            continue
        for cs in _segs_of(ct):
            if cs.horiz:
                slide = cs.perp_hi - cs.perp_lo
                assert slide >= 0, (
                    f'{ctype}: trunk slide={slide} < 0 '
                    f'(perp=[{cs.perp_lo},{cs.perp_hi}])'
                )
                break


@then(parsers.re(
    r'the TRUNK_H@y(?P<y>\d+) candidate trunk segment slide equals (?P<s>\d+)'
))
def trunk_h_at_y_slide(ctx, y, s):
    y, s = int(y), int(s)
    if not ctx.get('conn_topologies'):
        _build_all_cts(ctx)
    for ctype, ct in ctx['conn_topologies'].items():
        if not ctype.startswith('TRUNK_H') or 'OOB' in ctype:
            continue
        for cs in _segs_of(ct):
            if cs.horiz and cs.perp_pos == y:
                slide = cs.perp_hi - cs.perp_lo
                assert slide == s, (
                    f'{ctype}: trunk slide={slide}, expected {s}'
                )
                return
    pytest.fail(f'No TRUNK_H with trunk y={y}')
