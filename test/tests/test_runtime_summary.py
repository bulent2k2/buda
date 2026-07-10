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


def _flags_while_sourcing(tmp_path, files, entry):
    """Source `entry` and record (command, _at_last_command) for each real
    command run. `files` maps basename -> text written into tmp_path."""
    s = buda_cli.BudaSession()
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    seen = []
    orig = s.run_command

    def spy(line):
        st = line.strip()
        if st and not st.startswith('#') and not st.startswith('source'):
            seen.append((st.split()[0], s._at_last_command))
        return orig(line)

    s.run_command = spy
    s._at_last_command = True                     # as main() sets it
    orig(f"source {tmp_path / entry}")
    return seen


def test_at_last_command_only_the_final_command(tmp_path):
    # Only the LAST real command of the flow is flagged — an interleaved
    # command (with more to follow) is not, so a mid-flow visualize won't
    # early-print a partial summary.
    seen = _flags_while_sourcing(tmp_path, {
        "f.buda": "# header\n"
                  "def_layer 3 M3 H TOP 0.0\n"
                  "\n"
                  "def_layer 4 M4 V TOP 0.0\n"
                  "# trailing comment\n",
    }, "f.buda")
    assert [flag for _, flag in seen] == [False, True], seen


def test_at_last_command_nested_source_tail(tmp_path):
    # A command that is the last line of a child sourced as the parent's last
    # line IS the flow's final command; when the parent continues after the
    # source, the child's last command is NOT.
    tail = _flags_while_sourcing(tmp_path, {
        "parent.buda": "def_layer 3 M3 H TOP 0.0\nsource child.buda\n",
        "child.buda":  "def_layer 4 M4 V TOP 0.0\n",
    }, "parent.buda")
    assert tail == [("def_layer", False), ("def_layer", True)], tail

    notail = _flags_while_sourcing(tmp_path, {
        "parent.buda": "source child.buda\ndef_layer 5 M5 V TOP 0.0\n",
        "child.buda":  "def_layer 4 M4 V TOP 0.0\n",
    }, "parent.buda")
    assert notail == [("def_layer", False), ("def_layer", True)], notail
