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

"""NUTS execution, diagnostics, and post-NUTS reporting.

The detailed-NUTS runner, per-layer NUTS re-solve, post-NUTS stub layer
reassignment, NUTS log writing + diagnostics, wirelength reporting, the
planner capacity-mode / iteration parsing, and the checkpoint/replan/rerun
plumbing shared by the feedback loops.

Methods extracted verbatim from buda_cli.BudaSession (the CLI mixin
split); bodies unchanged — `self` is the composed BudaSession, so
cross-mixin helper calls resolve through the class as before.
"""
import math
import os
import sys

import buda


class NutsFlowMixin:

    @staticmethod
    def _wirelength_by_bundle(segments):
        """Sum routing-direction length |span_hi - span_lo| per bundle and per
        layer over a placed-segment list (TrackSegment for abstract NUTS, or
        NetSegment for detailed).  Unplaced abstract segments (a TrackSegment
        with placed=False) contribute no wire and are counted; NetSegments are
        always placed bit-wires (their unplaced count comes from
        DetailedNUTSResult.num_unplaced).  Returns (per_bundle: {bid: WL},
        per_layer: {layer: WL}, total: WL, n_unplaced_segs: int)."""
        per_bundle, per_layer, total, n_unplaced = {}, {}, 0.0, 0
        for s in segments:
            if getattr(s, 'placed', True) is False:
                n_unplaced += 1
                continue
            length = abs(s.span_hi - s.span_lo)
            per_bundle[s.bundle_id] = per_bundle.get(s.bundle_id, 0.0) + length
            per_layer[s.layer] = per_layer.get(s.layer, 0.0) + length
            total += length
        return per_bundle, per_layer, total, n_unplaced

    def _fp_extent(self, fp=None):
        """Floorplan coordinate extent (block edges) grown by one span each side.
        Used to clamp an untightened (INT-sentinel) slide range so the WL interval
        stays finite.  None when the floorplan is empty.  `fp` defaults to
        `self.fp`; the WL-envelope path passes the floorplan the topology was
        generated against (a hier template's cell-local one)."""
        xs, ys = (fp if fp is not None else self.fp).get_hanan_grid()
        if not xs or not ys:
            return None
        mx, Mx, my, My = min(xs), max(xs), min(ys), max(ys)
        dx = (Mx - mx) or 1
        dy = (My - my) or 1
        return (mx - dx, Mx + dx, my - dy, My + dy)

    _WL_SENT = 10 ** 8   # ConnTopology marks an unbounded slide with ~5e8

    def _seg_slide_box(self, segs, slide_lo=None, slide_hi=None, fp=None):
        """Per-segment perpendicular slide box [lo, hi], with untightened
        (INT-sentinel) ranges clamped to the floorplan extent (then perp_pos).

        `slide_lo`/`slide_hi` (a bundle plan's `seg_slide_lo`/`seg_slide_hi`,
        indexed by ConnSeg) override ConnTopology's window for a segment when set
        (non-NaN) — this is what NUTS honours when a dogleg was adopted
        (`nuts.cpp` slide_map; `_adopt_doglegs`), so the envelope must use the same
        window or a doglegged bundle reads as false out-of-envelope."""
        ext = self._fp_extent(fp)
        SENT = self._WL_SENT
        use_ovr = (slide_lo is not None and slide_hi is not None
                   and len(slide_lo) == len(segs) and len(slide_hi) == len(segs))
        box = []
        for i, cs in enumerate(segs):
            if use_ovr:
                olo, ohi = slide_lo[i], slide_hi[i]
                if olo == olo and ohi == ohi:          # both non-NaN
                    # Overrides are floats; round to int so the integer coordinate
                    # descent below stays integer (sub-unit precision is irrelevant
                    # for a WL diagnostic).
                    box.append((int(round(min(olo, ohi))),
                                int(round(max(olo, ohi)))))
                    continue
            lo = min(cs.perp_lo, cs.perp_hi)
            hi = max(cs.perp_lo, cs.perp_hi)
            if ext is not None:
                axlo, axhi = (ext[2], ext[3]) if cs.horiz else (ext[0], ext[1])
                if lo < -SENT:
                    lo = axlo
                if hi > SENT:
                    hi = axhi
            if lo < -SENT:
                lo = cs.perp_pos
            if hi > SENT:
                hi = cs.perp_pos
            box.append((lo, hi))
        return box

    def _topology_wl_interval(self, topo, slide_lo=None, slide_hi=None, fp=None):
        """[lo, hi] abstract wirelength envelope from the topology's slide + span
        DOF.  Model: each ConnTopology segment's along-span = max − min over its
        junction / busterm coordinates; a busterm coordinate is fixed at its face,
        a SEG-junction coordinate rides the CONNECTED segment's perpendicular
        slide.  The free variable is each segment's perpendicular position within
        [perp_lo, perp_hi] (INT-sentinel ranges clamped to the floorplan extent).

          hi (loose upper): each segment independently stretched to its max span —
              a valid but loose OUTER upper bound.
          lo (tight lower): the total span MINIMIZED jointly over the slide box by
              convex coordinate descent (a ternary search per coordinate; the
              objective is a sum of |affine| terms and converges to the box
              optimum).  Far tighter than the per-segment sum.  When a dogleg was
              adopted the plan's per-segment slide overrides define the box and the
              jog's own span is excluded (see `_seg_slide_box` / the jog mask), so
              a doglegged bundle stays inside its envelope.

        Returns (lo, hi); (0, 0) for an empty topology.  NUTS jogs are extra wire
        outside the topology and are reported separately, not bracketed here.
        `fp` = the floorplan the topology was generated against (default
        `self.fp`; dump_topologies passes a pre-expansion hier bundle's
        cell-local floorplan so the envelope is finite and honest)."""
        ct = buda.ConnTopology()
        ct.build(topo, fp if fp is not None else self.fp)
        segs = ct.segs()
        n = len(segs)
        if n == 0:
            return (0, 0)
        box = self._seg_slide_box(segs, slide_lo, slide_hi, fp)

        # Per-segment along-coordinate sources: (fixed_value, -1) for a busterm,
        # (None, seg_idx) for a junction riding segment seg_idx's perp position.
        src = []
        for cs in segs:
            lst = []
            for cn in cs.conns:
                if (str(cn.kind).rsplit('.', 1)[-1] == "SEG"
                        and 0 <= cn.seg_idx < n):
                    lst.append((None, cn.seg_idx))
                else:
                    lst.append((cn.at_pos, -1))
            src.append(lst)

        # A NUTS-adopted dogleg leaves a JOG segment in the candidate topology; the
        # report compares the envelope against the NON-jog routed WL, so exclude a
        # jog's OWN span from the sums — but keep it as a coordinate provider (its
        # position still constrains the trunk pieces it bridges).  ct.segs() is
        # built 1:1 from topo.segments, so is_jog maps by index.
        jog = [False] * n
        if len(topo.segments) == n:
            jog = [bool(getattr(topo.segments[i], 'is_jog', False))
                   for i in range(n)]

        # hi — per-segment independent max span (endpoints ride neighbour slide
        # extremes): a valid, loose OUTER upper bound.
        def end_box(cs, coord):
            for cn in cs.conns:
                if (cn.at_pos == coord and cn.is_endpoint
                        and str(cn.kind).rsplit('.', 1)[-1] == "SEG"
                        and 0 <= cn.seg_idx < n):
                    return box[cn.seg_idx]
            return (coord, coord)
        hi = 0
        for i, cs in enumerate(segs):
            if jog[i]:
                continue
            lr = end_box(cs, cs.along_lo)
            hr = end_box(cs, cs.along_hi)
            hi += max(0, hr[1] - lr[0])

        # lo — joint minimum span over the slide box by convex coordinate descent.
        p = [max(box[i][0], min(box[i][1], int(round(segs[i].perp_pos))))
             for i in range(n)]
        neigh = [[] for _ in range(n)]
        for t, lst in enumerate(src):
            for fx, j in lst:
                if fx is None:
                    neigh[j].append(t)

        def span(t):
            if jog[t]:
                return 0                      # jog wire excluded from the sum
            cs = [(p[j] if fx is None else fx) for fx, j in src[t]]
            return (max(cs) - min(cs)) if cs else 0

        def local(s):
            return sum(span(t) for t in neigh[s])

        prev = None
        for _ in range(12):
            for s in range(n):
                a, b = box[s]
                if b <= a:
                    continue
                # The convex objective's minimum is a breakpoint within the
                # neighbours' OTHER coordinates; restrict the search there so the
                # ternary loop is O(log spread), not O(log extent).
                others = []
                for t in neigh[s]:
                    for fx, j in src[t]:
                        if j != s:
                            others.append(p[j] if fx is None else fx)
                if others:
                    a = max(a, min(others))
                    b = min(b, max(others))
                if b <= a:
                    p[s] = max(box[s][0], min(box[s][1], a))
                    continue
                while b - a > 2:
                    m1 = a + (b - a) // 3
                    m2 = b - (b - a) // 3
                    p[s] = m1
                    v1 = local(s)
                    p[s] = m2
                    v2 = local(s)
                    if v1 < v2:
                        b = m2
                    else:
                        a = m1
                best, bv = a, None
                for c in range(a, b + 1):
                    p[s] = c
                    v = local(s)
                    if bv is None or v < bv:
                        bv, best = v, c
                p[s] = best
            tot = sum(span(t) for t in range(n))
            if prev is not None and abs(prev - tot) < 1e-6:
                break
            prev = tot
        lo = min(sum(span(t) for t in range(n)), hi)
        return (lo, hi)

    def _annotate_wl_envelopes(self, wraps, fp=None):
        """Stamp every candidate's slide/span WL envelope onto its Topology
        (`wl_lo`/`wl_hi`) so the planner's opt-in kWLSpread knob can price
        realization risk on top of the nominal segment-sum (the b44
        mis-ranking; see set_planner_param "kWLSpread").  Called by the
        run_planner handlers only when kWLSpread >= 0 — the envelope is a
        per-candidate CONSTANT (independent of contention/plan state), so
        one pass before planning also covers every later replan/rr trial on
        the same candidates.  A candidate whose envelope cannot be derived
        is reset to -1 and falls back to the nominal in the planner.
        Element access on w.input.candidates hands back a reference into the
        C++ vector, so the stamp writes through.  `fp` overrides the
        per-bundle frame resolution for EVERY wrapper — the bottom-up
        template solve passes the cell-local floorplan it already built
        (its wrappers all belong to one cell); default = the same
        cell-local/depth/endpoint resolution dump_topologies uses."""
        topo_fp = (lambda w: fp) if fp is not None \
            else self._make_topo_fp_resolver()
        n_ok = n_fail = 0
        for w in wraps:
            w_fp = topo_fp(w)
            cands = w.input.candidates
            for i in range(len(cands)):
                c = cands[i]
                # Always recompute (never trust a prior stamp): a candidate can
                # be mutated between planner runs (dogleg split, TopoEdit), and
                # the recompute is cheap relative to planning.
                try:
                    lo, hi = self._topology_wl_interval(c, fp=w_fp)
                    c.wl_lo = float(lo)
                    c.wl_hi = float(hi)
                    n_ok += 1
                except Exception:
                    # Clear any prior stamp: a mutated candidate whose
                    # recompute fails must fall back to the nominal, not
                    # keep a stale pre-mutation envelope.
                    c.wl_lo = -1.0
                    c.wl_hi = -1.0
                    n_fail += 1
        msg = f"[Planner] kWLSpread: WL envelopes annotated on {n_ok} candidate(s)"
        if n_fail:
            msg += f" ({n_fail} underivable -> nominal fallback)"
        print(msg)

    def _selected_wl_intervals(self):
        """Per-bundle [lo, hi] WL interval for each bundle's SELECTED topology.
        {bundle_id: (lo, hi)} — bundles without a selection are omitted."""
        out = {}
        for w in self.bundles:
            sel = w.plan.selected_topology_index
            if 0 <= sel < len(w.input.candidates):
                # Pass the plan's per-segment slide overrides (set by
                # _adopt_doglegs) so a doglegged bundle's envelope uses the SAME
                # windows NUTS placed within, not ConnTopology's recomputed ones.
                out[w.input.original_bundle.id] = self._topology_wl_interval(
                    w.input.candidates[sel],
                    list(w.plan.seg_slide_lo), list(w.plan.seg_slide_hi))
        return out

    def _report_wirelength(self):
        """Report routed wirelength per bundle + total, for comparing how a
        change affects interconnect quality.  Prints (and thus logs, via the
        command-capture wrapper) the ABSTRACT bus-level WL whenever run_nuts has
        run — one length per placed bus segment, the metric topology decisions
        move — and additionally the DETAILED bit-level WL (every bit-wire) once
        run_detailed_nuts has run.  A per-layer breakdown shows metal
        distribution (cheap LOW vs premium TOP).

        Every total carries its UNPLACED count: WL sums only placed wire, so a
        lower total that comes from dropped (unplaced) segments/bits is NOT a
        better route — the count sits on the same line so a WL comparison can
        never silently reward incomplete routing.  Every current bundle is
        listed even at 0 WL, so an all-unplaced bundle can't vanish from the
        table."""
        if self.nuts_result is None:
            print("[report_wirelength] no NUTS result — run run_nuts first.")
            return
        layer_names = self._make_layer_names()
        bid_to_wrap = {w.input.original_bundle.id: w for w in self.bundles}
        all_ids = list(bid_to_wrap)

        def bits(bid):
            w = bid_to_wrap.get(bid)
            return len(w.input.original_bundle.get_net_names()) if w else 0

        def layer_line(per_layer):
            return "  by layer: " + "  ".join(
                f"{layer_names.get(l, 'L' + str(l))}={per_layer[l]:.0f}"
                for l in sorted(per_layer)) if per_layer else "  by layer: (none)"

        def emit(title, per_bundle, per_layer, total, unit_hdr):
            # Seed every current bundle at 0 so an all-unplaced bundle shows as
            # a 0-WL row rather than silently disappearing.
            for bid in all_ids:
                per_bundle.setdefault(bid, 0.0)
            print(f"[report_wirelength] {title}:")
            print(f"  {'bundle':>8} {unit_hdr:>6} {'WL':>12}")
            print(f"  {'-'*8} {'-'*6} {'-'*12}")
            for bid in sorted(per_bundle):
                print(f"  {bid:>8} {bits(bid):>6} {per_bundle[bid]:>12.0f}")
            print(f"  {'-'*8} {'-'*6} {'-'*12}")
            print(f"  {'TOTAL':>8} {'':>6} {total:>12.0f}")
            print(layer_line(per_layer))

        ab_b, ab_l, ab_t, ab_unpl = self._wirelength_by_bundle(
            self.nuts_result.segments)
        # Per-bundle interval + jog split.  The interval brackets the topology's
        # own segments; NUTS-inserted dogleg jogs are extra wire outside the
        # topology, so they are shown in their own column and excluded from the
        # bracketed WL (else a jog-heavy bundle would read as "above envelope").
        intervals = self._selected_wl_intervals()
        jog_b = {}
        for ts in self.nuts_result.segments:
            if getattr(ts, 'placed', True) is False:
                continue
            if getattr(ts, 'is_jog', False):
                jog_b[ts.bundle_id] = jog_b.get(ts.bundle_id, 0.0) \
                    + abs(ts.span_hi - ts.span_lo)
        for bid in all_ids:
            ab_b.setdefault(bid, 0.0)

        print("[report_wirelength] Abstract bus-level wirelength (after run_nuts) "
              "vs the topology's slide/span DOF envelope [lo..hi]:")
        print(f"  {'bundle':>8} {'bits':>5} {'lo':>9} {'WL':>9} {'hi':>9} "
              f"{'jog':>6} {'fill':>6}")
        dsh = f"  {'-'*8} {'-'*5} {'-'*9} {'-'*9} {'-'*9} {'-'*6} {'-'*6}"
        print(dsh)
        tlo = thi = tseg = tjog = 0.0
        inside = graded = 0
        for bid in sorted(ab_b):
            jog = jog_b.get(bid, 0.0)
            seg_wl = ab_b[bid] - jog          # non-jog topology WL (bracketed)
            tseg += seg_wl
            tjog += jog
            lohi = intervals.get(bid)
            if lohi is None:                  # no selection (unrouted bundle)
                print(f"  {bid:>8} {bits(bid):>5} {'—':>9} {seg_wl:>9.0f} "
                      f"{'—':>9} {jog:>6.0f} {'—':>6}")
                continue
            lo, hi = lohi
            tlo += lo
            thi += hi
            in_env = (lo - 0.5 <= seg_wl <= hi + 0.5)
            inside += in_env
            graded += 1
            width = hi - lo
            fill = f"{100*(seg_wl-lo)/width:>4.0f}%" if width > 0 else " flat"
            mark = "" if in_env else " *"
            print(f"  {bid:>8} {bits(bid):>5} {lo:>9.0f} {seg_wl:>9.0f} "
                  f"{hi:>9.0f} {jog:>6.0f} {fill}{mark}")
        print(dsh)
        print(f"  {'TOTAL':>8} {'':>5} {tlo:>9.0f} {tseg:>9.0f} {thi:>9.0f} "
              f"{tjog:>6.0f}")
        print(layer_line(ab_l))
        # Final, greppable summary line (matches the terminal-headline markers).
        # The unplaced count rides the same line so a WL comparison always sees
        # whether a lower number means "tighter" or merely "incomplete".
        print(f"[report_wirelength] total abstract WL = {ab_t:.0f} "
              f"(seg {tseg:.0f} + jog {tjog:.0f}) over {len(all_ids)} bundle(s), "
              f"{ab_unpl} unplaced segment(s); DOF envelope [{tlo:.0f}..{thi:.0f}], "
              f"{inside}/{graded} bundle(s) inside")
        print("  ('WL' = topology-segment wire the envelope brackets; 'lo' = "
              "tightest routing the DOF allow (joint slide minimum), 'hi' = loose "
              "outer bound; 'fill' = where WL sits in [lo..hi], lower = tighter; "
              "'*' = routed WL outside the envelope, a rare residual slide-model "
              "gap.)")
        if ab_unpl:
            print(f"  NOTE: {ab_unpl} abstract segment(s) unplaced — this WL "
                  f"excludes them and is NOT comparable to a complete route.")

        if self.detailed_result is not None:
            de_b, de_l, de_t, _ = self._wirelength_by_bundle(
                self.detailed_result.net_segments)
            n_wires = len(self.detailed_result.net_segments)
            n_unpl = self.detailed_result.num_unplaced   # authoritative bit count
            emit("Detailed bit-level wirelength (after run_detailed_nuts)",
                 de_b, de_l, de_t, "bits")
            # Bit-scaled envelope: the abstract interval is a per-bus (one-wire)
            # bound, so multiply each bundle's [lo, hi] by its bit count for a
            # bit-level envelope to bracket the detailed WL against.  Per-bit
            # jogs/vias add wire beyond a flat scale, so detailed WL can ride
            # higher in — or slightly above — this scaled envelope.
            dlo = sum(intervals[bid][0] * bits(bid) for bid in intervals)
            dhi = sum(intervals[bid][1] * bits(bid) for bid in intervals)
            print(f"[report_wirelength] total detailed WL = {de_t:.0f} "
                  f"over {len(all_ids)} bundle(s) / {n_wires} bit-wire(s), "
                  f"{n_unpl} unplaced bit(s); bit-scaled envelope "
                  f"[{dlo:.0f}..{dhi:.0f}]")
            if n_unpl:
                print(f"  NOTE: {n_unpl} bit(s) unplaced — this WL excludes "
                      f"them and is NOT comparable to a complete route.")

    def _write_nuts_log(self, layer_names=None, append=False, rerun_layer_name=None,
                        extra_lines: list[str] | None = None):
        """Write (or append to) the per-overlap log file alongside the .buda script.

        File: <script_stem>_nuts.log  (or nuts.log if no script path).
        Mirrors key [NUTS] console messages then lists per-overlap detail.

        append=True       — append a re-run section instead of overwriting.
        rerun_layer_name  — label shown in the re-run header (append mode only).
        extra_lines       — additional lines (e.g. [Planner] messages) written
                            before the NUTS summary, so the log stays in the
                            same order as the console output.
        """
        if self.nuts_result is None:
            return
        if layer_names is None:
            layer_names = self._make_layer_names()

        log_path = self._get_log_path('nuts.log')

        details = self.nuts_result.overlap_details
        per_layer = self.nuts_result.overlaps_per_layer

        # Build a segment label map: (bundle_id, seg_idx) -> display name
        seg_label = {}
        for w in self.bundles:
            bid   = w.input.original_bundle.id
            nets  = w.input.original_bundle.get_net_names()
            hint  = nets[0] if nets else f"B{bid}"
            if not w.input.candidates or w.plan.selected_topology_index < 0 or w.plan.selected_topology_index >= len(w.input.candidates):
                continue  # bundle has no topology (e.g. src==dst or no candidates generated)
            topo  = w.input.candidates[w.plan.selected_topology_index]
            for si, seg in enumerate(topo.segments):
                lname = layer_names.get(seg.layer_hint, f"L{seg.layer_hint}")
                seg_label[(bid, si)] = f"B{bid}.{lname}[{si}]"

        # Compute layer count from segments (mirrors C++ "[NUTS] N segments placed across K layer(s)")
        layer_ids_used = {s.layer for s in self.nuts_result.segments}
        n_layers = len(layer_ids_used)

        from datetime import datetime
        open_mode = 'a' if append else 'w'
        with open(log_path, open_mode) as f:
            script_name = os.path.basename(self.script_path) if self.script_path else '(interactive)'
            if append:
                f.write(f"\n{'='*60}\n")
                if rerun_layer_name:
                    f.write(f"  Re-run: {rerun_layer_name}  —  {script_name}\n")
                else:
                    f.write(f"  Re-run  —  {script_name}\n")
                f.write(f"  At        : {datetime.now().isoformat(timespec='seconds')}\n")
                f.write(f"{'='*60}\n\n")
            else:
                f.write(f"NUTS Overlap Report — {script_name}\n")
                f.write(f"Generated : {datetime.now().isoformat(timespec='seconds')}\n\n")

            # Mirror any Planner / caller messages that preceded this NUTS run.
            if extra_lines:
                for line in extra_lines:
                    f.write(line + "\n")
                f.write("\n")

            # Mirror the C++ [NUTS] summary line.
            total = self.nuts_result.num_overlaps
            f.write(f"[NUTS] {len(self.nuts_result.segments)} segments placed across "
                    f"{n_layers} layer(s). "
                    f"Track overlaps: {total}, "
                    f"Interval violations: {self.nuts_result.num_violations}.\n")

            layer_summary = '  '.join(
                f"{layer_names.get(lid, f'L{lid}')}={cnt}"
                for lid, cnt in sorted(per_layer.items())
            )
            f.write(f"Overlaps  : {total}  ({layer_summary})\n")
            f.write("\n")

            # Build a placed-segment map for full coordinate lookup.
            ts_map = {(ts.bundle_id, ts.seg_idx): ts for ts in self.nuts_result.segments}

            def _seg_coords(bid, si):
                ts = ts_map.get((bid, si))
                if ts is None:
                    return "    (segment not found)\n"
                return (f"    span=[{ts.span_lo:.1f}, {ts.span_hi:.1f}]"
                        f"  perp_center={ts.track_position:.2f}  width={ts.width:.2f}"
                        f"  → perp=[{ts.track_position - ts.width/2:.2f},"
                        f" {ts.track_position + ts.width/2:.2f}]"
                        f"  interval=[{ts.interval_lo:.1f}, {ts.interval_hi:.1f}]\n")

            if not details:
                f.write("No overlaps.\n")
            else:
                # Group by layer
                by_layer = {}
                for od in details:
                    by_layer.setdefault(od.layer, []).append(od)

                for lid in sorted(by_layer):
                    lname = layer_names.get(lid, f"L{lid}")
                    entries = by_layer[lid]
                    f.write(f"{'='*60}\n")
                    f.write(f"  {lname}  —  {len(entries)} overlap(s)\n")
                    f.write(f"{'='*60}\n")
                    for n, od in enumerate(entries, 1):
                        la = seg_label.get((od.bid_a, od.seg_a), f"B{od.bid_a}[{od.seg_a}]")
                        lb = seg_label.get((od.bid_b, od.seg_b), f"B{od.bid_b}[{od.seg_b}]")
                        span_len = od.span_hi - od.span_lo
                        perp_dep = od.perp_hi - od.perp_lo
                        area     = span_len * perp_dep
                        f.write(
                            f"  [{n:3d}]  {la}  ×  {lb}\n"
                            f"         overlap span  [{od.span_lo:.1f}, {od.span_hi:.1f}]"
                            f"  len={span_len:.1f}\n"
                            f"         overlap perp  [{od.perp_lo:.2f}, {od.perp_hi:.2f}]"
                            f"  depth={perp_dep:.2f}  area={area:.2f}\n"
                        )
                        f.write(f"         {la}:\n" + _seg_coords(od.bid_a, od.seg_a))
                        f.write(f"         {lb}:\n" + _seg_coords(od.bid_b, od.seg_b))
                    f.write("\n")

        action = "appended to" if append else "→"
        print(f"NUTS overlap log {action} {log_path}")

    def extract_instances(self, bundle):
        # Helper to find source/dest instances from a bundle's nets for Topology Generation
        if not bundle.get_net_names(): return "top", "top"
        # Hack: assume first net's driver/receiver pins follow instance.pin format
        first_net_name = bundle.get_net_names()[0]
        # Find this net in the netlist to get its pins. This is inefficient but works for prototype.
        # A real implementation would store src/dst instance on the Bundle object itself.
        driver_pin = ""
        receiver_pin = ""
        # C++ Netlist doesn't expose find_net yet, so we rely on the input script naming convention for the demo.
        # Assuming net name is like 'b1_0' and driver is 'u_cpu.tx'
        # Let's just pass the block names directly in the script for now to simplify the connection.
        return "top", "top"

    def _run_post_nuts_planner(self,
                               v_thresholds: tuple[float, float] | None,
                               h_thresholds: tuple[float, float] | None,
                               top_only: bool = False):
        """Stage 4c — Post-NUTS stub layer reassignment.

        Classifies every bundle's V and/or H stub segments by max span length
        and moves short stubs to the lowest layer and long stubs to the highest
        layer for each direction.  After all reassignments a single full NUTS
        solve is run so all layers are consistent with the new assignments.

        v_thresholds : (short_thresh, long_thresh) for V segments, or None to skip.
        h_thresholds : (short_thresh, long_thresh) for H segments, or None to skip.
        """
        if self.nuts_result is None:
            print("Error: run_planner post_nuts requires run_nuts to have been called first")
            return

        layer_names = self._make_layer_names()
        extra_lines: list[str] = []

        def _reassign_dir(dir_enum, thresholds: tuple[float, float]):
            short_thresh, long_thresh = thresholds
            layers_sorted = sorted(self.layers.get_layer_ids_by_dir(dir_enum))
            dir_label = "V" if dir_enum == buda.LayerDir.VERTICAL else "H"
            is_v = (dir_enum == buda.LayerDir.VERTICAL)
            # `top` mode: reassign within the TOP layers only, so short stubs land on
            # the next-highest TOP layer (not the LOW escape layers) and long hauls on
            # the highest.  E.g. V → {M5(short), M7(long)}, H → {M4(short), M6(long)} —
            # keeping the over-subscribed top layers for long-haul and spreading short
            # stubs onto the next TOP tier instead of the LOW (often track-starved) layers.
            if top_only:
                # Restrict to TOP layers — never fall back to the LOW escape
                # layers, even when this direction has fewer than 2 TOP layers
                # (the `< 2` guard below then no-ops the direction, rather than
                # letting lo_layer become a LOW layer and reintroducing the
                # track-starved LOW placement top-only mode exists to avoid).
                layers_sorted = [l for l in layers_sorted if self.layers.is_top(l)]
            if len(layers_sorted) < 2:
                scope = "TOP " if top_only else ""
                print(f"[Planner] post_nuts {dir_label}: fewer than 2 {scope}{dir_label} layers — nothing to reassign")
                return
            lo_layer = layers_sorted[0]
            hi_layer = layers_sorted[-1]
            layer_set = set(layers_sorted)

            # A locked (bottom-up copy) wrapper's plan must stay identical to
            # its placed fixed routing: reassigning its layers here would
            # diverge plan.seg_layers from the routed copies (extraction
            # skips fixed bundles, so the re-run below cannot move them) and
            # a later load_pipeline would restore an internally inconsistent
            # pin. Excluded from the span map too, so the short/medium/long
            # counts reflect only the reassignable bundles.
            locked_ids = {w.input.original_bundle.id for w in self.bundles
                          if w.hier.locked}

            # Map bundle_id → max span length among segments on this direction's layers.
            bid_max_span: dict[int, float] = {}
            for seg in self.nuts_result.segments:
                if seg.layer not in layer_set or seg.bundle_id in locked_ids:
                    continue
                span_len = seg.span_hi - seg.span_lo
                bid = seg.bundle_id
                if bid not in bid_max_span or span_len > bid_max_span[bid]:
                    bid_max_span[bid] = span_len

            short_count = medium_count = long_count = 0
            for w in self.bundles:
                bid = w.input.original_bundle.id
                if bid not in bid_max_span:
                    continue
                max_span = bid_max_span[bid]
                if max_span < short_thresh:
                    new_layer = lo_layer
                    short_count += 1
                elif max_span > long_thresh:
                    new_layer = hi_layer
                    long_count += 1
                else:
                    medium_count += 1
                    continue

                # Update per-segment layers for segments of this direction.
                # If seg_layers is populated (from run_planner), update it directly;
                # otherwise fall back to the legacy assigned_v/h_layer attribute.
                if not w.input.candidates or w.plan.selected_topology_index < 0 or w.plan.selected_topology_index >= len(w.input.candidates):
                    continue
                topo = w.input.candidates[w.plan.selected_topology_index]
                if w.plan.seg_layers:
                    sl = list(w.plan.seg_layers)
                    for si, seg in enumerate(topo.segments):
                        seg_is_v = (seg.start.y != seg.end.y)
                        if (is_v and seg_is_v) or (not is_v and not seg_is_v):
                            if si < len(sl):
                                sl[si] = new_layer
                    w.plan.seg_layers = sl
                else:
                    if is_v:
                        w.input.assigned_v_layer = new_layer
                    else:
                        w.input.assigned_h_layer = new_layer

            lo_name = layer_names.get(lo_layer, f"L{lo_layer}")
            hi_name = layer_names.get(hi_layer, f"L{hi_layer}")
            msg = (f"[Planner] post_nuts {dir_label}: short<{short_thresh:.0f}→{lo_name} ({short_count}b), "
                   f"medium ({medium_count}b), long>{long_thresh:.0f}→{hi_name} ({long_count}b)")
            print(msg)
            extra_lines.append(msg)

        if v_thresholds is not None:
            _reassign_dir(buda.LayerDir.VERTICAL, v_thresholds)
        if h_thresholds is not None:
            _reassign_dir(buda.LayerDir.HORIZONTAL, h_thresholds)

        # Single NUTS re-run after all reassignments.
        pitch = self._nuts_pitch if hasattr(self, '_nuts_pitch') and self._nuts_pitch else 1.0
        nuts = buda.NUTSEngine(self.fp, self.layers)
        nuts.set_track_pitch(pitch)
        self._inject_bottom_up_fixed(nuts)
        self.nuts_result = nuts.run(self.bundles)
        self._adopt_doglegs()

        layer_names = self._make_layer_names()
        self._write_nuts_log(layer_names, append=True, rerun_layer_name="post_nuts",
                             extra_lines=extra_lines)

    def _segment_states_from_topology(self) -> dict:
        """Build a 'before' snapshot from topology geometry (no track assignment yet).

        track_position = NaN signals 'unplaced'; _nuts_diagnostics skips movement
        stats for those segments so the same diagnostic code works for both the
        initial run_nuts and per-layer rerun_layer calls.
        """
        states: dict[tuple, dict] = {}
        for bw in self.bundles:
            if not bw.input.candidates or bw.plan.selected_topology_index < 0 or bw.plan.selected_topology_index >= len(bw.input.candidates):
                continue
            topo = bw.input.candidates[bw.plan.selected_topology_index]
            bid  = bw.input.original_bundle.id
            for si, seg in enumerate(topo.segments):
                is_h = (seg.start.y == seg.end.y)
                if is_h:
                    span_lo = float(min(seg.start.x, seg.end.x))
                    span_hi = float(max(seg.start.x, seg.end.x))
                    layer   = bw.input.assigned_h_layer if bw.input.assigned_h_layer >= 0 else seg.layer_hint
                else:
                    span_lo = float(min(seg.start.y, seg.end.y))
                    span_hi = float(max(seg.start.y, seg.end.y))
                    layer   = bw.input.assigned_v_layer if bw.input.assigned_v_layer >= 0 else seg.layer_hint
                states[(bid, si)] = {
                    'layer':          layer,
                    'track_position': float('nan'),   # unplaced sentinel
                    'span_lo':        span_lo,
                    'span_hi':        span_hi,
                }
        return states

    def _nuts_diagnostics(self, result, layer_names: dict,
                          before: dict, target_layer: int | None = None) -> list[str]:
        """Emit and collect NUTS diagnostic lines after a solve.

        Shared by run_nuts (target_layer=None, all layers) and
        _rerun_nuts_layer (target_layer=layer_id, focus on one layer).

        before: (bid, seg_idx) -> {layer, track_position, span_lo, span_hi}
            track_position == NaN  →  unplaced; movement stats suppressed.
        target_layer: if set, only that layer is reported per-layer and other
            layers are treated as 'connected' for span-adjustment analysis.
            If None, all layers are reported; span adjustments shown across all.
        """
        diag: list[str] = []

        def emit(msg: str):
            print(msg)
            diag.append(msg)

        by_layer: dict[int, list] = {}
        for s in result.segments:
            by_layer.setdefault(s.layer, []).append(s)

        report_layers = [target_layer] if target_layer is not None else sorted(by_layer.keys())

        for lid in report_layers:
            segs  = by_layer.get(lid, [])
            lname = layer_names.get(lid, f'L{lid}')
            n     = len(segs)

            # Movement stats — only when segment was placed before (track_position not NaN).
            moved_deltas: list[float] = []
            for s in segs:
                bef = before.get((s.bundle_id, s.seg_idx))
                if bef and not math.isnan(bef['track_position']):
                    delta = abs(s.track_position - bef['track_position'])
                    if delta > 1e-6:
                        moved_deltas.append(delta)
            if moved_deltas:
                avg_d = sum(moved_deltas) / len(moved_deltas)
                max_d = max(moved_deltas)
                emit(f"[NUTS] {lname}: {len(moved_deltas)}/{n} segments moved "
                     f"(avg |Δperp|={avg_d:.1f}, max={max_d:.1f})")

            # Report physical width used (helps diagnose dilution issues)
            if segs:
                total_w = sum(s.width for s in segs)
                min_p = min(s.track_position - s.width/2.0 for s in segs)
                max_p = max(s.track_position + s.width/2.0 for s in segs)
                emit(f"[NUTS] {lname}: total bus width {total_w:.1f} units, "
                     f"spanning perpendicular interval [{min_p:.1f}, {max_p:.1f}]")

            # Local overlaps on this layer.
            local_ov = [od for od in result.overlap_details if od.layer == lid]
            if local_ov:
                pairs_str = ', '.join(f"B{od.bid_a}×B{od.bid_b}" for od in local_ov)
                emit(f"[NUTS] {lname} local overlaps: {len(local_ov)} → {pairs_str}")
            else:
                emit(f"[NUTS] {lname}: no local overlaps")

        # Books-vs-metal divergence (wishlist-planner "Charge pulled segments
        # at their predicted pull target"): for each PULLED segment, compare
        # the planner's charged band centre (plan.seg_perp) against the placed
        # track.  A pulled segment's placement outranks the charged band, so a
        # large divergence means the congestion books reserved capacity where
        # the metal did not go (and vice versa) — the systematic source of
        # "unpredicted" overlaps on the congested corpus (bigHalf: 141/185
        # pulled segments diverged >100 units before the charge fix).
        if target_layer is None:
            sp_by_key = {}
            for w in self.bundles:
                bid = w.input.original_bundle.id
                for si, sp in enumerate(w.plan.seg_perp):
                    if sp != -(2 ** 31):
                        sp_by_key[(bid, si)] = sp
            n_pulled = n_div = 0
            worst = 0.0
            for s in result.segments:
                if s.net_pull == 0 or not s.placed:
                    continue
                sp = sp_by_key.get((s.bundle_id, s.seg_idx))
                if sp is None:
                    continue
                n_pulled += 1
                d = abs(s.track_position - sp)
                if d > 100.0:
                    n_div += 1
                worst = max(worst, d)
            if n_pulled:
                emit(f"[NUTS] books-vs-metal: {n_div}/{n_pulled} pulled "
                     f"segment(s) placed >100 units from the planner's "
                     f"charged band (worst Δ={worst:.0f})")

        # Span adjustments: compare before vs after spans.
        # For rerun: skip the target layer (it drove the adjustment).
        # For full run: report all layers.
        span_adj: dict[int, int] = {}
        for s in result.segments:
            if target_layer is not None and s.layer == target_layer:
                continue
            bef = before.get((s.bundle_id, s.seg_idx))
            if bef and (abs(s.span_lo - bef['span_lo']) > 1e-6 or
                        abs(s.span_hi - bef['span_hi']) > 1e-6):
                span_adj[s.layer] = span_adj.get(s.layer, 0) + 1

        if span_adj:
            label   = "Connected span adjustments" if target_layer is not None else "Span adjustments"
            adj_str = ', '.join(
                f"{layer_names.get(lid, f'L{lid}')}:{cnt}"
                for lid, cnt in sorted(span_adj.items())
            )
            emit(f"[NUTS] {label}: {adj_str}")

            # Post-adjust overlaps on adjusted layers.
            # For full run these equal the global overlap summary — skip to avoid noise.
            if target_layer is not None:
                adj_layer_ids = set(span_adj)
                post_ov_by_layer: dict[int, list] = {}
                for od in result.overlap_details:
                    if od.layer in adj_layer_ids:
                        post_ov_by_layer.setdefault(od.layer, []).append(od)
                if post_ov_by_layer:
                    summary = ', '.join(
                        f"{layer_names.get(lid, f'L{lid}')}:{len(ods)}"
                        for lid, ods in sorted(post_ov_by_layer.items())
                    )
                    all_pairs = ', '.join(
                        f"B{od.bid_a}×B{od.bid_b}"
                        for ods in post_ov_by_layer.values()
                        for od in ods
                    )
                    emit(f"[NUTS] Post-adjust overlaps: {summary} → {all_pairs}")
                else:
                    adj_names = ', '.join(
                        layer_names.get(lid, f'L{lid}') for lid in sorted(adj_layer_ids))
                    emit(f"[NUTS] No overlaps on adjusted layers ({adj_names})")
        elif target_layer is not None:
            emit(f"[NUTS] No connected span adjustments")

        return diag

    def _rerun_nuts_layer(self, layer_id: int):
        """Re-solve one layer with NUTS, emit diagnostics, and log.

        Returns the updated NUTSResult (also stored in self.nuts_result).
        Used by both the run_nuts_on_layer command and the visualizer ↺ button.
        """
        layer_names  = self._make_layer_names()
        layer_name   = layer_names.get(layer_id, f"L{layer_id}")
        nuts = buda.NUTSEngine(self.fp, self.layers)
        nuts.set_track_pitch(self._nuts_pitch)
        self._inject_bottom_up_fixed(nuts)
        if self.planner is not None:
            nuts.set_extra_grid_points(
                list(self.planner.get_x_grid()),
                list(self.planner.get_y_grid()))

        # Snapshot full state before rerun.
        before: dict[tuple, dict] = {
            (s.bundle_id, s.seg_idx): {
                'layer':          s.layer,
                'track_position': s.track_position,
                'span_lo':        s.span_lo,
                'span_hi':        s.span_hi,
            }
            for s in self.nuts_result.segments
        }
        n_layer_segs = sum(1 for s in self.nuts_result.segments if s.layer == layer_id)

        pre_msg = f"[NUTS] Running {layer_name}: {n_layer_segs} segment(s)"
        print(pre_msg)

        # C++ also prints its own [NUTS] rerun_layer(...) line here.
        with buda.ostream_redirect():
            self.nuts_result = nuts.rerun_layer(self.nuts_result, self.bundles, layer_id)

        diag = self._nuts_diagnostics(self.nuts_result, layer_names, before,
                                      target_layer=layer_id)

        rerun_msg = (f"[NUTS] rerun_layer({layer_id}={layer_name}): "
                     f"{n_layer_segs} segment(s) re-placed. "
                     f"Violations: {self.nuts_result.num_violations}, "
                     f"Overlaps: {self.nuts_result.num_overlaps}.")
        print(rerun_msg)

        self._write_nuts_log(layer_names, append=True, rerun_layer_name=layer_name,
                             extra_lines=[pre_msg] + diag + [rerun_msg])

        # Deliberately NO persist here: this helper is also the visualizer's
        # interactive ↺ preview (rerun_layer_fn), and exploring an alternative
        # solve must not overwrite the BDB checkpoint. Committing paths (the
        # run_nuts_on_layer command, ripup_reroute) call _checkpoint_routing().
        if self.detailed_result is not None:
            self._run_detailed_nuts(bit_order=self._detailed_bit_order)
            return self.nuts_result, self.detailed_result

        return self.nuts_result

    def _checkpoint_routing(self):
        """Persist the FULL current routing state to the open BDB: planner
        selections/layers + abstract-NUTS bus rows (+ detailed rows when
        present). The single commit choke point for engine-driven re-run paths
        that bypass the stage command handlers (ripup_reroute, the
        run_nuts_on_layer command). Interactive visualizer previews (↺ /
        Re-run & Refresh) deliberately do NOT call this — a checkpoint changes
        only on explicit commands, never while exploring. No-op without a BDB.
        """
        if self.bdb is None:
            return
        self._persist_planner_output()
        self._persist_nuts()
        if self.detailed_result is not None:
            self._persist_detailed_nuts()

    def _replan_layers(self):
        """Re-run planner layer assignment on the live planner, honoring
        topology_pinned wrappers, and copy the assignments back onto the
        wrappers.  Band usage is reset first (build_congestion_map): the
        previous run's demand is still recorded on the cuts, and re-planning
        on top of it would double-count every bundle.

        No-op when the planner has not run yet — the pinned selection is
        then honored by the next run_planner.
        """
        if self.planner is None:
            return
        self.planner.build_congestion_map()
        with buda.ostream_redirect():
            assignments = self.planner.optimize_topologies(
                self.bundles, self._planner_iterations)
        bid_to_wrapper = {w.input.original_bundle.id: w for w in self.bundles}
        for asn in assignments:
            w = bid_to_wrapper.get(asn.bundle_id)
            if w is not None:
                w.plan.selected_topology_index = asn.topo_index
                w.input.assigned_v_layer = asn.v_layer_id
                w.input.assigned_h_layer = asn.h_layer_id
                w.plan.seg_layers = list(asn.seg_layers)
                w.plan.seg_perp = list(asn.seg_perp)

    def _rerun_all(self):
        """Apply sidecar topology selections, re-run planner layer assignment,
        then re-run full NUTS.

        Called by the TopoExplorer "Re-run & Refresh" button.
        Returns the updated NUTSResult (also stored in self.nuts_result).
        """
        # Pin topology indices from sidecar.
        self._apply_selections()

        # Re-run the planner so that assigned_h_layer / assigned_v_layer / seg_layers
        # are updated to match the new topology's segment directions.  The planner
        # respects topology_pinned=True set by _apply_selections() and will not
        # override the user's topology choice.
        self._replan_layers()

        layer_names = self._make_layer_names()
        nuts = buda.NUTSEngine(self.fp, self.layers)
        nuts.set_track_pitch(self._nuts_pitch)
        # Bottom-up designs: the frozen template copies must ride EVERY
        # engine construction — the post_nuts and run_nuts_on_layer paths
        # inject them, but this explorer re-run path did not, so a re-run
        # re-solved without the copies and could route through / shift the
        # frozen bottom-up interconnect (audit P4-02).
        self._inject_bottom_up_fixed(nuts)
        if self.planner is not None:
            nuts.set_extra_grid_points(
                list(self.planner.get_x_grid()),
                list(self.planner.get_y_grid()))
        before = self._segment_states_from_topology()
        self.nuts_result = nuts.run(self.bundles)
        self._adopt_doglegs()
        diag = self._nuts_diagnostics(self.nuts_result, layer_names, before)
        self._write_nuts_log(layer_names, append=True,
                             rerun_layer_name="topo-rerun", extra_lines=diag)

        if self.detailed_result is not None:
            self._run_detailed_nuts(bit_order=self._detailed_bit_order)
            return self.nuts_result, self.detailed_result

        return self.nuts_result

    def _install_leaf_keepouts(self):
        """Install implicit solid-leaf-cell keepouts on every non-TOP layer grid
        so signal tracks over cells are excluded — matching the planner and
        abstract NUTS (Gap 2).  Independent of the order in which blocks,
        containers, and track patterns were declared.  Guarded per installed
        (layer, rect) pair — NOT per grid object — so repeated calls (detailed
        re-runs, or a `signal_tracks` plan before DNUTS) don't re-add
        duplicates, while a layer patterned or a block/zone added AFTER the
        first solve still receives its keepouts on the next call (audit
        P4-01: the old grid-identity guard made this a one-shot, leaving
        later-patterned LOW layers routable through solid cells).  No-op
        without a routing grid.

        Also the one grid-sync point for user zones with EMPTY layer_ids, which
        block EVERY layer (keepout-model audit class 3 — the convention shared
        by the topology predicates, keepout_occupied, and the planner's band
        capacity).  Such zones only arise via the Python Floorplan API (the CLI
        requires explicit layers) and def_track_pattern's re-apply skips them,
        so they are installed here on every defined grid, TOP included."""
        if self.routing_grid is None:
            return
        if getattr(self, '_leaf_keepouts_grid', None) is not self.routing_grid:
            self._leaf_keepouts_grid = self.routing_grid
            self._leaf_keepouts_done = set()
        done = self._leaf_keepouts_done

        def _add(lid, bbox):
            key = (lid, bbox.x1, bbox.y1, bbox.x2, bbox.y2)
            if key not in done:
                done.add(key)
                self.routing_grid.add_keepout(lid, bbox.x1, bbox.y1,
                                              bbox.x2, bbox.y2)

        for d in (buda.LayerDir.HORIZONTAL, buda.LayerDir.VERTICAL):
            for lid in self.layers.get_layer_ids_by_dir(d):
                if not self.routing_grid.has_layer(lid):
                    continue
                for koz in self.fp.get_keepout_zones():
                    if not koz.layer_ids:
                        _add(lid, koz.bbox)
                if self.layers.is_top(lid):
                    continue
                for koz in self.fp.low_layer_keepouts([lid]):
                    if lid in koz.layer_ids:
                        _add(lid, koz.bbox)

    @staticmethod
    def _planner_iters(args, default=5):
        """First numeric token in a run_planner arg list, skipping the `hier` and
        `signal_tracks` keywords; `default` if none."""
        for a in args:
            if a in ("hier", "signal_tracks"):
                continue
            try:
                return int(a)
            except ValueError:
                continue
        return default

    def _configure_capacity_mode(self, args):
        """Enable the signal-track band-capacity model on self.planner when the
        `signal_tracks` keyword is present (Gap A part 2).  Requires a routing grid
        with `def_track_pattern` layers; installs the leaf-cell keepouts first so
        the planner counts exactly the tracks DetailedNUTS will place.  Must be
        called after the planner is constructed and before build_congestion_map.
        No-op (width model) without the keyword.

        Requesting `signal_tracks` with no `def_track_pattern` defined is a hard
        error (exit 1), not a silent fall-back: the user asked for a specific
        capacity model that cannot be honoured, and quietly planning with the
        width model instead would hide that the signal-track accounting never
        happened."""
        has_pattern = self.routing_grid is not None and any(
            self.routing_grid.has_layer(lid)
            for d in (buda.LayerDir.HORIZONTAL, buda.LayerDir.VERTICAL)
            for lid in self.layers.get_layer_ids_by_dir(d))
        if "signal_tracks" not in args:
            # Width mode keeps the geometric capacity model, but the planner
            # still receives the routing grid when patterned layers exist:
            # peak_util_segment's absolute-supply floor (kPeak) consults the
            # real signal-track supply in EITHER capacity mode.  Leaf keepouts
            # go in first so the count matches what DetailedNUTS will place
            # from.  The grid is only read behind kPeak>0 / signal-track-mode
            # gates, so default width-mode flows stay bit-identical.
            if has_pattern:
                self._install_leaf_keepouts()
                self.planner.set_routing_grid(self.routing_grid)
            return
        if not has_pattern:
            print("Error: run_planner signal_tracks needs a routing grid to count "
                  "signal tracks, but no def_track_pattern is defined. Add "
                  "def_track_pattern for the routed layers, or drop the "
                  "signal_tracks option to plan with the width model.")
            sys.exit(1)
        self._install_leaf_keepouts()
        self.planner.set_routing_grid(self.routing_grid)
        self.planner.set_capacity_mode(buda.CapacityMode.SIGNAL_TRACKS)

    def _run_detailed_nuts(self, bit_order="LO_HI", emit_vias=True,
                           abort_unplaced=-1):
        """Execute bit-level track assignment using DetailedNUTSEngine.

        emit_vias=False (RR fast trials): skip the per-bit via emission —
        pure output, never read by the stage-b metric, so the trial metric
        is identical; the commit re-runs with vias on.

        abort_unplaced >= 0 (RR fast trials): sound early abort — placement
        stops once the running unplaced count exceeds the committed metric's
        opens (a certain rejection; unplaced never decreases).  Normal path
        only: the bottom-up merge path ignores it (partial r1/r2 results
        cannot be merged meaningfully) — conservative, never wrong."""
        if self.nuts_result is None or self.routing_grid is None:
            return None

        # Match the planner / abstract NUTS by excluding signal tracks over solid
        # leaf cells on LOW layers before the solve.
        self._install_leaf_keepouts()

        # Stage-4 -> stage-9 handoff, single-sourced in C++ (make_bus_segments,
        # detailed_nuts.cpp): every TrackSegment becomes a BusSegment, with the
        # per-segment SEG connections / BUSTERM faces derived from the selected
        # topology's cached analysis — the same derivation the abstract solve
        # placed with, so the two stages can never drift.
        bus_segs = buda.make_bus_segments(self.bundles, self.nuts_result,
                                          self.fp, bit_order)
        # Bottom-up cells (stage c): solve the reference instance once, copy
        # its bits/vias to the aligned siblings, and solve everything else
        # around the copies (their tracks pre-reserved).  May raise under the
        # 'stop' mismatch policy.  None = no bottom-up routing, single run.
        bu_plan = self._bottom_up_dnuts_plan()
        if bu_plan is None:
            engine = buda.DetailedNUTSEngine(self.routing_grid)
            with buda.ostream_redirect():
                self.detailed_result = engine.run(bus_segs, emit_vias,
                                              abort_unplaced)
        else:
            ref_ids, copy_specs, skip_ids = bu_plan
            ref_segs  = [b for b in bus_segs if b.bundle_id in ref_ids]
            rest_segs = [b for b in bus_segs
                         if b.bundle_id not in ref_ids
                         and b.bundle_id not in skip_ids]
            horiz_of = {(ts.bundle_id, ts.seg_idx): ts.horiz
                        for ts in self._bottom_up_fixed_segments()}
            eng1 = buda.DetailedNUTSEngine(self.routing_grid)
            with buda.ostream_redirect():
                r1 = eng1.run(ref_segs, emit_vias)
            # Per-instance copies of the reference bits + vias.  Unplaced
            # reference bits have no rows to copy, so each copy inherits the
            # reference's shortfall in the honest unplaced count.
            # INVARIANT (load-bearing): copies are NOT re-culled against
            # keepouts — they come from the already-culled reference solve,
            # and a sibling is only copied when check_template_tracks proved
            # its span-aware track pools (which INCLUDE keepouts) identical
            # to the reference's: a sibling whose window differs by a keepout
            # is misaligned and never reaches this copy loop.  If the
            # keepout model changes, keep the check's pools in lockstep.
            exp_bits, placed_bits = {}, {}
            for b in ref_segs:
                exp_bits[b.bundle_id] = (exp_bits.get(b.bundle_id, 0)
                                         + b.bit_width)
            for ns in r1.net_segments:
                placed_bits[ns.bundle_id] = placed_bits.get(ns.bundle_id,
                                                            0) + 1
            copies, copy_vias, extra_unplaced = [], [], 0
            for ref_bid, sib_bid, oi, cw, ch, rx, ry, sx, sy in copy_specs:
                for ns in r1.net_segments:
                    if ns.bundle_id == ref_bid:
                        copies.append(buda.transform_net_segment(
                            ns, oi, cw, ch, rx, ry, sx, sy, sib_bid,
                            horiz_of.get((ref_bid, ns.seg_idx), True)))
                for v in r1.net_vias:
                    if v.bundle_id == ref_bid:
                        copy_vias.append(buda.transform_net_via(
                            v, oi, cw, ch, rx, ry, sx, sy, sib_bid))
                extra_unplaced += (exp_bits.get(ref_bid, 0)
                                   - placed_bits.get(ref_bid, 0))
            eng2 = buda.DetailedNUTSEngine(self.routing_grid)
            eng2.add_fixed_bits(list(r1.net_segments) + copies)
            with buda.ostream_redirect():
                r2 = eng2.run(rest_segs, emit_vias)
            merged = buda.DetailedNUTSResult()
            merged.net_segments = (list(r1.net_segments) + copies
                                   + list(r2.net_segments))
            merged.net_vias = (list(r1.net_vias) + copy_vias
                               + list(r2.net_vias))
            merged.num_unplaced = (r1.num_unplaced + extra_unplaced
                                   + r2.num_unplaced)
            merged.num_keepout_bits = (r1.num_keepout_bits
                                       + r2.num_keepout_bits)
            # Carry the per-pass profile through the merge (Codex #289):
            # without it a bottom-up stage-b trial charges the dnuts WALL
            # but contributes no dnuts.* pass buckets — a misleading gap in
            # the round-3 profiling data.
            pp = dict(r1.pass_seconds)
            for k, v in r2.pass_seconds.items():
                pp[k] = pp.get(k, 0.0) + v
            merged.pass_seconds = pp
            self.detailed_result = merged
            print(f"[BottomUp] DNUTS: {len(r1.net_segments)} reference "
                  f"bit(s) solved once, {len(copies)} copied to "
                  f"{len(copy_specs)} sibling instance(s); "
                  f"{len(r2.net_segments)} other bit(s) solved around them.")

        n_net = len(self.detailed_result.net_segments)
        n_unplaced = self.detailed_result.num_unplaced
        print(f"[DetailedNUTS] {n_net} net segments placed, "
              f"{n_unplaced} bits unplaced.")
        return self.detailed_result
