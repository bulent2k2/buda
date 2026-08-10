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

"""The LEF/DEF entry point at scale (flow/rv/soc.buda).

`test_def_hier_flow.py` covers the same path on the SMALLEST design that
exercises it.  This file covers what only appears once a quantity stops
being one: a 32-bit datapath, part-selects that are not zero-based, ports
wider than what is handed to them, leaves at four different depths — and
the die-port depth fault that a four-level design cannot show, because
there the wrong depth and the right one coincide.

Each test names the reading it rules out.  A test that passes against both
the fixed and the broken reader is not a test of the fix.
"""
import contextlib
import io
import json
from pathlib import Path

import pytest

import buda_cli

_ROOT = Path(__file__).resolve().parents[2]
_RV = _ROOT / "flow" / "rv"


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
    f"import_lef_tech {_RV / 'soc.lef'}",
    f"import_def_lef {_RV / 'soc.def'} {_RV / 'soc.lef'}",
    f"import_verilog {_RV / 'soc.v'}",
]


@pytest.fixture(scope="module")
def imported():
    s, out = _session(_IMPORT)
    return s, out, {c.name: c for c in s.bdb.all_components()}, \
        {n.id: n.name for n in s.bdb.all_nets()}


def _pins(s, comps, nets, inst, prefix):
    """{bit index -> net name} for one vector port of one instance."""
    out = {}
    for p in s.bdb.pins_by_comp(comps[inst].id):
        if p.pin_name.startswith(prefix + "[") and p.pin_name.endswith("]"):
            out[int(p.pin_name[len(prefix) + 1:-1])] = nets[p.net_id]
    return out


# ── what the netlist says, read back off the merged database ───────────────

def test_a_word_address_slice_shifts_every_bit_by_two(imported):
    """`ALN32 u_iab (.din(pc[11:2]), …)` — a word-addressed memory does not
    take the byte address's low two bits.  Port bit k is net bit k+2, which
    is the whole reason a part-select's LOW index has to be carried rather
    than assumed zero: read as `pc[9:0]` every one of these ten wires goes
    to the wrong net, and nothing downstream can tell."""
    s, _, comps, nets = imported
    din = _pins(s, comps, nets, "u_cl/u_c0/u_ifu/u_iab", "din")
    assert din == {k: f"u_cl/u_c0/u_ifu/pc[{k + 2}]" for k in range(10)}


def test_a_formal_wider_than_its_actual_stops_at_the_actual(imported):
    """…and does not invent the bits above it.

    `din` is 32 bits and `pc[11:2]` is 10, so ten bits connect and 22 stay
    unconnected.  The tempting reading — pad the formal out — would give
    `din[10]` a net named `pc[12]` that the netlist never declared, and the
    DEF's own net count would no longer match the merged database's."""
    s, _, comps, nets = imported
    din = _pins(s, comps, nets, "u_cl/u_c0/u_ifu/u_iab", "din")
    assert max(din) == 9 and len(din) == 10


def test_a_ten_bit_address_through_a_thirty_two_bit_mux(imported):
    """The same rule on both sides of a cell at once: the crossbar's mux is
    32 bits wide and every one of its ports carries a 10-bit address."""
    s, _, comps, nets = imported
    for port in ("a", "b", "y"):
        got = _pins(s, comps, nets, "u_cl/u_dxb/u_amux", port)
        assert sorted(got) == list(range(10)), (port, sorted(got))


def test_one_control_bundle_feeds_bit_and_part_selects(imported):
    """`ctrl[0]` picks register-vs-immediate, `ctrl[3:1]` is the ALU op and
    `ctrl[6:4]` the branch condition — three different slices of one 8-bit
    decoder output, which is how a decoder's output is actually used."""
    s, _, comps, nets = imported
    core = "u_cl/u_c0"
    bsel_s = [p for p in s.bdb.pins_by_comp(comps[f"{core}/u_exu/u_bsel"].id)
              if p.pin_name == "s"]
    assert [nets[p.net_id] for p in bsel_s] == [f"{core}/ctrl[0]"]

    op = _pins(s, comps, nets, f"{core}/u_exu/u_alu/u_log", "op")
    # `.op(op[1:0])` inside the ALU, itself fed `ctrl[3:1]` — a slice of a
    # slice, so LOG32's op[0] is ctrl[1].
    assert op == {0: f"{core}/ctrl[1]", 1: f"{core}/ctrl[2]"}

    bru = _pins(s, comps, nets, f"{core}/u_exu/u_bru", "op")
    assert bru == {k: f"{core}/ctrl[{k + 4}]" for k in range(3)}


def test_the_two_cores_are_wired_identically(imported):
    """The hier pipeline solves a cell once and copies it, which is only
    sound if the occurrences really are the same design.  Reading the
    merged database is the cheapest place to find out they are not."""
    s, _, comps, nets = imported

    def shape(core):
        out = {}
        for name, c in comps.items():
            if not name.startswith(core + "/"):
                continue
            rel = name[len(core) + 1:]
            out[rel] = (c.cell, c.depth, c.is_leaf, sorted(
                p.pin_name for p in s.bdb.pins_by_comp(c.id)))
        return out

    assert shape("u_cl/u_c0") == shape("u_cl/u_c1")


def test_leaves_land_at_four_different_depths(imported):
    """`flow/def` has one leaf depth; here the routing interface has to be
    derived at 0, 2, 3 and 4."""
    _s, _out, comps, _nets = imported
    depths = {c.depth for c in comps.values() if c.is_leaf and c.x1 >= 0}
    assert depths == {0, 2, 3, 4}, depths


def test_a_hard_macro_keeps_its_identity_through_the_merge(imported):
    """`RF32` and `SRAM32` are `CLASS BLOCK` in the LEF and blackbox stubs
    in the netlist.  The LEF class is what decides — interchange item 1 —
    so the instance names never enter into it."""
    _s, _out, comps, _nets = imported
    for inst, cell in (("u_cl/u_c0/u_rf", "RF32"), ("u_imem", "SRAM32"),
                       ("u_dmem", "SRAM32")):
        c = comps[inst]
        assert c.cell == cell and c.is_leaf and c.x1 >= 0, inst


def test_the_def_and_the_netlist_agree_on_every_net(imported):
    """Both files come from one description, so the DEF's declared count
    and what the reader resolves must match exactly.  The generator's
    elaborator is an independent second implementation of Verilog's three
    width rules; if it and BUDA's reader ever disagree about a part-select,
    this is where it surfaces — the reader reports `imported N of M`."""
    _s, out, _comps, _nets = imported
    assert "[DEF] nets: imported 1230 of 1230" in out, out
    assert "[DEF] components: imported 44 of 44" in out
    assert "[DEF] ports: imported 64 of 64" in out


# ── the die-port depth fault (opens_interchange item 10) ────────────────────

def test_an_endpoint_depth_is_asked_of_the_component_not_its_name():
    """`PIN/dbg[7]` has a slash in it and sits at depth 0.

    Counting separators is right for every hierarchical path and wrong for
    the boundary components `import_def_lef` synthesizes, so a bundle with
    a die port at one end got a routing frame one level too deep — a level
    holding NEITHER endpoint.  It came out of generation with zero
    candidates and, at verify time, with both busterms reported "outside
    block face", which is also what a missing block looks like.
    """
    s, _ = _session(_IMPORT)
    comps = {c.name: c for c in s.bdb.all_components()}
    ep = buda_cli.BudaSession._endpoint_depth
    assert ep("PIN/dbg[7]", 3, comps) == 0          # the case that was wrong
    assert ep("u_cl/u_c0/u_exu/u_alu/u_add", 3, comps) == 4
    assert ep("u_imem", 3, comps) == 0
    # A block with no component row is a flat-flow block; the count is all
    # there is, and it stays the fallback.
    assert ep("a/b/c", 3, comps) == 2
    assert ep("", 3, comps) == 3


# ── the flow, end to end ───────────────────────────────────────────────────

_FLOW = _IMPORT + [
    "corner_margin dx 3000 dy 3000",
    "set_min_stub_length 1000",
    "derive_container_bboxes margin 4000",
    "derive_busterms 4",
    "add_blocks_from_bdb 0",
    "add_blocks_from_bdb 1 skip",
    "add_blocks_from_bdb 2 skip",
    "add_blocks_from_bdb 3 skip",
    "add_blocks_from_bdb 4 skip",
    "run_hier_bundler depth 4",
    "generate_hier_topologies",
]


@pytest.mark.mid
def test_every_bundle_gets_a_candidate():
    """The observable half of the test above, on the real design.

    With the depth counted off the name, the 32 `boot` port bundles came out
    of generation with an EMPTY pool — 32 nets with nothing to route.  The
    end-of-flow `check_design` still says "Success: no violations found",
    because a bundle with no candidates has no segments and there is nothing
    left to find a violation in.  That is why this assertion is here and not
    folded into the endpoint test below.
    """
    s, out = _session(_FLOW)
    empty = [w.input.original_bundle.id for w in s.bundles
             if not w.input.candidates]
    assert not empty, (empty, out[-3000:])
    assert "not placed in any bundle" not in out
    assert "127 hbundles (D0: 70, D1: 9, D2: 30, D3: 14, D4: 4)" in out, out[:2000]


@pytest.mark.mid
def test_the_whole_flow_routes_the_design_clean(tmp_path):
    """import → hier pipeline → export, on 1230 nets read off disk.

    The endpoint is asserted because a vehicle that quietly degrades is
    worse than no vehicle: it looks like coverage.  What this one adds over
    `flow/def` is that the healers have to work — the first `check_design
    dnuts` here is NOT clean.
    """
    out = tmp_path / "out"
    out.mkdir()
    s, text = _session(_FLOW + [
        "set_planner_param healersAhead 1",
        "run_planner hier 5",
        "run_nuts",
        "run_detailed_nuts",
        "check_design dnuts",
        "negotiate_congestion 10",
        "ripup_reroute 20",
        "refine_selection",
        "check_design dnuts",
        f"emit_guides {out / 'g.json'} margin 300",
    ])

    assert s.nuts_result.num_overlaps == 0
    assert s.detailed_result.num_unplaced == 0

    # Both audits, in order: the first is dirty and the last is clean.  The
    # dirty one is asserted deliberately — a design this size is where the
    # measured healers have something to do, and a vehicle that arrived
    # clean at the planner would never exercise them.  If it ever starts
    # clean, this vehicle has stopped covering that half of the flow and
    # somebody should know rather than enjoy the green.
    audits = [seg.split("[runtime]")[0] for seg in text.split(
        "Verifying Detailed NUTS-level design...")[1:]]
    assert len(audits) == 2, text[-4000:]
    assert "violation(s)" in audits[0], audits[0][:1500]
    assert "Success: no violations found" in audits[1], audits[1][:1500]

    m = json.loads((out / "g.json").read_text())
    named = {n for b in m["bundles"] for n in b["nets"]}
    # Net identity is the manifest's whole value, so the names it carries
    # must be the design's — at the top, inside a core, and inside the ALU
    # four levels down.
    assert {"mdata[0]", "u_cl/ia0[0]", "u_cl/u_c0/ctrl[0]",
            "u_cl/u_c0/u_exu/u_alu/sum[0]"} <= named, sorted(named)[:40]
    # Including the die ports at both ends: `boot` comes IN from a pad and
    # `dbg` goes OUT to one.  A bundle that generated no candidate reserves
    # no corridor and simply is not in this file — the one place the
    # silently-unrouted bus of item 10 is visible in an artifact.
    assert {f"boot[{i}]" for i in range(32)} <= named
    assert {f"dbg[{i}]" for i in range(32)} <= named
    # …and the width, which is what reading a part-select is for.  The
    # instruction bus is 32 bits and the fetch address is 10, the latter
    # because `pc[11:2]` is what a word-addressed memory takes.
    for bus, w in (("u_cl/u_c0/u_exu/u_alu/sum", 32), ("u_cl/ia0", 10)):
        bits = {n for n in named if n.startswith(bus + "[")}
        assert bits == {f"{bus}[{i}]" for i in range(w)}, sorted(bits)


@pytest.mark.mid
def test_the_checked_in_inputs_match_their_generator():
    """The three files are committed AND generated.  If they drift, the DEF
    and the netlist stop agreeing about instance names, and that failure is
    a silently dropped connection rather than an error."""
    import subprocess
    import sys
    names = ("soc.lef", "soc.def", "soc.v")
    before = {n: (_RV / n).read_bytes() for n in names}
    r = subprocess.run([sys.executable, str(_RV / "gen_soc.py")],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    after = {n: (_RV / n).read_bytes() for n in names}
    assert after == before, sorted(k for k in before if before[k] != after[k])
