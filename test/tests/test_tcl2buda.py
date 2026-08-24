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

"""`tools/tcl2buda.py` — a Tcl flow as the `.buda` it actually ran.

The reverse of `buda2tcl.py` cannot be a source-to-source translator: Tcl is
a general programming language, so which commands a flow issues is not a
property of its text (`array.tcl 3 2` and `array.tcl 4 5` are different
designs from one file).  It is a RECORDER instead — `BUDA_RECORD` writes
every command at `BudaSession.do_command`, the choke point every driver
already passes through — and what it produces is a FLATTENING: loops
unrolled, `source` inlined, branches resolved to whichever way they went.

So the tests are about the two claims that makes: the flattening really is
flat, and the recording really does reproduce the run.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_TOOL = _ROOT / "tools" / "tcl2buda.py"

pytestmark = pytest.mark.skipif(shutil.which("tclsh") is None,
                                reason="no tclsh on this host")


def _run(*args, cwd=None):
    return subprocess.run([sys.executable, str(_TOOL), *map(str, args)],
                          capture_output=True, encoding="utf-8",
                          errors="replace", cwd=str(cwd or _ROOT), timeout=900)


def _commands(path):
    return [ln.strip() for ln in Path(path).read_text().splitlines()
            if ln.strip() and not ln.startswith("#")]


@pytest.mark.mid
def test_a_computed_flow_records_the_design_it_computed(tmp_path):
    """The point of the whole exercise: what a Tcl loop COMPUTES comes out as
    the flat commands it became, and the same file with different arguments
    records a different design.  No parser could do this."""
    small = tmp_path / "s.buda"
    big = tmp_path / "b.buda"
    assert _run("flow/tcl/array.tcl", 2, 2, "-o", small).returncode == 0
    assert _run("flow/tcl/array.tcl", 3, 2, "-o", big).returncode == 0

    s, b = _commands(small), _commands(big)
    assert len(b) > len(s), "3x2 must record more commands than 2x2"
    # The buses a pair of nested Tcl loops emitted, as flat `add_bus` lines
    # with every `${R}_${C}` already substituted.
    assert any(ln.startswith("add_bus bh_0_0[4] t_0_0/") for ln in s), s[:5]
    assert any(ln.startswith("add_bus bh_2_0[4] t_2_0/") for ln in b), \
        "the third row of tiles is missing from the 3x2 recording"
    assert not any("bh_2_0" in ln for ln in s), \
        "the 2x2 recording contains a tile only 3x2 has"
    # Flat: no Tcl left, and no `source` (its children are recorded instead,
    # so keeping it would run them twice on replay).
    assert not any(ln.startswith(("source ", "set ", "if ", "foreach ",
                                  "proc ", "buda::")) for ln in b)


@pytest.mark.mid
def test_the_recording_replays_to_the_same_design(tmp_path):
    """`--verify` runs the recording through the CLI and compares the measured
    result against the Tcl run's.  Wirelength is the fingerprint: two runs can
    agree on the small integers by luck and cannot agree on that."""
    r = _run("flow/tcl/array.tcl", 2, 2, "-o", tmp_path / "a.buda", "--verify")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "identical on" in r.stdout, r.stdout
    assert "detailed WL" in r.stdout, r.stdout


@pytest.mark.mid
def test_a_replay_that_fails_is_reported_as_a_failure(tmp_path):
    """The failure mode this tool must not have.

    A corpus flow `cd`s to its design directory and names its fixture
    relatively (`open_bdb mix.bdb.sql`); a `.buda` resolves relative paths
    against its OWN directory, so a recording written elsewhere cannot find
    it.  That flow also runs `-echo 0`, so there is no metric to compare —
    and an earlier cut returned 0 on exactly that path, reporting success for
    a replay which had already died.  The check on the replay's own exit is
    therefore unconditional, and the message names the cause.
    """
    r = _run("flow/tcl/corpus/rnr/mix.tcl", "-o", tmp_path / "m.buda",
             "--verify")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "the replay FAILED" in r.stdout, r.stdout
    assert "resolves relative paths against its OWN directory" in r.stdout
    # ...and it says where to put it instead.
    assert str(_ROOT / "flow" / "rnr") in r.stdout, r.stdout


def test_the_recorder_flattens_a_source_tree(tmp_path):
    """`BUDA_RECORD` is not Tcl-specific — it records at the session, so a
    `.buda` run records too, and an include tree flattens into one file.
    `source` itself is dropped: recording both it and its children would run
    them twice on replay."""
    (tmp_path / "inc.buda").write_text("def_layer 4 M4 H TOP 30\n"
                                       "def_layer 5 M5 V TOP 30\n")
    top = tmp_path / "top.buda"
    top.write_text("source inc.buda\nadd_block a 0 0 10 10\n")
    out = tmp_path / "rec.buda"
    p = subprocess.run(
        [sys.executable, str(_ROOT / "src" / "buda_cli.py"), "--no-viz",
         str(top)],
        capture_output=True, encoding="utf-8", cwd=str(_ROOT), timeout=300,
        env=dict(os.environ, BUDA_RECORD=str(out), MPLBACKEND="Agg"))
    assert p.returncode == 0, p.stdout + p.stderr
    cmds = _commands(out)
    assert cmds == ["def_layer 4 M4 H TOP 30", "def_layer 5 M5 V TOP 30",
                    "add_block a 0 0 10 10"], cmds
    # The header states the directory relative paths resolved against, so a
    # recording of a flow that used them says where it can be replayed.
    assert any(ln.startswith("# cwd: ")
               for ln in Path(out).read_text().splitlines())


def test_a_flow_that_records_nothing_never_reports_a_stale_file(tmp_path):
    """The retry trap (Codex P1 on #701).

    A Tcl script that dies before `buda::start` records nothing.  If the
    destination already holds a PREVIOUS run's recording, an existence check
    accepts it, counts its commands, and reports an old design as the newly
    recorded result — with the flow's own non-zero exit excused as "its own
    verdict".  So the destination is cleared before Tcl launches, and its
    existence afterwards proves this run wrote it.
    """
    dead = tmp_path / "dead.tcl"
    dead.write_text('error "boom before buda::start"\n')
    out = tmp_path / "out.buda"
    out.write_text("# STALE from an earlier run\nadd_block stale 0 0 1 1\n")

    r = _run(dead, "-o", out)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "recorded nothing" in r.stdout + r.stderr
    assert "stale" not in r.stdout, "it counted the previous run's file"
    assert not out.exists(), "the stale recording survived and could be read"


@pytest.mark.mid
def test_a_relative_out_path_is_the_same_file_for_record_and_replay(tmp_path):
    """`record` resolves `-o` against the CALLER's directory while `replay`
    runs with cwd=<repo>, so a relative path used by both named two different
    files — the replay then checked some other file, or none (Codex P2)."""
    r = _run(_ROOT / "flow" / "tcl" / "array.tcl", 2, 2,
             "-o", "rel.buda", "--verify", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "rel.buda").exists(), "recorded somewhere else"
    assert not (_ROOT / "rel.buda").exists(), "recorded into the repo root"
    assert "identical on" in r.stdout, r.stdout


def test_a_recorded_open_bdb_carries_its_resolved_path(tmp_path):
    """The record flattens `source` trees, so a relative `open_bdb` recorded
    from a sourced file has lost the directory the engine resolved it
    against — the INNERMOST script's, not the entry flow's — and every
    reader had to guess (the Tcl driver's entry-dir guess put a build trace
    beside a file nobody has, #796).  The recorder resolves the path at the
    moment the engine does: provenance, not reconstruction.  `:memory:`
    stays itself and options ride along."""
    sub = tmp_path / "setup"
    sub.mkdir()
    (sub / "inner.buda").write_text("open_bdb ck.bdb\n")
    top = tmp_path / "top.buda"
    top.write_text("source setup/inner.buda\nset_die 100 100\n"
                   "open_bdb :memory:\nexit\n")
    out = tmp_path / "rec.buda"
    p = subprocess.run(
        [sys.executable, str(_ROOT / "src" / "buda_cli.py"), "--no-viz",
         str(top)],
        capture_output=True, encoding="utf-8", cwd=str(_ROOT), timeout=300,
        env=dict(os.environ, BUDA_RECORD=str(out), MPLBACKEND="Agg"))
    assert p.returncode == 0, p.stdout + p.stderr
    opens = [c for c in _commands(out) if c.startswith("open_bdb")]
    # Resolved against the SOURCED file's directory — where the engine
    # actually created it — not the entry flow's.
    assert opens[0] == f"open_bdb {sub / 'ck.bdb'}", opens
    assert (sub / "ck.bdb").exists()
    assert opens[1] == "open_bdb :memory:", opens

    # A spaced path quotes with a delimiter the path does not CONTAIN: the
    # grammar has no escape, so a `"` inside a double-quoted token closes
    # it early and the tail re-reads as unknown options (Codex #798 P2).
    # The awkward character is PLATFORM-CHOSEN, and the property is what
    # survives: a path carrying one quote kind, quoted with the OTHER.  `"`
    # is not a legal NTFS filename character, so the POSIX spelling could
    # not even `mkdir` there -- it raised WinError 123 and failed the test
    # for a reason that had nothing to do with the grammar (measured,
    # windows-validate run 36).  Mirroring keeps the coverage on both
    # platforms instead of skipping Windows; same move as the `?`->`#`
    # substitution in `test_audit2_tools`.
    if os.name == "nt":
        quo, delim = "'", '"'          # `'` is legal on NTFS; quote with `"`
    else:
        quo, delim = '"', "'"
    spaced = tmp_path / f"a {quo}quote{quo} dir"
    spaced.mkdir()
    top.write_text(f"open_bdb {delim}a {quo}quote{quo} dir/ck.bdb{delim}\nexit\n")
    out2 = tmp_path / "rec2.buda"
    p = subprocess.run(
        [sys.executable, str(_ROOT / "src" / "buda_cli.py"), "--no-viz",
         str(top)],
        capture_output=True, encoding="utf-8", cwd=str(_ROOT), timeout=300,
        env=dict(os.environ, BUDA_RECORD=str(out2), MPLBACKEND="Agg"))
    assert p.returncode == 0, p.stdout + p.stderr
    line = [c for c in _commands(out2) if c.startswith("open_bdb")][0]
    import buda_script
    toks = buda_script.split_quoted_args(line)
    assert toks == [str(spaced / "ck.bdb")], (line, toks)
