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
