import interconnect
import os

fp = interconnect.Floorplan()
fp.add_block("u1",    50,   50,   100, 100)
fp.add_block("u2",    150,  50,   200, 100)
fp.set_global_corner_margin(10, 10)

def make_seg(x1, y1, x2, y2, layer):
    s = interconnect.Segment()
    s.start = interconnect.Point(x1, y1)
    s.end = interconnect.Point(x2, y2)
    s.layer_hint = layer
    return s

cand = interconnect.Topology()
cand.type = "U_VHV@y105"
segs = []
segs.append(make_seg(90, 100, 90, 105, 5))
segs.append(make_seg(90, 105, 160, 105, 6))
segs.append(make_seg(160, 105, 160, 100, 5))
cand.segments = segs

ct = interconnect.ConnTopology()
ct.build(cand, fp)
print(f"U_VHV@y105 slides (Num segments={len(ct.segs())}):")
for i, cs in enumerate(ct.segs()):
    dir_str = "H" if cs.horiz else "V"
    print(f"  Seg {i} ({dir_str}): perp_pos={cs.perp_pos} slide=[{cs.perp_lo}, {cs.perp_hi}] along=[{cs.along_lo}, {cs.along_hi}]")
    for conn in cs.conns:
        if conn.kind == interconnect.SegConnKind.BUSTERM:
            print(f"    Conn to {conn.block_name} at face_coord={conn.face_coord}")
