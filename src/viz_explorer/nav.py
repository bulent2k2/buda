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

"""TopologyExplorer mixin — Keyboard navigation, zoom, and the re-run refresh.

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

class ExplorerNavMixin:

    # ------------------------------------------------------------------

    def _reset_rerun_btn(self):
        if self._btn_rerun is not None:
            self._btn_rerun.label.set_text('▶  Re-run')
            self._btn_rerun.ax.set_facecolor('#ffe0b0')


    def _step_topo(self, delta):
        self.idx = (self.idx + delta) % len(self.topos)
        self.sidx = -1
        self._reset_rerun_btn()
        self._draw()


    def _step_bundle(self, delta):
        self.bidx = (self.bidx + delta) % len(self.wrappers)
        self.idx  = self._focus_topo_index()   # jump to this bundle's pinned topo
        self.sidx = -1
        self._autoscale_needed = True
        self._reset_rerun_btn()
        self._draw()


    def show_bundle_index(self, idx):
        """Jump to a specific bundle (used when the explorer is already open and
        the parent viz wants to focus a different highlighted bundle)."""
        if 0 <= idx < len(self.wrappers) and idx != self.bidx:
            self.bidx = idx
            self.idx  = self._focus_topo_index()   # jump to its pinned topo
            self.sidx = -1
            self._autoscale_needed = True
            self._reset_rerun_btn()
            self._draw()


    def _on_key(self, event):
        # ── TopoEdit mode (Phase E3b): e/E open a session (copy/empty); while
        # open, T/Y add an H/V trunk at the cursor's Hanan line, S stubs the
        # block under the cursor to the selected segment, C/D pair-connect/
        # -disconnect, X removes, W/W refines the selected segment's slide
        # window at the cursor (two bounds; 'w' clears), enter commits (+pin),
        # escape aborts.  Candidate/bundle navigation is parked while editing.
        if event.key == 'e':                    self._edit_open(empty=False); return
        if event.key == 'E':                    self._edit_open(empty=True); return
        if self._edit_topo is not None:
            if event.key == 'T':                self._edit_add_trunk_at(event, horiz=True); return
            if event.key == 'Y':                self._edit_add_trunk_at(event, horiz=False); return
            if event.key == 'S':                self._edit_add_stub_at(event); return
            if event.key == 'C':                self._edit_pair_op(event, connect=True); return
            if event.key == 'D':                self._edit_pair_op(event, connect=False); return
            if event.key == 'W':                self._edit_slide_at(event); return
            if event.key == 'w':                self._edit_slide_clear(); return
            if event.key == 'X':
                if 0 <= self.sidx < len(self._edit_topo.segments):
                    si = self.sidx
                    self.sidx = -1
                    if self._edit_apply(ic.edit_remove_segment(
                            self._edit_topo, self.fp, si)):
                        # Indices above si shifted down: remap the staged
                        # slide windows, dropping the removed segment's own.
                        self._edit_slide = {
                            (i - 1 if i > si else i): v
                            for i, v in self._edit_slide.items() if i != si}
                        self._edit_slide_mark = None
                else:
                    self._edit_msg = "EDIT: select a segment first (j/k)"
                    self._draw()
                return
            if event.key == 'enter':            self._edit_commit(); return
            if event.key == 'escape':           self._edit_close("EDIT: aborted"); return
            if event.key in ('a', 'd', 'n', 'p', '[', ']', 'pageup', 'pagedown',
                             'cmd+n', 'ctrl+n', 'cmd+p', 'ctrl+p'):
                self._edit_msg = "EDIT: finish the session first (enter/esc)"
                self._draw(); return
        if event.key in ('cmd+q', 'ctrl+q'):    plt.close('all'); return
        if event.key in ('f', 'cmd+f', 'ctrl+f'): viz_window._toggle_fullscreen(self.fig); return
        # 'v' mirrors cmd/ctrl-1 (raise the main BUDA viz window) — since the
        # main window's 'v' opens/raises this explorer, tapping 'v' cycles
        # between the two windows.  Before switching back, hand the current
        # bundle to the main view so a [ / ] page here becomes its selection —
        # the two windows stay in sync in both directions.
        if event.key in ('v', 'cmd+1', 'ctrl+1'):
            if self._on_focus_bundle is not None and self.wrappers:
                self._on_focus_bundle(
                    self.wrappers[self.bidx].input.original_bundle.id)
            if self._main_fig is not None: viz_window.raise_window(self._main_fig)
            return
        if event.key in ('cmd+z', 'ctrl+z'):    self._zoom_to_bundle(); return
        if event.key == 'z':                    self._interactive_zoom(event, zoom_in=True); return
        if event.key == 'Z':                    self._interactive_zoom(event, zoom_in=False); return
        if event.key in ('h', 'H', 'cmd+a', 'ctrl+a'): self._zoom_home(); return
        # Arrow keys pan the view (see below); topo/segment nav uses the letter
        # aliases a/d (prev/next topology) and k/j (prev/next segment).
        if event.key == 'left':   _pan_axes(self.ax, self.fig, -_PAN_STEP, 0); return
        if event.key == 'right':  _pan_axes(self.ax, self.fig, +_PAN_STEP, 0); return
        if event.key == 'up':     _pan_axes(self.ax, self.fig, 0, +_PAN_STEP); return
        if event.key == 'down':   _pan_axes(self.ax, self.fig, 0, -_PAN_STEP); return
        if event.key == 'a':                    self._step_topo(-1)
        if event.key == 'd':                    self._step_topo(+1)
        if event.key in ('n', 'cmd+n', 'ctrl+n'):    self._step_topo(+1)
        if event.key in ('p', 'cmd+p', 'ctrl+p'):    self._step_topo(-1)
        if event.key in ('[', 'pageup'):        self._step_bundle(-1)
        if event.key in (']', 'pagedown'):      self._step_bundle(+1)
        if event.key == 'k':                    self._step_segment(-1)
        if event.key == 'j':                    self._step_segment(+1)
        # Layer up/down use the symbol keys only.  The letter aliases were
        # dropped: 'd' was double-bound (next-topology AND layer-down), and 'u'
        # was its orphaned layer-up partner.
        if event.key in ('+', '='):             self._cycle_layer(+1)
        if event.key in ('-', '_'):             self._cycle_layer(-1)
        if event.key == 'b':                    self.ui_state.toggle_blocks()
        if event.key == 'g':                    self.ui_state.toggle_hanan_grid()
        if event.key == 't':                    self.ui_state.toggle_busterms()
        if event.key == 's':
            if self._current_is_selected(): self._deselect_current()
            else:                           self._select_current()
        if event.key == 'x':                    self._deselect_current()
        if event.key == 'r':                    self._rerun_and_refresh()


    def _step_segment(self, delta):
        topo = self._shown_topo()
        n = len(topo.segments)
        if n == 0: return
        self.sidx = (self.sidx + delta) % n
        self._draw()


    def _cycle_layer(self, delta):
        if self.sidx == -1 or self.layer_stack is None:
            return
        wrapper = self.wrappers[self.bidx]
        topo = wrapper.input.candidates[self.idx]
        seg = topo.segments[self.sidx]
        is_h = (seg.start.y == seg.end.y)

        # Get compatible layers based on segment orientation.
        dir = ic.LayerDir.HORIZONTAL if is_h else ic.LayerDir.VERTICAL
        lids = list(self.layer_stack.get_layer_ids_by_dir(dir))
        if not lids: return

        # Resolve current layer ID using same precedence as _draw.
        sel = self._find_selection()
        is_current_selection = self._current_is_selected()
        is_planner_active = (self.idx == wrapper.plan.selected_topology_index)
        
        curr = -1
        if is_current_selection and sel and 'seg_layers' in sel:
            pinned = sel['seg_layers']
            if len(pinned) == len(topo.segments):
                curr = pinned[self.sidx]
        
        if curr == -1 and is_planner_active:
            if len(wrapper.plan.seg_layers) == len(topo.segments):
                curr = wrapper.plan.seg_layers[self.sidx]
        
        if curr == -1:
            curr = seg.layer_hint

        # Find index in compatible lids.
        try:
            lidx = lids.index(curr)
        except ValueError:
            # Current layer doesn't match orientation (unlikely but possible via hints)
            # Find closest match or fallback.
            lidx = 0

        new_lidx = (lidx + delta) % len(lids)
        new_lid = lids[new_lidx]

        # Update pinned_seg_layers scratchpad.
        pinned_list = list(wrapper.input.pinned_seg_layers)
        if len(pinned_list) != len(topo.segments):
            # Start from current actual layers.
            pinned_list = []
            for i, s in enumerate(topo.segments):
                l = -1
                if is_planner_active and len(wrapper.plan.seg_layers) == len(topo.segments):
                    l = wrapper.plan.seg_layers[i]
                else:
                    l = s.layer_hint
                pinned_list.append(l)

        pinned_list[self.sidx] = new_lid
        wrapper.input.pinned_seg_layers = pinned_list

        # Selection logic now automatically persists this to sidecar.
        self._select_current()


    def _rerun_and_refresh(self):
        """Select current topology, re-run NUTS, and refresh the main viz."""
        if self._rerun_fn is None:
            return
        # Persist the currently displayed topology as the selection for this bundle.
        self._select_current()
        # Visual feedback while running.
        if self._btn_rerun is not None:
            self._btn_rerun.label.set_text('Running…')
            self._btn_rerun.ax.set_facecolor('#ffcc88')
            self.fig.canvas.draw_idle()
            self.fig.canvas.flush_events()
        try:
            result = self._rerun_fn()
        except Exception as e:
            print(f"[Viz] Re-run failed: {e}")
            result = None
        if self._btn_rerun is not None:
            if result is not None:
                self._btn_rerun.label.set_text('✓  Done')
                self._btn_rerun.ax.set_facecolor('#c8f0c8')
            else:
                self._btn_rerun.label.set_text('✗  Error')
                self._btn_rerun.ax.set_facecolor('#f0c8c8')
            self.fig.canvas.draw_idle()
        if result is not None and self._refresh_fn is not None:
            self._refresh_fn(result)


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


    def _zoom_to_bundle(self, _=None):
        """Zoom axes to the bounding box of the active bundle's terminals/topology."""
        topo = self.topos[self.idx]
        ct = self._build_conn_topo(topo)
        xs, ys = [], []
        # Add topology endpoints
        for seg in topo.segments:
            xs.extend([float(seg.start.x), float(seg.end.x)])
            ys.extend([float(seg.start.y), float(seg.end.y)])
        # Add busterm connection positions
        for cs in ct.segs():
            display_perp = self._centered_perp(cs)
            for conn in cs.conns:
                if conn.kind != ic.SegConnKind.BUSTERM:
                    continue
                if cs.horiz:
                    px, py = conn.at_pos, display_perp
                else:
                    px, py = display_perp, conn.at_pos
                xs.append(float(px))
                ys.append(float(py))

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
