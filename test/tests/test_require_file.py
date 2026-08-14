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

"""`require_file`, and what a run looks like when it stops.

The reported shape (`flow/ariane133`, whose netlist and macro LEF are fetched
from upstream and deliberately not checked in): a fresh clone runs the flow,
three commands go by, a runtime summary prints, and the terminal reads like a
short clean run.  The reader HAD complained — the LEF it could not open — but
the complaint arrived as a Python traceback, after the summary saying nothing
had gone wrong, through frames the user did not write.

Three separate things are pinned here.

1. A flow can DECLARE its inputs, with the remedy, and stop on line one.  The
   importer's own message is about a path; where the file comes from is the
   flow's knowledge, not the engine's.
2. A raising command is reported as an identified diagnostic BEFORE the end
   report, so the summary reads as the tail of a failed run.
3. A command that ENDED the run puts its full detail on the terminal.  The
   one-line headline is right for a command that worked; for the reason a run
   stopped, sending the user to a log file sends them away from the only
   thing they need.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

from subprocess_env import buda_env

_ROOT = Path(__file__).parents[2]
CLI = _ROOT / "src" / "buda_cli.py"


def _run(tmp_path, body, name="flow.buda", env=None):
    script = tmp_path / name
    script.write_text(body)
    e = buda_env(_ROOT)
    e.update(env or {})
    r = subprocess.run([sys.executable, str(CLI), "--no-viz", str(script)],
                       capture_output=True, text=True, env=e)
    return r


# ── 1. the precondition ──────────────────────────────────────────────────────
def test_a_missing_required_file_stops_the_run_with_the_remedy(tmp_path):
    r = _run(tmp_path, "require_file netlist.v macro.lef "
                       "hint Fetch them: python3 fetch.py\n"
                       "def_layer 4 M4 H TOP 10\n")
    assert r.returncode == 1, r.stdout
    out = r.stdout + r.stderr
    assert "BUDA-1905" in out and "FATAL" in out
    # EVERY missing file, not the first — fetching two inputs should not cost
    # two failed runs.
    assert "netlist.v" in out and "macro.lef" in out
    assert "2 required input file(s)" in out
    # The remedy is the point of the command.
    assert "python3 fetch.py" in out


def test_the_run_stops_before_the_next_command(tmp_path):
    r = _run(tmp_path, "require_file gone.v\n"
                       "def_layer 4 M4 H TOP 10\n"
                       "add_block A 0 0 10 10\n")
    assert r.returncode == 1
    # `add_block` never ran: it is absent from the runtime summary, which is
    # the whole claim — a declared precondition stops the flow where it is
    # declared, not after the setup that precedes the read.
    assert "add_block" not in r.stdout


def test_a_satisfied_precondition_is_silent(tmp_path):
    (tmp_path / "there.v").write_text("// present\n")
    r = _run(tmp_path, "require_file there.v hint never printed\n"
                       "def_layer 4 M4 H TOP 10\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "BUDA-1905" not in r.stdout
    assert "never printed" not in r.stdout


def test_paths_resolve_against_the_script_not_the_cwd(tmp_path):
    """THE path rule, the same one every path-taking command follows: a
    required path must mean the file the import command will actually read."""
    sub = tmp_path / "flow"
    sub.mkdir()
    (sub / "beside.v").write_text("// beside the script\n")
    script = sub / "f.buda"
    script.write_text("require_file beside.v\ndef_layer 4 M4 H TOP 10\n")
    # Run from a directory where `beside.v` does NOT exist.
    r = subprocess.run([sys.executable, str(CLI), "--no-viz", str(script)],
                       capture_output=True, text=True, cwd=str(tmp_path),
                       env=buda_env(_ROOT))
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_missing_path_is_reported_resolved(tmp_path):
    """A bare name and the absolute path it resolved to — the second is what
    turns 'no such file' into something a user can act on."""
    r = _run(tmp_path, "require_file gone.v\n")
    out = r.stdout + r.stderr
    assert "gone.v" in out
    assert str(tmp_path / "gone.v") in out


def test_no_path_is_an_error_not_a_silent_pass(tmp_path):
    r = _run(tmp_path, "require_file\ndef_layer 4 M4 H TOP 10\n")
    assert "Error: require_file requires at least one path" in r.stdout
    r = _run(tmp_path, "require_file hint nothing before it\n")
    assert "Error: require_file: no path before 'hint'" in r.stdout


# ── 2. a raising command is a diagnostic, not a traceback ───────────────────
_RAISES = """\
open_bdb {bdb}
def_layer 4 M4 H TOP 10
import_def_lef no_such.def no_such.lef
def_layer 5 M5 V TOP 10
"""


def test_an_engine_error_is_identified_and_precedes_the_summary(tmp_path):
    r = _run(tmp_path, _RAISES.format(bdb=tmp_path / "x.bdb"))
    assert r.returncode == 1
    out = r.stdout
    assert "BUDA-1906" in out, out
    # BEFORE the end report: the summary lists the commands that DID run, and
    # read on its own it looks like a short clean flow.
    assert out.index("BUDA-1906") < out.index("Runtime summary"), out
    # The engine's own message survives — the id adds identity, it does not
    # replace what the reader said.
    assert "no_such.lef" in out


def test_the_traceback_is_in_the_log_and_off_the_terminal(tmp_path):
    r = _run(tmp_path, _RAISES.format(bdb=tmp_path / "x.bdb"))
    assert "Traceback (most recent call last)" not in r.stdout + r.stderr
    log = (tmp_path / "log" / "flow_flow.log").read_text()
    assert "Traceback (most recent call last)" in log, \
        "the traceback must stay available where the detail lives"


def test_buda_traceback_env_brings_it_back(tmp_path):
    r = _run(tmp_path, _RAISES.format(bdb=tmp_path / "x.bdb"),
             env={"BUDA_TRACEBACK": "1"})
    assert "Traceback (most recent call last)" in r.stdout + r.stderr


# ── 3. a failed command's detail reaches the terminal ───────────────────────
def test_a_failing_command_prints_its_full_detail(tmp_path):
    """The headline is truncated to fit one line, so the hint — the actionable
    half — can be cut off it entirely."""
    long_hint = "run this exact thing: python3 some/rather/long/path/fetch.py"
    r = _run(tmp_path, f"require_file gone.v hint {long_hint}\n")
    assert long_hint in r.stdout, \
        "the remedy did not reach the terminal (log-only is not enough)"


def test_a_clean_exit_is_not_treated_as_a_failure(tmp_path):
    """`exit` with code 0 is 18 flows' normal terminator, not a failure."""
    r = _run(tmp_path, "def_layer 4 M4 H TOP 10\nexit\n")
    assert r.returncode == 0
    assert r.stdout.count("Exiting on 'exit' command") == 1, \
        "a clean exit's line was echoed twice (treated as a failing command)"
