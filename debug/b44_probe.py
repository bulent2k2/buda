#!/usr/bin/env python3
"""Probe b44 cand 0: per-segment placed spans vs nominal vs the envelope's
joint-minimum positions. Where does the +1000 realization excess live?"""

# after pr#311
import sys, os
sys.path.insert(0, "/home/user/buda/build")
sys.path.insert(0, "/home/user/buda/src")
os.chdir("/home/user/buda/flow/big_data_test")
import buda
import buda_cli

def run(pin):
    s = buda_cli.BudaSession()
    s.no_viz = True
    for c in ["source ../tracks/tracks4top.buda",
              "add_block blk_07 2960 9750 4660 10250",
              "add_block blk_23 200 10830 2700 11830",
              "add_block io_pad_tl 200 12000 1200 12800",
              "add_bus bus_060[52] blk_23.p blk_07.p,io_pad_tl.p",
              "run_bundler", "generate_topologies"]:
        s.do_command(c)
    if pin is not None:
        s.do_command(f"select_topology 1 {pin}")
    s.do_command("run_planner")
    s.do_command("run_nuts")
    bw = s.bundles[0]
    topo = bw.candidates[bw.selected_topology_index]
    print(f"\n=== pinned={pin} selected idx {bw.selected_topology_index} type={topo.type} nominal WL={topo.estimated_wirelength}")
    total = 0
    for ts in s.nuts_result.segments:
        span = ts.span_hi - ts.span_lo
        total += span
        seg = topo.segments[ts.seg_idx]
        nom_lo = min(seg.start.x, seg.end.x) if ts.horiz else min(seg.start.y, seg.end.y)
        nom_hi = max(seg.start.x, seg.end.x) if ts.horiz else max(seg.start.y, seg.end.y)
        nom_span = nom_hi - nom_lo
        nom_perp = seg.start.y if ts.horiz else seg.start.x
        print(f"  seg{ts.seg_idx} L{ts.layer} {'H' if ts.horiz else 'V'} "
              f"placed span [{ts.span_lo:.0f},{ts.span_hi:.0f}] len={span:.0f} "
              f"(nominal len={nom_span} perp={nom_perp}) track={ts.track_position:.1f} "
              f"interval=[{ts.interval_lo:.0f},{ts.interval_hi:.0f}]")
        if abs(span - nom_span) > 1:
            print(f"        ^^ span delta {span - nom_span:+.0f}")
    print(f"  TOTAL placed WL = {total:.0f}")

run(None)   # planner's own choice (cand 0)
run(10)     # the L-topo (1-based 10 = idx 9)
