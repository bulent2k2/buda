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
Relay-tap slide preservation (root cause) + collapsed-span detection (verifier).

Regression: a relay block (e.g. flow/big_data_test/big.buda bundle 3, blk_19)
reached by an MST / trunk+MST staircase keeps exactly ONE busterm tap (the
single-tap model); the other landings become SEG junctions chained by connectors
that run along the block's faces.  A BUSTERM conn clamps only the tap segment's
PERPENDICULAR slide — its ALONG reach to face_coord is set by the connector at
that same endpoint.  That connector was left unbounded, so NUTS slid the whole
staircase off the block face: the tap's span collapsed below face_coord and the
block silently detached at NUTS/dNUTS while check_nuts (which only tested the
perpendicular track_position) passed.

Two fixes are guarded here:
  • check_nuts/check_dnuts now also require the placed along-span to REACH
    face_coord (else BUSTERM_OPEN).
  • ConnTopology::pin_relay_tap_connectors() pins the tap-adjacent connector to
    face_coord so NUTS span adjustment preserves the reach.
"""
import buda


# ── verifier: collapsed along-span is an open ──────────────────────────────────

def _two_block_vstub():
    """A vertical stub whose two ends land on bot's top face (y=100) and top's
    bottom face (y=200): a busterm to each block."""
    fp = buda.Floorplan()
    fp.add_block("bot", 0, 0,   100, 100)
    fp.add_block("top", 0, 200, 100, 300)
    ls = buda.LayerStack()
    ls.add_layer(4, "M4", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    ls.add_layer(5, "M5", buda.LayerDir.VERTICAL,   buda.LayerType.TOP)
    seg = buda.Segment()
    seg.start = buda.Point(50, 100)
    seg.end   = buda.Point(50, 200)
    seg.layer_hint = 5
    topo = buda.Topology()
    topo.type = "TEST_V"
    topo.segments = [seg]
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    return fp, ls, topo, ct


def _placed(span_lo, span_hi):
    ts = buda.TrackSegment()
    ts.bundle_id = 1
    ts.seg_idx = 0
    ts.layer = 5
    ts.horiz = False
    ts.span_lo, ts.span_hi = span_lo, span_hi
    ts.track_position = 50.0          # perpendicular x — inside BOTH block faces
    ts.placed = True
    nuts = buda.NUTSResult()
    nuts.segments = [ts]
    return nuts


def _busterm_opens(res, block):
    return [v for v in res.violations
            if v.kind == buda.ViolationKind.BUSTERM_OPEN and v.block_name == block]


def test_check_nuts_flags_collapsed_busterm_span():
    """Span retracted to [100,150] no longer reaches top's face (y=200), even
    though the perpendicular track_position (x=50) still lands within it."""
    fp, ls, topo, ct = _two_block_vstub()
    res = buda.check_nuts(ct, _placed(100, 150), topo, fp, ls, 1)
    opens = _busterm_opens(res, "top")
    assert len(opens) == 1, [v.message for v in res.violations]
    assert "no longer reaches" in opens[0].message
    # bot's face (y=100) is still reached — must NOT be flagged.
    assert not _busterm_opens(res, "bot")


def test_check_nuts_accepts_busterm_span_that_reaches():
    """Span [100,200] reaches both faces — no BUSTERM_OPEN."""
    fp, ls, topo, ct = _two_block_vstub()
    res = buda.check_nuts(ct, _placed(100, 200), topo, fp, ls, 1)
    assert not _busterm_opens(res, "top")
    assert not _busterm_opens(res, "bot")


# ── root cause: relay-tap connector is pinned to the block face ────────────────

# The six blocks of flow/big_data_test/big.buda bundle 3 (driver blk_01).
_B3_BLOCKS = {
    "blk_01": (10680, 2310, 13580, 3310),
    "blk_34": (200,   3400, 1400,  4100),
    "blk_39": (11280, 7150, 13580, 9050),
    "blk_09": (7810,  5520, 10710, 6020),
    "blk_19": (200,   5540, 2100,  6840),
    "io_pad_br": (12580, 200, 13580, 1000),
}


def test_relay_tap_connector_bounded_to_cell():
    """On every generated relay (MST/trunk+MST) candidate, a connector attached at
    a busterm tap's face endpoint is bounded to the tapped block's footprint (a
    real over-the-cell window) — NOT a degenerate zero-slide face pin, and NOT
    unbounded.  It may slide over the cell but never off it, so the tap's along
    reach is preserved while NUTS still has room to place a positive-width bus."""
    SENT = 1 << 29
    fp = buda.Floorplan()
    for nm, c in _B3_BLOCKS.items():
        fp.add_block(nm, *c)
    gen = buda.TopologyGenerator(fp)
    gen.set_layer_ids(6, 7)   # h=M6, v=M7 (both TOP layers in this design)
    cands = gen.generate_candidates(
        "blk_01", ["blk_34", "blk_39", "blk_09", "blk_19", "io_pad_br"])
    relay = [c for c in cands if "MST" in c.type]
    assert relay, f"expected a relay candidate; got {sorted({c.type for c in cands})}"

    checked = 0
    for cand in relay:
        ct = buda.ConnTopology()
        ct.build(cand, fp)
        segs = ct.segs()
        for cs in segs:
            for bc in cs.conns:
                if bc.kind != buda.SegConnKind.BUSTERM:
                    continue
                f = bc.face_coord
                if f != cs.along_lo and f != cs.along_hi:
                    continue
                bb = fp.get_block_bounds(bc.block_name)
                for sc in cs.conns:
                    if sc.kind != buda.SegConnKind.SEG or not sc.is_endpoint:
                        continue
                    if sc.at_pos != f:
                        continue
                    T = segs[sc.seg_idx]
                    if T.horiz == cs.horiz:   # need a perpendicular bend
                        continue
                    lo, hi = (bb.y1, bb.y2) if T.horiz else (bb.x1, bb.x2)
                    assert T.perp_lo < T.perp_hi, (
                        f"relay tap connector zero-slide pinned: "
                        f"perp=[{T.perp_lo},{T.perp_hi}] (cand {cand.type})")
                    assert abs(T.perp_lo) < SENT and abs(T.perp_hi) < SENT, (
                        f"relay tap connector unbounded: "
                        f"perp=[{T.perp_lo},{T.perp_hi}] (cand {cand.type})")
                    assert lo <= T.perp_lo and T.perp_hi <= hi, (
                        f"relay tap connector window [{T.perp_lo},{T.perp_hi}] "
                        f"escapes cell [{lo},{hi}] (cand {cand.type})")
                    checked += 1
    assert checked > 0, "no relay tap/connector pair exercised — test is vacuous"
