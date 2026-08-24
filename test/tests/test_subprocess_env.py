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
    # Compare against the ANCHORED form, not the literal: `_anchored` (#771)
    # absolutizes every inherited entry on purpose, and on Windows a rooted
    # POSIX path acquires the current DRIVE -- `/inherited/one` comes back
    # `D:\inherited\one`, so the literal held only on POSIX (measured,
    # windows-validate run 36).  What this test is FOR is the ORDER: the
    # inherited entry survives and comes last.  `abspath` is what the helper
    # itself uses, so the two cannot drift.
    assert parts[-1] == os.path.abspath("/inherited/one"), parts
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


def test_a_relative_inherited_entry_is_anchored(tmp_path, monkeypatch):
    """The failure this fixes, stated as the value rather than a property.

    `export PYTHONPATH=build` is the habit CLAUDE.md documents for invoking
    python directly, and a relative entry is re-resolved by Python at EVERY
    import — so handed to a child that runs elsewhere it silently names a
    different directory.  On clean main it also made the cwd-independence
    test below fail outright whenever pytest was launched that way.
    """
    monkeypatch.setenv("PYTHONPATH", "build")
    monkeypatch.chdir(tmp_path)
    parts = buda_env(_ROOT)["PYTHONPATH"].split(os.pathsep)
    assert parts[-1] == str(tmp_path / "build"), parts


def test_an_absolute_inherited_entry_is_passed_through_untouched(tmp_path,
                                                                 monkeypatch):
    """The Visual Studio `build\\Release` case must not be rewritten: it is
    already unambiguous, and only the caller knows it.

    The fixture comes from `tmp_path` because that is DRIVE-QUALIFIED on every
    platform.  Built as `Path(os.sep) / ...` it is `\\opt\\...` on Windows —
    rooted but driveless, which Python 3.13 stopped calling absolute
    (`ntpath.isabs`), so `_anchored` would expand it with the current drive and
    this assertion would fail on the windows-validate jobs ALONE: they pin
    3.13, while Linux CI pins 3.11 where the same path IS absolute.  That is
    the fixture being wrong rather than the helper — a driveless path really is
    drive-ambiguous, so anchoring it is the intended behaviour (Codex #756).
    """
    abs_entry = str(tmp_path / "opt" / "buda" / "build" / "Release")
    assert os.path.isabs(abs_entry), abs_entry      # the fixture's precondition
    monkeypatch.setenv("PYTHONPATH", abs_entry)
    parts = buda_env(_ROOT)["PYTHONPATH"].split(os.pathsep)
    assert parts[-1] == abs_entry, parts


def test_every_inherited_entry_is_anchored_in_order(tmp_path, monkeypatch):
    """Several entries, mixed absolute and relative: each is handled on its
    own and the caller's ORDER survives (it is a search path — the order is
    the caller's precedence, not ours to sort)."""
    # Drive-qualified, for the reason the test above spells out.
    abs_entry = str(tmp_path / "first")
    up = os.path.join("..", "up")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([abs_entry, "rel", up]))
    monkeypatch.chdir(tmp_path)
    parts = buda_env(_ROOT)["PYTHONPATH"].split(os.pathsep)
    assert parts[-3:] == [abs_entry,
                          str(tmp_path / "rel"),
                          os.path.abspath(up)], parts


def test_an_empty_inherited_entry_becomes_the_directory_it_already_meant(
        tmp_path, monkeypatch):
    """A stray separator (`build:`) yields an empty entry, which Python reads
    as the CURRENT DIRECTORY.  Anchoring keeps that meaning instead of
    exporting a `""` the child would re-read as ITS own directory."""
    monkeypatch.setenv("PYTHONPATH", "a" + os.pathsep)
    monkeypatch.chdir(tmp_path)
    parts = buda_env(_ROOT)["PYTHONPATH"].split(os.pathsep)
    assert "" not in parts, parts
    assert parts[-1] == str(tmp_path), parts


def test_anchoring_uses_the_cwd_not_the_root(tmp_path, monkeypatch):
    """The distinguishing case, and the reason `_anchored` does not take
    `root`: the two agree only while cwd IS root.  A relative entry means
    what the PARENT means by it — its own directory — so resolving it against
    the repo root would fabricate a path the caller never wrote."""
    monkeypatch.setenv("PYTHONPATH", "somewhere")
    monkeypatch.chdir(tmp_path)
    parts = buda_env(_ROOT)["PYTHONPATH"].split(os.pathsep)
    assert parts[-1] == str(tmp_path / "somewhere"), parts
    assert parts[-1] != str(_ROOT / "somewhere"), parts


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
