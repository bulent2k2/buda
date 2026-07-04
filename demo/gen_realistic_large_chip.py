#!/usr/bin/env python3
import os

def generate_realistic_large_chip():
    lines = []
    lines.append("# Realistic Large Chip: 50-unit SoC top-level floorplan")
    lines.append("# Inspired by fig/Buda-topo-1.png (Diverse block sizes)")
    lines.append("")

    # Layers
    lines.append("def_layer 3 M3 V TOP 0.0")
    lines.append("def_layer 4 M4 H TOP 0.0")
    lines.append("def_layer 5 M5 V TOP 0.0")
    lines.append("def_layer 6 M6 H TOP 0.0")
    lines.append("def_layer 7 M7 V TOP 0.0")
    lines.append("")

    blocks = []
    # 1. CPU Cores: 4 clusters of 8 cores (32 cores)
    # Clusters at top-left, top-right, mid-left, mid-right
    cluster_offsets = [(200, 200), (8200, 200), (200, 4200), (8200, 4200)]
    for ci, (ox, oy) in enumerate(cluster_offsets):
        for i in range(8):
            name = f"u_cpu_c{ci}_core{i}"
            x1 = ox + (i % 4) * 300
            y1 = oy + (i // 4) * 300
            x2 = x1 + 200
            y2 = y1 + 200
            blocks.append((name, x1, y1, x2, y2))
        # Cluster Local NOC
        blocks.append((f"u_cpu_c{ci}_noc", ox + 1200, oy, ox + 1600, oy + 500))

    # 2. Large Central Blocks
    blocks.append(("u_l3_cache", 3000, 2000, 7000, 4000)) # Extra large
    blocks.append(("u_system_noc", 3000, 4200, 7000, 6200)) # Extra large
    blocks.append(("u_gpu", 3000, 6400, 7000, 9400)) # Extra large

    # 3. Memory Controllers (Edges)
    # Left edge
    blocks.append(("u_hbm0", 0, 2000, 150, 4000))
    blocks.append(("u_hbm1", 0, 4200, 150, 6200))
    # Right edge
    blocks.append(("u_hbm2", 9850, 2000, 10000, 4000))
    blocks.append(("u_hbm3", 9850, 4200, 10000, 6200))
    # Top edge
    blocks.append(("u_ddr0", 3000, 0, 4800, 150))
    blocks.append(("u_ddr1", 5200, 0, 7000, 150))
    # Bottom edge
    blocks.append(("u_ddr2", 3000, 9850, 4800, 10000))
    blocks.append(("u_ddr3", 5200, 9850, 7000, 10000))

    # 4. Peripherals (Diverse sizes)
    blocks.append(("u_npu", 200, 7000, 1500, 8500))
    blocks.append(("u_isp", 200, 8700, 1500, 9800))
    blocks.append(("u_display", 8000, 7000, 9500, 8000))
    blocks.append(("u_pcie", 8000, 8200, 9500, 9200))
    blocks.append(("u_usb", 8000, 9400, 9500, 10000))

    # Verify we have ~50 blocks
    # 32 cores + 4 cluster_nocs + 3 large + 4 hbm + 4 ddr + 5 periph = 52 blocks
    for name, x1, y1, x2, y2 in blocks:
        lines.append(f"add_block {name} {x1} {y1} {x2} {y2}")
    lines.append("")

    # --- Large Fan-out Bundles ---
    
    # Bundle 1: System-wide Reset/Clock (Broadcast from Noc to everything)
    all_others = [b[0] for b in blocks if b[0] != "u_system_noc"]
    receivers = ",".join([f"{n}.rst" for n in all_others])
    lines.append(f"add_bus sys_rst[1] u_system_noc.out {receivers}")
    lines.append("")

    # Bundle 2: L3 to all CPU Clusters (Multicast)
    cluster_nocs = [f"u_cpu_c{i}_noc.in" for i in range(4)]
    lines.append(f"add_bus l3_to_cpu[256] u_l3_cache.out {','.join(cluster_nocs)}")
    lines.append("")

    # Bundle 3: GPU to Memory Controllers (Multicast to 4 HBMs)
    hbms = [f"u_hbm{i}.in" for i in range(4)]
    lines.append(f"add_bus gpu_mem[512] u_gpu.out {','.join(hbms)}")
    lines.append("")

    # Pipeline
    lines.append("run_bundler strict")
    lines.append("generate_topologies")
    lines.append("run_planner 5")
    lines.append("run_nuts 1.0")
    lines.append("visualize")

    out_path = "buda_system_v2/demo/realistic_large_chip.buda"
    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Generated {out_path} with {len(blocks)} blocks.")

if __name__ == "__main__":
    generate_realistic_large_chip()
