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

"""`demo/ariane`'s DEF and LEF are from different technologies, on purpose.

`ariane.def` is the TILOS MacroPlacement **NanGate45** ariane133 benchmark
(133 x `fakeram45_256x16`); `ariane.lef` is an **ASAP7** SRAM, obtained
separately — `demo/ariane/ReadMe.md` records it as "got it later".  Neither
file is wrong; the LEF was simply never the LEF for this DEF.

`opens_interchange.md` item 9 closed on that finding with no code change,
which is only safe while two things hold — so they are pinned here rather
than asserted in prose:

  * the reader REFUSES the pair (the old importer made every macro on this
    2.7 mm die a 0.5 um speck in silence);
  * nothing in the repo imports the pair, so the refusal is never in
    anyone's way.

Delete this file if the matching NanGate45 LEF is ever added — at that
point the pair is a design and its technology, and both claims stop being
true by intent rather than by accident.
"""
import contextlib
import io
from pathlib import Path

import pytest

import buda_cli

_ROOT = Path(__file__).resolve().parents[2]
_DEMO = _ROOT / "demo" / "ariane"
#: The NanGate45 macro `flow/ariane133/fetch.py` pulls down — the LEF that
#: actually describes `demo/ariane/ariane.def`.  Not checked in.
_LEF = _ROOT / "flow" / "ariane133" / "fakeram45_256x16.lef"


def _import(extra=""):
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    code = None
    try:
        with contextlib.redirect_stdout(buf):
            s.do_command("open_bdb :memory:")
            s.do_command(f"import_def_lef {_DEMO / 'ariane.def'} "
                         f"{_DEMO / 'ariane.lef'} {extra}".strip())
    except SystemExit as e:
        code = e.code
    return s, buf.getvalue(), code


def test_the_mismatched_pair_is_refused_and_says_why():
    """A design handed a technology that does not describe it must stop.

    The message has to name the cell and the consequence, because the
    failure it prevents is not a crash — it is a floorplan that looks
    plausible and is entirely wrong."""
    _s, out, code = _import()
    assert code == 1, (code, out[-500:])
    assert "BUDA-1601" in out, out[-800:]
    assert "fakeram45_256x16" in out
    assert "allow_missing_footprints" in out


def test_the_refusal_can_be_overridden_deliberately():
    """…but only on purpose.  The override exists so someone who knows the
    footprints are missing can proceed; it is not the default."""
    _s, out, code = _import("allow_missing_footprints")
    assert code is None, (code, out[-500:])
    assert "fakeram45_256x16" in out          # still LOUD, just not fatal


def test_the_visualizer_refuses_the_pair_too():
    """The CLI is not the only way in, and the other way was silent.

    `def_viz_shared.DefVizData._load_via_bdb` calls the RAW
    `BDB.import_def_lef`, which REPORTS `missing_cells` but does not refuse
    — the BUDA-1601 stop lives in the CLI command wrapper.  So the very
    command this demo's ReadMe opens with imported all 133 macros at the
    0.5 x 0.5 um fallback and CACHED the resulting BDB, meaning every later
    run reused a plausible and entirely wrong floorplan (Codex P1 on #710).

    That is exactly the fault this item claims is fixed, still reachable
    through the documented path — so the loader refuses it now."""
    import sys
    sys.path.insert(0, str(_ROOT / "tools"))
    from def_viz_shared import DefVizData

    with pytest.raises(SystemExit) as e:
        DefVizData().load(str(_DEMO / "ariane.def"), str(_DEMO / "ariane.lef"))
    msg = str(e.value)
    assert "fakeram45_256x16" in msg, msg
    assert "0.5" in msg and "entirely wrong" in msg, msg


def test_the_visualizers_no_lef_mode_still_works():
    """The refusal names a way forward, so that way has to work: with no
    LEF the loader infers sizes from the placement.  A guard that leaves
    the user with nothing to do would just be an obstacle."""
    import sys
    sys.path.insert(0, str(_ROOT / "tools"))
    from def_viz_shared import DefVizData

    for stale in _DEMO.glob("*.bdb"):
        stale.unlink()
    try:
        summary = DefVizData().load(str(_DEMO / "ariane.def"), "")
    finally:
        for stale in _DEMO.glob("*.bdb"):
            stale.unlink()
    assert "133 instances" in summary, summary
    # …and the die it reports is the DEF's real one, not the 226 um the
    # ReadMe used to claim from a different design's description.
    assert "1357" in summary, summary


#: How many physical lines a wrapped call may span before we stop following
#: it.  A `.buda` command is always one line (the language has no
#: continuation), but a Python or Tcl call is wrapped at whim, and the two
#: arguments this scan is about are long paths — so they land on separate
#: lines under any ordinary formatter.
_WRAP_LINES = 6


def _import_commands():
    """Every `import_def_lef` COMMAND in the tree that mentions ariane.

    Yields `(relative path, command text)` with the command reassembled from
    however many physical lines it occupies, because a line is not a command.
    Skips generated trees and this test directory; `def_viz_shared.py` is
    exempt for the same reason — it is the module that refuses, and it names
    the files in its own refusal.

    Reassembly is what makes the pair test sound.  Reading one stripped line
    at a time, the mismatched pair walks straight past a conjunction:

        db.import_def_lef("demo/ariane/ariane.def",
                          "demo/ariane/ariane.lef")

    is two lines, neither of which names both files (Codex P2 on #715) —
    and that is not an exotic spelling, it is what any formatter does to a
    call with two long path arguments.
    """
    for pattern in ("*.buda", "*.py", "*.tcl"):
        for path in _ROOT.rglob(pattern):
            s = str(path)
            if any(part in s for part in ("/log/", "/out/", "/build/",
                                          "/.git/", "__pycache__",
                                          "/test/tests/")):
                continue
            lines = path.read_text(errors="ignore").splitlines()
            # `.buda` has no line continuation, so a command IS its line;
            # widening the window there could only over-match.
            single = path.suffix == ".buda"
            for i, raw in enumerate(lines):
                line = raw.strip()
                if line.startswith("#") or "import_def_lef" not in line:
                    continue
                if not (line.startswith("import_def_lef")
                        or "import_def_lef(" in line):
                    continue
                if single:
                    cmd = line
                else:
                    cmd = " ".join(
                        ln.strip() for ln in lines[i:i + _WRAP_LINES]
                        if not ln.strip().startswith("#"))
                    # Stop at the call's own closing paren so the window
                    # cannot swallow an unrelated neighbouring statement.
                    close = cmd.find(")")
                    if close != -1:
                        cmd = cmd[:close + 1]
                if "ariane" in cmd:
                    yield path.relative_to(_ROOT), cmd


def test_nothing_in_the_repo_imports_the_MISMATCHED_pair():
    """The item closed because nothing depends on the two files agreeing.
    That is a property of the tree, so it is checked against the tree.

    This predicate has now been wrong three times, and each way is worth
    keeping written down, because they are three different mistakes:

      * it first scanned only `*.buda`, missing the documented entry point,
        which is a PYTHON one (`tools/def_viz_o3.py <def> <lef>`) — too
        narrow in WHICH FILES;
      * it then flagged ANY import naming ariane, which made a legitimate
        import of `ariane.def` indistinguishable from the fault, and fired
        the moment `flow/ariane133/` imported the same DEF with the LEF that
        actually describes it — too broad in WHAT COUNTS;
      * and it read one physical LINE at a time while asking a question
        about a COMMAND, so a call wrapped across two lines — the ordinary
        formatting of a call with two long paths — walked straight past it
        (Codex P2 on #715).  Too narrow in what it was even looking at.

    The guarded property was never "nobody may import this DEF".  It is
    "nobody may import this DEF **against `ariane.lef`**", those two files
    being from different technologies.  So the pair is what is matched, over
    reassembled commands rather than lines."""
    offenders = [f"{p}: {cmd}" for p, cmd in _import_commands()
                 if "ariane.def" in cmd and "ariane.lef" in cmd]
    assert not offenders, offenders


def test_the_ariane133_flow_imports_that_def_with_the_lef_that_fits_it():
    """The positive half, and the reason the scan above had to be narrowed.

    `flow/ariane133/` routes `demo/ariane/ariane.def` — the same DEF — using
    the NanGate45 `fakeram45_256x16.lef` fetched from the benchmark suite
    that produced the DEF.  That is the other half of item 9: the file is
    not missing from the world, only from this repo, and the flow says so by
    running.  If someone ever repoints it at `ariane.lef`, the test above
    fails, not this one."""
    cmds = [cmd for p, cmd in _import_commands() if "ariane133" in str(p)]
    assert cmds, "flow/ariane133 no longer imports a DEF — was it removed?"
    for cmd in cmds:
        assert "fakeram45_256x16.lef" in cmd, cmd
        assert "ariane.lef" not in cmd, cmd


@pytest.mark.skipif(not _LEF.exists(),
                    reason=f"{_LEF.name} not fetched "
                           "(python3 flow/ariane133/fetch.py)")
def test_the_matching_lef_gives_the_macros_their_real_size():
    """…and when the LEF is present, the claim is measured rather than
    asserted: 133 macros at 57.57 x 133.0 um, against the 0.5 x 0.5 speck
    the wrong LEF produced.  That number IS the finding.

    The skip gates on the LEF because the LEF is what this opens — it does
    not read the netlist at all.  It first gated on `ariane.v`, which is
    wrong in both directions: `fetch.py` downloads sequentially and leaves
    successful files in place, so a run that gets the netlist and then fails
    on the LEF armed this test against a file that is not there, turning an
    optional test into a hard `RuntimeError`; and a fetched LEF without the
    netlist skipped a test that would have run perfectly (Codex P2 on
    #715)."""
    import collections

    import buda
    lef = _ROOT / "flow" / "ariane133" / "fakeram45_256x16.lef"
    db = buda.BDB(":memory:")
    st = db.import_def_lef(str(_DEMO / "ariane.def"), str(lef))
    assert not list(getattr(st, "missing_cells", []) or [])
    sizes = collections.Counter(
        (round(c.x2 - c.x1, 2), round(c.y2 - c.y1, 2))
        for c in db.all_components())
    assert sizes[(57.57, 133.0)] == 133, sizes.most_common()


def test_the_def_alone_still_reads_completely():
    """The DEF is a perfectly good DEF and is used as one: the reader test
    goes through `read_def` and never opens a LEF, which is why the
    mismatch cannot reach it."""
    import buda
    d = buda.read_def(str(_DEMO / "ariane.def"))
    assert len(d.components) == d.declared_components == 133
    assert len(d.pins) == d.declared_pins == 495
