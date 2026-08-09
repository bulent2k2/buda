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

"""The LEF/DEF entry point, end to end (flow/def/chip.buda).

Every other hier vehicle DECLARES its design in the script.  This one reads
it off disk, which is the only way to exercise the join between the phase
1-5 importers and the hierarchy pipeline — and building it is what exposed
the four defects pinned below, each of which passed every existing test
because no test read a hierarchical design from files.
"""
import contextlib
import io
import json
from pathlib import Path

import pytest

import buda_cli

_ROOT = Path(__file__).resolve().parents[2]
_DEF = _ROOT / "flow" / "def"


def _session(cmds):
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for c in cmds:
            s.do_command(c)
    return s, buf.getvalue()


_IMPORT = [
    "open_bdb :memory:",
    "set_import_scale dbu",
    f"import_lef_tech {_DEF / 'chip.lef'}",
    f"import_def_lef {_DEF / 'chip.def'} {_DEF / 'chip.lef'}",
    f"import_verilog {_DEF / 'chip.v'}",
]


# ── the merge produces a hierarchy at all three levels ─────────────────────

def test_the_def_places_and_the_netlist_gives_the_names_meaning():
    """The DEF knows WHERE and the netlist knows WHAT CONTAINS WHAT; neither
    alone is a hierarchical design."""
    s, _ = _session(_IMPORT)
    by_name = {c.name: c for c in s.bdb.all_components()}
    leaf = by_name["u_q0/bl/lo"]
    assert leaf.depth == 2 and leaf.is_leaf          # from the netlist
    assert (leaf.x1, leaf.y1) == (10000, 10000)      # from the DEF, preserved
    assert by_name[str(leaf.parent_id) if False else "u_q0/bl"].depth == 1


def test_the_netlist_does_not_redefine_what_a_placed_component_is():
    """`module LEAF (...); endmodule` is how a netlist states a hard macro's
    port directions while leaving its geometry to the LEF.  The merge used
    to read that as "defined, therefore a container", demoting every placed
    leaf: its footprint stopped acting as a low-layer keepout, and busterm
    derivation walked into a cell that has no children.

    The DEF says what a component physically IS; the netlist says where it
    sits in the tree.  Note the scope — a Verilog-ONLY import keeps the
    landed rule that a defined module is a hierarchy level even when empty
    (features/bdb_import.feature), which is why this is about PLACED rows.
    """
    s, _ = _session(_IMPORT)
    by_name = {c.name: c for c in s.bdb.all_components()}
    assert by_name["u_q0/bl/lo"].is_leaf     # placed by the DEF: physical
    assert not by_name["u_q0/bl"].is_leaf    # netlist-only: a container


def test_a_die_port_keeps_its_connectivity_through_the_merge():
    """`import_verilog` clears the pin table to rebuild it from the netlist,
    and a top-level PORT is not an instance — so the boundary components
    Phase 3d synthesized from DEF `PINS` came out with no pins at all.  The
    components looked fine; only their connectivity was gone, and the
    bundler reported 8 nets 'not placed in any bundle'.  That is exactly the
    silent open those components exist to prevent."""
    s, _ = _session(_IMPORT)
    cid = {c.name: c.id for c in s.bdb.all_components()}["PIN/din0"]
    pins = s.bdb.pins_by_comp(cid)
    assert pins, "the die port lost its net in the merge"
    assert pins[0].pin_name == "din0"


def test_a_ports_direction_is_read_from_the_environments_side():
    """A boundary component stands in for the world OUTSIDE the die, and a
    port declared INPUT is one the outside world DRIVES.  Stored unflipped,
    an input-port net had two receivers and no driver, so the bundler
    dropped it."""
    s, _ = _session(_IMPORT)
    ids = {c.name: c.id for c in s.bdb.all_components()}
    din = s.bdb.pins_by_comp(ids["PIN/din0"])[0]
    dout = s.bdb.pins_by_comp(ids["PIN/dout0"])[0]
    assert din.dir == "OUTPUT", din.dir     # declared INPUT, drives inward
    assert dout.dir == "INPUT", dout.dir    # declared OUTPUT, receives


# ── containers, and what depends on them ───────────────────────────────────

def test_containers_have_no_geometry_until_they_are_derived():
    """Neither input file has a ROW for a hierarchical instance, so nothing
    places one.  This is the state the next test is about."""
    s, _ = _session(_IMPORT)
    by_name = {c.name: c for c in s.bdb.all_components()}
    assert by_name["u_q0"].x1 < 0 and by_name["u_q0/bl"].x1 < 0


def test_without_container_bboxes_the_middle_of_the_hierarchy_is_missing():
    """The failure this guards is quiet: every command succeeds, and the
    routing interface comes out with a HOLE in it.  Derivation skips
    unplaced components, so the ports (depth 0) and the leaves (depth 2)
    get busterms and the blk level in between does not — leaving the leaf
    busterms with no parent to belong to."""
    s, _ = _session(_IMPORT + ["derive_busterms 2"])
    bts = s.bdb.all_busterms()
    assert {b.depth for b in bts} == {0, 2}, "expected a hole at depth 1"
    assert all(not b.parent_id for b in bts if b.depth == 2)

    s2, out = _session(_IMPORT + ["derive_container_bboxes margin 1500",
                                  "derive_busterms 2"])
    bts2 = s2.bdb.all_busterms()
    assert {b.depth for b in bts2} == {0, 1, 2}
    assert all(b.parent_id for b in bts2 if b.depth == 2)
    assert "placed 6 container(s)" in out, out


def test_a_container_is_the_union_of_its_children():
    s, _ = _session(_IMPORT + ["derive_container_bboxes"])
    by_name = {c.name: c for c in s.bdb.all_components()}
    blk, lo, hi = by_name["u_q0/bl"], by_name["u_q0/bl/lo"], by_name["u_q0/bl/hi"]
    assert (blk.x1, blk.y1) == (min(lo.x1, hi.x1), min(lo.y1, hi.y1))
    assert (blk.x2, blk.y2) == (max(lo.x2, hi.x2), max(lo.y2, hi.y2))
    # …and deepest-FIRST, so the quad is the union of the resolved blks,
    # not of whatever happened to be placed when it was reached.
    quad = by_name["u_q0"]
    assert (quad.x1, quad.y2) == (blk.x1, by_name["u_q0/br"].y2)


def test_a_placed_component_is_never_moved():
    """It fills gaps; it does not re-floorplan."""
    before, _ = _session(_IMPORT)
    a = {c.name: (c.x1, c.y1, c.x2, c.y2) for c in before.bdb.all_components()
         if c.x1 >= 0}
    after, _ = _session(_IMPORT + ["derive_container_bboxes margin 5000"])
    b = {c.name: (c.x1, c.y1, c.x2, c.y2) for c in after.bdb.all_components()}
    for name, box in a.items():
        assert b[name] == box, name


# ── the flow, end to end ───────────────────────────────────────────────────

@pytest.mark.mid
def test_the_whole_flow_routes_the_design_clean(tmp_path):
    """import → hier pipeline → export, on files rather than declarations.

    The endpoint is asserted because a vehicle that quietly degrades is
    worse than no vehicle: it looks like coverage."""
    out = tmp_path / "out"
    out.mkdir()
    cmds = _IMPORT + [
        "corner_margin dx 2000 dy 2000",
        "set_min_stub_length 1000",
        "derive_container_bboxes margin 1500",
        "derive_busterms 2",
        "add_blocks_from_bdb 0",
        "add_blocks_from_bdb 1 skip",
        "add_blocks_from_bdb 2 skip",
        "run_hier_bundler depth 2",
        "generate_hier_topologies",
        "set_planner_param healersAhead 1",
        "run_planner hier 5",
        "run_nuts",
        "run_detailed_nuts",
        "check_design dnuts",
        f"emit_guides {out / 'g.json'} margin 200",
    ]
    s, text = _session(cmds)

    assert "15 hbundles (D0: 9, D1: 2, D2: 4)" in text, text[-3000:]
    assert "not placed in any bundle" not in text
    assert s.nuts_result.num_overlaps == 0
    assert s.detailed_result.num_unplaced == 0
    assert "Success: no violations found" in text, text[-3000:]

    m = json.loads((out / "g.json").read_text())
    assert m["n_bundles"] == 15
    named = {n for b in m["bundles"] for n in b["nets"]}
    # The manifest's whole value is net identity: the nets it names must be
    # the design's, at every level of the hierarchy.
    assert {"din0", "dout0", "c0", "u_q0/m0", "u_q0/bl/w0"} <= named, sorted(named)


@pytest.mark.mid
def test_the_checked_in_inputs_match_their_generator():
    """The three files are committed AND generated.  If they drift, the DEF
    and the netlist stop agreeing about instance names, and that failure is
    a silently dropped connection rather than an error."""
    import subprocess
    import sys
    before = {p.name: p.read_bytes()
              for p in _DEF.glob("chip.*") if p.suffix in (".lef", ".def", ".v")}
    assert len(before) == 3, sorted(before)
    r = subprocess.run([sys.executable, str(_DEF / "gen_chip.py")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    after = {p.name: p.read_bytes()
             for p in _DEF.glob("chip.*") if p.suffix in (".lef", ".def", ".v")}
    assert after == before, sorted(k for k in before if before[k] != after[k])
