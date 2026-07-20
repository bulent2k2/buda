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

"""BudaVisualizer mixin — Camera (home/zoom/keys) and the TopologyExplorer bridge.

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

class VizViewMixin:

    def _reset_view(self):
        """Force all bundles and layers to visible, and clear selection."""
        self._highlighted      = None
        self._highlighted_set  = set()
        self._selected_overlap = None
        self._overlap_state    = 0
        
        # Reset ViewState to defaults (most ON, Hanan OFF)
        self.ui_state.solo = False
        self.ui_state.all_vis = True
        self.ui_state.blocks = True
        self.ui_state.block_names = True
        self.ui_state.heatmap = True
        self.ui_state.keepouts = True
        self.ui_state.busterms = True
        self.ui_state.vias_conns = True
        self.ui_state.hanan_grid = False
        
        if self._btn_solo is not None:
             self._btn_solo.label.set_text("Solo OFF")
             self._btn_solo.ax.set_facecolor('#f0f0f0')

        # Redraw all via notification
        self.ui_state.notify()
        self.ui_state.tracks = False

        # Toggle All logic (forced to True)
        if self._btn_all is not None:
            self._btn_all.label.set_text('☑ All')

        for lid in self._layer_visible:
            self._layer_visible[lid] = True
        if self._btn_all_layers is not None:
            self._btn_all_layers.label.set_text('☑ All Layers')
            self._btn_all_layers.ax.set_facecolor('#e8e8e8')

        for bid in self._bundle_visible:
            self._bundle_visible[bid] = True
        if self._btn_all_bundles is not None:
            self._btn_all_bundles.ax.set_facecolor('#e8e8e8')

        # Artist visibility
        for a in self._heatmap_artists: a.set_visible(True)
        if self._cbar_ax: self._cbar_ax.set_visible(True)
        for a in self._keepout_artists: a.set_visible(True)
        self._redraw_blocks()   # restores default (all blocks equal) style
        self._apply_busterm_visibility()      # terminals hidden in detailed mode
        self._apply_vias_conns_visibility()   # abstract vias hidden in detailed mode
        for e in self._grid_rail_artists:
             e['artist'].set_visible(self.ui_state.detailed_mode and self.ui_state.tracks)

        # Update button labels
        if self._btn_heatmap is not None: self._btn_heatmap.label.set_text('☑ Heatmap')
        if self._btn_keepouts is not None: self._btn_keepouts.label.set_text('☑ Keepouts')
        if self._btn_blocks is not None: self._btn_blocks.label.set_text('☑ Blocks')
        if self._btn_blknames is not None: self._btn_blknames.label.set_text('☑ Blk Names')
        if self._btn_bustermss is not None: self._btn_bustermss.label.set_text('☑ Busterms')
        if self._btn_vias_conns is not None: self._btn_vias_conns.label.set_text('☑ Vias/Conns')
        if self._btn_tracks is not None:
             self._btn_tracks.label.set_text('☐ Tracks')
             self._btn_tracks.ax.set_facecolor('#e8f4e8')

        self._redraw_layer_list()
        self._redraw_bundle_list()
        self._refresh_highlight()
        self._refresh_topo_explorer()   # layers reset to all-visible
        self.fig.canvas.draw_idle()


    def _refresh_topo_explorer(self):
        """Redraw the topology explorer, if open, so it picks up shared state
        (e.g. layer visibility) that isn't routed through ui_state.notify()."""
        exp = self._topo_explorer
        if exp is not None and plt.fignum_exists(exp.fig.number):
            exp.fig_redraw()


    # ------------------------------------------------------------------

    def _topo_start_index(self, wrappers, bid):
        """Index in `wrappers` of bundle `bid` (every candidate-bearing bundle
        is listed, so this normally hits directly). Falls back to a same-cell
        bundle, then 0, only if `bid` itself has no candidates."""
        idx = next((i for i, w in enumerate(wrappers)
                    if w.input.original_bundle.id == bid), None)
        if idx is not None:
            return idx
        # `bid` has no candidates of its own (not in `wrappers`) — land on a
        # sibling in the same cell if there is one, else the first bundle.
        hl = next((w.input.original_bundle for w in self.bundles
                   if w.input.original_bundle.id == bid), None)
        if hl is not None and hl.cell_context:
            key = (hl.cell_context, hl.reason)
            idx = next((i for i, w in enumerate(wrappers)
                        if w.input.original_bundle.cell_context
                        and (w.input.original_bundle.cell_context,
                             w.input.original_bundle.reason) == key), None)
        return idx if idx is not None else 0


    def _open_topo_explorer(self):
        if self._highlighted is None:
            # Automatically select the first bundle that has candidates.
            for w in self.bundles:
                if w.input.candidates:
                    self._highlighted = w.input.original_bundle.id
                    self._refresh_highlight()
                    break

        if self._highlighted is None:
            return

        # Load *all* candidate-bearing bundles so the ◀/▶ Bundle buttons can page
        # through them, opening on the currently highlighted bundle.
        wrappers, _ = collect_candidate_bundles(self.bundles)
        if not wrappers:
            return
        start = self._topo_start_index(wrappers, self._highlighted)

        # Singleton Pattern: one explorer covers all bundles. If already open,
        # just raise it and jump to the highlighted bundle.
        if self._topo_explorer is not None and plt.fignum_exists(self._topo_explorer.fig.number):
            viz_window.raise_window(self._topo_explorer.fig)
            self._topo_explorer.show_bundle_index(start)
            return

        # Late import: buda_viz composes this mixin, so a module-level import
        # would be circular; the explorer class lives on the assembled facade.
        from buda_viz import TopologyExplorer

        refresh_fn = self._redraw_nuts_tracks if self._rerun_fn is not None else None
        self._topo_explorer = TopologyExplorer(
            self.fp, wrappers,
            sidecar_path=self._selections_path,
            main_fig=self.fig,
            rerun_fn=self._rerun_fn,
            refresh_fn=refresh_fn,
            layer_stack=self.layer_stack,
            ui_state=self.ui_state,
            start_bidx=start,
            layer_visible=self._layer_visible,
            on_focus_bundle=self._adopt_explorer_bundle,
            bundle_order_fn=lambda: self._bid_list,   # opens-first panel order
            fp_resolver=self._fp_resolver,            # hier per-bundle frames
            user_ops_sink=self._user_ops_sink)        # BDB op-log provenance
        self._topo_explorer.fig.show()
        viz_window.install_tk_geometry_resync(self._topo_explorer.fig)
        viz_window.extract_from_fullscreen_tab(self._topo_explorer.fig)


    def _adopt_explorer_bundle(self, bundle_id):
        """Explorer return-hook ('v' back to this window): select the bundle
        the explorer is showing, so a [ / ] page there becomes the selection
        here and the two windows stay in sync."""
        if bundle_id == self._highlighted:
            return
        if bundle_id in self._bid_list:
            self._scroll_bundle_into_view(self._bid_list.index(bundle_id))
        self._set_highlight(bundle_id)


    def _recompute_home_bbox(self):
        """Refresh the cached home extent from the LIVE design artists (blocks +
        the current route: abstract track / busterm / via lines and detailed
        bit-wire collections), so a later 'h' fits the CURRENT design after a
        re-run that pinned a different-extent topology.

        Computed explicitly, NOT via autoscale_view(): Matplotlib's Axes.dataLim
        never shrinks when artists are removed, so autoscaling would keep fitting
        a previous, larger topology after re-routing to a smaller one (Codex #242).
        Reserved bands (detour channel, pre-routes) are intentionally excluded —
        'h' frames the design, not the empty reservation. Cache-only: the current
        view is untouched (this does not move the camera)."""
        import numpy as np

        def _extent(a):
            if hasattr(a, 'get_xdata'):                       # Line2D
                xd = np.asarray(a.get_xdata(), float)
                yd = np.asarray(a.get_ydata(), float)
                if xd.size and yd.size:
                    return xd.min(), xd.max(), yd.min(), yd.max()
                return None
            if hasattr(a, 'get_width') and hasattr(a, 'get_x'):   # Rectangle (block)
                x, y = a.get_x(), a.get_y()
                w, h = a.get_width(), a.get_height()
                return min(x, x + w), max(x, x + w), min(y, y + h), max(y, y + h)
            if hasattr(a, 'get_paths'):                       # Line/Path Collection
                pts = [np.asarray(p.vertices, float) for p in a.get_paths()
                       if len(p.vertices)]
                off = np.asarray(a.get_offsets(), float)
                if off.ndim == 2 and off.size:
                    pts.append(off)
                if pts:
                    v = np.vstack(pts)
                    return v[:, 0].min(), v[:, 0].max(), v[:, 1].min(), v[:, 1].max()
            return None

        arts = list(self._block_patch_artists)
        for reg in (self._bundle_artists, self._detailed_bundle_artists):
            for entries in reg.values():
                arts.extend(e['artist'] for e in entries)

        x0 = x1 = y0 = y1 = None
        for a in arts:
            ext = _extent(a)
            if ext is None or not all(np.isfinite(ext)):
                continue
            ax0, ax1, ay0, ay1 = ext
            x0 = ax0 if x0 is None else min(x0, ax0)
            x1 = ax1 if x1 is None else max(x1, ax1)
            y0 = ay0 if y0 is None else min(y0, ay0)
            y1 = ay1 if y1 is None else max(y1, ay1)
        if x0 is None:
            return                              # nothing to fit — keep the old cache
        mx = 0.05 * (x1 - x0) if x1 > x0 else 1.0
        my = 0.05 * (y1 - y0) if y1 > y0 else 1.0
        self._home_data_bbox = (x0 - mx, x1 + mx, y0 - my, y1 + my)


    def _zoom_home(self):
        if self._home_data_bbox is not None:
            # Recompute the maximal fill from the cached data bbox so the home
            # view stays maximal even if the window was resized since first draw.
            _set_lims_filling_box(self.ax, *self._home_data_bbox)
            # Refresh the stored home so _install_home_fit_tracking recognizes
            # this as home again — otherwise, after a pan → 'h', its guard would
            # compare later resizes against the stale pre-pan tuple and stop
            # keeping the view maximal.
            self._home_xlim = self.ax.get_xlim()
            self._home_ylim = self.ax.get_ylim()
            toolbar = getattr(self.fig.canvas, 'toolbar', None)
            if toolbar is not None:
                toolbar.update()   # reset toolbar nav stack to current limits
        elif self._home_xlim is not None:
            self.ax.set_xlim(self._home_xlim)
            self.ax.set_ylim(self._home_ylim)
            toolbar = getattr(self.fig.canvas, 'toolbar', None)
            if toolbar is not None:
                toolbar.update()   # reset toolbar nav stack to current limits
        else:
            self.ax.autoscale()
        self.fig.canvas.draw_idle()


    def _on_key(self, event):
        if event.key in ('cmd+q', 'ctrl+q'): plt.close('all'); return
        if event.key in ('f', 'cmd+f', 'ctrl+f'): viz_window._toggle_fullscreen(self.fig); return
        if event.key in ('cmd+z', 'ctrl+z'): self._zoom_to_bundle(); return
        if event.key == 'z': self._interactive_zoom(event, zoom_in=True); return
        if event.key == 'Z': self._interactive_zoom(event, zoom_in=False); return
        if event.key in ('h', 'H', 'cmd+a', 'ctrl+a'): self._zoom_home(); return
        if event.key == 'a':
            if self._highlighted is not None:
                self._last_highlighted = self._highlighted
                if self.ui_state.detailed_mode:
                    self._set_highlight(None)
                else:
                    self._reset_view()
            else:
                if getattr(self, '_last_highlighted', None) is not None:
                    self._set_highlight(self._last_highlighted)
            return
        if event.key == 'left':   _pan_axes(self.ax, self.fig, -_PAN_STEP, 0); return
        if event.key == 'right':  _pan_axes(self.ax, self.fig, +_PAN_STEP, 0); return
        if event.key == 'up':     _pan_axes(self.ax, self.fig, 0, +_PAN_STEP); return
        if event.key == 'down':   _pan_axes(self.ax, self.fig, 0, -_PAN_STEP); return
        if event.key in ('n', 'cmd+n', 'ctrl+n'): self._step_bundle(+1)
        if event.key in ('p', 'cmd+p', 'ctrl+p'): self._step_bundle(-1)
        if event.key in ('[', 'pageup'):   self._step_bundle(-1)
        if event.key in (']', 'pagedown'): self._step_bundle(+1)
        if event.key in ('v', 'cmd+t', 'ctrl+t'): self._open_topo_explorer()
        if event.key == 'b':                  self._toggle_blocks()
        if event.key == 't':                  self._toggle_bustermss()
        if event.key == 'g':                  self._toggle_hanan()
        if event.key == 's':                  self._toggle_solo()
        if event.key == 'd' and self._has_detailed_data: self._toggle_detailed()


    def _zoom_to_bundle(self, _=None):
        """Zoom axes to the bounding box of the selected bundle, or reset to the
        maximal full view when nothing is selected."""
        from matplotlib.lines import Line2D as MplLine2D
        bid = self._highlighted
        if bid is None:
            # No selection → reset to the maximal home fill (same as 'h'), NOT
            # ax.autoscale() — under set_aspect('equal') that collapses to a
            # non-maximal sliver.  _zoom_home also keeps the resize-tracking
            # guard (_install_home_fit_tracking) in sync.
            self._zoom_home()
            return
        xs, ys = [], []
        for e in self._bundle_artists.get(bid, []):
            a = e['artist']
            if e['is_band'] or not isinstance(a, MplLine2D):
                continue
            xs.extend(a.get_xdata(orig=False))
            ys.extend(a.get_ydata(orig=False))
        # Include the bundle's busterm connection points so the driver/receiver
        # terminals are framed even when terminal markers are toggled off.
        w = next((w for w in self.bundles
                  if w.input.original_bundle.id == bid), None)
        if w and w.input.candidates and \
                0 <= w.plan.selected_topology_index < len(w.input.candidates):
            topo = w.input.candidates[w.plan.selected_topology_index]
            ct = ic.ConnTopology(); ct.build(topo, self.fp)
            for cs in ct.segs():
                for conn in cs.conns:
                    if conn.kind != ic.SegConnKind.BUSTERM:
                        continue
                    if cs.horiz:
                        xs.append(float(conn.at_pos)); ys.append(float(cs.perp_pos))
                    else:
                        xs.append(float(cs.perp_pos)); ys.append(float(conn.at_pos))
        if not xs:
            return
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        pad_x = max((x1 - x0) * 0.2, 50)
        pad_y = max((y1 - y0) * 0.2, 50)
        _set_lims_filling_box(self.ax, x0 - pad_x, x1 + pad_x,
                              y0 - pad_y, y1 + pad_y)
        self.fig.canvas.draw_idle()


    def _interactive_zoom(self, event, zoom_in: bool):
        scale = 0.8 if zoom_in else 1.25
        ax = self.ax
        x, y = event.xdata, event.ydata
        if event.inaxes != ax or x is None or y is None:
            xlim = ax.get_xlim()
            ylim = ax.get_ylim()
            x = (xlim[0] + xlim[1]) / 2
            y = (ylim[0] + ylim[1]) / 2

        xlim = ax.get_xlim()
        ylim = ax.get_ylim()
        
        new_xlim = (x - (x - xlim[0]) * scale, x + (xlim[1] - x) * scale)
        new_ylim = (y - (y - ylim[0]) * scale, y + (ylim[1] - y) * scale)
        
        ax.set_xlim(new_xlim)
        ax.set_ylim(new_ylim)
        self.fig.canvas.draw_idle()


    def _on_close(self, event):
        """Cleanup when the main window is closed."""
        # Deregister our ViewState listener (audit P6-02): the ViewState is
        # shared with the TopologyExplorer, so a stale fig_redraw left
        # registered here fires against a destroyed figure whenever the
        # explorer notifies after this window closes. Mirrors the explorer's
        # own _on_close.
        if getattr(self, 'ui_state', None) is not None:
            try:
                self.ui_state.remove_listener(self.fig_redraw)
            except Exception:
                pass
        if hasattr(self, '_ipc_timer') and self._ipc_timer is not None:
            try:
                self._ipc_timer.stop()
            except Exception:
                pass
            self._ipc_timer = None
        if hasattr(self, '_ipc') and self._ipc is not None:
            try:
                self._ipc.close()
            except Exception:
                pass
            self._ipc = None
