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

"""BudaVisualizer mixin — Artist registry, zoom-true width sync, picking, selection highlight, IPC.

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

class VizHighlightMixin:

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
        if not self._bundle_artists and not self._detailed_bundle_artists:
            return False
        o  = self.ax.transData.transform((0.0, 0.0))
        px = self.ax.transData.transform((1.0, 0.0))[0] - o[0]
        py = self.ax.transData.transform((0.0, 1.0))[1] - o[1]
        pts_x = abs(px) * 72.0 / self.fig.dpi   # points per data unit, x
        pts_y = abs(py) * 72.0 / self.fig.dpi   # points per data unit, y
        changed = False
        # BOTH registries: the detailed bit-wires are physical-width lines
        # exactly as the abstract NUTS lines are, and syncing only the
        # abstract ones left the detailed view's widths frozen at whatever
        # was baked in at build time.
        for entries in list(self._bundle_artists.values()) + \
                       list(self._detailed_bundle_artists.values()):
            for e in entries:
                pw = e.get('phys_w')
                mp = e.get('marker_phys')
                if pw:
                    # An H segment's width is vertical; a V segment's horizontal.
                    pts = pts_y if e['horiz'] else pts_x
                    cap = e.get('lw_cap')
                    floor = e.get('lw_floor') or self._LW_MIN_PTS
                    want = pw * pts
                    lw  = max(floor, want if cap is None else min(cap, want))
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
        # One mouse press dispatches a SEPARATE pick_event per registered
        # artist under the cursor; a bus wire always sits inside its own
        # footprint band, so two events fire for the same bundle and the
        # toggling _set_highlight cancelled itself out (select-then-deselect).
        # Dedupe per press by the triggering mouseevent identity (audit P7-02).
        if me is not None:
            if getattr(self, '_pick_me_id', None) == id(me):
                return
            self._pick_me_id = id(me)
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

        # B<id> endpoint labels follow the SELECTION (shown only for the
        # highlighted/soloed bundle) so they don't pile up across all drivers.
        self._apply_endpoint_label_visibility()

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

            # BUSTERMS — what the route lands on — and, when it differs, the
            # NET ENDPOINT count it resolves from.  This used to print
            # `num_terminals` under the "bterms" label, which is an endpoint
            # count at netlist depth: ariane133's B154 said 89 while the
            # drawing showed 17 blocks (see busterm_counts).
            nbt, nterms = busterm_counts(wrapper)

            # Segment count of the bundle's selected topology — handy when
            # cycling bundles with `n` to gauge each topology's complexity.
            cands = wrapper.input.candidates
            sel   = wrapper.plan.selected_topology_index
            nsegs = len(cands[sel].segments) if cands and 0 <= sel < len(cands) else 0

            info = []
            if nbits > 0:
                if nbt is None:
                    info.append(f"{nbits} bits/{nterms} endpoints")
                elif nbt == nterms:
                    info.append(f"{nbits} bits/{nbt} bterms")
                else:
                    # Both, because the GAP is the information: it says the
                    # netlist reaches N leaf instances that the routing frame
                    # resolves onto far fewer blocks.
                    info.append(f"{nbits} bits/{nbt} bterms of {nterms} endpoints")
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


    # ------------------------------------------------------------------
    # Detailed NUTS (Stage 9) drawing
    # ------------------------------------------------------------------

    def _register_detailed(self, bundle_id, artist, *, alpha, lw=None, layer=None,
                           phys_w=None, horiz=None, lw_floor=None):
        artist.set_picker(5)
        self._detailed_bundle_artists.setdefault(bundle_id, []).append({
            'artist':  artist,
            'alpha':   alpha,
            'lw':      lw,
            'is_band': False,
            'layer':   layer,
            # Bit-wires carry their PHYSICAL track width the same way the
            # abstract NUTS lines do, so _sync_nuts_linewidths fits the drawn
            # point-width to the true footprint at every zoom.  Baking a
            # static point-width from a layout-unit number instead is only
            # right when one layout unit happens to be about one point — see
            # the note in _build_detailed_artists.
            'phys_w':   phys_w,
            'horiz':    horiz,
            # UNCAPPED, unlike the abstract view: its cap keeps a schematic
            # bold line from ballooning, but a bit-wire IS its width — zoomed
            # in it has to keep matching the [Tracks] rails it sits on, which
            # are drawn in data coordinates.
            'lw_cap':   None,
            'lw_floor': lw_floor,
            'marker_phys': None,
            'ms': None, 'ms_cap': None, 'ms_floor': 4.0,
        })
