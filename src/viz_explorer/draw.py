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

"""TopologyExplorer mixin — The candidate renderer (_draw) and its slide/busterm overlays.

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
import viz_common as vc
import viz_window

class ExplorerDrawMixin:

    @staticmethod
    def _fmt_cost(v):
        """Compact cost number: integer-ish values without a decimal tail, small
        ones to 3 sig-figs — costs span WL (thousands) and soft terms (<1)."""
        av = abs(v)
        if av >= 100:
            return f"{v:.0f}"
        if av >= 1:
            return f"{v:.2f}"
        return f"{v:.3f}"

    def _fit_title_fontsize(self, title):
        """A title font size (7.5–12pt) whose widest line fits the figure width.

        A char-count estimate (~0.55pt of width per character per point of font
        for the default sans face), so a long title — many segments, a
        group-pinned super-candidate, the debug cost line — shrinks to fit
        rather than clipping at the window edges; a short title stays at 12pt.
        Robust with no renderer (used on the first draw); `_shrink_title_to_fit`
        refines it once the canvas can measure."""
        longest = max((len(ln) for ln in title.split('\n')), default=0)
        if longest <= 0:
            return 12.0
        fig_w_in = self.fig.get_size_inches()[0]
        max_fs = (0.96 * fig_w_in * 72.0) / (0.55 * longest)
        return max(7.5, min(12.0, max_fs))

    def _shrink_title_to_fit(self, title_artist):
        """Fine-tune the title font using the REAL rendered width when the canvas
        has a renderer (Agg/TkAgg).  Scales the size by the width ratio so the
        widest line fits ~98% of the figure width — one linear step, floored at
        7pt.  A no-op (leaving the char-count estimate) when no renderer is
        available or measurement fails."""
        try:
            renderer = self.fig.canvas.get_renderer()
        except Exception:
            return
        try:
            avail = self.fig.bbox.width * 0.98
            bb = title_artist.get_window_extent(renderer=renderer)
        except Exception:
            return
        if bb.width > avail and bb.width > 0:
            new_fs = max(7.0, title_artist.get_fontsize() * avail / bb.width)
            title_artist.set_fontsize(new_fs)

    def _refit_title_on_resize(self, _event=None):
        """Re-fit the title font to the CURRENT window size (resize_event hook).

        `_draw` fits the title against the figure size at draw time, but a
        resize does not re-run `_draw` (the shared home-fit tracker only refits
        the data view).  Re-run the same fit against the live title text — from
        scratch, so a narrowed window shrinks it and a widened one restores it
        toward 12pt.  The resize's own redraw renders the new size."""
        t = getattr(self.ax, 'title', None)
        if t is None:
            return
        text = t.get_text()
        if not text:
            return
        t.set_fontsize(self._fit_title_fontsize(text))
        self._shrink_title_to_fit(t)

    def _debug_cost_title(self):
        """The debug view's second title line: this candidate's planner cost, its
        cost-rank (position in the increasing-cost stepping order), and the cost
        components.  Empty string when the `debug` flag is off.

        REAL cost (post-`run_planner`): `cost=<total> [rank R/N] = seg <seg_cost>
        + wl <wl_term>`, seg_cost being the max-over-segments segment score
        (congestion + span + layer terms).  Pre-plan: `cost≈WL <wl> (intrinsic;
        congestion unknown until run_planner) [rank R/N]`."""
        res = self._debug_costs()
        if not res:
            return ""
        mapping, is_real = res
        cc = mapping.get(self.idx)
        rank = self._cost_rank(self.idx)
        rank_str = (f"  rank {rank[0]}/{rank[1]}"
                    if rank and rank[0] is not None else "")
        if cc is None:
            return f"[debug] cost=n/a{rank_str}"
        if not is_real:
            return (f"[debug] cost≈WL {self._fmt_cost(cc.wl_term)} "
                    f"(intrinsic; congestion unknown until run_planner)"
                    f"{rank_str}")
        feas = "" if cc.feasible else "  ⚠ infeasible"
        return (f"[debug] cost={self._fmt_cost(cc.total)}{rank_str}"
                f"  =  seg {self._fmt_cost(cc.seg_cost)}"
                f"  +  wl {self._fmt_cost(cc.wl_term)}{feas}")

    def _debug_seg_cost_line(self):
        """The per-segment congestion-cost line for the j/k selected-segment
        panel (debug view only): the cost the CURRENTLY SELECTED segment
        contributes, broken into its terms.  Empty string when the debug flag is
        off, no segment is selected, or the segment has no cost row (pre-plan —
        no per-segment breakdown exists)."""
        res = self._debug_costs()
        if not res or self.sidx < 0:
            return ""
        mapping, is_real = res
        cc = mapping.get(self.idx)
        if cc is None or not is_real:
            return ""
        sc = next((s for s in cc.segs if s.seg_idx == self.sidx), None)
        if sc is None:
            return ""
        parts = [f"cong {self._fmt_cost(sc.cong)}"]
        # Only show the terms that are non-zero, to keep the line short.
        for label, val in (("span", sc.span), ("non_top", sc.non_top),
                           ("bal", sc.balance), ("hgt", sc.height),
                           ("peak", sc.peak)):
            if abs(val) > 1e-9:
                parts.append(f"{label} {self._fmt_cost(val)}")
        return (f"[debug] seg cost {self._fmt_cost(sc.total)}  =  "
                + " + ".join(parts))

    def _draw_busterm_markers(self, topo, ct, viz_lw):
        """Draw a diamond at every busterm connection point.

        Gated on the shared ui_state.busterms (the "Terminals" toggle) so the
        setting carries over from the main BUDA viz, like Blocks and Hanan.
        """
        if not self.ui_state.busterms:
            return
        ax = self.ax
        msz = viz_lw * 1.1 + 3

        for ci, (raw_seg, cs) in enumerate(zip(topo.segments, ct.segs())):
            if ci in getattr(self, '_hidden_seg', ()):   # layer hidden in main viz
                continue
            col = _LAYER_COLOR.get(raw_seg.layer_hint, '#888888')
            display_perp = self._centered_perp(cs)
            for conn in cs.conns:
                if conn.kind != ic.SegConnKind.BUSTERM:
                    continue
                if cs.horiz:
                    px, py = conn.at_pos, display_perp
                    dx = -8 if px <= (cs.along_lo + cs.along_hi) / 2 else +8
                    dy = 0
                    ha = 'right' if dx < 0 else 'left'
                    va = 'center'
                else:
                    px, py = display_perp, conn.at_pos
                    dx = 0
                    dy = -8 if py <= (cs.along_lo + cs.along_hi) / 2 else +8
                    ha = 'center'
                    va = 'top' if dy < 0 else 'bottom'

                ax.plot(px, py, 'D',
                        color=col, markersize=msz,
                        markeredgecolor='white', markeredgewidth=1.2,
                        zorder=15)
                ax.text(px + dx, py + dy, conn.block_name,
                        fontsize=7, color=col, fontweight='bold',
                        ha=ha, va=va, zorder=16,
                        bbox=dict(boxstyle='round,pad=0.15', fc='white',
                                  ec='none', alpha=0.7))


    def _draw_pin_preview(self, topo):
        """Pin-span mode: bold-outline each picked busterm block, mark each
        picked grid line (a block-less anchor reaching beyond the busterms),
        preview the hovered grid line, and preview the resulting trunk span (a
        thick line along the selected segment's perp between the anchor extent)."""
        ax = self.ax
        col = '#d62728'   # red — the 'pin' accent
        seg_idx = self._trunk_pin_seg
        if not (0 <= seg_idx < len(topo.segments)):
            return
        seg = topo.segments[seg_idx]
        horiz = (seg.start.y == seg.end.y)
        x0, x1 = ax.get_xlim()
        y0, y1 = ax.get_ylim()
        for name in self._trunk_pin_set:
            r = self.fp.get_block_bounds(name)
            ax.add_patch(patches.Rectangle(
                (r.x1, r.y1), r.x2 - r.x1, r.y2 - r.y1, fill=False,
                edgecolor=col, linewidth=2.4, zorder=9))
        # Picked grid lines: a full-width/height dashed line at each anchor
        # coordinate (x for an H trunk, y for a V trunk).
        for c in self._trunk_pin_grid:
            if horiz:
                ax.plot([c, c], [y0, y1], color=col, lw=1.6, ls='--',
                        alpha=0.9, zorder=9)
            else:
                ax.plot([x0, x1], [c, c], color=col, lw=1.6, ls='--',
                        alpha=0.9, zorder=9)
        # Hovered grid line the next click would pick (orange, like trunk arm).
        hv = self._trunk_pin_hover
        if hv is not None:
            hcol = '#ff7f0e'
            if horiz:
                ax.plot([hv, hv], [y0, y1], color=hcol, lw=1.4, ls=':',
                        alpha=0.9, zorder=8)
            else:
                ax.plot([x0, x1], [hv, hv], color=hcol, lw=1.4, ls=':',
                        alpha=0.9, zorder=8)
        span = self._pin_span_of(self._trunk_pin_set, self._trunk_pin_grid,
                                 horiz, self._trunk_pin_seg)
        if span is None:
            return
        lo, hi = span
        perp = seg.start.y if horiz else seg.start.x
        if horiz:
            ax.plot([lo, hi], [perp, perp], color=col, lw=3.0, alpha=0.85,
                    solid_capstyle='butt', zorder=9)
        else:
            ax.plot([perp, perp], [lo, hi], color=col, lw=3.0, alpha=0.85,
                    solid_capstyle='butt', zorder=9)

    def _draw_trunk_preview(self):
        """Highlight the Hanan cell the armed trunk (T/Y) is hovering: a
        vertical column for a V trunk (Y), a horizontal row for an H trunk (T),
        with the dashed centerline at the snapped coordinate.  The slab is the
        FULL cell between the two bounding bundle-grid lines — exactly the
        initial slide range the placed trunk is seeded with."""
        ax = self.ax
        p = self._trunk_hover
        horiz = self._trunk_mode
        x0, x1 = ax.get_xlim()
        y0, y1 = ax.get_ylim()
        col = '#ff7f0e'   # orange — distinct from every layer colour
        xs, ys = self._bundle_hanan_grid()
        g_lo, g_hi = self._cell_around(p, ys if horiz else xs)
        lo = g_lo if g_lo is not None else p - (x1 - x0) * 0.01
        hi = g_hi if g_hi is not None else p + (x1 - x0) * 0.01
        if horiz:                                   # H trunk → horizontal row
            ax.add_patch(patches.Rectangle(
                (x0, lo), x1 - x0, hi - lo, facecolor=col, alpha=0.16,
                linewidth=0, zorder=8))
            ax.plot([x0, x1], [p, p], color=col, lw=2.0, ls='--',
                    alpha=0.9, zorder=8)
        else:                                       # V trunk → vertical column
            ax.add_patch(patches.Rectangle(
                (lo, y0), hi - lo, y1 - y0, facecolor=col, alpha=0.16,
                linewidth=0, zorder=8))
            ax.plot([p, p], [y0, y1], color=col, lw=2.0, ls='--',
                    alpha=0.9, zorder=8)

    def _draw_slide_spans(self, topo, ct):
        """Overlay slide-range bands on the current topology."""
        ax = self.ax
        xs, ys = self.fp.get_hanan_grid()

        margin = max((xs[-1] - xs[0]) if len(xs) > 1 else 50,
                     (ys[-1] - ys[0]) if len(ys) > 1 else 50) * 0.25
        x_lo_v = (xs[0]  if xs else 0)  - margin
        x_hi_v = (xs[-1] if xs else 100) + margin
        y_lo_v = (ys[0]  if ys else 0)  - margin
        y_hi_v = (ys[-1] if ys else 100) + margin

        def clamp_x(v): return x_lo_v if v < -_UNCONSTRAINED else (x_hi_v if v > _UNCONSTRAINED else v)
        def clamp_y(v): return y_lo_v if v < -_UNCONSTRAINED else (y_hi_v if v > _UNCONSTRAINED else v)

        cs_list = list(ct.segs())
        cs_map  = {j: cs_list[j] for j in range(len(cs_list))}

        # Emphasize the SELECTED segment's slide band + bounds and dim the rest,
        # so overlapping bands are disambiguated while stepping with j/k — on
        # ANY candidate (pinned or not, edit session or not), matching the
        # segment halo's gate.  hi == -1: no selection, all bands normal.  An
        # index outside the shown topology (stale across a view change) counts
        # as no selection rather than dimming everything.
        hi = self.sidx if 0 <= self.sidx < len(cs_list) else -1

        for ci, (raw_seg, cs) in enumerate(zip(topo.segments, cs_list)):
            if ci in getattr(self, '_hidden_seg', ()):   # layer hidden in main viz
                continue
            if hi == -1:
                band_a, line_a, line_w = 0.10, 0.7, 0.9
            elif ci == hi:
                band_a, line_a, line_w = 0.30, 1.0, 2.0   # the selected segment
            else:
                band_a, line_a, line_w = 0.04, 0.25, 0.7  # dimmed
            col = _LAYER_COLOR.get(raw_seg.layer_hint, '#888888')
            slide_lo, slide_hi = self._seg_slide(cs, ci)   # NUTS override if any

            # Extend the along range to cover the perp intervals of perpendicular
            # stubs connected at our endpoints.  A trunk's band should span the
            # full distance the trunk may need to reach, not just its nominal span.
            ext_lo, ext_hi = cs.along_lo, cs.along_hi
            for conn in cs.conns:
                if conn.kind != ic.SegConnKind.SEG or not conn.is_endpoint:
                    continue
                other = cs_map.get(conn.seg_idx)
                if other is None or other.horiz == cs.horiz:
                    continue  # same-direction connection — skip
                if abs(other.perp_lo) < _UNCONSTRAINED:
                    ext_lo = min(ext_lo, other.perp_lo)
                if abs(other.perp_hi) < _UNCONSTRAINED:
                    ext_hi = max(ext_hi, other.perp_hi)

            if cs.horiz:
                band_y0 = clamp_y(slide_lo)
                band_y1 = clamp_y(slide_hi)
                if band_y0 >= band_y1:
                    continue
                ax.add_patch(patches.Rectangle(
                    (ext_lo, band_y0), ext_hi - ext_lo, band_y1 - band_y0,
                    linewidth=0, facecolor=col, alpha=band_a, zorder=3))
                for y_b, label in ((slide_lo, 'lo'), (slide_hi, 'hi')):
                    if abs(y_b) < _UNCONSTRAINED:
                        ax.plot([ext_lo, ext_hi], [y_b, y_b],
                                color=col, linewidth=line_w, linestyle=':', alpha=line_a, zorder=4)
                        ax.text((ext_lo + ext_hi) / 2, y_b, f' {y_b:.0f}',
                                fontsize=6, color=col, va='bottom' if label == 'lo' else 'top',
                                ha='center', zorder=5, alpha=0.85)
            else:
                band_x0 = clamp_x(slide_lo)
                band_x1 = clamp_x(slide_hi)
                if band_x0 >= band_x1:
                    continue
                ax.add_patch(patches.Rectangle(
                    (band_x0, ext_lo), band_x1 - band_x0, ext_hi - ext_lo,
                    linewidth=0, facecolor=col, alpha=band_a, zorder=3))
                for x_b, label in ((slide_lo, 'lo'), (slide_hi, 'hi')):
                    if abs(x_b) < _UNCONSTRAINED:
                        ax.plot([x_b, x_b], [ext_lo, ext_hi],
                                color=col, linewidth=line_w, linestyle=':', alpha=line_a, zorder=4)
                        ax.text(x_b, (ext_lo + ext_hi) / 2, f' {x_b:.0f}',
                                fontsize=6, color=col, va='center',
                                ha='left' if label == 'lo' else 'right', zorder=5, alpha=0.85)


    def _draw_slide_mark_line(self, ct):
        """The 'W' refine's echo marker: the FIRST captured slide bound as a
        transient dashed line at its EFFECTIVE coordinate — re-snapped or raw
        per the current gridded/gridless sub-mode, exactly what the banner
        echoes — drawn across the marked segment's along extent so the user
        sees where the bound landed before committing the second press.
        Lives and dies with `_edit_slide_mark` (second `W` applies, `w`
        clears, session close discards)."""
        cs_list = list(ct.segs())
        si, raw = self._edit_slide_mark
        if not (0 <= si < len(cs_list)):
            return
        cs = cs_list[si]
        coord = self._slide_snap(raw, cs.horiz)
        col = _LAYER_COLOR.get(self._edit_topo.segments[si].layer_hint,
                               '#888888')
        mode = "grid" if self._edit_slide_grid else "free"
        ext = max((cs.along_hi - cs.along_lo) * 0.15, 1.0)
        a_lo, a_hi = cs.along_lo - ext, cs.along_hi + ext
        if cs.horiz:
            self.ax.plot([a_lo, a_hi], [coord, coord], color=col,
                         linewidth=1.6, linestyle='--', alpha=0.95, zorder=6)
            self.ax.text(a_hi, coord, f' W:{coord:.0f} [{mode}]',
                         fontsize=7, color=col, va='center', ha='left',
                         zorder=6)
        else:
            self.ax.plot([coord, coord], [a_lo, a_hi], color=col,
                         linewidth=1.6, linestyle='--', alpha=0.95, zorder=6)
            self.ax.text(coord, a_hi, f' W:{coord:.0f} [{mode}]',
                         fontsize=7, color=col, va='bottom', ha='center',
                         zorder=6)


    def _redraw_topo(self):
        # We trigger a fig_redraw so the UI state updates correctly.
        self.fig_redraw()

            # We don't automatically raise the main window here anymore,
            # as it snatches focus from the TopologyExplorer.
            # User can use 'v' / 'cmd+1' / 'ctrl+1' or just click the window.

    def fig_redraw(self):
        self._draw()


    def _draw(self):
        ax = self.ax
        if not getattr(self, '_autoscale_needed', True):
            curr_xlim = ax.get_xlim()
            curr_ylim = ax.get_ylim()
        else:
            curr_xlim = None
            curr_ylim = None
        ax.clear()
        n   = len(self.topos)
        nb  = len(self.wrappers)
        bid = self.wrapper.input.original_bundle.id
        _w  = self.wrappers[self.bidx]
        if hasattr(_w, 'path') and _w.path:
            bus_label = f"/{_w.path} "
        elif nb > 1:
            bus_label = f"bundle {self.bidx + 1}/{nb} · "
        else:
            bus_label = ""

        topo = self._shown_topo()
        wl   = topo.estimated_wirelength

        # Use centralized selection check
        sel = self._find_selection()
        is_current_selection = self._current_is_selected()
        has_any_sel = (sel is not None
                       or getattr(self.wrapper.input, 'topology_pinned', False))

        # Planner is active only if the planner actually ran (it assigns
        # per-segment layers) and chose this topo. select_topology(ies) also sets
        # selected_topology_index, so that alone would mislabel a script pin as a
        # planner choice when run_planner was never called.
        is_planner_active = (self.wrapper.plan is not None and
                             self.wrapper.plan.selected_topology_index == self.idx and
                             len(self.wrapper.plan.seg_layers) > 0)

        if is_planner_active and is_current_selection:
            sel_badge = "  ★ PINNED & PLANNER SELECTED"
        elif is_planner_active:
            sel_badge = "  ★ PLANNER SELECTED"
        elif is_current_selection:
            sel_badge = "  ★ PINNED"
        else:
            sel_badge = ""

        # Tuning buttons are only visible when the current topo is selected/pinned.
        # This allows per-segment layer overrides to be persisted.
        if hasattr(self, '_bax2'):
            for bax in self._bax2:
                bax.set_visible(is_current_selection)

        # ── Update selection button states ──
        # Set via style_button (btn.color), not ax.set_facecolor alone, so the
        # state color survives Button's motion-driven repaints.
        if is_current_selection:
            self._btn_select.label.set_text('★  Pinned')
            style_button(self._btn_select, '#aadd88')
        elif is_planner_active:
            self._btn_select.label.set_text('★  Pin Planner Choice')
            style_button(self._btn_select, '#cceeff')
        else:
            self._btn_select.label.set_text('★  Pin Topo')
            style_button(self._btn_select, '#f0f0f0')
        style_button(self._btn_deselect, '#ffbbaa' if has_any_sel else '#f0f0f0')

        # ── Axes border: gold for pinned, blue for planner, subtle grey otherwise ──
        if is_planner_active and is_current_selection:
            border_col, border_lw = '#BDB76B', 3.5  # Dark Khaki
        elif is_planner_active:
            border_col, border_lw = '#4682B4', 3.5  # Steel Blue
        elif is_current_selection:
            border_col, border_lw = '#FFD700', 3.5  # Gold
        else:
            border_col, border_lw = '#cccccc', 0.8
        for spine in ax.spines.values():
            spine.set_edgecolor(border_col)
            spine.set_linewidth(border_lw)

        if self._edit_topo is not None or self._edit_msg:
            # Edit banner: a boxed status chip INSIDE the axes at top-left —
            # the old spot just above the axes (0.01, 1.01) shared the title
            # band and a long verdict overlapped the header.  Top-left is free
            # (the legend sits upper-right); the box keeps it readable over
            # design content.
            ax.text(0.01, 0.985,
                    self._edit_msg or "EDIT",
                    transform=ax.transAxes, fontsize=9, color='#b03030',
                    va='top', ha='left', zorder=60, clip_on=False,
                    bbox=dict(boxstyle='round,pad=0.35', facecolor='#fff4f4',
                              edgecolor='#b03030', linewidth=0.8, alpha=0.92))
            if self._edit_topo is None:
                self._edit_msg = ""     # one-shot after the session closes

        ct = self._build_conn_topo(topo)
        cs_list = list(ct.segs())

        # Determine display geometry for segments — width proportional to
        # bundle width but capped thin (4.5pt) so a wide bus's fat line cannot
        # bury the slide bands and dotted nominals it is drawn over.  (The thin
        # cap used to be edit-mode-only; it now applies to the regular view
        # too — the slide indicators stay readable everywhere.)
        viz_lw = min(3.0 + math.log2(1 + self.wrapper.input.width) * 1.5, 4.5)
        actual_lids = []
        # A segment is "selected" (highlighted, info line, slide-marker
        # emphasis) whenever one is picked — on ANY shown candidate, pinned or
        # not (j/k stepping is a browsing tool, not a commitment; it originally
        # required a pin, then an edit session, now nothing).
        sel_active = (self.sidx != -1)

        # ── Pre-compute display geometry for all segments ──────────────────
        # Minimum pull-arrow length in data units (prevents invisible arrows on
        # tiny intervals and provides a direction indicator for one-side-constrained
        # segments where there is no finite interval width to derive from).
        _MIN_ARROW = 20

        # Pass A: centered perp position + pull-arrow length per segment.
        #
        # net_pull (from ConnTopology / NUTS override) is computed in C++:
        #   > 0  more connected-stub anchors lie above perp_pos → slide up/right
        #   < 0  more anchors lie below perp_pos                → slide down/left
        #   = 0  balanced or no stub connections                → no preferred direction
        #
        # Display at interval centre when both bounds are finite; at perp_pos otherwise.
        _draw_perp = []
        _pull_len  = []
        for ci, cs in enumerate(cs_list):
            lo, hi  = self._seg_slide(cs, ci)      # NUTS slide override if any
            net_pull = self._seg_net_pull(cs, ci)  # NUTS net_pull override if any
            lo_ok   = abs(lo) < _UNCONSTRAINED
            hi_ok   = abs(hi) < _UNCONSTRAINED
            if self._is_dogleg_seg(ci):
                # Show a dogleg piece/jog at its nominal position so the step is
                # visible (its two pieces share a slide range).
                raw = topo.segments[ci]
                dp  = float(raw.start.y if cs.horiz else raw.start.x)
            else:
                dp  = (lo + hi) / 2.0 if (lo_ok and hi_ok) else float(cs.perp_pos)

            if net_pull != 0:
                interval_w = (hi - lo) if (lo_ok and hi_ok) else 0.0
                plen = max(interval_w / 4.0, float(_MIN_ARROW)) * (1.0 if net_pull > 0 else -1.0)
            else:
                plen = 0.0

            _draw_perp.append(dp)
            _pull_len.append(plen)

        # Pass B: snap along endpoints to connected segments' display perp,
        # so a segment and the partner meeting it there are drawn touching.
        # The rule itself lives in viz_common.snap_endpoint_extents — shared
        # with the two web renderers, which must agree with this one (see that
        # docstring and test_web_displaygeom.py).
        _draw_lo, _draw_hi = vc.snap_endpoint_extents(
            [cs.along_lo for cs in cs_list],
            [cs.along_hi for cs in cs_list],
            [[(c.seg_idx, c.at_pos) for c in cs.conns
              if c.kind == ic.SegConnKind.SEG] for cs in cs_list],
            _draw_perp)
        # ──────────────────────────────────────────────────────────────────

        # Resolve each segment's layer once (pinned → planned → hint) and note
        # which are hidden by the main viz's layer toggles, so EVERY drawing
        # pass below — segments, slide spans, busterm markers — skips the same
        # set (a layer turned off in the main viz shows no artifacts here).
        def _resolved_lid(i):
            # During a TopoEdit session the WORKING COPY governs the display:
            # a '+'/'-' layer edit changes seg.layer_hint (what commit
            # persists), so honor it over the stale sidecar/planner layers
            # (audit P7-03).
            if getattr(self, '_edit_topo', None) is not None:
                return topo.segments[i].layer_hint
            if is_current_selection and sel and 'seg_layers' in sel:
                pinned = sel['seg_layers']
                if len(pinned) == len(topo.segments):
                    return pinned[i]
            if (is_planner_active
                    and len(self.wrapper.plan.seg_layers) == len(topo.segments)):
                return self.wrapper.plan.seg_layers[i]
            return topo.segments[i].layer_hint

        self._hidden_seg = {
            i for i in range(len(topo.segments))
            if self._layer_visible is not None
            and not self._layer_visible.get(_resolved_lid(i), True)
        }

        _sel_lid = _sel_is_h = None   # selected segment's layer + orientation
        for i, seg in enumerate(topo.segments):
            lid = _resolved_lid(i)

            # Layer hidden in the main viz → skip it (and its legend entry).
            if i in self._hidden_seg:
                continue
            actual_lids.append(lid)

            col      = _LAYER_COLOR.get(lid, '#888888')
            cs       = cs_list[i]
            dp       = _draw_perp[i]
            dlo, dhi = _draw_lo[i], _draw_hi[i]

            if cs.horiz:
                x0, y0, x1, y1 = dlo, dp, dhi, dp
            else:
                x0, y0, x1, y1 = dp, dlo, dp, dhi

            # Highlight selected segment; dim others
            seg_alpha = 1.0
            if sel_active:
                if i == self.sidx:
                    _sel_lid, _sel_is_h = lid, cs.horiz
                    ax.plot([x0, x1], [y0, y1],
                            color='white', linewidth=viz_lw + 4,
                            alpha=0.6, solid_capstyle='round', zorder=9)
                else:
                    seg_alpha = 0.3

            ax.plot([x0, x1], [y0, y1],
                    color=col, linewidth=viz_lw,
                    solid_capstyle='round', zorder=10, alpha=seg_alpha)
            ax.plot(x0, y0, 'o',
                    color=col, markersize=viz_lw * 0.6, zorder=11, alpha=seg_alpha)
            ax.plot(x1, y1, 'o',
                    color=col, markersize=viz_lw * 0.6, zorder=11, alpha=seg_alpha)

            # Pull arrow: from display position toward the direction that reduces
            # total wirelength of SEG-connected neighbours (positive = up / right).
            plen = _pull_len[i]
            if abs(plen) > 1e-6:
                mid = (dlo + dhi) / 2.0
                if cs.horiz:
                    ax.annotate("",
                        xy=(mid, dp + plen), xytext=(mid, dp),
                        arrowprops=dict(arrowstyle='->', color=col, lw=1.5,
                                        mutation_scale=11),
                        zorder=12, alpha=seg_alpha)
                else:
                    ax.annotate("",
                        xy=(dp + plen, mid), xytext=(dp, mid),
                        arrowprops=dict(arrowstyle='->', color=col, lw=1.5,
                                        mutation_scale=11),
                        zorder=12, alpha=seg_alpha)

        # Update title with layer info (Compacted: M4x5 M5x3)
        counts = {}
        for lid in actual_lids:
            counts[lid] = counts.get(lid, 0) + 1
        
        # Sort by layer ID for consistency
        sorted_lids = sorted(counts.keys())
        layer_summary_parts = []
        for lid in sorted_lids:
            lbl = _LAYER_LABEL.get(lid, f"L{lid}").split()[0]
            cnt = counts[lid]
            if cnt > 1:
                layer_summary_parts.append(f"{lbl}x{cnt}")
            else:
                layer_summary_parts.append(lbl)
        layer_summary = " ".join(layer_summary_parts)
        
        nterms = self.wrapper.input.original_bundle.num_terminals
        # Super-candidate family annotation: `fam F/M` (this candidate's family
        # index / family count) with a `▸` when family-stepping ('G') is active
        # and `+K` for the K other nominal-locus variants in the family.
        fam_str = ""
        grp_pos = self._group_position()
        if grp_pos is not None:
            fam_i, n_fam = grp_pos
            k = len(self._group_of(self.idx)) - 1
            mark = "▸" if getattr(self, '_group_step', False) else ""
            fam_str = (f" · {mark}fam {fam_i + 1}/{n_fam}"
                       + (f" +{k}" if k > 0 else ""))
        # GROUP-PINNED badge (the 'S' super-candidate pin has no sidecar entry).
        gpin = getattr(self.wrapper.input, 'pinned_group', [])
        grp_badge = (f"  ◆ GROUP-PINNED ({len(gpin)})"
                     + (" • member" if self.idx in gpin else "")) if gpin else ""
        title_main = (
            f"{bus_label}B{bid} ({nterms} terms/{len(topo.segments)} segs) · topo {self.idx + 1}/{n}"
            f"{fam_str} · {topo.type} · WL={wl} · [{layer_summary}]{sel_badge}{grp_badge}"
        )
        # Debug view ('debug' flag): a second title line with the planner cost of
        # THIS candidate + its cost-rank (candidates are stepped in increasing
        # cost, so the rank is the traversal position) and the cost components.
        cost_line = self._debug_cost_title()
        if cost_line:
            title_main += "\n" + cost_line
        
        title_color = 'black'
        if is_planner_active and is_current_selection:
            title_color = '#666600'  # mixture
        elif is_current_selection:
            title_color = '#886600'  # gold
        elif is_planner_active:
            title_color = '#005588'  # blue

        # Shrink the title font so a long title (many segments, a group-pinned
        # super-candidate, or the debug cost line) fits the figure width instead
        # of clipping at both window edges (a centered title overflows equally
        # left and right).  A short title keeps the full 12pt.
        t = ax.set_title(title_main, fontsize=self._fit_title_fontsize(title_main),
                         pad=10, color=title_color)
        # Refine with the real text metrics when a renderer is available (Agg /
        # TkAgg): the char-count estimate is conservative, so this only ever
        # relaxes it back up toward 12 or nudges it down a touch — never clips.
        self._shrink_title_to_fit(t)

        # Selected-segment info line: "Selected V segment 3 on M5." — sits under
        # the edit banner in a TopoEdit session, else standalone at top-left
        # (pure j/k selection). A layer change (-/+) shows the transient
        # "V segment 3 is now on M7." via a one-shot override set by _cycle_layer.
        if sel_active and _sel_lid is not None:
            orient = 'H' if _sel_is_h else 'V'
            # Just the metal name (M5), not "M5 V" — the orientation is already
            # in the "V segment" prefix.
            info = (self._seg_info_override or
                    f"Selected {orient} segment {self.sidx} on "
                    f"{_layer_label(_sel_lid, self.layer_stack).split()[0]}.")
            # Second line: the segment's perpendicular position + slide range —
            # "y=<perp> V-slide=[lo,hi]" for an H segment (it slides vertically),
            # "x=<perp> H-slide=[lo,hi]" for a V one.  Reflects any staged 'W'
            # window / NUTS override via _seg_slide.
            if 0 <= self.sidx < len(cs_list):
                cs_i = cs_list[self.sidx]
                s_lo, s_hi = self._seg_slide(cs_i, self.sidx)
                axis, sdir = ('y', 'V') if cs_i.horiz else ('x', 'H')
                adir = 'H' if cs_i.horiz else 'V'
                info += (f"\n{axis}={cs_i.perp_pos} "
                         f"{adir}-span=[{cs_i.along_lo},{cs_i.along_hi}] "
                         f"{sdir}-slide="
                         f"[{self._fmt_perp(float(s_lo))},"
                         f"{self._fmt_perp(float(s_hi))}]")
                # A dogleg-split jog piece (is_jog on the topology segment,
                # set by NUTS's dogleg adoption) — flag it so j/k stepping
                # shows which segment the split introduced.
                shown = self._shown_topo()
                if (0 <= self.sidx < len(shown.segments)
                        and getattr(shown.segments[self.sidx], 'is_jog', False)):
                    info += "  · [dogleg-jog]"
                # Third line: net-pull + the segment's connectivity — busterm
                # taps, pass-through blocks, and connected segs (if any).
                info += "\n" + self._seg_conn_line(cs_i, self.sidx)
                # Debug view: the congestion cost this segment contributes.
                _dbg = self._debug_seg_cost_line()
                if _dbg:
                    info += "\n" + _dbg
            y_info = 0.93 if (self._edit_topo is not None or self._edit_msg) else 0.985
            ax.text(0.01, y_info, info, transform=ax.transAxes, fontsize=8.5,
                    color='#20304a', va='top', ha='left', zorder=60,
                    clip_on=False,
                    bbox=dict(boxstyle='round,pad=0.3', facecolor='#eef4ff',
                              edgecolor='#8899bb', linewidth=0.7, alpha=0.93))
        self._seg_info_override = ""     # one-shot

        if self.fig.canvas.manager:
            bid_  = self.wrapper.input.original_bundle.id
            names_ = self.wrapper.input.original_bundle.get_net_names()
            net0  = names_[0] if names_ else f"B{bid_}"
            self.fig.canvas.manager.set_window_title(f"{net0} (Bundle {bid_})")

        # Identify blocks that this topology must connect (endpoints + passthru)
        highlight_blocks = set(topo.connected_block_names)
        # In a TopoEdit session, always highlight the bundle's busterm blocks —
        # an EMPTY topology (E) has no connected_block_names yet, so the target
        # blocks would otherwise go un-highlighted while building from scratch.
        if self._edit_topo is not None:
            highlight_blocks |= self._bundle_busterm_names()

        # Floorplan blocks
        self._block_patch_artists, self._block_name_artists = _draw_blocks(
            ax, self.fp, self.ui_state, highlight_blocks)

        # Hanan grid — forced ON while a TopoEdit session is open: T/Y place
        # trunks at the cursor's Hanan line, so the candidate lines must show.
        # The explorer shows the BUNDLE-scoped grid (busterm-block + keepout
        # edges — what generation derives candidates from for THIS bundle),
        # not the full-design grid; T/Y snapping stays on the full grid, a
        # superset, so every displayed line remains a snap target.
        _draw_hanan_grid(ax, self.fp, self.ui_state,
                         force=self._edit_topo is not None,
                         grid=self._bundle_hanan_grid())


        # Two-step trunk placement (T/Y): highlight the target Hanan cell the
        # cursor is hovering, so the user sees where the trunk will land.
        if self._trunk_mode is not None and self._trunk_hover is not None:
            self._draw_trunk_preview()

        # Pin-span mode (P): outline the picked busterms and preview the span
        # the selected trunk would take.
        if self._trunk_pin_set is not None:
            self._draw_pin_preview(topo)

        # Slide-range bands (drawn before segments so segments sit on top)
        self._draw_slide_spans(topo, ct)

        # W refine in progress: echo the first captured slide bound so the
        # user sees where it landed before committing the second.
        if self._edit_topo is not None and self._edit_slide_mark is not None:
            self._draw_slide_mark_line(ct)

        # Busterm diamonds (on top of segments and junction dots)
        self._draw_busterm_markers(topo, ct, viz_lw)

        # Legend
        from matplotlib.lines import Line2D
        used_layers = sorted(set(actual_lids))
        handles = [Line2D([0], [0], color=_LAYER_COLOR.get(l, '#888'), lw=3,
                          label=_layer_label(l, self.layer_stack))
                   for l in used_layers]
        handles.append(patches.Patch(facecolor='#888888', alpha=0.20,
                                     label='slide range'))
        handles.append(Line2D([0], [0], color='#888888', lw=0.9, linestyle=':',
                               alpha=0.7, label='slide bound'))
        handles.append(Line2D([0], [0], marker='D', color='w',
                               markerfacecolor='#888888', markeredgecolor='white',
                               markersize=8, label='bus-term'))
        handles.append(Line2D([0], [0], color='#888888', lw=1.5, marker='>',
                               markersize=7, label='pull direction'))
        ax.legend(handles=handles, loc='upper right', fontsize=9)

        ax.set_aspect('equal')
        if curr_xlim is not None:
            ax.set_xlim(curr_xlim)
            ax.set_ylim(curr_ylim)
        else:
            # Fit to the data bbox, then expand the shorter axis so the view
            # fills the whole window (same maximal framing as cmd-z), instead of
            # collapsing to a thin sliver under set_aspect('equal').
            ax.autoscale_view()
            x0, x1 = ax.get_xlim()
            y0, y1 = ax.get_ylim()
            self._home_data_bbox = (x0, x1, y0, y1)
            _set_lims_filling_box(ax, x0, x1, y0, y1)
            self._home_xlim = ax.get_xlim()
            self._home_ylim = ax.get_ylim()
            self._autoscale_needed = False

        self.fig.canvas.draw_idle()
