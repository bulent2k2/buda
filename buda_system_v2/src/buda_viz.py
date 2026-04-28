import matplotlib.pyplot as plt
import matplotlib.patches as patches


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

    def draw_hanan_grid(self):
        xs, ys = self.fp.get_hanan_grid()
        for x in xs:
            self.ax.axvline(x=x, color='#cccccc', linestyle='--', linewidth=0.5, zorder=0)
        for y in ys:
            self.ax.axhline(y=y, color='#cccccc', linestyle='--', linewidth=0.5, zorder=0)

    def draw_buses(self):
        """Draw topology segments without NUTS track assignment."""
        layer_specs = {
            3: {'color': '#007ACC'},
            4: {'color': '#CC0000'},
        }
        for i, wrapper in enumerate(self.bundles):
            bid      = wrapper.original_bundle.id
            topo     = wrapper.candidates[wrapper.selected_topology_index]
            viz_lw   = (wrapper.width * 1.5) + 2.0
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
        layer_specs = {
            3: {'color': '#007ACC'},
            4: {'color': '#CC0000'},
        }
        ts_map = {(ts.bundle_id, ts.seg_idx): ts for ts in nuts_result.segments}
        band_alpha = 0.08
        seg_alpha  = 0.85

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
                        band = patches.Rectangle(
                            (min(sx, ex), ts.interval_lo),
                            abs(ex - sx), ts.interval_hi - ts.interval_lo,
                            linewidth=0, facecolor=col,
                            alpha=band_alpha, zorder=5)
                    else:
                        sy, ey = seg.start.y, seg.end.y
                        sx = ex = center
                        band = patches.Rectangle(
                            (ts.interval_lo, min(sy, ey)),
                            ts.interval_hi - ts.interval_lo, abs(ey - sy),
                            linewidth=0, facecolor=col,
                            alpha=band_alpha, zorder=5)

                    self.ax.add_patch(band)
                    self._register(bid, band, alpha=band_alpha, is_band=True)
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
        if topo_start:
            drv, = self.ax.plot(topo_start[0], topo_start[1], 's',
                                color='#00FFFF', markeredgecolor='black',
                                markersize=viz_lw, alpha=alpha, zorder=20)
            self._register(bundle_id, drv, alpha=alpha, lw=viz_lw)
            self.ax.text(topo_start[0], topo_start[1], f"B{bundle_id}",
                         fontsize=8, color='black', fontweight='bold',
                         ha='center', va='center', zorder=21)

        if topo_end:
            rcv, = self.ax.plot(topo_end[0], topo_end[1], 'o',
                                color='#FF00FF', markeredgecolor='black',
                                markersize=viz_lw, alpha=alpha, zorder=20)
            self._register(bundle_id, rcv, alpha=alpha, lw=viz_lw)

    def show(self):
        self.ax.set_aspect('equal')
        self.ax.set_title(
            "BUDA: Non-Uniform Track Sharing (NUTS)  "
            "— click a bus-term or bus-seg to highlight",
            fontsize=13)

        from matplotlib.lines import Line2D
        self.ax.legend(handles=[
            Line2D([0], [0], color='#007ACC', lw=4, label='Metal 3 (Horiz)'),
            Line2D([0], [0], color='#CC0000', lw=4, label='Metal 4 (Vert)'),
            Line2D([0], [0], marker='s', color='w',
                   markerfacecolor='#00FFFF', markeredgecolor='k', label='Driver term'),
            Line2D([0], [0], marker='o', color='w',
                   markerfacecolor='#FF00FF', markeredgecolor='k', label='Receiver term'),
        ], loc='upper right')

        self.ax.autoscale_view()
        plt.tight_layout()
        plt.show()
