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

"""BudaVisualizer mixin — The right-hand panel: layer/bundle/overlap lists, stats, toggles, adaptive layout.

Split out of buda_viz.py (see viz_main/__init__.py); methods run on
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

class VizPanelsMixin:

    def fig_redraw(self):
        """Callback for ViewState changes. Syncs UI elements and redraws."""
        # 1. Update button labels
        if self._btn_heatmap:
            self._btn_heatmap.label.set_text('☑ Heatmap' if self.ui_state.heatmap else '☐ Heatmap')
        if self._btn_keepouts:
            self._btn_keepouts.label.set_text('☑ Keepouts' if self.ui_state.keepouts else '☐ Keepouts')
        if self._btn_blknames:
            self._btn_blknames.label.set_text('☑ Names' if self.ui_state.block_names else '☐ Names')
        if self._btn_blocks:
            self._btn_blocks.label.set_text('☑ Blocks' if self.ui_state.blocks else '☐ Blocks')
        if self._btn_bustermss:
            self._btn_bustermss.label.set_text('☑ Terminals' if self.ui_state.busterms else '☐ Terminals')
        if self._btn_vias_conns:
            self._btn_vias_conns.label.set_text('☑ Vias/Conns' if self.ui_state.vias_conns else '☐ Vias/Conns')
        if self._btn_all:
            self._btn_all.label.set_text('☑ All' if self.ui_state.all_vis else '☐ All')
        if self._btn_detailed:
            self._btn_detailed.label.set_text('☑ Detailed' if self.ui_state.detailed_mode else '☐ Detailed')
        if self._btn_tracks:
            self._btn_tracks.label.set_text('☑ Tracks' if self.ui_state.tracks else '☐ Tracks')
        if self._btn_preroutes:
            mode = self.ui_state.preroutes_mode
            self._btn_preroutes.label.set_text(
                '☐ Preroutes' if mode == 'off' else f'☑ Prer:{mode}')
        if self._btn_hanan:
            self._btn_hanan.label.set_text('☑ Hanan' if self.ui_state.hanan_grid else '☐ Hanan')

        # 2. Update artist visibility directly for simple toggles
        for a in self._heatmap_artists:
            a.set_visible(self.ui_state.heatmap)
        if self._cbar_ax:
            self._cbar_ax.set_visible(self.ui_state.heatmap)
        
        for a in self._keepout_artists:
            a.set_visible(self.ui_state.keepouts)
        
        # Terminals hide in detailed mode (per-bit view replaces them); gate on
        # both so a redraw (e.g. from a solo toggle) can't re-reveal them there.
        self._apply_busterm_visibility()

        self._apply_preroute_visibility()

        # Abstract via/conn markers hide in detailed mode (per-bit vias replace
        # them); gate both sets together so neither is left behind.
        self._apply_vias_conns_visibility()

        for a in self._hanan_artists:
            a.set_visible(self.ui_state.hanan_grid)
            
        for a in self._block_patch_artists:
            a.set_visible(self.ui_state.blocks)

        for a in self._block_name_artists:
            a.set_visible(self.ui_state.block_names and self.ui_state.blocks)

        # Detailed-mode track rails.  Tracks can be turned on via paths that
        # don't go through _toggle_tracks() — notably the All toggle, which sets
        # ui_state.tracks via ViewState.toggle_all() and only notifies — so build
        # the (lazy) rails here if they're now needed but not yet built.
        if (self.ui_state.tracks and self.ui_state.detailed_mode
                and not self._rails_built and self._has_detailed_data):
            self._build_rail_artists()
        # Re-apply the set_visible gate (alpha is handled by _refresh_highlight).
        for e in self._grid_rail_artists:
            e['artist'].set_visible(self.ui_state.detailed_mode and self.ui_state.tracks)

        # Pre-routes can likewise be turned on without _cycle_preroutes()
        # (the All toggle sets preroutes_mode='ALL' and only notifies) —
        # build the lazy artists here if they're now needed, then re-apply
        # visibility (the earlier apply ran before these artists existed).
        if (self.ui_state.preroutes_mode != 'off'
                and not self._preroutes_built and self._has_preroute_data):
            self._build_preroute_artists()
            self._apply_preroute_visibility()

        # 3. Complex redraws (blocks, highlights)
        self._refresh_highlight()
        self.fig.canvas.draw_idle()


    def _step_bundle(self, delta):
        if not self._bid_list:
            return
        if self._highlighted not in self._bid_list:
            idx = 0 if delta > 0 else len(self._bid_list) - 1
        else:
            idx = (self._bid_list.index(self._highlighted) + delta) % len(self._bid_list)
        self._highlighted = self._bid_list[idx]
        self._scroll_bundle_into_view(idx)
        self._refresh_highlight()
        self._ipc_send_highlight(self._highlighted)


    def _scroll_bundle_into_view(self, idx):
        """Adjust the bundle-list scroll so row `idx` is within the visible window
        (so the selection's radio stays on screen when cycling with n/p)."""
        n_vis = self._bundle_list_n_visible()
        if idx < self._bundle_scroll:
            self._bundle_scroll = idx
        elif idx >= self._bundle_scroll + n_vis:
            self._bundle_scroll = idx - n_vis + 1
        max_scroll = max(0, len(self._bid_list) - n_vis)
        self._bundle_scroll = max(0, min(max_scroll, self._bundle_scroll))


    def _toggle_heatmap(self):
        if not getattr(self._btn_heatmap, '_buda_enabled', True):
            return                       # dimmed: no congestion heatmap drawn
        self.ui_state.toggle_heatmap()


    def _set_button_enabled(self, btn, enabled, on_color='#e8f4e8'):
        """Keep a panel button always visible, but grey it out (dimmed) when
        `enabled` is False so it reads as inactive instead of vanishing. Each
        button's slot is pre-reserved by `_lrect`, so an always-visible button
        does not shift the panel. Toggle handlers check `_buda_enabled` and
        no-op while dimmed."""
        if btn is None:
            return
        btn.ax.set_visible(True)
        btn._buda_enabled = bool(enabled)
        face = on_color if enabled else '#f0f0f0'
        btn.color = face                 # resting colour (hover-out restores it)
        btn.hovercolor = '0.95' if enabled else face   # no hover glow when dimmed
        btn.ax.set_facecolor(face)
        btn.label.set_color('#111111' if enabled else '#b0b0b0')


    def _toggle_keepouts(self):
        if not getattr(self._btn_keepouts, '_buda_enabled', True):
            return                       # dimmed: no keepouts in the design
        self.ui_state.toggle_keepouts()


    def _toggle_hanan(self):
        self.ui_state.toggle_hanan_grid()


    def _toggle_all(self):
        self.ui_state.toggle_all()
        vis = self.ui_state.all_vis

        # All layers
        for lid in self._layer_visible:
            self._layer_visible[lid] = vis
        if self._btn_all_layers is not None:
            self._btn_all_layers.label.set_text('☑ All Layers' if vis else '☐ All Layers')
            self._btn_all_layers.ax.set_facecolor('#e8e8e8' if vis else '#cccccc')
        self._redraw_layer_list()

        # All bundles
        for bid in self._bundle_visible:
            self._bundle_visible[bid] = vis
        if self._btn_all_bundles is not None:
            self._btn_all_bundles.ax.set_facecolor('#e8e8e8' if vis else '#cccccc')
        self._redraw_bundle_list()

        self._refresh_highlight()
        # ui_state.toggle_all() above redrew the explorer BEFORE _layer_visible
        # was rewritten, so refresh it again now that the layer set is current.
        self._refresh_topo_explorer()
        self.fig.canvas.draw_idle()


    def _toggle_bustermss(self):
        self.ui_state.toggle_busterms()


    def _toggle_vias_conns(self):
        self.ui_state.toggle_vias_conns()


    def _toggle_blocks(self):
        self.ui_state.toggle_blocks()


    def _toggle_block_names(self):
        self.ui_state.toggle_block_names()


    def _toggle_solo(self):
        self.ui_state.toggle_solo()
        if self._btn_solo is not None:
            if self.ui_state.solo:
                self._btn_solo.label.set_text('Solo  ON')
                self._btn_solo.ax.set_facecolor('#ffddaa')
            else:
                self._btn_solo.label.set_text('Solo OFF')
                self._btn_solo.ax.set_facecolor('#f0f0f0')


    # ------------------------------------------------------------------
    # Layer panel (custom, replaces CheckButtons)
    # ------------------------------------------------------------------

    def _update_layer_ids(self):
        """Re-derive the sorted list of layer IDs to show in the panel.
        We include all layers defined in the LayerStack, plus anything
        actually present in the current drawing (e.g. from topology hints).
        """
        import buda as ic_mod
        ids = set()
        if self.layer_stack:
            for d in (ic_mod.LayerDir.HORIZONTAL, ic_mod.LayerDir.VERTICAL):
                ids.update(self.layer_stack.get_layer_ids_by_dir(d))
        
        # Also check what's actually drawn (in case layer_stack is missing 
        # or a topology hint uses an undefined layer ID).
        for artists_list in self._bundle_artists.values():
            for e in artists_list:
                if e.get('layer') is not None:
                    ids.add(e['layer'])
        
        self._layer_ids = sorted(list(ids))
        # Ensure visibility mapping is populated for any new layers found.
        for lid in self._layer_ids:
            if lid not in self._layer_visible:
                self._layer_visible[lid] = True


    def _on_layer_toggle(self, lid):
        self._layer_visible[lid] = not self._layer_visible.get(lid, True)
        self._redraw_layer_list()
        self._refresh_highlight()
        self._refresh_topo_explorer()


    def _on_layer_toggle_all(self):
        """Toggle all layers on (if any are off) or off (if all are on)."""
        all_on    = all(self._layer_visible.values())
        new_state = not all_on
        for lid in self._layer_visible:
            self._layer_visible[lid] = new_state
        if self._btn_all_layers is not None:
            self._btn_all_layers.label.set_text(
                '☑ All Layers' if new_state else '☐ All Layers')
            self._btn_all_layers.ax.set_facecolor(
                '#e8e8e8' if new_state else '#cccccc')
        self._redraw_layer_list()
        self._refresh_highlight()
        self._refresh_topo_explorer()


    def _redraw_layer_list(self):
        ax = self._ax_layers
        if ax is None:
            return
        ax.clear()
        ax.set_axis_off()
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)

        n = len(self._layer_ids)
        if n == 0:
            self.fig.canvas.draw_idle()
            return

        # Per-layer segment and bit counts from the current nuts result.
        layer_seg_count = {}
        layer_bit_count = {}
        if self._nuts_result is not None:
            for ts in self._nuts_result.segments:
                if not ts.placed:
                    continue
                lid = ts.layer
                layer_seg_count[lid] = layer_seg_count.get(lid, 0) + 1
                layer_bit_count[lid] = (layer_bit_count.get(lid, 0)
                                        + self._bundle_bits(ts.bundle_id))

        has_rerun = self._rerun_layer_fn is not None
        for row, lid in enumerate(self._layer_ids):
            # Two text lines per row: name on top, stats just below.
            # 0.25/0.75 split gives ~9 pt separation at min chk_h — no overlap.
            y_name  = 1.0 - (row + 0.25) / n
            y_stats = 1.0 - (row + 0.75) / n
            on    = self._layer_visible.get(lid, True)
            col   = _LAYER_COLOR.get(lid, '#888888')
            vis_char  = '☑' if on else '☐'
            txt_color = col if on else '#bbbbbb'
            dim_color = (col if on else '#bbbbbb')

            # Checkbox glyph — centred vertically across the full row.
            y_mid = 1.0 - (row + 0.5) / n
            ax.text(0.04, y_mid, vis_char,
                    transform=ax.transAxes, fontsize=9, color=txt_color,
                    va='center', clip_on=True)

            # Layer name with its actual orientation ("M4 V" when overridden).
            ax.text(0.22, y_name, _layer_label(lid, self.layer_stack),
                    transform=ax.transAxes, fontsize=9, color=txt_color,
                    va='center', clip_on=True, fontweight='bold')

            # Stats line ("6 segs, 48 wires").
            n_segs = layer_seg_count.get(lid, 0)
            n_bits = layer_bit_count.get(lid, 0)
            if n_segs:
                stats_txt = f'{n_segs} segs, {n_bits} wires'
                ax.text(0.22, y_stats, stats_txt,
                        transform=ax.transAxes, fontsize=8,
                        color=dim_color, va='center', clip_on=True)

            if has_rerun:
                ax.text(0.88, y_mid, '↺',
                        transform=ax.transAxes, fontsize=9,
                        color='#555555', va='center', ha='center',
                        clip_on=True,
                        bbox=dict(boxstyle='round,pad=0.25',
                                  fc='#eeeeee', ec='#aaaaaa', lw=0.6))

        self.fig.canvas.draw_idle()


    def _on_layer_list_click(self, event):
        ax = self._ax_layers
        if ax is None or event.ydata is None or event.xdata is None:
            return
        n = len(self._layer_ids)
        if n == 0:
            return
        row = int((1.0 - event.ydata) * n)
        if not (0 <= row < n):
            return
        lid = self._layer_ids[row]
        if event.xdata >= 0.75 and self._rerun_layer_fn is not None:
            # Rerun button area → re-solve this layer and refresh the view.
            result = self._rerun_layer_fn(lid)
            if result is not None:
                self._redraw_nuts_tracks(result)
        else:
            # Checkbox area → toggle layer visibility.
            self._on_layer_toggle(lid)


    # ------------------------------------------------------------------
    # Bundle list panel
    # ------------------------------------------------------------------

    def _bundle_list_n_visible(self):
        """Number of bundle rows that fit in the list panel at current figure size."""
        if self._ax_bundles is None:
            return 20
        fig_h_in  = self.fig.get_figheight()
        ax_h_frac = self._ax_bundles.get_position().height
        ax_h_in   = fig_h_in * ax_h_frac
        row_h_in  = 0.145   # ~10 pt rows at standard DPI
        return max(1, int(ax_h_in / row_h_in))


    @staticmethod
    def _expected_bit_wires(w):
        """Expected DNUTS bit-wire count for a wrapper's selected topology,
        or None when no topology is selected.

        Taper-aware (#268): a fan-in segment with a non-empty
        Topology::seg_bits entry emits NetSegments only for its member bits;
        every other segment carries the full bundle width — the same rule as
        C++ seg_bit_count and check_dnuts's UNPLACED audit (#273).  The naive
        nets × segments would report phantom opens on clean CONVERGENT
        fan-in bundles."""
        sel = w.plan.selected_topology_index
        if not w.input.candidates or not (0 <= sel < len(w.input.candidates)):
            return None
        topo  = w.input.candidates[sel]
        nbits = len(w.input.original_bundle.get_net_names())
        sb    = topo.seg_bits
        return sum(len(sb.get(si, [])) or nbits
                   for si in range(len(topo.segments)))


    def _bundle_unplaced(self):
        """{bundle_id: DNUTS-dropped bit-wire count}, only for bundles with
        opens.  Empty when detailed NUTS hasn't run."""
        if not self._detailed_result:
            return {}
        placed = {}
        for ns in self._detailed_result.net_segments:
            placed[ns.bundle_id] = placed.get(ns.bundle_id, 0) + 1
        opens = {}
        for w in self.bundles:
            bid   = w.input.original_bundle.id
            n_exp = self._expected_bit_wires(w)
            if n_exp is None:
                continue
            n_unp = n_exp - placed.get(bid, 0)
            if n_unp > 0:
                opens[bid] = n_unp
        return opens


    def _sort_bid_list(self):
        """Order the bundle rows: bundles with DNUTS-dropped (open) bits first,
        most dropped first, so every open is investigated straight from the top
        of the panel; the clean rest keep the plain id order."""
        opens = self._bundle_unplaced()
        self._bid_list = sorted(self._bundle_artists.keys(),
                                key=lambda b: (-opens.get(b, 0), b))


    def _apply_overlap_layout(self):
        """Fold the Overlap panel's vertical space into the bundle list while
        the design has no overlaps (the panel would sit empty), and restore the
        split when overlaps (re)appear after a re-route.  Hidden widgets are
        parked on a degenerate off-corner rect so they can't catch clicks or
        scroll events.  Returns True when the layout changed."""
        if self._ax_overlaps is None or not self._right_rects:
            return False
        mode = 'with_ov' if self._overlap_entries else 'no_ov'
        if mode == self._ov_layout_mode:
            return False
        self._ov_layout_mode = mode
        r = self._right_rects[mode]
        self._ax_bundles.set_position(r['bundle_list'])
        self._ax_bscroll_dn.set_position(r['bscroll_dn'])
        self._btn_all_overlaps.ax.set_position(r['ov_btn'])
        show_ov = (mode == 'with_ov')
        if show_ov:
            u = self._right_rects['ov_widgets']
            self._ax_oscroll_up.set_position(u['oscroll_up'])
            self._ax_overlaps.set_position(u['overlaps'])
            self._ax_oscroll_dn.set_position(u['oscroll_dn'])
        for a in (self._ax_oscroll_up, self._ax_overlaps, self._ax_oscroll_dn):
            a.set_visible(show_ov)
            if not show_ov:
                a.set_position([0.0005, 0.0005, 0.0004, 0.0004])
        for b in (self._btn_oscroll_up, self._btn_oscroll_dn):
            if b is not None:
                b.set_active(show_ov)
        self._redraw_bundle_list()     # row capacity follows the new height
        if show_ov:
            self._redraw_overlap_list()
        return True


    def _design_counts(self):
        """Return (n_bundles, n_buses, n_nets) for the whole design.

        A *net* is one wire; a *bus* is the `add_bus` grouping it belongs to
        (`<bus>_<idx>` / `<bus>_b<idx>` — same convention the CLI's bus summary
        uses), and a bare net with no numeric suffix counts as its own bus.
        Bundles/buses/nets are fixed once the pipeline has run, so this is
        computed from the bundle wrappers with no extra plumbing."""
        n_bundles = len(self.bundles)
        n_nets = 0
        buses = set()
        for w in self.bundles:
            for nm in w.input.original_bundle.get_net_names():
                n_nets += 1
                m = re.match(r'^(.*?)_([A-Za-z]*)(\d+)$', nm)
                buses.add((m.group(1), m.group(2)) if m else ('', nm))
        return n_bundles, len(buses), n_nets


    def _redraw_design_stats(self):
        """Draw the always-on 'M buses · K nets' design-size line. The bundle
        count lives on the All Bundles button, so this stays one compact line."""
        ax = self._ax_design_stats
        if ax is None:
            return
        ax.clear()
        ax.set_axis_off()
        _nb, nbus, nn = self._design_counts()
        ax.text(0.5, 0.5, f"{nbus} buses · {nn} nets",
                transform=ax.transAxes, ha='center', va='center',
                fontsize=8.5, color='#333333', fontweight='bold', clip_on=True)


    def _nuts_seg_counts(self):
        """Return (n_segments, n_bits) placed across all layers in the current
        NUTS result — the totals the per-layer rows break down.  (0, 0) before
        NUTS has run."""
        n_segs = n_bits = 0
        if self._nuts_result is not None:
            for ts in self._nuts_result.segments:
                if not ts.placed:
                    continue
                n_segs += 1
                n_bits += self._bundle_bits(ts.bundle_id)
        return n_segs, n_bits


    def _redraw_layer_stats(self):
        """Draw the always-on total-NUTS-segments header above the layer panel,
        mirroring the 'buses · nets' line above the bundle panel."""
        ax = self._ax_layer_stats
        if ax is None:
            return
        ax.clear()
        ax.set_axis_off()
        nseg, nbits = self._nuts_seg_counts()
        if nseg:
            ax.text(0.5, 0.5, f"{nseg} segments · {nbits} wires",
                    transform=ax.transAxes, ha='center', va='center',
                    fontsize=8.5, color='#333333', fontweight='bold',
                    clip_on=True)


    def _redraw_bundle_list(self):
        """Clear and redraw all rows in the bundle checkbox list."""
        ax = self._ax_bundles
        if ax is None:
            return
        ax.clear()
        ax.set_axis_off()
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)

        # Update the header button label with global stats if available.
        if self._btn_all_bundles is not None:
            all_on = all(self._bundle_visible.values())
            # Fold the design-wide bundle count into the toggle label
            # ("☑ 80 Bundles") so the stats line can stay a single row.
            all_lbl = f"{'☑' if all_on else '☐'} {len(self.bundles)} Bundles"
            if self._detailed_result:
                n_total = sum(n for n in map(self._expected_bit_wires, self.bundles)
                              if n is not None)
                all_lbl += f" [{self._detailed_result.num_unplaced}/{n_total}]"
            self._btn_all_bundles.label.set_text(all_lbl)

        n_vis   = self._bundle_list_n_visible()
        bids    = self._bid_list
        n_total = len(bids)

        for row in range(n_vis):
            idx = self._bundle_scroll + row
            if idx >= n_total:
                break
            bid = bids[idx]
            on  = self._bundle_visible.get(bid, True)

            # y coordinate: top row at y≈1, bottom row at y≈0.
            y = 1.0 - (row + 0.5) / n_vis

            w = next(w for w in self.bundles if w.input.original_bundle.id == bid)

            # Right-aligned [unplaced/total] stats column (detailed mode only).
            # Computed first so the label budget below can reserve room for it
            # and never run into it.
            stats_part = ""
            stats_color = '#111111'
            if self._detailed_result:
                n_expected = self._expected_bit_wires(w)   # taper-aware (#268)
                if n_expected is None:
                    stats_part = '[no topo]'; stats_color = '#888888'
                else:
                    n_placed   = sum(1 for ns in self._detailed_result.net_segments if ns.bundle_id == bid)
                    n_unp = n_expected - n_placed
                    stats_part = f"[{n_unp}/{n_expected}]"
                    stats_color = '#CC0000' if n_unp > 0 else '#008800'

            # Build label: "B{bid} {name} [bits]", truncated so it stops short of
            # the stats column. In detailed mode the stats' total already conveys
            # the bit count, so drop the redundant [bits] suffix for extra room.
            name  = self._bundle_name(bid)
            nbits = self._bundle_bits(bid)
            prefix = f"B{bid} "
            bits_suffix = "" if stats_part else (f" [{nbits}]" if nbits > 0 else "")
            # ~25 chars span the row at fontsize 7; reserve the stats width (+1
            # gap) on the right, and 2 chars for the "☑ " marker on the left.
            ROW_CHARS = 25
            reserve   = (len(stats_part) + 1) if stats_part else 0
            full_budget = max(4, ROW_CHARS - 2 - reserve)
            max_name = max(3, full_budget - len(prefix) - len(bits_suffix))
            name_part = name if len(name) <= max_name else name[:max_name - 1] + "…"
            full  = f"{prefix}{name_part}{bits_suffix}"

            # Radio indicator (left, for selection) and checkbox (for visibility).
            radio_char = '◉' if bid == self._highlighted else '○'
            vis_char   = '☑' if on else '☐'
            txt_color  = '#111111' if on else '#bbbbbb'
            sel_color  = '#004488' if bid == self._highlighted else txt_color

            ax.text(0.04, y, radio_char,
                    transform=ax.transAxes,
                    fontsize=7, color=sel_color,
                    va='center', clip_on=True)

            ax.text(0.11, y, f"{vis_char} {full}",
                    transform=ax.transAxes,
                    fontsize=7, color=txt_color,
                    va='center', clip_on=True)
            if stats_part:
                 ax.text(0.89, y, stats_part,
                        transform=ax.transAxes,
                        fontsize=7, color=stats_color,
                        va='center', ha='right', fontweight='bold', clip_on=True)

        # Scrollbar thumb on right edge.
        if n_total > n_vis:
            # Background track.
            ax.add_patch(patches.Rectangle(
                (0.91, 0.0), 0.07, 1.0,
                transform=ax.transAxes,
                linewidth=0, facecolor='#e8e8e8',
                clip_on=True, zorder=1))
            # Thumb.
            y1 = 1.0 - self._bundle_scroll / n_total
            y0 = max(0.0, y1 - n_vis / n_total)
            ax.add_patch(patches.Rectangle(
                (0.91, y0), 0.07, y1 - y0,
                transform=ax.transAxes,
                linewidth=0.5, edgecolor='#888888',
                facecolor='#aaaaaa',
                clip_on=True, zorder=2))

        self.fig.canvas.draw_idle()


    def _on_bundle_list_click(self, event):
        """Radio column (x<0.10): select bundle.  Checkbox+label (x>=0.10): toggle visibility."""
        ax = self._ax_bundles
        if ax is None or event.ydata is None or event.xdata is None:
            return
        n_vis = self._bundle_list_n_visible()
        row = int((1.0 - event.ydata) * n_vis)
        idx = self._bundle_scroll + row
        bids = self._bid_list
        if 0 <= idx < len(bids):
            bid = bids[idx]
            if event.xdata < 0.10:
                # Radio click → select / deselect bundle in main view.
                self._set_highlight(bid)   # _set_highlight toggles if same bid
            else:
                # Checkbox click → toggle visibility.
                self._bundle_visible[bid] = not self._bundle_visible.get(bid, True)
                self._redraw_bundle_list()
                self._refresh_highlight()


    def _scroll_bundles(self, delta):
        n_vis      = self._bundle_list_n_visible()
        max_scroll = max(0, len(self._bid_list) - n_vis)
        self._bundle_scroll = max(0, min(max_scroll, self._bundle_scroll + delta))
        self._redraw_bundle_list()


    def _on_scroll_event(self, event):
        if self._ax_bundles is not None and event.inaxes == self._ax_bundles:
            delta = -3 if event.button == 'up' else 3
            self._scroll_bundles(delta)
        elif self._ax_overlaps is not None and event.inaxes == self._ax_overlaps:
            delta = -3 if event.button == 'up' else 3
            self._scroll_overlaps(delta)


    def _on_bundle_toggle_all(self):
        """Toggle all bundles on (if any are off) or off (if all are on)."""
        all_on    = all(self._bundle_visible.values())
        new_state = not all_on
        for bid in self._bundle_visible:
            self._bundle_visible[bid] = new_state
        if self._btn_all_bundles is not None:
            self._btn_all_bundles.ax.set_facecolor(
                '#e8e8e8' if new_state else '#cccccc')
        self._redraw_bundle_list()
        self._refresh_highlight()


    # ------------------------------------------------------------------
    # Overlap list panel
    # ------------------------------------------------------------------

    def _on_overlap_toggle_all(self):
        """Clear the current overlap selection and return to normal view."""
        self._highlighted_set  = set()
        self._selected_overlap = None
        self._highlighted      = None
        self._overlap_state    = 0
        self._refresh_highlight()


    def _scroll_overlaps(self, delta):
        n_vis      = self._overlap_list_n_visible()
        max_scroll = max(0, len(self._overlap_entries) - n_vis)
        self._overlap_scroll = max(0, min(max_scroll, self._overlap_scroll + delta))
        self._redraw_overlap_list()


    def _overlap_list_n_visible(self):
        if self._ax_overlaps is None:
            return 8
        fig_h_in  = self.fig.get_figheight()
        ax_h_frac = self._ax_overlaps.get_position().height
        ax_h_in   = fig_h_in * ax_h_frac
        row_h_in  = 0.145
        return max(1, int(ax_h_in / row_h_in))


    def _redraw_overlap_list(self):
        ax = self._ax_overlaps
        if ax is None:
            return
        ax.clear()
        ax.set_axis_off()
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)

        entries = self._overlap_entries
        n_total = len(entries)

        if n_total == 0:
            ax.text(0.5, 0.5, "No overlaps", transform=ax.transAxes,
                    fontsize=7, color='#888888', ha='center', va='center')
            self.fig.canvas.draw_idle()
            return

        n_vis = self._overlap_list_n_visible()
        for row in range(n_vis):
            idx = self._overlap_scroll + row
            if idx >= n_total:
                break
            od     = entries[idx]
            y      = 1.0 - (row + 0.5) / n_vis
            is_sel = (od is self._selected_overlap)

            radio_char = '◉' if is_sel else '○'
            txt_color  = '#cc0000' if is_sel else '#444444'
            layer_lbl  = _LAYER_LABEL.get(od.layer, f'M{od.layer}')[:2]
            label      = f"B{od.bid_a}×B{od.bid_b} {layer_lbl}"

            ax.text(0.03, y, radio_char,
                    transform=ax.transAxes,
                    fontsize=7, color=txt_color,
                    va='center', clip_on=True)
            ax.text(0.20, y, label,
                    transform=ax.transAxes,
                    fontsize=7, color=txt_color,
                    va='center', clip_on=True)

        # Scrollbar thumb when list overflows.
        if n_total > n_vis:
            ax.add_patch(patches.Rectangle(
                (0.91, 0.0), 0.07, 1.0,
                transform=ax.transAxes,
                linewidth=0, facecolor='#e8e8e8',
                clip_on=True, zorder=1))
            y1 = 1.0 - self._overlap_scroll / n_total
            y0 = max(0.0, y1 - n_vis / n_total)
            ax.add_patch(patches.Rectangle(
                (0.91, y0), 0.07, y1 - y0,
                transform=ax.transAxes,
                linewidth=0.5, edgecolor='#888888',
                facecolor='#ffaaaa',
                clip_on=True, zorder=2))

        self.fig.canvas.draw_idle()


    def _on_overlap_list_click(self, event):
        ax = self._ax_overlaps
        if ax is None or event.ydata is None:
            return
        n_vis = self._overlap_list_n_visible()
        row   = int((1.0 - event.ydata) * n_vis)
        idx   = self._overlap_scroll + row
        if 0 <= idx < len(self._overlap_entries):
            self._set_overlap_highlight(self._overlap_entries[idx])
