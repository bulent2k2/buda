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

_ROOT = Path(__file__).resolve().parents[2]
_TOOLS = _ROOT / "tools"


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
            f"print(json.dumps(m.run_flow('{flow}')))")
    p = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       encoding="utf-8", cwd=str(_ROOT), timeout=900)
    assert p.returncode == 0, p.stderr[-2000:]
    cli_row = json.loads(p.stdout.strip().splitlines()[-1])
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
                uplevel #0 [list source {_ROOT / 'tools' / 'buda.tcl'}]
                buda::start -python {sys.executable}
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
