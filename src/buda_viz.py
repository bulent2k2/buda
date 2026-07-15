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

import json
import math
import os
import re
import sys
import types
from datetime import datetime

import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.collections import PatchCollection, LineCollection
from matplotlib.widgets import Button

import buda as ic
from ui_state import ViewState
from viz_common import *          # noqa: F401,F403 — shared helpers (re-exported)
from viz_window import (          # noqa: F401 — window glue (re-exported)
    raise_window, install_tk_geometry_resync, extract_from_fullscreen_tab,
    set_icon, set_dock_icon, set_app_name,
    _toggle_fullscreen, _disable_default_keymaps)
from viz_explorer import (ExplorerEditMixin, ExplorerAnalysisMixin, ExplorerSidecarMixin, ExplorerDrawMixin, ExplorerNavMixin)


class TopologyExplorer(ExplorerEditMixin, ExplorerAnalysisMixin, ExplorerSidecarMixin, ExplorerDrawMixin, ExplorerNavMixin):
    """Cycle through topology candidates across one or more bundles.

    Navigation:
      ← / →  (or ◀/▶ Topo buttons)    — prev / next topology within bundle
      cmd-p / cmd-n                    — prev / next topology within bundle
      [ / ]  (or ◀/▶ Bundle buttons)  — prev / next bundle
      s (or Select button)             — toggle selection (pin/unpin)
      v / cmd-1 / ctrl-1               — raise the main BUDA viz window
                                         ('v' cycles back and forth: the main
                                          window's 'v' opens/raises this one)
      z / Z                            — zoom in / out
      cmd-z / ctrl-z                   — zoom to active bundle extent
      h / H / cmd-a / ctrl-a           — reset zoom to home full view
    """

    def __init__(self, fp, wrappers, sidecar_path=None, main_fig=None,
                 rerun_fn=None, refresh_fn=None, layer_stack=None,
                 ui_state: ViewState = None, start_bidx=0, layer_visible=None,
                 on_focus_bundle=None):
        self.fp          = fp
        self.layer_stack = layer_stack
        self.ui_state    = ui_state or ViewState()
        # Live reference to the main viz's {layer_id: visible} map so a layer
        # toggled off there is hidden here too (None = show every layer).
        self._layer_visible = layer_visible
        self._hidden_seg = set()       # seg indices hidden by main-viz layer toggles (rebuilt per _draw)
        self._main_fig   = main_fig    # back-reference to main viz figure for cmd-1
        self._rerun_fn   = rerun_fn    # () -> NUTSResult | None
        self._refresh_fn = refresh_fn  # (NUTSResult) -> None
        # Called with the current bundle's id when 'v' cycles back to the main
        # window, so the main view adopts the bundle paged to with [ / ] here.
        self._on_focus_bundle = on_focus_bundle
        
        self._block_patch_artists = []
        self._block_name_artists = []

        # Zoom state
        self._autoscale_needed = True
        self._home_xlim        = None
        self._home_ylim        = None
        self._home_data_bbox   = None   # raw data bbox (x0,x1,y0,y1) for maximal home fit

        # Listen for global visibility changes (e.g. from parent BudaVisualizer)
        self.ui_state.add_listener(self.fig_redraw)

        # Accept a single wrapper or a list for backward compatibility.
        self.wrappers = wrappers if isinstance(wrappers, list) else [wrappers]
        # Open on the requested bundle (e.g. the one matched by a viz hint).
        self.bidx     = start_bidx if 0 <= start_bidx < len(self.wrappers) else 0
        self.idx      = 0
        self.sidx     = -1  # current selected segment index within current topology
        # TopoEdit mode (Phase E3b GUI): a working COPY being edited in place of
        # the shown candidate.  Opened with 'e' (copy) / 'E' (empty), committed
        # with enter (appended to the pool as a USER candidate + pinned),
        # discarded with escape.  _edit_pending marks the first segment of a
        # two-step connect/disconnect pair.  _edit_slide stages per-segment
        # slide-window refinements ({seg_idx: (lo, hi)}, 'W' two-step / 'w'
        # clears) written to plan.seg_slide_lo/hi on commit; _edit_slide_mark
        # holds the first 'W' bound as (seg_idx, coord).
        self._edit_topo    = None
        self._edit_pending = -1
        self._edit_msg     = ""
        self._edit_slide   = {}
        self._edit_slide_mark = None

        # bundle_hint -> {topo_type, topo_wl, topo_index_hint, note, selected_at, seg_layers}
        self._selections    = {}
        self._sidecar_path  = sidecar_path
        _disable_default_keymaps()
        if sidecar_path and os.path.exists(sidecar_path):
            self._load_sidecar()
        # Focus the bundle's pinned topology (live pin or sidecar), else the
        # planner's choice — same resolution used when cycling bundles, so the
        # gold border lands consistently on open and after switching.
        self.idx = self._focus_topo_index()

        self.fig = plt.figure(figsize=(13, 10))
        self.fig.patch.set_facecolor('#f0f0f0')
        set_icon(self.fig)
        raise_window(self.fig)

        # Main axes — two button rows below; leave y=0.14 for buttons and x-tick labels
        self.ax = self.fig.add_axes([0.05, 0.14, 0.90, 0.81])

        # ── Navigation row (y=0.015) ─────────────────────────────────────────
        _BY1, _BH1  = 0.015, 0.038   # row y and height
        _BY2, _BH2  = 0.065, 0.038   # tuning row y and height (above nav)
        _MARGIN    = 0.010
        _GAP       = 0.008

        _nav_specs = [
            ('◀  Bundle',  '#d9f5d9', 1.0),
            ('◀  Topo',    '#ddeeff', 1.0),
            ('★  Select',  '#f0f0f0', 1.0),
            ('✕  Desel',   '#f0f0f0', 0.85),
        ]
        if rerun_fn is not None:
            _nav_specs.append(('▶  Re-run', '#ffe0b0', 1.2))
        _nav_specs += [
            ('Topo  ▶',   '#ddeeff', 1.0),
            ('Bundle  ▶', '#d9f5d9', 1.0),
        ]

        _n1          = len(_nav_specs)
        _total_w1    = sum(w for _, _, w in _nav_specs)
        _avail1      = 1.0 - 2 * _MARGIN - (_n1 - 1) * _GAP
        _unit1       = _avail1 / _total_w1

        _bax1 = []
        _x = _MARGIN
        for _label, _color, _weight in _nav_specs:
            _bw = _weight * _unit1
            _bax1.append(self.fig.add_axes([_x, _BY1, _bw, _BH1]))
            _x += _bw + _GAP

        self._btn_bprev = Button(_bax1[0], _nav_specs[0][0], color=_nav_specs[0][1])
        self._btn_bprev.on_clicked(lambda _: self._step_bundle(-1))
        self._btn_tprev = Button(_bax1[1], _nav_specs[1][0], color=_nav_specs[1][1])
        self._btn_tprev.on_clicked(lambda _: self._step_topo(-1))
        self._btn_select   = Button(_bax1[2], _nav_specs[2][0], color=_nav_specs[2][1])
        self._btn_select.on_clicked(lambda _: self._select_current())
        self._btn_deselect = Button(_bax1[3], _nav_specs[3][0], color=_nav_specs[3][1])
        self._btn_deselect.on_clicked(lambda _: self._deselect_current())
        
        idx = 4
        self._btn_rerun = None
        if rerun_fn is not None:
            self._btn_rerun = Button(_bax1[idx], _nav_specs[idx][0], color=_nav_specs[idx][1])
            self._btn_rerun.on_clicked(lambda _: self._rerun_and_refresh())
            idx += 1
            
        self._btn_tnext = Button(_bax1[idx], _nav_specs[idx][0], color=_nav_specs[idx][1])
        self._btn_tnext.on_clicked(lambda _: self._step_topo(+1))
        self._btn_bnext = Button(_bax1[idx+1], _nav_specs[idx+1][0], color=_nav_specs[idx+1][1])
        self._btn_bnext.on_clicked(lambda _: self._step_bundle(+1))

        # Hide bus buttons when only one bundle is loaded.
        if len(self.wrappers) == 1:
            _bax1[0].set_visible(False)
            _bax1[idx+1].set_visible(False)

        # ── Tuning row (y=0.065) ─────────────────────────────────────────
        _tune_specs = [
            ('◀  Seg',    '#f5f5f5', 0.8),
            ('Seg  ▶',    '#f5f5f5', 0.8),
            ('+',         '#ffe8cc', 0.6),
            ('-',         '#ffe8cc', 0.6),
        ]
        
        _n2          = len(_tune_specs)
        _total_w2    = sum(w for _, _, w in _tune_specs)
        _w2_frac     = 0.4 
        _avail2      = _w2_frac - (_n2 - 1) * _GAP
        _unit2       = _avail2 / _total_w2
        
        _bax2 = []
        self._bax2 = _bax2 # for visibility toggling
        _x = (1.0 - _w2_frac) / 2.0
        for _label, _color, _weight in _tune_specs:
            _bw = _weight * _unit2
            _bax2.append(self.fig.add_axes([_x, _BY2, _bw, _BH2]))
            _x += _bw + _GAP
            
        self._btn_sprev = Button(_bax2[0], _tune_specs[0][0], color=_tune_specs[0][1])
        self._btn_sprev.on_clicked(lambda _: self._step_segment(-1))
        self._btn_snext = Button(_bax2[1], _tune_specs[1][0], color=_tune_specs[1][1])
        self._btn_snext.on_clicked(lambda _: self._step_segment(+1))
        self._btn_promote = Button(_bax2[2], _tune_specs[2][0], color=_tune_specs[2][1])
        self._btn_promote.on_clicked(lambda _: self._cycle_layer(+1))
        self._btn_demote  = Button(_bax2[3], _tune_specs[3][0], color=_tune_specs[3][1])
        self._btn_demote.on_clicked(lambda _: self._cycle_layer(-1))

        # Give every button a clearly-visible hover shade (the default '0.95'
        # is invisible on the near-white buttons, so they showed no feedback).
        for _btn in (self._btn_bprev, self._btn_tprev, self._btn_select,
                     self._btn_deselect, self._btn_rerun, self._btn_tnext,
                     self._btn_bnext, self._btn_sprev, self._btn_snext,
                     self._btn_promote, self._btn_demote):
            if _btn is not None:
                _btn.hovercolor = _hover_color(_btn.color)

        self.fig.canvas.mpl_connect('key_press_event', self._on_key)
        self.fig.canvas.mpl_connect('close_event', self._on_close)
        _install_bbox_zoom(self)   # right-click-drag zoom-to-box (from the Floorplanner)

        self._draw()
        # Make the home view maximal from the first frame (not only after 'h'):
        # _draw fit the nominal figure size; track resizes to the real (and
        # macOS-maximized) window geometry as it settles.
        _install_home_fit_tracking(self)

    def _on_close(self, event):
        if hasattr(self, 'ui_state'):
            self.ui_state.remove_listener(self.fig_redraw)

    # ------------------------------------------------------------------

    @property
    def wrapper(self):
        return self.wrappers[self.bidx]

    @property
    def topos(self):
        return self.wrapper.input.candidates

    def _shown_topo(self):
        """The topology on screen: the edit-mode working copy when a session
        is open, else the current candidate."""
        return self._edit_topo if self._edit_topo is not None \
            else self.topos[self.idx]

    def show(self):
        raise_window(self.fig)
        install_tk_geometry_resync(self.fig)
        extract_from_fullscreen_tab(self.fig)
        plt.show()


class BudaVisualizer:
    def __init__(self, floorplan, bundles, sidecar_path=None, rerun_layer_fn=None,
                 rerun_fn=None, routing_grid=None, layer_stack=None,
                 net_endpoints=None, ipc_session=None, ipc_verbose=False):
        self.fp           = floorplan
        self.bundles      = bundles
        self._ipc_session = ipc_session
        self._ipc_verbose = ipc_verbose   # gated: --ipc-verbose surfaces IPC chatter
        self._ipc         = None
        self.routing_grid = routing_grid
        self.layer_stack  = layer_stack
        self._selections_path = (
            os.path.splitext(sidecar_path)[0] + '.json'
            if sidecar_path else None
        )
        _disable_default_keymaps()
        self.fig, self.ax = plt.subplots(figsize=(14, 12))
        self.fig.patch.set_facecolor('#f0f0f0')
        set_icon(self.fig)
        set_dock_icon()          # macOS Dock tile icon (window now realized)
        raise_window(self.fig)

        if sidecar_path and self.fig.canvas.manager:
            stem = os.path.splitext(os.path.basename(sidecar_path))[0]
            self.fig.canvas.manager.set_window_title(stem)
            # Relabel the OS app/dock name from 'python3' to the design's name.
            set_app_name(stem, self.fig)

        self.ui_state = ViewState()
        self.ui_state.add_listener(self.fig_redraw)

        # bundle_id -> list of dicts {artist, alpha, lw, is_band, layer}
        self._bundle_artists    = {}
        self._highlighted       = None
        self._last_highlighted  = None
        self._highlighted_set   = set()   # multi-highlight (overlap pair selection)
        self._selected_overlap  = None    # OverlapDetail currently selected
        self._overlap_state     = 0       # 0=none 1=both 2=A-only 3=B-only
        self._highlight_overlays = []   # thin boundary lines added on selection
        self._layer_visible  = {}   # populated in show()
        self._layer_ids      = []   # sorted list of active layer IDs
        self._bundle_visible = {}     # bid -> bool; populated in show()
        self._bundle_scroll  = 0      # first visible row in the bundle list
        self._bid_list       = []
        self._btn_solo       = None
        self._btn_all_layers = None
        self._btn_all_bundles= None
        self._btn_all_overlaps = None
        self._ax_layers      = None   # custom layer panel (replaces CheckButtons)
        self._ax_layer_stats = None   # nuts-segment total header above the layer panel
        self._ax_design_stats = None  # bundles·buses·nets header above the list
        self._ax_bundles     = None
        self._ax_overlaps    = None
        # Adaptive right-panel split: when the design has no overlaps the empty
        # Overlap panel's space is folded into the bundle list.  Geometries for
        # both modes are captured at show()-time; _apply_overlap_layout switches.
        self._right_rects    = None
        self._ov_layout_mode = 'with_ov'
        self._ax_bscroll_dn  = None
        self._ax_oscroll_up  = None
        self._ax_oscroll_dn  = None
        self._btn_oscroll_up = None
        self._btn_oscroll_dn = None
        self._rerun_layer_fn = rerun_layer_fn   # (layer_id: int) -> NUTSResult | None
        self._rerun_fn       = rerun_fn         # () -> NUTSResult | None  (full re-run)
        self._overlap_entries = []   # sorted list of OverlapDetail from nuts_result
        self._overlap_scroll = 0
        self._nuts_result    = None  # stored in draw_nuts_tracks for the overlap panel
        self._detailed_result = None # stored in draw_detailed_tracks for bit stats
        self._topo_explorer  = None
        self._pick_happened  = False
        self._cbar_ax        = None   # colorbar axes for congestion heatmap
        self._heatmap_artists = []    # patches + texts created by draw_congestion_map
        self._hanan_artists  = []     # lines created by draw_hanan_grid
        self._block_patch_artists = [] # block rectangle patches
        self._block_name_artists = [] # text artists created by draw_blocks
        self._home_xlim          = None
        self._home_ylim          = None
        self._home_data_bbox     = None   # raw data bbox (x0,x1,y0,y1) for maximal home fit
        self._busterm_artists    = []    # driver/receiver terminal artists
        self._vias_conns_artists = []    # via and busterm-conn marker artists
        self._keepout_artists    = []    # hatched rectangles + labels
        self._btn_heatmap    = None
        self._btn_keepouts   = None
        self._btn_blknames   = None
        self._btn_blocks     = None
        self._btn_bustermss  = None
        self._btn_vias_conns = None
        self._btn_all        = None
        self._btn_detailed   = None
        self._btn_tracks     = None
        self._btn_preroutes  = None
        self._btn_hanan      = None

        # Layout constants for the left panel (view toggles + heatmap)
        self._LX, self._LW = 0.005, 0.065
        # Start with a conservative estimate to leave room for ~8 buttons (8 * 0.046 = 0.368)
        # 0.97 - 0.368 = 0.602.
        self._ly_post_buttons = 0.60 

        # Detailed NUTS (Stage 9) visualisation state.
        self._detailed_bundle_artists = {}   # bid -> [{artist,alpha,lw,is_band,layer}]
        self._detailed_via_artists   = []    # per-bit via scatters (also registered above)
        self._grid_rail_artists      = []    # POWER/GND/CLK stripe collections (not per-bundle)
        self._layer_is_h             = {}    # layer_id -> bool (populated by draw_detailed_tracks)
        # Lazy build: bit-wires are created on the first [Detailed] toggle; the
        # background rail stripes (~thousands of track rects) are deferred even
        # further — to the first [Tracks] enable — since Tracks is off by default
        # and most detailed-view sessions only look at the routing wiring.
        self._has_detailed_data      = False
        self._detailed_built         = False   # bit-wire LineCollections built
        self._rails_built            = False   # rail-stripe PatchCollections built
        self._detailed_result        = None
        self._detailed_grid_stack    = None
        self._detailed_layer_stack   = None

        # Pre-route layer (draw_preroutes — first-class PreRoutedSegments from
        # RoutingGridStack.preroutes; see docs/internal/placed_segment_preroutes.md).
        # Unlike the [Tracks] rails view this works in the ABSTRACT view too.
        # Lazy build on the first [Preroutes] cycle away from 'off'.
        self._has_preroute_data = False
        self._preroutes_built   = False
        self._preroute_grid_stack  = None
        self._preroute_layer_stack = None
        self._preroute_artists  = []   # [{artist, layer, slot_type}] (not per-bundle)

        # IPC: bundle_id -> set of instance names (driver + receivers, 'top' excluded)
        self._bundle_insts: dict = {}
        if net_endpoints:
            for w in bundles:
                bid   = w.input.original_bundle.id
                insts = set()
                for net_name in w.input.original_bundle.get_net_names():
                    ep = net_endpoints.get(net_name)
                    if ep:
                        drv, rcvs = ep
                        if drv and drv != 'top':
                            insts.add(drv.rsplit('.', 1)[0])
                        for r in rcvs:
                            if r and r != 'top':
                                insts.add(r.rsplit('.', 1)[0])
                self._bundle_insts[bid] = insts

        self.fig.canvas.mpl_connect('pick_event',         self._on_pick)
        self.fig.canvas.mpl_connect('button_press_event', self._on_click)
        self.fig.canvas.mpl_connect('key_press_event',    self._on_key)
        _install_bbox_zoom(self)   # right-click-drag zoom-to-box (from the Floorplanner)

    # ------------------------------------------------------------------
    # Artist registry & interaction
    # ------------------------------------------------------------------

    def _register(self, bundle_id, artist, *, alpha, lw=None, is_band=False, layer=None,
                  phys_w=None, horiz=None, marker_phys=None, ms_floor=4.0):
        artist.set_picker(5)
        self._bundle_artists.setdefault(bundle_id, []).append({
            'artist':  artist,
            'alpha':   alpha,
            'lw':      lw,
            'is_band': is_band,
            'layer':   layer,
            # NUTS-placed segment lines carry their PHYSICAL bus width (data
            # units, perpendicular to the segment) so _sync_nuts_linewidths can
            # fit the drawn point-width to the true footprint at every zoom.
            # lw stays the CURRENT drawn width; lw_cap is the static ceiling.
            'phys_w':  phys_w,
            'horiz':   horiz,
            'lw_cap':  lw,
            # Via/busterm-conn MARKERS carry their physical extent the same way
            # (marker_phys, data units — the connected segments' width) so the
            # marker size tracks the segment widths at every zoom; ms is the
            # CURRENT markersize, ms_cap the static ceiling, ms_floor the
            # visibility floor (points).
            'marker_phys': marker_phys,
            'ms':          lw if marker_phys else None,
            'ms_cap':      lw if marker_phys else None,
            'ms_floor':    ms_floor,
        })

    _LW_MIN_PTS = 2.5   # visibility floor for physical-width segment lines

    def _sync_nuts_linewidths(self):
        """Fit each NUTS segment line's point-width to the segment's PHYSICAL
        footprint at the current zoom.

        The bold segment line is centered on the true track position but drawn
        with a fixed point-width (viz_lw, up to ~15pt for wide buses).  Zoomed
        out, those points span many times the physical band, so a line whose
        track hugs a block face appears shifted/straddling its busterm (the
        home-view artifact); zoomed in, the same points are NARROWER than the
        band and the picture is accurate.  Convert the physical width to
        points at the current zoom and clamp: never wider than the footprint
        (unless below the visibility floor), never wider than the static
        viz_lw (preserving the zoomed-in look).  Returns True if any width
        changed (caller decides whether a redraw is needed)."""
        if not self._bundle_artists:
            return False
        o  = self.ax.transData.transform((0.0, 0.0))
        px = self.ax.transData.transform((1.0, 0.0))[0] - o[0]
        py = self.ax.transData.transform((0.0, 1.0))[1] - o[1]
        pts_x = abs(px) * 72.0 / self.fig.dpi   # points per data unit, x
        pts_y = abs(py) * 72.0 / self.fig.dpi   # points per data unit, y
        changed = False
        for entries in self._bundle_artists.values():
            for e in entries:
                pw = e.get('phys_w')
                mp = e.get('marker_phys')
                if pw:
                    # An H segment's width is vertical; a V segment's horizontal.
                    pts = pts_y if e['horiz'] else pts_x
                    lw  = max(self._LW_MIN_PTS, min(e['lw_cap'], pw * pts))
                    if abs(lw - e['lw']) > 0.1:
                        e['lw'] = lw
                        e['artist'].set_linewidth(lw)
                        changed = True
                elif mp:
                    # Via / busterm-conn marker: square, so use the tighter
                    # axis scale; same clamp shape as the segment lines.
                    ms = max(e['ms_floor'], min(e['ms_cap'], mp * min(pts_x, pts_y)))
                    if abs(ms - e['ms']) > 0.1:
                        e['ms'] = ms
                        e['artist'].set_markersize(ms)
                        changed = True
        return changed

    def _hook_lw_sync(self):
        """Keep physical-width segment lines zoom-true: sync on axis-limit
        changes (zoom/pan/home — fires before the draw, so the frame is right
        the first time) with a draw_event backstop for figure resizes (which
        change points-per-data-unit without touching the limits).  The
        changed-guard prevents a redraw loop, as in the legend _refit hook."""
        if getattr(self, '_lw_sync_hooked', False):
            return
        self._lw_sync_hooked = True
        self.ax.callbacks.connect(
            'xlim_changed', lambda ax: self._sync_nuts_linewidths())
        self.ax.callbacks.connect(
            'ylim_changed', lambda ax: self._sync_nuts_linewidths())

        def _on_draw(_evt):
            if self._sync_nuts_linewidths():
                self.fig.canvas.draw_idle()
        self.fig.canvas.mpl_connect('draw_event', _on_draw)

    def _on_pick(self, event):
        # Right-click is the zoom-to-box gesture (_install_bbox_zoom), not a
        # selection — ignore picks it triggers so it never changes the highlight.
        me = getattr(event, 'mouseevent', None)
        if me is not None and me.button == 3:
            return
        self._pick_happened = True
        active_reg = (self._detailed_bundle_artists
                      if self.ui_state.detailed_mode else self._bundle_artists)
        for bid, entries in active_reg.items():
            for e in entries:
                if event.artist is e['artist']:
                    self._set_highlight(bid)
                    return

    def _on_click(self, event):
        # Right-click drives the rubber-band zoom-to-box (_install_bbox_zoom);
        # it must not select/deselect a bundle.
        if event.button == 3:
            return
        # Ignore clicks while a toolbar tool (zoom, pan) is active.
        toolbar = getattr(self.fig.canvas, 'toolbar', None)
        if toolbar is not None and getattr(toolbar, 'mode', '') != '':
            self._pick_happened = False
            return

        # Route panel clicks before doing anything else.
        if self._ax_layers is not None and event.inaxes == self._ax_layers:
            self._on_layer_list_click(event)
            self._pick_happened = False
            return
        if self._ax_bundles is not None and event.inaxes == self._ax_bundles:
            self._on_bundle_list_click(event)
            self._pick_happened = False
            return
        if self._ax_overlaps is not None and event.inaxes == self._ax_overlaps:
            self._on_overlap_list_click(event)
            self._pick_happened = False
            return
        # A click that didn't land on any registered artist → deselect.
        if not self._pick_happened and event.inaxes == self.ax:
            self._set_highlight(None)
        self._pick_happened = False

    def _bundle_name(self, bid):
        """Return the first net name for bid, or 'B{bid}' as fallback."""
        w = next((w for w in self.bundles if w.input.original_bundle.id == bid), None)
        if w:
            names = w.input.original_bundle.get_net_names()
            return names[0] if names else f"B{bid}"
        return f"B{bid}"

    def _bundle_bits(self, bid):
        """Return the number of nets (bit-width) for bid, or 0 if unknown."""
        w = next((w for w in self.bundles if w.input.original_bundle.id == bid), None)
        if w:
            return len(w.input.original_bundle.get_net_names())
        return 0

    def _set_highlight(self, bundle_id):
        if bundle_id == self._highlighted and not self._highlighted_set:
            bundle_id = None
        self._highlighted      = bundle_id
        self._highlighted_set  = set()
        self._selected_overlap = None
        self._overlap_state    = 0
        self._refresh_highlight()
        self._ipc_send_highlight(bundle_id)

    def _ipc_send_highlight(self, bundle_id):
        if self._ipc is None:
            return
        if bundle_id is None:
            self._ipc.send({'type': 'clear'})
            return
        w = next((w for w in self.bundles if w.input.original_bundle.id == bundle_id), None)
        net_names  = list(w.input.original_bundle.get_net_names()) if w else []
        inst_names = sorted(self._bundle_insts.get(bundle_id, set()))
        msg = {
            'type': 'select_bundle',
            'bundle_id': bundle_id,
            'net_names': net_names,
            'inst_names': inst_names,
        }
        self._ipc.send(msg)

    def _on_ipc_message(self, msg: dict):
        kind = msg.get('type')
        if kind == 'select_inst':
            inst_names = set(msg.get('inst_names', []))
            matching = [bid for bid, insts in self._bundle_insts.items()
                        if insts & inst_names]
            if matching:
                self._set_highlight(matching[0])
        elif kind == 'clear':
            if self._highlighted is not None or self._highlighted_set:
                self._set_highlight(None)

    def _set_overlap_highlight(self, od):
        """Cycle through 4 states on repeated clicks of the same overlap row.

        State 1 — both bundles glow, everything else dims
        State 2 — bid_a (first)  glows, bid_b and everything else dims
        State 3 — bid_b (second) glows, bid_a and everything else dims
        State 0 — cleared (back to normal)
        """
        if self._selected_overlap is od:
            # Advance the cycle: 1→2→3→0
            new_state = (self._overlap_state + 1) % 4
        else:
            # New row selected → always start at state 1
            new_state = 1

        self._overlap_state    = new_state
        self._highlighted      = None

        if new_state == 0:
            self._highlighted_set  = set()
            self._selected_overlap = None
        else:
            self._selected_overlap = od
            if new_state == 1:
                self._highlighted_set = {od.bid_a, od.bid_b}
            elif new_state == 2:
                self._highlighted_set = {od.bid_a}
                self._highlighted     = od.bid_a   # also select so sidebar/title update
            else:  # state 3
                self._highlighted_set = {od.bid_b}
                self._highlighted     = od.bid_b   # also select so sidebar/title update

        self._refresh_highlight()

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

    def _refresh_highlight(self):
        """Apply highlight + solo + layer-visibility + bundle-visibility to all artists."""
        from matplotlib.lines import Line2D as MplLine2D

        bundle_id = self._highlighted
        if bundle_id is not None:
            self._last_highlighted = bundle_id
        hset      = self._highlighted_set

        # Resolve which bundle ids are "active" (shown at full alpha).
        if hset:
            active_bids = hset
        elif bundle_id is not None:
            active_bids = {bundle_id}
        else:
            active_bids = None   # none selected → all at resting alpha

        # Remove overlay boundary lines from the previous selection.
        for art in self._highlight_overlays:
            try: art.remove()
            except Exception: pass
        self._highlight_overlays.clear()

        active_reg = (self._detailed_bundle_artists
                      if self.ui_state.detailed_mode else self._bundle_artists)

        for bid, entries in active_reg.items():
            bundle_on = self._bundle_visible.get(bid, True)
            selected  = (active_bids is None) or (bid in active_bids)
            # An *explicitly* selected bundle is revealed even when "All Bundles"
            # hid it, so n/p step through and turn on the chosen bus's segs/bits.
            explicitly_selected = (active_bids is not None) and (bid in active_bids)

            for e in entries:
                a = e['artist']

                # Bundle visibility: hard gate — off when hidden, unless this is
                # the explicitly selected bundle (highlight overrides the toggle).
                if not bundle_on and not explicitly_selected:
                    a.set_alpha(0.0)
                    if e['lw'] is not None: a.set_linewidth(e['lw'])
                    continue

                # Layer visibility: hard gate.
                if e['layer'] is not None and not self._layer_visible.get(e['layer'], True):
                    a.set_alpha(0.0)
                    if e['lw'] is not None: a.set_linewidth(e['lw'])
                    continue

                if e['lw'] is not None:
                    a.set_linewidth(e['lw'])  # current width (zoom-synced for
                                              # physical-width NUTS lines)

                if active_bids is None:
                    a.set_alpha(e['alpha'])
                elif selected:
                    a.set_alpha(0.2 if e['is_band'] else 1.0)
                else:
                    a.set_alpha(0.0 if self.ui_state.solo else (0.03 if e['is_band'] else 0.1))

        # Layer visibility also gates the pre-route bands (any view mode).
        self._apply_preroute_visibility()

        # Apply layer visibility to non-bundle detailed artists (grid rails).
        # These are only shown when detailed_mode is active.
        if self.ui_state.detailed_mode:
            for e in self._grid_rail_artists:
                a = e['artist']
                layer_on = self._layer_visible.get(e['layer'], True)
                tracks_on = self.ui_state.tracks
                # Use stored base alpha (0.15 for rails, 0.10 for signal)
                base_alpha = e.get('alpha', 0.15)
                a.set_alpha(base_alpha if (layer_on and tracks_on) else 0.0)

        # Draw thin white boundary lines over each selected bundle's segments.
        # Skipped in detailed mode — overlays would cover bit-wire lines entirely.
        if active_bids is not None and not self.ui_state.detailed_mode:
            for sel_bid in active_bids:
                for e in active_reg.get(sel_bid, []):
                    a = e['artist']
                    if e['is_band'] or not isinstance(a, MplLine2D):
                        continue
                    x, y = a.get_xdata(orig=False), a.get_ydata(orig=False)
                    ol, = self.ax.plot(x, y, '-',
                                       color='white', linewidth=2,
                                       solid_capstyle='butt',
                                       alpha=0.9, zorder=200,
                                       picker=False)
                    self._highlight_overlays.append(ol)

        # Update title.
        od = self._selected_overlap
        if od is not None and self._overlap_state > 0:
            layer_str = f" M{od.layer}"
            if self._overlap_state == 1:
                msg = f"Bundle {od.bid_a} × Bundle {od.bid_b}{layer_str} — both highlighted"
            elif self._overlap_state == 2:
                msg = f"Bundle {od.bid_a} highlighted · Bundle {od.bid_b} dimmed{layer_str}"
            else:
                msg = f"Bundle {od.bid_a} dimmed · Bundle {od.bid_b} highlighted{layer_str}"
            # For ref: old title had f"BUDA — Overlap: {msg}  (click row to cycle, All Overlaps to clear)"
            self.ax.set_title(f"BUDA — Overlap: {msg}", fontsize=13)
        elif bundle_id is not None:
            bname = self._bundle_name(bundle_id)
            nbits = self._bundle_bits(bundle_id)

            wrapper = next((w for w in self.bundles if w.input.original_bundle.id == bundle_id), None)

            # Get busterm count from the bundle metadata.
            nterms = wrapper.input.original_bundle.num_terminals

            # Segment count of the bundle's selected topology — handy when
            # cycling bundles with `n` to gauge each topology's complexity.
            cands = wrapper.input.candidates
            sel   = wrapper.plan.selected_topology_index
            nsegs = len(cands[sel].segments) if cands and 0 <= sel < len(cands) else 0

            info = []
            if nbits > 0:
                info.append(f"{nbits} bits/{nterms} bterms")
            info.append(f"{nsegs} segs")
            info_str = f" ({', '.join(info)})"
            # For ref, old title had: f"(click again or click background to deselect)"
            # and a trailing "  [Solo ON]" hint (dropped — the Solo button shows state).
            self.ax.set_title(
                f"BUDA — B{bundle_id} {bname}{info_str} selected",
                fontsize=13)
        else:
            self.ax.set_title(stat_title, fontsize=13)

        self._redraw_bundle_list()
        self._redraw_overlap_list()
        self._redraw_blocks(bundle_id)
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

    def _refresh_topo_explorer(self):
        """Redraw the topology explorer, if open, so it picks up shared state
        (e.g. layer visibility) that isn't routed through ui_state.notify()."""
        exp = self._topo_explorer
        if exp is not None and plt.fignum_exists(exp.fig.number):
            exp.fig_redraw()

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
            raise_window(self._topo_explorer.fig)
            self._topo_explorer.show_bundle_index(start)
            return

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
            on_focus_bundle=self._adopt_explorer_bundle)
        self._topo_explorer.fig.show()
        install_tk_geometry_resync(self._topo_explorer.fig)
        extract_from_fullscreen_tab(self._topo_explorer.fig)

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
        if event.key in ('f', 'cmd+f', 'ctrl+f'): _toggle_fullscreen(self.fig); return
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

    # At most this many overflow cells get a text label (the worst by ratio).
    # Text+bbox is matplotlib's most expensive artist; on large congested
    # designs there can be thousands of overflow cells — colour already encodes
    # utilisation, so we only annotate the worst offenders.
    _HEATMAP_LABEL_CAP = 40

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

    # ------------------------------------------------------------------
    # Detailed NUTS (Stage 9) drawing
    # ------------------------------------------------------------------

    def _register_detailed(self, bundle_id, artist, *, alpha, lw=None, layer=None):
        artist.set_picker(5)
        self._detailed_bundle_artists.setdefault(bundle_id, []).append({
            'artist':  artist,
            'alpha':   alpha,
            'lw':      lw,
            'is_band': False,
            'layer':   layer,
        })

    # ── Pre-routes (Phase G: first-class PreRoutedSegments) ────────────────
    def draw_preroutes(self, routing_grid_stack, layer_stack):
        """Register the pre-route layer (no artists yet — lazy build on the
        first [Preroutes] cycle away from 'off').

        Unlike the [Tracks] rail stripes (detailed-mode only, SIGNAL slots
        included), this draws only the pre-route (non-SIGNAL) bands and works
        in the ABSTRACT view too — the pre-route context exists before any
        detailed routing does.  Both views build their bands from the same
        enumeration (_track_band_rects over RoutingGridStack.preroutes).
        See docs/internal/placed_segment_preroutes.md.
        """
        if routing_grid_stack is None or layer_stack is None:
            return
        self._preroute_grid_stack  = routing_grid_stack
        self._preroute_layer_stack = layer_stack
        self._has_preroute_data    = True
        if self._btn_preroutes is not None:
            self._set_button_enabled(self._btn_preroutes, True, on_color='#f4ece8')

    def _layout_bbox(self):
        """Floorplan bounding box (x_min, x_max, y_min, y_max) for full-extent
        track bands, with a default extent when no blocks exist."""
        all_blocks = list(self.fp.get_all_blocks())
        if all_blocks:
            return (min(r.x1 for _, r in all_blocks),
                    max(r.x2 for _, r in all_blocks),
                    min(r.y1 for _, r in all_blocks),
                    max(r.y2 for _, r in all_blocks))
        return 0, 1000, 0, 1000

    def _track_band_rects(self, grid_stack, layer_is_h,
                          include_signal=False, pad_perp=False):
        """Enumerate a RoutingGridStack's track slots over the floorplan bbox
        as (layer_id, slot_type, Rectangle) bands — the single geometry source
        shared by the [Preroutes] bands and the [Tracks] rail stripes.

        include_signal adds the SIGNAL stripes (the rails view); pad_perp
        extends the perpendicular window by one unit pitch so the stripes run
        past the outermost blocks (the rails view's extent idiom)."""
        x_min, x_max, y_min, y_max = self._layout_bbox()
        for lid, is_h in layer_is_h.items():
            if not grid_stack.has_layer(lid):
                continue
            pad = 0.0
            if pad_perp:
                grid = grid_stack.get_layer_grid(lid)
                pad  = grid.effective_pattern_at(0.0, 0.0).unit_pitch()
                if pad <= 0:
                    continue
            if is_h:
                perp_lo, perp_hi   = y_min - pad, y_max + pad
                along_lo, along_hi = x_min, x_max
            else:
                perp_lo, perp_hi   = x_min - pad, x_max + pad
                along_lo, along_hi = y_min, y_max
            for pr in grid_stack.preroutes(lid, perp_lo, perp_hi,
                                           along_lo, along_hi,
                                           include_signal):
                if pr.slot_type == 'SIGNAL':
                    col = _LAYER_COLOR.get(lid, '#888888')
                else:
                    col = _PREROUTE_COLOR.get(pr.slot_type, '#f0f0f0')
                half = pr.width / 2.0
                if is_h:
                    rect = patches.Rectangle(
                        (pr.span_lo, pr.track_position - half),
                        pr.span_hi - pr.span_lo, pr.width,
                        linewidth=0, facecolor=col)
                else:
                    rect = patches.Rectangle(
                        (pr.track_position - half, pr.span_lo),
                        pr.width, pr.span_hi - pr.span_lo,
                        linewidth=0, facecolor=col)
                yield lid, pr.slot_type, rect

    def _build_preroute_artists(self):
        """Create the pre-route band artists (once, lazily): one
        PatchCollection per (layer, slot type) from the enumerated
        PreRoutedSegments over the floorplan bbox, so per-type visibility
        is a collection flip."""
        if self._preroutes_built or not self._has_preroute_data:
            return
        self._preroutes_built = True

        groups = {}   # (layer, slot_type) -> [Rectangle, ...]
        for lid, stype, rect in self._track_band_rects(
                self._preroute_grid_stack,
                _layer_is_h_map(self._preroute_layer_stack)):
            groups.setdefault((lid, stype), []).append(rect)

        for (lid, stype), rects in sorted(groups.items()):
            pc = PatchCollection(rects, match_original=True, zorder=3)
            pc.set_alpha(0.35)
            pc.set_visible(False)
            self.ax.add_collection(pc)
            self._preroute_artists.append(
                {'artist': pc, 'layer': lid, 'slot_type': stype})

    def _apply_preroute_visibility(self):
        """Visibility from the cycling mode ('off' hides all, 'ALL' shows
        every type, a slot-type name shows just that type) AND the layer
        panel — a metal layer unchecked there hides its pre-route bands too,
        exactly like bundle artists and the detailed grid rails."""
        mode = self.ui_state.preroutes_mode
        for e in self._preroute_artists:
            type_on  = (mode == 'ALL' or e['slot_type'] == mode)
            layer_on = self._layer_visible.get(e['layer'], True)
            e['artist'].set_visible(type_on and layer_on)

    def _cycle_preroutes(self):
        if not getattr(self._btn_preroutes, '_buda_enabled', True):
            return                       # dimmed: no pre-route data in the design
        # Peek the next mode so the lazy build happens BEFORE the
        # notify-driven visibility sync in fig_redraw.
        cyc = self.ui_state.preroute_cycle
        cur = self.ui_state.preroutes_mode
        i = cyc.index(cur) if cur in cyc else 0
        if cyc[(i + 1) % len(cyc)] != 'off':
            self._build_preroute_artists()
        self.ui_state.cycle_preroutes()
        self.fig.canvas.draw_idle()

    def draw_detailed_tracks(self, detailed_result, routing_grid_stack, layer_stack):
        """Register Stage-9 detailed-NUTS data for visualisation.

        The actual artists (one LineCollection per bundle×layer for bit-wires,
        one PatchCollection per layer for rail stripes) are *not* built here —
        they are created lazily on the first [Detailed] toggle by
        `_build_detailed_artists()`.  On large designs this is ~8k artists, so
        deferring them keeps the initial load fast for the common case where the
        user never opens the detailed view.
        """
        # Build layer direction map from the LayerStack (cheap; needed for stats).
        self._layer_is_h.update(_layer_is_h_map(layer_stack))

        self._detailed_result      = detailed_result
        self._detailed_grid_stack  = routing_grid_stack
        self._detailed_layer_stack = layer_stack
        self._has_detailed_data    = True

        # Reveal the [Detailed] button; artists are built on demand.
        if self._btn_detailed is not None:
            self._btn_detailed.ax.set_visible(True)

        # Update bundle list to show bit placement stats.
        self._redraw_bundle_list()

    def _has_rail_layers(self):
        """True if any layer has a track pattern (so rails could be drawn).

        Cheap check (no rect enumeration) used to decide whether the [Tracks]
        button is meaningful, without eagerly building the rail artists.
        """
        grid = self._detailed_grid_stack
        if grid is None:
            return False
        return any(grid.has_layer(lid) for lid in self._layer_is_h)

    def _build_rail_artists(self):
        """Create the background rail-stripe artists (once, lazily).

        Deferred to the first [Tracks] enable: enumerating every track across
        the layout and building a Rectangle each is the costly part of the
        detailed view, and Tracks is off by default, so most sessions never
        need it.  Same band enumeration as the [Preroutes] view
        (_track_band_rects) with the perpendicular window padded, but the
        rails view keeps only the SIGNAL stripes ("where can bits land") —
        the non-SIGNAL context is the [Preroutes] layer's job (it works in
        detailed mode too), so the two buttons are orthogonal and nothing is
        double-drawn.  Stripes are coloured by their layer (same colour as
        the bit-wires that land on them), one PatchCollection per layer;
        they start hidden and the toggle reveals them.
        """
        if self._rails_built or not self._has_detailed_data:
            return
        self._rails_built = True

        rail_groups = {}   # layer_id -> [Rectangle, ...]
        for lid, stype, rect in self._track_band_rects(
                self._detailed_grid_stack, self._layer_is_h,
                include_signal=True, pad_perp=True):
            if stype != 'SIGNAL':
                continue   # power/clock context is the [Preroutes] layer
            rail_groups.setdefault(lid, []).append(rect)

        # One collection per layer so each has a single base alpha
        # (set_alpha in _refresh_highlight is uniform per collection).
        for lid, rects in rail_groups.items():
            base_alpha = 0.20
            pc = PatchCollection(rects, match_original=True, zorder=4)
            pc.set_alpha(base_alpha)
            pc.set_visible(False)
            self.ax.add_collection(pc)
            self._grid_rail_artists.append({'artist': pc, 'layer': lid, 'alpha': base_alpha})

    def _build_detailed_artists(self):
        """Create the detailed bit-wire artists (once, lazily).

        Bit-wires are grouped into one LineCollection per (bundle, layer),
        collapsing thousands of individual artists into a few hundred so the
        detailed view renders quickly.  The background rail stripes are built
        separately by _build_rail_artists() on the first [Tracks] enable.  All
        artists start hidden; the toggle reveals them.
        """
        if self._detailed_built or not self._has_detailed_data:
            return
        self._detailed_built = True

        detailed_result = self._detailed_result

        # Bit-wire NetSegments → one LineCollection per (bundle, layer).
        # span_lo/span_hi are already junction-adjusted by DetailedNUTSEngine.
        layer_specs = {k: {'color': v} for k, v in _LAYER_COLOR.items()}
        seg_groups = {}   # (bundle_id, layer) -> ([segment], [linewidth])
        for ns in detailed_result.net_segments:
            is_h = self._layer_is_h.get(ns.layer, True)
            lw   = max(0.6, ns.width * 0.6)
            if is_h:
                seg = [(ns.span_lo, ns.track_position), (ns.span_hi, ns.track_position)]
            else:
                seg = [(ns.track_position, ns.span_lo), (ns.track_position, ns.span_hi)]
            g = seg_groups.setdefault((ns.bundle_id, ns.layer), ([], []))
            g[0].append(seg); g[1].append(lw)

        for (bid, layer), (segs, lws) in seg_groups.items():
            col = layer_specs.get(layer, {'color': 'green'})['color']
            lc  = LineCollection(segs, colors=col, linewidths=lws,
                                 capstyle='butt', zorder=15)
            lc.set_alpha(0.9)
            lc.set_visible(False)
            self.ax.add_collection(lc)
            # lw=None: widths are baked per-segment on the collection, so
            # _refresh_highlight must not overwrite them with a scalar.
            self._register_detailed(bid, lc, alpha=0.9, lw=None, layer=layer)

        # Per-bit vias (NetVia) → one scatter (PathCollection) per
        # (bundle, upper layer): thousands of via markers collapse into a
        # handful of artists, same rationale as the bit-wire LineCollections.
        via_groups = {}   # (bundle_id, upper_layer) -> ([x], [y])
        for nv in detailed_result.net_vias:
            up = max(nv.from_layer, nv.to_layer)
            g = via_groups.setdefault((nv.bundle_id, up), ([], []))
            g[0].append(nv.x); g[1].append(nv.y)
        for (bid, up), (xs, ys) in via_groups.items():
            sc = self.ax.scatter(
                xs, ys, s=14, marker='s', facecolors='white',
                edgecolors=_LAYER_COLOR.get(up, '#888888'), linewidths=0.9,
                zorder=16)   # above the bit-wires (zorder 15)
            sc.set_alpha(0.95)
            sc.set_visible(False)
            self._register_detailed(bid, sc, alpha=0.95, lw=None, layer=up)
            self._detailed_via_artists.append(sc)

    def _apply_busterm_visibility(self):
        """Terminal (busterm) markers belong to the abstract view — hidden in
        Detailed mode. They're registered per-bundle, so entering Detailed
        already bulk-hides them; this keeps every OTHER state change consistent
        (a solo toggle or any fig_redraw would otherwise re-reveal them on the
        Terminals toggle alone). Shown only with Terminals on AND not detailed.
        """
        vis = self.ui_state.busterms and not self.ui_state.detailed_mode
        for a in self._busterm_artists:
            a.set_visible(vis)

    def _apply_vias_conns_visibility(self):
        """Gate BOTH via/conn artist sets by mode so they never coexist.

        The abstract (bus-level) via/conn markers belong to the NUTS view; the
        per-bit vias replace them in Detailed mode. So the abstract set shows
        only with Vias/Conns on AND NOT in detailed mode, and the per-bit set
        only in detailed mode. Every place that changes vias_conns/detailed_mode
        state must call this — otherwise a stale abstract marker is left behind
        on top of the detailed view (e.g. any fig_redraw while detailed was on).
        """
        abstract_vis = self.ui_state.vias_conns and not self.ui_state.detailed_mode
        for a in self._vias_conns_artists:
            a.set_visible(abstract_vis)
        self._apply_detailed_via_visibility()

    def _apply_detailed_via_visibility(self):
        """Per-bit vias show only in detailed mode AND with Vias/Conns on.

        The via scatters are also registered in _detailed_bundle_artists (for
        layer/bundle/highlight gating), whose bulk set_visible loops would
        otherwise reveal them whenever detailed mode is entered — this gate
        runs after those loops.
        """
        vis = self.ui_state.detailed_mode and self.ui_state.vias_conns
        for a in self._detailed_via_artists:
            a.set_visible(vis)

    def _toggle_detailed(self):
        # Build the detailed artists the first time the view is opened.
        if not self.ui_state.detailed_mode and not self._detailed_built:
            self._build_detailed_artists()
        self.ui_state.toggle_detailed()
        active = self.ui_state.detailed_mode

        # Show/hide the inactive set first (bulk operation without highlight logic).
        for entries in self._bundle_artists.values():
            for e in entries:
                e['artist'].set_visible(not active)
        for entries in self._detailed_bundle_artists.values():
            for e in entries:
                e['artist'].set_visible(active)
        # Re-gate the abstract-only marker sets: entering hides what the bulk
        # loop above just revealed; leaving respects the Terminals / Vias/Conns
        # toggles instead of the unconditional set_visible the bulk loop applied.
        self._apply_busterm_visibility()
        self._apply_vias_conns_visibility()
        # If Tracks is already on when entering Detailed, build the rails now.
        if active and self.ui_state.tracks and not self._rails_built:
            self._build_rail_artists()
        for e in self._grid_rail_artists:
            e['artist'].set_visible(active and self.ui_state.tracks)

        if self._btn_detailed is not None:
            lbl = '☑ Detailed' if active else '☐ Detailed'
            self._btn_detailed.label.set_text(lbl)
            self._btn_detailed.ax.set_facecolor('#ffe8cc' if active else '#e8f4e8')

        if self._btn_tracks is not None:
            # Always visible; dimmed (inactive) until Detailed is on with rails.
            # Gate on rail-layer availability (cheap) — not on built artifacts —
            # so the button activates before the rails are lazily built.
            self._set_button_enabled(
                self._btn_tracks, active and self._has_rail_layers())

        # Re-apply highlight/layer/bundle visibility to the now-active set.
        self._refresh_highlight()
        self.fig.canvas.draw_idle()

    def _toggle_tracks(self):
        if not getattr(self._btn_tracks, '_buda_enabled', True):
            return                       # dimmed: Detailed off or no rail layers
        self.ui_state.toggle_tracks()
        vis = self.ui_state.tracks

        # Build the rail stripes the first time Tracks is enabled (deferred from
        # the [Detailed] build since Tracks is off by default).
        if vis and not self._rails_built:
            self._build_rail_artists()

        for e in self._grid_rail_artists:
            # Visibility is hard-gated by detailed_mode, alpha by _refresh_highlight.
            e['artist'].set_visible(self.ui_state.detailed_mode and vis)
            
        if self._btn_tracks is not None:
            lbl = '☑ Tracks' if vis else '☐ Tracks'
            self._btn_tracks.label.set_text(lbl)
            self._btn_tracks.ax.set_facecolor('#ffe8cc' if vis else '#e8f4e8')
            
        self._refresh_highlight()
        self.fig.canvas.draw_idle()

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

    def show(self):
        self._sort_bid_list()          # DNUTS-open bundles rank first
        self._bundle_visible = {bid: True for bid in self._bid_list}

        # Build overlap entries sorted by layer → bid_a → bid_b.
        if self._nuts_result is not None:
            self._overlap_entries = sorted(
                self._nuts_result.overlap_details,
                key=lambda od: (od.layer, od.bid_a, od.bid_b)
            )

        # Collect and initialize layer IDs.
        self._update_layer_ids()

        self.ax.set_aspect('equal')
        self.ax.set_title(stat_title, fontsize=13)

        from matplotlib.lines import Line2D
        legend_handles = [
            Line2D([0], [0], marker='s', color='w',
                   markerfacecolor='#00FFFF', markeredgecolor='k', label='Driver'),
            Line2D([0], [0], marker='o', color='w',
                   markerfacecolor='#FF00FF', markeredgecolor='k', label='Receivers'),
        ]
        self.ax.legend(handles=legend_handles, loc='upper right')
        self.ax.autoscale_view()
        self._home_data_bbox = self.ax.get_xlim() + self.ax.get_ylim()  # (x0,x1,y0,y1)

        # Right panel: x=0.83, width=0.15.  Plot right edge at 0.81.
        # Left panel: centered area for toggles and heatmap.
        # bottom=0.11 reserves room for x-tick labels above the button row.
        # top=0.97 reclaims the wasted margin above the title.
        self.fig.subplots_adjust(left=0.13, bottom=0.11, right=0.81, top=0.97)

        # Expand the autoscaled data bbox to the main axes' on-screen aspect so
        # the home view fills the window (same maximal framing as cmd-z) instead
        # of collapsing to a thin sliver under set_aspect('equal').  Done after
        # subplots_adjust so the axes box reflects the final layout.
        _set_lims_filling_box(self.ax, *self._home_data_bbox)
        self._home_xlim = self.ax.get_xlim()
        self._home_ylim = self.ax.get_ylim()

        # ── Left panel: view toggles ──────────────────────────────────────
        LX, LW = self._LX, self._LW
        BTN_H_L = 0.038
        GAP_L   = 0.008

        ly = 0.97  # top-down, same as right panel

        def _lrect(h, gap=0):
            nonlocal ly
            ly -= gap + h
            return [LX, ly, LW, h]

        ax_all = self.fig.add_axes(_lrect(BTN_H_L))
        self._btn_all = Button(ax_all, '☑ All', color='#d0e8ff')
        self._btn_all.label.set_fontsize(7.5)
        self._btn_all.on_clicked(lambda _: self._toggle_all())

        ax_blocks = self.fig.add_axes(_lrect(BTN_H_L, GAP_L))
        self._btn_blocks = Button(ax_blocks, '☑ Blocks', color='#e8f4e8')
        self._btn_blocks.label.set_fontsize(7.5)
        self._btn_blocks.on_clicked(lambda _: self._toggle_blocks())

        ax_blknames = self.fig.add_axes(_lrect(BTN_H_L, GAP_L))
        self._btn_blknames = Button(ax_blknames, '☑ Names', color='#e8f4e8')
        self._btn_blknames.label.set_fontsize(7.5)
        self._btn_blknames.on_clicked(lambda _: self._toggle_block_names())

        ax_hanan = self.fig.add_axes(_lrect(BTN_H_L, GAP_L))
        self._btn_hanan = Button(ax_hanan, '☐ Hanan', color='#e8f4e8')
        self._btn_hanan.label.set_fontsize(7.5)
        self._btn_hanan.on_clicked(lambda _: self._toggle_hanan())

        ax_bustermss = self.fig.add_axes(_lrect(BTN_H_L, GAP_L))
        self._btn_bustermss = Button(ax_bustermss, '☑ Terminals', color='#e8f4e8')
        self._btn_bustermss.label.set_fontsize(7.5)
        self._btn_bustermss.on_clicked(lambda _: self._toggle_bustermss())

        ax_vias_conns = self.fig.add_axes(_lrect(BTN_H_L, GAP_L))
        self._btn_vias_conns = Button(ax_vias_conns, '☑ Vias/Conns', color='#e8f4e8')
        self._btn_vias_conns.label.set_fontsize(7.5)
        self._btn_vias_conns.on_clicked(lambda _: self._toggle_vias_conns())

        ax_heatmap = self.fig.add_axes(_lrect(BTN_H_L, GAP_L))
        self._btn_heatmap = Button(
            ax_heatmap, '☑ Heatmap' if self.ui_state.heatmap else '☐ Heatmap',
            color='#e8f4e8')
        self._btn_heatmap.label.set_fontsize(7.5)
        self._btn_heatmap.on_clicked(lambda _: self._toggle_heatmap())
        # Always visible; dimmed (inactive) unless a congestion map was drawn.
        self._set_button_enabled(
            self._btn_heatmap,
            bool(self._heatmap_artists) or self._cbar_ax is not None)

        ax_keepouts = self.fig.add_axes(_lrect(BTN_H_L, GAP_L))
        self._btn_keepouts = Button(ax_keepouts, '☑ Keepouts', color='#e8f4e8')
        self._btn_keepouts.label.set_fontsize(7.5)
        self._btn_keepouts.on_clicked(lambda _: self._toggle_keepouts())
        # Always visible; dimmed (inactive) when the design has no keepouts.
        self._set_button_enabled(self._btn_keepouts, bool(self.fp.get_keepout_zones()))

        ax_detailed = self.fig.add_axes(_lrect(BTN_H_L, GAP_L))
        self._btn_detailed = Button(ax_detailed, '☐ Detailed', color='#e8f4e8')
        self._btn_detailed.label.set_fontsize(7.5)
        self._btn_detailed.on_clicked(lambda _: self._toggle_detailed())
        # Hidden until draw_detailed_tracks() has registered detailed data.
        if not self._has_detailed_data:
            self._btn_detailed.ax.set_visible(False)

        ax_tracks = self.fig.add_axes(_lrect(BTN_H_L, GAP_L))
        self._btn_tracks = Button(ax_tracks, '☐ Tracks', color='#e8f4e8')
        self._btn_tracks.label.set_fontsize(7.5)
        self._btn_tracks.on_clicked(lambda _: self._toggle_tracks())
        # Always visible; dimmed (inactive) until Detailed mode is on and rail
        # layers exist.  Gate on rail-layer availability (cheap), not on built
        # artifacts — rails are built lazily on the first [Tracks] enable.
        self._set_button_enabled(
            self._btn_tracks,
            self.ui_state.detailed_mode and self._has_rail_layers())

        ax_preroutes = self.fig.add_axes(_lrect(BTN_H_L, GAP_L))
        self._btn_preroutes = Button(ax_preroutes, '☐ Preroutes', color='#f4ece8')
        self._btn_preroutes.label.set_fontsize(7.5)
        self._btn_preroutes.on_clicked(lambda _: self._cycle_preroutes())
        # Always visible; dimmed (inactive) until draw_preroutes() has
        # registered a routing grid.
        self._set_button_enabled(
            self._btn_preroutes, self._has_preroute_data, on_color='#f4ece8')

        # Store the current packing position for the colorbar.
        self._ly_post_buttons = ly
        self._redraw_colorbar()

        RX, RW   = 0.83, 0.15
        BTN_H    = 0.044   # All Bundles header (now carries the bundle count)
        BTN_H_SM = 0.034   # slimmer All Layers / All Overlaps headers
        SCROLL_H = 0.033
        GAP      = 0.012
        STAT_H   = 0.028   # design-stats: one compact 'buses · nets' line
        TOP_Y    = 0.95
        BOT_Y    = 0.09   # bottom margin

        # Fixed overhead consumed by buttons, scroll arrows, the stats lines, and
        # gaps: All Bundles btn + 2 slim btns + (4 scroll arrows) + 2 stats
        # (layer-seg total + buses·nets) + (7 gaps)
        fixed_h  = BTN_H + 2 * BTN_H_SM + 4 * SCROLL_H + 2 * STAT_H + 7 * GAP
        avail    = max(TOP_Y - BOT_Y - fixed_h, 0.15)
        # Split the list space unevenly: the bundle list is the one worth
        # scanning, so give it the most; the layer list gets a slightly larger
        # slice than before (a bit more breathing room between layers) and the
        # overlap list is the most compact.
        layer_list_h   = max(avail * 0.31, 0.05)
        bundle_list_h  = max(avail * 0.43, 0.05)
        overlap_list_h = max(avail * 0.26, 0.05)

        # Top-down allocation.  y tracks the top edge of the next widget.
        y = TOP_Y

        def _rect(h, gap=0):
            nonlocal y
            y -= gap + h
            return [RX, y, RW, h]

        # ── NUTS segment total: 'N segments · M bits' (always-on header) ──
        self._ax_layer_stats = self.fig.add_axes(_rect(STAT_H))
        self._ax_layer_stats.set_axis_off()
        self._redraw_layer_stats()

        # ── All Layers ──────────────────────────────────────────────────
        ax_all_layers = self.fig.add_axes(_rect(BTN_H_SM, GAP))
        self._btn_all_layers = Button(ax_all_layers, '☑ All Layers', color='#e8e8e8')
        self._btn_all_layers.label.set_fontsize(8.5)
        self._btn_all_layers.on_clicked(lambda _: self._on_layer_toggle_all())

        # ── Per-layer custom panel ───────────────────────────────────────
        self._ax_layers = self.fig.add_axes(_rect(layer_list_h, GAP))
        self._ax_layers.set_facecolor('#f8f8f8')
        self._redraw_layer_list()

        # ── Design stats: bundles · buses · nets (always-on header) ──────
        self._ax_design_stats = self.fig.add_axes(_rect(STAT_H, GAP))
        self._ax_design_stats.set_axis_off()
        self._redraw_design_stats()

        # ── All Bundles ──────────────────────────────────────────────────
        ax_all_bundles = self.fig.add_axes(_rect(BTN_H, GAP))
        self._btn_all_bundles = Button(ax_all_bundles, f'☑ {len(self.bundles)} Bundles', color='#e8e8e8')
        self._btn_all_bundles.label.set_fontsize(8.5)
        self._btn_all_bundles.on_clicked(lambda _: self._on_bundle_toggle_all())

        # ── Bundle list: ▲ · list · ▼ ───────────────────────────────────
        ax_bscroll_up = self.fig.add_axes(_rect(SCROLL_H, GAP))
        btn_bscroll_up = Button(ax_bscroll_up, '▲', color='#f0f0f0')
        btn_bscroll_up.on_clicked(lambda _: self._scroll_bundles(-5))

        self._ax_bundles = self.fig.add_axes(_rect(bundle_list_h))
        self._ax_bundles.set_facecolor('#fafafa')
        self._redraw_bundle_list()

        ax_bscroll_dn = self.fig.add_axes(_rect(SCROLL_H))
        btn_bscroll_dn = Button(ax_bscroll_dn, '▼', color='#f0f0f0')
        btn_bscroll_dn.on_clicked(lambda _: self._scroll_bundles(+5))

        # ── All Overlaps ─────────────────────────────────────────────────
        n_ov = len(self._overlap_entries)
        ov_label = f'Overlaps ({n_ov})' if n_ov else 'No Overlaps'
        ax_all_overlaps = self.fig.add_axes(_rect(BTN_H_SM, GAP))
        self._btn_all_overlaps = Button(ax_all_overlaps, ov_label, color='#e8e8e8')
        self._btn_all_overlaps.on_clicked(lambda _: self._on_overlap_toggle_all())

        # ── Overlap list: ▲ · list · ▼ ──────────────────────────────────
        ax_oscroll_up = self.fig.add_axes(_rect(SCROLL_H, GAP))
        btn_oscroll_up = Button(ax_oscroll_up, '▲', color='#f0f0f0')
        btn_oscroll_up.on_clicked(lambda _: self._scroll_overlaps(-5))

        self._ax_overlaps = self.fig.add_axes(_rect(overlap_list_h))
        self._ax_overlaps.set_facecolor('#fff8f8')
        self._redraw_overlap_list()

        ax_oscroll_dn = self.fig.add_axes(_rect(SCROLL_H))
        btn_oscroll_dn = Button(ax_oscroll_dn, '▼', color='#f0f0f0')
        btn_oscroll_dn.on_clicked(lambda _: self._scroll_overlaps(+5))

        # Capture both right-panel geometries — the split above ('with_ov') and
        # the overlap space folded into the bundle list ('no_ov'). The widgets
        # from the bundle list downward shift by the overlap widgets' extent.
        def _lbwh(a):
            p = a.get_position()
            return [p.x0, p.y0, p.width, p.height]
        _extra = 2 * SCROLL_H + GAP + overlap_list_h
        _bl, _bd, _ob = (_lbwh(self._ax_bundles), _lbwh(ax_bscroll_dn),
                         _lbwh(ax_all_overlaps))
        self._right_rects = {
            'with_ov': {'bundle_list': _bl, 'bscroll_dn': _bd, 'ov_btn': _ob},
            'no_ov': {
                'bundle_list': [_bl[0], _bl[1] - _extra, _bl[2], _bl[3] + _extra],
                'bscroll_dn':  [_bd[0], _bd[1] - _extra, _bd[2], _bd[3]],
                'ov_btn':      [_ob[0], _ob[1] - _extra, _ob[2], _ob[3]],
            },
            'ov_widgets': {'oscroll_up': _lbwh(ax_oscroll_up),
                           'overlaps':   _lbwh(self._ax_overlaps),
                           'oscroll_dn': _lbwh(ax_oscroll_dn)},
        }
        self._ax_bscroll_dn  = ax_bscroll_dn
        self._ax_oscroll_up  = ax_oscroll_up
        self._ax_oscroll_dn  = ax_oscroll_dn
        self._btn_oscroll_up = btn_oscroll_up
        self._btn_oscroll_dn = btn_oscroll_dn
        self._ov_layout_mode = 'with_ov'
        self._apply_overlap_layout()   # no overlaps → bundle list takes the space

        self.fig.canvas.mpl_connect('scroll_event', self._on_scroll_event)
        self.fig.canvas.mpl_connect('close_event',  self._on_close)

        # ── Bottom navigation buttons ─────────────────────────────────────
        # All buttons sit in x=[0.02, 0.79] (same footprint as main plot),
        # y=0.02, h=0.06 — safely below the bottom=0.11 plot boundary so that
        # x-tick labels have room between button tops (0.08) and the plot (0.11).
        _by, _bh = 0.02, 0.06
        ax_bprev = self.fig.add_axes([0.02, _by, 0.14, _bh])
        ax_solo  = self.fig.add_axes([0.17, _by, 0.12, _bh])
        ax_bnext = self.fig.add_axes([0.30, _by, 0.14, _bh])
        ax_topos = self.fig.add_axes([0.45, _by, 0.21, _bh])

        btn_bprev = Button(ax_bprev, '◀  Prev Bundle', color='#ddeeff')
        btn_bprev.on_clicked(lambda _: self._step_bundle(-1))

        self._btn_solo = Button(ax_solo, 'Solo OFF', color='#f0f0f0')
        self._btn_solo.on_clicked(lambda _: self._toggle_solo())

        btn_bnext = Button(ax_bnext, 'Next Bundle  ▶', color='#ddeeff')
        btn_bnext.on_clicked(lambda _: self._step_bundle(+1))

        btn_topos = Button(ax_topos, 'View Topologies  ↗', color='#fff0cc')
        btn_topos.on_clicked(lambda _: self._open_topo_explorer())

        if self._ipc_session:
            import sys as _sys, os as _os
            _tools = _os.path.normpath(
                _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', '..', 'tools'))
            if _tools not in _sys.path:
                _sys.path.insert(0, _tools)
            from viz_ipc import VizIPC, POLL_MS
            self._ipc = VizIPC(self._ipc_session, verbose=self._ipc_verbose)
            self._ipc.on_message = self._on_ipc_message
            self._ipc.connect_or_serve()
            self._ipc_timer = self.fig.canvas.new_timer(interval=POLL_MS)
            self._ipc_timer.add_callback(self._ipc.poll)
            self._ipc_timer.start()
            if self._ipc_verbose:
                print(f'[buda_viz] IPC session={self._ipc_session!r} '
                      f'connected={self._ipc._connected}')
                print(f'[buda_viz] IPC timer started '
                      f'(backend={self.fig.canvas.__class__.__name__})')

        # Make the home view maximal from the first frame (not only after 'h'):
        # the fit above used the nominal figure size; track resizes to the real
        # (and macOS-maximized) window geometry as it settles.
        _install_home_fit_tracking(self)

        raise_window(self.fig)
        install_tk_geometry_resync(self.fig)
        extract_from_fullscreen_tab(self.fig)
        plt.show()
