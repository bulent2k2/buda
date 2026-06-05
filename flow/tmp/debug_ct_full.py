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

import interconnect as ic

with open("/Users/ben/src/buda/buda_system_v2/flow/two.buda", 'r') as f:
    for line in f:
        stripped = line.strip()
        if stripped.startswith('#') or not stripped: continue
        if stripped.startswith('run_nuts') or stripped.startswith('visualize'): break
        session.do_command(line)

# Find bundle 13 and compute ConnTopology
for w in session.bundles:
    if w.original_bundle.id == 13:
        topo = w.candidates[w.selected_topology_index]
        ct = ic.ConnTopology()
        ct.build(topo, session.fp)
        print(f"Bundle 13 selected topo: {topo.type}")
        for i, cs in enumerate(ct.segs()):
            print(f"  ConnSeg {i}: horiz={cs.horiz} along=[{cs.along_lo},{cs.along_hi}] perp_pos={cs.perp_pos}")
            print(f"    slide=[{cs.perp_lo},{cs.perp_hi}]")
            for conn in cs.conns:
                bn = getattr(conn, 'block_name', '')
                print(f"    conn: kind={conn.kind} block={bn} seg_idx={conn.seg_idx}")
