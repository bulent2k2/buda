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

"""BudaVisualizer mixin — Abstract-view drawing: blocks, keepouts, heatmap, buses, NUTS tracks, markers.

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

class VizAbstractDrawMixin:

    def _redraw_nuts_tracks(self, rerun_result):
        """Remove all NUTS track artists and redraw from an updated result."""
        # result can be NUTSResult or (NUTSResult, DetailedNUTSResult)
        if isinstance(rerun_result, tuple):
            nuts_result, detailed_result = rerun_result
        else:
            nuts_result, detailed_result = rerun_result, None

        # Detach every registered artist from the axes (abstract).
        for entries in self._bundle_artists.values():
            for e in entries:
                try: e['artist'].remove()
                except Exception: pass
        for art in self._highlight_overlays:
            try: art.remove()
            except Exception: pass
        self._highlight_overlays.clear()
        self._bundle_artists.clear()

        # Detach detailed artists if they exist.
        for entries in self._detailed_bundle_artists.values():
            for e in entries:
                try: e['artist'].remove()
                except Exception: pass
        for e in self._grid_rail_artists:
            try: e['artist'].remove()
            except Exception: pass
        self._detailed_bundle_artists.clear()
        self._detailed_via_artists.clear()   # removed above via the registry
        self._grid_rail_artists.clear()
        self._detailed_built = False   # force lazy rebuild from the new result
        self._rails_built    = False

        # Redraw segments at new track positions.
        self.draw_nuts_tracks(nuts_result)
        if detailed_result is not None and self.routing_grid and self.layer_stack:
            self.draw_detailed_tracks(detailed_result, self.routing_grid, self.layer_stack)
            # If the detailed view is currently open, rebuild its artists now so
            # the window isn't left empty until the next toggle.  Rails are only
            # rebuilt when Tracks is actually on (kept lazy otherwise).
            if self.ui_state.detailed_mode:
                self._build_detailed_artists()
                if self.ui_state.tracks:
                    self._build_rail_artists()
                # draw_nuts_tracks() just created the abstract artists visible;
                # hide them so detailed mode doesn't overlay both sets
                # (_refresh_highlight only governs _detailed_bundle_artists here).
                for entries in self._bundle_artists.values():
                    for e in entries:
                        e['artist'].set_visible(False)
                for entries in self._detailed_bundle_artists.values():
                    for e in entries:
                        e['artist'].set_visible(True)
                self._apply_detailed_via_visibility()
                for e in self._grid_rail_artists:
                    e['artist'].set_visible(self.ui_state.tracks)

        # Rebuild the congestion heatmap from the RE-PLANNED cut/band state
        # (audit P7-05): the re-run recomputed every band's usage, so the
        # original overlay would keep shading bands the routes no longer use
        # (and miss new hot bands).  Remove the stale artists and redraw from
        # fresh cuts; dim the [Heatmap] button when there is nothing to show.
        if getattr(self, "_cuts_provider", None) is not None:
            for a in self._heatmap_artists:
                try: a.remove()
                except Exception: pass
            self._heatmap_artists = []
            fresh = None
            try:
                fresh = self._cuts_provider()
            except Exception:
                fresh = None
            if fresh:
                cuts, xs, ys = fresh
                if cuts:
                    self.draw_congestion_map(cuts, xs, ys)
            if getattr(self, "_btn_heatmap", None) is not None:
                self._set_button_enabled(self._btn_heatmap,
                                         bool(self._heatmap_artists))

        # Refresh layer list in case new layers were introduced.
        self._update_layer_ids()
        self._redraw_layer_list()
        self._redraw_layer_stats()

        # Rebuild overlap list.
        self._overlap_entries = sorted(
            nuts_result.overlap_details,
            key=lambda od: (od.layer, od.bid_a, od.bid_b)
        )
        n_ov = len(self._overlap_entries)
        if self._btn_all_overlaps is not None:
            self._btn_all_overlaps.label.set_text(
                f'Overlaps ({n_ov})' if n_ov else 'No Overlaps')

        # Clear overlap selection — geometry has changed.
        self._highlighted_set  = set()
        self._selected_overlap = None
        self._overlap_state    = 0

        # Re-rank the bundle rows (opens may have moved between bundles) and
        # re-fit the panel split (overlaps may have appeared or vanished).
        self._sort_bid_list()
        self._apply_overlap_layout()

        self._refresh_highlight()

        # The route changed (e.g. a re-run pinned a different topology): refresh
        # the cached home extent so a later 'h' fits the re-routed design, not
        # the extent captured at first render.
        self._recompute_home_bbox()


    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _redraw_blocks(self, highlight_bid=None):
        """Clear and redraw floorplan blocks.

        When highlight_bid is set, blocks connected to that bundle's selected
        topology (via topo.connected_block_names) are drawn with a green fill
        and bold border; all others are dimmed.  When None, all blocks use the
        default style.
        """
        for p in self._block_patch_artists:
            try: p.remove()
            except Exception: pass
        for txt in self._block_name_artists:
            try: txt.remove()
            except Exception: pass
        self._block_patch_artists = []
        self._block_name_artists  = []

        highlight_blocks = set()
        if highlight_bid is not None:
            wrapper = next((w for w in self.bundles
                            if w.input.original_bundle.id == highlight_bid), None)
            if (wrapper and wrapper.input.candidates and
                    0 <= wrapper.plan.selected_topology_index < len(wrapper.input.candidates)):
                topo = wrapper.input.candidates[wrapper.plan.selected_topology_index]
                highlight_blocks = set(topo.connected_block_names)

        for name, rect in self.fp.get_all_blocks():
            if name in highlight_blocks:
                ps, txt = _draw_block(self.ax, name, rect, self.fp,
                                      lw=1.8, edge='#333333', face='#d0f0d0',
                                      alpha=0.50, fontsize=8, zorder=1.5)
            elif highlight_blocks:
                ps, txt = _draw_block(self.ax, name, rect, self.fp,
                                      lw=0.8, alpha=0.12, fontsize=7)
            else:
                ps, txt = _draw_block(self.ax, name, rect, self.fp)
            self._block_patch_artists.extend(ps)
            if txt is not None:
                self._block_name_artists.append(txt)

        for p in self._block_patch_artists:
            p.set_visible(self.ui_state.blocks)
        for txt in self._block_name_artists:
            txt.set_visible(self.ui_state.blocks and self.ui_state.block_names)


    def draw_blocks(self):
        self._redraw_blocks()
        self.draw_keepouts()


    def draw_keepouts(self):
        """Draw KeepoutZones as hatched rectangles with layer labels."""
        self._keepout_artists = []
        vis = self.ui_state.keepouts
        for koz in self.fp.get_keepout_zones():
            r = koz.bbox
            w = r.x2 - r.x1
            h = r.y2 - r.y1
            # Red hatched rectangle
            rect = patches.Rectangle(
                (r.x1, r.y1), w, h,
                linewidth=1, edgecolor='red', facecolor='none',
                hatch='///', alpha=0.4, zorder=1)
            rect.set_visible(vis)
            self.ax.add_patch(rect)
            self._keepout_artists.append(rect)
            
            # Label with layers
            layers = sorted(list(koz.layer_ids))
            layer_str = "KOZ: " + ",".join(_LAYER_LABEL.get(lid, f"M{lid}").split()[0] for lid in layers)
            txt = self.ax.text((r.x1 + r.x2) / 2, r.y2 + 5,
                         layer_str, color='red', fontsize=7, 
                         ha='center', va='bottom', clip_on=True, zorder=2)
            txt.set_visible(vis)
            self._keepout_artists.append(txt)

        if self._btn_keepouts is not None:
             # Always visible; dimmed (inactive) when there are no keepouts.
             self._set_button_enabled(self._btn_keepouts, bool(self._keepout_artists))


    def draw_congestion_map(self, cuts, xs, ys):
        """Shade each Hanan (cut × perpendicular-band) cell by utilisation ratio.

        V-cuts (vertical lines, counting H-segments) are shaded per Y-band.
        H-cuts (horizontal lines, counting V-segments) are shaded per X-band.
        Each cell gets its own colour so the map shows true 2D congestion.

        All cells are collapsed into a single PatchCollection (one artist
        instead of thousands) and only the worst `_HEATMAP_LABEL_CAP` overflow
        cells are annotated, so the overlay stays cheap to render.
        """
        cmap = plt.cm.RdYlGn_r
        self._heatmap_artists = []

        rects = []                 # Rectangle patches (one per congested cell)
        label_cands = []           # (ratio, cx, cy, layer_id, cap) for overflow cells

        for cut in cuts:
            is_vcut = (cut.p1.x == cut.p2.x)   # V-cut → shades an X-channel × Y-bands
            n_bands = len(cut.band_cap)
            if n_bands == 0:
                continue

            # Locate the X-channel (V-cut) or Y-channel (H-cut) this cut belongs to.
            if is_vcut:
                cx = cut.p1.x
                x_idx = [i for i, x in enumerate(xs) if x <= cx]
                if not x_idx: continue
                xi = x_idx[-1]
                lo_a = xs[xi]
                hi_a = xs[xi + 1] if xi + 1 < len(xs) else cx + 20
                perp_grid = ys
            else:
                cy = cut.p1.y
                y_idx = [i for i, y in enumerate(ys) if y <= cy]
                if not y_idx: continue
                yi = y_idx[-1]
                lo_a = ys[yi]
                hi_a = ys[yi + 1] if yi + 1 < len(ys) else cy + 20
                perp_grid = xs

            for b in range(min(n_bands, len(perp_grid) - 1)):
                cap   = cut.band_cap[b]
                usage = cut.band_usage[b]
                if usage == 0:
                    continue
                ratio = usage / cap if cap > 0 else 2.0  # cap<=0 → blocked cell

                # Bake alpha into the RGBA facecolor so a single collection can
                # carry per-cell colour + opacity (match_original picks it up).
                r, g, bl, _ = cmap(min(ratio, 1.5) / 1.5)
                alpha = 0.12 + 0.22 * min(ratio, 1.0)

                p_lo = perp_grid[b]
                p_hi = perp_grid[b + 1]

                if is_vcut:
                    rects.append(patches.Rectangle(
                        (lo_a, p_lo), hi_a - lo_a, p_hi - p_lo,
                        linewidth=0, facecolor=(r, g, bl, alpha)))
                    cell_cx, cell_cy = (lo_a + hi_a) / 2, (p_lo + p_hi) / 2
                else:
                    rects.append(patches.Rectangle(
                        (p_lo, lo_a), p_hi - p_lo, hi_a - lo_a,
                        linewidth=0, facecolor=(r, g, bl, alpha)))
                    cell_cx, cell_cy = (p_lo + p_hi) / 2, (lo_a + hi_a) / 2

                if ratio > 1.0:
                    label_cands.append((ratio, cell_cx, cell_cy, cut.layer_id, cap))

        # One PatchCollection for every cell — match_original keeps per-cell RGBA.
        if rects:
            pc = PatchCollection(rects, match_original=True, zorder=3)
            self.ax.add_collection(pc)
            self._heatmap_artists.append(pc)

        # Annotate only the worst overflow cells.
        label_cands.sort(key=lambda t: -t[0])
        for ratio, cx, cy, layer_id, cap in label_cands[:self._HEATMAP_LABEL_CAP]:
            layer_name = _LAYER_LABEL.get(layer_id, f"M{layer_id}").split()[0]
            label = f"{layer_name}\nBLOCK" if cap <= 0 else f"{layer_name}\n{ratio:.0%}"
            txt = self.ax.text(cx, cy, label, fontsize=7, color='white',
                               ha='center', va='center', zorder=5,
                               fontweight='bold', clip_on=True)
            txt.set_bbox(dict(facecolor='darkred', alpha=0.8, edgecolor='none', pad=1))
            self._heatmap_artists.append(txt)
        if len(label_cands) > self._HEATMAP_LABEL_CAP:
            print(f"[buda_viz] heatmap: labelling {self._HEATMAP_LABEL_CAP} worst of "
                  f"{len(label_cands)} overflow cells (colour shows the rest).")

        # Apply current visibility state.
        vis = self.ui_state.heatmap
        for a in self._heatmap_artists:
            a.set_visible(vis)

        # Colorbar legend — created once per draw, rebuilt on subsequent calls.
        self._redraw_colorbar()


    def _redraw_colorbar(self):
        """Re-create the congestion colorbar in the correct left-panel position."""
        if not self._heatmap_artists and self._cbar_ax is None:
            return
        
        # Build dummy data for ScalarMappable if we haven't already.
        import matplotlib.colors as mcolors
        import numpy as np
        # plt.cm.get_cmap was removed in matplotlib 3.9; plt.get_cmap still works.
        cmap = plt.get_cmap('RdYlGn_r')

        if self._cbar_ax is not None:
            try:
                self._cbar_ax.remove()
            except Exception:
                pass

        # Positioned below the button stack, aligned horizontally with buttons.
        # Height reduced to 0.35.
        cb_w = 0.012
        cb_h = 0.35
        # Align right edge of heatmap with right edge of buttons:
        cb_x = self._LX + self._LW - cb_w
        # Pack below the last button.
        cb_y = self._ly_post_buttons - cb_h - 0.04
        
        self._cbar_ax = self.fig.add_axes([cb_x, cb_y, cb_w, cb_h])

        # Build a custom colormap that matches the actual cell appearance:
        # at 0% congestion cells are ~white (low alpha on white bg); at 150%+ they
        # are the full RdYlGn_r red.  Blend from white → RdYlGn_r colours.
        base_colors = cmap(np.linspace(0, 1, 256))
        # alpha ramp: 0.12 at ratio=0, up to 0.34 at ratio=1, held at 0.34 beyond
        ratios = np.linspace(0, 1.5, 256)
        alphas = np.clip(0.12 + 0.22 * np.minimum(ratios, 1.0), 0, 1)
        white = np.array([1.0, 1.0, 1.0, 1.0])
        blended = base_colors.copy()
        for i, a in enumerate(alphas):
            blended[i, :3] = a * base_colors[i, :3] + (1 - a) * white[:3]
        blended[:, 3] = 1.0   # fully opaque in the colorbar
        cbar_cmap = mcolors.ListedColormap(blended)
        sm = plt.cm.ScalarMappable(
            cmap=cbar_cmap,
            norm=mcolors.Normalize(vmin=0.0, vmax=1.5))
        sm.set_array([])
        cbar = self.fig.colorbar(sm, cax=self._cbar_ax)
        cbar.set_ticks([0.0, 0.5, 1.0, 1.5])
        cbar.set_ticklabels(['0%', '50%', '100%', '≥150%'])
        cbar.ax.tick_params(labelsize=7)
        cbar.set_label('Congestion', fontsize=8, labelpad=4)
        cbar.ax.yaxis.set_label_position('left')
        cbar.ax.yaxis.set_ticks_position('left')
        self._cbar_ax.set_visible(self.ui_state.heatmap)


    def draw_hanan_grid(self):
        # Remove any existing
        for a in self._hanan_artists:
            try: a.remove()
            except Exception: pass
        self._hanan_artists = _draw_hanan_grid(self.ax, self.fp, self.ui_state)


    @staticmethod
    def _busterm_positions(topo, ct, ts_map=None, bid=None, offset=0.0):
        """Return (driver_pos, [receiver_pos, ...]) from ConnTopology BUSTERMs.

        Uses ts.track_position (NUTS-adjusted perp) when ts_map is supplied,
        otherwise falls back to the nominal cs.perp_pos.  The first BUSTERM
        encountered is treated as the driver terminal; the rest are receivers.
        """
        positions = []
        for si, (seg, cs) in enumerate(zip(topo.segments, ct.segs())):
            key = (bid, si) if bid is not None else None
            ts  = ts_map.get(key) if (ts_map and key) else None
            perp = (ts.track_position if (ts and ts.placed) else cs.perp_pos) + offset
            for conn in cs.conns:
                if conn.kind != ic.SegConnKind.BUSTERM:
                    continue
                if cs.horiz:
                    px, py = conn.at_pos + offset, perp
                else:
                    px, py = perp, conn.at_pos + offset
                positions.append((px, py))
        if not positions:
            return None, []
        return positions[0], positions[1:]


    def _draw_via_marker(self, bid, x, y, msz, alpha, zorder, layer=None,
                         phys=None):
        """X inside a square at an H↔V segment junction.

        phys (data units, NUTS view): the junction's physical extent — the
        wider of the two crossing segments' bus widths — so the marker size
        tracks the segment widths at every zoom (_sync_nuts_linewidths)."""
        sq, = self.ax.plot(x, y, 's', color='white',
                           markeredgecolor='black', markeredgewidth=1.2,
                           markersize=msz, alpha=alpha, zorder=zorder, clip_on=True)
        self._register(bid, sq, alpha=alpha, lw=msz, layer=layer,
                       marker_phys=phys)
        self._vias_conns_artists.append(sq)
        xm, = self.ax.plot(x, y, 'x', color='black',
                           markersize=msz * 0.65, markeredgewidth=1.5,
                           alpha=alpha, zorder=zorder + 1, clip_on=True)
        self._register(bid, xm, alpha=alpha, lw=msz * 0.65, layer=layer,
                       marker_phys=(phys * 0.65 if phys else None),
                       ms_floor=4.0 * 0.65)
        self._vias_conns_artists.append(xm)
        if not self.ui_state.vias_conns:
            sq.set_visible(False)
            xm.set_visible(False)


    def _draw_busterm_conn(self, bid, x, y, col, msz, alpha, zorder, layer=None,
                           phys=None):
        """Filled square at a segment endpoint that connects to a busterm.

        phys (data units, NUTS view): the owning segment's bus width — the
        marker size tracks it at every zoom (_sync_nuts_linewidths)."""
        sq, = self.ax.plot(x, y, 's', color=col,
                           markeredgecolor='black', markeredgewidth=1.0,
                           markersize=msz, alpha=alpha, zorder=zorder, clip_on=True)
        self._register(bid, sq, alpha=alpha, lw=msz, layer=layer,
                       marker_phys=phys)
        self._vias_conns_artists.append(sq)
        if not self.ui_state.vias_conns:
            sq.set_visible(False)


    def _draw_seg_connectors(self, bid, seg_idx, cs, sx, sy, col, msz, alpha,
                              zorder, along_offset=0.0, adj_perp=None, layer=None,
                              seg_widths=None):
        """Draw via or busterm-conn marker at each connection point on a segment.

        Uses ConnTopology's cs.conns to determine the marker type:
          BUSTERM → filled square (_draw_busterm_conn)
          SEG     → X-in-square  (_draw_via_marker)

        Deduplication: each SEG via is shared between two segments.  We draw it
        only from the lower-indexed segment (skip when conn.seg_idx < seg_idx).

        adj_perp (dict seg_idx→track_position): when provided (NUTS view), SEG via
        positions are snapped to the visual intersection of the two drawn lines —
        i.e. the adjacent segment's NUTS track position — instead of conn.at_pos
        which is the original geometry coordinate.

        seg_widths (dict seg_idx→physical bus width): when provided (NUTS view),
        markers carry the connected segments' physical extent so their size
        tracks the drawn segment widths at every zoom (a via spans the junction
        of two segments → the wider of the two).
        """
        def _phys(*idxs):
            if not seg_widths:
                return None
            ws = [seg_widths[i] for i in idxs if i in seg_widths]
            return max(ws) if ws else None

        for conn in cs.conns:
            if conn.kind == ic.SegConnKind.SEG:
                # Draw each via only once — from the lower-indexed segment.
                if conn.seg_idx < seg_idx:
                    continue
                # NUTS alignment: via at the visual intersection of both lines.
                if adj_perp is not None and conn.seg_idx in adj_perp:
                    if cs.horiz:
                        cx, cy = adj_perp[conn.seg_idx], sy
                    else:
                        cx, cy = sx, adj_perp[conn.seg_idx]
                    self._draw_via_marker(bid, cx, cy, msz, alpha, zorder,
                                          layer=layer,
                                          phys=_phys(seg_idx, conn.seg_idx))
                    continue
            if cs.horiz:
                cx, cy = conn.at_pos + along_offset, sy
            else:
                cx, cy = sx, conn.at_pos + along_offset
            if conn.kind == ic.SegConnKind.BUSTERM:
                self._draw_busterm_conn(bid, cx, cy, col, msz, alpha, zorder,
                                        layer=layer, phys=_phys(seg_idx))
            else:
                self._draw_via_marker(bid, cx, cy, msz, alpha, zorder,
                                      layer=layer,
                                      phys=_phys(seg_idx, conn.seg_idx))


    def draw_buses(self):
        """Draw topology segments without NUTS track assignment."""
        self._busterm_artists = []
        self._vias_conns_artists = []
        layer_specs = {k: {'color': v} for k, v in _LAYER_COLOR.items()}
        for i, wrapper in enumerate(self.bundles):
            bid      = wrapper.input.original_bundle.id
            if not wrapper.input.candidates or wrapper.plan.selected_topology_index < 0 or wrapper.plan.selected_topology_index >= len(wrapper.input.candidates):
                continue
            topo     = wrapper.input.candidates[wrapper.plan.selected_topology_index]
            viz_lw   = 3.0 + math.log2(1 + wrapper.input.width) * 2.0
            offset   = (i % 3 - 1) * 2.0
            alpha    = 0.8

            ct = ic.ConnTopology(); ct.build(topo, self.fp)
            cs_list = list(ct.segs())
            msz = max(4, viz_lw)
            for idx, seg in enumerate(topo.segments):
                spec = layer_specs.get(seg.layer_hint, {'color': 'green'})
                col  = spec['color']
                sx = seg.start.x + offset;  sy = seg.start.y + offset
                ex = seg.end.x   + offset;  ey = seg.end.y   + offset

                line, = self.ax.plot([sx, ex], [sy, ey],
                                     color=col, linewidth=viz_lw,
                                     solid_capstyle='butt', alpha=alpha,
                                     zorder=10 + i)
                self._register(bid, line, alpha=alpha, lw=viz_lw,
                                layer=seg.layer_hint)

                self._draw_seg_connectors(bid, idx, cs_list[idx], sx, sy, col,
                                          msz, alpha, 12 + i, along_offset=offset,
                                          layer=seg.layer_hint)

            drv, rcvs = self._busterm_positions(topo, ct, offset=offset)
            bidir = wrapper.input.original_bundle.reason.startswith("BIDIR:")
            self._draw_terminals(bid, drv, rcvs, viz_lw, alpha, bidir=bidir)


    def draw_nuts_tracks(self, nuts_result):
        """Draw segments at NUTS-assigned track positions with interval bands."""
        self._busterm_artists = []
        self._vias_conns_artists = []
        self._nuts_result = nuts_result   # saved for overlap panel in show()
        layer_specs = {k: {'color': v} for k, v in _LAYER_COLOR.items()}
        ts_map = {(ts.bundle_id, ts.seg_idx): ts for ts in nuts_result.segments}
        band_alpha = 0.04
        seg_alpha  = 0.90

        # Interval bands (footprints + dashed bounds) are pure-visual `is_band`
        # artists that the highlight overlay skips, so we batch them into one
        # collection per (bundle, layer, colour) instead of 3 artists/segment.
        fp_groups = {}   # (bid, layer, col) -> [Rectangle]
        bd_groups = {}   # (bid, layer, col) -> [segment]

        for i, wrapper in enumerate(self.bundles):
            bid    = wrapper.input.original_bundle.id
            if not wrapper.input.candidates or wrapper.plan.selected_topology_index < 0 or wrapper.plan.selected_topology_index >= len(wrapper.input.candidates):
                continue
            topo   = wrapper.input.candidates[wrapper.plan.selected_topology_index]
            viz_lw = 3.0 + math.log2(1 + wrapper.input.width) * 2.0
            msz     = max(4, viz_lw)
            ct      = ic.ConnTopology(); ct.build(topo, self.fp)
            cs_list = list(ct.segs())
            # adj_perp: seg_idx → NUTS track_position, used to snap vias to
            # the visual intersection of the two drawn lines.
            # seg_widths: seg_idx → physical bus width, so via/conn markers can
            # track the drawn segment widths at every zoom.
            adj_perp = {}
            seg_widths = {}
            for j in range(len(topo.segments)):
                adj_ts = ts_map.get((bid, j))
                if adj_ts and adj_ts.placed:
                    adj_perp[j] = adj_ts.track_position
                    if adj_ts.width > 0:
                        seg_widths[j] = adj_ts.width

            for idx, seg in enumerate(topo.segments):
                ts   = ts_map.get((bid, idx))
                # Use ts.layer (NUTS-assigned, honours assigned_v_layer) when
                # available; fall back to seg.layer_hint for pre-NUTS drawing.
                effective_layer = ts.layer if ts else seg.layer_hint
                spec = layer_specs.get(effective_layer, {'color': 'green'})
                col  = spec['color']
                fp_key = (bid, effective_layer, col)

                if ts and ts.placed:
                    half   = ts.width / 2.0
                    center = ts.track_position
                    is_h   = (seg.start.y == seg.end.y)

                    if is_h:
                        sx, ex = ts.span_lo, ts.span_hi
                        sy = ey = center
                        fp_groups.setdefault(fp_key, []).append(patches.Rectangle(
                            (sx, center - half), ex - sx, ts.width,
                            linewidth=0, facecolor=col))
                        for y_bound in (ts.interval_lo, ts.interval_hi):
                            bd_groups.setdefault(fp_key, []).append(
                                [(min(sx,ex), y_bound), (max(sx,ex), y_bound)])
                    else:
                        sy, ey = ts.span_lo, ts.span_hi
                        sx = ex = center
                        fp_groups.setdefault(fp_key, []).append(patches.Rectangle(
                            (center - half, sy), ts.width, ey - sy,
                            linewidth=0, facecolor=col))
                        for x_bound in (ts.interval_lo, ts.interval_hi):
                            bd_groups.setdefault(fp_key, []).append(
                                [(x_bound, min(sy,ey)), (x_bound, max(sy,ey))])
                else:
                    sx, sy = seg.start.x, seg.start.y
                    ex, ey = seg.end.x,   seg.end.y

                line, = self.ax.plot([sx, ex], [sy, ey],
                                     color=col, linewidth=viz_lw,
                                     solid_capstyle='butt',
                                     alpha=seg_alpha, zorder=10 + i)
                # Placed segments carry their physical width so the drawn
                # point-width can be fit to the true footprint at every zoom
                # (_sync_nuts_linewidths) — a fixed point-width line overhangs
                # its busterm face when zoomed out (home view).
                if ts and ts.placed and ts.width > 0:
                    self._register(bid, line, alpha=seg_alpha, lw=viz_lw,
                                   layer=effective_layer,
                                   phys_w=ts.width, horiz=is_h)
                else:
                    self._register(bid, line, alpha=seg_alpha, lw=viz_lw,
                                   layer=effective_layer)

                self._draw_seg_connectors(bid, idx, cs_list[idx], sx, sy, col,
                                          msz, seg_alpha, 12 + i,
                                          adj_perp=adj_perp,
                                          layer=effective_layer,
                                          seg_widths=seg_widths)

            drv, rcvs = self._busterm_positions(topo, ct, ts_map=ts_map, bid=bid)
            bidir = wrapper.input.original_bundle.reason.startswith("BIDIR:")
            self._draw_terminals(bid, drv, rcvs, viz_lw, seg_alpha, bidir=bidir)

        # Emit one footprint PatchCollection + one dashed-bound LineCollection
        # per (bundle, layer, colour) group.
        for (bid, layer, col), rects in fp_groups.items():
            pc = PatchCollection(rects, match_original=True, alpha=band_alpha * 3, zorder=5)
            self.ax.add_collection(pc)
            self._register(bid, pc, alpha=band_alpha * 3, is_band=True, layer=layer)
        for (bid, layer, col), segs in bd_groups.items():
            lc = LineCollection(segs, colors=col, linewidths=0.5, linestyles='--',
                                alpha=0.3, zorder=4)
            self.ax.add_collection(lc)
            self._register(bid, lc, alpha=0.3, is_band=True, layer=layer)

        # Keep the physical-width lines zoom-true from now on.
        self._sync_nuts_linewidths()
        self._hook_lw_sync()


    def _draw_terminals(self, bundle_id, drv_pos, rcv_positions, viz_lw, alpha,
                        bidir=False):
        """Draw driver (cyan square) and receiver (magenta circle) terminals.

        rcv_positions may be a single (x,y) or a list of (x,y).
        viz_lw may be the physical bus width (large for wide buses in NUTS view),
        so marker size is capped to stay visually reasonable.

        `bidir` (a BIDIRECTIONAL-strategy bundle) instead draws EVERY terminal as
        a green diamond: routing is direction-agnostic and each endpoint both
        drives and receives, so the driver/receiver split would mislabel them.
        """
        msz = max(6, min(viz_lw, 16))
        new_artists = []
        if bidir:
            positions = []
            if drv_pos:
                positions.append(drv_pos)
            if rcv_positions is not None:
                positions += ([rcv_positions] if isinstance(rcv_positions, tuple)
                              else list(rcv_positions))
            for pos in positions:
                if pos is None:
                    continue
                m, = self.ax.plot(pos[0], pos[1], 'D', color='#22C55E',
                                  markeredgecolor='black', markersize=msz,
                                  alpha=alpha, zorder=20)
                self._register(bundle_id, m, alpha=alpha, lw=msz)
                new_artists.append(m)
            if positions and positions[0] is not None:
                lbl = self.ax.text(positions[0][0], positions[0][1], f"B{bundle_id}",
                                   fontsize=8, color='black', fontweight='bold',
                                   ha='center', va='center', zorder=21, clip_on=True)
                lbl.set_alpha(alpha)
                self._register(bundle_id, lbl, alpha=alpha)
                new_artists.append(lbl)
            self._busterm_artists.extend(new_artists)
            if not self.ui_state.busterms:
                for a in new_artists:
                    a.set_visible(False)
            return
        if drv_pos:
            drv, = self.ax.plot(drv_pos[0], drv_pos[1], 's',
                                color='#00FFFF', markeredgecolor='black',
                                markersize=msz, alpha=alpha, zorder=20)
            self._register(bundle_id, drv, alpha=alpha, lw=msz)
            new_artists.append(drv)
            lbl = self.ax.text(drv_pos[0], drv_pos[1], f"B{bundle_id}",
                               fontsize=8, color='black', fontweight='bold',
                               ha='center', va='center', zorder=21, clip_on=True)
            lbl.set_alpha(alpha)
            self._register(bundle_id, lbl, alpha=alpha)
            new_artists.append(lbl)

        if rcv_positions is not None:
            # Accept single tuple or list of tuples
            if isinstance(rcv_positions, tuple):
                rcv_positions = [rcv_positions]
            for pos in rcv_positions:
                if pos is None:
                    continue
                rcv, = self.ax.plot(pos[0], pos[1], 'o',
                                    color='#FF00FF', markeredgecolor='black',
                                    markersize=msz, alpha=alpha, zorder=20)
                self._register(bundle_id, rcv, alpha=alpha, lw=msz)
                new_artists.append(rcv)

        self._busterm_artists.extend(new_artists)
        # Apply current visibility state to newly created artists
        if not self.ui_state.busterms:
            for a in new_artists:
                a.set_visible(False)
