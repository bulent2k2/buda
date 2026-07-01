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

"""Step definitions for features/datapath_trunk.feature.

Opt-in multi-trunk BITRUNK_HVH / BITRUNK_VHV trees for high-fan-out datapath nets.
Reuses conftest.py Given (blocks/layers) and helpers; adds a multi_trunk-enabled
generation When step and structural Then checks (connectivity, acyclicity, root
orientation, multi-tap pass-through span).
"""
import re
import buda
import pytest
from pytest_bdd import scenarios, given, when, then, parsers  # noqa: F401
from conftest import _find_candidate, _segs_of, _has_no_cycles, _build_gen

scenarios('features/datapath_trunk.feature')


def _bitrunks(ctx):
    return [c for c in ctx['candidates'] if c.type.startswith('BITRUNK')]


def _busterms(ct):
    blocks = set()
    for cs in _segs_of(ct):
        for conn in cs.conns:
            if conn.kind == buda.SegConnKind.BUSTERM and conn.block_name:
                blocks.add(conn.block_name)
    return blocks


def _has_root_with_branches(ct, root_horiz, min_branches=2):
    """A 2-level BITRUNK: some segment of the root orientation is joined (SEG) to
    at least `min_branches` segments of the perpendicular (branch) orientation."""
    segs = _segs_of(ct)
    for cs in segs:
        if cs.horiz != root_horiz:
            continue
        perp = sum(1 for c in cs.conns
                   if c.kind == buda.SegConnKind.SEG and segs[c.seg_idx].horiz != root_horiz)
        if perp >= min_branches:
            return True
    return False


# --- When -------------------------------------------------------------------

@when(parsers.re(
    r'I generate multi-trunk candidates from "(?P<src>[^"]+)" to \[(?P<dsts>[^\]]+)\]'
))
def gen_multi_trunk(ctx, src, dsts):
    dst_list = re.findall(r'"([^"]+)"', dsts)
    g = _build_gen(ctx)
    g.set_multi_trunk(True)
    ctx['candidates'] = g.generate_candidates(src, dst_list)


# --- Then -------------------------------------------------------------------

@then(parsers.re(r'no candidate of type "(?P<t>[^"]+)" exists'))
def no_cand_of_type(ctx, t):
    hit = _find_candidate(ctx['candidates'], t)
    assert hit is None, f'unexpected {t!r} candidate: {[c.type for c in ctx["candidates"]]}'


@then(parsers.re(r'every BITRUNK candidate connects all (?P<n>\d+) blocks'))
def every_bitrunk_connects_all(ctx, n):
    n = int(n)
    bits = _bitrunks(ctx)
    assert bits, 'no BITRUNK candidate generated'
    for c in bits:
        reached = set(c.connected_block_names)
        assert len(reached) >= n, (
            f'{c.type} lists {len(reached)} connected blocks, expected {n}')


@then('every BITRUNK candidate has no cycles')
def every_bitrunk_acyclic(ctx):
    for c in _bitrunks(ctx):
        ct = buda.ConnTopology(); ct.build(c, ctx['fp'])
        assert _has_no_cycles(ct), f'{c.type}: cycle detected'


@then("every BITRUNK candidate's root trunk is horizontal")
def bitrunk_root_horizontal(ctx):
    bits = [c for c in _bitrunks(ctx) if c.type.startswith('BITRUNK_HVH')]
    assert bits, 'no BITRUNK_HVH candidate generated'
    for c in bits:
        ct = buda.ConnTopology(); ct.build(c, ctx['fp'])
        assert _has_root_with_branches(ct, root_horiz=True), \
            f'{c.type}: no horizontal root trunk with ≥2 vertical branches'


@then("every BITRUNK candidate's root trunk is vertical")
def bitrunk_root_vertical(ctx):
    bits = [c for c in _bitrunks(ctx) if c.type.startswith('BITRUNK_VHV')]
    assert bits, 'no BITRUNK_VHV candidate generated'
    for c in bits:
        ct = buda.ConnTopology(); ct.build(c, ctx['fp'])
        assert _has_root_with_branches(ct, root_horiz=False), \
            f'{c.type}: no vertical root trunk with ≥2 horizontal branches'


@then(parsers.re(
    r'some BITRUNK_HVH candidate has a vertical branch spanning column '
    r'"(?P<a>[^"]+)","(?P<b>[^"]+)","(?P<c>[^"]+)"'
))
def branch_spans_column(ctx, a, b, c):
    # The three column blocks: the branch V segment must cover their full y extent.
    ys = []
    for name in (a, b, c):
        r = ctx['fp'].get_block_bounds(name)
        ys += [r.y1, r.y2]
    y_lo, y_hi = min(ys), max(ys)
    for cand in ctx['candidates']:
        if not cand.type.startswith('BITRUNK_HVH'):
            continue
        ct = buda.ConnTopology(); ct.build(cand, ctx['fp'])
        for cs in _segs_of(ct):
            if not cs.horiz and cs.along_lo <= y_lo and cs.along_hi >= y_hi:
                return
    pytest.fail(f'no BITRUNK_HVH V branch spans column [{y_lo},{y_hi}]')
