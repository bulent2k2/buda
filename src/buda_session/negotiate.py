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


"""Measured-congestion negotiation (`negotiate_congestion`) — Phase D-2.

Extracted VERBATIM from RipupMixin (risk_reduction_plan.md R3): the
band-demand-injection healer, its template-class price translation
(negotiate v2), and `_heal_dead_spans` — the stage-b dead-span
escalation fold SHARED by both healers (ripup_reroute calls it via self
too, which is why it lives with negotiate rather than duplicating).
Snapshots/restores ride RRStateMixin; trial plumbing and metric helpers
ride RipupMixin; member sets disjoint (guarded by buda_session.MIXINS).
"""

import contextlib
import io
import os
import time

import buda

from .util import _RR_HEAL_DEAD_SPANS_DEFAULT


class NegotiateMixin:
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
