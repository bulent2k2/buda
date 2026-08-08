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

import time

import buda

import buda_session.ripup as _ripup_mod


class RefineMixin:
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
                    for tidx in alts[:_ripup_mod._RR_SCREEN_TOP_N]:
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
                    self._decision(
                        f"[refine_selection] COMMIT bundle {bid} "
                        f"{self._rr_move_str(old, move)}, metric "
                        f"{m_str(cur)}->"
                        f"{m_str(metric())}",
                        "refine_commit", bid=bid,
                        move=self._rr_move_str(old, move), m_from=cur,
                        m_to=metric())
                    cur = metric()
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
