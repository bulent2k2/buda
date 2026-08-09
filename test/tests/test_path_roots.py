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

"""Which root a script-declared relative path resolves against.

This file PINS the split documented in docs/internal/opens_interchange.md
item 4 — the state before the unification, so the flip is a visible contract
change in this file's history rather than an undocumented drift:

  * `open_bdb` / `save_bdb` / `source` resolve against the SCRIPT's directory;
  * the import/export commands resolve against the CWD.

Two same-looking relative paths on adjacent lines mean different files, and
`flow/def/chip.buda` runs from exactly one directory because of it.
"""
import contextlib
import io
from pathlib import Path

import buda_cli

# A one-routing-layer LEF; the layer name is the discriminator, so a test can
# put a DIFFERENT layer on each candidate root and see which file was read.
_LEF = """VERSION 5.8 ;
LAYER {name}
  TYPE ROUTING ;
  DIRECTION {direction} ;
  PITCH 0.2 ;
  WIDTH 0.1 ;
END {name}
END LIBRARY
"""

_V = "module top ();\nendmodule\n"


def _run(script, cwd, monkeypatch, *, expect_error=False):
    """Source `script` (by absolute path) with the process CWD at `cwd`."""
    monkeypatch.chdir(cwd)
    s = buda_cli.BudaSession()
    s.no_viz = True
    out, err = io.StringIO(), None
    with contextlib.redirect_stdout(out):
        try:
            s.do_command(f"source {script}")
        except Exception as e:           # noqa: BLE001 - the point is which root
            err = e
    assert (err is not None) == expect_error, (err, out.getvalue())
    return s, out.getvalue(), err


def _setup(tmp_path):
    sdir = tmp_path / "scripts"
    cwd = tmp_path / "cwd"
    sdir.mkdir()
    cwd.mkdir()
    return sdir, cwd


# ── the script-rooted family ────────────────────────────────────────────────

def test_open_bdb_resolves_against_the_script_directory(tmp_path, monkeypatch):
    sdir, cwd = _setup(tmp_path)
    script = sdir / "t.buda"
    script.write_text("open_bdb sub.bdb\n")
    _run(script, cwd, monkeypatch)
    assert (sdir / "sub.bdb").exists()
    assert not (cwd / "sub.bdb").exists()


def test_save_bdb_snapshot_resolves_against_the_script_directory(
        tmp_path, monkeypatch):
    sdir, cwd = _setup(tmp_path)
    script = sdir / "t.buda"
    script.write_text("open_bdb :memory:\nsave_bdb snap/s.bdb\n")
    _run(script, cwd, monkeypatch)
    assert (sdir / "snap" / "s.bdb").exists()
    assert not (cwd / "snap").exists()


def test_source_resolves_against_the_including_script(tmp_path, monkeypatch):
    sdir, cwd = _setup(tmp_path)
    (sdir / "inner.buda").write_text("def_layer 4 M4 H 50\n")
    (cwd / "inner.buda").write_text("def_layer 5 M5 V 50\n")
    script = sdir / "t.buda"
    script.write_text("source inner.buda\n")
    s, _, _ = _run(script, cwd, monkeypatch)
    assert s.layers.has_layer(4)         # the script-side file was included
    assert not s.layers.has_layer(5)


# ── the CWD-rooted family (the OTHER root, same-looking paths) ─────────────

def test_import_lef_tech_resolves_against_the_cwd(tmp_path, monkeypatch):
    sdir, cwd = _setup(tmp_path)
    (sdir / "tech.lef").write_text(_LEF.format(name="M1",
                                               direction="HORIZONTAL"))
    (cwd / "tech.lef").write_text(_LEF.format(name="M9",
                                              direction="VERTICAL"))
    script = sdir / "t.buda"
    script.write_text("import_lef_tech tech.lef\n")
    s, _, _ = _run(script, cwd, monkeypatch)
    assert "M9" in s._layer_name_map     # the CWD-side file was read
    assert "M1" not in s._layer_name_map


def test_import_verilog_resolves_against_the_cwd(tmp_path, monkeypatch):
    sdir, cwd = _setup(tmp_path)
    (cwd / "top.v").write_text(_V)       # exists ONLY under the CWD
    script = sdir / "t.buda"
    script.write_text("open_bdb :memory:\nimport_verilog top.v\n")
    _run(script, cwd, monkeypatch)       # no error: the CWD copy was found

    # …and the same script fails when the file sits only next to the script,
    # which is the sharp edge: open_bdb one line up DOES look there.
    (cwd / "top.v").unlink()
    (sdir / "top.v").write_text(_V)
    _run(script, cwd, monkeypatch, expect_error=True)


def test_emit_guides_writes_under_the_cwd(tmp_path, monkeypatch):
    sdir, cwd = _setup(tmp_path)
    script = sdir / "t.buda"
    script.write_text("emit_guides out/g.json\n")
    _run(script, cwd, monkeypatch)
    assert (cwd / "out" / "g.json").exists()
    assert not (sdir / "out").exists()


def test_export_def_blockages_writes_under_the_cwd(tmp_path, monkeypatch):
    sdir, cwd = _setup(tmp_path)
    script = sdir / "t.buda"
    script.write_text("export_def_blockages out/a.def\n")
    _run(script, cwd, monkeypatch)
    assert (cwd / "out" / "a.def").exists()
    assert not (sdir / "out").exists()


# ── shared by both eras: absolute paths and script-less sessions ───────────

def test_absolute_paths_are_never_rewritten(tmp_path, monkeypatch):
    sdir, cwd = _setup(tmp_path)
    dest = tmp_path / "abs" / "g.json"
    script = sdir / "t.buda"
    script.write_text(f"emit_guides {dest}\n")
    _run(script, cwd, monkeypatch)
    assert dest.exists()


def test_no_script_means_cwd_for_everyone(tmp_path, monkeypatch):
    """Interactive / Tcl-front-end / Python-API commands have no enclosing
    script, so every family falls back to the CWD."""
    _sdir, cwd = _setup(tmp_path)
    monkeypatch.chdir(cwd)
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("open_bdb direct.bdb")
        s.do_command("emit_guides out/g.json")
    assert (cwd / "direct.bdb").exists()
    assert (cwd / "out" / "g.json").exists()
