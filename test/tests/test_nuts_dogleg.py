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

"""
Dogleg resolution of cyclic vertical constraints.

A genuine vertical-constraint cycle (trunk A must sit above B at one column but
below it at another) cannot be resolved by any single track ordering, so the
corner pass gives up.  NUTS resolves it by splitting one trunk on the cycle
across two tracks joined by a jog, so its pieces become independent trunks that
straddle their neighbours.  These tests check the two repros end to end: the
dogleg fires, abstract NUTS has zero overlaps, and detailed NUTS places every
bit with no bit-level short (different-net segments sharing a track over
overlapping spans).

  flow/nuts_dogleg_cycle.buda — 2-cycle (two trunks, reversed at two columns)
  flow/dogleg1.buda           — 3-cycle (x->y->z->x)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
import buda_cli  # noqa: E402  (adds build/ to sys.path and imports the buda module)

FLOW_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'flow')


def _run(flow):
    sess = buda_cli.BudaSession()
    sess.no_viz = True
    sess.do_command(f"source {os.path.abspath(os.path.join(FLOW_DIR, flow))}")
    return sess


def _detailed_shorts(sess):
    """Count different-net detailed segments sharing a layer+track over an
    overlapping (closed) span — a bit-level short."""
    ns = sess.detailed_result.net_segments
    shorts = 0
    for i in range(len(ns)):
        a = ns[i]
        for j in range(i + 1, len(ns)):
            b = ns[j]
            # Same net = same bundle AND same bit_index; distinct bits short.
            if a.layer != b.layer or (a.bundle_id == b.bundle_id and
                                      a.bit_index == b.bit_index):
                continue
            if abs(a.track_position - b.track_position) < 1e-6 and \
               a.span_lo <= b.span_hi and b.span_lo <= a.span_hi:
                shorts += 1
    return shorts


def _check_resolved(flow):
    sess = _run(flow)
    # The dogleg fired: at least one trunk's topology was split.
    assert sess.nuts_result.dogleg_topologies, \
        f"{flow}: expected a dogleg to be applied"
    # Abstract NUTS: no track overlaps left.
    assert sess.nuts_result.num_overlaps == 0, \
        f"{flow}: abstract overlaps {sess.nuts_result.num_overlaps} (expected 0)"
    # Detailed NUTS: every bit placed, and no bit-level short.
    assert sess.detailed_result.num_unplaced == 0, \
        f"{flow}: unplaced bits {sess.detailed_result.num_unplaced} (expected 0)"
    shorts = _detailed_shorts(sess)
    assert shorts == 0, f"{flow}: detailed bit-level shorts {shorts} (expected 0)"


def test_dogleg_two_cycle_resolved():
    _check_resolved("nuts_dogleg_cycle.buda")


def test_dogleg_three_cycle_resolved():
    _check_resolved("dogleg1.buda")


def _net_pull_by_seg(sess, bid):
    return {ts.seg_idx: ts.net_pull
            for ts in sess.nuts_result.segments if ts.bundle_id == bid}


# Net-pull rules for a dogleg (a split rewrites the topology, so ConnTopology
# would recompute the bundle's pulls wrongly — the pass pins them instead):
#   1. each original stub PRESERVES its pre-split pull;
#   2. both sub-trunks INHERIT the original trunk's pull;
#   3. the jog is net-zero.
def test_dogleg1_net_pull():
    # Bus z (B3) is split.  Original topology: s0 stub→bot3 (-1), s1 trunk (0),
    # s2 stub→top1 (+1).  After the split: s1 becomes the left piece, s3 the
    # right piece, s4 the jog.  The trunk had net_pull 0, so both pieces are 0.
    sess = _run("dogleg1.buda")
    assert sess.nuts_result.dogleg_topologies, "expected a dogleg"
    np = _net_pull_by_seg(sess, 3)
    assert np == {0: -1, 1: 0, 2: 1, 3: 0, 4: 0}, np


def test_dogleg2_net_pull():
    # Bus z (B3) is multicast (bot3 -> top1, top4), so its trunk has net_pull +1
    # (up).  Original topology: s0 trunk (+1), s1 stub->bot3 (0), s2 stub->top1
    # (+1, the left endpoint of the spine), s3 stub->top4 (-1, the right
    # endpoint).  After the split: s0 left piece, s4 right piece, s5 jog.  Both
    # sub-trunks inherit the trunk's +1; the three stubs preserve 0 / +1 / -1;
    # the jog is 0.  (The endpoint stubs are ±1, not ±2: a multi-fanout spine
    # contributes one unit of pull per endpoint, not one per far segment.)
    sess = _run("dogleg2.buda")
    assert sess.nuts_result.dogleg_topologies, "expected a dogleg"
    np = _net_pull_by_seg(sess, 3)
    assert np == {0: 1, 1: 0, 2: 1, 3: -1, 4: 1, 5: 0}, np


def _check_jog_window_pruned(flow):
    # The dogleg jog's slide window must not stretch beyond the outer
    # stubs/busterms of the original trunk — i.e. it stays within the span of
    # the trunk's two pieces (the bundle's H segments).
    sess = _run(flow)
    assert sess.nuts_result.dogleg_topologies, f"{flow}: expected a dogleg"
    for bid in sess.nuts_result.dogleg_topologies:
        hs = [(t.span_lo, t.span_hi) for t in sess.nuts_result.segments
              if t.bundle_id == bid and t.horiz]
        foot_lo, foot_hi = min(a for a, _ in hs), max(b for _, b in hs)
        jogs = [t for t in sess.nuts_result.segments if t.bundle_id == bid and t.is_jog]
        assert jogs, f"{flow}: no jog on B{bid}"
        for j in jogs:
            assert j.interval_lo >= foot_lo - 1e-6 and j.interval_hi <= foot_hi + 1e-6, \
                (f"{flow}: jog window [{j.interval_lo},{j.interval_hi}] exceeds trunk "
                 f"footprint [{foot_lo},{foot_hi}]")


def test_dogleg_jog_window_pruned_2cycle():
    _check_jog_window_pruned("nuts_dogleg_cycle.buda")


def test_dogleg_jog_window_pruned_multicast():
    _check_jog_window_pruned("dogleg2.buda")


def _check_subtrunks_share_slide(flow):
    # The dogleg's two sub-trunks (the H pieces) inherit the SAME slide window
    # from the original trunk, and they sit on distinct tracks (no swap into one).
    sess = _run(flow)
    for bid in sess.nuts_result.dogleg_topologies:
        pieces = [t for t in sess.nuts_result.segments if t.bundle_id == bid and t.horiz]
        assert len(pieces) == 2, f"{flow}: expected 2 sub-trunks on B{bid}, got {len(pieces)}"
        a, b = pieces
        assert abs(a.interval_lo - b.interval_lo) < 1e-6 and \
               abs(a.interval_hi - b.interval_hi) < 1e-6, \
            f"{flow}: sub-trunks have different slide windows [{a.interval_lo},{a.interval_hi}] vs [{b.interval_lo},{b.interval_hi}]"
        assert abs(a.track_position - b.track_position) > 1e-6, \
            f"{flow}: sub-trunks collapsed onto the same track {a.track_position}"


def test_dogleg_subtrunks_share_slide_2cycle():
    _check_subtrunks_share_slide("nuts_dogleg_cycle.buda")


def test_dogleg_subtrunks_share_slide_multicast():
    _check_subtrunks_share_slide("dogleg2.buda")


def test_dogleg_resolution_survives_resolve():
    # Re-running NUTS on the adopted (doglegged) bundle must stay resolved: the
    # adoption carries the mutated seg_perp / seg_net_pull / seg_slide, so the
    # split pieces are not pulled back to the original single-trunk band (which
    # would reintroduce the cycle).
    sess = _run("dogleg2.buda")
    assert sess.nuts_result.num_overlaps == 0
    n_segs = len(sess.nuts_result.segments)
    sess.do_command("run_nuts")
    assert sess.nuts_result.num_overlaps == 0, \
        f"re-solve reintroduced {sess.nuts_result.num_overlaps} overlap(s)"
    assert len(sess.nuts_result.segments) == n_segs, "dogleg lost on re-solve"
