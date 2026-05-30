import sys, os
sys.path.insert(0, '/Users/ben/src/buda/buda_system_v2/src')
import interconnect as ic

# Reproduce bundle 13: u0→u51 (L_HV topology)
# u0: (200,400)-(300,600), u51: (500,100)-(600,300)
# L_HV: seg0 H=(300,400)-(500,400), seg1 V=(500,400)-(500,300)

fp = ic.Floorplan()
fp.add_block("u0",   200, 400, 300, 600); fp.set_block_corner_margin("u0",  15, 20)
fp.add_block("u41",  400, 200, 500, 400); fp.set_block_corner_margin("u41", 15, 20)
fp.add_block("u51",  500, 100, 600, 300); fp.set_block_corner_margin("u51", 15, 20)

ls = ic.LayerStack()
ls.add_layer(4, "M4", ic.LayerDir.HORIZONTAL, ic.LayerType.TOP)
ls.add_layer(5, "M5", ic.LayerDir.VERTICAL,   ic.LayerType.TOP)

tg = ic.TopologyGenerator(fp)
tg.set_layer_ids(4, 5)
candidates = tg.generate_candidates("u0", "u51")
for i, c in enumerate(candidates):
    print(f"  {i}: {c.type} segs={[(s.start.x,s.start.y,s.end.x,s.end.y) for s in c.segments]}")

# Use L_HV topo (should be candidates[0])
lhv = None
for c in candidates:
    if c.type == "L_HV":
        lhv = c
        break
        
if lhv:
    print(f"\nL_HV topo:")
    for i, seg in enumerate(lhv.segments):
        bt = lhv.seg_busterms.get(i)
        ep0 = bt[0].block_name if bt and bt[0] else None
        ep1 = bt[1].block_name if bt and bt[1] else None
        print(f"  seg {i}: ({seg.start.x},{seg.start.y})-({seg.end.x},{seg.end.y}) layer={seg.layer_hint}")
        print(f"    busterms: ep0={ep0} ep1={ep1}")
    
    # Build ConnTopology
    ct = ic.ConnTopology()
    ct.build(lhv, fp)
    for i, cs in enumerate(ct.segs()):
        print(f"\n  ConnSeg {i}: horiz={cs.horiz} along=[{cs.along_lo},{cs.along_hi}] perp_pos={cs.perp_pos}")
        print(f"    slide=[{cs.perp_lo},{cs.perp_hi}]")
        for conn in cs.conns:
            print(f"    conn: kind={conn.kind} block={getattr(conn,'block_name','N/A')} seg_idx={conn.seg_idx}")
