import argparse
import sys
import interconnect 
from buda_viz import BudaVisualizer, TopologyExplorer

class BudaSession:
    def __init__(self):
        self.fp = interconnect.Floorplan()
        self.netlist = interconnect.Netlist()
        self.layers = interconnect.LayerStack()
        self.bundler = interconnect.Bundler()
        self.planner = None
        self.bundles = []
        self.nuts_result = None
        self._layer_overheads = {}   # layer_id -> overhead_percent

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
        elif cmd == "add_bus":
            # Syntax: add_bus <prefix>[<N>] <drv_pin> <rcv_pin>
            #      or add_bus <prefix>[<lo>:<hi>] <drv_pin> <rcv_pin>
            # Expands to add_net calls: <prefix>_<lo> … <prefix>_<hi>
            import re
            m = re.match(r'^(.+)\[(\d+)(?::(\d+))?\]$', args[0])
            if not m:
                print(f"Error: bad add_bus syntax '{args[0]}' — expected name[N] or name[lo:hi]")
                return
            prefix = m.group(1)
            lo = int(m.group(2))
            hi = int(m.group(3)) if m.group(3) is not None else lo - 1
            if m.group(3) is None:      # name[N]  → indices 0 … N-1
                lo, hi = 0, int(m.group(2)) - 1
            drv_pin  = args[1]
            rcv_pins = args[2].split(',')
            for i in range(lo, hi + 1):
                self.netlist.add_net(f"{prefix}_{i}", drv_pin, rcv_pins)
        elif cmd == "def_layer":
            lid, name, dirstr, typestr, ovh = args
            ldir = interconnect.LayerDir.HORIZONTAL if dirstr.upper()=="H" else interconnect.LayerDir.VERTICAL
            ltype = interconnect.LayerType.TOP if typestr.upper()=="TOP" else interconnect.LayerType.LOW
            self.layers.add_layer(int(lid), name, ldir, ltype)
            ovh_val = float(ovh)
            if ovh_val > 0.0:
                self._layer_overheads[int(lid)] = ovh_val
        elif cmd == "run_bundler":
            self.bundler.set_strategy(interconnect.Strategy.STRICT)
            raw_bundles = self.bundler.run(self.netlist)
            self.bundles = []
            for b in raw_bundles:
                w = interconnect.BundleWrapper()
                w.original_bundle = b
                w.width = len(b.get_net_names()) * 1.5 # 1.5 layout-units per bit
                self.bundles.append(w)
            print(f"Bundler created {len(self.bundles)} bundles.")
        elif cmd == "generate_topologies_for_bundle":
            # Usage: generate_topologies_for_bundle <hint> <src> <dst> [<dst2> ...] [center_mode]
            # Single dst  → 2-pin L/Z/U candidates
            # Multiple dst → multicast trunk+branch candidates
            # Append "center_mode" as last arg to use block centres instead of busterm faces.
            use_center = args and args[-1] == "center_mode"
            pos_args = args[:-1] if use_center else args
            hint = pos_args[0]; src = pos_args[1]; dsts = pos_args[2:]
            topo_gen = interconnect.TopologyGenerator(self.fp)
            if use_center:
                topo_gen.set_busterm_mode(False)
            found = False
            for w in self.bundles:
                if w.original_bundle.get_net_names()[0].startswith(hint):
                    if len(dsts) == 1:
                        w.candidates = topo_gen.generate_candidates(src, dsts[0])
                        label = f"{src}->{dsts[0]}"
                    else:
                        w.candidates = topo_gen.generate_multicast_candidates(src, dsts)
                        label = f"{src}->[{','.join(dsts)}]"
                    print(f"Generated {len(w.candidates)} topologies for bundle "
                          f"{w.original_bundle.id} ({label})")
                    found = True
            if not found: print(f"Warning: Could not find bundle matching hint {hint}")

        elif cmd == "run_planner":
            self.planner = interconnect.GlobalRouter(self.fp, self.layers)
            for lid, ovh in self._layer_overheads.items():
                self.planner.set_layer_overhead(lid, ovh)
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
        elif cmd == "visualize_topologies":
            # Usage: visualize_topologies <bundle_hint>
            # Opens the TopologyExplorer for the matching bundle so you can
            # step through every candidate in wirelength order.
            hint = args[0] if args else ""
            for w in self.bundles:
                if not w.candidates: continue
                if w.original_bundle.get_net_names()[0].startswith(hint):
                    print(f"Opening explorer for bundle {w.original_bundle.id} "
                          f"({len(w.candidates)} topologies, sorted by WL)")
                    TopologyExplorer(self.fp, w).show()
                    return
            print(f"Warning: no bundle with candidates matching hint '{hint}'")
        elif cmd == "visualize":
            viz = BudaVisualizer(self.fp, self.bundles)
            viz.draw_blocks()
            if self.planner is not None:
                cuts = self.planner.get_cuts()
                if cuts:
                    viz.draw_congestion_map(cuts)
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