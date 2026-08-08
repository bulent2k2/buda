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

"""Feedback-driven rip-up & re-route and congestion negotiation.

The ripup_reroute greedy hill-climb (contender ranking, candidate order,
MST edge flips, snapshot/restore, incremental replan trials) and the
negotiate_congestion measured-demand injection loop.

Methods extracted verbatim from buda_cli.BudaSession (the CLI mixin
split); bodies unchanged — `self` is the composed BudaSession, so
cross-mixin helper calls resolve through the class as before.
"""
import contextlib
import copy
import io
import os
import time

import buda

from .util import (_RR_CLASS_MAX_TRIALS, _RR_RELEASE_MAX_TRIALS,
                   _RR_PARALLEL_SWEEP_DEFAULT,
                   _RR_CLASS_TOP_N, _RR_CONVERGE_FLOOR, _RR_CONVERGE_FRAC,
                   _RR_CONVERGE_GUARD_DEFAULT, _RR_CONVERGE_WINDOW,
                   _RR_DEFAULT_MAX_ITER, _RR_FAST_TRIALS_DEFAULT,
                   _RR_GLOBAL_MAX_TRIALS, _RR_GLOBAL_MOVES_PER_OCC,
                   _RR_GLOBAL_TOP_K, _RR_HEAL_DEAD_SPANS_DEFAULT,
                   _RR_MAX_CANDIDATES_PER_BUNDLE,
                   _RR_SCREEN_DEFAULT, _RR_SCREEN_TOP_N,
                   _RR_WARM_TRIALS_DEFAULT)


class RipupMixin:

    # ---- ripup_reroute: feedback-driven rip-up & re-route -------------------
    # After run_nuts (stage a) or run_detailed_nuts (stage b) the planner may have
    # left a congestion-induced NUTS overlap / DNUTS open that its own band model
    # did not predict (it reports overflow=0).  This greedy hill-climb reads the
    # ACTUAL overlaps/opens, re-routes a contending bundle to an alternate topology,
    # re-runs the pipeline, and keeps moves that reduce the metric — the loop the
    # planner cannot do because it is blind to the real NUTS/DNUTS result.

    def _rr_stage_metric(self):
        """(stage, metric_fn) for the active pipeline state, or (None, None).

        Stage b's metric is LEXICOGRAPHIC: (DNUTS opens, NUTS overlaps).  The
        hill-climb optimizes opens first, but among moves with equal opens it
        must not trade abstract-NUTS packing away as collateral (a move that
        clears the last opens while adding bus-level overlaps leaves a worse
        route than one that clears them cleanly — the big2 <=9 guard).  Python
        tuples compare lexicographically, so the loop's `m < cur` works
        unchanged; zero/no-op checks use _rr_m_primary."""
        if self.detailed_result is not None:
            return 'b', (lambda: (self.detailed_result.num_unplaced
                                  + self._rr_disconnected_bits(),
                                  self.nuts_result.num_overlaps
                                  if self.nuts_result is not None else 0))
        if self.nuts_result is not None:
            return 'a', (lambda: self.nuts_result.num_overlaps)
        return None, None

    def _rr_disconnected_bits(self):
        """Total bits of bundles whose SELECTED topology splits into 2+ separate
        electrical islands (unbridged DISCONNECTED — the whole bus is severed).

        Folded into the stage-b opens count so a heal move that SEVERS a bus
        RAISES the primary metric and is never accepted over a connected route
        (issue #399 follow-up 2): a severed bus's bits are placed (so they never
        show as DNUTS opens — the exact blind spot bigHalf bundle 67 hit) but are
        electrically incomplete, so counting them as effective opens is honest.

        Defense-in-depth: 0 in the common case — generation drops disconnected
        candidates (filter_uncovered) and the dogleg fallback refuses severing
        splits (#405).  Per-eval cost is the cached ConnTopology plus the island
        union-find over the selected topologies; the `disconnected_islands_bridged`
        exemption keeps declared-feedthru splits (a real bridged relay) from
        counting.  Any pre-existing DISCONNECTED bundle is a constant offset on
        both sides of `m < cur`, so it never blocks other progress.

        Per-run memoized (docs/internal/ripup_runtime_analysis.md, item B): the
        floorplan is fixed for a ripup/negotiate run, so a candidate's verdict
        is a pure function of (topo_uid, bid).  `self._rr_disc_memo` (init in
        _rr_t_init, cleared at exit) caches the boolean, so a metric eval only
        pays the ConnTopology + union-find for a candidate it has not seen —
        the moved bundle's new topo — instead of rescanning every bundle each
        eval.  None outside a run → compute directly (behavior unchanged).

        The per-eval walk itself is C++-keyed (rnr runtime N1,
        docs/internal/rnr_runtime_parallelism.md): `buda.selected_topo_key`
        fingerprints the SELECTED candidate in one zero-copy crossing —
        the old loop paid a full candidate-POOL copy per bundle
        (`w.input.candidates` materializes every Topology) plus an
        original_bundle copy for the id, per metric evaluation.  The pool
        copy is now paid only on a memo MISS (the moved bundle's new
        candidate) and on the rare no-memo path."""
        import buda
        fp = self.fp
        memo = getattr(self, "_rr_disc_memo", None)
        total = 0
        for w in self.bundles:
            uid, bid = buda.selected_topo_key(w)
            if not uid:                       # no valid selection
                continue
            try:
                if memo is not None:
                    key = (uid, bid)
                    dc = memo.get(key)
                    if dc is None:
                        topo = w.input.candidates[
                            w.plan.selected_topology_index]
                        dc = self._rr_topo_disconnected(topo, fp, bid)
                        memo[key] = dc
                else:
                    topo = w.input.candidates[
                        w.plan.selected_topology_index]
                    dc = self._rr_topo_disconnected(topo, fp, bid)
                if dc:
                    total += len(w.input.original_bundle.get_net_names())
            except Exception:
                continue
        return total

    @staticmethod
    def _rr_topo_disconnected(topo, fp, bid):
        """True iff `topo` is unbridged-DISCONNECTED (wire graph splits into 2+
        electrical islands with no declared-feedthru bridge).  A pure function
        of (topo, fp); `bid` only labels the violation, so the result is safe to
        memoize by topo content fingerprint."""
        import buda
        ct = buda.ConnTopology(); ct.build(topo, fp)
        return (any(v.kind == buda.ViolationKind.DISCONNECTED
                    for v in buda.check_topo(ct, topo, fp, bid).violations)
                and not buda.disconnected_islands_bridged(ct, topo, fp))

    def _rr_disconnected_bids(self):
        """The bundle ids whose selected topology is unbridged-DISCONNECTED —
        for the loud end-of-run report (issue #399 follow-up 3): a severed bus
        never shows as a DNUTS open, so a plain `done: metric …->0` line would
        read as fully clean.  Cheap: run once at ripup exit."""
        import buda
        fp = self.fp
        bids = []
        for w in self.bundles:
            cands = w.input.candidates
            sel = w.plan.selected_topology_index
            if not (0 <= sel < len(cands)):
                continue
            topo = cands[sel]
            try:
                ct = buda.ConnTopology(); ct.build(topo, fp)
                bid = w.input.original_bundle.id
                if any(v.kind == buda.ViolationKind.DISCONNECTED
                       for v in buda.check_topo(ct, topo, fp, bid).violations) \
                        and not buda.disconnected_islands_bridged(ct, topo, fp):
                    bids.append(bid)
            except Exception:
                continue
        return bids

    @staticmethod
    def _rr_m_primary(m):
        """The metric's primary component (opens/overlaps count)."""
        return m[0] if isinstance(m, tuple) else m

    @staticmethod
    def _rr_m_str(m):
        """Readable metric for progress lines: '60' or '60 (ovl 9)'."""
        return f"{m[0]} (ovl {m[1]})" if isinstance(m, tuple) else str(m)

    # ---- per-run timing (where does a ripup/negotiate run spend its time?) --
    # A run owns an accumulator for its lifetime (init at entry, cleared at
    # exit); the shared helpers (_rr_rerun/_rr_snapshot/_rr_restore) charge it
    # only while one exists (getattr guard), so calls outside a run are free.
    # The always-on one-line summary rides the run's final "done:" print; set
    # BUDA_RR_TRACE=1 for a per-trial line (move + metric + trial seconds).

    def _rr_t_init(self):
        self._rr_t = {'replan': 0.0, 'nuts': 0.0, 'dnuts': 0.0,
                      'screen': 0.0, 'warm': 0.0, 'psweep': 0.0,
                      'snapshot': 0.0, 'restore': 0.0,
                      'n_replan': 0, 'n_nuts': 0, 'n_dnuts': 0,
                      'n_screen': 0, 'n_warm': 0, 'n_psweep': 0,
                      'n_snapshot': 0, 'n_restore': 0,
                      'passes': {}}
        # Per-run memo for _rr_disconnected_bits: the floorplan is fixed for a
        # run's lifetime, so a candidate's DISCONNECTED verdict is a pure
        # function of (topo_uid, bid) and unchanged for every bundle a trial
        # did not move.  Cleared at run exit (both self._rr_t = None sites).
        self._rr_disc_memo = {}

    def _validate_stage_entry(self, where):
        from buda_session.util import _mixin_validate_stage_entry
        _mixin_validate_stage_entry(self, where)

    def _rr_t_add(self, key, dt):
        t = getattr(self, '_rr_t', None)
        if t is not None:
            t[key] += dt
            t['n_' + key] += 1

    def _rr_t_add_passes(self, prefix, pass_seconds):
        """Fold a solve's per-pass profile (NUTSResult/DetailedNUTSResult
        .pass_seconds — RR round-3 Phase 0) into the run accumulator under
        '<prefix>.<pass>' keys.  Same getattr guard as _rr_t_add: free
        outside a run."""
        t = getattr(self, '_rr_t', None)
        if t is not None:
            p = t['passes']
            for k, v in pass_seconds.items():
                key = f"{prefix}.{k}"
                p[key] = p.get(key, 0.0) + v

    def _rr_t_str(self):
        t = getattr(self, '_rr_t', None)
        if t is None:
            return ""
        s = (f"replan {t['replan']:.2f}s/{t['n_replan']}, "
             f"nuts {t['nuts']:.2f}s/{t['n_nuts']}, "
             f"dnuts {t['dnuts']:.2f}s/{t['n_dnuts']}, ")
        if t.get('n_screen'):
            s += f"screen {t['screen']:.2f}s/{t['n_screen']}, "
        if t.get('n_warm'):
            s += f"warm {t['warm']:.2f}s/{t['n_warm']}, "
        if t.get('n_psweep'):
            s += f"psweep {t['psweep']:.2f}s/{t['n_psweep']}, "
        return s + (f"snapshot {t['snapshot']:.2f}s/{t['n_snapshot']}, "
                    f"restore {t['restore']:.2f}s/{t['n_restore']}")

    def _rr_t_passes_str(self):
        """One line answering WHERE inside the solves the nuts/dnuts seconds
        go: per-pass totals across every trial's solve (dogleg trial
        re-solves folded in by the engine), descending, grouped by stage.
        Empty string when no pass profile was collected."""
        t = getattr(self, '_rr_t', None)
        if not t or not t.get('passes'):
            return ""
        groups = []
        for prefix in ('nuts', 'dnuts'):
            items = sorted(((k.split('.', 1)[1], v)
                            for k, v in t['passes'].items()
                            if k.startswith(prefix + '.')),
                           key=lambda kv: -kv[1])
            if items:
                body = " ".join(f"{k} {v:.2f}" for k, v in items)
                groups.append(f"{prefix}[{body}]")
        return " ".join(groups)

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
        contended bundle — including one pinned earlier (its pin is replaced).
        The one exception is a hier.locked wrapper (bottom-up template
        instance): its assignment is a uniform copy shared by every sibling
        instance and must not be moved unilaterally."""
        order, seen = [], set()
        def add(bid):
            if bid in seen:
                return
            seen.add(bid)
            w = self._rr_wrapper(bid)
            if w is not None and w.hier.locked:
                return
            order.append(bid)
        for bid in self._rr_contention_sources(stage):
            add(bid)
        if not order:                       # fallback: any re-routable bundle
            for w in self.bundles:
                if len(w.input.candidates) > 1:
                    add(w.input.original_bundle.id)
        return order

    def _rr_contention_sources(self, stage):
        """Contention-derived bundle ids in priority order (duplicates
        included — callers dedup): stage b's OPEN bundles first (a DNUTS
        open is caused by a NUTS overlap, so re-routing either side of the
        overlap can clear it), then the NUTS-overlap partners, then
        junction-infeasibility bundles (Part B: a bundle whose junction
        edge could only close by a large partner stretch is a contender
        even with no overlap — a quality signal, listed after the hard
        ones).  The shared walk under _rr_contenders (locked excluded) and
        _rr_locked_contenders (locked only)."""
        if stage == 'b':
            yield from self._rr_open_bundles()
        yield from self._rr_overlap_bundles()
        if self.nuts_result is not None:
            for ji in self.nuts_result.junction_infeasibilities:
                yield ji.bundle_id

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
        # The caller IS a healer (ripup's hier re-plan), so the env-default
        # gate is satisfied by construction — script scan or not.
        self._apply_healers_ahead(self.planner, healing_now=True)
        self._apply_hier_refine_default(self.planner)
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

    def _run_nuts_internal(self, skip_tighten=False):
        """Solve abstract NUTS for the current plan WITHOUT the run_nuts
        command's reporting/persistence (nuts-log write, diagnostics, BDB
        persist + route-snapshot hash).  Used by ripup_reroute reruns: nearly
        every trial result is discarded, and _checkpoint_routing persists the
        final accepted state once at the end.  skip_tighten (fast trials,
        stage a only): the solve skips the WL-only tighten_pulls pass, whose
        overlap count is provably non-increasing — the trial metric is then
        an UPPER BOUND, so accepts stay sound (commits re-run full)."""
        nuts = buda.NUTSEngine(self.fp, self.layers)
        nuts.set_track_pitch(self._nuts_pitch)
        nuts.set_skip_tighten(skip_tighten)
        self._inject_bottom_up_fixed(nuts)
        if self.planner is not None:
            nuts.set_extra_grid_points(
                list(self.planner.get_x_grid()),
                list(self.planner.get_y_grid()))
        with buda.ostream_redirect():
            self.nuts_result = nuts.run(self.bundles)
        self._adopt_doglegs()

    def _rr_rerun(self, stage, target_bid=None, full=False,
                  abort_opens=-1, skip_replan=False):
        """Silently re-run planner + NUTS (+ DNUTS for stage b).

        When `target_bid` names the one bundle a trial moved, the replan is
        INCREMENTAL: CongestionPlanner.replan_bundle recharges every other
        wrapper's committed assignment (no scoring) and re-plans the target
        alone — the full-design optimize_topologies per trial was ~90% of
        ripup_reroute runtime on large hier designs.  Adopted doglegs stay
        adopted: other bundles' split candidates + exported per-segment pins
        are exactly the committed state to charge and re-solve against (the
        target's own stale pins are cleared by _rr_trial).  Returns True when
        the incremental path was used; precondition failures fall back to the
        legacy full replan (a rejected trial of either kind restores exactly
        from the snapshot).

        In hier mode (`run_planner hier` has run, so self.bundles is the expanded
        per-instance list) the full replan re-plans the expanded wrappers in place
        via _rr_replan_hier — driving the flat `run_planner` would re-expand and
        corrupt the wrapper set.  Stage b replays DetailedNUTS through the private
        helper with the user's selected bit order preserved.  The
        `run_detailed_nuts` *command* resets `_detailed_bit_order` to LO_HI before
        parsing its (here absent) arg, so driving it via `do_command` would
        silently flip a HI_LO flow to LO_HI and change detailed wiring semantics
        unrelated to the topology move.

        `skip_replan` (template class trials) skips the planner turn
        entirely: every wrapper keeps its committed assignment, and the
        moved class's routing is the fixed copies the NUTS re-run
        recomputes from the re-pinned template — nothing needs planning,
        and any replan would perturb the committed state the trial must be
        measured against."""
        exact = False
        sink = io.StringIO()
        # A trial's state must NEVER reach the BDB: the full-replan fallback
        # below drives the run_planner COMMAND, whose handler persists the
        # planner output — for a rejected trial (or a run that commits
        # nothing) a later load_pipeline would rehydrate the rejected state
        # (#286 retrospective item 1).  _persist_planner_output checks this
        # flag; the accepted final state is persisted once by
        # _checkpoint_routing at the end of the run.
        self._rr_in_trial = True
        try:
            with contextlib.redirect_stdout(sink), buda.ostream_redirect():
                t0 = time.perf_counter()
                # skip_replan (template class trial): every wrapper keeps
                # its committed assignment (a full replan would re-decide
                # all unpinned bundles and destroy the incremental commits
                # — the exact hazard the main loop's commit comment
                # documents), and the moved class needs no replan at all:
                # locked instances are never planner-placed — their routing
                # is the fixed copies _run_nuts_internal recomputes from
                # the re-pinned template below.
                asn = None
                if not skip_replan:
                    if target_bid is not None and self.planner is not None:
                        asn = self.planner.replan_bundle(self.bundles,
                                                         target_bid)
                    if asn is not None:
                        w = self._rr_wrapper(target_bid)
                        w.plan.selected_topology_index = asn.topo_index
                        w.input.assigned_v_layer = asn.v_layer_id
                        w.input.assigned_h_layer = asn.h_layer_id
                        w.plan.seg_layers = list(asn.seg_layers)
                        w.plan.seg_perp = list(asn.seg_perp)
                        exact = True
                    elif self._planner_is_hier:
                        self._rr_replan_hier(self._planner_iterations)
                    else:
                        self.do_command(
                            f"run_planner {self._planner_iterations}")
                self._rr_t_add('replan', time.perf_counter() - t0)
                t0 = time.perf_counter()
                fast = (getattr(self, '_rr_fast_trials', False)
                        and not full)
                self._run_nuts_internal(
                    skip_tighten=(fast and stage == 'a'))
                self._rr_t_add('nuts', time.perf_counter() - t0)
                self._rr_t_add_passes('nuts', self.nuts_result.pass_seconds)
                # Dirty set for the scoped per-trial restore: the incremental
                # replan mutated only the target; _adopt_doglegs (inside
                # _run_nuts_internal) may additionally have rewritten any
                # bundle in the dogleg-slot map (new adoptions AND in-place
                # re-solves of existing slots — including every pre-existing
                # slot key is conservative and cheap).  A full replan touches
                # everything.
                self._rr_dirty = ({target_bid} | set(self._dogleg_slot)
                                  if exact else None)
                if stage == 'b':
                    t0 = time.perf_counter()
                    self._run_detailed_nuts(
                        bit_order=self._detailed_bit_order,
                        emit_vias=not fast,
                        abort_unplaced=(abort_opens if fast else -1))
                    self._rr_t_add('dnuts', time.perf_counter() - t0)
                    self._rr_t_add_passes(
                        'dnuts', self.detailed_result.pass_seconds)
        finally:
            self._rr_in_trial = False
        return exact

    def _rr_contention_centres(self, stage, bid):
        """(is_horiz, perp_centre) of every measured contention site involving
        bundle `bid`: stage a = its NUTS overlap rectangles, stage b = its
        DNUTS-open segments' placed windows."""
        sites = []
        if stage == 'a' and self.nuts_result is not None:
            h_layers = set(self.layers.get_layer_ids_by_dir(
                buda.LayerDir.HORIZONTAL))
            for od in self.nuts_result.overlap_details:
                if bid in (od.bid_a, od.bid_b):
                    sites.append((od.layer in h_layers,
                                  0.5 * (od.perp_lo + od.perp_hi)))
        elif stage == 'b' and self.nuts_result is not None:
            ts_map = {(t.bundle_id, t.seg_idx): t
                      for t in self.nuts_result.segments}
            for b2, si, _missing, _exp in self._open_segments():
                if b2 != bid:
                    continue
                ts = ts_map.get((b2, si))
                if ts is not None:
                    sites.append((ts.horiz,
                                  0.5 * (ts.interval_lo + ts.interval_hi)))
        return sites

    def _rr_width_infeasible(self, w, tidx):
        """STATIC WIDTH GATE (issue #523): True iff candidate `tidx` carries a
        segment whose bit demand cannot physically fit its own slide window —
        `bits x best-case bit-pitch > window` — so every placement on every
        layer strands bits at DetailedNUTS.  The unpinned planner's STRICT
        refuses such a candidate naturally (charging its width into a smaller
        window overflows in signal_tracks mode); only ripup's PINNED trials
        (whose ladder ends in BEST_EFFORT, by design) can commit one, which is
        how mix2's climb walked into b101's Z_HVH: a 32-bit stub over a
        50-unit window (72 track-units of demand), measured locally better at
        commit time, then unfixable — the fault is width, which no layer
        escalation or re-place can reach.  Gating the TRIAL SET keeps the
        climb out of those corners.

        Conservative by construction — never gates a feasible candidate:
        * bit-pitch is the MINIMUM over the segment's same-direction layers
          (the best case any layer assignment could achieve);
        * a direction where ANY layer lacks a track pattern is skipped (the
          width-mode demand model is not per-track, so no reliable bound);
        * unbounded slide windows (the +-2^30 no-clamp sentinel) never gate;
        * tapered fan-in segments count only their member bits.
        User pins are respected by the caller (a pinned_group returns before
        the gate; the hard single pin never reaches the alternate list).
        Memoized per ripup run — pools only grow mid-run (dogleg appends), so
        (bundle_id, tidx) stays a stable key within one invocation."""
        if os.environ.get('BUDA_RR_WIDTH_GATE', '1') == '0':
            return False                      # study opt-out
        # Scope-out: bottom-up sessions (any hier.locked wrapper) run CLASS
        # moves — one template re-pin re-routes every instance, the coarsest
        # move in the healer, and the most sensitive to trial-list
        # perturbation.  Measured on mix2_fast_bottomup (main + refold): gate
        # off heals 1/0/0 -> 0/0/0, gate on lands 6/60/4 with the gate never
        # excluding a winning move — pure path chaos amplified by class-move
        # granularity.  The gate's motivating corners (mix/mix2) have no
        # locked wrappers, so the blunt boundary costs nothing there.
        if any(w.hier.locked for w in self.bundles):
            return False
        memo = getattr(self, '_rr_width_memo', None)
        if memo is None:
            memo = self._rr_width_memo = {}
            self._rr_width_logged = set()
        try:
            key = (w.input.original_bundle.id, buda.topo_uid(
                w.input.candidates[tidx]))
        except Exception:
            key = (w.input.original_bundle.id, tidx)
        hit = memo.get(key)
        if hit is not None:
            return hit
        verdict = False
        try:
            cand = w.input.candidates[tidx]
            nbits = len(w.input.original_bundle.get_net_names())
            ct = buda.ConnTopology()
            ct.build(cand, self.fp)          # served by the analysis cache
            sb = cand.seg_bits
            for si, cs in enumerate(ct.segs()):
                win = cs.perp_hi - cs.perp_lo
                if win >= 10 ** 8 or win < 0:
                    continue                  # unbounded / degenerate: no bound
                need = nbits
                if si in sb and 0 < len(sb[si]) < nbits:
                    need = len(sb[si])        # tapered fan-in subset
                dir_enum = (buda.LayerDir.HORIZONTAL if cs.horiz
                            else buda.LayerDir.VERTICAL)
                pitches = []
                for lid in self.layers.get_layer_ids_by_dir(dir_enum):
                    # Per-cell layer policy: the best-case pitch must range
                    # over the ALLOWED layers only, or the bound is unsound
                    # for a governed bundle (hier_layer_caps.md F9).
                    if not w.input.allows_layer(lid):
                        continue
                    if (self.routing_grid is None
                            or not self.routing_grid.has_layer(lid)):
                        pitches = []
                        break                 # width-mode layer: no track bound
                    # has_layer() proves a RoutingGrid EXISTS, not that a
                    # global pattern set the per-track bit pitch: an
                    # add_grid_override on an undefined layer default-
                    # constructs the grid, and eff_bus_width then returns the
                    # width/dilution fallback — not a pitch — which must not
                    # be multiplied by a bit count (Codex P2 on #531).
                    pat = self.routing_grid.get_layer_grid(lid).global_pattern()
                    if not pat.slots or pat.signal_density() <= 0:
                        pitches = []
                        break                 # override-only / degenerate: stand down
                    pitches.append(self.layers.eff_bus_width(1, 1.0, lid))
                if not pitches:
                    continue
                if need * min(pitches) > win:
                    verdict = True
                    break
        except Exception:
            verdict = False                   # a gate must never break the healer
        memo[key] = verdict
        return verdict

    def _rr_candidate_order(self, w, old_tidx, stage, sites=None):
        """Alternate-candidate trial order for one contender (wishlist-healer
        item 4 + QoR-measured class re-rank): candidates whose
        same-orientation segments sit FARTHEST from the bundle's measured
        contention sites are tried first — they are the likeliest to move the
        offending wire out of the congested window, so the first-improving
        scan usually stops after one or two trials instead of walking all
        eight.  Candidates are WL-sorted (annotate_and_sort), so the legacy
        first-N pool restricts trials to the N cheapest estimates forever — a
        higher-estimate class (e.g. a two-level BITRUNK tree at index 20)
        could never be promoted no matter what the measured metric says.
        When contention sites exist, the top-N farness-ranked candidates from
        BEYOND the first-N window are therefore APPENDED after the legacy
        pool.  The caller's per-contender scan keeps the best measured
        metric over the whole move list with a STRICT `<` (ripup loop), so
        ordering cheap-first is load-bearing: an extra can displace a cheap
        fix only by a STRICTLY better (opens, overlaps) metric — at an equal
        metric the earlier (cheap) move always wins the tie.  That is
        precisely the QoR-gated promotion this exists for; where no extra
        strictly beats the cheap best, routes are unchanged (the golden
        corpora guard that — mix.buda is byte-identical, and big2's
        promotion clears its final residual overlap).  Farness-first over
        the WHOLE pool was tried and rejected: it put the far expensive
        candidate BEFORE the cheap same-effect one, handing it the tie
        (mix.buda bundle 85: idx 26 over idx 5, +2% abstract WL at an equal
        metric).  With no sites the legacy first-N pool is returned (nothing
        to rank by, no evidence to justify extra trials).

        `sites` overrides the bundle's OWN contention sites (same
        (is_horiz, perp_centre) shape): the global-occupant pass ranks a
        NON-contended occupant's candidates against the overlap it holds
        bands under — its own site list is empty by definition, and without
        the override the beyond-window promotion (the reach to b61-class
        window-infeasible candidates) would be lost.  None (the default)
        keeps the derive-from-own-contention behavior byte-identically."""
        cap = _RR_MAX_CANDIDATES_PER_BUNDLE
        # Group-pinned bundle (a super-candidate): ripup may only move it WITHIN
        # its pinned family — never to a candidate outside it, which would
        # silently break the user's `select_topology group:` / 'S' pin.  The
        # alternate set is therefore exactly the family members (no farness rank
        # or beyond-window promotion): each trial's replan already re-selects
        # within the family via the planner's `pinned_group` precedence, so the
        # members collapse to the same family-best and their order is immaterial.
        if getattr(w.input, 'pinned_group', None):
            return [i for i in w.input.pinned_group
                    if 0 <= i < len(w.input.candidates) and i != old_tidx]
        n = min(len(w.input.candidates), cap)
        idxs = [i for i in range(n) if i != old_tidx]
        # STATIC WIDTH GATE (issue #523): drop alternates that are dead on
        # arrival at DetailedNUTS — see _rr_width_infeasible.  Applied to the
        # auto trial set only (group pins returned above; the incumbent is
        # not in the list), logged once per bundle per run.
        gated = {i for i in idxs if self._rr_width_infeasible(w, i)}
        if gated:
            bid = w.input.original_bundle.id
            if bid not in self._rr_width_logged:
                self._rr_width_logged.add(bid)
                print(f"[ripup_reroute] width-gate: bundle {bid}: "
                      f"{len(gated)} candidate(s) statically width-infeasible "
                      f"(bit demand exceeds slide window) — excluded from "
                      f"trials.", flush=True)
            idxs = [i for i in idxs if i not in gated]
        if sites is None:
            sites = self._rr_contention_centres(stage,
                                                w.input.original_bundle.id)
        if not sites:
            return idxs
        extras = [i for i in range(n, len(w.input.candidates))
                  if i != old_tidx and not self._rr_width_infeasible(w, i)]
        def farness(i):
            cand = w.input.candidates[i]
            worst = None
            for horiz, centre in sites:
                best = None   # this candidate's closest same-orientation wire
                for seg in cand.segments:
                    seg_h = (seg.start.y == seg.end.y)
                    if seg_h != horiz:
                        continue
                    perp = seg.start.y if seg_h else seg.start.x
                    d = abs(perp - centre)
                    if best is None or d < best:
                        best = d
                if best is not None and (worst is None or best < worst):
                    worst = best
            return worst if worst is not None else 0.0
        return (sorted(idxs, key=farness, reverse=True)
                + sorted(extras, key=farness, reverse=True)[:cap])

    def _rr_screen_scores(self, w, tidxs):
        """Fixed-context screen scores for one contender's index alternates
        (RR round 3, the final lever — wishlist-healer "fixed-context
        single-bundle placement"; batched in round 5).

        Per candidate: pin it, replan its layers incrementally, then place
        ONLY this bundle's segments against every other bundle's baseline
        placement frozen as fixed occupancy (add_fixed_segments_except —
        the bottom-up fixed-segment machinery), doglegs and tighten skipped
        (the result is discarded; surgery and WL polish have no ordering
        value).  Score = (num_overlaps, num_violations) of the screened
        result.  Overlaps among the frozen context count too, but they are
        a CONSTANT within one contender's scan (only the target moves), so
        the scores are a valid ORDERING — never a metric: accept decisions
        always run on the true full-trial metric, which is what separates
        this screen from the reverted layer-scoped two-tier trials.

        The whole contender screens in ONE C++ call
        (NUTSEngine.screen_candidates): the wrapper list converts once
        instead of once per replan_bundle, every mutation lands on a
        C++-side copy — the session wrappers are untouched, so there is
        nothing to restore — and only (tidx, overlaps, violations) triples
        cross back instead of a full-design segment vector per candidate.
        Grid parity comes for free: run() sees the whole list, so its
        empty-grid fallback derives from the CURRENT selections including
        the pinned candidate — exactly a full trial's derivation, with no
        caller-side union/merging (this subsumes the round-3 per-pin
        fallback fix; the planner grids are still passed as extras for the
        populated-grid case, mirroring _run_nuts_internal).

        Returns {tidx: score}, or None when the incremental replan is
        unavailable for some candidate (the caller falls back to the
        unscreened order — a full trial there would fall back to the legacy
        full replan, which the screen cannot mirror)."""
        bid = w.input.original_bundle.id
        if self.planner is None or self.nuts_result is None:
            return None
        t0 = time.perf_counter()
        eng = buda.NUTSEngine(self.fp, self.layers)
        eng.set_track_pitch(self._nuts_pitch)
        eng.set_skip_tighten(True)
        eng.set_skip_doglegs(True)
        eng.set_extra_grid_points(list(self.planner.get_x_grid()),
                                  list(self.planner.get_y_grid()))
        # The baseline already carries any bottom-up fixed copies (run()
        # appends them), so this is the WHOLE frozen context — do not
        # also _inject_bottom_up_fixed here.
        eng.add_fixed_segments_except(self.nuts_result, bid)
        with contextlib.redirect_stdout(io.StringIO()), \
                buda.ostream_redirect():
            rows = eng.screen_candidates(self.bundles, bid, list(tidxs),
                                         self.planner,
                                         bid in self._dogleg_slot)
        # Charge per screened candidate so the timing bucket's count stays
        # the number of screens, comparable across rounds.
        dt, n = time.perf_counter() - t0, max(len(tidxs), 1)
        for _ in range(len(tidxs)):
            self._rr_t_add('screen', dt / n)
        if rows is None:
            return None
        return {t: (o, v) for t, o, v in rows}

    def _rr_screen_prune(self, w, idx_moves):
        """Split a contender's idx-move list into (kept, deferred) by
        screened score: the _RR_SCREEN_TOP_N best-screened moves are
        full-trialed now, the rest DEFERRED to the iteration's stall sweep
        (never dropped — completeness is the loop's, not the screen's).
        Ties keep the incoming farness/cheap-first position, so the screen
        may reorder only on evidence; with screening unavailable the full
        unscreened list is returned (nothing deferred)."""
        scores = self._rr_screen_scores(w, [mv[1] for mv in idx_moves])
        if scores is None:
            return idx_moves, []
        order = sorted(range(len(idx_moves)),
                       key=lambda i: (scores[idx_moves[i][1]], i))
        return ([idx_moves[i] for i in order[:_RR_SCREEN_TOP_N]],
                [idx_moves[i] for i in order[_RR_SCREEN_TOP_N:]])

    def _rr_sweep_threads(self):
        """The sweep pool size: BUDA_SWEEP_THREADS (explicit) wins; else the
        CLI's machine-wide governor BUDA_THREADS caps the pool; else 0 =
        hardware concurrency (resolved in C++).  A nonnumeric value falls
        back to 0 (auto) — the C++ engine env parsers are equally tolerant,
        and the sequential paths never consulted these vars at all (Codex
        #604)."""
        try:
            return int(os.environ.get("BUDA_SWEEP_THREADS")
                       or os.environ.get("BUDA_THREADS", "0") or 0)
        except ValueError:
            return 0

    def _rr_sweep_stage_setup(self, flat, stage, metric):
        """(base_disc, net_counts, dn_kwargs) for a parallel_sweep call over
        `flat` [(ci, bid, old_tidx, tidx)] — the stage-b DISCONNECTED
        decomposition (base = total minus the moved bundle's CURRENT
        contribution; other bundles' contributions cannot change with the
        move, and trial dogleg-adoption drift is excluded by the dogleg
        pass's non-severing guarantee, #405) plus the bottom-up DNUTS
        copy-plan port.  Stage a returns empties."""
        import buda
        base_disc, net_counts, dn_kwargs = {}, {}, {}
        if stage == 'b':
            total = self._rr_disconnected_bits()
            memo = getattr(self, "_rr_disc_memo", None) or {}
            for _ci, bid, _o, _t in flat:
                if bid in base_disc:
                    continue
                w = self._rr_wrapper(bid)
                nets = len(w.input.original_bundle.get_net_names())
                uid, _b = buda.selected_topo_key(w)
                contrib = nets if (uid and memo.get((uid, bid))) else 0
                base_disc[bid] = total - contrib
                net_counts[bid] = nets
            plan = self._bottom_up_dnuts_plan()
            if plan is not None:
                ref_ids, copy_specs, skip_ids = plan
                dn_kwargs.update(
                    ref_ids=set(ref_ids), skip_ids=set(skip_ids),
                    copy_specs=[tuple(cs) for cs in copy_specs],
                    horiz_of={(ts.bundle_id, ts.seg_idx): ts.horiz
                              for ts in self._bottom_up_fixed_segments()})
            self._install_leaf_keepouts()
            dn_kwargs.update(grid=self.routing_grid,
                             bit_order=self._detailed_bit_order,
                             abort_unplaced=self._rr_m_primary(metric()))
        return base_disc, net_counts, dn_kwargs

    def _rr_sweep_eval(self, flat, stage, base_disc, net_counts, dn_kwargs):
        """One parallel_sweep evaluation of `flat` — private wrapper/planner
        copies per move on C++ worker threads, GIL released; returns the
        per-move (prim, sec, ok) outcomes in flat order."""
        import buda
        t0 = time.perf_counter()
        outcomes = buda.parallel_sweep(
            self.bundles,
            [(bid, t) for _ci, bid, _o, t in flat],
            self.planner, self.fp, self.layers, self._nuts_pitch,
            self._bottom_up_fixed_segments(),
            list(self.planner.get_x_grid()),
            list(self.planner.get_y_grid()),
            stage == 'b',
            bool(getattr(self, '_rr_fast_trials', False)),
            set(self._dogleg_slot), base_disc, net_counts,
            n_threads=self._rr_sweep_threads(),
            **dn_kwargs)
        self._rr_t_add('psweep', time.perf_counter() - t0)
        return outcomes

    def _rr_parallel_scan_sweep(self, contenders, cur, stage, metric, snap,
                                deferred, screen, n_cont, it):
        """Parallel PRIMARY scan (rnr runtime P1b): evaluate the contenders'
        SCREENED kept moves — the trials the sequential first-improving loop
        runs one at a time — on the sweep pool, in visit-order chunks.

        LAZY like the sequential loop: a chunk's contenders are screened
        only when the chunk is reached, so an early improver leaves later
        contenders unscreened (the sequential loop's cost profile — an
        eager pre-pass measured 2x slower on improver-heavy flows), and
        their screened-out tails join `deferred` (the caller's list) only
        when actually scanned, exactly as the sequential scan accumulates
        it.

        Print- and decision-TRANSPARENT vs the sequential loop by
        construction: a contender none of whose moves improves per the
        sweep prints the sequential heartbeat and books the same trial
        count; the FIRST contender (visit order) with a sweep-improving or
        unevaluable move replays its ENTIRE kept list through the
        sequential _rr_scan_moves — the per-contender best-of-list trial,
        verbatim — so the committed move, the printed lines, and the trial
        counts all match the sequential scan exactly (the sweep's metrics
        only order the pick; a sweep-vs-replay disagreement is LOUD and
        the replay verdict wins).  Chunking bounds the wasted evaluations
        when an early contender improves while a stalled scan — the
        dominant case on grinding stage-b runs — gets the full pool win.
        Returns (best-or-None, trials)."""
        n_thr = self._rr_sweep_threads() or (os.cpu_count() or 1)
        chunk_moves = max(8, 2 * n_thr)
        trials = 0
        i, n_items = 0, len(contenders)
        while i < n_items:
            # Build (and screen) just enough contenders to fill this chunk.
            group = []               # (ci, bid, old_tidx, kept moves)
            nmv = 0
            while i < n_items and nmv < chunk_moves:
                ci, bid = i + 1, contenders[i]
                i += 1
                w = self._rr_wrapper(bid)
                if w is None:
                    continue
                old_tidx = snap['wrap'][bid][0]
                moves = [('idx', t)
                         for t in self._rr_candidate_order(w, old_tidx,
                                                           stage)]
                if screen and len(moves) > _RR_SCREEN_TOP_N:
                    moves, rest = self._rr_screen_prune(w, moves)
                    if rest:
                        deferred.append((ci, bid, old_tidx, rest))
                # A zero-move contender stays in the group so its sequential
                # heartbeat still prints in visit order (Codex #604).
                group.append((ci, bid, old_tidx, moves))
                nmv += len(moves)
            if not group:
                continue
            flat = [(ci, bid, old, t)
                    for ci, bid, old, moves in group
                    for _k, t in moves]
            if flat:
                base_disc, net_counts, dn_kwargs = \
                    self._rr_sweep_stage_setup(flat, stage, metric)
                outcomes = self._rr_sweep_eval(flat, stage, base_disc,
                                               net_counts, dn_kwargs)
            else:
                outcomes = []
            oi = 0
            for ci, bid, old_tidx, moves in group:
                outs = outcomes[oi:oi + len(moves)]
                oi += len(moves)
                w = self._rr_wrapper(bid)
                if w is None:
                    continue
                sweep_improving = False
                for (_kind, _t), (prim, sec, ok) in zip(moves, outs):
                    if not ok:
                        sweep_improving = True      # unevaluable: replay decides
                        break
                    m = (prim, sec) if stage == 'b' else prim
                    if m < cur:
                        sweep_improving = True
                        break
                if sweep_improving:
                    cand_best, t2 = self._rr_scan_moves(
                        w, bid, old_tidx, moves, cur, stage, metric, snap,
                        trial_base=trials)
                    trials += t2
                    if cand_best is not None:
                        print(f"[ripup_reroute] iter {it}: contender "
                              f"{ci}/{n_cont} bundle {bid} improves "
                              f"{self._rr_m_str(cur)}->"
                              f"{self._rr_m_str(cand_best[0])} "
                              f"({self._rr_move_str(old_tidx, cand_best[3])})",
                              flush=True)
                        return cand_best, trials
                    if all(ok for _p, _s, ok in outs):
                        print(f"[ripup_reroute] WARNING: parallel-sweep "
                              f"divergence on bundle {bid} (screened scan) "
                              f"— replay verdict kept", flush=True)
                else:
                    trials += len(moves)   # the sequential trials these replace
                print(f"[ripup_reroute] iter {it}: contender {ci}/{n_cont} "
                      f"bundle {bid} — no improvement", flush=True)
        return None, trials

    def _rr_parallel_deferred_sweep(self, deferred, cur, stage, metric,
                                    snap, n_cont, it):
        """Parallel stall-certificate sweep (rnr runtime P1,
        trial_sweep.cpp): evaluate every deferred ('idx', tidx) move on C++
        worker threads against the committed baseline — private wrapper/
        planner copies per move, GIL released — with metrics implementing
        the sequential fast-trial semantics exactly (stage-a skip_tighten;
        stage-b vias-off + plain-path abort; the moved bundle's
        DISCONNECTED term on the caller-side base decomposition).  Walks
        the outcomes in the sequential visit order: the first in-order
        strict improver is REPLAYED through the normal single-move
        sequential trial (its result is the accept basis and the committed
        state — the sweep's metrics only order the pick and carry the
        stall certificate), and a move the workers could not evaluate
        (incremental replan unavailable) is trialed sequentially at its
        original position.  A sweep-vs-replay disagreement is LOUD and the
        replay verdict wins.  Returns (best-or-None, trials)."""
        flat = []                     # (ci, bid, old_tidx, tidx)
        for ci, bid, old_tidx, moves in deferred:
            for kind, t in moves:
                if kind != 'idx':     # structurally impossible today
                    continue
                flat.append((ci, bid, old_tidx, t))
        if not flat:
            return None, 0
        base_disc, net_counts, dn_kwargs = \
            self._rr_sweep_stage_setup(flat, stage, metric)
        print(f"[ripup_reroute] iter {it}: screened scan stalled — "
              f"sweeping {len(flat)} deferred move(s) in parallel",
              flush=True)
        outcomes = self._rr_sweep_eval(flat, stage, base_disc, net_counts,
                                       dn_kwargs)
        trials = 0
        for (ci, bid, old_tidx, tidx), (prim, sec, ok) in zip(flat,
                                                              outcomes):
            if ok:
                m = (prim, sec) if stage == 'b' else prim
                if os.environ.get("BUDA_RR_TRACE"):
                    print(f"[rr-trace] psweep bundle {bid} topo "
                          f"{old_tidx + 1}->{tidx + 1}: "
                          f"{self._rr_m_str(m)}", flush=True)
                if not (m < cur):
                    trials += 1   # counted as the sequential trial it replaces
                    continue
                # Improving: do NOT count the sweep eval — the replay below
                # is the trial (keeps the done-line trial count identical to
                # the sequential path).
            # Improving per the sweep (or unevaluable): the sequential
            # single-move trial decides — and produces the committed state.
            w = self._rr_wrapper(bid)
            if w is None:
                continue
            cand_best, t2 = self._rr_scan_moves(w, bid, old_tidx,
                                                [('idx', tidx)], cur,
                                                stage, metric, snap,
                                                trial_base=trials,
                                                first_improving=True)
            trials += t2
            if cand_best is not None:
                print(f"[ripup_reroute] iter {it}: contender "
                      f"{ci}/{n_cont} bundle {bid} improves "
                      f"{self._rr_m_str(cur)}->"
                      f"{self._rr_m_str(cand_best[0])} "
                      f"({self._rr_move_str(old_tidx, cand_best[3])}"
                      f", deferred)", flush=True)
                return cand_best, trials
            if ok:
                print(f"[ripup_reroute] WARNING: parallel-sweep divergence "
                      f"on bundle {bid} topo {old_tidx + 1}->{tidx + 1} "
                      f"(sweep {self._rr_m_str(m)} vs replay non-improving) "
                      f"— replay verdict kept", flush=True)
        return None, trials

    # ── measured selection refine (selection-basis lever 3) ─────────────────
    def _refine_selection(self, max_moves=30, chase_overlaps=False):
        """Re-rank SELECTIONS on measured routability, not the generation-time
        WL estimate (wishlist-planner "Selection basis", the deferred lever;
        levers 1 kPeak and 2 ripup pool re-rank shipped earlier).  The two
        existing measured loops cannot close this gap: ripup's metric is
        overlap/opens-only (it stops at parity and never improves WL), and
        the planner's refine_passes re-scores through the COST MODEL whose
        WL term is the estimate — so a candidate that ROUTES shorter or
        cleaner than it estimates (the BITRUNK/spine realization gap the
        2026-07-30 default-flip study measured) structurally loses.

        After run_nuts, sweep every eligible bundle: screen ALL its
        alternate candidates against the other bundles' frozen placement
        (the P1 fixed-context screen — ordering only, never a metric),
        full-trial the top-2 screened, and adopt on a strictly better
        MEASURED lexicographic (NUTS overlaps, realized abstract WL) —
        realized WL = the placed spans' total length, the post-settle
        geometry the estimate only approximates.  Trials run FULL (fast
        trials forced off for the pass: tighten_pulls is WL-only, so a
        tighten-skipped trial's WL would be biased against the move), which
        also makes every winning index trial forward-restorable — commits
        reuse ripup's snapshot-restore + recharge path.  Skips: USER-pinned
        bundles (inviolable), hier.locked bottom-up copies (uniform
        templates), single-candidate pools.  Deterministic: bundle order is
        the session list, first strictly-improving trial per bundle, sweeps
        until a full sweep commits nothing or the move budget is spent.

        Stage-aware like the healers, and meant to run AFTER them (the
        end-of-flow WL polish): both pre-healer placements were measured
        to perturb the healers' basins (even overlap-parity selection
        changes shifted their trajectories — bottomup 0->16 opens under
        the WL-polish accept), so the default accept is componentwise —
        opens, overlaps AND interval violations PARITY-OR-BETTER with WL
        strictly lower — which
        makes an endpoint regression impossible by construction.  The
        `chase_overlaps` token switches to the plain lexicographic accept
        (the aggressive pre-healer form; measured mixed on the vehicles)."""
        self._healers_ran = True   # past-healer stamp (reseat-heal gate)
        self._healers_ran_cycle = True   # ...and for THIS routing cycle
        #   (pair-align gate; cleared by a fresh run_planner)
        if not self.bundles:
            print("Error: refine_selection has no bundles.")
            return
        if self.planner is None or self.nuts_result is None:
            print("Error: refine_selection needs run_planner + run_nuts "
                  "to have run first.")
            return

        def wl_now():
            return int(round(sum(abs(ts.span_hi - ts.span_lo)
                                 for ts in self.nuts_result.segments
                                 if ts.placed)))

        # Stage-aware like the healers: after run_detailed_nuts the metric
        # carries the DNUTS opens (+ severed-bus bits) ahead of the abstract
        # overlaps, so the accept guard can protect the HEALED endpoint
        # componentwise; before it, (overlaps, WL) as in the stage-a form.
        stage = 'b' if self.detailed_result is not None else 'a'
        # Interval violations (a segment committed OUTSIDE its hard Hanan
        # interval — the exhausted-window fallback) are a reported routing
        # failure the healers' own metrics don't carry; a WL win must not
        # smuggle one in, so they get their own parity component (review
        # #525).
        if stage == 'b':
            metric = lambda: (self.detailed_result.num_unplaced  # noqa: E731
                              + self._rr_disconnected_bits(),
                              self.nuts_result.num_overlaps,
                              self.nuts_result.num_violations, wl_now())

            def m_str(m):
                return f"{m[0]} open (ovl {m[1]}, viol {m[2]}, wl {m[3]})"

            def accept(m, cur):
                # componentwise no-worse on opens, overlaps AND interval
                # violations, WL strictly better (chase_overlaps: plain
                # lexicographic)
                if chase_overlaps:
                    return m < cur
                return (m[0] <= cur[0] and m[1] <= cur[1]
                        and m[2] <= cur[2] and m[3] < cur[3])
        else:
            metric = lambda: (self.nuts_result.num_overlaps,  # noqa: E731
                              self.nuts_result.num_violations, wl_now())

            def m_str(m):
                return f"{m[0]} ov (viol {m[1]}, wl {m[2]})"

            def accept(m, cur):
                if chase_overlaps:
                    return m < cur
                return (m[0] <= cur[0] and m[1] <= cur[1] and m[2] < cur[2])

        saved_fast = getattr(self, '_rr_fast_trials', False)
        self._rr_fast_trials = False       # WL-fair full trials + fwd snapshots
        self._rr_t_init()
        m0 = metric()
        cur = m0
        committed = 0
        n_trials = 0
        try:
            sweeping = True
            while sweeping and committed < max_moves:
                sweeping = False
                for w in list(self.bundles):
                    if committed >= max_moves:
                        break
                    if w.input.topology_pinned:
                        continue                     # user pin inviolable
                    if w.hier.locked:
                        continue                     # bottom-up fixed copy
                    n = len(w.input.candidates)
                    old = w.plan.selected_topology_index
                    if n < 2 or old < 0:
                        continue
                    bid = w.input.original_bundle.id
                    snap = self._rr_snapshot()
                    alts = [t for t in range(n) if t != old]
                    scores = self._rr_screen_scores(w, alts)
                    if scores is not None:
                        alts.sort(key=lambda t: (scores[t][0], scores[t][1],
                                                 t))
                    accepted = None
                    for tidx in alts[:_RR_SCREEN_TOP_N]:
                        move = ('idx', tidx)
                        m = self._rr_guarded_move(w, move, old, stage,
                                                  metric, snap)
                        # WL polish (default): adopt only when opens and
                        # overlaps are PARITY-or-better componentwise with
                        # strictly shorter realized WL — the healers' work
                        # is never traded for length.  chase_overlaps:
                        # plain lexicographic (an aggressive pre-healer —
                        # measured mixed on the vehicles, see the wishlist
                        # entry).
                        take = m is not None and accept(m, cur)
                        # Full trials (fast off) => the trial state is a
                        # full-pipeline state: capture it as the commit's
                        # forward snapshot before restoring.
                        fwd = self._rr_snapshot() if take else None
                        self._rr_undo_move(w, move, old)
                        self._rr_restore(snap, only=self._rr_dirty)
                        n_trials += 1
                        if take:
                            accepted = (m, move, fwd)
                            break
                    if accepted is None:
                        continue
                    m_new, move, fwd = accepted
                    if fwd is not None and self._rr_fwd_ok(snap, fwd):
                        self._rr_restore(fwd)
                    else:
                        self._rr_apply_move(w, move, old, stage, metric,
                                            full=True)
                    self.planner.recharge_committed(self.bundles)
                    committed += 1
                    sweeping = True
                    print(f"[refine_selection] COMMIT bundle {bid} "
                          f"{self._rr_move_str(old, move)}, metric "
                          f"{m_str(cur)}->"
                          f"{m_str(metric())}", flush=True)
                    cur = metric()
            print(f"[refine_selection] done: metric {m_str(m0)}->"
                  f"{m_str(metric())} after {committed} move(s), "
                  f"{n_trials} trial(s).", flush=True)
            print(f"[refine_selection] timing: {self._rr_t_str()}",
                  flush=True)
            self._checkpoint_routing()
        finally:
            self._rr_fast_trials = saved_fast
            self._rr_t = None
            self._rr_disc_memo = {}

    def _gen_hv(self):
        """The generator's top H / top V layer ids (used to hint flipped MST legs
        by direction, mirroring _make_topo_gen)."""
        return (self.layers.get_top_layer(buda.LayerDir.HORIZONTAL),
                self.layers.get_top_layer(buda.LayerDir.VERTICAL))

    def _rr_global_sites(self, stage):
        """Contention rectangles for the global-occupant pass, one per
        measured failure: (layer, span_lo, span_hi, perp_lo, perp_hi).
        Stage a: the NUTS overlap rectangles.  Stage b: the open segments'
        placed windows (the same sites negotiate_congestion injects) PLUS
        the NUTS overlap rectangles — the lexicographic metric keeps
        grinding collateral overlaps after the opens hit 0, and a stall in
        that regime (or one whose opens coexist with abstract overlaps)
        must still surface its overlap sites or the pass is silently
        inert (review #287)."""
        sites = []
        if self.nuts_result is None:
            return sites
        if stage == 'b':
            ts_map = {(ts.bundle_id, ts.seg_idx): ts
                      for ts in self.nuts_result.segments}
            for bid, si, _missing, _exp in self._open_segments():
                ts = ts_map.get((bid, si))
                if ts is None:
                    continue
                sites.append((ts.layer,
                              min(ts.span_lo, ts.span_hi),
                              max(ts.span_lo, ts.span_hi),
                              ts.interval_lo, ts.interval_hi))
        for od in self.nuts_result.overlap_details:
            sites.append((od.layer, od.span_lo, od.span_hi,
                          od.perp_lo, od.perp_hi))
        return sites

    def _rr_global_pass(self, stage, metric, snap, cur, exclude):
        """Global-occupant pass (wishlist-healer "global-overlap re-route of
        NON-contended bundles" — the big2 b61 class).  Runs only when the
        normal first-improving contender scan stalls above zero: a bundle
        that appears in no overlap/open can still HOLD the contended bands,
        and moving IT can be the global fix the contended bundles' own
        alternates cannot reach.

        Per remaining contention site: rank the committed bundles by their
        demand on the site's bands (planner.band_occupants — the
        replan_bundle_ripup victim ranking, exposed read-only), and trial
        each occupant's index alternates ranked against THE SITE's location
        (`sites=` override — the occupant itself is non-contended, so its own
        site list is empty and the beyond-window promotion would be lost).
        A trial rides the existing pinned `_rr_trial` path, whose replan
        ladder ends in BEST_EFFORT — so a window-infeasible candidate
        (STRICT-rejected at plan time; exactly b61's winning TRUNK_H+MST) is
        reachable, and it commits only on a STRICTLY better MEASURED metric.

        This is the complement of negotiate's `replan_bundle_ripup` victim
        stage, not a duplicate: that stage triggers only for a CONTENDED,
        STRICT-infeasible target, moves victims via unpinned-STRICT replans
        (window-infeasible candidates unreachable by construction), and
        accepts on planner-model pair-feasibility; this pass is
        measured-metric-driven, occupant-first, and pinned-trial.

        Budgets: top-K occupants per site, M moves per occupant, and a hard
        per-stall trial cap.  First strict improvement wins (the main
        scan's philosophy at the main scan's trial cost).  Like the
        contender scan, the pass may override a `topology_pinned`
        occupant's pin (ripup is an explicit congestion-fix pass; only a
        hier.locked template copy is inviolable).  Returns
        (cand_best-or-None, trials) in the main loop's tuple shape."""
        if self.planner is None:
            return None, 0
        sites = self._rr_global_sites(stage)
        if not sites:
            return None, 0
        print(f"[ripup_reroute] GLOBAL pass: contenders stalled at "
              f"{self._rr_m_str(cur)} — ranking band occupants of "
              f"{len(sites)} contention site(s)", flush=True)
        h_layers = set(self.layers.get_layer_ids_by_dir(
            buda.LayerDir.HORIZONTAL))
        trials = 0
        tried = set()
        for (layer, s_lo, s_hi, p_lo, p_hi) in sites:
            # The site's own overlap parties charge its bands by
            # construction (their committed seg_perp IS the contended
            # band), so a top-K C++ truncation would hand them the slots
            # and starve the genuinely NON-contended holder the pass
            # exists to find (review #287).  Request enough entries that
            # K real occupants can survive the Python-side exclusion —
            # including the cross-site `tried` dedupe, which also consumes
            # ranking slots at later sites — then cap the TRIALED
            # occupants per site at K here.
            # Honest-books mode (charge_pull_target): rank occupants by where
            # the metal actually IS — the charge prediction can diverge from
            # the placed track under contention fallback (and the whole point
            # of the mode is that the charge moved off the legacy anchor), so
            # the plan-based ranking would miss the bundle physically holding
            # the site's bands.  Off: empty overlay = legacy plan ranking.
            placed = []
            if (self._planner_params.get("charge_pull_target", 0.0) != 0.0
                    and self.nuts_result is not None):
                placed = [(ts.bundle_id, ts.seg_idx,
                           int(round(ts.track_position)))
                          for ts in self.nuts_result.segments if ts.placed]
            occ = self.planner.band_occupants(
                self.bundles, layer, s_lo, s_hi, p_lo, p_hi,
                _RR_GLOBAL_TOP_K + len(exclude) + len(tried),
                placed)
            site = [(layer in h_layers, 0.5 * (p_lo + p_hi))]
            site_occupants = 0
            for bid, _demand in occ:
                if site_occupants >= _RR_GLOBAL_TOP_K:
                    break
                if bid in exclude or bid in tried:
                    continue
                w = self._rr_wrapper(bid)
                if (w is None or w.hier.locked
                        or len(w.input.candidates) < 2):
                    continue
                tried.add(bid)
                site_occupants += 1
                old_tidx = snap['wrap'][bid][0]
                for tidx in self._rr_global_moves(w, old_tidx, stage, site):
                    if trials >= _RR_GLOBAL_MAX_TRIALS:
                        return None, trials
                    m = self._rr_guarded_move(w, ('idx', tidx), old_tidx,
                                              stage, metric, snap)
                    fwd = (self._rr_snapshot()
                           if m < cur and not getattr(
                               self, '_rr_fast_trials', False) else None)
                    self._rr_restore(snap, only=self._rr_dirty)
                    trials += 1
                    if m < cur:
                        print(f"[ripup_reroute] GLOBAL: occupant bundle "
                              f"{bid} improves {self._rr_m_str(cur)}->"
                              f"{self._rr_m_str(m)} "
                              f"(topo {old_tidx + 1}->{tidx + 1})",
                              flush=True)
                        return (m, bid, old_tidx, ('idx', tidx), fwd), trials
        return None, trials

    def _rr_global_moves(self, w, old_tidx, stage, site):
        """The global pass's per-occupant move budget, extras GUARANTEED.

        _rr_candidate_order returns the in-window pool (farness-ranked)
        followed by the promoted beyond-window extras.  A plain head-slice
        of _RR_GLOBAL_MOVES_PER_OCC would be all in-window whenever the
        occupant has more than _RR_MAX_CANDIDATES_PER_BUNDLE candidates —
        starving exactly the b61-class beyond-window candidates this pass
        exists to reach (Codex #287 P1).  Split the budget instead: the
        extras take up to half the slots (as many as exist), the in-window
        pool fills the rest and stays FIRST — the first-improving accept
        then still prefers a cheap fix when one improves."""
        order = self._rr_candidate_order(w, old_tidx, stage, sites=site)
        n = min(len(w.input.candidates), _RR_MAX_CANDIDATES_PER_BUNDLE)
        in_win = [t for t in order if t < n]
        extras = [t for t in order if t >= n]
        k_ex = min(_RR_GLOBAL_MOVES_PER_OCC // 2, len(extras))
        return (in_win[:_RR_GLOBAL_MOVES_PER_OCC - k_ex] + extras[:k_ex])

    # ---- Bottom-up template CLASS moves (stall tier 3) ------------------
    # docs/internal/bottomup_healer_templates.md, plan item A.  A
    # `hier.locked` wrapper is a bottom-up template instance: its routing is
    # a uniform fixed copy of the cell template's local solve, so no
    # per-instance move exists — every contender/global pass skips it, and a
    # design whose residual contention sits ON a locked bundle is stuck (the
    # mix2_fast_bottomup 2-overlap/16-open plateau).  The class pass moves
    # the TEMPLATE instead: force-pin an alternate candidate on the template,
    # re-run the cell-local solve for correct layers (the pin is kept, the
    # planner only assigns layers), propagate the pin to every instance of
    # the class, invalidate the fixed-copy caches, and measure a full
    # pipeline re-run — one move re-routes ALL instances of the class, and
    # it commits only on a strictly better measured metric.

    def _rr_template_wrapper(self, tid):
        """The pre-expansion TEMPLATE wrapper for a template bundle id
        (self.bundles holds only the expanded per-instance wrappers)."""
        for w in getattr(self, "_hier_bundles_orig", None) or []:
            if w.input.original_bundle.id == tid:
                return w
        return None

    def _rr_locked_contenders(self, stage):
        """Contender bundle ids RESTRICTED to hier.locked wrappers — the
        exact complement of _rr_contenders' locked-skip, same source walk
        (_rr_contention_sources) and order, and no any-bundle fallback:
        with no locked contention there is no class to move."""
        order, seen = [], set()
        for bid in self._rr_contention_sources(stage):
            if bid in seen:
                continue
            seen.add(bid)
            w = self._rr_wrapper(bid)
            if w is not None and w.hier.locked:
                order.append(bid)
        return order

    def _rr_class_cell_wrappers(self):
        """cell_context -> [template wrappers] for every bottom-up cell —
        the same grouping (incl. the replica filter) as
        _plan_bottom_up_templates, so a class trial's per-cell re-plan runs
        on exactly the wrapper set the original local solve did."""
        bu_cells = set(self.bdb.bottom_up_cells()) if self.bdb else set()
        templates = getattr(self, "_hier_bundles_orig", None) or []
        if not bu_cells or not templates:
            return {}
        cell_ids = {w.input.original_bundle.id for w in templates
                    if w.input.original_bundle.cell_context}
        by_cell = {}
        for w in templates:
            b = w.input.original_bundle
            if (not b.cell_context or not b.instances
                    or self._bu_cell_of(b.cell_context) not in bu_cells
                    or b.parent_id in cell_ids            # replica
                    or not w.input.candidates):
                continue
            by_cell.setdefault(b.cell_context, []).append(w)
        return by_cell

    def _rr_class_snapshot(self):
        """The base trial snapshot EXTENDED with the template-side state a
        class trial mutates: per-template plan/pin state (same 11-tuple
        shape as snap['wrap']), the bottom-up dogleg bookkeeping (slot map,
        pre-split selections, and a REAL copy of each adopted slot's
        Topology — the trial's fixed-copy recompute may overwrite the slot
        in place, exactly the base snapshot's dl_cand hazard), the
        cell-local planner grids, and the derived caches (fixed copies,
        track verdict, DNUTS copy plan, resume preference)."""
        snap = self._rr_snapshot()
        templates = getattr(self, "_hier_bundles_orig", None) or []
        snap['tmpl'] = {w.input.original_bundle.id:
                        (w.plan.selected_topology_index,
                         w.input.topology_pinned,
                         len(w.input.candidates),
                         list(w.plan.seg_layers), list(w.plan.seg_perp),
                         list(w.plan.seg_net_pull),
                         list(w.plan.seg_slide_lo),
                         list(w.plan.seg_slide_hi),
                         w.input.assigned_v_layer, w.input.assigned_h_layer,
                         list(w.input.pinned_seg_layers))
                        for w in templates}
        snap['bu_dl_slot'] = dict(getattr(self, "_bu_dogleg_slot", None)
                                  or {})
        snap['bu_dl_orig'] = dict(getattr(self, "_bu_dogleg_originals", None)
                                  or {})
        snap['bu_dl_cand'] = {}
        for tid, slot in snap['bu_dl_slot'].items():
            w = self._rr_template_wrapper(tid)
            if w is not None and 0 <= slot < len(w.input.candidates):
                snap['bu_dl_cand'][tid] = copy.copy(w.input.candidates[slot])
        snap['bu_grids'] = dict(getattr(self, "_bu_planner_grids", None)
                                or {})
        # hier.locked is written by _rr_class_apply's propagation and is NOT
        # in the base snapshot's wrap tuple.  True->True in the happy path,
        # but if the cell re-plan returns no assignment for the moved
        # template its pinned_seg_layers stay empty and locked flips False —
        # a REJECTED trial must not leave the class permanently unlocked
        # (issue #475).
        snap['locked'] = {w.input.original_bundle.id: bool(w.hier.locked)
                          for w in self.bundles}
        snap['bu_fixed'] = getattr(self, "_bu_fixed_cache", None)
        snap['bu_verdict'] = getattr(self, "_template_track_verdict", None)
        snap['bu_dnuts_plan'] = getattr(self, "_bu_dnuts_plan_cache", None)
        snap['bu_from_resume'] = getattr(self, "_bu_fixed_from_resume",
                                         False)
        return snap

    def _rr_class_restore(self, snap):
        """Restore a rejected class trial: the base restore (expanded
        wrappers, results, instance dogleg bookkeeping) plus the template
        side.  A class trial can only GROW a template pool (the fixed-copy
        recompute's _adopt_bottom_up_doglegs appends or overwrites its
        slot; _reset_bottom_up_doglegs — the only deleter — never runs in a
        trial), so the pool restore is trim + slot-content overwrite."""
        self._rr_restore(snap)
        for w in self.bundles:               # issue #475: see snapshot note
            lk = snap['locked'].get(w.input.original_bundle.id)
            if lk is not None:
                w.hier.locked = lk
        for w in getattr(self, "_hier_bundles_orig", None) or []:
            tid = w.input.original_bundle.id
            cap = snap['tmpl'].get(tid)
            if cap is None:
                continue
            (sel, pinned, ncand, seg_layers, seg_perp,
             seg_net_pull, seg_slide_lo, seg_slide_hi, av, ah,
             pinned_layers) = cap
            cands = w.input.candidates
            while len(cands) > ncand:    # drop trial-appended dogleg splits
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
        # Put back the committed split geometry where the trial's recompute
        # overwrote an adopted template slot in place (same count, new
        # content — the trim above cannot see it).
        for tid, topo in snap['bu_dl_cand'].items():
            slot = snap['bu_dl_slot'].get(tid)
            w = self._rr_template_wrapper(tid)
            if w is None or slot is None:
                continue
            cands = w.input.candidates
            if 0 <= slot < len(cands):
                cands[slot] = topo
                w.input.candidates = cands
        self._bu_dogleg_slot = dict(snap['bu_dl_slot'])
        self._bu_dogleg_originals = dict(snap['bu_dl_orig'])
        self._bu_planner_grids = dict(snap['bu_grids'])
        # The pre-trial fixed-copy cache is the committed state's — putting
        # the object back (instead of None) spares the next trial a full
        # local re-solve; it stays consistent because the template pins it
        # was computed from were just restored above.
        self._bu_fixed_cache = snap['bu_fixed']
        self._template_track_verdict = snap['bu_verdict']
        self._bu_dnuts_plan_cache = snap['bu_dnuts_plan']
        self._bu_fixed_from_resume = snap['bu_from_resume']

    def _rr_class_apply(self, tw, cell, cell_wrappers, tidx):
        """Apply one class move: force-pin candidate `tidx` on template
        `tw`, re-run the cell-local solve (the pin is kept — the local
        planner only assigns layers for it, and re-decides the OTHER
        templates of the cell under the new congestion picture; their own
        pins keep their selections), propagate the resulting pins to every
        instance wrapper of the cell, and invalidate the caches derived
        from the template geometry.  BDB persistence is deferred to the
        accept path (_plan_bottom_up_cell persist=False + the trial-guarded
        dogleg adoption)."""
        tw.input.topology_pinned = True
        tw.plan.selected_topology_index = tidx
        # NOTE: if the template's committed selection was its adopted
        # dogleg slot, _bu_dogleg_originals[tid] still records the
        # PRE-SPLIT selection after this move — a later
        # _reset_bottom_up_doglegs would restore that instead of tidx.
        # Harmless by construction: its only caller is a full
        # _plan_bottom_up_templates re-plan, which re-decides every
        # selection anyway.
        # The old candidate's per-segment state must not leak onto the new
        # one: pinned layers are honored for ANY candidate (wrong layers),
        # and a same-segment-count dogleg override array would silently
        # apply (build_nuts_maps only checks lengths).  The local solve
        # below re-fills layers/perp from its assignment.
        tw.input.pinned_seg_layers = []
        tw.plan.seg_net_pull = []
        tw.plan.seg_slide_lo = []
        tw.plan.seg_slide_hi = []
        iters = (getattr(self, "_bu_local_iterations", None)
                 or self._planner_iterations)
        with contextlib.redirect_stdout(io.StringIO()), \
                buda.ostream_redirect():
            self._plan_bottom_up_cell(cell, cell_wrappers, iters,
                                      persist=False)
        # Propagate the new pin to the MOVED template's instances only —
        # the other templates of the cell kept their pins + pinned layers
        # (honored by the local planner), so their instances' committed
        # state (including any adopted dogleg-slot selection, which does
        # NOT equal the template index) must not be touched.
        self._rr_propagate_template_pin(tw)
        self._rr_invalidate_bottom_up_caches()

    def _rr_propagate_template_pin(self, tw):
        """Copy template `tw`'s pin (index + layers) to every instance
        wrapper of its class.  Candidate indices are preserved by
        expansion, and layer ids are frame-independent, so index + layers
        copy directly; the per-segment dogleg overrides are cleared
        exactly as _rr_trial does when moving a bundle off its split (the
        class snapshot restores them on rejection).  seg_perp is cleared
        too — NUTS never reads it for a locked bundle (its routing is the
        fixed copies), but the planner's recharge (commit_plan via
        recharge_committed / replan_bundle) consumes it as a per-segment
        band-charge override for the SELECTED candidate, and the stale
        values index the OLD candidate's geometry — cleared, each new
        segment charges at its own nominal perp, the convention for any
        wrapper without overrides (Codex #472 P1).  Mirrors
        _expand_hier_bundles' pin propagation + locked rule (bottom-up
        cells never expand 90°-rotated instances; the rotation classes
        have their own clone templates)."""
        exp_map = getattr(self, "_hier_expansion_map", None) or {}
        for iw in exp_map.get(tw.input.original_bundle.id, []):
            iw.input.topology_pinned = True
            iw.plan.selected_topology_index = \
                tw.plan.selected_topology_index
            iw.input.pinned_seg_layers = list(tw.input.pinned_seg_layers)
            iw.plan.seg_layers = list(tw.plan.seg_layers)
            iw.plan.seg_perp = []
            iw.plan.seg_net_pull = []
            iw.plan.seg_slide_lo = []
            iw.plan.seg_slide_hi = []
            iw.hier.locked = (tw.input.topology_pinned
                              and bool(tw.input.pinned_seg_layers))

    def _rr_invalidate_bottom_up_caches(self):
        """Template geometry changed — every cache derived from it is
        stale.  A trial's re-run recomputes the fixed copies (and
        re-adopts any dogleg) under _rr_in_trial, so nothing reaches the
        BDB until the accept path replays the deferred persistence."""
        self._bu_fixed_cache = None
        self._template_track_verdict = None
        self._bu_dnuts_plan_cache = None
        self._bu_fixed_from_resume = False

    def _rr_class_maps(self):
        """(by_cell, cell_of, tmpl_of) for the bottom-up template classes:
        cell -> template wrappers, canonical template id -> cell, and
        instance-wrapper bid -> CANONICAL template id.  exp_map also
        aliases every REPLICA bundle id to its instance's wrapper
        (inserted after the canonical entries by _expand_hier_bundles), so
        the walk skips non-canonical tids — an unfiltered walk would
        overwrite the canonical mapping with the replica id and a locked
        contender on that wrapper would silently skip its class
        (Codex #472 P1)."""
        by_cell = self._rr_class_cell_wrappers()
        cell_of = {w.input.original_bundle.id: cell
                   for cell, ws in by_cell.items() for w in ws}
        exp_map = getattr(self, "_hier_expansion_map", None) or {}
        tmpl_of = {}
        for tid, iws in exp_map.items():
            if tid not in cell_of:            # replica alias / non-bottom-up
                continue
            for iw in iws:
                tmpl_of[iw.input.original_bundle.id] = tid
        return by_cell, cell_of, tmpl_of

    def _rr_class_moves(self, tw, iw, stage):
        """Alternate template candidate indices for one class, best-first:
        ranked by the CONTENDED INSTANCE's measured contention sites (its
        coordinates are absolute — the template's are cell-local), which is
        valid on the template pool because expansion preserves candidate
        order.  Excludes indices past the template pool (instance-local
        dogleg appends), the template's own adopted dogleg slot (a split of
        the OLD selection), and the current selection; capped at
        _RR_CLASS_TOP_N."""
        old_inst = iw.plan.selected_topology_index
        order = self._rr_candidate_order(iw, old_inst, stage)
        ntc = len(tw.input.candidates)
        old_t = tw.plan.selected_topology_index
        bu_slot = (getattr(self, "_bu_dogleg_slot", None)
                   or {}).get(tw.input.original_bundle.id)
        return [t for t in order
                if t < ntc and t != old_t and t != bu_slot
                ][:_RR_CLASS_TOP_N]

    def _rr_class_pass(self, stage, metric, cur):
        """Template-class move pass, the stall chain's last tier (after the
        global-occupant pass): for each locked-contender CLASS, trial up to
        _RR_CLASS_TOP_N alternate template candidates via
        apply -> full re-run -> measure, committing the first strictly
        better one (the trial state IS a full-pipeline state — kept as-is,
        with the deferred BDB persistence replayed outside the trial flag).
        Returns (committed, trials).  No-op (False, 0) on flat flows and on
        designs whose residual contention holds no locked bundle — routes
        byte-identical.  A template the USER pinned before the local solve
        is never moved (_bu_user_pinned provenance)."""
        if not getattr(self, "_planner_is_hier", False):
            return False, 0
        exp_map = getattr(self, "_hier_expansion_map", None) or {}
        if not exp_map:
            return False, 0
        locked = self._rr_locked_contenders(stage)
        if not locked:
            return False, 0
        by_cell, cell_of, tmpl_of = self._rr_class_maps()
        if not by_cell:
            return False, 0
        user_pinned = getattr(self, "_bu_user_pinned", None) or set()
        classes, seen = [], set()
        for bid in locked:
            tid = tmpl_of.get(bid)
            if tid is None or tid in seen:
                continue
            seen.add(tid)
            tw = self._rr_template_wrapper(tid)
            if tw is None or tid not in cell_of:
                continue
            if tid in user_pinned:
                print(f"[ripup_reroute] CLASS: template {tid} is "
                      f"user-pinned — skipped", flush=True)
                continue
            if len(tw.input.candidates) < 2:
                continue
            classes.append((tw, self._rr_wrapper(bid)))
        if not classes:
            return False, 0
        print(f"[ripup_reroute] CLASS pass: locked template instance(s) "
              f"hold the residual contention at {self._rr_m_str(cur)} — "
              f"trying template moves for {len(classes)} class(es)",
              flush=True)
        trials = 0
        for tw, iw in classes:
            tid = tw.input.original_bundle.id
            cell = cell_of[tid]
            n_inst = len(exp_map.get(tid, []))
            old_tidx = tw.plan.selected_topology_index
            moves = self._rr_class_moves(tw, iw, stage)
            improved = False
            for tidx in moves:
                if trials >= _RR_CLASS_MAX_TRIALS:
                    print(f"[ripup_reroute] CLASS: trial budget "
                          f"({_RR_CLASS_MAX_TRIALS}) exhausted — stop.",
                          flush=True)
                    return False, trials
                snap = self._rr_class_snapshot()
                self._rr_class_apply(tw, cell, by_cell[cell], tidx)
                # No replan: every wrapper keeps its committed assignment
                # (the incremental commits survive), and the moved class
                # needs none — its routing is the fixed copies the NUTS
                # re-run recomputes from the re-pinned template.
                self._rr_rerun(stage, full=True, skip_replan=True)
                m = metric()
                trials += 1
                if os.environ.get("BUDA_RR_TRACE"):
                    print(f"[rr-trace] CLASS template {tid} topo "
                          f"{old_tidx + 1}->{tidx + 1}: "
                          f"{self._rr_m_str(m)}", flush=True)
                if m < cur:
                    improved = True
                    break
                self._rr_class_restore(snap)
            if not improved:
                print(f"[ripup_reroute] CLASS: template {tid} "
                      f"(cell '{cell}', {n_inst} instance(s)) — no "
                      f"improvement", flush=True)
                continue
            # Commit: the trial state is already a FULL pipeline state,
            # and the trial's fixed-copy cache is exactly what produced
            # the accepted nuts_result — keep both authoritative.  Replay
            # the persistence the trial guard deferred: the template rows
            # (_persist_bottom_up_cell_decision) and any dogleg
            # adoption's template/instance rows, from the LIVE adopted
            # state — NOT by re-running the cell-local solve, which on an
            # already-split pinned template could re-dogleg into a
            # split-of-a-split and silently diverge from the accepted
            # routing (issue #473).
            self._persist_bottom_up_cell_decision(by_cell[cell])
            self._persist_bottom_up_dogleg_adoptions()
            self.planner.recharge_committed(self.bundles)
            print(f"[ripup_reroute] CLASS COMMIT: template {tid} "
                  f"(cell '{cell}') topo {old_tidx + 1}->"
                  f"{tw.plan.selected_topology_index + 1} across "
                  f"{n_inst} instance(s), metric {self._rr_m_str(cur)}->"
                  f"{self._rr_m_str(metric())}", flush=True)
            return True, trials
        return False, trials

    # ---- Measured-infeasibility uniformity break (stall tier 4) ---------
    # opens #14 fix space (a) / docs/internal/bottomup_healer_templates.md.
    # A locked instance whose plan-time track pools MATCH its reference can
    # still strand bits at DNUTS: the conflict is dynamic — neighbors and
    # occupancy at THAT instance — invisible to any static pool comparison
    # (mix2_fast_bottomup bundle 166: the uniform copy works at 3 of 4
    # instances, the 4th's surroundings close its window).  When even the
    # class pass stalls, the release pass breaks uniformity for exactly
    # the measured-infeasible instance: unlock it (pin + layers kept, the
    # PLACEMENT freed — its fixed copy is withdrawn and NUTS solves it
    # individually), and if the free re-solve alone does not strictly
    # improve, try the freed wrapper's candidate alternates before
    # restoring.  Gated on the user's declared
    # `check_template_tracks on_mismatch independent` policy — the same
    # opt-in that already accepts per-instance solving for environmental
    # mismatches; `stop`-policy flows are structurally untouched.  The
    # aligned siblings keep the uniform copy; every commit is LOUD.

    def _rr_release_pass(self, stage, metric, cur):
        """Release-move pass: returns (committed, trials).  No-op (False, 0)
        outside stage b, without the `independent` policy, or when no
        locked bundle holds measured DNUTS opens."""
        if stage != 'b':
            return False, 0
        if getattr(self, "_bu_mismatch_policy", "stop") != "independent":
            return False, 0
        if not getattr(self, "_planner_is_hier", False):
            return False, 0
        open_bids = {bid for bid, _si, _m, _e in self._open_segments()}
        locked_open = [w for w in self.bundles
                       if w.hier.locked
                       and w.input.original_bundle.id in open_bids]
        if not locked_open:
            return False, 0
        by_cell, cell_of, tmpl_of = self._rr_class_maps()
        print(f"[ripup_reroute] RELEASE pass: {len(locked_open)} locked "
              f"instance(s) still hold measured DNUTS opens at "
              f"{self._rr_m_str(cur)} — trying measured-infeasibility "
              f"uniformity breaks (policy: independent)", flush=True)
        trials = 0
        for w in locked_open:
            # Aggregate budget (the _RR_GLOBAL/_RR_CLASS_MAX_TRIALS
            # symmetry, Codex #487): each instance costs up to
            # 1 + _RR_CLASS_TOP_N full NUTS/DNUTS reruns — a
            # many-instance infeasible design must not run unbounded.
            # Checked per INSTANCE (not per inner trial) so a started
            # instance's release + alternates finish as one unit.
            if trials >= _RR_RELEASE_MAX_TRIALS:
                print(f"[ripup_reroute] RELEASE: trial budget "
                      f"({_RR_RELEASE_MAX_TRIALS}) exhausted — stop.",
                      flush=True)
                return False, trials
            bid = w.input.original_bundle.id
            inst = (w.input.original_bundle.instances[0]
                    if w.input.original_bundle.instances else "?")
            tid = tmpl_of.get(bid)
            cell = cell_of.get(tid, w.input.original_bundle.cell_context)
            snap = self._rr_class_snapshot()
            old_tidx = w.plan.selected_topology_index
            # Release: the wrapper keeps its pin (selected index + plan
            # seg_layers); hier.locked False withdraws it from the
            # fixed-copy compute (the locked filter) and from the DNUTS
            # copy plan, so the re-run below NUTS-solves it individually
            # against real occupancy.  The FORCED per-segment layers from
            # the bottom-up propagation must go: the planner applies
            # pinned_seg_layers[si] to EVERY candidate (the unpin_topology
            # hazard), so a repin trial below would carry the OLD
            # candidate's H/V layers onto a different-direction shape —
            # an unbuildable LAYER_DIR route the (opens, overlaps) metric
            # cannot see.  The release-alone path keeps its current
            # direction-correct plan.seg_layers; a repin's incremental
            # replan then assigns fresh direction-correct layers.
            w.hier.locked = False
            w.input.pinned_seg_layers = []
            self._rr_invalidate_bottom_up_caches()
            self._rr_rerun(stage, full=True, skip_replan=True)
            m = metric()
            trials += 1
            if os.environ.get("BUDA_RR_TRACE"):
                print(f"[rr-trace] RELEASE bundle {bid} ({inst}): "
                      f"{self._rr_m_str(m)}", flush=True)
            move = None                      # None = release alone won
            if not (m < cur):
                # The free re-solve alone did not improve — the pinned
                # candidate itself may be the infeasible shape (bundle
                # 166's junction-closed L).  Now that the wrapper is
                # unlocked, the normal incremental-replan trial machinery
                # applies: try its farness-ranked alternates on top of the
                # release before giving up.
                snap2 = self._rr_snapshot()
                for tidx in self._rr_candidate_order(w, old_tidx,
                                                     stage)[:_RR_CLASS_TOP_N]:
                    m = self._rr_trial(w, tidx, stage, metric, full=True)
                    trials += 1
                    if os.environ.get("BUDA_RR_TRACE"):
                        print(f"[rr-trace] RELEASE bundle {bid} topo "
                              f"{old_tidx + 1}->{tidx + 1}: "
                              f"{self._rr_m_str(m)}", flush=True)
                    if m < cur:
                        move = tidx
                        break
                    self._rr_restore(snap2, only=self._rr_dirty)
                if not (m < cur):
                    self._rr_class_restore(snap)
                    print(f"[ripup_reroute] RELEASE: bundle {bid} "
                          f"({inst}) — no improvement, copy kept",
                          flush=True)
                    continue
            # Commit: replay the trial-deferred persistence (any dogleg
            # adoption from the fixed-copy recompute), update the released
            # instance's expanded row (bu_locked=False — a resumed session
            # must restore it unlocked), and recharge the planner cuts.
            self._persist_bottom_up_dogleg_adoptions()
            if self.bdb is not None and tid is not None:
                self._add_expanded_bundle(
                    w, w.plan.selected_topology_index, {bid: tid})
            self.planner.recharge_committed(self.bundles)
            how = ("free re-solve" if move is None
                   else f"topo {old_tidx + 1}->{move + 1}")
            print(f"[ripup_reroute] RELEASE COMMIT: bundle {bid} "
                  f"({inst}, cell '{cell}') released from the uniform "
                  f"copy — solved individually ({how}), metric "
                  f"{self._rr_m_str(cur)}->{self._rr_m_str(metric())}; "
                  f"the aligned siblings keep the copy", flush=True)
            return True, trials
        return False, trials

    def _rr_flip_edges(self, w, stage):
        """MST edge_ids of w's SELECTED candidate that a current contention touches
        (step 4b).  A per-edge L/Z flip is an alternate move alongside the index
        alternates: map each overlap/open segment of this bundle -> its seg's
        edge_id (>= 0, deduped).  Empty unless the selected candidate is an MST
        type carrying edge tags, so non-MST bundles pay nothing."""
        # A group-pinned bundle must stay within its nominal-locus family: a
        # flip mutates the selected candidate's geometry IN PLACE (outside the
        # family), so — unlike an index alternate — it would escape the user's
        # super-candidate pin.  Offer no flips for it (Codex).
        if getattr(w.input, 'pinned_group', None):
            return []
        sel = w.plan.selected_topology_index
        cands = w.input.candidates
        if sel < 0 or sel >= len(cands):
            return []
        topo = cands[sel]
        if "MST" not in topo.type:
            return []
        segs = topo.segments
        bid = w.input.original_bundle.id
        eids, seen = [], set()

        def add_seg(si):
            if 0 <= si < len(segs):
                eid = segs[si].edge_id
                if eid >= 0 and eid not in seen:
                    seen.add(eid)
                    eids.append(eid)

        if self.nuts_result is not None:
            for od in self.nuts_result.overlap_details:
                if od.bid_a == bid:
                    add_seg(od.seg_a)
                if od.bid_b == bid:
                    add_seg(od.seg_b)
        # Stage b (DNUTS opens) may have 0 NUTS overlaps: also flip edges whose
        # segments failed to place all their bits.
        if stage == 'b':
            for b2, si, _missing, _exp in self._open_segments():
                if b2 == bid:
                    add_seg(si)
        return eids

    def _rr_apply_move(self, w, move, sel, stage, metric, full=False):
        """Apply a ripup move + re-run the pipeline; return the metric (or None
        if the move is an invalid flip that changed nothing).  Two kinds:
          ('idx', tidx) — pin candidate tidx (the wrapper's index alternate).
          ('flip', eid) — flip edge eid's L/Z bend on the SELECTED candidate in
            place, then re-pin that same index.  The flip preserves segment slots
            and far-endpoint taps (only the internal bend moves), so only
            seg_conns needs re-deriving (annotate_seg_conns — no fp, hier-safe)."""
        if move[0] == 'idx':
            return self._rr_trial(w, move[1], stage, metric, full=full)
        cands = w.input.candidates
        if not (0 <= sel < len(cands)):
            return None
        h, v = self._gen_hv()
        if not buda.flip_mst_edge(cands[sel], move[1], h, v, self.fp):
            return None                          # alt bend on an obstacle: no move
        buda.annotate_seg_conns(cands[sel])
        # cands elements are owned copies (audit C7-04) — write the flipped
        # pool back so the trial pipeline sees the new geometry.
        w.input.candidates = cands
        return self._rr_trial(w, sel, stage, metric, full=full)

    def _rr_guarded_move(self, w, move, old_tidx, stage, metric, snap):
        """_rr_apply_move with exception hygiene (#286 retrospective note):
        a mid-trial exception — e.g. the bottom-up DNUTS 'stop' policy
        raising inside _rr_rerun — must not leave trial state live.  Undo
        any flip geometry and restore the FULL baseline (the dirty set may
        be stale mid-flight) before re-raising."""
        try:
            return self._rr_apply_move(w, move, old_tidx, stage, metric)
        except BaseException:
            self._rr_undo_move(w, move, old_tidx)
            self._rr_restore(snap)
            raise

    def _rr_undo_move(self, w, move, sel):
        """Undo a rejected flip trial's in-place geometry change (flip_mst_edge is
        an involution).  Index moves need no geometry undo — _rr_restore already
        restores selection/pin/plan arrays."""
        if move[0] != 'flip':
            return
        cands = w.input.candidates
        if not (0 <= sel < len(cands)):
            return
        h, v = self._gen_hv()
        buda.flip_mst_edge(cands[sel], move[1], h, v, self.fp)
        buda.annotate_seg_conns(cands[sel])
        w.input.candidates = cands       # owned copies (C7-04): write back

    @staticmethod
    def _rr_move_str(old_tidx, move):
        if move[0] == 'idx':
            return f"topo {old_tidx + 1}->{move[1] + 1}"
        return f"flip edge {move[1]} (topo {old_tidx + 1})"

    @staticmethod
    def _rr_fwd_ok(snap, fwd):
        """True when committing by restoring the winning trial's forward
        snapshot is exact.  _rr_restore can TRIM a candidate list back down
        but never re-grow it, so a trial that appended a dogleg candidate
        (or moved a dogleg slot) cannot be committed by forward-restore —
        the baseline restore already dropped the appended candidate.  Those
        rare commits take the legacy re-run path."""
        if snap['dl_slot'] != fwd['dl_slot']:
            return False
        for bid, cap in fwd['wrap'].items():
            base = snap['wrap'].get(bid)
            if base is None or cap[2] != base[2]:      # candidate count grew
                return False
        return True


    def _rr_warm_eval(self, w, tidx, stage, cur, snap):
        """Warm-trial pre-filter (RR round 4): pin candidate tidx, replan its
        layers incrementally, and measure the move with the warm-start
        single-bundle re-solve (rerun_bundle_warm) instead of a full cold
        pipeline — the Phase-0-measured PREDICTOR of the cold metric
        (91-100% accept agreement, 4.6-6x cheaper).  Stage b adds a
        stateless DNUTS on the warm result, place-abort armed at the
        current opens (sound for the filter: unplaced is non-decreasing, so
        an aborted count already exceeds the strict-improvement bar).
        Tighten runs (matching the study's fidelity conditions).

        Returns the warm metric, or None when the incremental replan is
        unavailable (caller falls through to the cold trial — the
        conservative choice).  Only the target's plan state is touched and
        it is restored from `snap` before returning; session result refs
        are never mutated.  NEVER an accept basis: a warm-improving move
        still runs the full cold trial, and warm-rejected moves are
        cold-swept at the stall point (the loop's certificate stays a full
        COLD sweep)."""
        bid = w.input.original_bundle.id
        if self.planner is None or self.nuts_result is None:
            return None
        t0 = time.perf_counter()
        wm = None
        try:
            w.plan.selected_topology_index = tidx
            w.input.topology_pinned = True
            if bid in self._dogleg_slot:
                # Same hazard as _rr_trial: the target's per-segment dogleg
                # overrides index its adopted split topology, not this one.
                w.plan.seg_net_pull = []
                w.plan.seg_slide_lo = []
                w.plan.seg_slide_hi = []
            asn = self.planner.replan_bundle(self.bundles, bid)
            if asn is None:
                return None
            w.plan.selected_topology_index = asn.topo_index
            w.input.assigned_v_layer = asn.v_layer_id
            w.input.assigned_h_layer = asn.h_layer_id
            w.plan.seg_layers = list(asn.seg_layers)
            w.plan.seg_perp = list(asn.seg_perp)
            eng = buda.NUTSEngine(self.fp, self.layers)
            eng.set_track_pitch(self._nuts_pitch)
            eng.set_extra_grid_points(
                list(self.planner.get_x_grid()),
                list(self.planner.get_y_grid()))
            with contextlib.redirect_stdout(io.StringIO()), \
                    buda.ostream_redirect():
                warm = eng.rerun_bundle_warm(snap['nuts'], self.bundles,
                                             bid)
                if stage == 'b':
                    segs = buda.make_bus_segments(
                        self.bundles, warm, self.fp,
                        self._detailed_bit_order)
                    deng = buda.DetailedNUTSEngine(self.routing_grid)
                    dres = deng.run(segs, emit_vias=False,
                                    abort_unplaced=self._rr_m_primary(cur))
                    wm = (dres.num_unplaced, warm.num_overlaps)
                else:
                    wm = warm.num_overlaps
        finally:
            self._rr_t_add('warm', time.perf_counter() - t0)
            self._rr_restore(snap, only={bid})
        return wm

    def _rr_warm_study_sample(self, w, tidx, stage, cur, cold_m, snap):
        """RR round-4 Phase-0 probe (`BUDA_RR_WARM_STUDY=1`): after a COLD
        trial computed its metric — the loop still runs entirely on cold, so
        trajectories are byte-identical with the study off — ALSO run the
        warm-start single-bundle re-solve from the same baseline and record
        how well the warm metric predicts the cold one.  The wrapper is
        still in trial state here (pinned + replanned), which is exactly
        the state a production warm trial would evaluate; one fidelity
        caveat is that a cold trial may have dogleg-adopted the target
        (rare — cyclic constraints only), which a warm-only world would
        not have.  Run with `no_fast_trials` so the cold metric is the
        exact full-pipeline one (fast trials abort/skips would truncate
        it).  Rows accumulate on the session; _rr_warm_study_report
        summarizes at run end."""
        bid = w.input.original_bundle.id
        t0 = time.perf_counter()
        eng = buda.NUTSEngine(self.fp, self.layers)
        eng.set_track_pitch(self._nuts_pitch)
        if self.planner is not None:
            eng.set_extra_grid_points(list(self.planner.get_x_grid()),
                                      list(self.planner.get_y_grid()))
        with contextlib.redirect_stdout(io.StringIO()), \
                buda.ostream_redirect():
            warm = eng.rerun_bundle_warm(snap['nuts'], self.bundles, bid)
            if stage == 'b':
                segs = buda.make_bus_segments(self.bundles, warm, self.fp,
                                              self._detailed_bit_order)
                deng = buda.DetailedNUTSEngine(self.routing_grid)
                dres = deng.run(segs, emit_vias=False)
                warm_m = (dres.num_unplaced, warm.num_overlaps)
            else:
                warm_m = warm.num_overlaps
        dt = time.perf_counter() - t0
        if not hasattr(self, '_rr_warm_rows'):
            self._rr_warm_rows = []
        self._rr_warm_rows.append((stage, bid, tidx, cur, cold_m, warm_m,
                                   dt))
        if os.environ.get("BUDA_RR_TRACE"):
            print(f"[rr-warm-study] bundle {bid} topo ->{tidx + 1}: "
                  f"cur {self._rr_m_str(cur)} cold {self._rr_m_str(cold_m)} "
                  f"warm {self._rr_m_str(warm_m)} ({dt * 1000:.1f}ms)",
                  flush=True)

    def _rr_warm_study_report(self):
        """Summarize the warm-vs-cold fidelity rows: exact metric matches,
        accept-decision agreement against the committed metric, the two
        error kinds — false-accept (warm improves, cold does not; cheap,
        a verify-on-accept commit rejects it) and FALSE-REJECT (warm
        misses a cold improvement; the two-tier killer) — and the mean
        per-trial cost of each side."""
        rows = getattr(self, '_rr_warm_rows', None)
        if not rows:
            return
        t = getattr(self, '_rr_t', None) or {}
        cold_s = t.get('nuts', 0.0) + t.get('dnuts', 0.0)
        cold_n = max(t.get('n_nuts', 0), 1)
        for stg in sorted({r[0] for r in rows}):
            rs = [r for r in rows if r[0] == stg]
            n = len(rs)
            exact = sum(1 for r in rs if r[4] == r[5])
            fa = sum(1 for r in rs if r[5] < r[3] and not r[4] < r[3])
            fr = sum(1 for r in rs if not r[5] < r[3] and r[4] < r[3])
            agree = n - fa - fr
            warm_s = sum(r[6] for r in rs)
            print(f"[rr-warm-study] stage {stg}: {n} trial(s), metric "
                  f"exact {exact}/{n}, accept agreement {agree}/{n}, "
                  f"false-accept {fa}, FALSE-REJECT {fr}; warm "
                  f"{1000 * warm_s / n:.1f}ms/trial vs cold "
                  f"{1000 * cold_s / cold_n:.1f}ms/trial", flush=True)
        self._rr_warm_rows = []

    def _rr_scan_moves(self, w, bid, old_tidx, moves, cur, stage, metric,
                       snap, trial_base=0, warm=False, warm_rej=None,
                       first_improving=False):
        """First-improving scan of ONE contender's move list — the inner
        trial loop of _ripup_reroute, extracted verbatim so the screened
        scan, the deferred-move stall sweep, and the warm-stall cold sweep
        share it.  Every COLD trial is applied, measured on the TRUE
        metric, and restored to the snapshot baseline; the winning trial's
        forward snapshot is captured when the commit path can use it.

        `warm` (round 4): idx moves are pre-filtered by the warm-start
        re-solve — a move whose WARM metric does not strictly improve
        skips its cold trial and is appended to `warm_rej` (the caller's
        list), to be cold-swept at the iteration's stall point; a
        warm-improving move falls through to the normal cold trial, so
        accepts stay on the true metric.  Flip moves and moves the warm
        eval cannot mirror (replan unavailable) are never filtered.

        `first_improving` (item E): stop this contender's scan on the FIRST
        strictly-improving move instead of trialing the whole list for its
        BEST move.  Used ONLY by the stall sweeps (deferred + warm-rescued),
        whose callers break on the first improving contender anyway — so the
        stop certificate (no improver ⇒ whole list trialed) is preserved, and
        the sweeps' input is already screen-sorted best-first, so the first
        improver is the best-screened one.  Cuts sweep trial VOLUME; it does
        NOT change the main contender scan (where best-of-contender still
        holds).  Trajectory-affecting (which improver commits can differ),
        not byte-identical.  Returns (cand_best, n_trials) with cand_best =
        (metric, bid, old_tidx, move, fwd) or None; n_trials counts cold
        trials."""
        zero = (0, 0) if isinstance(cur, tuple) else 0
        cand_best = None
        n_trials = 0
        for move in moves:
            if warm and move[0] == 'idx':
                wm = self._rr_warm_eval(w, move[1], stage, cur, snap)
                if wm is not None and not wm < cur:
                    if warm_rej is not None:
                        warm_rej.append(move)
                    continue
            t_trial = time.perf_counter()
            m = self._rr_guarded_move(w, move, old_tidx, stage, metric, snap)
            if m is None:
                continue                 # invalid flip (bend on obstacle)
            # Round-4 Phase-0 probe: the wrapper is still in trial state
            # (restored below), so the warm re-solve samples exactly what a
            # production warm trial would see.  No-op without the env var.
            if (move[0] == 'idx'
                    and os.environ.get("BUDA_RR_WARM_STUDY")):
                self._rr_warm_study_sample(w, move[1], stage, cur, m, snap)
            # New best so far: capture the trial's state BEFORE the
            # restore, so the commit can jump straight to it instead
            # of re-running the whole pipeline (index moves only —
            # a flip's in-place geometry is not snapshot-covered).
            take = ((m < cur and (cand_best is None
                                  or m < cand_best[0]))
                    or m == zero)
            fwd = None
            # Fast trials skip metric-neutral passes, so the trial
            # state is NOT the committable full-pipeline state — the
            # commit must take the legacy full re-run (no fwd).
            if (take and move[0] == 'idx'
                    and not getattr(self, '_rr_fast_trials', False)):
                fwd = self._rr_snapshot()
            self._rr_undo_move(w, move, old_tidx)
            self._rr_restore(snap, only=self._rr_dirty)
            n_trials += 1
            if os.environ.get("BUDA_RR_TRACE"):
                print(f"[rr-trace] trial {trial_base + n_trials}: bundle "
                      f"{bid} {self._rr_move_str(old_tidx, move)} -> "
                      f"{self._rr_m_str(m)} "
                      f"({time.perf_counter() - t_trial:.3f}s)",
                      flush=True)
            if take:
                cand_best = (m, bid, old_tidx, move, fwd)
                # Item E: in a stall sweep, the caller commits the first
                # improving CONTENDER, so this contender only needs one
                # improving move — and the sweep list is screen-sorted
                # best-first, so the first improver is the best-screened.
                # Stop here rather than trialing the rest for the metric
                # optimum (which the caller would discard anyway).
                if first_improving:
                    break
            # Absolute best (stage b: 0 opens AND 0 overlaps) — take it
            # now.  A merely-primary zero keeps scanning: among moves
            # that clear the opens, the lexicographic metric still
            # prefers the one with the least collateral overlap.
            if m == zero:
                break
        return cand_best, n_trials

    def _rr_trial(self, w, tidx, stage, metric, full=False):
        """Pin w to candidate tidx, re-run the pipeline, return metric (no restore)."""
        # Sound stage-b early abort (fast trials): capture the COMMITTED
        # metric's opens BEFORE mutating — once a trial's running unplaced
        # count exceeds it, the trial is a certain rejection and DNUTS stops
        # placing (unplaced never decreases through place/cull).
        abort_opens = -1
        if (stage == 'b' and not full
                and getattr(self, '_rr_fast_trials', False)):
            abort_opens = self._rr_m_primary(metric())
        w.plan.selected_topology_index = tidx
        w.input.topology_pinned = True
        bid = w.input.original_bundle.id
        if bid in self._dogleg_slot:
            # The target carries per-segment dogleg overrides indexed by ITS
            # adopted split topology; pinning a different candidate must not
            # inherit them (misapplied slide/pull pins — the hazard
            # _reset_doglegs guards against, scoped here to the one bundle the
            # trial moves).  The snapshot restores them on rejection.
            w.plan.seg_net_pull = []
            w.plan.seg_slide_lo = []
            w.plan.seg_slide_hi = []
        self._rr_rerun(stage, target_bid=bid, full=full,
                       abort_opens=abort_opens)
        return metric()

    def _open_segments(self):
        """(bundle_id, seg_idx, missing_bits, expected_bits) for every
        under-placed segment of the current detailed result (the per-bundle
        rollup of the same walk is _rr_open_bundles).

        Memoized by the identity of `self.detailed_result` (the contender scan
        calls this several times per iteration on the same result — via
        _rr_contenders / _rr_contention_centres / _rr_open_bundles / the stage-b
        edge walk).  A solve always REPLACES detailed_result with a new object
        (never mutates in place), and selections change in lockstep with it (a
        move re-solves; a restore reverts both together), so object identity is a
        sound, self-validating key needing no explicit invalidation — a stale
        pointer can never survive a state change.  Read-only tuples, so returning
        the cached list is safe (callers only iterate)."""
        dr = self.detailed_result
        if dr is None:
            return []
        cache = getattr(self, "_open_seg_cache", None)
        if cache is not None and cache[0] is dr:
            return cache[1]
        per_seg = {}
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
            segs = per_seg.get(bid, {})
            for si in range(len(cands[sel].segments)):
                missing = exp - segs.get(si, 0)
                if missing > 0:
                    out.append((bid, si, missing, exp))
        self._open_seg_cache = (dr, out)
        return out

    def _neg_template_targets(self, affected):
        """Negotiate v2 (docs/internal/bottomup_healer_templates.md item D):
        the bottom-up template classes whose LOCKED instances appear in this
        iteration's affected list — the bundles today's iteration skips.
        Returns [(cell, [target template wrappers], cell_wrappers)] with one
        entry per cell (a cell's local re-plan handles all its targets at
        once); canonical templates only (_rr_class_maps), user-pinned and
        single-candidate templates excluded.  Empty on flat flows / no
        locked contention — the v2 path is then structurally inert."""
        if not getattr(self, "_planner_is_hier", False):
            return []
        locked = [bid for bid in affected
                  if (w := self._rr_wrapper(bid)) is not None
                  and w.hier.locked]
        if not locked:
            return []
        by_cell, cell_of, tmpl_of = self._rr_class_maps()
        if not by_cell:
            return []
        user_pinned = getattr(self, "_bu_user_pinned", None) or set()
        per_cell = {}
        for bid in locked:
            tid = tmpl_of.get(bid)
            if tid is None or tid in user_pinned:
                continue
            tw = self._rr_template_wrapper(tid)
            if tw is None or len(tw.input.candidates) < 2:
                continue
            per_cell.setdefault(cell_of[tid], {})[tid] = tw
        return [(cell, list(tws.values()), by_cell[cell])
                for cell, tws in per_cell.items()]

    def _neg_replan_cell_templates(self, cell, targets, cell_wrappers,
                                   inj_recs):
        """Negotiate v2's per-cell mutation: UNPIN the target templates (the
        corrected prices, not a pinned index, choose the topology — the
        negotiate convention; a user-pinned template never reaches here),
        translate the iteration's injected demand into the cell frame
        summed across instances, re-run the cell-local solve under that
        aggregated price field, and propagate the resulting pins to every
        instance of each target class.  The caller owns accept/restore
        (class snapshot) and the deferred-persistence replay on accept."""
        for tw in targets:
            tw.input.topology_pinned = False
            tw.input.pinned_seg_layers = []
            # Dogleg per-segment overrides are indexed by the current
            # (possibly split) selection — they must not leak onto whatever
            # the priced re-plan selects (the class snapshot restores them
            # on rejection).
            tw.plan.seg_net_pull = []
            tw.plan.seg_slide_lo = []
            tw.plan.seg_slide_hi = []
        local_inj = self._translate_injections_to_cell(cell, cell_wrappers,
                                                       inj_recs)
        iters = (getattr(self, "_bu_local_iterations", None)
                 or self._planner_iterations)
        self._plan_bottom_up_cell(cell, cell_wrappers, iters,
                                  persist=False, inject=local_inj)
        for tw in targets:
            self._rr_propagate_template_pin(tw)
        self._rr_invalidate_bottom_up_caches()

    def _negotiate_iteration(self, affected, stage, sink, tmpl_neg=(),
                             inj_recs=()):
        """One negotiation iteration's mutation body, silenced: first the
        template-class re-plans (negotiate v2 — `tmpl_neg` cells re-planned
        in their local frames under the translated `inj_recs` prices, so
        the free-bundle replans below charge the NEW template placement),
        then replan every affected FREE bundle UNPINNED under the injected
        prices, then re-run NUTS (+ DNUTS for stage b).  Extracted so
        _negotiate_congestion can wrap it in exception hygiene (restore +
        clear injected demand).  When templates are re-planned the body
        runs under _rr_in_trial so a REJECTED iteration's fixed-copy
        recompute (dogleg adoption) cannot reach the BDB — the accept path
        replays the deferred persistence."""
        with contextlib.redirect_stdout(sink), buda.ostream_redirect():
            t0 = time.perf_counter()
            if tmpl_neg:
                self._rr_in_trial = True
            try:
                self._negotiate_iteration_body(affected, stage, tmpl_neg,
                                               inj_recs, t0)
            finally:
                if tmpl_neg:
                    self._rr_in_trial = False

    def _negotiate_iteration_body(self, affected, stage, tmpl_neg,
                                  inj_recs, t0):
        """The mutation sequence of one negotiation iteration (see
        _negotiate_iteration, which owns the output redirect and the
        _rr_in_trial guard): template-class re-plans first, then the free
        affected bundles, then the NUTS (+ DNUTS) re-solve."""
        for cell, targets, cell_ws in tmpl_neg:
            self._neg_replan_cell_templates(cell, targets, cell_ws,
                                            inj_recs)
        for bid in affected:
            w = self._rr_wrapper(bid)
            if w is None:
                continue
            # A hier.locked wrapper (bottom-up template instance) is never
            # an INDIVIDUAL negotiation target: its pinned assignment is a
            # uniform copy shared by all sibling instances.  Its class is
            # negotiated at the TEMPLATE level instead (the tmpl_neg loop
            # above — negotiate v2 price translation); its overlap partner
            # (if unlocked) still replans around it, and
            # replan_bundle_ripup's victim stage skips locked blockers
            # C++-side too.
            if w.hier.locked:
                continue
            # Unpin: the corrected prices, not a pinned index, choose
            # the topology (snapshot restores the pins on rejection).
            # Per-segment layer pins must go too — plan_bundle applies
            # pinned_seg_layers[si] to EVERY candidate regardless of
            # topology_pinned, so a stale sidecar pin would force
            # layers onto whatever topology negotiation picks
            # (including H/V mismatches that charge no cuts).
            w.input.topology_pinned = False
            w.input.pinned_seg_layers = []
            # NOTE: pinned_group is deliberately NOT cleared — a
            # super-candidate group pin must survive negotiation.  The C++
            # selection loop gives pinned_group precedence over the (now
            # cleared) single pin, so replan_bundle_ripup re-selects WITHIN
            # the family; the injected prices still steer WHICH member wins,
            # but negotiation can never move a group-pinned bundle out of the
            # family the user pinned (mirrors _rr_candidate_order for ripup).
            # The victim stage is likewise family-safe: C++ only reads
            # pinned_group, never clears it.
            # The ripup-capable replan (v2b): when the target has no
            # overflow-free candidate under the injected prices, the
            # planner's own ladder may also displace the committed
            # bundle blocking it — both assignments come back and the
            # outer accept/restore guard still owns safety.
            asns = self.planner.replan_bundle_ripup(self.bundles, bid)
            for asn in asns:
                w2 = self._rr_wrapper(asn.bundle_id)
                if w2 is None:
                    continue
                w2.plan.selected_topology_index = asn.topo_index
                w2.input.assigned_v_layer = asn.v_layer_id
                w2.input.assigned_h_layer = asn.h_layer_id
                w2.plan.seg_layers = list(asn.seg_layers)
                w2.plan.seg_perp = list(asn.seg_perp)
        self._rr_t_add('replan', time.perf_counter() - t0)
        t0 = time.perf_counter()
        self._run_nuts_internal()
        self._rr_t_add('nuts', time.perf_counter() - t0)
        self._rr_t_add_passes('nuts', self.nuts_result.pass_seconds)
        if stage == 'b':
            t0 = time.perf_counter()
            self._run_detailed_nuts(bit_order=self._detailed_bit_order)
            self._rr_t_add('dnuts', time.perf_counter() - t0)
            self._rr_t_add_passes('dnuts',
                                  self.detailed_result.pass_seconds)

    def _heal_dead_spans(self, stage):
        """Preconditioning step folded into the stage-b healers.

        A LOW-layer segment whose ACTUAL placed geometry offers fewer
        keepout-clear signal tracks than its member bits (the exact
        DetailedNUTS admission arithmetic — `_escalate_dead_low_segments`;
        admission is all-or-nothing, so a partial-supply shortfall strands
        EVERY bit) is a guaranteed DNUTS open that NO candidate re-pin can
        fix: it is a layer-assignment fault, not a topology-selection one, so
        the healers' hill-climb (which re-pins candidates / replans off
        contended bands) can grind on it forever.  Escalating each such
        segment to the cheapest same-direction TOP layer strictly reduces
        opens (a starved LOW segment strands 100% of its bits;
        TOP carries full supply), so we run it ONCE before the hill-climb and
        let the healer's own loop absorb any collateral overlap the re-solve
        surfaces — the same "escalate, then heal the fallout" contract the
        planner escalations already rely on.

        Stage b only (opens); a no-op when nothing is dead.  Returns the
        number of segments escalated.  Honors `_heal_dead_spans_in_healers`
        (default on) so a study run / regression bisect can disable it."""
        if stage != 'b' or self.nuts_result is None:
            return 0
        if not getattr(self, '_heal_dead_spans_in_healers',
                       _RR_HEAL_DEAD_SPANS_DEFAULT):
            return 0
        # NOTE: this stage-b fold runs even when cmd_run_nuts already
        # escalated at run_nuts (the earlier, before-the-healers timing).
        # Running BOTH is measured-best: the run_nuts escalation fixes the
        # flows the late fold alone leaves open (mix 16->0, bigHalf 190->94),
        # while the late fold recovers the one flow the early escalation alone
        # regresses (mix2 stays 42, not 66).  A dead LOW segment the early
        # pass already moved to TOP is simply not re-found here (no-op), so
        # the two passes compose without conflict.
        n = self._escalate_dead_low_segments()
        if n:
            # Refresh the stage-b metric off the escalated abstract solve.
            self._run_detailed_nuts(bit_order=self._detailed_bit_order)
            print(f"[heal] dead-span escalation: moved {n} dead LOW "
                  f"segment(s) to a TOP layer and re-solved before the "
                  f"hill-climb.", flush=True)
            # Persist the heal even if no later hill-climb move follows: the
            # escalation mutated seg_layers and re-solved through the
            # no-persist NUTS/DNUTS helpers, so the BDB still holds the
            # pre-heal LOW-layer route.  Without this, a heal-only fix (all
            # opens cleared by escalation, no committed move) would be lost on
            # load_pipeline.  Idempotent — a following commit re-persists the
            # final state.
            if self.bdb is not None:
                self._checkpoint_routing()
                print(f"[BDB] re-persisted routing after dead-span "
                      f"escalation ({n} segment(s)).")
        return n

    def _stage_a_scope_advisory(self, who: str):
        """Stage-a healer SCOPE advisory (caps-flow UX, 2026-08-07): the
        stage-a metric is NUTS overlaps ONLY, so a clean stage-a line
        ("metric already 0 — nothing to do" / "done: metric ...->0") can sit
        right above a dirty `check_design` — keepout-seated segments (the
        exhausted-window commits `NUTSResult.num_keepout_conflicts` counts)
        and supply-doomed seats (the #536 census) are invisible to the
        overlap metric and only surface as DNUTS opens at stage b.  Say so
        whenever such seats exist, so a stage-a healer's clean exit is never
        read as "design clean".  Print-only; silent when there is nothing to
        report (a clean flow's log is unchanged)."""
        n_keep = getattr(self.nuts_result, "num_keepout_conflicts", 0) or 0
        n_doom = len(self._doomed_seats())
        if not n_keep and not n_doom:
            return
        parts = []
        if n_keep:
            parts.append(f"{n_keep} keepout-seated segment(s)")
        if n_doom:
            parts.append(f"{n_doom} supply-doomed seat(s)")
        print(f"[{who}] advisory: stage-a metric is overlaps-only — "
              f"{', '.join(parts)} remain and will surface as DNUTS opens "
              f"at stage b (details: check_design).", flush=True)

    def _negotiate_congestion(self, max_iter=5, use_class_moves=False,
                              use_press=False):
        """Measured-congestion negotiation (wishlist-healer item 1).  Instead of
        guess-and-test over topology candidates, feed the ACTUAL failures back
        into the planner as demand on the exact bands where they happened
        (inject_band_demand), then re-plan the offending bundles UNPINNED
        against those corrected prices (replan_bundle) — the cost model itself
        steers them off the contended bands, choosing among ALL their
        candidates in one pass, no per-candidate NUTS trial.

        Stage auto-detected like ripup_reroute:
          a (after run_nuts) — each NUTS overlap rectangle is injected on its
            layer; both bundles of every overlap are re-planned; metric =
            overlap count.
          b (after run_detailed_nuts, v2) — each DNUTS-open segment marks a
            band whose REAL signal-track supply fell short of what the
            (track-blind) width model promised; its whole placed window is
            injected, scaled by the missing-bit fraction, and the open bundles
            are re-planned; metric = lexicographic (opens, overlaps) so
            clearing opens cannot silently trade abstract packing away.

        Re-injection pressure grows each time the same rectangle re-appears
        (PathFinder-style history), so persistent contention gets progressively
        more expensive.  Iterations are accepted only on strict metric
        improvement (snapshot/restore otherwise) — a safe hill-climb;
        ripup_reroute remains the finisher for whatever negotiation leaves."""
        self._healers_ran = True   # past-healer stamp (reseat-heal gate)
        self._healers_ran_cycle = True   # ...and for THIS routing cycle
        #   (pair-align gate; cleared by a fresh run_planner)
        if not self.bundles:
            print("Error: negotiate_congestion has no bundles.")
            return
        if self.planner is None:
            print("Error: negotiate_congestion needs run_planner to have run first.")
            return
        if self.nuts_result is None:
            print("Error: negotiate_congestion needs run_nuts to have run first.")
            return
        stage = 'b' if self.detailed_result is not None else 'a'
        if stage == 'b':
            metric = lambda: (self.detailed_result.num_unplaced,   # noqa: E731
                              self.nuts_result.num_overlaps)
        else:
            metric = lambda: self.nuts_result.num_overlaps         # noqa: E731
        # Fold in the dead-span escalation before the hill-climb (stage b):
        # clear the guaranteed opens no replan can reach, then let negotiate
        # heal the fallout.
        self._heal_dead_spans(stage)
        m0 = metric()
        # Opens-only primary check (NOT the full-metric one ripup uses after a
        # heal): negotiate's stage-b engine injects DNUTS-OPEN segments only —
        # it has no mechanism to reduce NUTS overlaps in stage b, so once the
        # heal has cleared the opens there is genuinely nothing for negotiate
        # to do and any overlap fallout is deferred to ripup_reroute (the
        # documented finisher).  The heal's own checkpoint persists the route.
        if self._rr_m_primary(m0) == 0:
            print(f"[negotiate] stage {stage}: metric already 0 — nothing to do.")
            if stage == 'a':
                self._stage_a_scope_advisory("negotiate")
            return
        what = "DNUTS opens" if stage == 'b' else "NUTS overlaps"
        print(f"[negotiate] stage {stage} ({what}): start "
              f"metric={self._rr_m_str(m0)}, max_iter={max_iter}", flush=True)
        self._rr_t_init()
        history = {}          # contention rectangle -> times seen (pressure)
        accepted = 0
        last_fail = None      # failed retry's metric (repeat detection)
        for it in range(1, max_iter + 1):
            cur = metric()
            if cur == ((0, 0) if isinstance(cur, tuple) else 0):
                break
            self.planner.clear_injected_demand()
            affected = []
            inj_recs = []     # (layer, s_lo, s_hi, p_lo, p_hi, amount) —
            #                   this iteration's injections, kept for the
            #                   template price translation (negotiate v2)
            n_sites = 0
            if stage == 'a':
                for od in self.nuts_result.overlap_details:
                    key = (od.layer, round(od.span_lo), round(od.span_hi),
                           round(od.perp_lo), round(od.perp_hi))
                    history[key] = history.get(key, 0) + 1
                    # Pressure = the physical over-subscription (the overlap's
                    # perpendicular extent + one pitch), scaled by how often
                    # this rectangle has resisted (history).
                    amount = ((od.perp_hi - od.perp_lo) + self._nuts_pitch) \
                        * history[key]
                    self.planner.inject_band_demand(
                        od.layer, od.span_lo, od.span_hi,
                        od.perp_lo, od.perp_hi, amount)
                    inj_recs.append((od.layer, od.span_lo, od.span_hi,
                                     od.perp_lo, od.perp_hi, amount))
                    n_sites += 1
                    for bid in (od.bid_a, od.bid_b):
                        if bid not in affected:
                            affected.append(bid)
            else:
                ts_map = {(ts.bundle_id, ts.seg_idx): ts
                          for ts in self.nuts_result.segments}
                for bid, si, missing, exp in self._open_segments():
                    ts = ts_map.get((bid, si))
                    if ts is None:
                        continue
                    s_lo = min(ts.span_lo, ts.span_hi)
                    s_hi = max(ts.span_lo, ts.span_hi)
                    key = ('open', ts.layer, round(ts.interval_lo),
                           round(ts.interval_hi), round(s_lo), round(s_hi))
                    history[key] = history.get(key, 0) + 1
                    # The whole placed window is short of tracks; charge it in
                    # proportion to how much of the bus failed to land there.
                    amount = (ts.interval_hi - ts.interval_lo) \
                        * (missing / exp) * history[key]
                    self.planner.inject_band_demand(
                        ts.layer, s_lo, s_hi,
                        ts.interval_lo, ts.interval_hi, amount)
                    inj_recs.append((ts.layer, s_lo, s_hi,
                                     ts.interval_lo, ts.interval_hi, amount))
                    n_sites += 1
                    if bid not in affected:
                        affected.append(bid)
            # Planner convention: widest first.
            affected.sort(
                key=lambda b: -(self._rr_wrapper(b).input.width
                                if self._rr_wrapper(b) is not None else 0.0))
            # Negotiate v2: the locked members of `affected` map to bottom-up
            # template classes negotiated in their cell-local frames under
            # the translated prices.  Empty on flat / no-locked-contention
            # flows — the base snapshot then keeps the historical iteration
            # byte-identical; with template targets the EXTENDED snapshot
            # covers the template-side state the iteration now mutates.
            tmpl_neg = (self._neg_template_targets(affected)
                        if use_class_moves else [])
            snap = (self._rr_class_snapshot() if tmpl_neg
                    else self._rr_snapshot())
            restore = (self._rr_class_restore if tmpl_neg
                       else self._rr_restore)
            sink = io.StringIO()
            try:
                self._negotiate_iteration(affected, stage, sink,
                                          tmpl_neg=tmpl_neg,
                                          inj_recs=inj_recs)
            except BaseException:
                # A mid-iteration exception (e.g. the bottom-up DNUTS 'stop'
                # policy raising) must not leave speculative state live
                # (#286 retrospective note).
                restore(snap)
                self.planner.clear_injected_demand()
                raise
            new = metric()
            if new < cur:
                accepted += 1
                last_fail = None
                if tmpl_neg:
                    # Replay the persistence the _rr_in_trial guard deferred
                    # (template decisions + any dogleg adoption) — the same
                    # accept contract as a ripup class move.
                    for _cell, _targets, cell_ws in tmpl_neg:
                        self._persist_bottom_up_cell_decision(cell_ws)
                    self._persist_bottom_up_dogleg_adoptions()
                n_cls = sum(len(t) for _c, t, _w in tmpl_neg)
                cls_note = (f" (+{n_cls} template class(es))"
                            if n_cls else "")
                print(f"[negotiate] iter {it}: {n_sites} contention site(s) -> "
                      f"replanned {len(affected)} bundle(s){cls_note}, metric "
                      f"{self._rr_m_str(cur)}->{self._rr_m_str(new)}", flush=True)
            else:
                restore(snap)
                # `press` (opt-in): don't stop at the FIRST non-improving
                # iteration — `history` persists across iterations, so a
                # retry re-injects the SAME contention rectangles at grown
                # PathFinder pressure (amount scales with history[key]), the
                # escalation the first-failure stop computes and then throws
                # away.  Accepts stay strict and failed iterations restore;
                # cost is bounded by max_iter AND by repeat detection: a
                # failed retry reproducing the SAME metric as the previous
                # failure means the deterministic replans are insensitive to
                # the grown amounts (caps/mix stage b: the failure repeats
                # byte-identically at doubled pressure) — certified waste,
                # stop.  OPT-IN because the corpus measured 0 better/1 worse
                # as a default: on mix2_fast_on_aligned_sql the pressed
                # retries DO crack the stall on negotiate's own metric
                # (150 (ovl 12) -> 130 (ovl 6)) but the improved hand-off
                # shifts ripup's greedy basin and the flow ENDPOINT lands
                # worse (2/16/1 -> 4/28/2).  Default = the historical
                # first-failure stop, byte-identical.
                if not use_press:
                    print(f"[negotiate] iter {it}: no improvement "
                          f"(metric {self._rr_m_str(cur)}->"
                          f"{self._rr_m_str(new)}) — restored, stop.",
                          flush=True)
                    break
                if new == last_fail:
                    print(f"[negotiate] iter {it}: no improvement "
                          f"(metric {self._rr_m_str(cur)}->"
                          f"{self._rr_m_str(new)}) — restored; identical "
                          f"outcome under escalated pressure, stop.",
                          flush=True)
                    break
                last_fail = new
                if it < max_iter:
                    print(f"[negotiate] iter {it}: no improvement "
                          f"(metric {self._rr_m_str(cur)}->"
                          f"{self._rr_m_str(new)}) — restored, pressure "
                          f"escalates.", flush=True)
                else:
                    print(f"[negotiate] iter {it}: no improvement "
                          f"(metric {self._rr_m_str(cur)}->"
                          f"{self._rr_m_str(new)}) — restored, stop.",
                          flush=True)
        # Never leak injected demand into later commands: ripup_reroute's
        # replan_bundle trials would silently re-apply it.
        self.planner.clear_injected_demand()
        print(f"[negotiate] done: metric {self._rr_m_str(m0)}->"
              f"{self._rr_m_str(metric())} "
              f"after {accepted} accepted iteration(s).", flush=True)
        if stage == 'a':
            self._stage_a_scope_advisory("negotiate")
        print(f"[negotiate] timing: {self._rr_t_str()}", flush=True)
        _pp = self._rr_t_passes_str()
        if _pp:
            print(f"[negotiate] solve passes: {_pp}", flush=True)
        self._rr_t = None
        self._rr_disc_memo = None
        if self.bdb is not None and accepted:
            self._checkpoint_routing()
            print(f"[BDB] re-persisted post-negotiate routing "
                  f"({accepted} iteration(s) accepted).")

    def _ripup_reroute(self, max_iter=_RR_DEFAULT_MAX_ITER,
                       use_edge_candidates=False, use_global=True,
                       fast_trials=None, screen=None, warm=None,
                       converge_guard=None, use_class_moves=True,
                       use_release_moves=True, use_parallel_sweep=None):
        self._healers_ran = True   # past-healer stamp (reseat-heal gate)
        self._healers_ran_cycle = True   # ...and for THIS routing cycle
        #   (pair-align gate; cleared by a fresh run_planner)
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
        # Fast trials (round 3): trials skip metric-neutral passes; commits
        # re-run full.  Scoped to THIS run (reset at run end, and every run
        # re-sets it at entry) so it cannot leak into other flows.
        self._rr_fast_trials = (_RR_FAST_TRIALS_DEFAULT
                                if fast_trials is None else fast_trials)
        # Fixed-context screen (round 3, final lever): rank each contender's
        # alternates by a cheap frozen-context placement and full-trial only
        # the top few, deferring the rest to the iteration's stall sweep.
        # Ordering only — accepts stay on the true full metric.
        screen = _RR_SCREEN_DEFAULT if screen is None else screen
        # Warm trials (round 4): pre-filter each move with the warm-start
        # re-solve; only warm-improving moves pay a cold trial, and
        # warm-rejected moves are cold-swept at the stall point — the stop
        # certificate stays a full COLD sweep.
        warm = _RR_WARM_TRIALS_DEFAULT if warm is None else warm
        # Convergence guard: bail early on an over-capacity design that can't
        # converge (see util.py).  Default on; `no_converge_guard` disables.
        converge_guard = (_RR_CONVERGE_GUARD_DEFAULT
                          if converge_guard is None else converge_guard)
        # Parallel stall sweep (rnr runtime P1): default per
        # _RR_PARALLEL_SWEEP_DEFAULT; `no_parallel_sweep` opts out (the
        # sequential sweep, the pre-P1 behavior).  Also auto-disabled when
        # the trial semantics differ from what the C++ workers implement
        # (no_fast_trials / warm_trials) — see the sweep call site.
        use_parallel_sweep = (_RR_PARALLEL_SWEEP_DEFAULT
                              if use_parallel_sweep is None
                              else use_parallel_sweep)

        # Fold in the dead-span escalation before the hill-climb (stage b):
        # a dead LOW segment is a guaranteed open no candidate re-pin reaches,
        # so escalate it to TOP first and let ripup heal the fallout.
        n_heal = self._heal_dead_spans(stage)

        m0 = metric()
        # A heal that moved routing may clear the opens but leave (or surface)
        # NUTS-overlap fallout.  In stage b the loop grinds that overlap once
        # opens hit 0 (contenders include the overlap partners), so after a
        # heal the entry "nothing to do" check must look at the FULL metric,
        # not the opens-only primary — else the fallout the escalation was
        # meant to hand off is stranded.  With no heal, keep the historical
        # primary check (do not start a run just to chase pre-existing
        # abstract overlaps).
        if n_heal:
            entry_clean = (m0 == (0, 0)) if isinstance(m0, tuple) else (m0 == 0)
        else:
            entry_clean = (self._rr_m_primary(m0) == 0)
        if entry_clean:
            print(f"[ripup_reroute] stage {stage}: metric already 0 — nothing to do.")
            if stage == 'a':
                self._stage_a_scope_advisory("ripup_reroute")
            return
        what = "DNUTS opens" if stage == 'b' else "NUTS overlaps"
        print(f"[ripup_reroute] stage {stage} ({what}): "
              f"start metric={self._rr_m_str(m0)}, "
              f"max_iter={max_iter}, {len(self._rr_contenders(stage))} contenders",
              flush=True)

        self._rr_t_init()
        self._rr_width_memo = {}         # static width gate, per-run memo
        self._rr_width_logged = set()
        self._rr_dirty = None            # set per trial by _rr_rerun
        committed = 0
        it = 0
        n_trials = 0
        # Convergence guard: primary-metric value after each committed
        # iteration (seed with the entry value), scanned over a trailing
        # window to detect a non-converging grind.
        prim_hist = [self._rr_m_primary(m0)]
        # Rejected trials need no post-loop rebuild: _rr_snapshot captures (and
        # _rr_restore restores) every field a trial can mutate — selection, pin,
        # dogleg-appended candidates, the plan assignment arrays
        # (seg_layers/seg_perp/assigned_*), the dogleg per-segment overrides
        # (seg_net_pull/seg_slide_*), the result refs, and the dogleg
        # bookkeeping — so after a restore the live plan and the reported
        # metric are consistent by construction.  (A full-replan rebuild here
        # would be WRONG with incremental commits: re-planning the committed
        # selections from scratch assigns different layers than the committed
        # single-bundle replans and can destroy the improvement just made.)
        stopped_early = False            # True if we converged / ran out of moves
        # NOTE (measured, bigHalf stage b): a "skip contenders whose own
        # contention is unchanged since their last failed sweep" cache was
        # tried here and REVERTED — a contender's trial outcomes depend on
        # global state, not just its own contention sites (commits to five
        # other bundles freed the capacity bundle 77's alternates needed
        # while 77's own signature never moved; the skip stranded 52 opens).
        # Re-sweep cost is instead addressed by cheaper trials.
        while it < max_iter:
            it += 1
            cur = metric()
            # Stop only at the metric's ABSOLUTE zero.  For stage b's
            # lexicographic (opens, overlaps) that means the loop keeps going
            # after the opens hit 0, grinding back the collateral NUTS-overlap
            # creep its own opens-clearing moves may have introduced (a strict
            # tuple improvement with opens already 0 IS an overlap reduction).
            # Entry with opens already 0 is still a no-op (the primary-based
            # check above): ripup repairs damage it caused, it does not start
            # a stage-b run just to chase abstract overlaps.
            if cur == ((0, 0) if isinstance(cur, tuple) else 0):
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
            deferred = []    # (ci, bid, old_tidx, moves) screened-out idx moves
            warm_pend = {}   # bid -> (ci, old_tidx, [moves]) warm-rejected,
            #                  cold-pending (the certificate sweep's input)
            # First-improving-contender.  Each trial is a full pipeline re-run
            # (~1-2s on a large hier design), so the old "best move over ALL
            # contenders" sweep cost contenders*candidates re-runs per iteration —
            # minutes of silent work (e.g. 25 contenders * ~8 candidates ≈ 150
            # re-runs ≈ 3+ min with no output, which reads as a hang).  Instead,
            # scan contenders in priority order and commit the FIRST whose best
            # alternate candidate strictly lowers the metric; a per-contender
            # heartbeat (flushed) makes progress visible.
            #
            # Parallel primary scan (rnr runtime P1b): the same gate as the
            # deferred stall sweep (fast-trial semantics the C++ workers
            # implement; no warm pre-filter) plus idx-only moves (the sweep
            # cannot evaluate flips) — AND a real pool.  On a 1-thread pool
            # (e.g. qor sweep workers pin BUDA_SWEEP_THREADS=1) the chunked
            # sweep would serially evaluate whole chunks where the
            # sequential loop commits after the first improving trial —
            # measured 2.5x slower on improver-heavy flows (rnr/mix) — so
            # single-thread pools keep the sequential scan.  The DEFERRED
            # stall sweep stays engaged at any width: its certificate case
            # needs every move evaluated regardless.
            use_par_scan = (use_parallel_sweep
                            and getattr(self, '_rr_fast_trials', False)
                            and not warm and not use_edge_candidates)
            if use_par_scan:
                # Pool width is consulted only once the other gates pass, so
                # sequential runs (no_parallel_sweep / no_fast_trials / warm
                # / edge candidates) never parse the thread env (Codex #604).
                n_pool = self._rr_sweep_threads() or (os.cpu_count() or 1)
                use_par_scan = n_pool > 1
            if use_par_scan:
                best, t = self._rr_parallel_scan_sweep(
                    contenders, cur, stage, metric, snap, deferred, screen,
                    n_cont, it)
                n_trials += t
            else:
                for ci, bid in enumerate(contenders, 1):
                    w = self._rr_wrapper(bid)
                    if w is None:
                        continue
                    old_tidx = snap['wrap'][bid][0]
                    # Two move sources for this contender:
                    #   ('idx', t)  — pin an alternate candidate index.
                    #   ('flip', e) — flip one contended MST edge's L/Z bend
                    #                 on the SELECTED candidate in place
                    #                 (step 4b), keeping the index.  Only
                    #                 contended edges are tried, so cost
                    #                 stays ~linear in overflows, not 2^N.
                    # Relevance-first index order (item 4): candidates
                    # farthest from the measured contention are the
                    # likeliest fixes.
                    moves = [('idx', t)
                             for t in self._rr_candidate_order(w, old_tidx,
                                                               stage)]
                    # Fixed-context screen (round 3, final lever): rank the idx
                    # alternates by a ~ms single-bundle placement against the
                    # frozen baseline and FULL-trial only the top few; the rest
                    # are deferred to this iteration's stall sweep below, so no
                    # reachable fix is ever pruned away — only postponed.
                    if screen and len(moves) > _RR_SCREEN_TOP_N:
                        moves, rest = self._rr_screen_prune(w, moves)
                        if rest:
                            deferred.append((ci, bid, old_tidx, rest))
                    # The per-edge MST L/Z flip move-source is opt-in
                    # (`use_edge_candidates`): on the current corpus a flip
                    # is only ever *tried* on real contended MST edges — an
                    # index alternate always wins the commit — so it is off
                    # by default (routes unchanged) and enabled only when
                    # asked to explore edge flips.
                    if use_edge_candidates:
                        moves += [('flip', e)
                                  for e in self._rr_flip_edges(w, stage)]
                    wr = []
                    cand_best, t = self._rr_scan_moves(
                        w, bid, old_tidx, moves, cur, stage, metric, snap,
                        trial_base=n_trials, warm=warm, warm_rej=wr)
                    n_trials += t
                    if wr:
                        warm_pend.setdefault(bid, (ci, old_tidx, []))[2] \
                            .extend(wr)
                    if cand_best is not None:
                        best = cand_best
                        print(f"[ripup_reroute] iter {it}: contender "
                              f"{ci}/{n_cont} bundle {bid} improves "
                              f"{self._rr_m_str(cur)}->"
                              f"{self._rr_m_str(cand_best[0])} "
                              f"({self._rr_move_str(old_tidx, cand_best[3])})",
                              flush=True)
                        break
                    print(f"[ripup_reroute] iter {it}: contender "
                          f"{ci}/{n_cont} bundle {bid} — no improvement",
                          flush=True)
            if best is None and deferred:
                # Completeness fallback: the screened scan stalled, so sweep
                # the deferred (screened-out) moves at full fidelity before
                # concluding anything.  With this sweep the iteration's "no
                # improving re-route" verdict rests on exactly the same full
                # trial set as an unscreened run — the screen can reorder
                # WHICH improving move is found first (like fast trials'
                # trajectory effect) but never remove the stall certificate.
                #
                # PARALLEL path (rnr runtime P1, trial_sweep.cpp): the sweep
                # is embarrassingly parallel — k independent moves against
                # one frozen baseline, and the certificate case needs all k.
                # Gated on the default trial semantics the C++ workers
                # implement faithfully (fast trials, no warm pre-filter);
                # any winning move is REPLAYED through the sequential trial
                # before committing, so a wrong commit is impossible.
                use_par = (use_parallel_sweep
                           and getattr(self, '_rr_fast_trials', False)
                           and not warm)
                if use_par:
                    best, t = self._rr_parallel_deferred_sweep(
                        deferred, cur, stage, metric, snap, n_cont, it)
                    n_trials += t
                    deferred = []            # fully consumed by the sweep
                n_def = sum(len(mv) for _c, _b, _o, mv in deferred)
                if deferred:
                    print(f"[ripup_reroute] iter {it}: screened scan stalled "
                          f"— sweeping {n_def} deferred move(s)", flush=True)
                for ci, bid, old_tidx, moves in deferred:
                    w = self._rr_wrapper(bid)
                    if w is None:
                        continue
                    wr = []
                    cand_best, t = self._rr_scan_moves(w, bid, old_tidx,
                                                       moves, cur, stage,
                                                       metric, snap,
                                                       trial_base=n_trials,
                                                       warm=warm,
                                                       warm_rej=wr,
                                                       first_improving=True)
                    n_trials += t
                    if wr:
                        warm_pend.setdefault(bid, (ci, old_tidx, []))[2] \
                            .extend(wr)
                    if cand_best is not None:
                        best = cand_best
                        print(f"[ripup_reroute] iter {it}: contender "
                              f"{ci}/{n_cont} bundle {bid} improves "
                              f"{self._rr_m_str(cur)}->"
                              f"{self._rr_m_str(cand_best[0])} "
                              f"({self._rr_move_str(old_tidx, cand_best[3])}"
                              f", deferred)", flush=True)
                        break
            if best is None and warm_pend:
                # Warm-stall certificate sweep: every warm-rejected move is
                # cold-trialed before the iteration concludes anything, so
                # the "no improving re-route" verdict (and the global-pass
                # entry) rests on exactly the same full COLD trial set as a
                # no-warm run — a warm false-reject costs this sweep, never
                # the endpoint (the Phase-0 study's contract).
                n_wp = sum(len(mv) for _c, _o, mv in warm_pend.values())
                print(f"[ripup_reroute] iter {it}: warm scan stalled — "
                      f"cold-sweeping {n_wp} warm-rejected move(s)",
                      flush=True)
                for bid, (ci, old_tidx, moves) in warm_pend.items():
                    w = self._rr_wrapper(bid)
                    if w is None:
                        continue
                    cand_best, t = self._rr_scan_moves(w, bid, old_tidx,
                                                       moves, cur, stage,
                                                       metric, snap,
                                                       trial_base=n_trials)
                    n_trials += t
                    if cand_best is not None:
                        best = cand_best
                        print(f"[ripup_reroute] iter {it}: contender "
                              f"{ci}/{n_cont} bundle {bid} improves "
                              f"{self._rr_m_str(cur)}->"
                              f"{self._rr_m_str(cand_best[0])} "
                              f"({self._rr_move_str(old_tidx, cand_best[3])}"
                              f", warm-rescued)", flush=True)
                        break
            if best is None and use_global:
                # Normal contenders stalled above zero: bounded global pass
                # over the contention sites' band OCCUPANTS (bundles that hold
                # the contended bands without being contended themselves — the
                # b61 class no contender-derived move can fix).  Opt out with
                # the `no_global` keyword.
                best, g_trials = self._rr_global_pass(stage, metric, snap,
                                                      cur, set(contenders))
                n_trials += g_trials
            if best is None and use_class_moves:
                # Everything above stalled: bottom-up template CLASS moves —
                # when the residual contention sits on hier.locked template
                # instances (which every pass above must skip), re-pin the
                # TEMPLATE and re-route the whole class in one measured
                # move.  Commits internally (the trial state is a full
                # pipeline state, persisted via the deferred path).  No-op
                # on flat / non-bottom-up designs.  Opt out with
                # `no_class_moves`.
                cls_ok, c_trials = self._rr_class_pass(stage, metric, cur)
                n_trials += c_trials
                if cls_ok:
                    committed += 1
                    prim_hist.append(self._rr_m_primary(metric()))
                    continue
            if best is None and use_release_moves:
                # Even the class pass stalled: measured-infeasibility
                # uniformity break (opens #14 (a)) — release a locked
                # instance whose copied routing is measured DNUTS-open and
                # solve it individually.  Gated on the `independent`
                # policy; `no_release_moves` disables.
                rel_ok, r_trials = self._rr_release_pass(stage, metric,
                                                         cur)
                n_trials += r_trials
                if rel_ok:
                    committed += 1
                    prim_hist.append(self._rr_m_primary(metric()))
                    continue
            if best is None:
                print(f"[ripup_reroute] iter {it}: no improving re-route "
                      f"(metric={self._rr_m_str(cur)}) — stop.")
                stopped_early = True
                break
            m_new, bid, old_t, move, fwd = best
            # Commit.  An index move whose trial appended no dogleg candidate
            # commits by restoring the FORWARD snapshot captured at trial time
            # — the exact winning state, no pipeline re-run.  The planner's
            # cut usage is then explicitly recharged from the restored
            # committed assignments: replanning consumers recharge anyway,
            # but DIRECT cut readers (the visualizer's congestion overlay via
            # get_cuts) must see the committed route, not the last rejected
            # trial's recharge (Codex #286).  Flip moves and dogleg-appending
            # trials take the legacy re-run: the flip's in-place geometry /
            # the appended candidate are not forward-restorable (_rr_restore
            # never re-grows a pool).
            if fwd is not None and self._rr_fwd_ok(snap, fwd):
                self._rr_restore(fwd)
            else:
                self._rr_apply_move(self._rr_wrapper(bid), move, old_t,
                                    stage, metric, full=True)
            # Recharge after BOTH commit paths: the forward restore never
            # touched the cuts, and even the legacy re-run's cuts predate
            # _adopt_doglegs when the winning trial re-solved a dogleg in
            # place — direct cut readers (the viz congestion overlay) must
            # see the committed post-adoption state either way (#286
            # retrospective item 2).
            self.planner.recharge_committed(self.bundles)
            committed += 1
            print(f"[ripup_reroute] iter {it}: COMMIT bundle {bid} "
                  f"{self._rr_move_str(old_t, move)}, metric {self._rr_m_str(cur)}->"
                  f"{self._rr_m_str(metric())}", flush=True)

            # Convergence guard: a committing iteration made progress, but if
            # the primary metric is still high AND has barely moved over the
            # trailing window, this design won't converge in a reasonable
            # budget — stop now rather than grind (over-capacity flows burn
            # tens of seconds per stage-b run for a fraction-of-a-percent
            # gain).  Provably cannot fire on a flow that reaches the floor in
            # < WINDOW iterations.  STAGE-B ONLY: the guard was designed and
            # measured for DNUTS opens; a stage-a run (NUTS overlaps, before
            # DNUTS) must exhaust its requested budget so it doesn't strand
            # abstract overlaps for DetailedNUTS — even a slowly-improving
            # ≥FLOOR stage-a run is doing the exact work stage b depends on
            # (Codex #359).
            prim_now = self._rr_m_primary(metric())
            prim_hist.append(prim_now)
            if (converge_guard and stage == 'b'
                    and len(prim_hist) > _RR_CONVERGE_WINDOW
                    and prim_now >= _RR_CONVERGE_FLOOR):
                win_start = prim_hist[-1 - _RR_CONVERGE_WINDOW]
                if win_start > 0:
                    cleared = (win_start - prim_now) / win_start
                    if cleared < _RR_CONVERGE_FRAC:
                        print(f"[ripup_reroute] not converging — primary metric "
                              f"plateaued at {prim_now} "
                              f"({cleared * 100:.1f}% cleared over last "
                              f"{_RR_CONVERGE_WINDOW} iters, ≥ floor "
                              f"{_RR_CONVERGE_FLOOR}); stopping early to save "
                              f"runtime (over-capacity design — fix placement, "
                              f"or `no_converge_guard` / raise max_iter to "
                              f"continue).", flush=True)
                        stopped_early = True
                        break

        # POST-CLIMB DEAD-SPAN REFOLD (issue #523 regression tail).  The
        # entry heal ran before the climb, but the climb itself can CREATE
        # dead spans: a committed re-pin lands a candidate whose stub sits in
        # a starved interval (mix2 iter 8: b101 -> topo 3, a 32-bit stub over
        # a 22-track cell — measured better overall at commit time, then
        # unreachable, because every later trial moved TOPOLOGIES while the
        # fault was the LAYER).  Whether the climb ended by stall or by
        # max_iter, re-run the same escalation the entry heal uses, measured:
        # snapshot, escalate + re-solve, accept only on a strictly better
        # metric, restore otherwise.  Runs once — _escalate_dead_low_segments
        # loops internally until no dead LOW segment remains.
        if (stage == 'b' and self._rr_m_primary(metric()) > 0
                and getattr(self, '_heal_dead_spans_in_healers',
                            _RR_HEAL_DEAD_SPANS_DEFAULT)):
            # Same opt-out the entry heal honors: a study / regression bisect
            # that sets _heal_dead_spans_in_healers=False must retain the
            # pre-feature healer behavior end to end (Codex P2 on #531).
            #
            # Two tiers, each independently measured (snapshot / escalate +
            # re-solve / accept only on a strictly better metric):
            #   1. "dead" — the exact DNUTS admission predicate (the entry
            #      heal's), for dead spans the climb itself introduced.
            #   2. "cull-risk" — the SURVIVAL predictor (bounded span-clear
            #      pool < member bits, no midpoint retry): the midpoint pool
            #      admits bits whose final span still crosses the keepout,
            #      and cull_keepout_crossers then strands them (the #523
            #      interval-pull residual — b61 seg6: span-clear 0, midpoint
            #      19, all 16 admitted bits culled).  A superset of tier 1,
            #      run separately so a rejected aggressive batch cannot
            #      cancel an accepted conservative one.  Only HERE — the
            #      accept contract prices its false positives at zero, which
            #      the unconditional entry/auto heals cannot.
            for tier_name, tier_kw in (("dead", {}),
                                       ("cull-risk", {"cull_risk": True})):
                if self._rr_m_primary(metric()) == 0:
                    break
                cur_end = metric()
                heal_snap = self._rr_snapshot()
                n_refold = self._escalate_dead_low_segments(**tier_kw)
                if not n_refold:
                    continue
                self._run_detailed_nuts(bit_order=self._detailed_bit_order)
                m_heal = metric()
                if m_heal < cur_end:
                    print(f"[ripup_reroute] HEAL-REFOLD ({tier_name}): "
                          f"escalated {n_refold} starved LOW segment(s), "
                          f"metric {self._rr_m_str(cur_end)}->"
                          f"{self._rr_m_str(m_heal)}", flush=True)
                    self.planner.recharge_committed(self.bundles)
                    if self.bdb is not None:
                        self._checkpoint_routing()
                    committed += 1
                else:
                    self._rr_restore(heal_snap)

        if not stopped_early and self._rr_m_primary(metric()) > 0:
            print(f"[ripup_reroute] reached max_iter={max_iter} while still "
                  f"improving — re-run ripup_reroute or raise max_iter "
                  f"(e.g. `ripup_reroute {max_iter * 5}`) to continue.", flush=True)
        self._rr_warm_study_report()
        print(f"[ripup_reroute] done: metric {self._rr_m_str(m0)}->"
              f"{self._rr_m_str(metric())} "
              f"after {committed} move(s), {n_trials} trial(s).", flush=True)
        # A severed bus places all its bits (never a DNUTS open), so a "metric
        # ->0" line reads as fully clean — call it out loud if any bundle ended
        # DISCONNECTED (issue #399 follow-up 3; folded into the metric above so
        # the heal never PICKS one, this catches a pre-existing / non-heal one).
        disc = self._rr_disconnected_bids()
        if disc:
            print(f"[ripup_reroute] WARNING: {len(disc)} bundle(s) end "
                  f"DISCONNECTED (electrically severed, not a DNUTS open): "
                  f"{disc}", flush=True)
        if stage == 'a':
            self._stage_a_scope_advisory("ripup_reroute")
        print(f"[ripup_reroute] timing: {self._rr_t_str()}", flush=True)
        _pp = self._rr_t_passes_str()
        if _pp:
            print(f"[ripup_reroute] solve passes: {_pp}", flush=True)
        self._rr_t = None
        self._rr_disc_memo = None
        self._rr_fast_trials = False

        # Commit the FINAL state: the trials/commits above re-ran the
        # planner/NUTS(/DNUTS) through the internal no-persist helpers
        # (_run_nuts_internal / replan_bundle), so the BDB still holds the
        # pre-ripup routing. A checkpoint (save/exit → load_pipeline resume)
        # must reflect what this session now holds. Idempotent; no-op without
        # an open BDB or without moves.
        if self.bdb is not None and committed:
            self._checkpoint_routing()
            print(f"[BDB] re-persisted post-ripup routing "
                  f"({committed} re-route(s) committed).")
