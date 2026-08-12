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


def test_nothing_in_the_repo_imports_the_pair():
    """The item closed because nothing depends on the two files agreeing.
    That is a property of the tree, so it is checked against the tree — a
    future caller would make the refusals above a broken demo instead of a
    correct guard, and should have to notice.

    Scanning only `*.buda` was not enough and is the reason this test was
    wrong when written: the documented entry point is a PYTHON one
    (`tools/def_viz_o3.py <def> <lef>`), so the scan covers scripts too.
    `def_viz_shared.py` itself is exempt — it is the module that now
    refuses, and it names the files in that refusal's own test."""
    offenders = []
    for pattern in ("*.buda", "*.py", "*.tcl"):
        for path in _ROOT.rglob(pattern):
            s = str(path)
            if any(part in s for part in ("/log/", "/out/", "/build/",
                                          "/.git/", "__pycache__",
                                          "/test/tests/")):
                continue
            for line in path.read_text(errors="ignore").splitlines():
                line = line.strip()
                if "ariane" not in line:
                    continue
                if line.startswith("import_def_lef") or (
                        "import_def_lef(" in line and not line.startswith("#")):
                    offenders.append(f"{path.relative_to(_ROOT)}: {line}")
    assert not offenders, offenders


def test_the_def_alone_still_reads_completely():
    """The DEF is a perfectly good DEF and is used as one: the reader test
    goes through `read_def` and never opens a LEF, which is why the
    mismatch cannot reach it."""
    import buda
    d = buda.read_def(str(_DEMO / "ariane.def"))
    assert len(d.components) == d.declared_components == 133
    assert len(d.pins) == d.declared_pins == 495
