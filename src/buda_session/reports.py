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
import math

import buda

import buda_diag
from comp_placement import is_placed
from .util import (UNIT_PITCH_UM_MAX, UNIT_PITCH_UM_MIN, UNIT_TRACKS_MAX,
                   UNIT_TRACKS_MIN, seg_crosses_rect, seg_spans_block,
                   unit_consistency_signals, unit_plausibility_faults, fmt_pos)


def _fmt_pull_opt(cs):
    """The anchor-interval pull optimum for the pull display (#523/#539): the
    flat-optimum interval `[pull_lo, pull_hi]` the segment's connected
    wirelength is minimized over (every position inside is WL-identical, so
    NUTS may spend it on packing), plus the same-side ANCHOR COUNTS just
    outside it (`pull_f_lo`/`pull_f_hi` — how many coupled anchors pull back
    below `pull_lo` / above `pull_hi`).  Those counts are GROSS, an upper
    bound on the true marginal WL slope, NOT the slope (the exact netting was
    measured worse — interval_pull_model.md "exact net slopes"); they are the
    phase-1 strongest-pull-first ORDERING signal.  `net_pull` shown beside it
    stays the DERIVED scalar (only its SIGN carries model meaning).  Returns
    '' when the optimum is flat everywhere (INT_MIN..INT_MAX sentinel — no
    anchor constrains the slide, so there is nothing to show)."""
    lo, hi = cs.pull_lo, cs.pull_hi
    _SENT = 1_000_000_000            # well above any real coord, below INT_MAX
    lo_inf, hi_inf = lo <= -_SENT, hi >= _SENT
    if lo_inf and hi_inf:
        return ""
    los = "-inf" if lo_inf else str(lo)
    his = "+inf" if hi_inf else str(hi)
    return f"  opt=[{los}..{his}] anchors={cs.pull_f_lo}/{cs.pull_f_hi}"


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


class _UnroutedViolation:
    """A bundle the planner left with NO selected topology — same duck-typed
    shape as _FidelityViolation.

    The audit walked bundles by their SELECTED candidate and skipped any
    without one, so a bus the planner could not commit contributed nothing
    and the run printed "Success: no violations found."  A bus with no wire
    anywhere is the one thing an audit must never call clean; it is the
    `flow/rv` item-10 failure reached through the planner instead of through
    generation."""
    class _Kind:
        name = "UNROUTED_BUNDLE"
    kind = _Kind()
    seg_idx = -1
    seg_idx2 = -1
    bit_index = -1
    block_name = ""

    def __init__(self, message):
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

    # ── unit-plausibility guard (Phase 1d) ─────────────────────────────────
    def _check_unit_plausibility(self, stage):
        """Stop a run whose block coordinates and track patterns are on
        different scales.

        The engine is unit-agnostic, so such a mix does not crash — it
        produces a plan in which every bus reserves a wrong fraction of the
        space it needs, and reports it as feasible.  `tracks across the
        design` (extent / track pitch) is a ratio of two layout-unit lengths:
        invariant under any CONSISTENT unit, and off by the scale ratio under
        an inconsistent one.  See `unit_plausibility_faults` for the bounds
        and how they were fixed.

        Runs once per session, at the first stage that has both a floorplan
        and a routing grid — `run_planner` for most flows, `run_nuts` for the
        ones that declare their patterns after planning (demo/comprehensive_
        demo.buda does).  A no-op with no grid: there is no second scale to
        disagree with."""
        import os
        mode = os.environ.get("BUDA_UNIT_CHECK", "").lower() or self._unit_check
        if mode not in ("on", "warn", "off"):
            mode = self._unit_check
        measuring = bool(os.environ.get("BUDA_UNIT_SIGNAL"))
        if mode == "off" and not measuring:
            return
        if self.routing_grid is None or self.fp is None:
            return
        signals, extent = unit_consistency_signals(self.fp, self.routing_grid)
        if not signals:
            return
        # "Once per session" really means "once per unchanged grid".  A plain
        # done-flag set at run_planner would permanently retire the run_nuts
        # hook, so a design that declares M4 before planning and a
        # wrong-scale M5 after would be judged on M4 alone and pass — and
        # declaring patterns late is exactly the flow shape the second hook
        # exists for (Codex P2 on #645).  Keyed on the patterns themselves, so
        # a new grid command re-arms the check without any command handler
        # having to remember to reset a flag.
        sig_key = tuple((lid, up) for lid, up, _n in signals)
        if self._unit_check_done == sig_key:
            return
        self._unit_check_done = sig_key
        if measuring:
            # The raw measurement, for re-calibrating the bounds against a
            # corpus.  The bounds are only as good as the range they were set
            # outside of, so the way to widen that range must stay in-tree —
            # and it has to work with the check itself OFF, since a
            # calibration sweep must not be stopped by the bounds it is
            # measuring.
            for lid, up, n in signals:
                print(f"[UnitSignal] extent={extent:g} layer {lid} "
                      f"unit_pitch={up:g} tracks_across={n:.6g}")
        if mode == "off":
            return
        lu = 1.0
        if self.bdb is not None:
            try:
                lu = float(self.bdb.import_scale())
            except Exception:
                lu = 1.0
        faults = unit_plausibility_faults(signals, lu)
        if not faults:
            return
        lo, hi = UNIT_TRACKS_MIN, UNIT_TRACKS_MAX
        # The FIRST line carries the id (BUDA-1901): the continuation lines
        # are the evidence for it, and giving each one an id would make a
        # single fault look like five.  `set_unit_check warn` is a severity
        # DOWNGRADE of that same fault — "I have seen it and I accept it on
        # this design" — so the id is unchanged and only the severity moves,
        # which is what keeps a methodology's gate on BUDA-1901 meaningful.
        head = (f"[UnitCheck] {stage}: implausible design scale — the block "
                f"coordinates and the track patterns look like they are in "
                f"different units.")
        lines = [buda_diag.format("BUDA-1901", head,
                                  buda_diag.WARNING if mode == "warn" else None),
                 f"[UnitCheck]   design extent = {extent:g} layout units; "
                 f"plausible range is {lo:g}..{hi:g} tracks across."]
        if any(f[3].startswith("pitch") for f in faults):
            lines.append(
                f"[UnitCheck]   import scale = {lu:g} layout units per micron, "
                f"so a track pitch must land in "
                f"{UNIT_PITCH_UM_MIN * lu:g}..{UNIT_PITCH_UM_MAX * lu:g} "
                f"layout units to be a real metal pitch.")
        _WHY = {"low": "too few tracks across",
                "high": "too many tracks across",
                "pitch_lo": "far too fine to be a real metal pitch",
                "pitch_hi": "far too coarse to be a real metal pitch"}
        for lid, up, n, side in faults:
            detail = (f"{up / lu:g} µm" if side.startswith("pitch")
                      else f"{n:.3g} tracks across")
            lines.append(f"[UnitCheck]   layer {lid}: track pitch {up:g} → "
                         f"{detail} ({_WHY[side]})")
        lines.append("[UnitCheck]   Fix the scale, or `set_unit_check warn`"
                     " / `off` if this design really is like this.")
        text = "\n".join(lines)
        if mode == "warn":
            print(text)
            return
        # The whole diagnostic goes in the EXCEPTION, not just in the log: a
        # command that raises has its captured output written to the flow log
        # and NOT echoed to the terminal, so a stop whose reason lives only in
        # a file is a stop the user cannot act on.
        print(text)                      # for the flow-log record
        raise ValueError("\n" + text)

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
        """Delegates to the shared predicate (buda_session.util) — the web
        client's pass-through markers read the same geometry, so the two
        cannot drift on what "crosses a block" means."""
        return seg_crosses_rect(cs, x1, y1, x2, y2)

    def _seg_spans_block(self, cs, name, ubbox, fp=None):
        """Delegates to the shared predicate (buda_session.util)."""
        return seg_spans_block(cs, name, ubbox, fp if fp is not None else self.fp)

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
        # A dogleg pin overrides a segment's pull/slide only on the SELECTED
        # topology (the override arrays are indexed by its segments); there
        # the ConnSeg's recomputed optimum is not what NUTS placed with, so
        # suppress `opt=` — mirroring build_nuts_maps' interval suppression
        # (nuts.cpp) and the explorer's `_pull_overridden` (Codex P2 on #555).
        is_sel = cand_idx == w.plan.selected_topology_index
        _snp = list(getattr(w.plan, 'seg_net_pull', []) or [])
        _sslo = list(getattr(w.plan, 'seg_slide_lo', []) or [])
        nseg = len(topo.segments)

        def _pull_overridden(i):
            if not is_sel:
                return False
            if len(_snp) == nseg and _snp[i] != -2147483648:
                return True
            # seg_slide_lo[i] is NaN when unpinned; NaN != NaN detects a pin
            # without importing math.
            return len(_sslo) == nseg and _sslo[i] == _sslo[i]

        print(f"   conn detail — topo {cand_idx + 1}: {topo.type}"
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
            opt = "" if _pull_overridden(si) else _fmt_pull_opt(cs)
            print(f"     seg{si:<2} {orient} {lyr_s}  "
                  f"along[{cs.along_lo},{cs.along_hi}] perp={cs.perp_pos}  "
                  f"slide={slide}  pull={pull}({cs.net_pull}){opt}")

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
            # The SAME selector `select_topology` takes, so a bare integer is a
            # bundle ID here too.  It used to be a net-name prefix ONLY, which
            # made the number this very report prints — and the one
            # `check_design` prints in "Bundle 8: ..." — the one thing you could
            # not look up: `dump_topologies 8` answered "No bundles whose first
            # net name starts with '8'" while `select_topology 8` pinned bundle
            # 8.  One token, two meanings, in adjacent commands.  `net:8` still
            # forces the prefix reading for a bus whose name starts with a
            # digit.
            bids, err = self._resolve_bundle_selector(hint)
            if err:
                print(f"No bundles matched '{hint}': {err}.")
                return
            want = set(bids)
            wraps = [w for w in wraps if w.input.original_bundle.id in want]
            if not wraps:
                known = sorted(w.input.original_bundle.id for w in self.bundles)
                rng = (f"{known[0]}..{known[-1]}" if known else "none")
                print(f"No bundle matched '{hint}' "
                      f"(bundle ids present: {rng}).")
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
            # Every candidate number this command prints is 1-BASED, which
            # is what `select_topology` / `edit_topology` take and what the
            # explorer's `topo N/n` title shows.  It used to print the raw
            # 0-based index here and in the `idx` column while emitting a
            # 1-based `group:` token on the SAME ROW — one line, two
            # numbering systems, and the off-by-one silently pinned the
            # neighbouring candidate.  `-` means nothing is selected: `0`
            # would read as a valid id under 1-based numbering.
            sel_s = f"{sel + 1}" if (sel is not None and sel >= 0) else "-"
            print(f"\n── bundle {b.id}  nets={len(b.net_names)} ({net0}…)  "
                  f"width={w.input.width}  sel={sel_s}{pin_s}  "
                  f"{cand_s}  {' '.join(flags)}")
            # Size the type column to the widest type so every later column
            # stays aligned regardless of long names like TRUNK_V_OOB@x6282.
            type_w = max([len("type")] + [len(r[1]) for r in rows])
            # `wl` is the candidate's nominal (as-generated) estimate; `wl[lo..hi]`
            # is the routing envelope its slide/span DOF permit — lo = tightest
            # (joint slide minimum), hi = loose outer bound.  A wide envelope means
            # the candidate has lots of routing freedom for NUTS to exploit.
            # `topo`, not `idx`: the number changed meaning (1-based now),
            # and a changed value under an unchanged header is how a reader's
            # habit of adding one silently starts double-counting.
            print(f"   {'topo':>4} {'type':<{type_w}} {'wl':>8} {'wl[lo..hi]':>17} "
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
                    # It is the rep's own `topo` number: the ordinal position
                    # among families is NOT the pin id.
                    marks.append(f"group:{i + 1}")
                ms_s = ("-" if ms is None
                        else "free" if ms >= self._SLIDE_SENTINEL
                        else str(ms))
                env = f"[{lo:.0f}..{hi:.0f}]" if lo is not None else "-"
                print(f"   {i + 1:>4} {typ:<{type_w}} {wl:>8} {env:>17} "
                      f"{nsegs:>4} {pt:>4} {ms_s:>7}  {','.join(marks)}")

            # Tug-of-war detail: which interior segment each opposing rider
            # pair stretches (cand `disp` = the selected/displayed candidate).
            for (t, lo, hi) in tug:
                print(f"   tug: topo {disp + 1} seg{t} stretched by "
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

    def _report_block_contract(self, missing):
        """Report topologies whose block contract does not resolve (BUDA-1502).

        The invariant is `load_pipeline`'s: a candidate names blocks, and a
        resumed session looks them up in the frame `_restore_wrapper` picks
        for that bundle.  If the lookup fails there, the checkpoint cannot
        reload — and until this ran here, nothing said so until the resume,
        in the one session that could no longer repair it (`flow/ariane133`:
        `load_pipeline expanded` refusing seven blocks on a checkpoint whose
        build session had just reported a routed design).

        ADVISORY, not a counted violation.  What it describes is a fault in
        the design's INPUT — blocks a floorplan never placed — rather than in
        the wire, so folding it into the violation total would restate the
        same input fault in a metric that exists to measure routing.  It
        carries an id instead, which is what a methodology needs to gate.

        The cause is what makes it actionable, so each block is asked which
        of the two it is: the BDB has it and it has no placement (nothing
        downstream can help — it needs `derive_container_bboxes`, or a
        placed descendant for that to work from), or the BDB places it and
        the session simply never projected it (`add_blocks_from_bdb`)."""
        if not missing:
            return
        comps = ({c.name: c for c in self.bdb.all_components()}
                 if self.bdb is not None else {})
        unplaced, unprojected, unknown = [], [], []
        for name in sorted(missing):
            c = comps.get(name)
            if c is None:
                unknown.append(name)
            elif is_placed(c):
                unprojected.append(name)
            else:
                unplaced.append(name)

        def _sample(names, limit=5):
            head = ', '.join(names[:limit])
            return head + (f", … (+{len(names) - limit})"
                           if len(names) > limit else "")

        n_bundles = len({b for ids in missing.values() for b in ids})
        parts = [f"{len(missing)} block(s) named by {n_bundles} bundle(s) are "
                 f"not in the floorplan those topologies resolve against, so "
                 f"`load_pipeline` will refuse this design on resume."]
        if unplaced:
            parts.append(f"  {len(unplaced)} have a component row with NO "
                         f"placement ({_sample(unplaced)}) — give them one "
                         f"(`derive_container_bboxes` needs a placed "
                         f"descendant) or bundle at a depth that is placed.")
        if unprojected:
            parts.append(f"  {len(unprojected)} ARE placed in the BDB but "
                         f"were never projected into the session floorplan "
                         f"({_sample(unprojected)}) — add the missing "
                         f"`add_blocks_from_bdb <depth> skip`.")
        if unknown:
            parts.append(f"  {len(unknown)} match no component row at all "
                         f"({_sample(unknown)}).")
        buda_diag.emit("BUDA-1502", '\n'.join(parts))

    def _check_design(self, stage: str, all_candidates: bool = False):
        """Audit the design at `stage`.

        Returns the verdict as a dict — `{stage, ok, violations, by_kind}` —
        so the caller can record it (Phase 0: a flow harness must be able to
        gate on design quality, which needs the outcome as a value rather
        than as printed prose).  `ok=False` means the audit could not run at
        all; the printing behaviour is unchanged."""
        if stage in ("nuts", "dnuts") and self.nuts_result is None:
            print("  Error: run_nuts required first.")
            return {"stage": stage, "ok": False, "violations": 0,
                    "reason": "run_nuts required first", "by_kind": {}}
        if stage == "dnuts" and self.detailed_result is None:
            print("  Error: run_detailed_nuts required first.")
            return {"stage": stage, "ok": False, "violations": 0,
                    "reason": "run_detailed_nuts required first",
                    "by_kind": {}}

        # For the topo stage, auto-switch to all-candidates mode when no
        # topology has been selected yet (before run_planner).
        #
        # Gated on the planner never having RUN, not merely on the absence of
        # selections.  Those differ: a run in which the planner committed
        # nothing anywhere also leaves every selection unset, and promoting
        # THAT to all-candidates mode audits the candidate pool — which is
        # perfectly valid — and reports Success over a design with no routes
        # at all.  `self.planner` is set only by run_planner (and by ripup,
        # which follows it), so it is exactly the "has planning happened"
        # signal.
        if (stage == "topo" and not all_candidates
                and getattr(self, "planner", None) is None):
            no_selection = all(
                not w.input.candidates or w.plan.selected_topology_index < 0
                for w in self.bundles
            )
            if no_selection:
                all_candidates = True

        labels = {"topo": "topology", "nuts": "NUTS", "dnuts": "Detailed NUTS"}
        suffix = " (all candidates)" if (all_candidates and stage == "topo") else ""
        print(f"[Check] Verifying {labels[stage]}-level design{suffix}...")

        # BLOCK-CONTRACT audit — accumulated in the per-bundle loop below,
        # where each bundle's own frame is already resolved, and reported
        # after it as BUDA-1502.  See `_report_block_contract`.
        contract_missing = {}    # block name -> set of bundle ids

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
        thru_census = []  # TegThruCensusEntry — placed-stage THRU report (open 5)
        tug_bundles = 0  # realization-risk advisory (NOT violations); nuts/dnuts only
        tug_pairs = 0
        ndr_index = None  # shared detailed-row index, built on first governed bundle
        for w in self.bundles:
            if not w.input.candidates:
                continue
            bid = w.input.original_bundle.id

            b = w.input.original_bundle
            check_fp = self.fp
            # Resolve a hier bundle's own generation-space floorplan — but only
            # for a bundle the HIER bundler marked as such: a FLAT bundle is
            # also a `buda.HBundle` (the type was renamed), and with a BDB open
            # the bare isinstance test sent every flat bundle through the
            # depth-projection case, whose floorplan holds the BDB's components
            # — not the session blocks flat candidates were generated against —
            # so every busterm face failed (the design.tcl checkpoint flow).
            # Same marker guard as load_pipeline's restore validation and
            # dump_topologies' resolver (Codex #231), so the three cannot
            # disagree about which space a bundle is checked in.
            if (self.bdb is not None and isinstance(b, buda.HBundle)
                    and id(w) not in expanded_ids
                    and ((b.cell_context and b.entry_busterm_ids)
                         or b.drv_spec_depth >= 0)):
                resolved = self._floorplan_for_hbundle(b, hier_fp_cache, comps_by_name)
                if resolved is not None:
                    check_fp = resolved

            # Does every candidate's BLOCK CONTRACT resolve in the very frame
            # a resume would restore it against?  `check_fp` is picked by the
            # same rule `_restore_wrapper` uses, so this is `load_pipeline`'s
            # gate asked one session earlier — the only session that can
            # still fix it.
            #
            # Over the WHOLE pool, deliberately outside the `to_check` loop
            # below: persistence saves every candidate and `_restore_wrapper`
            # validates every candidate, so scoping this to the SELECTED one
            # would let an unselected candidate carry an unresolvable block,
            # pass the audit, and still be refused on resume — the exact
            # split this message exists to close (Codex P1 on #780).
            for topo in w.input.candidates:
                for bname in topo.connected_block_names:
                    if not check_fp.has_block(bname):
                        contract_missing.setdefault(bname, set()).add(bid)

            if all_candidates and stage == "topo":
                to_check = list(enumerate(w.input.candidates))
            elif w.plan.selected_topology_index >= 0:
                idx = w.plan.selected_topology_index
                to_check = [(idx, w.input.candidates[idx])]
            else:
                # The planner RAN and committed nothing for this bundle: it
                # has candidates and no selection, so there is no route to
                # audit.  Skipping it silently is what let a starved layer
                # mask produce a bus with no wire and a clean verdict.
                # `self.planner is None` means planning has not happened
                # yet, which is not a fault.
                if getattr(self, "planner", None) is not None:
                    nets = w.input.original_bundle.get_net_names()
                    collected.append((f"Bundle {bid}", _UnroutedViolation(
                        f"UNROUTED_BUNDLE: bundle {bid} "
                        f"('{nets[0] if nets else '?'}' x{len(nets)}) has "
                        f"{len(w.input.candidates)} candidate(s) but the "
                        f"planner selected none — the bus has no layer "
                        f"assignment and no wire (see the planner's "
                        f"NOTHING committed warning for the cause)")))
                    total += 1
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
                # THRU-block census rows (nuts/dnuts only — check_topo never
                # fills them): report-only, surfaced after the verdict below.
                thru_census.extend(getattr(res, "thru_census", ()) or ())
                violations += self._net_driver_fidelity(w, topo)
                # R9 typed NDR audit (dnuts stage, governed bundles only —
                # no rules = no calls = byte-identical output): under-width
                # wires, foreign metal inside a reserved run, missing or
                # misplaced shields.  Duck-typed violations, same summary
                # machinery.
                if stage == "dnuts" and w.input.ndr.active():
                    from buda_cmds import ndr_cmds
                    if ndr_index is None:
                        ndr_index = ndr_cmds.build_ndr_audit_index(self)
                    violations += ndr_cmds.audit_ndr_dnuts(self, w, ndr_index)
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

        self._report_block_contract(contract_missing)

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

        # THRU-block census (teg_multirect_status.md open 5): which rects of a
        # `teg_mode thru` multi-rect block are left to the block's internal
        # routing — INFO, never a violation (thru DECLARES the rects
        # internally connected; this makes the assumption discoverable when
        # it is wrong).  Verdict-keyed memo in the BUDA-1913/1914 style: a
        # repeat of the same verdict (same bundle, block, untouched-rect set)
        # says nothing — the nuts- and dnuts-stage audits usually agree — but
        # a verdict that genuinely CHANGED (placement moved a stub off a
        # rect) is reported rather than suppressed by a key already seen.
        # The stage is in the message, deliberately not in the key.
        if thru_census:
            said = self.__dict__.setdefault("_teg_thru_census_said", set())
            for e in thru_census:
                verdict = (e.bundle_id, e.block_name, e.detail)
                if verdict in said:
                    continue
                said.add(verdict)
                buda_diag.emit(
                    "BUDA-1907",
                    f"Bundle {e.bundle_id}: teg_mode thru block "
                    f"'{e.block_name}': {e.n_untouched} of {e.n_rects} "
                    f"rect(s) touched by no placed metal of the bundle — "
                    f"{e.detail} — left to the block's internal routing "
                    f"(thru declares the rects internally connected; report "
                    f"only, not a violation) ({stage})")

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
            # Positional reservations (LAYER_RESERVE): per cell and layer,
            # what the reservation bought (the top's tracks on reserved
            # ones) and whether the cell's own metal honours it.  Silent
            # when nothing is reserved.
            if stage in ("nuts", "dnuts"):
                self._print_layer_reserve_advisory()

        # Supply-doomed seat census (#536 option 1, report-only): placed
        # segments whose layer's real signal-track supply cannot host their
        # member bits — static width-infeasibility, the class behind
        # "unexplained" DNUTS opens on TOP layers.  NUTS-placed geometry, so
        # nuts/dnuts stages only.
        if stage in ("nuts", "dnuts"):
            self._report_doomed_seats()

        # Phase 0: hand the verdict back as data.  `total` and `collected` are
        # what the printing above already used, so this adds no new
        # computation and cannot disagree with what was reported.
        by_kind = {}
        for _prefix, v in collected:
            k = v.kind.name
            by_kind[k] = by_kind.get(k, 0) + 1
        return {"stage": stage, "ok": True, "violations": total,
                "by_kind": by_kind, "all_candidates": bool(all_candidates)}

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
        "ANTENNA":      "dangling metal past its own attachments",
        "TEG_OPEN":     "an OVER block's rect touched by no placed metal "
                        "(declared TEG bridge unrealized)",
        "NDR_WIDTH":    "governed bit narrower than its rule's width",
        "NDR_SPACING":  "foreign wire inside a rule's reserved run (clearance violated)",
        "NDR_SHIELD":   "shield wires missing or misplaced vs the rule's arrangement",
        "NDR_BOND":     "emitted shield not strapped to the power grid (floating metal)",
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

    # ── per-instance, per-layer demand (convergence ladder item 3) ─────────

    def _layer_demand(self, inst_filter="", layer_name=""):
        """What the REST of the design has placed over each instance's
        footprint, per layer, in the currency `set_cell_layer_share` speaks
        (signal tracks of the layer's pattern inside the instance bbox).

        This is the top plan's DEMAND on a block, read off the routed result
        rather than guessed: for instance I and layer L, every placed segment
        of a bundle NOT owned by I (owned = the bundle's frame instance is I
        or lies in I's subtree — a cell-local bundle expanded onto I, or one
        living deeper inside it) whose along-span overlaps I's extent and
        whose metal lies inside I's perpendicular extent.  The metal is
        unioned along the perpendicular axis and the layer's SIGNAL tracks
        whose centre falls inside that union are counted: `used`.  `supply`
        is every signal track in the same window (the share budget's own
        count — `_apply_layer_policies` sizes the collective lease from it,
        so the two agree by construction), and `pct` is used/supply.  The
        UNION is the honest figure for a share, which thins the cell's
        pattern uniformly over the whole instance: a track the top takes
        anywhere over the instance is a track the cell's uniform thinning
        must leave.  `bits` is the plain sum of the crossing segments' member
        bits — the size of the foreign traffic, NOT of its footprint: the
        two coincide for default-width unguarded routing (used <= bits
        there, since every bit takes one track and tracks can be shared
        along the instance), while an NDR-governed run's footprint EXCEEDS
        its traffic (guards and shields take tracks and carry no bit).

        Reads the DETAILED result when one exists (each bit's own track —
        exact; an NDR shield row is metal that blocks a track but not a
        member bit, so it counts in `used` and not in `bits`, and a result
        with every bit unplaced is an authoritative ZERO, never a fallback)
        and the abstract NUTS placement otherwise (a bus segment's `width`
        centred on its track, which is `bits` pitches of metal, so the
        count is the same up to phase).  Either placement may store a
        segment's span REVERSED (`span_lo > span_hi` keeps the endpoint
        identity corner logic relies on), so the extent is ordered before
        it is tested against the instance.

        Returns None when there is nothing to read — no open BDB, no placed
        component, no NUTS result — which the Tcl query reports as -1: a
        demand that was never computed is not a demand of zero.  `inst_filter`
        restricts the rows to one instance and its subtree (exact path, or
        `cell:<name>` for every placed instance of a cell); `layer_name` to
        one layer (unknown name: ValueError).  Rows are dicts ordered by
        depth, path, layer.  Layers with no `def_track_pattern` have no
        supply to count against and are skipped."""
        if self.bdb is None or self.nuts_result is None \
                or self.routing_grid is None:
            return None
        comps = [c for c in self.bdb.all_components() if is_placed(c)]
        if not comps:
            return None
        if inst_filter:
            if inst_filter.startswith("cell:"):
                cell = inst_filter[len("cell:"):]
                comps = [c for c in comps if c.cell == cell]
            else:
                pre = inst_filter.rstrip("/")
                comps = [c for c in comps
                         if c.name == pre or c.name.startswith(pre + "/")]
        lids = sorted(
            list(self.layers.get_layer_ids_by_dir(buda.LayerDir.HORIZONTAL)) +
            list(self.layers.get_layer_ids_by_dir(buda.LayerDir.VERTICAL)))
        names = {lid: n for n, lid in
                 getattr(self, "_layer_name_map", {}).items()}

        def lname(lid):
            return names.get(lid, f"L{lid}")

        if layer_name:
            want = [l for l in lids
                    if lname(l) == layer_name or str(l) == layer_name]
            if not want:
                raise ValueError(f"unknown layer {layer_name!r}; known: "
                                 + ", ".join(lname(l) for l in lids))
            lids = want
        lids = [l for l in lids if self.routing_grid.has_layer(l)
                and self.routing_grid.get_layer_grid(l)
                        .global_pattern().slots]
        # Ownership: the frame instance each bundle routes in.  Expanded
        # cell-local wrappers name their one instance; a same-level bundle
        # names its LCA container; a top-level one names nothing (owned by
        # no instance, so it is demand on every instance it crosses).
        frame, segbits = {}, {}
        for w in self.bundles:
            b = w.input.original_bundle
            frame[b.id] = b.instances[0] if b.instances else ""
            nb = len(b.net_names)
            sb = {}
            sel = w.plan.selected_topology_index
            if 0 <= sel < len(w.input.candidates):
                t = w.input.candidates[sel]
                for si in range(len(t.segments)):
                    n = nb
                    if t.seg_bits and si < len(t.seg_bits) and t.seg_bits[si]:
                        n = len(t.seg_bits[si])
                    sb[si] = n
            segbits[b.id] = (nb, sb)
        # The metal, per layer: (bundle, seg, along_lo, along_hi, perp_lo,
        # perp_hi, bits).  Detailed bits carry their own track and width and
        # count 1 bit each; an abstract bus segment is `bits` pitches wide.
        det = getattr(self, "detailed_result", None)
        metal = {}
        if det is not None:
            governed = {}
            for ns in det.net_segments:
                tp = ns.track_position
                if tp != tp:      # NaN = unplaced
                    continue
                metal.setdefault(ns.layer, []).append(
                    (ns.bundle_id, ns.seg_idx,
                     min(ns.span_lo, ns.span_hi), max(ns.span_lo, ns.span_hi),
                     tp - ns.width / 2.0, tp + ns.width / 2.0,
                     0 if getattr(ns, "is_shield", False) else 1))
                governed.setdefault((ns.bundle_id, ns.seg_idx), []).append(ns)
            # An NDR-governed run RESERVES more than its emitted rows: the
            # guard slots between and beyond its wires are kept empty and
            # emit no NetSegment, yet a cell cannot use them without
            # violating the rule's clearance — so the run's reserved
            # window joins the union too (0 bits: it is footprint, not
            # traffic), read by the SAME function the NDR_SPACING audit
            # reads it with.
            from buda_cmds import ndr_cmds
            wrappers = {w.input.original_bundle.id: w for w in self.bundles}
            for (bid, si), rows in governed.items():
                w = wrappers.get(bid)
                if w is None or not w.input.ndr.active():
                    continue
                layer = rows[0].layer
                spec = ndr_cmds.ndr_spec_for_layer(self, w.input.ndr, layer, w)
                if not spec.active():
                    continue
                s_lo = min(min(r.span_lo, r.span_hi) for r in rows)
                s_hi = max(max(r.span_lo, r.span_hi) for r in rows)
                run_lo, run_hi = ndr_cmds.ndr_reserved_run(
                    spec, self.routing_grid, layer, rows, s_lo, s_hi)
                metal.setdefault(layer, []).append(
                    (bid, si, s_lo, s_hi, run_lo, run_hi, 0))
        else:
            for ts in self.nuts_result.segments:
                tp = ts.track_position
                if not ts.placed or tp != tp:
                    continue
                nb, sb = segbits.get(ts.bundle_id, (0, {}))
                metal.setdefault(ts.layer, []).append(
                    (ts.bundle_id, ts.seg_idx,
                     min(ts.span_lo, ts.span_hi), max(ts.span_lo, ts.span_hi),
                     tp - ts.width / 2.0, tp + ts.width / 2.0,
                     sb.get(ts.seg_idx, nb)))
        # The instance's OWN need, per layer: the worst SEAT among the bus
        # segments solved in ITS frame or inside its SUBTREE — the same
        # ownership the demand reads, and the one the share is enforced
        # over: a share's thinned pattern is installed over the instance's
        # BBOX for the DNUTS reference solve, so a nested cell's own buses
        # see the enclosing cell's thinning too (E1's first measurement:
        # a cluster's 71% M5 share stranded the CORES' 32-bit buses inside
        # it, whose seats hold 35 tracks — 91%).  The need is the DNUTS
        # admission arithmetic — member bits against the span-clear pool
        # of the seat this plan gave the bus (`_seg_admission_pool`).  A
        # share thins the pattern UNIFORMLY, and a bus needing N of the P
        # tracks in its window cannot live under a share keeping fewer than
        # N/P of them, so this is the floor a derived share must respect.
        # Read off the ABSTRACT placement, which carries the seat windows;
        # the seat a cell-local solve gives the same bus can be narrower,
        # so the floor is a lower bound, said in the derivation's notes.
        wrappers_by_id = {w.input.original_bundle.id: w for w in self.bundles}
        own_segs = []
        for ts in self.nuts_result.segments:
            if not ts.placed or ts.track_position != ts.track_position:
                continue
            f = frame.get(ts.bundle_id)
            if f:
                own_segs.append((f, ts))
        eps = 1e-6
        rows = []
        for c in sorted(comps, key=lambda c: (c.depth, c.name)):
            own_pre = c.name + "/"
            for lid in lids:
                horiz = (self.layers.get_layer_dir(lid)
                         == buda.LayerDir.HORIZONTAL)
                if horiz:
                    a_lo, a_hi, p_lo, p_hi = c.x1, c.x2, c.y1, c.y2
                else:
                    a_lo, a_hi, p_lo, p_hi = c.y1, c.y2, c.x1, c.x2
                # The metal over this instance, split by OWNERSHIP and
                # read with ONE footprint rule: a wire covers the tracks
                # under its WIDTH, and an NDR-governed run covers its
                # guard slots too (the 0-bit rows above).  The own half
                # used to record each own bit's CENTRE track alone, so a
                # widened own wire or its guard run sat on a reserved
                # track with `own_hit` reading zero (Codex P2 on #936).
                ivals, own_ivals, bits, bundles = [], [], 0, set()
                for bid, _si, s_lo, s_hi, m_lo, m_hi, nb in metal.get(lid, []):
                    if s_hi <= a_lo + eps or s_lo >= a_hi - eps:
                        continue          # does not reach over the instance
                    if m_hi <= p_lo + eps or m_lo >= p_hi - eps:
                        continue          # beside it, not over it
                    f = frame.get(bid)
                    if f is not None and (f == c.name or f.startswith(own_pre)):
                        own_ivals.append((max(m_lo, p_lo), min(m_hi, p_hi)))
                        continue          # the instance's own routing
                    ivals.append((max(m_lo, p_lo), min(m_hi, p_hi)))
                    bits += nb
                    bundles.add(bid)

                def _union(iv):
                    out = []
                    for lo, hi in sorted(iv):
                        if out and lo <= out[-1][1] + eps:
                            out[-1][1] = max(out[-1][1], hi)
                        else:
                            out.append([lo, hi])
                    return out
                union, own_union = _union(ivals), _union(own_ivals)
                g = self.routing_grid.get_layer_grid(lid)
                tracks = [pos for pos, _slot in
                          g.signal_tracks_in(0.5 * (a_lo + a_hi), p_lo, p_hi)]
                used_tracks = [pos for pos in tracks
                               if any(lo - eps < pos < hi + eps
                                      for lo, hi in union)]
                used = len(used_tracks)
                supply = len(tracks)
                # The cell's OWN metal's tracks over the instance — what a
                # positional reservation's audit checks against the
                # reserved tracks — by the same rule as `used_tracks`.
                own_tracks = {round(pos, 6) for pos in tracks
                              if any(lo - eps < pos < hi + eps
                                     for lo, hi in own_union)}
                own_need, own_seat, own_window = 0.0, None, None
                for f, ts in own_segs:
                    if ts.layer != lid or not (f == c.name
                                               or f.startswith(own_pre)):
                        continue
                    w = wrappers_by_id.get(ts.bundle_id)
                    if w is None:
                        continue
                    sel = w.plan.selected_topology_index
                    if sel < 0 or sel >= len(w.input.candidates):
                        continue
                    # NEED is what the engine ADMITS on — for a governed
                    # segment its NDR group demand (wide bits, guards,
                    # shields, run ends), not its bit count — and the POOL
                    # is selected against the full demand with the doom
                    # test on the credited minimum: `_doomed_seats`'s own
                    # split, so a governed seat is not read as needing
                    # fewer slots than DNUTS will ask of it (Codex P2 on
                    # #935).  Identity on every ungoverned segment.
                    need = self._seg_admission_need(w, sel, ts.seg_idx,
                                                    layer=ts.layer)
                    pool = self._seg_admission_pool(
                        ts, g, self._seg_admission_need(
                            w, sel, ts.seg_idx, credited=False,
                            layer=ts.layer))
                    frac = 1.0 if pool <= 0 else min(1.0, need / pool)
                    if frac > own_need:
                        own_need = frac
                        own_seat = (ts.bundle_id, ts.seg_idx, need, pool)
                        # The seat's slide window in ABSOLUTE coordinates —
                        # what a positional derivation reads to say how
                        # many reserved tracks fall inside the block's own
                        # worst seat.
                        own_window = (min(ts.interval_lo, ts.interval_hi),
                                      max(ts.interval_lo, ts.interval_hi))
                rows.append({
                    "inst": c.name, "cell": c.cell, "depth": c.depth,
                    "layer": lid, "layer_name": lname(lid),
                    "bits": bits, "used": used, "supply": supply,
                    "pct": (100.0 * used / supply) if supply else 0.0,
                    "bundles": len(bundles),
                    # WHICH tracks (positions), for the share derivation's
                    # collision check; not part of the Tcl row.
                    "used_tracks": used_tracks,
                    # The instance's own worst seat on this layer: the
                    # fraction of its window the bus needs (1.0 = all of
                    # it), and which (bundle, seg, need, pool) it is.
                    "own_need": own_need, "own_seat": own_seat,
                    "own_window": own_window,
                    # The cell's own metal's tracks over the instance on
                    # this layer (positions) — the positional reservation
                    # audit's other half; not part of the Tcl row.
                    "own_tracks": sorted(own_tracks),
                })
        return rows

    def _print_layer_reserve_advisory(self):
        """check_design's LAYER_RESERVE lines: per (cell, layer) with a
        reservation, over its instances — reserved tracks, how many the
        top used (min..max per instance) and how many carry the cell's own
        metal (LOUD when non-zero: on a template that should be
        impossible, on a top-down cell it is the BUDA-1920 gap)."""
        rows = self._layer_reserve_audit()
        if not rows:
            return
        # Before detailed NUTS the rows read the ABSTRACT bus placement — a
        # seat's footprint, bits or bits+1 tracks by phase, conservative by
        # design — so a bus seated flush against a reserved track reads as
        # touching it while its bits, once placed, do not (measured on the
        # E5 SoC: io_blk_cell's own bus, 6 of 48 at the abstract stage, 0
        # at the detailed one).  An estimate is reported as one; VIOLATED
        # is a verdict on placed bits.
        placed = getattr(self, "detailed_result", None) is not None
        by = {}
        for r in rows:
            by.setdefault((r["cell"], r["layer_name"]), []).append(r)
        for (cell, lname), rs in sorted(by.items()):
            used = [r["top_used"] for r in rs]
            own = [r["own_hit"] for r in rs]
            # The corridor's HIT RATE: of every track the top takes over
            # these instances, the share that is a reserved one.  Printed
            # by the audit because E5 computed it by hand and got the
            # denominator wrong (hits over the instance's SUPPLY read 6-11 %
            # where hits over the top's own tracks read 0.65-0.97).
            hit, tot = sum(used), sum(r["top_total"] for r in rs)
            rate = (f"{100.0 * hit / tot:.0f}% of its {tot}" if tot
                    else "none of its 0")
            print(f"  LAYER_RESERVE: {cell} {lname}: {rs[0]['reserved']} "
                  f"track(s) reserved over {len(rs)} instance(s); the top "
                  f"uses {min(used)}..{max(used)} of them per instance "
                  f"({rate} track(s) over them); "
                  f"own metal on reserved tracks"
                  + ("" if placed else
                     " (abstract seat footprint — an estimate, the bits "
                     "are read at the detailed stage)")
                  + f": {min(own)}..{max(own)} per instance"
                  + ("" if max(own) == 0 or not placed else " — VIOLATED at "
                     + ", ".join(r["inst"] for r in rs if r["own_hit"])))

    def _report_layer_demand(self, inst_filter="", layer_name=""):
        """`report_layer_demand`: the `_layer_demand` rows as a table (flow
        log) with the per-layer worst instance on the terminal — the number
        a share derivation would start from."""
        try:
            rows = self._layer_demand(inst_filter, layer_name)
        except ValueError as e:
            print(f"Error: report_layer_demand: {e}")
            return
        if rows is None:
            print("Error: report_layer_demand needs an open BDB with placed "
                  "components and a NUTS result (run_nuts) to read the "
                  "demand off")
            return
        basis = ("detailed bit tracks"
                 if getattr(self, "detailed_result", None) is not None
                 else "abstract bus tracks")
        print(f"=== Layer demand ({basis}; used/supply = signal tracks over "
              f"the instance the rest of the design takes) ===")
        if not rows:
            print(f"  no placed instance matches {inst_filter!r}")
            return
        w_inst = max(len(r["inst"]) for r in rows)
        w_cell = max(len(r["cell"]) for r in rows)
        print(f"  {'instance':<{w_inst}}  {'cell':<{w_cell}}  layer  "
              f"{'bits':>6} {'used':>6} {'supply':>6}  {'pct':>6}  bundles")
        for r in rows:
            print(f"  {r['inst']:<{w_inst}}  {r['cell']:<{w_cell}}  "
                  f"{r['layer_name']:<5}  {r['bits']:>6} {r['used']:>6} "
                  f"{r['supply']:>6}  {r['pct']:>5.1f}%  {r['bundles']}")
        worst = {}
        for r in rows:
            k = r["layer_name"]
            if k not in worst or r["pct"] > worst[k]["pct"]:
                worst[k] = r
        n_inst = len({r["inst"] for r in rows})
        print(f"  {n_inst} instance(s) x {len(worst)} layer(s); worst per "
              f"layer: " + ", ".join(
                  f"{k} {v['pct']:.1f}% ({v['inst']})"
                  for k, v in sorted(worst.items(),
                                     key=lambda kv: kv[1]["layer"])))

    # ── derive_cell_layer_shares (convergence ladder item 4) ──────────────

    def _derive_cell_layer_shares(self, cells=None, floor_own=True):
        """Turn the demand rows into `set_cell_layer_share` lines: for every
        cell in scope and every patterned layer, the COMPLEMENT of the
        worst demand over the cell's instances, floored to a whole percent
        (a template is solved once and copied everywhere, so the cell gets
        what its most crowded occurrence leaves).  Rung 4 of the ladder —
        the budget handed DOWN is derived from the top's own plan instead
        of guessed.

        Scope, in order: `cells` when given; else the cells marked
        `set_bottom_up` (the ones about to be solved once and copied —
        the E1 arm); else every cell owning a cell-local template bundle
        (a share governs a cell's OWN interconnect, so a cell with none
        has nothing to budget).  Each rung is taken when it EXISTS, not
        when it survives the placed-instance filter: marks naming only
        instance-less cells give an EMPTY scope (each one said), never
        the next rung's cells.  A layer the top does not touch over any
        instance gets no line (full use is the default); a complement
        whose thinning keeps ZERO slots per period is SKIPPED and said
        (the top leaves the cell less than one slot — a band question,
        `set_cell_layer_cap`, not a share), since `set_cell_layer_share`
        refuses it loudly.

        The complement is FLOORED by the cell's own need (`floor_own`,
        the default): a share thins the pattern uniformly, so a bus of the
        cell's own that needs N of the P tracks in its seat cannot live
        under a share keeping fewer than N/P of them — the share is raised
        to the smallest slot count that hosts the worst own seat over the
        cell's instances, said on the line; where that is EVERY slot the
        layer gets no line at all (full use) and the note says what the
        top wanted there, since a uniform share cannot hand the top a
        complement the block's own buses need too — that wants a
        POSITIONAL reservation, which is not a share.  E1 measured the
        unfloored derivation stranding 410 bits at NQ=2 where a blind
        round strands 8, every one of them a cluster's own 32-bit bus in a
        35-track window under a 71% share.  `floor_own=False` is the
        study knob (`nofloor`).

        Each line carries a COLLISION count: a share is a BUDGET (the
        cell's pattern is thinned to its first floor(s x n_signal) SIGNAL
        slots per period) and the top's demand sits on SPECIFIC tracks, so
        the two need not be disjoint.  `collide` is the most tracks, over
        the cell's instances, that the top holds INSIDE the slots the
        thinned pattern would keep — zero means the budget is also a
        reservation there, nonzero is exactly what E1 measures.

        Returns (lines, notes, scope): `lines` are dicts (cell, layer,
        layer_name, pct, kept, n_sig, worst_inst, worst_pct, n_inst,
        collide); `notes` are printable strings; `scope` is the list of
        cells the derivation judged — what `apply` replaces the shares OF,
        so a scoped cell's share on a layer with no line is a stale one.
        None when there is no demand to read (no NUTS result)."""
        rows = self._layer_demand()
        if rows is None:
            return None
        notes = []
        by_cell = {}
        for r in rows:
            by_cell.setdefault(r["cell"], []).append(r)
        if cells is not None:
            # An explicit list decides the scope, an EMPTY one included
            # (no cell — not the default rungs, Codex P2 on #934).
            scope = list(cells)
            unknown = [c for c in scope if c not in by_cell]
            for c in unknown:
                notes.append(f"cell '{c}': no placed instance — skipped")
            scope = [c for c in scope if c in by_cell]
            how = "named"
        else:
            marked = sorted(set(self.bdb.bottom_up_cells())) \
                if self.bdb is not None else []
            if marked:
                # The MARKS decide the scope, not the marks that happen
                # to have demand rows: a mark on a cell with no placed
                # instance (set_bottom_up accepts one) used to empty this
                # list and fall through to the bundle-owning cells, so
                # `apply`/`file` derived — and REMOVED — shares for cells
                # the documented scope never named (Codex P2 on #934).
                for c in marked:
                    if c not in by_cell:
                        notes.append(f"marked cell '{c}': no placed "
                                     f"instance — skipped")
                scope = [c for c in marked if c in by_cell]
                how = "marked set_bottom_up"
            else:
                templates = (getattr(self, "_hier_bundles_orig", None)
                             or self.bundles)
                owners = set()
                for w in templates:
                    ctx = w.input.original_bundle.cell_context
                    if ctx:
                        owners.add(self._bu_cell_of(ctx) or ctx)
                scope = sorted(c for c in owners if c in by_cell)
                how = "owning a cell-local bundle"
        notes.append(f"scope: {len(scope)} cell(s) ({how})"
                     + (": " + ", ".join(scope) if scope else ""))
        lines = []
        for cell in scope:
            per_layer = {}
            for r in by_cell[cell]:
                per_layer.setdefault(r["layer"], []).append(r)
            for lid in sorted(per_layer):
                lrows = per_layer[lid]
                lname = lrows[0]["layer_name"]
                worst = max(lrows, key=lambda r: r["pct"])
                if worst["used"] == 0:
                    continue                # the top takes nothing here
                pct = int(math.floor(100.0 - worst["pct"]))
                pat = self.routing_grid.get_layer_grid(lid).global_pattern()
                n_sig = sum(1 for sl in pat.slots if sl.type == "SIGNAL")
                kept = int(pct / 100.0 * n_sig + 1e-9)
                # The own-need floor: the smallest slot count per period
                # that hosts the cell's worst own seat on this layer.
                own = max(lrows, key=lambda r: r["own_need"])
                own_pct = 100.0 * own["own_need"]
                floored = False
                if floor_own and own["own_seat"] is not None:
                    kept_min = int(math.ceil(own["own_need"] * n_sig - 1e-9))
                    bid, si, need, pool = own["own_seat"]
                    if kept_min >= n_sig:
                        notes.append(
                            f"{cell} {lname}: the top leaves "
                            f"{100.0 - worst['pct']:.1f}% at {worst['inst']} "
                            f"but {cell}'s own bundle {bid} seg {si} needs "
                            f"{need} of the {pool} tracks in its seat "
                            f"({own_pct:.0f}%, at {own['inst']}) — no share "
                            f"(full use): a uniform share cannot host both; "
                            f"this wants a positional reservation")
                        continue
                    if kept_min > kept:
                        pct_floor = int(math.ceil(100.0 * kept_min / n_sig))
                        # The share is declared in WHOLE percent and the
                        # command keeps floor(pct/100 x n_sig) slots, so
                        # the line must report the count the DECLARED
                        # percent keeps (on a 128-slot pattern 50 slots
                        # round up to 40%, which keeps 51) — and a floor
                        # that rounds to 100% is full use, not a share:
                        # `set_cell_layer_share ... 100` REMOVES the share
                        # (Codex P2 on #935; 127 of 128 slots is the case).
                        if pct_floor >= 100:
                            notes.append(
                                f"{cell} {lname}: the top leaves "
                                f"{100.0 - worst['pct']:.1f}% at {worst['inst']} "
                                f"but {cell}'s own bundle {bid} seg {si} needs "
                                f"{need} of the {pool} tracks in its seat "
                                f"({own_pct:.0f}%, at {own['inst']}), "
                                f"{kept_min} of {n_sig} slots — every whole "
                                f"percent under 100 keeps fewer; no share "
                                f"(full use): a uniform share cannot host "
                                f"both; this wants a positional reservation")
                            continue
                        kept_floor = int(pct_floor / 100.0 * n_sig + 1e-9)
                        notes.append(
                            f"{cell} {lname}: share floored {pct}% -> "
                            f"{pct_floor}% ({kept_floor}/{n_sig} slots): "
                            f"{cell}'s own bundle {bid} seg {si} needs "
                            f"{need} of the {pool} tracks in its seat "
                            f"({own_pct:.0f}%, at {own['inst']})")
                        pct, kept, floored = pct_floor, kept_floor, True
                if pct <= 0 or kept == 0:
                    notes.append(
                        f"{cell} {lname}: the top leaves {100.0 - worst['pct']:.1f}% "
                        f"at {worst['inst']} — floor({pct / 100.0:.2f} x {n_sig}) "
                        f"= 0 slot(s)/period, below the minimum meaningful share "
                        f"({math.ceil(100.0 / n_sig)}%); no share derived — a "
                        f"band question (set_cell_layer_cap), not a share")
                    continue
                # Collision: the top's tracks that fall INSIDE the slots the
                # thinned pattern keeps, at the worst instance over the cell.
                tp = self._thinned_pattern(pat, pct / 100.0)
                horiz = (self.layers.get_layer_dir(lid)
                         == buda.LayerDir.HORIZONTAL)
                comps = {c.name: c for c in self.bdb.all_components()} \
                    if self.bdb is not None else {}
                collide, collide_inst = 0, ""
                for r in lrows:
                    if not r["used_tracks"]:
                        continue
                    c = comps.get(r["inst"])
                    if c is None:
                        continue
                    p_lo, p_hi = (c.y1, c.y2) if horiz else (c.x1, c.x2)
                    kept_pos = [pos for pos, sl in tp.tracks_in_range(p_lo, p_hi)
                                if sl.type == "SIGNAL"]
                    n = sum(1 for u in r["used_tracks"]
                            if any(abs(u - k) < 1e-6 for k in kept_pos))
                    if n > collide:
                        collide, collide_inst = n, r["inst"]
                lines.append({
                    "cell": cell, "layer": lid, "layer_name": lname,
                    "pct": pct, "kept": kept, "n_sig": n_sig,
                    "worst_inst": worst["inst"], "worst_pct": worst["pct"],
                    "worst_used": worst["used"], "worst_supply": worst["supply"],
                    "n_inst": len(lrows),
                    "collide": collide, "collide_inst": collide_inst,
                    "own_pct": own_pct, "floored": floored,
                })
        return lines, notes, scope

    def _policy_scope(self, cells, by_cell, notes):
        """The derivation scope shared by the share and the reservation
        derivations: the named `cells` (an EMPTY list names no cell —
        Codex P2 on #934), else the `set_bottom_up` MARKS (the marks, not
        the marks that happen to have demand rows), else every cell owning
        a cell-local bundle.  Appends the scope note; returns the list."""
        if cells is not None:
            scope = list(cells)
            for c in [c for c in scope if c not in by_cell]:
                notes.append(f"cell '{c}': no placed instance — skipped")
            scope = [c for c in scope if c in by_cell]
            how = "named"
        else:
            marked = sorted(set(self.bdb.bottom_up_cells())) \
                if self.bdb is not None else []
            if marked:
                for c in marked:
                    if c not in by_cell:
                        notes.append(f"marked cell '{c}': no placed "
                                     f"instance — skipped")
                scope = [c for c in marked if c in by_cell]
                how = "marked set_bottom_up"
            else:
                templates = (getattr(self, "_hier_bundles_orig", None)
                             or self.bundles)
                owners = set()
                for w in templates:
                    ctx = w.input.original_bundle.cell_context
                    if ctx:
                        owners.add(self._bu_cell_of(ctx) or ctx)
                scope = sorted(c for c in owners if c in by_cell)
                how = "owning a cell-local bundle"
        notes.append(f"scope: {len(scope)} cell(s) ({how})"
                     + (": " + ", ".join(scope) if scope else ""))
        return scope

    # ── derive_cell_layer_reserves (convergence ladder item 6) ────────────

    def _derive_cell_layer_reserves(self, cells=None):
        """The POSITIONAL twin of _derive_cell_layer_shares: per cell in
        scope and per layer, the UNION over the cell's instances of the
        tracks the top placed over them (`used_tracks`), each mapped into
        the CELL's frame (position minus the instance's origin on the
        layer's perpendicular axis), as a `set_cell_layer_reserve` line.
        A template is solved once and copied, so the cell must leave
        free, on every instance, every track the top wants on ANY of them
        — the union is the price of solve-once-copy, and the per-instance
        `used` range says how much of it each instance really needs.

        Only instances in the cell's own frame (orientation N) contribute:
        a rotated or mirrored instance's tracks would need the inverse
        transform, and E1's vehicles carry none — such an instance is
        counted and said rather than folded in wrongly.

        Each line reports `seat_hit` — how many of the reserved tracks
        fall inside the cell's own worst seat window (`own_window`, read
        off the abstract placement) — and `own_hit`, how many carry the
        cell's own metal NOW.  Neither is a refusal: a positional
        reservation is exactly the primitive that lets the block MOVE its
        bus off named tracks instead of losing a fraction of every period,
        and whether the local solve finds the room is what a run under the
        lines measures.

        Returns (lines, notes, scope): `lines` are dicts (cell, layer,
        layer_name, positions, n_inst, n_skipped, used_lo, used_hi,
        seat_hit, own_hit); None before a NUTS result."""
        rows = self._layer_demand()
        if rows is None:
            return None
        notes = []
        by_cell = {}
        for r in rows:
            by_cell.setdefault(r["cell"], []).append(r)
        scope = self._policy_scope(cells, by_cell, notes)
        comps = {c.name: c for c in self.bdb.all_components()} \
            if self.bdb is not None else {}
        lines = []
        frames, ocache = {}, {}
        for cell in scope:
            per_layer = {}
            for r in by_cell[cell]:
                per_layer.setdefault(r["layer"], []).append(r)
            for lid in sorted(per_layer):
                lrows = per_layer[lid]
                lname = lrows[0]["layer_name"]
                horiz = (self.layers.get_layer_dir(lid)
                         == buda.LayerDir.HORIZONTAL)
                # Every occurrence sharing the template frame folds in
                # through its orientation (S/FN/FS flip the axis); a
                # 90-degree one belongs to the clone class, whose frame an
                # upright-stated position cannot reach (BUDA-1921).
                if cell not in frames:
                    frames[cell] = self._reserve_frames(
                        cell, None, list(comps.values()), ocache)
                fr, n_rot = frames[cell]
                union, used_counts, skipped = [], [], 0

                def to_ref(u, c, o):
                    # The computed float, unrounded: the line's formatter
                    # round-trips it, and a three-decimal rounding here
                    # MOVED a track (`10.0004` -> `10.0`, outside the
                    # audit's 1e-6 match; Codex P2 on #936).
                    ext = (c.y2 - c.y1) if horiz else (c.x2 - c.x1)
                    origin = c.y1 if horiz else c.x1
                    return self._reserve_ref_pos(u - origin, o, ext, horiz)

                def to_abs(q, c, o):
                    ext = (c.y2 - c.y1) if horiz else (c.x2 - c.x1)
                    origin = c.y1 if horiz else c.x1
                    return origin + self._reserve_ref_pos(q, o, ext, horiz)

                for r in lrows:
                    c = comps.get(r["inst"])
                    if c is None:
                        continue
                    o = fr.get(r["inst"])
                    if o is None:
                        skipped += 1
                        continue
                    used_counts.append(len(r["used_tracks"]))
                    for u in r["used_tracks"]:
                        loc = to_ref(u, c, o)
                        if not any(abs(loc - q) < 1e-6 for q in union):
                            union.append(loc)
                if skipped:
                    notes.append(f"{cell} {lname}: {skipped} 90-degree-"
                                 f"rotated instance(s) not folded in (an "
                                 f"upright-frame position has no image on "
                                 f"the same layer there — BUDA-1921)")
                if not union:
                    continue                # the top takes nothing here
                union.sort()
                # The block's own worst seat on this layer, and how many of
                # the reserved tracks land inside its window; the own metal
                # currently ON reserved tracks (the union, per instance).
                own = max(lrows, key=lambda r: r["own_need"])
                seat_hit = 0
                if own["own_window"] is not None:
                    oc = comps.get(own["inst"])
                    oo = fr.get(own["inst"])
                    if oc is not None and oo is not None:
                        lo, hi = own["own_window"]
                        seat_hit = sum(1 for q in union
                                       if lo - 1e-6 <= to_abs(q, oc, oo)
                                       <= hi + 1e-6)
                own_hit = 0
                for r in lrows:
                    c = comps.get(r["inst"])
                    o = fr.get(r["inst"])
                    if c is None or o is None:
                        continue
                    absres = [to_abs(q, c, o) for q in union]
                    own_hit = max(own_hit, sum(
                        1 for t_ in r.get("own_tracks", [])
                        if any(abs(t_ - a) < 1e-6 for a in absres)))
                lines.append({
                    "cell": cell, "layer": lid, "layer_name": lname,
                    "positions": union, "n_inst": len(used_counts),
                    "n_skipped": skipped,
                    "used_lo": min(used_counts), "used_hi": max(used_counts),
                    "seat_hit": seat_hit,
                    "seat": own["own_seat"], "seat_inst": own["inst"],
                    "own_hit": own_hit,
                })
        return lines, notes, scope

    def _report_cell_layer_reserves(self, cells=None, apply=False, path=""):
        """`derive_cell_layer_reserves`: the derivation as a table plus the
        `set_cell_layer_reserve` paste lines; `apply` declares them here
        through the command itself, `path` writes them for a later session
        to `source`.  The derivation is the reservation of every cell in
        scope: a scoped cell's reservation on a layer with no line is
        REMOVED (declared `off`; written as an `off` line, since a session
        reopening the same BDB restores it before sourcing the file) — the
        share derivation's contract (Codex P2 on #934)."""
        out = self._derive_cell_layer_reserves(cells)
        if out is None:
            print("Error: derive_cell_layer_reserves needs a NUTS result to "
                  "read the demand off (run_nuts; run_detailed_nuts for "
                  "exact tracks) — see report_layer_demand")
            return
        lines, notes, scope = out
        det = getattr(self, "detailed_result", None)
        basis = ("detailed bit tracks" if det is not None
                 else "abstract bus tracks")
        print(f"=== Cell track reservations derived from the top's demand "
              f"({basis}) ===")
        for n in notes:
            print(f"  {n}")

        def _fmt(l):
            return ",".join(fmt_pos(q) for q in l["positions"])
        text = [f"set_cell_layer_reserve {l['cell']} {l['layer_name']} "
                f"{_fmt(l)}" for l in lines]
        names = {lid: n for n, lid in
                 getattr(self, "_layer_name_map", {}).items()}
        emitted = {(l["cell"], l["layer"]) for l in lines}
        held = getattr(self, "_cell_layer_reserves", None) or {}
        stale = [(c, lid, names.get(lid, f"L{lid}"), len(held[(c, lid)]))
                 for (c, lid) in sorted(held)
                 if c in scope and (c, lid) not in emitted and held[(c, lid)]]
        if lines:
            w_cell = max(len(l["cell"]) for l in lines)
            print(f"  {'cell':<{w_cell}}  layer  tracks  insts  used/inst  "
                  f"seat_hit  own_hit")
            for l in lines:
                seat = ""
                if l["seat"] is not None:
                    bid, si, need, pool = l["seat"]
                    seat = (f" (bundle {bid} seg {si} needs {need} of "
                            f"{pool} at {l['seat_inst']})")
                print(f"  {l['cell']:<{w_cell}}  {l['layer_name']:<5}  "
                      f"{len(l['positions']):>6}  {l['n_inst']:>5}  "
                      f"{l['used_lo']:>4}..{l['used_hi']:<4}  "
                      f"{l['seat_hit']:>8}  {l['own_hit']:>7}{seat}")
            n_seat = sum(1 for l in lines if l["seat_hit"])
            print(f"  {len(lines)} reservation(s) derived; {n_seat} with "
                  f"reserved tracks inside the cell's own worst seat (the "
                  f"local solve must move that bus — what a run under "
                  f"these lines measures)")
            print("  --- flow-text lines (declare BEFORE run_planner hier) ---")
            for t in text:
                print(f"  {t}")
        else:
            print("  nothing to declare: the top takes no track over any "
                  "instance in scope")
        if path:
            with open(path, "w") as f:
                f.write("# derive_cell_layer_reserves: the top's placed "
                        f"tracks over each instance ({basis}), in the "
                        "cell's frame; source before run_planner hier\n")
                f.write("# scope: " + (",".join(scope) if scope else "(none)")
                        + "\n")
                if not text:
                    f.write("# nothing to declare: the top takes no track "
                            "over any instance in scope\n")
                for t in text:
                    f.write(t + "\n")
                if stale:
                    f.write("# removed: reservations held when this was "
                            "derived that the top's demand no longer "
                            "supports\n")
                for c, lid, lname, n in stale:
                    f.write(f"set_cell_layer_reserve {c} {lname} off"
                            f"   # was {n} track(s)\n")
            print(f"  written to {path}"
                  + ("" if text else " (header only — no line to declare)")
                  + (f"; {len(stale)} removal line(s)" if stale else ""))
        if apply:
            from buda_cmds import bdb_cmds
            for l in lines:
                bdb_cmds.cmd_set_cell_layer_reserve(
                    self, "set_cell_layer_reserve",
                    [l["cell"], l["layer_name"], _fmt(l)],
                    f"set_cell_layer_reserve {l['cell']} {l['layer_name']} "
                    f"{_fmt(l)}")
            for c, lid, lname, n in stale:
                print(f"  {c} {lname}: {n} reserved track(s) held from an "
                      f"earlier declaration, no line derived now — removed")
                bdb_cmds.cmd_set_cell_layer_reserve(
                    self, "set_cell_layer_reserve", [c, lname, "off"],
                    f"set_cell_layer_reserve {c} {lname} off")
            print(f"  applied {len(lines)} reservation(s) to this session"
                  + (f"; removed {len(stale)} stale reservation(s) in scope"
                     if stale else ""))

    def _report_cell_layer_shares(self, cells=None, apply=False, path="",
                                  floor_own=True):
        """`derive_cell_layer_shares`: print the derivation as a table plus
        the `set_cell_layer_share` lines as flow-text paste lines; `apply`
        declares them in this session through the command itself (so the
        validation, the BDB write-through and the print are the command's),
        `path` writes them to a file a later session can `source`.

        `apply` makes the derivation THE budget of every cell in scope: a
        share a scoped cell holds on a layer the derivation emitted no line
        for — the top no longer touches it, or the new complement keeps
        zero slots — is REMOVED (declared at 100%, the command's own
        removal) and said, since leaving it would keep constraining the
        next plan under a budget this run did not derive (Codex P2 on
        #934).  A cell outside the scope keeps its shares."""
        out = self._derive_cell_layer_shares(cells, floor_own)
        if out is None:
            print("Error: derive_cell_layer_shares needs a NUTS result to read "
                  "the demand off (run_nuts; run_detailed_nuts for exact "
                  "tracks) — see report_layer_demand")
            return
        lines, notes, scope = out
        det = getattr(self, "detailed_result", None)
        basis = ("detailed bit tracks" if det is not None
                 else "abstract bus tracks")
        print(f"=== Cell layer shares derived from the top's demand ({basis}) "
              f"===")
        for n in notes:
            print(f"  {n}")
        text = [f"set_cell_layer_share {l['cell']} {l['layer_name']} {l['pct']}"
                for l in lines]
        # The derivation is the budget of every cell in scope, so a share a
        # scoped cell still HOLDS on a layer with no line is a stale one —
        # an earlier declaration the top's demand no longer supports.  Both
        # doors remove it: `apply` through the command's own pct-100 path
        # (session entry and BDB row together), and the FILE as a written
        # `... 100` line, because a later session opening the SAME BDB
        # restores the persisted share BEFORE it sources the file, so a
        # file carrying only the new lines would leave it constraining the
        # next plan (Codex P2 on #934, both halves).
        names = {lid: n for n, lid in
                 getattr(self, "_layer_name_map", {}).items()}
        emitted = {(l["cell"], l["layer"]) for l in lines}
        held = getattr(self, "_cell_layer_shares", None) or {}
        stale = [(c, lid, names.get(lid, f"L{lid}"), held[(c, lid)])
                 for (c, lid) in sorted(held)
                 if c in scope and (c, lid) not in emitted]

        def _write(path):
            # ALWAYS rewrite the requested file, an empty derivation
            # included: a later session sources it, and a stale file from
            # an earlier run would hand that session constraints this run
            # did not derive (Codex P2 on #934).
            with open(path, "w") as f:
                f.write("# derive_cell_layer_shares: the complement of the "
                        f"top's demand ({basis}); source before "
                        "run_planner hier\n")
                # The SCOPE, every cell of it: a driver re-deriving in a
                # later round must pin the same scope, and a cell the top
                # took nothing over this round has no line to read it off
                # (Codex P2 on #935).
                f.write("# scope: " + (",".join(scope) if scope else "(none)")
                        + "\n")
                if not text:
                    f.write("# nothing to declare: the top takes no track "
                            "over any instance in scope\n")
                for t in text:
                    f.write(t + "\n")
                if stale:
                    f.write("# removed: shares held when this was derived "
                            "that the top's demand no longer supports (a "
                            "session reopening the same BDB restores them "
                            "before sourcing this file)\n")
                for c, lid, lname, was in stale:
                    f.write(f"set_cell_layer_share {c} {lname} 100"
                            f"   # was {100.0 * was:g}%\n")
            print(f"  written to {path}"
                  + ("" if text else " (header only — no line to declare)")
                  + (f"; {len(stale)} removal line(s)" if stale else ""))

        def _apply():
            from buda_cmds import bdb_cmds
            for l in lines:
                bdb_cmds.cmd_set_cell_layer_share(
                    self, "set_cell_layer_share",
                    [l["cell"], l["layer_name"], str(l["pct"])],
                    f"set_cell_layer_share {l['cell']} {l['layer_name']} "
                    f"{l['pct']}")
            # The stale set above, removed through the command's own
            # pct-100 path so the BDB row goes with the session entry.
            for c, lid, lname, was in stale:
                print(f"  {c} {lname}: share {100.0 * was:g}% held from an "
                      f"earlier declaration, no line derived now — removed")
                bdb_cmds.cmd_set_cell_layer_share(
                    self, "set_cell_layer_share", [c, lname, "100"],
                    f"set_cell_layer_share {c} {lname} 100")
            print(f"  applied {len(lines)} share(s) to this session"
                  + (f"; removed {len(stale)} stale share(s) in scope"
                     if stale else ""))

        if not lines:
            print("  nothing to declare: the top takes no track over any "
                  "instance in scope")
            if path:
                _write(path)
            if apply:
                _apply()
            return
        w_cell = max(len(l["cell"]) for l in lines)
        w_inst = max([len(l["worst_inst"]) for l in lines] + [14])
        print(f"  {'cell':<{w_cell}}  layer  share  kept   insts  "
              f"{'worst instance':<{w_inst}}  worst    own   collide")
        for l in lines:
            print(f"  {l['cell']:<{w_cell}}  {l['layer_name']:<5}  "
                  f"{l['pct']:>4}%  {l['kept']}/{l['n_sig']:<3}  "
                  f"{l['n_inst']:>5}  {l['worst_inst']:<{w_inst}}  "
                  f"{l['worst_pct']:>5.1f}%  {l['own_pct']:>4.0f}%"
                  f"{'F' if l['floored'] else ' '}  {l['collide']}"
                  + (f" ({l['collide_inst']})" if l['collide'] else ""))
        n_coll = sum(1 for l in lines if l["collide"])
        print(f"  {len(lines)} share(s) derived; {n_coll} with the top holding "
              f"tracks inside the kept slots (a share is a budget, not a "
              f"reservation — E1 measures what that costs)")
        print("  --- flow-text lines (declare BEFORE run_planner hier) ---")
        for t in text:
            print(f"  {t}")
        if path:
            _write(path)
        if apply:
            _apply()

    # ── derive_top_plan (convergence ladder item 6c) ──────────────────────

    def _top_plan_scope_insts(self, cells, notes):
        """The placed instances of the cells in the derivation scope (the
        share/reserve derivations' rule: named `cells`, else the
        `set_bottom_up` marks, else every cell owning a cell-local bundle)
        — the frames a handed-down plan must NOT come from, since those
        cells are re-solved as templates under the budget the same round
        derived for them.  Returns (paths, scope)."""
        if self.bdb is None:
            notes.append("scope: no BDB open — a flat design, every "
                         "planned bundle is the top's")
            return [], []
        comps = [c for c in self.bdb.all_components() if is_placed(c)]
        by_cell = {}
        for c in comps:
            by_cell.setdefault(c.cell, []).append(c)
        scope = self._policy_scope(cells, by_cell, notes)
        in_scope = set(scope)
        return [c.name for c in comps if c.cell in in_scope], scope

    def _derive_top_plan(self, cells=None):
        """The top's PLAN — every globally planned bundle's selected
        candidate, its per-segment layers and its abstract seats — as
        `pin_plan` lines a later session sources before `run_planner hier`,
        so the blocks are routed under the SAME top the reservation was
        derived from (convergence ladder item 6c).

        E5 measured why the informed loop had no fixpoint: the informed
        round re-plans the top from scratch after the templates moved, so
        on the recorded NQ = 2 rounds 4 of 13 top-level bundles kept their
        topology and none kept its seat, and every derivation named a top
        that the next round did not route.  A track PREFERENCE cannot fix
        that (6b, measured); a pin can.

        "The top" is every bundle in the routed list that is not a
        bottom-up copy and whose frame instance is not inside a placed
        instance of a scoped cell — the same bundles the reserve derivation
        read as demand on those cells.  Per bundle: the selected candidate
        by content uid (`topo_uid`) AND by its type spec (the pin that
        survives a pool whose loci moved — the uid is tried first, the
        spec is the fallback and is reported), the planner's layer per
        segment by name, and the abstract seat per segment as the
        width-wide window `[pos - w/2, pos + w/2]` (a POINT is refused by
        NUTS's fit — `first_fit` needs `hi - lo >= width` — while the
        width-wide window reproduces the position exactly); an unplaced
        segment hands down no seat (`-`).

        Returns (lines, notes, scope): `lines` are dicts (bid, net, type,
        uid, layers, seats, frame); None before a NUTS result."""
        if self.nuts_result is None or not self.bundles:
            return None
        from buda_script import reads_back   # the reader's own verdict
        notes = []
        inside, scope = self._top_plan_scope_insts(cells, notes)
        seats = {(t.bundle_id, t.seg_idx): t
                 for t in self.nuts_result.segments}
        names = self._make_layer_names()
        # A dogleg NUTS adopted is an APPENDED, geometry-mutated copy of the
        # selected candidate (edit.py `_adopt_doglegs`): its uid is in no
        # fresh pool, and its layers/seats index the split (two segments
        # more than the shape's), so a line written from it could not
        # replay — the type spec would land on the unsplit candidate and
        # the segment-count guard would drop every layer and seat (Codex
        # P1 on #939).  Hand down the PRE-split candidate instead: the split
        # keeps the original segment indices (the trunk is rewritten in
        # place as the left piece, the right piece and the jog are
        # appended), so the first `nseg` layers are the original's, and
        # every seat but the split trunk's reproduces; the trunk's is
        # withheld, since NUTS re-derives the dogleg from the same cycle.
        dl_slot = getattr(self, "_dogleg_slot", None) or {}
        dl_orig = getattr(self, "_dogleg_originals", None) or {}
        lines = []
        n_locked = n_inside = n_unplanned = n_dogleg = n_user = 0
        n_unspell, unspell = 0, []
        for w in self.bundles:
            b = w.input.original_bundle
            if getattr(w.hier, "locked", False):
                n_locked += 1
                continue
            frame = b.instances[0] if b.instances else ""
            if frame and any(frame == p or frame.startswith(p + "/")
                             for p in inside):
                n_inside += 1
                continue
            sel = w.plan.selected_topology_index
            nets = b.get_net_names()
            if not (0 <= sel < len(w.input.candidates)) or not nets:
                n_unplanned += 1
                continue
            t = w.input.candidates[sel]
            trunk_si = None            # the split trunk of an adopted dogleg
            if (b.id in dl_slot and sel == dl_slot[b.id]
                    and 0 <= dl_orig.get(b.id, -1) < len(w.input.candidates)
                    and dl_orig[b.id] != sel):
                split, t = t, w.input.candidates[dl_orig[b.id]]
                trunk_si = self._dogleg_trunk_index(t, split)
                n_dogleg += 1
            if t.type == "USER":
                # A hand-built candidate is in no fresh pool — regeneration
                # cannot produce it, and only a sidecar / `dump_user_ops`
                # replay rebuilds it — so a line naming it could not apply;
                # omitted and said rather than handed down as a pin that
                # silently re-plans (Codex P2 on #939).
                n_user += 1
                continue
            nseg = len(t.segments)
            sl = list(w.plan.seg_layers)
            layers = [(names.get(l, f"L{l}") if l >= 0 else "-")
                      for l in (sl + [-1] * nseg)[:nseg]]
            if not (reads_back("net:" + nets[0]) and reads_back(t.type)
                    and self._csv_reads_back(layers)
                    and not any(l >= 0 and names.get(l) == "-"
                                for l in sl[:nseg])):
                # The script grammar cannot spell this line: a selector
                # carrying whitespace AND both quote characters has no
                # escape (`quote_arg` returns it unchanged, and `pin_plan`
                # then reads it as two tokens and refuses the line), a
                # layer NAME carrying a comma splits into two on the CSV
                # `layers` field (the segment-count guard then drops every
                # layer and seat), and one named `-` reads as the
                # UNASSIGNED placeholder — so the entry is omitted and said
                # rather than written as a line the next session cannot
                # replay (Codex P2s on #939).  Asked of the READER, so
                # whatever it cannot read back is what is omitted.
                n_unspell += 1
                unspell.append(nets[0])
                continue
            seat = []
            for si in range(nseg):
                ts = seats.get((b.id, si))
                if si == trunk_si:
                    seat.append(None)          # the split trunk: re-derived
                elif ts is None or not ts.placed \
                        or ts.track_position != ts.track_position:
                    seat.append(None)
                else:
                    seat.append((ts.track_position - ts.width / 2.0,
                                 ts.track_position + ts.width / 2.0))
            lines.append({"bid": b.id, "net": nets[0], "type": t.type,
                          "uid": buda.topo_uid(t), "layers": layers,
                          "seats": seat, "frame": frame})
        if n_locked:
            notes.append(f"{n_locked} bottom-up copy/copies not handed "
                         f"down (a template is solved in its own frame)")
        if n_inside:
            notes.append(f"{n_inside} bundle(s) framed inside a scoped "
                         f"cell's instance not handed down (re-solved "
                         f"under the derived budget)")
        if n_unplanned:
            notes.append(f"{n_unplanned} bundle(s) with no selected "
                         f"candidate skipped")
        if n_user:
            notes.append(f"{n_user} bundle(s) on a hand-built USER candidate "
                         f"not handed down (regeneration cannot produce it; "
                         f"a sidecar or dump_user_ops replays it)")
        if n_unspell:
            shown = ", ".join(repr(n) for n in unspell[:3])
            more = f", +{n_unspell - 3} more" if n_unspell > 3 else ""
            notes.append(f"{n_unspell} bundle(s) whose net name, type or "
                         f"layer names the script grammar cannot spell not "
                         f"handed down ({shown}{more}: whitespace plus both "
                         f"quote characters has no escape, a comma in a "
                         f"layer name splits the layers field, so no "
                         f"pin_plan line reads back)")
        if n_dogleg:
            notes.append(f"{n_dogleg} dogleg-adopted bundle(s) handed down "
                         f"as the pre-split candidate with its layers, the "
                         f"split trunk's seat withheld (NUTS re-derives the "
                         f"dogleg)")
        return lines, notes, scope

    @staticmethod
    def _dogleg_trunk_index(orig, split):
        """Which of `orig`'s segments the dogleg split in two: the one
        whose orientation matches the appended right piece and whose
        along-extent is the union of the left piece's (rewritten in place
        at the same index) and the right piece's.  The split also re-ends
        every stub on the pieces' tracks, so a geometry diff is no
        discriminator; a stub's PERPENDICULAR coordinate — its seat — is
        unchanged, which is why the other seats reproduce.  None when the
        shape is not the trunk/piece/jog split `_adopt_doglegs` records."""
        nseg = len(orig.segments)
        if len(split.segments) != nseg + 2:
            return None

        def geom(seg):
            horiz = seg.start.y == seg.end.y
            a0, a1 = ((seg.start.x, seg.end.x) if horiz
                      else (seg.start.y, seg.end.y))
            return horiz, min(a0, a1), max(a0, a1)

        rh, rlo, rhi = geom(split.segments[nseg])
        for si in range(nseg):
            oh, olo, ohi = geom(orig.segments[si])
            lh, llo, lhi = geom(split.segments[si])
            if oh == lh == rh and min(llo, rlo) == olo \
                    and max(lhi, rhi) == ohi:
                return si
        return None

    @staticmethod
    def _csv_reads_back(items):
        """Whether a comma-joined list reads back as the same list through
        the reader: the joined token as ONE token (`reads_back`) and its
        comma split as these items — a name carrying a comma fails the
        second half, since the CSV grammar has no escape for it."""
        from buda_script import reads_back
        tok = ",".join(items)
        return reads_back(tok) and tok.split(",") == list(items)

    @staticmethod
    def _top_plan_line(l):
        """One `pin_plan` line for a derived entry — the grammar
        `cmd_pin_plan` reads back."""
        from buda_script import quote_arg   # the tokenizer's own inverse

        def _seat(s):
            return "-" if s is None else f"{fmt_pos(s[0])}:{fmt_pos(s[1])}"

        # The selector is quoted WHOLE ("net:foo bar") by the tokenizer's
        # inverse, which honours its rule — a quote counts only where a token
        # begins — and picks the delimiter the name does not contain, so
        # `foo"bar baz` is spelled with apostrophes (Codex P2s on #939).
        return (f"pin_plan {quote_arg('net:' + l['net'])} "
                f"{quote_arg(l['type'])} "
                f"uid {l['uid']} layers {quote_arg(','.join(l['layers']))} "
                f"seats {','.join(_seat(s) for s in l['seats'])}")

    def _report_top_plan(self, cells=None, path=""):
        """`derive_top_plan`: the plan as a table plus the `pin_plan` paste
        lines; `path` writes them for a later session to `source` (they are
        held until that session's `run_planner`)."""
        out = self._derive_top_plan(cells)
        if out is None:
            print("Error: derive_top_plan needs a NUTS result to read the "
                  "seats off (run_planner, then run_nuts)")
            return
        lines, notes, scope = out
        print("=== The top's plan (selection, layers, seats per globally "
              "planned bundle) ===")
        for n in notes:
            print(f"  {n}")
        n_seg = sum(len(l["seats"]) for l in lines)
        n_seated = sum(1 for l in lines for s in l["seats"] if s is not None)
        if lines:
            w_net = max(len(l["net"]) for l in lines)
            w_type = max(len(l["type"]) for l in lines)
            print(f"  {'bundle':>6}  {'net':<{w_net}}  {'type':<{w_type}}  "
                  f"layers  seats")
            for l in lines:
                print(f"  {l['bid']:>6}  {l['net']:<{w_net}}  "
                      f"{l['type']:<{w_type}}  "
                      f"{' '.join(l['layers'])}  "
                      f"{sum(1 for s in l['seats'] if s is not None)}"
                      f"/{len(l['seats'])}")
        print(f"  {len(lines)} bundle(s) handed down ({n_seg} segment(s), "
              f"{n_seated} seated)")
        text = [self._top_plan_line(l) for l in lines]
        if lines:
            print("  --- flow-text lines (source BEFORE run_planner; held "
                  "until it runs) ---")
            for t in text[:12]:
                print(f"  {t}")
            if len(text) > 12:
                print(f"  ... {len(text) - 12} more line(s)")
        if path:
            with open(path, "w") as f:
                f.write("# derive_top_plan: the globally planned bundles' "
                        "selection, layers and abstract seats; source "
                        "before run_planner (hier) — held until it runs\n")
                f.write("# scope: " + (",".join(scope) if scope else "(none)")
                        + "\n")
                f.write(f"# bundles: {len(lines)}\n")
                for t in text:
                    f.write(t + "\n")
            print(f"  written to {path}"
                  + ("" if text else " (header only — nothing planned)"))
