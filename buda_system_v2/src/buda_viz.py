import json
import math
import os
from datetime import datetime

import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.widgets import Button

import interconnect as ic

_LAYER_COLOR = {3: '#FF8800', 4: '#007ACC', 5: '#CC0000', 6: '#00AA44', 7: '#8800CC'}
_LAYER_LABEL = {3: 'M3 V', 4: 'M4 H', 5: 'M5 V', 6: 'M6 H', 7: 'M7 V'}

def _toggle_fullscreen(fig):
    mgr = fig.canvas.manager
    if mgr:
        mgr.full_screen_toggle()

def _raise_window(fig):
    """Bring fig's window to the front (best-effort; backend-dependent)."""
    mgr = fig.canvas.manager
    if mgr is None:
        return
    # MacOSX backend exposes show() which calls makeKeyAndOrderFront: internally.
    # Other backends may use window.raise_() / window.activateWindow().
    if callable(getattr(mgr, 'show', None)):
        try:
            mgr.show()
            return
        except Exception:
            pass
    win = getattr(mgr, 'window', None)
    if win is not None:
        for method in ('raise_', 'activateWindow', 'lift'):
            if callable(getattr(win, method, None)):
                try:
                    getattr(win, method)()
                    return
                except Exception:
                    pass

# Values beyond this magnitude are the INT_MIN/2 or INT_MAX/2 sentinels
# that ConnTopology uses for "unconstrained" slide ranges.
_UNCONSTRAINED = 1_000_000_000


class TopologyExplorer:
    """Cycle through topology candidates across one or more bundles.

    Navigation:
      ← / →  (or ◀/▶ Topo buttons)  — prev / next topology within bundle
      cmd-p / cmd-n                  — prev / next topology within bundle
      [ / ]  (or ◀/▶ Bus buttons)   — prev / next bundle
      cmd-1                          — raise the main BUDA viz window
    """

    def __init__(self, fp, wrappers, sidecar_path=None, main_fig=None,
                 rerun_fn=None, refresh_fn=None):
        self.fp         = fp
        self._main_fig  = main_fig    # back-reference to main viz figure for cmd-1
        self._rerun_fn  = rerun_fn    # () -> NUTSResult | None
        self._refresh_fn = refresh_fn  # (NUTSResult) -> None
        # Accept a single wrapper or a list for backward compatibility.
        self.wrappers = wrappers if isinstance(wrappers, list) else [wrappers]
        self.bidx     = 0   # current bundle index
        self.idx      = 0   # current topology index within bundle

        # bundle_hint -> {topo_type, topo_wl, topo_index_hint, note, selected_at}
        self._selections    = {}
        self._sidecar_path  = sidecar_path
        if sidecar_path and os.path.exists(sidecar_path):
            self._load_sidecar()
            # Jump to the saved topo index so the gold border appears on open.
            saved_sel = self._find_selection(self.wrappers[0])
            if saved_sel is not None:
                saved = saved_sel.get('topo_index_hint', 0)
                n_cands = len(self.wrappers[0].candidates)
                if 0 <= saved < n_cands:
                    self.idx = saved

        self.fig = plt.figure(figsize=(13, 10))
        self.fig.patch.set_facecolor('#f0f0f0')

        # Main axes — single button row below; leave y=0.10 for x-tick labels
        self.ax = self.fig.add_axes([0.05, 0.10, 0.90, 0.84])

        # ── Single button row ────────────────────────────────────────────
        # Order: ◀Bus  ◀Topo  ★Select  ✕Desel  [▶Re-run]  Topo▶  Bus▶
        # Positions are computed from weights so adding/removing Re-run
        # doesn't require touching all the other numbers.
        _BY, _BH   = 0.022, 0.038   # row y and height
        _MARGIN    = 0.010
        _GAP       = 0.008

        _btn_specs = [
            ('◀  Bus',     '#d9f5d9', 1.0),
            ('◀  Topo',    '#ddeeff', 1.0),
            ('★  Select',  '#f0f0f0', 1.0),
            ('✕  Desel',   '#f0f0f0', 0.85),
        ]
        if rerun_fn is not None:
            _btn_specs.append(('▶  Re-run', '#ffe0b0', 1.2))
        _btn_specs += [
            ('Topo  ▶',   '#ddeeff', 1.0),
            ('Bus  ▶',    '#d9f5d9', 1.0),
        ]

        _n          = len(_btn_specs)
        _total_w    = sum(w for _, _, w in _btn_specs)
        _avail      = 1.0 - 2 * _MARGIN - (_n - 1) * _GAP
        _unit       = _avail / _total_w

        _bax = []
        _x = _MARGIN
        for _label, _color, _weight in _btn_specs:
            _bw = _weight * _unit
            _bax.append(self.fig.add_axes([_x, _BY, _bw, _BH]))
            _x += _bw + _GAP

        _i = 0
        ax_bprev    = _bax[_i]; _i += 1
        ax_tprev    = _bax[_i]; _i += 1
        ax_select   = _bax[_i]; _i += 1
        ax_deselect = _bax[_i]; _i += 1
        ax_rerun    = _bax[_i] if rerun_fn is not None else None
        if rerun_fn is not None: _i += 1
        ax_tnext    = _bax[_i]; _i += 1
        ax_bnext    = _bax[_i]

        self._btn_bprev = Button(ax_bprev, '◀  Bus',    color='#d9f5d9')
        self._btn_tprev = Button(ax_tprev, '◀  Topo',   color='#ddeeff')
        self._btn_select   = Button(ax_select,   '★  Select', color='#f0f0f0')
        self._btn_deselect = Button(ax_deselect, '✕  Desel',  color='#f0f0f0')
        self._btn_tnext = Button(ax_tnext, 'Topo  ▶',   color='#ddeeff')
        self._btn_bnext = Button(ax_bnext, 'Bus  ▶',    color='#d9f5d9')

        self._btn_bprev.on_clicked(lambda _: self._step_bundle(-1))
        self._btn_tprev.on_clicked(lambda _: self._step_topo(-1))
        self._btn_select.on_clicked(lambda _: self._select_current())
        self._btn_deselect.on_clicked(lambda _: self._deselect_current())
        self._btn_tnext.on_clicked(lambda _: self._step_topo(+1))
        self._btn_bnext.on_clicked(lambda _: self._step_bundle(+1))

        # Hide bus buttons when only one bundle is loaded.
        if len(self.wrappers) == 1:
            ax_bprev.set_visible(False)
            ax_bnext.set_visible(False)

        if ax_rerun is not None:
            self._btn_rerun = Button(ax_rerun, '▶  Re-run', color='#ffe0b0')
            self._btn_rerun.on_clicked(lambda _: self._rerun_and_refresh())
        else:
            self._btn_rerun = None

        self.fig.canvas.mpl_connect('key_press_event', self._on_key)

        self._draw()

    # ------------------------------------------------------------------

    @property
    def wrapper(self):
        return self.wrappers[self.bidx]

    @property
    def topos(self):
        return self.wrapper.candidates

    # ------------------------------------------------------------------

    def _build_conn_topo(self, topo):
        ct = ic.ConnTopology()
        ct.build(topo, self.fp)
        return ct

    def _draw_busterm_markers(self, topo, ct, viz_lw):
        """Draw a diamond at every busterm connection point."""
        ax = self.ax
        msz = viz_lw * 1.1 + 3

        for raw_seg, cs in zip(topo.segments, ct.segs()):
            col = _LAYER_COLOR.get(raw_seg.layer_hint, '#888888')
            for conn in cs.conns:
                if conn.kind != ic.SegConnKind.BUSTERM:
                    continue
                if cs.horiz:
                    px, py = conn.at_pos, cs.perp_pos
                    dx = -8 if px <= (cs.along_lo + cs.along_hi) / 2 else +8
                    dy = 0
                    ha = 'right' if dx < 0 else 'left'
                    va = 'center'
                else:
                    px, py = cs.perp_pos, conn.at_pos
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

        for raw_seg, cs in zip(topo.segments, ct.segs()):
            col = _LAYER_COLOR.get(raw_seg.layer_hint, '#888888')

            if cs.horiz:
                band_y0 = clamp_y(cs.perp_lo)
                band_y1 = clamp_y(cs.perp_hi)
                if band_y0 >= band_y1:
                    continue
                ax.add_patch(patches.Rectangle(
                    (cs.along_lo, band_y0), cs.along_hi - cs.along_lo, band_y1 - band_y0,
                    linewidth=0, facecolor=col, alpha=0.10, zorder=3))
                for y_b, label in ((cs.perp_lo, 'lo'), (cs.perp_hi, 'hi')):
                    if abs(y_b) < _UNCONSTRAINED:
                        ax.plot([cs.along_lo, cs.along_hi], [y_b, y_b],
                                color=col, linewidth=0.9, linestyle=':', alpha=0.7, zorder=4)
                        ax.text((cs.along_lo + cs.along_hi) / 2, y_b, f' {y_b}',
                                fontsize=6, color=col, va='bottom' if label == 'lo' else 'top',
                                ha='center', zorder=5, alpha=0.85)
            else:
                band_x0 = clamp_x(cs.perp_lo)
                band_x1 = clamp_x(cs.perp_hi)
                if band_x0 >= band_x1:
                    continue
                ax.add_patch(patches.Rectangle(
                    (band_x0, cs.along_lo), band_x1 - band_x0, cs.along_hi - cs.along_lo,
                    linewidth=0, facecolor=col, alpha=0.10, zorder=3))
                for x_b, label in ((cs.perp_lo, 'lo'), (cs.perp_hi, 'hi')):
                    if abs(x_b) < _UNCONSTRAINED:
                        ax.plot([x_b, x_b], [cs.along_lo, cs.along_hi],
                                color=col, linewidth=0.9, linestyle=':', alpha=0.7, zorder=4)
                        ax.text(x_b, (cs.along_lo + cs.along_hi) / 2, f' {x_b}',
                                fontsize=6, color=col, va='center',
                                ha='left' if label == 'lo' else 'right', zorder=5, alpha=0.85)

    # ------------------------------------------------------------------
    # Selection DB helpers
    # ------------------------------------------------------------------

    def _bundle_hint(self, wrapper=None):
        w = wrapper or self.wrapper
        names = w.original_bundle.get_net_names()
        return names[0] if names else f"bundle_{w.original_bundle.id}"

    def _find_selection(self, wrapper=None):
        """Return the saved selection dict for the given wrapper, or None.

        Tries the current bundle_hint first (first net name); falls back to
        matching by bundle_id so that sidecars created with older hint
        conventions (e.g. 't0_b0' instead of 't0_b0_00') still work.
        """
        w   = wrapper or self.wrapper
        sel = self._selections.get(self._bundle_hint(w))
        if sel is None:
            bid = w.original_bundle.id
            sel = next((s for s in self._selections.values()
                        if s.get('bundle_id') == bid), None)
        return sel

    def _current_is_selected(self):
        sel  = self._find_selection()
        if sel is None:
            return False
        topo = self.topos[self.idx]
        return (topo.type == sel['topo_type'] and
                topo.estimated_wirelength == sel['topo_wl'])

    def _select_current(self):
        topo = self.topos[self.idx]
        hint = self._bundle_hint()
        # Remove any old entry for this bundle (may have a different hint key).
        old_sel = self._find_selection()
        if old_sel is not None:
            stale_key = next((k for k, v in self._selections.items()
                              if v is old_sel), None)
            if stale_key and stale_key != hint:
                del self._selections[stale_key]
        self._selections[hint] = {
            'bundle_id':       self.wrapper.original_bundle.id,
            'topo_type':       topo.type,
            'topo_wl':         topo.estimated_wirelength,
            'topo_index_hint': self.idx,
            'note':            '',
            'selected_at':     datetime.now().isoformat(timespec='seconds'),
        }
        self._save_sidecar()
        self._draw()

    def _deselect_current(self):
        hint    = self._bundle_hint()
        old_sel = self._find_selection()
        if old_sel is not None:
            stale_key = next((k for k, v in self._selections.items()
                              if v is old_sel), hint)
            self._selections.pop(stale_key, None)
            self._save_sidecar()
        self._draw()

    def _load_sidecar(self):
        try:
            with open(self._sidecar_path) as f:
                data = json.load(f)
            for entry in data.get('selections', []):
                self._selections[entry['bundle_hint']] = {
                    k: entry[k] for k in
                    ('bundle_id', 'topo_type', 'topo_wl',
                     'topo_index_hint', 'note', 'selected_at')
                }
            print(f"Loaded {len(self._selections)} selection(s) from {self._sidecar_path}")
        except Exception as e:
            print(f"Warning: could not load sidecar {self._sidecar_path}: {e}")

    def _save_sidecar(self):
        if not self._sidecar_path:
            return
        entries = [{'bundle_hint': hint, **sel}
                   for hint, sel in sorted(self._selections.items())]
        try:
            with open(self._sidecar_path, 'w') as f:
                json.dump({'selections': entries}, f, indent=2)
            print(f"Saved {len(entries)} selection(s) to {self._sidecar_path}")
        except Exception as e:
            print(f"Warning: could not save sidecar {self._sidecar_path}: {e}")

    # ------------------------------------------------------------------

    def _reset_rerun_btn(self):
        if self._btn_rerun is not None:
            self._btn_rerun.label.set_text('▶  Re-run')
            self._btn_rerun.ax.set_facecolor('#ffe0b0')

    def _step_topo(self, delta):
        self.idx = (self.idx + delta) % len(self.topos)
        self._reset_rerun_btn()
        self._draw()

    def _step_bundle(self, delta):
        self.bidx = (self.bidx + delta) % len(self.wrappers)
        self.idx  = 0
        self._reset_rerun_btn()
        self._draw()

    def _on_key(self, event):
        if event.key in ('cmd+q', 'ctrl+q'):    plt.close('all'); return
        if event.key in ('cmd+f', 'ctrl+f'):    _toggle_fullscreen(self.fig); return
        if event.key in ('cmd+1', 'ctrl+1'):
            if self._main_fig is not None: _raise_window(self._main_fig)
            return
        if event.key in ('left',  'a'):         self._step_topo(-1)
        if event.key in ('right', 'd'):         self._step_topo(+1)
        if event.key in ('n', 'cmd+n', 'ctrl+n'):    self._step_topo(+1)
        if event.key in ('p', 'cmd+p', 'ctrl+p'):    self._step_topo(-1)
        if event.key in ('[', 'pageup'):        self._step_bundle(-1)
        if event.key in (']', 'pagedown'):      self._step_bundle(+1)
        if event.key == 's':                    self._select_current()
        if event.key == 'x':                    self._deselect_current()
        if event.key == 'r':                    self._rerun_and_refresh()

    def _rerun_and_refresh(self):
        """Select current topology, re-run NUTS, and refresh the main viz."""
        if self._rerun_fn is None:
            return
        # Persist the currently displayed topology as the selection for this bundle.
        self._select_current()
        # Visual feedback while running.
        if self._btn_rerun is not None:
            self._btn_rerun.label.set_text('⏳ Running…')
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
            if self._main_fig is not None:
                _raise_window(self._main_fig)

    def _draw(self):
        ax = self.ax
        ax.clear()

        topo  = self.topos[self.idx]
        n     = len(self.topos)
        bid   = self.wrapper.original_bundle.id
        wl    = topo.estimated_wirelength
        ct     = self._build_conn_topo(topo)
        viz_lw = min(3.0 + math.log2(1 + self.wrapper.width) * 1.5, 14.0)

        is_sel = self._current_is_selected()
        has_any_sel = self._find_selection() is not None

        # ── Update selection button states ──
        if is_sel:
            self._btn_select.label.set_text('★  Selected')
            self._btn_select.ax.set_facecolor('#aadd88')
        else:
            self._btn_select.label.set_text('★  Select Topo')
            self._btn_select.ax.set_facecolor('#f0f0f0')
        self._btn_deselect.ax.set_facecolor('#ffbbaa' if has_any_sel else '#f0f0f0')

        # ── Axes border: gold when selected, subtle grey otherwise ──
        border_col = '#FFD700' if is_sel else '#cccccc'
        border_lw  = 3.5      if is_sel else 0.8
        for spine in ax.spines.values():
            spine.set_edgecolor(border_col)
            spine.set_linewidth(border_lw)

        nb   = len(self.wrappers)
        n_bt = sum(1 for cs in ct.segs()
                   for c in cs.conns if c.kind == ic.SegConnKind.BUSTERM)
        bus_label = (f"bus {self.bidx + 1}/{nb} · " if nb > 1 else "")
        sel_badge = "  ★ SELECTED" if is_sel else ""
        ax.set_title(
            f"{bus_label}Bundle {bid}  ·  topo {self.idx + 1}/{n}"
            f"  ·  {topo.type}  ·  WL={wl}"
            f"  ·  bterms={n_bt}"
            + (f" (+{topo.pass_through_count} pass-thru)" if topo.pass_through_count else "")
            + f"  ·  bsegs={len(topo.segments)}{sel_badge}",
            fontsize=13, pad=10,
            color='#886600' if is_sel else 'black')
        if self.fig.canvas.manager:
            bid_  = self.wrapper.original_bundle.id
            names_ = self.wrapper.original_bundle.get_net_names()
            net0  = names_[0] if names_ else f"B{bid_}"
            self.fig.canvas.manager.set_window_title(
                f"{net0} (Bundle {bid_})")

        # Floorplan blocks
        for name, rect in self.fp.get_all_blocks():
            w = rect.x2 - rect.x1
            h = rect.y2 - rect.y1
            ax.add_patch(patches.Rectangle(
                (rect.x1, rect.y1), w, h,
                linewidth=1.5, edgecolor='#444444', facecolor='#d9d9d9',
                alpha=0.55, zorder=1))
            ax.text((rect.x1 + rect.x2) / 2, (rect.y1 + rect.y2) / 2,
                    name, ha='center', va='center',
                    fontsize=8, fontweight='bold', color='#333333', zorder=2)

        # Hanan grid
        xs, ys = self.fp.get_hanan_grid()
        for x in xs:
            ax.axvline(x=x, color='#cccccc', linestyle='--', linewidth=0.5, zorder=0)
        for y in ys:
            ax.axhline(y=y, color='#cccccc', linestyle='--', linewidth=0.5, zorder=0)

        # Slide-range bands (drawn before segments so segments sit on top)
        self._draw_slide_spans(topo, ct)

        # Topology segments — width proportional to bundle width
        for seg in topo.segments:
            col = _LAYER_COLOR.get(seg.layer_hint, '#888888')
            ax.plot([seg.start.x, seg.end.x], [seg.start.y, seg.end.y],
                    color=col, linewidth=viz_lw,
                    solid_capstyle='round', zorder=10)
            ax.plot(seg.start.x, seg.start.y, 'o',
                    color=col, markersize=viz_lw * 0.6, zorder=11)
            ax.plot(seg.end.x, seg.end.y, 'o',
                    color=col, markersize=viz_lw * 0.6, zorder=11)

        # Busterm diamonds (on top of segments and junction dots)
        self._draw_busterm_markers(topo, ct, viz_lw)

        # Legend
        from matplotlib.lines import Line2D
        used_layers = sorted({s.layer_hint for seg in topo.segments
                               for s in [seg]})
        handles = [Line2D([0], [0], color=_LAYER_COLOR.get(l, '#888'), lw=3,
                          label=_LAYER_LABEL.get(l, f'Layer {l}'))
                   for l in used_layers]
        handles.append(patches.Patch(facecolor='#888888', alpha=0.20,
                                     label='slide range'))
        handles.append(Line2D([0], [0], color='#888888', lw=0.9, linestyle=':',
                               alpha=0.7, label='slide bound'))
        handles.append(Line2D([0], [0], marker='D', color='w',
                               markerfacecolor='#888888', markeredgecolor='white',
                               markersize=8, label='bus-term'))
        ax.legend(handles=handles, loc='upper right', fontsize=9)

        ax.set_aspect('equal')
        ax.autoscale_view()
        self.fig.canvas.draw_idle()

    def show(self):
        plt.show()


class BudaVisualizer:
    def __init__(self, floorplan, bundles, sidecar_path=None, rerun_layer_fn=None,
                 rerun_fn=None):
        self.fp           = floorplan
        self.bundles      = bundles
        self._selections_path = (
            os.path.splitext(sidecar_path)[0] + '.json'
            if sidecar_path else None
        )
        self.fig, self.ax = plt.subplots(figsize=(14, 12))
        self.fig.patch.set_facecolor('#f0f0f0')
        if sidecar_path and self.fig.canvas.manager:
            self.fig.canvas.manager.set_window_title(
                os.path.splitext(os.path.basename(sidecar_path))[0]
            )

        # bundle_id -> list of dicts {artist, alpha, lw, is_band, layer}
        self._bundle_artists    = {}
        self._highlighted       = None
        self._highlighted_set   = set()   # multi-highlight (overlap pair selection)
        self._selected_overlap  = None    # OverlapDetail currently selected
        self._overlap_state     = 0       # 0=none 1=both 2=A-only 3=B-only
        self._highlight_overlays = []   # thin boundary lines added on selection
        self._solo           = False
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
        self._ax_bundles     = None
        self._ax_overlaps    = None
        self._rerun_layer_fn = rerun_layer_fn   # (layer_id: int) -> NUTSResult | None
        self._rerun_fn       = rerun_fn         # () -> NUTSResult | None  (full re-run)
        self._overlap_entries = []   # sorted list of OverlapDetail from nuts_result
        self._overlap_scroll = 0
        self._nuts_result    = None  # stored in draw_nuts_tracks for the overlap panel
        self._topo_explorer  = None
        self._pick_happened  = False
        self._cbar_ax        = None   # colorbar axes for congestion heatmap
        self._heatmap_artists = []    # patches + texts created by draw_congestion_map
        self._block_name_artists = [] # text artists created by draw_blocks
        self._heatmap_visible    = True
        self._block_names_visible = True
        self._bustermss_visible  = True
        self._vias_conns_visible = True
        self._all_vis            = True
        self._home_xlim          = None
        self._home_ylim          = None
        self._busterm_artists    = []    # driver/receiver terminal artists
        self._vias_conns_artists = []    # via and busterm-conn marker artists
        self._btn_heatmap    = None
        self._btn_blknames   = None
        self._btn_bustermss  = None
        self._btn_vias_conns = None
        self._btn_all        = None
        self._btn_detailed   = None

        # Detailed NUTS (Stage 9) visualisation state.
        self._detailed_mode          = False
        self._detailed_bundle_artists = {}   # bid -> [{artist,alpha,lw,is_band,layer}]
        self._grid_rail_artists      = []    # POWER/GND/CLK stripe patches (not per-bundle)
        self._layer_is_h             = {}    # layer_id -> bool (populated by draw_detailed_tracks)

        self.fig.canvas.mpl_connect('pick_event',         self._on_pick)
        self.fig.canvas.mpl_connect('button_press_event', self._on_click)
        self.fig.canvas.mpl_connect('key_press_event',    self._on_key)

    # ------------------------------------------------------------------
    # Artist registry & interaction
    # ------------------------------------------------------------------

    def _register(self, bundle_id, artist, *, alpha, lw=None, is_band=False, layer=None):
        artist.set_picker(5)
        self._bundle_artists.setdefault(bundle_id, []).append({
            'artist':  artist,
            'alpha':   alpha,
            'lw':      lw,
            'is_band': is_band,
            'layer':   layer,
        })

    def _on_pick(self, event):
        self._pick_happened = True
        active_reg = (self._detailed_bundle_artists
                      if self._detailed_mode else self._bundle_artists)
        for bid, entries in active_reg.items():
            for e in entries:
                if event.artist is e['artist']:
                    self._set_highlight(bid)
                    return

    def _on_click(self, event):
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
        w = next((w for w in self.bundles if w.original_bundle.id == bid), None)
        if w:
            names = w.original_bundle.get_net_names()
            return names[0] if names else f"B{bid}"
        return f"B{bid}"

    def _bundle_bits(self, bid):
        """Return the number of nets (bit-width) for bid, or 0 if unknown."""
        w = next((w for w in self.bundles if w.original_bundle.id == bid), None)
        if w:
            return len(w.original_bundle.get_net_names())
        return 0

    def _set_highlight(self, bundle_id):
        if bundle_id == self._highlighted and not self._highlighted_set:
            bundle_id = None
        self._highlighted      = bundle_id
        self._highlighted_set  = set()
        self._selected_overlap = None
        self._overlap_state    = 0
        self._refresh_highlight()

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

    def _refresh_highlight(self):
        """Apply highlight + solo + layer-visibility + bundle-visibility to all artists."""
        from matplotlib.lines import Line2D as MplLine2D

        bundle_id = self._highlighted
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
                      if self._detailed_mode else self._bundle_artists)

        for bid, entries in active_reg.items():
            bundle_on = self._bundle_visible.get(bid, True)
            selected  = (active_bids is None) or (bid in active_bids)

            for e in entries:
                a = e['artist']

                # Bundle visibility: hard gate — always off when hidden.
                if not bundle_on:
                    a.set_alpha(0.0)
                    if e['lw'] is not None: a.set_linewidth(e['lw'])
                    continue

                # Layer visibility: hard gate.
                if e['layer'] is not None and not self._layer_visible.get(e['layer'], True):
                    a.set_alpha(0.0)
                    if e['lw'] is not None: a.set_linewidth(e['lw'])
                    continue

                if e['lw'] is not None:
                    a.set_linewidth(e['lw'])  # width never changes

                if active_bids is None:
                    a.set_alpha(e['alpha'])
                elif selected:
                    a.set_alpha(0.2 if e['is_band'] else 1.0)
                else:
                    a.set_alpha(0.0 if self._solo else (0.03 if e['is_band'] else 0.1))

        # Draw thin white boundary lines over each selected bundle's segments.
        # Skipped in detailed mode — overlays would cover bit-wire lines entirely.
        if active_bids is not None and not self._detailed_mode:
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
            self.ax.set_title(
                f"BUDA — Overlap: {msg}  (click row to cycle, All Overlaps to clear)",
                fontsize=13)
        elif bundle_id is not None:
            solo_hint = "  [Solo ON]" if self._solo else ""
            bname = self._bundle_name(bundle_id)
            nbits = self._bundle_bits(bundle_id)
            bits_str = f" ({nbits} bits)" if nbits > 0 else ""
            self.ax.set_title(
                f"BUDA — B{bundle_id} {bname}{bits_str} selected{solo_hint}  "
                f"(click again or click background to deselect)",
                fontsize=13)
        else:
            self.ax.set_title(
                "BUDA: Non-Uniform Track Sharing (NUTS)  "
                "— click a bus-term or bus-seg to highlight",
                fontsize=13)

        self._redraw_bundle_list()
        self._redraw_overlap_list()
        self.fig.canvas.draw_idle()

    def _step_bundle(self, delta):
        if not self._bid_list:
            return
        if self._highlighted not in self._bid_list:
            idx = 0 if delta > 0 else len(self._bid_list) - 1
        else:
            idx = (self._bid_list.index(self._highlighted) + delta) % len(self._bid_list)
        self._highlighted = self._bid_list[idx]
        self._refresh_highlight()

    def _toggle_heatmap(self):
        self._heatmap_visible = not self._heatmap_visible
        vis = self._heatmap_visible
        for a in self._heatmap_artists:
            a.set_visible(vis)
        if self._cbar_ax is not None:
            self._cbar_ax.set_visible(vis)
        label = '☑ Heatmap' if vis else '☐ Heatmap'
        if self._btn_heatmap is not None:
            self._btn_heatmap.label.set_text(label)
        self.fig.canvas.draw_idle()

    def _toggle_all(self):
        self._all_vis = not self._all_vis
        vis = self._all_vis
        self._btn_all.label.set_text('☑ All' if vis else '☐ All')

        # Heatmap
        self._heatmap_visible = vis
        for a in self._heatmap_artists:
            a.set_visible(vis)
        if self._cbar_ax is not None:
            self._cbar_ax.set_visible(vis)
        if self._btn_heatmap is not None:
            self._btn_heatmap.label.set_text('☑ Heatmap' if vis else '☐ Heatmap')

        # Block names
        self._block_names_visible = vis
        for txt in self._block_name_artists:
            txt.set_visible(vis)
        if self._btn_blknames is not None:
            self._btn_blknames.label.set_text('☑ Blk Names' if vis else '☐ Blk Names')

        # Busterms
        self._bustermss_visible = vis
        for a in self._busterm_artists:
            a.set_visible(vis)
        if self._btn_bustermss is not None:
            self._btn_bustermss.label.set_text('☑ Busterms' if vis else '☐ Busterms')

        # Vias/Conns
        self._vias_conns_visible = vis
        for a in self._vias_conns_artists:
            a.set_visible(vis)
        if self._btn_vias_conns is not None:
            self._btn_vias_conns.label.set_text('☑ Vias/Conns' if vis else '☐ Vias/Conns')

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
            self._btn_all_bundles.label.set_text('☑ All Bundles' if vis else '☐ All Bundles')
            self._btn_all_bundles.ax.set_facecolor('#e8e8e8' if vis else '#cccccc')
        self._redraw_bundle_list()

        self._refresh_highlight()
        self.fig.canvas.draw_idle()

    def _toggle_bustermss(self):
        self._bustermss_visible = not self._bustermss_visible
        vis = self._bustermss_visible
        for a in self._busterm_artists:
            a.set_visible(vis)
        label = '☑ Busterms' if vis else '☐ Busterms'
        self._btn_bustermss.label.set_text(label)
        self.fig.canvas.draw_idle()

    def _toggle_vias_conns(self):
        self._vias_conns_visible = not self._vias_conns_visible
        vis = self._vias_conns_visible
        for a in self._vias_conns_artists:
            a.set_visible(vis)
        label = '☑ Vias/Conns' if vis else '☐ Vias/Conns'
        self._btn_vias_conns.label.set_text(label)
        self.fig.canvas.draw_idle()

    def _toggle_block_names(self):
        self._block_names_visible = not self._block_names_visible
        vis = self._block_names_visible
        for txt in self._block_name_artists:
            txt.set_visible(vis)
        label = '☑ Blk Names' if vis else '☐ Blk Names'
        if self._btn_blknames is not None:
            self._btn_blknames.label.set_text(label)
        self.fig.canvas.draw_idle()

    def _toggle_solo(self):
        self._solo = not self._solo
        if self._btn_solo is not None:
            if self._solo:
                self._btn_solo.label.set_text('Solo  ON')
                self._btn_solo.ax.set_facecolor('#ffddaa')
            else:
                self._btn_solo.label.set_text('Solo OFF')
                self._btn_solo.ax.set_facecolor('#f0f0f0')
        self._refresh_highlight()

    # ------------------------------------------------------------------
    # Layer panel (custom, replaces CheckButtons)
    # ------------------------------------------------------------------

    def _on_layer_toggle(self, lid):
        self._layer_visible[lid] = not self._layer_visible.get(lid, True)
        self._redraw_layer_list()
        self._refresh_highlight()

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

            # Layer name ("M4 H").
            ax.text(0.22, y_name, _LAYER_LABEL.get(lid, f'M{lid}'),
                    transform=ax.transAxes, fontsize=9, color=txt_color,
                    va='center', clip_on=True, fontweight='bold')

            # Stats line ("6 segs, 48 bits").
            n_segs = layer_seg_count.get(lid, 0)
            n_bits = layer_bit_count.get(lid, 0)
            if n_segs:
                stats_txt = f'{n_segs} segs, {n_bits} bits'
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

    def _redraw_nuts_tracks(self, nuts_result):
        """Remove all NUTS track artists and redraw from an updated result."""
        # Detach every registered artist from the axes.
        for entries in self._bundle_artists.values():
            for e in entries:
                try: e['artist'].remove()
                except Exception: pass
        for art in self._highlight_overlays:
            try: art.remove()
            except Exception: pass
        self._highlight_overlays.clear()
        self._bundle_artists.clear()

        # Redraw segments at new track positions.
        self.draw_nuts_tracks(nuts_result)

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

        self._refresh_highlight()

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

    def _redraw_bundle_list(self):
        """Clear and redraw all rows in the bundle checkbox list."""
        ax = self._ax_bundles
        if ax is None:
            return
        ax.clear()
        ax.set_axis_off()
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)

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

            # Build label: "B{bid} {name} ({nbits} bits)", truncated to fit.
            name  = self._bundle_name(bid)
            nbits = self._bundle_bits(bid)
            bits_suffix = f" ({nbits} bits)" if nbits > 0 else ""
            prefix = f"B{bid} "
            max_name = max(4, 20 - len(prefix) - len(bits_suffix))
            name_part = name if len(name) <= max_name else name[:max_name - 1] + '…'
            full  = f"{prefix}{name_part}{bits_suffix}"

            # Radio indicator (left, for selection) and checkbox (for visibility).
            radio_char = '◉' if bid == self._highlighted else '○'
            vis_char   = '☑' if on else '☐'
            txt_color  = '#111111' if on else '#bbbbbb'
            sel_color  = '#004488' if bid == self._highlighted else txt_color

            ax.text(0.03, y, radio_char,
                    transform=ax.transAxes,
                    fontsize=7, color=sel_color,
                    va='center', clip_on=True)
            ax.text(0.20, y, f"{vis_char} {full}",
                    transform=ax.transAxes,
                    fontsize=7, color=txt_color,
                    va='center', clip_on=True)

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
        """Radio column (x<0.18): select bundle.  Checkbox column (x>=0.18): toggle visibility."""
        ax = self._ax_bundles
        if ax is None or event.ydata is None or event.xdata is None:
            return
        n_vis = self._bundle_list_n_visible()
        row = int((1.0 - event.ydata) * n_vis)
        idx = self._bundle_scroll + row
        bids = self._bid_list
        if 0 <= idx < len(bids):
            bid = bids[idx]
            if event.xdata < 0.18:
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
            self._btn_all_bundles.label.set_text(
                '☑ All Bundles' if new_state else '☐ All Bundles')
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

    def _open_topo_explorer(self):
        if self._highlighted is None:
            return
        wrapper = next((w for w in self.bundles
                        if w.original_bundle.id == self._highlighted), None)
        if wrapper is None or not wrapper.candidates:
            return
        refresh_fn = self._redraw_nuts_tracks if self._rerun_fn is not None else None
        self._topo_explorer = TopologyExplorer(
            self.fp, wrapper,
            sidecar_path=self._selections_path,
            main_fig=self.fig,
            rerun_fn=self._rerun_fn,
            refresh_fn=refresh_fn)
        self._topo_explorer.fig.show()

    def _on_key(self, event):
        if event.key in ('cmd+q', 'ctrl+q'): plt.close('all'); return
        if event.key in ('cmd+f', 'ctrl+f'): _toggle_fullscreen(self.fig); return
        if event.key in ('cmd+z', 'ctrl+z'): self._zoom_to_bundle(); return
        if event.key in ('cmd+a', 'ctrl+a'):
            if self._home_xlim is not None:
                self.ax.set_xlim(self._home_xlim)
                self.ax.set_ylim(self._home_ylim)
                toolbar = getattr(self.fig.canvas, 'toolbar', None)
                if toolbar is not None:
                    toolbar.update()   # reset toolbar nav stack to current limits
            else:
                self.ax.autoscale()
            self.fig.canvas.draw_idle()
            return
        if event.key in ('n', 'cmd+n', 'ctrl+n'): self._step_bundle(+1)
        if event.key in ('p', 'cmd+p', 'ctrl+p'): self._step_bundle(-1)
        if event.key in ('[', 'pageup'):   self._step_bundle(-1)
        if event.key in (']', 'pagedown'): self._step_bundle(+1)
        if event.key in ('v', 't', 'cmd+t', 'ctrl+t'): self._open_topo_explorer()
        if event.key == 'd' and self._detailed_bundle_artists: self._toggle_detailed()

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def draw_blocks(self):
        self._block_name_artists = []
        for name, rect in self.fp.get_all_blocks():
            w = rect.x2 - rect.x1
            h = rect.y2 - rect.y1
            self.ax.add_patch(patches.Rectangle(
                (rect.x1, rect.y1), w, h,
                linewidth=2, edgecolor='#444444', facecolor='#d9d9d9',
                alpha=0.6, zorder=1))
            cx, cy = (rect.x1 + rect.x2) / 2, (rect.y1 + rect.y2) / 2
            txt = self.ax.text(cx, cy, name,
                               ha='center', va='center', fontsize=9, fontweight='bold',
                               color='#333333', zorder=2, clip_on=True)
            self._block_name_artists.append(txt)

    def draw_congestion_map(self, cuts, xs, ys):
        """Shade each Hanan (cut × perpendicular-band) cell by utilisation ratio.

        V-cuts (vertical lines, counting H-segments) are shaded per Y-band.
        H-cuts (horizontal lines, counting V-segments) are shaded per X-band.
        Each cell gets its own colour so the map shows true 2D congestion.
        """
        cmap = plt.cm.RdYlGn_r
        self._heatmap_artists = []

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
                x_lo = xs[xi]
                x_hi = xs[xi + 1] if xi + 1 < len(xs) else cx + 20
                perp_grid = ys
            else:
                cy = cut.p1.y
                y_idx = [i for i, y in enumerate(ys) if y <= cy]
                if not y_idx: continue
                yi = y_idx[-1]
                y_lo = ys[yi]
                y_hi = ys[yi + 1] if yi + 1 < len(ys) else cy + 20
                perp_grid = xs

            for b in range(min(n_bands, len(perp_grid) - 1)):
                cap   = cut.band_cap[b]
                usage = cut.band_usage[b]
                if usage == 0:
                    continue
                if cap > 0:
                    ratio = usage / cap
                else:
                    ratio = 2.0  # blocked cell

                color = cmap(min(ratio, 1.5) / 1.5)
                alpha = 0.12 + 0.22 * min(ratio, 1.0)

                p_lo = perp_grid[b]
                p_hi = perp_grid[b + 1]

                if is_vcut:
                    rect = patches.Rectangle(
                        (x_lo, p_lo), x_hi - x_lo, p_hi - p_lo,
                        linewidth=0, facecolor=color, alpha=alpha, zorder=3)
                    self.ax.add_patch(rect)
                    self._heatmap_artists.append(rect)
                    if ratio > 1.0:
                        label = "BLOCK" if cap <= 0 else f"OVF\n{ratio:.0%}"
                        txt = self.ax.text((x_lo + x_hi) / 2, (p_lo + p_hi) / 2,
                                           label, fontsize=6, color='darkred',
                                           ha='center', va='center', zorder=4,
                                           fontweight='bold', clip_on=True)
                        self._heatmap_artists.append(txt)
                else:
                    rect = patches.Rectangle(
                        (p_lo, y_lo), p_hi - p_lo, y_hi - y_lo,
                        linewidth=0, facecolor=color, alpha=alpha, zorder=3)
                    self.ax.add_patch(rect)
                    self._heatmap_artists.append(rect)
                    if ratio > 1.0:
                        label = "BLOCK" if cap <= 0 else f"OVF\n{ratio:.0%}"
                        txt = self.ax.text((p_lo + p_hi) / 2, (y_lo + y_hi) / 2,
                                           label, fontsize=6, color='darkred',
                                           ha='center', va='center', zorder=4,
                                           fontweight='bold', clip_on=True)
                        self._heatmap_artists.append(txt)

        # Apply current visibility state.
        vis = self._heatmap_visible
        for a in self._heatmap_artists:
            a.set_visible(vis)

        # Colorbar legend — created once per draw, rebuilt on subsequent calls.
        if self._cbar_ax is not None:
            try:
                self._cbar_ax.remove()
            except Exception:
                pass
        self._cbar_ax = self.fig.add_axes([0.040, 0.20, 0.018, 0.46])
        import matplotlib.colors as mcolors
        import numpy as np
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
        self._cbar_ax.set_visible(vis)

    def draw_hanan_grid(self):
        xs, ys = self.fp.get_hanan_grid()
        for x in xs:
            self.ax.axvline(x=x, color='#cccccc', linestyle='--', linewidth=0.5, zorder=0)
        for y in ys:
            self.ax.axhline(y=y, color='#cccccc', linestyle='--', linewidth=0.5, zorder=0)

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

    def _draw_via_marker(self, bid, x, y, msz, alpha, zorder):
        """X inside a square at an H↔V segment junction."""
        sq, = self.ax.plot(x, y, 's', color='white',
                           markeredgecolor='black', markeredgewidth=1.2,
                           markersize=msz, alpha=alpha, zorder=zorder, clip_on=True)
        self._register(bid, sq, alpha=alpha, lw=msz)
        self._vias_conns_artists.append(sq)
        xm, = self.ax.plot(x, y, 'x', color='black',
                           markersize=msz * 0.65, markeredgewidth=1.5,
                           alpha=alpha, zorder=zorder + 1, clip_on=True)
        self._register(bid, xm, alpha=alpha, lw=msz * 0.65)
        self._vias_conns_artists.append(xm)
        if not self._vias_conns_visible:
            sq.set_visible(False)
            xm.set_visible(False)

    def _draw_busterm_conn(self, bid, x, y, col, msz, alpha, zorder):
        """Filled square at a segment endpoint that connects to a busterm."""
        sq, = self.ax.plot(x, y, 's', color=col,
                           markeredgecolor='black', markeredgewidth=1.0,
                           markersize=msz, alpha=alpha, zorder=zorder, clip_on=True)
        self._register(bid, sq, alpha=alpha, lw=msz)
        self._vias_conns_artists.append(sq)
        if not self._vias_conns_visible:
            sq.set_visible(False)

    def _draw_seg_connectors(self, bid, seg_idx, cs, sx, sy, col, msz, alpha,
                              zorder, along_offset=0.0, adj_perp=None):
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
        """
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
                    self._draw_via_marker(bid, cx, cy, msz, alpha, zorder)
                    continue
            if cs.horiz:
                cx, cy = conn.at_pos + along_offset, sy
            else:
                cx, cy = sx, conn.at_pos + along_offset
            if conn.kind == ic.SegConnKind.BUSTERM:
                self._draw_busterm_conn(bid, cx, cy, col, msz, alpha, zorder)
            else:
                self._draw_via_marker(bid, cx, cy, msz, alpha, zorder)

    def draw_buses(self):
        """Draw topology segments without NUTS track assignment."""
        self._busterm_artists = []
        self._vias_conns_artists = []
        layer_specs = {k: {'color': v} for k, v in _LAYER_COLOR.items()}
        for i, wrapper in enumerate(self.bundles):
            bid      = wrapper.original_bundle.id
            topo     = wrapper.candidates[wrapper.selected_topology_index]
            viz_lw   = 3.0 + math.log2(1 + wrapper.width) * 2.0
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
                                          msz, alpha, 12 + i, along_offset=offset)

            drv, rcvs = self._busterm_positions(topo, ct, offset=offset)
            self._draw_terminals(bid, drv, rcvs, viz_lw, alpha)

    def draw_nuts_tracks(self, nuts_result):
        """Draw segments at NUTS-assigned track positions with interval bands."""
        self._busterm_artists = []
        self._vias_conns_artists = []
        self._nuts_result = nuts_result   # saved for overlap panel in show()
        layer_specs = {k: {'color': v} for k, v in _LAYER_COLOR.items()}
        ts_map = {(ts.bundle_id, ts.seg_idx): ts for ts in nuts_result.segments}
        band_alpha = 0.04
        seg_alpha  = 0.90

        for i, wrapper in enumerate(self.bundles):
            bid    = wrapper.original_bundle.id
            topo   = wrapper.candidates[wrapper.selected_topology_index]
            viz_lw = 3.0 + math.log2(1 + wrapper.width) * 2.0
            msz     = max(4, viz_lw)
            ct      = ic.ConnTopology(); ct.build(topo, self.fp)
            cs_list = list(ct.segs())
            # adj_perp: seg_idx → NUTS track_position, used to snap vias to
            # the visual intersection of the two drawn lines.
            adj_perp = {}
            for j in range(len(topo.segments)):
                adj_ts = ts_map.get((bid, j))
                if adj_ts and adj_ts.placed:
                    adj_perp[j] = adj_ts.track_position

            for idx, seg in enumerate(topo.segments):
                ts   = ts_map.get((bid, idx))
                # Use ts.layer (NUTS-assigned, honours assigned_v_layer) when
                # available; fall back to seg.layer_hint for pre-NUTS drawing.
                effective_layer = ts.layer if ts else seg.layer_hint
                spec = layer_specs.get(effective_layer, {'color': 'green'})
                col  = spec['color']

                if ts and ts.placed:
                    half   = ts.width / 2.0
                    center = ts.track_position
                    is_h   = (seg.start.y == seg.end.y)

                    if is_h:
                        sx, ex = ts.span_lo, ts.span_hi
                        sy = ey = center
                        footprint = patches.Rectangle(
                            (sx, center - half), ex - sx, ts.width,
                            linewidth=0, facecolor=col,
                            alpha=band_alpha * 3, zorder=5)
                        self.ax.add_patch(footprint)
                        self._register(bid, footprint, alpha=band_alpha*3, is_band=True,
                                       layer=effective_layer)
                        for y_bound in (ts.interval_lo, ts.interval_hi):
                            bl, = self.ax.plot([min(sx,ex), max(sx,ex)], [y_bound, y_bound],
                                               color=col, linewidth=0.5, linestyle='--',
                                               alpha=0.3, zorder=4)
                            self._register(bid, bl, alpha=0.3, is_band=True,
                                           layer=effective_layer)
                    else:
                        sy, ey = ts.span_lo, ts.span_hi
                        sx = ex = center
                        footprint = patches.Rectangle(
                            (center - half, sy), ts.width, ey - sy,
                            linewidth=0, facecolor=col,
                            alpha=band_alpha * 3, zorder=5)
                        self.ax.add_patch(footprint)
                        self._register(bid, footprint, alpha=band_alpha*3, is_band=True,
                                       layer=effective_layer)
                        for x_bound in (ts.interval_lo, ts.interval_hi):
                            bl, = self.ax.plot([x_bound, x_bound], [min(sy,ey), max(sy,ey)],
                                               color=col, linewidth=0.5, linestyle='--',
                                               alpha=0.3, zorder=4)
                            self._register(bid, bl, alpha=0.3, is_band=True,
                                           layer=effective_layer)
                else:
                    sx, sy = seg.start.x, seg.start.y
                    ex, ey = seg.end.x,   seg.end.y

                line, = self.ax.plot([sx, ex], [sy, ey],
                                     color=col, linewidth=viz_lw,
                                     solid_capstyle='butt',
                                     alpha=seg_alpha, zorder=10 + i)
                self._register(bid, line, alpha=seg_alpha, lw=viz_lw,
                                layer=effective_layer)

                self._draw_seg_connectors(bid, idx, cs_list[idx], sx, sy, col,
                                          msz, seg_alpha, 12 + i,
                                          adj_perp=adj_perp)

            drv, rcvs = self._busterm_positions(topo, ct, ts_map=ts_map, bid=bid)
            self._draw_terminals(bid, drv, rcvs, viz_lw, seg_alpha)

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

    def draw_detailed_tracks(self, detailed_result, routing_grid_stack, layer_stack):
        """Draw Stage-9 bit-wire lines and routing-grid power/ground rail stripes.

        All artists are created hidden; the [Detailed] toggle button makes them
        visible and hides the abstract NUTS artists.
        """
        import interconnect as ic_mod

        # Build layer direction map from the LayerStack.
        h_ids = set(layer_stack.get_layer_ids_by_dir(ic_mod.LayerDir.HORIZONTAL))
        for lid in h_ids:
            self._layer_is_h[lid] = True
        for lid in layer_stack.get_layer_ids_by_dir(ic_mod.LayerDir.VERTICAL):
            self._layer_is_h[lid] = False

        # Layout bounding box for grid-rail extent.
        all_blocks = list(self.fp.get_all_blocks())
        if all_blocks:
            x_min = min(r.x1 for _, r in all_blocks)
            x_max = max(r.x2 for _, r in all_blocks)
            y_min = min(r.y1 for _, r in all_blocks)
            y_max = max(r.y2 for _, r in all_blocks)
        else:
            x_min, x_max, y_min, y_max = 0, 1000, 0, 1000

        # Rail stripe colours by slot type.
        _RAIL_COLOR = {'POWER': '#ffcccc', 'GROUND': '#cce0ff', 'CLOCK': '#fffacc'}

        # Draw grid rail stripes for every layer that has a pattern.
        for lid, is_h in self._layer_is_h.items():
            if not routing_grid_stack.has_layer(lid):
                continue
            grid    = routing_grid_stack.get_layer_grid(lid)
            # Use the global pattern (no override; representative for full-layout view).
            pattern = grid.effective_pattern_at(0.0, 0.0)
            up      = pattern.unit_pitch()
            if up <= 0:
                continue

            if is_h:
                # Horizontal layer: track_position is Y.  Draw stripes spanning full X.
                lo, hi = y_min - up, y_max + up
            else:
                # Vertical layer: track_position is X.  Draw stripes spanning full Y.
                lo, hi = x_min - up, x_max + up

            all_tracks = pattern.tracks_in_range(lo, hi)
            for centre, slot in all_tracks:
                col = _RAIL_COLOR.get(slot.type, None)
                if col is None:
                    continue   # SIGNAL slots are the transparent gaps
                half = slot.width / 2.0
                if is_h:
                    rect = patches.Rectangle(
                        (x_min, centre - half), x_max - x_min, slot.width,
                        linewidth=0, facecolor=col, alpha=0.15, zorder=4)
                else:
                    rect = patches.Rectangle(
                        (centre - half, y_min), slot.width, y_max - y_min,
                        linewidth=0, facecolor=col, alpha=0.15, zorder=4)
                self.ax.add_patch(rect)
                self._grid_rail_artists.append(rect)

        # Draw bit-wire NetSegments.
        # Build lookup for inter-segment junction stretching: each bit-wire's
        # span endpoint is extended to reach the track_position of the same bit
        # on the adjacent perpendicular segment (the per-bit equivalent of
        # NUTS span adjustment, which only reaches the abstract track centre).
        ns_map = {(ns.bundle_id, ns.seg_idx, ns.bit_index): ns
                  for ns in detailed_result.net_segments}

        layer_specs = {k: {'color': v} for k, v in _LAYER_COLOR.items()}
        for ns in detailed_result.net_segments:
            is_h  = self._layer_is_h.get(ns.layer, True)
            col   = layer_specs.get(ns.layer, {'color': 'green'})['color']
            lw    = max(0.6, ns.width * 0.6)

            draw_lo, draw_hi = ns.span_lo, ns.span_hi
            for d_si in (ns.seg_idx - 1, ns.seg_idx + 1):
                nb = ns_map.get((ns.bundle_id, d_si, ns.bit_index))
                if nb is None:
                    continue
                if self._layer_is_h.get(nb.layer, True) == is_h:
                    continue  # same direction — no perpendicular junction
                tp = nb.track_position   # on the same axis as draw_lo/draw_hi
                if abs(tp - draw_hi) <= abs(tp - draw_lo):
                    draw_hi = tp
                else:
                    draw_lo = tp

            if is_h:
                line, = self.ax.plot(
                    [draw_lo, draw_hi], [ns.track_position, ns.track_position],
                    color=col, linewidth=lw, solid_capstyle='butt',
                    alpha=0.9, zorder=15)
            else:
                line, = self.ax.plot(
                    [ns.track_position, ns.track_position], [draw_lo, draw_hi],
                    color=col, linewidth=lw, solid_capstyle='butt',
                    alpha=0.9, zorder=15)
            self._register_detailed(ns.bundle_id, line, alpha=0.9, lw=lw, layer=ns.layer)

        # Start hidden; toggle button reveals them.
        for entries in self._detailed_bundle_artists.values():
            for e in entries:
                e['artist'].set_visible(False)
        for a in self._grid_rail_artists:
            a.set_visible(False)

    def _toggle_detailed(self):
        self._detailed_mode = not self._detailed_mode
        active = self._detailed_mode

        # Show/hide the inactive set first (bulk operation without highlight logic).
        for entries in self._bundle_artists.values():
            for e in entries:
                e['artist'].set_visible(not active)
        for entries in self._detailed_bundle_artists.values():
            for e in entries:
                e['artist'].set_visible(active)
        for a in self._grid_rail_artists:
            a.set_visible(active)

        if self._btn_detailed is not None:
            lbl = '☑ Detailed' if active else '☐ Detailed'
            self._btn_detailed.label.set_text(lbl)
            self._btn_detailed.ax.set_facecolor('#ffe8cc' if active else '#e8f4e8')

        # Re-apply highlight/layer/bundle visibility to the now-active set.
        self._refresh_highlight()
        self.fig.canvas.draw_idle()

    def _draw_terminals(self, bundle_id, drv_pos, rcv_positions, viz_lw, alpha):
        """Draw driver (cyan square) and receiver (magenta circle) terminals.

        rcv_positions may be a single (x,y) or a list of (x,y).
        viz_lw may be the physical bus width (large for wide buses in NUTS view),
        so marker size is capped to stay visually reasonable.
        """
        msz = max(6, min(viz_lw, 16))
        new_artists = []
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
        if not self._bustermss_visible:
            for a in new_artists:
                a.set_visible(False)

    def _zoom_to_bundle(self, _=None):
        """Zoom axes to the bounding box of the selected bundle, or reset to full view."""
        from matplotlib.lines import Line2D as MplLine2D
        bid = self._highlighted
        if bid is None:
            self.ax.autoscale()
            self.fig.canvas.draw_idle()
            return
        xs, ys = [], []
        for e in self._bundle_artists.get(bid, []):
            a = e['artist']
            if e['is_band'] or not isinstance(a, MplLine2D):
                continue
            xs.extend(a.get_xdata(orig=False))
            ys.extend(a.get_ydata(orig=False))
        if not xs:
            return
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        pad_x = max((x1 - x0) * 0.2, 50)
        pad_y = max((y1 - y0) * 0.2, 50)
        self.ax.set_xlim(x0 - pad_x, x1 + pad_x)
        self.ax.set_ylim(y0 - pad_y, y1 + pad_y)
        self.fig.canvas.draw_idle()

    def show(self):
        self._bid_list = sorted(self._bundle_artists.keys())
        self._bundle_visible = {bid: True for bid in self._bid_list}

        # Build overlap entries sorted by layer → bid_a → bid_b.
        if self._nuts_result is not None:
            self._overlap_entries = sorted(
                self._nuts_result.overlap_details,
                key=lambda od: (od.layer, od.bid_a, od.bid_b)
            )

        # Collect all layer IDs actually present in the drawn artists.
        seen_layers: set = set()
        for artists_list in self._bundle_artists.values():
            for e in artists_list:
                if e.get('layer') is not None:
                    seen_layers.add(e['layer'])
        self._layer_ids = sorted(seen_layers)
        self._layer_visible = {lid: True for lid in self._layer_ids}

        self.ax.set_aspect('equal')
        self.ax.set_title(
            "BUDA: Non-Uniform Track Sharing (NUTS)  "
            "— click a bus-term or bus-seg to highlight",
            fontsize=13)

        from matplotlib.lines import Line2D
        legend_handles = [
            Line2D([0], [0], marker='s', color='w',
                   markerfacecolor='#00FFFF', markeredgecolor='k', label='Driver'),
            Line2D([0], [0], marker='o', color='w',
                   markerfacecolor='#FF00FF', markeredgecolor='k', label='Receivers'),
        ]
        self.ax.legend(handles=legend_handles, loc='upper right')
        self.ax.autoscale_view()
        self._home_xlim = self.ax.get_xlim()
        self._home_ylim = self.ax.get_ylim()

        # Right panel: x=0.83, width=0.15.  Plot right edge at 0.81.
        # Left panel: x=0.005, width=0.09.  Plot left edge at 0.10.
        # bottom=0.11 reserves room for x-tick labels above the button row.
        # top=0.97 reclaims the wasted margin above the title.
        self.fig.subplots_adjust(left=0.10, bottom=0.11, right=0.81, top=0.97)

        # ── Left panel: view toggles ──────────────────────────────────────
        LX, LW = 0.005, 0.088
        BTN_H_L = 0.038
        GAP_L   = 0.008

        ly = 0.97  # top-down, same as right panel

        def _lrect(h, gap=0):
            nonlocal ly
            ly -= gap + h
            return [LX, ly, LW, h]

        ax_all = self.fig.add_axes(_lrect(BTN_H_L))
        self._btn_all = Button(ax_all, '☑ All', color='#d0e8ff')
        self._btn_all.label.set_fontsize(8)
        self._btn_all.on_clicked(lambda _: self._toggle_all())

        ax_blknames = self.fig.add_axes(_lrect(BTN_H_L, GAP_L))
        self._btn_blknames = Button(ax_blknames, '☑ Blk Names', color='#e8f4e8')
        self._btn_blknames.label.set_fontsize(8)
        self._btn_blknames.on_clicked(lambda _: self._toggle_block_names())

        ax_bustermss = self.fig.add_axes(_lrect(BTN_H_L, GAP_L))
        self._btn_bustermss = Button(ax_bustermss, '☑ Busterms', color='#e8f4e8')
        self._btn_bustermss.label.set_fontsize(8)
        self._btn_bustermss.on_clicked(lambda _: self._toggle_bustermss())

        ax_vias_conns = self.fig.add_axes(_lrect(BTN_H_L, GAP_L))
        self._btn_vias_conns = Button(ax_vias_conns, '☑ Vias/Conns', color='#e8f4e8')
        self._btn_vias_conns.label.set_fontsize(8)
        self._btn_vias_conns.on_clicked(lambda _: self._toggle_vias_conns())

        ax_heatmap = self.fig.add_axes(_lrect(BTN_H_L, GAP_L))
        self._btn_heatmap = Button(ax_heatmap, '☑ Heatmap', color='#e8f4e8')
        self._btn_heatmap.label.set_fontsize(8)
        self._btn_heatmap.on_clicked(lambda _: self._toggle_heatmap())
        # Heatmap button is only meaningful when a congestion map was drawn.
        if not self._heatmap_artists and self._cbar_ax is None:
            self._btn_heatmap.ax.set_visible(False)

        ax_detailed = self.fig.add_axes(_lrect(BTN_H_L, GAP_L))
        self._btn_detailed = Button(ax_detailed, '☐ Detailed', color='#e8f4e8')
        self._btn_detailed.label.set_fontsize(8)
        self._btn_detailed.on_clicked(lambda _: self._toggle_detailed())
        # Hidden until draw_detailed_tracks() has been called.
        if not self._detailed_bundle_artists:
            self._btn_detailed.ax.set_visible(False)

        RX, RW   = 0.83, 0.15
        BTN_H    = 0.044
        SCROLL_H = 0.033
        GAP      = 0.012
        TOP_Y    = 0.95
        BOT_Y    = 0.09   # bottom margin

        # Fixed overhead consumed by buttons, scroll arrows, and gaps
        # (3 header btns) + (4 scroll arrows for bundles+overlaps) + (5 gaps)
        fixed_h  = 3 * BTN_H + 4 * SCROLL_H + 5 * GAP
        list_h   = max((TOP_Y - BOT_Y - fixed_h) / 3, 0.05)

        # Top-down allocation.  y tracks the top edge of the next widget.
        y = TOP_Y

        def _rect(h, gap=0):
            nonlocal y
            y -= gap + h
            return [RX, y, RW, h]

        # ── All Layers ──────────────────────────────────────────────────
        ax_all_layers = self.fig.add_axes(_rect(BTN_H))
        self._btn_all_layers = Button(ax_all_layers, '☑ All Layers', color='#e8e8e8')
        self._btn_all_layers.on_clicked(lambda _: self._on_layer_toggle_all())

        # ── Per-layer custom panel ───────────────────────────────────────
        self._ax_layers = self.fig.add_axes(_rect(list_h, GAP))
        self._ax_layers.set_facecolor('#f8f8f8')
        self._redraw_layer_list()

        # ── All Bundles ──────────────────────────────────────────────────
        ax_all_bundles = self.fig.add_axes(_rect(BTN_H, GAP))
        self._btn_all_bundles = Button(ax_all_bundles, '☑ All Bundles', color='#e8e8e8')
        self._btn_all_bundles.on_clicked(lambda _: self._on_bundle_toggle_all())

        # ── Bundle list: ▲ · list · ▼ ───────────────────────────────────
        ax_bscroll_up = self.fig.add_axes(_rect(SCROLL_H, GAP))
        btn_bscroll_up = Button(ax_bscroll_up, '▲', color='#f0f0f0')
        btn_bscroll_up.on_clicked(lambda _: self._scroll_bundles(-5))

        self._ax_bundles = self.fig.add_axes(_rect(list_h))
        self._ax_bundles.set_facecolor('#fafafa')
        self._redraw_bundle_list()

        ax_bscroll_dn = self.fig.add_axes(_rect(SCROLL_H))
        btn_bscroll_dn = Button(ax_bscroll_dn, '▼', color='#f0f0f0')
        btn_bscroll_dn.on_clicked(lambda _: self._scroll_bundles(+5))

        # ── All Overlaps ─────────────────────────────────────────────────
        n_ov = len(self._overlap_entries)
        ov_label = f'Overlaps ({n_ov})' if n_ov else 'No Overlaps'
        ax_all_overlaps = self.fig.add_axes(_rect(BTN_H, GAP))
        self._btn_all_overlaps = Button(ax_all_overlaps, ov_label, color='#e8e8e8')
        self._btn_all_overlaps.on_clicked(lambda _: self._on_overlap_toggle_all())

        # ── Overlap list: ▲ · list · ▼ ──────────────────────────────────
        ax_oscroll_up = self.fig.add_axes(_rect(SCROLL_H, GAP))
        btn_oscroll_up = Button(ax_oscroll_up, '▲', color='#f0f0f0')
        btn_oscroll_up.on_clicked(lambda _: self._scroll_overlaps(-5))

        self._ax_overlaps = self.fig.add_axes(_rect(list_h))
        self._ax_overlaps.set_facecolor('#fff8f8')
        self._redraw_overlap_list()

        ax_oscroll_dn = self.fig.add_axes(_rect(SCROLL_H))
        btn_oscroll_dn = Button(ax_oscroll_dn, '▼', color='#f0f0f0')
        btn_oscroll_dn.on_clicked(lambda _: self._scroll_overlaps(+5))

        self.fig.canvas.mpl_connect('scroll_event', self._on_scroll_event)

        # ── Bottom navigation buttons ─────────────────────────────────────
        # All buttons sit in x=[0.02, 0.79] (same footprint as main plot),
        # y=0.02, h=0.06 — safely below the bottom=0.11 plot boundary so that
        # x-tick labels have room between button tops (0.08) and the plot (0.11).
        _by, _bh = 0.02, 0.06
        ax_bprev = self.fig.add_axes([0.02, _by, 0.14, _bh])
        ax_solo  = self.fig.add_axes([0.17, _by, 0.12, _bh])
        ax_bnext = self.fig.add_axes([0.30, _by, 0.14, _bh])
        ax_zoom  = self.fig.add_axes([0.45, _by, 0.12, _bh])
        ax_topos = self.fig.add_axes([0.58, _by, 0.21, _bh])

        btn_bprev = Button(ax_bprev, '◀  Prev Bundle', color='#ddeeff')
        btn_bprev.on_clicked(lambda _: self._step_bundle(-1))

        self._btn_solo = Button(ax_solo, 'Solo OFF', color='#f0f0f0')
        self._btn_solo.on_clicked(lambda _: self._toggle_solo())

        btn_bnext = Button(ax_bnext, 'Next Bundle  ▶', color='#ddeeff')
        btn_bnext.on_clicked(lambda _: self._step_bundle(+1))

        self._btn_zoom = Button(ax_zoom, '[Z] Zoom to Sel', color='#f0f0f0')
        self._btn_zoom.on_clicked(self._zoom_to_bundle)

        btn_topos = Button(ax_topos, 'View Topologies  ↗', color='#fff0cc')
        btn_topos.on_clicked(lambda _: self._open_topo_explorer())

        plt.show()
