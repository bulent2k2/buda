#!/usr/bin/env python3
"""
tools/fp_demo.py — Generate demo BDB scenarios for the bfp launcher.

Usage:
  python3 tools/fp_demo.py tc1 [/tmp/bfp_tc1.bdb]
  python3 tools/fp_demo.py tc2 [/tmp/bfp_tc2.bdb]
  python3 tools/fp_demo.py tc3 [/tmp/bfp_tc3.bdb]

TC1 — overlap storm: 40 varied-size blocks all stacked at (0, 0), connected
      by 80 buses.  In the GUI: Optimize → SA, 50 000 iter, weights default.

TC2 — fixed I/O: io_pad at origin, 39 free blocks, 80 buses.
      In the GUI: Optimize → SA, 50 000 iter, check Fixed for io_pad.

TC3 — multi-pad I/O: 4 io_pads at die corners (fixed), 40 free blocks,
      80 buses with fanout 2–6 (Normal μ=4) and bit widths from 4–60.
      In the GUI: Optimize → SA, 50 000 iter, check Fixed for all io_pads.
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
    """Write n_buses undirected 1-bit bus nets to the BDB (components must be in DB already).

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


def _add_buses_multipin(state, names, io_names, rng, n_buses: int) -> None:
    """Write n_buses multi-pin buses with varying fanout and bit widths.

    Fanout per bus: 2–6 endpoints, roughly Normal(μ=4, σ=1.2), clamped.
    Bit widths: random choice from range(4, 64, 4).
    io_pad guarantee: each name in io_names appears in ≥4 buses.
    Tail guarantee: ≥10 buses at fanout=2 and ≥10 buses at fanout=6.
    """
    bit_widths = list(range(4, 64, 4))  # [4, 8, 12, ..., 60]
    n_free = len(names)
    n_io = len(io_names)
    all_names = io_names + names

    # --- generate fanouts ---
    raw_fanouts = [max(2, min(6, round(rng.gauss(4.0, 1.2)))) for _ in range(n_buses)]

    # enforce tails: ≥10 buses at fanout=2 and ≥10 at fanout=6
    count2 = raw_fanouts.count(2)
    count6 = raw_fanouts.count(6)
    mid_indices = [i for i, f in enumerate(raw_fanouts) if f in (3, 5)]
    rng.shuffle(mid_indices)
    mi = iter(mid_indices)
    while count2 < 10:
        idx = next(mi, None)
        if idx is None:
            break
        if raw_fanouts[idx] == 3:
            raw_fanouts[idx] = 2
            count2 += 1
    while count6 < 10:
        idx = next(mi, None)
        if idx is None:
            break
        if raw_fanouts[idx] == 5:
            raw_fanouts[idx] = 6
            count6 += 1

    # --- track io_pad coverage ---
    io_bus_count = {name: 0 for name in io_names}

    # --- build endpoint lists ---
    bus_endpoints: list[list[str]] = []
    for fanout in raw_fanouts:
        # pick random free blocks
        chosen = rng.sample(names, min(fanout, n_free))
        if len(chosen) < fanout:
            chosen = [names[rng.randint(0, n_free - 1)] for _ in range(fanout)]
        bus_endpoints.append(list(dict.fromkeys(chosen)) or [names[0]])  # dedup, keep order

    # --- guarantee each io_pad appears in ≥4 buses ---
    # for buses that need more coverage, swap one free endpoint for the io_pad
    for io_name in io_names:
        # find buses already containing this io_pad
        for i, eps in enumerate(bus_endpoints):
            if io_name in eps:
                io_bus_count[io_name] += 1
        needed = max(0, 4 - io_bus_count[io_name])
        if needed == 0:
            continue
        # pick buses that don't already include this io_pad, preferring larger fanout
        candidates = [i for i, eps in enumerate(bus_endpoints) if io_name not in eps]
        rng.shuffle(candidates)
        for i in candidates[:needed]:
            # replace last free endpoint with io_pad (keep fanout count the same)
            eps = bus_endpoints[i]
            # only replace a free block (not another io_pad)
            for j in range(len(eps) - 1, -1, -1):
                if eps[j] not in io_names:
                    eps[j] = io_name
                    io_bus_count[io_name] += 1
                    break
            else:
                # all endpoints are io_pads — append (increases fanout by 1)
                eps.append(io_name)
                io_bus_count[io_name] += 1

    # --- write nets ---
    for bus_idx, eps in enumerate(bus_endpoints):
        bits = rng.choice(bit_widths)
        for bit in range(bits):
            state.bdb.add_net_pins_undirected(
                f"bus_{bus_idx:03d}_b{bit:02d}",
                eps,
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


def setup_tc3(path: str) -> None:
    """TC3 — multi-pad I/O: 4 io_pads at die corners, 40 free blocks, 80 buses.

    io_pads are placed at the four die corners and should be marked Fixed.
    Each io_pad participates in ≥4 buses.  Buses have fanout 2–6 (Normal μ=4)
    and bit widths drawn from range(4, 64, 4).  At least 10 buses have fanout=2
    and at least 10 have fanout=6 (populated tails).

    In the GUI: Optimize → SA, 50 000 iter, check Fixed for all four io_pads.
    """
    rng = random.Random(13)
    state = fpc.create_bdb(path, 3000.0, 2400.0, grid=10.0)

    # io_pads at die corners (100×80 each)
    io_w, io_h = 100.0, 80.0
    corner_margin = 0.0  # placed flush at corners; optimizer keeps them fixed
    die_w, die_h = 3000.0, 2400.0
    io_pads = [
        ("io_pad_bl", 0.0,               0.0),
        ("io_pad_br", die_w - io_w,      0.0),
        ("io_pad_tl", 0.0,               die_h - io_h),
        ("io_pad_tr", die_w - io_w,      die_h - io_h),
    ]
    for name, x, y in io_pads:
        fpc.add_block(state, name, x, y, io_w, io_h)

    # 40 free blocks all starting at origin
    free_sizes = [(rng.randint(8, 30) * 10, rng.randint(5, 20) * 10) for _ in range(40)]
    free_names = [f"blk_{i:02d}" for i in range(40)]
    for name, (w, h) in zip(free_names, free_sizes):
        fpc.add_block(state, name, 0, 0, w, h)

    fpc.write_bdb(state)

    io_names = [name for name, _, _ in io_pads]
    _add_buses_multipin(state, free_names, io_names, rng, n_buses=80)

    n_nets = sum(
        1 for net in state.bdb.all_nets()
    ) if hasattr(state.bdb, "all_nets") else "~"
    print(f"TC3 written to {path}")
    print(f"  4 io_pads at corners + 40 free blocks, die 3000×2400 grid=10")
    print(f"  80 buses × mixed bit widths (4–60 bits), fanout 2–6 (Normal μ=4)")
    print(f"  Optimize → SA, 50 000 iter, mark all io_pads Fixed.")


_DEMOS: dict = {"tc1": setup_tc1, "tc2": setup_tc2, "tc3": setup_tc3}


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
