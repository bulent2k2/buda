import interconnect
import os

fp = interconnect.Floorplan()
fp.add_block("u1",    50,   50,   100, 100)
fp.add_block("u2",    150,  50,   200, 100)
fp.add_block("u3",    50,  150,   100, 200)
fp.add_block("u4",    150, 150,   200, 200)
fp.set_global_corner_margin(10, 10)
print(f"Blocks:")
for name, rect in fp.get_all_blocks():
    print(f"  {name}: {rect.x1},{rect.y1} to {rect.x2},{rect.y2}")

tg = interconnect.TopologyGenerator(fp)
# multicast generator uses the full floorplan Hanan grid
candidates = tg.generate_multicast_candidates("u1", ["u2"])

print(f"Candidates for u1->u2:")
for i, cand in enumerate(candidates):
    if "U_VHV@y105" in cand.type:
        print(f"Candidate {i}: {cand.type}")
        ct = interconnect.ConnTopology()
        ct.build(cand, fp)
        for j, cs in enumerate(ct.segs()):
            dir_str = "H" if cs.horiz else "V"
            print(f"  Seg {j} ({dir_str}): perp_pos={cs.perp_pos} slide=[{cs.perp_lo}, {cs.perp_hi}] along=[{cs.along_lo}, {cs.along_hi}]")
            for conn in cs.conns:
                if conn.kind == interconnect.SegConnKind.BUSTERM:
                    print(f"    Conn to {conn.block_name} at face_coord={conn.face_coord}")
