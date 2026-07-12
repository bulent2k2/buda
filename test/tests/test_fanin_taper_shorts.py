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

"""Fan-in per-bit taper vs the same-bundle track-sharing exemption.

The abstract NUTS occupancy predicate and DetailedNUTS's reservation both
EXEMPT same-bundle segment pairs from conflict ("per-bit they are the same
nets and may share tracks").  The per-bit taper (Topology::seg_bits, PR
#268) weakened that premise: two same-bundle fan-in segments can carry
DIFFERENT bit subsets, and track assignment maps a segment's LOCAL bit
index to its GLOBAL bit — so two segments with non-prefix-aligned
memberships (say {0,1} and {1,2}) co-located by the exemption at one track
with overlapping spans would put DIFFERENT NETS on the same wire.

Nothing structurally *enforces* that this cannot happen; today the tree
candidates' interval structure keeps differing-subset segments apart (the
pass-through trunk absorbs the same-row case, distinct Hanan bands separate
staggered stubs).  These tests pin that property on the three adversarial
geometries from the PR #268 review — each built to co-locate two driver
stubs with different bit subsets — using an independent bit-level short
predicate (same layer, same track, overlapping spans, different global
bits: the tools/show_detailed_shorts.py rule, which is also check_dnuts's
BIT_SHORT).  If a future change to the exemption, the taper, or the
alignment preference lets the corner materialize, these fail with the
offending wire pair named instead of shipping a silent short.
"""
import io
import contextlib

import buda_cli


def _route(blocks_nets):
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = ["def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
            "def_track_pattern 4 0 SIGNAL 1 1",
            "def_track_pattern 5 0 SIGNAL 1 1",
            *blocks_nets,
            "run_bundler CONVERGENT", "generate_topologies",
            "run_planner", "run_nuts", "run_detailed_nuts"]
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    return s


def _cross_net_same_track_overlaps(s):
    """Bit-wire pairs of one bundle that share a physical track with
    overlapping spans while carrying DIFFERENT global bits (= different
    nets) — the show_detailed_shorts / BIT_SHORT predicate, evaluated
    independently of the checker so the test cannot inherit a checker
    regression."""
    ns = s.detailed_result.net_segments
    shorts = []
    for i in range(len(ns)):
        for j in range(i + 1, len(ns)):
            a, b = ns[i], ns[j]
            if a.layer != b.layer or a.bit_index == b.bit_index:
                continue
            if abs(a.track_position - b.track_position) > 1e-9:
                continue
            if min(a.span_hi, b.span_hi) - max(a.span_lo, b.span_lo) > 1e-9:
                shorts.append(((a.seg_idx, a.bit_index),
                               (b.seg_idx, b.bit_index),
                               a.layer, a.track_position))
    return shorts


def _assert_clean_fanin(s, blocks):
    """The bundle really is a tapered fan-in (the scenario under test),
    everything places, and no cross-net pair shares a track."""
    w = s.bundles[0]
    topo = w.input.candidates[w.plan.selected_topology_index]
    assert set(topo.connected_block_names) == set(blocks)
    assert s.detailed_result.num_unplaced == 0
    shorts = _cross_net_same_track_overlaps(s)
    assert not shorts, f"cross-net same-track overlap(s): {shorts}"


def test_same_row_drivers_no_cross_net_short():
    """Two drivers on the SAME row: their stubs would share one Hanan band
    end to end.  Today the generator's pass-through trunk absorbs this
    (one full-width segment through both blocks) — either way, no two
    different bits may share a track."""
    s = _route(["add_block ma 0 0 100 80", "add_block mb 200 0 300 80",
                "add_block sink 800 0 950 200",
                "add_net w0 ma.tx0 sink.r0", "add_net w1 ma.tx1 sink.r1",
                "add_net w2 mb.tx sink.r2"])
    _assert_clean_fanin(s, ("ma", "mb", "sink"))


def test_overlapping_row_ranges_no_cross_net_short():
    """Staggered drivers with OVERLAPPING row ranges: both stubs' rows fall
    in one shared Hanan band, so the same-bundle exemption would allow
    co-locating them on one track with overlapping x-spans — bits {0,1}
    (ma) against bit {2} (mb)."""
    s = _route(["add_block ma 0 0 100 80", "add_block mb 250 20 350 100",
                "add_block sink 800 300 950 500",
                "add_net w0 ma.tx0 sink.r0", "add_net w1 ma.tx1 sink.r1",
                "add_net w2 mb.tx sink.r2"])
    _assert_clean_fanin(s, ("ma", "mb", "sink"))


def test_same_side_column_drivers_no_cross_net_short():
    """Two drivers stacked in one column on the SAME side of the trunk:
    the align-sibling preference (same-bundle stubs off one perpendicular
    connector prefer one track) is the mechanism most likely to co-locate
    differing-subset stubs."""
    s = _route(["add_block ma 0 0 100 80", "add_block mb 0 120 100 200",
                "add_block sink 700 60 850 140",
                "add_net w0 ma.tx0 sink.r0", "add_net w1 ma.tx1 sink.r1",
                "add_net w2 mb.tx sink.r2"])
    _assert_clean_fanin(s, ("ma", "mb", "sink"))
