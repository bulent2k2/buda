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
import io

import buda

from .util import _RR_DEFAULT_MAX_ITER, _RR_MAX_CANDIDATES_PER_BUNDLE


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
            return 'b', (lambda: (self.detailed_result.num_unplaced,
                                  self.nuts_result.num_overlaps
                                  if self.nuts_result is not None else 0))
        if self.nuts_result is not None:
            return 'a', (lambda: self.nuts_result.num_overlaps)
        return None, None

    @staticmethod
    def _rr_m_primary(m):
        """The metric's primary component (opens/overlaps count)."""
        return m[0] if isinstance(m, tuple) else m

    @staticmethod
    def _rr_m_str(m):
        """Readable metric for progress lines: '60' or '60 (ovl 9)'."""
        return f"{m[0]} (ovl {m[1]})" if isinstance(m, tuple) else str(m)

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
        if stage == 'b':
            for bid in self._rr_open_bundles():
                add(bid)
        for bid in self._rr_overlap_bundles():
            add(bid)
        # Junction infeasibilities (Part B): a bundle whose junction edge could
        # only close by a large partner stretch is a re-route contender even
        # when the stretch produced no overlap — an alternate topology may
        # avoid the compromise entirely.  Listed after overlaps: they are a
        # quality signal, overlaps are a hard one.
        if self.nuts_result is not None:
            for ji in self.nuts_result.junction_infeasibilities:
                add(ji.bundle_id)
        if not order:                       # fallback: any re-routable bundle
            for w in self.bundles:
                if len(w.input.candidates) > 1:
                    add(w.input.original_bundle.id)
        return order

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
        # below can't undo it.  Capture the slot's Topology (the pybind list
        # conversion hands back an independent copy) so restore can put the
        # committed split geometry back.
        for bid, slot in self._dogleg_slot.items():
            w = self._rr_wrapper(bid)
            if w is not None and 0 <= slot < len(w.input.candidates):
                snap['dl_cand'][bid] = w.input.candidates[slot]
        return snap

    def _rr_restore(self, snap):
        for w in self.bundles:
            bid = w.input.original_bundle.id
            cap = snap['wrap'].get(bid)
            if cap is None:
                continue
            (sel, pinned, ncand, seg_layers, seg_perp,
             seg_net_pull, seg_slide_lo, seg_slide_hi, av, ah,
             pinned_layers) = cap
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

    def _run_nuts_internal(self):
        """Solve abstract NUTS for the current plan WITHOUT the run_nuts
        command's reporting/persistence (nuts-log write, diagnostics, BDB
        persist + route-snapshot hash).  Used by ripup_reroute reruns: nearly
        every trial result is discarded, and _checkpoint_routing persists the
        final accepted state once at the end."""
        nuts = buda.NUTSEngine(self.fp, self.layers)
        nuts.set_track_pitch(self._nuts_pitch)
        self._inject_bottom_up_fixed(nuts)
        if self.planner is not None:
            nuts.set_extra_grid_points(
                list(self.planner.get_x_grid()),
                list(self.planner.get_y_grid()))
        with buda.ostream_redirect():
            self.nuts_result = nuts.run(self.bundles)
        self._adopt_doglegs()

    def _rr_rerun(self, stage, target_bid=None):
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
        unrelated to the topology move."""
        exact = False
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), buda.ostream_redirect():
            asn = None
            if target_bid is not None and self.planner is not None:
                asn = self.planner.replan_bundle(self.bundles, target_bid)
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
                self.do_command(f"run_planner {self._planner_iterations}")
            self._run_nuts_internal()
            if stage == 'b':
                self._run_detailed_nuts(bit_order=self._detailed_bit_order)
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

    def _rr_candidate_order(self, w, old_tidx, stage):
        """Alternate-candidate trial order for one contender (wishlist-ripup
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
        to rank by, no evidence to justify extra trials)."""
        cap = _RR_MAX_CANDIDATES_PER_BUNDLE
        n = min(len(w.input.candidates), cap)
        idxs = [i for i in range(n) if i != old_tidx]
        sites = self._rr_contention_centres(stage, w.input.original_bundle.id)
        if not sites:
            return idxs
        extras = [i for i in range(n, len(w.input.candidates)) if i != old_tidx]
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

    def _gen_hv(self):
        """The generator's top H / top V layer ids (used to hint flipped MST legs
        by direction, mirroring _make_topo_gen)."""
        return (self.layers.get_top_layer(buda.LayerDir.HORIZONTAL),
                self.layers.get_top_layer(buda.LayerDir.VERTICAL))

    def _rr_flip_edges(self, w, stage):
        """MST edge_ids of w's SELECTED candidate that a current contention touches
        (step 4b).  A per-edge L/Z flip is an alternate move alongside the index
        alternates: map each overlap/open segment of this bundle -> its seg's
        edge_id (>= 0, deduped).  Empty unless the selected candidate is an MST
        type carrying edge tags, so non-MST bundles pay nothing."""
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

    def _rr_apply_move(self, w, move, sel, stage, metric):
        """Apply a ripup move + re-run the pipeline; return the metric (or None
        if the move is an invalid flip that changed nothing).  Two kinds:
          ('idx', tidx) — pin candidate tidx (the wrapper's index alternate).
          ('flip', eid) — flip edge eid's L/Z bend on the SELECTED candidate in
            place, then re-pin that same index.  The flip preserves segment slots
            and far-endpoint taps (only the internal bend moves), so only
            seg_conns needs re-deriving (annotate_seg_conns — no fp, hier-safe)."""
        if move[0] == 'idx':
            return self._rr_trial(w, move[1], stage, metric)
        cands = w.input.candidates
        if not (0 <= sel < len(cands)):
            return None
        h, v = self._gen_hv()
        if not buda.flip_mst_edge(cands[sel], move[1], h, v, self.fp):
            return None                          # alt bend on an obstacle: no move
        buda.annotate_seg_conns(cands[sel])
        return self._rr_trial(w, sel, stage, metric)

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

    @staticmethod
    def _rr_move_str(old_tidx, move):
        if move[0] == 'idx':
            return f"topo {old_tidx + 1}->{move[1] + 1}"
        return f"flip edge {move[1]} (topo {old_tidx + 1})"

    def _rr_trial(self, w, tidx, stage, metric):
        """Pin w to candidate tidx, re-run the pipeline, return metric (no restore)."""
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
        self._rr_rerun(stage, target_bid=bid)
        return metric()

    def _open_segments(self):
        """(bundle_id, seg_idx, missing_bits, expected_bits) for every
        under-placed segment of the current detailed result (the per-bundle
        rollup of the same walk is _rr_open_bundles)."""
        if self.detailed_result is None:
            return []
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
        return out

    def _negotiate_congestion(self, max_iter=5):
        """Measured-congestion negotiation (wishlist-ripup item 1).  Instead of
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
        m0 = metric()
        if self._rr_m_primary(m0) == 0:
            print(f"[negotiate] stage {stage}: metric already 0 — nothing to do.")
            return
        what = "DNUTS opens" if stage == 'b' else "NUTS overlaps"
        print(f"[negotiate] stage {stage} ({what}): start "
              f"metric={self._rr_m_str(m0)}, max_iter={max_iter}", flush=True)
        history = {}          # contention rectangle -> times seen (pressure)
        accepted = 0
        for it in range(1, max_iter + 1):
            cur = metric()
            if cur == ((0, 0) if isinstance(cur, tuple) else 0):
                break
            snap = self._rr_snapshot()
            self.planner.clear_injected_demand()
            affected = []
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
                    n_sites += 1
                    if bid not in affected:
                        affected.append(bid)
            # Planner convention: widest first.
            affected.sort(
                key=lambda b: -(self._rr_wrapper(b).input.width
                                if self._rr_wrapper(b) is not None else 0.0))
            sink = io.StringIO()
            with contextlib.redirect_stdout(sink), buda.ostream_redirect():
                for bid in affected:
                    w = self._rr_wrapper(bid)
                    if w is None:
                        continue
                    # A hier.locked wrapper (bottom-up template instance) is
                    # never a negotiation target: its pinned assignment is a
                    # uniform copy shared by all sibling instances.  Its
                    # overlap partner (if unlocked) still replans around it —
                    # and replan_bundle_ripup's victim stage skips locked
                    # blockers C++-side too.
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
                self._run_nuts_internal()
                if stage == 'b':
                    self._run_detailed_nuts(bit_order=self._detailed_bit_order)
            new = metric()
            if new < cur:
                accepted += 1
                print(f"[negotiate] iter {it}: {n_sites} contention site(s) -> "
                      f"replanned {len(affected)} bundle(s), metric "
                      f"{self._rr_m_str(cur)}->{self._rr_m_str(new)}", flush=True)
            else:
                self._rr_restore(snap)
                print(f"[negotiate] iter {it}: no improvement "
                      f"(metric {self._rr_m_str(cur)}->{self._rr_m_str(new)}) "
                      f"— restored, stop.", flush=True)
                break
        # Never leak injected demand into later commands: ripup_reroute's
        # replan_bundle trials would silently re-apply it.
        self.planner.clear_injected_demand()
        print(f"[negotiate] done: metric {self._rr_m_str(m0)}->"
              f"{self._rr_m_str(metric())} "
              f"after {accepted} accepted iteration(s).", flush=True)
        if self.bdb is not None and accepted:
            self._checkpoint_routing()
            print(f"[BDB] re-persisted post-negotiate routing "
                  f"({accepted} iteration(s) accepted).")

    def _ripup_reroute(self, max_iter=_RR_DEFAULT_MAX_ITER,
                       use_edge_candidates=False):
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

        m0 = metric()
        if self._rr_m_primary(m0) == 0:
            print(f"[ripup_reroute] stage {stage}: metric already 0 — nothing to do.")
            return
        what = "DNUTS opens" if stage == 'b' else "NUTS overlaps"
        print(f"[ripup_reroute] stage {stage} ({what}): "
              f"start metric={self._rr_m_str(m0)}, "
              f"max_iter={max_iter}, {len(self._rr_contenders(stage))} contenders",
              flush=True)

        committed = 0
        it = 0
        n_trials = 0
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
            # First-improving-contender.  Each trial is a full pipeline re-run
            # (~1-2s on a large hier design), so the old "best move over ALL
            # contenders" sweep cost contenders*candidates re-runs per iteration —
            # minutes of silent work (e.g. 25 contenders * ~8 candidates ≈ 150
            # re-runs ≈ 3+ min with no output, which reads as a hang).  Instead,
            # scan contenders in priority order and commit the FIRST whose best
            # alternate candidate strictly lowers the metric; a per-contender
            # heartbeat (flushed) makes progress visible.
            for ci, bid in enumerate(contenders, 1):
                w = self._rr_wrapper(bid)
                if w is None:
                    continue
                old_tidx = snap['wrap'][bid][0]
                cand_best = None                 # (metric, bid, old_tidx, move)
                # Two move sources for this contender:
                #   ('idx', t)  — pin an alternate candidate index (existing).
                #   ('flip', e) — flip one contended MST edge's L/Z bend on the
                #                 SELECTED candidate in place (step 4b), keeping
                #                 the index.  Only contended edges are tried, so
                #                 cost stays ~linear in overflows, not 2^N.
                # Relevance-first index order (item 4): candidates farthest from
                # the measured contention are the likeliest fixes.
                moves = [('idx', t)
                         for t in self._rr_candidate_order(w, old_tidx, stage)]
                # The per-edge MST L/Z flip move-source is opt-in
                # (`use_edge_candidates`): on the current corpus a flip is only
                # ever *tried* on real contended MST edges — an index alternate
                # always wins the commit — so it is off by default (routes
                # unchanged) and enabled only when asked to explore edge flips.
                if use_edge_candidates:
                    moves += [('flip', e)
                              for e in self._rr_flip_edges(w, stage)]
                zero = (0, 0) if isinstance(cur, tuple) else 0
                for move in moves:
                    m = self._rr_apply_move(w, move, old_tidx, stage, metric)
                    if m is None:
                        continue                 # invalid flip (bend on obstacle)
                    self._rr_undo_move(w, move, old_tidx)
                    self._rr_restore(snap)
                    n_trials += 1
                    if m < cur and (cand_best is None or m < cand_best[0]):
                        cand_best = (m, bid, old_tidx, move)
                    # Absolute best (stage b: 0 opens AND 0 overlaps) — take it
                    # now.  A merely-primary zero keeps scanning: among moves
                    # that clear the opens, the lexicographic metric still
                    # prefers the one with the least collateral overlap.
                    if m == zero:
                        cand_best = (m, bid, old_tidx, move)
                        break
                if cand_best is not None:
                    best = cand_best
                    print(f"[ripup_reroute] iter {it}: contender {ci}/{n_cont} "
                          f"bundle {bid} improves {self._rr_m_str(cur)}->"
                          f"{self._rr_m_str(cand_best[0])} "
                          f"({self._rr_move_str(old_tidx, cand_best[3])})",
                          flush=True)
                    break
                print(f"[ripup_reroute] iter {it}: contender {ci}/{n_cont} "
                      f"bundle {bid} — no improvement", flush=True)
            if best is None:
                print(f"[ripup_reroute] iter {it}: no improving re-route "
                      f"(metric={self._rr_m_str(cur)}) — stop.")
                stopped_early = True
                break
            m_new, bid, old_t, move = best
            # Commit: re-apply the winning move (geometry already restored to
            # baseline by the last _rr_restore, so re-flip if it was a flip).
            self._rr_apply_move(self._rr_wrapper(bid), move, old_t, stage, metric)
            committed += 1
            print(f"[ripup_reroute] iter {it}: COMMIT bundle {bid} "
                  f"{self._rr_move_str(old_t, move)}, metric {self._rr_m_str(cur)}->"
                  f"{self._rr_m_str(metric())}", flush=True)

        if not stopped_early and self._rr_m_primary(metric()) > 0:
            print(f"[ripup_reroute] reached max_iter={max_iter} while still "
                  f"improving — re-run ripup_reroute or raise max_iter "
                  f"(e.g. `ripup_reroute {max_iter * 5}`) to continue.", flush=True)
        print(f"[ripup_reroute] done: metric {self._rr_m_str(m0)}->"
              f"{self._rr_m_str(metric())} "
              f"after {committed} move(s), {n_trials} trial(s).", flush=True)

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
