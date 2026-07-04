
def generate_large_fanout_buda():
    content = []
    content.append("# Large Fan-out Test Case: 50-unit CPU/GPU floorplan")
    
    # Layers
    content.append("def_layer 3 M3 V TOP 0.0")
    content.append("def_layer 4 M4 H TOP 0.0")
    content.append("def_layer 5 M5 V TOP 0.0")
    content.append("def_layer 6 M6 H TOP 0.0")
    content.append("def_layer 7 M7 V TOP 0.0")
    content.append("")
    
    # Blocks: 10x5 grid
    block_names = []
    for x in range(10):
        for y in range(5):
            name = f"u_{x}_{y}"
            x1 = x * 200
            y1 = y * 200
            x2 = x1 + 100
            y2 = y1 + 100
            content.append(f"add_block {name} {x1} {y1} {x2} {y2}")
            block_names.append(name)
    content.append("")
    
    # Bundle 1: Global Broadcast from u_0_0 to all others
    receivers = ",".join([n for n in block_names if n != "u_0_0"])
    content.append(f"add_bus broadcast[32] u_0_0.out {receivers}")
    content.append("")
    
    # Bundle 2: Column 0 to Column 9
    receivers_col9 = ",".join([f"u_9_{y}.in" for y in range(5)])
    content.append(f"add_bus col_to_col[64] u_0_2.out {receivers_col9}")
    content.append("")
    
    # Bundle 3: Center to Corners
    corners = "u_0_0.in,u_0_4.in,u_9_0.in,u_9_4.in"
    content.append(f"add_bus center_to_corners[16] u_4_2.out {corners}")
    content.append("")
    
    # Flow commands
    content.append("run_bundler strict")
    content.append("generate_topologies")
    content.append("run_planner 5")
    content.append("run_nuts 1.0")
    content.append("visualize")
    
    with open("buda_system_v2/demo/large_fanout.buda", "w") as f:
        f.write("\n".join(content))

if __name__ == "__main__":
    generate_large_fanout_buda()
