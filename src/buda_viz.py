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

# On macOS the native 'macosx' backend can intermittently segfault,
# especially with the IPC timer or when multiple windows open.  Force TkAgg
# before pyplot locks in a backend.  This lives in the viz façade (not the
# headless command layer) so importing buda_cmds/buda_cli stays
# matplotlib-free — the headless-server requirement.
#
# Only override the IMPLICIT default ('macosx'): respect a backend the caller
# already selected explicitly — via MPLBACKEND or a prior matplotlib.use(...) —
# so headless macOS contexts (tests / PNG rendering that pick Agg) keep their
# choice instead of being dragged onto Tk.  Matplotlib 3.9+'s
# get_backend(auto_select=False) checks for an already selected backend without
# triggering lazy backend resolution; older Matplotlib falls back to the raw
# rcParams peek used here originally.
if sys.platform == 'darwin':
    import matplotlib
    try:
        _backend_selected = matplotlib.get_backend(auto_select=False) is not None
    except TypeError:
        _backend_selected = isinstance(
            dict.__getitem__(matplotlib.rcParams, 'backend'), str)
    if not _backend_selected:
        matplotlib.use('TkAgg')

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
from viz_main import (VizHighlightMixin, VizPanelsMixin, VizAbstractDrawMixin, VizDetailedDrawMixin, VizViewMixin)
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
                 on_focus_bundle=None, bundle_order_fn=None, fp_resolver=None,
                 user_ops_sink=None, groups_fn=None, cost_fn=None):
        self.fp          = fp
        # Per-bundle frame resolution (hier): fp_resolver is the session's
        # _make_topo_fp_resolver — wrapper -> the Floorplan its candidates were
        # generated in (cell-local for a cell-level template, endpoint frame
        # for cross-level; the session fp otherwise).  self.fp is re-pointed on
        # every bundle switch (_sync_bundle_fp), so ALL fp consumers — the
        # drawn blocks, the bundle-scoped Hanan grid, block hit-tests for
        # S/P, edit-op verdicts, span anchors — follow the shown bundle's
        # frame.  None (flat session / orphan explorer) = never swap.
        self._session_fp  = fp
        self._fp_resolver = fp_resolver
        # Optional (wrapper, fp) -> [[idx,...], ...] grouping of a bundle's
        # candidates into nominal-locus FAMILIES (super-candidates), the
        # session's _loci_groups.  When set, 'G' toggles family-stepping (a/d
        # jump family-to-family) and 'S' group-pins the current family.  None
        # (orphan explorer / no session) -> grouping off, every candidate its own.
        self._groups_fn   = groups_fn
        self._group_step  = False   # 'G' toggles: step by family vs by candidate
        # Optional wrapper -> ({cand_index: CandidateCost}, is_real) HYBRID cost
        # source, the session's _candidate_costs.  When set (the `debug` flag on
        # visualize_topologies), the explorer ORDERS candidate stepping (a/d) by
        # INCREASING planner cost — the real charged cost post-plan, the
        # intrinsic wirelength pre-plan — while keeping self.idx the REAL
        # candidate index (pins, group IDs, the `topo i/n` display unchanged),
        # and shows the cost + its components in the title and per-segment panel.
        # None (no debug flag) -> byte-identical to the historical WL-ordered view.
        self._cost_fn     = cost_fn
        self._cost_cache  = {}      # (bidx, npool) -> (mapping, is_real), lazily filled
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
        # Optional () -> [bundle_id, ...]: the parent BudaViz's live bundle-panel
        # order (opens-first). When set, [ / ] step in THAT order instead of
        # numeric, so the two windows page bundles the same way. None (orphan
        # explorer, no parent) -> numeric/id order.
        self._bundle_order_fn = bundle_order_fn
        
        self._block_patch_artists = []
        self._block_name_artists = []

        # Zoom state
        self._autoscale_needed = True
        self._home_xlim        = None
        self._home_ylim        = None
        self._home_data_bbox   = None   # raw data bbox (x0,x1,y0,y1) for maximal home fit
        # cmd/ctrl-z toggle state: 'bundle' = the view last fit the bundle (or
        # the selection just changed), so the next press zooms to the SELECTED
        # segment when one is picked — segment-first: the press right after a
        # j/k selection frames that segment, no double-press.  'seg' = it just
        # did a segment zoom, so the next press returns to the bundle.
        self._zoom_sel_mode    = 'bundle'

        # Listen for global visibility changes (e.g. from parent BudaVisualizer)
        self.ui_state.add_listener(self.fig_redraw)

        # Accept a single wrapper or a list for backward compatibility.
        self.wrappers = wrappers if isinstance(wrappers, list) else [wrappers]
        # Open on the requested bundle (e.g. the one matched by a viz hint).
        self.bidx     = start_bidx if 0 <= start_bidx < len(self.wrappers) else 0
        self._sync_bundle_fp()         # the opening bundle's own frame (hier)
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
        # One-shot text for the selected-segment info line (e.g. set by a layer
        # change to "V segment 3 is now on M7."); cleared after one _draw.
        self._seg_info_override = ""
        # Two-step trunk placement (T/Y): _trunk_mode is None (off) / True (H) /
        # False (V) while arming; _trunk_hover is the snapped perp coord the
        # cursor is previewing.  Hover highlights the target cell; a click / a
        # second T·Y / enter places it, escape cancels.
        self._trunk_mode  = None
        self._trunk_hover = None
        # True while _trunk_hover holds an EXACT coordinate (set by 'G' — the
        # dropped line itself) rather than a hover-snapped cell centerline:
        # placement then uses it verbatim, ignoring the cursor re-snap.
        self._trunk_hover_exact = False
        # Temporary Hanan lines added with 'G' while a trunk is armed — the
        # channel-splitting escape: the bundle grid only carries block/keepout
        # edges (+ OOB margins), so an EMPTY channel between two blocks has no
        # line for T/Y to snap to.  'G' drops one at the cursor on the armed
        # axis (y for T, x for Y); it becomes a snap target and draws with the
        # bundle grid.  Session-scoped (cleared at commit/abort).
        self._edit_grid_x = set()
        self._edit_grid_y = set()
        # "Pin trunk span to busterms" mode (P): _trunk_pin_set is None (off) or
        # a set of clicked busterm-block names whose extent along the selected
        # segment's axis becomes its span; _trunk_pin_seg is that segment index.
        # _trunk_pin_grid holds clicked Hanan-grid coordinates (a block-less
        # anchor, so a span endpoint can land on a grid line BEYOND the last
        # busterm — e.g. a C-detour trunk); _trunk_pin_hover is the grid line the
        # cursor is previewing while in pin mode.
        self._trunk_pin_seg = -1
        self._trunk_pin_set = None
        self._trunk_pin_grid = set()
        # coord -> source segment index for anchors picked off a PERPENDICULAR
        # segment: the apply stretches the pinned span to the coordinate AND
        # connects the pair (edit_connect — the partner extends to the
        # crossing when its span falls short).
        self._trunk_pin_seg_srcs = {}
        self._trunk_pin_hover = None
        self._edit_slide   = {}
        self._edit_slide_mark = None
        # 'W' bounds snap to bundle-grid Hanan lines by default; enter while a
        # refine is pending toggles the gridless sub-mode (raw coordinates).
        self._edit_slide_grid = True
        # True once the session re-layered a segment (+/-): on commit the pinned
        # layer overrides are rebuilt from the working copy so the edit sticks
        # (else a stale pinned_seg_layers from the source would re-pin the old
        # layers over the edit — Codex #302).
        self._edit_layers_changed = False
        # Session op-log: each applied edit op in .buda command syntax, printed
        # as [edit-cmd] lines (foldable into the flow script) and stored in the
        # sidecar at commit ('user_topo': base uid + ops) so a re-run replays
        # them and the USER candidate — which regeneration never produces —
        # exists again for the pin to resolve.  None = no session.
        self._edit_ops  = None
        self._edit_base = 'new'
        # Optional (bundle_id, uid, base, ops) -> None: the session's
        # _record_user_ops — a live GUI commit then stores its op-log as BDB
        # meta provenance too (user_ops:<bid>:<uid>), not just the sidecar.
        # None (orphan explorer / no BDB) = sidecar only, as before.
        self._user_ops_sink = user_ops_sink

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
        # Two-step trunk placement (T/Y): hover previews the target cell, a
        # left-click places it (right-click-drag stays the zoom-to-box gesture).
        self.fig.canvas.mpl_connect('motion_notify_event', self._on_trunk_motion)
        self.fig.canvas.mpl_connect('button_press_event', self._on_trunk_click)
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


class BudaVisualizer(VizHighlightMixin, VizPanelsMixin, VizAbstractDrawMixin, VizDetailedDrawMixin, VizViewMixin):
    def __init__(self, floorplan, bundles, sidecar_path=None, rerun_layer_fn=None,
                 rerun_fn=None, routing_grid=None, layer_stack=None,
                 net_endpoints=None, ipc_session=None, ipc_verbose=False,
                 fp_resolver=None, cuts_provider=None, user_ops_sink=None,
                 groups_fn=None):
        self.fp           = floorplan
        self.bundles      = bundles
        # Forwarded to the TopologyExplorer it opens ('v'): the session's
        # _loci_groups, so the explorer can group nominal-locus super-candidates
        # ('G' family-step / 'S' group-pin).  None (orphan viz) -> grouping off.
        self._groups_fn   = groups_fn
        # Forwarded to the TopologyExplorer it opens ('v'): the session's
        # _record_user_ops, so a GUI edit_commit stores BDB op-log
        # provenance (see TopologyExplorer.__init__).
        self._user_ops_sink = user_ops_sink
        # () -> (cuts, x_grid, y_grid) | None: fresh planner cut/band state for
        # the congestion heatmap, so an in-GUI re-run can redraw the overlay
        # against the RE-ROUTED design instead of leaving the stale original
        # planner run's utilisation shaded on screen (audit P7-05).
        self._cuts_provider = cuts_provider
        # Forwarded to the TopologyExplorer it opens ('v'): per-bundle frame
        # resolution for hier sessions (see TopologyExplorer.__init__).  The
        # main viz itself always draws the session floorplan.
        self._fp_resolver = fp_resolver
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

    _LW_MIN_PTS = 2.5   # visibility floor for physical-width segment lines

    # At most this many overflow cells get a text label (the worst by ratio).
    # Text+bbox is matplotlib's most expensive artist; on large congested
    # designs there can be thousands of overflow cells — colour already encodes
    # utilisation, so we only annotate the worst offenders.
    _HEATMAP_LABEL_CAP = 40

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
        self._btn_bscroll_up = Button(ax_bscroll_up, '▲', color='#f0f0f0')
        self._btn_bscroll_up.on_clicked(lambda _: self._scroll_bundles_page(-1))

        self._ax_bundles = self.fig.add_axes(_rect(bundle_list_h))
        self._ax_bundles.set_facecolor('#fafafa')
        self._redraw_bundle_list()

        ax_bscroll_dn = self.fig.add_axes(_rect(SCROLL_H))
        self._btn_bscroll_dn = Button(ax_bscroll_dn, '▼', color='#f0f0f0')
        self._btn_bscroll_dn.on_clicked(lambda _: self._scroll_bundles_page(+1))

        # ── All Overlaps ─────────────────────────────────────────────────
        n_ov = len(self._overlap_entries)
        ov_label = f'Overlaps ({n_ov})' if n_ov else 'No Overlaps'
        ax_all_overlaps = self.fig.add_axes(_rect(BTN_H_SM, GAP))
        self._btn_all_overlaps = Button(ax_all_overlaps, ov_label, color='#e8e8e8')
        self._btn_all_overlaps.on_clicked(lambda _: self._on_overlap_toggle_all())

        # ── Overlap list: ▲ · list · ▼ ──────────────────────────────────
        ax_oscroll_up = self.fig.add_axes(_rect(SCROLL_H, GAP))
        btn_oscroll_up = Button(ax_oscroll_up, '▲', color='#f0f0f0')
        btn_oscroll_up.on_clicked(lambda _: self._scroll_overlaps_page(-1))

        self._ax_overlaps = self.fig.add_axes(_rect(overlap_list_h))
        self._ax_overlaps.set_facecolor('#fff8f8')
        self._redraw_overlap_list()

        ax_oscroll_dn = self.fig.add_axes(_rect(SCROLL_H))
        btn_oscroll_dn = Button(ax_oscroll_dn, '▼', color='#f0f0f0')
        btn_oscroll_dn.on_clicked(lambda _: self._scroll_overlaps_page(+1))

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

        self._btn_bprev = Button(ax_bprev, '◀  Prev Bundle', color='#ddeeff')
        self._btn_bprev.on_clicked(lambda _: self._step_bundle(-1))

        self._btn_solo = Button(ax_solo, 'Solo OFF', color='#f0f0f0')
        self._btn_solo.on_clicked(lambda _: self._toggle_solo())

        self._btn_bnext = Button(ax_bnext, 'Next Bundle  ▶', color='#ddeeff')
        self._btn_bnext.on_clicked(lambda _: self._step_bundle(+1))

        self._btn_topos = Button(ax_topos, 'View Topologies  ↗', color='#fff0cc')
        self._btn_topos.on_clicked(lambda _: self._open_topo_explorer())

        if self._ipc_session:
            import sys as _sys, os as _os
            # tools/ is a sibling of src/ — ONE level up from this file
            # (audit P6-01: '..', '..' pointed outside the repo, so the
            # fallback could never resolve viz_ipc; matches buda_cli.py).
            _tools = _os.path.normpath(
                _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'tools'))
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
