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
helpers they share, check_connectivity at all three stages, and the
violation summary collapsing (with its class-level constants).

Methods extracted verbatim from buda_cli.BudaSession (the CLI mixin
split); bodies unchanged — `self` is the composed BudaSession, so
cross-mixin helper calls resolve through the class as before.
"""
import buda


class ReportsMixin:

    # ── topology inspection (dump_topologies) ──────────────────────────────
    @staticmethod
    def _topo_geom_sig(topo):
        """Geometric signature of a candidate: frozenset of its segment
        coordinate tuples. Two candidates with the same signature draw the
        identical set of wires and are redundant (dedup target)."""
        return frozenset(
            (s.start.x, s.start.y, s.end.x, s.end.y) for s in topo.segments)

    def _topo_min_slide(self, topo):
        """Minimum perpendicular slide (perp_hi - perp_lo) across the candidate's
        ConnSegs, via the same ConnTopology API the flexibility tests use. A value
        of 0 means a pinched/zero-freedom candidate. Returns None if connectivity
        can't be built (e.g. a hier candidate in a non-self.fp floorplan)."""
        try:
            ct = buda.ConnTopology()
            ct.build(topo, self.fp)
            slides = [cs.perp_hi - cs.perp_lo for cs in ct.segs()
                      if cs.perp_hi > cs.perp_lo]
            return min(slides) if slides else 0
        except Exception:
            return None

    _SLIDE_SENTINEL = 1e8   # ConnTopology marks an unbounded slide with ~5e8

    def _seg_crosses_rect(self, cs, x1, y1, x2, y2):
        """True iff ConnSeg `cs` geometrically crosses the rect (perp coordinate
        inside the rect's perp extent and the along span overlapping the rect's
        along extent).  Mirrors verify's seg_spans_rect."""
        if cs.horiz:   # perp = y (perp_pos), along = x
            return (y1 <= cs.perp_pos <= y2
                    and cs.along_lo <= x2 and cs.along_hi >= x1)
        else:          # perp = x, along = y
            return (x1 <= cs.perp_pos <= x2
                    and cs.along_lo <= y2 and cs.along_hi >= y1)

    def _seg_spans_block(self, cs, name, ubbox):
        """True iff `cs` crosses block `name`'s SOLID geometry.  Multi-rect / TEG
        blocks store their real rectangles in get_block_rects(); a segment through
        a notch/gap between them does NOT cross the block even though it crosses the
        union bbox.  Single-rect blocks have an empty rect list — fall back to the
        union bbox (which is their solid extent)."""
        rects = self.fp.get_block_rects(name)   # [] for single-rect blocks
        if rects:
            return any(self._seg_crosses_rect(cs, x1, y1, x2, y2)
                       for (x1, y1, x2, y2) in rects)
        return self._seg_crosses_rect(cs, ubbox.x1, ubbox.y1, ubbox.x2, ubbox.y2)

    def _dump_conn_detail(self, w, cand_idx):
        """Print per-segment connectivity for one candidate of bundle `w`:
        (1) what each seg connects to (busterms + other segs), (2) the busterms
        it passes through without tapping, (3) its perpendicular slide range, and
        (4) its net-pull preference.  Built from ConnTopology — the same view the
        planner and NUTS consume."""
        cands = list(w.input.candidates)
        if not (0 <= cand_idx < len(cands)):
            print("     (no candidate to detail)")
            return
        topo = cands[cand_idx]
        try:
            ct = buda.ConnTopology()
            ct.build(topo, self.fp)
            segs = list(ct.segs())
        except Exception as e:
            print(f"     (connectivity unavailable: {e})")
            return

        blocks = self.fp.get_all_blocks()   # [(name, Rect union-bbox)]
        feedthru = set(topo.feedthru_blocks)
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

            # Pass-through: blocks this seg crosses (solid geometry) but does NOT tap.
            passt = []
            for name, ubbox in blocks:
                if name in tapped:
                    continue
                if self._seg_spans_block(cs, name, ubbox):
                    passt.append(name + ("[feedthru]" if name in feedthru else ""))
            print(f"        passthru: {', '.join(passt) if passt else '(none)'}")

    def _dump_topologies(self, hint, problems_only, conn_detail=False):
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
        n_dup_cands = 0
        printed = 0

        for w in wraps:
            b = w.input.original_bundle
            cands = list(w.input.candidates)
            cand_counts.append(len(cands))
            sel = w.plan.selected_topology_index
            pinned = bool(getattr(w.input, "topology_pinned", False))

            # Per-candidate facts.
            rows = []          # (idx, type, wl, nsegs, passthru, min_slide)
            sigs = {}          # geom signature -> [idx,...]
            for i, c in enumerate(cands):
                ms = self._topo_min_slide(c)
                try:
                    lo, hi = self._topology_wl_interval(c)
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

            has_dup = bool(dup_groups)
            has_pinch = bool(pinch_idx)
            is_single = len(cands) <= 1
            has_passthru = bool(passthru_idx)
            if has_dup:      n_dup_bundles += 1
            if has_pinch:    n_pinch_bundles += 1
            if is_single:    n_single_bundles += 1
            if has_passthru: n_passthru_bundles += 1
            n_dup_cands += sum(len(idxs) - 1 for idxs in dup_groups)

            if problems_only and not (has_dup or has_pinch or is_single or has_passthru):
                continue

            printed += 1
            flags = []
            if has_dup:      flags.append(f"DUP({len(dup_idx)})")
            if has_pinch:    flags.append(f"PINCH({len(pinch_idx)})")
            if is_single:    flags.append("SINGLE")
            if has_passthru: flags.append(f"PASSTHRU({len(passthru_idx)})")
            net0 = (b.get_net_names()[0] if b.get_net_names() else "?")
            pin_s = " PINNED" if pinned else ""
            print(f"\n── bundle {b.id}  nets={len(b.net_names)} ({net0}…)  "
                  f"width={w.input.width}  sel={sel}{pin_s}  "
                  f"cands={len(cands)}  {' '.join(flags)}")
            # Size the type column to the widest type so every later column
            # stays aligned regardless of long names like TRUNK_V_OOB@x6282.
            type_w = max([len("type")] + [len(r[1]) for r in rows])
            # `wl` is the candidate's nominal (as-generated) estimate; `wl[lo..hi]`
            # is the routing envelope its slide/span DOF permit — lo = tightest
            # (joint slide minimum), hi = loose outer bound.  A wide envelope means
            # the candidate has lots of routing freedom for NUTS to exploit.
            print(f"   {'idx':>3} {'type':<{type_w}} {'wl':>8} {'wl[lo..hi]':>17} "
                  f"{'segs':>4} {'pass':>4} {'mslide':>7}  notes")
            for (i, typ, wl, nsegs, pt, ms, lo, hi) in rows:
                marks = []
                if i == sel:      marks.append("*SEL")
                if i in dup_idx:  marks.append("dup")
                if i in pinch_idx: marks.append("pinch")
                ms_s = "-" if ms is None else str(ms)
                env = f"[{lo:.0f}..{hi:.0f}]" if lo is not None else "-"
                print(f"   {i:>3} {typ:<{type_w}} {wl:>8} {env:>17} {nsegs:>4} "
                      f"{pt:>4} {ms_s:>7}  {','.join(marks)}")

            # --conn: per-segment connectivity / pass-through / slide / pull for
            # the selected candidate (or candidate 0 if not yet planned).
            if conn_detail:
                self._dump_conn_detail(w, sel if sel is not None and sel >= 0 else 0)

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
        top_shapes = sorted(shape_hist.items(), key=lambda kv: -kv[1])
        print("   shape histogram: "
              + ", ".join(f"{t}={n}" for t, n in top_shapes))

    def _check_connectivity(self, stage: str, all_candidates: bool = False):
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
        print(f"[Check] Verifying {labels[stage]}-level connectivity{suffix}...")

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
                    res = buda.check_nuts(ct, self.nuts_result, topo, check_fp, self.layers, bid)
                else:
                    num_bits = len(w.input.original_bundle.get_net_names())
                    res = buda.check_dnuts(ct, self.detailed_result, topo, check_fp,
                                           self.layers, bid, num_bits)

                for v in res.violations:
                    if all_candidates and stage == "topo":
                        prefix = f"Bundle {bid} topo {topo_idx + 1} ({topo.type})"
                    else:
                        prefix = f"Bundle {bid}"
                    collected.append((prefix, v))
                    total += 1

        if total == 0:
            print("  Success: no opens found.")
        elif self.verbose_conn:
            for prefix, v in collected:
                print(f"  {prefix}: {v.message}")
        else:
            self._report_violations_summary(collected)

    # Reason text per ViolationKind, used when collapsing per-bit violations.
    _CONN_KIND_REASON = {
        "UNPLACED":     "unplaced (no track in DetailedNUTS)",
        "BUSTERM_OPEN": "no pass-through/busterm connection",
        "BUSTERM_FACE": "invalid busterm face",
        "SEG_OPEN":     "segment disconnected",
        "LAYER_DIR":    "wrong layer direction",
        "FEEDTHRU_RELAY": "block used as feedthrough relay (segments not wire-joined)",
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
