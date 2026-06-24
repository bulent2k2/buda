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
# A WIDE relay block R with MST neighbours on its LEFT and RIGHT faces at the
# same height: the chaining connector between the two opposite-face landings is a
# V,H,V JOG routed OVER THE CELL.  Its perpendicular bend must get a real,
# cell-bounded over-the-cell slide window -- never a zero-slide face pin.
WIDE_RELAY = ({"L": (0, 0, 100, 100), "R": (300, 0, 900, 100),
               "Rt": (1100, 0, 1200, 100), "T": (1100, 300, 1200, 400)},
              "L", ["R", "Rt", "T"])


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


def _seg_has_cycle(ct):
    """True if the SEG (wire-junction) graph has a cycle: a unique undirected SEG
    edge that re-closes an already-connected component.  A faithful routing tree
    must be acyclic -- a cycle is a redundant wire loop (e.g. duplicate connectors
    at a high-degree relay)."""
    segs = ct.segs()
    n = len(segs)
    uf = list(range(n))

    def find(x):
        while uf[x] != x:
            uf[x] = uf[uf[x]]
            x = uf[x]
        return x

    edges = set()
    for i, cs in enumerate(segs):
        for co in cs.conns:
            if co.kind == buda.SegConnKind.SEG:
                edges.add((min(i, co.seg_idx), max(i, co.seg_idx)))
    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra == rb:
            return True
        uf[ra] = rb
    return False


def test_completion_bridges_relay_block():
    """The staircase MST has a relay block (degree >= 2); completion bridges it so
    its wirelength is no longer understated.  The raw (un-completed) MST of this
    4-block staircase has 6 segments (3 diagonal L-edges).  Completion either ADDS
    a JOG connector or EXTENDS the incident stubs to meet over the cell (orthogonal
    relays) -- it never drops an MST edge -- so the candidate keeps >= 6 segments
    and is ONE physically connected component (no through-block relay)."""
    coords, src, dsts = STAIRCASE
    fp = _make_fp(coords)
    msts = _mst_cands(_gen(fp).generate_candidates(src, dsts))
    assert msts
    for c in msts:
        assert len(c.segments) >= 6, (
            f"{c.type}: completion dropped an MST edge, got {len(c.segments)}"
        )
        assert _num_geom_components(c.segments) == 1, (
            f"{c.type}: relay not bridged -- {_num_geom_components(c.segments)} "
            f"geometric components"
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
    detailed-NUTS see disconnected pieces and may slide them apart.  The completion
    must also be a TREE (acyclic): at a high-degree relay the landing-chaining can
    lay collinear connector wire on top of itself, closing a redundant loop -- the
    de-overlap pass drops those.  Also guards against zero-length connectors."""
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
            assert not _seg_has_cycle(ct), (
                f"{c.type} on {list(coords)}: completed MST has a wire cycle "
                f"(redundant overlapping connectors at a high-degree relay)"
            )


def test_high_degree_relay_has_no_overlapping_connectors():
    """Regression for the PLUS centre (a degree-4 relay): chaining its four MST
    edges used to lay one connector's return leg collinear on top of the next
    connector, an overlap that closed a cycle.  The de-overlap pass drops the
    contained connector, so no two segments are collinear-overlapping."""

    def _overlap(a, b):
        a_h, b_h = a.start.y == a.end.y, b.start.y == b.end.y
        if a_h != b_h:
            return False
        if a_h:
            if a.start.y != b.start.y:
                return False
            alo, ahi = sorted((a.start.x, a.end.x))
            blo, bhi = sorted((b.start.x, b.end.x))
        else:
            if a.start.x != b.start.x:
                return False
            alo, ahi = sorted((a.start.y, a.end.y))
            blo, bhi = sorted((b.start.y, b.end.y))
        return min(ahi, bhi) > max(alo, blo)   # share more than a single point

    coords, src, dsts = PLUS
    fp = _make_fp(coords)
    msts = _mst_cands(_gen(fp).generate_candidates(src, dsts))
    assert msts
    for c in msts:
        segs = c.segments
        for i in range(len(segs)):
            for j in range(i + 1, len(segs)):
                assert not _overlap(segs[i], segs[j]), (
                    f"{c.type}: segments {i} and {j} collinear-overlap "
                    f"(redundant connector wire not de-overlapped)"
                )


def test_contained_connector_kept_when_load_bearing():
    """The de-overlap pass must not drop a collinear-contained connector that is the
    only inferable SEG link to a busterm-annotated edge endpoint.  ConnTopology
    suppresses SEG inference at a busterm-tagged endpoint and does not infer
    collinear overlaps, so the covering segment cannot stand in for it -- dropping
    it would split the topology.  Regression for the reviewer's 4-block case: every
    completed MST candidate stays ONE SEG-connected component."""
    coords = {"A": (300, 600, 350, 650), "B": (400, 400, 450, 450),
              "C": (200, 400, 250, 450), "D": (700, 200, 750, 250)}
    fp = _make_fp(coords)
    msts = _mst_cands(_gen(fp).generate_candidates("A", ["B", "C", "D"]))
    assert msts
    for c in msts:
        ct = buda.ConnTopology()
        ct.build(c, fp)
        assert _seg_components(ct) == 1, (
            f"{c.type}: {_seg_components(ct)} SEG components -- a load-bearing "
            f"contained connector was wrongly de-overlapped"
        )
        assert not _seg_has_cycle(ct), f"{c.type}: wire cycle"


# ── OTC (over-the-cell) relay completion ──────────────────────────────────────

def test_relay_connectors_have_no_zero_slide_window():
    """A relay block that is NOT a feedthru chains its landings with a JOG /
    extension routed OVER THE CELL (OTC).  The tap connector gets a REAL slide
    window (the cell footprint) so NUTS has room to place a positive-width bus --
    never a degenerate zero-slide pin.  No ConnSeg of a relay candidate may have
    perp_lo == perp_hi."""
    for coords, src, dsts in (WIDE_RELAY, STAIRCASE, PLUS, GRID):
        fp = _make_fp(coords)
        cands = _gen(fp).generate_candidates(src, dsts)
        relayish = [c for c in cands if c.type.startswith("MST_") or "+MST" in c.type]
        assert relayish, f"expected MST/hybrid candidates for {list(coords)}"
        for c in relayish:
            ct = buda.ConnTopology()
            ct.build(c, fp)
            zero = [i for i, cs in enumerate(ct.segs())
                    if cs.perp_lo == cs.perp_hi]
            assert not zero, (
                f"{c.type} on {list(coords)}: zero-slide ConnSeg(s) {zero} "
                f"(relay connectors must get a real over-the-cell window)"
            )


def test_relay_tap_connector_bounded_to_cell():
    """The perpendicular connector sharing a relay tap's face endpoint is bounded
    to the tapped block's footprint: a FINITE window (it slides OVER the cell but
    never off it), not the unbounded default.  WIDE_RELAY's opposite-face relay
    exercises this directly."""
    SENT = 1 << 29
    coords, src, dsts = WIDE_RELAY
    fp = _make_fp(coords)
    cands = [c for c in _gen(fp).generate_candidates(src, dsts)
             if c.type.startswith("MST_") or "+MST" in c.type]
    assert cands
    for c in cands:
        ct = buda.ConnTopology()
        ct.build(c, fp)
        segs = ct.segs()
        taps = _busterm_taps(ct)
        for blk, tap_segs in taps.items():
            x1, y1, x2, y2 = coords[blk]
            for ti in tap_segs:
                tap = segs[ti]
                for co in tap.conns:
                    if co.kind != buda.SegConnKind.SEG:
                        continue
                    T = segs[co.seg_idx]
                    if T.horiz == tap.horiz:            # need a perpendicular bend
                        continue
                    lo, hi = (y1, y2) if T.horiz else (x1, x2)
                    if not (lo <= T.perp_pos <= hi):    # only the over-cell bend is bounded
                        continue
                    assert abs(T.perp_lo) < SENT and abs(T.perp_hi) < SENT, (
                        f"{c.type}: relay tap connector {co.seg_idx} unbounded "
                        f"[{T.perp_lo},{T.perp_hi}] (should be over-the-cell bounded)"
                    )
                    assert lo <= T.perp_lo and T.perp_hi <= hi, (
                        f"{c.type}: relay tap connector {co.seg_idx} window "
                        f"[{T.perp_lo},{T.perp_hi}] escapes cell [{lo},{hi}]"
                    )


def test_wide_relay_completion_is_clean():
    """The WIDE_RELAY opposite-face relay completes cleanly: no FEEDTHRU_RELAY,
    one SEG component, a single busterm tap per block, no zero-length segs."""
    coords, src, dsts = WIDE_RELAY
    fp = _make_fp(coords)
    cands = [c for c in _gen(fp).generate_candidates(src, dsts)
             if c.type.startswith("MST_") or "+MST" in c.type]
    assert cands
    for c in cands:
        zero = [(s.start.x, s.start.y) for s in c.segments
                if s.start.x == s.end.x and s.start.y == s.end.y]
        assert not zero, f"{c.type}: zero-length segs {zero}"
        ct = buda.ConnTopology()
        ct.build(c, fp)
        assert _feedthru_count(ct, c, fp) == 0, f"{c.type}: FEEDTHRU_RELAY"
        assert _seg_components(ct) == 1, f"{c.type}: {_seg_components(ct)} SEG comps"
        assert not _seg_has_cycle(ct), f"{c.type}: wire cycle"
        multi = {b: sorted(s) for b, s in _busterm_taps(ct).items() if len(s) > 1}
        assert not multi, f"{c.type}: double-tapped {multi}"


# ── orthogonal relay: OTC pass-through extension ──────────────────────────────

def test_orthogonal_relay_extends_without_L_connector():
    """A relay block touched by exactly two orthogonal stubs (one H, one V) is
    wired by EXTENDING both stubs over the cell to meet at the corner, not by an L
    connector.  So the block has NO segment endpoint on its face (no busterm tap)
    and is covered by the crossing wires (pass-through).  Regression for
    flow/big_data_test/big.buda bundle 3 / blk_19, where the L (one H + one V) was
    redundant.  This R block has a V stub from below and an H stub from the right
    -- orthogonal -- so completion must extend, leaving no tap and no FEEDTHRU."""
    coords = {"S": (0, 0, 100, 100), "R": (300, 300, 500, 500),
              "B": (350, 700, 450, 800), "E": (800, 350, 900, 450)}
    fp = _make_fp(coords)
    cands = _gen(fp).generate_candidates("S", ["R", "B", "E"])
    relayish = [c for c in cands if c.type.startswith("MST_") or "+MST" in c.type]
    assert relayish
    saw_extension = False
    for c in relayish:
        ct = buda.ConnTopology()
        ct.build(c, fp)
        assert _feedthru_count(ct, c, fp) == 0, f"{c.type}: FEEDTHRU_RELAY"
        assert _seg_components(ct) == 1, f"{c.type}: {_seg_components(ct)} SEG comps"
        # If R is wired purely by extension it holds no busterm tap (pass-through).
        if "R" not in _busterm_taps(ct):
            # verify R is still covered: some segment overlaps R's footprint.
            rx1, ry1, rx2, ry2 = coords["R"]
            covered = any(
                (s.start.y == s.end.y
                 and ry1 <= s.start.y <= ry2
                 and min(s.start.x, s.end.x) <= rx2 and max(s.start.x, s.end.x) >= rx1)
                or (s.start.x == s.end.x
                    and rx1 <= s.start.x <= rx2
                    and min(s.start.y, s.end.y) <= ry2 and max(s.start.y, s.end.y) >= ry1)
                for s in c.segments)
            assert covered, f"{c.type}: R untapped AND uncovered"
            saw_extension = True
    assert saw_extension, "no orthogonal-relay extension exercised — test is vacuous"


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


def test_trunk_mst_completed_no_feedthru():
    """trunk+MST hybrids are now COMPLETED (the redesign: each MST edge replaces a
    child block's trunk stub, so the hybrid is a cycle-free trunk-rooted tree that
    complete_relay_junctions wires up).  For single-rect designs every emitted
    +MST hybrid must therefore be clean: no FEEDTHRU_RELAY, one SEG-connected
    component, a single busterm tap per block, and no zero-length segments.  A
    hybrid that cannot be cleanly completed (a stub collinear with an incident MST
    edge) is dropped, not emitted -- so there is nothing left flagged here."""
    for coords, src, dsts in (STAIRCASE, PLUS, GRID):
        fp = _make_fp(coords)
        hybrids = [c for c in _gen(fp).generate_candidates(src, dsts)
                   if "+MST" in c.type]
        assert hybrids, f"expected trunk+MST hybrids for {list(coords)}"
        for c in hybrids:
            zero = [(s.start.x, s.start.y) for s in c.segments
                    if s.start.x == s.end.x and s.start.y == s.end.y]
            assert not zero, f"{c.type} on {list(coords)}: zero-length segs {zero}"
            ct = buda.ConnTopology()
            ct.build(c, fp)
            assert _feedthru_count(ct, c, fp) == 0, (
                f"{c.type} on {list(coords)}: still FEEDTHRU_RELAY-flagged after "
                f"trunk+MST completion"
            )
            assert _seg_components(ct) == 1, (
                f"{c.type} on {list(coords)}: {_seg_components(ct)} SEG components "
                f"(emitted but not cleanly completed)"
            )
            assert not _seg_has_cycle(ct), (
                f"{c.type} on {list(coords)}: emitted hybrid has a wire cycle "
                f"(should be a clean trunk-rooted tree or dropped)"
            )
            multi = {b: sorted(s) for b, s in _busterm_taps(ct).items() if len(s) > 1}
            assert not multi, f"{c.type} on {list(coords)}: blocks double-tapped: {multi}"


def test_trunk_mst_root_double_tap_demoted():
    """Regression for the single-tap demotion over ALL landings (not just distinct
    points): in a trunk+MST tree the root keeps its trunk stub AND is an MST-edge
    endpoint, so two segments leave it at the same corner.  Both must not stay
    tagged as a busterm -- exactly one keeps the tap, the other becomes a SEG
    junction.  Covered by the no-double-tap assertion across all hybrids above;
    this pins the STAIRCASE root case explicitly."""
    coords, src, dsts = STAIRCASE
    fp = _make_fp(coords)
    hybrids = [c for c in _gen(fp).generate_candidates(src, dsts) if "+MST" in c.type]
    assert hybrids
    for c in hybrids:
        ct = buda.ConnTopology()
        ct.build(c, fp)
        for blk, segs in _busterm_taps(ct).items():
            assert len(segs) == 1, f"{c.type}: block {blk} tapped by {sorted(segs)}"
