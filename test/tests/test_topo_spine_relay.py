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
"""Star→spine relay completion (`TopologyGenerator::set_spine_relays`).

At a degree-≥3 MST relay whose incident stubs split as ≥2 parallel (majority)
+ exactly 1 perpendicular (minority), `complete_relay_junctions` replaces the
over-the-cell bracket chain with a single collector SPINE that the majority
stubs T-tap independently.  This is the bigHalf b63/b0 "double-star" geometry
(each hub has two vertical leaf stubs + one horizontal backbone edge).

Opt-in: the generator's default leaves the completion untouched (the bracket
chain), so pools are bit-identical unless `set_spine_relays(True)` is called
(or `BUDA_SPINE_RELAYS=1` is set).
"""
import buda

# A minimal "double-star": two hubs (A, B) on one row joined by an edge, each
# with two leaf blocks above/below at DIFFERENT x so the two vertical stubs into
# a hub are on distinct columns -- the case where merging them into one spine
# would cost a slide DOF, and independent taps must not.
_COORDS = {
    "A":  (1000, 1000, 1400, 1200),   # hub, wide
    "B":  (3000, 1000, 3400, 1200),   # hub, same row
    "A1": (1000, 2000, 1100, 2100),   # above A, left column
    "A2": (1300,  100, 1400,  200),   # below A, right column
    "B1": (3000, 2000, 3100, 2100),   # above B, left column
    "B2": (3300,  100, 3400,  200),   # below B, right column
}
_DRV = "A"
_RCV = ["A1", "A2", "B", "B1", "B2"]


def _gen(spine):
    fp = buda.Floorplan()
    for name, (x1, y1, x2, y2) in _COORDS.items():
        fp.add_block(name, x1, y1, x2, y2)
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(6, 7)              # M6 H, M7 V
    # Set unconditionally so the test overrides any ambient BUDA_SPINE_RELAYS
    # (the constructor reads it) — the off-state must be genuinely off.
    g.set_spine_relays(spine)
    return fp, g.generate_candidates(_DRV, _RCV)


def _mst_hv(cands):
    return next((c for c in cands if c.type == "MST_HV"), None)


def _connectors(topo):
    # relay-completion connectors carry edge_id < 0; raw MST edge legs carry >= 0
    return sum(1 for s in topo.segments if s.edge_id < 0)


def _violations(topo, fp):
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    return [str(v.kind).split(".")[-1] for v in buda.check_topo(ct, topo, fp, 0).violations]


def _pinched(topo, fp):
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    return any(s.perp_lo == s.perp_hi for s in ct.segs())


def test_spine_off_is_the_bracket_chain_default():
    """Default (opt-out): the degree-3 relays are wired by the OTC bracket chain,
    so the MST carries relay connectors."""
    fp, cands = _gen(spine=False)
    m = _mst_hv(cands)
    assert m is not None, "expected an MST_HV candidate for the 6-block double-star"
    assert _connectors(m) > 0, "default completion should emit bracket connectors"


def test_spine_on_collapses_brackets_to_a_clean_spine():
    """With spine relays on, both hubs become collector spines: no relay
    connectors, fewer segments, still connected/clean and un-pinched."""
    fp_off, off = _gen(spine=False)
    fp_on, on = _gen(spine=True)
    m_off, m_on = _mst_hv(off), _mst_hv(on)
    assert m_on is not None
    assert _connectors(m_on) == 0, (
        f"spine completion should emit no bracket connectors; got {_connectors(m_on)}")
    assert len(m_on.segments) < len(m_off.segments), (
        f"spine MST should be smaller: on={len(m_on.segments)} off={len(m_off.segments)}")
    for bad in ("BUSTERM_OPEN", "FEEDTHRU_RELAY", "DISCONNECTED"):
        assert bad not in _violations(m_on, fp_on), _violations(m_on, fp_on)
    assert not _pinched(m_on, fp_on), "spine completion must not pinch a segment to zero slide"


def test_spine_taps_stay_on_independent_columns():
    """The two vertical stubs into hub A keep their OWN x (not merged onto one
    shared spine column) -- the slide-DOF-preserving property the design exists
    for.  A1 sits at x-column ~1050, A2 at ~1350."""
    fp, cands = _gen(spine=True)
    m = _mst_hv(cands)
    # vertical segments (start.x == end.x) tapping hub A's x-band [1000,1400]
    cols = sorted({s.start.x for s in m.segments
                   if s.start.x == s.end.x and 1000 <= s.start.x <= 1400})
    assert len(cols) >= 2, (
        f"the two vertical taps into hub A must keep independent columns; got {cols}")


# ── all-same-orientation hub (follow-up B) ───────────────────────────────────
# A wide hub with three leaves just ABOVE it, far apart in x, MST-stars at the hub
# with all three stubs vertical (landing on the hub's top face) -- the case with no
# perpendicular minority stub, so the spine adds a dedicated collector segment.
_ALLSAME = {
    "H":  (500, 1000, 3500, 1200),   # wide hub
    "L1": (500, 1300,  700, 1500),   # leaf above-left
    "L2": (1900, 1300, 2100, 1500),  # leaf above-middle
    "L3": (3300, 1300, 3500, 1500),  # leaf above-right
}


def _gen_allsame(spine):
    fp = buda.Floorplan()
    for name, (x1, y1, x2, y2) in _ALLSAME.items():
        fp.add_block(name, x1, y1, x2, y2)
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(6, 7)
    g.set_spine_relays(spine)
    return fp, g.generate_candidates("L1", ["L2", "L3", "H"])


def test_all_same_orientation_hub_gets_a_collector_spine():
    """When every incident stub shares one orientation there is no perpendicular
    stub to serve as the spine, so the completion ADDS a dedicated collector
    segment the stubs tap.  Result: fewer segments than the chain, still
    clean/un-pinched, and the three stubs keep independent columns."""
    fp_off, off = _gen_allsame(spine=False)
    fp_on, on = _gen_allsame(spine=True)
    m_off, m_on = _mst_hv(off), _mst_hv(on)
    assert m_on is not None and m_off is not None
    assert len(m_on.segments) < len(m_off.segments), (
        f"the collector spine should be smaller than the chain: "
        f"on={len(m_on.segments)} off={len(m_off.segments)}")
    for bad in ("BUSTERM_OPEN", "FEEDTHRU_RELAY", "DISCONNECTED", "SEG_OPEN"):
        assert bad not in _violations(m_on, fp_on), _violations(m_on, fp_on)
    assert not _pinched(m_on, fp_on)
    # the three vertical stubs keep their own columns (independent slide)
    cols = sorted({s.start.x for s in m_on.segments
                   if s.start.x == s.end.x and 500 <= s.start.x <= 3500})
    assert len(cols) >= 3, f"three independent vertical taps expected; got {cols}"
    # a horizontal collector segment now spans the tap range
    assert any(s.start.y == s.end.y and abs(s.end.x - s.start.x) > 1000
               for s in m_on.segments), "expected a horizontal collector spine"
