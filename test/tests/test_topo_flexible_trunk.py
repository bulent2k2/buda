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
Coverage-driven flexible trunk span (under double_detour).

A trunk is the bundle's root: under double_detour its endpoints become flexible
and the spine FULLY traverses every block it connects by pass-through /
containment (reaching the far faces), then extends beyond the busterm bbox.  This
unlocks the "region-4" trunk that connects blocks PURELY by pass-through — the
flow/big_data_test/big2/b4_bus_077.buda geometry, where a V trunk inside
io_pad_tr's x-span (x≈5772) taps all four right-column receivers
(blk_22/blk_12/blk_29/io_pad_tr) by pass-through and stubs only the driver blk_02.

Previously that candidate was silently dropped: at x5772 nearly every block is
pass-through, the stub-only span was degenerate / the lone driver stub pinned
against its own face, and filter_pinched removed it.  Without double_detour the
span stays tight (unchanged behaviour), so this candidate only appears under it.
"""
import buda

# bus_077: driver blk_02 (top-left) -> receivers stacked in the right column.
_COORDS = {
    "blk_02":    (4280, 5350, 5445, 6300),   # driver
    "blk_12":    (4870, 4425, 6100, 5350),
    "blk_22":    (4870, 2385, 6100, 3365),
    "blk_29":    (4870, 1425, 6100, 2385),
    "io_pad_tr": (5445, 5350, 6100, 6300),
}
_DRV = "blk_02"
_RCV = ["blk_22", "blk_12", "blk_29", "io_pad_tr"]


def _make_fp():
    fp = buda.Floorplan()
    for name, (x1, y1, x2, y2) in _COORDS.items():
        fp.add_block(name, x1, y1, x2, y2)
    return fp


def _gen(fp, double_detour):
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(6, 7)  # M6 H, M7 V (matches the big2 tracks)
    if double_detour:
        g.set_double_detour(True)
    return g


def _violations(topo, fp):
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    return [str(v.kind).split(".")[-1] for v in buda.check_topo(ct, topo, fp, 0).violations]


def _pinched(topo, fp):
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    return any(s.perp_lo == s.perp_hi for s in ct.segs())


def test_region4_trunk_generated_under_double_detour():
    """The x≈5772 pass-through trunk exists, covers every block, is not pinched."""
    fp = _make_fp()
    cands = _gen(fp, double_detour=True).generate_candidates(_DRV, _RCV)
    region4 = [c for c in cands if c.type == "TRUNK_V@x5772"]
    assert region4, (
        "expected the region-4 TRUNK_V@x5772 (inside io_pad_tr's x-span) under "
        f"double_detour; got {sorted({c.type for c in cands})}"
    )
    c = region4[0]
    # One V spine + exactly one stub (the driver blk_02); the four right-column
    # receivers are all tapped by pass-through.
    assert len(c.segments) == 2, f"expected spine + 1 driver stub, got {len(c.segments)} segs"
    assert "BUSTERM_OPEN" not in _violations(c, fp), _violations(c, fp)
    assert "FEEDTHRU_RELAY" not in _violations(c, fp), _violations(c, fp)
    assert not _pinched(c, fp), "region-4 trunk must not have a zero-slide segment"


def test_flexible_trunk_is_double_detour_gated():
    """Without double_detour the coverage extension is OFF: the purely-pass-through
    x5772 trunk is not emitted (tight-span behaviour unchanged)."""
    fp = _make_fp()
    cands = _gen(fp, double_detour=False).generate_candidates(_DRV, _RCV)
    assert not any(c.type == "TRUNK_V@x5772" for c in cands), (
        "region-4 trunk should only appear under double_detour"
    )


def test_recenter_keeps_suppressed_block_covered():
    """Recentering stubs to their centerlines must not orphan a block that stub
    suppression dropped (the Codex P1).

    Geometry (V trunk near x=1200, all three blocks to its right/left as stubs):
    `F` (far right, y[0,100], centre 50) is the lo-extreme, so the att_y
    refinement pulls it UP to its top face y=100 — which lands inside `N`
    (near right, y[90,110]), suppressing N's stub (F's wire covers it).  Under
    double_detour we then recenter F back to its centre y=50, which no longer
    crosses N.  Without the un-suppress re-validation, N is left with neither a
    busterm nor pass-through and TRUNK_V@x1200 reports BUSTERM_OPEN; with it, N is
    re-stubbed and every TRUNK_V candidate stays fully covered.
    """
    coords = {
        "drv": (1000,  0, 1100, 200),   # driver, left of the x≈1200 trunk
        "N":   (1300, 90, 1500, 110),   # near right stub, y centre 100
        "F":   (1700,  0, 2000, 100),   # far  right stub, y centre 50 → pulled to 100
    }
    fp = buda.Floorplan()
    for name, (x1, y1, x2, y2) in coords.items():
        fp.add_block(name, x1, y1, x2, y2)
    cands = _gen(fp, double_detour=True).generate_candidates("drv", ["N", "F"])
    trunk_v = [c for c in cands if c.type.startswith("TRUNK_V")]
    assert trunk_v, "expected TRUNK_V candidates for this multicast bundle"
    for c in trunk_v:
        assert "BUSTERM_OPEN" not in _violations(c, fp), (
            f"{c.type} left a block uncovered after recentering: {_violations(c, fp)}"
        )
