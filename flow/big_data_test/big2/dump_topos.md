After the fix
===
── bundle 1  nets=60 (bus_077_b00…)  width=90.0  sel=3 PINNED  cands=14  PASSTHRU(5)
   idx type                 wl segs pass  mslide  notes
     0 TRUNK_V@x5157      3253    2    2     555  
     1 TRUNK_V_OOB@x6282  4166    4    0     960  
     2 TRUNK_V@x4575      4425    4    0     570  
     3 TRUNK_H@y4887      5605    5    1     635  *SEL
     4 TRUNK_V_OOB@x4098  5856    4    0     960  
     5 TRUNK_H@y3895      6710    6    0     655  
     6 BITRUNK_H          6810    5    0     575  
     7 TRUNK_H+MST@y3895  7475    8    0     575  
     8 TRUNK_H@y2875      7605    5    0     555  
     9 TRUNK_H@y5825      7605    4    2     575  
    10 TRUNK_H+MST@y4887  8655    8    1     905  
    11 TRUNK_H@y1905     11120    5    1     555  
    12 TRUNK_H_OOB@y6787 11465    6    0     655  
    13 TRUNK_H_OOB@y938  15475    6    0     655  
   conn detail — candidate 3: TRUNK_H@y4887
     seg0  H M6·hint  along[5445,6100] perp=4887  slide=[4425..5330] = 905  pull=none(0)
        busterms: blk_12@face=6100(mid)
        segs:     seg1@5445(end), seg4@5445(end), seg2@6100(end), seg3@5485(mid)
        passthru: (none)
     seg1  V M7·hint  along[4887,5350] perp=5445  slide=[4280..5445] = 1165  pull=→hi(1)
        busterms: blk_02@face=5350(mid)
        segs:     seg0@4887(end)
        passthru: blk_12, io_pad_tr
     seg2  V M7·hint  along[3365,4887] perp=6100  slide=[4870..6100] = 1230  pull=none(0)
        busterms: blk_22@face=3365(mid)
        segs:     seg0@4887(end)
        passthru: blk_12, blk_39
     seg3  V M7·hint  along[2385,4887] perp=5485  slide=[4870..6080] = 1210  pull=→hi(1)
        busterms: blk_29@face=2385(mid)
        segs:     seg0@4887(end)
        passthru: blk_12, blk_22, blk_39
     seg4  V M7·hint  along[4887,5350] perp=5445  slide=[5445..6080] = 635  pull=→hi(1)
        busterms: io_pad_tr@face=5350(mid)
        segs:     seg0@4887(end)
        passthru: blk_02, blk_12

── bundle 2  nets=48 (bus_056_b00…)  width=72.0  sel=7 PINNED  cands=23  PASSTHRU(4)
   idx type                 wl segs pass  mslide  notes
     0 TRUNK_H@y3205      3885    3    1     190  
     1 TRUNK_V@x4185      3885    3    0     190  
     2 TRUNK_H@y3337      3912    4    1      15  
     3 MST_VH             4710    5    0     380  
     4 TRUNK_V+MST@x5485  4795    6    0     380  
     5 TRUNK_V@x2865      4900    4    0     360  
     6 MST_HV             4945    6    0     380  
     7 TRUNK_V@x5485      5115    3    0     530  *SEL
     8 TRUNK_V+MST@x2865  5240    8    0     190  
     9 TRUNK_H_OOB@y2385  5395    5    0    1230  
    10 TRUNK_H@y3895      5530    4    0    1040  
    11 TRUNK_V@x1740      5645    4    0     380  
    12 TRUNK_V_OOB+MST@x6710 6435 8    0     380  
    13 TRUNK_H+MST@y3895     7195 4    0     380  
    14 TRUNK_V@x625       7385    3    1     380  
    15 TRUNK_H_OOB@y4610  7860    5    0    1230  
    16 TRUNK_V_OOB@x6710  8175    4    0     530  
    17 TRUNK_V+MST@x625   8560    8    1     440  
    18 TRUNK_H_OOB+MST@y2385 8922 10   0     530  
    19 TRUNK_H_OOB+MST@y4610 9045 7    0     380  
    20 TRUNK_V_OOB+MST@x-610 9795 8    0     440  
    21 TRUNK_V_OOB@x-610     9855 3    0     380  
    22 BITRUNK_H         10630    3    0     380  
   conn detail — candidate 7: TRUNK_V@x5485
     seg0  V M7·hint  along[3100,3365] perp=5485  slide=[4890..6100] = 1210  pull=→lo(-2)
        busterms: blk_39@face=3365(mid)
        segs:     seg1@3100(end), seg2@3310(mid)
        passthru: blk_22
     seg1  H M6·hint  along[4870,5485] perp=3100  slide=[2570..3100] = 530  pull=→hi(1)
        busterms: blk_09@face=4870(mid)
        segs:     seg0@5485(end)
        passthru: blk_21, blk_22
     seg2  H M6·hint  along[1250,5485] perp=3310  slide=[2720..3310] = 590  pull=→hi(1)
        busterms: blk_19@face=1250(mid)
        segs:     seg0@5485(end)
        passthru: blk_05, blk_14, blk_21, blk_22, blk_37

══ summary (2 bundles) ══
   candidates: total=37 avg=18.5 median=18.5 min=14 max=23
   bundles with duplicates : 0/2 (0 redundant candidates)
   bundles with pinched cand: 0/2
   single-candidate bundles : 0/2
   bundles with pass-through: 2/2
   shape histogram: TRUNK_H=8, TRUNK_V=7, TRUNK_V_OOB=4, TRUNK_H_OOB=4, TRUNK_H+MST=3, TRUNK_V+MST=3, BITRUNK_H=2, TRUNK_V_OOB+MST=2, TRUNK_H_OOB+MST=2, MST_VH=1, MST_HV=1
[Planner] Layer channel capacities:
  M3 (V)  min_band_cap=0
  M5 (V)  min_band_cap=60
  M7 (V)  min_band_cap=60
  M2 (H)  min_band_cap=0
  M4 (H)  min_band_cap=15
  M6 (H)  min_band_cap=15
[Planner] Grid extended: 3 X, 1 Y points from topology candidates.
[Planner] Layer channel capacities:
  M3 (V)  min_band_cap=0
  M5 (V)  min_band_cap=60
  M7 (V)  min_band_cap=60
  M2 (H)  min_band_cap=0
  M4 (H)  min_band_cap=15
  M6 (H)  min_band_cap=15
[Planner] Bundle 1 (90 units wide) -> topo 4 of 14: TRUNK_H@y4887 [pinned]  [H→M6 V→M7 V→M7 V→M7 V→M7]  overflow=0
[Planner] Bundle 2 (72 units wide) -> topo 8 of 23: TRUNK_V@x5485 [pinned]  [V→M7 H→M6 H→M6]  overflow=0

── bundle 1  nets=60 (bus_077_b00…)  width=90.0  sel=3 PINNED  cands=14  PASSTHRU(5)
   idx type                 wl segs pass  mslide  notes
     0 TRUNK_V@x5157      3253    2    2     555  
     1 TRUNK_V_OOB@x6282     4166    4    0     960  
     2 TRUNK_V@x4575      4425    4    0     570  
     3 TRUNK_H@y4887      5605    5    1     635  *SEL
     4 TRUNK_V_OOB@x4098     5856    4    0     960  
     5 TRUNK_H@y3895      6710    6    0     655  
     6 BITRUNK_H          6810    5    0     575  
     7 TRUNK_H+MST@y3895     7475    8    0     575  
     8 TRUNK_H@y2875      7605    5    0     555  
     9 TRUNK_H@y5825      7605    4    2     575  
    10 TRUNK_H+MST@y4887     8655    8    1     905  
    11 TRUNK_H@y1905     11120    5    1     555  
    12 TRUNK_H_OOB@y6787    11465    6    0     655  
    13 TRUNK_H_OOB@y938    15475    6    0     655  
   conn detail — candidate 3: TRUNK_H@y4887
     seg0  H M6  along[5445,6100] perp=4887  slide=[4425..5330] = 905  pull=none(0)
        busterms: blk_12@face=6100(mid)
        segs:     seg1@5445(end), seg4@5445(end), seg2@6100(end), seg3@5485(mid)
        passthru: (none)
     seg1  V M7  along[4887,5350] perp=5445  slide=[4280..5445] = 1165  pull=→hi(1)
        busterms: blk_02@face=5350(mid)
        segs:     seg0@4887(end)
        passthru: blk_12, io_pad_tr
     seg2  V M7  along[3365,4887] perp=6100  slide=[4870..6100] = 1230  pull=none(0)
        busterms: blk_22@face=3365(mid)
        segs:     seg0@4887(end)
        passthru: blk_12, blk_39
     seg3  V M7  along[2385,4887] perp=5485  slide=[4870..6080] = 1210  pull=→hi(1)
        busterms: blk_29@face=2385(mid)
        segs:     seg0@4887(end)
        passthru: blk_12, blk_22, blk_39
     seg4  V M7  along[4887,5350] perp=5445  slide=[5445..6080] = 635  pull=→hi(1)
        busterms: io_pad_tr@face=5350(mid)
        segs:     seg0@4887(end)
        passthru: blk_02, blk_12

── bundle 2  nets=48 (bus_056_b00…)  width=72.0  sel=7 PINNED  cands=23  PASSTHRU(4)
   idx type                 wl segs pass  mslide  notes
     0 TRUNK_H@y3205      3885    3    1     190  
     1 TRUNK_V@x4185      3885    3    0     190  
     2 TRUNK_H@y3337      3912    4    1      15  
     3 MST_VH             4710    5    0     380  
     4 TRUNK_V+MST@x5485     4795    6    0     380  
     5 TRUNK_V@x2865      4900    4    0     360  
     6 MST_HV             4945    6    0     380  
     7 TRUNK_V@x5485      5115    3    0     530  *SEL
     8 TRUNK_V+MST@x2865     5240    8    0     190  
     9 TRUNK_H_OOB@y2385     5395    5    0    1230  
    10 TRUNK_H@y3895      5530    4    0    1040  
    11 TRUNK_V@x1740      5645    4    0     380  
    12 TRUNK_V_OOB+MST@x6710     6435    8    0     380  
    13 TRUNK_H+MST@y3895     7195    4    0     380  
    14 TRUNK_V@x625       7385    3    1     380  
    15 TRUNK_H_OOB@y4610     7860    5    0    1230  
    16 TRUNK_V_OOB@x6710     8175    4    0     530  
    17 TRUNK_V+MST@x625     8560    8    1     440  
    18 TRUNK_H_OOB+MST@y2385     8922   10    0     530  
    19 TRUNK_H_OOB+MST@y4610     9045    7    0     380  
    20 TRUNK_V_OOB+MST@x-610     9795    8    0     440  
    21 TRUNK_V_OOB@x-610     9855    3    0     380  
    22 BITRUNK_H         10630    3    0     380  
   conn detail — candidate 7: TRUNK_V@x5485
     seg0  V M7  along[3100,3365] perp=5485  slide=[4890..6100] = 1210  pull=→lo(-2)
        busterms: blk_39@face=3365(mid)
        segs:     seg1@3100(end), seg2@3310(mid)
        passthru: blk_22
     seg1  H M6  along[4870,5485] perp=3100  slide=[2570..3100] = 530  pull=→hi(1)
        busterms: blk_09@face=4870(mid)
        segs:     seg0@5485(end)
        passthru: blk_21, blk_22
     seg2  H M6  along[1250,5485] perp=3310  slide=[2720..3310] = 590  pull=→hi(1)
        busterms: blk_19@face=1250(mid)
        segs:     seg0@5485(end)
        passthru: blk_05, blk_14, blk_21, blk_22, blk_37

══ summary (2 bundles) ══
   candidates: total=37 avg=18.5 median=18.5 min=14 max=23
   bundles with duplicates : 0/2 (0 redundant candidates)
   bundles with pinched cand: 0/2
   single-candidate bundles : 0/2
   bundles with pass-through: 2/2
   shape histogram: TRUNK_H=8, TRUNK_V=7, TRUNK_V_OOB=4, TRUNK_H_OOB=4, TRUNK_H+MST=3, TRUNK_V+MST=3, BITRUNK_H=2, TRUNK_V_OOB+MST=2, TRUNK_H_OOB+MST=2, MST_VH=1, MST_HV=1

Before the fix
===

── bundle 1  nets=60 (bus_077_b00…)  width=90.0  sel=3 PINNED  cands=14  PASSTHRU(5)
   idx type                 wl segs pass  mslide  notes
     0 TRUNK_V@x5157      3253    2    2     555  
     1 TRUNK_V_OOB@x6282  4166    4    0     960  
     2 TRUNK_V@x4575      4425    4    0     570  
     3 TRUNK_H@y4887      5605    5    1     635  *SEL
     4 TRUNK_V_OOB@x4098  5856    4    0     960  
     5 TRUNK_H@y3895      6710    6    0     655  
     6 BITRUNK_H          6810    5    0     575  
     7 TRUNK_H+MST@y3895  7475    8    0     575  
     8 TRUNK_H@y2875      7605    5    0     555  
     9 TRUNK_H@y5825      7605    4    2     575  
    10 TRUNK_H+MST@y4887  8655    8    1     905  
    11 TRUNK_H@y1905     11120    5    1     555  
    12 TRUNK_H_OOB@y6787 11465    6    0     655  
    13 TRUNK_H_OOB@y938  15475    6    0     655  

── bundle 2  nets=48 (bus_056_b00…)  width=72.0  sel=3 PINNED  cands=23  PASSTHRU(4)
   idx type                 wl segs pass  mslide  notes
     0 TRUNK_H@y3205      3885    3    1     190  
     1 TRUNK_V@x4185      3885    3    0     190  
     2 TRUNK_H@y3337      3912    4    1      15  
     3 TRUNK_V@x5485      4290    2    0     590  *SEL
     4 MST_VH             4710    5    0     380  
     5 TRUNK_V@x2865      4900    4    0     360  
     6 MST_HV             4945    6    0     380  
     7 TRUNK_V+MST@x2865  5240    8    0     190  
     8 TRUNK_H_OOB@y2385  5395    5    0    1230  
     9 TRUNK_H@y3895      5530    4    0    1040  
    10 TRUNK_V@x1740      5645    4    0     380  
    11 TRUNK_V_OOB@x6710  6125    3    0     590  
    12 TRUNK_V_OOB+MST@x6710 6225 8    0     380  
    13 TRUNK_H+MST@y3895     7195 4    0     380  
    14 TRUNK_V@x625       7385    3    1     380  
    15 TRUNK_H_OOB@y4610  7860    5    0    1230  
    16 TRUNK_V+MST@x5485  8310    6    0     380  
    17 TRUNK_V+MST@x625   8560    8    1     440  
    18 TRUNK_H_OOB+MST@y2385 8922 10   0     530  
    19 TRUNK_H_OOB+MST@y4610 9045 7    0     380  
    20 TRUNK_V_OOB+MST@x-610 9795 8    0     440  
    21 TRUNK_V_OOB@x-610     9855 3    0     380  
    22 BITRUNK_H         10630    3    0     380  

══ summary (2 bundles) ══
   candidates: total=37 avg=18.5 median=18.5 min=14 max=23
   bundles with duplicates : 0/2 (0 redundant candidates)
   bundles with pinched cand: 0/2
   single-candidate bundles : 0/2
   bundles with pass-through: 2/2
   shape histogram: TRUNK_H=8, TRUNK_V=7, TRUNK_V_OOB=4, TRUNK_H_OOB=4, TRUNK_H+MST=3, TRUNK_V+MST=3, BITRUNK_H=2, TRUNK_V_OOB+MST=2, TRUNK_H_OOB+MST=2, MST_VH=1, MST_HV=1
