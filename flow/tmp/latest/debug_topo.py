import sys, os
sys.path.insert(0, '/Users/ben/src/buda/buda_system_v2/src')
os.chdir('/Users/ben/src/buda/buda_system_v2/src')

import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt; plt.show = lambda: None

import importlib.util
spec = importlib.util.spec_from_file_location("buda_cli", "buda_cli.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

session = mod.BudaSession()
session.script_path = "/Users/ben/src/buda/buda_system_v2/flow/two.buda"
with open("/Users/ben/src/buda/buda_system_v2/flow/two.buda", 'r') as f:
    for line in f:
        stripped = line.strip()
        if stripped.startswith('#') or not stripped: continue
        if stripped.startswith('visualize'): break
        session.do_command(line)

# Find bundle 13
for w in session.bundles:
    if w.original_bundle.id == 13:
        idx = w.selected_topology_index
        topo = w.candidates[idx]
        print(f"Bundle 13 selected topo idx={idx} type={topo.type}")
        for i, seg in enumerate(topo.segments):
            print(f"  seg {i}: ({seg.start.x},{seg.start.y})->({seg.end.x},{seg.end.y}) layer={seg.layer_hint}")
            bt = topo.seg_busterms.get(i)
            if bt:
                ep0 = bt[0].block_name if bt[0] else None
                ep1 = bt[1].block_name if bt[1] else None
                print(f"    busterms: ep0={ep0} ep1={ep1}")
        print("\nAll candidates:")
        for ci, c in enumerate(w.candidates):
            print(f"  {ci}: {c.type} segs={[(s.start.x,s.start.y,s.end.x,s.end.y) for s in c.segments]}")
# Also debug bundle 9
print("\n=== Bundle 9 ===")
for w in session.bundles:
    if w.original_bundle.id == 9:
        topo = w.candidates[w.selected_topology_index]
        print(f"Type: {topo.type}, nets: {w.original_bundle.get_net_names()}")
        for i, seg in enumerate(topo.segments):
            print(f"  seg {i}: ({seg.start.x},{seg.start.y})->({seg.end.x},{seg.end.y}) layer={seg.layer_hint}")
            bt = topo.seg_busterms.get(i)
            if bt:
                ep0 = bt[0].block_name if bt[0] else None
                ep1 = bt[1].block_name if bt[1] else None
                print(f"    busterms: ep0={ep0} ep1={ep1}")
        ct = ic.ConnTopology()
        ct.build(topo, session.fp)
        for i, cs in enumerate(ct.segs()):
            print(f"  ConnSeg {i}: horiz={cs.horiz} perp_pos={cs.perp_pos} slide=[{cs.perp_lo},{cs.perp_hi}]")
