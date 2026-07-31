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

"""Read-only inspection and verification reports.

dump_topologies (+ per-segment connectivity detail), the geometry/slide
helpers they share, check_design (alias check_connectivity) at all three stages, and the
violation summary collapsing (with its class-level constants).

Methods extracted verbatim from buda_cli.BudaSession (the CLI mixin
split); bodies unchanged — `self` is the composed BudaSession, so
cross-mixin helper calls resolve through the class as before.
"""
import buda


class _FidelityViolation:
    """Python-side violation for the net-driver fidelity check — duck-typed
    to the C++ ConnViolation interface the reporting paths consume
    (kind.name / seg indices / block_name / bit_index / message).
    Module-level (like the shared hier helpers) so the mixin carries no
    per-class descriptors the composed-class identity test would reject."""
    class _Kind:
        name = "NET_DRIVER_OPEN"
    kind = _Kind()
    seg_idx = -1
    seg_idx2 = -1
    bit_index = -1

    def __init__(self, block, message):
        self.block_name = block
        self.message = message


def _fidelity_union(block, nets):
    shown = ", ".join(nets[:4]) + (" …" if len(nets) > 4 else "")
    return _FidelityViolation(block, (
        f"NET_DRIVER_OPEN: block '{block}' is an endpoint of net(s) "
        f"{shown} but is not among the topology's connected blocks — "
        f"the bus never attaches it (a fan-in driver dropped by a "
        f"single-source topology?)"))


class _IntrinsicCost:
    """Pre-plan fallback standing in for the C++ `CandidateCost` (same duck-typed
    fields the explorer's debug view reads).  Before `run_planner` there is no
    committed congestion state, so the only cost signal is the candidate's own
    estimated wirelength: `total == wl_term == estimated_wirelength`, congestion
    zero, and no per-segment breakdown (layers aren't assigned yet).  Ordering by
    this is the wirelength ordering the pool already ships in."""
    __slots__ = ("cand_index", "total", "seg_cost", "wl_term", "feasible", "segs")

    def __init__(self, cand_index, wl):
        self.cand_index = cand_index
        self.total      = wl
        self.seg_cost   = 0.0
        self.wl_term    = wl
        self.feasible   = True
        self.segs       = []


class ReportsMixin:

    # ── debug cost inspection (topology explorer `debug` view) ─────────────
    def _candidate_costs(self, w):
        """HYBRID cost source for the explorer's `debug` view.  Returns
        `(mapping, is_real)` where `mapping` is `{candidate_index -> cost}` and
        `cost` is duck-typed to the C++ `CandidateCost` (`total`, `seg_cost`,
        `wl_term`, `feasible`, `segs`).

        POST-`run_planner` (`is_real=True`): the REAL planner cost of every
        candidate against the current committed band state — the true congestion
        other bundles impose — via `CongestionPlanner::candidate_costs`
        (read-only; it recharges around itself and leaves the committed plan
        untouched).  `segs` carries the per-segment breakdown the j/k stepper
        shows (the congestion cost this segment pays).

        PRE-plan (`is_real=False`): no committed state exists, so fall back to
        the intrinsic estimate — each candidate's `estimated_wirelength` with
        congestion 0 (`_IntrinsicCost`).  Ordering by it is the wirelength order
        the pool already ships in.

        Never raises: a planner lookup miss / empty result degrades to the
        intrinsic estimate rather than failing the view."""
        planner = getattr(self, "planner", None)
        if planner is not None:
            try:
                rows = planner.candidate_costs(
                    self.bundles, w.input.original_bundle.id)
            except Exception:                       # noqa: BLE001 — degrade, don't crash
                rows = []
            if rows:
                return {r.cand_index: r for r in rows}, True
        # Pre-plan (or no charged state): intrinsic wirelength cost.
        mapping = {i: _IntrinsicCost(i, c.estimated_wirelength)
                   for i, c in enumerate(w.input.candidates)}
        return mapping, False

    # ── topology inspection (dump_topologies) ──────────────────────────────
    @staticmethod
    def _topo_geom_sig(topo):
        """Geometric signature of a candidate: frozenset of its segment
        coordinate tuples. Two candidates with the same signature draw the
        identical set of wires and are redundant (dedup target)."""
        return frozenset(
            (s.start.x, s.start.y, s.end.x, s.end.y) for s in topo.segments)

    def _topo_min_slide(self, topo, fp=None):
        """Minimum perpendicular slide (perp_hi - perp_lo) across the candidate's
        ConnSegs, via the same ConnTopology API the flexibility tests use. A value
        of 0 means a pinched/zero-freedom candidate. Returns None if connectivity
        can't be built.

        `fp` is the floorplan the candidate was generated against (default
        `self.fp`) — dump_topologies resolves a pre-expansion hier bundle's
        cell-local / depth / endpoint floorplan via `_make_topo_fp_resolver`
        so a cell-level template shows real finite slides before
        `run_planner hier`.  A sentinel-scale return (>= `_SLIDE_SENTINEL`)
        means every segment's slide is still *unbounded* — the candidate
        references block faces ConnTopology can't resolve against the given
        floorplan.  The caller displays that as `free`, mirroring the `--conn`
        detail."""
        try:
            ct = buda.ConnTopology()
            ct.build(topo, fp if fp is not None else self.fp)
            # Include ZERO-length slides (audit P4-06): the C++ filter_pinched
            # flags a candidate when ANY segment has perp_lo == perp_hi, so
            # dropping zero-slide segs here made a partially-pinched candidate
            # report a nonzero min-slide and miss the PINCH flag. Only the
            # inverted (1,0) unbounded sentinel is excluded.
            slides = [cs.perp_hi - cs.perp_lo for cs in ct.segs()
                      if cs.perp_hi >= cs.perp_lo]
            return min(slides) if slides else 0
        except Exception:
            return None

    _SLIDE_SENTINEL = 1e8   # ConnTopology marks an unbounded slide with ~5e8

    def _seg_crosses_rect(self, cs, x1, y1, x2, y2):
        """True iff ConnSeg `cs` crosses the rect's INTERIOR: perp coordinate
        strictly inside the rect's perp extent, along overlap of positive
        length.  A wire that merely rides a face line, or abuts the rect at a
        single point (a trunk whose endpoint lands on the block face flanking
        its junction), does not cross it.  Deliberately STRICTER than verify's
        seg_spans_rect, whose inclusive bounds grant COVERAGE for bundle
        blocks (full-edge abutment is load-bearing there — ABUT candidates)."""
        if cs.horiz:   # perp = y (perp_pos), along = x
            return (y1 < cs.perp_pos < y2
                    and cs.along_lo < x2 and cs.along_hi > x1)
        else:          # perp = x, along = y
            return (x1 < cs.perp_pos < x2
                    and cs.along_lo < y2 and cs.along_hi > y1)

    def _seg_spans_block(self, cs, name, ubbox, fp=None):
        """True iff `cs` crosses block `name`'s SOLID geometry.  Multi-rect / TEG
        blocks store their real rectangles in get_block_rects(); a segment through
        a notch/gap between them does NOT cross the block even though it crosses the
        union bbox.  Single-rect blocks have an empty rect list — fall back to the
        union bbox (which is their solid extent)."""
        if fp is None:
            fp = self.fp
        rects = fp.get_block_rects(name)   # [] for single-rect blocks
        if rects:
            return any(self._seg_crosses_rect(cs, x1, y1, x2, y2)
                       for (x1, y1, x2, y2) in rects)
        return self._seg_crosses_rect(cs, ubbox.x1, ubbox.y1, ubbox.x2, ubbox.y2)

    def _dump_conn_detail(self, w, cand_idx, fp=None):
        """Print per-segment connectivity for one candidate of bundle `w`:
        (1) what each seg connects to (busterms + other segs), (2) the busterms
        it passes through without tapping, (3) its perpendicular slide range, and
        (4) its net-pull preference.  Built from ConnTopology — the same view the
        planner and NUTS consume.  `fp` is the floorplan the candidate was
        generated against (default `self.fp`; a pre-expansion hier bundle's
        cell-local / depth / endpoint floorplan when the dump resolves one)."""
        if fp is None:
            fp = self.fp
        cands = list(w.input.candidates)
        if not (0 <= cand_idx < len(cands)):
            print("     (no candidate to detail)")
            return
        topo = cands[cand_idx]
        try:
            ct = buda.ConnTopology()
            ct.build(topo, fp)
            segs = list(ct.segs())
        except Exception as e:
            print(f"     (connectivity unavailable: {e})")
            return

        blocks = fp.get_all_blocks()   # [(name, Rect union-bbox)]
        feedthru = set(topo.feedthru_blocks)
        contract = set(topo.connected_block_names)   # the bundle's block set
        # Effective per-segment layer: when this candidate IS the planned/selected
        # one, the planner may have reassigned layers (or honoured a pinned
        # selection / post_nuts move) — report that, the layer NUTS actually routes
        # on, not the candidate's original generation layer_hint.  seg_layers is
        # indexed by the selected topology's segments, so it only aligns here.
        seg_layers = (list(w.plan.seg_layers)
                      if cand_idx == w.plan.selected_topology_index else [])
        print(f"   conn detail — candidate {cand_idx}: {topo.type}"
              + (f"   feedthru={sorted(feedthru)}" if feedthru else ""))
        for si, cs in enumerate(segs):
            orient = "H" if cs.horiz else "V"
            planned = si < len(seg_layers) and seg_layers[si] >= 0
            layer = seg_layers[si] if planned else cs.layer_id
            lyr_s = f"M{layer}" + ("" if planned else "·hint")
            rng = cs.perp_hi - cs.perp_lo
            if abs(cs.perp_lo) >= self._SLIDE_SENTINEL or abs(cs.perp_hi) >= self._SLIDE_SENTINEL:
                slide = "free"
            else:
                slide = f"[{cs.perp_lo}..{cs.perp_hi}] = {rng}{' PINCHED' if rng == 0 else ''}"
            pull = ("→hi" if cs.net_pull > 0 else "→lo" if cs.net_pull < 0 else "none")
            print(f"     seg{si:<2} {orient} {lyr_s}  "
                  f"along[{cs.along_lo},{cs.along_hi}] perp={cs.perp_pos}  "
                  f"slide={slide}  pull={pull}({cs.net_pull})")

            bts, sgs, tapped = [], [], set()
            for c in cs.conns:
                if c.kind == buda.SegConnKind.BUSTERM:
                    tapped.add(c.block_name)
                    bts.append(f"{c.block_name}@face={c.face_coord}"
                               f"{'(end)' if c.is_endpoint else '(mid)'}")
                else:
                    sgs.append(f"seg{c.seg_idx}@{c.at_pos}"
                               f"{'(end)' if c.is_endpoint else '(mid)'}")
            print(f"        busterms: {', '.join(bts) if bts else '(none)'}")
            print(f"        segs:     {', '.join(sgs) if sgs else '(none)'}")

            # Pass-through: BUNDLE blocks this seg crosses (solid geometry)
            # without tapping — the coverage/feedthru-relevant set, matching
            # the table's `pass` column semantics.  Unrelated floorplan blocks
            # the wire flies over split by the segment's EFFECTIVE layer:
            # on a TOP layer (or over a container envelope, transparent to LOW
            # layers) that is normal over-the-cell routing — `otc-over`,
            # context, not a problem indicator; on a non-TOP layer a LEAF
            # footprint is an implicit keepout, so the crossing is flagged
            # `low-cross` — a problem an expert needs to notice.
            low_layer = (self.layers.has_layer(layer)
                         and not self.layers.is_top(layer))
            passt, otc, lowx = [], [], []
            for name, ubbox in blocks:
                if name in tapped:
                    continue
                if self._seg_spans_block(cs, name, ubbox, fp):
                    if name in contract:
                        passt.append(name + ("[feedthru]" if name in feedthru else ""))
                    elif low_layer and not fp.is_container(name):
                        lowx.append(name)
                    else:
                        otc.append(name)
            print(f"        passthru: {', '.join(passt) if passt else '(none)'}")
            if otc:
                print(f"        otc-over: {', '.join(otc)}")
            if lowx:
                print(f"        low-cross: {', '.join(lowx)}  "
                      f"(M{layer} is non-TOP — leaf footprints are keepouts)")

    def _tug_pairs(self, topo, fp, plan=None):
        """Outward opposite-pull connector pairs on `topo` — a NUTS
        realization-risk signal (wishlist-nuts "Opposite-pull connector
        pairs").  Read-only over the derived ConnSeg data (net_pull + junction
        at_pos), so it never affects selection or placement.  Returns a list of
        (t, lo_rider, hi_rider) segment-index tuples; [] on any build failure.

        `plan` (the wrapper's BundlePlan) supplies the post-dogleg
        `seg_net_pull` override so the report reflects the pulls NUTS actually
        placed with — a dogleg pins values ConnTopology recomputes wrongly on
        the split topology.  The detector applies the same length/sentinel guard
        NUTS uses, so a stale array is ignored."""
        from buda_session.util import find_tug_of_war_pairs
        try:
            ct = buda.ConnTopology()
            ct.build(topo, fp)
            override = getattr(plan, "seg_net_pull", None) if plan is not None else None
            return find_tug_of_war_pairs(list(ct.segs()), net_pull=override)
        except Exception:
            return []

    @staticmethod
    def _locus_coord(typ):
        """Parse the nominal trunk locus out of a candidate type string
        (`TRUNK_H@y10830` → 10830, `TRUNK_V_OOB@x-246` → -246,
        `TRUNK_H+MST@y12000` → 12000).  None when there is no `@<axis><n>`."""
        if "@" not in typ:
            return None
        tail = typ.split("@", 1)[1]
        if not tail or tail[0] not in "xy":
            return None
        j = 2 if len(tail) > 1 and tail[1] == '-' else 1
        k = j
        while k < len(tail) and tail[k].isdigit():
            k += 1
        return int(tail[1:k]) if k > j else None

    def _dump_topologies(self, hint, problems_only, conn_detail=False,
                         grouped=False):
        if not self.bundles:
            print("Warning: no bundles — run the bundler and generate_topologies first.")
            return
        wraps = self.bundles
        if hint:
            wraps = [w for w in wraps
                     if w.input.original_bundle.get_net_names()
                     and w.input.original_bundle.get_net_names()[0].startswith(hint)]
            if not wraps:
                print(f"No bundles whose first net name starts with '{hint}'.")
                return

        # Aggregates across the (possibly filtered) set.
        n_bundles = len(wraps)
        cand_counts = []
        shape_hist = {}
        n_dup_bundles = n_pinch_bundles = n_single_bundles = n_passthru_bundles = 0
        n_tug_bundles = 0
        n_dup_cands = 0
        printed = 0

        # Resolve each hier bundle's generation-time floorplan (cell-local /
        # depth / endpoint) so pre-planner templates show real finite slides
        # and honest WL envelopes instead of the unbounded-sentinel `free` —
        # the same resolution check_design uses.
        topo_fp = self._make_topo_fp_resolver()

        for w in wraps:
            b = w.input.original_bundle
            cands = list(w.input.candidates)
            cand_counts.append(len(cands))
            sel = w.plan.selected_topology_index
            pinned = bool(getattr(w.input, "topology_pinned", False))
            w_fp = topo_fp(w)

            # Per-candidate facts.
            rows = []          # (idx, type, wl, nsegs, passthru, min_slide)
            sigs = {}          # geom signature -> [idx,...]
            for i, c in enumerate(cands):
                ms = self._topo_min_slide(c, w_fp)
                try:
                    lo, hi = self._topology_wl_interval(c, fp=w_fp)
                except Exception:
                    lo, hi = None, None
                rows.append((i, c.type, c.estimated_wirelength,
                             len(c.segments), c.pass_through_count, ms, lo, hi))
                sigs.setdefault(self._topo_geom_sig(c), []).append(i)
                # Histogram on the shape *family* (strip the @coord suffix that
                # makes every Hanan-line trunk a distinct string) so the report
                # shows how many candidates each family contributes.
                fam = c.type.split("@", 1)[0]
                shape_hist[fam] = shape_hist.get(fam, 0) + 1

            dup_groups = [idxs for idxs in sigs.values() if len(idxs) > 1]
            dup_idx = {i for idxs in dup_groups for i in idxs}
            pinch_idx = {i for (i, _, _, _, _, ms, _, _) in rows if ms == 0}
            passthru_idx = {i for (i, _, _, _, pt, _, _, _) in rows if pt > 0}

            # Tug-of-war signal on the display candidate (selected, else cand 0):
            # opposite-pull rider pairs stretching an interior trunk segment.
            disp = sel if (sel is not None and 0 <= sel < len(cands)) else 0
            # Pass the plan so a post-dogleg seg_net_pull override is honored
            # only for the SELECTED candidate (its overrides match that topology);
            # for an unselected display fallback the length guard ignores them.
            tug_plan = w.plan if disp == sel else None
            tug = self._tug_pairs(cands[disp], w_fp, tug_plan) if cands else []

            has_dup = bool(dup_groups)
            has_pinch = bool(pinch_idx)
            is_single = len(cands) <= 1
            has_passthru = bool(passthru_idx)
            has_tug = bool(tug)
            if has_dup:      n_dup_bundles += 1
            if has_pinch:    n_pinch_bundles += 1
            if is_single:    n_single_bundles += 1
            if has_passthru: n_passthru_bundles += 1
            if has_tug:      n_tug_bundles += 1
            n_dup_cands += sum(len(idxs) - 1 for idxs in dup_groups)

            if problems_only and not (has_dup or has_pinch or is_single
                                      or has_passthru or has_tug):
                continue

            printed += 1
            flags = []
            if has_dup:      flags.append(f"DUP({len(dup_idx)})")
            if has_pinch:    flags.append(f"PINCH({len(pinch_idx)})")
            if is_single:    flags.append("SINGLE")
            if has_passthru: flags.append(f"PASSTHRU({len(passthru_idx)})")
            if has_tug:      flags.append(f"TUG({len(tug)})")
            net0 = (b.get_net_names()[0] if b.get_net_names() else "?")
            grp_pinned = bool(getattr(w.input, "pinned_group", []))
            pin_s = (" GROUP-PINNED" if grp_pinned
                     else " PINNED" if pinned else "")
            # --grouped: collapse nominal-locus families to representatives.
            loci_groups = self._loci_groups(w, w_fp) if grouped else None
            cand_s = f"cands={len(cands)}"
            if loci_groups is not None:
                cand_s += f" → {len(loci_groups)} famil{'y' if len(loci_groups)==1 else 'ies'}"
            print(f"\n── bundle {b.id}  nets={len(b.net_names)} ({net0}…)  "
                  f"width={w.input.width}  sel={sel}{pin_s}  "
                  f"{cand_s}  {' '.join(flags)}")
            # Size the type column to the widest type so every later column
            # stays aligned regardless of long names like TRUNK_V_OOB@x6282.
            type_w = max([len("type")] + [len(r[1]) for r in rows])
            # `wl` is the candidate's nominal (as-generated) estimate; `wl[lo..hi]`
            # is the routing envelope its slide/span DOF permit — lo = tightest
            # (joint slide minimum), hi = loose outer bound.  A wide envelope means
            # the candidate has lots of routing freedom for NUTS to exploit.
            print(f"   {'idx':>3} {'type':<{type_w}} {'wl':>8} {'wl[lo..hi]':>17} "
                  f"{'segs':>4} {'pass':>4} {'mslide':>7}  notes")
            # --grouped: only the lowest-WL representative of each nominal-locus
            # family is printed; its notes carry the variant count + perp span.
            reps = grp_of_rep = None
            if loci_groups is not None:
                grp_of_rep = {g[0]: g for g in loci_groups}
                reps = set(grp_of_rep)
            for (i, typ, wl, nsegs, pt, ms, lo, hi) in rows:
                if reps is not None and i not in reps:
                    continue
                marks = []
                if i == sel:      marks.append("*SEL")
                if i in dup_idx:  marks.append("dup")
                if i in pinch_idx: marks.append("pinch")
                if grp_of_rep is not None:
                    g = grp_of_rep[i]
                    if len(g) > 1:
                        cs = [self._locus_coord(rows[j][1]) for j in g]
                        cs = [c for c in cs if c is not None]
                        marks.append(f"family:+{len(g) - 1}@{min(cs)}..{max(cs)}"
                                     if cs else f"family:+{len(g) - 1}")
                    # The directly-pinnable token for this family, emitted
                    # VERBATIM as `select_topology <bundle> group:<N>` accepts
                    # it — copy it straight after the bundle hint, no editing.
                    # The rep's 1-based candidate id (the `idx` column is
                    # 0-based, and the ordinal position among families is NOT
                    # the pin id — see docs/script_reference/planner.md).
                    marks.append(f"group:{i + 1}")
                ms_s = ("-" if ms is None
                        else "free" if ms >= self._SLIDE_SENTINEL
                        else str(ms))
                env = f"[{lo:.0f}..{hi:.0f}]" if lo is not None else "-"
                print(f"   {i:>3} {typ:<{type_w}} {wl:>8} {env:>17} {nsegs:>4} "
                      f"{pt:>4} {ms_s:>7}  {','.join(marks)}")

            # Tug-of-war detail: which interior segment each opposing rider
            # pair stretches (cand `disp` = the selected/displayed candidate).
            for (t, lo, hi) in tug:
                print(f"   tug: cand {disp} seg{t} stretched by "
                      f"seg{lo}(-)/seg{hi}(+)  [realization risk]")

            # --conn: per-segment connectivity / pass-through / slide / pull for
            # the selected candidate (or candidate 0 if not yet planned).
            if conn_detail:
                self._dump_conn_detail(w, sel if sel is not None and sel >= 0 else 0,
                                       w_fp)

        # Aggregate summary.
        import statistics as _st
        tot_cands = sum(cand_counts)
        avg = (tot_cands / n_bundles) if n_bundles else 0
        med = _st.median(cand_counts) if cand_counts else 0
        print(f"\n══ summary ({n_bundles} bundles"
              f"{f', {printed} shown' if problems_only else ''}) ══")
        print(f"   candidates: total={tot_cands} avg={avg:.1f} median={med} "
              f"min={min(cand_counts) if cand_counts else 0} "
              f"max={max(cand_counts) if cand_counts else 0}")
        print(f"   bundles with duplicates : {n_dup_bundles}/{n_bundles} "
              f"({n_dup_cands} redundant candidates)")
        print(f"   bundles with pinched cand: {n_pinch_bundles}/{n_bundles}")
        print(f"   single-candidate bundles : {n_single_bundles}/{n_bundles}")
        print(f"   bundles with pass-through: {n_passthru_bundles}/{n_bundles}")
        print(f"   bundles with tug-of-war  : {n_tug_bundles}/{n_bundles}")
        top_shapes = sorted(shape_hist.items(), key=lambda kv: -kv[1])
        print("   shape histogram: "
              + ", ".join(f"{t}={n}" for t, n in top_shapes))

    def _net_driver_fidelity(self, w, topo):
        """Net-driver fidelity check: every net endpoint block of the bundle
        must appear in the topology's required-block contract
        (`connected_block_names` — what check_topo verifies coverage FOR).
        This is the check whose absence let the CONVERGENT single-driver gap
        slip through: check_topo validates a topology's INTERNAL consistency
        against its own block list, so a topology generated from one driver
        passed while three drivers were silently unrouted
        (docs/internal/convergent_bundling.md).

        Flat-flow only: hier bundles' endpoint instances live in a different
        name space than their generation floorplans' blocks (leaf paths vs
        depth/cell-local blocks), so the comparison is meaningless there —
        gated on the session's hier markers plus a per-block has_block guard
        (an endpoint not present as a block in the session floorplan is a
        container/hierarchy artifact, not a dropped driver).  An empty
        connected_block_names (a hand-built USER candidate) is skipped."""
        if (not self._net_endpoints
                or getattr(self, "_hier_bundles_orig", None)
                or self._hier_expansion_map
                or not topo.connected_block_names):
            return []
        nets = w.input.original_bundle.get_net_names()
        by_block = {}
        for net in nets:
            ep = self._net_endpoints.get(net)
            if ep is None:
                continue
            for blk in (ep[0], *ep[1]):
                by_block.setdefault(blk, []).append(net)
        connected = set(topo.connected_block_names)
        out = []
        missing = set()
        for blk in sorted(set(by_block) - connected):
            if not self.fp.has_block(blk):
                continue                     # hierarchy artifact, not a block
            missing.add(blk)
            out.append(_fidelity_union(blk, sorted(by_block[blk])))
        # Per-BIT fidelity for fan-in bundles (tapered model): a bit whose
        # driver→sink segment path could not be established fell back to
        # all-segments — the taper derivation IS the per-bit check, so
        # re-running it (idempotent: it recomputes the derived seg_bits
        # cache) yields exactly the failed bits.  Skip drivers the union
        # check above already reported.
        eps = self._fanin_net_endpoints(w)
        if eps is not None:
            drvs, rcvs = eps
            for b in buda.derive_fanin_seg_bits(topo, self.fp, drvs, rcvs):
                blk = drvs[b] if b < len(drvs) else ""
                if not blk or blk in missing or not self.fp.has_block(blk):
                    continue
                net = nets[b] if b < len(nets) else f"bit {b}"
                out.append(_FidelityViolation(blk, (
                    f"NET_DRIVER_OPEN: net '{net}' (bit {b}) has no "
                    f"driver→sink segment path from block '{blk}' in the "
                    f"fan-in topology — its wires fall back to the whole "
                    f"tree (untapered)")))
        return out

    def _check_design(self, stage: str, all_candidates: bool = False):
        if stage in ("nuts", "dnuts") and self.nuts_result is None:
            print("  Error: run_nuts required first.")
            return
        if stage == "dnuts" and self.detailed_result is None:
            print("  Error: run_detailed_nuts required first.")
            return

        # For the topo stage, auto-switch to all-candidates mode when no
        # topology has been selected yet (before run_planner).
        if stage == "topo" and not all_candidates:
            no_selection = all(
                not w.input.candidates or w.plan.selected_topology_index < 0
                for w in self.bundles
            )
            if no_selection:
                all_candidates = True

        labels = {"topo": "topology", "nuts": "NUTS", "dnuts": "Detailed NUTS"}
        suffix = " (all candidates)" if (all_candidates and stage == "topo") else ""
        print(f"[Check] Verifying {labels[stage]}-level design{suffix}...")

        if self._hier_expansion_map:
            fp_block_names = {name for name, _ in self.fp.get_all_blocks()}
            missing = set()
            for w in self.bundles:
                if w.input.candidates and w.plan.selected_topology_index >= 0:
                    topo = w.input.candidates[w.plan.selected_topology_index]
                    for bname in topo.connected_block_names:
                        if bname not in fp_block_names:
                            missing.add(bname)
            if missing:
                shown = sorted(missing)[:5]
                ellipsis_str = "..." if len(missing) > 5 else ""
                print(f"  Warning: {len(missing)} block(s) referenced in topologies "
                      f"but not in floorplan: {', '.join(shown)}{ellipsis_str}")
                print(f"  Hint: call 'add_blocks_from_bdb N skip' for all required depths.")

        # Hier bundles' candidates may live in a cell-local / depth / custom
        # floorplan rather than self.fp; resolve the right one per bundle so the
        # check uses the same coordinate and block-name space the candidates
        # were generated in.  Per-instance wrappers from _expand_hier_bundles
        # (absolute coords, dropped seg_busterms) are excluded and use self.fp.
        hier_fp_cache = {}
        comps_by_name = ({c.name: c for c in self.bdb.all_components()}
                         if self.bdb is not None else {})
        expanded_ids = {id(w)
                        for ws in (self._hier_expansion_map or {}).values()
                        for w in ws}

        total = 0
        collected = []   # (prefix, violation) — aggregated below unless --verbose-conn
        tug_bundles = 0  # realization-risk advisory (NOT violations); nuts/dnuts only
        tug_pairs = 0
        for w in self.bundles:
            if not w.input.candidates:
                continue
            bid = w.input.original_bundle.id

            b = w.input.original_bundle
            check_fp = self.fp
            if (self.bdb is not None and isinstance(b, buda.HBundle)
                    and id(w) not in expanded_ids):
                resolved = self._floorplan_for_hbundle(b, hier_fp_cache, comps_by_name)
                if resolved is not None:
                    check_fp = resolved

            if all_candidates and stage == "topo":
                to_check = list(enumerate(w.input.candidates))
            elif w.plan.selected_topology_index >= 0:
                idx = w.plan.selected_topology_index
                to_check = [(idx, w.input.candidates[idx])]
            else:
                continue

            for topo_idx, topo in to_check:
                ct = buda.ConnTopology()
                ct.build(topo, check_fp)

                if stage == "topo":
                    res = buda.check_topo(ct, topo, check_fp, bid)
                elif stage == "nuts":
                    # zone_fp = self.fp: NUTS placed against the SESSION
                    # floorplan's keepout zones; a hier bundle's resolved
                    # check_fp (right space for the busterm-face checks) has
                    # no zones, and testing KEEPOUT_CROSS against it would
                    # bless conflicts the engine itself counted.
                    res = buda.check_nuts(ct, self.nuts_result, topo, check_fp,
                                          self.layers, bid, zone_fp=self.fp)
                else:
                    num_bits = len(w.input.original_bundle.get_net_names())
                    res = buda.check_dnuts(ct, self.detailed_result, topo, check_fp,
                                           self.layers, bid, num_bits,
                                           zone_fp=self.fp)

                violations = list(res.violations)
                violations += self._net_driver_fidelity(w, topo)
                for v in violations:
                    if all_candidates and stage == "topo":
                        prefix = f"Bundle {bid} topo {topo_idx + 1} ({topo.type})"
                    else:
                        prefix = f"Bundle {bid}"
                    collected.append((prefix, v))
                    total += 1

                # Realization-risk ADVISORY (not a violation): opposite-pull
                # rider pairs stretching an interior trunk (wishlist-nuts
                # "tug-of-war").  Only meaningful on the SELECTED, placed
                # candidate — read the plan's post-dogleg seg_net_pull override
                # so the count matches what NUTS actually placed with.
                if stage in ("nuts", "dnuts") and not (all_candidates
                                                       and stage == "topo"):
                    tp = self._tug_pairs(topo, check_fp, w.plan)
                    if tp:
                        tug_bundles += 1
                        tug_pairs += len(tp)

        if total == 0:
            print("  Success: no violations found.")
        elif self.verbose_conn:
            for prefix, v in collected:
                print(f"  {prefix}: {v.message}")
        else:
            self._report_violations_summary(collected)

        if tug_pairs:
            print(f"  Advisory: {tug_pairs} tug-of-war realization-risk pair(s) "
                  f"on {tug_bundles} bundle(s) — opposite-pull riders stretch an "
                  f"interior trunk; see 'dump_topologies --problems'.")

        # LAYER_CAP / LAYER_SHARE advisory (hier_layer_caps.md Phase 4,
        # defense-in-depth): in-effect layers vs each governed bundle's band
        # + the collective share leases.  Unpinned out-of-band metal should
        # be IMPOSSIBLE (mask enforced in the planner core and every healer
        # path) — a nonzero count is LOUD; pinned exceptions are the
        # documented override, surfaced so they are visible, never silent.
        # No policy in the session => completely silent (byte-identical).
        if not (all_candidates and stage == "topo"):
            cap_bad, cap_pinned, share_over = \
                self._layer_policy_advisories(stage)
            for msg in cap_bad:
                print(f"  LAYER_CAP: {msg}")
            if cap_bad:
                print(f"  WARNING: {len(cap_bad)} segment(s) hold metal "
                      f"outside their cell band with NO pin — the mask "
                      f"should make this impossible; please report.")
            if cap_pinned:
                print(f"  Advisory: {len(cap_pinned)} pinned above-cap "
                      f"exception(s) honored (pins override the mask):")
                for msg in cap_pinned:
                    print(f"    {msg}")
            if share_over:
                print(f"  Advisory: {share_over} share group(s) over their "
                      f"collective lease (LAYER_SHARE) — a non-STRICT "
                      f"commit spent past the budget; see the run_planner "
                      f"hier audit.")

        # Supply-doomed seat census (#536 option 1, report-only): placed
        # segments whose layer's real signal-track supply cannot host their
        # member bits — static width-infeasibility, the class behind
        # "unexplained" DNUTS opens on TOP layers.  NUTS-placed geometry, so
        # nuts/dnuts stages only.
        if stage in ("nuts", "dnuts"):
            self._report_doomed_seats()

    # Reason text per ViolationKind, used when collapsing per-bit violations.
    _CONN_KIND_REASON = {
        "UNPLACED":     "unplaced (no track in DetailedNUTS)",
        "BUSTERM_OPEN": "no pass-through/busterm connection",
        "BUSTERM_FACE": "invalid busterm face",
        "SEG_OPEN":     "segment disconnected",
        "LAYER_DIR":    "wrong layer direction",
        "FEEDTHRU_RELAY": "block used as feedthrough relay (segments not wire-joined)",
        "KEEPOUT_CROSS": "wire placed on a keepout",
        "NET_DRIVER_OPEN": "net endpoint block not attached to the topology",
        "BIT_SHORT":    "different bits (nets) share a track with overlapping spans",
    }

    _CONN_GROUP_CAP = 100   # max summary lines before eliding the rest

    def _report_violations_summary(self, collected):
        """Collapse the per-bit connectivity violations into one line per
        (bundle, topo, kind, locus) group.  On a large design this turns tens
        of thousands of 'Seg N Bit M ...' lines into a few hundred.  Pass
        --verbose-conn to restore the full per-bit dump."""
        from collections import OrderedDict
        groups = OrderedDict()
        for prefix, v in collected:
            key = (prefix, v.kind.name, v.seg_idx, v.seg_idx2, v.block_name)
            g = groups.get(key)
            if g is None:
                g = {"prefix": prefix, "kind": v.kind.name, "seg_idx": v.seg_idx,
                     "seg_idx2": v.seg_idx2, "block": v.block_name,
                     "bits": set(), "msg": v.message}
                groups[key] = g
            if v.bit_index >= 0:
                g["bits"].add(v.bit_index)

        def locus(g):
            if g["block"]:
                return f"Block '{g['block']}'"
            if g["seg_idx"] >= 0 and g["seg_idx2"] >= 0:
                return f"Seg {g['seg_idx']}<->{g['seg_idx2']}"
            if g["seg_idx"] >= 0:
                return f"Seg {g['seg_idx']}"
            return ""

        bundles = set()
        for i, g in enumerate(groups.values()):
            bundles.add(g["prefix"])
            if i >= self._CONN_GROUP_CAP:
                continue
            nbits = len(g["bits"])
            if nbits == 0:
                # Not a per-bit violation (topo/nuts stage) — show it verbatim.
                print(f"  {g['prefix']}: {g['msg']}")
            else:
                loc = locus(g)
                loc_part = f"{loc}: " if loc else ""
                reason = self._CONN_KIND_REASON.get(g["kind"], g["kind"])
                print(f"  {g['prefix']}: {loc_part}{nbits} bit(s) — {reason}")

        n_groups = len(groups)
        if n_groups > self._CONN_GROUP_CAP:
            print(f"  ... and {n_groups - self._CONN_GROUP_CAP} more group(s) "
                  f"(use --verbose-conn for full detail).")
        total = sum(max(1, len(g["bits"])) for g in groups.values())
        print(f"  Total: {total} violation(s) in {n_groups} group(s) across "
              f"{len(bundles)} bundle(s). Use --verbose-conn for per-bit detail.")
