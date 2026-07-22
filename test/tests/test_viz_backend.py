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

"""Guard the matplotlib-backend contract src/buda_viz.py relies on.

On macOS `buda_viz` forces the TkAgg backend (the native 'macosx' backend can
segfault), but ONLY when no backend was explicitly chosen — it peeks the raw
`rcParams['backend']` via `dict.__getitem__`, which is the auto-sentinel (a
non-str object) until a backend is selected and a plain string afterwards.

If a future matplotlib changed that representation the force would misfire:
either overriding a caller's explicit Agg (the bug PR #386 fixed) or silently
never firing and re-exposing the macosx segfault. These tests pin the contract.

The force is macOS-only, but the underlying discriminator is platform-
independent, so the contract tests run everywhere; the end-to-end
`import buda_viz` assertion is gated on darwin.
"""
import os
import subprocess
import sys

import pytest

# MID tier: each contract check spawns a fresh subprocess that imports matplotlib
# (~1s total), too heavy for the default fast inner loop.  Runs under `bb mid` /
# `bb slow` (pytest -m "not slow" / everything).
pytestmark = pytest.mark.mid


def _raw_backend_kind(setup="", env_extra=None):
    """Run `setup` in a FRESH interpreter (backend state is process-global) and
    report whether `dict.__getitem__(rcParams, 'backend')` is a str ('STR') or
    the not-yet-resolved auto sentinel ('NONSTR') — the exact value buda_viz's
    force keys on."""
    code = (
        "import matplotlib\n"
        f"{setup}\n"
        "v = dict.__getitem__(matplotlib.rcParams, 'backend')\n"
        "print('STR' if isinstance(v, str) else 'NONSTR')\n"
    )
    env = dict(os.environ)
    env.pop("MPLBACKEND", None)          # start from a clean, unset default
    if env_extra:
        env.update(env_extra)
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_unset_backend_is_nonstr_sentinel():
    """Unset default -> non-str auto sentinel -> buda_viz WOULD force TkAgg
    (the macosx-segfault avoidance case)."""
    assert _raw_backend_kind() == "NONSTR"


def test_explicit_use_makes_backend_str():
    """A prior matplotlib.use(...) -> plain string -> buda_viz SKIPS the force,
    respecting the caller's choice (PR #386)."""
    assert _raw_backend_kind("matplotlib.use('Agg')") == "STR"


def test_mplbackend_env_makes_backend_str():
    """MPLBACKEND=Agg -> plain string -> buda_viz SKIPS the force."""
    assert _raw_backend_kind(env_extra={"MPLBACKEND": "Agg"}) == "STR"


def test_peek_does_not_resolve_the_sentinel():
    """The raw dict.__getitem__ peek must NOT trigger lazy backend resolution —
    otherwise merely checking would pull in the very macosx backend it avoids."""
    code = (
        "import matplotlib\n"
        "before = isinstance(dict.__getitem__(matplotlib.rcParams,'backend'), str)\n"
        "_ = dict.__getitem__(matplotlib.rcParams, 'backend')\n"   # peek
        "after = isinstance(dict.__getitem__(matplotlib.rcParams,'backend'), str)\n"
        "print(before, after)\n"
    )
    env = dict(os.environ)
    env.pop("MPLBACKEND", None)
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False False"    # stays the unresolved sentinel


@pytest.mark.skipif(sys.platform != "darwin",
                    reason="the TkAgg backend force is macOS-only")
def test_buda_viz_respects_explicit_agg_on_macos():
    """End-to-end (macOS): importing buda_viz with MPLBACKEND=Agg keeps Agg
    instead of being dragged onto Tk."""
    root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
    pp = os.pathsep.join([os.path.join(root, "build"), os.path.join(root, "src")])
    env = dict(os.environ, MPLBACKEND="Agg")
    env["PYTHONPATH"] = pp + os.pathsep + env.get("PYTHONPATH", "")
    out = subprocess.run(
        [sys.executable, "-c",
         "import buda_viz, matplotlib; print(matplotlib.get_backend())"],
        capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().lower() == "agg"
