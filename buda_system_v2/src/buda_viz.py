import json
import math
import os
from datetime import datetime

import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.widgets import Button, CheckButtons

import interconnect as ic

_LAYER_COLOR = {3: '#FF8800', 4: '#007ACC', 5: '#CC0000', 6: '#00AA44', 7: '#8800CC'}
_LAYER_LABEL = {3: 'M3 V', 4: 'M4 H', 5: 'M5 V', 6: 'M6 H-trunk', 7: 'M7 V'}

def _toggle_fullscreen(fig):
    mgr = fig.canvas.manager
    if mgr:
        mgr.full_screen_toggle()

# Values beyond this magnitude are the INT_MIN/2 or INT_MAX/2 sentinels
# that ConnTopology uses for "unconstrained" slide ranges.
_UNCONSTRAINED = 1_000_000_000


class TopologyExplorer:
    """Cycle through topology candidates across one or more bundles.

    Navigation:
      ← / → (or ◀/▶ Topo buttons)  — prev / next topology within bundle
      [ / ] (or ◀/▶ Bus buttons)    — prev / next bundle
    """

    def __init__(self, fp, wrappers, sidecar_path=None):
        self.fp       = fp
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

        # Main axes — leave bottom margin for two button rows
        self.ax = self.fig.add_axes([0.05, 0.14, 0.90, 0.80])

        # ── Row 1: topo navigation (inner pair, blue) + bus navigation (outer, green) ──
        ax_tprev = self.fig.add_axes([0.22, 0.02, 0.20, 0.05])
        ax_tnext = self.fig.add_axes([0.58, 0.02, 0.20, 0.05])
        self._btn_tprev = Button(ax_tprev, '◀  Prev Topo', color='#ddeeff')
        self._btn_tnext = Button(ax_tnext, 'Next Topo  ▶', color='#ddeeff')
        self._btn_tprev.on_clicked(lambda _: self._step_topo(-1))
        self._btn_tnext.on_clicked(lambda _: self._step_topo(+1))

        ax_bprev = self.fig.add_axes([0.01, 0.02, 0.19, 0.05])
        ax_bnext = self.fig.add_axes([0.80, 0.02, 0.19, 0.05])
        self._btn_bprev = Button(ax_bprev, '◀  Prev Bus', color='#d9f5d9')
        self._btn_bnext = Button(ax_bnext, 'Next Bus  ▶', color='#d9f5d9')
        self._btn_bprev.on_clicked(lambda _: self._step_bundle(-1))
        self._btn_bnext.on_clicked(lambda _: self._step_bundle(+1))

        # Hide bus buttons when only one bundle is loaded.
        if len(self.wrappers) == 1:
            ax_bprev.set_visible(False)
            ax_bnext.set_visible(False)

        # ── Row 2: selection controls ──
        ax_select   = self.fig.add_axes([0.22, 0.08, 0.26, 0.05])
        ax_deselect = self.fig.add_axes([0.52, 0.08, 0.26, 0.05])
        self._btn_select   = Button(ax_select,   '★  Select Topo', color='#f0f0f0')
        self._btn_deselect = Button(ax_deselect, '✕  Deselect',    color='#f0f0f0')
        self._btn_select.on_clicked(lambda _: self._select_current())
        self._btn_deselect.on_clicked(lambda _: self._deselect_current())

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

    def _step_topo(self, delta):
        self.idx = (self.idx + delta) % len(self.topos)
        self._draw()

    def _step_bundle(self, delta):
        self.bidx = (self.bidx + delta) % len(self.wrappers)
        self.idx  = 0
        self._draw()

    def _on_key(self, event):
        if event.key in ('cmd+q', 'ctrl+q'):    plt.close('all'); return
        if event.key in ('cmd+f', 'ctrl+f'):    _toggle_fullscreen(self.fig); return
        if event.key in ('left',  'a'):         self._step_topo(-1)
        if event.key in ('right', 'd'):         self._step_topo(+1)
        if event.key in ('[', 'pageup'):        self._step_bundle(-1)
        if event.key in (']', 'pagedown'):      self._step_bundle(+1)
        if event.key == 's':                    self._select_current()
        if event.key == 'x':                    self._deselect_current()

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
    def __init__(self, floorplan, bundles, sidecar_path=None):
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
        self._chk_layers     = None
        self._ax_bundles     = None
        self._topo_explorer  = None
        self._pick_happened  = False
        self._in_bulk_layer_toggle = False  # suppress layer callbacks during bulk update

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
        for bid, entries in self._bundle_artists.items():
            for e in entries:
                if event.artist is e['artist']:
                    self._set_highlight(bid)
                    return

    def _on_click(self, event):
        # Route bundle list clicks before doing anything else.
        if self._ax_bundles is not None and event.inaxes == self._ax_bundles:
            self._on_bundle_list_click(event)
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
        if bundle_id == self._highlighted:
            bundle_id = None
        self._highlighted = bundle_id
        self._refresh_highlight()

    def _refresh_highlight(self):
        """Apply highlight + solo + layer-visibility + bundle-visibility to all artists."""
        from matplotlib.lines import Line2D as MplLine2D

        bundle_id = self._highlighted

        # Remove overlay boundary lines from the previous selection.
        for art in self._highlight_overlays:
            try: art.remove()
            except Exception: pass
        self._highlight_overlays.clear()

        for bid, entries in self._bundle_artists.items():
            bundle_on = self._bundle_visible.get(bid, True)
            selected  = (bundle_id is None) or (bid == bundle_id)

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

                if bundle_id is None:
                    a.set_alpha(e['alpha'])
                elif selected:
                    a.set_alpha(0.2 if e['is_band'] else 1.0)
                else:
                    a.set_alpha(0.0 if self._solo else (0.03 if e['is_band'] else 0.1))

        # Draw thin white boundary lines over each segment of the selected bundle.
        if bundle_id is not None:
            for e in self._bundle_artists.get(bundle_id, []):
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

        if bundle_id is not None:
            solo_hint = "  [Solo ON]" if self._solo else ""
            bname = self._bundle_name(bundle_id)
            nbits = self._bundle_bits(bundle_id)
            bits_str = f"  {nbits}-bit" if nbits > 0 else ""
            self.ax.set_title(
                f"BUDA — Bundle {bundle_id} ({bname}){bits_str} selected{solo_hint}  "
                f"(click again or click background to deselect)",
                fontsize=13)
        else:
            self.ax.set_title(
                "BUDA: Non-Uniform Track Sharing (NUTS)  "
                "— click a bus-term or bus-seg to highlight",
                fontsize=13)

        self._redraw_bundle_list()
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
    # Layer toggle
    # ------------------------------------------------------------------

    def _on_layer_toggle(self, label):
        if self._in_bulk_layer_toggle:
            return
        # Labels are of the form 'M3', 'M4', etc.
        try:
            layer = int(label[1:])
        except (ValueError, IndexError):
            layer = None
        if layer is not None:
            self._layer_visible[layer] = not self._layer_visible.get(layer, True)
        self._refresh_highlight()

    def _on_layer_toggle_all(self):
        """Toggle all layers on (if any are off) or off (if all are on)."""
        all_on    = all(self._layer_visible.values())
        new_state = not all_on

        # Bulk-update: suppress the per-layer checkbox callback.
        self._in_bulk_layer_toggle = True
        if self._chk_layers is not None:
            statuses = self._chk_layers.get_status()
            for i, lid in enumerate(self._layer_ids):
                if statuses[i] != new_state:
                    self._layer_visible[lid] = new_state
                    self._chk_layers.set_active(i)   # syncs visual only
        else:
            for lid in self._layer_visible:
                self._layer_visible[lid] = new_state
        self._in_bulk_layer_toggle = False

        if self._btn_all_layers is not None:
            self._btn_all_layers.label.set_text(
                '☑ All Layers' if new_state else '☐ All Layers')
            self._btn_all_layers.ax.set_facecolor(
                '#e8e8e8' if new_state else '#cccccc')
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

            # Build label: "{name} B{bid} ({nbits}b)", truncated to fit.
            name  = self._bundle_name(bid)
            nbits = self._bundle_bits(bid)
            bits_suffix = f" ({nbits}b)" if nbits > 0 else ""
            full  = f"{name} B{bid}{bits_suffix}"
            if len(full) > 20:
                full = name[:max(4, 20 - len(f" B{bid}{bits_suffix}"))] + '…' + f" B{bid}{bits_suffix}"

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
        if self._ax_bundles is None or event.inaxes != self._ax_bundles:
            return
        delta = -3 if event.button == 'up' else 3
        self._scroll_bundles(delta)

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

    def _open_topo_explorer(self):
        if self._highlighted is None:
            return
        wrapper = next((w for w in self.bundles
                        if w.original_bundle.id == self._highlighted), None)
        if wrapper is None or not wrapper.candidates:
            return
        self._topo_explorer = TopologyExplorer(self.fp, wrapper,
                                               sidecar_path=self._selections_path)
        self._topo_explorer.fig.show()

    def _on_key(self, event):
        if event.key in ('cmd+q', 'ctrl+q'): plt.close('all'); return
        if event.key in ('cmd+f', 'ctrl+f'): _toggle_fullscreen(self.fig); return
        if event.key in ('[', 'pageup'):   self._step_bundle(-1)
        if event.key in (']', 'pagedown'): self._step_bundle(+1)
        if event.key == 't':               self._open_topo_explorer()

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def draw_blocks(self):
        for name, rect in self.fp.get_all_blocks():
            w = rect.x2 - rect.x1
            h = rect.y2 - rect.y1
            self.ax.add_patch(patches.Rectangle(
                (rect.x1, rect.y1), w, h,
                linewidth=2, edgecolor='#444444', facecolor='#d9d9d9',
                alpha=0.6, zorder=1))
            cx, cy = (rect.x1 + rect.x2) / 2, (rect.y1 + rect.y2) / 2
            self.ax.text(cx, cy, name,
                         ha='center', va='center', fontsize=9, fontweight='bold',
                         color='#333333', zorder=2)

    def draw_congestion_map(self, cuts):
        """Shade each Hanan channel by utilisation ratio (green→red)."""
        xs, ys = self.fp.get_hanan_grid()
        cmap = plt.cm.RdYlGn_r

        for cut in cuts:
            ratio = (cut.current_usage / cut.capacity) if cut.capacity > 0 else 0.0
            if ratio == 0:
                continue
            color = cmap(min(ratio, 1.5) / 1.5)
            alpha = 0.12 + 0.22 * min(ratio, 1.0)

            if cut.p1.x == cut.p2.x:
                cx = cut.p1.x
                x_idx = [i for i, x in enumerate(xs) if x <= cx]
                if not x_idx: continue
                xi = x_idx[-1]
                x_lo = xs[xi]
                x_hi = xs[xi + 1] if xi + 1 < len(xs) else cx + 20
                self.ax.add_patch(patches.Rectangle(
                    (x_lo, ys[0]), x_hi - x_lo, ys[-1] - ys[0],
                    linewidth=0, facecolor=color, alpha=alpha, zorder=3))
                if ratio > 1.0:
                    self.ax.text((x_lo + x_hi) / 2, (ys[0] + ys[-1]) / 2,
                                 f"OVF\n{ratio:.0%}", fontsize=7, color='darkred',
                                 ha='center', va='center', zorder=4, fontweight='bold')
            else:
                cy = cut.p1.y
                y_idx = [i for i, y in enumerate(ys) if y <= cy]
                if not y_idx: continue
                yi = y_idx[-1]
                y_lo = ys[yi]
                y_hi = ys[yi + 1] if yi + 1 < len(ys) else cy + 20
                self.ax.add_patch(patches.Rectangle(
                    (xs[0], y_lo), xs[-1] - xs[0], y_hi - y_lo,
                    linewidth=0, facecolor=color, alpha=alpha, zorder=3))

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

    def draw_buses(self):
        """Draw topology segments without NUTS track assignment."""
        layer_specs = {k: {'color': v} for k, v in _LAYER_COLOR.items()}
        for i, wrapper in enumerate(self.bundles):
            bid      = wrapper.original_bundle.id
            topo     = wrapper.candidates[wrapper.selected_topology_index]
            viz_lw   = 3.0 + math.log2(1 + wrapper.width) * 2.0
            offset   = (i % 3 - 1) * 2.0
            alpha    = 0.8

            for idx, seg in enumerate(topo.segments):
                spec = layer_specs.get(seg.layer_hint, {'color': 'green'})
                sx = seg.start.x + offset;  sy = seg.start.y + offset
                ex = seg.end.x   + offset;  ey = seg.end.y   + offset

                line, = self.ax.plot([sx, ex], [sy, ey],
                                     color=spec['color'], linewidth=viz_lw,
                                     solid_capstyle='butt', alpha=alpha,
                                     zorder=10 + i)
                self._register(bid, line, alpha=alpha, lw=viz_lw,
                                layer=seg.layer_hint)

                if idx < len(topo.segments) - 1:
                    via_sz = max(2, min(viz_lw / 3, 6))
                    via, = self.ax.plot(ex, ey, 'o', color='black',
                                        markersize=via_sz,
                                        alpha=alpha, zorder=11 + i)
                    self._register(bid, via, alpha=alpha, lw=via_sz)

            ct = ic.ConnTopology(); ct.build(topo, self.fp)
            drv, rcvs = self._busterm_positions(topo, ct, offset=offset)
            self._draw_terminals(bid, drv, rcvs, viz_lw, alpha)

    def draw_nuts_tracks(self, nuts_result):
        """Draw segments at NUTS-assigned track positions with interval bands."""
        layer_specs = {k: {'color': v} for k, v in _LAYER_COLOR.items()}
        ts_map = {(ts.bundle_id, ts.seg_idx): ts for ts in nuts_result.segments}
        band_alpha = 0.04
        seg_alpha  = 0.90

        for i, wrapper in enumerate(self.bundles):
            bid    = wrapper.original_bundle.id
            topo   = wrapper.candidates[wrapper.selected_topology_index]
            viz_lw = (wrapper.width * 1.5) + 2.0

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

                if idx < len(topo.segments) - 1:
                    via_sz = max(2, min(viz_lw / 3, 6))
                    via, = self.ax.plot(ex, ey, 'o', color='black',
                                        markersize=via_sz,
                                        alpha=seg_alpha, zorder=11 + i)
                    self._register(bid, via, alpha=seg_alpha, lw=via_sz)

            ct = ic.ConnTopology(); ct.build(topo, self.fp)
            drv, rcvs = self._busterm_positions(topo, ct, ts_map=ts_map, bid=bid)
            self._draw_terminals(bid, drv, rcvs, viz_lw, seg_alpha)

    def _draw_terminals(self, bundle_id, drv_pos, rcv_positions, viz_lw, alpha):
        """Draw driver (cyan square) and receiver (magenta circle) terminals.

        rcv_positions may be a single (x,y) or a list of (x,y).
        viz_lw may be the physical bus width (large for wide buses in NUTS view),
        so marker size is capped to stay visually reasonable.
        """
        msz = max(6, min(viz_lw, 16))
        if drv_pos:
            drv, = self.ax.plot(drv_pos[0], drv_pos[1], 's',
                                color='#00FFFF', markeredgecolor='black',
                                markersize=msz, alpha=alpha, zorder=20)
            self._register(bundle_id, drv, alpha=alpha, lw=msz)
            lbl = self.ax.text(drv_pos[0], drv_pos[1], f"B{bundle_id}",
                               fontsize=8, color='black', fontweight='bold',
                               ha='center', va='center', zorder=21)
            lbl.set_alpha(alpha)
            self._register(bundle_id, lbl, alpha=alpha)

        if rcv_positions is None:
            return
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
            Line2D([0], [0], color=_LAYER_COLOR.get(lid, '#888888'), lw=4,
                   label=_LAYER_LABEL.get(lid, f'Layer {lid}'))
            for lid in self._layer_ids
        ]
        legend_handles += [
            Line2D([0], [0], marker='s', color='w',
                   markerfacecolor='#00FFFF', markeredgecolor='k', label='Driver'),
            Line2D([0], [0], marker='o', color='w',
                   markerfacecolor='#FF00FF', markeredgecolor='k', label='Receivers'),
        ]
        self.ax.legend(handles=legend_handles, loc='upper right')

        self.ax.autoscale_view()

        # Right panel starts at x=0.83; leave plot right edge at 0.81.
        self.fig.subplots_adjust(bottom=0.09, right=0.81)

        RX = 0.83   # right-panel left edge (figure fraction)
        RW = 0.15   # right-panel width

        # ── "All Layers" global toggle ──────────────────────────────────
        ax_all_layers = self.fig.add_axes([RX, 0.90, RW, 0.04])
        self._btn_all_layers = Button(ax_all_layers, '☑ All Layers', color='#e8e8e8')
        self._btn_all_layers.on_clicked(lambda _: self._on_layer_toggle_all())

        # ── Per-layer checkboxes — built dynamically from active layers ──
        n_layers = max(len(self._layer_ids), 1)
        chk_h = min(0.04 * n_layers + 0.04, 0.18)   # scale with count, cap at 0.18
        ax_layers = self.fig.add_axes([RX, 0.78, RW, chk_h])
        ax_layers.set_title('Layers', fontsize=9, pad=4)
        layer_labels  = [f'M{lid}' for lid in self._layer_ids]
        layer_colors  = [_LAYER_COLOR.get(lid, '#888888') for lid in self._layer_ids]
        self._chk_layers = CheckButtons(
            ax_layers,
            labels  = layer_labels,
            actives = [True] * len(self._layer_ids),
        )
        self._chk_layers.set_check_props({'facecolor': layer_colors})
        self._chk_layers.set_label_props({'color': layer_colors})
        for lbl in self._chk_layers.labels:
            lbl.set_fontsize(8)
        self._chk_layers.on_clicked(self._on_layer_toggle)

        # ── "All Bundles" global toggle ──────────────────────────────────
        ax_all_bundles = self.fig.add_axes([RX, 0.73, RW, 0.04])
        self._btn_all_bundles = Button(ax_all_bundles, '☑ All Bundles', color='#e8e8e8')
        self._btn_all_bundles.on_clicked(lambda _: self._on_bundle_toggle_all())

        # ── Bundle list: scroll ▲, list area, scroll ▼ ──────────────────
        ax_bscroll_up = self.fig.add_axes([RX, 0.68, RW, 0.04])
        btn_bscroll_up = Button(ax_bscroll_up, '▲', color='#f0f0f0')
        btn_bscroll_up.on_clicked(lambda _: self._scroll_bundles(-5))

        self._ax_bundles = self.fig.add_axes([RX, 0.14, RW, 0.53])
        self._ax_bundles.set_facecolor('#fafafa')
        self._redraw_bundle_list()

        ax_bscroll_dn = self.fig.add_axes([RX, 0.09, RW, 0.04])
        btn_bscroll_dn = Button(ax_bscroll_dn, '▼', color='#f0f0f0')
        btn_bscroll_dn.on_clicked(lambda _: self._scroll_bundles(+5))

        self.fig.canvas.mpl_connect('scroll_event', self._on_scroll_event)

        # ── Bottom navigation buttons ────────────────────────────────────
        ax_bprev = self.fig.add_axes([0.02, 0.02, 0.14, 0.05])
        ax_solo  = self.fig.add_axes([0.18, 0.02, 0.13, 0.05])
        ax_bnext = self.fig.add_axes([0.33, 0.02, 0.14, 0.05])
        ax_zoom  = self.fig.add_axes([0.49, 0.02, 0.13, 0.05])
        ax_topos = self.fig.add_axes([0.64, 0.02, 0.34, 0.05])

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
