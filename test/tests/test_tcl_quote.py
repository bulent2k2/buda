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

"""`tcl_path` gives Tcl back exactly the path it was handed — as ONE word.

The shapes here are Windows-shaped STRINGS, not Windows-only behaviour: it is
the Tcl parser that misreads them, and every platform ships the same parser.
So a POSIX host can hold the whole property, which is the point — the defect
these pin was invisible until `windows-validate` ran, six days after it
landed, and cost ~21 failures across three jobs.
"""
import os
import shutil
import subprocess
import sys

import pytest

from tcl_quote import posix_sep, tcl_path, tcl_word

_needs_tclsh = pytest.mark.skipif(shutil.which("tclsh") is None,
                                  reason="no tclsh on this host")

_PATHS = [
    # The escape reading.  `\a` is BEL, `\b` backspace, `\t` TAB, so the raw
    # form arrives as `D:uda<BS>uda<TAB>oolsuda.tcl` — a filename that never
    # existed, which is what windows-validate run 23 reported.
    r"D:\a\buda\buda\tools\buda.tcl",
    # The word split, and the one `tcl_word` alone does NOT cover: the
    # backslashes send it down the escaping path, which leaves the space bare.
    r"C:\Program Files\Python313\python.exe",
    # Both stages at once, already forward-slashed (what `.as_posix()` gives).
    "C:/Program Files/Python313/python.exe",
    # POSIX, where all of this is a no-op.
    "/home/u/buda/tools/buda.tcl",
    # A path a test could really be handed, carrying every character the
    # parser acts on: substitution, command substitution, separator.
    "/tmp/we ird/[x]/py$thon;puts hi",
    # A backslash that is NOT a separator, which is every backslash on POSIX
    # (Codex P2 on #734).  Respelling it there would name a different file.
    "/tmp/back\\slash/python",
]


def _same_file_spelling(raw):
    """The one string that names `raw`'s file on THIS platform.

    Respelled only where the backslash is a separator — which is the whole
    of what `tcl_path` may change, so this is the invariant stated once.
    """
    return raw.replace("\\", "/") if os.sep == "\\" else raw


@_needs_tclsh
@pytest.mark.parametrize("raw", _PATHS, ids=lambda v: v[:24])
def test_a_quoted_path_is_one_word_and_still_that_path(raw):
    script = "set L [list %s]\nputs \"[llength $L]|[lindex $L 0]\"" % tcl_path(raw)
    r = subprocess.run(["tclsh"], input=script, capture_output=True,
                       encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    n, _, got = r.stdout.strip().partition("|")
    assert n == "1", f"split into {n} words: {r.stdout}"
    assert got == _same_file_spelling(raw), r.stdout


@_needs_tclsh
def test_a_real_posix_path_with_a_backslash_in_it_still_opens(tmp_path):
    """The round trip against a file that EXISTS, not just a string compare:
    on POSIX `back\\slash` is one directory, and respelling it unconditionally
    pointed `source` at a path nothing had created."""
    if os.sep == "\\":
        pytest.skip("a backslash is a separator here, so no such filename")
    d = tmp_path / "back\\slash"
    d.mkdir()
    (d / "it.tcl").write_text('puts OPENED\n')
    r = subprocess.run(["tclsh"], input="source %s\n" % tcl_path(d / "it.tcl"),
                       capture_output=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "OPENED" in r.stdout, r.stdout + r.stderr


@_needs_tclsh
def test_the_raw_interpolation_this_replaces_really_does_lose_the_path():
    """The negative control.  Without it the test above proves only that some
    string survives, not that the quoting is what saves it."""
    raw = r"D:\a\buda\buda\tools\buda.tcl"
    r = subprocess.run(["tclsh"], input="puts [list %s]\n" % raw,
                       capture_output=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "buda.tcl" not in r.stdout, r.stdout      # the separators are gone


def test_posix_sep_respells_a_separator_and_keeps_a_filename_character():
    """The rule in one place, tested from both platforms' side.

    `sep` is injectable precisely so a POSIX host can hold the Windows half:
    the alternative is asserting about a branch nothing here executes.
    """
    # Where the backslash IS the separator, respell it.
    assert posix_sep(r"flow\rnr\mix.buda", sep="\\") == "flow/rnr/mix.buda"
    assert posix_sep(r"D:\a\buda\tools", sep="\\") == "D:/a/buda/tools"
    # Where it is NOT, it is part of the NAME and must survive.
    assert posix_sep("flow/back\\slash/x", sep="/") == "flow/back\\slash/x"
    # Already-`/` input is untouched either way.
    assert posix_sep("flow/rnr/mix.buda", sep="\\") == "flow/rnr/mix.buda"


def test_the_generated_origin_is_repo_spelled_not_platform_spelled(monkeypatch):
    """`origin` is stamped into line 1 of EVERY generated file and into
    `corpus::begin`, so a native spelling rewrites all 59 and the drift guard
    calls the whole corpus stale — which is what windows-validate run 24
    reported on this branch.  Simulated here by handing the translator the
    spelling a Windows `relpath` would return."""
    import importlib.util
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "b2t_origin", root / "tools" / "buda2tcl.py")
    b2t = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b2t)

    # `os` is one module object, so patching it here also patches the `os.sep`
    # that `posix_sep` reads — which is the point: it must respell under the
    # simulated separator, not under this host's.
    monkeypatch.setattr(b2t.os.path, "relpath",
                        lambda *a, **k: "flow\\rnr\\mix.buda")
    monkeypatch.setattr(b2t.os, "sep", "\\")
    _out, text, _refs = b2t.translate_file(
        str(root / "flow" / "four_blocks.buda"), "/tmp/out", str(root / "flow"))
    assert "flow/rnr/mix.buda" in text, text[:200]
    assert "flow\\rnr\\mix.buda" not in text, text[:200]


def test_a_whitespace_free_token_quotes_exactly_as_it_always_did():
    """`tcl_word`'s output is byte-load-bearing — `flow/tcl/corpus/` is
    checked in and a drift guard compares it against its `.buda` sources — so
    the whitespace handling `tcl_path` needs had to arrive as an opt-in."""
    for tok in ("bus[4]", "$x", "(SIGNAL", "0.5)x4", r"C:\foo;puts", "}x{"):
        assert tcl_word(tok) == tcl_word(tok, escape_ws=False)


def test_every_tcl_writing_test_goes_through_the_helper():
    """The defect was N private interpolations, so the fix is not N private
    corrections — it is that there is one helper and everybody calls it.

    The rule is on the interpolated VALUE, not on whether the file mentions
    the helper somewhere: an earlier cut asked the latter and stayed green
    with a reintroduced raw interpolation two lines below a `tcl_path` call.
    """
    from pathlib import Path
    here = Path(__file__).parent
    # Raw interpolations of a path into Tcl source.  None of these has a
    # legitimate form — a quoted value reads `{_PY_Q}` / `{_TCL_Q}`.
    raw = ("{sys.executable}", "% sys.executable", "%s\" % sys.executable",
           "{_TCL}", "{_BUDA_TCL}", "{_TOOLS / 'buda.tcl'}",
           "{_ROOT / 'tools' / 'buda.tcl'}")
    offenders = []
    for p in sorted(here.glob("test_*.py")):
        if p.name == Path(__file__).name:
            continue
        text = p.read_text(encoding="utf-8")
        if "buda::start" not in text:      # not a Tcl-writing test
            continue
        offenders += [f"{p.name}: {bad}" for bad in raw if bad in text]
    assert not offenders, offenders
