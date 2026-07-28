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

"""Unknown CLI options are a hard error, not a silent skip.

Many commands read a FIXED vocabulary of optional keyword tokens by a membership
test and used to silently ignore anything unrecognized — so `run_planner foo bar`
or `dump_topologies --problem` (typo) ran with the mistyped feature quietly off.
`reject_unknown_options` turns that into a flow-stopping error.  This covers the
shared helper plus a representative sample of the commands wired to it (the ones
reachable without a BDB / edit session).
"""
import contextlib
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
import buda_cli  # noqa: E402
from buda_cmds._options import looks_numeric, reject_unknown_options  # noqa: E402


# ── the shared helper ────────────────────────────────────────────────────────
def test_helper_noop_when_all_known(capsys):
    reject_unknown_options("cmd", ["a", "b"], ("a", "b", "c"))   # no raise
    assert capsys.readouterr().out == ""


def test_helper_rejects_unknown(capsys):
    with pytest.raises(SystemExit) as ei:
        reject_unknown_options("cmd", ["a", "zzz"], ("a", "b"))
    assert ei.value.code != 0
    out = capsys.readouterr().out
    assert "unknown option" in out and "'zzz'" in out
    assert "Valid options: a b" in out


def test_helper_plural_and_suggestion(capsys):
    with pytest.raises(SystemExit):
        reject_unknown_options("cmd", ["multi_trunc", "foo"], ("multi_trunk",))
    out = capsys.readouterr().out
    assert "unknown options" in out                       # plural
    assert "Did you mean 'multi_trunk'?" in out


def test_helper_also_accepted_is_silent(capsys):
    # A legacy no-op token is honored but not advertised.
    reject_unknown_options("cmd", ["legacy"], ("real",), also_accepted=("legacy",))
    assert capsys.readouterr().out == ""


def test_looks_numeric():
    assert looks_numeric("5") and looks_numeric("-3") and looks_numeric("2.5")
    assert not looks_numeric("hier") and not looks_numeric("signal_tracks")


# ── end-to-end command wiring (no BDB / edit session needed) ──────────────────
def _planned_session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in ("def_layer 4 M4 H 50", "def_layer 5 M5 V 50",
                  "def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
                  "add_block A 8000 10000 9000 11000",
                  "add_block B 2000 3000 3000 4000",
                  "add_bus dbus[8] A.p B.p", "run_bundler", "generate_topologies"):
            s.do_command(c)
    return s


def _expect_reject(cmd, needle="unknown option"):
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = io.StringIO()
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit):
        for setup in ("def_layer 4 M4 H 50", "def_layer 5 M5 V 50",
                      "add_block A 0 0 100 100", "add_block B 200 0 300 100"):
            s.do_command(setup)
        s.do_command(cmd)
    assert needle in out.getvalue(), (cmd, out.getvalue())


def test_run_planner_rejects_unknown():
    s = _planned_session()
    out = io.StringIO()
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit):
        s.do_command("run_planner foo bar")
    o = out.getvalue()
    assert "unknown options" in o and "'foo'" in o and "'bar'" in o


def test_run_planner_accepts_valid():
    s = _planned_session()
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("run_planner 3")            # numeric iterations, no raise
    assert s.bundles[0].plan.selected_topology_index >= 0


def test_dump_topologies_rejects_unknown_flag():
    s = _planned_session()
    out = io.StringIO()
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit):
        s.do_command("dump_topologies --problem")   # typo of --problems
    assert "unknown option" in out.getvalue()
    # A valid flag (and a bare hint) still work.
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("dump_topologies --problems")
        s.do_command("dump_topologies dbus")


def test_add_net_rejects_bad_direction():
    _expect_reject("add_net n1 A.o B.i unkown")     # typo of unknown/inout


def test_add_bus_rejects_bad_direction():
    _expect_reject("add_bus b[4] A.o B.i inou")     # typo of inout


def test_def_layer_rejects_bad_keyword():
    _expect_reject("def_layer 8 M8 H 50 span_mx 300")   # typo of span_max


def test_detour_channel_rejects_bad_dir():
    _expect_reject("detour_channel Q 50", needle="unknown direction char")


def test_valid_options_still_work():
    """Guards must not reject legitimate usage."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in ("def_layer 4 M4 H TOP 50 span_max 300",   # keyword arg
                  "def_layer 5 M5 V 50",
                  "detour_channel A 50",                     # all-sides
                  "add_block A 0 0 100 100 container",       # container flag
                  "add_block B 200 0 300 100 corner_margin dx 5",
                  "add_net n1 A.o B.i inout",                # direction keyword
                  "add_bus bus[4] A.o B.i unknown"):
            s.do_command(c)   # none should raise
