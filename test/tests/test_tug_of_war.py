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
T between them.  The canonical case is b44's `TRUNK_H+MST@y11915` staircase:
seg5 tugged by seg1(-)/seg3(+), nominal separation 1250.  The detector reads
only the already-derived ConnSeg data (net_pull + junction at_pos), so it never
changes selection or placement.
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


def test_b44_canonical_tug_pair():
    """The TRUNK_H+MST@y11915 staircase reproduces the documented tug: on the
    interior trunk seg5, the -pull rider seg1 sits BELOW the +pull rider seg3,
    so they diverge and stretch seg5 (separation 2700-1450 = 1250)."""
    s = _b44_bundle()
    cand = next(c for c in s.bundles[0].input.candidates
                if c.type == "TRUNK_H+MST@y11915"
                and c.estimated_wirelength == 3510)
    segs = _segs(cand, s.fp)

    # The detector names exactly the documented pair (T=seg5, lo=seg1, hi=seg3).
    assert find_tug_of_war_pairs(segs) == [(5, 1, 3)]

    # ...and the underlying semantics hold: opposite pulls, distinct positions,
    # the -puller strictly below the +puller along the stretched trunk.
    assert segs[1].net_pull < 0          # seg1 pulls toward lower-along-T
    assert segs[3].net_pull > 0          # seg3 pulls toward higher-along-T
    at = {c.seg_idx: c.at_pos for c in segs[5].conns
          if c.kind == buda.SegConnKind.SEG}
    assert at[1] < at[3]                  # seg1 rides below seg3 on seg5
    assert at[3] - at[1] == 1250          # the documented separation


def test_plain_trunk_has_no_tug():
    """A plain 2-segment L/trunk candidate has no interior segment with
    opposing riders — the detector must not flag it (negative control)."""
    s = _b44_bundle()
    cand = next(c for c in s.bundles[0].input.candidates
                if c.type.startswith("TRUNK_V") and len(c.segments) == 2)
    assert find_tug_of_war_pairs(_segs(cand, s.fp)) == []


def test_tug_is_structural_to_mst_families():
    """Across b44's whole candidate pool, tug pairs occur ONLY on MST-family
    trees (multiple riders on a shared spine), never on plain trunks — the
    structural signature the wishlist describes."""
    s = _b44_bundle()
    flagged = [c.type for c in s.bundles[0].input.candidates
               if find_tug_of_war_pairs(_segs(c, s.fp))]
    assert flagged, "expected at least the canonical MST staircase to flag"
    assert all("MST" in t for t in flagged), flagged


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


def test_b44_canonical_tug_unaffected_by_empty_override():
    """The canonical b44 pair has no dogleg override (empty seg_net_pull), so
    the effective-pull path is a no-op and the report is unchanged."""
    s = _b44_bundle()
    cand = next(c for c in s.bundles[0].input.candidates
                if c.type == "TRUNK_H+MST@y11915"
                and c.estimated_wirelength == 3510)
    segs = _segs(cand, s.fp)
    assert find_tug_of_war_pairs(segs, net_pull=[]) == [(5, 1, 3)]
    assert find_tug_of_war_pairs(segs, net_pull=None) == [(5, 1, 3)]


def test_dump_topologies_reports_tug():
    """The signal is surfaced in `dump_topologies --problems`: a TUG flag on
    the bundle, a per-pair detail line, and a summary count."""
    s = _b44_bundle()
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        idx = next(i for i, c in enumerate(s.bundles[0].input.candidates)
                   if c.type == "TRUNK_H+MST@y11915"
                   and c.estimated_wirelength == 3510)
        s.do_command(f"select_topology 1 {idx + 1}")
        s.do_command("run_planner")
    out = io.StringIO()
    with contextlib.redirect_stdout(out), buda.ostream_redirect():
        s.do_command("dump_topologies --problems")
    txt = out.getvalue()
    assert "TUG(1)" in txt
    assert "seg5 stretched by seg1(-)/seg3(+)" in txt
    assert "bundles with tug-of-war  : 1/1" in txt


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
