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

"""Topo-truth Phase 4 — seg_conns is the ONE source of seg-to-seg connectivity.

`Topology.seg_conns` maps (seg_idx, endpoint 0=start/1=end) -> the segments that
endpoint lands on.  It is derived ONCE (generation post-pass / annotate_seg_conns
/ apply_dogleg after a split); `ConnTopology::infer_connections` READS it and no
longer scans the geometry for touching segments.  An endpoint with no record is
a free end or a busterm tap — never a cue to go guessing.

See docs/internal/single_source_topo_truth.md (Phase 4).
"""
import buda


# ── generation coverage ────────────────────────────────────────────────────────

def _gen(blocks, src, dsts, multi_trunk=False):
    fp = buda.Floorplan()
    for n, (x1, y1, x2, y2) in blocks.items():
        fp.add_block(n, x1, y1, x2, y2)
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(6, 7)
    if multi_trunk:
        g.set_multi_trunk(True)
    return fp, g.generate_candidates(src, dsts)


_TWO = {"A": (0, 0, 100, 100), "B": (400, 300, 500, 400)}
_COLUMN = {
    "S":  (0, 0, 60, 480),
    "A0": (200, 100, 260, 160), "A1": (200, 300, 260, 360),
    "A2": (200, 500, 260, 560),
    "B0": (500, 100, 560, 160), "B1": (500, 300, 560, 360),
    "B2": (500, 500, 560, 560),
}


def _junction_on(P, seg):
    """P lies on seg (inclusive) — the invariant every record must satisfy."""
    x1, x2 = sorted((seg.start.x, seg.end.x))
    y1, y2 = sorted((seg.start.y, seg.end.y))
    return x1 <= P.x <= x2 and y1 <= P.y <= y2


def _assert_records_valid(topo):
    """Every seg_conns record must name a real segment the endpoint actually
    touches, and never a tapped endpoint (which is a busterm, not a junction)."""
    for (i, ep), others in topo.seg_conns.items():
        assert 0 <= i < len(topo.segments), f"stale seg index {i}"
        P = topo.segments[i].start if ep == 0 else topo.segments[i].end
        bt = topo.seg_busterms.get(i)
        if bt is not None:
            tapped = bt[0] if ep == 0 else bt[1]
            assert tapped is None, \
                f"seg {i} ep {ep} is a busterm tap AND a junction record"
        assert others == sorted(set(others)), f"unsorted/dup record at ({i},{ep})"
        for j in others:
            assert 0 <= j < len(topo.segments), f"stale other index {j}"
            assert _junction_on(P, topo.segments[j]), \
                f"record ({i},{ep})->{j} does not touch segment {j}"


def test_generated_2pin_candidates_carry_seg_conns():
    """Every multi-segment 2-pin candidate (L/Z/U/UU) records its bends."""
    _fp, cands = _gen(_TWO, "A", ["B"])
    assert cands
    for c in cands:
        if len(c.segments) >= 2:
            assert c.seg_conns, f"{c.type} has no seg_conns"
        _assert_records_valid(c)


def test_generated_npin_candidates_carry_seg_conns():
    """Trunk / MST / BITRUNK candidates all record their stub↔trunk junctions."""
    _fp, cands = _gen(_COLUMN, "S", list(_COLUMN.keys() - {"S"}), multi_trunk=True)
    assert cands
    seen_multi = False
    for c in cands:
        if len(c.segments) >= 2:
            assert c.seg_conns, f"{c.type} has no seg_conns"
            seen_multi = True
        _assert_records_valid(c)
    assert seen_multi


def test_unannotated_topology_has_no_seg_junctions():
    """Phase 4: the geometric touching-segment scan is retired — a hand-built
    topology with no seg_conns yields NO SEG connections; annotate_topology
    (which now derives both kinds) restores them."""
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 100)
    fp.add_block("B", 400, 300, 500, 400)
    h = buda.Segment(); h.start = buda.Point(100, 50); h.end = buda.Point(450, 50)
    v = buda.Segment(); v.start = buda.Point(450, 50); v.end = buda.Point(450, 300)
    t = buda.Topology(); t.type = "L_HV"; t.segments = [h, v]

    def seg_edges(topo):
        ct = buda.ConnTopology(); ct.build(topo, fp)
        return sorted({(min(i, cc.seg_idx), max(i, cc.seg_idx))
                       for i, cs in enumerate(ct.segs()) for cc in cs.conns
                       if cc.kind == buda.SegConnKind.SEG})

    assert seg_edges(t) == [], \
        "unannotated topology must have no SEG junctions (scan retired)"

    buda.annotate_topology(t, fp)
    assert t.seg_conns, "annotate_topology must derive seg_conns"
    assert seg_edges(t) == [(0, 1)], "the L bend junction must be restored"


def test_annotate_skips_tapped_endpoints():
    """A busterm-tapped endpoint gets no junction record even when it touches a
    perpendicular segment (mirrors infer_connections' tap short-circuit)."""
    fp = buda.Floorplan()
    fp.add_block("own", 180, 0, 220, 40)
    # Stub start taps 'own' top face; a trunk crosses exactly through that face
    # coordinate — the tapped endpoint must NOT also become a junction.
    trunk = buda.Segment(); trunk.start = buda.Point(0, 40); trunk.end = buda.Point(400, 40)
    stub = buda.Segment(); stub.start = buda.Point(200, 40); stub.end = buda.Point(200, 200)
    t = buda.Topology(); t.type = "T"; t.segments = [trunk, stub]
    bt = buda.Busterm(); bt.block_name = "own"; bt.bbox = buda.Rect(180, 0, 220, 40)
    t.seg_busterms = {1: (bt, None)}
    buda.annotate_seg_conns(t)
    assert (1, 0) not in t.seg_conns, "tapped stub start must not be a junction"
    # The trunk's coverage of that point is unaffected; the stub's far end is free.
    _assert_records_valid(t)


def test_multi_join_endpoint_records_all_segments():
    """3+ segments meeting at one point: the endpoint's record lists them all
    (this is why persistence needs other_seg in the PK)."""
    # Two horizontal segments end-to-end at (200,100), one vertical through it.
    h1 = buda.Segment(); h1.start = buda.Point(0, 100);   h1.end = buda.Point(200, 100)
    v  = buda.Segment(); v.start  = buda.Point(200, 0);   v.end  = buda.Point(200, 300)
    h2 = buda.Segment(); h2.start = buda.Point(200, 100); h2.end = buda.Point(400, 100)
    t = buda.Topology(); t.type = "X"; t.segments = [h1, v, h2]
    buda.annotate_seg_conns(t)
    # h1's end lands on v (perpendicular-only: h2 is parallel, never recorded).
    assert t.seg_conns.get((0, 1)) == [1]
    assert t.seg_conns.get((2, 0)) == [1]
    # v's interior hosts both; v's own endpoints touch nothing.
    assert (1, 0) not in t.seg_conns and (1, 1) not in t.seg_conns


def test_offset_topology_carries_seg_conns():
    """The hier per-instance offset must preserve the junction records (they are
    index-only, so a coordinate shift cannot invalidate them)."""
    _fp, cands = _gen(_TWO, "A", ["B"])
    multi = [c for c in cands if len(c.segments) >= 2]
    assert multi
    src = multi[0]
    off = buda.offset_topology(src, 1000, 500, "inst")
    assert dict(off.seg_conns) == dict(src.seg_conns)
    _assert_records_valid(off)


def test_dogleg_split_rederives_seg_conns():
    """After NUTS splits a trunk (dogleg), the adopted topology's seg_conns must
    describe the post-split structure: the jog joins both trunk pieces, and every
    stub reaches a piece — validated by the same touch invariant."""
    import os
    import buda_cli
    flow = os.path.join(os.path.dirname(__file__), '..', '..',
                        'flow', 'dogleg1.buda')
    sess = buda_cli.BudaSession()
    sess.no_viz = True
    sess.do_command(f"source {os.path.abspath(flow)}")
    doglegged = [w for w in sess.bundles
                 if any(s.is_jog for s in
                        w.input.candidates[w.plan.selected_topology_index].segments)]
    assert doglegged, "dogleg1.buda must produce at least one dogleg split"
    for w in doglegged:
        topo = w.input.candidates[w.plan.selected_topology_index]
        _assert_records_valid(topo)
        # the appended jog participates in at least one junction record
        jog_idx = [i for i, s in enumerate(topo.segments) if s.is_jog]
        joined = {j for others in topo.seg_conns.values() for j in others}
        joined |= {i for (i, _ep) in topo.seg_conns.keys()}
        assert any(j in joined for j in jog_idx), \
            "jog must appear in the post-split junction records"
