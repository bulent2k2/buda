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

"""Opt-in `charge_pull_target` honest-books mode (wishlist-planner "Charge
pulled segments at their predicted pull target").

The planner charges each segment's congestion demand at a chosen band
(seg_perp), but NUTS's placement preference chain lets pull/face semantics
OUTRANK the charged band for pulled segments — the books say one place, the
metal lands in another (bigHalf: 141/185 pulled segments placed >100 units
from their charged band).  With the knob on: (1) a pulled segment's charge
and scored bands anchor at its DETERMINISTIC predicted pull target (window
bound tightened by an in-travel ConnSeg::pull_break, bus-width clamped);
(2) ripup's `band_occupants` victim ranking follows the PLACED positions
(the session passes an overlay), because contention fallback can still move
metal off even an honest prediction.  Off (default) = bit-identical legacy.
"""
import io
import contextlib

import buda
import buda_cli


def _b44(knob):
    """b44 with the TRUNK_H+MST@y11915 staircase PINNED — its seg1 pull is
    the books-vs-metal subject here.  The structural (wl, nsegs, type)
    tie-break now sorts the 3-seg plain trunks ahead of it at the 3510 tie,
    so the fixture pins it by content instead of relying on default order."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = ["source flow/tracks/tracks4top.buda",
            "add_block blk_07 2960 9750 4660 10250",
            "add_block blk_23 200 10830 2700 11830",
            "add_block io_pad_tl 200 12000 1200 12800",
            "add_bus bus_060[52] blk_23.p blk_07.p,io_pad_tl.p",
            "run_bundler", "generate_topologies"]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), buda.ostream_redirect():
        for c in cmds:
            s.do_command(c)
        mst = next(i for i, c in enumerate(s.bundles[0].input.candidates)
                   if c.type == "TRUNK_H+MST@y11915"
                   and c.estimated_wirelength == 3510)
        cmds = [f"select_topology 1 {mst + 1}"]
        if knob:
            cmds.append("set_planner_param charge_pull_target 1")
        cmds += ["run_planner", "run_nuts"]
        for c in cmds:
            s.do_command(c)
    return s


def _divergences(s):
    """|placed track − charged band| per pulled segment."""
    out = {}
    w = s.bundles[0]
    for ts in s.nuts_result.segments:
        if ts.net_pull == 0 or not ts.placed:
            continue
        sp = w.plan.seg_perp[ts.seg_idx]
        if sp != -(2 ** 31):
            out[ts.seg_idx] = abs(ts.track_position - sp)
    return out


def test_off_by_default_keeps_legacy_anchor():
    """Knob off: b44 seg1's charge stays at the legacy best_band_perp choice
    (1450), far from where the pulled metal lands (1200) — the books-vs-metal
    divergence this mode exists to remove."""
    s = _b44(knob=False)
    w = s.bundles[0]
    assert w.plan.seg_perp[1] == 1450
    div = _divergences(s)
    assert div[1] > 200        # the legacy divergence is real on this fixture


def test_knob_charges_at_pull_target():
    """Knob on: b44 seg1 charges AT its breakpoint-clamped pull target (1200)
    and the pulled-segment divergence collapses (only the alignment-sibling
    residual remains — a class the static prediction cannot see)."""
    s = _b44(knob=True)
    w = s.bundles[0]
    assert w.plan.seg_perp[1] == 1200
    div = _divergences(s)
    assert div[1] < 1          # charge == metal for the breakpoint-clamped pull
    off = _divergences(_b44(knob=False))
    # 3 of 4 pulled charges become exact (<1 unit); legacy had none.  The
    # remaining outlier (seg3) is the documented alignment-sibling residual:
    # NUTS aligned it onto seg1's track, which no static prediction sees —
    # that's what the band_occupants placed overlay exists for.
    assert sum(1 for d in div.values() if d < 1) > \
           sum(1 for d in off.values() if d < 1)
    assert sum(1 for d in div.values() if d < 1) >= 3


def _occupant_session(knob):
    """The ripup band_occupants fixture (test_ripup_reroute): three buses, a
    forced (1,3) overlap on M4."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = ["def_layer 5 M5 V TOP 50", "def_layer 4 M4 H TOP 50",
            "def_track_pattern 4 0 SIGNAL 1 4", "def_track_pattern 5 0 SIGNAL 1 4",
            "add_block D1 0 1000 200 1400", "add_block D2 400 1000 600 1400",
            "add_block R1 2400 1000 2600 1400", "add_block R2 2400 1600 2600 2000",
            "add_block D3 1700 1000 1900 1400", "add_block R3 2400 400 2600 800",
            "add_bus a[8] D1.p R1.p", "add_bus b[8] D2.p R2.p",
            "add_bus c[8] D3.p R3.p",
            "add_keepout 0 1270 3000 4000 4", "add_keepout 0 660 3000 1180 4",
            "add_keepout 0 0 1650 660 4", "add_keepout 0 0 3000 600 4",
            "run_bundler", "generate_topologies",
            "select_topology 1 1", "select_topology 2 1", "select_topology 3 1"]
    if knob:
        cmds.append("set_planner_param charge_pull_target 1")
    cmds += ["run_planner", "run_nuts"]
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        for c in cmds:
            s.do_command(c)
    return s


def test_occupant_ranking_follows_placed_metal():
    """Knob on, the charge prediction diverges from contention-forced
    placements — the PLACED overlay restores the physical holders of the
    overlap's bands to the ranking (plan-based ranking sees only b1)."""
    s = _occupant_session(knob=True)
    assert [(od.bid_a, od.bid_b)
            for od in s.nuts_result.overlap_details] == [(1, 3)]
    od = s.nuts_result.overlap_details[0]
    plan_ranked = {bid for bid, _ in s.planner.band_occupants(
        s.bundles, od.layer, od.span_lo, od.span_hi,
        od.perp_lo, od.perp_hi, 3)}
    placed = [(ts.bundle_id, ts.seg_idx, int(round(ts.track_position)))
              for ts in s.nuts_result.segments if ts.placed]
    metal_ranked = {bid for bid, _ in s.planner.band_occupants(
        s.bundles, od.layer, od.span_lo, od.span_hi,
        od.perp_lo, od.perp_hi, 3, placed)}
    # The physical overlap parties must both rank under the metal overlay.
    assert {1, 3} <= metal_ranked
    # And the overlay genuinely adds holders the plan ranking missed.
    assert not ({1, 3} <= plan_ranked)


def test_global_pass_heals_under_knob():
    """End-to-end: with the knob on, ripup_reroute still reaches the clean
    endpoint on the occupant fixture (the overlay keeps victim discovery
    alive)."""
    s = _occupant_session(knob=True)
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        s.do_command("ripup_reroute")
    assert s.nuts_result.num_overlaps == 0


def _demo(level):
    """demo/comprehensive_demo.buda with the knob at the given level."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    with open("demo/comprehensive_demo.buda") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("visualize"):
                continue
            if level and line.startswith("run_planner"):
                with contextlib.redirect_stdout(io.StringIO()):
                    s.do_command(f"set_planner_param charge_pull_target {level}")
                level = 0          # inject once
            with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
                s.do_command(line)
    return s


def test_level2_junction_prediction_heals_demo_b3():
    """Level 2 (the junction prediction, follow-on (a)): comprehensive_demo's
    b3 reshuffle under level 1 lands an MST leg's junction-extended span on
    the M4 keepout (nominal 35-unit stub, stretched 200 units to its pulled
    trunk's predicted track) — 1 bit strands.  The level-2 dead-band gate
    (span_hits_dead_band over the junction-extended span) makes the crossing
    visible to STRICT, and the flow ends clean."""
    s1 = _demo(1)
    assert s1.detailed_result.num_unplaced == 1      # the b3 keepout strand
    s2 = _demo(2)
    assert s2.detailed_result.num_unplaced == 0
    assert s2.nuts_result.num_overlaps == 0


def test_param_recognized():
    s = buda_cli.BudaSession()
    s.no_viz = True
    for c in ["def_layer 4 M4 H TOP 50", "add_block A 0 0 10 10",
              "add_block B 20 0 30 10", "add_net n A.p B.p",
              "run_bundler strict", "generate_topologies", "run_planner"]:
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(c)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), buda.ostream_redirect():
        s.do_command("set_planner_param charge_pull_target 1")
    assert "unknown param" not in buf.getvalue()
