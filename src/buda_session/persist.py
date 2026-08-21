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
import buda_diag

from .util import _batched, apply_pattern_layer_facts


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
        # BUDA_BDB_MATERIALIZE_TO: a launcher's request to give the
        # materialization a DURABLE name instead of a throwaway temp — the
        # `btcl -b` read-only-input flow.  The semantics of the open are
        # unchanged in the one way that matters: the .sql input is still
        # NEVER written (there is no writeback source armed), the pipeline
        # persists into the named file exactly as it persisted into the
        # temp, and the file simply survives the session as a checkpoint.
        # Popped, not read: the request names ONE file, so only the first
        # no-writeback .sql open takes it (the launcher pre-flights the flow
        # and only arms this for a single-open flow).  With `writeback` the
        # open already has a durable home — the .sql itself — so the request
        # is declined loudly rather than silently changing which file is
        # authoritative.  An EXISTING target is opened as-is (a previous
        # session's materialization, pins and routing included); the
        # launcher deletes it first when it wants a fresh copy of the input.
        redirect = os.environ.pop('BUDA_BDB_MATERIALIZE_TO', None)
        if redirect and writeback:
            print("open_bdb: BUDA_BDB_MATERIALIZE_TO ignored -- `writeback` "
                  "already gives this open a durable home (the .sql itself)")
            redirect = None
        if redirect:
            out = os.path.abspath(redirect)
            if os.path.exists(out) and not os.path.isfile(out):
                # A directory here is a caller's typo; materializing "onto"
                # it can only fail confusingly later, and a reuse claim on
                # it would be nonsense.  Fail fast, before any file is
                # touched (the launcher refuses this shape earlier for the
                # same reason — this guards the direct env-var user).
                raise RuntimeError(
                    f"BUDA_BDB_MATERIALIZE_TO names an existing non-file "
                    f"({out}) -- pick a checkpoint filename")
            if os.path.exists(out):
                print(f"open_bdb: reusing the durable materialization {out} "
                      f"of {sql_path} (changes persist there; the .sql input "
                      f"is never written)")
                return out
            parent = os.path.dirname(out)
            if parent and not os.path.isdir(parent):
                os.makedirs(parent, exist_ok=True)
            bdb_serialize.load(sql_path, out)
            print(f"open_bdb: materialized {sql_path} -> {out} (durable: "
                  f"changes persist there; the .sql input is never written)")
            return out
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

    def _persist_wrappers(self):
        """The wrapper list a bundle/topology persist must describe: the
        PRE-expansion view.

        After `run_planner hier`, `self.bundles` is the EXPANDED per-instance
        list (synthetic wrapper ids).  A clear-and-rewrite persist walking it —
        a post-route `select_topology`/`unpin_topology`/`edit_commit`, e.g.
        hdesign.tcl's prompt — replaced the template/replica bundle rows with
        per-instance rows carrying per-instance geometry and no expansion
        marker, and the next session's `load_pipeline` restored those as
        cell-local templates whose blocks are not in any frame (the
        checkpoint-clobber found by flow/tcl/hdesign.tcl).  The pre-expansion
        originals are what a checkpoint means; run_planner's own SELECTIVE
        expanded-row re-persist is separate machinery and unaffected.  Flat
        sessions (and hier sessions before expansion) keep `self.bundles`.
        """
        if (getattr(self, "_planner_is_hier", False)
                and getattr(self, "_hier_bundles_orig", None)):
            return self._hier_bundles_orig
        return self.bundles

    @_batched
    def _persist_bundles(self, strategy):
        """Persist the session's bundles into the open BDB's bundle tables
        (Stage-1 output; pre-expansion view — see _persist_wrappers).

        Flow-agnostic: net membership is stored by name, so the flat flow (whose
        nets may not have rows in the BDB `net` table) persists too. Clears and
        rewrites so re-running the bundler replaces prior rows. No-op (returns 0)
        when no BDB is open. See docs/internal/bdb_test_data.md.
        """
        if self.bdb is None:
            return 0
        import json
        wrappers = self._persist_wrappers()
        # This clear-and-rewrite drops expanded-bundle rows too, so the
        # selective-persist fingerprint memo no longer describes the DB.
        self._persisted_plan_fp = None
        # The per-bundle generation-knob memo (v15) must survive this
        # clear-and-rewrite — it is written OUTSIDE this path (generate_more)
        # and would otherwise be wiped by every re-persist.
        knob_memo = {}
        for _w in wrappers:
            _bid = str(_w.input.original_bundle.id)
            _k = self.bdb.bundle_gen_knobs(_bid)
            if _k:
                knob_memo[_bid] = _k
        # A durable select_topology pin must survive it too: the clear wipes
        # the topology rows carrying is_pinned at BUNDLING time, two commands
        # before the generation tail's _apply_bdb_pins could read them — so
        # the session keeps a (bundle -> pinned uid) memo that MIRRORS the
        # table's pin state.  Initialized from the rows here, once per BDB
        # connection (the cross-session case: rows a previous session
        # wrote); from then on _persist_topologies rebuilds it from the very
        # wrapper state it serializes, so the mirror holds through every
        # in-session pin, unpin and REBUNDLE — the bundler replaces
        # session.bundles with fresh unpinned wrappers before calling here,
        # which is exactly why the rows (mirrored here) are the source, not
        # memory (Codex #758).
        if getattr(self, '_bdb_pin_snap_for', None) != id(self.bdb):
            self._bdb_pin_snap_for = id(self.bdb)
            self._bdb_pin_memo = {}
            for _row in self.bdb.all_bundles():
                _pinned = [tr.topo_uid for tr in self.bdb.topologies(_row.id)
                           if tr.is_pinned]
                if _pinned:
                    self._bdb_pin_memo[_row.id] = _pinned[0]
        # Membership of FK-kept bundles must survive too (audit P3-04):
        # clear_bundles(keep_user) preserves a bundle row still referenced
        # by a kept USER topology, but wipes ALL bundle_net/bundle_busterm
        # rows, and the re-add loop below covers only the CURRENT
        # self.bundles — so a kept bundle absent from this run permanently
        # lost its net membership, reloading as a zero-net, zero-width
        # bundle whose "kept" user routing routes nothing.  Snapshot the
        # absent bundles' membership and rewrite it for the rows the clear
        # actually kept.
        cur_ids = {str(w.input.original_bundle.id) for w in wrappers}
        absent_membership = {}
        for _row in self.bdb.all_bundles():
            if _row.id not in cur_ids:
                absent_membership[_row.id] = (
                    self.bdb.bundle_nets(_row.id),
                    self.bdb.bundle_busterms(_row.id),
                    # v27: and its per-bit endpoints.  They live ON the
                    # bundle_net rows this clear wipes, and the re-add below
                    # would put back bare membership — so a kept fan-in
                    # would come back untapered (BUDA-1904) purely because
                    # some LATER run re-persisted without it.
                    self.bdb.bundle_net_endpoints(_row.id))
        self.bdb.clear_bundles(keep_user=True)
        kept_ids = ({r.id for r in self.bdb.all_bundles()}
                    if absent_membership else set())
        for _bid, (_nets, _bts, _eps) in absent_membership.items():
            if _bid not in kept_ids:
                continue
            for _nm in _nets:
                self.bdb.add_bundle_net(_bid, _nm)
            # Both lists come back in bundle_nets() order — one ORDER BY,
            # written once and shared by the two readers — so zipping them is
            # the same bit alignment the rows were stored under.
            for _nm, (_dp, _rp) in zip(_nets, _eps):
                if _dp:
                    self.bdb.set_bundle_net_endpoints(_bid, _nm, _dp, _rp)
            for _bt, _role in _bts:
                self.bdb.add_bundle_busterm(_bid, _bt, _role)
        for w in wrappers:
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
            # v21: the governing NDR rule — load_pipeline's VOID basis.
            from buda_cmds.ndr_cmds import stamp_bundle_ndr
            stamp_bundle_ndr(self, row, w)
            self.bdb.add_bundle(row)
            names = hb.get_net_names()
            for nm in names:
                self.bdb.add_bundle_net(row.id, nm)
            # v27: the per-bit endpoints of a fan-in / fan-out bundle.  Only
            # these bundles carry them (net_drivers is empty for every
            # other), and without them a resumed session cannot re-derive
            # `Topology::seg_bits` — so its tree comes back UNTAPERED, every
            # segment carrying every bit, wider than the design that was
            # saved and reporting itself perfectly clean.  Stored rather than
            # re-derived at load because the driver/receiver roles come out
            # of a subtle pass in the bundler (deepest OUTPUT, path-maximal
            # receivers, INOUT/UNKNOWN fallbacks, extra-driver attachment)
            # that a second implementation would drift from.
            drvs = list(hb.net_drivers)
            if drvs:
                rcvs = [list(r) for r in hb.net_receivers]
                for i, nm in enumerate(names):
                    if i >= len(drvs):
                        break
                    self.bdb.set_bundle_net_endpoints(
                        row.id, nm, drvs[i],
                        json.dumps(rcvs[i] if i < len(rcvs) else []))
            for bt in hb.entry_busterm_ids:
                self.bdb.add_bundle_busterm(row.id, bt, "entry")
            for bt in hb.exit_busterm_ids:
                self.bdb.add_bundle_busterm(row.id, bt, "exit")
        for _bid, _k in knob_memo.items():
            self.bdb.set_bundle_gen_knobs(_bid, _k)
        return len(wrappers)

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
                # The selective-persist fingerprint memo may have been
                # published inside the discarded body (nested batches defer
                # the real commit past the inner function's return, so a
                # post-call assignment would still be premature — Codex
                # #610 P1); a rolled-back batch means the memo no longer
                # describes the DB, so the next persist must run full.
                self._persisted_plan_fp = None
                self.bdb.rollback_batch()            # body failed → discard
            else:
                # A failed COMMIT leaves the transaction open (begin/commit_batch
                # only advance the depth on SQL success), so roll it back rather
                # than leak a half-open batch into the next command.
                try:
                    self.bdb.commit_batch()
                except BaseException:
                    self._persisted_plan_fp = None   # same contract as above
                    self.bdb.rollback_batch()
                    raise

    def _apply_bdb_pins(self):
        """Re-attach durable `select_topology` pins from the open BDB onto a
        REBUILT candidate pool, by stable content uid.

        `select_topology` writes `topology.is_pinned` through to the BDB at
        once, and `load_pipeline` restores it — but a flow that REBUILDS
        (re-bundles and regenerates rather than resuming) starts from
        unpinned session state, and the generation-tail persist rewrites the
        topology table FROM that state: the durable pin was silently wiped
        by the very re-run it was meant to outlive (the `btcl -i`
        back-to-back case).  So the generation tail calls this right before
        persisting: an unpinned wrapper whose BDB rows carry `is_pinned`
        re-adopts the pin, resolved by `topo_uid` (indices may renumber
        across regenerations, and generation is deterministic, so the same
        candidate carries the same uid).  Precedence matches the sidecar
        baseline: a pin made in THIS session (a script `select_topology`
        before regeneration) wins; a pin whose uid matches no regenerated
        candidate is reported and dropped rather than landing on a
        neighbour.  Single pins only — a `pinned_group` super-candidate pin
        rides BDB meta and stays load_pipeline-restored.  No-op without an
        open BDB, and on any BDB with no pinned rows (every checked-in
        fixture: none persist topology rows at all)."""
        if self.bdb is None or not self.bundles:
            return
        memo = getattr(self, '_bdb_pin_memo', None) or {}
        # Rows are worth querying only while the mirror is UNINITIALIZED for
        # this connection (e.g. a flat flow that bundled BEFORE open_bdb, so
        # no persist has snapshotted yet); once initialized, rows == mirror
        # by invariant and the per-bundle query would be pure waste.
        mirror_fresh = (getattr(self, '_bdb_pin_snap_for', None)
                        == id(self.bdb))
        for w in self.bundles:
            if (getattr(w.input, 'topology_pinned', False)
                    or getattr(w.input, 'pinned_group', [])):
                continue                              # session state wins
            if not w.input.candidates:
                # No pool to restore ONTO (rebundled, not yet regenerated) —
                # neither restored nor dropped: the mirror keeps the entry
                # (see _persist_topologies) for the generation that builds
                # this bundle's pool.
                continue
            bid = str(w.input.original_bundle.id)
            # The re-bundle's clear already wiped the rows, so the memo —
            # the mirror of the table's pin state, maintained by
            # _persist_topologies — is the primary source; live rows are
            # the fallback for a regeneration with no re-bundle.  Read, not
            # popped: the mirror must keep matching the rows, and a
            # re-restore is already prevented by the pinned-wrapper skip
            # above (a dropped pin cannot return either — the drop's own
            # persist rebuilt the mirror without it).
            uid = memo.get(bid)
            if uid is None and not mirror_fresh:
                try:
                    rows = [tr for tr in self.bdb.topologies(bid)
                            if tr.is_pinned]
                except RuntimeError:
                    continue
                uid = rows[0].topo_uid if rows else None
            if uid is None:
                continue
            for ci, cand in enumerate(w.input.candidates):
                if buda.topo_uid(cand) == uid:
                    w.input.topology_pinned = True
                    w.plan.selected_topology_index = ci
                    print(f"[BDB] bundle {bid}: durable pin restored -> "
                          f"topo {ci + 1} ({cand.type})")
                    break
            else:
                print(f"Warning: bundle {bid}: durable pin (uid {uid}) "
                      f"matches no regenerated candidate — pin dropped")

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
        wrappers = self._persist_wrappers()
        for w in wrappers:
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
        for w in wrappers:
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
            self._persist_group_pin(w, bid)
            self._persist_pinned_layers(w, bid)
        # The durable-pin memo mirrors the table, and this method is the
        # table's only writer — so rebuild the mirror from the wrapper state
        # just serialized.  This is what makes an in-session pin survive an
        # in-session rebundle (the pin's persist refreshed the mirror before
        # the bundler wiped memory and rows alike), and what makes an
        # in-session unpin durable (the mirror goes empty WITH the rows, so
        # no later restore can resurrect it).  One asymmetry, on purpose: a
        # wrapper with an EMPTY candidate pool persisted no rows, so it said
        # nothing about its pin either way — its old entry is carried
        # forward, because dropping it would lose a pin merely because a
        # rebundle's persist ran before that bundle's pool was regenerated.
        old_memo = getattr(self, '_bdb_pin_memo', None) or {}
        self._bdb_pin_snap_for = id(self.bdb)
        self._bdb_pin_memo = {}
        for w in wrappers:
            bid = str(w.input.original_bundle.id)
            if getattr(w.input, 'topology_pinned', False):
                sel = w.plan.selected_topology_index
                if 0 <= sel < len(w.input.candidates):
                    self._bdb_pin_memo[bid] = \
                        buda.topo_uid(w.input.candidates[sel])
            elif not w.input.candidates and bid in old_memo:
                self._bdb_pin_memo[bid] = old_memo[bid]
        return n_cands

    def _persist_group_pin(self, w, bid):
        """Persist a super-candidate group pin (`input.pinned_group`) as BDB meta
        `pinned_group:<bid>` = JSON list of the family members' `topo_uid`s.
        Keyed by uid (not index) so a resumed `load_pipeline` maps it back to
        indices even if the candidate pool re-orders.  Always written — an empty
        value clears a stale group pin so an unpinned re-persist doesn't leave
        one behind."""
        import json
        grp = list(getattr(w.input, "pinned_group", []) or [])
        uids = [buda.topo_uid(w.input.candidates[i]) for i in grp
                if 0 <= i < len(w.input.candidates)]
        self.bdb.meta_set(f"pinned_group:{bid}", json.dumps(uids) if uids else "")

    def _persist_pinned_layers(self, w, bid):
        """Persist forced per-segment layers (`input.pinned_seg_layers` — an
        `edit_commit pin` after `edit_set_layer`, or a sidecar layer merge)
        as BDB meta `pinned_layers:<bid>`.  They had NO durable home: the
        RESULT layers persist as `topology_segment.assigned_layer`, but the
        FORCED-ness — "keep these under re-planning" — lived only in the
        sidecar `.json`, so deleting it silently returned layer choice to
        the planner on the next plan-resume.  Meta like `pinned_group`
        (uid-independent, no schema bump); written only for a PINNED wrapper
        (the planner applies forced layers to whatever is selected, so
        restoring them unpinned would force a candidate the user never
        chose), and always written — empty clears a stale entry."""
        import json
        pl = list(getattr(w.input, "pinned_seg_layers", []) or [])
        keep = (pl and any(l != -1 for l in pl)
                and getattr(w.input, "topology_pinned", False))
        self.bdb.meta_set(f"pinned_layers:{bid}",
                          json.dumps(pl) if keep else "")

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
            # 0 <= guard: an NDR shield's NEGATIVE ordinal must never wrap
            # around into a real net name (Python names[-1] would silently
            # persist the last bit's identity onto a shield wire).
            names = bid_to_names.get(bundle_id, [])
            return names[bit] if 0 <= bit < len(names) else ""

        bid_to_shield = {w.input.original_bundle.id: w.input.ndr.shield_net
                         for w in self.bundles if w.input.ndr.active()}

        for ns in self.detailed_result.net_segments:
            r = buda.NetSegRow()
            r.id = str(ns.bundle_id)
            r.seg_idx = ns.seg_idx
            r.bit_index = ns.bit_index
            r.net_name = (bid_to_shield.get(ns.bundle_id, "GND")
                          if ns.is_shield
                          else bit_net(ns.bundle_id, ns.bit_index))
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
            # An NDR shield BOND strap (R6) is keyed by a NEGATIVE to_seg
            # (the strap ordinal — see NetVia in detailed_nuts.h): its far
            # end is a power-grid rail, not a routed segment, and both ends
            # carry the rule's shield net.  bit_net() would return "" for
            # its negative bit_index, so name it the same way the shield
            # NetSegment above is named.
            r.net_name = (bid_to_shield.get(nv.bundle_id, "GND")
                          if nv.to_seg < 0
                          else bit_net(nv.bundle_id, nv.bit_index))
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
        # v27: the per-bit fan-in / fan-out endpoints, in bit order — the
        # input the per-bit taper is derived from (see _retaper_fanin below).
        # Empty for every other bundle, and empty for a pre-v27 checkpoint,
        # which then resumes exactly as it did before this existed.
        eps = self.bdb.bundle_net_endpoints(br.id)
        if eps and any(d for d, _r in eps):
            hb.net_drivers = [d for d, _r in eps]
            hb.net_receivers = [json.loads(r) if r else [] for _d, r in eps]
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
        # was persisted (hier expanded bundles keep their selected topology —
        # plus any per-instance USER candidates — at their original template
        # cand_indices).
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
        # Surface op-log provenance (user_ops meta, written by edit_commit):
        # a USER candidate restores geometrically either way, but the
        # how-it-was-built record is worth a pointer at rehydration time.
        for ci, t in enumerate(cands):
            if t.type == "USER":
                entry = self._user_ops_entry(br.id, buda.topo_uid(t))
                if entry is not None:
                    print(f"  bundle {br.id} candidate {ci + 1} is USER, "
                          f"built from {len(entry.get('ops', []))} op(s) — "
                          f"`dump_user_ops {br.id}` shows them")
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
        # Restore forced per-segment layers (BDB meta, the sidecar-free
        # durable home) onto a PINNED wrapper — the bottom-up locked path
        # above already set its own from the plan and is not overridden.
        if (getattr(w.input, "topology_pinned", False)
                and not getattr(w.hier, "locked", False)
                and not list(getattr(w.input, "pinned_seg_layers", []) or [])):
            rawpl = self.bdb.meta_get(f"pinned_layers:{br.id}", "")
            if rawpl:
                try:
                    pl = [int(x) for x in json.loads(rawpl)]
                except (ValueError, TypeError):
                    pl = []
                if pl:
                    w.input.pinned_seg_layers = pl
        # Restore a super-candidate group pin (BDB meta, uid-keyed): map the
        # persisted family uids back to the reloaded candidates' indices, so a
        # resumed run_planner still refines within the pinned family.
        raw = self.bdb.meta_get(f"pinned_group:{br.id}", "")
        if raw:
            try:
                uids = set(json.loads(raw))
            except (ValueError, TypeError):
                uids = set()
            if uids:
                w.input.pinned_group = [
                    i for i, t in enumerate(w.input.candidates)
                    if buda.topo_uid(t) in uids]
        self._retaper_fanin(w, fp)
        return w

    def _retaper_fanin(self, w, fp):
        """Re-derive the per-bit fan-in taper (`Topology::seg_bits`) on every
        restored candidate.  No-op unless the bundle persisted per-bit
        endpoints (v27) — i.e. unless it is a fan-in / fan-out.

        Without this a resumed fan-in tree is UNTAPERED: `seg_bits` is
        derived at generation and by no load path, and an empty map means
        every segment carries every bit.  The resumed design is still clean
        and still complete — the route comes back via the `FANIN:` reason —
        it is simply WIDER than the one that was saved, and nothing says so.
        Measured on `flow/tcl/array_save.tcl` before this: every plain
        bundle round-tripped bit-for-bit while the two fan-in bundles grew
        16 -> 18 and 32 -> 48 bit-wires, both endpoints reporting 0/0/0.

        The frame is ASKED, not inferred.  A cell-local template names its
        blocks by leaf while everything else uses the stored path, and the
        two restore paths reach here with different frames (the loader's
        resolved one, the bottom-up templates' cell-local one) — so rather
        than re-deriving which case this is, both spellings are offered to
        the floorplan and the one it recognises wins.  If neither does, the
        taper is left underived: conservative full width is what this
        already did, and a WRONG taper would drop a bit's wire.
        """
        hb = w.input.original_bundle
        drvs = list(hb.net_drivers)
        if not drvs:
            # A fan-in whose endpoints were never stored: a checkpoint from
            # before v27.  It resumes exactly as it used to — untapered —
            # which is the silence this whole change is about, so it is
            # counted and reported (BUDA-1904) rather than left to be
            # noticed as a mysteriously wider design.
            if str(hb.reason).startswith(("FANIN:", "FANOUT:")):
                self._retaper_stale = getattr(self, "_retaper_stale", 0) + 1
            return
        if fp is None or not w.input.candidates:
            return
        rcvs = [list(r) for r in hb.net_receivers]
        leaf = lambda p: p.rsplit('/', 1)[-1]            # noqa: E731
        for d_names, r_names in (
                (drvs, rcvs),
                ([leaf(d) for d in drvs], [[leaf(r) for r in rl]
                                           for rl in rcvs])):
            if not all(fp.has_block(d) for d in d_names):
                continue
            cands = w.input.candidates      # pybind copy semantics
            for t in cands:
                buda.derive_fanin_seg_bits(t, fp, d_names, r_names)
            w.input.candidates = cands
            self._retaper_done = getattr(self, "_retaper_done", 0) + 1
            return
        # Endpoints were stored but name neither as-is nor by leaf: the frame
        # is not the one they were written in.  Same outcome as a stale
        # checkpoint (untapered), and worth the same word — silently guessing
        # a mapping could drop a bit's wire.
        self._retaper_stale = getattr(self, "_retaper_stale", 0) + 1

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
        the pre-expansion templates; an expanded instance persists its
        selected topology plus any per-instance USER candidates, so its
        selected index is remapped to the compact in-memory candidate list.

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
        # Per-bit fan-in taper accounting (v27) — see _retaper_fanin.
        self._retaper_done = 0
        self._retaper_stale = 0
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
        # A checkpoint written BEFORE v29 carries no grid rows, so the restore
        # above found nothing to install and this session will re-solve
        # against whatever the stack alone implies — the `def_layer` overhead
        # figure instead of the pattern's own pitch, and no keepouts.  That is
        # the state this whole schema bump exists to end, and the one thing it
        # cannot fix retroactively; so it is SAID, at the moment the session
        # that suffers it is created, rather than surfacing six stages later
        # as `run_detailed_nuts requires a routing grid`.
        #
        # Detected from the DATA, not from the version: opening the BDB
        # MIGRATES it, so `schema_version()` already reads 29 by the time
        # anything here can ask, and a version test could never fire (Codex
        # P2 on #782).  The exact condition is "this checkpoint was
        # DETAIL-routed and carries no track pattern": detailed NUTS cannot
        # run without a grid, so a checkpoint holding `net_segment` rows was
        # certainly routed with one — while a design that legitimately never
        # had a grid holds none of those rows and is not accused of anything.
        if (self.routing_grid is None
                and not self.bdb.track_patterns()
                and any(self.bdb.net_segments(str(w.input.original_bundle.id))
                        for w in bundles)):
            buda_diag.emit(
                "BUDA-1503",
                "this checkpoint holds a routed design but no routing grid "
                "(written before schema v29, which added one): the restored "
                "plan was solved against track patterns and keepouts this "
                "session does not have, so re-solving it here uses the "
                "layer overhead figure and no obstruction.  Re-run the flow "
                "once to write a v29 checkpoint, or declare the patterns "
                "(`def_track_pattern`) before `load_pipeline`.")
        if self._retaper_done:
            print(f"[Resume] re-derived the per-bit fan-in taper on "
                  f"{self._retaper_done} bundle(s)")
        if self._retaper_stale:
            buda_diag.emit(
                "BUDA-1904",
                f"{self._retaper_stale} fan-in/fan-out bundle(s) restored "
                f"without per-bit endpoints (checkpoint written before "
                f"schema v27); they resume UNTAPERED — every segment "
                f"carrying every bit — so the design is wider than the one "
                f"that was saved.  Re-run the bundler and re-checkpoint to "
                f"store them.")
        if expanded:
            # Rebuild the hier bookkeeping the bottom-up machinery needs on
            # resume: the planner ran (this IS the post-expansion view), and
            # the template→instances map comes back from parent_id links.
            # parent_id may point at a REPLICA (pre-fix checkpoints) — walk
            # the chain to the root template so sibling instances group.
            self._planner_is_hier = True
            # Inherited-uid registry for _add_expanded_bundle on this RESUMED
            # session: a restored expanded pool contains only the selected
            # row + instance-local USER extras, so every non-USER row counts
            # as template-inherited — a later re-persist (post-resume
            # edit_commit) then keeps the extras instead of conservatively
            # dropping them.  (An ex-selected template-replicated USER
            # re-persisting as an extra after a pin-away is the accepted
            # conservative corner — nothing is lost, growth stays bounded.)
            self._inherited_uids = getattr(self, "_inherited_uids", {})
            for br in rows:
                if getattr(br, "is_expanded", False):
                    self._inherited_uids[int(br.id)] = {
                        tr.topo_uid for tr in self.bdb.topologies(br.id)
                        if tr.source != "user"}
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

        # Per-cell layer policies (v20): restore from the BDB, resolve the
        # masks onto the restored wrappers, then audit the restored plan
        # against them — a cap declared/tightened AFTER the checkpoint was
        # routed voids the violating bundles' restored selection (LOUD),
        # never silently keeps illegal metal (hier_layer_caps.md §9.5).
        self._restore_layer_policies()
        self._apply_layer_policies()
        cap_voided = self._audit_restored_layer_caps()

        # NDR rules + scopes (v21): restore, re-resolve specs onto the
        # restored wrappers, and VOID any restored plan whose governing rule
        # changed since the checkpoint — the plan was priced under a
        # different demand (ndr_architecture.md §4; the same LOUD
        # re-plan-required semantics as the cap audit above).
        from buda_cmds import ndr_cmds
        ndr_cmds.restore_ndr_from_bdb(self)
        ndr_voided = ndr_cmds.audit_restored_ndr(
            self, {int(b.id): b.ndr_rule for b in rows
                   if str(b.id).lstrip("-").isdigit()})

        # Rehydrate the abstract-NUTS result (if run_nuts was persisted) so
        # run_detailed_nuts can resume from it.  A cap- or NDR-voided
        # bundle's persisted routing is equally illegal — leave it out.
        ts_list = []
        for w in self.bundles:
            if int(w.input.original_bundle.id) in cap_voided \
                    or int(w.input.original_bundle.id) in ndr_voided:
                continue
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
        elif (cap_voided or ndr_voided) and self.nuts_result is not None:
            # Every restored bundle with routing was voided: a nuts_result
            # left over from earlier in THIS session is stale (potentially
            # illegal) metal the audits just excluded — clearing it keeps
            # run_detailed_nuts from consuming it (Codex #546).
            self.nuts_result = None
            print("[NDR/LayerCap] cleared the session's previous NUTS result "
                  "— the audits voided every restored routed bundle")

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

    def _planner_persist_fp(self, w):
        """Cheap content fingerprint of what _add_expanded_bundle would write
        for this wrapper: the selected candidate's identity, the assigned
        layers, the lock flag, and (only when the instance CAN carry
        instance-local USER extras — an `_inherited_uids` entry exists) the
        extra USER-candidate uids, and the governing NDR rule.

        Equal fingerprints ⟺ identical persisted rows, so the selective
        re-persist may skip the bundle.  The NDR stamp is part of the ROW,
        so it has to be part of the fingerprint: without it a re-persist
        after a rule change would leave the old stamp in place and the next
        resume would audit against a rule the design no longer has."""
        from buda_cmds.ndr_cmds import bundle_ndr_stamp
        uid, _b = buda.selected_topo_key(w)
        extras = ()
        inherited = getattr(self, "_inherited_uids", {}) \
            .get(w.input.original_bundle.id)
        if inherited is not None:
            extras = tuple(sorted(
                buda.topo_uid(t) for t in w.input.candidates
                if t.type == "USER" and buda.topo_uid(t) not in inherited))
        return (uid, tuple(w.plan.seg_layers), bool(w.hier.locked), extras,
                bundle_ndr_stamp(self, w),
                bool(getattr(w.input, "topology_pinned", False)),
                tuple(getattr(w.input, "pinned_seg_layers", []) or ()))

    @_batched
    def _persist_planner_output(self, selective=False):
        """Persist the planner's decision into the BDB after run_planner.

        For every current wrapper: record the selected topology (`is_selected`) and
        the per-segment assigned layers (`topology_segment.assigned_layer`). Wrappers
        whose id already has BDB rows (flat bundles, hier cross-block / pass-through)
        are UPDATED in place; hier's expanded per-instance wrappers (synthetic ids)
        are ADDED as `is_replicated=1` bundle rows (parent_id = template) with
        their selected topology plus any per-instance USER candidates
        (TopoEdit follow-on #3), so `bus_segment` rows join back to a bundle. No-op
        without an open BDB. See docs/internal/wishlist-bdb.md.

        `selective=True` (the RE-persist sites: the run_nuts escalation
        re-persist, _checkpoint_routing, the FK fallbacks) rewrites only the
        expanded bundles whose fingerprint changed since the last persist —
        the chip-scale profile showed each re-persist rewriting all 560
        expanded instances when a handful changed
        (docs/internal/chip_flow_parallelism.md C1).  It engages only when
        the expanded-bundle id set matches the previous persist exactly
        (else the full clear+rewrite runs, exactly the historical
        behavior); the run_planner command itself always persists fully — a
        fresh plan is a semantic reset.  Either path leaves the BDB
        byte-identical to a full rewrite (the fingerprint covers every
        row-shaping input).
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
        # Fingerprint the expanded wrappers that WILL be persisted (valid
        # selection only — mirrors the loop's guard below).
        fp_now, exp_wrappers = {}, []
        for w in self.bundles:
            hbid = w.input.original_bundle.id
            if hbid not in expanded_to_template:
                continue
            sel = w.plan.selected_topology_index
            if sel < 0 or sel >= len(w.input.candidates):
                continue
            fp_now[hbid] = self._planner_persist_fp(w)
            exp_wrappers.append(w)
        memo = getattr(self, '_persisted_plan_fp', None)
        use_selective = (selective and memo is not None
                         and set(memo) == set(fp_now))
        if not use_selective:
            self.bdb.clear_expanded_bundles()      # idempotent re-plan
        original_ids = {b.id for b in self.bdb.all_bundles()}
        # Busterm-row dedup across THIS pass (same contract as the
        # generation-time persist): 'tb:' ids are geometry-fingerprinted, so
        # a busterm shared by many expanded instances' topologies (e.g. the
        # top-level blocks every cross-hierarchy bundle taps) writes its wide
        # JSON row once; every candidate still writes its cheap link rows.
        seen_busterms = set()
        n = 0
        for w in self.bundles:
            sel = w.plan.selected_topology_index
            if sel < 0 or sel >= len(w.input.candidates):
                continue
            hbid = w.input.original_bundle.id
            bid = str(hbid)
            if hbid in expanded_to_template:        # genuine hier expanded instance
                if use_selective and fp_now[hbid] == memo[hbid]:
                    n += 1                          # rows already identical
                    continue
                if use_selective:
                    self.bdb.clear_expanded_bundle(bid)
                self._add_expanded_bundle(w, sel, expanded_to_template,
                                          seen_busterms)
                self._persist_pinned_layers(w, bid)
            else:                                   # normal bundle (flat / cross-block)
                if bid not in original_ids:         # not persisted yet → persist fully
                    self._persist_normal_bundle(w)
                ci = self._selected_bdb_cand_index(bid, w, sel)
                self.bdb.set_topology_selected(bid, ci)
                # A pin applied AT the planner (a sidecar selection —
                # _apply_selections runs inside run_planner) used to persist
                # only as is_selected: below-plan resumes reproduced the
                # route, but a plan-resume without the sidecar silently
                # re-decided.  Refresh is_pinned from the wrapper, and give
                # the forced layers their durable home too.
                self.bdb.set_topology_pinned(
                    bid, ci if getattr(w.input, "topology_pinned", False)
                    else -1)
                self._persist_pinned_layers(w, bid)
                self.bdb.reset_assigned_layers(bid)  # drop stale layers from a prior plan
                self._persist_assigned_layers(bid, ci, w)
            n += 1
        self._persisted_plan_fp = fp_now
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
        # v21: the governing NDR rule — load_pipeline's VOID basis.
        from buda_cmds.ndr_cmds import stamp_bundle_ndr
        stamp_bundle_ndr(self, row, w)
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
        # A live group pin (e.g. restored from the sidecar before the BDB was
        # opened) must be checkpointed on THIS first-persist path too, or a
        # later load_pipeline resumes without the group constraint (Codex).
        self._persist_group_pin(w, bid)
        self._persist_pinned_layers(w, bid)

    def _selected_bdb_cand_index(self, bid, w, sel):
        """BDB cand_index of the wrapper's selected candidate, resolved by
        stable content uid (audit P3-01): on a load_pipeline-resumed session
        the compact in-memory index diverges from the persisted cand_index
        whenever the topology table has holes — which the keep_user
        renumbering itself creates (a kept USER row at ci >= n_new stays
        put when a later session's pool shrinks).  Writing the compact
        index then marked the WRONG persisted row — or none at all — as
        selected and parked the layer assignments on it, silently dropping
        a pinned USER selection from the checkpoint.  The loader already
        models this divergence (sel vs sel_ci); this is the persist-side
        half.  Falls back to the compact index when no persisted row
        carries the uid (fresh pool just persisted in compact order)."""
        try:
            uid = buda.topo_uid(w.input.candidates[sel])
        except Exception:
            return sel
        for tr in self.bdb.topologies(bid):
            if tr.topo_uid == uid:
                return tr.cand_index
        return sel

    def _persist_assigned_layers(self, bid, sel, w):
        """Write the planner's per-segment assigned layers for a selected topology
        (`sel` here is the BDB cand_index — see _selected_bdb_cand_index)."""
        for seg_index, layer in enumerate(w.plan.seg_layers):
            self.bdb.set_segment_layer(bid, sel, seg_index, int(layer))

    # ── routing-grid persistence (v29) ────────────────────────────────────
    #
    # The grid and the keepouts were the last physical-design facts with no
    # table: pure session state, rebuilt by whoever declared them.  When that
    # "whoever" is `import_def_lef`, a hier resume loses them — it HOLDS the
    # import (a replayed one is a duplicate-instance error) — and the result
    # is not merely that `run_detailed_nuts` refuses for want of a grid.  That
    # refusal is the only loud moment: `run_nuts` and the healers run on
    # before it, against a coarser, blockage-free model than the checkpoint
    # was routed under (measured on flow/ariane133: the build reports 20
    # keepout-seated segments, the resume 18).
    #
    # What is written is the DECLARATION, not a read-back of the built grid —
    # origin, slots, bounds, region — so a restore replays `define_layer` /
    # `add_override` / `add_keepout` verbatim.  Recording at the declaration
    # site also means the C++ grid needs no new getters: every install site
    # already holds the values.

    @staticmethod
    def _slots_json(pattern):
        """A TrackPattern's slot list as the stored JSON array."""
        import json
        return json.dumps([{"t": s.type, "l": s.label,
                            "w": s.width, "s": s.space_after}
                           for s in pattern.slots])

    @staticmethod
    def _slots_from_json(text):
        import json
        return [buda.TrackSlot(type=d["t"], label=d["l"],
                               width=d["w"], space_after=d["s"])
                for d in (json.loads(text) if text else [])]

    def _persist_track_pattern(self, layer_id, pattern, is_horiz, source):
        """Write through one `define_layer` declaration.  No BDB = no-op, so
        every install site can call it unconditionally."""
        if self.bdb is None:
            return
        row = buda.TrackPatternRow()
        row.layer_id = int(layer_id)
        row.origin   = float(pattern.origin)
        row.is_horiz = 1 if is_horiz else 0
        row.bounded  = 1 if pattern.bounded else 0
        row.bound_lo = float(pattern.bound_lo)
        row.bound_hi = float(pattern.bound_hi)
        row.source   = source
        row.slots    = self._slots_json(pattern)
        self.bdb.set_track_pattern(row)

    def _persist_grid_override(self, layer_id, x1, y1, x2, y2, pattern):
        if self.bdb is None:
            return
        row = buda.GridOverrideRow()
        row.layer_id = int(layer_id)
        row.x1, row.y1, row.x2, row.y2 = int(x1), int(y1), int(x2), int(y2)
        row.origin = float(pattern.origin)
        row.slots  = self._slots_json(pattern)
        self.bdb.set_grid_override(row)

    def _persist_keepouts(self, zones):
        """Write through a burst of keepout declarations.

        `zones` is [(x1,y1,x2,y2, [layer_ids], inside_block, net)] — the zone
        as declared, layer set included, because a zone is the object
        `set_keepout_loci` reasons about; one row per (zone, layer) would
        lose it.  `net` rides along for the same reason the importer carries
        it rather than re-splitting a provenance string: a power strap's
        identity has to survive intact.  One call per burst so the 13k a DEF
        import declares are one transaction rather than 13k."""
        if self.bdb is None or not zones:
            return
        rows = []
        for x1, y1, x2, y2, lids, inside, net in zones:
            r = buda.KeepoutRow()
            r.x1, r.y1, r.x2, r.y2 = int(x1), int(y1), int(x2), int(y2)
            r.layers = ",".join(str(int(l)) for l in sorted(lids))
            r.inside_block = 1 if inside else 0
            r.net = net or ""
            rows.append(r)
        self.bdb.add_keepouts(rows)

    def _restore_grid_from_bdb(self):
        """Rebuild the routing grid and the keepouts from the open BDB.

        Called from `open_bdb`, beside the layer-policy and NDR restores that
        already work this way.  Precedence follows the rule the LEF/DEF
        importers already obey — an explicit `def_track_pattern` outranks
        imported data in EITHER order — which is why `source` is stored: a
        pattern alone cannot say who declared it.  A layer this session has
        already declared is left alone; a layer declared LATER replaces what
        was restored, and `_pattern_restored` is what lets it, since the
        duplicate-declaration error must fire for a second declaration in
        ONE session and not for a re-declaration of a checkpoint's own."""
        if self.bdb is None:
            return
        pats = self.bdb.track_patterns()
        ovrs = self.bdb.grid_overrides()
        zones = self.bdb.keepouts()
        if not pats and not ovrs and not zones:
            return
        restored_layers = []
        for r in pats:
            lid = int(r.layer_id)
            if self._pattern_source.get(lid) == "script":
                continue                    # this session already declared it
            pat = buda.TrackPattern(origin=r.origin,
                                    slots=self._slots_from_json(r.slots))
            if r.bounded:
                pat.set_bounds(r.bound_lo, r.bound_hi)
            if self.routing_grid is None:
                self.routing_grid = buda.RoutingGridStack()
            self.routing_grid.define_layer(lid, pat, bool(r.is_horiz))
            self._pattern_source[lid] = r.source or "script"
            self._pattern_restored.add(lid)
            # The derived layer facts the pattern feeds — without these the
            # width model falls back to the def_layer overhead figure, which
            # is the silent half of the resume divergence.
            apply_pattern_layer_facts(self.layers, lid, pat)
            restored_layers.append(lid)
        for r in ovrs:
            if self.routing_grid is None or not self.routing_grid.has_layer(r.layer_id):
                continue
            pat = buda.TrackPattern(origin=r.origin,
                                    slots=self._slots_from_json(r.slots))
            self.routing_grid.add_override(r.layer_id, r.x1, r.y1, r.x2, r.y2, pat)
        n_zones = 0
        for r in zones:
            lids = [int(t) for t in r.layers.split(",") if t]
            self.fp.add_keepout_zone(r.x1, r.y1, r.x2, r.y2, lids,
                                     bool(r.inside_block), r.net)
            if self.routing_grid is not None:
                for lid in lids:
                    if self.routing_grid.has_layer(lid):
                        # WITH the net: a resumed session must see the same
                        # rails the build did, or NDR credit and bond answers
                        # differ between a design and its own checkpoint
                        # (Codex #785).
                        self.routing_grid.add_keepout(lid, r.x1, r.y1,
                                                      r.x2, r.y2, r.net)
            n_zones += 1
        if restored_layers or n_zones:
            parts = []
            if restored_layers:
                parts.append(f"{len(restored_layers)} track pattern(s) "
                             f"(layers {','.join(map(str, restored_layers))})")
            if ovrs:
                parts.append(f"{len(ovrs)} region override(s)")
            if n_zones:
                parts.append(f"{n_zones} keepout(s)")
            print(f"[open_bdb] restored the routing grid: {', '.join(parts)}")
