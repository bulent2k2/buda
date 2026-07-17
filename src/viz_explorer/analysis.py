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

"""TopologyExplorer mixin — Slide/conn/analysis readers over the shown topology.

Split out of buda_viz.py (see viz_explorer/__init__.py); methods run on
the composed class and share its state via self."""
import json
import math
import os
import re
import sys
from datetime import datetime

import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.collections import PatchCollection, LineCollection
from matplotlib.widgets import Button

import buda as ic
from ui_state import ViewState              # noqa: F401
from viz_common import *                    # noqa: F401,F403
import viz_window

class ExplorerAnalysisMixin:

    # ------------------------------------------------------------------

    def _build_conn_topo(self, topo):
        ct = ic.ConnTopology()
        ct.build(topo, self.fp)
        return ct


    # The dogleg pass pins per-segment net_pull and slide windows on the plan
    # (ConnTopology would recompute them wrongly on the split topology).  Those
    # overrides are indexed by the SELECTED topology's segments, so honor them
    # only when the explorer is showing that topology — then the view matches
    # what NUTS actually used.
    def _show_overrides(self):
        # Only the SELECTED topology, and only while an override array still
        # matches that topology's segment count — a later run_planner can replace
        # the selected topology without refreshing these, so a size mismatch means
        # the overrides are stale and must be ignored (as build_nuts_maps does).
        return self.idx == self.wrapper.plan.selected_topology_index


    def _n_segs(self):
        return len(self.topos[self.idx].segments)


    def _seg_net_pull(self, cs, ci):
        snp = getattr(self.wrapper.plan, 'seg_net_pull', None)
        if self._show_overrides() and snp and len(snp) == self._n_segs():
            if snp[ci] != -2147483648:
                return snp[ci]
        return cs.net_pull


    def _seg_slide(self, cs, ci):
        # A staged edit-session slide window wins while editing, so the slide
        # band and its bound labels live-update as 'W' refines the window.
        if self._edit_topo is not None and ci in self._edit_slide:
            return self._edit_slide[ci]
        slo = getattr(self.wrapper.plan, 'seg_slide_lo', None)
        shi = getattr(self.wrapper.plan, 'seg_slide_hi', None)
        if (self._show_overrides() and slo and shi and len(slo) == self._n_segs()
                and len(shi) == self._n_segs() and not math.isnan(slo[ci])):
            return slo[ci], shi[ci]
        return cs.perp_lo, cs.perp_hi


    def _is_dogleg_seg(self, ci):
        # A dogleg piece/jog carries a pinned slide window; such a segment must
        # display at its NOMINAL position (the two pieces share a slide range, so
        # the range-centre display would collapse them and hide the dogleg step).
        slo = getattr(self.wrapper.plan, 'seg_slide_lo', None)
        return bool(self._show_overrides() and slo and len(slo) == self._n_segs()
                    and not math.isnan(slo[ci]))


    def _centered_perp(self, cs) -> float:
        """Centered display perp position within the slide interval.

        When both perp_lo and perp_hi are finite, returns the interval midpoint
        so the drawn segment line never obscures either boundary.  Falls back to
        the nominal perp_pos when one or both ends are unconstrained.
        """
        lo, hi = cs.perp_lo, cs.perp_hi
        if abs(lo) < _UNCONSTRAINED and abs(hi) < _UNCONSTRAINED:
            return (lo + hi) / 2.0
        return float(cs.perp_pos)


    def _seg_passthru_names(self, cs, tapped):
        """Split the blocks `cs` geometrically crosses (solid rects — each real
        rect of a multi-rect block, the union bbox otherwise) WITHOUT tapping
        into `(passthru, otc_over)`: `passthru` = the BUNDLE's blocks — the
        coverage/feedthru-relevant set; `otc_over` = unrelated floorplan blocks
        the wire merely flies over (normal over-the-cell routing on TOP
        layers).  Crossing means INTERIOR overlap (perp strictly inside, along
        overlap of positive length) — riding a face line or abutting at a
        single point does not count.  Mirrors reports' _seg_spans_block, at
        the nominal perp."""
        def crosses(x1, y1, x2, y2):
            if cs.horiz:   # perp = y, along = x
                return (y1 < cs.perp_pos < y2
                        and cs.along_lo < x2 and cs.along_hi > x1)
            return (x1 < cs.perp_pos < x2
                    and cs.along_lo < y2 and cs.along_hi > y1)
        contract = (set(self._shown_topo().connected_block_names)
                    | self._bundle_busterm_names())
        passt, otc = [], []
        for name, ubbox in self.fp.get_all_blocks():
            if name in tapped:
                continue
            rects = self.fp.get_block_rects(name)   # [] for single-rect blocks
            if rects:
                hit = any(crosses(x1, y1, x2, y2) for (x1, y1, x2, y2) in rects)
            else:
                hit = crosses(ubbox.x1, ubbox.y1, ubbox.x2, ubbox.y2)
            if hit:
                (passt if name in contract else otc).append(name)
        return passt, otc


    def _seg_eff_lid(self, ci):
        """The segment's EFFECTIVE layer id — the same precedence the drawing
        and `+`/`-` restyle paths resolve with: pinned selection override →
        planner assignment (when showing the planned candidate) → generation
        hint."""
        topo = self._shown_topo()
        sel = self._find_selection()
        if self._current_is_selected() and sel and 'seg_layers' in sel:
            pinned = sel['seg_layers']
            if len(pinned) == len(topo.segments):
                return pinned[ci]
        if (self.idx == self.wrapper.plan.selected_topology_index
                and len(self.wrapper.plan.seg_layers) == len(topo.segments)):
            return self.wrapper.plan.seg_layers[ci]
        return topo.segments[ci].layer_hint


    def _seg_conn_line(self, cs, ci):
        """Line 3 of the segment info banner: net-pull always, then busterm
        taps / pass-through blocks / connected segs when present."""
        pull = self._seg_net_pull(cs, ci)
        bts = list(dict.fromkeys(c.block_name for c in cs.conns
                                 if c.kind == ic.SegConnKind.BUSTERM))
        sgs = list(dict.fromkeys(str(c.seg_idx) for c in cs.conns
                                 if c.kind == ic.SegConnKind.SEG))
        p_dir = '→hi' if pull > 0 else '→lo' if pull < 0 else 'none'
        parts = [f"pull={p_dir}({pull})"]
        if bts:
            parts.append("busterms: " + ",".join(bts))
        passt, otc = self._seg_passthru_names(cs, set(bts))
        if passt:
            parts.append("passthru: " + ",".join(passt))
        if otc:
            # On a non-TOP effective layer a LEAF footprint is an implicit
            # keepout — flag the crossing (`low-cross`) instead of calling it
            # benign over-the-cell routing; containers stay transparent.
            lid = self._seg_eff_lid(ci)
            low = (self.layer_stack is not None
                   and self.layer_stack.has_layer(lid)
                   and not self.layer_stack.is_top(lid))
            lowx = [n for n in otc
                    if low and not self.fp.is_container(n)]
            rest = [n for n in otc if n not in lowx]
            if rest:
                parts.append("otc-over: " + ",".join(rest))
            if lowx:
                parts.append("low-cross: " + ",".join(lowx))
        if sgs:
            parts.append("segs: " + ",".join(sgs))
        return " · ".join(parts)


    def _bundle_busterm_names(self):
        """The bundle's busterm block set — the union of seg_busterms taps
        across this bundle's candidates (the generator's own tap record; every
        valid candidate taps exactly the bundle's endpoint blocks, per the
        coverage gate).  Falls back to the shown topo's connected_block_names
        when no candidate carries taps (a fully hand-built pool)."""
        names = set()
        for t in self.wrapper.input.candidates:
            for eps in t.seg_busterms.values():
                for bt in eps:
                    if bt is not None:
                        names.add(bt.block_name)
        if not names:
            names = set(self._shown_topo().connected_block_names)
        return names


    def _bundle_hanan_grid(self):
        """The per-bundle Hanan grid GENERATION actually uses for this bundle
        (TopologyGenerator::generate_npin): edges of the bundle's busterm
        rects — each individual rect of a multi-rect block, the orig bbox
        otherwise — plus every keepout's edges.  The full-design
        fp.get_hanan_grid() adds every unrelated block's lines, which is
        noise when editing one bundle."""
        xs, ys = set(), set()
        for n in self._bundle_busterm_names():
            rects = self.fp.get_block_rects(n)
            if not rects:
                rects = [self.fp.get_block_bounds(n)]
            for r in rects:
                xs.update((r.x1, r.x2)); ys.update((r.y1, r.y2))
        for koz in self.fp.get_keepout_zones():
            xs.update((koz.bbox.x1, koz.bbox.x2))
            ys.update((koz.bbox.y1, koz.bbox.y2))
        # Out-of-bounds DETOUR lines: one line a margin beyond each extreme so a
        # U-shape / OOB detour trunk has a visible snap target (T/Y snap to this
        # grid; without these the cursor always snaps back to a busterm edge and
        # an OOB trunk can't be placed).
        for s in (xs, ys):
            if len(s) >= 2:
                lo, hi = min(s), max(s)
                m = max(int(round((hi - lo) * 0.25)), 1)
                s.update((lo - m, hi + m))
        # Temporary lines dropped with 'G' while a trunk is armed — the escape
        # for an EMPTY channel between blocks (no edge line to snap to there).
        xs.update(getattr(self, '_edit_grid_x', ()))
        ys.update(getattr(self, '_edit_grid_y', ()))
        return sorted(xs), sorted(ys)
