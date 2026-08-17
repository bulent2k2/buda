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

"""Shared BUDA-viz helpers: layer colors/labels, block & Hanan-grid drawing,
zoom/pan geometry, button styling, and small pure utilities.  Consumed by
both viewer classes (TopologyExplorer, BudaVisualizer) and re-exported by
buda_viz for external callers.  No class state, no imports back into the
viewer modules."""
import math

import matplotlib.pyplot as plt
import matplotlib.patches as patches

import buda as ic
from ui_state import ViewState

__all__ = [
    "_LAYER_COLOR", "_LAYER_LABEL", "_PREROUTE_COLOR", "stat_title",
    "_layer_is_h_map", "_layer_label",
    "_draw_hanan_grid", "_draw_blocks", "_rects_disconnected", "_draw_block",
    "_set_lims_filling_box", "_install_home_fit_tracking",
    "_apply_bbox_zoom", "_install_bbox_zoom",
    "_hover_color", "style_button",
    "_pan_axes", "_PAN_STEP", "_UNCONSTRAINED",
    "collect_candidate_bundles", "_get_nterms", "busterm_counts",
    "snap_endpoint_extents",
]


_LAYER_COLOR = {1: '#000075', 2: '#a9a9a9', 3: '#FF8800', 4: '#007ACC', 5: '#CC0000', 6: '#00AA44', 7: '#8800CC', 8: '#F032E6', 9: '#42D4F4', 10: '#9A6324'}

# Default-orientation fallback only; the real H/V comes from the LayerStack via
# _layer_label() (def_layer can override these, e.g. M4 V instead of M4 H).
_LAYER_LABEL = {1: 'M1 V', 2: 'M2 H', 3: 'M3 V', 4: 'M4 H', 5: 'M5 V', 6: 'M6 H', 7: 'M7 V', 8: 'M8 H', 9: 'M9 V', 10: 'M10 H'}


# Track-band colours by TrackSlot type, shared by the [Preroutes] bands and
# the [Tracks] rail stripes.  SIGNAL rails (the [Tracks] view) are coloured
# by their LAYER instead — a faint stripe of the same colour as the
# bit-wires that land on it (the old near-white '#f9f9f9' was invisible on
# the white canvas).
_PREROUTE_COLOR = {'POWER': '#ffcccc', 'GROUND': '#cce0ff',
                   'CLOCK': '#fffacc', 'SHIELD': '#e0d4f7'}



def _layer_is_h_map(layer_stack):
    """{layer_id: is_horizontal} for every layer in a LayerStack."""
    import buda as ic_mod
    m = {}
    for lid in layer_stack.get_layer_ids_by_dir(ic_mod.LayerDir.HORIZONTAL):
        m[lid] = True
    for lid in layer_stack.get_layer_ids_by_dir(ic_mod.LayerDir.VERTICAL):
        m[lid] = False
    return m



def _layer_label(lid, layer_stack=None):
    """'M<id> <H|V>' using the actual orientation from the layer stack when
    available, so a def_layer that overrides the default (e.g. M4 V) is honored
    in the UI. Falls back to the default-orientation table otherwise."""
    if layer_stack is not None:
        try:
            if layer_stack.has_layer(lid):
                d = ('H' if layer_stack.get_layer_dir(lid) == ic.LayerDir.HORIZONTAL
                     else 'V')
                return f"M{lid} {d}"
        except Exception:
            pass
    return _LAYER_LABEL.get(lid, f"M{lid}")


stat_title = "Bundle-based Design Assistant (BUDA) with Non-Uniform Track Sharing (NUTS)"



def _draw_hanan_grid(ax, fp, ui_state: ViewState, force=False, grid=None):
    """Draw the Hanan grid and return the line artists. Visibility is set by
    ui_state; `force` shows the grid regardless (the topology editor turns it
    on for the session — trunks land on Hanan lines, so the targets must be
    visible — without touching the shared ui_state the main window reads).
    `grid` = explicit (xs, ys) to draw instead of the full-design
    fp.get_hanan_grid() (the explorer passes the bundle-scoped grid)."""
    artists = []
    xs, ys = grid if grid is not None else fp.get_hanan_grid()
    # Style: prominent dashed line for debugging
    color = '#94a3b8'  # slate-400
    visible = force or ui_state.hanan_grid
    for x in xs:
        l = ax.axvline(x=x, color=color, linestyle='--', linewidth=0.7, alpha=0.6, zorder=0)
        l.set_visible(visible)
        artists.append(l)
    for y in ys:
        l = ax.axhline(y=y, color=color, linestyle='--', linewidth=0.7, alpha=0.6, zorder=0)
        l.set_visible(visible)
        artists.append(l)
    return artists



def _draw_blocks(ax, fp, ui_state: ViewState, highlight_names=None):
    """Draw all floorplan blocks based on ViewState.
    
    Returns (patch_artists, name_artists).
    """
    patch_artists = []
    name_artists = []
    if not ui_state.blocks:
        return patch_artists, name_artists

    highlights = highlight_names or set()
    for name, rect in fp.get_all_blocks():
        if name in highlights:
            # Highlighted style for busterm blocks
            ps, txt = _draw_block(ax, name, rect, fp,
                                  lw=1.8, edge='#333333', face='#d0f0d0',
                                  alpha=0.50, fontsize=8, zorder=1.5)
        else:
            # Dimmed style for the rest
            ps, txt = _draw_block(ax, name, rect, fp,
                                  lw=1.0, alpha=0.18, fontsize=7)
        patch_artists.extend(ps)
        if txt is not None:
            txt.set_visible(ui_state.block_names)
            name_artists.append(txt)
    return patch_artists, name_artists



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



def _short_block_label(name, maxlen=16):
    """Compact on-screen block label.

    Hierarchical instance paths (`chip/i_dnuts1_0/u0`) overlap badly on dense
    floorplans — their FULL length swamps neighbouring blocks.  For a `a/b/c`
    path show the LEAF component (`u0`), which identifies the block within its
    parent, with the spatial position carrying the instance context; a flat name
    (no `/`, e.g. `blk_20`) is returned unchanged.  A still-over-long leaf is
    tail-ellipsised to `maxlen` (the tail is the most-identifying part).  This is
    display only — the block's identity for click/highlight stays the full
    `name`."""
    label = name.rsplit('/', 1)[-1] if '/' in name else name
    if len(label) > maxlen:
        label = '…' + label[-(maxlen - 1):]
    return label


# Label anchor per hierarchy depth: (x-attr, y-attr, ha, va, dx_pt, dy_pt).
# A block labels at a CORNER chosen by its depth so a parent and its nested
# child — which share the bottom-left corner — anchor at DIFFERENT corners and
# their labels no longer overlap.  Cycles through the four corners (depth % 4);
# depth 0 keeps the historical bottom-left placement, so flat designs are
# unchanged.  The point offsets inset the text toward the block interior.
_LABEL_CORNERS = [
    ('x1', 'y1', 'left',  'bottom',  4,  4),   # depth 0: bottom-left
    ('x1', 'y2', 'left',  'top',     4, -4),   # depth 1: top-left
    ('x2', 'y1', 'right', 'bottom', -4,  4),   # depth 2: bottom-right
    ('x2', 'y2', 'right', 'top',    -4, -4),   # depth 3: top-right
]


def _label_anchor(bbox, depth):
    """(x, y, ha, va, dx_pt, dy_pt) for a block label at the corner chosen by
    its hierarchy `depth` — cycling the four corners so a parent (depth d) and
    its child (depth d+1) never share a label position."""
    xk, yk, ha, va, dx, dy = _LABEL_CORNERS[depth % len(_LABEL_CORNERS)]
    return getattr(bbox, xk), getattr(bbox, yk), ha, va, dx, dy


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

    import matplotlib.transforms as mtransforms
    # Anchor the label at a depth-selected corner (see _LABEL_CORNERS) so a
    # parent cell and its bottom-left child no longer stack their names.
    lx, ly, ha, va, dx, dy = _label_anchor(bbox, name.count('/'))
    offset_trans = mtransforms.offset_copy(
        ax.transData, fig=ax.figure, x=dx, y=dy, units='points')
    txt = ax.text(lx, ly, _short_block_label(name), transform=offset_trans,
                  ha=ha, va=va,
                  fontsize=fontsize, fontweight='bold', color='#444444',
                  alpha=min(1.0, alpha * 4.0), # label slightly brighter than block
                  zorder=zorder + 1, clip_on=True)
    return added_patches, txt


def _set_lims_filling_box(ax, x0, x1, y0, y1):
    """Set ax limits to frame [x0,x1]x[y0,y1] but expand the shorter axis so the
    limits' aspect matches the axes' on-screen box.  With set_aspect('equal')
    this makes the view fill the whole window instead of collapsing to the
    narrower dimension (a thin sliver/letterbox in a sea of background)."""
    # original=True: the full allocated axes rectangle.  With set_aspect('equal')
    # + adjustable='box', the active position is already shrunk to the data
    # aspect (the distortion we're undoing), so we must use the original box.
    pos = ax.get_position(original=True)
    fw, fh = ax.figure.get_size_inches()
    ax_h_px = fh * pos.height
    if ax_h_px <= 0:
        ax.set_xlim(x0, x1); ax.set_ylim(y0, y1)
        return
    box_aspect = (fw * pos.width) / ax_h_px   # desired data width / height
    bw, bh = x1 - x0, y1 - y0
    if bh <= 0 or bw / bh < box_aspect:        # too narrow -> widen
        cx, half = (x0 + x1) / 2, max(bh, 1e-9) * box_aspect / 2
        x0, x1 = cx - half, cx + half
    else:                                      # too short -> heighten
        cy, half = (y0 + y1) / 2, bw / box_aspect / 2
        y0, y1 = cy - half, cy + half
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)


def _install_home_fit_tracking(viz):
    """Keep the home view maximal as the window reaches (and changes) its size.

    The fit computed at build time (in show()/_draw) uses the *nominal* figure
    geometry; the true on-screen axes box is not known until the GUI backend
    realizes — and, on macOS, *maximizes* — the window, which settles over
    several frames AFTER show() (see install_tk_geometry_resync).  A single
    first-draw refit fires too early, at the pre-maximize size, so the view is
    only maximal after a manual 'h'.  Instead, re-apply the fill on every
    resize_event (and the first draws), always with the same math the 'h' key
    uses, so the home view tracks the window to its final maximized size and
    stays maximal on any later resize.

    The refit only runs while the view is still the home fit, so it never
    clobbers a user pan/zoom; and it forces a redraw only when the fill actually
    changed, so the resize→draw feedback settles instead of looping.  Harmless
    under headless backends where these events never fire (the nominal fit
    already stands)."""
    def _refit(_event):
        bbox = getattr(viz, '_home_data_bbox', None)
        if bbox is None:
            return
        def _close(a, b):
            return all(abs(p - q) <= 1e-6 * (1 + abs(q)) for p, q in zip(a, b))
        cur  = viz.ax.get_xlim() + viz.ax.get_ylim()
        home = ((viz._home_xlim or (0.0, 0.0)) + (viz._home_ylim or (0.0, 0.0)))
        # Still at the home fit? If the user has panned/zoomed away, leave it.
        if viz._home_xlim is not None and not _close(cur, home):
            return
        _set_lims_filling_box(viz.ax, *bbox)
        new = viz.ax.get_xlim() + viz.ax.get_ylim()
        viz._home_xlim = viz.ax.get_xlim()
        viz._home_ylim = viz.ax.get_ylim()
        # Only redraw when the fill moved, so a resize's follow-up draw_event
        # (which re-enters here at the settled size) terminates instead of looping.
        if viz._home_xlim is None or not _close(new, home):
            viz.fig.canvas.draw_idle()
    viz.fig.canvas.mpl_connect('resize_event', _refit)
    viz.fig.canvas.mpl_connect('draw_event', _refit)


def _apply_bbox_zoom(viz, x0, y0, x1, y1):
    """Apply a rubber-band zoom to viz.ax (adopted from the Floorplanner).

    LR drag (x1 > x0): zoom IN — frame the drawn box, expanded to fill the
        window so set_aspect('equal') adds no letterbox (same math as 'h').
    RL drag (x1 < x0): zoom OUT — the current view expands so it would occupy
        the drawn box, centred on the current view.
    A degenerate (barely-moved / skinny) box is ignored."""
    ax = viz.ax
    xlim = ax.get_xlim(); ylim = ax.get_ylim()
    box_w, box_h = abs(x1 - x0), abs(y1 - y0)
    view_w, view_h = abs(xlim[1] - xlim[0]), abs(ylim[1] - ylim[0])
    if box_w < view_w * 0.01 or box_h < view_h * 0.01:
        viz.fig.canvas.draw_idle()
        return
    if x1 > x0:
        _set_lims_filling_box(ax, min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1))
    else:
        # Aspect-correct the box to the axes box, then expand the view by
        # view/box so the current viewport would land inside the drawn box.
        pos = ax.get_position(original=True)
        fw, fh = ax.figure.get_size_inches()
        ax_h = fh * pos.height
        ax_aspect = (fw * pos.width) / ax_h if ax_h > 0 else 1.0
        if box_h > 0 and box_w / box_h < ax_aspect:
            box_w = box_h * ax_aspect
        elif box_w > 0:
            box_h = box_w / ax_aspect
        f = max(view_w / box_w if box_w > 0 else 1.0,
                view_h / box_h if box_h > 0 else 1.0)
        vcx = (xlim[0] + xlim[1]) / 2; vcy = (ylim[0] + ylim[1]) / 2
        ax.set_xlim(vcx - view_w * f / 2, vcx + view_w * f / 2)
        ax.set_ylim(vcy - view_h * f / 2, vcy + view_h * f / 2)
    viz.fig.canvas.draw_idle()


def _install_bbox_zoom(viz):
    """Right-click-drag rubber-band zoom on viz.ax, adopted from the Floorplanner.

    Drag left→right to zoom INTO the box, right→left to zoom OUT.  A live dashed
    rectangle previews the box (blue = in, orange = out).  Right-click is used so
    it never conflicts with left-click selection.  A zoomed view is not the home
    fit, so the resize-tracking guard leaves it alone; 'h' returns to home."""
    st = {"x0": None, "y0": None, "patch": None}

    def _clear_patch():
        if st["patch"] is not None:
            try:
                st["patch"].remove()
            except Exception:
                pass
            st["patch"] = None

    def _press(event):
        if event.button != 3 or event.inaxes != viz.ax:
            return
        # Don't hijack the gesture while a matplotlib toolbar tool (pan/zoom) is
        # active — same guard as _on_click and the Floorplanner press handler.
        toolbar = getattr(viz.fig.canvas, 'toolbar', None)
        if toolbar is not None and getattr(toolbar, 'mode', '') != '':
            return
        if event.xdata is None or event.ydata is None:
            return
        st["x0"], st["y0"] = event.xdata, event.ydata

    def _motion(event):
        if st["x0"] is None or event.inaxes != viz.ax:
            return
        if event.xdata is None or event.ydata is None:
            return
        x0, y0 = st["x0"], st["y0"]
        x1, y1 = event.xdata, event.ydata
        _clear_patch()
        color = "#0ea5e9" if x1 > x0 else "#f97316"   # blue = in, orange = out
        st["patch"] = patches.Rectangle(
            (min(x0, x1), min(y0, y1)), abs(x1 - x0), abs(y1 - y0),
            linewidth=1.5, edgecolor=color, facecolor=color,
            alpha=0.15, linestyle="--", zorder=1e6)
        viz.ax.add_patch(st["patch"])
        viz.fig.canvas.draw_idle()

    def _release(event):
        if st["x0"] is None:
            return
        x0, y0 = st["x0"], st["y0"]
        st["x0"] = st["y0"] = None
        _clear_patch()
        if event.inaxes == viz.ax and event.xdata is not None and event.ydata is not None:
            _apply_bbox_zoom(viz, x0, y0, event.xdata, event.ydata)
        else:
            viz.fig.canvas.draw_idle()

    viz.fig.canvas.mpl_connect('button_press_event',   _press)
    viz.fig.canvas.mpl_connect('motion_notify_event',  _motion)
    viz.fig.canvas.mpl_connect('button_release_event', _release)



def _hover_color(color, factor=0.82):
    """A clearly-visible hover/feedback shade (darkened) for a button.

    matplotlib's default Button.hovercolor ('0.95') is invisible on buttons whose
    resting color is already near-white, so hovering gives no feedback. Darkening
    the resting color gives a consistent, noticeable cue on every button.
    """
    try:
        import matplotlib.colors as mcolors
        r, g, b = mcolors.to_rgb(color)
        return (r * factor, g * factor, b * factor)
    except Exception:
        return color



def style_button(btn, color):
    """Set a Button's resting color, a derived hovercolor, and repaint now.

    Setting only ``btn.ax.set_facecolor(...)`` is fragile: Button repaints the
    axes to ``btn.color``/``btn.hovercolor`` on every mouse-motion event, so a
    directly-set facecolor is wiped on the next mouse move. Updating ``btn.color``
    makes the color persist and keeps hover feedback consistent.
    """
    btn.color = color
    btn.hovercolor = _hover_color(color)
    btn.ax.set_facecolor(color)


def _pan_axes(ax, fig, dx_frac, dy_frac):
    """Pan the view by a fraction of its current span (arrow-key panning).

    The viewport follows the arrow direction: right/up reveal content to the
    right/above.  A fixed fraction keeps the step size consistent at any zoom.
    """
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    dx = (x1 - x0) * dx_frac
    dy = (y1 - y0) * dy_frac
    ax.set_xlim(x0 + dx, x1 + dx)
    ax.set_ylim(y0 + dy, y1 + dy)
    fig.canvas.draw_idle()



# Fraction of the visible span to shift per arrow-key press.
_PAN_STEP = 0.15


# Values beyond this magnitude are the INT_MIN/2 or INT_MAX/2 sentinels
# that ConnTopology uses for "unconstrained" slide ranges.
_UNCONSTRAINED = 1_000_000_000



def collect_candidate_bundles(bundles):
    """Every candidate-bearing bundle once, in order, for the topology explorer.

    In the hier flow ``run_planner hier`` expands each cell into per-instance
    bundles that are routed **independently** — a different placement per
    occurrence yields a different selected topology, index, and coordinates — so
    they are NOT interchangeable replicas. Each is therefore listed separately,
    matching the main viz's bundle panel: selecting bundle N in the viz and
    hitting ``v`` opens *that* bundle, and the bundle count agrees. Only an
    accidental exact-id duplicate is removed.

    Returns ``(wrappers, cell_seen)`` where ``cell_seen`` maps each cell key to
    ``(representative_wrapper, instance_count)`` for optional annotation.
    """
    seen, wrappers = set(), []
    cell_seen = {}
    for w in bundles:
        if not w.input.candidates:
            continue
        b = w.input.original_bundle
        if b.id in seen:
            continue
        seen.add(b.id)
        wrappers.append(w)
        cell_key = (b.cell_context, b.reason) if b.cell_context else None
        if cell_key is not None:
            rep, cnt = cell_seen.get(cell_key, (w, 0))
            cell_seen[cell_key] = (rep, cnt + 1)
    # Order by bundle id so the explorer's "bundle i/N" index matches the main
    # viz bundle panel (which is sorted by id) — selecting the first listed
    # bundle opens it at position 1, not wherever it sat in self.bundles.
    wrappers.sort(key=lambda w: w.input.original_bundle.id)
    return wrappers, cell_seen


# Just for ref. No longer used.
def _get_nterms(topo):
    """Blocks this topology LANDS ON — face taps and bridges only.

    NOT the busterm count, and it has no callers: use `busterm_counts()`.
    `seg_busterms` records where segment ENDPOINTS tap a face, so this misses
    every PASS-THROUGH — a trunk crossing a block covers it without ending
    there, and that is a real connection the block contract carries.
    Measured on `flow/ariane133`: this returns 6 for bundle 154 where the
    contract has 17, and agrees with it on only 14 of 47 bundles.
    """
    names = set()
    # 1. Block connections stored during topology generation
    for eps in topo.seg_busterms.values():
        if eps[0] is not None: names.add(eps[0].block_name)
        if eps[1] is not None: names.add(eps[1].block_name)
    # 2. Bridge segments (for multi-rect TEG blocks)
    for bname in topo.bridge_segments.keys():
        names.add(bname)
    return len(names)


def snap_endpoint_extents(along_lo, along_hi, endpoint_conns, perp):
    """Display extents for a candidate's segments — draw.py's "Pass B".

    THE one implementation of this rule.  Three renderers must agree on it —
    matplotlib (`viz_explorer/draw.py`), the JS client
    (`src/web/static/index.html` `computeDisplay`) and the Scala client
    (`web/.../render/DisplayGeom.scala`) — because they draw the same topology
    and a divergence shows up as segments meeting on one screen and detached on
    another.  `test_web_displaygeom.py` pins the two ports against this
    function; it used to hold its own private copy, so a change to draw.py
    could (and did) diverge silently while the parity test stayed green.

    Rule: a SEG connection whose `at_pos` sits within 1 unit of the segment's
    `along_lo`/`along_hi` is incident to that endpoint; the endpoint is drawn at
    the connected partner's display perp so the two visually meet.  Connections
    that land mid-segment (a T-tap) are NOT snapped — snapping those stretched
    a segment toward a far partner (the overextension bug: up to +1260 units on
    the b44 demo).

    Several partners can be incident to ONE endpoint, because the match carries
    a +/-1 tolerance: a trunk whose outermost tap sits a unit from an interior
    tap collects both at the same end.  Take the EXTREME of them — min at the
    low end, max at the high end — so the drawn segment reaches every partner
    that meets it there.  Assigning per match instead let whichever came LAST
    win, and an interior partner could drag the end inside the outermost one,
    drawing the extreme stubs detached from a trunk they are connected to
    (issue #554).  With a single partner this is exactly that assignment, so
    the overwhelmingly common case is unchanged.

    Note this is NOT "extend-only": the extreme is taken over the incident
    PARTNERS, never against the segment's own nominal along value, so an
    endpoint whose partner moved inward still moves inward with it.

    Args:
        along_lo/along_hi: per-segment nominal along extents.
        endpoint_conns:    per-segment iterable of (partner_seg_idx, at_pos)
                           for its SEG connections.
        perp:              per-segment display perp (slide-interval centre).

    Returns:
        (lo, hi) lists of drawn along extents.
    """
    n = len(perp)
    lo = [float(v) for v in along_lo]
    hi = [float(v) for v in along_hi]
    for i in range(n):
        lo_adj, hi_adj = [], []
        for seg_idx, at_pos in endpoint_conns[i]:
            if not (0 <= seg_idx < n):
                continue
            if abs(at_pos - along_lo[i]) <= 1:
                lo_adj.append(perp[seg_idx])
            elif abs(at_pos - along_hi[i]) <= 1:
                hi_adj.append(perp[seg_idx])
        if lo_adj:
            lo[i] = min(lo_adj)
        if hi_adj:
            hi[i] = max(hi_adj)
    return lo, hi


def busterm_counts(wrapper):
    """(busterms, net endpoints) for a bundle — two different numbers.

    Both titles used to print `HBundle.num_terminals` under the label
    "bterms", and it is not a busterm count.  It is `1 + |receiver
    instances|` of the bundle's net — an endpoint count at the depth the
    NETLIST connects, while a busterm is what the ROUTE lands on, in the
    frame the bundle is planned in.  On a hierarchical design those are far
    apart: `flow/ariane133` bundle 154 reports 89, and the route touches 17
    blocks, because 88 leaf SRAM instances at depth 3 resolve onto 17
    `sram_block[N].data_sram` / `tag_sram` containers at depth 2.  Measured
    across that design, 10 of 47 bundles (21%) disagree.

    The busterm number comes from the SELECTED candidate's block contract,
    which is what the drawing shows and therefore what the title must agree
    with.  It matches the persisted `bundle_busterm` rows on 41 of 42
    bundles there; the one exception is a bundle whose second endpoint is an
    UNPLACED container, which `derive_busterms` skips and the topology still
    references — so the route is the more faithful source, not the table.

    Returns (None, num_terminals) when no candidate is selected: there is no
    route to count blocks on yet, and inventing one would be worse than
    saying so.
    """
    bundle = wrapper.input.original_bundle
    endpoints = bundle.num_terminals
    cands = wrapper.input.candidates
    sel = wrapper.plan.selected_topology_index
    if not cands or not (0 <= sel < len(cands)):
        return None, endpoints
    return len(set(cands[sel].connected_block_names)), endpoints
