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
pytest-bdd step definitions for features/topology_flexibility.feature.

Tests slide-range computation, adjusted_wl formula, kFlex, and ref_slide.

Slide ranges (perp_lo, perp_hi) are already in the C++ API via ConnTopology.
adjusted_wl, min_slide, and kFlex-based ranking are NOT yet in the C++ API;
those scenarios are marked xfail.
"""
import math
import pytest
import buda
from pytest_bdd import scenarios, given, when, then, parsers
from conftest import (
    _find_candidate, _segs_of, _build_all_cts,
)

scenarios('features/topology_flexibility.feature')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ct_for_prefix(ctx, prefix):
    if not ctx.get('conn_topologies'):
        _build_all_cts(ctx)
    key = next((k for k in ctx['conn_topologies'] if k.startswith(prefix)), None)
    assert key is not None, (
        f'No ConnTopology for {prefix!r}. Got: {list(ctx["conn_topologies"])}'
    )
    return ctx['conn_topologies'][key]


def _min_slide(ct):
    """Minimum slide (perp_hi - perp_lo) across all ConnSegs in ct."""
    slides = []
    for cs in _segs_of(ct):
        if cs.perp_hi > cs.perp_lo:
            slides.append(cs.perp_hi - cs.perp_lo)
    return min(slides) if slides else 0


def _adjusted_wl(wl, min_sl, kflex, ref_slide):
    discount = kflex * min(min_sl / ref_slide, 1.0)
    return wl * (1.0 - discount)


# ---------------------------------------------------------------------------
# Then: slide ranges (currently implemented via ConnTopology)
# ---------------------------------------------------------------------------

@then(parsers.re(
    r"the L_HV candidate's H segment has perp_lo=(?P<lo>\d+) and perp_hi=(?P<hi>\d+)"
))
def l_hv_h_perp(ctx, lo, hi):
    lo, hi = int(lo), int(hi)
    ct = _ct_for_prefix(ctx, 'L_HV')
    h_segs = [cs for cs in _segs_of(ct) if cs.horiz]
    assert h_segs, 'No H segment in L_HV candidate'
    cs = h_segs[0]
    assert cs.perp_lo == lo, f'H perp_lo={cs.perp_lo}, expected {lo}'
    assert cs.perp_hi == hi, f'H perp_hi={cs.perp_hi}, expected {hi}'


@then(parsers.re(
    r"the L_HV candidate's H segment slide equals (?P<s>\d+)"
))
def l_hv_h_slide(ctx, s):
    s = int(s)
    ct = _ct_for_prefix(ctx, 'L_HV')
    h_segs = [cs for cs in _segs_of(ct) if cs.horiz]
    assert h_segs, 'No H segment in L_HV candidate'
    cs = h_segs[0]
    slide = cs.perp_hi - cs.perp_lo
    assert slide == s, f'H segment slide={slide}, expected {s}'


@then(parsers.re(
    r"the Z_HVH@x(?P<x>\d+) candidate's V trunk segment has "
    r"perp_lo=(?P<lo>\d+) and perp_hi=(?P<hi>\d+)"
))
def z_hvh_v_trunk_perp(ctx, x, lo, hi):
    x, lo, hi = int(x), int(lo), int(hi)
    if not ctx.get('conn_topologies'):
        _build_all_cts(ctx)
    prefix = f'Z_HVH@x{x}'
    ct = _ct_for_prefix(ctx, prefix)
    v_segs = [cs for cs in _segs_of(ct) if not cs.horiz]
    assert v_segs, f'No V segment in {prefix} candidate'
    # The trunk is the V segment with SEG connections
    trunk = next(
        (cs for cs in v_segs
         if any(c.kind == buda.SegConnKind.SEG for c in cs.conns)),
        v_segs[0]
    )
    assert trunk.perp_lo == lo, f'V trunk perp_lo={trunk.perp_lo}, expected {lo}'
    assert trunk.perp_hi == hi, f'V trunk perp_hi={trunk.perp_hi}, expected {hi}'


@then(parsers.re(
    r"the Z_HVH@x(?P<x>\d+) candidate's V trunk segment slide equals (?P<s>\d+)"
))
def z_hvh_v_trunk_slide(ctx, x, s):
    x, s = int(x), int(s)
    if not ctx.get('conn_topologies'):
        _build_all_cts(ctx)
    prefix = f'Z_HVH@x{x}'
    ct = _ct_for_prefix(ctx, prefix)
    v_segs = [cs for cs in _segs_of(ct) if not cs.horiz]
    assert v_segs, f'No V segment in {prefix}'
    trunk = next(
        (cs for cs in v_segs
         if any(c.kind == buda.SegConnKind.SEG for c in cs.conns)),
        v_segs[0]
    )
    slide = trunk.perp_hi - trunk.perp_lo
    assert slide == s, f'V trunk slide={slide}, expected {s}'


# ---------------------------------------------------------------------------
# Then: adjusted_wl formula (requires API not yet in C++ → xfail)
# ---------------------------------------------------------------------------

@then(
    'each candidate has adjusted_wl = estimated_wl × (1 − kFlex × clamp(min_slide/ref_slide,0,1))'
)
def each_candidate_adjusted_wl(ctx):
    kflex = ctx['kflex']
    ref_slide = ctx['ref_slide']
    if not ctx.get('conn_topologies'):
        _build_all_cts(ctx)
    for c in ctx['candidates']:
        actual = getattr(c, 'adjusted_wl', None)
        if actual is None:
            pytest.xfail('adjusted_wl not yet in C++ API')
        ct = ctx['conn_topologies'].get(c.type)
        if ct is None:
            ct = buda.ConnTopology()
            ct.build(c, ctx['fp'])
        min_sl = _min_slide(ct)
        expected = _adjusted_wl(c.estimated_wirelength, min_sl, kflex, ref_slide)
        assert math.isclose(actual, expected, rel_tol=1e-6), (
            f'{c.type}: adjusted_wl={actual}, expected {expected}'
        )


@then(parsers.re(
    r"the Z_HVH@x(?P<x>\d+) candidate's adjusted_wl equals (?P<v>\d+)"
))
def z_hvh_adjusted_wl(ctx, x, v):
    x, v = int(x), float(v)
    target = next((c for c in ctx['candidates'] if c.type.startswith(f'Z_HVH@x{x}')), None)
    if target is None:
        pytest.xfail(f'No Z_HVH@x{x} candidate')
    actual = getattr(target, 'adjusted_wl', None)
    if actual is None:
        pytest.xfail('adjusted_wl not yet in C++ API')
    assert math.isclose(actual, v, rel_tol=1e-6), f'adjusted_wl={actual}, expected {v}'


@then(parsers.re(
    r"the L_HV candidate's adjusted_wl equals (?P<v>\d+)"
))
@pytest.mark.xfail(strict=False, reason='adjusted_wl not yet in C++ API')
def l_hv_adjusted_wl(ctx, v):
    v = float(v)
    target = _find_candidate(ctx['candidates'], 'L_HV')
    assert target is not None, 'No L_HV candidate'
    actual = getattr(target, 'adjusted_wl', None)
    assert actual is not None, 'adjusted_wl not set'
    assert math.isclose(actual, v, rel_tol=1e-6), f'adjusted_wl={actual}, expected {v}'


@then('for every candidate adjusted_wl equals estimated_wl')
def every_candidate_adjusted_eq_estimated(ctx):
    for c in ctx['candidates']:
        actual = getattr(c, 'adjusted_wl', None)
        if actual is None:
            pytest.xfail('adjusted_wl not yet in C++ API')
        assert math.isclose(actual, c.estimated_wirelength, rel_tol=1e-6), (
            f'{c.type}: adjusted_wl={actual} != estimated_wl={c.estimated_wirelength}'
        )


# ---------------------------------------------------------------------------
# Then: ref_slide boundary / discount factor (requires adjusted_wl → xfail)
# ---------------------------------------------------------------------------

def _find_i_h(ctx):
    """Return (candidate, ConnTopology) for I_H candidate (any @y suffix accepted)."""
    if not ctx.get('conn_topologies'):
        _build_all_cts(ctx)
    key = next((k for k in ctx['conn_topologies'] if k.startswith('I_H')), None)
    assert key is not None, f'No I_H candidate. Got: {list(ctx["conn_topologies"])}'
    c = _find_candidate(ctx['candidates'], 'I_H')
    return c, ctx['conn_topologies'][key]


@then(parsers.re(
    r'the I_H@y(?P<y>\d+) candidate has min_slide = (?P<s>\d+)'
))
def i_h_min_slide(ctx, y, s):
    s = int(s)
    _, ct = _find_i_h(ctx)
    actual = _min_slide(ct)
    assert actual == s, f'I_H min_slide={actual}, expected {s}'


@then(parsers.re(r'the I_H candidate has min_slide = (?P<s>\d+)'))
def i_h_min_slide_plain(ctx, s):
    s = int(s)
    _, ct = _find_i_h(ctx)
    actual = _min_slide(ct)
    assert actual == s, f'I_H min_slide={actual}, expected {s}'


@then(parsers.re(
    r'the I_H@y(?P<y>\d+) candidate discount factor is (?P<d>[\d.]+)'
))
def i_h_discount(ctx, y, d):
    d = float(d)
    c, _ = _find_i_h(ctx)
    actual = getattr(c, 'discount_factor', None)
    if actual is None:
        pytest.xfail('discount_factor not yet in C++ API')
    assert math.isclose(actual, d, rel_tol=1e-6), f'discount={actual}, expected {d}'


@then(parsers.re(r'the I_H candidate discount factor is (?P<d>[\d.]+)'))
def i_h_discount_plain(ctx, d):
    d = float(d)
    c, _ = _find_i_h(ctx)
    actual = getattr(c, 'discount_factor', None)
    if actual is None:
        pytest.xfail('discount_factor not yet in C++ API')
    assert math.isclose(actual, d, rel_tol=1e-6), f'discount={actual}, expected {d}'


@then(parsers.re(
    r'the I_H@y(?P<y>\d+) candidate adjusted_wl equals estimated_wl multiplied by (?P<factor>[\d.]+)'
))
def i_h_adjusted_wl_factor(ctx, y, factor):
    factor = float(factor)
    c, _ = _find_i_h(ctx)
    actual = getattr(c, 'adjusted_wl', None)
    if actual is None:
        pytest.xfail('adjusted_wl not yet in C++ API')
    expected = c.estimated_wirelength * factor
    assert math.isclose(actual, expected, rel_tol=1e-6), (
        f'adjusted_wl={actual}, expected estimated_wl×{factor}={expected}'
    )


@then(parsers.re(
    r'the I_H candidate adjusted_wl equals estimated_wl multiplied by (?P<factor>[\d.]+)'
))
def i_h_adjusted_wl_factor_plain(ctx, factor):
    factor = float(factor)
    c, _ = _find_i_h(ctx)
    actual = getattr(c, 'adjusted_wl', None)
    if actual is None:
        pytest.xfail('adjusted_wl not yet in C++ API')
    expected = c.estimated_wirelength * factor
    assert math.isclose(actual, expected, rel_tol=1e-6), (
        f'adjusted_wl={actual}, expected estimated_wl×{factor}={expected}'
    )


@when('I find a candidate with min_slide = 0')
def find_zero_slide_candidate(ctx):
    if not ctx.get('conn_topologies'):
        _build_all_cts(ctx)
    for c in ctx['candidates']:
        ct = ctx['conn_topologies'].get(c.type)
        if ct is None:
            ct = buda.ConnTopology()
            ct.build(c, ctx['fp'])
        if _min_slide(ct) == 0:
            ctx['zero_slide_cand'] = c
            ctx['zero_slide_ct'] = ct
            return
    pytest.skip('No candidate with min_slide=0 found')


@then("that candidate's adjusted_wl equals estimated_wl")
@pytest.mark.xfail(strict=False, reason='adjusted_wl not yet in C++ API')
def zero_slide_adjusted_eq_estimated(ctx):
    c = ctx.get('zero_slide_cand')
    if c is None:
        pytest.skip('No zero-slide candidate')
    actual = getattr(c, 'adjusted_wl', None)
    assert actual is not None, 'adjusted_wl not set'
    assert math.isclose(actual, c.estimated_wirelength, rel_tol=1e-6)


# ---------------------------------------------------------------------------
# Then: tie-break by min_slide (requires ranking → xfail)
# ---------------------------------------------------------------------------

@when('two candidates have equal adjusted_wl')
@pytest.mark.xfail(strict=False, reason='adjusted_wl not yet in C++ API')
def find_equal_adjusted_wl_pair(ctx):
    kflex = ctx['kflex']
    ref_slide = ctx['ref_slide']
    if not ctx.get('conn_topologies'):
        _build_all_cts(ctx)
    pairs = []
    cands = ctx['candidates']
    for i in range(len(cands)):
        for j in range(i + 1, len(cands)):
            ci, cj = cands[i], cands[j]
            ct_i = ctx['conn_topologies'].get(ci.type)
            ct_j = ctx['conn_topologies'].get(cj.type)
            if ct_i is None or ct_j is None:
                continue
            awl_i = _adjusted_wl(ci.estimated_wirelength, _min_slide(ct_i), kflex, ref_slide)
            awl_j = _adjusted_wl(cj.estimated_wirelength, _min_slide(ct_j), kflex, ref_slide)
            if math.isclose(awl_i, awl_j, rel_tol=1e-6):
                pairs.append((ci, ct_i, cj, ct_j))
    ctx['equal_awl_pairs'] = pairs
    assert pairs, 'No pair of candidates with equal adjusted_wl'


@then('the candidate with larger min_slide ranks first among those two')
@pytest.mark.xfail(strict=False, reason='adjusted_wl ranking not yet in C++ API')
def larger_min_slide_ranks_first(ctx):
    pairs = ctx.get('equal_awl_pairs', [])
    assert pairs, 'No equal-adjusted_wl pairs found'
    cands = ctx['candidates']
    for ci, ct_i, cj, ct_j in pairs:
        slide_i = _min_slide(ct_i)
        slide_j = _min_slide(ct_j)
        if math.isclose(slide_i, slide_j):
            continue
        idx_i = next(k for k, c in enumerate(cands) if c.type == ci.type)
        idx_j = next(k for k, c in enumerate(cands) if c.type == cj.type)
        if slide_i > slide_j:
            assert idx_i < idx_j, (
                f'{ci.type}(slide={slide_i}) should rank before {cj.type}(slide={slide_j})'
            )
        else:
            assert idx_j < idx_i, (
                f'{cj.type}(slide={slide_j}) should rank before {ci.type}(slide={slide_i})'
            )
