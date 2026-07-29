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
