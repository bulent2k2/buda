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

def seg_pull(cs, cs_list):
    """Sum of (BT.face_coord - cs.perp_pos) for each BT in each SEG-connected neighbour."""
    total = 0.0
    for conn in cs.conns:
        if conn.kind != ic.SegConnKind.SEG:
            continue
        nb = cs_list[conn.seg_idx]
        for sc in nb.conns:
            if sc.kind != ic.SegConnKind.BUSTERM:
                continue
            total += sc.face_coord - cs.perp_pos
    return total

print(f"{'Topo':40s}  {'seg':4s}  {'dir':2s}  {'perp_pos':8s}  {'pull_new':10s}  {'arrow':8s}")
print("-"*80)
for idx, t in enumerate(topos):
    ct = ic.ConnTopology()
    ct.build(t, fp)
    segs = list(ct.segs())
    for i, cs in enumerate(segs):
        p = seg_pull(cs, segs)
        d = "H" if cs.horiz else "V"
        lo, hi = cs.perp_lo, cs.perp_hi
        lo_ok = abs(lo) < UNC
        hi_ok = abs(hi) < UNC
        iw = (hi-lo) if (lo_ok and hi_ok) else 0
        MIN = 20
        if abs(p) > 1e-6:
            plen = max(iw/4, MIN) * (1 if p > 0 else -1)
            if d == "H":
                arr = "UP" if plen>0 else "DOWN"
            else:
                arr = "RIGHT" if plen>0 else "LEFT"
        else:
            arr = "(none)"
        print(f"  {t.type:38s}  s[{i}]  {d}   pp={cs.perp_pos:5d}   pull={p:+8.0f}   {arr}")
