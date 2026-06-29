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
Pass-through slide tightening must only apply to blocks whose connectivity
actually comes from the pass-through (no BUSTERM anywhere in the topology).

Regression (flow/psi1.buda): a U_HVH source stub off `left` happened to graze
the dst block's perp extent at its nominal position; tighten_passthrough_ranges
clamped its slide range to the dst block's y-span ([450,500] / [60,190])
instead of the source face's full extent ([0,500]) — even though the dst block
is explicitly connected via the other H stub's BUSTERM.
"""
import buda


def _seg(x1, y1, x2, y2, hint):
    s = buda.Segment()
    s.start = buda.Point(x1, y1)
    s.end   = buda.Point(x2, y2)
    s.layer_hint = hint
    return s


def _build_u_hvh(fp):
    """psi1 bundle 1 geometry: left(0,0,400,500) -> right(600,450,700,550),
    U detour at x=770: H stub at y=500, V trunk, H stub at y=450."""
    topo = buda.Topology()
    topo.type = "U_HVH@x770"
    topo.segments = [
        _seg(400, 500, 770, 500, 6),   # seg0: H stub off left's right face
        _seg(770, 450, 770, 500, 5),   # seg1: V trunk
        _seg(700, 450, 770, 450, 6),   # seg2: H stub off right's right face
    ]
    topo.connected_block_names = ["left", "right"]
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    return ct


def test_stub_grazing_busterm_connected_block_keeps_full_slide():
    fp = buda.Floorplan()
    fp.add_block("left",  0,   0,   400, 500)
    fp.add_block("right", 600, 450, 700, 550)

    ct = _build_u_hvh(fp)
    segs = ct.segs()
    assert len(segs) == 3

    # seg0 grazes `right` (y=500 in [450,550], x overlap) but `right` is
    # explicitly connected via seg2's BUSTERM — seg0 must keep the full
    # y-extent of `left`'s face as its slide range.
    assert (segs[0].perp_lo, segs[0].perp_hi) == (0, 500), (
        f"source stub slide must span left's whole face, got "
        f"[{segs[0].perp_lo},{segs[0].perp_hi}]"
    )
    # seg2 is anchored to `right`: clamped to its face extent.
    assert (segs[2].perp_lo, segs[2].perp_hi) == (450, 550)


def test_true_passthrough_block_still_tightens():
    """A connected block with NO busterm anywhere (suppressed stub) must
    still clamp the spanning segment so NUTS can't slide it off the block."""
    fp = buda.Floorplan()
    fp.add_block("src", 0,   0,  100, 300)
    fp.add_block("mid", 200, 100, 300, 200)
    fp.add_block("dst", 400, 0,  500, 300)

    topo = buda.Topology()
    topo.type = "I_H"
    topo.segments = [_seg(100, 150, 400, 150, 6)]
    topo.connected_block_names = ["src", "mid", "dst"]
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    cs = ct.segs()[0]

    # mid has no BUSTERM (pure pass-through) → slide clamped to its y-span.
    assert cs.perp_lo >= 100 and cs.perp_hi <= 200, (
        f"pass-through block must clamp slide to [100,200], got "
        f"[{cs.perp_lo},{cs.perp_hi}]"
    )


# ── Boundary-graze de-pinch (flow/big_data_test/big2/b4_bus_077.buda) ──────────
# A horizontal stub bound for a receiver busterm runs along the TOP edge it shares
# with a neighbouring pass-through block (perp_pos == block.y2).  The inclusive
# pass-through clamp used to pin that stub to the neighbour's y-extent, collapsing
# it against its receiver-face window to ZERO slide (PINCHED) — unplaceable at
# NUTS.  When the neighbour is already covered by a STRICT-interior coverer (a
# trunk crossing it), the graze is redundant and the stub must keep its receiver
# window; when the graze is the SOLE cover, the clamp stays (pinched) so the block
# is not silently disconnected and filter_pinched can reject the candidate.

def test_grazing_busterm_stub_with_strict_cover_keeps_slide():
    """seg1 grazes `cover`'s top edge en route to tap `recv`, but `cover` is
    strictly crossed by the trunk seg0 → seg1 keeps recv's face window, not {300}."""
    fp = buda.Floorplan()
    fp.add_block("cover", 100, 100, 300, 300)
    fp.add_block("recv",  400, 300, 600, 500)

    topo = buda.Topology()
    topo.type = "TRUNK_V@x200"
    topo.segments = [
        _seg(200, 50, 200, 350, 5),    # seg0: V trunk strictly crossing `cover`
        _seg(200, 300, 400, 300, 6),   # seg1: H stub grazing cover's top, taps recv
    ]
    topo.connected_block_names = ["cover", "recv"]
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    h = ct.segs()[1]

    assert h.perp_lo != h.perp_hi, (
        f"grazing busterm stub must NOT be pinched when the block has a strict "
        f"coverer, got [{h.perp_lo},{h.perp_hi}]"
    )
    assert (h.perp_lo, h.perp_hi) == (300, 500), (
        f"stub must keep recv's left-face window [300,500], got "
        f"[{h.perp_lo},{h.perp_hi}]"
    )


def test_grazing_busterm_stub_as_sole_cover_stays_clamped():
    """Same graze, but no trunk → `cover` is covered ONLY by the grazing stub.
    The clamp must stay so cover is not disconnected; the stub pinches (the
    candidate is genuinely over-constrained and filter_pinched would drop it)."""
    fp = buda.Floorplan()
    fp.add_block("cover", 100, 100, 300, 300)
    fp.add_block("recv",  400, 300, 600, 500)

    topo = buda.Topology()
    topo.type = "I_H"
    topo.segments = [
        _seg(200, 300, 400, 300, 6),   # H stub: sole (grazing) cover of `cover`
    ]
    topo.connected_block_names = ["cover", "recv"]
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    h = ct.segs()[0]

    assert h.perp_lo == h.perp_hi == 300, (
        f"sole grazing cover must stay clamped to the shared edge {{300}} so the "
        f"block stays connected, got [{h.perp_lo},{h.perp_hi}]"
    )
