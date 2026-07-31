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

"""NUTS pull-target breakpoint clamp (the b44 tug-of-war fix).

net_pull is a direction; NUTS's legacy pull placement aimed at the
bus-clamped slide-window EDGE.  On a wide interior window (a connector
crossing the covered block, w=2500 on b44) that overshoots the point where
the pull's wirelength gain saturates and stretches the coupled trunk
between the connectors: b44's TRUNK_H+MST realized +1000/bit of pure
overshoot (seg1 placed at 258.5 where its gain ended at 1200; the interior
trunk spanned 2383 instead of collapsing).

derive_net_pull now records each vote's saturation coordinate and resolves
the net pull's slope-crossing breakpoint (`ConnSeg::pull_break`): a busterm
vote saturates at its face_coord, a floating-spine vote at the nearest far
segment's slide bound.  build_nuts_maps clamps the pull preference at the
breakpoint when it lies within the travel to the bound (a tightening,
never a new direction); dogleg-pinned slides and overridden pulls keep the
bound semantics.
"""
import contextlib
import io
import math

import buda
import buda_cli


def _b44_session():
    """b44 with the TRUNK_H+MST@y11915 staircase PINNED — the subject of
    these tests is that candidate's pull mechanics.  It used to sort first
    at the 3510 tie by alphabet ('+' < '@'), so the default planner pick
    was it; the structural (wl, nsegs, type) tie-break now sorts the 3-seg
    plain trunks ahead, so the tests pin it by content."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), buda.ostream_redirect():
        for c in ["source flow/tracks/tracks4top.buda",
                  "add_block blk_07 2960 9750 4660 10250",
                  "add_block blk_23 200 10830 2700 11830",
                  "add_block io_pad_tl 200 12000 1200 12800",
                  "add_bus bus_060[52] blk_23.p blk_07.p,io_pad_tl.p",
                  "run_bundler", "generate_topologies"]:
            s.do_command(c)
        idx = _mst_index(s)
        for c in [f"select_topology 1 {idx + 1}", "run_planner", "run_nuts"]:
            s.do_command(c)
    return s


def _mst_index(s):
    """0-based index of the 6-seg TRUNK_H+MST@y11915 staircase."""
    return next(i for i, c in enumerate(s.bundles[0].input.candidates)
                if c.type == "TRUNK_H+MST@y11915"
                and c.estimated_wirelength == 3510)


def _mst_cand(s):
    return s.bundles[0].input.candidates[_mst_index(s)]


def test_spine_vote_breakpoint_recorded():
    """b44 MST staircase seg1: a floating-spine vote via the y11915 trunklet whose
    far segment (the io_pad tap) has slide [200,1200] — the pull's gain
    saturates at 1200, far short of the window edge 200."""
    s = _b44_session()
    c0 = _mst_cand(s)
    assert "MST" in c0.type
    ct = buda.ConnTopology()
    ct.build(c0, s.fp)
    segs = ct.segs()
    assert segs[1].net_pull == -1
    assert segs[1].pull_break == 1200


def test_anchored_vote_flattened_by_interval_model():
    """b44 MST staircase seg3: the old model's ANCHORED busterm vote gave it
    +1 toward blk_07's face (2960) — a gain-only claim that never priced the
    trunk wire the slide stretched (the #523 defect, the '+' half of the b44
    tug).  The interval model prices both: seg3's nominal (2700) sits at the
    LOW end of its flat optimum [2700, 2960], so the pull is honestly 0, no
    breakpoint is recorded, and NUTS receives no pull_target for it."""
    s = _b44_session()
    ct = buda.ConnTopology()
    ct.build(_mst_cand(s), s.fp)
    segs = ct.segs()
    assert segs[3].net_pull == 0
    assert segs[3].pull_break == -2147483648        # INT_MIN: no saturation
    assert (segs[3].pull_lo, segs[3].pull_hi) == (2700, 2960)
    assert segs[3].pull_lo <= segs[3].perp_pos <= segs[3].pull_hi
    ts = {t.seg_idx: t for t in s.nuts_result.segments}
    assert ts[3].net_pull == 0
    assert math.isnan(ts[3].pull_target)            # no pull, no target


def test_b44_mst_realization_no_overshoot():
    """The end-to-end effect: the pinned 6-seg TRUNK_H+MST
    realizes near its envelope floor instead of +1000/bit of tug-of-war
    stretch.  seg1's pull_target is the 1200 breakpoint (was 258.5 = window
    edge), and total placed WL is within 5% of the joint minimum 3510
    (was 4510)."""
    s = _b44_session()
    ts = {t.seg_idx: t for t in s.nuts_result.segments}
    assert abs(ts[1].pull_target - 1200.0) < 1e-6
    total = sum(abs(t.span_hi - t.span_lo) for t in s.nuts_result.segments)
    assert total < 1.05 * 3510
    assert s.nuts_result.num_overlaps == 0


def test_pull_break_sentinel_without_votes():
    """A segment with no pull carries the INT_MIN sentinel (no breakpoint)."""
    s = _b44_session()
    ct = buda.ConnTopology()
    ct.build(_mst_cand(s), s.fp)
    segs = ct.segs()
    for cs in segs:
        if cs.net_pull == 0:
            assert cs.pull_break == -(2**31)
