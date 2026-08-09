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


"""Measured selection refine (`refine_selection`) — Phase D-2.

Extracted VERBATIM from RipupMixin (risk_reduction_plan.md R3): the
end-of-flow WL polish sweep.  Shares trial machinery (_rr_scan_moves,
screens, snapshots) and reporting helpers with RipupMixin/RRStateMixin
via self; member sets disjoint (guarded by buda_session.MIXINS).
"""

import os
import time

import buda

import buda_session.ripup as _ripup_mod


class RefineMixin:
    # ── measured selection refine (selection-basis lever 3) ─────────────────
    def _refine_eligible(self, w):
        """The sweep's eligibility test: returns the current selection index
        for a refinable bundle, None for a skip (user pin inviolable /
        bottom-up fixed copy / single-candidate pool / no selection)."""
        if w.input.topology_pinned:
            return None
        if w.hier.locked:
            return None
        n = len(w.input.candidates)
        old = w.plan.selected_topology_index
        if n < 2 or old < 0:
            return None
        return old

    def _refine_screen_alts(self, w, old):
        """Screen-ordered top alternates for one bundle (ordering only, the
        P1 fixed-context screen): all alternate indices sorted by screened
        (overlaps, violations), ties on index, truncated to the trial
        budget.  Falls back to the unscreened index order when the screen
        is unavailable."""
        n = len(w.input.candidates)
        alts = [t for t in range(n) if t != old]
        scores = self._rr_screen_scores(w, alts)
        if scores is not None:
            alts.sort(key=lambda t: (scores[t][0], scores[t][1], t))
        return alts[:_ripup_mod._RR_SCREEN_TOP_N]

    def _refine_try_bundle(self, w, bid, old, tops, stage, metric, accept,
                           m_str, cur):
        """The sequential per-bundle trial + commit body (verbatim from the
        pre-parallel sweep loop): full-trial the screened alternates in
        order, adopt the FIRST one the accept guard takes, commit via the
        forward snapshot (or the full re-apply fallback) + recharge.
        Returns (cur, committed?, trials_run) — `cur` re-read after a
        commit."""
        snap = self._rr_snapshot()
        accepted = None
        trials = 0
        for tidx in tops:
            move = ('idx', tidx)
            m = self._rr_guarded_move(w, move, old, stage, metric, snap)
            # WL polish (default): adopt only when opens and overlaps are
            # PARITY-or-better componentwise with strictly shorter realized
            # WL — the healers' work is never traded for length.
            # chase_overlaps: plain lexicographic (an aggressive pre-healer
            # — measured mixed on the vehicles, see the wishlist entry).
            take = m is not None and accept(m, cur)
            # Full trials (fast off) => the trial state is a full-pipeline
            # state: capture it as the commit's forward snapshot before
            # restoring.
            fwd = self._rr_snapshot() if take else None
            self._rr_undo_move(w, move, old)
            self._rr_restore(snap, only=self._rr_dirty)
            trials += 1
            if take:
                accepted = (m, move, fwd)
                break
        if accepted is None:
            return cur, False, trials
        m_new, move, fwd = accepted
        if fwd is not None and self._rr_fwd_ok(snap, fwd):
            self._rr_restore(fwd)
        else:
            self._rr_apply_move(w, move, old, stage, metric, full=True)
        self.planner.recharge_committed(self.bundles)
        self._decision(
            f"[refine_selection] COMMIT bundle {bid} "
            f"{self._rr_move_str(old, move)}, metric "
            f"{m_str(cur)}->"
            f"{m_str(metric())}",
            "refine_commit", bid=bid,
            move=self._rr_move_str(old, move), m_from=cur,
            m_to=metric())
        return metric(), True, trials

    def _refine_parallel_sweep(self, stage, metric, accept, m_str, cur,
                               committed, max_moves, n_trials):
        """One PARALLEL refine sweep over the session bundles (the ripup
        P1/P1b machinery in full-trial form): screen lazily in visit-order
        chunks, evaluate every kept move on the C++ sweep pool with the
        WL-fair full-trial semantics (tighten on, DNUTS abort off) and the
        4-component outcome (opens/overlaps/violations/realized-WL), then
        walk the outcomes in visit order.  A bundle none of whose moves the
        accept guard would take books its trials and moves on — the
        dominant case on a polish sweep, now at pool width instead of two
        sequential full pipelines per bundle.  A bundle with a would-accept
        (or unevaluable) move REPLAYS its whole kept list through the
        sequential `_refine_try_bundle` — the replay is the accept basis
        and the committed state, so committed moves, printed lines and
        trial counts match the sequential sweep exactly; a sweep-vs-replay
        disagreement is LOUD and the replay verdict wins.  After a commit
        the baseline changed: the chunk's remaining evaluations are stale,
        so the sweep resumes rebuilding from the next bundle.
        Returns (cur, committed, n_trials, swept_any_commit)."""
        bundles = list(self.bundles)
        n = len(bundles)
        n_thr = self._rr_sweep_threads() or (os.cpu_count() or 1)
        chunk_moves = max(8, 2 * n_thr)
        sweeping = False
        i = 0
        while i < n and committed < max_moves:
            pre = []              # (pos, w, bid, old) — chunk membership
            nmv = 0
            while i < n and nmv < chunk_moves:
                pos, w = i, bundles[i]
                i += 1
                old = self._refine_eligible(w)
                if old is None:
                    continue
                bid = w.input.original_bundle.id
                # Post-prune kept count is knowable without screening, so
                # chunk membership is fixed BEFORE the batched screen.
                pre.append((pos, w, bid, old))
                nmv += min(len(w.input.candidates) - 1,
                           _ripup_mod._RR_SCREEN_TOP_N)
            if not pre:
                continue
            # Batched parallel screen for the whole chunk (the sequential
            # per-bundle screens were the sweep's dominant cost at chip
            # scale); ordering per bundle identical to _refine_screen_alts.
            reqs = [(w, [t for t in range(len(w.input.candidates))
                         if t != old])
                    for _p, w, _b, old in pre]
            scores_list = self._rr_screen_scores_many(reqs)
            group = []            # (pos, w, bid, old, tops)
            for (pos, w, bid, old), (_w2, alts), scores in \
                    zip(pre, reqs, scores_list):
                if scores is not None:
                    alts = sorted(alts,
                                  key=lambda t: (scores[t][0],
                                                 scores[t][1], t))
                group.append((pos, w, bid, old,
                              alts[:_ripup_mod._RR_SCREEN_TOP_N]))
            flat = [(0, bid, old, t)
                    for _p, _w, bid, old, tops in group for t in tops]
            if flat:
                base_disc, net_counts, dn_kwargs = \
                    self._rr_sweep_stage_setup(flat, stage, metric,
                                               full=True)
                outcomes = self._rr_sweep_eval(flat, stage, base_disc,
                                               net_counts, dn_kwargs,
                                               full=True)
            else:
                outcomes = []
            oi = 0
            restart = None
            for pos, w, bid, old, tops in group:
                outs = outcomes[oi:oi + len(tops)]
                oi += len(tops)
                flagged = False
                all_ok = True
                for (prim, sec, viols, wl, ok) in outs:
                    if not ok:
                        flagged, all_ok = True, False
                        break
                    if stage == 'b':
                        m = (prim, sec, viols, int(round(wl)))
                    else:
                        m = (prim, viols, int(round(wl)))
                    if accept(m, cur):
                        flagged = True
                        break
                if not flagged:
                    n_trials += len(tops)   # the sequential trials replaced
                    continue
                cur, did, t = self._refine_try_bundle(
                    w, bid, old, tops, stage, metric, accept, m_str, cur)
                n_trials += t
                if did:
                    committed += 1
                    sweeping = True
                    if committed >= max_moves:
                        return cur, committed, n_trials, sweeping
                    restart = pos + 1   # baseline changed: chunk is stale
                    break
                if all_ok:
                    self._decision(
                        f"[refine_selection] WARNING: parallel-sweep "
                        f"divergence on bundle {bid} — replay verdict kept",
                        "refine_divergence", bid=bid)
            if restart is not None:
                i = restart
        return cur, committed, n_trials, sweeping

    def _refine_selection(self, max_moves=30, chase_overlaps=False,
                          use_parallel_sweep=None):
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
        # Parallel refine sweep (chip-scale runtime, 2026-08-08): the polish
        # sweep full-trials the screened top-2 of EVERY eligible bundle —
        # ~2 full pipeline solves per bundle per sweep, the dominant cost at
        # chip scale (chip_stack: 500s+ per call) — and most bundles accept
        # nothing, which makes the sweep the same embarrassingly-parallel
        # certificate shape as ripup's stall sweep.  Gate mirrors the P1b
        # scan: default on, `no_parallel_sweep` opts out, 1-thread pools
        # keep the sequential loop (chunked eval wastes work when commits
        # are frequent and the pool adds nothing).
        use_par = (_ripup_mod._RR_PARALLEL_SWEEP_DEFAULT
                   if use_parallel_sweep is None else use_parallel_sweep)
        if use_par:
            n_pool = self._rr_sweep_threads() or (os.cpu_count() or 1)
            use_par = n_pool > 1
        try:
            sweeping = True
            while sweeping and committed < max_moves:
                sweeping = False
                if use_par:
                    cur, committed, n_trials, sweeping = \
                        self._refine_parallel_sweep(
                            stage, metric, accept, m_str, cur, committed,
                            max_moves, n_trials)
                    continue
                for w in list(self.bundles):
                    if committed >= max_moves:
                        break
                    old = self._refine_eligible(w)
                    if old is None:
                        continue
                    bid = w.input.original_bundle.id
                    tops = self._refine_screen_alts(w, old)
                    cur, did, t = self._refine_try_bundle(
                        w, bid, old, tops, stage, metric, accept, m_str,
                        cur)
                    n_trials += t
                    if did:
                        committed += 1
                        sweeping = True
            self._decision(
                f"[refine_selection] done: metric {m_str(m0)}->"
                f"{m_str(metric())} after {committed} move(s), "
                f"{n_trials} trial(s).",
                "refine_done", m_from=m0, m_to=metric(),
                moves=committed, trials=n_trials)
            print(f"[refine_selection] timing: {self._rr_t_str()}",
                  flush=True)
            self._checkpoint_routing()
        finally:
            self._rr_fast_trials = saved_fast
            self._rr_t = None
            self._rr_disc_memo = {}
