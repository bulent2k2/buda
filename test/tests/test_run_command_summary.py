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

"""The per-command terminal summary is the run's visible output.

Every command's output is captured into the flow log and only a one-line
headline reaches the terminal, so `_emit_cmd_summary` IS what a user sees a
`buda` run doing.  #643 nested that call inside the `--report-json` branch
while adding the machine-readable report, which silenced normal runs
completely — the flow still ran and still exited 0, and the only symptom was
an empty terminal between the banner and the runtime summary.

Four repro tests caught it downstream by asserting on phrases like `0 bits
unplaced`, but each of those is really testing its own topology fix.  This
file states the property directly, so the next person to touch the capture
path fails on the cause rather than on four unrelated symptoms.
"""
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).parents[2]
_CLI = _ROOT / "src" / "buda_cli.py"

_FLOW = """def_layer 4 M4 H TOP 30
def_layer 5 M5 V TOP 30
add_block a 0 0 10 10
add_block b 100 100 110 110
add_net n1 a.o b.i
run_bundler STRICT
generate_topologies
run_planner 1
run_nuts
"""


def _run(tmp_path, *flags):
    p = tmp_path / "t.buda"
    p.write_text(_FLOW)
    return subprocess.run([sys.executable, str(_CLI), "--no-viz", *flags,
                           str(p)], capture_output=True, text=True,
                          cwd=str(tmp_path))


def _live_part(out):
    """Everything printed BEFORE the end-of-run runtime table.

    The distinction matters: the runtime table names every command too, so
    asserting "run_nuts appears in the output" passes even when the live
    lines are gone.  That is precisely how the regression survived — the
    symptom is an empty gap here, not a missing command name."""
    banner = out.find("Runtime summary")
    return out if banner < 0 else out[:banner]


def test_a_plain_run_reports_each_command_as_it_finishes(tmp_path):
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout[-2000:]
    live = _live_part(r.stdout)
    for cmd in ("run_bundler", "generate_topologies", "run_planner",
                "run_nuts"):
        assert cmd in live, (
            f"no live summary line for {cmd} — only the end-of-run table:\n"
            f"{r.stdout}")


def test_the_live_lines_carry_results_not_just_names(tmp_path):
    """A headline's value is the one line of RESULT a user gets without
    opening the flow log: how many bundles, how many topologies, whether NUTS
    overlapped.  Command names alone are what the runtime table already
    gives."""
    live = _live_part(_run(tmp_path).stdout)
    assert "Bundler created" in live, live
    assert "topologies for bundle" in live, live
    assert "segments placed" in live, live


def test_reporting_does_not_depend_on_report_json(tmp_path):
    """The regression exactly: the headline emission moved under the
    `--report-json` guard, so an optional machine-readable flag silently
    decided whether the tool narrated its run at all."""
    plain = _live_part(_run(tmp_path).stdout)
    with_json = _live_part(_run(tmp_path, "--report-json",
                                str(tmp_path / "r.json")).stdout)
    for phrase in ("Bundler created", "segments placed"):
        assert phrase in plain, plain
        assert phrase in with_json, with_json
