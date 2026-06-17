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

"""An unrecognised .buda command is a fatal input error, not a silent no-op.

Regression (flow/unknown_cmd_check.flow): a script used `add_layer` (the command
is `def_layer`).  do_command used to silently skip anything it didn't recognise,
so the design ran with no layers defined and no warning.  do_command now exits
with a clear "did you mean" message.  The companion `exit` command lets a script
stop mid-flow on purpose.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
import buda_cli  # noqa: E402

import pytest    # noqa: E402


def _session():
    sess = buda_cli.BudaSession()
    sess.no_viz = True
    return sess


def test_unknown_command_is_fatal():
    sess = _session()
    with pytest.raises(SystemExit) as ei:
        sess.do_command("add_layer M3 V TOP 10")   # typo: the command is def_layer
    assert ei.value.code == 1


def test_unknown_command_suggests_closest(capsys):
    sess = _session()
    with pytest.raises(SystemExit):
        sess.do_command("add_layer M3 V TOP 10")
    out = capsys.readouterr().out
    assert "unknown command 'add_layer'" in out
    assert "def_layer" in out          # "did you mean" suggestion


def test_known_command_is_not_rejected():
    # A valid command must not trip the unknown-command guard.
    sess = _session()
    sess.do_command("def_layer 4 M4 H TOP 1.0")    # must not raise SystemExit
    sess.do_command("add_block CPU 0 0 100 100")
    assert sess.fp.has_block("CPU")


def test_comments_and_blank_lines_are_ignored():
    sess = _session()
    sess.do_command("")                 # blank
    sess.do_command("   ")              # whitespace only
    sess.do_command("# this is a comment")
    # None of the above should raise; reaching here means they were skipped.


def test_exit_command_stops_with_code_zero():
    sess = _session()
    with pytest.raises(SystemExit) as ei:
        sess.do_command("exit")
    assert ei.value.code == 0


def test_exit_command_with_explicit_code():
    sess = _session()
    with pytest.raises(SystemExit) as ei:
        sess.do_command("exit 2")
    assert ei.value.code == 2


def test_exit_command_with_bad_code_is_error():
    sess = _session()
    with pytest.raises(SystemExit) as ei:
        sess.do_command("exit oops")    # non-integer code → error exit
    assert ei.value.code == 1


def test_known_commands_stay_in_sync_with_dispatch():
    """KNOWN_COMMANDS (used for the typo suggestion) must list exactly the
    commands do_command actually dispatches, so suggestions never drift."""
    src_path = os.path.join(os.path.dirname(__file__), '..', '..', 'src', 'buda_cli.py')
    with open(src_path) as f:
        src = f.read()
    # Only scan the dispatch body, not the KNOWN_COMMANDS literal itself.
    dispatch = set(re.findall(r'cmd == "([a-z_]+)"', src))
    assert dispatch == set(buda_cli.KNOWN_COMMANDS)
