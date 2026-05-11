import argparse
import json
import os
import sys
import interconnect
from buda_viz import BudaVisualizer, TopologyExplorer


class _TeeStream:
    """Write to two streams simultaneously.

    Used to mirror sys.stdout/sys.stderr to a flow-log file so that all
    Python-level print() output is preserved alongside the overlap log.

    Note: C++ extensions write directly to fd 1 and are NOT captured here;
    their key metrics are mirrored into the nuts log from Python data instead.
    """
    def __init__(self, primary, secondary):
        self._primary   = primary
        self._secondary = secondary

    def write(self, data):
        n = self._primary.write(data)
        self._secondary.write(data)
        return n

    def flush(self):
        self._primary.flush()
        self._secondary.flush()

    def isatty(self):
        return self._primary.isatty()

    def fileno(self):
        # Return primary's fd so C extensions (pybind11) still reach the terminal.
        return self._primary.fileno()

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
        self._net_endpoints   = {}   # net_name -> (driver_instance, [receiver_instances])
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

    def _write_nuts_log(self, layer_names=None, append=False, rerun_layer_name=None,
                        extra_lines: list[str] | None = None):
        """Write (or append to) the per-overlap log file alongside the .buda script.

        File: <script_stem>_nuts.log  (or nuts.log if no script path).
        Mirrors key [NUTS] console messages then lists per-overlap detail.

        append=True       — append a re-run section instead of overwriting.
        rerun_layer_name  — label shown in the re-run header (append mode only).
        extra_lines       — additional lines (e.g. [Planner] messages) written
                            before the NUTS summary, so the log stays in the
                            same order as the console output.
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

        # Compute layer count from segments (mirrors C++ "[NUTS] N segments placed across K layer(s)")
        layer_ids_used = {s.layer for s in self.nuts_result.segments}
        n_layers = len(layer_ids_used)

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
                f.write(f"Generated : {datetime.now().isoformat(timespec='seconds')}\n\n")

            # Mirror any Planner / caller messages that preceded this NUTS run.
            if extra_lines:
                for line in extra_lines:
                    f.write(line + "\n")
                f.write("\n")

            # Mirror the C++ [NUTS] summary line.
            total = self.nuts_result.num_overlaps
            f.write(f"[NUTS] {len(self.nuts_result.segments)} segments placed across "
                    f"{n_layers} layer(s). "
                    f"Interval violations: {self.nuts_result.num_violations}, "
                    f"Track overlaps: {total}.\n")

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
        planner_msg = (f"[Planner] post_nuts: short<{short_thresh:.0f}→{lo_name} ({short_count}b), "
                       f"medium ({medium_count}b), long>{long_thresh:.0f}→{hi_name} ({long_count}b)")
        print(planner_msg)

        # Re-run full NUTS with the updated layer assignments.
        pitch = self._nuts_pitch if hasattr(self, '_nuts_pitch') and self._nuts_pitch else 1.0
        nuts = interconnect.NUTSEngine(self.fp)
        nuts.set_track_pitch(pitch)
        self.nuts_result = nuts.run(self.bundles)

        layer_names = self._make_layer_names()
        self._write_nuts_log(layer_names, append=True, rerun_layer_name="post_nuts",
                             extra_lines=[planner_msg])

    def _segment_states_from_topology(self) -> dict:
        """Build a 'before' snapshot from topology geometry (no track assignment yet).

        track_position = -1.0 signals 'unplaced'; _nuts_diagnostics skips movement
        stats for those segments so the same diagnostic code works for both the
        initial run_nuts and per-layer rerun_layer calls.
        """
        states: dict[tuple, dict] = {}
        for bw in self.bundles:
            if not bw.candidates:
                continue
            topo = bw.candidates[bw.selected_topology_index]
            bid  = bw.original_bundle.id
            for si, seg in enumerate(topo.segments):
                is_h = (seg.start.y == seg.end.y)
                if is_h:
                    span_lo = float(min(seg.start.x, seg.end.x))
                    span_hi = float(max(seg.start.x, seg.end.x))
                    layer   = bw.assigned_h_layer if bw.assigned_h_layer >= 0 else seg.layer_hint
                else:
                    span_lo = float(min(seg.start.y, seg.end.y))
                    span_hi = float(max(seg.start.y, seg.end.y))
                    layer   = bw.assigned_v_layer if bw.assigned_v_layer >= 0 else seg.layer_hint
                states[(bid, si)] = {
                    'layer':          layer,
                    'track_position': -1.0,   # unplaced sentinel
                    'span_lo':        span_lo,
                    'span_hi':        span_hi,
                }
        return states

    def _nuts_diagnostics(self, result, layer_names: dict,
                          before: dict, target_layer: int | None = None) -> list[str]:
        """Emit and collect NUTS diagnostic lines after a solve.

        Shared by run_nuts (target_layer=None, all layers) and
        _rerun_nuts_layer (target_layer=layer_id, focus on one layer).

        before: (bid, seg_idx) -> {layer, track_position, span_lo, span_hi}
            track_position == -1.0  →  unplaced; movement stats suppressed.
        target_layer: if set, only that layer is reported per-layer and other
            layers are treated as 'connected' for span-adjustment analysis.
            If None, all layers are reported; span adjustments shown across all.
        """
        diag: list[str] = []

        def emit(msg: str):
            print(msg)
            diag.append(msg)

        by_layer: dict[int, list] = {}
        for s in result.segments:
            by_layer.setdefault(s.layer, []).append(s)

        report_layers = [target_layer] if target_layer is not None else sorted(by_layer.keys())

        for lid in report_layers:
            segs  = by_layer.get(lid, [])
            lname = layer_names.get(lid, f'L{lid}')
            n     = len(segs)

            # Movement stats — only when segment was placed before (track_position >= 0).
            moved_deltas: list[float] = []
            for s in segs:
                bef = before.get((s.bundle_id, s.seg_idx))
                if bef and bef['track_position'] >= 0:
                    delta = abs(s.track_position - bef['track_position'])
                    if delta > 1e-6:
                        moved_deltas.append(delta)
            if moved_deltas:
                avg_d = sum(moved_deltas) / len(moved_deltas)
                max_d = max(moved_deltas)
                emit(f"[NUTS] {lname}: {len(moved_deltas)}/{n} segments moved "
                     f"(avg |Δperp|={avg_d:.1f}, max={max_d:.1f})")

            # Local overlaps on this layer.
            local_ov = [od for od in result.overlap_details if od.layer == lid]
            if local_ov:
                pairs_str = ', '.join(f"B{od.bid_a}×B{od.bid_b}" for od in local_ov)
                emit(f"[NUTS] {lname} local overlaps: {len(local_ov)} → {pairs_str}")
            else:
                emit(f"[NUTS] {lname}: no local overlaps")

        # Span adjustments: compare before vs after spans.
        # For rerun: skip the target layer (it drove the adjustment).
        # For full run: report all layers.
        span_adj: dict[int, int] = {}
        for s in result.segments:
            if target_layer is not None and s.layer == target_layer:
                continue
            bef = before.get((s.bundle_id, s.seg_idx))
            if bef and (abs(s.span_lo - bef['span_lo']) > 1e-6 or
                        abs(s.span_hi - bef['span_hi']) > 1e-6):
                span_adj[s.layer] = span_adj.get(s.layer, 0) + 1

        if span_adj:
            label   = "Connected span adjustments" if target_layer is not None else "Span adjustments"
            adj_str = ', '.join(
                f"{layer_names.get(lid, f'L{lid}')}:{cnt}"
                for lid, cnt in sorted(span_adj.items())
            )
            emit(f"[NUTS] {label}: {adj_str}")

            # Post-adjust overlaps on adjusted layers.
            # For full run these equal the global overlap summary — skip to avoid noise.
            if target_layer is not None:
                adj_layer_ids = set(span_adj)
                post_ov_by_layer: dict[int, list] = {}
                for od in result.overlap_details:
                    if od.layer in adj_layer_ids:
                        post_ov_by_layer.setdefault(od.layer, []).append(od)
                if post_ov_by_layer:
                    summary = ', '.join(
                        f"{layer_names.get(lid, f'L{lid}')}:{len(ods)}"
                        for lid, ods in sorted(post_ov_by_layer.items())
                    )
                    all_pairs = ', '.join(
                        f"B{od.bid_a}×B{od.bid_b}"
                        for ods in post_ov_by_layer.values()
                        for od in ods
                    )
                    emit(f"[NUTS] Post-adjust overlaps: {summary} → {all_pairs}")
                else:
                    adj_names = ', '.join(
                        layer_names.get(lid, f'L{lid}') for lid in sorted(adj_layer_ids))
                    emit(f"[NUTS] No overlaps on adjusted layers ({adj_names})")
        elif target_layer is not None:
            emit(f"[NUTS] No connected span adjustments")

        return diag

    def _rerun_nuts_layer(self, layer_id: int):
        """Re-solve one layer with NUTS, emit diagnostics, and log.

        Returns the updated NUTSResult (also stored in self.nuts_result).
        Used by both the run_nuts_on_layer command and the visualizer ↺ button.
        """
        layer_names  = self._make_layer_names()
        layer_name   = layer_names.get(layer_id, f"L{layer_id}")
        nuts = interconnect.NUTSEngine(self.fp)
        nuts.set_track_pitch(self._nuts_pitch)

        # Snapshot full state before rerun.
        before: dict[tuple, dict] = {
            (s.bundle_id, s.seg_idx): {
                'layer':          s.layer,
                'track_position': s.track_position,
                'span_lo':        s.span_lo,
                'span_hi':        s.span_hi,
            }
            for s in self.nuts_result.segments
        }
        n_layer_segs = sum(1 for s in self.nuts_result.segments if s.layer == layer_id)

        pre_msg = f"[NUTS] Running {layer_name}: {n_layer_segs} segment(s)"
        print(pre_msg)

        # C++ also prints its own [NUTS] rerun_layer(...) line here.
        self.nuts_result = nuts.rerun_layer(self.nuts_result, self.bundles, layer_id)

        diag = self._nuts_diagnostics(self.nuts_result, layer_names, before,
                                      target_layer=layer_id)

        rerun_msg = (f"[NUTS] rerun_layer({layer_id}={layer_name}): "
                     f"{n_layer_segs} segment(s) re-placed. "
                     f"Violations: {self.nuts_result.num_violations}, "
                     f"Overlaps: {self.nuts_result.num_overlaps}.")
        print(rerun_msg)

        self._write_nuts_log(layer_names, append=True, rerun_layer_name=layer_name,
                             extra_lines=[pre_msg] + diag + [rerun_msg])
        return self.nuts_result

    def do_command(self, cmd_line):
        parts = cmd_line.strip().split()
        if not parts or parts[0].startswith('#'): return
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd == "add_block":
            self.fp.add_block(args[0], int(args[1]), int(args[2]), int(args[3]), int(args[4]))
        elif cmd == "add_net":
            name, drv_pin, rcv_str = args[0], args[1], args[2]
            rcv_pins = rcv_str.split(',')
            self.netlist.add_net(name, drv_pin, rcv_pins)
            self._net_endpoints[name] = (
                drv_pin.split('.')[0],
                [r.split('.')[0] for r in rcv_pins],
            )
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
            drv_inst = drv_pin.split('.')[0]
            rcv_insts = [r.split('.')[0] for r in rcv_pins]
            for i in range(lo, hi + 1):
                net_name = f"{prefix}_{i}"
                self.netlist.add_net(net_name, drv_pin, rcv_pins)
                self._net_endpoints[net_name] = (drv_inst, rcv_insts)
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

        elif cmd == "generate_topologies":
            # Usage: generate_topologies [center_mode] [double_detour]
            # Generates topologies for every bundle produced by run_bundler,
            # deriving src/dst block names from the netlist automatically.
            use_center        = "center_mode"   in args
            use_double_detour = "double_detour" in args
            topo_gen = interconnect.TopologyGenerator(self.fp)
            h_layer = self.layers.get_top_layer(interconnect.LayerDir.HORIZONTAL)
            v_layer = self.layers.get_top_layer(interconnect.LayerDir.VERTICAL)
            if h_layer != -1 and v_layer != -1:
                topo_gen.set_layer_ids(h_layer, v_layer)
            if use_center:
                topo_gen.set_busterm_mode(False)
            if use_double_detour:
                topo_gen.set_double_detour(True)
            for w in self.bundles:
                net_name = w.original_bundle.get_net_names()[0]
                ep = self._net_endpoints.get(net_name)
                if ep is None:
                    print(f"Warning: no endpoint info for net '{net_name}' — skipping bundle {w.original_bundle.id}")
                    continue
                src, dsts = ep
                if len(dsts) == 1:
                    w.candidates = topo_gen.generate_candidates(src, dsts[0])
                    label = f"{src}->{dsts[0]}"
                else:
                    w.candidates = topo_gen.generate_multicast_candidates(src, dsts)
                    label = f"{src}->[{','.join(dsts)}]"
                print(f"Generated {len(w.candidates)} topologies for bundle "
                      f"{w.original_bundle.id} ({label})")

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
            # Snapshot topology-derived initial spans before the solve.
            before = self._segment_states_from_topology()
            # C++ prints its own [NUTS] N segments placed across K layer(s) line.
            self.nuts_result = nuts.run(self.bundles)
            layer_names = self._make_layer_names()
            diag = self._nuts_diagnostics(self.nuts_result, layer_names, before)
            self._write_nuts_log(layer_names, extra_lines=diag)
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
            self._rerun_nuts_layer(layer_id)
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
            rerun_fn = self._rerun_nuts_layer if self.nuts_result is not None else None
            viz = BudaVisualizer(self.fp, self.bundles,
                                 sidecar_path=self.script_path,
                                 rerun_layer_fn=rerun_fn)
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

        # Install TeeStream so all Python print() output is mirrored to a flow log.
        # C++ extensions write directly to fd 1 (terminal) and are NOT captured here;
        # their key [NUTS] metrics are mirrored into the nuts log from Python data.
        flow_log_path = os.path.splitext(session.script_path)[0] + '_flow.log'
        try:
            _flow_log_file = open(flow_log_path, 'w', buffering=1)
            sys.stdout = _TeeStream(sys.stdout, _flow_log_file)
            sys.stderr = _TeeStream(sys.stderr, _flow_log_file)
        except OSError as e:
            print(f"Warning: could not open flow log {flow_log_path}: {e}")

        session.do_command(f"source {script}")

if __name__ == "__main__":
    main()