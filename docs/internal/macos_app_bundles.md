# macOS `.app` launcher bundles

BUDA is a terminal-driven tool that also pops a Tk/matplotlib GUI. On macOS
the GUI shows up in four places, each named by a **different** OS mechanism:

| Surface | Driven by | Set where |
|---|---|---|
| Window title bar | `set_window_title` (Tk) | `buda_viz.BudaVisualizer.__init__` |
| Menu-bar app name (bold, next to  Apple) | `NSProcessInfo.processName` | `buda_viz.set_app_name` (called from `buda_cli.main`, **before** the first window) |
| Dock **icon** | `NSApplication.setApplicationIconImage_` | `buda_viz.set_dock_icon` (called once the window is realized) |
| Dock **text label** | the executable / the bundle LaunchServices launched | **needs a `.app` bundle** — this doc |

The first three are handled at runtime and work for the plain `bin/buda` /
`bin/fp` terminal commands. The Dock **text label** is the stubborn one: for a
terminal-launched interpreter it is cached from the executable (`python3`) when
the process first registers with LaunchServices, and **no runtime API renames
it**. `setProcessName_` and `CFBundleName` both leave it untouched (verified on
macOS 14 + Anaconda python + Tk 8.6).

## The fix: launch the same python through a `.app`

When a process is started **through a `.app` bundle by LaunchServices**, LS
establishes the application identity (name + icon) at launch, before the
launcher execs the interpreter. That identity is process-level and survives the
`exec`, so the Dock tile reads the bundle's `CFBundleName`. The interpreter is
the very same `python3`; only the launch path differs.

`tools/make_macos_apps.py` builds two thin bundles into `bin/`:

- `bin/Buda.app` → `src/buda_cli.py`
- `bin/Floorplanner.app` → `tools/bdb_floorplanner.py`

Each bundle is just:

```
Buda.app/Contents/
  Info.plist                 # CFBundleName=Buda, CFBundleExecutable=Buda, …
  MacOS/Buda                 # shell launcher: resolve repo root, set PYTHONPATH,
                             #   exec /usr/bin/env python3 <entry> "$@"
  Resources/buda.icns        # from buda_icon.png (sips+iconutil); optional
```

No interpreter is copied — `/usr/bin/env python3` resolves to whatever python is
on `PATH` (e.g. the active conda env), exactly like the `bin/` wrappers. The
launcher finds the repo root from its own location (`…/bin/Buda.app/Contents/
MacOS/Buda` → up 4), so the bundle is relocatable within the checkout.

## Build & use

```bash
python3 tools/make_macos_apps.py          # once, or after changing buda_icon.png
```

The bundles are host-specific build products, so `bin/*.app/` is git-ignored.
Off macOS the script still writes the `Info.plist` + launcher (for inspection);
the `.icns` step is skipped (it needs Apple's `sips`/`iconutil`), and
`set_dock_icon()` supplies the icon at runtime regardless.

Launch options:

```bash
open -a bin/Floorplanner.app --args foo.bdb   # explicit
open -a bin/Buda.app --args flow/x.buda
# …or double-click the bundle in Finder, or drag it onto the Dock.
```

`bin/fp` does this automatically: on macOS, if `Floorplanner.app` exists, it
`exec open -a …`s through the bundle (resolving any path arg to absolute, since
`open` runs from a different cwd). Set `BUDA_NO_APP=1` to force the direct
in-terminal launch (keeps stdout on your terminal — useful for debugging).

`bin/buda` is **not** rerouted: it is a CLI whose per-command summary and flow
log go to the terminal, and launching via `open` would detach that output. Use
`bin/Buda.app` when you specifically want the Dock identity for a flow run; use
`bin/buda` for normal terminal work (it still gets the menu-bar name, window
title, and Dock icon from the runtime hooks).

## Trade-off recap

- `bin/buda` / `bin/fp` in a terminal → correct window title, menu-bar name, and
  Dock icon; Dock **text** still `python3` (unless `fp` reroutes through the app).
- Launch via the `.app` → all four correct, but stdout is detached (fine for the
  GUI-centric Floorplanner; the flow log file still captures full detail).
