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

_LAYER_COLOR = {1: '#000075', 2: '#a9a9a9', 3: '#FF8800', 4: '#007ACC', 5: '#CC0000', 6: '#00AA44', 7: '#8800CC', 8: '#F032E6', 9: '#42D4F4', 10: '#9A6324'}
# Default-orientation fallback only; the real H/V comes from the LayerStack via
# _layer_label() (def_layer can override these, e.g. M4 V instead of M4 H).
_LAYER_LABEL = {1: 'M1 V', 2: 'M2 H', 3: 'M3 V', 4: 'M4 H', 5: 'M5 V', 6: 'M6 H', 7: 'M7 V', 8: 'M8 H', 9: 'M9 V', 10: 'M10 H'}

# Pre-route band colours by TrackSlot type (draw_preroutes; the [Tracks] rail
# view keeps its own local copy because it also colours SIGNAL stripes).
_PREROUTE_COLOR = {'POWER': '#ffcccc', 'GROUND': '#cce0ff',
                   'CLOCK': '#fffacc', 'SHIELD': '#e0d4f7'}


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


def _draw_hanan_grid(ax, fp, ui_state: ViewState):
    """Draw the Hanan grid and return the line artists. Visibility is set by ui_state."""
    artists = []
    xs, ys = fp.get_hanan_grid()
    # Style: prominent dashed line for debugging
    color = '#94a3b8'  # slate-400
    for x in xs:
        l = ax.axvline(x=x, color=color, linestyle='--', linewidth=0.7, alpha=0.6, zorder=0)
        l.set_visible(ui_state.hanan_grid)
        artists.append(l)
    for y in ys:
        l = ax.axhline(y=y, color=color, linestyle='--', linewidth=0.7, alpha=0.6, zorder=0)
        l.set_visible(ui_state.hanan_grid)
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
    offset_trans = mtransforms.offset_copy(
        ax.transData, fig=ax.figure, x=4, y=4, units='points')
    txt = ax.text(bbox.x1, bbox.y1, name, transform=offset_trans,
                  ha='left', va='bottom',
                  fontsize=fontsize, fontweight='bold', color='#444444',
                  alpha=min(1.0, alpha * 4.0), # label slightly brighter than block
                  zorder=zorder + 1, clip_on=True)
    return added_patches, txt


def _toggle_fullscreen(fig):
    mgr = fig.canvas.manager
    if mgr:
        mgr.full_screen_toggle()

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

def raise_window(win_or_fig):
    """Bring a window or figure to the front and ensure it has keyboard focus."""
    win = None
    canvas = None
    if hasattr(win_or_fig, "canvas") and hasattr(win_or_fig.canvas, "manager"):
        canvas = win_or_fig.canvas
        mgr = canvas.manager
        if mgr is not None:
            win = getattr(mgr, "window", None)
            if win is None and hasattr(mgr, "canvas"):
                try:
                    # Fallback for some matplotlib backends (like TkAgg)
                    win = mgr.canvas.get_tk_widget().winfo_toplevel()
                except Exception:
                    pass

            # MacOSX backend exposes show() which calls makeKeyAndOrderFront: internally.
            if win is None and callable(getattr(mgr, "show", None)):
                try:
                    mgr.show()
                except Exception:
                    pass
    else:
        # Assume it's a direct window object (like tk.Tk)
        win = win_or_fig

    if win is not None:
        # macOS specific: force process to front using AppleScript.
        if sys.platform == "darwin":
            try:
                import subprocess
                pid = os.getpid()
                as_cmd = f'tell application "System Events" to set frontmost of every process whose unix id is {pid} to true'
                subprocess.run(["osascript", "-e", as_cmd], capture_output=True)
            except Exception:
                pass

        # Tkinter specific: force to front
        if hasattr(win, "lift"):
            try:
                win.lift()
                # Focus the window
                if hasattr(win, "focus_force"):
                    win.focus_force()
                
                # CRITICAL: Also focus the canvas widget specifically.
                # key_press_event often only fires when the canvas has focus.
                if canvas is not None and hasattr(canvas, "get_tk_widget"):
                    try:
                        cw = canvas.get_tk_widget()
                        cw.focus_force()
                        # Also schedule a delayed focus in case mapping is slow
                        if hasattr(win, "after"):
                            win.after(150, cw.focus_force)
                    except Exception:
                        pass
                elif hasattr(win, "after"):
                    # Fallback for non-matplotlib windows
                    win.after(150, win.focus_force)

                if hasattr(win, "attributes"):
                    win.attributes("-topmost", True)
                    # after_idle might not be available if not Tk
                    if hasattr(win, "after_idle"):
                        win.after_idle(win.attributes, "-topmost", False)
                    else:
                        win.attributes("-topmost", False)
            except Exception:
                pass

        # Other backends / fallback
        for method in ("raise_", "activateWindow"):
            if callable(getattr(win, method, None)):
                try:
                    getattr(win, method)()
                except Exception:
                    pass


def install_tk_geometry_resync(fig, settle_ms=80):
    """Keep the TkAgg canvas hit-testing aligned with the rendered layout.

    matplotlib's TkAgg ``resize`` re-places the figure image *centered for the
    size carried by each <Configure> event*::

        self._tkcanvas.create_image(int(width/2), int(height/2), image=...)

    During map / maximize / fullscreen transitions an intermediate or stale size
    can be used, leaving the image — and therefore mouse hit-testing — vertically
    offset from the widgets, so clicks miss buttons (especially noticeable on
    macOS) until a real resize occurs.

    This re-runs ``resize`` with the *settled* widget size after every geometry
    change (debounced) so the image re-centers correctly. It changes no window
    geometry, so it works in fullscreen too. No-op on non-Tk backends.
    """
    try:
        canvas = fig.canvas
        tkw = canvas.get_tk_widget()
        win = tkw.winfo_toplevel()
    except Exception:
        return  # not a Tk backend; nothing to do

    state = {"after_id": None}

    def _resync(prev=None, tries=0):
        state["after_id"] = None
        try:
            w, h = tkw.winfo_width(), tkw.winfo_height()
        except Exception:
            return
        if w > 1 and h > 1:
            try:
                ev = types.SimpleNamespace(width=w, height=h)
                canvas.resize(ev)
            except Exception:
                pass
        # An async maximize/fullscreen settles over several frames; keep
        # re-running resize until the widget size stops changing so the figure
        # image is finally centered for the *settled* size (cap the retries).
        if (w, h) != prev and tries < 12:
            try:
                state["after_id"] = win.after(
                    settle_ms, lambda: _resync((w, h), tries + 1))
            except Exception:
                pass

    def _schedule(_evt=None):
        if state["after_id"] is not None:
            try:
                win.after_cancel(state["after_id"])
            except Exception:
                pass
        try:
            state["after_id"] = win.after(settle_ms, _resync)
        except Exception:
            pass

    try:
        # React only to toplevel geometry changes (children also emit <Configure>).
        win.bind("<Configure>",
                 lambda e: _schedule() if e.widget is win else None, add="+")
        # <Map> covers the nested case where the window is shown already maximized.
        win.bind("<Map>", lambda e: _schedule() if e.widget is win else None, add="+")
    except Exception:
        pass
    _schedule()  # initial alignment once the current geometry settles


def extract_from_fullscreen_tab(fig, settle_ms=180, regain_ms=300):
    """Pop a window out of another window's macOS fullscreen tab group.

    On macOS, a window opened while another app window is fullscreen is placed
    *into that window's fullscreen Space* and merged into a tabbed window with a
    tab bar. Tk doesn't account for the tab bar, so mouse hit-testing is shifted
    and clicks miss the widgets — and (unlike a plain resize) only leaving the tab
    group fixes it. The reliable cure the user found is pressing 'f' twice
    (fullscreen off then on), which moves the window into its *own* Space.

    This replicates that: once the window has settled, if it is fullscreen with a
    tab bar above the canvas (``canvas.winfo_rooty() > 0``), toggle ``-fullscreen``
    off and back on. A standalone fullscreen window has rooty 0 and is left alone,
    so there is no flicker in the common case. No-op off macOS / non-Tk.
    """
    if sys.platform != "darwin":
        return
    try:
        canvas = fig.canvas
        tkw = canvas.get_tk_widget()
        win = tkw.winfo_toplevel()
    except Exception:
        return

    def _step(tries=0):
        try:
            if bool(win.attributes("-fullscreen")):
                if tkw.winfo_rooty() > 0:          # tab bar present → tabbed
                    win.attributes("-fullscreen", False)
                    win.after(regain_ms,
                              lambda: win.attributes("-fullscreen", True))
                    return
                return  # standalone fullscreen (rooty 0) — nothing to do
            if tries < 8:                          # not settled yet — retry
                win.after(settle_ms, lambda: _step(tries + 1))
        except Exception:
            pass

    try:
        win.after(settle_ms, _step)
    except Exception:
        pass


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


def set_icon(win_or_fig, icon_name="buda_icon.png"):
    """Set the application icon for the window or figure."""
    # 1. Resolve absolute path to the icon file
    try:
        _HERE = os.path.dirname(os.path.abspath(__file__))
        # Icon is in project root, one level up from src/
        icon_path = os.path.join(_HERE, "..", icon_name)
        if not os.path.exists(icon_path):
            # Fallback: maybe we are in a flattened structure or frozen?
            icon_path = os.path.join(_HERE, icon_name)
        
        if not os.path.exists(icon_path):
            return
    except Exception:
        return

    # 2. Extract the window object
    win = None
    target = win_or_fig
    if hasattr(win_or_fig, "canvas") and hasattr(win_or_fig.canvas, "manager"):
        mgr = win_or_fig.canvas.manager
        if mgr is not None:
            win = getattr(mgr, "window", None)
            if win is None and hasattr(mgr, "canvas"):
                # Fallback for some matplotlib backends
                try:
                    win = mgr.canvas.get_tk_widget().winfo_toplevel()
                except Exception:
                    pass
    else:
        win = win_or_fig

    if win is None:
        return

    # 3. Apply the icon using Tkinter
    try:
        import tkinter as tk
        
        # We need a reference to the root to call iconphoto properly
        root = win
        if hasattr(win, "winfo_toplevel"):
            root = win.winfo_toplevel()

        if hasattr(root, "iconphoto"):
            # Load the image. tk.PhotoImage supports PNG in Tk 8.6+
            # We store it on the target to prevent garbage collection.
            if not hasattr(target, "_icon_ref"):
                target._icon_ref = tk.PhotoImage(file=icon_path, master=root)
            
            # Set as default for all windows (important for macOS dock/switcher)
            root.iconphoto(True, target._icon_ref)
            
            # macOS specific: sometimes default=True isn't enough for the dock icon
            if sys.platform == "darwin":
                try:
                    root.tk.call('wm', 'iconphoto', root._w, "-default", target._icon_ref)
                except Exception:
                    pass
    except Exception:
        pass


def set_app_name(name, fig=None):
    """Best-effort relabel of the app from 'python3'/'Python' to `name` in OS
    chrome (macOS dock / menu bar, `ps`). Every hook is optional and fully
    guarded, so this is a no-op where the mechanism isn't available (e.g. a
    plain Linux run) and never raises.

    IMPORTANT (macOS): the process-name / CFBundleName writes (step 3) only
    take effect if they run BEFORE the first Tk window is realized — Tk's
    Cocoa port caches the name when it builds the application menu. Call this
    once at process startup (see buda_cli.main) for the dock / menu-bar /
    Cmd-Tab name; the per-figure call here still sets the Tk appname and
    window title and is otherwise a no-op."""
    if not name:
        return
    # 1. Process title — `ps`/`top` and some Linux docks. Optional dependency.
    try:
        import setproctitle
        setproctitle.setproctitle(name)
    except Exception:
        pass
    # 2. Tk application name — the TkAgg menu-bar title on macOS.
    if fig is not None:
        try:
            fig.canvas.manager.window.tk.call('tk', 'appname', name)
        except Exception:
            pass
    # 3. macOS process name — the bold app-menu / Dock / Cmd-Tab label for a
    #    Tk app comes from NSProcessInfo.processName (Tk's Cocoa port reads it
    #    when it builds the application menu), NOT from CFBundleName. Set it
    #    before the first Tk window is realized (see buda_cli.main).
    if sys.platform == "darwin":
        try:
            from Foundation import NSProcessInfo
            NSProcessInfo.processInfo().setProcessName_(name)
        except Exception:
            pass
        # 3b. CFBundleName too — belt-and-suspenders for the MacOSX/Cocoa
        #     backend and the Dock tile; harmless if the dict is immutable.
        try:
            from Foundation import NSBundle
            info = (NSBundle.mainBundle().localizedInfoDictionary()
                    or NSBundle.mainBundle().infoDictionary())
            if info is not None:
                info["CFBundleName"] = name
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
    # The arrow keys drive BUDA's own pan; drop matplotlib's defaults (back =
    # 'left', forward = 'right') so they don't also fire and undo the pan.
    for key in ('keymap.back', 'keymap.forward'):
        try:
            vals = plt.rcParams.get(key, [])
            for v in ('left', 'right', 'up', 'down'):
                if v in vals:
                    vals.remove(v)
        except Exception:
            pass

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


class TopologyExplorer:
    """Cycle through topology candidates across one or more bundles.

    Navigation:
      ← / →  (or ◀/▶ Topo buttons)    — prev / next topology within bundle
      cmd-p / cmd-n                    — prev / next topology within bundle
      [ / ]  (or ◀/▶ Bundle buttons)  — prev / next bundle
      s (or Select button)             — toggle selection (pin/unpin)
      cmd-1                            — raise the main BUDA viz window
      z / Z                            — zoom in / out
      cmd-z / ctrl-z                   — zoom to active bundle extent
      h / H / cmd-a / ctrl-a           — reset zoom to home full view
    """

    def __init__(self, fp, wrappers, sidecar_path=None, main_fig=None,
                 rerun_fn=None, refresh_fn=None, layer_stack=None,
                 ui_state: ViewState = None, start_bidx=0, layer_visible=None):
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
        # two-step connect/disconnect pair.
        self._edit_topo    = None
        self._edit_pending = -1
        self._edit_msg     = ""

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

    # ── TopoEdit mode (Phase E3b) ─────────────────────────────────────
    def _edit_default_layers(self):
        h = v_ = -1
        if self.layer_stack is not None:
            h  = self.layer_stack.get_top_layer(ic.LayerDir.HORIZONTAL)
            v_ = self.layer_stack.get_top_layer(ic.LayerDir.VERTICAL)
        return (h if h != -1 else 4), (v_ if v_ != -1 else 5)

    def _edit_open(self, empty):
        if self._edit_topo is not None:
            self._edit_msg = "edit session already open — enter commits, esc aborts"
        elif empty:
            self._edit_topo = ic.Topology()
            self._edit_topo.type = "USER"
            self._edit_msg = "EDIT: empty topology — T/Y add a trunk at the cursor"
        else:
            if not (0 <= self.idx < len(self.topos)):
                return
            # candidates[] elements alias pool storage; deep-copy explicitly.
            self._edit_topo = ic.offset_topology(self.topos[self.idx], 0, 0)
            self._edit_msg = f"EDIT: copy of topo {self.idx + 1} ({self._edit_topo.type})"
        self._edit_pending = -1
        self._draw()

    def _edit_close(self, msg):
        self._edit_topo    = None
        self._edit_pending = -1
        self._edit_msg     = msg
        self._draw()

    def _edit_apply(self, verdict):
        """Render one op's verdict into the edit banner (and the console)."""
        if not verdict.applied:
            self._edit_msg = f"EDIT rejected: {verdict.note}"
        else:
            kinds = {}
            for viol in verdict.conn.violations:
                k = str(viol.kind).split('.')[-1]
                kinds[k] = kinds.get(k, 0) + 1
            issues = ", ".join(f"{k}x{n}" for k, n in sorted(kinds.items())) or "none"
            state = "clean" if verdict.ok() else \
                f"violations: {issues}; comps={verdict.components}" \
                + ("; PINCHED" if verdict.pinched else "")
            self._edit_msg = f"EDIT: {verdict.note} — {state}"
        print(f"[edit] {self._edit_msg}")
        self._draw()

    @staticmethod
    def _snap(val, coords):
        """Snap a cursor coordinate to the nearest Hanan line (or round)."""
        iv = int(round(val))
        return min(coords, key=lambda c: abs(c - iv)) if coords else iv

    def _block_at(self, x, y):
        for name, r in self.fp.get_all_blocks():
            if r.x1 <= x <= r.x2 and r.y1 <= y <= r.y2:
                return name
        return None

    def _edit_add_trunk_at(self, event, horiz):
        if event.xdata is None or event.ydata is None:
            self._edit_msg = "EDIT: put the cursor on the canvas first"
            self._draw(); return
        xs, ys = self.fp.get_hanan_grid()
        perp = self._snap(event.ydata if horiz else event.xdata,
                          ys if horiz else xs)
        h, v_ = self._edit_default_layers()
        self._edit_apply(ic.edit_add_trunk(
            self._edit_topo, self.fp, horiz, perp, 1, 0,   # lo>hi = full span
            h if horiz else v_))

    def _edit_add_stub_at(self, event):
        if event.xdata is None or event.ydata is None:
            self._edit_msg = "EDIT: put the cursor over a block"
            self._draw(); return
        block = self._block_at(event.xdata, event.ydata)
        if block is None:
            self._edit_msg = "EDIT: no block under the cursor"
            self._draw(); return
        if not (0 <= self.sidx < len(self._edit_topo.segments)):
            self._edit_msg = "EDIT: select the target segment first (j/k)"
            self._draw(); return
        tgt = self._edit_topo.segments[self.sidx]
        h, v_ = self._edit_default_layers()
        layer = v_ if tgt.start.y == tgt.end.y else h    # stub ⟂ target
        self._edit_apply(ic.edit_add_stub(
            self._edit_topo, self.fp, block, self.sidx, layer))

    def _edit_pair_op(self, event, connect):
        """Two-step connect/disconnect: first press marks the current segment,
        second press pairs it with the (newly j/k-selected) current one."""
        if not (0 <= self.sidx < len(self._edit_topo.segments)):
            self._edit_msg = "EDIT: select a segment first (j/k)"
            self._draw(); return
        if self._edit_pending < 0:
            self._edit_pending = self.sidx
            self._edit_msg = (f"EDIT: seg {self.sidx} marked — select the "
                              f"partner (j/k) and press the key again")
            self._draw(); return
        i, j = self._edit_pending, self.sidx
        self._edit_pending = -1
        if i == j:
            self._edit_msg = "EDIT: same segment twice — pair cancelled"
            self._draw(); return
        if connect:
            self._edit_apply(ic.edit_connect(self._edit_topo, self.fp, i, j))
        else:
            si = self._edit_topo.segments[i]
            horiz = si.start.y == si.end.y
            coord = event.xdata if horiz else event.ydata
            if coord is None:
                self._edit_msg = "EDIT: cursor sets the retract position"
                self._draw(); return
            self._edit_apply(ic.edit_disconnect(
                self._edit_topo, self.fp, i, j, int(round(coord))))

    def _edit_commit(self):
        topo = self._edit_topo
        if topo is None or not topo.segments:
            self._edit_close("EDIT: nothing to commit")
            return
        topo.type = "USER"
        topo.estimated_wirelength = (
            sum(abs(s.end.x - s.start.x) + abs(s.end.y - s.start.y)
                for s in topo.segments)
            + sum(abs(s.end.x - s.start.x) + abs(s.end.y - s.start.y)
                  for s in topo.bridge_segments.values()))
        w = self.wrapper
        uid = ic.topo_uid(topo)
        pool = list(w.input.candidates)
        existing = next((k for k, c in enumerate(pool)
                         if ic.topo_uid(c) == uid), None)
        if existing is not None:
            idx = existing
            note = f"identical candidate already at topo {idx + 1}"
        else:
            pool.append(topo)
            idx = len(pool) - 1
            w.input.candidates = pool
            note = f"committed as topo {idx + 1} (USER)"
        self._edit_topo    = None
        self._edit_pending = -1
        self.idx  = idx
        self.sidx = -1
        self._select_current()          # pin + sidecar (uid-carrying) + redraw
        self._edit_msg = f"EDIT: {note}, pinned"
        print(f"[edit] {self._edit_msg}")
        self._draw()

    # ------------------------------------------------------------------

    def _build_conn_topo(self, topo):
        ct = ic.ConnTopology()
        ct.build(topo, self.fp)
        return ct

    # The dogleg pass pins per-segment net_pull and slide windows on the plan
    # (ConnTopology would recompute them wrongly on the split topology).  Those
    # overrides are indexed by the SELECTED topology's segments, so honor them
    # only when the explorer is showing that topology — then the view matches
    # what NUTS actually used.
    def _show_overrides(self):
        # Only the SELECTED topology, and only while an override array still
        # matches that topology's segment count — a later run_planner can replace
        # the selected topology without refreshing these, so a size mismatch means
        # the overrides are stale and must be ignored (as build_nuts_maps does).
        return self.idx == self.wrapper.plan.selected_topology_index

    def _n_segs(self):
        return len(self.topos[self.idx].segments)

    def _seg_net_pull(self, cs, ci):
        snp = getattr(self.wrapper.plan, 'seg_net_pull', None)
        if self._show_overrides() and snp and len(snp) == self._n_segs():
            if snp[ci] != -2147483648:
                return snp[ci]
        return cs.net_pull

    def _seg_slide(self, cs, ci):
        slo = getattr(self.wrapper.plan, 'seg_slide_lo', None)
        shi = getattr(self.wrapper.plan, 'seg_slide_hi', None)
        if (self._show_overrides() and slo and shi and len(slo) == self._n_segs()
                and len(shi) == self._n_segs() and not math.isnan(slo[ci])):
            return slo[ci], shi[ci]
        return cs.perp_lo, cs.perp_hi

    def _is_dogleg_seg(self, ci):
        # A dogleg piece/jog carries a pinned slide window; such a segment must
        # display at its NOMINAL position (the two pieces share a slide range, so
        # the range-centre display would collapse them and hide the dogleg step).
        slo = getattr(self.wrapper.plan, 'seg_slide_lo', None)
        return bool(self._show_overrides() and slo and len(slo) == self._n_segs()
                    and not math.isnan(slo[ci]))

    def _draw_busterm_markers(self, topo, ct, viz_lw):
        """Draw a diamond at every busterm connection point.

        Gated on the shared ui_state.busterms (the "Terminals" toggle) so the
        setting carries over from the main BUDA viz, like Blocks and Hanan.
        """
        if not self.ui_state.busterms:
            return
        ax = self.ax
        msz = viz_lw * 1.1 + 3

        for ci, (raw_seg, cs) in enumerate(zip(topo.segments, ct.segs())):
            if ci in getattr(self, '_hidden_seg', ()):   # layer hidden in main viz
                continue
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

        cs_list = list(ct.segs())
        cs_map  = {j: cs_list[j] for j in range(len(cs_list))}

        for ci, (raw_seg, cs) in enumerate(zip(topo.segments, cs_list)):
            if ci in getattr(self, '_hidden_seg', ()):   # layer hidden in main viz
                continue
            col = _LAYER_COLOR.get(raw_seg.layer_hint, '#888888')
            slide_lo, slide_hi = self._seg_slide(cs, ci)   # NUTS override if any

            # Extend the along range to cover the perp intervals of perpendicular
            # stubs connected at our endpoints.  A trunk's band should span the
            # full distance the trunk may need to reach, not just its nominal span.
            ext_lo, ext_hi = cs.along_lo, cs.along_hi
            for conn in cs.conns:
                if conn.kind != ic.SegConnKind.SEG or not conn.is_endpoint:
                    continue
                other = cs_map.get(conn.seg_idx)
                if other is None or other.horiz == cs.horiz:
                    continue  # same-direction connection — skip
                if abs(other.perp_lo) < _UNCONSTRAINED:
                    ext_lo = min(ext_lo, other.perp_lo)
                if abs(other.perp_hi) < _UNCONSTRAINED:
                    ext_hi = max(ext_hi, other.perp_hi)

            if cs.horiz:
                band_y0 = clamp_y(slide_lo)
                band_y1 = clamp_y(slide_hi)
                if band_y0 >= band_y1:
                    continue
                ax.add_patch(patches.Rectangle(
                    (ext_lo, band_y0), ext_hi - ext_lo, band_y1 - band_y0,
                    linewidth=0, facecolor=col, alpha=0.10, zorder=3))
                for y_b, label in ((slide_lo, 'lo'), (slide_hi, 'hi')):
                    if abs(y_b) < _UNCONSTRAINED:
                        ax.plot([ext_lo, ext_hi], [y_b, y_b],
                                color=col, linewidth=0.9, linestyle=':', alpha=0.7, zorder=4)
                        ax.text((ext_lo + ext_hi) / 2, y_b, f' {y_b:.0f}',
                                fontsize=6, color=col, va='bottom' if label == 'lo' else 'top',
                                ha='center', zorder=5, alpha=0.85)
            else:
                band_x0 = clamp_x(slide_lo)
                band_x1 = clamp_x(slide_hi)
                if band_x0 >= band_x1:
                    continue
                ax.add_patch(patches.Rectangle(
                    (band_x0, ext_lo), band_x1 - band_x0, ext_hi - ext_lo,
                    linewidth=0, facecolor=col, alpha=0.10, zorder=3))
                for x_b, label in ((slide_lo, 'lo'), (slide_hi, 'hi')):
                    if abs(x_b) < _UNCONSTRAINED:
                        ax.plot([x_b, x_b], [ext_lo, ext_hi],
                                color=col, linewidth=0.9, linestyle=':', alpha=0.7, zorder=4)
                        ax.text(x_b, (ext_lo + ext_hi) / 2, f' {x_b:.0f}',
                                fontsize=6, color=col, va='center',
                                ha='left' if label == 'lo' else 'right', zorder=5, alpha=0.85)

    # ------------------------------------------------------------------
    # Selection DB helpers
    # ------------------------------------------------------------------

    def _bundle_hint(self, wrapper=None):
        w = wrapper or self.wrapper
        names = w.input.original_bundle.get_net_names()
        return names[0] if names else f"bundle_{w.input.original_bundle.id}"

    def _find_selection(self, wrapper=None):
        """Return the saved selection dict for the given wrapper, or None.

        Tries the current bundle_hint first (first net name); falls back to
        matching by bundle_id so that sidecars created with older hint
        conventions (e.g. 't0_b0' instead of 't0_b0_00') still work.
        """
        w   = wrapper or self.wrapper
        sel = self._selections.get(self._bundle_hint(w))
        if sel is None:
            bid = w.input.original_bundle.id
            sel = next((s for s in self._selections.values()
                        if s.get('bundle_id') == bid), None)
        return sel

    def _selected_topo_index(self):
        """The single topology index pinned for the current bundle, or -1.

        A bundle has at most ONE pinned topology.  Resolution order:
          1. The live pin (script `select_topology` or an applied sidecar,
             reflected in plan.selected_topology_index + topology_pinned) — this
             is authoritative after run_planner and honours script-vs-sidecar
             precedence.
          2. Otherwise a sidecar entry for this bundle (used before run_planner,
             e.g. right after generate_topologies): its saved index, else the
             first candidate matching the saved (type, wirelength).

        Previously each of these criteria was OR'd inside _current_is_selected,
        so a stale sidecar could light up several different topologies of one
        bundle as "pinned" at once.
        """
        w = self.wrapper
        if getattr(w.input, 'topology_pinned', False):
            idx = w.plan.selected_topology_index
            return idx if 0 <= idx < len(self.topos) else -1

        sel = self._find_selection()
        if sel is not None:
            uid = sel.get('topo_uid')   # stable identity first (E1b)
            if uid:
                for i, topo in enumerate(self.topos):
                    if ic.topo_uid(topo) == uid:
                        return i
            hint = sel.get('topo_index_hint', -1)
            if 0 <= hint < len(self.topos):
                return hint
            for i, topo in enumerate(self.topos):
                if (topo.type == sel['topo_type'] and
                        topo.estimated_wirelength == sel['topo_wl']):
                    return i
        return -1

    def _focus_topo_index(self):
        """Which topology to display when opening or switching to the current
        bundle: its pinned topology (live pin or sidecar) if any, else the
        planner's choice, else topo 0.  Used by __init__, bundle cycling, and
        jump-to-bundle so all three focus the bundle's pin consistently."""
        idx = self._selected_topo_index()          # pinned (live or sidecar) or -1
        if idx >= 0:
            return idx
        sel = self.wrapper.plan.selected_topology_index
        if 0 <= sel < len(self.topos):              # planner's choice (unpinned)
            return sel
        return 0

    def _current_is_selected(self):
        return self.idx == self._selected_topo_index()

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
            'bundle_id':       wrapper.input.original_bundle.id,
            'topo_type':       topo.type,
            'topo_wl':         topo.estimated_wirelength,
            'topo_uid':        ic.topo_uid(topo),   # stable identity (E1b)
            'topo_index_hint': self.idx,
            'note':            '',
            'selected_at':     datetime.now().isoformat(timespec='seconds'),
        }
        
        pinned = list(wrapper.input.pinned_seg_layers)
        if len(pinned) == len(topo.segments):
            if any(lid != -1 for lid in pinned):
                sel['seg_layers'] = pinned
        
        # Update live object
        if wrapper.plan.selected_topology_index != self.idx:
            wrapper.plan.seg_layers = [] # Clear stale results for different topology
        
        wrapper.plan.selected_topology_index = self.idx
        wrapper.input.topology_pinned = True
        
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
            wrapper.input.topology_pinned = False
            wrapper.input.pinned_seg_layers = []

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
                if 'topo_uid' in entry:   # stable identity (E1b; older sidecars lack it)
                    self._selections[entry['bundle_hint']]['topo_uid'] = entry['topo_uid']
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
        self.idx  = self._focus_topo_index()   # jump to this bundle's pinned topo
        self.sidx = -1
        self._autoscale_needed = True
        self._reset_rerun_btn()
        self._draw()

    def show_bundle_index(self, idx):
        """Jump to a specific bundle (used when the explorer is already open and
        the parent viz wants to focus a different highlighted bundle)."""
        if 0 <= idx < len(self.wrappers) and idx != self.bidx:
            self.bidx = idx
            self.idx  = self._focus_topo_index()   # jump to its pinned topo
            self.sidx = -1
            self._autoscale_needed = True
            self._reset_rerun_btn()
            self._draw()

    def _redraw_topo(self):
        # We trigger a fig_redraw so the UI state updates correctly.
        self.fig_redraw()

    def _on_key(self, event):
        # ── TopoEdit mode (Phase E3b): e/E open a session (copy/empty); while
        # open, T/Y add an H/V trunk at the cursor's Hanan line, S stubs the
        # block under the cursor to the selected segment, C/D pair-connect/
        # -disconnect, X removes, enter commits (+pin), escape aborts.
        # Candidate/bundle navigation is parked while editing.
        if event.key == 'e':                    self._edit_open(empty=False); return
        if event.key == 'E':                    self._edit_open(empty=True); return
        if self._edit_topo is not None:
            if event.key == 'T':                self._edit_add_trunk_at(event, horiz=True); return
            if event.key == 'Y':                self._edit_add_trunk_at(event, horiz=False); return
            if event.key == 'S':                self._edit_add_stub_at(event); return
            if event.key == 'C':                self._edit_pair_op(event, connect=True); return
            if event.key == 'D':                self._edit_pair_op(event, connect=False); return
            if event.key == 'X':
                if 0 <= self.sidx < len(self._edit_topo.segments):
                    si = self.sidx
                    self.sidx = -1
                    self._edit_apply(ic.edit_remove_segment(
                        self._edit_topo, self.fp, si))
                else:
                    self._edit_msg = "EDIT: select a segment first (j/k)"
                    self._draw()
                return
            if event.key == 'enter':            self._edit_commit(); return
            if event.key == 'escape':           self._edit_close("EDIT: aborted"); return
            if event.key in ('a', 'd', 'n', 'p', '[', ']', 'pageup', 'pagedown',
                             'cmd+n', 'ctrl+n', 'cmd+p', 'ctrl+p'):
                self._edit_msg = "EDIT: finish the session first (enter/esc)"
                self._draw(); return
        if event.key in ('cmd+q', 'ctrl+q'):    plt.close('all'); return
        if event.key in ('f', 'cmd+f', 'ctrl+f'): _toggle_fullscreen(self.fig); return
        if event.key in ('cmd+1', 'ctrl+1'):
            if self._main_fig is not None: raise_window(self._main_fig)
            return
        if event.key in ('cmd+z', 'ctrl+z'):    self._zoom_to_bundle(); return
        if event.key == 'z':                    self._interactive_zoom(event, zoom_in=True); return
        if event.key == 'Z':                    self._interactive_zoom(event, zoom_in=False); return
        if event.key in ('h', 'H', 'cmd+a', 'ctrl+a'): self._zoom_home(); return
        # Arrow keys pan the view (see below); topo/segment nav uses the letter
        # aliases a/d (prev/next topology) and k/j (prev/next segment).
        if event.key == 'left':   _pan_axes(self.ax, self.fig, -_PAN_STEP, 0); return
        if event.key == 'right':  _pan_axes(self.ax, self.fig, +_PAN_STEP, 0); return
        if event.key == 'up':     _pan_axes(self.ax, self.fig, 0, +_PAN_STEP); return
        if event.key == 'down':   _pan_axes(self.ax, self.fig, 0, -_PAN_STEP); return
        if event.key == 'a':                    self._step_topo(-1)
        if event.key == 'd':                    self._step_topo(+1)
        if event.key in ('n', 'cmd+n', 'ctrl+n'):    self._step_topo(+1)
        if event.key in ('p', 'cmd+p', 'ctrl+p'):    self._step_topo(-1)
        if event.key in ('[', 'pageup'):        self._step_bundle(-1)
        if event.key in (']', 'pagedown'):      self._step_bundle(+1)
        if event.key == 'k':                    self._step_segment(-1)
        if event.key == 'j':                    self._step_segment(+1)
        # Layer up/down use the symbol keys only.  The letter aliases were
        # dropped: 'd' was double-bound (next-topology AND layer-down), and 'u'
        # was its orphaned layer-up partner.
        if event.key in ('+', '='):             self._cycle_layer(+1)
        if event.key in ('-', '_'):             self._cycle_layer(-1)
        if event.key == 'b':                    self.ui_state.toggle_blocks()
        if event.key == 'g':                    self.ui_state.toggle_hanan_grid()
        if event.key == 't':                    self.ui_state.toggle_busterms()
        if event.key == 's':
            if self._current_is_selected(): self._deselect_current()
            else:                           self._select_current()
        if event.key == 'x':                    self._deselect_current()
        if event.key == 'r':                    self._rerun_and_refresh()

    def _step_segment(self, delta):
        topo = self._shown_topo()
        n = len(topo.segments)
        if n == 0: return
        self.sidx = (self.sidx + delta) % n
        self._draw()

    def _cycle_layer(self, delta):
        if self.sidx == -1 or self.layer_stack is None:
            return
        wrapper = self.wrappers[self.bidx]
        topo = wrapper.input.candidates[self.idx]
        seg = topo.segments[self.sidx]
        is_h = (seg.start.y == seg.end.y)

        # Get compatible layers based on segment orientation.
        dir = ic.LayerDir.HORIZONTAL if is_h else ic.LayerDir.VERTICAL
        lids = list(self.layer_stack.get_layer_ids_by_dir(dir))
        if not lids: return

        # Resolve current layer ID using same precedence as _draw.
        sel = self._find_selection()
        is_current_selection = self._current_is_selected()
        is_planner_active = (self.idx == wrapper.plan.selected_topology_index)
        
        curr = -1
        if is_current_selection and sel and 'seg_layers' in sel:
            pinned = sel['seg_layers']
            if len(pinned) == len(topo.segments):
                curr = pinned[self.sidx]
        
        if curr == -1 and is_planner_active:
            if len(wrapper.plan.seg_layers) == len(topo.segments):
                curr = wrapper.plan.seg_layers[self.sidx]
        
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
        pinned_list = list(wrapper.input.pinned_seg_layers)
        if len(pinned_list) != len(topo.segments):
            # Start from current actual layers.
            pinned_list = []
            for i, s in enumerate(topo.segments):
                l = -1
                if is_planner_active and len(wrapper.plan.seg_layers) == len(topo.segments):
                    l = wrapper.plan.seg_layers[i]
                else:
                    l = s.layer_hint
                pinned_list.append(l)

        pinned_list[self.sidx] = new_lid
        wrapper.input.pinned_seg_layers = pinned_list

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

    def fig_redraw(self):
        self._draw()

    def _draw(self):
        ax = self.ax
        if not getattr(self, '_autoscale_needed', True):
            curr_xlim = ax.get_xlim()
            curr_ylim = ax.get_ylim()
        else:
            curr_xlim = None
            curr_ylim = None
        ax.clear()
        n   = len(self.topos)
        nb  = len(self.wrappers)
        bid = self.wrapper.input.original_bundle.id
        _w  = self.wrappers[self.bidx]
        if hasattr(_w, 'path') and _w.path:
            bus_label = f"/{_w.path} "
        elif nb > 1:
            bus_label = f"bundle {self.bidx + 1}/{nb} · "
        else:
            bus_label = ""

        topo = self._shown_topo()
        wl   = topo.estimated_wirelength

        # Use centralized selection check
        sel = self._find_selection()
        is_current_selection = self._current_is_selected()
        has_any_sel = (sel is not None
                       or getattr(self.wrapper.input, 'topology_pinned', False))

        # Planner is active only if the planner actually ran (it assigns
        # per-segment layers) and chose this topo. select_topology(ies) also sets
        # selected_topology_index, so that alone would mislabel a script pin as a
        # planner choice when run_planner was never called.
        is_planner_active = (self.wrapper.plan is not None and
                             self.wrapper.plan.selected_topology_index == self.idx and
                             len(self.wrapper.plan.seg_layers) > 0)

        if is_planner_active and is_current_selection:
            sel_badge = "  ★ PINNED & PLANNER SELECTED"
        elif is_planner_active:
            sel_badge = "  ★ PLANNER SELECTED"
        elif is_current_selection:
            sel_badge = "  ★ PINNED"
        else:
            sel_badge = ""

        # Tuning buttons are only visible when the current topo is selected/pinned.
        # This allows per-segment layer overrides to be persisted.
        if hasattr(self, '_bax2'):
            for bax in self._bax2:
                bax.set_visible(is_current_selection)

        # ── Update selection button states ──
        # Set via style_button (btn.color), not ax.set_facecolor alone, so the
        # state color survives Button's motion-driven repaints.
        if is_current_selection:
            self._btn_select.label.set_text('★  Pinned')
            style_button(self._btn_select, '#aadd88')
        elif is_planner_active:
            self._btn_select.label.set_text('★  Pin Planner Choice')
            style_button(self._btn_select, '#cceeff')
        else:
            self._btn_select.label.set_text('★  Pin Topo')
            style_button(self._btn_select, '#f0f0f0')
        style_button(self._btn_deselect, '#ffbbaa' if has_any_sel else '#f0f0f0')

        # ── Axes border: gold for pinned, blue for planner, subtle grey otherwise ──
        if is_planner_active and is_current_selection:
            border_col, border_lw = '#BDB76B', 3.5  # Dark Khaki
        elif is_planner_active:
            border_col, border_lw = '#4682B4', 3.5  # Steel Blue
        elif is_current_selection:
            border_col, border_lw = '#FFD700', 3.5  # Gold
        else:
            border_col, border_lw = '#cccccc', 0.8
        for spine in ax.spines.values():
            spine.set_edgecolor(border_col)
            spine.set_linewidth(border_lw)

        if self._edit_topo is not None or self._edit_msg:
            ax.text(0.01, 1.01,
                    self._edit_msg or "EDIT",
                    transform=ax.transAxes, fontsize=9, color='#b03030',
                    va='bottom', ha='left', clip_on=False)
            if self._edit_topo is None:
                self._edit_msg = ""     # one-shot after the session closes

        ct = self._build_conn_topo(topo)
        cs_list = list(ct.segs())

        # Determine display geometry for segments — width proportional to bundle width
        viz_lw = min(3.0 + math.log2(1 + self.wrapper.input.width) * 1.5, 14.0)
        actual_lids = []

        # ── Pre-compute display geometry for all segments ──────────────────
        # Minimum pull-arrow length in data units (prevents invisible arrows on
        # tiny intervals and provides a direction indicator for one-side-constrained
        # segments where there is no finite interval width to derive from).
        _MIN_ARROW = 20

        # Pass A: centered perp position + pull-arrow length per segment.
        #
        # net_pull (from ConnTopology / NUTS override) is computed in C++:
        #   > 0  more connected-stub anchors lie above perp_pos → slide up/right
        #   < 0  more anchors lie below perp_pos                → slide down/left
        #   = 0  balanced or no stub connections                → no preferred direction
        #
        # Display at interval centre when both bounds are finite; at perp_pos otherwise.
        _draw_perp = []
        _pull_len  = []
        for ci, cs in enumerate(cs_list):
            lo, hi  = self._seg_slide(cs, ci)      # NUTS slide override if any
            net_pull = self._seg_net_pull(cs, ci)  # NUTS net_pull override if any
            lo_ok   = abs(lo) < _UNCONSTRAINED
            hi_ok   = abs(hi) < _UNCONSTRAINED
            if self._is_dogleg_seg(ci):
                # Show a dogleg piece/jog at its nominal position so the step is
                # visible (its two pieces share a slide range).
                raw = topo.segments[ci]
                dp  = float(raw.start.y if cs.horiz else raw.start.x)
            else:
                dp  = (lo + hi) / 2.0 if (lo_ok and hi_ok) else float(cs.perp_pos)

            if net_pull != 0:
                interval_w = (hi - lo) if (lo_ok and hi_ok) else 0.0
                plen = max(interval_w / 4.0, float(_MIN_ARROW)) * (1.0 if net_pull > 0 else -1.0)
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
                adj_idx = conn.seg_idx
                if not (0 <= adj_idx < len(cs_list)):
                    continue
                adj = _draw_perp[adj_idx]
                if abs(conn.at_pos - cs.along_lo) <= 1:
                    _draw_lo[i] = adj
                elif abs(conn.at_pos - cs.along_hi) <= 1:
                    _draw_hi[i] = adj
        # ──────────────────────────────────────────────────────────────────

        # Resolve each segment's layer once (pinned → planned → hint) and note
        # which are hidden by the main viz's layer toggles, so EVERY drawing
        # pass below — segments, slide spans, busterm markers — skips the same
        # set (a layer turned off in the main viz shows no artifacts here).
        def _resolved_lid(i):
            if is_current_selection and sel and 'seg_layers' in sel:
                pinned = sel['seg_layers']
                if len(pinned) == len(topo.segments):
                    return pinned[i]
            if (is_planner_active
                    and len(self.wrapper.plan.seg_layers) == len(topo.segments)):
                return self.wrapper.plan.seg_layers[i]
            return topo.segments[i].layer_hint

        self._hidden_seg = {
            i for i in range(len(topo.segments))
            if self._layer_visible is not None
            and not self._layer_visible.get(_resolved_lid(i), True)
        }

        for i, seg in enumerate(topo.segments):
            lid = _resolved_lid(i)

            # Layer hidden in the main viz → skip it (and its legend entry).
            if i in self._hidden_seg:
                continue
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
            if is_current_selection and self.sidx != -1:
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
        
        nterms = self.wrapper.input.original_bundle.num_terminals
        title_main = (
            f"{bus_label}B{bid} ({nterms} terms/{len(topo.segments)} segs) · topo {self.idx + 1}/{n} "
            f"· {topo.type} · WL={wl} · [{layer_summary}]{sel_badge}"
        )
        
        title_color = 'black'
        if is_planner_active and is_current_selection:
            title_color = '#666600'  # mixture
        elif is_current_selection:
            title_color = '#886600'  # gold
        elif is_planner_active:
            title_color = '#005588'  # blue

        ax.set_title(title_main, fontsize=12, pad=10, color=title_color)

        if self.fig.canvas.manager:
            bid_  = self.wrapper.input.original_bundle.id
            names_ = self.wrapper.input.original_bundle.get_net_names()
            net0  = names_[0] if names_ else f"B{bid_}"
            self.fig.canvas.manager.set_window_title(f"{net0} (Bundle {bid_})")

        # Identify blocks that this topology must connect (endpoints + passthru)
        highlight_blocks = set(topo.connected_block_names)

        # Floorplan blocks
        self._block_patch_artists, self._block_name_artists = _draw_blocks(
            ax, self.fp, self.ui_state, highlight_blocks)

        # Hanan grid
        _draw_hanan_grid(ax, self.fp, self.ui_state)


        # Slide-range bands (drawn before segments so segments sit on top)
        self._draw_slide_spans(topo, ct)

        # Busterm diamonds (on top of segments and junction dots)
        self._draw_busterm_markers(topo, ct, viz_lw)

        # Legend
        from matplotlib.lines import Line2D
        used_layers = sorted(set(actual_lids))
        handles = [Line2D([0], [0], color=_LAYER_COLOR.get(l, '#888'), lw=3,
                          label=_layer_label(l, self.layer_stack))
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
        if curr_xlim is not None:
            ax.set_xlim(curr_xlim)
            ax.set_ylim(curr_ylim)
        else:
            # Fit to the data bbox, then expand the shorter axis so the view
            # fills the whole window (same maximal framing as cmd-z), instead of
            # collapsing to a thin sliver under set_aspect('equal').
            ax.autoscale_view()
            x0, x1 = ax.get_xlim()
            y0, y1 = ax.get_ylim()
            self._home_data_bbox = (x0, x1, y0, y1)
            _set_lims_filling_box(ax, x0, x1, y0, y1)
            self._home_xlim = ax.get_xlim()
            self._home_ylim = ax.get_ylim()
            self._autoscale_needed = False

        self.fig.canvas.draw_idle()

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

    def _zoom_to_bundle(self, _=None):
        """Zoom axes to the bounding box of the active bundle's terminals/topology."""
        topo = self.topos[self.idx]
        ct = self._build_conn_topo(topo)
        xs, ys = [], []
        # Add topology endpoints
        for seg in topo.segments:
            xs.extend([float(seg.start.x), float(seg.end.x)])
            ys.extend([float(seg.start.y), float(seg.end.y)])
        # Add busterm connection positions
        for cs in ct.segs():
            display_perp = self._centered_perp(cs)
            for conn in cs.conns:
                if conn.kind != ic.SegConnKind.BUSTERM:
                    continue
                if cs.horiz:
                    px, py = conn.at_pos, display_perp
                else:
                    px, py = display_perp, conn.at_pos
                xs.append(float(px))
                ys.append(float(py))

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
        self._ax_design_stats = None  # bundles·buses·nets header above the list
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
        
        for a in self._busterm_artists:
            a.set_visible(self.ui_state.busterms)
            
        self._apply_preroute_visibility()

        for a in self._vias_conns_artists:
            a.set_visible(self.ui_state.vias_conns)
        self._apply_detailed_via_visibility()

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
                    a.set_linewidth(e['lw'])  # width never changes

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
        for a in self._busterm_artists: a.set_visible(True)
        for a in self._vias_conns_artists: a.set_visible(True)
        self._apply_detailed_via_visibility()
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
                n_total = sum(len(w.input.original_bundle.get_net_names()) * len(w.input.candidates[w.plan.selected_topology_index].segments)
                              for w in self.bundles if w.input.candidates and 0 <= w.plan.selected_topology_index < len(w.input.candidates))
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
                sel = w.plan.selected_topology_index
                if not w.input.candidates or not (0 <= sel < len(w.input.candidates)):
                    stats_part = '[no topo]'; stats_color = '#888888'
                else:
                    n_expected = len(w.input.original_bundle.get_net_names()) * len(w.input.candidates[sel].segments)
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
            layer_visible=self._layer_visible)
        self._topo_explorer.fig.show()
        install_tk_geometry_resync(self._topo_explorer.fig)
        extract_from_fullscreen_tab(self._topo_explorer.fig)

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
        if not self.ui_state.vias_conns:
            sq.set_visible(False)
            xm.set_visible(False)

    def _draw_busterm_conn(self, bid, x, y, col, msz, alpha, zorder, layer=None):
        """Filled square at a segment endpoint that connects to a busterm."""
        sq, = self.ax.plot(x, y, 's', color=col,
                           markeredgecolor='black', markeredgewidth=1.0,
                           markersize=msz, alpha=alpha, zorder=zorder, clip_on=True)
        self._register(bid, sq, alpha=alpha, lw=msz, layer=layer)
        self._vias_conns_artists.append(sq)
        if not self.ui_state.vias_conns:
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
                self._register(bid, line, alpha=seg_alpha, lw=viz_lw,
                                layer=effective_layer)

                self._draw_seg_connectors(bid, idx, cs_list[idx], sx, sy, col,
                                          msz, seg_alpha, 12 + i,
                                          adj_perp=adj_perp,
                                          layer=effective_layer)

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

        Unlike the [Tracks] rail stripes (detailed-mode only, re-derived ad
        hoc from the raw pattern), this draws the first-class PreRoutedSegment
        objects from RoutingGridStack.preroutes() and works in the ABSTRACT
        view too — the pre-route context exists before any detailed routing
        does.  See docs/internal/placed_segment_preroutes.md.
        """
        if routing_grid_stack is None or layer_stack is None:
            return
        self._preroute_grid_stack  = routing_grid_stack
        self._preroute_layer_stack = layer_stack
        self._has_preroute_data    = True
        if self._btn_preroutes is not None:
            self._set_button_enabled(self._btn_preroutes, True, on_color='#f4ece8')

    def _build_preroute_artists(self):
        """Create the pre-route band artists (once, lazily): one
        PatchCollection per (layer, slot type) from the enumerated
        PreRoutedSegments over the floorplan bbox, so per-type visibility
        is a collection flip."""
        if self._preroutes_built or not self._has_preroute_data:
            return
        self._preroutes_built = True
        import buda as ic_mod

        stack = self._preroute_grid_stack
        layer_is_h = {}
        for lid in self._preroute_layer_stack.get_layer_ids_by_dir(
                ic_mod.LayerDir.HORIZONTAL):
            layer_is_h[lid] = True
        for lid in self._preroute_layer_stack.get_layer_ids_by_dir(
                ic_mod.LayerDir.VERTICAL):
            layer_is_h[lid] = False

        # Layout bounding box (the rails-view extent idiom).
        all_blocks = list(self.fp.get_all_blocks())
        if all_blocks:
            x_min = min(r.x1 for _, r in all_blocks)
            x_max = max(r.x2 for _, r in all_blocks)
            y_min = min(r.y1 for _, r in all_blocks)
            y_max = max(r.y2 for _, r in all_blocks)
        else:
            x_min, x_max, y_min, y_max = 0, 1000, 0, 1000

        groups = {}   # (layer, slot_type) -> [Rectangle, ...]
        for lid, is_h in layer_is_h.items():
            if not stack.has_layer(lid):
                continue
            if is_h:
                perp_lo, perp_hi   = y_min, y_max
                along_lo, along_hi = x_min, x_max
            else:
                perp_lo, perp_hi   = x_min, x_max
                along_lo, along_hi = y_min, y_max
            for pr in stack.preroutes(lid, perp_lo, perp_hi,
                                      along_lo, along_hi):
                col  = _PREROUTE_COLOR.get(pr.slot_type, '#f0f0f0')
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
                groups.setdefault((lid, pr.slot_type), []).append(rect)

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
        import buda as ic_mod

        # Build layer direction map from the LayerStack (cheap; needed for stats).
        for lid in layer_stack.get_layer_ids_by_dir(ic_mod.LayerDir.HORIZONTAL):
            self._layer_is_h[lid] = True
        for lid in layer_stack.get_layer_ids_by_dir(ic_mod.LayerDir.VERTICAL):
            self._layer_is_h[lid] = False

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
        the layout (tracks_in_range per layer) and building a Rectangle each is
        the costly part of the detailed view, and Tracks is off by default, so
        most sessions never need it.  Rails are grouped into one PatchCollection
        per (layer, kind); they start hidden and the toggle reveals them.
        """
        if self._rails_built or not self._has_detailed_data:
            return
        self._rails_built = True

        routing_grid_stack = self._detailed_grid_stack

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

        # Collect rail rectangles per (layer, kind) so each resulting collection
        # has a single base alpha (set_alpha in _refresh_highlight is uniform).
        rail_groups = {}   # (layer_id, is_signal) -> [Rectangle, ...]
        for lid, is_h in self._layer_is_h.items():
            if not routing_grid_stack.has_layer(lid):
                continue
            grid    = routing_grid_stack.get_layer_grid(lid)
            pattern = grid.effective_pattern_at(0.0, 0.0)
            up      = pattern.unit_pitch()
            if up <= 0:
                continue
            if is_h:
                lo, hi = y_min - up, y_max + up
            else:
                lo, hi = x_min - up, x_max + up
            for centre, slot in pattern.tracks_in_range(lo, hi):
                is_signal = (slot.type == 'SIGNAL')
                col  = _RAIL_COLOR.get(slot.type, '#f9f9f9')
                half = slot.width / 2.0
                if is_h:
                    rect = patches.Rectangle((x_min, centre - half), x_max - x_min,
                                             slot.width, linewidth=0, facecolor=col)
                else:
                    rect = patches.Rectangle((centre - half, y_min), slot.width,
                                             y_max - y_min, linewidth=0, facecolor=col)
                rail_groups.setdefault((lid, is_signal), []).append(rect)

        for (lid, is_signal), rects in rail_groups.items():
            base_alpha = 0.10 if is_signal else 0.15
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
        self._apply_detailed_via_visibility()
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

        # Fixed overhead consumed by buttons, scroll arrows, the stats line, and
        # gaps: All Bundles btn + 2 slim btns + (4 scroll arrows) + stats + (6 gaps)
        fixed_h  = BTN_H + 2 * BTN_H_SM + 4 * SCROLL_H + STAT_H + 6 * GAP
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

        # ── All Layers ──────────────────────────────────────────────────
        ax_all_layers = self.fig.add_axes(_rect(BTN_H_SM))
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
