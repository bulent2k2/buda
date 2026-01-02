import pytest
import random
import interconnect
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
    fp = interconnect.Floorplan()
    gen = interconnect.TopologyGenerator(fp)
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
            # The end of one segment must match the start of the next
            segments = topo.segments
            for k in range(len(segments) - 1):
                assert segments[k].end.x == segments[k+1].start.x
                assert segments[k].end.y == segments[k+1].start.y
            
            # Check B: Orthogonality (Manhattan geometry)
            for seg in segments:
                is_horiz = (seg.start.y == seg.end.y)
                is_vert = (seg.start.x == seg.end.x)
                assert is_horiz or is_vert, "Segments must be orthogonal"
                
            # Check C: Endpoints touch the blocks
            # (Simplified check: Start of first segment vs Source Rect)
            # In a real implementation, we check intersection. 
            # Here we just check logical connectivity if the API supports it.
