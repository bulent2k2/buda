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

"""BUDA-1908 — `set_feedthru` on a multi-rect block (teg_multirect_status.md
open 7).

Feedthru is single-rect only in the engine: the trunk generator's
"MVP: single-rect only" gate (src/topology.cpp) skips multi-rect blocks with
no report, so a declaration ENABLING feedthru for one states an intent the
engine silently drops.  The CLI handler now warns at DECLARATION time —
once per command, naming every affected block — while the declaration still
takes effect for every single-rect block it names.
"""
import contextlib
import io

import buda_cli


def _session(cmds):
    s = buda_cli.BudaSession()
    s.no_viz = True
    for c in cmds:
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(c)
    return s


_SETUP = [
    "def_layer 4 M4 H TOP 0",
    "def_layer 5 M5 V TOP 0",
    "add_block solo 0 0 100 100",
    "add_block L rect 200 0 300 400 rect 200 0 600 100",
    "add_block T rect 700 300 900 400 rect 700 0 900 100",
]


def _do(s, cmd):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command(cmd)
    return buf.getvalue()


def test_named_multirect_block_warns_and_others_still_take_effect():
    s = _session(_SETUP)
    out = _do(s, "set_feedthru solo,L * on")
    line = next((l for l in out.splitlines() if "BUDA-1908" in l), None)
    assert line is not None, out
    assert "WARNING" in line
    assert "single-rect only" in line
    assert "L" in line.split(":")[-1]
    # The declaration still succeeded for the single-rect block it names.
    assert s.fp.get_feedthru("solo", 4)
    # And the multi-rect block is not warned twice in one command.
    assert out.count("BUDA-1908") == 1


def test_wildcard_warns_once_listing_every_affected_block():
    s = _session(_SETUP)
    out = _do(s, "set_feedthru * * on")
    assert out.count("BUDA-1908") == 1, out
    line = next(l for l in out.splitlines() if "BUDA-1908" in l)
    # One warning, both multi-rect blocks named (sorted), the single-rect
    # one not.
    assert "L, T" in line
    assert "solo" not in line
    assert s.fp.get_feedthru("solo", 4)


def test_no_warning_for_single_rect_blocks():
    s = _session(_SETUP)
    out = _do(s, "set_feedthru solo * on")
    assert "BUDA-1908" not in out, out
    assert s.fp.get_feedthru("solo", 4)


def test_off_is_not_warned():
    # A multi-rect block never relays regardless of the flag, so disabling
    # feedthru for one changes nothing AND the outcome matches the intent —
    # warning would be noise.
    s = _session(_SETUP)
    out = _do(s, "set_feedthru L * off")
    assert "BUDA-1908" not in out, out
