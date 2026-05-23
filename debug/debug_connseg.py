import sys
sys.path.insert(0, '/Users/ben/src/buda/buda_system_v2/src')
import interconnect as ic

fp = ic.Floorplan()
fp.add_block("src", 500, 150, 600, 250)
fp.add_block_rects("L", [(0,0,100,400), (0,0,400,100)], ic.TegMode.OVER)

gen = ic.TopologyGenerator(fp)
gen.set_busterm_mode(True)
gen.set_layer_ids(4, 5)  # H=M4, V=M5

topos = gen.generate_candidates("src", ["L"])

print(f"Total topologies: {len(topos)}")
print("--- All topo types ---")
for i, t in enumerate(topos):
    print(f"  {i+1}: {t.type}")

def dump_topo(idx, t):
    print(f"\n=== Topo {idx+1}: {t.type} ===")
    ct = ic.ConnTopology()
    ct.build(t, fp)
    segs = list(ct.segs())
    UNC = 1_000_000_000
    for i, cs in enumerate(segs):
        dir_s = "H" if cs.horiz else "V"
        plo = cs.perp_lo if abs(cs.perp_lo) < UNC else "−∞"
        phi = cs.perp_hi if abs(cs.perp_hi) < UNC else "+∞"
        print(f"  seg[{i}] {dir_s}  along=[{cs.along_lo},{cs.along_hi}]  perp_pos={cs.perp_pos}  perp=[{plo},{phi}]")
        for c in cs.conns:
            if c.kind == ic.SegConnKind.BUSTERM:
                print(f"    BUSTERM -> {c.block_name}  face_coord={c.face_coord}")
            else:
                print(f"    SEG -> seg[{c.seg_idx}]  at_pos={c.at_pos}")

for idx in [1, 7]:  # topo 2 and topo 8 (0-indexed)
    if idx < len(topos):
        dump_topo(idx, topos[idx])
    else:
        print(f"\n(topo {idx+1} doesn't exist, only {len(topos)} topologies)")
