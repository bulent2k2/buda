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

"""Importing the Floorplanner GUI module must not drag a headless caller onto Tk.

`tools/bdb_floorplanner.py` needs TkAgg to RUN the GUI, but `matplotlib.use()`
is process-GLOBAL and its tkinter try/except only covers tkinter being ABSENT.
So on every box where tkinter EXISTS (all macOS, any Linux with python3-tk) an
unconditional `use("TkAgg")` flipped a caller that had explicitly selected Agg.

In the fast test tier that was a real, visible bug: `test_floorplanner_undo`
imports this module INSIDE its test body, so the flip happened mid-session;
from then on `viz_window._is_headless_backend()` was False, which stopped
suppressing `raise_window` / `set_icon`, and the next fast test constructing a
BudaVisualizer / TopologyExplorer opened a REAL window and ran
`osascript … set frontmost` — stealing focus on the developer's machine.
Headless CI never saw it (no tkinter -> the except branch -> no flip), which is
exactly why it needs a test that does not depend on tkinter being missing.

Each case runs in a SUBPROCESS: backend selection is process-global and
one-shot, so an in-process assertion would depend on which other test imported
the module first.

CAVEAT, so green CI is not mistaken for coverage: the Agg-preservation cases
are only LOAD-BEARING where tkinter exists.  On a tkinter-less runner the
module takes the except branch and never touches the backend, so they pass
trivially.  They fail (pre-fix) exactly where the bug was reachable — a
developer machine or any runner with python3-tk installed.
"""
import atexit
import glob
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

# A private, EMPTY matplotlib config dir for every probe below.  The suite
# exports MPLCONFIGDIR (conftest points it at <repo>/log/matplotlib), and a
# matplotlibrc there with a `backend:` line counts as an explicit selection —
# which would make the "no backend was chosen" case below silently not be that
# case at all, and fail.  A test whose whole premise is "nothing is selected"
# has to own its config source instead of inheriting one (Codex #603).
_MPLCONFIG = tempfile.mkdtemp(prefix="buda-mplconfig-")
atexit.register(shutil.rmtree, _MPLCONFIG, True)


def _warm_cache_dir():
    """A directory that already holds matplotlib's built `fontlist-v*.json`.

    Prefer the suite's MPLCONFIGDIR when it is ALREADY warm; otherwise fall
    back to matplotlib's DEFAULT cache dir.  The fallback must resolve that
    default with MPLCONFIGDIR *removed* from the environment: matplotlib points
    its cache AT MPLCONFIGDIR when set, so on a clean checkout (or after `rm
    -rf log/`) `get_cachedir()` would just echo the same empty suite dir and we
    would find nothing — every worker would still rebuild, even though the
    user's normal cache is warm (Codex #638 P2).  A machine with no cache
    anywhere yields None and the probe pays a one-time rebuild (pre-existing)."""
    mcd = os.environ.get("MPLCONFIGDIR")
    if mcd and glob.glob(os.path.join(mcd, "fontlist-v*.json")):
        return mcd
    try:
        env = {k: v for k, v in os.environ.items() if k != "MPLCONFIGDIR"}
        default = subprocess.run(
            [sys.executable, "-c",
             "import matplotlib; print(matplotlib.get_cachedir())"],
            capture_output=True, text=True, env=env, timeout=60).stdout.strip()
    except Exception:
        return None
    return default if default and glob.glob(
        os.path.join(default, "fontlist-v*.json")) else None


def _seed_font_cache(dst):
    """Copy the already-built `fontlist-v*.json` from a warm cache into the
    fresh `dst`.  A pristine MPLCONFIGDIR is empty of a `matplotlibrc` backend
    line (the whole point above) — but it is ALSO empty of the font cache, so
    every probe subprocess would otherwise rebuild it from scratch (~26s on
    macOS, scanning all system fonts).  That rebuild happens at COLLECTION time
    (the `skipif` below calls `_tkinter_available()`), so under `bb -p` every
    xdist worker paid a full font-cache build before the first test ran — the
    long delay before `[0%]`.  Seeding the cache (NOT a matplotlibrc) keeps the
    "nothing selected" premise intact while making each probe <1s."""
    src = _warm_cache_dir()
    if not src:
        return
    for fc in glob.glob(os.path.join(src, "fontlist-v*.json")):
        shutil.copy(fc, dst)


_seed_font_cache(_MPLCONFIG)


def _run(code, env_extra=None):
    # PREPEND to the inherited PYTHONPATH, never replace: the VS-generator
    # layout keeps the extension in build/Release, which only the inherited
    # value knows about (measured: windows-validate run 19, all three probes
    # died with ModuleNotFoundError: buda; the documented subprocess trap —
    # WINDOWS_BUILD.md).  Only the MPL env needs to be controlled here, and
    # PYTHONPATH does not influence matplotlib's backend selection.
    env = {**os.environ, "MPLCONFIGDIR": _MPLCONFIG}
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_ROOT), str(_ROOT / "build"), str(_ROOT / "src")]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env.pop("MPLBACKEND", None)
    env.pop("MATPLOTLIBRC", None)      # the other explicit-rc channel
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, env=env, timeout=180)


# Probe by IMPORTING what bdb_floorplanner's try-block imports, not by asking
# whether the package is discoverable: a CPython built without Tcl/Tk still
# ships the pure-Python `tkinter` package, so find_spec() says yes while
# `import tkinter` raises on the missing `_tkinter` extension.  There the skip
# would not fire, the module would report _TK_AVAILABLE=False, and the GUI case
# would FAIL instead of skipping.  The gate must test the same predicate the
# code under test does (Codex #603).
_TK_PROBE = (
    "try:\n"
    "    import tkinter\n"
    "    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg\n"
    "    print('True')\n"
    "except Exception:\n"
    "    print('False')\n")


def _tkinter_available():
    return _run(_TK_PROBE).stdout.strip() == "True"


def test_explicit_agg_survives_importing_the_floorplanner():
    """THE regression: an explicitly headless caller keeps Agg."""
    r = _run(
        "import matplotlib; matplotlib.use('Agg')\n"
        "from tools import bdb_floorplanner\n"
        "print(matplotlib.get_backend())\n")
    assert r.returncode == 0, f"stdout={r.stdout}\nstderr={r.stderr}"
    assert r.stdout.strip().lower() == "agg", (
        "importing bdb_floorplanner hijacked the caller's headless backend "
        f"(got {r.stdout.strip()!r}) — a later visualizer would open a real "
        "window and steal focus")


def test_mplbackend_env_also_survives():
    """The form the test suite actually uses (conftest / bin/bb set the env)."""
    r = _run("import matplotlib\n"
             "from tools import bdb_floorplanner\n"
             "print(matplotlib.get_backend())\n",
             env_extra={"MPLBACKEND": "Agg"})
    assert r.returncode == 0, f"stdout={r.stdout}\nstderr={r.stderr}"
    assert r.stdout.strip().lower() == "agg", r.stdout


@pytest.mark.skipif(not _tkinter_available(),
                    reason="no tkinter: the GUI branch cannot be exercised")
def test_gui_still_gets_tkagg_when_no_backend_was_chosen():
    """The other half of the contract — the fix must not break `bin/fp`.
    With nothing selected, the module still picks TkAgg for the real GUI."""
    r = _run("import matplotlib\n"
             "from tools import bdb_floorplanner as F\n"
             "print(matplotlib.get_backend(), F._TK_AVAILABLE)\n")
    assert r.returncode == 0, f"stdout={r.stdout}\nstderr={r.stderr}"
    backend, tk_avail = r.stdout.split()
    assert backend.lower() == "tkagg", r.stdout
    assert tk_avail == "True", r.stdout
