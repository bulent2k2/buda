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

"""--log (-l): per-run archive dirs for exploratory re-runs.

Without it, log/<cell>_flow.log is overwritten by every re-run and nothing
records which script text produced a kept log.  With it, each run gets
log/<cell>/<timestamp>/ holding all the run's logs PLUS a copy of the exact
scripts executed — the top-level .buda and every sourced file (where the
tweak under exploration often lives) — with a MANIFEST mapping each copy to
its origin path.
"""
import io
import contextlib
import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import pytest

import buda_cli

_CHILD = "def_layer 4 M4 H TOP 0.0\n"
_TOP   = ("source sub/child.buda\n"
          "def_layer 5 M5 V TOP 0.0\n")


def _write_flow(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "child.buda").write_text(_CHILD)
    flow = tmp_path / "cellA.buda"
    flow.write_text(_TOP)
    return flow


def _run_main(flow, *extra):
    argv = ["buda", str(flow), "--no-viz", *extra]
    old = sys.argv
    sys.argv = argv
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            buda_cli.main()
    finally:
        sys.argv = old
    return buf.getvalue()


def _run_dirs(tmp_path):
    base = tmp_path / "log" / "cellA"
    return sorted(base.iterdir()) if base.is_dir() else []


def test_log_flag_archives_run(tmp_path):
    flow = _write_flow(tmp_path)
    out = _run_main(flow, "-l")
    assert "Run archive →" in out

    dirs = _run_dirs(tmp_path)
    assert len(dirs) == 1, dirs
    run = dirs[0]
    # All logs live in the run dir (no shared log/cellA_flow.log written).
    assert (run / "flow.log").is_file()
    assert not (tmp_path / "log" / "cellA_flow.log").exists()
    # The exact scripts executed are archived: top-level + the sourced child.
    assert (run / "cellA.buda").read_text() == _TOP
    assert (run / "child.buda").read_text() == _CHILD
    manifest = (run / "MANIFEST").read_text()
    assert f"cellA.buda <- {flow}" in manifest
    assert str(tmp_path / "sub" / "child.buda") in manifest


def test_log_flag_reruns_never_collide(tmp_path):
    flow = _write_flow(tmp_path)
    _run_main(flow, "-l")
    flow.write_text(_TOP + "def_layer 6 M6 H TOP 0.0\n")   # the tweak
    _run_main(flow, "-l")

    dirs = _run_dirs(tmp_path)
    assert len(dirs) == 2, dirs           # same-second re-run uniquified too
    first, second = dirs
    assert (first / "cellA.buda").read_text() == _TOP
    assert "M6" in (second / "cellA.buda").read_text()
    assert (first / "flow.log").is_file() and (second / "flow.log").is_file()


def test_basename_collision_uniquified(tmp_path):
    # Two different sourced files sharing a basename both survive, and an
    # identical re-source is archived only once.
    (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
    (tmp_path / "a" / "inc.buda").write_text("def_layer 4 M4 H TOP 0.0\n")
    (tmp_path / "b" / "inc.buda").write_text("def_layer 5 M5 V TOP 0.0\n")
    flow = tmp_path / "cellA.buda"
    flow.write_text("source a/inc.buda\n"
                    "source b/inc.buda\n"
                    "source a/inc.buda\n")     # identical re-source: no 3rd copy
    _run_main(flow, "-l")

    (run,) = _run_dirs(tmp_path)
    names = sorted(p.name for p in run.iterdir() if p.suffix == ".buda")
    assert names == ["cellA.buda", "inc-2.buda", "inc.buda"], names
    inc_lines = [l for l in (run / "MANIFEST").read_text().splitlines()
                 if l.startswith("inc")]
    assert len(inc_lines) == 2              # two origins, third source deduped


def test_default_logging_unchanged_without_flag(tmp_path):
    flow = _write_flow(tmp_path)
    _run_main(flow)
    assert (tmp_path / "log" / "cellA_flow.log").is_file()
    assert not (tmp_path / "log" / "cellA").exists()
