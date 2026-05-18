# gemini
import interconnect
import os

fp = interconnect.Floorplan()
fp.add_block("u0", 200, 400, 300, 600)
fp.add_block("u12", 550, 600, 600, 1000)
fp.set_global_corner_margin(15, 20)

tg = interconnect.TopologyGenerator(fp)
candidates = tg.generate_candidates("u0", "u12")

for i, cand in enumerate(candidates):
    print(f"Candidate {i}: {cand.type}")
    for j, seg in enumerate(cand.segments):
        print(f"  Seg {j}: ({seg.start.x}, {seg.start.y}) -> ({seg.end.x}, {seg.end.y})")
