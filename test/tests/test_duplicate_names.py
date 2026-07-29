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

"""Setup-command name uniqueness.  Several setup commands used to accept a
duplicate name SILENTLY, each corrupting state a different way:
  * `add_net`/`add_bus` — `Netlist::add_net` only appends, so a redefined name
    created a second net (double-counted bits, or two same-named bundles plus a
    clobbered endpoint map when the endpoints differ).
  * `add_block` — `Floorplan::add_block` overwrites (last-wins), silently
    moving/resizing the block or dropping one of two intended blocks.
  * `def_layer` — `LayerStack::add_layer` keeps the first for a duplicate id
    (redefinition dropped) and clobbers the name->id map for a reused name.
  * `def_track_pattern` — `define_layer` overwrites a layer's pattern.
Each is now a flow-stopping error."""
import contextlib
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
import buda_cli  # noqa: E402

_BASE = ("def_layer 4 M4 H 50", "def_layer 5 M5 V 50",
         "add_block A 0 0 100 100", "add_block B 500 0 600 100",
         "add_block C 0 500 100 600")


def _run(*cmds):
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = io.StringIO()
    code = None
    with contextlib.redirect_stdout(out):
        try:
            for c in _BASE + cmds:
                s.do_command(c)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue()


def _err(out):
    return next((l for l in out.splitlines() if l.startswith("Error:")), "")


def test_duplicate_add_net_same_endpoints_errors():
    code, out = _run("add_net foo A.p B.p", "add_net foo A.p B.p")
    assert code == 1
    assert "net 'foo' is already defined" in _err(out)


def test_duplicate_add_net_different_endpoints_errors():
    # The most dangerous case: two distinct nets sharing a name.
    code, out = _run("add_net foo A.p B.p", "add_net foo A.p C.p")
    assert code == 1
    assert "already defined" in _err(out)


def test_duplicate_add_bus_errors_and_names_bits():
    code, out = _run("add_bus bus_03[4] A.p B.p", "add_bus bus_03[4] A.p B.p")
    assert code == 1
    assert "bus_03" in _err(out) and "bus_03_0" in _err(out)


def test_bus_over_net_collision_errors():
    code, out = _run("add_net bus_03_0 A.p B.p", "add_bus bus_03[4] A.p B.p")
    assert code == 1
    assert "bus_03_0" in _err(out)


def test_net_over_bus_collision_errors():
    code, out = _run("add_bus bus_03[4] A.p B.p", "add_net bus_03_0 A.p C.p")
    assert code == 1
    assert "bus_03_0" in _err(out)


def test_distinct_names_ok():
    code, out = _run("add_net foo A.p B.p", "add_net bar A.p C.p",
                     "add_bus d[4] A.p B.p", "add_bus e[2] A.p C.p")
    assert code is None and not _err(out)


def test_piecewise_bus_ranges_ok():
    # Building one bus in disjoint index ranges is legitimate (no overlap).
    code, out = _run("add_bus b[0:1] A.p B.p", "add_bus b[2:3] A.p C.p")
    assert code is None and not _err(out)


# --- add_block / def_layer / def_track_pattern name-uniqueness ---
# Same footgun as add_net: Floorplan::add_block silently overwrites (last-wins);
# LayerStack::add_layer silently keeps the first for a dup id and clobbers the
# name->id map for a reused name; RoutingGridStack::define_layer silently
# overwrites a layer's pattern.  All are hard errors now.

def test_duplicate_add_block_errors():
    code, out = _run("add_block X 0 0 100 100", "add_block X 200 200 300 300")
    assert code == 1
    assert "block 'X' is already defined" in _err(out)


def test_duplicate_layer_id_errors():
    code, out = _run("def_layer 7 P7 H TOP 30", "def_layer 7 P7b V LOW 30")
    assert code == 1
    assert "layer id 7 is already defined" in _err(out)


def test_duplicate_layer_name_errors():
    code, out = _run("def_layer 7 P7 H TOP 30", "def_layer 8 P7 V TOP 30")
    assert code == 1
    assert "layer name 'P7' is already used" in _err(out)


def test_duplicate_track_pattern_errors():
    code, out = _run("def_track_pattern 4 0.0 SIGNAL 1 1",
                     "def_track_pattern 4 0.0 SIGNAL 2 2")
    assert code == 1
    assert "layer 4 already has a track pattern" in _err(out)


def test_distinct_layers_blocks_tracks_ok():
    code, out = _run("add_block X 0 0 100 100", "add_block Y 200 200 300 300",
                     "def_layer 7 P7 H TOP 30", "def_layer 8 P8 V TOP 30",
                     "def_track_pattern 4 0.0 SIGNAL 1 1",
                     "def_track_pattern 5 0.0 SIGNAL 2 2")
    assert code is None and not _err(out)


def test_grid_override_is_not_a_redefinition_ok():
    # add_grid_override adds a region-scoped pattern; it must NOT trip the guard.
    code, out = _run("def_track_pattern 4 0.0 SIGNAL 1 1",
                     "add_grid_override 4 0 0 50 50 0.0 SIGNAL 2 2")
    assert code is None and not _err(out)


def test_grid_override_before_global_pattern_ok():
    # add_grid_override creates the layer's map entry, so has_layer() is already
    # true before the global def.  The guard must key off the GLOBAL pattern
    # (empty until def_track_pattern), not has_layer() — an override-first
    # ordering is valid (init sets the global, keeping the earlier override).
    code, out = _run("add_grid_override 4 0 0 50 50 0.0 SIGNAL 2 2",
                     "def_track_pattern 4 0.0 SIGNAL 1 1")
    assert code is None and not _err(out)


def test_duplicate_track_pattern_still_errors_after_override():
    # A genuine duplicate global still errors even with an override present.
    code, out = _run("def_track_pattern 4 0.0 SIGNAL 1 1",
                     "add_grid_override 4 0 0 50 50 0.0 SIGNAL 2 2",
                     "def_track_pattern 4 0.0 SIGNAL 3 3")
    assert code == 1
    assert "layer 4 already has a track pattern" in _err(out)
