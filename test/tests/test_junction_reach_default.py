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


def _overlaps(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(_VAR, raising=False)
    else:
        monkeypatch.setenv(_VAR, value)
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.do_command(f"source {os.path.abspath(_FLOW)}")
    return s.nuts_result.num_overlaps


def test_the_default_run_uses_the_longer_spans(monkeypatch):
    # nuts_corner_touch is built from the pre-lever spans: off it places clean,
    # and the default run (lever on) does not -- the lever really is in force.
    assert _overlaps(monkeypatch, "0") == 0
    assert _overlaps(monkeypatch, None) == 1
