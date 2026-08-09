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

"""The Tcl front end (Phase 5 of docs/internal/lefdef_interface_plan.md).

Driven through a REAL `tclsh`, not a stub.  The plumbing here — a pipe, a
length-prefixed frame, an interpreter with its own encoding defaults — is
exactly the kind that passes every mock and fails on contact, so mocking it
would test the mock.

The properties worth pinning are the ones where a plausible design is wrong:

  * a command that fails by PRINTING must still raise in Tcl, because Tcl's
    contract is that a failed command raises and a flow that keeps going
    after a failed step ships a wrong result;
  * a WARNING must not raise, or every flow with a benign warning dies;
  * a fail-fast command ends the session AND fails — reporting only the
    ending makes a crash look like a clean finish;
  * the command list comes from the engine, so it cannot drift.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_TCL = _ROOT / "tools" / "buda.tcl"

pytestmark = pytest.mark.skipif(shutil.which("tclsh") is None,
                                reason="no tclsh on this host")


def _tcl(tmp_path, body, expect_rc=0, no_locale=False):
    """Run a Tcl flow.

    `no_locale` strips LC_*/LANG from the child's environment.  That is not
    an exotic case: Python sets `LC_CTYPE=C.UTF-8` in its own environ (PEP
    538) and passes it to every child, so a tclsh launched from pytest gets a
    UTF-8 stdout for free — while the same script run from a plain shell on a
    compute farm, where nothing sets a locale, gets iso8859-1.  Without this
    the encoding test would pass no matter what the code did.
    """
    script = tmp_path / "t.tcl"
    script.write_text(f"source {_TCL}\nbuda::start -python {sys.executable}\n"
                      f"{body}\n")
    env = dict(os.environ)
    if no_locale:
        for k in [k for k in env if k.startswith("LC_") or k == "LANG"]:
            del env[k]
    r = subprocess.run(["tclsh", str(script)], capture_output=True,
                       encoding="utf-8", errors="replace",
                       cwd=tmp_path, timeout=300, env=env)
    if expect_rc is not None:
        assert r.returncode == expect_rc, r.stdout + r.stderr
    return r.stdout + r.stderr


# ── the bridge itself ──────────────────────────────────────────────────────

def test_every_registry_command_becomes_a_tcl_command(tmp_path):
    """The list is asked of the running engine, so a command added to
    `src/buda_cmds/` is callable from Tcl with no second list to maintain —
    the drift a hand-written binding always eventually has."""
    sys.path.insert(0, str(_ROOT / "src"))
    sys.path.insert(0, str(_ROOT / "build"))
    from buda_cmds import COMMANDS
    out = _tcl(tmp_path, """
        puts "N=[llength [buda::commands]]"
        puts "HAS=[expr {[lsearch -exact [buda::commands] run_bundler] >= 0}]"
        puts "PROC=[llength [info commands ::buda::run_bundler]]"
        buda::stop""")
    assert f"N={len(COMMANDS)}" in out, out
    assert "HAS=1" in out and "PROC=1" in out, out


def test_a_flow_runs_end_to_end_and_returns_values(tmp_path):
    """The reason to drive a tool from Tcl at all: a command that RETURNS a
    value can be branched on, which a `.buda` script cannot do."""
    out = _tcl(tmp_path, r"""
        buda::def_layer 4 M4 H TOP 30
        buda::def_layer 5 M5 V TOP 30
        foreach b {a b} { }
        buda::add_block a 0 0 100 100
        buda::add_block b 800 600 900 700
        buda::add_bus d\[8\] a.o b.i
        buda::run_bundler STRICT
        buda::generate_topologies
        buda::run_planner 3
        buda::run_nuts
        puts "BUNDLES=[buda::query bundles]"
        puts "BLOCKS=[buda::query blocks]"
        puts "OVERLAPS=[buda::query overlaps]"
        buda::stop""")
    assert "BUNDLES=1" in out, out
    assert "BLOCKS=2" in out, out
    assert "OVERLAPS=0" in out, out


def test_a_count_that_was_never_computed_is_not_zero(tmp_path):
    """`no NUTS result` and `no overlaps` are opposite conclusions, and a
    flow that branches on the number must be able to tell them apart."""
    out = _tcl(tmp_path, """
        puts "OVERLAPS=[buda::query overlaps]"
        buda::stop""")
    assert "OVERLAPS=-1" in out, out


def test_an_unknown_query_says_what_it_knows(tmp_path):
    out = _tcl(tmp_path, """
        if {[catch {buda::query nonesuch} e]} { puts "E=$e" }
        buda::stop""")
    assert "unknown query" in out and "bundles" in out, out


# ── failure is Tcl's kind of failure ───────────────────────────────────────

def test_a_command_that_fails_by_printing_still_raises(tmp_path):
    """BUDA's handlers report most errors by PRINTING `Error: …` and
    returning normally.  Passing that through as success would let a flow
    run on past a failed step — the exact bug the Tcl front end exists to
    stop a site from writing by hand."""
    out = _tcl(tmp_path, """
        if {[catch {buda::select_topology 999 1} e]} {
            puts "RAISED"
        } else { puts "SILENT" }
        buda::stop""")
    assert "RAISED" in out, out


def test_a_warning_does_not_raise(tmp_path):
    """The other direction, and the one that would make the bridge unusable:
    `run_nuts` with nothing to do warns and returns, and a flow with a benign
    warning must not die."""
    out = _tcl(tmp_path, """
        if {[catch {buda::run_nuts} e]} { puts "RAISED" } else { puts "OK" }
        buda::stop""")
    assert "OK" in out and "RAISED" not in out, out


def test_a_fail_fast_command_reports_the_failure_not_just_the_end(tmp_path):
    """A malformed argument ends a `.buda` run on the spot, and it ends the
    Tcl session too — a command must mean the same thing in both.  What it
    must NOT do is look like a clean finish: folding this into `BYE` let the
    flow continue and then fail later with 'the engine exited unexpectedly',
    blaming the wrong command."""
    out = _tcl(tmp_path, """
        if {[catch {buda::add_block a 0 0 10.5 10} e]} {
            puts "RAISED"
            puts "WHY=[string match {*whole number*} $e]"
        } else { puts "SILENT" }
        buda::stop""")
    assert "RAISED" in out, out
    assert "WHY=1" in out, out


def test_exit_zero_is_a_clean_finish(tmp_path):
    """…and the same path must NOT raise when the script simply says it is
    done, which is how 18 of the repo's flows end."""
    out = _tcl(tmp_path, """
        if {[catch {buda::exit 0} e]} { puts "RAISED=$e" } else { puts "CLEAN" }
        buda::stop
        puts "SURVIVED\"""")
    assert "CLEAN" in out and "SURVIVED" in out, out


# ── the details that only a real interpreter exposes ───────────────────────

def test_non_ascii_diagnostics_survive_the_trip(tmp_path):
    """The frame length is counted in CHARACTERS on both sides, and the echo
    channel is set to UTF-8 — on a host with no locale, tclsh's stdout
    defaults to iso8859-1 and every `→` in the planner's output becomes `?`,
    so a correct tool reads as a corrupt one.

    Run with the locale stripped ON PURPOSE (see `_tcl`): pytest's own child
    environment carries `LC_CTYPE=C.UTF-8`, which hides the bug entirely —
    the first version of this test passed with the fix removed."""
    out = _tcl(tmp_path, no_locale=True, body=r"""
        buda::def_layer 4 M4 H TOP 30
        buda::def_layer 5 M5 V TOP 30
        buda::add_block a 0 0 100 100
        buda::add_block b 800 600 900 700
        buda::add_bus d\[4\] a.o b.i
        buda::run_bundler STRICT
        buda::generate_topologies
        buda::run_planner 3
        buda::stop""")
    assert "→" in out, out
    assert "?M4" not in out, out


def test_output_is_available_even_with_the_echo_off(tmp_path):
    out = _tcl(tmp_path, """
        buda::stop
        buda::start -python %s -echo 0
        buda::dump_messages
        puts "CAPTURED=[string match {*BUDA-1601*} [buda::output]]"
        puts "QUIET=[string match {*BUDA-1601*} {}]"
        buda::stop""" % sys.executable)
    assert "CAPTURED=1" in out, out
    # …and it did NOT reach the terminal, which is what `-echo 0` means.
    assert "BUDA-1601  ERROR" not in out, out
