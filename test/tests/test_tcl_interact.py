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

"""`btcl -i <flow>.buda` — interactive iteration on ANY .buda flow.

design.tcl/hdesign.tcl carry their own design and route recipe; the
interact driver (tools/buda_interact.tcl) carries neither.  It runs an
arbitrary flow verbatim via the engine's `source`, learns what the flow DID
from the recorder (BUDA_RECORD at do_command — loops unrolled, source trees
flattened), and derives from that: hier vs flat, the flow's own routing
tail as the prompt's `replan`, whether it routed at all, and where its
checkpoint lives.  Then the shared prompt (flow/tcl/prompt.tcl) takes
over — the same loop the vehicles use, so a pin means the same thing in
all three drivers.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_DRIVER = _ROOT / "tools" / "buda_interact.tcl"
_BTCL = _ROOT / "bin" / "btcl"

pytestmark = [pytest.mark.mid,
              pytest.mark.skipif(shutil.which("tclsh") is None,
                                 reason="no tclsh on this host")]

# flow/tcl/design.tcl's design as a plain .buda — proven clean, with enough
# candidates per bundle for a pin to be a real choice.
_FLAT_FLOW = """\
def_layer 2 M2 H 55.56
def_layer 3 M3 V 55.56
def_layer 4 M4 H 55.56
def_layer 5 M5 V TOP 50
def_layer 6 M6 H TOP 52.94
def_layer 7 M7 V TOP 56.10
def_track_pattern 2 -100 POWER 2 1 (SIGNAL 1 0.5)x4 GROUND 2 1 (SIGNAL 1 0.5)x4
def_track_pattern 3 0 POWER 2 1 (SIGNAL 1 0.5)x4 GROUND 2 1 (SIGNAL 1 0.5)x4
def_track_pattern 4 -200 POWER 2 1 (SIGNAL 1 0.5)x4 GROUND 2 1 (SIGNAL 1 0.5)x4
def_track_pattern 5 0 POWER 3 1 (SIGNAL 2 1)x4 GROUND 3 1 (SIGNAL 2 1)x4
def_track_pattern 6 -400 POWER 4 1 (SIGNAL 2 1)x4 GROUND 4 1 (SIGNAL 2 1)x4
def_track_pattern 7 -600 POWER 6 2 (SIGNAL 3 2)x3 GROUND 2 1 (SIGNAL 3 2)x3
corner_margin dx 5 dy 5
set_min_stub_length 2
set_planner_param healersAhead 1
add_block cpu 50 50 250 250
add_block mem0 550 50 750 250
add_block mem1 550 350 750 550
add_block dsp 50 350 250 550
add_block io 330 20 470 120
add_block noc 330 480 470 580
add_bus d0[8] cpu.d0 mem0.d0
add_bus d1[8] cpu.d1 mem1.d1
add_bus w[4] dsp.w mem1.w
add_bus io_in[4] io.i cpu.i
add_bus io_out[4] cpu.o io.o
add_bus n0[4] noc.a dsp.a
add_bus n1[4] noc.b mem1.b
add_net irq io.irq dsp.irq
run_bundler STRICT
generate_topologies
run_planner 5
run_nuts
check_design nuts
run_detailed_nuts
check_design dnuts
"""


def _run(cmd, tmp_path, stdin="done\n"):
    import os
    return subprocess.run([*map(str, cmd)], input=stdin, capture_output=True,
                          encoding="utf-8", errors="replace", cwd=tmp_path,
                          timeout=900, env={**os.environ})


def test_flat_flow_pin_replan_verdict(tmp_path):
    flow = tmp_path / "mini.buda"
    flow.write_text(_FLAT_FLOW)
    r = _run(["tclsh", _DRIVER, flow], tmp_path, stdin="pin d1 4\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FLAT flow" in r.stdout
    assert "`replan` replays the flow's own routing tail" in r.stdout
    assert "no file-backed BDB" in r.stdout        # no open_bdb in the flow
    assert "pins changed since the last route" in r.stdout
    assert "topo 4 of" in r.stdout                 # the replay honored the pin
    # The replay is the FLOW's tail: its checks and detailed stage re-ran.
    assert "replay> run_detailed_nuts" in r.stdout
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout


def test_hier_flow_is_autodetected_and_replans_hier(tmp_path):
    # The unmodified hbundles cross-level case: 3 levels, cell-local template
    # + cross-level bundles, :memory: BDB, ends in `visualize` (headless note).
    flow = _ROOT / "flow" / "hbundles" / "08_cross_level.buda"
    r = _run(["tclsh", _DRIVER, flow], tmp_path, stdin="pin b_lohi 2\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "HIER flow" in r.stdout
    assert "starting `run_planner hier 5`" in r.stdout
    assert "expanded instances" in r.stdout        # the template pin fanned out
    assert r.stdout.count("topo 2 of 5") >= 4, "the replay lost the pin"


def test_a_flow_that_never_planned_has_no_replan_and_no_verdict(tmp_path):
    flow = tmp_path / "partial.buda"
    flow.write_text("def_layer 4 M4 H TOP 30\n"
                    "add_block A 0 0 100 100\n")
    r = _run(["tclsh", _DRIVER, flow], tmp_path, stdin="replan\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "never ran the planner" in r.stdout
    assert "no replan recipe" in r.stdout          # the verb says so, politely
    assert "did not route" in r.stdout and "no verdict" in r.stdout


def test_btcl_dash_i_wraps_the_driver_and_refuses_tcl(tmp_path):
    flow = tmp_path / "mini.buda"
    flow.write_text(_FLAT_FLOW)
    r = _run(["bash", _BTCL, "-i", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FLAT flow" in r.stdout

    tclflow = tmp_path / "flow.tcl"
    tclflow.write_text("puts hi\n")
    r = _run(["bash", _BTCL, "-i", tclflow], tmp_path)
    assert r.returncode == 2
    assert "prompt.tcl" in r.stderr           # the message names the path
