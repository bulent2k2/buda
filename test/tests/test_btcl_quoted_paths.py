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

"""`btcl -i` reads `.buda` lines too — with the same rule the engine uses.

The path-quoting work (#771/#772/#775) gave the engine one tokenizer and gave
four TOOL readers of `.buda` a shared helper.  `tools/buda_interact.tcl` is
the fifth reader and is written in Tcl, so no shared-helper fix could reach
it: it split a recorded `open_bdb "my designs/ck.bdb"` on whitespace, took
the path as `"my`, and resolved that against the flow's directory.

The damage was not the banner it printed.  No `.trace` was written beside the
checkpoint, so every later `btcl -i <flow> <ckpt> <stage>` refused with "run a
build session first" — naming the session that had just run.  A spaced
checkpoint path was an unbreakable loop.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from buda_script import split_quoted_args
from tcl_quote import tcl_path
from wrapper_select import wrapper_command, wrapper_missing_reason

_ROOT = Path(__file__).resolve().parents[2]
_DRIVER = _ROOT / "tools" / "buda_interact.tcl"

# mid tier: each test drives a real tclsh + engine subprocess through a whole
# flow, like the rest of the front-end suite.
# The PLATFORM's launcher, never `bin/btcl` by path: that one is a bash
# script, and Windows cannot execute it -- `subprocess` raises `WinError
# 193: %1 is not a valid Win32 application` before the test does anything
# (measured, windows-validate run 36: all four tests here).  The `.ps1`
# twin exists precisely for this, and `test_tcl_interact` already selects
# it the same way.
_BTCL_CMD = wrapper_command(_ROOT, "btcl")

pytestmark = [pytest.mark.mid,
              pytest.mark.skipif(shutil.which("tclsh") is None,
                                 reason="no tclsh on this host"),
              pytest.mark.skipif(_BTCL_CMD is None,
                                 reason=wrapper_missing_reason("btcl"))]

_FLOW = """def_layer 4 M4 H TOP 30
def_layer 5 M5 V TOP 30
add_block a 0 0 100 100
add_block b 300 0 400 100
add_net n1 a.o b.i
run_bundler STRICT
generate_topologies
run_planner 3
run_nuts
"""

# The cases that separate the rule from a plain split.  Kept here rather than
# in either implementation so neither can quietly become its own oracle.
_CASES = [
    'open_bdb "my designs/ck.bdb" writeback',
    'open_bdb plain.bdb',
    'import_def_lef "rev 2/a.def" "rev 2/b.lef" no_tracks',
    'require_file "inputs/rev #2/top.v"',
    'open_bdb x.bdb  # a note',
    "add_block a's_b 0 0 1 1 # note",
    'open_bdb "unterminated',
    'run_planner 3',
    "import_def_lef 'a b.def' c.lef",
]


def _btcl_i(tmp_path, *args, stdin="done\n", timeout=300):
    """Run `btcl -i` in tmp_path and return its combined output."""
    p = subprocess.run([*_BTCL_CMD, "-i", *args],
                       cwd=tmp_path, input=stdin, text=True,
                       capture_output=True, timeout=timeout)
    return p.stdout + p.stderr


def _tcl_brace(s):
    """A Tcl literal for `s` — braced, since none of the cases carry braces."""
    assert "{" not in s and "}" not in s and "\\" not in s
    return "{" + s + "}"


def test_the_tcl_tokenizer_agrees_with_the_engines(tmp_path):
    """One rule, two languages — so the agreement is MEASURED, not asserted.

    A Tcl copy of `split_quoted_args` is the only way to give the driver the
    engine's reading (the alternative, asking the engine over the pipe, buys
    one implementation at the cost of a round trip during trace analysis).
    The cost of a copy is drift, and this makes drift a test failure instead
    of a wrong checkpoint path.

    Tokens are printed ONE PER LINE rather than as a Tcl list: comparing the
    list form would mean reimplementing Tcl's own quoting rules here, and
    that second oracle was wrong on the first try (`"unterminated`).
    """
    # Source ONLY the proc, not the driver — sourcing the driver runs a
    # session.  Extracting it also proves the proc is self-contained.
    text = _DRIVER.read_text().splitlines()
    a = next(i for i, l in enumerate(text) if l.startswith("proc _split_args"))
    b = next(i for i in range(a, len(text)) if text[i] == "}")
    body = text[a:b + 1]
    for case in _CASES:
        body.append("foreach t [_split_args " + _tcl_brace(case)
                    + "] { puts \"TOK:$t\" }")
        body.append('puts "END"')
    script = tmp_path / "probe.tcl"
    script.write_text("\n".join(body) + "\n")

    out = subprocess.run(["tclsh", str(script)], text=True,
                         capture_output=True, timeout=120)
    assert out.returncode == 0, out.stderr

    got, cur = [], []
    for line in out.stdout.splitlines():
        if line == "END":
            got.append(cur)
            cur = []
        else:
            assert line.startswith("TOK:"), line
            cur.append(line[4:])
    assert len(got) == len(_CASES), out.stdout
    for case, tokens in zip(_CASES, got):
        assert tokens == split_quoted_args(case), case


def test_a_spaced_checkpoint_gets_a_trace_and_resumes(tmp_path):
    """The end-to-end failure: BUILD then RESUME across a spaced path.

    The trace is the assertion that matters — the banner was only the
    symptom.  Without it the stage session refuses, so this test fails at the
    resume even if the message were somehow repaired on its own.
    """
    (tmp_path / "my designs").mkdir()
    (tmp_path / "flow.buda").write_text(_FLOW)
    ck = "my designs/ck.bdb"

    out = _btcl_i(tmp_path, "flow.buda", ck)
    assert (tmp_path / ck).exists(), out
    assert (tmp_path / (ck + ".trace")).exists(), \
        f"no build trace beside a spaced checkpoint\n{out}"
    # The banner names the path the user gave, not a fragment of it.
    assert "designs/ck.bdb" in out and '/"' not in out, out
    # …and no false "the flow opened its own BDB after the armed one": this
    # flow opens none, and the claim only appeared because the mangled path
    # differed from the armed one.
    assert "opened its own BDB" not in out, out

    # The resume the banner advertises actually runs.
    out2 = _btcl_i(tmp_path, "flow.buda", ck, "plan")
    assert "run a build session first" not in out2, out2
    assert "pin/edit prompt" in out2, out2


def test_the_printed_resume_recipe_is_a_command_you_can_type(tmp_path):
    """A recipe is advice, and unquoted a spaced path makes it four shell
    words.  Extract the line the driver printed and RUN it."""
    (tmp_path / "my designs").mkdir()
    (tmp_path / "flow.buda").write_text(_FLOW)
    out = _btcl_i(tmp_path, "flow.buda", "my designs/ck.bdb")
    recipe = next((l for l in out.splitlines() if "or resume: btcl -i" in l),
                  None)
    assert recipe, out
    cmd = recipe.split("or resume: ", 1)[1].split(") and they hold")[0]
    assert "'" in cmd, f"a spaced path went unquoted in the advice: {cmd}"
    # The QUOTING above is asserted everywhere -- it is a property of what
    # the driver PRINTED.  Only running it is POSIX-shell-specific: this
    # executes the line through `bash`, which is how a user on this platform
    # would type it.  A Windows user pastes the same line into PowerShell,
    # whose word-splitting and quoting are different rules; asserting that
    # here would be testing a shell we never taught the driver to quote for
    # (windows-validate run 36).
    if sys.platform == "win32":
        pytest.skip("the printed recipe is executed here through a POSIX "
                    "shell; the quoting assertion above still ran")
    p = subprocess.run(["bash", "-c",
                        cmd.replace("btcl", str(_ROOT / "bin" / "btcl"), 1)],
                       cwd=tmp_path, input="done\n", text=True,
                       capture_output=True, timeout=300)
    assert "run a build session first" not in p.stdout + p.stderr, p.stdout
    assert "pin/edit prompt" in p.stdout, p.stdout + p.stderr


def test_an_unspaced_checkpoint_is_untouched(tmp_path):
    """The other half: every existing flow has no space, and its banner must
    not grow quotes it never had."""
    (tmp_path / "flow.buda").write_text(_FLOW)
    out = _btcl_i(tmp_path, "flow.buda", "ck.bdb")
    recipe = next(l for l in out.splitlines() if "or resume: btcl -i" in l)
    assert "'" not in recipe and '"' not in recipe, recipe
    assert (tmp_path / "ck.bdb.trace").exists(), out


def test_a_shell_metacharacter_in_the_path_is_not_executed(tmp_path):
    """A recipe is typed into a shell, so quoting it wrong is not cosmetic
    (Codex #783 P2).  Double quotes still expand `$(...)`, backticks and
    `$VAR`, so a checkpoint named `ck $(id).bdb` printed a recipe that RUNS
    something; a path containing a `"` came back unquoted altogether.

    The assertion is the whole point of the claim "a command you can type":
    the printed recipe is handed to a real shell and must name the file the
    driver meant, with no substitution performed.
    """
    (tmp_path / "flow.buda").write_text(_FLOW)
    ck = "ck $(id).bdb"                       # legal filename, hostile word
    out = _btcl_i(tmp_path, "flow.buda", ck)
    assert (tmp_path / ck).exists(), out      # the engine took it literally

    recipe = next(l for l in out.splitlines() if "or resume: btcl -i" in l)
    cmd = recipe.split("or resume: ", 1)[1].split(") and they hold")[0]
    # What a shell makes of the printed words — `printf %s\n` per word, so
    # substitution would show up as a changed or extra word.
    words = subprocess.run(
        ["bash", "-c", f"printf '%s\n' {cmd}"], text=True,
        capture_output=True, timeout=60).stdout.splitlines()
    assert words[-2:] == [str(tmp_path / ck), "plan"], (cmd, words)
    assert "uid=" not in " ".join(words), f"the recipe ran a command: {words}"


def test_buda2tcl_keeps_a_quoted_path_one_argument():
    """The fifth reader's sibling in the fourth: `buda2tcl` translated
    `open_bdb "my designs/ck.bdb" writeback` into THREE Tcl arguments with
    stray quote characters, so the generated flow opened a file nobody has.
    Latent — no checked-in flow uses a quoted path — which is exactly why it
    needs a test rather than a corpus diff."""
    sys.path.insert(0, str(_ROOT / "tools"))
    import buda2tcl

    line, _ref, _end = buda2tcl.translate_line(
        'open_bdb "my designs/ck.bdb" writeback', ".", ".", ".")
    assert line == ["buda::open_bdb {my designs/ck.bdb} {writeback}"], line

    line, _ref, _end = buda2tcl.translate_line(
        'import_def_lef "rev 2/a.def" "rev 2/b.lef"', ".", ".", ".")
    assert line == ["buda::import_def_lef {rev 2/a.def} {rev 2/b.lef}"], line


def test_buda2tcl_does_not_re_split_a_backslash_path():
    """The token that cannot be brace-quoted (Codex #783 P2).

    `tcl_word` falls back to character escaping for a token carrying a
    backslash, and its `escape_ws` was off because the translator's tokens
    used to be whitespace-split — a premise this PR removed.  So the ordinary
    Windows spelling `C:\\Program Files\\ck.bdb` came out with a bare space
    and Tcl read three arguments.  The `tcl_word` docstring names this exact
    case; what changed is that the translator became it.
    """
    sys.path.insert(0, str(_ROOT / "tools"))
    import buda2tcl

    line, _ref, _end = buda2tcl.translate_line(
        r'open_bdb "C:\Program Files\ck.bdb" writeback', ".", ".", ".")
    assert line == [r"buda::open_bdb C:\\Program\ Files\\ck.bdb {writeback}"], \
        line
