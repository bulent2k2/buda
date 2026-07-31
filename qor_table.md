# QoR corpus snapshot — 2026-07-31 (main @ b78a4c2)

Regenerate with `tools/qor_table.py --out qor_table.md`.  Columns: `bund`/`busS`/`netS` = bundle / bus-segment / net-segment counts; `busWL`/`netWL` = abstract (after NUTS) / detailed (after DNUTS) wirelength, placed-only; `ovl`/`unpl`/`viol` = overlaps / unplaced bits / bundles with `check_design` violations; `sec` = wall-clock.  `null` = that stage did not run.

28 clean · 6 with residuals · 34 flows.

## DIRTY — residual overlaps / unplaced / viol_bundles (or incomplete)

```
flow                                          bund  busS   netS    busWL      netWL |  ovl unpl viol    sec   note
------------------------------------------------------------------------------------------------------------------------------------
chip/chip_topdown                              640  2292  43793  3030492   61828900 |  266 3307  159  178.2
chip/chip3_topdown                             640  2093  43469  2889132   60833132 |  121 1731  133  176.5
chip/chip_bottomup                             640  2074  38573  2647566   46331791 |  485 2207  106  124.3
chip/chip3a_bottomup                           640  1959  37405  2648674   47203194 |  447 2269  103  127.6
rnr/mix2_topdown_refine                        100   276   3392    68200     835130 |    1    0    0   67.8
rnr/mix2_fast_on_aligned_sql                   100   262   3326    76368     911840 |    2   16    1   37.1
```

## CLEAN — 0 overlaps / 0 unplaced / 0 viol_bundles

```
flow                                          bund  busS   netS    busWL      netWL     sec
------------------------------------------------------------------------------------------
big_data_test/bigHalf                           80   259   8980   405139   14073009    70.1
big_data_test/big                               80   261   8672   769850   26456791     1.1
big_data_test/big2/big2                         80   243   8308   346340   11818258     0.9
rnr/mix2_fast_topdown                          100   268   3412    66129     802202     5.7
rnr/mix2_fast_bottomup                         100   262   3324    71805     885066     5.3
rnr/mix2                                       100   244   3204    69713     866029    29.3
rnr/mix                                        100   248   3132    62039     761520     3.7
hbundles/10_chip_units_blocks_leaf             176   206   1200    46092     337719     0.7
big_data_test/tc3a                              80   262   1108    78806     300930     1.9
big_data_test/big_3bundles_sel_pure_mst_topo     3    24    668    43973    1096866     0.0
big_3bundles_sel_trunk+mst_topo                  3    14    468    38826     973059     0.0
hbundles/06_multipin_stress                     35   144    462    14475      49609    16.4
big_data_test/big2/b24_bus_056                   1     8    384     4818     231264     0.0
hbundles/05_stress_grid                         61    86    372     6781      31978     0.2
hbundles/07_wide_fan_stress                     24   153    269    19587      40373     0.4
big_data_test/b61                                1    10    160    15333     245348     0.0
big_data_test/big2/b1_bus_007                    1     5    140     4946     138502     0.0
big_data_test/big2/b4_bus_077                    1     2    120     3199     191669     0.1
big_data_test/b44                                1     2    104     3715     193376     0.0
big_data_test/big2/b34_bus_028                   1     3     84      166       4732     0.0
hbundles/08_cross_level                         14    19     76     2576      10322     0.0
hbundles/02_two_procs                            8     8     64      660       5280     0.0
big_data_test/bigHalf_bus038_bitrunk            80     1     56     4245     237720     0.1
hbundles/09_local_global_compete                 2     2     56      860      21920     0.0
hbundles/01_pipeline_hier                        4     4     32      330       2640     0.0
hbundles/04_deep_hierarchy                       7     8     32      796       3203     0.0
hbundles/03_priority_ordering                    3     3     20     1950      13400     0.0
big_data_test/big2/b3_bus_023                    1     6      6     8424       8426     0.0
```
