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

"""TopoEdit sessions, candidate provenance, and selection state.

The edit_* command engine hooks (session lifecycle, verdict reporting,
dogleg adoption/reset), the generate_more knob memo, user-candidate
protection helpers, plan-reset-for-regeneration, and single-topology
selection.

Methods extracted verbatim from buda_cli.BudaSession (the CLI mixin
split); bodies unchanged — `self` is the composed BudaSession, so
cross-mixin helper calls resolve through the class as before.
"""
import buda


class EditMixin:

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

    def _edit_layers(self):
        """Default H/V layer ids for TopoEdit ops (same resolution as
        _make_topo_gen: the stack's TOP layers, falling back to M4/M5)."""
        h = self.layers.get_top_layer(buda.LayerDir.HORIZONTAL)
        v = self.layers.get_top_layer(buda.LayerDir.VERTICAL)
        return (h if h != -1 else 4), (v if v != -1 else 5)

    def _edit_report(self, v):
        """Print one edit op's verdict (the transaction's immediate feedback)."""
        if not v.applied:
            print(f"  [edit] REJECTED: {v.note}")
            return
        kinds = {}
        for viol in v.conn.violations:
            k = str(viol.kind).split('.')[-1]
            kinds[k] = kinds.get(k, 0) + 1
        issues = ", ".join(f"{k}x{n}" for k, n in sorted(kinds.items())) or "none"
        tag = "  << clean" if v.ok() else ""
        seg = f" (seg {v.seg_idx})" if v.seg_idx >= 0 else ""
        print(f"  [edit] {v.note}{seg} — violations: {issues}; "
              f"components={v.components}; pinched={'yes' if v.pinched else 'no'}{tag}")

    def _edit_session(self):
        """The open edit session's (wrapper, topo), or None with an error."""
        if self._edit_topo is None:
            print("Error: no edit session — run edit_topology <bundle_id> [<cand#>|new] first")
            return None
        return self._edit_w, self._edit_topo

    def _resort_pool_preserving_selection(self, w, pool):
        """Sort `pool` by generation's key (wirelength, then type — the same
        annotate_and_sort uses) and remap the candidate-index references that must
        follow their candidate: the selection (selected_topology_index) and the
        dogleg slot/original.  Per-segment plan arrays ride the SELECTED candidate,
        not a candidate index, so they need no remap.  Shared by
        generate_more_topologies and the knob-memo replay (_apply_gen_knobs) so both
        leave the pool consistently WL-ranked.  Returns the sorted list (the caller
        assigns it to w.input.candidates).  The dogleg dicts are keyed by the
        INTEGER original_bundle.id — matching _adopt_doglegs/_reset_doglegs."""
        bid = w.input.original_bundle.id      # int key (dogleg dicts); NOT str

        def _uid_at(idx):
            return buda.topo_uid(pool[idx]) if 0 <= idx < len(pool) else None
        sel_uid = _uid_at(w.plan.selected_topology_index)
        dg_uid = _uid_at(self._dogleg_slot.get(bid, -1))
        og_uid = _uid_at(self._dogleg_originals.get(bid, -1))
        pool.sort(key=lambda c: (c.estimated_wirelength, c.type))
        posn = {buda.topo_uid(c): i for i, c in enumerate(pool)}
        if sel_uid is not None:
            w.plan.selected_topology_index = posn[sel_uid]
        if dg_uid is not None:
            self._dogleg_slot[bid] = posn[dg_uid]
        if og_uid is not None:
            self._dogleg_originals[bid] = posn[og_uid]
        return pool

    def _merge_more_candidates(self, w, fresh):
        """ADDITIVE install of a generation result: append the candidates of
        `fresh` whose stable content uid (`topo_uid`) is not already in w's
        pool, then re-sort the merged pool WL-ranked with the selection and
        dogleg references remapped (`_resort_pool_preserving_selection`).
        Existing candidates, the pin, and plan state are untouched — the
        accretion contract of generate_more_topologies, shared by its flat
        and hier paths and by the knob-memo replays.  Returns
        (n_added, n_duplicates)."""
        existing = list(w.input.candidates)
        seen = {buda.topo_uid(c) for c in existing}
        added = 0
        for c in fresh:
            uid = buda.topo_uid(c)
            if uid in seen:
                continue
            seen.add(uid)
            existing.append(c)
            added += 1
        w.input.candidates = self._resort_pool_preserving_selection(
            w, existing)
        return added, len(fresh) - added

    def _apply_gen_knobs(self, w, src, dsts, old_pin_uid=None):
        """Honor the bundle's persisted generation-knob memo (v15): re-run the
        knob-configured generator additively after a bulk regeneration, so a
        pool accreted with generate_more_topologies does not silently revert.
        Re-attempts the uid pin reattach among the appended extras."""
        if self.bdb is None:
            return
        knobs = self.bdb.bundle_gen_knobs(str(w.input.original_bundle.id))
        if not knobs:
            return
        ks = set(knobs.split())
        tg = self._make_topo_gen(self.fp, "center_mode" in ks,
                                 "double_detour" in ks, "multi_trunk" in ks)
        pool = list(w.input.candidates)
        seen = {buda.topo_uid(c) for c in pool}
        added = 0
        for c in tg.generate_candidates(src, dsts):
            uid = buda.topo_uid(c)
            if uid not in seen:
                seen.add(uid)
                pool.append(c)
                added += 1
        if added:
            # Re-sort so the resumed pool matches the WL ranking generate_more_
            # topologies produced (else it reverts to base + unsorted knob tail);
            # remaps a live pin + dogleg refs.  The uid re-attach below then finds
            # a pin that was LOST during regeneration in the already-sorted pool.
            pool = self._resort_pool_preserving_selection(w, pool)
            w.input.candidates = pool
            print(f"  Re-applied knob memo '{knobs}' for bundle "
                  f"{w.input.original_bundle.id}: +{added} candidate(s).")
            if old_pin_uid and not w.input.topology_pinned:
                for i, c in enumerate(pool):
                    if buda.topo_uid(c) == old_pin_uid:
                        w.plan.selected_topology_index = i
                        w.input.topology_pinned = True
                        print(f"  Pin re-attached by topo_uid to candidate {i + 1}.")
                        break

    def _user_candidates(self, w):
        """Deep copies of w's hand-committed (type USER) candidates, captured
        BEFORE a regeneration replaces the candidate vector — the pybind list
        elements alias the vector's storage, so the copies must be taken while
        it is still alive (Phase E4: user candidates survive regeneration)."""
        return [buda.offset_topology(c, 0, 0)
                for c in w.input.candidates if c.type == "USER"]

    def _pinned_uid(self, w):
        """The stable content uid (buda.topo_uid) of w's pinned candidate, or
        None when unpinned/out of range.  Captured BEFORE a regeneration
        replaces the candidate list, so _reset_plan_for_regen can re-attach
        the pin by identity (Phase E1b of topo_conn_unification.md)."""
        sel = w.plan.selected_topology_index
        if (getattr(w.input, 'topology_pinned', False)
                and 0 <= sel < len(w.input.candidates)):
            return buda.topo_uid(w.input.candidates[sel])
        return None

    def _reset_plan_for_regen(self, w, old_pin_uid=None, kept_user=None):
        """Reset one wrapper to the pristine 'candidates generated, not yet planned'
        state after its candidate list was regenerated.  A prior plan's
        selected_topology_index and per-segment overrides are indexed into the OLD
        candidate list; after regeneration they are stale, and a dogleg may have left
        selected_topology_index pointing at an appended split the fresh list no longer
        has — optimize_topologies would then dereference an out-of-range candidate
        (ValueError: vector).  Also drop this bundle's dogleg bookkeeping so a later
        _adopt_doglegs cannot overwrite/restore a slot that no longer exists.

        Pin survival (Phase E1b): indices are meaningless across lists, but
        IDENTITY is not — when the regenerated list contains a candidate with
        the same stable content uid as the previously pinned one (captured by
        _pinned_uid before the swap), the pin re-attaches to it, so a user's
        selection survives a knob-tweaked regeneration.  Per-segment overrides
        stay dropped either way (they may reference the old plan's layers)."""
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
        # Phase E4: hand-committed candidates survive the regeneration —
        # re-append them (uid-deduped against the fresh list) BEFORE the pin
        # reattach below, so a pin on a user candidate re-attaches too.
        if kept_user:
            pool = list(w.input.candidates)
            seen = {buda.topo_uid(c) for c in pool}
            readded = 0
            for c in kept_user:
                if buda.topo_uid(c) not in seen:
                    pool.append(c)
                    readded += 1
            if readded:
                w.input.candidates = pool
                print(f"  Kept {readded} user candidate(s) of bundle {bid} "
                      f"across the regeneration.")
        if old_pin_uid:
            for i, c in enumerate(w.input.candidates):
                if buda.topo_uid(c) == old_pin_uid:
                    w.plan.selected_topology_index = i
                    w.input.topology_pinned = True
                    print(f"  Pin re-attached by topo_uid: bundle {bid} -> "
                          f"topology {i + 1} ({c.type})")
                    break

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
