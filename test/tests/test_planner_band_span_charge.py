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
`band_span_charge` — width-aware band charging (issue #518).

The planner charges a segment's whole bus width into the SINGLE Hanan band
holding the segment's perp.  A bus wider than that band therefore overflows
**by construction — on a completely empty layer**, because eff_width > cap
before any other bundle is charged.  #516's repro hit exactly this: the only
planned bundle in the design reported overflow while NUTS/DNUTS placed it
cleanly, since the metal simply spans several bands (Hanan bands are
accounting buckets from block edges, not physical barriers).

The knob spreads the charge over the bands the footprint actually covers,
weighted by each band's share.

**Mode 1 is now the DEFAULT** (oversized-only + proportional): spread only a
bus too wide for its own band — exactly the mis-charged case — and leave every
bus that already fits on the legacy path.  Measured 3 better / 1 worse on the
29-flow corpus at +7.1% runtime, with two flows going from broken to fully
clean; the one regression (rnr/mix2) is documented and resisted three separate
repair attempts.  `band_span_charge 0` is the escape hatch back to the legacy
single-band charge.  See docs/internal/band_span_charge.md.
"""
import pytest
import buda


def _one_band_design(bus_width, band_h=10, chan=200, seg_x=55):
    """A channel finely subdivided by blocks, so Hanan bands are ~band_h tall.

    The stack is a single V TOP layer, so a vertical bus through the channel
    is charged on H-cuts against bands taken from the x-grid... which the
    block edges below subdivide.  `bus_width` is what makes the bus wider or
    narrower than one band.
    """
    fp = buda.Floorplan()
    # Ladder of blocks whose edges lay down a fine Hanan grid along x.
    x = 0
    for i in range(6):
        fp.add_block(f"b{i}", x, 0, x + band_h, chan)
        x += 2 * band_h

    ls = buda.LayerStack()
    ls.add_layer(3, "M3", buda.LayerDir.VERTICAL,   buda.LayerType.TOP)
    ls.add_layer(4, "M4", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)

    seg = buda.Segment()
    seg.start = buda.Point(seg_x, 0)
    seg.end   = buda.Point(seg_x, chan)
    seg.layer_hint = 3

    topo = buda.Topology()
    topo.type = "TEST_V"
    topo.segments = [seg]

    b = buda.HBundle()
    b.id = 1

    w = buda.BundleWrapper()
    w.input.original_bundle = b
    w.input.width = bus_width
    w.input.candidates = [topo]
    w.plan.selected_topology_index = 0
    return fp, ls, w


def _plan(fp, ls, w, knob):
    p = buda.CongestionPlanner(fp, ls)
    if knob is not None:
        p.set_planner_param("band_span_charge", knob)
    p.build_congestion_map()
    with buda.ostream_redirect():
        asn = p.optimize_topologies([w], 1)
    return asn


# ── The off-path guarantee ────────────────────────────────────────────────────

def _key(assignments):
    return [(a.bundle_id, a.topo_index, list(a.seg_layers)) for a in assignments]


def test_default_is_mode_1():
    """The compiled default is mode 1, not off.

    Pins the default-flip itself: leaving the knob unset must behave exactly
    like asking for mode 1, so a flow that says nothing gets the width-aware
    charge.
    """
    unset = _plan(*_one_band_design(bus_width=60.0), knob=None)
    mode1 = _plan(*_one_band_design(bus_width=60.0), knob=1)
    assert _key(unset) == _key(mode1)


def test_explicit_zero_is_the_legacy_escape_hatch():
    """`band_span_charge 0` still reaches the old single-band charge.

    The escape hatch has to be REACHABLE and has to differ from the default on
    a bus wide enough to be mis-charged — otherwise the flip would be
    unrevertable in practice and this whole knob inert.  Distinguished by the
    band usage, since that is what the two models actually disagree about: the
    legacy charge dumps the bus on one band, mode 1 spreads it.
    """
    def usage_profile(knob):
        fp, ls, w = _one_band_design(bus_width=60.0)
        p = buda.CongestionPlanner(fp, ls)
        if knob is not None:
            p.set_planner_param("band_span_charge", knob)
        p.build_congestion_map()
        with buda.ostream_redirect():
            p.optimize_topologies([w], 1)
        return sorted(c.usage(b) for c in p.get_cuts()
                      for b in range(c.num_bands()) if c.usage(b) > 0)

    legacy, default = usage_profile(0), usage_profile(None)
    assert len(legacy) < len(default), (
        f"explicit 0 should concentrate the charge on fewer bands than the "
        f"spreading default; got legacy={legacy} default={default}"
    )
    assert sum(legacy) == pytest.approx(sum(default), rel=1e-9), (
        "the escape hatch must charge the same TOTAL, only differently spread"
    )


def test_narrow_bus_unaffected_by_the_knob():
    """A bus that already fits inside one band is charged identically either
    way — the knob must only move buses that actually straddle bands.

    This is what makes the off/on difference attributable to the spread, and
    not to some incidental re-derivation.
    """
    fp, ls, w = _one_band_design(bus_width=1.0)
    off = _plan(fp, ls, w, knob=0)
    fp, ls, w = _one_band_design(bus_width=1.0)
    on  = _plan(fp, ls, w, knob=1)
    assert [(a.bundle_id, a.topo_index, a.seg_layers) for a in off] == \
           [(a.bundle_id, a.topo_index, a.seg_layers) for a in on]


# ── The mechanism ─────────────────────────────────────────────────────────────

def test_wide_bus_still_plans_under_both_settings():
    """A bus far wider than any band plans either way — the knob changes the
    PRICE, never whether a lone bundle gets an assignment.

    Guards against the spread accidentally hard-blocking a segment (e.g. via
    the cap<=0 path in cong_cost_segment) rather than merely re-pricing it.
    """
    for knob in (0, 1):
        fp, ls, w = _one_band_design(bus_width=60.0)
        asn = _plan(fp, ls, w, knob=knob)
        assert len(asn) == 1, f"knob={knob}: lone wide bus lost its assignment"
        assert asn[0].seg_layers, f"knob={knob}: no per-segment layer assigned"


def test_charge_is_conserved_across_the_spread():
    """Total charged demand is invariant: spreading redistributes it, it does
    not create or destroy demand.

    This is the soundness property the whole knob rests on: if spreading
    could shed demand, it would relieve congestion on paper that still
    exists in metal.  Here the 60-wide bus lands on seven bands as
    [5.5, 10, 10, 10, 10, 10, 5.5] instead of one band's 61.0 — same total,
    different distribution.

    Scope note: this exercises the in-grid case only.  The normalization that
    protects a footprint OVERHANGING the grid edge is not reachable from
    here, because the planner picks the perp itself (best_band_perp) and
    re-centres the bus away from the edge; verifying that path would need
    direct access to for_each_band_w, which is not bound to Python.
    """
    totals = {}
    for knob in (0, 1):
        fp, ls, w = _one_band_design(bus_width=60.0)
        p = buda.CongestionPlanner(fp, ls)
        p.set_planner_param("band_span_charge", knob)
        p.build_congestion_map()
        with buda.ostream_redirect():
            p.optimize_topologies([w], 1)
        totals[knob] = sum(c.usage(b)
                           for c in p.get_cuts()
                           for b in range(c.num_bands()))
    assert totals[0] == pytest.approx(totals[1], rel=1e-9), (
        f"spreading changed total charged demand: {totals[0]} -> {totals[1]}"
    )
