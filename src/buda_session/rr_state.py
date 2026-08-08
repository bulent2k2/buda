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

"""Healer trial state: snapshot / restore / dirty-tracking (Phase D-1).

Extracted VERBATIM from RipupMixin (risk_reduction_plan.md R3 — carve at
the bug seam first): every historical restore bug was "the snapshot
didn't cover X", so this module now owns the coverage contract:

- `_rr_snapshot` captures, per wrapper, exactly the fields a trial can
  mutate — the tuple in its 'wrap' dict — plus the result refs
  (nuts/dnuts), the dogleg bookkeeping, and a deep copy of any adopted
  dogleg slot's Topology (dl_cand).
- `_rr_restore(snap, only=...)` writes them back; `only` restricts the
  wrapper rewrite to the bundles a trial dirtied (`self._rr_dirty`, set
  by the trial machinery in ripup.py).
- The CHECKED contract lives in
  test/tests/test_wrapper_invariants.py::test_snapshot_coverage_contract:
  every writable wrapper field must be classified snapshotted (this
  module captures it) or exempt-with-reason.  Adding a bound field
  without deciding its snapshot fate fails that test — change BOTH
  together.

Timing is booked through self._rr_t_add ('snapshot'/'restore'), defined
in RipupMixin; the two mixins share state via self, member sets disjoint
by construction like the rest of buda_session.
"""

import copy
import time


class RRStateMixin:
    def _rr_wrapper(self, bid):
        for w in self.bundles:
            if w.input.original_bundle.id == bid:
                return w
        return None

    def _rr_snapshot(self):
        """Capture the state a trial mutates so it can be fully restored.

        Besides selection/pin/candidate-count, capture every wrapper's plan
        assignment arrays (seg_layers/seg_perp/assigned_*) and the dogleg
        per-segment overrides (seg_net_pull/seg_slide_*): an incremental trial
        (replan_bundle) mutates only its target's arrays, and restoring them
        exactly means a rejected trial leaves NO divergence between the live
        plan and the restored result refs — the `dirty` full rebuild is then
        needed only for legacy full-replan trials."""
        t0 = time.perf_counter()
        snap = {
            'wrap': {w.input.original_bundle.id:
                     (w.plan.selected_topology_index, w.input.topology_pinned,
                      len(w.input.candidates),
                      list(w.plan.seg_layers), list(w.plan.seg_perp),
                      list(w.plan.seg_net_pull),
                      list(w.plan.seg_slide_lo), list(w.plan.seg_slide_hi),
                      w.input.assigned_v_layer, w.input.assigned_h_layer,
                      list(w.input.pinned_seg_layers))
                     for w in self.bundles},
            'nuts': self.nuts_result,
            'dnuts': self.detailed_result,
            'dl_slot': dict(self._dogleg_slot),
            'dl_orig': dict(self._dogleg_originals),
            'dl_cand': {},
        }
        # An adopted dogleg's split candidate is OVERWRITTEN in place when a
        # trial's NUTS re-solves the same bundle's cycle (cands[slot] = ...,
        # _adopt_doglegs) — the candidate COUNT doesn't change, so the trim
        # below can't undo it.  Capture a REAL copy of the slot's Topology
        # (copy.copy → the __copy__ binding): element access on the bound
        # vector hands back a reference into its storage, which DANGLES if
        # a trial's size-changing reassignment reallocates the vector — the
        # previous direct capture survived only while size-preserving
        # overwrites reused the storage (#286 retrospective follow-up).
        for bid, slot in self._dogleg_slot.items():
            w = self._rr_wrapper(bid)
            if w is not None and 0 <= slot < len(w.input.candidates):
                snap['dl_cand'][bid] = copy.copy(w.input.candidates[slot])
        self._rr_t_add('snapshot', time.perf_counter() - t0)
        return snap

    def _rr_restore(self, snap, only=None):
        """Restore trial-mutated state from a snapshot.

        `only` (a set of bundle ids) restricts the wrapper rewrite to the
        bundles a trial actually dirtied — the incremental replan mutates
        just its target (+ any dogleg-adopted bundles), so rewriting all
        ~N wrappers' pybind arrays per rejected trial is redundant work.
        The result refs and dogleg bookkeeping are always restored.
        `only=None` = full restore (legacy full-replan trials, negotiate)."""
        t0 = time.perf_counter()
        for w in self.bundles:
            bid = w.input.original_bundle.id
            if only is not None and bid not in only:
                continue
            cap = snap['wrap'].get(bid)
            if cap is None:
                continue
            (sel, pinned, ncand, seg_layers, seg_perp,
             seg_net_pull, seg_slide_lo, seg_slide_hi, av, ah,
             pinned_layers) = cap
            cands = w.input.candidates
            # RE-GROW a pool a rejected full-replan-fallback trial SHRANK:
            # its _reset_doglegs deleted the adopted split candidate, and a
            # trim-only restore would put selected_topology_index back onto
            # an out-of-range slot — the bundle would silently vanish from
            # opens/recharges (#286 retrospective item 1).  The only deleter
            # is _reset_doglegs and the only deletable slot is the adopted
            # one, whose content the snapshot holds in dl_cand — re-insert
            # it at its slot before the trim.
            if len(cands) < ncand:
                slot = snap['dl_slot'].get(bid)
                topo = snap['dl_cand'].get(bid)
                if slot is not None and topo is not None \
                        and 0 <= slot <= len(cands):
                    # INVARIANT (shared with _adopt_doglegs, which appends —
                    # "never overwrite the original in place" — so the
                    # adopted slot is always the LAST index): end-deletion
                    # shifts nothing and re-adoption re-appends to the same
                    # position, which is what makes this insert and the
                    # dl_cand overwrite below exact.  Mid-pool adoption
                    # would silently mis-place both restores — trip loudly
                    # instead.
                    assert slot == len(cands), \
                        f"dogleg slot {slot} is not the end slot " \
                        f"({len(cands)}) — _adopt_doglegs no longer appends?"
                    cands.insert(slot, topo)
                    w.input.candidates = cands
                    cands = w.input.candidates
            while len(cands) > ncand:        # drop dogleg-appended candidates
                del cands[len(cands) - 1]
            w.input.candidates = cands
            w.plan.selected_topology_index = sel
            w.input.topology_pinned = pinned
            w.plan.seg_layers = seg_layers
            w.plan.seg_perp = seg_perp
            w.plan.seg_net_pull = seg_net_pull
            w.plan.seg_slide_lo = seg_slide_lo
            w.plan.seg_slide_hi = seg_slide_hi
            w.input.assigned_v_layer = av
            w.input.assigned_h_layer = ah
            w.input.pinned_seg_layers = pinned_layers
        # Put back the committed split geometry where a trial's re-solve
        # overwrote an adopted dogleg's slot in place (same count, new content
        # — the ncand trim above cannot see it).
        for bid, topo in snap['dl_cand'].items():
            if only is not None and bid not in only:
                continue
            slot = snap['dl_slot'].get(bid)
            w = self._rr_wrapper(bid)
            if w is None or slot is None:
                continue
            cands = w.input.candidates
            if 0 <= slot < len(cands):
                cands[slot] = topo
                w.input.candidates = cands
        self.nuts_result = snap['nuts']
        self.detailed_result = snap['dnuts']
        self._dogleg_slot = dict(snap['dl_slot'])
        self._dogleg_originals = dict(snap['dl_orig'])
        self._rr_t_add('restore', time.perf_counter() - t0)

