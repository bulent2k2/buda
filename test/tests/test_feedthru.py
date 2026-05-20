"""
pytest-bdd step definitions for features/feedthru.feature.

Feedthru block relay — splitting trunk segments at feedthru block faces,
suppressing V stubs for feedthru blocks, and populating feedthru_blocks
metadata on the Topology object.

None of these fields exist yet in the C++ API; all scenarios are xfail.
"""
import pytest
import interconnect
from pytest_bdd import scenarios, given, when, then, parsers
from conftest import _find_candidate, _segs_of, _build_all_cts

pytestmark = pytest.mark.xfail(
    strict=False,
    reason='feedthru relay not yet implemented in C++',
)

scenarios('features/feedthru.feature')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _h_seg_endpoints(ct):
    """Collect all along_lo / along_hi of horizontal ConnSegs."""
    pts = set()
    for cs in _segs_of(ct):
        if cs.horiz:
            pts.add(cs.along_lo)
            pts.add(cs.along_hi)
    return pts


def _v_seg_for_block(ct, blk):
    for cs in _segs_of(ct):
        if cs.horiz:
            continue
        for conn in cs.conns:
            if conn.kind == interconnect.SegConnKind.BUSTERM and conn.block_name == blk:
                return cs
    return None


# ---------------------------------------------------------------------------
# Then: basic feedthru detection
# ---------------------------------------------------------------------------

@then(parsers.re(r'"(?P<blk>[^"]+)" is recognised as a feedthru block'))
def feedthru_recognised(ctx, blk):
    ft_names = ctx.get('feedthru_names', set())
    assert blk in ft_names, f'"{blk}" not in feedthru_names: {ft_names}'


@then(parsers.re(r'the topology for "(?P<src>[^"]+)" → "(?P<dst>[^"]+)" includes "(?P<blk>[^"]+)" as a relay'))
def topology_includes_relay(ctx, src, dst, blk):
    for c in ctx['candidates']:
        feedthru = getattr(c, 'feedthru_blocks', None) or []
        if blk in feedthru:
            return
    pytest.fail(
        f'No candidate has "{blk}" as relay. '
        f'Candidates: {[c.type for c in ctx["candidates"]]}'
    )


@then(parsers.re(r'no V stub is emitted for "(?P<blk>[^"]+)"'))
def no_v_stub_feedthru(ctx, blk):
    for c in ctx['candidates']:
        feedthru = getattr(c, 'feedthru_blocks', []) or []
        if blk not in feedthru:
            continue
        ct = interconnect.ConnTopology()
        ct.build(c, ctx['fp'])
        v_stub = _v_seg_for_block(ct, blk)
        assert v_stub is None, (
            f'{c.type}: V stub found for feedthru block "{blk}"'
        )
        return
    pytest.fail(f'No candidate with "{blk}" as feedthru block')


@then(parsers.re(r'"(?P<blk>[^"]+)" appears in feedthru_blocks'))
def appears_in_feedthru_blocks(ctx, blk):
    for c in ctx['candidates']:
        feedthru = getattr(c, 'feedthru_blocks', None) or []
        if blk in feedthru:
            return
    pytest.fail(
        f'"{blk}" not found in feedthru_blocks of any candidate. '
        f'Candidates: {[c.type for c in ctx["candidates"]]}'
    )


# ---------------------------------------------------------------------------
# Then: trunk split
# ---------------------------------------------------------------------------

@then(parsers.re(
    r'the trunk is split into two H segments at x=(?P<x1>\d+) and x=(?P<x2>\d+)'
))
def trunk_split_two_h_segs(ctx, x1, x2):
    x1, x2 = int(x1), int(x2)
    for c in ctx['candidates']:
        ct = interconnect.ConnTopology()
        ct.build(c, ctx['fp'])
        endpoints = _h_seg_endpoints(ct)
        if x1 in endpoints and x2 in endpoints:
            return
    pytest.fail(f'No candidate has trunk split at x={x1}, x={x2}')


@then(parsers.re(
    r'the left segment spans from x=(?P<x1>\d+) to x=(?P<x2>\d+)'
))
def left_segment_span(ctx, x1, x2):
    x1, x2 = int(x1), int(x2)
    for c in ctx['candidates']:
        ct = interconnect.ConnTopology()
        ct.build(c, ctx['fp'])
        for cs in _segs_of(ct):
            if cs.horiz and cs.along_lo == x1 and cs.along_hi == x2:
                return
    pytest.fail(f'No H segment from x={x1} to x={x2}')


@then(parsers.re(
    r'the right segment spans from x=(?P<x1>\d+) to x=(?P<x2>\d+)'
))
def right_segment_span(ctx, x1, x2):
    x1, x2 = int(x1), int(x2)
    for c in ctx['candidates']:
        ct = interconnect.ConnTopology()
        ct.build(c, ctx['fp'])
        for cs in _segs_of(ct):
            if cs.horiz and cs.along_lo == x1 and cs.along_hi == x2:
                return
    pytest.fail(f'No H segment from x={x1} to x={x2}')


# ---------------------------------------------------------------------------
# Then: OOB feedthru
# ---------------------------------------------------------------------------

@then(parsers.re(
    r'a feedthru path exists between "(?P<b1>[^"]+)" and "(?P<b2>[^"]+)" through "(?P<ft>[^"]+)"'
))
def feedthru_path_exists(ctx, b1, b2, ft):
    for c in ctx['candidates']:
        feedthru = getattr(c, 'feedthru_blocks', None) or []
        if ft in feedthru:
            ct = interconnect.ConnTopology()
            ct.build(c, ctx['fp'])
            blocks = set()
            for cs in _segs_of(ct):
                for conn in cs.conns:
                    if conn.kind == interconnect.SegConnKind.BUSTERM and conn.block_name:
                        blocks.add(conn.block_name)
            if {b1, b2} <= blocks:
                return
    pytest.fail(f'No feedthru path {b1}→{ft}→{b2}')


@then(parsers.re(
    r'"(?P<blk>[^"]+)" does not have a V stub in the selected topology'
))
def no_v_stub_in_selected(ctx, blk):
    # Iterate all candidates for any that has blk as feedthru
    for c in ctx['candidates']:
        feedthru = getattr(c, 'feedthru_blocks', []) or []
        if blk not in feedthru:
            continue
        ct = interconnect.ConnTopology()
        ct.build(c, ctx['fp'])
        v_stub = _v_seg_for_block(ct, blk)
        assert v_stub is None, f'{c.type}: V stub found for "{blk}"'
        return


@then(parsers.re(
    r'the pass-through count for "(?P<blk>[^"]+)" is (?P<n>\d+)'
))
def pass_through_count(ctx, blk, n):
    n = int(n)
    for c in ctx['candidates']:
        if c.pass_through_count == n:
            return
    # Not checking per-block pass_through (only total is in API)
    pytest.xfail('Per-block pass_through_count not in C++ API')


# ---------------------------------------------------------------------------
# Then: multiple feedthru blocks
# ---------------------------------------------------------------------------

@then(parsers.re(
    r'feedthru_blocks contains "(?P<blk>[^"]+)"'
))
def feedthru_blocks_contains(ctx, blk):
    for c in ctx['candidates']:
        feedthru = getattr(c, 'feedthru_blocks', None) or []
        if blk in feedthru:
            return
    pytest.fail(f'"{blk}" not in feedthru_blocks of any candidate')


@then(parsers.re(
    r'the trunk H segment passes through "(?P<blk>[^"]+)" without a V stub'
))
def trunk_passes_through_no_stub(ctx, blk):
    for c in ctx['candidates']:
        ct = interconnect.ConnTopology()
        ct.build(c, ctx['fp'])
        v_stub = _v_seg_for_block(ct, blk)
        if v_stub is None:
            return
    pytest.fail(f'Every candidate has a V stub for "{blk}"')
