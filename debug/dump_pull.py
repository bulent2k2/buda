"""Headless probe: run a .buda flow (sans visualize) and dump net_pull per ConnSeg."""
import sys, os

_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(_root, 'build'))
sys.path.insert(0, os.path.join(_root, 'src'))

import buda
from buda_cli import BudaSession

def main(script_path):
    sess = BudaSession()
    with open(script_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            cmd = line.split()[0]
            if cmd.startswith('visualize'):
                continue
            sess.do_command(line)

    type_filter = sys.argv[2] if len(sys.argv) > 2 else None
    for w in sess.bundles:
        if type_filter:
            topos = [c for c in w.candidates if c.type.startswith(type_filter)]
        elif w.selected_topology_index >= 0:
            topos = [w.candidates[w.selected_topology_index]]
        else:
            topos = []
        for topo in topos:
            dump_topo(w, topo, sess)

def dump_topo(w, topo, sess):
        ct = buda.ConnTopology()
        ct.build(topo, sess.fp)
        b = w.original_bundle
        print(f"\n=== bundle {b.id} topo={topo.type} ===")
        for i, cs in enumerate(ct.segs()):
            d = 'H' if cs.horiz else 'V'
            conns = []
            for c in cs.conns:
                if c.kind == buda.SegConnKind.BUSTERM:
                    conns.append(f"BT({c.block_name}@face{c.face_coord})")
                else:
                    conns.append(f"SEG({c.seg_idx}@{c.at_pos})")
            print(f"  seg{i} {d} along[{cs.along_lo},{cs.along_hi}] perp={cs.perp_pos} "
                  f"slide[{cs.perp_lo},{cs.perp_hi}] net_pull={cs.net_pull} conns={conns}")

if __name__ == '__main__':
    main(sys.argv[1])
