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

"""BudaVisualizer mixin — Detailed-view drawing: preroutes, rails, bit-wires, via visibility, mode toggles.

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

# Visibility floor for a bit-wire, in POINTS.  A wire narrower than this at
# the current zoom is drawn at the floor so it stays on screen: at
# full-design zoom a real track is far below one pixel, and the detailed
# view's job there is "where did the bits go", not "how wide are they".
# Lower than the abstract view's 2.5pt floor because a detailed view shows
# every BIT — a thousand of them at 2.5pt is a smear where 0.6pt is a bus.
_DETAILED_LW_FLOOR = 0.6


class VizDetailedDrawMixin:

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
        # Grouped by (bundle, layer, WIDTH), not just (bundle, layer): the
        # width is now a per-collection PHYSICAL property that the zoom sync
        # sets as a scalar, so a collection has to be width-uniform.  In
        # practice a layer's signal slots are one width and this adds no
        # groups; a pattern that mixes them gets one collection per width
        # rather than one wrong width for all of them.
        layer_specs = {k: {'color': v} for k, v in _LAYER_COLOR.items()}
        seg_groups = {}   # (bundle_id, layer, width) -> [segment]
        for ns in detailed_result.net_segments:
            is_h = self._layer_is_h.get(ns.layer, True)
            if is_h:
                seg = [(ns.span_lo, ns.track_position), (ns.span_hi, ns.track_position)]
            else:
                seg = [(ns.track_position, ns.span_lo), (ns.track_position, ns.span_hi)]
            seg_groups.setdefault((ns.bundle_id, ns.layer, ns.width), []).append(seg)

        for (bid, layer, width), segs in seg_groups.items():
            col  = layer_specs.get(layer, {'color': 'green'})['color']
            is_h = self._layer_is_h.get(layer, True)
            # A LINEWIDTH IS IN POINTS AND A TRACK WIDTH IS IN LAYOUT UNITS.
            # This used to bake `max(0.6, ns.width * 0.6)` — a points value
            # computed from a layout-unit number, which is only sane when one
            # layout unit is about one point.  It is at micron scale, so the
            # picture looked right for years; `set_import_scale dbu` makes a
            # unit 1/2000 µm, so ariane133's 1.6 µm metal9 wire (3200 DBU)
            # asked for a 1920-POINT line — 27 inches on a 14-inch figure, a
            # band across the whole canvas with the span still correct.
            #
            # The width now travels as the PHYSICAL number it is and
            # _sync_nuts_linewidths converts it to points at the current
            # zoom, as the abstract NUTS lines have always done.  Unit-scale
            # independent by construction, and a zoomed-in wire is drawn at
            # its true width against the [Tracks] rails.
            lc  = LineCollection(segs, colors=col, linewidths=_DETAILED_LW_FLOOR,
                                 capstyle='butt', zorder=15)
            lc.set_alpha(0.9)
            lc.set_visible(False)
            self.ax.add_collection(lc)
            self._register_detailed(bid, lc, alpha=0.9, lw=_DETAILED_LW_FLOOR,
                                    layer=layer, phys_w=width, horiz=is_h,
                                    lw_floor=_DETAILED_LW_FLOOR)

        # The zoom sync is what makes a physical width drawable, so ensure it
        # is hooked HERE too.  It used to be installed only by the abstract
        # NUTS draw, which was fine while it was the only consumer — a
        # detailed view in a session that never drew abstract lines (or one
        # built before them) would otherwise keep its widths frozen.
        # Idempotent: the hook guards itself.
        self._hook_lw_sync()
        # Fit the freshly built widths to the CURRENT zoom.  The hook only
        # fires on a limit change or a draw, and these artists are built
        # lazily on the first [Detailed] toggle — without this they would
        # show at the floor width until the user happened to zoom.
        self._sync_nuts_linewidths()

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
        self._apply_endpoint_label_visibility()


    def _apply_endpoint_label_visibility(self):
        """The `B<id>` endpoint labels show ONLY for the highlighted / soloed
        bundle (and only with Terminals on, not in detailed mode).  Every bundle
        drives one label, so showing all at once piles them up wherever drivers
        cluster (the reported overlap); gating on the selection keeps exactly the
        label the user is inspecting.  Nothing selected -> none shown."""
        show = self.ui_state.busterms and not self.ui_state.detailed_mode
        active = set(getattr(self, '_highlighted_set', None) or set())
        if getattr(self, '_highlighted', None) is not None:
            active.add(self._highlighted)
        for bid, a in getattr(self, '_endpoint_label_artists', ()):
            a.set_visible(show and bid in active)


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
