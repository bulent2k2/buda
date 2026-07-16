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

"""BDB persistence + pipeline rehydration.

The _persist_* checkpoint writers (bundles, candidate topologies, planner
output, NUTS bus segments/vias, detailed bit-wires, route snapshots), the
single-transaction _bdb_batch machinery, the *.bdb.sql text-fixture
materialize/write-back pair, and _load_pipeline_from_bdb — the resume flow
that rehydrates bundles, candidates, plan state, and the abstract-NUTS
result from a previously persisted BDB.

Methods extracted verbatim from buda_cli.BudaSession (the CLI mixin
split); bodies unchanged — `self` is the composed BudaSession, so
cross-mixin helper calls resolve through the class as before.
"""
import contextlib
import hashlib
import os

import buda

from .util import _batched


class PersistMixin:

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

    @_batched
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
        # The per-bundle generation-knob memo (v15) must survive this
        # clear-and-rewrite — it is written OUTSIDE this path (generate_more)
        # and would otherwise be wiped by every re-persist.
        knob_memo = {}
        for _w in self.bundles:
            _bid = str(_w.input.original_bundle.id)
            _k = self.bdb.bundle_gen_knobs(_bid)
            if _k:
                knob_memo[_bid] = _k
        self.bdb.clear_bundles(keep_user=True)
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
            # v19: rotation-class clone provenance survives every rewrite.
            row.cloned_from = str((getattr(self, "_bu_clone_from", None)
                                   or {}).get(hb.id, ""))
            self.bdb.add_bundle(row)
            for nm in hb.get_net_names():
                self.bdb.add_bundle_net(row.id, nm)
            for bt in hb.entry_busterm_ids:
                self.bdb.add_bundle_busterm(row.id, bt, "entry")
            for bt in hb.exit_busterm_ids:
                self.bdb.add_bundle_busterm(row.id, bt, "exit")
        for _bid, _k in knob_memo.items():
            self.bdb.set_bundle_gen_knobs(_bid, _k)
        return len(self.bundles)

    @contextlib.contextmanager
    def _bdb_batch(self):
        """Fold a burst of BDB row inserts into ONE transaction.

        Nestable via the C++ depth counter, so composing persist helpers each
        wrap their own body safely; a mid-batch exception rolls the whole stack
        back.  No-op when no BDB is open.  See the _batched decorator.
        """
        if self.bdb is None:
            yield
            return
        self.bdb.begin_batch()
        ok = False
        try:
            yield
            ok = True
        finally:
            if not ok:
                self.bdb.rollback_batch()            # body failed → discard
            else:
                # A failed COMMIT leaves the transaction open (begin/commit_batch
                # only advance the depth on SQL success), so roll it back rather
                # than leak a half-open batch into the next command.
                try:
                    self.bdb.commit_batch()
                except BaseException:
                    self.bdb.rollback_batch()
                    raise

    @_batched
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
        # v15 (Phase E4): the wipe spares source='user' rows, so a hand-
        # committed candidate from an EARLIER session (not in this pool)
        # cannot be deleted by a bulk re-persist.  Kept rows are then moved
        # out of the fresh 0..n-1 index block (or dropped if the pool itself
        # carries the same uid — the pool write recreates them).
        self.bdb.clear_topologies(keep_user=True)
        for w in self.bundles:
            bid = str(w.input.original_bundle.id)
            kept = self.bdb.topologies(bid)
            if not kept:
                continue
            pool_uids = {buda.topo_uid(t) for t in w.input.candidates}
            n_new = len(w.input.candidates)
            next_ci = n_new
            for tr in kept:
                if tr.topo_uid in pool_uids:
                    self.bdb.delete_topology(bid, tr.cand_index)
                    continue
                ci = tr.cand_index
                if ci < n_new:
                    while any(k.cand_index == next_ci for k in kept):
                        next_ci += 1
                    self.bdb.renumber_topology(bid, ci, next_ci)
                    ci = next_ci
                    next_ci += 1
                print(f"  [BDB] kept user candidate {tr.topo_uid} of bundle "
                      f"{bid} (not in this session's pool) at index "
                      f"{ci + 1} — load_pipeline restores it.")
        n_cands = 0
        # clear_topologies wiped the 'tb:' busterm rows, so this pass rewrites
        # them; dedup so each block's (identical, JSON-rects-heavy) busterm row
        # is written once across all candidates, not once per candidate.
        seen_busterms = set()
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
                # A pre-plan select_topology pin must survive a checkpoint so a
                # resumed run_planner still honors it (v10).
                tr.is_pinned = bool(w.input.topology_pinned and ci == selected)
                # Stable content identity (v14, Phase E1): recomputable from the
                # persisted rows alone, so uid(generated) == uid(reloaded).
                tr.topo_uid = buda.topo_uid(topo)
                # Provenance (v15, Phase E4): protects user candidates from the
                # keep_user wipe above; dogleg = the adopted split slot.
                tr.source = ("user" if topo.type == "USER" else
                             "dogleg" if self._dogleg_slot.get(
                                 w.input.original_bundle.id) == ci else
                             "generated")
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
                    sr.edge_id = seg.edge_id
                    sr.perp_clamp_lo = seg.perp_clamp_lo
                    sr.perp_clamp_hi = seg.perp_clamp_hi
                    self.bdb.add_topology_segment(sr)
                self._persist_topology_annotations(bid, ci, topo, seen_busterms)
                n_cands += 1
        return n_cands

    def _persist_topology_annotations(self, bid, ci, topo, seen_busterms=None):
        """Persist ONE candidate's derived annotations — ALWAYS as a pair:

        - the authoritative seg-busterm links (LOGICAL connectivity; a reload
          rebuilds it from these, never re-deriving from geometry),
        - the seg-to-seg junction links (seg_conns, v12 — the other half of
          the topology's connectivity truth; load_seg_busterms restores both,
          falling back to a geometric derive only for pre-v12 checkpoints), and
        - the TEG-over bridge segments (bridge_segments: block_name -> Segment,
          v11 — without them a resumed TEG-over multi-rect design silently
          drops the bridge over the block's notch).

        The single choke point for every topology-persist site: a future site
        that called some but not all of these would silently reintroduce a
        lossy resume, and nothing would fail until that path was resumed.
        The candidate's topology row must already exist (FK parent).

        `seen_busterms` (a set of already-written 'tb:' ids, scoped to one
        persist pass) dedups the heavy busterm-row insert across a bundle's
        candidates — the same block busterm is written once, then only the
        cheap link rows per candidate.  None = write every row (planner path).
        """
        buda.persist_seg_busterms(self.bdb, bid, ci, topo, seen_busterms)
        buda.persist_seg_conns(self.bdb, bid, ci, topo)
        for name, seg in topo.bridge_segments.items():
            r = buda.TopoBridgeRow()
            r.id = bid
            r.cand_index = ci
            r.block_name = name
            r.x1, r.y1 = seg.start.x, seg.start.y
            r.x2, r.y2 = seg.end.x, seg.end.y
            r.layer_hint = seg.layer_hint
            r.is_jog = seg.is_jog
            self.bdb.add_topology_bridge(r)

    @_batched
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
            # Stage-4 solver state (v9): lets load_pipeline rehydrate a
            # NUTSResult good enough to resume into detailed NUTS.
            r.interval_lo = ts.interval_lo
            r.interval_hi = ts.interval_hi
            r.track_lo_bound = ts.track_lo_bound
            r.track_hi_bound = ts.track_hi_bound
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

    @_batched
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


    def _restore_wrapper(self, br, h_layer_ids, v_layer_ids, missing_blocks,
                         fp=None, fp_env=None):
        """Rebuild ONE BundleWrapper from its persisted rows (bundle +
        candidate topologies + segments + logical annotations + bridges +
        selection/pin/layers/bu_locked) — the loader loop's body, extracted
        so the expanded view can ALSO reconstruct the pre-expansion
        bottom-up TEMPLATE wrappers (_restore_bottom_up_templates).

        `fp` is the floorplan candidates are validated against (block-name
        existence gate before load_seg_busterms) — a cell-local TEMPLATE
        passes its own cell-local floorplan, since its block names are
        cell-local and legitimately absent from self.fp.  With fp=None the
        frame is RESOLVED per bundle (fp_env = the loader's shared
        (fp_cache, comps_by_name)): a pre-expansion cell-local template /
        replica or a cross-level bundle gets its own floorplan via the same
        `_floorplan_for_hbundle` cases check_design uses — so a hier
        checkpoint taken BEFORE `run_planner hier` (templates only, incl. a
        hand-committed USER candidate) loads without tripping the
        missing-block gate; everything else validates against self.fp.
        Returns None when the row has no persisted candidates (e.g. a
        bundler-only stop); unknown block names accumulate in
        missing_blocks (the caller decides whether that is fatal)."""
        import json
        topos = self.bdb.topologies(br.id)
        if not topos:
            return None
        hb = buda.HBundle()
        hb.id = int(br.id)
        hb.net_names = list(self.bdb.bundle_nets(br.id))
        hb.reason = br.reason
        hb.num_terminals = br.num_terminals
        hb.level = br.level
        hb.cell_context = br.cell_context
        hb.instances = json.loads(br.instances) if br.instances else []
        hb.parent_id = int(br.parent_id) if br.parent_id else -1
        hb.drv_spec_depth = br.drv_spec_depth
        hb.rcv_spec_depth = br.rcv_spec_depth
        hb.drv_spec_path = br.drv_spec_path
        hb.rcv_spec_paths = (json.loads(br.rcv_spec_paths)
                             if br.rcv_spec_paths else [])
        # The entry/exit busterm links are persisted (bundle_busterm) but were
        # never restored — and `entry_busterm_ids` is exactly the marker
        # _floorplan_for_hbundle's cell-local case gates on, so a resumed
        # session's check_design/dump/TopoEdit could not resolve a template's
        # frame without this.
        ent, ext = [], []
        for bt_id, kind in self.bdb.bundle_busterms(br.id):
            (ent if kind == "entry" else ext).append(bt_id)
        hb.entry_busterm_ids = ent
        hb.exit_busterm_ids = ext
        if fp is None:
            fp = self.fp
            if (fp_env is not None
                    and not getattr(br, "is_expanded", False)
                    and ((hb.cell_context and hb.entry_busterm_ids)
                         or hb.drv_spec_depth >= 0)):
                fp_cache, comps_by_name = fp_env
                resolved = self._floorplan_for_hbundle(hb, fp_cache,
                                                       comps_by_name)
                if resolved is not None:
                    fp = resolved
        w = buda.BundleWrapper()
        w.input.original_bundle = hb
        w.input.width = len(hb.get_net_names()) * 1.5   # as run_bundler sets it
        # sel/sel_ci: the selected candidate's COMPACT in-memory index vs its
        # PERSISTED cand_index. They differ when only a subset of candidates
        # was persisted (hier expanded bundles keep just their selected
        # topology, at its original template cand_index).
        cands, sel, sel_ci, pinned = [], -1, -1, False
        # One bulk read per bundle (empty for non-TEG designs, the common
        # case) instead of one SELECT per candidate.
        bridges_by_ci = {}
        for brg in self.bdb.all_topology_bridges(br.id):
            bridges_by_ci.setdefault(brg.cand_index, []).append(brg)
        for tr in topos:
            t = buda.Topology()
            t.type = tr.type
            t.estimated_wirelength = tr.wirelength
            t.trunk_location = tr.trunk_location
            t.pass_through_count = tr.pass_through_count
            t.connected_block_names = (json.loads(tr.connected_blocks)
                                       if tr.connected_blocks else [])
            t.feedthru_blocks = (json.loads(tr.feedthru_blocks)
                                 if tr.feedthru_blocks else [])
            segs = []
            for sr in self.bdb.topology_segments(br.id, tr.cand_index):
                sg = buda.Segment()
                sg.start = buda.Point(int(sr.x1), int(sr.y1))
                sg.end = buda.Point(int(sr.x2), int(sr.y2))
                sg.layer_hint = sr.layer_hint
                sg.is_jog = sr.is_jog
                sg.edge_id = sr.edge_id       # MST-edge identity (v14)
                sg.perp_clamp_lo = sr.perp_clamp_lo   # overlap-U slide clamp (v16)
                sg.perp_clamp_hi = sr.perp_clamp_hi
                segs.append(sg)
            t.segments = segs        # reassign whole vector (pybind copies)
            bad = [n for n in t.connected_block_names
                   if not fp.has_block(n)]
            if bad:
                missing_blocks.update(bad)
            else:
                # Restore the authoritative seg_busterms annotation from the
                # persisted topology_seg_busterm links — LOGICALLY, never
                # re-derived from geometry (single-source-of-topo-truth
                # Phase 3: annotate_topology would just re-guess what the
                # links record exactly).
                # (also re-derives seg_conns — Phase 4's junction records —
                # inside the helper, so EVERY reload path gets a fully
                # annotated topology, not just load_pipeline; Phase 5 will
                # persist them logically like the busterm links.)
                buda.load_seg_busterms(self.bdb, br.id, tr.cand_index, t)
            # TEG-over bridges (v11): the explicit segment over a multi-rect
            # block's notch, kept OUTSIDE t.segments (bridge_segments map).
            bridges = {}
            for brg in bridges_by_ci.get(tr.cand_index, ()):
                sg = buda.Segment()
                sg.start = buda.Point(int(brg.x1), int(brg.y1))
                sg.end = buda.Point(int(brg.x2), int(brg.y2))
                sg.layer_hint = brg.layer_hint
                sg.is_jog = brg.is_jog
                bridges[brg.block_name] = sg
            if bridges:
                t.bridge_segments = bridges
            # v14 uid integrity: topo_uid is recomputable from the persisted
            # rows alone, so a reloaded candidate must reproduce it exactly
            # (uid(generated) == uid(reloaded) — Phase E1's round-trip
            # contract). Pre-v14 checkpoints carry no uid and backfill
            # silently; a mismatch on a v14 checkpoint flags a lossy reload.
            if tr.topo_uid and not bad and buda.topo_uid(t) != tr.topo_uid:
                print(f"  Warning: bundle {br.id} cand {tr.cand_index}: "
                      f"reloaded topo_uid {buda.topo_uid(t)} != persisted "
                      f"{tr.topo_uid} (lossy checkpoint?)")
            if tr.is_selected:
                sel = len(cands)     # compact index of this candidate
                sel_ci = tr.cand_index
                pinned = tr.is_pinned
            cands.append(t)
        w.input.candidates = cands
        if sel >= 0:
            # Restore the planner's decision so run_nuts can run directly.
            w.plan.selected_topology_index = sel
            # A pre-plan select_topology pin survives the checkpoint so a
            # resumed run_planner honors it (Codex #136 P2).
            w.input.topology_pinned = pinned
            layers = [sr.assigned_layer
                      for sr in self.bdb.topology_segments(br.id, sel_ci)]
            if any(l >= 0 for l in layers):
                w.plan.seg_layers = layers
                for l in layers:
                    if l in h_layer_ids and w.input.assigned_h_layer < 0:
                        w.input.assigned_h_layer = l
                    if l in v_layer_ids and w.input.assigned_v_layer < 0:
                        w.input.assigned_v_layer = l
            # Bottom-up copy (v18): restore the wrapper LOCKED — pinned
            # index + pinned layers, planned first, never moved by
            # rip-up/negotiate/ripup_reroute — so a resumed session keeps
            # template uniformity instead of re-deciding per instance.
            if getattr(br, "bu_locked", False):
                w.hier.locked = True
                w.input.topology_pinned = True
                w.input.pinned_seg_layers = list(w.plan.seg_layers)
        return w

    def _restore_bottom_up_templates(self, exp_map, all_rows,
                                     h_layer_ids, v_layer_ids):
        """Rebuild the pre-expansion bottom-up TEMPLATE wrappers from the
        persisted template bundle rows (candidates + the v18-persisted
        local-solve selection/layers), into self._hier_bundles_orig — so a
        resume from a PRE-run_nuts checkpoint re-runs the cell-local solve
        and keeps NUTS-copy uniformity instead of falling back
        per-instance with a WARNING (the opens.md resume conditional).

        Templates are the canonical parents of the expanded instance rows
        (exp_map keys); only marked cells' templates (clone contexts
        resolved via _bu_cell_of) are restored.  Each is validated against
        its OWN cell-local floorplan — its block names are cell-local and
        legitimately absent from self.fp — and its persisted selection is
        restored as the FULL pin stage (b) requires (topology_pinned +
        pinned_seg_layers), exactly what _plan_bottom_up_templates wrote
        live.  A template without a usable persisted selection is skipped
        with a WARNING (its cell falls back per-instance, loud as before)."""
        bu = set(self.bdb.bottom_up_cells())
        if not bu:
            return
        templates = []
        for tid in sorted(exp_map):
            br = all_rows.get(str(tid))
            if (br is None or not br.cell_context
                    or self._bu_cell_of(br.cell_context) not in bu):
                continue
            import json
            insts = json.loads(br.instances) if br.instances else []
            cell_fp = (self._build_cell_local_floorplan(insts[0])
                       if insts else None)
            if cell_fp is None:
                continue
            missing = set()
            w = self._restore_wrapper(br, h_layer_ids, v_layer_ids,
                                      missing, fp=cell_fp)
            if w is None:
                continue
            if (0 <= w.plan.selected_topology_index
                    < len(w.input.candidates)
                    and w.plan.seg_layers
                    and any(l >= 0 for l in w.plan.seg_layers)):
                w.input.topology_pinned = True
                w.input.pinned_seg_layers = list(w.plan.seg_layers)
                templates.append(w)
            else:
                print(f"WARNING: bottom-up template {br.id} "
                      f"('{br.cell_context}') restored without a persisted "
                      f"local-solve selection — its cell falls back to "
                      f"per-instance NUTS on this resume")
        if templates:
            self._hier_bundles_orig = templates
            print(f"[BottomUp] restored {len(templates)} template "
                  f"wrapper(s) from the checkpoint — a pre-run_nuts resume "
                  f"re-runs the cell-local solve; a post-run_nuts resume "
                  f"keeps the persisted routing")

    def _load_pipeline_from_bdb(self, expanded=False):
        """Rehydrate the in-memory pipeline from the open BDB (resume path).

        Reverses the persistence: rebuilds self.bundles (HBundle in bit order via
        bundle_net.ord + all candidate Topologies, their seg_busterms restored
        LOGICALLY from the topology_seg_busterm links — single-source-of-topo-
        truth, never re-derived from geometry), the plan (selected candidate +
        assigned layers + pre-plan pin) from is_selected / assigned_layer /
        is_pinned, and — when bus rows exist — a NUTSResult from bus_segment, so
        a fresh session can stop after generate_topologies / run_planner /
        run_nuts and continue with run_planner / run_nuts / run_detailed_nuts.

        Requires the Floorplan + LayerStack to be re-declared first (topology
        coordinates are absolute; the planner/NUTS/ConnTopology re-derive slide
        ranges and net_pull from geometry + Floorplan — those are recomputed,
        by design). `expanded` selects the hier post-expansion view
        (is_replicated=1 per-instance rows + non-template bundles) instead of
        the pre-expansion templates; an expanded instance persists only its
        selected topology, so its selected index is remapped to the compact
        in-memory candidate list.

        Not restored (recomputed downstream or absent): seg_perp (a NUTS
        placement *preference* from the planner's charged bands — a resumed
        run_nuts may legally place segments at different track positions than
        the original session), planner band state, overlap details, doglegs.
        TEG-over bridge segments ARE restored (topology_bridge_segment, v11).
        """
        import json
        if self.bdb is None:
            print("Error: load_pipeline requires an open BDB (open_bdb first)")
            return 0
        rows = self.bdb.all_bundles()
        # v19: rebuild the rotation-class clone registry (clone cell_context
        # → real marked cell) so the bottom-up gates resolve clone templates
        # through _bu_cell_of on this resumed session too.
        by_id = {b.id: b for b in rows}
        self._bu_clone_cells = {}
        self._bu_clone_from = {}
        for b in rows:
            if getattr(b, "cloned_from", ""):
                origin = by_id.get(b.cloned_from)
                if origin is not None and origin.cell_context:
                    self._bu_clone_cells[b.cell_context] = origin.cell_context
                    try:
                        self._bu_clone_from[int(b.id)] = int(b.cloned_from)
                    except ValueError:
                        pass
        if self._bu_clone_cells:
            print(f"[BottomUp] restored {len(self._bu_clone_cells)} "
                  f"rotation-class clone template(s): "
                  + ", ".join(f"{k}←{v}"
                              for k, v in sorted(self._bu_clone_cells.items())))
        if expanded:
            exp_rows = [b for b in rows if b.is_expanded]
            if exp_rows:
                # v18: exact view — the planner-expanded instance rows plus
                # every bundle that is neither a template of one, nor a
                # bundler replica of one (replicas persist is_replicated=0
                # with parent_id=template and carry the template's
                # cell-local candidates — parent linkage is their only tell).
                tids = {b.parent_id for b in exp_rows}
                rows = exp_rows + [b for b in rows
                                   if not b.is_expanded
                                   and b.id not in tids
                                   and b.parent_id not in tids]
            else:
                # Pre-v18 checkpoint: the legacy heuristic (expanded rows'
                # parent_ids happened to cover replicas too).
                template_ids = {b.parent_id for b in rows if b.is_replicated}
                rows = [b for b in rows
                        if b.is_replicated or b.id not in template_ids]
        else:
            rows = [b for b in rows if not b.is_replicated]
        if not rows:
            print("Error: load_pipeline found no persisted bundles — run "
                  "run_bundler (and generate_topologies) with a BDB open first")
            return 0

        h_layer_ids = set(self.layers.get_layer_ids_by_dir(buda.LayerDir.HORIZONTAL))
        v_layer_ids = set(self.layers.get_layer_ids_by_dir(buda.LayerDir.VERTICAL))
        # Shared frame-resolution environment for _restore_wrapper's fp=None
        # path (cell-local templates / cross-level bundles validate in their
        # own floorplan, not self.fp — see the docstring there).
        fp_env = ({}, {c.name: c for c in self.bdb.all_components()})
        bundles, missing_blocks, skipped = [], set(), 0
        for br in rows:
            w = self._restore_wrapper(br, h_layer_ids, v_layer_ids,
                                      missing_blocks, fp_env=fp_env)
            if w is None:
                skipped += 1
                continue          # no candidates persisted (e.g. bundler-only stop)
            bundles.append(w)

        if missing_blocks:
            print(f"Error: load_pipeline: block(s) "
                  f"{', '.join(sorted(missing_blocks))} referenced by persisted "
                  f"topologies are not in the current Floorplan — re-declare the "
                  f"setup (add_block / add_blocks_from_bdb + def_layer) before "
                  f"load_pipeline")
            return 0
        if not bundles:
            print("Error: load_pipeline found no persisted candidate topologies "
                  "— run generate_topologies with a BDB open first")
            return 0
        self.bundles = bundles
        if expanded:
            # Rebuild the hier bookkeeping the bottom-up machinery needs on
            # resume: the planner ran (this IS the post-expansion view), and
            # the template→instances map comes back from parent_id links.
            # parent_id may point at a REPLICA (pre-fix checkpoints) — walk
            # the chain to the root template so sibling instances group.
            self._planner_is_hier = True
            all_rows = {b.id: b for b in self.bdb.all_bundles()}

            def canon(pid):
                seen = set()
                while (pid in all_rows and all_rows[pid].parent_id
                       and pid not in seen):
                    seen.add(pid)
                    pid = all_rows[pid].parent_id
                return pid

            exp_map = {}
            by_id = {w.input.original_bundle.id: w for w in bundles}
            for br in rows:
                if br.is_replicated and br.parent_id:
                    w = by_id.get(int(br.id))
                    if w is not None:
                        exp_map.setdefault(int(canon(br.parent_id)),
                                           []).append(w)
            if exp_map:
                self._hier_expansion_map = exp_map
                self._restore_bottom_up_templates(exp_map, all_rows,
                                                  h_layer_ids, v_layer_ids)
                # Post-run_nuts resumes source the bottom-up fixed copies
                # from the persisted routing (exact); a pre-run_nuts resume
                # has none and falls through to a fresh local solve on the
                # restored templates.  A re-plan clears the flag.
                self._bu_fixed_from_resume = True
        # Bottom-up mismatch policy survives the checkpoint (meta, v17+).
        pol = self.bdb.meta_get("bu_mismatch_policy", "")
        if pol in ("stop", "independent"):
            self._bu_mismatch_policy = pol

        # Rehydrate the abstract-NUTS result (if run_nuts was persisted) so
        # run_detailed_nuts can resume from it.
        ts_list = []
        for w in self.bundles:
            bid = str(w.input.original_bundle.id)
            for g in self.bdb.bus_segments(bid):
                ts = buda.TrackSegment()
                ts.bundle_id = int(g.id)
                ts.seg_idx = g.seg_idx
                ts.layer = g.layer
                ts.horiz = g.is_horiz
                if g.is_horiz:                # span is x; track_position is y
                    ts.span_lo, ts.span_hi = g.x1, g.x2
                else:                         # span is y; track_position is x
                    ts.span_lo, ts.span_hi = g.y1, g.y2
                ts.track_position = g.track_position
                ts.width = g.width
                ts.placed = g.placed
                ts.is_jog = g.is_jog
                ts.interval_lo = g.interval_lo
                ts.interval_hi = g.interval_hi
                ts.track_lo_bound = g.track_lo_bound
                ts.track_hi_bound = g.track_hi_bound
                ts_list.append(ts)
        if ts_list:
            nr = buda.NUTSResult()
            nr.segments = ts_list
            self.nuts_result = nr             # persisted routing = final, clean

        n_cand = sum(len(w.input.candidates) for w in self.bundles)
        n_planned = sum(1 for w in self.bundles
                        if w.plan.selected_topology_index >= 0)
        stage = ("abstract NUTS" if ts_list else
                 "planner" if n_planned else "topologies")
        print(f"[load_pipeline] rehydrated {len(self.bundles)} bundle(s), "
              f"{n_cand} candidate topolog{'y' if n_cand == 1 else 'ies'}, "
              f"{n_planned} planned selection(s)"
              + (f", {len(ts_list)} placed bus segment(s)" if ts_list else "")
              + f" from the BDB (deepest persisted stage: {stage})"
              + (f"; {skipped} bundle(s) skipped (no candidates)" if skipped else ""))
        return len(self.bundles)

    @_batched
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
        if getattr(self, '_rr_in_trial', False):
            # A ripup trial's full-replan fallback drives the run_planner
            # COMMAND; its state is speculative and usually rejected — it
            # must never reach the BDB (a load_pipeline resume would
            # rehydrate it).  The accepted final state is persisted by
            # _checkpoint_routing when the run commits moves.
            return 0
        self.bdb.clear_expanded_bundles()          # idempotent re-plan
        # An id is an expanded per-instance wrapper ONLY if it came from the hier
        # expansion map — NOT merely because it's absent from the BDB (a flat flow
        # can open_bdb after generate, so its normal bundles aren't persisted yet).
        # A wrapper can appear under BOTH its template's key and a replica
        # alias key (replicas map to the template's wrapper at their
        # instance).  Largest-list-first + setdefault makes the TEMPLATE id
        # win, so the persisted parent_id always links instance → template
        # (the loader's expansion-map rebuild groups instances by it).
        expanded_to_template = {}
        for tid, wrappers in sorted((self._hier_expansion_map or {}).items(),
                                    key=lambda kv: -len(kv[1])):
            for ew in wrappers:
                expanded_to_template.setdefault(ew.input.original_bundle.id,
                                                tid)
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
        row.cloned_from = str((getattr(self, "_bu_clone_from", None)
                               or {}).get(hb.id, ""))
        self.bdb.add_bundle(row)                    # is_replicated defaults to False
        for nm in hb.get_net_names():
            self.bdb.add_bundle_net(bid, nm)
        for bt in hb.entry_busterm_ids:
            self.bdb.add_bundle_busterm(bid, bt, "entry")
        for bt in hb.exit_busterm_ids:
            self.bdb.add_bundle_busterm(bid, bt, "exit")
        self._persist_bundle_candidates(w)

    def _persist_bundle_candidates(self, w):
        """(Re)write ONE bundle's candidate topology rows from its in-memory
        pool: deletes any existing rows, then persists every candidate with
        segments + logical annotations, is_selected/is_pinned, and the v15
        source tag (a USER candidate persisted through this path must survive
        the keep_user wipe like any other).  Used by _persist_normal_bundle
        (fresh bundle — the delete is a no-op) and by the post-expansion
        edit_commit for a NORMAL (non-expanded) wrapper, whose planner
        persist only updates selection/layers and would otherwise point
        is_selected at a candidate the BDB never received."""
        import json
        bid = str(w.input.original_bundle.id)
        for tr in self.bdb.topologies(bid):
            self.bdb.delete_topology(bid, tr.cand_index)
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
            tr.is_pinned = bool(w.input.topology_pinned and ci == sel)
            tr.topo_uid = buda.topo_uid(topo)
            tr.source = ("user" if topo.type == "USER" else
                         "dogleg" if self._dogleg_slot.get(
                             w.input.original_bundle.id) == ci else
                         "generated")
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
                sr.edge_id = seg.edge_id
                sr.perp_clamp_lo = seg.perp_clamp_lo
                sr.perp_clamp_hi = seg.perp_clamp_hi
                self.bdb.add_topology_segment(sr)
            # Logical seg-busterm links + TEG-over bridges (load_pipeline
            # restores both; never re-derived from geometry).
            self._persist_topology_annotations(bid, ci, topo)

    def _persist_assigned_layers(self, bid, sel, w):
        """Write the planner's per-segment assigned layers for a selected topology."""
        for seg_index, layer in enumerate(w.plan.seg_layers):
            self.bdb.set_segment_layer(bid, sel, seg_index, int(layer))
