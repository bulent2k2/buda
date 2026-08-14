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

"""The translated Tcl corpus: it must not drift, and it must still route.

`flow/tcl/corpus/` is generated from the `.buda` corpus by
`tools/buda2tcl.py`.  A generated tree checked into the repo has exactly one
failure mode that matters — it stops matching what it was generated from, and
then a passing Tcl sweep is evidence about a flow nobody runs any more.  The
first test closes that: it regenerates in memory and compares.

The second is the point of the whole exercise in miniature: one flow routed
through a real `tclsh` must produce the SAME result as the CLI running the
same design.  The full 41-flow version of that comparison is a sweep
(`tools/tcl_corpus.py` + `qor_corpus.py --compare`), too slow for the suite;
this keeps one honest example in it.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tcl_quote import tcl_path

_ROOT = Path(__file__).resolve().parents[2]
_TOOLS = _ROOT / "tools"
# Quoting, not formatting -- see `src/tcl_quote.py`.
_TCL_Q = tcl_path(_TOOLS / "buda.tcl")
_PY_Q = tcl_path(sys.executable)


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_translation_matches_its_buda_sources():
    """The drift guard.  `--check` regenerates every corpus flow (and every
    fixture they source) and compares against what is committed."""
    b2t = _load("buda2tcl")
    rc = b2t.main(["--check", "--corpus",
                   "--out-dir", str(_ROOT / "flow" / "tcl" / "corpus")])
    assert rc == 0, ("flow/tcl/corpus/ is stale — regenerate with "
                     "`tools/buda2tcl.py --out-dir flow/tcl/corpus --corpus`")


def test_every_corpus_flow_has_a_translation():
    """A flow added to CORPUS without regenerating would simply be absent from
    the Tcl sweep — a silently smaller corpus, which is the failure this
    catches before the sweep reports a clean 40 of 41."""
    qc = _load("qor_corpus")
    tc = _load("tcl_corpus")
    missing = [f for f in qc.CORPUS if not os.path.exists(tc.tcl_path(f))]
    assert not missing, missing


@pytest.mark.mid
@pytest.mark.skipif(shutil.which("tclsh") is None, reason="no tclsh on this host")
def test_a_flow_routes_identically_through_tclsh_and_the_cli():
    """One flow, both drivers, same numbers.

    `flow/hbundles/04_deep_hierarchy.buda` is the smallest corpus vehicle that
    exercises the hier pipeline end to end (bundler, generation, planner, NUTS,
    detailed NUTS), so a bridge fault that only shows on real routing has
    somewhere to show.  Overlaps and unplaced are the gate; the wirelengths
    are the fingerprint — two runs can agree on three small integers by luck
    and cannot agree on the wirelength by luck.
    """
    flow = "flow/hbundles/04_deep_hierarchy.buda"
    tc = _load("tcl_corpus")
    tcl_row = tc.run_one(flow)
    assert "err" not in tcl_row, tcl_row

    # The CLI side, in a subprocess: qor_corpus imports the engine and chdirs,
    # neither of which belongs in the test process.
    code = (f"import json,importlib.util,sys;"
            f"spec=importlib.util.spec_from_file_location('qc',"
            f"r'{_TOOLS / 'qor_corpus.py'}');"
            f"m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
            f"import os;os.chdir(r'{_ROOT}');"
            f"print('CLIROW '+json.dumps(m.run_flow('{flow}')))")
    p = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       encoding="utf-8", cwd=str(_ROOT), timeout=900)
    assert p.returncode == 0, p.stderr[-2000:]
    # Marker-scanned, not last-line: on Windows the engine's C++ stdout is
    # FULLY buffered (the setvbuf line-buffering is deliberately absent —
    # bindings.cpp) and flushes at process exit, AFTER python's print — so
    # the last stdout line was '  M6 (H)  min_band_cap=10', not the JSON
    # (measured, windows-validate run 30, the pass's final residual).  The
    # marker makes the row order-independent, like the Tcl side's 'QOR {…}'.
    row_lines = [ln for ln in p.stdout.splitlines() if ln.startswith("CLIROW ")]
    assert row_lines, p.stdout[-2000:]
    cli_row = json.loads(row_lines[-1].removeprefix("CLIROW "))
    assert "err" not in cli_row, cli_row

    keys = ("overlaps", "unplaced", "viol_bundles", "abstract_wl", "detailed_wl")
    assert {k: tcl_row.get(k) for k in keys} == {k: cli_row.get(k) for k in keys}


@pytest.mark.skipif(shutil.which("tclsh") is None, reason="no tclsh on this host")
def test_the_package_loads_from_inside_a_namespace(tmp_path):
    """`buda.tcl` must land in the GLOBAL namespace however it is sourced.

    Sourced from inside another namespace — which is what a GUI does, and what
    the corpus harness does — a relative `namespace eval buda` defined the whole
    package as `::caller::buda::*`, and the failure surfaced far from the
    cause ("parent namespace doesn't exist" from inside buda::start).
    """
    script = tmp_path / "ns.tcl"
    script.write_text(f"""
        namespace eval myapp {{
            proc boot {{}} {{
                uplevel #0 [list source {_TCL_Q}]
                buda::start -python {_PY_Q}
                buda::add_block a 0 0 10 10
                puts "blocks=[buda::query blocks]"
                buda::stop
            }}
        }}
        myapp::boot
    """)
    r = subprocess.run(["tclsh", str(script)], capture_output=True,
                       encoding="utf-8", cwd=str(tmp_path), timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "blocks=1" in r.stdout, r.stdout + r.stderr


@pytest.mark.skipif(shutil.which("tclsh") is None, reason="no tclsh on this host")
def test_the_engine_resolves_paths_from_the_flows_directory(tmp_path):
    """The harness `cd`s BEFORE starting the engine, because the engine is a
    child process and inherits the directory it was SPAWNED in.  With the cd
    after the start, every path-taking command (`open_bdb mix.bdb.sql`)
    resolved against wherever the sweep was launched from.
    """
    harness = (_ROOT / "flow" / "tcl" / "corpus" / "harness.tcl").read_text()
    cd_at = harness.index("cd [file dirname [file join $repo $origin_buda]]")
    start_at = harness.index("buda::start -echo 0")
    assert cd_at < start_at, ("harness.tcl starts the engine before the cd — "
                              "the child would inherit the wrong directory")


# ── the translation's edge cases (Codex review on #686) ────────────────────
# Each of these is a shape the corpus itself does not contain today.  They are
# pinned anyway because the translator is a general tool: the corpus is what
# it was built for, not the limit of what it will be pointed at.

def _mini(tmp_path, files, top, run=True):
    """Translate a throwaway .buda tree and (optionally) run it through tclsh.

    Returns (out_dir, CompletedProcess|None).
    """
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    for name, text in files.items():
        (src / name).write_text(text)
    out = tmp_path / "out"
    b2t = _load("buda2tcl")
    written = b2t.translate([str(src / top)], str(out), str(src))
    for path, text in written.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        Path(path).write_text(text)
    shutil.copy(_ROOT / "flow" / "tcl" / "corpus" / "harness.tcl", out / "harness.tcl")
    if not run:
        return out, None
    env = dict(os.environ, BUDA_REPO=str(_ROOT))
    p = subprocess.run(["tclsh", str(out / (Path(top).stem + ".tcl"))],
                       capture_output=True, encoding="utf-8", errors="replace",
                       cwd=str(tmp_path), timeout=600, env=env)
    return out, p


@pytest.mark.skipif(shutil.which("tclsh") is None, reason="no tclsh on this host")
def test_a_word_that_cannot_be_braced_escapes_the_command_separator():
    """`;` ends a command in Tcl.  A token that falls to the escaping path —
    it carries a backslash or unbalanced braces — would otherwise not merely
    arrive corrupted: the rest of the line would RUN as a second command."""
    b2t = _load("buda2tcl")
    w = b2t.tcl_word(r"C:\foo;puts")
    assert ";" not in w.replace(r"\;", ""), w
    # ...and the word still means what it said, through a real interpreter.
    out = subprocess.run(["tclsh"], input=f'puts [list {w}]\n',
                         capture_output=True, encoding="utf-8", timeout=60)
    assert out.stdout.strip() == r"{C:\foo;puts}", out.stdout + out.stderr


def test_a_suffixless_source_resolves_like_the_cli(tmp_path):
    """`source fixture` is legal — cmd_source falls back to `fixture.buda`.
    The translator has to resolve it the same way or the flow cannot be
    translated at all."""
    out, _ = _mini(tmp_path, {
        "top.buda": "source fixture\nadd_block b 0 0 10 10\n",
        "fixture.buda": "def_layer 4 M4 H TOP 30\n",
    }, "top.buda", run=False)
    assert (out / "fixture.tcl").exists(), "the suffixless source was not followed"
    assert "fixture.tcl" in (out / "top.tcl").read_text()


@pytest.mark.skipif(shutil.which("tclsh") is None, reason="no tclsh on this host")
def test_an_exit_inside_a_sourced_file_ends_the_whole_run(tmp_path):
    """In the CLI `exit` raises SystemExit and unwinds everything, sourced
    files included.  A translation that only returned from the Tcl `source`
    would keep issuing commands the original never reached — and then measure
    a design that never existed."""
    # The probe is the RE-DECLARATION of `a`: a duplicate block name is a
    # hard error, so if the parent kept going past the fixture's exit the run
    # would fail loudly instead of reporting a clean row.  (The generated text
    # still CONTAINS that line — a translator cannot know statically that a
    # sourced file exits — so the property to test is the runtime one, which
    # is also the property the CLI has.)
    _out, p = _mini(tmp_path, {
        "top.buda": ("add_block a 0 0 10 10\n"
                     "source fixture.buda\n"
                     "add_block a 0 0 10 10\n"),      # unreachable; fatal if run
        "fixture.buda": "add_block b 20 0 30 10\nexit\n",
    }, "top.buda")
    assert p.returncode == 0, p.stdout + p.stderr
    rows = [l for l in p.stdout.splitlines() if l.startswith("QOR ")]
    assert len(rows) == 1, p.stdout
    assert "err" not in rows[0], rows[0]
    assert "already" not in (p.stdout + p.stderr), p.stdout + p.stderr


@pytest.mark.mid
@pytest.mark.skipif(shutil.which("tclsh") is None, reason="no tclsh on this host")
def test_an_armed_writeback_is_flushed_when_the_run_ends(tmp_path):
    """`open_bdb <f>.bdb.sql writeback` persists at the END of the run, via
    the script-level `exit` handler.  `buda::stop` alone does not go through
    it — the server answers `__exit` by closing the session — so the harness
    would have discarded the modified BDB while reporting a clean row."""
    fixture = next((_ROOT / "test" / "tests" / "data").glob("*.bdb.sql"))
    sql = tmp_path / "src" / "w.bdb.sql"
    sql.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(fixture, sql)
    before = sql.read_text()
    _out, p = _mini(tmp_path, {
        "w.buda": "open_bdb w.bdb.sql writeback\nadd_cell zzz_probe 10 10\n",
    }, "w.buda")
    assert p.returncode == 0, p.stdout + p.stderr
    after = sql.read_text()
    assert after != before, "the writeback never reached the .sql"
    assert "zzz_probe" in after, "the modification was not persisted"
