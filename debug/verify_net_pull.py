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

# Check key topos 2 and 8 using C++ cs.net_pull
for idx, label in [(1, "Topo 2"), (7, "Topo 8")]:
    t = topos[idx]
    ct = ic.ConnTopology()
    ct.build(t, fp)
    segs = list(ct.segs())
    print(f"\n{label}: {t.type}")
    for i, cs in enumerate(segs):
        d = "H" if cs.horiz else "V"
        arr = ("UP" if cs.net_pull > 0 else "DOWN") if d=="H" else ("RIGHT" if cs.net_pull > 0 else "LEFT") if cs.net_pull != 0 else "(none)"
        print(f"  s[{i}] {d}  perp_pos={cs.perp_pos}  net_pull={cs.net_pull}  arrow={arr}")
