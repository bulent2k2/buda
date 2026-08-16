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
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tcl_quote import tcl_path

_ROOT = Path(__file__).resolve().parents[2]
_TCL = _ROOT / "tools" / "buda.tcl"
# Interpolating a path into Tcl source is quoting, not formatting: see
# `src/tcl_quote.py` for the two separate ways a native Windows path is
# otherwise eaten before anything looks at a filename.
_TCL_Q = tcl_path(_TCL)
_PY_Q = tcl_path(sys.executable)

# mid tier: drives a real tclsh + buda_server subprocess per test (bridge
# integration), too heavy for the fast inner loop — run with `bb -m`/`bb -s`.
pytestmark = [pytest.mark.mid,
              pytest.mark.skipif(shutil.which("tclsh") is None,
                                 reason="no tclsh on this host")]


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
    script.write_text(f"source {_TCL_Q}\nbuda::start -python {_PY_Q}\n"
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


def test_a_printed_error_inside_a_summarized_source_still_raises(tmp_path):
    """The same contract, through the door `buda::log` opens (Codex #769 P1).

    Summarizing replaces a command's `Error: …` line with a one-line abstract
    that LEADS with the `x ` marker, and the bridge decides "this command
    failed" by scanning the transcript for a line that leads with the
    diagnostic — so an anchored scan stops matching and a printed-error
    command inside a summarized `source` returned OK.  The driver then
    prompted on a half-failed flow.  Armed and unarmed must agree, so this
    runs the SAME flow both ways and compares.
    """
    flow = tmp_path / "bad.buda"
    flow.write_text("def_layer 5 M5 V TOP 30\n"
                    "def_layer 6 M6 H TOP 30\n"
                    "add_block a 0 0 100 100\n"
                    "add_block b 300 0 400 100\n"
                    "add_net n1 a.o b.i\n"
                    "run_bundler STRICT\n"
                    "generate_topologies\n"
                    # Prints `Error: …` and returns normally — no raise.
                    "run_detailed_nuts\n")
    body = """
        if {[info exists ::env(ARM)]} { buda::log %s }
        if {[catch {buda::source %s} e]} { puts "RAISED" } else { puts "SILENT" }
        buda::stop""" % (tcl_path(flow), tcl_path(flow))

    import os
    assert "RAISED" in _tcl(tmp_path, body), "unarmed lost the contract"
    os.environ["ARM"] = "1"
    try:
        out = _tcl(tmp_path, body)
    finally:
        del os.environ["ARM"]
    assert "RAISED" in out, out          # …and armed keeps it
    # The failure is real, not a stray match: the summary IS the error line.
    assert "x run_detailed_nuts" in out, out


def test_a_clean_summarized_source_does_not_raise(tmp_path):
    """The other direction of the same fix: counting error lines must not
    make a clean summarized flow look failed."""
    flow = tmp_path / "ok.buda"
    flow.write_text("def_layer 5 M5 V TOP 30\n"
                    "def_layer 6 M6 H TOP 30\n"
                    "add_block a 0 0 100 100\n"
                    "add_block b 300 0 400 100\n"
                    "add_net n1 a.o b.i\n"
                    "run_bundler STRICT\n"
                    "generate_topologies\n"
                    "run_planner 3\n"
                    "run_nuts\n")
    out = _tcl(tmp_path, """
        buda::log %s
        if {[catch {buda::source %s} e]} { puts "RAISED: $e" } else { puts "CLEAN" }
        buda::stop""" % (tcl_path(flow), tcl_path(flow)))
    assert "CLEAN" in out, out


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
        buda::stop""" % _PY_Q)
    assert "CAPTURED=1" in out, out
    # …and it did NOT reach the terminal, which is what `-echo 0` means.
    assert "BUDA-1601  ERROR" not in out, out


def test_the_violations_query_is_the_audit_leg(tmp_path):
    """`overlaps` and `unplaced` say the metal was placed; `violations` says
    the last check_design found it electrically right.  -1 until an audit
    has run — a flow gate must not read "never audited" as clean."""
    out = _tcl(tmp_path, """
        puts "pre=[buda::query violations]"
        buda::def_layer 5 M5 V TOP 30
        buda::def_layer 6 M6 H TOP 30
        buda::add_block a 0 0 100 100
        buda::add_block b 300 0 400 100
        buda::add_net n1 a.o b.i
        buda::run_bundler STRICT
        buda::generate_topologies
        buda::run_planner 3
        buda::run_nuts
        buda::check_design
        puts "post=[buda::query violations]"
        buda::stop
    """)
    assert "pre=-1" in out, out
    assert "post=0" in out, out


def test_buda_log_gives_the_terminal_the_cli_gives(tmp_path):
    """`bin/buda` prints one line per command and files the detail; the same
    flow driven from Tcl printed every line of every command, because the
    summarizer is gated on a flow log being open and only the CLI ever opened
    one.  `buda::log` opens the same one — same path, same rotation, same
    summaries — so a flow means the same thing through both doors."""
    flow = tmp_path / "mini.buda"
    flow.write_text("def_layer 5 M5 V TOP 30\n"
                    "def_layer 6 M6 H TOP 30\n"
                    "add_block a 0 0 100 100\n"
                    "add_block b 300 0 400 100\n"
                    "add_bus d[4] a.o b.i\n"
                    "run_bundler STRICT\n"
                    "generate_topologies\n"
                    "run_planner 3\n"
                    "run_nuts\n")
    out = _tcl(tmp_path, f"""
        puts "OFF=[buda::log]"
        puts "ARMED=[buda::log {tcl_path(flow)}]"
        buda::source {tcl_path(flow)}
        buda::endreport
        puts "DISARMED=[buda::log off]"
        puts "AFTER=[buda::log]"
        buda::stop""")

    log_path = tmp_path / "log" / "mini_flow.log"
    assert re.search(r"^OFF=$", out, re.M), out          # "" while off
    assert f"ARMED={log_path}" in out, out
    assert f"DISARMED={log_path}" in out, out
    # …and a bare query reports OFF again: the path outlives the close
    # (the end report names the file it wrote), the ARMED state does not.
    assert re.search(r"^AFTER=$", out, re.M), out

    # The console got the abstract…
    assert "═══════ Runtime summary (mini.buda)" in out, out
    assert "Full per-command detail →" in out, out
    assert "[Planner] Bundle" not in out, out
    # …and the detail is in the file, at the path the CLI derives.
    detail = log_path.read_text()
    assert "[Planner] Bundle" in detail
    assert "━━━ run_nuts ━━━" in detail


def test_an_unarmed_session_is_the_flow_it_always_was(tmp_path):
    """Opt-in, and byte-identical when not used: a Tcl flow issues its
    commands one at a time and usually wants each one's output, so arming
    this in `buda::start` would change every existing flow's console."""
    body = """
        buda::def_layer 5 M5 V TOP 30
        buda::def_layer 6 M6 H TOP 30
        buda::add_block a 0 0 100 100
        buda::add_block b 300 0 400 100
        buda::add_net n1 a.o b.i
        buda::run_bundler STRICT
        buda::generate_topologies
        buda::run_planner 3
        buda::stop"""
    out = _tcl(tmp_path, body)
    assert "[Planner] Bundle" in out, out          # every line, as before
    assert "Runtime summary" not in out, out       # and no end report
    assert not (tmp_path / "log").exists()         # and no log written
