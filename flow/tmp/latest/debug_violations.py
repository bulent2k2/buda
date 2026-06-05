import sys
sys.path.insert(0, '/Users/ben/src/buda/buda_system_v2/src')
import interconnect

def run_two():
    fp = interconnect.Floorplan()
    ls = interconnect.LayerStack()
    ls.add_layer(4, "M4", interconnect.LayerDir.HORIZONTAL, interconnect.LayerType.TOP)
    ls.add_layer(5, "M5", interconnect.LayerDir.VERTICAL, interconnect.LayerType.TOP)
    
    blocks = [
        ("u0",   200, 400, 300,  600, 15, 20),
        ("u11",  500, 800, 550, 1000, 15, 20),
        ("u12",  550, 600, 600, 1000, 15, 20),
        ("u13",  600, 500, 650, 1000, 15, 20),
        ("u14",  650, 400, 700, 1000, 15, 20),
        ("u15",  700, 200, 750, 1000, 15, 20),
        ("u21", -150, 500, -100, 600, 15, 20),
        ("u22", -100, 400,  -50, 600, 15, 20),
        ("u23",  -50, 300,    0, 600, 15, 20),
        ("u31",    0, 450,   50, 550, 15, 20),
        ("u32",   50, 400,  100, 550, 15, 20),
        ("u33",  100, 350,  150, 550, 15, 20),
        ("u41",  400, 200,  500, 400, 15, 20),
        ("u51",  500, 100,  600, 300, 15, 20),
    ]
    for name, x1, y1, x2, y2, dx, dy in blocks:
        fp.add_block(name, x1, y1, x2, y2)
        fp.set_block_corner_margin(name, dx, dy)

    nl = interconnect.Netlist()
    nets = [
        ("n11","u0.tx",["u11.rx"]), ("n12","u0.tx",["u12.rx"]), ("n13","u0.tx",["u13.rx"]),
        ("n14","u0.tx",["u14.rx"]), ("n15","u0.tx",["u15.rx"]),
        ("n21","u0.tx",["u21.rx"]), ("n22","u0.tx",["u22.rx"]), ("n23","u0.tx",["u23.rx"]),
        ("n31","u0.tx",["u31.rx"]), ("n32","u0.tx",["u32.rx"]), ("n33","u0.tx",["u33.rx"]),
        ("n41","u0.tx",["u41.rx"]), ("n51","u0.tx",["u51.rx"]),
    ]
    for name, drv, rcv in nets:
        nl.add_net(name, drv, rcv)

    bundler = interconnect.Bundler()
    bundler.set_strategy(interconnect.Strategy.STRICT)
    bundles = bundler.run(nl)
    
    tg = interconnect.TopologyGenerator(fp, ls)
    wrappers = []
    for b in bundles:
        bw = interconnect.BundleWrapper(b, 1.5, fp, nl)
        tg.generate_candidates(bw)
        wrappers.append(bw)

    gr = interconnect.GlobalRouter(ls, fp)
    gr.optimize_topologies(wrappers, fp)

    solver = interconnect.NUTSSolver(ls)
    result = solver.solve(wrappers, fp)
    
    print(f"Total violations={result.num_violations}, overlaps={result.num_overlaps}")
    print(f"Total segments: {len(result.segments)}")
    
    viol_count = 0
    for ts in result.segments:
        lo, hi = ts.interval_lo, ts.interval_hi
        pos = ts.track_position
        if pos < lo - 0.001 or pos > hi + 0.001:
            print(f"VIOLATION bid={ts.bundle_id} seg={ts.seg_idx} layer={ts.layer} pos={pos:.1f} interval=[{lo:.1f},{hi:.1f}] span=[{ts.span_lo},{ts.span_hi}]")
            viol_count += 1
    
    print(f"\nManual violation count: {viol_count}")
    print("\n--- All segments ---")
    for ts in sorted(result.segments, key=lambda x: (x.bundle_id, x.seg_idx)):
        lo, hi = ts.interval_lo, ts.interval_hi
        pos = ts.track_position
        flag = " *** VIOLATION ***" if (pos < lo - 0.001 or pos > hi + 0.001) else ""
        print(f"  bid={ts.bundle_id} seg={ts.seg_idx} layer={ts.layer} pos={pos:.1f} interval=[{lo:.1f},{hi:.1f}] span=[{ts.span_lo},{ts.span_hi}]{flag}")

run_two()
