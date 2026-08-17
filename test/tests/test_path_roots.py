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

import pytest
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
    if expect_error is not None:
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
    LEF draws the note naming it, and a CWD-only DEF draws the note AND the
    failure.  The LEF half asserts only the note (expect_error=None): whether
    a missing LEF file is an error is the importer's contract, pinned in
    test_def_import.py, not a path-resolution question."""
    sdir, cwd = _setup(tmp_path)
    (sdir / "d.def").write_text("DESIGN top ;\nEND DESIGN\n")
    (cwd / "t.lef").write_text(_LEF.format(name="M1",
                                           direction="HORIZONTAL"))
    script = sdir / "t.buda"
    script.write_text("open_bdb :memory:\nimport_def_lef d.def t.lef\n")
    _s, out, _err = _run(script, cwd, monkeypatch, expect_error=None)
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


# ── a space in the path is part of the filename ────────────────────────────

def test_a_sourced_path_may_contain_spaces(tmp_path, monkeypatch):
    """`source` takes exactly ONE path, so its argument runs to end of line.

    `do_command` splits on whitespace, so `args[0]` was the path only up to
    its first space: `source my dir/inner.buda` looked for `my` and the error
    named a file the author never wrote.
    """
    sdir, cwd = _setup(tmp_path)
    sub = sdir / "my dir"
    sub.mkdir()
    (sub / "inner file.buda").write_text("def_layer 4 M4 H 50\n")
    script = sdir / "t.buda"
    script.write_text("source my dir/inner file.buda\n")
    s, _, _ = _run(script, cwd, monkeypatch)
    assert s.layers.has_layer(4)


def test_the_flow_s_OWN_path_may_contain_spaces(tmp_path, monkeypatch):
    """The path that actually bit, because it is the one a user does not
    choose: `bin/buda "my dir/x.buda"` reaches the engine as a `source` line,
    and so does `btcl -i`, so a checkout under `~/My Designs/` could not be
    run at all."""
    sdir, cwd = _setup(tmp_path)
    home = sdir / "My Designs"
    home.mkdir()
    script = home / "my flow.buda"
    script.write_text("def_layer 4 M4 H 50\n")
    s, _, _ = _run(script, cwd, monkeypatch)
    assert s.layers.has_layer(4)
    assert s.script_path == str(script)      # and it is the flow's identity


def test_a_quoted_path_is_accepted(tmp_path, monkeypatch):
    """The instinctive spelling, and the norm on Windows.  Without this it
    fails as a near-miss with the quotes inside the filename."""
    sdir, cwd = _setup(tmp_path)
    sub = sdir / "my dir"
    sub.mkdir()
    (sub / "inner.buda").write_text("def_layer 4 M4 H 50\n")
    script = sdir / "t.buda"
    script.write_text('source "my dir/inner.buda"\n')
    s, _, _ = _run(script, cwd, monkeypatch)
    assert s.layers.has_layer(4)


def test_a_missing_spaced_path_is_named_in_full(tmp_path, monkeypatch):
    """Half the original confusion was the diagnostic: it reported the
    truncation (`.../my`) rather than the path the author wrote."""
    sdir, cwd = _setup(tmp_path)
    script = sdir / "t.buda"
    script.write_text("source no such file.buda\n")
    monkeypatch.chdir(cwd)
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = io.StringIO()
    # A missing source is FAIL-FAST — SystemExit, which is a BaseException and
    # so sails past `_run`'s `except Exception`.
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit):
        s.do_command(f"source {script}")
    # The RESOLVED path, not the echoed command line — the line is quoted in
    # the message either way, so asserting on it would pass unfixed.
    head = out.getvalue().split(" ('")[0]
    assert head.endswith("no such file.buda"), out.getvalue()


def test_a_trailing_comment_is_still_not_part_of_the_path(
        tmp_path, monkeypatch):
    """Rest-of-line must not swallow the comment the dispatcher strips."""
    sdir, cwd = _setup(tmp_path)
    (sdir / "inner.buda").write_text("def_layer 4 M4 H 50\n")
    script = sdir / "t.buda"
    script.write_text("source inner.buda   # the tracks fixture\n")
    s, _, _ = _run(script, cwd, monkeypatch)
    assert s.layers.has_layer(4)


# ── the option-bearing family: a QUOTED path may contain spaces ───────────

def test_a_quoted_path_works_for_a_command_that_also_takes_options(
        tmp_path, monkeypatch):
    """`source` can take the rest of the line because it has no options.
    `open_bdb <path> [writeback]` cannot — so a spaced path is QUOTED there,
    and the quote is what resolves an ambiguity nothing else can."""
    sdir, cwd = _setup(tmp_path)
    (sdir / "my dir").mkdir()
    script = sdir / "t.buda"
    script.write_text('open_bdb "my dir/ck.bdb"\n')
    s, _, _ = _run(script, cwd, monkeypatch)
    assert s.bdb is not None
    assert (sdir / "my dir" / "ck.bdb").exists()


def test_the_option_after_a_quoted_path_is_still_read(tmp_path, monkeypatch):
    """The whole point of not using rest-of-line here: the trailing option
    must not be swallowed into the filename."""
    sdir, cwd = _setup(tmp_path)
    (sdir / "my dir").mkdir()
    fixture = sdir / "my dir" / "f.bdb.sql"
    fixture.write_text("")                      # empty .sql fixture
    script = sdir / "t.buda"
    script.write_text('open_bdb "my dir/f.bdb.sql" writeback\n')
    s, _, _ = _run(script, cwd, monkeypatch)
    # `writeback` was READ as the option (it armed the .sql write-back), not
    # taken as part of the path.
    assert s._bdb_writeback_src is not None, "writeback was not armed"
    assert "my dir" in s._bdb_writeback_src


def test_an_unknown_option_is_still_refused_beside_a_path(
        tmp_path, monkeypatch):
    """The regression that decided the design: with a rest-of-line rule
    `export_gds out.gds bogus_option 1` reads the typo as part of the
    filename, the unknown-option error disappears, and the run writes a file
    with a garbage name.  Nothing in the line distinguishes that from a
    genuinely spaced path, so unquoted keeps its old meaning exactly."""
    sdir, cwd = _setup(tmp_path)
    script = sdir / "t.buda"
    script.write_text("open_bdb x.bdb\nexport_gds out.gds bogus_option 1\n")
    _, out, _ = _run(script, cwd, monkeypatch)
    assert "unknown option 'bogus_option'" in out, out
    assert not list(sdir.glob("*bogus*")), "the typo became a filename"


def test_an_option_value_that_is_a_path_may_be_quoted(tmp_path, monkeypatch):
    """`emit_guides … tcl <file>` — the VALUE is a path too, so it takes the
    same quoting.  (`margin 5` is a number and is unaffected either way.)"""
    sdir, cwd = _setup(tmp_path)
    (sdir / "my out").mkdir()
    script = sdir / "t.buda"
    script.write_text(
        "def_layer 5 M5 V TOP 30\ndef_layer 6 M6 H TOP 30\n"
        "add_block a 0 0 100 100\nadd_block b 300 0 400 100\n"
        "add_net n1 a.o b.i\nrun_bundler STRICT\ngenerate_topologies\n"
        "run_planner 3\nrun_nuts\n"
        'emit_guides "my out/g.json" margin 5 tcl "my out/g.tcl"\n')
    _run(script, cwd, monkeypatch)
    assert (sdir / "my out" / "g.json").exists()
    assert (sdir / "my out" / "g.tcl").exists(), "the quoted tcl value was lost"


def test_a_sub_verb_path_may_contain_spaces(tmp_path, monkeypatch):
    """`def_gds_layer file <path>` takes one path and nothing after it, so
    it takes the rest of the line — the `source` rule, one sub-verb deeper."""
    sdir, cwd = _setup(tmp_path)
    (sdir / "my maps").mkdir()
    (sdir / "my maps" / "gds.map").write_text("4 63\n")
    script = sdir / "t.buda"
    script.write_text("def_layer 4 M4 H 50\n"
                      "def_gds_layer file my maps/gds.map\n")
    s, out, _ = _run(script, cwd, monkeypatch)
    assert s.layers.get_gds_layer(4) == 63, out


# ── the two-path family: quoting is the only thing that can say where ─────
#    one path ends and the next begins

# The smallest pair `import_def_lef` will actually READ — enough that the
# reader's own count line proves both files were found, not just resolved.
_DEF2 = """VERSION 5.8 ;
DESIGN t ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 100000 100000 ) ;
COMPONENTS 1 ;
  - i0 m + PLACED ( 1000 2000 ) N ;
END COMPONENTS
END DESIGN
"""

_LEF2 = """MACRO m
  SIZE 10 BY 4 ;
END m
END LIBRARY
"""


def _def_lef_in(sdir, dirname="my dir"):
    d = sdir / dirname
    d.mkdir()
    (d / "a.def").write_text(_DEF2)
    (d / "b.lef").write_text(_LEF2)
    return d


def test_both_paths_of_import_def_lef_may_be_quoted(tmp_path, monkeypatch):
    """`import_def_lef <def> <lef>` is the shape rest-of-line cannot serve at
    all: with TWO adjacent paths there is no trailing keyword to stop at, and
    nothing between them but whitespace.  A quote is the only thing that can
    state the boundary — so it does, on either path independently."""
    sdir, cwd = _setup(tmp_path)
    _def_lef_in(sdir)
    script = sdir / "t.buda"
    script.write_text('open_bdb :memory:\n'
                      'import_def_lef "my dir/a.def" "my dir/b.lef"\n')
    s, out, _ = _run(script, cwd, monkeypatch)
    # The reader RAN on both files: it read the component, and it never fell
    # back to the 0.5x0.5 speck that a missing LEF footprint produces.
    assert "[DEF] components: imported 1 of 1" in out, out
    assert "BUDA-1601" not in out and "BUDA-1609" not in out, out
    assert s.bdb.all_components()[0].x2 == 11.0, "the LEF SIZE was not read"


def test_a_quoted_def_still_leaves_the_lef_a_separate_argument(
        tmp_path, monkeypatch):
    """The half a rest-of-line rule would lose: quoting the FIRST path must
    not swallow the second."""
    sdir, cwd = _setup(tmp_path)
    _def_lef_in(sdir)
    script = sdir / "t.buda"
    script.write_text('open_bdb :memory:\n'
                      'import_def_lef "my dir/a.def" my dir/b.lef\n')
    monkeypatch.chdir(cwd)
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = io.StringIO()
    # Not `_run`: the refusal is fail-fast (SystemExit, a BaseException), so
    # it sails past `_run`'s `except Exception` before it can return the
    # output this asserts on.
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit):
        s.do_command(f"source {script}")
    # Whatever follows the quoted path is parsed as its own arguments, exactly
    # as it always was: `my` is the LEF and `dir/b.lef` is a third token, which
    # the option check refuses.  A rest-of-line rule would have eaten both into
    # the filename and reported nothing at all.
    assert "unknown option 'dir/b.lef'" in out.getvalue(), out.getvalue()


def test_one_quoted_path_is_not_two_paths(tmp_path, monkeypatch):
    """The count is taken AFTER quote-aware splitting.  Counted on the
    dispatcher's plain split, `import_def_lef "a b.def"` is two tokens and
    sails through the arity check carrying ONE path — then indexes off the
    end."""
    sdir, cwd = _setup(tmp_path)
    _def_lef_in(sdir)
    script = sdir / "t.buda"
    script.write_text('open_bdb :memory:\n'
                      'import_def_lef "my dir/a.def"\n')
    _s, out, err = _run(script, cwd, monkeypatch, expect_error=None)
    assert "requires <def_path> <lef_path>" in out, out
    assert not isinstance(err, IndexError), err


def test_an_unknown_option_is_still_refused_after_two_paths(
        tmp_path, monkeypatch):
    """The guard that decided the design, in its two-path form: unquoted
    tokens keep their old meaning, so a typo is still a typo.

    Passes both ways by design — it pins the decision, not a bug."""
    sdir, cwd = _setup(tmp_path)
    _def_lef_in(sdir)
    script = sdir / "t.buda"
    script.write_text('open_bdb :memory:\n'
                      'import_def_lef "my dir/a.def" "my dir/b.lef" '
                      'no_blockage\n')
    with pytest.raises(SystemExit):
        _run(script, cwd, monkeypatch, expect_error=None)


def test_an_option_word_inside_a_quoted_path_stays_part_of_the_path(
        tmp_path, monkeypatch):
    """A membership test over the DISPATCHER's tokens reads a directory named
    `no_tracks` as the option — the file is found (the handler resolves the
    quoted path) and the DEF's tracks are silently dropped."""
    sdir, cwd = _setup(tmp_path)
    _def_lef_in(sdir, "no_tracks dir")
    (sdir / "no_tracks dir" / "a.def").write_text(
        _DEF2.replace("COMPONENTS 1 ;",
                      "TRACKS Y 500 DO 10 STEP 400 LAYER metal2 ;\n"
                      "COMPONENTS 1 ;"))
    script = sdir / "t.buda"
    script.write_text(
        'def_layer 2 metal2 H 50\nopen_bdb :memory:\n'
        'import_def_lef "no_tracks dir/a.def" "no_tracks dir/b.lef"\n')
    s, out, _ = _run(script, cwd, monkeypatch)
    assert "[DEF] components: imported 1 of 1" in out, out
    assert "tracks installed" in out, \
        f"the DEF's TRACKS were dropped — the path read as `no_tracks`\n{out}"
    assert s.routing_grid is not None


def test_require_file_accepts_a_quoted_spaced_path(tmp_path, monkeypatch):
    """`require_file` takes a LIST of paths, so it has the same problem twice
    over.  A satisfied precondition is silent — which is the assertion: the
    run reaches the command after it."""
    sdir, cwd = _setup(tmp_path)
    (sdir / "my inputs").mkdir()
    (sdir / "my inputs" / "top.v").write_text(_V)
    script = sdir / "t.buda"
    script.write_text('require_file "my inputs/top.v"\n'
                      'def_layer 4 M4 H 50\n')
    s, out, _ = _run(script, cwd, monkeypatch)
    assert s.layers.has_layer(4), out     # it did not stop
    assert "BUDA-1905" not in out, out


def test_a_missing_quoted_path_is_named_in_full_by_require_file(
        tmp_path, monkeypatch):
    """And when it is NOT satisfied, the diagnostic names the path the author
    wrote — the truncation was the other half of the original confusion.

    The COUNT is what discriminates: split on whitespace, one missing file is
    reported as two, both under names nobody typed."""
    sdir, cwd = _setup(tmp_path)
    script = sdir / "t.buda"
    script.write_text('require_file "my inputs/top.v" hint run fetch.py\n')
    monkeypatch.chdir(cwd)
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = io.StringIO()
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit):
        s.do_command(f"source {script}")
    msg = out.getvalue()
    assert "1 required input file(s) not found" in msg, msg
    assert "my inputs/top.v" in msg, msg
    assert "run fetch.py" in msg, msg      # the remedy still rides along


def test_a_hash_inside_a_quoted_path_is_not_a_comment(tmp_path, monkeypatch):
    """`#` in a filename is legal on every filesystem, and the comment rule cut
    the line at the space before it — so the handler resolved `"inputs/rev`,
    and the quoted-path support failed on exactly the spelling it exists for
    (Codex #775 P2).

    Fixed in `strip_inline_comment` rather than only in the tokenizer,
    because the DISPATCHER strips the comment to derive `args` and to write
    the `BUDA_RECORD` line: a tokenizer-only fix would have left the recorder
    writing a path the handler never read.
    """
    sdir, cwd = _setup(tmp_path)
    d = sdir / "rev #2"
    d.mkdir()
    (d / "top.v").write_text(_V)
    (d / "inner.buda").write_text("def_layer 7 M7 V 50\n")
    script = sdir / "t.buda"
    script.write_text('require_file "rev #2/top.v"\n'
                      'source "rev #2/inner.buda"\n')
    s, out, _ = _run(script, cwd, monkeypatch)
    assert "BUDA-1905" not in out, out       # the precondition was satisfied
    assert s.layers.has_layer(7), out        # …and the sourced file was found


def test_an_unquoted_hash_still_starts_a_comment(tmp_path, monkeypatch):
    """The other direction, which is the whole corpus: quoting is the ESCAPE
    for a `#`, exactly as it is for a space.  Unquoted, the comment wins."""
    sdir, cwd = _setup(tmp_path)
    script = sdir / "t.buda"
    script.write_text("def_layer 4 M4 H 50   # def_layer 9 M9 V 50\n")
    s, _, _ = _run(script, cwd, monkeypatch)
    assert s.layers.has_layer(4) and not s.layers.has_layer(9)


def test_no_checked_in_flow_parses_differently(tmp_path):
    """The claim the whole design rests on, MEASURED rather than asserted: a
    quote is honoured only where a token BEGINS, so for every line that does
    not use one the tokenizer is `.split()` and the comment rule is the
    character scan it always was.

    Every `.buda` in the tree is walked because the byte-identity claim is
    about the corpus, not about a sample of it — and a flow is exactly where a
    stray quote character would hide.

    The baseline is the OLD comment rule written out here, not
    `strip_inline_comment` itself: both rules now come off one scan, so
    comparing them to each other would pass however that scan changed.
    """
    from buda_script import split_quoted_args, strip_inline_comment

    def old_strip(line):                    # the rule before quoting existed
        for i, ch in enumerate(line):
            if ch == '#' and (i == 0 or line[i - 1].isspace()):
                return line[:i]
        return line

    root = Path(__file__).resolve().parents[2]
    differ, n_files, n_lines = [], 0, 0
    for p in sorted(root.rglob("*.buda")):
        n_files += 1
        for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            n_lines += 1
            if strip_inline_comment(line) != old_strip(line):
                differ.append(f"{p.relative_to(root)}:{i}: comment {line!r}")
            if split_quoted_args(line, skip=0) != old_strip(line).split():
                differ.append(f"{p.relative_to(root)}:{i}: split {line!r}")
    assert n_files > 100 and n_lines > 1000, (n_files, n_lines)  # it walked
    assert not differ, differ[:10]


def test_every_buda_reader_resolves_a_spaced_source_the_same(tmp_path):
    """The engine is not the only thing that parses `.buda` (Codex #771 P2).

    `buda2tcl` translates a flow, `buda2bdb` ingests one as a cell,
    `qor_corpus` walks one for feature coverage, `scan_fanin` scans one for
    net shapes — and each FOLLOWS `source` into the next file.  A rule that
    lives only in the engine is one the tools disagree with, silently: they
    resolve a different file, or none, and report on a script nobody wrote.
    (`qor_corpus` is the one that bites hardest — its walk decides whether a
    flow is a full-pipeline corpus candidate at all.)

    So they share `buda_script.sole_path_arg`, and this pins that they agree
    on the case that motivated it.  Deliberately behavioural, one assertion
    per reader: an import-level check would pass while a reader still split
    the argument itself.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    import qor_corpus
    import scan_fanin
    import buda2tcl

    sub = tmp_path / "my dir"
    sub.mkdir()
    (sub / "inner file.buda").write_text(
        "run_bundler COMBINED\nadd_net n1 a.o b.i\n")
    flow = tmp_path / "top.buda"
    flow.write_text("source my dir/inner file.buda\nrun_nuts\n")

    # qor_corpus: the sourced file's tokens must reach the walk.
    assert "run_bundler:combined" in qor_corpus.flow_tokens(str(flow))

    # scan_fanin: the sourced file's nets must reach the scan.
    nets, _ = scan_fanin.parse_script(flow)
    assert [n[0] for n in nets] == ["n1"], nets

    # buda2tcl: the include must resolve to the real file, so it is queued
    # for translation instead of a path that does not exist.
    _, ref, _ = buda2tcl.translate_line(
        "source my dir/inner file.buda\n", str(tmp_path),
        str(tmp_path), str(tmp_path))
    assert ref == str(sub / "inner file.buda"), ref


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
