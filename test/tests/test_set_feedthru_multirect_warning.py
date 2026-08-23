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


def test_wildcard_before_block_declaration_warns_at_add_block():
    # Codex P2 on #834: `set_feedthru * * on` declared BEFORE any block
    # exists warned about nothing, so a later-declared multi-rect block
    # inherited the enabled policy silently — the exact ignored-intent case
    # the warning exists for, surviving a perfectly valid command order.
    # The add_block side now warns when an active setting resolves feedthru
    # ON for the new multi-rect block.
    s = _session(["def_layer 4 M4 H TOP 0", "set_feedthru * * on"])
    out = _do(s, "add_block L rect 200 0 300 400 rect 200 0 600 100")
    assert out.count("BUDA-1908") == 1, out
    line = next(l for l in out.splitlines() if "BUDA-1908" in l)
    assert "WARNING" in line and "'L'" in line and "single-rect only" in line
    # A single-rect block under the same active wildcard stays silent.
    out2 = _do(s, "add_block solo 0 0 100 100")
    assert "BUDA-1908" not in out2, out2


def test_per_layer_enable_before_block_declaration_warns_too():
    s = _session(["def_layer 4 M4 H TOP 0", "set_feedthru * M4 on"])
    out = _do(s, "add_block L rect 200 0 300 400 rect 200 0 600 100")
    assert out.count("BUDA-1908") == 1, out


def test_inactive_or_disabled_settings_stay_silent_at_add_block():
    # No feedthru declared at all: the add_block side must add nothing
    # (byte-identity where the feature is unused).
    s = _session(["def_layer 4 M4 H TOP 0"])
    out = _do(s, "add_block L rect 200 0 300 400 rect 200 0 600 100")
    assert "BUDA-1908" not in out, out
    # Declared but resolving OFF for the new block: silent too.
    s2 = _session(["def_layer 4 M4 H TOP 0", "set_feedthru * M4 off"])
    out2 = _do(s2, "add_block L rect 200 0 300 400 rect 200 0 600 100")
    assert "BUDA-1908" not in out2, out2


def test_both_sites_share_one_memo_no_double_fire():
    # Order A: wildcard first -> the add_block site warns; a later explicit
    # set_feedthru naming the SAME block says nothing new (one memoized
    # mechanism, said once per block).
    s = _session(["def_layer 4 M4 H TOP 0", "set_feedthru * * on"])
    out = _do(s, "add_block L rect 200 0 300 400 rect 200 0 600 100")
    assert out.count("BUDA-1908") == 1, out
    out2 = _do(s, "set_feedthru L * on")
    assert "BUDA-1908" not in out2, out2
    # A NEW multi-rect block under the still-active wildcard is a fresh
    # verdict and warns once for itself.
    out3 = _do(s, "add_block T rect 700 300 900 400 rect 700 0 900 100")
    assert out3.count("BUDA-1908") == 1 and "'T'" in out3, out3
    # Order B: block first, warned at set_feedthru declaration; repeating
    # the declaration for the same block stays quiet.
    s2 = _session(_SETUP)
    outb = _do(s2, "set_feedthru L * on")
    assert outb.count("BUDA-1908") == 1, outb
    outb2 = _do(s2, "set_feedthru L * on")
    assert "BUDA-1908" not in outb2, outb2


def test_off_is_not_warned():
    # A multi-rect block never relays regardless of the flag, so disabling
    # feedthru for one changes nothing AND the outcome matches the intent —
    # warning would be noise.
    s = _session(_SETUP)
    out = _do(s, "set_feedthru L * off")
    assert "BUDA-1908" not in out, out
