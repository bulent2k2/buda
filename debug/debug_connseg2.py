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

def dump_topo(idx):
    t = topos[idx]
    print(f"\n=== Topo {idx+1}: {t.type} ===")
    ct = ic.ConnTopology()
    ct.build(t, fp)
    segs = list(ct.segs())
    for i, cs in enumerate(segs):
        dir_s = "H" if cs.horiz else "V"
        plo = cs.perp_lo if abs(cs.perp_lo) < UNC else "−∞"
        phi = cs.perp_hi if abs(cs.perp_hi) < UNC else "+∞"
        center = (cs.perp_lo + cs.perp_hi) / 2.0 if abs(cs.perp_lo) < UNC and abs(cs.perp_hi) < UNC else "N/A"
        pull = (cs.perp_pos - center) if isinstance(center, float) else "N/A"
        print(f"  seg[{i}] {dir_s}  along=[{cs.along_lo},{cs.along_hi}]")
        print(f"    perp_pos={cs.perp_pos}  interval=[{plo},{phi}]  center={center}  pull={pull}")
        for c in cs.conns:
            if c.kind == ic.SegConnKind.BUSTERM:
                print(f"    BUSTERM -> {c.block_name}  face_coord={c.face_coord}")
            else:
                print(f"    SEG -> seg[{c.seg_idx}]  at_pos={c.at_pos}")

# Dump all topos to find arrows
for idx in range(len(topos)):
    t = topos[idx]
    ct = ic.ConnTopology()
    ct.build(t, fp)
    segs = list(ct.segs())
    issues = []
    for i, cs in enumerate(segs):
        lo, hi = cs.perp_lo, cs.perp_hi
        lo_ok = abs(lo) < UNC
        hi_ok = abs(hi) < UNC
        if lo_ok and hi_ok:
            center = (lo + hi) / 2.0
            pull = cs.perp_pos - center
            has_arrow = abs(pull) > 1e-6
        elif lo_ok or hi_ok:
            has_arrow = True
        else:
            has_arrow = False
        if not has_arrow and (lo_ok or hi_ok):
            issues.append(f"seg[{i}] CENTERED (no arrow, but constrained)")
    if issues:
        print(f"Topo {idx+1} {t.type}: {'; '.join(issues)}")

print("\n--- Detailed dump of topos 2 and 8 ---")
dump_topo(1)  # topo 2
dump_topo(7)  # topo 8
