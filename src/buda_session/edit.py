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
import os

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
            # LOAD-BEARING: appending means the adopted slot is always the LAST
            # index — ripup's _rr_restore depends on it (its re-grow insert and
            # dl_cand overwrite are exact only for an end slot, asserted there).
            # Do not change adoption to mid-pool insertion without updating both.
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
        """Print one edit op's verdict (the transaction's immediate feedback).
        Returns v.applied so handlers can chain bookkeeping — op-log
        recording, staged-window remaps — on success (GUI _edit_apply
        parity)."""
        if not v.applied:
            print(f"  [edit] REJECTED: {v.note}")
            return False
        kinds = {}
        for viol in v.conn.violations:
            k = str(viol.kind).split('.')[-1]
            kinds[k] = kinds.get(k, 0) + 1
        issues = ", ".join(f"{k}x{n}" for k, n in sorted(kinds.items())) or "none"
        tag = "  << clean" if v.ok() else ""
        seg = f" (seg {v.seg_idx})" if v.seg_idx >= 0 else ""
        print(f"  [edit] {v.note}{seg} — violations: {issues}; "
              f"components={v.components}; pinched={'yes' if v.pinched else 'no'}{tag}")
        return True

    def _edit_record(self, op):
        """Record one APPLIED edit op (its `.buda` command line) into the CLI
        session's op-log — the same design-intent record the GUI's
        _edit_log_op keeps; edit_commit stores it as BDB meta provenance.

        Strip an inline `# comment` first (same rule as the CLI parser): the
        handlers receive the RAW cmd_line, so a commented demo line
        (`edit_set_span 0 75 210  # trim`) would otherwise bake the note into
        the persisted op-log and its `dump_user_ops` replay."""
        if self._edit_ops is None:
            return
        for i, ch in enumerate(op):
            if ch == '#' and (i == 0 or op[i - 1].isspace()):
                op = op[:i]
                break
        op = op.strip()
        if op:
            self._edit_ops.append(op)

    def _record_user_ops(self, bundle_id, uid, base, ops):
        """Store a committed USER candidate's op-log as BDB meta provenance:
        `user_ops:<bundle_id>:<topo_uid>` -> {"base": <source uid|'new'>,
        "ops": [...]} — the human-readable how-it-was-built record beside the
        geometric topology rows (which restore never needs it for).  Rides
        the key-value meta table, so no schema bump; shared by the CLI
        edit_commit and the explorer's commit (via the user_ops_sink hook).
        No-op without an open BDB or an empty op-log."""
        import json
        if self.bdb is None or not ops:
            return False
        self.bdb.meta_set(f"user_ops:{bundle_id}:{uid}",
                          json.dumps({"base": base, "ops": list(ops)}))
        return True

    def _user_ops_entry(self, bundle_id, uid):
        """The persisted op-log for a (bundle, uid), or None."""
        import json
        if self.bdb is None:
            return None
        raw = self.bdb.meta_get(f"user_ops:{bundle_id}:{uid}", "")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None

    def _edit_session(self):
        """The open edit session's (wrapper, topo), or None with an error."""
        if self._edit_topo is None:
            print("Error: no edit session — run edit_topology <bundle_id> [<cand#>|new] first")
            return None
        return self._edit_w, self._edit_topo

    def _resort_pool_preserving_selection(self, w, pool):
        """Sort `pool` by generation's key (wirelength, then structural
        segment count incl. TEG-over bridges, then type — the same
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
        # Same mode-aware key as C++ annotate_and_sort: the segments-first
        # EXPERIMENT toggle (BUDA_TOPO_SORT=segs) must survive accretion —
        # an unconditional WL sort here would silently revert the pool order
        # after generate_more_topologies / knob-memo replays (Codex #326).
        # Default key mirrors the structural (wl, nsegs, type) tie-break
        # (bridge segments counted, as in C++).
        _nsegs = lambda c: len(c.segments) + len(c.bridge_segments)
        if os.environ.get("BUDA_TOPO_SORT") == "segs":
            pool.sort(key=lambda c: (len(c.segments),
                                     c.estimated_wirelength, _nsegs(c), c.type))
        else:
            pool.sort(key=lambda c: (c.estimated_wirelength, _nsegs(c), c.type))
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

    def _apply_gen_knobs(self, w, src, dsts, old_pin_uid=None,
                         bulk_knobs=(False, False, False, True)):
        """Honor the bundle's persisted generation-knob memo (v15): re-run the
        knob-configured generator additively after a bulk regeneration, so a
        pool accreted with generate_more_topologies does not silently revert.
        Re-attempts the uid pin reattach among the appended extras.

        bulk_knobs = the bulk command's effective (center_mode, double_detour,
        multi_trunk, hanan_loci) — the knobs the base pool was just generated
        with.  A `no_hanan_loci` memo token (the default-flip opt-out) cannot
        be replayed additively (an additive merge only ADDS candidates), so it
        is honored by REGENERATING the base pool midpoint-only with the bulk's
        other knobs (pin re-attached by uid, USER candidates kept) before the
        additive opt-in replay — the exact mirror of how opt-ins round-trip.
        Loci polarity for the additive part: memo token wins (either
        polarity), else the bulk setting; see the memo-encoding note at
        _record_gen_knob_memo (topologies_cmds.py)."""
        if self.bdb is None:
            return
        knobs = self.bdb.bundle_gen_knobs(str(w.input.original_bundle.id))
        if not knobs:
            return
        ks = set(knobs.split())
        if "no_hanan_loci" in ks:
            replay_loci = False
        elif "hanan_loci" in ks:
            replay_loci = True
        else:
            replay_loci = bulk_knobs[3]
        if "no_hanan_loci" in ks and bulk_knobs[3]:
            pin_uid = self._pinned_uid(w) or old_pin_uid
            kept_user = self._user_candidates(w)
            tg_off = self._make_topo_gen(self.fp, bulk_knobs[0],
                                         bulk_knobs[1], bulk_knobs[2],
                                         use_hanan_loci=False)
            w.input.candidates = tg_off.generate_candidates(src, dsts)
            self._reset_plan_for_regen(w, pin_uid, kept_user)
            print(f"  Re-applied knob memo '{knobs}' for bundle "
                  f"{w.input.original_bundle.id}: regenerated midpoint-only "
                  f"(no_hanan_loci).")
        # A memo with NO additive opt-in tokens has nothing left to replay:
        # the base pool already honors the bulk knobs and the loci polarity
        # (regenerated above for an opt-out under a loci-on bulk).  Running
        # the additive generator anyway — all non-memo knobs off — would
        # append a PLAIN midpoint pool into e.g. a center_mode bulk regen,
        # polluting the requested candidate set.  The one additive job left
        # for a pure-polarity memo is a loci OPT-IN against a loci-off base.
        base_loci = False if "no_hanan_loci" in ks else bulk_knobs[3]
        if (not (ks - {"hanan_loci", "no_hanan_loci"})
                and not (replay_loci and not base_loci)):
            return
        tg = self._make_topo_gen(self.fp, "center_mode" in ks,
                                 "double_detour" in ks, "multi_trunk" in ks,
                                 replay_loci)
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

    # ── WL-dominance pruning (opt-in: set_prune_dominated) ────────────────
    #
    # Drop a candidate whose WL envelope bottom (wl_lo) exceeds another
    # candidate's envelope top (wl_hi) — deterministic WL dominance — but
    # ONLY when the two are equivalent in every non-WL respect the planner
    # scores or the escalation ladder exploits (Codex P2 on PR #313: the
    # planner scores congestion/span/layer/balance/peak BEFORE weighted WL,
    # and a longer candidate can be the only overflow-free / window-feasible
    # option, so an unconditional drop could strand the only routable
    # topology).  The gate is deliberately conservative: a false prune is a
    # correctness bug, a missed prune only wasted planner work.

    _PRUNE_SENT = 1e8            # ConnTopology unbounded-slide sentinel (~5e8)
    _CLAMP_LO_SENT = -2**31      # Segment::perp_clamp_lo INT_MIN sentinel
    _CLAMP_HI_SENT = 2**31 - 1   # Segment::perp_clamp_hi INT_MAX sentinel

    def _topo_prune_info(self, topo, fp):
        """Extract one candidate's dominance/equivalence facts, or None when
        the candidate must not participate in pruning at all (as dominated OR
        survivor): TEG bridge segments (extra wire outside `segments`),
        fan-in per-bit taper (`seg_bits` — per-segment demand differs per
        bit), adopted dogleg jogs, U_OVL perp clamps (a NUTS constraint the
        window model below does not carry), or an underivable envelope/
        connectivity.  Returns {lo, hi, nominal, blocks, feedthru, segs}
        where segs = [(horiz, layer_hint, win_lo, win_hi, along_lo,
        along_hi)] with unbounded windows widened to ±inf."""
        try:
            if topo.bridge_segments or topo.seg_bits:
                return None
            for seg in topo.segments:
                if getattr(seg, 'is_jog', False):
                    return None
                if (seg.perp_clamp_lo != self._CLAMP_LO_SENT
                        or seg.perp_clamp_hi != self._CLAMP_HI_SENT):
                    return None
            ct = buda.ConnTopology()
            ct.build(topo, fp)
            csegs = list(ct.segs())
            if not csegs or len(csegs) != len(topo.segments):
                return None
            lo, hi = self._topology_wl_interval(topo, fp=fp)
            segs = []
            for i, cs in enumerate(csegs):
                wlo = min(cs.perp_lo, cs.perp_hi)
                whi = max(cs.perp_lo, cs.perp_hi)
                if wlo < -self._PRUNE_SENT:
                    wlo = float('-inf')
                if whi > self._PRUNE_SENT:
                    whi = float('inf')
                segs.append((cs.horiz, topo.segments[i].layer_hint,
                             wlo, whi, cs.along_lo, cs.along_hi))
            return {"lo": lo, "hi": hi,
                    "nominal": topo.estimated_wirelength,
                    "blocks": frozenset(topo.connected_block_names),
                    "feedthru": frozenset(topo.feedthru_blocks),
                    "segs": segs}
        except Exception:
            return None

    @staticmethod
    def _wl_prune_equivalent(d, s):
        """True iff survivor `s` offers at least dominated `d`'s routing
        freedom in every non-WL respect: same block contract and feedthru
        declarations, same segment count, and a one-to-one segment matching
        where each matched pair has the same orientation and layer hint, the
        SURVIVOR's slide window COVERS the dominated one's (the safe
        containment direction — every band/track placement reachable by the
        dominated segment is reachable by the survivor's), and the survivor's
        along-extent lies INSIDE the dominated one's (the survivor crosses a
        subset of the cuts with the same bus width, so any overflow-free
        assignment for the dominated candidate maps to one for the survivor).
        Inputs are `_topo_prune_info` dicts."""
        if d["blocks"] != s["blocks"] or d["feedthru"] != s["feedthru"]:
            return False
        ds, ss = d["segs"], s["segs"]
        n = len(ds)
        if n != len(ss):
            return False

        def compatible(di, sj):
            (dh, dl, dwlo, dwhi, dalo, dahi) = di
            (sh, sl, swlo, swhi, salo, sahi) = sj
            return (dh == sh and dl == sl
                    and swlo <= dwlo and swhi >= dwhi     # window coverage
                    and dalo <= salo and sahi <= dahi)    # span containment

        # Perfect bipartite matching (augmenting paths; n is small).
        adj = [[j for j in range(n) if compatible(ds[i], ss[j])]
               for i in range(n)]
        match = [-1] * n     # survivor seg j -> dominated seg i

        def augment(i, seen):
            for j in adj[i]:
                if j in seen:
                    continue
                seen.add(j)
                if match[j] < 0 or augment(match[j], seen):
                    match[j] = i
                    return True
            return False

        return all(augment(i, set()) for i in range(n))

    def _prune_wl_dominated(self, w, fp):
        """Prune bundle `w`'s WL-dominated + gate-equivalent candidates.
        A USER candidate takes no part at all — never pruned AND never a
        survivor (edit_commit accepts a not-clean hand edit with only a
        warning, so it must not evict a valid generated alternative); the
        currently selected/pinned candidate is never pruned either;
        the selection index is remapped by uid across the shrink.  A pruned
        survivor may still prune others: dominance + the gate are transitive
        (window coverage composes; s pruned by s2 means s.lo > s2.hi, and
        d.lo > s.hi >= s.lo > s2.hi).  Returns (n_pruned, n_refused_pairs)
        where refused = dominated pairs the equivalence gate kept."""
        cands = list(w.input.candidates)
        if len(cands) < 2:
            return (0, 0)
        infos = [self._topo_prune_info(c, fp) for c in cands]
        sel = w.plan.selected_topology_index
        bid = w.input.original_bundle.id
        pruned, refused = set(), 0
        for i in range(len(cands)):
            di = infos[i]
            if di is None or cands[i].type == "USER" or i == sel:
                continue
            for j in range(len(cands)):
                sj = infos[j]
                # A USER candidate never acts as SURVIVOR either: edit_commit
                # allows committing a not-clean hand-edited topology with only
                # a warning, so an invalid-but-shorter USER candidate must not
                # evict the valid generated alternative (Codex on PR #329).
                if j == i or sj is None or cands[j].type == "USER":
                    continue
                # Deterministic dominance: the dominated candidate's BEST
                # realization exceeds the survivor's WORST — and the nominal
                # (which carries dangling wire the conn-based envelope does
                # not) must dominate too, so the planner's kWL term never
                # preferred the pruned one.
                if not (di["lo"] > sj["hi"]
                        and sj["nominal"] <= di["nominal"]):
                    continue
                if self._wl_prune_equivalent(di, sj):
                    pruned.add(i)
                    print(f"  [TopoPrune] bundle {bid}: dropped {cands[i].type} "
                          f"wl[{di['lo']:.0f}..{di['hi']:.0f}] — WL-dominated by "
                          f"{cands[j].type} wl[{sj['lo']:.0f}..{sj['hi']:.0f}] "
                          f"(equivalent layers/corridors/contract)")
                    break
                refused += 1
        if pruned:
            sel_uid = (buda.topo_uid(cands[sel])
                       if 0 <= sel < len(cands) else None)
            keep = [c for k, c in enumerate(cands) if k not in pruned]
            w.input.candidates = keep
            if sel_uid is not None:
                for k, c in enumerate(w.input.candidates):
                    if buda.topo_uid(c) == sel_uid:
                        w.plan.selected_topology_index = k
                        break
        return (len(pruned), refused)

    def _prune_dominated_pools(self, wraps=None):
        """Run the opt-in WL-dominance prune over `wraps` (default: all
        bundles) and print the per-run summary.  No-op (bit-identical) when
        set_prune_dominated is off.  Called by the generation commands AFTER
        the pool is final (knob-memo replay included) and BEFORE sidecar
        selection restore / BDB persistence, so indices, persisted rows, and
        later select_topology pins all see the same (pruned) pool."""
        if not getattr(self, "_prune_dominated", False):
            return (0, 0)
        if wraps is None:
            wraps = self.bundles
        topo_fp = self._make_topo_fp_resolver()
        total_pruned = total_refused = 0
        n_bundles = 0
        for w in wraps:
            if len(w.input.candidates) < 2:
                continue
            np_, nr = self._prune_wl_dominated(w, topo_fp(w))
            if np_:
                n_bundles += 1
            total_pruned += np_
            total_refused += nr
        print(f"[TopoPrune] pruned {total_pruned} WL-dominated candidate(s) "
              f"across {n_bundles} bundle(s); {total_refused} dominated "
              f"pair(s) refused by the equivalence gate.")
        return (total_pruned, total_refused)

    def _cleanup_candidate_pools(self, wraps=None):
        """Run the opt-in candidate-pool cleanups in order: WL-dominance prune
        (set_prune_dominated), nominal-locus dedup (set_dedup_loci), dangling
        drop (set_drop_dangling).  Each is a no-op (bit-identical) when its knob
        is off.  Called by every generation command AFTER the pool is final
        (knob-memo replay included) and BEFORE sidecar restore / persistence, so
        indices, pins and persisted rows all see the same cleaned pool."""
        self._prune_dominated_pools(wraps)
        self._dedup_loci_pools(wraps)
        self._drop_dangling_pools(wraps)

    # ── opt-in pool cleanups: locus dedup + dangling drop ────────────────────
    _DANGLING_SENTINEL = 100_000_000   # |perp| >= this = an unclamped window

    def _topo_loci_canon(self, t, fp):
        """Structural fingerprint of a candidate that is INVARIANT to the
        nominal trunk locus but SENSITIVE to connectivity, layers, slide
        windows, block taps and net-pull.  Two candidates with the same key
        are the SAME topological choice differing only in a nominal position
        inside a shared slide window — the trunk locus slides with its stubs,
        so they route within NUTS realization-noise of each other (the nominal
        is only a placement hint; the residual spread is the b44 realization
        sensitivity).

        Returns None (the candidate never participates in the dedup — as
        neither a survivor NOR a collapsed member) for anything the ConnSeg
        skeleton does not fully capture, mirroring `_topo_prune_info`: TEG
        bridge segments (real wire OUTSIDE `segments`, persisted separately —
        two candidates with the same skeleton but different bridge wires must
        not collide, Codex #389), fan-in per-bit taper (`seg_bits`), adopted
        dogleg jogs, and U_OVL perp clamps.  Also None if the ConnTopology
        can't be built."""
        if t.bridge_segments or t.seg_bits:
            return None
        for seg in t.segments:
            if getattr(seg, 'is_jog', False):
                return None
            if (seg.perp_clamp_lo != self._CLAMP_LO_SENT
                    or seg.perp_clamp_hi != self._CLAMP_HI_SENT):
                return None
        try:
            ct = buda.ConnTopology(); ct.build(t, fp)
        except Exception:
            return None
        segs = ct.segs()
        base = []
        for cs in segs:
            taps = tuple(sorted((c.block_name, round(c.face_coord))
                                for c in cs.conns if c.block_name))
            base.append((cs.horiz, cs.layer_id, round(cs.perp_lo),
                         round(cs.perp_hi), round(cs.net_pull), taps))
        adj = []
        for i, cs in enumerate(segs):
            nb = tuple(sorted(base[c.seg_idx]
                              for c in cs.conns if not c.block_name))
            adj.append((base[i], nb))
        return tuple(sorted(adj))

    def _dedup_loci_one(self, w, fp):
        cands = list(w.input.candidates)
        if len(cands) < 2:
            return 0
        sel = w.plan.selected_topology_index
        sel_uid = (buda.topo_uid(cands[sel]) if 0 <= sel < len(cands) else None)
        bid = w.input.original_bundle.id
        seen, drop = {}, set()
        for i, c in enumerate(cands):
            if c.type == "USER":            # hand-built: never collapsed
                continue
            key = self._topo_loci_canon(c, fp)
            if key is None:
                continue
            if key not in seen:
                seen[key] = i               # first = lowest-WL representative
            elif i != sel:                  # never drop the pinned candidate
                drop.add(i)
                print(f"  [TopoDedup] bundle {bid}: dropped {c.type} "
                      f"(idx {i + 1}) — same slide-window/connectivity as "
                      f"idx {seen[key] + 1} (nominal-locus variant)")
        if not drop:
            return 0
        w.input.candidates = [c for k, c in enumerate(cands) if k not in drop]
        if sel_uid is not None:
            for k, c in enumerate(w.input.candidates):
                if buda.topo_uid(c) == sel_uid:
                    w.plan.selected_topology_index = k
                    break
        return len(drop)

    def _dedup_loci_pools(self, wraps=None):
        """Opt-in nominal-locus dedup (set_dedup_loci): collapse candidates
        that share connectivity + slide windows + taps + net-pull, keeping the
        best-estimated (lowest-WL) representative.  No-op (bit-identical) when
        off.  Runs after the WL-dominance prune, before sidecar restore /
        persistence, so indices/pins/persisted rows see the collapsed pool."""
        if not getattr(self, "_dedup_loci", False):
            return 0
        if wraps is None:
            wraps = self.bundles
        topo_fp = self._make_topo_fp_resolver()
        total = nb = 0
        for w in wraps:
            n = self._dedup_loci_one(w, topo_fp(w))
            nb += 1 if n else 0
            total += n
        if total:
            print(f"[TopoDedup] collapsed {total} nominal-locus duplicate "
                  f"candidate(s) across {nb} bundle(s).")
        return total

    def _topo_dangling_reason(self, t, fp):
        """A candidate is 'dangling' when some ConnSeg has (a) a single
        connection that is NOT a block tap — a wire whose other end connects to
        nothing — or (b) an unbounded/unclamped slide window (the +/-2^30
        no-clamp sentinel).  The DROP-mode predicate.  Returns a reason, or
        None."""
        try:
            ct = buda.ConnTopology(); ct.build(t, fp)
        except Exception:
            return None
        S = self._DANGLING_SENTINEL
        for j, cs in enumerate(ct.segs()):
            if abs(cs.perp_lo) >= S or abs(cs.perp_hi) >= S:
                return f"seg{j} has an unbounded (unclamped) slide window"
            blk = sum(1 for c in cs.conns if c.block_name)
            if len(cs.conns) == 1 and blk == 0:
                return f"seg{j} is a dangling stub (single non-block connection)"
        return None

    def _topo_truly_dangling_reason(self, t, fp):
        """TRULY dangling: some ConnSeg has a single connection that is NOT a
        block tap — a wire whose other end connects to nothing.  An unbounded
        slide window alone is NOT truly dangling (clamp fixes that).  The
        clamp_drop-mode drop predicate.  Returns a reason, or None."""
        try:
            ct = buda.ConnTopology(); ct.build(t, fp)
        except Exception:
            return None
        for j, cs in enumerate(ct.segs()):
            blk = sum(1 for c in cs.conns if c.block_name)
            if len(cs.conns) == 1 and blk == 0:
                return f"seg{j} is a dangling stub (single non-block connection)"
        return None

    def _drop_dangling_one(self, w, fp, reason_fn):
        cands = list(w.input.candidates)
        sel = w.plan.selected_topology_index
        sel_uid = (buda.topo_uid(cands[sel]) if 0 <= sel < len(cands) else None)
        bid = w.input.original_bundle.id
        drop = set()
        for i, c in enumerate(cands):
            if c.type == "USER" or i == sel:   # keep USER + the pinned one
                continue
            reason = reason_fn(c, fp)
            if reason:
                drop.add(i)
                print(f"  [TopoDangling] bundle {bid}: dropped {c.type} "
                      f"(idx {i + 1}) — {reason}")
        if not drop:
            return 0
        keep = [c for k, c in enumerate(cands) if k not in drop]
        if not keep:                            # never strand a bundle
            print(f"  [TopoDangling] bundle {bid}: every candidate flagged — "
                  f"keeping the pool (nothing dropped).")
            return 0
        w.input.candidates = keep
        if sel_uid is not None:
            for k, c in enumerate(w.input.candidates):
                if buda.topo_uid(c) == sel_uid:
                    w.plan.selected_topology_index = k
                    break
        return len(drop)

    def _design_extent(self, w, fp):
        """The routable extent used as the clamp bound: the bounding box of all
        blocks AND every candidate's segment nominals (so the clamp never
        excludes a real position), grown by a margin (the detour channel width
        if reserved, else a quarter of the span) so OOB detours keep slide
        room.  Returns (xlo, xhi, ylo, yhi) or None."""
        xs, ys = [], []
        try:
            for _name, r in fp.get_all_blocks():   # (name, Rect) tuples
                xs += [r.x1, r.x2]; ys += [r.y1, r.y2]
        except Exception:
            return None
        for c in w.input.candidates:
            for seg in c.segments:
                xs += [seg.start.x, seg.end.x]; ys += [seg.start.y, seg.end.y]
        if not xs:
            return None
        xlo, xhi, ylo, yhi = min(xs), max(xs), min(ys), max(ys)
        try:
            dsz = int(getattr(fp.get_detour_channel(), "size", 0) or 0)
        except Exception:
            dsz = 0
        mx = max(dsz, (xhi - xlo) // 4, 1)
        my = max(dsz, (yhi - ylo) // 4, 1)
        return (xlo - mx, xhi + mx, ylo - my, yhi + my)

    def _clamp_unbounded_one(self, w, fp, ext):
        """Bound every unbounded slide window in this bundle's candidates to the
        design extent via Segment.perp_clamp_lo/hi (which analyze() pass 3
        intersects into the window), clearing each touched candidate's analysis
        cache so the next build recomputes the bounded window.  Keeps the
        candidates; never touches USER candidates.  Returns the number of
        candidates clamped."""
        if ext is None:
            return 0
        xlo, xhi, ylo, yhi = ext
        S = self._DANGLING_SENTINEL
        INT_MIN, INT_MAX = -2147483648, 2147483647
        cands = list(w.input.candidates)
        changed = 0
        for c in cands:
            if c.type == "USER":
                continue
            try:
                ct = buda.ConnTopology(); ct.build(c, fp)
            except Exception:
                continue
            segs = ct.segs()
            if len(segs) != len(c.segments):
                continue    # can't map ConnSeg -> raw seg reliably; skip
            touched = False
            for j, cs in enumerate(segs):
                lo_unb = cs.perp_lo <= -S
                hi_unb = cs.perp_hi >= S
                if not (lo_unb or hi_unb):
                    continue
                seg = c.segments[j]
                clo, chi = (ylo, yhi) if cs.horiz else (xlo, xhi)
                if lo_unb and seg.perp_clamp_lo == INT_MIN:
                    seg.perp_clamp_lo = clo; touched = True
                if hi_unb and seg.perp_clamp_hi == INT_MAX:
                    seg.perp_clamp_hi = chi; touched = True
            if touched:
                c.clear_analysis_cache()
                changed += 1
        w.input.candidates = cands          # write back (pybind copy semantics)
        return changed

    def _drop_dangling_pools(self, wraps=None):
        """Opt-in dangling/unclamped-segment handling (set_drop_dangling), by
        mode: 'drop' drops any dangling/unbounded candidate (most aggressive);
        'clamp' bounds every unbounded slide window and drops nothing; 'clamp_drop'
        clamps the windows AND drops only the TRULY dangling candidates.  Never
        touches a USER candidate, never drops the pinned one, never strands a
        bundle.  No-op (bit-identical) when off."""
        mode = getattr(self, "_drop_dangling_mode", "off")
        if mode == "off":
            return 0
        if wraps is None:
            wraps = self.bundles
        topo_fp = self._make_topo_fp_resolver()
        tot_drop = tot_clamp = nb_drop = nb_clamp = 0
        for w in wraps:
            fp = topo_fp(w)
            if mode in ("drop", "clamp_drop"):
                reason_fn = (self._topo_dangling_reason if mode == "drop"
                             else self._topo_truly_dangling_reason)
                nd = self._drop_dangling_one(w, fp, reason_fn)
                nb_drop += 1 if nd else 0
                tot_drop += nd
            if mode in ("clamp", "clamp_drop"):
                nc = self._clamp_unbounded_one(w, fp, self._design_extent(w, fp))
                nb_clamp += 1 if nc else 0
                tot_clamp += nc
        if tot_clamp:
            print(f"[TopoDangling] clamped unbounded slide windows on "
                  f"{tot_clamp} candidate(s) across {nb_clamp} bundle(s).")
        if tot_drop:
            print(f"[TopoDangling] dropped {tot_drop} candidate(s) across "
                  f"{nb_drop} bundle(s).")
        return tot_drop + tot_clamp

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

    @staticmethod
    def _clear_stale_seg_overrides(w, tidx):
        """Drop per-segment overrides when a pin moves to a DIFFERENT candidate
        (audit P5-03): edit_commit stages seg_slide_lo/hi (and the dogleg pass
        seg_net_pull / seg_perp) for the candidate selected at commit time, and
        NUTS's only staleness guard is an array-LENGTH match — so re-pinning
        another same-segment-count candidate (all Z/U shapes have 3 segments)
        silently applied the old windows to unrelated segments, clamping the
        new trunk into a window meant for a perpendicular-axis segment.
        Re-pinning the SAME candidate keeps its overrides."""
        if tidx == w.plan.selected_topology_index:
            return
        w.plan.seg_net_pull = []
        w.plan.seg_slide_lo = []
        w.plan.seg_slide_hi = []
        w.plan.seg_perp = []

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
                    self._clear_stale_seg_overrides(w, tidx)
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
                # BundleWrapper has no bare .candidates — the pool lives on
                # .input (audit P3-02: this branch crashed with
                # AttributeError before any pin was applied).
                if tidx < 0 or tidx >= len(wrappers[0].input.candidates):
                    print(f"Error: invalid topology id {tid} for bundle {bid}")
                else:
                    for w in wrappers:
                        self._clear_stale_seg_overrides(w, tidx)
                        w.plan.selected_topology_index = tidx
                        w.input.topology_pinned = True
                    n = len(wrappers)
                    print(f"Pinned bundle {bid} to topology {tid} "
                          f"({n} expanded instance{'s' if n > 1 else ''})")
                found = True
        if not found:
            print(f"Error: bundle {bid} not found")
        return found
