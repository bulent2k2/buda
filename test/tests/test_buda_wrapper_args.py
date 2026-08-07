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

"""bin/buda's macOS .app relaunch arg handling (BUDA_TEST_ANCHOR hook).

The Darwin branch anchors relative path args to the invoking cwd (the .app
launches elsewhere) and detects the script arg for the Dock cell name.  Both
loops must skip the VALUES of non-path value-taking options: `buda -j 4
flow/x.buda` once rewrote the 4 to "$PWD/4" ("invalid int value" on macOS)
and would have named the Dock cell "4".  The BUDA_TEST_ANCHOR hook prints
the computed SCRIPT_ARG and anchored args on any OS, so this logic is
CI-testable where the Darwin gate itself cannot run.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

# bin/buda is a Bash script; on native Windows `bash` resolves to the
# System32 WSL stub, which prints "Windows Subsystem for Linux has no
# installed distributions" instead of running anything (measured,
# windows-validate run 18 — all 5 tests fail on that output).  The docs
# already state the bin/ wrappers do not apply on native Windows; Cygwin
# (sys.platform == "cygwin") has a real bash and is not skipped.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="bin/buda needs a real bash; Windows' `bash` is the WSL stub "
           "(measured, windows-validate run 18)")

_BUDA = str(Path(__file__).parents[2] / "bin" / "buda")


def _anchor(args, cwd):
    out = subprocess.run(
        ["bash", _BUDA, *args], capture_output=True, text=True, timeout=30,
        cwd=str(cwd), env={**os.environ, "BUDA_TEST_ANCHOR": "1"})
    lines = out.stdout.splitlines()
    assert lines and lines[0].startswith("SCRIPT_ARG="), out.stdout
    return lines[0].removeprefix("SCRIPT_ARG="), lines[1:]


def test_threads_value_is_not_a_path(tmp_path):
    """The macOS repro: `buda -j 4 flow.buda` must keep the 4 verbatim and
    detect flow.buda (not 4) as the script."""
    (tmp_path / "flow.buda").write_text("exit\n")
    script, args = _anchor(["-j", "4", "flow.buda"], tmp_path)
    assert script == "flow.buda"
    assert args == ["-j", "4", str(tmp_path / "flow.buda")]
    # Long form too.
    script, args = _anchor(["--threads", "4", "flow.buda"], tmp_path)
    assert script == "flow.buda"
    assert args == ["--threads", "4", str(tmp_path / "flow.buda")]


def test_threads_value_untouched_even_if_a_file_named_like_it_exists(tmp_path):
    # A file literally named "4" in cwd must not turn the thread count into
    # an absolute path either — the value skip runs before the -e check.
    (tmp_path / "4").write_text("")
    (tmp_path / "flow.buda").write_text("exit\n")
    _, args = _anchor(["-j", "4", "flow.buda"], tmp_path)
    assert args[1] == "4"


def test_tag_value_still_passes_verbatim(tmp_path):
    (tmp_path / "flow.buda").write_text("exit\n")
    script, args = _anchor(["-t", "exp1", "flow.buda"], tmp_path)
    assert script == "flow.buda"
    assert args == ["-t", "exp1", str(tmp_path / "flow.buda")]


def test_relative_script_and_buda_less_form_are_anchored(tmp_path):
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "foo.buda").write_text("exit\n")
    _, args = _anchor(["demo/foo.buda"], tmp_path)
    assert args == [str(tmp_path / "demo" / "foo.buda")]
    _, args = _anchor(["demo/foo"], tmp_path)          # .buda-less form
    assert args == [str(tmp_path / "demo" / "foo.buda")]


def test_attached_and_option_forms_pass_through(tmp_path):
    (tmp_path / "flow.buda").write_text("exit\n")
    script, args = _anchor(["-j4", "--threads=8", "-nv", "flow.buda"], tmp_path)
    assert script == "flow.buda"
    assert args[:3] == ["-j4", "--threads=8", "-nv"]
