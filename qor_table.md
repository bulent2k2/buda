# QoR corpus snapshot — 2026-07-27 (main @ 79a57ec)

Regenerate with `tools/qor_table.py --out qor_table.md`.  Columns: `bund`/`busS`/`netS` = bundle / bus-segment / net-segment counts; `busWL`/`netWL` = abstract (after NUTS) / detailed (after DNUTS) wirelength, placed-only; `ovl`/`unpl`/`viol` = overlaps / unplaced bits / bundles with `check_design` violations; `sec` = wall-clock.  `null` = that stage did not run.

24 clean · 11 with residuals · 35 flows.

## DIRTY — residual overlaps / unplaced / viol_bundles (or incomplete)

```
flow                                          bund  busS   netS    busWL      netWL |  ovl unpl viol    sec   note
------------------------------------------------------------------------------------------------------------------------------------
big_data_test/big2/big2_noviz                   80   252   8372   341993   11411044 |    8   40    3    0.5
big_data_test/big2/tc3b_flat                    80   252   8372   341993   11411044 |    8   40    3    0.6
rnr/slowdown_rnr                               100   256   3298    62824     785430 |    2    8    1   48.5
rnr/mix2_fast_topdown                          100   265   3157    67558     772624 |   16  175   11    3.9
rnr/mix2_fast_bottomup                         100   259   3128    67553     782926 |   17  196   11    4.1
rnr/mix2_fast                                  100   265   3115    71218     745146 |   34  241   14    4.0
rnr/mix2_fast_on_aligned_sql                   100   265   3115    71218     745146 |   34  241   14    3.8
rnr/mix2                                       100   240   3078    69186     839868 |    2   42    3   28.3
big_data_test/big2/big2_b4_b24                   2    12    564     8924     433343 |    0   60    1    0.0
hbundles/06_multipin_stress                     35   143    384    15613      47500 |    1   54    5    0.6
rnr/mix2_repro                                 100   238   null    66800       null |   17 null   10    3.2
```

## CLEAN — 0 overlaps / 0 unplaced / 0 viol_bundles

```
flow                                          bund  busS   netS    busWL      netWL     sec
------------------------------------------------------------------------------------------
big_data_test/bigHalf                           80   269   9400   417619   14706968     4.9
big_data_test/big                               80   258   8536   767207   26324433     1.7
big_data_test/big2/big2                         80   239   8068   343037   11656888     1.0
rnr/mix                                        100   265   3348    67425     815129    24.9
hbundles/10_chip_units_blocks_leaf             176   209   1220    50422     364252     1.5
big_data_test/tc3a                              80   264   1116    78270     301190     2.4
big_data_test/big_3bundles_sel_pure_mst_topo     3    24    668    43351    1081116     0.1
big_3bundles_sel_trunk+mst_topo                  3    14    468    38742     973059     0.0
big_data_test/big2/b24_bus_056                   1     8    384     4818     231264     0.0
hbundles/05_stress_grid                         61    86    372     6728      31298     0.3
hbundles/07_wide_fan_stress                     24   153    269    19571      40383     0.7
big_data_test/b61                                1    10    160    15294     244772     0.0
big_data_test/big2/b1_bus_007                    1     5    140     4946     138502     0.0
big_data_test/big2/b4_bus_077                    1     2    120     3199     191669     0.1
big_data_test/b44                                1     2    104     3715     193376     0.0
big_data_test/big2/b34_bus_028                   1     3     84      166       4732     0.0
hbundles/08_cross_level                         14    19     76     2576      10322     0.0
hbundles/02_two_procs                            8     8     64      660       5280     0.0
big_data_test/bigHalf_bus038_bitrunk            80     1     56     4245     237720     0.0
hbundles/09_local_global_compete                 2     2     56      860      21920     0.0
hbundles/01_pipeline_hier                        4     4     32      330       2640     0.0
hbundles/04_deep_hierarchy                       7     8     32      796       3203     0.0
hbundles/03_priority_ordering                    3     3     20     1950      13400     0.0
big_data_test/big2/b3_bus_023                    1     6      6     8424       8426     0.0
```
