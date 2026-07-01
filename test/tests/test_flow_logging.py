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

"""The CLI splits its output: the terminal carries only an abstract per-command
summary + a final runtime table, while the full detail (planner/NUTS/… lines,
including C++ output) is written to <script>_flow.log — no longer duplicated.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).parents[2]
CLI = _ROOT / "src" / "buda_cli.py"

_FLOW = """\
def_layer 4 M4 H TOP 10
def_layer 5 M5 V TOP 10
add_block A 0 0 100 100
add_block B 400 0 500 100
add_bus x[4] A.o B.i
run_bundler
generate_topologies
run_planner 1
run_nuts
check_connectivity nuts
"""


def _run(tmp_path):
    script = tmp_path / "flow.buda"
    script.write_text(_FLOW)
    build_dir, tools_dir = _ROOT / "build", _ROOT / "tools"
    ppath = os.environ.get("PYTHONPATH", "")
    env = {**os.environ,
           "PYTHONPATH": f"{build_dir}:{tools_dir}:{ppath}".rstrip(":")}
    r = subprocess.run([sys.executable, str(CLI), "--no-viz", str(script)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, f"flow failed:\n{r.stdout}{r.stderr}"
    log = (tmp_path / "log" / "flow_flow.log")
    assert log.exists(), "flow log was not written"
    return r.stdout, log.read_text()


def test_terminal_shows_summary_not_detail(tmp_path):
    stdout, _ = _run(tmp_path)
    # A one-line abstract summary per significant command (cmd + runtime).
    assert re.search(r"run_nuts\s+\d+\.\d+s", stdout), stdout
    assert re.search(r"run_planner 1\s+\d+\.\d+s", stdout), stdout
    # The final runtime table is on the terminal.
    assert "Runtime summary" in stdout
    assert re.search(r"total \(\d+ commands\)", stdout)
    # But the verbose per-layer NUTS detail is NOT dumped to the terminal.
    assert "total bus width" not in stdout, \
        "per-layer NUTS detail leaked to the terminal (should be log-only)"


def test_log_captures_full_detail(tmp_path):
    _, log = _run(tmp_path)
    # Full detail lives in the log …
    assert "segments placed" in log
    assert "total bus width" in log          # per-layer NUTS detail
    assert "Success: no opens found." in log
    # … under per-command headers, each with a runtime footer.
    assert "━━━ run_nuts ━━━" in log
    assert re.search(r"\[runtime\] run_nuts: \d+\.\d+s", log)


def test_no_duplication_between_terminal_and_log(tmp_path):
    stdout, log = _run(tmp_path)
    # The headline (e.g. the NUTS summary line) appears once on the terminal
    # and once in the log — never the whole detailed block twice.
    detail_lines = [ln for ln in log.splitlines() if "total bus width" in ln]
    assert detail_lines, "expected per-layer NUTS detail in the log"
    for ln in detail_lines:
        assert ln not in stdout
