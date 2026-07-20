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

"""Command-capture shim for the web backend.

`run_one(session, cmd_line)` drives ONE `.buda` command through the same
`BudaSession.do_command` dispatch the CLI uses, but instead of logging to a
flow-log and printing a summary (as `buda_cli.run_command` does,
`buda_cli.py:266-330`), it CAPTURES all output — Python prints AND C++
`std::cout`/`std::cerr` routed through `buda.ostream_redirect` — and RETURNS it
as a structured dict.

Load-bearing invariant: `BudaSession.do_command` calls `sys.exit(1)` on an
unknown command (buda_cli.py:426), and some handlers `sys.exit` on bad input.
`run_one` catches `SystemExit` (and any other exception) so a bad command can
never kill the server process — it returns `{ok: False, ...}` instead.
"""
import io
import sys

import buda
from buda_cli import BudaSession


def run_one(session, cmd_line):
    """Run one command string against `session`; capture output, never raise.

    Returns a dict:
        ok:            bool  — True iff the command completed without raising.
        error:         None | ["exit", code] | ["error", repr]
        log_lines:     list[str] — captured stdout+stderr (Python + C++), split.
        num_warnings:  int   — lines containing 'warning' (case-insensitive).
        num_errors:    int   — lines containing 'error'.
        summary:       str   — the most summary-like line (reuses the CLI's
                               headline heuristic), '' if none.
    """
    real_out, real_err = sys.stdout, sys.stderr
    cap = io.StringIO()
    sys.stdout = cap
    sys.stderr = cap
    raised = None
    try:
        # ostream_redirect routes C++ std::cout/std::cerr into sys.stdout (now
        # `cap`), so engine chatter is captured alongside Python prints.
        with buda.ostream_redirect():
            session.do_command(cmd_line)
    except SystemExit as e:
        # `exit` command and fail-fast handlers (unknown command, bad args).
        raised = ["exit", e.code]
    except BaseException as e:  # noqa: BLE001 — a bad command must not kill the server
        raised = ["error", repr(e)]
    finally:
        sys.stdout, sys.stderr = real_out, real_err

    text = cap.getvalue()
    lines = text.splitlines()
    nonblank = [ln for ln in lines if ln.strip()]
    num_warnings = sum(1 for ln in lines if "warning" in ln.lower())
    num_errors = sum(1 for ln in lines if "error" in ln.lower())
    summary = BudaSession._extract_headline(nonblank) if nonblank else ""

    return {
        "ok": raised is None,
        "error": raised,
        "log_lines": lines,
        "num_warnings": num_warnings,
        "num_errors": num_errors,
        "summary": summary,
    }


def run_many(session, cmd_lines):
    """Run a batch of commands in order; return the list of per-command results.

    Commands run unconditionally (a failing command does not abort the batch) so
    the caller sees every command's outcome — the client decides what to do with
    a failure.  Blank / comment-only lines are skipped (return nothing for them).
    """
    results = []
    for cmd_line in cmd_lines:
        if not cmd_line or not cmd_line.strip():
            continue
        results.append(run_one(session, cmd_line))
    return results
