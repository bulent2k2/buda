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

"""`auto_floorplan` — a first hierarchical floorplan from a netlist and a
PDK (docs/AUTO_FLOORPLAN.md).  The vehicle is the TPU's own Verilog
(`flow/tpu/tpu.v`, no DEF): a netlist whose every container is a template
instantiated eight times, which is what the command must keep congruent."""
import io
import contextlib
from pathlib import Path

import pytest

import buda
import buda_cli
from comp_placement import is_placed

_ROOT = Path(__file__).resolve().parents[2]
_STACK = _ROOT / "flow" / "mockpdk" / "stack.buda"
_PDK = _ROOT / "flow" / "mockpdk" / "mock.pdk"
_TPU_V = _ROOT / "flow" / "tpu" / "tpu.v"


def _session(*setup):
    s = buda_cli.BudaSession()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        s.run_command(f"source {_STACK}")
        s.run_command("open_bdb :memory:")
        for line in setup:
            s.run_command(line)
    return s, out.getvalue()


def _run(s, line):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        s.run_command(line)
    return out.getvalue()


@pytest.fixture(scope="module")
def tpu():
    s, _ = _session(f"import_verilog {_TPU_V}")
    out = _run(s, f"auto_floorplan pdk {_PDK} wl 0.25")
    return s, out


def test_every_component_is_placed_and_the_die_set(tpu):
    s, out = tpu
    comps = s.bdb.all_components()
    assert comps and all(is_placed(c) for c in comps)
    assert s.bdb.die_w() > 0 and s.bdb.die_h() > 0
    for c in comps:
        assert 0 <= c.x1 < c.x2 <= s.bdb.die_w() + 1e-9, c.name
        assert 0 <= c.y1 < c.y2 <= s.bdb.die_h() + 1e-9, c.name
    assert "placement audit: clean" in out
    # a ports-only module is a block, as the DEF+Verilog merge concludes
    assert all(c.is_leaf for c in comps if c.cell == "pe_cell")
    assert not any(c.is_leaf for c in comps if c.cell == "row_cell")


def test_every_instance_of_a_cell_is_congruent(tpu):
    """A cell is placed ONCE and stamped at every occurrence: the child
    offsets inside every `row_cell` instance are identical, which is what
    the bottom-up family (solve once, copy) needs."""
    s, _ = tpu
    comps = s.bdb.all_components()
    by_id = {c.id: c for c in comps}
    shapes = {}
    for c in comps:
        if c.parent_id == -1:
            continue
        p = by_id[c.parent_id]
        key = (p.cell, c.name.rsplit("/", 1)[-1])
        off = (round(c.x1 - p.x1, 6), round(c.y1 - p.y1, 6),
               round(c.x2 - c.x1, 6), round(c.y2 - c.y1, 6))
        shapes.setdefault(key, set()).add(off)
    assert all(len(v) == 1 for v in shapes.values()), \
        {k: v for k, v in shapes.items() if len(v) > 1}
    # and the template rows say the same
    edges = s.bdb.cell_child_edges()
    assert ("row_cell", "pe_cell") in edges
    sizes = {r.name: (r.width, r.height) for r in s.bdb.all_cells()}
    assert sizes["pe_cell"][0] > 0 and sizes["row_cell"][0] > sizes["pe_cell"][0]


def test_the_face_rule_sizes_the_pe_and_the_row(tpu):
    """A PE's faces carry psum+weight one way (32 bits -> 152 units at
    the stack's 4-unit bit pitch, plus pad) and the activation the other;
    a row's face carries every column's psum+weight (256 bits), so the
    row is a LINE wider than 1048 and not a padded square."""
    s, _ = tpu
    rows = s._autofp_last["cells"]
    pe = rows["pe_cell"]
    assert pe["how"] == "face" and pe["w"] >= 152 and pe["h"] >= 56
    row = rows["row_cell"]
    assert row["w"] >= 1048 and row["h"] < row["w"] / 4
    assert row["util"] > 0.6


def test_a_rerun_is_byte_identical(tpu):
    s, _ = tpu
    before = [(c.name, c.x1, c.y1, c.x2, c.y2) for c in s.bdb.all_components()]
    _run(s, f"auto_floorplan pdk {_PDK} wl 0.25")
    after = [(c.name, c.x1, c.y1, c.x2, c.y2) for c in s.bdb.all_components()]
    assert before == after


def test_the_flow_routes_on_the_first_floorplan(tpu):
    """What the floorplan is FOR: the hier flow runs on it end to end and
    its audits judge it.  The first floorplan of the TPU is dirty at the
    first audit and one healer round takes most of it out (measured —
    recorded in docs/AUTO_FLOORPLAN.md, where the hand-drawn tpu.tcl is
    clean); this pins that the flow runs and that the healers find room."""
    s, _ = tpu
    for line in ("derive_busterms 2", "add_blocks_from_bdb 0",
                 "add_blocks_from_bdb 1 skip", "run_hier_bundler depth 2",
                 "generate_hier_topologies", "set_planner_param healersAhead 1",
                 "run_planner hier 5", "run_nuts", "run_detailed_nuts",
                 "check_design dnuts"):
        _run(s, line)
    first = s.detailed_result.num_unplaced
    assert len(s.bundles) == 152
    _run(s, "negotiate_congestion 10")
    _run(s, "ripup_reroute 20")
    _run(s, "check_design dnuts")
    assert s.detailed_result.num_unplaced < first / 4


def test_without_a_pdk_the_face_rule_alone_sizes_every_leaf():
    s, _ = _session(f"import_verilog {_TPU_V}")
    out = _run(s, "auto_floorplan")
    assert "PDK none (face rule only)" in out
    rows = s._autofp_last["cells"]
    assert all(r["how"] == "face" for r in rows.values() if r["kind"] == "leaf")


def test_options_are_validated_loudly():
    s, _ = _session(f"import_verilog {_TPU_V}")
    with pytest.raises(SystemExit):        # the registry's unknown-option guard
        _run(s, "auto_floorplan pdq x")
    assert "not found" in _run(s, "auto_floorplan pdk /nonexistent.pdk")
    assert "place must be one of" in _run(s, "auto_floorplan place random")
    assert "snap wants" in _run(s, "auto_floorplan snap 18")
    assert "wants <cell>=<value>" in _run(s, "auto_floorplan grow pe_cell")


def test_a_cell_that_is_not_a_template_is_refused():
    s, _ = _session()
    for line in ("set_die 1000 1000", "add_cell leaf 50 50", "add_cell box 200 200",
                 "add_inst_to_cell box a leaf 10 10",
                 "add_inst b0 box - 0 0",
                 "add_inst b1 box - 300 0",
                 "add_comp b1/extra leaf b1 310 10 360 60"):
        _run(s, line)
    out = _run(s, "auto_floorplan")
    assert "is not a template" in out


def test_grow_and_snap_reach_the_result(tpu):
    s, _ = _session(f"import_verilog {_TPU_V}")
    _run(s, f"auto_floorplan pdk {_PDK} wl 0.25 grow pe_cell=1.5 snap 18 32")
    rows = s._autofp_last["cells"]
    base = tpu[0]._autofp_last["cells"]["pe_cell"]
    assert rows["pe_cell"]["w"] >= 1.5 * base["w"] - 18
    for c in s.bdb.all_components():
        assert c.x1 % 18 == 0 and c.y1 % 32 == 0, c.name
