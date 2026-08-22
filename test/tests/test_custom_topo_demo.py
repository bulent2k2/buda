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

"""docs/CUSTOM_TOPOLOGIES_GUIDE.md's worked example, pinned so the transcripts cannot rot.

demo/custom_topo.buda is the guide's runnable companion.  The guide walks a
reader through: inspect the pool, pin candidate 4 (Z_VHV), drop the two stub
layers to M3 in a TopoEdit session, `edit_commit pin`, re-plan clean, and prove
the pin + forced layers survive a session boundary.  These tests hold each of
those claims against the real tool, in the same shapes the guide prints —
through the same `btcl -b` / `btcl -r -s plan` doors the guide walks.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from wrapper_select import wrapper_command, wrapper_missing_reason
from subprocess_env import buda_env

_ROOT = Path(__file__).parents[2]
_FLOW = _ROOT / "demo" / "custom_topo.buda"

_BTCL_CMD = wrapper_command(_ROOT, "btcl")


def _btcl_missing():
    """Empty string when btcl is runnable here — the wrapper needs BOTH a
    launcher and tclsh (the test_viz_final pattern, so the skip reason names
    the prerequisite that is actually missing)."""
    if shutil.which("tclsh") is None:
        return "no tclsh on this host"
    if _BTCL_CMD is None:
        return wrapper_missing_reason("btcl")
    return ""


_BTCL_MISSING = _btcl_missing()
_btcl_required = pytest.mark.skipif(bool(_BTCL_MISSING),
                                    reason=_BTCL_MISSING or "btcl is runnable")


def _flow_copy(tmp_path):
    """The flow is self-contained (no source/require_file), so a copy runs
    anywhere — and keeps each test's flow log out of the repo's flow/log/."""
    dst = tmp_path / "custom_topo.buda"
    shutil.copy(_FLOW, dst)
    return dst


@pytest.mark.mid
def test_baseline_flow_runs_clean(tmp_path):
    """The vehicle's own promise: the plain flow (no pins, no edits) reaches
    detailed NUTS and audits clean."""
    flow = _flow_copy(tmp_path)
    env = buda_env(_ROOT, "build", "src")
    r = subprocess.run(
        [sys.executable, str(_ROOT / "src" / "buda_cli.py"),
         "--no-viz", str(flow)],
        capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode == 0, (r.returncode, r.stdout[-800:], r.stderr[-400:])
    assert "Success: no violations found" in r.stdout, r.stdout[-800:]
    assert "0 bits unplaced" in r.stdout, r.stdout[-800:]


@pytest.mark.mid
def test_scripted_customizing_tail_pins_and_forces_layers(tmp_path):
    """The guide's 'same thing as a plain script' section: pin candidate 4,
    re-layer the stubs in an edit session, commit-with-pin, re-plan — clean,
    with the commit reporting the forced layers."""
    flow = _flow_copy(tmp_path)
    steer = tmp_path / "steer.buda"
    steer.write_text(
        "source custom_topo.buda\n"
        "select_topology a_0 4\n"
        "run_planner 5\n"
        "edit_topology 1\n"
        "edit_set_layer 0 3\n"
        "edit_set_layer 2 3\n"
        "edit_commit pin\n"
        "run_planner 5\n"
        "run_nuts\n"
        "run_detailed_nuts\n"
        "check_design\n")
    env = buda_env(_ROOT, "build", "src")
    r = subprocess.run(
        [sys.executable, str(_ROOT / "src" / "buda_cli.py"),
         "--no-viz", str(steer)],
        capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode == 0, (r.returncode, r.stdout[-800:], r.stderr[-400:])
    out = r.stdout
    # the edit_commit pin trio the guide highlights
    assert "Pinned 3 segment layer(s)" in out, out[-1200:]
    # …and the steered result still audits clean
    assert "Success: no violations found" in out, out[-1200:]
    assert flow.exists()  # the copy, not the checked-in flow, took the logs


@pytest.mark.mid
@_btcl_required
def test_pin_and_forced_layers_survive_a_session_boundary(tmp_path):
    """The guide's money property: session 1 (`btcl -b`) pins + edits + saves
    through the auto-armed checkpoint; session 2 (`btcl -r -s plan`) restores
    the USER candidate still pinned, with the forced layers
    (`layers[M3 M4 M3]`)."""
    flow = _flow_copy(tmp_path)
    env = {**os.environ, "MPLBACKEND": "Agg"}

    # `-b` arms the auto-named checkpoint beside the flow copy — in tmp_path,
    # so nothing lands in the repo's demo/.
    s1 = subprocess.run(
        [*_BTCL_CMD, "-b", str(flow)],
        input=("pin a_0 4\n"
               "edit_topology 1\n"
               "edit_set_layer 0 3\n"
               "edit_set_layer 2 3\n"
               "edit_commit pin\n"
               "replan\n"
               "done\n"),
        capture_output=True, text=True, env=env, timeout=300)
    out1 = (s1.stdout or "") + (s1.stderr or "")
    assert s1.returncode == 0, (s1.returncode, out1[-1200:])
    assert "-b arming checkpoint" in out1, out1[-1200:]
    assert "Pinned 3 segment layer(s)" in out1, out1[-1200:]
    assert "0 overlaps, 0 unplaced, 0 audit violations" in out1, out1[-1200:]

    s2 = subprocess.run(
        [*_BTCL_CMD, "-r", "-s", "plan", str(flow)],
        input="pins\ndone\n",
        capture_output=True, text=True, env=env, timeout=300)
    out2 = (s2.stdout or "") + (s2.stderr or "")
    assert s2.returncode == 0, (s2.returncode, out2[-1200:])
    # the USER candidate is restored, still pinned, forced layers intact
    assert "(USER) layers[M3 M4 M3]" in out2, out2[-1200:]
