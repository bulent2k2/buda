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
pytest-bdd step definitions for features/multi_rect_block.feature.

Tests multi-rect block support: equivalent busterms, rectilinear block shapes,
correct best-rect selection, Hanan grid contributions, and stub geometry.

All scenarios xfail until add_block_rects is implemented in C++.
"""
import pytest
import buda
from pytest_bdd import scenarios, given, when, then, parsers
from conftest import (
    _find_candidate, _segs_of,
    _stub_seg_for_block, _stub_length,
)

scenarios('features/multi_rect_block.feature')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cand_ct(ctx, type_str):
    """Return ConnTopology for the candidate matching type_str, or xfail."""
    cand = _find_candidate(ctx['candidates'], type_str)
    if cand is None:
        pytest.xfail(
            f'No candidate of type {type_str!r}; '
            'multi-rect block support not yet in C++'
        )
    ct = buda.ConnTopology()
    ct.build(cand, ctx['fp'])
    return ct


# ---------------------------------------------------------------------------
# Then: no V stub — block connects Direct to H trunk
# ---------------------------------------------------------------------------

@then(parsers.re(
    r'"(?P<blk>[^"]+)" has no V stub in the TRUNK_H@y(?P<y>\d+) candidate'
))
def no_v_stub_in_trunk_h(ctx, blk, y):
    ct = _cand_ct(ctx, f'TRUNK_H@y{y}')
    cs = _stub_seg_for_block(ct, blk)
    assert cs is not None, f'Block {blk!r} not found in TRUNK_H@y{y} topology'
    assert cs.horiz, (
        f'Block {blk!r} has a V stub in TRUNK_H@y{y}; '
        f'expected Direct connection (best rect straddles trunk y)'
    )


# ---------------------------------------------------------------------------
# Then: V stub of specified length to H trunk
# ---------------------------------------------------------------------------

@then(parsers.re(
    r'the stub from "(?P<blk>[^"]+)" in the TRUNK_H@y(?P<y>\d+) candidate '
    r'has length (?P<n>\d+)'
))
def v_stub_length_in_trunk_h(ctx, blk, y, n):
    n = int(n)
    ct = _cand_ct(ctx, f'TRUNK_H@y{y}')
    cs = _stub_seg_for_block(ct, blk)
    assert cs is not None, f'Block {blk!r} not found in TRUNK_H@y{y} topology'
    assert not cs.horiz, (
        f'Block {blk!r} is Direct (no V stub) in TRUNK_H@y{y}; '
        f'expected a V stub of length {n}'
    )
    actual = _stub_length(cs)
    assert actual == n, (
        f'Block {blk!r} V stub in TRUNK_H@y{y}: expected length {n}, got {actual}'
    )


# ---------------------------------------------------------------------------
# Then: no H stub — block connects Direct to V trunk
# ---------------------------------------------------------------------------

@then(parsers.re(
    r'"(?P<blk>[^"]+)" has no H stub in the TRUNK_V@x(?P<x>\d+) candidate'
))
def no_h_stub_in_trunk_v(ctx, blk, x):
    ct = _cand_ct(ctx, f'TRUNK_V@x{x}')
    cs = _stub_seg_for_block(ct, blk)
    assert cs is not None, f'Block {blk!r} not found in TRUNK_V@x{x} topology'
    assert not cs.horiz, (
        f'Block {blk!r} has an H stub in TRUNK_V@x{x}; '
        f'expected Direct connection (best rect straddles trunk x)'
    )


# ---------------------------------------------------------------------------
# Then: H stub of specified length to V trunk
# ---------------------------------------------------------------------------

@then(parsers.re(
    r'the H stub from "(?P<blk>[^"]+)" in the TRUNK_V@x(?P<x>\d+) candidate '
    r'has length (?P<n>\d+)'
))
def h_stub_length_in_trunk_v(ctx, blk, x, n):
    n = int(n)
    ct = _cand_ct(ctx, f'TRUNK_V@x{x}')
    cs = _stub_seg_for_block(ct, blk)
    assert cs is not None, f'Block {blk!r} not found in TRUNK_V@x{x} topology'
    assert cs.horiz, (
        f'Block {blk!r} is Direct (no H stub) in TRUNK_V@x{x}; '
        f'expected an H stub of length {n}'
    )
    actual = _stub_length(cs)
    assert actual == n, (
        f'Block {blk!r} H stub in TRUNK_V@x{x}: expected length {n}, got {actual}'
    )
