import pytest
import random
import buda
from pytest_bdd import scenarios, given, when, then, parsers

# Load Gherkin features (if any exist for specific scenarios)
# scenarios('features/topology_generation.feature')

def is_point_in_rect(pt_x, pt_y, rect):
    # C++ Rect struct exposed as tuple or object with properties
    # Assuming the binding exposes .x1, .y1, .x2, .y2
    return (rect.x1 <= pt_x <= rect.x2) and (rect.y1 <= pt_y <= rect.y2)

def calculate_manhattan_dist(p1, p2):
    return abs(p1.x - p2.x) + abs(p1.y - p2.y)

@pytest.fixture
def topology_setup():
    fp = buda.Floorplan()
    gen = buda.TopologyGenerator(fp)
    return fp, gen

# --- Random Test Generator ---

def test_random_topologies(topology_setup):
    fp, gen = topology_setup
    
    print("\n--- Running 10 Random Topology Scenarios ---")
    
    for i in range(10):
        # 1. Randomize Coordinates (Grid 0-1000)
        # Source Block
        sx1 = random.randint(0, 800)
        sy1 = random.randint(0, 800)
        src_name = f"u_src_{i}"
        fp.add_block(src_name, sx1, sy1, sx1+50, sy1+50)
        
        # Dest Block (ensure some distance)
        dx1 = random.randint(0, 800)
        dy1 = random.randint(0, 800)
        dst_name = f"u_dst_{i}"
        fp.add_block(dst_name, dx1, dy1, dx1+50, dy1+50)
        
        # Add random "obstacle" blocks to populate Hanan Grid
        for j in range(3):
            ox = random.randint(0, 900)
            oy = random.randint(0, 900)
            fp.add_block(f"u_obs_{i}_{j}", ox, oy, ox+20, oy+20)

        # 2. Create a Mock Bundle
        # In a real scenario, this comes from the Bundler. 
        # Here we mock the connectivity info required by the generator.
        # We assume the generator takes src_instance and dst_instance names.
        candidates = gen.generate_candidates(src_name, dst_name)
        
        print(f"Case {i+1}: {src_name}->{dst_name} | Candidates found: {len(candidates)}")
        
        # 3. Assertions
        assert len(candidates) > 0, "Should always find at least L-shapes"
        
        for topo in candidates:
            # Check A: Continuity
            # Segments must be connected: either end matches start, or they intersect
            # (allowing for spread-case overlaps used to preserve V/H segments).
            segments = topo.segments
            for k in range(len(segments) - 1):
                s1, s2 = segments[k], segments[k+1]
                # Is s1 connected to s2?
                # Case 1: Exact match
                if s1.end.x == s2.start.x and s1.end.y == s2.start.y:
                    continue
                
                # Case 2: Intersection (T-junction or overlap)
                # s1 must contain s2.start OR s2 must contain s1.end
                def point_on_seg(p, s):
                    if s.start.x == s.end.x: # Vertical
                        return p.x == s.start.x and min(s.start.y, s.end.y) <= p.y <= max(s.start.y, s.end.y)
                    else: # Horizontal
                        return p.y == s.start.y and min(s.start.x, s.end.x) <= p.x <= max(s.start.x, s.end.x)
                
                assert point_on_seg(s2.start, s1) or point_on_seg(s1.end, s2), \
                    f"Disconnected segments in {topo.type}: seg{k} end ({s1.end.x},{s1.end.y}) vs seg{k+1} start ({s2.start.x},{s2.start.y})"
            
            # Check B: Orthogonality (Manhattan geometry)
            for seg in segments:
                is_horiz = (seg.start.y == seg.end.y)
                is_vert = (seg.start.x == seg.end.x)
                assert is_horiz or is_vert, "Segments must be orthogonal"
                
            # Check C: Endpoints touch the blocks
            # (Simplified check: Start of first segment vs Source Rect)
            # In a real implementation, we check intersection.
            # Here we just check logical connectivity if the API supports it.


# ---------------------------------------------------------------------------
# Regression: spread-Z trunk slide range must be finite
# ---------------------------------------------------------------------------

def _make_spread_z_floorplan():
    """Two blocks at identical Y range, side-by-side in X.
    Their shrunken centres share the same Y → spread-Z candidates generated."""
    fp = buda.Floorplan()
    fp.set_global_corner_margin(15, 20)
    fp.add_block("u0",   200, 400, 300, 600)
    fp.add_block("u22", -100, 400,  -50, 600)
    return fp


def test_spread_z_hvh_trunk_has_bounded_slide_range():
    """Spread Z_HVH trunk must have finite perp_lo and perp_hi.

    Regression for topology.cpp bug where the H stubs terminated at
    x_cut ± 1 instead of x_cut, leaving ConnTopology with no connections
    for the trunk and an unbounded slide interval.
    """
    fp = _make_spread_z_floorplan()
    gen = buda.TopologyGenerator(fp)
    gen.set_layer_ids(4, 5)
    candidates = gen.generate_candidates("u0", "u22")

    spread_z = [t for t in candidates if t.type.startswith("Z_HVH")]
    assert len(spread_z) >= 1, "Expected at least one spread Z_HVH candidate"

    BOUND = 10_000  # anything larger than the floorplan is effectively unbounded
    ct = buda.ConnTopology()
    for topo in spread_z:
        ct.build(topo, fp)
        for j, cs in enumerate(ct.segs()):
            if not cs.horiz:  # V trunk segment
                assert cs.perp_lo > -BOUND, (
                    f"Trunk perp_lo unbounded in {topo.type} seg{j}: {cs.perp_lo}"
                )
                assert cs.perp_hi < BOUND, (
                    f"Trunk perp_hi unbounded in {topo.type} seg{j}: {cs.perp_hi}"
                )
                # Trunk should slide within the inter-block gap
                # u22.x2=-50, u0.x1=200, min_stub_length=20 (default)
                assert cs.perp_lo >= -50 + 20 - 1   # allow 1-unit tolerance
                assert cs.perp_hi <=  200 - 20 + 1


def test_spread_z_hvh_trunk_has_two_seg_connections():
    """Each spread Z_HVH trunk must be connected to both H stubs."""
    fp = _make_spread_z_floorplan()
    gen = buda.TopologyGenerator(fp)
    gen.set_layer_ids(4, 5)
    candidates = gen.generate_candidates("u0", "u22")

    spread_z = [t for t in candidates if t.type.startswith("Z_HVH")]
    ct = buda.ConnTopology()
    for topo in spread_z:
        ct.build(topo, fp)
        for j, cs in enumerate(ct.segs()):
            if not cs.horiz:  # V trunk
                seg_conns = [c for c in cs.conns
                             if c.kind == buda.SegConnKind.SEG]
                assert len(seg_conns) == 2, (
                    f"Trunk in {topo.type} seg{j} has {len(seg_conns)} SEG "
                    f"connections, expected 2"
                )
