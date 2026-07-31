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

"""Tug-of-war connector-pair detector (wishlist-nuts "Opposite-pull connector
pairs" — a structural realization-risk signal).

Two connectors riding one interior segment T, pulling in opposite OUTWARD
directions, each shorten their own perpendicular leg while jointly STRETCHING
T between them.  The historical canonical case was b44's `TRUNK_H+MST@y11915`
staircase: seg5 tugged by seg1(-)/seg3(+), nominal separation 1250.

Under the ANCHOR-INTERVAL pull model (issue #523) that canonical tug is
DISSOLVED: the tug was an artifact of the old gain-only votes (seg3's +1 came
from the busterm path never pricing the wire its slide stretched), and the
interval model prices both sides — seg3's nominal now sits inside its optimum
interval, so its pull is honestly 0 and no b44 candidate tugs at all.  The
detector itself stays: it reads only the derived ConnSeg data (net_pull +
junction at_pos), a REAL tug (genuinely disjoint optima on a shared spine)
would still flag, and the post-dogleg `plan.seg_net_pull` override path can
still create one — which is how the display tests below exercise the
positive reporting path honestly.
"""
import io
import contextlib

import buda
import buda_cli
from buda_session.util import find_tug_of_war_pairs


def _b44_bundle():
    """b44's single bundle after generate_topologies (no selection needed —
    the detector scans a candidate's ConnTopology directly)."""
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
    return s


def _segs(cand, fp):
    ct = buda.ConnTopology()
    ct.build(cand, fp)
    return list(ct.segs())


def test_b44_canonical_tug_dissolved():
    """The TRUNK_H+MST@y11915 staircase no longer tugs: the old model's
    seg1(-)/seg3(+) divergence was a gain-only-vote artifact, and the interval
    model prices seg3's stretched wire — its nominal (2700) sits inside its
    optimum interval, so its pull is honestly 0 and the pair dissolves."""
    s = _b44_bundle()
    cand = next(c for c in s.bundles[0].input.candidates
                if c.type == "TRUNK_H+MST@y11915"
                and c.estimated_wirelength == 3510)
    segs = _segs(cand, s.fp)

    assert find_tug_of_war_pairs(segs) == []

    # The model-level reason, pinned: seg1 keeps its genuine -1 (its optimum
    # lies below, saturating where the trunk stops following — 1200, not the
    # old far-face overshoot), while seg3's nominal is INSIDE its optimum.
    assert segs[1].net_pull < 0
    assert segs[1].pull_break == 1200
    assert segs[3].net_pull == 0
    assert segs[3].pull_lo <= segs[3].perp_pos <= segs[3].pull_hi


def test_plain_trunk_has_no_tug():
    """A plain 2-segment L/trunk candidate has no interior segment with
    opposing riders — the detector must not flag it (negative control)."""
    s = _b44_bundle()
    cand = next(c for c in s.bundles[0].input.candidates
                if c.type.startswith("TRUNK_V") and len(c.segments) == 2)
    assert find_tug_of_war_pairs(_segs(cand, s.fp)) == []


def test_no_phantom_tugs_across_b44_pool():
    """Across b44's whole candidate pool, the interval model leaves ZERO tug
    pairs — every historical flag on this vehicle was the gain-only-vote
    artifact, dissolved by pricing both sides of a slide.  (A genuine tug —
    disjoint optima on a shared spine — would still flag; b44 simply has
    none.)"""
    s = _b44_bundle()
    flagged = [c.type for c in s.bundles[0].input.candidates
               if find_tug_of_war_pairs(_segs(c, s.fp))]
    assert flagged == [], flagged


def test_inward_pull_pair_not_flagged():
    """Only OUTWARD (diverging) pairs stretch T.  A -puller ABOVE a +puller
    converges (benign) and must not be flagged.  Exercised directly on the
    detector's contract via a tiny fake-seg model so the geometry is explicit."""
    class _C:
        def __init__(self, kind, seg_idx, at_pos):
            self.kind = kind
            self.seg_idx = seg_idx
            self.at_pos = at_pos

    class _S:
        def __init__(self, net_pull, conns):
            self.net_pull = net_pull
            self.conns = conns

    SEG = buda.SegConnKind.SEG
    # T (idx 0) carries two riders: idx1 (+pull) low, idx2 (-pull) high →
    # they converge, so NO tug.  Flip the pulls and it WOULD flag.
    segs = [
        _S(0, [_C(SEG, 1, 100), _C(SEG, 2, 900)]),   # T
        _S(+1, [_C(SEG, 0, 100)]),                    # low rider, +pull
        _S(-1, [_C(SEG, 0, 900)]),                    # high rider, -pull
    ]
    assert find_tug_of_war_pairs(segs) == []
    # Same positions, opposite pulls → the outward (diverging) case flags.
    segs[1].net_pull, segs[2].net_pull = -1, +1
    assert find_tug_of_war_pairs(segs) == [(0, 1, 2)]


def test_seg_net_pull_override_changes_verdict():
    """Post-dogleg, NUTS places with `plan.seg_net_pull` overrides where they
    differ from ConnTopology's recomputed pull (build_nuts_maps: an entry wins
    when the array length matches and it is not the INT_MIN sentinel).  The
    detector must read the SAME effective pulls, so an override flips its verdict
    exactly as it flips the placed geometry."""
    class _C:
        def __init__(self, seg_idx, at_pos):
            self.kind = buda.SegConnKind.SEG
            self.seg_idx = seg_idx
            self.at_pos = at_pos

    class _S:
        def __init__(self, net_pull, conns):
            self.net_pull = net_pull
            self.conns = conns

    INT_MIN = -2147483648
    # Raw pulls: low rider +1, high rider -1 → converging → NO tug.
    segs = [
        _S(0, [_C(1, 100), _C(2, 900)]),   # T
        _S(+1, [_C(0, 100)]),               # low rider
        _S(-1, [_C(0, 900)]),               # high rider
    ]
    assert find_tug_of_war_pairs(segs) == []

    # A dogleg override that flips both riders → diverging → the report MUST
    # now flag the tug (matching what NUTS actually placed).
    override = [INT_MIN, -1, +1]            # T untouched; riders flipped
    assert find_tug_of_war_pairs(segs, net_pull=override) == [(0, 1, 2)]

    # Guard fidelity: a length-mismatched (stale) override is ignored, exactly
    # as build_nuts_maps ignores np_ok == false.
    assert find_tug_of_war_pairs(segs, net_pull=[-1, +1]) == []
    # A per-entry INT_MIN sentinel falls back to cs.net_pull for that rider.
    assert find_tug_of_war_pairs(segs, net_pull=[INT_MIN, INT_MIN, INT_MIN]) == []


def test_b44_canonical_verdict_unaffected_by_empty_override():
    """An absent/empty dogleg override (seg_net_pull) is a no-op on the
    effective-pull path: the canonical staircase's verdict (dissolved, [])
    is identical with [] and None."""
    s = _b44_bundle()
    cand = next(c for c in s.bundles[0].input.candidates
                if c.type == "TRUNK_H+MST@y11915"
                and c.estimated_wirelength == 3510)
    segs = _segs(cand, s.fp)
    assert find_tug_of_war_pairs(segs, net_pull=[]) == []
    assert find_tug_of_war_pairs(segs, net_pull=None) == []


_INT_MIN = -2147483648


def _select_canonical_with_tug_override(s):
    """Select the canonical staircase and apply a post-dogleg-style
    `plan.seg_net_pull` override that recreates the historical tug (seg3
    forced to +1; seg1 falls back to its genuine -1) — the honest vehicle
    for the positive display path now that no b44 candidate tugs naturally
    (the display code consumes exactly this override: reports.py passes
    plan.seg_net_pull to the detector with NUTS's length/sentinel guard)."""
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        idx = next(i for i, c in enumerate(s.bundles[0].input.candidates)
                   if c.type == "TRUNK_H+MST@y11915"
                   and c.estimated_wirelength == 3510)
        s.do_command(f"select_topology 1 {idx + 1}")
        s.do_command("run_planner")
    w = s.bundles[0]
    n = len(w.input.candidates[w.plan.selected_topology_index].segments)
    ov = [_INT_MIN] * n
    ov[3] = +1
    w.plan.seg_net_pull = ov


def test_dump_topologies_reports_tug():
    """The signal is surfaced in `dump_topologies --problems`: a TUG flag on
    the bundle, a per-pair detail line, and a summary count.  Exercised via
    the seg_net_pull override (see the helper) since the natural pulls no
    longer tug."""
    s = _b44_bundle()
    _select_canonical_with_tug_override(s)
    out = io.StringIO()
    with contextlib.redirect_stdout(out), buda.ostream_redirect():
        s.do_command("dump_topologies --problems")
    txt = out.getvalue()
    assert "TUG(1)" in txt
    assert "seg5 stretched by seg1(-)/seg3(+)" in txt
    assert "bundles with tug-of-war  : 1/1" in txt


def _run_to_nuts(s, cand_pred):
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        idx = next(i for i, c in enumerate(s.bundles[0].input.candidates)
                   if cand_pred(c))
        for c in (f"select_topology 1 {idx + 1}", "run_planner", "run_nuts"):
            s.do_command(c)


def test_check_design_reports_tug_advisory():
    """check_design surfaces the realization-risk signal as an ADVISORY at the
    nuts/dnuts stage — separate from the violation count (a tug is a risk, not a
    violation) — so the standard audit points at it too, not only
    dump_topologies.  Exercised via the seg_net_pull override since the
    natural pulls no longer tug."""
    s = _b44_bundle()
    _select_canonical_with_tug_override(s)
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        s.do_command("run_nuts")
    out = io.StringIO()
    with contextlib.redirect_stdout(out), buda.ostream_redirect():
        s.do_command("check_design nuts")
    txt = out.getvalue()
    assert "no violations found" in txt          # the tug is NOT a violation
    assert "Advisory: 1 tug-of-war realization-risk pair(s) on 1 bundle(s)" in txt


def test_check_design_no_tug_advisory_on_canonical_natural_pulls():
    """Without the override the canonical staircase carries no tug under the
    interval model, so check_design emits no advisory — the dissolved artifact
    stays dissolved end-to-end."""
    s = _b44_bundle()
    _run_to_nuts(s, lambda c: c.type == "TRUNK_H+MST@y11915"
                 and c.estimated_wirelength == 3510)
    out = io.StringIO()
    with contextlib.redirect_stdout(out), buda.ostream_redirect():
        s.do_command("check_design nuts")
    assert "tug-of-war" not in out.getvalue()


def test_check_design_no_tug_advisory_on_plain_trunk():
    """A plain trunk selection has no tug pair, so check_design emits no
    advisory line."""
    s = _b44_bundle()
    _run_to_nuts(s, lambda c: c.type.startswith("TRUNK_V")
                 and len(c.segments) == 2)
    out = io.StringIO()
    with contextlib.redirect_stdout(out), buda.ostream_redirect():
        s.do_command("check_design nuts")
    assert "tug-of-war" not in out.getvalue()


def test_dump_topologies_no_tug_on_plain_selection():
    """Selecting a plain trunk shows no TUG flag (the signal tracks the
    displayed candidate, not the pool)."""
    s = _b44_bundle()
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        idx = next(i for i, c in enumerate(s.bundles[0].input.candidates)
                   if c.type.startswith("TRUNK_V") and len(c.segments) == 2)
        s.do_command(f"select_topology 1 {idx + 1}")
        s.do_command("run_planner")
    out = io.StringIO()
    with contextlib.redirect_stdout(out), buda.ostream_redirect():
        s.do_command("dump_topologies")
    txt = out.getvalue()
    assert "TUG" not in txt
    assert "bundles with tug-of-war  : 0/1" in txt
