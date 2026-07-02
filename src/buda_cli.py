# Copyright 2026 Ben Bulent Basaran
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import contextlib
import faulthandler
import hashlib
import io
import json
import math
import os
import re
import sys
import time

# Ensure the compiled extension is loaded from build/ rather than a stale
# copy that might exist alongside this script.
_build = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'build'))
if _build not in sys.path:
    sys.path.insert(0, _build)

# tools/ holds bdb_serialize (used by open_bdb to load *.bdb.sql text fixtures).
_tools = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'tools'))
if _tools not in sys.path:
    sys.path.append(_tools)

# On macOS the native 'macosx' backend can intermittently segfault,
# especially with the IPC timer or when multiple windows open.
# Force TkAgg to ensure stability.
if sys.platform == 'darwin':
    import matplotlib
    matplotlib.use('TkAgg')

import buda

faulthandler.enable()
from buda_viz import BudaVisualizer, TopologyExplorer, collect_candidate_bundles

# Every command BudaSession.do_command() understands. Used to detect typos in
# scripts (e.g. 'add_layer' for 'def_layer') and suggest the closest match.
# Keep in sync with the dispatch chain in do_command().
KNOWN_COMMANDS = frozenset({
    "add_block", "add_blocks_from_bdb", "add_bus", "add_cell", "add_cell_pin",
    "add_comp", "add_grid_override", "add_inst", "add_inst_to_cell", "add_keepout",
    "add_net", "bdb_net_mode", "check_connectivity", "corner_margin",
    "def_layer", "def_track_pattern", "derive_busterms", "detour_channel",
    "dump_hbundles", "dump_topologies", "exit", "flip_comp", "generate_hier_topologies", "generate_topologies",
    "generate_topologies_for_bundle", "generate_topologies_for_hbundle",
    "import_def_lef", "import_verilog", "move_comp", "open_bdb", "refine_busterms",
    "report_overhead", "resize_cell", "ripup_reroute", "rotate_comp", "run_bundler",
    "run_detailed_nuts", "run_hier_bundler", "run_nuts", "run_nuts_on_layer",
    "run_planner", "save_bdb", "select_topologies", "select_topology", "set_die",
    "set_feedthru",
    "set_min_stub_length", "set_min_stub_length_dir", "set_min_stub_length_layer",
    "set_planner_param", "set_track_pitch", "source", "visualize",
    "visualize_topologies",
})

# ripup_reroute tuning (greedy hill-climb over topology selections).
_RR_MAX_CANDIDATES_PER_BUNDLE = 8   # alternate candidates tried per contender / iter
_RR_DEFAULT_MAX_ITER = 10           # outer-loop cap when no arg given


class _Capture:
    """A stdout/stderr replacement installed *per command* during a flow run.

    Buffers everything a command writes — Python prints and C++ output routed
    through sys.stdout via buda.ostream_redirect — so the CLI can persist the
    full detail to the flow log and derive a one-line terminal summary + runtime
    stats afterwards.  `fallback` supplies fileno()/isatty() for the rare bit of
    code that consults them, so fd-level writes still reach the real terminal.
    """
    def __init__(self, fallback):
        self.buf = []
        self._fallback = fallback

    def write(self, data):
        self.buf.append(data)
        return len(data)

    def flush(self):
        pass

    def isatty(self):
        return False

    def fileno(self):
        return self._fallback.fileno()

    def getvalue(self):
        return ''.join(self.buf)


# Regexes that identify the "headline" line of a command's output — the one
# worth echoing to the terminal.  Matched against captured lines bottom-up; the
# last match wins, else we fall back to a line count / the last non-empty line.
_SUMMARY_MARKERS = [re.compile(p, re.I) for p in (
    r'\btotal\b.*(candidate|violation|wrapper|segment|bundle|move|net)',
    r'\b\d+\s+(hbundles|busterms|blocks|wrappers|candidates|net segments|'
    r'segments|bundles|nets|violation)',
    r'segments placed',
    r'bits unplaced',
    r'wrappers after expansion',
    r'materialized',
    r'\bsuccess\b',
    r'no opens',
    r'done:\s*metric',
    r'metric \d+->\d+',
    r'added \d+ blocks',
)]

# Commands that must NOT be redirected/timed: `source` is a container whose
# child commands are each summarized instead, and the visualize commands open
# interactive windows whose output belongs on the terminal.
_PASSTHROUGH_CMDS = frozenset({"source", "visualize", "visualize_topologies"})


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
        self._bundler_strategy = "STRICT"  # last run_[hier_]bundler strategy (for re-persist)
        self.nuts_result = None
        self._layer_overheads = {}   # layer_id -> overhead_percent
        self._planner_params  = {}   # param_name -> value (buffered before planner exists)
        self._net_endpoints   = {}   # net_name -> (driver_instance, [receiver_instances])
        self._layer_name_map = {}    # layer_name -> layer_id
        self._nuts_pitch = 1.0
        self._planner_pitch = None   # pitch the last run_planner reserved bands for
        self._detailed_bit_order = "LO_HI"
       # last track pitch used by run_nuts
        self._planner_iterations = 5 # last iteration count used by run_planner
        self._script_stack = []      # stack of absolute paths of sourced scripts
        self.script_path = None      # set when a .buda script is sourced
        self.routing_grid = None     # RoutingGridStack (stage 8)
        self.detailed_result = None  # DetailedNUTSResult (stage 9)
        self._dogleg_originals = {}  # bid -> pre-split selected_topology_index (restored on re-plan)
        self._dogleg_slot = {}       # bid -> appended candidate index holding the split topology
        self.no_viz = False          # set by --no-viz CLI flag
        self.verbose_conn = False    # set by --verbose-conn: print every per-bit violation
        self.ipc_verbose = False     # set by --ipc-verbose: surface buda_viz/def_viz IPC chatter
        self._die_w = 0.0            # stored by set_die when no BDB is open (flat flow)
        self._die_h = 0.0
        self.bdb = None              # BDB (opened by open_bdb command)
        self._bdb_writeback_src = None  # *.sql to write back to on save_bdb/exit (opt-in)
        self._bdb_writeback_bin = None  # temp binary materialized from that .sql
        self._bdb_added_ids = set()  # component ids loaded into fp via add_blocks_from_bdb
        self._busterm_gen = None     # BustermGen instance (created by derive_busterms)
        self.bdb_net_mode = False    # when True, add_net/add_bus also write to BDB
        self._corner_margin = (0, 0) # (dx, dy) — mirrors fp global corner margin
        self._hier_expansion_map = {}  # original bundle id → [expanded BundleWrappers]
        self._hier_bundles_orig = []   # pre-expansion snapshot set by run_hier_bundler
        self._planner_is_hier = False  # True after `run_planner hier` (self.bundles is expanded)
        self._flow_log = None          # open flow-log file (set by main); enables per-command logging
        self._cmd_stats = []           # per-command (cmd_line, elapsed, nlines, nwarn, nerr) for runtime summary

    def _sidecar_path(self):
        """Return the .json path for the current script, or None."""
        if not self.script_path:
            return None
        base = os.path.splitext(self.script_path)[0]
        return base + '.json'

    def _materialize_bdb_sql(self, sql_path, writeback=False):
        """Rebuild a serialized BDB (*.bdb.sql text) into a throwaway temp binary.

        Returns the temp path. By default the checked-in text fixture is never
        opened for writing, so the routing pipeline (derive_busterms, etc.) cannot
        dirty it. With `writeback=True` the temp binary is armed to be dumped back
        to `sql_path` on `save_bdb`, on the next `open_bdb`, and at exit / end of
        run — an opt-in way to deliberately update a serialized fixture.
        See tools/bdb_serialize.py and docs/internal/bdb_test_data.md.
        """
        import tempfile
        import bdb_serialize
        base = os.path.basename(sql_path)[:-len('.sql')]  # mix.b_db.sql -> mix.b_db
        tmp_dir = tempfile.mkdtemp(prefix='buda_bdb_')
        out = os.path.join(tmp_dir, base)
        bdb_serialize.load(sql_path, out)
        if not hasattr(self, '_tmp_bdbs'):
            self._tmp_bdbs = []
        self._tmp_bdbs.append(out)
        if writeback:
            self._bdb_writeback_src = os.path.abspath(sql_path)
            self._bdb_writeback_bin = out
            print(f"open_bdb: materialized {sql_path} -> temp binary "
                  f"(writeback ON: changes are written back on save_bdb/exit)")
        else:
            print(f"open_bdb: materialized {sql_path} -> temp binary "
                  f"(changes not written back)")
        return out

    def _write_bdb_sql(self):
        """Serialize the working (temp) binary back to its writeback source `.sql`.

        No-op (returns False) unless a writeback target is armed. Keeps the target
        armed so a later save / exit re-dumps the latest state.
        """
        if not self._bdb_writeback_src:
            return False
        import bdb_serialize
        bdb_serialize.dump(self._bdb_writeback_bin, self._bdb_writeback_src)
        return True

    def _flush_bdb_writeback(self):
        """Write back a pending serialized fixture, then disarm.

        Called before switching BDBs (`open_bdb` again), on `exit`, and at the end
        of a run. Disarming afterward prevents a stale temp binary from being
        re-dumped over a fixture the session has moved on from.
        """
        if self._write_bdb_sql():
            print(f"open_bdb writeback: wrote {self._bdb_writeback_src}")
        self._bdb_writeback_src = None
        self._bdb_writeback_bin = None

    def _persist_bundles(self, strategy):
        """Persist self.bundles into the open BDB's bundle tables (Stage-1 output).

        Flow-agnostic: net membership is stored by name, so the flat flow (whose
        nets may not have rows in the BDB `net` table) persists too. Clears and
        rewrites so re-running the bundler replaces prior rows. No-op (returns 0)
        when no BDB is open. See docs/internal/bdb_test_data.md.
        """
        if self.bdb is None:
            return 0
        import json
        self.bdb.clear_bundles()
        for w in self.bundles:
            hb = w.input.original_bundle
            row = buda.BundleRow()
            row.id = str(hb.id)
            row.level = hb.level
            row.strategy = strategy
            row.reason = hb.reason
            row.num_terminals = hb.num_terminals
            row.cell_context = hb.cell_context
            row.instances = json.dumps(list(hb.instances))
            row.parent_id = str(hb.parent_id) if hb.parent_id >= 0 else ""
            row.drv_spec_depth = hb.drv_spec_depth
            row.rcv_spec_depth = hb.rcv_spec_depth
            row.drv_spec_path = hb.drv_spec_path
            row.rcv_spec_paths = json.dumps(list(hb.rcv_spec_paths))
            self.bdb.add_bundle(row)
            for nm in hb.get_net_names():
                self.bdb.add_bundle_net(row.id, nm)
            for bt in hb.entry_busterm_ids:
                self.bdb.add_bundle_busterm(row.id, bt, "entry")
            for bt in hb.exit_busterm_ids:
                self.bdb.add_bundle_busterm(row.id, bt, "exit")
        return len(self.bundles)

    def _persist_topologies(self):
        """Persist all candidate topologies in self.bundles to the BDB (Stage 2).

        Written at generate_*topologies time (before run_planner) so a design's
        candidates are inspectable/tweakable up front, without paying the planner's
        runtime on large designs. Clears and rewrites (idempotent on re-generate).
        No-op (returns 0) when no BDB is open. `is_selected` reflects any pre-plan
        pin; marking the planner's choice is a follow-up (see wishlist-bdb.md).
        """
        if self.bdb is None:
            return 0
        import json
        # Ensure the FK-parent bundle rows exist and match the current bundles.
        # (A flat flow may have run run_bundler *before* open_bdb, so the bundles
        # were never persisted; without this the topology.bundle_id FK would reject
        # every insert.) Re-persisting is idempotent and keeps the two in sync.
        self._persist_bundles(self._bundler_strategy)
        self.bdb.clear_topologies()
        n_cands = 0
        for w in self.bundles:
            bid = str(w.input.original_bundle.id)
            selected = w.plan.selected_topology_index
            for ci, topo in enumerate(w.input.candidates):
                tr = buda.TopoRow()
                tr.id = bid
                tr.cand_index = ci
                tr.type = topo.type
                tr.wirelength = topo.estimated_wirelength
                tr.trunk_location = topo.trunk_location
                tr.pass_through_count = topo.pass_through_count
                tr.connected_blocks = json.dumps(list(topo.connected_block_names))
                tr.feedthru_blocks = json.dumps(list(topo.feedthru_blocks))
                tr.is_selected = (ci == selected)
                self.bdb.add_topology(tr)
                for si, seg in enumerate(topo.segments):
                    sr = buda.TopoSegRow()
                    sr.id = bid
                    sr.cand_index = ci
                    sr.seg_index = si
                    sr.x1, sr.y1 = seg.start.x, seg.start.y
                    sr.x2, sr.y2 = seg.end.x, seg.end.y
                    sr.layer_hint = seg.layer_hint
                    sr.is_jog = seg.is_jog
                    self.bdb.add_topology_segment(sr)
                # Persist the authoritative seg-busterm annotation LOGICALLY (the
                # topology row above is the FK parent). A reload rebuilds
                # connectivity from these links, never re-deriving from geometry.
                buda.persist_seg_busterms(self.bdb, bid, ci, topo)
                n_cands += 1
        return n_cands

    def _persist_nuts(self):
        """Persist abstract-NUTS bus segments + symbolic bus-vias (Stage 4).

        Written after run_nuts. Each placed TrackSegment becomes a bus_segment row
        whose geometry is the placed rectangle (real coords). A bus-via is recorded
        wherever two connected (endpoint-sharing) segments of the SAME bundle sit on
        DIFFERENT layers — one symbolic row per bus-level transition (bit_width bit-
        vias). No-op (returns (0, 0)) without an open BDB or NUTS result.

        `bundle_id` is a hard FK to bundle(id): every bus row joins a persisted
        bundle. Before writing, ensure the referenced parents exist — if any are
        missing (e.g. run_nuts reached without a persisted run_planner), persist the
        planner output first so the FK is satisfiable (see docs/internal/wishlist-bdb.md).
        """
        if self.bdb is None or self.nuts_result is None:
            return (0, 0)
        # Hard FK: bus rows reference bundle(id). Make sure every referenced parent
        # is present before inserting, else the FK would reject the row.
        referenced = {str(ts.bundle_id) for ts in self.nuts_result.segments}
        persisted = {b.id for b in self.bdb.all_bundles()}
        if not referenced <= persisted:
            self._persist_planner_output()
        self.bdb.clear_bus_routing()
        for ts in self.nuts_result.segments:
            r = buda.BusSegRow()
            r.id = str(ts.bundle_id)
            r.seg_idx = ts.seg_idx
            r.layer = ts.layer
            r.is_horiz = ts.horiz
            half = ts.width / 2.0
            if ts.horiz:                      # span is x; track_position is y
                r.x1, r.x2 = ts.span_lo, ts.span_hi
                r.y1, r.y2 = ts.track_position - half, ts.track_position + half
            else:                             # span is y; track_position is x
                r.y1, r.y2 = ts.span_lo, ts.span_hi
                r.x1, r.x2 = ts.track_position - half, ts.track_position + half
            r.track_position = ts.track_position
            r.width = ts.width
            r.placed = ts.placed
            r.is_jog = ts.is_jog
            self.bdb.add_bus_segment(r)
        n_via = 0
        for w in self.bundles:
            n_via += self._persist_bundle_vias(w)
        n_seg = len(self.nuts_result.segments)
        self._persist_route_snapshot(n_seg, n_via, "abstract_nuts")
        return (n_seg, n_via)

    def _persist_route_snapshot(self, n_seg, n_via, stage, n_net_seg=0, n_net_via=0):
        """Fingerprint the routed output (all bus_segment + bus_via rows, plus the
        detailed net_segment + net_via rows once persisted) into the singleton
        route_snapshot row. The hash is over a canonical, order-independent
        serialization, so an identical routing always yields the same hash (stable in
        the *.bdb.sql diff) and any geometry/layer/via change flips exactly one line.
        Net rows hash by net_NAME, not net_id, so the digest is independent of the
        net table's autoincrement history.
        """
        if self.bdb is None:
            return
        bids = sorted({b.id for b in self.bdb.all_bundles()})
        rows = []
        for bid in bids:
            for g in self.bdb.bus_segments(bid):
                rows.append(("S", g.id, g.seg_idx, g.layer, int(g.is_horiz),
                             g.x1, g.y1, g.x2, g.y2, g.track_position, g.width,
                             int(g.placed), int(g.is_jog)))
            for v in self.bdb.bus_vias(bid):
                rows.append(("V", v.id, v.from_seg, v.to_seg, v.from_layer,
                             v.to_layer, v.x, v.y, v.bit_width))
            # Empty until _persist_detailed_nuts runs, so the abstract-stage
            # hash is unchanged by the mere existence of the net tables.
            for g in self.bdb.net_segments(bid):
                rows.append(("N", g.id, g.seg_idx, g.bit_index, g.net_name,
                             g.layer, int(g.is_horiz), g.x1, g.y1, g.x2, g.y2,
                             g.track_position, g.width))
            for v in self.bdb.net_vias(bid):
                rows.append(("W", v.id, v.from_seg, v.to_seg, v.bit_index,
                             v.net_name, v.from_layer, v.to_layer, v.x, v.y))
        rows.sort(key=repr)
        digest = hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()
        self.bdb.set_route_snapshot(digest, n_seg, n_via, stage,
                                    n_net_seg, n_net_via)

    def _persist_bundle_vias(self, w):
        """Record one symbolic bus-via per layer-transition in a bundle's placed
        segments. Segment adjacency comes from `ConnTopology` (the repo's canonical
        connection model), so it covers both shared-endpoint bends AND T-junctions
        where a stub lands on a trunk's interior (multi-terminal topologies). The
        via's position is the PLACED junction (from the segments' track positions).
        """
        sel = w.plan.selected_topology_index
        if sel < 0 or sel >= len(w.input.candidates):
            return 0
        topo = w.input.candidates[sel]
        bid = w.input.original_bundle.id
        ts_map = {ts.seg_idx: ts for ts in self.nuts_result.segments
                  if ts.bundle_id == bid}
        bit_width = len(w.input.original_bundle.get_net_names())
        ct = buda.ConnTopology()
        ct.build(topo, self.fp)
        segs = list(ct.segs())
        n = 0
        seen = set()
        for a, cs in enumerate(segs):
            ta = ts_map.get(a)
            if ta is None or not ta.placed:
                continue
            for conn in cs.conns:
                if conn.kind != buda.SegConnKind.SEG:
                    continue
                b = conn.seg_idx
                tb = ts_map.get(b)
                if tb is None or not tb.placed:
                    continue
                if ta.layer == tb.layer:                 # same layer → no via
                    continue
                key = (min(a, b), max(a, b))
                if key in seen:                          # conns are symmetric
                    continue
                seen.add(key)
                if ta.horiz != tb.horiz:                 # bend or T-junction (H↔V)
                    h, v = (ta, tb) if ta.horiz else (tb, ta)
                    jx, jy = v.track_position, h.track_position
                elif ta.horiz:                           # stacked H segments
                    jx, jy = conn.at_pos, ta.track_position
                else:                                    # stacked V segments
                    jx, jy = ta.track_position, conn.at_pos
                r = buda.BusViaRow()
                r.id = str(bid)
                r.from_seg, r.to_seg = key
                r.from_layer, r.to_layer = ts_map[key[0]].layer, ts_map[key[1]].layer
                r.x, r.y = jx, jy
                r.bit_width = bit_width
                self.bdb.add_bus_via(r)
                n += 1
        return n

    def _persist_detailed_nuts(self):
        """Persist detailed-NUTS per-bit wires + vias (Stage 9, schema v8).

        Written after run_detailed_nuts. Each NetSegment becomes a net_segment row
        (placed rectangle + the bit's net identity: `net_names[bit_index]`, resolved
        to a net_id via _ensure_net) and each NetVia a net_via row (the symbolic
        bus_via fanned out per bit — same (bundle_id, from_seg, to_seg) key). The
        route_snapshot is rewritten with stage 'detailed_nuts' and hashes the net
        rows too. No-op (returns (0, 0)) without an open BDB or detailed result.
        """
        if self.bdb is None or self.detailed_result is None:
            return (0, 0)
        referenced = {str(ns.bundle_id)
                      for ns in self.detailed_result.net_segments}
        # The net rows describe the persisted abstract bus routing. If the bus
        # rows are missing (e.g. the BDB was opened only after run_nuts ran),
        # persist the abstract stage first — _persist_nuts also ensures the FK
        # bundle parents and writes the abstract snapshot whose bus counts we
        # preserve below. Otherwise just ensure the parents (same guard as
        # _persist_nuts).
        missing_bus = {bid for bid in referenced if not self.bdb.bus_segments(bid)}
        if missing_bus and self.nuts_result is not None:
            self._persist_nuts()
        else:
            persisted = {b.id for b in self.bdb.all_bundles()}
            if not referenced <= persisted:
                self._persist_planner_output()
        # Preserve the bus counts for the snapshot rewrite BEFORE the clear
        # drops the singleton along with the old net rows.
        snap = self.bdb.route_snapshot()
        self.bdb.clear_detailed_routing()

        bid_to_names = {w.input.original_bundle.id:
                        list(w.input.original_bundle.get_net_names())
                        for w in self.bundles}
        h_layers = set(self.layers.get_layer_ids_by_dir(buda.LayerDir.HORIZONTAL))

        def bit_net(bundle_id, bit):
            names = bid_to_names.get(bundle_id, [])
            return names[bit] if bit < len(names) else ""

        for ns in self.detailed_result.net_segments:
            r = buda.NetSegRow()
            r.id = str(ns.bundle_id)
            r.seg_idx = ns.seg_idx
            r.bit_index = ns.bit_index
            r.net_name = bit_net(ns.bundle_id, ns.bit_index)
            r.layer = ns.layer
            r.is_horiz = ns.layer in h_layers
            half = ns.width / 2.0
            # Spans are stored AS-IS (may be reversed, span_lo > span_hi, after
            # the engine's endpoint snap — same convention as bus_segment;
            # consumers take min/max).
            if r.is_horiz:                    # span is x; track_position is y
                r.x1, r.x2 = ns.span_lo, ns.span_hi
                r.y1, r.y2 = ns.track_position - half, ns.track_position + half
            else:                             # span is y; track_position is x
                r.y1, r.y2 = ns.span_lo, ns.span_hi
                r.x1, r.x2 = ns.track_position - half, ns.track_position + half
            r.track_position = ns.track_position
            r.width = ns.width
            self.bdb.add_net_segment(r)

        for nv in self.detailed_result.net_vias:
            r = buda.NetViaRow()
            r.id = str(nv.bundle_id)
            r.from_seg, r.to_seg = nv.from_seg, nv.to_seg
            r.bit_index = nv.bit_index
            r.net_name = bit_net(nv.bundle_id, nv.bit_index)
            r.from_layer, r.to_layer = nv.from_layer, nv.to_layer
            r.x, r.y = nv.x, nv.y
            self.bdb.add_net_via(r)

        n_ns = len(self.detailed_result.net_segments)
        n_nv = len(self.detailed_result.net_vias)
        self._persist_route_snapshot(snap.n_bus_segments, snap.n_bus_vias,
                                     "detailed_nuts", n_ns, n_nv)
        return (n_ns, n_nv)

    def _persist_planner_output(self):
        """Persist the planner's decision into the BDB after run_planner.

        For every current wrapper: record the selected topology (`is_selected`) and
        the per-segment assigned layers (`topology_segment.assigned_layer`). Wrappers
        whose id already has BDB rows (flat bundles, hier cross-block / pass-through)
        are UPDATED in place; hier's expanded per-instance wrappers (synthetic ids)
        are ADDED as `is_replicated=1` bundle rows (parent_id = template) with just
        their selected topology, so `bus_segment` rows join back to a bundle. No-op
        without an open BDB. See docs/internal/wishlist-bdb.md.
        """
        if self.bdb is None:
            return 0
        self.bdb.clear_expanded_bundles()          # idempotent re-plan
        # An id is an expanded per-instance wrapper ONLY if it came from the hier
        # expansion map — NOT merely because it's absent from the BDB (a flat flow
        # can open_bdb after generate, so its normal bundles aren't persisted yet).
        expanded_to_template = {}
        for tid, wrappers in (self._hier_expansion_map or {}).items():
            for ew in wrappers:
                expanded_to_template[ew.input.original_bundle.id] = tid
        original_ids = {b.id for b in self.bdb.all_bundles()}
        n = 0
        for w in self.bundles:
            sel = w.plan.selected_topology_index
            if sel < 0 or sel >= len(w.input.candidates):
                continue
            hbid = w.input.original_bundle.id
            bid = str(hbid)
            if hbid in expanded_to_template:        # genuine hier expanded instance
                self._add_expanded_bundle(w, sel, expanded_to_template)
            else:                                   # normal bundle (flat / cross-block)
                if bid not in original_ids:         # not persisted yet → persist fully
                    self._persist_normal_bundle(w)
                self.bdb.set_topology_selected(bid, sel)
                self.bdb.reset_assigned_layers(bid)  # drop stale layers from a prior plan
                self._persist_assigned_layers(bid, sel, w)
            n += 1
        return n

    def _persist_normal_bundle(self, w):
        """Persist a single non-expanded bundle (is_replicated=0) and ALL of its
        candidate topologies — for the flat flow that opened a BDB only after
        run_bundler/generate_topologies, so nothing was persisted yet."""
        import json
        hb = w.input.original_bundle
        bid = str(hb.id)
        row = buda.BundleRow()
        row.id = bid
        row.level = hb.level
        row.strategy = self._bundler_strategy
        row.reason = hb.reason
        row.num_terminals = hb.num_terminals
        row.cell_context = hb.cell_context
        row.instances = json.dumps(list(hb.instances))
        row.parent_id = str(hb.parent_id) if hb.parent_id >= 0 else ""
        row.drv_spec_depth = hb.drv_spec_depth
        row.rcv_spec_depth = hb.rcv_spec_depth
        row.drv_spec_path = hb.drv_spec_path
        row.rcv_spec_paths = json.dumps(list(hb.rcv_spec_paths))
        self.bdb.add_bundle(row)                    # is_replicated defaults to False
        for nm in hb.get_net_names():
            self.bdb.add_bundle_net(bid, nm)
        for bt in hb.entry_busterm_ids:
            self.bdb.add_bundle_busterm(bid, bt, "entry")
        for bt in hb.exit_busterm_ids:
            self.bdb.add_bundle_busterm(bid, bt, "exit")
        sel = w.plan.selected_topology_index
        for ci, topo in enumerate(w.input.candidates):
            tr = buda.TopoRow()
            tr.id = bid
            tr.cand_index = ci
            tr.type = topo.type
            tr.wirelength = topo.estimated_wirelength
            tr.trunk_location = topo.trunk_location
            tr.pass_through_count = topo.pass_through_count
            tr.connected_blocks = json.dumps(list(topo.connected_block_names))
            tr.feedthru_blocks = json.dumps(list(topo.feedthru_blocks))
            tr.is_selected = (ci == sel)
            self.bdb.add_topology(tr)
            for si, seg in enumerate(topo.segments):
                sr = buda.TopoSegRow()
                sr.id = bid
                sr.cand_index = ci
                sr.seg_index = si
                sr.x1, sr.y1 = seg.start.x, seg.start.y
                sr.x2, sr.y2 = seg.end.x, seg.end.y
                sr.layer_hint = seg.layer_hint
                sr.is_jog = seg.is_jog
                self.bdb.add_topology_segment(sr)

    def _persist_assigned_layers(self, bid, sel, w):
        """Write the planner's per-segment assigned layers for a selected topology."""
        for seg_index, layer in enumerate(w.plan.seg_layers):
            self.bdb.set_segment_layer(bid, sel, seg_index, int(layer))

    def _add_expanded_bundle(self, w, sel, expanded_to_template):
        """Add one expanded per-instance bundle row + its selected topology."""
        import json
        hb = w.input.original_bundle
        bid = str(hb.id)
        row = buda.BundleRow()
        row.id = bid
        row.level = hb.level
        row.strategy = self._bundler_strategy
        row.reason = hb.reason
        row.num_terminals = hb.num_terminals
        row.cell_context = hb.cell_context
        row.instances = json.dumps(list(hb.instances))
        # parent_id links the instance back to its template bundle.
        tpl = expanded_to_template.get(hb.id)
        row.parent_id = str(tpl) if tpl is not None else ""
        row.is_replicated = True                   # marks an expanded instance
        row.drv_spec_depth = hb.drv_spec_depth
        row.rcv_spec_depth = hb.rcv_spec_depth
        row.drv_spec_path = hb.drv_spec_path
        row.rcv_spec_paths = json.dumps(list(hb.rcv_spec_paths))
        self.bdb.add_bundle(row)
        for nm in hb.get_net_names():
            self.bdb.add_bundle_net(bid, nm)
        # entry/exit busterms may be cell-local (no "/"); qualify with the instance.
        inst = hb.instances[0] if hb.instances else ""
        def _qual(bt):
            return f"{inst}/{bt}" if inst and "/" not in bt else bt
        for bt in hb.entry_busterm_ids:
            self.bdb.add_bundle_busterm(bid, _qual(bt), "entry")
        for bt in hb.exit_busterm_ids:
            self.bdb.add_bundle_busterm(bid, _qual(bt), "exit")
        # Persist only the SELECTED (placed) topology for the instance, with the
        # planner's assigned layers; the template retains the full candidate set.
        topo = w.input.candidates[sel]
        tr = buda.TopoRow()
        tr.id = bid
        tr.cand_index = sel
        tr.type = topo.type
        tr.wirelength = topo.estimated_wirelength
        tr.trunk_location = topo.trunk_location
        tr.pass_through_count = topo.pass_through_count
        tr.connected_blocks = json.dumps(list(topo.connected_block_names))
        tr.feedthru_blocks = json.dumps(list(topo.feedthru_blocks))
        tr.is_selected = True
        self.bdb.add_topology(tr)
        seg_layers = list(w.plan.seg_layers)
        for si, seg in enumerate(topo.segments):
            sr = buda.TopoSegRow()
            sr.id = bid
            sr.cand_index = sel
            sr.seg_index = si
            sr.x1, sr.y1 = seg.start.x, seg.start.y
            sr.x2, sr.y2 = seg.end.x, seg.end.y
            sr.layer_hint = seg.layer_hint
            sr.is_jog = seg.is_jog
            sr.assigned_layer = int(seg_layers[si]) if si < len(seg_layers) else -1
            self.bdb.add_topology_segment(sr)

    def _apply_selections(self):
        """Load the sidecar and apply pinned topologies and layer overrides.

        This acts as a baseline load. If a bundle is already pinned (e.g., via
        a `select_topology` script command), the script's choice is respected,
        but any matching layer overrides from the sidecar will still be applied.
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
                        if w.input.original_bundle.get_net_names() and
                           w.input.original_bundle.get_net_names()[0].startswith(hint)]
            if not matching:
                continue

            first_w = matching[0]
            bid = first_w.input.original_bundle.id

            # 1. Resolve which topology the sidecar points to
            resolved_sidecar_idx = None
            for i, cand in enumerate(first_w.input.candidates):
                if (cand.type == sel['topo_type'] and
                        cand.estimated_wirelength == sel['topo_wl']):
                    resolved_sidecar_idx = i
                    break
            
            if resolved_sidecar_idx is None:
                idx_hint = sel.get('topo_index_hint', -1)
                if 0 <= idx_hint < len(first_w.input.candidates):
                    resolved_sidecar_idx = idx_hint
                    print(f"Warning: sidecar selection for bundle {bid} matched by index hint "
                          f"(type/WL changed?) — using topo {resolved_sidecar_idx + 1}")
                else:
                    print(f"Warning: sidecar selection for bundle {bid} could not be resolved — ignored")
                    continue

            # 2. Apply per wrapper (the candidate set is shared across instances,
            #    so resolved_sidecar_idx is common, but the pin decision is not):
            #    a script-pinned wrapper keeps its own topology; an unpinned one
            #    adopts the sidecar's.  Sidecar layer overrides are applied only
            #    when that wrapper's selected topology matches the sidecar's, so
            #    their segment count lines up.
            sidecar_layers = sel.get('seg_layers')
            n_adopted = 0   # wrappers newly pinned from the sidecar
            n_layered = 0   # wrappers that received layer overrides
            for w in matching:
                w_pinned = getattr(w.input, 'topology_pinned', False)
                target_idx = (w.plan.selected_topology_index if w_pinned
                              else resolved_sidecar_idx)

                if not w_pinned:
                    w.plan.selected_topology_index = target_idx
                    w.input.topology_pinned = True
                    n_adopted += 1

                if (sidecar_layers is not None
                        and target_idx == resolved_sidecar_idx
                        and 0 <= target_idx < len(w.input.candidates)
                        and len(sidecar_layers) == len(w.input.candidates[target_idx].segments)):
                    w.input.pinned_seg_layers = list(sidecar_layers)
                    n_layered += 1
                else:
                    # The sidecar's layer overrides belong to its topology; when the
                    # script pinned a *different* topology they don't apply, so clear
                    # any stale overrides (e.g. left by an earlier baseline apply)
                    # rather than let the planner truncate them onto the new topo.
                    w.input.pinned_seg_layers = []

            n = len(matching)
            suffix = f" [{n} instances]" if n > 1 else ""
            if n_adopted:
                msg = (f"Pinned bundle {bid} to topology {resolved_sidecar_idx + 1} "
                       f"({sel['topo_type']}, WL={sel['topo_wl']})")
                if n_layered:
                    msg += f" with {len(sidecar_layers)} layer overrides"
                print(msg + suffix)
            elif n_layered:
                print(f"Merged sidecar layer overrides ({len(sidecar_layers)}) "
                      f"onto script-pinned bundle {bid}{suffix}")

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
            self._bdb_added_ids.add(r.id)
            added_rows.append((r, is_fallback))
            if is_fallback:
                fallback_count += 1

        # Container marking (Gap 2): a block is a hierarchy envelope only when at
        # least one of its BDB descendants was ALSO loaded into the floorplan —
        # those finer blocks supply the internal cuts that charge intra-block
        # congestion.  A node whose descendants were NOT imported (e.g.
        # `add_blocks_from_bdb 1 skip` on non-leaf depth-1 blocks) stays a solid
        # leaf cell and keeps blocking LOW layers.  Recomputed over every block
        # imported so far so a shallow block flips to container once a later
        # call loads its descendants.
        self._mark_bdb_containers(by_id, children_of)

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

    def _mark_bdb_containers(self, by_id, children_of):
        """Mark imported blocks container/leaf by whether a loaded descendant exists.

        A block is a container (transparent to LOW layers; intra-block congestion
        charged via descendant cuts) only if at least one of its BDB descendants
        was itself loaded into the floorplan.  Otherwise it stays a solid leaf
        cell that blocks LOW layers.  Recomputed over self._bdb_added_ids so the
        decision stays correct as deeper levels are imported in later calls.
        """
        added = self._bdb_added_ids
        memo: dict[int, bool] = {}

        def has_loaded_descendant(nid):
            if nid in memo:
                return memo[nid]
            memo[nid] = False   # guard against cycles
            res = any(ch.id in added or has_loaded_descendant(ch.id)
                      for ch in children_of.get(nid, []))
            memo[nid] = res
            return res

        for nid in added:
            row = by_id.get(nid)
            if row is not None:
                self.fp.set_container(row.name, has_loaded_descendant(nid))

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
        """Parse a bundle reason into (src, [dsts]).

        'DRV:x|REC:a,b,'  → ('x', ['a', 'b'])
        'BIDIR:a,b,c,'    → ('a', ['b', 'c'])   — direction-agnostic: any
            instance can root the trunk, so use the first and branch to the rest;
            the block-to-block topology reaches every instance either way.
        Returns (None, []) on failure."""
        try:
            if reason.startswith('BIDIR:'):
                insts = [n for n in reason[len('BIDIR:'):].split(',') if n]
                return (insts[0], insts[1:]) if insts else (None, [])
            drv_part, rec_part = reason.split('|REC:')
            src = drv_part[4:]              # strip leading "DRV:"
            dsts = [n for n in rec_part.split(',') if n]
            return src, dsts
        except (ValueError, IndexError):
            return None, []

    @staticmethod
    def _fmt_index_ranges(nums):
        """Compress sorted-unique ints into compact ranges: [0,1,2,3,5,6] -> '0:3,5:6'."""
        nums = sorted(set(nums))
        out, i = [], 0
        while i < len(nums):
            j = i
            while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
                j += 1
            out.append(str(nums[i]) if i == j else f"{nums[i]}:{nums[j]}")
            i = j + 1
        return ",".join(out)

    def _bundle_net_summary(self, net_names, max_len=200):
        """Group a bundle's nets into buses for a compact log line.

        A net named '<bus>_<idx>' or '<bus>_b<idx>' (the two forms add_bus/add_net
        emit — e.g. 'bus_007_3', 'bus_077_b00') is folded into its bus with a
        compressed bit range like 'bus_077[0:59]'; nets with no numeric suffix are
        listed verbatim.  First-seen order is preserved; truncated past max_len."""
        groups, order, seen = {}, [], set()
        for nm in net_names:
            m = re.match(r'^(.*?)_([A-Za-z]*)(\d+)$', nm)
            if m:
                key = (m.group(1), m.group(2))      # (bus, bit-tag) e.g. ('bus_077','b')
                if key not in groups:
                    groups[key] = []
                    order.append(('bus', key, m.group(1)))
                groups[key].append(int(m.group(3)))
            elif ('net', nm) not in seen:
                seen.add(('net', nm))
                order.append(('net', nm, nm))
        parts = [f"{disp}[{self._fmt_index_ranges(groups[key])}]" if kind == 'bus'
                 else disp
                 for kind, key, disp in order]
        summary = ", ".join(parts)
        if len(summary) > max_len:
            summary = summary[:max_len - 1].rstrip(", ") + "…"
        return summary

    def _bundle_nets_suffix(self, w):
        """'<n> nets: <bus summary>' appended to a generate_* per-bundle log line,
        so the bundle-id -> buses/nets correspondence rides on the same line."""
        nets = w.input.original_bundle.get_net_names()
        return (f"{len(nets)} net{'' if len(nets) == 1 else 's'}: "
                f"{self._bundle_net_summary(nets)}")

    def _generate_hier_topo_one(self, w, use_center, use_double_detour,
                                fp_cache, comps_by_name):
        """Generate topology candidates for a single HBundle wrapper.

        Updates w.input.candidates in place. Returns candidate count.
        fp_cache is a dict shared across calls; pass {} for a fresh cache.
        comps_by_name is {name: ComponentRow} from bdb.all_components().
        """
        b = w.input.original_bundle
        nets_suffix = self._bundle_nets_suffix(w)   # rides on each per-bundle log line
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
            w.input.candidates = tg.generate_candidates(src_local, dsts_local)
            self._reset_plan_for_regen(w)
            label = f"{src_local}→{dsts_local[0]}"
            n = len(w.input.candidates)
            if n == 0:
                print(f"  WARNING: HierTopo D{b.level}: bundle {b.id} ({label}) "
                      f"0 candidates — bundle will be unrouted!  [cell:{b.cell_context}] {nets_suffix}")
            else:
                print(f"HierTopo D{b.level}: bundle {b.id} ({label}) "
                      f"{n} candidates  [cell:{b.cell_context}] {nets_suffix}")
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
            w.input.candidates = tg.generate_candidates(b.drv_spec_path, list(b.rcv_spec_paths))
            self._reset_plan_for_regen(w)
            label = (f"{b.drv_spec_path}→{b.rcv_spec_paths[0]}"
                     if len(b.rcv_spec_paths) == 1
                     else f"{b.drv_spec_path}→[{','.join(b.rcv_spec_paths)}]")
            n = len(w.input.candidates)
            tag = f"[cross-level D{b.drv_spec_depth}→D{b.rcv_spec_depth}]"
            if n == 0:
                print(f"  WARNING: HierTopo D{b.level}: bundle {b.id} ({label}) "
                      f"0 candidates — bundle will be unrouted!  {tag} {nets_suffix}")
            else:
                print(f"HierTopo D{b.level}: bundle {b.id} ({label}) {n} candidates  {tag} {nets_suffix}")
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
            w.input.candidates = tg.generate_candidates(src, dsts)
            self._reset_plan_for_regen(w)
            label = f"{src}→{dsts[0]}" if len(dsts) == 1 else f"{src}→[{','.join(dsts)}]"
            n = len(w.input.candidates)
            if n == 0:
                print(f"  WARNING: HierTopo D{b.level}: bundle {b.id} ({label}) "
                      f"0 candidates — bundle will be unrouted! {nets_suffix}")
            else:
                print(f"HierTopo D{b.level}: bundle {b.id} ({label}) {n} candidates {nets_suffix}")
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
        cell_bundle_ids = {w.input.original_bundle.id for w in bundles
                           if w.input.original_bundle.cell_context}
        donor_nets = {}    # (template id, instance path) → replica net list
        replica_wrapper_of = {}  # replica id → (template id, instance path)
        for w in bundles:
            b = w.input.original_bundle
            if (b.cell_context and b.parent_id in cell_bundle_ids
                    and b.instances):
                donor_nets[(b.parent_id, b.instances[0])] = list(b.net_names)
                replica_wrapper_of[b.id] = (b.parent_id, b.instances[0])
        # Start synthetic IDs above any real bundle ID in the set.
        max_id = max((w.input.original_bundle.id for w in bundles), default=-1)
        next_id = max_id + 1
        result = []
        expansion_map = {}  # original bundle id → [expanded wrappers]
        wrapper_at = {}     # (template id, instance path) → expanded wrapper
        for w in bundles:
            b = w.input.original_bundle
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
                new_w.input.original_bundle = clone
                new_w.input.width = w.input.width
                # Offset each template candidate to instance coords AND qualify
                # its cell-local block names (segments' seg_busterms annotation,
                # connected_block_names, feedthru_blocks) with the instance path,
                # so ConnTopology's authoritative endpoint annotation resolves
                # against the global floorplan (no geometric-fallback mis-taps).
                new_w.input.candidates = [
                    buda.offset_topology(t, dx, dy, inst_name)
                    for t in w.input.candidates]
                # Reserve the instance footprint: until this local bundle is
                # planned, its demand is parked as virtual usage so earlier
                # (global) bundles leave room over the cell interior.
                new_w.hier.has_reservation = True
                new_w.hier.res_x1 = dx
                new_w.hier.res_y1 = dy
                new_w.hier.res_x2 = int(round(parent.x2))
                new_w.hier.res_y2 = int(round(parent.y2))
                # (block-name qualification now done inside offset_topology above)
                # Propagate topology pinning from template to each instance.
                # Candidate indices are preserved (expansion offsets coordinates
                # but keeps the same ordering as the template candidate list).
                new_w.input.topology_pinned = w.input.topology_pinned
                new_w.plan.selected_topology_index = w.plan.selected_topology_index
                if w.input.pinned_seg_layers:
                    new_w.input.pinned_seg_layers = list(w.input.pinned_seg_layers)
                expansion_map[b.id].append(new_w)
                wrapper_at[(b.id, inst_name)] = new_w
                result.append(new_w)
        # Replicas map to the template wrapper at their instance, so a pin on
        # a replica's bundle ID still reaches the wrapper that routes it.
        for rid, key in replica_wrapper_of.items():
            if key in wrapper_at:
                expansion_map[rid] = [wrapper_at[key]]
        return result, expansion_map

    def _make_topo_gen(self, fp, use_center=False, use_double_detour=False,
                       use_multi_trunk=False):
        """Create a TopologyGenerator on fp with the current layer stack."""
        tg = buda.TopologyGenerator(fp)
        h = self.layers.get_top_layer(buda.LayerDir.HORIZONTAL)
        v = self.layers.get_top_layer(buda.LayerDir.VERTICAL)
        if h != -1 and v != -1:
            tg.set_layer_ids(h, v)
            # Inform the generator of ALL same-direction layers so keepout
            # avoidance only suppresses a trunk position when every routable
            # layer for that direction is blocked.  Without this, a keepout on
            # the preferred (TOP) layer alone would wrongly cull candidates the
            # planner could legally reassign to a free same-direction layer.
            h_all = self.layers.get_layer_ids_by_dir(buda.LayerDir.HORIZONTAL)
            v_all = self.layers.get_layer_ids_by_dir(buda.LayerDir.VERTICAL)
            if h_all:
                tg.set_all_h_layers(h_all)
            if v_all:
                tg.set_all_v_layers(v_all)
        if use_center:
            tg.set_busterm_mode(False)
        if use_double_detour:
            tg.set_double_detour(True)
        if use_multi_trunk:
            tg.set_multi_trunk(True)
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
            bid   = w.input.original_bundle.id
            nets  = w.input.original_bundle.get_net_names()
            hint  = nets[0] if nets else f"B{bid}"
            if not w.input.candidates or w.plan.selected_topology_index < 0 or w.plan.selected_topology_index >= len(w.input.candidates):
                continue  # bundle has no topology (e.g. src==dst or no candidates generated)
            topo  = w.input.candidates[w.plan.selected_topology_index]
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
                               h_thresholds: tuple[float, float] | None,
                               top_only: bool = False):
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
            # `top` mode: reassign within the TOP layers only, so short stubs land on
            # the next-highest TOP layer (not the LOW escape layers) and long hauls on
            # the highest.  E.g. V → {M5(short), M7(long)}, H → {M4(short), M6(long)} —
            # keeping the over-subscribed top layers for long-haul and spreading short
            # stubs onto the next TOP tier instead of the LOW (often track-starved) layers.
            if top_only:
                # Restrict to TOP layers — never fall back to the LOW escape
                # layers, even when this direction has fewer than 2 TOP layers
                # (the `< 2` guard below then no-ops the direction, rather than
                # letting lo_layer become a LOW layer and reintroducing the
                # track-starved LOW placement top-only mode exists to avoid).
                layers_sorted = [l for l in layers_sorted if self.layers.is_top(l)]
            if len(layers_sorted) < 2:
                scope = "TOP " if top_only else ""
                print(f"[Planner] post_nuts {dir_label}: fewer than 2 {scope}{dir_label} layers — nothing to reassign")
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
                bid = w.input.original_bundle.id
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
                if not w.input.candidates or w.plan.selected_topology_index < 0 or w.plan.selected_topology_index >= len(w.input.candidates):
                    continue
                topo = w.input.candidates[w.plan.selected_topology_index]
                if w.plan.seg_layers:
                    sl = list(w.plan.seg_layers)
                    for si, seg in enumerate(topo.segments):
                        seg_is_v = (seg.start.y != seg.end.y)
                        if (is_v and seg_is_v) or (not is_v and not seg_is_v):
                            if si < len(sl):
                                sl[si] = new_layer
                    w.plan.seg_layers = sl
                else:
                    if is_v:
                        w.input.assigned_v_layer = new_layer
                    else:
                        w.input.assigned_h_layer = new_layer

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
        self._adopt_doglegs()

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
            if not bw.input.candidates or bw.plan.selected_topology_index < 0 or bw.plan.selected_topology_index >= len(bw.input.candidates):
                continue
            topo = bw.input.candidates[bw.plan.selected_topology_index]
            bid  = bw.input.original_bundle.id
            for si, seg in enumerate(topo.segments):
                is_h = (seg.start.y == seg.end.y)
                if is_h:
                    span_lo = float(min(seg.start.x, seg.end.x))
                    span_hi = float(max(seg.start.x, seg.end.x))
                    layer   = bw.input.assigned_h_layer if bw.input.assigned_h_layer >= 0 else seg.layer_hint
                else:
                    span_lo = float(min(seg.start.y, seg.end.y))
                    span_hi = float(max(seg.start.y, seg.end.y))
                    layer   = bw.input.assigned_v_layer if bw.input.assigned_v_layer >= 0 else seg.layer_hint
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
        bid_to_wrapper = {w.input.original_bundle.id: w for w in self.bundles}
        for asn in assignments:
            w = bid_to_wrapper.get(asn.bundle_id)
            if w is not None:
                w.plan.selected_topology_index = asn.topo_index
                w.input.assigned_v_layer = asn.v_layer_id
                w.input.assigned_h_layer = asn.h_layer_id
                w.plan.seg_layers = list(asn.seg_layers)
                w.plan.seg_perp = list(asn.seg_perp)

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
        self._adopt_doglegs()
        diag = self._nuts_diagnostics(self.nuts_result, layer_names, before)
        self._write_nuts_log(layer_names, append=True,
                             rerun_layer_name="topo-rerun", extra_lines=diag)

        if self.detailed_result is not None:
            self._run_detailed_nuts(bit_order=self._detailed_bit_order)
            return self.nuts_result, self.detailed_result

        return self.nuts_result

    def _adopt_doglegs(self):
        """Adopt any dogleg-mutated topologies from the NUTS result into the live
        bundles, so a later run_detailed_nuts rebuilds ConnTopology from the
        post-split geometry (otherwise the split bundle's stubs keep their stale
        pre-split connectivity and detailed NUTS routes them with bad spans)."""
        dl = getattr(self.nuts_result, "dogleg_topologies", None)
        if not dl:
            return
        seg_layers = self.nuts_result.dogleg_seg_layers
        for w in self.bundles:
            bid = w.input.original_bundle.id
            if bid not in dl:
                continue
            cands = w.input.candidates        # pybind copy
            # Append the split as a NEW candidate (never overwrite the original in
            # place — pybind hands back live element handles, so we cannot snapshot
            # the pre-split Topology to restore later).  The pristine original stays
            # at its index for run_planner to re-evaluate; _reset_doglegs drops this
            # appended slot.  A re-solve (run_nuts again, no re-plan) overwrites the
            # same slot rather than appending a duplicate.
            slot = self._dogleg_slot.get(bid)
            if slot is not None and 0 <= slot < len(cands):
                cands[slot] = dl[bid]          # re-solve: overwrite the same slot
            else:
                # First adoption (or stale slot after a candidate regen): append.
                self._dogleg_originals[bid] = w.plan.selected_topology_index
                cands.append(dl[bid])
                slot = len(cands) - 1
                self._dogleg_slot[bid] = slot
            w.input.candidates = cands        # reassign the whole vector back
            w.plan.selected_topology_index = slot
            w.plan.seg_layers = list(seg_layers[bid])
            np = getattr(self.nuts_result, "dogleg_seg_net_pull", None)
            if np and bid in np:
                w.plan.seg_net_pull = list(np[bid])
            sp = getattr(self.nuts_result, "dogleg_seg_perp", None)
            if sp and bid in sp:
                w.plan.seg_perp = list(sp[bid])   # carries the cleared trunk band
            slo = getattr(self.nuts_result, "dogleg_seg_slide_lo", None)
            shi = getattr(self.nuts_result, "dogleg_seg_slide_hi", None)
            if slo and bid in slo:
                w.plan.seg_slide_lo = list(slo[bid])
                w.plan.seg_slide_hi = list(shi[bid])

    def _reset_doglegs(self):
        """Discard any adopted dogleg before re-planning: drop the appended split
        candidate, restore the pre-split selection, and clear the pinned per-segment
        overrides (seg_net_pull / seg_slide_*).  A fresh run_planner may change
        neighboring topologies, so the next run_nuts must re-detect cycles from
        scratch rather than inherit a stale split — and its overrides, indexed by the
        obsolete split topology, must not survive onto whatever is selected next."""
        if not self._dogleg_slot:
            return
        for w in self.bundles:
            bid = w.input.original_bundle.id
            slot = self._dogleg_slot.get(bid)
            if slot is None:
                continue
            cands = w.input.candidates
            if 0 <= slot < len(cands):       # appended last; drop it
                del cands[slot]
                w.input.candidates = cands
            orig_sel = self._dogleg_originals.get(bid, 0)
            cands = w.input.candidates
            w.plan.selected_topology_index = orig_sel if 0 <= orig_sel < len(cands) else 0
            # The planner refreshes seg_layers/seg_perp from its assignments for
            # every routed bundle, but a bundle it does not return would keep the
            # dogleg's pins; clear them all explicitly so nothing indexed by the
            # obsolete split topology survives.
            w.plan.seg_net_pull = []
            w.plan.seg_slide_lo = []
            w.plan.seg_slide_hi = []
            w.plan.seg_perp = []
        self._dogleg_originals = {}
        self._dogleg_slot = {}

    def _reset_plan_for_regen(self, w):
        """Reset one wrapper to the pristine 'candidates generated, not yet planned'
        state after its candidate list was regenerated.  A prior plan's
        selected_topology_index and per-segment overrides are indexed into the OLD
        candidate list; after regeneration they are stale, and a dogleg may have left
        selected_topology_index pointing at an appended split the fresh list no longer
        has — optimize_topologies would then dereference an out-of-range candidate
        (ValueError: vector).  Also drop this bundle's dogleg bookkeeping so a later
        _adopt_doglegs cannot overwrite/restore a slot that no longer exists.  The
        user must re-pin/re-plan after regenerating, so dropping the pin is correct."""
        w.plan.selected_topology_index = -1
        w.input.topology_pinned = False
        w.plan.seg_layers     = []
        w.plan.seg_perp       = []
        w.plan.seg_net_pull   = []
        w.plan.seg_slide_lo   = []
        w.plan.seg_slide_hi   = []
        bid = w.input.original_bundle.id
        self._dogleg_slot.pop(bid, None)
        self._dogleg_originals.pop(bid, None)

    # ── topology inspection (dump_topologies) ──────────────────────────────
    @staticmethod
    def _topo_geom_sig(topo):
        """Geometric signature of a candidate: frozenset of its segment
        coordinate tuples. Two candidates with the same signature draw the
        identical set of wires and are redundant (dedup target)."""
        return frozenset(
            (s.start.x, s.start.y, s.end.x, s.end.y) for s in topo.segments)

    def _topo_min_slide(self, topo):
        """Minimum perpendicular slide (perp_hi - perp_lo) across the candidate's
        ConnSegs, via the same ConnTopology API the flexibility tests use. A value
        of 0 means a pinched/zero-freedom candidate. Returns None if connectivity
        can't be built (e.g. a hier candidate in a non-self.fp floorplan)."""
        try:
            ct = buda.ConnTopology()
            ct.build(topo, self.fp)
            slides = [cs.perp_hi - cs.perp_lo for cs in ct.segs()
                      if cs.perp_hi > cs.perp_lo]
            return min(slides) if slides else 0
        except Exception:
            return None

    _SLIDE_SENTINEL = 1e8   # ConnTopology marks an unbounded slide with ~5e8

    def _seg_crosses_rect(self, cs, x1, y1, x2, y2):
        """True iff ConnSeg `cs` geometrically crosses the rect (perp coordinate
        inside the rect's perp extent and the along span overlapping the rect's
        along extent).  Mirrors verify's seg_spans_rect."""
        if cs.horiz:   # perp = y (perp_pos), along = x
            return (y1 <= cs.perp_pos <= y2
                    and cs.along_lo <= x2 and cs.along_hi >= x1)
        else:          # perp = x, along = y
            return (x1 <= cs.perp_pos <= x2
                    and cs.along_lo <= y2 and cs.along_hi >= y1)

    def _seg_spans_block(self, cs, name, ubbox):
        """True iff `cs` crosses block `name`'s SOLID geometry.  Multi-rect / TEG
        blocks store their real rectangles in get_block_rects(); a segment through
        a notch/gap between them does NOT cross the block even though it crosses the
        union bbox.  Single-rect blocks have an empty rect list — fall back to the
        union bbox (which is their solid extent)."""
        rects = self.fp.get_block_rects(name)   # [] for single-rect blocks
        if rects:
            return any(self._seg_crosses_rect(cs, x1, y1, x2, y2)
                       for (x1, y1, x2, y2) in rects)
        return self._seg_crosses_rect(cs, ubbox.x1, ubbox.y1, ubbox.x2, ubbox.y2)

    def _dump_conn_detail(self, w, cand_idx):
        """Print per-segment connectivity for one candidate of bundle `w`:
        (1) what each seg connects to (busterms + other segs), (2) the busterms
        it passes through without tapping, (3) its perpendicular slide range, and
        (4) its net-pull preference.  Built from ConnTopology — the same view the
        planner and NUTS consume."""
        cands = list(w.input.candidates)
        if not (0 <= cand_idx < len(cands)):
            print("     (no candidate to detail)")
            return
        topo = cands[cand_idx]
        try:
            ct = buda.ConnTopology()
            ct.build(topo, self.fp)
            segs = list(ct.segs())
        except Exception as e:
            print(f"     (connectivity unavailable: {e})")
            return

        blocks = self.fp.get_all_blocks()   # [(name, Rect union-bbox)]
        feedthru = set(topo.feedthru_blocks)
        # Effective per-segment layer: when this candidate IS the planned/selected
        # one, the planner may have reassigned layers (or honoured a pinned
        # selection / post_nuts move) — report that, the layer NUTS actually routes
        # on, not the candidate's original generation layer_hint.  seg_layers is
        # indexed by the selected topology's segments, so it only aligns here.
        seg_layers = (list(w.plan.seg_layers)
                      if cand_idx == w.plan.selected_topology_index else [])
        print(f"   conn detail — candidate {cand_idx}: {topo.type}"
              + (f"   feedthru={sorted(feedthru)}" if feedthru else ""))
        for si, cs in enumerate(segs):
            orient = "H" if cs.horiz else "V"
            planned = si < len(seg_layers) and seg_layers[si] >= 0
            layer = seg_layers[si] if planned else cs.layer_id
            lyr_s = f"M{layer}" + ("" if planned else "·hint")
            rng = cs.perp_hi - cs.perp_lo
            if abs(cs.perp_lo) >= self._SLIDE_SENTINEL or abs(cs.perp_hi) >= self._SLIDE_SENTINEL:
                slide = "free"
            else:
                slide = f"[{cs.perp_lo}..{cs.perp_hi}] = {rng}{' PINCHED' if rng == 0 else ''}"
            pull = ("→hi" if cs.net_pull > 0 else "→lo" if cs.net_pull < 0 else "none")
            print(f"     seg{si:<2} {orient} {lyr_s}  "
                  f"along[{cs.along_lo},{cs.along_hi}] perp={cs.perp_pos}  "
                  f"slide={slide}  pull={pull}({cs.net_pull})")

            bts, sgs, tapped = [], [], set()
            for c in cs.conns:
                if c.kind == buda.SegConnKind.BUSTERM:
                    tapped.add(c.block_name)
                    bts.append(f"{c.block_name}@face={c.face_coord}"
                               f"{'(end)' if c.is_endpoint else '(mid)'}")
                else:
                    sgs.append(f"seg{c.seg_idx}@{c.at_pos}"
                               f"{'(end)' if c.is_endpoint else '(mid)'}")
            print(f"        busterms: {', '.join(bts) if bts else '(none)'}")
            print(f"        segs:     {', '.join(sgs) if sgs else '(none)'}")

            # Pass-through: blocks this seg crosses (solid geometry) but does NOT tap.
            passt = []
            for name, ubbox in blocks:
                if name in tapped:
                    continue
                if self._seg_spans_block(cs, name, ubbox):
                    passt.append(name + ("[feedthru]" if name in feedthru else ""))
            print(f"        passthru: {', '.join(passt) if passt else '(none)'}")

    def _dump_topologies(self, hint, problems_only, conn_detail=False):
        if not self.bundles:
            print("Warning: no bundles — run the bundler and generate_topologies first.")
            return
        wraps = self.bundles
        if hint:
            wraps = [w for w in wraps
                     if w.input.original_bundle.get_net_names()
                     and w.input.original_bundle.get_net_names()[0].startswith(hint)]
            if not wraps:
                print(f"No bundles whose first net name starts with '{hint}'.")
                return

        # Aggregates across the (possibly filtered) set.
        n_bundles = len(wraps)
        cand_counts = []
        shape_hist = {}
        n_dup_bundles = n_pinch_bundles = n_single_bundles = n_passthru_bundles = 0
        n_dup_cands = 0
        printed = 0

        for w in wraps:
            b = w.input.original_bundle
            cands = list(w.input.candidates)
            cand_counts.append(len(cands))
            sel = w.plan.selected_topology_index
            pinned = bool(getattr(w.input, "topology_pinned", False))

            # Per-candidate facts.
            rows = []          # (idx, type, wl, nsegs, passthru, min_slide)
            sigs = {}          # geom signature -> [idx,...]
            for i, c in enumerate(cands):
                ms = self._topo_min_slide(c)
                rows.append((i, c.type, c.estimated_wirelength,
                             len(c.segments), c.pass_through_count, ms))
                sigs.setdefault(self._topo_geom_sig(c), []).append(i)
                # Histogram on the shape *family* (strip the @coord suffix that
                # makes every Hanan-line trunk a distinct string) so the report
                # shows how many candidates each family contributes.
                fam = c.type.split("@", 1)[0]
                shape_hist[fam] = shape_hist.get(fam, 0) + 1

            dup_groups = [idxs for idxs in sigs.values() if len(idxs) > 1]
            dup_idx = {i for idxs in dup_groups for i in idxs}
            pinch_idx = {i for (i, _, _, _, _, ms) in rows if ms == 0}
            passthru_idx = {i for (i, _, _, _, pt, _) in rows if pt > 0}

            has_dup = bool(dup_groups)
            has_pinch = bool(pinch_idx)
            is_single = len(cands) <= 1
            has_passthru = bool(passthru_idx)
            if has_dup:      n_dup_bundles += 1
            if has_pinch:    n_pinch_bundles += 1
            if is_single:    n_single_bundles += 1
            if has_passthru: n_passthru_bundles += 1
            n_dup_cands += sum(len(idxs) - 1 for idxs in dup_groups)

            if problems_only and not (has_dup or has_pinch or is_single or has_passthru):
                continue

            printed += 1
            flags = []
            if has_dup:      flags.append(f"DUP({len(dup_idx)})")
            if has_pinch:    flags.append(f"PINCH({len(pinch_idx)})")
            if is_single:    flags.append("SINGLE")
            if has_passthru: flags.append(f"PASSTHRU({len(passthru_idx)})")
            net0 = (b.get_net_names()[0] if b.get_net_names() else "?")
            pin_s = " PINNED" if pinned else ""
            print(f"\n── bundle {b.id}  nets={len(b.net_names)} ({net0}…)  "
                  f"width={w.input.width}  sel={sel}{pin_s}  "
                  f"cands={len(cands)}  {' '.join(flags)}")
            # Size the type column to the widest type so every later column
            # stays aligned regardless of long names like TRUNK_V_OOB@x6282.
            type_w = max([len("type")] + [len(r[1]) for r in rows])
            print(f"   {'idx':>3} {'type':<{type_w}} {'wl':>8} {'segs':>4} "
                  f"{'pass':>4} {'mslide':>7}  notes")
            for (i, typ, wl, nsegs, pt, ms) in rows:
                marks = []
                if i == sel:      marks.append("*SEL")
                if i in dup_idx:  marks.append("dup")
                if i in pinch_idx: marks.append("pinch")
                ms_s = "-" if ms is None else str(ms)
                print(f"   {i:>3} {typ:<{type_w}} {wl:>8} {nsegs:>4} {pt:>4} "
                      f"{ms_s:>7}  {','.join(marks)}")

            # --conn: per-segment connectivity / pass-through / slide / pull for
            # the selected candidate (or candidate 0 if not yet planned).
            if conn_detail:
                self._dump_conn_detail(w, sel if sel is not None and sel >= 0 else 0)

        # Aggregate summary.
        import statistics as _st
        tot_cands = sum(cand_counts)
        avg = (tot_cands / n_bundles) if n_bundles else 0
        med = _st.median(cand_counts) if cand_counts else 0
        print(f"\n══ summary ({n_bundles} bundles"
              f"{f', {printed} shown' if problems_only else ''}) ══")
        print(f"   candidates: total={tot_cands} avg={avg:.1f} median={med} "
              f"min={min(cand_counts) if cand_counts else 0} "
              f"max={max(cand_counts) if cand_counts else 0}")
        print(f"   bundles with duplicates : {n_dup_bundles}/{n_bundles} "
              f"({n_dup_cands} redundant candidates)")
        print(f"   bundles with pinched cand: {n_pinch_bundles}/{n_bundles}")
        print(f"   single-candidate bundles : {n_single_bundles}/{n_bundles}")
        print(f"   bundles with pass-through: {n_passthru_bundles}/{n_bundles}")
        top_shapes = sorted(shape_hist.items(), key=lambda kv: -kv[1])
        print("   shape histogram: "
              + ", ".join(f"{t}={n}" for t, n in top_shapes))

    @staticmethod
    def _pin_instance(pin):
        """Instance (block) name for a pin, matching the bundler's rule
        (bundler.cpp: substr up to the LAST '.').  A block name may itself contain
        dots (e.g. 'u.core' with pin 'u.core.tx'), so split on the last dot, not the
        first — otherwise the endpoint resolves to 'u' and misroutes / fails to
        validate against the real block 'u.core'."""
        return pin.rsplit('.', 1)[0]

    def _validate_endpoint_blocks(self, net_name, src, dsts):
        """Fatal input validation: every block a net/bus connects to must exist in
        the floorplan.  get_block_bounds() silently returns {0,0,0,0} for an unknown
        name, so a typo'd endpoint (e.g. 'IOPAD' for the block 'IO_PAD') would route
        from the chip origin instead of the intended block, with no error.  Surface
        it as a fatal error and quit rather than misroute."""
        missing = [b for b in [src, *dsts] if not self.fp.has_block(b)]
        if not missing:
            return
        import difflib
        known = sorted(n for n, _ in self.fp.get_all_blocks())
        for b in missing:
            hint = difflib.get_close_matches(b, known, n=1)
            suffix = f" — did you mean '{hint[0]}'?" if hint else ""
            print(f"Error: net '{net_name}' references block '{b}', which is not "
                  f"defined in the floorplan{suffix}")
        print(f"  Defined blocks: {', '.join(known) if known else '(none)'}")
        sys.exit(1)

    def _install_leaf_keepouts(self):
        """Install implicit solid-leaf-cell keepouts on every non-TOP layer grid
        so signal tracks over cells are excluded — matching the planner and
        abstract NUTS (Gap 2).  Independent of the order in which blocks,
        containers, and track patterns were declared.  Guarded per grid object so
        repeated calls (detailed re-runs, or a `signal_tracks` plan before DNUTS)
        don't re-add duplicates.  No-op without a routing grid."""
        if self.routing_grid is None:
            return
        if getattr(self, '_leaf_keepouts_grid', None) is self.routing_grid:
            return
        for d in (buda.LayerDir.HORIZONTAL, buda.LayerDir.VERTICAL):
            for lid in self.layers.get_layer_ids_by_dir(d):
                if self.layers.is_top(lid) or not self.routing_grid.has_layer(lid):
                    continue
                for koz in self.fp.low_layer_keepouts([lid]):
                    if lid in koz.layer_ids:
                        self.routing_grid.add_keepout(lid, koz.bbox.x1, koz.bbox.y1,
                                                      koz.bbox.x2, koz.bbox.y2)
        self._leaf_keepouts_grid = self.routing_grid

    @staticmethod
    def _planner_iters(args, default=5):
        """First numeric token in a run_planner arg list, skipping the `hier` and
        `signal_tracks` keywords; `default` if none."""
        for a in args:
            if a in ("hier", "signal_tracks"):
                continue
            try:
                return int(a)
            except ValueError:
                continue
        return default

    def _configure_capacity_mode(self, args):
        """Enable the signal-track band-capacity model on self.planner when the
        `signal_tracks` keyword is present (Gap A part 2).  Requires a routing grid
        with `def_track_pattern` layers; installs the leaf-cell keepouts first so
        the planner counts exactly the tracks DetailedNUTS will place.  Must be
        called after the planner is constructed and before build_congestion_map.
        No-op (width model) without the keyword.

        Requesting `signal_tracks` with no `def_track_pattern` defined is a hard
        error (exit 1), not a silent fall-back: the user asked for a specific
        capacity model that cannot be honoured, and quietly planning with the
        width model instead would hide that the signal-track accounting never
        happened."""
        if "signal_tracks" not in args:
            return
        has_pattern = self.routing_grid is not None and any(
            self.routing_grid.has_layer(lid)
            for d in (buda.LayerDir.HORIZONTAL, buda.LayerDir.VERTICAL)
            for lid in self.layers.get_layer_ids_by_dir(d))
        if not has_pattern:
            print("Error: run_planner signal_tracks needs a routing grid to count "
                  "signal tracks, but no def_track_pattern is defined. Add "
                  "def_track_pattern for the routed layers, or drop the "
                  "signal_tracks option to plan with the width model.")
            sys.exit(1)
        self._install_leaf_keepouts()
        self.planner.set_routing_grid(self.routing_grid)
        self.planner.set_capacity_mode(buda.CapacityMode.SIGNAL_TRACKS)

    def _run_detailed_nuts(self, bit_order="LO_HI"):
        """Execute bit-level track assignment using DetailedNUTSEngine."""
        if self.nuts_result is None or self.routing_grid is None:
            return None

        # Match the planner / abstract NUTS by excluding signal tracks over solid
        # leaf cells on LOW layers before the solve.
        self._install_leaf_keepouts()

        bid_to_nbits = {w.input.original_bundle.id: len(w.input.original_bundle.get_net_names())
                        for w in self.bundles}
        # Build ConnTopology per bundle for endpoint adj info.
        bid_to_cs = {}
        for w in self.bundles:
            if not w.input.candidates or w.plan.selected_topology_index < 0 or w.plan.selected_topology_index >= len(w.input.candidates):
                bid_to_cs[w.input.original_bundle.id] = []
                continue
            ct = buda.ConnTopology()
            ct.build(w.input.candidates[w.plan.selected_topology_index], self.fp)
            bid_to_cs[w.input.original_bundle.id] = list(ct.segs())

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
            # Cross-layer corner split bounds (carried into detailed NUTS so the
            # trunk's bits snap to its committed side on real signal tracks).
            bs.track_lo_bound = ts.track_lo_bound
            bs.track_hi_bound = ts.track_hi_bound

            # Populate connections from ConnTopology.
            cs_list = bid_to_cs.get(ts.bundle_id, [])
            if ts.seg_idx < len(cs_list):
                cs = cs_list[ts.seg_idx]
                faces = []
                for conn in cs.conns:
                    if conn.kind == buda.SegConnKind.SEG:
                        c = buda.BusSegmentConn()
                        c.seg_idx     = conn.seg_idx
                        c.at_pos      = float(conn.at_pos)
                        c.is_endpoint = conn.is_endpoint
                        mid = 0.5 * (cs.along_lo + cs.along_hi)
                        c.lo_end      = (c.at_pos <= mid)
                        bs.connections.append(c)
                    else:  # BUSTERM: keep the block-face tap reachable per-bit
                        faces.append(float(conn.face_coord))
                bs.busterm_faces = faces

            bus_segs.append(bs)

        engine = buda.DetailedNUTSEngine(self.routing_grid)
        with buda.ostream_redirect():
            self.detailed_result = engine.run(bus_segs)

        n_net = len(self.detailed_result.net_segments)
        n_unplaced = self.detailed_result.num_unplaced
        print(f"[DetailedNUTS] {n_net} net segments placed, "
              f"{n_unplaced} bits unplaced.")
        return self.detailed_result

    # ---- ripup_reroute: feedback-driven rip-up & re-route -------------------
    # After run_nuts (stage a) or run_detailed_nuts (stage b) the planner may have
    # left a congestion-induced NUTS overlap / DNUTS open that its own band model
    # did not predict (it reports overflow=0).  This greedy hill-climb reads the
    # ACTUAL overlaps/opens, re-routes a contending bundle to an alternate topology,
    # re-runs the pipeline, and keeps moves that reduce the metric — the loop the
    # planner cannot do because it is blind to the real NUTS/DNUTS result.

    def _rr_stage_metric(self):
        """(stage, metric_fn) for the active pipeline state, or (None, None)."""
        if self.detailed_result is not None:
            return 'b', (lambda: self.detailed_result.num_unplaced)
        if self.nuts_result is not None:
            return 'a', (lambda: self.nuts_result.num_overlaps)
        return None, None

    def _rr_open_bundles(self):
        """Bundle ids whose placed-bit count falls short of expected (stage b).

        Counts placed bits per (bundle, seg_idx), then compares against EVERY
        segment the bundle's selected topology should have placed — walking
        `range(n_selected_segments)`, not just the observed seg indices.  A
        segment DetailedNUTS skipped entirely (0 placed bits — e.g. no valid
        signal-track window) emits no `net_segments` rows, so iterating only
        observed segments would miss a bundle whose failed segment has zero bits
        while its other segments are fully placed."""
        if self.detailed_result is None:
            return []
        per_seg = {}   # bid -> {seg_idx: placed bit count}
        for ns in self.detailed_result.net_segments:
            per_seg.setdefault(ns.bundle_id, {})
            per_seg[ns.bundle_id][ns.seg_idx] = \
                per_seg[ns.bundle_id].get(ns.seg_idx, 0) + 1
        out = []
        for w in self.bundles:
            bid = w.input.original_bundle.id
            exp = len(w.input.original_bundle.get_net_names())
            if not exp:
                continue
            sel = w.plan.selected_topology_index
            cands = w.input.candidates
            if sel < 0 or sel >= len(cands):
                continue
            n_segs = len(cands[sel].segments)
            segs = per_seg.get(bid, {})
            if any(segs.get(si, 0) < exp for si in range(n_segs)):
                out.append(bid)
        return out

    def _rr_overlap_bundles(self):
        """Bundle ids appearing in the current NUTS overlaps (both sides)."""
        out = []
        if self.nuts_result is not None:
            for od in self.nuts_result.overlap_details:
                out.append(od.bid_a)
                out.append(od.bid_b)
        return out

    def _rr_contenders(self, stage):
        """Ordered, de-duped contender bundle ids, human-pinned ones excluded.

        Stage b lists the OPEN bundles first, then their NUTS-overlap partners (a
        DNUTS open is caused by a NUTS overlap, so re-routing either side of the
        overlap can clear it — and the partner's fix is often a lower-index
        candidate than the victim's).

        ripup_reroute is an explicit congestion-fix pass, so it may re-route ANY
        contended bundle — including one pinned earlier (its pin is replaced)."""
        order, seen = [], set()
        def add(bid):
            if bid not in seen:
                seen.add(bid)
                order.append(bid)
        if stage == 'b':
            for bid in self._rr_open_bundles():
                add(bid)
        for bid in self._rr_overlap_bundles():
            add(bid)
        if not order:                       # fallback: any re-routable bundle
            for w in self.bundles:
                if len(w.input.candidates) > 1:
                    add(w.input.original_bundle.id)
        return order

    def _rr_wrapper(self, bid):
        for w in self.bundles:
            if w.input.original_bundle.id == bid:
                return w
        return None

    def _rr_snapshot(self):
        """Capture the state a trial mutates so it can be fully restored."""
        return {
            'wrap': {w.input.original_bundle.id:
                     (w.plan.selected_topology_index, w.input.topology_pinned,
                      len(w.input.candidates))
                     for w in self.bundles},
            'nuts': self.nuts_result,
            'dnuts': self.detailed_result,
            'dl_slot': dict(self._dogleg_slot),
            'dl_orig': dict(self._dogleg_originals),
        }

    def _rr_restore(self, snap):
        for w in self.bundles:
            bid = w.input.original_bundle.id
            sel, pinned, ncand = snap['wrap'].get(
                bid, (w.plan.selected_topology_index, w.input.topology_pinned,
                      len(w.input.candidates)))
            cands = w.input.candidates
            while len(cands) > ncand:        # drop dogleg-appended candidates
                del cands[len(cands) - 1]
            w.input.candidates = cands
            w.plan.selected_topology_index = sel
            w.input.topology_pinned = pinned
        self.nuts_result = snap['nuts']
        self.detailed_result = snap['dnuts']
        self._dogleg_slot = dict(snap['dl_slot'])
        self._dogleg_originals = dict(snap['dl_orig'])

    def _rr_replan_hier(self, iterations):
        """Re-plan the already-expanded hier wrappers in place (no re-expansion).

        `run_planner hier` replaced self.bundles with per-instance absolute-coord
        wrappers (unique IDs); ripup re-optimizes THOSE directly so a trial re-pins
        a single instance without rebuilding the template->instance expansion.  The
        wrappers keep their `.hier.priority` and reservation fields (which the
        planner only reads — parked +1 up front, released -1 at each bundle's turn,
        never consumed), and the congestion map is rebuilt here, so re-optimizing is
        deterministic.  Mirrors the `run_planner hier` branch minus _apply_selections
        (pins are already baked onto the wrappers) and minus _expand_hier_bundles."""
        self._reset_doglegs()
        self.planner = buda.CongestionPlanner(self.fp, self.layers)
        for pname, pval in self._planner_params.items():
            self.planner.set_planner_param(pname, pval)
        self.planner.set_track_pitch(self._nuts_pitch)
        self._planner_pitch = self._nuts_pitch
        self.planner.build_congestion_map()
        assignments = self.planner.optimize_topologies(self.bundles, iterations)
        bid_to_wrapper = {w.input.original_bundle.id: w for w in self.bundles}
        for asn in assignments:
            w = bid_to_wrapper.get(asn.bundle_id)
            if w is not None:
                w.plan.selected_topology_index = asn.topo_index
                w.input.assigned_v_layer = asn.v_layer_id
                w.input.assigned_h_layer = asn.h_layer_id
                w.plan.seg_layers = list(asn.seg_layers)
                w.plan.seg_perp = list(asn.seg_perp)

    def _rr_rerun(self, stage):
        """Silently re-run planner + NUTS (+ DNUTS for stage b).

        In hier mode (`run_planner hier` has run, so self.bundles is the expanded
        per-instance list) the trial re-plans the expanded wrappers in place via
        _rr_replan_hier — driving the flat `run_planner` would re-expand and corrupt
        the wrapper set.  Stage b replays DetailedNUTS through the private helper
        with the user's selected bit order preserved.  The `run_detailed_nuts`
        *command* resets `_detailed_bit_order` to LO_HI before parsing its (here
        absent) arg, so driving it via `do_command` would silently flip a HI_LO flow
        to LO_HI and change detailed wiring semantics unrelated to the topology move."""
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), buda.ostream_redirect():
            if self._planner_is_hier:
                self._rr_replan_hier(self._planner_iterations)
            else:
                self.do_command(f"run_planner {self._planner_iterations}")
            self.do_command("run_nuts")
            if stage == 'b':
                self._run_detailed_nuts(bit_order=self._detailed_bit_order)

    def _rr_trial(self, w, tidx, stage, metric):
        """Pin w to candidate tidx, re-run the pipeline, return metric (no restore)."""
        w.plan.selected_topology_index = tidx
        w.input.topology_pinned = True
        self._rr_rerun(stage)
        return metric()

    def _ripup_reroute(self, max_iter=_RR_DEFAULT_MAX_ITER):
        if not self.bundles:
            print("Error: ripup_reroute has no bundles.")
            return
        if self.planner is None:
            print("Error: ripup_reroute needs run_planner to have run first.")
            return
        stage, metric = self._rr_stage_metric()
        if stage is None:
            print("Error: ripup_reroute needs run_nuts (stage a) or "
                  "run_detailed_nuts (stage b) to have run first.")
            return

        m0 = metric()
        if m0 == 0:
            print(f"[ripup_reroute] stage {stage}: metric already 0 — nothing to do.")
            return
        what = "DNUTS opens" if stage == 'b' else "NUTS overlaps"
        print(f"[ripup_reroute] stage {stage} ({what}): start metric={m0}, "
              f"max_iter={max_iter}, {len(self._rr_contenders(stage))} contenders",
              flush=True)

        committed = 0
        it = 0
        n_trials = 0
        # A trial re-runs the planner, mutating every wrapper's layer/perp
        # assignment fields (seg_layers, seg_perp, assigned_*_layer) — which
        # `_rr_snapshot`/`_rr_restore` do NOT capture (they restore only
        # selection, pin, and result refs).  After a commit the live plan is
        # consistent; after rejected trials it carries the last trial's
        # assignment while the reported result is the snapshot's.  Track that
        # divergence and rebuild a consistent plan from the committed selections
        # before returning, so a later run_nuts/DNUTS/visualization matches the
        # reported metric.
        dirty = False
        stopped_early = False            # True if we converged / ran out of moves
        while it < max_iter:
            it += 1
            cur = metric()
            if cur == 0:
                stopped_early = True
                break
            contenders = self._rr_contenders(stage)
            if not contenders:
                print(f"[ripup_reroute] iter {it}: no contenders — stop.")
                stopped_early = True
                break
            snap = self._rr_snapshot()
            n_cont = len(contenders)
            best = None                      # (metric, bid, old_tidx, new_tidx)
            # First-improving-contender.  Each trial is a full pipeline re-run
            # (~1-2s on a large hier design), so the old "best move over ALL
            # contenders" sweep cost contenders*candidates re-runs per iteration —
            # minutes of silent work (e.g. 25 contenders * ~8 candidates ≈ 150
            # re-runs ≈ 3+ min with no output, which reads as a hang).  Instead,
            # scan contenders in priority order and commit the FIRST whose best
            # alternate candidate strictly lowers the metric; a per-contender
            # heartbeat (flushed) makes progress visible.
            for ci, bid in enumerate(contenders, 1):
                w = self._rr_wrapper(bid)
                if w is None:
                    continue
                old_tidx = snap['wrap'][bid][0]
                cand_best = None
                for tidx in range(min(len(w.input.candidates),
                                      _RR_MAX_CANDIDATES_PER_BUNDLE)):
                    if tidx == old_tidx:
                        continue
                    m = self._rr_trial(w, tidx, stage, metric)
                    self._rr_restore(snap)
                    dirty = True             # trial mutated live plan assignments
                    n_trials += 1
                    if m < cur and (cand_best is None or m < cand_best[0]):
                        cand_best = (m, bid, old_tidx, tidx)
                    if m == 0:               # cannot do better — take it now
                        cand_best = (m, bid, old_tidx, tidx)
                        break
                if cand_best is not None:
                    best = cand_best
                    print(f"[ripup_reroute] iter {it}: contender {ci}/{n_cont} "
                          f"bundle {bid} improves {cur}->{cand_best[0]} "
                          f"(topo {old_tidx + 1}->{cand_best[3] + 1})", flush=True)
                    break
                print(f"[ripup_reroute] iter {it}: contender {ci}/{n_cont} "
                      f"bundle {bid} — no improvement", flush=True)
            if best is None:
                print(f"[ripup_reroute] iter {it}: no improving re-route "
                      f"(metric={cur}) — stop.")
                stopped_early = True
                break
            m_new, bid, old_t, new_t = best
            self._rr_trial(self._rr_wrapper(bid), new_t, stage, metric)  # commit
            dirty = False                    # commit left live plan consistent
            committed += 1
            print(f"[ripup_reroute] iter {it}: COMMIT bundle {bid} topo {old_t + 1}"
                  f"->{new_t + 1}, metric {cur}->{metric()}", flush=True)

        # If we exited with rejected trials still live, the wrappers carry the
        # last trial's layer assignments while the result refs were restored to
        # the committed plan.  Rebuild a plan consistent with the committed
        # selections (deterministic; reproduces the reported metric) so any
        # later run_nuts/DNUTS/visualization operates on the same plan.
        if dirty:
            self._rr_rerun(stage)

        if not stopped_early and metric() > 0:
            print(f"[ripup_reroute] reached max_iter={max_iter} while still "
                  f"improving — re-run ripup_reroute or raise max_iter "
                  f"(e.g. `ripup_reroute {max_iter * 5}`) to continue.", flush=True)
        print(f"[ripup_reroute] done: metric {m0}->{metric()} "
              f"after {committed} move(s), {n_trials} trial(s).", flush=True)

    def _select_single_topology_internal(self, bid, tid):
        """Helper for select_topology/select_topologies: set a pin without re-planning layers.
        Returns True if the bundle (or its hierarchical expansion) was found.
        """
        tidx = tid - 1  # Convert 1-based id to 0-based index
        found = False
        for w in self.bundles:
            if w.input.original_bundle.id == bid:
                if tidx < 0 or tidx >= len(w.input.candidates):
                    print(f"Error: invalid topology id {tid} for bundle {bid}")
                else:
                    w.plan.selected_topology_index = tidx
                    w.input.topology_pinned = True
                    print(f"Pinned bundle {bid} to topology {tid}")
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
                        w.plan.selected_topology_index = tidx
                        w.input.topology_pinned = True
                    n = len(wrappers)
                    print(f"Pinned bundle {bid} to topology {tid} "
                          f"({n} expanded instance{'s' if n > 1 else ''})")
                found = True
        if not found:
            print(f"Error: bundle {bid} not found")
        return found

    # ── Per-command logging / runtime stats ─────────────────────────────────
    def run_command(self, cmd_line):
        """Run one script command, routing its detailed output to the flow log
        and printing only a one-line summary (plus runtime) to the terminal.

        `do_command` stays the raw dispatcher (used directly by tests/tools);
        this wrapper is what the CLI flow drives so the terminal is not flooded
        with the same lines the log already captures.
        """
        stripped = cmd_line.strip()
        if not stripped or stripped.startswith('#'):
            return
        cmd = stripped.split()[0].lower()

        # No flow log (interactive/embedded use), or a passthrough command:
        # run it directly with no redirect.  For `source` this means its child
        # commands recurse back through run_command and are each summarized.
        if self._flow_log is None or cmd in _PASSTHROUGH_CMDS:
            return self.do_command(cmd_line)

        real_out, real_err = sys.stdout, sys.stderr
        cap = _Capture(real_out)
        sys.stdout = sys.stderr = cap
        t0 = time.perf_counter()
        raised = None
        try:
            # ostream_redirect routes C++ std::cout/std::cerr to sys.stdout
            # (now `cap`), so even C++ output printed outside the inner
            # per-call redirects is captured to the log instead of leaking to
            # the terminal.
            with buda.ostream_redirect():
                self.do_command(cmd_line)
        except BaseException as e:   # incl. SystemExit from `exit`/fail-fast commands
            raised = e
        finally:
            elapsed = time.perf_counter() - t0
            sys.stdout, sys.stderr = real_out, real_err

        text     = cap.getvalue()
        lines    = text.splitlines()
        nonblank = [ln for ln in lines if ln.strip()]
        nlines   = len(nonblank)
        nwarn    = sum(1 for ln in lines if 'warning' in ln.lower())
        nerr     = sum(1 for ln in lines if 'error' in ln.lower())

        # Silent, instant setup commands (add_block, def_layer, set_*, …) are
        # not worth a terminal line or a log section — only surface commands
        # that produced output, took real time, raised, or reported a problem.
        significant = bool(nonblank) or nwarn or nerr or elapsed >= 0.02 \
            or raised is not None
        if significant:
            # Persist the full detail + a runtime line to the flow log …
            self._flow_log.write(f"\n━━━ {stripped} ━━━\n")
            self._flow_log.write(text if text.endswith('\n') or not text else text + '\n')
            self._flow_log.write(
                f"[runtime] {stripped}: {elapsed:.3f}s "
                f"({nlines} lines, {nwarn} warn, {nerr} err)\n")
            self._flow_log.flush()
            # … and a one-line abstract summary to the terminal.
            self._cmd_stats.append((stripped, elapsed, nlines, nwarn, nerr))
            headline = self._extract_headline(nonblank)
            self._emit_cmd_summary(real_out, stripped, elapsed, nlines,
                                   nwarn, nerr, headline)

        if raised is not None:
            raise raised

    @staticmethod
    def _extract_headline(nonblank):
        """Pick the most summary-like line from a command's (non-blank) output."""
        for ln in reversed(nonblank):
            if any(m.search(ln) for m in _SUMMARY_MARKERS):
                return ln.strip()
        if len(nonblank) > 3:
            return f"({len(nonblank)} lines)"
        return nonblank[-1].strip() if nonblank else ""

    @staticmethod
    def _emit_cmd_summary(out, cmd_line, elapsed, nlines, nwarn, nerr, headline):
        marker = 'x ' if nerr else ('! ' if nwarn else '  ')
        flags  = ''
        if nerr:  flags += f"[{nerr} err] "
        if nwarn: flags += f"[{nwarn} warn] "
        detail = (flags + headline).strip()
        if len(detail) > 68:
            detail = detail[:67] + '…'
        label = cmd_line if len(cmd_line) <= 34 else cmd_line[:33] + '…'
        out.write(f"{marker}{label:<34} {elapsed:6.2f}s  {detail}\n")
        out.flush()

    def print_runtime_summary(self, out):
        """Print a per-command runtime table (also to the flow log)."""
        if not self._cmd_stats:
            return
        total = sum(e for _, e, _, _, _ in self._cmd_stats)
        tw = sum(w for _, _, _, w, _ in self._cmd_stats)
        te = sum(x for _, _, _, _, x in self._cmd_stats)
        slowest = max(self._cmd_stats, key=lambda r: r[1])
        name = os.path.basename(self.script_path) if self.script_path else 'flow'
        lines = [f"\n═══════ Runtime summary ({name}) ═══════"]
        for cmd_line, elapsed, _nl, w, e in self._cmd_stats:
            tag = ' x' if e else (' !' if w else '')
            lines.append(f"  {cmd_line[:40]:<40} {elapsed:7.2f}s{tag}")
        lines.append(f"  {'':-<40} {'-'*8}")
        lines.append(f"  {'total (' + str(len(self._cmd_stats)) + ' commands)':<40} "
                     f"{total:7.2f}s")
        lines.append(f"  slowest: {slowest[0][:40]} ({slowest[1]:.2f}s)")
        if tw or te:
            lines.append(f"  {te} error line(s), {tw} warning line(s) — see the flow log for detail")
        text = '\n'.join(lines) + '\n'
        out.write(text); out.flush()
        if self._flow_log is not None:
            self._flow_log.write(text); self._flow_log.flush()

    def _log_write(self, text):
        """Mirror a diagnostic to the flow log, independent of the per-command
        capture.  Used by passthrough commands (e.g. a `source` that fails fast)
        whose own output bypasses run_command's capture but must still land in
        the post-mortem log."""
        if self._flow_log is not None:
            self._flow_log.write(text if text.endswith('\n') else text + '\n')
            self._flow_log.flush()

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
            # Optional 'container' flag: marks a hierarchy envelope (transparent
            # to LOW layers) rather than a solid leaf cell.  See Gap 2.
            if any(t.lower() == "container" for t in rest):
                self.fp.set_container(name)
                rest = [t for t in rest if t.lower() != "container"]
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
        elif cmd == "set_feedthru":
            # set_feedthru <blocks|*> <layers|*> [on|off]   (value defaults to on)
            #   blocks : comma-separated block names, or * / all
            #   layers : comma-separated layer names or ids, or * / all
            # Resolution is most-specific-first: (block,layer) > (block,*) > (*,layer) > global.
            if len(args) < 2:
                print("Error: usage: set_feedthru <blocks|*> <layers|*> [on|off]")
            else:
                blocks_tok, layers_tok = args[0], args[1]
                val, ok = True, True
                if len(args) >= 3:
                    v = args[2].lower()
                    if v in ("on", "true", "1", "yes"):
                        val = True
                    elif v in ("off", "false", "0", "no"):
                        val = False
                    else:
                        print(f"Error: unknown on/off value '{args[2]}' — use on or off")
                        ok = False
                if ok:
                    blocks_wild = blocks_tok.lower() in ("*", "all")
                    layers_wild = layers_tok.lower() in ("*", "all")
                    block_names = []
                    if not blocks_wild:
                        known = {n for n, _ in self.fp.get_all_blocks()}
                        for b in blocks_tok.split(","):
                            b = b.strip()
                            if not b:
                                continue
                            if b in known:
                                block_names.append(b)
                            else:
                                print(f"Warning: unknown block '{b}' in set_feedthru")
                    layer_ids = []
                    if not layers_wild:
                        for t in layers_tok.split(","):
                            t = t.strip()
                            if not t:
                                continue
                            if t.isdigit():
                                layer_ids.append(int(t))
                            elif t in self._layer_name_map:
                                layer_ids.append(self._layer_name_map[t])
                            else:
                                print(f"Warning: unknown layer '{t}' in set_feedthru")
                    if blocks_wild and layers_wild:
                        self.fp.set_feedthru(val)
                    elif blocks_wild:
                        for lid in layer_ids:
                            self.fp.set_feedthru_layer(lid, val)
                    elif layers_wild:
                        for n in block_names:
                            self.fp.set_feedthru_block(n, val)
                    else:
                        for n in block_names:
                            for lid in layer_ids:
                                self.fp.set_feedthru_block_layer(n, lid, val)
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
            # Syntax A (directed):       add_net <name> <drv_pin> <rcv_pins_csv>
            # Syntax B (undirected):     add_net <name> <pin1> <pin2_csv> unknown
            # Syntax C (bidirectional):  add_net <name> <pin1> <pin2_csv> inout
            last_kw = args[3].lower() if len(args) >= 4 else ""
            unknown_dir = (last_kw == "unknown")
            inout_dir   = (last_kw == "inout")
            name, drv_pin, rcv_str = args[0], args[1], args[2]
            rcv_pins = rcv_str.split(',')
            drv_inst = self._pin_instance(drv_pin)
            rcv_insts = [self._pin_instance(r) for r in rcv_pins]
            if not (unknown_dir or inout_dir) and drv_inst in rcv_insts:
                print(f"Error: block '{drv_inst}' is used as both driver and receiver in net '{name}'")
                sys.exit(1)
            self.netlist.add_net(name, drv_pin, rcv_pins)
            self._net_endpoints[name] = (drv_inst, rcv_insts)
            if self.bdb is not None and self.bdb_net_mode:
                if unknown_dir:
                    self.bdb.add_net_pins_undirected(name, [drv_pin] + rcv_pins)
                elif inout_dir:
                    self.bdb.add_net_pins_inout(name, [drv_pin] + rcv_pins)
                else:
                    self.bdb.add_net_pins(name, drv_pin, rcv_pins)
        elif cmd == "add_bus":
            # Syntax A (directed):       add_bus <prefix>[N] <drv_pin> <rcv_pin>
            # Syntax B (undirected):     add_bus <prefix>[N] <pin1> <pin2> unknown
            # Syntax C (bidirectional):  add_bus <prefix>[N] <pin1> <pin2> inout
            import re
            last_kw = args[-1].lower() if args else ""
            unknown_dir = (last_kw == "unknown")
            inout_dir   = (last_kw == "inout")
            bus_args = args[:-1] if (unknown_dir or inout_dir) else args
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
            drv_inst = self._pin_instance(drv_pin)
            rcv_insts = [self._pin_instance(r) for r in rcv_pins]
            if not (unknown_dir or inout_dir) and drv_inst in rcv_insts:
                print(f"Error: block '{drv_inst}' is used as both driver and receiver in bus '{prefix}'")
                sys.exit(1)
            for i in range(lo, hi + 1):
                net_name = f"{prefix}_{i}"
                self.netlist.add_net(net_name, drv_pin, rcv_pins)
                self._net_endpoints[net_name] = (drv_inst, rcv_insts)
                if self.bdb is not None and self.bdb_net_mode:
                    if unknown_dir:
                        self.bdb.add_net_pins_undirected(net_name, [drv_pin] + rcv_pins)
                    elif inout_dir:
                        self.bdb.add_net_pins_inout(net_name, [drv_pin] + rcv_pins)
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
        elif cmd == "set_track_pitch":
            # Usage: set_track_pitch <pitch>
            # Declare the inter-bus pitch BEFORE run_planner so its band
            # reservations (Gap 1) match the run_nuts that packs the tracks.
            # run_nuts with no argument reuses this value.
            if not args:
                print("Error: set_track_pitch requires a pitch value"); return
            self._nuts_pitch = float(args[0])
        elif cmd == "run_bundler":
            # run_bundler [STRICT|CONVERGENT|BIDIRECTIONAL]  (default STRICT)
            strat_arg = args[0].upper() if args else "STRICT"
            if strat_arg not in ("STRICT", "CONVERGENT", "BIDIRECTIONAL"):
                print(f"Error: run_bundler strategy must be STRICT, CONVERGENT "
                      f"or BIDIRECTIONAL, got '{args[0]}'"); return
            if strat_arg == "CONVERGENT":
                # CONVERGENT groups nets by shared receiver only, so a bundle can
                # span several DIFFERENT driver blocks at different locations.
                # Topology generation (a single src->dst per bundle) then routes
                # from one driver and leaves the others unrouted.  Warn rather
                # than silently misroute.  See docs/internal/convergent_bundling.md.
                self.bundler.set_strategy(buda.Strategy.CONVERGENT)
                print("Warning: run_bundler CONVERGENT groups nets by shared "
                      "receiver only; bundles that span multiple driver blocks "
                      "are routed from a single driver (the others are left "
                      "unrouted). See docs/internal/convergent_bundling.md.")
            elif strat_arg == "BIDIRECTIONAL":
                # BIDIRECTIONAL bundles nets that connect the SAME set of blocks
                # in any direction (A->B with B->A, a->b,c with b->c,a / c->b,a).
                # Routing is block-to-block and direction-agnostic, so the single
                # trunk serves every net — no warning needed.  (In the visualizer
                # such a busterm is both a driver and a receiver; it gets its own
                # symbol.)
                self.bundler.set_strategy(buda.Strategy.BIDIRECTIONAL)
            else:
                self.bundler.set_strategy(buda.Strategy.STRICT)
            raw_bundles = self.bundler.run(self.netlist)
            self.bundles = []
            for b in raw_bundles:
                w = buda.BundleWrapper()
                w.input.original_bundle = b
                w.input.width = len(b.get_net_names()) * 1.5 # 1.5 layout-units per bit
                self.bundles.append(w)
            print(f"Bundler created {len(self.bundles)} hbundles.")
            self._bundler_strategy = strat_arg
            n = self._persist_bundles(strat_arg)
            if n:
                print(f"[BDB] persisted {n} bundle(s) to the open BDB.")
        elif cmd == "run_hier_bundler":
            # run_hier_bundler [depth <N>] [STRICT|BIDIRECTIONAL]
            if self.bdb is None:
                print("Error: run_hier_bundler requires an open BDB (use open_bdb first)"); return
            max_depth = 1
            if "depth" in args:
                idx = list(args).index("depth")
                if idx + 1 < len(args):
                    max_depth = int(args[idx + 1])
            # Optional strategy token (anything that isn't 'depth'/its value).
            strat_toks = [a.upper() for a in args
                          if a.lower() != "depth" and not a.isdigit()]
            strat = strat_toks[0] if strat_toks else "STRICT"
            if strat not in ("STRICT", "BIDIRECTIONAL"):
                print(f"Error: run_hier_bundler strategy must be STRICT or "
                      f"BIDIRECTIONAL, got '{strat}'"); return
            hb = buda.HierarchicalBundler(self.bdb)
            # BIDIRECTIONAL is direction-agnostic and connects the same blocks, so
            # (like the flat run_bundler) it routes correctly — no warning needed.
            hb.set_strategy(buda.Strategy.BIDIRECTIONAL if strat == "BIDIRECTIONAL"
                            else buda.Strategy.STRICT)
            raw_bundles = hb.run(max_depth)
            self.bundles = []
            for b in raw_bundles:
                w = buda.BundleWrapper()
                w.input.original_bundle = b
                w.input.width = len(b.get_net_names()) * 1.5
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
            self._bundler_strategy = strat
            n = self._persist_bundles(strat)
            if n:
                print(f"[BDB] persisted {n} bundle(s) to the open BDB.")
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
                    b = w.input.original_bundle
                    if filter_depth is not None and b.level != filter_depth:
                        continue
                    if b.drv_spec_depth >= 0:
                        kind = "cross-level"
                    elif b.cell_context:
                        kind = f"cell:{b.cell_context}"
                    else:
                        kind = "cross-block"
                    short_reason = b.reason[:50].rstrip(',')
                    cands = len(w.input.candidates)
                    inst_str = ""
                    if b.instances:
                        insts = list(b.instances)
                        shown = insts[:3]
                        ellipsis = "…" if len(insts) > 3 else ""
                        inst_str = f"  [{', '.join(shown)}{ellipsis}]"
                    print(f"hb-{b.id:<3}  D{b.level}  {kind:<24}  \"{short_reason}\"  "
                          f"nets={len(b.get_net_names())}  cands={cands}{inst_str}")
        elif cmd == "generate_topologies_for_bundle":
            # Usage: generate_topologies_for_bundle <hint> [center_mode] [double_detour]
            # Single dst  → 2-pin L/Z/U candidates
            # Multiple dst → multicast trunk+branch candidates
            # Append "center_mode"    to use block centres instead of busterm faces.
            # Append "double_detour"  to include UU_VHV / UU_HVH high-congestion variants.
            use_center        = "center_mode"   in args
            use_double_detour = "double_detour" in args
            pos_args = [a for a in args if a not in ("center_mode", "double_detour")]
            if not pos_args:
                print("Error: generate_topologies_for_bundle requires a hint")
                return
            hint = pos_args[0]
            topo_gen = self._make_topo_gen(self.fp, use_center, use_double_detour)
            found = False
            for w in self.bundles:
                net_name = w.input.original_bundle.get_net_names()[0]
                if net_name.startswith(hint):
                    ep = self._net_endpoints.get(net_name)
                    if ep is None:
                        print(f"Warning: no endpoint info for net '{net_name}' — skipping bundle {w.input.original_bundle.id}")
                        continue
                    src, dsts = ep
                    self._validate_endpoint_blocks(net_name, src, dsts)
                    w.input.candidates = topo_gen.generate_candidates(src, dsts)
                    self._reset_plan_for_regen(w)
                    label = f"{src}->{dsts[0]}" if len(dsts) == 1 else f"{src}->[{','.join(dsts)}]"
                    print(f"Generated {len(w.input.candidates)} topologies for bundle "
                          f"{w.input.original_bundle.id} ({label})")
                    found = True
            if not found: print(f"Warning: Could not find bundle matching hint {hint}")
            elif self._persist_topologies():
                print("[BDB] re-persisted candidate topologies to the open BDB.")

        elif cmd == "generate_topologies":
            # Usage: generate_topologies [center_mode] [double_detour]
            # Generates topologies for every bundle produced by run_bundler,
            # deriving src/dst block names from the netlist automatically.
            if not self.bundles:
                if self._net_endpoints:
                    print("Warning: no bundles to generate topologies for — nets are "
                          "defined but the netlist hasn't been bundled. Run `run_bundler` "
                          "(or `run_hier_bundler` for a BDB hierarchy) first.")
                else:
                    print("Warning: no bundles to generate topologies for — define nets "
                          "with add_net/add_bus, then run `run_bundler` first.")
                return
            use_center        = "center_mode"   in args
            use_double_detour = "double_detour" in args
            use_multi_trunk   = "multi_trunk"   in args
            topo_gen = self._make_topo_gen(self.fp, use_center, use_double_detour,
                                           use_multi_trunk)
            for w in self.bundles:
                net_name = w.input.original_bundle.get_net_names()[0]
                ep = self._net_endpoints.get(net_name)
                if ep is None:
                    print(f"Warning: no endpoint info for net '{net_name}' — skipping bundle {w.input.original_bundle.id}")
                    continue
                src, dsts = ep
                self._validate_endpoint_blocks(net_name, src, dsts)
                w.input.candidates = topo_gen.generate_candidates(src, dsts)
                self._reset_plan_for_regen(w)
                label = f"{src}->{dsts[0]}" if len(dsts) == 1 else f"{src}->[{','.join(dsts)}]"
                print(f"Generated {len(w.input.candidates)} topologies for bundle "
                      f"{w.input.original_bundle.id} ({label}) {self._bundle_nets_suffix(w)}")
            # Restore the sidecar baseline (pins + per-segment layer overrides) onto
            # the freshly generated candidates, so the live state matches the GUI
            # even before run_planner. A later select_topology overrides it; the
            # sidecar's layer overrides for a matching topology are still merged.
            self._apply_selections()
            nt = self._persist_topologies()
            if nt:
                print(f"[BDB] persisted {nt} candidate topolog"
                      f"{'y' if nt == 1 else 'ies'} to the open BDB.")

        elif cmd == "generate_hier_topologies":
            # generate_hier_topologies [center_mode] [double_detour]
            # Generates topology candidates for all HBundles produced by
            # run_hier_bundler.  Three cases per bundle:
            #   (a) cell-level (cell_context set)     → cell-local floorplan
            #   (c) cross-level (drv_spec_depth >= 0) → custom floorplan from actual endpoint blocks
            #   (b) same-level cross-block             → BDB depth-D floorplan
            if self.bdb is None:
                print("Error: generate_hier_topologies requires an open BDB"); return
            if not self.bundles:
                print("Warning: no HBundles to generate topologies for — run "
                      "`run_hier_bundler` first.")
                return
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
            # Restore the sidecar baseline onto the fresh candidates (see
            # generate_topologies); keeps live state and GUI consistent pre-plan.
            self._apply_selections()
            nt = self._persist_topologies()
            if nt:
                print(f"[BDB] persisted {nt} candidate topolog"
                      f"{'y' if nt == 1 else 'ies'} to the open BDB.")

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
            target_w = next((w for w in self.bundles if w.input.original_bundle.id == bid), None)
            if target_w is None:
                orig_w = next((w for w in self._hier_bundles_orig
                               if w.input.original_bundle.id == bid), None)
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
            if self._persist_topologies():
                print("[BDB] re-persisted candidate topologies to the open BDB.")

        elif cmd == "run_planner":
            if args and args[0] == "post_nuts":
                # Stage 4c: post-NUTS stub layer reassignment.
                # Syntax: post_nuts [V [short [long]]] [H [short [long]]]
                # Bare "post_nuts" (no letter) → V with defaults (backward compat).
                _V_DEFAULTS = (80.0, 200.0)
                _H_DEFAULTS = (150.0, 400.0)
                rest = args[1:]
                # Optional leading 'top' keyword: reassign within TOP layers only
                # (short → next-highest TOP, long → highest TOP), leaving the LOW
                # escape layers out of the spread.
                top_only = False
                if rest and rest[0].lower() == "top":
                    top_only = True
                    rest = rest[1:]
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
                self._run_post_nuts_planner(v_thresholds, h_thresholds, top_only=top_only)
            elif args and args[0] == "hier":
                # run_planner hier [N]
                # Hierarchy-aware planning: expand cell-level bundles to per-instance
                # absolute-coord wrappers, assign priorities, then run the flat planner.
                if self.bdb is None:
                    print("Error: run_planner hier requires an open BDB"); return
                iterations = self._planner_iters(args)
                # Re-planning invalidates any adopted dogleg (and its pins).
                self._reset_doglegs()
                # Apply user-pinned selections to template wrappers BEFORE expansion
                # so topology_pinned + pinned_seg_layers propagate to all instances.
                self._apply_selections()
                # Expand cell-level bundles → per-instance absolute-coord wrappers.
                # Each expanded wrapper gets a unique HBundle ID.
                expanded, self._hier_expansion_map = self._expand_hier_bundles(self.bundles)
                # priority = -(level * 10000 + n_candidates): higher routes first.
                # Depth-0 before depth-1; fewer candidates (less flexibility) first.
                for w in expanded:
                    b = w.input.original_bundle
                    w.hier.priority = -(b.level * 10_000 + len(w.input.candidates))
                    w.hier.level    = b.level   # for the per-level planning summary
                self.planner = buda.CongestionPlanner(self.fp, self.layers)
                for pname, pval in self._planner_params.items():
                    self.planner.set_planner_param(pname, pval)
                # Mirror NUTS inter-bus pitch so the band books reserve the
                # spacing NUTS enforces (Gap 1).  Use set_track_pitch before
                # run_planner to plan a non-default pitch; run_nuts warns if its
                # pitch ends up differing from the one planned here.
                self.planner.set_track_pitch(self._nuts_pitch)
                self._planner_pitch = self._nuts_pitch
                self._configure_capacity_mode(args)   # opt-in signal_tracks (Gap A part 2)
                self.planner.build_congestion_map()
                self._planner_iterations = iterations
                with buda.ostream_redirect():
                    assignments = self.planner.optimize_topologies(expanded, iterations)
                # Apply assignments.  Each expanded wrapper has a unique HBundle ID so
                # this lookup is unambiguous even for multiple cell instances.
                bid_to_wrapper = {w.input.original_bundle.id: w for w in expanded}
                for asn in assignments:
                    w = bid_to_wrapper.get(asn.bundle_id)
                    if w is not None:
                        w.plan.selected_topology_index = asn.topo_index
                        w.input.assigned_v_layer = asn.v_layer_id
                        w.input.assigned_h_layer = asn.h_layer_id
                        w.plan.seg_layers = list(asn.seg_layers)
                        w.plan.seg_perp = list(asn.seg_perp)
                self.bundles = expanded
                self._planner_is_hier = True
                print(f"run_planner hier: {len(self.bundles)} wrappers after expansion")
            else:
                # Re-planning invalidates any adopted dogleg (and its pins): the
                # planner may move neighbors, so cycles are re-detected next NUTS.
                self._reset_doglegs()
                self.planner = buda.CongestionPlanner(self.fp, self.layers)
                for pname, pval in self._planner_params.items():
                    self.planner.set_planner_param(pname, pval)
                # Mirror NUTS inter-bus pitch so the band books reserve the
                # spacing NUTS enforces (Gap 1).  Use set_track_pitch before
                # run_planner to plan a non-default pitch; run_nuts warns if its
                # pitch ends up differing from the one planned here.
                self.planner.set_track_pitch(self._nuts_pitch)
                self._planner_pitch = self._nuts_pitch
                self._configure_capacity_mode(args)   # opt-in signal_tracks (Gap A part 2)
                self.planner.build_congestion_map()
                # Apply architect-pinned selections BEFORE optimizing so the
                # planner scores the correct topology and assigns layers for it.
                self._apply_selections()
                self._planner_is_hier = False
                self._planner_iterations = self._planner_iters(args)
                with buda.ostream_redirect():
                    assignments = self.planner.optimize_topologies(self.bundles, self._planner_iterations)
                # Apply planner layer decisions (vector copy in C++ means we must apply here).
                bid_to_wrapper = {w.input.original_bundle.id: w for w in self.bundles}
                for asn in assignments:
                    w = bid_to_wrapper.get(asn.bundle_id)
                    if w is not None:
                        w.plan.selected_topology_index = asn.topo_index
                        w.input.assigned_v_layer = asn.v_layer_id
                        w.input.assigned_h_layer = asn.h_layer_id
                        w.plan.seg_layers = list(asn.seg_layers)
                        w.plan.seg_perp = list(asn.seg_perp)
            # Persist the planner's decision into the BDB: expanded per-instance
            # bundles (hier), the selected topology, and per-segment assigned
            # layers — for both flows.
            self._persist_planner_output()
        elif cmd == "run_nuts":
            # Usage: run_nuts [track_pitch]
            # NUTS places the planner-selected topology of each bundle, so it
            # needs a plan first. Without one every selected_topology_index is -1
            # (reset by generate_topologies) and NUTS would place 0 segments.
            if not self.bundles:
                print("Warning: run_nuts has no bundles — run run_bundler, "
                      "generate_topologies, and run_planner first.")
                return
            if not any(0 <= w.plan.selected_topology_index < len(w.input.candidates)
                       for w in self.bundles):
                print("Warning: run_nuts found no selected topology to place — run "
                      "`run_planner` (or `run_planner hier`) after generate_topologies "
                      "first (or pin one with select_topology).")
                return
            # Default to the stored pitch (possibly set via set_track_pitch
            # before run_planner) rather than resetting to 1.0, so a planner
            # that reserved bands for a non-default pitch stays consistent.
            pitch = float(args[0]) if args else self._nuts_pitch
            self._nuts_pitch = pitch
            if (self._planner_pitch is not None and
                    abs(pitch - self._planner_pitch) > 1e-9):
                print(f"Warning: run_nuts pitch {pitch} differs from the pitch "
                      f"{self._planner_pitch} run_planner reserved bands for. "
                      f"Set the pitch with 'set_track_pitch <p>' before "
                      f"run_planner (or re-run run_planner) so the planner's "
                      f"pitch-aware band reservations match this NUTS run.")
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
            self._adopt_doglegs()
            layer_names = self._make_layer_names()
            diag = self._nuts_diagnostics(self.nuts_result, layer_names, before)
            self._write_nuts_log(layer_names, extra_lines=diag)
            ns, nv = self._persist_nuts()
            if ns:
                print(f"[BDB] persisted {ns} bus segment(s) and {nv} bus via(s) "
                      f"to the open BDB.")
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
            n_ns, n_nv = self._persist_detailed_nuts()
            if n_ns:
                print(f"[BDB] persisted {n_ns} net segment(s) and {n_nv} "
                      f"net via(s) to the open BDB.")
        elif cmd == "ripup_reroute":
            # Usage: ripup_reroute [max_iter]
            # Stage auto-detected: after run_detailed_nuts ⇒ drive down DNUTS opens;
            # else after run_nuts ⇒ drive down NUTS overlaps.
            max_iter = int(args[0]) if args else _RR_DEFAULT_MAX_ITER
            self._ripup_reroute(max_iter=max_iter)
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
            bid = int(args[0])
            tid = int(args[1])
            if self._select_single_topology_internal(bid, tid):
                self._replan_layers()
                self._persist_topologies()   # refresh is_selected in the BDB

        elif cmd == "select_topologies":
            # Usage: select_topologies <bundle_ids> <topo_id> [<bundle_ids> <topo_id> ...]
            # bundle_ids can be comma-separated or ranges, e.g. "1,5-9,11"
            if len(args) < 2 or len(args) % 2 != 0:
                print("Error: select_topologies requires (bundle_ids, topo_id) pairs")
                return
            any_found = False
            for i in range(0, len(args), 2):
                bid_str = args[i]
                try:
                    tid = int(args[i+1])
                except ValueError:
                    print(f"Error: invalid topology ID '{args[i+1]}'")
                    continue

                # Parse comma-separated IDs and ranges (e.g. "1,5-9,11")
                bids = []
                for chunk in bid_str.split(','):
                    chunk = chunk.strip()
                    if not chunk: continue
                    if '-' in chunk:
                        try:
                            s_start, s_end = chunk.split('-', 1)
                            b_start, b_end = int(s_start), int(s_end)
                            if b_start <= b_end:
                                bids.extend(range(b_start, b_end + 1))
                            else:
                                bids.extend(range(b_start, b_end - 1, -1))
                        except ValueError:
                            print(f"Error: invalid range '{chunk}' in bundle ID list")
                            continue
                    else:
                        try:
                            bids.append(int(chunk))
                        except ValueError:
                            print(f"Error: invalid bundle ID '{chunk}' in list")
                            continue
                
                for bid in bids:
                    if self._select_single_topology_internal(bid, tid):
                        any_found = True
            if any_found:
                self._replan_layers()
                self._persist_topologies()   # refresh is_selected in the BDB

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
            #   visualize_topologies [hint]         — load ALL bundles; a hint just
            #                                         picks which one it opens on, and
            #                                         you can step through the rest
            #                                         with the ◀/▶ Bundle buttons.
            #   visualize_topologies -all [hints…]  — load only bundles matching hints
            #                                         (no hints = every bundle)
            all_mode = bool(args) and args[0] == '-all'
            hints    = args[1:] if all_mode else args[:1]

            # Collect every candidate-bearing bundle once (cell-level hier
            # templates deduplicated); shared with the GUI "View Topologies" path.
            all_wrappers, cell_seen = collect_candidate_bundles(self.bundles)

            def _matches(w):
                names = w.input.original_bundle.get_net_names()
                net0  = names[0] if names else ""
                return (not hints) or any(net0.startswith(h) for h in hints)

            if not all_wrappers:
                print("Warning: no bundle with candidates")
            else:
                if all_mode:
                    # Filter to matching bundles (or all if no hints given).
                    wrappers = [w for w in all_wrappers if _matches(w)] or all_wrappers
                    start = 0
                else:
                    # Load every bundle; open on the first one matching the hint.
                    wrappers = all_wrappers
                    start = next((i for i, w in enumerate(all_wrappers)
                                  if _matches(w)), 0)

                for i, w in enumerate(wrappers):
                    b = w.input.original_bundle
                    cell_key = (b.cell_context, b.reason) if b.cell_context else None
                    inst_note = ""
                    if cell_key is not None and cell_key in cell_seen:
                        cnt = cell_seen[cell_key][1]
                        if cnt > 1:
                            inst_note = f" ({cnt} instances — showing first)"
                    marker = "  ← opens here" if (not all_mode and i == start) else ""
                    print(f"  bundle {b.id}: {len(w.input.candidates)} "
                          f"topologies{inst_note}{marker}")
                TopologyExplorer(self.fp, wrappers,
                                 sidecar_path=self._sidecar_path(),
                                 layer_stack=self.layers,
                                 start_bidx=start).show()
        elif cmd == "dump_topologies":
            # Usage: dump_topologies [hint] [--problems] [--conn]
            # Text inspection of the candidate topologies generated per bundle.
            # `hint` filters to bundles whose first net name starts with it.
            # `--problems` prints only bundles with flagged candidates (duplicate
            # geometry, pinched/zero-slide, single-candidate, pass-through) and
            # an aggregate summary.
            # `--conn` adds, per shown bundle, a per-segment connectivity detail
            # for the selected candidate: what each seg connects to (busterms +
            # other segs), the busterms it passes through, its slide range, and
            # its net-pull preference. Read-only: never mutates session state.
            problems_only = "--problems" in args
            conn_detail = "--conn" in args
            hint = next((a for a in args if not a.startswith("--")), None)
            self._dump_topologies(hint, problems_only, conn_detail)

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
                                 ipc_session=ipc_session,
                                 ipc_verbose=self.ipc_verbose)
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
            # open_bdb <path> [writeback]
            if not args:
                print("Error: open_bdb requires a file path"); return
            # Persist any fixture armed by a previous open_bdb before switching.
            self._flush_bdb_writeback()
            # `writeback` must be the explicit optional argument immediately after
            # the path. A membership test (`in args[1:]`) would also match the word
            # inside a trailing comment — do_command only strips full-line comments,
            # so `open_bdb foo.sql # no writeback` keeps 'writeback' as a token —
            # silently arming write-back on a fixture from a read-looking line.
            writeback = len(args) >= 2 and args[1] == "writeback"
            bdb_path = args[0]
            if (self._script_stack
                    and not os.path.isabs(bdb_path)
                    and bdb_path != ':memory:'):
                parent_dir = os.path.dirname(self._script_stack[-1])
                bdb_path = os.path.normpath(os.path.join(parent_dir, bdb_path))
            # A serialized text BDB (e.g. mix.bdb.sql) is materialized into a
            # throwaway temp binary so the pipeline never dirties the checked-in
            # text fixture. With `writeback`, changes are dumped back to the .sql on
            # save_bdb/exit. See docs/internal/bdb_test_data.md.
            if bdb_path != ':memory:' and bdb_path.endswith('.sql'):
                bdb_path = self._materialize_bdb_sql(bdb_path, writeback=writeback)
            elif writeback:
                print("open_bdb: 'writeback' applies only to a serialized *.sql "
                      "fixture; a binary BDB is opened read-write and persists "
                      "directly — ignoring.")
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
            w, h = float(args[0]), float(args[1])
            if self.bdb is not None:
                self.bdb.set_die(w, h)
            else:
                self._die_w, self._die_h = w, h
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
            self._busterm_gen = buda.BustermGen(self.bdb)
            self._busterm_gen.derive(max_depth)
            bts = self.bdb.all_busterms()
            print(f"derive_busterms: {len(bts)} busterms written (depth 0..{max_depth}).")
        elif cmd == "refine_busterms":
            # refine_busterms — re-derive busterms using the same max_depth as
            # the last derive_busterms call (clears and rewrites the busterm table).
            if self._busterm_gen is None:
                print("Error: run derive_busterms first"); return
            self._busterm_gen.refine()
            bts = self.bdb.all_busterms()
            print(f"refine_busterms: {len(bts)} busterms written.")
        elif cmd == "source":
            if not args:
                msg = "Error: source command requires a file path"
                print(msg); self._log_write(msg)
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
                # Fail fast (like an unknown command): a missing/typo'd source
                # silently continuing would leave the design misconfigured — e.g.
                # no def_layers loaded, so run_planner falls back to its M4/M5
                # default and routes on the wrong metal with no obvious cause.
                where = (f" in {os.path.basename(self._script_stack[-1])}"
                         if self._script_stack else "")
                msg = (f"Error: sourced file not found{where}: {full_path} "
                       f"('{cmd_line.strip()}').")
                print(msg); self._log_write(msg)
                sys.exit(1)

            if self.script_path is None:
                self.script_path = full_path

            self._script_stack.append(full_path)
            try:
                with open(full_path, 'r') as f:
                    for line in f:
                        if not line.strip().startswith('#'):
                            # run_command times + log-routes each command when a
                            # flow log is active; it falls back to do_command
                            # (raw, unlogged) for interactive/embedded callers.
                            self.run_command(line)
            finally:
                self._script_stack.pop()

        elif cmd == "save_bdb":
            # Serialize the working BDB back to its writeback source .sql now.
            # Only meaningful after `open_bdb <file>.sql writeback`.
            if self._write_bdb_sql():
                print(f"save_bdb: wrote {self._bdb_writeback_src}")
            else:
                print("save_bdb: nothing to write — open the BDB with "
                      "`open_bdb <file>.sql writeback` to enable write-back.")

        elif cmd == "exit":
            # Stop the run mid-script (handy for debugging a flow incrementally).
            # Optional integer exit code (default 0 = clean stop).
            code = 0
            if args:
                try:
                    code = int(args[0])
                except ValueError:
                    print(f"Error: exit code must be an integer, got '{args[0]}'")
                    code = 1
            self._flush_bdb_writeback()  # persist an armed fixture before stopping
            where = (f" in {os.path.basename(self._script_stack[-1])}"
                     if self._script_stack else "")
            print(f"Exiting on 'exit' command{where} (code {code}).")
            sys.exit(code)

        else:
            # Unknown command — fail loudly rather than silently skipping it.
            # A typo like 'add_layer' (the command is 'def_layer') would otherwise
            # leave the design misconfigured (no layers) with no warning.
            import difflib
            sugg = difflib.get_close_matches(cmd, KNOWN_COMMANDS, n=1)
            hint = f" Did you mean '{sugg[0]}'?" if sugg else ""
            where = (f" in {os.path.basename(self._script_stack[-1])}"
                     if self._script_stack else "")
            print(f"Error: unknown command '{cmd}'{where} — "
                  f"'{cmd_line.strip()}'.{hint}")
            sys.exit(1)

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
                not w.input.candidates or w.plan.selected_topology_index < 0
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
                if w.input.candidates and w.plan.selected_topology_index >= 0:
                    topo = w.input.candidates[w.plan.selected_topology_index]
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
        collected = []   # (prefix, violation) — aggregated below unless --verbose-conn
        for w in self.bundles:
            if not w.input.candidates:
                continue
            bid = w.input.original_bundle.id

            b = w.input.original_bundle
            check_fp = self.fp
            if (self.bdb is not None and isinstance(b, buda.HBundle)
                    and id(w) not in expanded_ids):
                resolved = self._floorplan_for_hbundle(b, hier_fp_cache, comps_by_name)
                if resolved is not None:
                    check_fp = resolved

            if all_candidates and stage == "topo":
                to_check = list(enumerate(w.input.candidates))
            elif w.plan.selected_topology_index >= 0:
                idx = w.plan.selected_topology_index
                to_check = [(idx, w.input.candidates[idx])]
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
                    num_bits = len(w.input.original_bundle.get_net_names())
                    res = buda.check_dnuts(ct, self.detailed_result, topo, check_fp,
                                           self.layers, bid, num_bits)

                for v in res.violations:
                    if all_candidates and stage == "topo":
                        prefix = f"Bundle {bid} topo {topo_idx + 1} ({topo.type})"
                    else:
                        prefix = f"Bundle {bid}"
                    collected.append((prefix, v))
                    total += 1

        if total == 0:
            print("  Success: no opens found.")
        elif self.verbose_conn:
            for prefix, v in collected:
                print(f"  {prefix}: {v.message}")
        else:
            self._report_violations_summary(collected)

    # Reason text per ViolationKind, used when collapsing per-bit violations.
    _CONN_KIND_REASON = {
        "UNPLACED":     "unplaced (no track in DetailedNUTS)",
        "BUSTERM_OPEN": "no pass-through/busterm connection",
        "BUSTERM_FACE": "invalid busterm face",
        "SEG_OPEN":     "segment disconnected",
        "LAYER_DIR":    "wrong layer direction",
        "FEEDTHRU_RELAY": "block used as feedthrough relay (segments not wire-joined)",
    }
    _CONN_GROUP_CAP = 100   # max summary lines before eliding the rest

    def _report_violations_summary(self, collected):
        """Collapse the per-bit connectivity violations into one line per
        (bundle, topo, kind, locus) group.  On a large design this turns tens
        of thousands of 'Seg N Bit M ...' lines into a few hundred.  Pass
        --verbose-conn to restore the full per-bit dump."""
        from collections import OrderedDict
        groups = OrderedDict()
        for prefix, v in collected:
            key = (prefix, v.kind.name, v.seg_idx, v.seg_idx2, v.block_name)
            g = groups.get(key)
            if g is None:
                g = {"prefix": prefix, "kind": v.kind.name, "seg_idx": v.seg_idx,
                     "seg_idx2": v.seg_idx2, "block": v.block_name,
                     "bits": set(), "msg": v.message}
                groups[key] = g
            if v.bit_index >= 0:
                g["bits"].add(v.bit_index)

        def locus(g):
            if g["block"]:
                return f"Block '{g['block']}'"
            if g["seg_idx"] >= 0 and g["seg_idx2"] >= 0:
                return f"Seg {g['seg_idx']}<->{g['seg_idx2']}"
            if g["seg_idx"] >= 0:
                return f"Seg {g['seg_idx']}"
            return ""

        bundles = set()
        for i, g in enumerate(groups.values()):
            bundles.add(g["prefix"])
            if i >= self._CONN_GROUP_CAP:
                continue
            nbits = len(g["bits"])
            if nbits == 0:
                # Not a per-bit violation (topo/nuts stage) — show it verbatim.
                print(f"  {g['prefix']}: {g['msg']}")
            else:
                loc = locus(g)
                loc_part = f"{loc}: " if loc else ""
                reason = self._CONN_KIND_REASON.get(g["kind"], g["kind"])
                print(f"  {g['prefix']}: {loc_part}{nbits} bit(s) — {reason}")

        n_groups = len(groups)
        if n_groups > self._CONN_GROUP_CAP:
            print(f"  ... and {n_groups - self._CONN_GROUP_CAP} more group(s) "
                  f"(use --verbose-conn for full detail).")
        total = sum(max(1, len(g["bits"])) for g in groups.values())
        print(f"  Total: {total} violation(s) in {n_groups} group(s) across "
              f"{len(bundles)} bundle(s). Use --verbose-conn for per-bit detail.")

def main():
    parser = argparse.ArgumentParser(
        prog='buda',
        description='Run a BUDA interconnect-planning flow script (.buda). '
                    'Executes the script top-to-bottom, printing a one-line '
                    'summary per command; full detail goes to the flow log.',
        epilog='Script commands are documented in docs/BUDA_SCRIPT_REFERENCE.md; '
               'these command-line options in docs/BUDA_CLI.md.')
    parser.add_argument('script', nargs='?',
                        help='path to a .buda flow script; a missing .buda '
                             'suffix is added automatically')
    parser.add_argument('--no-viz', action='store_true',
                        help='skip visualize commands (useful for batch/CI runs)')
    parser.add_argument('--verbose-conn', action='store_true',
                        help='print every connectivity violation individually; '
                             'default collapses per-bit violations into a summary')
    parser.add_argument('--ipc-verbose', action='store_true',
                        help='surface buda_viz/def_viz IPC socket status chatter '
                             '(listening/connected/timer lines); off by default')
    args = parser.parse_args()
    session = BudaSession()
    session.no_viz = args.no_viz
    session.verbose_conn = args.verbose_conn
    session.ipc_verbose = args.ipc_verbose
    if args.script:
        script = args.script
        if not os.path.exists(script) and not script.endswith('.buda'):
            script = script + '.buda'
        session.script_path = os.path.abspath(script)

        # Open a flow log that captures the FULL detail of every command
        # (Python prints + C++ output routed through sys.stdout via
        # buda.ostream_redirect).  run_command mirrors each command's detail
        # here and prints only a one-line summary to the terminal, so the two
        # are no longer duplicated.
        flow_log_path = session._get_log_path('flow.log')
        try:
            session._flow_log = open(flow_log_path, 'w', buffering=1)
        except OSError as e:
            print(f"Warning: could not open flow log {flow_log_path}: {e}")

        try:
            session.run_command(f"source {script}")
            # Persist a fixture opened with `open_bdb <file>.sql writeback` if the
            # run completed without an explicit exit (which flushes on its own).
            session._flush_bdb_writeback()
        finally:
            session.print_runtime_summary(sys.stdout)
            if session._flow_log is not None:
                print(f"Full per-command detail → {flow_log_path}")
                session._flow_log.close()

if __name__ == "__main__":
    main()
