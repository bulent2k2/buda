#!/usr/bin/env python3
"""
tools/fp_demo.py — Generate demo BDB scenarios for the bfp launcher.

Usage:
  python3 tools/fp_demo.py tc1 [/tmp/bfp_tc1.bdb]
  python3 tools/fp_demo.py tc2 [/tmp/bfp_tc2.bdb]

TC1 — overlap storm: 40 varied-size blocks all stacked at (0, 0), connected
      by 80 buses.  In the GUI: Optimize → SA, 50 000 iter, weights default.

TC2 — fixed I/O: io_pad at origin, 39 free blocks, 80 buses.
      In the GUI: Optimize → SA, 50 000 iter, check Fixed for io_pad.
"""

import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in [os.path.join(_ROOT, "build"), _HERE]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import floorplanner_commands as fpc


def _add_buses(state, names, rng, n_buses: int) -> None:
    """Write n_buses undirected bus nets to the BDB (components must be in DB already).

    Each net connects two root-level blocks.  Pins default to block centres
    (BDB derives centre from each component's stored bbox).
    """
    n = len(names)
    pairs = [(rng.randint(0, n - 1), rng.randint(0, n - 1)) for _ in range(n_buses)]
    pairs = [(a, (b + 1) % n if b == a else b) for a, b in pairs]
    for bus_idx, (a_idx, b_idx) in enumerate(pairs):
        state.bdb.add_net_pins_undirected(
            f"bus_{bus_idx:03d}",
            [names[a_idx], names[b_idx]],
        )


def setup_tc1(path: str) -> None:
    """TC1 — overlap storm: 40 varied-size blocks all stacked at (0, 0).

    80 bus nets written to BDB (block centres as pins).
    In the GUI: Optimize → SA, 50 000 iterations, default weights.
    Blocks spread across the 3000 × 2400 die driven by bus connectivity.
    """
    rng = random.Random(42)
    state = fpc.create_bdb(path, 3000.0, 2400.0, grid=10.0)

    sizes = [(rng.randint(6, 30) * 10, rng.randint(4, 20) * 10) for _ in range(40)]
    names = [f"blk_{i:02d}" for i in range(40)]
    for name, (w, h) in zip(names, sizes):
        fpc.add_block(state, name, 0, 0, w, h)

    fpc.write_bdb(state)          # components must be in DB before adding nets
    _add_buses(state, names, rng, n_buses=80)

    print(f"TC1 written to {path}")
    print(f"  40 blocks (all at origin), 80 bus nets, die 3000×2400 grid=10")
    print(f"  Optimize → SA, 50 000 iter — blocks will spread across the die.")


def setup_tc2(path: str) -> None:
    """TC2 — fixed I/O: io_pad at origin, 39 free blocks, 80 bus nets.

    In the GUI: Optimize → SA, 50 000 iterations, check Fixed for io_pad.
    io_pad stays at (0, 0); remaining blocks spread to minimise HPWL + overlap.
    """
    rng = random.Random(99)
    state = fpc.create_bdb(path, 3000.0, 2400.0, grid=10.0)

    io_w, io_h = 100.0, 80.0
    fpc.add_block(state, "io_pad", 0, 0, io_w, io_h)

    free_sizes = [(rng.randint(8, 30) * 10, rng.randint(5, 20) * 10) for _ in range(39)]
    free_names = [f"blk_{i:02d}" for i in range(39)]
    for name, (w, h) in zip(free_names, free_sizes):
        fpc.add_block(state, name, 0, 0, w, h)

    all_names = ["io_pad"] + free_names
    fpc.write_bdb(state)
    _add_buses(state, all_names, rng, n_buses=80)

    print(f"TC2 written to {path}")
    print(f"  40 blocks (io_pad + 39 free), 80 bus nets, die 3000×2400 grid=10")
    print(f"  Optimize → SA, 50 000 iter, check Fixed for io_pad.")


_DEMOS: dict = {"tc1": setup_tc1, "tc2": setup_tc2}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in _DEMOS:
        print(f"Usage: {os.path.basename(sys.argv[0])} <demo> [output.bdb]")
        print(f"  demo: {', '.join(_DEMOS)}")
        sys.exit(1)
    demo = sys.argv[1]
    path = sys.argv[2] if len(sys.argv) > 2 else f"/tmp/bfp_{demo}.bdb"
    _DEMOS[demo](path)


if __name__ == "__main__":
    main()
