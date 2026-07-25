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

"""Platform / window-manager glue for the BUDA viz windows: raise/focus,
fullscreen, Tk geometry resync, macOS Dock icon + app name, and the
matplotlib default-keymap opt-out.  Self-contained (matplotlib + Tk/AppKit
only); no imports back into the viewer modules."""
import os
import sys
import types

import matplotlib.pyplot as plt



def _resync_canvas_to_widget(fig, settle_ms=80, max_tries=12):
    """Re-run TkAgg's ``resize`` with the widget's SETTLED size until it stops
    changing, re-centering the figure image (and the Tk→figure y-flip that maps
    a click to a data coord) for the real on-screen size.

    This is the same cure ``install_tk_geometry_resync`` applies on ``<Configure>``
    events, but driven DIRECTLY off a caller (see ``_toggle_fullscreen``): a
    programmatic transition can settle without a clean toplevel ``<Configure>``
    on some WMs, so the event-driven resync never fires and the image stays
    centered for a stale size — leaving button clicks a few pixels off
    vertically. ``update_idletasks`` forces Tk to apply the pending geometry
    before we read it, so the first pass already sees the real size. No-op off
    Tk / on a headless backend."""
    try:
        canvas = fig.canvas
        tkw = canvas.get_tk_widget()
        win = tkw.winfo_toplevel()
    except Exception:
        return  # not a Tk backend; nothing to do

    def _step(prev=None, tries=0):
        try:
            win.update_idletasks()
            w, h = tkw.winfo_width(), tkw.winfo_height()
        except Exception:
            return
        if w > 1 and h > 1:
            try:
                canvas.resize(types.SimpleNamespace(width=w, height=h))
                canvas.draw_idle()
            except Exception:
                pass
        if (w, h) != prev and tries < max_tries:
            try:
                win.after(settle_ms, lambda: _step((w, h), tries + 1))
            except Exception:
                pass

    _step()


def _toggle_fullscreen(fig):
    mgr = fig.canvas.manager
    if mgr:
        mgr.full_screen_toggle()
        # Re-center the figure image for the post-toggle size so button clicks
        # don't land a few pixels off vertically — the <Configure>-driven
        # resync alone proved unreliable for the programmatic 'f' toggle.
        _resync_canvas_to_widget(fig)


def raise_window(win_or_fig):
    """Bring a window or figure to the front and ensure it has keyboard focus."""
    import matplotlib
    if matplotlib.get_backend().lower() in ("agg", "svg", "pdf", "ps", "template"):
        return
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



def set_icon(win_or_fig, icon_name="buda_icon.png"):
    """Set the application icon for the window or figure."""
    import matplotlib
    if matplotlib.get_backend().lower() in ("agg", "svg", "pdf", "ps", "template"):
        return
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



def set_dock_icon(icon_name="buda_icon.png"):
    """macOS only: replace the Dock tile's icon image (the default python3
    rocket) with BUDA's icon via NSApplication.setApplicationIconImage_.
    Needs the NSApp to exist, so call AFTER the first window is realized.
    Fully guarded — a no-op off macOS, without pyobjc, or if the icon file
    is missing. Note: this changes the Dock *icon*, not the Dock *text
    label* (that is derived from the executable and needs a .app bundle)."""
    if sys.platform != "darwin":
        return
    import matplotlib
    if matplotlib.get_backend().lower() in ("agg", "svg", "pdf", "ps", "template"):
        return
    try:
        _HERE = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(_HERE, "..", icon_name)   # project root
        if not os.path.exists(icon_path):
            icon_path = os.path.join(_HERE, icon_name)
        if not os.path.exists(icon_path):
            return
        from AppKit import NSApplication, NSImage
        img = NSImage.alloc().initByReferencingFile_(icon_path)
        if img is not None:
            NSApplication.sharedApplication().setApplicationIconImage_(img)
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
    import matplotlib
    if matplotlib.get_backend().lower() in ("agg", "svg", "pdf", "ps", "template"):
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
        'keymap.forward', 'keymap.xscale', 'keymap.yscale', 'keymap.grid',
        # 'p' (pan) and 'o' (zoom) toggle matplotlib's interactive modes; BUDA
        # binds 'p' to previous-bundle/topo and drives pan/zoom itself, so drop
        # matplotlib's single-letter defaults or both fire on one keypress.
        'keymap.pan', 'keymap.zoom'
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
