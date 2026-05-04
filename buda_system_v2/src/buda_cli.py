import argparse
import json
import os
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
        self.script_path = None      # set when a .buda script is sourced

    def _sidecar_path(self):
        """Return the .selections.json path for the current script, or None."""
        if not self.script_path:
            return None
        base = os.path.splitext(self.script_path)[0]
        return base + '.selections.json'

    def _apply_selections(self):
        """Load the sidecar and override selected_topology_index for pinned bundles.

        Selected bundles are processed first by the real planner (future work).
        For now we simply force the topology index after optimize_topologies runs.
        """
        path = self._sidecar_path()
        if not path or not os.path.exists(path):
            return
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception as e:
            print(f"Warning: could not read selections sidecar: {e}")
            return

        for sel in data.get('selections', []):
            hint = sel['bundle_hint']
            matched_w = None
            for w in self.bundles:
                names = w.original_bundle.get_net_names()
                if names and names[0] == hint:
                    matched_w = w
                    break
            if matched_w is None:
                print(f"Warning: no bundle found for hint '{hint}' — skipping")
                continue

            # Prefer stable (type, wl) match; fall back to stored index hint.
            resolved = None
            for i, cand in enumerate(matched_w.candidates):
                if (cand.type == sel['topo_type'] and
                        cand.estimated_wirelength == sel['topo_wl']):
                    resolved = i
                    break
            if resolved is None:
                idx_hint = sel.get('topo_index_hint', -1)
                if 0 <= idx_hint < len(matched_w.candidates):
                    resolved = idx_hint
                    print(f"Warning: selection for '{hint}' matched by index hint "
                          f"(type/WL changed?) — using topo {resolved}")
                else:
                    print(f"Warning: selection for '{hint}' could not be resolved — ignored")
                    continue

            matched_w.selected_topology_index = resolved
            print(f"Pinned bundle '{hint}' to topology {resolved} "
                  f"({sel['topo_type']}, WL={sel['topo_wl']})")

    def _stretch_nuts_segments(self):
        """Post-NUTS span stretch: extend each segment's span to physically
        overlap every segment it is logically connected to.

        NUTS assigns track_position but leaves span_lo/hi at the original
        topology coordinates.  After placement, connected segments may have
        shifted perpendicularly so their spans no longer overlap.  For each
        SEG-to-SEG connection we extend both spans to include the center of
        the connected segment's placed track.

        BUSTERM connections are not stretched: the topology already terminates
        at the block face, and ConnTopology perp constraints keep the stub
        track within the face extent.
        """
        ts_map = {(ts.bundle_id, ts.seg_idx): ts
                  for ts in self.nuts_result.segments}

        stretch_count = 0
        for w in self.bundles:
            if not w.candidates:
                continue
            bid  = w.original_bundle.id
            topo = w.candidates[w.selected_topology_index]

            ct = interconnect.ConnTopology()
            ct.build(topo, self.fp)

            for seg_idx, (raw_seg, cs) in enumerate(
                    zip(topo.segments, ct.segs())):
                ts = ts_map.get((bid, seg_idx))
                if ts is None or not ts.placed:
                    continue

                for conn in cs.conns:
                    if conn.kind != interconnect.SegConnKind.SEG:
                        continue
                    other = ts_map.get((bid, conn.seg_idx))
                    if other is None or not other.placed:
                        continue

                    # other is perpendicular; its track centre is a coordinate
                    # along our span direction — stretch to include it.
                    other_centre = other.track_position + other.width / 2.0
                    new_lo = min(ts.span_lo, other_centre)
                    new_hi = max(ts.span_hi, other_centre)
                    if new_lo != ts.span_lo or new_hi != ts.span_hi:
                        ts.span_lo = new_lo
                        ts.span_hi = new_hi
                        stretch_count += 1

        return stretch_count

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
            # Usage: generate_topologies_for_bundle <hint> <src> <dst> [<dst2> ...] [center_mode] [double_detour]
            # Single dst  → 2-pin L/Z/U candidates
            # Multiple dst → multicast trunk+branch candidates
            # Append "center_mode"    to use block centres instead of busterm faces.
            # Append "double_detour"  to include UU_VHV / UU_HVH high-congestion variants.
            use_center        = "center_mode"   in args
            use_double_detour = "double_detour" in args
            pos_args = [a for a in args if a not in ("center_mode", "double_detour")]
            hint = pos_args[0]; src = pos_args[1]; dsts = pos_args[2:]
            topo_gen = interconnect.TopologyGenerator(self.fp)
            if use_center:
                topo_gen.set_busterm_mode(False)
            if use_double_detour:
                topo_gen.set_double_detour(True)
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
            # Override planner choices with any architect-pinned selections.
            self._apply_selections()
        elif cmd == "run_nuts":
            # Usage: run_nuts [track_pitch]
            pitch = float(args[0]) if args else 1.0
            nuts = interconnect.NUTSEngine(self.fp)
            nuts.set_track_pitch(pitch)
            self.nuts_result = nuts.run(self.bundles)
            print(f"NUTS placed {len(self.nuts_result.segments)} segments "
                  f"({self.nuts_result.num_violations} interval violations, "
                  f"{self.nuts_result.num_overlaps} track overlaps).")
            n_stretched = self._stretch_nuts_segments()
            if n_stretched:
                print(f"Stretched {n_stretched} segment span(s) to restore "
                      f"physical connectivity after track placement.")
        elif cmd == "visualize_topologies":
            # Usage:
            #   visualize_topologies <hint>         — first matching bundle
            #   visualize_topologies -all [hints…]  — all matching bundles
            #                                         (no hints = every bundle)
            all_mode = args and args[0] == '-all'
            hints    = args[1:] if all_mode else args[:1]

            seen, wrappers = set(), []
            for w in self.bundles:
                if not w.candidates: continue
                bid  = w.original_bundle.id
                if bid in seen: continue
                net0 = w.original_bundle.get_net_names()[0]
                if not hints or any(net0.startswith(h) for h in hints):
                    wrappers.append(w)
                    seen.add(bid)
                    if not all_mode:
                        break   # single-bundle mode: stop at first match

            if not wrappers:
                print(f"Warning: no bundle with candidates matching {hints or '(any)'}")
            else:
                for w in wrappers:
                    print(f"  bundle {w.original_bundle.id}: "
                          f"{len(w.candidates)} topologies")
                TopologyExplorer(self.fp, wrappers,
                                 sidecar_path=self._sidecar_path()).show()
        elif cmd == "visualize":
            viz = BudaVisualizer(self.fp, self.bundles,
                                 sidecar_path=self._sidecar_path())
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
            if self.script_path is None:
                self.script_path = os.path.abspath(args[0])
            with open(args[0], 'r') as f:
                for line in f:
                    if not line.strip().startswith('#'): self.do_command(line)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('script', nargs='?')
    args = parser.parse_args()
    session = BudaSession()
    if args.script:
        session.script_path = os.path.abspath(args.script)
        session.do_command(f"source {args.script}")

if __name__ == "__main__":
    main()