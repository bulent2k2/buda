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

"""ANTENNA — dangling-wire detection and the generator fix (issue #482).

Two halves, matching the issue:

1. `check_design` must FLAG a dangling wire at the placed stages: a segment
   attached to the route at fewer than two points is electrically inert metal
   whose far end terminates in nothing.  Detector is structural (conn records),
   so check_nuts and check_dnuts answer identically.

2. The trunk+MST generator must not PRODUCE one.  The reported case: an MST
   edge leg laid collinear on top of the trunk stub leaving the same block
   face — the relay single-tap model demotes the duplicate landing to a
   nullopt junction, and ConnTopology never infers a junction between
   collinear segments, so the leg's near end connects to nothing.
"""
import contextlib
import io
import os
import sys
from pathlib import Path

import pytest

import buda

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
import buda_cli  # noqa: E402

_ROOT = Path(__file__).parents[2]
LAYER_H, LAYER_V = 4, 5


# ── fixtures ─────────────────────────────────────────────────────────────────

def _layers():
    ls = buda.LayerStack()
    ls.add_layer(LAYER_H, "M4", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    ls.add_layer(LAYER_V, "M5", buda.LayerDir.VERTICAL, buda.LayerType.TOP)
    return ls


def _seg(x1, y1, x2, y2, hint):
    s = buda.Segment()
    s.start = buda.Point(x1, y1)
    s.end = buda.Point(x2, y2)
    s.layer_hint = hint
    return s


def _gen_z(fp=None):
    """A REAL generated Z_HVH route (A --H-- V trunk --H-- B).

    Generated, not hand-built: ConnTopology takes its BUSTERM conns from the
    generator's `seg_busterms` annotation (Phase 4 retired geometric
    inference), so a hand-assembled topology would look like all-antennas and
    the fixture would prove nothing.
    """
    if fp is None:
        fp = buda.Floorplan()
        fp.add_block("A", 0, 0, 100, 100)
        fp.add_block("B", 400, 200, 500, 300)
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(LAYER_H, LAYER_V)
    cands = g.generate_candidates("A", ["B"])
    topo = next(c for c in cands if c.type.startswith("Z_HVH"))
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    return fp, topo, ct


def _clean_setup():
    """The healthy generated route — every segment attached at 2+ points."""
    return _gen_z()


def _dangling_setup():
    """The same route plus one appended stub hanging off the V trunk whose far
    end reaches nothing — the antenna (the shape the trunk+MST generator used
    to leave behind)."""
    fp, topo, ct = _gen_z()
    trunk = next(i for i, cs in enumerate(ct.segs()) if not cs.horiz)
    tx = ct.segs()[trunk].perp_pos
    lo, hi = ct.segs()[trunk].along_lo, ct.segs()[trunk].along_hi
    mid = (lo + hi) // 2
    segs = list(topo.segments)
    segs.append(_seg(tx, mid, tx + 80, mid, LAYER_H))   # dangles at tx+80
    topo.segments = segs
    buda.annotate_seg_conns(topo)
    ct2 = buda.ConnTopology()
    ct2.build(topo, fp)
    return fp, topo, ct2


def _nominal_nuts(ct, bundle_id=1):
    nuts = buda.NUTSResult()
    out = []
    for i, cs in enumerate(ct.segs()):
        ts = buda.TrackSegment()
        ts.bundle_id = bundle_id
        ts.seg_idx = i
        ts.horiz = cs.horiz
        ts.layer = LAYER_H if cs.horiz else LAYER_V
        ts.span_lo, ts.span_hi = cs.along_lo, cs.along_hi
        ts.track_position = float(cs.perp_pos)
        ts.width = 1.0
        ts.placed = True
        out.append(ts)
    nuts.segments = out
    return nuts


def _nominal_dnuts(ct, num_bits, bundle_id=1):
    dnuts = buda.DetailedNUTSResult()
    out = []
    for i, cs in enumerate(ct.segs()):
        for b in range(num_bits):
            ns = buda.NetSegment()
            ns.bundle_id = bundle_id
            ns.seg_idx = i
            ns.bit_index = b
            ns.layer = LAYER_H if cs.horiz else LAYER_V
            ns.span_lo, ns.span_hi = cs.along_lo, cs.along_hi
            ns.track_position = float(cs.perp_pos)
            ns.width = 1.0
            out.append(ns)
    dnuts.net_segments = out
    return dnuts


def _antennas(res):
    return [v for v in res.violations
            if v.kind == buda.ViolationKind.ANTENNA]


# ── 1. the check ─────────────────────────────────────────────────────────────

def test_check_nuts_flags_the_antenna():
    """A segment with a single connection is reported at the NUTS stage, and
    the violation names the offending segment."""
    fp, topo, ct = _dangling_setup()
    ant = len(ct.segs()) - 1                 # the appended stub
    # Fixture precondition: it really is attached at one point only.
    assert len(ct.segs()[ant].conns) == 1
    res = buda.check_nuts(ct, _nominal_nuts(ct), topo, fp, _layers(), 1)
    ants = _antennas(res)
    assert len(ants) == 1, [v.message for v in res.violations]
    assert ants[0].seg_idx == ant
    assert "dangling" in ants[0].message and "nuts" in ants[0].message


def test_check_dnuts_flags_the_antenna():
    """Same structural verdict at the DetailedNUTS stage (the issue asks for
    both), tagged with that stage."""
    fp, topo, ct = _dangling_setup()
    res = buda.check_dnuts(ct, _nominal_dnuts(ct, 4), topo, fp, _layers(), 1, 4)
    ants = _antennas(res)
    assert len(ants) == 1 and ants[0].seg_idx == len(ct.segs()) - 1
    assert "dnuts" in ants[0].message


def test_no_antenna_on_a_healthy_route():
    """No false positive: every segment of a well-formed route is attached at
    two or more points, at both placed stages."""
    fp, topo, ct = _clean_setup()
    assert not _antennas(buda.check_nuts(ct, _nominal_nuts(ct), topo, fp,
                                        _layers(), 1))
    assert not _antennas(buda.check_dnuts(ct, _nominal_dnuts(ct, 4), topo, fp,
                                         _layers(), 1, 4))


def test_single_segment_topology_is_not_an_antenna():
    """A one-segment topology has no junctions by construction — a missing
    block tap there is BUSTERM_OPEN's business, not the antenna detector's
    (guards against the check firing on every point-to-point I-shape)."""
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 100)
    fp.add_block("B", 400, 0, 500, 100)
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(LAYER_H, LAYER_V)
    topo = next(c for c in g.generate_candidates("A", ["B"])
                if len(c.segments) == 1)
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    assert not _antennas(buda.check_nuts(ct, _nominal_nuts(ct), topo, fp,
                                        _layers(), 1))


def test_pass_through_block_counts_as_an_attachment():
    """Codex review on #483 (P2): a segment that CROSSES a connected block's
    footprint is electrically joined there — the block-coverage checks accept
    exactly that as a valid connection — so it must count toward the two-point
    bar.  Without this term a trunk crossing its driver and joining one
    junction would be reported on an otherwise clean design (false positive).

    Built explicitly (block C added to the floorplan and to the topology's
    connected-block contract, positioned ON the appended stub) so the test
    states the property directly instead of hunting for a generator shape
    that happens to produce a pass-through."""
    fp, topo, ct = _gen_z()
    trunk = next(i for i, cs in enumerate(ct.segs()) if not cs.horiz)
    tx = ct.segs()[trunk].perp_pos
    lo, hi = ct.segs()[trunk].along_lo, ct.segs()[trunk].along_hi
    mid = (lo + hi) // 2
    # C straddles the appended stub's row, to the right of the trunk.
    fp.add_block("C", tx + 40, mid - 30, tx + 120, mid + 30)
    segs = list(topo.segments)
    segs.append(_seg(tx, mid, tx + 80, mid, LAYER_H))   # ends INSIDE C
    topo.segments = segs
    topo.connected_block_names = list(topo.connected_block_names) + ["C"]
    buda.annotate_seg_conns(topo)
    ct2 = buda.ConnTopology()
    ct2.build(topo, fp)
    added = len(ct2.segs()) - 1
    cs = ct2.segs()[added]
    # Preconditions: ONE conn record, and it really does cross C — so the
    # record-count rule would have called this an antenna.
    assert len(cs.conns) == 1
    c = fp.get_block_bounds("C")
    assert c.y1 <= cs.perp_pos <= c.y2 and cs.along_hi >= c.x1
    res = buda.check_nuts(ct2, _nominal_nuts(ct2), topo, fp, _layers(), 1)
    assert not _antennas(res), [v.message for v in res.violations]


def test_edge_grazing_block_is_not_a_pass_through_attachment():
    """The pass-through term counts an INTERIOR crossing, not an edge-graze.

    A wire lying ON a block's face (perp == a face coordinate) or touching only
    a corner is over-the-cell metal that joins nothing THROUGH the block, so it
    must NOT manufacture a phantom second attachment.  The escape it hid: the
    `TRUNK_V_OOB+MST` candidate of the lShape1-dnuts repro carried two collinear
    stub halves grazing the driver block's top face — one real junction each,
    rescued by the inclusive coverage span and reported CLEAN.  Mirror of
    `test_pass_through_block_counts_as_an_attachment`, with C's bottom edge
    flush against the stub's row instead of straddling it: the inclusive span
    still 'passes through' (pre-fix), the interior test does not (post-fix)."""
    fp, topo, ct = _gen_z()
    trunk = next(i for i, cs in enumerate(ct.segs()) if not cs.horiz)
    tx = ct.segs()[trunk].perp_pos
    lo, hi = ct.segs()[trunk].along_lo, ct.segs()[trunk].along_hi
    mid = (lo + hi) // 2
    # C's BOTTOM edge sits exactly on the stub's row: the stub grazes C's face,
    # it does not cross C's interior (perp == C.y1).
    fp.add_block("C", tx + 40, mid, tx + 120, mid + 60)
    segs = list(topo.segments)
    segs.append(_seg(tx, mid, tx + 80, mid, LAYER_H))   # runs ALONG C's bottom
    topo.segments = segs
    topo.connected_block_names = list(topo.connected_block_names) + ["C"]
    buda.annotate_seg_conns(topo)
    ct2 = buda.ConnTopology()
    ct2.build(topo, fp)
    added = len(ct2.segs()) - 1
    cs = ct2.segs()[added]
    # Preconditions: one real conn, and it grazes C's face (perp ON the edge),
    # so the inclusive span would have — wrongly — rescued it.
    assert len(cs.conns) == 1
    c = fp.get_block_bounds("C")
    assert cs.perp_pos == c.y1 and cs.along_hi > c.x1
    res = buda.check_nuts(ct2, _nominal_nuts(ct2), topo, fp, _layers(), 1)
    ants = _antennas(res)
    assert [v.seg_idx for v in ants] == [added], [v.message for v in res.violations]


def test_internal_seam_of_a_rectilinear_block_is_a_pass_through(monkeypatch):
    """The interior of a RECTILINEAR block is the interior of its UNION, not of
    each rect.  Two abutting rects share an edge that is a FACE of each yet
    INTERIOR to the block — a wire along it is over-the-cell for its whole
    length, with metal on both sides.  The per-rect strict test alone would
    (wrongly) flag it as a graze; the seam term restores it (Ben's review on
    #848).  Same probe and one-junction precondition as the graze test — only
    C's shape differs: two abutting rects meeting on the stub's row."""
    fp, topo, ct = _gen_z()
    trunk = next(i for i, cs in enumerate(ct.segs()) if not cs.horiz)
    tx = ct.segs()[trunk].perp_pos
    lo, hi = ct.segs()[trunk].along_lo, ct.segs()[trunk].along_hi
    mid = (lo + hi) // 2
    # C is two abutting rects sharing the edge y=mid — the stub runs the seam
    # (add_block_rects self-creates the block from its rect list).
    fp.add_block_rects("C", [(tx + 40, mid - 60, tx + 120, mid),
                             (tx + 40, mid, tx + 120, mid + 60)])
    segs = list(topo.segments)
    segs.append(_seg(tx, mid, tx + 80, mid, LAYER_H))            # runs the seam
    topo.segments = segs
    topo.connected_block_names = list(topo.connected_block_names) + ["C"]
    buda.annotate_seg_conns(topo)
    ct2 = buda.ConnTopology()
    ct2.build(topo, fp)
    added = len(ct2.segs()) - 1
    cs = ct2.segs()[added]
    rects = fp.get_block_rects("C")
    # Precondition: two rects abutting on the stub's row (a real internal seam),
    # and the stub carries the single junction that would make it an antenna.
    assert len(rects) == 2 and len(cs.conns) == 1
    assert cs.perp_pos == rects[0][3] == rects[1][1]            # seam coordinate
    res = buda.check_nuts(ct2, _nominal_nuts(ct2), topo, fp, _layers(), 1)
    assert not _antennas(res), [v.message for v in res.violations]


def test_multiway_junction_conns_count_as_one_point():
    """Codex review on #483 (P2): a segment whose end meets a MULTI-WAY
    junction collects one SegConn per neighbour, all at the same `at_pos`.
    Counting records would read 2+ and hide the antenna; counting distinct
    attachment POSITIONS surfaces it.  Fixture: a collinear-split trunk (the
    dogleg shape) with a stub landing exactly on the split point."""
    fp, topo, ct = _gen_z()
    trunk = next(i for i, cs in enumerate(ct.segs()) if not cs.horiz)
    tx = ct.segs()[trunk].perp_pos
    lo, hi = ct.segs()[trunk].along_lo, ct.segs()[trunk].along_hi
    split = (lo + hi) // 2
    segs = list(topo.segments)
    segs[trunk] = _seg(tx, lo, tx, split, LAYER_V)        # lower piece
    segs.append(_seg(tx, split, tx, hi, LAYER_V))         # upper piece
    segs.append(_seg(tx, split, tx + 120, split, LAYER_H))  # the antenna
    topo.segments = segs
    buda.annotate_seg_conns(topo)
    ct2 = buda.ConnTopology()
    ct2.build(topo, fp)
    ant = len(ct2.segs()) - 1
    cs = ct2.segs()[ant]
    seg_conns = [c for c in cs.conns if c.kind == buda.SegConnKind.SEG]
    # The precondition that makes this test discriminating: MORE THAN ONE
    # conn record, all at a SINGLE position — a record count would pass it.
    assert len(seg_conns) >= 2, [(c.seg_idx, c.at_pos) for c in cs.conns]
    assert len({c.at_pos for c in seg_conns}) == 1
    res = buda.check_nuts(ct2, _nominal_nuts(ct2), topo, fp, _layers(), 1)
    ants = _antennas(res)
    assert [v.seg_idx for v in ants] == [ant], [v.message for v in ants]


# ── 2. the generator fix ─────────────────────────────────────────────────────

def _bus011_candidates():
    """The issue's repro pool: bus_011 of the big2 design (tc3b_flat_x5)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    cwd = os.getcwd()
    os.chdir(_ROOT / "flow")
    try:
        with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
            for c in ["source tracks/tracks4top.buda",
                      "source big_data_test/big2/tc3b_flat_x5.buda",
                      "run_bundler",
                      "generate_topologies_for_bundle bus_011"]:
                s.do_command(c)
    finally:
        os.chdir(cwd)
    w = next(w for w in s.bundles
             if w.input.original_bundle.get_net_names()[0].startswith("bus_011"))
    return s, w


def _antenna_segs(topo, fp):
    """Antenna count via the AUTHORITATIVE detector, never a proxy.

    This helper used to re-roll the predicate as `len(cs.conns) < 2` — the
    conn-RECORD count.  That is precisely the defect the #483 review found in
    the checker and issue #485 found a third copy of in the generator's seed-
    trunk gate: it misses a segment whose records all sit at ONE point, and it
    falsely accuses one attached via a pass-through block.  Ask check_nuts.
    """
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        res = buda.check_nuts(ct, _nominal_nuts(ct), topo, fp, _layers(), 1)
    return len(_antennas(res))


def _free_collinear_splits(topo):
    """The issue #849 defect predicate, over a candidate's raw geometry.

    Two COLLINEAR segments meeting end-to-end at a point where nothing else of
    the topology is attached (no endpoint, no perpendicular crossing) are one
    wire the generator emitted as two.  ConnTopology infers no junction between
    collinear segments, so the pair is not merely untidy — it is not connected
    at all, and NUTS is free to seat the halves on different tracks.  Returns
    the offending (i, j) index pairs.
    """
    segs = list(topo.segments)

    def horiz(s):
        return s.start.y == s.end.y

    def perp(s):
        return s.start.y if horiz(s) else s.start.x

    def lo(s):
        return min(s.start.x, s.end.x) if horiz(s) else min(s.start.y, s.end.y)

    def hi(s):
        return max(s.start.x, s.end.x) if horiz(s) else max(s.start.y, s.end.y)

    def touches(s, qx, qy):
        if horiz(s):
            return s.start.y == qy and lo(s) <= qx <= hi(s)
        return s.start.x == qx and lo(s) <= qy <= hi(s)

    out = []
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            a, b = segs[i], segs[j]
            if horiz(a) != horiz(b) or perp(a) != perp(b):
                continue
            if lo(a) == hi(a) or lo(b) == hi(b):
                continue          # degenerate: no wire to split
            if hi(a) == lo(b):
                q = hi(a)
            elif hi(b) == lo(a):
                q = lo(a)
            else:
                continue          # overlapping or apart, not end-to-end
            qx, qy = (q, perp(a)) if horiz(a) else (perp(a), q)
            if any(touches(segs[k], qx, qy)
                   for k in range(len(segs)) if k not in (i, j)):
                continue          # a real junction holds the split together
            out.append((i, j))
    return out


@pytest.mark.mid
def test_lshape_oob_mst_free_collinear_split_is_not_generated():
    """The reported escape, end to end — repaired at GENERATION (issue #849).

    The `TRUNK_V_OOB+MST@x-80` candidate of the lShape1-dnuts design carried
    THREE collinear pieces on the driver block's top row: the seed trunk's stub
    `[-80,100]`, a relay connector `[100,150]`, and `[150,600]`.  The join at
    x=150 is a real T (the MST leg to `l1` lands there); the one at x=100 held
    NOTHING — `connect`'s orthogonal-landing branch emitted its H leg from the
    stub's own endpoint after the V leg vanished — so the stub and the
    connector were two dangling wires, both ANTENNA, and pre-#848 the audit
    reported Success on them.

    Relay completion now MERGES that free join (dropping either piece would
    lose reach).  The merge dissolves the antennas and reveals what they were
    hiding: the merged wire closes a LOOP with the spine, so the pre-existing
    clean-tree gate drops the whole hybrid — no gate change was needed.  So the
    candidate is GONE from the pool, and the design routes clean on the
    planner's own pick.
    """
    s = buda_cli.BudaSession()
    s.no_viz = True
    cwd = os.getcwd()
    os.chdir(_ROOT / "flow")
    try:
        with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
            for c in ["add_block rect 100 0 700 100",
                      "add_block l1 rect 0 300 200 600 rect 0 600 300 800",
                      "add_block l2 rect 500 300 800 400 rect 600 400 800 500",
                      "source tracks/tracks2top.buda",
                      "add_bus bus1[8] rect.out l1.in,l2.in",
                      "run_bundler STRICT", "generate_topologies"]:
                s.do_command(c)
    finally:
        os.chdir(cwd)
    w = s.bundles[0]
    types = [t.type for t in w.input.candidates]
    # Non-vacuity: the pool is real and still carries the hybrid FAMILY, so the
    # assertion below is about this shape and not about an empty pool.
    assert len(types) > 20, types
    assert any(t.startswith("TRUNK_V+MST") for t in types), types
    assert not any(t.startswith("TRUNK_V_OOB+MST") for t in types), (
        f"the free-split hybrid is back in the pool: {types}")
    # And no candidate of this pool carries the defect shape or an antenna.
    for i, t in enumerate(w.input.candidates):
        assert not _free_collinear_splits(t), (i, t.type)
        assert _antenna_segs(t, s.fp) == 0, (i, t.type)


@pytest.mark.mid
@pytest.mark.parametrize("flow", ["demo/comprehensive_demo.buda",
                                  "flow/rnr/mix.buda",
                                  "flow/big_data_test/big.buda"])
def test_no_free_collinear_split_survives_generation(flow):
    """Issue #849 corpus-wide: no candidate anywhere may carry a FREE
    collinear-adjacent split.  The `TRUNK_V_OOB+MST` report is one instance of
    a shape that is broken wherever it occurs — two pieces of one wire with
    nothing holding them at the same track — so the guard is the predicate, not
    the vehicle.  Measured over EVERY candidate of EVERY bundle, like its
    antenna sibling above: a non-selected split is still a candidate the
    planner may pick under different congestion.

    HONEST SCOPE: this is a FORWARD guard, not fail-before evidence.  Measured
    on the pre-fix engine these three flows carry ZERO of the shape (0 of 300 /
    1183 / 3851 candidates), so it passed before the fix too — the shape needs
    an orthogonal relay landing whose connector leg degenerates, which the
    lShape1 vehicle has and these do not.  The non-vacuous instance is
    `test_lshape_oob_mst_free_collinear_split_is_not_generated` above, where
    the pre-fix engine emits exactly one (candidate 28, segments 1|5 at
    (100,100))."""
    s, pool = _corpus_pool(flow)
    offenders = [(bid, t.type, _free_collinear_splits(t)) for bid, t in pool
                 if _free_collinear_splits(t)]
    assert not offenders, (
        f"{len(offenders)} candidate(s) with a free collinear split: "
        f"{offenders[:8]}")


@pytest.mark.mid
def test_trunk_mst_generates_no_antenna():
    """Issue #482: bus_011's TRUNK_V+MST@x3822 candidate carried a redundant
    MST edge leg collinear on the trunk stub out of blk_19, dangling at its
    near end.  No candidate of the pool may have an antenna — and the pool
    must not shrink (the fix repairs the candidate, it does not drop it)."""
    s, w = _bus011_candidates()
    assert len(w.input.candidates) > 30, "fixture pool unexpectedly small"
    offenders = [(i, t.type, _antenna_segs(t, s.fp))
                 for i, t in enumerate(w.input.candidates)
                 if _antenna_segs(t, s.fp)]
    assert not offenders, f"antenna segments still generated: {offenders}"


@pytest.mark.mid
def test_hybrid_and_pure_mst_candidates_are_present_and_clean():
    """The issue asks explicitly that pure MST not suffer the same bug: both
    families must actually BE in the pool (else the test above is vacuous)
    and both must be antenna-free."""
    s, w = _bus011_candidates()
    fams = {"hybrid": [], "pure": []}
    for t in w.input.candidates:
        if "+MST" in t.type:
            fams["hybrid"].append(t)
        elif "MST" in t.type:
            fams["pure"].append(t)
    assert fams["hybrid"], "no TRUNK+MST candidate in the repro pool"
    for kind, topos in fams.items():
        for t in topos:
            assert _antenna_segs(t, s.fp) == 0, f"{kind} {t.type} has an antenna"


# ── 3. the seed trunk (issue #485) ───────────────────────────────────────────
#
# #482's census left 26 antennas, every one of them seg 0 — the trunk+MST
# hybrid's SEED TRUNK.  Two independent defects in the gate meant to drop
# exactly that shape, one per family:
#
#   OOB (20)     the dangling-spine gate judged a copy re-annotated by
#                annotate_topology, which re-derives seg_busterms
#                GEOMETRICALLY and so re-adds the face landings
#                complete_relay_junctions demoted to nullopt.  The gate saw a
#                topology that is not the one being pooled.
#   in-bbox (6)  the removability test is RIGHT to keep these (the spine is
#                the only thing joining two collinear stubs at its single
#                junction), but that is a fact about the trunk-less topology,
#                which is never emitted.  The candidate as it stands carries
#                an antenna, so it is dropped on that ground.

def _corpus_pool(flow):
    """Every candidate of every bundle at the generation stage of `flow`."""
    sys.path.insert(0, str(_ROOT / "tools"))
    import topo_snapshot as ts
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        s = ts.run_flow_generation(flow)
    return s, [(w.input.original_bundle.id, t)
               for w in s.bundles for t in w.input.candidates]


def test_reannotation_clobbers_the_completed_annotation():
    """The OOB defect's MECHANISM, pinned so the gate cannot regress to it.

    complete_relay_junctions demotes a relay's duplicate face landing to
    nullopt on purpose (the single-tap model) — that demotion is what makes
    the junction a real SEG junction instead of a silent through-block relay.
    annotate_topology re-derives seg_busterms from geometry and puts the
    landing back; annotate_seg_conns derives only the junctions and leaves the
    annotation alone.  The seed-trunk gate must use the latter, or it judges a
    topology nobody will route.
    """
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 100)
    fp.add_block("B", 400, 200, 500, 300)
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(LAYER_H, LAYER_V)
    topo = next(c for c in g.generate_candidates("A", ["B"])
                if c.type.startswith("Z_HVH"))

    def taps(t):
        out = {}
        for i in range(len(t.segments)):
            e = t.seg_busterms.get(i)
            out[i] = (None if e is None else
                      (e[0].block_name if e[0] else None,
                       e[1].block_name if e[1] else None))
        return out

    before = taps(topo)
    buda.annotate_seg_conns(topo)
    assert taps(topo) == before, "annotate_seg_conns must not touch seg_busterms"

    # Demote a landing the way complete_relay_junctions does, then show the two
    # annotators disagree about it.
    seg, entry = next((i, e) for i, e in before.items()
                      if e is not None and (e[0] or e[1]))
    demoted = dict(topo.seg_busterms)
    del demoted[seg]
    topo.seg_busterms = demoted
    buda.annotate_seg_conns(topo)
    assert taps(topo)[seg] is None, "annotate_seg_conns re-added a demoted tap"
    buda.annotate_topology(topo, fp)
    assert taps(topo)[seg] == entry, (
        "annotate_topology no longer re-derives taps geometrically — if that is "
        "intentional, the seed-trunk gate's comment needs updating")


@pytest.mark.mid
@pytest.mark.parametrize("flow", ["demo/comprehensive_demo.buda",
                                  "flow/rnr/mix.buda",
                                  "flow/big_data_test/big.buda"])
def test_no_antenna_survives_generation(flow):
    """The issue's headline: zero antennas generated, corpus-wide.

    These three flows carried all 26 (demo 3, mix 8, big 15).  Asserted with
    the authoritative detector over EVERY candidate of EVERY bundle, not just
    the selected ones — a non-selected antenna is still a candidate the
    planner may pick under different congestion, which is why #485 exists.
    """
    s, pool = _corpus_pool(flow)
    offenders = [(bid, t.type) for bid, t in pool if _antenna_segs(t, s.fp)]
    assert not offenders, f"{len(offenders)} antenna candidate(s): {offenders[:8]}"


@pytest.mark.mid
def test_seed_trunk_families_are_still_generated():
    """Non-vacuity guard for the test above: the drop must not have emptied
    the hybrid families it polices.  Both the OOB and in-bbox TRUNK+MST
    shapes must still reach the pool — a gate that drops everything would
    also report zero antennas."""
    _, pool = _corpus_pool("flow/big_data_test/big.buda")
    types = {t.type.split("@")[0] for _, t in pool}
    assert "TRUNK_H+MST" in types or "TRUNK_V+MST" in types, types
    assert any(x.endswith("_OOB+MST") for x in types), (
        f"no OOB hybrid survived — the gate is over-dropping: {sorted(types)}")


# ── 3. tap-overhang antennas (issue #514) ────────────────────────────────────
#
# The attachment-count rule cannot see a dangling piece that ENDS on a face
# tap: the tap is an attachment, so the span terminates on a "valid" landing —
# but when the piece between the tap and the segment's nearest other
# attachment runs entirely over the tapped block's own footprint and the block
# stays covered without it, the piece is redundant metal (the repro: a
# spine-relay hub spine extended 35 units past its last junction to the hub's
# FAR face).  detect_antennas now flags that shape; the spine-relay completion
# no longer produces it (J-anchor: the spine stops at the outermost tap and
# the hub is covered by the crossing).

def _hub_fixture(overhang, hub_over_rects=None, hub_teg=None):
    """The issue-514 shape, flat.  An H spine runs westward over hub block H
    from east block C; two V stubs junction ON the spine inside the hub's
    footprint (the spine-relay 2-1 split).  overhang=True reproduces the
    retired M-anchor form — spine extended past the last junction (x=140) to
    the hub's FAR face (x=100) and tapped there; False is the J-anchor form
    (spine ends at the outermost junction, hub covered by the crossing).

    hub_over_rects declares HUB as a multi-rect block with those rects instead
    of the single bbox, `teg_mode over` unless hub_teg says otherwise — the
    scope controls for the limitation-2 companion fix (see the OVER tests
    below)."""
    fp = buda.Floorplan()
    if hub_over_rects:
        fp.add_block_rects("HUB", hub_over_rects)
        fp.set_block_teg_mode("HUB", hub_teg or buda.TegMode.OVER)
    else:
        fp.add_block("HUB", 100, 100, 300, 200)
    fp.add_block("B1", 120, 0, 160, 50)       # below; stub at x=140
    fp.add_block("B2", 160, 260, 200, 310)    # above; stub at x=180
    fp.add_block("C", 580, 100, 660, 200)     # east; spine terminates inside
    topo = buda.Topology()
    topo.type = "TEST_HUB_SPINE"
    spine_w = 100 if overhang else 140
    topo.segments = [
        _seg(spine_w, 150, 600, 150, LAYER_H),   # 0: the spine
        _seg(140, 50, 140, 150, LAYER_V),        # 1: stub to B1's top face
        _seg(180, 150, 180, 260, LAYER_V),       # 2: stub to B2's bottom face
    ]
    topo.connected_block_names = ["HUB", "B1", "B2", "C"]
    buda.annotate_topology(topo, fp)             # geometric taps (incl. HUB @100)
    buda.annotate_seg_conns(topo)                # junction records
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    return fp, topo, ct


def test_tap_overhang_antenna_is_flagged():
    """The retired M-anchor shape: the spine's [100,140] piece lies entirely
    over HUB, exists only to tap HUB's far face at 100, and HUB stays covered
    without it — a tap-overhang antenna at both placed stages."""
    fp, topo, ct = _hub_fixture(overhang=True)
    spine = ct.segs()[0]
    # Fixture preconditions: the tap really is at the far face, with the
    # nearest other attachment at 140, and attachments >= 2 (so the plain
    # count rule stays quiet — this is exactly the blind spot).
    assert any(c.kind == buda.SegConnKind.BUSTERM and c.block_name == "HUB"
               and c.at_pos == 100 for c in spine.conns), \
        [f"{c.kind} {c.block_name} {c.at_pos}" for c in spine.conns]
    res = buda.check_nuts(ct, _nominal_nuts(ct), topo, fp, _layers(), 1)
    ants = _antennas(res)
    assert len(ants) == 1, [v.message for v in res.violations]
    assert ants[0].seg_idx == 0
    assert ants[0].block_name == "HUB"
    assert "tap-overhang" in ants[0].message
    res_d = buda.check_dnuts(ct, _nominal_dnuts(ct, 2), topo, fp, _layers(), 1, 2)
    assert len(_antennas(res_d)) == 1


def test_tap_overhang_on_an_over_block_still_flagged_when_rects_keep_contact():
    """The SCOPE control for the limitation-2 companion fix (2026-08-27).

    `teg_mode over` makes the redundancy test per RECT rather than per BLOCK,
    because an OVER block's rects are not interchangeable — but that is a
    sharper question, not an exemption.  Here HUB is OVER with two abutting
    rects [100,200] and [200,300] and the overhang piece [100,140] lies inside
    the first: the trimmed remainder [140,600] still touches BOTH rects, so no
    rect loses its attachment and the antenna is still flagged, exactly as for
    the single-rect HUB above."""
    fp, topo, ct = _hub_fixture(
        overhang=True, hub_over_rects=[(100, 100, 200, 200),
                                       (200, 100, 300, 200)])
    res = buda.check_nuts(ct, _nominal_nuts(ct), topo, fp, _layers(), 1)
    ants = _antennas(res)
    assert len(ants) == 1, [v.message for v in res.violations]
    assert ants[0].block_name == "HUB"
    assert "tap-overhang" in ants[0].message


def test_tap_overhang_holding_an_over_rect_is_load_bearing():
    """The fix itself: same shape, but HUB is OVER with a second rect
    (100,120)-(139,180) lying under the overhang piece [100,140] and ending
    BEFORE the trim point, so that piece is the only metal touching it.  Under
    `over` the piece is that rect's own attachment, and calling it an antenna
    would demand the removal of the one wire holding it.  Block-level coverage
    cannot see the difference — HUB stays 'covered' either way, by the
    remainder over the big rect — which is why the test is per rect.  Fails on
    the pre-fix build (1 ANTENNA)."""
    fp, topo, ct = _hub_fixture(
        overhang=True, hub_over_rects=[(100, 100, 300, 200),
                                       (100, 120, 139, 180)])
    res = buda.check_nuts(ct, _nominal_nuts(ct), topo, fp, _layers(), 1)
    assert not _antennas(res), [v.message for v in res.violations]


def test_thru_multirect_keeps_the_block_level_overhang_verdict():
    """The other scope boundary: the per-rect redundancy test is gated on
    `teg_mode over`, which is the declaration that revokes block-level
    equivalence.  A THRU multi-rect block with the SAME geometry as the
    load-bearing case above declares its interior DOES join its rects, so the
    block-level verdict is the right one and the antenna is still flagged."""
    fp, topo, ct = _hub_fixture(
        overhang=True, hub_over_rects=[(100, 100, 300, 200),
                                       (100, 120, 139, 180)],
        hub_teg=buda.TegMode.THRU)
    res = buda.check_nuts(ct, _nominal_nuts(ct), topo, fp, _layers(), 1)
    ants = _antennas(res)
    assert len(ants) == 1, [v.message for v in res.violations]
    assert "tap-overhang" in ants[0].message


def test_j_anchor_spine_is_clean_and_covers_the_hub():
    """The replacement shape: spine trimmed to the outermost junction, no
    tap.  No antenna of either kind, and the hub is still covered (by the
    crossing) — no BUSTERM_OPEN."""
    fp, topo, ct = _hub_fixture(overhang=False)
    res = buda.check_nuts(ct, _nominal_nuts(ct), topo, fp, _layers(), 1)
    assert not _antennas(res), [v.message for v in res.violations]
    opens = [v for v in res.violations
             if v.kind == buda.ViolationKind.BUSTERM_OPEN]
    assert not opens, [v.message for v in opens]


def test_load_bearing_far_face_tap_is_not_flagged():
    """A far-face tap whose removal WOULD open the block is a real tap, not
    overhang: same spine shape but with no other coverage of the tapped block
    (stub junctions outside its footprint) — the redundancy clause must keep
    it."""
    fp = buda.Floorplan()
    fp.add_block("HUB", 100, 100, 180, 200)   # narrow: junctions lie EAST of it
    fp.add_block("B1", 300, 0, 340, 50)
    fp.add_block("C", 580, 100, 660, 200)
    topo = buda.Topology()
    topo.type = "TEST_HUB_TAP"
    topo.segments = [
        _seg(100, 150, 600, 150, LAYER_H),    # spine taps HUB's west face
        _seg(320, 50, 320, 150, LAYER_V),     # junction at x=320 (outside HUB)
    ]
    topo.connected_block_names = ["HUB", "B1", "C"]
    buda.annotate_topology(topo, fp)
    buda.annotate_seg_conns(topo)
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    # The piece [100,320] is NOT inside HUB (HUB ends at 180), so the inside
    # guard alone skips it; this pins the rule stays quiet on ordinary
    # outside-approach spans that happen to start on a face.
    res = buda.check_nuts(ct, _nominal_nuts(ct), topo, fp, _layers(), 1)
    assert not _antennas(res), [v.message for v in res.violations]


@pytest.mark.mid
def test_issue_514_repro_spine_has_no_overhang():
    """The reported vehicle end-to-end (mix.bdb clone bundle, spine_relays):
    the TRUNK_V+MST hub spine must stop at its outermost junction (x=1035)
    instead of extending to the hub's far face (x=1000), and the full
    pipeline pinned to that candidate must pass check_design clean."""
    rnr = _ROOT / "flow" / "rnr"
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        for c in ["def_layer 5 M5 V TOP 63.64",
                  "def_layer 6 M6 H TOP 63.64",
                  "def_track_pattern 5 0 VDD 2 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 "
                  "VSS 2 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1",
                  "def_track_pattern 6 0 VDD 2 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 "
                  "VSS 2 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1",
                  f"open_bdb {rnr / 'mix.bdb.sql'}",
                  "derive_busterms 2",
                  "add_blocks_from_bdb 0",
                  "add_blocks_from_bdb 1 skip",
                  "add_blocks_from_bdb 2 skip",
                  "bdb_net_mode on",
                  "add_bus clone[4] chip/i_dnuts1_0/u0 chip/i_dogleg2_0/top3,"
                  "chip/i_dnuts2_2/u4,chip/i_dogleg1_1/top2,"
                  "chip/i_dogleg2_2/top3,chip/i_dogleg2_3/bot2",
                  "run_hier_bundler depth 2",
                  "generate_topologies_for_hbundle 91 no_hanan_loci spine_relays"]:
            s.do_command(c)
    w = next(w for w in s.bundles if w.input.original_bundle.id == 91)
    spine_cands = [(k, t) for k, t in enumerate(w.input.candidates)
                   if t.type.startswith("TRUNK_V+MST@x1705")]
    assert spine_cands, [t.type for t in w.input.candidates]
    k, topo = spine_cands[0]
    # The hub block is (1000,1130)-(1200,1230); the H spine at y=1180 must
    # start at the outermost junction (1035), not the far face (1000).
    spine = next(sg for sg in topo.segments
                 if sg.start.y == 1180 and sg.end.y == 1180)
    assert min(spine.start.x, spine.end.x) == 1035, \
        f"spine spans [{spine.start.x},{spine.end.x}] — far-face overhang is back"
    with contextlib.redirect_stdout(out):
        for c in [f"select_topology 91 {k + 1}",
                  "run_planner hier signal_tracks",
                  "run_nuts",
                  "run_detailed_nuts",
                  "check_design"]:
            s.do_command(c)
    assert "Success: no violations found" in out.getvalue(), \
        out.getvalue()[-2000:]


def test_piece_covering_a_nested_block_is_not_flagged():
    """Codex #517: the terminal piece may be the ONLY pass-through coverage
    for a DIFFERENT connected block (nested/overlapping footprints) even
    though the tapped block stays covered after trimming — then the piece is
    load-bearing and must not be reported."""
    fp = buda.Floorplan()
    fp.add_block("HUB", 100, 100, 300, 200)
    fp.add_block("INNER", 105, 140, 130, 160)   # nested; only [100,140] covers it
    fp.add_block("B1", 120, 0, 160, 50)
    fp.add_block("B2", 160, 260, 200, 310)
    fp.add_block("C", 580, 100, 660, 200)
    topo = buda.Topology()
    topo.type = "TEST_HUB_NESTED"
    topo.segments = [
        _seg(100, 150, 600, 150, LAYER_H),      # spine taps HUB's far face
        _seg(140, 50, 140, 150, LAYER_V),
        _seg(180, 150, 180, 260, LAYER_V),
    ]
    topo.connected_block_names = ["HUB", "INNER", "B1", "B2", "C"]
    buda.annotate_topology(topo, fp)
    buda.annotate_seg_conns(topo)
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    # Preconditions: the spine's [100,140] piece is INNER's only coverage —
    # the trimmed remainder [140,600] misses INNER ([105,130]).
    res = buda.check_nuts(ct, _nominal_nuts(ct), topo, fp, _layers(), 1)
    assert not _antennas(res), [v.message for v in res.violations]


# ── 4. the audit reads PLACEMENT, not just nominal geometry ──────────────────
#
# teg_multirect_status.md limitation 0.  Both redundancy tests inside the
# tap-overhang rule ask "does something ELSE still hold this?", and both used
# to answer off the nominal ConnTopology alone — so a sibling that went
# UNPLACED still counted as metal, and a piece which is the only PLACED wire
# holding an OVER rect could be reported ANTENNA on the strength of a wire
# nobody built.  The audit now takes the placement at both stages and requires
# the verdict to hold in every world the placement defines (one at NUTS, one
# per BIT at DNUTS).  Placement is an EXISTENCE test only — a sibling that
# exists is still measured at its nominal extent, as the whole audit is.

def _limitation0_fixture():
    """The defect, constructed.

    HUB is `teg_mode over` with rects R0 (100,100)-(300,200) and R1
    (100,120)-(139,180).  The spine (seg 0) taps HUB's far west face at x=100
    and its nearest other attachment is the seg-1 junction at x=140, so the
    overhang piece is [100,140] — the ONLY part of the spine that touches R1
    (the trimmed remainder [140,600] misses it, R1 ending at 139).

    Seg 3 is an H sibling at y=170 running [110,250]: it crosses R1
    nominally, so the per-rect guard's `still` reads true, the piece is not
    judged load-bearing, and the block-level test then finds HUB covered by
    the remainder and reports ANTENNA.  Segs 4/5 are risers joining seg 3 to
    the spine so seg 3 is properly attached and the count rule stays quiet —
    the tap-overhang verdict is the only ANTENNA in play.
    """
    fp = buda.Floorplan()
    fp.add_block_rects("HUB", [(100, 100, 300, 200), (100, 120, 139, 180)])
    fp.set_block_teg_mode("HUB", buda.TegMode.OVER)
    fp.add_block("B1", 120, 0, 160, 50)
    fp.add_block("B2", 160, 260, 200, 310)
    fp.add_block("C", 580, 100, 660, 200)
    topo = buda.Topology()
    topo.type = "TEST_HUB_SPINE_L0"
    topo.segments = [
        _seg(100, 150, 600, 150, LAYER_H),   # 0: spine, taps HUB far face @100
        _seg(140, 50, 140, 150, LAYER_V),    # 1: stub to B1 — junction at 140
        _seg(180, 150, 180, 260, LAYER_V),   # 2: stub to B2
        _seg(110, 170, 250, 170, LAYER_H),   # 3: the sibling crossing R1
        _seg(200, 150, 200, 170, LAYER_V),   # 4: riser joining 0 and 3
        _seg(250, 150, 250, 170, LAYER_V),   # 5: riser joining 0 and 3
    ]
    topo.connected_block_names = ["HUB", "B1", "B2", "C"]
    buda.annotate_topology(topo, fp)
    buda.annotate_seg_conns(topo)
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    return fp, topo, ct


def _nuts_without(ct, unplaced, bundle_id=1):
    """`_nominal_nuts` with the named segments marked NOT placed — how an
    unplaced abstract segment really looks (NUTSResult carries the row with
    `placed` false; check_nuts's own idiom is `if (!ts.placed) continue`)."""
    nuts = _nominal_nuts(ct, bundle_id)
    for ts in nuts.segments:
        if ts.seg_idx in unplaced:
            ts.placed = False
    return nuts


def _dnuts_without(ct, num_bits, unplaced, bundle_id=1):
    """`_nominal_dnuts` with the named (seg, bit) wires MISSING.

    That is what an unplaced bit is at DetailedNUTS: the engine emits no
    NetSegment row for a bit it cannot place (nothing in detailed_nuts.cpp
    ever sets `placed` false), and check_dnuts raises its own UNPLACED off
    exactly that absence.
    """
    dnuts = _nominal_dnuts(ct, num_bits, bundle_id)
    dnuts.net_segments = [ns for ns in dnuts.net_segments
                          if (ns.seg_idx, ns.bit_index) not in unplaced]
    return dnuts


def test_limitation0_fixture_reproduces_the_shape_when_all_is_placed():
    """Non-vacuity + the byte-identity control in one: with everything placed
    the fixture reports exactly the ANTENNA it always did.  If this ever goes
    quiet the tests below prove nothing."""
    fp, topo, ct = _limitation0_fixture()
    res = buda.check_nuts(ct, _nominal_nuts(ct), topo, fp, _layers(), 1)
    ants = _antennas(res)
    assert len(ants) == 1, [v.message for v in res.violations]
    assert ants[0].seg_idx == 0 and ants[0].block_name == "HUB"
    assert "tap-overhang" in ants[0].message


def test_unplaced_sibling_no_longer_excuses_a_load_bearing_piece_at_nuts():
    """The fix at NUTS.  Seg 3 is unplaced, so the spine's [100,140] piece is
    the only PLACED metal touching R1 — removing it would open the rect, and
    calling it an antenna asks for exactly that.  Fails on the pre-change
    build (1 ANTENNA)."""
    fp, topo, ct = _limitation0_fixture()
    nuts = _nuts_without(ct, {3})
    # Precondition: with seg 3 gone, no other PLACED segment touches R1.
    r1 = (100, 120, 139, 180)
    others = [ts.seg_idx for ts in nuts.segments
              if ts.placed and ts.seg_idx != 0
              and _touches(ts.horiz, ts.track_position,
                           ts.span_lo, ts.span_hi, r1)]
    assert not others, f"fixture drifted: segs {others} still hold R1"
    res = buda.check_nuts(ct, nuts, topo, fp, _layers(), 1)
    assert not _antennas(res), [v.message for v in res.violations]


def test_unplaced_bit_no_longer_excuses_a_load_bearing_piece_at_dnuts():
    """The fix at DNUTS, where the question is per BIT.  Seg 3 has a wire for
    bit 0 and none for bit 1; in bit 1's world the piece is the only metal on
    R1, and the piece cannot be removed for one bit and kept for another — so
    the verdict must hold for every bit, and it does not.  Fails on the
    pre-change build (1 ANTENNA)."""
    fp, topo, ct = _limitation0_fixture()
    res = buda.check_dnuts(ct, _dnuts_without(ct, 2, {(3, 1)}),
                           topo, fp, _layers(), 1, 2)
    assert not _antennas(res), [v.message for v in res.violations]
    # The route is still reported dirty — by the audit that owns the fault.
    assert [v for v in res.violations
            if v.kind == buda.ViolationKind.UNPLACED], \
        "the unplaced bit itself must still be reported"


def test_fully_placed_dnuts_keeps_the_verdict():
    """The DNUTS byte-identity control: every bit placed collapses to ONE
    world, which is the nominal audit's, so the verdict is unchanged."""
    fp, topo, ct = _limitation0_fixture()
    res = buda.check_dnuts(ct, _nominal_dnuts(ct, 2), topo, fp, _layers(), 1, 2)
    ants = _antennas(res)
    assert len(ants) == 1 and "tap-overhang" in ants[0].message


def test_an_unplaced_piece_still_gets_the_nominal_verdict():
    """The fix only ever SUPPRESSES a verdict placement disproves; it must
    never manufacture one, and it must not go quiet where it has nothing to
    say.  With the judged segment itself unplaced it belongs to no world, so
    the audit keeps the nominal answer it has always given — and TEG_OPEN,
    which reads placed metal independently, reports the real open."""
    fp, topo, ct = _limitation0_fixture()
    res = buda.check_nuts(ct, _nuts_without(ct, {0, 3}), topo, fp, _layers(), 1)
    assert len(_antennas(res)) == 1, [v.message for v in res.violations]
    teg = [v for v in res.violations if v.kind == buda.ViolationKind.TEG_OPEN]
    assert teg and "rect#1" in teg[0].message, [v.message for v in res.violations]


def test_check_topo_still_runs_no_antenna_detector():
    """`detect_antennas` must stay out of check_topo, which feeds generation
    gates, dogleg trials and healer metrics — a reporting audit must not
    perturb them.  check_topo has no placement to read, so a placement-aware
    detector there would be meaningless as well as harmful."""
    fp, topo, ct = _limitation0_fixture()
    res = buda.check_topo(ct, topo, fp, 1)
    assert not _antennas(res), [v.message for v in res.violations]


def test_generation_predicate_takes_no_placement():
    """Generation has none to take.  `seg_attachment` is shared with the
    trunk+MST seed gate (`seed_trunk_is_antenna`) and `topology_is_clean_tree`
    in topology.cpp, so its signature is a generation contract; and
    `detect_antennas`, which now carries placement, is called from the two
    placed-stage checkers and nowhere else.  Pinned at the source so a later
    edit cannot quietly wire placement into the generator.
    """
    verify = (_ROOT / "src" / "verify.cpp").read_text()
    header = (_ROOT / "src" / "verify.h").read_text()
    decl = header[header.index("SegAttachment seg_attachment"):]
    decl = decl[:decl.index(";")]
    for banned in ("NUTSResult", "DetailedNUTSResult", "placed"):
        assert banned not in decl, \
            f"seg_attachment took on {banned!r} — generation cannot supply it"

    calls = [ln.strip() for ln in verify.splitlines()
             if "detect_antennas(" in ln and not ln.lstrip().startswith("//")
             and "static void" not in ln]
    assert len(calls) == 2, calls
    assert any('"nuts"' in c for c in calls) and any('"dnuts"' in c for c in calls)
    topo_body = verify[verify.index("ConnResult check_topo("):
                       verify.index("ConnResult check_nuts(")]
    assert "detect_antennas" not in topo_body, \
        "detect_antennas leaked into check_topo"


def _touches(horiz, perp, a_lo, a_hi, rect):
    """topology.h's `axis_touches_rect`, in Python, for fixture assertions."""
    x1, y1, x2, y2 = rect
    p1, p2 = (y1, y2) if horiz else (x1, x2)
    a1, a2 = (x1, x2) if horiz else (y1, y2)
    lo, hi = min(a_lo, a_hi), max(a_lo, a_hi)
    return p1 <= perp <= p2 and lo <= a2 and hi >= a1
