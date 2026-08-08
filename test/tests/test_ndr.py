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

"""NDR phase 1 (path A, slot-quantized): demand conversion, CLI
declaration/attachment, rule-class split, DNUTS placement, R3
realizability, and the R12 byte-identity guard."""
import contextlib
import io

import pytest

import buda
import buda_cli
from buda_cmds import ndr_cmds


# ── The single-sourced demand conversion (R4) ──────────────────────────────

def _spec(width=1, guard=0, mode=0, per_n=0):
    s = buda.NdrSpec()
    s.width_slots, s.guard_slots = width, guard
    s.shield_mode, s.shield_per_n = mode, per_n
    return s


def test_demand_and_layout_are_lockstep():
    # The layout is the placement-side rendering of the demand arithmetic —
    # they must agree for every shape (the R4 no-drift property).
    shapes = [
        _spec(),                            # inactive
        _spec(width=2),
        _spec(width=3, guard=2),
        _spec(guard=1),
        _spec(mode=1),                      # flank-the-bus
        _spec(width=2, guard=1, mode=1),
        _spec(width=2, mode=2),             # flank-every-bit
        _spec(width=2, guard=1, mode=3, per_n=2),
        _spec(mode=3, per_n=3),
    ]
    for s in shapes:
        for nbits in (1, 2, 3, 4, 8, 16):
            du = buda.ndr_group_demand(s, nbits)
            layout = buda.ndr_run_layout(s, nbits)
            assert len(layout) == du, (s.width_slots, s.guard_slots,
                                       s.shield_mode, s.shield_per_n, nbits)
            assert layout.count("B") == nbits


def test_group_demand_is_group_level_not_per_bit():
    # The R4 counter-example from review: a flanked 8-bit group needs TWO
    # shields; two 4-bit groups need FOUR.  Group demand is NOT additive.
    s = _spec(width=1, guard=0, mode=1)
    # active() needs a constraint; flank-only spec:
    assert s.active()
    assert buda.ndr_group_demand(s, 8) == 8 + 2
    assert 2 * buda.ndr_group_demand(s, 4) == 2 * (4 + 2)
    assert buda.ndr_group_demand(s, 8) < 2 * buda.ndr_group_demand(s, 4)


def test_demand_shapes():
    # 4 bits x 2 slots + 3 interior guard gaps (1 each) + 2 end shields.
    assert buda.ndr_group_demand(_spec(2, 1, 1), 4) == 8 + 3 + 2
    # flank-every-bit: interior gaps are shields, ends are shields.
    assert buda.ndr_group_demand(_spec(1, 0, 2), 4) == 4 + 3 + 2
    # per:2 on 4 bits: shield after bit 2 (1 interior), guards elsewhere.
    assert buda.ndr_group_demand(_spec(1, 1, 3, 2), 4) == 4 + 1 + 2 * 1 + 2
    # unshielded spacing keeps end guards (clearance to neighbors).
    assert buda.ndr_group_demand(_spec(1, 2, 0), 2) == 2 + 2 + 2 * 2
    # inactive spec is the identity.
    assert buda.ndr_group_demand(_spec(), 7) == 7


# ── CLI declaration / attachment ───────────────────────────────────────────

def _bare_session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    return s


def _run(s, cmd):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command(cmd)
    return buf.getvalue()


def test_def_ndr_declares_and_quantizes():
    s = _bare_session()
    out = _run(s, "def_ndr clk2x width x2 spacing x2 shield bus net GND")
    assert "width x2 -> 2 slot(s)/bit" in out
    assert "spacing x2 -> 1 guard slot(s)/gap" in out
    assert "shield bus (net GND)" in out


@pytest.mark.parametrize("cmd", [
    "def_ndr dup width x2",                       # (declared twice below)
    "def_ndr r width 2",                          # absolute form refused
    "def_ndr r width x0.5",                       # multiplier < 1
    "def_ndr r shield sideways",                  # unknown shield mode
    "def_ndr r shield per:0",                     # per-N needs N >= 1
    "def_ndr r bogus x2",                         # unknown token
    "def_ndr r",                                  # constrains nothing
])
def test_def_ndr_validation_is_loud(cmd):
    s = _bare_session()
    if cmd.startswith("def_ndr dup"):
        _run(s, cmd)                              # first declaration OK
    with pytest.raises(SystemExit):
        _run(s, cmd)


def test_set_ndr_unknown_rule_is_loud():
    s = _bare_session()
    with pytest.raises(SystemExit):
        _run(s, "set_ndr clk_ nosuchrule")


def test_prefix_resolution_longest_wins():
    s = _bare_session()
    _run(s, "def_ndr a width x2")
    _run(s, "def_ndr b width x3")
    _run(s, "def_ndr c width x4")
    _run(s, "set_ndr * a")
    _run(s, "set_ndr clk_ b")
    _run(s, "set_ndr clk_fast_ c")
    assert ndr_cmds.ndr_rule_for_net(s, "data_0") == "a"       # global
    assert ndr_cmds.ndr_rule_for_net(s, "clk_0") == "b"
    assert ndr_cmds.ndr_rule_for_net(s, "clk_fast_0") == "c"   # longest
    _run(s, "set_ndr clk_ off")
    assert ndr_cmds.ndr_rule_for_net(s, "clk_0") == "a"        # falls back


def test_hier_bundler_refuses_with_scopes():
    s = _bare_session()
    _run(s, "def_ndr r width x2")
    _run(s, "set_ndr clk_ r")
    with pytest.raises(SystemExit):
        _run(s, "run_hier_bundler")


# ── End-to-end flow ────────────────────────────────────────────────────────

_PATTERN = ("0 VDD 2 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 "
            "GND 2 1")

_FLOW_SETUP = [
    "add_block blkA 0 0 200 400",
    "add_block blkB 800 0 1000 400",
    "add_bus clk[4] blkA.p blkB.q",
    "add_bus data[8] blkA.d blkB.e",
    "def_layer 3 M3 H 20",
    "def_layer 4 M4 V 20",
    "def_layer 5 M5 H TOP 20",
    "def_layer 6 M6 V TOP 20",
]

_FLOW_RUN = [
    "run_bundler STRICT",
    "generate_topologies",
    "set_track_pitch 3",
    "run_planner 1",
    f"def_track_pattern 3 {_PATTERN}",
    f"def_track_pattern 4 {_PATTERN}",
    f"def_track_pattern 5 {_PATTERN}",
    f"def_track_pattern 6 {_PATTERN}",
    "run_nuts",
    "run_detailed_nuts",
]


def _flow(ndr_cmd_lines):
    s = _bare_session()
    out = []
    for c in _FLOW_SETUP + ndr_cmd_lines + _FLOW_RUN:
        out.append(_run(s, c))
    return s, "".join(out)


def test_end_to_end_shielded_double_width_bus():
    s, out = _flow(["def_ndr clk2x width x2 spacing x2 shield bus net GND",
                    "set_ndr clk_ clk2x"])
    # The mixed STRICT bundle (clk+data share endpoints) split LOUDLY.
    assert "rule-uniform part(s)" in out
    dr = s.detailed_result
    assert dr.num_unplaced == 0
    clk_bid = next(w.input.original_bundle.id for w in s.bundles
                   if w.input.ndr.active())
    rows = sorted((ns for ns in dr.net_segments if ns.bundle_id == clk_bid),
                  key=lambda n: n.track_position)
    shields = [n for n in rows if n.is_shield]
    bits    = [n for n in rows if not n.is_shield]
    # flank-the-bus: exactly 2 shields, OUTSIDE the 4 bits.
    assert len(shields) == 2 and len(bits) == 4
    assert shields[0] is rows[0] and shields[1] is rows[-1]
    assert {n.bit_index for n in shields} == {-1, -2}
    # 2-slot bits: wider than one slot (slot width 1, pitch 2 -> 3.0).
    for n in bits:
        assert n.width == pytest.approx(3.0)
    # Spacing: adjacent bits at least one empty slot apart (pitch 2 per
    # slot, 2-slot bit footprint = centres >= 6 apart with a guard).
    positions = sorted(n.track_position for n in bits)
    for a, b in zip(positions, positions[1:]):
        assert b - a >= 6.0
    # Shields carry no vias.
    assert all(v.bit_index >= 0 for v in dr.net_vias)
    # The default data bundle is untouched: 1-slot bits, no shields.
    data = [ns for ns in dr.net_segments if ns.bundle_id != clk_bid]
    assert data and all(not n.is_shield for n in data)
    assert all(n.width == pytest.approx(1.0) for n in data)


def test_planner_charges_group_demand():
    # The clk wrapper's segment charge must be the 13-slot group demand,
    # not 4 bits — visible through ndr_group_demand on the stamped spec.
    s, _ = _flow(["def_ndr clk2x width x2 spacing x2 shield bus net GND",
                  "set_ndr clk_ clk2x"])
    w = next(w for w in s.bundles if w.input.ndr.active())
    nb = len(w.input.original_bundle.get_net_names())
    assert nb == 4
    assert buda.ndr_group_demand(w.input.ndr, nb) == 13
    # Abstract NUTS reserved the demand footprint: the clk TrackSegment is
    # wider than the 8-bit default bundle's despite carrying half the bits.
    widths = {ts.bundle_id: ts.width for ts in s.nuts_result.segments}
    clk_bid = w.input.original_bundle.id
    data_bid = next(x.input.original_bundle.id for x in s.bundles
                    if not x.input.ndr.active())
    assert widths[clk_bid] > widths[data_bid]


def test_r3_realizability_refuses_unhostable_rule():
    # width x4 needs 4 physically contiguous SIGNAL slots; the pattern's
    # runs are broken by rails every 2 slots — run_detailed_nuts must
    # refuse LOUDLY (R3), not strand silently.
    narrow = ("0 VDD 2 1 _ 1 1 _ 1 1 GND 2 1")
    s = _bare_session()
    for c in _FLOW_SETUP + ["def_ndr wide width x4", "set_ndr clk_ wide",
                            "run_bundler STRICT", "generate_topologies",
                            "set_track_pitch 3", "run_planner 1",
                            f"def_track_pattern 3 {narrow}",
                            f"def_track_pattern 4 {narrow}",
                            f"def_track_pattern 5 {narrow}",
                            f"def_track_pattern 6 {narrow}",
                            "run_nuts"]:
        _run(s, c)
    with pytest.raises(SystemExit):
        _run(s, "run_detailed_nuts")


def test_r12_declared_but_unattached_rule_is_inert():
    # A declared rule with NO set_ndr scope must be byte-identical to no
    # declaration at all — attachment, not declaration, activates the path.
    base_s, _ = _flow([])
    ndr_s, _ = _flow(["def_ndr clk2x width x2 spacing x2 shield bus"])
    base = [(ns.bundle_id, ns.seg_idx, ns.bit_index, ns.track_position,
             ns.width, ns.span_lo, ns.span_hi)
            for ns in base_s.detailed_result.net_segments]
    ndr = [(ns.bundle_id, ns.seg_idx, ns.bit_index, ns.track_position,
            ns.width, ns.span_lo, ns.span_hi)
           for ns in ndr_s.detailed_result.net_segments]
    assert base == ndr
    assert not any(ns.is_shield for ns in ndr_s.detailed_result.net_segments)


def test_report_wirelength_separates_shield_metal():
    s, _ = _flow(["def_ndr clk2x width x2 spacing x2 shield bus",
                  "set_ndr clk_ clk2x"])
    out = _run(s, "report_wirelength")
    assert "NDR shield metal:" in out
    # 2 shields x 600 span.
    assert "NDR shield metal: 1200" in out


def test_check_design_clean_on_the_demo_shape():
    s, _ = _flow(["def_ndr clk2x width x2 spacing x2 shield bus",
                  "set_ndr clk_ clk2x"])
    out = _run(s, "check_design")
    assert "no violations" in out
