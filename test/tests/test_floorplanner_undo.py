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

"""Floorplanner undo restores the BDB cell dims, not just the engine (T2-05).

`sync_cell_to_instances` writes a resized shared cell's dims to the BDB `cell`
row IMMEDIATELY (intentional — a non-synthesized cell name would otherwise
never be updated, guarded by
test_floorplanner_commands.test_resize_shared_cell_preserves_sibling_positions).
An undo restores only the engine, so the undo snapshot/restore must also
capture+revert the cell dims or the BDB cell row would stay diverged from the
reverted engine until the next write_bdb.
"""

import os
import sys

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("MPLBACKEND", "Agg")


def _cell_dims(state, cell):
    return {c.name: (c.width, c.height) for c in state.bdb.all_cells()}[cell]


def _comp_dims(state, name):
    for c in state.bdb.all_components():
        if c.name == name:
            return (c.x2 - c.x1, c.y2 - c.y1)
    raise KeyError(name)


def test_undo_reverts_shared_cell_dims(tmp_path):
    # bdb_floorplanner guards its tkinter import, so its pure-logic undo
    # methods import (and run) headless.
    from tools import bdb_floorplanner as F
    from tools import floorplanner_commands as fpc

    state = fpc.create_bdb(str(tmp_path / "u.bdb"), 2000, 1000, grid=10)
    fpc.add_block(state, "rack_a", 0, 0, 300, 200)
    fpc.add_block(state, "rack_b", 400, 0, 300, 200)
    fpc.add_block(state, "rack_a/slot", 10, 10, 100, 80)
    fpc.add_block(state, "rack_b/slot", 10, 10, 100, 80)
    fpc.write_bdb(state)

    cell = fpc.get_block_cell(state, "rack_a/slot")
    before = _cell_dims(state, cell)
    assert before == (100.0, 80.0)

    class _Fake:            # _snapshot / _restore_silent only touch self.state
        pass
    gui = _Fake()
    gui.state = state

    snap = F.BdbFloorplanner._snapshot(gui)      # pre-edit snapshot

    # A shared-cell resize writes the new dims to the BDB cell row immediately
    # (and resizes every sibling instance in the engine).
    b = state.block("rack_a/slot")
    fpc.sync_cell_to_instances(state, "rack_a/slot",
                               b.x1, b.y1, b.x1 + 120, b.y1 + 90)
    assert _cell_dims(state, cell) == (120.0, 90.0), "immediate write lost"

    # Undo: restores the engine AND (audit T2-05) the BDB cell row.
    F.BdbFloorplanner._restore_silent(gui, snap)
    after = _cell_dims(state, cell)
    assert after == before == (100.0, 80.0), \
        "undo left the BDB cell row diverged from the reverted engine"

    # The reverted engine sizes agree with the reverted cell row.
    for inst in ("rack_a/slot", "rack_b/slot"):
        blk = state.block(inst)
        assert (blk.x2 - blk.x1, blk.y2 - blk.y1) == (100.0, 80.0), inst


def test_undo_after_write_keeps_bdb_components_consistent(tmp_path):
    """resize -> Write -> undo must not leave the BDB partially mutated.

    `sync_cell_to_instances` persists the resized cell row immediately but
    defers the component bboxes to `write_bdb`.  If the user Writes after the
    resize, the BDB holds BOTH the resized cell row and the resized component
    rows.  A subsequent undo that reverted only the cell row (and the in-memory
    engine) would leave the file with the cell row at the OLD dims and the
    component rows still at the resized dims — a partial mutation.  Undo must
    revert the persisted component rows together with the cell row.
    """
    from tools import bdb_floorplanner as F
    from tools import floorplanner_commands as fpc

    state = fpc.create_bdb(str(tmp_path / "u.bdb"), 2000, 1000, grid=10)
    fpc.add_block(state, "rack_a", 0, 0, 300, 200)
    fpc.add_block(state, "rack_b", 400, 0, 300, 200)
    fpc.add_block(state, "rack_a/slot", 10, 10, 100, 80)
    fpc.add_block(state, "rack_b/slot", 10, 10, 100, 80)
    fpc.write_bdb(state)                       # component rows persisted @ 100x80

    class _Fake:
        pass
    gui = _Fake()
    gui.state = state

    snap = F.BdbFloorplanner._snapshot(gui)     # pre-edit snapshot (100x80)

    # Resize the shared cell, then WRITE — so the resized component bboxes are
    # persisted to the BDB (the intervening Write is the crux of the finding).
    b = state.block("rack_a/slot")
    fpc.sync_cell_to_instances(state, "rack_a/slot",
                               b.x1, b.y1, b.x1 + 120, b.y1 + 90)
    fpc.write_bdb(state)
    for inst in ("rack_a/slot", "rack_b/slot"):
        assert _comp_dims(state, inst) == (120.0, 90.0), inst

    # Undo: the persisted component rows revert together with the cell row.
    F.BdbFloorplanner._restore_silent(gui, snap)

    cell = fpc.get_block_cell(state, "rack_a/slot")
    assert _cell_dims(state, cell) == (100.0, 80.0), "cell row not reverted"
    for inst in ("rack_a/slot", "rack_b/slot"):
        assert _comp_dims(state, inst) == (100.0, 80.0), \
            f"BDB component row {inst} left diverged from the reverted cell row"
