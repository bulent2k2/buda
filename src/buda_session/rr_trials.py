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


"""Healer trial execution — Phase D-3.

Extracted VERBATIM from RipupMixin (risk_reduction_plan.md R3): the
warm-start pre-filter (+ its study harness), the per-contender
best-of-list scan (_rr_scan_moves — the inner trial loop whose
fast-trial semantics the C++ sweeps replicate), and the single-move
full trial (_rr_trial).  The trial pipeline re-run (_rr_rerun), the
candidate ordering policy, and the hill-climb driver stay in
RipupMixin; state snapshot/restore rides RRStateMixin.  Member sets
disjoint (buda_session.MIXINS guard).
"""

import contextlib
import io
import os
import time

import buda

import buda_session.ripup as _ripup_mod


class RRTrialsMixin:
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
                        self._detailed_bit_order, self.layers)
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
                                              self._detailed_bit_order,
                                              self.layers)
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
