import argparse
import faulthandler
import json
import math
import os
import sys

# Ensure the compiled extension is loaded from build/ rather than a stale
# copy that might exist alongside this script.
_build = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'build'))
if _build not in sys.path:
    sys.path.insert(0, _build)

# On macOS the native 'macosx' backend can intermittently segfault,
# especially with the IPC timer or when multiple windows open.
# Force TkAgg to ensure stability.
if sys.platform == 'darwin':
    import matplotlib
    matplotlib.use('TkAgg')

import buda

faulthandler.enable()
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
    def _get_log_path(self, suffix):
        """Get the log path for a given suffix, ensuring the log directory exists."""
        if self.script_path:
            script_dir = os.path.dirname(self.script_path)
            script_stem = os.path.splitext(os.path.basename(self.script_path))[0]
            log_dir = os.path.join(script_dir, 'log')
            os.makedirs(log_dir, exist_ok=True)
            return os.path.join(log_dir, f"{script_stem}_{suffix}")
        else:
            log_dir = 'log'
            os.makedirs(log_dir, exist_ok=True)
            return os.path.join(log_dir, suffix)

    def __init__(self):
        self.fp = buda.Floorplan()
        self.netlist = buda.Netlist()
        self.layers = buda.LayerStack()
        self.bundler = buda.Bundler()
        self.planner = None
        self.bundles = []
        self.nuts_result = None
        self._layer_overheads = {}   # layer_id -> overhead_percent
        self._planner_params  = {}   # param_name -> value (buffered before planner exists)
        self._net_endpoints   = {}   # net_name -> (driver_instance, [receiver_instances])
        self._layer_name_map = {}    # layer_name -> layer_id
        self._nuts_pitch = 1.0
        self._detailed_bit_order = "LO_HI"
       # last track pitch used by run_nuts
        self._planner_iterations = 5 # last iteration count used by run_planner
        self._script_stack = []      # stack of absolute paths of sourced scripts
        self.script_path = None      # set when a .buda script is sourced
        self.routing_grid = None     # RoutingGridStack (stage 8)
        self.detailed_result = None  # DetailedNUTSResult (stage 9)
        self.no_viz = False          # set by --no-viz CLI flag
        self.bdb = None              # BDB (opened by open_bdb command)
        self.bdb_net_mode = False    # when True, add_net/add_bus also write to BDB
        self._corner_margin = (0, 0) # (dx, dy) — mirrors fp global corner margin
        self._hier_expansion_map = {}  # original bundle id → [expanded BundleWrappers]
        self._hier_bundles_orig = []   # pre-expansion snapshot set by run_hier_bundler

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
            matching = [w for w in self.bundles
                        if w.original_bundle.get_net_names() and
                           w.original_bundle.get_net_names()[0].startswith(hint)]
            if not matching:
                print(f"Warning: no bundle found for hint '{hint}' — skipping")
                continue

            # Prefer stable (type, wl) match; fall back to stored index hint.
            # Resolution uses the first match; all instances share the same candidate set.
            first_w = matching[0]
            resolved = None
            for i, cand in enumerate(first_w.candidates):
                if (cand.type == sel['topo_type'] and
                        cand.estimated_wirelength == sel['topo_wl']):
                    resolved = i
                    break
            if resolved is None:
                idx_hint = sel.get('topo_index_hint', -1)
                bid = first_w.original_bundle.id
                if 0 <= idx_hint < len(first_w.candidates):
                    resolved = idx_hint
                    print(f"Warning: selection for bundle {bid} matched by index hint "
                          f"(type/WL changed?) — using topo {resolved + 1}")
                else:
                    print(f"Warning: selection for bundle {bid} could not be resolved — ignored")
                    continue

            # Apply pin to ALL matching wrappers (handles multiple cell instances).
            pinned_layers = sel.get('seg_layers')
            for w in matching:
                w.selected_topology_index = resolved
                w.topology_pinned = True
                if pinned_layers is not None:
                    topo = w.candidates[resolved]
                    if len(pinned_layers) == len(topo.segments):
                        w.pinned_seg_layers = list(pinned_layers)

            n = len(matching)
            bid = first_w.original_bundle.id
            suffix = f" [{n} instances]" if n > 1 else ""
            if pinned_layers is not None and len(pinned_layers) == len(first_w.candidates[resolved].segments):
                print(f"  (pinned {len(pinned_layers)} segment layers)")
            print(f"Pinned bundle {bid} to topology {resolved + 1} "
                  f"({sel['topo_type']}, WL={sel['topo_wl']}){suffix}")

    def _add_blocks_from_bdb(self, depth: int, mode: str = "deepest"):
        """Walk BDB hierarchy and call fp.add_block() for components at `depth`.

        mode="deepest": DFS — go down to `depth`; if a branch ends before
            reaching depth, add that branch's deepest component instead.
        mode="skip": only add components whose .depth == requested depth;
            shallower branches produce no block.
        mode="error": like deepest but prints a warning and returns early
            if any branch is shorter than the requested depth.
        Components without valid placement (x1 < 0) are always skipped.
        """
        comps = self.bdb.all_components()

        # Build id → row and id → children maps
        by_id       = {r.id: r for r in comps}
        children_of = {}          # parent_id → list[ComponentRow]
        for r in comps:
            children_of.setdefault(r.parent_id, []).append(r)

        blocks_to_add = []   # (ComponentRow, fallback: bool)

        if mode == "skip":
            blocks_to_add = [(r, False) for r in comps if r.depth == depth]

        else:  # deepest or error
            shallow_names = []

            def collect(node, cur_depth):
                if cur_depth == depth:
                    blocks_to_add.append((node, False))
                    return
                children = children_of.get(node.id, [])
                if not children:
                    # Branch ended before requested depth
                    shallow_names.append(
                        f"{node.name!r} (depth {cur_depth}, requested {depth})")
                    if mode == "deepest":
                        blocks_to_add.append((node, True))
                    # error mode: record but don't add; will abort after DFS
                    return
                for child in children:
                    collect(child, cur_depth + 1)

            roots = [r for r in comps if r.parent_id == -1]
            for root in roots:
                collect(root, 0)

            if shallow_names and mode == "error":
                print(f"[BDB] Error: {len(shallow_names)} branch(es) shallower "
                      f"than depth {depth}:")
                for s in shallow_names[:10]:
                    print(f"  {s}")
                if len(shallow_names) > 10:
                    print(f"  … and {len(shallow_names)-10} more")
                return

        # Add blocks to floorplan
        added_rows = []   # (ComponentRow, is_fallback) for placed blocks
        skipped_unplaced = fallback_count = 0
        for r, is_fallback in blocks_to_add:
            if r.x1 < 0 or r.y1 < 0:
                skipped_unplaced += 1
                continue
            self.fp.add_block(r.name,
                              int(round(r.x1)), int(round(r.y1)),
                              int(round(r.x2)), int(round(r.y2)))
            added_rows.append((r, is_fallback))
            if is_fallback:
                fallback_count += 1

        added = len(added_rows)
        parts = [f"[BDB] Added {added} blocks at depth {depth} (mode={mode})"]
        if fallback_count:
            parts.append(f"{fallback_count} used deepest-available fallback")
        if skipped_unplaced:
            parts.append(f"{skipped_unplaced} skipped (unplaced)")
        print("; ".join(parts))

        # Write block names to log file
        log_path = self._get_log_path('bdb_blocks.log')
        try:
            with open(log_path, 'w') as f:
                f.write(f"# add_blocks_from_bdb depth={depth} mode={mode}\n")
                f.write(f"# {added} blocks added\n")
                for r, is_fallback in added_rows:
                    tag = " [deepest-fallback]" if is_fallback else ""
                    f.write(f"{r.name}{tag}\n")
            print(f"[BDB] Block list written to {log_path}")
        except OSError as e:
            print(f"[BDB] Warning: could not write block log {log_path}: {e}")

    # ── Hierarchical topology helpers ─────────────────────────────────────────

    def _build_bdb_floorplan(self, depth):
        """Build a Floorplan with placed components at exactly this depth from BDB."""
        fp = buda.Floorplan()
        dx, dy = self._corner_margin
        if dx or dy:
            fp.set_global_corner_margin(dx, dy)
        for c in self.bdb.all_components():
            if c.depth == depth and c.x1 >= 0:
                fp.add_block(c.name, int(round(c.x1)), int(round(c.y1)),
                             int(round(c.x2)), int(round(c.y2)))
        return fp

    def _build_cell_local_floorplan(self, parent_comp_name):
        """Build a Floorplan in cell-local coords for sub-components of parent."""
        comps = {c.name: c for c in self.bdb.all_components()}
        parent = comps.get(parent_comp_name)
        if parent is None:
            return None
        fp = buda.Floorplan()
        for c in comps.values():
            if c.parent_id == parent.id and c.x1 >= 0:
                local_name = c.name.rsplit('/', 1)[-1]
                lx1 = int(round(c.x1 - parent.x1))
                ly1 = int(round(c.y1 - parent.y1))
                lx2 = int(round(c.x2 - parent.x1))
                ly2 = int(round(c.y2 - parent.y1))
                fp.add_block(local_name, lx1, ly1, lx2, ly2)
        return fp

    @staticmethod
    def _parse_bundle_reason(reason):
        """Parse 'DRV:x|REC:a,b,' → ('x', ['a', 'b']).  Returns (None, []) on failure."""
        try:
            drv_part, rec_part = reason.split('|REC:')
            src = drv_part[4:]              # strip leading "DRV:"
            dsts = [n for n in rec_part.split(',') if n]
            return src, dsts
        except (ValueError, IndexError):
            return None, []

    def _generate_hier_topo_one(self, w, use_center, use_double_detour,
                                fp_cache, comps_by_name):
        """Generate topology candidates for a single HBundle wrapper.

        Updates w.candidates in place. Returns candidate count.
        fp_cache is a dict shared across calls; pass {} for a fresh cache.
        comps_by_name is {name: ComponentRow} from bdb.all_components().
        """
        b = w.original_bundle
        if b.cell_context and b.entry_busterm_ids:
            # Case (a): cell-local floorplan
            parent_name = b.instances[0] if b.instances else None
            if parent_name is None:
                print(f"  Warning: bundle {b.id} has cell_context but no instances — skipping")
                return 0
            cache_key = ('cell', parent_name)
            if cache_key not in fp_cache:
                fp_cache[cache_key] = self._build_cell_local_floorplan(parent_name)
            cell_fp = fp_cache[cache_key]
            if cell_fp is None:
                print(f"  Warning: could not build cell-local fp for {parent_name!r} — skipping")
                return 0
            tg = self._make_topo_gen(cell_fp, use_center, use_double_detour)
            src_local = b.entry_busterm_ids[0].removeprefix('bt:').rsplit('/', 1)[-1]
            dsts_local = [e.removeprefix('bt:').rsplit('/', 1)[-1] for e in b.exit_busterm_ids]
            w.candidates = tg.generate_candidates(src_local, dsts_local)
            label = f"{src_local}→{dsts_local[0]}"
            n = len(w.candidates)
            if n == 0:
                print(f"  WARNING: HierTopo D{b.level}: bundle {b.id} ({label}) "
                      f"0 candidates — bundle will be unrouted!  [cell:{b.cell_context}]")
            else:
                print(f"HierTopo D{b.level}: bundle {b.id} ({label}) "
                      f"{n} candidates  [cell:{b.cell_context}]")
            return n

        elif b.drv_spec_depth >= 0:
            # Case (c): cross-level — custom floorplan from actual endpoint blocks
            drv_comp = comps_by_name.get(b.drv_spec_path)
            if drv_comp is None:
                print(f"  Warning: driver comp {b.drv_spec_path!r} not found — "
                      f"skipping bundle {b.id}")
                return 0
            fp = buda.Floorplan()
            dx, dy = self._corner_margin
            if dx or dy:
                fp.set_global_corner_margin(dx, dy)
            fp.add_block(b.drv_spec_path,
                         int(round(drv_comp.x1)), int(round(drv_comp.y1)),
                         int(round(drv_comp.x2)), int(round(drv_comp.y2)))
            ok = True
            for rpath in b.rcv_spec_paths:
                rc = comps_by_name.get(rpath)
                if rc is None:
                    print(f"  Warning: receiver comp {rpath!r} not found — "
                          f"skipping bundle {b.id}")
                    ok = False; break
                fp.add_block(rpath,
                             int(round(rc.x1)), int(round(rc.y1)),
                             int(round(rc.x2)), int(round(rc.y2)))
            if not ok:
                return 0
            tg = self._make_topo_gen(fp, use_center, use_double_detour)
            w.candidates = tg.generate_candidates(b.drv_spec_path, list(b.rcv_spec_paths))
            label = (f"{b.drv_spec_path}→{b.rcv_spec_paths[0]}"
                     if len(b.rcv_spec_paths) == 1
                     else f"{b.drv_spec_path}→[{','.join(b.rcv_spec_paths)}]")
            n = len(w.candidates)
            tag = f"[cross-level D{b.drv_spec_depth}→D{b.rcv_spec_depth}]"
            if n == 0:
                print(f"  WARNING: HierTopo D{b.level}: bundle {b.id} ({label}) "
                      f"0 candidates — bundle will be unrouted!  {tag}")
            else:
                print(f"HierTopo D{b.level}: bundle {b.id} ({label}) {n} candidates  {tag}")
            return n

        else:
            # Case (b): same-level cross-block — BDB floorplan at the
            # endpoints' depth.  b.level is the routing-context depth (the
            # endpoints' common ancestor), which can be shallower than the
            # endpoint paths themselves; the blocks to route between live at
            # the endpoint depth (path segments − 1).
            src, dsts = self._parse_bundle_reason(b.reason)
            ep_depth = src.count('/') if src else b.level
            cache_key = ('depth', ep_depth)
            if cache_key not in fp_cache:
                fp_cache[cache_key] = self._build_bdb_floorplan(ep_depth)
            depth_fp = fp_cache[cache_key]
            tg = self._make_topo_gen(depth_fp, use_center, use_double_detour)
            if src is None:
                print(f"  Warning: could not parse reason for bundle {b.id}: {b.reason!r}")
                return 0
            w.candidates = tg.generate_candidates(src, dsts)
            label = f"{src}→{dsts[0]}" if len(dsts) == 1 else f"{src}→[{','.join(dsts)}]"
            n = len(w.candidates)
            if n == 0:
                print(f"  WARNING: HierTopo D{b.level}: bundle {b.id} ({label}) "
                      f"0 candidates — bundle will be unrouted!")
            else:
                print(f"HierTopo D{b.level}: bundle {b.id} ({label}) {n} candidates")
            return n

    def _floorplan_for_hbundle(self, b, fp_cache, comps_by_name):
        """Return the Floorplan an HBundle's candidates were generated in.

        Mirrors the 3-case floorplan selection in _generate_hier_topo_one so a
        connectivity check evaluates each topology in the same coordinate /
        block-name space its candidates were built in.  Returns None when the
        floorplan can't be reconstructed (caller falls back to self.fp).

        This resolves the PRE-expansion floorplan for a cell-level template
        (cell-local coords, cell-local block names).  Per-instance wrappers
        produced by _expand_hier_bundles carry absolute coords with dropped
        seg_busterms and must be checked against self.fp — callers exclude them.
        """
        if b.cell_context and b.entry_busterm_ids:
            # Case (a): cell-local floorplan.
            parent_name = b.instances[0] if b.instances else None
            if parent_name is None:
                return None
            cache_key = ('cell', parent_name)
            if cache_key not in fp_cache:
                fp_cache[cache_key] = self._build_cell_local_floorplan(parent_name)
            return fp_cache[cache_key]

        if b.drv_spec_depth >= 0:
            # Case (c): cross-level custom floorplan from the endpoint blocks.
            cache_key = ('xlevel', b.id)
            if cache_key not in fp_cache:
                fp = None
                drv_comp = comps_by_name.get(b.drv_spec_path)
                if drv_comp is not None:
                    fp = buda.Floorplan()
                    dx, dy = self._corner_margin
                    if dx or dy:
                        fp.set_global_corner_margin(dx, dy)
                    fp.add_block(b.drv_spec_path,
                                 int(round(drv_comp.x1)), int(round(drv_comp.y1)),
                                 int(round(drv_comp.x2)), int(round(drv_comp.y2)))
                    for rpath in b.rcv_spec_paths:
                        rc = comps_by_name.get(rpath)
                        if rc is None:
                            fp = None
                            break
                        fp.add_block(rpath,
                                     int(round(rc.x1)), int(round(rc.y1)),
                                     int(round(rc.x2)), int(round(rc.y2)))
                fp_cache[cache_key] = fp
            return fp_cache[cache_key]

        # Case (b): same-level cross-block — BDB floorplan at the endpoint depth.
        src, _ = self._parse_bundle_reason(b.reason)
        ep_depth = src.count('/') if src else b.level
        cache_key = ('depth', ep_depth)
        if cache_key not in fp_cache:
            fp_cache[cache_key] = self._build_bdb_floorplan(ep_depth)
        return fp_cache[cache_key]

    @staticmethod
    def _offset_topology(topo, dx, dy):
        """Return a new Topology with every segment shifted by (dx, dy)."""
        new_t = buda.Topology()
        new_t.type                  = topo.type
        new_t.estimated_wirelength  = topo.estimated_wirelength
        new_t.trunk_location        = topo.trunk_location   # metadata only; not transformed
        new_t.pass_through_count    = topo.pass_through_count
        new_t.connected_block_names = topo.connected_block_names
        new_segs = []
        for s in topo.segments:
            ns = buda.Segment()
            ns.start      = buda.Point(s.start.x + dx, s.start.y + dy)
            ns.end        = buda.Point(s.end.x   + dx, s.end.y   + dy)
            ns.layer_hint = s.layer_hint
            new_segs.append(ns)
        new_t.segments = new_segs
        return new_t

    @staticmethod
    def _clone_hbundle_with_id(b, new_id):
        """Return a shallow clone of HBundle b with id replaced by new_id."""
        nb = buda.HBundle()
        nb.id                = new_id
        nb.net_names         = b.net_names
        nb.reason            = b.reason
        nb.num_terminals     = b.num_terminals
        nb.level             = b.level
        nb.cell_context      = b.cell_context
        nb.instances         = b.instances
        nb.parent_id         = b.parent_id
        nb.child_ids         = b.child_ids
        nb.entry_busterm_ids = b.entry_busterm_ids
        nb.exit_busterm_ids  = b.exit_busterm_ids
        nb.drv_spec_depth    = b.drv_spec_depth
        nb.rcv_spec_depth    = b.rcv_spec_depth
        nb.drv_spec_path     = b.drv_spec_path
        nb.rcv_spec_paths    = b.rcv_spec_paths
        return nb

    def _expand_hier_bundles(self, bundles):
        """Expand cell-level BundleWrappers to per-instance absolute-coord wrappers.

        Cross-block bundles are kept as-is (candidates already absolute).
        Cell-level bundles (cell_context set) are replaced by one wrapper
        per entry in b.instances, each with candidates offset to that
        instance's absolute position.  Each expanded wrapper gets a unique
        HBundle ID so assignment matching is unambiguous.

        Returns (result_list, expansion_map) where expansion_map maps each
        original bundle ID to the list of expanded wrappers produced from it.
        Cross-block bundles (not expanded) are not included in the map.
        """
        comps = {c.name: c for c in self.bdb.all_components()}
        # Replica bookkeeping: the multiple-occurrence merge accumulates all
        # instance paths on the template but leaves each replica in the list
        # with its own instance and its own nets.  Expanding both the template
        # (over all instances) and the replicas would route the same physical
        # buses twice — so replicas are skipped here, and the template's
        # per-instance wrappers take their nets from the bundle that
        # physically lives in that instance (the replica = "donor").
        cell_bundle_ids = {w.original_bundle.id for w in bundles
                           if w.original_bundle.cell_context}
        donor_nets = {}    # (template id, instance path) → replica net list
        replica_wrapper_of = {}  # replica id → (template id, instance path)
        for w in bundles:
            b = w.original_bundle
            if (b.cell_context and b.parent_id in cell_bundle_ids
                    and b.instances):
                donor_nets[(b.parent_id, b.instances[0])] = list(b.net_names)
                replica_wrapper_of[b.id] = (b.parent_id, b.instances[0])
        # Start synthetic IDs above any real bundle ID in the set.
        max_id = max((w.original_bundle.id for w in bundles), default=-1)
        next_id = max_id + 1
        result = []
        expansion_map = {}  # original bundle id → [expanded wrappers]
        wrapper_at = {}     # (template id, instance path) → expanded wrapper
        for w in bundles:
            b = w.original_bundle
            if not b.cell_context or not b.instances:
                result.append(w)
                continue
            if b.id in replica_wrapper_of:
                continue   # replica: covered by its template's expansion
            expansion_map[b.id] = []
            for inst_name in b.instances:
                parent = comps.get(inst_name)
                if parent is None:
                    continue
                dx = int(round(parent.x1))
                dy = int(round(parent.y1))
                new_w = buda.BundleWrapper()
                clone = self._clone_hbundle_with_id(b, next_id)
                next_id += 1
                # Each instance wrapper carries the nets that physically live
                # in that instance and names only its own instance path.
                clone.net_names = donor_nets.get((b.id, inst_name),
                                                 list(b.net_names))
                clone.instances = [inst_name]
                new_w.original_bundle = clone
                new_w.width = w.width
                new_w.candidates = [self._offset_topology(t, dx, dy)
                                    for t in w.candidates]
                # Reserve the instance footprint: until this local bundle is
                # planned, its demand is parked as virtual usage so earlier
                # (global) bundles leave room over the cell interior.
                new_w.has_reservation = True
                new_w.res_x1 = dx
                new_w.res_y1 = dy
                new_w.res_x2 = int(round(parent.x2))
                new_w.res_y2 = int(round(parent.y2))
                # Rewrite cell-local block names to absolute paths so that
                # ConnTopology can look them up in the global floorplan.
                # Cell-local names have no "/" (e.g. "pa_i"); absolute names
                # already contain the hierarchy separator.
                for topo in new_w.candidates:
                    topo.connected_block_names = [
                        inst_name + "/" + n if "/" not in n else n
                        for n in topo.connected_block_names
                    ]
                # Propagate topology pinning from template to each instance.
                # Candidate indices are preserved (expansion offsets coordinates
                # but keeps the same ordering as the template candidate list).
                new_w.topology_pinned = w.topology_pinned
                new_w.selected_topology_index = w.selected_topology_index
                if w.pinned_seg_layers:
                    new_w.pinned_seg_layers = list(w.pinned_seg_layers)
                expansion_map[b.id].append(new_w)
                wrapper_at[(b.id, inst_name)] = new_w
                result.append(new_w)
        # Replicas map to the template wrapper at their instance, so a pin on
        # a replica's bundle ID still reaches the wrapper that routes it.
        for rid, key in replica_wrapper_of.items():
            if key in wrapper_at:
                expansion_map[rid] = [wrapper_at[key]]
        return result, expansion_map

    def _make_topo_gen(self, fp, use_center=False, use_double_detour=False):
        """Create a TopologyGenerator on fp with the current layer stack."""
        tg = buda.TopologyGenerator(fp)
        h = self.layers.get_top_layer(buda.LayerDir.HORIZONTAL)
        v = self.layers.get_top_layer(buda.LayerDir.VERTICAL)
        if h != -1 and v != -1:
            tg.set_layer_ids(h, v)
        if use_center:
            tg.set_busterm_mode(False)
        if use_double_detour:
            tg.set_double_detour(True)
        return tg

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

        log_path = self._get_log_path('nuts.log')

        details = self.nuts_result.overlap_details
        per_layer = self.nuts_result.overlaps_per_layer

        # Build a segment label map: (bundle_id, seg_idx) -> display name
        seg_label = {}
        for w in self.bundles:
            bid   = w.original_bundle.id
            nets  = w.original_bundle.get_net_names()
            hint  = nets[0] if nets else f"B{bid}"
            if not w.candidates or w.selected_topology_index < 0 or w.selected_topology_index >= len(w.candidates):
                continue  # bundle has no topology (e.g. src==dst or no candidates generated)
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

            # Build a placed-segment map for full coordinate lookup.
            ts_map = {(ts.bundle_id, ts.seg_idx): ts for ts in self.nuts_result.segments}

            def _seg_coords(bid, si):
                ts = ts_map.get((bid, si))
                if ts is None:
                    return "    (segment not found)\n"
                return (f"    span=[{ts.span_lo:.1f}, {ts.span_hi:.1f}]"
                        f"  perp_center={ts.track_position:.2f}  width={ts.width:.2f}"
                        f"  → perp=[{ts.track_position - ts.width/2:.2f},"
                        f" {ts.track_position + ts.width/2:.2f}]"
                        f"  interval=[{ts.interval_lo:.1f}, {ts.interval_hi:.1f}]\n")

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
                            f"         overlap span  [{od.span_lo:.1f}, {od.span_hi:.1f}]"
                            f"  len={span_len:.1f}\n"
                            f"         overlap perp  [{od.perp_lo:.2f}, {od.perp_hi:.2f}]"
                            f"  depth={perp_dep:.2f}  area={area:.2f}\n"
                        )
                        f.write(f"         {la}:\n" + _seg_coords(od.bid_a, od.seg_a))
                        f.write(f"         {lb}:\n" + _seg_coords(od.bid_b, od.seg_b))
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

    def _run_post_nuts_planner(self,
                               v_thresholds: tuple[float, float] | None,
                               h_thresholds: tuple[float, float] | None):
        """Stage 4c — Post-NUTS stub layer reassignment.

        Classifies every bundle's V and/or H stub segments by max span length
        and moves short stubs to the lowest layer and long stubs to the highest
        layer for each direction.  After all reassignments a single full NUTS
        solve is run so all layers are consistent with the new assignments.

        v_thresholds : (short_thresh, long_thresh) for V segments, or None to skip.
        h_thresholds : (short_thresh, long_thresh) for H segments, or None to skip.
        """
        if self.nuts_result is None:
            print("Error: run_planner post_nuts requires run_nuts to have been called first")
            return

        layer_names = self._make_layer_names()
        extra_lines: list[str] = []

        def _reassign_dir(dir_enum, thresholds: tuple[float, float]):
            short_thresh, long_thresh = thresholds
            layers_sorted = sorted(self.layers.get_layer_ids_by_dir(dir_enum))
            dir_label = "V" if dir_enum == buda.LayerDir.VERTICAL else "H"
            is_v = (dir_enum == buda.LayerDir.VERTICAL)
            if len(layers_sorted) < 2:
                print(f"[Planner] post_nuts {dir_label}: fewer than 2 {dir_label} layers — nothing to reassign")
                return
            lo_layer = layers_sorted[0]
            hi_layer = layers_sorted[-1]
            layer_set = set(layers_sorted)

            # Map bundle_id → max span length among segments on this direction's layers.
            bid_max_span: dict[int, float] = {}
            for seg in self.nuts_result.segments:
                if seg.layer not in layer_set:
                    continue
                span_len = seg.span_hi - seg.span_lo
                bid = seg.bundle_id
                if bid not in bid_max_span or span_len > bid_max_span[bid]:
                    bid_max_span[bid] = span_len

            short_count = medium_count = long_count = 0
            for w in self.bundles:
                bid = w.original_bundle.id
                if bid not in bid_max_span:
                    continue
                max_span = bid_max_span[bid]
                if max_span < short_thresh:
                    new_layer = lo_layer
                    short_count += 1
                elif max_span > long_thresh:
                    new_layer = hi_layer
                    long_count += 1
                else:
                    medium_count += 1
                    continue

                # Update per-segment layers for segments of this direction.
                # If seg_layers is populated (from run_planner), update it directly;
                # otherwise fall back to the legacy assigned_v/h_layer attribute.
                if not w.candidates or w.selected_topology_index < 0 or w.selected_topology_index >= len(w.candidates):
                    continue
                topo = w.candidates[w.selected_topology_index]
                if w.seg_layers:
                    sl = list(w.seg_layers)
                    for si, seg in enumerate(topo.segments):
                        seg_is_v = (seg.start.y != seg.end.y)
                        if (is_v and seg_is_v) or (not is_v and not seg_is_v):
                            if si < len(sl):
                                sl[si] = new_layer
                    w.seg_layers = sl
                else:
                    if is_v:
                        w.assigned_v_layer = new_layer
                    else:
                        w.assigned_h_layer = new_layer

            lo_name = layer_names.get(lo_layer, f"L{lo_layer}")
            hi_name = layer_names.get(hi_layer, f"L{hi_layer}")
            msg = (f"[Planner] post_nuts {dir_label}: short<{short_thresh:.0f}→{lo_name} ({short_count}b), "
                   f"medium ({medium_count}b), long>{long_thresh:.0f}→{hi_name} ({long_count}b)")
            print(msg)
            extra_lines.append(msg)

        if v_thresholds is not None:
            _reassign_dir(buda.LayerDir.VERTICAL, v_thresholds)
        if h_thresholds is not None:
            _reassign_dir(buda.LayerDir.HORIZONTAL, h_thresholds)

        # Single NUTS re-run after all reassignments.
        pitch = self._nuts_pitch if hasattr(self, '_nuts_pitch') and self._nuts_pitch else 1.0
        nuts = buda.NUTSEngine(self.fp, self.layers)
        nuts.set_track_pitch(pitch)
        self.nuts_result = nuts.run(self.bundles)

        layer_names = self._make_layer_names()
        self._write_nuts_log(layer_names, append=True, rerun_layer_name="post_nuts",
                             extra_lines=extra_lines)

    def _segment_states_from_topology(self) -> dict:
        """Build a 'before' snapshot from topology geometry (no track assignment yet).

        track_position = NaN signals 'unplaced'; _nuts_diagnostics skips movement
        stats for those segments so the same diagnostic code works for both the
        initial run_nuts and per-layer rerun_layer calls.
        """
        states: dict[tuple, dict] = {}
        for bw in self.bundles:
            if not bw.candidates or bw.selected_topology_index < 0 or bw.selected_topology_index >= len(bw.candidates):
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
                    'track_position': float('nan'),   # unplaced sentinel
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
            track_position == NaN  →  unplaced; movement stats suppressed.
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

            # Movement stats — only when segment was placed before (track_position not NaN).
            moved_deltas: list[float] = []
            for s in segs:
                bef = before.get((s.bundle_id, s.seg_idx))
                if bef and not math.isnan(bef['track_position']):
                    delta = abs(s.track_position - bef['track_position'])
                    if delta > 1e-6:
                        moved_deltas.append(delta)
            if moved_deltas:
                avg_d = sum(moved_deltas) / len(moved_deltas)
                max_d = max(moved_deltas)
                emit(f"[NUTS] {lname}: {len(moved_deltas)}/{n} segments moved "
                     f"(avg |Δperp|={avg_d:.1f}, max={max_d:.1f})")

            # Report physical width used (helps diagnose dilution issues)
            if segs:
                total_w = sum(s.width for s in segs)
                min_p = min(s.track_position - s.width/2.0 for s in segs)
                max_p = max(s.track_position + s.width/2.0 for s in segs)
                emit(f"[NUTS] {lname}: total bus width {total_w:.1f} units, "
                     f"spanning perpendicular interval [{min_p:.1f}, {max_p:.1f}]")

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
        nuts = buda.NUTSEngine(self.fp, self.layers)
        nuts.set_track_pitch(self._nuts_pitch)
        if self.planner is not None:
            nuts.set_extra_grid_points(
                list(self.planner.get_x_grid()),
                list(self.planner.get_y_grid()))

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
        with buda.ostream_redirect():
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

        if self.detailed_result is not None:
            self._run_detailed_nuts(bit_order=self._detailed_bit_order)
            return self.nuts_result, self.detailed_result

        return self.nuts_result

    def _replan_layers(self):
        """Re-run planner layer assignment on the live planner, honoring
        topology_pinned wrappers, and copy the assignments back onto the
        wrappers.  Band usage is reset first (build_congestion_map): the
        previous run's demand is still recorded on the cuts, and re-planning
        on top of it would double-count every bundle.

        No-op when the planner has not run yet — the pinned selection is
        then honored by the next run_planner.
        """
        if self.planner is None:
            return
        self.planner.build_congestion_map()
        with buda.ostream_redirect():
            assignments = self.planner.optimize_topologies(
                self.bundles, self._planner_iterations)
        bid_to_wrapper = {w.original_bundle.id: w for w in self.bundles}
        for asn in assignments:
            w = bid_to_wrapper.get(asn.bundle_id)
            if w is not None:
                w.selected_topology_index = asn.topo_index
                w.assigned_v_layer = asn.v_layer_id
                w.assigned_h_layer = asn.h_layer_id
                w.seg_layers = list(asn.seg_layers)
                w.seg_perp = list(asn.seg_perp)

    def _rerun_all(self):
        """Apply sidecar topology selections, re-run planner layer assignment,
        then re-run full NUTS.

        Called by the TopoExplorer "Re-run & Refresh" button.
        Returns the updated NUTSResult (also stored in self.nuts_result).
        """
        # Pin topology indices from sidecar.
        self._apply_selections()

        # Re-run the planner so that assigned_h_layer / assigned_v_layer / seg_layers
        # are updated to match the new topology's segment directions.  The planner
        # respects topology_pinned=True set by _apply_selections() and will not
        # override the user's topology choice.
        self._replan_layers()

        layer_names = self._make_layer_names()
        nuts = buda.NUTSEngine(self.fp, self.layers)
        nuts.set_track_pitch(self._nuts_pitch)
        if self.planner is not None:
            nuts.set_extra_grid_points(
                list(self.planner.get_x_grid()),
                list(self.planner.get_y_grid()))
        before = self._segment_states_from_topology()
        self.nuts_result = nuts.run(self.bundles)
        diag = self._nuts_diagnostics(self.nuts_result, layer_names, before)
        self._write_nuts_log(layer_names, append=True,
                             rerun_layer_name="topo-rerun", extra_lines=diag)

        if self.detailed_result is not None:
            self._run_detailed_nuts(bit_order=self._detailed_bit_order)
            return self.nuts_result, self.detailed_result

        return self.nuts_result

    def _run_detailed_nuts(self, bit_order="LO_HI"):
        """Execute bit-level track assignment using DetailedNUTSEngine."""
        if self.nuts_result is None or self.routing_grid is None:
            return None

        bid_to_nbits = {w.original_bundle.id: len(w.original_bundle.get_net_names())
                        for w in self.bundles}
        # Build ConnTopology per bundle for endpoint adj info.
        bid_to_cs = {}
        for w in self.bundles:
            if not w.candidates or w.selected_topology_index < 0 or w.selected_topology_index >= len(w.candidates):
                bid_to_cs[w.original_bundle.id] = []
                continue
            ct = buda.ConnTopology()
            ct.build(w.candidates[w.selected_topology_index], self.fp)
            bid_to_cs[w.original_bundle.id] = list(ct.segs())

        bus_segs = []
        for ts in self.nuts_result.segments:
            bs = buda.BusSegment()
            bs.bundle_id   = ts.bundle_id
            bs.seg_idx     = ts.seg_idx
            bs.layer       = ts.layer
            bs.span_lo     = ts.span_lo
            bs.span_hi     = ts.span_hi
            bs.interval_lo = ts.interval_lo
            bs.interval_hi = ts.interval_hi
            bs.bit_width   = bid_to_nbits.get(ts.bundle_id, 1)
            bs.bit_order   = bit_order
            bs.abstract_pos = ts.track_position

            # Populate connections from ConnTopology.
            cs_list = bid_to_cs.get(ts.bundle_id, [])
            if ts.seg_idx < len(cs_list):
                cs = cs_list[ts.seg_idx]
                for conn in cs.conns:
                    if conn.kind == buda.SegConnKind.SEG:
                        c = buda.BusSegmentConn()
                        c.seg_idx     = conn.seg_idx
                        c.at_pos      = float(conn.at_pos)
                        c.is_endpoint = conn.is_endpoint
                        mid = 0.5 * (cs.along_lo + cs.along_hi)
                        c.lo_end      = (c.at_pos <= mid)
                        bs.connections.append(c)

            bus_segs.append(bs)

        engine = buda.DetailedNUTSEngine(self.routing_grid)
        with buda.ostream_redirect():
            self.detailed_result = engine.run(bus_segs)

        n_net = len(self.detailed_result.net_segments)
        n_unplaced = self.detailed_result.num_unplaced
        print(f"[DetailedNUTS] {n_net} net segments placed, "
              f"{n_unplaced} bits unplaced.")
        return self.detailed_result

    def do_command(self, cmd_line):
        parts = cmd_line.strip().split()
        if not parts or parts[0].startswith('#'): return
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd == "add_block":
            # Single-rect: add_block <name> <x1> <y1> <x2> <y2> [corner_margin ...]
            # Multi-rect:  add_block <name> rect <x1> <y1> <x2> <y2> [rect ...] [corner_margin ...]
            name = args[0]
            if len(args) > 1 and args[1].lower() == "rect":
                rects = []
                i = 1
                while i < len(args) and args[i].lower() == "rect":
                    x1r, y1r, x2r, y2r = int(args[i+1]), int(args[i+2]), int(args[i+3]), int(args[i+4])
                    rects.append((x1r, y1r, x2r, y2r))
                    i += 5
                # Optional teg_mode keyword after rects
                teg_mode = buda.TegMode.THRU
                if i < len(args) and args[i].lower() == "teg_mode":
                    i += 1
                    if i < len(args):
                        teg_mode = buda.TegMode.OVER if args[i].lower() == "over" else buda.TegMode.THRU
                        i += 1
                self.fp.add_block_rects(name, rects, teg_mode)
                x1 = min(r[0] for r in rects); y1 = min(r[1] for r in rects)
                x2 = max(r[2] for r in rects); y2 = max(r[3] for r in rects)
                rest = list(args[i:])
            else:
                x1, y1, x2, y2 = int(args[1]), int(args[2]), int(args[3]), int(args[4])
                self.fp.add_block(name, x1, y1, x2, y2)
                rest = list(args[5:])
            if rest and rest[0].lower() == "corner_margin":
                rest = rest[1:]
                kws = {}
                i = 0
                while i < len(rest):
                    kw = rest[i].lower()
                    if kw in ("dx", "dy", "pct_h", "pct_v") and i + 1 < len(rest):
                        kws[kw] = float(rest[i + 1]); i += 2
                    else: i += 1
                # Resolve to absolute dx, dy
                cm_dx = cm_dy = 0
                if "dx" in kws:    cm_dx = int(round(kws["dx"]))
                if "dy" in kws:    cm_dy = int(round(kws["dy"]))
                if "pct_h" in kws: cm_dx = int(round((x2 - x1) * kws["pct_h"] / 100.0))
                if "pct_v" in kws: cm_dy = int(round((y2 - y1) * kws["pct_v"] / 100.0))
                # If only one axis specified, mirror to the other
                if "dx" in kws and "dy" not in kws and "pct_v" not in kws: cm_dy = cm_dx
                if "dy" in kws and "dx" not in kws and "pct_h" not in kws: cm_dx = cm_dy
                if "pct_h" in kws and "pct_v" not in kws and "dy" not in kws: cm_dy = cm_dx
                if "pct_v" in kws and "pct_h" not in kws and "dx" not in kws: cm_dx = cm_dy
                if cm_dx > 0 or cm_dy > 0:
                    self.fp.set_block_corner_margin(name, cm_dx, cm_dy)
        elif cmd == "corner_margin":
            # Syntax: corner_margin [dx <dx>] [dy <dy>] [pct_h <pct>] [pct_v <pct>]
            #      or corner_margin <dx> [<dy>]
            kws = {}
            i = 0
            while i < len(args):
                kw = args[i].lower()
                if kw in ("dx", "dy") and i + 1 < len(args):
                    kws[kw] = float(args[i + 1]); i += 2
                elif kw[0].isdigit() or (kw[0] == '-' and len(kw) > 1 and kw[1].isdigit()): # Positional
                    if "dx" not in kws: kws["dx"] = float(kw)
                    elif "dy" not in kws: kws["dy"] = float(kw)
                    i += 1
                elif kw in ("pct_h", "pct_v"):
                    print(f"Error: corner_margin pct_h/pct_v not supported globally "
                          f"(no single block dimension to use). Use dx/dy instead.")
                    i += 2
                else:
                    i += 1
            cm_dx = int(round(kws.get("dx", 0)))
            cm_dy = int(round(kws.get("dy", 0)))
            if "dx" in kws and "dy" not in kws: cm_dy = cm_dx
            if "dy" in kws and "dx" not in kws: cm_dx = cm_dy
            self.fp.set_global_corner_margin(cm_dx, cm_dy)
            self._corner_margin = (cm_dx, cm_dy)
        elif cmd == "set_min_stub_length":
            if args: self.fp.set_min_stub_length(int(args[0]))
        elif cmd == "set_min_stub_length_dir":
            if len(args) >= 2:
                dstr = args[0].upper()
                val = int(args[1])
                if dstr in ("H", "HORIZONTAL"):
                    self.fp.set_min_stub_length_dir(buda.LayerDir.HORIZONTAL, val)
                elif dstr in ("V", "VERTICAL"):
                    self.fp.set_min_stub_length_dir(buda.LayerDir.VERTICAL, val)
                else:
                    print(f"Error: unknown direction '{args[0]}' — use H or V")
        elif cmd == "set_min_stub_length_layer":
            if len(args) >= 2:
                lname = args[0]
                val = int(args[1])
                lid = self._layer_name_map.get(lname)
                if lid is not None:
                    self.fp.set_min_stub_length_layer(lid, val)
                else:
                    print(f"Error: unknown layer '{lname}'")
        elif cmd == "detour_channel":
            # Usage: detour_channel <dir> <size> [<dir> <size> ...]
            # dir : N/S/E/W (single), Y (N+S), X (E+W), A (all four).
            # size: outer-band width in layout units; negative resets to auto.
            # Multiple dir/size pairs may appear in one command, e.g.:
            #   detour_channel Y 50 X 30
            i = 0
            while i + 1 < len(args):
                dirs = args[i]
                try:
                    size = int(args[i + 1])
                except ValueError:
                    print(f"Error: detour_channel size must be an integer, got '{args[i+1]}'")
                    break
                self.fp.set_detour_channel(dirs, size)
                i += 2
        elif cmd == "add_keepout":
            # Usage: add_keepout <x1> <y1> <x2> <y2> <layer1> <layer2> ...
            if len(args) < 5:
                print("Error: add_keepout requires x1 y1 x2 y2 and at least one layer")
                return
            try:
                x1, y1 = int(float(args[0])), int(float(args[1]))
                x2, y2 = int(float(args[2])), int(float(args[3]))
                layer_ids = []
                for name in args[4:]:
                    if name.isdigit():
                        layer_ids.append(int(name))
                    elif name in self._layer_name_map:
                        layer_ids.append(self._layer_name_map[name])
                    else:
                        print(f"Warning: unknown layer '{name}' in add_keepout")

                if not layer_ids:
                    print("Error: no valid layers specified for add_keepout")
                    return

                # 1. Update Floorplan (for CongestionPlanner / Stage 7)
                self.fp.add_keepout_zone(x1, y1, x2, y2, layer_ids)

                # 2. Update RoutingGrid (for DetailedNUTS / Stage 9)
                if self.routing_grid:
                    for lid in layer_ids:
                        if self.routing_grid.has_layer(lid):
                            self.routing_grid.add_keepout(lid, x1, y1, x2, y2)

                print(f"[Floorplan] Added keepout zone at ({x1},{y1})-({x2},{y2}) "
                      f"for layers {layer_ids}")
            except (ValueError, IndexError):
                print("Error: invalid arguments for add_keepout")

        elif cmd == "bdb_net_mode":
            # bdb_net_mode on|off
            if len(args) < 1 or args[0].lower() not in ('on', 'off'):
                print("Error: bdb_net_mode requires on|off"); return
            self.bdb_net_mode = (args[0].lower() == 'on')
            print(f"[BDB] net mode {'enabled' if self.bdb_net_mode else 'disabled'}")
        elif cmd == "add_cell_pin":
            # add_cell_pin <cell> <pin_name> [INPUT|OUTPUT|INOUT] [<px> <py>]
            if self.bdb is None:
                print("Error: add_cell_pin requires an open BDB (use open_bdb first)"); return
            if len(args) < 2:
                print("Error: add_cell_pin requires <cell> <pin_name> [dir] [px py]"); return
            cell_name = args[0]; pin_name = args[1]
            direction = args[2].upper() if len(args) > 2 else "INOUT"
            px = float(args[3]) if len(args) > 3 else -1.0
            py = float(args[4]) if len(args) > 4 else -1.0
            self.bdb.add_cell_pin(cell_name, pin_name, direction, px, py)
        elif cmd == "add_net":
            # Syntax A (directed):   add_net <name> <drv_pin> <rcv_pins_csv>
            # Syntax B (undirected): add_net <name> <pin1> <pin2_csv> unknown
            unknown_dir = len(args) >= 4 and args[3].lower() == "unknown"
            name, drv_pin, rcv_str = args[0], args[1], args[2]
            rcv_pins = rcv_str.split(',')
            drv_inst = drv_pin.split('.')[0]
            rcv_insts = [r.split('.')[0] for r in rcv_pins]
            if not unknown_dir and drv_inst in rcv_insts:
                print(f"Error: block '{drv_inst}' is used as both driver and receiver in net '{name}'")
                sys.exit(1)
            self.netlist.add_net(name, drv_pin, rcv_pins)
            self._net_endpoints[name] = (drv_inst, rcv_insts)
            if self.bdb is not None and self.bdb_net_mode:
                if unknown_dir:
                    self.bdb.add_net_pins_undirected(name, [drv_pin] + rcv_pins)
                else:
                    self.bdb.add_net_pins(name, drv_pin, rcv_pins)
        elif cmd == "add_bus":
            # Syntax A (directed):   add_bus <prefix>[N] <drv_pin> <rcv_pin>
            # Syntax B (undirected): add_bus <prefix>[N] <pin1> <pin2> unknown
            import re
            unknown_dir = args and args[-1].lower() == "unknown"
            bus_args = args[:-1] if unknown_dir else args
            m = re.match(r'^(.+)\[(\d+)(?::(\d+))?\]$', bus_args[0])
            if not m:
                print(f"Error: bad add_bus syntax '{bus_args[0]}' — expected name[N] or name[lo:hi]")
                return
            prefix = m.group(1)
            lo = int(m.group(2))
            hi = int(m.group(3)) if m.group(3) is not None else lo - 1
            if m.group(3) is None:      # name[N]  → indices 0 … N-1
                lo, hi = 0, int(m.group(2)) - 1
            drv_pin  = bus_args[1]
            rcv_pins = bus_args[2].split(',')
            drv_inst = drv_pin.split('.')[0]
            rcv_insts = [r.split('.')[0] for r in rcv_pins]
            if not unknown_dir and drv_inst in rcv_insts:
                print(f"Error: block '{drv_inst}' is used as both driver and receiver in bus '{prefix}'")
                sys.exit(1)
            for i in range(lo, hi + 1):
                net_name = f"{prefix}_{i}"
                self.netlist.add_net(net_name, drv_pin, rcv_pins)
                self._net_endpoints[net_name] = (drv_inst, rcv_insts)
                if self.bdb is not None and self.bdb_net_mode:
                    if unknown_dir:
                        self.bdb.add_net_pins_undirected(net_name, [drv_pin] + rcv_pins)
                    else:
                        self.bdb.add_net_pins(net_name, drv_pin, rcv_pins)
        elif cmd == "def_layer":
            # def_layer <id> <name> <H|V> [TOP|LOW] <overhead%>
            #           [span_min N] [span_max N] [kSpan K]
            # TOP/LOW is optional; omitting it means non-TOP. LOW is accepted for
            # backward compatibility and treated as non-TOP.
            lid, name, dirstr = args[0], args[1], args[2]
            rest = list(args[3:])
            if rest and rest[0].upper() in ("TOP", "LOW"):
                typestr = rest.pop(0).upper()
            else:
                typestr = "NONE"
            ovh = rest.pop(0)
            # Parse optional keyword args
            span_min = span_max = kspan_override = None
            i = 0
            while i < len(rest):
                kw = rest[i].lower()
                if kw == "span_min":    span_min = int(rest[i+1]);    i += 2
                elif kw == "span_max":  span_max = int(rest[i+1]);    i += 2
                elif kw == "kspan":     kspan_override = float(rest[i+1]); i += 2
                else: i += 1
            ldir  = buda.LayerDir.HORIZONTAL if dirstr.upper()=="H" else buda.LayerDir.VERTICAL
            ltype = buda.LayerType.TOP if typestr == "TOP" else buda.LayerType.LOW
            self.layers.add_layer(int(lid), name, ldir, ltype)
            if span_min is not None or span_max is not None:
                smin = span_min if span_min is not None else 0
                smax = span_max if span_max is not None else 1_000_000_000
                self.layers.set_layer_span(int(lid), smin, smax)
            if kspan_override is not None:
                self.layers.set_layer_kspan(int(lid), kspan_override)
            ovh_val = float(ovh)
            if ovh_val > 0.0:
                self.layers.set_layer_overhead(int(lid), ovh_val)
                self._layer_overheads[int(lid)] = ovh_val
            self._layer_name_map[name] = int(lid)

        elif cmd == "set_planner_param":
            name_p, value_p = args[0], float(args[1])
            # Always record in the stash: run_planner builds a fresh
            # CongestionPlanner seeded from _planner_params, so a value set
            # between runs must survive until the next run.
            self._planner_params[name_p] = value_p
            if self.planner is not None:
                self.planner.set_planner_param(name_p, value_p)
        elif cmd == "run_bundler":
            self.bundler.set_strategy(buda.Strategy.STRICT)
            raw_bundles = self.bundler.run(self.netlist)
            self.bundles = []
            for b in raw_bundles:
                w = buda.BundleWrapper()
                w.original_bundle = b
                w.width = len(b.get_net_names()) * 1.5 # 1.5 layout-units per bit
                self.bundles.append(w)
            print(f"Bundler created {len(self.bundles)} hbundles.")
        elif cmd == "run_hier_bundler":
            # run_hier_bundler [depth <N>]
            if self.bdb is None:
                print("Error: run_hier_bundler requires an open BDB (use open_bdb first)"); return
            max_depth = 1
            if "depth" in args:
                idx = list(args).index("depth")
                if idx + 1 < len(args):
                    max_depth = int(args[idx + 1])
            hb = buda.HierarchicalBundler(self.bdb)
            raw_bundles = hb.run(max_depth)
            self.bundles = []
            for b in raw_bundles:
                w = buda.BundleWrapper()
                w.original_bundle = b
                w.width = len(b.get_net_names()) * 1.5
                self.bundles.append(w)
            self._hier_bundles_orig = list(self.bundles)  # snapshot for dump_hbundles
            counts = {}
            for b in raw_bundles:
                counts[b.level] = counts.get(b.level, 0) + 1
            summary = ", ".join(f"D{d}: {n}" for d, n in sorted(counts.items()))
            print(f"HierBundler: {len(raw_bundles)} hbundles ({summary})")
            # Warn about nets that had pins in BDB but ended up in no bundle.
            bundled_nets: set[str] = set()
            for b in raw_bundles:
                bundled_nets.update(b.get_net_names())
            all_bdb_nets = {r.name for r in self.bdb.all_nets()}
            dropped = sorted(all_bdb_nets - bundled_nets)
            if dropped:
                shown = dropped[:5]
                ellipsis_str = f" … and {len(dropped)-5} more" if len(dropped) > 5 else ""
                print(f"  Warning: {len(dropped)} net(s) not placed in any bundle "
                      f"(possibly UNKNOWN direction or missing receiver): "
                      f"{', '.join(shown)}{ellipsis_str}")
        elif cmd == "dump_hbundles":
            # Usage: dump_hbundles [expanded] [depth N]
            # Without 'expanded': prints the pre-expansion HBundle list (from _hier_bundles_orig).
            # With 'expanded':    prints the current self.bundles (post-expansion after run_planner hier).
            # With 'depth N':     filters to bundles at level N only.
            use_expanded = "expanded" in args
            filter_depth = None
            if "depth" in args:
                idx = list(args).index("depth")
                if idx + 1 < len(args):
                    filter_depth = int(args[idx + 1])
            source = self.bundles if use_expanded else self._hier_bundles_orig
            if not source:
                label = "expanded bundles" if use_expanded else "original HBundles"
                print(f"  (no {label} — run run_hier_bundler first)")
            else:
                for w in source:
                    b = w.original_bundle
                    if filter_depth is not None and b.level != filter_depth:
                        continue
                    if b.drv_spec_depth >= 0:
                        kind = "cross-level"
                    elif b.cell_context:
                        kind = f"cell:{b.cell_context}"
                    else:
                        kind = "cross-block"
                    short_reason = b.reason[:50].rstrip(',')
                    cands = len(w.candidates)
                    inst_str = ""
                    if b.instances:
                        insts = list(b.instances)
                        shown = insts[:3]
                        ellipsis = "…" if len(insts) > 3 else ""
                        inst_str = f"  [{', '.join(shown)}{ellipsis}]"
                    print(f"hb-{b.id:<3}  D{b.level}  {kind:<24}  \"{short_reason}\"  "
                          f"nets={len(b.get_net_names())}  cands={cands}{inst_str}")
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
            topo_gen = buda.TopologyGenerator(self.fp)
            h_layer = self.layers.get_top_layer(buda.LayerDir.HORIZONTAL)
            v_layer = self.layers.get_top_layer(buda.LayerDir.VERTICAL)
            if h_layer != -1 and v_layer != -1:
                topo_gen.set_layer_ids(h_layer, v_layer)
            if use_center:
                topo_gen.set_busterm_mode(False)
            if use_double_detour:
                topo_gen.set_double_detour(True)
            found = False
            for w in self.bundles:
                if w.original_bundle.get_net_names()[0].startswith(hint):
                    w.candidates = topo_gen.generate_candidates(src, dsts)
                    label = f"{src}->{dsts[0]}" if len(dsts) == 1 else f"{src}->[{','.join(dsts)}]"
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
            topo_gen = buda.TopologyGenerator(self.fp)
            h_layer = self.layers.get_top_layer(buda.LayerDir.HORIZONTAL)
            v_layer = self.layers.get_top_layer(buda.LayerDir.VERTICAL)
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
                w.candidates = topo_gen.generate_candidates(src, dsts)
                label = f"{src}->{dsts[0]}" if len(dsts) == 1 else f"{src}->[{','.join(dsts)}]"
                print(f"Generated {len(w.candidates)} topologies for bundle "
                      f"{w.original_bundle.id} ({label})")

        elif cmd == "generate_hier_topologies":
            # generate_hier_topologies [center_mode] [double_detour]
            # Generates topology candidates for all HBundles produced by
            # run_hier_bundler.  Three cases per bundle:
            #   (a) cell-level (cell_context set)     → cell-local floorplan
            #   (c) cross-level (drv_spec_depth >= 0) → custom floorplan from actual endpoint blocks
            #   (b) same-level cross-block             → BDB depth-D floorplan
            if self.bdb is None:
                print("Error: generate_hier_topologies requires an open BDB"); return
            use_center        = "center_mode"   in args
            use_double_detour = "double_detour" in args

            # Cache floorplans keyed by (depth, is_cell_local, instance_or_empty)
            fp_cache = {}
            total_candidates = 0
            comps_by_name = {c.name: c for c in self.bdb.all_components()}

            for w in self.bundles:
                n = self._generate_hier_topo_one(w, use_center, use_double_detour,
                                                  fp_cache, comps_by_name)
                total_candidates += n
            print(f"generate_hier_topologies: {len(self.bundles)} bundles, "
                  f"{total_candidates} total candidates")

        elif cmd == "generate_topologies_for_hbundle":
            # Usage: generate_topologies_for_hbundle <bundle_id> [center_mode] [double_detour]
            if not args:
                print("Error: generate_topologies_for_hbundle requires a bundle_id"); return
            if self.bdb is None:
                print("Error: generate_topologies_for_hbundle requires an open BDB"); return
            try:
                bid = int(args[0])
            except ValueError:
                print(f"Error: invalid bundle_id {args[0]!r}"); return
            use_center        = "center_mode"   in args[1:]
            use_double_detour = "double_detour" in args[1:]
            target_w = next((w for w in self.bundles if w.original_bundle.id == bid), None)
            if target_w is None:
                orig_w = next((w for w in self._hier_bundles_orig
                               if w.original_bundle.id == bid), None)
                if orig_w is not None:
                    print(f"Note: bundle {bid} was expanded by run_planner hier — "
                          f"re-run generate_hier_topologies before planning.")
                else:
                    print(f"Error: bundle {bid} not found")
                return
            fp_cache = {}
            comps_by_name = {c.name: c for c in self.bdb.all_components()}
            n = self._generate_hier_topo_one(target_w, use_center, use_double_detour,
                                              fp_cache, comps_by_name)
            print(f"generate_topologies_for_hbundle: bundle {bid} — {n} candidates")

        elif cmd == "run_planner":
            if args and args[0] == "post_nuts":
                # Stage 4c: post-NUTS stub layer reassignment.
                # Syntax: post_nuts [V [short [long]]] [H [short [long]]]
                # Bare "post_nuts" (no letter) → V with defaults (backward compat).
                _V_DEFAULTS = (80.0, 200.0)
                _H_DEFAULTS = (150.0, 400.0)
                rest = args[1:]
                v_thresholds = None
                h_thresholds = None
                i = 0
                while i < len(rest):
                    tok = rest[i].upper()
                    if tok in ("V", "H"):
                        # Consume up to two following numeric tokens as thresholds.
                        defaults = _V_DEFAULTS if tok == "V" else _H_DEFAULTS
                        s = float(rest[i + 1]) if i + 1 < len(rest) and rest[i + 1].replace('.','',1).isdigit() else defaults[0]
                        l = float(rest[i + 2]) if i + 2 < len(rest) and rest[i + 2].replace('.','',1).isdigit() else defaults[1]
                        # Advance past any numeric tokens we consumed.
                        i += 1
                        if i < len(rest) and rest[i].replace('.','',1).isdigit():
                            i += 1
                            if i < len(rest) and rest[i].replace('.','',1).isdigit():
                                i += 1
                        if tok == "V":
                            v_thresholds = (s, l)
                        else:
                            h_thresholds = (s, l)
                    else:
                        print(f"Warning: run_planner post_nuts — unexpected token '{rest[i]}', ignored")
                        i += 1
                # Bare "post_nuts" with no direction letters → V with defaults.
                if v_thresholds is None and h_thresholds is None:
                    v_thresholds = _V_DEFAULTS
                self._run_post_nuts_planner(v_thresholds, h_thresholds)
            elif args and args[0] == "hier":
                # run_planner hier [N]
                # Hierarchy-aware planning: expand cell-level bundles to per-instance
                # absolute-coord wrappers, assign priorities, then run the flat planner.
                if self.bdb is None:
                    print("Error: run_planner hier requires an open BDB"); return
                iterations = int(args[1]) if len(args) > 1 else 5
                # Apply user-pinned selections to template wrappers BEFORE expansion
                # so topology_pinned + pinned_seg_layers propagate to all instances.
                self._apply_selections()
                # Expand cell-level bundles → per-instance absolute-coord wrappers.
                # Each expanded wrapper gets a unique HBundle ID.
                expanded, self._hier_expansion_map = self._expand_hier_bundles(self.bundles)
                # priority = -(level * 10000 + n_candidates): higher routes first.
                # Depth-0 before depth-1; fewer candidates (less flexibility) first.
                for w in expanded:
                    b = w.original_bundle
                    w.priority = -(b.level * 10_000 + len(w.candidates))
                    w.level    = b.level   # for the per-level planning summary
                self.planner = buda.CongestionPlanner(self.fp, self.layers)
                for pname, pval in self._planner_params.items():
                    self.planner.set_planner_param(pname, pval)
                self.planner.build_congestion_map()
                self._planner_iterations = iterations
                with buda.ostream_redirect():
                    assignments = self.planner.optimize_topologies(expanded, iterations)
                # Apply assignments.  Each expanded wrapper has a unique HBundle ID so
                # this lookup is unambiguous even for multiple cell instances.
                bid_to_wrapper = {w.original_bundle.id: w for w in expanded}
                for asn in assignments:
                    w = bid_to_wrapper.get(asn.bundle_id)
                    if w is not None:
                        w.selected_topology_index = asn.topo_index
                        w.assigned_v_layer = asn.v_layer_id
                        w.assigned_h_layer = asn.h_layer_id
                        w.seg_layers = list(asn.seg_layers)
                        w.seg_perp = list(asn.seg_perp)
                self.bundles = expanded
                print(f"run_planner hier: {len(self.bundles)} wrappers after expansion")
            else:
                self.planner = buda.CongestionPlanner(self.fp, self.layers)
                for pname, pval in self._planner_params.items():
                    self.planner.set_planner_param(pname, pval)
                self.planner.build_congestion_map()
                # Apply architect-pinned selections BEFORE optimizing so the
                # planner scores the correct topology and assigns layers for it.
                self._apply_selections()
                self._planner_iterations = int(args[0]) if args else 5
                with buda.ostream_redirect():
                    assignments = self.planner.optimize_topologies(self.bundles, self._planner_iterations)
                # Apply planner layer decisions (vector copy in C++ means we must apply here).
                bid_to_wrapper = {w.original_bundle.id: w for w in self.bundles}
                for asn in assignments:
                    w = bid_to_wrapper.get(asn.bundle_id)
                    if w is not None:
                        w.selected_topology_index = asn.topo_index
                        w.assigned_v_layer = asn.v_layer_id
                        w.assigned_h_layer = asn.h_layer_id
                        w.seg_layers = list(asn.seg_layers)
                        w.seg_perp = list(asn.seg_perp)
        elif cmd == "run_nuts":
            # Usage: run_nuts [track_pitch]
            pitch = float(args[0]) if args else 1.0
            self._nuts_pitch = pitch
            nuts = buda.NUTSEngine(self.fp, self.layers)
            nuts.set_track_pitch(pitch)

            if self.planner is not None:
                nuts.set_extra_grid_points(
                    list(self.planner.get_x_grid()),
                    list(self.planner.get_y_grid()))
            # Snapshot topology-derived initial spans before the solve.
            before = self._segment_states_from_topology()
            # C++ prints its own [NUTS] N segments placed across K layer(s) line.
            with buda.ostream_redirect():
                self.nuts_result = nuts.run(self.bundles)
            layer_names = self._make_layer_names()
            diag = self._nuts_diagnostics(self.nuts_result, layer_names, before)
            self._write_nuts_log(layer_names, extra_lines=diag)
        elif cmd == "def_track_pattern":
            # Usage: def_track_pattern <layer_id> <origin> [<type> <width> <space_after>] ...
            # Example: def_track_pattern 4 0.0  POWER 2.0 1.0  SIGNAL 1.0 1.0  GROUND 2.0 1.0
            if len(args) < 2:
                print("Error: def_track_pattern requires layer_id and origin")
                return
            layer_id = int(args[0])
            origin   = float(args[1])
            slots = []
            i = 2
            while i + 2 < len(args):
                slot_type   = args[i]
                width       = float(args[i + 1])
                space_after = float(args[i + 2])
                slots.append(buda.TrackSlot(
                    type=slot_type, label=slot_type.lower(),
                    width=width, space_after=space_after))
                i += 3
            if not slots:
                print("Error: def_track_pattern requires at least one slot triple")
                return
            pat = buda.TrackPattern(origin=origin, slots=slots)
            if self.routing_grid is None:
                self.routing_grid = buda.RoutingGridStack()

            # Resolve layer direction.
            is_h = True
            if self.layers.has_layer(layer_id):
                is_h = (self.layers.get_layer_dir(layer_id) == buda.LayerDir.HORIZONTAL)

            self.routing_grid.define_layer(layer_id, pat, is_h)
            self.layers.set_layer_dilution(layer_id, pat.dilution_factor())
            # Measured per-bit channel cost: one signal track every
            # unit_pitch/n_signals units.  Supersedes the density model
            # (base width x dilution) in planner/NUTS effective widths.
            n_sig = sum(1 for s in slots if s.type == "SIGNAL")
            if n_sig > 0:
                self.layers.set_bit_pitch(layer_id, pat.unit_pitch() / n_sig)

            # Re-apply any existing keepouts to this new layer grid.
            for koz in self.fp.get_keepout_zones():
                if layer_id in koz.layer_ids:
                    self.routing_grid.add_keepout(layer_id, 
                                                 koz.bbox.x1, koz.bbox.y1, 
                                                 koz.bbox.x2, koz.bbox.y2)

            print(f"[RoutingGrid] Layer {layer_id}: {len(slots)} slots, "
                  f"unit_pitch={pat.unit_pitch():.3f}, "
                  f"signal_density={pat.signal_density():.3f}, dilution={pat.dilution_factor():.3f}")

        elif cmd == "report_overhead":
            # Usage: report_overhead
            # For each layer, compare the overhead% set in def_layer against the
            # overhead implied by the track pattern.  Prints a suggested corrected
            # def_layer command for any layer where the two values diverge.
            layer_names = self._make_layer_names()

            # Collect all layer IDs that have either a def_layer overhead or a track pattern.
            all_lids: set[int] = set(self._layer_overheads.keys())
            if self.routing_grid is not None:
                for lid in (self.layers.get_layer_ids_by_dir(buda.LayerDir.HORIZONTAL) +
                            self.layers.get_layer_ids_by_dir(buda.LayerDir.VERTICAL)):
                    if self.routing_grid.has_layer(lid):
                        all_lids.add(lid)

            if not all_lids:
                print("[report_overhead] No layers defined.")
                return

            print("[report_overhead] Layer overhead analysis:")
            print(f"  {'Layer':<10} {'def_layer%':>11} {'actual%':>9} {'dilution':>9}  status")
            print(f"  {'-'*10} {'-'*11} {'-'*9} {'-'*9}  ------")
            for lid in sorted(all_lids):
                lname    = layer_names.get(lid, f"L{lid}")
                def_ovh  = self._layer_overheads.get(lid)
                def_str  = f"{def_ovh:.2f}%" if def_ovh is not None else "(not set)"

                if self.routing_grid is not None and self.routing_grid.has_layer(lid):
                    pat          = self.routing_grid.get_layer_grid(lid).effective_pattern_at(0.0, 0.0)
                    actual_ovh   = (1.0 - pat.signal_density()) * 100.0
                    actual_dil   = pat.dilution_factor()

                    if def_ovh is None:
                        status = "MISSING in def_layer"
                    else:
                        diff   = abs(actual_ovh - def_ovh)
                        status = "OK" if diff < 0.05 else f"MISMATCH (diff={diff:.2f}%)"

                    print(f"  {lname:<10} {def_str:>11} {actual_ovh:>8.2f}% {actual_dil:>9.4f}  {status}")

                    if def_ovh is None or abs(actual_ovh - def_ovh) >= 0.05:
                        if self.layers.has_layer(lid):
                            dir_str  = "H" if self.layers.get_layer_dir(lid) == buda.LayerDir.HORIZONTAL else "V"
                            type_str = " TOP" if self.layers.is_top(lid) else ""
                            print(f"    -> suggested: def_layer {lid} {lname} {dir_str}{type_str} {actual_ovh:.2f}")
                else:
                    print(f"  {lname:<10} {def_str:>11} {'(no pattern)':>9}  {'':>9}  no track pattern defined")

        elif cmd == "add_grid_override":
            # Usage: add_grid_override <layer_id> <x1> <y1> <x2> <y2> <origin> [<type> <w> <sp>] ...
            if len(args) < 6:
                print("Error: add_grid_override requires layer_id x1 y1 x2 y2 origin [slots...]")
                return
            layer_id = int(args[0])
            x1, y1 = int(float(args[1])), int(float(args[2]))
            x2, y2 = int(float(args[3])), int(float(args[4]))
            origin = float(args[5])
            slots = []
            i = 6
            while i + 2 < len(args):
                slot_type   = args[i]
                width       = float(args[i + 1])
                space_after = float(args[i + 2])
                slots.append(buda.TrackSlot(
                    type=slot_type, label=slot_type.lower(),
                    width=width, space_after=space_after))
                i += 3
            if not slots:
                print("Error: add_grid_override requires at least one slot triple")
                return
            pat = buda.TrackPattern(origin=origin, slots=slots)
            if self.routing_grid is None:
                self.routing_grid = buda.RoutingGridStack()
            self.routing_grid.add_override(layer_id, x1, y1, x2, y2, pat)
            print(f"[RoutingGrid] Override on layer {layer_id} "
                  f"region=({x1},{y1})-({x2},{y2}): "
                  f"{len(slots)} slots, unit_pitch={pat.unit_pitch():.3f}")
        elif cmd == "run_detailed_nuts":
            # Usage: run_detailed_nuts [lo_hi|hi_lo]
            self._detailed_bit_order = "LO_HI"
            if args and args[0].lower() in ("lo_hi", "hi_lo"):
                self._detailed_bit_order = args[0].upper()

            if self.nuts_result is None:
                print("Error: run_detailed_nuts requires run_nuts to have been called first")
                return
            if self.routing_grid is None:
                print("Error: run_detailed_nuts requires a routing grid (def_track_pattern)")
                return

            self._run_detailed_nuts(bit_order=self._detailed_bit_order)
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
        elif cmd == "select_topology":
            # Usage: select_topology <bundle_id> <topo_id>
            if len(args) < 2:
                print("Error: select_topology requires bundle_id and topo_id (1-based)")
                return
            bid  = int(args[0])
            tid  = int(args[1])
            tidx = tid - 1  # Convert 1-based id to 0-based index
            found = False
            for w in self.bundles:
                if w.original_bundle.id == bid:
                    if tidx < 0 or tidx >= len(w.candidates):
                        print(f"Error: invalid topology id {tid} for bundle {bid}")
                    else:
                        w.selected_topology_index = tidx
                        w.topology_pinned = True
                        print(f"Pinned bundle {bid} to topology {tid}")
                        # If the planner already ran, its per-segment layer
                        # assignments (seg_layers) describe the PREVIOUS
                        # topology's segment list; re-run layer assignment so
                        # NUTS doesn't route the new topology with stale layers.
                        self._replan_layers()
                    found = True
                    break
            if not found:
                # Hier mode: the original bundle was expanded into synthetic-ID wrappers.
                # Look up the original ID in the expansion map and apply to all instances.
                wrappers = self._hier_expansion_map.get(bid, [])
                if wrappers:
                    if tidx < 0 or tidx >= len(wrappers[0].candidates):
                        print(f"Error: invalid topology id {tid} for bundle {bid}")
                    else:
                        for w in wrappers:
                            w.selected_topology_index = tidx
                        n = len(wrappers)
                        print(f"Pinned bundle {bid} to topology {tid} "
                              f"({n} expanded instance{'s' if n > 1 else ''})")
                    found = True
            if not found:
                print(f"Error: bundle {bid} not found")

        elif cmd == "check_connectivity":
            # Usage: check_connectivity [topo|nuts|dnuts] [all]
            # 'all' is only meaningful for the topo stage: checks every candidate
            # topology, not just the selected one.  Automatically used when no
            # topology has been selected yet (i.e. before run_planner).
            stage     = args[0].lower() if args else "dnuts"
            all_cands = len(args) > 1 and args[1].lower() == "all"
            if stage in ("topo", "nuts", "dnuts"):
                self._check_connectivity(stage, all_candidates=all_cands)
            else:
                print(f"Error: unknown stage '{stage}' — use topo, nuts, or dnuts")

        elif cmd == "visualize_topologies":
            if self.no_viz:
                return
            # Usage:
            #   visualize_topologies <hint>         — first matching bundle
            #   visualize_topologies -all [hints…]  — all matching bundles
            #                                         (no hints = every bundle)
            all_mode = args and args[0] == '-all'
            hints    = args[1:] if all_mode else args[:1]

            seen, wrappers = set(), []
            # For cell-level bundles expanded by run_planner hier, deduplicate by
            # (cell_context, reason) so the same cell-level template isn't listed once
            # per instance.  We keep the first instance and annotate it.
            cell_seen: dict[tuple, tuple[object, int]] = {}  # key → (wrapper, count)
            for w in self.bundles:
                if not w.candidates: continue
                bid  = w.original_bundle.id
                b    = w.original_bundle
                if bid in seen: continue
                cell_key = (b.cell_context, b.reason) if b.cell_context else None
                if cell_key is not None:
                    if cell_key in cell_seen:
                        first_w, cnt = cell_seen[cell_key]
                        cell_seen[cell_key] = (first_w, cnt + 1)
                        continue
                    cell_seen[cell_key] = (w, 1)
                net0 = b.get_net_names()[0] if b.get_net_names() else ""
                if not hints or any(net0.startswith(h) for h in hints):
                    wrappers.append(w)
                    seen.add(bid)
                    if not all_mode:
                        break   # single-bundle mode: stop at first match

            if not wrappers:
                print(f"Warning: no bundle with candidates matching {hints or '(any)'}")
            else:
                for w in wrappers:
                    b = w.original_bundle
                    cell_key = (b.cell_context, b.reason) if b.cell_context else None
                    inst_note = ""
                    if cell_key is not None and cell_key in cell_seen:
                        cnt = cell_seen[cell_key][1]
                        if cnt > 1:
                            inst_note = f" ({cnt} instances — showing first)"
                    print(f"  bundle {b.id}: {len(w.candidates)} topologies{inst_note}")
                TopologyExplorer(self.fp, wrappers,
                                 sidecar_path=self._sidecar_path(),
                                 layer_stack=self.layers).show()
        elif cmd == "visualize":
            if self.no_viz:
                return
            rerun_layer_fn = self._rerun_nuts_layer if self.nuts_result is not None else None
            rerun_all_fn   = self._rerun_all        if self.nuts_result is not None else None
            ipc_session = (os.path.splitext(os.path.basename(self.script_path))[0]
                           if self.script_path else None)
            viz = BudaVisualizer(self.fp, self.bundles,
                                 sidecar_path=self.script_path,
                                 rerun_layer_fn=rerun_layer_fn,
                                 rerun_fn=rerun_all_fn,
                                 routing_grid=self.routing_grid,
                                 layer_stack=self.layers,
                                 net_endpoints=self._net_endpoints,
                                 ipc_session=ipc_session)
            viz.draw_blocks()
            if self.planner is not None:
                cuts = self.planner.get_cuts()
                if cuts:
                    viz.draw_congestion_map(cuts, self.planner.get_x_grid(), self.planner.get_y_grid())
            viz.draw_hanan_grid()
            if self.nuts_result is not None:
                viz.draw_nuts_tracks(self.nuts_result)
                if self.detailed_result is not None:
                    viz.draw_detailed_tracks(
                        self.detailed_result, self.routing_grid, self.layers)
            else:
                viz.draw_buses()
            viz.show()
        # ── BDB ────────────────────────────────────────────────────────────
        elif cmd == "open_bdb":
            # open_bdb <path>
            if not args:
                print("Error: open_bdb requires a file path"); return
            bdb_path = args[0]
            if (self._script_stack
                    and not os.path.isabs(bdb_path)
                    and bdb_path != ':memory:'):
                parent_dir = os.path.dirname(self._script_stack[-1])
                bdb_path = os.path.normpath(os.path.join(parent_dir, bdb_path))
            self.bdb = buda.BDB(bdb_path)
        elif cmd == "import_def_lef":
            # import_def_lef <def_path> <lef_path>
            if len(args) < 2:
                print("Error: import_def_lef requires <def_path> <lef_path>"); return
            if self.bdb is None:
                print("Error: open_bdb first"); return
            self.bdb.import_def_lef(args[0], args[1])
        elif cmd == "import_verilog":
            # import_verilog <v_path>
            if not args:
                print("Error: import_verilog requires a file path"); return
            if self.bdb is None:
                print("Error: open_bdb first"); return
            self.bdb.import_verilog(args[0])
        elif cmd == "add_blocks_from_bdb":
            # add_blocks_from_bdb <depth> [deepest|skip|error]
            if not args:
                print("Error: add_blocks_from_bdb requires <depth>"); return
            if self.bdb is None:
                print("Error: open_bdb first"); return
            depth    = int(args[0])
            mode     = args[1].lower() if len(args) > 1 else "deepest"
            if mode not in ("deepest", "skip", "error"):
                print(f"Error: unknown mode {mode!r}; use deepest|skip|error"); return
            self._add_blocks_from_bdb(depth, mode)
        elif cmd == "set_die":
            # set_die <w> <h>
            if len(args) < 2:
                print("Error: set_die requires <w> <h>"); return
            self.bdb.set_die(float(args[0]), float(args[1]))
        elif cmd == "move_comp":
            # move_comp <name> <x> <y>
            if len(args) < 3:
                print("Error: move_comp requires <name> <x> <y>"); return
            if self.bdb is None:
                print("Error: open_bdb first"); return
            self.bdb.move_comp(args[0], float(args[1]), float(args[2]))
        elif cmd == "resize_cell":
            # resize_cell <cell> <w> <h>
            if len(args) < 3:
                print("Error: resize_cell requires <cell> <w> <h>"); return
            if self.bdb is None:
                print("Error: open_bdb first"); return
            self.bdb.resize_cell(args[0], float(args[1]), float(args[2]))
        elif cmd == "add_cell":
            # add_cell <name> <width> <height>
            if len(args) < 3:
                print("Error: add_cell requires <name> <width> <height>"); return
            if self.bdb is None:
                print("Error: open_bdb first"); return
            self.bdb.add_cell(args[0], float(args[1]), float(args[2]))
        elif cmd == "add_inst":
            # add_inst <inst_name> <cell_name> <parent|-> <x> <y>
            # x,y are relative to parent's origin; absolute when parent is "-"
            if len(args) < 5:
                print("Error: add_inst requires <inst_name> <cell_name> "
                      "<parent|-> <x> <y>"); return
            if self.bdb is None:
                print("Error: open_bdb first"); return
            parent = "" if args[2] == "-" else args[2]
            self.bdb.add_inst(args[0], args[1], parent,
                              float(args[3]), float(args[4]))
        elif cmd == "add_inst_to_cell":
            # add_inst_to_cell <parent_cell> <inst_name> <child_cell> <x> <y>
            # Defines the structural contents of parent_cell; no component rows
            # are created until add_inst places an occurrence of parent_cell.
            if len(args) < 5:
                print("Error: add_inst_to_cell requires <parent_cell> <inst_name> "
                      "<child_cell> <x> <y>"); return
            if self.bdb is None:
                print("Error: open_bdb first"); return
            self.bdb.add_inst_to_cell(args[0], args[1], args[2],
                                      float(args[3]), float(args[4]))
        elif cmd == "flip_comp":
            # flip_comp <name> x|y
            if len(args) < 2:
                print("Error: flip_comp requires <name> x|y"); return
            if self.bdb is None:
                print("Error: open_bdb first"); return
            axis = args[1].lower()
            if axis not in ('x', 'y'):
                print(f"Error: flip_comp axis must be 'x' or 'y', got {args[1]!r}"); return
            self.bdb.flip_comp(args[0], axis == 'x')
        elif cmd == "rotate_comp":
            # rotate_comp <name> 90|180|270
            if len(args) < 2:
                print("Error: rotate_comp requires <name> 90|180|270"); return
            if self.bdb is None:
                print("Error: open_bdb first"); return
            try:
                degrees = int(args[1])
            except ValueError:
                print("Error: rotate_comp degrees must be 90, 180, or 270"); return
            self.bdb.rotate_comp(args[0], degrees)
        elif cmd == "add_comp":
            # add_comp <name> <cell> <parent|-> <x1> <y1> <x2> <y2> [leaf]
            # Use "-" for parent to create a root instance.
            if len(args) < 7:
                print("Error: add_comp requires <name> <cell> <parent|-> "
                      "<x1> <y1> <x2> <y2> [leaf]"); return
            if self.bdb is None:
                print("Error: open_bdb first"); return
            parent = "" if args[2] == "-" else args[2]
            is_leaf = True
            if len(args) >= 8:
                is_leaf = args[7].lower() not in ("0", "false", "no", "nonleaf")
            self.bdb.add_comp(args[0], args[1], parent,
                              float(args[3]), float(args[4]),
                              float(args[5]), float(args[6]), is_leaf)
        elif cmd == "derive_busterms":
            # derive_busterms [max_depth]
            # Populate BDB busterm table from the component hierarchy.
            if self.bdb is None:
                print("Error: open_bdb first"); return
            max_depth = int(args[0]) if args else 1
            gen = buda.BustermGen(self.bdb)
            gen.derive(max_depth)
            bts = self.bdb.all_busterms()
            print(f"derive_busterms: {len(bts)} busterms written (depth 0..{max_depth}).")
        elif cmd == "source":
            if not args:
                print("Error: source command requires a file path")
                return

            raw_path = args[0]
            if not raw_path.endswith('.buda') and not os.path.exists(raw_path):
                raw_path += '.buda'

            # Resolve path relative to the current executing script (if any)
            if self._script_stack:
                parent_dir = os.path.dirname(self._script_stack[-1])
                full_path = os.path.normpath(os.path.join(parent_dir, raw_path))
            else:
                full_path = os.path.abspath(raw_path)

            if not os.path.exists(full_path):
                print(f"Error: sourced file not found: {full_path}")
                return

            if self.script_path is None:
                self.script_path = full_path

            self._script_stack.append(full_path)
            try:
                with open(full_path, 'r') as f:
                    for line in f:
                        if not line.strip().startswith('#'):
                            self.do_command(line)
            finally:
                self._script_stack.pop()

    def _check_connectivity(self, stage: str, all_candidates: bool = False):
        if stage in ("nuts", "dnuts") and self.nuts_result is None:
            print("  Error: run_nuts required first.")
            return
        if stage == "dnuts" and self.detailed_result is None:
            print("  Error: run_detailed_nuts required first.")
            return

        # For the topo stage, auto-switch to all-candidates mode when no
        # topology has been selected yet (before run_planner).
        if stage == "topo" and not all_candidates:
            no_selection = all(
                not w.candidates or w.selected_topology_index < 0
                for w in self.bundles
            )
            if no_selection:
                all_candidates = True

        labels = {"topo": "topology", "nuts": "NUTS", "dnuts": "Detailed NUTS"}
        suffix = " (all candidates)" if (all_candidates and stage == "topo") else ""
        print(f"[Check] Verifying {labels[stage]}-level connectivity{suffix}...")

        if self._hier_expansion_map:
            fp_block_names = {name for name, _ in self.fp.get_all_blocks()}
            missing = set()
            for w in self.bundles:
                if w.candidates and w.selected_topology_index >= 0:
                    topo = w.candidates[w.selected_topology_index]
                    for bname in topo.connected_block_names:
                        if bname not in fp_block_names:
                            missing.add(bname)
            if missing:
                shown = sorted(missing)[:5]
                ellipsis_str = "..." if len(missing) > 5 else ""
                print(f"  Warning: {len(missing)} block(s) referenced in topologies "
                      f"but not in floorplan: {', '.join(shown)}{ellipsis_str}")
                print(f"  Hint: call 'add_blocks_from_bdb N skip' for all required depths.")

        # Hier bundles' candidates may live in a cell-local / depth / custom
        # floorplan rather than self.fp; resolve the right one per bundle so the
        # check uses the same coordinate and block-name space the candidates
        # were generated in.  Per-instance wrappers from _expand_hier_bundles
        # (absolute coords, dropped seg_busterms) are excluded and use self.fp.
        hier_fp_cache = {}
        comps_by_name = ({c.name: c for c in self.bdb.all_components()}
                         if self.bdb is not None else {})
        expanded_ids = {id(w)
                        for ws in (self._hier_expansion_map or {}).values()
                        for w in ws}

        total = 0
        for w in self.bundles:
            if not w.candidates:
                continue
            bid = w.original_bundle.id

            b = w.original_bundle
            check_fp = self.fp
            if (self.bdb is not None and isinstance(b, buda.HBundle)
                    and id(w) not in expanded_ids):
                resolved = self._floorplan_for_hbundle(b, hier_fp_cache, comps_by_name)
                if resolved is not None:
                    check_fp = resolved

            if all_candidates and stage == "topo":
                to_check = list(enumerate(w.candidates))
            elif w.selected_topology_index >= 0:
                idx = w.selected_topology_index
                to_check = [(idx, w.candidates[idx])]
            else:
                continue

            for topo_idx, topo in to_check:
                ct = buda.ConnTopology()
                ct.build(topo, check_fp)

                if stage == "topo":
                    res = buda.check_topo(ct, topo, check_fp, bid)
                elif stage == "nuts":
                    res = buda.check_nuts(ct, self.nuts_result, topo, check_fp, self.layers, bid)
                else:
                    num_bits = len(w.original_bundle.get_net_names())
                    res = buda.check_dnuts(ct, self.detailed_result, topo, check_fp,
                                           self.layers, bid, num_bits)

                for v in res.violations:
                    if all_candidates and stage == "topo":
                        print(f"  Bundle {bid} topo {topo_idx + 1} ({topo.type}): {v.message}")
                    else:
                        print(f"  Bundle {bid}: {v.message}")
                    total += 1

        if total == 0:
            print("  Success: no opens found.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('script', nargs='?')
    parser.add_argument('--no-viz', action='store_true',
                        help='skip visualize commands (useful for batch/CI runs)')
    args = parser.parse_args()
    session = BudaSession()
    session.no_viz = args.no_viz
    if args.script:
        script = args.script
        if not os.path.exists(script) and not script.endswith('.buda'):
            script = script + '.buda'
        session.script_path = os.path.abspath(script)

        # Install TeeStream so all Python print() output is mirrored to a flow log.
        # C++ extensions write directly to fd 1 (terminal) and are NOT captured here;
        # their key [NUTS] metrics are mirrored into the nuts log from Python data.
        flow_log_path = session._get_log_path('flow.log')
        try:
            _flow_log_file = open(flow_log_path, 'w', buffering=1)
            sys.stdout = _TeeStream(sys.stdout, _flow_log_file)
            sys.stderr = _TeeStream(sys.stderr, _flow_log_file)
        except OSError as e:
            print(f"Warning: could not open flow log {flow_log_path}: {e}")

        session.do_command(f"source {script}")

if __name__ == "__main__":
    main()
