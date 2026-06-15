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
pytest-bdd step definitions for features/hierarchy_depth_planning.feature.

Hierarchy-depth busterm generation: blocks carry a depth and parent annotation;
generate_topologies with a depth parameter rolls up deeper blocks to their
depth-N ancestors and skips intra-ancestor nets.

The depth/parent API on add_block and generate_topologies is not yet implemented
in the C++ engine; all scenarios are xfail.
"""
import pytest
import buda
from pytest_bdd import scenarios, given, when, then, parsers

pytestmark = pytest.mark.xfail(
    strict=False,
    reason='hierarchy depth / rollup not yet implemented in C++ (depth_ is dead code)',
)

scenarios('features/hierarchy_depth_planning.feature')


# ---------------------------------------------------------------------------
# Context fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def ctx():
    return {}


# ---------------------------------------------------------------------------
# Given — hierarchical floorplan
# ---------------------------------------------------------------------------

@given('a hierarchical floorplan with blocks:')
def given_hierarchical_floorplan(ctx, datatable):
    """
    Datatable columns: Name | x1 | y1 | x2 | y2 | depth | parent
    """
    fp = buda.Floorplan()
    hierarchy = {}  # name → {depth, parent}
    for row in datatable[1:]:
        name, x1, y1, x2, y2, depth, parent = row
        fp.add_block(
            name, int(x1), int(y1), int(x2), int(y2),
            depth=int(depth),
            parent=parent.strip() if parent.strip() else None,
        )
        hierarchy[name] = {'depth': int(depth), 'parent': parent.strip()}
    ctx['fp'] = fp
    ctx['hierarchy'] = hierarchy


@given(parsers.re(r'a net "(?P<name>[^"]+)" from "(?P<drv>[^"]+)" to "(?P<rcv>[^"]+)"'))
def given_net(ctx, name, drv, rcv):
    nl = ctx.setdefault('netlist', buda.Netlist())
    nl.add_net(name, f'{drv}.tx', f'{rcv}.rx')
    ctx['netlist'] = nl


@given(parsers.re(r'layer (?P<layer_name>\w+) is HORIZONTAL with id (?P<lid>\d+)'))
def given_layer_h(ctx, layer_name, lid):
    ctx['layer_h'] = int(lid)
    ctx.setdefault('layer_names', {})[layer_name] = int(lid)


@given(parsers.re(r'layer (?P<layer_name>\w+) is VERTICAL with id (?P<lid>\d+)'))
def given_layer_v(ctx, layer_name, lid):
    ctx['layer_v'] = int(lid)
    ctx.setdefault('layer_names', {})[layer_name] = int(lid)


@given('a .buda script:')
def given_buda_script(ctx, text):
    ctx['buda_script'] = text.strip()


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------

@when(parsers.re(r'I generate topologies at depth (?P<depth>\d+)'))
def when_generate_at_depth(ctx, depth):
    gen = buda.TopologyGenerator(ctx['fp'])
    gen.set_layer_ids(ctx['layer_h'], ctx['layer_v'])
    gen.set_depth(int(depth))
    nl = ctx.get('netlist')
    ctx['candidates_by_net'] = {}
    if nl:
        for net_name in nl.net_names():
            net = nl.get_net(net_name)
            drv_block = net.driver_instance()
            rcv_block = net.receiver_instances()[0]
            cands = gen.generate_candidates(drv_block, rcv_block)
            ctx['candidates_by_net'][net_name] = cands
    ctx['depth'] = int(depth)


@when(parsers.re(r'I run the bundler at depth (?P<depth>\d+)'))
def when_run_bundler_at_depth(ctx, depth):
    from buda import Bundler, BundlerStrategy
    b = Bundler()
    b.set_depth(int(depth))
    nl = ctx.get('netlist')
    if nl:
        ctx['bundles'] = b.bundle(nl, BundlerStrategy.STRICT)
    ctx['bundler_depth'] = int(depth)


@when(parsers.re(
    r'I run pass 1: generate and place "(?P<net>[^"]+)" at depth (?P<depth>\d+)'
))
def when_pass1(ctx, net, depth):
    gen = buda.TopologyGenerator(ctx['fp'])
    gen.set_layer_ids(ctx['layer_h'], ctx['layer_v'])
    gen.set_depth(int(depth))
    nl = ctx.get('netlist')
    net_obj = nl.get_net(net)
    drv = net_obj.driver_instance()
    rcv = net_obj.receiver_instances()[0]
    cands = gen.generate_candidates(drv, rcv)
    # Run NUTS to place; store result for keepout promotion
    engine = buda.NUTSEngine()
    from buda import BundleWrapper
    w = BundleWrapper()
    w.input.candidates = cands
    result = engine.solve([w])
    ctx['pass1_result'] = result
    ctx['pass1_net'] = net


@when(parsers.re(
    r'I promote the depth=(?P<depth>\d+) NUTS result as a keepout for layer (?P<layer>\w+)'
))
def when_promote_keepout(ctx, depth, layer):
    result = ctx.get('pass1_result')
    if result is None:
        return
    lid = ctx.get('layer_names', {}).get(layer, 4)
    fp = ctx['fp']
    for seg in result.segments:
        if seg.layer == lid:
            lo = seg.track_position - seg.width / 2
            hi = seg.track_position + seg.width / 2
            if seg.is_horizontal:
                fp.add_keepout_zone(int(seg.span_lo), int(lo), int(seg.span_hi), int(hi), [lid])
            else:
                fp.add_keepout_zone(int(lo), int(seg.span_lo), int(hi), int(seg.span_hi), [lid])


@when(parsers.re(
    r'I run pass 2: generate topologies for "(?P<net>[^"]+)" at depth (?P<depth>\d+)'
))
def when_pass2(ctx, net, depth):
    gen = buda.TopologyGenerator(ctx['fp'])
    gen.set_layer_ids(ctx['layer_h'], ctx['layer_v'])
    gen.set_depth(int(depth))
    nl = ctx.get('netlist')
    net_obj = nl.get_net(net)
    drv = net_obj.driver_instance()
    rcv = net_obj.receiver_instances()[0]
    ctx['pass2_candidates'] = gen.generate_candidates(drv, rcv)


@when(parsers.re(r'I run the global planner for "(?P<net>[^"]+)"'))
def when_run_planner(ctx, net):
    ls = buda.LayerStack()
    ls.add_layer(ctx['layer_h'], 'M4', buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    ls.add_layer(ctx['layer_v'], 'M5', buda.LayerDir.VERTICAL,   buda.LayerType.TOP)
    router = buda.GlobalRouter(ctx['fp'], ls)
    router.build_congestion_map()
    from buda import BundleWrapper, Bundle
    b = Bundle()
    b.id = 1
    w = BundleWrapper()
    w.input.original_bundle = b
    w.input.width = 8.0
    w.input.candidates = ctx['pass2_candidates']
    assignments = router.optimize_topologies([w], 1)
    ctx['pass2_assignment'] = assignments[0] if assignments else None


@when('I execute the .buda script')
def when_execute_script(ctx):
    import subprocess, tempfile, os
    script = ctx['buda_script']
    with tempfile.NamedTemporaryFile(mode='w', suffix='.buda', delete=False) as f:
        f.write(script)
        fname = f.name
    try:
        result = subprocess.run(
            ['python3', 'buda_cli.py', fname],
            cwd='/Users/ben/src/buda/buda_system_v2/src',
            capture_output=True, text=True, timeout=30,
        )
        ctx['script_returncode'] = result.returncode
        ctx['script_stdout'] = result.stdout
        ctx['script_stderr'] = result.stderr
    finally:
        os.unlink(fname)


# ---------------------------------------------------------------------------
# Then — depth=0 and depth=1 candidate existence
# ---------------------------------------------------------------------------

@then(parsers.re(r'(?P<n>\d+) topology candidates are generated for "(?P<net>[^"]+)"'))
def then_n_candidates(ctx, n, net):
    cands = ctx.get('candidates_by_net', {}).get(net, [])
    assert len(cands) == int(n), \
        f'Expected {n} candidates for {net!r}, got {len(cands)}'


@then(parsers.re(r'topology candidates are generated for "(?P<net>[^"]+)"'))
def then_some_candidates(ctx, net):
    cands = ctx.get('candidates_by_net', {}).get(net, [])
    assert len(cands) > 0, f'No topology candidates generated for {net!r}'


@then(parsers.re(
    r'the busterm endpoints for "(?P<net>[^"]+)" resolve to "(?P<blk1>[^"]+)" and "(?P<blk2>[^"]+)"'
))
def then_endpoints_resolve(ctx, net, blk1, blk2):
    cands = ctx.get('candidates_by_net', {}).get(net, [])
    assert cands, f'No candidates for {net!r}'
    c = cands[0]
    src_name = getattr(c, 'src_block', None)
    dst_name = getattr(c, 'dst_block', None)
    expected = {blk1, blk2}
    actual = {src_name, dst_name}
    assert actual == expected, \
        f'Endpoints for {net!r}: expected {expected}, got {actual}'


@then(parsers.re(r'a candidate of type "(?P<key>[^"]+)" exists for "(?P<net>[^"]+)"'))
def then_candidate_type_exists(ctx, key, net):
    cands = ctx.get('candidates_by_net', {}).get(net, [])
    matching = [c for c in cands if getattr(c, 'type', '') == key or getattr(c, 'trunk_key', '') == key]
    assert matching, f'No candidate {key!r} for {net!r}; got: {[getattr(c,"type","?") for c in cands]}'


@then(parsers.re(
    r'the H stub from "(?P<blk>[^"]+)" in (?P<key>\S+) touches "(?P<blk2>[^"]+)"\'s right face'
))
def then_h_stub_right_face(ctx, blk, key, blk2):
    # Structural assertion: stub endpoint == block right face x coordinate
    fp = ctx['fp']
    rects = fp.get_block_rects(blk2) if hasattr(fp, 'get_block_rects') else []
    if not rects:
        block = fp.get_block(blk2)
        right_face = block.x2
    else:
        right_face = max(r.x2 for r in rects)
    net_cands = list(ctx.get('candidates_by_net', {}).values())
    cands = [c for cl in net_cands for c in cl]
    c = next((c for c in cands if getattr(c, 'type', '') == key or getattr(c, 'trunk_key', '') == key), None)
    assert c is not None, f'Candidate {key!r} not found'
    # Find H stub endpoint for the block
    for seg in getattr(c, 'segments', []):
        if seg.start.y == seg.end.y:  # horizontal
            if seg.start.x == right_face or seg.end.x == right_face:
                return
    pytest.fail(f'No H stub touching {blk2!r} right face (x={right_face}) in {key!r}')


@then(parsers.re(
    r'the H stub from "(?P<blk>[^"]+)" in (?P<key>\S+) touches "(?P<blk2>[^"]+)"\'s left face'
))
def then_h_stub_left_face(ctx, blk, key, blk2):
    fp = ctx['fp']
    rects = fp.get_block_rects(blk2) if hasattr(fp, 'get_block_rects') else []
    if not rects:
        block = fp.get_block(blk2)
        left_face = block.x1
    else:
        left_face = min(r.x1 for r in rects)
    net_cands = list(ctx.get('candidates_by_net', {}).values())
    cands = [c for cl in net_cands for c in cl]
    c = next((c for c in cands if getattr(c, 'type', '') == key or getattr(c, 'trunk_key', '') == key), None)
    assert c is not None, f'Candidate {key!r} not found'
    for seg in getattr(c, 'segments', []):
        if seg.start.y == seg.end.y:
            if seg.start.x == left_face or seg.end.x == left_face:
                return
    pytest.fail(f'No H stub touching {blk2!r} left face (x={left_face}) in {key!r}')


# ---------------------------------------------------------------------------
# Then — bundler depth assertions
# ---------------------------------------------------------------------------

def _find_bundle_for_net(bundles, net_name):
    for b in bundles:
        if net_name in (b.net_names if hasattr(b, 'net_names') else getattr(b, 'nets', [])):
            return b
    return None


@then(parsers.re(r'"(?P<n1>[^"]+)" and "(?P<n2>[^"]+)" are in the same bundle'))
def then_same_bundle(ctx, n1, n2):
    bundles = ctx.get('bundles', [])
    b1 = _find_bundle_for_net(bundles, n1)
    b2 = _find_bundle_for_net(bundles, n2)
    assert b1 is not None, f'{n1!r} not in any bundle'
    assert b2 is not None, f'{n2!r} not in any bundle'
    assert b1 is b2 or b1.id == b2.id, \
        f'{n1!r} and {n2!r} are in different bundles: {b1.id} vs {b2.id}'


@then(parsers.re(r'"(?P<n1>[^"]+)" and "(?P<n2>[^"]+)" are in different bundles'))
def then_different_bundles(ctx, n1, n2):
    bundles = ctx.get('bundles', [])
    b1 = _find_bundle_for_net(bundles, n1)
    b2 = _find_bundle_for_net(bundles, n2)
    assert b1 is not None, f'{n1!r} not in any bundle'
    assert b2 is not None, f'{n2!r} not in any bundle'
    assert b1 is not b2 and b1.id != b2.id, \
        f'{n1!r} and {n2!r} are in the same bundle {b1.id} but should be different'


# ---------------------------------------------------------------------------
# Then — multi-pass assertions
# ---------------------------------------------------------------------------

@then(parsers.re(r'"(?P<net>[^"]+)" is not placed at y=(?P<y>\d+)'))
def then_not_placed_at_y(ctx, net, y):
    asn = ctx.get('pass2_assignment')
    assert asn is not None, 'No pass2 assignment found'
    cands = ctx.get('pass2_candidates', [])
    topo = cands[asn.topo_index]
    target_y = int(y)
    for seg in topo.segments:
        if seg.start.y == target_y and seg.end.y == target_y:
            pytest.fail(f'{net!r} has a segment placed at y={target_y}, expected to avoid it')


@then(parsers.re(r'"(?P<net>[^"]+)" avoids the keepout zone from the depth=(?P<depth>\d+) pass'))
def then_avoids_keepout(ctx, net, depth):
    asn = ctx.get('pass2_assignment')
    assert asn is not None, 'No pass2 assignment found'
    cands = ctx.get('pass2_candidates', [])
    topo = cands[asn.topo_index]
    fp = ctx['fp']
    kozs = fp.get_keepout_zones()
    lid = ctx.get('layer_h', 4)
    for i, seg in enumerate(topo.segments):
        seg_layer = asn.seg_layers[i] if hasattr(asn, 'seg_layers') else lid
        if seg_layer != lid:
            continue
        for koz in kozs:
            if lid in koz.layer_ids:
                r = koz.bbox
                is_h = (seg.start.y == seg.end.y)
                if is_h:
                    if r.y1 <= seg.start.y <= r.y2:
                        slo = min(seg.start.x, seg.end.x)
                        shi = max(seg.start.x, seg.end.x)
                        if not (shi < r.x1 or slo > r.x2):
                            pytest.fail(f'{net!r} segment {i} intersects keepout from depth={depth} pass')


# ---------------------------------------------------------------------------
# Then — .buda script assertions
# ---------------------------------------------------------------------------

@then('the script completes without error')
def then_script_no_error(ctx):
    rc = ctx.get('script_returncode', -1)
    stderr = ctx.get('script_stderr', '')
    assert rc == 0, f'Script exited with code {rc}. Stderr:\n{stderr}'
