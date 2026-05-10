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
        self._layer_name_map = {}    # layer_name -> layer_id
        self._nuts_pitch = 1.0       # last track pitch used by run_nuts
        self.script_path = None      # set when a .buda script is sourced

    def _sidecar_path(self):
        """Return the .json path for the current script, or None."""
        if not self.script_path:
            return None
        base = os.path.splitext(self.script_path)[0]
        return base + '.json'

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
                if names and names[0].startswith(hint):
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
            matched_w.topology_pinned = True
            print(f"Pinned bundle '{hint}' to topology {resolved} "
                  f"({sel['topo_type']}, WL={sel['topo_wl']})")

    def _make_layer_names(self):
        """Build a layer_id -> name dict from def_layer commands, with fallback defaults."""
        names = {4: 'M4', 5: 'M5', 6: 'M6'}
        for name, lid in self._layer_name_map.items():
            names[lid] = name
        return names

    def _write_nuts_log(self, layer_names=None, append=False, rerun_layer_name=None):
        """Write (or append to) the per-overlap log file alongside the .buda script.

        File: <script_stem>_nuts.log  (or nuts.log if no script path).
        Each overlap is reported with its two segments, the overlap rectangle
        (routing-direction span and perpendicular band), and the overlap area.

        append=True  — append a re-run section instead of overwriting.
        rerun_layer_name — layer name shown in the re-run header (append mode only).
        """
        if self.nuts_result is None:
            return
        if layer_names is None:
            layer_names = self._make_layer_names()

        if self.script_path:
            log_path = os.path.splitext(self.script_path)[0] + '_nuts.log'
        else:
            log_path = 'nuts.log'

        details = self.nuts_result.overlap_details
        per_layer = self.nuts_result.overlaps_per_layer

        # Build a segment label map: (bundle_id, seg_idx) -> display name
        seg_label = {}
        for w in self.bundles:
            bid   = w.original_bundle.id
            nets  = w.original_bundle.get_net_names()
            hint  = nets[0] if nets else f"B{bid}"
            topo  = w.candidates[w.selected_topology_index]
            for si, seg in enumerate(topo.segments):
                lname = layer_names.get(seg.layer_hint, f"L{seg.layer_hint}")
                seg_label[(bid, si)] = f"B{bid}.{lname}[{si}]"

        from datetime import datetime
        open_mode = 'a' if append else 'w'
        with open(log_path, open_mode) as f:
            script_name = os.path.basename(self.script_path) if self.script_path else '(interactive)'
            if append:
                f.write(f"\n{'='*60}\n")
                if rerun_layer_name:
                    f.write(f"  Re-run: {rerun_layer_name}  —  {script_name}\n")
                else:
                    f.write(f"  Re-run  —  {script_name}\n")
                f.write(f"  At        : {datetime.now().isoformat(timespec='seconds')}\n")
                f.write(f"{'='*60}\n\n")
            else:
                f.write(f"NUTS Overlap Report — {script_name}\n")
                f.write(f"Generated : {datetime.now().isoformat(timespec='seconds')}\n")
            f.write(f"Segments  : {len(self.nuts_result.segments)}\n")
            f.write(f"Violations: {self.nuts_result.num_violations}\n")
            total = self.nuts_result.num_overlaps
            layer_summary = '  '.join(
                f"{layer_names.get(lid, f'L{lid}')}={cnt}"
                for lid, cnt in sorted(per_layer.items())
            )
            f.write(f"Overlaps  : {total}  ({layer_summary})\n")
            f.write("\n")

            if not details:
                f.write("No overlaps.\n")
            else:
                # Group by layer
                by_layer = {}
                for od in details:
                    by_layer.setdefault(od.layer, []).append(od)

                for lid in sorted(by_layer):
                    lname = layer_names.get(lid, f"L{lid}")
                    entries = by_layer[lid]
                    f.write(f"{'='*60}\n")
                    f.write(f"  {lname}  —  {len(entries)} overlap(s)\n")
                    f.write(f"{'='*60}\n")
                    for n, od in enumerate(entries, 1):
                        la = seg_label.get((od.bid_a, od.seg_a), f"B{od.bid_a}[{od.seg_a}]")
                        lb = seg_label.get((od.bid_b, od.seg_b), f"B{od.bid_b}[{od.seg_b}]")
                        span_len = od.span_hi - od.span_lo
                        perp_dep = od.perp_hi - od.perp_lo
                        area     = span_len * perp_dep
                        f.write(
                            f"  [{n:3d}]  {la}  ×  {lb}\n"
                            f"         span  [{od.span_lo:.1f}, {od.span_hi:.1f}]"
                            f"  len={span_len:.1f}\n"
                            f"         perp  [{od.perp_lo:.2f}, {od.perp_hi:.2f}]"
                            f"  depth={perp_dep:.2f}  area={area:.2f}\n"
                        )
                    f.write("\n")

        action = "appended to" if append else "→"
        print(f"NUTS overlap log {action} {log_path}")

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

    def _run_post_nuts_planner(self, short_thresh: float, long_thresh: float):
        """Stage 4c — Post-NUTS stub layer reassignment.

        Classifies every bundle's vertical stub segments by span length and
        moves short stubs to the lowest available V layer (e.g. M3) and long
        stubs to the highest (e.g. M7).  After reassignment a full NUTS solve
        is run so all layers are consistent with the new assignments.

        short_thresh : stubs shorter than this go to the lowest V layer.
        long_thresh  : stubs longer than this go to the highest V layer.
        """
        if self.nuts_result is None:
            print("Error: run_planner post_nuts requires run_nuts to have been called first")
            return

        v_dir = interconnect.LayerDir.VERTICAL
        v_layers = sorted(self.layers.get_layer_ids_by_dir(v_dir))
        if len(v_layers) < 2:
            print("[Planner] post_nuts: fewer than 2 V layers defined — nothing to reassign")
            return

        lo_v = v_layers[0]   # e.g. M3 — short stubs (near block face)
        hi_v = v_layers[-1]  # e.g. M7 — long stubs (full channel crossing)
        v_layer_set = set(v_layers)
        layer_names = self._make_layer_names()

        # Map bundle_id → max V-segment span length in the current NUTS result.
        bid_max_span: dict[int, float] = {}
        for seg in self.nuts_result.segments:
            if seg.layer not in v_layer_set:
                continue
            span_len = seg.span_hi - seg.span_lo
            bid = seg.bundle_id
            if bid not in bid_max_span or span_len > bid_max_span[bid]:
                bid_max_span[bid] = span_len

        # Reassign assigned_v_layer on each bundle.
        short_count = medium_count = long_count = 0
        for w in self.bundles:
            bid = w.original_bundle.id
            if bid not in bid_max_span:
                continue  # no V segments for this bundle
            max_span = bid_max_span[bid]
            if max_span < short_thresh:
                new_v = lo_v
                short_count += 1
            elif max_span > long_thresh:
                new_v = hi_v
                long_count += 1
            else:
                medium_count += 1
                continue  # keep current assignment
            w.assigned_v_layer = new_v

        lo_name = layer_names.get(lo_v, f"L{lo_v}")
        hi_name = layer_names.get(hi_v, f"L{hi_v}")
        print(f"[Planner] post_nuts: short<{short_thresh:.0f}→{lo_name} ({short_count}b), "
              f"medium ({medium_count}b), long>{long_thresh:.0f}→{hi_name} ({long_count}b)")

        # Re-run full NUTS with the updated layer assignments.
        pitch = self._nuts_pitch if hasattr(self, '_nuts_pitch') and self._nuts_pitch else 1.0
        nuts = interconnect.NUTSEngine(self.fp)
        nuts.set_track_pitch(pitch)
        self.nuts_result = nuts.run(self.bundles)

        layer_names = self._make_layer_names()
        per_layer = self.nuts_result.overlaps_per_layer
        if per_layer:
            detail = ', '.join(
                f"{layer_names.get(lid, f'L{lid}')}={cnt}"
                for lid, cnt in sorted(per_layer.items())
            )
            overlap_str = f"{self.nuts_result.num_overlaps} track overlaps ({detail})"
        else:
            overlap_str = "0 track overlaps"
        print(f"[Planner] post_nuts NUTS: {len(self.nuts_result.segments)} segments "
              f"({self.nuts_result.num_violations} violations, {overlap_str}).")
        self._write_nuts_log(layer_names, append=True, rerun_layer_name="post_nuts")

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
            self._layer_name_map[name] = int(lid)
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
            h_layer = self.layers.get_top_layer(interconnect.LayerDir.HORIZONTAL)
            v_layer = self.layers.get_top_layer(interconnect.LayerDir.VERTICAL)
            if h_layer != -1 and v_layer != -1:
                topo_gen.set_layer_ids(h_layer, v_layer)
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
            if args and args[0] == "post_nuts":
                # Stage 4c: post-NUTS stub layer reassignment.
                short_thresh = float(args[1]) if len(args) > 1 else 80.0
                long_thresh  = float(args[2]) if len(args) > 2 else 200.0
                self._run_post_nuts_planner(short_thresh, long_thresh)
            else:
                self.planner = interconnect.GlobalRouter(self.fp, self.layers)
                for lid, ovh in self._layer_overheads.items():
                    self.planner.set_layer_overhead(lid, ovh)
                self.planner.build_congestion_map()
                # Apply architect-pinned selections BEFORE optimizing so the
                # planner scores the correct topology and assigns layers for it.
                self._apply_selections()
                assignments = self.planner.optimize_topologies(self.bundles, int(args[0]) if args else 5)
                # Apply planner layer decisions (vector copy in C++ means we must apply here).
                bid_to_wrapper = {w.original_bundle.id: w for w in self.bundles}
                for asn in assignments:
                    w = bid_to_wrapper.get(asn.bundle_id)
                    if w is not None:
                        w.selected_topology_index = asn.topo_index
                        w.assigned_v_layer = asn.v_layer_id
                        w.assigned_h_layer = asn.h_layer_id
        elif cmd == "run_nuts":
            # Usage: run_nuts [track_pitch]
            pitch = float(args[0]) if args else 1.0
            self._nuts_pitch = pitch
            nuts = interconnect.NUTSEngine(self.fp)
            nuts.set_track_pitch(pitch)
            self.nuts_result = nuts.run(self.bundles)
            layer_names = self._make_layer_names()
            per_layer = self.nuts_result.overlaps_per_layer
            if per_layer:
                detail = ', '.join(
                    f"{layer_names.get(lid, f'L{lid}')}={cnt}"
                    for lid, cnt in sorted(per_layer.items())
                )
                overlap_str = f"{self.nuts_result.num_overlaps} track overlaps ({detail})"
            else:
                overlap_str = "0 track overlaps"
            print(f"NUTS placed {len(self.nuts_result.segments)} segments "
                  f"({self.nuts_result.num_violations} interval violations, "
                  f"{overlap_str}).")
            self._write_nuts_log(layer_names)
        elif cmd == "run_nuts_on_layer":
            # Usage: run_nuts_on_layer <layer-name>
            if not args:
                print("Error: run_nuts_on_layer requires a layer name")
                return
            layer_name = args[0]
            layer_id = self._layer_name_map.get(layer_name)
            if layer_id is None:
                print(f"Error: unknown layer '{layer_name}' — define it with def_layer first")
                return
            if self.nuts_result is None:
                print("Error: run_nuts must be called before run_nuts_on_layer")
                return
            nuts = interconnect.NUTSEngine(self.fp)
            nuts.set_track_pitch(self._nuts_pitch)
            self.nuts_result = nuts.rerun_layer(self.nuts_result, self.bundles, layer_id)
            layer_names = self._make_layer_names()
            per_layer = self.nuts_result.overlaps_per_layer
            if per_layer:
                detail = ', '.join(
                    f"{layer_names.get(lid, f'L{lid}')}={cnt}"
                    for lid, cnt in sorted(per_layer.items())
                )
                overlap_str = f"{self.nuts_result.num_overlaps} track overlaps ({detail})"
            else:
                overlap_str = "0 track overlaps"
            print(f"NUTS re-solved {layer_name}: "
                  f"{self.nuts_result.num_violations} violations, {overlap_str}.")
            self._write_nuts_log(layer_names, append=True, rerun_layer_name=layer_name)
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
                                 sidecar_path=self.script_path)
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
        script = args.script
        if not os.path.exists(script) and not script.endswith('.buda'):
            script = script + '.buda'
        session.script_path = os.path.abspath(script)
        session.do_command(f"source {script}")

if __name__ == "__main__":
    main()