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

"""The `BUDA_NDR_METAL` study knob, and the reporting it exists to compare.

`docs/internal/ndr_metal_default_study.md` measured whether metal-shaped
quantization should become the default reading for an absolute NDR value and
concluded NO — it strands bits on three of five vehicles.  The knob is what
makes that measurement re-runnable, so it has to keep working, and it has to
stay a STUDY knob: declaration only, never overriding a persisted design's
own reading.
"""
import os
import sys

import pytest

import buda

sys.path[:0] = ["build", "src", "tools"]
import buda_cli  # noqa: E402

# Dense H / sparse V: `width 3` needs 2 dense slots either way, but on the
# sparse layer channel says 1 (3 <= pitch 10) and metal says 2 (one slot is
# only 2 units of metal).  The one stack where the two readings visibly part.
_STACK = """def_layer 3 M3 H 20
def_layer 4 M4 V 20
def_track_pattern 3 0 VDD 2 1 (_ 1 1)x12 GND 2 1
def_track_pattern 4 0 VDD 10 2 (_ 2 2)x4 GND 10 2
"""


@pytest.fixture
def no_env():
    """The knob must not leak between tests, nor out of the suite."""
    prev = os.environ.pop("BUDA_NDR_METAL", None)
    yield
    if prev is None:
        os.environ.pop("BUDA_NDR_METAL", None)
    else:
        os.environ["BUDA_NDR_METAL"] = prev


def _session(body):
    s = buda_cli.BudaSession()
    for raw in body.splitlines():
        line = raw.split("#")[0].strip()
        if line:
            s.do_command(line)
    return s


def _slots_on(s, rule, lid):
    from buda_cmds import ndr_cmds
    spec = ndr_cmds._spec_of(s, rule)
    return ndr_cmds.ndr_spec_for_layer(s, spec, lid).width_slots


def test_the_compiled_default_is_CHANNEL(no_env):
    """Unset means unchanged: the study measured a flip that has not
    happened, and this is what pins that."""
    s = _session(_STACK + "def_ndr r width 3\n")
    assert _slots_on(s, "r", 4) == 1


def test_the_env_knob_selects_metal(no_env):
    os.environ["BUDA_NDR_METAL"] = "1"
    s = _session(_STACK + "def_ndr r width 3\n")
    assert _slots_on(s, "r", 4) == 2, "the study knob must move the reading"


def test_only_the_exact_value_1_enables_it(no_env):
    """A knob that turns on for any non-empty string turns on for `0`, and a
    study whose control arm was silently the treatment arm measures nothing.
    `tools/ndr_metal_study.py` sets the variable to "0" for its baseline."""
    for val in ("0", "", "true", "yes", "2"):
        os.environ["BUDA_NDR_METAL"] = val
        s = _session(_STACK + "def_ndr r width 3\n")
        assert _slots_on(s, "r", 4) == 1, f"BUDA_NDR_METAL={val!r} enabled it"


def test_the_token_still_works_with_the_env_off(no_env):
    s = _session(_STACK + "def_ndr r width 3 metal\n")
    assert _slots_on(s, "r", 4) == 2


def test_a_RESTORED_rule_keeps_its_own_reading(tmp_path, no_env):
    """The knob is declaration-only.

    A rule's reading is persisted (v28 `ndr_rule.metal`) because it is part
    of the DESIGN, not of the process that reopens it — so a study run must
    not silently re-read a stored channel rule as metal, which would change
    what a checkpointed design means."""
    db = str(tmp_path / "m.bdb")
    _session(f"open_bdb {db}\n" + _STACK + "def_ndr r width 3\n")
    os.environ["BUDA_NDR_METAL"] = "1"
    s2 = _session(f"open_bdb {db}\n" + _STACK)
    from buda_cmds import ndr_cmds
    assert "r" in ndr_cmds._rules(s2), "the rule must restore at all"
    assert _slots_on(s2, "r", 4) == 1, "a stored CHANNEL rule stayed channel"


def test_a_restored_METAL_rule_stays_metal_with_the_env_off(tmp_path, no_env):
    """The mirror, so the test above cannot pass by the reading simply never
    being restored."""
    db = str(tmp_path / "m2.bdb")
    _session(f"open_bdb {db}\n" + _STACK + "def_ndr r width 3 metal\n")
    s2 = _session(f"open_bdb {db}\n" + _STACK)
    assert _slots_on(s2, "r", 4) == 2


def test_the_declaration_report_quotes_the_rules_OWN_reading(capsys, no_env):
    """The header printed the CHANNEL spread for every absolute rule.

    It resolved through `ndr_resolve_for_pitch` — the channel branch alone —
    so a `metal` rule was advertised at a slot count the engine never
    charges: measured "L4:1/0" for a rule that resolves to 2 slots/bit
    there.  A report with a layer in hand is bound by the same single-
    sourcing rule as the R9 audit, and reads `ndr_spec_for_layer`."""
    _session(_STACK + "def_ndr r width 3 metal\n")
    line = next(ln for ln in capsys.readouterr().out.splitlines()
                if ln.startswith("[NDR] rule 'r':"))
    assert "L3:2/0" in line and "L4:2/0" in line, line

    _session(_STACK + "def_ndr c width 3\n")
    line = next(ln for ln in capsys.readouterr().out.splitlines()
                if ln.startswith("[NDR] rule 'c':"))
    # The channel rule is untouched by the fix — 1 slot on the coarse layer.
    assert "L3:2/0" in line and "L4:1/0" in line, line
