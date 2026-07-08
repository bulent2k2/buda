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
Running the generator by hand is optional: `bin/fp` self-bootstraps
`Floorplanner.app` on its first macOS launch (and `bin/buda` builds its per-cell
bundle on the fly), so a fresh clone needs no manual step. Off macOS the script
still writes the `Info.plist` + launcher (for inspection); the `.icns` step is
skipped (it needs Apple's `sips`/`iconutil`), and `set_dock_icon()` supplies the
icon at runtime regardless.

Launch options:

```bash
open -a bin/Floorplanner.app --args foo.bdb   # explicit
open -a bin/Buda.app --args flow/x.buda
# …or double-click the bundle in Finder, or drag it onto the Dock.
```

Both wrappers do this automatically on macOS (opt out with `BUDA_NO_APP=1`),
resolving any path arg to absolute since `open` runs from a different cwd:

- **`bin/fp`** → builds `Floorplanner.app` if missing, then `exec open -n -a
  Floorplanner.app --args …`. It is a pure GUI, so detaching stdout is fine.
- **`bin/buda`** → routes through a **per-cell** bundle so the Dock tile shows
  the *cell name* (the `.buda` basename), not a generic "Buda" — running many
  cells, that makes the right window easy to pick out. It writes a throwaway
  `<cellname>.app` (`CFBundleName=<cellname>`, repo root baked into its launcher,
  icon copied from `bin/Buda.app`) under `${TMPDIR}/buda_cellapps/` and
  `exec open -W -a <cellname>.app --stdin/--stdout/--stderr "$(tty)" --args …`.
  `buda` is a CLI whose per-command summary and `flow.log` line must stay on the
  terminal, so it wires stdio back to the controlling tty and `-W` blocks until
  the run exits — you get the per-cell Dock identity **and** the terminal output.
  Taken only when stdin, stdout **and** stderr are all ttys (so a redirected
  `buda … >run.log` or `| tee` isn't clobbered by `open --stdout "$(tty)"` — it
  falls through to the direct launch that honors the pipe/file), an `open` that
  supports `--stdout` (macOS 10.15+), a script argument, and no `--no-viz` (a
  batch run has no window to name); otherwise it uses the plain in-terminal
  launch.

Why per-cell: the menu-bar name, window title, and Dock *icon* already follow
the cell name at runtime, but the Dock *text* is fixed to the launching bundle's
`CFBundleName` and can't be changed after launch — so a distinct bundle per cell
is the only way to vary it. `bin/fp` needs no such trick (one Floorplanner).

Both wrappers pass `open -n` (new instance each launch), so you can run the same
cell twice — e.g. `buda foo &` `buda foo &` — and get two windows side by side.
Without `-n`, `open` re-activates the one running instance and reports
`… was already running and so the redirected stdin/stdout/stderr … could not be
set`, leaving a single window.

Note: through `open -W` the wrapper does not propagate `buda`'s exit code (it
reports `open`'s). Scripts that check `$?` should run with `BUDA_NO_APP=1`.

## Trade-off recap

- `BUDA_NO_APP=1` (or older macOS / no tty / `--no-viz` / non-macOS) → direct
  launch: correct window title, menu-bar name, and Dock icon, but the Dock
  **text** is `python3`.
- Default on macOS 10.15+ → routed through a `.app`: all four surfaces correct.
  `fp` detaches stdout (GUI); `buda` keeps it via `open --stdout` + `-W` and
  names the tile after the cell.
