import argparse
import sys
import interconnect 
from buda_viz import BudaVisualizer

class BudaSession:
    def __init__(self):
        self.fp = interconnect.Floorplan()
        self.netlist = interconnect.Netlist()
        self.layers = interconnect.LayerStack()
        self.bundler = interconnect.Bundler()
        self.planner = None 
        self.bundles = [] 

    def do_command(self, cmd_line):
        parts = cmd_line.strip().split()
        if not parts: return
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd == "add_block":
            self.fp.add_block(args[0], int(args[1]), int(args[2]), int(args[3]), int(args[4]))
        elif cmd == "add_net":
            self.netlist.add_net(args[0], args[1], args[2].split(','))
        elif cmd == "def_layer":
            lid, name, dirstr, typestr, ovh = args
            ldir = interconnect.LayerDir.HORIZONTAL if dirstr.upper()=="H" else interconnect.LayerDir.VERTICAL
            ltype = interconnect.LayerType.TOP if typestr.upper()=="TOP" else interconnect.LayerType.LOW
            self.layers.add_layer(int(lid), name, ldir, ltype)
        elif cmd == "run_bundler":
            strat = interconnect.Strategy.STRICT
            if len(args) > 0 and args[0] == "convergent": strat = interconnect.Strategy.CONVERGENT
            self.bundler.set_strategy(strat)
            raw_bundles = self.bundler.run(self.netlist)
            self.bundles = []
            for b in raw_bundles:
                w = interconnect.BundleWrapper()
                w.original_bundle = b
                w.width = len(b.get_net_names()) * 2.0 
                self.bundles.append(w)
            print(f"Bundler created {len(self.bundles)} bundles.")
        elif cmd == "run_planner":
            self.planner = interconnect.GlobalRouter(self.fp, self.layers)
            topo_gen = interconnect.TopologyGenerator(self.fp)
            for w in self.bundles:
                src = w.original_bundle.get_net_names()[0].split('.')[0] 
                dst = "u_dst" # Hack for stress test demo
                # In real cli we'd extract from netlist
                w.candidates = topo_gen.generate_candidates("u_src", "u_dst")
            self.planner.build_congestion_map()
            self.planner.optimize_topologies(self.bundles, int(args[0]) if args else 5)
        elif cmd == "visualize":
            viz = BudaVisualizer(self.fp, self.bundles)
            viz.draw_blocks()
            viz.draw_hanan_grid()
            viz.draw_buses()
            viz.show()
        elif cmd == "source":
             with open(args[0], 'r') as f:
                 for line in f:
                     if not line.strip().startswith('#'): self.do_command(line)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('script', nargs='?')
    args = parser.parse_args()
    session = BudaSession()
    if args.script: session.do_command(f"source {args.script}")

if __name__ == "__main__":
    main()