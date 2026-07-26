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
segfault), but ONLY when no backend was explicitly chosen.  It uses
`matplotlib.get_backend(auto_select=False)`, which returns None until a backend
is selected and a backend name afterwards.

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


def _backend_state(setup="", env_extra=None):
    """Run `setup` in a FRESH interpreter (backend state is process-global) and
    report whether matplotlib sees an explicit backend or the unresolved
    implicit default."""
    code = (
        "import matplotlib\n"
        f"{setup}\n"
        "v = matplotlib.get_backend(auto_select=False)\n"
        "print('UNSET' if v is None else v)\n"
    )
    env = dict(os.environ)
    env.pop("MPLBACKEND", None)          # start from a clean, unset default
    if env_extra:
        env.update(env_extra)
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_unset_backend_is_unresolved():
    """Unset default -> unresolved backend -> buda_viz WOULD force TkAgg
    (the macosx-segfault avoidance case)."""
    assert _backend_state() == "UNSET"


def test_explicit_use_sets_backend():
    """A prior matplotlib.use(...) -> backend name -> buda_viz SKIPS the force,
    respecting the caller's choice (PR #386)."""
    assert _backend_state("matplotlib.use('Agg')").lower() == "agg"


def test_mplbackend_env_sets_backend():
    """MPLBACKEND=Agg -> backend name -> buda_viz SKIPS the force."""
    assert _backend_state(env_extra={"MPLBACKEND": "Agg"}).lower() == "agg"


def test_backend_probe_does_not_auto_select():
    """The backend probe must NOT trigger lazy backend resolution —
    otherwise merely checking would pull in the very macosx backend it avoids."""
    code = (
        "import matplotlib\n"
        "before = matplotlib.get_backend(auto_select=False)\n"
        "_ = matplotlib.get_backend(auto_select=False)\n"
        "after = matplotlib.get_backend(auto_select=False)\n"
        "print(before is None, after is None)\n"
    )
    env = dict(os.environ)
    env.pop("MPLBACKEND", None)
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "True True"


def test_sitecustomize_sets_pytest_backend_without_importing_matplotlib(tmp_path):
    """The process-start hook gives pytest an Agg default before conftest, without
    importing matplotlib itself."""
    root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
    probe = tmp_path / "test_backend_probe.py"
    probe.write_text(
        "import os, sys\n\n"
        "def test_probe():\n"
        "    assert os.environ.get('MPLBACKEND') == 'Agg'\n"
        "    assert 'matplotlib' not in sys.modules\n",
        encoding="utf-8")
    env = dict(os.environ)
    env.pop("MPLBACKEND", None)
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-o", "addopts=", str(probe)],
        capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stdout + out.stderr


def test_headless_backend_skips_window_manager_side_effects():
    """Agg-backed tests may instantiate visualizers, but they must not touch OS
    window APIs that can flash a window or activate AppKit."""
    root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
    pp = os.pathsep.join([os.path.join(root, "build"), os.path.join(root, "src")])
    code = r"""
import builtins
import types
import viz_window

assert viz_window._is_headless_backend(), viz_window.plt.get_backend()

calls = []
manager = types.SimpleNamespace(
    window=None,
    show=lambda: calls.append("show"),
    full_screen_toggle=lambda: calls.append("fullscreen"),
)
fig = types.SimpleNamespace(canvas=types.SimpleNamespace(manager=manager))

real_import = builtins.__import__
blocked = []

def guarded_import(name, *args, **kwargs):
    root = name.split(".", 1)[0]
    if root in {"AppKit", "Foundation", "tkinter"}:
        blocked.append(root)
        raise AssertionError(f"unexpected headless import: {name}")
    return real_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
viz_window.sys.platform = "darwin"

viz_window._toggle_fullscreen(fig)
viz_window.raise_window(fig)
viz_window.set_icon(fig)
viz_window.set_dock_icon()
viz_window.set_app_name("probe", fig)

assert calls == [], calls
assert blocked == [], blocked
"""
    env = dict(os.environ, MPLBACKEND="Agg")
    env["PYTHONPATH"] = pp + os.pathsep + env.get("PYTHONPATH", "")
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stdout + out.stderr


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
