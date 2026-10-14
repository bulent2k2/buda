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

"""Junction reach is the default for top-down designs (#991).

A stub that meets a perpendicular partner ends at the partner's far edge, not
its centre, so every partner bit is reachable.  The default (variable unset,
or `topdown`) applies it to a design with no bottom-up marks; `0`/`off` turns
it off; `1` applies it to every design.  The session decides per command, so
the verdict is read through `buda.junction_reach_active()`.
"""
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
import buda_cli  # noqa: E402
import buda  # noqa: E402

_VAR = "BUDA_NUTS_JUNCTION_REACH"
_FLOW = os.path.join(os.path.dirname(__file__), '..', '..', 'flow',
                     'nuts_corner_touch.buda')


class _Bdb:
    def __init__(self, marked):
        self._marked = marked

    def bottom_up_cells(self):
        return ["core_cell"] if self._marked else []


def _active(monkeypatch, value, marked):
    if value is None:
        monkeypatch.delenv(_VAR, raising=False)
    else:
        monkeypatch.setenv(_VAR, value)
    s = buda_cli.BudaSession()
    s.bdb = _Bdb(marked)
    s._sync_junction_reach()
    return buda.junction_reach_active()


@pytest.mark.parametrize("value", [None, "", "topdown"])
def test_default_is_on_for_a_top_down_design_and_off_with_bottom_up_marks(
        monkeypatch, value):
    assert _active(monkeypatch, value, marked=False) is True
    assert _active(monkeypatch, value, marked=True) is False


@pytest.mark.parametrize("value", ["0", "off"])
def test_zero_and_off_turn_it_off_for_every_design(monkeypatch, value):
    assert _active(monkeypatch, value, marked=False) is False
    assert _active(monkeypatch, value, marked=True) is False


def test_one_applies_it_to_every_design(monkeypatch):
    assert _active(monkeypatch, "1", marked=False) is True
    assert _active(monkeypatch, "1", marked=True) is True


def test_the_variable_is_re_read_on_every_command(monkeypatch):
    # No state is cached from the first call: off, then the default, then `1`
    # on a bottom-up-marked design, in one process and in any order.
    assert _active(monkeypatch, "0", marked=False) is False
    assert _active(monkeypatch, None, marked=False) is True
    assert _active(monkeypatch, None, marked=True) is False
    assert _active(monkeypatch, "1", marked=True) is True
    assert _active(monkeypatch, "off", marked=False) is False


def _overlaps(monkeypatch, value, tmp_path, keep_switch):
    """Run nuts_corner_touch in-process.  The flow pins the pre-lever spans
    itself (`set_junction_reach off`); keep_switch=False runs a copy without
    that line (and with its sidecar, found by the flow's name) so the
    environment alone decides."""
    if value is None:
        monkeypatch.delenv(_VAR, raising=False)
    else:
        monkeypatch.setenv(_VAR, value)
    flow = os.path.abspath(_FLOW)
    if not keep_switch:
        src = open(flow).read()
        assert "set_junction_reach off\n" in src
        flow = str(tmp_path / "nuts_corner_touch.buda")
        with open(flow, "w") as f:
            f.write(src.replace("set_junction_reach off\n", ""))
        shutil.copy(os.path.abspath(_FLOW).replace(".buda", ".json"),
                    str(tmp_path / "nuts_corner_touch.json"))
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.do_command(f"source {flow}")
    return s.nuts_result.num_overlaps


def test_the_default_run_uses_the_longer_spans(monkeypatch, tmp_path):
    # nuts_corner_touch is built from the pre-lever spans: with the lever off
    # it places clean, and a copy without the flow's own switch, on the default,
    # does not -- the lever really is in force.
    assert _overlaps(monkeypatch, "0", tmp_path, keep_switch=False) == 0
    assert _overlaps(monkeypatch, None, tmp_path, keep_switch=False) == 1


def _typed(monkeypatch, env, line, marked):
    if env is None:
        monkeypatch.delenv(_VAR, raising=False)
    else:
        monkeypatch.setenv(_VAR, env)
    s = buda_cli.BudaSession()
    s.bdb = _Bdb(marked)
    s.do_command(line)
    return buda.junction_reach_active()


@pytest.mark.parametrize("marked", [False, True])
def test_set_junction_reach_on_and_off_outrank_the_variable(monkeypatch, marked):
    assert _typed(monkeypatch, "0", "set_junction_reach on", marked) is True
    assert _typed(monkeypatch, "1", "set_junction_reach off", marked) is False


def test_set_junction_reach_topdown_is_the_default_policy(monkeypatch):
    assert _typed(monkeypatch, "0", "set_junction_reach topdown", False) is True
    assert _typed(monkeypatch, "1", "set_junction_reach topdown", True) is False


def test_set_junction_reach_env_hands_the_decision_back(monkeypatch):
    monkeypatch.setenv(_VAR, "0")
    s = buda_cli.BudaSession()
    s.bdb = _Bdb(False)
    s.do_command("set_junction_reach on")
    assert buda.junction_reach_active() is True
    s.do_command("set_junction_reach env")
    assert buda.junction_reach_active() is False


def test_set_junction_reach_rejects_a_bad_value_and_keeps_the_setting(
        monkeypatch, capsys):
    monkeypatch.delenv(_VAR, raising=False)
    s = buda_cli.BudaSession()
    s.bdb = _Bdb(False)
    s.do_command("set_junction_reach off")
    s.do_command("set_junction_reach maybe")
    assert "expects on|off|topdown|env" in capsys.readouterr().out
    s.do_command("set_junction_reach")
    assert buda.junction_reach_active() is False


def test_a_flow_can_switch_it_off_itself(monkeypatch, tmp_path):
    # The shipped flow's own `set_junction_reach off` beats the environment,
    # even `1` (every design): it places clean where the copy without the
    # switch does not.
    assert _overlaps(monkeypatch, "1", tmp_path, keep_switch=True) == 0
    assert _overlaps(monkeypatch, None, tmp_path, keep_switch=True) == 0
    assert _overlaps(monkeypatch, "1", tmp_path, keep_switch=False) == 1
