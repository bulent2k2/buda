import math
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.widgets import Button


_LAYER_COLOR = {4: '#007ACC', 5: '#CC0000', 6: '#00AA44'}
_LAYER_LABEL = {4: 'M4 H', 5: 'M5 V', 6: 'M6 H-trunk'}


class TopologyExplorer:
    """Cycle through all topology candidates for one bundle.

    Navigation: ◀ / ▶ buttons, or ← / → arrow keys.
    Topologies are shown in order of increasing wirelength (pre-sorted by the
    C++ generator).
    """

    def __init__(self, fp, bundle_wrapper):
        self.fp      = fp
        self.wrapper = bundle_wrapper
        self.topos   = bundle_wrapper.candidates   # already sorted by WL
        self.idx     = 0

        self.fig = plt.figure(figsize=(13, 10))
        self.fig.patch.set_facecolor('#f0f0f0')

        # Main axes — leave bottom margin for buttons
        self.ax = self.fig.add_axes([0.05, 0.12, 0.90, 0.82])

        # Prev / Next buttons
        ax_prev = self.fig.add_axes([0.08, 0.02, 0.18, 0.05])
        ax_next = self.fig.add_axes([0.74, 0.02, 0.18, 0.05])
        self._btn_prev = Button(ax_prev, '◀  Prev',  color='#ddeeff')
        self._btn_next = Button(ax_next, 'Next  ▶', color='#ddeeff')
        self._btn_prev.on_clicked(lambda _: self._step(-1))
        self._btn_next.on_clicked(lambda _: self._step(+1))

        self.fig.canvas.mpl_connect('key_press_event', self._on_key)

        self._draw()

    # ------------------------------------------------------------------

    def _step(self, delta):
        self.idx = (self.idx + delta) % len(self.topos)
        self._draw()

    def _on_key(self, event):
        if event.key in ('left',  'a'): self._step(-1)
        if event.key in ('right', 'd'): self._step(+1)

    def _draw(self):
        ax = self.ax
        ax.clear()

        topo  = self.topos[self.idx]
        n     = len(self.topos)
        bid   = self.wrapper.original_bundle.id
        wl    = topo.estimated_wirelength

        ax.set_title(
            f"Bundle {bid}  ·  topology {self.idx + 1} / {n}"
            f"  ·  {topo.type}  ·  WL = {wl}",
            fontsize=13, pad=10)

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

        # Topology segments — width proportional to bundle width
        viz_lw = min(3.0 + math.log2(1 + self.wrapper.width) * 1.5, 14.0)
        for seg in topo.segments:
            col = _LAYER_COLOR.get(seg.layer_hint, '#888888')
            ax.plot([seg.start.x, seg.end.x], [seg.start.y, seg.end.y],
                    color=col, linewidth=viz_lw,
                    solid_capstyle='round', zorder=10)
            # Junction dot where stubs meet the trunk
            ax.plot(seg.start.x, seg.start.y, 'o',
                    color=col, markersize=viz_lw * 0.6, zorder=11)
            ax.plot(seg.end.x, seg.end.y, 'o',
                    color=col, markersize=viz_lw * 0.6, zorder=11)

        # Legend
        from matplotlib.lines import Line2D
        used_layers = sorted({s.layer_hint for seg in topo.segments
                               for s in [seg]})
        handles = [Line2D([0], [0], color=_LAYER_COLOR.get(l, '#888'), lw=3,
                          label=_LAYER_LABEL.get(l, f'Layer {l}'))
                   for l in used_layers]
        ax.legend(handles=handles, loc='upper right', fontsize=9)

        ax.set_aspect('equal')
        ax.autoscale_view()
        self.fig.canvas.draw_idle()

    def show(self):
        plt.show()


class BudaVisualizer:
    def __init__(self, floorplan, bundles):
        self.fp = floorplan
        self.bundles = bundles
        self.fig, self.ax = plt.subplots(figsize=(14, 12))
        self.fig.patch.set_facecolor('#f0f0f0')

        # bundle_id -> list of dicts {artist, alpha, lw, is_band}
        self._bundle_artists = {}
        self._highlighted = None   # currently highlighted bundle_id, or None
        self._pick_happened = False

        self.fig.canvas.mpl_connect('pick_event',        self._on_pick)
        self.fig.canvas.mpl_connect('button_press_event', self._on_click)

    # ------------------------------------------------------------------
    # Artist registry & interaction
    # ------------------------------------------------------------------

    def _register(self, bundle_id, artist, *, alpha, lw=None, is_band=False):
        """Make an artist pickable and store its resting style for later restore."""
        artist.set_picker(5)
        self._bundle_artists.setdefault(bundle_id, []).append({
            'artist': artist,
            'alpha':  alpha,
            'lw':     lw,
            'is_band': is_band,
        })

    def _on_pick(self, event):
        self._pick_happened = True
        for bid, entries in self._bundle_artists.items():
            for e in entries:
                if event.artist is e['artist']:
                    self._set_highlight(bid)
                    return

    def _on_click(self, event):
        # A click that didn't land on any registered artist → deselect.
        if not self._pick_happened and event.inaxes == self.ax:
            self._set_highlight(None)
        self._pick_happened = False

    def _set_highlight(self, bundle_id):
        # Clicking the already-highlighted bundle toggles it off.
        if bundle_id == self._highlighted:
            bundle_id = None
        self._highlighted = bundle_id

        for bid, entries in self._bundle_artists.items():
            selected = (bundle_id is None) or (bid == bundle_id)
            for e in entries:
                a = e['artist']
                if bundle_id is None:
                    # Reset to resting style.
                    a.set_alpha(e['alpha'])
                    if e['lw'] is not None:
                        a.set_linewidth(e['lw'])
                elif selected:
                    # Highlight: full opacity, thicker lines.
                    a.set_alpha(0.2 if e['is_band'] else 1.0)
                    if e['lw'] is not None:
                        a.set_linewidth(e['lw'] * 2.2)
                else:
                    # Dim everything else.
                    a.set_alpha(0.03 if e['is_band'] else 0.1)
                    if e['lw'] is not None:
                        a.set_linewidth(e['lw'])

        if bundle_id is not None:
            self.ax.set_title(
                f"BUDA — Bundle {bundle_id} selected  "
                f"(click again or click background to deselect)",
                fontsize=13)
        else:
            self.ax.set_title(
                "BUDA: Non-Uniform Track Sharing (NUTS)  "
                "— click a bus-term or bus-seg to highlight",
                fontsize=13)

        self.fig.canvas.draw_idle()

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
        import matplotlib.colors as mcolors
        cmap = plt.cm.RdYlGn_r   # green=free, red=overflowed
        xs, ys = self.fp.get_hanan_grid()

        for cut in cuts:
            ratio = (cut.current_usage / cut.capacity) if cut.capacity > 0 else 0.0
            if ratio == 0:
                continue
            color = cmap(min(ratio, 1.5) / 1.5)   # cap visual at 150 % for colour
            alpha = 0.12 + 0.22 * min(ratio, 1.0)  # subtle — buses stay readable

            # Draw a band covering the channel this cut represents.
            # Vertical cut → shade the channel it bisects (between adjacent x-lines).
            if cut.p1.x == cut.p2.x:          # vertical cut
                cx = cut.p1.x
                # Find enclosing x interval
                x_idx = [i for i, x in enumerate(xs) if x <= cx]
                if not x_idx: continue
                xi = x_idx[-1]
                x_lo = xs[xi]
                x_hi = xs[xi + 1] if xi + 1 < len(xs) else cx + 20
                self.ax.add_patch(patches.Rectangle(
                    (x_lo, ys[0]), x_hi - x_lo, ys[-1] - ys[0],
                    linewidth=0, facecolor=color, alpha=alpha, zorder=3))
                # Label overflow
                if ratio > 1.0:
                    self.ax.text((x_lo + x_hi) / 2, (ys[0] + ys[-1]) / 2,
                                 f"OVF\n{ratio:.0%}", fontsize=7, color='darkred',
                                 ha='center', va='center', zorder=4, fontweight='bold')
            else:                               # horizontal cut
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

    def draw_buses(self):
        """Draw topology segments without NUTS track assignment."""
        layer_specs = {k: {'color': v} for k, v in _LAYER_COLOR.items()}
        for i, wrapper in enumerate(self.bundles):
            bid      = wrapper.original_bundle.id
            topo     = wrapper.candidates[wrapper.selected_topology_index]
            viz_lw   = 3.0 + math.log2(1 + wrapper.width) * 2.0
            offset   = (i % 3 - 1) * 2.0
            alpha    = 0.8

            topo_start = topo_end = None
            for idx, seg in enumerate(topo.segments):
                spec = layer_specs.get(seg.layer_hint, {'color': 'green'})
                sx = seg.start.x + offset;  sy = seg.start.y + offset
                ex = seg.end.x   + offset;  ey = seg.end.y   + offset
                if idx == 0: topo_start = (sx, sy)
                if idx == len(topo.segments) - 1: topo_end = (ex, ey)

                line, = self.ax.plot([sx, ex], [sy, ey],
                                     color=spec['color'], linewidth=viz_lw,
                                     solid_capstyle='butt', alpha=alpha,
                                     zorder=10 + i)
                self._register(bid, line, alpha=alpha, lw=viz_lw)

                if idx < len(topo.segments) - 1:
                    via, = self.ax.plot(ex, ey, 'o', color='black',
                                        markersize=viz_lw / 3,
                                        alpha=alpha, zorder=11 + i)
                    self._register(bid, via, alpha=alpha, lw=viz_lw / 3)

            self._draw_terminals(bid, topo_start, topo_end, viz_lw, alpha)

    def draw_nuts_tracks(self, nuts_result):
        """Draw segments at NUTS-assigned track positions with interval bands."""
        layer_specs = {k: {'color': v} for k, v in _LAYER_COLOR.items()}
        ts_map = {(ts.bundle_id, ts.seg_idx): ts for ts in nuts_result.segments}
        band_alpha = 0.04   # very subtle — just marks the hard interval boundary
        seg_alpha  = 0.90

        for i, wrapper in enumerate(self.bundles):
            bid    = wrapper.original_bundle.id
            topo   = wrapper.candidates[wrapper.selected_topology_index]
            viz_lw = (wrapper.width * 1.5) + 2.0

            topo_start = topo_end = None
            for idx, seg in enumerate(topo.segments):
                ts   = ts_map.get((bid, idx))
                spec = layer_specs.get(seg.layer_hint, {'color': 'green'})
                col  = spec['color']

                if ts and ts.placed:
                    center = ts.track_position + ts.width / 2.0
                    is_h   = (seg.start.y == seg.end.y)

                    if is_h:
                        sx, ex = seg.start.x, seg.end.x
                        sy = ey = center
                        # Filled footprint of the placed bus (actual track occupancy)
                        footprint = patches.Rectangle(
                            (min(sx, ex), ts.track_position),
                            abs(ex - sx), ts.width,
                            linewidth=0, facecolor=col,
                            alpha=band_alpha * 3, zorder=5)
                        self.ax.add_patch(footprint)
                        self._register(bid, footprint, alpha=band_alpha*3, is_band=True)
                        # Dashed lines at interval bounds (constraint context)
                        for y_bound in (ts.interval_lo, ts.interval_hi):
                            bl, = self.ax.plot([min(sx,ex), max(sx,ex)], [y_bound, y_bound],
                                               color=col, linewidth=0.5, linestyle='--',
                                               alpha=0.3, zorder=4)
                            self._register(bid, bl, alpha=0.3, is_band=True)
                    else:
                        sy, ey = seg.start.y, seg.end.y
                        sx = ex = center
                        footprint = patches.Rectangle(
                            (ts.track_position, min(sy, ey)),
                            ts.width, abs(ey - sy),
                            linewidth=0, facecolor=col,
                            alpha=band_alpha * 3, zorder=5)
                        self.ax.add_patch(footprint)
                        self._register(bid, footprint, alpha=band_alpha*3, is_band=True)
                        for x_bound in (ts.interval_lo, ts.interval_hi):
                            bl, = self.ax.plot([x_bound, x_bound], [min(sy,ey), max(sy,ey)],
                                               color=col, linewidth=0.5, linestyle='--',
                                               alpha=0.3, zorder=4)
                            self._register(bid, bl, alpha=0.3, is_band=True)
                else:
                    sx, sy = seg.start.x, seg.start.y
                    ex, ey = seg.end.x,   seg.end.y

                if idx == 0: topo_start = (sx, sy)
                if idx == len(topo.segments) - 1: topo_end = (ex, ey)

                line, = self.ax.plot([sx, ex], [sy, ey],
                                     color=col, linewidth=viz_lw,
                                     solid_capstyle='butt',
                                     alpha=seg_alpha, zorder=10 + i)
                self._register(bid, line, alpha=seg_alpha, lw=viz_lw)

                if idx < len(topo.segments) - 1:
                    via, = self.ax.plot(ex, ey, 'o', color='black',
                                        markersize=viz_lw / 3,
                                        alpha=seg_alpha, zorder=11 + i)
                    self._register(bid, via, alpha=seg_alpha, lw=viz_lw / 3)

            self._draw_terminals(bid, topo_start, topo_end, viz_lw, seg_alpha)

    def _draw_terminals(self, bundle_id, topo_start, topo_end, viz_lw, alpha):
        """Draw driver (cyan square) and receiver (magenta circle) terminals."""
        msz = min(viz_lw, 16)   # cap terminal size so fat buses don't blow up
        if topo_start:
            drv, = self.ax.plot(topo_start[0], topo_start[1], 's',
                                color='#00FFFF', markeredgecolor='black',
                                markersize=msz, alpha=alpha, zorder=20)
            self._register(bundle_id, drv, alpha=alpha, lw=msz)
            self.ax.text(topo_start[0], topo_start[1], f"B{bundle_id}",
                         fontsize=8, color='black', fontweight='bold',
                         ha='center', va='center', zorder=21)

        if topo_end:
            rcv, = self.ax.plot(topo_end[0], topo_end[1], 'o',
                                color='#FF00FF', markeredgecolor='black',
                                markersize=msz, alpha=alpha, zorder=20)
            self._register(bundle_id, rcv, alpha=alpha, lw=msz)

    def show(self):
        self.ax.set_aspect('equal')
        self.ax.set_title(
            "BUDA: Non-Uniform Track Sharing (NUTS)  "
            "— click a bus-term or bus-seg to highlight",
            fontsize=13)

        from matplotlib.lines import Line2D
        self.ax.legend(handles=[
            Line2D([0], [0], color='#007ACC', lw=4, label='M4 Horizontal'),
            Line2D([0], [0], color='#CC0000', lw=4, label='M5 Vertical'),
            Line2D([0], [0], color='#00AA44', lw=4, label='M6 H-trunk (U-shape)'),
            Line2D([0], [0], marker='s', color='w',
                   markerfacecolor='#00FFFF', markeredgecolor='k', label='Driver term'),
            Line2D([0], [0], marker='o', color='w',
                   markerfacecolor='#FF00FF', markeredgecolor='k', label='Receiver term'),
        ], loc='upper right')

        self.ax.autoscale_view()
        plt.tight_layout()
        plt.show()
