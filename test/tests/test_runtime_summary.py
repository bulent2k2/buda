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

"""The runtime summary must be emitted exactly once.

It is now printed *before* a blocking `visualize` window opens (so it survives
the macOS .app quit-on-window-close, which terminates the process before
main()'s finally runs) and again in the finally — guarded to fire only once.
"""
import io
import contextlib

import buda_cli


def _session_with_stats():
    s = buda_cli.BudaSession()
    # (cmd_line, elapsed, nlines, nwarn, nerr)
    s._cmd_stats = [("run_nuts", 0.01, 1, 0, 0),
                    ("run_detailed_nuts", 0.02, 1, 0, 0)]
    return s


def test_end_report_prints_once():
    s = _session_with_stats()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s._print_end_report()          # e.g. before a blocking visualize
        s._print_end_report()          # e.g. main()'s finally — must be a no-op
    out = buf.getvalue()
    assert out.count("Runtime summary") == 1, out
    assert s._end_report_done is True


def test_end_report_emits_the_table():
    s = _session_with_stats()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s._print_end_report()
    out = buf.getvalue()
    assert "run_nuts" in out
    assert "total (2 commands)" in out


def test_no_stats_no_output():
    # A run with no recorded commands prints nothing (guarded in
    # print_runtime_summary) but is still marked done so the finally is a no-op.
    s = buda_cli.BudaSession()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s._print_end_report()
    assert "Runtime summary" not in buf.getvalue()
