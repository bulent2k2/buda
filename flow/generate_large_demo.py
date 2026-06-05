#!/usr/bin/env python3
"""
generate_large_demo.py — produces large_scale_demo.buda

SoC stress-test floorplan:
  ~10400 × 10200 units, 14 functional blocks, 116 bundles,
  bus widths 8–128 bits, three metal layers with 10% overhead each.

Layer assignments (from topology.cpp):
  M4 (layer 4, H) — horizontal stubs in L/Z/U shapes
  M5 (layer 5, V) — all vertical segments
  M6 (layer 6, H) — horizontal trunks in U_VHV shapes (long-haul detours)
"""

import textwrap

# ---------------------------------------------------------------------------
# Floorplan blocks  (name, x1, y1, x2, y2)
# ---------------------------------------------------------------------------
BLOCKS = [
    # Compute column (left)
    ("u_cpu0",   200,   200,  1000,  1600),   # CPU core cluster 0
    ("u_cpu1",   200,  2000,  1000,  3400),   # CPU core cluster 1
    ("u_gpu",    200,  3800,  2000,  6600),   # GPU cluster
    ("u_npu",    200,  7000,  2000,  8400),   # Neural processing unit

    # Cache / fabric (centre)
    ("u_l3",    2400,   200,  6000,  3400),   # Shared L3 cache (large)
    ("u_noc",   2400,  3800,  8400,  6600),   # Network-on-chip fabric

    # Memory (right)
    ("u_hbm0",  8800,   200, 10400,  3400),   # HBM controller 0
    ("u_hbm1",  8800,  3800, 10400,  6600),   # HBM controller 1
    ("u_ddr",   8800,  7000, 10400,  8400),   # DDR controller

    # Peripherals (bottom row)
    ("u_disp",  2400,  7000,  5000,  8400),   # Display controller
    ("u_isp",   5600,  7000,  8400,  8400),   # Image signal processor
    ("u_pcie",  2400,  9000,  5000, 10200),   # PCIe subsystem
    ("u_usb",   5600,  9000,  8400, 10200),   # USB / IO hub
    ("u_sec",   8800,  9000, 10400, 10200),   # Security / crypto
]

# ---------------------------------------------------------------------------
# Port groups  (prefix, src_block, dst_block, width_bits, num_ports)
#
# Each port in a group becomes one BUDA bundle; ports are separated by using
# hierarchical driver/receiver pin names  (u_block.prefix_portN.tx / .rx)
# so the STRICT bundler assigns each port its own bundle.
# ---------------------------------------------------------------------------
PORT_GROUPS = [
    # ── CPU0 ↔ L3 cache ───────────────────────────────────────────────────
    ("c0l3d", "u_cpu0", "u_l3",   128, 2),   # 2× 128-bit data
    ("c0l3t", "u_cpu0", "u_l3",    64, 1),   # 64-bit tag
    ("c0l3c", "u_cpu0", "u_l3",    32, 1),   # 32-bit ctrl

    # ── CPU1 ↔ L3 cache ───────────────────────────────────────────────────
    ("c1l3d", "u_cpu1", "u_l3",   128, 2),
    ("c1l3t", "u_cpu1", "u_l3",    64, 1),
    ("c1l3c", "u_cpu1", "u_l3",    32, 1),

    # ── CPU0 → NoC ────────────────────────────────────────────────────────
    ("c0ncp", "u_cpu0", "u_noc",   64, 4),   # 4× 64-bit NoC ports
    ("c0ncm", "u_cpu0", "u_noc",   32, 2),   # 2× 32-bit mgmt

    # ── CPU1 → NoC ────────────────────────────────────────────────────────
    ("c1ncp", "u_cpu1", "u_noc",   64, 4),
    ("c1ncm", "u_cpu1", "u_noc",   32, 2),

    # ── GPU → NoC ─────────────────────────────────────────────────────────
    ("gpncp", "u_gpu",  "u_noc",  128, 6),   # 6× 128-bit data ports
    ("gpncc", "u_gpu",  "u_noc",   32, 2),   # 2× 32-bit ctrl

    # ── GPU → L3 ──────────────────────────────────────────────────────────
    ("gpl3p", "u_gpu",  "u_l3",   128, 4),

    # ── NPU → NoC ─────────────────────────────────────────────────────────
    ("npncp", "u_npu",  "u_noc",   64, 4),
    ("npncc", "u_npu",  "u_noc",   32, 2),

    # ── NPU → L3 ──────────────────────────────────────────────────────────
    ("npl3p", "u_npu",  "u_l3",    64, 2),

    # ── L3 → NoC ──────────────────────────────────────────────────────────
    ("l3ncp", "u_l3",   "u_noc",  128, 8),   # 8× 128-bit (deliberate congestion)
    ("l3ncc", "u_l3",   "u_noc",   32, 2),

    # ── NoC → HBM0 ────────────────────────────────────────────────────────
    ("nh0_p", "u_noc",  "u_hbm0", 128, 4),

    # ── NoC → HBM1 ────────────────────────────────────────────────────────
    ("nh1_p", "u_noc",  "u_hbm1", 128, 4),

    # ── NoC → DDR ─────────────────────────────────────────────────────────
    ("ndr_p", "u_noc",  "u_ddr",   64, 4),

    # ── NoC → Display ─────────────────────────────────────────────────────
    ("ndsp_", "u_noc",  "u_disp", 128, 4),
    ("ndsc_", "u_noc",  "u_disp",  32, 2),

    # ── NoC → ISP ─────────────────────────────────────────────────────────
    ("nisp_", "u_noc",  "u_isp",   64, 4),

    # ── NoC → PCIe ────────────────────────────────────────────────────────
    ("npc__", "u_noc",  "u_pcie",  32, 6),

    # ── NoC → USB ─────────────────────────────────────────────────────────
    ("nusb_", "u_noc",  "u_usb",   16, 4),

    # ── NoC → Security ────────────────────────────────────────────────────
    ("nsec_", "u_noc",  "u_sec",   32, 4),

    # ── Security → CPUs ───────────────────────────────────────────────────
    ("sc0_p", "u_sec",  "u_cpu0",  64, 2),
    ("sc1_p", "u_sec",  "u_cpu1",  64, 2),

    # ── ISP → NoC (return data path) ──────────────────────────────────────
    ("isn_p", "u_isp",  "u_noc",   32, 4),

    # ── GPU → Display (direct framebuffer bypass) ──────────────────────────
    ("gdsp_", "u_gpu",  "u_disp", 128, 4),

    # ── PCIe → NoC (return) ───────────────────────────────────────────────
    ("pcn_p", "u_pcie", "u_noc",   32, 4),

    # ── 8-bit management buses ────────────────────────────────────────────
    ("c0sm_", "u_cpu0", "u_sec",    8, 2),
    ("c1sm_", "u_cpu1", "u_sec",    8, 2),
    ("c0pm_", "u_cpu0", "u_pcie",   8, 1),
    ("c0um_", "u_cpu0", "u_usb",    8, 1),
]


def generate(out_path: str) -> None:
    lines: list[str] = []

    def w(*args):
        lines.append(" ".join(str(a) for a in args))

    def blank():
        lines.append("")

    def comment(text):
        lines.append(f"# {text}")

    # ------------------------------------------------------------------
    comment("=" * 66)
    comment("BUDA Large-Scale Stress-Test Demo")
    comment("14 blocks · 116 bundles · 8–128-bit buses · M4/M5/M6 layers")
    comment("10% power+clock overhead per layer")
    comment("=" * 66)
    blank()

    # Layers
    comment("Layers: M4 horizontal, M5 vertical, M6 long-haul horizontal")
    w("def_layer", 4, "M4", "H", "TOP", "10.0")
    w("def_layer", 5, "M5", "V", "TOP", "10.0")
    w("def_layer", 6, "M6", "H", "TOP", "10.0")
    blank()

    # Blocks
    comment("Floorplan blocks")
    for name, x1, y1, x2, y2 in BLOCKS:
        w("add_block", name, x1, y1, x2, y2)
    blank()

    # Buses — one add_bus line per bundle (vector syntax)
    comment("Buses (vector syntax: add_bus prefix[N] drv rcv)")
    bundle_list = []   # (hint, src_block, dst_block) for topology generation
    for pfx, src, dst, width, num_ports in PORT_GROUPS:
        src_inst = src
        dst_inst = dst
        for port in range(num_ports):
            pin_tag  = f"{pfx}{port}"
            drv_pin  = f"{src_inst}.{pin_tag}.tx"
            rcv_pin  = f"{dst_inst}.{pin_tag}.rx"
            w("add_bus", f"{pin_tag}[{width}]", drv_pin, rcv_pin)
            bundle_list.append((f"{pin_tag}_", src, dst))
        blank()

    # Bundler
    comment("Stage 1 — bundle nets by driver+receiver instance")
    w("run_bundler", "strict")
    blank()

    # Topology generation — one call per bundle
    comment("Stage 2 — generate L/Z/U topology candidates per bundle")
    for hint, src, dst in bundle_list:
        w("generate_topologies_for_bundle", hint, src, dst)
    blank()

    # Planner
    comment("Stage 3 — congestion-aware topology selection (fattest-bus-first)")
    w("run_planner", 1)
    blank()

    # NUTS
    comment("Stage 4 — NUTS abstract track placement (pitch=2.0)")
    w("run_nuts", "2.0")
    blank()

    comment("Visualize")
    w("visualize")

    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    # Summary
    total_bundles = sum(n for _, _, _, _, n in PORT_GROUPS)
    total_nets    = sum(w * n for _, _, _, w, n in PORT_GROUPS)
    print(f"Written: {out_path}")
    print(f"  Blocks  : {len(BLOCKS)}")
    print(f"  Bundles : {total_bundles}")
    print(f"  Nets    : {total_nets}")


if __name__ == "__main__":
    import os
    out = os.path.join(os.path.dirname(__file__), "large_scale_demo.buda")
    generate(out)
