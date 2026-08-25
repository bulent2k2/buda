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

"""The user-defined `alias` command.

`alias <new> <command>` adds a SPELLING for an existing command; it never
replaces one (a name that is already a real command is refused — no
Tcl-`rename`-style shadowing).  Resolution happens once, at the do_command
choke point BEFORE recording, so a flow using an alias records CANONICAL
and replays anywhere — the portability property that lets a sourced flow
use aliases without becoming environment-dependent.  `unalias` removes
one.
"""
import contextlib
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

import buda_cli
from subprocess_env import buda_env

_ROOT = Path(__file__).parents[2]


def _session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    return s


def _run(s, cmd):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command(cmd)
    return buf.getvalue()


def test_alias_resolves_to_its_target_handler():
    # A defined alias reaches the SAME handler as its target — proven by the
    # target's own error text (run_detailed_nuts before run_nuts).
    s = _session()
    _run(s, "alias qr run_detailed_nuts")
    out = _run(s, "qr")
    assert "run_detailed_nuts requires run_nuts" in out, out


def test_alias_records_canonical_not_the_spelling(tmp_path):
    # The portability property: a flow using an alias records the CANONICAL
    # command (and NOT the `alias` definition), so the flattened trace
    # replays with no alias defined.
    flow = tmp_path / "f.buda"
    flow.write_text(
        "alias qr run_detailed_nuts\n"
        "def_layer 4 M4 H TOP 50\n"
        "def_layer 5 M5 V TOP 50\n"
        "def_track_pattern 4 0 POWER 2 1 (SIGNAL 1 0.5)x4\n"
        "def_track_pattern 5 0 POWER 2 1 (SIGNAL 1 0.5)x4\n"
        "add_block A 0 0 100 100\n"
        "add_block B 300 0 400 100\n"
        "add_net n0 A.tx B.rx\n"
        "run_bundler strict\n"
        "generate_topologies\n"
        "run_planner 3\n"
        "run_nuts\n"
        "qr\n")
    rec = tmp_path / "rec.buda"
    env = buda_env(_ROOT, "build", "src")
    env["BUDA_RECORD"] = str(rec)
    r = subprocess.run(
        [sys.executable, str(_ROOT / "src" / "buda_cli.py"),
         "--no-viz", str(flow)],
        capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode == 0, (r.stdout[-800:], r.stderr[-400:])
    body = [ln for ln in rec.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")]
    assert "run_detailed_nuts" in body, body
    assert not any(ln.startswith("alias") or ln.startswith("qr")
                   for ln in body), body


def test_alias_cannot_shadow_a_real_command():
    s = _session()
    with pytest.raises(SystemExit):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            s.do_command("alias run_nuts run_detailed_nuts")
    assert "shadow a real command" in buf.getvalue()


def test_alias_target_must_be_a_command():
    s = _session()
    with pytest.raises(SystemExit):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            s.do_command("alias foo not_a_command")
    assert "not a command" in buf.getvalue()


def test_alias_target_may_be_an_existing_alias():
    # Chained definition resolves to the underlying real command (stored
    # resolved, so a chain can never cycle).
    s = _session()
    _run(s, "alias a run_detailed_nuts")
    _run(s, "alias b a")
    assert s._aliases["b"] == "run_detailed_nuts"


def test_redefining_an_alias_notes_the_change():
    s = _session()
    _run(s, "alias qr run_nuts")
    out = _run(s, "alias qr run_detailed_nuts")
    assert "redefining alias 'qr'" in out
    assert s._aliases["qr"] == "run_detailed_nuts"


def test_bare_alias_lists_and_one_arg_shows():
    s = _session()
    assert "No aliases defined" in _run(s, "alias")
    _run(s, "alias qr run_detailed_nuts")
    assert "qr -> run_detailed_nuts" in _run(s, "alias")
    assert "qr -> run_detailed_nuts" in _run(s, "alias qr")
    assert "is a command, not an alias" in _run(s, "alias run_nuts")
    assert "No alias 'nope'" in _run(s, "alias nope")


def test_unalias_removes_and_warns_when_absent():
    s = _session()
    _run(s, "alias qr run_detailed_nuts")
    _run(s, "unalias qr")
    assert "qr" not in s._aliases
    assert "no alias 'nope'" in _run(s, "unalias nope").lower()


def test_alias_joins_the_did_you_mean_pool():
    # A typo of a user alias is suggested, like a typo of a real command.
    s = _session()
    _run(s, "alias myroute run_detailed_nuts")
    with pytest.raises(SystemExit):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            s.do_command("myroutx")
    assert "Did you mean 'myroute'?" in buf.getvalue()
