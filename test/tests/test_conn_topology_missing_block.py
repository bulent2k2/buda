"""ConnTopology.build must not hard-crash when a busterm annotation references
a block that is absent from the floorplan it is being checked against.

Regression (flow/hbundles/10_chip_units_blocks_leaf.buda, `check_connectivity
topo`): cell-level HBundle templates carry seg_busterms annotations naming
cell-local blocks (e.g. `lo`/`hi`).  When `_check_connectivity` built a
ConnTopology against the chip floorplan (`self.fp`), `compute_slide_ranges`
called `bmap.at(block_name)` and threw `IndexError: map::at: key not found`,
aborting the whole CLI.  The fix:
  - C++: compute_slide_ranges skips a busterm whose block is missing from the
    floorplan (mirrors the graceful misses in the other Floorplan accessors).
  - Python: connectivity checks resolve each hier bundle's own generation
    floorplan (cell-local / depth / custom) so the check is meaningful.
"""
import buda


def _seg(x1, y1, x2, y2, hint):
    s = buda.Segment()
    s.start = buda.Point(x1, y1)
    s.end   = buda.Point(x2, y2)
    s.layer_hint = hint
    return s


def test_busterm_annotation_to_missing_block_does_not_throw():
    fp = buda.Floorplan()
    fp.add_block("real", 0, 0, 100, 100)

    # H segment whose left endpoint is annotated as a busterm on `ghost`, a
    # block that does not exist in this floorplan (cell-local name checked
    # against the chip floorplan).
    topo = buda.Topology()
    topo.type = "L"
    topo.segments = [_seg(0, 50, 100, 50, 6)]
    topo.connected_block_names = ["ghost", "real"]
    bt = buda.Busterm()
    bt.block_name = "ghost"
    bt.bbox = buda.Rect(-10, 0, 0, 100)
    topo.seg_busterms = {0: (bt, None)}

    ct = buda.ConnTopology()
    ct.build(topo, fp)            # must not raise

    segs = ct.segs()
    assert len(segs) == 1
    # The busterm connection is still recorded; only the (unavailable) slide
    # constraint from the missing block is skipped.
    kinds = [(c.kind, c.block_name) for c in segs[0].conns]
    assert (buda.SegConnKind.BUSTERM, "ghost") in kinds
