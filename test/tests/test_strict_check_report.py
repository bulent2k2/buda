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

"""`--strict-check` and `--report-json`: the design outcome as an exit code
and as data (Phase 0 of docs/internal/lefdef_interface_plan.md).

The gap these close: a flow whose `check_design` reported violations still
exited 0, so no CI/regression harness could gate on design quality — BUDA
failed loudly on a malformed *script* and silently on a broken *design*.

The load-bearing case is a flow ending in `exit`: that raises SystemExit,
which sails past end-of-run bookkeeping unless it is caught — and most real
flows end that way, so a naive implementation works only on the flows nobody
runs.
"""
import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).parents[2]
_CLI = _ROOT / "src" / "buda_cli.py"

# Minimal design that audits CLEAN at the nuts stage.
_CLEAN = """def_layer 4 M4 H TOP 30
def_layer 5 M5 V TOP 30
add_block a 0 0 10 10
add_block b 100 100 110 110
add_net n1 a.o b.i
run_bundler STRICT
generate_topologies
run_planner 1
run_nuts
check_design
"""

# `check_design dnuts` before run_detailed_nuts: the audit cannot run at all.
# "Did not demonstrate a clean design" is the question a harness asks, so this
# must fail under --strict-check just as violations do.
_CANNOT_RUN = _CLEAN.replace("check_design\n", "check_design dnuts\n")


def _run(tmp_path, script_text, *flags, name="t.buda"):
    p = tmp_path / name
    p.write_text(script_text)
    r = subprocess.run([sys.executable, str(_CLI), "--no-viz", *flags, str(p)],
                       capture_output=True, text=True, cwd=str(tmp_path))
    return r


def test_clean_design_exits_zero_under_strict_check(tmp_path):
    r = _run(tmp_path, _CLEAN, "--strict-check")
    assert r.returncode == 0, r.stdout[-2000:]


def test_unrunnable_audit_fails_only_under_strict_check(tmp_path):
    """The flag is what changes the verdict — the default stays exit 0 so
    existing scripts keep working (one release of opt-in, per the plan)."""
    assert _run(tmp_path, _CANNOT_RUN).returncode == 0
    r = _run(tmp_path, _CANNOT_RUN, "--strict-check")
    assert r.returncode == 3, r.stdout[-2000:]
    assert "strict-check" in r.stdout


def test_explicit_nonzero_exit_code_wins(tmp_path):
    """An explicit NON-ZERO `exit <code>` is the author stating a failure of
    their own; strict-check must not overwrite it with its own 3."""
    r = _run(tmp_path, _CANNOT_RUN + "exit 7\n", "--strict-check")
    assert r.returncode == 7, r.stdout[-2000:]


def test_explicit_zero_exit_does_not_suppress_the_verdict(tmp_path):
    """A bare `exit` (code 0) is how most flows in this repo normally
    terminate — 18 of them — so treating it as "the author forced success"
    would make --strict-check a no-op on essentially every real flow."""
    for ending in ("exit\n", "exit 0\n"):
        r = _run(tmp_path, _CANNOT_RUN + ending, "--strict-check")
        assert r.returncode == 3, (ending, r.stdout[-2000:])


def test_scripts_own_exit_3_is_not_blamed_on_strict_check(tmp_path):
    """`exit 3` from a script that never ran an audit must not print
    "design audit failed" — a false diagnostic is worse than none."""
    r = _run(tmp_path, _CLEAN.replace("check_design\n", "") + "exit 3\n")
    assert r.returncode == 3
    assert "strict-check" not in r.stdout, r.stdout[-2000:]
    assert "design audit failed" not in r.stdout


def test_report_lists_silent_setup_commands(tmp_path):
    """The report must list what RAN, not what was worth printing: the
    terminal summary drops silent instant commands (add_block, def_layer),
    which would make the report's list and its total quietly incomplete."""
    out = tmp_path / "run.json"
    _run(tmp_path, _CLEAN, "--report-json", str(out))
    d = json.loads(out.read_text())
    names = [c["command"].split()[0] for c in d["commands"]]
    for silent in ("def_layer", "add_block", "add_net"):
        assert silent in names, f"{silent} missing from the report: {names}"
    # …and they are marked as not surfaced, so a consumer can still tell
    # which ones the terminal showed.
    assert any(not c["surfaced"] for c in d["commands"])
    # Epsilon, not exact: total_seconds is round(sum(rounded values), 4), and
    # a 4-decimal value is not exactly representable in binary — so summing
    # the surfaced subset here can exceed the stored total by float dust
    # (measured flaking at 0.0029 >= 0.0029000000000000002, ~1/6 runs).  The
    # invariant is about coverage, not femtosecond accounting.
    assert d["total_seconds"] >= sum(
        c["seconds"] for c in d["commands"] if c["surfaced"]) - 1e-9


def test_report_written_for_a_flow_ending_in_exit(tmp_path):
    """Regression: `exit` raises SystemExit, which skipped the end-of-run
    bookkeeping entirely — so both the strict status and the report silently
    never happened on the flows that actually end that way."""
    out = tmp_path / "run.json"
    r = _run(tmp_path, _CLEAN + "exit\n", "--report-json", str(out))
    assert r.returncode == 0, r.stdout[-2000:]
    assert out.exists(), "report not written when the flow ends in `exit`"
    d = json.loads(out.read_text())
    assert d["audits"] and d["audits"][0]["stage"] == "nuts"
    assert d["audits"][0]["ok"] is True
    assert d["exit_status"] == 0
    assert any(c["command"].startswith("run_nuts") for c in d["commands"])


def test_report_records_violations_by_kind(tmp_path):
    """The report is the harness-facing artifact: it must carry the verdict,
    not just a runtime table."""
    out = tmp_path / "run.json"
    _run(tmp_path, _CANNOT_RUN, "--report-json", str(out))
    d = json.loads(out.read_text())
    a = d["audits"][0]
    assert a["ok"] is False and "run_detailed_nuts" in a["reason"]
    assert d["audit_failed"] is True
    # audit_failed is recorded even without --strict-check, so a harness can
    # gate on the data while the exit code stays back-compatible.
    assert d["exit_status"] == 0
    assert d["strict_check"] is False


def test_no_report_written_without_the_flag(tmp_path):
    r = _run(tmp_path, _CLEAN)
    assert r.returncode == 0
    assert not list(tmp_path.glob("*.json")), "report written unasked"


def test_diagnostic_counting_ignores_incidental_prose():
    """The per-command error/warning counts matched the bare substrings, so a
    net named `error_flag` inflated a run's error count.  Count markers, not
    words."""
    sys.path.insert(0, str(_ROOT / "src"))
    import buda_cli  # noqa: E402

    err, warn = buda_cli._DIAG_ERR_RE, buda_cli._DIAG_WARN_RE
    # Real diagnostics, in the forms the engines and CLI actually emit.
    assert err.search("  Error: run_nuts required first.")
    assert err.search("[BDB] ERROR: schema too new")
    assert warn.search("[Planner] WARNING: Bundle 7 overflow")
    assert warn.search("[DetailedNUTS] Warning: Layer 5 short")
    # Lowercase-with-colon is a REAL form here (the C++ emits it), so the
    # matcher must not become capitalisation-dependent.
    assert err.search("BDB SQL error: no such table")
    # Incidental prose must not count.
    assert not err.search("add_net error_flag drv.o rcv.i")
    assert not err.search("  bundle b_error_status placed")
    assert not warn.search("net warning_led connected")
    assert not err.search("no errors found")
    # A summary line is not a diagnostic -- counting it double-counts.
    assert not warn.search("2 warnings raised")
