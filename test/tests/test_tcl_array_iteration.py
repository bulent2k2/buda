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

"""`array_save.tcl` + `array_resume.tcl` — a design iteration across processes.

`array.tcl` proves the pipeline runs from Tcl.  This pair proves the thing a
person actually needs next: that a decision SURVIVES.  It opens `:memory:`,
so every topology, plan and hand-picked candidate dies with the process —
which is exactly how a candidate chosen in the explorer gets lost between
runs.

So what is pinned here is the loop, in three separate `tclsh` processes:
build and checkpoint; reopen, pin, re-plan, checkpoint again; reopen once
more and find the pin still in force with nothing re-specified.  A checkpoint
that reads back but does not BIND the next session's planner would pass a
weaker test and be worth nothing.
"""
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SAVE = _ROOT / "flow" / "tcl" / "array_save.tcl"
_RESUME = _ROOT / "flow" / "tcl" / "array_resume.tcl"

pytestmark = [pytest.mark.mid,
              pytest.mark.skipif(shutil.which("tclsh") is None,
                                 reason="no tclsh on this host")]


def _run(script, tmp_path, *args, **env):
    """One session, with the checkpoint in tmp_path so the repo stays clean."""
    e = {"BUDA_ARRAY_BDB": str(tmp_path / "it.bdb"), **env}
    import os
    r = subprocess.run(["tclsh", str(script), *map(str, args)],
                       capture_output=True, encoding="utf-8", errors="replace",
                       cwd=tmp_path, timeout=900, env={**os.environ, **e})
    return r


def _selected(bdb, bundle_id):
    """(cand_index, type, is_pinned) of a bundle's selected candidate."""
    c = sqlite3.connect(str(bdb))
    try:
        return c.execute(
            "select cand_index, type, is_pinned from topology "
            "where bundle_id=? and is_selected=1", (bundle_id,)).fetchone()
    finally:
        c.close()


def test_a_pinned_candidate_survives_into_the_next_session(tmp_path):
    """The whole point, in three processes."""
    bdb = tmp_path / "it.bdb"

    # 1. Build.  The checkpoint is a real file, and the flow is self-checking.
    r = _run(_SAVE, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "clean -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout
    assert bdb.exists(), "session 1 wrote no checkpoint"
    assert (tmp_path / "it.bdb.sql").exists(), "no diffable snapshot"

    # Unpinned, the planner picks this design's `diag` bundle by cost.
    unpinned = _selected(bdb, "10")
    assert unpinned is not None and unpinned[2] == 0, unpinned

    # 2. Reopen, pin a DIFFERENT candidate, re-plan.  1-based, as the
    #    explorer's title bar shows it.
    r = _run(_RESUME, tmp_path, BUDA_ARRAY_PIN="diag=14")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "restored 12 bundles" in r.stdout, r.stdout
    assert "topo 14 of" in r.stdout, "the planner did not honor the pin"
    assert "clean -- 0 overlaps" in r.stdout

    pinned = _selected(bdb, "10")
    assert pinned[2] == 1, f"the pin did not persist: {pinned}"
    assert pinned[0] == 13, f"1-based 14 should pin cand_index 13: {pinned}"
    assert pinned[0] != unpinned[0], (
        "the test proves nothing if the pin picked what the planner would "
        f"have chosen anyway ({unpinned})")

    # 3. Reopen with NOTHING specified.  The choice is the checkpoint's now.
    r = _run(_RESUME, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "no BUDA_ARRAY_PIN" in r.stdout
    assert "topo 14 of" in r.stdout, "the checkpointed pin was not honored"
    assert _selected(bdb, "10")[0] == 13


def test_the_resume_starts_at_the_planner_and_rebuilds_nothing(tmp_path):
    """A design iteration re-plans; it does not re-elaborate.  If the resume
    were quietly rebuilding the design, every one of these would appear in its
    output — and `add_inst` into a populated BDB is a duplicate-instance error,
    so it would not even get that far."""
    assert _run(_SAVE, tmp_path).returncode == 0
    r = _run(_RESUME, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    for absent in ("add_cell", "derive_busterms", "run_hier_bundler",
                   "generate_hier_topologies"):
        assert absent not in r.stdout, f"the resume re-ran {absent}"
    assert "[Planner]" in r.stdout, "the resume did not reach the planner"


def test_the_save_flow_refuses_to_rebuild_over_a_file_it_does_not_own(tmp_path):
    """Session 1 REBUILDS, so it clears its own `.bdb` first.  Pointed at
    anything else it must refuse rather than delete it."""
    precious = tmp_path / "not_mine.sqlite"
    precious.write_text("important")
    r = _run(_SAVE, tmp_path, BUDA_ARRAY_BDB=str(precious))
    assert r.returncode != 0
    assert "refusing to overwrite" in r.stdout + r.stderr
    assert precious.read_text() == "important", "it deleted the file anyway"


def test_a_caller_named_bdb_is_never_deleted_without_being_asked(tmp_path):
    """The dangerous case, because the guard used to PASS it: a `.bdb`
    suffix says nothing about who owns the file (Codex P1 on #693).  A real
    checkpoint named by a mistyped `BUDA_ARRAY_BDB` would have been deleted,
    with no undo — so only this flow's own default output goes without
    asking, and anything the caller named needs `BUDA_ARRAY_FRESH=1`."""
    theirs = tmp_path / "production_checkpoint.bdb"
    theirs.write_bytes(b"SQLite format 3\x00 not really, but theirs")
    before = theirs.read_bytes()

    r = _run(_SAVE, tmp_path, BUDA_ARRAY_BDB=str(theirs))
    assert r.returncode != 0, "it went ahead and rebuilt over their database"
    assert theirs.read_bytes() == before, "it deleted a file it does not own"
    out = r.stdout + r.stderr
    assert "refusing to overwrite" in out
    assert "BUDA_ARRAY_FRESH=1" in out, "no way forward in the message"

    # ...and the escape hatch works, because a refusal with no override is
    # just a different way to be unusable.
    r = _run(_SAVE, tmp_path, BUDA_ARRAY_BDB=str(theirs), BUDA_ARRAY_FRESH="1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert theirs.read_bytes() != before, "FRESH=1 did not rebuild"


def test_the_resume_flow_says_so_when_there_is_no_checkpoint(tmp_path):
    """The first thing anyone does wrong is run session 2 first."""
    r = _run(_RESUME, tmp_path)
    assert r.returncode != 0
    assert "no checkpoint at" in r.stdout + r.stderr
    assert "array_save.tcl" in r.stdout + r.stderr, "no recipe in the message"
