# QoR corpus snapshot — 2026-07-29 (main @ 690cd13)

Regenerate with `tools/qor_table.py --out qor_table.md`.  Columns: `bund`/`busS`/`netS` = bundle / bus-segment / net-segment counts; `busWL`/`netWL` = abstract (after NUTS) / detailed (after DNUTS) wirelength, placed-only; `ovl`/`unpl`/`viol` = overlaps / unplaced bits / bundles with `check_design` violations; `sec` = wall-clock.  `null` = that stage did not run.

27 clean · 2 with residuals · 29 flows.

## DIRTY — residual overlaps / unplaced / viol_bundles (or incomplete)

```
flow                                          bund  busS   netS    busWL      netWL |  ovl unpl viol    sec   note
------------------------------------------------------------------------------------------------------------------------------------
rnr/mix2_fast_on_aligned_sql                   100   268   3314    74416     883692 |    2   30    2   61.5
rnr/mix2_fast_bottomup                         100   248   3064    73155     870070 |    1    0    0   52.0
```

## CLEAN — 0 overlaps / 0 unplaced / 0 viol_bundles

```
flow                                          bund  busS   netS    busWL      netWL     sec
------------------------------------------------------------------------------------------
big_data_test/bigHalf                           80   269   9400   417619   14706968     4.7
big_data_test/big                               80   258   8536   767207   26324433     1.3
big_data_test/big2/big2                         80   239   8068   340445   11554186     1.3
rnr/mix2_fast_topdown                          100   267   3432    74253     900682    31.7
rnr/mix                                        100   265   3348    67425     815129    23.4
rnr/mix2                                       100   244   3170    69823     862476    49.5
hbundles/10_chip_units_blocks_leaf             176   209   1220    50422     364252     1.3
big_data_test/tc3a                              80   264   1116    78270     301190     2.9
big_data_test/big_3bundles_sel_pure_mst_topo     3    24    668    43351    1081116     0.0
big_3bundles_sel_trunk+mst_topo                  3    14    468    38742     973059     0.0
hbundles/06_multipin_stress                     35   136    448    14545      49772     5.9
big_data_test/big2/b24_bus_056                   1     8    384     4818     231264     0.0
hbundles/05_stress_grid                         61    86    372     6728      31298     0.3
hbundles/07_wide_fan_stress                     24   153    269    19571      40383     0.6
big_data_test/b61                                1    10    160    15294     244772     0.0
big_data_test/big2/b1_bus_007                    1     5    140     4946     138502     0.1
big_data_test/big2/b4_bus_077                    1     2    120     3199     191669     0.1
big_data_test/b44                                1     2    104     3715     193376     0.1
big_data_test/big2/b34_bus_028                   1     3     84      166       4732     0.0
hbundles/08_cross_level                         14    19     76     2576      10322     0.3
hbundles/02_two_procs                            8     8     64      660       5280     0.0
big_data_test/bigHalf_bus038_bitrunk            80     1     56     4245     237720     0.1
hbundles/09_local_global_compete                 2     2     56      860      21920     0.0
hbundles/01_pipeline_hier                        4     4     32      330       2640     0.2
hbundles/04_deep_hierarchy                       7     8     32      796       3203     0.0
hbundles/03_priority_ordering                    3     3     20     1950      13400     0.0
big_data_test/big2/b3_bus_023                    1     6      6     8424       8426     0.0
```
