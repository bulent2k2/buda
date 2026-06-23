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
MST topology completion (feedthrough fix).

Standalone MST (MST_HV / MST_VH) edges each land independently on their two
blocks' nearest faces, so a block with MST degree >= 2 used to be touched by
two segments at different points that were "connected" only THROUGH the block
(a feedthrough relay).  complete_relay_junctions adds dogleg / stretch wire so
every such junction is physically connected within the topology, which also
makes the wirelength honest.

The verifier's FEEDTHRU_RELAY check (check_topo) flags any single-rect block
whose connected segments' wires do not actually touch.
"""
from collections import defaultdict

import buda


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_fp(coords):
    fp = buda.Floorplan()
    for name, (x1, y1, x2, y2) in coords.items():
        fp.add_block(name, x1, y1, x2, y2)
    return fp


def _gen(fp):
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(4, 5)  # h=M4, v=M5
    return g


def _mst_cands(cands):
    return [c for c in cands if c.type.startswith("MST_")]


def _feedthru_count(ct, topo, fp):
    res = buda.check_topo(ct, topo, fp, 0)
    return sum(1 for v in res.violations
               if v.kind == buda.ViolationKind.FEEDTHRU_RELAY)


def _violations(ct, topo, fp):
    return [str(v.kind).split(".")[-1]
            for v in buda.check_topo(ct, topo, fp, 0).violations]


def _seg_endpoints(s):
    return (s.start.x, s.start.y), (s.end.x, s.end.y)


def _pt_on_seg(px, py, s):
    horiz = (s.start.y == s.end.y)
    if horiz:
        return (py == s.start.y
                and min(s.start.x, s.end.x) <= px <= max(s.start.x, s.end.x))
    return (px == s.start.x
            and min(s.start.y, s.end.y) <= py <= max(s.start.y, s.end.y))


def _num_geom_components(segs):
    """Geometric connected components: two segments are connected if their wires
    share a point or form a T-junction (touch anywhere)."""
    n = len(segs)
    uf = list(range(n))

    def find(x):
        while uf[x] != x:
            uf[x] = uf[uf[x]]
            x = uf[x]
        return x

    def touch(a, b):
        for (px, py) in _seg_endpoints(a):
            if _pt_on_seg(px, py, b):
                return True
        for (px, py) in _seg_endpoints(b):
            if _pt_on_seg(px, py, a):
                return True
        return False

    for i in range(n):
        for j in range(i + 1, n):
            if touch(segs[i], segs[j]):
                uf[find(i)] = find(j)
    return len({find(i) for i in range(n)})


# Arrangements that force MST relay (degree >= 2) blocks.
STAIRCASE = ({"A": (0, 0, 100, 100), "B": (200, 150, 300, 250),
              "C": (400, 300, 500, 400), "D": (600, 450, 700, 550)},
             "A", ["B", "C", "D"])
PLUS = ({"C": (250, 250, 350, 350), "N": (250, 500, 350, 600),
         "S": (250, 0, 350, 100), "E": (500, 250, 600, 350),
         "W": (0, 250, 100, 350)},
        "C", ["N", "S", "E", "W"])
GRID = ({"A": (0, 0, 100, 100), "B": (300, 0, 400, 100),
         "C": (0, 300, 100, 400), "D": (300, 300, 400, 400),
         "E": (600, 150, 700, 250)},
        "A", ["B", "C", "D", "E"])


# ── completion correctness ────────────────────────────────────────────────────

def test_mst_candidates_have_no_feedthru_relay():
    """Every generated standalone-MST candidate is free of feedthrough relays."""
    for coords, src, dsts in (STAIRCASE, PLUS, GRID):
        fp = _make_fp(coords)
        cands = _gen(fp).generate_candidates(src, dsts)
        msts = _mst_cands(cands)
        assert msts, f"expected MST candidates for {list(coords)}"
        for c in msts:
            ct = buda.ConnTopology()
            ct.build(c, fp)
            assert _violations(ct, c, fp) == [], (
                f"{c.type} on {list(coords)} has violations: {_violations(ct, c, fp)}"
            )


def test_mst_topology_is_single_geometric_component():
    """After completion an MST topology's wires form ONE connected component
    (no piece relies on a block to bridge it)."""
    for coords, src, dsts in (STAIRCASE, PLUS, GRID):
        fp = _make_fp(coords)
        for c in _mst_cands(_gen(fp).generate_candidates(src, dsts)):
            assert _num_geom_components(c.segments) == 1, (
                f"{c.type} on {list(coords)} is not physically connected: "
                f"{_num_geom_components(c.segments)} components"
            )


def _busterm_taps(ct):
    """block_name -> set of segment indices that BUSTERM-tap it."""
    taps = defaultdict(set)
    for i, cs in enumerate(ct.segs()):
        for co in cs.conns:
            if co.kind == buda.SegConnKind.BUSTERM:
                taps[co.block_name].add(i)
    return taps


def _seg_components(ct):
    """Number of connected components using ONLY SEG (wire-junction) connections.
    This is the connectivity the downstream stages (NUTS / detailed-NUTS) see: a
    component bridged only through a block's busterm is NOT one wire."""
    segs = ct.segs()
    n = len(segs)
    uf = list(range(n))

    def find(x):
        while uf[x] != x:
            uf[x] = uf[uf[x]]
            x = uf[x]
        return x

    for i, cs in enumerate(segs):
        for co in cs.conns:
            if co.kind == buda.SegConnKind.SEG:
                uf[find(i)] = find(co.seg_idx)
    return len({find(i) for i in range(n)})


def test_completion_adds_wire_for_relay_block():
    """The staircase MST has a relay block (degree >= 2) and completion adds the
    bridging wire.  The raw (un-completed) MST of this 4-block staircase has
    exactly 6 segments (3 diagonal L-edges); completion adds connectors so the
    candidate carries strictly more, and its wirelength is no longer understated."""
    coords, src, dsts = STAIRCASE
    fp = _make_fp(coords)
    msts = _mst_cands(_gen(fp).generate_candidates(src, dsts))
    assert msts
    for c in msts:
        assert len(c.segments) > 6, (
            f"{c.type}: expected completion connectors beyond the 6 raw edge "
            f"segments, got {len(c.segments)}"
        )


def test_relay_keeps_single_busterm_tap():
    """Single-tap model: after completion every block is tapped by AT MOST one
    segment.  A relay block used to be tapped by >= 2 segments (the through-block
    feedthrough); now exactly one stub keeps the busterm and the rest are wired
    as SEG junctions."""
    for coords, src, dsts in (STAIRCASE, PLUS, GRID):
        fp = _make_fp(coords)
        for c in _mst_cands(_gen(fp).generate_candidates(src, dsts)):
            ct = buda.ConnTopology()
            ct.build(c, fp)
            taps = _busterm_taps(ct)
            multi = {b: sorted(s) for b, s in taps.items() if len(s) > 1}
            assert not multi, (
                f"{c.type} on {list(coords)}: blocks with >1 busterm tap "
                f"(feedthrough not removed): {multi}"
            )


def test_completion_seg_connected_downstream():
    """The real fix for the reviewer's concern: the completed MST must be ONE
    component under SEG (wire-junction) connectivity alone -- not merely touching
    geometrically, and not bridged through a block's busterm.  Otherwise NUTS /
    detailed-NUTS see disconnected pieces and may slide them apart.  Also guards
    against degenerate zero-length connector segments."""
    for coords, src, dsts in (STAIRCASE, PLUS, GRID):
        fp = _make_fp(coords)
        for c in _mst_cands(_gen(fp).generate_candidates(src, dsts)):
            zero = [(s.start.x, s.start.y) for s in c.segments
                    if s.start.x == s.end.x and s.start.y == s.end.y]
            assert not zero, f"{c.type} on {list(coords)}: zero-length segs {zero}"
            ct = buda.ConnTopology()
            ct.build(c, fp)
            nc = _seg_components(ct)
            assert nc == 1, (
                f"{c.type} on {list(coords)}: SEG-connectivity has {nc} components "
                f"(connectors modelled as busterm taps, not wire junctions)"
            )


# ── verifier safety-net (FEEDTHRU_RELAY) ──────────────────────────────────────

def _seg(x1, y1, x2, y2, layer):
    s = buda.Segment()
    s.start = buda.Point(x1, y1)
    s.end = buda.Point(x2, y2)
    s.layer_hint = layer
    return s


def test_feedthru_relay_detected_on_raw_relay():
    """A hand-built relay (two L-edges meeting block B at DIFFERENT face points,
    wires not touching) is flagged FEEDTHRU_RELAY."""
    fp = _make_fp({"A": (0, 0, 100, 100), "B": (200, 150, 300, 250),
                   "C": (400, 300, 500, 400)})
    t = buda.Topology()
    t.type = "RAW_RELAY"
    t.segments = [_seg(100, 100, 200, 100, 4), _seg(200, 100, 200, 150, 5),   # A->B
                  _seg(300, 250, 400, 250, 4), _seg(400, 250, 400, 300, 5)]   # B->C
    t.connected_block_names = ["A", "B", "C"]
    ct = buda.ConnTopology()
    ct.build(t, fp)
    res = buda.check_topo(ct, t, fp, 0)
    ft = [v for v in res.violations
          if v.kind == buda.ViolationKind.FEEDTHRU_RELAY]
    assert len(ft) == 1 and ft[0].block_name == "B", (
        f"expected one FEEDTHRU_RELAY on B, got "
        f"{[(str(v.kind), v.block_name) for v in res.violations]}"
    )


def test_straight_trunk_through_block_not_flagged():
    """A straight trunk crossing a block is one continuous wire (a pass-through),
    NOT a feedthru -- it must not be flagged FEEDTHRU_RELAY.  B is a tall
    receiver straddling the H-trunk row, so the trunk passes through it."""
    fp = _make_fp({"A": (0, 100, 100, 200), "B": (200, 0, 300, 300),
                   "C": (400, 100, 500, 200)})
    cands = _gen(fp).generate_candidates("A", ["B", "C"])
    assert cands, "expected candidates"
    # At least one candidate must actually pass a trunk through a block.
    assert any(c.pass_through_count >= 1 for c in cands), (
        f"no pass-through candidate generated: {[c.type for c in cands]}"
    )
    # Plain trunk / L / Z / U candidates (everything except the deferred trunk+MST
    # hybrid) must NOT be flagged as feedthru relays -- a trunk crossing a block is
    # a continuous wire, not a relay.
    for c in cands:
        if "+MST" in c.type:
            continue
        ct = buda.ConnTopology()
        ct.build(c, fp)
        assert _feedthru_count(ct, c, fp) == 0, (
            f"candidate {c.type} (pass_through={c.pass_through_count}) wrongly "
            f"flagged as feedthru relay"
        )


def test_trunk_mst_relay_currently_flagged_deferred():
    """trunk+MST hybrids are NOT completed yet (their MST edges are redundant with
    the trunk spine; completing would create cycles -- redesign deferred).  Until
    then the verifier correctly flags their feedthrough relays.  This test pins
    that current behavior; update it when trunk+MST completion lands."""
    coords, src, dsts = STAIRCASE
    fp = _make_fp(coords)
    cands = _gen(fp).generate_candidates(src, dsts)
    mst_hybrids = [c for c in cands if "+MST" in c.type]
    if not mst_hybrids:
        import pytest
        pytest.skip("no trunk+MST candidate for this geometry")
    flagged = 0
    for c in mst_hybrids:
        ct = buda.ConnTopology()
        ct.build(c, fp)
        if _feedthru_count(ct, c, fp) > 0:
            flagged += 1
    assert flagged > 0, "expected at least one trunk+MST hybrid to be flagged"
