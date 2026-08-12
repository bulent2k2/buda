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

"""`pip install .` — the parts of it that can be checked without installing.

Actually building the wheel takes minutes (it compiles the engine), so it is a
by-hand step recorded in docs/internal/packaging.md.  What is pinned here is
everything that decides whether that build produces something that WORKS, and
every one of these is a pair that can silently drift:

  * two declarations of the version (pyproject.toml / CMakeLists.txt);
  * three console scripts naming functions in `buda_runtime.entry`, which name
    modules that have to have a `main`;
  * the layout rule in `buda_runtime`, whose installed branch no test in a
    checkout would otherwise ever execute;
  * `buda.tcl` resolving its engine child as a file beside itself, which is
    what forces the two into one directory in the wheel.
"""
import os
import re
import shutil
import sys
import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

# Under pytest the repo root is already on sys.path (it is rootdir), so this
# is normally a no-op; guarded so running this file standalone still works
# WITHOUT pushing a duplicate entry in front of everything.
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
import buda_runtime            # noqa: E402
from buda_runtime import entry  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_sys_path():
    """Give every test in this file its own `sys.path`.

    Several of them call `install()` for real, which PERMANENTLY reorders the
    pytest process's path for every module collected after this one — and it
    is not a harmless reorder: `conftest.py` deliberately APPENDS `tools`
    while inserting `build`/`src` at the front, so `tools` is a last resort by
    design, and `install()` moves it to index 0 mid-session.  This file sorts
    into the middle of the suite, so half the tests would resolve `tools`
    differently from the other half — nothing fails today, and a failure that
    depended on it would be very hard to read.
    """
    saved = list(sys.path)
    yield
    sys.path[:] = saved


def _pyproject():
    with open(_ROOT / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)


_isolated_n = 0


def _load_isolated(path):
    """Import a COPY of buda_runtime from `path` under a throwaway name.

    Loaded through a real spec, not exec'd into a bare dict: the module reads
    `__file__` to decide the layout, which is the very thing under test, and a
    dict without one would fail for a reason that has nothing to do with it.
    Kept out of `sys.modules` so the real `buda_runtime` stays untouched.
    """
    global _isolated_n
    _isolated_n += 1
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        f"_buda_runtime_probe_{_isolated_n}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── the version pair ──────────────────────────────────────────────────────

def test_version_matches_cmake():
    """One version, declared twice.  Nothing at build time compares them —
    CMake's is what `project()` stamps on the library, pyproject's is what pip
    puts on the wheel — so a release could ship 3.1.0 built from a 3.0.0 tree
    and every check would pass."""
    cml = (_ROOT / "CMakeLists.txt").read_text()
    m = re.search(r"project\(buda\s+VERSION\s+(\S+)", cml)
    assert m, "CMakeLists.txt no longer declares project(buda VERSION ...)"
    assert _pyproject()["project"]["version"] == m.group(1)


# ── the console scripts ───────────────────────────────────────────────────

def test_console_scripts_resolve():
    """Every `[project.scripts]` target must be an attribute of the module it
    names.  A bad entry point is not a build error — pip installs the script
    happily and it dies with an AttributeError the first time a user runs it."""
    scripts = _pyproject()["project"]["scripts"]
    assert set(scripts) == {"buda", "buda-fp", "buda-viz"}
    for name, target in scripts.items():
        mod, _, func = target.partition(":")
        assert mod == "buda_runtime.entry", f"{name} points outside entry.py"
        assert callable(getattr(entry, func)), f"{name} -> {target} is not callable"


@pytest.mark.parametrize("module,where", [("buda_cli", "src"),
                                          ("bdb_floorplanner", "tools"),
                                          ("def_viz_o3", "tools")])
def test_entry_target_modules_have_main(module, where):
    """`entry._main` calls `<module>.main()`.  These three modules are reached
    only through the installed scripts, so a rename would be invisible until
    someone ran the installed command.

    The two GUI entry points are checked on the SOURCE rather than by
    importing.  `def_viz_o3` calls `matplotlib.use('TkAgg')` at module scope,
    so importing it here would switch the backend for the whole pytest
    PROCESS — the suite runs headless under Agg, and every viz test after this
    one would be drawing at a display that is not there.  (`bdb_floorplanner`
    guards its own backend choice and would import safely; it is checked the
    same way for one rule rather than two.)  Nor would skipping when tkinter
    is missing help: `actions/setup-python` ships tkinter, so the skip would
    protect this container and fire the hazard on CI.  A source check has no
    such reach and still catches the rename this exists for.
    """
    src = (_ROOT / where / f"{module}.py").read_text()
    assert re.search(r"^def main\(", src, re.M), f"{module}.py defines no main()"
    if module == "buda_cli":                 # no module-scope backend choice
        buda_runtime.install()
        __import__(module)
        assert callable(getattr(sys.modules[module], "main"))


@pytest.mark.parametrize("func,command", [("floorplanner", "buda-fp"),
                                          ("viz", "buda-viz")])
@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_gui_scripts_answer_help_without_a_display(func, command, flag,
                                                   monkeypatch, capsys):
    """`buda-fp --help` must not need tkinter, a display, or a real BDB.

    `bin/fp` and `bin/viz` answer help in bash before they exec anything, for
    this reason; the console scripts did not, and it showed — measured on an
    installed copy, `buda-fp --help` reported `cannot open BDB — file not
    found: --help` and `buda-viz --help` died in `import tkinter`.

    The assertion that matters is the SECOND one: help must be answered
    without reaching the module at all.  A version that printed usage after
    importing the GUI would satisfy the text check and still fail on the
    headless host that needs it.
    """
    monkeypatch.setattr(sys, "argv", [command, flag])
    monkeypatch.setattr(entry, "_main",
                        lambda *a, **k: pytest.fail("imported the GUI module"))
    assert getattr(entry, func)() == 0
    assert capsys.readouterr().out.startswith(f"Usage: {command}")


def test_buda_help_is_left_to_argparse():
    """`buda --help` is generated by argparse inside `buda_cli`, which knows
    the real option list.  A copy of it here would go stale silently, so the
    help table must not claim that command."""
    assert "buda" not in entry._HELP
    assert set(entry._HELP) == {"buda-fp", "buda-viz"}


# ── the layout rule ───────────────────────────────────────────────────────

def test_checkout_layout_is_the_repo():
    """In a checkout the layer is the four directories PYTHONPATH names."""
    assert buda_runtime.in_checkout()
    assert buda_runtime.paths() == [str(_ROOT / "build"), str(_ROOT / "src"),
                                    str(_ROOT / "tools"), str(_ROOT)]


def test_importing_buda_runtime_has_no_side_effect(monkeypatch):
    """`import buda_runtime` must not touch `sys.path` — `install()` is the
    ask.  This is the visible half of a deliberate two-tier contract, so it is
    stated rather than left to be inferred:

      * `import buda` (and `buda_db`) work bare after `pip install .` — they
        are the project's public names and land at the wheel root;
      * `buda_cli`, `buda_cmds`, `bdb_serialize`, `from tools import …` need
        `import buda_runtime; buda_runtime.install()` first.

    The second tier is NOT an oversight to be fixed by moving those modules up.
    They are about forty generic top-level names — `ui_state`, `viz_common`,
    `slot_groups`, `render`, `web`, `tools` — and a distribution that claimed
    them in site-packages would shadow other people's code.  `tools` is not
    hypothetical: one appeared transitively in CI and broke every `from tools
    import …` in the suite at once (see tools/__init__.py).
    """
    # Start from a sys.path with the layer removed — an earlier test in this
    # file has already installed it, and `install()` is idempotent, so leaving
    # it there would make the "asking does something" half vacuously pass.
    monkeypatch.setattr(sys, "path",
                        [p for p in sys.path if p not in buda_runtime.paths()])
    before = list(sys.path)
    mod = _load_isolated(_ROOT / "buda_runtime" / "__init__.py")
    assert sys.path == before, "importing buda_runtime changed sys.path"
    mod.install()                       # ... and asking for it does the work
    assert sys.path[:len(mod.paths())] == mod.paths()


def test_install_is_idempotent_and_does_not_reorder(monkeypatch):
    """Called twice it must be a no-op the second time — a version that
    removed-and-reinserted would quietly move a caller's own entries."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    buda_runtime.install()
    before = list(sys.path)
    buda_runtime.install()
    assert sys.path == before


def test_install_front_false_appends(monkeypatch):
    """`install(front=False)` is the library caller's mode: the layer goes at
    the BACK, so the caller's own modules keep winning.

    Without it, `install()` claims ~40 generic names at `sys.path[0]` — a
    stronger claim than site-packages would have been, since site-packages
    sits near the end and index 0 beats even the caller's script directory.
    Measured: from a directory holding the caller's own `render.py`, plain
    `install()` makes `import render` return BUDA's `tools/render.py`.
    """
    monkeypatch.setattr(sys, "path",
                        [p for p in sys.path if p not in buda_runtime.paths()])
    caller_owns = sys.path[0]
    buda_runtime.install(front=False)
    assert sys.path[0] == caller_owns, "appended mode must not take the front"
    assert sys.path[-len(buda_runtime.paths()):] == buda_runtime.paths()


def test_install_front_false_keeps_build_ahead_of_src(monkeypatch):
    """Appending changes where the block goes, never the order inside it —
    `build` stays ahead of `src` so a stale `.so` beside the sources cannot
    win, which is the one ordering constraint that matters."""
    monkeypatch.setattr(sys, "path",
                        [p for p in sys.path if p not in buda_runtime.paths()])
    buda_runtime.install(front=False)
    where = buda_runtime.paths()
    assert sys.path.index(where[0]) < sys.path.index(where[1])


def test_viz_ipc_fallback_rejects_a_stranger_tools(tmp_path):
    """`buda_viz`'s `../tools` fallback must ask whether that `tools` is OURS.

    An `isdir` test cannot: installed, `..` is site-packages, where `tools` is
    a real distribution name — so `isdir` is TRUE exactly when the stranger
    exists and false only in the case that was never the hazard.  The guard is
    content-addressed on the file it is about to import.
    """
    src = (_ROOT / "src" / "buda_viz.py").read_text()
    assert "isdir(_tools)" not in src, "the fallback is back to an isdir guard"
    assert "_tools, 'viz_ipc.py'" in src, "the fallback is not content-addressed"

    stranger = tmp_path / "site-packages" / "tools"      # someone else's
    stranger.mkdir(parents=True)
    (stranger / "__init__.py").write_text("")
    assert os.path.isdir(stranger)                       # an isdir guard passes
    assert not os.path.isfile(stranger / "viz_ipc.py")   # this one does not


def test_installed_layout_folds_into_one_directory(tmp_path):
    """The wheel branch of `paths()`, which a checkout never executes.

    Built the way the wheel is: `buda_runtime/` holding the package plus the
    `src/` modules flattened in, and `tools/` as a subdirectory.  Both go on
    sys.path — `_HERE` so `from tools import ...` and the `src` modules
    resolve, `_HERE/tools` so `import bdb_serialize` does (that is how
    `open_bdb` reads a `.bdb.sql`).
    """
    pkg = tmp_path / "site-packages" / "buda_runtime"
    (pkg / "tools").mkdir(parents=True)
    shutil.copy(_ROOT / "buda_runtime" / "__init__.py", pkg / "__init__.py")
    assert not (tmp_path / "site-packages" / "CMakeLists.txt").exists()

    mod = _load_isolated(pkg / "__init__.py")
    assert mod.in_checkout() is False
    assert mod.paths() == [str(pkg), str(pkg / "tools")]


def test_a_build_distribution_is_not_mistaken_for_a_checkout(tmp_path):
    """`in_checkout()` asks for the CMakeLists that BUILDS us, deliberately.

    Probing for a `build/` directory instead would be a guess, and a wrong one:
    `build` is a real distribution (the PEP 517 frontend) and installs as
    `site-packages/build/`, so any machine with it would read as a checkout and
    the wheel would put site-packages/src and site-packages/tools on sys.path.
    """
    sp = tmp_path / "site-packages"
    (sp / "build").mkdir(parents=True)          # the PEP 517 frontend
    (sp / "buda_runtime" / "tools").mkdir(parents=True)
    shutil.copy(_ROOT / "buda_runtime" / "__init__.py",
                sp / "buda_runtime" / "__init__.py")

    mod = _load_isolated(sp / "buda_runtime" / "__init__.py")
    assert mod.in_checkout() is False
    assert str(sp / "build") not in mod.paths()


# ── what the wheel has to keep together ───────────────────────────────────

def test_tcl_bridge_and_server_are_adjacent():
    """`buda.tcl` computes its engine child from its OWN directory, so the two
    must install into the same directory — no console script or import can
    stand in for a path Tcl resolves.  The wheel keeps `tools/` whole for
    exactly this reason; pinned here so a future split notices."""
    tcl = (_ROOT / "tools" / "buda.tcl").read_text()
    assert "file join $dir buda_server.py" in tcl
    assert (_ROOT / "tools" / "buda_server.py").exists()


def test_wheel_ships_the_layer_wholesale():
    """The BUDA_WHEEL rules install `src/` and `tools/` by PATTERN, not by
    list.  A list is a judgement call that fails silently — a module left out
    of it surfaces as an ImportError in a user's flow, not at build time."""
    cml = (_ROOT / "CMakeLists.txt").read_text()
    assert re.search(r'install\(DIRECTORY src/ DESTINATION "\$\{BUDA_PY_DEST\}"\s*\n'
                     r'\s*FILES_MATCHING PATTERN "\*\.py"', cml)
    assert re.search(r'install\(DIRECTORY tools/ DESTINATION "\$\{BUDA_PY_DEST\}/tools"\s*\n'
                     r'\s*FILES_MATCHING PATTERN "\*\.py" PATTERN "\*\.tcl"', cml)


def test_editable_redirects_rather_than_copies():
    """`buda_runtime` must be declared as the wheel's package, not installed by
    CMake.  Measured: with the whole layer coming from CMake, `pip install -e .`
    COPIED `src/*.py` into site-packages — editing the checkout changed
    nothing and the install reported success while serving a stale copy.
    scikit-build-core redirects a declared package and copies everything else,
    so this one line is the difference."""
    assert _pyproject()["tool"]["scikit-build"]["wheel"]["packages"] == ["buda_runtime"]
    cml = (_ROOT / "CMakeLists.txt").read_text()
    assert 'if(NOT SKBUILD_STATE STREQUAL "editable")' in cml


def test_pip_build_does_not_share_the_developer_build_dir():
    """`build/` is the developer's, written by `bin/bb` and read via
    PYTHONPATH.  If pip configured CMake in it, a `pip install` would leave the
    tree configured with a different BUDA_ARCH — which is precisely what
    perturbs the placement goldens."""
    sk = _pyproject()["tool"]["scikit-build"]
    assert not sk["build-dir"].startswith("build/")
    assert sk["cmake"]["define"]["BUDA_ARCH"] == "none"
