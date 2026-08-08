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


"""Healer screened evaluation + parallel sweeps — Phase D-3.

Extracted VERBATIM from RipupMixin (risk_reduction_plan.md R3): the
fixed-context screen (scores + prune), the sweep-pool plumbing
(threads/stage-setup/eval over buda.parallel_sweep), and the two
parallel evaluation paths — the PRIMARY screened scan (rnr runtime P1b)
and the deferred stall-certificate sweep (P1).  Decisions route through
self._decision; trial replays ride self._rr_scan_moves (RRTrialsMixin);
snapshots ride RRStateMixin.  Member sets disjoint (buda_session.MIXINS
guard).
"""

import contextlib
import io
import os
import time

import buda

import buda_session.ripup as _ripup_mod


class RRSweepsMixin:
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
        screened score: the _ripup_mod._RR_SCREEN_TOP_N best-screened moves are
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
        return ([idx_moves[i] for i in order[:_ripup_mod._RR_SCREEN_TOP_N]],
                [idx_moves[i] for i in order[_ripup_mod._RR_SCREEN_TOP_N:]])

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
                if screen and len(moves) > _ripup_mod._RR_SCREEN_TOP_N:
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
                        self._decision(
                            f"[ripup_reroute] iter {it}: contender "
                            f"{ci}/{n_cont} bundle {bid} improves "
                            f"{self._rr_m_str(cur)}->"
                            f"{self._rr_m_str(cand_best[0])} "
                            f"({self._rr_move_str(old_tidx, cand_best[3])})",
                            "rr_improve", it=it, ci=ci, bid=bid,
                            m_from=cur, m_to=cand_best[0],
                            move=self._rr_move_str(old_tidx, cand_best[3]))
                        return cand_best, trials
                    if all(ok for _p, _s, ok in outs):
                        self._decision(
                            f"[ripup_reroute] WARNING: parallel-sweep "
                            f"divergence on bundle {bid} (screened scan) "
                            f"— replay verdict kept",
                            "rr_divergence", it=it, bid=bid)
                else:
                    trials += len(moves)   # the sequential trials these replace
                self._decision(
                    f"[ripup_reroute] iter {it}: contender {ci}/{n_cont} "
                    f"bundle {bid} — no improvement",
                    "rr_heartbeat", it=it, ci=ci, bid=bid)
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
        self._decision(
            f"[ripup_reroute] iter {it}: screened scan stalled — "
            f"sweeping {len(flat)} deferred move(s) in parallel",
            "rr_stall_sweep", it=it, n_moves=len(flat))
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
                self._decision(
                    f"[ripup_reroute] iter {it}: contender "
                    f"{ci}/{n_cont} bundle {bid} improves "
                    f"{self._rr_m_str(cur)}->"
                    f"{self._rr_m_str(cand_best[0])} "
                    f"({self._rr_move_str(old_tidx, cand_best[3])}"
                    f", deferred)",
                    "rr_improve", it=it, ci=ci, bid=bid, m_from=cur,
                    m_to=cand_best[0],
                    move=self._rr_move_str(old_tidx, cand_best[3]),
                    deferred=True)
                return cand_best, trials
            if ok:
                self._decision(
                    f"[ripup_reroute] WARNING: parallel-sweep divergence "
                    f"on bundle {bid} topo {old_tidx + 1}->{tidx + 1} "
                    f"(sweep {self._rr_m_str(m)} vs replay non-improving) "
                    f"— replay verdict kept",
                    "rr_divergence", it=it, bid=bid,
                    move=f"{old_tidx + 1}->{tidx + 1}")
        return None, trials
