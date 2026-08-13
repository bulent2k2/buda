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

"""`design.tcl` — the ONE-file iteration vehicle.

The array_save/array_resume pair is two scripts and a shared lib; this is the
same loop folded into a single file a person just keeps running: it BUILDS
when its checkpoint is missing, RESUMES when it exists, and between route and
save it reads pin/edit commands from stdin — so the interactive session and
the scripted one are the same code path, and EOF simply ends the session.

What is pinned here, in separate `tclsh` processes: build; resume + pin via
the prompt (with the exit-path auto-replan that keeps the checkpoint
coherent); resume with NOTHING specified and find the pin still in force;
FRESH=1 rebuilding from scratch; and the prompt surviving a typo instead of
costing the session.
"""
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "flow" / "tcl" / "design.tcl"

pytestmark = [pytest.mark.mid,
              pytest.mark.skipif(shutil.which("tclsh") is None,
                                 reason="no tclsh on this host")]


def _run(tmp_path, stdin="done\n", **env):
    """One session; the checkpoint lives in tmp_path so the repo stays clean."""
    import os
    e = {"BUDA_DESIGN_BDB": str(tmp_path / "it.bdb"), **env}
    return subprocess.run(["tclsh", str(_SCRIPT)], input=stdin,
                          capture_output=True, encoding="utf-8",
                          errors="replace", cwd=tmp_path, timeout=900,
                          env={**os.environ, **e})


def _selected(bdb, bundle_id):
    """(cand_index, type, is_pinned) of a bundle's selected candidate."""
    c = sqlite3.connect(str(bdb))
    try:
        return c.execute(
            "select cand_index, type, is_pinned from topology "
            "where bundle_id=? and is_selected=1", (bundle_id,)).fetchone()
    finally:
        c.close()


def test_one_file_is_the_whole_iteration_loop(tmp_path):
    """Build, pin, come back: three processes, one script, no re-specifying."""
    bdb = tmp_path / "it.bdb"

    # 1. No checkpoint -> BUILD.  EOF right after the route ends the session.
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "BUILT 8 bundles" in r.stdout
    assert "clean -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout
    assert bdb.exists(), "session 1 wrote no checkpoint"
    assert (tmp_path / "it.bdb.sql").exists(), "no diffable snapshot"

    # Unpinned, the planner picks d1 (bundle 3) by cost — candidate 1.
    unpinned = _selected(bdb, "3")
    assert unpinned is not None and unpinned[2] == 0, unpinned

    # 2. Checkpoint exists -> RESUME.  Pin a DIFFERENT candidate at the
    #    prompt (1-based, as the explorer title bar shows it); `done` must
    #    re-plan first so the saved metal belongs to the pinned candidate.
    r = _run(tmp_path, stdin="pin d1 4\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESUMED 8 bundles" in r.stdout
    assert "pins changed since the last route" in r.stdout, \
        "the exit path did not re-plan a dirty pin"
    assert "topo 4 of" in r.stdout, "the re-plan did not honor the pin"
    assert "clean -- 0 overlaps" in r.stdout

    pinned = _selected(bdb, "3")
    assert pinned[2] == 1, f"the pin did not persist: {pinned}"
    assert pinned[0] == 3, f"1-based 4 should pin cand_index 3: {pinned}"
    assert pinned[0] != unpinned[0], (
        "the test proves nothing if the pin picked what the planner would "
        f"have chosen anyway ({unpinned})")

    # 3. Run it again with NOTHING specified: the choice is the checkpoint's.
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESUMED" in r.stdout
    assert "topo 4 of" in r.stdout, "the checkpointed pin was not honored"
    assert _selected(bdb, "3")[0] == 3

    # 4. Unpin by the SAME name the pin used (the selector-parity fix), and
    #    the planner is free again — back to its own choice.
    r = _run(tmp_path, stdin="unpin d1\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Unpinned bundle 3" in r.stdout
    freed = _selected(bdb, "3")
    assert freed[2] == 0, f"the unpin did not persist: {freed}"
    assert freed[0] == unpinned[0], "freed, the planner should re-choose"


def test_the_resume_starts_at_the_planner_and_rebuilds_nothing(tmp_path):
    """An iteration re-plans; it does not re-bundle or re-generate."""
    assert _run(tmp_path).returncode == 0
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESUMED" in r.stdout
    assert "BUILT" not in r.stdout
    for absent in ("[Bundler]", "[TopoGen]"):
        assert absent not in r.stdout, f"the resume re-ran {absent}"
    assert "[Planner]" in r.stdout, "the resume did not reach the planner"


def test_fresh_discards_the_checkpoint_and_the_pin_with_it(tmp_path):
    """BUDA_DESIGN_FRESH=1 is the explicit ask to start over."""
    assert _run(tmp_path).returncode == 0
    assert _run(tmp_path, stdin="pin d1 4\ndone\n").returncode == 0
    assert _selected(tmp_path / "it.bdb", "3")[2] == 1

    r = _run(tmp_path, BUDA_DESIGN_FRESH="1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "removing" in r.stdout and "BUILT" in r.stdout
    fresh = _selected(tmp_path / "it.bdb", "3")
    assert fresh[2] == 0, f"FRESH kept the pin: {fresh}"


def test_the_prompt_survives_mistakes_and_passes_raw_commands_through(tmp_path):
    """A typo prints and continues — it must not cost the session.  An
    engine-registry command goes through verbatim (the escape hatch the
    edit_* commands ride); a command the registry does NOT know is refused
    LOCALLY, because sending it would trip the CLI's fail-fast and kill the
    engine mid-session."""
    assert _run(tmp_path).returncode == 0
    r = _run(tmp_path, stdin="pin d1\n"                 # wrong arity
                             "no_such_command_xyz\n"    # not in the registry
                             "dump_topologies d1\n"     # raw pass-through
                             "pin d1 4\n"
                             "done\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "usage: pin" in r.stdout
    assert "unknown command 'no_such_command_xyz'" in r.stdout
    assert "bundle 3" in r.stdout               # dump_topologies reached it
    assert "topo 4 of" in r.stdout              # ...and the session went on
    assert "clean -- 0 overlaps" in r.stdout
