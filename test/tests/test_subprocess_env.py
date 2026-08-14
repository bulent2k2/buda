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

"""`buda_env` builds a PYTHONPATH a child process can actually use.

Like `test_tcl_quote.py`, the Windows half is held on any host: the two
properties under test — which separator joins the entries, and whether the
inherited value survives — are decided by plain string handling, so a POSIX
run can check both.  That matters because the failure they prevent is
invisible here: `ModuleNotFoundError: No module named 'buda'`, ~55 times on
the msbuild job of windows-validate run 24.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from subprocess_env import buda_env

_ROOT = Path(__file__).resolve().parents[2]


def test_entries_are_joined_with_the_platforms_separator():
    env = buda_env(_ROOT)
    parts = env["PYTHONPATH"].split(os.pathsep)
    assert str(_ROOT / "build") in parts, env["PYTHONPATH"]
    assert str(_ROOT / "tools") in parts, env["PYTHONPATH"]


def test_a_windows_path_would_not_survive_a_colon_join():
    """Why `os.pathsep` and not `:`, stated as the arithmetic.

    A Windows path carries a colon of its own, so a `:`-joined value does not
    merely split in the wrong places — `D:\\a\\build:D:\\a\\tools` is ONE
    entry naming nothing, which is exactly how the child lost `buda`.
    """
    win = ["D:\\a\\buda\\buda\\build", "D:\\a\\buda\\buda\\tools"]
    assert len(":".join(win).split(";")) == 1        # one nonsense entry
    assert len(";".join(win).split(";")) == 2        # two real ones


def test_the_inherited_pythonpath_is_kept_and_comes_last(monkeypatch):
    """PREPEND, never replace.  Under the Visual Studio multi-config layout
    the extension lives in `build\\Release`, which only the caller's
    environment knows about — replacing it orphans the child even with the
    separator right (measured: doc-validation run 8, windows-validate 19)."""
    monkeypatch.setenv("PYTHONPATH", "/inherited/one")
    parts = buda_env(_ROOT)["PYTHONPATH"].split(os.pathsep)
    assert parts[-1] == "/inherited/one", parts
    assert parts[0] == str(_ROOT / "build"), parts


def test_an_absent_inherited_pythonpath_leaves_no_empty_entry(monkeypatch):
    """An empty entry means the CURRENT DIRECTORY to Python, so a stray
    separator silently puts cwd on the path of every child."""
    monkeypatch.delenv("PYTHONPATH", raising=False)
    parts = buda_env(_ROOT)["PYTHONPATH"].split(os.pathsep)
    assert "" not in parts, parts


def test_directories_resolve_against_root_not_the_current_directory(tmp_path,
                                                                    monkeypatch):
    """One site passed the literal `"build:src:tools"`, which names real
    directories only while cwd happens to be the repo root."""
    monkeypatch.chdir(tmp_path)
    parts = buda_env(_ROOT)["PYTHONPATH"].split(os.pathsep)
    assert all(os.path.isabs(p) for p in parts), parts
    assert os.path.isdir(parts[0]), parts


def test_overrides_are_applied():
    env = buda_env(_ROOT, MPLBACKEND="Agg")
    assert env["MPLBACKEND"] == "Agg"


@pytest.mark.mid
def test_a_child_really_imports_buda_with_it():
    """The round trip.  The properties above are about strings; this is the
    thing they exist to make true."""
    r = subprocess.run([sys.executable, "-c", "import buda; print('OK')"],
                       capture_output=True, text=True, timeout=180,
                       env=buda_env(_ROOT, "build", "src", "tools"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "OK" in r.stdout, r.stdout + r.stderr


def test_no_test_builds_a_pythonpath_by_hand():
    """The defect was ~15 private spellings of one idea, five of them wrong
    on Windows.  The fix is not 15 private corrections — it is that there is
    one helper and everybody calls it.

    Written against the VALUE (a `:`-joined literal, or a replaced env), not
    against whether the file mentions the helper: a file can call `buda_env`
    on one line and hand-roll a PYTHONPATH on the next.
    """
    import re
    here = Path(__file__).parent
    # Anchored on the VALUE, not on the line: a first cut matched a bare `":`
    # and so flagged the dict KEY `"PYTHONPATH":` in every correct site too —
    # a guard that fires on the right files for the wrong reason is a guard
    # that will fire on the wrong ones next.
    colon_joined_fstring = re.compile(r"\}:\{")          # f"{a}:{b}"
    colon_literal = re.compile(                          # PYTHONPATH="a:b"
        r"""PYTHONPATH["']?\s*[:=]\s*["']([^"']*:[^"']*)["']""")
    offenders = []
    for p in sorted(here.glob("test_*.py")):
        if p.name == Path(__file__).name:
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "PYTHONPATH" not in line or line.lstrip().startswith("#"):
                continue
            if colon_joined_fstring.search(line) or colon_literal.search(line):
                offenders.append(f"{p.name}:{n}: {line.strip()}")
    assert not offenders, offenders
