import pytest
import interconnect
from pytest_bdd import scenarios, given, when, then, parsers

scenarios('features/no_tiny_stubs.feature')

@pytest.fixture
def test_state():
    return {}

@given('a floorplan')
def setup_floorplan(test_state):
    test_state['fp'] = interconnect.Floorplan()

@given(parsers.parse('block "{name}" at {x1:d}, {y1:d} to {x2:d}, {y2:d}'))
def add_block(test_state, name, x1, y1, x2, y2):
    test_state['fp'].add_block(name, x1, y1, x2, y2)

@given(parsers.parse('global corner margin is dx={dx:d}, dy={dy:d}'))
def setup_margin(test_state, dx, dy):
    test_state['fp'].set_global_corner_margin(dx, dy)

@given(parsers.parse('a bundle from "{src}" to "{dst}"'))
def setup_bundle(test_state, src, dst):
    test_state['src'] = src
    test_state['dst'] = dst

@when('I generate topologies')
def generate_topos(test_state):
    gen = interconnect.TopologyGenerator(test_state['fp'])
    # Strip face hints like .tx or .rx
    src = test_state['src'].split('.')[0]
    dst = test_state['dst'].split('.')[0]
    cands = gen.generate_candidates(src, dst)
    print(f"DEBUG: Generated {len(cands)} candidates for {src}->{dst}")
    for c in cands:
        print(f"  DEBUG: Candidate: {c.type}")
    test_state['candidates'] = cands

@then('all L-shape candidates should have slide ranges for their spine segments')
def check_l_shape_slides(test_state):
    l_shapes = [c for c in test_state['candidates'] if 'L_' in c.type]
    assert len(l_shapes) > 0, "No L-shapes generated"

@then("these slide ranges should NOT overlap with the connected block's core (non-margin) area")
def check_slide_overlap(test_state):
    fp = test_state['fp']
    for cand in test_state['candidates']:
        ct = interconnect.ConnTopology()
        ct.build(cand, fp)
        for i, cs in enumerate(ct.segs()):
            is_stub = any(c.kind == interconnect.SegConnKind.BUSTERM for c in cs.conns)
            if not is_stub:
                for conn in cs.conns:
                    if conn.kind == interconnect.SegConnKind.SEG:
                        stub_idx = conn.seg_idx
                        stub_cs = list(ct.segs())[stub_idx]
                        for sconn in stub_cs.conns:
                            if sconn.kind == interconnect.SegConnKind.BUSTERM:
                                block_name = sconn.block_name
                                rect = fp.get_block_bounds(block_name)
                                cm = fp.get_block_corner_margin(block_name)
                                core_x1, core_x2 = rect.x1 + cm.dx, rect.x2 - cm.dx
                                core_y1, core_y2 = rect.y1 + cm.dy, rect.y2 - cm.dy
                                if cs.horiz:
                                    assert cs.perp_hi <= core_y1 or cs.perp_lo >= core_y2, \
                                        f"Spine slide [{cs.perp_lo}, {cs.perp_hi}] overlaps core Y [{core_y1}, {core_y2}]"
                                else:
                                    assert cs.perp_hi <= core_x1 or cs.perp_lo >= core_x2, \
                                        f"Spine slide [{cs.perp_lo}, {cs.perp_hi}] overlaps core X [{core_x1}, {core_x2}]"

@then('all stubs should have a minimum length of 20 units')
def check_min_stub_length(test_state):
    fp = test_state['fp']
    for cand in test_state['candidates']:
        ct = interconnect.ConnTopology()
        ct.build(cand, fp)
        for cs in ct.segs():
            is_stub = any(c.kind == interconnect.SegConnKind.BUSTERM for c in cs.conns)
            if is_stub:
                length = abs(cs.along_hi - cs.along_lo)
                assert length >= 20, f"Tiny stub in {cand.type}: length={length}"

@then('all L-shape candidates should connect exactly to the block\'s physical face')
def check_physical_face(test_state):
    fp = test_state['fp']
    for cand in test_state['candidates']:
        if 'L_' not in cand.type: continue
        ct = interconnect.ConnTopology()
        ct.build(cand, fp)
        for cs in ct.segs():
            for conn in cs.conns:
                if conn.kind == interconnect.SegConnKind.BUSTERM:
                    rect = fp.get_block_bounds(conn.block_name)
                    # Check if the segment spans TO the face
                    if cs.horiz:
                        # H-stub connects to left face (x1) or right face (x2)
                        assert cs.along_lo == rect.x1 or cs.along_hi == rect.x1 or \
                               cs.along_lo == rect.x2 or cs.along_hi == rect.x2, \
                            f"Stub in {cand.type} doesn't touch physical face: along=[{cs.along_lo}, {cs.along_hi}] face=[{rect.x1}, {rect.x2}]"
                    else:
                        # V-stub connects to top face (y2) or bottom face (y1)
                        assert cs.along_lo == rect.y1 or cs.along_hi == rect.y1 or \
                               cs.along_lo == rect.y2 or cs.along_hi == rect.y2, \
                            f"Stub in {cand.type} doesn't touch physical face: along=[{cs.along_lo}, {cs.along_hi}] face=[{rect.y1}, {rect.y2}]"

@then('all U-shape candidates should have trunk segments with non-trivial slide ranges')
def check_u_shape_slides(test_state):
    u_shapes = [c for c in test_state['candidates'] if 'U_' in c.type]
    assert len(u_shapes) > 0, "No U-shapes generated"
    for cand in u_shapes:
        ct = interconnect.ConnTopology()
        ct.build(cand, test_state['fp'])
        trunk = list(ct.segs())[1] # Trunk is always middle segment
        assert trunk.perp_hi > trunk.perp_lo, f"U-trunk in {cand.type} has trivial slide"
