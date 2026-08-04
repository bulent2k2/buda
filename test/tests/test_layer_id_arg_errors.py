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

"""A layer NAME where a layer ID belongs is a named error, not a traceback.

`def_track_pattern` / `add_grid_override` take the layer *id* (the number in
`def_layer <id> <name> …`), but the *name* ('M4') is what the rest of a script
reads back, so reaching for the name here is the classic beginner slip.  It used
to surface as a raw

    ValueError: invalid literal for int() with base 10: 'M4'

traceback out of `int(args[0])` — naming neither the command, nor the argument,
nor the fix.  `require_layer_id` / `require_number` turn it into a flow-stopping
error that names the exact id to type, and the runtime summary now headlines the
diagnostic so the fix is visible on the terminal (not just in the flow log).
"""
import contextlib
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
import buda_cli  # noqa: E402
from buda_cmds._options import require_layer_id, require_number  # noqa: E402


class _FakeSession:
    def __init__(self, name_map=None):
        self._layer_name_map = dict(name_map or {})


# ── require_layer_id ─────────────────────────────────────────────────────────
def test_numeric_layer_id_passes_through(capsys):
    s = _FakeSession({"M4": 4})
    assert require_layer_id("cmd", "4", s) == 4
    assert require_layer_id("cmd", "-1", s) == -1        # sentinel ids allowed
    assert capsys.readouterr().out == ""


def test_layer_name_names_the_id_to_type(capsys):
    """The reported bug: `add_grid_override M4 …`."""
    s = _FakeSession({"M4": 4, "M5": 5})
    with pytest.raises(SystemExit) as ei:
        require_layer_id("add_grid_override", "M4", s,
                         usage="add_grid_override <layer_id> …")
    assert ei.value.code != 0
    out = capsys.readouterr().out
    assert "use layer id 4" in out          # the fix, front-loaded
    assert "'M4'" in out                    # what they typed
    assert "add_grid_override" in out       # which command
    assert "Usage:" in out


def test_layer_name_case_slip_still_resolves(capsys):
    s = _FakeSession({"M4": 4})
    with pytest.raises(SystemExit):
        require_layer_id("def_track_pattern", "m4", s)
    assert "use layer id 4" in capsys.readouterr().out


def test_undeclared_name_lists_declared_layers(capsys):
    s = _FakeSession({"M4": 4, "M5": 5})
    with pytest.raises(SystemExit):
        require_layer_id("add_grid_override", "M9", s)
    out = capsys.readouterr().out
    assert "'M9'" in out
    assert "M4=4" in out and "M5=5" in out          # ordered by id
    assert "use layer id" not in out                # nothing to suggest


def test_no_layers_declared_points_at_def_layer(capsys):
    with pytest.raises(SystemExit):
        require_layer_id("def_track_pattern", "M4", _FakeSession())
    out = capsys.readouterr().out
    assert "No layers are declared yet" in out and "def_layer" in out


def test_missing_session_does_not_crash(capsys):
    # Callers without a session still get a named error, never a TypeError.
    with pytest.raises(SystemExit):
        require_layer_id("cmd", "M4")
    assert "expected a numeric layer id" in capsys.readouterr().out


# ── require_number ───────────────────────────────────────────────────────────
def test_require_number_parses(capsys):
    assert require_number("cmd", "<origin>", "-2.5") == -2.5
    assert require_number("cmd", "<x1>", "640.0", integer=True) == 640
    assert capsys.readouterr().out == ""


def test_require_number_names_the_argument(capsys):
    with pytest.raises(SystemExit) as ei:
        require_number("add_grid_override", "<y1>", "zz", integer=True,
                       usage="add_grid_override <layer_id> <x1> <y1> …")
    assert ei.value.code != 0
    out = capsys.readouterr().out
    assert "<y1>" in out and "'zz'" in out and "Usage:" in out


# ── end-to-end command wiring ────────────────────────────────────────────────
def _session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("def_layer 4 M4 H TOP 55.56")
        s.do_command("def_layer 5 M5 V TOP 55.56")
    return s


def _expect_exit(cmd):
    s = _session()
    out = io.StringIO()
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit):
        s.do_command(cmd)
    return out.getvalue()


def test_add_grid_override_layer_name_is_a_named_error():
    # Verbatim from the bug report (M4 where 4 belongs).
    out = _expect_exit(
        "add_grid_override M4  20   0 640  80   0  VDD 10 1 _ 1 1 GND 10 1 _ 1 1")
    assert "use layer id 4" in out and "'M4'" in out


def test_def_track_pattern_layer_name_is_a_named_error():
    out = _expect_exit("def_track_pattern M4 -200  POWER 2 1  SIGNAL 1 0.5")
    assert "use layer id 4" in out and "def_track_pattern" in out


def test_bad_coordinate_names_the_argument():
    out = _expect_exit("add_grid_override 4 20 zz 640 80 0 VDD 10 1")
    assert "<y1>" in out and "'zz'" in out


def test_bad_slot_width_names_the_slot():
    out = _expect_exit("def_track_pattern 5 0 SIGNAL wide 1")
    assert "<width>" in out and "'SIGNAL'" in out and "'wide'" in out


def test_valid_numeric_ids_still_work():
    """No regression: the correct form is unaffected."""
    s = _session()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        s.do_command("def_track_pattern 4 -200  POWER 2 1  SIGNAL 1 0.5")
        s.do_command("add_grid_override 4 20 0 640 80 0  VDD 10 1  SIGNAL 1 1")
    assert s.routing_grid.has_layer(4)
    assert "Override on layer 4" in out.getvalue()


# ── the diagnostic reaches the terminal, not just the flow log ───────────────
def test_headline_prefers_the_error_over_a_trailing_usage_line():
    """A multi-line diagnostic used to headline its trailing Usage line,
    burying the actionable message in the flow log."""
    lines = ["Error: add_grid_override: use layer id 4, not the name 'M4' (…).",
             "  Usage: add_grid_override <layer_id> <x1> <y1> …"]
    assert buda_cli.BudaSession._extract_headline(lines).startswith("Error:")


def test_headline_still_prefers_summary_markers_when_no_error():
    lines = ["some chatter", "42 bundles", "trailing noise"]
    assert buda_cli.BudaSession._extract_headline(lines) == "42 bundles"


def test_a_warning_does_not_displace_the_summary():
    """ERROR-only by design: a warning is incidental to a command whose
    summary is still the story (the '!' marker + [N warn] flag surface it)."""
    lines = ["Warning: something odd", "42 bundles"]
    assert buda_cli.BudaSession._extract_headline(lines) == "42 bundles"


def test_plural_errors_prose_is_not_mistaken_for_a_diagnostic():
    lines = ["Errors found: 3", "42 bundles"]
    assert buda_cli.BudaSession._extract_headline(lines) == "42 bundles"


def test_summary_line_does_not_repeat_the_error_count():
    out = io.StringIO()
    buda_cli.BudaSession._emit_cmd_summary(
        out, "add_grid_override M4", 0.0, 2, 0, 1,
        "Error: add_grid_override: use layer id 4, not the name 'M4'.")
    line = out.getvalue()
    assert "[1 err]" not in line          # the line already says "Error:"
    assert "use layer id 4" in line       # and the fix survives truncation
    assert line.startswith("x ")          # the failure marker still shows


def test_summary_line_keeps_the_count_for_a_non_error_headline():
    out = io.StringIO()
    buda_cli.BudaSession._emit_cmd_summary(
        out, "run_planner", 0.0, 9, 0, 2, "42 bundles")
    assert "[2 err]" in out.getvalue()


def test_summary_line_keeps_the_warn_count_beside_an_error_headline():
    """Only the redundant err count is dropped — a warn count still carries
    information the "Error:" line does not."""
    out = io.StringIO()
    buda_cli.BudaSession._emit_cmd_summary(
        out, "cmd", 0.0, 4, 3, 1, "Error: cmd: use layer id 4")
    line = out.getvalue()
    assert "[1 err]" not in line and "[3 warn]" in line
