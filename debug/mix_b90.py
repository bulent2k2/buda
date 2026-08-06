# issue #485 Analyze the 26 remaining ANTENNA candidates (TRUNK_*+MST hybrid legs)
"""Bundle 90 of flow/rnr/mix.buda: what covers block chip/i_dnuts2_2/u4 in the
SELECTED topology, at nominal and at placed positions?"""
# 
""" sample run: 
(base) ben@Bulents-MacBook-Pro cc % time python3 debug/mix_b90.py
time python3 debug/mix_b90.py
do command source mix_tracks.buda..
do command open_bdb mix.bdb.sql..
do command derive_busterms 2..
do command add_blocks_from_bdb 0..
do command add_blocks_from_bdb 1 skip..
do command add_blocks_from_bdb 2 skip..
do command run_hier_bundler depth 2..
do command dump_hbundles..
do command generate_hier_topologies no_hanan_loci..
[TopoGen] dropped 5 redundant trunk+MST hybrid(s) (removable seed trunk; first: TRUNK_H+MST@y1335).
[TopoGen] dropped 1 candidate(s) (0 feedthru-relay, first open: TRUNK_V+MST@x1775 missing block 'chip/i_dnuts1_0/u12'); 28 remain.
[TopoGen] dropped 2 redundant trunk+MST hybrid(s) (removable seed trunk; first: TRUNK_V_OOB+MST@x426).
[TopoGen] dropped 3 redundant trunk+MST hybrid(s) (removable seed trunk; first: TRUNK_H+MST@y610).
[TopoGen] dropped 2 redundant trunk+MST hybrid(s) (removable seed trunk; first: TRUNK_H_OOB+MST@y608).
[TopoGen] dropped 2 redundant trunk+MST hybrid(s) (removable seed trunk; first: TRUNK_V+MST@x1675).
[TopoGen] dropped 2 redundant trunk+MST hybrid(s) (removable seed trunk; first: TRUNK_V+MST@x845).
[TopoGen] dropped 1 redundant trunk+MST hybrid(s) (removable seed trunk; first: TRUNK_H_OOB+MST@y2023).
[TopoGen] dropped 1 redundant trunk+MST hybrid(s) (removable seed trunk; first: TRUNK_V_OOB+MST@x-75).
[TopoGen] dropped 4 redundant trunk+MST hybrid(s) (removable seed trunk; first: TRUNK_H+MST@y335).
[TopoGen] dropped 1 redundant trunk+MST hybrid(s) (removable seed trunk; first: TRUNK_H_OOB+MST@y2049).
[TopoGen] dropped 4 redundant trunk+MST hybrid(s) (removable seed trunk; first: TRUNK_V+MST@x790).
[TopoGen] dropped 2 redundant trunk+MST hybrid(s) (removable seed trunk; first: TRUNK_H+MST@y1250).
[TopoGen] dropped 2 redundant trunk+MST hybrid(s) (removable seed trunk; first: TRUNK_H_OOB+MST@y137).
[TopoGen] dropped 3 redundant trunk+MST hybrid(s) (removable seed trunk; first: TRUNK_V+MST@x125).
[TopoGen] dropped 2 redundant trunk+MST hybrid(s) (removable seed trunk; first: TRUNK_H_OOB+MST@y1067).
[TopoGen] dropped 3 redundant trunk+MST hybrid(s) (removable seed trunk; first: TRUNK_V+MST@x125).
[TopoGen] dropped 3 redundant trunk+MST hybrid(s) (removable seed trunk; first: TRUNK_V+MST@x125).
[TopoGen] dropped 3 redundant trunk+MST hybrid(s) (removable seed trunk; first: TRUNK_V+MST@x125).
do command set_planner_param healersAhead 1..
do command run_planner hier 5 signal_tracks..
[Planner] Layer channel capacities (signal-track mode):
  M3 (V)  min_signal_tracks=0
  M5 (V)  min_signal_tracks=4
  M7 (V)  min_signal_tracks=1
  M2 (H)  min_signal_tracks=0
  M4 (H)  min_signal_tracks=4
  M6 (H)  min_signal_tracks=3
do command run_nuts..
do command negotiate_congestion..
do command run_detailed_nuts..
do command negotiate_congestion..
bundle 90: 39 candidates, selected 9 = TRUNK_H+MST@y1555
  pinned=False  blocks=['chip/i_dogleg2_0/top3', 'chip/i_dnuts1_0/u0', 'chip/i_dnuts2_2/u4', 'chip/i_dogleg1_1/top2', 'chip/i_dogleg2_2/top3', 'chip/i_dogleg2_3/bot2']
  layers=[6, 5, 5, 5, 6, 5, 4]
  chip/i_dnuts2_2/u4 bbox=(1680,1530)-(1730,1580)
  seg0 (1095,1555)-(1850,1555) nominal_perp=1555 placed_perp=1545.0 taps=[]
  seg1 (1850,790)-(1850,1555) nominal_perp=1850 placed_perp=1861.25 taps=[]
  seg2 (1100,1180)-(1100,1555) nominal_perp=1100 placed_perp=1108.75 taps=[]
  seg3 (1095,1780)-(1095,1555) nominal_perp=1095 placed_perp=1108.75 taps=['chip/i_dogleg2_2/top3']
  seg4 (1850,790)-(1660,790) nominal_perp=790 placed_perp=775.0 taps=['chip/i_dogleg1_1/top2']
  seg5 (1035,1180)-(1035,490) nominal_perp=1035 placed_perp=1048.75 taps=['chip/i_dogleg2_3/bot2']
  seg6 (1100,1180)-(1035,1180) nominal_perp=1180 placed_perp=1208.75 taps=[]
  --- candidate types (all) ---
     0 TRUNK_H@y1165
     1 TRUNK_H@y1210
     2 TRUNK_V+MST@x1160
     3 TRUNK_H+MST@y960
     4 TRUNK_V+MST@x1095
     5 TRUNK_V+MST@x1705
     6 TRUNK_H+MST@y765
     7 TRUNK_H+MST@y1380
     8 TRUNK_V@x1405
     9 TRUNK_H+MST@y1555 <== SELECTED
    10 TRUNK_V@x1635
    11 TRUNK_V@x1160
    12 TRUNK_V+MST@x1035
    13 TRUNK_V+MST@x1790
    14 TRUNK_H@y1380
    15 TRUNK_H@y960
    16 TRUNK_V@x1095
    17 TRUNK_H+MST@y1680
    18 TRUNK_V@x1705
    19 MST_HV
    20 TRUNK_H+MST@y615
    21 TRUNK_V+MST@x1875
    22 TRUNK_H+MST@y1805
    23 TRUNK_V@x1035
    24 TRUNK_V@x1790
    25 TRUNK_H@y1555
    26 TRUNK_H@y765
    27 BITRUNK_H
    28 TRUNK_H_OOB+MST@y301
    29 TRUNK_V@x1875
    30 TRUNK_H_OOB+MST@y1969
    31 TRUNK_V_OOB@x910
    32 TRUNK_H@y1680
    33 TRUNK_H@y615
    34 TRUNK_V_OOB@x1990
    35 TRUNK_H@y1805
    36 TRUNK_H@y465
    37 TRUNK_H_OOB@y1969
    38 TRUNK_H_OOB@y301
python3 debug/mix_b90.py  10.80s user 0.25s system 97% cpu 11.343 total
"""

import io, contextlib, sys, os
path ='/Users/ben/src/git/buda/cc/'
sys.path.insert(0, path+'src'); sys.path.insert(0, path+'tools')
os.chdir(path+'flow/rnr')
import buda_cli, buda

s = buda_cli.BudaSession(); s.no_viz = True
for line in open('mix.buda'):
    c = line.strip()
    if not c or c.startswith('#'): continue
    if c.split()[0] in ('visualize', 'exit', 'check_design', 'report_wirelength','ripup_reroute'):
        continue
    print(f"do command {c}..")
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command(c)

BAD = 'chip/i_dnuts2_2/u4'
for w in s.bundles:
    b = w.input.original_bundle
    if b.id != 90: continue
    ti = w.plan.selected_topology_index
    t = w.input.candidates[ti]
    print(f"bundle {b.id}: {len(w.input.candidates)} candidates, selected {ti} = {t.type}")
    print(f"  pinned={w.input.topology_pinned}  blocks={list(t.connected_block_names)}")
    print(f"  layers={list(w.plan.seg_layers)}")
    ct = buda.ConnTopology(); ct.build(t, s.fp)
    r = s.fp.get_block_bounds(BAD)
    print(f"  {BAD} bbox=({r.x1},{r.y1})-({r.x2},{r.y2})")
    for j, sg in enumerate(t.segments):
        cs = ct.segs()[j]
        bt = t.seg_busterms.get(j)
        names = []
        if bt is not None:
            for e in bt:
                if e: names.append(e.block_name)
        placed = [x for x in s.nuts_result.segments
                  if x.bundle_id == b.id and x.seg_idx == j]
        pp = f" placed_perp={placed[0].track_position}" if placed else " UNPLACED"
        print(f"  seg{j} ({sg.start.x},{sg.start.y})-({sg.end.x},{sg.end.y}) "
              f"nominal_perp={cs.perp_pos}{pp} taps={names}")
    print("  --- candidate types (all) ---")
    for i, c in enumerate(w.input.candidates):
        mark = " <== SELECTED" if i == ti else ""
        print(f"   {i:3d} {c.type}{mark}")
