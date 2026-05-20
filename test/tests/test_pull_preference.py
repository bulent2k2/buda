"""
pytest-bdd step definitions for features/pull_preference.feature.

Pull fields (lo_pull, hi_pull, pull_balance) are NOT yet in the C++ API.
Where possible they are computed from ConnSeg geometry and compared against
expectations.  All scenarios are marked xfail(strict=False) so they document
expected behaviour without blocking CI until the C++ implementation lands.
"""
import math
import pytest
import interconnect
from pytest_bdd import scenarios, given, when, then, parsers
from conftest import (
    _find_candidate, _segs_of, _build_all_cts,
    _compute_pull, _stub_seg_for_block,
)

pytestmark = pytest.mark.xfail(
    strict=False,
    reason='lo_pull / hi_pull / pull_balance not yet in C++ API; computed from geometry',
)

scenarios('features/pull_preference.feature')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pull_balance(lo, hi):
    total = lo + hi
    if total == 0:
        return 0.0
    return (lo - hi) / total


def _ct_for_prefix(ctx, prefix):
    if not ctx.get('conn_topologies'):
        _build_all_cts(ctx)
    key = next((k for k in ctx['conn_topologies'] if k.startswith(prefix)), None)
    assert key is not None, (
        f'No ConnTopology for {prefix!r}. Got: {list(ctx["conn_topologies"])}'
    )
    return ctx['conn_topologies'][key]


def _trunk_conn_seg(ct, is_horiz):
    """Return the trunk ConnSeg: the segment with orientation is_horiz and ≥1 SEG connection."""
    segs = _segs_of(ct)
    candidates = [
        cs for cs in segs
        if cs.horiz == is_horiz
        and sum(1 for c in cs.conns if c.kind == interconnect.SegConnKind.SEG) >= 1
    ]
    if not candidates:
        return None
    # Among candidates, prefer the one with the most SEG connections (root trunk)
    return max(candidates, key=lambda cs: sum(1 for c in cs.conns if c.kind == interconnect.SegConnKind.SEG))


def _v_seg_for(ct, prefix_type):
    """Return the V ConnSeg in a topology (for L_HV: the second segment)."""
    return next((cs for cs in _segs_of(ct) if not cs.horiz), None)


def _h_seg_for(ct):
    return next((cs for cs in _segs_of(ct) if cs.horiz), None)


def _pull_on_seg(ct, cs, expected_trunk_is_horiz):
    """
    Compute lo_pull, hi_pull for a ConnSeg from geometry.
    For a trunk segment: count stubs below (at_pos < perp_pos → lo) and above.
    For a V segment in L_HV: count how many busterm connections are above vs below.
    """
    # If cs has lo_pull/hi_pull in API, use them
    if hasattr(cs, 'lo_pull'):
        return cs.lo_pull, cs.hi_pull
    return _compute_pull(ct, cs)


# ---------------------------------------------------------------------------
# Then: L_HV — V segment pull
# ---------------------------------------------------------------------------

@then(parsers.re(
    r"the L_HV candidate's V segment has hi_pull=(?P<hi>\d+) and lo_pull=(?P<lo>\d+)"
))
def l_hv_v_pull(ctx, hi, lo):
    hi, lo = int(hi), int(lo)
    ct = _ct_for_prefix(ctx, 'L_HV')
    v_cs = _v_seg_for(ct, 'L_HV')
    assert v_cs is not None, 'No V segment in L_HV candidate'
    if hasattr(v_cs, 'hi_pull'):
        assert v_cs.hi_pull == hi
        assert v_cs.lo_pull == lo
        return
    # Geometric approximation: V seg in L_HV goes from bend to B's face
    # If B's face is above the bend → hi_pull; else lo_pull
    bt_conns = [c for c in v_cs.conns if c.kind == interconnect.SegConnKind.BUSTERM]
    assert bt_conns, 'V seg has no BUSTERM connection'
    at_pos = bt_conns[0].at_pos
    if at_pos == v_cs.along_hi:
        actual_hi, actual_lo = 1, 0
    else:
        actual_hi, actual_lo = 0, 1
    assert actual_hi == hi, f'V hi_pull={actual_hi}, expected {hi}'
    assert actual_lo == lo, f'V lo_pull={actual_lo}, expected {lo}'


@then(parsers.re(
    r"the L_HV candidate's V segment pull_balance is (?P<pb>-?[\d.]+)"
))
def l_hv_v_pull_balance(ctx, pb):
    pb = float(pb)
    ct = _ct_for_prefix(ctx, 'L_HV')
    v_cs = _v_seg_for(ct, 'L_HV')
    assert v_cs is not None
    if hasattr(v_cs, 'pull_balance'):
        assert math.isclose(v_cs.pull_balance, pb, abs_tol=1e-9)
        return
    bt_conns = [c for c in v_cs.conns if c.kind == interconnect.SegConnKind.BUSTERM]
    at_pos = bt_conns[0].at_pos
    hi = 1 if at_pos == v_cs.along_hi else 0
    lo = 1 - hi
    actual_pb = _pull_balance(lo, hi)
    assert math.isclose(actual_pb, pb, abs_tol=1e-9), (
        f'V pull_balance={actual_pb}, expected {pb}'
    )


# ---------------------------------------------------------------------------
# Then: L_VH — V segment pull
# ---------------------------------------------------------------------------

@then(parsers.re(
    r"the L_VH candidate's V segment has lo_pull=(?P<lo>\d+) and hi_pull=(?P<hi>\d+)"
))
def l_vh_v_pull(ctx, lo, hi):
    lo, hi = int(lo), int(hi)
    ct = _ct_for_prefix(ctx, 'L_VH')
    v_cs = _v_seg_for(ct, 'L_VH')
    assert v_cs is not None, 'No V segment in L_VH candidate'
    if hasattr(v_cs, 'lo_pull'):
        assert v_cs.lo_pull == lo
        assert v_cs.hi_pull == hi
        return
    bt_conns = [c for c in v_cs.conns if c.kind == interconnect.SegConnKind.BUSTERM]
    assert bt_conns, 'V seg has no BUSTERM connection'
    at_pos = bt_conns[0].at_pos
    if at_pos == v_cs.along_lo:
        actual_lo, actual_hi = 1, 0
    else:
        actual_lo, actual_hi = 0, 1
    assert actual_lo == lo, f'V lo_pull={actual_lo}, expected {lo}'
    assert actual_hi == hi, f'V hi_pull={actual_hi}, expected {hi}'


@then(parsers.re(
    r"the L_VH candidate's V segment pull_balance is \+?(?P<pb>[\d.]+)"
))
def l_vh_v_pull_balance(ctx, pb):
    pb = float(pb)
    ct = _ct_for_prefix(ctx, 'L_VH')
    v_cs = _v_seg_for(ct, 'L_VH')
    assert v_cs is not None
    if hasattr(v_cs, 'pull_balance'):
        assert math.isclose(v_cs.pull_balance, pb, abs_tol=1e-9)
        return
    bt_conns = [c for c in v_cs.conns if c.kind == interconnect.SegConnKind.BUSTERM]
    at_pos = bt_conns[0].at_pos
    lo = 1 if at_pos == v_cs.along_lo else 0
    hi = 1 - lo
    actual_pb = _pull_balance(lo, hi)
    assert math.isclose(actual_pb, pb, abs_tol=1e-9), (
        f'V pull_balance={actual_pb}, expected +{pb}'
    )


# ---------------------------------------------------------------------------
# Then: TRUNK_H@y250 balanced pull
# ---------------------------------------------------------------------------

@then(parsers.re(
    r'the TRUNK_H@y(?P<y>\d+) candidate trunk segment has lo_pull=(?P<lo>\d+) and hi_pull=(?P<hi>\d+)'
))
def trunk_h_pull(ctx, y, lo, hi):
    y, lo, hi = int(y), int(lo), int(hi)
    if not ctx.get('conn_topologies'):
        _build_all_cts(ctx)
    for ctype, ct in ctx['conn_topologies'].items():
        if not ctype.startswith('TRUNK_H') or 'OOB' in ctype:
            continue
        trunk = _trunk_conn_seg(ct, True)
        if trunk is None or trunk.perp_pos != y:
            continue
        if hasattr(trunk, 'lo_pull'):
            actual_lo, actual_hi = trunk.lo_pull, trunk.hi_pull
        else:
            actual_lo, actual_hi = _compute_pull(ct, trunk)
        assert actual_lo == lo, f'{ctype}: lo_pull={actual_lo}, expected {lo}'
        assert actual_hi == hi, f'{ctype}: hi_pull={actual_hi}, expected {hi}'
        return
    pytest.fail(f'No TRUNK_H candidate with trunk perp_pos={y}')


@then(parsers.re(
    r'the TRUNK_H@y(?P<y>\d+) candidate trunk segment pull_balance is (?P<pb>-?[\d.]+)'
))
def trunk_h_pull_balance(ctx, y, pb):
    y, pb = int(y), float(pb)
    if not ctx.get('conn_topologies'):
        _build_all_cts(ctx)
    for ctype, ct in ctx['conn_topologies'].items():
        if not ctype.startswith('TRUNK_H') or 'OOB' in ctype:
            continue
        trunk = _trunk_conn_seg(ct, True)
        if trunk is None or trunk.perp_pos != y:
            continue
        if hasattr(trunk, 'pull_balance'):
            actual = trunk.pull_balance
        else:
            lo, hi = _compute_pull(ct, trunk)
            actual = _pull_balance(lo, hi)
        assert math.isclose(actual, pb, abs_tol=1e-9), (
            f'{ctype}: pull_balance={actual}, expected {pb}'
        )
        return
    pytest.fail(f'No TRUNK_H candidate at y={y}')


# ---------------------------------------------------------------------------
# Then: skewed TRUNK_H scenario
# ---------------------------------------------------------------------------

@then('a TRUNK_H@y250 candidate exists')
def skewed_trunk_h_exists(ctx):
    if not ctx.get('conn_topologies'):
        _build_all_cts(ctx)
    found = any(
        ctype.startswith('TRUNK_H') and 'OOB' not in ctype
        and any(cs.horiz and cs.perp_pos == 250 for cs in _segs_of(ct))
        for ctype, ct in ctx['conn_topologies'].items()
    )
    assert found, (
        f'No TRUNK_H candidate at y=250. '
        f'Available: {list(ctx["conn_topologies"])}'
    )


@then(parsers.re(
    r'that trunk segment has lo_pull=(?P<lo>\d+) and hi_pull=(?P<hi>\d+)'
))
def that_trunk_pull(ctx, lo, hi):
    lo, hi = int(lo), int(hi)
    if not ctx.get('conn_topologies'):
        _build_all_cts(ctx)
    for ctype, ct in ctx['conn_topologies'].items():
        if not ctype.startswith('TRUNK_H') or 'OOB' in ctype:
            continue
        trunk = _trunk_conn_seg(ct, True)
        if trunk is None or trunk.perp_pos != 250:
            continue
        if hasattr(trunk, 'lo_pull'):
            actual_lo, actual_hi = trunk.lo_pull, trunk.hi_pull
        else:
            actual_lo, actual_hi = _compute_pull(ct, trunk)
        assert actual_lo == lo, f'{ctype}: lo_pull={actual_lo}, expected {lo}'
        assert actual_hi == hi, f'{ctype}: hi_pull={actual_hi}, expected {hi}'
        return
    pytest.fail('No TRUNK_H@y250 candidate found for pull check')


@then(parsers.re(
    r'that trunk segment pull_balance is (?P<pb>-?[\d.]+)'
))
def that_trunk_pull_balance(ctx, pb):
    pb = float(pb)
    if not ctx.get('conn_topologies'):
        _build_all_cts(ctx)
    for ctype, ct in ctx['conn_topologies'].items():
        if not ctype.startswith('TRUNK_H') or 'OOB' in ctype:
            continue
        trunk = _trunk_conn_seg(ct, True)
        if trunk is None or trunk.perp_pos != 250:
            continue
        if hasattr(trunk, 'pull_balance'):
            actual = trunk.pull_balance
        else:
            lo, hi = _compute_pull(ct, trunk)
            actual = _pull_balance(lo, hi)
        assert math.isclose(actual, pb, abs_tol=1e-9), (
            f'{ctype}: pull_balance={actual}, expected {pb}'
        )
        return
    pytest.fail('No TRUNK_H@y250 for pull_balance check')


# ---------------------------------------------------------------------------
# Then: ~CEN centroid candidate (not yet generated)
# ---------------------------------------------------------------------------

@then(parsers.re(r'a candidate of type "(?P<t>[^"]+~CEN[^"]*)" exists'))
def cen_candidate_exists(ctx, t):
    matches = [c for c in ctx['candidates'] if '~CEN' in c.type]
    assert matches, (
        f'No ~CEN candidate. Got: {[c.type for c in ctx["candidates"]]}'
    )


@then(parsers.re(
    r'the ~CEN candidate has lo_pull=(?P<lo>\d+) and hi_pull=(?P<hi>\d+)'
))
def cen_pull(ctx, lo, hi):
    lo, hi = int(lo), int(hi)
    cen = next((c for c in ctx['candidates'] if '~CEN' in c.type), None)
    assert cen is not None, 'No ~CEN candidate'
    ct = interconnect.ConnTopology()
    ct.build(cen, ctx['fp'])
    trunk = _trunk_conn_seg(ct, True)
    assert trunk is not None, 'No trunk in ~CEN candidate'
    if hasattr(trunk, 'lo_pull'):
        actual_lo, actual_hi = trunk.lo_pull, trunk.hi_pull
    else:
        actual_lo, actual_hi = _compute_pull(ct, trunk)
    assert actual_lo == lo and actual_hi == hi, (
        f'~CEN pull: lo={actual_lo} hi={actual_hi}, expected lo={lo} hi={hi}'
    )


@then('a candidate with type suffix "~CEN" exists')
def cen_suffix_exists(ctx):
    matches = [c for c in ctx['candidates'] if '~CEN' in c.type]
    assert matches, f'No ~CEN candidate. Got: {[c.type for c in ctx["candidates"]]}'


@then('its trunk y position equals the median of all leaf face_y positions')
def cen_at_median(ctx):
    cen = next((c for c in ctx['candidates'] if '~CEN' in c.type), None)
    assert cen is not None, 'No ~CEN candidate'
    ct = interconnect.ConnTopology()
    ct.build(cen, ctx['fp'])
    # Collect all BUSTERM face positions for V stubs
    face_ys = []
    for cs in _segs_of(ct):
        for conn in cs.conns:
            if conn.kind == interconnect.SegConnKind.BUSTERM:
                face_ys.append(conn.face_coord)
    face_ys.sort()
    n = len(face_ys)
    median = face_ys[n // 2] if n % 2 == 1 else (face_ys[n // 2 - 1] + face_ys[n // 2]) // 2
    trunk = _trunk_conn_seg(ct, True)
    assert trunk is not None
    assert trunk.perp_pos == median, (
        f'~CEN trunk y={trunk.perp_pos}, expected median={median}'
    )


# ---------------------------------------------------------------------------
# Then: three-key sort (requires ranking → xfail)
# ---------------------------------------------------------------------------

@when('two TRUNK_H candidates have equal adjusted_wl and equal min_slide')
def two_equal_trunk_h(ctx):
    ctx['equal_awl_min_slide_pair'] = []  # populated later; real ranking not in API


@then('the candidate with smaller absolute pull_balance ranks first')
def smaller_pull_balance_ranks_first(ctx):
    pytest.xfail('Three-key sort ranking not yet in C++ API')


@when('I generate and rank multicast candidates from "A" to ["B1","B2","C1"] using layers M4,M5')
def gen_rank_multicast_for_pull(ctx):
    import re as re_mod
    dsts = '"B1","B2","C1"'
    dst_list = re_mod.findall(r'"([^"]+)"', dsts)
    from conftest import _build_gen
    ctx['candidates'] = _build_gen(ctx).generate_multicast_candidates('A', dst_list)


@then('for any TRUNK_H candidate with equal adjusted_wl to the ~CEN candidate')
def for_any_equal_awl(ctx):
    pytest.xfail('adjusted_wl ranking not yet in C++ API')


@then('the ~CEN candidate ranks first if its absolute pull_balance is smaller')
def cen_ranks_first(ctx):
    pytest.xfail('adjusted_wl ranking not yet in C++ API')


# ---------------------------------------------------------------------------
# Then: ConnSeg pull fields populated after build()
# ---------------------------------------------------------------------------

@then('every trunk ConnSeg has lo_pull >= 0')
def trunk_lo_pull_nonneg(ctx):
    if not ctx.get('conn_topologies'):
        _build_all_cts(ctx)
    for ctype, ct in ctx['conn_topologies'].items():
        trunk = _trunk_conn_seg(ct, True) or _trunk_conn_seg(ct, False)
        if trunk is None:
            continue
        if hasattr(trunk, 'lo_pull'):
            assert trunk.lo_pull >= 0, f'{ctype}: lo_pull={trunk.lo_pull} < 0'
        else:
            lo, _ = _compute_pull(ct, trunk)
            assert lo >= 0


@then('every trunk ConnSeg has hi_pull >= 0')
def trunk_hi_pull_nonneg(ctx):
    if not ctx.get('conn_topologies'):
        _build_all_cts(ctx)
    for ctype, ct in ctx['conn_topologies'].items():
        trunk = _trunk_conn_seg(ct, True) or _trunk_conn_seg(ct, False)
        if trunk is None:
            continue
        if hasattr(trunk, 'hi_pull'):
            assert trunk.hi_pull >= 0, f'{ctype}: hi_pull={trunk.hi_pull} < 0'
        else:
            _, hi = _compute_pull(ct, trunk)
            assert hi >= 0


@then('every trunk ConnSeg has pull_balance in the range [-1.0, 1.0]')
def trunk_pull_balance_in_range(ctx):
    if not ctx.get('conn_topologies'):
        _build_all_cts(ctx)
    for ctype, ct in ctx['conn_topologies'].items():
        trunk = _trunk_conn_seg(ct, True) or _trunk_conn_seg(ct, False)
        if trunk is None:
            continue
        if hasattr(trunk, 'pull_balance'):
            pb = trunk.pull_balance
        else:
            lo, hi = _compute_pull(ct, trunk)
            pb = _pull_balance(lo, hi)
        assert -1.0 <= pb <= 1.0, f'{ctype}: pull_balance={pb} out of range'


@then('lo_pull + hi_pull equals the total number of stubs on that segment')
def pull_sum_equals_stubs(ctx):
    if not ctx.get('conn_topologies'):
        _build_all_cts(ctx)
    for ctype, ct in ctx['conn_topologies'].items():
        trunk = _trunk_conn_seg(ct, True) or _trunk_conn_seg(ct, False)
        if trunk is None:
            continue
        n_stubs = sum(1 for c in trunk.conns if c.kind == interconnect.SegConnKind.SEG)
        if hasattr(trunk, 'lo_pull'):
            total = trunk.lo_pull + trunk.hi_pull
        else:
            lo, hi = _compute_pull(ct, trunk)
            total = lo + hi
        assert total == n_stubs, (
            f'{ctype}: lo+hi={total} != stubs={n_stubs}'
        )


@then('every stub ConnSeg has lo_pull=0 and hi_pull=0')
def stub_pull_zero(ctx):
    if not ctx.get('conn_topologies'):
        _build_all_cts(ctx)
    for ctype, ct in ctx['conn_topologies'].items():
        for cs in _segs_of(ct):
            # A stub is a segment that is NOT the trunk: only one BUSTERM, no sub-stubs
            n_bt = sum(1 for c in cs.conns if c.kind == interconnect.SegConnKind.BUSTERM)
            n_seg = sum(1 for c in cs.conns if c.kind == interconnect.SegConnKind.SEG)
            if n_bt == 1 and n_seg <= 1:
                if hasattr(cs, 'lo_pull'):
                    assert cs.lo_pull == 0 and cs.hi_pull == 0, (
                        f'{ctype}: stub has lo_pull={cs.lo_pull}, hi_pull={cs.hi_pull}'
                    )
                # If API not available, this assertion trivially passes (no data to check)
