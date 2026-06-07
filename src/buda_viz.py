import json
import math
import os
from datetime import datetime

import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.widgets import Button

import buda as ic

_LAYER_COLOR = {1: '#000075', 2: '#a9a9a9', 3: '#FF8800', 4: '#007ACC', 5: '#CC0000', 6: '#00AA44', 7: '#8800CC', 8: '#F032E6', 9: '#42D4F4', 10: '#9A6324'}
_LAYER_LABEL = {1: 'M1 V', 2: 'M2 H', 3: 'M3 V', 4: 'M4 H', 5: 'M5 V', 6: 'M6 H', 7: 'M7 V', 8: 'M8 H', 9: 'M9 V', 10: 'M10 H'}

stat_title = "Bundle-based Design Assistant (BUDA) with Non-Uniform Track Sharing (NUTS)"

# Bulent: no longer used. But keep as ref.
def _rects_disconnected(rects_raw):
    """Return True if any rect in the group has no touching/overlapping neighbour.

    Two rects are considered connected when their x-intervals and y-intervals
    both overlap or share an edge (i.e. a strictly-positive-area OR edge-only
    contact).  Uses union-find over all pairs.
    """
    n = len(rects_raw)
    if n <= 1:
        return False
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            a, b = rects_raw[i], rects_raw[j]
            if a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]:
                parent[find(i)] = find(j)

    return len({find(i) for i in range(n)}) > 1


def _draw_block(ax, name, bbox, fp, lw=1.0, edge='#888888', face='#e8e8e8',
                alpha=0.20, fontsize=8, zorder=1):
    """Draw one floorplan block.

    Single-rect blocks: one filled rectangle with a solid edge.
    Multi-rect blocks: each rect drawn individually with a solid edge, plus a
    dashed bounding box around the group to signal they are one logical block.

    Returns ([patches], label_text_artist).
    """
    rects_raw = fp.get_block_rects(name)
    added_patches = []

    if rects_raw:
        for (rx1, ry1, rx2, ry2) in rects_raw:
            p = patches.Rectangle(
                (rx1, ry1), rx2 - rx1, ry2 - ry1,
                linewidth=lw, edgecolor=edge, facecolor=face,
                alpha=alpha, zorder=zorder)
            ax.add_patch(p)
            added_patches.append(p)

        # Dashed bounding box for multi-rect blocks
        p_bb = patches.Rectangle(
            (bbox.x1, bbox.y1), bbox.x2 - bbox.x1, bbox.y2 - bbox.y1,
            linewidth=max(0.8, lw * 0.6), edgecolor=edge, facecolor='none',
            linestyle='--', alpha=alpha * 0.8, zorder=zorder + 0.5)
        ax.add_patch(p_bb)
        added_patches.append(p_bb)
    else:
        p = patches.Rectangle(
            (bbox.x1, bbox.y1), bbox.x2 - bbox.x1, bbox.y2 - bbox.y1,
            linewidth=lw, edgecolor=edge, facecolor=face,
            alpha=alpha, zorder=zorder)
        ax.add_patch(p)
        added_patches.append(p)

    if not name:
        return added_patches, None

    cx = (bbox.x1 + bbox.x2) / 2
    cy = (bbox.y1 + bbox.y2) / 2
    txt = ax.text(cx, cy, name, ha='center', va='center',
                  fontsize=fontsize, fontweight='bold', color='#444444',
                  alpha=min(1.0, alpha * 4.0), # label slightly brighter than block
                  zorder=zorder + 1, clip_on=True)
    return added_patches, txt


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

def _disable_default_keymaps():
    """Remove default matplotlib keybindings that interfere with BUDA shortcuts."""
    keys_to_clear = (
        'keymap.save', 'keymap.fullscreen', 'keymap.home', 'keymap.back',
        'keymap.forward', 'keymap.xscale', 'keymap.yscale', 'keymap.grid'
    )
    for key in keys_to_clear:
        try:
            vals = plt.rcParams.get(key, [])
            # We want to remove single-letter shortcuts like 's', 'f', 'h', 'p', 'n', 'k', etc.
            # but keep multi-key ones like 'ctrl+s' if possible.
            to_remove = [v for v in vals if len(v) == 1]
            for v in to_remove:
                vals.remove(v)
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
      s (or Select button)           — toggle selection (pin/unpin)
      cmd-1                          — raise the main BUDA viz window
    """

    def __init__(self, fp, wrappers, sidecar_path=None, main_fig=None,
                 rerun_fn=None, refresh_fn=None, layer_stack=None):
        self.fp          = fp
        self.layer_stack = layer_stack
        self._main_fig   = main_fig    # back-reference to main viz figure for cmd-1
        self._rerun_fn   = rerun_fn    # () -> NUTSResult | None
        self._refresh_fn = refresh_fn  # (NUTSResult) -> None
        
        self._blocks_visible = True
        self._block_names_visible = True
        self._block_patch_artists = []
        self._block_name_artists = []

        # Accept a single wrapper or a list for backward compatibility.
        self.wrappers = wrappers if isinstance(wrappers, list) else [wrappers]
        self.bidx     = 0   # current bundle index
        self.idx      = 0   # current topology index within bundle
        self.sidx     = -1  # current selected segment index within current topology

        # bundle_hint -> {topo_type, topo_wl, topo_index_hint, note, selected_at, seg_layers}
        self._selections    = {}
        self._sidecar_path  = sidecar_path
        _disable_default_keymaps()
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

        # Main axes — two button rows below; leave y=0.14 for buttons and x-tick labels
        self.ax = self.fig.add_axes([0.05, 0.14, 0.90, 0.81])

        # ── Navigation row (y=0.015) ─────────────────────────────────────────
        _BY1, _BH1  = 0.015, 0.038   # row y and height
        _BY2, _BH2  = 0.065, 0.038   # tuning row y and height (above nav)
        _MARGIN    = 0.010
        _GAP       = 0.008

        _nav_specs = [
            ('◀  Bus',     '#d9f5d9', 1.0),
            ('◀  Topo',    '#ddeeff', 1.0),
            ('★  Select',  '#f0f0f0', 1.0),
            ('✕  Desel',   '#f0f0f0', 0.85),
        ]
        if rerun_fn is not None:
            _nav_specs.append(('▶  Re-run', '#ffe0b0', 1.2))
        _nav_specs += [
            ('Topo  ▶',   '#ddeeff', 1.0),
            ('Bus  ▶',    '#d9f5d9', 1.0),
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
        
        wrapper = self.wrappers[self.bidx]
        sel = {
            'bundle_id':       wrapper.original_bundle.id,
            'topo_type':       topo.type,
            'topo_wl':         topo.estimated_wirelength,
            'topo_index_hint': self.idx,
            'note':            '',
            'selected_at':     datetime.now().isoformat(timespec='seconds'),
        }
        
        pinned = list(wrapper.pinned_seg_layers)
        if len(pinned) == len(topo.segments):
            if any(lid != -1 for lid in pinned):
                sel['seg_layers'] = pinned
        
        # Update live object
        if wrapper.selected_topology_index != self.idx:
            wrapper.seg_layers = [] # Clear stale results for different topology
        
        wrapper.selected_topology_index = self.idx
        wrapper.topology_pinned = True
        
        self._selections[hint] = sel
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
            
            # Update live object
            wrapper = self.wrappers[self.bidx]
            wrapper.topology_pinned = False
            wrapper.pinned_seg_layers = []

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
                if 'seg_layers' in entry:
                    self._selections[entry['bundle_hint']]['seg_layers'] = entry['seg_layers']

            print(f"Loaded {len(self._selections)} selection(s) from {self._sidecar_path}")
        except Exception as e:
            print(f"Warning: could not load sidecar {self._sidecar_path}: {e}")

    def _save_sidecar(self):
        if not self._sidecar_path:
            return
        entries = []
        for hint, sel in sorted(self._selections.items()):
            # Find the wrapper to check for actual pinned layers if they aren't in sel yet.
            # (Though _select_current usually populates sel from wrapper).
            entries.append({'bundle_hint': hint, **sel})

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
        self.sidx = -1
        self._reset_rerun_btn()
        self._draw()

    def _step_bundle(self, delta):
        self.bidx = (self.bidx + delta) % len(self.wrappers)
        self.idx  = 0
        self.sidx = -1
        self._reset_rerun_btn()
        self._draw()

    def _toggle_blocks(self):
        self._blocks_visible = not self._blocks_visible
        for p in self._block_patch_artists:
            p.set_visible(self._blocks_visible)
        for t in self._block_name_artists:
            t.set_visible(self._blocks_visible)
        self.fig.canvas.draw_idle()

    def _on_key(self, event):
        if event.key in ('cmd+q', 'ctrl+q'):    plt.close('all'); return
        if event.key in ('f', 'cmd+f', 'ctrl+f'): _toggle_fullscreen(self.fig); return
        if event.key in ('cmd+1', 'ctrl+1'):
            if self._main_fig is not None: _raise_window(self._main_fig)
            return
        if event.key in ('left',  'a'):         self._step_topo(-1)
        if event.key in ('right', 'd'):         self._step_topo(+1)
        if event.key in ('n', 'cmd+n', 'ctrl+n'):    self._step_topo(+1)
        if event.key in ('p', 'cmd+p', 'ctrl+p'):    self._step_topo(-1)
        if event.key in ('[', 'pageup'):        self._step_bundle(-1)
        if event.key in (']', 'pagedown'):      self._step_bundle(+1)
        if event.key in ('up', 'k'):            self._step_segment(-1)
        if event.key in ('down', 'j'):          self._step_segment(+1)
        if event.key in ('+', '=', 'u'):        self._cycle_layer(+1)
        if event.key in ('-', '_', 'd'):        self._cycle_layer(-1)
        if event.key == 'b':                    self._toggle_blocks()
        if event.key == 's':
            if self._current_is_selected(): self._deselect_current()
            else:                           self._select_current()
        if event.key == 'x':                    self._deselect_current()
        if event.key == 'r':                    self._rerun_and_refresh()

    def _step_segment(self, delta):
        topo = self.topos[self.idx]
        n = len(topo.segments)
        if n == 0: return
        self.sidx = (self.sidx + delta) % n
        self._draw()

    def _cycle_layer(self, delta):
        if self.sidx == -1 or self.layer_stack is None:
            return
        wrapper = self.wrappers[self.bidx]
        topo = wrapper.candidates[self.idx]
        seg = topo.segments[self.sidx]
        is_h = (seg.start.y == seg.end.y)

        # Get compatible layers based on segment orientation.
        dir = ic.LayerDir.HORIZONTAL if is_h else ic.LayerDir.VERTICAL
        lids = list(self.layer_stack.get_layer_ids_by_dir(dir))
        if not lids: return

        # Resolve current layer ID using same precedence as _draw.
        sel = self._find_selection()
        is_current_selection = self._current_is_selected()
        is_planner_active = (self.idx == wrapper.selected_topology_index)
        
        curr = -1
        if is_current_selection and sel and 'seg_layers' in sel:
            pinned = sel['seg_layers']
            if len(pinned) == len(topo.segments):
                curr = pinned[self.sidx]
        
        if curr == -1 and is_planner_active:
            if len(wrapper.seg_layers) == len(topo.segments):
                curr = wrapper.seg_layers[self.sidx]
        
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
        pinned_list = list(wrapper.pinned_seg_layers)
        if len(pinned_list) != len(topo.segments):
            # Start from current actual layers.
            pinned_list = []
            for i, s in enumerate(topo.segments):
                l = -1
                if is_planner_active and len(wrapper.seg_layers) == len(topo.segments):
                    l = wrapper.seg_layers[i]
                else:
                    l = s.layer_hint
                pinned_list.append(l)

        pinned_list[self.sidx] = new_lid
        wrapper.pinned_seg_layers = pinned_list

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
            # We don't automatically raise the main window here anymore, 
            # as it snatches focus from the TopologyExplorer.
            # User can use 'cmd+1' (on Mac) or just click the window.

    def _draw(self):
        ax = self.ax
        ax.clear()

        topo  = self.topos[self.idx]
        n     = len(self.topos)
        bid   = self.wrapper.original_bundle.id
        wl    = topo.estimated_wirelength
        ct      = self._build_conn_topo(topo)
        cs_list = list(ct.segs())
        viz_lw  = min(3.0 + math.log2(1 + self.wrapper.width) * 1.5, 14.0)

        is_sel = self._current_is_selected()
        has_any_sel = self._find_selection() is not None

        # ── Enable/Disable Tuning Row ──
        for b in self._bax2:
            b.set_visible(is_sel)

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

        # Topology segments — width proportional to bundle width
        actual_lids = []
        sel = self._find_selection()
        is_current_selection = self._current_is_selected()
        is_planner_active = (self.idx == self.wrapper.selected_topology_index)

        # ── Pre-compute display geometry for all segments ──────────────────
        # Minimum pull-arrow length in data units (prevents invisible arrows on
        # tiny intervals and provides a direction indicator for one-side-constrained
        # segments where there is no finite interval width to derive from).
        _MIN_ARROW = 20

        # Pass A: centered perp position + pull-arrow length per segment.
        #
        # cs.net_pull is computed by ConnTopology.compute_net_pull() in C++:
        #   > 0  more connected-stub anchors lie above perp_pos → slide up/right
        #   < 0  more anchors lie below perp_pos                → slide down/left
        #   = 0  balanced or no stub connections                → no preferred direction
        #
        # Display at interval centre when both bounds are finite; at perp_pos otherwise.
        _draw_perp = []
        _pull_len  = []
        for cs in cs_list:
            lo, hi  = cs.perp_lo, cs.perp_hi
            lo_ok   = abs(lo) < _UNCONSTRAINED
            hi_ok   = abs(hi) < _UNCONSTRAINED
            dp      = (lo + hi) / 2.0 if (lo_ok and hi_ok) else float(cs.perp_pos)

            if cs.net_pull != 0:
                interval_w = (hi - lo) if (lo_ok and hi_ok) else 0.0
                plen = max(interval_w / 4.0, float(_MIN_ARROW)) * (1.0 if cs.net_pull > 0 else -1.0)
            else:
                plen = 0.0

            _draw_perp.append(dp)
            _pull_len.append(plen)

        # Pass B: snap along endpoints to connected segments' display perp.
        # Each SEG connection "at_pos" in segment i's along direction should
        # equal one of cs.along_lo/hi; replace that endpoint with the
        # connected segment's centered display position so segments visually meet.
        _draw_lo = [float(cs.along_lo) for cs in cs_list]
        _draw_hi = [float(cs.along_hi) for cs in cs_list]
        for i, cs in enumerate(cs_list):
            for conn in cs.conns:
                if conn.kind != ic.SegConnKind.SEG:
                    continue
                adj = _draw_perp[conn.seg_idx]
                if abs(conn.at_pos - cs.along_lo) <= 1:
                    _draw_lo[i] = adj
                elif abs(conn.at_pos - cs.along_hi) <= 1:
                    _draw_hi[i] = adj
        # ──────────────────────────────────────────────────────────────────

        for i, seg in enumerate(topo.segments):
            lid = -1
            # 1. Pinned layers (from sidecar/active tuning)
            if is_current_selection and sel and 'seg_layers' in sel:
                pinned = sel['seg_layers']
                if len(pinned) == len(topo.segments):
                    lid = pinned[i]

            # 2. Planned layers (from CongestionPlanner result)
            if lid == -1 and is_planner_active:
                if len(self.wrapper.seg_layers) == len(topo.segments):
                    lid = self.wrapper.seg_layers[i]

            # 3. Default from topology generator
            if lid == -1:
                lid = seg.layer_hint
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
            if self.sidx != -1:
                if i == self.sidx:
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
        
        nterms = self.wrapper.original_bundle.num_terminals
        title_main = (
            f"{bus_label}B{bid} ({nterms} terms/{len(topo.segments)} segs) · topo {self.idx + 1}/{n} "
            f"· {topo.type} · WL={wl} · [{layer_summary}]{sel_badge}"
        )
        ax.set_title(title_main, fontsize=12, pad=10, color='#886600' if is_sel else 'black')

        if self.fig.canvas.manager:
            bid_  = self.wrapper.original_bundle.id
            names_ = self.wrapper.original_bundle.get_net_names()
            net0  = names_[0] if names_ else f"B{bid_}"
            self.fig.canvas.manager.set_window_title(f"{net0} (Bundle {bid_})")

        # Identify blocks that this topology must connect (endpoints + passthru)
        highlight_blocks = set(topo.connected_block_names)

        # Floorplan blocks
        self._block_patch_artists = []
        self._block_name_artists = []
        for name, rect in self.fp.get_all_blocks():
            if name in highlight_blocks:
                # Highlighted style for busterm blocks
                ps, txt = _draw_block(ax, name, rect, self.fp,
                                      lw=1.8, edge='#333333', face='#d0f0d0',
                                      alpha=0.50, fontsize=8, zorder=1.5)
            else:
                # Dimmed style for the rest
                ps, txt = _draw_block(ax, name, rect, self.fp,
                                      lw=1.0, alpha=0.18, fontsize=7)
            self._block_patch_artists.extend(ps)
            if txt is not None:
                self._block_name_artists.append(txt)
        
        for p in self._block_patch_artists: p.set_visible(self._blocks_visible)
        for t in self._block_name_artists:  t.set_visible(self._blocks_visible)

        # Hanan grid
        xs, ys = self.fp.get_hanan_grid()
        for x in xs:
            ax.axvline(x=x, color='#dddddd', linestyle=':', linewidth=0.4, zorder=0)
        for y in ys:
            ax.axhline(y=y, color='#dddddd', linestyle=':', linewidth=0.4, zorder=0)


        # Slide-range bands (drawn before segments so segments sit on top)
        self._draw_slide_spans(topo, ct)

        # Busterm diamonds (on top of segments and junction dots)
        self._draw_busterm_markers(topo, ct, viz_lw)

        # Legend
        from matplotlib.lines import Line2D
        used_layers = sorted(set(actual_lids))
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
        handles.append(Line2D([0], [0], color='#888888', lw=1.5, marker='>',
                               markersize=7, label='pull direction'))
        ax.legend(handles=handles, loc='upper right', fontsize=9)

        ax.set_aspect('equal')
        ax.autoscale_view()
        self.fig.canvas.draw_idle()

    def show(self):
        plt.show()


class BudaVisualizer:
    def __init__(self, floorplan, bundles, sidecar_path=None, rerun_layer_fn=None,
                 rerun_fn=None, routing_grid=None, layer_stack=None,
                 net_endpoints=None, ipc_session=None):
        self.fp           = floorplan
        self.bundles      = bundles
        self._ipc_session = ipc_session
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
        self._detailed_result = None # stored in draw_detailed_tracks for bit stats
        self._topo_explorer  = None
        self._pick_happened  = False
        self._cbar_ax        = None   # colorbar axes for congestion heatmap
        self._heatmap_artists = []    # patches + texts created by draw_congestion_map
        self._block_patch_artists = [] # block rectangle patches
        self._block_name_artists = [] # text artists created by draw_blocks
        self._heatmap_visible    = True
        self._keepouts_visible   = True
        self._block_names_visible = True
        self._blocks_visible     = True
        self._bustermss_visible  = True
        self._vias_conns_visible = True
        self._all_vis            = True
        self._home_xlim          = None
        self._home_ylim          = None
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

        # Layout constants for the left panel (view toggles + heatmap)
        self._LX, self._LW = 0.005, 0.065
        # Start with a conservative estimate to leave room for ~7 buttons (7 * 0.046 = 0.322)
        # 0.97 - 0.322 = 0.648.
        self._ly_post_buttons = 0.62 

        # Detailed NUTS (Stage 9) visualisation state.
        self._detailed_mode          = False
        self._tracks_visible         = True
        self._detailed_bundle_artists = {}   # bid -> [{artist,alpha,lw,is_band,layer}]
        self._grid_rail_artists      = []    # POWER/GND/CLK stripe patches (not per-bundle)
        self._layer_is_h             = {}    # layer_id -> bool (populated by draw_detailed_tracks)

        # IPC: bundle_id -> set of instance names (driver + receivers, 'top' excluded)
        self._bundle_insts: dict = {}
        if net_endpoints:
            for w in bundles:
                bid   = w.original_bundle.id
                insts = set()
                for net_name in w.original_bundle.get_net_names():
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
        self._ipc_send_highlight(bundle_id)

    def _ipc_send_highlight(self, bundle_id):
        if self._ipc is None:
            return
        if bundle_id is None:
            self._ipc.send({'type': 'clear'})
            return
        w = next((w for w in self.bundles if w.original_bundle.id == bundle_id), None)
        net_names  = list(w.original_bundle.get_net_names()) if w else []
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

        # Apply layer visibility to non-bundle detailed artists (grid rails).
        # These are only shown when _detailed_mode is active.
        if self._detailed_mode:
            for e in self._grid_rail_artists:
                a = e['artist']
                layer_on = self._layer_visible.get(e['layer'], True)
                tracks_on = self._tracks_visible
                # Use stored base alpha (0.15 for rails, 0.10 for signal)
                base_alpha = e.get('alpha', 0.15)
                a.set_alpha(base_alpha if (layer_on and tracks_on) else 0.0)

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
            # For ref: old title had f"BUDA — Overlap: {msg}  (click row to cycle, All Overlaps to clear)"
            self.ax.set_title(f"BUDA — Overlap: {msg}", fontsize=13)
        elif bundle_id is not None:
            solo_hint = "  [Solo ON]" if self._solo else ""
            bname = self._bundle_name(bundle_id)
            nbits = self._bundle_bits(bundle_id)

            wrapper = next((w for w in self.bundles if w.original_bundle.id == bundle_id), None)

            # for ref only. Old code:
            # Get busterm count from the active topology.
            # nterms = 0
            # if wrapper and wrapper.candidates and wrapper.selected_topology_index >= 0:
            #    topo = wrapper.candidates[wrapper.selected_topology_index]
            #    nterms = _get_nterms(topo)

            # Get busterm count from the bundle metadata.
            nterms = wrapper.original_bundle.num_terminals

            bits_str = f" ({nbits} bits/{nterms} bterms)" if nbits > 0 else ""
            # For ref, old title had: f"(click again or click background to deselect)"
            self.ax.set_title(
                f"BUDA — B{bundle_id} {bname}{bits_str} selected{solo_hint}",
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
        self._refresh_highlight()
        self._ipc_send_highlight(self._highlighted)

    def _toggle_heatmap(self):
        self._heatmap_visible = not self._heatmap_visible
        vis = self._heatmap_visible
        for a in self._heatmap_artists:
            a.set_visible(vis)
        if self._cbar_ax is not None:
            self._cbar_ax.set_visible(vis)
        if self._btn_heatmap is not None:
            self._btn_heatmap.label.set_text('☑ Heatmap' if vis else '☐ Heatmap')
        self.fig.canvas.draw_idle()

    def _toggle_keepouts(self):
        self._keepouts_visible = not self._keepouts_visible
        vis = self._keepouts_visible
        for a in self._keepout_artists:
            a.set_visible(vis)
        if self._btn_keepouts is not None:
            self._btn_keepouts.label.set_text('☑ Keepouts' if vis else '☐ Keepouts')
        self.fig.canvas.draw_idle()

    def _reset_view(self):
        """Force all bundles and layers to visible, and clear selection."""
        self._highlighted      = None
        self._highlighted_set  = set()
        self._selected_overlap = None
        self._overlap_state    = 0
        self._solo             = False
        if self._btn_solo is not None:
             self._btn_solo.label.set_text("Solo OFF")
             self._btn_solo.ax.set_facecolor('#f0f0f0')

        # Reset design element toggles to True.
        self._all_vis             = True
        self._heatmap_visible     = True
        self._keepouts_visible    = True
        self._blocks_visible      = True
        self._block_names_visible = True
        self._bustermss_visible   = True
        self._vias_conns_visible  = True
        self._tracks_visible      = True

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
        for a in self._busterm_artists: a.set_visible(True)
        for a in self._vias_conns_artists: a.set_visible(True)
        for e in self._grid_rail_artists:
             e['artist'].set_visible(self._detailed_mode)

        # Update button labels
        if self._btn_heatmap is not None: self._btn_heatmap.label.set_text('☑ Heatmap')
        if self._btn_keepouts is not None: self._btn_keepouts.label.set_text('☑ Keepouts')
        if self._btn_blocks is not None: self._btn_blocks.label.set_text('☑ Blocks')
        if self._btn_blknames is not None: self._btn_blknames.label.set_text('☑ Blk Names')
        if self._btn_bustermss is not None: self._btn_bustermss.label.set_text('☑ Busterms')
        if self._btn_vias_conns is not None: self._btn_vias_conns.label.set_text('☑ Vias/Conns')
        if self._btn_tracks is not None:
             self._btn_tracks.label.set_text('☑ Tracks')
             self._btn_tracks.ax.set_facecolor('#ffe8cc')

        self._redraw_layer_list()
        self._redraw_bundle_list()
        self._refresh_highlight()
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

        # Blocks
        self._blocks_visible = vis
        for p in self._block_patch_artists:
            p.set_visible(vis)
        if self._btn_blocks is not None:
            self._btn_blocks.label.set_text('☑ Blocks' if vis else '☐ Blocks')

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

        # Tracks
        self._tracks_visible = vis
        if self._btn_tracks is not None:
            lbl = '☑ Tracks' if vis else '☐ Tracks'
            self._btn_tracks.label.set_text(lbl)
            self._btn_tracks.ax.set_facecolor('#ffe8cc' if vis else '#e8f4e8')
            for e in self._grid_rail_artists:
                e['artist'].set_visible(self._detailed_mode and vis)

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

    def _toggle_blocks(self):
        self._blocks_visible = not self._blocks_visible
        vis = self._blocks_visible
        for p in self._block_patch_artists:
            p.set_visible(vis)
        label = '☑ Blocks' if vis else '☐ Blocks'
        if self._btn_blocks is not None:
            self._btn_blocks.label.set_text(label)
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
        self._grid_rail_artists.clear()

        # Redraw segments at new track positions.
        self.draw_nuts_tracks(nuts_result)
        if detailed_result is not None and self.routing_grid and self.layer_stack:
            self.draw_detailed_tracks(detailed_result, self.routing_grid, self.layer_stack)

        # Refresh layer list in case new layers were introduced.
        self._update_layer_ids()
        self._redraw_layer_list()

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

        # Update the header button label with global stats if available.
        if self._btn_all_bundles is not None:
            all_on = all(self._bundle_visible.values())
            all_lbl = '☑ All Bundles' if all_on else '☐ All Bundles'
            if self._detailed_result:
                n_total = sum(len(w.original_bundle.get_net_names()) * len(w.candidates[w.selected_topology_index].segments)
                              for w in self.bundles if w.candidates and 0 <= w.selected_topology_index < len(w.candidates))
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

            # Build label: "B{bid} {name} (N bits/M bterms)", truncated to fit.
            name  = self._bundle_name(bid)
            nbits = self._bundle_bits(bid)

            # Get busterm count from metadata
            w = next(w for w in self.bundles if w.original_bundle.id == bid)
            nterms = w.original_bundle.num_terminals

            #bits_suffix = f" ({nbits} bits/{nterms} bterms)" if nbits > 0 else ""
            bits_suffix = f" ({nbits} bits)" if nbits > 0 else ""
            prefix = f"B{bid} "
            max_name = max(4, 20 - len(prefix) - len(bits_suffix))
            name_part = name if len(name) <= max_name else name[:max_name - 1] + "…"
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
            
            # Bit stats for this bundle: [unplaced/total]
            stats_part = ""
            stats_color = '#111111'
            if self._detailed_result:
                # DetailedNUTSResult doesn't easily provide per-bundle unplaced count.
                # Let's count how many net_segments we have vs how many we expect.
                w = next(w for w in self.bundles if w.original_bundle.id == bid)
                if not w.candidates or w.selected_topology_index < 0 or w.selected_topology_index >= len(w.candidates):
                    stats_part = ' [no topo]'; stats_color = '#888888'
                    continue
                n_expected = len(w.original_bundle.get_net_names()) * len(w.candidates[w.selected_topology_index].segments)
                n_placed   = sum(1 for ns in self._detailed_result.net_segments if ns.bundle_id == bid)
                n_unp = n_expected - n_placed
                stats_part = f" [{n_unp}/{n_expected}]"
                stats_color = '#CC0000' if n_unp > 0 else '#008800'

            ax.text(0.20, y, f"{vis_char} {full}",
                    transform=ax.transAxes,
                    fontsize=7, color=txt_color,
                    va='center', clip_on=True)
            if stats_part:
                 ax.text(0.85, y, stats_part,
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
            # Automatically select the first bundle that has candidates.
            for w in self.bundles:
                if w.candidates:
                    self._highlighted = w.original_bundle.id
                    self._refresh_highlight()
                    break

        if self._highlighted is None:
            return
        wrapper = next((w for w in self.bundles
                        if w.original_bundle.id == self._highlighted), None)
        if wrapper is None or not wrapper.candidates:
            return

        # Singleton Pattern: check if a TopologyExplorer window is already open.
        if self._topo_explorer is not None and plt.fignum_exists(self._topo_explorer.fig.number):
            # If it's for the SAME bundle, just raise it.
            if self._topo_explorer.wrappers[0].original_bundle.id == self._highlighted:
                _raise_window(self._topo_explorer.fig)
                return
            else:
                # Different bundle? Close the old one to avoid confusion/clutter.
                plt.close(self._topo_explorer.fig)

        refresh_fn = self._redraw_nuts_tracks if self._rerun_fn is not None else None
        self._topo_explorer = TopologyExplorer(
            self.fp, wrapper,
            sidecar_path=self._selections_path,
            main_fig=self.fig,
            rerun_fn=self._rerun_fn,
            refresh_fn=refresh_fn,
            layer_stack=self.layer_stack)
        self._topo_explorer.fig.show()

    def _on_key(self, event):
        if event.key in ('cmd+q', 'ctrl+q'): plt.close('all'); return
        if event.key in ('f', 'cmd+f', 'ctrl+f'): _toggle_fullscreen(self.fig); return
        if event.key in ('cmd+z', 'ctrl+z'): self._zoom_to_bundle(); return
        if event.key == 'a':
            if self._detailed_mode: self._set_highlight(None)
            else:                   self._reset_view()
            return
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
        if event.key == 'b':                  self._toggle_blocks()
        if event.key == 'd' and self._detailed_bundle_artists: self._toggle_detailed()

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
                            if w.original_bundle.id == highlight_bid), None)
            if (wrapper and wrapper.candidates and
                    0 <= wrapper.selected_topology_index < len(wrapper.candidates)):
                topo = wrapper.candidates[wrapper.selected_topology_index]
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
            p.set_visible(self._blocks_visible)
        for txt in self._block_name_artists:
            txt.set_visible(self._blocks_visible and self._block_names_visible)

    def draw_blocks(self):
        self._redraw_blocks()
        self.draw_keepouts()

    def draw_keepouts(self):
        """Draw KeepoutZones as hatched rectangles with layer labels."""
        self._keepout_artists = []
        vis = self._keepouts_visible
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
             # Only show button if there are keepouts
             self._btn_keepouts.ax.set_visible(bool(self._keepout_artists))

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
        self._redraw_colorbar()

    def _redraw_colorbar(self):
        """Re-create the congestion colorbar in the correct left-panel position."""
        if not self._heatmap_artists and self._cbar_ax is None:
            return
        
        # Build dummy data for ScalarMappable if we haven't already.
        import matplotlib.colors as mcolors
        import numpy as np
        cmap = plt.cm.get_cmap('RdYlGn_r')

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
        self._cbar_ax.set_visible(self._heatmap_visible)

    def draw_hanan_grid(self):
        # Hanan grid
        xs, ys = self.fp.get_hanan_grid()
        for x in xs:
            self.ax.axvline(x=x, color='#dddddd', linestyle=':', linewidth=0.4, zorder=0)
        for y in ys:
            self.ax.axhline(y=y, color='#dddddd', linestyle=':', linewidth=0.4, zorder=0)

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

    def _draw_via_marker(self, bid, x, y, msz, alpha, zorder, layer=None):
        """X inside a square at an H↔V segment junction."""
        sq, = self.ax.plot(x, y, 's', color='white',
                           markeredgecolor='black', markeredgewidth=1.2,
                           markersize=msz, alpha=alpha, zorder=zorder, clip_on=True)
        self._register(bid, sq, alpha=alpha, lw=msz, layer=layer)
        self._vias_conns_artists.append(sq)
        xm, = self.ax.plot(x, y, 'x', color='black',
                           markersize=msz * 0.65, markeredgewidth=1.5,
                           alpha=alpha, zorder=zorder + 1, clip_on=True)
        self._register(bid, xm, alpha=alpha, lw=msz * 0.65, layer=layer)
        self._vias_conns_artists.append(xm)
        if not self._vias_conns_visible:
            sq.set_visible(False)
            xm.set_visible(False)

    def _draw_busterm_conn(self, bid, x, y, col, msz, alpha, zorder, layer=None):
        """Filled square at a segment endpoint that connects to a busterm."""
        sq, = self.ax.plot(x, y, 's', color=col,
                           markeredgecolor='black', markeredgewidth=1.0,
                           markersize=msz, alpha=alpha, zorder=zorder, clip_on=True)
        self._register(bid, sq, alpha=alpha, lw=msz, layer=layer)
        self._vias_conns_artists.append(sq)
        if not self._vias_conns_visible:
            sq.set_visible(False)

    def _draw_seg_connectors(self, bid, seg_idx, cs, sx, sy, col, msz, alpha,
                              zorder, along_offset=0.0, adj_perp=None, layer=None):
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
                    self._draw_via_marker(bid, cx, cy, msz, alpha, zorder, layer=layer)
                    continue
            if cs.horiz:
                cx, cy = conn.at_pos + along_offset, sy
            else:
                cx, cy = sx, conn.at_pos + along_offset
            if conn.kind == ic.SegConnKind.BUSTERM:
                self._draw_busterm_conn(bid, cx, cy, col, msz, alpha, zorder, layer=layer)
            else:
                self._draw_via_marker(bid, cx, cy, msz, alpha, zorder, layer=layer)

    def draw_buses(self):
        """Draw topology segments without NUTS track assignment."""
        self._busterm_artists = []
        self._vias_conns_artists = []
        layer_specs = {k: {'color': v} for k, v in _LAYER_COLOR.items()}
        for i, wrapper in enumerate(self.bundles):
            bid      = wrapper.original_bundle.id
            if not wrapper.candidates or wrapper.selected_topology_index < 0 or wrapper.selected_topology_index >= len(wrapper.candidates):
                continue
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
                                          msz, alpha, 12 + i, along_offset=offset,
                                          layer=seg.layer_hint)

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
            if not wrapper.candidates or wrapper.selected_topology_index < 0 or wrapper.selected_topology_index >= len(wrapper.candidates):
                continue
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
                                          adj_perp=adj_perp,
                                          layer=effective_layer)

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
        import buda as ic_mod

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
                # Rails (Power, Ground, CLK) have specific colors.
                # Signal tracks get a very subtle background.
                col = _RAIL_COLOR.get(slot.type, '#f9f9f9')
                alpha = 0.15 if slot.type != 'SIGNAL' else 0.10
                
                half = slot.width / 2.0
                if is_h:
                    rect = patches.Rectangle(
                        (x_min, centre - half), x_max - x_min, slot.width,
                        linewidth=0, facecolor=col, alpha=alpha, zorder=4)
                else:
                    rect = patches.Rectangle(
                        (centre - half, y_min), slot.width, y_max - y_min,
                        linewidth=0, facecolor=col, alpha=alpha, zorder=4)
                self.ax.add_patch(rect)
                self._grid_rail_artists.append({'artist': rect, 'layer': lid, 'alpha': alpha})

        # Draw bit-wire NetSegments.
        self._detailed_result = detailed_result
        # span_lo/span_hi are already junction-adjusted by DetailedNUTSEngine.
        layer_specs = {k: {'color': v} for k, v in _LAYER_COLOR.items()}
        for ns in detailed_result.net_segments:
            is_h  = self._layer_is_h.get(ns.layer, True)
            col   = layer_specs.get(ns.layer, {'color': 'green'})['color']
            lw    = max(0.6, ns.width * 0.6)
            if is_h:
                line, = self.ax.plot(
                    [ns.span_lo, ns.span_hi], [ns.track_position, ns.track_position],
                    color=col, linewidth=lw, solid_capstyle='butt',
                    alpha=0.9, zorder=15)
            else:
                line, = self.ax.plot(
                    [ns.track_position, ns.track_position], [ns.span_lo, ns.span_hi],
                    color=col, linewidth=lw, solid_capstyle='butt',
                    alpha=0.9, zorder=15)
            self._register_detailed(ns.bundle_id, line, alpha=0.9, lw=lw, layer=ns.layer)

        # Start hidden; toggle button reveals them.
        for entries in self._detailed_bundle_artists.values():
            for e in entries:
                e['artist'].set_visible(False)
        for e in self._grid_rail_artists:
            e['artist'].set_visible(False)

        # Reveal control buttons if they exist.
        if self._btn_detailed is not None:
            self._btn_detailed.ax.set_visible(True)
        if self._btn_tracks is not None and self._grid_rail_artists:
            self._btn_tracks.ax.set_visible(self._detailed_mode)

        # Update bundle list to show bit placement stats.
        self._redraw_bundle_list()

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
        for e in self._grid_rail_artists:
            e['artist'].set_visible(active and self._tracks_visible)

        if self._btn_detailed is not None:
            lbl = '☑ Detailed' if active else '☐ Detailed'
            self._btn_detailed.label.set_text(lbl)
            self._btn_detailed.ax.set_facecolor('#ffe8cc' if active else '#e8f4e8')
            
        if self._btn_tracks is not None:
            self._btn_tracks.ax.set_visible(active and bool(self._grid_rail_artists))

        # Re-apply highlight/layer/bundle visibility to the now-active set.
        self._refresh_highlight()
        self.fig.canvas.draw_idle()

    def _toggle_tracks(self):
        self._tracks_visible = not self._tracks_visible
        vis = self._tracks_visible
        
        for e in self._grid_rail_artists:
            # Visibility is hard-gated by detailed_mode, alpha by _refresh_highlight.
            e['artist'].set_visible(self._detailed_mode and vis)
            
        if self._btn_tracks is not None:
            lbl = '☑ Tracks' if vis else '☐ Tracks'
            self._btn_tracks.label.set_text(lbl)
            self._btn_tracks.ax.set_facecolor('#ffe8cc' if vis else '#e8f4e8')
            
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
        self._home_xlim = self.ax.get_xlim()
        self._home_ylim = self.ax.get_ylim()

        # Right panel: x=0.83, width=0.15.  Plot right edge at 0.81.
        # Left panel: centered area for toggles and heatmap.
        # bottom=0.11 reserves room for x-tick labels above the button row.
        # top=0.97 reclaims the wasted margin above the title.
        self.fig.subplots_adjust(left=0.13, bottom=0.11, right=0.81, top=0.97)

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
        self._btn_blknames = Button(ax_blknames, '☑ Blk Names', color='#e8f4e8')
        self._btn_blknames.label.set_fontsize(7.5)
        self._btn_blknames.on_clicked(lambda _: self._toggle_block_names())

        ax_bustermss = self.fig.add_axes(_lrect(BTN_H_L, GAP_L))
        self._btn_bustermss = Button(ax_bustermss, '☑ Busterms', color='#e8f4e8')
        self._btn_bustermss.label.set_fontsize(7.5)
        self._btn_bustermss.on_clicked(lambda _: self._toggle_bustermss())

        ax_vias_conns = self.fig.add_axes(_lrect(BTN_H_L, GAP_L))
        self._btn_vias_conns = Button(ax_vias_conns, '☑ Vias/Conns', color='#e8f4e8')
        self._btn_vias_conns.label.set_fontsize(7.5)
        self._btn_vias_conns.on_clicked(lambda _: self._toggle_vias_conns())

        ax_heatmap = self.fig.add_axes(_lrect(BTN_H_L, GAP_L))
        self._btn_heatmap = Button(ax_heatmap, '☑ Heatmap', color='#e8f4e8')
        self._btn_heatmap.label.set_fontsize(7.5)
        self._btn_heatmap.on_clicked(lambda _: self._toggle_heatmap())
        # Heatmap button is only meaningful when a congestion map was drawn.
        if not self._heatmap_artists and self._cbar_ax is None:
            self._btn_heatmap.ax.set_visible(False)

        ax_keepouts = self.fig.add_axes(_lrect(BTN_H_L, GAP_L))
        self._btn_keepouts = Button(ax_keepouts, '☑ Keepouts', color='#e8f4e8')
        self._btn_keepouts.label.set_fontsize(7.5)
        self._btn_keepouts.on_clicked(lambda _: self._toggle_keepouts())
        # Only visible if there are keepouts
        if not self.fp.get_keepout_zones():
            self._btn_keepouts.ax.set_visible(False)

        ax_detailed = self.fig.add_axes(_lrect(BTN_H_L, GAP_L))
        self._btn_detailed = Button(ax_detailed, '☐ Detailed', color='#e8f4e8')
        self._btn_detailed.label.set_fontsize(7.5)
        self._btn_detailed.on_clicked(lambda _: self._toggle_detailed())
        # Hidden until draw_detailed_tracks() has been called.
        if not self._detailed_bundle_artists:
            self._btn_detailed.ax.set_visible(False)

        ax_tracks = self.fig.add_axes(_lrect(BTN_H_L, GAP_L))
        self._btn_tracks = Button(ax_tracks, '☑ Tracks', color='#ffe8cc')
        self._btn_tracks.label.set_fontsize(7.5)
        self._btn_tracks.on_clicked(lambda _: self._toggle_tracks())
        # Only visible when detailed mode is active and there are rail artists.
        if not self._detailed_mode or not self._grid_rail_artists:
            self._btn_tracks.ax.set_visible(False)

        # Store the current packing position for the colorbar.
        self._ly_post_buttons = ly
        self._redraw_colorbar()

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
        self._btn_all_layers.label.set_fontsize(8.5)
        self._btn_all_layers.on_clicked(lambda _: self._on_layer_toggle_all())

        # ── Per-layer custom panel ───────────────────────────────────────
        self._ax_layers = self.fig.add_axes(_rect(list_h, GAP))
        self._ax_layers.set_facecolor('#f8f8f8')
        self._redraw_layer_list()

        # ── All Bundles ──────────────────────────────────────────────────
        ax_all_bundles = self.fig.add_axes(_rect(BTN_H, GAP))
        self._btn_all_bundles = Button(ax_all_bundles, '☑ All Bundles', color='#e8e8e8')
        self._btn_all_bundles.label.set_fontsize(8.5)
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

        if self._ipc_session:
            import sys as _sys, os as _os
            _tools = _os.path.normpath(
                _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', '..', 'tools'))
            if _tools not in _sys.path:
                _sys.path.insert(0, _tools)
            from viz_ipc import VizIPC, POLL_MS
            self._ipc = VizIPC(self._ipc_session)
            self._ipc.on_message = self._on_ipc_message
            self._ipc.connect_or_serve()
            print(f'[buda_viz] IPC session={self._ipc_session!r} connected={self._ipc._connected}')
            self._ipc_timer = self.fig.canvas.new_timer(interval=POLL_MS)
            self._ipc_timer.add_callback(self._ipc.poll)
            self._ipc_timer.start()
            print(f'[buda_viz] IPC timer started (backend={self.fig.canvas.__class__.__name__})')

        plt.show()

# Just for ref. No longer used.
def _get_nterms(topo):
    """Return the number of unique blocks connected by this topology metadata."""
    names = set()
    # 1. Block connections stored during topology generation
    for eps in topo.seg_busterms.values():
        if eps[0] is not None: names.add(eps[0].block_name)
        if eps[1] is not None: names.add(eps[1].block_name)
    # 2. Bridge segments (for multi-rect TEG blocks)
    for bname in topo.bridge_segments.keys():
        names.add(bname)
    return len(names)
