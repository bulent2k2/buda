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

"""DIVERGENT — the fan-OUT relation, mirror of CONVERGENT.

The lattice was asymmetric.  N nets arriving at one sink from N places
bundle (CONVERGENT); the same N nets leaving one driver for N places did
not, under any strategy.  That is the same physical object drawn
backwards, and it is what left a 32-bit die-port bus routing as 32
one-net bundles — `opens_interchange.md` item 11.

Correcting what that item first said: it claimed *no* current relation
covers a die-port bus.  Measured on `flow/rv`, CONVERGENT already merges
the INPUT half (32 pads → one memory is a fan-in), and only the OUTPUT
half was uncovered.  The gap was one direction, not both.
"""
import contextlib
import io

import buda_cli

_LAYERS = "def_layer 4 M4 H TOP 50\ndef_layer 5 M5 V TOP 50\n"

# Every net shares a driver and NO two share a receiver: the shape STRICT
# splits four ways and DIVERGENT keeps together.
# The driver sits in the MIDDLE with receivers on both sides, so the tree
# genuinely branches: no single trunk reaches all four, which is what makes
# the per-bit taper observable rather than vacuously true.
_FANOUT = _LAYERS + """add_block drv 400 150 500 250
add_block rcv0 0 0 100 80
add_block rcv1 0 320 100 400
add_block rcv2 800 0 900 80
add_block rcv3 800 320 900 400
add_net n0 drv.o0 rcv0.i
add_net n1 drv.o1 rcv1.i
add_net n2 drv.o2 rcv2.i
add_net n3 drv.o3 rcv3.i
"""


def _run(script):
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for line in script.strip().splitlines():
            if line.strip():
                s.do_command(line.strip())
    return s, buf.getvalue()


def _bundles(s):
    return [w.input.original_bundle for w in s.bundles]


# ── the relation itself ────────────────────────────────────────────────────

def test_strict_splits_a_fan_out_four_ways():
    """The starting point, so the next test is a comparison and not a
    claim in isolation."""
    s, _ = _run(_FANOUT + "run_bundler STRICT")
    assert len(_bundles(s)) == 4
    assert all(len(b.get_net_names()) == 1 for b in _bundles(s))


def test_divergent_keeps_a_shared_driver_together():
    s, out = _run(_FANOUT + "run_bundler DIVERGENT")
    bs = _bundles(s)
    assert len(bs) == 1, [b.reason for b in bs]
    assert sorted(bs[0].get_net_names()) == ["n0", "n1", "n2", "n3"]
    assert "DIVERGENT" in out


def test_convergent_does_not_see_a_fan_out():
    """The point of adding a relation rather than widening one: CONVERGENT
    keys on the receiver SET, which is exactly what differs here, so it
    leaves the fan-out split.  The two are mirrors, not substitutes."""
    s, _ = _run(_FANOUT + "run_bundler CONVERGENT")
    assert len(_bundles(s)) == 4


def test_divergent_leaves_a_fan_in_alone():
    """…and the mirror of that: four drivers into one sink is CONVERGENT's
    case, and DIVERGENT keys on the driver, which is what differs."""
    fanin = _LAYERS + """add_block rcv 400 0 500 400
add_block drv0 0 0 100 80
add_block drv1 0 100 100 180
add_block drv2 0 200 100 280
add_block drv3 0 300 100 380
add_net n0 drv0.o rcv.i0
add_net n1 drv1.o rcv.i1
add_net n2 drv2.o rcv.i2
add_net n3 drv3.o rcv.i3
"""
    s_div, _ = _run(fanin + "run_bundler DIVERGENT")
    s_con, _ = _run(fanin + "run_bundler CONVERGENT")
    s_str, _ = _run(fanin + "run_bundler STRICT")
    # CONVERGENT keys on the receiver INSTANCE set, and all four nets end at
    # the block `rcv` (different pins on it), so it merges them into one.
    assert len(_bundles(s_con)) == 1
    # DIVERGENT keys on the driver, which is what differs here, so it leaves
    # the fan-in exactly where STRICT does.  Mirrors, not substitutes.
    assert len(_bundles(s_div)) == 4
    assert len(_bundles(s_str)) == 4


# ── it must not change anything unless asked ───────────────────────────────

def test_combined_does_not_quietly_include_divergent():
    """COMBINED is the join of the relations safe to apply unasked.  Fan-out
    is not one — a high-fanout driver is not always a bus — and folding it
    in would silently re-bundle every existing COMBINED flow.  If someone
    ever adds it to the join, this fails and they have to mean it."""
    s, _ = _run(_FANOUT + "run_bundler COMBINED")
    assert len(_bundles(s)) == 4


def test_an_unknown_strategy_is_still_refused():
    s, out = _run(_FANOUT + "run_bundler DIVERGENTISH")
    assert "must be STRICT, CONVERGENT, DIVERGENT" in out, out


def test_set_bundling_can_hold_a_prefix_out():
    """`no_divergent` excludes exactly the fan-out relation and nothing
    else — the escape hatch for the clock net that shares a driver with a
    bus but is not part of it."""
    s, _ = _run(_FANOUT + "set_bundling n3 no_divergent\nrun_bundler DIVERGENT")
    bs = _bundles(s)
    sizes = sorted(len(b.get_net_names()) for b in bs)
    assert sizes == [1, 3], [(b.reason, b.get_net_names()) for b in bs]


# ── the routed result ──────────────────────────────────────────────────────

def test_a_fan_out_bundle_routes_and_reaches_every_receiver():
    """A bundle is only worth forming if it still lands on every block its
    nets touch.  `check_design`'s NET_DRIVER_OPEN is the check that would
    catch a tree that dropped one, so run the whole pipeline and read it."""
    s, out = _run(_FANOUT + """run_bundler DIVERGENT
generate_topologies
run_planner 5
run_nuts
check_design nuts
""")
    assert s.nuts_result.num_overlaps == 0
    assert "Success: no violations found" in out, out[-2000:]
    topo = s.bundles[0].input.candidates[
        s.bundles[0].input.topology_pinned
        if s.bundles[0].input.topology_pinned >= 0 else 0]
    blocks = set(topo.connected_block_names)
    assert {"drv", "rcv0", "rcv1", "rcv2", "rcv3"} <= blocks, sorted(blocks)


def test_each_bit_rides_only_its_own_branch():
    """The taper is what makes a fan-out bundle honest rather than a
    four-times-too-wide wire: bit k's metal must not land on the block bit
    k never reaches.  Without it the bundle would be a bus to nowhere —
    every receiver charged for every bit."""
    s, _ = _run(_FANOUT + """def_track_pattern 4 0 SIGNAL 1 1
def_track_pattern 5 0 SIGNAL 1 1
run_bundler DIVERGENT
generate_topologies
run_planner 5
run_nuts
run_detailed_nuts
""")
    assert s.detailed_result.num_unplaced == 0
    per_bit = {}
    for ns in s.detailed_result.net_segments:
        per_bit.setdefault(ns.bit_index, set()).add(ns.seg_idx)
    assert len(per_bit) == 4, per_bit
    # No bit may occupy every segment of the tree — if one did, the taper
    # is not tapering and this test is measuring nothing.
    all_segs = set().union(*per_bit.values())
    assert any(v != all_segs for v in per_bit.values()), (per_bit, all_segs)
