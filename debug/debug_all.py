import sys
sys.path.insert(0, '/Users/ben/src/buda/buda_system_v2/src')
import interconnect as ic

fp = ic.Floorplan()
fp.add_block("src", 500, 150, 600, 250)
fp.add_block_rects("L", [(0,0,100,400), (0,0,400,100)], ic.TegMode.OVER)

gen = ic.TopologyGenerator(fp)
gen.set_busterm_mode(True)
gen.set_layer_ids(4, 5)
topos = gen.generate_candidates("src", ["L"])
UNC = 1_000_000_000

centered = [1, 2, 5, 9, 12]  # 1-indexed topos with centered constraint
for ti in centered:
    t = topos[ti-1]
    ct = ic.ConnTopology()
    ct.build(t, fp)
    segs = list(ct.segs())
    print(f"\nTopo {ti}: {t.type}")
    for i, cs in enumerate(segs):
        lo, hi = cs.perp_lo, cs.perp_hi
        lo_ok = abs(lo) < UNC
        hi_ok = abs(hi) < UNC
        dir_s = "H" if cs.horiz else "V"
        if lo_ok and hi_ok:
            ctr = (lo + hi) / 2.0
            pull = cs.perp_pos - ctr
            print(f"  seg[{i}] {dir_s} perp_pos={cs.perp_pos} interval=[{lo},{hi}] pull={pull:.1f}")
        for c in cs.conns:
            k = "BT" if c.kind == ic.SegConnKind.BUSTERM else "SEG"
            extra = f" block={c.block_name} face={c.face_coord}" if k == "BT" else f" seg_idx={c.seg_idx}"
            print(f"    {k}{extra} at_pos={c.at_pos}")

# Also check topo 8's H segment more carefully
print("\n\n--- Topo 8 detailed ---")
t = topos[7]
ct = ic.ConnTopology()
ct.build(t, fp)
segs = list(ct.segs())
for i, cs in enumerate(segs):
    lo, hi = cs.perp_lo, cs.perp_hi
    dir_s = "H" if cs.horiz else "V"
    ctr = (lo+hi)/2.0 if abs(lo)<UNC and abs(hi)<UNC else None
    pull = cs.perp_pos - ctr if ctr is not None else None
    plen_sign = "+" if pull is not None and pull > 1e-6 else ("-" if pull is not None and pull < -1e-6 else "0")
    print(f"  seg[{i}] {dir_s} perp_pos={cs.perp_pos} interval=[{lo},{hi}] ctr={ctr} pull={pull} plen_sign={plen_sign}")
    # Arrow semantics: from dp toward dp+plen (=dp+(pull_sign * interval_w/4))
    if pull is not None and abs(pull) > 1e-6:
        interval_w = hi - lo
        plen = max(interval_w/4, 20) * (1.0 if pull > 0 else -1.0)
        dp = ctr
        if cs.horiz:
            print(f"    -> H arrow: from (mid, {dp:.0f}) to (mid, {dp+plen:.0f}) [direction {'UP' if plen>0 else 'DOWN'}]")
        else:
            print(f"    -> V arrow: from ({dp:.0f}, mid) to ({dp+plen:.0f}, mid) [direction {'RIGHT' if plen>0 else 'LEFT'}]")
        print(f"    -> Pull means: segment at y/x={cs.perp_pos} wants to be pulled {'UP' if pull>0 else 'DOWN'} (toward y/x={ctr:.0f}+{pull:.0f} = {ctr+pull:.0f})")
