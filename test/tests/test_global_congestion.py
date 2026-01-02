import pytest
import interconnect
from pytest_bdd import scenarios, given, when, then, parsers

# Load features if using file-based Gherkin, or define steps directly
# scenarios('features/global_congestion.feature')

@pytest.fixture
def router_context():
    fp = interconnect.Floorplan()
    ls = interconnect.LayerStack()
    
    # Define a simple grid: 0, 100 in X and Y
    fp.add_block("blk_1", 0, 0, 100, 100)
    
    # Define layers
    ls.add_layer(3, "M3_Horiz", interconnect.LayerDir.HORIZONTAL, interconnect.LayerType.TOP)
    ls.add_layer(4, "M4_Vert", interconnect.LayerDir.VERTICAL, interconnect.LayerType.TOP)
    
    router = interconnect.GlobalRouter(fp, ls)
    return {'router': router, 'fp': fp, 'ls': ls}

def test_dilution_logic(router_context):
    router = router_context['router']
    
    # Set 25% overhead for M3 (Layer 3)
    # Math: Effective = Width * (100 / (100 - 25)) = Width * 1.333
    router.set_layer_overhead(3, 25.0)
    
    # Check effective width for a raw width of 10.0
    eff_width = router.test_get_effective_width(10.0, 3)
    assert eff_width == pytest.approx(13.333, 0.01)
    
    # Check M4 (0% overhead)
    eff_width_m4 = router.test_get_effective_width(10.0, 4)
    assert eff_width_m4 == pytest.approx(10.0, 0.01)

def test_congestion_and_selection(router_context):
    router = router_context['router']
    
    # 1. Setup Environment
    # Set high overhead to force congestion easily
    router.set_layer_overhead(3, 50.0) # 2x dilution on Horiz
    
    # 2. Build Congestion Map (Constructs cuts from the floorplan)
    router.build_congestion_map()
    
    # 3. Create a Bundle with 2 Candidates
    # Candidate A: L-Shape (Horizontal first) -> Uses M3 heavily
    # Candidate B: Z-Shape (Vertical first) -> Uses M4 heavily
    
    # We define a wrapper manually or via helper
    # Assuming C++ binding exposes BundleWrapper and Topology structs
    
    cand_A = interconnect.Topology()
    cand_A.type = "L_Horiz_Dominant"
    # Seg: (0,0)->(100,0) on M3, then (100,0)->(100,100) on M4
    seg_a1 = interconnect.Segment(0,0, 100,0)
    seg_a1.layer_hint = 3
    cand_A.segments.append(seg_a1)
    
    cand_B = interconnect.Topology()
    cand_B.type = "L_Vert_Dominant"
    # Seg: (0,0)->(0,100) on M4, then (0,100)->(100,100) on M3
    seg_b1 = interconnect.Segment(0,0, 0,100)
    seg_b1.layer_hint = 4 # Vertical layer (low overhead)
    cand_B.segments.append(seg_b1)

    # Create the bundle wrapper
    wrapper = interconnect.BundleWrapper()
    wrapper.width = 10.0
    wrapper.candidates = [cand_A, cand_B]
    
    # 4. Inject artificial congestion on M3 (Horizontal)
    # This should force the router to pick Candidate B (Vertical dominant)
    router.inject_congestion_on_layer(3, 9999.0) 
    
    # 5. Run Optimization
    # We pass a list containing our one bundle
    bundles = [wrapper]
    router.optimize_topologies(bundles, max_iterations=2)
    
    # 6. Verify Selection
    selected = bundles[0].candidates[bundles[0].selected_topology_index]
    print(f"Selected Topology: {selected.type}")
    
    assert selected.type == "L_Vert_Dominant"
