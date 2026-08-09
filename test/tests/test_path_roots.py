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

"""ONE root for every script-declared relative path: the script's directory.

The previous revision of this file pinned the split this replaces
(docs/internal/opens_interchange.md item 4): `open_bdb`/`save_bdb`/`source`
resolved against the script's directory while the import/export commands
resolved against the CWD — so two same-looking paths on adjacent lines meant
different files, and `flow/def/chip.buda` ran from exactly one directory.

Now every path-taking command goes through `resolve_script_path`: relative
paths resolve against the enclosing script's directory (the innermost
`source`d file), and a session with no script — interactive, the Tcl front
end, the Python API — keeps the CWD, since there is no script to be relative
to.  A `.buda` script is a location-independent artifact, like any other
language's include.
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


# ── reads resolve against the script's directory ───────────────────────────

def test_import_lef_tech_resolves_against_the_script_directory(
        tmp_path, monkeypatch):
    """Two candidate files, one per root — the script-side one must win."""
    sdir, cwd = _setup(tmp_path)
    (sdir / "tech.lef").write_text(_LEF.format(name="M1",
                                               direction="HORIZONTAL"))
    (cwd / "tech.lef").write_text(_LEF.format(name="M9",
                                              direction="VERTICAL"))
    script = sdir / "t.buda"
    script.write_text("import_lef_tech tech.lef\n")
    s, _, _ = _run(script, cwd, monkeypatch)
    assert "M1" in s._layer_name_map
    assert "M9" not in s._layer_name_map


def test_import_verilog_agrees_with_open_bdb_one_line_up(
        tmp_path, monkeypatch):
    """THE sharp edge item 4 documented: open_bdb and import_verilog are
    adjacent lines and used to resolve against different roots.  Now one
    file placed next to the script satisfies both."""
    sdir, cwd = _setup(tmp_path)
    (sdir / "top.v").write_text(_V)      # next to the script ONLY
    script = sdir / "t.buda"
    script.write_text("open_bdb sub.bdb\nimport_verilog top.v\n")
    _run(script, cwd, monkeypatch)       # no error: both found it
    assert (sdir / "sub.bdb").exists()


def test_a_cwd_only_file_fails_loud_and_names_both_roots(
        tmp_path, monkeypatch):
    """The migration aid (BUDA-1609): a script written against the old CWD
    rule gets a diagnosis naming both roots, not a bare file-not-found for a
    file its author can see exists.  The note never changes the resolution —
    the read still fails."""
    sdir, cwd = _setup(tmp_path)
    (cwd / "top.v").write_text(_V)       # under the CWD ONLY (the old root)
    script = sdir / "t.buda"
    script.write_text("open_bdb :memory:\nimport_verilog top.v\n")
    _s, out, err = _run(script, cwd, monkeypatch, expect_error=True)
    assert "BUDA-1609" in out
    assert str(sdir) in out and str(cwd) in out
    assert err is not None               # deterministic rule, then the failure


def test_import_def_lef_resolves_both_paths_against_the_script(
        tmp_path, monkeypatch):
    """BOTH arguments go through the resolver, proven separately: a CWD-only
    LEF draws the note naming it (a missing LEF file is tolerated by the
    importer, so no error), and a CWD-only DEF draws the note AND the
    failure."""
    sdir, cwd = _setup(tmp_path)
    (sdir / "d.def").write_text("DESIGN top ;\nEND DESIGN\n")
    (cwd / "t.lef").write_text(_LEF.format(name="M1",
                                           direction="HORIZONTAL"))
    script = sdir / "t.buda"
    script.write_text("open_bdb :memory:\nimport_def_lef d.def t.lef\n")
    _s, out, _err = _run(script, cwd, monkeypatch)
    assert "BUDA-1609" in out and "'t.lef'" in out

    (cwd / "d.def").write_text("DESIGN top ;\nEND DESIGN\n")
    (sdir / "d.def").unlink()
    _s, out, err = _run(script, cwd, monkeypatch, expect_error=True)
    assert "BUDA-1609" in out and "'d.def'" in out
    assert err is not None


# ── writes land next to the script ─────────────────────────────────────────

def test_emit_guides_writes_next_to_the_script(tmp_path, monkeypatch):
    sdir, cwd = _setup(tmp_path)
    script = sdir / "t.buda"
    script.write_text("emit_guides out/g.json csv out/g.csv tcl out/g.tcl\n")
    _run(script, cwd, monkeypatch)
    for name in ("g.json", "g.csv", "g.tcl"):
        assert (sdir / "out" / name).exists()
    assert not (cwd / "out").exists()


def test_export_def_blockages_writes_next_to_the_script(tmp_path, monkeypatch):
    sdir, cwd = _setup(tmp_path)
    script = sdir / "t.buda"
    script.write_text("export_def_blockages out/a.def\n")
    _run(script, cwd, monkeypatch)
    assert (sdir / "out" / "a.def").exists()
    assert not (cwd / "out").exists()


def test_all_output_families_agree_on_one_directory(tmp_path, monkeypatch):
    """What item 4 could not have: script-rooted save_bdb and the exports
    landing in the SAME out/ regardless of where the run started."""
    sdir, cwd = _setup(tmp_path)
    script = sdir / "t.buda"
    script.write_text("open_bdb :memory:\n"
                      "emit_guides out/g.json\n"
                      "save_bdb out/s.bdb\n")
    _run(script, cwd, monkeypatch)
    assert (sdir / "out" / "g.json").exists()
    assert (sdir / "out" / "s.bdb").exists()
    assert not (cwd / "out").exists()


# ── the rest of the rule: unchanged families, nesting, fallbacks ───────────

def test_open_bdb_still_resolves_against_the_script_directory(
        tmp_path, monkeypatch):
    sdir, cwd = _setup(tmp_path)
    script = sdir / "t.buda"
    script.write_text("open_bdb sub.bdb\n")
    _run(script, cwd, monkeypatch)
    assert (sdir / "sub.bdb").exists()
    assert not (cwd / "sub.bdb").exists()


def test_source_still_resolves_against_the_including_script(
        tmp_path, monkeypatch):
    sdir, cwd = _setup(tmp_path)
    (sdir / "inner.buda").write_text("def_layer 4 M4 H 50\n")
    (cwd / "inner.buda").write_text("def_layer 5 M5 V 50\n")
    script = sdir / "t.buda"
    script.write_text("source inner.buda\n")
    s, _, _ = _run(script, cwd, monkeypatch)
    assert s.layers.has_layer(4)
    assert not s.layers.has_layer(5)


def test_nested_source_resolves_against_the_innermost_script(
        tmp_path, monkeypatch):
    """The base is the INNERMOST script — the rule `source` has always used,
    now carried by every command: a sourced library resolves its own paths
    relative to itself, wherever it is included from."""
    sdir, cwd = _setup(tmp_path)
    sub = sdir / "lib"
    sub.mkdir()
    (sub / "tech.lef").write_text(_LEF.format(name="M1",
                                              direction="HORIZONTAL"))
    (sdir / "tech.lef").write_text(_LEF.format(name="M9",
                                               direction="VERTICAL"))
    (sub / "inner.buda").write_text("import_lef_tech tech.lef\n")
    script = sdir / "t.buda"
    script.write_text("source lib/inner.buda\n")
    s, _, _ = _run(script, cwd, monkeypatch)
    assert "M1" in s._layer_name_map     # lib/'s own tech.lef, not the outer one
    assert "M9" not in s._layer_name_map


def test_absolute_paths_are_never_rewritten(tmp_path, monkeypatch):
    sdir, cwd = _setup(tmp_path)
    dest = tmp_path / "abs" / "g.json"
    script = sdir / "t.buda"
    script.write_text(f"emit_guides {dest}\n")
    _run(script, cwd, monkeypatch)
    assert dest.exists()


def test_no_script_means_cwd_for_everyone(tmp_path, monkeypatch):
    """Interactive / Tcl-front-end / Python-API commands have no enclosing
    script, so every family falls back to the CWD — there is no script to be
    relative to."""
    sdir, cwd = _setup(tmp_path)
    (cwd / "tech.lef").write_text(_LEF.format(name="M9",
                                              direction="VERTICAL"))
    monkeypatch.chdir(cwd)
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("open_bdb direct.bdb")
        s.do_command("import_lef_tech tech.lef")
        s.do_command("emit_guides out/g.json")
    assert (cwd / "direct.bdb").exists()
    assert "M9" in s._layer_name_map
    assert (cwd / "out" / "g.json").exists()
