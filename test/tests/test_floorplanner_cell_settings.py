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

"""Floorplanner per-cell settings command layer (cell_settings_ui.md).

Covers the GUI-free command layer behind the Cell Settings dialog and the
Selection-panel Bottom-Up quick toggle: the declarative CellSetting registry,
list_cell_settings (value + activate-eligibility per cell), set_cell_setting
(transition-gated writes, read-only guard), and the shared
bottom_up_congruence_issues helper the CLI's set_bottom_up uses too.

The dialog itself stays a thin, schema-driven view (headless Tk is not
unit-tested, matching the existing Optimize coverage boundary).
"""

import os
import sys

import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools import floorplanner_commands as fpc

import buda
from buda_session.util import bottom_up_congruence_issues


def _hier_state(x2=500, y2=0):
    """proc_cell (two pipe_cell children) instantiated twice, translated —
    the congruent bottom-up vehicle (mirrors test_hier_bottom_up)."""
    state = fpc.new_state()
    db = buda.BDB(":memory:")
    db.add_cell("proc_cell", 420, 200)
    db.add_cell("pipe_cell", 110, 80)
    db.add_inst_to_cell("proc_cell", "pa_i", "pipe_cell", 20, 60)
    db.add_inst_to_cell("proc_cell", "pb_i", "pipe_cell", 155, 60)
    db.add_inst("proc_i1", "proc_cell", "", 0, 0)
    db.add_inst("proc_i2", "proc_cell", "", x2, y2)
    state.bdb = db
    return state


def _row(rows, cell):
    match = [r for r in rows if r.cell == cell]
    assert match, f"no row for cell '{cell}' in {[r.cell for r in rows]}"
    return match[0]


# ── list_cell_settings ────────────────────────────────────────────────────────

def test_list_reports_value_and_eligibility_for_congruent_cell():
    state = _hier_state()
    rows = fpc.list_cell_settings(state)
    assert [r.cell for r in rows] == ["pipe_cell", "proc_cell"]   # sorted
    proc = _row(rows, "proc_cell")
    assert proc.instances == 2
    value, can_activate, reason = proc.settings["bottom_up"]
    assert value is False and can_activate is True and reason == ""
    # The four pipe_cell children (two per instance) are translated copies.
    pipe = _row(rows, "pipe_cell")
    assert pipe.instances == 4
    assert pipe.settings["bottom_up"][:2] == (False, True)


def test_list_flags_incongruent_cell_with_reason():
    state = _hier_state()
    # rotate_comp on a *hierarchical* block rewrites the children and keeps
    # orient='N' — exactly the case the geometric subtree compare catches.
    state.bdb.rotate_comp("proc_i2", 180)
    rows = fpc.list_cell_settings(state)
    value, can_activate, reason = _row(rows, "proc_cell").settings["bottom_up"]
    assert value is False and can_activate is False
    assert "subtree differs" in reason


def test_list_no_bdb_returns_empty():
    assert fpc.list_cell_settings(fpc.new_state()) == []


# ── set_cell_setting ──────────────────────────────────────────────────────────

def test_set_bottom_up_on_congruent_cell_persists():
    state = _hier_state()
    fpc.set_cell_setting(state, "proc_cell", "bottom_up", True)
    assert state.bdb.cell_bottom_up("proc_cell") is True
    assert state.bdb.bottom_up_cells() == ["proc_cell"]
    fpc.set_cell_setting(state, "proc_cell", "bottom_up", False)
    assert state.bdb.cell_bottom_up("proc_cell") is False


def test_set_bottom_up_refuses_incongruent_cell():
    state = _hier_state()
    state.bdb.rotate_comp("proc_i2", 180)
    with pytest.raises(ValueError, match="not congruent"):
        fpc.set_cell_setting(state, "proc_cell", "bottom_up", True)
    assert state.bdb.cell_bottom_up("proc_cell") is False   # flag stays off


def test_clearing_is_unconditional_even_when_incongruent():
    """A cell marked bottom_up that LATER becomes incongruent (a rotate in
    the same session, or a stale BDB) must always be clearable — the OFF
    transition is never gated by congruence (Codex #248 P2)."""
    state = _hier_state()
    fpc.set_cell_setting(state, "proc_cell", "bottom_up", True)
    state.bdb.rotate_comp("proc_i2", 180)                    # now incongruent
    rows = fpc.list_cell_settings(state)
    value, can_activate, _ = _row(rows, "proc_cell").settings["bottom_up"]
    assert value is True and can_activate is False           # "clear only"
    fpc.set_cell_setting(state, "proc_cell", "bottom_up", False)
    assert state.bdb.cell_bottom_up("proc_cell") is False


def test_read_only_session_raises_permission_error():
    state = _hier_state()
    state.is_read_only = True
    with pytest.raises(PermissionError):
        fpc.set_cell_setting(state, "proc_cell", "bottom_up", True)
    assert state.bdb.cell_bottom_up("proc_cell") is False


def test_unknown_key_raises():
    state = _hier_state()
    with pytest.raises(ValueError, match="unknown cell setting"):
        fpc.set_cell_setting(state, "proc_cell", "nope", 1)


def test_unknown_cell_propagates_bdb_error():
    state = _hier_state()
    # 0 instances → congruence trivially passes; the BDB write is the guard.
    with pytest.raises(RuntimeError, match="cell not defined"):
        fpc.set_cell_setting(state, "ghost_cell", "bottom_up", True)


# ── Shared congruence definition ─────────────────────────────────────────────

def test_gui_and_cli_share_one_congruence_definition():
    """fpc's eligibility must be the same function of all_components() the
    CLI's set_bottom_up uses (buda_session.util.bottom_up_congruence_issues),
    so the two can never diverge on what "congruent" means."""
    state = _hier_state()
    state.bdb.rotate_comp("proc_i2", 180)
    comps = state.bdb.all_components()
    issues = bottom_up_congruence_issues(comps, "proc_cell")
    assert issues, "shared helper must flag the rotated instance"
    ok, reason = fpc._bottom_up_eligible(state, "proc_cell", False, True, comps)
    assert ok is False
    assert issues[0] in reason


# ── Extensibility smoke ──────────────────────────────────────────────────────

def test_second_descriptor_roundtrips_without_dialog_changes():
    """Adding a per-cell config = appending one descriptor: it shows up in
    list_cell_settings rows and round-trips through set_cell_setting with
    its own transition gate — no dialog/command-layer edits."""
    state = _hier_state()
    stored: dict[str, float] = {}
    dummy = fpc.CellSetting(
        key="route_priority", label="Priority", kind="float", default=0.0,
        get=lambda st, c: stored.get(c, 0.0),
        set=lambda st, c, v: stored.__setitem__(c, float(v)),
        eligible=lambda st, c, old, new: (
            (float(new) >= 0, "") if float(new) >= 0
            else (False, "priority must be >= 0")),
        help="dummy second setting",
    )
    fpc.CELL_SETTINGS.append(dummy)
    try:
        rows = fpc.list_cell_settings(state)
        proc = _row(rows, "proc_cell")
        assert set(proc.settings) == {"bottom_up", "route_priority"}
        assert proc.settings["route_priority"] == (0.0, True, "")

        fpc.set_cell_setting(state, "proc_cell", "route_priority", 2.5)
        assert stored["proc_cell"] == 2.5
        with pytest.raises(ValueError, match=">= 0"):
            fpc.set_cell_setting(state, "proc_cell", "route_priority", -1)
    finally:
        fpc.CELL_SETTINGS.remove(dummy)
