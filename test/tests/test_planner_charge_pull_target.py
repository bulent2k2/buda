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
bound tightened by an in-travel ConnSeg::pull_break, bus-width clamped) — but
OCCUPANCY-AWARE: the anchor is used only when that band is overflow-free, else
the charge falls to the occupancy-aware best_band_perp, mirroring NUTS's
preferred_fit (target the pull, spread to the nearest free track).  Charging
the raw anchor unconditionally over-concentrated every pulled segment on its
window bound and booked phantom demand there, steering topology SELECTION to
longer detours on an UNcongested design (big2 est-WL +8%, all still 0/0); the
occupancy-aware anchor recovers that (+8% -> +1.6%) and, by keeping the charge
honest, heals bigHalf's level-2 opens and the comprehensive_demo b3 strand at
level 1.  (2) ripup's `band_occupants` victim ranking follows the PLACED
positions (the session passes an overlay), the general-case guard for
contention-fallback divergence.  Off (default) = bit-identical legacy.
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
    # Under the anchor-interval pull model (issue #523) seg1's optimum is the
    # FLAT interval [200, 1200]: every coordinate in it is
    # wirelength-identical, and NUTS may park the bus anywhere inside it for
    # packing (the placed 1141.5 = alignment/packing inside the flat span).
    # The charge stays at the breakpoint end (1200), so "charge == metal" now
    # means: same flat optimum, divergence bounded well below the 100-unit
    # books-vs-metal threshold — not the exact-coordinate identity the point
    # model gave (div < 1).
    ts1 = next(t for t in s.nuts_result.segments if t.seg_idx == 1)
    ct = buda.ConnTopology()
    ct.build(w.input.candidates[w.plan.selected_topology_index], s.fp)
    cs1 = ct.segs()[1]
    assert cs1.pull_lo <= ts1.track_position <= cs1.pull_hi  # metal in the flat optimum
    assert div[1] < 100        # bounded by the flat span, below the report threshold
    off = _divergences(_b44(knob=False))
    # Under the interval model 3 segments stay pulled (1, 4, 5 — the old
    # seg3 vote was the dissolved gain-only artifact).  Knob on: 2 of 3
    # charges are exact (<1 unit) and ALL sit below the 100-unit
    # books-vs-metal threshold (seg1's 58.5 is packing inside its flat
    # optimum, asserted above).  Knob off: every pulled charge diverges
    # >100 (308.5 / 191.5 / 441.5) — the phantom-reservation class the
    # knob exists to remove.
    assert sum(1 for d in div.values() if d < 1) > \
           sum(1 for d in off.values() if d < 1)
    assert sum(1 for d in div.values() if d < 1) >= 2
    assert all(d < 100 for d in div.values())
    assert all(d > 100 for d in off.values())


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
    """Knob on, the PLACED overlay ranks the physical holders of the overlap's
    bands.  Since the occupancy-aware pull anchor (this arc's charge fix) books
    a pulled segment at its anchor only when that band is free — else at the
    occupancy-aware best_band_perp, exactly where NUTS spreads it — the level-1
    charge on THIS small fixture is already honest enough that the plan-based
    ranking catches both overlap parties too.  The placed overlay remains the
    general-case guard for CONTENTION-FALLBACK divergence (a BEST_EFFORT commit
    whose charge is stale vs the moved metal — the big2-b61 class the corpus
    exercises); here it must at minimum preserve the physical parties."""
    s = _occupant_session(knob=True)
    assert [(od.bid_a, od.bid_b)
            for od in s.nuts_result.overlap_details] == [(1, 3)]
    od = s.nuts_result.overlap_details[0]
    placed = [(ts.bundle_id, ts.seg_idx, int(round(ts.track_position)))
              for ts in s.nuts_result.segments if ts.placed]
    metal_ranked = {bid for bid, _ in s.planner.band_occupants(
        s.bundles, od.layer, od.span_lo, od.span_hi,
        od.perp_lo, od.perp_hi, 3, placed)}
    # The physical overlap parties must both rank under the metal overlay.
    assert {1, 3} <= metal_ranked


def test_global_pass_heals_under_knob():
    """End-to-end: with the knob on, ripup_reroute still reaches the clean
    endpoint on the occupant fixture (the overlay keeps victim discovery
    alive)."""
    s = _occupant_session(knob=True)
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        s.do_command("ripup_reroute")
    assert s.nuts_result.num_overlaps == 0


def _demo(level):
    """flow/comprehensive_regression.buda with the knob at the given level.

    A FROZEN snapshot of demo/comprehensive_demo.buda: the b3 keepout-strand
    repro depends on bundle id 3 being the b3 net with a strand-forming MST leg,
    so it reads the frozen fixture (not the live demo, which is free to be
    tweaked for demonstrations and would otherwise dislodge bundle 3).

    Bundle 3 is pinned BY CONTENT to the strand-forming `TRUNK_V+MST@x285`
    candidate (what the planner auto-selects from today's default pool), so the
    repro also survives candidate-pool growth under a different generation
    default (e.g. the `hanan_loci` flip): it is a property of that candidate's
    MST-leg geometry, not of its pool index.  Under today's default the pin
    matches the planner's own choice."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    pinned = False
    with open("flow/comprehensive_regression.buda") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("visualize"):
                continue
            if not pinned and line.startswith("run_planner"):
                pinned = True      # inject once, before the first planner run
                w = next(b for b in s.bundles
                         if b.input.original_bundle.id == 3)
                idx = next(i for i, c in enumerate(w.input.candidates)
                           if c.type == "TRUNK_V+MST@x285") + 1
                with contextlib.redirect_stdout(io.StringIO()):
                    s.do_command(f"select_topology 3 {idx}")
                    if level:
                        s.do_command(
                            f"set_planner_param charge_pull_target {level}")
            with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
                s.do_command(line)
    return s


def test_level2_junction_prediction_heals_demo_b3():
    """comprehensive_demo's b3 keepout strand — historically a level-1 reshuffle
    landed an MST leg's junction-extended span on the M4 keepout (nominal
    35-unit stub, stretched 200 units to its pulled trunk's predicted track,
    1 bit stranded), and only level 2's dead-band gate (span_hits_dead_band)
    healed it.  The occupancy-aware pull anchor (this arc's charge fix) removes
    the over-concentrated charge that drove that reshuffle, so **level 1 now
    heals b3 directly** — the trunk no longer books its whole demand at the
    window bound, so the junction stretch that crossed the keepout does not
    happen.  Level 2's dead-band gate remains as defense-in-depth for the
    junction-stretch class on other geometry.  Both levels end clean."""
    s1 = _demo(1)
    assert s1.detailed_result.num_unplaced == 0      # L1 now heals the strand
    assert s1.nuts_result.num_overlaps == 0
    s2 = _demo(2)
    assert s2.detailed_result.num_unplaced == 0
    assert s2.nuts_result.num_overlaps == 0


def test_anchor_feasibility_consistent_under_zero_kcong():
    """The occupancy-aware anchor's feasibility test uses score_segment (RAW
    overflow), the same measure the STRICT ladder rejects on — NOT the
    kCong-scaled cong_cost_segment, which reads 0 on a genuinely overflowing
    anchor when kCong is small/zero and would charge there, then let STRICT
    reject the layer without trying the free band best_band_perp finds
    (Codex #364).  Smoke guard: the knob-on occupant fixture plans+places
    consistently with kCong 0 (all segments placed, same overlap count as the
    default-kCong knob-on run) — the kCong=0 path is not a special case."""
    base = _occupant_session(knob=True)
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
            "select_topology 1 1", "select_topology 2 1", "select_topology 3 1",
            "set_planner_param charge_pull_target 1", "set_planner_param kCong 0",
            "run_planner", "run_nuts"]
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        for c in cmds:
            s.do_command(c)
    placed = sum(1 for t in s.nuts_result.segments if t.placed)
    assert placed == len(s.nuts_result.segments)          # nothing stranded
    assert s.nuts_result.num_overlaps == base.nuts_result.num_overlaps


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
