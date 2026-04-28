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
        self.nuts_result = None

    def extract_instances(self, bundle):
        # Helper to find source/dest instances from a bundle's nets for Topology Generation
        if not bundle.get_net_names(): return "top", "top"
        # Hack: assume first net's driver/receiver pins follow instance.pin format
        first_net_name = bundle.get_net_names()[0]
        # Find this net in the netlist to get its pins. This is inefficient but works for prototype.
        # A real implementation would store src/dst instance on the Bundle object itself.
        driver_pin = ""
        receiver_pin = ""
        # C++ Netlist doesn't expose find_net yet, so we rely on the input script naming convention for the demo.
        # Assuming net name is like 'b1_0' and driver is 'u_cpu.tx'
        # Let's just pass the block names directly in the script for now to simplify the connection.
        return "top", "top"

    def do_command(self, cmd_line):
        parts = cmd_line.strip().split()
        if not parts or parts[0].startswith('#'): return
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
            self.bundler.set_strategy(interconnect.Strategy.STRICT)
            raw_bundles = self.bundler.run(self.netlist)
            self.bundles = []
            for b in raw_bundles:
                w = interconnect.BundleWrapper()
                w.original_bundle = b
                w.width = len(b.get_net_names()) * 3.0 # Simulating wide buses
                self.bundles.append(w)
            print(f"Bundler created {len(self.bundles)} bundles.")
        elif cmd == "generate_topologies_for_bundle":
            # Usage: generate_topologies_for_bundle <bundle_id_hint> <src_inst> <dst_inst>
            hint, src, dst = args
            topo_gen = interconnect.TopologyGenerator(self.fp)
            found = False
            for w in self.bundles:
                if w.original_bundle.get_net_names()[0].startswith(hint):
                    w.candidates = topo_gen.generate_candidates(src, dst)
                    print(f"Generated {len(w.candidates)} topologies for bundle {w.original_bundle.id} ({src}->{dst})")
                    found = True
            if not found: print(f"Warning: Could not find bundle matching hint {hint}")

        elif cmd == "run_planner":
            self.planner = interconnect.GlobalRouter(self.fp, self.layers)
            self.planner.build_congestion_map()
            self.planner.optimize_topologies(self.bundles, int(args[0]) if args else 5)
        elif cmd == "run_nuts":
            # Usage: run_nuts [track_pitch]
            pitch = float(args[0]) if args else 1.0
            nuts = interconnect.NUTSEngine(self.fp)
            nuts.set_track_pitch(pitch)
            self.nuts_result = nuts.run(self.bundles)
            print(f"NUTS placed {len(self.nuts_result.segments)} segments "
                  f"({self.nuts_result.num_violations} interval violations, "
                  f"{self.nuts_result.num_overlaps} track overlaps).")
        elif cmd == "visualize":
            viz = BudaVisualizer(self.fp, self.bundles)
            viz.draw_blocks()
            viz.draw_hanan_grid()
            if self.nuts_result is not None:
                viz.draw_nuts_tracks(self.nuts_result)
            else:
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