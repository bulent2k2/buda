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

"""`buda -v/--visualize`: open a viewer on the FINISHED design even when the
script has no `visualize`, so a test vehicle can be eyeballed without editing
it — unless the flow already ends by visualizing."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import buda_cli

_ROOT = Path(__file__).parents[2]
_CLI = _ROOT / "src" / "buda_cli.py"
_BTCL = _ROOT / "bin" / "btcl"
_BUDA_TCL = _ROOT / "tools" / "buda.tcl"

_FLOW = """\
def_layer 4 M4 H TOP 50
def_layer 5 M5 V TOP 50
add_block A 0 0 100 100
add_block B 400 0 500 100
add_bus x[4] A.p B.p
run_bundler strict
generate_topologies
run_planner
run_nuts
"""


# ── unit: the "does the flow already end by visualizing?" decision ──────────

@pytest.mark.parametrize("verbs, expected", [
    (["run_nuts"], False),                              # no viewer at all
    (["run_nuts", "visualize"], True),                  # last is visualize
    (["run_nuts", "visualize_topologies"], True),       # …or the explorer
    (["visualize", "exit"], True),                      # visualize just before exit
    (["visualize_topologies", "exit"], True),
    (["visualize", "run_nuts"], False),                 # viewer is mid-flow, more after
    (["visualize", "run_nuts", "exit"], False),         # exit NOT preceded by a viewer
    (["run_nuts", "exit"], False),
    ([], False),
])
def test_flow_ends_by_visualizing(verbs, expected):
    s = buda_cli.BudaSession()
    s._recent_verbs = list(verbs)
    assert s._flow_ends_by_visualizing() is expected


# ── integration: run the CLI headless (Agg → `visualize` self-skips with
#    BUDA-1903, so each attempt is countable and nothing blocks) ─────────────

def _run(tmp_path, body, *flags):
    script = tmp_path / "veh.buda"
    script.write_text(body)
    env = {**os.environ, "MPLBACKEND": "Agg",
           "PYTHONPATH": f"{_ROOT/'build'}:{_ROOT/'tools'}"}
    r = subprocess.run([sys.executable, str(_CLI), str(script), *flags],
                       capture_output=True, text=True, env=env)
    return r.returncode, r.stdout + r.stderr


def _viewer_attempts(out):
    # Headless, every `visualize` reports BUDA-1903 ("no window opened") once.
    return out.count("BUDA-1903")


@pytest.mark.mid
def test_no_v_flag_no_viewer(tmp_path):
    rc, out = _run(tmp_path, _FLOW)
    assert rc == 0
    assert _viewer_attempts(out) == 0


@pytest.mark.mid
def test_v_appends_viewer(tmp_path):
    rc, out = _run(tmp_path, _FLOW, "-v")
    assert rc == 0
    assert _viewer_attempts(out) == 1


@pytest.mark.mid
def test_v_no_double_when_flow_ends_with_visualize(tmp_path):
    rc, out = _run(tmp_path, _FLOW + "visualize\n", "-v")
    assert rc == 0
    assert _viewer_attempts(out) == 1          # the script's own, not doubled


@pytest.mark.mid
def test_v_no_double_when_visualize_just_before_exit(tmp_path):
    rc, out = _run(tmp_path, _FLOW + "visualize\nexit\n", "-v")
    assert _viewer_attempts(out) == 1


@pytest.mark.mid
def test_v_adds_final_viewer_when_visualize_is_mid_flow(tmp_path):
    body = _FLOW.replace("run_planner\n", "visualize\nrun_planner\n")
    rc, out = _run(tmp_path, body, "-v")
    assert _viewer_attempts(out) == 2          # the mid-flow one + the appended


@pytest.mark.mid
def test_v_preserves_a_nonzero_exit_code(tmp_path):
    rc, out = _run(tmp_path, _FLOW + "exit 3\n", "-v")
    assert _viewer_attempts(out) == 1          # viewer still opened…
    assert rc == 3                             # …and the exit code is preserved


@pytest.mark.mid
def test_v_and_no_viz_are_mutually_exclusive(tmp_path):
    rc, out = _run(tmp_path, _FLOW, "-v", "--no-viz")
    assert rc != 0
    assert "not allowed with" in out or "mutually exclusive" in out


# ── Tcl twin: `btcl -v flow.tcl` opens a viewer at buda::stop ───────────────

_tcl_required = pytest.mark.skipif(shutil.which("tclsh") is None,
                                   reason="no tclsh on this host")

_TCL_FLOW = f"""\
source {_BUDA_TCL}
buda::start
buda::def_layer 4 M4 H TOP 50
buda::def_layer 5 M5 V TOP 50
buda::add_block A 0 0 100 100
buda::add_block B 400 0 500 100
buda::add_bus {{x[4]}} A.p B.p
buda::run_bundler strict
buda::generate_topologies
buda::run_planner
buda::run_nuts
"""


def _run_btcl(tmp_path, tcl_body, *flags):
    script = tmp_path / "flow.tcl"
    script.write_text(tcl_body)
    env = {**os.environ, "MPLBACKEND": "Agg"}
    r = subprocess.run(["bash", str(_BTCL), *flags, str(script)],
                       capture_output=True, text=True, env=env, timeout=300)
    return r.returncode, r.stdout + r.stderr


@pytest.mark.mid
@_tcl_required
def test_btcl_no_v_no_viewer(tmp_path):
    _, out = _run_btcl(tmp_path, _TCL_FLOW + "buda::stop\n")
    assert _viewer_attempts(out) == 0


@pytest.mark.mid
@_tcl_required
def test_btcl_v_appends_viewer_at_stop(tmp_path):
    _, out = _run_btcl(tmp_path, _TCL_FLOW + "buda::stop\n", "-v")
    assert _viewer_attempts(out) == 1          # appended at buda::stop


@pytest.mark.mid
@_tcl_required
def test_btcl_v_no_double_when_flow_visualizes(tmp_path):
    body = _TCL_FLOW + "buda::visualize\nbuda::stop\n"
    _, out = _run_btcl(tmp_path, body, "-v")
    assert _viewer_attempts(out) == 1          # the flow's own, not doubled
