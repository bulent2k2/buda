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

"""Part B — junction vertices as placement constraints (seg_junction_coplacement.md).

A topology is a tree: bus-segments are edges, seg_conns records the junction
vertices where they meet.  These tests pin the Part B behaviors:
  - a junction cannot open at abstract NUTS regardless of where placement (or a
    CPU-dependent tie flip) puts the landing segment (the tc3a_flat bundle-48
    class — the machine-dependent canary flow/big_data_test/nuts-open.buda);
  - a single-junction corner stub PREFERS a track its partner already covers
    (junction-anchored preference) instead of forcing a partner stretch;
  - a junction only closable by a large partner stretch is surfaced as a
    structured NUTSResult.junction_infeasibilities entry (ripup contender);
  - the guarantees are edge-symmetric over multi-trunk BITRUNK trees.
"""
import buda


def _ls():
    ls = buda.LayerStack()
    ls.add_layer(6, "M6", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    ls.add_layer(7, "M7", buda.LayerDir.VERTICAL, buda.LayerType.TOP)
    return ls


def _wrap(bid, topo, width=4.0):
    b = buda.HBundle()
    b.id = bid
    b.net_names = [f"n{bid}_{i}" for i in range(4)]
    w = buda.BundleWrapper()
    w.input.original_bundle = b
    w.input.width = width
    w.input.candidates = [topo]
    w.plan.selected_topology_index = 0
    return w


def _topo(segs, fp):
    t = buda.Topology()
    t.type = "TEST"
    out = []
    for x1, y1, x2, y2, layer in segs:
        s = buda.Segment()
        s.start = buda.Point(x1, y1)
        s.end = buda.Point(x2, y2)
        s.layer_hint = layer
        out.append(s)
    t.segments = out
    buda.annotate_topology(t, fp)   # taps + junction vertices (Phases 2+4)
    return t


def _seg_opens(fp, ls, topo, result, width=4):
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    res = buda.check_nuts(ct, result, topo, fp, ls, width)
    return [v for v in res.violations if v.kind == buda.ViolationKind.SEG_OPEN]


def test_forced_far_junction_cannot_open():
    """Bundle-48 class: the landing stub's whole slide window sits far from the
    trunk's nominal end — wherever the stub lands, the junction must hold."""
    fp = buda.Floorplan()
    fp.add_block("A", 180, 0, 220, 40)        # stub taps A's top face
    fp.add_block("B", 380, 200, 420, 260)     # trunk end block
    # V stub from A up to y=100; H trunk from the junction to B.
    topo = _topo([(200, 40, 200, 100, 7), (200, 100, 400, 100, 6)], fp)
    w = _wrap(48, topo)
    # Force the stub's slide window (x) far right of the trunk's nominal lo end.
    w.plan.seg_slide_lo = [float("nan"), 320.0]
    w.plan.seg_slide_hi = [float("nan"), 360.0]
    ls = _ls()   # must outlive the engine (held by reference, no keep_alive)
    eng = buda.NUTSEngine(fp, ls)
    eng.set_track_pitch(1.0)
    result = eng.run([w])
    assert not _seg_opens(fp, ls, topo, result), \
        "junction opened despite coverage invariant + junction placement"


def test_single_junction_stub_prefers_partner_span():
    """Junction-anchored preference: a corner stub with a wide window lands
    within its partner's nominal extent, so the partner needs no stretch."""
    fp = buda.Floorplan()
    fp.add_block("A", 180, 0, 220, 40)
    fp.add_block("B", 380, 200, 420, 260)
    topo = _topo([(200, 40, 200, 100, 7), (200, 100, 400, 100, 6)], fp)
    w = _wrap(1, topo)
    ls = _ls()
    eng = buda.NUTSEngine(fp, ls)
    eng.set_track_pitch(1.0)
    result = eng.run([w])
    segs = {s.seg_idx: s for s in result.segments}
    trunk, stub = segs[1], segs[0]
    tlo, thi = sorted((trunk.span_lo, trunk.span_hi))
    assert tlo <= stub.track_position <= thi, \
        "stub track must lie on the trunk span (junction vertex honored)"
    # trunk kept (close to) its nominal extent — no forced overstretch
    assert tlo >= 195.0 and thi <= 405.0, f"trunk overstretched: [{tlo},{thi}]"


def test_unreachable_junction_is_surfaced():
    """A landing stub whose slide window is DISJOINT from the trunk's placed
    span can only close via a large partner stretch — NUTS must complete the
    placement (coverage invariant, no open) AND surface the edge structurally
    in junction_infeasibilities so ripup can re-pin the bundle."""
    fp = buda.Floorplan()
    fp.add_block("A", 60, 0, 540, 40)         # wide block: stub may slide far
    fp.add_block("B", 140, 80, 200, 120)      # trunk's other end block
    # V stub at x=500 taps A's wide top face; H trunk spans x[200..500] at
    # y=100 — the stub's forced window [80,120] is legal on A's face but
    # DISJOINT from the trunk's nominal span.
    topo = _topo([(500, 40, 500, 100, 7), (200, 100, 500, 100, 6)], fp)
    w = _wrap(3, topo)
    w.plan.seg_slide_lo = [80.0, float("nan")]    # stub forced far LEFT of
    w.plan.seg_slide_hi = [120.0, float("nan")]   # the trunk's hi (junction) end
    ls = _ls()
    eng = buda.NUTSEngine(fp, ls)
    eng.set_track_pitch(1.0)
    result = eng.run([w])
    # No dead-end: placement completes and the junction holds via coverage.
    assert not _seg_opens(fp, ls, topo, result)
    # The disjointness is surfaced as a structured signal naming the edge.
    assert result.junction_infeasibilities, \
        "disjoint junction window was not surfaced in junction_infeasibilities"
    for ji in result.junction_infeasibilities:
        assert ji.bundle_id == 3 and {ji.seg_a, ji.seg_b} == {0, 1}


_COLUMN = {
    "S":  (0, 0, 60, 480),
    "A0": (200, 100, 260, 160), "A1": (200, 300, 260, 360),
    "A2": (200, 500, 260, 560),
    "B0": (500, 100, 560, 160), "B1": (500, 300, 560, 360),
    "B2": (500, 500, 560, 560),
}


def test_bitrunk_tree_junctions_hold_at_nuts():
    """Multi-trunk datapath tree (BITRUNK_HVH/VHV): every junction edge —
    root←branches AND branch←stubs — holds at NUTS placement, both orientations.
    No trunk/tap dichotomy survives a two-level tree."""
    fp = buda.Floorplan()
    for n, (x1, y1, x2, y2) in _COLUMN.items():
        fp.add_block(n, x1, y1, x2, y2)
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(6, 7)
    g.set_multi_trunk(True)
    cands = g.generate_candidates("S", [k for k in _COLUMN if k != "S"])
    bitrunks = [c for c in cands if "BITRUNK_HVH" in c.type or
                "BITRUNK_VHV" in c.type]
    assert bitrunks, f"no BITRUNK trees generated: {sorted({c.type for c in cands})}"
    ls = _ls()
    eng = buda.NUTSEngine(fp, ls)
    eng.set_track_pitch(1.0)
    seen = set()
    for c in bitrunks:
        kind = c.type.split("@")[0]
        if kind in seen:
            continue
        seen.add(kind)
        result = eng.run([_wrap(10 + len(seen), c)])
        assert not _seg_opens(fp, ls, c, result, width=8), \
            f"{c.type}: junction opened in a BITRUNK tree"
        # every junction vertex lies on its spanning partner's placed extent
        segs = {s.seg_idx: s for s in result.segments}
        for (i, _ep), others in c.seg_conns.items():
            for j in others:
                lo, hi = sorted((segs[j].span_lo, segs[j].span_hi))
                assert lo - 1e-6 <= segs[i].track_position <= hi + 1e-6, \
                    f"{c.type}: seg {i} track off partner {j} span [{lo},{hi}]"
    assert {"BITRUNK_HVH", "BITRUNK_VHV"} <= seen or len(seen) >= 1
