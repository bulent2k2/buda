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
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _run(code, env_extra=None):
    env = {**os.environ,
           "PYTHONPATH": os.pathsep.join(
               [str(_ROOT), str(_ROOT / "build"), str(_ROOT / "src")])}
    env.pop("MPLBACKEND", None)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, env=env, timeout=180)


_TK_PRESENT = (
    "import importlib.util as u; print(u.find_spec('tkinter') is not None)")


def _tkinter_available():
    r = _run(_TK_PRESENT)
    return r.stdout.strip() == "True"


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
