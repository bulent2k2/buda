import sys
sys.path.insert(0, '/Users/ben/src/buda/buda_system_v2/src')
import interconnect

fp = interconnect.Floorplan()
fp.set_global_corner_margin(15, 20)   # dx=15, dy=20
fp.add_block("u0",  200, 400, 300,  600)
fp.add_block("u11", 500, 800, 550, 1000)

gen = interconnect.TopologyGenerator(fp)
gen.set_layer_ids(4, 5)
topos = gen.generate_candidates("u0", "u11")

print(f"u0  : (200,400)-(300,600)  expected H-face slide [420,580]  V-face slide [215,285]")
print(f"u11 : (500,800)-(550,1000) expected H-face slide [820,980]  V-face slide [515,535]")
print()

for i, t in enumerate(topos):
    ct = interconnect.ConnTopology()
    ct.build(t, fp)
    segs = ct.segs()
    print(f"[{i}] {t.type}  (wl={t.estimated_wirelength})")
    for si, cs in enumerate(segs):
        conns = [(c.block_name if c.kind == interconnect.SegConnKind.BUSTERM else f"seg{c.seg_idx}") for c in cs.conns]
        print(f"     seg{si} {'H' if cs.horiz else 'V'}  perp_pos={cs.perp_pos}  slide=[{cs.perp_lo},{cs.perp_hi}]  conns={conns}")
        print()
