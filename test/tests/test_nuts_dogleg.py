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

# The dogleg-forming topology each flow's `select_topologies` pin targets, BY
# CONTENT.  The checked-in flows pin bundle 3 by 1-based index into the
# WL-sorted candidate pool; a generation-default change (e.g. the `hanan_loci`
# flip) grows the pool and renumbers those indices, silently landing the pin on
# a non-dogleg candidate.  These tests are about the dogleg MECHANISM, not the
# index, so `_run` re-pins by type when the flow's index pin missed and re-runs
# the pinned pipeline.  No-op under today's default (the flow's pin already
# selects this candidate), so default-off behavior is unchanged.
_DOGLEG_PIN = {
    "dogleg2.buda": (3, "TRUNK_H@y225"),
    "dogleg2-aligned-dogleg-stub.buda": (3, "TRUNK_H@y225"),
}


def _cand_index(sess, bid, type_str):
    """1-based index of the candidate with the given type in bundle bid's pool."""
    w = next(b for b in sess.bundles if b.input.original_bundle.id == bid)
    return next(i for i, c in enumerate(w.input.candidates)
                if c.type == type_str) + 1


def _repin_by_type(sess, bid, type_str):
    """Re-pin bundle bid to the candidate of the given type and re-run the
    pinned planner->NUTS->DetailedNUTS pipeline — only if the current selection
    is a different candidate (content-based rescue of a stale index pin)."""
    w = next(b for b in sess.bundles if b.input.original_bundle.id == bid)
    if w.input.candidates[w.plan.selected_topology_index].type == type_str:
        return
    sess.do_command(f"select_topologies {bid} {_cand_index(sess, bid, type_str)}")
    sess.do_command("run_planner 1")
    sess.do_command("run_nuts")
    sess.do_command("run_detailed_nuts")


def _run(flow):
    sess = buda_cli.BudaSession()
    sess.no_viz = True
    sess.do_command(f"source {os.path.abspath(os.path.join(FLOW_DIR, flow))}")
    if flow in _DOGLEG_PIN:
        _repin_by_type(sess, *_DOGLEG_PIN[flow])
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


def test_dogleg_aligned_stub_no_overstretch():
    # Repro of the "original stub on the jog column" case: top1/top4 are placed
    # symmetric about bot3, so the multicast driver stub (bot3) sits exactly on
    # the jog column and becomes an interior tap on the split trunk.  The lower
    # sub-trunk must meet the jog where it actually slid to, not stay stretched
    # to the pre-slide split column — span_adjustments must CONTRACT a piece's
    # inner end to the jog (an interior tap at that end must not block it).
    sess = _run("dogleg2-aligned-dogleg-stub.buda")
    assert sess.nuts_result.dogleg_topologies, "expected a dogleg"
    assert sess.nuts_result.num_overlaps == 0, \
        f"abstract overlaps {sess.nuts_result.num_overlaps} (expected 0)"
    assert sess.detailed_result.num_unplaced == 0
    assert _detailed_shorts(sess) == 0
    for bid in sess.nuts_result.dogleg_topologies:
        jogs = [t for t in sess.nuts_result.segments if t.bundle_id == bid and t.is_jog]
        pieces = [t for t in sess.nuts_result.segments if t.bundle_id == bid and t.horiz]
        assert len(jogs) == 1 and len(pieces) == 2
        jx = jogs[0].track_position
        a, b = pieces
        # The two sub-trunks meet AT the jog column; they must not overlap in the
        # routing direction beyond it (an overstretched piece straddles the jog
        # and overlaps its sibling's span — wasted, mis-placed abstract footprint).
        overlap = min(a.span_hi, b.span_hi) - max(a.span_lo, b.span_lo)
        assert overlap <= 1e-6, \
            f"sub-trunks overlap in span by {overlap} (overstretch past the jog)"
        # Each piece terminates exactly at the jog column on its inner end.
        for p in (a, b):
            assert abs(p.span_lo - jx) < 1e-6 or abs(p.span_hi - jx) < 1e-6, \
                f"piece span [{p.span_lo},{p.span_hi}] does not meet the jog at {jx}"


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
    # On a re-solve the seed's no-swap ordering is gone, so the sibling-alignment
    # pass must not align the two sub-trunks (both follow the jog) onto one track —
    # that would collapse the split.  They must stay on distinct tracks.
    for bid in sess.nuts_result.dogleg_topologies:
        pieces = [t for t in sess.nuts_result.segments if t.bundle_id == bid and t.horiz]
        assert len(pieces) == 2 and abs(pieces[0].track_position - pieces[1].track_position) > 1e-6, \
            f"B{bid}: sub-trunks collapsed onto one track on re-solve"


def test_dogleg_state_cleared_on_regenerate():
    # Regenerating a bundle's candidate list after a dogleg was adopted replaces
    # the appended split candidate, so its slot index and the bundle's selection
    # (which pointed at that appended split) no longer refer to anything.  Without
    # a reset, the dangling selected_topology_index dereferences past the fresh,
    # shorter candidate list and optimize_topologies crashes (ValueError: vector).
    sess = _run("dogleg2.buda")
    assert sess.nuts_result.dogleg_topologies and sess._dogleg_slot, \
        "expected an adopted dogleg with slot bookkeeping"
    sess.do_command("generate_topologies")
    # Bookkeeping forgotten and every regenerated bundle reset to 'not planned'.
    assert sess._dogleg_slot == {} and sess._dogleg_originals == {}, \
        "dogleg bookkeeping survived candidate regeneration"
    for w in sess.bundles:
        sel = w.plan.selected_topology_index
        assert sel < len(w.input.candidates), \
            f"B{w.input.original_bundle.id}: stale selection {sel} >= {len(w.input.candidates)}"
    # The full re-plan + re-solve sequence that used to crash now completes and
    # re-detects the cycle from scratch.
    sess.do_command("select_topologies 1,2 4")
    # Bundle 3 -> TRUNK_H@y225 (the dogleg-forming topology), pinned by CONTENT
    # so the test survives candidate-pool growth (index renumbering) under a
    # different generation default.
    sess.do_command(f"select_topologies 3 {_cand_index(sess, 3, 'TRUNK_H@y225')}")
    sess.do_command("run_planner 1")
    sess.do_command("run_nuts")
    assert sess.nuts_result.num_overlaps == 0, \
        f"re-solve after regenerate left {sess.nuts_result.num_overlaps} overlap(s)"
    assert sess.nuts_result.dogleg_topologies, "dogleg not re-detected after regenerate"


def test_dogleg_reset_on_replan():
    # Re-running the planner discards any adopted dogleg: the bundle's pre-split
    # topology is restored and the pinned per-segment overrides are dropped, so
    # the next NUTS re-detects cycles from scratch (neighbors may have moved).
    # The split must not silently outlive the planner that produced its inputs.
    sess = _run("dogleg2.buda")
    w = [b for b in sess.bundles if b.input.original_bundle.id == 3][0]
    # After the initial dogleg the split topology (more segments than the pre-split
    # original) is selected with pinned pulls.
    split_segs = len(w.input.candidates[w.plan.selected_topology_index].segments)
    assert w.plan.seg_net_pull, "expected pinned pulls after the dogleg"
    assert len(w.plan.seg_net_pull) == split_segs

    sess.do_command("run_planner 1")
    # Re-plan cleared the pins and restored a pre-split selection (behavioral
    # contract; the exact candidate-list bookkeeping is an implementation detail).
    w = [b for b in sess.bundles if b.input.original_bundle.id == 3][0]
    assert not w.plan.seg_net_pull and not w.plan.seg_slide_lo, \
        "dogleg overrides survived a re-plan"
    restored_segs = len(w.input.candidates[w.plan.selected_topology_index].segments)
    assert restored_segs < split_segs, \
        f"pre-split topology not restored ({restored_segs} vs split {split_segs})"

    # The next NUTS re-detects the cycle and re-applies the dogleg → still clean.
    sess.do_command("run_nuts")
    assert sess.nuts_result.dogleg_topologies, "dogleg not re-detected after re-plan"
    assert sess.nuts_result.num_overlaps == 0, \
        f"re-plan reintroduced {sess.nuts_result.num_overlaps} overlap(s)"
